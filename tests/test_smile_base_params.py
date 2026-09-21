"""SMILE The source of the base parameter of optimisation is consistent with the template
(0.2.199-patch29hz-Revision 18). user 2026-09-11: "Didn't I say at the beginning that only the
smile parameter should be changed based on the final script? How could I change it to this?" --
The original implementation `_last_spectrum_params` only recognizes `process`/`reconstruct_nus`,
while the normal unified route registration is `phase_optimize_unified` -> base parameter It is
always empty, and SMILE template is rebuilt from scratch (default window/PS(0,0)/POLY auto),
which is not equivalent to running the script at the end, and will report "insufficient memory"
according to the default wide window."""

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
    """The running parameter of the unified route (phase_optimize_unified) should be used as the
    base parameter of SMILE."""
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
    """Strict data_id ownership: Spectrum operations of other data cannot be used as the base
    parameter of this data."""
    manager, exp_id, _data_id = _manager_with_spectrum_run(
        tmp_path, ref="phase_optimize_unified", data_id="d_002"
    )
    ctrl = ProcessingController(manager)

    assert ctrl._last_spectrum_params(exp_id, "d_001") == {}


def test_non_spectrum_run_is_ignored(tmp_path: Path) -> None:
    """Peak picking/Operation of non-spectral steps such as analysis parameter does not
    participate."""
    manager, exp_id, data_id = _manager_with_spectrum_run(tmp_path, ref="pick_peaks")
    ctrl = ProcessingController(manager)

    assert ctrl._last_spectrum_params(exp_id, data_id) == {}


def test_direct_phase_override_accepts_run_key() -> None:
    """Template direct dimension phase: Explicit direct_phase_override takes precedence, otherwise
    the running record direct_phase will be used."""
    from backend.nmrpipe_backend import direct_phase_override

    assert direct_phase_override({"direct_phase_override": [10.0, 1.0]}) == (10.0, 1.0)
    assert direct_phase_override({"direct_phase": [161.822, 37.5]}) == (161.822, 37.5)
    assert direct_phase_override(
        {"direct_phase_override": [1.0, 2.0], "direct_phase": [3.0, 4.0]}
    ) == (1.0, 2.0)
    assert direct_phase_override({}) is None
    assert direct_phase_override({"direct_phase": "bad"}) is None
