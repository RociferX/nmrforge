# Architecture

NMRForge separates the desktop interface, processing workflows, external
engine integration, and reusable scientific data operations. The published
source tree keeps these components in one project.

    GUI and viewer
         |
         v
    workflow and nmrforge_api
         |
         v
    core data and processing operations
         |
         v
    backend integrations -> NMRPipe / SMILE

## Responsibilities

- gui/ and viewer/ provide the desktop application and spectrum viewing.

- nmrforge_api/ exposes the versioned Python and command-line interface. It
  records study inputs, outputs, and processing provenance.

- workflow/ coordinates processing routes and user-requested operations.

- core/ contains data handling, experiment interpretation, processing,
  optimization, quality checks, and project records.

- backend/ integrates processing with external engines and generates or runs
  their commands.

- nmrforge_data/ contains packaged configuration and preset resources.

The interface and processing code communicate through explicit data
structures and API contracts. GUI code owns presentation; engine-specific
command handling belongs at the backend boundary. NMRPipe and SMILE are
external dependencies and are not part of the Python package distribution.

## Data flow

Bruker input is read and interpreted by core data modules. A selected
workflow delegates conversion or processing to the backend, which produces
spectrum files and run records. The viewer reads spectrum data for display.
The public scripting API follows its documented reference-and-combination
workflow and writes study products separately from the active desktop project.

See the [API contract](API_CONTRACT.md) for the scripting boundary, the
[processing model](processing-model.md) for user-visible routes, and
[external dependencies](external-dependencies.md) for engine requirements.
