"""Manual entry-point tests: script editor / main-window wiring and peak-table
write-back (offscreen)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtcompat.QtWidgets import QApplication

from core.project import ProjectManager
from gui.dialogs import ScriptEditorDialog
from gui.main_window import MainWindow
from gui.peaks_io import import_peaks_poky
from gui.processing import ProcessingController
from gui.spectrum_panel import SpectrumPanel


class SyncThread:
    """Make the background thread synchronous; tests do not depend on thread timing."""

    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


def _manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch | None = None) -> ProjectManager:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    manager = ProjectManager.create_project(ws / "proj", "demo")
    manager.add_experiment("/data/1", title="HSQC")
    manager.save()
    if monkeypatch is not None:
        monkeypatch.setattr("gui.main_window.WorkspaceManager", lambda: _TempWorkspace(ws))
        monkeypatch.setattr("core.workspace.WorkspaceManager", lambda *a, **k: _TempWorkspace(ws))
    return manager


class _TempWorkspace:
    def __init__(self, root) -> None:
        self.root = Path(root)

    def list_projects(self):
        return sorted(
            p for p in self.root.iterdir() if p.is_dir() and (p / "project.json").is_file()
        )

    def ensure(self):
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root


class FakeManualController:
    """Fake manual-processing controller: records calls and returns synchronously."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.script_baselines: list[dict[str, str] | None] = []
        self.fid_content = "#!/bin/csh\nbruk2pipe -in ./ser -out ./test.fid\n"
        self.scripts = {"process.com": "#!/bin/csh\nxyz2pipe -in x.fid\n"}

    def set_manager(self, manager) -> None:
        self.manager = manager

    def manual_fid_com(self, data, exp_id=None, data_id=None) -> str:
        self.calls.append(("manual_fid_com", exp_id, data_id))
        return self.fid_content

    def manual_scripts(self, data, params=None, exp_id=None, data_id=None) -> dict:
        self.calls.append(("manual_scripts", exp_id, data_id, params))
        return dict(self.scripts)

    def run_manual_fid_com(self, data, content, exp_id=None, data_id=None, progress=None) -> str:
        self.calls.append(("run_manual_fid_com", content))
        return "/tmp/test.fid"

    def run_manual_spectrum(
        self,
        data,
        scripts,
        exp_id=None,
        data_id=None,
        progress=None,
        script_baselines=None,
    ) -> str:
        self.calls.append(("run_manual_spectrum", scripts))
        self.script_baselines.append(script_baselines)
        return "/tmp/x.ft2"

    def save_peaks_manual(self, data, peaks, exp_id=None, data_id=None) -> str:
        self.calls.append(("save_peaks_manual", len(peaks), exp_id, data_id))
        return f"/tmp/{exp_id}-{data_id}.csv"


def test_script_editor_dialog_content_and_run_signal(qapp: QApplication) -> None:
    dialog = ScriptEditorDialog(None, "HSQC (exp_001)", content="nmrPipe -in test.fid")
    assert "nmrPipe" in dialog.editor.toPlainText()
    assert dialog.result_data() == {"content": "nmrPipe -in test.fid"}
    emitted: list[str] = []
    dialog.run_requested.connect(emitted.append)
    dialog.editor.setPlainText("xyz2pipe -in x.fid")
    dialog.run_requested.emit(dialog.editor.toPlainText())
    assert emitted == ["xyz2pipe -in x.fid"]
    dialog.close()


def test_main_window_manual_flows(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manual entry opens the matching editor/prompt per step (non-blocking, fake controller)."""
    messages: list[str] = []
    monkeypatch.setattr(
        "gui.main_window.InfoDialog.show_info",
        staticmethod(lambda parent, title, text_: messages.append(text_)),
    )
    shown: list[str] = []
    monkeypatch.setattr(
        "gui.main_window.ScriptEditorDialog.show",
        lambda self: shown.append(self.script_name),
    )
    manager = _manager(tmp_path, monkeypatch)
    controller = FakeManualController()
    window = MainWindow(manager=manager, controller=controller)
    window.project_tree.select_experiment("exp_001")

    window._open_manual_dialog("fid")
    assert ("manual_fid_com", "exp_001", "d_001") in controller.calls
    assert "fid.com" in shown  # 0.2.192: non-modal show, does not exec-lock the main window

    window._open_manual_dialog("spectrum")
    assert ("manual_scripts", "exp_001", "d_001", None) in controller.calls  # open script editor
    assert "process.com" in shown

    window._open_manual_dialog("peaks")
    assert any("峰表" in message for message in messages)
    window.close()


def test_script_editor_single_instance_per_data_step(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only one script editor per data + step; different steps coexist (0.2.193)."""
    manager = _manager(tmp_path, monkeypatch)
    controller = FakeManualController()
    window = MainWindow(manager=manager, controller=controller)
    window.project_tree.select_experiment("exp_001")

    window._open_manual_dialog("spectrum")
    assert len(window._script_editors) == 1
    assert controller.calls.count(("manual_scripts", "exp_001", "d_001", None)) == 1

    # reopen the same step for the same data: reuse, do not create anew
    window._open_manual_dialog("spectrum")
    assert len(window._script_editors) == 1
    assert controller.calls.count(("manual_scripts", "exp_001", "d_001", None)) == 1

    # fid and spectrum are different steps and can coexist
    window._open_manual_dialog("fid")
    assert len(window._script_editors) == 2
    assert ("manual_fid_com", "exp_001", "d_001") in controller.calls

    # closing the spectrum editor releases the dedup key, so it can be reopened
    dialog = window._script_editors[("d_001", "spectrum")]
    dialog.close()
    qapp.processEvents()
    assert ("d_001", "spectrum") not in window._script_editors
    window._open_manual_dialog("spectrum")
    assert len(window._script_editors) == 2
    window.close()


def test_script_run_wires_controller(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Script editor "Run" → run_manual_spectrum (wired via the main window)."""
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager = _manager(tmp_path, monkeypatch)
    controller = FakeManualController()
    window = MainWindow(manager=manager, controller=controller)
    entry = manager.project.experiment("exp_001")
    data_node = entry.data[0]
    baseline = "#!/bin/csh\noriginal script\n"
    dialog = ScriptEditorDialog(
        None, "x", script_name="process.com", content=baseline, save_dir=tmp_path
    )
    window._wire_script_run(dialog, data_node, "exp_001", "d_001", "process.com")
    dialog.editor.setPlainText("edited and saved script")
    dialog._on_run()
    assert (
        "run_manual_spectrum",
        {"process.com": "edited and saved script"},
    ) in controller.calls
    assert controller.script_baselines == [{"process.com": baseline}]
    assert (tmp_path / "process.com").read_text(encoding="utf-8") == ("edited and saved script")
    window.close()
    dialog.close()


def test_standalone_fid_diagnostic_menu_pickers(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    "Regression coverage: test standalone fid diagnostic menu pickers."
    from qtcompat.QtWidgets import QFileDialog

    from ui_support.i18n import tr

    manager = _manager(tmp_path, monkeypatch)
    window = MainWindow(manager=manager, controller=FakeManualController())
    menu_bar = window.menuBar()
    tools_menu = next(
        action.menu()
        for action in menu_bar.actions()
        if action.menu() is not None and action.menu().title() == tr("&Tools")
    )
    diagnostic_menu = tools_menu.actions()[0].menu()
    file_action, folder_action = diagnostic_menu.actions()
    requests = []
    monkeypatch.setattr(
        window,
        "_run_standalone_check",
        lambda paths, kind: requests.append((paths, kind)),
    )
    file_picks = []
    folder_picks = []
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args: (file_picks.append(args) or "/tmp/a.fid", "")),
    )
    monkeypatch.setattr(
        "gui.main_window.choose_directory",
        lambda *args: folder_picks.append(args) or "/tmp/slices",
    )

    file_action.trigger()
    folder_action.trigger()
    assert [(str(paths[0]), kind) for paths, kind in requests] == [
        ("/tmp/a.fid", "fid"),
        ("/tmp/slices", "fid"),
    ]
    assert len(file_picks) == 1
    assert len(folder_picks) == 1

    requests.clear()
    file_picks.clear()
    folder_picks.clear()
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args: (file_picks.append(args) or "", "")),
    )
    monkeypatch.setattr(
        "gui.main_window.choose_directory",
        lambda *args: folder_picks.append(args) or "",
    )
    file_action.trigger()
    folder_action.trigger()
    assert requests == []
    assert len(file_picks) == 1
    assert len(folder_picks) == 1
    window.close()


def test_script_editor_save_writes_script_file(tmp_path: Path, qapp: QApplication) -> None:
    """The "Save" button writes the script to the data directory at once and closes (0.2.192)."""
    from qtcompat.QtWidgets import QDialog

    dialog = ScriptEditorDialog(
        None, "x", script_name="process.com", content="old", save_dir=tmp_path
    )
    dialog.editor.setPlainText("#!/bin/csh\nxyz2pipe -in x.fid\n")
    dialog.save_btn.click()
    assert (tmp_path / "process.com").read_text(encoding="utf-8") == (
        "#!/bin/csh\nxyz2pipe -in x.fid\n"
    )
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.close()


def test_script_editor_run_saves_emits_and_closes(tmp_path: Path, qapp: QApplication) -> None:
    """The "Run" button saves first, emits the content and closes automatically (0.2.192)."""
    dialog = ScriptEditorDialog(
        None, "x", script_name="process.com", content="old", save_dir=tmp_path
    )
    emitted: list[str] = []
    dialog.run_requested.connect(emitted.append)
    dialog.show()
    assert dialog.isVisible()
    dialog.editor.setPlainText("#!/bin/csh\nxyz2pipe -in x.fid\n")
    dialog.run_btn.click()
    assert emitted == ["#!/bin/csh\nxyz2pipe -in x.fid\n"]
    assert (tmp_path / "process.com").read_text(encoding="utf-8") == (
        "#!/bin/csh\nxyz2pipe -in x.fid\n"
    )
    assert not dialog.isVisible()  # closes automatically after running
    dialog.close()


def test_spectrum_panel_peak_add_edit_delete_save(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Peak table add/edit/delete/save: write back Poky .list, record
    manual_peaks, refresh viewer."""
    messages: list[str] = []
    monkeypatch.setattr(
        "gui.spectrum_panel.InfoDialog.show_info",
        staticmethod(lambda parent, title, text_: messages.append(text_)),
    )
    manager = _manager(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / "exp_001-d_001.ft2").write_bytes(b"x")
    peaks = manager.data_dir("exp_001", "d_001", "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / "exp_001-d_001.list").write_text(
        "Assignment w1 w2 Data Height Volume\nG1  115.000  8.000  0  100  0\n",
        encoding="utf-8",
    )
    controller = ProcessingController(manager)
    panel = SpectrumPanel(manager, controller=controller)
    panel.set_context("exp_001", "d_001")
    panel._load_peaks(spectra / "exp_001-d_001.ft2")  # 0.2.88: load the peak table explicitly
    assert panel.peak_table.rowCount() == 1

    # 0.2.199-patch29ar: a peak is added through the spectrum click entry (snap then
    # callback), no longer by appending a blank row directly
    panel._on_manual_peak_added({"H_shift": 8.5, "N_shift": 117.0, "label": ""})
    assert panel.peak_table.rowCount() == 2
    panel.peak_table.item(1, 2).setText("7.5")  # H_shift is column 2, after Assignment
    panel.peak_table.item(1, 3).setText("118.0")  # N_shift at column 3
    panel._on_save_peaks()

    list_path = peaks / "exp_001-d_001.list"
    saved = list_path.read_text(encoding="utf-8")
    assert "7.5" in saved and "118.0" in saved
    runs = [run for run in manager.project.workflow_runs if run.workflow_ref == "manual_peaks"]
    assert runs and runs[-1].status == "success"
    assert len(panel.viewer._peaks) == 2

    panel.peak_table.selectRow(1)
    panel._on_delete_peak()
    assert panel.peak_table.rowCount() == 1
    panel._on_save_peaks()
    saved = list_path.read_text(encoding="utf-8")
    assert "7.5" not in saved and "G1" in saved
    panel.close()


def test_import_peaks_poky_2d_3d(tmp_path: Path) -> None:
    """Poky .list import: 2D N/H + 3D F1/F2/F3."""
    list_2d = tmp_path / "peaks.list"
    list_2d.write_text(
        "Assignment w1 w2 Data Height Volume\n?-?  115.0  8.0  0  100  0\n",
        encoding="utf-8",
    )
    peaks_2d = import_peaks_poky(list_2d)
    assert len(peaks_2d) == 1
    assert peaks_2d[0]["N_shift"] == 115.0
    assert peaks_2d[0]["H_shift"] == 8.0

    list_3d = tmp_path / "peaks3d.list"
    list_3d.write_text(
        "Assignment w1 w2 w3 Data Height Volume\n?-?-?  118.0  120.0  8.0  0  90  0\n",
        encoding="utf-8",
    )
    peaks_3d = import_peaks_poky(list_3d)
    assert len(peaks_3d) == 1
    assert peaks_3d[0]["F1_shift"] == 118.0
    assert peaks_3d[0]["F3_shift"] == 8.0
    assert peaks_3d[0]["Intensity"] == 90.0


def test_spectrum_panel_3d_columns_auto(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loading a 3D peak table switches the table columns to F1/F2/F3."""
    manager = _manager(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / "exp_001-d_001.ft3").write_bytes(b"x")
    peaks = manager.data_dir("exp_001", "d_001", "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / "exp_001-d_001.csv").write_text(
        "Peak_ID,F1_shift,F2_shift,F3_shift,Intensity,SN,label\n1,118.0,120.0,8.0,90,10,G1\n",
        encoding="utf-8",
    )
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    panel._load_peaks(spectra / "exp_001-d_001.ft3")  # 0.2.88: load the peak table explicitly
    assert panel.peak_table.rowCount() == 1
    assert "F1_shift" in panel._peak_keys
    assert panel.peak_table.horizontalHeaderItem(1).text() == "Assignment ✓"
    assert panel.peak_table.horizontalHeaderItem(2).text() == "F1_shift"
    panel.close()


def test_peak_table_assignment_column(tmp_path, qapp, monkeypatch) -> None:
    # 0.2.199-patch29at: the peak table has an Assignment column (label)
    manager = _manager(tmp_path, monkeypatch)
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
    panel._load_peaks(spectra / "exp_001-d_001.ft2")
    assert panel.peak_table.horizontalHeaderItem(1).text() == "Assignment ✓"
    # 0.2.199-patch29cr: item text cleared to avoid overlap; merged value read from the widget
    assert panel.peak_table.item(0, 1).text() == ""
    widget0 = panel.peak_table.cellWidget(0, 1)
    # 2D has two segments; a missing segment is filled with ?
    assert widget0 is not None and widget0.merged_text() == "G1-?"
    panel.close()


def test_peak_modes_mutually_exclusive(tmp_path, qapp, monkeypatch) -> None:
    # 0.2.199-patch29at: Select / Add peak / 1D are mutually exclusive
    manager = _manager(tmp_path, monkeypatch)
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    panel.select_peaks_button.setChecked(True)
    assert not panel.add_peak_button.isChecked()
    assert not panel.viewer.show_1d_button.isChecked()
    assert panel.viewer._box_select_enabled
    panel.add_peak_button.setChecked(True)
    assert not panel.select_peaks_button.isChecked()
    assert not panel.viewer._box_select_enabled
    panel.viewer.show_1d_button.setChecked(True)
    assert not panel.add_peak_button.isChecked()
    assert not panel.select_peaks_button.isChecked()
    panel.close()


def test_peak_size_spin_controls_marker(tmp_path, qapp, monkeypatch) -> None:
    # 0.2.199-patch29az: the peak-marker spin box adjusts the viewer marker size
    manager = _manager(tmp_path, monkeypatch)
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert panel.peak_size_spin.isEnabled()
    panel.peak_size_spin.setValue(15.0)
    assert panel.viewer._peak_size == 15.0
    panel.close()


def test_delete_button_enabled_when_peaks_exist(tmp_path, qapp, monkeypatch) -> None:
    # 0.2.199-patch29ba: automatic and manual peaks are both deletable --
    # the delete button is enabled whenever a peak table exists
    manager = _manager(tmp_path, monkeypatch)
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
    assert not panel.delete_peak_button.isEnabled()  # disabled when there is no peak table
    panel._load_peaks(spectra / "exp_001-d_001.ft2")
    assert panel.delete_peak_button.isEnabled()  # automatic peaks are deletable
    panel._on_manual_peak_added({"H_shift": 8.5, "N_shift": 117.0, "label": ""})
    assert panel.delete_peak_button.isEnabled()  # manual peaks are deletable
    panel.close()


def test_1d_mode_hides_peak_ui(tmp_path, qapp, monkeypatch) -> None:
    # 0.2.199-patch29bd: turning 1D on hides the peak toolbar / peak table / peak
    # info; turning it off restores them
    import numpy as np

    from viewer.spectrum import Spectrum, SpectrumAxis

    manager = _manager(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / "exp_001-d_001.ft2").write_bytes(b"x")
    panel = SpectrumPanel(manager)
    axis_x = SpectrumAxis(
        label="H",
        size=64,
        sw_hz=6000.0,
        obs_mhz=600.0,
        carrier_ppm=4.7,
        orig_hz=None,
    )
    axis_y = SpectrumAxis(
        label="N",
        size=64,
        sw_hz=2189.0,
        obs_mhz=60.8,
        carrier_ppm=118.0,
        orig_hz=None,
    )
    panel.set_context("exp_001", "d_001")
    panel.viewer.add_spectrum(Spectrum(np.zeros((64, 64)), [axis_y, axis_x]))
    panel.refresh()
    assert panel.peak_toolbar_widget.isVisibleTo(panel)
    assert panel.peak_table.isVisibleTo(panel)
    panel.viewer.show_1d_button.setChecked(True)
    assert panel._viewer_1d_active
    assert not panel.peak_toolbar_widget.isVisibleTo(panel)
    assert not panel.peak_table.isVisibleTo(panel)
    assert not panel.viewer.peak_label.isVisibleTo(panel)
    panel.viewer.show_1d_button.setChecked(False)
    assert not panel._viewer_1d_active
    assert panel.peak_toolbar_widget.isVisibleTo(panel)
    assert panel.peak_table.isVisibleTo(panel)
    panel.close()


def test_box_select_no_flash_table_click_flashes(tmp_path, qapp, monkeypatch) -> None:
    """0.2.199-patch29bo: box-select syncing does not flash; only a table click does."""
    import numpy as np

    from viewer.spectrum import Spectrum, SpectrumAxis

    manager = _manager(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / "exp_001-d_001.ft2").write_bytes(b"x")
    peaks = manager.data_dir("exp_001", "d_001", "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / "exp_001-d_001.list").write_text(
        "Assignment w1 w2 Data Height Volume\n"
        "G1  115.000  8.000  0  100  0\n"
        "G2  112.000  8.500  0  90  0\n",
        encoding="utf-8",
    )
    panel = SpectrumPanel(manager)
    axis_x = SpectrumAxis(
        label="H",
        size=64,
        sw_hz=6000.0,
        obs_mhz=600.0,
        carrier_ppm=4.7,
        orig_hz=None,
    )
    axis_y = SpectrumAxis(
        label="N",
        size=64,
        sw_hz=2189.0,
        obs_mhz=60.8,
        carrier_ppm=118.0,
        orig_hz=None,
    )
    panel.set_context("exp_001", "d_001")
    panel.viewer.add_spectrum(Spectrum(np.zeros((64, 64)), [axis_y, axis_x]))
    panel._load_peaks(spectra / "exp_001-d_001.ft2")
    panel.viewer._clear_flash()
    # box select: syncing the peak table multi-selection must not flash
    panel._on_peaks_box_selected([0])
    assert panel.viewer._flash_item is None
    # peak-table click (programmatic selectRow, equivalent to a user click): flash
    panel.peak_table.selectRow(1)
    assert panel.viewer._flash_item is not None
    panel.viewer._clear_flash()
    panel.close()


def test_click_already_selected_peak_row_flashes(tmp_path, qapp, monkeypatch) -> None:
    """0.2.199-patch29cm: clicking an already selected peak-table row also flashes and
    localizes it (selectionChanged does not fire on a selected row, so cellClicked handles it)."""
    import numpy as np

    from viewer.spectrum import Spectrum, SpectrumAxis

    manager = _manager(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / "exp_001-d_001.ft2").write_bytes(b"x")
    peaks = manager.data_dir("exp_001", "d_001", "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / "exp_001-d_001.list").write_text(
        "Assignment w1 w2 Data Height Volume\n"
        "G1  115.000  8.000  0  100  0\n"
        "G2  112.000  8.500  0  90  0\n",
        encoding="utf-8",
    )
    panel = SpectrumPanel(manager)
    axis_x = SpectrumAxis(
        label="H",
        size=64,
        sw_hz=6000.0,
        obs_mhz=600.0,
        carrier_ppm=4.7,
        orig_hz=None,
    )
    axis_y = SpectrumAxis(
        label="N",
        size=64,
        sw_hz=2189.0,
        obs_mhz=60.8,
        carrier_ppm=118.0,
        orig_hz=None,
    )
    panel.set_context("exp_001", "d_001")
    panel.viewer.add_spectrum(Spectrum(np.zeros((64, 64)), [axis_y, axis_x]))
    panel._load_peaks(spectra / "exp_001-d_001.ft2")
    panel.viewer._clear_flash()
    # first click selects the row → flash
    panel.peak_table.selectRow(1)
    assert panel.viewer._flash_item is not None
    panel.viewer._clear_flash()
    # already selected row clicked again (cellClicked) → still flashes
    panel.peak_table.cellClicked.emit(1, 0)
    assert panel.viewer._flash_item is not None
    panel.viewer._clear_flash()
    panel.close()


def test_edit_assignment_applies_immediately(tmp_path, qapp, monkeypatch) -> None:
    """0.2.199-patch29cp: the Assignment column is a fixed hyphen plus per-segment
    inputs (default ?); edits apply to the on-spectrum labels at once and are
    normalized segment by segment per Poky."""
    import numpy as np

    from viewer.spectrum import Spectrum, SpectrumAxis

    manager = _manager(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / "exp_001-d_001.ft2").write_bytes(b"x")
    peaks = manager.data_dir("exp_001", "d_001", "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / "exp_001-d_001.list").write_text(
        "Assignment w1 w2 Data Height Volume\n"
        "G1  115.000  8.000  0  100  0\n"
        "G2  112.000  8.500  0  90  0\n",
        encoding="utf-8",
    )
    panel = SpectrumPanel(manager)
    axis_x = SpectrumAxis(
        label="H",
        size=64,
        sw_hz=6000.0,
        obs_mhz=600.0,
        carrier_ppm=4.7,
        orig_hz=None,
    )
    axis_y = SpectrumAxis(
        label="N",
        size=64,
        sw_hz=2189.0,
        obs_mhz=60.8,
        carrier_ppm=118.0,
        orig_hz=None,
    )
    panel.set_context("exp_001", "d_001")
    panel.viewer.add_spectrum(Spectrum(np.zeros((64, 64)), [axis_y, axis_x]))
    panel._load_peaks(spectra / "exp_001-d_001.ft2")
    # cell = fixed hyphen + per-segment inputs (2D has two segments; an
    # unassigned one shows the ? placeholder)
    widget = panel.peak_table.cellWidget(0, 1)
    assert widget is not None and len(widget.lines) == 2
    assert widget.lines[0].text() == "G1"  # first segment of the old label kept
    assert widget.lines[1].text() == ""  # default segment has no real text
    assert widget.lines[1].placeholderText() == "?"  # placeholder shows ?
    assert panel.peak_table.item(0, 1).text() == ""  # item text cleared to avoid overlap
    assert widget.merged_text() == "G1-?"
    # second row: typing into the placeholder box replaces it, no "?5" appended
    w2 = panel.peak_table.cellWidget(1, 1)
    assert w2 is not None and w2.lines[0].text() == "G2"
    assert w2.lines[1].text() == "" and w2.lines[1].placeholderText() == "?"
    from qtcompat.QtTest import QTest

    w2.lines[1].setFocus()
    QTest.keyClicks(w2.lines[1], "5")
    qapp.processEvents()
    assert w2.lines[1].text() == "5"  # no "?5" appended
    assert w2.merged_text() == "G2-5"
    # first row edit: normalized per segment and applied at once
    widget.lines[0].setText("g1h")
    widget.lines[1].setText("g1n")
    qapp.processEvents()
    assert widget.merged_text() == "G1H-G1N"  # Poky 2D, two segments
    assert panel.peak_table.item(0, 1).text() == ""  # item text stays cleared
    assert panel.viewer._peaks[0]["label"] == "G1H-G1N"  # applies at once
    labels = panel.viewer._label_overlay._collect_labels()
    assert any(text == "G1H-G1N" for _xi, _yi, text in labels)
    panel.close()


def test_assignment_header_toggles_labels(tmp_path, qapp, monkeypatch) -> None:
    # 0.2.199-patch29bf: clicking the Assignment column header toggles the on-spectrum labels
    import numpy as np

    from viewer.spectrum import Spectrum, SpectrumAxis

    manager = _manager(tmp_path, monkeypatch)
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
    axis_x = SpectrumAxis(
        label="H",
        size=64,
        sw_hz=6000.0,
        obs_mhz=600.0,
        carrier_ppm=4.7,
        orig_hz=None,
    )
    axis_y = SpectrumAxis(
        label="N",
        size=64,
        sw_hz=2189.0,
        obs_mhz=60.8,
        carrier_ppm=118.0,
        orig_hz=None,
    )
    panel.set_context("exp_001", "d_001")
    panel.viewer.add_spectrum(Spectrum(np.zeros((64, 64)), [axis_y, axis_x]))
    panel._load_peaks(spectra / "exp_001-d_001.ft2")
    assert panel.viewer._show_peak_labels
    assert panel.viewer._label_overlay.visible_label_count() == 1
    panel._on_peak_header_clicked(1)
    assert not panel.viewer._show_peak_labels
    assert panel.viewer._label_overlay.visible_label_count() == 0
    panel._on_peak_header_clicked(1)
    assert panel.viewer._show_peak_labels
    assert panel.viewer._label_overlay.visible_label_count() == 1
    panel.close()
