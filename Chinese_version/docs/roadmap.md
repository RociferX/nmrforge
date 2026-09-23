# 路线图

## 第一阶段:数据理解与基础处理

```text
Bruker 解析 -> 2D/3D 判定 -> uniform/NUS 判定 -> 轴映射 -> 基础处理
-> 自动相位校正 -> 基线 QC -> 噪声估计 -> 质量分
```

已交付:

- core/experiment:parse_dataset_params / sampling_detector / dimension_mapper / acquisition_mode_detector
- core 处理原语:apodization / zero_fill / ft / phase / baseline / calibration
- core/planning:ProcessingDag.execution_order(拓扑排序)+ PipelineRunner 缓存
- core/qc:noise / snr / phase_quality / baseline_quality / spectrum_quality.evaluate
- backend:NMRPipeBackend.health_check / process(确定性的 .com 生成)
- AutoProcessor.run —— 最小闭环(uniform 2D)

## 第二阶段:实验判定

- 多证据判定器(脉冲程序 -> 核组合 -> 维度顺序 -> FnMODE -> 参数 -> 命名)
- ExperimentTemplate.from_yaml + 加载 presets/*.yaml
- HSQC / HNCA / HNCO / HNCACB / CBCANH 模板匹配,带置信度门槛

## 第三阶段:NUS 流水线

```text
直接维优化(代表性数据子集)-> 重构候选(快速预览 + 少量候选 + 局部搜索 + 提前停止)
-> 最优重构 -> 间接维优化 -> DAG 缓存
```

已交付:NusReconstructionParams 接入后端(SMILE)、CandidateGenerator / GridSearch /
LocalSearch、OptimizationBudget 执行、NUS 质量分(数据一致性 + 信噪比 + 峰质量 + 稳定性
- 伪影)。

## 第四阶段:更深入的优化与报告

- 谱中心与参考偏移调整(分析部分):通过改写 ft3/ft2 的 CAR/ORIG 文件头来平移谱轴,
  不动数据本身;这让实验室显示约定与人工校正对齐(机制已于 2026-08-18 验证)
- 峰稳定性分析
- BayesianOptimizer(昂贵任务)
- ProcessingReport:report.json / report.html / processing_recipe.json
- 学习用户最终接受的参数(参数预测器是长期设想;第一版不引入机器学习)

## 最值得先做的优化器

```text
AutoProcessor
├── AxisOptimizer / ApodizationOptimizer / ZFOptimizer
├── PhaseOptimizer / BaselineOptimizer / CalibrationChecker
└── SpectrumQC

NUSOptimizer
├── DirectOptimizer / ReconstructionOptimizer / IndirectOptimizer
├── CandidateManager / CacheManager / StabilityAnalyzer
```

## 明确不在范围内(第一版)

- 完整的结构解析、自动指认、AI 结构预测、NOE。
- 用大模型从图片里猜相位或 NUS 参数;大模型只用于解释与报告。

## v1.0.1 —— 修复版(2026-09-23 已完成)

1.0.0 之后的补丁,**不含新功能**:修的都是「会让结果静默出错」的几处 —— 转换参数(`-xN` 按
`ser` 物理行长,不再按 `acqus` 的 TD 覆盖)、FnMODE → 转换关键字(不再把 `bruker -AUTO`
写对的 `fid.com` 改错)、多段(分段采样 / 重复实验叠加)合并前的组间场漂自动对齐;外加文档与
元数据跟进(作者单位、DOI 引用、执行位排障、-xN 说明)。逐条见维护者私有仓库里的变更记录。

## v1.0.0 —— 正式版(2026-09-23 已完成)

整包版本从 0.11.0 升到 **1.0.0**:升 1.0 的两个门槛都满足了 ——

- **端到端科学验证**:证据页 [`evidence/real-data-comparison.md`](evidence/real-data-comparison.md)
  在公开数据(BMRB timedomain **53374**)上给出依赖严格容差的定量回收率与逐峰归因,并且附带
  随仓库发布的复现脚本(`scripts/vm_truth_benchmark.py` 等);
- **兼容性承诺**:行为面由 compat 流程管理(行为指纹 + `same/additive/behavior_changed/`
  `contract_changed` 四级分级 + 黄金向量),`nmrforge_api` 的契约版本自 2026-09-22 起是 1.0;
- 发布物:源码 + Linux AppImage(一份产物,界面中英运行时切换),PyPI 仍未发布。

## v0.11.0 —— 英文版与首个 AppImage 发布(2026-09-20 已完成)

目标:本项目的英文版与首个 Linux AppImage。第一版保留了中文主干,并把它翻译成一棵结构平行
的英文树,用翻译记忆库与平行守卫把两边对齐。

## 运行时本地化(2026-09-21 已完成)

维护两棵平行树让每处改动都要做两遍,于是界面文案改成运行时语言层:

- 代码里写**英文原文**并用 `tr("...")` 包起来:所有版本共用一份代码,公开树里不出现中文;
- 中文在 [`ui_support/locales/zh.json`](../../ui_support/locales/zh.json),键就是英文原文;
  查不到对照就回退英文,不会显示空白、也不会报错;
- 界面语言跟随系统语言,也可以用 `NMRFORGE_LANG=zh` 钉死中文(见[开发](development.md)、
  [图形界面](gui.md));
- `python scripts/i18n_extract_ui.py --check` 是守卫:新增文案必须先登记,已转换的文件里不得
  再有 `tr()` 之外的中文字面量,中文覆盖率不得回退 —— 这里的「覆盖率」指界面文案的中文条目
  覆盖率,不是代码覆盖率;本项目不设代码覆盖率门槛。

`no_spectrum_change`、`roi_capped`、`processing_script_not_found` 这类告警码是语言无关的
标识符,不会被改动,因此磁盘上的契约不受影响。

**中文文档**留在 [`Chinese_version/`](../README.md),根 README 指向它;双语(左右对照)的
文档版刻意不纳入 v0.11.0。
