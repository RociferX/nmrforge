# 07 · Methods and QC criteria (v1.1)

> This page describes what the software **executed** and what QC records were left. Any
> cross-combination or cross-condition statistic
> (statistical inference and scientific conclusions) are outside the scope of this software and are
> computed downstream from the unified peak table.

## 7.1 Reference workflow

1. `generate_fid`(bruker `-AUTO`/fid.com conversion) -> `generate_spectrum`.
   (NMRPipe pipeline + unified phase route; NUS go SMILE reconstruction);
2. Freeze **reference spectrum** and **reference script **(`process.com`, with SHA-256) -- reference script is the condition.
   Template for all subsequent workflows;
3. Reference peak position: the software selects peaks on the reference spectrum (threshold
   `sigma_multiplier` is set when **generating the reference**, default 35σ; API `sigma_multiplier=` /
   CLI `peaks --sigma`). Automatic axial screening requires compatible experiment/acquisition
   priors and evidence from many narrow, aligned candidates at original spectrum edges; it does
   not use a blanket edge margin. An explicit margin is a manual override. Peaks receive stable
   identities in row order `R0001…`;
   **The threshold is frozen together with the reference**: All subsequent workflows can only use the reference threshold and give different thresholds.
   An error will be reported (if you want to change the threshold, you must rebuild the reference);
4. Apply the single supported localisation method, a **three-point parabola**, and write
   `reference_peak_table_parabolic.csv`;
5. The reference is only used as a benchmark for parameter perturbation and does not claim global optimality.

## 7.2 parameter perturbation (workflow)

- `parameters_used` for each combination = effective parameter (base) of the conditional reference run + combination table coverage;
- Phase is locked at the reference value by default (direct dimension skips phase search, indirect dimension follows reference optimisation phase);
  Manual deviation is represented by `phase_delta.<axis>.p0|p1` (relative reference) or `phase.<axis>.p0|p1` (absolute value).
  The actual value is written into `phase.<axis>.actual_p0/actual_p1`;
- Reference mode imports, converts and merges the source segments. Combination mode processes only
  the existing reference FID; it does not reconvert automatically. A missing/damaged FID, mismatched
  source or conversion evidence, or parameters requiring conversion/merge raises an error and requires
  rebuilding the reference. Single-file FIDs, 3D uniform slice directories and merged multi-segment
  FIDs are supported. Candidate spectra are written to `study/workflows/<id>/<condition>/` and do not
  replace the active spectrum; see [FID reuse boundary](09-limitations-and-roadmap.md#99-fid-reuse-boundary);
- Phase/window function/zero filling/baseline/NUS parameters are all executed according to the table, and all parameters that affect the results are dropped in three layers

## 7.2b Sampling route: coverage and schedule order

Sampling is classified from supported metadata, the valid data grid and schedule order. Only a
standard `nuslist` or a file explicitly named by `acqus.NUSLIST` is used. A full grid in standard
order may use uniform processing; full coverage in another order still requires schedule-based
placement. Explicit NUS data with a missing schedule or unrecoverable positions is rejected at
import. Trailing zero padding is not counted as missing samples. API parameter-combination support
for 3D NUS remains limited as described in [09](09-limitations-and-roadmap.md).

## 7.3 Peak localisation: three-point parabola

| Method | Practice | Scope of application |
| --- | --- | --- |
| `parabolic` | Refine the detected maximum independently along each requested axis using the local three points | Any dimension; only method |

- **Reference mode**: refine each peak in the reference peak table and write one parabolic table;
- **Combination mode** (2026-09-14): each combination first picks peaks independently on **its own
  candidate spectrum** with the reference-locked threshold, then refines them with the parabola.
  `reference_peak_id` stays blank in the peak table --
  matching peaks between combinations, and against the reference, is downstream work.

Localisation is a deterministic closed-form calculation, not an iterative fit. `boundary_hit`
marks a vertex offset at ±0.5 points. `fit_success` indicates whether a finite equivalent linewidth
could be calculated; it differs from the localisation record's `success` (which indicates that
localisation ran). `fallback` and `fallback_reason` remain compatibility fields and do not imply
an algorithm switch. Removed method requests such as `gaussian` or `both` raise an error.

Parabolic localisation needs only the local three-point neighbourhood on each axis. There is no
ROI size or iterative evaluation budget.

## 7.4 Peak selection threshold and margin (physical width caliber)

- Peak selection threshold = **Noise σ multiple** (`sigma_multiplier`, internally as `min_snr`):
  The reference mode is determined (default 35σ), the combination mode is **locked and inherited**, and is archived by workflow.
  `parameters_resolved.detection`(`source="reference(locked)"`);
- Default edge handling uses experiment/acquisition priors and evidence from many narrow, aligned
  peaks at the original spectrum edges. No unconditional edge band is excluded. An isolated edge
  peak, internal carrier peak, unknown experiment or uncertain crop boundary is retained. An
  explicit `edge_margin_ppm` or `edge_margin_points` is a manual override and is recorded apart
  from automatic screening;
- The conversion results are archived group by group: `run.json.window`(points/ppm/effective_ppm/
  Ppm_per_point/source) and `records/measurement.json`;
- The combination mode has no `max_peaks`; each workflow detects the peaks allowed by the
  reference-locked threshold. Reference measurements may use explicit search windows; these are
  not a blanket edge-exclusion rule. Automatic axial screening is separate and documented in
  [Peak picking](../peak-picking.md).
- Structural points (local maximum 3-point neighborhood, parabola +/-1 point) are not scaled -- they have nothing to do with resolution

Peak height, the selection threshold, S/N and reference measurements share the global median
background convention (`baseline_offset`, `height_reference="global_median_baseline"`). This is
not a spatial baseline correction and it does not change the spectrum. Automatic sign handling
follows the experiment template; phase-sensitive COSY/NOESY/ROESY retain both signs. For unknown or
low-confidence experiment types, strong evidence for both signs can be used as a fallback. An
explicit API request (`positive`, `negative`, `both` or `dominant`) takes precedence. The
low-level detector uses `sign_mode="auto"` by default.

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
| `intensity` / `SNR` | Signed peak height relative to the global median background and `|height|/σ` (σ = robust MAD noise) |
| `fit_success` / `FWHM_H` / `FWHM_N` / `boundary_hit` | Three-point parabolic localisation QC; the equivalent linewidth is a local-curvature estimate (`FWHM = 2.3548 sigma`, `sigma^2 = H/(2|a|)`) |
| `duplicate_localization` | The row shares its coordinates with another row of the same table (ppm to 1e-6): every row of a group is flagged true and no row is dropped; `peak_localization.parabolic.n_duplicate` counts extra rows and `run.json.warnings` gains `duplicate_localization` |
| `localization_requested` / `localization_method` | Requested method versus actual result: `parabolic` or `none` when not detected/skipped |
| `failure_reason` / `fallback_reason` | Independent localization failure reason versus actual fallback reason |
| `cell_low_*` / `cell_high_*` / `cell_edge` | Always NaN; joint multidimensional ownership cannot be represented by per-axis cell bounds |
| `search_bounds_by_axis` / `candidate_ownership_conflict` | Physical search intervals and ownership conflicts are separate audit fields |

`cell_edge` is always NaN in the current contract; physical search windows and ownership-conflict
audit are recorded separately. It is not a boolean boundary detector.

`window_edge` and `cell_edge` were previously described as distinct search boundaries in
a reference-measurement record. They do not by themselves establish that a peak is an artifact.

**Historical v1.0 wording (superseded; not current behavior):** the following discussion treated
`cell_edge` as an exclusive-cell marker. Current v1.1 always writes this field as NaN.
**How to read `cell_edge` (owner's wording, 2026-09-19 - historical)**: it fires very
often because an exclusive cell can be narrow in a crowded spectrum
only 1-2 points wide, so an extremum sitting on the cell bound is normal for crowded spectra. It is
therefore **not a criterion, only a necessary-condition filter**: the caller's own criteria are
`intensity_ratio_vs_picked` (for example >1.10) and `shift_vs_picked_*` (for example >1.5 points),
with `cell_edge` confirming that a suspicious row really was cut by a neighbour (no false
negatives were observed). Making the marker readable on its own would need a directional test (the
cell bound was hit **and** the in-cell extremum is clearly higher than the identity height) or a
continuous quantity (in-cell maximum / physical-window maximum), with the threshold left to the
the downstream reader.


Reference-measurement records may include additional location and boundary fields; the public
peak-table columns and meanings are listed in [outputs and records](06-outputs-and-records.md).

## 7.6 test/Detection aid (not included in the processing contract)

`nmrforge_api.uncertainty` (`position_uncertainty`, `uncertainty_summary`,
`PeakUncertainty`) is retained only as a test/detection aid. The processing pipeline does not call it
or write it to `records/`. It does not match peaks and its output is not a CSP detection floor or
significance threshold. Do not pass independent runs or unmatched peak tables to it. The utility is for:

- **Regression detection**: unchanged outputs can flag an ignored parameter for investigation,
  but zero dispersion alone does not prove a processing defect.
- **Regression/detection checks only**: it is not a downstream analysis method or scientific result.

```python
# Do not calculate a CSP floor from independent, unmatched peak-picking runs.
# Downstream analysis must match peaks using its own explicit criteria first.
```

Formal statistics and significance judgment should be completed according to your own assumptions in your analysis code.

## 7.7 Versions and Recalculability

Each record contains: software version (`core.__version__`), Python and key dependency versions, real machine registration.
NMRPipe/SMILE version, reference script and candidate script SHA-256, reference spectrum and candidate spectrum SHA-256.
Grid hash (`grid_sha256`), parameter three layers, window conversion record, complete log. With.
`records/manifest.json` + `workflows/<id>/` can be recalculated and checked.
