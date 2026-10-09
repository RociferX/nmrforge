# 01 · 定位与术语(v1.1.1,2026-10-10)

## 它是什么

`nmrforge_api` 是 NMRForge 对外提供的**参数组合处理执行器**:无 GUI、不依赖
Qt、可在无显示环境或集群上运行。输入原始 NMR 数据与**用户定义的参数组合表**,
输出**可追溯的峰表与处理记录**。

它复用的是 NMRForge 自身的处理与判读口径:

- 处理:同一套 NMRPipe 脚本生成与执行(统一相位优化、SMILE 重构等);
- 峰位:与 NMRForge 选峰**同一套** ppm 轴映射(ORIG 优先、回退 CAR,按
  FDDIMORDER 把逻辑维映射到数据轴);
- 记录:每个 workflow 都带完整脚本、峰表(三点抛物线)、完整日志、
  参数三层与版本表。

## 它做什么(规范流程)

```text
Raw data(A/B…)
    ↓  每个条件各自建立参考谱与参考峰表;外部峰表只用于主条件
Reference workflow
    ↓  用户参数组合表:每行 = 一个 workflow_id(W0001、W0002…)
User-defined workflow ensemble
    ↓  以参考脚本为模板,只替换该组合指定的参数,自动运行处理
Processed spectra
    ↓  每个组合用**参考锁定阈值**在自己的谱上独立选峰,再做三点抛物线精修
Parabolic peak table
    ↓
Complete provenance + QC
```

参考只作后续参数扰动的**基准**,不要求证明为全局最优参数组合。

采样口径:标注 NUS 的数据只有在合法采样日程完整覆盖且为标准顺序时才按
**uniform** 常规 FT 处理。全覆盖乱序仍须按日程归位；明确 NUS 缺日程且采样坐标无法
从数据还原时拒绝导入。`ser` 无零行本身不足以证明 uniform，判定与证据随参考留档。

## 它不做什么(软件边界)

- **不做统计分析与显著性判断**、
  **不给科学结论**——这些不进处理契约与 records 产物。σ/Δδ 汇总代码
  (`nmrforge_api.uncertainty`)保留为**测试/检测辅助**(处理链不调用它),
  其余由后续独立分析代码基于统一峰表完成;
- 不做峰归属/指认(可用外部峰表作为参考峰,但软件不推断归属);
- 不做峰重叠解耦与去卷积(当前 v1.1.1 只有三点抛物线亚像素精修;二维高斯单峰拟合
  已于 2026-09-26 整体删除);
- 3D NUS 当前只能建参考，不能执行研究组合；2D uniform 与 2D NUS 支持组合执行；
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
| 参考峰表 | 每个条件各自登记 `reference.list`(条件内身份 R0001…)+ `reference_peak_table_parabolic.csv`;给外部峰表时只应用于主条件。条件内编号不构成跨条件或跨谱对应 |
| `reference_peak_id` | **参考峰表**里的峰身份 `R0001`…;组合峰表留空(组合独立选峰)，API 不做跨谱匹配，由使用者分析完成 |
| workflow_id | 参数组合表一行 = 一个 workflow,编号 `W0001`、`W0002`… |
| `parameters_requested` | 用户原样给的那一行参数 |
| `parameters_used` | 实际喂给后端的完整参数(参考基底 + 组合覆盖) |
| `parameters_resolved` | 自动参数的**实际结果**(`actual_p0/actual_p1`、SMILE 实际 nSigma/thresh、谱噪声 σ) |
| 候选谱 | 某 workflow × 某条件跑出的谱,存 `study/workflows/<id>/<条件>/spectrum.ft2`,**不替换**活动谱 |
| 峰表 | `peak_table_parabolic.csv`(38 列;含 F1/F2/F3 逻辑轴 ppm、核名和等效 FWHM;本谱峰序号 `peak_id`;仅在 1H/15N 轴各自唯一时提供 H/N 坐标与线宽兼容列) |
| 状态 | `success` / `success_with_warning` / `failed` |

## 运行语义(硬约束)

1. **不 import Qt / 不改 GUI 状态**——可在集群运行,有专项测试守护;
2. **workflow 只处理参考阶段生成的 FID**——不重新导入、转换或合并；FID 缺失、损坏、
   输入/转换证据不一致或参数要求重转时直接报错，要求用 `force=True` 重建参考。支持单文件、
   3D uniform 切片目录及多段合并 FID；详见[FID 复用边界](09-limitations-and-roadmap.md#99-fid-复用边界);
3. **相位锁定参考值**——直接维跳过相位搜索、间接维沿用参考优化相位;人工偏差
   用 `phase_delta.<轴>.p0|p1`(相对参考)或 `phase.<轴>.p0|p1`(绝对值);
4. **候选谱不替换活动谱**——只写 `study/workflows/`,项目状态不受影响;
5. **可断点续跑**——每个 workflow × 条件成功即写 `run.json`,重跑跳过;
6. **单条件失败不中断**——状态 `failed` + 原因落盘,继续下一组合;
7. **两条件同请求参数**——同一 workflow 对 A/B 用同一份 `parameters_requested`,
   各自输出峰表；这不表示峰身份共享或跨谱峰对应已完成。
