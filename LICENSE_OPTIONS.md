# Licence Options

**DECIDED on 2026-09-16: the source is released under the Apache License 2.0
(`Apache-2.0`); LGPL is used only where a packaged distribution bundles an LGPL library (the
AppImage's Qt/PySide6).** The text is committed as [LICENSE](LICENSE); the copyright holder is
recorded there as "Xuanfeng Li (李宣锋)" - the owner supplied the name on 2026-09-17; it was
temporarily "NMRForge contributors" before that. Sections 3-5 below are kept as the record of the
analysis that led to the decision; the Apache-2.0 option analysed there was chosen.

> Note (same day, after the first decision): the licence was first set to LGPL-3.0-only for the
> whole project. The owner then clarified the intent - **source code Apache-2.0, LGPL only for what
> the packaged distribution bundles** - and that is what is now in force. The LGPL mechanism for the
> AppImage is unchanged and still required; only the project's own terms changed.

Read this together with [THIRD_PARTY.md](THIRD_PARTY.md), which records the dependency facts
that constrain the choice. This is not legal advice.

## 1. What has already been checked

| Check | Result |
| --- | --- |
| Existing `LICENSE` / `COPYING` file | **`LICENSE` (added 2026-09-16): Apache License 2.0 text + project notice; SPDX `Apache-2.0`.** Before that date the repository was "all rights reserved" by default. |
| Institutional or laboratory copyright notice in the tree | None found. No `Copyright (c)` header in any source file. |
| Per-file licence headers | None. |
| Third-party components that constrain the choice | **Yes, but no longer fatally.** The GUI uses PySide6 (`LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`), which leaves a permissive licence open in principle. See section 2. |
| Copyright holder named in packaging metadata | `LICENSE` notice: "Copyright 2026 Xuanfeng Li (李宣锋)"; `pyproject.toml` declares the licence SPDX id and `authors = [{ name = "Xuanfeng Li" }]`. |
| Git author identity used so far | `Xuanfeng Li <330249944+RociferX@users.noreply.github.com>` (the identity used in the public history); no personal email address is published |

## 2. What constrains the choice now

The GUI (`gui/`, `viewer/`, `ui_support/`) uses Qt through a single boundary module,
`qtcompat/`, which imports **PySide6**. `pyproject.toml` declares `PySide6` as the runtime
dependency. PyQt6 - the binding whose `GPL-3.0-only` licence used to force this project to be GPL -
is no longer used or declared anywhere; the migration is complete
([docs/pyside6-migration/migration-plan.md](docs/pyside6-migration/migration-plan.md)).

PySide6 is published by the Qt Company under:

    LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only

Consequences, and the part that is easy to get wrong:

- **LGPL-3.0-only is available as an option**, so a permissive licence for *this project's own code*
  is now possible in principle. That was not true before the migration.
- **There is a difference between publishing the source and shipping a binary.** A source-only
  publication (a GitHub repository, or an sdist) distributes *your* code and merely declares PySide6
  as a dependency: recipients install Qt themselves, so you are not distributing the LGPL library
  and the LGPL's obligations on the library are not triggered by that act alone.
- **Shipping the AppImage distributes Qt.** The AppImage bundles PySide6 and the Qt libraries, so
  LGPL obligations do apply to it: licence texts and notices, and the requirement that a recipient
  can replace or relink the LGPL library. The PySide6 wheels ship **no LGPL text at all** (only
  `LicenseRef-Qt-Commercial.txt`), so the distributor must supply it. How to satisfy the
  replace/relink requirement inside a read-only single-file AppImage is an open legal question, not
  a packaging preference.
- **The core is unaffected either way.** `core/`, `backend/`, `workflow/` and `nmrforge_api/` are
  Qt-free, so a "core-only" distribution has no Qt dependency at all.

### 2.1 Publishing the source on GitHub is itself distribution

**Publishing the source is distribution of your program**, which is why the licence decision cannot
be deferred past the moment the repository becomes visible. It is also why the earlier obstacle had
to be fixed in code rather than worked around in the documentation.

What it does *not* do is make the LGPL's library obligations apply to your source tree: with a
source-only distribution you are not handing out Qt, you are naming it as a dependency. The
obligations follow the library, so they bite when a binary you distribute contains it.

### 2.2 Status of the migration and of the licence decision

- PyQt6 removal: **done** (Stage 5 of the migration; the whole suite passes on PySide6, 1148 tests,
  and `tests/test_qt_independence.py` fails if any module outside `qtcompat/` names a binding).
- Rebuilding and smoke-testing the AppImage against PySide6: **not done** - it needs a Linux build
  machine (Stage 4).
- The full third-party audit **has** been rerun since the migration
  (`scripts/audit_third_party.py`: 28 permissive, 4 weak copyleft, 0 strong-copyleft-only; inventory
  in `THIRD_PARTY.md` section 7). It must be rerun again inside the AppImage **build** environment
  before a release, because that set is what ships.
- The LGPL distribution obligations for the AppImage are **implemented and enforced**
  (`packaging/linux/THIRD_PARTY_LICENSES/`, hash-checked by
  `scripts/check_third_party_licenses.py`, copied into the AppDir, exposed as `--licenses`, with a
  documented replace/relink route), but **not yet reviewed by the IP owner**, and no AppImage has
  been built since the migration.

A `LICENSE` file **has now been committed** (`Apache-2.0`, 2026-09-16, on the owner's
instruction) with the LGPL confined to the packaged AppImage's Qt/PySide6. The remaining open points
are the owner's review of those distribution obligations (section 6 of
[docs/pyside6-migration/migration-plan.md](docs/pyside6-migration/migration-plan.md)) and the author
list / IP ownership: the author name is now public (Xuanfeng Li); the institution, funding terms
and co-author list still need the owner's confirmation.

## 2.1 What the chosen licence means for this project

**The project's own code is Apache-2.0.** That is permissive: anyone may use, modify and redistribute
it, including inside closed products, provided they keep the copyright/licence notices, state which
files they changed, and respect the patent-termination clause. Contributions are covered by section
5 (inbound = outbound) and by `CONTRIBUTING.md`.

**LGPL-3.0 applies only to the packaged distribution, and only to what it bundles.** The AppImage
contains Qt and PySide6, distributed under Qt's `LGPL-3.0-only` option, so that *binary* must carry
the LGPL-3.0/GPL-3.0 texts and the Qt notices and must let a recipient replace or relink those
libraries (implemented in `packaging/linux/THIRD_PARTY_LICENSES/` and `build_appimage.sh`). This is
an obligation about those libraries, not about nmrforge's source: a user who installs from source
installs Qt themselves, as a separate package, and no LGPL obligation attaches to the nmrforge code.

The one place to keep honest is therefore the AppImage: nothing in the source tree may claim that the
project is LGPL, and nothing in the AppImage may drop the Qt/PySide6 notices.

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
| Compatibility with the current GUI dependency (PySide6, LGPL-3.0 option) | Fine for source distribution; binary distribution must meet the LGPL obligations | Fine, same | Fine, same |
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
- **A permissive licence is now possible, but not yet recommended.** Nothing in the code
  blocks it after the PySide6 migration; what is missing is the rerun of the full third-party
  audit and the owner's decision (section 5).

## 4. What a chosen licence would require us to change

Whichever option is chosen, the following places must be updated together:

1. Add the licence text as `LICENSE` at the repository root.
2. Set `license` (SPDX expression) and `authors` in `pyproject.toml`; add a `License ::`
   classifier.
3. Add a "Licence" section to `README.md` and to `docs/README.md`.
4. For Apache-2.0: add a `NOTICE` file and a per-file change notice when redistributing.
5. State the Qt situation explicitly in `README.md` and `LICENSE`: the project depends on
   PySide6 (LGPL-3.0 among its options) and ships it inside the AppImage.
6. Ship the LGPL-3.0 text and the Qt/PySide6 notices with any binary distribution, and document how
   a recipient can replace or relink the bundled Qt libraries.
7. Record the decision in `docs/manager/decisions.md` so it is not silently revisited.

## 5. Recommendation for the owner to consider

Because the source itself will be published, the licence decision is not optional and not
deferrable past the moment the repository becomes visible.

Recommended order:

1. **Decide IP ownership and the author list first** (blocking everything else).
2. **Rerun the full third-party audit** on the post-migration dependency set (section 2.2), and
   answer the AppImage/LGPL questions in section 6 of
   [docs/pyside6-migration/migration-plan.md](docs/pyside6-migration/migration-plan.md).
3. **Then** choose. With PyQt6 gone, both a permissive licence (BSD-3-Clause or MIT, matching the
   numerical stack this project sits on) and GPL-3.0-only are defensible; the choice is the owner's,
   and this document does not make it.