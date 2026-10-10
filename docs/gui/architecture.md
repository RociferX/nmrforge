# GUI architecture

The desktop application is assembled by `gui/main_window.py`. `MainWindow` owns the active
`ProjectManager`, `ProcessingController`, project tree, pipeline/center panels, log panel, and the
spectrum panel. Project selection changes the manager's active project; project JSON and data-entry
updates go through `core.project` services. The spectrum panel is built after startup prewarming in
the normal application entry path so the window can appear before the heavier plotting modules are
loaded.

## Control and processing

Widgets emit typed Qt signals for user actions such as import, step execution, manual-script
editing, opening a spectrum, and stopping a task. `MainWindow` and `PipelinePanel` connect these
requests to `ProcessingController` and project services. The controller adapts GUI operations to
`workflow/` functions: import, generate FID, generate spectrum, peak picking, manual processing,
and group batch processing. It resolves processing parameters and records step success and script
snapshots with the project manager.

Long-running import and processing work runs in daemon `threading.Thread` workers. Progress,
completion, and failure are sent back through Qt signals; slots update widgets on the GUI thread.
The worker does not own the UI. The controller creates its backend lazily per worker thread using
thread-local storage, so concurrent jobs do not share an `NMRPipeBackend`'s mutable working
directory. The pipeline tracks each active `(experiment, data, step)` target and emits start and
finish signals to update that data row. The stop action calls the backend runtime cancellation
path. `CshRuntime` registers child processes so cancellation can stop the running command tree.

Logs are routed to the visible `LogPanel` by Qt signals. A log can be global, scoped to a data item,
or scoped to a group; task output is also retained in the corresponding run record. The GUI keeps
references to non-modal script editor dialogs, keyed by data and step, until those dialogs close.
This keeps the active editor alive and prevents duplicate editors for the same data-step pair.

## Spectrum loading and display

`gui/spectrum_panel.py` scans the selected data entry's spectra directory and opens a chosen
`.ft1`, `.ft2`, or `.ft3` file. Smaller 2D files can be read synchronously; large 2D loads and 3D
loads run in background workers. A monotonically increasing load token identifies the latest
selection. Results and errors from stale workers are discarded when their token or path no longer
matches the current selection. After a successful processing run, a signal reloads the spectrum for
the frozen experiment/data target.

For a 3D `.ft3`, `Spectrum3D.load_from_ft3(..., lazy=True)` reads the header and axis information
first. Stream-format files use `nmrglue` low-memory access to read planes on demand; unsupported
layouts fall back to full loading. The first plane and noise estimate are prepared in the worker.
The GUI binds the resulting `Spectrum3D` to `Spectrum3DPanel`, displays the selected 2D plane in
`SpectrumViewer`, then streams the remaining planes in the background. Plane data are cached on the
`Spectrum3D` object under a lock with a bounded cache; a cancellation token stops an obsolete plane
stream when the spectrum or plane axis changes. The panel owns the current 3D object until it is
cleared or replaced, and the viewer owns the current 2D slice objects used for rendering.

The plane selector fixes one logical axis (F1, F2, or F3); the other two axes form the displayed
2D slice. The slider selects an index along the fixed axis and the ppm field maps a chemical-shift
value to its nearest index. The panel emits `slice_changed`, and the main window asks the viewer to
replace the current slice. `SpectrumViewer.update_spectrum_data()` updates the existing image layer
when the view is a single 3D slice, avoiding a full clear and re-creation. 2D/3D axis metadata is
read from NMRPipe headers: data axes are mapped to FDF blocks through `FDDIMORDER`, and logical
axis labels and ppm coordinates are kept with each axis object. The spectrum loader records the
original dimension indices on slices so peak coordinates remain associated with their 3D axes.

The standalone viewer entry is `viewer/app.py`; it uses the same spectrum objects, panel, and
viewer widget. For user-facing behavior, see the [GUI guide](../gui.md); for the shared data and
processing map, see [architecture](../architecture.md).
