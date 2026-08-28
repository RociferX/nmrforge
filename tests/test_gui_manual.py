"""人工处理入口测试:脚本编辑器 / 主窗口接线 + 峰表编辑回写(offscreen)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

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
    """人工入口按步骤打开对应编辑器/提示(不阻塞,经假控制器)。"""
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
    assert "fid.com" in shown  # 0.2.192:非模态 show 打开,不 exec 锁定主界面

    window._open_manual_dialog("spectrum")
    assert ("manual_scripts", "exp_001", "d_001", None) in controller.calls  # 打开脚本编辑器
    assert "process.com" in shown

    window._open_manual_dialog("peaks")
    assert any("峰表" in message for message in messages)

    window._open_manual_dialog("analysis")
    assert any("分析" in message for message in messages)
    window.close()


def test_script_editor_single_instance_per_data_step(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同数据同步骤只允许一个脚本编辑器;不同步骤可并存(0.2.193)。"""
    manager = _manager(tmp_path, monkeypatch)
    controller = FakeManualController()
    window = MainWindow(manager=manager, controller=controller)
    window.project_tree.select_experiment("exp_001")

    window._open_manual_dialog("spectrum")
    assert len(window._script_editors) == 1
    assert controller.calls.count(("manual_scripts", "exp_001", "d_001", None)) == 1

    # 再开同一数据同一步骤:复用,不新建
    window._open_manual_dialog("spectrum")
    assert len(window._script_editors) == 1
    assert controller.calls.count(("manual_scripts", "exp_001", "d_001", None)) == 1

    # fid 与 spectrum 是不同步骤,可并存
    window._open_manual_dialog("fid")
    assert len(window._script_editors) == 2
    assert ("manual_fid_com", "exp_001", "d_001") in controller.calls

    # 关闭 spectrum 编辑器后去重键释放,可重新打开
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


def test_script_editor_save_writes_script_file(
    tmp_path: Path, qapp: QApplication
) -> None:
    """「保存」立即写回数据目录并关闭(0.2.192:之前保存不落盘)。"""
    from PyQt6.QtWidgets import QDialog

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
    """「运行」先保存,发出内容并自动关闭(0.2.192)。"""
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
    assert not dialog.isVisible()  # 运行后自动关闭
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
    window._manual_script_editor_menu()
    assert messages and "选择" in messages[0]
    window.close()


def test_spectrum_panel_peak_add_edit_delete_save(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """峰表加/改/删/存:写回 Poky .list + 登记 manual_peaks + viewer 刷新。"""
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
    panel._load_peaks(spectra / "exp_001-d_001.ft2")  # 0.2.88:显式加载峰表
    assert panel.peak_table.rowCount() == 1

    # 0.2.199-补29ar:加峰走点击谱图入口(吸附后回调),不再直接追加空行
    panel._on_manual_peak_added({"H_shift": 8.5, "N_shift": 117.0, "label": ""})
    assert panel.peak_table.rowCount() == 2
    panel.peak_table.item(1, 2).setText("7.5")  # Assignment 列后 H_shift 在 2
    panel.peak_table.item(1, 3).setText("118.0")  # N_shift 在 3
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
    panel._load_peaks(spectra / "exp_001-d_001.ft3")  # 0.2.88:显式加载峰表
    assert panel.peak_table.rowCount() == 1
    assert "F1_shift" in panel._peak_keys
    assert panel.peak_table.horizontalHeaderItem(1).text() == "Assignment"
    assert panel.peak_table.horizontalHeaderItem(2).text() == "F1_shift"
    panel.close()



def test_peak_table_assignment_column(tmp_path, qapp, monkeypatch) -> None:
    # 0.2.199-补29at:峰表含 Assignment 列(label)
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
    assert panel.peak_table.horizontalHeaderItem(1).text() == 'Assignment'
    assert panel.peak_table.item(0, 1).text() == 'G1'
    panel.close()


def test_peak_modes_mutually_exclusive(tmp_path, qapp, monkeypatch) -> None:
    # 0.2.199-补29at:选择/Add peak/1D 互斥
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
    # 0.2.199-补29az:峰标记输入框调节 viewer 峰标记大小
    manager = _manager(tmp_path, monkeypatch)
    panel = SpectrumPanel(manager)
    panel.set_context('exp_001', 'd_001')
    assert panel.peak_size_spin.isEnabled()
    panel.peak_size_spin.setValue(15.0)
    assert panel.viewer._peak_size == 15.0
    panel.close()



def test_delete_button_enabled_when_peaks_exist(tmp_path, qapp, monkeypatch) -> None:
    # 0.2.199-补29ba:自动/手动峰均可删除——有峰表即启用删除按钮
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
    assert not panel.delete_peak_button.isEnabled()  # 无峰表时禁用
    panel._load_peaks(spectra / 'exp_001-d_001.ft2')
    assert panel.delete_peak_button.isEnabled()  # 自动峰可删
    panel._on_manual_peak_added({'H_shift': 8.5, 'N_shift': 117.0, 'label': ''})
    assert panel.delete_peak_button.isEnabled()  # 手动峰可删
    panel.close()
