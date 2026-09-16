# Getting started

This page takes you from nothing to a first useful result. It has two tracks:

- **Users** install the AppImage and work in the GUI.
- **Developers** install from source and can also run the scripted walkthrough, which needs no
  NMRPipe at all and shows what nmrForge understands about a dataset.

## Track A - users: the AppImage

1. Download `NMRForge-<version>-x86_64.AppImage` and make it executable:

   ```bash
   chmod +x NMRForge-<version>-x86_64.AppImage
   ./NMRForge-<version>-x86_64.AppImage
   ```

2. Install NMRPipe on the same machine (SMILE comes with it). nmrForge does not bundle it and
   never downloads it. See [external-dependencies.md](external-dependencies.md).

3. In the application: import your Bruker dataset directory, check the detected experiment type
   and sampling mode, run the automated processing path, read the quality report, then pick and
   export peaks. [gui.md](gui.md) walks through the interface.

4. If nmrForge cannot find NMRPipe, it says so explicitly instead of failing silently. Point it at
   your installation through `config/nmrforge.yaml`, or use the GUI setting if you prefer.

The AppImage is the supported way to install nmrForge for processing. It bundles Python, Qt and
the runtime resources, so there is nothing else to install besides NMRPipe.

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

`config/nmrforge.yaml` holds the defaults; copy it to `config/nmrforge.local.yaml` for
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

- [installation.md](installation.md) - AppImage details, extras, uninstall
- [external-dependencies.md](external-dependencies.md) - NMRPipe/SMILE
- [troubleshooting.md](troubleshooting.md) | [FAQ](faq.md)