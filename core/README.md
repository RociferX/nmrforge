# core/

Pure data structures and algorithms: the project model, Bruker readers, experiment
classification, sampling detection, peak localisation, quality metrics and processing
primitives. This is the layer everything else is allowed to depend on.

**`core/` stays Qt-free and does not spawn subprocesses.** Both rules are enforced by
[`tests/test_qt_independence.py`](../tests/test_qt_independence.py), which additionally checks
that importing the core does not pull a Qt module into the process.

## Subpackages

| Package | Contents |
| --- | --- |
| `data/` | Bruker directory and container readers, dtype handling, NUS schedule reading, the internal data model |
| `experiment/` | `acqus`/`acqu2s`/`acqu3s` parsing, experiment-type classification, uniform-vs-NUS detection |
| `planning/` | method selection and the processing plan |
| `peaks/` | peak tables, parabolic and 2D Gaussian localisation, axis units |
| `qc/` | FID-level, sampling-level and spectrum-level quality metrics |
| `optimization/` | phase search, phase consensus, projection phase, scoring |
| `processing/` | axis conventions, phase and baseline primitives |
| `project/` | the on-disk project model and `ProjectManager` |
| `audit/` | the structured record of the quality-control corrections applied to data |
| `experiments/` | the experiment-preset registry, loaded from `presets/*.yaml` |

Single-file modules worth knowing: `version.py` (the single version source), `user_errors.py`
(user-facing error translation), `logging_setup.py` (logging setup and per-run log files),
`workspace.py`, `app_paths.py` and `trash.py`.
