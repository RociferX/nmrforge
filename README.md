# NMRForge

面向 Bruker 1D/2D/3D NMR 数据的自动化处理、参数优化、质量控制与谱图查看平台。

> NMRForge 的目标不只是为 NMRPipe 提供图形界面，而是让软件理解实验和采样方式，生成可解释的处理方案，并用质量指标与运行记录保存处理依据。

## 核心原则

1. 先理解数据，再处理数据；实验识别与处理规划先于脚本执行。
2. 实验类型识别使用 pulse program、核组合、维度顺序、FnMODE 和采集参数等多重证据，不依赖文件名。
3. 逻辑维度、物理维度和显示维度分离。
4. 处理流程使用带缓存和失效语义的依赖图。
5. 昂贵的 NUS reconstruction 与普通参数优化分离。
6. 自动处理以 QC、可回滚产物和可追踪参数为目标；完整 provenance 仍在补齐。

## 架构

```text
GUI / Viewer
    ↓
Workflow：数据理解 → 规划 → 处理 → 优化 → QC → 产物
    ↓
ProcessingBackend
    └── NMRPipeBackend（唯一生产后端；未实现的 native 骨架已删除）
    ↓
core：项目模型、数据读取、实验识别、规划、处理原语、优化与 QC
```

## 当前能力边界

- 已实现 Bruker 1D/2D/3D、uniform/NUS 识别，以及 NMRPipe 处理、自动/人工相位、基线、QC、峰挑选和谱图查看。
- 已实现项目/实验/数据层级、运行记录、数据组、回收站和批量入口；**批量处理为 2D-only 能力边界**，非 2D 数据会被跳过并说明原因。
- SMILE 参数扫描和“按 Rank1 重跑”当前只在 2D NUS 界面开放；扫描只出排名与候选脚本，不自动替换活动谱。
- 3D NUS 处理可用，但 3D SMILE 参数优化入口暂时隐藏。
- 分析（HSQC CSP）功能已于 2026-09-12 按用户决定删除（删除范围与恢复方法见[留档](docs/tasks/archive/2026-09-12-analysis-removal.md)）；13C 显示约定与谱中心调整仍待产品决策。

当前风险与待修复问题见[全项目审查问题台账](docs/reviews/2026-09-12-project-audit.md)，长期规划见[路线图](docs/roadmap.md)。

## 给下游研究项目的接口(`nmrforge_api`)

NMRForge 同时提供一个**无 Qt、可脚本化**的对外接口,供独立研究项目调用
(当前服务「不同处理参数组合对 2D 谱峰位置的影响 / CSP 判据下限」这类研究)。

```python
from nmrforge_api import run_parameter_study

result = run_parameter_study(
    "~/studies/hsqc_params",            # 研究根(可复用/断点续跑)
    "~/data/bmr12345/1",                # 公开库下载解压后的 Bruker 目录
    axes={"zero_fill": [1, 2, 4], "window.F1.off": [0.35, 0.45, 0.55]},
)
print(result.summary["delta_std_ppm"])   # 峰位不确定度 → CSP 下限
```

```bash
python -m nmrforge_api init      --study DIR --dataset BRUKER_DIR
python -m nmrforge_api reference --study DIR
python -m nmrforge_api peaks     --study DIR
python -m nmrforge_api sweep     --study DIR --grid grid.yaml
```

要点:参考谱与参考脚本自动优化后冻结并带哈希;同一份 fid 只转一次;每个参数
组合留下脚本、候选谱(不替换活动谱)与同一批峰的亚像素峰位;结果落成
`manifest.json` / `runs.json` / `peak_positions.csv` / `uncertainty.csv`。
契约与边界见[提案文档](docs/proposals/external-api/001-parameter-sweep-api.md)。

## 开发

```powershell
python main.py
python -m pytest
python -m ruff check .
```

发行物只有 Linux AppImage（`packaging/linux/`）；普通 wheel/pip 仅用于开发（`pip install -e .`），不作为发行方式——运行资源（`config/`、`presets/`、`gui/assets/`）由 PyInstaller 打进 AppImage。

分支为 `master`；提交风格为 `feat:` / `fix:` / `refactor:` / `docs:` + 中文简述。详见[开发规范](docs/development.md)。

## 文档入口

- [文档总入口](docs/README.md)
- [当前项目状态](docs/manager/project_state.md)
- [当前任务](docs/tasks/current.md)
- [开放问题审查](docs/reviews/2026-09-12-project-audit.md)
- [架构设计](docs/architecture.md)
- [变更历史](CHANGELOG.md)
