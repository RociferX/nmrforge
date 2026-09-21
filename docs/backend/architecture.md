# Backend architecture

## Directory

- Backend/:nmrpipe_backend.py(process/reconstruct_nus/finalize_nus/conversion)
  Script_generator.py(deterministic.com), bruker_workflow.py(fid.com patch).
  memory_guard.py(0.2.112), runtime.py(CshRuntime), factory.py,
  Base.py(ProcessingBackend Protocol,Shared), config.py(default parameter).
  native_backend.py, nmrpipe_finder.py;
- Workflow/:stepwise.py(three-step interface), phase_routes.py(unified_route)
  memory_phase_search.py, import_workflow.py, pick_peaks.py, analyze.py,
  batch.py, smile_optimize.py, param_optimize.py, baseline_optimize.py,
  window_optimize.py, direct_diagnostics.py, manual.py, ucsf_export.py,
  optimization_report.py;
- core/:data(bruker_reader/nus_reader/pipe_io/internal_data_model[Shared]),
  Experiment(classifier), experiments(registry + presets loading), processing.
  planning, optimization, qc, reporting, peaks, project[Shared],
  workspace.py[Shared].

## Critical flow

Read_dataset -> Experiment -> convert_to_fid(3D NUS:acqu3s TD Copy -> Slice.
Fid/test%03d.fid) -> reconstruct_nus(SMILE, direct dimension 1 x TD, memory guard) ->.
Finalize_nus(phase / baseline / zero filling) -> final spectrum;NMRPipe semantics only exist between backend/ and generated.
Script.

## Interface

ProcessingBackend returns stable keys (success/message/logs/spectrum_path/metrics/.
Effective_params);See docs/API_CONTRACT.md for details.
