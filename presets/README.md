# 实验模板（presets/）

模板 = 先验 + 约束 + 期望行为（框架 §43），具体参数由优化器决定。
YAML 由 core/experiments/registry.ExperimentTemplate.from_yaml 加载（Phase 2 实现）。

现有模板：hsqc.yaml / hnca.yaml / generic_2d.yaml / generic_3d.yaml。
