# nmrForge documentation

Entry point for the documentation that ships with the repository.

## Start here

1. [README](../README.md) - what nmrForge is, how to install it, how to run it.
2. [Getting started](getting-started.md) - the AppImage path for users and a no-NMRPipe walkthrough
   for developers.
3. [Installation](installation.md) - source install and the deferred AppImage boundary
   (development).

## Using it

- [GUI guide](gui.md) - window layout, the four pipeline steps, a typical session.
- [CLI reference](cli.md) - `python -m nmrforge_api` subcommands and options.
- [Python API](python-api.md) - the public `nmrforge_api` surface versus the internal layers.
- [Processing model](processing-model.md) - understand, plan, execute, document.
- [QC system](qc-system.md) - FID, sampling and spectrum-level quality control.
- [Peak picking](peak-picking.md) - detection, sub-grid localisation, peak tables.
- [Batch processing](batch-processing.md) - batch runs (2D only) and how they differ from sweeps.
- [External dependencies](external-dependencies.md) - NMRPipe and SMILE: order, detection, missing.
- [Troubleshooting](troubleshooting.md) and [FAQ](faq.md).

## For downstream analysis

- [nmrforge_api guide](external-api/README.md) - the parameter-study API: quick start, API/CLI
  reference, inputs and outputs, methods and QC, handing results to your own analysis.

## Design and contracts

- [Architecture](architecture.md) - layers, responsibilities, dependency directions.
- [API contract](API_CONTRACT.md) - the shared contract between GUI, workflow, backend and the
  project model.
- [Development](development.md) - local development, testing, environment notes.
- [Packaging](packaging.md) - the AppImage and what it contains.
- [Roadmap](roadmap.md) - longer-term plans (not a statement about current behaviour).

## Release and licensing

- [THIRD_PARTY.md](../THIRD_PARTY.md) - third-party dependencies and their licences.
- [LICENSE_OPTIONS.md](../LICENSE_OPTIONS.md) - why the source is Apache-2.0 while the AppImage
  carries LGPL-3.0 obligations for the Qt/PySide6 libraries it bundles.
- [PUBLIC_RELEASE_AUDIT.md](../PUBLIC_RELEASE_AUDIT.md) and
  [RELEASE_CHECKLIST_v0.9.0.md](../RELEASE_CHECKLIST_v0.9.0.md) - the preparation record.
