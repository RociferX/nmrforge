# 07 · Methods and QC criteria (v1.0)

> This page describes what the software **executed** and what QC records were left. Any
> cross-combination or cross-condition statistic
> (statistical inference and scientific conclusions) are outside the scope of this software and are
> computed downstream from the unified peak table.

## 7.1 Reference workflow

1. `generate_fid`(bruker `-AUTO`/fid.com conversion) -> `generate_spectrum`.
   (NMRPipe pipeline + unified phase route; NUS go SMILE reconstruction);
2. Freeze **reference spectrum** and **reference script **(`process.com`, with SHA-256) -- reference script is the condition.
   Template for all subsequent workflows;
3. Reference peak position: The software automatically selects the peak on the reference spectrum (threshold `sigma_multiplier` when **generating the reference**.
   Can be specified externally, default 35σ;API `sigma_multiplier=` / CLI `peaks --sigma`).
   Axial peaks are culled by physical margins, or using an external peak table; peaks get stable identities in row order `R0001…`;
   **The threshold is frozen together with the reference**: All subsequent workflows can only use the reference threshold and give different thresholds.
   An error will be reported (if you want to change the threshold, you must rebuild the reference);
4. Use **parabolic** and **2D gaussian** to locate the same reference spectrum respectively, and write two.
   Reference peak table;
5. The reference is only used as a benchmark for parameter perturbation and does not claim global optimality.

## 7.2 parameter perturbation (workflow)

- `parameters_used` for each combination = effective parameter (base) of the conditional reference run + combination table coverage;
- Phase is locked at the reference value by default (direct dimension skips phase search, indirect dimension follows reference optimisation phase);
  Manual deviation is represented by `phase_delta.<axis>.p0|p1` (relative reference) or `phase.<axis>.p0|p1` (absolute value).
  The actual value is written into `phase.<axis>.actual_p0/actual_p1`;
- Within the same condition, fid is only converted once (reference run), candidate spectrum is written
  `study/workflows/<id>/<condition>/`, do not replace the activity spectrum;
- Phase/window function/zero filling/baseline/NUS parameters are all executed according to the table, and all parameters that affect the results are dropped in three layers

## 7.2b sampling route (full sampling -> uniform)

Valid sampling is determined during the data reading stage: mark NUS but `nuslist` covers the whole grid, or 2D `ser` is "full grid +.
"zero filling" and **no zero rows** -> judged as **actual full sampling**,`sampling="uniform"`.
`sampling_schedule="full_sampling"`, and record the evidence; handle `process()` as usual FT.
**Does not run SMILE**. True NUS (the sampling schedule only covers a subset, or the sparse file has no sampling schedule) still runs.
`reconstruct_nus()`;3D NUS Maintain the original state (only supports reference creation).

## 7.3 Two kinds of peak positioning

| Method | Practice | Scope of application |
| --- | --- | --- |
| `parabolic` (Reference method) | Take the \| intensity\| extreme value in the window near the reference peak position, and then do +/-1 point three-point parabolic sub-pixel refine for each participating axis | Arbitrary dimension |
| `gaussian` | Take the parabola's integer-grid result as the centre, then least-squares fit a 2D Gaussian **on the same candidate** (no rotation, axially separable, with a local constant baseline), returning centre/FWHM/amplitude/rmse | **2D only** |

The two run independently on the exact same candidate, and the results can be directly compared (the peak position difference is the algorithm difference).

- **Reference mode**: Do two positions for each peak in the reference peak table. The two reference peak tables use the same batch
  `reference_peak_id`;
- **Combination mode** (2026-09-14): each combination first picks peaks independently on **its own
  candidate spectrum** with the reference-locked threshold, then refines them with `localization`
  (parabolic default / gaussian / both). `reference_peak_id` stays blank in the peak table --
  matching peaks between combinations, and against the reference, is downstream work.

When Gaussian fails (ROI too small/Not convergent/hit the border/sick): fall back to the parabola position and return to the peak table.
`fallback`/`fallback_reason`/`fit_success` and `run.json.peak_localization`.
Reason for peak-by-peak recording, workflow status upgraded to `success_with_warning` -- **Silence is not allowed**.

### Fitting cost and result caliber (2026-09-14)

The cost of peak-by-peak 2D Gaussian = iteration number x ROI points; three constraints prevent the cost from linear expansion with zero filling:

| Mechanism | Default | Impact on results |
| --- | --- | --- |
| Analytical Jacobian | On | **None**: The same model, the same objective function and convergence criterion; measured peak difference <= 3e-05 ppm(15N)/3e-06 ppm(1H), method zero rollover, backoff count unchanged |
| The upper limit of the half-width of each axis of the fitting window (`peaks.localization.gaussian_roi_max_points`) | **0 = no limit (default)** | The default is no truncation at all -> the result is consistent with the old version; setting a positive value (such as 48) can limit the fine grid fitting scale in exchange for speed, **will change the result**: real machine 4 x maximum 0.39 ppm (3 peaks flipped), two-dimensional 2 x maximum 0.71 ppm(1 flip); trigger peak-by-peak retention `roi_capped`, if the fitting fails after truncation, it will automatically retry with the complete ROI (`fit_retry_uncapped`/`retry_n_iter`) |
| The upper limit of single-peak evaluation (`peaks.localization.gaussian_max_nfev`) | 200 | only affects the difficult-to-converge peaks that originally require more evaluations; 1 iteration in the numerical difference period ≈7 evaluations, 1 iteration = 1 evaluation under the analytical Jacobian, so 200 evaluations ≈ 200 iterations (more relaxed than the original ~57 iterations), just halving the "worst cost" |

Real machine comparison (248 peak tracking, same as reference peak table; default configuration = no upper limit):

| Grid | Old version | New version (default) | Speed up | Peak difference (15N/1H)/method flip |
| --- | --- | --- | --- | --- |
| F1 1× | 2.02 s | 1.46 s | 1.38× | 3e-05 / 3e-06 ppm,0 |
| F1 2× | 3.67 s | 2.24 s | 1.64× | 7e-06 / 3e-06 ppm,0 |
| F1 4× | 6.43 s | 3.89 s | 1.65× | 1e-06 / 0 ppm,**0** |
| F1 2× + F2 2× | 4.98 s | 3.13 s | 1.59× | 0 / 0 ppm,**0** |

If `gaussian_roi_max_points` is set to 48 (optional): 4 x 1.73 x, two dimensions 2 x 1.84 x, but the cost is.
Changes in peak positions listed above (keep files available for audit).

## 7.4 Peak selection threshold and margin (physical width caliber)

- Peak selection threshold = **Noise σ multiple** (`sigma_multiplier`, internally as `min_snr`):
  The reference mode is determined (default 35σ), the combination mode is **locked and inherited**, and is archived by workflow.
  `parameters_resolved.detection`(`source="reference(locked)"`);
- Edge axis peak exclusion margin default = **3 x nuclide line width of this axis (Hz) converted into ppm**
  (`core.peaks.axis_units`);`edge_margin_ppm` can explicitly give the physical width;
  At run time the point count is derived from the point spacing of the current candidate spectrum, e.g. after zero filling k times: only the point spacing changes, the margin's ppm coverage does not.
  The ppm width;
- The conversion results are archived group by group: `run.json.window`(points/ppm/effective_ppm/
  Ppm_per_point/source) and `records/measurement.json`;
- The combination mode **does not** `max_peaks`, nor does it have a "reference peak search window" (the reference peak table is not tracked);
  Reference mode/Lower floor `measure_peak_positions` remains `window_ppm` (default 1.5 x line width).
  With `window_pts` escape hatch;
- `window_ppm`/`window_pts` are an **upper bound** only: with the default
  `exclusive_windows=True` each reference peak's search region is truncated at the midpoints
  to its neighbours, so a record only takes the extremum inside its own cell. Otherwise a
  window wider than the spacing between neighbouring peaks relocates two reference records
  onto one grid point and the reference table gets duplicate rows differing only in
  `reference_peak_id` (fixed 2026-09-19; on a real 2D HSQC data set, condition A: 253 rows, 184 unique
  coordinates). `exclusive_windows=False` reproduces the previous wording;
- Gaussian ROI is also defined according to the physical width (ppm) (`peaks.localization.gaussian_roi_f1_ppm`
  / `_f2_ppm`,or function/CLI parameter), convert points according to point distance;
- Structural points (local maximum 3-point neighborhood, parabola +/-1 point) are not scaled -- they have nothing to do with resolution

### Baseline correction caliber (corrected on 2026-09-16)

- Time domain: direct dimension DC offset is handled by `POLY -time` (determined by automatic diagnosis, written to the reference base
  `direct_poly_time`, the combination is inherited);
- Frequency domain:`mode=order` render **`POLY -ord N -auto`**(NMRPipe `-auto` automatically select baseline point
  Then do N-order fitting). Historical defects: NMRPipe naked `POLY -ord N` default `-nc 0` and none.
  `-first/-last` -> No baseline node -> **Identity operation** (bit-by-bit verification on real machine), resulting in "turning off the axis spectrum remains unchanged";
- Therefore the reference baseline optimisation score (memory robust polynomial fit) and the final run script now have the same origin;
- `mode=auto` still renders `POLY -auto` (NMRPipe comes with the default order)

### Axis effects are reported according to conditions

The same parameter may be completely different under different conditions (different data, different reference configurations). The software changes one by one.
(workflow, condition) Compare the candidate spectrum with SHA-256 of **the reference spectrum** of the condition: bit by bit identical and emitted.
`no_spectrum_change` Warning -- Formal plans should not treat this axis as a real perturbation under this condition.

## 7.5 Peak by Peak QC(drop table field)

| Field | Meaning |
| --- | --- |
| `detected` | Whether the peak is detected on the spectrum (combination mode: only the detected peaks are in the table -> constant true; refer to the peak table
Unmeasured reference peaks in tracking mode retain rows and `detected=false`) |.
| `intensity` / `SNR` | The peak intensity at the extremum and `|intensity|/σ` (σ = the spectrum's robust MAD noise) |
| `fit_success` / `FWHM_H` / `FWHM_N` / `boundary_hit` | QC of the localization method this row **actually used** (Gaussian fit / three-point parabola); since P3-7 (2026-09-19) a parabolic table carries real values too, and the parabola reports an **equivalent linewidth** (`FWHM = 2.3548 sigma`, `sigma^2 = H/(2|a|)`) |
| `fit_rmse` | Gaussian fit residual RMS; **Gaussian only** - a parabolic table writes NaN (the three-point parabola is an exact solve) |
| `duplicate_localization` | The row shares its coordinates with another row of the same table (ppm to 1e-6, P2-5): every row of a group is flagged true and no row is dropped; `peak_localization.<method>.n_duplicate` counts the extra rows and `run.json.warnings` gains a `duplicate_localization` code |
| `fallback` / `fallback_reason` | Whether to roll back and why |
| `cell_low_*` / `cell_high_*` / `cell_edge` / `intensity_ratio_vs_picked` / `shift_vs_picked_*` | Reference-table per-peak **cell/identity QC** (P1-3): the final search interval (closed, data-axis grid points), whether the extremum was cut by the neighbour's cell, |measured intensity| / |the identity table's `Height`|, and measured - picked in ppm; **combination tables write NaN** (the sweep picks and localizes in one step, so there is no such step) |

`window_edge` and `cell_edge` are **orthogonal**: the first is true only when the extremum sits on a **physical window** bound (+-1.5x linewidth converted to points, clipped by the spectrum edge); the second is true only when the **exclusive cell** (the midpoint to the neighbouring reference peaks) truncated that peak's search interval and the extremum stopped exactly on that bound. With the historical wording `measure_peak_positions(exclusive_windows=False)` there is no neighbour truncation, so `cell_edge` is always false. Both true together means the true peak top may lie outside the window and may belong to the neighbour.

**How to read `cell_edge` (owner's wording, 2026-09-19 - measured, and it matters)**: it fires very
often (a real 2D HSQC data set, condition A: 233/253; synthetic A 418/431, B 423/431) because the exclusive cell is often
only 1-2 points wide, so an extremum sitting on the cell bound is normal for crowded spectra. It is
therefore **not a criterion, only a necessary-condition filter**: the caller's own criteria are
`intensity_ratio_vs_picked` (for example >1.10) and `shift_vs_picked_*` (for example >1.5 points),
with `cell_edge` confirming that a suspicious row really was cut by a neighbour (no false
negatives were observed). Making the marker readable on its own would need a directional test (the
cell bound was hit **and** the in-cell extremum is clearly higher than the identity height) or a
continuous quantity (in-cell maximum / physical-window maximum), with the threshold left to the
the downstream reader.


Internal `PeakMeasurement`(Reference peak tracking/Low level measurement path) with `window_edge`(extreme value sticker window.
Boundary), `boundary` (adhesion to the spectrum boundary), `out_of_range` (reference position outside the spectrum range) and `deltas`.
(relative to the reference peak position), summarized into `run.json.peak_localization` and.
`records/measurement.json`; Change the combination mode to use the peak detected by this spectrum -> Peak by Peak QC as.
`fit_success`/`fit_rmse`/`FWHM_*`/`boundary_hit`/`fallback`.

## 7.6 test/Detection aid (not included in the processing contract)

`nmrforge_api.uncertainty`(`position_uncertainty` / `uncertainty_summary` /
`PeakUncertainty`) Calculate σ, range and Δδ lower limit of the same batch of peaks among multiple combinations. It **does not participate**.
The processing chain will not appear in `records/`; its purpose is:

- **Regression detection**: σ/Δδ All 0 means that the scanned parameter is silently ignored (this defect has appeared in the history of real machines);
- **Algorithm comparison**: Peak difference between parabolic and gaussian under the same batch of candidates;
- **Downstream reference implementation**: The analysis side can be directly reused or implemented as such

```python
from nmrforge_api import position_uncertainty, uncertainty_summary

items = position_uncertainty(runs, csp_n_weight=0.2)
summary = uncertainty_summary(items, n_runs=len(runs))
```

Formal statistics and significance judgment should be completed according to your own assumptions in your analysis code.

## 7.7 Versions and Recalculability

Each record contains: software version (`core.__version__`), Python and key dependency versions, real machine registration.
NMRPipe/SMILE version, reference script and candidate script SHA-256, reference spectrum and candidate spectrum SHA-256.
Grid hash (`grid_sha256`), parameter three layers, window conversion record, complete log. With.
`records/manifest.json` + `workflows/<id>/` can be recalculated and checked.
