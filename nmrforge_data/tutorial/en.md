# NMRForge usage tutorial

This tutorial is for a first look at the graphical application: first what it can do, then one
typical session from start to finish. Menu and button names follow the text the program shows.

## 1. What the program does

- **Reads Bruker data**: works out the experiment type, the sampling mode (uniform / NUS) and the
  dimension layout;
- **Processes automatically**: raw data to NMRPipe FID, then to a spectrum; phase, baseline, window
  functions and zero filling are chosen by the program;
- **Optimises parameters**: SMILE parameter scan and ranking (2D NUS only), ranked by "net true
  peaks" or by "hold-out residual";
- **Picks peaks and writes peak tables**: automatic detection with parabolic / 2D Gaussian
  sub-grid localisation, POKY-style `.list` export, UCSF export for spectra;
- **Quality control**: FID / sampling / spectrum checks with a per-item report when a run ends;
- **Projects and provenance**: every data set keeps its working directory, generated scripts, run
  records and logs inside your project directory;
- **Data groups and batch runs**: organise 2D data into groups and process them together;
- **Manual path**: any generated script can be opened, edited and run directly.

## 2. The window at a glance

**Four columns**, with draggable separators:

- **Column 1 - project tree**: project → experiment → inputs / processing / outputs / graphics.
  Selecting a node sets the context for the other columns.
- **Column 2 - processing pipeline**: the breadcrumb for the current data set plus the four steps,
  each with a status and a "what to do next" hint.
- **Column 3 - task log**: a permanent **middle column** (it no longer sits at the bottom),
  scoped to the current selection (single data set, data group, experiment type or everything).
- **Column 4 - spectra**: the embedded viewer plus the spectra already produced for this context.

Top menu: `File`, `Experiment`, `View` (hide/show the project tree, the pipeline or the spectra),
`Tools`, `Settings`, `Help` (Usage tutorial, and About - the tutorial is this document).

## 3. First run, step by step

1. **New project**: `File → New project...`, pick a directory (empty is easiest); everything is
   recorded there.
2. **New experiment**: `Experiment → New experiment...`. The experiment type can be left blank - the
   program classifies the data itself.
3. **Import data**: right-click the experiment node in column 1 → "Import sample data...",
   then pick the **Bruker data directory** (the one holding `acqus` and friends). Segments of the
   same experiment can be imported as several runs; 2D data can also be imported as a group.
4. **Check what the program understood**: column 2 shows the experiment type, the sampling
   classification and the dimension layout, with the evidence in the log. **Fix a wrong
   classification now** - it is far cheaper than patching things up after processing.
5. **Generate FID**, then **Generate spectrum**. The data-quality inspection runs before any
   optimisation and writes its conclusion to the log.
6. **Read the report at the end of the run**: spectrum-quality items (signal-to-noise, phase,
   baseline, artefacts), the data-quality conclusion, and the processing parameters actually used
   (including the ones the program chose).
7. **Look at the spectrum**: wheel to zoom, middle button to pan, several spectra can be overlaid;
   clicking a row in the peak table jumps to that peak.
8. **Pick peaks**, then export the peak table or export the spectrum as UCSF.
9. To change processing details, use the **manual path**: open the generated script, edit it and run
   it directly (manual runs are recorded separately from automatic ones).

## 4. The four steps

### Generate FID

Converts the raw Bruker data with the generated `fid.com` (it calls `bruker -AUTO` internally).
**This step needs NMRPipe on the machine.** For 3D data the program decides whether the output is a
single file or slices.

### Generate spectrum

Runs the optimised processing path and writes the final spectrum; **NUS data automatically includes
the SMILE reconstruction**. This is the step that costs the most time and memory, and the log records
the parameters it really used.

### SMILE optimisation (optional, 2D NUS only)

Scans reconstruction-parameter candidates and ranks them, producing a ranking table and candidate
scripts. **It does not replace the active spectrum by itself** - rerun the one you want. 3D NUS data
processes normally, but this scan is not offered in the interface.

### Peak picking

Automatic detection with intensity and S/N estimates; 2D Gaussian refinement can be restricted to
selected peaks. Peak tables export to the POKY style.

### Status and "expired"

Each step has six states: **not ready** (a precondition is missing), **can be run**, **running**,
**completed**, **failed**, **expired**. After you change an input or a parameter, the affected
downstream steps are marked "expired" instead of quietly reusing the old products - just rerun that
step.

## 5. Looking at spectra

- the viewer in column 4: wheel to zoom, middle button to pan, an intensity slider for the
  contour levels, several spectra overlaid;
- 3D data can be inspected slice by slice with projections;
- a **standalone viewer** (no project needed) can be started on its own: `nmrforge-viewer` after an
  editable install, or `python -m viewer`; it accepts dropped `.ft2` / `.ft3` files.

## 6. Data groups and batch runs (2D only)

Data can be organised into groups and processed together. **Batch runs are 2D only**: non-2D data is
skipped with a reason, and one failing data set does not abort the whole group.

## 7. The Tools menu

- **Data quality inspection**: checks the raw FID; the conclusion goes into the log before any
  optimisation;
- **spectrum quality assessment**: scores an existing spectrum and lists the items behind the score.

## 8. Settings

`Settings → Software settings`:

- **Interface language**: follow the system, Chinese or English (restart the program as the hint
  says; `NMRFORGE_LANG` can pin it for a single run);
- **NMRPipe path**: leave empty to search automatically (PATH, `NMRPIPEBIN` from `~/.cshrc`, the
  usual install locations);
- **Data directory**: the starting point of the import browser;
- **default line width** and **alignment tolerance** per nucleus: they feed the automatic
  optimisation and the peak-matching criteria;
- **SMILE threads**: 2 by default, upper limit is "machine cores minus 2"; more is faster and
  hungrier, and staying conservative is recommended for NUS reconstruction.

## 9. Editing the scripts yourself

The script produced by any step can be opened in the script editor, edited and run directly. Editing
a script is a deliberate override: manual runs are recorded separately from automatic ones so the
two can be compared.

## 10. Where the products go

Everything lives in the **project directory** you chose: the project file, each data set's working
directory, the generated scripts, run records and logs. On the normal path the program does not write
into your original data directory, and deleting project data goes to the operating system's trash
(recoverable).

**One exception: NUS bad-point cleaning happens at the source.** The program **rewrites `ser` and
`nuslist` in place** in the raw directory (dropping the whole row of each bad point), copying the
originals to `ser.bak` / `nuslist.bak` next to them first (an existing `.bak` is not overwritten); the
log states `Source cleanup ... (backup .bak)`. It does this so the reconstruction gets clean input;
**if your raw data is read-only, or you cannot accept the original being rewritten, copy it yourself
first** - the `.bak` in the raw directory is your way back.

## 11. Known boundaries

- real processing needs an external **NMRPipe**; NUS reconstruction additionally needs **SMILE** -
  neither is bundled;
- the program processes and records, and **does not draw scientific conclusions for you** -
  statistics, significance and the final call belong to your own workflow;
- peak-overlap deconvolution, structure determination and automatic assignment are out of scope, and
  batch processing is 2D only;
- check the results against your own data (the evidence page is
  `docs/evidence/real-data-comparison.md` in the repository).

## 12. Where to look when something fails

1. the **task log** in column 3 (the middle column): the reason is usually there (a missing executable, memory
   running out at a particular step, ...);
2. `Tools → Data quality inspection`: separates "the data has a problem" from "the parameters do
   not fit";
3. the scripts, run records and `log.txt` in the project directory: inputs, scripts and parameter
   fingerprints for every step;
4. `docs/troubleshooting.md` and `docs/faq.md` in the repository.
