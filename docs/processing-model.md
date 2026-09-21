# Processing model

This page explains how nmrForge decides what to do to a dataset, in what order, and why the steps
are grouped the way they are. It is the conceptual companion to
[architecture.md](architecture.md) (layers) and [qc-system.md](qc-system.md) (quality metrics).

## The central idea

A processing script that works is not the same thing as a processing script that *should* have
been used. nmrForge therefore splits processing into four groups of decisions:

1. **Understand** - what experiment is this, and how was it sampled?
2. **Plan** - which processing route and which parameters are appropriate?
3. **Execute** - run NMRPipe with an explicit, inspectable script.
4. **Justify** - record the quality metrics, the resolved parameters and the warnings.

Everything after step 1 is a consequence of step 1. If the experiment or sampling classification
is wrong, every later choice is wrong in a way that quality metrics cannot reliably detect, which
is why classification is treated as a gate rather than a hint.

## Step 1 - data understanding

| Decision | Made from | Where |
| --- | --- | --- |
| Dimensionality | `PARMODE`, then presence of `acqu2s`/`acqu3s` | `core/data/bruker_reader.py` |
| Dimension roles and nuclei | per-dimension `NUC1`, `TD`, `SW_h`, `SFO1`, `O1` | `core/data/bruker_reader.py` |
| Acquisition mode per axis | `FnMODE` (`ft_kind_for`, `ft_alt_for`, `ft_neg_for`) | `core/experiment/acquisition_mode_detector.py` |
| Experiment type | pulse program, nucleus combination per dimension, dimension order, acquisition parameters | `core/experiment/experiment_classifier.py` + `presets/*.yaml` |
| Sampling mode | `nuslist`, pulse program, point counts, actual data length, acquisition parameters | `core/experiment/sampling_detector.py` |

Two rules matter here:

- **Filenames are not evidence.** Classification uses the pulse program and the physical
  parameters, not the directory or file name.
- **Contradictions become `uncertain`, not a guess.** If the metadata says NUS but the sampling
  table actually covers the complete grid, nmrForge says so and refuses to process, because a
  wrong uniform/NUS decision silently changes what every parameter means.

`presets/*.yaml` is the only data source for experiment templates. A template declares the
expected nucleus combination, whether the experiment is phase-sensitive or magnitude, and
processing hints such as whether automatic phase search applies.

## Step 2 - planning

The plan is a small dependency graph (`core/planning/dependency_graph.py`) of nodes whose status
is one of `PENDING`, `READY`, `RUNNING`, `DONE`, `FAILED`, `OUTDATED`. Nodes are keyed by their
inputs, so:

- an expensive node (a SMILE reconstruction, say) is not recomputed when nothing it depends on
  changed;
- when something *did* change, everything downstream is marked `OUTDATED` rather than silently
  reused.

This is where the "expensive reconstruction is separated from ordinary parameter optimisation"
principle lives: NUS reconstruction is its own node, and parameter sweeps over cheap parameters
do not force a re-reconstruction.

## Step 3 - execution order

The order is fixed, and it is not arbitrary:

```text
1  FID-level diagnostics            (before anything else; any repair is logged)
2  baseline optimisation            (must precede window optimisation)
2.1 re-render with the chosen baseline
2.5 direct-dimension window optimisation
3  indirect-dimension window optimisation
4  zero filling / final sizes
5  phase correction                 (automatic search, or the manual values)
6  final run                        (the script that actually produces the spectrum)
```

Why the constraints exist:

- **Diagnostics first.** Bad points, non-finite values and anomalous traces change every metric
  measured afterwards, so they are found and reported before optimisation starts.
- **Baseline before windows.** Window-function scoring uses line width, S/N and lineshape. A
  rolling baseline distorts those scores, so it has to be removed first. (This ordering was
  learned the hard way; it is recorded in `docs/development.md`.)
- **Windows before phase search.** Phase quality is scored on peak windows, which depend on the
  window function applied. Optimising the window after choosing phases would invalidate the
  choice.
- **Zero filling last but one.** Zero filling changes the digital resolution and therefore what
  "sub-grid peak position" means; it must be settled before peak localisation, and it influences
  the cost of the phase search.

Both the direct and the indirect dimensions are optimised for real. The window candidate pool
includes "no window", because a natural-decay axis should legitimately end up unwindowed; axes
that are truncated in acquisition prefer mild windows; indirect dimensions are scored with an
explicit resolution-retention factor.

## Step 4 - what is recorded

Each run writes a `WorkflowRun` record (`core/project/models.py`) with the run id, input
references, resolved parameters, referenced scripts, outputs, software version, external tool
versions (`core/version.py`), timestamps and warnings. Scripts and the spectra they produced are
kept, so a result can be re-derived rather than merely believed.

The scripting API writes a comparable record set into the study directory
(`manifest.json`, `runs.json`) - see
[external-api/06-outputs-and-records.md](external-api/06-outputs-and-records.md).

## Parameter resolution

Parameters arrive from several places, and they are merged in a defined order rather than by
luck. Reading the precedence from lowest to highest priority:

1. experiment template defaults (`presets/*.yaml`);
2. `config/nmrforge.yaml`, plus `config/nmrforge.local.yaml` for machine-local overrides;
3. the reference run's effective parameters (for parameter-sweep studies);
4. batch-level overrides;
5. per-combination overrides (an empty cell in a combination table means "not specified",
   not "set to empty").

The resolved set is recorded per run, together with which source each value came from. A
parameter that was requested but could not be honoured is reported as a warning rather than
quietly ignored - for example, writing window sub-parameters for an axis whose window type is
`none` is an error, not a no-op.

## Dimensionality and the four processing paths

nmrForge treats these as four distinct paths, and a change to processing behaviour is expected to
apply to all four unless the exception is justified and documented:

| Path | Reconstruction |
| --- | --- |
| 2D uniform | conventional FT |
| 2D NUS | SMILE |
| 3D uniform | conventional FT |
| 3D NUS | SMILE |

Path-specific differences that are legitimately allowed (for example SMILE requiring a fixed
apodisation on the direct dimension, or the 3D slice streaming) are documented in
`docs/development.md` with their reasons.

## Automation boundaries

nmrForge automates what it can verify and reports what it cannot:

- automatic phase search is skipped for magnitude spectra, where it is meaningless;
- window candidates are filtered by a resolution criterion rather than always taking the
  numerically highest score;
- a reconstruction that fails inside SMILE is a failed run, never a success with a wrong
  spectrum;
- bad-point *repair* is optional and always logged; bad-point *detection* is unconditional.