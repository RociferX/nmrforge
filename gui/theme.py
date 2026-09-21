"""Deprecated re-export of the shared theme.

The implementation moved to the shared UI layer so that ``viewer/`` no longer has to import
``gui/`` (see ``docs/pyside6-migration/migration-plan.md``, option A):

- :mod:`ui_support.colors` - Qt-free semantic colours;
- :mod:`ui_support.assets` - Qt-free icon and image lookup;
- :mod:`ui_support.theme` - the palette, the global stylesheet, the icon and ``fit_combo_width``.

This shim keeps the old import path working for out-of-tree callers and for branches that have not
been rebased yet. New code must import ``ui_support.*`` directly. Deleting this file is part of
Stage 5 of the migration.
"""

from __future__ import annotations

from ui_support.assets import app_icon_path, gui_assets_dir, theme_images_dir
from ui_support.colors import (
    PANEL_BACKGROUND,
    PANEL_BORDER,
    STATUS_COLORS,
    SURFACE_ALT,
    TEXT_MUTED,
    TEXT_ON_LIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WINDOW_BACKGROUND,
)
from ui_support.theme import app_icon, apply_dark_theme, fit_combo_width

__all__ = [
    "PANEL_BACKGROUND",
    "PANEL_BORDER",
    "STATUS_COLORS",
    "SURFACE_ALT",
    "TEXT_MUTED",
    "TEXT_ON_LIGHT",
    "TEXT_PRIMARY",
    "TEXT_SECONDARY",
    "WINDOW_BACKGROUND",
    "app_icon",
    "app_icon_path",
    "apply_dark_theme",
    "fit_combo_width",
    "gui_assets_dir",
    "theme_images_dir",
]
