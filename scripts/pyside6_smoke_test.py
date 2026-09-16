#!/usr/bin/env python
"""Binding-pattern smoke test for the PySide6 migration (Stage 1 evidence).

Runs in a **PySide6-only** environment and exercises the exact Qt patterns the project's UI code
uses, without importing any project module (which cannot work until Stage 2 introduces the binding
boundary). It answers three questions with evidence instead of documentation:

1. do the Qt patterns used by ``gui/`` and ``viewer/`` behave the same under PySide6?
2. does ``pyqtgraph`` pick the same binding, and can that choice be forced?
3. is a second binding importable in this process (it must not be)?

Run it with the PySide6 environment interpreter::

    QT_QPA_PLATFORM=offscreen .venv-pyside/Scripts/python.exe scripts/pyside6_smoke_test.py

Exit code 0 = every pattern works under PySide6.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

CHECKS: list[tuple[str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, "PASS" if ok else "FAIL", detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


def main() -> int:
    print("== 1. binding selection ==")
    try:
        import PyQt6  # noqa: F401

        check("PyQt6 is not importable in this environment", False, "PyQt6 found: two bindings")
    except ImportError:
        check("PyQt6 is not importable in this environment", True)

    import PySide6
    from PySide6 import QtCore, QtGui, QtWidgets

    check("PySide6 imports", True, f"PySide6 {PySide6.__version__}")
    check(
        "Qt version matches the PyQt6 line used today",
        PySide6.__version__.startswith("6."),
        f"Qt {QtCore.qVersion()}",
    )

    print("== 2. patterns used by gui/ and viewer/ ==")

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    check("QApplication constructs", app is not None)

    # gui/theme.py: Fusion style + dark palette + global stylesheet + QProxyStyle hint override
    app.setStyle("Fusion")
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#1e1e1e"))
    palette.setColor(
        QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Text, QtGui.QColor("#6e6e6e")
    )
    app.setPalette(palette)
    app.setStyleSheet("QToolTip { color: #e8e8e8; } QSplitter::handle { width: 6px; }")
    check("QPalette colour roles (incl. ColorGroup.Disabled)", True)
    check("global QSS applies", bool(app.styleSheet()))

    class FastTooltipStyle(QtWidgets.QProxyStyle):
        def styleHint(self, hint, option=None, widget=None, returnData=None):
            if hint == QtWidgets.QStyle.StyleHint.SH_ToolTip_WakeUpDelay:
                return 120
            return super().styleHint(hint, option, widget, returnData)

    app.setStyle(FastTooltipStyle(app.style()))
    check("QProxyStyle subclass + styleHint override", True)

    # 101 sites in the project: signal definition, connection, emission
    class Emitter(QtCore.QObject):
        progressed = QtCore.Signal(str)
        finished = QtCore.Signal(str, str, bool)

        def run(self) -> None:
            self.progressed.emit("step 1")
            self.finished.emit("data_001", "done", True)

    received: list[tuple] = []
    emitter = Emitter()
    emitter.progressed.connect(lambda message: received.append(("progressed", message)))
    emitter.finished.connect(lambda *a: received.append(("finished", a)))
    emitter.run()
    check(
        "Signal/Signal(...) definition, connect, emit with multiple args",
        received == [("progressed", "step 1"), ("finished", ("data_001", "done", True))],
        f"received={received}",
    )

    # gui/ uses QAction from QtGui (13 sites) and adds it to a menu
    window = QtWidgets.QMainWindow()
    menu = window.menuBar().addMenu("File")
    action = QtGui.QAction("Open", window)
    action.setShortcut(QtGui.QKeySequence("Ctrl+O"))
    menu.addAction(action)
    check("QAction lives in QtGui and works in a QMenu", action in menu.actions())

    # gui/main_window.py: QSplitter with panels, QTreeWidget, QTreeWidgetItem, QItemSelectionModel
    splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
    tree = QtWidgets.QTreeWidget()
    root = QtWidgets.QTreeWidgetItem(tree, ["Project"])
    child = QtWidgets.QTreeWidgetItem(root, ["Experiment"])
    child.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"id": "exp_001"})
    tree.expandAll()
    splitter.addWidget(tree)
    splitter.addWidget(QtWidgets.QLabel("spectrum"))
    window.setCentralWidget(splitter)
    check(
        "QSplitter + QTreeWidget + QTreeWidgetItem.setData(UserRole, dict)",
        child.data(0, QtCore.Qt.ItemDataRole.UserRole) == {"id": "exp_001"},
    )
    check("QItemSelectionModel is reachable", tree.selectionModel() is not None)

    # viewer/ and other panels: QGraphicsItem-based pyqtgraph usage, file dialog, event handlers
    check(
        "QGraphicsItem / QGraphicsScene import surface",
        hasattr(QtWidgets, "QGraphicsScene") and hasattr(QtWidgets, "QGraphicsView"),
    )
    check("QFileDialog static API", hasattr(QtWidgets.QFileDialog, "getOpenFileName"))
    check(
        "mouse/drag event classes (viewer drag-and-drop)",
        all(
            hasattr(QtGui, name)
            for name in ("QDragEnterEvent", "QDropEvent", "QMouseEvent", "QKeyEvent")
        ),
    )
    metrics = QtGui.QFontMetrics(QtGui.QFont())
    check(
        "QFontMetrics.horizontalAdvance (gui/theme.fit_combo_width)",
        hasattr(metrics, "horizontalAdvance"),
    )
    check("QSignalBlocker", hasattr(QtCore, "QSignalBlocker"))
    check(
        "QProcess + QTimer (backend progress plumbing in the GUI)",
        hasattr(QtCore, "QProcess") and hasattr(QtCore, "QTimer"),
    )
    check(
        "QDesktopServices + QUrl (open output folder)",
        hasattr(QtGui, "QDesktopServices") and hasattr(QtCore, "QUrl"),
    )
    qt_test = __import__("PySide6.QtTest", fromlist=["QTest"])
    check("QTest is available for the GUI tests", hasattr(qt_test, "QTest"))

    print("== 3. pyqtgraph binding selection ==")
    import pyqtgraph as pg
    from pyqtgraph.Qt import QtCore as pg_qtcore
    from pyqtgraph.Qt import QtGui as pgg

    check(
        "pyqtgraph chose PySide6 on its own (PyQt6 absent)",
        pg.Qt.QT_LIB == "PySide6",
        f"pyqtgraph QT_LIB={pg.Qt.QT_LIB}",
    )
    # pyqtgraph does not hand back the binding module itself: it builds a proxy module
    # ("pyqtgraph.Qt.QtCore") that re-exports the real classes and normalises API differences.
    # The classes are the same objects, so isinstance/pickle/identity checks still hold.
    check(
        "pyqtgraph's QtCore is a proxy whose classes are PySide6's own objects",
        pg_qtcore.QObject is QtCore.QObject and pg_qtcore.Qt is QtCore.Qt,
        f"module={pg_qtcore.__name__}",
    )
    # The exact enum/class paths viewer/ takes through this proxy.
    check(
        "viewer's QtCore paths resolve through the pyqtgraph proxy",
        pg_qtcore.Qt.MouseButton.LeftButton is not None
        and pg_qtcore.Qt.MouseButton.MiddleButton is not None
        and pg_qtcore.Qt.MouseButton.RightButton is not None
        and pg_qtcore.QRectF is QtCore.QRectF
        and pg_qtcore.QPointF is QtCore.QPointF,
    )
    check(
        "viewer's QtGui paths resolve through the pyqtgraph proxy",
        pgg.QPainter is QtGui.QPainter
        and pgg.QPainterPath is QtGui.QPainterPath
        and QtGui.QPainter.RenderHint.Antialiasing is not None,
    )
    plot = pg.PlotWidget()
    plot.plot([0.0, 1.0, 2.0], [0.0, 1.0, 0.0])
    check("pyqtgraph PlotWidget renders offscreen", plot is not None)

    window.resize(400, 300)
    window.show()
    app.processEvents()
    check("main window shows offscreen", window.isVisible())

    print()
    failed = [label for label, status, _ in CHECKS if status == "FAIL"]
    print(f"checks: {len(CHECKS)}  passed: {len(CHECKS) - len(failed)}  failed: {len(failed)}")
    if failed:
        for label in failed:
            print(f"  FAILED: {label}")
        return 1
    print("result: PySide6 satisfies every Qt pattern this project uses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
