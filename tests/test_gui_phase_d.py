"""阶段 D 测试:首次导入引导 + 树增量刷新。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from core.project import ProjectManager
from gui.main_window import MainWindow
from gui.pipeline_panel import PipelinePanel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


class _TempWorkspace:
    def __init__(self, root) -> None:
        self.root = Path(root)

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def list_projects(self):
        return sorted(
            p
            for p in self.root.iterdir()
            if p.is_dir() and (p / "project.json").is_file()
        )

    def create_project(self, name: str, **kwargs):
        return ProjectManager.create_project(self.root / name, name, **kwargs)


class _FakeController:
    def set_manager(self, manager) -> None:
        pass


def test_tree_incremental_refresh_preserves_nodes(
    tmp_path: Path, qapp: QApplication
) -> None:
    from gui.project_tree import ProjectTreePanel

    ws = tmp_path / "ws"
    ws.mkdir()
    manager = ProjectManager.create_project(ws / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    manager.import_data(entry.id, "/fake/1")
    manager.save()
    panel = ProjectTreePanel(manager, workspace=_TempWorkspace(ws))
    data_item = panel.tree.topLevelItem(0).child(0).child(0).child(0)
    assert data_item is not None
    # 未变化刷新:数据节点保留同一实例
    panel.refresh()
    data_item2 = panel.tree.topLevelItem(0).child(0).child(0).child(0)
    assert data_item2 is data_item
    # 新增数据:旧节点保留,新节点加入
    manager.import_data(entry.id, "/fake/2")
    manager.save()
    panel.refresh()
    exp_item = panel.tree.topLevelItem(0).child(0).child(0)
    assert exp_item.childCount() == 2
    assert exp_item.child(0) is data_item
    panel.close()


def test_tree_folder_children_preserved_when_unchanged(
    tmp_path: Path, qapp: QApplication
) -> None:
    from gui.project_tree import ProjectTreePanel

    ws = tmp_path / "ws"
    ws.mkdir()
    manager = ProjectManager.create_project(ws / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    raw = manager.data_dir(entry.id, data.id, "raw")
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "acqus").write_text("x")
    manager.save()
    panel = ProjectTreePanel(manager, workspace=_TempWorkspace(ws))
    data_item = panel.tree.topLevelItem(0).child(0).child(0).child(0)
    folder_item = data_item.child(0)
    assert folder_item.childCount() >= 1
    before = [folder_item.child(i) for i in range(folder_item.childCount())]
    panel.refresh()
    folder_item2 = (
        panel.tree.topLevelItem(0).child(0).child(0).child(0).child(0)
    )
    after = [folder_item2.child(i) for i in range(folder_item2.childCount())]
    assert after == before  # 未变化:文件子节点未重建
    # 目录变化 → 刷新文件子节点
    (raw / "ser").write_text("y")
    panel.refresh()
    folder_item3 = (
        panel.tree.topLevelItem(0).child(0).child(0).child(0).child(0)
    )
    names = [
        folder_item3.child(i).text(0)
        for i in range(folder_item3.childCount())
    ]
    assert "ser" in names
    panel.close()


def test_first_import_hint_highlights_next_step(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    manager.import_data(entry.id, "/fake/1")
    manager.save()
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", entry.id, "d_001")
    panel.show_first_import_hint()
    assert not panel.hint_bubble.isHidden()
    assert "生成 FID" in panel.hint_bubble.text()
    panel.close()


def test_first_import_hint_only_once(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    import copy

    from gui import settings as settings_module

    state: dict = {"guide": {"first_import_hint_shown": False}}

    def fake_load() -> dict:
        return copy.deepcopy(state)

    def fake_save(settings: dict) -> None:
        state.clear()
        state.update(copy.deepcopy(settings))

    monkeypatch.setattr(settings_module, "load_settings", fake_load)
    monkeypatch.setattr(settings_module, "save_settings", fake_save)
    ws = tmp_path / "ws"
    ws.mkdir()
    manager = ProjectManager.create_project(ws / "proj", "demo")
    manager.create_experiment("HSQC")
    manager.save()
    monkeypatch.setattr(
        "gui.main_window.WorkspaceManager", lambda: _TempWorkspace(ws)
    )
    monkeypatch.setattr(
        "core.workspace.WorkspaceManager",
        lambda *a, **k: _TempWorkspace(ws),
    )
    window = MainWindow(manager=manager)
    shown: list[str] = []
    monkeypatch.setattr(
        window.pipeline,
        "show_first_import_hint",
        lambda: shown.append("hint"),
    )
    window._maybe_show_first_import_hint()
    window._maybe_show_first_import_hint()
    assert shown == ["hint"]  # 只出现一次
    assert state["guide"]["first_import_hint_shown"] is True
    window.close()
