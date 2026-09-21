"""Batch processing test: data group import, entire group running within the group (new engine),
tree display."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtcompat.QtWidgets import QApplication

from core.project import ProjectManager
from gui.pipeline_panel import PipelinePanel
from gui.processing import ProcessingController


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _manager_with_experiment(tmp_path: Path) -> tuple[ProjectManager, str]:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    manager.import_data(entry.id, "/fake/1")
    manager.save()
    return manager, entry.id


def test_batch_group_is_data_group(tmp_path: Path) -> None:
    """0.2.164-patch1: The batch group is the data group (schema 1.4, the only source)."""
    manager, exp_id = _manager_with_experiment(tmp_path)
    manager.import_data(exp_id, "/fake/2")
    manager.save()
    data_ids = [d.id for d in manager.project.experiment(exp_id).data]
    group = manager.create_data_group(exp_id, data_ids=data_ids)
    assert manager.group_data_ids(exp_id, group.id) == data_ids
    assert manager.group_of_data(exp_id, data_ids[0]) is not None
    # Move out of group: restore single data.
    manager.remove_from_group(exp_id, group.id, data_ids[1])
    assert manager.group_data_ids(exp_id, group.id) == [data_ids[0]]
    assert manager.group_of_data(exp_id, data_ids[1]) is None



def test_batch_import_marks_group(tmp_path: Path, bruker_dir: Path) -> None:
    """Batch import: Multiple directories import the same experiment type and are classified into
    the same data group. The group number will be incremented for multiple imports."""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("batch")
    manager.save()
    folders = [
        str(bruker_dir / "hsqc_2d"),
        str(bruker_dir / "nus_2d"),
    ]
    # 0.2.199-patch29gl: Batch import verification original data file; supplement ser for temporary
    # backup for testing.
    (bruker_dir / "hsqc_2d" / "ser").write_bytes(b"")
    (bruker_dir / "nus_2d" / "ser").write_bytes(b"")
    (bruker_dir / "hsqc_small" / "ser").write_bytes(b"")
    controller = ProcessingController(manager)
    result = controller.batch_import(entry.id, folders)
    assert result["batch_id"] == "G1"
    assert all(item["ok"] for item in result["results"])
    data_ids = [item["data_id"] for item in result["results"]]
    assert len(data_ids) == 2
    assert manager.group_data_ids(entry.id, "G1") == data_ids
    # 0.2.163: Data group synchronization falls into project.json(schema 1.4).
    group = manager.group(entry.id, "G1")
    assert group is not None and group.data_ids == data_ids
    # Batch import again -> G2.
    result2 = controller.batch_import(
        entry.id, [str(bruker_dir / "hsqc_small")]
    )
    assert result2["batch_id"] == "G2"
    assert [g.id for g in manager.data_groups(entry.id)] == ["G1", "G2"]


class _FakeController:
    """Log each data_id step call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def set_manager(self, manager) -> None:
        pass

    def generate_spectrum(self, data, exp_id=None, data_id=None) -> str:
        self.calls.append(data_id or "")
        return "/tmp/x.ft2"


class _ProgressController(_FakeController):
    """Fake controller with progress callback (log into panel during verification phase)."""

    def generate_spectrum(
        self, data, exp_id=None, data_id=None, progress=None
    ) -> str:
        self.calls.append(data_id or "")
        if progress:
            progress("NUS Data: Start SMILE reconstruction (including direct dimension phase)")
        return "/tmp/x.ft2"


class _GroupFakeController(_FakeController):
    """New engine entry fake implementation: record run_group_batch delegate call."""

    def __init__(self) -> None:
        super().__init__()
        self.group_calls: list[tuple] = []

    def run_group_batch(
        self,
        exp_id,
        group_id,
        steps,
        reference_data_id="",
        progress=None,
        params=None,
    ) -> dict:
        self.group_calls.append((group_id, list(steps), dict(params or {})))
        if progress:
            progress(f"{group_id}: Finish")
        ids = list(self.member_ids)
        return {
            "batch_id": group_id,
            "data_ids": ids,
            "steps": list(steps),
            "results": {
                d: {"data_id": d, "status": "success", "steps": {}, "error": ""}
                for d in ids
            },
            "failed": [],
            "summary": {"total": len(ids), "success": len(ids), "failed": 0},
        }


class _SyncThread:
    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


def test_pipeline_group_run_applies_to_all(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Intermediate processing page operations: The entire data group is executed uniformly and
    entrusted to the new engine run_group_batch."""
    monkeypatch.setattr("threading.Thread", _SyncThread)
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("batch")
    data1 = manager.import_data(entry.id, "/fake/1")
    data2 = manager.import_data(entry.id, "/fake/2")
    group = manager.create_data_group(entry.id, data_ids=[data1.id, data2.id])
    manager.save()
    # 0.2.163-patch14: Do not run the next step if the pre-processing is not completed -- Let the
    # two sets of fids be ready first.
    from gui.pipeline_state import record_step_success

    for data in (data1, data2):
        fid = manager.data_dir(entry.id, data.id, "process") / f"{data.id}.fid"
        fid.parent.mkdir(parents=True, exist_ok=True)
        fid.write_bytes(b"fid")
        manager.set_data_fid(entry.id, data.id, fid)
        record_step_success(manager, entry.id, data.id, "fid")
    manager.save()
    controller = _GroupFakeController()
    controller.member_ids = [data1.id, data2.id]
    panel = PipelinePanel(manager, controller)
    panel.set_selection("data", entry.id, data1.id)
    assert f"Group {group.id}" in panel.context_label.text()
    # 0.2.199-patch29gv: Single data in the group runs independently in the panel (not automatically
    # converted to the entire group).
    panel._on_run_requested("spectrum")
    assert controller.group_calls == []
    assert controller.calls == [data1.id]
    panel.close()


class _TempWorkspace:
    """Workspace stub pointing to a temporary directory (used to list projects in the tree
    panel)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def list_projects(self):
        return sorted(
            p
            for p in self.root.iterdir()
            if p.is_dir() and (p / "project.json").is_file()
        )

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root


def test_tree_data_label_shows_no_batch_suffix(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.164-patch1: The batch group is the data group; the ungrouped data labels no longer have
    the [batch] suffix."""
    from gui.project_tree import ProjectTreePanel

    ws = tmp_path / "ws"
    ws.mkdir()
    manager = ProjectManager.create_project(ws / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    manager.import_data(entry.id, "/fake/1")
    manager.save()
    panel = ProjectTreePanel(manager, workspace=_TempWorkspace(ws))
    data_item = panel.tree.topLevelItem(0).child(0).child(0).child(0)
    assert "[" not in data_item.text(0)
    panel.close()


def test_pipeline_status_shows_selected_data(
    tmp_path: Path, qapp: QApplication
) -> None:
    """After importing new data, the intermediate state is based on the currently selected data
    instead of the first data."""
    from gui.pipeline_panel import compute_data_step_statuses

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("multi")
    data1 = manager.import_data(entry.id, "/fake/1")
    data2 = manager.import_data(entry.id, "/fake/2")
    # Data1 has been processed (fid+ft2), data2 is blank.
    process = manager.data_dir(entry.id, data1.id, "process")
    process.mkdir(parents=True, exist_ok=True)
    fid = process / f"{entry.id}-{data1.id}.fid"
    fid.write_bytes(b"fid")
    manager.set_data_fid(entry.id, data1.id, fid)
    spectra = manager.data_dir(entry.id, data1.id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    ft2 = spectra / f"{entry.id}-{data1.id}.ft2"
    ft2.write_bytes(b"ft2")
    manager.set_data_spectrum(entry.id, data1.id, ft2)
    manager.save()

    st1 = compute_data_step_statuses(manager, entry.id, data1.id)
    assert st1["fid"] == "SUCCESS" and st1["spectrum"] == "SUCCESS"
    st2 = compute_data_step_statuses(manager, entry.id, data2.id)
    assert st2["fid"] == "READY" and st2["spectrum"] == "LOCKED"
    # When data2 is selected in the panel, its status is displayed (not the completed status of
    # data1).
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", entry.id, data2.id)
    assert panel._rows["fid"].status_label.text().startswith("▶")
    assert panel._rows["spectrum"].status_label.text().startswith("🔒")
    panel.close()


def test_batch_subfolder_scan(tmp_path: Path) -> None:
    """Automatically check Bruker datasets in sub-file folders when adding the total file folder in
    batches."""
    from gui.dashboards import ExperimentDashboard

    root = tmp_path / "batch_root"
    (root / "hsqc").mkdir(parents=True)
    (root / "hsqc" / "acqus").write_text("x")
    (root / "hsqc" / "acqu2s").write_text("x")  # 2D Logo.
    (root / "nested" / "hnca").mkdir(parents=True)
    (root / "nested" / "hnca" / "acqus").write_text("x")
    (root / "nested" / "hnca" / "acqu2s").write_text("x")  # 2D Logo.
    (root / "nested" / "canh").mkdir(parents=True)
    (root / "nested" / "canh" / "acqus").write_text("x")
    (root / "nested" / "canh" / "acqu3s").write_text("x")  # 3D Logo.
    (root / "notes.txt").write_text("not a dataset")
    found = ExperimentDashboard._bruker_datasets_under(root)
    names = {p.name for p in found}
    # 0.2.199-patch29hd: Batch only supports 2D -- 3D (canh) is filtered, only 2D hsqc/hnca.
    assert names == {"hsqc", "hnca"}
    # Directly select the dataset directory -> return to itself.
    direct = ExperimentDashboard._bruker_datasets_under(root / "hsqc")
    assert [p.name for p in direct] == ["hsqc"]


def test_experiment_dashboard_single_batch_groups(
    qapp: QApplication,
) -> None:
    """Experiment type page: Single import and batch processing group display (visual
    distinction)."""
    from qtcompat.QtWidgets import QGroupBox

    from gui.dashboards import ExperimentDashboard

    page = ExperimentDashboard()
    assert isinstance(page.single_group, QGroupBox)
    assert isinstance(page.batch_group, QGroupBox)
    assert page.single_group.title() == "single import"
    assert page.batch_group.title() == "Batch processing"
    page.close()


def test_experiment_dashboard_segmented_between_single_and_batch(
    qapp: QApplication,
) -> None:
    """Experiment type page: The segmented acquisition import (merge FID) entry is located between
    single import and batch processing."""
    from qtcompat.QtWidgets import QGroupBox

    from gui.dashboards import ExperimentDashboard

    page = ExperimentDashboard()
    assert isinstance(page.segmented_group, QGroupBox)
    assert page.segmented_group.title() == "segmented data or repeated experiment overlay import"
    # 0.2.162-patch11: The import block is within import_panel (the experiment type page is no
    # longer displayed inline).
    layout = page.import_panel.layout()
    assert layout.indexOf(page.single_group) < layout.indexOf(page.segmented_group)
    assert layout.indexOf(page.segmented_group) < layout.indexOf(page.batch_group)
    emitted: list[tuple[str, str]] = []
    page.segmented_import_requested.connect(
        lambda exp_id, source: emitted.append((exp_id, source))
    )
    page.segmented_source_edit.setText("/data/container")
    page.import_panel._exp_id = "exp_003"
    page.import_panel._on_segmented_import()
    assert emitted == [("exp_003", "/data/container")]
    page.close()


def test_project_single_click_opens(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Click the project node to open it (no need to double-click); the current project will not be
    opened repeatedly."""
    from gui.project_tree import ProjectTreePanel

    ws = tmp_path / "ws"
    ws.mkdir()
    ProjectManager.create_project(ws / "projA", "projA")
    manager = ProjectManager.create_project(ws / "projB", "projB")
    manager.create_experiment()
    manager.save()
    panel = ProjectTreePanel(manager, workspace=_TempWorkspace(ws))
    opened: list[str] = []
    panel.open_project_requested.connect(lambda p: opened.append(p))
    workspace_item = panel.tree.topLevelItem(0)

    def project_item(name: str):
        for index in range(workspace_item.childCount()):
            child = workspace_item.child(index)
            if child.text(0) == name:
                return child
        return None

    proj_a = project_item("projA")
    proj_b = project_item("projB")
    assert proj_a is not None and proj_b is not None
    panel._on_item_clicked(proj_a, 0)  # Non-current project -> Open.
    assert opened and Path(opened[0]).name == "projA"
    panel._on_item_clicked(proj_b, 0)  # Current project -> Do not open repeatedly.
    assert len(opened) == 1
    panel.close()


def test_pipeline_progress_logs_to_panel(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When running to generate a spectrum, the stage progress enters the log panel through
    progress."""
    from gui.log_panel import LogPanel

    monkeypatch.setattr("threading.Thread", _SyncThread)
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("prog")
    data = manager.import_data(entry.id, "/fake/1")
    manager.save()
    # 0.2.163-patch14: The next step will not be run if the prefix is not completed -- Let fid be
    # ready first.
    from gui.pipeline_state import record_step_success

    fid = manager.data_dir(entry.id, data.id, "process") / f"{data.id}.fid"
    fid.parent.mkdir(parents=True, exist_ok=True)
    fid.write_bytes(b"fid")
    manager.set_data_fid(entry.id, data.id, fid)
    record_step_success(manager, entry.id, data.id, "fid")
    manager.save()
    controller = _ProgressController()
    panel = PipelinePanel(manager, controller)
    log = LogPanel()
    panel.log_message.connect(log.append)
    panel.log_scoped.connect(log.append)  # 0.2.199-Patch29d: run log by scope.
    panel.set_selection("data", entry.id, data.id)
    log.set_scope("data", entry.id, data.id)
    panel._on_run_requested("spectrum")
    assert "Start SMILE reconstruction" in log.text.toPlainText()
    panel.close()
    log.close()


def test_welcome_single_click_opens(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Welcome page recent items click to open."""
    from core.workspace import WorkspaceManager
    from gui.welcome_page import WelcomePage

    ws = tmp_path / "ws"
    ws.mkdir()
    ProjectManager.create_project(ws / "projA", "projA")
    page = WelcomePage(workspace=WorkspaceManager(ws))
    opened: list[str] = []
    page.open_project_requested.connect(lambda p: opened.append(p))
    item = page.recent_list.item(0)
    page._on_recent_clicked(item)
    assert opened and Path(opened[0]).name == "projA"
    page.close()


def test_experiment_dashboard_copy_check_above_groups(
    qapp: QApplication,
) -> None:
    """0.2.112: The "Link raw data to project" checkbox is located above the three import groups."""
    from gui.dashboards import ExperimentDashboard

    page = ExperimentDashboard()
    layout = page.import_panel.layout()
    assert layout.indexOf(page.copy_check) < layout.indexOf(page.single_group)
    assert layout.indexOf(page.single_group) < layout.indexOf(page.segmented_group)
    assert layout.indexOf(page.segmented_group) < layout.indexOf(page.batch_group)
    assert page.copy_check.isChecked()
    page.close()


def test_experiment_dashboard_clear_import_form(qapp: QApplication) -> None:
    """0.2.112: The import form provides cleaning (name/path) to facilitate continuous import."""
    from gui.dashboards import ExperimentDashboard

    page = ExperimentDashboard()
    page.name_edit.setText("sample 1")
    page.source_edit.setText("/data/a")
    page.segmented_source_edit.setText("/data/container")
    page.clear_import_form()
    assert page.name_edit.text() == ""
    assert page.source_edit.text() == ""
    assert page.segmented_source_edit.text() == ""
    page.close()


def test_import_data_dropdown_panel(qapp: QApplication) -> None:
    """0.2.162-patch11: The "Import data" drop-down contains the complete import panel and forwards
    the signal."""
    from gui.dashboards import ImportDataDropdown

    dd = ImportDataDropdown()
    assert dd.panel.single_group.title() == "single import"
    assert dd.panel.segmented_group.title() == (
        "segmented data or repeated experiment overlay import"
    )
    assert dd.panel.batch_group.title() == "Batch processing"
    emitted: list[tuple] = []
    dd.import_options_requested.connect(lambda *a: emitted.append(a))
    dd.panel.set_context("exp_1")
    dd.panel.source_edit.setText("/data/a")
    dd.panel._on_import()
    assert emitted and emitted[0][0] == "exp_1"
    dd.close()


def test_batch_import_no_group(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.162-patch12: Batch import is not grouped = multiple single imports (no data group is
    created)."""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("batch")
    manager.save()
    folders = [
        str(bruker_dir / "hsqc_2d"),
        str(bruker_dir / "nus_2d"),
    ]
    # 0.2.199-patch29gl: Batch import verification original data file; supplement ser for temporary
    # backup for testing.
    (bruker_dir / "hsqc_2d" / "ser").write_bytes(b"")
    (bruker_dir / "nus_2d" / "ser").write_bytes(b"")
    (bruker_dir / "hsqc_small" / "ser").write_bytes(b"")
    controller = ProcessingController(manager)
    result = controller.batch_import(entry.id, folders, group=False)
    assert result["batch_id"] == ""
    data_ids = [item["data_id"] for item in result["results"]]
    assert len(data_ids) == 2
    for data_id in data_ids:
        assert manager.group_of_data(entry.id, data_id) is None
    assert manager.data_groups(entry.id) == []


def test_batch_import_group_option(qapp: QApplication) -> None:
    """0.2.162-patch12: The batch import panel has a group option, and the signal carries group."""
    from gui.dashboards import ExperimentImportPanel

    panel = ExperimentImportPanel()
    assert panel.batch_group_check.isChecked()  # Default group.
    emitted: list[tuple] = []
    panel.batch_import_requested.connect(lambda *a: emitted.append(a))
    panel.set_context("exp_1")
    panel.batch_list.addItem("/data/a")
    panel._on_batch_import()
    assert emitted and emitted[0][2] is True
    panel.batch_group_check.setChecked(False)
    # 0.2.199-patch29gn: The list is cleared after importing and added again to test the lack of
    # grouping.
    panel.batch_list.addItem("/data/b")
    panel._on_batch_import()
    assert emitted[-1][2] is False
    panel.close()


def test_import_button_no_source_gives_hint(qapp: QApplication) -> None:
    """When the import button has no path, a prompt will be given instead of being silent and
    unresponsive (user feedback)."""
    from gui.dashboards import ExperimentImportPanel

    hints: list[str] = []
    from gui.dashboards import InfoDialog

    orig_show = InfoDialog.show_info
    InfoDialog.show_info = staticmethod(
        lambda parent, title, text: hints.append(f"{title}: {text}")
    )
    try:
        panel = ExperimentImportPanel()
        emitted: list[tuple] = []
        panel.import_options_requested.connect(lambda *a: emitted.append(a))
        panel.set_context("exp_1")
        panel._on_import()
        assert emitted == [], "No import request should be made if there is no path"
        assert hints and "acqus) first" in hints[-1]
        # With path + empty exp_id: Send request as usual (the main window automatically creates
        # experiment type).
        hints.clear()
        panel.set_context("")
        panel.source_edit.setText("/data/foo")
        panel._on_import()
        assert emitted and emitted[0][0] == ""
    finally:
        InfoDialog.show_info = orig_show
        panel.close()


def test_experiment_dashboard_rename_data_name(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.162-patch13: Editing of the "Name" column of the data table triggers renaming and
    placement."""
    from gui.main_window import MainWindow

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    manager.import_data(entry.id, "/fake/1")
    manager.save()
    window = MainWindow(manager=manager)
    page = window.center_panel.experiment_page
    page.set_context(manager, entry.id, "Label")
    assert page.data_table.rowCount() >= 1
    item = page.data_table.item(0, 1)
    item.setText("new name")  # itemChanged → data_rename_requested → _rename_data
    entry2 = manager.project.experiment(entry.id)
    assert entry2 is not None and entry2.data[0].title == "new name"
    window.close()
