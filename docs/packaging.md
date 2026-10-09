# Packaging

The v1.0.5 source and AppImage release includes the source and one Linux AppImage (API v1.1.1). This
patch packages existing phase/NUS fixes and evidence organization; API parameters and the 38-column
peak-table contract are unchanged. See the [release page](https://github.com/RociferX/nmrforge/releases)
for artifact availability and validation status. Version 1.0.2 is retained as a historical release.
These version numbers describe separate interfaces.
Source installation and wheel packages include the runtime resources.

## Linux AppImage

The build entry point is
[packaging/linux/build_appimage.sh](../packaging/linux/build_appimage.sh).
Run it on Linux with Python 3.12 or newer and appimagetool available. The
script creates one Linux artifact and performs its configured packaging
checks. This page does not claim a new artifact has been built or validated.

The build packages the application dependencies declared in pyproject.toml
and runtime resources from nmrforge_data, gui/assets, and ui_support/locales.
The output is named NMRForge-<version>-<arch>.AppImage under
build/appimage/. Release information identifies the source revision
represented by a distributed artifact.

NMRPipe and SMILE are external tools supplied and installed separately by
the user. They are not bundled in the source package or AppImage. NMRPipe is
used for conversion and processing; SMILE is used for NUS reconstruction.
See [external dependencies](external-dependencies.md) for their runtime roles.

## Source and third-party notices

The source is licensed under [Apache-2.0](../LICENSE). Binary distributions
may contain third-party components with separate licence terms. For the
AppImage:

- Bundled licence texts and notices are in
  packaging/linux/THIRD_PARTY_LICENSES/.

- Component source and hash records are in
  [PROVENANCE.txt](../packaging/linux/THIRD_PARTY_LICENSES/PROVENANCE.txt).

- The bundled-component notice is
  [NOTICE.md](../packaging/linux/THIRD_PARTY_LICENSES/NOTICE.md).

- Project distribution notes are in [THIRD_PARTY.md](../THIRD_PARTY.md) and
  [LICENSE_OPTIONS.md](../LICENSE_OPTIONS.md).

The build includes third-party licence materials with the AppImage under
usr/share/doc/NMRForge/third-party/. When distributing a binary, retain the
notices and licence files that correspond to the components actually
included.
