# NMRForge usage tutorial

This tutorial is for a first look at the graphical application: first what it can do, then one
typical session from importing data all the way to a peak table. Menu and button names follow the
text the program shows (the interface language can be changed under `Settings → Software settings`,
and this document follows it).

This guide follows the current source tree. Version 1.0.4 is source-only; the existing 1.0.2
AppImage is unchanged and does not include the later API v1.1 updates.

## 1. What the program does

- **Reads Bruker data**: works out the experiment type, the sampling mode (uniform / NUS) and the
  dimension layout;
- **Processes automatically**: raw data to NMRPipe FID, then to a spectrum; phase, baseline, window
  functions and zero filling are chosen by the program;
- **Optimises parameters**: SMILE parameter scan and ranking (2D NUS only), ranked by "net true
  peaks" or by "hold-out residual";
- **Picks peaks and writes peak tables**: automatic detection with three-point parabolic sub-grid
  localisation (the only method), POKY-style `.list` export, UCSF export for spectra;
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
- **Column 3 - task log**: a permanent **middle column**, scoped to the current selection.
  **Failures are usually explained here.**
- **Column 4 - spectra**: the embedded viewer plus the spectra already produced for this context.

Top menu: `File`, `Experiment`, `View`, `Tools`, `Settings`, `Help` (Usage tutorial, and About - the
tutorial is this document).

The single, segmented and batch import forms stay inline on the experiment page; scroll that page
to reach them. Dataset, group and global logs are saved separately: individual member runs go to
the member log, while whole-group runs go to the group log.

The line at the top of column 2 is a **guide**: depending on the current state it reads "Next step:
Generate spectrum", "Next step: rerun Peak picking", "Optional: SMILE optimisation" or "All steps
completed".

## 3. The standard flow: from a new project to a peak table

This is the **main line** for processing one data set; follow it in order. The sections marked
"optional" are side branches - skip them if you do not need them.

### (1) New project

`File → New project...`, pick a directory (empty is easiest). **Everything is recorded in this
project directory.** `File → recent project` jumps back to a recent one.

### (2) New experiment

`Experiment → New experiment...`. **The experiment type can be left blank** - the program classifies
the data itself.

### (3) Import data

Use the single-import form on the experiment page, or **right-click the experiment node in column
1 → "Import sample data..."**. Pick the **Bruker data directory** (the one holding `acqus`). Only
directories with Bruker parameter files are accepted; picking the wrong one is reported straight
away.

When the data is a **segmented acquisition** or **repeated experiments to be averaged**, use the
entry in section 7 instead; to process **several 2D data sets** at once, use the batch import in
section 8.

### (4) Check what the program understood

Column 2 shows the **experiment type, the sampling classification (uniform / NUS) and the dimension
layout**, with the evidence in the log. **Fix a wrong classification now** - this is what every later
automatic choice is based on (dimension roles, processing path, parameter defaults).

### (5) Generate FID → set the direct dimension range (optional) → Generate spectrum

1. **Click run on "Generate FID"**. This step needs **NMRPipe** on the machine; if it is missing the
   step fails and says so. For 3D data the program decides whether the output is a single file or
   slices.
2. **Set the direct dimension range (optional, 6.5-10.5 ppm by default)**: click the **"direct
   dimension range"** button on the spectrum step row. The direct dimension is 1H. **Leaving it alone
   uses the default 6.5-10.5 ppm** (wide, safest); when you know your peaks sit in a narrower window
   (an aliphatic-only region, say), narrowing it saves a lot of time and memory - fewer extracted
   points make the zero filling and the NUS reconstruction lighter too.
   In the dialog, "**apply this range to the optimisation as well**" is **on by default**: with it on,
   the optimisation and the final run use the same window (consistent results); turning it off keeps
   the optimisation on the default wide window and narrows only the final run.
3. **Click run on "Generate spectrum"**. **NUS data automatically includes the SMILE
   reconstruction.** This is the step that costs the most time and memory; the data-quality
   inspection runs before any optimisation and writes its conclusion to the log. Progress streams
   into the column-3 log.

### (6) Read the run report

A finished run produces a report with three parts:

1. **spectrum-quality items**: signal-to-noise, phase, baseline, artefacts;
2. the **data-quality conclusion** (FID and sampling);
3. the **processing parameters actually used** - including the ones the program chose, plus the
   **direct dimension range** and the **CAR carrier convention**.

**Read this before looking at the spectrum**: when an item is clearly off, adjusting a parameter and
rerunning is usually faster than staring at the contours.

### (7) Look at the spectrum

Selecting a sample dataset automatically shows its current spectrum in column 4; after a short
debounce, large files load in the background. A 3D spectrum opens on the F3-F2 plane. In the viewer:

- **wheel to zoom**, **middle button to pan**, an **intensity slider** for the contour levels;
- **several spectra can be overlaid** for comparison; clicking a **row in the peak table** jumps to
  that peak;
- 3D data can be inspected slice by slice with projections.

In expanded comparison, the reference is on the left and the current spectrum is in the centre;
the two viewers have independent controls and equal-height plot areas. Numeric labels and units sit
outside their step arrows. Type a value and press Enter to apply it; arrow buttons and keys still
adjust by the configured step. The aspect-ratio field accepts decimals such as `0.8`; `0` means free.

### (8) Optional: change the spectrum centre / flip an indirect dimension / any other custom edit

After looking at the spectrum you usually want one of the following three things. **Do them at this
point, before picking peaks.**

**Case 1 - change the spectrum centre (the carrier, CAR)**

Open the manual script in the **Generate spectrum** step and edit `CAR`, then run it. The report
identifies the current CAR source; the default carrier is configured from acquisition information.
To use another reference, edit CAR manually in this step. The script difference is recorded and
affected processing steps expire.

**Case 2 - flip an indirect dimension**

**Select the dimension to flip** on the spectrum step row, then click **"Re-run the final script"**:

- **2D**: a single checkbox, "Indirect (F1)" - checked adds `FT -neg`, unchecked removes it. It is a
  **state**, refilled from the current final script, so an earlier flip is not silently dropped;
- **3D**: a three-entry dropdown - **Indirect (F2)** / **Indirect (F1)** / **F1 and F2**. It is a
  **command**: picking an entry flips it, **picking the same entry again cancels it**, and the
  dropdown resets to "no flip" afterwards.

The flip control and the rerun button live in **one box** (read it as "flip → rerun"). **When do you
need a flip?** When the frequency axis of the spectrum runs the wrong way (peak positions mirrored
about the expected values) that dimension has a sign problem. The program infers this from the
acquisition mode, and where it cannot decide the log tells you to confirm by hand.

**"Re-run the final script" reuses the script that was already generated and does not re-optimise** -
phase, window functions, baseline and the SMILE result all stay put, and only the range and the flips
you specify are applied. For 3D NUS only the **indirect-dimension section** is rerun; for 3D uniform
data the direct dimension is rerun too, so follow what the prompt tells you.

**Case 3 - any other custom edit**

Use the **manual** entry: open the relevant step's script, change whatever you like, and run it.
Successful manual runs list command and parameter differences from the last successful script in
the step report. Failed runs label those differences as attempted changes, not successful results.
When a step has failed, its row offers **"View log"** to jump to the matching part of the log. A
successful rerun or optimisation refreshes the target spectrum without changing the selected dataset.

### (9) Peak picking

Run **Peak picking**: automatic detection with intensity and S/N estimates.

**When the picked peaks are not what you want** (too many noise peaks / too few real ones):

1. **change the threshold and pick again** - adjust the peak-picking parameters (threshold and
   friends) and rerun the step;
2. **set a reference spectrum and pick again** - click **"Reference spectrum"** on the Peak picking
   step row and pick a data set that **already has a peak table**; peak picking will then **keep only
  the peaks that match the reference peak table** when the reference is usable; if it is unreliable
  or has no common nuclei, filtering is skipped and the detected peaks are kept. This suits picking
  **the same set of peaks** across related samples; the **"Clear"** button next to it
   removes the constraint.

### (10) Export the peak table and the spectra

- **Export the POKY-style peak table**: click **`Export peaks`** in the spectrum panel. Either
  **export directly** (original coordinates) or **export after alignment** (overall translation onto
  a selected reference peak table); the format is POKY / Sparky `.list`, which other software reads
  directly. `Import peaks` reads a peak table back from a `.list`. Alignment changes only the
  exported peak table, not the spectrum file. If too few peaks match, the interface warns and skips
  aligned export, leaving the peak table and spectrum unchanged.
- **Find the processed spectra**: in the **`spectra` folder inside that data set's working
  directory** - the NMRPipe final spectra (`.ft2` / `.ft3`) are there, and the **exported UCSF
  spectra** go into the same `spectra` folder, with the same name as the final spectrum and a
  `.ucsf` extension.

## 4. The four steps

### Generate FID

Converts the raw Bruker data with the generated `fid.com` (it calls `bruker -AUTO` internally).
**This step needs NMRPipe on the machine.** For 3D data the program decides whether the output is a
single file or slices. The FID conversion script is generated from acquisition parameters and
usually needs no edits; **view or change CAR manually in the Generate spectrum step**.

### Generate spectrum

Runs the optimised processing path and writes the final spectrum; **NUS data automatically includes
the SMILE reconstruction**. This is the step that costs the most time and memory, and the log records
the parameters it really used. When it completes, **"Display spectrum"** and **"Re-run the final
script"** appear.

### SMILE optimisation (optional, 2D NUS only)

Scans reconstruction-parameter candidates and ranks them, producing a ranking table and candidate
scripts. **It does not replace the active spectrum by itself** - rerun the one you want. 3D NUS data
processes normally, but this scan is not offered in the interface.

### Peak picking

Automatic detection with intensity and S/N estimates; localisation uses three-point parabolic
refinement only, with no method chooser. A **reference spectrum** can constrain results when the
reference is usable; if it is not, the filter is skipped safely. Peak tables export to the POKY style.

### Status and "expired"

Each step has six states: **not ready** (a precondition is missing), **can be run**, **running**,
**completed**, **failed**, **expired**. After you change an input or a parameter, the affected
downstream steps are marked "expired" instead of quietly reusing the old products - just rerun that
step.

## 5. Looking at spectra

- the viewer in column 4: wheel to zoom, middle button to pan, an intensity slider for the contour
  levels, several spectra overlaid;
- 3D data can be inspected slice by slice with projections;
- a **standalone viewer** (no project needed) can be started on its own: `nmrforge-viewer` after an
  editable install, or `python -m viewer`; it accepts dropped `.ft2` / `.ft3` files.

## 6. Data groups and batch runs (2D only)

Data can be organised into groups and processed together: tick "Batch import and group (only supports
2D spectra)" while importing, or add and remove members from the data group node in the project tree.
**Batch runs are 2D only**: non-2D data is skipped with a reason, and one failing data set does not
abort the whole group.

## 7. Importing segmented acquisitions / averaged repeat experiments

When one acquisition was split into several segments (**segmented NUS**, the segments complementing
each other to fill the sampling grid), or when the same experiment was acquired repeatedly and you
want to average them for better signal-to-noise, use this path: do **not** import the segments as
separate data sets - let the program merge them into **one** sample data set.

**Entries (two, same effect)**:

- **Right-click the experiment node in column 1 → "Import sample data..."** and tick **"Segmented
  data or repeated experiment overlay import (container directory: multiple subdirectories
  containing acqus are merged into one data)"** in the dialog;
- or use the **"segmented data or repeated experiment overlay import"** group in the experiment
  page's import area directly: fill in or browse to the **container directory** and click that
  group's import button.

**What the container directory looks like**:

- **no `acqus` at the top level**, and **at least 2 subdirectories that each hold a set of `acqus`**
  (each subdirectory is one segment);
- the program recognises this structure by itself - when you pick such a directory, the checkbox in
  the import dialog is **ticked automatically**;
- conversely, if the box is ticked but the directory is not such a container (fewer than 2 segments),
  it tells you plainly instead of importing half a data set.

**After importing**: the segments are **merged into one data set** that goes through the whole flow
(Generate FID / Generate spectrum run once), not several data sets. Averaged repeat experiments work
the same way: they are treated as "same parameters, same sampling points", and the average has better
signal-to-noise.

## 8. Batch-importing several 2D data sets

Use this path to process **several independent data sets** at once.
Note that this is **not** the same thing as the "merge into one" import of section 7.

**Entry**: the **"Batch processing"** group in the experiment page's import area:

1. click **"Add data folder..."** to add data directories to the list one by one (several are fine),
   and **"Clear list"** when you need to;
2. **"Batch import and group (only supports 2D spectra)" is ticked by default** - with it ticked the
   imported data lands in **one data group** that can then be processed together; unticked, there is
   no grouping and it is equivalent to doing several single imports;
3. click **"Batch import"**; a summary (total / succeeded / failed) follows.

**Boundaries of batch processing**:

- **2D only**: non-2D data such as 3D is **skipped with a reason** (process those one at a time);
- **failures are isolated per data set** - one bad data set does not abort the whole group.

## 9. The Tools menu

- **Data quality inspection**: checks the raw FID; the conclusion goes into the log before any
  optimisation. It **does not need a project**, so it is a good way to separate "the data has a
  problem" from "the parameters do not fit";
- **spectrum quality assessment**: scores an existing spectrum and lists the items behind the score.

## 10. Settings

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

Settings are written to the local override file and take effect after a restart.

## 11. Where the products go

- **peak tables**: you choose the location when you use `Export peaks`; the format is POKY / Sparky
  `.list`;
- **spectra**: in the **`spectra` folder** of that data set's working directory - the NMRPipe final
  spectra (`.ft2` / `.ft3`) and the exported **UCSF** spectra (same name as the final spectrum,
  `.ucsf` extension) are both there;
- **everything else**: the project file, each data set's working directory, the generated scripts,
  run records and logs all live in the **project directory** you chose; deleting project data goes to
  the operating system's trash (recoverable).

**One exception: NUS bad-point cleaning happens at the source.** The program **rewrites `ser` and
`nuslist` in place** in the raw directory (dropping the whole row of each bad point), copying the
originals to `ser.bak` / `nuslist.bak` next to them first (an existing `.bak` is not overwritten); the
log states `Source cleanup ... (backup .bak)`. **If your raw data is read-only, or you cannot accept
the original being rewritten, copy it yourself first** - the `.bak` in the raw directory is your way
back.

## 12. Known boundaries

- real processing needs an external **NMRPipe**; NUS reconstruction additionally needs **SMILE** -
  neither is bundled;
- the program processes and records, and **does not draw scientific conclusions for you** -
  statistics, significance and the final call belong to your own workflow;
- peak-overlap deconvolution, structure determination and automatic assignment are out of scope, and
  batch processing is 2D only;
- check the results against your own data (the evidence page is
  `docs/evidence/real-data-comparison.md` in the repository).

## 13. Where to look when something fails

1. the **task log** in column 3 (the middle column): the reason is usually there (a missing
   executable, memory running out at a particular step, ...). A failed step row has a **"View log"**
   button that jumps straight to it;
2. `Tools → Data quality inspection`: separates "the data has a problem" from "the parameters do
   not fit";
3. the scripts, run records and `log.txt` in the project directory: inputs, scripts and parameter
   fingerprints for every step;
4. `docs/troubleshooting.md` and `docs/faq.md` in the repository.
