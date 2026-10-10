# Architecture

NMRForge 1.0.5 combines a Qt desktop application, a reusable data and processing core, workflow
orchestration, an NMRPipe backend, and a separate Python/CLI API (API 1.1.1). The components share
the same project and processing capabilities, but have different entry points and output records.

```text
Desktop: gui/ ──> ProcessingController ──> workflow/ ──> core/ data, planning and project services
                                             │                    │
                                             └──── backend/ <─────┘
                                                   │
                                                   └── NMRPipe / SMILE

Python/CLI API: nmrforge_api/ ──> core/, workflow/ and backend/
Viewer: viewer/ ──> NMRPipe spectrum files (.ft1/.ft2/.ft3)
```

This is a responsibility map rather than a strict dependency ladder: workflows coordinate core
domain operations and backend execution, while selected workflow and core operations also use
backend runtime or configuration services. NMRPipe-specific command semantics live at the backend
boundary and in its script generator.

## Components and data flow

- `gui/` contains the main window, project tree, pipeline and group panels, import and script
  dialogs, settings, and logs. It accesses project state through `core.project` and invokes
  processing through `gui.processing.ProcessingController`.
- `nmrforge_api/` provides the Qt-free Python and CLI entry points for version 1.1.1. It builds
  reference products and runs explicit parameter combinations; its records and outputs belong to
  API sessions rather than the active desktop project. See the [API contract](API_CONTRACT.md).
- `workflow/` implements application operations: Bruker import, the import/FID/spectrum steps,
  batch runs, manual scripts, processing routes, peak picking, and export. It combines domain
  objects and plans from `core/` with the backend protocol.
- `core/` parses Bruker metadata and data, models experiments and sampling, classifies experiments,
  selects processing plans, and provides project, workspace, optimization, QC, and peak services.
  `read_dataset()` builds an `Experiment` from acquisition files, detects sampling, and classifies
  its experiment type. Project services own project records and paths.
- `backend/` implements `ProcessingBackend`. The factory currently selects `NMRPipeBackend`, which
  converts Bruker inputs, runs uniform processing or NUS reconstruction, and returns result paths,
  effective parameters, metrics, and logs.
- `viewer/` reads processed NMRPipe files and renders 1D, 2D, or 3D spectra. It can run embedded in
  the desktop application or from its standalone viewer entry point.
- `nmrforge_data/` supplies packaged defaults, presets, and other runtime resources. User-local
  paths and settings are resolved by the configuration layer.

In the desktop path, import stores or references Bruker input under a project data entry and writes
metadata and run records through project services. The FID step calls backend conversion. The
spectrum step selects the uniform `process()` or NUS `reconstruct_nus()` route, then registers the
result under the data entry's spectra directory. The viewer reads those spectrum files for display.
The API reuses lower-level data and processing services but creates its own study/session products.

NMRPipe and SMILE are external programs. `backend/runtime.py` runs their C-shell commands as
subprocesses and returns output for progress and run logs. See [external dependencies](external-dependencies.md),
the [GUI architecture](gui/architecture.md), and the [backend architecture](backend/architecture.md).
