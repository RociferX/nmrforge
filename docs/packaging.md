# Packaging

NMRForge software **1.0.5** and scripting API **1.1.1** have separate version numbers. The release
provides source and a Linux AppImage. The AppImage is the simplest desktop installation path;
NMRPipe and SMILE must be obtained and installed separately. NMRPipe handles conversion and
processing, while SMILE performs NUS reconstruction.

## Linux AppImage

Build on Linux with Python 3.12 or newer, pip, and `appimagetool` available on `PATH`:

```bash
bash packaging/linux/build_appimage.sh
```

The script reads the software version from `core.__version__` and architecture from `uname -m`.
It creates a build virtual environment, installs the project in editable mode and PyInstaller, and
uses [`packaging/linux/NMRForge.spec`](../packaging/linux/NMRForge.spec) to freeze `main.py`. The
single output is `build/appimage/NMRForge-<version>-<arch>.AppImage`.

The spec includes runtime resources at their package-relative locations:
`nmrforge_data/config`, `nmrforge_data/presets`, `nmrforge_data/tutorial`, `gui/assets`, and
`ui_support/locales`. It also includes the project licence/notice and collects installed
dependency licence files. The build checks the vendored third-party licence texts before freezing,
checks the frozen resource directories and language catalogues, and launches the frozen GUI for a
20-second offscreen startup smoke check. It writes `usr/share/doc/NMRForge/BUILD_INFO.txt` with the
version, source commit, worktree state, build time, host, Python version, and selected dependency
versions. `AppRun` starts `usr/bin/NMRForge`, provides the `--licenses` option, and installs a
desktop entry and icon on first launch; `--remove-desktop` removes that integration, and
`NMRFORGE_NO_DESKTOP=1` skips it.

The AppImage does not contain NMRPipe or SMILE. Users install those tools separately and configure
or expose them through the supported discovery paths described in [external dependencies](external-dependencies.md).

## Source installs and wheel resources

The project supports editable development installs and wheel installation. `pyproject.toml`
explicitly lists packaged resources: the default `nmrforge_data/config/nmrforge.yaml`, presets,
tutorials, GUI assets, and runtime language catalogues. `nmrforge.local.yaml` is a machine-local
override and is excluded. The `core`, `backend`, `workflow`, and `nmrforge_api` package README files
are also included because the compatibility `behavior_digest` covers every file in those trees;
omitting them would make the installed-package fingerprint disagree with its declaration.

Language resources are listed individually. `default.json`, `en.json`, and `zh.json` are used at
runtime. `source.json` and `converted.json` support the development interface-text checks and are
not runtime catalogues. This explicit package-data list keeps local configuration and tooling files
out of distributions while ensuring `core/app_paths.py` finds the same runtime layout in a checkout,
installed package, and frozen application.

## Licences and notices

NMRForge source is licensed under [Apache-2.0](../LICENSE). The AppImage bundles third-party
components, including Qt/PySide6 under its LGPL option. The build copies licence texts, provenance,
and the component notice from `packaging/linux/THIRD_PARTY_LICENSES/` to
`usr/share/doc/NMRForge/third-party/`; dependency licence files are retained inside the frozen
bundle, and the project licence and notice are included as well. The application can print the
third-party notice locations with `--licenses`.

To rebuild with another PySide6/shiboken6 wheel, the build script accepts
`PYSIDE6_REQUIREMENT`, `SHIBOKEN6_REQUIREMENT`, and `EXTRA_PIP_ARGS`, for example:

```bash
PYSIDE6_REQUIREMENT="PySide6 @ file:///path/to/PySide6.whl" \
EXTRA_PIP_ARGS="--find-links /path/to/wheels" \
bash packaging/linux/build_appimage.sh
```

The replacement wheel must match the build platform and the PySide6/shiboken6 pair. The script and
spec provide the inputs to rebuild the application with those libraries. See the bundled
[third-party notice](../packaging/linux/THIRD_PARTY_LICENSES/NOTICE.md) for component and source
details.
