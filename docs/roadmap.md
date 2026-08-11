# 路线图

## Phase 1：数据理解与基础处理

```text
Bruker parser → 2D/3D 检测 → uniform/NUS 检测 → 轴映射 → 基础处理
→ 自动相位 → 基线 QC → 噪声估计 → 质量评分
```

落地项：

- core/experiment：parse_dataset_params / sampling_detector / dimension_mapper / acquisition_mode_detector
- core/processing：apodization / zero_fill / ft / phase / baseline / calibration 原语
- core/planning：ProcessingDag.execution_order（拓扑排序）+ PipelineRunner 缓存
- core/qc：noise / snr / phase_quality / baseline_quality / spectrum_quality.evaluate
- backend：NMRPipeBackend.health_check / process（确定性 .com 生成）
- AutoProcessor.run 最小闭环（uniform 2D）

## Phase 2：实验分类

- 多证据 classifier（pulse program → 核组合 → 维度顺序 → FnMODE → 参数 → 命名）
- ExperimentTemplate.from_yaml + presets/*.yaml 加载
- HSQC / HNCA / HNCO / HNCACB / CBCANH 模板匹配与置信度门控

## Phase 3：NUS 管线

```text
direct 优化（代表性子集）→ reconstruction 候选（fast preview + 少量候选 + 局部搜索 + early stopping）
→ 最佳重建 → indirect 优化 → DAG 缓存
```

落地项：NusReconstructionParams 后端接线（SMILE）、CandidateGenerator/GridSearch/LocalSearch、
OptimizationBudget 执行、NUS quality score（data consistency + SNR + peak quality + stability - artifact）。

## Phase 4：深度优化与报告

- peak stability 分析
- BayesianOptimizer（昂贵任务）
- ProcessingReport：report.json / report.html / processing_recipe.json
- 学习用户最终接受的参数（Parameter Predictor 远期，不做第一版 ML）

## 第一版最值得实现的优化器

```text
AutoProcessor
├── AxisOptimizer / ApodizationOptimizer / ZFOptimizer
├── PhaseOptimizer / BaselineOptimizer / CalibrationChecker
└── SpectrumQC

NUSOptimizer
├── DirectOptimizer / ReconstructionOptimizer / IndirectOptimizer
├── CandidateManager / CacheManager / StabilityAnalyzer
```

## 明确不做（第一版）

- 完整结构解析、自动 assignment、AI 结构预测、NOE。
- 用 LLM 直接看图猜 phase/猜 NUS 参数；LLM 只做解释与报告。
