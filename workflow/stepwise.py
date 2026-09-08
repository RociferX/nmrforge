"""步骤化处理编排(API_CONTRACT §8.3 / G2B-002)。

三步:
1. import_data —— workflow.import_workflow.import_data(只读参数 + 链接(G2B-009));
2. generate_fid —— backend.convert_to_fid(生成 NMRPipe fid);
3. generate_spectrum —— backend.process / reconstruct_nus(含 NUS SMILE 重构)。

相位优化:先用 SMILE 重构生成谱,再逐候选反复跑后端(暴力)优化,
最终谱由真实管线产出(内存相位搜索 + 终跑脚本写回,0.2.164 起统一)。
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend import memory_disk
from core.data.bruker_reader import read_dataset, read_dataset_container
from core.data.internal_data_model import Experiment, SamplingMode
from core.planning.method_selector import select_method
from core.project import ProjectManager
from workflow.import_workflow import ImportResult, import_data
from workflow.ucsf_export import export_ucsf


class StepwiseError(Exception):
    """步骤化处理错误。"""


def _require_data(manager: ProjectManager, exp_id: str, data_id: str) -> Any:
    return manager.data(exp_id, data_id)


def _read_experiment(manager: ProjectManager, exp_id: str, data_id: str) -> Experiment:
    """从数据条目读取 Experiment(优先项目内 raw 副本;单数据分段采集用各段目录)。"""
    data_entry = _require_data(manager, exp_id, data_id)
    if data_entry.segments:
        # 分段采集:source 是容器目录(无 acqus),各段在 data_entry.segments
        from core.data.bruker_reader import read_segments

        seg_paths: list[Path] = []
        for seg in data_entry.segments:
            seg_path = Path(seg)
            if not seg_path.is_absolute():
                seg_path = manager.root / seg_path
            seg_paths.append(seg_path)
        experiment = read_segments(seg_paths)
    else:
        source = Path(data_entry.raw_dir) if data_entry.raw_dir else Path(data_entry.source)
        if not source.is_absolute():
            source = manager.root / source
        try:
            experiment = read_dataset(source)
        except ValueError:
            # 容器目录(旧数据/分段残留)回退容器读取(0.2.164 与 manual 统一)
            experiment = read_dataset_container(source)[0]
    # 2026-08-19:中间产物/终谱前缀统一用数据 id(d_001),不随重命名变化;
    # read_dataset 的 dataset_id 取自 raw 目录名(常为 raw),必须覆盖
    experiment.dataset_id = data_id
    # 0.2.199-补29fd:用户在 GUI 选择的实验类型(metadata evidence=gui_user_selected)
    # 权威覆盖 live 分类(pdata/title/PULPROG),处理/选峰以其为准。
    _apply_gui_type_override(manager, exp_id, data_id, experiment)
    return experiment


def _apply_gui_type_override(
    manager: ProjectManager, exp_id: str, data_id: str, experiment: Experiment
) -> None:
    """若数据 metadata 的 experiment_type 标记 gui_user_selected,以其为准。"""
    try:
        from core.data.internal_data_model import ExperimentType

        data_entry = _require_data(manager, exp_id, data_id)
        path = manager.data_metadata_path(exp_id, data_id)
        if not path.is_file() and data_entry.metadata_path:
            mp = Path(data_entry.metadata_path)
            path = mp if mp.is_absolute() else manager.root / mp
        if not path.is_file():
            return
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        et = ((payload.get("dataset") or {}).get("experiment_type") or {})
        if not et:
            return
        evidence = [str(e) for e in (et.get("evidence") or [])]
        if not any("gui_user_selected" in e for e in evidence):
            return
        name = str(et.get("name", "") or "")
        if not name:
            return
        experiment.experiment_type = ExperimentType(
            name=name, confidence=1.0, evidence=evidence
        )
    except Exception:
        return


def _work_dir(manager: ProjectManager, exp_id: str, data_id: str) -> Path:
    """每数据独立工作目录:<exp_id>/<data_id>/process/(契约 §9.2)。"""
    return manager.data_dir(exp_id, data_id, "process")


def _rewrite_duplicate_nucleus_labels(
    spectrum_path: str,
    experiment: Any,
) -> bool:
    """同核谱重复标签唯一化(0.2.199-补29af/补29ag/补29ah)。

    proj3D 按 FDF 标签选轴,重复核标签即歧义;处理时就改:重复核
    按下标优先级「直接维 > acqu2 > acqu3」依次加 x/y/z(2D 双 1H:
    F2(直接)→1Hx、F1→1Hy;3D 三同核:F3(直接)→1Hx、F2→1Hy、
    F1→1Hz;HNN 双 15N:F2(acqu2,HSQC 的 N)→15Nx、F1→15Ny)。
    GUI 的 nucleus_symbol 会把 15Nx 显示为 Nx。返回是否改写。
    """
    import nmrglue as ng
    import numpy as np

    try:
        dic, data = ng.pipe.read(str(spectrum_path))
        data = np.asarray(data)
        ndim = data.ndim
        if ndim not in (2, 3):
            return False
        order = [int(v) for v in dic.get("FDDIMORDER") or []]

        def _fdf(axis_idx: int) -> str:
            if len(order) >= ndim:
                dim = order[ndim - 1 - axis_idx]
                if 1 <= dim <= 4:
                    return f"FDF{dim}"
            return f"FDF{axis_idx + 1}"

        # numpy 存储轴逻辑名:2D (F1,F2);3D (F2,F1,F3)
        logical = ["F1", "F2"] if ndim == 2 else ["F2", "F1", "F3"]
        # 重复核下标优先级:直接维 > acqu2 > acqu3
        # (2D:F2 直接维、F1=acqu2;3D:F3 直接维、F2=acqu2、F1=acqu3)
        priority = ["F2", "F1"] if ndim == 2 else ["F3", "F2", "F1"]
        labels = [
            str(dic.get(f"{_fdf(i)}LABEL", "") or "") for i in range(ndim)
        ]
        counts: dict[str, int] = {}
        for lbl in labels:
            counts[lbl] = counts.get(lbl, 0) + 1
        dups = {lbl for lbl, c in counts.items() if c > 1 and lbl}
        if not dups:
            return False
        changed = False
        for dup in sorted(dups):
            dup_axes = sorted(
                (i for i, lbl in enumerate(labels) if lbl == dup),
                key=lambda i: priority.index(logical[i]),
            )
            for rank, axis_idx in enumerate(dup_axes):
                suf = "xyz"[rank] if rank < 3 else str(rank + 1)
                dic[f"{_fdf(axis_idx)}LABEL"] = dup + suf
                changed = True
        if changed:
            ng.pipe.write(str(spectrum_path), dic, data, overwrite=True)
        return changed
    except Exception:  # noqa: BLE001 - 标签改写失败不影响谱图
        return False


def _register_spectrum(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    spectrum_path: str,
) -> str:
    """把后端产出的终谱归位到 <exp_id>/<data_id>/spectra/(move,process 不留副本)并登记。"""
    source = Path(spectrum_path)
    spectra_dir = manager.data_dir(exp_id, data_id, "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    # 2026-08-19:终谱命名前缀用数据 id(d_001),重命名不影响
    target = spectra_dir / f"{data_id}{source.suffix}"
    if source.is_file() and source.resolve() != target.resolve():
        shutil.move(str(source), str(target))
    # 0.2.199-补29af:同核谱(HNN/NNH 双 15N)唯一化标签(15Nx/15Ny),
    # 使 proj3D 按标签选轴可用;改写后投影与 GUI 均显示 Nx/Ny
    try:
        experiment = _read_experiment(manager, exp_id, data_id)
        _rewrite_duplicate_nucleus_labels(str(target), experiment)
    except Exception:  # noqa: BLE001 - 改写失败不影响谱图登记
        pass
    manager.set_data_spectrum(exp_id, data_id, target)
    return str(target)


def _export_ucsf(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    spectrum_path: str,
) -> tuple[str | None, str]:
    """终谱归位后顺带生成 Sparky UCSF 文件(spectra/<data_id>.ucsf)。"""
    # 0.2.199-补29gj-修(用户):1D 谱(.ft1)不生成 UCSF 文件——UCSF 面向 2D/3D,
    # pipe2ucsf 对 1D 无意义;直接跳过,避免多余产物与失败日志。
    if Path(spectrum_path).suffix.lower() == ".ft1":
        return None, "1D 谱不生成 UCSF 文件(跳过)"
    spectra_dir = manager.data_dir(exp_id, data_id, "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    target = spectra_dir / f"{Path(spectrum_path).stem}.ucsf"
    return export_ucsf(spectrum_path, target)


def _ensure_work_dir(backend: Any, work: Path) -> None:
    """把后端工作目录固定到数据级目录(backend.work_dir 可写时)。"""
    if hasattr(backend, "work_dir"):
        backend.work_dir = str(work)


def _finish_step(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    workflow_ref: str,
    outputs: dict[str, str],
    message: str,
    params: dict[str, Any] | None = None,
) -> str:
    """登记一次步骤 WorkflowRun(只追加,审计)。"""
    run = manager.start_run(
        exp_id,
        workflow_ref=workflow_ref,
        inputs={"data_id": data_id},
        params=dict(params or {}),
    )
    manager.finish_run(run.run_id, "success", outputs=outputs, message=message)
    return run.run_id


def generate_fid(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    backend: Any,
    *,
    work_dir: Path | str | None = None,
    progress: Callable[[str], None] | None = None,
) -> str:
    """第 2 步:转换 Bruker 原始数据为 NMRPipe fid(独立阶段)。"""
    experiment = _read_experiment(manager, exp_id, data_id)
    data_entry = _require_data(manager, exp_id, data_id)
    data_dir = Path(data_entry.raw_dir) if data_entry.raw_dir else Path(data_entry.source)
    if not data_dir.is_absolute():
        data_dir = manager.root / data_dir
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    _ensure_work_dir(backend, work)
    resp = backend.convert_to_fid(experiment, data_dir, progress=progress)
    logs = list(resp.get("logs", []))
    if not resp.get("success"):
        raise StepwiseError(
            str(resp.get("message", "转换失败")) + " | " + " | ".join(logs)
        )
    # 0.2.199-补29gu:成功也把后端详细日志转发 progress(组批/单个都能看到)
    if progress is not None:
        for _lg in logs:
            progress(_lg)
    fid_path = str(resp.get("fid_path", ""))
    manager.set_data_fid(exp_id, data_id, fid_path)
    merged_params = dict(resp.get("effective_params") or {})
    _finish_step(
        manager,
        exp_id,
        data_id,
        "convert_to_fid",
        outputs={"fid_path": fid_path},
        message="生成 FID",
        params=merged_params,
    )
    return fid_path



def _apply_note_overrides(manager, exp_id: str, data_id: str, experiment) -> None:
    """用数据注释(数据类型的实验类型/峰符号)覆盖自动分类与预设(0.2.199-补29hc)。"""
    try:
        project = getattr(manager, "project", None)
        if project is None:
            return
        exp = project.experiment(exp_id)
        if exp is None:
            return
        note = ((exp.metadata or {}).get("data_notes") or {}).get(data_id)
        if not isinstance(note, dict):
            return
        tname = str(note.get("experiment_type", "") or "").strip()
        if tname:
            from gui.notes import experiment_type_options
            if tname in experiment_type_options(experiment.ndim):
                # 0.2.199-补29hc:填的是类型名字符串,须构造 ExperimentType
                # 对象(.name/.confidence/.evidence),否则下游 select_method/
                # import_workflow 访问 .confidence 报 AttributeError。
                from core.data.internal_data_model import ExperimentType
                experiment.experiment_type = ExperimentType(
                    name=tname, confidence=1.0, evidence=["data_note"]
                )
        psign = str(note.get("peak_sign", "") or "").strip()
        if psign in ("uniform", "mixed"):
            experiment.note_peak_sign = psign
    except Exception:  # noqa: BLE001 - 注释覆盖失败不阻断
        pass


def _default_phase_route(experiment) -> str:
    """按维度选默认 phase_route:1D 无间接维,直连 process(补29gj)。"""
    return "none" if int(getattr(experiment, "ndim", 2) or 2) == 1 else "unified"


def generate_spectrum(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    backend: Any,
    *,
    params: dict[str, Any] | None = None,
    work_dir: Path | str | None = None,
    progress: Callable[[str], None] | None = None,
) -> str:
    """第 3 步:生成谱图(NUS 自动走 SMILE 重构;复用已转换 fid)。

    params["phase_route"] 选择处理途径:
    - "unified"(默认):统一方案——第一遍逐维复型预览(仅搜索轴
      不加 -di),内存调相(旧算法判断标准,零额外后端),完整终跑;
    - "none":保持旧路径,直接 process/reconstruct_nus,不额外优化
      (逃生口)。

    0.2.199-补29ey:未显式指定 work_dir 时,按 processing.intermediate_memory
    自适应把中间谱工作目录放到内存盘(内存余量充足时),用完整体删除。
    """
    experiment = _read_experiment(manager, exp_id, data_id)
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    _ensure_work_dir(backend, work)

    def _sweep_intermediates() -> None:
        """清理本数据 unified 中间产物残留(0.2.199-补29gi)。

        运行前与结束各执行一次:运行前清上次硬中断(SIGKILL/断电)遗留,
        结束(含异常)清本次残留;只删中间产物,终谱/最终脚本/fid 保留。
        """
        from workflow.phase_routes import _cleanup_unified_intermediates

        _cleanup_unified_intermediates(
            work,
            experiment.dataset_id,
            experiment=experiment,
            backend=backend,
        )

    _sweep_intermediates()
    memory_dir: Path | None = None
    if work_dir is None:
        # 0.2.199-补29ez(用户方案):中间产物统一收进 work/_intermediate 子目录,
        # 内存余量充足时该子目录符号链接到内存盘;工作目录其余内容(fid/脚本/
        # phase.json/终跑)一律保持原逻辑在磁盘。
        _intermediate_root, memory_dir = memory_disk.prepare_intermediate(
            work, experiment, params=params
        )
        if memory_dir is not None and progress is not None:
            progress(f"中间谱工作目录使用内存盘(自适应): {_intermediate_root}")
        elif progress is not None:
            _reason = memory_disk.selection_reason(experiment, params=params)
            progress(
                "中间谱工作目录使用磁盘"
                + (f"({_reason})" if _reason else "(内存目录创建失败)")
            )
    try:
        return _generate_spectrum_impl(
            manager,
            exp_id,
            data_id,
            backend,
            work=work,
            params=params,
            progress=progress,
        )
    finally:
        # 0.2.199-补29gi:异常/中断也不留中间产物(运行前清扫兜底硬中断)
        _sweep_intermediates()
        if work_dir is None:
            memory_disk.teardown_intermediate(work, memory_dir)


def _generate_spectrum_impl(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    backend: Any,
    *,
    work: Path,
    params: dict[str, Any] | None = None,
    progress: Callable[[str], None] | None = None,
) -> str:
    """原 generate_spectrum 主体(工作目录已由外层决定)。"""
    experiment = _read_experiment(manager, exp_id, data_id)
    params = dict(params or {})
    route = str(params.pop("phase_route", _default_phase_route(experiment)))
    # 0.2.199-补29hc:数据注释里的"数据类型/峰符号"优先于自动分类/预设。
    _apply_note_overrides(manager, exp_id, data_id, experiment)
    plan = select_method(experiment)
    if route == "none":
        # 0.2.162-补15:逃生口只有一次运行,直接把终跑直接维范围映射到 ext
        for key, target_key in (("final_ext_lo", "ext_lo"), ("final_ext_hi", "ext_hi")):
            if key in params and str(params[key]).strip():
                params[target_key] = str(params[key])
            params.pop(key, None)
        if experiment.sampling.mode is SamplingMode.NUS:
            workflow_ref = "reconstruct_nus"
            resp = backend.reconstruct_nus(experiment, params, progress=progress)
        else:
            workflow_ref = "process"
            resp = backend.process(
                experiment, plan, params=params, progress=progress
            )
        logs = list(resp.get("logs", []))
        if not resp.get("success"):
            raise StepwiseError(
                str(resp.get("message", "谱图生成失败")) + " | " + " | ".join(logs)
            )
        # 0.2.199-补29gu:成功也把后端详细日志转发 progress
        if progress is not None:
            for _lg in logs:
                progress(_lg)
        spectrum_path = _register_spectrum(
            manager, exp_id, data_id, str(resp.get("spectrum_path", ""))
        )
        ucsf_path, ucsf_msg = _export_ucsf(
            manager, exp_id, data_id, spectrum_path
        )
        if progress is not None:
            progress(ucsf_msg)
        merged_params = dict(resp.get("effective_params") or {})
        merged_params.update(params)
        outputs: dict[str, str] = {"spectrum_path": spectrum_path}
        if ucsf_path:
            outputs["ucsf_path"] = ucsf_path
        _finish_step(
            manager,
            exp_id,
            data_id,
            workflow_ref,
            outputs=outputs,
            message="生成谱图",
            params=merged_params,
        )
        return spectrum_path

    if route != "unified":
        raise StepwiseError(f"未知 phase_route: {route}")

    from workflow.phase_routes import unified_route

    try:
        result = unified_route(
            experiment,
            backend,
            plan=plan,
            work_dir=work,
            base_params=params,
            progress=progress,
        )
    except (RuntimeError, StepwiseError) as exc:  # noqa: BLE001
        # 0.2.199-补29gy:统一自动相位复型预览对某些数据(如固体 CANH)会失败
        # (NMRPipe 报 data in Frequency Domain / Broken pipe);回退 phase_route=none
        # 逃生口,复用已转换 fid,保证正常出谱(相位 p0=p1=0)。
        msg = str(exc)
        if "复型预览" not in msg and "NMRPipe 处理失败" not in msg:
            raise
        if progress is not None:
            progress(f"统一复型预览失败({msg});回退 phase_route=none")
        fb_params = dict(params)
        fb_params["phase_route"] = "none"
        return _generate_spectrum_impl(
            manager,
            exp_id,
            data_id,
            backend,
            work=work,
            params=fb_params,
            progress=progress,
        )
    workflow_ref = "phase_optimize_unified"

    if not result.get("spectrum_path"):
        raise StepwiseError("相位优化未产出谱图")
    spectrum_path = _register_spectrum(
        manager, exp_id, data_id, str(result.get("spectrum_path"))
    )
    ucsf_path, ucsf_msg = _export_ucsf(manager, exp_id, data_id, spectrum_path)
    if progress is not None:
        progress(ucsf_msg)
    merged_params = dict(params)
    merged_params["phase_route"] = route
    for key in (
        "phases",
        "baseline",
        "zero_fill",
        "window",
        "fill",
        "backend_runs",
        "direct_phase",
        "diagnostics",
    ):
        if key in result:
            merged_params[key] = result[key]
    run_id = _finish_step(
        manager,
        exp_id,
        data_id,
        workflow_ref,
        outputs=(
            {"spectrum_path": spectrum_path, "ucsf_path": ucsf_path}
            if ucsf_path
            else {"spectrum_path": spectrum_path}
        ),
        message="生成谱图",
        params=merged_params,
    )
    # Task E(0.2.133):3D 终谱用 NMRPipe proj3D.tcl 生成三个投影,落
    # spectra/<id>_<核A>-<核B>.ft2(文件名含平面实际两核,GUI 以
    # <data_id>_*.ft2 通配扫描,旧 *_proj_*.ft2 亦兼容)。
    if experiment.ndim >= 3 and getattr(backend, "project_3d", None):
        try:
            spectra_dir = manager.data_dir(exp_id, data_id, "spectra")
            proj = backend.project_3d(
                spectrum_path,
                spectra_dir,
                prefix=f"{data_id}_proj",
            )
            labels = proj.get("labels", {})
            nuclei = proj.get("nuclei", {})
            for tag, path in proj.get("paths", {}).items():
                if proj.get("numpy_fallback"):
                    # 0.2.199-补29w:HNN 等重复核标签——核名投影会冲突
                    # (两个 15N-1H 平面),用固定逻辑轴命名 {data_id}_proj_F{n},
                    # GUI _proj_F{n} 兼容解析;后端已按同名写出,无需重命名
                    logical = str(tag)
                    target = spectra_dir / f"{data_id}_proj_{logical}.ft2"
                else:
                    fixed_nucleus = str(labels.get(tag, "") or "")
                    logical = next(
                        (
                            dim.logical_axis
                            for dim in experiment.dimensions
                            if dim.nucleus == fixed_nucleus
                        ),
                        "",
                    )
                    target = spectra_dir / projection_filename(
                        data_id, nuclei.get(tag), logical, tag
                    )
                if Path(path) != target:
                    if target.exists():
                        target.unlink()
                    Path(path).replace(str(target))
                merged_params.setdefault("projections", {})[logical or tag] = str(
                    target
                )
        except Exception as exc:  # noqa: BLE001 - 投影失败不阻断谱图
            merged_params.setdefault("projections", {})["error"] = str(exc)
        _run = manager.project.run(run_id)
        if _run is not None and "projections" in merged_params:
            # 0.2.133:投影注册写回运行参数(供 GUI/汇报读取)
            _run.params["projections"] = merged_params["projections"]
    # 0.2.199-补29t:报告缓存落盘必须放在投影注册之后——run.params 会
    # 追加 projections,记录若提前写入则指纹与 GUI 读取的 run.params 不
    # 匹配,报告永远显示「无报告记录」(此前 3D 谱全部中招)。
    try:
        from workflow.optimization_report import (
            report_text_from_logs,
            write_quality_record,
        )

        report_text = report_text_from_logs(list(result.get("logs", [])))
        if report_text:
            write_quality_record(spectrum_path, merged_params, report_text)
    except Exception:  # noqa: BLE001 - 缓存记录失败不影响谱图生成
        pass
    return spectrum_path


def projection_filename(
    data_id: str,
    nuclei: list[str] | None,
    logical: str,
    tag: str,
) -> str:
    """投影文件名(0.2.133):d_001_15N-1H.ft2 —— 含平面实际两核。

    核缺失/不可用时回退旧名 d_001_proj_<logical|tag>.ft2(仍被 GUI
    <data_id>_*.ft2 与 *_proj_*.ft2 通配扫描命中,兼容历史文件)。
    """
    if nuclei and len(nuclei) >= 2:
        safe = [
            re.sub(r"[^A-Za-z0-9]", "", str(nuc or ""))
            for nuc in nuclei[:2]
        ]
        if all(safe):
            return f"{data_id}_{safe[0]}-{safe[1]}.ft2"
    return f"{data_id}_proj_{logical or tag}.ft2"






__all__ = [
    "ImportResult",
    "StepwiseError",
    "generate_fid",
    "generate_spectrum",
    "import_data",
]
