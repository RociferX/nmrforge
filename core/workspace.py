"""工作区容器(契约 v1.3 §9.1 / G2B-003)。

软件第一次启动在用户文件夹自动创建默认工作区 `~/NMRForgeWorkspace`;
工作区下一层是项目(含 project.json),再下一层是实验/数据目录层级。
"""

from __future__ import annotations

import re
from pathlib import Path

from core.project import ProjectManager

DEFAULT_WORKSPACE_NAME = "NMRForgeWorkspace"

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff ]+$")


class WorkspaceError(Exception):
    """工作区操作错误(路径/重名/IO)。"""


def default_workspace_path() -> Path:
    """默认工作区路径(Windows 与 Linux 一致:用户目录/NMRForgeWorkspace)。"""
    return Path.home() / DEFAULT_WORKSPACE_NAME


class WorkspaceManager:
    """工作区:项目目录的容器(Shared,契约 §9.1)。"""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = (
            Path(root).resolve()
            if root is not None
            else default_workspace_path().resolve()
        )

    def ensure(self) -> Path:
        """确保工作区目录存在(幂等),返回根路径。"""
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def list_projects(self) -> list[Path]:
        """列出工作区内的项目目录(含 project.json,忽略其它目录/文件)。"""
        if not self.root.is_dir():
            return []
        return sorted(
            p
            for p in self.root.iterdir()
            if p.is_dir() and (p / "project.json").is_file()
        )

    def create_project(self, name: str, **kwargs: object) -> ProjectManager:
        """在工作区下创建项目目录(workspace/<name>/),返回 ProjectManager。"""
        name = str(name).strip()
        if not name or "/" in name or "\\" in name or not _SAFE_NAME.match(name):
            raise WorkspaceError(f"非法项目名: {name!r}")
        self.ensure()
        root = self.root / name
        if (root / "project.json").exists():
            raise WorkspaceError(f"工作区已存在项目: {name}")
        return ProjectManager.create_project(root, name=name, **kwargs)

    def open_project(self, name_or_path: Path | str) -> ProjectManager:
        """按名称(工作区内)或绝对路径打开项目。"""
        path = Path(name_or_path)
        if not path.is_absolute():
            path = self.root / path
        return ProjectManager.open_project(path)


__all__ = [
    "DEFAULT_WORKSPACE_NAME",
    "WorkspaceError",
    "WorkspaceManager",
    "default_workspace_path",
]
