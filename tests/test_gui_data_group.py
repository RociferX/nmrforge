"""GUI 数据组测试:树渲染组节点/组内数据、右键菜单、组批量面板。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QMenu

from core.project import ProjectManager
from gui.group_panel import GroupBatchPanel
from gui.project_tree import ProjectTreePanel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


class _TempWorkspace:
    """指向临时目录的工作区桩。"""

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


def _manager_with_group(
    tmp_path: Path,
) -> tuple[ProjectManager, str, list[str], str]:
    manager = ProjectManager.create_project(tmp_path / "ws" / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    d1 = manager.import_data(entry.id, "/fake/1").id
    d2 = manager.import_data(entry.id, "/fake/2").id
    d3 = manager.import_data(entry.id, "/fake/3").id
    group = manager.create_data_group(entry.id, title="对比组", data_ids=[d1, d2])
    manager.save()
    return manager, entry.id, [d1, d2, d3], group.id


def _tree_items(panel: ProjectTreePanel):
    """取 [experiment, ...children] 树结构(workspace → project → experiment)。"""
    workspace = panel.tree.topLevelItem(0)
    project = workspace.child(0)
    experiment = project.child(0)
    return [
        (experiment.child(i), experiment.child(i).data(0, 0x0100))
        for i in range(experiment.childCount())
    ]


def test_tree_renders_group_node(
    tmp_path: Path, qapp: QApplication
) -> None:
    """实验类型下:数据组节点与未入组数据同级,组内数据在组节点下。"""
    manager, exp_id, data_ids, group_id = _manager_with_group(tmp_path)
    panel = ProjectTreePanel(
        manager, workspace=_TempWorkspace(tmp_path / "ws")
    )
    children = _tree_items(panel)
    kinds = [role.get("kind") for _item, role in children]
    # 组节点在前,未入组数据在后(同级)
    assert kinds == ["group", "data"]
    group_item = children[0][0]
    role = group_item.data(0, 0x0100)
    assert role["group_id"] == group_id
    assert group_item.text(0) == "对比组"
    assert group_item.text(1) == "2 个数据"
    member_ids = [
        group_item.child(i).data(0, 0x0100).get("data_id")
        for i in range(group_item.childCount())
    ]
    assert member_ids == data_ids[:2]
    # 未入组数据直接挂实验类型下
    ungrouped = children[1][0]
    assert ungrouped.data(0, 0x0100).get("data_id") == data_ids[2]
    panel.tree.clear()
    panel.close()


def test_group_context_menu(tmp_path: Path, qapp: QApplication) -> None:
    """组节点右键含「把其它数据加入该组/重命名组/删除组」。"""
    manager, exp_id, _data_ids, group_id = _manager_with_group(tmp_path)
    panel = ProjectTreePanel(
        manager, workspace=_TempWorkspace(tmp_path / "ws")
    )
    children = _tree_items(panel)
    group_item = children[0][0]
    menu = QMenu(panel)
    panel._on_context_menu_impl(menu, group_item)
    texts = [a.text() for a in menu.actions()]
    assert "把其它数据加入该组..." in texts
    assert "重命名组..." in texts
    assert "删除组" in texts
    panel.tree.clear()
    panel.close()


def test_group_member_context_menu_remove(
    tmp_path: Path, qapp: QApplication
) -> None:
    """组内数据右键含「把该数据移出组」。"""
    manager, exp_id, data_ids, group_id = _manager_with_group(tmp_path)
    panel = ProjectTreePanel(
        manager, workspace=_TempWorkspace(tmp_path / "ws")
    )
    children = _tree_items(panel)
    group_item = children[0][0]
    member = group_item.child(0)
    menu = QMenu(panel)
    panel._on_context_menu_impl(menu, member)
    texts = [a.text() for a in menu.actions()]
    assert "把该数据移出组" in texts
    panel.tree.clear()
    panel.close()


def test_ungrouped_data_has_no_remove_option(
    tmp_path: Path, qapp: QApplication
) -> None:
    """未入组数据右键不含「把该数据移出组」。"""
    manager, exp_id, _data_ids, _group_id = _manager_with_group(tmp_path)
    panel = ProjectTreePanel(
        manager, workspace=_TempWorkspace(tmp_path / "ws")
    )
    children = _tree_items(panel)
    ungrouped = children[1][0]
    menu = QMenu(panel)
    panel._on_context_menu_impl(menu, ungrouped)
    texts = [a.text() for a in menu.actions()]
    assert "把该数据移出组" not in texts
    panel.close()


def test_group_batch_panel_context(
    tmp_path: Path, qapp: QApplication
) -> None:
    """组批量面板:成员显示、参考数据下拉只列已生成谱图的数据。"""
    manager, exp_id, data_ids, group_id = _manager_with_group(tmp_path)
    entry = manager.project.experiment(exp_id)
    entry.data[0].spectrum_path = "/tmp/a.ft2"  # d1 已处理
    manager.save()
    panel = GroupBatchPanel()
    panel.set_context(manager, exp_id, group_id)
    assert "2 个" in panel.member_label.text()
    assert panel.run_optimize_button.isEnabled()
    refs = [
        panel.reference_combo.itemData(i)
        for i in range(panel.reference_combo.count())
    ]
    assert data_ids[0] in refs
    assert data_ids[1] not in refs  # 未生成谱图,不作为参考
    panel.close()


def test_remove_from_group_returns_to_ungrouped(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「把该数据移出组」后,数据回到实验类型下成为普通单个数据。"""
    from gui.main_window import MainWindow

    manager, exp_id, data_ids, group_id = _manager_with_group(tmp_path)
    manager.save()
    monkeypatch.setattr(
        "gui.main_window.WorkspaceManager",
        lambda: _TempWorkspace(tmp_path / "ws"),
    )
    monkeypatch.setattr(
        "core.workspace.WorkspaceManager",
        lambda *a, **k: _TempWorkspace(tmp_path / "ws"),
    )
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    experiment = tree.topLevelItem(0).child(0).child(0)
    group_item = experiment.child(0)
    assert group_item.data(0, 0x0100).get("kind") == "group"
    assert group_item.childCount() == 2

    # 组内数据右键移出组(经 main_window 接线)
    window._group_remove_data(exp_id, group_id, data_ids[0])

    # 组内成员减少,移出数据成为实验类型下的普通单个数据
    experiment = window.project_tree.tree.topLevelItem(0).child(0).child(0)
    group_item = experiment.child(0)
    assert group_item.childCount() == 1
    kinds = [
        experiment.child(i).data(0, 0x0100).get("kind")
        for i in range(experiment.childCount())
    ]
    # 组 + 两个普通单个数据(移出的 d1 与原本未入组的 d3)
    assert kinds == ["group", "data", "data"]
    ungrouped_ids = [
        experiment.child(i).data(0, 0x0100).get("data_id")
        for i in range(1, experiment.childCount())
    ]
    assert data_ids[0] in ungrouped_ids
    # 组内成员同步减少(恢复普通单个数据)
    assert manager.group(exp_id, group_id).data_ids == [data_ids[1]]
    window.close()


def test_group_batch_stop_steps(tmp_path: Path, qapp: QApplication) -> None:
    """截止步骤下拉:选「生成 FID」→ steps 仅 fid。"""
    panel = GroupBatchPanel()
    panel.stop_combo.setCurrentIndex(0)  # 生成 FID
    assert panel._stop_steps() == ["fid"]
    panel.stop_combo.setCurrentIndex(1)  # 生成谱图
    assert panel._stop_steps() == ["fid", "spectrum"]
    panel.close()



