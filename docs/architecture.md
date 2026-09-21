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

**Development strategy**: the desktop application (`gui/`, `viewer/`) and the backend
(`backend/`, `workflow/`, `core/`) are developed **separately** against this shared contract.
Each side can be built, reviewed and tested on its own - the backend never imports Qt, the GUI
never writes NMRPipe syntax - and the two sides are **integrated and unified** through the
contract layer before release. Both ship together from one code base.

## 2. Directory ownership

| Path | Side | Description |
| --- | --- | --- |
| `gui/` | GUI | main window/dialog box/processing control/panel |
| `viewer/` | GUI | Independent spectrum viewer (including spectrum reading contract implementation) |
| `main.py` | GUI | Program entry (venv boot + Qt startup) |
| `scripts/make_icon.py` | GUI | icon |
| `backend/` | Backend | NMRPipe/SMILE Backend and runtime |
| `workflow/` | Backend | stepwise / `phase_routes` unified phase / manual / batch (2D only) / optimisation |
| `core/data/` (except internal_data_model) | Backend | Bruker read/nus/pipe_io |
| `core/experiment/`, `core/experiments/` | Backend | parse/Classification/ template |
| `core/processing/`, `core/planning/` | Backend | processing primitives/DAG |
| `core/optimisation/` | Backend | parameter space/search/ phase |
| `core/qc/` | Backend | QC(core/reporting Cleaned and deleted on 0.2.164; CSP Analysis and deleted on 2026-09-12) |
| `scripts/{smile_optimize,param_optimize}.py` | Backend | Command line tool (optional) |
| `core/project/` | Shared | Project management model (GUI Foundation + Backend operation registration) |
| `core/workspace.py` | Shared | Workspace container (default ~/NMRForgeWorkspace, created on first startup) |
| `core/data/internal_data_model.py` | Shared | Experiment/Dimension/Sampling |
| `backend/base.py` | Shared | ProcessingBackend Protocol |
| `viewer/spectrum.py` | Shared | Spectrum/SpectrumAxis(Music Reading Contract) |
| `gui/processing.py` | Shared(implementation attribute GUI) | ProcessingController cross-border adaptation |
| `pyproject.toml`/`.gitignore`/`nmrforge_data/` | Shared | Project Configuration and shipped data |
| `docs/`, `scripts/check_ownership.py` | Shared | Documentation and the ownership-boundary check |

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

## 4. Current cross-border contact (unique)

`gui/processing.py::ProcessingController`:

- `generate_fid(data, exp_id, data_id, progress)` → `workflow.stepwise.generate_fid`
  -> `backend.convert_to_fid`(Backend), returns the fid path;
- `generate_spectrum(data, exp_id, data_id, params, progress)` →
  `workflow.stepwise.generate_spectrum` → `phase_routes.unified_route`
  (Backend, unified phase optimisation), returns spectrum path;
- `manual_param_table()` / `manual_script_editor()`: Artificial path occupancy interface

In addition, `gui/` and `viewer/` only rely on Shared Contract and have no other Backend import.
For contract changes, see docs/API_CONTRACT.md, Proposal must be used.

## 5. Handling dual paths

- **Automation**:ProcessingController -> workflow.stepwise -> phase_routes.unified_route
  (Understanding -> Processing -> optimisation -> QC), the backend goes NMRPipe/SMILE;
- **Artificial (implemented)**:workflow/manual -- fid.com Check/Revise/run, spectrum script
  Edit/run, the product is returned and registered WorkflowRun.

## 6. Test layering

- Backend test: does not depend on Qt;NMRPipe logic uses fake/synthetic data;
- GUI Test: offscreen, does not rely on the real backend (monkeypatch ProcessingController);
- Real-engine regression: NMRPipe/SMILE validation on a machine that has the licensed engine

## 7. Related documents

- Docs/API_CONTRACT.md -- Shared Contract definition and change process
- Docs/PROJECT_STATUS.md -- Status and unfinished items
- Docs/DECISIONS.md -- Decision record
- Docs/GIT_WORKFLOW.md -- Branch collaboration
- Docs/GUI_ARCHITECTURE_VISION.md -- user GUI layout vision (design reference)
