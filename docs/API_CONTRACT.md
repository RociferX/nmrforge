# Shared interfaces and API contract

Software **1.0.5** · Python/CLI API **1.1.1** · Project schema **1.4**.

## 1. Acquisition data model

[`core/data/internal_data_model.py`](../core/data/internal_data_model.py) defines the records
passed between readers, planning, workflows and the backend.

| Record | Fields and meaning |
| --- | --- |
| `Dimension` | `logical_axis`, `nucleus`, `sf` (MHz), resolved `sw` (Hz), raw `sw_ppm` and `sw_hz_raw`, `sw_source`, `sw_note`, `o1` (Hz), `o1p` (ppm), `td`, `ft_size`, `acquisition_mode`, `axis_direction`, `role` |
| `Sampling` | `mode` (`uniform`, `nus`, `uncertain`), `nus_list`, `sampling_fraction`, `schedule_type`, `confidence`, `evidence`, `schedule_file`, `schedule_source` |
| `ExperimentType` | `name`, `confidence`, `evidence` |
| `Experiment` | `dataset_id`, `source_path`, `ndim`, `acquisition_order`, `dimensions`, `sampling`, `experiment_type`, `acquisition_parameters`, `processing_state`, ordered `segments` |

`Experiment.direct_dimension` returns the dimension whose `role` is `direct`. Logical axes are
not array positions: Bruker acquisition order, quadrature mode, NMRPipe storage order and nucleus
identity must be resolved together. `td` is acquisition metadata, not an unconditional complex-point count.

[`resolve_sweep_width`](../core/data/bruker_reader.py) compares `SW_h` with `SW × SFO1`.
It selects `SW_h` when the difference is at most 1% of the ppm-derived width, otherwise the ppm-derived value; when only one convention is
available it uses that value. The adopted value, source and explanation remain separate from the
raw fields. Non-finite values are treated as missing. Explicit calibration overrides are audited
by logical axis and applied during reference/FID construction.

NUS input uses the standard `nuslist` or the file explicitly named by `acqus.NUSLIST`.
The schedule determines placement, including a full but reordered grid. A nominal sampling
percentage is not enough to reconstruct missing schedule positions. Trailing storage padding
does not imply missing increments.

## 2. Project model and persistence

[`core/project/models.py`](../core/project/models.py) defines `ProjectInfo`, `ExperimentEntry`,
`DataEntry`, `DataGroupEntry` and `WorkflowRun`.

- `ProjectInfo` stores schema, title/metadata, directory mappings, experiments, samples and runs.
- `ExperimentEntry.data` contains independently imported datasets; `groups` contains ordered
  `data_ids` for group operations.
- `DataEntry` records the source, project raw copy, ordered segments, metadata/FID/spectrum paths,
  checksums, status and trash state. Its processing states are `imported`, `fid_ready`, `processed`.
- `WorkflowRun` records `run_id`, experiment/workflow identity, inputs, scripts, requested/resolved
  parameters, outputs, software/tool versions, start/end times, status/message, snapshots and decisions.

[`ProjectManager`](../core/project/manager.py) creates/opens/saves projects, imports datasets,
registers products, manages groups, starts/finishes runs and handles recoverable deletion.
Whole JSON state uses temporary-file replacement. Append-only logs and audit streams retain
individual events rather than replacing the whole stream.

## 3. Processing backend protocol

[`ProcessingBackend`](../backend/base.py) is a runtime-checkable protocol. `BackendCapabilities`
exposes `provider`, `supports_nus`, `supports_phase_optimization` and `features`.

```python
health_check() -> dict[str, Any]
process(experiment, plan, *, params=None, progress=None) -> dict[str, Any]
convert_to_fid(experiment, data_dir, progress=None, fid_com_overrides=None) -> dict[str, Any]
reconstruct_nus(experiment, params=None, progress=None) -> dict[str, Any]
```

`progress` is an optional `Callable[[str], None]`. Conversion produces NMRPipe time-domain
input independently of spectrum generation. `process` executes a `ProcessingPlan`; `reconstruct_nus`
executes the NUS route. `NMRPipeBackend` implements these methods through script generation and
external execution; the GUI does not implement NMRPipe command syntax. See
[Backend architecture](backend/architecture.md) for script/runtime responsibilities.

## 4. Spectrum and axis interfaces

[`SpectrumAxis`](../viewer/spectrum.py) stores `label`, `size`, `sw_hz`, `obs_mhz`,
`carrier_ppm`, and optional `orig_hz`. `ppm`, `index_at`, `index_at_f`, `ppm_at`, and `ppm_at_f`
convert between array indices and chemical shift. A zero `orig_hz` is valid; only `None` means missing.
With ORIG present, the axis coordinate is:

```text
ppm[i] = orig_hz / obs_mhz + (size - 1 - i) * sw_hz / (size * obs_mhz)
```

`Spectrum` stores a 2D array, axes and metadata; `x_axis` is `axes[1]`, `y_axis` is `axes[0]`.
`Spectrum1D` reads 1D spectra or FIDs. `Spectrum3D` supports full arrays and lazy plane access,
`slice(axis_idx, index)`, `project(axis_idx, mode)`, plane blocks/cache and noise estimates.
NMRPipe logical identity follows `FDDIMORDER`, including repeated nuclei. A nucleus label alone
does not identify a unique axis. Plane views keep the identity and calibration of their remaining axes.

## 5. Desktop processing adapter

[`ProcessingController`](../gui/processing.py) binds a `ProjectManager` to workflow calls.
It imports datasets, generates FIDs/spectra, runs edited scripts, picks peaks and manages supported
SMILE optimisation/rerun operations. Calls carry `exp_id` and `data_id`, so status, products and logs
remain associated with their original target even if the selection changes.

Conversion and spectrum execution delegate to `workflow.stepwise`; manual execution delegates to
`workflow.manual`. The controller records successful steps and script snapshots. Worker signals carry
results to the GUI thread; rendering and widget changes stay there. See [GUI architecture](gui/architecture.md).

## 6. Plans and parameter ownership

[`ProcessingPlan`](../core/planning/processing_plan.py) contains `experiment_id`, a `ProcessingDag`,
`method_choices`, `rationale`, and `confidence`. The DAG supplies dependency order; backend execution
maps resolved parameters to commands.

- Acquisition/calibration settings determine the converted FID; spectrum parameters operate on that FID.
- Requested parameters and actual applied values are recorded separately. Automatic values must not
  be reported as if they were explicitly requested.
- GUI controls and API inputs use shared processing operations, while their orchestration and
  persistence are separate entry points.

## 7. Run results and errors

Backend results are mappings describing success, products, messages and stage-specific diagnostics.
The conversion result has stable keys `success`, `fid_path`, `message`, `logs`. Workflow entry points
return the registered output path or raise an error; an unsuccessful external process is not a
successful processing result. Run status and warnings remain available alongside the resulting files.

CLI commands write structured JSON to stdout and logs to stderr. API exceptions distinguish input,
reference, processing and sweep failures; see [API reference](external-api/03-api-reference.md) and
[troubleshooting](external-api/10-troubleshooting.md).

## 8. Stepwise execution

### 8.1 Import

Import creates the dataset entry and project raw-data copy/metadata. Explicit segmented import
preserves ordered source identities and records the directories actually used for conversion.

### 8.2 Spectrum processing

`workflow.stepwise.generate_spectrum(manager, exp_id, data_id, backend, *, params, work_dir, progress)`
processes the converted FID. The default `phase_route="unified"` coordinates preview, phase
optimisation and final processing. `phase_route="none"` selects direct backend execution.
Uniform routes use conventional processing and NUS routes use SMILE reconstruction.

### 8.3 FID generation

`workflow.stepwise.generate_fid(manager, exp_id, data_id, backend, *, work_dir, progress, params)`
performs conversion and returns a registered FID path. Segment shifts are conversion-time
parameters. `fid_com_overrides` supports the manual conversion script route; output shape and
storage geometry remain governed by the input and backend conversion logic.

## 9. Workspace and dataset layout

[`WorkspaceManager`](../core/workspace.py) ensures/lists a workspace and creates, opens, renames
or deletes its projects. The default workspace is `~/NMRForgeWorkspace`.
`ProjectManager.data_base(exp_id, data_id)` resolves the dataset base; `data_dir` resolves its products.

```text
project/
  project.json
  <experiment_id>/<data_id>/
    metadata.json
    raw/
    process/
    spectra/
    peaks/
    figures/
    report/
    smile_optimized/       # when this operation is used
```

Directories are created as operations need them. Run snapshots and project-level directory mappings
are resolved by the manager, separately from the per-dataset tree. Group members retain separate dataset products.

## 10. Three-dimensional views

A full 3D result and a plane directory are both supported storage forms. `Spectrum3D` maps storage
axes from the header, reads/caches requested planes and supplies 2D slices. The GUI defaults to the
F3–F2 view with F1 fixed, and can retain a selected plane. Projections summarize a volume;
they do not establish independent 3D peak identities or resolve every overlap.

## 11. Public nmrforge_api contract

### 11.1 Workflow semantics

API **v1.1.1** is defined once by `nmrforge_api.session.API_VERSION`. A study has labelled conditions,
each with an independent reference spectrum/table. Reference construction imports/converts/merges
the specified sources and freezes the FID, schedule and conversion evidence.

Combination studies reuse that frozen FID read-only, execute explicit combinations/grids, and
independently detect/localise peaks for each workflow and condition. They do not reconvert or merge
as a fallback. Missing or changed frozen evidence requires reference rebuilding with `force=True`.
`segmented=True` explicitly enables an ordered list of two or more raw directories per condition.

### 11.2 Entry points and support

Public calls include session/dataset management, `build_reference`, `detect_and_localize`,
`run_reference_study`, `run_combination_study`, `run_parameter_study`, status/report and compatibility
functions. Exact signatures and return records are in the [API reference](external-api/03-api-reference.md).
The [CLI reference](external-api/04-cli-reference.md) documents `python -m nmrforge_api`.
The API builds 3D NUS references; 3D NUS combination execution raises `SweepError`.
Desktop batch operations are 2D-only; the [API support matrix](external-api/09-limitations-and-roadmap.md)
describes scripting combinations separately.

### 11.3 Parameters, identity and calibration

Common reference parameters may be overlaid per condition with `params_by_condition`.
Conversion calibration (`sweep_width_hz`, `carrier_ppm`, segment shifts) belongs to reference
construction. Combinations cannot change it. FT-neg/flip may be candidates; phase routes and
FT-alt remain locked. Changing FT-neg does not trigger automatic phase reoptimisation.

Peak IDs are local to one spectrum. Combination `reference_peak_id` and `assignment` remain empty.
The API does not match peaks across conditions or candidates, nor compute downstream statistical inference.

### 11.4 Peak table fields

The unified table has **38 columns**, in this exact order:

```text
workflow_id, condition, dataset, peak_id, reference_peak_id,
assignment, H_ppm, N_ppm, intensity, SNR,
detected, localization_method, localization_requested, fallback, fallback_reason,
failure_reason, fit_success, FWHM_H, FWHM_N, boundary_hit,
duplicate_localization, F1_ppm, F1_nucleus, FWHM_F1, F2_ppm,
F2_nucleus, FWHM_F2, F3_ppm, F3_nucleus, FWHM_F3,
cell_low_H, cell_high_H, cell_low_N, cell_high_N, cell_edge,
intensity_ratio_vs_picked, shift_vs_picked_H, shift_vs_picked_N
```

`localization_requested` records the request; `localization_method` records actual execution
(`parabolic` or `none`). Targeted localisation refines selected peaks without changing detection
or row IDs; other detected peaks retain integer-grid coordinates and uncomputed QC values as `NaN`.
`failure_reason` describes detection/numerical QC failure; `fallback_reason` describes actual fallback.
`fit_success` and equivalent FWHM describe the three-point calculation, not peak identity.
H/N aliases are populated only for unambiguous nuclei; complete F1/F2/F3 identity remains available.
See [output fields and record semantics](external-api/06-outputs-and-records.md).

### 11.5 Products and resume

Relative to the study root, studies write `study/study.json`, reference products,
`study/workflows/<id>/<condition>/` products, `study/records/manifest.json`,
`study/records/workflows.json`, per-run records and unified peak tables.
Run records retain requested/resolved parameters, conversion/script provenance, warnings and tool versions.
Resume checks workflow/condition inputs, parameters and reference evidence before reusing successful work.
CLI `--study` and `--reference` must resolve to the same root.

### 11.6 Compatibility metadata

`compat_manifest()` exposes the API/parameter/table contract and compatibility declaration.
`behavior_digest` hashes processing code/resources; `token_digest` removes comments/docstrings for
executable-code comparison. The declaration classifies changes as `same`, `additive`,
`behavior_changed` or `contract_changed`; affected steps guide recomputation. Golden-vector
spectrum/table hashes identify deterministic conformance outputs. See [development](development.md).
