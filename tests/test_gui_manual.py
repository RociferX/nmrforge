"""Manual processing entrance test: script editor/main window wiring + peak table editing and
writeback (offscreen)."""

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


def _manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch | None = None
) -> ProjectManager:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    manager = ProjectManager.create_project(ws / "proj", "demo")
    manager.add_experiment("/sampleD", title="HSQC")
    manager.save()
    if monkeypatch is not None:
        monkeypatch.setattr(
            "gui.main_window.WorkspaceManager", lambda: _TempWorkspace(ws)
        )
        monkeypatch.setattr(
            "core.workspace.WorkspaceManager", lambda *a, **k: _TempWorkspace(ws)
        )
    return manager


class _TempWorkspace:
    def __init__(self, root) -> None:
        self.root = Path(root)

    def list_projects(self):
        return sorted(
            p
            for p in self.root.iterdir()
            if p.is_dir() and (p / "project.json").is_file()
        )

    def ensure(self):
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root


class FakeManualController:
    """Manually handle fake controllers: record calls and return synchronously."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
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

    def run_manual_fid_com(
        self, data, content, exp_id=None, data_id=None, progress=None
    ) -> str:
        self.calls.append(("run_manual_fid_com", content))
        return "/tmp/test.fid"

    def run_manual_spectrum(
        self, data, scripts, exp_id=None, data_id=None, progress=None
    ) -> str:
        self.calls.append(("run_manual_spectrum", scripts))
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
    """Manual entrance opens the corresponding editor step by step/hint (not blocking, via fake
    controller)."""
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
    assert "fid.com" in shown  # 0.2.192:Non-modal show opens, no exec locks the main interface.

    window._open_manual_dialog("spectrum")
    assert ("manual_scripts", "exp_001", "d_001", None) in controller.calls  # Open script editor.
    assert "process.com" in shown

    window._open_manual_dialog("peaks")
    assert any("peak table" in message for message in messages)
    window.close()


def test_script_editor_single_instance_per_data_step(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only one script editor is allowed for the same data and the same step; different steps can
    coexist (0.2.193)."""
    manager = _manager(tmp_path, monkeypatch)
    controller = FakeManualController()
    window = MainWindow(manager=manager, controller=controller)
    window.project_tree.select_experiment("exp_001")

    window._open_manual_dialog("spectrum")
    assert len(window._script_editors) == 1
    assert controller.calls.count(("manual_scripts", "exp_001", "d_001", None)) == 1

    # Open the same data again with the same steps: reuse, no new creation.
    window._open_manual_dialog("spectrum")
    assert len(window._script_editors) == 1
    assert controller.calls.count(("manual_scripts", "exp_001", "d_001", None)) == 1

    # Fid and spectrum are different steps and can coexist.
    window._open_manual_dialog("fid")
    assert len(window._script_editors) == 2
    assert ("manual_fid_com", "exp_001", "d_001") in controller.calls

    # After closing the spectrum editor, release the deduplication key and reopen it.
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
    """Script editor "Run" -> run_manual_spectrum (wired via the main window)."""
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager = _manager(tmp_path, monkeypatch)
    controller = FakeManualController()
    window = MainWindow(manager=manager, controller=controller)
    entry = manager.project.experiment("exp_001")
    data_node = entry.data[0]
    dialog = ScriptEditorDialog(None, "x", script_name="process.com", content="")
    window._wire_script_run(dialog, data_node, "exp_001", "d_001", "process.com")
    dialog.run_requested.emit("new content")
    assert ("run_manual_spectrum", {"process.com": "new content"}) in controller.calls
    window.close()
    dialog.close()


def test_script_editor_save_writes_script_file(
    tmp_path: Path, qapp: QApplication
) -> None:
    """"Save" immediately writes back the data directory and closes it (0.2.192: the previous save
    will not be saved)."""
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


def test_script_editor_run_saves_emits_and_closes(
    tmp_path: Path, qapp: QApplication
) -> None:
    """"Run" first saves, sends the content and automatically closes (0.2.192)."""
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
    assert not dialog.isVisible()  # Automatically close after running.
    dialog.close()


def test_spectrum_panel_peak_add_edit_delete_save(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Peak table plus/change/delete/live: Write back Poky.list + register manual_peaks + viewer
    refresh."""
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
        "Assignment w1 w2 Data Height Volume\n"
        "G1  115.000  8.000  0  100  0\n",
        encoding="utf-8",
    )
    controller = ProcessingController(manager)
    panel = SpectrumPanel(manager, controller=controller)
    panel.set_context("exp_001", "d_001")
    panel._load_peaks(spectra / "exp_001-d_001.ft2")  # 0.2.88:Explicitly load peak table.
    assert panel.peak_table.rowCount() == 1

    # 0.2.199-patch29ar: Add peak and click the spectrum entrance (callback after adsorption), no
    # longer directly append blank lines.
    panel._on_manual_peak_added({"H_shift": 8.5, "N_shift": 117.0, "label": ""})
    assert panel.peak_table.rowCount() == 2
    panel.peak_table.item(1, 2).setText("7.5")  # Assignment Column after H_shift in 2.
    panel.peak_table.item(1, 3).setText("118.0")  # N_shift In 3.
    panel._on_save_peaks()

    list_path = peaks / "exp_001-d_001.list"
    saved = list_path.read_text(encoding="utf-8")
    assert "7.5" in saved and "118.0" in saved
    runs = [
        run
        for run in manager.project.workflow_runs
        if run.workflow_ref == "manual_peaks"
    ]
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
    """Poky.list import:2D N/H + 3D F1/F2/F3."""
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
    """When the 3D peak table is loaded, the table columns automatically switch to F1/F2/F3."""
    manager = _manager(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / "exp_001-d_001.ft3").write_bytes(b"x")
    peaks = manager.data_dir("exp_001", "d_001", "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / "exp_001-d_001.csv").write_text(
        "Peak_ID,F1_shift,F2_shift,F3_shift,Intensity,SN,label\n"
        "1,118.0,120.0,8.0,90,10,G1\n",
        encoding="utf-8",
    )
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    panel._load_peaks(spectra / "exp_001-d_001.ft3")  # 0.2.88:Explicitly load peak table.
    assert panel.peak_table.rowCount() == 1
    assert "F1_shift" in panel._peak_keys
    assert panel.peak_table.horizontalHeaderItem(1).text() == "Assignment ✓"
    assert panel.peak_table.horizontalHeaderItem(2).text() == "F1_shift"
    panel.close()



def test_peak_table_assignment_column(tmp_path, qapp, monkeypatch) -> None:
    # 0.2.199-patch29at: The peak table contains the Assignment column (label).
    manager = _manager(tmp_path, monkeypatch)
    spectra = manager.data_dir('exp_001', 'd_001', 'spectra')
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / 'exp_001-d_001.ft2').write_bytes(b'x')
    peaks = manager.data_dir('exp_001', 'd_001', 'peaks')
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / 'exp_001-d_001.list').write_text(
        'Assignment w1 w2 Data Height Volume\nG1  115.000  8.000  0  100  0\n',
        encoding='utf-8',
    )
    panel = SpectrumPanel(manager)
    panel.set_context('exp_001', 'd_001')
    panel._load_peaks(spectra / 'exp_001-d_001.ft2')
    assert panel.peak_table.horizontalHeaderItem(1).text() == 'Assignment ✓'
    # 0.2.199-patch29cr: The item text is cleared and does not overlap; the merged value is read by
    # the component.
    assert panel.peak_table.item(0, 1).text() == ''
    widget0 = panel.peak_table.cellWidget(0, 1)
    # Two paragraphs, make up for the missing paragraph?
    assert widget0 is not None and widget0.merged_text() == 'G1-?'
    panel.close()


def test_peak_modes_mutually_exclusive(tmp_path, qapp, monkeypatch) -> None:
    # 0.2.199-patch29at:choose/Add peak/1D Mutually exclusive.
    manager = _manager(tmp_path, monkeypatch)
    panel = SpectrumPanel(manager)
    panel.set_context('exp_001', 'd_001')
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
    # 0.2.199-patch29az: Peak mark input box adjusts viewer peak mark size.
    manager = _manager(tmp_path, monkeypatch)
    panel = SpectrumPanel(manager)
    panel.set_context('exp_001', 'd_001')
    assert panel.peak_size_spin.isEnabled()
    panel.peak_size_spin.setValue(15.0)
    assert panel.viewer._peak_size == 15.0
    panel.close()



def test_delete_button_enabled_when_peaks_exist(tmp_path, qapp, monkeypatch) -> None:
    # 0.2.199-patch29ba:automatic/Manual peaks can be deleted -- Enable the delete button when there
    # is a peak table.
    manager = _manager(tmp_path, monkeypatch)
    spectra = manager.data_dir('exp_001', 'd_001', 'spectra')
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / 'exp_001-d_001.ft2').write_bytes(b'x')
    peaks = manager.data_dir('exp_001', 'd_001', 'peaks')
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / 'exp_001-d_001.list').write_text(
        'Assignment w1 w2 Data Height Volume\nG1  115.000  8.000  0  100  0\n',
        encoding='utf-8',
    )
    panel = SpectrumPanel(manager)
    panel.set_context('exp_001', 'd_001')
    assert not panel.delete_peak_button.isEnabled()  # Disabled when there is no peak table.
    panel._load_peaks(spectra / 'exp_001-d_001.ft2')
    assert panel.delete_peak_button.isEnabled()  # Automatic peak can be deleted.
    panel._on_manual_peak_added({'H_shift': 8.5, 'N_shift': 117.0, 'label': ''})
    assert panel.delete_peak_button.isEnabled()  # Peaks can be deleted manually.
    panel.close()



def test_1d_mode_hides_peak_ui(tmp_path, qapp, monkeypatch) -> None:
    # 0.2.199-patch29bd: Enable 1D Hide peak toolbar/peak table/Peak information; Disable recovery.
    import numpy as np

    from viewer.spectrum import Spectrum, SpectrumAxis

    manager = _manager(tmp_path, monkeypatch)
    spectra = manager.data_dir('exp_001', 'd_001', 'spectra')
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / 'exp_001-d_001.ft2').write_bytes(b'x')
    panel = SpectrumPanel(manager)
    axis_x = SpectrumAxis(
        label='H', size=64, sw_hz=6000.0, obs_mhz=600.0,
        carrier_ppm=4.7, orig_hz=0.0,
    )
    axis_y = SpectrumAxis(
        label='N', size=64, sw_hz=2189.0, obs_mhz=60.8,
        carrier_ppm=118.0, orig_hz=0.0,
    )
    panel.viewer.add_spectrum(Spectrum(np.zeros((64, 64)), [axis_y, axis_x]))
    panel.set_context('exp_001', 'd_001')
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



def test_box_select_no_flash_table_click_flashes(
    tmp_path, qapp, monkeypatch
) -> None:
    """0.2.199-patch29bo: The linked peak table does not trigger flashing when frame-selected; it
    flashes only when the peak table is clicked."""
    import numpy as np

    from viewer.spectrum import Spectrum, SpectrumAxis

    manager = _manager(tmp_path, monkeypatch)
    spectra = manager.data_dir('exp_001', 'd_001', 'spectra')
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / 'exp_001-d_001.ft2').write_bytes(b'x')
    peaks = manager.data_dir('exp_001', 'd_001', 'peaks')
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / 'exp_001-d_001.list').write_text(
        'Assignment w1 w2 Data Height Volume\n'
        'G1  115.000  8.000  0  100  0\n'
        'G2  112.000  8.500  0  90  0\n',
        encoding='utf-8',
    )
    panel = SpectrumPanel(manager)
    axis_x = SpectrumAxis(
        label='H', size=64, sw_hz=6000.0, obs_mhz=600.0,
        carrier_ppm=4.7, orig_hz=0.0,
    )
    axis_y = SpectrumAxis(
        label='N', size=64, sw_hz=2189.0, obs_mhz=60.8,
        carrier_ppm=118.0, orig_hz=0.0,
    )
    panel.viewer.add_spectrum(Spectrum(np.zeros((64, 64)), [axis_y, axis_x]))
    panel.set_context('exp_001', 'd_001')
    panel._load_peaks(spectra / 'exp_001-d_001.ft2')
    panel.viewer._clear_flash()
    # Frame selection: Linked peak table multiple selection, should not flash.
    panel._on_peaks_box_selected([0])
    assert panel.viewer._flash_item is None
    # Peak table click (programmed selectRow equivalent user click): triggers flashing.
    panel.peak_table.selectRow(1)
    assert panel.viewer._flash_item is not None
    panel.viewer._clear_flash()
    panel.close()


def test_click_already_selected_peak_row_flashes(
    tmp_path, qapp, monkeypatch
) -> None:
    """0.2.199-patch29cm: Clicking the selected row in the peak table again will trigger flickering
    positioning (selectionChanged is not triggered on the selected row, and cellClicked
    processing is added)."""
    import numpy as np

    from viewer.spectrum import Spectrum, SpectrumAxis

    manager = _manager(tmp_path, monkeypatch)
    spectra = manager.data_dir('exp_001', 'd_001', 'spectra')
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / 'exp_001-d_001.ft2').write_bytes(b'x')
    peaks = manager.data_dir('exp_001', 'd_001', 'peaks')
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / 'exp_001-d_001.list').write_text(
        'Assignment w1 w2 Data Height Volume\n'
        'G1  115.000  8.000  0  100  0\n'
        'G2  112.000  8.500  0  90  0\n',
        encoding='utf-8',
    )
    panel = SpectrumPanel(manager)
    axis_x = SpectrumAxis(
        label='H', size=64, sw_hz=6000.0, obs_mhz=600.0,
        carrier_ppm=4.7, orig_hz=0.0,
    )
    axis_y = SpectrumAxis(
        label='N', size=64, sw_hz=2189.0, obs_mhz=60.8,
        carrier_ppm=118.0, orig_hz=0.0,
    )
    panel.viewer.add_spectrum(Spectrum(np.zeros((64, 64)), [axis_y, axis_x]))
    panel.set_context('exp_001', 'd_001')
    panel._load_peaks(spectra / 'exp_001-d_001.ft2')
    panel.viewer._clear_flash()
    # First click to select row -> flashing.
    panel.peak_table.selectRow(1)
    assert panel.viewer._flash_item is not None
    panel.viewer._clear_flash()
    # The selected row is clicked again (cellClicked) -> Still flashing.
    panel.peak_table.cellClicked.emit(1, 0)
    assert panel.viewer._flash_item is not None
    panel.viewer._clear_flash()
    panel.close()


def test_edit_assignment_applies_immediately(
    tmp_path, qapp, monkeypatch
) -> None:
    """0.2.199-patch29cp: Assignment column fixed hyphen + segment input box (default ?), the edit
    takes effect immediately to the label on the diagram and is normalized segment by segment
    according to Poky."""
    import numpy as np

    from viewer.spectrum import Spectrum, SpectrumAxis

    manager = _manager(tmp_path, monkeypatch)
    spectra = manager.data_dir('exp_001', 'd_001', 'spectra')
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / 'exp_001-d_001.ft2').write_bytes(b'x')
    peaks = manager.data_dir('exp_001', 'd_001', 'peaks')
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / 'exp_001-d_001.list').write_text(
        'Assignment w1 w2 Data Height Volume\n'
        'G1  115.000  8.000  0  100  0\n'
        'G2  112.000  8.500  0  90  0\n',
        encoding='utf-8',
    )
    panel = SpectrumPanel(manager)
    axis_x = SpectrumAxis(
        label='H', size=64, sw_hz=6000.0, obs_mhz=600.0,
        carrier_ppm=4.7, orig_hz=0.0,
    )
    axis_y = SpectrumAxis(
        label='N', size=64, sw_hz=2189.0, obs_mhz=60.8,
        carrier_ppm=118.0, orig_hz=0.0,
    )
    panel.viewer.add_spectrum(Spectrum(np.zeros((64, 64)), [axis_y, axis_x]))
    panel.set_context('exp_001', 'd_001')
    panel._load_peaks(spectra / 'exp_001-d_001.ft2')
    # Cell = fixed hyphen + segment input box (2D two segments, unspecified segments are
    # placeholders?).
    widget = panel.peak_table.cellWidget(0, 1)
    assert widget is not None and len(widget.lines) == 2
    assert widget.lines[0].text() == 'G1'  # The first paragraph of the old label is retained.
    assert widget.lines[1].text() == ''              # Default section has no real text.
    assert widget.lines[1].placeholderText() == '?'  # Placeholder display?
    assert panel.peak_table.item(0, 1).text() == ''  # item Text is cleared without overlapping.
    assert widget.merged_text() == 'G1-?'
    # Second line: Enter the placeholder box and replace it without appending ?5.
    w2 = panel.peak_table.cellWidget(1, 1)
    assert w2 is not None and w2.lines[0].text() == 'G2'
    assert w2.lines[1].text() == '' and w2.lines[1].placeholderText() == '?'
    from qtcompat.QtTest import QTest
    w2.lines[1].setFocus()
    QTest.keyClicks(w2.lines[1], '5')
    qapp.processEvents()
    assert w2.lines[1].text() == '5'                # Do not append ?5.
    assert w2.merged_text() == 'G2-5'
    # First line edit: take effect immediately after normalisation section by section.
    widget.lines[0].setText('g1h')
    widget.lines[1].setText('g1n')
    qapp.processEvents()
    assert widget.merged_text() == 'G1H-G1N'  # Poky 2D Two paragraphs.
    assert panel.peak_table.item(0, 1).text() == ''  # item Text remains empty.
    assert panel.viewer._peaks[0]['label'] == 'G1H-G1N'  # Effective immediately.
    labels = panel.viewer._label_overlay._collect_labels()
    assert any(text == 'G1H-G1N' for _xi, _yi, text in labels)
    panel.close()


def test_assignment_header_toggles_labels(tmp_path, qapp, monkeypatch) -> None:
    # 0.2.199-patch29bf: Click the Assignment column header to switch the assignment label on the
    # graph.
    import numpy as np

    from viewer.spectrum import Spectrum, SpectrumAxis

    manager = _manager(tmp_path, monkeypatch)
    spectra = manager.data_dir('exp_001', 'd_001', 'spectra')
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / 'exp_001-d_001.ft2').write_bytes(b'x')
    peaks = manager.data_dir('exp_001', 'd_001', 'peaks')
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / 'exp_001-d_001.list').write_text(
        'Assignment w1 w2 Data Height Volume\nG1  115.000  8.000  0  100  0\n',
        encoding='utf-8',
    )
    panel = SpectrumPanel(manager)
    axis_x = SpectrumAxis(
        label='H', size=64, sw_hz=6000.0, obs_mhz=600.0,
        carrier_ppm=4.7, orig_hz=0.0,
    )
    axis_y = SpectrumAxis(
        label='N', size=64, sw_hz=2189.0, obs_mhz=60.8,
        carrier_ppm=118.0, orig_hz=0.0,
    )
    panel.viewer.add_spectrum(Spectrum(np.zeros((64, 64)), [axis_y, axis_x]))
    panel.set_context('exp_001', 'd_001')
    panel._load_peaks(spectra / 'exp_001-d_001.ft2')
    assert panel.viewer._show_peak_labels
    assert panel.viewer._label_overlay.visible_label_count() == 1
    panel._on_peak_header_clicked(1)
    assert not panel.viewer._show_peak_labels
    assert panel.viewer._label_overlay.visible_label_count() == 0
    panel._on_peak_header_clicked(1)
    assert panel.viewer._show_peak_labels
    assert panel.viewer._label_overlay.visible_label_count() == 1
    panel.close()
