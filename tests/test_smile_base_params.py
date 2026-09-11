"""SMILE 优化的基参数来源与模板一致性(0.2.199-补29hz-修18)。

用户 2026-09-11:「我不是一开始说的是参照终脚本只改 smile 参数吗,怎么会改到这个」——
原实现 `_last_spectrum_params` 只认 `process`/`reconstruct_nus`,而正常统一路线登记的是
`phase_optimize_unified` → 基参数恒为空,SMILE 模板从零重建(默认窗口/PS(0,0)/POLY auto),
既不等于终跑脚本,又会按默认宽窗报「内存不够」。
"""

from __future__ import annotations

from pathlib import Path

from core.project import ProjectManager
from gui.processing import ProcessingController


def _manager_with_spectrum_run(tmp_path: Path, *, ref: str, data_id: str = "d_001"):
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HNCA")
    data = manager.import_data(entry.id, "/fake/1")
    run = manager.start_run(
        entry.id,
        workflow_ref=ref,
        inputs={"data_id": data_id},
        params={
            "final_ext_lo": "8.5",
            "final_ext_hi": "7.5",
            "phases": {"F1": [357.5, 0.0]},
            "direct_phase": [161.822, 37.5],
            "baseline": {"mode": "auto"},
        },
    )
    manager.finish_run(
        run.run_id, "success", outputs={"spectrum_path": "/fake/1.ft2"}
    )
    return manager, entry.id, data.id


def test_unified_run_is_used_as_base_params(tmp_path: Path) -> None:
    """统一路线(phase_optimize_unified)的运行参数要作为 SMILE 基参数。"""
    manager, exp_id, data_id = _manager_with_spectrum_run(
        tmp_path, ref="phase_optimize_unified"
    )
    ctrl = ProcessingController(manager)

    params = ctrl._last_spectrum_params(exp_id, data_id)

    assert params["final_ext_lo"] == "8.5"
    assert params["final_ext_hi"] == "7.5"
    assert params["phases"] == {"F1": [357.5, 0.0]}
    assert params["direct_phase"] == [161.822, 37.5]


def test_other_data_run_is_ignored(tmp_path: Path) -> None:
    """严格 data_id 归属:别的数据的谱图运行不能当本数据的基参数。"""
    manager, exp_id, _data_id = _manager_with_spectrum_run(
        tmp_path, ref="phase_optimize_unified", data_id="d_002"
    )
    ctrl = ProcessingController(manager)

    assert ctrl._last_spectrum_params(exp_id, "d_001") == {}


def test_non_spectrum_run_is_ignored(tmp_path: Path) -> None:
    """峰挑选/分析等非出谱步骤的运行参数不参与。"""
    manager, exp_id, data_id = _manager_with_spectrum_run(tmp_path, ref="pick_peaks")
    ctrl = ProcessingController(manager)

    assert ctrl._last_spectrum_params(exp_id, data_id) == {}


def test_direct_phase_override_accepts_run_key() -> None:
    """模板直接维相位:显式 direct_phase_override 优先,否则认运行记录的 direct_phase。"""
    from backend.nmrpipe_backend import direct_phase_override

    assert direct_phase_override({"direct_phase_override": [10.0, 1.0]}) == (10.0, 1.0)
    assert direct_phase_override({"direct_phase": [161.822, 37.5]}) == (161.822, 37.5)
    assert direct_phase_override(
        {"direct_phase_override": [1.0, 2.0], "direct_phase": [3.0, 4.0]}
    ) == (1.0, 2.0)
    assert direct_phase_override({}) is None
    assert direct_phase_override({"direct_phase": "bad"}) is None
