"""Workspace container test(core/workspace, contract v1.3 §9.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.project import ProjectManager
from core.workspace import (
    WorkspaceError,
    WorkspaceManager,
    default_workspace_path,
)


def test_default_workspace_path() -> None:
    assert default_workspace_path() == Path.home() / "NMRForgeWorkspace"


def test_ensure_is_idempotent(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "ws")
    first = manager.ensure()
    second = manager.ensure()
    assert first == second == manager.root
    assert first.is_dir()


def test_list_projects_discovers_and_ignores(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "ws")
    manager.ensure()
    # There are project.json items.
    ProjectManager.create_project(manager.root / "proj_a", "proj_a")
    ProjectManager.create_project(manager.root / "proj_b", "proj_b")
    # Directories without project.json should be ignored.
    (manager.root / "not_a_project").mkdir()
    # File should also be ignored.
    (manager.root / "readme.txt").write_text("x", encoding="utf-8")

    projects = manager.list_projects()
    assert [p.name for p in projects] == ["proj_a", "proj_b"]


def test_create_project_in_workspace(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "ws")
    pm = manager.create_project("demo", protein_name="GB1")
    assert manager.root / "demo" / "project.json"
    assert pm.root == (manager.root / "demo").resolve()
    assert pm.project is not None
    assert pm.project.name == "demo"


def test_create_project_rejects_duplicate(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "ws")
    manager.create_project("demo")
    with pytest.raises(WorkspaceError, match="already has a project"):
        manager.create_project("demo")


def test_create_project_rejects_unsafe_name(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "ws")
    with pytest.raises(WorkspaceError, match="illegal project name"):
        manager.create_project("../escape")
    with pytest.raises(WorkspaceError, match="illegal project name"):
        manager.create_project("")


def test_open_project_by_name_and_path(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "ws")
    manager.create_project("demo")
    by_name = manager.open_project("demo")
    assert by_name.project is not None
    assert by_name.project.name == "demo"
    by_path = manager.open_project(manager.root / "demo")
    assert by_path.root == by_name.root



def test_rename_project_updates_dir_and_name(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "ws")
    manager.create_project("alpha")
    new_path = manager.rename_project("alpha", "beta")
    assert new_path == manager.root / "beta"
    assert not (manager.root / "alpha").exists()
    pm = ProjectManager.open_project(new_path)
    assert pm.project.name == "beta"


def test_rename_project_conflicts_and_validation(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "ws")
    manager.create_project("alpha")
    manager.create_project("beta")
    with pytest.raises(WorkspaceError, match="already has a project"):
        manager.rename_project("alpha", "beta")
    with pytest.raises(WorkspaceError, match="illegal project name"):
        manager.rename_project("alpha", "../x")
    with pytest.raises(WorkspaceError, match="project does not exist"):
        manager.rename_project("nope", "gamma")


def test_delete_project_trash_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29ex: Delete the project into the (fake) recycle bin, and the project will be
    visible again after being moved back to the workspace."""
    import shutil

    manager = WorkspaceManager(tmp_path / "ws")
    manager.create_project("alpha")
    trash = tmp_path / "trash"
    trash.mkdir()

    def fake(path, fallback_dir, rel=None):
        dest = trash / (rel or Path(path).name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
        return dest

    monkeypatch.setattr("core.workspace.send_to_trash", fake)
    target = manager.delete_project("alpha", trash=True)
    assert not (manager.root / "alpha").exists()
    assert target.exists()
    assert (target / "project.json").is_file()
    # Recovery: Move back to the workspace -> Project is visible again (scanning, no central
    # registry).
    shutil.move(str(target), str(manager.root / "alpha"))
    assert manager.list_projects() == [manager.root / "alpha"]


def test_delete_project_direct_and_missing(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "ws")
    manager.create_project("alpha")
    manager.delete_project("alpha", trash=False)
    assert not (manager.root / "alpha").exists()
    with pytest.raises(WorkspaceError, match="project does not exist"):
        manager.delete_project("alpha")
