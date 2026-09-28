# GUI guide

The desktop application is the intended interface for processing spectra. This page is a tour of
what the interface does; the underlying engine behaviour is in
[processing-model.md](processing-model.md) and [qc-system.md](qc-system.md).

![NMRForge main window](../gui/assets/nmrforge.png)

> **Language note.** English is the interface's source language, and the public-repo build shows
> English by default; the same build also speaks Chinese: follow the system language, pick it in
> `Settings -> Software settings -> Interface language`, or pin it with `NMRFORGE_LANG=zh`. The
> Chinese wording lives in `ui_support/locales/zh.json` and is the **same code** as the English
> version. A Chinese edition of this guide is in
> [`Chinese_version/docs/gui.md`](../Chinese_version/docs/gui.md).

## Starting it

```bash
python main.py    # 1.0.1 source entry point (the AppImage starts it internally)
```

The 1.0.1 Linux AppImage is on the releases page (one artefact, interface language switched at
run time); the first
normal run installs the desktop entry (`--remove-desktop` removes it). Build and acceptance
records are in [packaging.md](packaging.md).

## Window layout

The window has **four columns** (the separators can be dragged; the View menu only toggles three of them):

| Column | What it shows |
| --- | --- |
| 1 | Project tree: Project -> Experiment -> Input / Processing / Output / Figures. Selecting a node sets the context for everything else. |
| 2 | Processing pipeline for the selected dataset: a breadcrumb plus the status-driven step list and the "what next" prompt. |
| 3 | Task log: the permanent middle column, scoped to the current selection (single dataset, data group, experiment type, or global). |
| 4 | Spectrum panel: the embedded viewer plus the list of spectra produced for the current context. |

Menus: `File` (new/open/save project, recent projects), `experiment` (new/rename/delete
experiment), `View` (hide/show the project tree, the pipeline or the spectra), `Tools` (data-quality inspection, spectrum
quality assessment), `Settings` (software settings), `Help` (usage tutorial, about).
**`Help -> Usage tutorial`** opens the tutorial that ships with the program (what it does and how to use it, in the current interface language); the text lives in `nmrforge_data/tutorial/{zh,en}.md`.

`Settings -> Software settings` covers: the interface language (follow the system / Chinese /
English, since 2026-09-21), the NMRPipe path, the data directory, the per-nucleus default line
widths and alignment tolerances, and the SMILE thread count (plus "simple mode" in a source
checkout). All of it goes into the local override config
(`nmrforge_data/config/nmrforge.local.yaml`; `~/.config/NMRForge/nmrforge.local.yaml` inside
the AppImage) and takes effect after a restart.

## The processing pipeline

Four steps, each with a status - `Not ready` (locked, prerequisites missing), `Can be run`
(ready), `Running`, `Completed`, `Failed`, and `Expired` (an input or parameter changed
afterwards):

| Step | Label | What it does |
| --- | --- | --- |
| 1 | Generate FID | Converts the raw Bruker data to an NMRPipe FID (`bruker -AUTO` from a generated `fid.com`). |
| 2 | Generate spectrum | Runs the optimised processing path, including SMILE reconstruction for NUS data. |
| 3 | SMILE optimisation | Optional: ranks reconstruction parameter candidates. 2D NUS only; it never replaces the active spectrum by itself. |
| 4 | Peak picking | Automatic peak detection with intensity and S/N evaluation. |

Steps unlock in order. A finished step whose inputs or parameters changed is marked `Expired`
rather than silently reused - the step fingerprints its inputs, its script and its parameters.

## Typical session

The interface carries its own step-by-step walkthrough: `Help -> Usage tutorial` (the text is
`nmrforge_data/tutorial/{zh,en}.md`). What follows is a short version of the same flow; the button
positions and the branches of every step are in the tutorial.

1. **Create or open a project** (`File -> New project...` / `Recent projects`), then **import** a
   Bruker dataset directory (right-click the experiment node in the project tree ->
   "Import sample data..."). Only directories containing Bruker parameter files are accepted;
   picking the wrong one is reported straight away and no half dataset is left behind.
   **Segmented acquisition / repeated experiment overlay** goes through "segmented data or repeated
   experiment overlay import" (a container directory, merged into **one** dataset); **several
   independent 2D datasets** go through the "Batch processing" group - the two are **different
   things**, see the sections below.
2. **Check what was understood**: experiment type with its evidence, sampling classification, and
   the dimension layout. If the classification is wrong, the experiment template can be corrected
   before processing - much cheaper than patching things up afterwards. This is the basis for every
   later automatic choice.
3. **Generate FID** -> **set the direct dimension range** (optional, "direct dimension range" on the
   step row, 6.5-10.5 ppm by default) -> **Generate spectrum** (the next step unlocks
   automatically). The data-quality inspection runs first and its conclusion goes into the log
   before any optimisation step. Generate FID needs **NMRPipe** on the machine; the SMILE
   reconstruction of NUS data is included automatically in the Generate spectrum step.
4. **Read the report** at the end of the run: final spectrum quality (with signal-to-noise, phase,
   baseline and artefact sub-scores), the data-quality inspection section, and the resolved
   processing parameters, including anything the optimiser chose, plus the direct dimension range
   and the CAR carrier convention. Read the report before looking at the spectrum.
5. **Look at the spectrum**: click **"Display spectrum"** on the step row to open the current final
   spectrum in column 4 - wheel to zoom, middle button to pan, an intensity slider, overlaid
   spectra; clicking a row in the peak table jumps to that peak.
6. **Adjust as needed** (usual after looking at the spectrum, and done before picking peaks): to
   change the spectrum centre, use the **Generate FID manual** entry to edit `CAR` and then run it;
   to flip an indirect dimension, select the dimension and click **"Re-run the final script"**;
   anything else, use the **manual** entry, edit the script and run it.
7. **Pick peaks** and export the peak table (POKY / Sparky `.list`). When the picked peaks are not
   what you want, **change the threshold and pick again**, or **set a reference spectrum** and pick
   again (only peaks matching the reference peak table are kept). The export is either
   **export directly** (original coordinates) or **export after alignment** (overall translation
   onto the selected reference peak table); a peak table can also be **imported** from a `.list`.
8. **Collect the results**: export the peak table with `Export peaks`; the processed spectra are in
   the **`spectra` folder** of that dataset's working directory (the NMRPipe final spectra
   `.ft2`/`.ft3`, plus the exported **UCSF**, same name as the final spectrum with a `.ucsf`
   extension).

**Extra entry points**: once the Generate spectrum step has completed, "Re-run the final script" on
the step row reuses the existing script and **does not re-optimise**, changing only the direct
dimension range and the indirect-dimension flips (the flip control and the rerun button share one
box: in 2D the checkbox is a state, in 3D the dropdown is a command); "Reference spectrum" on the
Peak picking step row picks a dataset that already has a peak table as the reference, so that peak
picking keeps only the peaks matching the reference peak table. **Manual path** - any generated
script can be opened in the script editor, edited and run directly; editing a script is a deliberate
override, so it is recorded separately from the automated run, and when a step has failed its row
offers "View log" to jump straight to the log entry.

## Importing segmented acquisitions / averaged repeat experiments

When one acquisition was split into several segments (**segmented NUS**, the segments complementing
each other to fill the sampling grid), or when the same experiment was acquired repeatedly and you
want to average them for better signal-to-noise, use this path: do **not** import the segments as
separate datasets. There are two entries (same effect): tick "segmented data or repeated experiment
overlay import (container directory: multiple subdirectories containing acqus are merged into one
data)" in the import dialog, or use the **"segmented data or repeated experiment overlay import"**
group in the experiment page's import area directly.

A container directory = **no `acqus` at the top level** and **at least 2 subdirectories that each
hold a set of `acqus`** below it; when you pick such a directory the checkbox in the dialog is
**ticked automatically**; if the box is ticked but the directory is not such a structure it reports
an error plainly. After importing, the segments are **merged into one dataset** that goes through
the whole flow (Generate FID / Generate spectrum run once).

## Data groups and batch processing

To **batch-import several independent datasets**, use the **"Batch processing"** group in the
experiment page's import area: add data folders one by one with "Add data folder..." ->
**"Batch import and group (only supports 2D spectra)" is ticked by default** (with it ticked the
imported data lands in one data group that can then be processed together) -> click "Batch import",
and a summary (total / succeeded / failed) follows. Members can also be added and removed by
right-clicking a data group node in the project tree.

**Batch processing is 2D only**: non-2D data is skipped with a reason; failures are isolated per
dataset, so one bad dataset does not abort the whole group.

## Standalone viewer

```bash
nmrforge-viewer          # after an editable install
python -m viewer
```

The viewer opens `.ft2`/`.ft3` files with drag-and-drop, keeps a recent-file list, overlays
spectra with intensity sliders, marks peaks, and supports 3D slicing with projections.

## Where the GUI writes

All state lives in the project directory you chose: the project file, per-dataset working
directories, generated scripts, run records and logs. Deleting project data goes to the operating
system's trash (recoverable) rather than being erased.

**One exception: NUS bad-point cleaning rewrites the raw directory in place.** When it removes bad
points the program **rewrites `ser` and `nuslist` in place** in the raw directory (dropping the
whole row), copying the originals to `ser.bak` / `nuslist.bak` next to them first (an existing
`.bak` is not overwritten), with the log stating `Source cleanup ... (backup .bak)`; the point is to
give the reconstruction clean input. **If your raw data is read-only, or you cannot accept the
original being rewritten, make your own copy first** - the `.bak` in the source directory is the one
to fall back to.
