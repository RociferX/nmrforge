"""Script snapshot GUI Wiring test: add WorkflowRun snapshot + history display after the step is
run."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtcompat.QtWidgets import QApplication

from core.project import ProjectManager
from gui.dialogs import RunHistoryDialog
from gui.processing import ProcessingController


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _manager_with_data(tmp_path: Path) -> tuple[ProjectManager, str, str]:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    manager.save()
    return manager, entry.id, data.id


def test_snapshot_step_writes_scripts_and_params(tmp_path: Path) -> None:
    """After the step is successful, write the script/parameter into the corresponding WorkflowRun
    snapshot directory."""
    manager, exp_id, data_id = _manager_with_data(tmp_path)
    run = manager.start_run(
        exp_id,
        workflow_ref="convert_to_fid",
        inputs={"data_id": data_id},
        params={"mode": "auto"},
    )
    manager.finish_run(run.run_id, "success", outputs={"fid_path": "/x.fid"})

    controller = ProcessingController(manager)
    snapshot = controller._snapshot_step(
        exp_id,
        data_id,
        ("convert_to_fid",),
        {"fid.com": "#!/bin/csh\nbruk2pipe ...\n"},
    )
    assert snapshot
    assert run.snapshot_dir
    snapshot_dir = manager.root / run.snapshot_dir
    assert (snapshot_dir / "fid.com").is_file()
    assert "bruk2pipe" in (snapshot_dir / "fid.com").read_text(encoding="utf-8")
    params = (snapshot_dir / "params.json").read_text(encoding="utf-8")
    assert '"mode": "auto"' in params


def test_snapshot_step_skips_when_already_snapshotted(tmp_path: Path) -> None:
    manager, exp_id, data_id = _manager_with_data(tmp_path)
    run = manager.start_run(
        exp_id, workflow_ref="process", inputs={"data_id": data_id}
    )
    manager.finish_run(run.run_id, "success")
    controller = ProcessingController(manager)
    first = controller._snapshot_step(
        exp_id, data_id, ("process",), {"process.com": "v1"}
    )
    assert first
    second = controller._snapshot_step(
        exp_id, data_id, ("process",), {"process.com": "v2"}
    )
    assert second == ""
    snapshot_dir = manager.root / run.snapshot_dir
    assert "v1" in (snapshot_dir / "process.com").read_text(encoding="utf-8")


def test_snapshot_step_no_matching_run(tmp_path: Path) -> None:
    manager, exp_id, _data_id = _manager_with_data(tmp_path)
    controller = ProcessingController(manager)
    assert controller._snapshot_step(exp_id, "d_001", ("process",), {}) == ""


def test_snapshot_step_filters_by_data_id(tmp_path: Path) -> None:
    """Multi-sample data experiment type: Snapshot only the most recent run matching data_id."""
    manager, exp_id, _data_id = _manager_with_data(tmp_path)
    manager.import_data(exp_id, "/fake/2")  # d_002
    run2 = manager.start_run(
        exp_id, workflow_ref="process", inputs={"data_id": "d_002"}
    )
    manager.finish_run(run2.run_id, "success")
    run1 = manager.start_run(
        exp_id, workflow_ref="process", inputs={"data_id": "d_001"}
    )
    manager.finish_run(run1.run_id, "success")

    controller = ProcessingController(manager)
    controller._snapshot_step(exp_id, "d_001", ("process",), {"process.com": "x"})
    assert run1.snapshot_dir
    assert not run2.snapshot_dir


def test_fid_com_script_helper(tmp_path: Path) -> None:
    manager, exp_id, data_id = _manager_with_data(tmp_path)
    process = manager.data_dir(exp_id, data_id, "process")
    process.mkdir(parents=True, exist_ok=True)
    controller = ProcessingController(manager)
    assert controller._fid_com_script(exp_id, data_id) == {}
    (process / "fid.com").write_text("bruk2pipe -in ser", encoding="utf-8")
    scripts = controller._fid_com_script(exp_id, data_id)
    assert scripts == {"fid.com": "bruk2pipe -in ser"}


def test_spectrum_scripts_helper(tmp_path: Path) -> None:
    manager, exp_id, data_id = _manager_with_data(tmp_path)
    process = manager.data_dir(exp_id, data_id, "process")
    process.mkdir(parents=True, exist_ok=True)
    (process / "process.com").write_text("nmrPipe -in fid", encoding="utf-8")
    (process / "nus.com").write_text("smile ...", encoding="utf-8")
    (process / "fid.com").write_text("not a spectrum script", encoding="utf-8")
    controller = ProcessingController(manager)
    scripts = controller._spectrum_scripts(exp_id, data_id)
    assert set(scripts) == {"process.com", "nus.com"}


def test_run_history_dialog_shows_snapshot(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    run = manager.start_run(entry.id, workflow_ref="manual_process")
    manager.finish_run(run.run_id, "success", outputs={"spectrum": "x.ft2"})
    manager.snapshot_run(run.run_id, {"process.com": "nmrPipe ..."})

    dialog = RunHistoryDialog(
        None, list(manager.project.workflow_runs), "demo",
        project_root=manager.root,
    )
    dialog.table.selectRow(0)
    assert "Snapshot:" in dialog.detail_label.text()
    assert "process.com" in dialog.detail_label.text()
    assert dialog.snapshot_button.isEnabled()
    dialog.close()

    # Run without snapshot: button disabled, details labeled (none).
    run2 = manager.start_run(entry.id, workflow_ref="import")
    manager.finish_run(run2.run_id, "success")
    dialog2 = RunHistoryDialog(
        None, list(manager.project.workflow_runs), "demo",
        project_root=manager.root,
    )
    dialog2.table.selectRow(1)
    assert "(none)" in dialog2.detail_label.text()
    assert not dialog2.snapshot_button.isEnabled()
    dialog2.close()


def test_run_history_dialog_opens_snapshot(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    run = manager.start_run(entry.id, workflow_ref="manual_fid")
    manager.finish_run(run.run_id, "success")
    manager.snapshot_run(run.run_id, {"fid.com": "csh"})

    dialog = RunHistoryDialog(
        None, list(manager.project.workflow_runs), "demo",
        project_root=manager.root,
    )
    dialog.table.selectRow(0)
    opened: list[str] = []
    monkeypatch.setattr(
        "qtcompat.QtGui.QDesktopServices.openUrl",
        staticmethod(lambda url: opened.append(url.toString())),
    )
    dialog._open_snapshot()
    assert opened and "snapshot" in opened[0]
    dialog.close()


def test_snapshot_matches_unified_route_run(tmp_path: Path) -> None:
    """Fix 24 (Problem 1): The running records of the unified phase route must also be matched by
    the snapshot. It turns out that generate_spectrum is passed in hard-coded (process,
    reconstruct_nus), and the unified route is registered as phase_optimize_unified ->
    snapshot_dir constant space."""
    from core.project.run_refs import STEP_RUN_REFS

    manager, exp_id, data_id = _manager_with_data(tmp_path)
    run = manager.start_run(
        exp_id,
        workflow_ref="phase_optimize_unified",
        inputs={"data_id": data_id},
    )
    manager.finish_run(run.run_id, "success", outputs={"spectrum": "x/ft2"})

    controller = ProcessingController(manager)
    snapshot = controller._snapshot_step(
        exp_id,
        data_id,
        STEP_RUN_REFS["spectrum"],
        {"process.com": "nmrPipe ..."},
    )
    assert snapshot
    assert run.snapshot_dir
    assert (manager.root / run.snapshot_dir / "process.com").is_file()


def test_generate_spectrum_snapshot_uses_shared_ref_table() -> None:
    """The snapshot call point must use a shared table (hard-coding ref is the bug that is fixed
    and prevents rollback)."""
    import inspect

    from gui import processing

    src = inspect.getsource(processing.ProcessingController.generate_spectrum)
    assert 'STEP_RUN_REFS["spectrum"]' in src
    assert '("process", "reconstruct_nus")' not in src
