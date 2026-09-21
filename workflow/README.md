# workflow/

Orchestration. This layer sequences the steps of a processing session and records what happened.
It may use `core/` and `backend/`, and must not import `gui/` or `viewer/`.

| Module | Role |
| --- | --- |
| `import_workflow.py` | import: read parameters, copy or link raw data, classify, register |
| `stepwise.py` | the stepwise pipeline (FID generation, spectrum generation) shared with the GUI |
| `phase_routes.py` | the unified phase route: complex preview, in-memory phase search, parameter optimisation, final run |
| `manual.py` | the manual path: view, edit and run `fid.com` and the processing scripts |
| `batch.py` | batch runs over a group (2D only) |
| `pick_peaks.py` | peak picking and localisation |
| `peak_align.py` | aligning peak tables between spectra |
| `direct_diagnostics.py` | FID diagnostics: DC offset, bad points, drift, broadband peaks |
| `smile_optimize.py` | the SMILE parameter scan |
| `baseline_optimize.py`, `window_optimize.py`, `param_optimize.py` | parameter-optimisation grids |
| `memory_phase_search.py` | in-memory phase search |
| `script_check.py` | script validation before a run |
| `optimization_report.py` | writing the quality and optimisation record |
| `ucsf_export.py` | exporting processed spectra |

One documented exception to the boundary rule: `stepwise.py` reads the experiment-type labels from
`gui/notes.py`, so that the GUI list and the stepwise path cannot drift apart.

End-to-end coverage lives in [`tests/test_full_paths.py`](../tests/test_full_paths.py).
