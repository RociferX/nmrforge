"""Three-column layout GUI Test: Project tree (Project -> Experiment -> Data)/Five-step
Pipeline/spectrum panel (offscreen)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from qtcompat.QtWidgets import QApplication, QDialog, QLabel, QMenu

from core.project import ProjectManager
from gui.log_panel import LogPanel
from gui.main_window import MainWindow
from gui.pipeline_panel import (
    PIPELINE_STEPS,
    PipelinePanel,
    compute_step_statuses,
)
from gui.project_tree import ProjectTreePanel
from gui.spectrum_panel import SpectrumPanel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


class SyncThread:
    """Turn the background thread into synchronous execution, and the test does not depend on
    thread timing."""

    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


def _manager_with_experiment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch | None = None
) -> ProjectManager:
    """Create a project in the temporary workspace, and let the tree/The main window uses this
    workspace (test isolation)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    manager = ProjectManager.create_project(workspace / "proj", "demo")
    for source, title in (("/sampleD", "HSQC"), ("/sampleE", "HNCACB")):
        entry = manager.add_experiment(source, title=title)
        entry.status = "registered"
    manager.save()
    if monkeypatch is not None:
        monkeypatch.setattr(
            "gui.main_window.WorkspaceManager",
            lambda: _TempWorkspace(workspace),
        )
        monkeypatch.setattr(
            "core.workspace.WorkspaceManager",
            lambda *a, **k: _TempWorkspace(workspace),
        )
    return manager


class _TempWorkspace:
    """Workspace stub pointing to temporary directory."""

    def __init__(self, root) -> None:
        self.root = Path(root)

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root
    def delete_project(self, name: str, trash: bool = True) -> None:
        target = self.root / name
        if target.exists():
            import shutil

            shutil.rmtree(target)

    def rename_project(self, old_name: str, new_name: str) -> Path:
        target = self.root / old_name
        new_target = self.root / new_name
        if target.exists():
            target.rename(new_target)
        return new_target

    def list_projects(self) -> list[Path]:
        return sorted(
            p
            for p in self.root.iterdir()
            if p.is_dir() and (p / "project.json").is_file()
        )

    def create_project(self, name: str, **kwargs):
        from core.project import ProjectManager

        return ProjectManager.create_project(self.root / name, name, **kwargs)


def _write_ft2(path: Path) -> None:
    from nmrglue.fileio import pipe

    shape = (64, 128)
    data = np.zeros(shape, dtype=np.float32)
    data[32, 64] = 100.0
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = shape[1]
    dic["FDSPECNUM"] = shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDF1T"] = shape[0]
    dic["FDF1SW"] = 6000.0
    dic["FDF1OBS"] = 600.0
    dic["FDF1CAR"] = 118.0
    dic["FDF1ORIG"] = 118.0 * 600.0
    dic["FDF2T"] = shape[1]
    dic["FDF2SW"] = 6000.0
    dic["FDF2OBS"] = 600.0
    dic["FDF2CAR"] = 4.7
    dic["FDF2ORIG"] = 4.7 * 600.0
    pipe.write(str(path), dic, data, overwrite=True)


class FakeProcessingController:
    """Five-step process fake controller: generate_fid/generate_spectrum records the call and
    completes it synchronously."""

    def set_manager(self, manager) -> None:
        self.manager = manager

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.last_entry = None

    def import_data(self, entry, source) -> dict:
        self.calls.append("import_data")
        self.last_entry = entry
        return {"experiment_id": entry.id, "status": "imported"}

    def generate_fid(self, data, exp_id=None, data_id=None) -> str:
        self.calls.append("generate_fid")
        self.last_entry = data
        return "/tmp/x.fid"

    def generate_spectrum(self, data, exp_id=None, data_id=None) -> str:
        self.calls.append("generate_spectrum")
        self.last_entry = data
        return "/tmp/x.ft2"


# ----------------------------------------------------------------------
# Project tree (Project -> Experiment -> Data).
# ----------------------------------------------------------------------
def test_project_tree_structure(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    assert panel.tree.topLevelItemCount() == 1
    workspace_item = panel.tree.topLevelItem(0)
    assert workspace_item.text(0) == "NMRForgeWorkspace"  # Workspace Root node.
    project_item = workspace_item.child(0)
    assert project_item.text(0) == "demo"  # The current project displays project.name.
    assert project_item.text(1) == "current"  # Current project tag.
    assert project_item.childCount() == 2
    exp_item = project_item.child(0)
    assert exp_item.text(0) == "HSQC"
    assert exp_item.childCount() == 1
    data_item = exp_item.child(0)
    assert data_item.text(0) == "Data d_001"
    assert data_item.text(1) == "Already imported"
    assert data_item.childCount() == 6  # raw/process/spectra/peaks/figures/report
    panel.close()


def test_project_tree_data_status_shows_running(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch5: The running data displays "Running", and the inference state is restored
    after the end."""
    from gui.project_tree import ProjectTreePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)

    def _data_item():
        return panel.tree.topLevelItem(0).child(0).child(0).child(0)

    assert _data_item().text(1) == "Already imported"
    panel.mark_running("exp_001", "d_001")
    assert _data_item().text(1) == "Running"
    panel.clear_running("exp_001", "d_001")
    assert _data_item().text(1) == "Already imported"
    panel.close()


def test_project_tree_current_experiment_from_data(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    panel.select_experiment("exp_002")
    assert panel.current_experiment_id() == "exp_002"
    # The selected sample data node is still normalized to the experiment type it belongs to.
    exp_item = panel.tree.topLevelItem(0).child(0).child(1)
    panel.tree.setCurrentItem(exp_item.child(0))
    assert panel.current_experiment_id() == "exp_002"
    assert panel._data_id_of(panel.tree.currentItem()) == "d_001"
    panel.close()


def test_project_tree_column_widths_readable(qapp: QApplication) -> None:
    panel = ProjectTreePanel()
    assert panel.tree.columnWidth(0) >= 180  # Minimum readable width of object column.
    assert panel.tree.columnWidth(1) >= 70  # Status column.
    panel.close()


# ----------------------------------------------------------------------
# Pipeline five-step status.
# ----------------------------------------------------------------------
def test_pipeline_spectrum_row_ext_range_button_before_run(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.162-patch15: There is a "direct dimension range" button before the run button of the
    generated spectrum row (other steps are hidden)."""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    spectrum_row = panel._rows["spectrum"]
    assert not spectrum_row.ext_range_button.isHidden()
    assert panel._rows["fid"].ext_range_button.isHidden()
    assert panel._rows["peaks"].ext_range_button.isHidden()
    # 0.2.163-patch5: The button is moved to a separate line below the title (ext_range before run).
    button_row = spectrum_row.layout().itemAt(1)
    assert button_row is not None and hasattr(button_row, "count")
    widgets = [button_row.itemAt(i).widget() for i in range(button_row.count())]
    assert widgets.index(spectrum_row.ext_range_button) < widgets.index(
        spectrum_row.run_button
    )
    panel.close()


def test_pipeline_run_guard_blocks_repeat(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch12: Clicking again during operation will be rejected and will not be started
    again."""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    messages: list[str] = []
    panel.log_message.connect(messages.append)
    panel._run_active = True
    panel._on_run_requested("spectrum")
    assert any("There is already a task running" in m for m in messages)
    panel._run_active = False
    panel.close()


def test_spectrum_report_cache_by_fingerprint(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch12: Generate spectrum parameter report by spectrum file fingerprint cache
    multiplexing."""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    missing = str(tmp_path / "no.ft3")
    text1 = panel._cached_spectrum_report(
        {"diagnostics": {"reports": []}}, missing
    )
    text2 = panel._cached_spectrum_report(
        {"diagnostics": {"reports": []}}, missing
    )
    assert text1 == text2
    # 0.2.199-patch29e: No records are generated on-site (prompt to rerun), so they are not written
    # to the cache.
    assert "No report record" in text1
    assert not panel._spectrum_report_cache
    panel.close()


def test_pipeline_button_row_wraps_when_narrow(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch4: The step row button area has a fluid layout, and the buttons automatically
    wrap when the width is insufficient."""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    row = panel._rows["spectrum"]
    buttons = [
        row.ext_range_button,
        row.run_button,
        row.rerun_final_button,
        row.show_spectrum_button,
        row.manual_button,
    ]
    for button in buttons:
        # 5 visible buttons after the simulation spectrum step is completed.
        button.setVisible(True)
    row.show()
    qapp.processEvents()
    button_row = row.layout().itemAt(1)

    def _row_ys() -> set[int]:
        return {
            button_row.itemAt(i).widget().y()
            for i in range(button_row.count())
            if button_row.itemAt(i).widget() is not None
        }

    # 5 buttons must be folded into multiple lines under narrow width (y coordinate at least two
    # lines).
    row.setFixedWidth(180)
    qapp.processEvents()
    ys = _row_ys()
    assert len(ys) >= 2, f"Buttons are not wrapped in narrow width: y={sorted(ys)}"
    panel.close()


def test_pipeline_final_ext_override_params(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.162-patch15: Direct dimension range coverage of each data final run -> generate_spectrum
    params."""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    assert panel._spectrum_ext_params("d_001") is None
    panel._final_ext[("exp_001", "d_001")] = ("11.0", "5.5", True)
    assert panel._spectrum_ext_params("d_001") == {
        "apply_ext_to_opt": "1",
        "final_ext_lo": "11.0",
        "final_ext_hi": "5.5",
    }
    # Set only one end: do not inject the other end; turn off "Apply this range to the optimisation
    # process".
    panel._final_ext[("exp_001", "d_001")] = ("11.0", "", False)
    assert panel._spectrum_ext_params("d_001") == {
        "apply_ext_to_opt": "0",
        "final_ext_lo": "11.0",
    }
    panel.close()


def test_pipeline_ext_button_text_reflects_override(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.162-patch15: The button text is refreshed with the final running range of the current
    data."""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    assert panel._rows["spectrum"].ext_range_button.text() == "direct dimension range"
    panel._final_ext[("exp_001", "d_001")] = ("11.0", "5.5", True)
    panel.refresh()
    assert (
        panel._rows["spectrum"].ext_range_button.text()
        == "direct dimension range 11.0/5.5 · including optimisation"
    )
    panel.close()
    assert (
        panel._rows["spectrum"].ext_range_button.toolTip()
        == "direct-dimension window: 11.0-5.5 ppm (EXT -x1/-xn, including optimisation)\n"
           "first-pass reconstruction / phase search and the optimisation evaluation share the "
           "window; peaks outside it do not enter the final spectrum,\n"
           "p1 is renormalised to the window width; each data set shows its own settings"
    )
    # Switch data: When the data window is not set, the prompt word returns to the default
    # description.
    panel.set_selection("data", "exp_001", "d_002")
    assert "Use default if not set" in panel._rows["spectrum"].ext_range_button.toolTip()
    panel.close()



def test_pipeline_steps_include_optional_smile() -> None:
    ids = [step[0] for step in PIPELINE_STEPS]
    assert ids == [
        "fid", "spectrum", "smile", "peaks"
    ]
    deps = {step[0]: step[3] for step in PIPELINE_STEPS}
    # SMILE optimisation is optional: peak picking does not depend on it.
    assert "smile" not in deps["peaks"]
    assert deps["smile"] == ("spectrum",)
    for _, _, _, step_deps in PIPELINE_STEPS:
        for dep in step_deps:
            assert dep in ids


def test_pipeline_status_registered(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["fid"] == "READY"
    for step_id in ("spectrum", "peaks"):
        assert statuses[step_id] == "LOCKED"


def test_pipeline_status_after_spectrum(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    # Both fid and spectrum require real products: fid is registered in the process directory,
    # fid_path.
    process_dir = manager.data_dir("exp_001", "d_001", "process")
    process_dir.mkdir(parents=True, exist_ok=True)
    fid_file = process_dir / "exp_001-d_001.fid"
    fid_file.write_bytes(b"fid")
    manager.set_data_fid("exp_001", "d_001", fid_file)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / "d_001.ft2")
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["fid"] == "SUCCESS"
    assert statuses["spectrum"] == "SUCCESS"
    assert statuses["peaks"] == "READY"


def test_pipeline_fid_unlocks_spectrum(
    tmp_path: Path, qapp: QApplication
) -> None:
    """After generating FID, the spectrum generation step should be unlocked as READY (regression:
    spectrum was used to determine fid incorrectly)."""
    manager = _manager_with_experiment(tmp_path)
    process_dir = manager.data_dir("exp_001", "d_001", "process")
    process_dir.mkdir(parents=True, exist_ok=True)
    fid_file = process_dir / "exp_001-d_001.fid"
    fid_file.write_bytes(b"fid")
    manager.set_data_fid("exp_001", "d_001", fid_file)
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["fid"] == "SUCCESS"
    assert statuses["spectrum"] == "READY"  # Key: spectrum step unlock.
    assert statuses["peaks"] == "LOCKED"


def test_pipeline_panel_refresh_shows_next_step(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    assert "Next step" in panel.next_label.text()
    assert "Generate FID" in panel.next_label.text()
    assert not panel._rows["fid"].run_button.isHidden()
    assert panel._rows["spectrum"].run_button.isHidden()
    # 0.2.199-patch29dm: Generate FID artificial buttons that must be processed automatically
    # (SUCCESS) before they appear.
    assert panel._rows["fid"].manual_button.isHidden()
    panel.close()


def test_pipeline_panel_run_generate_fid(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager = _manager_with_experiment(tmp_path)
    controller = FakeProcessingController()
    panel = PipelinePanel(manager, controller)
    log = LogPanel()
    panel.log_message.connect(log.append)
    panel.log_scoped.connect(log.append)  # 0.2.199-Patch29d: run log by scope.
    panel.set_selection("data", "exp_001", "d_001")
    # 0.2.199-patch29d: log is implemented according to the data scope, and the panel is displayed
    # only after switching to the data scope.
    log.set_scope("data", "exp_001", "d_001")
    panel._on_run_requested("fid")
    assert controller.calls == ["generate_fid"]
    assert "Finish Generate FID" in log.text.toPlainText()
    # After the operation is completed, re-infer according to the product file (when there is no
    # ft2, fid returns to READY).
    assert panel._rows["fid"].status_label.text().startswith("▶")
    panel.close()
    log.close()


# ----------------------------------------------------------------------
# Spectrum panel.
# ----------------------------------------------------------------------
def test_spectrum_panel_lists_and_loads_spectrum(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = _manager_with_experiment(tmp_path)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    spectrum = spectra / "hsqc_2d.ft2"   # Backends are named by dataset_id.
    _write_ft2(spectrum)
    panel = SpectrumPanel(manager)
    # Experimental level: summarize all data spectrum under the experiment.
    panel.set_context("exp_001")
    assert panel.file_list.count() == 1
    assert panel.file_list.item(0).text() == "hsqc_2d.ft2"
    assert panel.open_spectrum(spectrum) is True
    assert panel.viewer.layer_list.count() == 1
    panel.close()


def test_spectrum_panel_open_corrupt_returns_false(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = _manager_with_experiment(tmp_path)
    panel = SpectrumPanel(manager)
    bad = tmp_path / "bad.ft2"
    bad.write_bytes(b"not a pipe file")
    assert panel.open_spectrum(bad) is False
    panel.close()


# ----------------------------------------------------------------------
# Main window three columns.
# ----------------------------------------------------------------------
def test_main_window_three_column_layout(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    assert window.main_splitter.count() == 4
    assert window.project_tree is not None
    assert window.pipeline is not None
    assert window.spectrum_panel is not None
    # 0.2.141: The log column is between the pipeline and the spectrum viewer.
    assert (
        window.main_splitter.indexOf(window.center_panel)
        < window.main_splitter.indexOf(window.log_panel)
        < window.main_splitter.indexOf(window.spectrum_panel)
    )
    # 0.2.143: There is no hard upper limit for column width and can be dragged freely (the lower
    # limit is the natural size of the content).
    assert window.log_panel.minimumWidth() <= 400
    assert window.log_panel.maximumWidth() >= 10000
    assert window.project_tree.minimumWidth() <= 1  # No longer mandatory 330.
    # The default initial column width is fixed [420,600,300,600] (1920 in total), and the narrow
    # screen is narrowed by splitter.
    from qtcompat.QtGui import QGuiApplication

    screen = window.screen() or QGuiApplication.primaryScreen()
    if screen is not None:
        avail = screen.availableGeometry()
        cols = window.main_splitter.sizes()
        assert sum(cols) <= avail.width()
        assert min(cols) > 0
        assert window.geometry().top() == avail.top()
        assert window.height() <= avail.height()
    # By default, the first experiment type is focused -> the middle is the experiment type page
    # (embedded sample data import form).
    assert window.center_panel.stack.currentIndex() == 2
    assert window.center_panel.experiment_page._exp_id == "exp_001"
    assert "demo" in window.windowTitle()
    # The flat compatibility table has been deleted (0.2.199-patch29hr): changed to check the number
    # of real project experiments.
    assert len([e for e in window.manager.project.experiments if not e.trashed]) == 2
    window.close()


def test_main_window_spectrum_expand_toggle(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29bp: spectrum zoom button -- Collapse the three parts on the left, then click
    restore."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    btn = window.spectrum_panel.expand_button
    assert btn.text() == "enlarge"
    assert not window.project_tree.isHidden()
    assert not window.center_panel.isHidden()
    assert not window.log_panel.isHidden()
    btn.setChecked(True)
    assert btn.text() == "close"
    assert window.project_tree.isHidden()
    assert window.center_panel.isHidden()
    assert window.log_panel.isHidden()
    assert not window.spectrum_panel.isHidden()
    # 0.2.199-patch29bq: Only the drawing area is enlarged, the control column on the right is
    # retained, and the small drawing area is hidden.
    panel = window.spectrum_panel
    assert panel._expanded
    assert panel._expand_splitter is not None
    assert panel.viewer.plot_area.parent() is panel._expand_splitter
    assert panel._expand_splitter.indexOf(panel.viewer.plot_area) == 0
    assert panel._expand_controls is not None
    assert panel.viewer.controls_layout.parentWidget().parent() is panel._expand_controls
    assert panel.lists_row_widget.parent() is panel._expand_controls
    assert panel.peak_toolbar_widget.parent() is panel._expand_controls
    assert panel.peak_table.parent() is panel._expand_controls
    assert panel.viewer.isHidden()
    # 0.2.199-patch29hz - Modification 26: The top title line must not be stretched into a blank
    # block after zooming in.
    window.resize(1200, 800)
    window.show()
    QApplication.processEvents()
    btn.setChecked(False)
    btn.setChecked(True)
    QApplication.processEvents()
    lay = panel.layout()
    header_item = lay.itemAt(0)
    title = next(
        lb for lb in panel.findChildren(QLabel) if lb.text() == "spectrum"
    )
    assert header_item.geometry().height() <= 40  # Originally 311px (blank block).
    assert title.height() <= 40
    assert panel._expand_splitter is not None
    assert panel._expand_splitter.height() >= panel.height() - 80
    btn.setChecked(False)
    assert btn.text() == "enlarge"
    assert not window.project_tree.isHidden()
    assert not window.center_panel.isHidden()
    assert not window.log_panel.isHidden()
    assert not panel._expanded
    assert panel._expand_splitter is None
    assert panel.viewer.plot_area.parent() is panel.viewer.view_splitter
    assert not panel.viewer.isHidden()
    assert panel.lists_row_widget.parent() is panel._panel_splitter
    window.close()


def test_spectrum_panel_file_help_menus(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29br: The file/help menu is to the right of the zoom button; the view menu has
    no spectrum viewer entry."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    panel = window.spectrum_panel
    assert panel.file_button.menu() is panel.file_menu
    assert panel.help_button.menu() is panel.help_menu
    row = panel.lists_row
    assert row.indexOf(panel.expand_button) < row.indexOf(panel.file_button)
    assert row.indexOf(panel.file_button) < row.indexOf(panel.help_button)
    file_texts = [a.text() for a in panel.file_menu.actions()]
    assert "Open the current data spectrum" in file_texts and "clear spectrum" in file_texts
    assert "Open any spectrum..." in file_texts
    help_texts = [a.text() for a in panel.help_menu.actions()]
    assert "Operating Instructions" in help_texts
    panel._on_menu_clear_spectrum()  # Safe in empty state.
    view_menu = None
    for action in window.menuBar().actions():
        if action.text() == "&View":
            view_menu = action.menu()
    assert view_menu is not None
    texts = [a.text() for a in view_menu.actions()]
    assert not any("spectrum viewer" in t for t in texts)
    window.close()


def test_main_window_has_app_icon(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29eq: The main window is set with an application icon
    (gui/assets/nmrforge.png)."""
    from ui_support.theme import app_icon

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    assert app_icon() is not None
    assert not window.windowIcon().isNull()
    window.close()


def test_tools_menu_standalone_quality_entries(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29em: The "Tools" menu contains an independent entrance for data quality
    inspection/spectrum quality assessment, located between view and settings."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    menus = [a.text() for a in window.menuBar().actions()]
    assert "&Tools" in menus
    idx = menus.index("&Tools")
    assert menus[idx - 1] == "&View"
    assert menus[idx + 1] == "&Settings"
    tools_menu = next(
        a.menu() for a in window.menuBar().actions() if a.text() == "&Tools"
    )
    labels = [a.text() for a in tools_menu.actions()]
    assert "Data quality inspection..." in labels
    assert "spectrum quality assessment..." in labels
    window.close()


def test_tools_run_jumps_to_workspace_log(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29em: When the tool is executed, it jumps to the top level (workspace root) and
    switches the log to the global (NMRForgeWorkspace) scope."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    top = tree.topLevelItem(0)
    assert top is not None
    child = top.child(0)
    if child is not None:
        tree.setCurrentItem(child)
    monkeypatch.setattr(
        "qtcompat.QtWidgets.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(tmp_path / "nope.fid"), ""),
    )
    window._run_standalone_fid_diagnostics()
    assert tree.currentItem() is top
    assert window.log_panel.current_scope() == "global"
    window.close()


def test_menu_mnemonics_unique_and_activate(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29en: The top-level menu mnemonic key (&X) is unique, and Alt+letter can pop up
    the corresponding menu (tool/set up has always been T, and the ambiguity of Alt+T caused the
    setting of the mnemonic key to be invalid)."""
    from qtcompat.QtCore import Qt
    from qtcompat.QtTest import QTest

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    window.show()
    qapp.processEvents()
    bar = window.menuBar()
    letters: list[str] = []
    by_letter: dict[str, object] = {}
    for action in bar.actions():
        text = action.text()
        assert "&" in text, f"The menu is missing a mnemonic key: {text}"
        letter = text.split("&", 1)[1][0]
        letters.append(letter)
        by_letter[letter] = action.menu()
    assert len(set(letters)) == len(letters), f"Mnemonic key repetition: {letters}"
    # Alt+T -> Tools;Alt+S -> Settings.
    for key, title in ((Qt.Key.Key_T, "&Tools"), (Qt.Key.Key_S, "&Settings")):
        target = next(
            a.menu()
            for a in bar.actions()
            if a.text() == title
        )
        bar.setFocus()
        QTest.keyClick(bar, key, Qt.KeyboardModifier.AltModifier)
        qapp.processEvents()
        popup = QApplication.activePopupWidget()
        assert popup is target, f"Alt+{key} Did not pop up {title}"
        popup.close()
        qapp.processEvents()
    window.close()


def test_other_menu_routes_to_standalone_check(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29ej: Independent entry is routed to detection by kind."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    calls: list[tuple[str, str]] = []
    window._run_standalone_check = (  # type: ignore[method-assign]
        lambda paths, kind: calls.append((str(paths[0]), kind))
    )
    monkeypatch.setattr(
        "qtcompat.QtWidgets.QFileDialog.getOpenFileName",
        lambda *a, **k: (r"C:\x\d_001.fid", ""),
    )
    window._run_standalone_fid_diagnostics()
    assert calls == [(r"C:\x\d_001.fid", "fid")]
    monkeypatch.setattr(
        "qtcompat.QtWidgets.QFileDialog.getOpenFileName",
        lambda *a, **k: (r"C:\x\d_001.ft3", ""),
    )
    window._run_standalone_spectrum_quality()
    assert calls[-1] == (r"C:\x\d_001.ft3", "spectrum")
    window.close()


def test_main_window_context_updates_on_tree_selection(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    window.project_tree.select_experiment("exp_002")
    assert window.center_panel.stack.currentIndex() == 2  # Experiment type page.
    assert window.center_panel.experiment_page._exp_id == "exp_002"
    assert window.spectrum_panel._current_exp_id == "exp_002"
    window.close()


def test_pipeline_no_import_step(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.162-patch12: The import has been moved to the drop-down, and the pipeline no longer
    contains the import step."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    assert "import" not in panel._rows
    panel.set_selection("data", "exp_001", "d_001")
    assert not panel._rows["fid"].isHidden()
    panel.close()


def test_main_window_log_panel_expands_on_message(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager, controller=FakeProcessingController())
    # 0.2.143:log permanent display.
    assert not window.log_panel.isHidden()
    window.center_panel.set_selection("data", "exp_001", "d_001")
    window.pipeline._on_run_requested("fid")
    assert not window.log_panel.isHidden()
    assert "Generate FID" in window.log_panel.text.toPlainText()
    window.close()


def test_log_panel_scopes_isolate_data_and_group(
    qapp: QApplication,
) -> None:
    """Individual data logs are independent of each other; data groups share the same log;
    switching the selection switches the display."""
    from gui.log_panel import LogPanel

    panel = LogPanel()
    # Data A and Data B are independent.
    panel.set_scope("data", "exp_001", "d_001")
    panel.append("A's log")
    panel.set_scope("data", "exp_001", "d_002")
    panel.append("B's log")
    panel.set_scope("data", "exp_001", "d_001")
    assert "A's log" in panel.text.toPlainText()
    assert "B's log" not in panel.text.toPlainText()
    # Data group sharing.
    panel.set_scope("group", "exp_001", "", "g_1")
    panel.append("group log")
    panel.set_scope("group", "exp_001", "", "g_1")
    assert "group log" in panel.text.toPlainText()
    # The experiment type does not contaminate the global situation.
    panel.set_scope("experiment", "exp_001")
    assert "A's log" not in panel.text.toPlainText()
    panel.set_scope("", "", "")
    assert panel.text.toPlainText() == ""
    panel.close()


def test_log_panel_explicit_scope_routes_group_batch(
    qapp: QApplication,
) -> None:
    """The group batch log explicitly falls into the group scope and is not affected by the
    currently selected data."""
    from gui.log_panel import LogPanel

    panel = LogPanel()
    panel.set_scope("data", "exp_001", "d_001")
    panel.append("Single data log")
    group_scope = panel.scope_key("group", "exp_001", "", "g_1")
    panel.append("Batch Progress 1/3", scope=group_scope)
    # The data log is still displayed, and the group log is in the group scope.
    assert "Batch Progress 1/3" not in panel.text.toPlainText()
    panel.set_scope("group", "exp_001", "", "g_1")
    assert "Batch Progress 1/3" in panel.text.toPlainText()
    assert "Single data log" not in panel.text.toPlainText()
    panel.close()


def test_main_window_empty_state(qapp: QApplication) -> None:
    window = MainWindow()
    assert window.project_tree.tree.topLevelItemCount() == 1  # Workspace Root.
    assert window.manager.project is None
    assert "welcome" in window.windowTitle()
    assert window.pipeline.current_experiment_id() == ""
    window.close()

def test_tree_data_node_context_menu_actions(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Right-click on the Data node: delete/Open directory + batch group addition (excluding
    generation steps)."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    data_item = panel.tree.topLevelItem(0).child(0).child(0).child(0)
    actions: list[tuple[str, str]] = []
    panel.data_action_requested.connect(
        lambda action, data_id: actions.append((action, data_id))
    )
    menu = QMenu()
    panel._on_context_menu_impl(menu, data_item)
    labels = [a.text() for a in menu.actions()]
    assert "Generate FID" not in labels and "Generate spectrum" not in labels
    delete_action = next(a for a in menu.actions() if a.text() == "delete sample data")
    delete_action.trigger()
    assert actions == [("delete", "d_001")]
    panel.close()


def test_tree_folder_terminal_menu_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.93:Raw and other sub-file folders, right-click and select "Open in Terminal", click to
    send the path."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    data_item = panel.tree.topLevelItem(0).child(0).child(0).child(0)
    raw_item = data_item.child(0)  # raw Subfile folder.
    seen: list[str] = []
    panel.open_terminal_requested.connect(seen.append)
    menu = QMenu()
    panel._on_context_menu_impl(menu, raw_item)
    labels = [a.text() for a in menu.actions()]
    assert "Open in terminal" in labels
    action = next(a for a in menu.actions() if a.text() == "Open in terminal")
    action.trigger()
    assert seen and Path(seen[0]).name == "raw"
    panel.close()


def test_terminal_argv_prefers_csh(monkeypatch: pytest.MonkeyPatch) -> None:
    """0.2.93: Open priority csh in terminal; Windows fallback cmd."""
    import sys

    from gui.project_tree import _terminal_argv

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/usr/bin/{name}"
        if name in ("csh", "gnome-terminal")
        else None,
    )
    argv = _terminal_argv("/data/raw")
    assert argv is not None
    assert "csh" in argv
    assert "--working-directory=/data/raw" in argv

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "shutil.which",
        lambda name: "C:/cygwin/bin/csh.exe"
        if name == "csh"
        else (r"C:\Windows\System32\cmd.exe" if name == "cmd" else None),
    )
    argv = _terminal_argv("C:/data/raw")
    assert argv is not None
    assert "csh" in argv[0]


def test_tree_subfolder_context_menu_has_open_path(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Right-click on a subdirectory such as raw and provide "Open the directory where it is
    located"."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    folder_item = panel.tree.topLevelItem(0).child(0).child(0).child(0).child(0)
    menu = QMenu()
    panel._on_context_menu_impl(menu, folder_item)
    labels = [a.text() for a in menu.actions()]
    assert "Open the directory where it is located" in labels
    panel.close()


def test_create_blank_experiment_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blank space/Project Right-click to create a new blank experiment type."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)

    class _FakeNotesDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, parent, title, kind="", values=None):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_fields(self):
            return {}

    monkeypatch.setattr("gui.main_window.NotesDialog", _FakeNotesDialog)
    window._create_experiment()
    assert window.project_tree._pending_kind == "experiment"
    window.project_tree._commit_pending_create("T4")
    assert manager.project is not None
    assert any(e.title == "T4" for e in manager.project.experiments)
    window.close()


def test_delete_project_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Project Right-click to delete the project: After confirmation, close the project and clear
    the tree."""
    from gui.dialogs import ConfirmDialog

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    monkeypatch.setattr(ConfirmDialog, "confirm", staticmethod(lambda *a, **k: True))
    window._delete_project()
    assert window.manager.project is None
    assert window.project_tree.tree.topLevelItemCount() == 1  # Workspace The roots are still there.
    assert "welcome" in window.windowTitle()
    window.close()

def test_welcome_page_shows_workspace_and_recent(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Welcome page: workspace path + recent projects list + new entry (contract v1.3 §9.4)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ProjectManager.create_project(workspace / "projA", "projA")
    ProjectManager.create_project(workspace / "projB", "projB")

    from core.workspace import WorkspaceManager
    from gui.welcome_page import WelcomePage

    page = WelcomePage(workspace=WorkspaceManager(workspace))
    assert str(workspace) in page.workspace_label.text()
    assert page.recent_list.count() == 2
    names = {page.recent_list.item(i).text() for i in range(page.recent_list.count())}
    assert names == {"projA", "projB"}
    page.close()


def test_main_window_welcome_page_on_startup(qapp: QApplication) -> None:
    """When no project is open, the main window displays the welcome page (Workspace page in three
    columns)."""
    window = MainWindow()
    assert window.center_panel.welcome_page is not None
    # Three columns are visible, the welcome page is in the middle.
    assert not window.main_splitter.isHidden()
    assert window.center_panel.stack.currentIndex() == 0  # Workspace Page.
    window.close()

def test_data_selected_shows_pipeline_page(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Select Data -> Pipeline page in the middle; import without manual button."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    tree.setCurrentItem(data_item)
    assert window.center_panel.stack.currentIndex() == 3  # Pipeline Page.
    assert window.pipeline.current_experiment_id() == "exp_001"
    assert "import" not in window.pipeline._rows  # 0.2.162-Patch12:pipeline no import step.
    window.close()

def test_import_failure_handled_on_main_thread(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the import fails, the signal will be returned to the main thread for processing (the
    modal box will not pop up in the background thread)."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    messages: list[str] = []
    monkeypatch.setattr(
        "gui.main_window.InfoDialog.show_info",
        staticmethod(lambda parent, title, text_: messages.append(text_)),
    )
    monkeypatch.setattr(
        "threading.Thread",
        lambda *a, **k: _SyncThread(*a, **k),
    )

    def fail_import(mgr, exp_id, source, *, segments=None, copy=True):
        raise RuntimeError("Simulate import failure")

    monkeypatch.setattr("workflow.import_workflow.import_data", fail_import)
    window = MainWindow(manager=manager)
    window._import_experiment_async(
        {"source": str(tmp_path / "nonexistent"), "title": "T", "copy": True}
    )
    assert messages and ((
        "directory does not exist"
    ) in messages[0] or "import failed" in messages[0])
    assert "import failed" in window.log_panel.text.toPlainText()
    window.close()


def test_kinetics_import_failure_is_explicit_rejection(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IMPORT-007 A:Kinetics uses the "refuse to import" prompt, which does not imply that it has
    been imported read-only."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "gui.main_window.InfoDialog.show_info",
        staticmethod(
            lambda parent, title, text_: messages.append((title, text_))
        ),
    )
    window = MainWindow(manager=manager)
    window._on_import_failed(
            "KineticsUnsupportedError: Kinetics experiment detected, current product does not "
            "support import"
    )

    assert messages == [
        ("Import is not supported", (
            "Kinetics experiment detected, current product does not support import"
        ))
    ]
    log = window.log_panel.text.toPlainText()
    assert "import rejected" in log
    assert "Data has been imported" not in log
    window.close()


class _SyncThread:
    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        self._target()

def test_spectrum_panel_open_current_data_spectrum(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29hz-Repair 27: Split the menu into two items + No score/If there is no data,
    there should be a clear prompt."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = SpectrumPanel(manager)
    texts = [a.text() for a in panel.file_menu.actions()]
    assert texts[:3] == [
        "Open the current data spectrum",
        "Open any spectrum...",
        "clear spectrum",
    ]

    shown: list[str] = []
    monkeypatch.setattr(
        "gui.spectrum_panel.InfoDialog.show_info",
        staticmethod(lambda _parent, _title, text: shown.append(text)),
    )
    # ① No data selected.
    panel.set_context("", "")
    panel._on_menu_open_current_spectrum()
    assert shown[-1] == "No data is currently selected"
    # ② The data is selected but the spectrum has not been generated (the scenario that the user
    # will actually click on).
    panel.set_context("exp_001", "d_001")
    panel._on_menu_open_current_spectrum()
    assert shown[-1] == "The current data has not generated spectrum yet"
    # ③ With spectrum: The same effect as Pipeline's "display spectrum", it is loaded directly and
    # no prompt is displayed.
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")
    panel.set_context("exp_001", "d_001")
    panel._on_menu_open_current_spectrum()
    assert panel._current_spectrum is not None
    assert len(shown) == 2
    panel.close()


def test_spectrum_panel_scans_data_dir_layout(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schema 1.3 layout: spectrum is located under data_dir(...,"spectra")/, and the panel can be
    listed and opened."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert panel.file_list.count() == 1
    assert panel.file_list.item(0).text() == "exp_001-d_001.ft2"
    assert panel.open_spectrum(spectra_dir / "exp_001-d_001.ft2") is True
    panel.close()


def test_spectrum_panel_excludes_fid_from_list(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.77: The spectrum file list only lists.ft2/.ft3, and the process directory raw.fid is no
    longer mixed in."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")
    process_dir = manager.data_dir("exp_001", "d_001", "process")
    process_dir.mkdir(parents=True, exist_ok=True)
    (process_dir / "raw.fid").write_bytes(b"fid")
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert panel.file_list.count() == 1
    assert panel.file_list.item(0).text() == "exp_001-d_001.ft2"
    panel.close()


def test_spectrum_panel_empty_spectra_clears_viewer(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.85: When the sample data has no spectrum, the right side is left blank (the previous
    spectrum will not be retained after switching)."""
    manager = ProjectManager.create_project(tmp_path / "proj2", "demo")
    entry = manager.create_experiment("A")
    data1 = manager.import_data(entry.id, "/fake/1")
    data2 = manager.import_data(entry.id, "/fake/2")
    spectra1 = manager.data_dir(entry.id, data1.id, "spectra")
    spectra1.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra1 / f"{entry.id}-{data1.id}.ft2")
    panel = SpectrumPanel(manager)
    panel.set_context(entry.id, data1.id)
    assert panel.load_current_spectrum() is True
    assert panel.viewer.layer_list.count() == 1
    # Data2 spectrum file folder is empty -> the viewer is cleared.
    panel.set_context(entry.id, data2.id)
    assert panel.viewer.layer_list.count() == 0
    assert panel._current_spectrum is None
    panel.close()


def test_spectrum_panel_finds_dataset_id_named_spectrum(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.112: The final spectrum named according to dataset_id (not exp_id-data_id) can also be
    found."""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("A")
    data = manager.import_data(entry.id, "/data/hsqc_2d")
    spectra = manager.data_dir(entry.id, data.id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / "hsqc_2d.ft2")
    panel = SpectrumPanel(manager)
    panel.set_context(entry.id, data.id)
    assert panel.load_current_spectrum() is True
    assert panel.file_list.count() == 1
    assert panel.file_list.item(0).text() == "hsqc_2d.ft2"
    panel.close()


def test_spectrum_param_report_shows_phase_results() -> None:
    """0.2.108: parameter report displays dimension-by-dimension phase / direct dimension phase /
    number of backend runs."""
    from gui.pipeline_panel import _spectrum_param_report

    report = _spectrum_param_report(
        {
            "phase_route": "unified",
            "phases": {"F2": (0.0, 10.0), "F1": (-45.0, 0.0)},
            "direct_phase": (0.0, 10.0),
            "backend_runs": 3,
            "extract": True,
        }
    )
    assert "phase optimisation approach: Unified automatic processing" in report
    assert "Phase per dimension" in report
    assert "F1: p0=-45.0° p1=0.0°" in report
    assert "F2: p0=0.0° p1=10.0°" in report
    assert "direct dimension phase" in report
    assert "Number of backend runs: 3" in report
    assert "◆ Handle parameter and optimisation" in report
    # 0.2.155: Simplification -- Internal parameters (such as the extraction window) no longer
    # appear in the report.
    assert "extract" not in report
    assert "Extraction window" not in report


def test_pipeline_show_spectrum_button_on_spectrum_success(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.88: After the spectrum generation is completed, the "Show spectrum" button will appear.
    Click to make a request."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / "exp_001-d_001.ft2")
    from gui.pipeline_panel import PipelinePanel

    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    button = panel._rows["spectrum"].show_spectrum_button
    assert not button.isHidden()
    seen: list[str] = []
    panel.show_spectrum_requested.connect(seen.append)
    button.click()
    assert seen == ["spectrum"]
    panel.close()


def test_import_done_clears_import_form(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.112: Clear the middle page import form after successful import (name/path)."""
    from types import SimpleNamespace

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    window.center_panel.experiment_page.name_edit.setText("sample 1")
    window.center_panel.experiment_page.source_edit.setText("/data/a")
    monkeypatch.setattr(
        "gui.settings.load_settings",
        lambda: {"guide": {"first_import_hint_shown": True}},
    )
    result = SimpleNamespace(
        experiment_id="exp_001",
        data_id="d_001",
        run_id="R-1",
        file_count=1,
        total_bytes=10,
        warnings=[],
    )
    window._on_import_done(result)
    assert window.center_panel.experiment_page.name_edit.text() == ""
    assert window.center_panel.experiment_page.source_edit.text() == ""
    window.close()


def test_pipeline_status_peaks_from_data_dir(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Peaks status check data_dir(...,"peaks")/<exp>-<data>.list."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")
    peaks_dir = manager.data_dir("exp_001", "d_001", "peaks")
    peaks_dir.mkdir(parents=True, exist_ok=True)
    (peaks_dir / "exp_001-d_001.list").write_text(
        "Assignment w1 w2 Data Height Volume\nG1  115.000  8.000  0  100  0\n",
        encoding="utf-8",
    )
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["spectrum"] == "SUCCESS"
    assert statuses["peaks"] == "SUCCESS"


def test_pipeline_peaks_step_runs_pick_peaks(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The peaks step run button goes to pick_peaks and refreshes the state."""
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")

    class PeaksController(FakeProcessingController):
        def pick_peaks(
            self, data, exp_id=None, data_id=None, sigma_multiplier=None
        ) -> dict:
            self.calls.append("pick_peaks")
            peaks_dir = manager.data_dir(exp_id, data_id, "peaks")
            peaks_dir.mkdir(parents=True, exist_ok=True)
            (peaks_dir / f"{exp_id}-{data_id}.list").write_text(
                "Assignment w1 w2 Data Height Volume\nG1  115.000  8.000  0  100  0\n",
                encoding="utf-8",
            )
            return {"status": "success", "peak_count": 1}

    controller = PeaksController()
    panel = PipelinePanel(manager, controller)
    log = LogPanel()
    panel.log_message.connect(log.append)
    panel.set_selection("data", "exp_001", "d_001")
    assert not panel._rows["peaks"].run_button.isHidden()  # peaks READY
    panel._on_run_requested("peaks")
    assert "pick_peaks" in controller.calls
    # Peak table appears -> SUCCESS.
    assert panel._rows["peaks"].status_label.text().startswith("✓")
    panel.close()
    log.close()


def test_data_delete_wires_manager_delete_data(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sample data deletion wiring: manager.delete_data (experiment type is not deleted)."""
    from gui.dialogs import ConfirmDialog

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    monkeypatch.setattr(ConfirmDialog, "confirm", staticmethod(lambda *a, **k: True))

    def fake(path, fallback_dir, rel=None):
        return path

    monkeypatch.setattr("core.project.manager.send_to_trash", fake)
    window.project_tree.select_experiment("exp_001")
    window._delete_data("exp_001", "d_001")
    entry = manager.project.experiment("exp_001")
    assert entry is not None and entry.data and all(d.trashed for d in entry.data)
    window.close()


def test_data_rename_persists_title(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Data rename: write DataEntry.title and drop to disk (readable after restarting)."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    window._rename_data("exp_001", "d_001", "After rename")
    data_entry = manager.project.experiment("exp_001").data[0]
    assert data_entry.title == "After rename"
    # Tree display title.
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    assert data_item.text(0) == "After rename"
    window.close()

def test_double_click_data_keeps_pipeline_and_opens_path(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Double-click the data node: issue open_path_requested (without jumping to the import page),
    and keep the Pipeline in the middle."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    base = manager.data_base("exp_001", "d_001")
    base.mkdir(parents=True, exist_ok=True)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    opened: list[str] = []
    window.project_tree.open_path_requested.connect(lambda p: opened.append(p))
    tree.setCurrentItem(data_item)
    window.project_tree._on_double_clicked(data_item, 0)
    # 0.2.199-patch29ge: Double-click the data to open the d_xxx base, not raw.
    assert opened and Path(opened[0]) == base
    assert window.center_panel.stack.currentIndex() == 3  # Pipeline Page.
    window.close()


def test_double_click_folder_opens_folder_path(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Double-click the sub-file folder: open the file folder directory and keep Pipeline in the
    middle."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    folder_item = data_item.child(2)  # spectra
    opened: list[str] = []
    window.project_tree.open_path_requested.connect(lambda p: opened.append(p))
    tree.setCurrentItem(folder_item)
    window.project_tree._on_double_clicked(folder_item, 0)
    assert opened and Path(opened[0]) == spectra_dir
    assert window.center_panel.stack.currentIndex() == 3
    window.close()

def test_right_click_open_path_emits_signal(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Right-click "Open the directory where it is located": data/folder The node emits
    open_path_requested (the same as double-clicking)."""
    # 0.2.199-patch29gk Supplement: MainWindow connects open_terminal_requested to _open_terminal ->
    # open_in_terminal(x-terminal-emulator). The test only verifies the signal; mock it to avoid
    # actually opening the terminal window.
    monkeypatch.setattr("gui.project_tree.open_in_terminal", lambda p: True)
    from qtcompat.QtWidgets import QMenu

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    base = manager.data_base("exp_001", "d_001")
    base.mkdir(parents=True, exist_ok=True)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    folder_item = data_item.child(2)  # spectra
    opened: list[str] = []
    opened_term: list[str] = []
    window.project_tree.open_path_requested.connect(lambda p: opened.append(p))
    window.project_tree.open_terminal_requested.connect(
        lambda p: opened_term.append(p)
    )

    data_menu = window.project_tree._on_context_menu_impl(QMenu(), data_item)
    data_acts = [a for a in data_menu.actions() if a.text() == (
        "Open the directory where it is located"
    )]
    assert len(data_acts) == 1
    data_acts[0].trigger()
    # 0.2.199-patch29ge: Right-click the data to open d_xxx base, not raw.
    assert opened and Path(opened[0]) == base
    # 0.2.199-patch29gf: The data node terminal is also opened d_xxx (the raw child node can open
    # the terminal by itself).
    data_terms = [
        a for a in data_menu.actions() if a.text() == "Open in terminal"
    ]
    assert len(data_terms) == 1
    data_terms[0].trigger()
    assert opened_term and Path(opened_term[0]) == base

    folder_menu = window.project_tree._on_context_menu_impl(QMenu(), folder_item)
    folder_acts = [a for a in folder_menu.actions() if a.text() == (
        "Open the directory where it is located"
    )]
    assert len(folder_acts) == 1
    folder_acts[0].trigger()
    assert len(opened) == 2 and Path(opened[1]) == spectra_dir
    assert window.center_panel.stack.currentIndex() == 3  # Pipeline Page.
    window.close()

def test_spectrum_panel_vertical_layout(qapp: QApplication) -> None:
    """The spectrum panel is arranged up and down: the file list is at the top and the viewer is at
    the bottom."""
    from qtcompat.QtCore import Qt
    from qtcompat.QtWidgets import QSplitter

    panel = SpectrumPanel()
    found: list[QSplitter] = []

    def walk(widget) -> None:
        for child in widget.children():
            if isinstance(child, QSplitter):
                found.append(child)
            walk(child)

    walk(panel)
    assert found, "SpectrumPanel should have QSplitter inside"
    splitter = next(s for s in found if s.count() == 4)
    assert splitter.orientation() == Qt.Orientation.Vertical
    assert splitter.count() == 4  # viewer / File list/toolbar/peak table.
    assert panel.file_list.maximumWidth() > 1000  # No horizontal width limit.
    panel.close()

def test_viewer_internal_vertical_layout(qapp: QApplication) -> None:
    """The internal layout of SpectrumViewer is up and down: plot is on the top and the control
    panel is on the bottom."""
    from qtcompat.QtCore import Qt
    from qtcompat.QtWidgets import QSplitter

    from viewer.spectrum_viewer import SpectrumViewer

    viewer = SpectrumViewer()
    found: list[QSplitter] = []

    def walk(widget) -> None:
        for child in widget.children():
            if isinstance(child, QSplitter):
                found.append(child)
            walk(child)

    walk(viewer)
    assert found, "There should be QSplitter in SpectrumViewer"
    splitter = found[0]
    assert splitter.orientation() == Qt.Orientation.Vertical
    assert splitter.count() == 2
    assert splitter.widget(0) is viewer.plot_area  # Upper spectrum area.
    viewer.close()

def test_project_dashboard_stats_and_runs(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Project Dashboard: Statistics + recent runs."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    run = manager.start_run("exp_001", workflow_ref="import")
    manager.finish_run(run.run_id, "success", message="ok")
    manager.save()
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    proj_item = tree.topLevelItem(0).child(0)
    tree.setCurrentItem(proj_item)
    assert window.center_panel.stack.currentIndex() == 1  # Project Dashboard
    assert "experiment:" in window.center_panel.project_page.stats_label.text()
    assert window.center_panel.project_page.runs_table.rowCount() >= 1
    window.close()


def test_experiment_dashboard_data_rows(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Experiment Dashboard: Data list."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    exp_item = tree.topLevelItem(0).child(0).child(0)
    tree.setCurrentItem(exp_item)
    assert window.center_panel.stack.currentIndex() == 2
    assert window.center_panel.experiment_page.data_table.rowCount() >= 1
    window.close()


def test_run_history_dialog(tmp_path: Path, qapp: QApplication) -> None:
    """Run History Dialog: List + Details."""
    from gui.dialogs import RunHistoryDialog

    manager = ProjectManager.create_project(tmp_path / "ws" / "proj", "demo")
    manager.create_experiment("HSQC")
    run = manager.start_run("exp_001", workflow_ref="import")
    manager.finish_run(run.run_id, "success", outputs={"spectrum": "x.ft2"}, message="ok")
    dialog = RunHistoryDialog(None, manager.project.workflow_runs, "demo")
    assert dialog.table.rowCount() == 1
    dialog.table.selectRow(0)
    assert "x.ft2" in dialog.detail_label.text()
    dialog.close()


def test_spectrum_peak_linkage(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spectrum - peak table linkage: peak table loading + Two-way highlighting/selected."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / "exp_001-d_001.ft2").write_bytes(b"x")
    peaks = manager.data_dir("exp_001", "d_001", "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / "exp_001-d_001.list").write_text(
        "Assignment w1 w2 Data Height Volume\n"
        "G1  115.000  8.000  0  100  0\n"
        "A2  118.000  7.500  0  80  0\n",
        encoding="utf-8",
    )
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    panel._load_peaks(spectra / "exp_001-d_001.ft2")  # 0.2.88:Explicitly load peak table.
    assert panel.peak_table.rowCount() == 2
    assert len(panel.viewer._peaks) == 2
    panel.peak_table.selectRow(1)
    assert panel.viewer._selected_peak == 1
    panel._on_viewer_peak_clicked(0)
    assert panel.peak_table.currentRow() == 0
    panel.close()

def test_export_poky_button_generates_list(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Export Poky" is available when there is a peak table, and a.list is generated and contains
    header/Fengxing."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / "exp_001-d_001.ft2").write_bytes(b"x")
    peaks = manager.data_dir("exp_001", "d_001", "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / "exp_001-d_001.list").write_text(
        "Assignment w1 w2 Data Height Volume\nG1  115.000  8.000  0  100  0\n",
        encoding="utf-8",
    )
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    panel._load_peaks(spectra / "exp_001-d_001.ft2")  # 0.2.88:Explicitly load peak table.
    assert panel.export_poky_button.isEnabled()

    out = tmp_path / "out.list"
    from gui.peaks_io import export_peaks_poky, load_peaks

    export_peaks_poky(out, load_peaks(peaks / "exp_001-d_001.list"))
    content = out.read_text(encoding="utf-8")
    assert "G1" in content and "115.0" in content and "8.0" in content
    panel.close()


def test_export_poky_button_disabled_without_peaks(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Export Poky" is disabled when there is no peak table."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert not panel.export_poky_button.isEnabled()
    panel.close()


def test_peak_linkage_via_load_peaks(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Load_peaks Line number display and linkage highlighting will not be affected after unified
    loading."""
    import csv

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / "exp_001-d_001.ft2").write_bytes(b"x")
    peaks = manager.data_dir("exp_001", "d_001", "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    with (peaks / "exp_001-d_001.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Peak_ID", "H_shift", "N_shift", "Intensity", "SN", "label"])
        writer.writerow(["1", "8.0", "115.0", "100", "20", "G1"])
        writer.writerow(["2", "7.5", "118.0", "80", "15", "A2"])
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    panel._load_peaks(spectra / "exp_001-d_001.ft2")  # 0.2.88:Explicitly load peak table.
    assert panel.peak_table.rowCount() == 2
    assert len(panel.viewer._peaks) == 2
    panel.peak_table.selectRow(1)
    assert panel.viewer._selected_peak == 1
    panel.close()

def test_spectrum_auto_shown_on_data_select(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After selecting the data, the first spectrum will be automatically displayed (when there is
    a spectrum); repeated refreshes will not reload."""
    import numpy as np
    from nmrglue.fileio import pipe

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    shape = (32, 64)
    data = np.zeros(shape, dtype=np.float32)
    data[16, 32] = 50
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = 64
    dic["FDSPECNUM"] = 32
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDF1T"] = 32
    dic["FDF1SW"] = 6000
    dic["FDF1OBS"] = 600
    dic["FDF1CAR"] = 118
    dic["FDF1ORIG"] = 118 * 600
    dic["FDF2T"] = 64
    dic["FDF2SW"] = 6000
    dic["FDF2OBS"] = 600
    dic["FDF2CAR"] = 4.7
    dic["FDF2ORIG"] = 4.7 * 600
    pipe.write(str(spectra / "exp_001-d_001.ft2"), dic, data, overwrite=True)
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert panel.viewer.layer_list.count() == 0  # 0.2.88:Do not display automatically.
    assert panel.load_current_spectrum() is True
    assert panel.viewer.layer_list.count() == 1
    assert panel._current_spectrum is not None
    panel.refresh()
    assert panel.viewer.layer_list.count() == 1  # Refresh does not reopen.
    panel.close()

def test_folder_node_shows_files(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The drop-down menu of raw and other sub-file folder nodes displays the files in directory."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    raw = manager.data_dir("exp_001", "d_001", "raw")
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "acqus").write_text("x")
    (raw / "ser").write_text("y")
    panel = ProjectTreePanel(manager)
    data_item = panel.tree.topLevelItem(0).child(0).child(0).child(0)
    raw_item = data_item.child(0)
    names = [raw_item.child(i).text(0) for i in range(raw_item.childCount())]
    assert "acqus" in names and "ser" in names
    panel.close()

def test_spectrum_file_double_click_opens_in_panel(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Double-click the spectrum file in the tree -> the spectrum panel on the right is displayed
    directly."""
    import numpy as np
    from nmrglue.fileio import pipe

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    shape = (16, 32)
    data = np.zeros(shape, dtype=np.float32)
    data[8, 16] = 10
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = 32
    dic["FDSPECNUM"] = 16
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDF1T"] = 16
    dic["FDF1SW"] = 6000
    dic["FDF1OBS"] = 600
    dic["FDF1CAR"] = 118
    dic["FDF1ORIG"] = 118 * 600
    dic["FDF2T"] = 32
    dic["FDF2SW"] = 6000
    dic["FDF2OBS"] = 600
    dic["FDF2CAR"] = 4.7
    dic["FDF2ORIG"] = 4.7 * 600
    pipe.write(str(spectra / "exp_001-d_001.ft2"), dic, data, overwrite=True)
    window = MainWindow(manager=manager)
    window.project_tree.select_experiment("exp_001")
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    file_item = data_item.child(2).child(0)  # spectra/exp_001-d_001.ft2
    opened: list[str] = []
    window.project_tree.open_spectrum_requested.connect(lambda p: opened.append(p))
    window.project_tree._on_double_clicked(file_item, 0)
    assert opened and Path(opened[0]).name == "exp_001-d_001.ft2"
    window._open_spectrum_from_tree(opened[0])
    assert window.spectrum_panel.viewer.layer_list.count() == 1
    window.close()


def test_viewer_default_dir_matches_current_data(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The standalone spectrum viewer defaults to opening path=current data spectra directory."""
    from viewer.app import SpectrumWindow

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    window = MainWindow(manager=manager)
    window.project_tree.select_experiment("exp_001")
    window.spectrum_panel.set_context("exp_001", "d_001")
    viewer = SpectrumWindow(start_dir=str(spectra))
    assert Path(viewer.start_dir) == spectra
    viewer.close()
    window.close()

def test_run_step_uses_selected_data_id(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B2G-002: run_step acts on the selected data (not the first one) when passing data_id."""
    monkeypatch.setattr("threading.Thread", SyncThread)
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    manager.import_data("exp_001", "/sampleE")
    manager.save()
    # 0.2.163-patch14: The next step will not be run if the prefix is not completed -- Let the fid
    # of d_002 be ready first.
    from gui.pipeline_state import record_step_success

    fid = manager.data_dir("exp_001", "d_002", "process") / "d_002.fid"
    fid.parent.mkdir(parents=True, exist_ok=True)
    fid.write_bytes(b"fid")
    manager.set_data_fid("exp_001", "d_002", fid)
    record_step_success(manager, "exp_001", "d_002", "fid")
    manager.save()
    seen: list[str] = []

    class ScopedController(FakeProcessingController):
        def generate_spectrum(self, data, exp_id=None, data_id=None) -> str:
            seen.append(data_id or "")
            return "/tmp/x.ft2"

    controller = ScopedController()
    panel = PipelinePanel(manager, controller)
    panel.set_selection("data", "exp_001", "d_002")
    panel._on_run_requested("spectrum")
    assert seen == ["d_002"]
    panel.close()


def test_reset_view_union_of_all_layers(qapp: QApplication) -> None:
    """Multispectral overlay: reset_view displays the combined range of all spectra."""
    from viewer.spectrum import Spectrum, SpectrumAxis
    from viewer.spectrum_viewer import SpectrumViewer

    def axis(label: str, size: int) -> SpectrumAxis:
        return SpectrumAxis(label, size, 6000, 600, 4.7, 4.7 * 600)

    import numpy as np

    s1 = Spectrum(np.random.rand(64, 128), [axis("F1", 64), axis("F2", 128)])
    s2 = Spectrum(np.random.rand(32, 256), [axis("F1", 32), axis("F2", 256)])
    viewer = SpectrumViewer()
    viewer.add_spectrum(s1, name="s1")
    viewer.add_spectrum(s2, name="s2")
    viewer.reset_view()
    x_range, y_range = viewer.plot.getViewBox().viewRange()
    assert x_range[1] >= 255 and y_range[1] >= 63  # Cover two spectrums.
    viewer.close()

def test_rename_project_to_sample_wording(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File structure three-level name: project -> experiment -> sample data (menu/Welcome
    page/context bar)."""
    workspace = tmp_path / "ws2"
    monkeypatch.setattr(
        "gui.main_window.WorkspaceManager", lambda: _TempWorkspace(workspace)
    )
    monkeypatch.setattr(
        "core.workspace.WorkspaceManager",
        lambda *a, **k: _TempWorkspace(workspace),
    )
    window = MainWindow()
    # Unopened items: context bar and welcome page entry copy.
    assert window.context_bar.text() == "project not open"
    assert window.center_panel.welcome_page.new_button.text() == "New project..."
    # Menu bar: "&Experiment" menu, does not contain "project management/Add to/Delete project".
    menus = [action.text() for action in window.menuBar().actions()]
    assert "&experiment" in menus
    experiment_menu = next(
        action.menu()
        for action in window.menuBar().actions()
        if action.text() == "&experiment"
    )
    labels = [action.text() for action in experiment_menu.actions()]
    assert "New experiment..." in labels
    assert "project management" not in labels
    assert "Add project..." not in labels
    assert "delete project..." not in labels
    window.close()


def test_welcome_page_new_project_inline_input(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Welcome page "New Project": Name inline in the page (no pop-up window), press Enter to
    submit and send signal / Esc to cancel."""
    from core.workspace import WorkspaceManager
    from gui.welcome_page import WelcomePage

    workspace = tmp_path / "ws"
    workspace.mkdir()
    page = WelcomePage(workspace=WorkspaceManager(workspace))
    page.show()
    names: list[str] = []
    page.new_project_requested.connect(names.append)
    page._on_new_clicked()
    assert page._name_edit.isVisible()
    assert page._name_edit.placeholderText() == "Enter project name"
    assert page._name_ok_button.isVisible()
    page._name_edit.setText("demo")
    page._commit_name()
    assert names == ["demo"]
    assert page._name_ok_button.isHidden()
    # OK button submit.
    page._on_new_clicked()
    page._name_edit.setText("demo2")
    page._name_ok_button.click()
    assert names == ["demo", "demo2"]
    # Esc Cancel: The input line and OK button are hidden and not signaled.
    page._on_new_clicked()
    page._cancel_name()
    assert page._name_edit.isHidden()
    assert page._name_ok_button.isHidden()
    page.close()


def test_tree_inline_create_experiment_editor_commit(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create a new experiment type: create the in-tree editor after commitData -> closeEditor
    (simulate carriage return)."""
    from qtcompat.QtWidgets import QAbstractItemDelegate

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)

    class _FakeNotesDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, parent, title, kind="", values=None):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_fields(self):
            return {}

    monkeypatch.setattr("gui.main_window.NotesDialog", _FakeNotesDialog)
    window._create_experiment()
    assert window.project_tree._pending_kind == "experiment"

    class _Editor:
        def text(self):
            return "HNCACB"

    window.project_tree._on_editor_commit_data(_Editor())
    window.project_tree._on_editor_closed(
        None, QAbstractItemDelegate.EndEditHint.NoHint
    )
    assert manager.project is not None
    assert any(e.title == "HNCACB" for e in manager.project.experiments)
    window.close()


def test_tree_inline_create_cancel_removes_pending(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create a new experiment type: Edit and cancel (Esc) without creating and removing the node
    to be named."""
    from qtcompat.QtWidgets import QAbstractItemDelegate

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    before = len(manager.project.experiments)
    window._create_experiment()
    assert window.project_tree._pending_item is not None
    window.project_tree._on_editor_closed(
        None, QAbstractItemDelegate.EndEditHint.RevertModelCache
    )
    assert window.project_tree._pending_item is None
    assert len(manager.project.experiments) == before
    window.close()


def test_segmented_import_entry_validates_and_calls_async(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.110: Segmented collection import entry: Container directory is verified and then
    asynchronously imported in segments."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    container = tmp_path / "container"
    container.mkdir()
    for seg in ("s1", "s2"):
        (container / seg).mkdir()
        (container / seg / "acqus").write_text("x", encoding="utf-8")
    captured: list[dict] = []
    monkeypatch.setattr(
        MainWindow,
        "_import_experiment_async",
        lambda self, data: captured.append(data),
    )
    window._segmented_import("exp_001", str(container))
    assert captured and captured[0]["segmented"] is True
    assert captured[0]["source"] == str(container)
    assert captured[0]["title"] == "container"
    assert captured[0]["experiment_id"] == "exp_001"  # G2B-011:Import the current experiment type.
    window.close()


def test_segmented_import_rejects_non_container(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Segmented collection and import: non-container directory prompts and does not initiate
    import."""
    messages: list[str] = []
    monkeypatch.setattr(
        "gui.main_window.InfoDialog.show_info",
        staticmethod(lambda parent, title, text_: messages.append(text_)),
    )
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    captured: list[dict] = []
    monkeypatch.setattr(
        MainWindow,
        "_import_experiment_async",
        lambda self, data: captured.append(data),
    )
    single = tmp_path / "single"
    single.mkdir()
    (single / "acqus").write_text("x", encoding="utf-8")
    window._segmented_import("exp_001", str(single))
    assert not captured
    assert messages and "not a segmented/repeat-experiment container" in messages[0]
    window.close()


def test_segmented_import_emits_current_experiment(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G2B-011: The experiment type page segmentation entry carries the current experiment type id
    when sending a request."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    captured: list[dict] = []
    monkeypatch.setattr(
        MainWindow,
        "_import_experiment_async",
        lambda self, data: captured.append(data),
    )
    container = tmp_path / "container"
    container.mkdir()
    for seg in ("s1", "s2"):
        (container / seg).mkdir()
        (container / seg / "acqus").write_text("x", encoding="utf-8")
    page = window.center_panel.experiment_page
    page.set_context(manager, "exp_001", "Label")
    page.segmented_source_edit.setText(str(container))
    page._on_segmented_import()
    assert captured and captured[0]["segmented"] is True
    assert captured[0]["experiment_id"] == "exp_001"
    window.close()


def test_rename_editor_appears_at_click_position(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After clicking "Rename", the rename input box will appear directly at the right-click
    position (press Enter to submit)."""
    from qtcompat.QtCore import QPoint

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    window.show()
    QApplication.processEvents()
    panel = window.project_tree
    exp_item = panel.tree.topLevelItem(0).child(0).child(0)
    anchor = panel.tree.viewport().mapToGlobal(QPoint(30, 10))
    assert not panel._rename_editor.isVisible()  # Not displayed by default (0.2.112 return).
    panel._begin_rename("experiment", exp_item, anchor)
    editor = panel._rename_editor
    assert editor.isVisible()
    # 0.2.163-patch4: embedded sub-component, the position is relative to the tree panel coordinates
    # (the right edge is retracted when the panel is too narrow).
    expected = panel.mapFromGlobal(anchor)
    assert editor.pos().y() == expected.y()
    assert 0 <= editor.pos().x() <= max(0, panel.width() - editor.width())
    editor._edit.setText("HNCACB2")
    editor._commit()
    assert manager.project is not None
    assert any(e.title == "HNCACB2" for e in manager.project.experiments)
    assert not editor.isVisible()
    window.close()


def test_context_menu_rename_opens_inline_editor(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Right-click on the tree and "Rename": After the menu item is triggered, the right-click
    position changes to the rename input box."""
    from qtcompat.QtCore import QPoint

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    panel.show()
    QApplication.processEvents()
    assert not panel._rename_editor.isVisible()  # Not displayed by default (0.2.112 return).
    project_item = panel.tree.topLevelItem(0).child(0)
    menu = QMenu()
    panel._on_context_menu_impl(menu, project_item, QPoint(10, 20))
    action = next(a for a in menu.actions() if "rename project" in a.text())
    action.triggered.emit()
    assert panel._rename_editor.isVisible()
    assert panel._rename_target == ("project",)
    panel.close()


def test_log_panel_stop_button_emits_signal(qapp: QApplication) -> None:
    """Stop current task button: Click to send stop_requested signal."""
    from gui.log_panel import LogPanel

    panel = LogPanel()
    got: list[int] = []
    panel.stop_requested.connect(lambda: got.append(1))
    assert panel.stop_button.text() == "Stop task"
    panel.stop_button.click()
    assert got == [1]
    panel.close()


def test_main_window_stop_button_logs_termination(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Click Stop: Call the process tree to terminate and record the log (no residual prompts)."""
    import backend.runtime as rt

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager, controller=FakeProcessingController())
    calls: list[int] = []

    def fake_terminate() -> int:
        calls.append(1)
        return 2

    monkeypatch.setattr(rt, "terminate_current_tasks", fake_terminate)
    window.log_panel.stop_button.click()
    assert calls == [1]
    assert not window.log_panel.isHidden()
    assert "Current task has been stopped" in window.log_panel.text.toPlainText()
    assert "2" in window.log_panel.text.toPlainText()
    window.close()


def test_main_window_stop_no_task_notice(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    import backend.runtime as rt

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager, controller=FakeProcessingController())
    monkeypatch.setattr(rt, "terminate_current_tasks", lambda: 0)
    window.log_panel.stop_button.click()
    assert "There are currently no tasks running" in window.log_panel.text.toPlainText()
    window.close()


def test_default_column_widths_1920(qapp: QApplication) -> None:
    """The default column width is fixed [420, 600, 300, 600], totaling 1920; narrow screen will
    not overflow."""
    from qtcompat.QtGui import QGuiApplication

    window = MainWindow()
    screen = window.screen() or QGuiApplication.primaryScreen()
    cols = window.main_splitter.sizes()
    if screen is None:
        assert sum(cols) == 1920
        window.close()
        return
    avail = screen.availableGeometry()
    if avail.width() >= 1920:
        assert sum(cols) == 1920
        assert abs(cols[0] - 420) <= 1
        assert abs(cols[1] - 600) <= 1
        assert abs(cols[2] - 300) <= 1
        assert abs(cols[3] - 600) <= 1
        assert window.width() == 1920
    else:
        assert sum(cols) <= avail.width()
        assert window.width() == avail.width()
    window.close()

def test_phase_panel_visible_only_in_1d(qapp: QApplication) -> None:
    """0.2.147:p0/p1 phase panel single line, only 1D mode appears."""
    from viewer.spectrum_viewer import SpectrumViewer

    viewer = SpectrumViewer()
    assert viewer.phase_panel.isHidden()
    viewer.add_spectrum(_synthetic_spectrum_2d())
    assert viewer.phase_panel.isHidden()  # 2D Don't show.
    viewer.set_1d_mode(True)
    assert not viewer.phase_panel.isHidden()  # Strip 1D mode display.
    viewer.set_1d_mode(False)
    assert viewer.phase_panel.isHidden()
    viewer.close()


def test_spectrum_panel_new_layout_constraints(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.147:Files/Layers One line; Show peaks before Add peak; remove the word Poky."""
    from gui.spectrum_panel import SpectrumPanel

    panel = SpectrumPanel()
    assert panel.file_list.parent() is panel.lists_row_widget
    assert panel.viewer.layer_list.parent() is panel.lists_row_widget
    rows = panel.peak_toolbar_widget.layout()
    first = rows.itemAt(0).layout().itemAt(0).widget()
    assert first is panel.viewer.show_peaks_checkbox
    assert panel.import_poky_button.text() == "Import peaks"
    assert panel.export_poky_button.text() == "Export peaks"
    # 0.2.199-patch29bb: put Delete/Import/Export/Save in the second line.
    row2 = rows.itemAt(1).layout()
    row2_widgets = [row2.itemAt(i).widget() for i in range(row2.count())]
    assert panel.delete_peak_button in row2_widgets
    assert panel.import_poky_button in row2_widgets
    assert panel.export_poky_button in row2_widgets
    assert panel.save_peaks_button in row2_widgets
    # Peak operation row spacing is obvious.
    assert panel.peak_toolbar.spacing() >= 10
    assert panel.peak_toolbar2.spacing() >= 10
    panel.close()


def _synthetic_spectrum_2d():
    from viewer.spectrum import Spectrum, SpectrumAxis

    axis_x = SpectrumAxis(
        label="1H", size=64, sw_hz=6000.0, obs_mhz=600.0,
        carrier_ppm=4.7, orig_hz=4.7 * 600.0,
    )
    axis_y = SpectrumAxis(
        label="15N", size=32, sw_hz=2000.0, obs_mhz=60.0,
        carrier_ppm=118.0, orig_hz=118.0 * 60.0,
    )
    import numpy as np
    return Spectrum(data=np.zeros((32, 64)), axes=[axis_y, axis_x])



def test_spectrum_param_report_shows_diagnostics_details() -> None:
    """0.2.157: The report directly displays the data quality diagnosis details (the running log is
    no longer referenced)."""
    from gui.pipeline_panel import _spectrum_param_report

    report = _spectrum_param_report(
        {
            "diagnostics": {
                "reports": [
                    "DC Offset: Automatically enabled POLY -time",
                    "bad point: 3 points have been fixed",
                ],
                "apply_poly_time": True,
            },
            "backend_runs": 2,
        }
    )
    assert "◆ Data quality diagnosis" in report
    assert "DC Offset: Automatically enabled POLY -time" in report
    assert "bad point: 3 points have been fixed" in report
    assert "See run log for details" not in report


def test_experiment_page_import_buttons(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.162-patch12: The experiment type page (original import block location) contains the
    "Import data" button and drop-down ("Inter-data group analysis" has been hidden,
    2026-09-03)."""
    from gui.main_window import MainWindow

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    manager.create_experiment()
    manager.save()
    window = MainWindow(manager=manager)
    page = window.center_panel.experiment_page
    assert page.import_dropdown_button.text() == "import data"
    page._open_import_dropdown()
    assert page._import_dropdown is not None
    window.close()


def test_rename_editor_text_color(qapp: QApplication) -> None:
    """0.2.162-patch11: Rename the input box with black text on a white background (fix invisible
    text)."""
    from gui.project_tree import _InlineRenameEditor

    editor = _InlineRenameEditor()
    stylesheet = editor._edit.styleSheet()
    assert "background: white" in stylesheet
    assert "color: #222" in stylesheet
    editor.close()


def test_experiment_page_dropdown_not_covering_button(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.163-patch3: Limit the height and scroll bar when the pull-down is too long, and do not
    cover the trigger button."""
    from qtcompat.QtCore import QPoint

    from gui.main_window import MainWindow

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    manager.create_experiment()
    manager.save()
    window = MainWindow(manager=manager)
    window.show()
    QApplication.processEvents()
    # 0.2.194-patch2: The drop-down is an experiment type page sub-component, and the page needs to
    # be selected first (consistent with the real operation); the window is widened to ensure that
    # the middle column can accommodate a 560-wide drop-down (to avoid x clamping).
    window.center_panel.set_selection("experiment", "exp_001")
    window.resize(1400, 900)
    window.main_splitter.setSizes([300, 720, 180, 200])
    QApplication.processEvents()
    page = window.center_panel.experiment_page
    btn = page.import_dropdown_button
    btn_top = btn.mapTo(page, QPoint(0, 0)).y()
    btn_bottom = btn.mapTo(page, QPoint(0, btn.height())).y()
    page._open_import_dropdown()
    drop = page._import_dropdown
    assert drop.isVisible()
    # The drop-down does not cover the button: either below the button (top >= bottom of the
    # button), or completely above the button 0.2.194-patch2: the drop-down is an experiment type
    # page sub-component, pos() is relative to this page.
    covering = drop.pos().y() < btn_bottom and (
        drop.pos().y() + drop.height() > btn_top
    )
    msg = (
        f"drop down {drop.pos().y()}..{drop.pos().y() + drop.height()}"
        f" cover button {btn_top}..{btn_bottom}"
    )
    assert not covering, msg
    # Height restriction: no more than the height of this page, and a scrolling area appears (when
    # the content is too long).
    assert drop.height() <= page.height()
    assert hasattr(drop, "_scroll") and drop._scroll.isVisible()
    window.close()


def test_experiment_page_dropdown_switch(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.162-patch13: Import drop-down switch behaviour (drop-down for inter-group analysis has
    been removed)."""
    from gui.main_window import MainWindow

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    manager.create_experiment()
    manager.save()
    window = MainWindow(manager=manager)
    window.show()
    QApplication.processEvents()
    # 0.2.194-patch2: The drop-down is an experiment type page sub-component, and the page needs to
    # be selected first (consistent with the real operation); the window is widened to ensure that
    # the middle column can accommodate a 560-wide drop-down (to avoid x clamping).
    window.center_panel.set_selection("experiment", "exp_001")
    window.resize(1400, 900)
    window.main_splitter.setSizes([300, 720, 180, 200])
    QApplication.processEvents()
    page = window.center_panel.experiment_page
    page._open_import_dropdown()
    assert page._import_dropdown.isVisible()
    # 0.2.162-patch14: The drop-down should be directly below the button (show first and then move).
    from qtcompat.QtCore import QPoint

    # 0.2.194-patch2: Drop down to the experiment type page sub-component, the position is relative
    # to this page.
    expected = page.import_dropdown_button.mapTo(
        page, QPoint(0, page.import_dropdown_button.height())
    )
    drop = page._import_dropdown
    # Align with the left edge of the button; when the middle column cannot fit it, the
    # position is clamped by the page width (0.2.199-patch30: the drop-down no longer exceeds
    # the page, and a clamped form is carried by the horizontal scrollbar).
    assert drop.pos().x() == min(expected.x(), max(0, page.width() - drop.width()))
    assert drop.width() <= page.width()
    assert drop.pos().y() >= expected.y() - 1  # Below the button.
    assert drop.pos().y() + drop.height() <= page.height() + 1
    window.close()



def test_import_dropdown_has_horizontal_scrollbar_when_host_is_narrow(
    qapp: QApplication,
) -> None:
    """0.2.199-patch30 (user): a host narrower than the drop-down may clip its right edge,
    but the form has to stay reachable by dragging horizontally."""
    from qtcompat.QtWidgets import QPushButton, QWidget

    from gui.dashboards import ImportDataDropdown

    host = QWidget()
    host.resize(300, 480)
    host.show()
    button = QPushButton(host)
    button.setGeometry(8, 8, 120, 28)
    QApplication.processEvents()

    drop = ImportDataDropdown(host)
    drop.open_below(button, "exp_001")
    QApplication.processEvents()
    assert drop.isVisible()
    # Clamped to the host width: the right edge is no longer clipped silently by the parent,
    # it can be reached by scrolling.
    assert drop.width() <= host.width()
    assert drop.pos().x() >= 0
    viewport = drop._scroll.viewport()
    panel = drop._scroll.widget()
    assert panel.width() > viewport.width(), "the form must not be squeezed into the viewport"
    bar = drop._scroll.horizontalScrollBar()
    assert bar.maximum() > 0, "a clamped drop-down must offer a horizontal scrollbar"
    assert bar.value() == 0
    bar.setValue(bar.maximum())
    assert bar.value() == bar.maximum(), "the scrollbar must reach the right edge"
    drop.close()

    # A wide host must not keep the scrollbar: reopening an instance has to measure the
    # unclamped width again, so the clamp from the previous placement cannot stick.
    host.resize(900, 480)
    QApplication.processEvents()
    drop.open_below(button, "exp_001")
    QApplication.processEvents()
    assert drop.width() > 300, "the clamp from a narrow host must not stick"
    # The clamp itself has to be released: the form must no longer be pinned to its natural
    # width (whether a wide host still needs a scrollbar depends on the form's own minimum
    # width, which this change does not govern).
    assert drop._scroll.widget().minimumWidth() == 0
    drop.close()
    host.close()

def test_peak_threshold_range_up_to_50(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29bo/patch29cn: The upper limit of the peak selection threshold is 30σ -> 50σ;
    there is no upper limit for the input box."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection('data', 'exp_001', 'd_001')
    row = panel._rows['peaks']
    assert row.threshold_slider.maximum() == 500          # 50σ
    # There is no upper limit for the input box (patch29cn).
    assert row.threshold_spin.maximum() > 50.0
    assert row.threshold_spin.value() == pytest.approx(35.0)  # Default 35σ(patch29hn).
    assert row.threshold_slider.value() == 350
    row.threshold_spin.setValue(28.5)
    assert row.threshold_slider.value() == 285
    row.threshold_spin.setValue(100.0)                    # Slider limit exceeded.
    assert row.threshold_slider.value() == 500            # Slider stops at 50σ.
    assert row.threshold_spin.value() == pytest.approx(100.0)  # No writeback coverage.
    panel.close()


def test_peaks_threshold_change_does_not_auto_run(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 0.2.199-patch29au: Threshold adjustment does not automatically select peaks, click
    # "run/reprocess" to execute.
    monkeypatch.setattr('threading.Thread', SyncThread)
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir('exp_001', 'd_001', 'spectra')
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / 'exp_001-d_001.ft2')

    class PeaksController(FakeProcessingController):
        def pick_peaks(
            self, data, exp_id=None, data_id=None, sigma_multiplier=None
        ) -> dict:
            self.calls.append(('pick_peaks', sigma_multiplier))
            peaks_dir = manager.data_dir(exp_id, data_id, 'peaks')
            peaks_dir.mkdir(parents=True, exist_ok=True)
            (peaks_dir / f'{exp_id}-{data_id}.list').write_text(
                'Assignment w1 w2 Data Height Volume\n'
                'G1  115.000  8.000  0  100  0\n',
                encoding='utf-8',
            )
            return {'status': 'success', 'peak_count': 1}

    controller = PeaksController()
    panel = PipelinePanel(manager, controller)
    log = LogPanel()
    panel.log_message.connect(log.append)
    panel.set_selection('data', 'exp_001', 'd_001')
    row = panel._rows['peaks']
    row.threshold_spin.setValue(8.0)
    row.threshold_slider.sliderReleased.emit()
    assert controller.calls == []  # Threshold adjustment does not run automatically.
    panel._on_run_requested('peaks')
    # Click Run to execute according to new threshold.
    assert controller.calls == [('pick_peaks', 8.0)]
    panel.close()
    log.close()



def test_project_tree_data_status_shows_picked(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 0.2.199-patch29av: When there is a peak table, the data displays "peak selected".
    from gui.project_tree import ProjectTreePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir('exp_001', 'd_001', 'spectra')
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / 'exp_001-d_001.ft2')
    manager.set_data_spectrum(
        'exp_001', 'd_001', str(spectra / 'exp_001-d_001.ft2')
    )
    peaks = manager.data_dir('exp_001', 'd_001', 'peaks')
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / 'exp_001-d_001.list').write_text(
        'Assignment w1 w2 Data Height Volume\n'
        'G1  115.000  8.000  0  100  0\n',
        encoding='utf-8',
    )
    manager.save()
    panel = ProjectTreePanel(manager)

    def _data_item():
        return panel.tree.topLevelItem(0).child(0).child(0).child(0)

    assert _data_item().text(1) == 'Already peak picking'
    panel.close()


def test_spectrum_display_settings_isolated_per_data(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29fz:contour start / levels / aspect / Mark size is isolated by data -- The
    adjustment of d_001 is not connected to d_002, switch back to d_001 and restore."""
    from gui.spectrum_panel import SpectrumPanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    exp = manager.project.experiment("exp_001")
    assert exp is not None
    d1_id = str(exp.data[0].id)
    d2 = manager.import_data(exp.id, "/fake/2")
    spectra1 = manager.data_dir(exp.id, d1_id, "spectra")
    spectra1.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra1 / f"{exp.id}-{d1_id}.ft2")
    spectra2 = manager.data_dir(exp.id, d2.id, "spectra")
    spectra2.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra2 / f"{exp.id}-{d2.id}.ft2")
    panel = SpectrumPanel(manager)
    panel.set_context(exp.id, d1_id)
    assert panel.load_current_spectrum() is True
    panel.viewer.level_slider.setValue(60)
    panel.viewer.count_slider.setValue(12)
    panel.viewer.aspect_slider.setValue(80)
    panel.peak_size_spin.setValue(2.0)
    assert panel._display_states[(exp.id, d1_id)]["level_slider"] == 60
    # D_002 Use the default when opening for the first time, not affected by d_001.
    panel.set_context(exp.id, d2.id)
    assert panel.load_current_spectrum() is True
    assert panel.viewer.level_slider.value() == 31
    assert panel.viewer.count_slider.value() == 8
    assert panel.viewer.aspect_slider.value() == 0
    assert panel.peak_size_spin.value() == 1.5
    # Switch back to d_001 to restore individual adjustments.
    panel.set_context(exp.id, d1_id)
    assert panel.load_current_spectrum() is True
    assert panel.viewer.level_slider.value() == 60
    assert panel.viewer.count_slider.value() == 12
    assert panel.viewer.aspect_slider.value() == 80
    assert panel.peak_size_spin.value() == 2.0
    panel.close()


def test_log_panel_data_scope_persists_to_data_folder(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.199-patch29ga: The single data log is persisted to d_xxx/log.txt, and the new panel can
    read it back."""
    from core.project import ProjectManager
    from gui.log_panel import LogPanel

    manager = ProjectManager.create_project(tmp_path / "proj_log", "demo")
    exp = manager.create_experiment("HSQC")
    data = manager.import_data(exp.id, "/fake/1")
    log = LogPanel()
    log.set_manager(manager)
    scope = log.scope_key("data", exp.id, data.id)
    log.append("First processing completed", scope=scope)
    path = manager.data_base(exp.id, data.id) / "report" / "log.txt"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "First processing completed" in text
    # New LogPanel (simulation restart) switches to this data and you can see the history.
    log2 = LogPanel()
    log2.set_manager(manager)
    log2.set_scope("data", exp.id, data.id)
    assert any(
        "First processing completed" in line for line in log2._buffers[scope]
    )
    # Clear the panel to clear the record file synchronously (keep the header row).
    log2.clear()
    text2 = path.read_text(encoding="utf-8")
    assert "First processing completed" not in text2
    assert text2.startswith("#")
    log.close()
    log2.close()


def test_spectrum_display_settings_persisted_in_data_folder(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29ga: Display adjustment persistence to d_xxx/ui_state.json, restart and
    restore."""
    import json

    from gui.spectrum_panel import SpectrumPanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    exp = manager.project.experiment("exp_001")
    assert exp is not None
    d1_id = str(exp.data[0].id)
    spectra1 = manager.data_dir(exp.id, d1_id, "spectra")
    spectra1.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra1 / f"{exp.id}-{d1_id}.ft2")
    panel = SpectrumPanel(manager)
    panel.set_context(exp.id, d1_id)
    assert panel.load_current_spectrum() is True
    panel.viewer.level_slider.setValue(55)
    panel.viewer.count_slider.setValue(11)
    panel.viewer.aspect_slider.setValue(70)
    panel.peak_size_spin.setValue(2.5)
    path = manager.data_base(exp.id, d1_id) / "ui_state.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    spectrum = payload["spectrum"]
    assert spectrum["level_slider"] == 55
    assert spectrum["level_count"] == 11
    assert spectrum["aspect"] == 70
    assert spectrum["peak_size"] == 2.5
    # Open the data recovery in a new panel (simulated restart).
    panel2 = SpectrumPanel(manager)
    panel2.set_context(exp.id, d1_id)
    assert panel2.load_current_spectrum() is True
    assert panel2.viewer.level_slider.value() == 55
    assert panel2.viewer.count_slider.value() == 11
    assert panel2.viewer.aspect_slider.value() == 70
    assert panel2.peak_size_spin.value() == 2.5
    panel.close()
    panel2.close()


def test_log_panel_group_scope_persists_to_group_folder(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.199-patch29gb: The data group log is also saved to disk (group directory log.txt), and
    the new panel reads it back."""
    from core.project import ProjectManager
    from gui.log_panel import LogPanel

    manager = ProjectManager.create_project(tmp_path / "proj_gl", "demo")
    exp = manager.create_experiment("HSQC")
    data = manager.import_data(exp.id, "/fake/1")
    group = manager.create_data_group(exp.id, data_ids=[data.id])
    log = LogPanel()
    log.set_manager(manager)
    scope = log.scope_key("group", exp.id, "", group.id)
    log.append("Group log first line", scope=scope)
    path = (
        manager.root
        / exp.id
        / "groups"
        / group.id
        / "report"
        / "log.txt"
    )
    assert path.is_file()
    assert "Group log first line" in path.read_text(encoding="utf-8")
    log2 = LogPanel()
    log2.set_manager(manager)
    log2.set_scope("group", exp.id, "", group.id)
    assert any(
        "Group log first line" in line for line in log2._buffers[scope]
    )
    log2.clear()
    assert "Group log first line" not in path.read_text(encoding="utf-8")
    log.close()
    log2.close()
