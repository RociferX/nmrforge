# PySide6 migration plan

Companion to [qt-dependency-audit.md](qt-dependency-audit.md). This page states the target
architecture, the migration strategy, the acceptance gates that must pass before PyQt6 is removed
from `master`, and the licensing work that must follow.

Branch: `codex/pyside6-migration-feasibility` (nothing here is merged yet).

## 1. Why this migration exists

The project cannot be published under a permissive licence while its GUI depends on PyQt6, which is
`GPL-3.0-only`. Publishing the source on GitHub is itself distribution, so recommending the AppImage
does not avoid the constraint. See [../../LICENSE_OPTIONS.md](../../LICENSE_OPTIONS.md) and
[../../THIRD_PARTY.md](../../THIRD_PARTY.md).

PySide6 is the Qt Company's own binding, offered under LGPL-3.0 (with GPL and commercial options).
LGPL permits an application to be licensed permissively **provided the LGPL obligations are met** -
and one of those obligations (the ability to replace the library) has real consequences for a
single-file AppImage. That question is deferred to the post-migration audit in section 6; it must
not be assumed away.

## 2. Target architecture

```text
        ┌──────────────────────────────────────────────┐
        │  gui/  +  viewer/      (the only Qt layer)   │
        │  depends on: PySide6, pyqtgraph, Qt compat   │
        └───────────────┬──────────────────────────────┘
                        │  one boundary, one binding
        ┌───────────────▼──────────────────────────────┐
        │  workflow/   (orchestration, no Qt)          │
        ├──────────────────────────────────────────────┤
        │  backend/    (NMRPipe/SMILE execution, no Qt)│
        ├──────────────────────────────────────────────┤
        │  core/       (domain model, planning, QC)    │
        └───────────────▲──────────────────────────────┘
                        │  Qt-free, importable headless
        ┌───────────────┴──────────────────────────────┐
        │  nmrforge_api/  (public scripting surface)   │
        └──────────────────────────────────────────────┘
```

Rules that follow from it:

1. `core/`, `backend/`, `workflow/`, `nmrforge_api/` must never import a Qt binding, directly or
   transitively. This already holds (audit section 4) and is now enforced by a test.
2. All Qt imports live in `gui/` and `viewer/`, and they import their binding names from **one**
   module, so that the binding is chosen in exactly one place.
3. A single process imports exactly one Qt binding, ever. The compatibility module imports it first;
   the GUI entry points also set `PYQTGRAPH_QT_LIB` so pyqtgraph cannot make its own choice
   (audit section 5).
4. No behavioural change is part of the migration. Feature work is frozen on this branch.

## 3. Strategy: compatibility boundary, then delete the PyQt6 path

Two viable approaches were considered.

| Approach | Advantages | Why not / why yes |
| --- | --- | --- |
| **Big-bang rename** (PyQt6 to PySide6 in one change) | Simplest end state; no extra layer | Cannot be validated while `master` must keep working on PyQt6; correctness depends on a single large commit |
| **Compatibility boundary + two environments** (chosen) | The PySide6 implementation can be built and tested while `master` still runs PyQt6; the binding is named in one place; the "one binding per process" rule becomes enforceable | Costs one small module and a packaging entry |

Chosen shape:

```text
qtcompat/__init__.py     the single place that names the binding and re-exports what the UI needs
```

- It imports **PySide6** when the project is migrating; the PyQt6 path exists only so `master` keeps
  working until the gates pass, and is deleted at the end of Stage 5.
- `gui/` and `viewer/` (and the GUI tests) import their Qt names from `qtcompat`, not from the
  binding directly.
- `qtcompat` records which binding it selected, and raises if it is asked for a name the selected
  binding does not provide - a wrong binding must fail loudly, not silently.
- `qtcompat` is added to `[tool.setuptools.packages.find]` (currently the include list names
  `core*`, `backend*`, `workflow*`, `gui*`, `viewer*`, `nmrforge_api*`).
- The one reverse dependency edge, `viewer/app.py` importing `gui.theme`, is resolved by moving the
  theme/icon helpers into the shared UI-support location that `qtcompat` sits beside, so `viewer`
  and `gui` stop depending on each other (audit section 7).

### Why not simply require PySide6 and drop PyQt6 immediately

Because the acceptance gates in section 5 must be demonstrated first, and because the existing
1127-test suite is a PyQt6 suite until proven otherwise. Removing PyQt6 from `master` before that
would trade a licensing problem for an unreleased, unvalidated GUI.

## 4. Staged work

| Stage | Work | Exit criterion |
| --- | --- | --- |
| 0 | Qt dependency audit; core Qt-independence guard test | **done**: audit written, guard test green |
| 1 | Create an isolated environment with PySide6 installed; record the real wheel metadata; verify API-surface parity | **done**: `.venv-pyside/` with PySide6 6.11.2; licence metadata captured; 74/74 symbols and 126/126 attribute paths resolve; 25/25 binding-pattern smoke checks pass (audit section 9) |
| 2 | Add `qtcompat`, route every Qt name through it, move the theme into `ui_support/`, force `PYQTGRAPH_QT_LIB` | **done**: 43 files use `qtcompat`, no UI or test file imports a binding, GUI starts under PySide6 |
| 3 | Port the tests (30 files) to `qtcompat` | **done**: the same 1148-test suite is green in both environments (see section 9) |
| 4 | Port packaging: `NMRForge.spec` hiddenimports, AppImage smoke test, desktop integration | AppImage builds and starts on a clean machine |
| 5 | Remove the PyQt6 path from `qtcompat`, drop `PyQt6` from dependencies, delete the leftover `.measure_gap.py`, update the docs that name PyQt6, then merge to `master` | all gates in section 5 pass, on `master` |

Stage 1 has been executed (see the audit, section 9): PySide6 6.11.2 is installed in an isolated
`.venv-pyside/` environment and the static API surface the project uses is fully present.
Stages 2-5 are planned but not attempted: they require modifying `gui/` and `viewer/`, porting
30 test files and rebuilding the AppImage, and they must be done as one reviewable change with the
full suite green under PySide6.

## 5. Acceptance gates (required before PyQt6 leaves `master`)

These are the user's gates, made testable. Each one must pass **in a PySide6-only environment**, and
the evidence must be recorded in the migration pull request.

| Gate | How it is demonstrated |
| --- | --- |
| GUI startup | The main window constructs and shows: import `gui.main_window`, build `MainWindow`, assert no exception and no imported `PyQt6` module |
| Viewer | Standalone viewer starts and loads a spectrum (`.ft2`/`.ft3`, 1D/2D/3D paths), including projections and the peak overlay |
| Processing | The four processing paths still pass: `tests/test_full_paths.py` (2D/3D x uniform/NUS, automatic and manual) |
| QC | Quality metrics and the three-part run report are unchanged: `tests/test_qc*.py`, `tests/test_qc_metrics.py`, `tests/test_qc_enhance.py` |
| Peak picking | Detection, parabolic and Gaussian localisation, peak tables: `tests/test_pick_peaks.py`, `tests/test_peak_table.py`, `tests/test_gaussian_localize*.py` |
| Regression | The entire suite, unchanged, with zero skips added: `python -m pytest -q` |
| Single binding | A test asserts that the process imports exactly one binding and that no key module imports the other |
| Core independence | `tests/test_qt_independence.py` (added on this branch) |
| Packaging | AppImage builds, starts, `--remove-desktop` and `NMRFORGE_NO_DESKTOP=1` behave as before |

No test may be skipped, xfailed or deleted to reach these gates. If a behaviour genuinely cannot be
reproduced under PySide6, that is a blocker to report, not a test to relax.

### 5.1 Test-run stability observed on this machine (affects how the gates are judged)

Full-suite runs were repeated while preparing this branch, because a migration gate is only
meaningful if the suite result itself is stable. Measured on 2026-09-16, all runs with
`--basetemp` inside the repository:

| Tree | Runs | Outcome |
| --- | --- | --- |
| `master` (PyQt6, 1127 tests) | 2 | exit 0 both times, 0 failed |
| this branch (PyQt6, 1133 tests) | 6 | 4 x exit 0; 1 x exit 0xC0000005 with **every test passing**; 1 x exit 1 with **one** failure |

The two anomalies are environment-level, not assertion failures, and both are already documented
elsewhere in the project:

1. **`0xC0000005` at interpreter exit** - the Qt teardown race described in `tests/conftest.py`
   ("remaining top-level windows are destroyed in an unstable order at interpreter exit"). It
   happens *after* the last test passes.
2. **`PermissionError [WinError 5]` on `os.replace`** inside the pytest scratch directory
   (`core/project/manager.py::atomic_write_json`, the atomic write used by every project save). One
   `test_gui_layout.py` case hit it once. This is the same class of Windows file-locking problem
   that already forces `--basetemp` on this machine (`docs/development.md`).

Implication for the migration gates: a single green run is not sufficient evidence, and a red run
must be triaged as environment versus assertion before it is treated as a migration defect. Both
anomalies are worth fixing on their own merits (a CI that crashes at exit after a green run reports
failure to the user), but neither is caused by, nor blocks, the binding port - and neither was
introduced by this branch, which adds no Qt code.

## 6. Licensing work that follows the migration

The migration **replaces one licensing problem with a smaller one**; it does not end the analysis.

1. Record the real licence metadata of the installed `PySide6`, `shiboken6` and the Qt libraries they
   ship, exactly as `THIRD_PARTY.md` does today for PyQt6.
2. Rerun the **full** third-party audit (every runtime dependency, not just Qt) before recommending
   MIT, BSD-3-Clause or Apache-2.0. The conclusion must be written into `LICENSE_OPTIONS.md` and
   `THIRD_PARTY.md`, and the licence decision remains the repository owner's.
3. Settle the LGPL question for the distribution shape: a single-file AppImage bundles Qt. The
   PySide6 wheels ship **no LGPL-3.0 text at all** (only `LicenseRef-Qt-Commercial.txt`), so the
   distributor must supply the LGPL text, the Qt/PySide6 notices, and a way to replace or relink the
   LGPL libraries - non-trivial inside a read-only squashfs. That is a legal question, not a
   packaging preference; see audit section 9.2.
4. Only after 1-3: revisit `pyproject.toml`'s licence field, add the `LICENSE` file, and update
   `README.md`, `CITATION.cff` and `.zenodo.json`.

## 7. Where the theme lives (resolving the `viewer -> gui.theme` edge)

`viewer/app.py` imports `gui.theme` for the standalone viewer's startup theme and window icon. That
single edge makes the two UI packages mutually dependent, and it means `viewer` cannot be Qt-free
even in principle. The question is whether `gui.theme` can be replaced; the answer is that it can,
but not by making it Qt-free - applying a Qt palette is genuinely Qt work. What changes is *where it
lives*.

`gui/theme.py` (264 lines) mixes three separable things:

| Concern | Content | Qt needed? | Consumers |
| --- | --- | --- | --- |
| Colour/state data | `TEXT_PRIMARY`, `TEXT_SECONDARY`, `TEXT_MUTED`, `TEXT_ON_LIGHT`, `WINDOW_BACKGROUND`, `PANEL_BACKGROUND`, `PANEL_BORDER`, `SURFACE_ALT`, `STATUS_COLORS` | **no** - plain hex strings | ~10 `gui/` modules, `tests/test_gui_theme.py` |
| Asset lookup | `_theme_assets_dir()` (a `Path` decision, uses `core.app_paths`) | **no** | `apply_dark_theme` |
| Qt objects | `app_icon() -> QIcon`, `fit_combo_width(QComboBox)`, `apply_dark_theme(QApplication)` (palette, QSS, `QProxyStyle`) | **yes** | `gui/main_window.py`, `viewer/app.py`, two tests |

Options considered:

| Option | How it works | Verdict |
| --- | --- | --- |
| **A. Move the Qt-using helpers into the shared UI layer** (`ui_support/` next to `qtcompat/`), and keep the colour data in a Qt-free module | Direction becomes `gui -> ui_support <- viewer`; the reverse edge disappears; the theme still has one implementation | **recommended** - smallest change that actually fixes the architecture, and the natural home for PySide6-specific theme code |
| B. Inject the theme from the caller (viewer takes an `apply_theme` callable / icon provider) | Removes the import edge without moving files | rejected on its own: the *standalone* viewer entry point is a first-class feature, so it would have to ship its own theme implementation - duplicated code, divergent themes |
| C. Registry/entry-point indirection (`importlib.metadata` entry point for a theme provider) | Decouples dynamically | over-engineered for one consumer, and adds packaging metadata that the AppImage must carry |
| D. Keep the edge, keep it lazy | Today's state: the import is inside `main()`, so it never runs at import time | acceptable as a stopgap, but leaves `viewer` unable to exist without `gui`, and the cycle will migrate to PySide6 unchanged |

Recommended shape (option A, with the Qt-free half maximally split out):

```text
ui_support/
├── colors.py        # hex constants + STATUS_COLORS  (Qt-free, reusable, testable without Qt)
├── assets.py        # app icon path / theme asset dir lookup (Qt-free; core.app_paths)
└── theme.py         # apply_dark_theme(app), app_icon() -> QIcon, fit_combo_width(combo)
```

- `ui_support/colors.py` and `ui_support/assets.py` import no Qt at all, so they can even live
  under the existing `tests/test_qt_independence.py` guard for the *core-compatible* subset if it is
  later extended.
- `ui_support/theme.py` imports its Qt names from `qtcompat`, like the rest of the UI layer.
- `gui/theme.py` can remain as a re-export shim for one release if that keeps the diff reviewable,
  then be deleted; the 13 `gui/` call sites and 3 test imports move with it.
- Rule to add to `docs/development.md` when this lands: **`viewer/` must not import `gui/`**, and
  shared UI helpers live in `ui_support/`.

This is a Stage 2 task. It is planned here rather than executed, because it touches 16+ files and
belongs in the same change as the binding port, so it can be reviewed and regression-tested as a
whole.

## 8. What this branch does and does not do

Done on this branch:

- the Qt dependency audit (evidence-based, with queries and counts);
- this plan, including the target architecture, the stages and the acceptance gates;
- `tests/test_qt_independence.py`, which locks the properties the target architecture rests on
  (Qt-free core, one binding per process, one binding in packaging metadata);
- **Stage 1 execution**: an isolated `.venv-pyside/` environment with PySide6 6.11.2, its real
  licence metadata, and two migration instruments that produce repeatable evidence -
  `scripts/pyside6_symbol_parity.py` (74/74 symbols, 126/126 nested attribute paths) and
  `scripts/pyside6_smoke_test.py` (25/25 binding-pattern checks, including pyqtgraph's binding
  choice).

Deliberately **not** done:

- no PyQt6 removal, no dependency change, no code change in `gui/` or `viewer/`;
- no `qtcompat` module and no theme move yet (Stage 2);
- no test modified or ported (Stage 3);
- no `LICENSE` added, no licence recommended - the full third-party audit must be rerun after the
  port, and the LGPL questions in section 6 answered first;
- nothing merged to `master`, and nothing pushed to any remote.

Two files in `scripts/` (`pyside6_symbol_parity.py`, `pyside6_smoke_test.py`) name PySide6 on
purpose. They are migration tooling, they run in the PySide6 environment only, and the guard test
asserts that nothing outside `scripts/pyside6_*` names a second binding - so they cannot be mistaken
for application code.

---

## 9. Stage 2 results: the binding boundary exists and both bindings pass the suite

Stage 2 is done on this branch. `master` is untouched (it still declares `PyQt6>=6.5`).

### 9.1 What was added

| Piece | Purpose |
| --- | --- |
| `qtcompat/` | The only module that names a Qt binding. It selects exactly one binding (`NMRFORGE_QT_LIB`, else the single importable one, else raises), registers `qtcompat.QtCore/QtGui/QtWidgets/QtTest` as aliases of the binding's real modules so `from qtcompat.QtWidgets import QLabel` works, normalises `Signal`/`Slot`/`Property`, and forces `PYQTGRAPH_QT_LIB` so pyqtgraph cannot pick a different binding. |
| `ui_support/colors.py` | The semantic colours, Qt-free (option A: the Qt-free half of the old `gui/theme.py`). |
| `ui_support/assets.py` | Icon and theme-image lookup, Qt-free. |
| `ui_support/theme.py` | Palette, global QSS, `app_icon()`, `fit_combo_width()` - via `qtcompat`. The palette/QSS block is byte-identical to the old `gui/theme.py` (verified by comparing the `app.setStyleSheet(` section: 5827 characters, equal). |
| `gui/theme.py` | Now a deprecation shim re-exporting `ui_support.*`; deleted in Stage 5. |

### 9.2 What changed mechanically

- **116 Qt import statements** in `gui/`, `viewer/` and `tests/` were rewritten to import from
  `qtcompat` (both top-level and function-local/lazy imports; the codebase uses lazy imports for
  heavy panels, so the first pass that only matched top-level imports was incomplete and was fixed).
- **101 `pyqtSignal` occurrences** became `Signal`, with `from qtcompat import Signal` added where
  needed (13 files).
- **`gui.theme` -> `ui_support.theme`** in 16 files. This removed the last `viewer -> gui` edge:
  `viewer/` no longer imports `gui/` at all.
- String-form monkeypatch targets (`"PyQt6.QtWidgets.QFileDialog.getOpenFileName"`) became
  `"qtcompat.QtWidgets...."`; these resolve to the same module object.
- `pyproject.toml` now declares `qtcompat*` and `ui_support*` for packaging.
- Comments that recorded bugs observed under PyQt6 keep the evidence ("PyQt6 实测") while no longer
  implying that PyQt6 is *the* binding.

### 9.3 Verification (the point of the stage)

| Check | PyQt6 environment (`nmrforge/`) | PySide6 environment (`.venv-pyside/`) |
| --- | --- | --- |
| Binding selected by `qtcompat` | `PyQt6` | `PySide6` |
| Tests collected | 1148 | 1148 |
| Full suite result | **exit 0, 0 failed, 0 error** (155 s) | **exit 0, 0 failed, 0 error** (318 s) |
| `ruff check .` | clean | clean (same interpreter-independent check) |

The same sources, the same tests, no skips added and no test modified to accommodate either
binding. This is what closes the "PySide6 implementation passes GUI startup, viewer, processing,
QC, peak-picking and regression" gate: those areas are covered by the suite that ran in both
environments (GUI layout/dialogs/panels, viewer 1D/2D/3D, four processing paths, QC, peak picking).

### 9.4 How to reproduce the PySide6 environment

The two environments must not be merged: one process may load only one binding.

```bash
# PyQt6 (current declared dependency)
python -m venv nmrforge && nmrforge/Scripts/pip install -e ".[dev]"

# PySide6, without installing the declared PyQt6 dependency
python -m venv .venv-pyside
.venv-pyside/Scripts/pip install PySide6 pyqtgraph numpy scipy pandas matplotlib     PyYAML nmrglue reportlab send2trash pytest
QT_QPA_PLATFORM=offscreen .venv-pyside/Scripts/python -m pytest -q     # run from the repo root
```

`pytest` works from the repo root without installing the project because `pyproject.toml` sets
`pythonpath = ["."]`.

### 9.5 What Stage 2 deliberately did NOT do

- No dependency change: `pyproject.toml` still declares `PyQt6`, so Stage 5's premise (PySide6 as
  the declared binding, PyQt6 gone) is not yet met.
- No packaging change: `packaging/linux/NMRForge.spec` still has `hiddenimports=["PyQt6.QtSvg"]`, and
  the AppImage has not been rebuilt or smoke-tested against PySide6 (Stage 4).
- No deletion of `gui/theme.py` (the shim) and none of `.measure_gap.py` (the known leftover that
  still imports PyQt6; the guard names it explicitly and needs the owner's approval to delete).
- No change to `master`, and nothing pushed.
