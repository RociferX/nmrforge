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
| `workflow/` | Backend | stepwise 步骤化 / phase_routes 统一相位 / manual 人工 / batch(2D-only) / 优化 |
| `core/data/`(除 internal_data_model) | Backend | Bruker 读取/nus/pipe_io |
| `core/experiment/`、`core/experiments/` | Backend | 解析/分类/模板 |
| `core/processing/`、`core/planning/` | Backend | 处理原语/DAG |
| `core/optimization/` | Backend | 参数空间/搜索/相位 |
| `core/qc/` | Backend | QC(core/reporting 已于 0.2.164 清理删除;CSP 分析 2026-09-12 删除) |
| `scripts/{smile_optimize,param_optimize}.py` | Backend | 命令行工具(可选) |
| `core/project/` | Shared | 项目管理模型(GUI 地基 + Backend 运行登记) |
| `core/workspace.py` | Shared | 工作区容器(默认 ~/NMRForgeWorkspace,首次启动创建) |
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

项目管理:core/workspace.WorkspaceManager(Shared,首次启动创建默认工作区)
  → core/project.ProjectManager(Shared):
  实验登记 → 数据导入(raw 副本 + metadata)→ WorkflowRun(参数/快照/产物)
  → 状态推断;目录层级即层级(见 API_CONTRACT §9)
```

## 4. 当前跨边界触点(唯一)

`gui/processing.py::ProcessingController`:

- `generate_fid(data, exp_id, data_id, progress)` → `workflow.stepwise.generate_fid`
  → `backend.convert_to_fid`(Backend),返回 fid 路径;
- `generate_spectrum(data, exp_id, data_id, params, progress)` →
  `workflow.stepwise.generate_spectrum` → `phase_routes.unified_route`
  (Backend,统一相位优化),返回谱图路径;
- `manual_param_table()` / `manual_script_editor()`:人工路径占位接口。

除此外,`gui/` 与 `viewer/` 只依赖 Shared Contract,无其它 Backend import。
契约变化见 docs/API_CONTRACT.md,必须走 Proposal。

## 5. 处理双路径

- **自动化**:ProcessingController → workflow.stepwise → phase_routes.unified_route
  (理解→处理→优化→QC),后端走 NMRPipe/SMILE;
- **人工(已实现)**:workflow/manual——fid.com 查看/修改/运行,谱图脚本
  编辑/运行,产物归位并登记 WorkflowRun。

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
