# Public release audit - nmrForge

Prepared for the repository owner. **Nothing has been published, pushed, tagged, released or
uploaded.** Every action below is local to this working copy.

## Current source-release decision (2026-09-17)

This document began as the preparation audit and retains its dated findings below. The current
decision supersedes its earlier blockers and distribution wording:

- v0.9.0 is a **source-only** release under Apache-2.0;
- the AppImage is deferred and is governed by `APPIMAGE_RELEASE_CHECKLIST.md`;
- the public repository is the filtered `publish/` history on `main`;
- an all-object audit now checks blobs, trees, commits, tags, ref names and unreachable objects;
- final engineering evidence is recorded in `RELEASE_CHECKLIST_v0.9.0.md` and this document's
  final-review addendum, while GitHub CI remains an external post-push gate.

No AppImage readiness statement elsewhere in this historical report applies to v0.9.0.

---

| | |
| --- | --- |
| Audit date | 2026-09-16 |
| Audited working tree | the maintainer's private working copy (Git branch `master`); renamed on 2026-09-17 |
| Audit baseline commit | `b9cc67dcb53b9d4336ecd68a26d6f6c22a5ac2be` (working tree dirty, as expected during preparation) |
| Software version in the tree | `0.2.199` (`core/__init__.py`, single source) |
| Task specification | the private release-preparation task record |
| Platform used for verification | Windows, project venv `nmrforge/` (Python 3.13.2), no NMRPipe installed |

**Summary: the repository is close to publishable on the engineering side, and blocked on legal /
privacy decisions. Do not change the repository visibility yet.**

| Category | Where |
| --- | --- |
| READY | section E (16 items) |
| WARNINGS | section F (12 items) |
| BLOCKERS | section G (5 items). G.2: the licence is **decided (source Apache-2.0; LGPL only for the bundled Qt/PySide6, 2026-09-16)** - what remains there is the owner's review of the AppImage's distribution obligations; G.1 and G.3-G.5 are unchanged |
| MANUAL ACTIONS | section H (12 items) |

---

## A. Repository inventory (Phase 0)

### A.1 Git state

| Item | Value |
| --- | --- |
| Current branch | `master` (single long-lived branch) |
| Commits | 1166 at the start of this audit; this preparation adds new commits on top |
| Tags | 1 - `archive/0.2.184-backup`. No release tags exist. |
| Working tree at audit start | clean |
| Remotes | `vm` = `ssh://<lab-user>@<lab-host>:<port>/home/<lab-user>/nmrforge.git` - a **private development VM bare repository, not GitHub**. No GitHub remote is configured. |
| History strategy | Preserved. No rebase, no squash, no history rewrite was performed or is recommended. |

### A.2 Size

| Item | Value |
| --- | --- |
| `.git` directory | 81.8 MiB (6627 loose objects 79.24 MiB + 1 pack of 1.90 MiB / 5622 objects) |
| Largest blob in history | 463,068 bytes (a `CHANGELOG.md` revision) |
| Working tree on disk | 829.9 MiB, of which the ignored development venv `nmrforge/` is the bulk |
| Clone size (packed, reachable) | ~2 MiB |
| Note | The 81.8 MiB is local loose-object overhead, not history bloat. `git gc` would shrink it; it does not affect what a clone downloads. |

### A.3 Largest tracked files

| Size (bytes) | Path | Kind |
| --- | --- | --- |
| 469,909 | `CHANGELOG.md` | text |
| 157,671 | `backend/nmrpipe_backend.py` | source |
| 119,931 | `docs/problems.md` | text |
| 111,665 | `docs/tasks/archive/2026-09-11-current-history-through-fix27.md` | text |
| 102,921 | `gui/pipeline_panel.py` | source |
| 102,515 | `tests/test_nmrforge_api.py` | source |
| 101,476 | `tests/test_gui_layout.py` | source |
| 95,208 | `gui/main_window.py` | source |

**No binary artefacts are tracked.** There is no commit containing raw NMR data, no `.zip`, no
`.tar`, no `.npy`, no compiled binary, and no third-party executable.

### A.4 Toolchain and dependencies

| Item | Value |
| --- | --- |
| Python requirement | `>=3.12` (`pyproject.toml`) |
| Python used for verification | 3.13.2 (project venv) |
| Runtime dependencies | **PySide6** (switched from PyQt6 in the Stage 5 migration, 2026-09-16), pyqtgraph, NumPy (`>=1.24,<2.5`), SciPy, pandas, Matplotlib, PyYAML, nmrglue, reportlab, send2trash |
| Optional groups | `test`, `dev`, `docs` (added during this preparation) |
| External, not pip-managed | NMRPipe, SMILE, Java (detected at runtime, never bundled) |
| Build backend | setuptools, `dynamic = ["version"]` from `core.__version__` |
| Distribution | Linux AppImage via PyInstaller (`packaging/linux/build_appimage.sh`). A wheel is explicitly **not** a supported distribution path (decision PACK-015). |

### A.5 Surfaces

| Surface | Present | Evidence |
| --- | --- | --- |
| GUI | yes | `gui/main_window.py`, `MainWindow.run()`; entry point `main.py` |
| CLI | yes | `python -m nmrforge_api --help` lists `init/reference/peaks/sweep/workflows/report/status`; `nmrforge-viewer` console script |
| Scripting API | yes | `nmrforge_api` (Qt-free; verified `import nmrforge_api` loads zero Qt modules - asserted for `PyQt*`, `PySide*` and `shiboken*`) |
| Viewer (1D/2D/3D) | yes | `viewer/` |

### A.6 Tests and checks

| Item | Value |
| --- | --- |
| Test modules | 99 (`tests/*.py`) |
| Collected tests | 1113 before this preparation added more |
| Result (Windows, no NMRPipe) | **1112 passed, 1 skipped, 0 failed** |
| `ruff check .` | clean (`All checks passed!`) |
| `ruff format --check .` | 191 of 330 files *would* be reformatted - advisory only, deliberately not enforced (see F.5) |
| NMRPipe required for the suite | **No.** The engine boundary is mocked, which is why hosted CI can gate pull requests. |
| Regression coverage of interest | four processing paths (`tests/test_full_paths.py`), sampling metadata conflict, peak localisation, QC metrics, GUI layout, packaging contract |

### A.7 Documentation

| Item | Value |
| --- | --- |
| Tracked files under `docs/` | 86 |
| `CHANGELOG.md` | 5918 lines; 16 dated "unreleased" entries |
| Internal management docs | `docs/manager/`, `docs/tasks/`, `docs/reviews/`, `docs/proposals/`, `docs/AGENT_PROMPTS.md`, `docs/HANDOVER.md`, `docs/PROJECT_STATUS.md` |
| Public-facing docs before this audit | none (the README and all docs were written for internal readers) |
| Public-facing docs after this audit | 12 new pages under `docs/` plus the root-level release documents |

### A.8 Packaging, version, licence, citation, CI

| Item | Before | After this preparation |
| --- | --- | --- |
| Version definition | `core/__init__.py::__version__` (already single-source) | unchanged; the `v0.9.0` bump is deferred to the release step |
| Packaging | AppImage (PyInstaller spec + build script + desktop/icon) | unchanged; contract still locked by a test |
| LICENSE | absent | **added 2026-09-16**: Apache-2.0 text + project notice (holder **Xuanfeng Li / 李宣锋** since 2026-09-17); `pyproject.toml` declares the same SPDX id and `authors`; LGPL stays confined to the AppImage's bundled Qt/PySide6 |
| CITATION | absent | `CITATION.cff` **usable**: author Xuanfeng Li, version 0.9.0, licence Apache-2.0; affiliation / repository URL / DOI still to add |
| CI | absent | `.github/workflows/ci.yml` + issue/PR templates |
| Security policy | absent | `SECURITY.md` (contact is a placeholder) |

### A.9 Raw data and third-party binaries

| Item | Finding |
| --- | --- |
| Raw NMR test data | **None.** `tests/fixtures/bruker/**` contains only text headers (`acqus`, `acqu2s`, `acqu3s`, `nuslist`), largest 0.4 KB. No `ser`, no `fid`, no `.ft2`/`.ft3`. |
| Third-party binaries or scripts | **None bundled.** No NMRPipe/SMILE binary, no copied macro database, no vendored third-party script. |
| Directory names | `tests/fixtures/bruker/{hsqc_2d,hsqc_small,hnca_3d,hnca_small,nus_2d,nus_3d,unknown_2d}` - synthetic, no sample identity. |
---

## B. Security and privacy audit (Phase 1)

### B.1 Method

- Pattern scan of the current tree for credentials (API keys, passwords, tokens, private keys,
  AWS/GitHub token shapes).
- Full-history scan for the same patterns (`git log -S` over all refs for `BEGIN RSA PRIVATE KEY`,
  `BEGIN OPENSSH PRIVATE KEY`, `AKIA`, `ghp_`).
- Scan of every path ever added in history for suspicious names (`.env`, `*.pem`, `*.key`,
  `id_rsa`, `*secret*`, `*credential*`, `*.sqlite`, ...).
- Largest-blob enumeration over all objects.
- Scan for developer absolute paths (`C:\`, `/home/...`, `/Users/...`, `OneDrive`, `~/Desktop`,
  `/mnt/x/`).
- Scan for unpublished identifiers, sample names and e-mail addresses.

### B.2 Results - no credentials

| Check | Result |
| --- | --- |
| Credentials in the current tree | **none found**. The many `token` matches are lexer/tokeniser variables, not auth tokens. |
| Credentials in the full history | **none found** (all four high-signal patterns returned nothing) |
| Suspicious filenames ever committed | **none** |
| E-mail addresses in tracked files | **none** |
| Git author identity | `Xuanfeng Li <330249944+RociferX@users.noreply.github.com>` (public history; no personal email published) |
| Private keys / `.env` / secret config | none present, and now ignored by `.gitignore` |

This is the strongest single result in the audit: the repository is clean of credentials, and it
appears to have always been.

### B.3 Findings

#### [BLOCKER] Unpublished sample and study identifiers in tracked files

| | |
| --- | --- |
| Files | `docs/proposals/external-api/001-parameter-sweep-api.md` (lines 305, 306, 335), `docs/tasks/2026-09-14-combination-independent-picking.md` (line 67) |
| Content | the real sample identifier `sampleA.fid`, and paths under `~/nmr-uncertainty/` |
| Problem | This is a named, apparently unpublished sample, plus the existence and directory layout of the separate uncertainty study. Publishing the source exposes both. |
| Recommended action | Owner decides per occurrence: keep (if the sample is public by then), or replace with a neutral descriptor such as `<uniform 2D HSQC dataset, 800 MHz, 15N 81.09 MHz>`. Do not delete silently; the evidence values are worth keeping in generic form. |

#### [BLOCKER] Internal dataset shorthand in shipped code comments

| | |
| --- | --- |
| Files | `backend/script_generator.py` (lines 8, 1021), `core/experiment/acquisition_mode_detector.py` (lines 78, 79, 97, 98), `core/processing/axes.py` (line 6), `presets/README.md`, `scripts/README.md` |
| Content | references such as `sampleA`, `sampleB`, `sampleC` used as the evidence that a macro or layout decision was validated |
| Problem | These are internal dataset/operator shorthands. They leak laboratory working practice and are meaningless to outside readers. (Not credentials, and not a security problem.) |
| Recommended action | Keep the technical reason, replace the identifier: "validated on a 3D NUS dataset whose `acqu3s` TD is written as 1" instead of naming the dataset. Bulk occurrences in `CHANGELOG.md` (89) and `docs/PROJECT_STATUS.md` (44) are historical records; decide separately whether historical logs get the same treatment. |

#### [WARNING] Internal development host and laboratory paths in docs

| | |
| --- | --- |
| Files | `.codex/AGENTS.md`, `docs/GIT_WORKFLOW.md`, `docs/HANDOVER.md`, `docs/manager/project_state.md`, `docs/AGENT_PROMPTS.md` |
| Content | `ssh://<lab-user>@<lab-host>:<port>/home/<lab-user>/nmrforge.git`, `~/Desktop/data/sample/...`, `~/NMRForge`, `~/nmrforge-test-artifacts/`, `/home/<lab-user>/pipe/nmrtxt/` |
| Problem | Not credentials (the host is reachable only from the developer's machine), but it documents the laboratory's internal workflow in a repository that would be public. `scripts/vm_*.py` also defaults to `/home/<lab-user>/...` paths. |
| Recommended action | Decide the boundary: (a) keep - it is honest about how the software is validated; (b) move the internal process docs out of the public repository; (c) keep the docs but replace host paths with placeholders. The executable `scripts/vm_*.py` are the strongest candidates for sanitising, because they are code, not history. |

#### [WARNING] `.codex/AGENTS.md` and internal agent-workflow documents are tracked

| | |
| --- | --- |
| Files | `.codex/AGENTS.md`, `.codex/backend/AGENTS.md`, `.codex/gui/AGENTS.md`, `docs/AGENT_PROMPTS.md` |
| Problem | These describe the project's internal multi-agent development process, including branch/ownership rules and the validation host. Harmless technically; unusual and potentially confusing in a public scientific repository. |
| Recommended action | Owner decision, same options as above. If kept, label them as internal process documentation in `docs/README.md`. |

#### [WARNING] Root-level development leftovers

| | |
| --- | --- |
| Files | `.measure_gap.py`, `.patch_smile_gap.py` |
| Problem | One-off development/debug scripts at the repository root. They are not referenced by any doc, test or import. |
| Recommended action | Recommend deletion (**not done** - deletions require your approval, per the task rules). Either delete them, or move them under `scripts/` with a header explaining their purpose. |

#### [INFO] Development artefacts that exist only in history

Earlier commits added and later removed files such as `_apply_patch_*.py`, `_commit_msg*.txt`,
`_commit_dev.txt`. They contain no secrets and their removal is already part of history. No
history rewrite is warranted (Phase 35 criteria: credentials, confidential data, copyrighted
binaries, enormous raw data, PII - none apply).

### B.4 Large-file classification (Phase 1.3)

| Class | Files | Decision |
| --- | --- | --- |
| `required_test_fixture` | `tests/fixtures/bruker/**` (19 text files, <= 0.4 KB each) | keep in version control |
| `example_data` | `examples/make_synthetic_dataset.py` output | generated locally, git-ignored, never committed |
| `development_artifact` | `.measure_gap.py`, `.patch_smile_gap.py`, `scripts/vm_*.py`, `.pytest_tmp/`, `.ruff_cache/`, `__pycache__/` | see the warnings above; caches are git-ignored |
| `should_not_be_public` | none found | - |

No file needed to be deleted, moved or history-rewritten for size reasons.

---

## C. Third-party dependencies and licensing (Phase 2-3)

### C.1 Inventory

Created: [THIRD_PARTY.md](THIRD_PARTY.md) - every runtime dependency (PySide6 after the migration,
pyqtgraph, NumPy,
SciPy, pandas, Matplotlib, PyYAML, nmrglue, reportlab, send2trash), the development dependencies
(pytest, Ruff), the external engines (NMRPipe, SMILE, Java), and an explicit list of what this
repository does **not** bundle.

Verified licence facts (from installed distribution metadata, 2026-09-16):

| Component | Declared licence |
| --- | --- |
| PySide6 6.11.2 | `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` (the current dependency) |
| PyQt6 6.11.0 | **`GPL-3.0-only`** (or commercial, from Riverbank) - **removed** in Stage 5 |
| pyqtgraph | MIT |
| NumPy / SciPy / pandas | BSD-3-Clause |
| Matplotlib | Matplotlib licence (PSF-derived, BSD-compatible) |
| PyYAML | MIT |
| nmrglue | BSD-3-Clause (New BSD) |
| reportlab | BSD-3-Clause |
| send2trash | BSD-3-Clause |
| pytest / Ruff | MIT |
| NMRPipe / SMILE | external, free-of-charge research software; not bundled, not redistributed here |

### C.2 [BLOCKER] PyQt6 is GPL-3.0-only

> **Superseded on 2026-09-16.** The GUI now uses PySide6 and `pyproject.toml` no longer declares
> PyQt6, so this blocker is resolved *in code*; see G.2 for what still has to happen before a licence
> can be recommended. The analysis below is kept unchanged as the record of why the migration was
> undertaken - read it as history, not as the current state.

`gui/` and `viewer/` import PyQt6, and `pyproject.toml` lists `PyQt6>=6.5` as a **required**
dependency. Because the source itself will be published on GitHub, publishing it *is* a
distribution of a program that depends on a GPL-3.0-only library. Recommending the AppImage does
not avoid this: the AppImage changes how users install the software, not the licence analysis.

Consequences:

- A permissive licence (MIT / BSD-3-Clause / Apache-2.0) for the project as it stands would be
  inconsistent with the PyQt6 terms.
- Publishing with no `LICENSE` file leaves the terms undefined *while* the GPL obligation still
  exists - the worst of the three positions.

Options, analysed in [LICENSE_OPTIONS.md](LICENSE_OPTIONS.md):

1. Licence the project **GPL-3.0-only** (or `-or-later`). No code change. Simplest and fully
   consistent.
2. **Migrate the GUI from PyQt6 to PySide6** (LGPL-3.0), then choose a permissive licence. The
   Qt-free core makes this a `gui/` + `viewer/` migration.
3. **Buy a commercial PyQt6 licence.**

No `LICENSE` file has been committed, by instruction.

### C.3 Redistribution rules applied

Nothing from an NMRPipe/SMILE installation is copied into the repository; no third-party script is
vendored; the AppImage build does not download NMRPipe. `README.md` and
[docs/external-dependencies.md](docs/external-dependencies.md) state the install relationship, and
the locator reports a clear message when the engine is missing instead of failing obscurely.
---

## D. Work performed in this preparation

Each item below was done locally. Nothing was committed to a remote, pushed, tagged or published.

| Phase | Deliverable | Status |
| --- | --- | --- |
| 0 | Inventory (section A) | done |
| 1 | Security/privacy audit (section B) | done |
| 2 | [THIRD_PARTY.md](THIRD_PARTY.md) | done |
| 3 | [LICENSE_OPTIONS.md](LICENSE_OPTIONS.md) - **no `LICENSE` committed** | done, awaiting decision |
| 4 | `.gitignore` extended (venvs, build/packaging output, coverage, caches, `.env`/keys, raw NMR data, example and benchmark output) | done; verified that the tracked header fixtures are still not ignored |
| 5 | Structure audit; **no forced restructuring** | done |
| 6 | Version: already single-source; the v0.9.0 bump is deferred to the release step | done |
| 7 | [README.md](README.md) rewritten for public readers (AppImage-first installation, accurate feature list, limitations, Mermaid workflow) | done |
| 8 | Features verified against the code before being claimed | done |
| 9 | Workflow schematic added (Mermaid) | done |
| 10 | QC audit-ability reviewed; a structured per-run audit record still does not exist | **partial - see F.3** |
| 11 | Provenance: `core/version.py` now records the Git commit and a dirty flag in run records | done |
| 12 | Test system: release-readiness suite added; the flat `tests/` layout was deliberately kept | done, see F.2 |
| 13 | CI: `.github/workflows/ci.yml` (static checks, Python 3.12/3.13 matrix, release-readiness job, opt-in self-hosted engine job) | done |
| 14 | Static checks: Ruff lint enforced in CI; the formatter is advisory | done |
| 15 | Dependency groups `test` / `dev` / `docs` in `pyproject.toml`; no `gui` group (Qt is required, documented) | done |
| 16 | Install experience documented: AppImage for users, editable install for developers, wheel explicitly unsupported | done |
| 17 | Quickstart: `examples/make_synthetic_dataset.py` + `examples/quickstart.py`, verified for 2D and 3D | done |
| 18 | 12 public documentation pages under `docs/` | done |
| 19 | API documentation for the public surfaces (`nmrforge_api`, `pick_peaks`, `localize_peak`) | done |
| 20 | GUI documentation written; **no real screenshot committed** | **partial - see F.4** |
| 21 | Error-message audit: sampled, not exhaustive | **partial - see F.6** |
| 22 | Logging: bare `print()` usage inventoried | **partial - see F.7** |
| 23 | [CHANGELOG.md](CHANGELOG.md) restructured with a Keep-a-Changelog header and an `[Unreleased]` section; dated history kept as-is | done |
| 24 | [CITATION.cff](CITATION.cff) with explicit placeholders (no invented authors) | done, awaiting decision |
| 25 | [CONTRIBUTING.md](CONTRIBUTING.md) | done |
| 26 | `.github/ISSUE_TEMPLATE/` - bug report, feature request, data-processing problem, config | done |
| 27 | `.github/PULL_REQUEST_TEMPLATE.md` | done |
| 28 | [SECURITY.md](SECURITY.md) - contact is a placeholder | done, awaiting contact |
| 29 | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) - reporting contact is a placeholder | done, awaiting contact |
| 30 | Separation of nmrForge from the uncertainty study: **not done** - references remain | **blocked - see G.3 / G.4** |
| 31 | [benchmarks/](benchmarks/README.md) framework, runnable, no invented results | done |
| 32 | No unsupported performance claims: the rewritten README makes no speed or accuracy claim, and the benchmark rule is written down | done |
| 33 | [RELEASE_CHECKLIST_v0.9.0.md](RELEASE_CHECKLIST_v0.9.0.md); no release created | done |
| 34 | `.zenodo.json` prepared; no upload, no DOI reserved, no external login | done |
| 35 | History preserved; no rewrite | done |
| 36 | Work split into logical commits | done |
| 37 | This report | done |

### D.1 Verification performed

| Check | Command | Result |
| --- | --- | --- |
| Full test suite | `python -m pytest -q --basetemp=...` | 1112 passed, 1 skipped, 0 failed (before the new tests were added) |
| Lint | `python -m ruff check .` | clean |
| Imports | `import core, nmrforge_api, gui, viewer, backend, workflow` | OK; zero `PyQt` modules loaded by `core` + `nmrforge_api` |
| CLI | `python -m nmrforge_api --help`, `... sweep --help` | OK |
| Example workflow | `python examples/make_synthetic_dataset.py --out ...` then `python examples/quickstart.py ...` | OK for 2D (classified HSQC / uniform) and 3D (classified HNCA / NUS, matrix shape `(32, 48, 128)`) |
| Benchmark framework | `python benchmarks/run_benchmarks.py` | 14 rows written, 10 measured, 4 engine rows honestly `skipped_no_engine` |
| Version single source | `pyproject.toml` dynamic version == `core.__version__` | OK (also locked by a test) |
| Fixtures not newly ignored | `git check-ignore tests/fixtures/bruker/hsqc_2d/acqus` | not ignored (correct) |

GUI note: the GUI module imports, the viewer process starts under `QT_QPA_PLATFORM=offscreen`, and
the GUI layout tests pass. A full interactive GUI session was **not** performed as part of this
audit.

---

## E. READY

These are done, verified, and need no decision from you:

1. **No credentials anywhere** - not in the tree, not in the history (section B.2).
2. **No raw NMR data** in the repository or its history; fixtures are tiny text headers.
3. **No third-party binaries or vendored third-party scripts**, and no bundling of NMRPipe/SMILE.
4. **No file needs history rewriting** for size, secrets or binaries (Phase 35 criteria).
5. **All existing tests pass** (1112 passed, 1 skipped) and the suite does not require NMRPipe.
6. **Ruff is clean**, and Ruff lint is enforced in CI.
7. **A single, dynamic version source** exists and is now locked by a test.
8. **Provenance now includes the Git commit** and whether the tree was dirty when the run was made.
9. **CI exists and is validated by a test** (jobs, matrix, packaging contract).
10. **Issue/PR templates, contributing guide, security policy and code of conduct exist.**
11. **A runnable, NMRPipe-free example workflow exists** and was verified on 2D and 3D synthetic data.
12. **The benchmark framework is real and self-consistent** (classification rates 1.0 on the
    labelled fixtures, localisation error measured against a synthetic peak, engine timings
    reported as skipped rather than invented).
13. **Public documentation exists** for installation, GUI, CLI, API, processing model, QC, peak
    picking, batch processing, external dependencies, troubleshooting and FAQ.
14. **The README makes no performance claim**, and states the known limitations (2D-only batch,
    hidden 3D SMILE UI, NMRPipe requirement, Chinese-only UI).
15. **`.gitignore` now covers** virtual environments, build/packaging output, caches, coverage,
    credential files, raw NMR data and example/benchmark output - without touching the tracked
    fixtures.
16. **Machine-specific overrides are already git-ignored** (`config/nmrforge.local.yaml`), and the
    configuration loader prefers them over the tracked defaults.
---

## F. WARNINGS

Publishable, but worth fixing later. None of these should delay the release if you accept them.

### F.1 Naming: 旧名 vs nmrforge - **RESOLVED 2026-09-16 (name: nmrforge)**

**Decision (2026-09-16, owner): the project name is `nmrforge` (display "nmrforge"); "旧名" is
legacy and must not be used for new material.** What was updated in this round: the `core` package
docstring, the naming note in `CHANGELOG.md`, this audit and the release checklist. What is kept as
history and must *not* be rewritten: the pre-2026-09-16 `CHANGELOG.md` entries, `docs/problems.md`,
`docs/tasks/archive/**`, `docs/HANDOVER.md`, `docs/DECISIONS.md` and `docs/AGENT_PROMPTS.md` - they
record the state of the time (and several of them quote the local checkout path, see below).

**Done 2026-09-17 (owner):** the private local checkout folder was renamed to match the
VM working copy (`~/NMRForge`). Nothing in the
repository depends on the folder name, and the historical records that quote the old path
(`docs/HANDOVER.md`, `docs/DECISIONS.md`, `docs/AGENT_PROMPTS.md`, `docs/problems.md`, old
`CHANGELOG.md` entries) are deliberately left untouched as history.

### F.2 Test layout is flat

99 test modules in `tests/` with no `unit/`, `integration/`, `regression/` split. The task
suggested classifying them. It was **not** done, deliberately: many tests locate fixtures with
`Path(__file__).parent / "fixtures"`, so moving files is a real refactor with real breakage risk and
no benefit at release time. Instead, the release-readiness suite adds the release-level invariants,
and `CONTRIBUTING.md` documents the expectation.

### F.3 QC audit trail is log-based, not a structured record - **RESOLVED 2026-09-16**

> Resolved in this round: `core/audit/qc_audit.py` now writes an append-only
> `qc_audit.jsonl` per work directory with `issue_detected / location / detection_rule /
> action_taken / before_state / after_state / timestamp / software_version` (plus git
> provenance), rules out silent modification, and is wired into bad-point repair, sampling-grid
> shrinkage and source deletion. Coverage: `tests/test_qc_audit.py` (16 tests).
> `_zero_bad_point_fid` already accepts the audit parameter and is passed at its call sites.
> See `docs/qc-system.md`.

The original finding is kept below for the record.


Automatic bad-point handling already follows *detect -> flag -> log -> optional correction*, and
findings (DC offset, non-finite points, all-zero traces, anomalous traces, indices, metrics) are
reported before any decision. What does not exist yet is a single machine-readable per-run record
with the fields `issue_detected / location / detection_rule / action_taken / before_state /
after_state / timestamp / software_version`. The intended shape is documented in
[docs/qc-system.md](docs/qc-system.md). This is the largest remaining gap between the current
implementation and the task's Phase 10 goal.

### F.4 No real GUI screenshot

The README embeds `gui/assets/nmrforge.png`, which is the application **icon**, not a screenshot of
the interface. A real screenshot must be taken on a machine with the GUI running, using publishable
data, with no user name, sample name or laboratory path visible. Deliberately not fabricated or
generated here.

### F.5 Formatter is not enforced

`ruff format --check .` would reformat 191 of 330 files. Enforcing it now would produce a large diff
unrelated to the release and would obscure real changes, so CI enforces `ruff check` (lint) only.
The intended policy is "new code strict, old code gradually improved".

### F.6 Error-message audit was sampled, not exhaustive

Spot checks show explicit, actionable messages where it matters ("not a Bruker dataset directory:
...", "NMRPipe script needs a C-shell", "Gaussian peak fitting is currently supported only for 2D
spectra.", "missing spectrum, cannot pick peaks"). A systematic sweep of every user-visible
exception path was not performed, so a few raw `KeyError`/`TypeError` traces may still reach users
in rarely hit branches.

### F.7 Some bare `print()` calls remain

Small counts in `gui/pipeline_state.py` (12), `gui/pipeline_panel.py` (5), `workflow/batch.py` (3)
and `workflow/param_optimize.py` (1), plus the CLI (legitimate). The GUI ones are debug leftovers
and are the best candidates for moving behind the logging setup.

### F.8 Per-run `run.log` file does not exist

Logs live in the GUI log panel and in the `WorkflowRun` record; there is no per-run `run.log` file
on disk (the study API writes `manifest.json` / `runs.json` instead). If a file per run is wanted,
it should be added deliberately, with a decision about path redaction.

### F.9 Bilingual documentation

New public documents are English; the pre-existing internal documents and the GUI are Chinese. This
is a reasonable split, but it means the repository reads as two audiences. Unifying later (a
Chinese mirror, or an English UI translation) is real work and should be scheduled, not improvised.

### F.10 `.git` is 81.8 MiB locally due to loose objects

Harmless for clones (~2 MiB packed) but worth `git gc` before archiving the repository or building a
release tarball.

### F.11 Environment caveat: pytest scratch directory

On this Windows machine the default pytest scratch path (`%TEMP%\pytest-of-<user>`) is ACL-locked,
so plain `pytest` fails and `--basetemp=<dir>` is required. This is a pre-existing,
already-documented environment limitation (`docs/development.md`), not a repository defect, but
every CI job and documented command therefore passes `--basetemp`.

### F.12 Documentation for a future docs site

The `docs` extra declares MkDocs for a future documentation site. The Markdown under `docs/` is
readable as-is on GitHub; no site is built today.

---

## G. BLOCKERS

Must be resolved by you before the repository becomes visible to anyone else.

### G.1 [BLOCKER] IP ownership and the author list - **author known, IP questions open**

**Update 2026-09-17:** the author and copyright holder are now on record: **Xuanfeng Li (李宣锋)**,
recorded in `LICENSE`, `README.md`, `pyproject.toml`, `CITATION.cff` and `.zenodo.json`; version
0.9.0. What is still unresolved is the *institutional* side: whether the institute or laboratory
claims the copyright, whether a funding agreement imposes redistribution conditions, whether
co-authors exist and should be listed, and the affiliation to put in `CITATION.cff`. No source file
carries a per-file copyright header (the root `LICENSE` notice covers the work).

### G.2 [BLOCKER] Licence - **decided 2026-09-16: source Apache-2.0, LGPL only for bundled Qt**

**Decision (2026-09-16, owner):** the project's own source code is licensed under the **Apache
License 2.0** (`Apache-2.0`); **LGPL applies only to what a packaged distribution bundles** - the
Linux AppImage's Qt/PySide6 (`LGPL-3.0-only` option). The first decision that day was to put the
whole project under LGPL-3.0-only; the owner then clarified that this was not the intent, and the
split above is what is in force.

What was changed: `LICENSE` (root) now holds the Apache-2.0 text plus a project notice,
`pyproject.toml` declares `Apache-2.0` with the Apache classifier, and `README.md` (both languages),
[LICENSE_OPTIONS.md](LICENSE_OPTIONS.md), `THIRD_PARTY.md`, `CONTRIBUTING.md`, `CITATION.cff` and
`.zenodo.json` state the same. `tests/test_release_readiness.py` fails if the source licence drifts
or if the project's own licence text claims LGPL, and it also fails if the AppImage's shipped LGPL
text disappears. The LGPL machinery is unchanged and still required for the AppImage
(`packaging/linux/THIRD_PARTY_LICENSES/`, `PYSIDE6_REQUIREMENT` et al.).

The item stays a blocker only because the owner (or whoever owns the IP) has not yet reviewed those
distribution obligations - the relink/replace requirement for a single-file AppImage is the open
legal question.

**History (unchanged analysis below):** the GPL obstacle was removed by the PySide6 migration.
**Status changed on 2026-09-16.** The GUI no longer uses PyQt6: the PySide6 migration is complete on
the branch `codex/pyside6-migration-feasibility` (Stage 5), `pyproject.toml` declares `PySide6`, and
nothing outside `qtcompat/` imports a Qt binding. PySide6 is offered under
`LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`, so a permissive licence for this project is now
**possible in principle** — the GPL-3.0-only constraint that previously forced the whole project to
be GPL is gone.

It is not **recommended** yet, and this remains a blocker because:

1. the full third-party audit **has now been rerun** (`scripts/audit_third_party.py`:
   28 permissive, 4 weak copyleft, 0 strong-copyleft-only; inventory in `THIRD_PARTY.md` section 7),
   but it must be rerun again *in the build environment* before a release, because the build
   machine's set is what actually ships;
2. the LGPL obligations for a *binary* distribution are **implemented but not reviewed**: the
   AppImage bundles Qt, so it now ships hash-checked LGPL-3.0 and GPL-3.0 texts, a notice naming the
   licence option relied on, and a documented route for a recipient to replace or relink the
   libraries (`packaging/linux/THIRD_PARTY_LICENSES/`, enforced by
   `scripts/check_third_party_licenses.py`, documented in `docs/packaging.md`). What is missing is a
   review of that mechanism by whoever owns the IP - the notice is not legal advice;
3. the AppImage has not been rebuilt or smoke-tested against PySide6 (Stage 4; needs a Linux build
   machine);
4. the owner still has to choose the licence.

**No `LICENSE` file has been committed**, by instruction. See
[LICENSE_OPTIONS.md](LICENSE_OPTIONS.md) section 2 and 5, and
[THIRD_PARTY.md](THIRD_PARTY.md).

### G.3 [RESOLVED 2026-09-17] Unpublished sample identifiers and dataset shorthand

**Sanitised (owner chose option A on 2026-09-17).** All laboratory dataset labels, the real sample
filename (`sampleA_…`), the study directory name and the internal host reference were replaced by
neutral labels across the working tree (58 files: `CHANGELOG.md`, `docs/**`, four source comments,
`scripts/`, `tests/`); the VM helper scripts and their test were renamed to neutral names.
The label mapping is kept
in the owner's private archive, deliberately not in the repository. **Caveat:** older commits still
contain the original identifiers - see the history decision in section 2 of
`RELEASE_CHECKLIST_v0.9.0.md`.

Original finding (kept for the record):

`sampleA.fid` and the uncertainty-study paths, plus internal dataset shorthand
across `CHANGELOG.md`, `docs/` and three source files. Details and recommended replacements are in
B.3. Decide per occurrence.

### G.4 [RESOLVED 2026-09-17] Separation of nmrForge from the uncertainty study

Handled together with G.3: the study directory is referred to as `~/nmr-uncertainty/...` and the
dataset it used is now `sampleA.fid`; no results, plans or drafts were ever in this repository.

Original finding (kept for the record):

The task's Phase 30 requires the nmrForge repository not to carry the unpublished study's results,
plans or drafts. In practice the repository contains no results and no drafts, but it does reference
the study's existence, its directory layout (`~/nmr-uncertainty/...`) and one of its real datasets.
Either sanitise those references, or accept them explicitly. Nothing about the study was moved or
deleted here.

### G.5 [RESOLVED 2026-09-17] Private contacts for `SECURITY.md` and `CODE_OF_CONDUCT.md`

Both files now point at a real channel: GitHub private vulnerability reporting on
<https://github.com/RociferX/nmrforge>, with the maintainer **@RociferX** as the fallback contact
for both security and conduct reports. The owner chose **not** to publish an email address, so no
personal address appears in the repository.

---

## H. MANUAL ACTIONS

Things only you can do. Suggested order:

1. **Confirm IP ownership** (institution, funding terms, who holds copyright). *(Author/holder name
   recorded 2026-09-17: Xuanfeng Li; the institutional claims are still open.)*
2. ~~Confirm the author list~~ **partially done 2026-09-17** (Xuanfeng Li entered in `CITATION.cff`
   and `.zenodo.json`); still to do: affiliation, any co-authors and their agreement.
3. **Rerun the audit in the AppImage build environment**
   (`python scripts/audit_third_party.py --csv /tmp/audit.csv`) and have the LGPL mechanism reviewed
   by whoever owns the IP. *(The licence itself is decided: source Apache-2.0, LGPL only for the
   bundled Qt/PySide6 - see G.2.)*
4. ~~Add the `LICENSE` file and set the `license` field and classifier~~ **done 2026-09-16**
   (Apache-2.0, root `LICENSE` + `pyproject.toml`).
5. **Add the private contacts** for `SECURITY.md` and `CODE_OF_CONDUCT.md`.
6. **Review the privacy findings** in B.3, G.3 and G.4: sample identifier, study paths, dataset
   shorthand, internal host references. Decide keep / sanitise / remove per occurrence.
7. **Decide the fate of the internal process material**: `.codex/AGENTS.md`, `docs/AGENT_PROMPTS.md`,
   `docs/manager/`, `docs/tasks/`, `docs/reviews/`, `docs/proposals/`.
8. **Decide the fate of `.patch_smile_gap.py`** (recommended: delete) — `.measure_gap.py` and the
   two `scripts/pyside6_*` migration tools were deleted in Stage 5, recoverable from git history —
   and whether `scripts/vm_*.py` should be sanitised.
9. **Take a real GUI screenshot** with publishable data, no user name, no sample name and no
   laboratory path, and add it to the README.
10. ~~Resolve the naming question~~ **done 2026-09-16**: the name is `nmrforge`; "旧名" is legacy
    (see F.1). Remaining manual step: rename the local checkout folder, which is a local operation.
11. **Create the GitHub repository yourself, choose its visibility, add the remote and push.** This
    preparation deliberately did none of that.
12. **After the repository exists:** enable branch protection, add CI status badges to the README,
    create the `v0.9.0` tag and GitHub release (bump `core/__init__.py` to `0.9.0` first), attach the
    AppImage, then connect Zenodo, obtain the DOI, and update `CITATION.cff` and `.zenodo.json` with
    the real DOI and licence.

Optional, recommended cleanups: delete `.patch_smile_gap.py`; run `git gc` to shrink the local
`.git`. (`.measure_gap.py` was deleted in Stage 5 - see the commit history if you want it back.)

---

## I. Acceptance criteria status

| Criterion | Status |
| --- | --- |
| all existing tests pass | **yes** - 1112 passed, 1 skipped, 0 failed |
| new release-readiness tests pass | **yes** - `tests/test_release_readiness.py` |
| static checks pass | **yes** - `ruff check .` clean |
| CI configuration valid | **yes** - parsed and asserted by a test |
| package install works | **yes for the supported shapes** - editable install verified; a wheel is explicitly not a supported distribution path (PACK-015) |
| GUI launches | **partially verified** - imports fine, offscreen Qt starts, GUI tests pass; no interactive session performed |
| CLI launches | **yes** - `python -m nmrforge_api --help` and subcommand help |
| core API imports | **yes** - and loads no Qt module |
| example workflow runs | **yes** - 2D and 3D synthetic data |
| README quick start is reproducible | **yes** - commands verified as written |
| no known secrets in current tree | **yes** |
| third-party dependencies documented | **yes** - `THIRD_PARTY.md` |
| release checklist generated | **yes** - `RELEASE_CHECKLIST_v0.9.0.md` |
| no publication performed | **yes** - nothing pushed, tagged, released or uploaded |

---

## J. What was deliberately NOT done

Per the task's prohibitions, the following were not done, and are recorded here so that the omission
is auditable:

- No repository visibility change, no GitHub repository creation, no remote added, no push, no
  force-push.
- No Git history rewrite, no squash, no rebase, no deletion of original history.
- No `LICENSE` chosen or committed.
- No Zenodo record, no DOI, no upload, no login to any external service.
- No deletion of any suspect file (the two root leftovers are recommended for deletion, not deleted).
- No raw NMR data uploaded, and no example data added beyond the synthetic generator.
- No uncertainty-study repository published or modified.
- No failing test was disabled or skipped to make the suite green.
- No large refactor unrelated to release readiness; in particular the test layout was left alone on
  purpose.
- No performance claim was written without a benchmark behind it.

---

## K. Honesty notes about this report

- The test counts, the lint result, the classification results, the benchmark rows and the
  example-workflow results in section D.1 were produced on this machine on 2026-09-16 with no
  NMRPipe present; they are reproducible with the commands given.
- Measurements on a machine **with** NMRPipe were not repeated here. Historical records of such runs
  (VM validation) live in `CHANGELOG.md` and `docs/` and are not re-asserted by this report.
- The licence discussion is an engineering and dependency analysis, not legal advice. Have the
  licence decision reviewed by whoever owns the IP.