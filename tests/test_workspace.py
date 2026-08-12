"""工作区容器测试(core/workspace,契约 v1.3 §9.1)。"""

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
    # 有 project.json 的项目
    ProjectManager.create_project(manager.root / "proj_a", "proj_a")
    ProjectManager.create_project(manager.root / "proj_b", "proj_b")
    # 无 project.json 的目录应被忽略
    (manager.root / "not_a_project").mkdir()
    # 文件也应被忽略
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
    with pytest.raises(WorkspaceError, match="已存在项目"):
        manager.create_project("demo")


def test_create_project_rejects_unsafe_name(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "ws")
    with pytest.raises(WorkspaceError, match="非法项目名"):
        manager.create_project("../escape")
    with pytest.raises(WorkspaceError, match="非法项目名"):
        manager.create_project("")


def test_open_project_by_name_and_path(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "ws")
    manager.create_project("demo")
    by_name = manager.open_project("demo")
    assert by_name.project is not None
    assert by_name.project.name == "demo"
    by_path = manager.open_project(manager.root / "demo")
    assert by_path.root == by_name.root
