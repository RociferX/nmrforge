"""Dark theme readability guard (0.2.199-patch29hz review fix).

Background: the software enforces the dark theme (window #1e1e1e), but
historically many QSS still wrote the light theme's dark text (#2c3e50 ≈1.5:1,
#333 ≈1.6:1, #444 ≈2.1:1, #555 ≈2.4:1, #666 ≈2.9:1), which is effectively
invisible. This test solidifies "text colour must be readable" into a rule to
keep fallback wording from being mixed in again; semantic colours are
concentrated in ui_support/colors.py and the style sheets are in
ui_support/theme.py.
"""

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

# Text colour (excluding background-color and other attributes ending with "color:")
COLOR_RE = re.compile(r"(?<!background-)color:\s*(#[0-9a-fA-F]{3,6})")

# Text clearly drawn on a light background: white run details box, white inline
# naming editor, light yellow hint bubble.
ALLOWED_ON_LIGHT = {
    ("gui/pipeline_panel.py", "#222"),      # detail_frame white background
    ("gui/project_tree.py", "#222"),        # inline rename editor white background
    ("gui/pipeline_panel.py", "#935116"),   # hint_bubble light yellow background
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


#: The theme definition itself intentionally contains low-contrast disabled
#: colours, so no contrast check is made; interface files (including QSS) are all
#: in scope -- moving the style sheet away does not mean it is no longer checked.
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
    """Semantic colours must all reach 4.5:1 on the dark window background."""
    for color in (TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED):
        assert _contrast(color, WINDOW_BACKGROUND) >= 4.5, color
    assert _contrast(TEXT_MUTED, WINDOW_BACKGROUND) > _contrast(
        "#666666", WINDOW_BACKGROUND
    )


def test_no_dark_text_on_dark_theme() -> None:
    """gui/ and viewer/ must no longer contain low-contrast text on dark windows."""
    offenders = []
    for rel, lineno, color, line in _iter_text_colors():
        if (rel, color) in ALLOWED_ON_LIGHT:
            continue
        ratio = _contrast(color, WINDOW_BACKGROUND)
        if ratio < 4.5:
            offenders.append(f"{rel}:{lineno} {color} ({ratio:.2f}:1) {line}")
    assert not offenders, "暗色主题下的低对比文字:\n" + "\n".join(offenders)


def test_retired_light_theme_colors_are_gone() -> None:
    """Dark text literals from the historical light theme must not come back."""
    retired = {"#2c3e50", "#333333", "#444444", "#555555", "#666666"}
    found = [
        f"{rel}:{lineno} {color}"
        for rel, lineno, color, _line in _iter_text_colors()
        if color in retired
    ]
    assert not found, "回退的深色文字: " + ", ".join(found)


def test_tree_icon_uses_theme_color() -> None:
    """The tree icon colour comes from the theme (was hardcoded #2c3e50, which is
    invisible on dark trees)."""
    source = (ROOT / "gui" / "project_tree.py").read_text(encoding="utf-8")
    assert 'painter.setPen(QColor(TEXT_SECONDARY))' in source
    assert 'QColor("#2c3e50")' not in source
