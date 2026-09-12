# 06 · 输出目录与记录字段

## 6.1 目录布局

```text
<研究根>/
├── project.json                 NMRForge 项目(schema 1.4:数据集与运行记录)
├── <exp_id>/<data_id>/          项目数据(raw/process/spectra/peaks/…)
│   └── spectra/<data_id>.ft2    参考谱(活动谱;扫描不替换它)
└── study/
    ├── study.json               研究状态(数据集引用 + 参考谱摘要 + records 路径)
    ├── work/                    处理工作目录:转换后的 fid(所有组合共享)
    ├── reference/<exp>_<data>/
    │   ├── reference.ft2        参考谱冻结副本
    │   ├── process.com          参考运行实际执行的脚本
    │   ├── reference.list       参考峰表(自动选峰或外部)
    │   └── reference.json       参考谱/脚本/峰表的参数与哈希(见 6.3)
    ├── runs/<run_id>/
    │   ├── process.com          该组合实际执行的脚本
    │   ├── spectrum.ft2         该组合的候选谱
    │   └── run.json             该组合的参数/哈希/峰位/日志(见 6.4)
    └── records/                 汇总产物(见 6.2)
```

`run_id` 为 `s0001`、`s0002`…(按网格顺序)。项目自身的运行记录
(`WorkflowRun`,编号 `R-YYYYMMDD-NNN`)记在 `project.json`,用于追溯导入/参考
处理这一步。

## 6.2 `records/` 六个产物

| 文件 | 内容 |
| --- | --- |
| `manifest.json` | 复算所需的全部来源:数据集、参考谱/脚本/峰表(含哈希)、扫描网格与哈希、组合数、版本表 |
| `sweep_plan.json` | 扫描计划(`SweepPlan`):轴、组合列表、基底参数、网格哈希、`phase_locked`、notes |
| `runs.json` | 全部组合的完整记录(`SweepRun` 列表,含逐峰测量) |
| `peak_positions.csv` | 长表:每组合 × 每峰 × 每核一行(最常用于画「参数 → 峰位」) |
| `uncertainty.csv` | 逐峰:σ、极差、Δδ 下限、最差组合 |
| `uncertainty_summary.json` | 数据集级:Δδ 下限分布与逐核 σ 分布 |

重算汇总不需要重跑处理:`python -m nmrforge_api report --study DIR`。

## 6.3 `reference.json` 字段

| 字段 | 说明 |
| --- | --- |
| `dataset_key` / `exp_id` / `data_id` | 归属 |
| `run_id` | 参考运行号 |
| `phase_route` / `ndim` / `sampling` | 运行路线、维度、采样方式 |
| `spectrum_path` / `frozen_spectrum` / `spectrum_sha256` | 项目内活动谱与冻结副本 |
| `script_path` / `script_sha256` | 参考脚本副本与哈希 |
| `params` | 参考运行的有效参数(含 `phases`、`baseline`、`zero_fill`、`window` 等) |
| `sweep_params` | 扫描基底参数(剔除运行期派生键) |
| `direct_phase` | 各轴 PS(p0,p1):扫描时锁定用 |
| `peak_table_path` / `peak_table_sha256` / `peak_count` | 参考峰表 |
| `peak_source` / `peak_params` / `peak_created_at` | `auto`(软件选峰)/`external` |
| `created_at` / `software_version` / `tool_versions` / `logs_tail` | 溯源 |

## 6.4 `runs/<run_id>/run.json` 字段

| 字段 | 说明 |
| --- | --- |
| `run_id` / `index` | 组合编号(如 `s0003` / 3) |
| `combo` | 该组合的覆盖项(点号键 → 值) |
| `params` | 合并后的完整处理参数 |
| `status` / `message` | `success` / `failed`(或 `cancelled`)与说明 |
| `script_path` / `script_sha256` | 本次执行脚本与其哈希 |
| `spectrum_path` / `spectrum_sha256` | 候选谱与其哈希 |
| `wall_time_s` | 处理耗时(秒) |
| `phase_locked` | 是否用了参考相位(应恒为 `true`) |
| `logs_tail` | NMRPipe 日志尾部(最多 40 行) |
| `measurements` | 逐峰测量(见 6.5) |
| `updated` / `software_version` | 写入时间与 NMRForge 版本 |

## 6.5 `peak_positions.csv` 列

| 列 | 说明 |
| --- | --- |
| `run_id` / `combo_index` / `status` | 组合标识与状态 |
| `peak_id` / `assignment` | 峰编号(参考峰表内)与指认标签(若有) |
| `nucleus` | 核名(`1H`/`15N`/`13C`…) |
| `ppm` | 测量的峰位(亚像素) |
| `reference_ppm` | 参考峰位 |
| `delta_ppm` | `ppm - reference_ppm` |
| `intensity` | 极值处强度(实部值,可为负) |
| `found` | 是否测到(`0/1`) |
| `window_edge` | 极值落在搜索窗口边界(真峰可能在窗外) |
| `boundary` | 极值贴谱边界 |
| `out_of_range` | 参考峰位落在谱范围外 |

## 6.6 `uncertainty.csv` 列

`peak_id`、`assignment`、`n_runs`、`missing_runs`,随后逐核三组列
`mean_{核}`、`sigma_{核}`、`range_{核}`,最后
`delta_std_ppm`(主指标)、`delta_max_ppm`、`worst_run`。

## 6.7 `uncertainty_summary.json` 字段

| 字段 | 说明 |
| --- | --- |
| `csp_n_weight` / `n_runs` / `n_peaks` / `n_peaks_unmeasured` | 口径与样本量 |
| `delta_std_ppm` | 各峰 Δδ 下限的 `min`/`median`/`p90`/`max` |
| `sigma_ppm` | 逐核 σ 的 `median`/`p90`/`max` |
| `definition` | 指标的算式与权重说明(可直接引用到方法部分) |

## 6.8 复算与引用

复算一次研究的完整链条:

```text
manifest.json        → 数据来源、参考脚本/谱/峰表哈希、网格哈希、版本
sweep_plan.json      → 每个组合覆盖了哪些参数
runs/<run_id>/run.json → 该组合实际执行的脚本与谱(可单独重跑比对)
```

引用建议:给出 NMRForge 版本(`nmrforge_version`)、NMRPipe 版本
(`tool_versions`)、参考脚本与谱的 SHA-256、网格哈希,以及峰表来源
(`auto`/`external` + 选峰参数)。
