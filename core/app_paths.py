"""Resource path resolution: identical for the development, installed and frozen
(AppImage/PyInstaller) layouts.

Since 2026-09-21 ("option A") the runtime resources live in a **data package**
(``nmrforge_data`` -- ``config/`` and ``presets/``) instead of bare directories in the
repository root:

- development: the resources are under the checked-out ``nmrforge_data/``;
- installed (wheel / ``pip install``): the same relative position inside
  ``site-packages/nmrforge_data/``;
- frozen (AppImage/PyInstaller): the spec's ``datas`` copies ``config/`` and ``presets/``
  into ``_MEIPASS/nmrforge_data``, so ``resource_root()`` points there and the callers'
  relative paths do not change.

``resource_path("config/nmrforge.yaml")`` therefore holds in all three forms. AppImage
packaging and the resource layout: see docs/packaging.md; the package itself:
``nmrforge_data/__init__.py``.
"""

from __future__ import annotations

import os
import sys
from importlib.resources import files
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
#: package holding the runtime data (default config and experiment templates)
DATA_PACKAGE = "nmrforge_data"


def is_frozen() -> bool:
    """Whether we are running inside a PyInstaller frozen bundle."""
    return bool(getattr(sys, "frozen", False))


def _data_package_root() -> Path:
    """The data package directory; falls back to the repository copy when it cannot be found."""
    try:
        return Path(str(files(DATA_PACKAGE)))
    except (ImportError, ModuleNotFoundError, TypeError, ValueError):
        return PROJECT_ROOT / DATA_PACKAGE


def resource_root() -> Path:
    """Return the resource root (the parent of config/ and presets/)."""
    if is_frozen():
        meipass = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
        # the spec keeps the data-package shape (_MEIPASS/nmrforge_data); older artefacts
        # degrade to its parent
        packaged = meipass / DATA_PACKAGE
        return packaged if packaged.is_dir() else meipass
    return _data_package_root()


def resource_path(relative: str) -> Path:
    """Return a path inside the shipped data (for example config/nmrforge.yaml, presets)."""
    return resource_root() / relative

def local_config_path(
    filename: str = "nmrforge.local.yaml", *, packaged: bool | None = None
) -> Path:
    """Return the user override config path shared by the GUI and the backend.

    The frozen/AppImage build uses the user config directory; a development
    workspace prefers config/ inside the data package and falls back to a dotfile in the
    home directory when that is not writable. ``packaged`` lets GUI tests pin the
    environment.
    """
    if packaged is None:
        packaged = is_frozen() or bool(os.environ.get("APPIMAGE"))
    if packaged:
        return Path.home() / ".config" / "NMRForge" / filename
    candidate = resource_path("config") / filename
    try:
        if os.access(candidate.parent, os.W_OK):
            return candidate
    except OSError:
        pass
    return Path.home() / f".{filename}"
