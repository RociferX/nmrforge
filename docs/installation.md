# Installation

## Current release: source only

v0.9.0 does not include an AppImage or wheel. Use an editable install from the repository:

```bash
git clone https://github.com/RociferX/nmrforge.git
cd nmrforge
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[test]"
python main.py
```

A plain `pip install .` is not supported because `config/`, `presets/` and `gui/assets/` remain
repository-root resources. Real processing requires a separately installed NMRPipe; NUS
reconstruction also requires SMILE.

## Deferred AppImage

The build recipe remains available, but its presence does not mean a binary has been released.
An AppImage bundles PySide6/Qt and other libraries whose licences remain in force alongside the
Apache-2.0 licence for nmrForge's source. Before any binary is offered, the final artefact must pass
the licence, provenance and clean-machine checks in
[APPIMAGE_RELEASE_CHECKLIST.md](../APPIMAGE_RELEASE_CHECKLIST.md).

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