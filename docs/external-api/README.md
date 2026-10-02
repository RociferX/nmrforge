# nmrforge_api external documentation (API contract 1.0)

> `nmrforge_api` is the public, versioned scripting interface (`API_VERSION = "1.0"`) and does
> not depend on Qt. Use `compat_manifest()` and the release notes to identify behaviour or contract
> changes between versions. Source updates and released AppImages are separate deliverables.


`nmrforge_api` runs parameter-combination processing studies. It imports Bruker data, builds and
freezes a reference workflow, executes user-provided combinations and writes one parabolic peak
table per candidate spectrum with processing provenance and QC records.

The software only performs processing and archiving; **statistical inference and scientific
conclusions are outside its scope** and belong to downstream analysis. The public functions,
records, and compatibility behaviour are described in this documentation set.

## Validation boundary: engineering regression vs scientific validation

When you cite a product of this software, keep the two apart:

- **Engineering regression** (the configured tests using mocked engine boundaries) checks
  covered software paths and record consistency; these tests do not run NMRPipe or SMILE and
  are not real-engine or scientific validation;
- **Scientific validation** (a benchmark and criteria of your own) is outside the scope of this
  software and belongs to the downstream analysis program -- engineering regression and real-engine
  smoke runs **cannot** show that the processing is scientifically correct on real systems.

See [limitations and roadmap](09-limitations-and-roadmap.md) for supported processing routes and
validation boundaries.

## Reading order

| Document | Content |
| --- | --- |
| [01-overview.md](01-overview.md) | Positioning, terminology, runtime semantics, software boundaries |
| [02-quickstart.md](02-quickstart.md) | One-step / step-by-step / two conditions A/B / CLI Get started |
| [03-api-reference.md](03-api-reference.md) | All public functions and data structures |
| [04-cli-reference.md](04-cli-reference.md) | `python -m nmrforge_api` Six commands |
| [05-inputs-and-data.md](05-inputs-and-data.md) | data, condition, parameter key, combination table |
| [06-outputs-and-records.md](06-outputs-and-records.md) | directory layout, unified peak table fields, status and warning codes |
| [07-methods-and-metrics.md](07-methods-and-metrics.md) | Reference workflow, parabolic localisation, peak threshold/margin and QC |
| [08-integration-guide.md](08-integration-guide.md) | Handover to downstream analysis (what to read, how to read) |
| [09-limitations-and-roadmap.md](09-limitations-and-roadmap.md) | support matrix, NUS boundaries, shards, roadmap |
| [10-troubleshooting.md](10-troubleshooting.md) | Common errors, warning processing, breakpoint resume |
| [examples/](examples/) | Runnable example(one step/step by step/Measure only) |

Contract version: `API_VERSION = "1.0"`; the public entry points and output fields are described
in this document set.

## Install and run

No additional dependencies are required: just use NMRForge’s own environment (`numpy`/`scipy`/`nmrglue`;
Real machine processing requires NMRPipe). Command line entry:

```bash
python -m nmrforge_api --help
```

## One minute example

```python
from nmrforge_api import run_reference_study, run_combination_study

# reference mode: reference spectrum + script + one parabolic reference peak table (the threshold is set here and locked afterwards)
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
