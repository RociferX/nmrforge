# Third-party notices for the NMRForge AppImage

This directory is copied **into** every built AppImage (see `../build_appimage.sh`), so the notices
travel with the binary that actually distributes the libraries. Extract them from an AppImage with:

    ./NMRForge-<version>-x86_64.AppImage --appimage-extract
    # then: squashfs-root/usr/share/doc/NMRForge/third-party/

or ask the application to print them:

    ./NMRForge-<version>-x86_64.AppImage --licenses

## What the AppImage bundles, and under which licence

| Component | Version bundled | Licence option relied on | Copyright |
| --- | --- | --- | --- |
| Qt libraries (`Qt6Core`, `Qt6Gui`, `Qt6Widgets`, plugin libraries, ...) | 6.11.2 (from `PySide6-Qt6`/`PySide6_Essentials`/`PySide6_Addons` wheels) | **LGPL-3.0-only** (Qt is triple-licensed: LGPL-3.0 / GPL-2.0 / GPL-3.0 / commercial) | The Qt Company Ltd. and contributors |
| PySide6, PySide6_Essentials, PySide6_Addons | 6.11.2 | **LGPL-3.0-only** | The Qt Company Ltd. |
| shiboken6 | 6.11.2 | **LGPL-3.0-only** | The Qt Company Ltd. |
| Python runtime | 3.12 (bundled by PyInstaller) | PSF-2.0 | Python Software Foundation |
| pyqtgraph | 0.14.0 | MIT | pyqtgraph contributors |
| NumPy, SciPy, pandas, Matplotlib, nmrglue, reportlab, Send2Trash, ... | see `THIRD_PARTY.md` in the repository | permissive (BSD-3-Clause / MIT / PSF-derived) | respective authors |

The licence expression declared by the PySide6/shiboken6 distributions is
`LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`. This distribution relies on the **LGPL-3.0-only**
option; the full texts of LGPL-3.0 and of GPL-3.0 (which LGPL-3.0 incorporates by reference) are
shipped next to this file as `LGPL-3.0.txt` and `GPL-3.0.txt`.

Full texts of the permissive licences (MIT / BSD-3-Clause / PSF) for the remaining bundled
components are their own package licences; the authoritative list of components and their licences
is `THIRD_PARTY.md` in the source repository, and every wheel's `dist-info/licenses/` directory is
likewise present inside the AppImage under `_internal/`.

## Corresponding source

The exact upstream versions are pinned and public, so the corresponding source is obtainable
directly from the distributors:

    pip download PySide6==6.11.2 shiboken6==6.11.2 --only-binary=:all: --dest ./wheels
    # sources:  https://download.qt.io/official_releases/QtForPython/pyside6/
    #           https://code.qt.io/cgit/pyside/pyside-setup.git/
    # Qt itself: https://download.qt.io/archive/qt/6.11/6.11.2/single/

NMRForge's own corresponding source is the public repository at the commit recorded in
`CITATION.cff` / the AppImage's `--licenses` output.

## Replacing or relinking the LGPL libraries

The LGPL requires that a recipient be able to replace the LGPL-covered libraries with a modified
version and still run the program. An AppImage is a single read-only squashfs, so the practical
mechanism is a documented rebuild:

1. Build (or obtain) your own PySide6/shiboken6 — the same or a modified build.
2. Rebuild the AppImage with your libraries instead of the default ones:

       PYSIDE6_REQUIREMENT="PySide6 @ file:///path/to/your/PySide6-....whl" \
       EXTRA_PIP_ARGS="--find-links /path/to/your/wheels" \
       bash packaging/linux/build_appimage.sh

   `build_appimage.sh` reads `PYSIDE6_REQUIREMENT`, `SHIBOKEN_REQUIREMENT` and `EXTRA_PIP_ARGS`
   exactly for this purpose, and prints the versions it actually resolved.
3. The build script and the PyInstaller spec are part of the public source, so no additional
   material is needed to perform the relink.

> **TODO (owner, before publishing a binary):** have this notice reviewed by whoever owns the IP.
> It states the compliance approach but is not legal advice, and Qt's own licensing FAQ is the
> authoritative reference for the LGPL obligations.

## Why there is no separate Qt notice file

The PySide6 wheels ship **only** `LicenseRef-Qt-Commercial.txt` and no LGPL text, which is why the
texts above are vendored here. Qt's copyright notices are reproduced in the table in this file;
the per-module notices remain inside the bundled wheels.
