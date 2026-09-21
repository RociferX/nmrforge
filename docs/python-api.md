# Python API

> **Track B - still changing.** This surface is under active development; names, defaults and the
> records it writes can change between releases. Pin a commit and compare
> `nmrforge_api.compat_manifest()` (`behavior_digest`, `compat_level`, `affected`) before treating
> a set of numbers as comparable. The desktop application (Track A) is the mature path.

Two Python surfaces exist, with different stability guarantees.


| Surface | Import | Stability |

| --- | --- | --- |

| Project/processing layer | `core`, `backend`, `workflow` | Internal. Used by the GUI; may change between releases. |

| Scripting API | `nmrforge_api` | Public. Documented, versioned separately as an API contract, and Qt-free. |



If you are writing analysis code around nmrForge, use `nmrforge_api`.



## The scripting API in one example



```python

from nmrforge_api import (

    position_uncertainty,

    run_parameter_study,

    uncertainty_summary,

)



result = run_parameter_study(

    "~/studies/hsqc_params",        # study root (reusable and resumable)

    "~/data/bmr12345/1",            # extracted Bruker dataset directory

    axes={"zero_fill": [1, 2, 4], "window.F1.off": [0.35, 0.45, 0.55]},

)

print(result.summary["status_counts"])       # what this run did (execution summary)



# Statistics are a **separate** step: pair the same peak across the runs of one

# workflow to get peak-position uncertainty (the CSP detection floor).

# ``StudyResult.summary`` reports execution only and carries no statistics.

by_run = {run.workflow_id: run.measurements for run in result.runs}

print(uncertainty_summary(position_uncertainty(by_run))["delta_std_ppm"])

```



What the call does: freeze a reference spectrum, reference script and reference peak tables

(optimised automatically unless you supply your own peaks), run every parameter combination, and

localise the same peaks on every candidate spectrum with the chosen method. Each combination

leaves behind its script, its candidate spectrum (the active spectrum is not replaced), its peak

positions and its warnings; the study accumulates `manifest.json`, `runs.json`,

`peak_positions.csv` and `uncertainty.csv`.



## Importing without Qt



```python

import nmrforge_api          # no Qt import

```



The scripting API never imports a Qt binding (the GUI layer uses PySide6 through

`qtcompat`). Verified behaviour:



```bash

python -c "import sys, core, nmrforge_api; print([m for m in sys.modules if m.startswith('PyQt')])"

# -> []

```



That is what makes it usable on a headless cluster node.



## Lower-level building blocks



For work that needs the pieces rather than a whole study:



```python

from core.data.bruker_reader import read_dataset, read_data

from core.experiment.sampling_detector import full_sampling_evidence

from core.qc import ...          # quality metrics

from core.peaks.localize import ...   # peak localisation

from workflow.direct_diagnostics import run_fid_diagnostics_paths

```



`core.data.bruker_reader.read_dataset` is the entry point for data understanding: it parses the

Bruker parameters, detects the dimensionality, builds the dimension list, classifies the

experiment type and classifies sampling, all without needing NMRPipe.



These modules are the internal layer: they are stable enough to script against, but they carry no

compatibility promise between releases. The runnable example in `examples/quickstart.py` shows the

supported way to use them.



## Error handling



The scripting API raises `nmrforge_api.errors` types rather than leaking `KeyError`/`TypeError`

from deep inside the processing path, so callers can distinguish "your grid is wrong" from "the

engine failed":



```python

from nmrforge_api import SweepError



try:

    run_parameter_study(study, dataset, axes={"window.F1.off": [0.35]})

except SweepError as exc:

    print(f"the sweep request is not valid: {exc}")

```



## Documentation



- [external-api/README.md](external-api/README.md) - overview

- [external-api/02-quickstart.md](external-api/02-quickstart.md) - quickstart

- [external-api/03-api-reference.md](external-api/03-api-reference.md) - full API reference

- [external-api/05-inputs-and-data.md](external-api/05-inputs-and-data.md) - parameter axes

- [external-api/09-limitations-and-roadmap.md](external-api/09-limitations-and-roadmap.md) - limits