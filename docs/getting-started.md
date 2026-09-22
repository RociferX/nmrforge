# Getting started

This page takes you from v0.11.0 to a first useful result: either the Linux AppImage
(self-contained, interface language switched at run time) or a source installation. Both tracks are covered below.

## Track A - source installation and GUI

```bash
git clone https://github.com/RociferX/nmrforge.git
cd nmrforge
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[test]"
python main.py
```

Install NMRPipe on the same machine for real processing; SMILE comes with it. nmrForge does not
bundle or download either engine. If they are missing, data inspection still works and the program
reports the unavailable processing capability explicitly.

On first launch, open **`Help -> Usage tutorial`**: a full walkthrough from importing data to a peak table, and the text follows the interface language.

The future AppImage path and its additional PySide6/Qt distribution checks are reserved in
the release checklist kept in the maintainer's private repository.

## Track B - developers: inspect a dataset without NMRPipe

Everything in this step is pure Python: Bruker parameter parsing, dimensionality detection,
experiment classification, sampling classification and reading the time-domain data. It needs no
NMRPipe installation.

```bash
git clone <this repository>
cd nmrForge
pip install -e ".[test]"
```

Create a small synthetic dataset (headers plus a synthetic FID, clearly marked as not real data),
or point the walkthrough at one of your own Bruker dataset directories:

```bash
python examples/make_synthetic_dataset.py --out example_data/hsqc_2d
python examples/quickstart.py example_data/hsqc_2d
```

The walkthrough prints, in order:

1. which parameter files were found (`acqus`/`acqu2s`/`acqu3s`);
2. the dimensionality and, per dimension, nucleus, role, TD and sweep width;
3. the detected experiment type with the evidence behind it;
4. the sampling classification (uniform / NUS / uncertain) with evidence;
5. the time-domain storage layout the backend will use;
6. whether NMRPipe and SMILE were found, plus the exact next command to run.

If step 6 reports "NMRPipe NOT FOUND", processing is not available yet - data understanding and
QC still are. That boundary is deliberate: nmrForge refuses to pretend it can process without the
engine. A 3D example, if you want to see dimension handling and NUS classification:

```bash
python examples/make_synthetic_dataset.py --out example_data/hnca_3d --ndim 3 --nuclei 13C,15N,1H --nus
python examples/quickstart.py example_data/hnca_3d
```

## Configure NMRPipe

The backend searches for NMRPipe in this order (`backend/nmrpipe_finder.py`):

1. `backend.nmrpipe.path` from the configuration (a directory or an executable);
2. `backend.nmrpipe.nmrpipe_bin`;
3. `PATH`;
4. the `csh` environment, i.e. what a terminal has after sourcing the NMRPipe environment;
5. common installation locations.

`nmrforge_data/config/nmrforge.yaml` holds the defaults; copy it to
`nmrforge_data/config/nmrforge.local.yaml` for
machine-local overrides (that file is git-ignored so machine paths never reach the repository):

```yaml
backend:
  nmrpipe:
    path: /opt/NMRPipe/nmrbin.linux212_64
```

## Run the processing path

Developers can drive the same engine from the command line; the full reference is
[external-api/04-cli-reference.md](external-api/04-cli-reference.md):

```bash
python -m nmrforge_api init      --study ./study --dataset /path/to/bruker/dataset
python -m nmrforge_api reference --study ./study
python -m nmrforge_api peaks     --study ./study
python -m nmrforge_api sweep     --study ./study --grid grid.yaml --reference study/reference.json
```

A "study" is a self-contained root directory. The reference spectrum, its script and its peak
tables are frozen once; parameter combinations are then evaluated against that reference, and each
combination keeps its own script, candidate spectrum, peak table, run record and warnings.

## Where results live

- [external-api/06-outputs-and-records.md](external-api/06-outputs-and-records.md) - study
  directory layout: `manifest.json`, `runs.json`, `peak_positions.csv`, `uncertainty.csv`,
  per-run scripts and spectra.
- [qc-system.md](qc-system.md) - what the quality metrics mean and how to read the report.
- [processing-model.md](processing-model.md) - how a processing plan is built.

## Next steps

- [installation.md](installation.md) - the Linux AppImage and the source installation
- [external-dependencies.md](external-dependencies.md) - NMRPipe/SMILE
- [troubleshooting.md](troubleshooting.md) | [FAQ](faq.md)