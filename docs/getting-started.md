# Getting started

This guide covers a source installation, first data inspection, and the processing boundary. A
Linux AppImage may also be available from the [releases page](https://github.com/RociferX/nmrforge/releases);
each binary belongs to its stated release and source revision. Updating a checkout does not update
an already downloaded AppImage.

## Install from source

Linux is the target runtime. Create an editable installation:

```bash
git clone https://github.com/RociferX/nmrforge.git
cd nmrforge
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
python main.py
```

For the complete dependency and packaged-resource notes, see
[Installation](installation.md). NMRPipe is required for spectral conversion and processing;
SMILE is required for NUS reconstruction. nmrForge does not install or bundle these external tools.

## Inspect a Bruker dataset

Parsing and data understanding can be used without NMRPipe. In a source checkout, run:

```bash
python examples/quickstart.py /path/to/bruker/dataset
```

The inspection reports available acquisition files, dimensionality and axis metadata, experiment
classification, sampling classification and its evidence, time-domain layout, and whether external
processing engines can be located. To make a clearly synthetic example:

```bash
python examples/make_synthetic_dataset.py --out ./example_data/hsqc_2d
python examples/quickstart.py ./example_data/hsqc_2d
```

Synthetic data are for software checks and walkthroughs only, not scientific conclusions.

## Configure external engines

NMRPipe is resolved from the explicit `backend.nmrpipe.path` or
`backend.nmrpipe.nmrpipe_bin` configuration first, then `PATH`, the environment available
through `csh`, and supported common locations. SMILE is located with the NMRPipe installation.
Missing engines are reported when an operation needs them; the application does not silently
substitute a different processing engine.

Use the tracked defaults in `nmrforge_data/config/nmrforge.yaml`. For a machine-local path, copy
the file to the ignored `nmrforge_data/config/nmrforge.local.yaml` and edit the local copy:

```yaml
backend:
  nmrpipe:
    path: /path/to/nmrpipe/bin
```

Do not commit machine-specific paths. See [External dependencies](external-dependencies.md) for
resource discovery, memory notes, and what remains available without NMRPipe.

## Run a processing study

The Python API and command line use the same backend. A study consists of registered input
conditions, a frozen reference, and user-supplied parameter combinations:

```bash
python -m nmrforge_api init --study ./study --dataset /path/to/bruker/dataset --condition A
python -m nmrforge_api reference --study ./study
python -m nmrforge_api peaks --study ./study
python -m nmrforge_api sweep --study ./study --reference ./study --combos design.csv
python -m nmrforge_api report --study ./study
```

For a complete option list, see [CLI reference](cli.md). The API runs peak selection independently
on each candidate spectrum using the reference-locked threshold, then writes one
`peak_table_parabolic.csv` per study/run level as documented in
[Outputs and records](external-api/06-outputs-and-records.md). Localization is three-point
parabolic only.

## Where to go next

- [Installation](installation.md) — source and versioned Linux AppImage boundary.
- [External dependencies](external-dependencies.md) — NMRPipe/SMILE discovery and resource notes.
- [Processing model](processing-model.md) and [QC system](qc-system.md) — processing behaviour.
- [External API](external-api/README.md) — Python, CLI, records, and examples.
- [Troubleshooting](troubleshooting.md) and [FAQ](faq.md) — common issues.
