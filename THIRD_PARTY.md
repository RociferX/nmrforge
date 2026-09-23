# Third-Party Dependencies

This file records every third-party component that nmrForge depends on, whether it is
bundled with this repository, and under which terms it is used.

Status note: this inventory was produced during public-release preparation and updated when the
GUI moved from PyQt6 to PySide6. Items marked **BLOCKER** must be resolved by the repository owner
before the repository is made public. Nothing here is legal advice.

## 1. Summary table

| Name | Purpose | Bundled or external | License (declared by upstream) | Official source | Required / optional |
| --- | --- | --- | --- | --- | --- |
| PySide6 | Desktop GUI toolkit bindings | external (pip) | **LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only** | https://pypi.org/project/PySide6/ | required for GUI and viewer |
| pyqtgraph | Interactive plotting widgets (GUI contours) | external (pip) | MIT | https://pypi.org/project/pyqtgraph/ | required for GUI and viewer |
| NumPy | Array/FFT numerics | external (pip) | BSD-3-Clause | https://numpy.org/ | required |
| SciPy | Optimization and signal routines | external (pip) | BSD-3-Clause | https://scipy.org/ | required |
| pandas | Tabular peak/record I/O | external (pip) | BSD-3-Clause | https://pandas.pydata.org/ | required |
| Matplotlib | 2D contour rendering on the GUI canvas | external (pip) | Matplotlib license (PSF-derived, BSD-compatible) | https://matplotlib.org/ | required |
| PyYAML | Configuration and preset parsing | external (pip) | MIT | https://pyyaml.org/ | required |
| nmrglue | NMRPipe/Bruker file format readers | external (pip) | BSD-3-Clause (New BSD) | https://nmrglue.readthedocs.io/ | required |
| reportlab | PDF report generation | external (pip) | BSD-3-Clause | https://pypi.org/project/reportlab/ | required |
| send2trash | Move deleted projects to the OS trash instead of erasing | external (pip) | BSD-3-Clause | https://pypi.org/project/Send2Trash/ | required |
| pytest | Test runner | external (pip, dev) | MIT | https://pytest.org/ | optional (development) |
| Ruff | Linter and formatter | external (pip, dev) | MIT | https://docs.astral.sh/ruff/ | optional (development) |
| NMRPipe | The actual NMR processing engine invoked by the backend | **not bundled**, user-installed | Proprietary / free-of-charge research software distributed by NIST (see upstream terms) | https://www.ibbr.umd.edu/nmrpipe/ | required for real processing |
| SMILE | NUS / non-uniform sampling reconstruction engine | **not bundled**, user-installed | Distributed together with NMRPipe (see NMRPipe terms) | https://www.ibbr.umd.edu/nmrpipe/ | required for NUS reconstruction |
| Java runtime | NMRPipe accessory tools such as `bruker`/GUI helpers on some installs | **not bundled**, user-installed | Oracle/OpenJDK terms | https://adoptium.net/ | optional |

Versions observed while preparing this file (2026-09-16, Windows development machine):
PyQt6 6.11.0, pyqtgraph 0.14.0, NumPy 2.4.6, SciPy 1.18.0, pandas 3.0.5, Matplotlib 3.11.1,
PyYAML 6.0.3, nmrglue 0.11, reportlab 5.0.0, send2trash 2.1.0, pytest 9.1.1, Ruff 0.16.5.
`pyproject.toml` is the authoritative dependency list; the recorded versions merely describe
the environment the release audit was performed in.

## 2. Reproducible upstream citations

If you use nmrForge for published work, the processing engine and reconstruction engine should
be cited as well:

- NMRPipe - Delaglio, F., Grzesiek, S., Vuister, G. W., Zhu, G., Pfeifer, J., Bax, A.
  "NMRPipe: A multidimensional spectral processing system based on UNIX pipes."
  *Journal of Biomolecular NMR* 6, 277-293 (1995). doi:10.1007/BF00197809
- SMILE - Ying, J., Delaglio, F., Torchia, D. A., Bax, A.
  "Sparse multidimensional iterative lineshape-enhanced (SMILE) reconstruction of both
  non-uniformly sampled and conventional NMR data."
  *Journal of Biomolecular NMR* 68, 101-118 (2017). doi:10.1007/s10858-016-0072-7

## 3. What this repository does NOT bundle

The following are deliberately **not** included in this repository or in any built artefact:

- NMRPipe binaries, macro files, or its `nmrtxt` help database;
- SMILE binaries;
- any third-party script copied from an NMRPipe installation or from another laboratory;
- third-party Java runtimes;
- any real (or unpublished) NMR datasets.

nmrForge only *invokes* NMRPipe/SMILE as external executables found on the user's own machine.
The backend locates them at runtime (`backend/nmrpipe_finder.py`) and reports a clear,
user-facing error when they are missing, instead of shipping or downloading them.

## 4. Redistribution rules applied here

1. Nothing from an NMRPipe/SMILE installation is copied into this repository.
2. No third-party licensed script is vendored under `scripts/` or `nmrforge_data/presets/`.
3. `nmrforge_data/presets/*.yaml` and the code in `core/`, `backend/`, `workflow/`, `gui/`, `viewer/`,
   `nmrforge_api/` are original project code. The pulse-program keyword lists they contain are
   identifiers (laboratory sample shorthand), not copied source code.
4. Icons under `gui/assets/` and `packaging/linux/icons/` are project assets.
5. Users who build the Linux AppImage (`packaging/linux/build_appimage.sh`) are responsible for
   complying with the terms of every component they pack.

## 5. BLOCKERS

### PySide6 is the GUI dependency (LGPL-3.0 option available)

As of the PySide6 migration (Stage 5, 2026-09-16) the GUI and viewer use **PySide6** through the
`qtcompat/` boundary, and `pyproject.toml` declares `PySide6`. **PyQt6 is no longer used or
declared**; it is recorded here only as the removed dependency.

| Item | Verified value (2026-09-16) |
| --- | --- |
| Distributions | `PySide6 6.11.2`, `PySide6_Essentials 6.11.2`, `PySide6_Addons 6.11.2`, `shiboken6 6.11.2` (Qt 6.11.2) |
| Licence expression | `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` |
| Licence text shipped in the wheel | **only** `dist-info/licenses/LicenseRef-Qt-Commercial.txt` - no LGPL-3.0 text |
| `requires-python` | `>=3.10,<3.15` |
| Removed dependency | `PyQt6` (`GPL-3.0-only` or commercial) - not declared, not imported anywhere |
| Guard | `tests/test_qt_independence.py` fails if any module outside `qtcompat/` imports a binding |

What this means for distribution:

1. **Source-only distribution** (GitHub repository, sdist): you distribute your own code and declare
   PySide6 as a dependency, so the LGPL obligations on the library are not triggered by that act.
2. **Binary distribution** (the AppImage, which bundles PySide6 and the Qt libraries): LGPL
   obligations apply. The distributing party must supply the LGPL-3.0 text and the Qt/PySide6
   notices (the wheel does not), and must satisfy the requirement that a recipient can replace or
   relink the LGPL libraries - a genuinely open question for a read-only single-file AppImage.
3. The **full** third-party audit must be rerun after the migration, before recommending MIT,
   BSD-3-Clause or Apache-2.0. See [LICENSE_OPTIONS.md](LICENSE_OPTIONS.md) section 2.

### This project's own licence (decided 2026-09-16)

nmrforge's own code is licensed under the **Apache License 2.0** (`Apache-2.0`, SPDX); the text is at
the repository root as [LICENSE](LICENSE) and `pyproject.toml` declares the same expression. The
copyright holder is recorded as "Xuanfeng Li (李宣锋)". **LGPL is used only for what a packaged distribution
bundles**: the AppImage's Qt/PySide6 libraries (see the next section). Installing from source pulls
Qt in as a separate package and triggers no LGPL obligation on this project. Third-party components
keep their own licences regardless of the project licence above.

### Author list, affiliation, and IP ownership

Confirmed 2026-09-23: `CITATION.cff` names the author as "Li, Xuanfeng" (李宣锋) with the
affiliation University of Science and Technology of China (中国科学技术大学, ROR
https://ror.org/04c4dkn09), and it carries no placeholders. `.zenodo.json` declares the same
creator and affiliation for the archived release, and `NOTICE` records the copyright holder.

Still to be confirmed by the rights holder (it does not change the Apache-2.0 grant already in
`LICENSE`, and it only becomes relevant if the copyright line is ever changed to name an
institution):

- whether the institution/laboratory claims copyright over this software;
- whether the software was created under a funding agreement with redistribution conditions.

## 6. Verification gaps

- Matplotlib ships a project-specific licence text (PSF-derived) rather than an SPDX
  identifier; it is BSD-compatible, but the exact text should be reproduced verbatim if a
  binary distribution is ever published.
- NMRPipe/SMILE are distributed free of charge for academic use; their precise redistribution
  terms are stated inside the downloaded package rather than on a web page. Because nothing is
  bundled here, they are only listed as external prerequisites.
- `reportlab` is used for PDF report export; confirm that the version pinned in a future
  release is the BSD-licensed open-source edition (not the commercial ReportLab Plus).

---

## 7. Full dependency inventory (audit rerun after the PySide6 migration)

Generated with `python scripts/audit_third_party.py --csv <file>` against the environment that
gets bundled (Windows development venv, 2026-09-16, PySide6 6.11.2). Re-run it in the AppImage
build venv before any release: the build machine's set is what actually ships.

Result: **28 permissive, 4 weak copyleft (the Qt/PySide6 set), 0 strong-copyleft-only**. The
project's own distribution declares no licence, which is the pending decision, not a third-party
finding. Every audited distribution ships at least one licence file - with the documented
exception that the PySide6 wheels ship only `LicenseRef-Qt-Commercial.txt` and no LGPL text, which
is why the texts are vendored in `packaging/linux/THIRD_PARTY_LICENSES/`.

| Package | Version | Licence as declared | Class | Licence files | Direct dep |
| --- | --- | --- | --- | --- | --- |
| charset-normalizer | 3.4.9 | MIT | permissive | 1 | no |
| colorama | 0.4.6 | BSD License | permissive | 1 | no |
| contourpy | 1.3.3 | BSD License | permissive | 1 | no |
| cycler | 0.12.1 | BSD License | permissive | 1 | no |
| fonttools | 4.63.0 | MIT | permissive | 2 | no |
| iniconfig | 2.3.0 | MIT | permissive | 1 | no |
| kiwisolver | 1.5.0 | BSD License | permissive | 1 | no |
| matplotlib | 3.11.1 | Python Software Foundation License | permissive | 3 | yes |
| nmrforge | 0.2.199 | (none declared) | unknown | 1 | no |
| nmrglue | 0.11 | New BSD License | permissive | 1 | yes |
| numpy | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | permissive | 20 | yes |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | permissive | 5 | no |
| pandas | 3.0.5 | BSD License | permissive | 1 | yes |
| pillow | 12.3.0 | MIT-CMU | permissive | 1 | no |
| pip | 26.2.1 | MIT | permissive | 44 | no |
| pluggy | 1.6.0 | MIT | permissive | 1 | no |
| Pygments | 2.21.0 | BSD-2-Clause | permissive | 2 | no |
| pyparsing | 3.3.2 | MIT | permissive | 1 | no |
| pyqtgraph | 0.14.0 | MIT | permissive | 1 | yes |
| PySide6 | 6.11.2 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | weak-copyleft | 1 | yes |
| PySide6_Addons | 6.11.2 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | weak-copyleft | 1 | no |
| PySide6_Essentials | 6.11.2 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | weak-copyleft | 1 | no |
| pytest | 9.1.1 | MIT | permissive | 1 | yes |
| python-dateutil | 2.9.0.post0 | BSD License OR Apache Software License | permissive | 1 | no |
| PyYAML | 6.0.3 | MIT | permissive | 1 | yes |
| reportlab | 5.0.0 | BSD license (see license.txt for details), Copyright (c) 2000-2025, ReportLab Inc. | permissive | 1 | yes |
| ruff | 0.16.5 | MIT | permissive | 1 | yes |
| scipy | 1.18.0 | BSD License | permissive | 5 | yes |
| Send2Trash | 2.1.0 | BSD-3-Clause | permissive | 1 | yes |
| shiboken6 | 6.11.2 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | weak-copyleft | 1 | no |
| six | 1.17.0 | MIT | permissive | 1 | no |
| tzdata | 2026.3 | Apache-2.0 | permissive | 2 | no |
| vulture | 2.16 | MIT License | permissive | 1 | no |

How to read the *Class* column: it is the best option available in the declared expression, not a
verdict on the whole expression. `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` is weak copyleft
because the LGPL option can be used; a bare `GPL-3.0-only` would be strong copyleft and would
constrain the project licence.

Weak-copyleft components and what they require of a *binary* distribution:

| Component | Requirement |
| --- | --- |
| Qt libraries, PySide6, shiboken6 | Ship the LGPL-3.0 text and the Qt notices (done: `packaging/linux/THIRD_PARTY_LICENSES/`, copied into the AppImage by the build script) and let the recipient replace or relink the libraries (done: the build script accepts `PYSIDE6_REQUIREMENT` / `SHIBOKEN6_REQUIREMENT` / `EXTRA_PIP_ARGS`, see `NOTICE.md`) |
