# nmrforge_api guide (API 1.1.1)

`nmrforge_api` is the Qt-free Python/CLI interface for software **1.0.5**. It imports Bruker data,
builds and freezes an independent reference spectrum/table per condition, runs explicit parameter
combinations and writes independently detected parabolic peak tables with provenance and QC records.

A condition uses one raw directory by default. `segmented=True` enables an ordered list of two or
more directories. Reference construction imports/converts/merges the sources; combinations reuse
the frozen FID and do not convert or merge as a fallback. Missing or changed evidence requires
reference rebuilding with `force=True` (`reference --force` on the CLI).

The unified table has **38 columns**, including full F1/F2/F3 coordinates, nuclei and equivalent
linewidths. Peak IDs belong to one spectrum; combinations leave `reference_peak_id` empty.
Cross-spectrum matching, assignment and statistical inference belong to downstream analysis.
`compat_manifest()` records code/contract fingerprints and affected processing steps.

## Checks and evidence

Mocked-engine tests check workflow and record behaviour. Real NMRPipe/SMILE comparisons retain
input, command/parameter provenance and resulting spectra in the [evidence report](../evidence/real-data-comparison.md).
Detection/localisation QC and candidate matching describe those measurements, rather than assigned
peak identities. See [support and execution boundaries](09-limitations-and-roadmap.md).

## Reading order

| Document | Content |
| --- | --- |
| [Overview](01-overview.md) | Terminology, runtime semantics and boundaries |
| [Quickstart](02-quickstart.md) | One-step, stepwise, multiple conditions and CLI examples |
| [API reference](03-api-reference.md) | Public signatures and return structures |
| [CLI reference](04-cli-reference.md) | Commands and flags |
| [Inputs and data](05-inputs-and-data.md) | Conditions, segmented import, parameter keys and tables |
| [Outputs and records](06-outputs-and-records.md) | Layout, 38 columns, status, warnings and provenance |
| [Methods and metrics](07-methods-and-metrics.md) | Reference processing, localisation, threshold, sign and QC |
| [Integration](08-integration-guide.md) | Reading products and downstream interfaces |
| [Support and execution boundaries](09-limitations-and-roadmap.md) | NUS, parameter validation, resume and FID reuse |
| [Troubleshooting](10-troubleshooting.md) | Errors, warnings and recovery |
| [Examples](examples/) | Executable API walkthroughs |

## Install and run

No additional dependencies are required: just use NMRForge’s own environment (`numpy`/`scipy`/`nmrglue`;
Real machine processing requires NMRPipe). Command line entry:

```bash
python -m nmrforge_api --help
```

## One minute example

```python
from nmrforge_api import run_reference_study, run_combination_study

# reference mode: each condition gets its own spectrum, script, and peak table (threshold locked afterwards)
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
