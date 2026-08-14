"""样本/实验/数据三级注释功能测试:notes helper + 中间顶部注释条 + 菜单。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from core.project import ProjectManager
from gui.notes import (
    data_note,
    experiment_note,
    sample_note,
    set_data_note,
    set_experiment_note,
    set_sample_note,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _manager(tmp_path: Path) -> tuple[ProjectManager, str, str]:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    return manager, entry.id, data.id


def test_notes_helper_roundtrip(tmp_path: Path) -> None:
    """三级注释读写,manager.save() 后重新打开仍可读。"""
    manager, exp_id, data_id = _manager(tmp_path)
    assert sample_note(manager.project) == ""
    assert experiment_note(manager.project, exp_id) == ""
    assert data_note(manager.project, exp_id, data_id) == ""
    set_sample_note(manager.project, "GB1, 0.5 mM, PBS")
    set_experiment_note(manager.project, exp_id, "15N HSQC")
    set_data_note(manager.project, exp_id, data_id, "2026-08-13")
    assert sample_note(manager.project) == "GB1, 0.5 mM, PBS"
    assert experiment_note(manager.project, exp_id) == "15N HSQC"
    assert data_note(manager.project, exp_id, data_id) == "2026-08-13"
    manager.save()
    reopened = ProjectManager.open_project(tmp_path / "proj")
    assert sample_note(reopened.project) == "GB1, 0.5 mM, PBS"
    assert experiment_note(reopened.project, exp_id) == "15N HSQC"
    assert data_note(reopened.project, exp_id, data_id) == "2026-08-13"


def test_center_panel_notes_bar(tmp_path: Path, qapp: QApplication) -> None:
    """中间区域最上方按选中层级显示样本/实验/数据注释。"""
    manager, exp_id, data_id = _manager(tmp_path)
    set_sample_note(manager.project, "样本注释A")
    set_experiment_note(manager.project, exp_id, "实验注释B")
    set_data_note(manager.project, exp_id, data_id, "数据注释C")
    manager.save()
    from gui.center_panel import CenterPanel

    panel = CenterPanel(manager)
    panel.set_selection("project", exp_id)
    assert "样本注释A" in panel.notes_label.text()
    panel.set_selection("experiment", exp_id)
    assert "实验注释B" in panel.notes_label.text()
    panel.set_selection("data", exp_id, data_id)
    assert "数据注释C" in panel.notes_label.text()
    assert not panel.edit_notes_button.isHidden()
    seen: list[tuple[str, str, str]] = []
    panel.edit_notes_requested.connect(lambda k, e, d: seen.append((k, e, d)))
    panel.edit_notes_button.click()
    assert seen and seen[0] == ("data", exp_id, data_id)
    panel.close()


def test_main_window_menu_experiment(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """顶部菜单:「样本」改为「实验」,去掉样本管理/添加/删除样本。"""
    from gui.main_window import MainWindow

    workspace = tmp_path / "ws"
    workspace.mkdir()

    class _Ws:
        def __init__(self, root):
            self.root = root

        def ensure(self):
            self.root.mkdir(parents=True, exist_ok=True)
            return self.root

        def list_projects(self):
            return sorted(
                p
                for p in self.root.iterdir()
                if p.is_dir() and (p / "project.json").is_file()
            )

    monkeypatch.setattr("gui.main_window.WorkspaceManager", lambda: _Ws(workspace))
    monkeypatch.setattr(
        "core.workspace.WorkspaceManager", lambda *a, **k: _Ws(workspace)
    )
    window = MainWindow()
    menus = [action.text() for action in window.menuBar().actions()]
    assert "实验(&E)" in menus
    assert "样本(&S)" not in menus
    experiment_menu = next(
        action.menu()
        for action in window.menuBar().actions()
        if action.text() == "实验(&E)"
    )
    labels = [action.text() for action in experiment_menu.actions()]
    assert "新建实验..." in labels
    assert "样本管理" not in labels
    assert "添加样本..." not in labels
    assert "删除样本..." not in labels
    window.close()


def test_edit_notes_saves(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「编辑注释」→ 主窗口保存到项目并刷新顶部注释条。"""
    from gui.main_window import MainWindow

    manager, exp_id, _data_id = _manager(tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    class _Ws:
        def __init__(self, root):
            self.root = root

        def ensure(self):
            self.root.mkdir(parents=True, exist_ok=True)
            return self.root

        def list_projects(self):
            return sorted(
                p
                for p in self.root.iterdir()
                if p.is_dir() and (p / "project.json").is_file()
            )

    monkeypatch.setattr("gui.main_window.WorkspaceManager", lambda: _Ws(workspace))
    monkeypatch.setattr(
        "core.workspace.WorkspaceManager", lambda *a, **k: _Ws(workspace)
    )

    from PyQt6.QtWidgets import QDialog

    class _FakeNotesDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, parent, title, text):
            self._text = text

        def exec(self):
            return self.DialogCode.Accepted

        def result_text(self):
            return "新注释内容"

    monkeypatch.setattr("gui.main_window.NotesDialog", _FakeNotesDialog)
    window = MainWindow(manager=manager)
    window.center_panel.set_selection("experiment", exp_id)
    window._edit_notes("experiment", exp_id, "")
    assert experiment_note(manager.project, exp_id) == "新注释内容"
    assert "新注释内容" in window.center_panel.notes_label.text()
    window.close()
