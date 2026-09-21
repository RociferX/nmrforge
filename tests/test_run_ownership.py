"""Determination of ownership of running records (0.2.199-patch29hz-repair). Background: patch29hi
Peak selection written in the previous version/There is no analysis run record inputs.data_id,
Pipeline's _last_run_for originally accepted such records, and an old failure in the multi-data
experiment would be counted on **all** data heads; the project tree uses strict matching, and
inconsistencies will be displayed on both sides. User 2026-09-10 Confirmed: Old project products
will be regenerated without compatibility rollback. Now unified ProjectManager.last_run_for_data
Strict judgment + a refs table."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from qtcompat.QtWidgets import QApplication

from core.project import ProjectManager  # noqa: E402
from gui.pipeline_panel import compute_data_step_statuses  # noqa: E402
from gui.pipeline_state import ALL_STEP_RUN_REFS, STEP_RUN_REFS  # noqa: E402
from gui.project_tree import ProjectTreePanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def host(qapp: QApplication):
    """Control host: The entire test is destroyed to avoid remaining top-level controls (Qt crashes
    at the end)."""
    from qtcompat.QtWidgets import QWidget

    widget = QWidget()
    yield widget
    widget.deleteLater()
    QApplication.processEvents()


def _project(tmp_path: Path, name: str):
    manager = ProjectManager.create_project(tmp_path / name, name)
    exp = manager.create_experiment("HSQC")
    return manager, exp, manager.import_data(exp.id, "/fake/1")


def _record(manager, exp_id: str, data_id: str, ref: str, status: str) -> None:
    inputs = {"data_id": data_id} if data_id else {}
    run = manager.start_run(exp_id, workflow_ref=ref, inputs=inputs)
    manager.finish_run(run.run_id, status, message="test")


def test_strict_match_finds_exact_data_id(tmp_path: Path) -> None:
    manager, exp, d1 = _project(tmp_path, "own_exact")
    _record(manager, exp.id, d1.id, "pick_peaks", "failed")
    run = manager.last_run_for_data(exp.id, d1.id, ("pick_peaks",))
    assert run is not None and run.status == "failed"


def test_legacy_run_without_data_id_is_ignored(tmp_path: Path) -> None:
    """Old records with empty data_id are no longer counted in any data header (compatible fallback
    is not preserved)."""
    manager, exp, d1 = _project(tmp_path, "own_legacy")
    d2 = manager.import_data(exp.id, "/fake/2")
    _record(manager, exp.id, "", "pick_peaks", "failed")

    assert manager.last_run_for_data(exp.id, d1.id, ("pick_peaks",)) is None
    for data in (d1, d2):
        statuses = compute_data_step_statuses(manager, exp.id, data.id)
        assert statuses["peaks"] != "FAILED", data.id


def test_failure_marks_only_its_own_data(tmp_path: Path) -> None:
    manager, exp, d1 = _project(tmp_path, "own_split")
    d2 = manager.import_data(exp.id, "/fake/2")
    _record(manager, exp.id, d2.id, "process", "failed")

    assert compute_data_step_statuses(manager, exp.id, d2.id)["spectrum"] == "FAILED"
    assert compute_data_step_statuses(manager, exp.id, d1.id)["spectrum"] != "FAILED"


def test_tree_and_pipeline_agree(
    tmp_path: Path, qapp: QApplication, host
) -> None:
    """The project tree "failure" and Pipeline step FAILED must have the same origin."""
    manager, exp, d1 = _project(tmp_path, "own_agree")
    d2 = manager.import_data(exp.id, "/fake/2")
    _record(manager, exp.id, d2.id, "pick_peaks", "failed")

    tree = ProjectTreePanel(manager, parent=host)
    assert tree._data_last_run_failed(exp.id, d2.id) is True
    assert tree._data_last_run_failed(exp.id, d1.id) is False
    assert compute_data_step_statuses(manager, exp.id, d2.id)["peaks"] == "FAILED"
    assert compute_data_step_statuses(manager, exp.id, d1.id)["peaks"] != "FAILED"


def test_step_refs_table_is_single_source(tmp_path: Path) -> None:
    """There is only one copy of the step ref table and it covers four steps + optional SMILE
    (analysis deleted)."""
    assert set(STEP_RUN_REFS) == {"fid", "spectrum", "smile", "peaks"}
    assert "phase_optimize_unified" in STEP_RUN_REFS["spectrum"]
    assert set(ALL_STEP_RUN_REFS) == {
        ref for refs in STEP_RUN_REFS.values() for ref in refs
    }
    from gui.pipeline_panel import _step_refs

    for step, refs in STEP_RUN_REFS.items():
        assert _step_refs(step) == refs
