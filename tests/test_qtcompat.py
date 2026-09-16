"""Tests for the Qt binding boundary.

``qtcompat`` is the one module allowed to name a Qt binding, so its decision logic is tested
directly: which binding is chosen, that only one is ever loaded, that the signal/slot/property
names are normalised, and that the ambiguous or destructive cases fail loudly instead of guessing.
"""

from __future__ import annotations

import os
import sys

import pytest

import qtcompat


def test_binding_is_supported() -> None:
    assert qtcompat.BINDING in qtcompat.SUPPORTED
    assert qtcompat.BINDING_MODULE == qtcompat.BINDING


def test_submodules_are_the_selected_binding() -> None:
    for name in ("QtCore", "QtGui", "QtWidgets", "QtTest"):
        module = getattr(qtcompat, name)
        assert module.__name__ == f"{qtcompat.BINDING}.{name}", name


def test_submodules_are_registered_so_from_imports_work() -> None:
    """``from qtcompat.QtWidgets import QLabel`` must resolve to the binding's own module."""
    assert sys.modules[f"{qtcompat.__name__}.QtWidgets"] is qtcompat.QtWidgets

    from qtcompat.QtCore import Qt
    from qtcompat.QtWidgets import QLabel

    assert Qt is qtcompat.QtCore.Qt
    assert QLabel is qtcompat.QtWidgets.QLabel


def test_exactly_one_binding_is_loaded_in_this_process() -> None:
    loaded = {
        name.split(".")[0] for name in sys.modules if name.startswith(("PyQt", "PySide"))
    }
    assert loaded == {qtcompat.BINDING}, loaded


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


def test_selection_refuses_an_unsupported_forced_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(qtcompat.ENV_VAR, "Qt5")
    with pytest.raises(RuntimeError, match="not a supported Qt binding"):
        qtcompat._select()


def test_selection_refuses_a_forced_binding_that_is_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = "PySide6" if qtcompat.BINDING == "PyQt6" else "PyQt6"
    monkeypatch.setenv(qtcompat.ENV_VAR, other)
    monkeypatch.setattr(qtcompat, "_importable", lambda name: False)
    with pytest.raises(RuntimeError, match="not installed"):
        qtcompat._select()


def test_selection_refuses_to_guess_when_both_are_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two bindings in one process crashes the interpreter, so this must raise, not choose."""
    monkeypatch.delenv(qtcompat.ENV_VAR, raising=False)
    monkeypatch.setattr(qtcompat, "_importable", lambda name: True)
    with pytest.raises(RuntimeError, match="Both"):
        qtcompat._select()


def test_selection_reports_what_to_install_when_nothing_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(qtcompat.ENV_VAR, raising=False)
    monkeypatch.setattr(qtcompat, "_importable", lambda name: False)
    with pytest.raises(ImportError, match="pip install"):
        qtcompat._select()


def test_contradictory_pyqtgraph_variable_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    other = "PySide6" if qtcompat.BINDING == "PyQt6" else "PyQt6"
    monkeypatch.setenv(qtcompat.PYQTGRAPH_ENV_VAR, other)
    with pytest.raises(RuntimeError, match="contradicts"):
        qtcompat._force_pyqtgraph_binding(qtcompat.BINDING)


def test_explicit_selection_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(qtcompat.ENV_VAR, qtcompat.BINDING)
    monkeypatch.setattr(qtcompat, "_importable", lambda name: name == qtcompat.BINDING)
    assert qtcompat._select() == qtcompat.BINDING
