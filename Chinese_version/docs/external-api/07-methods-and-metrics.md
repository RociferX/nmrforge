# 07 · 方法与 QC 口径(v1.1)

> API 只做两步：自动优化并冻结参考谱/参考峰表；按用户修改的参数生成新谱/新峰表，
> 连同脚本、参数和 QC 交给下游。它不负责建立谱或峰表之间的对应关系，
> 不做跨谱匹配、统计推断或显著性判断。既有问题及本轮修复记录见
> [限制与路线图](09-limitations-and-roadmap.md)。

## 7.1 参考工作流

1. `generate_fid`(bruker `-AUTO`/fid.com 转换)→ `generate_spectrum`
   (NMRPipe 管道 + 统一相位路线;NUS 走 SMILE 重构);
2. 冻结**参考谱**与**参考脚本**(`process.com`,带 SHA-256)——参考脚本是该条件
   后续所有 workflow 的模板;
3. 参考峰位:软件在参考谱上自动选峰(阈值 `sigma_multiplier` 在**生成参考时**
   可由外部指定,缺省 35σ;API `sigma_multiplier=` / CLI `peaks --sigma`),
   轴峰按采集先验与原始边缘证据保守筛查（显式边距为人工覆盖），或使用外部峰表；
   每个条件独立建立自动参考峰表并获得条件内 `R0001…` 编号。外部峰表只应用于主条件；
   条件内编号不证明跨条件或跨谱峰对应。
   **阈值随参考一起冻结**:后续所有 workflow 只能沿用参考的阈值,给不同阈值
   会报错(要换阈值须重建参考);
4. 参考重定位使用联合窗口，只接受局部真峰且要求 `min_snr >= 3`；对测得峰做
   **三点抛物线**亚像素定位(唯一方法,2026-09-26 起)，写一张参考峰表
   `reference_peak_table_parabolic.csv`;
5. 参考只作参数扰动的基准,**不声称全局最优**。

## 7.2 参数扰动(workflow)

- 每个组合的 `parameters_used` = 该条件参考运行的有效参数(基底)+ 组合表覆盖;
- 相位默认**锁定在参考值**(直接维跳过相位搜索、间接维沿用参考优化相位);
  人工偏差用 `phase_delta.<轴>.p0|p1`(相对参考)或 `phase.<轴>.p0|p1`(绝对值),
  实际值写进 `phase.<轴>.actual_p0/actual_p1`;
- 同一个条件内 fid 只转换一次(参考运行),候选谱写
  `study/workflows/<id>/<条件>/`,不替换活动谱;
- 受支持的相位/窗函数/填零/基线/NUS 参数按表执行，参数三层落档。
  未知键目前不都硬性拒绝，不能只凭传参成功就判断参数已生效；应核对实际脚本与记录。

## 7.2b 采样路由(满采样 → uniform)

读数据阶段区分覆盖率与顺序：合法 `nuslist` 满覆盖且为标准栅格顺序，可记录
`sampling="uniform"` / `sampling_schedule="full_sampling"`，走常规 FT、不跑 SMILE。
满覆盖但乱序仍按表展开归位；`NusAMOUNT=100` 不等于可以无表处理。
传统采集的有效网格填满后，尾部全零 padding 不算缺采点。
明确 NUS 却缺表、位置无法还原时导入拒绝，不将失败数据送入 SMILE。
2D NUS 支持组合运行；3D NUS 的研究组合边界仍为仅建参考（桌面处理支持 3D NUS）。

## 7.3 峰定位:三点抛物线(唯一方法)

| 方法 | 做法 | 适用范围 |
| --- | --- | --- |
| `parabolic` | 参考峰在联合窗口内通过局部真峰与 `min_snr >= 3` 检查后定位；组合在本谱独立检出时做 ±1 点三点抛物线亚像素定位 | 输出 38 列，记录 F1/F2/F3 逻辑轴 ppm、核名与等效 FWHM；H/N 坐标和线宽仅在对应核唯一时作为兼容别名 |

**算法选择已取消**(2026-09-26,用户需求⑦):二维高斯最小二乘拟合
(`core.peaks.gaussian_fit`)与它的 API/CLI/GUI 表面整体删除,峰定位只剩这一种
方法。`localization` 只接受 `"parabolic"`;请求 `"gaussian"` / `"both"` 抛
公开入口的 `SweepError` / `MeasurementError`；内部 `LocalizationError` 不是包根导出的
公开异常。不会静默降级。

- **参考模式**:对参考峰表里的每个峰做抛物线定位,写一张
  `reference_peak_table_parabolic.csv`;
- **组合模式**(2026-09-14):每个组合在**自己的候选谱**上先用参考锁定阈值
  独立选峰,再做抛物线定位,峰表里 `reference_peak_id` 留空——不同组合之间的
  峰匹配由使用者完成;
- **targeted localization**:目标峰做抛物线精修；非目标峰保留检测整数格点位置，
  `localization_method=none`，定位 QC 列为空/NaN；这不表示检出失败。未检测到局部峰的
  参考身份行也以实际方法 `none` 表示;请求方法在 `localization_requested` 中单列。
- **定位 QC 仍逐峰落表**:`fit_success`/`FWHM_H`/`FWHM_N`/`boundary_hit` 由抛物线
  给出实数(等效线宽 `FWHM = 2.3548σ`,`σ² = H/(2|a|)`;顶点偏移贴 ±0.5 点 =
  `boundary_hit`);定位失败使用独立 `failure_reason`;`fallback`/`fallback_reason` 保留在 schema 里
  这些只是局部三点模型的数值诊断，不证明峰真实、线型正确或峰不重叠。
  等效线宽不等于实测半高全宽；QC 对正、负峰按峰极性对称计算。

## 7.4 选峰阈值与边距(物理宽度口径)

- 选峰阈值 = **噪声 σ 倍数**(`sigma_multiplier`,内部同时作为 `min_snr`):
  参考模式确定(缺省 35σ),组合模式**锁定沿用**,逐 workflow 留档
  `parameters_resolved.detection`(`source="reference(locked)"`);
- 自动轴峰筛查先核对实验/采集参数与原始边缘位置，再检查大量窄而对齐的边缘候选峰。
  少量孤立峰、参数冲突和边界未知时保留，不删除最终谱的内部载频峰。
  `edge_margin_ppm` 是显式人工边距覆盖，运行时按当前谱点距换算；不再默认 3×线宽遮罩。
- 自动判据与排除计数保存在 `parameters_resolved.detection.axial_screening`；独立低层
  `detect_and_localize` 缺少 Experiment 时不自动删除边缘峰。
- 换算结果逐组合留档:`run.json.window`(points/ppm/effective_ppm/
  ppm_per_point/source)与 `records/measurement.json`;
- 组合模式**没有** `max_peaks`,也没有「参考峰位搜索窗口」(不跟踪参考峰表);
  参考模式/低层 `measure_peak_positions` 仍保留 `window_ppm`(缺省 1.5×线宽)
  与 `window_pts` 逃生口;
- `window_ppm`/`window_pts` 定义物理搜索窗;联合多维候选 ownership 不能由逐轴边界表示。
  真实搜索边界按逻辑 F 轴记录于 `reference.peak_localization.search_windows`
  (`search_bounds_by_axis`,F 轴与 low/high 整数存储点)。所有权竞争另记
  `candidate_ownership_conflict`;峰表兼容字段 `cell_low_*` / `cell_high_*` / `cell_edge`
  一律为 NaN,不代表物理窗或 Voronoi 边界。
- 高斯 ROI 与拟合预算(`gaussian_roi_*` / `gaussian_max_nfev`)已随高斯拟合删除
  (2026-09-26):三点抛物线只需要局部 3 点,没有 ROI 与迭代预算这回事;
- 结构性点数(局部极大 3 点邻域、抛物线 ±1 点)保持不变；其覆盖的物理宽度随点距变化，
  因而不能据此声称定位误差与零填充或分辨率无关。

### 峰高、背景与符号口径

检出峰高、阈值、S/N 和参考测量强度相对全局中位数背景；记录
`baseline_offset` / `height_reference="global_median_baseline"`，不修改源谱。
这不是空间变化基线校正，旧表的原始幅度不能与新相对峰高直接算比值。
自动符号模式依据实验模板，未知/低置信度类型可用强双符号证据兜底；
相敏 COSY/NOESY/ROESY 保留正负候选，显式 `positive/negative/both/dominant` 请求优先。
轴坐标按 FDDIMORDER，重复核保留逻辑 F 轴身份；零 ORIG 是有效原点，不能当缺失值。

### 基线校正口径(2026-09-16 修正)

- 时间域:直接维 DC 偏置由 `POLY -time` 处理(自动诊断决定,写入参考基底
  `direct_poly_time`,组合沿用);
- 频域:`mode=order` 渲染 **`POLY -ord N -auto`**(NMRPipe `-auto` 自动挑基线点
  后做 N 阶拟合)。历史缺陷:NMRPipe 裸 `POLY -ord N` 默认 `-nc 0` 且无
  `-first/-last` → 没有基线节点 → **恒等操作**(真机逐位验证),导致“关掉该轴谱不变”;
- 因此参考的基线优化器评分(内存稳健多项式拟合)与终跑脚本现在同源;
- `mode=auto` 仍渲染 `POLY -auto`(NMRPipe 自带默认阶数)。

### 轴效果按条件报

同一参数在不同条件下可能截然不同(数据不同、参考配置不同)。软件逐
(workflow, 条件)比较候选谱与**该条件参考谱**的 SHA-256:逐位相同即发
`no_spectrum_change` 警告——正式 plan 不应把该轴在该条件下当成真实扰动。

## 7.5 逐峰 QC(落表字段)

| 字段 | 含义 |
| --- | --- |
| `detected` | 组合表只含按阈值检出的峰，恒 true；参考表保留未检测到的身份行，并依据联合窗口中的局部真峰及 `min_snr >= 3` 证据标记是否检测到 |
| `intensity` / `SNR` | 极值处相对全局中位基线的带符号峰高与 `|峰高|/σ`(σ = 该谱 robust MAD 噪声) |
| `fit_success` / `FWHM_H` / `FWHM_N` / `boundary_hit` | 三点抛物线的定位 QC:抛物线给**等效线宽**(`FWHM = 2.3548σ`,`σ² = H/(2|a|)`,`boundary_hit` = 顶点偏移贴 ±0.5 点);同时完整记录各逻辑 F 轴的等效 FWHM；QC 对正、负峰按极性对称计算 |
| `duplicate_localization` | 该行与同表另一行完整逻辑 F 轴及核名坐标相同(ppm 精确到 1e-6,P2-5):重复组每行都标 true、不删行;旧 H/N 行兼容按 H/N 坐标判重 |
| `fallback` / `fallback_reason` | 是否实际发生算法回退与原因;定位失败原因在独立的 `failure_reason` |
| `cell_low_*` / `cell_high_*` / `cell_edge` | 联合多维 ownership 无法投影为逐轴区间,因此始终 NaN;物理边界在参考 localization audit 另存 |
| `intensity_ratio_vs_picked` / `shift_vs_picked_*` | 参考表可测的逐峰身份诊断(**|测得强度| ÷ |身份表 `Height`|**、measured − picked(ppm));组合表写 NaN |

`window_edge` 表示定位点是否触及实际搜索窗边界。`cell_edge` 是保留的空值字段,不与物理窗口或联合 ownership 建立关系。


内部 `PeakMeasurement`(参考峰跟踪/低层测量路径)另带 `window_edge`(极值贴窗口
边界)、`boundary`(贴谱边界)、`out_of_range`(参考位置在谱范围外)与 `deltas`
(相对参考峰位),汇总进 `run.json.peak_localization` 与
`records/measurement.json`;组合模式改用本谱检出的峰 → 逐峰 QC 为
`fit_success`/`FWHM_*`/`boundary_hit`/`fallback`。

## 7.6 下游关系与分析边界

各候选谱独立编号，不能用 `peak_id` 直接连接参考表、不同 workflow 或不同条件。
匹配、指认、缺失峰处理、参数敏感性分析和 CSP 统计全部由下游实现；API 不输出这些关系。

代码暂时仍导出历史 `uncertainty` 辅助函数，但处理链不调用，也不写入 `records/`。
它按整数 `peak_id` 分组，不会建立峰对应，不能直接传入独立选峰的 `result.runs`。
只有下游先完成并核实同峰匹配后，才有讨论跨处理位置离散度的前提；单一结果不能估计
样本标准差，处理参数网格的离散度也不是自动成立的 CSP 显著性阈值或误差下限。
峰位相同不证明参数无效：参数可能只改变幅度、线宽或背景。应核对实际脚本、参数与谱数据。

## 7.7 版本与可复算

每条记录带:软件版本(`core.__version__`)、Python 与关键依赖版本、真机登记的
NMRPipe/SMILE 版本、参考脚本与候选脚本 SHA-256、参考谱与候选谱 SHA-256、
网格哈希(`grid_sha256`)、参数三层、窗口换算记录、完整日志。凭
`records/manifest.json` + `workflows/<id>/` 即可复算并核对。
