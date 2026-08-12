"""人工处理入口 UI 骨架测试:参数表格 / 脚本编辑器 / 主窗口接线(offscreen)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from core.project import ProjectManager
from gui.dialogs import ParameterTableDialog, ScriptEditorDialog
from gui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _manager(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    manager.add_experiment("/sampleD", title="HSQC")
    manager.save()
    return manager


def test_parameter_table_dialog_structure(qapp: QApplication) -> None:
    dialog = ParameterTableDialog(None, "HSQC (exp_001)")
    assert dialog.table.rowCount() == 2  # fid / process 骨架阶段
    assert dialog.table.item(0, 0).text() == "fid"
    data = dialog.result_data()
    assert data["stages"][0]["id"] == "fid"
    dialog.accept()
    dialog.close()


def test_script_editor_dialog_content(qapp: QApplication) -> None:
    dialog = ScriptEditorDialog(None, "HSQC (exp_001)", content="nmrPipe -in test.fid")
    assert "nmrPipe" in dialog.editor.toPlainText()
    assert dialog.result_data() == {"content": "nmrPipe -in test.fid"}
    dialog.close()


def test_main_window_open_manual_dialog(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages: list[str] = []
    monkeypatch.setattr(
        "gui.main_window.InfoDialog.show_info",
        staticmethod(lambda parent, title, text_: messages.append(text_)),
    )
    manager = _manager(tmp_path)
    window = MainWindow(manager=manager)
    window.project_tree.select_experiment("exp_001")
    called: list[str] = []
    monkeypatch.setattr("gui.main_window.ParameterTableDialog.exec", lambda self: 1)
    monkeypatch.setattr("gui.main_window.ScriptEditorDialog.exec", lambda self: 1)
    monkeypatch.setattr(
        window.controller, "manual_param_table", lambda entry: called.append(entry.id)
    )
    monkeypatch.setattr(
        window.controller, "manual_script_editor", lambda entry: called.append(entry.id)
    )
    window._open_manual_dialog("process")
    assert called == ["exp_001"]  # 参数表格路径
    window._open_manual_dialog("peaks")
    assert called == ["exp_001", "exp_001"]  # 脚本编辑器路径
    window.close()


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
