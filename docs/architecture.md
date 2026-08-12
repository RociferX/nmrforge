# NMRForge 架构

## 1. 分层总览

```text
GUI Agent                            Backend Agent
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
                                     │  reporting           │
                                     └──────────────────────┘
```

依赖方向:GUI → Shared Contract ← Backend。GUI 不直接接触 NMRPipe 语法,
Backend 不依赖 Qt。

## 2. 目录归属

| 路径 | Owner | 说明 |
| --- | --- | --- |
| `gui/` | GUI | 主窗口/对话框/处理控制/面板 |
| `viewer/` | GUI | 独立谱图查看器(含读谱契约实现) |
| `main.py` | GUI | 程序入口(venv 引导 + Qt 启动) |
| `scripts/make_icon.py` | GUI | 图标 |
| `backend/` | Backend | NMRPipe/SMILE 后端与运行时 |
| `workflow/` | Backend | AutoProcessor/管线/优化 |
| `core/data/`(除 internal_data_model) | Backend | Bruker 读取/nus/pipe_io |
| `core/experiment/`、`core/experiments/` | Backend | 解析/分类/模板 |
| `core/processing/`、`core/planning/` | Backend | 处理原语/DAG |
| `core/optimization/` | Backend | 参数空间/搜索/相位 |
| `core/qc/`、`core/reporting/` | Backend | QC/报告 |
| `scripts/{smile_optimize,param_optimize,recon_phase_search}.py` | Backend | 命令行工具 |
| `core/project/` | Shared | 项目管理模型(GUI 地基 + Backend 运行登记) |
| `core/data/internal_data_model.py` | Shared | Experiment/Dimension/Sampling |
| `backend/base.py` | Shared | ProcessingBackend Protocol |
| `viewer/spectrum.py` | Shared | Spectrum/SpectrumAxis(读谱契约) |
| `gui/processing.py` | Shared(实现属 GUI) | ProcessingController 跨边界适配 |
| `pyproject.toml`/`.gitignore`/`config/` | Shared | 工程配置 |
| `.codex/`、`docs/`、`scripts/check_ownership.py` | Architect | 协作基础设施 |

## 3. 核心数据流

```text
Bruker 目录 → core/data/bruker_reader.read_dataset → Experiment(Shared)
  → backend.process / reconstruct_nus → 谱图文件(ft2/ft3)
  → viewer/spectrum.Spectrum(Shared) → SpectrumViewer 展示

项目管理:core/project.ProjectManager(Shared)贯穿:
  实验登记 → WorkflowRun(参数/脚本快照/产物) → 状态推断
```

## 4. 当前跨边界触点(唯一)

`gui/processing.py::ProcessingController`:

- `auto_run_sync(entry)`:entry(source 目录)→ `read_dataset`(Backend)→
  `create_backend(config)`(Backend)→ `AutoProcessor(backend).run`(Backend)
  → `{status, message, logs}` 回 GUI;
- `auto_run_async(...)`:后台线程包装;
- `manual_param_table()` / `manual_script_editor()`:人工路径占位接口。

除此外,`gui/` 与 `viewer/` 只依赖 Shared Contract,无其它 Backend import。
契约变化见 docs/API_CONTRACT.md,必须走 Proposal。

## 5. 处理双路径

- **自动化**:ProcessingController → AutoProcessor(理解→规划→处理→QC→报告),
  后端可用时走 NMRPipe/SMILE;
- **人工(待实现)**:参数表格逐阶段改参数生成确定性 .com 脚本;或
  脚本编辑器(模仿 VSCode)直接编辑 fid.com/process.com/nus*.com。
  两条入口接口已在 `gui/processing.py` 占位。

## 6. 测试分层

- Backend 测试:不依赖 Qt;NMRPipe 逻辑用 fake/合成数据;
- GUI 测试:offscreen,不依赖真实后端(monkeypatch ProcessingController);
- VM 回归:真实 NMRPipe/SMILE 数据验证(ssh 到 VM 运行)。

## 7. 相关文档

- docs/API_CONTRACT.md — Shared Contract 定义与变更流程
- docs/PROJECT_STATUS.md — 状态与未完成项
- docs/DECISIONS.md — 决策记录
- docs/GIT_WORKFLOW.md — 分支协作
- docs/GUI_ARCHITECTURE_VISION.md — 用户 GUI 布局愿景(设计参考)
