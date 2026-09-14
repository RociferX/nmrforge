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

## 6.2 统一峰表字段

组合模式只写 `localization` 实际选择的方法。用 `resume=False` 重跑时会删除
上一轮未选方法的峰表和定位附件;`records/` 也会删除未选方法的旧汇总。旧版
`peak_positions.csv` 别名不再生成,升级运行时会清除残留。

```text
workflow_id, condition, dataset, peak_id, reference_peak_id, assignment,
H_ppm, N_ppm, intensity, SNR, detected, localization_method,
localization_requested, fallback, fallback_reason,
fit_success, FWHM_H, FWHM_N, fit_rmse, boundary_hit
```

- **两种算法表结构完全一致**;parabolic 不适用的 Gaussian 字段写 `NaN`
  (不是 false/0);
- `peak_id` 是**本谱**的峰序号(该组合自己那张谱的检出顺序);
- `reference_peak_id`(`R0001`…)只属于**参考峰表**;组合模式 2026-09-14 起独立
  选峰,组合峰表里该列与 `assignment` **留空**(`detected` 恒 true——表里只有该
  组合检出的峰);把组合峰匹配回参考峰身份由下游分析完成;
- `intensity` 为峰强(带符号),`SNR = |intensity| / σ`,σ 为该谱噪声
  (`core.qc.noise` 的 robust MAD),σ 同时写进
  `run.json.parameters_resolved.spectrum_noise_sigma`;
- 逐峰定位记录(`<峰表>.localization.json`、`run.json` 的 `measurements[]`)
  另有拟合规模留档:`roi_half_points`、`roi_half_points_uncapped`、`roi_capped`、
  `roi_capped_axes`、`max_nfev`(用于解释“同样峰在更细网格上耗时变化”);
- Gaussian 列:`fit_success`(拟合是否成功)、`FWHM_H`/`FWHM_N`(按**核名**映射
  的 FWHM,ppm)、`fit_rmse`(残差 RMS)、`boundary_hit`(中心/宽度撞拟合边界);
  `fallback`/`fallback_reason` 记录失败回退(禁止静默);
- `condition`/`dataset` 便于下游把 A/B 表按条件分组;
- 直接维范围留档:`reference.json.params.ext_lo/ext_hi`(参考层);每条
  `run.json.parameters_resolved.direct_range`(`ext_lo`/`ext_hi` + `source`:
  `reference_or_base` / `combo`);
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
| `parameters_resolved` | `phase`(phase_mode + actual_p0/p1)、`detection`(锁定阈值来源/边距/噪声 σ/精修方法)、`peak_counts`、`smile`(实际 nSigma/thresh)、`spectrum_noise_sigma`、`direct_range`、`sampling`、`effective_params_backend` |
| `phase` | 逐轴 `phase_mode`(`auto_reference_locked` / `manual_delta_from_reference` / `manual_absolute`)+ `actual_p0/actual_p1` |
| `base_script` | 参考脚本路径 + SHA-256(以参考脚本为模板的证据) |
| `script_path` / `script_sha256` / `spectrum_path` / `spectrum_sha256` | 产物与哈希 |
| `peak_tables` | 被选中精修方式的峰表路径 + SHA-256 + 行数 + detected 数 |
| `peak_localization` | 各方法 n_peaks/n_detected/n_missing/n_fallback/fallback_reasons/n_boundary_hit |
| `window` | 选峰边距:物理宽度、等效点数、点距、来源 |
| `log_path` | 完整日志路径 |
| `versions` | nmrforge / python / 依赖 / NMRPipe / SMILE(真机登记后) |
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
(`manifest["boundary"]`:软件只执行处理与留档;CSP/robustness/统计由下游独立
分析程序完成)。

软件**不产出**任何 CSP/robustness/统计/显著性产物:旧版的
`uncertainty.csv`/`uncertainty_summary.json` 已从 `records/` 移除。
σ/Δδ 汇总代码保留为**测试/检测辅助**(`nmrforge_api.uncertainty`,处理链
不调用),下游需要时读 `records/peak_table_*.csv` 自行计算或复用该助手;
留档见 `docs/tasks/archive/2026-09-13-csp-statistics-boundary.md`。
