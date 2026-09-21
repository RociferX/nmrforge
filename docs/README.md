# nmrForge documentation

Entry point for the documentation that ships with the repository.

## Start here

1. [README](../README.md) - what nmrForge is, how to install it, how to run it.
2. [Getting started](getting-started.md) - the AppImage path for users and a no-NMRPipe walkthrough
   for developers.
3. [Installation](installation.md) - source install and the deferred AppImage boundary
   (development).

## Two tracks

nmrForge has two tracks with different maturity. Pick the one that matches your work.

### Track A - the desktop application (mature)

- [GUI guide](gui.md) - window layout, the four pipeline steps, a typical session.
- [QC system](qc-system.md) - FID, sampling and spectrum-level quality control.
- [Peak picking](peak-picking.md) - detection, sub-grid localisation, peak tables.
- [Batch processing](batch-processing.md) - batch runs (2D only) and how they differ from sweeps.
- [External dependencies](external-dependencies.md) - NMRPipe and SMILE: order, detection, missing.
- [Real-data evidence](evidence/real-data-comparison.md) - automatic vs manual (anonymised): the
  comparison figure, the inspection verdict, the match rate, the QC scores, the two parameter sets
  and a real-machine repeatability snapshot.

### Track B - the Python/CLI API (still changing)

Names, defaults and the records these calls write can change before v1.0. Pin a commit and
compare `nmrforge_api.compat_manifest()` (`behavior_digest`, `compat_level`, `affected`) before
treating a set of numbers as comparable.

- [Python API](python-api.md) - the public `nmrforge_api` surface versus the internal layers.
- [CLI reference](cli.md) - `python -m nmrforge_api` subcommands and options.
- [nmrforge_api guide](external-api/README.md) - the parameter-study API: quick start, API/CLI
  reference, inputs and outputs, methods and QC, handing results to downstream analysis.
- [Processing model](processing-model.md) - understand, plan, execute, document.

- [Troubleshooting](troubleshooting.md) and [FAQ](faq.md).

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
- Release notes, the preparation record and the binary release gate are kept in the
  maintainer's private repository; each version's user-facing summary lives on the
  GitHub releases page.
