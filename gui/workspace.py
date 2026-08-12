"""工作区适配层(G2B-003 §9.4):GUI 侧调用 WorkspaceManager.ensure()。

Backend 的 core/workspace.WorkspaceManager 已落地(契约 v1.3 §9.1),
此处直接使用;create_project 返回 ProjectManager。
"""

from __future__ import annotations

from pathlib import Path

from core.workspace import WorkspaceManager as CoreWorkspaceManager


class WorkspaceManager:
    """GUI 工作区门面:ensure/list_projects/create_project。"""

    def __init__(self, root: Path | str | None = None) -> None:
        self._impl = CoreWorkspaceManager(root)

    def ensure(self) -> Path:
        return Path(self._impl.ensure())

    def list_projects(self) -> list[Path]:
        return [Path(p) for p in self._impl.list_projects()]

    def create_project(self, name: str, **kwargs):
        """在工作区下创建项目,返回 ProjectManager。"""
        return self._impl.create_project(name, **kwargs)

    def default_root(self) -> Path:
        return self.ensure()
