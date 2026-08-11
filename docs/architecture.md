# 架构设计（NMRForge 重写）

## 分层总览

```text
GUI（Dataset / Experiment / Plan / Viewer / Quality 面板）
  │
Workflow（AutoProcessor：理解 → 规划 → 处理 → 优化 → QC → 报告）
  │
ProcessingBackend 协议（backend/base.py）
  ├── NMRPipeBackend（NMRPipe 语义，仅此层与运行时接触）
  └── NativeBackend（长期目标，逐步替换 NMRPipe）
  │
core/（数据模型 / 实验理解 / 规划 DAG / 处理原语 / 优化 / QC / 实验模板 / 报告）
```

## 与旧版 NMRFlow 的继承关系

保留：

- 分层思想：GUI → 编排引擎 → Backend 协议 → 运行时，NMRPipe 语义隔离。
- 可复现核心：每次处理生成完整参数记录（新架构升级为 processing_recipe + provenance）。
- 确定性脚本生成、长任务队列 + 主线程轮询（PyQt6 跨线程信号会原生崩溃）。
- 原子写、history 只追加、参数权威源 acqus/acqu2s。

演进：

- 从「固定 pipeline 预设」→「ProcessingPlan（DAG）+ 方法选择 + 缓存」。
- 从「NMRPipe 语义散布」→「统一内部数据模型，backend 可替换」。
- 新增优化引擎（预算/early stopping/候选管理）与 QC 控制器（评分/回滚/溯源）。
- 实验类型识别升级为多证据分类（pulse program → 核组合 → 维度顺序 → FnMODE → 参数 → 命名）。

## 核心模块职责

| 模块 | 职责 |
| --- | --- |
| core/data | 统一内部数据模型（Experiment/Dimension/Sampling）、Bruker/NUS 读取、格式转换 |
| core/experiment | Bruker 参数解析、实验分类、维度映射、采样与采集模式检测 |
| core/planning | ProcessingPlan（DAG）、AxisPlan、方法选择、依赖图与缓存语义 |
| core/processing | 处理原语：apodization/ZF/FT/phase/baseline/calibration/transpose/sign/NUS 重建 |
| core/optimization | 参数空间、候选生成、评分（多目标）、网格/局部/贝叶斯搜索、early stopping、预算 |
| core/qc | 噪声/SNR/相位/基线/伪影/峰检测/峰稳定/综合质量（before/after + 回滚） |
| core/experiments | 实验模板（先验/约束/期望行为）注册表 |
| core/reporting | 处理报告、QC 报告、参数 provenance、recipe 导出 |
| backend | ProcessingBackend 协议 + NMRPipe/Native 实现 + 工厂 |
| workflow | AutoProcessor 编排、批处理、管线执行 |
| gui | 主窗口与五大面板（PyQt6，骨架阶段占位） |

## 关键数据流（Auto 模式）

```text
dataset
  → 理解：分类 + 维度映射 + NUS 检测（含置信度，低置信度走 Generic）
  → 规划：DAG（direct → reconstruction → indirect）+ 方法选择
  → 处理：direct 维（apodization/ZF/FT/粗 phase）
  → 优化：NUS 候选（≤预算）→ 最佳重建 → indirect 优化
  → QC：before/after 评分，变差自动回滚
  → 报告：spectrum + recipe + QC + warnings + 优化轨迹
```

## AI/LLM 的合理位置（框架 §58-59）

算法负责执行（评分/搜索/QC），LLM 负责解释（warning 原因、参数建议、报告生成）；
视觉模型可作为 numeric QC 的补充，但不替代。
