# 07 · 方法与指标(可直接写进论文方法部分)

## 7.1 研究设计的三个控制点

1. **同一份 fid**:参考处理阶段转换一次 fid,所有组合复用(日志会显示
   「复用已转换 fid」);
2. **同一相位**:参考运行的各轴 PS(p0,p1)被冻结,扫描时按轴覆盖,直接维跳过
   相位搜索、间接维沿用参考优化值(`phase_locked: true`);
3. **同一批峰**:参考峰位(SHA-256 冻结)在每张候选谱上被追踪,不重新选峰。

这三点保证「组合之间的差异 = 被扫处理参数的效应」。

## 7.2 峰位测量算法

输入:一张谱(ft1/ft2/ft3)、一份参考峰表、窗口半径(**物理宽度**,缺省
1.5×该轴核素线宽折算 ppm,按该谱点距换算成点数;`window_ppm` 可显式给,
`window_pts` 是点数逃生口)。

1. **ppm → 分数索引**。每个峰的核位置按数据轴定位:`ORIG` 优先、回退 `CAR`
   的 ppm 公式(与 NMRForge 选峰、viewer 同源),逻辑维按 `FDDIMORDER` 映射到
   数据轴;ppm 经轴数组线性插值转成分数索引。
2. **窗口内取极值**。在参考位置 ±`window_points_by_axis()` 换算出的点数范围内
   按 `sign` 取极值(同一物理窗口在不同填零下点数不同、覆盖 ppm 相同):
   - `abs`(默认):`|I|`,正负峰都追;
   - `positive` / `negative`:只追正峰 / 只追负峰。
3. **亚像素 refine**。每个参与测量的轴,在极值点两侧各取 1 点,用三点抛物线
   求顶点偏移 δ:

   ```text
   δ = 0.5 · (y₋₁ − y₊₁) / (y₋₁ − 2y₀ + y₊₁)      δ ∈ [−0.5, +0.5]
   ```

   分数索引 = 整数极值点 + δ(分母接近 0 或端点处不做 refine,δ=0)。
   这一步是必需的:填零等参数改变数字点距,只取整数极值会把「半个数字点」的
   抖动当成峰位变化(实测对已知 +1.25 点平移,本方法误差 < 0.2 点)。
4. **分数索引 → ppm**(轴数组线性插值),写入记录。

### 质量标记

| 标记 | 含义 | 建议处理 |
| --- | --- | --- |
| `found=false` | 窗口内没有有效数据 | 该峰该组合不进入统计 |
| `window_edge=true` | 极值落在窗口边界 | 真峰可能在窗外;考虑加大 `window_ppm`(物理半径) |
| `boundary=true` | 极值贴谱边界 | 峰在谱边缘,可能被截断 |
| `out_of_range=true` | 参考峰位落在谱范围外 | 峰表与处理窗口不一致(如提取窗口变了) |

### 可选:2D 高斯定位(与抛物线并列)

`refine="gaussian"`(仅 2D)在候选峰附近拟合不旋转、轴向可分离的 2D 高斯:

```text
I(x, y) = B + A·exp( -(x-x0)²/(2σx²) - (y-y0)²/(2σy²) )
x = F2(直接), y = F1(间接);参数 (A, x0, y0, σx, σy, B)
初始中心 = 抛物线结果;scipy.optimize.least_squares(bounds=…)
FWHM = 2·sqrt(2 ln 2)·σ
```

- **ROI 按物理半径(ppm)**给出(缺省 F1 ±1.5 ppm / F2 ±0.25 ppm,config
  `peaks.localization` 可改),运行时按当前谱点距换算点数 —— 与选峰边距/测量
  窗口同口径,填零不改变 ROI 覆盖的 ppm 范围;
- bounds:中心不出 ROI、σ∈[0.5 点, ROI 半径]、幅度 ≥0(负峰先按符号翻正再
  拟合,幅度按原单位返回)、基线 ∈[ROI 最小值 − 动态范围, ROI 最大值];
- 失败判定(全部带稳定 `fallback_reason`):`roi_too_small` /
  `insufficient_data` / `non_finite` / `flat_region` / `no_peak` /
  `optimizer_error` / `not_converged` / `center_at_boundary` /
  `sigma_at_bound` / `poor_fit`;
- 失败时**回退抛物线**并同时记录 `requested_method=gaussian`、
  `actual_method=parabolic`、`gaussian_fit_success=false`;
- 输出字段(`PeakMeasurement.localization` / 峰表附件):`fit_success`、
  `center_f1`/`center_f2`(ppm)、`amplitude`、`sigma_f1`/`sigma_f2`(ppm)、
  `fwhm_f1`/`fwhm_f2`、`baseline`、`fit_rmse`、`boundary_hit`、
  `fit_failure_reason`、点数口径的 `sigma_points_f1`/`f2`。

写论文时的口径建议:抛物线是**参考方法**(与既有选峰/测量一致);报告高斯结果时
注明 ROI 半径、bounds 策略、回退计数(`measurement.json` 的
`localization.actual_method_counts` 与 `fallback_reasons`)。两种方法对**同一批
candidate**独立运行,可直接给出「算法带来的峰位差」分布。

## 7.3 不确定度指标

对每个峰、每个核 n:

```text
σ_n        = 峰位在全部组合上的样本标准差(分母 n−1)
range_n    = max − min
mean_n     = 峰位均值
```

**CSP 下限(主指标)**:

```text
Δδ_std = sqrt( Σ_n (w_n · σ_n)² )
w(1H) = 1, w(15N) = csp_n_weight(默认 0.2),其余核 = 1
```

即 ¹⁵N-HSQC 常用的 1/5 加权口径;`csp_n_weight` 可通过参数/命令行调整。
另外给出经验最坏情形:

```text
Δδ_max = max_over_runs sqrt( Σ_n (w_n · (ppm_n(run) − mean_n))² )
worst_run = 取到最大值的组合
```

**只有所有被测核都测到的组合进入统计**(避免「只有一个核测到」的半边数据
把 σ 拉偏);被排除的组合数记在 `missing_runs`。

数据集级汇总(`uncertainty_summary.json`):各峰 Δδ_std 的
min/median/p90/max,以及逐核 σ 的 median/p90/max。

## 7.4 假设与边界(写方法部分时要声明)

1. **独立性假设**:Δδ_std 由各核 σ 平方和开方得到,等价于假设核间位置误差
   不相关;若两核误差强相关(如同一维的相位误差同时移动两个方向),该式是
   近似。
2. **描述性指标**:σ 与 Δδ 下限是「处理引入的位置离散程度」,不做显著性检验、
   不外推为浓度依赖的物理结论。
3. **不含其它误差源**:采样噪声、峰重叠/去卷积、指认错误、谱仪漂移、温度与
   pH 等实验条件都不在其中;这是**下限**,不是全部误差预算。
4. **峰集依赖**:参考峰集来源(`auto` vs `external`)与阈值会改变估计(同数据
   实测中位 Δδ 可差数倍)。同一研究内必须固定 `peak_source`、阈值与**窗口物理
   宽度**(`window_ppm`;若用 `window_pts` 点数口径,必须声明「点数不随填零
   换算」这一差异)。
5. **参数独立性**:网格是逐点扫描,不做因子交互建模;要研究交互效应请自行在
   `axes` 里构造组合(一个轴就是一组候选,可组合出任意网格)。
6. **单谱测量**:每个组合只跑一次处理(不做重复采集/重复处理);若需要噪声贡献,
   可在网格里加入等价重复(patch 不同但物理等价),或对同一组合重复运行。

## 7.5 建议的报告口径

报告不确定度时至少给出:

```text
NMRForge 版本 / NMRPipe 版本
数据集来源与 SHA 指纹(manifest.json)
参考脚本与参考谱 SHA-256
参考峰表来源(auto/external + 阈值/max_peaks)与 SHA-256
扫描网格(轴与取值)与 grid_sha256
每个核的 σ 分布(median/p90)+ Δδ_std 分布(min/median/p90/max)
排除/缺失的峰与组合(peak_positions.csv 的 found 列 + missing_runs)
```

这些字段全部在 `records/manifest.json`、`records/uncertainty.csv`、
`records/uncertainty_summary.json` 里,可直接引用。
