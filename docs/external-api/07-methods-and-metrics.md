# 07 · 方法与 QC 口径(v0.2)

> 本页描述软件**执行**了什么、留下了哪些 QC 记录。任何跨组合/跨条件的统计
> (σ、Δδ、robustness、显著性)都不在本软件范围内,由下游独立分析程序基于
> 统一峰表计算。

## 7.1 参考工作流

1. `generate_fid`(bruker `-AUTO`/fid.com 转换)→ `generate_spectrum`
   (NMRPipe 管道 + 统一相位路线;NUS 走 SMILE 重构);
2. 冻结**参考谱**与**参考脚本**(`process.com`,带 SHA-256)——参考脚本是该条件
   后续所有 workflow 的模板;
3. 参考峰位:软件在参考谱上自动选峰(阈值 `sigma_multiplier` 在**生成参考时**
   可由外部指定,缺省 35σ;API `sigma_multiplier=` / CLI `peaks --sigma`),
   轴峰按物理边距剔除,或使用外部峰表;峰按行序获得稳定身份 `R0001…`;
   **阈值随参考一起冻结**:后续所有 workflow 只能沿用参考的阈值,给不同阈值
   会报错(要换阈值须重建参考);
4. 在同一条参考谱上分别用 **parabolic** 与 **2D gaussian** 定位,写两张
   参考峰表;
5. 参考只作参数扰动的基准,**不声称全局最优**。

## 7.2 参数扰动(workflow)

- 每个组合的 `parameters_used` = 该条件参考运行的有效参数(基底)+ 组合表覆盖;
- 相位默认**锁定在参考值**(直接维跳过相位搜索、间接维沿用参考优化相位);
  人工偏差用 `phase_delta.<轴>.p0|p1`(相对参考)或 `phase.<轴>.p0|p1`(绝对值),
  实际值写进 `phase.<轴>.actual_p0/actual_p1`;
- 同一个条件内 fid 只转换一次(参考运行),候选谱写
  `study/workflows/<id>/<条件>/`,不替换活动谱;
- 相位/窗函数/填零/基线/NUS 参数全部按表执行,所有影响结果的参数三层落档。

## 7.2b 采样路由(满采样 → uniform)

读数据阶段判定有效采样:标注 NUS 但 `nuslist` 覆盖全格,或 2D `ser` 是「全格+
零填充」且**无零行** → 判为**实际满采样**,`sampling="uniform"`,
`sampling_schedule="full_sampling"`,并记录证据;处理走 `process()` 常规 FT,
**不跑 SMILE**。真 NUS(采样表只覆盖子集、或稀疏文件无采样表)仍走
`reconstruct_nus()`;3D NUS 维持原状(只支持建参考)。

## 7.3 两种峰定位

| 方法 | 做法 | 适用范围 |
| --- | --- | --- |
| `parabolic`(参考方法) | 在参考峰位附近的窗口内取 \|强度\| 极值,再对每个参与轴做 ±1 点三点抛物线亚像素 refine | 任意维 |
| `gaussian` | 以抛物线的整数格结果为中心,对**同一 candidate** 做 2D 高斯最小二乘拟合(不旋转、轴向可分离,含局部常数基线),给出中心/FWHM/幅度/rmse | **仅 2D** |

两者对**完全相同的 candidate** 独立运行,结果可直接比较(峰位差即算法差异);
同一张谱两张表使用同一批 `reference_peak_id`。

高斯失败(ROI 太小/不收敛/撞边界/病态)时:回退抛物线位置,并在峰表
`fallback`/`fallback_reason`/`fit_success` 与 `run.json.peak_localization`
中逐峰记录原因,workflow 状态升为 `success_with_warning`——**不允许静默**。

### 拟合成本与结果口径(2026-09-14)

逐峰 2D 高斯的成本 = 迭代数 × ROI 点数;三项约束让成本不随填零线性膨胀:

| 机制 | 默认 | 对结果的影响 |
| --- | --- | --- |
| 解析式雅可比 | 开启 | **无**:同一模型、同一目标函数与收敛判据;实测峰位差 ≤3e-05 ppm(15N)/3e-06 ppm(1H),方法零翻转、回退计数不变 |
| 拟合窗口每轴半宽上限(`peaks.localization.gaussian_roi_max_points`) | **0 = 不限制(默认)** | 默认完全不截断 → 结果与旧版一致;设正值(如 48)可限制细网格拟合规模换取速度,**会改结果**:真机 4× 最大 0.39 ppm(3 个峰翻转)、两维 2× 最大 0.71 ppm(1 个翻转);触发逐峰留档 `roi_capped`,截断后拟合失败会自动用完整 ROI 重试(`fit_retry_uncapped`/`retry_n_iter`) |
| 单峰求值上限(`peaks.localization.gaussian_max_nfev`) | 200 | 只影响原本要跑更多求值的难收敛峰;数值差分时期 1 迭代 ≈7 次求值,解析雅可比下 1 迭代 = 1 次求值,因此 200 求值 ≈ 200 迭代(比原来 ~57 迭代更宽松),只是把“最坏成本”减半 |

真机对照(248 峰跟踪、同参考峰表;默认配置 = 不限制上限):

| 网格 | 旧版 | 新版(默认) | 提速 | 峰位差(15N/1H)/方法翻转 |
| --- | --- | --- | --- | --- |
| F1 1× | 2.02 s | 1.46 s | 1.38× | 3e-05 / 3e-06 ppm,0 |
| F1 2× | 3.67 s | 2.24 s | 1.64× | 7e-06 / 3e-06 ppm,0 |
| F1 4× | 6.43 s | 3.89 s | 1.65× | 1e-06 / 0 ppm,**0** |
| F1 2× + F2 2× | 4.98 s | 3.13 s | 1.59× | 0 / 0 ppm,**0** |

若把 `gaussian_roi_max_points` 设为 48(可选):4× 1.73×、两维 2× 1.84×,但代价是
上面列出的峰位改变(留档可审计)。

## 7.4 峰位测量窗口(物理宽度口径)

- 窗口半径默认 = **1.5×该轴核素线宽(Hz)折算 ppm**
  (`core.peaks.axis_units`),`window_ppm` 可显式给物理半径;
  `window_pts` 是显式点数逃生口(跨分辨率不可比,不推荐);
- 运行时按**当前候选谱的点距**换算点数:零填零 k 倍只改点距,不改变窗口覆盖
  的 ppm 宽度;
- 换算结果逐组合留档:`run.json.window`(逐轴 points/ppm/effective_ppm/
  ppm_per_point/source)与 `records/measurement.json`;
- 高斯 ROI 同样按物理宽度(ppm)定义(`peaks.localization.gaussian_roi_f1_ppm`
  / `_f2_ppm`,或函数/CLI 参数),按点距换算点数;
- 结构性点数(局部极大 3 点邻域、抛物线 ±1 点)不换算——它们与分辨率无关。

## 7.5 逐峰 QC(落表字段)

| 字段 | 含义 |
| --- | --- |
| `detected` | 该谱上是否测到该参考峰(false 仍保留行) |
| `intensity` / `SNR` | 极值处的峰强与 `|峰强|/σ`(σ = 该谱 robust MAD 噪声) |
| `fit_success` / `fit_rmse` / `FWHM_H` / `FWHM_N` / `boundary_hit` | 高斯拟合 QC(parabolic 表写 NaN) |
| `fallback` / `fallback_reason` | 是否回退与原因 |

内部 `PeakMeasurement` 另带 `window_edge`(极值贴窗口边界)、`boundary`
(贴谱边界)、`out_of_range`(参考位置在谱范围外)与 `deltas`(相对参考峰位),
汇总进 `run.json.peak_localization` 与 `records/measurement.json`。

## 7.6 测试/检测辅助(不属于处理契约)

`nmrforge_api.uncertainty`(`position_uncertainty` / `uncertainty_summary` /
`PeakUncertainty`)计算同一批峰在多个组合间的 σ、极差与 Δδ 下限。它**不参与**
处理链,也不会出现在 `records/` 里;用途是:

- **回归检测**:σ/Δδ 全 0 说明被扫参数被静默忽略(真机历史上出现过该缺陷);
- **算法对比**:同一批 candidate 下 parabolic 与 gaussian 的峰位差;
- **下游参考实现**:分析侧可直接复用或照此实现。

```python
from nmrforge_api import position_uncertainty, uncertainty_summary

items = position_uncertainty(runs, csp_n_weight=0.2)
summary = uncertainty_summary(items, n_runs=len(runs))
```

正式统计与显著性判断请在你的分析代码里按自己的假设完成。

## 7.7 版本与可复算

每条记录带:软件版本(`core.__version__`)、Python 与关键依赖版本、真机登记的
NMRPipe/SMILE 版本、参考脚本与候选脚本 SHA-256、参考谱与候选谱 SHA-256、
网格哈希(`grid_sha256`)、参数三层、窗口换算记录、完整日志。凭
`records/manifest.json` + `workflows/<id>/` 即可复算并核对。
