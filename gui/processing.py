"""处理流程控制:自动化与人工两条路径。

- 步骤化(契约 v1.2 / G2B-002):``import_data`` → ``generate_fid`` →
  ``generate_spectrum``,每步独立按钮与状态;
- 人工:``manual_fid_com`` / ``run_manual_fid_com`` / ``manual_scripts`` /
  ``run_manual_spectrum`` 对接 workflow/manual(fid.com 与谱图脚本)。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from core.app_paths import resource_path
from core.project import ExperimentEntry, ProjectManager
from gui.pipeline_state import record_step_success


def _load_config() -> dict:
    import yaml

    path = resource_path("config/nmrforge.yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


_DATA_KEY_FILES = ('acqus', 'acqu2s', 'acqu3s', 'ser', 'fid', 'nuslist')


def _segment_dirs(root: Path) -> list[Path]:
    """直接含 acqus 的数据段子目录(分段导入用)。

    只认含 acqus 的子目录为数据段;只有 ser/fid 等数据文件但缺 acqus、
    或什么文件都没有的子目录一律忽略(0.2.198 用户规则)——选中的总文件夹
    顶层没有 acqus 是容器正常形态,不应报缺失。
    """
    return sorted(
        p for p in root.iterdir() if p.is_dir() and (p / "acqus").is_file()
    )


def is_segmented_container(path) -> bool:
    """容器目录判定:本身不是 Bruker 数据集(顶层无 acqus),
    但含 ≥2 个直接带 acqus 的分段子目录(分段采集导入用)。
    """
    try:
        root = Path(path)
        if not root.is_dir() or (root / "acqus").is_file():
            return False
        return len(_segment_dirs(root)) >= 2
    except OSError:
        return False


def resolve_import_source(path) -> tuple[str, bool]:
    """解析导入源(2026-08-19 Task F:忽略非数据子文件夹)。

    返回 (data_source, is_segmented):
    - 本身是 Bruker 数据集(含 acqus) → (path, False);
    - 容器含 ≥2 个含 acqus 的数据段子目录 → (path, True),走分段采集导入;
    - 恰好 1 个含 acqus 的数据段(其余非数据/缺 acqus,忽略)→ (该子目录, False);
    - 0 个 → 抛 ImportWorkflowError。
    """
    from workflow.import_workflow import ImportWorkflowError

    root = Path(path)
    if (root / 'acqus').is_file():
        return str(root), False
    if not root.is_dir():
        raise ImportWorkflowError(f'目录不存在: {root}')
    segments = _segment_dirs(root)
    if len(segments) >= 2:
        return str(root), True
    if len(segments) == 1:
        return str(segments[0]), False
    data_subdirs = sorted(
        p
        for p in root.iterdir()
        if p.is_dir() and any((p / name).is_file() for name in _DATA_KEY_FILES)
    )
    if data_subdirs:
        # 子目录只有数据文件但缺 acqus:按非数据文件夹忽略,但全部如此时
        # 无法导入,给出明确提示(不把顶层缺 acqus 当作问题,0.2.198)
        raise ImportWorkflowError(
            '所选目录的子目录含数据文件但均缺少 acqus,无法作为数据集导入'
            '(含 acqus 的子目录才算数据段);非数据子目录已忽略'
        )
    raise ImportWorkflowError(
        '所选目录既不是 Bruker 数据集,也没有含数据文件的子目录'
        '(acqus/acqu2s/acqu3s/ser/fid/nuslist);非数据子目录已忽略'
    )


class ProcessingController:
    """GUI 层处理控制:三步流程(import_data → generate_fid → generate_spectrum)
    对接 workflow/stepwise(契约 v1.2 §8.3);人工路径对接 workflow/manual。"""

    def __init__(self, manager: ProjectManager | None = None) -> None:
        self._backend = None
        self._manager = manager

    def set_manager(self, manager) -> None:
        """项目对象更换后绑定当前 ProjectManager(供步骤化调用)。"""
        self._manager = manager

    def _backend_instance(self):
        """惰性创建 ProcessingBackend(配置读取统一走 backend.config,0.2.164)。"""
        if self._backend is None:
            from backend.config import load_config
            from backend.factory import create_backend

            self._backend = create_backend(load_config())
        return self._backend


    # ------------------------------------------------------------------
    # 步骤化处理(G2B-002 / 契约 v1.2):导入样品数据 → 生成 FID → 生成谱图
    # 实现位于 workflow/(import_workflow / stepwise + backend),本控制器
    # 负责接线、状态登记与参数组装。
    # ------------------------------------------------------------------
    def _data_ndim(self, exp_id: str, data_id: str) -> int:
        """从 metadata 读数据维度(3D 批量暂不支持用);读不到回退 2。"""
        try:
            meta_path = self._manager.data_metadata_path(exp_id, data_id)
            if meta_path is not None and meta_path.is_file():
                import json

                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                return int((metadata.get("dataset") or {}).get("ndim") or 2)
        except Exception:  # noqa: BLE001
            pass
        return 2


    def import_data(self, entry: ExperimentEntry, source: str, copy: bool = True) -> dict:
        """第 1 步:导入样品数据(只读参数 + 复制 raw),返回 ImportResult dict。"""
        from workflow.import_workflow import import_data

        if self._manager is None:
            raise RuntimeError("ProcessingController 未绑定项目(ProjectManager)")
        result = import_data(self._manager, entry.id, source, copy=copy)
        data_id = getattr(result, "data_id", "") or ""
        if data_id:
            record_step_success(
                self._manager, entry.id, data_id, "import", params={"copy": copy}
            )
        self._manager.save()
        return {
            "experiment_id": entry.id,
            "data_id": data_id,
            "run_id": getattr(result, "run_id", ""),
            "warnings": list(getattr(result, "warnings", []) or []),
        }

    def import_segmented_dataset(
        self,
        source: str,
        *,
        exp_id: str = "",
        title: str = "",
        sample_id: str = "",
        copy: bool = True,
    ):
        """分段采集导入透传(0.2.108/G2B-011):容器目录下多个含 acqus 的
        分段子目录合并为一条样品数据(后端逐段转换 + addNMR 合并)。

        exp_id 非空时导入到该实验类型(与普通单个导入一致),为空时后端
        新建实验(旧行为);与批量导入(多条条目)明确区分。返回 ImportResult。
        """
        from workflow.import_workflow import import_segmented_dataset

        self._require_manager()
        return import_segmented_dataset(
            self._manager,
            source,
            exp_id=exp_id,
            title=title,
            sample_id=sample_id,
            copy=copy,
        )

    def batch_import(
        self, exp_id: str, folders: list, group: bool = True
    ) -> dict:
        """批量导入多个数据目录到实验类型;group=True 归入同一数据组(组 id 即 batch)。

        group=False 不成组(相当于多个单次导入,batch_id 为空);返回
        {"batch_id", "results": [{folder, data_id, ok, error}]};单个目录
        失败不阻断整批(结果中带 error 信息,0.2.162-补12)。
        0.2.164-补1:数据组为唯一来源,不再双写 pipeline_state 标记。
        """
        self._require_manager()
        if self._manager.project is None:
            raise RuntimeError("ProcessingController 未绑定项目(ProjectManager)")
        entry = self._manager.project.experiment(exp_id)
        if entry is None:
            raise RuntimeError(f"实验类型不存在: {exp_id}")
        # 0.2.163:成组批量导入建数据组(schema 1.4,组 id 即 batch);
        # 0.2.164-补1:数据组为唯一来源,不再双写 pipeline_state batch 标记
        batch = ""
        if group:
            batch = self._manager.create_data_group(exp_id).id
        results: list[dict] = []
        for folder in folders:
            item: dict = {
                "folder": str(folder),
                "data_id": "",
                "ok": False,
                "error": "",
            }
            try:
                # 0.2.199-补29gl:批量导入先校验原始数据文件存在,缺文件
                # 直接标记失败,避免"看着导入了、处理时才失败";采集不全/更多
                # 不在此拦截,由 FID 生成步骤按实际数据反推。
                _f = Path(str(folder))
                _is_nd = (_f / "acqu2s").is_file() or (_f / "acqu3s").is_file()
                _data_file = "ser" if _is_nd else "fid"
                if not (_f / _data_file).is_file():
                    raise RuntimeError(f"缺少数据文件 {_data_file}")
                result = self.import_data(entry, str(folder))
                data_id = str(result.get("data_id", "") or "")
                item["data_id"] = data_id
                if data_id and group:
                    # 0.2.199-补29hd:批量暂仅支持 2D 谱——3D 绑定批量组会在
                    # 批量处理时触发 SMILE(不稳定主机断电),此处拦截提示。
                    if self._data_ndim(exp_id, data_id) >= 3:
                        item["note"] = "3D 谱,批量暂仅支持 2D,未绑定批量组;可单个处理"
                    else:
                        self._manager.add_to_group(exp_id, batch, data_id)
                item["ok"] = True
            except Exception as exc:  # noqa: BLE001 - 单个失败不阻断整批
                item["error"] = f"{type(exc).__name__}: {exc}"
            results.append(item)
        # 全部失败时移除空组,避免残留空数据组节点
        if group and not any(item.get("ok") for item in results):
            try:
                self._manager.delete_data_group(exp_id, batch)
            except Exception:  # noqa: BLE001 - 组删除失败不阻断
                pass
        self._manager.save()
        return {"batch_id": batch if group else "", "results": results}

    def run_group_batch(
        self,
        exp_id: str,
        group_id: str,
        steps: list[str],
        reference_data_id: str = "",
        progress: Callable[[str], None] | None = None,
        params: dict | None = None,
    ) -> dict:
        """对数据组执行批量处理(workflow.batch.run_batch)。

        steps: BATCH_STEPS 子集(如 ["fid"] 只处理到生成 FID);
        reference_data_id 非空时,取其最近一次成功谱图运行的有效参数
        作为 spectrum 步骤参数基底;显式 params 覆盖参考参数。
        """
        from workflow.batch import run_batch

        self._require_manager()
        return run_batch(
            self._manager,
            exp_id,
            group_id,
            steps,
            self._backend_instance(),
            reference_data_id=reference_data_id or None,
            progress=progress,
            params=params,
        )

    def generate_fid(
        self,
        data,
        exp_id: str | None = None,
        data_id: str | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> str:
        """第 2 步:生成 FID(backend.convert_to_fid),返回 fid 路径。

        progress 可选回调:阶段进展(G2B-006;后端落地后转发真实阶段日志)。
        """
        import inspect

        from workflow.stepwise import generate_fid as stepwise_fid

        if self._manager is None:
            raise RuntimeError("ProcessingController 未绑定项目(ProjectManager)")
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")

        def emit(message: str) -> None:
            if progress is not None:
                progress(message)

        emit("bruker 转换中(fid.com)")
        kwargs: dict = {}
        if "progress" in inspect.signature(stepwise_fid).parameters:
            kwargs["progress"] = emit
        fid_path = stepwise_fid(
            self._manager,
            exp_id,
            data_id,
            self._backend_instance(),
            **kwargs,
        )
        emit("完成 FID 转换")
        if data_id:
            record_step_success(self._manager, exp_id, data_id, "fid")
            self._snapshot_step(
                exp_id,
                data_id,
                ("convert_to_fid",),
                self._fid_com_script(exp_id, data_id),
            )
        self._manager.save()
        return fid_path

    def generate_spectrum(
        self,
        data,
        exp_id: str | None = None,
        data_id: str | None = None,
        progress: Callable[[str], None] | None = None,
        phase_optimize: bool = True,
        params: dict | None = None,
    ) -> str:
        """第 3 步:生成谱图(process/reconstruct_nus,含 NUS SMILE 重构)。

        0.2.146 起生成谱图即统一自动处理(逐维复型预览 + 内存调相 +
        参数优化 + 完整终跑,见 workflow/phase_routes.unified_route);
        params 可选传 phase_route="unified"(默认)/"none"(逃生口)。
        phase_optimize 参数保留仅为兼容调用方,不再叠加旧逐维暴力优化
        (0.2.154 移除——该分支曾使相位优化在最终 SMILE 之后重复执行)。
        progress 可选回调:阶段进展。
        """
        import inspect

        from workflow.stepwise import generate_spectrum as stepwise_spectrum

        if self._manager is None:
            raise RuntimeError("ProcessingController 未绑定项目(ProjectManager)")
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")

        def emit(message: str) -> None:
            if progress is not None:
                progress(message)

        emit("读取数据,准备处理")
        linewidth_by_axis: dict[str, float] | None = None
        try:
            experiment = self._read_experiment(exp_id, data_id)
            # 0.2.112:软件设置「线宽」接入(核素 → 轴映射,显式 params 优先)
            linewidth_by_axis = self._linewidth_by_axis(experiment)
            from core.data.internal_data_model import SamplingMode

            if experiment.sampling.mode is SamplingMode.NUS:
                emit("NUS 数据: 开始 SMILE 重构(含直接维相位)")
            else:
                emit("均匀采样: 开始 NMRPipe 处理(含直接维相位)")
        except Exception:  # noqa: BLE001 - 采样信息不可用给通用提示
            emit("后端执行中(转换/重构/相位优化)")
        params = dict(params or {})
        if linewidth_by_axis is not None and "linewidth_hz" not in params:
            params["linewidth_hz"] = linewidth_by_axis
        kwargs: dict = {}
        if params:
            kwargs["params"] = dict(params)
        if "progress" in inspect.signature(stepwise_spectrum).parameters:
            kwargs["progress"] = emit
        spectrum_path = stepwise_spectrum(
            self._manager,
            exp_id,
            data_id,
            self._backend_instance(),
            **kwargs,
        )
        # 0.2.154:生成谱图即统一自动处理(0.2.146 移除途径下拉后,params
        # 无 phase_route 时旧分支曾再次触发逐维暴力相位优化,导致相位优化
        # 跑到最终 SMILE 之后重复执行)——不再叠加任何后置相位优化。
        route = (params or {}).get("phase_route")
        label = {"unified": "统一自动处理", "none": "None(逃生口)"}.get(
            str(route), route or "统一自动处理"
        )
        emit(f"生成谱图完成,相位途径: {label}")
        if data_id:
            record_step_success(self._manager, exp_id, data_id, "spectrum")
            self._snapshot_step(
                exp_id,
                data_id,
                ("process", "reconstruct_nus"),
                self._spectrum_scripts(exp_id, data_id),
            )
        self._manager.save()
        return spectrum_path

    # ------------------------------------------------------------------
    # 峰挑选 / 分析(G2B-004)
    # ------------------------------------------------------------------
    def pick_peaks(
        self,
        data,
        exp_id: str | None = None,
        data_id: str | None = None,
        sigma_multiplier: float | None = None,
        ref_peaks: list[dict] | None = None,
        ref_nuclei: list[str] | None = None,
        tolerance_ppm: dict[str, float] | None = None,
        ref_name: str = "",
    ) -> dict:
        """峰挑选:调 workflow.pick_peaks,返回 {status, peak_path, peak_count, logs}。"""
        try:
            from workflow.pick_peaks import pick_peaks as backend_pick_peaks
        except ImportError as exc:  # pragma: no cover - Backend 未落地
            raise NotImplementedError("峰挑选(workflow.pick_peaks)待 Backend 实现") from exc
        if self._manager is None:
            raise RuntimeError("ProcessingController 未绑定项目(ProjectManager)")
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        result = backend_pick_peaks(
            self._manager, exp_id, data_id, sigma_multiplier=sigma_multiplier,
            ref_peaks=ref_peaks, ref_nuclei=ref_nuclei,
            tolerance_ppm=tolerance_ppm,
            ref_name=ref_name,
        )
        if data_id and result.get("status") == "success":
            record_step_success(self._manager, exp_id, data_id, "peaks")
        self._manager.save()
        return result

    def analyze(
        self,
        data,
        exp_id: str | None = None,
        data_id: str | None = None,
        reference_data_id: str = "",
        progress: Callable[[str], None] | None = None,
    ) -> dict:
        """分析(HSQC CSP,0.2.199-补29er):当前数据(扰动态) vs 比对数据
        (自由态);输出 csp_data.csv + csp_plot.svg + overlay_spectra.svg。"""
        try:
            from workflow.analyze import analyze as backend_analyze
        except ImportError as exc:  # pragma: no cover - Backend 未落地
            raise NotImplementedError("分析(workflow.analyze)待 Backend 实现") from exc
        if self._manager is None:
            raise RuntimeError("ProcessingController 未绑定项目(ProjectManager)")
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        result = backend_analyze(
            self._manager,
            exp_id,
            data_id,
            reference_data_id=reference_data_id,
            progress=progress,
        )
        if data_id and result.get("status") == "success":
            record_step_success(self._manager, exp_id, data_id, "analysis")
        self._manager.save()
        return result

    # ------------------------------------------------------------------
    # 人工路径(workflow/manual 接线)
    # ------------------------------------------------------------------
    def manual_fid_com(self, data, exp_id: str | None = None, data_id: str | None = None) -> str:
        """获取/生成 fid.com 内容(供查看修改)。"""
        from workflow.manual import manual_fid_com as backend_manual_fid_com

        self._require_manager()
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        return backend_manual_fid_com(
            self._manager, exp_id, data_id, self._backend_instance()
        )

    def run_manual_fid_com(
        self,
        data,
        content: str,
        exp_id: str | None = None,
        data_id: str | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> str:
        """写入并运行修改后的 fid.com。"""
        from workflow.manual import run_manual_fid_com as backend_run_fid

        self._require_manager()
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        result = backend_run_fid(
            self._manager,
            exp_id,
            data_id,
            content,
            backend=self._backend_instance(),
            progress=progress,
        )
        if data_id:
            record_step_success(self._manager, exp_id, data_id, "fid")
            self._snapshot_step(
                exp_id, data_id, ("manual_fid",), {"fid.com": content}
            )
        self._manager.save()
        return result

    def manual_scripts(
        self,
        data,
        params: dict | None = None,
        exp_id: str | None = None,
        data_id: str | None = None,
    ) -> dict:
        """渲染谱图步骤脚本(process.com/nus*.com)。"""
        from workflow.manual import manual_scripts as backend_manual_scripts

        self._require_manager()
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        return backend_manual_scripts(
            self._manager, exp_id, data_id, params=params
        )

    def run_manual_spectrum(
        self,
        data,
        scripts: dict,
        exp_id: str | None = None,
        data_id: str | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> str:
        """运行谱图脚本(消费已转换 fid)。"""
        from workflow.manual import run_manual_spectrum as backend_run_spectrum

        self._require_manager()
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        result = backend_run_spectrum(
            self._manager, exp_id, data_id, scripts, progress=progress
        )
        if data_id:
            record_step_success(self._manager, exp_id, data_id, "spectrum")
            self._snapshot_step(
                exp_id, data_id, ("manual_process", "manual_nus"), scripts
            )
        self._manager.save()
        return result

    def save_peaks_manual(
        self,
        data,
        peaks: list[dict],
        exp_id: str | None = None,
        data_id: str | None = None,
        *,
        nuclei: list[str] | None = None,
    ) -> str:
        """人工峰表保存:写 Poky .list(峰表文件即 list)并登记运行。"""
        from gui.peaks_io import export_peaks_poky

        self._require_manager()
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        ndim = 3 if peaks and "F1_shift" in peaks[0] else 2
        peaks_dir = self._manager.data_dir(exp_id, data_id, "peaks")
        peaks_dir.mkdir(parents=True, exist_ok=True)
        list_path = export_peaks_poky(
            peaks_dir / f"{exp_id}-{data_id}.list", peaks,
            ndim=ndim, nuclei=nuclei,
        )
        if data_id:
            record_step_success(self._manager, exp_id, data_id, "peaks")
        run = self._manager.start_run(
            exp_id,
            workflow_ref="manual_peaks",
            inputs={"data_id": data_id},
            params={"mode": "manual", "peaks": len(peaks), "format": "list"},
        )
        try:
            self._manager.finish_run(
                run.run_id, "success", outputs={"peaks": str(list_path)},
                message=f"人工峰表编辑({len(peaks)} 峰)",
            )
        except Exception:  # noqa: BLE001
            self._manager.finish_run(run.run_id, "failed", message="峰表保存失败")
        self._manager.save()
        return str(list_path)

    def _read_experiment(self, exp_id: str, data_id: str):
        """读取数据对应 Experiment(复用 workflow.stepwise 统一实现,0.2.164)。"""
        from workflow.stepwise import _read_experiment as _read

        return _read(self._manager, exp_id, data_id)

    def _linewidth_by_axis(self, experiment) -> dict[str, float]:
        """软件设置「线宽」(核素 → Hz)→ 轴映射(生成谱图 params,0.2.112)。

        后端 params["linewidth_hz"] 按轴(logical_axis)取值;设置里未配置的
        核素置 0,由后端回退核素默认表。
        """
        from gui.settings import load_settings

        settings = load_settings()
        lw = settings.get("linewidth_hz") or {}
        mapping: dict[str, float] = {}
        for dim in getattr(experiment, "dimensions", None) or []:
            axis = str(getattr(dim, "logical_axis", "") or "").strip()
            nucleus = str(getattr(dim, "nucleus", "") or "").strip()
            try:
                value = float(lw.get(nucleus) or 0.0)
            except (TypeError, ValueError):
                value = 0.0
            if axis:
                mapping[axis] = value
        return mapping

    def _last_spectrum_params(self, exp_id: str, data_id: str) -> dict:
        """最近一次成功生成谱图的运行参数(作为 SMILE 优化基参数)。"""
        for run in reversed(self._manager.project.workflow_runs):
            if (
                run.experiment_id == exp_id
                and run.workflow_ref in ("process", "reconstruct_nus")
                and str((run.inputs or {}).get("data_id", "")) in ("", data_id)
                and run.status == "success"
            ):
                return dict(run.params or {})
        return {}

    def optimize_smile(
        self,
        data,
        exp_id=None,
        data_id=None,
        progress: Callable[[str], None] | None = None,
    ) -> str:
        """SMILE 优化(可选):基于已有参数仅优化 SMILE 参数,
        多次重构去伪峰,稳定峰写入 smile_optimized/。

        progress 可选回调:扫描/去伪进度(正在优化 x/25 + 当前参数),
        不转发后端 SMILE 原始输出(与生成谱图日志区分,0.2.162-补)。"""
        from core.data.internal_data_model import SamplingMode
        from workflow.smile_optimize import optimize_smile_parameters

        self._require_manager()
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        experiment = self._read_experiment(exp_id, data_id)
        if experiment.sampling.mode is not SamplingMode.NUS:
            raise RuntimeError("SMILE 优化仅适用于 NUS 数据(当前为均匀采样)")
        base_params = self._last_spectrum_params(exp_id, data_id)
        def _smile_progress(index: int, total: int, msg: str) -> None:
            if progress is not None:
                progress(msg)

        results = optimize_smile_parameters(
            experiment,
            self._backend_instance(),
            base_params=base_params,
            progress=_smile_progress,
        )
        valid = [
            result
            for result in results
            if getattr(result, "spectrum_path", "")
            and getattr(result, "decision", "") not in ("failed", "error")
        ]
        if not valid:
            raise RuntimeError("SMILE 优化未获得可用候选")
        # 0.2.162-补9:最终保留真峰数最多的前 3 个谱(rank 1..3)
        selected = [r for r in valid if getattr(r, "rank", 0) > 0][:3]
        if not selected:
            selected = valid[:1]
        applied = []
        for rank, result in enumerate(selected, start=1):
            applied.append(
                self._apply_smile_result(exp_id, data_id, result, rank=rank)
            )
        return (
            f"{len(results)} 组候选,保留真峰最多前 {len(applied)} 个谱:"
            + ", ".join(
                f"Top{o['rank']} 真峰 {o['true_peak_count']} 个" for o in applied
            )
        )

    def _apply_smile_result(
        self, exp_id: str, data_id: str, result, rank: int = 1
    ) -> dict:
        """候选谱归位 spectra/ + 稳定峰/评分写 smile_optimized/(与 raw 同级)。

        rank=1 为活动谱(设置 data_spectrum + 运行记录 + 步骤状态);
        rank>1 仅落盘(Top-N 保留谱,文件名带 _top{rank} 后缀,0.2.162-补9)。
        返回输出路径 dict。"""
        import shutil

        from workflow.smile_optimize import write_smile_optimized_output

        source = Path(getattr(result, "spectrum_path", ""))
        spectra_dir = self._manager.data_dir(exp_id, data_id, "spectra")
        spectra_dir.mkdir(parents=True, exist_ok=True)
        if rank <= 1:
            target = spectra_dir / source.name
        else:
            target = spectra_dir / f"{source.stem}_top{rank}{source.suffix}"
        if source.is_file() and source.resolve() != target.resolve():
            shutil.copy2(source, target)
        peaks_path, report_path, reliability_path = write_smile_optimized_output(
            self._manager, exp_id, data_id, source, result, rank=rank
        )
        result.peaks_path = str(peaks_path)
        if rank <= 1:
            self._manager.set_data_spectrum(exp_id, data_id, target)
            run = self._manager.start_run(
                exp_id,
                workflow_ref="smile_optimize",
                inputs={"data_id": data_id},
                params=dict(getattr(result, "params", {}) or {}),
            )
            self._manager.finish_run(
                run.run_id,
                "success",
                outputs={
                    "spectrum_path": str(target),
                    "peaks_path": str(peaks_path),
                    "report_path": str(report_path),
                    "reliability_path": str(reliability_path),
                },
                message=str(getattr(result, "message", "") or "SMILE 优化完成")
                + f"(真峰 {getattr(result, 'true_peak_count', 0)} 个,"
                f"稳定峰 {len(getattr(result, 'stable_peaks', []))} 个)",
            )
            record_step_success(self._manager, exp_id, data_id, "smile")
            record_step_success(self._manager, exp_id, data_id, "spectrum")
            self._snapshot_step(
                exp_id,
                data_id,
                ("smile_optimize",),
                self._spectrum_scripts(exp_id, data_id),
            )
        return {
            "rank": rank,
            "params": dict(getattr(result, "params", {}) or {}),
            "spectrum_path": str(target),
            "peaks_path": str(peaks_path),
            "report_path": str(report_path),
            "reliability_path": str(reliability_path),
            "stable_count": len(getattr(result, "stable_peaks", [])),
            "true_peak_count": getattr(result, "true_peak_count", 0),
        }

    # ------------------------------------------------------------------
    # 脚本快照(GUI 接线):步骤成功后把执行的脚本/参数写入 WorkflowRun
    # ------------------------------------------------------------------
    def _snapshot_step(
        self,
        exp_id: str,
        data_id: str,
        workflow_refs: tuple[str, ...],
        scripts: dict[str, str],
    ) -> str:
        """把最近一次匹配步骤的 WorkflowRun 补写脚本快照(snapshot_run)。

        返回快照目录(空串表示无匹配运行或已快照)。后端步骤只登记运行,
        不落脚本;GUI 在此把实际执行的 fid.com/process.com/nus*.com 与
        参数写入 run.snapshot_dir,保证可复现(契约 §2)。
        """
        if self._manager is None or self._manager.project is None:
            return ""
        run = None
        for candidate in reversed(self._manager.project.workflow_runs):
            if candidate.experiment_id != exp_id:
                continue
            if candidate.workflow_ref not in workflow_refs:
                continue
            recorded_data = (candidate.inputs or {}).get("data_id", "")
            if recorded_data and recorded_data != data_id:
                continue
            run = candidate
            break
        if run is None or run.snapshot_dir:
            return ""
        try:
            snapshot = self._manager.snapshot_run(
                run.run_id, dict(scripts or {}), params=dict(run.params or {})
            )
            return str(snapshot)
        except Exception:  # noqa: BLE001 - 快照失败不阻断处理
            return ""

    def _fid_com_script(self, exp_id: str, data_id: str) -> dict[str, str]:
        """读取 process/ 下的 fid.com(0.2.91 起;旧数据回退 raw/)。"""
        if self._manager is None:
            return {}
        try:
            entry = self._manager.data(exp_id, data_id)
        except Exception:  # noqa: BLE001
            return {}
        raw = (
            Path(entry.raw_dir)
            if getattr(entry, "raw_dir", "")
            else Path(entry.source)
        )
        if not raw.is_absolute():
            raw = self._manager.root / raw
        if not raw.is_dir():
            # schema 1.3 数据级 raw 目录回退(登记缺失时)
            raw = self._manager.data_dir(exp_id, data_id, "raw")
        fid_com = self._manager.data_dir(exp_id, data_id, "process") / "fid.com"
        if not fid_com.is_file():
            fid_com = raw / "fid.com"
        if fid_com.is_file():
            return {
                "fid.com": fid_com.read_text(
                    encoding="utf-8", errors="replace"
                )
            }
        return {}

    def _spectrum_scripts(self, exp_id: str, data_id: str) -> dict[str, str]:
        """读取 process 目录下谱图脚本(process.com/nus*.com,不含 fid.com)。"""
        scripts: dict[str, str] = {}
        if self._manager is None:
            return scripts
        proc = self._manager.data_dir(exp_id, data_id, "process")
        try:
            paths = sorted(proc.glob("*.com"))
        except OSError:
            return scripts
        for path in paths:
            if path.name != "fid.com":
                scripts[path.name] = path.read_text(
                    encoding="utf-8", errors="replace"
                )
        return scripts


    def _require_manager(self) -> None:
        if self._manager is None:
            raise RuntimeError("ProcessingController 未绑定项目(ProjectManager)")
