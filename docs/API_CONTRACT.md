# API Contract(Shared Contract definition)

Interfaces and data structures that cross the GUI/backend boundary. Any modification starts as a
change proposal and has to be approved in review (process: see CONTRIBUTING.md); it is released on
both sides together.

## 1. Data model (core/data/internal_data_model.py)

```python
class SamplingMode(str, Enum): UNIFORM / NUS / UNCERTAIN
class AxisRole(str, Enum): DIRECT / INDIRECT

@dataclass Dimension: logical_axis, nucleus, sf, sw, o1, o1p, td, ft_size,
                     acquisition_mode, axis_direction, role
@dataclass Sampling: mode, nus_list, sampling_fraction, schedule_type, confidence, evidence
@dataclass ExperimentType: name, confidence, evidence
@dataclass Experiment:
    dataset_id, source_path, ndim, acquisition_order, dimensions, sampling,
    experiment_type, acquisition_parameters, processing_state, segments
    property direct_dimension
```

Source:Backend's `core/data/bruker_reader.read_dataset(path) -> Experiment`;
`read_segments(paths) -> Experiment`(Multiple segments merged).

## 2. Project Management(core/project/)

```python
ProjectManager:
    create_project(root, name, ...) / open_project(root) / save() / close()
    add_experiment(source, title, sample_id, segments, metadata) -> ExperimentEntry
    rename_experiment / delete_experiment(to the system trash: soft delete, recoverable, audit kept)
    delete_data(to the system trash: soft delete, recoverable, audit kept) / recover_trashed(restored in place automatically)
    infer_status(exp_id) -> ExperimentStatus(registered→imported→processed→picked→analyzed)
    add_sample(**fields) -> SampleEntry / delete_sample(reference-protected)
    add_history(action, fields) -> HistoryEntry
    start_run(experiment_id, workflow_ref, inputs, scripts, params) -> WorkflowRun
    finish_run(run_id, status, outputs, message)
    snapshot_run(run_id, scripts, params) -> snapshot directory
    run_dir(run_id) -> Path        # processing/<exp_id>/runs/<run_id>/
    run_log_path(run_id) -> Path   # the run.log in that directory (every run has one, Phase 22)
```

`ExperimentEntry`:id(exp_NNN)/title/source/status/metadata/imported_at/notes/
sample_id/segments.`WorkflowRun`:run_id(R-YYYYMMDD-NNN)/inputs(SHA-256)/
Params/outputs/snapshot_dir/status. JSON schema 1.1, atomic writing.

Run directory (`processing/<exp_id>/runs/<run_id>/`) contains `snapshot/` (script + parameter
snapshot) and `run.log` (a start/end pair plus the log recorded by level during the run; the
path can be deduced from run_id and is not part of the schema).

**Records and state are always written atomically (mandatory, 2026-09-20)**: every
JSON document that is written and read back as a whole (study state, `run.json`,
`workflow.json`, reference and record JSON, localisation attachments, the
compatibility-manifest export, the per-step phase/NUS parameter caches,
`*.quality.json` and so on) goes through `core.project.manager.atomic_write_text`
(a temporary file in the same directory plus `os.replace`) -- a reader, or a resumed
run, only ever sees the complete old version or the complete new one, never a
half-written record; append-only logs (`run.log`, `qc_audit.jsonl`) are not covered
by this. The change **does not alter the bytes**: the serialisation, the file names
and the fingerprints are all unchanged.

## 3. ProcessingBackend(backend/base.py)

```python
@dataclass BackendCapabilities: provider, supports_nus, supports_phase_optimization, features

class ProcessingBackend(Protocol):
    capabilities: BackendCapabilities
    def health_check(self) -> dict            # {"ok": bool, "version": ...}
    def process(self, experiment, plan) -> dict
    def reconstruct_nus(self, experiment, params) -> dict
```

Return dict stable keys: `success: bool`, `message: str`, `logs: list[str]`.
`spectrum_path: str | None`, `metrics: dict`.
Factory:`backend/factory.create_backend(config) -> ProcessingBackend`.
(provider: `nmrpipe` only, see `SUPPORTED_PROVIDERS`; the unimplemented `native` skeleton has been.
Deleted on 2026-09-12,STUB-013).

## 4. Spectrum display model (viewer/spectrum.py)

```python
@dataclass(frozen=True) SpectrumAxis:
    label, size, sw_hz, obs_mhz, carrier_ppm, orig_hz
    ppm: np.ndarray(index -> ppm, ORIG preferred, CAR as fallback)
    index_at(ppm) -> int / ppm_at(index) / ppm_at_f(value)

class Spectrum:
    data(2D float, shape F1,F2), axes[0]=F1(rows), axes[1]=F2(columns)
    x_axis / y_axis / max_intensity / estimate_noise(fraction)
    load_from_ft2(path, labels=("F1","F2")) -> Spectrum
```

The implementation is located on the GUI side (viewer), but the structure is a contract; Backend must ensure that when outputting ft2.
The header FDF1*/FDF2*(SW/OBS/CAR/ORIG) is correctly parsed by this model.
For 3D extensions see §10 contract v1.4(Spectrum3D read/slice/projection).

## 5. ProcessingController(gui/processing.py, implementation attribute GUI)

```python
class ProcessingController:
    def generate_fid(self, data, exp_id=None, data_id=None, progress=None) -> str
        # via workflow.stepwise.generate_fid -> backend.convert_to_fid
    def generate_spectrum(self, data, exp_id=None, data_id=None, progress=None,
                          params=None) -> str
        # via stepwise.generate_spectrum -> phase_routes.unified_route (unified phase optimisation)
    def manual_fid_com(...) / run_manual_fid_com(...) / manual_scripts(...) /
        run_manual_spectrum(...)   # manual path (workflow/manual, implemented)
    def optimize_smile(self, data, exp_id=None, data_id=None,
                       progress=None, grid_size=None, rank_mode=None) -> str
        # optional SMILE optimisation (2D NUS only): scans a parameter grid and discards each
        # candidate spectrum once it has been scored; the products are a ranking table (CSV/JSON) plus the top three scripts, and the active spectrum is left alone (records a smile_optimize run)
    def rerun_smile_rank1(self, exp_id, data_id, progress=None) -> str
        # reruns the final spectrum with the rank-1 script and adopts it (records a smile_optimize_rank1 run)
    def pick_peaks(self, data, exp_id=None, data_id=None, *,
                   sigma_multiplier=None, ref_peaks=None, ref_nuclei=None,
                   tolerance_ppm=None) -> dict
        # reference mode (0.2.199-patch29dl): ref_peaks/ref_nuclei/tolerance_ppm are optional,
        # only peaks that match the reference peak table by nucleus are kept (a 2D reference matches every nucleus; a 3D+2D reference leaves the third dimension free)
```

Automated fixed move `stepwise.generate_fid/generate_spectrum`(internal create_backend +.
Unified phase route), GUI page shall not bypass this controller and directly call Backend.

## 6. Parameter/result convention

- Process parameter unified dict keys: `zero_fill`, `sampling`(ft_neg/ft_neg_f1/ft_neg_f2/
  flip_f1/flip_f2/ft_alt/auto_phase, consumed by script generation since 0.2.67: `ft_neg` is
  **global**, `ft_neg_f1`/`ft_neg_f2` are **per-axis**, and all three are tri-state -
  None=follow the acquisition mode / the automatic rule, True=**apply** `FT -neg`,
  False=**do not apply**; `ft_alt` True=automatic from the acquisition mode/False=force off;
  `auto_phase` False turns the direct-dimension automatic phase off).
  **Semantics of the per-axis keys (confirmed by the user by name on 2026-09-25)**: `ft_neg_f1`/
  `ft_neg_f2` **directly decide whether that axis gets** `-neg` (**absolute**); they are **not**
  "the result of flipping/inverting the automatic rule" - the same `True` means "apply" whether
  the automatic rule is on or off; `flip_f1`/`flip_f2` are the **legacy compatibility aliases**
  for these two keys (same meaning; new code should use `ft_neg_f*`). Precedence:
  **`ft_neg` (global) > `ft_neg_f*` (per-axis) > the automatic rule**; the per-axis keys take
  effect only when `ft_neg` is None (or absent).
  **The `-neg` decision (user, 2026-09-25, second and final round)**: the automatic rule is
  **on by default** (`AUTO_NEG_JUDGEMENT=True`), and it is the simple rule with the **lowest
  probability of error** (canonical `-N` / solved Layer A ⇒ take it; **the 3D NMRPipe `y`
  dimension (identified through `AQSEQ`, not blindly the logical F2) + the States family
  (`FnMODE` 2/3/4/5) ⇒ apply `-neg`**; the 3D `z` dimension, and the E/A family (QF family)
  agreeing with `FnMODE`, ⇒ do not apply; everything else - 2D States, `FnMODE` missing,
  `AQSEQ` that cannot tell y from z, **a pulse-program family contradicting `FnMODE`** ⇒
  **do not apply + the same reminder in all three places** (log / report / import), i.e.
  `ask_user`: "hand it to the user" - neither guess nor silently decide "do not apply" - and let
  the user settle it with the "indirect-dimension flip" control on the spectrum step).
  The spectrum step's "indirect-dimension flip" control edits the **already generated final
  script** (it only touches the indirect-dimension FT line; the control itself is a "command",
  and the panel first works out the **explicit target state** from the current final script and
  then sends it out); when building a reference, API/CLI uses
  `params={"sampling": {"ft_neg_f1"|"ft_neg_f2": true|false}}` (the alias `flip_f*` works as
  well);
  the keys that affect sign/direction (`ft_neg`/`ft_neg_f1`/`ft_neg_f2`/`flip_f1`/`flip_f2`/
  `ft_alt`) have a **single source**
  (`core.experiment.acquisition_mode_detector.sign_sampling_flags`), and the derived runs while
  the reference is built (per-axis phase preview / joint evaluation spectra) and the final run
  **must use the same sign convention**;
  these keys **must not** be used as combination sweep axes (`plan_sweep` raises an error; the
  same locked keys as `sampling.auto_phase`);
  `ReferenceSpectrum.sampling_flags` is a **derived read-only** attribute (taken from `params`),
  recorded together with `reference.json`, the reference record and `run.json`'s
  `parameters_resolved.sampling.flags`
  (`flags_source: reference(locked)`); the automatic rule's conclusion is recorded per
  dimension in `mode_symbol.dims` of `*.fid.conversion.json`
  (`neg_decision`/`neg_basis`/`neg_reason`/`neg_applied`);
- Process parameter unified dict keys (the rest): `baseline`(per-dimension baseline correction,
  see below), `stages`(list.
  id/tool/macro/params/param_docs);
- Baseline correction `baseline` key (G2B-007):
  `{"enabled": true, "mode": "auto"|"order", "order": N,
   "axes": "all" | ["F1", "F2"(, "F3")]}`;mode=auto →
  `POLY -auto`,mode=order -> `POLY -ord N`,enabled=false does not output;
  Default full-dimensional auto(aligned with manual xy.com);
- SMILE parameter:`nSigma/thresh/xQ3/scaling/report`, experience binning
  (≤20%: 5/0.95;20–30%: 6/0.90;30–40%: 7/0.85);
- Phase: complex data.fid direct dimension p1 consensus writing script PS; final spectrum (real data) does not do post-phase adjustment;
- Peak table: peak file is Poky/Sparky `.list` (saved by export_peaks_poky written
  Peaks/<exp>-<data>.list; old CSV compatible with reading). Internal field number Peak_ID.
  2D `Peak_ID,H_shift,N_shift,Intensity,SN,label`;3D plus.
  F1/F2/F3_shift;`.list` Format `Assignment w1 w2 [w3] Data Height.
  Volume`(2D w1=15N/w2=1H;3D according to external convention w1=15N/w2=13C/w3=1H.
  0.2.199-patch29dk, user:.list and peak table display are consistent with the external, internally according to F1/F2/F3 logic.
  Interpretation, Export/Introduction nuclei do external w column ↔ internal F column substitution; unnamed.
  `?-?`/`?-?-?`); Import reverse analysis and replace the peak table association (do not overwrite file).
  For implementation, see core/peaks/peak_table.py(G2B-005).

## 7. Change process

1. The requester writes a change proposal (process: see CONTRIBUTING.md);
2. the interface and its compatibility are reviewed;
3. After approval, the contract file is implemented and the version number of this file is updated (top);
4. both sides (desktop application and backend) are updated and tested together before release.

## 8. Contract v1.2 draft (Experiment -> Data level + step-by-step processing, to be implemented)

Status: draft (2026-08-12). It is implemented together with the stepwise-processing proposal
`gui-to-backend/002-stepwise-processing.md`.

### 8.1 Data model change (core/project/,schema 1.1 -> 1.2)

Level: Project -> Experiment (can be blank) -> Data (can be in multiple groups, importing data is an action under the experiment).

```python
@dataclass ExperimentEntry:  # changed
    id: str                       # exp_001(kept)
    title: str
    status: str                   # registered(empty) / imported(has data) / ...
    sample_id: str
    notes: str
    data: list[DataEntry]         # new: 0..n datasets
    metadata: dict
    created_at: str
    # top-level source/segments/imported_at removed (moved into DataEntry)

@dataclass DataEntry:             # new
    id: str                       # d_001
    source: str                   # external Bruker dataset directory (on import)
    raw_dir: str                  # copy inside the project: raw/<exp_id>/<data_id>/
    segments: list[str]
    status: str                   # imported / fid_ready / processed
    imported_at: str
    metadata_path: str            # metadata/<exp_id>-<data_id>.json
    fid_path: str                 # after the FID is generated (empty string = not generated)
    spectrum_path: str            # after the spectrum is generated (empty string = not generated)
    checksums: dict[str, str]
```

Migration rules: When reading project.json of schema 1.1, replace the old ExperimentEntry.
Source/segments/imported_at is migrated to data[0] and marked `migrated_from_1_1: true`.

### 8.2 ProjectManager changes(core/project/)

```python
create_experiment(title="", sample_id="") -> ExperimentEntry   # creates an empty experiment
import_data(exp_id, source, segments=None, title="") -> DataEntry
    # reads the parameters + links/copies raw/<exp_id>/<data_id>/ (G2B-009: hard link -> symlink ->
    # copy as fallback) + writes metadata + imports a WorkflowRun
    # no FID and no spectrum are generated
set_data_fid(data_id, fid_path)                               # registered after the FID is generated
set_data_spectrum(data_id, spectrum_path)                     # registered after the spectrum is generated
infer_status(exp_id)                                          # aggregated over the datasets
```

Compatible with:`add_experiment(source=...)` Reserved as a convenient entry point for "Import first data".
Internally equivalent to create_experiment + import_data.

### 8.3 Step-by-step processing (Backend + ProcessingController)

```python
# gui/processing.py ProcessingController (the implementation is GUI-owned, the contract is shared)
import_data(entry, source) -> DataEntry            # reads the parameters + copies, returns the data entry
generate_fid(data) -> str                          # calls the backend conversion, returns the fid path
generate_spectrum(data) -> str                     # calls the backend processing (including NUS reconstruction), returns the spectrum path

# backend/base.py ProcessingBackend(contract)
def convert_to_fid(self, experiment, data_dir) -> dict
    # returns {"success", "fid_path", "message", "logs"}
def process(self, experiment, plan) -> dict        # unchanged; decides NUS -> reconstruction internally
```

Process semantics:
- Import data = read-only experimental parameter + copy necessary files to raw, without triggering any processing;
- Generate FID = bruker -AUTO/fid.com transformation (existing backend/bruker_workflow logic);
- Generate spectrum (formerly "data processing") = process, automatically includes SMILE reconstruction, no separate step is required;
- GUI does not expose the separate SMILE reconstruction button; SMILE parameter optimisation is left as optional post-processing

### 8.4 Product naming

- Raw Link/copy:raw/<exp_id>/<data_id>/(G2B-009: Default hard link -> symbolic link -> copy fallback)
- metadata:metadata/<exp_id>-<data_id>.json
- Fid/intermediate product:process/ is prefixed with <data_id> (d_001.fid, d_001_nus.com
  D_001_preview_*.ft3, d_001_finalize.com, etc.; starting from 2026-08-19, renaming will not affect).
- Spectrum:spectra/<data_id>.ft2|ft3;3D projection spectra/<data_id>_proj_F{1,2,3}.ft2

### 8.5 GUI Tree level

Project (right click: delete project) -> Experiment (right click: Import data/Rename/delete) ->.
Data (right click: generate FID/generate spectrum / open directory / delete). The tree column width must be readable.
(Minimum column width + adaptive, prohibit display of only initial letters).

## 9. Contract v1.3(workspace level + data directory level)

Status: approved (2026-08-12; Proposal G2B-003).
Implementation: Backend(core/workspace.py + core/project schema 1.3).

### 9.1 WorkspaceManager(core/workspace.py,Shared)

```python
class WorkspaceManager:
    def __init__(self, root: Path | str | None = None)
        # default ~/NMRForgeWorkspace (same on Windows and Linux)
    def ensure(self) -> Path                    # creates the workspace idempotently
    def list_projects(self) -> list[Path]       # projects that contain project.json
    def create_project(self, name, **kwargs) -> ProjectManager
        # workspace/<name>/; an illegal or duplicate name raises WorkspaceError
    def open_project(self, name_or_path) -> ProjectManager
```

### 9.2 data directory hierarchy(ProjectManager,schema 1.3)

Project -> <exp_id>/ -> <data_id>/ -> {raw, process, spectra, peaks.
figures, report, metadata.json}

- Raw/ imported data (G2B-009: link type, default hard link -> symbolic link -> copy fallback)
- Process/ fid and processing intermediate products
- Spectra/ final spectrum (ft2/ft3)
- Peaks/ peak table Poky/Sparky.list (old CSV is only compatible with reading, 0.2.199-patch29ar)
- Figures/figures
- report/      logs and historical reports (today the only producer is the single-dataset/group
              log.txt; the statistics report page and the Report panel were removed on
              2026-09-12, REPORT-008).
- Metadata.json Data metadata

DataEntry path convention (relative to the project root): raw_dir = <exp_id>/<data_id>/raw.
Metadata_path = <exp_id>/<data_id>/metadata.json,fid_path in.
Within process/, spectrum_path is within spectra/.

Migrate/compatible:
- Schema 1.1/1.2 is automatically migrated to 1.3 when the project is opened (old source/segments ->
  Data[0]), the old flat product (project root raw/metadata/spectra) remains readable.
  Infer_status is compatible with old and new layouts when removed; old dir_path is retained as a compatibility layer;
- No physical migration is performed (old files are not moved), New import/processing press 1.3 layout is placed on the disk

## 10. Contract v1.4(3D spectrum view:read/slice/projection)

Status: approved (2026-08-12; the implementation lives on the GUI side, the contract is shared).
Implementation: viewer/spectrum.py Spectrum3D(Shared, implementation attribute GUI)+ viewer/.
Spectrum3d_panel.py + SpectrumWindow/gui.spectrum_panel wiring.

### 10.1 Spectrum3D(viewer/spectrum.py)

```python
class Spectrum3D:
    data: np.ndarray            # shape (F1, F2, F3), float
    axes: list[SpectrumAxis]    # [F1, F2, F3], reusing the §4 SpectrumAxis
    source: Path | None
    @classmethod
    def load_from_ft3(cls, path, labels=("F1", "F2", "F3")) -> Spectrum3D
        # nmrglue reads the ft3; the complex data is reduced to its real part; the axes come from the FDF1/FDF2/FDF3 headers
        # (SW/OBS/CAR/ORIG as in §4); a single-file 3D stream (FDPIPEFLAG=1)
        # is read back with shape (F1,F2,F3), where F1=FDF3SIZE, F2=FDSPECNUM, F3=FDSIZE;
        # a non-stream single file is reshaped by the same convention.
    def slice(self, axis_idx: int, index: int) -> Spectrum
        # fixes index along axis_idx and returns a 2D Spectrum of the other two axes;
        # axis order: axis 0 -> (F2,F3); axis 1 -> (F1,F3); axis 2 -> (F1,F2).
    def project(self, axis_idx: int, mode: str = "max") -> Spectrum
        # maximum-intensity projection along axis_idx (MIP, mode="max"), or the sum
        # (mode="sum"); the axis order matches slice.
    def index_at(self, axis_idx: int, ppm: float) -> int
        # locates an index by ppm along axis_idx (sliders position by ppm).
```

### 10.2 Viewer behaviour (viewer/app.py + gui/spectrum_panel.py)

- Open.ft3 to enter 3D mode: select the viewing plane (such as F1-F2), and the third axis is the slice axis;
- 3D mode control (viewer/spectrum3d_panel.py): View plane selection
  (F1-F2 / F1-F3 / F2-F3), third-axis slice slider (ppm display), projection mode.
  Switch(MIP/Sum);
- Slice/Projection products reuse existing SpectrumViewer/ContourLayer Draw a 2D plane
  (positive black and negative red, Frame selection zoom/Pan/Scroll wheels are retained);
- Both SpectrumWindow and GUI spectrum panels support.ft3 (file filter, drag-and-drop, double-click)
  Automatically enter 2D/3D mode according to dimension number;
- The 3D peak table columns (F1/F2/F3_shift) are mapped according to the current slice plane axis label, and the linkage is not affected

## 11. External interface contract: `nmrforge_api` (API_VERSION = "1.1", 2026-10-03)

The public Python and CLI interface is versioned independently from the desktop UI. Its purpose is to
run processing studies and archive provenance, not to perform downstream statistical inference or
make scientific conclusions. Current public details are also summarized in the
[external API guide](external-api/README.md).

### 11.1 Workflow semantics

```text
Bruker datasets and conditions
    ↓
Reference workflow: processed spectrum + executed script + peak identities
    ↓
User-defined parameter rows or axes (one workflow_id per combination)
    ↓
Candidate processing per workflow × condition
    ↓
Independent peak detection + three-point parabolic localization
    ↓
Unified peak table + records, hashes, versions, logs, status, and warnings
```

- Each condition has its own reference and effective parameter base. A workflow row is shared
  across conditions; each condition is processed against its own reference.
- The reference is a reproducible baseline for parameter perturbation, not a claim of global
  optimality.
- Each combination selects peaks independently on its candidate spectrum with the reference-locked
  threshold. Combination-table `reference_peak_id` and `assignment` remain blank; matching is
  downstream.
- Statistical summaries, significance testing, assignment, and scientific conclusions are outside
  this interface.
- Current v1.1 contract: each condition has an independent reference and reference peak table;
  every combination independently detects/localizes peaks into its own table. The API does not
  match peaks across conditions or between reference and combination spectra, and does no statistics.

Sampling is classified from supported metadata, grid coverage, and schedule order. Only a standard
`nuslist` or a file explicitly named by `acqus.NUSLIST` is used. A complete schedule in standard
order may take the uniform route; complete coverage in a different order still requires
schedule-based placement. Explicit NUS with a missing or unrecoverable schedule is rejected at
import. Trailing block padding is not an unsampled point. 2D NUS combinations use SMILE; 3D NUS
currently supports reference construction only.

### 11.2 Public entry points

```python
run_reference_study(root, dataset=None, *, datasets=None, params=None,
                    params_by_condition=None, phase_route=None, peaks=None, direct_range=None,
                    carrier_ppm=None,
                    sigma_multiplier=None, max_peaks=0,
                    force=False, backend=None, write=True, progress=None) -> ReferenceResult

run_combination_study(reference, *, combos=None, axes=None, max_runs=256,
                      localization="parabolic", localize_peaks=None,
                      edge_margin_ppm=None, direct_range=None,
                      allow_ext_override=False, resume=True,
                      backend=None, write=True, progress=None) -> StudyResult

run_parameter_study(root, dataset=None, *, datasets=None, combos=None, axes=None,
                     name="", params=None, params_by_condition=None, phase_route=None, peaks=None,
                     carrier_ppm=None,
                     sigma_multiplier=None, max_peaks=0, max_runs=256,
                     localization="parabolic", localize_peaks=None,
                     direct_range=None, allow_ext_override=False,
                     resume=True, backend=None, write=True, progress=None)

open_study / add_dataset / dataset_info
build_reference / load_reference / load_references
pick_reference_peaks / set_reference_peaks / ensure_reference_peaks
build_reference_peak_tables / rebuild_reference_peak_tables
plan_sweep / run_sweep / load_plan / load_runs / load_workflows
detect_and_localize / measure_peak_positions / read_reference_peaks
read_localization_targets / resolve_localization_targets
write_records / write_peak_table / read_peak_table / peak_table_rows
expand_grid / combos_from_rows / load_combo_table / write_combo_table
compat_manifest / compat_status / record_stamp / check_conformance
```

The API version is `API_VERSION = "1.1"` (current source contract as of 2026-10-03). The older
desktop/AppImage 1.0.2 is a separate build and does not include this contract. See the external API pages for exact signatures,
arguments, return structures, and errors. The command line is `python -m nmrforge_api` with
`init`, `reference`, `peaks`, `sweep`, `report`, `status`, and `compat` subcommands.

### 11.3 Reference, combination, and targeting rules

- Reference generation selects an identity table (automatically or from an external peak table),
  freezes its threshold, and writes one `reference_peak_table_parabolic.csv`.
- `params_by_condition` overlays common reference parameters per condition. FT-negation requests,
  resolved axis values, and actual script commands are separately audited.
- Reference reuse requires an exact normalized request match, including nested/dotted-equivalent
  parameters, `phase_route`, direct range, and carrier. The fingerprint is
  `reference.json.input_fingerprint` with schema `nmrforge_api.reference_input.v1`.
  Any mismatch, or a missing/invalid legacy fingerprint, raises `ReferenceError`; rebuild only
  with explicit `force=True` (CLI `--force`). Multi-condition requests preflight all conditions
  before starting the engine; they never partially rebuild. Resume uses schema
  `nmrforge_api.resume.v4`.
- Reference-only `carrier_ppm: Mapping[str, float] | None` is accepted by
  `build_reference`, `run_reference_study`, and `run_parameter_study`, or through common
  `params["carrier_ppm"]` / dotted `carrier_ppm.F1`. Explicit keyword values override common
  params per axis; `params_by_condition` then overrides per axis. Logical axes are F2=x/F1=y
  in 2D and F3=x/F2=y/F1=z in 3D. Values must be finite numbers (0 and negative are valid);
  bool, nonfinite values, empty mappings, unknown axes, and axes beyond the data dimensionality
  are rejected. Unspecified axes retain the selected conversion path's CAR; raw `acqus` is not
  edited. Carrier values are fingerprinted; changes require `force=True`. Sweeps inherit the
  reference carrier and reject carrier values in axes, combinations, or base overrides because
  they reuse the converted FID. CLI `reference` accepts repeatable `--carrier-ppm F1=120.0`.
- Explicit positive finite `sweep_width_hz` may override acquisition spectral width in reference
  construction by logical axis; the original and resolved values, source, and consistency ratio
  are recorded. A sweep reuses the converted FID and cannot change its spectral width.
- `sigma_multiplier` is selected while building the reference (default 35). Once frozen, a
  different threshold in a sweep is rejected; rebuild the reference to change it.
- Combination mode requires an explicit reference and exactly one of `combos` or `axes`.
  Rows execute in the supplied order; axes expand as a Cartesian product. The API does not invent
  a parameter design.
- `localization` accepts only `parabolic`. Removed Gaussian or mixed-method requests raise an
  explicit error; they are never silently substituted.
- `localize_peaks` or the method-independent `localization.targets` column can limit which
  detected peaks receive localization. Detection, row count, and per-spectrum `peak_id` numbering
  do not change. Unlisted peaks remain at their detection-stage coordinates and unrun localization
  fields are `NaN`, not failures.
- A target list may be a sequence of per-spectrum peak IDs or a CSV with `peak_id`. It can include
  a `condition` column, or be provided per condition. By default, a condition with no target rows
  fails before processing. `on_missing="all"` or `"none"` must be explicit to choose a different
  policy. Missing files, empty lists, missing columns, unknown conditions, or unknown peak IDs do
  not silently broaden the target set.
- Axial screening has no unconditional edge band. It requires compatible experiment/acquisition
  evidence and many narrow, aligned candidates at original spectrum edges. Internal peaks,
  isolated edge peaks, and uncertain cases are retained. `edge_margin_ppm` is a manual override
  distinct from automatic screening and is recorded separately.
- Peak height, threshold, SNR, and reference measurements share the global-median background
  convention. It does not modify the spectrum. Automatic sign selection follows experiment
  templates; phase-sensitive COSY/NOESY/ROESY preserve both signs. Explicit sign requests take
  precedence.
- Reference alignment does not modify spectra. Low-quality or ambiguous alignment is reported;
  it must not silently discard candidate peaks.

### 11.4 Peak table fields and stable records

The current v1.1 unified `PEAK_TABLE_COLUMNS` schema has 38 columns:

```text
workflow_id, condition, dataset,
peak_id, reference_peak_id, assignment,
H_ppm, N_ppm, intensity,
SNR, detected, localization_method, localization_requested,
fallback, fallback_reason, failure_reason,
fit_success, FWHM_H, FWHM_N,
boundary_hit, duplicate_localization,
F1_ppm, F1_nucleus, FWHM_F1,
F2_ppm, F2_nucleus, FWHM_F2,
F3_ppm, F3_nucleus, FWHM_F3,
cell_low_H, cell_high_H, cell_low_N, cell_high_N, cell_edge,
intensity_ratio_vs_picked, shift_vs_picked_H, shift_vs_picked_N
```

- `peak_id` is local to a single spectrum. `reference_peak_id` (`R0001`...) belongs to the
  reference identity table; combination tables leave reference identity and assignment blank.
- Reference tables may retain identity rows with `detected=false`; combination tables contain
  only detected peaks.
- `localization_requested` is the requested `parabolic` method; `localization_method` is the
  actual result (`parabolic` when localized, `none` when not detected or targeted localization
  was skipped). Unrun/skipped localization QC is NaN and `failure_reason` is empty. An attempted
  localization failure has an explicit `failure_reason` (for example
  `no_local_peak_above_threshold`); this is not a fallback. `fallback_reason` remains separate.
- `fit_success`, `FWHM_*`, and `boundary_hit` describe parabolic localization. Gaussian and
  `fit_rmse` are removed. H/N columns are compatibility aliases only when that nucleus is unique.
- `duplicate_localization` flags shared coordinates; it does not drop rows.
- `cell_low_H`, `cell_high_H`, `cell_low_N`, `cell_high_N`, and `cell_edge` are always NaN:
  joint multidimensional Voronoi ownership cannot be represented by per-axis boundaries. Physical
  search windows are recorded separately in `reference.peak_localization.search_windows.search_bounds_by_axis`
  (F-axis and closed low/high integer storage points). Candidate ownership conflicts use the
  separate `candidate_ownership_conflict` audit flag, not `cell_edge`.
- `intensity_ratio_vs_picked` and H/N shift deltas remain available when measured. Peak tables
  frozen under the old 29-, 27-, or 36-column contracts must be migrated/rebuilt; old API v0.2
  tables have no compatibility promise.
- Intensity is signed relative to the documented global-median background; SNR is the absolute
  height divided by robust noise. Missing or unrun localization metrics remain missing rather than
  being fabricated.

Each run records requested, used, and resolved parameters; reference and script/spectrum hashes;
tool/software versions; logs; status; and warning codes. Records distinguish requested values from
values actually applied. A failure or recommendation must not be reported as a completed
correction.

### 11.5 Product layout

```text
study/
  reference/<key>/
    reference.json
    process.com
    reference.list
    reference_peak_table_parabolic.csv
  workflows/W0001/
    workflow.json
    log.txt
    <condition>/
      process.com
      spectrum.ft2 or spectrum.ft3
      peak_table_parabolic.csv
      run.json
      log.txt
  records/
    manifest.json
    sweep_plan.json
    runs.json
    workflows.json
    measurement.json
    peak_table_parabolic.csv
```

The exact set of files depends on the operation and dimensionality. See
[Outputs and records](external-api/06-outputs-and-records.md) for field meanings and warning
codes.

A direct-dimension extraction range is given in ppm as `(high, low)`. In reference mode it is part
of the reference definition. In combination mode, an override that differs from the frozen range
is rejected unless `allow_ext_override=True`; accepted overrides are recorded as warnings.
Records retain the effective range and its source.

### 11.6 Compatibility and contract changes

`compat_manifest()` and `python -m nmrforge_api compat` report behaviour and contract fingerprints.
Compatibility levels are:

- `same`: no externally observable behaviour or contract change;
- `additive`: a new optional entry point or field that does not require downstream recomputation;
- `behavior_changed`: numerical or processing behaviour changed; name affected areas;
- `contract_changed`: output fields, columns, error codes, or public call contracts changed.

Do not claim `same` when behaviour or API tokens changed. Update the public documentation and
compatibility declaration together. The optional golden vector checks deterministic behaviour; it
is not a scientific validation benchmark.

To change this contract, update implementation, tests, API documentation, and compatibility
metadata together. Keep Qt/UI dependencies out of `nmrforge_api`. Do not overwrite the active
spectrum with a sweep candidate; candidates and records belong under the study's workflow tree.
