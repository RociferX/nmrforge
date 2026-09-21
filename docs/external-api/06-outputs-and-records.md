# 06 · Output and Record (v0.2)

## 6.1 directory layout

```text
<root>/
  project.json                    NMRForge project (datasets and WorkflowRun registrations)
  study/
    study.json                    condition datasets + reference summary
    work/                         shared fid plus each run's scripts/candidate spectra
    reference/<exp>_<data>/
        reference.json            reference state (parameters / hashes / two peak tables / versions)
        process.com               the complete script the reference run actually executed
        reference.ft2             the frozen reference spectrum
        reference.list            reference peak **identity** table (Poky, with R0001...)
        reference_peak_table_parabolic.csv
        reference_peak_table_gaussian.csv
    workflows/W0001/
        workflow.json             combination-level record (see 6.3)
        log.txt                   combination-level full log (the per-condition logs concatenated)
        <condition A|B>/
            process.com           the complete processing script this condition actually executed
            spectrum.ft2          candidate spectrum (does not replace the active spectrum)
            peak_table_<selected method>.csv  only the method localization actually selected appears
            log.txt               this condition's full run log (not just the tail)
            run.json              this condition's complete provenance record
    records/
        reference.json            reference-mode products (reference spectrum / script / two peak tables / sampling / threshold)
        manifest.json             combination-mode products (data / reference / plan / peak identity / version)
        sweep_plan.json           workflow plan (with workflow_ids)
        runs.json                 flat record per (workflow, condition)
        workflows.json            summary record per workflow
        measurement.json          measurement conventions and localisation QC summary
        peak_table_<selected method>.csv  long table of workflow × condition for each method actually used
```

## 6.2 Unified peak table fields (currently **29 columns**)

Combination mode only writes `localization` the actual selected method. It will be deleted when rerunning with `resume=False`.
Peak tables and positioning attachments for unselected methods in the previous round; `records/` will also delete old summaries of unselected methods. Old version.
`peak_positions.csv` Aliases are no longer generated, and the residue will be cleared when the upgrade is run.

```text
workflow_id, condition, dataset,
peak_id, reference_peak_id, assignment,
H_ppm, N_ppm, intensity,
SNR, detected, localization_method,
localization_requested, fallback, fallback_reason,
fit_success, FWHM_H, FWHM_N,
fit_rmse, boundary_hit, duplicate_localization,
cell_low_H, cell_high_H, cell_low_N,
cell_high_N, cell_edge, intensity_ratio_vs_picked,
shift_vs_picked_H, shift_vs_picked_N
```

The eight new columns (P1-3, 2026-09-19) describe the **reference** table only,
i.e. the result of relocating records from the peak identity table:

- `cell_low_*` / `cell_high_*` (int): the effective search interval (the 1.5x linewidth
  window intersected with the exclusive cell), a **closed** interval in data-axis
  integer indices; with `exclusive_windows=False` (the historical wording) they hold the
  window bounds that wording actually used;
- `cell_edge` (bool): the extremum sits on the **exclusive-cell** edge (the neighbour's
  cell cut it short). It is orthogonal to `window_edge`, which tracks the physical window
  edge; with the historical wording it is always `false`;
- `intensity_ratio_vs_picked` (float): **|measured intensity| / |the identity
  table's Height|** (both sides in magnitude - a negative-peak `.list` carries a
  negative Height; fixed 2026-09-19); NaN when the Height is missing or zero. About 1
  means the record
  stopped on its own peak top, clearly above 1 means a shoulder of a stronger peak;
- `shift_vs_picked_H` / `shift_vs_picked_N` (float, ppm, sign = measured - picked, same
  axis and direction as `H_ppm`/`N_ppm`): the per-axis shift. The 15N ppm axis runs
  opposite to the data index, so do not read the sign backwards;
- **combination (workflow) tables write `NaN` in all eight columns**: the sweep path
  picks and localizes in one step, so there is no identity-then-relocate step and
  writing 1.0/0 would be fabricated information (the same rule as the gaussian-only
  columns in parabolic tables);
- `duplicate_localization` (bool, P2-5, 2026-09-19): true when the row shares its
  coordinates with another row of the same table (ppm to 1e-6); every row of a
  duplicated group is flagged and no row is dropped or removed from the peak set.
  Both the reference and the combination tables carry the marker: after the
  exclusive-cell fix the reference table can only collide when two records round to
  the same grid point, while the combination table still collides when the peak
  picker's sub-grid refinement pulls two neighbouring detections into one cell;
- the frozen record `reference.json.peak_localization.<method>` also carries the
  summaries `n_cell_edge`, `n_duplicate` (= rows minus unique coordinates) and the
  `n`/`median`/`max` of `intensity_ratio_vs_picked`; every
  `run.json.peak_localization.<method>` carries `n_duplicate` too, and a table with
  shared coordinates adds a `duplicate_localization` entry (code plus row count) to
  `run.json.warnings`.

- **The table structures of the two algorithms are exactly the same (29 columns)**; only `fit_rmse` is still Gaussian-only and is written as `NaN` in a parabolic table
  (not false/0) - since P3-7 (2026-09-19) `fit_success` / `FWHM_*` / `boundary_hit` carry real values from the three-point
  parabola (the same linewidth wording as the Gaussian fit), so the two tables are directly comparable. The column
  sequence is the sequence of the code block below, `nmrforge_api.peak_tables.PEAK_TABLE_COLUMNS`.
- `localization_method` is the **actual** method used (`parabolic`/`gaussian`)
  `localization_requested` is the **request** method; if the two are different, it means that a rollback has occurred.
  The reason is `fallback`/`fallback_reason`;
- **Targeted localization (2026-09-19)**: the combination table's `localization.targets` / the CLI `--localize-peaks` / the API `localize_peaks=` let only the listed peaks take that method's refinement; detection, row count and `peak_id` numbering are **unchanged**, unlisted peaks **stay** with the detection-stage three-point parabola estimate, and that method's QC columns are `NaN` (not fitted, not a failure); a per-peak failure still writes `fallback`/`fallback_reason` (never re-fitting another candidate). Recorded in `run.json.parameters_resolved.detection.localization_targets` (`scope`/`source`/`path`/`sha256`/`n_targets`/`peak_ids` plus the `by_method` detail; `scope` is `mixed` under the per-method form) and in `peak_localization.<method>.localization_scope`/`n_targeted`/`n_skipped`;
- **Condition granularity (2026-09-20)**: when the target list is written per condition (a CSV `condition` column or a condition mapping), the same `localization_targets` record keeps `path`/`sha256` for the **whole source** (whole-file hash) while `peak_ids`/`n_targets`/`n_skipped` describe **this run (this condition)**, adds `condition` (this run's condition) and `on_missing` (the missing-row policy), and gives per-condition detail in `by_condition` `{peak_ids, n_targets, line_ranges, path + sha256, from}`; without a `condition` column (shared by the batch) `by_condition` is `"all"`, and `peak_localization.<method>` counts stay **per run**;
- `peak_id` is the peak number of **this spectrum** (the detection order of the spectrum of this combination);
- `reference_peak_id`(`R0001`…) belongs to the **reference peak table** only; since 2026-09-14 the
  combination mode picks peaks independently, so the combined peak table leaves
  `reference_peak_id`/`assignment` **blank** (`detected` is always true - the table holds only the
  peaks detected in this combination); matching the combined peaks back to reference peak
  identities is downstream work;
- `intensity` is the peak intensity (signed), `SNR = |intensity| / σ`, σ is the spectral noise
  (robust MAD of `core.qc.noise`),σ are written simultaneously.
  `run.json.parameters_resolved.spectrum_noise_sigma`;
- Peak-by-peak positioning record (`<peak table>.localization.json`, `run.json` of `measurements[]`)
  There are also fitting scale files: `roi_half_points`, `roi_half_points_uncapped`, `roi_capped`.
  `roi_capped_axes`, `max_nfev` (used to explain "the same peak takes time to change on a finer grid");
- Localization QC columns (the same wording for both algorithms): `fit_success` (whether the method this row actually
  used succeeded), `FWHM_H` / `FWHM_N` (FWHM in ppm, mapped by **nucleus name**; the parabola reports an equivalent
  linewidth), `boundary_hit` (Gaussian: centre/width hit the fitting bound; parabola: the vertex offset sits on the
  +/-0.5 point limit), `fit_rmse` (residual RMS, Gaussian only - a parabolic table writes `NaN`);
  `fallback` / `fallback_reason` record failures and fallbacks (never silently);
- `duplicate_localization` (bool, P2-5): the row shares its coordinates with another row of the same table (ppm to
  1e-6) - the reference table can only collide when two records round to the same grid point, while the combination
  table also collides when the peak picker's sub-grid refinement pulls two detections into one cell. Every row of a
  group is flagged `true` (no row is dropped) and `run.json.warnings` gains a `duplicate_localization` code;
- `condition`/`dataset` lets downstream analysis group the A/B tables by condition;
- Direct dimension range: `reference.json.params.ext_lo/ext_hi` (reference layer) and
  `reference.json.direct_range` = `{ext_lo, ext_hi, unit, source}` with `source` in
  `{explicit, params, default}` (P1-4, 2026-09-19; `default` means the caller gave no
  range and the backend/config default was used, with a `warning` attached); each
  `run.json.parameters_resolved.direct_range` (`ext_lo`/`ext_hi` + `source`:
  `reference_or_base` / `combo`). In combination mode a `--direct-range` that disagrees
  with the frozen reference range **raises by default** (exit code 2) and needs an
  explicit `--allow-ext-override`; with the switch every run carries the
  `direct_range_override` warning code;
- Sampling scope retained: `reference.json.sampling` (the effective mode),
  `sampling_schedule` (`nuslist` / `params` / `full_sampling`) and `sampling_evidence`; each
  `run.json.parameters_resolved.sampling` (`effective` / `schedule` / `route` / `evidence`)
  indicates whether the workflow takes `process()` or `reconstruct_nus()`;
- Peak selection threshold retention (refer to part of the definition, subsequent workflows can only use it):
  `reference.json.peak_params` of `sigma_multiplier` (the value selected when generating the reference).
  `previous_sigma_multiplier`(last version when force was rebuilt).
  `detection.sigma_multiplier` and `detection.threshold_source`.
  (`user` / `default(35sigma)`); each `run.json` is recorded separately.
  `parameters_resolved.peak_picking_threshold.locked_to_reference = true`;

## 6.3 `workflow.json` / `run.json`

`workflow.json`(combination level):

```json
{
  "workflow_id": "W0001",
  "status": "success_with_warning",
  "parameters_requested": {"zero_fill": 2},
  "conditions": ["A", "B"],
  "condition_records": [{"condition": "A", "status": "...",
                         "parameters_used": {}, "parameters_resolved": {},
                         "phase": {}, "warnings": [], "script_path": "...",
                         "script_sha256": "...", "spectrum_path": "...",
                         "spectrum_sha256": "...", "log_path": "...",
                         "peak_tables": {}, "peak_localization": {},
                         "window": {}, "run_json": "...", "versions": {}}],
  "warnings": [], "versions": {}, "base_script": {}, "grid_sha256": "..."
}
```

`run.json`(per workflow x conditions) Key fields:

| Field | Content |
| --- | --- |
| `workflow_id` / `index` / `condition` / `dataset` | Identity and Data Source |
| `parameters_requested` | A line given by user as it is (specification D2) |
| `parameters_used` | The complete parameter actually fed to the backend (reference base + coverage) |
| `parameters_resolved` | `phase`(phase_mode + actual_p0/p1), `detection`(Lock threshold source/margin/noise σ/Refining method), `peak_counts`, `smile`(actual nSigma/thresh), `spectrum_noise_sigma`, `direct_range`, `sampling`, `effective_params_backend` |
| `phase` | Axis by axis `phase_mode`(`auto_reference_locked` / `manual_delta_from_reference` / `manual_absolute`)+ `actual_p0/actual_p1` |
| `base_script` | Reference script path + SHA-256 (use reference script as evidence of template) |
| `script_path` / `script_sha256` / `spectrum_path` / `spectrum_sha256` | Products and Hashes |
| `peak_tables` | Peak table path of selected refinement mode + SHA-256 + number of rows + number of detected |
| `peak_localization` | Each method n_peaks/n_detected/n_missing/n_fallback/fallback_reasons/n_boundary_hit;`exclusive_windows` (wording: one cell per peak) |
| `window` | Peak selection margin: physical width, equivalent points, point distance, source |
| `script_diff` | Reference script vs this workflow script difference (`n_changed` + first 20 lines diff): used for auditing "only change the rows specified in the combination table" |
| `log_path` | Full log path |
| `versions` | nmrforge / python / dependencies / NMRPipe / SMILE (after real machine registration) |
| `behavior_digest` / `token_digest` | **Behaviour fingerprints** (2026-09-19): the first hashes the content of `core/`+`backend/`+`workflow/`+`nmrforge_api/` plus the shipped data, the second normalises through the AST with comments/docstrings stripped (comparable across editions) |
| `compat_level` / `compat_affected` / `compat_verified` | which behaviour produced this run: `same`/`additive`/`behavior_changed`/`contract_changed` (plus `unverified`); `compat_affected` names the downstream steps a behaviour change touches; `compat_verified` false means the working tree disagrees with the declaration (do not reuse) |
| `status` / `warnings` / `message` | Three-value status + warning code and count |

## 6.4 Status and warning code

| Status | Meaning |
| --- | --- |
| `success` | Processing + selected refinement method + corresponding peak tables are all completed without warning |
| `success_with_warning` | Completed but needing attention (see below, the results are available but need to be reviewed) |
| `failed` | deal with/Measurement failed; reason for writing `message` and log, not silent |

| Warning code | trigger |
| --- | --- |
| `peak_count_zero` | This combination did not detect a single peak under the locking threshold (Check threshold/data) |
| `processing_script_not_found` | The complete processing script of this workflow was not found (`process.com` is missing in the running directory); the processing results and peak tables are still valid, but the traceability of the script is incomplete. You need to check the back-end placement location |
| `no_spectrum_change` | This combination does not change the spectrum under **this condition** (identical to the reference spectrum bit by bit): indicating that these parameters are ignored on the data (window type/gate mismatch, etc.) or have no effect; the formal plan should not regard this axis as a real disturbance |
| `gaussian_fallback` | Gaussian fitting failed/retrace parabola(Peak-by-peak reason) |
| `gaussian_boundary_hit` | Gaussian centre/Width hits fitting boundary |

> Starting from 2026-09-14, the combined mode selects peaks independently (does not track the reference peak table), so it no longer outputs
> `peak_not_detected` / `peak_window_edge` / `peak_out_of_range` /
> `window_points_fallback`; non-2D Gaussian request directly reports an error (`MeasurementError`)
> The `gaussian_unsupported_ndim` table will no longer be generated

## 6.5 `records/` and borders

`manifest.json` (combination mode) summary: data conditions, condition-by-condition reference (script /Spectrum/Two peak table hashes).
Explicitly specified reference writing (`reference_spec`) and `mode="combination"`, plan and grid.
Hash, peak identity scheme (`peak_identity.matching`: matching of combined peaks to reference peaks **outside**).
Workflow state count, software/rely/External tool version, and **boundary declaration**.
(`manifest["boundary"]`: The software only performs processing and archiving; Statistical inference and scientific conclusions are yours.
Analysis program is completed).

The software **does not produce** any statistics or significance product: the old
`uncertainty.csv`/`uncertainty_summary.json` have been removed from `records/`. The σ/Δδ summary
code is kept as a **test/detection aid** (`nmrforge_api.uncertainty`; the processing chain does not
call it), and downstream analysis reads `records/peak_table_*.csv` when needed, computing the
summary itself or reusing the helper. See ../API_CONTRACT.md.
