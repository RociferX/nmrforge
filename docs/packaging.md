# AppImage packaging solution (NMRForge)

Status: **released with v0.11.0**. The source stays Apache-2.0; the Linux AppImage ships in the
release. **Since 2026-09-22 the release carries one artefact**, with the interface language chosen at
run time; it is built from the released source commit recorded in
`usr/share/doc/NMRForge/BUILD_INFO.txt`.

This page records the Linux AppImage build plan. Before every release the root
`APPIMAGE_RELEASE_CHECKLIST.md` must be completed, and the licences of the bundled PySide6/Qt and
other components must be re-checked.

## Release strategy (PACK-015, decided 2026-09-12)

- **v0.11.0 ships both the source and the AppImage.** The AppImage is
  built by PyInstaller; `nmrforge_data/config`, `nmrforge_data/presets`, `gui/assets` and
  `ui_support/locales` reach `_MEIPASS` through the spec's `datas` (the data package keeps its
  shape), and resources are located through `core/app_paths.py` and `ui_support/i18n.py`.
- **One artefact, runtime language (2026-09-21).** Now that UI strings are `tr("English text")`
  plus the `ui_support/locales/zh.json` lookup table, the build script produces a single
  `NMRForge-<version>-<arch>.AppImage`; the language is resolved as
  "`NMRFORGE_LANG`/`NMRFORGE_LANGUAGE` (temporary pin), then the preference stored in
  `nmrforge.local.yaml` (`language:`), then the system locale (QLocale / `LANG` / `LC_ALL`),
  falling back to the tree's `ui_support/locales/default.json`" (changeable in the GUI, see
  [gui.md](gui.md)). An artefact built from this public English tree therefore
  defaults to English and one built from the maintainer's trunk defaults to Chinese, and both
  carry `zh.json` - Chinese and English users download the same file. The spec also excludes the
  Qt modules this project does not use (size only); delete an entry from `_EXCLUDED_QT_MODULES`
  when a feature needs it. `PySide6.QtTest` must **not** be excluded -
  `qtcompat/__init__.py` imports it unconditionally (noted in the spec and guarded by a test).
- **`pip install .` / a wheel is supported (2026-09-21, option A)**: the runtime resources no
  longer sit in the repository root - the default config and the experiment templates moved into
  the data package `nmrforge_data/` (`config/`, `presets/`), and `gui/assets` plus
  `ui_support/locales` ship as `package-data` (see `[tool.setuptools.package-data]` in
  `pyproject.toml`). The **relative position is the same in a checkout and when installed**, so
  `resource_path("config/nmrforge.yaml")` and `resource_path("presets")` resolve in both;
  an editable install (`pip install -e .`) stays the recommended development path. The four in-package `core|backend|workflow|nmrforge_api/README.md` files ship as well -
  `behavior_digest` covers *every* file under those trees, so without them an installed copy
  computes a different fingerprint and `compat_verified` is permanently false. Conversely
  `ui_support/locales/{source.json,converted.json}` are tooling lists for
  `scripts/i18n_extract_ui.py` (read during development/CI only - the runtime reads
  `default.json`/`en.json`/`zh.json`), so `package-data` names the packs one by one instead
  of globbing `locales/*.json` (`package_data` is expanded from the filesystem and
  `exclude-package-data` does not apply to explicit `package_data`). Delete `build/` before
  rebuilding locally: `setuptools.build_py` does not purge `build/lib`, so files from an
  earlier `package-data` land in the next wheel (CI builds on a fresh checkout and is
  unaffected).
  Publishing to
  PyPI is a separate step that has not happened yet.
- `pyproject.toml` keeps a single console entry point, `nmrforge-viewer` (the standalone spectrum
  viewer), which does not depend on repository-root resources. The main GUI entry is `main.py`
  (started by the AppImage) and is not published as a console script.
- Regression gate: `tests/test_audit_rest_fixes.py` locks down that the spec's `datas` covers the
  four runtime resource directories, so the AppImage cannot silently end up missing resources
  after a packaging edit.

## Product boundary

**Packaged into AppImage: **.

- The application itself (Python bytecode + PySide6 + pyqtgraph + numpy/scipy/matplotlib/nmrglue and other dependencies)
- nmrforge_data/config and nmrforge_data/presets (into _MEIPASS via PyInstaller datas, see core/app_paths.py)
- Application icon and desktop file

**Not packaged (discovered at runtime): **.

- NMRPipe/SMILE and other external backends: follow the runtime search strategy of NMRFlow
  (PATH -> csh environment ~/.cshrc NMRPIPEBIN -> common installation directory), AppImage is not built-in.
  The application within the AppImage uses `core/app_paths.py` to locate its own resources, regardless of the external backend.

## Build process (executed on Linux Builder/VM)

```bash
# 1) install appimagetool (once only)
#    https://github.com/AppImage/appimagetool/releases

# 2) build (inside the script: venv -> pip install -e . -> PyInstaller -> AppDir -> appimagetool)
packaging/linux/build_appimage.sh

# product: build/appimage/NMRForge-<core.__version__>-x86_64.AppImage
#      (the version comes from core.__version__ and is not hard-coded here)
```

Key steps:

1. `python3 -m venv` + `pip install -e.` (depends on the same as pyproject.toml).
2. PyInstaller is packaged by packaging/linux/NMRForge.spec: entry main.py.
   Datas contains nmrforge_data/config and nmrforge_data/presets; `console=False` (Qt GUI).
   Two build-time checks run straight afterwards: the **runtime-resource self-check** (looking
   inside the `_internal` contents directory for `nmrforge_data/{config,presets}`,
   `gui/assets`, `ui_support/locales` and the catalogues) and the **frozen start-up smoke**
   (a dedicated HOME plus `QT_QPA_PLATFORM=offscreen`, requiring the packaged executable to
   survive 20 s). Both are needed: files present does not mean it runs - on 2026-09-21 the
   first real build had every file and still crashed on start because `PySide6.QtTest` was
   excluded while `qtcompat` imports it unconditionally.
3. Assembly AppDir: `usr/bin/NMRForge` (executable + _internal).
   `usr/share/applications/NMRForge.desktop`,
   `usr/share/icons/hicolor/256x256/apps/nmrforge.png`.
4. Generate executable `AppRun` (appimagetool **not** automatically generates). AppRun built-in desktop.
   Integration: first/Automatically after moving desktop portal and icons are installed into `~/.local/share` (Exec/.
   TryExec points to the real path of AppImage, and the menu item is automatically hidden after directly deleting the AppImage file);
   `./NMRForge.AppImage --remove-desktop` (or `--uninstall-desktop`) can be removed.
   Entry and icon; `NMRFORGE_NO_DESKTOP=1` Skip self-installation.
5. `appimagetool AppDir` Generate a single file AppImage (runtime preferentially uses local cache.
   `~/.cache/nmrforge-appimage/runtime-<arch>`, can be overridden by `RUNTIME_FILE`).

## Version and naming

- Version single source: `core/__init__.py` of `__version__` (automatically read by the build script)
- Product name: `NMRForge-<version>-<arch>.AppImage`
- Release rhythm: Build after each tag; CI (GitHub Actions Linux runner) can be added later

## Known points to note

- **pyinstaller-hooks-contrib hook-workflow conflict**: general hook-workflow of contrib
  Will treat the local top-level package `workflow/` as the PyPI distribution package and `copy_metadata('workflow')`.
  Build report PackageNotFoundError. Used packaging/linux/hooks/hook-workflow.py.
  Empty hook masking (spec `hookspath` takes effect).
- **hookspath path benchmark**: PyInstaller's hookspath parsed by process cwd (build script
  Run at the root of the warehouse), which is different from the spec directory benchmark of datas; must be used in spec.
  `os.path.abspath(os.path.join(SPECPATH, "hooks"))`, otherwise directory does not exist and is silenced.
  Jump over.
- **AppRun must come with it**: appimagetool does not generate AppRun, and when it is missing, AppImage will report after decompression
  "AppRun: No such file or directory".

## Build record (2026-09-02 first end-to-end verification)
- Authentication (desktop integration, isolation HOME): autoinstall -> desktop's Exec/TryExec points to
  AppImage; `--remove-desktop` Delete entry and icon; Run recovery again; `NMRFORGE_NO_DESKTOP=1`.
  No entry is created. Applications start normally.
- Runtime download jitter processing: appimagetool requires an Internet connection to download type2-runtime, GitHub
  Jitter will report "Failed to download runtime"; it can be extracted from appimagetool itself.
  (`--appimage-offset` + `dd`) and then put it into the cache directory, and the script will automatically reuse it.


- Builder: VM Ubuntu 22.04 (glibc 2.35), uv python 3.12.13
  PyInstaller 6.22.2, appimagetool continuous 8c8c91f, mksquashfs system package.
- Command: `PYTHON=<python3.12> PATH=$HOME/appimagetool:$PATH
  APPIMAGE_EXTRACT_AND_RUN=1 bash packaging/linux/build_appimage.sh`.
- Products: `build/appimage/NMRForge-0.1.0-x86_64.AppImage` (about 123 MB
  858 file, zstd squashfs).
- Verification: `_internal` contains nmrforge_data/config, nmrforge_data/presets, gui/assets; `ldd` has no missing system libraries;
  `timeout 15 env QT_QPA_PLATFORM=offscreen ./NMRForge-*.AppImage
  --appimage-extract-and-run` Exit code 124 (normal startup ended with timeout, no traceback).


- **glibc compatible**: PyInstaller does not statically link to glibc, and should be used on older distributions (such as Ubuntu 20.04/22.04)
  By building on it, the product can cover more target machines.
- **FUSE**: Some systems require `./NMRForge.AppImage --appimage-extract-and-run`
  Or set `APPIMAGE_EXTRACT_AND_RUN=1`; This is a common behaviour of AppImage, not a problem with this software.
- **Qt plug-in**: PyInstaller's PySide6 hook will automatically collect plug-ins; if
  "could not find or load the Qt platform plugin", verified in the packager.
  Smoke test under `QT_QPA_PLATFORM=offscreen`.
- **Path**: The code must always use `core/app_paths.py` to locate resources, and hard-coded absolute paths are prohibited
  Ensure that the AppImage can be moved to any directory to run.

## Verification checklist (after packaging)

```bash
./build/appimage/NMRForge-*.AppImage --appimage-extract-and-run   # starts up
QT_QPA_PLATFORM=offscreen ./build/appimage/NMRForge-*.AppImage    # runs without a display
ldd usr/bin/NMRForge | grep 'not found'                            # no missing system libraries
```

Packaging related files:

- packaging/linux/NMRForge.desktop: desktop entry
- packaging/linux/NMRForge.spec: PyInstaller configuration
- packaging/linux/build_appimage.sh: Build script with one click
- packaging/linux/icons/: application icon (SVG source + PNG product)
- Core/app_paths.py: develop/Frozen resource path analysis


## Licensing and Compliance (LGPL)

AppImage puts PySide6 and Qt libraries into the product, so they are libraries covered by **distribution LGPL**: the license text must be provided with the product.
The mechanism related to the statement, and allow the recipient to replace/Relink these libraries. has been solidified, and the build script will report an error when it fails instead of silently missing it:

| Mechanism | Position | Effect |
| --- | --- | --- |
| License text is included in the library | `packaging/linux/THIRD_PARTY_LICENSES/LGPL-3.0.txt`, `GPL-3.0.txt` | LGPL-3.0 are incorporated by reference GPL-3.0, so both are provided with the product (PySide6's wheel **does not have** any LGPL text) |
| Source and hash | `THIRD_PARTY_LICENSES/PROVENANCE.txt` | Record grab URL, byte count and SHA-256, is the only source of truth for verification |
| Statement | `THIRD_PARTY_LICENSES/NOTICE.md` | Lists the packaged components and the license options used, corresponding to the source code acquisition method, replace/Relink steps |
| Pre-build verification | `scripts/check_third_party_licenses.py` | Text missing/been changed, NOTICE Key content is deleted, and the build script will fail if it is no longer referenced or provided `--licenses` |
| Enter the product | `build_appimage.sh` Step 2.5 | Copy to `usr/share/doc/NMRForge/third-party/`, and write `BUILD_INFO.txt` (version, git commit, whether the workspace is dirty, build time, dependency version) |
| Runtime readme | `AppRun --licenses` | Print statement and each file location, self-checking in the product |
| Replaceable/Relink | `build_appimage.sh` of `PYSIDE6_REQUIREMENT` / `SHIBOKEN6_REQUIREMENT` / `EXTRA_PIP_ARGS` | Use self-built/Modified Qt binding rebuilds AppImage; the build script and spec are both in the open source code, so all the materials required for re-linking are available |

Build machine acceptance (in addition to normal startup testing):

```bash
python scripts/check_third_party_licenses.py                 # the texts and notices agree
python scripts/audit_third_party.py --csv /tmp/audit.csv      # full dependency-licence audit in the build environment
./build/appimage/NMRForge-*.AppImage --appimage-extract-and-run --licenses   # the artefact's own statement
find build/appimage/NMRForge.AppDir/usr/share/doc -type f     # should hold the LGPL/GPL texts, NOTICE and BUILD_INFO
```

> Note: `NOTICE.md` It is clearly marked that it needs to be reviewed by the rights holder; this section is a project implementation and compliance mechanism and does not constitute legal advice
