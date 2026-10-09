# Licensing and distribution

NMRForge's source code is released under **Apache-2.0**. The complete licence is in
[LICENSE](LICENSE), with the copyright and project notices in [NOTICE](NOTICE).
Third-party components retain their own terms; see [THIRD_PARTY.md](THIRD_PARTY.md).

## Source and external engines

The source repository declares Python dependencies for installation. NMRPipe and SMILE are
separately obtained processing engines and are not included in the source repository or AppImage.
Their availability, licences, and citation requirements are described in the third-party inventory.

## Packaged applications

The GUI uses **PySide6/Qt**, which offers an **LGPL** licensing option. An AppImage bundles these
and other libraries, so redistribution must follow the terms and notices of the components in
that particular binary. The Apache-2.0 licence of NMRForge's own source does not replace them.

The bundled notices and library-replacement/rebuild instructions are in
[packaging/linux/THIRD_PARTY_LICENSES/NOTICE.md](packaging/linux/THIRD_PARTY_LICENSES/NOTICE.md).
See [packaging](docs/packaging.md) for the source/binary version boundary and build provenance.
