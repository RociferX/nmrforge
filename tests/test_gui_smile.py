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
    """0.2.162:最优谱归位 + 稳定峰写入 smile_optimized/(与 raw 同级)。"""
    from workflow.smile_optimize import SmileParameterResult

    manager, exp_id, data_id, _ft2 = _manager_with_artifacts(tmp_path)
    best_spec = tmp_path / "best.ft2"
    _write_gui_ft2(best_spec)
    result = SmileParameterResult(
        params={"nsigma": 5.0, "thresh": 0.9},
        spectrum_path=str(best_spec),
        overall=70.0,
        stable_peaks=[
            {"position": [10.0, 20.0], "height": 30.0, "snr": 15.0},
            {"position": [30.0, 40.0], "height": 25.0, "snr": 12.0},
        ],
        message="ok",
    )
    controller = ProcessingController(manager)
    target = controller._apply_smile_result(exp_id, data_id, result)
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    assert Path(target) == spectra / "best.ft2"
    assert Path(target).is_file()
    # smile_optimized/ 与 raw 同级:稳定峰 CSV + 评分 JSON
    opt_dir = manager.data_dir(exp_id, data_id, "smile_optimized")
    csv_path = opt_dir / f"{exp_id}-{data_id}_smile_optimized.csv"
    json_path = opt_dir / f"{exp_id}-{data_id}_smile_optimized.json"
    assert csv_path.is_file()
    assert json_path.is_file()
    text = csv_path.read_text(encoding="utf-8")
    assert "H_shift" in text and "N_shift" in text
    runs = [
        r
        for r in manager.project.workflow_runs
        if r.workflow_ref == "smile_optimize"
    ]
    assert len(runs) == 1 and runs[0].status == "success"
    assert runs[0].params["nsigma"] == 5.0
    assert str(runs[0].outputs.get("peaks_path")) == str(csv_path)
    assert runs[0].snapshot_dir  # 脚本快照补写
    state = load_pipeline_state(manager, exp_id, data_id)
    assert "smile" in state["steps"]
    assert "spectrum" in state["steps"]


def _write_gui_ft2(path: Path, shape: tuple[int, int] = (64, 64)) -> None:
    """写合法 NMRPipe 2D ft2(供 smile_optimized CSV 读取)。"""
    import numpy as np
    from nmrglue.fileio import pipe

    data = np.zeros(shape, dtype=np.float32)
    data[10, 20] = 30.0
    data[30, 40] = 25.0
    dic = {k: "0" for k in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = shape[1]
    dic["FDSPECNUM"] = shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    for prefix in ("FDF1", "FDF2"):
        dic[prefix + "SW"] = 6000.0
        dic[prefix + "OBS"] = 600.0
        dic[prefix + "CAR"] = 4.7
        dic[prefix + "ORIG"] = 4.7 * 600.0
    pipe.write(str(path), dic, data, overwrite=True)

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
