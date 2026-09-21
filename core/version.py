"""Single version source and runtime provenance information (REPORT-008/PROV-009,
2026-09-12).

Rules:

- **the software version exists in exactly one place**: ``core.__version__`` (this
  module only reads it and never copies the literal); ``pyproject.toml`` takes the same
  attribute through ``dynamic = ["version"]`` and the packaging script
  ``packaging/linux/build_appimage.sh`` reads the same attribute;
- ``dependency_versions()`` records the versions of the key third-party dependencies so
  they can be re-verified;
- NMRPipe / SMILE versions cannot be obtained safely at import time: the ``backend``
  layer calls ``register_tool_version`` once it has really parsed an NMRPipe
  installation directory (see ``backend/nmrpipe_version.py``); before that, queries
  return an empty table and never fabricate ``unknown``;
- ``tool_versions()`` merges both groups for ``ProjectManager``, which writes
  ``software_version`` / ``tool_versions`` when a ``WorkflowRun`` starts and ends.
"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import subprocess
import sys
from pathlib import Path

# key dependencies: recording their versions lets an artifact answer "which tool chain made this"
_DEPENDENCIES = ("numpy", "scipy", "nmrglue", "matplotlib", "PySide6", "PyYAML")

# external tool versions detected by the backend layer (cached in-process)
_TOOL_VERSIONS: dict[str, str] = {}

# source commit (cached in-process; None = not probed yet)
_GIT_COMMIT: str | None = None
_GIT_DIRTY: bool | None = None


def _git(*args: str) -> tuple[int, str]:
    """Run git once in the repository root; on failure return (non-zero, "")."""
    root = Path(__file__).resolve().parent.parent
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:  # noqa: BLE001 - a missing git or a timeout is not an error
        return 1, ""
    if done.returncode != 0:
        return done.returncode, ""
    return 0, done.stdout


def _discover_git_commit() -> tuple[str, bool]:
    """Probe the current source commit and whether the working tree is dirty.

    A frozen build (AppImage/PyInstaller) has no ``.git`` and then every value comes back
    empty -- nothing fabricates ``unknown`` and nothing is guessed. Dirtiness counts
    modified tracked files only (``--untracked-files=no``), so temporary artifacts cannot
    mark every run as dirty.
    """
    if getattr(sys, "frozen", False):
        return "", False
    code, out = _git("rev-parse", "HEAD")
    if code != 0:
        return "", False
    commit = out.strip()
    code, status = _git("status", "--porcelain", "--untracked-files=no")
    dirty = code == 0 and bool(status.strip())
    return commit, dirty


def git_commit() -> str:
    """Hash of the current source commit (an empty string when it cannot be read)."""
    global _GIT_COMMIT, _GIT_DIRTY
    if _GIT_COMMIT is None:
        _GIT_COMMIT, _GIT_DIRTY = _discover_git_commit()
    return _GIT_COMMIT


def git_commit_dirty() -> bool:
    """Whether the working tree holds uncommitted changes to tracked files
    (False without git, or when frozen)."""
    if _GIT_COMMIT is None:
        git_commit()
    return bool(_GIT_DIRTY)


def reset_git_probe() -> None:
    """Clear the commit probe cache (for tests)."""
    global _GIT_COMMIT, _GIT_DIRTY
    _GIT_COMMIT = None
    _GIT_DIRTY = None


def software_version() -> str:
    """NMRForge own version (the single source is ``core.__version__``)."""
    from core import __version__

    return str(__version__)


def software_commit() -> str:
    """Source commit hash: the build-time stamp ``NMRFORGE_GIT_COMMIT`` if set, else the repo.

    Returns an empty string when it cannot be discovered (frozen build without .git);
    it is never faked as ``unknown``.
    """
    stamped = os.environ.get("NMRFORGE_GIT_COMMIT", "").strip()
    if stamped:
        return stamped
    return git_commit()


def _distribution_version(name: str) -> str:
    try:
        return str(importlib.metadata.version(name))
    except Exception:  # noqa: BLE001 - not installed / missing metadata is not an error
        return ""


def dependency_versions() -> dict[str, str]:
    """Python and key dependency versions (an empty string means the value is
    unavailable; nothing is faked as "unknown")."""
    versions = {"python": platform.python_version()}
    for name in _DEPENDENCIES:
        version = _distribution_version(name)
        if version:
            versions[name.lower()] = version
    return versions


def register_tool_version(name: str, version: str) -> None:
    """Register an external tool version (NMRPipe/SMILE/bruker...); empty values are ignored."""
    key = str(name).strip().lower()
    value = str(version).strip()
    if key and value:
        _TOOL_VERSIONS[key] = value


def registered_tool_versions() -> dict[str, str]:
    """External tool versions probed in this process (a copy)."""
    return dict(_TOOL_VERSIONS)


def tool_versions() -> dict[str, str]:
    """Full tool version table written to a WorkflowRun (itself + dependencies +
    probed external tools)."""
    versions = {"nmrforge": software_version()}
    versions.update(dependency_versions())
    versions.update(registered_tool_versions())
    # source commit: results trace back to a concrete code state (the key is omitted when
    # it cannot be read, never written as "unknown")
    commit = software_commit()
    if commit:
        versions["software_commit"] = commit
        versions["git_commit"] = commit
        versions["git_commit_dirty"] = "1" if git_commit_dirty() else "0"
    return versions


def reset_tool_versions() -> None:
    """Clear the registered external tool versions (for tests)."""
    _TOOL_VERSIONS.clear()


__all__ = [
    "dependency_versions",
    "git_commit",
    "git_commit_dirty",
    "register_tool_version",
    "reset_git_probe",
    "registered_tool_versions",
    "reset_tool_versions",
    "software_commit",
    "software_version",
    "tool_versions",
]
