# 03 · API reference (v1.0)

See `nmrforge_api/__init__.py`(`API_VERSION = "1.0"`) for top-level exports; since 2026-09-22 `nmrforge_api` is released as its **first version**, with the contract version defined once in `nmrforge_api.session`.

## 3.1 Sessions and Datasets

```python
open_study(root, *, name="", backend=None, config=None, create=True) -> StudySession
add_dataset(session, source, *, condition="", exp_id="", title="",
            make_default=True) -> DatasetRef
dataset_info(session, dataset=None) -> dict
```

- Open if `root/project.json` exists, otherwise create a new NMRForge project; research status
  (`study/study.json`)Restore condition dataset list;
- `condition` automatically assigns the next unused letter (A/B/C...) by default; the label must be unique;
- Import = link raw + write metadata + register import run, **do not do**Convert/deal with

`StudySession` Key attributes: `root`, `datasets`, `dataset` (main condition), `conditions`.
`dataset_by_condition(label)`, `study_dir`, `work_dir`, `reference_dir`,
`workflows_dir`, `records_dir`, `save_state()`, `save()`.

## 3.2 Reference workflow

```python
build_reference(session, dataset=None, *, params=None, phase_route=None,
                progress=None, force=False) -> ReferenceSpectrum
load_reference(session, dataset=None) -> ReferenceSpectrum | None
load_references(session) -> dict[str, ReferenceSpectrum]        # key = "exp/data"
pick_reference_peaks(session, *, sigma_multiplier=None, out_path=None,
                     details=None, localization_method="parabolic",
                     gaussian_roi_f1_ppm=None, gaussian_roi_f2_ppm=None,
                     dataset=None) -> Path
set_reference_peaks(session, peak_table, reference=None, *,
                    source="external", params=None) -> ReferenceSpectrum
ensure_reference_peaks(session, reference=None, *, sigma_multiplier=None,
                       max_peaks=0, force=False,
                       localization_method="parabolic",
                       gaussian_roi_f1_ppm=None,
                       gaussian_roi_f2_ppm=None) -> ReferenceSpectrum
build_reference_peak_tables(session, reference, *, window_pts=None,
                            window_ppm=None, roi_f1_ppm=None,
                            roi_f2_ppm=None) -> ReferenceSpectrum
```

- `build_reference` Go through the complete automatic chain (`generate_fid` -> `generate_spectrum`, including unity
  Phase optimisation), frozen spectrum and **actually executed script**; the actual results of automatic phase identification are written.
  `direct_phase`(`ReferenceSpectrum.phase_record()` gives `phase_mode="auto"` +.
  `actual_p0/actual_p1`);
- `ensure_reference_peaks`: Main condition automatically selects peaks (or external peak table) to establish peak identity
  `reference.list`; the same identity table is copied to the other conditions; **two reference peak
  tables are always written afterwards** (`reference_peak_table_parabolic.csv` / `_gaussian.csv`);
- `sigma_multiplier` (peak selection threshold, σ multiple) **can be specified externally when generating the reference**: default 35σ;
  Once the reference peak table is frozen, all subsequent workflows can only use the reference threshold -- and then throw different thresholds.
  `ReferenceError` (Change the threshold value to the reconstruction reference: `force=True` or delete the condition.
  `study/reference/<key>/`);The actual usage is written into `peak_params.sigma_multiplier` /.
  `peak_params.previous_sigma_multiplier` /
  `peak_params.detection.sigma_multiplier` /
  `peak_params.detection.threshold_source`;
- `localization_method` only determines the reference peak position selection method (default parabola); Gaussian on non-2D data
  Do not skip silently: Gaussian table write `fallback=true` +.
  `fallback_reason="gaussian_unsupported_ndim"`.

`ReferenceSpectrum` Key fields: `dataset_key`, `condition`, `ndim`, `sampling`.
`frozen_spectrum`, `script_path`, `script_sha256`, `spectrum_sha256`, `params`,
`sweep_params`, `direct_phase`, `peak_table_path`(identity table), `peak_count`.
`peak_source`(`auto|external|shared:<condition>`), `peak_params`, `peak_tables`.
(Paths to the two tables/Hash/Number of lines/detected number), `peak_localization`(position QC).
`tool_versions`;Method:`direct_phase_override()`, `phase_record()`.
`peak_table_parabolic_path`, `peak_table_gaussian_path`.

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
- `base_overrides` is a batch-level coverage; when executing each condition, press "The reference of the condition is valid parameter ->
  `base_overrides` -> Current combination "merged. The old absolute `base_params` entry has been deleted;
- `SweepPlan.workflow_ids()` → `["W0001", ...]`.

## 3.4 Batch execution

```python
run_sweep(session, plan, *, reference=None, datasets=None,
          localization="parabolic", localize_peaks=None,
          edge_margin_ppm=None,
          sign="abs",            # legacy parameter (the detection sign convention is fixed to dominant)
          roi_f1_ppm=None, roi_f2_ppm=None, resume=True,
          stop_on_error=False, progress=None, on_run=None) -> list[SweepRun]
```

- Each combination is processed once for every condition (default = all conditions in the session),
  and then each of the combination's own candidate spectra is peak-picked independently with the
  reference-locked threshold; records are returned by (workflow, condition);
- **Shared processing input with the reference**: the runtime facts of the reference are determined
  automatically (`params.diagnostics`, such as the direct-dimension DC correction `POLY -time`),
  folded into the combination base and executed **in the reference working directory** -- reusing
  the reference's converted fid and script from that directory; every run directory keeps the full
  `process.com` + SHA-256 (a missing one raises `processing_script_not_found`);
- `parameters_used` Base = The condition refers to the effective parameters of the run (phase lock), and subsequently applies the batch
  `base_overrides`, the combination table finally covers only the keys it explicitly specifies; threshold class keys (`sigma_multiplier`/`min_snr`/`threshold_sigma`/.
  `detection.sigma_multiplier`) is written into the combination table -> `SweepError` (the threshold is locked at the reference);
- `localization` = `parabolic`(default)/ `gaussian`(2D only)/ `both`: Output only the selected
  Peak table; per combination can be overridden with the `localization` key of the combination table;
- `localize_peaks` (**targeted localization**, 2026-09-19): a CSV path (at least a
  `peak_id` column) / a sequence of peak numbers / `LocalizationTargets`; **only those
  peaks take the chosen method's refinement**. Detection, row count and `peak_id`
  numbering are unchanged and unlisted peaks stay (position from the detection-stage
  parabola, that method's QC columns as NaN = not done, not a failure); a combination
  may override it with the combination table's `localization.targets`; default = the
  whole spectrum;
- `localize_peaks` (**condition granularity**, 2026-09-20): the CSV may carry a
  `condition` column, and each condition then reads only its own rows (`peak_id` is
  validated against that condition's spectrum); without the column one list is shared
  by the whole batch (recorded as `by_condition: "all"`). A condition with no rows
  fails **before processing** by default; to let it through pass `on_missing="all"`
  (unlimited) or `"none"` (refines nothing). A condition mapping
  `{"A": "a.csv", "B": "b.csv"}` or `{"default": "x.csv", "by_condition":
  {"A": "a.csv"}}` is also accepted;
- `edge_margin_ppm` = exclude the physical width of the edge axis peak when selecting peaks (default 3 x nuclide line width of this axis)
  Convert the number of points according to the point distance one by one and write it into `run.json.window`;
- Combined peak table **Do not track reference peak table**: `reference_peak_id`/`assignment` Leave blank, `detected`
  Always true (there are only peaks detected by this combination in the table).

`SweepRun` For key fields and methods, see 06;`run.peak_table_path("parabolic"|"gaussian")`.
Gives the peak table path for the selected method (an empty string is returned for unselected methods).

## 3.5 Peak position measurement (lower level)

```python
measure_peak_positions(spectrum_path, peaks, *, window_pts=None,
                       window_ppm=None, axes=None, sign="abs",
                       refine="parabolic", nuclei=None, roi_f1_ppm=None,
                       roi_f2_ppm=None, noise_sigma=None,
                       exclusive_windows=True) -> list[PeakMeasurement]
detect_and_localize(spectrum_path, *, method="parabolic",
                    sigma_multiplier=None, edge_margin_ppm=None,
                    edge_margin_points=None, roi_f1_ppm=None,
                    roi_f2_ppm=None, sign_mode="dominant", axes=None)
    -> (list[dict], dict)     # combination-mode independent peak picking: peak_id = index in this spectrum, reference_peak_id=""
read_reference_peaks(path) -> list[dict]      # fills in reference_peak_id
window_points_by_axis(axes, *, window_pts=None, window_ppm=None) -> dict
```

- `detect_and_localize` is the peak selection entrance of the combination mode: physical margin + `sigma_multiplier`
  (Also do `min_snr`) + dominant symbol caliber, then press `method` to refine; without `max_peaks`.
  (Write as many peaks as are detected under the locking threshold); Non-2D request `gaussian` throw `MeasurementError`;
- `refine`:`parabolic`(default)|`none`|`gaussian`(**2D only**, not 2D throw
  `MeasurementError("Gaussian peak fitting is currently supported only for
  2D spectra.")`, no downgrade);
- Gaussian failure peak-by-peak fallback parabola, `PeakMeasurement.localization` record
  `requested_method`/`actual_method`/`fit_success`/`fallback`/
  `fallback_reason`/`fit_rmse`/`boundary_hit` and press **Verification name**.
  `fwhm_by_nucleus`/`sigma_by_nucleus`;
- `PeakMeasurement`:`peak_id`, `reference_peak_id`, `assignment`, `reference`,
  `positions`, `deltas`, `intensity`, `noise_sigma`, `snr`, `found`,
  `window_edge`, `boundary`, `out_of_range`, `localization`.
- `exclusive_windows` (default `True`): the window half-width is only an **upper bound**; each
  reference peak's search region is truncated at the midpoints to its neighbours, so a record
  only takes the extremum inside its own cell and two reference records are never relocated
  onto the same grid point (fixed 2026-09-19; `False` is the previous wording). Records that
  round to the same grid point still share a cell - the resolution limit of that spectrum.

## 3.6 Unify peak tables and records

```python
write_peak_table(path, rows) -> Path       # header = PEAK_TABLE_COLUMNS (includes peak_id)
read_peak_table(path) -> list[dict]        # NaN → float("nan")
peak_table_rows(measurements, *, workflow_id, condition="", dataset="",
                method="parabolic") -> list[dict]
gaussian_fallback_rows(measurements, *, workflow_id, condition="", dataset="",
                       reason) -> list[dict]
reference_peak_id(peak_id) -> str          # 1 → "R0001"
write_records(session, *, reference=None, references=None, plan, runs,
              peaks=None) -> dict[str, str]
```

For fields and semantics, see 06;`write_records` produces `manifest.json`, `sweep_plan.json`.
`runs.json`, `workflows.json`, `measurement.json`, two long tables.
`peak_table_{parabolic,gaussian}.csv`.

## 3.9 Two modes: reference mode / combination mode (2026-09-14)

The interface splits "generate reference" and "run processing based on parameter combination" into **two modes**. The combination mode must be externally.
**Specify the reference explicitly**.

### Reference mode

```python
run_reference_study(root, datasets={"A": "~/data/a"},
                    params=None,                  # may carry reference_optimize(**testing only**, see 05 §5.10)
                    phase_route=None, peaks=None,
                    direct_range=(10.5, 6.5),         # direct-dimension range (high, low; ppm)
                    sigma_multiplier=25,              # peak-picking threshold (settable in this mode only)
                    max_peaks=0, localization_method="parabolic",
                    gaussian_roi_f1_ppm=None, gaussian_roi_f2_ppm=None,
                    backend=None, write=True, progress=None) -> ReferenceResult
```

- Import condition data (optional) -> automatic optimisation reference spectrum and reference script -> two reference peak tables; do not make any parameters
  Combination;
- The peak selection threshold, reference peak table (external peak table), and localization are all determined at this stage, and then locked;
- Reference phase window/baseline **Automatic optimisation **On by default; `params["reference_optimize"]` can be turned off or
  Limited candidate (Test only/Recurrence/audit; real experiments are not available, and must be stated in the record after use);
- Product: `study/reference/<key>/`(script /Spectrum/Two peak tables)+ `study/records/reference.json`;
- `ReferenceResult`:`session` / `references`(key → `ReferenceSpectrum`),
  `conditions`, `reference(condition="")`, `peak_tables`, `records`.

### Combination mode

```python
run_combination_study(reference,                  # <- required: give the reference explicitly
                      combos=[{"zero_fill": 1}],  # or axes=...
                      max_runs=256,
                      localization="parabolic",       # parabolic / gaussian / both
                      localize_peaks=None,            # refine only the named peaks (CSV/ids)
                      edge_margin_ppm=None,             # defaults to 3× the nucleus line width (physical width)
                      direct_range=(10.0, 6.5),        # overrides the workflow base value for this batch
                      roi_f1_ppm=None, roi_f2_ppm=None,
                      resume=True, backend=None, write=True,
                      progress=None) -> StudyResult
```

How to write `reference` (string/Path, or `ReferenceHandle`):

| Writing | Meaning |
| --- | --- |
| `"~/studies/s1"` | Reference to the **main condition** of the study; combine all conditions to run the study |
| `"~/studies/s1#B"` | Reference for this study **Condition B**; only run condition B |
| `"~/studies/s1/study/reference/<key>/reference.json"` | Directly give the reference file (the research root is inferred from the path; only the conditions corresponding to the reference are run) |

- Combination **does not generate a reference**: parameter base = valid parameter for this reference (phase locked), the combination table only covers it
  Explicitly specified key; **Peak selection threshold is locked with reference** (threshold key is written into the combination table -> `SweepError`.
  Prompt "If you want to change the threshold, please rebuild the reference");
- **Independent peak selection for combinations**: Each combination independently detects its own complete peak table on its own candidate spectrum
  (`peak_id` = serial number of this spectrum, `reference_peak_id`/`assignment` left blank), and
  matching against the reference peak table is done downstream. On a per-combination basis, record
  `parameters_resolved.detection` (locked-threshold source `source="reference(locked)"`, margin,
  noise σ, refinement method list);
- `localization` Only the selected peak table is output; each combination can be overwritten by the combination table `localization` key;
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
| `MeasurementError` | Spectrum does not exist, parameter is illegal, Gaussian is used in non-2D |
| `SweepError` | combination table/grid illegal (locked key, exceeded upper limit, no design input), unsupported data type |

All four inherit `SensitivityError`.

## Behaviour compatibility manifest (compat, 2026-09-19/20)

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
