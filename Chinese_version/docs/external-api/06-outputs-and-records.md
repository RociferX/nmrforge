# 06 · 输出与记录(v1.1.1)

## 6.1 目录布局

```text
<root>/
  project.json                    NMRForge 项目(数据与 WorkflowRun 登记)
  study/
    study.json                    条件数据集 + 参考摘要
    work/                         共享 fid、转换侧车与每次运行的脚本/候选谱
        <data>.fid.conversion.json 转换快照(数字滤波元数据、脚本与命令证据)
    reference/<exp>_<data>/
        reference.json            参考状态(参数/哈希/峰表/版本/阶段溯源)
        process.com               参考运行实际执行的完整脚本
        reference.ft2             冻结参考谱
        reference.list            参考峰**身份**表(Poky,含 R0001…)
        reference_peak_table_parabolic.csv
    workflows/W0001/
        workflow.json             组合级记录(见 6.3)
        log.txt                   组合级完整日志(各条件日志串接)
        <条件 A|B>/
            process.com           该条件实际执行的完整处理脚本
            spectrum.ft2          候选谱(不替换活动谱)
            peak_table_parabolic.csv  该条件的统一峰表
            log.txt               该条件的完整运行日志(不是尾部)
            run.json              该条件的完整溯源记录
    records/
        reference.json            参考模式产物(参考谱/脚本/峰表/采样/阈值/审计)
        manifest.json             组合模式产物(数据/参考/计划/峰身份/版本)
        sweep_plan.json           workflow 计划(含 workflow_ids)
        runs.json                 逐 (workflow, 条件) 扁平记录
        workflows.json            逐 workflow 汇总记录
        measurement.json          测量口径与定位 QC 汇总
        peak_table_parabolic.csv  唯一的组合汇总长表(workflow × 条件)
```

## 6.2 统一峰表字段(当前 **38 列**)

多段导入的来源留档：`study.json` 与参考/组合 records 的 `datasets` 中，
该条件的引用增加 `segmented: true` 与 `segments: [原始目录1, 原始目录2, ...]`，
顺序与输入一致；单目录引用不增加这些可选字段。逐条件 `run.json.dataset` 同样保存源段列表。
项目 DataEntry.segments 与导入 `metadata.json.segments` 是处理时实际使用的段目录，
原始列表单独存入 `metadata.json.source_segments`；导入运行的输入按段记录源路径及 SHA-256。
段列表和顺序绑定条件输入，组合 resume 指纹也包含它们；不能只比较第一段判断可复用。
这不增加峰表列，也不创建跨谱或跨条件峰对应关系。

参考 `conversion_provenance.reference_fid` 冻结 FID 文件的快速指纹与工作 `nuslist` 指纹，
组合的 `parameters_resolved.input` 记录 `policy="reference_fid_only"` 及 `reference_run_id`。
组合开始前校验且每次处理前复核；输入不再符合冻结证据时要求重建参考，不现场重转。
快速指纹沿用 ≤8 MiB 内容 SHA-256、大文件 size+mtime_ns 口径，不是内容级认证。
缺少这些证据的参考需用 `force=True` 重建，不能仅重复调用默认参考缓存。
resume 指纹绑定冻结 FID 证据与当前输入策略。

峰位亚像素精修采用三点抛物线方法，所以每个 workflow 写**一张**
`peak_table_parabolic.csv`,参考侧也只有
`reference_peak_table_parabolic.csv` 一张身份表;`records/` 里唯一的组合汇总表是
`records/peak_table_parabolic.csv`。用 `resume=False` 重跑时会清理残留的定位附件与汇总产物。

每个条件在自己的参考谱上独立选峰和登记 `reference.list`;外部峰表只作用于主条件,不传播。
`R0001…` 只在所属参考峰表内标识身份,不证明跨条件或参考/组合谱间存在对应关系。组合表的
`reference_peak_id` 留空,其中 `peak_id` 是本谱序号,不能跨谱连接。

参考记录中的 `input_fingerprint` 为
`{"schema":"nmrforge_api.reference_input.v1","params":{...},"sha256":"..."}`。
它覆盖完整规范化的处理参数、`phase_route` 与直接维范围;等价点号/嵌套参数得到同一输入。
复用要求完整对象一致。差异或缺失/无效的旧指纹必须由调用方显式 `force=True` / `--force`
重建,不会自动重建;多条件参考先全部预检,确认匹配后才启动任一条件的后端处理。
参考峰表必须符合当前 schema；不符合时可通过 `rebuild_reference_peak_tables()` 基于冻结参考谱
重建。输入指纹缺失或无效时，需显式强制重建参考。

```text
workflow_id, condition, dataset,
peak_id, reference_peak_id, assignment,
H_ppm, N_ppm, intensity,
SNR, detected, localization_method, localization_requested,
fallback, fallback_reason, failure_reason,
fit_success, FWHM_H, FWHM_N,
boundary_hit, duplicate_localization,
F1_ppm, F1_nucleus, FWHM_F1,
F2_ppm, F2_nucleus, FWHM_F2,
F3_ppm, F3_nucleus, FWHM_F3,
cell_low_H, cell_high_H, cell_low_N,
cell_high_N, cell_edge, intensity_ratio_vs_picked,
shift_vs_picked_H, shift_vs_picked_N
```

表保留兼容字段 `H_ppm` / `N_ppm` / `FWHM_H` / `FWHM_N`,并按逻辑轴增加
`F1_ppm` / `F1_nucleus` / `FWHM_F1`、F2 与 F3 对应字段。重复核素出现多次时,H/N
别名不指定其中某一轴,写空值;逻辑轴字段仍各自保留坐标与核名。峰表因此可记录完整 1D/2D/3D
逻辑坐标。`duplicate_localization` 按完整逻辑维身份和坐标判定;字段不完整时不猜测重复。

- 最后 8 列描述参考峰身份测量诊断;其中 `cell_low_H` / `cell_high_H` /
  `cell_low_N` / `cell_high_N` / `cell_edge` 始终写 `NaN`,因为联合多维 Voronoi
  ownership 不能表示成逐轴边界。实际物理搜索范围按逻辑 F 轴记录在
  `reference.peak_localization.search_windows[].search_bounds_by_axis`(每轴 low/high
  零基整数点、闭区间及 storage_axis);`candidate_ownership_conflict` 是独立审计标志,不编码为 `cell_edge`;
- `intensity_ratio_vs_picked`(float):**|测得强度| ÷ |峰身份表的 Height|**(分子分母都取绝对值；负峰数据集的 `.list` Height 可能为负);缺 Height 或 0 → `NaN`;
  ≈1 表示停在自己的峰顶上,明显 >1 说明更可能是强峰的肩峰/伴随峰;
- `shift_vs_picked_H` / `shift_vs_picked_N`(float,ppm,符号 = measured − picked,与
  `H_ppm`/`N_ppm` 同轴同向):逐轴位移;¹⁵N 的 ppm 方向与数据索引方向相反,不要读反;
- **组合(workflow)表这 8 列一律写 `NaN`**:sweep 是「选峰即定位」,没有「先给身份坐标、
  再重定位」这一步,写 1.0/0 是伪造信息;
- `duplicate_localization`(bool):该行与**同表另一行**落在同一完整逻辑维坐标
  (各轴 ppm 精确到 1e-6)时为 `true`,重复组的每一行都标(不删行、不改峰集)。参考表与组合表
  都会标;独立记录可能因存储点分辨率或亚格点精修落在同一坐标;
- 参考冻结记录 `reference.json.peak_localization.parabolic` 另有汇总:`qc_failure_reasons`
  (按原因计数;例如三点模板不能给出有效等效线宽、参考峰没有局部峰或坐标不完整;
  这些不是算法 fallback)、`n_cell_edge`(保留字段,当前为 `null`,表示未知而非 0)、
  `n_duplicate`(= 总行数 − 唯一坐标数)与 `intensity_ratio_vs_picked` 的 `n`/`median`/`max`;
  每条 `run.json.peak_localization.parabolic` 也有 `n_duplicate`,出现同坐标行时
  `run.json.warnings` 另留一条 `duplicate_localization`(码 + 行数)。

- 列序即上面的代码块顺序,`nmrforge_api.peak_tables.PEAK_TABLE_COLUMNS`
  是唯一来源;参考表与组合表**结构完全一致(38 列)**;
- `localization_requested` 是请求(`parabolic`);`localization_method` 是实际结果:
  实际执行精修时为 `parabolic`（不代表 QC 一定通过）,未检出或 targeted 跳过时为 `none`。
  未检测/跳过时未计算的 QC 为 NaN；只有正常 targeted 跳过的 `failure_reason` 为空。
  未检出或数值 QC 失败则写独立原因(例如 `no_local_peak_above_threshold`),
  不等同于 `fallback_reason`;
- `fallback`/`fallback_reason` 只表示实际回退;数值 QC 不等于峰真实性检验;
- **限定峰的定位（targeted localization）**:组合表 `localization.targets` /
  CLI `--localize-peaks` / API `localize_peaks=` 选择精修目标。检出、行数、`peak_id`
  编号**不变**;非目标峰保留检出整数格点,`localization_method="none"`,定位 QC 列写
  `NaN`(没计算该 QC,不是失败)。目标峰做三点抛物线精修。留档:
  `run.json.parameters_resolved.detection.localization_targets`
  (`scope`/`source`/`path`/`sha256`/`n_targets`/`peak_ids`;`scope` ∈
  `all`/`subset`/`none`)与
  `peak_localization.parabolic.localization_scope`/`n_targeted`/`n_skipped`;
- **按条件指定目标**:目标列表按条件写时(CSV 有 `condition` 列,或条件
  映射写法),同一份 `localization_targets` 记录里:`path`/`sha256` 是**整份来源**
  (整文件哈希),`peak_ids`/`n_targets`/`n_skipped` 是**本 run(本条件)实际生效**
  的那份,另有 `condition`(本 run 的条件)与 `on_missing`(缺行策略),
  `by_condition` 给逐条件明细 `{peak_ids, n_targets, line_ranges(来源行号范围),
  path + sha256(来源文件), from(rows/mapping/default/on_missing=…)}`;没有
  `condition` 列 = 整批共用时 `by_condition` 写 `"all"`,`peak_localization.parabolic`
  的计数仍是**逐 run** 口径;
- `peak_id` 是**本谱**的峰序号(该组合自己那张谱的检出顺序);参考峰 ID 也只在其本地参考表内有效;
- `reference_peak_id`(`R0001`…)只属于**参考峰表**;组合模式独立
  选峰,组合峰表里该列与 `assignment` **留空**(`detected` 恒 true——表里只有该
  组合检出的峰);把组合峰匹配回参考峰身份由使用者自己的分析完成;
- `intensity` 为相对该谱全局中位基线的带符号峰高，`SNR = |intensity| / σ`，σ 为该谱噪声。
  检出与参考测量同口径；定位 QC 记录 `baseline_offset` / `height_reference`，不修改原谱。
  (`core.qc.noise` 的 robust MAD),σ 同时写进
  `run.json.parameters_resolved.spectrum_noise_sigma`;
- 参考峰重定位只读冻结谱,不会平移或修改谱数据;低质量或邻峰竞争导致的未检出/歧义
  结果通过 `detected` 与定位 QC 留档,参考峰身份行保留,不会因对齐不确定而自动删峰;
- targeted localization 只对目标峰做三点抛物线精修。未被选中的峰留在检出整数格点，
  `localization_method="none"`;未计算的定位 QC 按字段类型写 NaN/空值，不表示失败。
- 逐峰定位记录(`<峰表>.localization.json`、`run.json` 的 `measurements[]`)
  含 `localization_method`/`requested_method`/`actual_method`/`fallback`/
  `fallback_reason`/`boundary_hit`(三点抛物线是闭式解,没有 ROI/迭代预算之类的
  拟合规模参数);
- 定位 QC 列:`fit_success`(三点抛物线在该行是否给出有限等效线宽——峰贴谱边界、
  三点模板非凹时可为 `false`,不是拟合失败)、
  `FWHM_H`/`FWHM_N`(唯一对应 `1H`/`15N` 核时的兼容别名)及 `FWHM_F1`/`FWHM_F2`/
  `FWHM_F3`(按**逻辑轴**记录的等效线宽,`FWHM = 2.3548σ`、`σ² = H/(2|a|)`,ppm)、
  `boundary_hit`(顶点偏移贴在 ±0.5 点,说明真峰顶可能落在三点模板之外);
  `fallback`/`fallback_reason` 保留为结构留档(禁止静默);
- `duplicate_localization`(bool):该行与同表另一行同坐标(ppm 精确到 1e-6)
  ——独立记录可能因存储点分辨率或亚格点精修落在同一坐标。重复组每行都标 `true`(不删行);`run.json.warnings`
  另留 `duplicate_localization` 码;
- `condition`/`dataset` 便于使用者把 A/B 表按条件分组;
- 直接维范围留档:`reference.json.params.ext_lo/ext_hi`(参考层)与
  `reference.json.direct_range` = `{ext_lo, ext_hi, unit, source}`,`source` ∈
  `{explicit, params, default}`(`default` = 调用方没给范围、用的是
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
                         "window": {}, "run_json": "...", "versions": {},
                         "stage_times_s": {"processing": 0.0,
                                            "detection_localization": 0.0,
                                            "total": 0.0}}],
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
| `peak_tables` | 峰表路径 + SHA-256 + 行数 + detected 数(只有 `parabolic` 一个键) |
| `peak_localization` | 只有 `parabolic` 键:`n_peaks`/`n_detected`/`n_missing`/`n_fallback`/`fallback_reasons`/`n_boundary_hit`/`n_duplicate`/`qc_failure_reasons`;后者按 QC 失败原因计数,三点模板无有效线宽或参考峰无局部峰等原因不是算法 fallback。targeted 时另有 `localization_scope`(`all`/`subset`/`none`)、`n_targeted`、`n_skipped`;参考冻结记录另有 `search_windows`(逐 F 轴物理搜索边界) 与 `candidate_ownership_conflict` |
| `stage_times_s` | 阶段用时;workflow 的每条 `condition_records[]` 会复制其 run 的阶段计时,`total` 与 `wall_time_s` 一致 |
| `window` | 选峰边距:物理宽度、等效点数、点距、来源 |
| `script_diff` | 参考脚本 vs 本 workflow 脚本的差异(`n_changed` + 前 20 行 diff):用于审计“只改组合表指定的那几行” |
| `log_path` | 完整日志路径 |
| `versions` | nmrforge / python / 依赖 / NMRPipe / SMILE(真机登记后) |
| `behavior_digest` / `token_digest` | **行为指纹**:前者对 `core/`+`backend/`+`workflow/`+`nmrforge_api/` 与随包数据取内容 SHA-256,后者去掉注释/docstring 后归一化 |
| `compat_level` / `compat_affected` / `compat_verified` | 该次运行由哪套行为产生:分级 `same`/`additive`/`behavior_changed`/`contract_changed`(另加 `unverified`);`compat_affected` 是行为变化影响的使用者步骤;`compat_verified` 为 false 表示工作树与声明不一致(别拿去复用) |
| `status` / `warnings` / `message` | 三值状态 + 警告码与计数 |

## 6.4 状态与警告码

| 状态 | 含义 |
| --- | --- |
| `success` | 处理 + 抛物线定位 + 峰表全部完成,无警告 |
| `success_with_warning` | 完成但有待注意项(见下,结果可用但需复核) |
| `failed` | 处理/测量失败;原因写入 `message` 与日志,不静默 |

| 警告码 | 触发 |
| --- | --- |
| `peak_count_zero` | 该组合在锁定阈值下一个峰都没检出(检查阈值/数据) |
| `processing_script_not_found` | 没找到该 workflow 的完整处理脚本(运行目录里 `process.com` 缺失);处理结果与峰表仍有效,但脚本溯源不完整,需检查后端落盘位置 |
| `no_spectrum_change` | 该组合在**该条件**下没有改变谱(与参考谱逐位相同):说明这几个参数在该数据上被忽略(窗型/门控不匹配等)或本来无效果;正式 plan 不应把该轴当成真实扰动 |
| `boundary_hit` | 有峰的三点抛物线顶点贴在 ±0.5 点边界(真峰顶可能落在三点模板之外;逐峰原因落表) |
| `duplicate_localization` | 同表出现同坐标(ppm 精确到 1e-6)的重复行;码里带行数 |
| `direct_range_override` | 组合模式用了 `--allow-ext-override`,脚本直接维范围与参考冻结范围不一致 |

## 6.5 `records/` 与边界

`manifest.json`(组合模式)汇总:数据条件、逐条件参考(脚本/谱/`reference_peak_table_parabolic.csv` 哈希)、
显式指定的参考写法(`reference_spec`)与 `mode="combination"`、计划与网格
哈希、峰身份方案(`peak_identity.matching`:组合峰与参考峰的匹配在**外部**)、
workflow 状态计数、软件/依赖/外部工具版本,以及**边界声明**
(`manifest["boundary"]`:软件只执行处理与留档;统计推断由使用者独立
分析程序完成)。

软件不产出统计推断与科学结论。`records/` 保存处理和峰表产物；跨谱峰匹配与统计分析由
下游程序基于峰表完成；
留档见[共享API契约](../API_CONTRACT.md)。

参考 `reference.json` 记录 `stage_times_s` 阶段耗时、`processing_audit` 与
`conversion_provenance`。转换溯源快照在参考建立时冻结；读记录时优先使用该快照，
避免后来变化的 `fid.com` 或 sidecar 覆盖当时证据。
`processing_audit` 区分 FT 符号参数的 `requested`、按采集规则解析的 `resolved` 和脚本中的
`ft_commands`。转换侧车 `<data>.fid.conversion.json` 记录各采集块的数字滤波原始参数，
实际 `fid.com` 的 SHA-256、bruk2pipe 参数与解析值;`records/reference.json` 和组合
`manifest.json` 中的参考记录携带对应转换侧车快照。证据不足时数字滤波校正记录为
`status="unknown"`、`method=null`;这不表示校正已执行,也不由 `GRPDLY` 等元数据推断执行或方法。阶段时间是
逐阶段记录，不应当作包含全部 API 开销的单一总用时。

参考转换的载频审计也随冻结参考记录保存：requested/resolved/source 与实际使用的转换脚本、
SHA-256 和 provenance 对应；修改载频需强制重建参考，原始 `acqus` 不被写回。
显式覆盖可读取 `reference.conversion_provenance.sidecars[].record.carrier.explicit_carrier`：
`axes.F1` 等保存 `requested`、`resolved`、`source="explicit_ppm"`、`conversion_key`；
同一记录另有 `script_name`、`script_sha256`、`command_evidence`（转换脚本文本）。
`resolved` 是最终命令解析的 ppm，不是额外测量的峰位置或浮点头部读回值。

每个 `workflow.json` 的 `condition_records[]` 也包含该条件的 `stage_times_s`，与单条件
`run.json` 阶段计时一致；它是该 run 分阶段用时的副本，不是 workflow 汇总总时长。
