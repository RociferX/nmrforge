# NMRForge architecture

## 1. Hierarchical overview

```text
Desktop application                  Backend
┌──────────────┐   Shared Contract   ┌──────────────────────┐
│ gui/         │◄───────────────────►│ backend/             │
│  main_window │  ProcessingBackend  │  nmrpipe_backend     │
│  dialogs     │  Experiment         │  script_generator    │
│  processing  │  Spectrum           │  runtime/finder      │
│ viewer/      │  ProjectInfo        │ workflow/            │
│  spectrum    │  ProcessingResult   │  engine/pipeline     │
│  viewer/app  │                     │ core/{data,...}      │
└──────────────┘                     │  processing/planning │
                                     │  optimization/qc     │
                                     └──────────────────────┘
```

Dependency direction: GUI -> Shared Contract ← Backend. GUI does not directly contact NMRPipe syntax.
Backend does not depend on Qt.

The boundary keeps calculations independent of Qt and confines NMRPipe syntax to the backend and
generated scripts. Both sides are maintained in one source tree and released together.

## 2. Directory ownership

| Path | Owner | Description |
| --- | --- | --- |
| `gui/` | GUI | main window / dialogs / processing controls / panels |
| `viewer/` | GUI | standalone spectrum viewer (implements the spectrum-reading contract) |
| `main.py` | GUI | entry point (venv bootstrap + Qt startup) |
| `scripts/make_icon.py` | GUI | icon |
| `backend/` | Backend | NMRPipe/SMILE backend and runtime |
| `workflow/` | Backend | stepwise / `phase_routes` unified phase / manual / batch (2D-only) / optimisation |
| `core/data/` (except internal_data_model) | Backend | Bruker reading / nus / pipe_io |
| `core/experiment/`, `core/experiments/` | Backend | parsing / classification / templates |
| `core/processing/`, `core/planning/` | Backend | processing primitives / DAG |
| `core/optimization/` | Backend | parameter space / search / phase |
| `core/qc/` | Backend | QC (`core/reporting` was removed in 0.2.164; the CSP analysis was removed on 2026-09-12) |
| `scripts/{smile_optimize,param_optimize}.py` | Backend | command-line tools (optional) |
| `core/project/` | Shared | project-management model (GUI foundation + Backend run registration) |
| `core/workspace.py` | Shared | workspace container (default ~/NMRForgeWorkspace, created on first start) |
| `core/data/internal_data_model.py` | Shared | Experiment/Dimension/Sampling |
| `backend/base.py` | Shared | ProcessingBackend Protocol |
| `viewer/spectrum.py` | Shared | Spectrum/SpectrumAxis (the spectrum-reading contract) |
| `gui/processing.py` | Shared (implementation owned by GUI) | ProcessingController cross-boundary adapter |
| `pyproject.toml` / `.gitignore` / `nmrforge_data/` | Shared | project configuration and shipped data |
| `docs/`, `scripts/check_ownership.py` | Shared | documentation and the ownership-boundary check |

## 3. Core data flow

```text
Bruker directory -> core/data/bruker_reader.read_dataset -> Experiment(Shared)
  -> backend.process / reconstruct_nus -> spectrum file(ft2/ft3)
  -> viewer/spectrum.Spectrum(Shared) -> SpectrumViewer display

Project management: core/workspace.WorkspaceManager(Shared, creates the default workspace on first start)
  -> core/project.ProjectManager(Shared):
  experiment registration -> data import(raw copy + metadata) -> WorkflowRun(parameters / snapshots / products)
  -> status inference; the directory hierarchy is the hierarchy(see API_CONTRACT §9)
```

## 4. Main GUI-to-workflow entry points

`gui/processing.py::ProcessingController`:

- `generate_fid(data, exp_id, data_id, progress)` → `workflow.stepwise.generate_fid`
  -> `backend.convert_to_fid`(Backend), returns the fid path;
- `generate_spectrum(data, exp_id, data_id, params, progress)` →
  `workflow.stepwise.generate_spectrum` → `phase_routes.unified_route`
  (Backend, unified phase optimisation), returns spectrum path;
- Manual scripts run through `workflow.manual`, which records the executed script, parameter
  differences, status and products in the workflow record.

The GUI uses the shared processing contract to reach backend workflows. See the
[Python API and processing documentation](python-api.md) for supported scripting interfaces.

## 5. Handling dual paths

- **Automation**:ProcessingController -> workflow.stepwise -> phase_routes.unified_route
  (Understanding -> Processing -> optimisation -> QC), the backend goes NMRPipe/SMILE;
- **Manual**: `workflow.manual` supports reviewing and running `fid.com` and spectrum scripts,
  then registering the outputs and run details.

## 6. Test layering

- Backend test: does not depend on Qt;NMRPipe logic uses fake/synthetic data;
- GUI Test: offscreen, does not rely on the real backend (monkeypatch ProcessingController);
- Real-engine regression: NMRPipe/SMILE validation on a machine that has the licensed engine

## 7. Related documents

- `docs/README.md` -- documentation index
