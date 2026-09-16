"""The single place where a Qt binding is named.

Every UI module imports its Qt names from here, so the binding is chosen exactly once per process:

    from qtcompat.QtWidgets import QApplication, QLabel      # Qt widgets
    from qtcompat.QtCore import Qt, QObject               # Qt core
    from qtcompat import Signal, Slot, Property           # the names PyQt6 calls pyqtSignal/...

Design rules (see ``docs/pyside6-migration/migration-plan.md``):

1. **One binding per process.** Importing PyQt6 and PySide6 into the same process is a hard failure,
   so this module refuses to guess when both are importable.
2. **The core never imports this module.** ``core/``, ``backend/``, ``workflow/`` and
   ``nmrforge_api/`` must stay importable on a machine with no Qt at all;
   ``tests/test_qt_independence.py`` enforces that.
3. **The choice is forced, not inherited.** ``pyqtgraph`` picks a binding for itself, preferring
   whatever is already in ``sys.modules`` and otherwise trying PyQt6 first. This module therefore
   sets ``PYQTGRAPH_QT_LIB`` before anything can import pyqtgraph, so the plotting layer cannot end
   up on a different binding than the widgets.
4. **The signal/slot/property names are normalised.** PyQt6 spells them ``pyqtSignal``,
   ``pyqtSlot``, ``pyqtProperty``; PySide6 spells them ``Signal``, ``Slot``, ``Property``. Code in
   this repository always uses the normalised names.

Selection:

- ``NMRFORGE_QT_LIB`` forces a binding (``PyQt6`` or ``PySide6``); a name that cannot be imported
  is an error, never a fallback.
- Otherwise, if exactly one of the two is importable, it is used.
- If both are importable, this module raises instead of guessing: that situation is precisely how a
  process ends up with two Qt libraries loaded, and the resulting crash is far harder to diagnose
  than this exception.
- If neither is importable, it raises with the install command to run.
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
    "QtCore",
    "QtGui",
    "QtTest",
    "QtWidgets",
    "Property",
    "Signal",
    "Slot",
    "is_pyqt6",
    "is_pyside6",
]

#: Environment variable that forces a binding.
ENV_VAR = "NMRFORGE_QT_LIB"

#: pyqtgraph reads this at import time; setting it stops pyqtgraph from choosing for itself.
PYQTGRAPH_ENV_VAR = "PYQTGRAPH_QT_LIB"

#: Supported bindings, in the order they are tried when auto-detecting.
SUPPORTED: Final[tuple[str, ...]] = ("PySide6", "PyQt6")

#: Submodules that must be available on every supported binding.
_SUBMODULES: Final[tuple[str, ...]] = ("QtCore", "QtGui", "QtWidgets", "QtTest")

#: Binding-specific spelling of the signal/slot/property factories.
_FACTORY_NAMES: Final[dict[str, dict[str, str]]] = {
    "PyQt6": {"Signal": "pyqtSignal", "Slot": "pyqtSlot", "Property": "pyqtProperty"},
    "PySide6": {"Signal": "Signal", "Slot": "Slot", "Property": "Property"},
}


def _importable(name: str) -> bool:
    """True if the top-level module ``name`` can be imported (no side effects kept)."""
    try:
        importlib.import_module(name)
    except ImportError:
        return False
    return True


def _select() -> str:
    forced = os.environ.get(ENV_VAR, "").strip()
    if forced:
        if forced not in SUPPORTED:
            raise RuntimeError(
                f"{ENV_VAR}={forced!r} is not a supported Qt binding; "
                f"choose one of {', '.join(SUPPORTED)}"
            )
        if not _importable(forced):
            raise RuntimeError(
                f"{ENV_VAR}={forced!r} was requested but that binding is not installed. "
                f"Install it (pip install {forced}) or unset {ENV_VAR}."
            )
        return forced

    available = [name for name in SUPPORTED if _importable(name)]
    if len(available) == 1:
        return available[0]
    if not available:
        raise ImportError(
            "nmrForge's user interface needs a Qt binding, and neither "
            f"{' nor '.join(SUPPORTED)} is installed. Install one, for example: "
            f"pip install {SUPPORTED[0]}"
        )
    raise RuntimeError(
        "Both "
        + " and ".join(available)
        + " are importable. Only one Qt binding may be loaded in a process, and loading both "
        "crashes the interpreter, so this is not guessed automatically. "
        f"Set {ENV_VAR} to the binding this application should use (one of "
        f"{', '.join(SUPPORTED)})."
    )


def _force_pyqtgraph_binding(binding: str) -> None:
    """Make pyqtgraph use the same binding, or refuse to continue if that contradicts a user."""
    existing = os.environ.get(PYQTGRAPH_ENV_VAR, "").strip()
    if existing and existing != binding:
        raise RuntimeError(
            f"{PYQTGRAPH_ENV_VAR}={existing!r} contradicts the selected Qt binding "
            f"({binding!r}). Clear it, or set it to {binding!r}."
        )
    os.environ[PYQTGRAPH_ENV_VAR] = binding


BINDING: Final[str] = _select()
BINDING_MODULE: Final[str] = BINDING

_force_pyqtgraph_binding(BINDING)


def _submodule(name: str) -> ModuleType:
    module = importlib.import_module(f"{BINDING}.{name}")
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
    """Resolve a normalised factory name (``Signal``, ``Slot``, ``Property``) on the binding."""
    spelling = _FACTORY_NAMES[BINDING][name]
    try:
        return getattr(QtCore, spelling)
    except AttributeError as exc:  # pragma: no cover - a binding would have to be broken
        raise RuntimeError(
            f"{BINDING}.QtCore has no {spelling!r} (needed for {name!r}); "
            "this binding is not usable by nmrForge"
        ) from exc


Signal = _factory("Signal")
Slot = _factory("Slot")
Property = _factory("Property")


def is_pyside6() -> bool:
    """True when the PySide6 binding is in use."""
    return BINDING == "PySide6"


def is_pyqt6() -> bool:
    """True when the PyQt6 binding is in use."""
    return BINDING == "PyQt6"
