# 10 - Troubleshooting (v1.0)

## 10.1 Common errors and what to do

| Symptom | Cause | What to do |
| --- | --- | --- |
| `DatasetError: not recognisable as a raw Bruker dataset` | an archive or an already-processed format was passed | unpack a directory holding `acqus` (and `acqu2s` for 2D) and `ser` |
| `DatasetError: condition label 'A' is taken by ...` | two datasets were bound to one label | use another label (B/C/...) or a second study root |
| `ReferenceError: reference artefacts are missing; rebuild with force=True` | `study/reference/<key>/` was moved or deleted | remove that directory, or call `build_reference(..., force=True)` |
| `ReferenceError: a non-primary condition needs the primary condition picked first` | peaks were picked for B while the primary condition A had none | call `ensure_reference_peaks` on A first |
| `MeasurementError: Gaussian peak fitting is currently supported only for 2D spectra.` | `measure_peak_positions(refine="gaussian")` was called on non-2D data | combination mode does not fail (it writes a fallback Gaussian table); a direct call should use `refine="parabolic"` |
| `SweepError: ... exceeds the max_runs limit` | too many combinations | shrink the grid, raise `max_runs` explicitly, or run in batches |
| `SweepError: 'phases'/'direct_phase' in the grid would break phase locking` | a phase dictionary was given directly | use `phase_delta.<axis>.p0|p1` or `phase.<axis>.p0|p1` |
| `SweepError: only 2D NUS parameter combinations are supported` | 3D NUS | build the reference only; combination execution is on the roadmap |
| `plan.notes` mentions "not in the list of parameters the backend reads" | a key name is misspelled | check the key table in 05; notes only warn and never fail a run |

## 10.2 The status is `success_with_warning`

Look at the `code` and `peaks` entries of `run.json.warnings` (and the `--- warnings ---`
block of `log.txt`):

| Code | What to do |
| --- | --- |
| `peak_count_zero` | this combination detected no peak at the locked threshold: check that its spectrum is sound, or rebuild the reference with a different threshold |
| `gaussian_fallback` | read `fallback_reason` (roi_too_small / not_converged / center_at_boundary / sigma_at_bound ...); widen the ROI or accept the parabolic fallback |
| `gaussian_boundary_hit` | the peak is too wide or too narrow, or the ROI does not fit; adjust the ROI radius |
| (removed) | `peak_not_detected` / `peak_window_edge` / `peak_out_of_range` / `window_points_fallback`: combination mode has picked independently since 2026-09-14 and no longer emits them |

## 10.3 Resuming and re-running

A successful run is reused only when the execution fingerprint matches. The fingerprint covers
the condition dataset, the parameter combination and its actual parameters, the locked phases,
the reference script/spectrum/peak-table hashes, and the **locked threshold, the refinement
method (`localization`) and the picking margin**, plus the Gaussian ROI options. A record
without a fingerprint, or any changed input, re-runs safely. After shortening a combination
table, old `Wxxxx` directories outside the active plan may be kept as history; they no longer
enter the current summary.

- workflow x condition pairs that are already `success`/`success_with_warning` are skipped;
- to re-run one combination, delete that condition directory under `study/workflows/<id>/` (or
  the whole `<id>/`) and run the same plan again;
- to rebuild the reference, call `build_reference(..., force=True)` or delete
  `study/reference/<key>/`;
- after changing the parameter table, plan again (`plan_sweep` gives a new `grid_sha256`); a
  re-run inside the same study root overwrites the matching workflow directories
  (`W0001...` numbered in table order).

## 10.4 Finding things

```bash
python -m nmrforge_api status --study <root>     # conditions / reference / workflow overview
cat <root>/study/workflows/W0001/workflow.json   # combination-level record
cat <root>/study/workflows/W0001/A/log.txt       # full log for that condition
cat <root>/study/records/manifest.json           # global manifest and boundary statement
```

When reporting a problem, include the study root path, `records/manifest.json`, the relevant
`run.json` and `log.txt`, and the output of `python -m nmrforge_api status`.
