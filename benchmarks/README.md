# Benchmarks

This directory contains the **framework** for measuring nmrForge, not a set of results.

Rule for this repository: no document may quote a performance or accuracy figure that
`run_benchmarks.py` has not produced on a stated machine and commit. The README deliberately
contains no claims such as "faster" or "more accurate" for exactly this reason.

## Running it

```bash
python benchmarks/run_benchmarks.py
python benchmarks/run_benchmarks.py --out results/benchmark_results.csv --repeats 10
python benchmarks/run_benchmarks.py --dataset example_data/hsqc_2d   # adds FID read throughput
```

`benchmark_results.csv` is written to the repository root by default and is **not** tracked
(see `.gitignore`); publish results with the machine and commit they came from, or not at all.

## What is measured

### Runtime

| Metric | Needs NMRPipe | Notes |
| --- | --- | --- |
| `data_understanding_mean`, `data_understanding_p95` | no | Parameter parsing + dimensionality + experiment/sampling classification, per dataset. |
| `peak_localization_parabolic`, `peak_localization_gaussian` | no | Per-peak localisation time on a synthetic peak. |
| `fid_read`, `fid_read_throughput` | no | Only when `--dataset` is given. |
| `conversion`, `processing`, `peak_picking`, `batch_throughput` | **yes** | Written as `skipped_no_engine` when NMRPipe is absent, or `skipped_engine_available_not_measured` when an engine exists but no real dataset was supplied. Never estimated. |

### Reliability

| Metric | Meaning |
| --- | --- |
| `experiment_classification_rate` | Fraction of the labelled fixture datasets classified as the experiment their directory name declares. |
| `sampling_classification_rate` | Fraction of the labelled fixture datasets whose uniform/NUS classification matches the directory name. This is the regression guard for the real case that motivated it: metadata claiming NUS while the acquisition covers the full grid. |
| `engine_detected` | Whether NMRPipe (and SMILE) were found at all. |

Both rates are self-consistency checks against the repository's own fixtures. They are not a
measurement of accuracy on arbitrary real data, because the fixtures are synthetic.

### Measurement quality

| Metric | Meaning |
| --- | --- |
| `peak_position_error_<method>_max` (points) | Distance between the localised peak centre and the known centre of a synthetic 2D Gaussian placed at a deliberately off-grid position. |
| `peak_position_error_<method>_max` (ppm) | The same error expressed in ppm using the stated point spacing. |

This is the honest way to state a peak-position capability: a synthetic peak with a known answer,
measured against the same localisation code the processing path uses. Note that a 2D Gaussian
result of `0.0` points is a property of the synthetic, noise-free input, not a claim about real
spectra - real performance is limited by noise, overlap and lineshape.

## Output schema

One row per measurement:

| Column | Meaning |
| --- | --- |
| `benchmark` | `runtime`, `reliability` or `measurement_quality` |
| `dataset` | What the measurement was taken on |
| `metric` | Metric name |
| `value` | Number, or empty when skipped |
| `unit` | `ms/dataset`, `fraction`, `points`, `ppm`, `Mpoints/s`, ... |
| `status` | `ok`, `skipped_no_engine`, `skipped_engine_available_not_measured`, `skipped_no_fixture` |
| `notes` | Repetitions, parameters, and anything needed to interpret the number |
| `software_version` | `core.__version__` |
| `python_version` | Interpreter version |
| `platform` | OS, release, machine |
| `git_commit` | Commit the measurement was taken at |
| `timestamp_utc` | When it ran |

`status` is part of the schema on purpose: a benchmark that could not be run must be
distinguishable from one that produced a value.

## Adding a benchmark

1. Add a `bench_*` function returning rows built with the module's `_row` helper.
2. Call it from `main`.
3. If it needs an external engine, return `skipped_*` rows when the engine is missing rather than
   approximating.
4. Keep the measurement attributable: one metric, one number, one context.