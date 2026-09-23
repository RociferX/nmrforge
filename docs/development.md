# Development process

## Environment

- Developed on Windows; validated against the real engine on Linux (where NMRPipe/SMILE runs).
- Entry: `python main.py` (venv `nmrforge/` and `pip install -e.` are automatically created on the first run)
- Dependencies see pyproject.toml; numpy restrictions <2.5 (nmrglue 0.11's dtype alias problem)

## Git workflow

- Branch `master`; One commit corresponds to one logical change
- Routine: change locally -> `pytest` (add `--basetemp=<dir>` if your temporary directory is
  restricted) -> `ruff check .` -> run the suite on a machine that has NMRPipe (the suite does not call the engine) ->
  commit.

## Test

- `pytest` Full quantity; GUI For testing `QT_QPA_PLATFORM=offscreen`
- **Full path regression (mandatory, 0.2.163-patch8) **: Must be run every time the code is modified
  `python -m pytest tests/test_full_paths.py`. This test covers automatic and manual entry.
  2D/3D uniform + NUS main path (including diagnosis and processing parameter optimisation). Batch currently only supports 2D.
  Should not be expressed as covering all four paths; this capability boundary is recorded as a review item `BATCH-012`.
  This file must be updated simultaneously when new or modified processing procedures are added.
- Tests that do not rely on the real NMRPipe are preferred (FakeBackend mode, `tests/test_full_paths.py`)
- If the system temporary directory is restricted, point pytest somewhere writable with
  `--basetemp=<directory>` (for example `$env:TEMP\pytest_nmrforge`).

### Where test and temporary products go

- Test and temporary products never go into the repository: pytest uses `--basetemp` (or the
  system temporary directory); `scripts/vm_test.sh` runs in a temporary directory and redirects
  the bytecode and ruff caches there too;
- Scripts worth keeping long term live in `scripts/`; one-off diagnostic scripts are deleted
  after use;
- Use the project's own virtual environment (`python main.py` creates it on first run).

**Test fixture discipline (mandatory, 2026-09-16, Phase 12)**: Tests ** must not ** rely on absolute paths that only exist on one machine: such a dependency does
not fail loudly, it just makes the whole group of cases silently skip under `skipif` elsewhere
(and in CI), which means there is no test. When a real file layout is required.
Replicate the layout with synthetic data in `tests/conftest.py` and assert inside the fixture that the parser actually accepts it. Example:
`nmrpipe_fid_template`(2D NMRPipe fid:`FDDIMCOUNT=2`, `FDQUADFLAG=0`,
**`FDF2QUADFLAG=0`**, 2048 byte header + real part block per trace/imaginary block), see the contact list.
`tests/conftest.py`.

## Back up before making changes (mandatory)

Before touching any file, make sure the state is **recoverable**: for work in progress, take a
stash commit (`git stash create`) and tag it `backup/<date>-<topic>` (or archive the complete
repository outside the working tree with `git bundle create`), and do not continue if the backup
failed. Restore with ``git checkout backup/<tag> -- <file>`` or by cloning the bundle.

## Test classification (unit/integration/regression)

The **single source** for the classification table is [`tests/categories.py`](../tests/categories.py),`tests/conftest.py`.
Mark each use case according to the file name (do not move the file -- Audit F.2 has determined that the fixture path dependency makes moving the directory not worth the gain):

```bash
pytest -m unit          # pure logic, seconds: the quick pre-commit regression
pytest -m integration   # real I/O / Qt / ProjectManager
pytest -m regression    # guards for defects, review fixes and release invariants
```

**The new test file must be registered in `tests/categories.py`**, otherwise `tests/test_test_categories.py` will fail.
(Unregistered items are treated conservatively as `integration`).

## Expose the docstring of API (mandatory, 2026-09-17, Phase 19)

Public entry must contain five sections. If one section is missing `tests/test_api_docstrings.py`, it will fail:

```text
Parameters (with types and physical meaning)/ Returns / Raises / Side effects / Examples
```

Coverage: External `nmrforge_api` processing and research entrance, `workflow/batch.run_batch`.
`core.peaks.localize.localize_peak`, QC API(`core.qc.spectrum_quality.evaluate`,
`core.audit.qc_audit.read_audit`, `workflow.direct_diagnostics.*`), sampling API
(`core.experiment.sampling_detector.detect`, `core.data.nus_reader.*`).
**Internal private functions do not require** complete user documentation; when adding a public entry, you must add it to the guard list and complete the documentation.

## Log (Phase 22, mandatory)

- The library code (`core/` `backend/` `workflow/` `viewer/` `gui/`) just does
  `logging.getLogger("nmrforge.<module>")`,**Do not configure handler during import**;
- The entrance is responsible for configuration: both `main.py` of GUI and the command line `python -m nmrforge_api` are called
  `core.logging_setup.configure_logging()`. The level is `NMRFORGE_LOG_LEVEL`.
  (DEBUG/INFO/WARNING/ERROR, default WARNING); log writes **stderr** -- stdout of CLI only puts JSON;
- **Nude is prohibited at the interface layer `print()`**(`tests/test_logging_setup.py` scan `gui/`, `viewer/` guard);
  The user output needs to go through the interface control/log panel, and the operation and maintenance output needs to go through logging;
- The log of each run: `core.logging_setup.attach_run_log(<run_dir>)` is hung in this directory `run.log`
  (Delayed creation: If there is no log record, the file will not be left empty; at the end `detach_run_log`). The failure path is used.
  `logger.exception` Write the complete traceback into `run.log`. API Run by run `log.txt` still process the log.
  Part of the contract, the two coexist without changing each other's names;
- When the log text involves an absolute path, use `core.logging_setup.sanitize_path()` to collapse home into `~`

## User visible errors and debugging information (mandatory, 2026-09-17, Phase 21)

The message seen by the user must be able to be modified accordingly:

- **front end/CLI Export** translates problems such as paths, permissions, input formats, and missing fields into "Error: <How to fix>";
  The message itself for known interface errors (`nmrforge_api.errors.SensitivityError` family) is written for the user and can be used directly;
- **Disabled** Only display type names such as `KeyError` / `IndexError` / `TypeError` / `NoneType` or bare traceback;
- Complete traceback only takes debug channel: CLI Use `--debug` or `NMRFORGE_DEBUG=1` (print to **stderr**)
  GUI/Backend write and run log, do not enter the dialog box text;
- Unknown exceptions retain the type name + original text (do not swallow information, do not make up reasons), but also add an executable prompt

Reference implementation: `core/user_errors.py::describe_exception` (GUI/CLI shared translation, Qt-free leaf module) and.
`nmrforge_api/cli.py::_report_unexpected`(CLI exit). Guard test `tests/test_user_errors.py`.
Will scan `gui/`, `viewer/`, and fail once the visible text of user in the form of `type(exc).__name__` appears.

## Processing process change coverage principle (mandatory, 0.2.163-patch15)

Changes proposed by user to the processing flow (generating FID/generate spectrum /Artificial/Batch etc.) must be made by default.
Applies to all four paths simultaneously: **2D uniform / 3D uniform / 2D NUS / 3D NUS**.
(The same goes for automatic and manual entrances); only changes are truly unique to one or a few paths.
(For example, NUS's SMILE reconstruction, 3D slice flow, uniform without reconstruction), only the modification is allowed.
Corresponding path, the reason for the exception is written in and must be explained in the change description/CHANGELOG.
Perform check: `tests/test_full_paths.py` Four paths have been covered (automatic 2D/3D uniform.
+ NUS, manual, batch), it must be all green after each change.

### Four-path function alignment check(mandatory,0.2.166)

New/When changing any processing function, check the following list to confirm the four paths (2D/3D x uniform/NUS.
Automatic/Artificial) There is no difference of "one is present, the others are not":

- Parameter switch transparent transmission: direct_poly_time, window/baseline/zero_fill/extract/ext
  Window, segment_shift_hz, phases; automatic phase is experiment type level fallback.
  (presets processing_hints.auto_phase, magnitude spectrum such as HMBC skip search in all axes).
  Sampling.auto_phase Only valid when route=none escape port is directly connected to the backend;
- Process capabilities: data quality diagnosis, direct dimension window/baseline/zero filling/indirect window optimisation, initial run script retention
  (before_optimize.com), final run quality and optimisation summary, direct dimension phase cache fingerprint.
  (Must include all parameters that affect spectrum, such as window);
- Macro semantics: subject to the macro definition of the target NMRPipe version (such as gaussian -> GMB), prohibited
  "Looks right" way of writing;
- Experiment type template: nmrforge_data/presets/*.yaml is the only data source (0.2.111); new template must
  Synchronous complement `_PULPROG_TYPES` keyword (long/Ranking for specific keywords, to prevent substring preemptive matching).
  And run test_data_understanding classification refinement regression; magnitude spectrum (phase_sensitive:
  False) template must be marked processing_hints.auto_phase=false;
- Kernel-combination matching convention (0.2.168): exact matching for the direct-dimension kernel,
  and counting by kernel species for the indirect dimension (order is ignored) -- the same kernel
  counts as a different fingerprint in the direct dimension than in the indirect dimension (HETCOR
  `13C`@direct vs HSQC-13C `13C`@indirect); indirect-dimension template order may be arbitrary
  (legacy is not uniform), but the number of occurrences of the same kernel must be exact (e.g. NNH
  carries two 15N); the viewer projects the same kernel through the file-header slot plus the Hx/Hy
  index label, and must not take the axis from a 3D spectrum by kernel species;
- NUS/SMILE Special functions (fid_noise, memory guard, nuslist cleaning, lightweight/display layer
  Phase search, direct dimension phase cache) allows to preserve differences, But it must be commented in the code/CHANGELOG.
  State the reason for the exception.

## Script generation convention (mandatory, 0.2.165)

All nmrPipe macro parameters must be based on the macro definition of the target version, and "looks right" writing is not allowed:

- Gaussian windows are always rendered as `nmrPipe -fn GM -g1 X -g2 Y`(0.2.170
  NMRPipe native parameter, default g1=8/g2=15). Disable GMB -lb/-gb(measured window in FID.
  The tail explosion is amplified, the spectrum is completely wrong) and GM -lb/-gb (which is silently ignored and the window does not take effect);
  Lb/gb (Bruker semantics) is no longer mapped directly;
- DC offset correction POLY -time Only enter the final run complete script (direct_poly_time), first pass
  The complex preview is not added (to avoid biased direct dimension phase search); the uniform is.
  Generate_process_script is controlled by direct_poly_time, NUS is controlled by.
  Generate_2d/3d_nus_script control, the four paths must be consistent;
- Process() only issues "start converting fid" when the bruker -> NMRPipe conversion is actually executed
  When reusing a converted fid, issue "reuse converted fid (skip conversion)"; do not issue conversion unconditionally.
  Progress (unified preview/joint/ candidate will call process multiple times).

## Old item cleanup principle (mandatory, 0.2.164)

Change/When adding a new function, old implementations that it supersedes or duplicates must also be cleaned up:

- Old implementations that are replaced by new paths (old phase optimisation, old automatic orchestration, local equivalent implementations, etc.) are all in
  In this change, delete or merge into a new path, and "new code goes online, old code stays behind" is not allowed;
- Dead code (no references in products or tests), unwired skeleton placeholder, used by Test only/verify script
  Side branch modules are deleted together (the old git history is retained and can be restored at any time);
- Only one implementation is reserved for the same responsibility: configuration loading, Experiment reading, work area, peak table IO
  Repeated implementations such as phase search will be merged into the new path first (0.2.164 has converged accordingly);
- Deletions must be synchronized: associated tests, scripts/tools, ownership prefix, document references
  (README/PROJECT_STATE/architecture/API_CONTRACT),
  __all__/Package export, all updated together;
- Delivery inspection: After cleaning `pytest` full quantity + `ruff check.` full green;
  `git diff --stat` The number of deleted rows should be significantly greater than the number of new rows (pure cleaning changes).

## ReportConvention(mandatory,0.2.169)

- The fixed three-section structure (`_append_final_summary`) is reported at the end: ◆ The final spectrum image quality (comprehensive
  Judgment + signal-to-noise ratio/phase/baseline/artifact sub-level score + baseline indicator + inspection instructions).
  ◆ Data quality diagnosis (detect N item/Processed automatically M conclusions + details), ◆ Process parameter.
  With optimisation; the single-line condensed "spectrum quality:..." may no longer be returned;
- The log of data quality diagnosis (before processing) must be arranged at the beginning of the process (uniform is consistent with NUS)
  It must not be deferred until after optimisation;
- Log is isolated by selected context (0.2.186): a single data log is independent, and the data group is shared within the group
  Experiment type experimental level, the rest is global; selected changes are switched to display by the main window set_scope, group batch.
  Progress is explicitly routed to group scope -- logs of different data must not be mixed into the same buffer.
- Manual operation/Rerun the final script must forward script output in real time (0.2.188):run_manual_*
  Transparently transmitted through progress CshRuntime.run(on_line=...),GUI Display line by line; must not only output.
  Finally complete a row without intermediate progress.
- Window function (0.2.190): direct dimension / indirect dimension are both real optimisation -- candidate pool contains no window (none)
  Use FWHM/SNR/linear memory score (do not use spectrum_quality to avoid phase / baseline linkage.
  The window is biased from the windowless band); the indirect dimension score band resolution retention factor, the natural attenuation axis falls correctly to the windowless.
  Truncate axis selection and window; order constraints: baseline correction precedes window function optimisation (2 baseline -> 2.1 re-rendering.
  -> 2.5 direct dimension window -> 3 indirect dimension window); direct dimension candidate 0.5-0.98 priority.
  (off=0.5 end=0.98 pow=2), resolution filter 1.25x;gaussian does not make the default candidate.
  (GM Model not confirmed by source, rendering/Manual configuration is still supported).
- The window vector must be consistent with NMRPipe point by point (0.2.191):SP first point multiplication -c(SP default 0.5
  GM/EM Default 1.0);GM Gaussian constant k=1/(2*sqrt(ln2))=0.6005612(measured.
  Non nmrglue 0.6 approximation), GM/EM dependent spectral width SW (take fid header FDFxSW, axis label.
  F1 -> FDF1SW); indirect dimension resolution pool 1.15x(direct dimension 1.25x), the natural attenuation axis is determined to have no window.
- GM Add direct dimension default candidate (0.2.192):GM g1=8 g2=15(g3=0,c=1.0), dependent on spectrum width
  SW, skip GM/EM candidate when SW is not provided; do not add GM to the indirect dimension candidate pool (maintaining the natural attenuation axis.
  No window). Manual script editor: non-modal show opens (does not lock the main interface), "Save" immediately.
  Place the disk and close it. "Run" saves it first, then sends run_requested and then closes it automatically.
- The script editor must be opened quickly and without repetition (0.2.193): quality diagnosis is only executed when clicking "Run"
  (within run_manual_spectrum), open the editor and read-only script without re-running the diagnosis; the same steps with the same data.
  Press (data_id, step) to remove duplicates, click repeatedly to focus only on open windows, different data/Steps can coexist.
- Import/Inter-data group analysis drop-down maintains main window sub-widget scheme(0.2.194 revision): Do not use independent
  Top-level window (positioning problem cannot be solved, 0.2.163-patch4 is therefore deprecated); subwidgets must be set.
  Qt.WA_AlwaysStackOnTop, ensure that it is drawn on top of the central component (Windows first pops up.
  Invisible means that the attribute is missing and is covered), and is positioned relative to the coordinates of the main window.
- NUS Conversion/bad point deletion must be based on sampling parameters to determine the layout (0.2.195):fid.com
  NusExpand and bruk2pipe must use the same NusTD grid (patch_fid_com mandatory.
  NusExpand -yT/-zT,xN keeps serPadSize completion value from being overwritten by acqus TD);
  Bad point source deletion press _ser_point_layout to derive each point byte block (direct dimension TD completion +.
  Word length + redundant number), fallback to generating FID if they do not match; clear fid and press States layout.
  Slice 2*f1+1/2*f1+2, row 2*f2/2*f2+1.
- Bad point potential problems are only reported and not automatically handled (0.2.196): NaN/Inf, uniform all zero trace
  Sustained abnormally high energy traces (>100 x non-zero energy median) are reported in the direct dimension diagnostics and given to the indicator.
  Do not change the data; ser row number and nuslist Points are inconsistent/Clean up at the source when the redundancy numbers are inconsistent.
  Report clearly in the log and roll back, without forcibly deleting it.
- After the bad point is removed, the grid must be adjusted according to the actual sampling range (0.2.197): delete the bad point from the source
  (source_removed) and then press the cleaned nuslist to derive the grid with max+1 per dimension and update.
  Acqu2s/acqu3s of NusTD (reduce only); cross-validation (parameter correction) must use the adjusted.
  NusTD, the fid.com grid must not be changed back to the static original value -- otherwise the fid grid and the cleaned data.
  Inconsistency, reconstruction misalignment.
- Segment container determination (0.2.198): data segment = subdirectory directly containing acqus; missing acqus or
  Subdirectories with no data will be ignored and "top-level missing acqus" will not be reported; if subdirectory parameters are inconsistent, it will be clarified.
  Report "Not a segmented experiment". Anything that will modify the original data such as acqus/acqu2s/acqu3s/nuslist/ser.
  The program of file must first back up.bak (only for the first time), and use copy instead of hard link to temporarily store directory to avoid in-situ.
  Writing through links pollutes the original.
- Segmented 3D NUS processing (0.2.199): patch_fid_out_name When renaming the main output, it must be changed simultaneously
  The `-in`;convert_to_fid segment branch in the mask stage must first be like reconstruct_nus.
  Clean up the bad points and click Merge to adjust the actual range of nuslist NusTD; the quality check of segmented data is based on the first segment.
  Evaluation (raw/ container root directory has no acqus/ser, no false positives allowed).
- Segmentation and normal NUS use the same slice flow (0.2.199-patch1): each segment also applies acqu3s TD
  Correct the temporary storage (first.bak backup, modify in the complex data), bruker directly outputs fid/test%03d.fid.
  Slice;_split_slices Skip splitting when the output directory already has slices (only process single file output).
  Multi-segment merging is still done by _merge_slices.
- Manual fid.com Single dataset is consistent with segmentation (0.2.199-patch2): Manually modified parameter
  Parse_fid_com is extracted as overrides, and the backend convert_to_fid performs the conversion uniformly/.
  Bad point clean up/return to position, user script cannot be run directly via csh (structural changes are not retained); manual.
  The editor prompt states "Only adjust the parameter, do not change the output name.".
- The direct dimension range can be applied to the optimisation process (0.2.199-patch3): the range dialog box is enabled by default with "Apply this
  Range to optimisation process" -- When enabled, the user range is used for both the first-pass reconstruction/phase search and optimisation evaluation.
  (baseline / zero filling / window function), and reduce SMILE memory; when turned off, optimisation uses the default 6.5-10.5 large range.
  This range is only used for final runs. SMILE The low memory prompt must also give "narrow the direct dimension range + turn on this option".
  Suggestions.
- The pipeline step row button area must be able to automatically wrap (0.2.199-patch4): generate a row after the spectrum is completed
  Up to 5 buttons (direct dimension scope/again optimisation/rerun final script/display spectrum/manual), width.
  When it is insufficient, a new line will be automatically added and no single line will be allowed. Hidden buttons do not occupy space.
- Left tree data running status (0.2.199-patch5): any processing (automatic steps/Rerun the final
  When script/manual script/data group batch) starts, the corresponding data in the tree on the left displays "Running". After.
  Restore the product inference state; the mark must be maintained in the main thread (the background thread queues the signal) and must not be directly.
  Cross-thread touch interface.
- Memory phase searches must be fast and cancelable (0.2.199-patch6): direct dimension phase scoring is only allowed for signals
  The window rows are rotated (the rows are taken first and then rotated, the result is consistent with the full plane rotation), and the full plane must not be multiplied each time;
  The search grid loop must check the backend.runtime cancellation flag and throw "Task canceled" at the checkpoint.
  Progress is output every ~100 ratings. "Task Termination" must also request cancellation of memory calculations.
  (terminate_current_tasks + request_cancel) and clean up orphans NMRPipe that are not in the registry.
  Process(cleanup_orphan_tasks,By tool name/Workspace path matching); main window closeEvent Same.
  First terminate + request_cancel and then cleanup to prevent the application’s legacy background processes from closing.
- Baseline optimisation must be progressed and can be canceled (0.2.199-patch7):optimize_baseline axis by axis/chase candidate
  Output the scoring progress. When cancel is set, check between candidates and throw "task canceled"; the caller.
  (phase_routes uniform/NUS) must transparently transmit progress and cancel_requested. Any.
  Trace-by-trace robust fitting/Full spectrum scoring memory optimisation also applies to this constraint.
- Baseline optimisation must control overhead (0.2.199-patch8): candidate scoring is only allowed on trace subsampled replicas
  (<= max_traces, default 4096), robust trace-by-trace fitting must not be performed on all traces; off.
  Axis with score >= 95 skips candidate and remains off directly; subsampling remains ndim,off in the same position as candidate.
  Comparing on the basis, the selection semantics remain unchanged.
- Baseline optimisation, stripe rejection must be relative semantics (0.2.199-patch9): only reject those that are significantly worse than the original spectrum
  Candidate (fringe ratio after correction > original spectrum + 4 and still > 8), improved correction is allowed when the original spectrum already has stripes;
  Axis with a fringe ratio of the original spectrum >8 is prohibited from subsampling (thin stripes will be missed) and must be fully evaluated; log must.
  Distinguish between "rejected by stripes" and "insufficient gain". Do not generally write "candidate is not better than the current configuration".
  The stripe ratio is capped (0.5) and each candidate score is the same as off same/Worse,The vertical stripes are data/acquisition artifacts.
  (DC bias, uneven energy distribution), is not a baseline that can be corrected by POLY, keep it off and report it truthfully.
- Qt events API uniformly use the name actually provided by the binding (from PySide6/`qtcompat`;0.2.199-patch10): drag processing path
  A non-existent scenePosition must not be used;QGraphicsSceneMouseEvent takes scenePos().
  Ordinary QMouseEvent takes position() and branches by type; 3D slice drag bar is kept long enough.
  (Minimum width >= 220).
- NUS direct dimension window must be fixed SP(0.2.199-patch11):SMILE requires direct dimension input to be apodised and
  The tail decays to zero (same model as laboratory smile.com), direct dimension window optimisation candidate (none/gaussian/.
  Exp) must not overwrite direct dimension; generate_2d/3d_nus_script always generates SP lines for direct dimension.
  (sine_bell is configured according to the configuration, and the rest falls back to the default SP); SMILE internal error ("SMILE Error" in.
  Output) must be explicitly judged as a failure, and it is forbidden to treat failed reconstruction as a successful spectrum.
- GUI Main thread prohibits reading large spectrum and calculating quality (0.2.199-patch12): Generate spectrum parameter report
  (spectrum_quality_report_lines Read the entire ft3 + full spectrum evaluation) Must press the spectrum file fingerprint.
  + Parameter fingerprint (path + mtime + size + parameter SHA256) cache, the spectrum and parameter must not be repeated if they remain unchanged.
  Read; the same panel only allows one run at the same time (running flag) to prevent continuous clicks from overlaying.
  The task is stuck.
- SMILE must be set each time according to the currently available memory -maxMem(0.2.199-patch14): real-time before reconstruction
  Calculate MemAvailable x MEM_SAFETY and pass in generate_2d/3d_nus_script(max_mem).
  Same budget as memory guard; must not rely on SMILE automatic cap, Prevent estimation bias/Concurrent occupation.
  As a result, the peak value exceeds the limit and is hard to carry. The script generator does not generate this line by default (testing byte-level stability).
- SMILE Memory estimates must be aligned SMILE Self-reported dimension (0.2.199-patch15): model = direct dimension
  Number of points x indirect dimension iteration FT size product x 16B (double) x 1.06, iteration FT =.
  Next_pow2(3 x NusTD); Use SMILE to start the banner before changing the constant Memory Used measured point.
  Verification (sampleK zero filling 1024 = 2.8GB, grid 146 x 145 -> 219 x 217 -> FT 1024²).
- NUS direct dimension phase must be searched on the complex frequency domain final spectrum (0.2.199-patch18): recon plane is indirect dimension
  Time domain, single point time domain trace is aliased by t1, direct dimension search must not be done on the recon plane; finalize.
  Keep_complex(all PS without -di) output complex frequency domain final spectrum, direct dimension =last axis.
  (3D), score on this axis; 2D keeps recon.ft1 axis 0. Layout convention: nus3d_rc plane.
  For "each direct dimension points a plane" (0.2.85 slice flow starts), the plane is an indirect dimension -- any.
  The assumption that "the direct dimension is in the plane axis" is outdated and wrong.
- Direct dimension symmetry score (0.2.199-patch19~21): Peak window symmetry must be shape-aware -- pure symmetry
  Scoring (negative absorption peaks are also symmetrical), sign penalty is only used for spectra with the same sign (negative window sym x 0.2 light penalty.
  Abnormal reverse sign peaks are weighted down); positive and negative peak coexistence spectra (mixed) use global pure symmetry; +/-180 is derived from the near-optimal platform.
  Minimum corrected disambiguation, platform tolerance 1 (not 5). Experiment template peak_sign must be correct.
  (Solution HNN=hncannh/hncocannh -> mixed, do not mistakenly map solid NNH=uniform).
- The conversion must be single file (0.2.199-patch16): four ways (automatic/Artificial x single dataset/segmentation)
  Fid.com always generates a single file {dataset_id}.fid(bruker -AUTO by NusTD grid +.
  -aq2D Complex, consistent with laboratory fid.com); acqu3s TD must no longer be used to correct the temporary storage.
  (When TD=1, bruker also outputs single file full mesh fid); 3D NUS slice only in SMILE script.
  Step1 direct dimension is processed and generated (nus3d_1/test%04d.ft1); multiple segments are merged using addNMR to merge.
  Single file (merged/{dataset_id}.fid), segment frequency migration PS -rs (a manual value
  goes through params["segment_shift_hz"]). Since 2026-09-23 a multi-part conversion also
  checks the inter-part field drift automatically (reference = part 1, criterion |d| > 1.5 Hz,
  Hz only - ppm is measured and recorded but does not gate): over the threshold,
  `PS -rs <d>Hz` is inserted before `MULT -c` in that
  part's fid.com (MULT kept) and the part is re-converted; the residual is re-checked before
  merging, and an unmeasurable peak or a residual still over the threshold is only reported
  (see workflow/field_drift.py). The conversion period slice is a historical product.
  New code must not be generated; the slicing fallback for _zero_bad_point_fid is only compatible with the old working directory.
- SMILE peak memory budget (0.2.199-patch15): a SMILE peak of <= 2.8GB (zero fill 1024) is
  safe; the 5.6GB magnitude (zero fill 2048, direct dimension doubled) exceeds the memory
  guard. Test data and re-runs must keep `estimate_smile_peak_mb <= 2.8GB`;
  `test_dev_smile_memory_ceiling` locks that constraint.
- If the quality assessment report baseline is not normal, it must be compared with the baseline optimisation result output reason (evaluation baseline difference/
  Keep off threshold), you must not only report "recommended baseline correction"; baseline evaluation takes the worst and merges each storage axis.
  Mark the worst axis (0.2.170), and you must not only check the last axis;
- Phase score must have the same origin as phase optimisation (0.2.172/0.2.185): peak window signature net absorption
  (score_axis_memory is the same as the formula), sign_mode is determined by experiment type template peak_sign --.
  Mixed spectrum (positive and negative peaks coexist) real negative peaks are not considered phase errors, Negative areas are prohibited/negative peak ratio.
  Absorption negative component error penalty; the four paths (2D/3D x uniform/NUS) phase optimisation scores are unified as.
  Net absorption (NUS direct dimension 0.2.185 has the same origin as the rest of the paths);
- To determine whether the net absorption is positive or negative, first level the baseline (0.2.185): the peak window minus the baseline level (the baseline area on both sides of the peak
  Median mean) and then divided into positive and negative, not bounded by zero -- baseline The overall offset no longer pollutes the net absorption;
  The offset is >= 1% of the peak height before being flattened; the 1D synthetic spectrum remains zero-bounded;
- NUS direct dimension Search with symmetry (0.2.95/0.2.187): recon the net absorption on the plane direct dimension
  The scoring distinction is poor and mixed cannot be disambiguated +/-180°, direct dimension is based on positive peak spectrum symmetry + positive peak constraints.
  Processing; indirect dimension maintains net absorption. The difference in scoring approach between direct dimension and indirect dimension is intentional (see 0.2.187).
- The report content is for the final user: use ✓/⚠/✗, good/Need to pay attention/Poor to make clear suggestions and avoid
  There are only technical indicators and no conclusion.

## Static checking

- `ruff check.` should be approved in full

## Submit an agreement

- Message style: `feat:` / `fix:` / `refactor:` / `docs:` + Chinese brief description
- Documentation and code are updated simultaneously (CHANGELOG / PROJECT_STATE / README)

## Behaviour fingerprint and change levels (mandatory, 2026-09-20)

After changing `core/`, `backend/`, `workflow/`, `nmrforge_api/` or the shipped data
(`nmrforge_data/config/nmrforge.yaml`, `nmrforge_data/presets/`): **decide the level first, then update the declaration**,
or `tests/test_compat.py` fails (it guards against silent behaviour changes):

```bash
python scripts/update_compat_declaration.py --level additive          # new entry points/fields
python scripts/update_compat_declaration.py --level behavior_changed \
    --affected localization,sweep_detection --note "what changed"     # numbers change
python scripts/update_compat_declaration.py --level same              # comments/wording only
```

- levels: `same` (code tokens unchanged) / `additive` (downstream need not re-run) /
  `behavior_changed` (**numbers change**, `affected` required) / `contract_changed` (columns,
  fields or error codes changed -- update the contract mirror and the guard);
- the declaration lives in `nmrforge_api/compat_declaration.py` (a pure data module,
  deliberately outside the fingerprint; never put logic in it);
- fingerprint scope: `behavior_digest` covers the four code trees plus the shipped data (the
  user's local `nmrforge_data/config/nmrforge.local.yaml` is not counted); `token_digest` strips
  comments/docstrings, so the two language editions agree whenever the code is identical;
- the artefact stamp in `run.json` / `records/reference.json` / `records/manifest.json` is
  written by `nmrforge_api.compat.record_stamp()` -- never assemble it by hand;
- the golden vector `python -m nmrforge_api compat --golden` must match the declaration (sync
  the declared `golden` hashes when behaviour changes).

## Record/state writes: always atomic (mandatory, 2026-09-20)

- Every JSON document written and read back **as a whole** (study state, `run.json`,
  `workflow.json`, reference/record JSON, localisation attachments, the
  compatibility-manifest export, the per-step phase and NUS parameter caches,
  `*.quality.json`, the SMILE ranking/report) goes through
  `core.project.manager.atomic_write_text` (a temporary file in the same directory
  plus `os.replace`) instead of `Path.write_text` directly: if a write is interrupted
  (crash / power loss / a concurrent reader) the reader sees either the complete old
  version or the complete new one.
- Append-only logs (`run.log`, `qc_audit.jsonl`, CSV streams) are not covered.
- When converting such a site **change only the write mechanism** and keep the
  serialisation expression as it is -- the file bytes must not change, and neither
  should downstream fingerprints, cache fingerprints or the golden vector (guards:
  `tests/test_compat.py`, the golden vector).
