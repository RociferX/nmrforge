"""Locations of the icons and theme images (Qt-free).

Kept separate from :mod:`ui_support.theme` so that path logic can be tested, and reused, without a
Qt binding. Development and frozen (AppImage) layouts are both handled through
``core.app_paths.resource_path``.
"""

from __future__ import annotations

from pathlib import Path

#: Application icon candidates, in priority order.
_ICON_NAMES = ("nmrforge.png",)

#: Images the theme's QSS refers to.
_THEME_IMAGES = ("spin_up.png", "spin_down.png")


def gui_assets_dir() -> Path | None:
    """``gui/assets`` (development) or its frozen equivalent; None when it cannot be resolved."""
    try:
        from core.app_paths import resource_path

        candidate = Path(resource_path("gui")) / "assets"
    except Exception:  # noqa: BLE001 - frozen/partial installs fall back to the source layout
        candidate = Path(__file__).resolve().parent.parent / "gui" / "assets"
    return candidate if candidate.is_dir() else None


def app_icon_path() -> Path | None:
    """Path of the application icon, or None if no candidate exists."""
    candidates: list[Path] = []
    assets = gui_assets_dir()
    if assets is not None:
        candidates.extend(assets / name for name in _ICON_NAMES)
    candidates.append(
        Path(__file__).resolve().parent.parent
        / "packaging"
        / "linux"
        / "icons"
        / _ICON_NAMES[0]
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def theme_images_dir() -> Path | None:
    """Directory holding the QSS-referenced images, or None when they are missing."""
    assets = gui_assets_dir()
    if assets is None:
        return None
    if all((assets / name).is_file() for name in _THEME_IMAGES):
        return assets
    return None
