"""处理流程控制:自动化与人工两条路径。

- 步骤化(契约 v1.2 / G2B-002):``import_data`` → ``generate_fid`` →
  ``generate_spectrum``,每步独立按钮与状态;旧 ``auto_run`` 保留兼容;
- 人工:``manual_fid_com`` / ``run_manual_fid_com`` / ``manual_scripts`` /
  ``run_manual_spectrum`` 对接 workflow/manual(fid.com 与谱图脚本)。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from core.app_paths import resource_path
from core.data.bruker_reader import read_dataset
from core.project import ExperimentEntry, ProjectManager
from gui.pipeline_state import record_step_success
from workflow.engine import AutoProcessor


def _load_config() -> dict:
    import yaml

    path = resource_path("config/nmrforge.yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


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
        """惰性创建 ProcessingBackend(与旧 auto_run 共用同一后端)。"""
        if self._backend is None:
            from backend.factory import create_backend

            self._backend = create_backend(_load_config())
        return self._backend

    # ------------------------------------------------------------------
    # 自动化路径
    # ------------------------------------------------------------------
    def auto_run_sync(self, entry: ExperimentEntry) -> dict:
        """同步执行自动化处理(供后台线程调用):返回状态/报告/日志。"""
        from backend.factory import create_backend

        experiment = read_dataset(Path(entry.source))
        backend = self._backend or create_backend(_load_config())
        result = AutoProcessor(backend).run(experiment)
        logs = list(result.logs or [])
        message = ""
        if result.quality is not None:
            message = f"QC: {result.quality.decision.value}"
        elif result.report is not None:
            message = f"谱图: {result.report}"
        return {
            "status": result.status,
            "message": message,
            "logs": logs,
            "experiment_id": entry.id,
        }

    def auto_run_async(
        self,
        entry: ExperimentEntry,
        on_done: Callable[[dict], None],
        on_error: Callable[[str], None],
    ) -> None:
        """后台线程运行自动化处理(避免阻塞 UI)。"""
        import threading

        def worker() -> None:
            try:
                on_done(self.auto_run_sync(entry))
            except Exception as exc:  # noqa: BLE001 - 错误统一回传 UI
                on_error(f"{type(exc).__name__}: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # 步骤化处理(G2B-002 / 契约 v1.2):导入数据 → 生成 FID → 生成谱图
    # 实现依赖 Backend 的 DataEntry 层级与 convert_to_fid(待 Backend 落地),
    # 当前提供签名与占位实现;GUI 界面按此接口接线。
    # ------------------------------------------------------------------
    def import_data(self, entry: ExperimentEntry, source: str, copy: bool = True) -> dict:
        """第 1 步:导入数据(只读参数 + 复制 raw),返回 ImportResult dict。"""
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

    def generate_fid(self, data, exp_id: str | None = None, data_id: str | None = None) -> str:
        """第 2 步:生成 FID(backend.convert_to_fid),返回 fid 路径。"""
        from workflow.stepwise import generate_fid as stepwise_fid

        if self._manager is None:
            raise RuntimeError("ProcessingController 未绑定项目(ProjectManager)")
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        fid_path = stepwise_fid(
            self._manager, exp_id, data_id, self._backend_instance()
        )
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
        self, data, exp_id: str | None = None, data_id: str | None = None
    ) -> str:
        """第 3 步:生成谱图(process/reconstruct_nus,含 NUS SMILE 重构)。"""
        from workflow.stepwise import generate_spectrum as stepwise_spectrum

        if self._manager is None:
            raise RuntimeError("ProcessingController 未绑定项目(ProjectManager)")
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        spectrum_path = stepwise_spectrum(
            self._manager, exp_id, data_id, self._backend_instance()
        )
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
    def pick_peaks(self, data, exp_id: str | None = None, data_id: str | None = None) -> dict:
        """峰挑选:调 workflow.pick_peaks,返回 {status, peak_path, peak_count, logs}。"""
        try:
            from workflow.pick_peaks import pick_peaks as backend_pick_peaks
        except ImportError as exc:  # pragma: no cover - Backend 未落地
            raise NotImplementedError("峰挑选(workflow.pick_peaks)待 Backend 实现") from exc
        if self._manager is None:
            raise RuntimeError("ProcessingController 未绑定项目(ProjectManager)")
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        result = backend_pick_peaks(self._manager, exp_id, data_id)
        if data_id and result.get("status") == "success":
            record_step_success(self._manager, exp_id, data_id, "peaks")
        self._manager.save()
        return result

    def analyze(self, data, exp_id: str | None = None, data_id: str | None = None) -> dict:
        """分析(峰归属/统计):调 workflow.analyze;接口占位。"""
        try:
            from workflow.analyze import analyze as backend_analyze
        except ImportError as exc:  # pragma: no cover - Backend 未落地
            raise NotImplementedError("分析(workflow.analyze)待 Backend 实现") from exc
        if self._manager is None:
            raise RuntimeError("ProcessingController 未绑定项目(ProjectManager)")
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        result = backend_analyze(self._manager, exp_id, data_id)
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
        self, data, content: str, exp_id: str | None = None, data_id: str | None = None
    ) -> str:
        """写入并运行修改后的 fid.com。"""
        from workflow.manual import run_manual_fid_com as backend_run_fid

        self._require_manager()
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        result = backend_run_fid(self._manager, exp_id, data_id, content)
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
        self, data, scripts: dict, exp_id: str | None = None, data_id: str | None = None
    ) -> str:
        """运行谱图脚本(消费已转换 fid)。"""
        from workflow.manual import run_manual_spectrum as backend_run_spectrum

        self._require_manager()
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        result = backend_run_spectrum(
            self._manager, exp_id, data_id, scripts
        )
        if data_id:
            record_step_success(self._manager, exp_id, data_id, "spectrum")
            self._snapshot_step(
                exp_id, data_id, ("manual_process", "manual_nus"), scripts
            )
        self._manager.save()
        return result

    def save_peaks_manual(
        self, data, peaks: list[dict], exp_id: str | None = None, data_id: str | None = None
    ) -> str:
        """人工峰表编辑回写(CSV)并登记 WorkflowRun(manual_peaks)。"""
        from core.peaks.peak_table import save_peaks

        self._require_manager()
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        peaks_dir = self._manager.data_dir(exp_id, data_id, "peaks")
        peaks_dir.mkdir(parents=True, exist_ok=True)
        csv_path = save_peaks(peaks_dir / f"{exp_id}-{data_id}.csv", peaks)
        if data_id:
            record_step_success(self._manager, exp_id, data_id, "peaks")
        run = self._manager.start_run(
            exp_id,
            workflow_ref="manual_peaks",
            inputs={"data_id": data_id},
            params={"mode": "manual", "peaks": len(peaks)},
        )
        try:
            self._manager.finish_run(
                run.run_id, "success", outputs={"peaks": str(csv_path)},
                message=f"人工峰表编辑({len(peaks)} 峰)",
            )
        except Exception:  # noqa: BLE001
            self._manager.finish_run(run.run_id, "failed", message="峰表保存失败")
        self._manager.save()
        return str(csv_path)

    def param_schema(self) -> dict:
        """处理计划参数 schema(param_schema 契约;缺失时返回可编辑默认骨架)。"""
        try:
            from backend.script_generator import param_schema as backend_schema

            schema = backend_schema()
            if isinstance(schema, dict) and schema:
                return schema
        except Exception:  # noqa: BLE001 - 后端未落地时用默认骨架
            pass
        return {
            'type': 'object',
            'title': 'NMRForge 处理参数',
            'description': '处理计划参数(表格编辑器/脚本渲染共享数据源)',
            'properties': {
                'zero_fill': {
                    'type': 'integer',
                    'default': 2,
                    'description': '间接维零填充倍数(auto 按 2 的幂)',
                },
                'sampling': {
                    'type': 'object',
                    'description': '采样/采集相关标志',
                    'properties': {
                        'ft_neg': {
                            'type': 'boolean',
                            'default': False,
                            'description': 'FT 后翻转该轴',
                        },
                        'ft_alt': {
                            'type': 'boolean',
                            'default': True,
                            'description': 'TPPI/States-TPPI ± 交替修正',
                        },
                        'flip_f1': {
                            'type': 'boolean',
                            'default': False,
                            'description': 'F1 轴翻转',
                        },
                        'auto_phase': {
                            'type': 'boolean',
                            'default': True,
                            'description': '直接维 p1 共识自动相位',
                        },
                    },
                },
                'ext_lo': {
                    'type': 'string',
                    'default': '11.0',
                    'description': '直接维 1H 提取窗口高 ppm(EXT -x1)',
                },
                'ext_hi': {
                    'type': 'string',
                    'default': '6.0',
                    'description': '直接维 1H 提取窗口低 ppm(EXT -xn)',
                },
                'extract': {
                    'type': 'boolean',
                    'default': True,
                    'description': '直接维提取窗口是否开启',
                },
                'stages': {
                    'type': 'array',
                    'description': '处理阶段列表(表格编辑器逐行展示)',
                    'items': {'type': 'object', 'properties': {}},
                },
            },
            'default': {
                'zero_fill': 2,
                'ext_lo': '11.0',
                'ext_hi': '6.0',
                'extract': True,
                'sampling': {
                    'ft_neg': False,
                    'ft_alt': True,
                    'flip_f1': False,
                    'auto_phase': True,
                },
                'stages': [],
            },
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
        """读取 raw 目录下的 fid.com(自动/人工 FID 步骤脚本)。"""
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
