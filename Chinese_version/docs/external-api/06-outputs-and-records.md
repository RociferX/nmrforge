# 06 · 输出与记录(v0.2)

## 6.1 目录布局

```text
<root>/
  project.json                    NMRForge 项目(数据与 WorkflowRun 登记)
  study/
    study.json                    条件数据集 + 参考摘要
    work/                         共享 fid 与每次运行的脚本/候选谱
    reference/<exp>_<data>/
        reference.json            参考状态(参数/哈希/两张峰表/版本)
        process.com               参考运行实际执行的完整脚本
        reference.ft2             冻结参考谱
        reference.list            参考峰**身份**表(Poky,含 R0001…)
        reference_peak_table_parabolic.csv
        reference_peak_table_gaussian.csv
    workflows/W0001/
        workflow.json             组合级记录(见 6.3)
        log.txt                   组合级完整日志(各条件日志串接)
        <条件 A|B>/
            process.com           该条件实际执行的完整处理脚本
            spectrum.ft2          候选谱(不替换活动谱)
            peak_table_<所选方法>.csv  只出现 localization 实际选择的方法
            log.txt               该条件的完整运行日志(不是尾部)
            run.json              该条件的完整溯源记录
    records/
        reference.json            参考模式产物(参考谱/脚本/两张峰表/采样/阈值)
        manifest.json             组合模式产物(数据/参考/计划/峰身份/版本)
        sweep_plan.json           workflow 计划(含 workflow_ids)
        runs.json                 逐 (workflow, 条件) 扁平记录
        workflows.json            逐 workflow 汇总记录
        measurement.json          测量口径与定位 QC 汇总
        peak_table_<所选方法>.csv  各实际方法的 workflow × 条件长表
```

## 6.2 统一峰表字段(当前 **29 列**)

组合模式只写 `localization` 实际选择的方法。用 `resume=False` 重跑时会删除
上一轮未选方法的峰表和定位附件;`records/` 也会删除未选方法的旧汇总。旧版
`peak_positions.csv` 别名不再生成,升级运行时会清除残留。

```text
workflow_id, condition, dataset,
peak_id, reference_peak_id, assignment,
H_ppm, N_ppm, intensity,
SNR, detected, localization_method,
localization_requested, fallback, fallback_reason,
fit_success, FWHM_H, FWHM_N,
fit_rmse, boundary_hit, duplicate_localization,
cell_low_H, cell_high_H, cell_low_N,
cell_high_N, cell_edge, intensity_ratio_vs_picked,
shift_vs_picked_H, shift_vs_picked_N
```

新增 8 列(P1-3,2026-09-19)只描述**参考表**的「按峰身份重定位」结果:

- `cell_low_*` / `cell_high_*`(int):最终生效的搜索区间(±1.5×线宽窗口 ∩ 独占邻域),
  **闭区间**,数据轴整数索引;`exclusive_windows=False`(历史口径)时写该口径实际用的窗口边界;
- `cell_edge`(bool):极值停在**独占邻域**那条边界上(= 邻居的格把它截断),与 `window_edge`
  (物理窗边界)正交;历史口径恒 `false`;
- `intensity_ratio_vs_picked`(float):**|测得强度| ÷ |峰身份表的 Height|**(分子分母都取绝对值——负峰数据集的 `.list` Height 是负数,2026-09-19 修);缺 Height 或 0 → `NaN`;
  ≈1 表示停在自己的峰顶上,明显 >1 说明更可能是强峰的肩峰/伴随峰;
- `shift_vs_picked_H` / `shift_vs_picked_N`(float,ppm,符号 = measured − picked,与
  `H_ppm`/`N_ppm` 同轴同向):逐轴位移;¹⁵N 的 ppm 方向与数据索引方向相反,不要读反;
- **组合(workflow)表这 8 列一律写 `NaN`**:sweep 是「选峰即定位」,没有「先给身份坐标、
  再重定位」这一步,写 1.0/0 是伪造信息(沿用 parabolic 表里 gaussian 专属列写 NaN 的先例);
- `duplicate_localization`(bool,P2-5,2026-09-19):该行与**同表另一行**落在同一坐标
  (ppm 精确到 1e-6)时为 `true`,重复组的每一行都标(不删行、不改峰集)。参考表与组合表
  都会标:参考表在独占邻域修复后同坐标只剩「取整后落同一格」的分辨率极限,组合表则还有
  选峰器亚格点精修把相邻两个检出峰收进同一格的情形;
- 参考冻结记录 `reference.json.peak_localization.<method>` 另有汇总:`n_cell_edge`、
  `n_duplicate`(= 总行数 − 唯一坐标数)与 `intensity_ratio_vs_picked` 的 `n`/`median`/`max`;
  每条 `run.json.peak_localization.<method>` 也有 `n_duplicate`,出现同坐标行时
  `run.json.warnings` 另留一条 `duplicate_localization`(码 + 行数)。

- **两种算法表结构完全一致(29 列)**;只有 `fit_rmse` 仍是高斯专属、parabolic 表写
  `NaN`(不是 false/0)——P3-7(2026-09-19)起 `fit_success`/`FWHM_*`/`boundary_hit`
  由三点抛物线给实数(等效线宽口径与高斯一致),两表可直接比较;列序即下面的代码块顺序,
  `nmrforge_api.peak_tables.PEAK_TABLE_COLUMNS`
  是唯一来源(`tests/test_api_docstrings.py` 会逐列比对,文档漏改会直接失败);
- `localization_method` 是**实际**采用的方法(`parabolic`/`gaussian`),
  `localization_requested` 是**请求**的方法;两者不同即表示发生了回退,
  原因在 `fallback`/`fallback_reason`;
- **限定峰的定位(targeted localization,2026-09-19)**:组合表 `localization.targets` /
  CLI `--localize-peaks` / API `localize_peaks=` 只让列表里的峰参与该方法的精修;
  检出、行数、`peak_id` 编号**不变**,未列入目标的峰**保留**、位置取检出阶段的
  三点抛物线估计,该方法的 QC 列写 `NaN`(没做拟合,不是失败);逐峰失败照常写
  `fallback`/`fallback_reason`(不换候选重拟合)。留档:
  `run.json.parameters_resolved.detection.localization_targets`
  (`scope`/`source`/`path`/`sha256`/`n_targets`/`peak_ids` + `by_method` 逐方法
  明细;`scope` 在逐方法写法下是 `mixed`)与
  `peak_localization.<method>.localization_scope`/`n_targeted`/`n_skipped`;
- **条件粒度(2026-09-20)**:目标列表按条件写时(CSV 有 `condition` 列,或条件
  映射写法),同一份 `localization_targets` 记录里:`path`/`sha256` 是**整份来源**
  (整文件哈希),`peak_ids`/`n_targets`/`n_skipped` 是**本 run(本条件)实际生效**
  的那份,另有 `condition`(本 run 的条件)与 `on_missing`(缺行策略),
  `by_condition` 给逐条件明细 `{peak_ids, n_targets, line_ranges(来源行号范围),
  path + sha256(来源文件), from(rows/mapping/default/on_missing=…)}`;没有
  `condition` 列 = 整批共用时 `by_condition` 写 `"all"`,`peak_localization.<method>`
  的计数仍是**逐 run** 口径;
- `peak_id` 是**本谱**的峰序号(该组合自己那张谱的检出顺序);
- `reference_peak_id`(`R0001`…)只属于**参考峰表**;组合模式 2026-09-14 起独立
  选峰,组合峰表里该列与 `assignment` **留空**(`detected` 恒 true——表里只有该
  组合检出的峰);把组合峰匹配回参考峰身份由使用者自己的分析完成;
- `intensity` 为峰强(带符号),`SNR = |intensity| / σ`,σ 为该谱噪声
  (`core.qc.noise` 的 robust MAD),σ 同时写进
  `run.json.parameters_resolved.spectrum_noise_sigma`;
- 逐峰定位记录(`<峰表>.localization.json`、`run.json` 的 `measurements[]`)
  另有拟合规模留档:`roi_half_points`、`roi_half_points_uncapped`、`roi_capped`、
  `roi_capped_axes`、`max_nfev`(用于解释“同样峰在更细网格上耗时变化”);
- 定位 QC 列(两算法同口径):`fit_success`(该行实际用的方法是否成功)、
  `FWHM_H`/`FWHM_N`(按**核名**映射的 FWHM,ppm;抛物线给的是等效线宽)、
  `boundary_hit`(高斯 = 中心/宽度撞拟合边界,抛物线 = 顶点偏移贴 ±0.5 点)、
  `fit_rmse`(残差 RMS,仅高斯有,parabolic 写 `NaN`);
  `fallback`/`fallback_reason` 记录失败回退(禁止静默);
- `duplicate_localization`(bool,P2-5):该行与同表另一行同坐标(ppm 精确到 1e-6)
  ——参考表在独占邻域修复后只剩「取整后落同一格」的分辨率极限,组合表还有选峰器
  亚格点精修收进同一格的情形。重复组每行都标 `true`(不删行);`run.json.warnings`
  另留 `duplicate_localization` 码;
- `condition`/`dataset` 便于使用者把 A/B 表按条件分组;
- 直接维范围留档:`reference.json.params.ext_lo/ext_hi`(参考层)与
  `reference.json.direct_range` = `{ext_lo, ext_hi, unit, source}`,`source` ∈
  `{explicit, params, default}`(P1-4,2026-09-19;`default` = 调用方没给范围、用的是
  后端/配置缺省值,附 `warning`);每条
  `run.json.parameters_resolved.direct_range`(`ext_lo`/`ext_hi` + `source`:
  `reference_or_base` / `combo`);组合模式的 `--direct-range` 与参考冻结范围不一致时
  **默认报错**(退出码 2),必须显式 `--allow-ext-override` 才放行,放行后 run 级警告码
  `direct_range_override`;
- 采样口径留档:`reference.json` 的 `sampling`(有效模式)、`sampling_schedule`
  (`nuslist` / `params` / `full_sampling`)、`sampling_evidence`;每条 `run.json`
  的 `parameters_resolved.sampling`(`effective` / `schedule` / `route` /
  `evidence`)说明该 workflow 走的是 `process()` 还是 `reconstruct_nus()`;
- 选峰阈值留档(参考定义的一部分,后续 workflow 只能沿用):
  `reference.json.peak_params` 的 `sigma_multiplier`(生成参考时选定的值)、
  `previous_sigma_multiplier`(force 重建时的上一版)、
  `detection.sigma_multiplier` 与 `detection.threshold_source`
  (`user` / `default(35sigma)`);每条 `run.json` 另记
  `parameters_resolved.peak_picking_threshold.locked_to_reference = true`;

## 6.3 `workflow.json` / `run.json`

`workflow.json`(组合级):

```json
{
  "workflow_id": "W0001",
  "status": "success_with_warning",
  "parameters_requested": {"zero_fill": 2},
  "conditions": ["A", "B"],
  "condition_records": [{"condition": "A", "status": "...",
                         "parameters_used": {}, "parameters_resolved": {},
                         "phase": {}, "warnings": [], "script_path": "...",
                         "script_sha256": "...", "spectrum_path": "...",
                         "spectrum_sha256": "...", "log_path": "...",
                         "peak_tables": {}, "peak_localization": {},
                         "window": {}, "run_json": "...", "versions": {}}],
  "warnings": [], "versions": {}, "base_script": {}, "grid_sha256": "..."
}
```

`run.json`(每 workflow × 条件)关键字段:

| 字段 | 内容 |
| --- | --- |
| `workflow_id` / `index` / `condition` / `dataset` | 身份与数据来源 |
| `parameters_requested` | 用户原样给的一行(规范 D2) |
| `parameters_used` | 实际喂给后端的完整参数(参考基底 + 覆盖) |
| `parameters_resolved` | `phase`(phase_mode + actual_p0/p1)、`detection`(锁定阈值来源/边距/噪声 σ/精修方法、`localization_targets` = scope + 路径/SHA-256/峰数)、`peak_counts`、`smile`(实际 nSigma/thresh)、`spectrum_noise_sigma`、`direct_range`、`sampling`、`effective_params_backend` |
| `phase` | 逐轴 `phase_mode`(`auto_reference_locked` / `manual_delta_from_reference` / `manual_absolute`)+ `actual_p0/actual_p1` |
| `base_script` | 参考脚本路径 + SHA-256(以参考脚本为模板的证据) |
| `script_path` / `script_sha256` / `spectrum_path` / `spectrum_sha256` | 产物与哈希 |
| `peak_tables` | 被选中精修方式的峰表路径 + SHA-256 + 行数 + detected 数 |
| `peak_localization` | 各方法 n_peaks/n_detected/n_missing/n_fallback/fallback_reasons/n_boundary_hit/`n_duplicate`;targeted 时另有 `localization_scope`(`all`/`subset`)、`n_targeted`、`n_skipped`;参考冻结记录另有 `exclusive_windows`(定位口径:每峰独占邻域) |
| `window` | 选峰边距:物理宽度、等效点数、点距、来源 |
| `script_diff` | 参考脚本 vs 本 workflow 脚本的差异(`n_changed` + 前 20 行 diff):用于审计“只改组合表指定的那几行” |
| `log_path` | 完整日志路径 |
| `versions` | nmrforge / python / 依赖 / NMRPipe / SMILE(真机登记后) |
| `behavior_digest` / `token_digest` | **行为指纹**(2026-09-19):前者对 `core/`+`backend/`+`workflow/`+`nmrforge_api/` 与随包数据取内容 SHA-256,后者去掉注释/docstring 后归一化(中英副本可比) |
| `compat_level` / `compat_affected` / `compat_verified` | 该次运行由哪套行为产生:分级 `same`/`additive`/`behavior_changed`/`contract_changed`(另加 `unverified`);`compat_affected` 是行为变化影响的使用者步骤;`compat_verified` 为 false 表示工作树与声明不一致(别拿去复用) |
| `status` / `warnings` / `message` | 三值状态 + 警告码与计数 |

## 6.4 状态与警告码

| 状态 | 含义 |
| --- | --- |
| `success` | 处理 + 所选精修方式 + 对应峰表全部完成,无警告 |
| `success_with_warning` | 完成但有待注意项(见下,结果可用但需复核) |
| `failed` | 处理/测量失败;原因写入 `message` 与日志,不静默 |

| 警告码 | 触发 |
| --- | --- |
| `peak_count_zero` | 该组合在锁定阈值下一个峰都没检出(检查阈值/数据) |
| `processing_script_not_found` | 没找到该 workflow 的完整处理脚本(运行目录里 `process.com` 缺失);处理结果与峰表仍有效,但脚本溯源不完整,需检查后端落盘位置 |
| `no_spectrum_change` | 该组合在**该条件**下没有改变谱(与参考谱逐位相同):说明这几个参数在该数据上被忽略(窗型/门控不匹配等)或本来无效果;正式 plan 不应把该轴当成真实扰动 |
| `gaussian_fallback` | 高斯拟合失败/回退抛物线(逐峰原因落表) |
| `gaussian_boundary_hit` | 高斯中心/宽度撞拟合边界 |

> 2026-09-14 起组合模式独立选峰(不跟踪参考峰表),因此不再产出
> `peak_not_detected` / `peak_window_edge` / `peak_out_of_range` /
> `window_points_fallback`;非 2D 请求高斯直接报错(`MeasurementError`),
> 不会再产出 `gaussian_unsupported_ndim` 表。

## 6.5 `records/` 与边界

`manifest.json`(组合模式)汇总:数据条件、逐条件参考(脚本/谱/两张峰表哈希)、
显式指定的参考写法(`reference_spec`)与 `mode="combination"`、计划与网格
哈希、峰身份方案(`peak_identity.matching`:组合峰与参考峰的匹配在**外部**)、
workflow 状态计数、软件/依赖/外部工具版本,以及**边界声明**
(`manifest["boundary"]`:软件只执行处理与留档;统计推断由使用者独立
分析程序完成)。

软件**不产出**任何 统计推断与科学结论产物:旧版的
`uncertainty.csv`/`uncertainty_summary.json` 已从 `records/` 移除。
σ/Δδ 汇总代码保留为**测试/检测辅助**(`nmrforge_api.uncertainty`,处理链
不调用),使用者需要时读 `records/peak_table_*.csv` 自行计算或复用该助手;
留档见 ../API_CONTRACT.md。
