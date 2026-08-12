"""脚本快照 GUI 接线测试:步骤运行后补写 WorkflowRun 快照 + 历史展示。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

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
    """步骤成功后把脚本/参数写入对应 WorkflowRun 快照目录。"""
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
    """多数据实验:只快照匹配 data_id 的最近运行。"""
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
    raw = manager.data_dir(exp_id, data_id, "raw")
    raw.mkdir(parents=True, exist_ok=True)
    controller = ProcessingController(manager)
    assert controller._fid_com_script(exp_id, data_id) == {}
    (raw / "fid.com").write_text("bruk2pipe -in ser", encoding="utf-8")
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
    assert "快照:" in dialog.detail_label.text()
    assert "process.com" in dialog.detail_label.text()
    assert dialog.snapshot_button.isEnabled()
    dialog.close()

    # 无快照的运行:按钮禁用,详情标注 (无)
    run2 = manager.start_run(entry.id, workflow_ref="import")
    manager.finish_run(run2.run_id, "success")
    dialog2 = RunHistoryDialog(
        None, list(manager.project.workflow_runs), "demo",
        project_root=manager.root,
    )
    dialog2.table.selectRow(1)
    assert "(无)" in dialog2.detail_label.text()
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
        "PyQt6.QtGui.QDesktopServices.openUrl",
        staticmethod(lambda url: opened.append(url.toString())),
    )
    dialog._open_snapshot()
    assert opened and "snapshot" in opened[0]
    dialog.close()
