# 03 · Python API 参考

```python
import nmrforge_api as nfa      # API_VERSION = "0.1"
```

所有公开名都在 `nmrforge_api/__init__.py` 的 `__all__` 里。接口**不 import Qt**。

## 3.1 一步式入口

### `run_parameter_study(...) -> StudyResult`

```python
run_parameter_study(
    root,                      # 研究根(不存在则创建项目)
    dataset=None,              # Bruker 目录;首次/换数据时给
    *,
    axes,                      # 参数网格 {点号键: [候选值, ...]}
    name="",                   # 新建研究时的项目名
    params=None,               # 参考处理的输入参数(可选)
    phase_route=None,          # 参考运行的相位路线(默认按数据类型)
    peaks=None,                # 可选:外部峰表;默认 None=软件自动选峰
    sigma_multiplier=None,     # 自动选峰阈值(σ 倍数;默认 35)
    max_peaks=0,               # >0 时只保留强度前 N 个峰
    max_runs=256,              # 组合数上限(超出直接报错,不静默截断)
    window_pts=3,              # 峰位搜索窗口(数据点)
    sign="abs",                # "abs" / "positive" / "negative"
    refine="parabolic",        # "parabolic" / "none"
    csp_n_weight=0.2,          # Δδ 公式里 15N 的权重
    resume=True,               # 跳过已成功的组合
    backend=None,              # 注入自定义后端(测试/特殊环境)
    write=True,                # 是否写 records/
    progress=None,             # 进度回调 f(str)
) -> StudyResult
```

行为:开/建研究 → (可选)导入数据 → `build_reference` → `ensure_reference_peaks`
→ `plan_sweep` → `run_sweep` → `position_uncertainty` + `uncertainty_summary`
→ `write_records`。任何一步失败都会抛出对应的 `SensitivityError` 子类。

`StudyResult` 字段:`session`、`reference`、`plan`、`runs`、`uncertainties`、
`summary`、`records`(文件名 → 路径);属性 `root`、`peak_table_path`、
`failed_runs`。

## 3.2 研究会话与数据

### `open_study(root, *, name="", backend=None, config=None, create=True) -> StudySession`

打开或创建研究项目(研究根 = NMRForge 项目根)。`backend` 可注入;否则按默认配置
构造 NMRPipe 后端。研究状态(数据集引用)从 `study/study.json` 恢复。

`StudySession` 属性:`root`、`manager`、`backend`、`dataset`;目录属性
`study_dir`、`work_dir`、`reference_dir`、`runs_dir`、`records_dir`;
方法 `ensure_dirs()`、`reference_dir_for()`、`save_state(**extra)`、`load_state()`、
`data_entry()`、`save()`。

### `add_dataset(session, source, *, exp_id="", title="", make_default=True) -> DatasetRef`

导入一个 Bruker 原始数据集(只做导入:raw 链接 + metadata + 导入运行记录)。
`exp_id` 为空时自动新建实验。非 Bruker 目录或策略守卫拒绝(Kinetics)会抛
`DatasetError`,消息里带原始错误文本。

`DatasetRef` 字段:`exp_id`、`data_id`、`title`、`ndim`、`nuclei`、`sampling`
(`uniform`/`nus`/`uncertain`)、`source`、`raw_dir`、`file_count`、`total_bytes`;
属性 `key`(`"exp_001/d_001"`)。

### `dataset_info(session, dataset=None) -> dict`

数据集摘要 + 研究根 + NMRForge 版本 + 工具版本(适合写进论文材料附件)。

## 3.3 参考谱 / 参考脚本 / 参考峰位

### `build_reference(session, *, params=None, phase_route=None, progress=None, force=False) -> ReferenceSpectrum`

跑一遍自动优化(`generate_fid` → `generate_spectrum`,默认含统一相位优化)。
幂等:已有 `reference.json` 且 `force=False` 时直接返回。冻结三样东西:

- `reference.ft2`(参考谱副本)、`process.com`(**实际执行的**脚本副本);
- `reference.json`:有效参数、脚本/谱 SHA-256、参考相位、版本表、峰表信息;
- `study/work/` 保留转换后的 fid(所有组合复用)。

### `load_reference(session) -> ReferenceSpectrum | None`

读取已冻结的参考谱(缺失返回 `None`;文件丢失抛 `ReferenceError`)。

### `ensure_reference_peaks(session, reference=None, *, sigma_multiplier=None, max_peaks=0, force=False) -> ReferenceSpectrum`

**保证参考峰表存在**:已有则复用;否则在参考谱上自动选峰
(`pick_reference_peaks`),冻结到 `study/reference/<key>/reference.list`,并把
`peak_source="auto"`、峰表 SHA-256、峰数、选峰参数写进 `reference.json`。

### `pick_reference_peaks(session, *, sigma_multiplier=None, out_path=None) -> Path`

直接调用 NMRForge 选峰(`workflow.pick_peaks`),返回峰表路径;`out_path` 给定时
额外复制一份。默认阈值 35σ。

### `set_reference_peaks(session, peak_table, reference=None, *, source="external", params=None) -> ReferenceSpectrum`

登记**外部**峰表(`source="external"`),同样记录 SHA-256/峰数/参数。

### `read_reference_peaks(path) -> list[dict]`

读峰表,支持三种格式:Poky `.list`、NMRForge 旧 CSV(`H_shift`/`N_shift`)、
研究项目 CSV(`peak_id,H_ppm,N_ppm[,height,linewidth,volume]`,按表头自动判别)。
返回统一行:`Peak_ID`、`label`、`H_shift`、`N_shift`、`Intensity`。

### `ReferenceSpectrum` 字段

| 字段 | 说明 |
| --- | --- |
| `dataset_key` / `exp_id` / `data_id` | 归属 |
| `run_id` | 参考运行的项目运行号(`R-YYYYMMDD-NNN`) |
| `phase_route` / `ndim` / `sampling` | 运行路线与数据形态 |
| `spectrum_path` / `frozen_spectrum` | 项目内活动谱 / 研究目录冻结副本 |
| `script_path` / `script_sha256` | 参考脚本副本与哈希 |
| `spectrum_sha256` | 参考谱副本哈希 |
| `params` | 参考运行记录的**全部**有效参数 |
| `sweep_params` | 去掉运行期派生键后、可再次喂给后端的参数(扫描基底) |
| `direct_phase` | 各轴 PS(p0,p1),扫描时用于锁定相位 |
| `peak_table_path` / `peak_table_sha256` / `peak_count` | 参考峰表 |
| `peak_source` / `peak_params` / `peak_created_at` | `auto`(软件选峰)/`external` |
| `created_at` / `software_version` / `tool_versions` / `logs_tail` | 溯源 |

属性/方法:`sweep_supported`(`uniform` 任意维与 **2D NUS** 为 True;3D NUS 为 False)、
`direct_phase_override()`(后端 override 参数)、`normalized_direct_phase()`。

辅助函数:`sanitize_sweep_params(params)`、`normalize_direct_phase(raw)`、
`reference_phase(effective)`(从运行参数取各轴 PS:先 `direct_phase` 后 `phases`)。

## 3.4 参数网格与扫描

### `expand_grid(axes) -> list[dict]`

把 `{"zero_fill": [1, 2], "window.F1.off": [0.35, 0.45]}` 展开成 4 个组合
(点号键保持为键);空轴抛 `SweepError`。

### `merge_overrides(base, overrides) -> dict`

把点号键覆盖合并进嵌套基底(不改原字典),如 `{"window.F1.off": 0.45}` 会更新
`base["window"]["F1"]["off"]`。

### `plan_sweep(reference, *, axes, max_runs=256, base_params=None, notes=None) -> SweepPlan`

组合数超过 `max_runs` 直接抛 `SweepError`(不静默截断)。`base_params` 缺省用
`reference.sweep_params`。若参考没有记录相位,会关闭自动相位搜索并在
`plan.notes` 里说明(`phase_locked=False`)。

`SweepPlan`:`axes`、`combos`、`base_params`、`grid_sha256`、
`reference_script_sha256`、`reference_spectrum_sha256`、`max_runs`、`phase_locked`、
`notes`;属性 `n_combos`。

### `run_sweep(...) -> list[SweepRun]`

```python
run_sweep(
    session, plan,
    *, reference=None, peaks=None,
    window_pts=3, sign="abs", refine="parabolic",
    resume=True, stop_on_error=False, progress=None, on_run=None,
)
```

逐组合:合并参数 → 调后端 `process(params=…, direct_phase_override=…,
script_name="sNNNN.com", out_file="sNNNN.ft2")` → 把脚本与谱复制进
`study/runs/<run_id>/` → 测量峰位 → 写 `run.json`。

- 单组合失败:状态 `failed` + 原因,继续下一组合(`stop_on_error=True` 改为中断);
- `resume=True`:已成功且有谱的组合直接跳过(断点续跑);
- `on_run` 回调在每个组合结束时调用(便于增量上报)。

2D NUS 数据自动改走 `reconstruct_nus()`(候选输出隔离);**3D NUS** 会抛
`SweepError`(见第 9 节)。

`SweepRun`:`run_id`、`index`、`combo`、`params`、`status`、`message`、`run_dir`、
`script_path`/`script_sha256`、`spectrum_path`/`spectrum_sha256`、`wall_time_s`、
`phase_locked`、`logs_tail`、`measurements`。

### `load_plan(session)` / `load_runs(session)`

从 `records/sweep_plan.json` 读回计划;从 `runs/*/run.json` 读回全部运行。

## 3.5 峰位测量

### `measure_peak_positions(spectrum_path, peaks, *, window_pts=3, sign="abs", refine="parabolic", nuclei=None) -> list[PeakMeasurement]`

在一张谱上追踪给定峰表(算法与质量标记见
[07-methods-and-metrics.md](07-methods-and-metrics.md))。可独立使用——例如只借
这一步测量你自己 pipeline 产出的谱。

`PeakMeasurement`:`peak_id`、`assignment`、`reference`(核→ppm)、`positions`、
`deltas`(相对参考)、`intensity`、`found`、`window_edge`、`boundary`、
`out_of_range`。

### `peak_coordinates(row, axes=None) -> dict[str, float]`

峰表行 → `{核名: ppm}`(命名键 `H_shift`/`N_shift`/`C_shift`,或 `F{k}_shift`
按 FDDIMORDER 映射)。

## 3.6 不确定度汇总

### `position_uncertainty(runs, *, csp_n_weight=0.2, nuclei=None) -> list[PeakUncertainty]`

`runs` 可以是 `run_id -> [PeakMeasurement]` 映射,也可以是 `SweepRun` 列表。
只有**所有被测核都测到**的组合进入统计(避免半边数据);默认核
`("1H", "15N")`。

`PeakUncertainty`:`peak_id`、`assignment`、`n_runs`、`missing_runs`、`mean`、
`sigma`、`range`、`delta_std`、`delta_max`、`worst_run`。

### `uncertainty_summary(uncertainties, *, csp_n_weight=0.2, n_runs=0) -> dict`

数据集级:`delta_std_ppm`(min/median/p90/max)、`sigma_ppm`(逐核
median/p90/max)、`n_peaks`、`n_runs`、`definition`(公式说明)。

## 3.7 记录落盘

### `write_records(session, *, reference, plan, runs, uncertainties=None, summary=None, peaks=None) -> dict[str, str]`

写 `study/records/` 的六个产物(字段见
[06-outputs-and-records.md](06-outputs-and-records.md)),返回文件名 → 路径。

## 3.8 异常

```text
SensitivityError(RuntimeError)
├── DatasetError      数据集导入/识别失败(非 Bruker、Kinetics 被拒、路径不存在)
├── ReferenceError    参考谱/脚本构建或加载失败、峰表不存在
├── SweepError        网格非法、组合超上限、NUS 不支持、缺参考峰表
└── MeasurementError  谱不可读、峰表为空、参数非法(window_pts/sign/refine)
```

建议在外部脚本里 `except SensitivityError as exc:` 统一处理并打印 `exc`。
