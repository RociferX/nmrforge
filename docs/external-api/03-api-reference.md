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
- `sigma_multiplier`(选峰阈值,σ 倍数)**在生成参考时可外部指定**:缺省 35σ;
  参考峰表一旦冻结,后续所有 workflow 只能沿用参考阈值——再给不同阈值抛
  `ReferenceError`(改阈值属于重建参考:`force=True` 或删除该条件的
  `study/reference/<key>/`);实际用量写进 `peak_params.sigma_multiplier` /
  `peak_params.previous_sigma_multiplier` /
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
           base_overrides=None, notes=None) -> SweepPlan
```

- `axes` 与 `combos` 必须且只能给一个;`combos` 原样按表序执行,接口**不做**
  设计决策(正交表/部分因子/D-optimal/LHS 由外部工具生成);
- 键支持点号路径(`window.F1.off`、`baseline.F2.enabled`、`zero_fill.F1`、
  `linewidth_hz.F1`、`points_per_line.F1`),两个维度可分别指定;详见
  [05-inputs §5.9](05-inputs-and-data.md)。相位轴 `phase.<轴>.p0|p1`(绝对值)/
  `phase_delta.<轴>.p0|p1`(相对参考的偏差);
- 锁定键(`phases`/`direct_phase`/`phase_route`/`sampling.auto_phase`)报错;
  确定性参数(提取窗口、点距目标、采样表、超时、`fid_noise*`)与未知键写
  `plan.notes` 提示但不阻断;
- `base_overrides` 是批次级覆盖;执行每个条件时按“该条件的参考有效参数 →
  `base_overrides` → 当前组合”合并。旧的绝对 `base_params` 入口已删除;
- `SweepPlan.workflow_ids()` → `["W0001", ...]`。

## 3.4 批量执行

```python
run_sweep(session, plan, *, reference=None, datasets=None,
          localization="parabolic", edge_margin_ppm=None,
          sign="abs",            # 历史参数(检测符号口径固定 dominant)
          roi_f1_ppm=None, roi_f2_ppm=None, resume=True,
          stop_on_error=False, progress=None, on_run=None) -> list[SweepRun]
```

- 每个组合对**全部条件**(缺省 = 会话里所有条件)跑一遍处理,再在**该组合自己的
  候选谱**上用参考锁定阈值独立选峰;返回逐 (workflow, 条件) 记录;
- **与参考共用处理输入**:参考的运行期自动决定(`params.diagnostics`,如直接维
  DC 纠正 `POLY -time`)一并进入组合基底,且候选运行在参考的**条件工作目录**里
  执行——复用参考已转换的 fid,脚本与参考脚本同目录;每个运行目录都保存完整
  `process.com` + SHA-256(找不到时报 `processing_script_not_found`);
- `parameters_used` 基底 = 该条件参考运行的有效参数(相位锁定),随后应用批次
  `base_overrides`,组合表最后只覆盖它显式指定的键;阈值类键(`sigma_multiplier`/`min_snr`/`threshold_sigma`/
  `detection.sigma_multiplier`)写进组合表 → `SweepError`(阈值锁定在参考);
- `localization` = `parabolic`(默认)/ `gaussian`(仅 2D)/ `both`:只输出被选中的
  峰表;逐组合可用组合表的 `localization` 键覆盖;
- `edge_margin_ppm` = 选峰时排除边缘轴峰的物理宽度(缺省 3×该轴核素线宽),
  逐谱按点距换算点数并写进 `run.json.window`;
- 组合峰表**不跟踪参考峰表**:`reference_peak_id`/`assignment` 留空,`detected`
  恒为 true(表里只有该组合检出的峰)。

`SweepRun` 关键字段与方法见 06;`run.peak_table_path("parabolic"|"gaussian")`
给出被选中方法的峰表路径(未选中的方法返回空串)。

## 3.5 峰位测量(低层)

```python
measure_peak_positions(spectrum_path, peaks, *, window_pts=None,
                       window_ppm=None, axes=None, sign="abs",
                       refine="parabolic", nuclei=None, roi_f1_ppm=None,
                       roi_f2_ppm=None, noise_sigma=None) -> list[PeakMeasurement]
detect_and_localize(spectrum_path, *, method="parabolic",
                    sigma_multiplier=None, edge_margin_ppm=None,
                    edge_margin_points=None, roi_f1_ppm=None,
                    roi_f2_ppm=None, sign_mode="dominant", axes=None)
    -> (list[dict], dict)     # 组合模式的独立选峰:peak_id=本谱序号,reference_peak_id=""
read_reference_peaks(path) -> list[dict]      # 补 reference_peak_id
window_points_by_axis(axes, *, window_pts=None, window_ppm=None) -> dict
```

- `detect_and_localize` 是组合模式的选峰入口:物理边距 + `sigma_multiplier`
  (同时作 `min_snr`)+ dominant 符号口径,再按 `method` 精修;没有 `max_peaks`
  (锁定阈值下检出多少峰就写多少峰);非 2D 请求 `gaussian` 抛 `MeasurementError`;
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
write_peak_table(path, rows) -> Path       # 表头 = PEAK_TABLE_COLUMNS(含 peak_id)
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

## 3.9 两种模式:参考模式 / 组合模式(2026-09-14)

接口把「生成参考」和「按参数组合跑处理」拆成**两个模式**,组合模式必须由外部
**显式指定参考**。

### 参考模式

```python
run_reference_study(root, datasets={"A": "~/data/a"},
                    params=None, phase_route=None, peaks=None,
                    direct_range=(10.5, 6.5),         # 直接维范围(high, low;ppm)
                    sigma_multiplier=25,              # 选峰阈值(仅此模式可定)
                    max_peaks=0, localization_method="parabolic",
                    gaussian_roi_f1_ppm=None, gaussian_roi_f2_ppm=None,
                    backend=None, write=True, progress=None) -> ReferenceResult
```

- 导入条件数据(可选)→ 自动优化参考谱与参考脚本 → 两张参考峰表;不做任何参数
  组合;
- 选峰阈值、参考峰表(外部峰表)、localization 都在这阶段确定,之后**锁定**;
- 产物:`study/reference/<key>/`(脚本/谱/两张峰表)+ `study/records/reference.json`;
- `ReferenceResult`:`session` / `references`(key → `ReferenceSpectrum`)、
  `conditions`、`reference(condition="")`、`peak_tables`、`records`。

### 组合模式

```python
run_combination_study(reference,                  # ← 必填:显式指定参考
                      combos=[{"zero_fill": 1}],  # 或 axes=...
                      max_runs=256,
                      localization="parabolic",       # parabolic / gaussian / both
                      edge_margin_ppm=None,             # 缺省 3×核素线宽(物理宽度)
                      direct_range=(10.0, 6.5),        # 覆盖本批 workflow 基值
                      roi_f1_ppm=None, roi_f2_ppm=None,
                      resume=True, backend=None, write=True,
                      progress=None) -> StudyResult
```

`reference` 的写法(字符串/Path,或 `ReferenceHandle`):

| 写法 | 含义 |
| --- | --- |
| `"~/studies/s1"` | 该研究**主条件**的参考;组合跑该研究的全部条件 |
| `"~/studies/s1#B"` | 该研究**条件 B** 的参考;只跑条件 B |
| `"~/studies/s1/study/reference/<key>/reference.json"` | 直接给参考文件(研究根由路径反推;只跑该参考对应条件) |

- 组合**不生成参考**:参数基底 = 该参考的有效参数(相位锁定),组合表只覆盖它
  显式指定的键;**选峰阈值随参考锁定**(阈值键写进组合表 → `SweepError`,
  提示「要改阈值请重建参考」);
- **组合独立选峰**:每个组合在自己的候选谱上独立检出该组合自己的完整峰表
  (`peak_id` = 本谱序号,`reference_peak_id`/`assignment` 留空),峰与参考峰表的
  匹配由下游分析完成;逐组合记录 `parameters_resolved.detection`(锁定阈值来源
  `source="reference(locked)"`、边距、噪声 σ、精修方法列表);
- `localization` 只输出被选中的峰表;逐组合可用组合表 `localization` 键覆盖;
- 参考不存在/峰表缺失 → `ReferenceError`,错误信息指明先跑参考模式;
- 每条运行记录写明参考:`run.json.base_script`(脚本/谱哈希)、
  `parameters_resolved.reference`(参考峰表哈希等),`manifest.json` 记
  `mode="combination"` 与 `reference_spec`;
- 一步式便利入口 `run_parameter_study(...)` 仍然可用:内部先跑参考模式,再用
  `str(root)` 显式调用组合模式(向后兼容)。

直接维范围(ppm)在两个模式都可给:`direct_range=(high, low)`(反序自动换回)、
`direct_range={"lo": …, "hi": …}` 或显式 `ext_lo=/ext_hi=`;参考模式里范围与
已建参考不一致会重建参考谱;组合模式里它覆盖 workflow 基值(参考不重建),逐组合
可用 `ext_lo`/`ext_hi` 再覆盖。逐 workflow 留档在
`parameters_resolved.direct_range`。非法输入抛 `SweepError`。

辅助函数:`parse_reference_spec(spec) -> ReferenceHandle`、
`parse_direct_range(value=None, *, ext_lo=None, ext_hi=None, params=None)`、
`resolve_reference(spec, backend=None) -> (session, DatasetRef, ReferenceSpectrum)`。


## 3.7 错误类型

| 异常 | 何时 |
| --- | --- |
| `DatasetError` | 数据目录不可识别、条件标签重复、研究里没有数据集 |
| `ReferenceError` | 参考谱/产物缺失、峰表不存在 |
| `MeasurementError` | 谱不存在、参数非法、Gaussian 用在非 2D |
| `SweepError` | 组合表/网格非法(锁定键、超上限、无设计输入)、不支持的数据类型 |

四者都继承 `SensitivityError`。
