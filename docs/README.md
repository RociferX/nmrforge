# nmrForge documentation

Entry point for the documentation that ships with the repository.

## Start here

1. [README](../README.md) - what nmrForge is, how to install it, how to run it.
2. [Getting started](getting-started.md) - source/AppImage startup options and a no-NMRPipe walkthrough.
3. [Installation](installation.md) - source installation and the version boundary for AppImages.

## Desktop application and Python/CLI API

The desktop application and `nmrforge_api` are supported interfaces in the same source tree. The
Python API is versioned separately (`API_VERSION = "1.0"`) and exposes a compatibility manifest
for behaviour and contract changes. A released AppImage corresponds to a specific source version;
source updates do not update that binary automatically.

### Desktop application

- [GUI guide](gui.md) - window layout, the four pipeline steps, a typical session.
- [QC system](qc-system.md) - FID, sampling and spectrum-level quality control.
- [Peak picking](peak-picking.md) - detection, sub-grid localisation, peak tables.
- [Batch processing](batch-processing.md) - batch runs (2D only) and how they differ from sweeps.
- [External dependencies](external-dependencies.md) - NMRPipe and SMILE: order, detection, missing.
- [Real-data evidence](evidence/real-data-comparison.md) - automatic vs manual (anonymised): the
  comparison figure, the inspection verdict, the match rate, the QC scores, the two parameter sets
  and a real-machine repeatability snapshot.

### Python/CLI API

- [Python API](python-api.md) - the public `nmrforge_api` surface versus the internal layers.
- [CLI reference](cli.md) - `python -m nmrforge_api` subcommands and options.
- [nmrforge_api guide](external-api/README.md) - the parameter-study API: quick start, API/CLI
  reference, inputs and outputs, methods and QC, handing results to downstream analysis.
- [Processing model](processing-model.md) - understand, plan, execute, document.

- [Troubleshooting](troubleshooting.md) and [FAQ](faq.md).

## Design and contracts

- [Architecture](architecture.md) - layers, responsibilities, dependency directions.
- [Development](development.md) - local development, testing, environment notes.
- [Packaging](packaging.md) - the AppImage and what it contains.
- [Roadmap](roadmap.md) - longer-term plans (not a statement about current behaviour).

## Release and licensing

- [THIRD_PARTY.md](../THIRD_PARTY.md) - third-party dependencies and their licences.
- [LICENSE](../LICENSE) and [THIRD_PARTY.md](../THIRD_PARTY.md) - source and third-party licence information.
- Version-specific changes are listed on the GitHub releases page.
