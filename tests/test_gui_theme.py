"""Dark theme readability guard (0.2.199-patch29hz review fix). Background: Software forces dark
theme (window #1e1e1e), but historically many QSS still write dark text in light theme (#2c3e50
≈1.5:1, #333 ≈1.6:1, #444 ≈2.1:1, #555 ≈2.4:1, #666 ≈2.9:1), which is actually invisible. This
test solidifies "text colour must be readable" into a rule to prevent fallback writing from
being mixed in again; semantic colors are concentrated in ui_support/colors.py, and style sheets
are in ui_support/theme.py."""

from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ui_support.colors import (  # noqa: E402
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WINDOW_BACKGROUND,
)

ROOT = Path(__file__).resolve().parent.parent

# Text colour (excluding background-colour and other attributes ending with "colour:").
COLOR_RE = re.compile(r"(?<!background-)color:\s*(#[0-9a-fA-F]{3,6})")

# Clearly draw text on a light background: white background run details box, white background inline
# naming editor, light yellow prompt bubble.
ALLOWED_ON_LIGHT = {
    ("gui/pipeline_panel.py", "#222"),      # detail_frame White background.
    ("gui/project_tree.py", "#222"),        # Inline rename editor white background.
    ("gui/pipeline_panel.py", "#935116"),   # hint_bubble Light yellow bottom.
}


def _channel(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    text = color.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    r, g, b = (int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# : The theme definition itself contains intentionally low-contrast disabled colors, and no contrast
# judgment is made;: Interface files (including QSS) are all within the scanning range -- Removing
# the style sheet does not mean that it is no longer checked.
THEME_DEFINITION_FILES = {"ui_support/theme.py"}


def _iter_text_colors():
    for pkg in ("gui", "viewer", "ui_support"):
        for path in sorted((ROOT / pkg).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel in THEME_DEFINITION_FILES:
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                for match in COLOR_RE.finditer(line):
                    yield rel, lineno, match.group(1).lower(), line.strip()


def test_theme_constants_are_readable() -> None:
    """Semantic colors must all be 4.5:1 on dark window backgrounds."""
    for color in (TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED):
        assert _contrast(color, WINDOW_BACKGROUND) >= 4.5, color
    assert _contrast(TEXT_MUTED, WINDOW_BACKGROUND) > _contrast(
        "#666666", WINDOW_BACKGROUND
    )


def test_no_dark_text_on_dark_theme() -> None:
    """Gui/ and viewer/ must no longer have low-contrast text on dark windows."""
    offenders = []
    for rel, lineno, color, line in _iter_text_colors():
        if (rel, color) in ALLOWED_ON_LIGHT:
            continue
        ratio = _contrast(color, WINDOW_BACKGROUND)
        if ratio < 4.5:
            offenders.append(f"{rel}:{lineno} {color} ({ratio:.2f}:1) {line}")
    assert not offenders, "Low contrast text in dark theme:\n" + "\n".join(offenders)


def test_retired_light_theme_colors_are_gone() -> None:
    """Dark text literals in historical light themes must not be rolled back."""
    retired = {"#2c3e50", "#333333", "#444444", "#555555", "#666666"}
    found = [
        f"{rel}:{lineno} {color}"
        for rel, lineno, color, _line in _iter_text_colors()
        if color in retired
    ]
    assert not found, "Dark text for fallback: " + ", ".join(found)


def test_tree_icon_uses_theme_color() -> None:
    """The colour of the tree icon is taken from the theme (originally hardcoded #2c3e50, which is
    not visible on dark trees)."""
    source = (ROOT / "gui" / "project_tree.py").read_text(encoding="utf-8")
    assert 'painter.setPen(QColor(TEXT_SECONDARY))' in source
    assert 'QColor("#2c3e50")' not in source
