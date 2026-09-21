"""Semantic colours for the forced-dark theme (Qt-free).

The application forces a dark theme (window ``#1e1e1e``) regardless of the desktop setting, so
light-theme leftovers - ``#2c3e50`` (~1.5:1), ``#333`` (~1.6:1), ``#444`` (~2.1:1), ``#555``
(~2.4:1), ``#666`` (~2.9:1) - are effectively invisible. Those literals are banned in
``gui/`` and ``viewer/``; take the colour from here instead. ``tests/test_gui_theme.py`` enforces
both the contrast requirement and the ban.

This module imports nothing, deliberately: it is usable from code that must not touch Qt.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Text colour (contrast is calculated based on dark window background colour #1e1e1e).
# ---------------------------------------------------------------------------
TEXT_PRIMARY = "#e8e8e8"        # Text and title (≈13:1).
TEXT_SECONDARY = "#b0b0b0"      # Secondary description (≈7.7:1).
TEXT_MUTED = "#8a8a8a"          # Hint/Placeholder/Gray text(≈4.8:1).
# Text in the light panel (Details box on white background/Inline editor).
TEXT_ON_LIGHT = "#222222"
WINDOW_BACKGROUND = "#1e1e1e"   # Consistent with the Window colour of apply_dark_theme.

# ---------------------------------------------------------------------------
# Partition (panel) appearance: The four main partitions are distinguished by card background colour
# + border.
# ---------------------------------------------------------------------------
PANEL_BACKGROUND = "#202124"
PANEL_BORDER = "#33363b"
SURFACE_ALT = "#232326"         # Sheet/tree alternating rows.

# ---------------------------------------------------------------------------
# Status semantic colour (Pipeline step line icon and text, tree status).
# ---------------------------------------------------------------------------
STATUS_COLORS: dict[str, str] = {
    "SUCCESS": "#5cb85c",
    "OUTDATED": "#f0ad4e",
    "FAILED": "#e05c5c",
    "RUNNING": "#4fc1ff",
    "READY": "#4fc1ff",
    "LOCKED": "#9e9e9e",
}
