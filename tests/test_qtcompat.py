"""Tests for the Qt binding boundary.

``qtcompat`` is the one module allowed to name a Qt binding, so its behaviour is tested directly:
which binding it selects, that only one is ever loaded, that the signal/slot/property names are
normalised, that pyqtgraph is forced onto the same binding, and that a missing or contradictory
environment fails with an actionable message instead of a traceback from deep inside Qt.
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest

import qtcompat


def test_binding_is_pyside6() -> None:
    assert qtcompat.BINDING == "PySide6"
    assert qtcompat.BINDING_MODULE == "PySide6"
    assert qtcompat.BINDING_VERSION, "the installed PySide6 version should be recorded"


def test_submodules_are_the_binding() -> None:
    for name in ("QtCore", "QtGui", "QtWidgets", "QtTest"):
        module = getattr(qtcompat, name)
        assert module.__name__ == f"PySide6.{name}", name


def test_submodules_are_registered_so_from_imports_work() -> None:
    """``from qtcompat.QtWidgets import QLabel`` must resolve to the binding's own module."""
    assert sys.modules[f"{qtcompat.__name__}.QtWidgets"] is qtcompat.QtWidgets

    from qtcompat.QtCore import Qt
    from qtcompat.QtWidgets import QLabel

    assert Qt is qtcompat.QtCore.Qt
    assert QLabel is qtcompat.QtWidgets.QLabel


def test_exactly_one_binding_is_loaded_in_this_process() -> None:
    loaded = {name.split(".")[0] for name in sys.modules if name.startswith(("PyQt", "PySide"))}
    assert loaded == {"PySide6"}, loaded


def test_signal_slot_property_are_normalised_and_work() -> None:
    class Emitter(qtcompat.QtCore.QObject):
        progressed = qtcompat.Signal(str)
        finished = qtcompat.Signal(str, bool)

    received: list[object] = []
    emitter = Emitter()
    emitter.progressed.connect(received.append)
    emitter.finished.connect(lambda name, ok: received.append((name, ok)))
    emitter.progressed.emit("step 1")
    emitter.finished.emit("data_001", True)

    assert received == ["step 1", ("data_001", True)]
    assert callable(qtcompat.Slot)
    assert callable(qtcompat.Property)


def test_pyqtgraph_binding_is_forced_to_the_selected_binding() -> None:
    assert os.environ.get(qtcompat.PYQTGRAPH_ENV_VAR) == qtcompat.BINDING


def test_contradictory_pyqtgraph_variable_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A contradictory value would make pyqtgraph load a second Qt library into this process."""
    monkeypatch.setenv(qtcompat.PYQTGRAPH_ENV_VAR, "PyQt6")
    with pytest.raises(RuntimeError, match="contradicts"):
        qtcompat._force_pyqtgraph_binding()


def test_missing_binding_error_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(name: str):
        raise ImportError(name)

    monkeypatch.setattr(qtcompat.importlib, "import_module", _missing)
    with pytest.raises(ImportError) as excinfo:
        qtcompat._import_binding()
    message = str(excinfo.value)
    assert "pip install PySide6" in message
    assert "do not need it" in message


def test_import_module_helper_returns_the_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    """The happy path of the same helper (the real import) is exercised at module import."""
    assert qtcompat._import_binding().__name__ == "PySide6"


def test_binding_version_matches_importlib_metadata() -> None:
    version = importlib.metadata.version("PySide6")
    assert qtcompat.BINDING_VERSION == version
