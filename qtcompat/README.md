# qtcompat/

The **only** module in this repository allowed to name a Qt binding. Every other module imports
Qt through here, which keeps the choice of binding a one-file decision.

`__init__.py` resolves the binding once, forces `PYQTGRAPH_QT_LIB` so that pyqtgraph uses the same
one, and re-exports the submodules used by the project (`QtCore`, `QtGui`, `QtWidgets`, ...) under
stable names. Switching bindings means editing this file, not a hundred call sites. The project
currently uses PySide6; PyQt6 is no longer a dependency.

Enforced by:

- [`tests/test_qt_independence.py`](../tests/test_qt_independence.py) - exactly one module may
  import a binding, `ui/` and `tests/` may only reach Qt through `qtcompat`, and `viewer/` must
  not import `gui/`;
- [`tests/test_qtcompat.py`](../tests/test_qtcompat.py) - the compatibility surface itself.
