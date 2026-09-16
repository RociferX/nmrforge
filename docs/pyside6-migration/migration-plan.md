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
| 0 | Qt dependency audit; core Qt-independence guard test | this branch: audit written, guard test green |
| 1 | Create an isolated environment with PySide6 installed; record the real wheel metadata (licences, versions) | `import PySide6` works, licence metadata captured in `THIRD_PARTY.md` |
| 2 | Add `qtcompat`, move the ~44 symbols and the 101 `pyqtSignal` sites behind it; force `PYQTGRAPH_QT_LIB` | GUI starts under PySide6; no module imports PyQt6 in that environment |
| 3 | Port the tests (30 files) and the `QApplication` fixture to `qtcompat` | full suite green in the PySide6 environment |
| 4 | Port packaging: `NMRForge.spec` hiddenimports, AppImage smoke test, desktop integration | AppImage builds and starts on a clean machine |
| 5 | Remove the PyQt6 path from `qtcompat`, drop `PyQt6` from dependencies, delete the leftover `.measure_gap.py`, update the docs that name PyQt6, then merge to `master` | all gates in section 5 pass, on `master` |

Stage 1 cannot be started in this environment: PySide6 is not installed and the sandbox has no
network access. Stages 2-5 are therefore planned, not attempted.

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

## 6. Licensing work that follows the migration

The migration **replaces one licensing problem with a smaller one**; it does not end the analysis.

1. Record the real licence metadata of the installed `PySide6`, `shiboken6` and the Qt libraries they
   ship, exactly as `THIRD_PARTY.md` does today for PyQt6.
2. Rerun the **full** third-party audit (every runtime dependency, not just Qt) before recommending
   MIT, BSD-3-Clause or Apache-2.0. The conclusion must be written into `LICENSE_OPTIONS.md` and
   `THIRD_PARTY.md`, and the licence decision remains the repository owner's.
3. Settle the LGPL question for the distribution shape: a single-file AppImage bundles Qt. Meeting
   the LGPL's library-replacement obligation for a bundled Qt inside a read-only squashfs needs an
   explicit answer (licence texts, ability to relink, or shipping Qt/PySide6 sources), and that is a
   legal question, not a packaging preference.
4. Only after 1-3: revisit `pyproject.toml`'s licence field, add the `LICENSE` file, and update
   `README.md`, `CITATION.cff` and `.zenodo.json`.

## 7. What this branch does and does not do

Done on this branch:

- the Qt dependency audit (evidence-based, with counts and queries);
- this plan, including the target architecture, the stages and the acceptance gates;
- `tests/test_qt_independence.py`, which locks the property the target architecture rests on.

Deliberately **not** done:

- no PyQt6 removal, no dependency change, no code change in `gui/` or `viewer/`;
- no `qtcompat` module yet (Stage 2 depends on Stage 1, which needs PySide6 installed);
- no test modified or ported;
- no `LICENSE` added, no licence recommended - the third-party audit must be rerun first;
- nothing merged to `master`, and nothing pushed to any remote.