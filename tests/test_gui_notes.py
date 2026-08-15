"""项目/实验类型/样品数据三级注释功能测试:结构化字段 helper + 中间顶部注释条。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from core.project import ProjectManager
from gui.notes import (
    DATA_FIELDS,
    EXPERIMENT_FIELDS,
    SAMPLE_FIELDS,
    data_note,
    data_note_fields,
    experiment_note,
    experiment_note_fields,
    experiment_type_options,
    format_fields,
    note_fields,
    sample_note,
    sample_note_fields,
    set_data_note_fields,
    set_experiment_note_fields,
    set_sample_note_fields,
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


def test_note_fields_schemas_differ_per_level() -> None:
    """0.2.79:各级注释字段不同(项目=蛋白基本信息;实验类型=类型/维度/核;样品数据=重复/条件/pH/温度)。"""
    assert [key for key, _ in SAMPLE_FIELDS] == [
        "protein_name",
        "expression_system",
        "concentration",
        "buffer",
        "notes",
    ]
    assert [key for key, _ in EXPERIMENT_FIELDS] == [
        "dimension",
        "experiment_type",
        "nuclei",
        "notes",
    ]
    assert [key for key, _ in DATA_FIELDS] == [
        "repeat",
        "condition",
        "buffer_ph",
        "temperature",
        "notes",
    ]
    assert note_fields("project") == SAMPLE_FIELDS
    assert note_fields("experiment") == EXPERIMENT_FIELDS
    assert note_fields("data") == DATA_FIELDS


def test_notes_helper_roundtrip(tmp_path: Path) -> None:
    """三级结构化注释读写,manager.save() 后重新打开仍可读。"""
    manager, exp_id, data_id = _manager(tmp_path)
    assert sample_note(manager.project) == ""
    assert experiment_note(manager.project, exp_id) == ""
    assert data_note(manager.project, exp_id, data_id) == ""
    set_sample_note_fields(
        manager.project,
        {"protein_name": "GB1", "concentration": "0.5 mM", "buffer": "PBS"},
    )
    set_experiment_note_fields(
        manager.project,
        exp_id,
        {"experiment_type": "HSQC", "dimension": "2D", "nuclei": "1H-15N"},
    )
    set_data_note_fields(
        manager.project,
        exp_id,
        data_id,
        {"repeat": "2", "condition": "pH 7.0", "buffer_ph": "7.0", "temperature": "25"},
    )
    assert "蛋白名称: GB1" in sample_note(manager.project)
    assert "浓度: 0.5 mM" in sample_note(manager.project)
    assert "实验类型: HSQC" in experiment_note(manager.project, exp_id)
    assert "重复号: 2" in data_note(manager.project, exp_id, data_id)
    assert "Buffer pH: 7.0" in data_note(manager.project, exp_id, data_id)
    manager.save()
    reopened = ProjectManager.open_project(tmp_path / "proj")
    assert sample_note_fields(reopened.project)["protein_name"] == "GB1"
    assert experiment_note_fields(reopened.project, exp_id)["dimension"] == "2D"
    assert data_note_fields(reopened.project, exp_id, data_id)["temperature"] == "25"


def test_legacy_plain_text_notes_compat(tmp_path: Path) -> None:
    """旧纯文本注释(protein.notes / entry.notes / data_notes 字符串)仍可读。"""
    manager, exp_id, data_id = _manager(tmp_path)
    manager.project.protein.notes = "GB1, 0.5 mM, PBS"
    entry = manager.project.experiment(exp_id)
    entry.notes = "15N HSQC"
    meta = dict(entry.metadata or {})
    meta.setdefault("data_notes", {})[data_id] = "2026-08-13"
    entry.metadata = meta
    assert sample_note(manager.project) == "GB1, 0.5 mM, PBS"
    assert experiment_note(manager.project, exp_id) == "15N HSQC"
    assert data_note(manager.project, exp_id, data_id) == "2026-08-13"


def test_format_fields_skips_empty() -> None:
    assert format_fields({"protein_name": "GB1", "notes": ""}) == "蛋白名称: GB1"
    assert format_fields({}) == ""


def test_center_panel_notes_bar(tmp_path: Path, qapp: QApplication) -> None:
    """中间区域最上方按选中层级显示各级注释字段。"""
    manager, exp_id, data_id = _manager(tmp_path)
    set_sample_note_fields(manager.project, {"protein_name": "样本A"})
    set_experiment_note_fields(manager.project, exp_id, {"experiment_type": "HSQC"})
    set_data_note_fields(manager.project, exp_id, data_id, {"repeat": "3"})
    manager.save()
    from gui.center_panel import CenterPanel

    panel = CenterPanel(manager)
    panel.set_selection("project", exp_id)
    assert "蛋白名称: 样本A" in panel.notes_label.text()
    panel.set_selection("experiment", exp_id)
    assert "实验类型: HSQC" in panel.notes_label.text()
    panel.set_selection("data", exp_id, data_id)
    assert "重复号: 3" in panel.notes_label.text()
    assert not panel.edit_notes_button.isHidden()
    seen: list[tuple[str, str, str]] = []
    panel.edit_notes_requested.connect(lambda k, e, d: seen.append((k, e, d)))
    panel.edit_notes_button.click()
    assert seen and seen[0] == ("data", exp_id, data_id)
    panel.close()


def test_main_window_menu_experiment(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """顶部菜单:「项目/实验类型」,去掉样本管理/添加/删除样本。"""
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
    assert "实验类型(&E)" in menus
    assert "样本(&S)" not in menus
    experiment_menu = next(
        action.menu()
        for action in window.menuBar().actions()
        if action.text() == "实验类型(&E)"
    )
    labels = [action.text() for action in experiment_menu.actions()]
    assert "新建实验类型..." in labels
    assert "项目管理" not in labels
    assert "添加项目..." not in labels
    assert "删除项目..." not in labels
    window.close()


def test_edit_notes_saves(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「编辑注释」→ 主窗口保存结构化字段并刷新顶部注释条。"""
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

        def __init__(self, parent, title, kind="", values=None):
            self._values = values or {}

        def exec(self):
            return self.DialogCode.Accepted

        def result_fields(self):
            return {"experiment_type": "新实验注释", "dimension": "2D"}

    monkeypatch.setattr("gui.main_window.NotesDialog", _FakeNotesDialog)
    window = MainWindow(manager=manager)
    window.center_panel.set_selection("experiment", exp_id)
    window._edit_notes("experiment", exp_id, "")
    fields = experiment_note_fields(manager.project, exp_id)
    assert fields["experiment_type"] == "新实验注释"
    assert "新实验注释" in window.center_panel.notes_label.text()
    window.close()
def test_experiment_type_options_from_presets() -> None:
    """0.2.85:实验类型选项来自 presets,按维度过滤(排除 Generic 兜底)。"""
    options_2d = experiment_type_options("2D")
    options_3d = experiment_type_options("3D")
    assert "HSQC" in options_2d and "COSY" in options_2d
    assert "HNCA" in options_3d and "HNCACB" in options_3d
    assert "HNCA" not in options_2d
    assert "HSQC" not in options_3d
    assert "Generic2D" not in options_2d
    assert "Generic3D" not in options_3d
    assert set(options_2d) | set(options_3d) == set(experiment_type_options())


def test_notes_dialog_combos_dimension_then_type(
    qapp: QApplication,
) -> None:
    """0.2.85:注释表单用下拉;先选维度,再按 presets 过滤实验类型。"""
    from gui.dialogs import NotesDialog

    dialog = NotesDialog(
        None,
        "实验类型注释",
        "experiment",
        {"dimension": "2D", "experiment_type": "HSQC"},
    )
    assert dialog._combos["dimension"].currentText() == "2D"
    type_combo = dialog._combos["experiment_type"]
    assert type_combo.currentText() == "HSQC"
    items = [type_combo.itemText(i) for i in range(type_combo.count())]
    assert "HNCA" not in items
    # 维度切到 3D → 类型选项切换为三共振谱
    dialog._combos["dimension"].setCurrentText("3D")
    items = [type_combo.itemText(i) for i in range(type_combo.count())]
    assert "HNCA" in items
    assert "HSQC" not in items
    type_combo.setCurrentText("HNCA")
    dialog._combos["nuclei"].setCurrentText("1H-15N-13C")
    fields = dialog.result_fields()
    assert fields["dimension"] == "3D"
    assert fields["experiment_type"] == "HNCA"
    assert fields["nuclei"] == "1H-15N-13C"
    dialog.close()
