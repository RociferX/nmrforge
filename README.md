# nmrForge

![nmrForge](gui/assets/nmrforge.png)

nmrForge automates Bruker multidimensional NMR processing, parameter optimization, and quality control with NMRPipe. It provides a desktop interface, command-line tools, and a Python API, and records the resolved parameters and outputs.

The current source release is **1.0.4**. It supports 2D and 3D uniform data, and 2D and 3D NUS processing; batch processing is currently limited to 2D. The Linux AppImage currently available is **1.0.2** and is a separate, older release. The source API contract is **1.1**; API version and application version are separate.

## Install and start on Linux

Python 3.12 or newer is required. From a checkout:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install .
python main.py
```

For real processing, install **NMRPipe** separately and ensure its programs are available to nmrForge. **SMILE**, distributed with NMRPipe, is needed for NUS reconstruction. nmrForge does not bundle or install either tool. The desktop application requires a graphical session.

## API quick start

The command-line API is available after installation:

```bash
python -m nmrforge_api --help
```

See the [API guide](docs/external-api/README.md) for Python and CLI examples, and the [API reference](docs/README.md) for the focused entry points. A short walkthrough is available in [examples/quickstart.py](examples/quickstart.py); [examples/make_synthetic_dataset.py](examples/make_synthetic_dataset.py) creates a small sample dataset.

## Evidence

The [real-data comparison report](docs/evidence/real-data-comparison.md) covers four examples:

- BMRB 27493: 2D uniform
- Controlled 2D NUS: 75% requested, 68/90 increments (75.56% actual)
- BMRB 15750: 3D uniform
- BMRB 52533: acquired 3D NUS at 25%

The main signals agree well with the stated references across these four cases, supporting reliable routine processing. Candidate matching is not assigned-peak recovery; the report records the scope and limits.

## Documentation and project information

- [Documentation index](docs/README.md) · [GUI guide](docs/gui.md) · [Installation details](docs/installation.md)
- [Release notes and downloads](https://github.com/RociferX/nmrforge/releases)
- Citation metadata: [CITATION.cff](CITATION.cff); concept DOI: [10.5281/zenodo.22909415](https://doi.org/10.5281/zenodo.22909415)
- Source license: [Apache-2.0](LICENSE); copyright notice: [NOTICE](NOTICE); [third-party notices](THIRD_PARTY.md) and [license options](LICENSE_OPTIONS.md) (bundled Qt/PySide6 libraries use LGPL-3.0)
- [Contribution guide](CONTRIBUTING.md) · [Security policy](SECURITY.md)

The desktop interface follows the system language. To use Chinese for a run, set `NMRFORGE_LANG=zh`; the Chinese documentation is in [Chinese_version](Chinese_version/README.md).
