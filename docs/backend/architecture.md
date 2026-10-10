# Backend architecture

The backend boundary turns an `Experiment`, a `ProcessingPlan`, and explicit parameter overrides
into external processing commands and recorded products. `backend/base.py` defines the
`ProcessingBackend` protocol and capabilities. `backend/factory.py` currently creates
`NMRPipeBackend`; its public operations include health checks, Bruker-to-FID conversion, uniform
processing, and NUS reconstruction. Results carry success state, messages, logs, output paths, and
effective parameters for their caller to register or report.

## Responsibilities

- `nmrpipe_backend.py` manages work directories and coordinates input conversion, segmented
  acquisitions, uniform processing, NUS reconstruction, finalization, and result reporting. It
  chooses the appropriate route from the experiment model and sampling metadata.
- `script_generator.py` renders the deterministic `fid.com`, uniform-processing, and SMILE/NUS
  scripts from experiment metadata, processing plans, and parameter values.
- `bruker_workflow.py` handles `bruker -AUTO` conversion output and conversion-related metadata.
- `runtime.py` locates `csh`/`tcsh`, sources the user's `.cshrc` when present, and runs commands as
  registered subprocesses. It captures stdout/stderr, can forward output lines as progress, enforces
  timeouts, and supports stopping the active process tree.
- `config.py`, `nmrpipe_finder.py`, and `nmrpipe_version.py` resolve configuration, tool locations,
  and engine versions. `memory_guard.py` and `memory_disk.py` support the NUS memory check and
  temporary processing storage.

## Inputs, axes, and sampling boundary

`core.data.bruker_reader.read_dataset()` parses `acqus`, `acqu2s`, and `acqu3s` into an `Experiment`
with logical dimensions, acquisition parameters, experiment classification, and a `Sampling`
record. Logical dimensions are named F1/F2/F3; Bruker acquisition files describe physical
acquisition order and are not interchangeable with those logical names. The workflow passes the
experiment and its source directories to the backend, which uses the parsed acquisition settings
when producing conversion and processing scripts.

Sampling detection accepts the standard `nuslist` name or the file explicitly named by
`acqus.NUSLIST`. The schedule is part of the conversion/reconstruction input: its row count and
coordinate columns are checked against the experiment's dimensionality, and the backend stages the
resolved schedule for the conversion and SMILE commands. The `Sampling` model communicates the
detected mode, grid, and schedule to workflow and backend code; it does not itself run a transform
or reconstruct missing points. For segment inputs, workflow and backend code coordinate conversion
and combination while retaining the sample order and associated sampling metadata.

## Execution and outputs

The desktop's `ProcessingController` calls `workflow.stepwise` for FID and spectrum steps. The
workflow passes an experiment and plan into `NMRPipeBackend`, forwarding parameter overrides and a
progress callback. The backend generates scripts and executes them through `CshRuntime`; for NUS,
the reconstruction command uses SMILE, followed by the NMRPipe processing/finalization commands.
Stdout progress and backend log entries are relayed to the GUI or API caller. A successful result
includes the generated path and effective parameters; the workflow moves/registers the final
spectrum under the project data entry and persists run information.

Manual script execution uses the same runtime boundary. The GUI edits and submits script text via
`workflow.manual`; NMRPipe commands remain interpreted by the configured external installation.
The Python/CLI API reaches the same backend through its own reference and parameter-combination
workflows, without importing GUI or viewer state.

The engine boundary requires external NMRPipe tools for conversion and Fourier processing, and
SMILE for NUS reconstruction. See [external dependencies](../external-dependencies.md), the
[processing model](../processing-model.md), and the shared [architecture](../architecture.md).
