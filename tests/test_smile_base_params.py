"""Where SMILE optimization base parameters come from, and template consistency
(0.2.199-patch29hz-fix18).

The user, 2026-09-11: "didn't I say from the start that we only change the smile
parameters against the final script, how did it end up changing this" -- the old
`_last_spectrum_params` recognized only `process`/`reconstruct_nus`, while the normal
unified route registers `phase_optimize_unified` -> the base parameters were always empty
and the SMILE template was rebuilt from scratch (default window / PS(0,0) / POLY auto),
which neither equals the final-run script nor avoids reporting "not enough memory" against
the default wide window.
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
    manager.finish_run(run.run_id, "success", outputs={"spectrum_path": "/fake/1.ft2"})
    return manager, entry.id, data.id


def test_unified_run_is_used_as_base_params(tmp_path: Path) -> None:
    """The run parameters of the unified route (phase_optimize_unified) must serve as the
    SMILE base parameters.
    """
    manager, exp_id, data_id = _manager_with_spectrum_run(tmp_path, ref="phase_optimize_unified")
    ctrl = ProcessingController(manager)

    params = ctrl._last_spectrum_params(exp_id, data_id)

    assert params["final_ext_lo"] == "8.5"
    assert params["final_ext_hi"] == "7.5"
    assert params["phases"] == {"F1": [357.5, 0.0]}
    assert params["direct_phase"] == [161.822, 37.5]


def test_other_data_run_is_ignored(tmp_path: Path) -> None:
    """Strict data_id ownership: a spectrum run of another data entry must not become this
    data entry's base parameters.
    """
    manager, exp_id, _data_id = _manager_with_spectrum_run(
        tmp_path, ref="phase_optimize_unified", data_id="d_002"
    )
    ctrl = ProcessingController(manager)

    assert ctrl._last_spectrum_params(exp_id, "d_001") == {}


def test_non_spectrum_run_is_ignored(tmp_path: Path) -> None:
    """Run parameters of non-spectrum steps (peak picking, analysis, ...) do not take
    part.
    """
    manager, exp_id, data_id = _manager_with_spectrum_run(tmp_path, ref="pick_peaks")
    ctrl = ProcessingController(manager)

    assert ctrl._last_spectrum_params(exp_id, data_id) == {}


def test_direct_phase_override_accepts_run_key() -> None:
    """Template direct-dimension phase: an explicit direct_phase_override wins, otherwise
    the run record's direct_phase is used.
    """
    from backend.nmrpipe_backend import direct_phase_override

    assert direct_phase_override({"direct_phase_override": [10.0, 1.0]}) == (10.0, 1.0)
    assert direct_phase_override({"direct_phase": [161.822, 37.5]}) == (161.822, 37.5)
    assert direct_phase_override(
        {"direct_phase_override": [1.0, 2.0], "direct_phase": [3.0, 4.0]}
    ) == (1.0, 2.0)
    assert direct_phase_override({}) is None
    assert direct_phase_override({"direct_phase": "bad"}) is None
