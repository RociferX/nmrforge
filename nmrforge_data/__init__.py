"""Runtime resources that ship inside the package: the default config and the templates.

Layout (since 2026-09-21, "option A")::

    nmrforge_data/
      config/nmrforge.yaml        processing / SMILE / NMRPipe defaults
      config/nmrforge.local.yaml  machine-local override (git-ignored, never shipped)
      presets/*.yaml              experiment templates (experiment type, nuclei, hints)

Why a package: both used to sit in the repository root, outside any Python package, so
``pip install .`` and a wheel could not carry them and ``resource_path()`` could not find
them once installed (only an editable install worked). Inside a package the **relative
position is identical in a source checkout and in an installed copy**, so
``core.app_paths.resource_path("config/nmrforge.yaml")`` and ``resource_path("presets")``
resolve in both; the AppImage copies the same two directories into ``_MEIPASS`` through the
PyInstaller spec.

**Do not** write runtime output here: this package is read-only data. User configuration is
resolved by ``core.app_paths.local_config_path()`` and lands in ``~/.config/NMRForge/`` when
installed.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["package_root"]

PACKAGE_DIR = Path(__file__).resolve().parent


def package_root() -> Path:
    """The data package directory (same relative position in a checkout and installed)."""
    return PACKAGE_DIR
