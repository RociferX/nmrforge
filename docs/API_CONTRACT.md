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

Run directory (`processing/<exp_id>/runs/<run_id>/`) contains `snapshot/`(script + parameter snapshot) and.
`run.log`(start/End two lines + log recorded by level during the run; the path can be deduced from run_id without entering the schema).

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

- Process parameter unified dict keys: `zero_fill`, `sampling`(ft_neg/ft_alt/flip_f1/
  Starting from auto_phase,0.2.67 script generates consumption: ft_neg None=Automatically according to the collection method/True=mandatory.
  FT -neg/False=Close;ft_alt True=Automatically according to the collection method/False=Force close;flip_f1.
  True, F1 axis FT -neg flip; auto_phase False, turn off direct dimension automatic phase).
  `baseline`(baseline correction for each dimension, see below), `stages`(list.
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

## 11. External interface contract:`nmrforge_api`(v0.2, specification updated on 2026-09-13)

Status: implemented. Source of specification: user 2026-09-13 "API specification update"; compliance ledger.
API_CONTRACT.md;External documents `docs/external-api/`.

Positioning: a **parameter-combination processing executor**. Input: raw NMR data + a user parameter
combination table. Output: traceable peak tables and processing records. The software performs
processing only and is **not** responsible for statistical inference or scientific conclusions.

### 11.1 Workflow semantics

```text
Raw data(A/B…)
    ↓  reference workflow (auto-optimised): 1 reference processing script + 2 reference peak tables
Reference workflow
    ↓  user parameter combination table: one workflow_id per row (W0001, W0002, ...)
User-defined workflow ensemble
    ↓  the reference script is the template: only the parameters named for that combination are replaced, then the processing runs automatically
Processed spectra (per workflow × per condition)
    ↓  every combination picks peaks independently on its own spectrum using the reference-locked threshold, then refines them with localization
Parabolic / Gaussian peak tables
    ↓
Complete provenance + QC (three parameter layers, script/spectrum hashes, full logs, versions, status)
```

- The reference is only used as a benchmark for subsequent parameter perturbations, and does not claim global optimality;
- Sampling routing: **actually fully sampled** data (labelled NUS but `nuslist` covers the whole
  grid, or a 2D `ser` covering the whole grid with no zero rows) is processed as **uniform**
  (SMILE is not run), and the effective sampling plus its evidence go into the reference/run
  records; real NUS goes through `reconstruct_nus`;
- Two conditions of data: the same workflow uses the same copy of `parameters_requested` for A/B, and outputs each
  Peak table(`A_raw -> W0037 -> A_peak_table`,`B_raw -> W0037 -> B_peak_table`);
- **Independent peak selection for combinations** (2026-09-14): each combination picks peaks on **its
  own candidate spectrum** with the reference-locked threshold, producing that combination's own
  complete peak table with `reference_peak_id`/`assignment` left blank. Matching against the
  reference peak table is done externally (downstream analysis); the threshold is determined in
  reference mode only and locked throughout (writing a threshold key in a combination table raises
  `SweepError`). The refinement method is chosen by `localization` (parabolic default / gaussian,
  2D only / both); combination mode has no `max_peaks`;
- Software final boundary: **do not add** statistical inference, significance testing or scientific
  conclusions (those are done by downstream analysis from the unified peak table). The σ/Δδ
  summary **does not enter the processing contract or the `records/` products**, but the code
  (`nmrforge_api/uncertainty.py`) is kept as a **test/detection aid**: the processing chain
  (study/sweep/records/CLI) does not call it.

### 11.2 Public (`nmrforge_api/__init__.py`,`API_VERSION = "0.2"`)

```python
# two modes (2026-09-14): reference mode builds the reference; combination mode must be given the reference explicitly
run_reference_study(root, dataset=None, *, datasets=None, params=None,
                    # params may carry reference_optimize(**testing/reproduction only**,
                    # forbidden for real experiments; see external-api/05 §5.10)
                    phase_route=None, peaks=None, sigma_multiplier=None,
                    max_peaks=0, localization_method="parabolic",
                    gaussian_roi_f1_ppm=None, gaussian_roi_f2_ppm=None,
                    backend=None, write=True, progress=None) -> ReferenceResult
run_combination_study(reference, *, combos=None, axes=None, max_runs=256,
                      localization="parabolic", edge_margin_ppm=None,
                      roi_f1_ppm=None, roi_f2_ppm=None, resume=True,
                      backend=None, write=True, progress=None) -> StudyResult
                      # window_pts/window_ppm/sign are kept but no longer used (compatibility)
parse_reference_spec(spec) -> ReferenceHandle
resolve_reference(spec, *, backend=None) -> (StudySession, DatasetRef, ReferenceSpectrum)
write_reference_records(session, references) -> dict[str, str]
# one-step convenience entry point (internally = reference mode + combination mode, passing the study root as the explicit reference)
run_parameter_study(root, dataset=None, *, datasets=None, axes=None, combos=None,
                    name="", params=None, phase_route=None, peaks=None,
                    sigma_multiplier=None, max_peaks=0, max_runs=256,
                    window_pts=None, window_ppm=None, sign="abs",
                    roi_f1_ppm=None, roi_f2_ppm=None,
                    localization="parabolic",        # combination-mode refinement method
                    localization_method="parabolic",
                    gaussian_roi_f1_ppm=None, gaussian_roi_f2_ppm=None,
                    resume=True, backend=None, write=True, progress=None)
open_study / add_dataset(session, source, condition="A") / dataset_info
build_reference / load_reference / load_references / set_reference_peaks
ensure_reference_peaks / build_reference_peak_tables /
rebuild_reference_peak_tables / pick_reference_peaks
measure_peak_positions / read_reference_peaks / window_points_by_axis
detect_and_localize        # combination-mode independent peak picking (rows: peak_id = index in this spectrum, reference_peak_id="")
plan_sweep / run_sweep / load_plan / load_runs / load_workflows
write_records / write_peak_table / read_peak_table / peak_table_rows
expand_grid / combos_from_rows / load_combo_table / write_combo_table
design_diagnostics / infer_axes / merge_overrides / sanitize_sweep_params
workflow_id_for / workflow_summary / reference_peak_id
```

CLI:`python -m nmrforge_api {init,reference,peaks,sweep(=workflows),report,status}`.

### 11.3 Stable return structure

- `DatasetRef`(exp_id/data_id/**condition**/ndim/nuclei/sampling/source/raw_dir);
- `ReferenceSpectrum`(condition, frozen spectrum and script path + SHA-256, valid parameter +
  `direct_phase` (PS for each axis, **actual results** of automatic phase identification) + reference peak identity table.
  (`reference.list` + SHA-256 + peak number + source auto|external|shared:<condition>)+.
  **Two reference peak tables** `reference_peak_table_parabolic.csv` /.
  `reference_peak_table_gaussian.csv`(path + SHA-256 + Number of lines/detected count)+.
  Positioning QC + version table); the reference records both ``software_version`` and
  ``software_commit`` (the latter from ``NMRFORGE_GIT_COMMIT`` or `git rev-parse`);
  ``direct_range`` = ``{ext_lo, ext_hi, unit, source}`` (``source`` in ``explicit`` / ``params`` / ``default``, P1-4, 2026-09-19; ``default`` = the caller gave no range and the backend/config default was used, with a ``warning``); ``rebuild_reference_peak_tables()`` restamps ``software_version``/``software_commit``/``tool_versions`` and refreshes the study-level aggregate ``records/reference.json`` (fixed 2026-09-19);
- `SweepPlan`(axes/combos/base_overrides/grid_sha256 with reference hash/design/
  diagnostics/`workflow_ids()`);
- `SweepRun`(one workflow x one condition):`workflow_id`, `condition`
  `parameters_requested`, `parameters_used`, `parameters_resolved`
  (phase `phase_mode`/`actual_p0`/`actual_p1`, SMILE actual `nsigma`/`thresh`.
  Spectral noise σ), `base_script`(reference script path + SHA-256), script /spectral path + SHA-256.
  `peak_tables`(path selected for refinement + SHA-256 + number of lines), `peak_localization`.
  `script_diff`(Refer to the difference between script vs this workflow script).
  (detected/rollback/hit boundary count), `window`(axis-by-axis physical width ↔ points conversion), `log_path`.
  (full log), `versions`(nmrforge/python/rely/NMRPipe/SMILE), `status`∈.
  {`success`, `success_with_warning`, `failed`}, `warnings`(code + count + peak);
- `StudyResult`(session/plan/references/runs/workflows/summary/records).

### 11.4 Peak table field (the two algorithms have the same structure, now 29 columns)

```text
workflow_id, condition, dataset,
peak_id, reference_peak_id, assignment,
H_ppm, N_ppm, intensity,
SNR, detected, localization_method,
localization_requested, fallback, fallback_reason,
fit_success, FWHM_H, FWHM_N,
fit_rmse, boundary_hit, duplicate_localization,
cell_low_H, cell_high_H, cell_low_N,
cell_high_N, cell_edge, intensity_ratio_vs_picked,
shift_vs_picked_H, shift_vs_picked_N
```

The eight new columns (P1-3, 2026-09-19) describe the **reference** table only,
i.e. the result of relocating records from the peak identity table:

- `cell_low_*` / `cell_high_*` (int): the effective search interval (the 1.5x linewidth
  window intersected with the exclusive cell), a **closed** interval in data-axis
  integer indices; with `exclusive_windows=False` (the historical wording) they hold the
  window bounds that wording actually used;
- `cell_edge` (bool): the extremum sits on the **exclusive-cell** edge (the neighbour's
  cell cut it short). It is orthogonal to `window_edge`, which tracks the physical window
  edge; with the historical wording it is always `false`;
- `intensity_ratio_vs_picked` (float): **|measured intensity| / |the identity
  table's Height|** (both sides in magnitude - a negative-peak `.list` carries a
  negative Height; fixed 2026-09-19); NaN when the Height is missing or zero. About 1
  means the record
  stopped on its own peak top, clearly above 1 means a shoulder of a stronger peak;
- `shift_vs_picked_H` / `shift_vs_picked_N` (float, ppm, sign = measured - picked, same
  axis and direction as `H_ppm`/`N_ppm`): the per-axis shift. The 15N ppm axis runs
  opposite to the data index, so do not read the sign backwards;
- **combination (workflow) tables write `NaN` in all eight columns**: the sweep path
  picks and localizes in one step, so there is no identity-then-relocate step and
  writing 1.0/0 would be fabricated information (the same rule as the gaussian-only
  columns in parabolic tables);
- `duplicate_localization` (bool, P2-5, 2026-09-19): true when the row shares its
  coordinates with another row of the same table (ppm to 1e-6); every row of a
  duplicated group is flagged and no row is dropped or removed from the peak set.
  Both the reference and the combination tables carry the marker: after the
  exclusive-cell fix the reference table can only collide when two records round to
  the same grid point, while the combination table still collides when the peak
  picker's sub-grid refinement pulls two neighbouring detections into one cell;
- the frozen record `reference.json.peak_localization.<method>` also carries the
  summaries `n_cell_edge`, `n_duplicate` (= rows minus unique coordinates) and the
  `n`/`median`/`max` of `intensity_ratio_vs_picked`; every
  `run.json.peak_localization.<method>` carries `n_duplicate` too, and a table with
  shared coordinates adds a `duplicate_localization` entry (code plus row count) to
  `run.json.warnings`.

- Column order = code block below = `nmrforge_api.peak_tables.PEAK_TABLE_COLUMNS`(only source
  `tests/test_api_docstrings.py` column-by-column comparison);
- `peak_id` is the peak number of this spectrum (this workflow x this condition);
- `localization_method` is the **actual** method, `localization_requested` is the **request** method;
  If the two are different, a rollback occurs. For the reason, see `fallback`/`fallback_reason`;
- `reference_peak_id`(R0001…) is established from the reference peak table (undetected peaks in the reference peak table are retained
  `detected=false` row); the combination mode selects peaks independently from 2026-09-14, and this column of the combination peak table is the same as.
  `assignment` left blank, and matching is done downstream;
- **Since P3-7 (2026-09-19) a parabolic table carries real QC as well**: `fit_success` /
  `FWHM_H` / `FWHM_N` / `boundary_hit` come from the three-point parabola (equivalent
  linewidth `FWHM = 2.3548 * sigma` with `sigma^2 = H/(2|a|)`; a vertex offset on the
  +/-0.5 point limit sets `boundary_hit`), so the two tables are directly comparable;
  only `fit_rmse` stays Gaussian-only (the three-point parabola is an exact solve) and
  is written as `NaN` in a parabolic table;
- Fitting failures and fallbacks (`fallback` / `fallback_reason` / `fit_success`) must be
  written peak by peak, never silently; a Gaussian failure or fallback writes
  `fit_success=false`, and only a missing key leaves `NaN`.
- Reference peak table coordinates are unique row by row: with the default
  `measure_peak_positions(exclusive_windows=True)` each reference peak's search region is
  truncated at the midpoints to its neighbours, so two reference records are never relocated
  onto the same grid point (fixed 2026-09-19; `False` reproduces the previous wording);
  records that round to the same grid point are the resolution limit and are recorded as-is.

### 11.5 Product structure (relative research root)

```text
study/
  reference/<exp>_<data>/   reference.json, process.com, reference.list,
                            reference_peak_table_{parabolic,gaussian}.csv
  workflows/W0001/
      workflow.json         combination-level record (three parameter layers / status / warnings / per-condition products / versions)
      log.txt               combination-level full log
      <condition A|B>/      process.com, spectrum.ft2, peak_table_*.csv of the selected method,
                            log.txt, run.json
  records/                  manifest.json, sweep_plan.json, runs.json,
                            workflows.json, measurement.json,
                            peak_table_*.csv of the selected method (long table)
```

### 11.6 Strong constraint (breaking it is considered breaking the contract)

1. Do not import Qt/gui; do not modify GUI status;
2. Do not replace project activity spectrum: candidate spectrum write only `study/workflows/`;
3. Within the same condition, fid is only converted once (refer to run); only the scanned parameters are allowed to be different between workflows.
   (phase is locked at the reference value by default, and the deviation is `phase_delta.<axis>.p0|p1`);
4. Use reference script as template: each condition is valid according to "its own reference parameter -> batch.
   `base_overrides` -> Combining the explicit key "generates `parameters_used`; must not copy the basis of other conditions;
5. Each workflow x and each condition must contain: complete script, unified peak table of the selected positioning method, complete log, parameter three layers.
   Version, status (three values) and warning;
6. Automatic parameter must record **actual result**(`actual_p0/actual_p1`, SMILE actual.
   `nsigma`/`thresh` and spectral noise σ),Gaussian fail/Fallbacks must be explicitly documented;
7. If a single condition fails, the entire round will not be interrupted: status `failed` + reason for placing the order;
8. **Do not do** statistics/significance/conclusion; do not automatically generate parameter space.
   (`combos=` is executed as is, `axes` is just a convenience entry);
9. Parameter is illegal error/warning: lock key error, certainty/Unknown key write `notes`;
10. Peak window/Gaussian ROI Defined by **physical width** (ppm), the number of points is converted according to the current spectral point distance during runtime.
    The conversion results must be kept on file (`run.json.window`, `records/measurement.json`).

To change the contract, you need to follow the Proposal process in §7; to add a new parameter axis, you do not need to change the contract (the period key is common).

## Targeted localization and the behaviour manifest (2026-09-19/20)

- **Targeted localization**: a combination table's `localization.targets` (= a CSV path;
  relative paths resolve against the combination table's directory), the CLI
  `--localize-peaks` / `--localize-peaks-gaussian` / `--localize-peaks-parabolic` and the API
  `localize_peaks=` let only the listed peaks take that method's refinement. **Detection, row
  count and `peak_id` numbering are unchanged**; unlisted peaks stay, positioned by the
  detection-stage three-point parabola, with that method's QC columns as `NaN` (not fitted, not
  a failure); a per-peak failure still records `fit_success=false` + `fallback_reason` and
  **never** re-fits another candidate; an empty target list / a missing file / a missing
  `peak_id` column / an unknown `peak_id` all raise (never a silent fallback to the whole
  spectrum). Recorded in `parameters_resolved.detection.localization_targets`
  (scope/source/path/sha256/n_targets/peak_ids) and in `peak_localization.<method>`'s
  `n_targeted`/`n_skipped`/`localization_scope`; no targets = the whole spectrum (default
  behaviour unchanged).
- **Per method** (2026-09-20): `localize_peaks` / `localization.targets` accept a mapping --
  `localize_peaks={"gaussian": csv}` or the key `localization.targets.gaussian` (method keys
  beat the method-independent one; `all`/`*` give a shared default; relative paths still
  resolve against the table directory). The record keeps the method-independent view at the top
  level and adds `by_method` detail (`scope` is `mixed` when methods differ).
- **Condition granularity** (2026-09-20): the target list may be written per (workflow,
  condition). The CSV may carry a `condition` column, and each condition then reads only its
  own rows with `peak_id` validated against **that condition's spectrum**; without the column
  one list serves the batch (recorded as `by_condition: "all"`). A condition with no rows fails
  **before processing** by default (`on_missing="all"|"none"` lets it through and is recorded),
  an unknown condition name raises, and an empty file / missing `peak_id` column keeps the
  existing errors. The mapping forms `{"A": "a.csv", "B": "b.csv"}`,
  `{"default": ..., "by_condition": {...}}` and the combination-table cell
  `"{A: a.csv, B: b.csv}"` are equivalent. The record gains `by_condition` (per-condition
  `peak_ids`/`n_targets`/`line_ranges`/source path + SHA-256); detection and `peak_id`
  numbering stay, and `n_targeted`/`n_skipped` stay per run.
- **Behaviour fingerprint and change levels** (2026-09-20): `compat_manifest()` / the CLI
  `python -m nmrforge_api compat [--out FILE] [--golden]` publish `nmrforge_api.compat.v1` --
  `behavior_digest` (content SHA-256 over `core/`+`backend/`+`workflow/`+`nmrforge_api/` and
  the shipped data `nmrforge_data/config/nmrforge.yaml` + `nmrforge_data/presets/`, computed over **line-ending-normalised
  bytes** so one commit fingerprints identically on Windows and Linux), `token_digest` (AST
  normalised, comments/docstrings stripped, comparable across editions), `compat_level`
  (`same`/`additive`/`behavior_changed`/`contract_changed`, plus `unverified` when the working
  tree and the declaration disagree), `affected` (the downstream steps a behaviour change
  touches: `reference`/`processing`/`sweep_detection`/`localization`/`records`/
  `api_surface`/`cli`/`qc`),
  `contracts` (peak-table columns + record schema + error and warning codes), `defaults`
  (built-in defaults, including the `gaussian_roi` mirror keys) and `golden` (the golden
  vector). `run.json` / `records/reference.json` / `records/manifest.json` write
  `behavior_digest`/`token_digest`/`compat_level`/`compat_affected`/`compat_verified` next to
  `versions`. The declaration lives in `nmrforge_api/compat_declaration.py` (pure data,
  excluded from the fingerprint) and `tests/test_compat.py` guards "fingerprint not updated /
  level inconsistent with affected / `same` with changed tokens / golden vector not
  reproducible"; this round is declared **additive** (downstream need not re-run).

```python
run_combination_study(reference, *, combos=None, axes=None, max_runs=256,
                      localization="parabolic", localize_peaks=None,
                      edge_margin_ppm=None,
                      roi_f1_ppm=None, roi_f2_ppm=None, resume=True,
                      backend=None, write=True, progress=None) -> StudyResult

detect_and_localize(spectrum, *, sigma_multiplier=None, edge_margin_ppm=None,
                    edge_margin_points=None, method="parabolic", roi_f1_ppm=None,
                    roi_f2_ppm=None, sign_mode="dominant", axes=None,
                    targets=None, allow_empty_targets=False)

read_localization_targets / resolve_localization_targets / LocalizationTargets
resolve_localization_targets_by_method / split_target_specs / combine_target_specs
compat_manifest / compat_status / record_stamp / write_compat_manifest
check_conformance / golden_hashes   # golden vector (behaviour self-proof)
```
