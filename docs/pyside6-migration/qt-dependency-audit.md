# Qt dependency audit (PySide6 migration feasibility)

Purpose: establish, with evidence, exactly how much of nmrForge depends on Qt, which Qt binding it
depends on, and what specifically has to change for the target architecture to hold:

> a **Qt-independent computational core**, with the GUI the only layer that depends on Qt, and that
> dependency being **PySide6**.

Branch: `codex/pyside6-migration-feasibility`. Nothing in this audit changes behaviour; `master`
still carries PyQt6 and is untouched.

## 1. Method

All counts were produced from the tracked working tree with `git grep`, on the committed state of
this branch, using these queries (abbreviated):

- import sites: `git grep -n -E "^\s*(from|import)\s+PyQt6" -- '*.py'`
- Qt submodule use: `git grep -h -o -E "PyQt6\.[A-Za-z0-9_]+" -- '*.py'`
- imported symbol set: parse the `from PyQt6.X import a, b, c` lines, deduplicate
- PyQt-specific APIs: counts for `pyqtSignal`, `pyqtSlot`, `pyqtProperty`, `sip.`, `QVariant`,
  `pyqtConfigure`, `exec_`, `QThread`, `QRunnable`, `QThreadPool`
- risky class shapes: class definitions containing a Qt base **and** a comma (multiple inheritance)
- kwargs signal emission: `\.emit\([a-zA-Z_]+=`
- process-level binding check: import `core`, `backend`, `workflow`, `nmrforge_api` in a fresh
  interpreter and list loaded modules matching `PyQt*` / `PySide*`

## 2. Where Qt appears

| Layer | Files referencing PyQt6 | Qt import statements | Verdict |
| --- | --- | --- | --- |
| `core/` | 1 (metadata string only, see 2.1) | **0** | already Qt-free |
| `backend/` | 0 | 0 | Qt-free |
| `workflow/` | 0 | 0 | Qt-free |
| `nmrforge_api/` | 0 | 0 | Qt-free |
| `gui/` | 12 | 79 | Qt-bound |
| `viewer/` | 6 | 37 | Qt-bound |
| `tests/` | 30 | - | Qt-bound (GUI tests) |
| `.measure_gap.py` (repo root, unused leftover) | 1 | 1 | should be deleted |

Totals for the shipped UI layer: **18 files, 116 Qt import statements, 44 distinct Qt symbols,
about 15,300 lines of Python**, plus 101 `pyqtSignal` occurrences.

### 2.1 The single exception in the core

`core/version.py` lists `"PyQt6"` inside its `_DEPENDENCIES` tuple, which is used to record
dependency versions into run records. That is a **string, not an import** - verified by static scan
(no `import` statement) and at runtime (see 4). It still matters for the migration: the core names
the toolkit, so under the target architecture that entry becomes `PySide6`, or better, the GUI
toolkit version is recorded by the GUI layer instead of hard-coded in the core's dependency list.

## 3. Which Qt APIs are actually used

### 3.1 Submodules

| Submodule | Import-statement occurrences |
| --- | --- |
| `QtWidgets` | 69 |
| `QtCore` | 35 |
| `QtGui` | 15 |
| `QtTest` | 2 (test helper only) |

### 3.2 Distinct imported symbols (44)

```text
QtCore    QEvent  QEventLoop  QItemSelectionModel  QObject  QPoint  QPointF  QProcess
          QRect  QRectF  QSize  QSignalBlocker  Qt  QTimer  QUrl  pyqtSignal
QtGui     QAction  QColor  QDesktopServices  QDragEnterEvent  QDropEvent  QFont
          QFontMetrics  QIcon  QKeyEvent  QMouseEvent  QPainter  QPalette  QPen
          QPixmap
QtWidgets QAbstractItemDelegate  QApplication  QDialog  QFileDialog  QGroupBox
          QGuiApplication  QLabel  QMenu  QPushButton  QSplitter  QTreeWidgetItem
          QVBoxLayout  QWidget
QtTest    QTest
```

Notes on this list:

- `QAction` comes from `QtGui` (13 uses). `QtWidgets` is where a PyQt5-era codebase would have put
  it; this codebase is already Qt6-correct, and PySide6 also has it in `QtGui`. No change needed.
- `QGuiApplication` (6 uses) exists in PySide6 as well; it is used for offscreen/instance checks.
- Everything else in the list exists under the same name and the same module in PySide6.

### 3.3 PyQt-specific API surface

| API | Uses | PySide6 equivalent | Risk |
| --- | --- | --- | --- |
| `pyqtSignal` | **101** (13 files) | `Signal` | low - mechanical rename |
| `pyqtSlot` | **0** | `Slot` | none |
| `pyqtProperty` | 0 | `Property` | none |
| `sip` / `PyQt6.sip` | 0 | `shiboken6` | none |
| `QVariant` | 0 | n/a (implicit) | none |
| `pyqtConfigure` | 0 | n/a | none |
| `exec_()` | 0 (the `exec_` grep hits are `exec_module`) | `exec()` | none |
| `QThread` / `QRunnable` / `QThreadPool` | 0 | same names | none |

The signal signatures in use are all plain builtins, which both bindings accept:

```text
pyqtSignal(str) x46   pyqtSignal(str, str) x11   pyqtSignal() x10
pyqtSignal(str, str, str) x7   pyqtSignal(str, list, bool) x4
pyqtSignal(str, str, str, bool) x4   pyqtSignal(int) x2   pyqtSignal(object) x2
pyqtSignal(bool) x2   pyqtSignal(dict) x2   ... (remainder are 2-5 argument mixes)
```

### 3.4 PyQt/PySide behavioural gaps that are *absent* here

These are the usual reasons a PyQt-to-PySide6 port turns painful. None of them occur in this
codebase, which is the main reason this migration is feasible:

| Known porting hazard | Present? | Evidence |
| --- | --- | --- |
| Multiple inheritance with a Qt base class (PySide6 layout conflicts) | **no** | no class definition combines a Qt base with a comma-separated mixin among the Qt-base matches |
| Signal emission with keyword arguments (PyQt accepts, PySide6 rejects) | **no** | no `.emit(name=` matches |
| Subclass shadowing `QObject.property` | **no** | no `def property(` / `property =` in `gui/` or `viewer/` |
| `QVariant`-dependent container conversion | **no** | no `QVariant` usage |
| Qt Designer `.ui` / `.qrc` / `.qss` files | **no** | `git ls-files` has none |
| `sip`-specific casts or `sip.isdeleted` | **no** | no `sip` usage |
| matplotlib Qt canvas (second binding consumer) | **no** | only `matplotlib.use("Agg")` in `workflow/peak_align.py` |

## 4. The core is already Qt-independent (verified)

Static: no `import`/`from` of any Qt binding in `core/`, `backend/`, `workflow/`, `nmrforge_api/`.

Runtime, in a fresh interpreter:

```bash
python -c "import sys, core, nmrforge_api, backend, workflow; \
print([m for m in sys.modules if m.startswith(('PyQt','PySide'))])"
# -> []
```

This is the property the target architecture is built on, and it is worth locking with a test (this
branch adds `tests/test_qt_independence.py`, which also forbids a future change from quietly
importing Qt into the computational core).

## 5. pyqtgraph is the one shared consumer that chooses its own binding

`pyqtgraph` supports PyQt5/PyQt6/PySide2/PySide6 and picks for itself. From the installed
`pyqtgraph/Qt/__init__.py` (0.14.0):

1. if `PYQTGRAPH_QT_LIB` is set, that binding is used and nothing else is probed;
2. otherwise, whichever binding is **already in `sys.modules`** wins, searching in the order
   `PyQt6, PySide6, PyQt5, PySide2`;
3. otherwise it tries to import `PyQt6.QtCore`, then `PySide6.QtCore`, and so on.

Consequences for the migration - this is the highest-risk item in the whole audit:

- **A process must import exactly one binding.** Importing both in one process is a hard failure
  (two different Qt libraries in one address space), and pyqtgraph will happily create the second
  one if it does not find the first in `sys.modules`.
- On the migration branch, `viewer/contour_layer.py` and `viewer/nmr_viewbox.py` import Qt *through*
  `from pyqtgraph.Qt import ...`, so they inherit pyqtgraph's choice rather than making their own.
  `viewer/spectrum_viewer.py` also uses pyqtgraph.
- Therefore the migration must force the choice rather than rely on import order: set
  `PYQTGRAPH_QT_LIB=PySide6` for the GUI entry points (and in the AppImage environment), or import
  the chosen binding in a single compatibility module **before** pyqtgraph is imported anywhere.
- Three files import pyqtgraph; two of them take Qt symbols from `pyqtgraph.Qt`.

`matplotlib` is not a second consumer (Agg only, see 3.4).

## 6. Packaging impact

| Item | Current | After migration |
| --- | --- | --- |
| `pyproject.toml` runtime dependency | `PyQt6>=6.5` | `PySide6>=6.6` (version to be pinned against a real install) |
| `packaging/linux/NMRForge.spec` | `hiddenimports=["PyQt6.QtSvg"]` | `["PySide6.QtSvg"]` |
| PyInstaller hook for the binding | provided by `pyinstaller-hooks-contrib` for both bindings | unchanged mechanism |
| `core/version.py` dependency list | includes `"PyQt6"` | `"PySide6"`, or moved out of the core |
| AppImage size | Qt6 runtime bundled | roughly unchanged (`shiboken6` replaces `PyQt6-sip`) |
| `docs/packaging.md`, `docs/development.md`, `gui/__init__.py` docstring | name PyQt6 | must be updated in the same change |

Note that `hiddenimports=["PyQt6.QtSvg"]` should be re-checked during the port: the icon loading path
uses `QIcon` with an SVG asset, so the SVG image-format plugin must still be collected under
PySide6.

## 7. Layer-direction finding relevant to the target architecture

The GUI depends on the viewer (`gui/spectrum_panel.py`, `gui/notes.py`, `gui/pipeline_panel.py`
import `viewer.*`), which is the intended direction. There is exactly **one reverse edge**:

```text
viewer/app.py:285    from gui.theme import app_icon, apply_dark_theme
```

`gui.theme` is Qt-only code (icons, palette, stylesheet). So today `viewer` cannot be Qt-free even in
principle, and the two UI packages are mutually dependent. For the stated target architecture - Qt
only at the GUI boundary - this edge should be resolved, most naturally by moving the theme/icon
helpers into the shared Qt compatibility module (or a small `ui_support` package) that both `gui`
and `viewer` may depend on.

## 8. Leftovers and documentation to touch

- `.measure_gap.py` at the repository root imports PyQt6 and is referenced by nothing (already
  flagged for deletion in `PUBLIC_RELEASE_AUDIT.md`). It must not be carried into the migration.
- `gui/__init__.py`'s docstring says "GUI 层（PyQt6）".
- `docs/development.md` line 217 states a Qt event-API convention by naming PyQt6.
- `docs/packaging.md` names PyQt6 in the bundled-content list and in the Qt-plugin troubleshooting
  note.
- 30 test files import PyQt6 directly; the GUI tests build the `QApplication` fixture in
  `tests/conftest.py`.

## 9. What this audit could NOT verify

Honest limitations, so the feasibility assessment is not read as stronger than it is:

1. **PySide6 is not installed on this machine, and PySide6 could not be installed** (the sandbox has
   no network access). Every PySide6-side claim in this document is therefore based on the binding's
   documented API surface and on the symbol set actually used here - it is **not** a demonstrated
   run. Stage 1 of the plan is precisely "install PySide6 in an isolated environment and prove it".
2. The number of Qt call sites that behave *subtly* differently cannot be counted statically. The
   audit removes the known large hazards (3.4); it cannot prove there is none left.
3. The exact licence metadata of the PySide6 wheel (LGPL-3.0 vs GPL/commercial, and the shiboken6
   wheel's terms) must be read from the installed distribution during Stage 1, exactly as was done
   for PyQt6 in `THIRD_PARTY.md`.
4. GUI behaviour was not re-verified on this branch: the branch adds documentation and a guard test
   only, and the existing suite result (1127 passing on `master`) is unchanged by it.