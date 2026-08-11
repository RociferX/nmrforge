# NMRForge

面向 Bruker 2D/3D NMR 数据的自动化处理、参数优化与质量控制平台（重写版）。

> 定位：不是简单的 NMRPipe GUI，而是「软件自己知道这个谱是什么、应该怎么处理、
> 为什么这样处理、结果是否足够好，以及下一步应该改变什么」的自动化平台。

## 核心原则

1. 先理解数据，再处理数据（实验识别与处理规划先于处理）。
2. 实验类型识别使用多证据（pulse program → 核组合 → 维度顺序 → FnMODE → 参数 → 命名），不依赖文件名。
3. 逻辑维度 / 物理维度 / 显示维度分离。
4. 处理流程是依赖图（DAG），带缓存与 invalidates 语义。
5. 昂贵的 NUS reconstruction 从普通参数优化循环中隔离（direct → reconstruction → indirect 分阶段）。
6. 每次自动操作都有 QC、置信度、回滚与完整参数记录（provenance）。

## 架构分层

```text
GUI（Dataset / Experiment / Plan / Viewer / Quality 面板）
  │
Workflow（AutoProcessor：理解 → 规划 → 处理 → 优化 → QC → 报告）
  │
ProcessingBackend 协议（backend/base.py）
  ├── NMRPipeBackend（NMRPipe 语义，仅此层接触）
  └── NativeBackend（长期目标）
  │
core/（项目管理 project / 数据模型 / 实验理解 / 规划 DAG / 处理原语 / 优化 / QC / 实验模板 / 报告）
```

## 当前状态

- [x] 项目工作树（2026-08-11 初始化）
- [x] 项目管理模块 core/project（项目/实验/样本/WorkflowRun/审计历史/最近项目）+ GUI 主窗口骨架
- [x] 独立谱图查看模块 viewer（Poky/nmrDraw 风格:多级数等高线/峰标记/缩放拖拽/长宽比）,可通过 `nmrforge-viewer` 独立启动
- [ ] Phase 1：Bruker 解析 / 2D-3D 与 uniform-NUS 检测 / 轴映射 / 基础处理 / 自动相位 / 基线 QC / 噪声估计 / 质量评分
- [ ] Phase 2：实验分类器 + HSQC/HNCA/HNCO/HNCACB/CBCANH 模板
- [ ] Phase 3：NUS 管线（direct 优化 → reconstruction 优化 → indirect 优化 → 缓存）
- [ ] Phase 4：peak stability / Bayesian 优化 / 处理报告

详见 docs/roadmap.md。

## 开发

```bash
python main.py          # 首次运行自动创建 venv 并安装
pytest                  # 全量测试
ruff check .            # 静态检查
```

分支 `master`；提交风格 `feat:` / `fix:` / `refactor:` / `docs:` + 中文简述。
详见 docs/development.md。

## 文档索引

- docs/architecture.md：新架构设计
- docs/development.md：开发流程与 Windows 沙箱经验
- docs/roadmap.md：路线图（Phase 1-4）
- PROJECT_STATE.md：开发状态交接
- CHANGELOG.md：变更史
