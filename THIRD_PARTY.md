# Third-Party Dependencies

This file records every third-party component that nmrForge depends on, whether it is
bundled with this repository, and under which terms it is used.

Status note: this inventory was produced during public-release preparation. Items marked
**BLOCKER** must be resolved by the repository owner before the repository is made public.
Nothing here is legal advice.

## 1. Summary table

| Name | Purpose | Bundled or external | License (declared by upstream) | Official source | Required / optional |
| --- | --- | --- | --- | --- | --- |
| PyQt6 | Desktop GUI toolkit bindings | external (pip) | **GPL-3.0-only** (or commercial from Riverbank) | https://pypi.org/project/PyQt6/ | required for GUI and viewer |
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
2. No third-party licensed script is vendored under `scripts/` or `presets/`.
3. `presets/*.yaml` and the code in `core/`, `backend/`, `workflow/`, `gui/`, `viewer/`,
   `nmrforge_api/` are original project code. The pulse-program keyword lists they contain are
   identifiers (e.g. `hsqc-sample`), not copied source code.
4. Icons under `gui/assets/` and `packaging/linux/icons/` are project assets.
5. Users who build the Linux AppImage (`packaging/linux/build_appimage.sh`) are responsible for
   complying with the terms of every component they pack.

## 5. BLOCKERS

### [BLOCKER] PyQt6 is GPL-3.0-only

`PyQt6` is distributed by Riverbank Computing under **GPL-3.0-only** (or a paid commercial
license). Its installed metadata states:

    License-Expression: GPL-3.0-only

Consequences for public release (note that publishing the source on GitHub is itself
distribution, so recommending the AppImage does not avoid this):

- The GUI main window (`gui/`) and the standalone spectrum viewer (`viewer/`) import PyQt6
  directly, and `pyproject.toml` lists `PyQt6>=6.5` as a **required** runtime dependency.
- If nmrForge is published under a permissive license (MIT / BSD-3-Clause / Apache-2.0) while
  the GUI depends on PyQt6, the distributed combination conflicts with the GPL: a GPL-3.0
  component cannot be relicensed under a permissive licence by adding it to this project.
- Publishing the source with no `LICENSE` file (the current state) does not resolve this; it
  only leaves the terms undefined.

Note that the non-GUI layers are already Qt-free, which keeps the options open:

    python -c "import sys, core, nmrforge_api; print([m for m in sys.modules if m.startswith('PyQt')])"
    # -> []

Possible resolutions (owner decision, see LICENSE_OPTIONS.md):

1. **Release nmrForge under GPL-3.0-only** (or `GPL-3.0-or-later`). Simplest, fully consistent
   with the current dependency set.
2. **Migrate the GUI from PyQt6 to PySide6** (LGPL-3.0). This preserves the option of a
   permissive licence for the project, at the cost of a GUI migration.
3. **Acquire a commercial PyQt6 licence** and document that fact; this is a cost/legal
   question for the owner, not a code change.

Until the owner decides, no `LICENSE` file is committed.

### [BLOCKER] Author list, affiliation, and IP ownership not confirmed

`CITATION.cff` contains placeholders (see the `TODO` entries). The owner must confirm:

- who the authors are and in which order;
- whether the institution/laboratory claims copyright or imposes a copyright notice;
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