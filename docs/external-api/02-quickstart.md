# 02 · Get started quickly (v1.1.1)

## 1. Two modes

The interface has two stages: reference mode builds the reference spectra and tables;
combination mode processes parameter combinations against an explicitly specified reference.

```python
from nmrforge_api import run_reference_study, run_combination_study, run_parameter_study

# 1) reference mode: build an independent reference spectrum/script and peak table per condition
reference = run_reference_study(
    "~/studies/hsqc_params",              # study root (reusable / resumable)
    datasets={"A": "~/data/bmr12345/1"},  # condition A (raw Bruker directory)
    sigma_multiplier=25,                   # peak-picking threshold: set in reference mode only
)

# 2) combination mode: give the reference explicitly (here the study root = the primary condition's reference)
result = run_combination_study(
    "~/studies/hsqc_params",              # or ".../study/reference/<key>/reference.json"
    combos=[                               # user parameter combination table (executed verbatim)
        {"zero_fill": 1},
        {"zero_fill": 2},
        {"zero_fill": 1, "window.F1.off": 0.45},
    ],
    localization="parabolic",              # the only supported localisation method
)

print(result.summary["workflow_ids"])      # ['W0001', 'W0002', 'W0003']
print(result.summary["reference_spec"])    # the explicitly given reference
for run in result.runs:
    print(run.workflow_id, run.condition, run.status,
          run.peak_table_path("parabolic"))
```

- The reference mode builds one reference and one peak table per condition; it does not run combinations. The peak selection threshold is determined here.
  It is locked for combinations: threshold overrides are rejected. Change it by explicitly
  rebuilding the reference with `force=True`.
- The combination mode does not generate a reference: the parameter base takes the valid parameter of the reference, and the combination table only covers the keys it explicitly specifies;
  The reference does not exist or the peak table is missing -> `ReferenceError` (prompts to run reference mode first);
- **Independent references and combination peak selection**: Each condition has its own reference; each combination uses the reference-locked threshold independently on its candidate spectrum
  peak selection -> the combination’s own complete peak table; `reference_peak_id`/`assignment`
  stay blank and matching against the reference peak table is done downstream;
- Peak-position refinement uses three-point parabolic localization. Unsupported methods raise an
  error rather than being substituted;
- `run_parameter_study(...)` is still a one-click convenient entry (internal = reference mode + explicit with research root
  Call combination mode) for quick trial;
- No external peak table required;`peaks=<external peak table>` There is a public library only on the research side/Used only when the peak table has been assigned;
- **Window type and parameters must be paired**: `window.<axis>.type` with `off/end/pow/lb/g1/g2`;
  supplying a sub-parameter for an axis whose reference window type is `none` or `off` raises an error;
- Baseline: `baseline.<axis>.mode=order` renders `POLY -ord N -auto`; `mode=auto` renders
  `POLY -auto`. A condition whose spectrum does not change is recorded with `no_spectrum_change`.

## 2. The two conditions (A/B) are the same as parameter

```python
result = run_parameter_study(
    "~/studies/titration",
    datasets={"A": "~/data/titr/apo", "B": "~/data/titr/holo"},
    combos=[{"zero_fill": 2, "window.F1.off": 0.45}],
)
```

The same `W0001` uses the same user parameter for A and B, and each outputs a peak table:

```text
study/workflows/W0001/A/peak_table_parabolic.csv      # this condition's independent table
study/workflows/W0001/B/peak_table_parabolic.csv
```


The peak table of the combination mode is the peak table of the spectrum of the combination: `peak_id` is the peak number of this spectrum.
`reference_peak_id`/`assignment` stay blank -- matching peaks back to reference peak identities, and
the statistics, are done by the downstream analysis program that reads these tables.

## 2.1 Explicit multi-segment input

Segmented input is opt-in. `segmented=False` (the default) keeps the existing single-directory input.
With `segmented=True`, provide at least two complete Bruker directories in acquisition order; the
list is one condition, not a list of conditions:

```python
run_reference_study(
    "~/studies/segmented",
    dataset=["~/data/segment1", "~/data/segment2"],
    segmented=True,
)
```

For multiple conditions, use a mapping whose values are ordered segment lists, for example
`datasets={"A": ["~/data/a1", "~/data/a2"], "B": ["~/data/b1", "~/data/b2"]}`.
Top-level `datasets=[s1, s2]` means one condition. Segments may be in different parent directories;
duplicates are rejected and the source order is saved. See [input boundaries](05-inputs-and-data.md).

## 3. Step-by-step usage (when fine control is required)

```python
from nmrforge_api import (
    add_dataset, build_reference, ensure_reference_peaks, open_study,
    plan_sweep, run_sweep, write_records,
)

session = open_study("~/studies/step_by_step")          # or a name
add_dataset(session, "~/data/bmr12345/1", condition="A")
reference = build_reference(session)                     # reference spectrum + reference script
reference = ensure_reference_peaks(session, reference)   # peak identity + parabolic reference table

plan = plan_sweep(reference, combos=[{"zero_fill": 1}, {"zero_fill": 2}])
runs = run_sweep(session, plan, reference=reference, localization="parabolic")
records = write_records(session, references={reference.dataset_key: reference},
                        plan=plan, runs=runs)
print(records)
```

## 4. Command line

```bash
python -m nmrforge_api init      --study ~/studies/s1 --dataset ~/data/a --condition A
python -m nmrforge_api init      --study ~/studies/s1 --dataset ~/data/b --condition B
python -m nmrforge_api init      --study ~/studies/segmented --segmented \
    --dataset ~/data/segment1 --dataset ~/data/segment2 --condition A
python -m nmrforge_api reference --study ~/studies/s1
python -m nmrforge_api peaks     --study ~/studies/s1
python -m nmrforge_api sweep     --study ~/studies/s1 --reference ~/studies/s1 \
    --combos design.csv
python -m nmrforge_api status    --study ~/studies/s1
python -m nmrforge_api report    --study ~/studies/s1
```

## 5. What file to look at

| Want to know | See |
| --- | --- |
| What is each combination and what is its status | `study/workflows/<id>/workflow.json` |
| Which parameters are actually used | `run.json.parameters_used` + `parameters_resolved` |
| Peak table (entry point for downstream analysis) | `study/workflows/<id>/<condition>/peak_table_*.csv`; the long table is `study/records/peak_table_*.csv` |
| Process script / spectrum | `study/workflows/<id>/<condition>/process.com` / `spectrum.ft2` |
| log | `study/workflows/<id>/<condition>/log.txt`(complete)+ `workflows/<id>/log.txt` |
| Versions and Hash | `run.json.versions` / `manifest.json` |
