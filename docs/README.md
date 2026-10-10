# nmrForge documentation

Software **1.0.5** · Python/CLI API **1.1.1**.
Start with the [project README](../README.md) for features, workflow, examples and evidence.
For Linux desktop use, download the [AppImage](https://github.com/RociferX/nmrforge/releases/tag/v1.0.5)
and follow the [installation steps](installation.md). NMRPipe and SMILE are installed separately.

## Installation and use

- [Getting started](getting-started.md), [Installation](installation.md) and [GUI guide](gui.md).
- [CLI reference](cli.md) and [Python API](python-api.md).
- [External dependencies](external-dependencies.md): engine discovery, configuration and execution.
- [Troubleshooting](troubleshooting.md) and [FAQ](faq.md).

## Processing and output behaviour

- [Processing model](processing-model.md): import, FID conversion, uniform/NUS processing
  and manual routes.
- [Quality control](qc-system.md): diagnostics, corrections, metrics and audit records.
- [Peak picking](peak-picking.md): detection, parabolic localisation and peak-table export.
- [Batch processing](batch-processing.md): data groups and 2D batch execution.
- [Capabilities and limitations](roadmap.md): supported operations and their boundaries.

## Architecture and interfaces

- [Architecture](architecture.md): components, dependencies and data flow.
- [GUI architecture](gui/architecture.md): controllers, workers, signals, log scope
  and spectrum loading.
- [Backend architecture](backend/architecture.md): conversion, script generation, execution
  and products.
- [Shared interfaces and API contract](API_CONTRACT.md): data/project models, backend protocol,
  spectrum axes, stepwise operations and the scripting contract.
- [Parameter-study API guide](external-api/README.md): signatures, CLI, parameter tables, records,
  localisation, integration and errors.

## Evidence

[Four-route comparisons](evidence/real-data-comparison.md) present spectra obtained with the current
processing workflow, scripts, parameters and candidate statistics. The cases include 2D uniform,
controlled artificial 2D NUS, 3D uniform and acquired 3D NUS.
[BMRB15750 script comparison](evidence/bmrb15750-script-comparison.md) gives the author and
automatic processing commands.

## Development and distribution

- [Development guide](development.md): environment, checks, compatibility and runtime resources.
- [Packaging](packaging.md): wheel resources, AppImage inputs, build layout and startup.
- [Contribution guide](../CONTRIBUTING.md) and [Security policy](../SECURITY.md).
- [Source licence](../LICENSE), [distribution notes](../LICENSE_OPTIONS.md),
  [third-party notices](../THIRD_PARTY.md).
- [Citation metadata](../CITATION.cff).
