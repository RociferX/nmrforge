# 03 · API reference (v1.1.1)

Top-level exports are defined in `nmrforge_api/__init__.py`; `nmrforge_api.session.API_VERSION` defines API **1.1.1**. Software **1.0.5** and the API have separate version identifiers. The unified peak table has 38 columns.

## 3.1 Sessions and Datasets

```python
open_study(root, *, name="", backend=None, config=None, create=True) -> StudySession
add_dataset(session, source, *, condition="", exp_id="", title="",
            make_default=True, segmented=False) -> DatasetRef
dataset_info(session, dataset=None) -> dict
```

- Open if `root/project.json` exists, otherwise create a new NMRForge project; research status
  (`study/study.json`)Restore condition dataset list;
- `condition` automatically assigns the next unused letter (A/B/C...) by default; the label must be unique;
- Import links raw data, writes metadata, and registers an import run; it does not convert or process raw data.
- `segmented=False` (strict `bool`, the default) expects one complete Bruker raw directory. With
  `segmented=True`, `source` must be an ordered list of at least two complete raw directories to
  register as one condition. Paths must be unique; segments need not share a parent directory, and
  the supplied order is saved. The API does not auto-discover segments. Kinetic layouts and NUS
  data without a usable `nuslist` are rejected. Acquisition parameters, dimensions, nuclei, effective
  TD, spectral width, sampling mode, axis layout, SFO frequency and carrier must agree across segments.

`StudySession` Key attributes: `root`, `datasets`, `dataset` (main condition), `conditions`.
`dataset_by_condition(label)`, `study_dir`, `work_dir`, `reference_dir`,
`workflows_dir`, `records_dir`, `save_state()`, `save()`.

## 3.2 Reference workflow

```python
build_reference(session, dataset=None, *, params=None, direct_range=None,
                phase_route=None, carrier_ppm=None,
                progress=None, force=False) -> ReferenceSpectrum
load_reference(session, dataset=None) -> ReferenceSpectrum | None
load_references(session) -> dict[str, ReferenceSpectrum]        # key = "exp/data"
pick_reference_peaks(session, *, sigma_multiplier=None, out_path=None,
                     details=None, dataset=None) -> Path
set_reference_peaks(session, peak_table, reference=None, *,
                    source="external", params=None) -> ReferenceSpectrum
ensure_reference_peaks(session, reference=None, *, sigma_multiplier=None,
                       max_peaks=0, force=False) -> ReferenceSpectrum
build_reference_peak_tables(session, reference, *, window_pts=None,
                            window_ppm=None) -> ReferenceSpectrum
```

- `build_reference` runs the complete automatic chain (`generate_fid` → `generate_spectrum`,
  including unified phase optimisation), then freezes the spectrum and the actually executed
  script. `direct_phase` records the phase result; `ReferenceSpectrum.phase_record()` reports
  `phase_mode="auto"` with `actual_p0` and `actual_p1`;
- `ensure_reference_peaks`: each condition independently selects peaks on its own reference
  spectrum. An external peak table applies to the main condition only. Every condition has its own
  reference.list and reference peak table; conditions do not share peak identities.
- Reference reuse requires an exact normalized request match, including parameters, phase route,
  and direct range. The reference record stores the nmrforge_api.reference_input.v1 fingerprint.
  Any mismatch or missing/invalid legacy fingerprint raises ReferenceError and requires explicit
  force=True (CLI --force); no automatic rebuild occurs. All conditions are preflighted before
  engine processing starts.
- carrier_ppm is accepted by build_reference, run_reference_study, and run_parameter_study, or
  through params["carrier_ppm"] / dotted carrier_ppm.F1. Explicit keyword values override common
  params per axis; params_by_condition then overrides per axis. Logical axes are F2=x/F1=y in 2D
  and F3=x/F2=y/F1=z in 3D. Values must be finite numbers (zero/negative valid); bool, NaN/Inf,
  empty maps, unknown axes, and axes beyond the data dimensionality are rejected. Unspecified axes
  retain the conversion path's CAR; raw acqus is unchanged. Changed carriers require force=True.
  Sweeps inherit the reference carrier and reject carrier scans because the converted FID is reused.
- `sigma_multiplier` is the noise-σ detection threshold, default 35σ, set during reference
  construction. Once the reference is frozen, a different threshold raises `ReferenceError`;
  rebuild with `force=True` to change it. Applied values and their source are recorded in
  `peak_params.sigma_multiplier`, `previous_sigma_multiplier`, `detection.sigma_multiplier`
  and `detection.threshold_source`;
- Reference peak positions use three-point parabolic localisation. The reference API does not
  accept `localization_method` or `gaussian_roi_*` arguments. Unsupported measurement and sweep
  methods raise the public errors described below.

`ReferenceSpectrum` fields include `dataset_key`, `condition`, `ndim`, `sampling`, `frozen_spectrum`,
`script_path`, `script_sha256`, `spectrum_sha256`, `params`, `sweep_params`, `direct_phase`,
`peak_table_path`, `peak_count`, `peak_source` (`auto|external`), `peak_params`, `peak_tables`,
`peak_localization`, `stage_times_s`, `processing_audit` and `tool_versions`. `peak_tables` and
`peak_localization` contain the single `parabolic` method. Methods include
`direct_phase_override()`, `phase_record()` and the `peak_table_parabolic_path` property.

## 3.3 parameter combination and workflow plan

```python
expand_grid(axes) -> list[dict]
combos_from_rows(rows, *, axes=None) -> list[dict]
load_combo_table(path) -> list[dict]        # CSV/TSV/YAML/JSON
write_combo_table(path, combos) -> Path
design_diagnostics(combos, *, axes=None) -> dict
infer_axes(combos) -> dict / merge_overrides(base, overrides) -> dict
plan_sweep(reference, *, axes=None, combos=None, max_runs=256,
           base_overrides=None, notes=None) -> SweepPlan
```

- `axes` and `combos` must and can only be given one; `combos` is executed in table order as it is, and the interface **does not**
  Design decisions (Orthogonal table/partial factor/D-optimal/LHS are generated by external tools);
- Keys support dotted paths (`window.F1.off`, `baseline.F2.enabled`, `zero_fill.F1`
  `linewidth_hz.F1`, `points_per_line.F1`), the two dimensions can be specified separately; see for details.
  [05-inputs §5.9](05-inputs-and-data.md). phase axis `phase.<axis>.p0|p1`(absolute value)/.
  `phase_delta.<axis>.p0|p1`(deviation from reference);
- Lock key (`phases`/`direct_phase`/`phase_route`/`sampling.auto_phase`) error;
  Deterministic parameters (extraction window, point distance target, sampling schedule, timeout, `fid_noise*`) and unknown key writing.
  `plan.notes` Prompt but not block;
- `base_overrides` is a batch-level override. Each condition merges its effective reference
  parameters, then `base_overrides`, then the current combination;
- `SweepPlan.workflow_ids()` → `["W0001", ...]`.

## 3.4 Batch execution

```python
run_sweep(session, plan, *, reference=None, datasets=None,
          localization="parabolic", localize_peaks=None,
          edge_margin_ppm=None,
          sign="abs",            # compatibility argument; does not choose detection polarity
          resume=True,
          stop_on_error=False, progress=None, on_run=None) -> list[SweepRun]
```

- Each combination is processed once for every condition (default = all conditions in the session),
  and then each of the combination's own candidate spectra is peak-picked independently with the
  reference-locked threshold; records are returned by (workflow, condition);
- **Shared processing input with the reference**: the runtime facts of the reference are determined
  automatically (`params.diagnostics`, such as the direct-dimension DC correction `POLY -time`),
  folded into the combination base and executed **in the reference working directory** -- reusing
  the reference's converted fid and script from that directory; every run directory keeps the full
  `process.com` + SHA-256 (a missing one records a `processing_script_not_found` warning);
- `parameters_used` starts from the condition’s effective reference parameters, then applies batch
  `base_overrides` and the combination’s explicitly supplied keys. Threshold keys (`sigma_multiplier`,
  `min_snr`, `threshold_sigma`, `detection.sigma_multiplier`) raise `SweepError`; detection thresholds
  are locked by the reference;
- `localization` accepts only `parabolic`; `gaussian` and `both` raise `SweepError`. The
  combination-table `localization` key follows the same rule;
- `localize_peaks` (**targeted localization**): a CSV path (at least a
  `peak_id` column) / a sequence of peak numbers / `LocalizationTargets`; **only those
  peaks take the chosen method's refinement**. Detection, row count and `peak_id`
  numbering are unchanged and unlisted peaks retain their integer detection-grid
  coordinates (`localization_requested="parabolic"`, `localization_method="none"`, unrun QC
  columns as NaN, empty `failure_reason`, and `fallback=false`); only targeted peaks receive
  parabolic refinement, and an attempted failure is recorded in `failure_reason`; a combination
  may override it with the combination table's `localization.targets`; default = the
  whole spectrum;
- `localize_peaks` (**condition granularity**): the CSV may carry a
  `condition` column, and each condition then reads only its own rows (`peak_id` is
  validated against that condition's spectrum); without the column one list is shared
  by the whole batch (recorded as `by_condition: "all"`). A condition with no rows
  fails **before processing** by default; to let it through pass `on_missing="all"`
  (unlimited) or `"none"` (refines nothing). A condition mapping
  `{"A": "a.csv", "B": "b.csv"}` or `{"default": "x.csv", "by_condition":
  {"A": "a.csv"}}` is also accepted;
- `edge_margin_ppm` is an optional manual edge exclusion margin. By default, experiment/acquisition
  priors and spectrum evidence determine whether axial-edge screening applies; there is no
  unconditional edge band. The effective point and ppm values are recorded in `run.json.window`;
- Combination peak tables contain independently detected peaks. `reference_peak_id` and `assignment`
  are blank; `detected` is always true because the table contains only detected candidates.

`SweepRun` fields and methods are described in 06; `run.peak_table_path("parabolic")` returns the
sole peak table path.

## 3.5 Peak position measurement (lower level)

```python
measure_peak_positions(spectrum_path, peaks, *, window_pts=None,
                       window_ppm=None, axes=None, sign="abs",
                       refine="parabolic", nuclei=None, noise_sigma=None,
                       exclusive_windows=True) -> list[PeakMeasurement]
detect_and_localize(spectrum_path, *,
                    sigma_multiplier=None, edge_margin_ppm=None,
                    edge_margin_points=None, sign_mode="auto", axes=None,
                    targets=None, allow_empty_targets=False, experiment=None)
    -> (list[dict], dict)     # combination-mode independent peak picking: peak_id = index in this spectrum, reference_peak_id=""
read_reference_peaks(path) -> list[dict]      # fills in reference_peak_id
window_points_by_axis(axes, *, window_pts=None, window_ppm=None) -> dict
```

- `detect_and_localize` detects peaks using the threshold (also used as `min_snr`), applies
  experiment/acquisition-aware edge screening when evidence permits, selects sign handling from
  experiment and spectrum evidence (`auto` by default), then uses three-point parabolic
  localisation. There is no `max_peaks` limit. Explicit sign requests take precedence over auto;
  without an `Experiment`, an independent spectrum retains edge peaks.
- `refine` uses the three-point parabola. Requests for unsupported methods raise an explicit error.
- `PeakMeasurement.localization` records localisation status, equivalent linewidth metrics,
  `boundary_hit` and compatibility fields `fallback` / `fallback_reason`.
  `fwhm_by_nucleus`/`sigma_by_nucleus`;
- `PeakMeasurement`:`peak_id`, `reference_peak_id`, `assignment`, `reference`,
  `positions`, `deltas`, `intensity`, `noise_sigma`, `snr`, `found`,
  `window_edge`, `boundary`, `out_of_range`, `localization`.
- `exclusive_windows` is retained for API compatibility. Search ownership is joint in
  multidimensional space and is not represented by independent per-axis bounds or the CSV
  `cell_edge` field. Physical windows are recorded at
  `reference.peak_localization.search_windows.search_bounds_by_axis`; candidate ownership
  conflicts are audited separately.

## 3.6 Unify peak tables and records

```python
write_peak_table(path, rows) -> Path       # header = PEAK_TABLE_COLUMNS (includes peak_id)
read_peak_table(path) -> list[dict]        # NaN → float("nan")
peak_table_rows(measurements, *, workflow_id, condition="", dataset="",
                method="parabolic") -> list[dict]
reference_peak_id(peak_id) -> str          # 1 → "R0001"
write_records(session, *, reference=None, references=None, plan, runs,
              peaks=None) -> dict[str, str]
```

For fields and semantics, see 06; `write_records` produces `manifest.json`, `sweep_plan.json`,
`runs.json`, `workflows.json`, `measurement.json` and the unified `peak_table_parabolic.csv`.

## 3.9 Two modes: reference mode / combination mode

The interface splits "generate reference" and "run processing based on parameter combination" into **two modes**. The combination mode must be externally.
**Specify the reference explicitly**.

### Reference mode

```python
run_reference_study(root, dataset=None, *, datasets=None, segmented=False, name="", params=None,
                    params_by_condition=None,
                    phase_route=None, peaks=None, direct_range=None,
                    carrier_ppm=None,
                    sigma_multiplier=25,              # peak-picking threshold (settable in this mode only)
                    max_peaks=0,
                    force=False, backend=None, write=True, progress=None) -> ReferenceResult

run_parameter_study(root, dataset=None, *, datasets=None, segmented=False, combos=None, axes=None,
                    name="", params=None, params_by_condition=None,
                    phase_route=None, peaks=None, carrier_ppm=None,
                    direct_range=None, sigma_multiplier=None, max_peaks=0,
                    max_runs=256, force=False, resume=True,
                    backend=None, write=True, progress=None) -> StudyResult
```

`segmented` must be a strict `bool` and defaults to `False`, preserving single-directory input.
When enabled, a single condition accepts `dataset=[s1, s2]` or `datasets=[s1, s2]`; a top-level
`datasets` list always represents one condition. For multiple conditions, use a mapping such as
`datasets={"A": [a1, a2], "B": [b1, b2]}`. Do not pass both `dataset` and `datasets` for one condition.

- Import condition data (optional) -> an independent reference spectrum, script, and parabolic
  peak table for each condition; no parameter combinations are run.
- The reference stage imports, converts and merges the selected source segments. The ordered source
  list is part of input identity; changing a source or its order requires a new condition/research
  root. `force=True` rebuilds reference processing but does not change the bound source list. Reopening
  an existing segmented study for reference or combination runs needs no segmented flag when no new
  dataset is being imported.
- The peak selection threshold, reference peak table (external peak table), and localization are all determined at this stage, and then locked;
- Reference phase window/baseline **Automatic optimisation **On by default; `params["reference_optimize"]` can be turned off or
  Limited candidate (Test only/Recurrence/audit; real experiments are not available, and must be stated in the record after use);
- Product: `study/reference/<key>/`(script/spectrum/one parabolic peak table)+ `study/records/reference.json`;
- `ReferenceResult`:`session` / `references`(key → `ReferenceSpectrum`),
  `conditions`, `reference(condition="")`, `peak_tables`, `records`.

### Combination mode

```python
run_combination_study(reference,                  # <- required: give the reference explicitly
                      combos=[{"zero_fill": 1}],  # or axes=...
                      max_runs=256,
                      localization="parabolic",       # only supported method
                      localize_peaks=None,            # refine only the named peaks (CSV/ids)
                      edge_margin_ppm=None,             # optional manual edge margin
                      direct_range=(10.0, 6.5),        # overrides the workflow base value for this batch
                      resume=True, backend=None, write=True,
                      progress=None) -> StudyResult
```

How to write `reference` (string/Path, or `ReferenceHandle`):

| Writing | Meaning |
| --- | --- |
| `"~/studies/s1"` | Reference to the **main condition** of the study; combine all conditions to run the study |
| `"~/studies/s1#B"` | Reference for this study **Condition B**; only run condition B |
| `"~/studies/s1/study/reference/<key>/reference.json"` | Directly give the reference file (the research root is inferred from the path; only the conditions corresponding to the reference are run) |

- Combination execution requires an existing reference. Effective reference parameters form the
  base, then explicit combination keys override it. Detection thresholds are locked: threshold
  keys in the combination table raise `SweepError` and require reference rebuilding;
- **Independent peak selection for combinations**: Each combination independently detects its own complete peak table on its own candidate spectrum
  (`peak_id` = serial number of this spectrum, `reference_peak_id`/`assignment` left blank), and
  matching against the reference peak table is done downstream. On a per-combination basis, record
  `parameters_resolved.detection` (locked-threshold source `source="reference(locked)"`, manual
  margin, noise σ and the parabolic refinement method);
- `localization` accepts only `parabolic`, including in a combination-table override;
- Reference does not exist/Peak table missing -> `ReferenceError`, the error message indicates that the reference mode should be run first;
- Each running record indicates the reference: `run.json.base_script`(script/spectrum hash)
  `parameters_resolved.reference` (refer to peak table hash, etc.), `manifest.json` note.
  `mode="combination"` and `reference_spec`;
- One-step convenient entry `run_parameter_study(...)` is still available: internally run reference mode first, then use
  `str(root)` Explicitly call combined mode (backwards compatible).

The direct dimension range (ppm) can be given in both modes: `direct_range=(high, low)` (reverse the order and it
is swapped back automatically), `direct_range={"lo": ..., "hi": ...}` or explicit `ext_lo=`/`ext_hi=`. In reference
mode the range is part of the reference definition: it is not rebuilt when it agrees with the established reference
and rebuilt when it does not, and the source is recorded in `reference.json.direct_range.source`
(`explicit`/`params`/`default`). In combination mode it overwrites the workflow base value (the reference is not
rebuilt); an override that disagrees with the frozen reference range raises by default and needs an explicit
`allow_ext_override=True` (every run then carries the `direct_range_override` warning code), and each combination
may still override with `ext_lo`/`ext_hi`. Every workflow keeps `parameters_resolved.direct_range`. Illegal input
throws `SweepError`.

Auxiliary function: `parse_reference_spec(spec) -> ReferenceHandle`.
`parse_direct_range(value=None, *, ext_lo=None, ext_hi=None, params=None)`,
`resolve_reference(spec, backend=None) -> (session, DatasetRef, ReferenceSpectrum)`.


## 3.7 Error type

| Exception | When |
| --- | --- |
| `DatasetError` | The data directory is not recognized, the condition label is repeated, and there is no dataset in the study |
| `ReferenceError` | Reference spectrum/Product missing, the peak table does not exist |
| `MeasurementError` | Spectrum does not exist, parameter is illegal, or an unsupported localization method was requested |
| `SweepError` | combination table/grid illegal (locked key, exceeded upper limit, no design input), unsupported data type |

All four inherit `SensitivityError`.

## Behaviour compatibility manifest (compat)

**Why**: an unchanged interface name does not mean unchanged behaviour (the exclusive window
and the two repairs of the ratio denominator all left the API surface alone while changing the
numbers). Downstream has to be able to answer "which behaviour produced these numbers, and do
they have to be re-run?" from a machine-readable value.

```python
from nmrforge_api import compat_manifest, compat_status, check_conformance

manifest = compat_manifest()      # nmrforge_api.compat.v1 (JSON serialisable)
manifest["behavior_digest"]       # content fingerprint (the four code trees + shipped data)
manifest["token_digest"]          # code fingerprint, comments/docstrings stripped
manifest["compat_level"]          # same / additive / behavior_changed / contract_changed / unverified
manifest["affected"]              # the downstream steps a behaviour change touches
manifest["contracts"]             # peak-table columns + record schema + error and warning codes
manifest["golden"]                # expected golden-vector hashes
check_conformance()               # run the golden recipe and compare item by item (seconds)
```

```bash
python -m nmrforge_api compat                     # print the manifest (pure JSON)
python -m nmrforge_api compat --out compat.json   # write it out
python -m nmrforge_api compat --golden            # also run the golden vector
```

- **Two fingerprints**: `behavior_digest` hashes the **file content** (any changed line of code
  or shipped data moves it); `token_digest` normalises through the AST with comments and
  docstrings stripped, so the two language editions **agree** whenever there is no code
  difference;
- **Level semantics**: `same` = code tokens unchanged (comments/wording only); `additive` = new
  entry points or optional fields only, downstream **need not re-run**; `behavior_changed` =
  numbers change (must name `affected`); `contract_changed` = columns/fields/error codes changed
  (update the contract mirror); `unverified` = the working tree disagrees with the declaration
  (do not reuse the value);
- **Artefacts carry the stamp**: `run.json` / `records/reference.json` / `records/manifest.json`
  write `behavior_digest` / `token_digest` / `compat_level` / `compat_affected` /
  `compat_verified` next to `versions`, so any artefact can be traced back;
- **Maintaining the declaration**: it lives in `nmrforge_api/compat_declaration.py` (a pure data
  module, deliberately outside the fingerprint); the guard `tests/test_compat.py` requires the
  fingerprint to match the declaration, the level to be consistent with `affected`, `same` to
  keep the tokens unchanged and the golden vector to reproduce. Update it with
  `python scripts/update_compat_declaration.py --level <level> [--affected ...]`.
