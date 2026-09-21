"""Shared UI support used by both ``gui/`` and ``viewer/``.

Split deliberately into a Qt-free half and a Qt-bound half:

- :mod:`ui_support.colors` - semantic colour constants (plain strings), no Qt;
- :mod:`ui_support.assets` - where the icons and theme images live, no Qt;
- :mod:`ui_support.theme` - turning those into a QPalette / QSS / QIcon, via ``qtcompat``.

``viewer/`` depends on this package, never on ``gui/``. Before this split, ``viewer/app.py``
imported ``gui.theme``, which made the two UI packages mutually dependent and meant the standalone
viewer could not exist without the project-management GUI.
"""

from __future__ import annotations

__all__: list[str] = []
