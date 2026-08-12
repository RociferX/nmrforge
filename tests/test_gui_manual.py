"""人工处理入口测试:参数表格 / 脚本编辑器 / 主窗口接线 + 峰表编辑回写(offscreen)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from core.project import ProjectManager
from gui.dialogs import ParameterTableDialog, ScriptEditorDialog
from gui.main_window import MainWindow
from gui.peaks_io import import_peaks_poky
from gui.processing import ProcessingController
from gui.spectrum_panel import SpectrumPanel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


class SyncThread:
    """把后台线程变为同步执行,测试不依赖线程时序。"""

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
    """人工处理假控制器:记录调用并同步返回。"""

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

    def run_manual_fid_com(self, data, content, exp_id=None, data_id=None) -> str:
        self.calls.append(("run_manual_fid_com", content))
        return "/tmp/test.fid"

    def run_manual_spectrum(self, data, scripts, exp_id=None, data_id=None) -> str:
        self.calls.append(("run_manual_spectrum", scripts))
        return "/tmp/x.ft2"

    def param_schema(self) -> dict:
        return {"properties": {}, "default": {}}

    def save_peaks_manual(self, data, peaks, exp_id=None, data_id=None) -> str:
        self.calls.append(("save_peaks_manual", len(peaks), exp_id, data_id))
        return f"/tmp/{exp_id}-{data_id}.csv"


def test_parameter_table_dialog_structure(qapp: QApplication) -> None:
    """参数表格以 param_schema 填充:标量行 + stages 表。"""
    dialog = ParameterTableDialog(None, "HSQC (exp_001)")
    assert dialog.param_table.rowCount() == 8  # zero_fill/ext_lo/ext_hi/extract + sampling.*
    keys = [
        dialog.param_table.item(row, 0).text()
        for row in range(dialog.param_table.rowCount())
    ]
    assert "zero_fill" in keys
    assert "ext_lo" in keys and "ext_hi" in keys and "extract" in keys
    assert "sampling.ft_neg" in keys and "sampling.ft_alt" in keys
    data = dialog.result_data()
    assert data["zero_fill"] == 2
    assert data["ext_lo"] == "11.0"
    assert data["sampling"]["ft_alt"] is True
    dialog.close()


def test_parameter_table_dialog_edit_values(qapp: QApplication) -> None:
    """编辑标量行后 result_data 回读(布尔/数值转换)。"""
    dialog = ParameterTableDialog(None, "HSQC (exp_001)")
    for row in range(dialog.param_table.rowCount()):
        key = dialog.param_table.item(row, 0).text()
        if key == "ext_hi":
            dialog.param_table.item(row, 1).setText("8.5")
        elif key == "sampling.ft_neg":
            dialog.param_table.item(row, 1).setText("true")
        elif key == "zero_fill":
            dialog.param_table.item(row, 1).setText("4")
    data = dialog.result_data()
    assert data["ext_hi"] == "8.5"  # schema type=string 保持原样
    assert data["zero_fill"] == 4
    assert data["sampling"]["ft_neg"] is True
    dialog.close()


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
    """人工入口按步骤打开对应编辑器/提示(不阻塞,经假控制器)。"""
    messages: list[str] = []
    monkeypatch.setattr(
        "gui.main_window.InfoDialog.show_info",
        staticmethod(lambda parent, title, text_: messages.append(text_)),
    )
    monkeypatch.setattr("gui.main_window.ScriptEditorDialog.exec", lambda self: 0)
    monkeypatch.setattr("gui.main_window.ParameterTableDialog.exec", lambda self: 0)
    manager = _manager(tmp_path, monkeypatch)
    controller = FakeManualController()
    window = MainWindow(manager=manager, controller=controller)
    window.project_tree.select_experiment("exp_001")

    window._open_manual_dialog("fid")
    assert ("manual_fid_com", "exp_001", "d_001") in controller.calls

    window._open_manual_dialog("spectrum")
    assert ("manual_scripts", "exp_001", "d_001", None) not in controller.calls  # 未点渲染

    window._open_manual_dialog("peaks")
    assert any("峰表" in message for message in messages)

    window._open_manual_dialog("analysis")
    assert any("分析" in message for message in messages)
    window.close()


def test_render_scripts_opens_editor(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """参数渲染:manual_scripts(params) 打开脚本编辑器。"""
    monkeypatch.setattr("gui.main_window.ScriptEditorDialog.exec", lambda self: 0)
    manager = _manager(tmp_path, monkeypatch)
    controller = FakeManualController()
    window = MainWindow(manager=manager, controller=controller)
    window.project_tree.select_experiment("exp_001")
    entry = manager.project.experiment("exp_001")
    data_node = entry.data[0]
    window._render_scripts_and_edit(
        {"zero_fill": 4}, data_node, "exp_001", "d_001", "label"
    )
    assert ("manual_scripts", "exp_001", "d_001", {"zero_fill": 4}) in controller.calls
    window.close()


def test_script_run_wires_controller(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """脚本编辑器「运行」→ run_manual_spectrum(经主窗口接线)。"""
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


def test_manual_menu_actions_require_experiment(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages: list[str] = []
    monkeypatch.setattr(
        "gui.main_window.InfoDialog.show_info",
        staticmethod(lambda parent, title, text_: messages.append(text_)),
    )
    window = MainWindow()
    window._manual_param_table_menu()
    assert messages and "选择" in messages[0]
    window.close()


def test_spectrum_panel_peak_add_edit_delete_save(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """峰表加/改/删/存:写回 CSV + 登记 manual_peaks + viewer 刷新。"""
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
    (peaks / "exp_001-d_001.csv").write_text(
        "Peak_ID,H_shift,N_shift,Intensity,SN,label\n"
        "1,8.0,115.0,100,20,G1\n",
        encoding="utf-8",
    )
    controller = ProcessingController(manager)
    panel = SpectrumPanel(manager, controller=controller)
    panel.set_context("exp_001", "d_001")
    assert panel.peak_table.rowCount() == 1

    panel._on_add_peak()
    assert panel.peak_table.rowCount() == 2
    panel.peak_table.item(1, 1).setText("7.5")
    panel.peak_table.item(1, 2).setText("118.0")
    panel._on_save_peaks()

    csv_path = peaks / "exp_001-d_001.csv"
    saved = csv_path.read_text(encoding="utf-8")
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
    saved = csv_path.read_text(encoding="utf-8")
    assert "7.5" not in saved and "G1" in saved
    panel.close()


def test_import_peaks_poky_2d_3d(tmp_path: Path) -> None:
    """Poky .list 导入:2D N/H + 3D F1/F2/F3。"""
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
    """3D 峰表加载时表格列自动切换为 F1/F2/F3。"""
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
    assert panel.peak_table.rowCount() == 1
    assert "F1_shift" in panel._peak_keys
    assert panel.peak_table.horizontalHeaderItem(1).text() == "F1_shift"
    panel.close()
