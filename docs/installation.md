# Installation

Linux is the target runtime; Windows may be used to edit source but is not a supported runtime
environment. Linux desktop users can use the AppImage from the [NMRForge 1.0.5 Release](https://github.com/RociferX/nmrforge/releases/tag/v1.0.5).
Install the Python API and command-line tools separately by following the source steps below.

## AppImage (Linux)

Download `NMRForge-1.0.5-x86_64.AppImage` and `SHA256SUMS-v1.0.5.txt` from the release, then verify
and start the AppImage in the same directory:

```bash
sha256sum -c SHA256SUMS-v1.0.5.txt
chmod +x NMRForge-1.0.5-x86_64.AppImage
./NMRForge-1.0.5-x86_64.AppImage --licenses
./NMRForge-1.0.5-x86_64.AppImage
```

The AppImage includes third-party licence notices, including those for PySide6/Qt. See the
release assets and [THIRD_PARTY.md](../THIRD_PARTY.md) for distribution information.

## Source installation

```bash
git clone https://github.com/RociferX/nmrforge.git
cd nmrforge
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
python main.py
```

Packaged installs and wheels include runtime resources (`nmrforge_data/config`,
`nmrforge_data/presets`, `gui/assets`, `ui_support/locales`). Real processing requires a separately
installed NMRPipe; NUS reconstruction also requires SMILE. Neither tool is downloaded or bundled
by nmrForge.

## Developers: editable source install

```bash
git clone <this repository>
cd nmrForge
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Optional dependency groups (declared in `pyproject.toml`):

```bash
pip install -e ".[test]"    # pytest
pip install -e ".[dev]"     # pytest + ruff
pip install -e ".[docs]"    # documentation tooling
```

`python main.py` creates or reuses the local application environment on first run.

The editable install exposes one console script:

| Command | What it does |
| --- | --- |
| `nmrforge-viewer` | Standalone spectrum viewer (`python -m viewer` works too) |

Use `python main.py` to start the main GUI from an editable checkout.

The processing command line lives in the `nmrforge_api` package:

```bash
python -m nmrforge_api --help
```

## Verifying an installation

```bash
python -m pytest -q                                       # full test suite
python -m ruff check .                                    # static checks
python -c "import core, nmrforge_api, gui, viewer; print('imports ok')"
python examples/make_synthetic_dataset.py --out example_data/hsqc_2d
python examples/quickstart.py example_data/hsqc_2d
```

GUI tests need a display or an offscreen Qt platform:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

## Upgrading and uninstalling

```bash
pip install -e .            # re-run after editing pyproject.toml or entry points
pip uninstall nmrforge      # removes the package and its console scripts
```

Uninstalling never deletes your projects or studies: those live in the directories you chose.
