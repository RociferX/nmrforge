# 03 · API 参考(v0.2)

顶层导出见 `nmrforge_api/__init__.py`(`API_VERSION = "0.2"`)。

## 3.1 会话与数据集

```python
open_study(root, *, name="", backend=None, config=None, create=True) -> StudySession
add_dataset(session, source, *, condition="", exp_id="", title="",
            make_default=True) -> DatasetRef
dataset_info(session, dataset=None) -> dict
```

- `root/project.json` 存在则打开,否则新建 NMRForge 项目;研究状态
  (`study/study.json`)恢复条件数据集列表;
- `condition` 缺省自动分配下一个未用字母(A/B/C…);标签必须唯一;
- 导入 = 链接 raw + 写 metadata + 登记 import 运行,**不做**转换/处理。

`StudySession` 关键属性:`root`、`datasets`、`dataset`(主条件)、`conditions`、
`dataset_by_condition(label)`、`study_dir`、`work_dir`、`reference_dir`、
`workflows_dir`、`records_dir`、`save_state()`、`save()`。

## 3.2 参考工作流

```python
build_reference(session, dataset=None, *, params=None, phase_route=None,
                progress=None, force=False) -> ReferenceSpectrum
load_reference(session, dataset=None) -> ReferenceSpectrum | None
load_references(session) -> dict[str, ReferenceSpectrum]        # key = "exp/data"
pick_reference_peaks(session, *, sigma_multiplier=None, out_path=None,
                     details=None, localization_method="parabolic",
                     gaussian_roi_f1_ppm=None, gaussian_roi_f2_ppm=None,
                     dataset=None) -> Path
set_reference_peaks(session, peak_table, reference=None, *,
                    source="external", params=None) -> ReferenceSpectrum
ensure_reference_peaks(session, reference=None, *, sigma_multiplier=None,
                       max_peaks=0, force=False,
                       localization_method="parabolic",
                       gaussian_roi_f1_ppm=None,
                       gaussian_roi_f2_ppm=None) -> ReferenceSpectrum
build_reference_peak_tables(session, reference, *, window_pts=None,
                            window_ppm=None, roi_f1_ppm=None,
                            roi_f2_ppm=None) -> ReferenceSpectrum
```

- `build_reference` 走完整自动链(`generate_fid` → `generate_spectrum`,含统一
  相位优化),冻结谱与**实际执行的脚本**;自动相位识别的实际结果写入
  `direct_phase`(`ReferenceSpectrum.phase_record()` 给出 `phase_mode="auto"` +
  `actual_p0/actual_p1`);
- `ensure_reference_peaks`:主条件自动选峰(或外部峰表)建立峰身份
  `reference.list`;其他条件复制同一身份表;**随后总是**写两张参考峰表
  (`reference_peak_table_parabolic.csv` / `_gaussian.csv`);
- `sigma_multiplier`(选峰阈值,σ 倍数)**可由外部指定**:缺省 35σ;给了就按
  该阈值选峰,与已冻结的不同会自动重新选峰,实际用量写进
  `peak_params.sigma_multiplier` / `peak_params.previous_sigma_multiplier` /
  `peak_params.detection.sigma_multiplier` /
  `peak_params.detection.threshold_source`;
- `localization_method` 只决定参考峰位取法(默认抛物线);高斯在非 2D 数据上
  不静默跳过:高斯表写 `fallback=true` +
  `fallback_reason="gaussian_unsupported_ndim"`。

`ReferenceSpectrum` 关键字段:`dataset_key`、`condition`、`ndim`、`sampling`、
`frozen_spectrum`、`script_path`、`script_sha256`、`spectrum_sha256`、`params`、
`sweep_params`、`direct_phase`、`peak_table_path`(身份表)、`peak_count`、
`peak_source`(`auto|external|shared:<条件>`)、`peak_params`、`peak_tables`
(两张表的路径/哈希/行数/detected 数)、`peak_localization`(定位 QC)、
`tool_versions`;方法:`direct_phase_override()`、`phase_record()`、
`peak_table_parabolic_path`、`peak_table_gaussian_path`。

## 3.3 参数组合与 workflow 计划

```python
expand_grid(axes) -> list[dict]
combos_from_rows(rows, *, axes=None) -> list[dict]
load_combo_table(path) -> list[dict]        # CSV/TSV/YAML/JSON
write_combo_table(path, combos) -> Path
design_diagnostics(combos, *, axes=None) -> dict
infer_axes(combos) -> dict / merge_overrides(base, overrides) -> dict
plan_sweep(reference, *, axes=None, combos=None, max_runs=256,
           base_params=None, notes=None) -> SweepPlan
```

- `axes` 与 `combos` 必须且只能给一个;`combos` 原样按表序执行,接口**不做**
  设计决策(正交表/部分因子/D-optimal/LHS 由外部工具生成);
- 键支持点号路径(`window.F1.off`);相位轴 `phase.<轴>.p0|p1`(绝对值)/
  `phase_delta.<轴>.p0|p1`(相对参考的偏差);
- 锁定键(`phases`/`direct_phase`/`phase_route`/`sampling.auto_phase`)报错;
  确定性参数(提取窗口、点距目标、采样表、超时、`fid_noise*`)与未知键写
  `plan.notes` 提示但不阻断;
- `SweepPlan.workflow_ids()` → `["W0001", ...]`。

## 3.4 批量执行

```python
run_sweep(session, plan, *, reference=None, datasets=None, peaks=None,
          window_pts=None, window_ppm=None, sign="abs", refine=None,
          roi_f1_ppm=None, roi_f2_ppm=None, resume=True,
          stop_on_error=False, progress=None, on_run=None) -> list[SweepRun]
```

- 每个组合对**全部条件**(缺省 = 会话里所有条件)跑一遍处理,再对同一张谱跑
  parabolic 与 Gaussian 两种定位;返回逐 (workflow, 条件) 记录;
- `parameters_used` 基底 = 该条件参考运行的有效参数(相位锁定),组合表只覆盖
  它显式指定的键;`peaks` 可显式给参考峰行(默认读条件参考的身份表);
- `refine` 已废弃(两种方法现在始终都跑),仅为兼容签名保留;
- 窗口按物理宽度定义:缺省 1.5×该轴核素线宽折算 ppm,逐谱按点距换算点数并
  写入 `run.json.window`。

`SweepRun` 关键字段与方法见 06;`run.peak_table_path("parabolic"|"gaussian")`
给出两张峰表路径。

## 3.5 峰位测量(低层)

```python
measure_peak_positions(spectrum_path, peaks, *, window_pts=None,
                       window_ppm=None, axes=None, sign="abs",
                       refine="parabolic", nuclei=None, roi_f1_ppm=None,
                       roi_f2_ppm=None, noise_sigma=None) -> list[PeakMeasurement]
read_reference_peaks(path) -> list[dict]      # 补 reference_peak_id
window_points_by_axis(axes, *, window_pts=None, window_ppm=None) -> dict
```

- `refine`:`parabolic`(默认)|`none`|`gaussian`(**仅 2D**,非 2D 抛
  `MeasurementError("Gaussian peak fitting is currently supported only for
  2D spectra.")`,不降级);
- Gaussian 失败逐峰回退抛物线,`PeakMeasurement.localization` 记录
  `requested_method`/`actual_method`/`fit_success`/`fallback`/
  `fallback_reason`/`fit_rmse`/`boundary_hit` 与按**核名**的
  `fwhm_by_nucleus`/`sigma_by_nucleus`;
- `PeakMeasurement`:`peak_id`、`reference_peak_id`、`assignment`、`reference`、
  `positions`、`deltas`、`intensity`、`noise_sigma`、`snr`、`found`、
  `window_edge`、`boundary`、`out_of_range`、`localization`。

## 3.6 统一峰表与记录

```python
write_peak_table(path, rows) -> Path       # 表头 = PEAK_TABLE_COLUMNS
read_peak_table(path) -> list[dict]        # NaN → float("nan")
peak_table_rows(measurements, *, workflow_id, condition="", dataset="",
                method="parabolic") -> list[dict]
gaussian_fallback_rows(measurements, *, workflow_id, condition="", dataset="",
                       reason) -> list[dict]
reference_peak_id(peak_id) -> str          # 1 → "R0001"
write_records(session, *, reference=None, references=None, plan, runs,
              peaks=None) -> dict[str, str]
```

字段与语义见 06;`write_records` 产 `manifest.json`、`sweep_plan.json`、
`runs.json`、`workflows.json`、`measurement.json`、两张长表
`peak_table_{parabolic,gaussian}.csv`。

## 3.7 错误类型

| 异常 | 何时 |
| --- | --- |
| `DatasetError` | 数据目录不可识别、条件标签重复、研究里没有数据集 |
| `ReferenceError` | 参考谱/产物缺失、峰表不存在 |
| `MeasurementError` | 谱不存在、参数非法、Gaussian 用在非 2D |
| `SweepError` | 组合表/网格非法(锁定键、超上限、无设计输入)、不支持的数据类型 |

四者都继承 `SensitivityError`。
