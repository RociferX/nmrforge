# Licensing and distribution

NMRForge's source code is licensed under **Apache-2.0**. The complete text is in
[LICENSE](LICENSE), and copyright and project notices are in [NOTICE](NOTICE). Retain these files
when redistributing source or modified source. Third-party components retain their own licence
terms; see [THIRD_PARTY.md](THIRD_PARTY.md) for the component inventory.

## Source installation

Source and wheel distributions declare Python dependencies in `pyproject.toml`. NMRPipe and SMILE
are external processing tools; they are not included in the source or wheel. The user obtains and
installs each tool separately when needed. See [external dependencies](docs/external-dependencies.md)
for their runtime roles.

## AppImage distribution

The AppImage bundles PySide6/Qt and other libraries. PySide6/Qt offers an LGPL licensing option;
the bundled component's notices and licence files travel with the AppImage in
`usr/share/doc/NMRForge/third-party/`. Dependency licence files and NMRForge's own licence/notice
are included in the frozen bundle. Run the AppImage with `--licenses` to print the notice locations
and display the third-party notice.

The build records the software version, source commit, build state, and selected dependency versions
in `usr/share/doc/NMRForge/BUILD_INFO.txt`. To rebuild with alternate PySide6/shiboken6 wheels, use
the public Linux build script and set the wheel requirements and pip options as needed:

```bash
PYSIDE6_REQUIREMENT="PySide6 @ file:///path/to/PySide6.whl" \
SHIBOKEN6_REQUIREMENT="shiboken6 @ file:///path/to/shiboken6.whl" \
EXTRA_PIP_ARGS="--find-links /path/to/wheels" \
bash packaging/linux/build_appimage.sh
```

See [AppImage packaging](docs/packaging.md) and the bundled-component
[NOTICE](packaging/linux/THIRD_PARTY_LICENSES/NOTICE.md) for the build inputs, notices, and
replacement/rebuild details.
