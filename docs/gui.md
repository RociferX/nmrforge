# GUI guide

The desktop application is the intended interface for processing spectra. This page is a tour of
what the interface does; the underlying engine behaviour is in
[processing-model.md](processing-model.md) and [qc-system.md](qc-system.md).

![NMRForge main window](../gui/assets/nmrforge.png)

> **Language note: the interface is currently Chinese only.** Menu entries and panel labels are
> Chinese; the code and documentation are bilingual. Adding an English UI translation is a
> planned improvement, not something this release claims.

## Starting it

```bash
./NMRForge-<version>-x86_64.AppImage    # users: the supported distribution
python main.py                          # developers: reuses the local nmrforge/ venv
```

On first launch the AppImage installs its own desktop entry and icon. `--remove-desktop` removes
them, `NMRFORGE_NO_DESKTOP=1` skips the desktop integration.

## Window layout

| Area | What it shows |
| --- | --- |
| Left | Project tree: Project -> Experiment -> Input / Processing / Output / Figures. Selecting a node sets the context for everything else. |
| Middle | Processing pipeline for the selected dataset: a breadcrumb plus the status-driven step list and the "what next" prompt. |
| Middle column | Task log, scoped to the current selection (single dataset, data group, experiment type, or global). It expands automatically. |
| Right | Spectrum panel: the embedded viewer plus the list of spectra produced for the current context. |

Menus: `文件` (File: new/open/save/recent projects), `实验` (Experiment: import data, samples),
`查看` (View: toggle the three columns, open the standalone viewer), `工具` (Tools: data groups,
batch), `设置` (Settings), `帮助` (Help).

## The processing pipeline

Four steps, each with a status - `未就绪` (locked, prerequisites missing), `可运行` (ready),
`运行中` (running), `已完成` (done), `失败` (failed), `已过期` (outdated, an input or parameter
changed afterwards):

| Step | Label | What it does |
| --- | --- | --- |
| 1 | 生成 FID | Converts the raw Bruker data to an NMRPipe FID (`bruker -AUTO` from a generated `fid.com`). |
| 2 | 生成谱图 | Runs the optimised processing path, including SMILE reconstruction for NUS data. |
| 3 | SMILE 优化 | Optional: ranks reconstruction parameter candidates. 2D NUS only; it never replaces the active spectrum by itself. |
| 4 | 峰挑选 | Automatic peak detection with intensity and S/N evaluation. |

Steps unlock in order. A finished step whose inputs or parameters changed is marked `已过期`
rather than silently reused - the step fingerprints its inputs, its script and its parameters.

## Typical session

1. **Create or open a project**, then **import** a Bruker dataset directory (`实验 -> 导入数据`).
   Only directories containing Bruker parameter files are accepted, and multiple segments of one
   experiment are offered as separate runs of the same experiment.
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

Data can be organised into groups and processed together from `工具` (Tools). Batch processing is
**2D-only**: non-2D datasets are skipped and the reason is stated. Failures are isolated per
dataset so one bad dataset does not abort the whole group.

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