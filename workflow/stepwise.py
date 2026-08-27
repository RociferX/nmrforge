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
    return experiment


def _work_dir(manager: ProjectManager, exp_id: str, data_id: str) -> Path:
    """每数据独立工作目录:<exp_id>/<data_id>/process/(契约 §9.2)。"""
    return manager.data_dir(exp_id, data_id, "process")


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
    manager.set_data_spectrum(exp_id, data_id, target)
    return str(target)


def _export_ucsf(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    spectrum_path: str,
) -> tuple[str | None, str]:
    """终谱归位后顺带生成 Sparky UCSF 文件(spectra/<data_id>.ucsf)。"""
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
    """
    experiment = _read_experiment(manager, exp_id, data_id)
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    _ensure_work_dir(backend, work)
    params = dict(params or {})
    route = str(params.pop("phase_route", "unified"))
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

    result = unified_route(
        experiment,
        backend,
        plan=plan,
        work_dir=work,
        base_params=params,
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
