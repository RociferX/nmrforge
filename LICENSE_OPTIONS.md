# Licence Options

**No licence has been chosen. No `LICENSE` file is committed. This document only prepares the
decision.**

Read this together with [THIRD_PARTY.md](THIRD_PARTY.md), which records the dependency facts
that constrain the choice. This is not legal advice.

## 1. What has already been checked

| Check | Result |
| --- | --- |
| Existing `LICENSE` / `COPYING` file | None. The repository is currently "all rights reserved" by default. |
| Institutional or laboratory copyright notice in the tree | None found. No `Copyright (c)` header in any source file. |
| Per-file licence headers | None. |
| Third-party components that constrain the choice | **Yes - PyQt6 is `GPL-3.0-only`.** See below. |
| Copyright holder named in packaging metadata | None (`pyproject.toml` has no `authors`/`license` field). |
| Git author identity used so far | `Xuanfeng Li <330249944+RociferX@users.noreply.github.com>` (the identity used in the public history); no personal email address is published |

## 2. The constraint that matters most

The GUI (`gui/`) and the standalone viewer (`viewer/`) import **PyQt6**, which Riverbank
Computing distributes under **GPL-3.0-only** or a paid commercial licence. `pyproject.toml`
lists PyQt6 as a required dependency.

This means:

- Choosing MIT / BSD-3-Clause / Apache-2.0 for the *whole* project, including the PyQt6 GUI, is
  not consistent with PyQt6's terms for a binary distribution.
- The core library and the scripting API (`core/`, `backend/`, `workflow/`, `nmrforge_api/`) are
  Qt-free and could in principle be licensed permissively, with the GUI carrying the GPL
  obligation. Splitting a licence across one repository is legal but needs an explicit
  statement in `README.md` and `LICENSE`, and it is easy for downstream users to get wrong.

### 2.1 Publishing the source on GitHub is itself distribution

This matters and is easy to get wrong: **publishing the source code on GitHub is distributing the
program**, so it is not possible to sidestep the GPL question by recommending that users install
only the AppImage, or by keeping PyQt6 out of the packaged artefact.

- The GUI and viewer import PyQt6. The program as distributed - source or binary - depends on a
  `GPL-3.0-only` library, so the terms that can be granted for that program are GPL-3.0 terms.
- Recommending the AppImage changes *how users install* the software. It does not change the
  licence analysis of what was published.
- Publishing the repository without a `LICENSE` file does not fix this either; it leaves the terms
  undefined while the GPL obligation still exists, which is the worst of both positions.

If the intent is "the source is public but the licence is permissive", the PyQt6 dependency has to
change first (Option B below). There is no ordering of the AppImage, the README wording or the
`pyproject.toml` that achieves it.

Because of this, the realistic options are:

- **Option A - GPL-3.0-only** (or `GPL-3.0-or-later`) for the whole project. Zero code changes.
- **Option B - migrate the GUI from PyQt6 to PySide6** (LGPL-3.0), then choose a permissive
  licence for the project. Code change, then freedom of licence choice.
- **Option C - split licensing**: permissive for the Qt-free core, GPL for the GUI. Legal, but
  must be stated unambiguously and cannot be expressed with a single SPDX identifier.

## 3. Candidate licences compared

| Aspect | MIT | BSD-3-Clause | Apache-2.0 |
| --- | --- | --- | --- |
| Type | Permissive | Permissive | Permissive with explicit patent grant |
| Patent grant | Not explicit | Not explicit | Explicit (section 3) |
| Attribution requirement | Keep copyright + licence text | Keep copyright + licence text | Keep copyright + licence + `NOTICE` file if one exists |
| Use of author names for endorsement | Not addressed | Prohibited without permission (3rd clause) | Prohibited (section 6) |
| Trademark grant | None | None | Explicitly none |
| Relicensing by downstream (including into closed products) | Allowed | Allowed | Allowed |
| Contribution terms | Silent (no explicit contributor patent/licence grant) | Silent | Explicit inbound contribution licence (section 5) |
| Change-notice obligation | None | None | Must state modified files (section 4b) |
| Text length / complexity | Shortest (~170 words) | Short (~220 words) | Long (~1500 words + `NOTICE`) |
| Typical academic-software use | Very common | Common (NumPy, SciPy, nmrglue) | Common for infrastructure (OpenSSL-style governance) |
| Compatibility with a GPL-3.0 GUI dependency | Incompatible as the licence of the combined work | Incompatible as the licence of the combined work | Incompatible as the licence of the combined work |
| Compatibility with PySide6 (LGPL-3.0) GUI | Fine (LGPL library linked by an MIT app) | Fine | Fine |
| Friction for downstream academic users | Lowest | Low | Slightly higher (NOTICE/modification bookkeeping) |

### Practical notes

- **MIT vs BSD-3-Clause**: nearly equivalent. BSD-3-Clause adds the explicit
  "don't use my name to endorse your product" clause and is the licence used by NumPy, SciPy and
  nmrglue, i.e. by the numerical stack this project already sits on. If the goal is maximal
  simplicity, MIT; if the goal is alignment with the scientific Python ecosystem, BSD-3-Clause.
- **Apache-2.0** is the strongest choice if patent protection matters (NMR processing methods
  can attract patents). The price is a longer licence, a `NOTICE` obligation, and the
  "state modified files" requirement, which is slightly awkward for academic users who copy
  code into analysis scripts.
- **A permissive licence does not make the PyQt6 problem go away.** It only makes the
  inconsistency harder to notice.

## 4. What a chosen licence would require us to change

Whichever option is chosen, the following places must be updated together:

1. Add the licence text as `LICENSE` at the repository root.
2. Set `license` (SPDX expression) and `authors` in `pyproject.toml`; add a `License ::`
   classifier.
3. Add a "Licence" section to `README.md` and to `docs/README.md`.
4. For Apache-2.0: add a `NOTICE` file and a per-file change notice when redistributing.
5. If Option B (PySide6) or Option C (split licensing) is chosen, state the split explicitly
   in both `README.md` and `LICENSE`, and record the PyQt6/PySide6 decision in
   `docs/manager/decisions.md`.
6. Record the decision in `docs/manager/decisions.md` so it is not silently revisited.

## 5. Recommendation for the owner to consider

Because the source itself will be published, the licence decision is not optional and not
deferrable past the moment the repository becomes visible.

Recommended order:

1. **Decide IP ownership and the author list first** (blocking everything else).
2. **Decide PyQt6 vs PySide6.** If a permissive licence is important for the intended software
   paper and for downstream reuse, migrating the GUI to PySide6 is the only way to get there;
   the Qt-free core makes that migration self-contained in `gui/` and `viewer/`.
3. **Then** pick the licence: BSD-3-Clause if staying permissive, GPL-3.0-only if the PyQt6
   dependency is kept as-is.