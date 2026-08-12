"""步骤化处理编排(API_CONTRACT §8.3 / G2B-002)。

三步:
1. import_data —— workflow.import_workflow.import_data(只读参数 + 复制);
2. generate_fid —— backend.convert_to_fid(生成 NMRPipe fid);
3. generate_spectrum —— backend.process / reconstruct_nus(含 NUS SMILE 重构)。

相位优化:先用 SMILE 重构生成谱,再逐候选反复跑后端(暴力)优化,
最终谱由真实管线产出(workflow.phase_optimize 的暴力搜索 + 相位写回)。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from core.data.bruker_reader import read_dataset
from core.data.internal_data_model import Experiment, SamplingMode
from core.planning.method_selector import select_method
from core.project import ProjectManager
from workflow.import_workflow import ImportResult, import_data


class StepwiseError(Exception):
    """步骤化处理错误。"""


def _require_data(manager: ProjectManager, exp_id: str, data_id: str) -> Any:
    return manager.data(exp_id, data_id)


def _read_experiment(manager: ProjectManager, exp_id: str, data_id: str) -> Experiment:
    """从数据条目读取 Experiment(优先项目内 raw 副本)。"""
    data_entry = _require_data(manager, exp_id, data_id)
    source = Path(data_entry.raw_dir) if data_entry.raw_dir else Path(data_entry.source)
    if not source.is_absolute():
        source = manager.root / source
    return read_dataset(source)


def _work_dir(manager: ProjectManager, exp_id: str, data_id: str) -> Path:
    """每数据独立工作目录:<exp_id>/<data_id>/process/(契约 §9.2)。"""
    return manager.data_dir(exp_id, data_id, "process")


def _register_spectrum(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    spectrum_path: str,
) -> str:
    """把后端产出的终谱归位到 <exp_id>/<data_id>/spectra/ 并登记。"""
    source = Path(spectrum_path)
    spectra_dir = manager.data_dir(exp_id, data_id, "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    target = spectra_dir / source.name
    if source.is_file() and source.resolve() != target.resolve():
        shutil.copy2(source, target)
    manager.set_data_spectrum(exp_id, data_id, target)
    return str(target)


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
) -> str:
    """第 2 步:转换 Bruker 原始数据为 NMRPipe fid(独立阶段)。"""
    experiment = _read_experiment(manager, exp_id, data_id)
    data_entry = _require_data(manager, exp_id, data_id)
    data_dir = Path(data_entry.raw_dir) if data_entry.raw_dir else Path(data_entry.source)
    if not data_dir.is_absolute():
        data_dir = manager.root / data_dir
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    _ensure_work_dir(backend, work)
    resp = backend.convert_to_fid(experiment, data_dir)
    logs = list(resp.get("logs", []))
    if not resp.get("success"):
        raise StepwiseError(
            str(resp.get("message", "转换失败")) + " | " + " | ".join(logs)
        )
    fid_path = str(resp.get("fid_path", ""))
    manager.set_data_fid(exp_id, data_id, fid_path)
    _finish_step(
        manager,
        exp_id,
        data_id,
        "convert_to_fid",
        outputs={"fid_path": fid_path},
        message="生成 FID",
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
) -> str:
    """第 3 步:生成谱图(NUS 自动走 SMILE 重构;复用已转换 fid)。"""
    experiment = _read_experiment(manager, exp_id, data_id)
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    _ensure_work_dir(backend, work)
    params = dict(params or {})
    if experiment.sampling.mode is SamplingMode.NUS:
        workflow_ref = "reconstruct_nus"
        resp = backend.reconstruct_nus(experiment, params)
    else:
        workflow_ref = "process"
        plan = select_method(experiment)
        resp = backend.process(experiment, plan, params=params)
    logs = list(resp.get("logs", []))
    if not resp.get("success"):
        raise StepwiseError(
            str(resp.get("message", "谱图生成失败")) + " | " + " | ".join(logs)
        )
    spectrum_path = _register_spectrum(
        manager, exp_id, data_id, str(resp.get("spectrum_path", ""))
    )
    _finish_step(
        manager,
        exp_id,
        data_id,
        workflow_ref,
        outputs={"spectrum_path": spectrum_path},
        message="生成谱图",
        params=params,
    )
    return spectrum_path


def optimize_phase_brute_force(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    backend: Any,
    *,
    candidates: list[dict[str, float]] | None = None,
    work_dir: Path | str | None = None,
    score_fn: Any | None = None,
) -> dict[str, Any]:
    """相位优化:先确保 SMILE 重构谱存在,再逐候选反复跑后端(暴力)优化。

    返回 {"phase", "spectrum_path", "method", "backend_runs", "logs"}。
    """
    from workflow.phase_optimize import (
        brute_force_direct_scores,
        direct_phase_candidates,
        produce_phased_spectrum,
    )

    experiment = _read_experiment(manager, exp_id, data_id)
    data_entry = _require_data(manager, exp_id, data_id)
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    _ensure_work_dir(backend, work)
    if not data_entry.spectrum_path or not Path(data_entry.spectrum_path).is_file():
        generate_spectrum(manager, exp_id, data_id, backend, work_dir=work)
    candidates = candidates if candidates is not None else direct_phase_candidates()
    scores = brute_force_direct_scores(
        experiment, backend, candidates, score_fn=score_fn
    )
    valid = [c for c in scores if c.score > float("-inf")]
    if not valid:
        raise StepwiseError("相位优化暴力搜索全部失败")
    best = max(valid, key=lambda c: c.score)
    direct_axis = "F2" if experiment.ndim == 2 else "F3"
    resp = produce_phased_spectrum(
        experiment,
        backend,
        {direct_axis: (best.params["p0"], best.params["p1"])},
    )
    if not resp.get("success"):
        raise StepwiseError(str(resp.get("message", "相位优化最终谱生成失败")))
    spectrum_path = _register_spectrum(
        manager, exp_id, data_id, str(resp.get("spectrum_path", ""))
    )
    backend_runs = 1 + len(candidates) + 1
    _finish_step(
        manager,
        exp_id,
        data_id,
        "phase_optimize",
        outputs={"spectrum_path": spectrum_path, "phase": str(best.params)},
        message="相位优化(暴力)",
        params={"candidates": len(candidates), "phase": dict(best.params)},
    )
    return {
        "phase": dict(best.params),
        "spectrum_path": spectrum_path,
        "method": "brute_force",
        "backend_runs": backend_runs,
        "logs": [f"相位优化(暴力): 最优 {best.params} score={best.score:.1f}"],
    }


__all__ = [
    "ImportResult",
    "StepwiseError",
    "generate_fid",
    "generate_spectrum",
    "import_data",
    "optimize_phase_brute_force",
]
