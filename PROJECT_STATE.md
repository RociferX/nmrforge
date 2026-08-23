# 项目状态交接

更新时间：2026-08-11

> 注:本文件为 2026-08-11 快照;0.2.164 起部分模块已清理删除
> (旧 git 历史保留),以 CHANGELOG / docs/development.md 为准。

## 一句话定位

面向 Bruker 2D/3D NMR 数据的自动化处理、参数优化与质量控制平台：
先理解数据（分类/维度映射/NUS 检测）→ 规划（DAG）→ 处理 → 优化 → QC → 报告，
所有自动操作带置信度、回滚与完整参数记录。

## 模块表

| 模块 | 状态 | 职责 |
| --- | --- | --- |
| core/project | 已实现 | 项目/实验/样本/运行记录/审计历史/最近项目（GUI 地基，见 core/project） |
| core/data | 实现中（Phase 1） | 内部数据模型、Bruker 元数据 + ser/fid 二进制读取（read_data） |
| core/experiment | 实现中（Phase 1） | Bruker 参数解析、基础分类、维度映射、NUS/采集模式检测 |
| core/planning | 实现中（Phase 1） | DAG 拓扑排序/缓存键、默认处理计划、轴计划 |
| core/processing | 实现中（Phase 1-3） | 原生处理原语 + 超复数合并（NUS 重建走 NMRPipe SMILE） |
| core/optimization | 骨架（EarlyStopping/评分已实现） | 参数空间、候选生成、搜索算法、预算 |
| core/qc | 实现中（Phase 1） | 噪声/SNR/峰检测/相位/基线/伪影/综合质量（峰稳定待 Phase 4） |
| core/experiments | 模板占位 | 实验模板注册表 + HSQC/HNCA 等先验 |
| core/reporting | 已删除(0.2.164) | 处理报告/QC/溯源未接入产品,已清理(旧 git 保留) |
| backend | 实现中（Phase 1-3） | NMRPipe 后端（bruker -AUTO + 管道 + SMILE + 多段 addNMR 合并）、查找器、csh 运行时 |
| workflow | 已实现(0.2.163) | stepwise + phase_routes(unified) + manual + batch + smile/baseline/window 优化 |
| viewer | 已实现（0.2.6） | 独立谱图查看：Spectrum/SpectrumAxis(ppm 轴)、ContourLayer(Poky 风格多级数/抗锯齿插值)、交互(框选缩放/中键平移/滚轮缩放/长宽比)、独立窗口 |
| gui | 已重构（0.2.8） | 简洁流程化布局（CryoSPARC 风格）：导入/处理/查看/报告四步；处理分自动化与人工两条路径（人工占位） | 主窗口与五大面板（Dataset/Experiment/Plan/Viewer/Quality） |
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
- GUI 已重构为流程化布局；自动化处理走 stepwise/unified；人工路径(fid.com/脚本编辑)已实现(0.2.163-补13/14)。
- config/nmrforge.local.yaml 不应提交（可能含敏感信息）。
- SMILE 大网格（>5000 间接点）必须限线程（护栏已内置）；data/12 为 2D NUS 但缺 nuslist，需补采样表后才能处理。
- numpy 限制 <2.5（nmrglue 0.11 的 dtype 别名问题）。

## 下一步

0. 项目管理模块完成：core/project（实验/样本/运行记录/快照/模板提取），GUI 主窗口骨架已接线。
0.5 独立谱图查看模块完成：viewer（Poky 风格等高线/峰标记/缩放拖拽/长宽比/独立窗口）。
1. Phase 1 已起步：Bruker parser + 内部数据模型 + NUS/采集模式检测 + 基础分类 + 轴映射（已测）。
2. 建立 FakeBackend 测试基座（沿用 NMRFlow 测试经验，不依赖真实 NMRPipe）。
3. ProcessingPlan DAG 拓扑排序与缓存命中逻辑。
