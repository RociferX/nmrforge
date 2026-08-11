# 项目状态交接

更新时间：2026-08-11

## 一句话定位

面向 Bruker 2D/3D NMR 数据的自动化处理、参数优化与质量控制平台：
先理解数据（分类/维度映射/NUS 检测）→ 规划（DAG）→ 处理 → 优化 → QC → 报告，
所有自动操作带置信度、回滚与完整参数记录。

## 模块表

| 模块 | 状态 | 职责 |
| --- | --- | --- |
| core/data | 实现中（Phase 1） | 内部数据模型、Bruker 元数据 + ser/fid 二进制读取（read_data） |
| core/experiment | 实现中（Phase 1） | Bruker 参数解析、基础分类、维度映射、NUS/采集模式检测 |
| core/planning | 实现中（Phase 1） | DAG 拓扑排序/缓存键、默认处理计划、轴计划 |
| core/processing | 实现中（Phase 1-3） | 原生处理原语 + 超复数合并（NUS 重建走 NMRPipe SMILE） |
| core/optimization | 骨架（EarlyStopping/评分已实现） | 参数空间、候选生成、搜索算法、预算 |
| core/qc | 实现中（Phase 1） | 噪声/SNR/峰检测/相位/基线/伪影/综合质量（峰稳定待 Phase 4） |
| core/experiments | 模板占位 | 实验模板注册表 + HSQC/HNCA 等先验 |
| core/reporting | 骨架 | 处理报告、QC 报告、参数溯源 |
| backend | 实现中（Phase 1-3） | NMRPipe 后端（bruker -AUTO + 管道 + SMILE + 多段 addNMR 合并）、查找器、csh 运行时 |
| workflow | 实现中（Phase 1） | PipelineRunner + AutoProcessor.run（NMRPipe/原生双路径，NUS 待 Phase 3） |
| gui | 占位 | 主窗口与五大面板（Dataset/Experiment/Plan/Viewer/Quality） |
| packaging | 规划 | AppImage 打包：desktop/icon/PyInstaller spec/构建脚本（docs/packaging.md） |
| presets | YAML 示例 | 实验模板（先验/约束/期望行为） |
| config | 默认配置 | backend/optimization/qc/reporting 默认值 |

## 设计原则

1. 先理解数据，再处理数据。
2. 实验类型识别必须使用多证据，而不是文件名。
3. 逻辑维度、物理维度和显示维度必须分离。
4. 所有处理参数都必须有依赖关系（invalidates）。
5. 所有昂贵结果必须缓存。
6. NUS reconstruction 不应该被普通参数优化反复触发。
7. Direct dimension 优先独立优化。
8. NUS reconstruction 使用低成本 preview + 少量候选 + 局部优化（带预算与 early stopping）。
9. Indirect dimension 在最佳 reconstruction 上独立优化。
10. 任何自动处理都必须有 QC、置信度、回滚和完整记录。

## 已知问题 / 未实现

- 所有 processing / optimization / qc 算法为占位，等待 Phase 1-4 实现。
- GUI 尚未接入任何信号与后端。
- config/nmrforge.local.yaml 不应提交（可能含敏感信息）。
- numpy 限制 <2.5（nmrglue 0.11 的 dtype 别名问题）。

## 下一步

1. Phase 1 已起步：Bruker parser + 内部数据模型 + NUS/采集模式检测 + 基础分类 + 轴映射（已测）。
2. 建立 FakeBackend 测试基座（沿用 NMRFlow 测试经验，不依赖真实 NMRPipe）。
3. ProcessingPlan DAG 拓扑排序与缓存命中逻辑。
