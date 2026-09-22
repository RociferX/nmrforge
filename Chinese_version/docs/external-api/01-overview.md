# 01 · 定位与术语(v1.0,2026-09-13 规范定稿)

## 它是什么

`nmrforge_api` 是 NMRForge 对外提供的**参数组合处理执行器**:无 GUI、不依赖
Qt、可在无显示环境或集群上运行。输入原始 NMR 数据与**用户定义的参数组合表**,
输出**可追溯的峰表与处理记录**。

它复用的是 NMRForge 自身的处理与判读口径:

- 处理:同一套 NMRPipe 脚本生成与执行(统一相位优化、SMILE 重构等);
- 峰位:与 NMRForge 选峰**同一套** ppm 轴映射(ORIG 优先、回退 CAR,按
  FDDIMORDER 把逻辑维映射到数据轴);
- 记录:每个 workflow 都带完整脚本、峰表(按选定的精修方式)、完整日志、
  参数三层与版本表。

## 它做什么(规范流程)

```text
Raw data(A/B…)
    ↓  参考工作流(自动优化):1 个 reference 处理脚本 + 2 张 reference 峰表
Reference workflow
    ↓  用户参数组合表:每行 = 一个 workflow_id(W0001、W0002…)
User-defined workflow ensemble
    ↓  以参考脚本为模板,只替换该组合指定的参数,自动运行处理
Processed spectra
    ↓  每个组合用**参考锁定阈值**在自己的谱上独立选峰,再按 localization 精修
Parabolic / Gaussian peak tables(parabolic 默认 / gaussian / both)
    ↓
Complete provenance + QC
```

参考只作后续参数扰动的**基准**,不要求证明为全局最优参数组合。

采样口径:**实际满采样**的数据(含「标注 NUS 但 nuslist 列满全格 / 2D ser
全格无零行」)按 **uniform** 常规 FT 处理,不走 SMILE;判定与证据随参考留档。

## 它不做什么(软件边界)

- **不做统计分析与显著性判断**、
  **不给科学结论**——这些不进处理契约与 records 产物。σ/Δδ 汇总代码
  (`nmrforge_api.uncertainty`)保留为**测试/检测辅助**(处理链不调用它),
  其余由后续独立分析代码基于统一峰表完成;
- 不做峰归属/指认(可用外部峰表作为参考峰,但软件不推断归属);
- 不做峰重叠解耦与去卷积(v1.0 只有抛物线亚像素 + 2D 高斯单峰拟合);
- 不做 3D NUS 的行为(2D uniform 与 2D NUS 都支持);
- 不做并行调度(串行 + 断点续跑);
- 不自动生成研究参数空间(`axes` 只是便捷展开入口;`combos=` 原样执行)。

## 术语表

| 术语 | 含义 |
| --- | --- |
| 研究根(root) | 一个目录 = 一个研究项目 = 一个 NMRForge 项目(`project.json`),内含 `study/` |
| 条件(condition) | 一组原始数据(A/B…);两条件研究即同一实验的两个样品状态 |
| 数据集(DatasetRef) | 导入研究项目的一个 Bruker 原始数据集(`exp_id/data_id` + 条件标签) |
| 参考谱 | NMRForge 自动优化跑出的谱,冻结在 `study/reference/<key>/reference.ft2` |
| 参考脚本 | 参考运行**实际执行**的 NMRPipe 脚本,冻结为 `process.com`(带 SHA-256) |
| 参考峰表 | `reference.list`(峰身份 R0001…)+ 两张统一峰表(parabolic / gaussian),都由软件自动选峰产生(或外部峰表) |
| `reference_peak_id` | **参考峰表**里的稳定峰身份 `R0001`…;组合峰表留空(组合独立选峰),把峰匹配回参考身份是**使用者分析**的工作 |
| workflow_id | 参数组合表一行 = 一个 workflow,编号 `W0001`、`W0002`… |
| `parameters_requested` | 用户原样给的那一行参数 |
| `parameters_used` | 实际喂给后端的完整参数(参考基底 + 组合覆盖) |
| `parameters_resolved` | 自动参数的**实际结果**(`actual_p0/actual_p1`、SMILE 实际 nSigma/thresh、谱噪声 σ) |
| 候选谱 | 某 workflow × 某条件跑出的谱,存 `study/workflows/<id>/<条件>/spectrum.ft2`,**不替换**活动谱 |
| 峰表 | `peak_table_parabolic.csv` / `peak_table_gaussian.csv`(按 `localization` 输出;结构统一、含本谱峰序号 `peak_id`;见 06) |
| 状态 | `success` / `success_with_warning` / `failed` |

## 运行语义(硬约束)

1. **不 import Qt / 不改 GUI 状态**——可在集群运行,有专项测试守护;
2. **每个条件 fid 只转换一次**——workflow 之间参数是唯一变量;
3. **相位锁定参考值**——直接维跳过相位搜索、间接维沿用参考优化相位;人工偏差
   用 `phase_delta.<轴>.p0|p1`(相对参考)或 `phase.<轴>.p0|p1`(绝对值);
4. **候选谱不替换活动谱**——只写 `study/workflows/`,项目状态不受影响;
5. **可断点续跑**——每个 workflow × 条件成功即写 `run.json`,重跑跳过;
6. **单条件失败不中断**——状态 `failed` + 原因落盘,继续下一组合;
7. **两条件同参数**——同一 workflow 对 A/B 用同一份 `parameters_requested`,
   峰身份共享,各自输出峰值表。
