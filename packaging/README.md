# packaging/

Distribution packaging assets.

> v0.11.0 ships **both the source and the Linux AppImage** (one artefact; the interface language is
> switched at run time). This directory holds the build assets; the release gate and its record are
> in the release checklist kept in the maintainer's private repository.

| Path | Role |
| --- | --- |
| `linux/NMRForge.spec` | the PyInstaller spec, including the PySide6 hidden imports |
| `linux/build_appimage.sh` | the end-to-end AppImage build |
| `linux/NMRForge.desktop`, `linux/icons/` | desktop entry and icon |
| `linux/THIRD_PARTY_LICENSES/` | the licence texts, NOTICE and provenance notes bundled into the binary |

Bundling Qt/PySide6 into a binary creates LGPL-3.0 obligations for the distributed artifact, which
is separate from the Apache-2.0 licence that covers the source. The analysis and the dependency
inventory are in [`LICENSE_OPTIONS.md`](../LICENSE_OPTIONS.md) and
[`THIRD_PARTY.md`](../THIRD_PARTY.md).
