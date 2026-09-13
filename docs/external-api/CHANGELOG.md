# nmrforge_api 变更记录

版本口径:新增功能/新增字段保持同一 minor;破坏性改动升 minor 并给出迁移说明。

## 0.1.1(2026-09-13)

**峰位窗口/选峰边距改为「物理宽度(ppm)」口径**(用户方案 A)。动机:填零 k 倍
只让网格变密(点距 1/k),同一个「5 点」覆盖的 ppm 宽度就随处理参数漂移,而填零
正是研究的自变量之一。

- 新增 `core/peaks/axis_units.py`(`ppm_per_point` / `points_for_ppm` /
  `ppm_for_points` / `edge_margin_ppm` / `measurement_window_ppm` /
  `describe_axis`),缺省宽度 = 该轴核素线宽(`processing.linewidth_hz`)的倍数:
  选峰边距 3×、峰位窗口 1.5×;
- `pick_peaks(..., edge_margin_ppm=, edge_margin_points=)`、
  `smile_scan_edge_margin(experiment=, n_points=)`、
  `measure_peak_positions(..., window_ppm=, window_pts=, axes=)`、
  `run_sweep/run_parameter_study(window_ppm=)`、CLI `sweep --window-ppm`;
- 新公开函数 `window_points_by_axis(axes, *, window_pts=, window_ppm=)`:
  逐轴给出 `points`/`ppm`/`effective_ppm`/`source`/`nucleus`/`obs_mhz`/
  `ppm_per_point`;
- 新增记录:`run.json` 的 `window`、`records/measurement.json`、
  `manifest.json` 的 `measurement`、选峰运行参数 `detection`;
- **结构性点数不换算**(局部极大 3 点邻域、抛物线 ±1 点模板)——它们必须等于
  网格步长本身。

迁移说明:`window_pts` 的**缺省值**由 `3` 改为 `None`(=按物理宽度自动)。显式
传 `window_pts=3` 的老脚本行为不变;想让结果跨填零可比,改用 `window_ppm`。
缺省窗口只有 1.5×线宽,若参数引起的位移超过它,测量会如实标
`window_edge=true`(不是 bug)。

验证:同一合成谱 1×/4× 网格、同一 1.0 ppm 窗口 → 点数 2↔8、覆盖宽度均
≈1.0 ppm、峰位差 < 0.02 点(`tests/test_nmrforge_api.py` 新增两条用例);
本地全量 1037 passed / 1 skipped;VM 全量 1019 passed / 19 skipped。
真机(真实 NMRPipe,800 MHz HSQC + 合成 25% NUS):`zero_fill=[1,4]` 下 15N
窗口 5↔19 点而覆盖宽度恒为 0.5 ppm(1H 73 点不变),66/66 与 60/60 峰全测到;
自动选峰记录的边距为 0.5549 ppm → 11 点;2D NUS 的 `nsigma` 扫描同样通过。
证据见 proposal 附录。

同版本内新增(2026-09-13,**2D 高斯峰定位**;承接用户「加入高斯拟合作为 2D
选峰的可选精细峰位确定算法,以后也可用于比较两种算法带来的峰位置差距」):

- 新增 `core/peaks/gaussian_fit.py`(不旋转、轴向可分离 2D 高斯:
  `A/x0/y0/sigma_x/sigma_y/B`,`scipy.optimize.least_squares` 带 bounds;
  失败返回稳定原因串)与 `core/peaks/localize.py`(`localize_peak` 统一入口、
  ROI 按 ppm 物理宽度换算点数、逐峰诊断 + 峰表附件读写、方法名规范化);
- 参考峰表:`ensure_reference_peaks(..., localization_method=)` /
  `pick_reference_peaks(..., localization_method=)`;CLI `peaks --localization`;
- 候选谱测量:`measure_peak_positions(..., refine="gaussian")`(仅 2D)、
  `run_sweep`/`run_parameter_study(..., refine=, roi_f1_ppm=, roi_f2_ppm=)`;
  CLI `sweep --refine`。**两种方法对同一批峰独立运行**,可直接比较峰位差;
- 逐峰诊断(方法/回退/QC)写入 `PeakMeasurement.localization`(随 `runs.json`)
  与 `records/measurement.json` 的 `localization.actual_method_counts`;
- config:`peaks.localization.{method,gaussian_roi_f1_ppm,gaussian_roi_f2_ppm}`;
- 默认仍为 `parabolic`,既有抛物线与峰检测逻辑零改动;高斯仅 2D,失败回退
  抛物线并记录 `requested_method`/`actual_method`/`fallback_reason`(不静默)。

  真机验证(VM `0764707`,真实 NMRPipe,800 MHz HSQC,同类网格 4 组合 × 60 峰):
  参考峰表 76 检出/60 保留 → 高斯成功 73、回退 3(`not_converged`,均是最弱峰);
  候选谱测量 480 次 → `{gaussian: 222, parabolic: 18}`(回退原因
  `sigma_at_bound` 12 / `not_converged` 6);两种算法的峰位差(同一批候选谱,
  `spectrum_sha256` 一致)15N 中位 0.0030 ppm/max 0.217、1H 中位 0.00017/max 0.0082,
  与「只改填零」的参数效应同量级——算法选择本身是需控制的不确定度来源。
  详见 `docs/proposals/external-api/001-parameter-sweep-api.md` 附录 B。

## 0.1(2026-09-12)

首个可用版本。公开面:

```text
run_parameter_study / open_study / add_dataset / dataset_info
build_reference / load_reference / set_reference_peaks / ensure_reference_peaks
sanitize_sweep_params / normalize_direct_phase / reference_phase
pick_reference_peaks / read_reference_peaks / measure_peak_positions / peak_coordinates
expand_grid / merge_overrides / plan_sweep / run_sweep / load_plan / load_runs
position_uncertainty / uncertainty_summary / write_records
```

CLI:`python -m nmrforge_api {init,reference,peaks,sweep,report,status}`。

要点:

- 参考谱、参考脚本、参考峰位全部由 NMRForge 自动优化/自动选峰产生;外部峰表
  为可选逃生口(`peaks=` / `--peak-table`);
- 同一 fid 只转换一次;参考相位(各轴 PS)锁定;候选谱不替换活动谱;
  逐组合 `run.json` 支持断点续跑;单组合失败不中断;
- 亚像素峰位测量(窗口极值 + 三点抛物线)与质量标记;
- 不确定度主指标 `Δδ_std = sqrt(Σ(w_n·σ_n)²)`(w(15N) 默认 0.2);
- `records/` 六个产物:manifest / sweep_plan / runs / peak_positions /
  uncertainty / uncertainty_summary;
- 不 import Qt;有「不加载 Qt」与断点续跑的专项测试。

同版本内新增/修复(发布前):

- **扫描口径与设计入口**:
  - 相位识别偏差可扫:`phase_delta.<轴>.p0|p1`(相对参考)/
    `phase.<轴>.p0|p1`(绝对值);直接写 `phases`/`direct_phase` 报错并提示;
  - 显式组合表 `combos=`(正交/部分因子/D-optimal/LHS/手挑由外部决定,
    接口原样按表序执行)+ `load_combo_table`/`write_combo_table`/
    `combos_from_rows` + `design_diagnostics`;CLI `sweep --combos`;
  - 确定性/策略参数与未知键写入 `plan.notes` 提示(锁定键直接报错);
  - `SweepRun.phase`、`SweepPlan.design/n_full/diagnostics`;
  - VM 真机验证(bmr6980 uniform,4 行组合表):4/4 成功、152 峰全测到,
    相位偏差精确为 F2=22.5/32.5(参考 27.5±5)、F1 保持 172.5。

- **支持 2D NUS 扫描**:`reconstruct_nus` 增加 `out_file`/`script_name`
  (候选输出写 `_intermediate/`,不覆盖终谱),扫描按数据采样方式自动派发;
  参考相位锁定扩展到 NUS(间接维 `phases` + 直接维扁平 `direct_phase`);
  可扫 `nsigma`(别名 `nSigma`)/`thresh`/`nthread`/`smile_scaling`(驼峰别名
  自动归一,避免参数未生效);3D NUS 仍不支持。

- **相位锁定**:统一相位路线把相位记在 `phases`(各轴 PS),最初只读
  `direct_phase` 导致候选谱间接维相位回退默认值;现按 `reference_phase()`
  取全轴 PS 并传给后端 `direct_phase_override`。
- **参考峰表格式**:支持研究项目导出的 `peak_id,H_ppm,N_ppm,...` CSV
  (按表头自动判别),与 Poky `.list`、旧 `H_shift/N_shift` CSV 并存。
- **默认自动选峰**:新增 `ensure_reference_peaks()`,并把峰表来源/SHA-256/
  峰数/选峰参数写入 `reference.json` 与 `manifest.json`。
