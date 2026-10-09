# 05 · Input: data, condition and parameter combination table (v1.1.1)

## 5.1 Raw data

- **Bruker raw dataset directory** (the downloaded/extracted directory holding `acqus`/`ser`);
  archives and processed formats are not supported, and the error message says so;
- Import only links the raw data, writes the metadata and registers the import run; it converts
  nothing;
- Sampling method: uniform any dimension, **2D NUS**(SMILE reconstruction), 3D NUS currently only supports reference construction;
- Inputs labelled NUS use uniform processing only when a valid schedule covers the entire grid
  in standard order. Full coverage in a different order still requires schedule-based placement.
  Declared NUS without a schedule or recoverable coordinates is rejected; a `ser` without zero
  traces is not sufficient evidence of uniform acquisition. See `reference.sampling_evidence`;
- Multi-segment input is explicit: set `segmented=True` and pass the complete ordered list of at
  least two Bruker raw directories. The default remains one directory; no segment auto-discovery is
  performed, and one string is not a segment list. Paths must be unique; segments can be in different
  parent directories. Kinetic layouts and NUS data missing a usable `nuslist` are rejected. Acquisition
  parameters, dimensions, nuclei, effective TD, spectral width, sampling mode, axis layout, SFO frequency
  and carrier must agree across segments. These checks do not establish that the segments share
  the same sample or experimental conditions; callers must confirm they are suitable to merge;
- Segments represent one condition, not independent experiments or spectra to average downstream.
  Uniform repeats use the existing repeat-sum behavior. For NUS, repeated points are summed and
  complementary schedules are merged by sampled point. Sampling rate is calculated from the unique
  union of sampled points over total coverage. No cross-condition or cross-workflow peak relationship
  is created by this input mode;

Examples for one condition and multiple conditions:

```python
run_parameter_study(
    root, dataset=["~/data/part1", "~/data/part2"], segmented=True,
    combos=[{"zero_fill": 1}],
)

run_parameter_study(
    root,
    datasets={"A": ["~/data/a1", "~/data/a2"],
              "B": ["~/data/b1", "~/data/b2"]},
    segmented=True, combos=[{"zero_fill": 1}],
)
```

At the API boundary, `segmented` must be a strict `bool`. A top-level `datasets=[s1, s2]` is one
condition; use a mapping for multiple conditions. Replacing the source list or changing its order in
an existing study requires a new condition or research root. `force=True` rebuilds reference
processing only; it does not change the bound source segments. Reopening an existing segmented study
for reference or combination processing requires no segmented flag when no new data is imported.

## 5.2 condition (A/B...)

```python
run_parameter_study(root, datasets={"A": "…/apo", "B": "…/holo"}, combos=[...])
# or step by step:
add_dataset(session, "…/apo", condition="A")
add_dataset(session, "…/holo", condition="B")
```

- The condition label must be unique (repeated errors will not be overwritten); the default is automatically assigned A/B/C…;
- Each condition has a **reference** (the phase / noise comes from the condition's own data);
- Peak identity, user parameter combination **full condition sharing**: the same workflow uses the same copy for A/B
  `parameters_requested`, output the respective peak table.

## 5.3 parameter combination table (user definition)

Two entrances (must and can only be given one):

1. **Explicit combination table** (recommended, the design is determined externally, the interface does not make design decisions):

```csv
window.F1.off,zero_fill,phase_delta.F2.p0
0.35,1,-5
0.45,2,5
```

```python
run_parameter_study(root, datasets={"A": path}, combos=load_combo_table("design.csv"))
```

2. **Axis Grid** (convenient full factor expansion):

```yaml
axes:
  zero_fill: [1, 2, 4]
  "window.F1.off": [0.35, 0.45, 0.55]
max_runs: 128
```

The upper limit of the number of combinations `max_runs` (default 256); combination order = table order = workflow number order.
**Empty cell / `null` = This row does not specify this parameter ** (the reference base is used), not "covered with a null value".

## 5.4 parameter key

### Process parameter (read directly by the processing backend)

| Key | Meaning |
| --- | --- |
| `zero_fill` | zero filling multiple (only the point distance is changed, the physical peak position is not changed) |
| `window.<axis>.type` + `off/end/pow/c/lb/g1/g2` | Window function: ** must be given in pairs** -- `type=none/off` When the axis does not insert a window row, the sub-parameter will be ignored (API will report an error directly); `sine_bell`(`off/end/pow/c`), `sine_bell_squared`, `gaussian`(`g1/g2`), `exp`(`lb`) |
| `baseline` | baseline correction (`{enabled, mode: auto|order, order, axes}`):`mode=order` render `POLY -ord N -auto`(NMRPipe `-auto` automatically picks the baseline point, effective from 2026-09-16);`mode=auto` render `POLY -auto`;`mode≠order` `order` is ignored (API prompt) |
| `reference_optimize` | **Test only/Recurrence/audit** Reference optimisation switch used (see §5.10); do not use | for real experiments
| `ext_lo`/`ext_hi`/`extract` | Extraction window (deterministic parameter, generally no need to enter the grid) |
| `points_per_line` | Target point distance/line width points(deterministic parameter) |
| `linewidth_hz` | Each core line width (Hz), affecting the default physical width conversion |
| `sampling.auto_phase` / `sampling.ft_alt` | Locked in combinations; change when explicitly rebuilding the reference |
| `sampling.ft_neg` / `sampling.ft_neg_f1/f2` / `sampling.flip_f1/f2` | Explicit boolean FT-neg candidates; global ft_neg takes precedence; changes do not automatically reoptimise phase |

### Phase axis (retain prefix)

- `phase_delta.<axis>.p0|p1`: **Deviation relative to the reference phase** (such as manual identification deviation +/-5°);
- `phase.<axis>.p0|p1`:**Absolute phase value**;
- Directly writing `phases`/`direct_phase` will report an error (which will destroy phase locking semantics) -- Please use the above two

### NUS(SMILE) parameter

| Key | Meaning |
| --- | --- |
| `nsigma`(alias `nSigma`) | SMILE Threshold multiple; if not written, it will be automatically divided by the sampling rate |
| `thresh` | SMILE Threshold |
| `nthread`(alias `nThread`) | Number of threads |
| `smile_scaling` / `smile_report` | Zoom/Report switch |
| `nuslist_file` / `nuslist_count` / `timeout_s` / `fid_noise*` | certainty/Strategy parameter (prompt only) |

During automatic binning, the `nsigma`/`thresh` actually used will be written in.
`parameters_resolved.smile`(requested = `auto(smile_tier)`,actual = actual value).

## 5.5 Reference peak table (optional input)

```python
run_parameter_study(..., peaks="library.list")   # or a peak_id,H_ppm,N_ppm CSV
```

- Not required by default: the software automatically selects peaks and establishes peak identities `R0001…`;
- An external peak table is given: the peak identity table as the main condition is frozen (`peak_source="external"`), other conditions
  The **reference peak table** follows the same identity (the peak table in combined mode does not track it -- the combined peak table selects peaks independently.
  `reference_peak_id` leave blank);
- Accepted formats: Poky/Sparky `.list`, NMRForge old CSV, research project
  `peak_id,H_ppm,N_ppm,height,linewidth,volume`.

## 5.6 parameter principle (software does not cross boundaries)

- **Run strictly according to user parameter**: The basis of this condition = the effective parameter of the reference operation, the combination table only covers it
  Explicitly specified key; the software does not modify the user specified parameter;
- **Do not automatically generate research parameter space**: `combos=` is executed as is, `axes` is just conveniently expanded;
- Parameter is illegal and gives **error or warning**: lock key error, certainty/Unknown key write `plan.notes`;
- All parameters that affect the result must be traceable: `parameters_requested` ->
  `parameters_used` -> `parameters_resolved` (automatic parameter actual result).

### Reference cache, carrier and spectral width (v1.1.1)

Reference reuse requires an exact normalized processing request match, including phase route,
direct range, and all parameters (nested mappings and equivalent dotted keys normalize alike).
The SHA-256 input fingerprint is stored at reference.json.input_fingerprint using schema
nmrforge_api.reference_input.v1. Any mismatch, or an old reference without a valid fingerprint,
raises ReferenceError and requires explicit force=True / CLI --force; there is no automatic rebuild.
Multi-condition requests preflight every condition before the processing engine starts. Resume uses
schema nmrforge_api.resume.v4.

Reference construction accepts carrier_ppm as a per-axis mapping or params["carrier_ppm"] /
dotted carrier_ppm.F1. CLI reference accepts repeatable --carrier-ppm F1=120.0 --carrier-ppm
F2=4.7; duplicate axes are errors. Explicit keyword values override common params per axis, then
params_by_condition overrides common values per axis. Logical F axes are F2=x/F1=y in 2D and
F3=x/F2=y/F1=z in 3D. Values must be finite numbers (zero and negative are valid); booleans,
NaN/Inf, empty maps, unknown axes, and out-of-dimension axes are rejected. Unspecified axes retain
the actual CAR from the selected conversion path; raw acqus is not changed. When Bruker -AUTO is
available and succeeds, the actual CAR in fid.com is retained (it may follow AUTO rules such as
water peak or gamma ratio; the API does not force O1/BF1 or promise a fixed value). Only when
uniform AUTO is unavailable or fails and the built-in bruk2pipe fallback is used does CAR come
from parsed Dimension.o1p: nonzero acquisition O1P first, otherwise O1/BF1, then O1/SFO1 when BF1
is absent, or 0 if neither is available. These paths do not share a CAR convention. Carrier is
ppm-axis calibration, not an acquisition SFO1 change or phase setting, and is not a scientific
identity inference. A changed carrier requires force=True. Sweeps inherit reference carrier and reject
carrier scans in axes, combos, or base_overrides because they reuse the converted FID.

Reference params may explicitly set positive finite sweep_width_hz per logical axis. Original and
resolved widths, source, and consistency ratio are recorded; sweeps reuse the converted FID and
cannot change its spectral width. This applies to any logical F axis, including an axis acquired
for 15N; use its F-axis key rather than a nucleus-name key.

## 5.9 Specify parameter by dimension (combination table)

Combination table/Grid key support**Dot number path**, so the two dimensions (and the third dimension of 3D) can be specified separately;
`parameters_requested` retains the user key as it is, `parameters_used` is the merged axis-by-axis structure.

| Processing steps | Axis-by-axis writing (example) | Semantics |
| --- | --- | --- |
| Window function | `window.F1.off`, `window.F2.off`, `window.F1.type` | One set for each logical dimension (type/end value) |
| baseline | `baseline.F1.enabled`, `baseline.F2.mode`, `baseline.F1.order` | switch per dimension/model/Order |
| zero filling | `zero_fill.F1=2`, `zero_fill.F1.size=512`, `zero_fill.F1.mode=none` | naked scalar = **k x TD** (synonymous with global `zero_fill=k`); explicit SI with `.size` |
| Line width (Hz) | `linewidth_hz.F1=12`, `linewidth_hz.F2=9` | Per-dimensional line width: affects automatic zero filling target and physical width conversion |
| target number resolution | `points_per_line.F1=4`, `points_per_line.F2=2` | "points per linewidth" per dimension (target for automatic SI) |
| phase | `phase.F1.p0`, `phase_delta.F2.p0` | Axis-by-axis absolute phase / deviation from reference |
| FT-neg candidates | `sampling.ft_neg`, `sampling.ft_neg_f1/f2`, `sampling.flip_f1/f2` | Explicit booleans; global ft_neg takes precedence; changing them does not automatically reoptimise phase |
| Locked sampling settings | `sampling.auto_phase`, `sampling.ft_alt` | Rejected in combinations; change them when explicitly rebuilding the reference |

```csv
window.F1.off,window.F2.off,zero_fill.F1,baseline.F2.enabled,points_per_line.F1
0.35,0.45,2,false,4
0.45,0.45,4,true,2
```

- Peak localization has one supported method, `parabolic`: a deterministic three-point vertex
  calculation. Gaussian-fitting ROI and iteration-budget settings were removed and are not
  accepted configuration keys.
- The direct dimension range `ext_lo`/`ext_hi` only applies to ** direct dimension **; please use `window.F3.*` for 3D data, etc
  Axis-by-axis key (if the axis is a direct dimension);
- The axis-by-axis parameter (reference spectrum definition) of the reference layer is specified with `params=`/`direct_range=` of the reference mode
  The keys in the combination table only cover **this combination**;
- Unknown axis keys (such as `window.F9.off`) will not report an error, but will not take effect: please check the axis name against the table above;
- **Window type and window parameter must be paired**: When the axis is valid `type=none/off`, write `window.<axis>.off/end/…`
  Will be rejected by `SweepError` (real machine example: after selecting none in the reference window, `window.F1.off`.
  No window function lines are rendered in the whole process); a prompt will be given when the base does not have `type` (it will be rendered by default sine_bell).

## 5.10 Reference optimisation switch (**Test only/Recurrence/audit; not available for real experiments**)

> ⚠️ **Please keep the default for real experiments (refer to automatic optimisation)**. The following switches will turn off/Limited to the reference stage
> Automatic optimisation, so that the reference is no longer "automatically optimised"; once used, it must be in the processing record and paper method
> Clearly write "the reference is not automatically optimised / optimisation is qualified", otherwise the legality of the reference will not be established

```python
run_reference_study(
    root, dataset,
    params={
        "reference_optimize": {
            "baseline": "off",            # off / auto / {"grid": [["off",0],["auto",1],["order",2],["order",3]]}
            "window": "off",              # off / auto / {"direct_candidates": [...], "indirect_candidates": [...]}
        },
        "baseline": {"F1": {"enabled": False}},   # with optimisation off, the final run uses this configuration directly
        "window": {"F1": {"type": "sine_bell", "off": 0.45, "end": 0.98}},
    },
)
```

- `baseline="off"` / `window="off"`: Skip the corresponding optimizer, refer to the final run and use the one you gave directly
  `baseline` / `window` configuration; `{"grid": …}` / `{…_candidates: …}` only limits the candidate set.
  The best ones are still selected by scoring;
- The switch is left as is in `reference.json.params.reference_optimize` (auditable), **won't**
  Into combined base(sweep_params);
- The combination mode (parameter perturbation stage) inherently controls the explicit control window/baseline one by one and does not require this switch


## 5.8 direct dimension range (can be specified externally)

The direct dimension extraction window is specified with **ppm**, and NMRPipe `EXT -x1/-xn` and config.
`processing.ext_lo/ext_hi` Same sequence:

- `ext_lo` = direct dimension **High-end** (larger ppm, corresponding to `EXT -x1`);
- `ext_hi` = direct dimension **low end** (smaller ppm, corresponding to `EXT -xn`)

The three writing methods can be combined, and the priority is `params` < `direct_range` < explicit `ext_lo/ext_hi`:

```python
run_reference_study(root, dataset, direct_range=(10.5, 6.5))       # (high, low)
run_reference_study(root, dataset, direct_range=(6.5, 10.5))       # reversed: swapped back automatically
run_reference_study(root, dataset, ext_lo="10.5", ext_hi="6.5")    # explicit
```

```bash
python -m nmrforge_api reference --study ~/studies/s1 --direct-range 10.5 6.5
python -m nmrforge_api sweep --study ~/studies/s1 --reference ~/studies/s1 \
    --combos design.csv --direct-range 9 7
```

- **Reference mode**: the range is part of the reference definition; when it disagrees with the
  established reference, the reference spectrum and its parabolic peak table are rebuilt (stated
  in the log), and `force=True` rebuilds unconditionally;
- **Combination mode**: `direct_range=` writes this batch's `base_overrides` (the reference spectrum is not
  rebuilt); every condition still starts from its own reference parameters and each combination may override with
  `ext_lo`/`ext_hi` (`plan.notes` states the wording). An override that **disagrees** with the frozen reference range
  raises by default - pass `allow_ext_override=True` (CLI `--allow-ext-override`) to confirm it, and every run then
  carries the `direct_range_override` warning code (P1-4, 2026-09-19);
- Archive: the reference record keeps `params.ext_lo/ext_hi` plus `direct_range` (`ext_lo`/`ext_hi`/`unit`/`source`
  with `source` in `explicit|params|default`; `default` = no range was given so the backend/config default was used,
  with a `warning`); each `run.json` keeps `parameters_resolved.direct_range` (`ext_lo`/`ext_hi` + `source`:
  `reference_or_base` / `combo`);
- Illegal input (only given to one end, both ends are the same, non-numeric) directly reports `SweepError`;
- Also effective in config/`params`: `params={"ext_lo": "10.5", "ext_hi": "6.5"}` will be
  Parse into the same range and keep files in a unified way.


## 5.7 Peak selection threshold (optional when generating reference, then locked)

Peak selection threshold of the reference peak table = **noise σ multiple** (`sigma_multiplier`,
passed to detection internally as `min_snr`). Default 35σ (existing default, unchanged behaviour);
**can be specified externally when generating the reference**:

```python
pick_reference_peaks(session, sigma_multiplier=20)          # builds the reference peak tables
ensure_reference_peaks(session, reference, sigma_multiplier=20)
run_parameter_study(root, datasets=..., combos=..., sigma_multiplier=20)
```

```bash
python -m nmrforge_api peaks --study ~/studies/s1 --sigma 20
```

- **Threshold is part of the reference definition**: Once the reference peak table is frozen, all subsequent workflow/parameter disturbances
  Only the reference threshold can be used; if you give a different threshold at this time, it will directly report `ReferenceError`(CLI.
  Exit code 2), peak reselection will not occur silently;
- The threshold value that is consistent with the reference (or consistent with the reference default 35σ) can be explicitly given to -> multiplexed, without repeated peak selection;
- The threshold you want to change belongs to **Reconstruction Reference**: Explicit `force=True`, or delete the condition
  `study/reference/<key>/` Post-rerun reference;
- Actual usage fallback: `reference.json.peak_params.sigma_multiplier` (when generating reference
  Selected value), `previous_sigma_multiplier`(force previous version when rebuilding).
  `detection.sigma_multiplier` and `detection.threshold_source`.
  (`user` / `default(35sigma)`); each workflow record is recorded separately.
  `parameters_resolved.detection`(`source="reference(locked)"`, actual σ, margin.
  Noise σ, refinement method list; `independent=true`, `reference_matching="external"`);
- The threshold is too high and peaks cannot be selected -> clearly report an error (do not silently produce an empty peak table);
- The threshold is written into the workflow parameter combination table -> an error is reported directly (`SweepError`), prompting "If you want to change the threshold, please rebuild."
  Refer to";
- Combination mode **None** `max_peaks`: This combination detects as many peaks as possible under the locking threshold

## 5.11 Targeted localization (2026-09-19)

By default localization covers **every detected peak** of the spectrum. To localize only named
peaks, provide a target list; detection and peak numbering remain unchanged:

```python
run_combination_study(f"{root}#A", combos=...,
                      localize_peaks="targets.csv")
run_sweep(session, plan, localize_peaks=[1, 5, 9])
detect_and_localize(spectrum, targets=(1, 5, 9))
```

```bash
python -m nmrforge_api sweep --study ~/studies/s1 --reference ~/studies/s1 \
    --combos design.csv --localize-peaks targets.csv
```

A combination table can name it per row (**highest priority**; a relative path resolves against
the **combination table's directory**):

```csv
zero_fill,localization.targets
1,targets.csv
2,
```

The target list needs at least a `peak_id` column (optionally `reference_peak_id` for the
record; a single-column text file with one number per line is accepted too; duplicate ids are
merged keeping first-seen order). Semantics:

- **Detection and the peak set are untouched**: the target list only decides which peaks take
  the method's refinement -- picking, row count and `peak_id` numbering never change;
- unlisted peaks **stay in the table** at their integer detection-grid coordinates, with
  `localization_requested="parabolic"`, `localization_method="none"`, unrun localization QC
  (`fit_success`/`FWHM_*`/`boundary_hit`) as `NaN`, an empty `failure_reason`, and
  `fallback=false`;
- a targeted parabolic refinement failure is recorded in `failure_reason` and **never**
  re-fits another candidate; `fallback` / `fallback_reason` are reserved for actual fallback;
- record: `run.json.parameters_resolved.detection.localization_targets` =
  `{scope, source, path, sha256, n_targets, peak_ids, reference_peak_ids?}` (same style as
  `direct_range.source`); `peak_localization.<method>` also carries `localization_scope`
  (`all`/`subset`), `n_targeted` and `n_skipped`;
- the resume fingerprint includes the **resolved** target list (path + SHA-256 + ids), so
  swapping the list -- or editing the content behind the same path -- re-runs instead of
  reusing the old run;
- it **raises** rather than silently degrading to the whole spectrum. Static errors (an empty
  list / a missing file / a missing `peak_id` column) raise `SweepError` before any processing
  (the whole batch stops); an unknown `peak_id` can only be judged against a spectrum, so it
  lands on that (workflow, condition): the run is marked `failed` with a message naming the
  undetected ids and the detected count (`peak_id` is a **per-spectrum** number, one spectrum
  per workflow x condition), and nothing is silently ignored or substituted;
- no targets = today's whole-spectrum behaviour (`scope=all`), with zero impact on existing
  study roots and records.

## 5.12 Legacy per-method target keys (removed)

The following section documents removed syntax only. Do not use these examples: `localization`
now accepts only `parabolic`, and method-specific target mappings/columns raise an error. Use
the method-independent `localize_peaks=` argument or `localization.targets` column instead.

The old method-specific syntax was removed when Gaussian fitting was deleted:

```python
run_combination_study(f"{root}#A", combos=..., localization="both",
                      localize_peaks={"gaussian": "truth_peaks.csv"})
```

```bash
python -m nmrforge_api sweep --study ~/studies/s1 --reference ~/studies/s1 \
    --combos design.csv --localization both \
    --localize-peaks-gaussian truth_peaks.csv
```

A combination table writes it per row (`localization.targets.<method>`; relative paths still
resolve against the table directory):

```csv
zero_fill,localization,localization.targets.gaussian,localization.targets.parabolic
1,both,truth_peaks.csv,
2,both,""
```

The semantics match the single-key form exactly (**detection unchanged, unlisted peaks kept,
defaults unchanged**), plus three rules:

- priority: `localization.targets.<method>` > `localization.targets.all` (or an `all`/`*`/`both`
  key) > `localization.targets` > the call argument `localize_peaks=`; a per-method key
  overrides only that method and the others keep the call argument;
- an explicit empty string means **unlimited** for that method (distinct from "not given",
  which inherits the method-independent list);
- record: `parameters_resolved.detection.localization_targets` stores the resolved target list;
  `peak_localization.parabolic` summarizes the method's targeted and skipped counts.

## 5.13 Condition granularity (2026-09-20)

A and B are two different spectra with **different detected peak sets**, so the same combination
row has different target peak numbers per condition and one list cannot serve both. The target
CSV may therefore carry a `condition` column:

```csv
condition,peak_id
A,12
A,37
B,9
B,41
```

- with a `condition` column, each condition reads only its own rows and `peak_id` is validated
  against **that condition's spectrum** (unknown ids still raise, never silently ignored);
- without it, behaviour is **bit-for-bit** the same as before (one list for the batch) and the
  record writes `by_condition: "all"`;
- a condition with **no rows at all** fails **before processing** (the default
  `on_missing="error"`); to let it through, declare `on_missing="all"` (unlimited =
  whole-spectrum refinement) or `on_missing="none"` (refines nothing), and the strategy is
  recorded;
- a condition name that **does not belong to the study** raises (never silently ignored);
- an empty file / a missing `peak_id` column keeps the existing error behaviour.

`on_missing` is written inside the mapping form (a call argument or a combination-table row):

```python
run_combination_study(f"{root}", combos=...,
                      localize_peaks={"path": "targets.csv", "on_missing": "none"})
```

**One file per condition** (the mapping form; the CLI takes a single file, and the `condition`
column already serves A and B without re-running the sweep per condition):

```python
run_combination_study(f"{root}", combos=...,
                      localize_peaks={"A": "a.csv", "B": "b.csv"})
run_combination_study(f"{root}", combos=...,
                      localize_peaks={"default": "all_conditions.csv",
                                      "by_condition": {"A": "a.csv"}})
```

A combination table works too (CSV cells use the brace form; relative paths still resolve
against the table directory):

```csv
zero_fill,localization.targets
1,"{A: a.csv, B: b.csv}"
```

Record: the top level of `run.json.parameters_resolved.detection.localization_targets` keeps
`path`/`sha256` (the whole source file) plus the `peak_ids`/`n_targets`/`n_skipped` actually in
force for this run, adds `condition`/`on_missing` when written per condition, and gains
`by_condition` (per condition: `peak_ids`/`n_targets`/`line_ranges`/source `path` + `sha256`/
`from`); when shared by the batch `by_condition` is `"all"`. `peak_localization.<method>`
`n_targeted`/`n_skipped` stay **per run**. The resume fingerprint carries the **resolved
per-condition list**, and only this condition's part: editing A's rows does not make B re-run
(the mapping form carries its own file's SHA-256). The fingerprint payload changed shape, so
existing resume caches are invalidated **once** and recomputed to the same numbers.
