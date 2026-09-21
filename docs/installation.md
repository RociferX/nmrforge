# Installation

## Current release: v0.11.0 (source + Linux AppImage)

Both paths are supported: a **Linux AppImage** (bundling Python and Qt, so no system environment is
needed, with the interface language following the system locale; see the
[releases page](https://github.com/RociferX/nmrforge/releases)), or an editable install from the
repository:

```bash
git clone https://github.com/RociferX/nmrforge.git
cd nmrforge
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[test]"
python main.py
```

`pip install .` and a wheel are supported since 2026-09-21: the shipped data
(`nmrforge_data/config`, `nmrforge_data/presets`, `gui/assets`, `ui_support/locales`) travels
with the package, and the relative position is the same in a checkout and when installed. Real processing requires a separately installed NMRPipe; NUS
reconstruction also requires SMILE.

## AppImage (Linux)

v0.11.0 offers an AppImage on the releases page ([Releases](https://github.com/RociferX/nmrforge/releases)):
one artefact whose interface language is switched at run time. It bundles its own interpreter and
Qt; verify it with:

```bash
sha256sum NMRForge-0.11.0-x86_64.AppImage      # compare with the release notes
chmod +x NMRForge-0.11.0-x86_64.AppImage
./NMRForge-0.11.0-x86_64.AppImage --licenses  # third-party licences and build provenance
./NMRForge-0.11.0-x86_64.AppImage             # first run installs the desktop entry
```

They bundle PySide6/Qt, distributed under LGPL-3.0 alongside the Apache-2.0 licence of nmrForge's
own source. The build, licence and clean-machine acceptance record is
the release checklist kept in the maintainer's private repository.

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

`python main.py` bootstraps a local `nmrforge/` virtual environment on first run and reuses it
afterwards. A future AppImage would wrap the same entry point.

The editable install exposes one console script:

| Command | What it does |
| --- | --- |
| `nmrforge-viewer` | Standalone spectrum viewer (`python -m viewer` works too) |

The main GUI is intentionally not published as a console script, because it depends on the
repository-root resources (decision PACK-015). Use `python main.py` in the editable checkout.

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