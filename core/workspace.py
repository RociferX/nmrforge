"""Workspace container (contract v1.3 §9.1 / G2B-003).

On first start the software creates a default workspace `~/NMRForgeWorkspace` inside the
user folder; the level below the workspace holds the projects (each with project.json),
and below that come the experiment/data directory levels.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from core.project import ProjectManager
from core.trash import send_to_trash
from ui_support.i18n import tr

DEFAULT_WORKSPACE_NAME = "NMRForgeWorkspace"

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff ]+$")


class WorkspaceError(Exception):
    """Workspace operation error (path / duplicate name / IO)."""


def _validate_name(name: str) -> str:
    """Validate a project name (non-empty, no path separator, safe characters)."""
    name = str(name).strip()
    if not name or "/" in name or "\\" in name or not _SAFE_NAME.match(name):
        raise WorkspaceError(tr("illegal project name: {p0!r}", p0=name))
    return name


def default_workspace_path() -> Path:
    """Default workspace path (identical on Windows and Linux: home directory/NMRForgeWorkspace)."""
    return Path.home() / DEFAULT_WORKSPACE_NAME


class WorkspaceManager:
    """Workspace: a container of project directories (Shared, contract §9.1)."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = (
            Path(root).resolve()
            if root is not None
            else default_workspace_path().resolve()
        )

    def ensure(self) -> Path:
        """Make sure the workspace directory exists (idempotent) and return the root path."""
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def list_projects(self) -> list[Path]:
        """List the project directories inside the workspace (those holding project.json;
        other directories and files are ignored)."""
        if not self.root.is_dir():
            return []
        return sorted(
            p
            for p in self.root.iterdir()
            if p.is_dir() and (p / "project.json").is_file()
        )

    def create_project(self, name: str, **kwargs: object) -> ProjectManager:
        """Create a project directory under the workspace (workspace/<name>/), return a
        ProjectManager."""
        name = _validate_name(name)
        self.ensure()
        root = self.root / name
        if (root / "project.json").exists():
            raise WorkspaceError(tr("the workspace already has a project: {p0}", p0=name))
        return ProjectManager.create_project(root, name=name, **kwargs)

    def open_project(self, name_or_path: Path | str) -> ProjectManager:
        """Open a project by name (inside the workspace) or by absolute path."""
        path = Path(name_or_path)
        if not path.is_absolute():
            path = self.root / path
        return ProjectManager.open_project(path)

    def delete_project(self, name: str, trash: bool = True) -> Path:
        """Delete a project directory (moved to the trash by default; returns the final
        location).

        trash=True: the system trash comes first (send2trash, an optional dependency) with a
        fallback to the system temporary directory (recoverable) -- workspace content is
        never removed with a bare rm -rf; trash=False: delete outright (unrecoverable). A
        missing project raises WorkspaceError.
        """
        path = (self.root / name).resolve()
        if (
            not path.is_relative_to(self.root)
            or not path.is_dir()
            or not (path / "project.json").is_file()
        ):
            raise WorkspaceError(tr("project does not exist: {p0}", p0=name))
        if not trash:
            shutil.rmtree(path)
            return path
        # 0.2.199-patch29ex: deletion goes to the system trash (send2trash, falling back to
        # .nmrforge_trash inside the workspace; both are recoverable); nothing is destroyed
        # outright.
        return send_to_trash(path, self.root / ".nmrforge_trash", Path(name))

    def rename_project(self, old_name: str, new_name: str) -> Path:
        """Rename a project: validate the new name and check for clashes, move the directory
        and keep the name in project.json in sync."""
        new_name = _validate_name(new_name)
        old_path = (self.root / old_name).resolve()
        if (
            not old_path.is_relative_to(self.root)
            or not (old_path / "project.json").is_file()
        ):
            raise WorkspaceError(tr("project does not exist: {p0}", p0=old_name))
        new_path = self.root / new_name
        if new_path.exists():
            raise WorkspaceError(
                tr("the workspace already has a project: {p0}", p0=new_name)
            )
        shutil.move(str(old_path), str(new_path))
        manager = ProjectManager.open_project(new_path)
        manager.project.name = new_name
        manager.save()
        return new_path


__all__ = [
    "DEFAULT_WORKSPACE_NAME",
    "WorkspaceError",
    "WorkspaceManager",
    "default_workspace_path",
]
