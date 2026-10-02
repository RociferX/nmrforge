# Backend architecture

## Directory

- `backend/`: `nmrpipe_backend.py` (conversion, processing, NUS reconstruction and finalisation),
  `script_generator.py` (deterministic scripts), `bruker_workflow.py` (Bruker conversion
  handling), `memory_guard.py`, `runtime.py`, `factory.py`, `base.py` (the shared
  `ProcessingBackend` protocol), `config.py` and `nmrpipe_finder.py`.
- `workflow/`: `stepwise.py`, `phase_routes.py`, `memory_phase_search.py`, `import_workflow.py`,
  `pick_peaks.py`, `batch.py`, the optimisation modules, `direct_diagnostics.py`, `manual.py` and
  `ucsf_export.py`.
- `core/`: data readers and models, experiment classification, planning, processing, optimisation,
  QC, peak handling, project management and workspace management.

## Critical flow

`read_dataset` → `Experiment` → `convert_to_fid` → `reconstruct_nus` when the sampling route
requires SMILE → final phase, baseline and zero-fill processing → spectrum. NMRPipe command
semantics stay in `backend/` and the generated scripts.

Sampling uses a standard `nuslist` or a file explicitly named by `acqus.NUSLIST`. If an NUS
schedule is missing or sample positions cannot be recovered, import fails instead of inventing a
grid. A full-coverage schedule in standard order can use the uniform route; a full-coverage
schedule in another order still requires schedule-based placement.

Peak axes and reference coordinates follow the `FDDIMORDER` logical-axis mapping. Duplicate nuclei
remain distinguishable by their F-axis identity. Noise, thresholds, peak heights and S/N use a
global median background without changing the spectrum. Peak localisation uses only a three-point
parabola. Low-quality or ambiguous reference alignment preserves the candidates rather than
deleting them.

## Interface

`ProcessingBackend` returns `success`, `message`, `logs`, `spectrum_path`, `metrics` and
`effective_params`. See the public [Python API documentation](../python-api.md) for the supported
scripting boundary.
