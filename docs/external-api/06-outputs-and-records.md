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
            peak_table_parabolic.csv
            peak_table_gaussian.csv
            log.txt               该条件的完整运行日志(不是尾部)
            run.json              该条件的完整溯源记录
    records/
        manifest.json             数据/参考/计划/峰身份/版本/边界声明
        sweep_plan.json           workflow 计划(含 workflow_ids)
        runs.json                 逐 (workflow, 条件) 扁平记录
        workflows.json            逐 workflow 汇总记录
        measurement.json          测量口径与定位 QC 汇总
        peak_table_parabolic.csv  全部 workflow × 条件的长表
        peak_table_gaussian.csv   同上(Gaussian 定位)
```

## 6.2 统一峰表字段

```text
workflow_id, condition, dataset, reference_peak_id, assignment,
H_ppm, N_ppm, intensity, SNR, detected, localization_method,
localization_requested, fallback, fallback_reason,
fit_success, FWHM_H, FWHM_N, fit_rmse, boundary_hit
```

- **两种算法表结构完全一致**;parabolic 不适用的 Gaussian 字段写 `NaN`
  (不是 false/0);
- `reference_peak_id`(`R0001`…)在参考峰表建立,所有条件与 workflow 沿用;
  某条件/workflow 没测到该峰 → 保留行,`detected=false`;
- `intensity` 为峰强(带符号),`SNR = |intensity| / σ`,σ 为该谱噪声
  (`core.qc.noise` 的 robust MAD),σ 同时写进
  `run.json.parameters_resolved.spectrum_noise_sigma`;
- Gaussian 列:`fit_success`(拟合是否成功)、`FWHM_H`/`FWHM_N`(按**核名**映射
  的 FWHM,ppm)、`fit_rmse`(残差 RMS)、`boundary_hit`(中心/宽度撞拟合边界);
  `fallback`/`fallback_reason` 记录失败回退(禁止静默);
- `condition`/`dataset` 便于下游把 A/B 表按条件分组;`assignment` 取自参考峰表;
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
| `parameters_resolved` | `phase`(phase_mode + actual_p0/p1)、`smile`(实际 nSigma/thresh)、`spectrum_noise_sigma`、`window`、`effective_params_backend` |
| `phase` | 逐轴 `phase_mode`(`auto_reference_locked` / `manual_delta_from_reference` / `manual_absolute`)+ `actual_p0/actual_p1` |
| `base_script` | 参考脚本路径 + SHA-256(以参考脚本为模板的证据) |
| `script_path` / `script_sha256` / `spectrum_path` / `spectrum_sha256` | 产物与哈希 |
| `peak_tables` | 两张峰表路径 + SHA-256 + 行数 + detected 数 |
| `peak_localization` | 两方法 n_peaks/n_detected/n_missing/n_fallback/fallback_reasons/n_boundary_hit |
| `window` | 逐轴物理宽度、等效点数、点距、来源 |
| `log_path` | 完整日志路径 |
| `versions` | nmrforge / python / 依赖 / NMRPipe / SMILE(真机登记后) |
| `status` / `warnings` / `message` | 三值状态 + 警告码与计数 |

## 6.4 状态与警告码

| 状态 | 含义 |
| --- | --- |
| `success` | 处理 + 两种定位 + 两张峰表全部完成,无警告 |
| `success_with_warning` | 完成但有待注意项(见下,结果可用但需复核) |
| `failed` | 处理/测量失败;原因写入 `message` 与日志,不静默 |

| 警告码 | 触发 |
| --- | --- |
| `peak_not_detected` | 有参考峰未测到(`detected=false`,行保留) |
| `peak_window_edge` | 极值落在搜索窗口边界(真峰可能在窗外) |
| `peak_out_of_range` | 参考峰位置落在谱范围外 |
| `gaussian_fallback` | 高斯拟合失败/回退抛物线(逐峰原因落表) |
| `gaussian_boundary_hit` | 高斯中心/宽度撞拟合边界 |
| `gaussian_unsupported_ndim` | 非 2D 数据:高斯不适用,位置回退抛物线 |
| `window_points_fallback` | 窗口无法按物理宽度换算,回退固定点数 |

## 6.5 `records/` 与边界

`manifest.json` 汇总:数据条件、逐条件参考(脚本/谱/两张峰表哈希)、计划与网格
哈希、峰身份方案、workflow 状态计数、软件/依赖/外部工具版本,以及**边界声明**
(`manifest["boundary"]`:软件只执行处理与留档;CSP/robustness/统计由下游独立
分析程序完成)。

软件**不产出**任何 CSP/robustness/统计/显著性产物:旧版的
`uncertainty.csv`/`uncertainty_summary.json` 已从 `records/` 移除。
σ/Δδ 汇总代码保留为**测试/检测辅助**(`nmrforge_api.uncertainty`,处理链
不调用),下游需要时读 `records/peak_table_*.csv` 自行计算或复用该助手;
留档见 `docs/tasks/archive/2026-09-13-csp-statistics-boundary.md`。
