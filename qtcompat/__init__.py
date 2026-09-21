"""The single place where a Qt binding is named: **PySide6**.

Every UI module imports its Qt names from here, so the binding is chosen exactly once per process:

    from qtcompat.QtWidgets import QApplication, QLabel   # Qt widgets
    from qtcompat.QtCore import Qt, QObject               # Qt core
    from qtcompat import Signal, Slot, Property           # normalised signal/slot/property names

Why PySide6 rather than PyQt6: PyQt6 is distributed under GPL-3.0-only (or a commercial licence),
which would force the whole project to be GPL. PySide6 is the Qt Company's own binding and is
offered under LGPL-3.0 as one of its options, which leaves the project's own licence an open
decision. See ``LICENSE_OPTIONS.md`` and ``THIRD_PARTY.md``; note that LGPL distribution
obligations still apply to any *binary* that bundles Qt.

Design rules (see ``docs/pyside6-migration/migration-plan.md``):

1. **One binding per process, and it is this one.** Importing PyQt6 and PySide6 into the same
   process is a hard failure, so nothing outside this module may name a binding at all;
   ``tests/test_qt_independence.py`` enforces that across the repository.
2. **The core never imports this module.** ``core/``, ``backend/``, ``workflow/`` and
   ``nmrforge_api/`` must stay importable on a machine with no Qt at all; the same test enforces
   it, statically and at runtime.
3. **The choice is forced, not inherited.** ``pyqtgraph`` picks a binding for itself, preferring
   whatever is already in ``sys.modules`` and otherwise probing PyQt6 first. This module therefore
   sets ``PYQTGRAPH_QT_LIB`` before anything can import pyqtgraph, and refuses to continue if a
   contradicting value was set, so the plotting layer cannot end up on a different binding than the
   widgets.
4. **The signal/slot/property names are normalised** to ``Signal``/``Slot``/``Property``.

If PySide6 is not installed, importing this module raises with the command to run. Only modules
under ``gui/``, ``viewer/`` and ``ui_support/`` may import it.
"""

from __future__ import annotations

import importlib
import os
import sys
from types import ModuleType
from typing import Final

__all__ = [
    "BINDING",
    "BINDING_MODULE",
    "BINDING_VERSION",
    "Property",
    "Signal",
    "Slot",
    "QtCore",
    "QtGui",
    "QtTest",
    "QtWidgets",
]

#: The binding this project uses. A single value, deliberately: supporting two bindings means one
#: process can end up loading two Qt libraries, and that crashes the interpreter.
BINDING: Final[str] = "PySide6"

#: Import name of the binding module (kept separate for callers that want to be explicit).
BINDING_MODULE: Final[str] = "PySide6"

#: pyqtgraph reads this at import time; setting it stops pyqtgraph from choosing for itself.
PYQTGRAPH_ENV_VAR: Final[str] = "PYQTGRAPH_QT_LIB"

#: Submodules that must be available.
_SUBMODULES: Final[tuple[str, ...]] = ("QtCore", "QtGui", "QtWidgets", "QtTest")


def _import_binding():
    try:
        return importlib.import_module(BINDING_MODULE)
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError(
            "nmrForge's user interface needs PySide6, which is not installed. "
            "Install it with:  pip install PySide6  "
            "(the computational core, the command line and nmrforge_api do not need it)"
        ) from exc


_BINDING = _import_binding()


def _binding_version() -> str:
    """Version string of the installed PySide6 (empty when it cannot be determined)."""
    for attribute in ("__version__",):
        value = getattr(_BINDING, attribute, "")
        if value:
            return str(value)
    try:
        import importlib.metadata as metadata

        return str(metadata.version(BINDING_MODULE))
    except Exception:  # noqa: BLE001 - metadata is a convenience, not a requirement
        return ""


BINDING_VERSION: Final[str] = _binding_version()


def _force_pyqtgraph_binding() -> None:
    """Make pyqtgraph use PySide6, or refuse to continue if that contradicts a user setting."""
    existing = os.environ.get(PYQTGRAPH_ENV_VAR, "").strip()
    if existing and existing != BINDING:
        raise RuntimeError(
            f"{PYQTGRAPH_ENV_VAR}={existing!r} contradicts the Qt binding this project uses "
            f"({BINDING!r}). pyqtgraph would then load a second Qt library into this process, "
            f"which crashes the interpreter. Clear it, or set it to {BINDING!r}."
        )
    os.environ[PYQTGRAPH_ENV_VAR] = BINDING


_force_pyqtgraph_binding()


def _submodule(name: str) -> ModuleType:
    module = importlib.import_module(f"{BINDING_MODULE}.{name}")
    # Register under this package too, so ``from qtcompat.QtWidgets import QLabel`` resolves to the
    # real binding module without a duplicate module object (pyqtgraph uses the same technique for
    # its own Qt shim).
    sys.modules[f"{__name__}.{name}"] = module
    return module


QtCore = _submodule("QtCore")
QtGui = _submodule("QtGui")
QtWidgets = _submodule("QtWidgets")
QtTest = _submodule("QtTest")


def _factory(name: str):
    """Resolve a normalised factory name (``Signal``, ``Slot``, ``Property``)."""
    try:
        return getattr(QtCore, name)
    except AttributeError as exc:  # pragma: no cover - only if the binding is broken
        raise RuntimeError(
            f"{BINDING_MODULE}.QtCore has no {name!r}; this PySide6 installation is not usable "
            "by nmrForge"
        ) from exc


Signal = _factory("Signal")
Slot = _factory("Slot")
Property = _factory("Property")
