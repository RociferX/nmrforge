# GUI guide

The desktop application is the intended interface for processing spectra. This page is a tour of
what the interface does; the underlying engine behaviour is in
[processing-model.md](processing-model.md) and [qc-system.md](qc-system.md).

![NMRForge main window](../gui/assets/nmrforge.png)

> **Language note.** English is the interface's source language, so the menus, panel labels,
> statuses and messages are English by default. The same build also speaks Chinese: follow the
> system language, pick it in `Settings -> Software settings -> Interface language`, or pin it
> with `NMRFORGE_LANG=zh`. A Chinese edition of this guide is in
> [`Chinese_version/docs/gui.md`](../Chinese_version/docs/gui.md).

## Starting it

```bash
python main.py    # v1.0.0 source entry point (the AppImage starts it internally)
```

The v1.0.0 Linux AppImage is on the releases page (one artefact, interface language switched at
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

1. **Create or open a project**, then **import** a Bruker dataset directory (right-click the
   experiment node in the project tree and choose "import sample data..."). Only directories
   containing Bruker parameter files are accepted, and multiple segments of one experiment are
   offered as separate runs of the same experiment.
2. **Check what was understood**: experiment type with its evidence, sampling classification, and
   the dimension layout. If the classification is wrong, the experiment template can be corrected
   before processing, which is much cheaper than fixing a processed spectrum.
3. **Generate FID**, then **generate the spectrum**. Data diagnostics run first and their findings
   are written to the log before any optimisation step.
4. **Read the report** at the end of the run: final spectrum quality (with signal-to-noise, phase,
   baseline and artefact sub-scores), the data-quality diagnostics section, and the resolved
   processing parameters, including anything the optimiser chose.
5. **Inspect the spectrum** in the right-hand panel: zoom with the mouse, pan with the middle
   button, overlay spectra, and click in the peak table to jump to a peak.
6. **Pick peaks** and export the peak table (POKY style). The spectrum can also be exported to
   UCSF for use in other software.
7. **Manual path** - any generated script can be opened in the script editor, edited and run
   directly. Editing a script is a deliberate override, so it is recorded separately from the
   automated run.

## Data groups and batch runs

Data can be organised into groups and processed together: tick "batch import and group (2D spectra
only)" while importing, or add and remove members by right-clicking a group node in the project
tree. Batch processing is **2D-only**: non-2D datasets are skipped and the reason is stated.
Failures are isolated per dataset so one bad dataset does not abort the whole group.

## Standalone viewer

```bash
nmrforge-viewer          # after an editable install
python -m viewer
```

The viewer opens `.ft2`/`.ft3` files with drag-and-drop, keeps a recent-file list, overlays
spectra with intensity sliders, marks peaks, and supports 3D slicing with projections.

## Where the GUI writes

All state lives in the project directory you chose: the project file, per-dataset working
directories, generated scripts, run records and logs. The GUI never writes into your raw data
directory, and anything it removes goes to the operating system trash rather than being erased.
