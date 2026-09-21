"""Deletion into the system trash (0.2.199-patch29ex): the system trash first, the in-app
trash as a fallback.

- system trash: send2trash (Linux ~/.local/share/Trash / the Windows Recycle Bin / the
  macOS Trash), which the user can restore from the system trash;
- fallback: move into the fallback_dir handed in by the caller (the in-app trash, which
  keeps the relative structure and avoids duplicate names), equally recoverable;
- data is never destroyed outright; restoring means moving the directory back to its
  original path, which ProjectManager.recover_trashed does automatically when a project is
  opened or refreshed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ui_support.i18n import tr


class TrashError(Exception):
    """The trash could not be written (the original is left untouched, nothing is deleted)."""


def _unique_target(fallback_dir: Path, rel: Path) -> Path:
    """A unique target path inside the fallback trash (a numbered suffix is added if taken)."""
    target = fallback_dir / rel
    if not target.exists():
        return target
    for i in range(1, 10000):
        candidate = fallback_dir / f"{rel.name}.trashed{i}"
        if not candidate.exists():
            return candidate
    raise TrashError(tr("trash target conflict: {p0}", p0=fallback_dir / rel))


def send_to_trash(path: Path, fallback_dir: Path, rel: Path | None = None) -> Path:
    """Move a file/directory into the system trash and return where it ended up.

    - system trash succeeded: the original path is returned (the real location is managed by
      the system);
    - fallback succeeded: the target path inside the fallback directory is returned;
    - the path does not exist: it comes back unchanged and nothing happens.
    """
    target = Path(path).resolve()
    if not target.exists():
        return target
    rel = rel or Path(target.name)
    try:
        from send2trash import send2trash
    except Exception:  # noqa: BLE001 - fall back when the dependency is missing
        send2trash = None
    if send2trash is not None:
        try:
            send2trash(str(target))
            return target
        except Exception:  # noqa: BLE001 - no desktop trash / across filesystems etc.
            pass
    fallback_dir.mkdir(parents=True, exist_ok=True)
    destination = _unique_target(fallback_dir, rel)
    shutil.move(str(target), str(destination))
    return destination


__all__ = ["TrashError", "send_to_trash"]
