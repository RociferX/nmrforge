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

- `docs/API_CONTRACT.md` -- Shared Contract definition and change process
- `docs/GUI_ARCHITECTURE_VISION.md` -- user GUI layout vision (design reference)
- `docs/README.md` -- documentation index
- `docs/roadmap.md` -- roadmap and open items

> The development process records (status, decisions, branch workflow) exist only in the private
> development trunk and are **not part of this snapshot**; for the public snapshot use this
> directory's README, the roadmap and the release notes. (2026-09-22 review: the public page used
> to list those three under "Related documents", where no reader could find them.)
