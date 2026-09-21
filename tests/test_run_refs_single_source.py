"""Run ref single source and batch reference parameter match exactly (0.2.199-patch29hz-revision
24)."""

from __future__ import annotations

from pathlib import Path

from core.project import ProjectManager


def _project(tmp_path: Path, name: str):
    manager = ProjectManager.create_project(tmp_path / name, name)
    exp = manager.create_experiment("HSQC")
    return manager, exp, manager.import_data(exp.id, "/fake/1")


def _record(manager, exp_id, data_id, ref, params=None, status="success") -> None:
    inputs = {"data_id": data_id} if data_id else {}
    run = manager.start_run(
        exp_id, workflow_ref=ref, inputs=inputs, params=dict(params or {})
    )
    manager.finish_run(run.run_id, status, message="test")


def test_step_run_refs_lives_in_core() -> None:
    """The table itself is in the core (also used in the workflow layer), and gui.pipeline_state is
    only re-exported."""
    import core.project.run_refs as run_refs
    import gui.pipeline_state as pipeline_state

    assert pipeline_state.STEP_RUN_REFS is run_refs.STEP_RUN_REFS
    assert pipeline_state.ALL_STEP_RUN_REFS is run_refs.ALL_STEP_RUN_REFS
    assert set(run_refs.MANUAL_SPECTRUM_RUN_REFS) <= set(
        run_refs.STEP_RUN_REFS["spectrum"]
    )
    assert "phase_optimize_unified" in run_refs.STEP_RUN_REFS["spectrum"]


def test_batch_reference_params_exact_match(tmp_path: Path) -> None:
    """Batch reference parameter: exact ref + strict data_id (substring with the same name ref/No
    records of attribution are counted.)."""
    from workflow.batch import _reference_spectrum_params

    manager, exp, d1 = _project(tmp_path, "batch_ref")
    d2 = manager.import_data(exp.id, "/fake/2")
    _record(manager, exp.id, d1.id, "process", params={"nthread": 1})
    # Other refs with names containing process are even later: the old implementation uses substring
    # matching and will be mistakenly run as spectrum.
    _record(manager, exp.id, d1.id, "process_preview", params={"nthread": 99})
    # The spectrum ref of the artificial path is also considered a reference (same origin as
    # STEP_RUN_REFS).
    _record(manager, exp.id, d1.id, "manual_process", params={"nthread": 6})
    # Other data + non-spectrum chain ref(smile_optimize) does not participate.
    _record(manager, exp.id, d2.id, "smile_optimize", params={"nthread": 8})
    # No old records of data_id: no data will be involved under strict ownership.
    _record(manager, exp.id, "", "process", params={"nthread": 7})

    assert _reference_spectrum_params(manager, exp.id, d1.id) == {"nthread": 6}
    assert _reference_spectrum_params(manager, exp.id, d2.id) == {}
