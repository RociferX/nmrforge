# packaging/

Distribution packaging assets.

> The v0.9.0 release is **source only**. The AppImage described here is deferred and is not a
> release artifact; see [`APPIMAGE_RELEASE_CHECKLIST.md`](../APPIMAGE_RELEASE_CHECKLIST.md).

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
