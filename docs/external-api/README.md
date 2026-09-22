# nmrforge_api external documents (v1.0 - first version)

> **Track B - still changing.** This is the in-flux scripting surface; see
> [Python API](../python-api.md) for the stability promise and the behaviour-digest check.


`nmrforge_api` is NMRForge's **parameter-combination processing executor**: give it raw NMR data and
a user-written parameter combination table, and it builds the reference workflow automatically,
runs the combinations in batches, and writes a peak table per spectrum for both the parabolic and
the 2D-Gaussian algorithm, with full provenance and QC.

The software only performs processing and archiving;**statistical inference and scientific
conclusions are outside its scope** and are done by downstream analysis (the σ/Δδ summary is a
**test/detection aid** only and does not enter the processing products). See
[API_CONTRACT.md](../API_CONTRACT.md).

## Validation boundary: engineering regression vs scientific validation

When you cite a product of this software, keep the two apart:

- **Engineering regression** (the full local `pytest`, the full real-engine VM suite
  `bash scripts/vm_test.sh`, the CI `static` / `tests` / `release-readiness` jobs, the
  real-engine API smoke test `scripts/vm_api_smoke.py`) shows that the pipeline and its
  records are self-consistent, that the same input gives the same result and that the
  products are reproducible;
- **Scientific validation** (a benchmark and criteria of your own) is outside the scope of this
  software and belongs to the downstream analysis program -- engineering regression and real-engine
  smoke runs **cannot** show that the processing is scientifically correct on real systems.

The smoke-test scripts, product paths and log directory (and why the CI real-engine job
is opt-in) are in [09-limitations-and-roadmap.md](09-limitations-and-roadmap.md) §9.5.

## Reading order

| Document | Content |
| --- | --- |
| [01-overview.md](01-overview.md) | Positioning, terminology, runtime semantics, software boundaries |
| [02-quickstart.md](02-quickstart.md) | One-step / step-by-step / two conditions A/B / CLI Get started |
| [03-api-reference.md](03-api-reference.md) | All public functions and data structures |
| [04-cli-reference.md](04-cli-reference.md) | `python -m nmrforge_api` Six commands |
| [05-inputs-and-data.md](05-inputs-and-data.md) | data, condition, parameter key, combination table |
| [06-outputs-and-records.md](06-outputs-and-records.md) | directory layout, unified peak table fields, status and warning codes |
| [07-methods-and-metrics.md](07-methods-and-metrics.md) | Reference workflow, two positions, Peak selection threshold/Margin size, QC |
| [08-integration-guide.md](08-integration-guide.md) | Handover to downstream analysis (what to read, how to read) |
| [09-limitations-and-roadmap.md](09-limitations-and-roadmap.md) | support matrix, NUS boundaries, shards, roadmap |
| [10-troubleshooting.md](10-troubleshooting.md) | Common errors, warning processing, breakpoint resume |
| [examples/](examples/) | Runnable example(one step/step by step/Measure only) |

Contract: `API_CONTRACT.md` (v1.0; released as the first version on 2026-09-22).

## Install and run

No additional dependencies are required: just use NMRForge’s own environment (`numpy`/`scipy`/`nmrglue`;
Real machine processing requires NMRPipe). Command line entry:

```bash
python -m nmrforge_api --help
```

## One minute example

```python
from nmrforge_api import run_reference_study, run_combination_study

# reference mode: reference spectrum + script + two reference peak tables (the peak-picking threshold is set here and locked afterwards)
run_reference_study(
    "~/studies/hsqc_params",
    datasets={"A": "~/data/apo", "B": "~/data/holo"},
    sigma_multiplier=25,
)

# combination mode: give the reference explicitly (required); processing runs per the combination table
result = run_combination_study(
    "~/studies/hsqc_params",
    combos=[{"zero_fill": 1}, {"zero_fill": 2}],
)
print(result.summary["status_counts"])
print(result.records["peak_table_parabolic"])
```
