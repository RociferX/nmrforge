"""GUI Data group test: tree render group node/Within group data, right-click menu, group batch
panel."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtcompat.QtWidgets import QApplication, QMenu

from core.project import ProjectManager
from gui.group_panel import GroupBatchPanel
from gui.project_tree import ProjectTreePanel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


class _TempWorkspace:
    """Workspace stub pointing to temporary directory."""

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
    group = manager.create_data_group(entry.id, title="comparison group", data_ids=[d1, d2])
    manager.save()
    return manager, entry.id, [d1, d2, d3], group.id


def _tree_items(panel: ProjectTreePanel):
    """Get the [experiment,...children] tree structure (workspace -> project -> experiment)."""
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
    """Under experiment type: the data group node is at the same level as the ungrouped data, and
    the data in the group is under the group node."""
    manager, exp_id, data_ids, group_id = _manager_with_group(tmp_path)
    panel = ProjectTreePanel(
        manager, workspace=_TempWorkspace(tmp_path / "ws")
    )
    children = _tree_items(panel)
    kinds = [role.get("kind") for _item, role in children]
    # The group node is in the front, and the ungrouped data is in the back (same level).
    assert kinds == ["group", "data"]
    group_item = children[0][0]
    role = group_item.data(0, 0x0100)
    assert role["group_id"] == group_id
    assert group_item.text(0) == "comparison group"
    assert group_item.text(1) == "2 data"
    member_ids = [
        group_item.child(i).data(0, 0x0100).get("data_id")
        for i in range(group_item.childCount())
    ]
    assert member_ids == data_ids[:2]
    # Ungrouped data is directly linked to the experiment type.
    ungrouped = children[1][0]
    assert ungrouped.data(0, 0x0100).get("data_id") == data_ids[2]
    panel.tree.clear()
    panel.close()


def test_group_context_menu(tmp_path: Path, qapp: QApplication) -> None:
    """The right click of the group node contains "Add other data to the group/Rename group/Delete
    group tag (data retention)" and "Delete group (including data)"."""
    manager, exp_id, _data_ids, group_id = _manager_with_group(tmp_path)
    panel = ProjectTreePanel(
        manager, workspace=_TempWorkspace(tmp_path / "ws")
    )
    children = _tree_items(panel)
    group_item = children[0][0]
    menu = QMenu(panel)
    panel._on_context_menu_impl(menu, group_item)
    texts = [a.text() for a in menu.actions()]
    assert "Add other data to the group..." in texts
    assert "rename group..." in texts
    assert "delete group tag (data retained)..." in texts
    assert "delete group (including data)..." in texts
    panel.tree.clear()
    panel.close()


def test_group_member_context_menu_remove(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Right-click the data in the group and select "Move this data out of the group"."""
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
    assert "Move the data out of the group" in texts
    panel.tree.clear()
    panel.close()


def test_ungrouped_data_has_no_remove_option(
    tmp_path: Path, qapp: QApplication
) -> None:
    """The right click of ungrouped data does not include "Move this data out of the group"."""
    manager, exp_id, _data_ids, _group_id = _manager_with_group(tmp_path)
    panel = ProjectTreePanel(
        manager, workspace=_TempWorkspace(tmp_path / "ws")
    )
    children = _tree_items(panel)
    ungrouped = children[1][0]
    menu = QMenu(panel)
    panel._on_context_menu_impl(menu, ungrouped)
    texts = [a.text() for a in menu.actions()]
    assert "Move the data out of the group" not in texts
    panel.close()


def test_group_batch_panel_context(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Group batch panel: members are displayed, and the reference data drop-down list only
    generates spectrum data."""
    manager, exp_id, data_ids, group_id = _manager_with_group(tmp_path)
    entry = manager.project.experiment(exp_id)
    entry.data[0].spectrum_path = "/tmp/a.ft2"  # d1 Processed.
    manager.save()
    panel = GroupBatchPanel()
    panel.set_context(manager, exp_id, group_id)
    assert "2" in panel.member_label.text()
    assert panel.run_optimize_button.isEnabled()
    refs = [
        panel.reference_combo.itemData(i)
        for i in range(panel.reference_combo.count())
    ]
    assert data_ids[0] in refs
    assert data_ids[1] not in refs  # Spectrum is not generated and is not used as a reference.
    panel.close()


def test_remove_from_group_returns_to_ungrouped(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After "moving the data out of the group", the data returns to the experiment type and
    becomes ordinary single data."""
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

    # Right-click the data in the group to move it out of the group (via main_window wiring).
    window._group_remove_data(exp_id, group_id, data_ids[0])

    # The number of members in the group decreases, and the removed data becomes an ordinary single
    # data under the experiment type.
    experiment = window.project_tree.tree.topLevelItem(0).child(0).child(0)
    group_item = experiment.child(0)
    assert group_item.childCount() == 1
    kinds = [
        experiment.child(i).data(0, 0x0100).get("kind")
        for i in range(experiment.childCount())
    ]
    # Group + two ordinary single data (d1 moved out and d3 originally not included in the group).
    assert kinds == ["group", "data", "data"]
    ungrouped_ids = [
        experiment.child(i).data(0, 0x0100).get("data_id")
        for i in range(1, experiment.childCount())
    ]
    assert data_ids[0] in ungrouped_ids
    # The synchronization of members within the group is reduced (recovering ordinary single data).
    assert manager.group(exp_id, group_id).data_ids == [data_ids[1]]
    window.close()


def test_group_batch_stop_steps(tmp_path: Path, qapp: QApplication) -> None:
    """Cut-off step drop-down: Select "Generate FID" -> steps fid only."""
    panel = GroupBatchPanel()
    panel.stop_combo.setCurrentIndex(0)  # Generate FID.
    assert panel._stop_steps() == ["fid"]
    panel.stop_combo.setCurrentIndex(1)  # Generate spectrum.
    assert panel._stop_steps() == ["fid", "spectrum"]
    panel.close()



