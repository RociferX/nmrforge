# Installation

## Which install do I want?

| You are... | Use this | Notes |
| --- | --- | --- |
| A user who wants to process spectra | **the Linux AppImage** | Bundles Python, Qt and all runtime resources. Nothing else to install except NMRPipe. |
| Developing nmrForge | editable source install (`pip install -e ".[dev]"`) | Needs Python 3.12+. |
| Scripting the parameter-sweep API on a cluster | editable source install from a clone | The API needs the repository layout, not an installed wheel. |

**A plain `pip install .` (wheel) is not a supported distribution path.** The runtime resources
(`config/`, `presets/`, `gui/assets/`) live at the repository root rather than inside a Python
package, so a non-editable install cannot locate them. See
[packaging.md](packaging.md) (decision PACK-015).

## Recommended: the AppImage

```bash
chmod +x NMRForge-<version>-x86_64.AppImage
./NMRForge-<version>-x86_64.AppImage
```

What the AppImage contains: the application, PyQt6, pyqtgraph, NumPy, SciPy, Matplotlib, nmrglue,
`config/`, `presets/` and the GUI assets.

What it does **not** contain: NMRPipe, SMILE, Java, or any dataset. nmrForge drives NMRPipe as an
external program, so install NMRPipe yourself and see
[external-dependencies.md](external-dependencies.md) if it is not detected.

Desktop integration happens automatically on first launch: the AppImage writes a desktop entry
and an icon into `~/.local/share`, pointing at the AppImage's real path, so moving or deleting the
AppImage behaves sensibly.

| Command | Effect |
| --- | --- |
| `./NMRForge-<version>-x86_64.AppImage` | Start the application |
| `./NMRForge-<version>-x86_64.AppImage --remove-desktop` | Remove the desktop entry and icon |
| `NMRFORGE_NO_DESKTOP=1 ./NMRForge-<version>-x86_64.AppImage` | Start without touching `~/.local/share` |
| `./NMRForge-<version>-x86_64.AppImage --appimage-extract-and-run` | Run systems/directories where FUSE mounting is unavailable |

Uninstalling is exactly deleting the AppImage file. Project data and studies live in your own
directories and are never touched.

### Building the AppImage

```bash
bash packaging/linux/build_appimage.sh
```

Requirements: Python 3.12+, `pip`, `appimagetool`, `mksquashfs`, and network access on first run
to fetch the AppImage runtime (a cached runtime under `~/.cache/nmrforge-appimage/` is reused, and
`RUNTIME_FILE` overrides it). The script reads the version from `core.__version__`, so the
artefact name and the version inside the application cannot drift. Build on an older glibc
distribution (Ubuntu 20.04/22.04) if you want the AppImage to run on more target machines.

Note: redistribution of a built AppImage is a licensing question as well as a technical one.
PyQt6 is GPL-3.0-only - see [LICENSE_OPTIONS.md](../LICENSE_OPTIONS.md) before publishing binaries.

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
afterwards; that is also the entry point the AppImage wraps.

The editable install exposes one console script:

| Command | What it does |
| --- | --- |
| `nmrforge-viewer` | Standalone spectrum viewer (`python -m viewer` works too) |

The main GUI is intentionally not published as a console script, because it depends on the
repository-root resources (decision PACK-015). Use the AppImage or `python main.py`.

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