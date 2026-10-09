# 06 · Output and records (v1.1.1)

## 6.1 directory layout

```text
<root>/
  project.json                    NMRForge project (datasets and WorkflowRun registrations)
  study/
    study.json                    condition datasets + reference summary
    work/                         shared fid plus each run's scripts/candidate spectra
    reference/<exp>_<data>/
        reference.json            reference state (parameters / hashes / parabolic peak table / versions)
        process.com               the complete script the reference run actually executed
        reference.ft2             the frozen reference spectrum
        reference.list            reference peak **identity** table (Poky, with R0001...)
        reference_peak_table_parabolic.csv
    workflows/W0001/
        workflow.json             combination-level record (see 6.3)
        log.txt                   combination-level full log (the per-condition logs concatenated)
        <condition A|B>/
            process.com           the complete processing script this condition actually executed
            spectrum.ft2          candidate spectrum (does not replace the active spectrum)
            peak_table_parabolic.csv
            log.txt               this condition's full run log (not just the tail)
            run.json              this condition's complete provenance record
    records/
        reference.json            reference-mode products (reference spectrum / script / peak table / sampling / threshold)
        manifest.json             combination-mode products (data / reference / plan / peak identity / version)
        sweep_plan.json           workflow plan (with workflow_ids)
        runs.json                 flat record per (workflow, condition)
        workflows.json            summary record per workflow
        measurement.json          measurement conventions and localisation QC summary
        peak_table_parabolic.csv  long table of workflow × condition
```

## 6.2 Unified peak table fields (**38 columns**)

Multi-segment source provenance is recorded separately from the peak table. In `study.json` and the
`datasets` entries in reference/combination records, a segmented condition adds `segmented: true`
and `segments: [source_dir_1, source_dir_2, ...]` in input order; single-directory entries omit
these optional fields. Each condition's `run.json.dataset` also retains the source segment list.
`DataEntry.segments` and `metadata.json.segments` hold the segment directories actually used during
processing, while the original list is preserved as `metadata.json.source_segments`. Import-run input
records include each segment's source path and SHA-256. The full ordered list is bound to the
condition and included in combination resume fingerprints; comparing only the first segment is not
sufficient. This adds no peak-table columns and creates no cross-spectrum or cross-condition peak links.

`conversion_provenance.reference_fid` freezes fast fingerprints for the reference FID files and the
working `nuslist`. Combination `parameters_resolved.input` records
`policy="reference_fid_only"` and `reference_run_id`. These inputs are checked before the combination
starts and rechecked before each processing run. If they no longer match the frozen evidence, the
reference must be rebuilt; combination mode does not reconvert in place. Fingerprints use content
SHA-256 for files up to 8 MiB and `size + mtime_ns` for larger files; they are not content-level
authentication. Legacy references without this evidence require an explicit `force=True` rebuild;
calling reference mode again with its default cache policy is not enough. Resume fingerprints include
the frozen FID evidence and strict input policy, so older permissive runs are not silently reused.

The v1.1.1 unified table has currently **38 columns**, in the order below. Older 29-, 27-, and
36-column tables are historical formats, not a current compatibility promise; rebuild old reference
peak tables from the frozen reference spectrum before reuse.

The reference and combination modes use three-point parabolic localisation and write one peak table.
Rerunning without resume replaces the corresponding run products. The table schema is declared by
`nmrforge_api.peak_tables.PEAK_TABLE_COLUMNS`.

```text
workflow_id, condition, dataset,
peak_id, reference_peak_id, assignment,
H_ppm, N_ppm, intensity,
SNR, detected, localization_method, localization_requested,
fallback, fallback_reason, failure_reason,
fit_success, FWHM_H, FWHM_N,
boundary_hit, duplicate_localization,
F1_ppm, F1_nucleus, FWHM_F1,
F2_ppm, F2_nucleus, FWHM_F2,
F3_ppm, F3_nucleus, FWHM_F3,
cell_low_H, cell_high_H, cell_low_N, cell_high_N, cell_edge,
intensity_ratio_vs_picked, shift_vs_picked_H, shift_vs_picked_N
```

- `localization_requested` records requested method `parabolic`, adjacent to
  `localization_method`, which records the actual method. A localized peak is `parabolic`;
  not-detected reference identities and targeted skips are `none`. Skipped/not-detected QC is
  NaN and `failure_reason` is empty. An attempted failure (e.g. `no_local_peak_above_threshold`)
  has its own `failure_reason`; it is not silently treated as fallback.
- `fallback` / `fallback_reason` remain distinct audit fields for actual fallback behavior.
  Gaussian fitting and `fit_rmse` are removed; no compatibility promise is made for the old
  frozen API v0.2 table.
- `cell_low_H`, `cell_high_H`, `cell_low_N`, `cell_high_N`, and `cell_edge` are always
  NaN. Joint multidimensional Voronoi ownership cannot be represented as independent per-axis
  bounds. Physical search bounds are recorded in
  `reference.peak_localization.search_windows.search_bounds_by_axis` (F-axis plus closed low/high
  integer storage points); candidate ownership conflicts use the separate
  `candidate_ownership_conflict` audit flag.
- `intensity_ratio_vs_picked` (float): **|measured intensity| / |the identity
  table's Height|** (both sides in magnitude - a negative-peak `.list` carries a
  negative Height; fixed 2026-09-19); NaN when the Height is missing or zero. About 1
  means the record
  stopped on its own peak top, clearly above 1 means a shoulder of a stronger peak;
- `shift_vs_picked_H` / `shift_vs_picked_N` (float, ppm, sign = measured - picked, same
  axis and direction as `H_ppm`/`N_ppm`): the per-axis shift. The 15N ppm axis runs
  opposite to the data index, so do not read the sign backwards;
- The five per-axis cell fields (`cell_low_H`, `cell_high_H`, `cell_low_N`,
  `cell_high_N`, `cell_edge`) are always NaN because joint multidimensional Voronoi ownership
  cannot be represented by independent axis bounds. Physical search bounds are stored separately
  in `reference.peak_localization.search_windows.search_bounds_by_axis`; candidate ownership
  conflicts use `candidate_ownership_conflict`. Intensity ratio and shift deltas remain when measured.
- `duplicate_localization` (bool, P2-5, 2026-09-19): true when the row shares its
  coordinates with another row of the same table (ppm to 1e-6); every row of a
  duplicated group is flagged and no row is dropped or removed from the peak set.
  Both reference and combination tables carry the marker; it does not imply shared Voronoi cells.
- the frozen record `reference.json.peak_localization.parabolic` carries `n_cell_edge=null`
  (unknown, not zero), `n_duplicate` (= rows minus unique coordinates), and the
  `n`/`median`/`max` of `intensity_ratio_vs_picked`; every
  `run.json.peak_localization.<method>` carries `n_duplicate` too, and a table with
  shared coordinates adds a `duplicate_localization` entry (code plus row count) to
  `run.json.warnings`.

- `localization_requested` is `parabolic`; `localization_method` is the actual result:
  `parabolic` when localized, `none` when not detected or targeted localization was skipped.
  Skipped/not-detected QC is NaN and `failure_reason` is empty. Attempted failures have a separate
  `failure_reason` (such as `no_local_peak_above_threshold`), not an algorithm fallback.
- `fit_success` / `FWHM_*` / `boundary_hit` report three-point parabola QC. The equivalent
  linewidth estimates local curvature; `fallback` / `fallback_reason` remain distinct from failure.
- **Targeted localization**: `localization.targets`, the CLI `--localize-peaks`, and the API
  `localize_peaks=` select which detected peaks receive the three-point parabolic refinement.
  Detection, row count and `peak_id` numbering are unchanged; unlisted peaks retain their
  integer detection-grid coordinates (`localization_requested="parabolic"`,
  `localization_method="none"`, unrun localization QC as `NaN`, empty `failure_reason`, and
  `fallback=false`). A targeted refinement failure is recorded in `failure_reason`. The resolved targets are recorded
  in `run.json.parameters_resolved.detection.localization_targets`; the method summary is under
  `peak_localization.parabolic`;
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
- Per-peak localisation is recorded in `<peak table>.localization.json` and `run.json`.
  `boundary_hit` marks a parabolic vertex at the ±0.5-point limit; `fit_success` indicates whether
  finite equivalent linewidths could be calculated.
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
                         "window": {}, "run_json": "...", "versions": {},
                         "stage_times_s": {"processing": 0.0,
                                            "detection_localization": 0.0,
                                            "total": 0.0}}],
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
| `peak_localization` | Parabolic counts, failure reasons, targeting scope, duplicate count, physical search bounds and ownership-conflict audit |
| `window` | Peak selection margin: physical width, equivalent points, point distance, source |
| `stage_times_s` | Per-stage elapsed times; each `workflow.json` `condition_records[]` copies the timing from its run, and `total` matches `wall_time_s` |
| `script_diff` | Reference script vs this workflow script difference (`n_changed` + first 20 lines diff): used for auditing "only change the rows specified in the combination table" |
| `log_path` | Full log path |
| `versions` | nmrforge / python / dependencies / NMRPipe / SMILE (after real machine registration) |
| `behavior_digest` / `token_digest` | **Behaviour fingerprints** (2026-09-19): the first hashes the content of `core/`+`backend/`+`workflow/`+`nmrforge_api/` plus the shipped data, the second normalises through the AST with comments/docstrings stripped (comparable across editions) |
| `compat_level` / `compat_affected` / `compat_verified` | which behaviour produced this run: `same`/`additive`/`behavior_changed`/`contract_changed` (plus `unverified`); `compat_affected` names the downstream steps a behaviour change touches; `compat_verified` false means the working tree disagrees with the declaration (do not reuse) |
| `status` / `warnings` / `message` | Three-value status + warning code and count |

## 6.4 Status and warning code

| Status | Meaning |
| --- | --- |
| `success` | Processing and the parabolic peak table completed without warning |
| `success_with_warning` | Completed but needing attention (see below, the results are available but need to be reviewed) |
| `failed` | deal with/Measurement failed; reason for writing `message` and log, not silent |

| Warning code | trigger |
| --- | --- |
| `peak_count_zero` | This combination did not detect a single peak under the locking threshold (Check threshold/data) |
| `processing_script_not_found` | The complete processing script of this workflow was not found (`process.com` is missing in the running directory); the processing results and peak tables are still valid, but the traceability of the script is incomplete. You need to check the back-end placement location |
| `no_spectrum_change` | This combination does not change the spectrum under **this condition** (identical to the reference spectrum bit by bit): indicating that these parameters are ignored on the data (window type/gate mismatch, etc.) or have no effect; the formal plan should not regard this axis as a real disturbance |

> Starting from 2026-09-14, the combined mode selects peaks independently (does not track the reference peak table), so it no longer outputs
> `peak_not_detected` / `peak_window_edge` / `peak_out_of_range` /
> `window_points_fallback`. Requests for removed localisation methods raise an explicit error.

## 6.5 `records/` and borders

`manifest.json` (combination mode) summary: data conditions, condition-by-condition reference (script / spectrum / parabolic peak-table hashes).
Explicitly specified reference writing (`reference_spec`) and `mode="combination"`, plan and grid.
Hash, peak identity scheme (`peak_identity.matching`: matching of combined peaks to reference peaks **outside**).
Workflow state count, software/rely/External tool version, and **boundary declaration**.
(`manifest["boundary"]`: The software only performs processing and archiving; Statistical inference and scientific conclusions are yours.
Analysis program is completed).

The software **does not produce** any statistics or significance product: the old
`uncertainty.csv`/`uncertainty_summary.json` have been removed from `records/`. The σ/Δδ summary
code is kept as a **test/detection aid** (`nmrforge_api.uncertainty`; the processing chain does not
call it), and downstream analysis reads `records/peak_table_parabolic.csv` when needed, computing
the summary itself or reusing the helper. See [Methods and metrics](07-methods-and-metrics.md).

## 6.6 Reference input fingerprint

Each reference record stores an `input_fingerprint` with this shape:

```json
{"schema":"nmrforge_api.reference_input.v1","params":{...},"sha256":"..."}
```

The fingerprint binds the complete normalized processing request, `phase_route`, and normalized
direct-dimension range. Equivalent dotted and nested parameter forms normalize to the same input.
Reference reuse requires the complete fingerprint to match. Any mismatch, or a missing or invalid
legacy fingerprint, requires an explicit `force=True` / CLI `--force` rebuild; references are never
rebuilt automatically. Multi-condition requests preflight every condition before any backend
processing starts. Legacy 36-column reference peak tables no longer satisfy the current contract and
can be rebuilt from the frozen reference spectrum with `rebuild_reference_peak_tables()`; a
reference missing its input fingerprint must itself be force-rebuilt.

## 6.7 Reference processing audit, conversion provenance and timing

`reference.json` records `stage_times_s`, `processing_audit`, and `conversion_provenance`. The
conversion-provenance snapshot is frozen when the reference is created and takes precedence when
reading the record, so later changes to `fid.com` or a sidecar do not replace the evidence from that
run. `processing_audit` distinguishes FT-sign values that were `requested`, `resolved` according to
the acquisition rules, and the actual `ft_commands` in the processing script.

The `<data>.fid.conversion.json` sidecar records raw digital-filter parameters for each acquisition
block, the SHA-256 of the actual `fid.com`, and the `bruk2pipe` arguments and resolved values.
`records/reference.json` and the reference entries in a combination `manifest.json` carry the
corresponding sidecar snapshot. When evidence is insufficient, digital-filter correction is recorded
as `status="unknown"` and `method=null`; this does not mean correction was performed, and execution
or method is not inferred from metadata such as `GRPDLY`.

Explicit `params.sweep_width_hz` values are recorded with the original value, adopted value, source,
and `consistency_ratio`. Combination runs reuse the already converted FID and cannot change its
sweep width. Carrier audit is also retained with the frozen reference conversion record: requested,
resolved, and source values are tied to the conversion script actually used, its SHA-256, and the
matching provenance. Changing the carrier requires force-rebuilding the reference; the original
`acqus` is not written back. For an explicit override, inspect
`reference.conversion_provenance.sidecars[].record.carrier.explicit_carrier`: `axes.F1` and other
logical axes store `requested`, `resolved`, `source="explicit_ppm"`, and `conversion_key`. The same
record includes `script_name`, `script_sha256`, and `command_evidence` (the conversion script text).
`resolved` is ppm parsed from the final command, not a separately measured peak position or a
floating-point header readback.

`stage_times_s` contains per-stage elapsed times, not one total that includes all API overhead. Each
`workflow.json` `condition_records[]` also contains that condition's `stage_times_s`, copied from its
single-condition `run.json`; it is not an aggregate workflow duration.
