# 02 · Get started quickly (v0.2)

## 1. Two modes (from 2026-09-14)

The interface splits the work into two modes: **Reference mode** (generate reference) and **Combined mode** (run according to parameter combination).
Processing), the combination mode **must explicitly specify the reference**.

```python
from nmrforge_api import run_reference_study, run_combination_study

# 1) reference mode: import the data + auto-optimise the reference spectrum/script + two reference peak tables
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
    localization="both",                   # parabolic (default)/ gaussian / both
)

print(result.summary["workflow_ids"])      # ['W0001', 'W0002', 'W0003']
print(result.summary["reference_spec"])    # the explicitly given reference
for run in result.runs:
    print(run.workflow_id, run.condition, run.status,
          run.peak_table_path("parabolic"), run.peak_table_path("gaussian"))
```

- The reference mode only builds a reference (1 script + 2 peak tables) and does not run any combination; the peak selection threshold is determined here
  Afterwards, **full locking** (write the threshold key in the combination table and an error will be reported directly. If you want to change the threshold, please rebuild the reference);
- The combination mode does not generate a reference: the parameter base takes the valid parameter of the reference, and the combination table only covers the keys it explicitly specifies;
  The reference does not exist or the peak table is missing -> `ReferenceError` (prompts to run reference mode first);
- **Independent peak selection for combinations** (2026-09-14): Each combination uses the reference locking threshold independently on its own candidate spectrum
  peak selection -> the combination’s own complete peak table; `reference_peak_id`/`assignment`
  stay blank and matching against the reference peak table is done downstream;
- External specification of refinement method: `localization="parabolic"` (default)/ `"gaussian"` (2D only)/
  `"both"`(Both peak tables are displayed);
- `run_parameter_study(...)` is still a one-click convenient entry (internal = reference mode + explicit with research root
  Call combination mode) for quick trial;
- No external peak table required;`peaks=<external peak table>` There is a public library only on the research side/Used only when the peak table has been assigned;
- **Window function and parameter must be paired**: `window.<axis>.type` + `off/end/pow/lb/g1/g2`;
  Writing these sub-parameters when the window type is `none` will be rejected (history will silently idle);
- Baseline:`baseline.<axis>.mode=order` Now render `POLY -ord N -auto` realistically;
  `mode=auto` rendering `POLY -auto`; the software will report `no_spectrum_change` when a certain condition does not change.

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
study/workflows/W0001/A/peak_table_parabolic.csv      # the default refinement method
study/workflows/W0001/B/peak_table_parabolic.csv
```

(You need to explicitly write `localization="gaussian"` in the Gaussian table, or `"both"` at the same time.
`peak_table_gaussian.csv`.)

The peak table of the combination mode is the peak table of the spectrum of the combination: `peak_id` is the peak number of this spectrum.
`reference_peak_id`/`assignment` stay blank -- matching peaks back to reference peak identities, and
the statistics, are done by the downstream analysis program that reads these tables.

## 3. Step-by-step usage (when fine control is required)

```python
from nmrforge_api import (
    add_dataset, build_reference, ensure_reference_peaks, open_study,
    plan_sweep, run_sweep, write_records,
)

session = open_study("~/studies/step_by_step")          # or a name
add_dataset(session, "~/data/bmr12345/1", condition="A")
reference = build_reference(session)                     # reference spectrum + reference script
reference = ensure_reference_peaks(session, reference)   # peak identity + two reference peak tables

plan = plan_sweep(reference, combos=[{"zero_fill": 1}, {"zero_fill": 2}])
runs = run_sweep(session, plan, reference=reference, localization="both")
records = write_records(session, references={reference.dataset_key: reference},
                        plan=plan, runs=runs)
print(records)
```

## 4. Command line

```bash
python -m nmrforge_api init      --study ~/studies/s1 --dataset ~/data/a --condition A
python -m nmrforge_api init      --study ~/studies/s1 --dataset ~/data/b --condition B
python -m nmrforge_api reference --study ~/studies/s1
python -m nmrforge_api peaks     --study ~/studies/s1
python -m nmrforge_api sweep     --study ~/studies/s1 --reference ~/studies/s1 \
    --combos design.csv --localization both
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
