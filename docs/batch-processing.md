# Batch processing

Batch processing applies the same steps to several datasets in one pass. It exists to make routine
work repeatable, and it is honest about its boundary: **batch processing is 2D-only.**

## What it does

`workflow/batch.py::run_batch` takes a set of data entries (typically all members of a data
group, or everything in an experiment) and runs the pipeline steps on each of them.

Key behaviours:

- **Group consistency is checked before running.** The reference spectrum's parameters are
  compared across the group, and a dataset whose processing parameters do not match the group
  reference is reported rather than silently processed with different settings.
- **Steps already completed are skipped.** Each step records an input fingerprint; a dataset whose
  step is already `DONE` with a matching fingerprint is not recomputed, which makes a batch
  re-runnable after an interruption.
- **Failures are isolated.** One failing dataset does not abort the batch; the failure is recorded
  for that dataset and the run continues.
- **Non-2D datasets are skipped with a reason.** They are not processed with a 2D approximation.
  This is decision BATCH-012 in the internal audit log.

## Using it

GUI: `工具` (Tools) -> data groups -> batch run. Progress is reported per dataset in the log for
the group's scope, and the project tree shows per-dataset running state.

Python:

```python
from workflow.batch import run_batch

result = run_batch(
    manager,
    "exp_001",
    ["data_001", "data_002"],      # targets: explicit ids, or "group:<id>" / "all"
    ["fid", "spectrum", "peaks"],  # steps, in order
    backend,
    progress=lambda message: print(message),
)
```

## Relationship to parameter sweeps

They solve different problems, and it is worth not confusing them:

| | Batch | Parameter sweep (`nmrforge_api`) |
| --- | --- | --- |
| Varies | the dataset | the processing parameters |
| Constant | the method | the dataset and the reference |
| Question | "process all of these" | "how sensitive is the result to these parameters?" |
| Output | per-dataset spectra and peak tables | per-combination spectra, peak positions and uncertainties |
| Dimensionality | 2D only | 2D and 3D |

If what you want is a grid of processing parameters on one dataset, use `nmrforge_api`; see
[external-api/README.md](external-api/README.md).

## Boundaries

- 2D only. Non-2D members are skipped and reported.
- Batch does not attempt "smart" per-dataset parameter selection: it applies the group
  configuration, so that differences between outputs reflect differences between datasets rather
  than differences between parameter sets.
- Batch output is recorded per dataset, not as a single batch-level run record, so a partial batch
  remains interpretable.