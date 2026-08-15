"""SMILE 优化步骤测试:状态推断(可选)+ ProcessingController 接线。"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.project import ProjectManager
from gui.pipeline_panel import PIPELINE_STEPS, compute_step_statuses
from gui.pipeline_state import load_pipeline_state, record_step_success
from gui.processing import ProcessingController


def _manager_with_artifacts(tmp_path: Path):
    """实验类型 + 样品数据 + fid/谱图(无峰表/报告)。"""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("NUS")
    data = manager.import_data(entry.id, "/fake/1")
    exp_id, data_id = entry.id, data.id
    process = manager.data_dir(exp_id, data_id, "process")
    process.mkdir(parents=True, exist_ok=True)
    fid = process / f"{exp_id}-{data_id}.fid"
    fid.write_bytes(b"fid")
    manager.set_data_fid(exp_id, data_id, fid)
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    ft2 = spectra / f"{exp_id}-{data_id}.ft2"
    ft2.write_bytes(b"ft2")
    manager.set_data_spectrum(exp_id, data_id, ft2)
    manager.save()
    return manager, exp_id, data_id, ft2


def test_smile_step_position_in_pipeline() -> None:
    ids = [step[0] for step in PIPELINE_STEPS]
    assert ids.index("smile") == 3  # 生成谱图之后、峰挑选之前


def test_smile_status_optional_and_outdated(tmp_path: Path) -> None:
    manager, exp_id, data_id, ft2 = _manager_with_artifacts(tmp_path)
    record_step_success(manager, exp_id, data_id, "spectrum")
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["smile"] == "READY"  # 谱图完成后可运行
    assert statuses["peaks"] == "READY"  # 可选:峰挑选不依赖 smile
    # 运行 smile 后 SUCCESS
    record_step_success(manager, exp_id, data_id, "smile")
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["smile"] == "SUCCESS"
    # 谱图重新生成 → smile 过期(输入指纹变化)
    ft2.write_bytes(b"ft2-v2")
    record_step_success(manager, exp_id, data_id, "spectrum")
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["smile"] == "OUTDATED"


def test_apply_smile_result(tmp_path: Path) -> None:
    manager, exp_id, data_id, _ft2 = _manager_with_artifacts(tmp_path)
    best_spec = tmp_path / "best.ft2"
    best_spec.write_bytes(b"best")
    result = SimpleNamespace(
        spectrum_path=str(best_spec),
        params={"nsigma": 5.0, "thresh": 0.9},
        message="ok",
    )
    controller = ProcessingController(manager)
    target = controller._apply_smile_result(exp_id, data_id, result)
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    assert Path(target) == spectra / "best.ft2"
    assert Path(target).is_file()
    runs = [
        r
        for r in manager.project.workflow_runs
        if r.workflow_ref == "smile_optimize"
    ]
    assert len(runs) == 1 and runs[0].status == "success"
    assert runs[0].params["nsigma"] == 5.0
    assert runs[0].snapshot_dir  # 脚本快照补写
    state = load_pipeline_state(manager, exp_id, data_id)
    assert "smile" in state["steps"]
    assert "spectrum" in state["steps"]


def test_optimize_smile_rejects_uniform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.data.internal_data_model import SamplingMode

    manager, exp_id, data_id, _ft2 = _manager_with_artifacts(tmp_path)
    controller = ProcessingController(manager)
    experiment = SimpleNamespace(
        sampling=SimpleNamespace(mode=SamplingMode.UNIFORM)
    )
    monkeypatch.setattr(controller, "_read_experiment", lambda *a, **k: experiment)
    with pytest.raises(RuntimeError, match="NUS"):
        controller.optimize_smile(None, exp_id=exp_id, data_id=data_id)
