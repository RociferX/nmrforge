# Installation

## Source and binary releases

The GitHub repository contains source. The [releases page](https://github.com/RociferX/nmrforge/releases)
may also provide a versioned Linux AppImage. An AppImage contains the source revision named in its
release; later source changes do not update that binary automatically. For the current source tree,
use an editable install:

```bash
git clone https://github.com/RociferX/nmrforge.git
cd nmrforge
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[test]"
python main.py
```

Packaged installs and wheels include runtime resources (`nmrforge_data/config`,
`nmrforge_data/presets`, `gui/assets`, `ui_support/locales`). Real processing requires a separately
installed NMRPipe; NUS reconstruction also requires SMILE. Neither tool is downloaded or bundled
by nmrForge.

## AppImage (Linux)

When an AppImage is available on the releases page, it bundles its interpreter and Qt. The release
notes identify its version and checksums. For example:

```bash
sha256sum NMRForge-<version>-x86_64.AppImage  # compare with the release notes
chmod +x NMRForge-<version>-x86_64.AppImage
./NMRForge-<version>-x86_64.AppImage --licenses
./NMRForge-<version>-x86_64.AppImage
```

The AppImage includes third-party licence notices, including those for PySide6/Qt. See the
release assets and [THIRD_PARTY.md](../THIRD_PARTY.md) for distribution information.

## Developers: editable source install

```bash
git clone <this repository>
cd nmrForge
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
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
QT_QPA_PLATFORM=offscreen python -m pytest -q      # Windows: set QT_QPA_PLATFORM=offscreen
```

## Upgrading and uninstalling

```bash
pip install -e .            # re-run after editing pyproject.toml or entry points
pip uninstall nmrforge      # removes the package and its console scripts
```

Uninstalling never deletes your projects or studies: those live in the directories you chose.
