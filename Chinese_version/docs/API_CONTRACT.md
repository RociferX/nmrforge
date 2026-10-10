# 共享接口与API契约

软件 **1.0.5** · Python/CLI API **1.1.1** · 项目schema **1.4**。

## 1. 采集数据模型

[`core/data/internal_data_model.py`](../../core/data/internal_data_model.py)定义读取、规划、工作流与后端之间传递的数据。

| 结构 | 字段与含义 |
| --- | --- |
| `Dimension` | `logical_axis`、`nucleus`、`sf`（MHz）、采用的`sw`（Hz）、原始`sw_ppm`和`sw_hz_raw`、`sw_source`、`sw_note`、`o1`（Hz）、`o1p`（ppm）、`td`、`ft_size`、`acquisition_mode`、`axis_direction`、`role` |
| `Sampling` | `mode`（`uniform`/`nus`/`uncertain`）、`nus_list`、`sampling_fraction`、`schedule_type`、`confidence`、`evidence`、`schedule_file`、`schedule_source` |
| `ExperimentType` | `name`、`confidence`、`evidence` |
| `Experiment` | `dataset_id`、`source_path`、`ndim`、`acquisition_order`、`dimensions`、`sampling`、`experiment_type`、`acquisition_parameters`、`processing_state`、有序`segments` |

`Experiment.direct_dimension`返回`role=direct`的维度。逻辑轴不等于数组位置，需联合解释Bruker采集顺序、正交模式、NMRPipe存储顺序及核身份。
`td`是采集元数据，不能无条件当成复数点数。

[`resolve_sweep_width`](../../core/data/bruker_reader.py)比较`SW_h`与`SW×SFO1`：差值不超过ppm派生值的1%时采用`SW_h`，冲突时采用ppm派生值；
仅一套口径可用时使用该值。采用值、来源和说明与原始字段分别保存；非有限数按缺失处理。
显式校准覆盖按逻辑轴留档，在参考/FID构建阶段生效。

NUS仅采用标准`nuslist`或`acqus.NUSLIST`明确指定的文件。采样表决定增量位置，满覆盖但乱序仍需按表归位。
名义采样比例不足以恢复缺失的位置，尾部存储padding也不代表未采样增量。

## 2. 项目模型与持久化

[`core/project/models.py`](../../core/project/models.py)定义`ProjectInfo`、`ExperimentEntry`、`DataEntry`、`DataGroupEntry`与`WorkflowRun`。

- `ProjectInfo`保存schema、名称/元数据、目录映射、实验、样品与运行。
- `ExperimentEntry.data`包含独立导入的数据；`groups`通过有序`data_ids`组织组操作。
- `DataEntry`保存来源、项目raw副本、有序段、metadata/FID/终谱路径、校验值、状态与回收状态。
  处理状态为`imported`、`fid_ready`、`processed`。
- `WorkflowRun`保存`run_id`、实验/工作流身份、输入、脚本、请求/采用参数、产物、软件/引擎版本、起止时间、状态/消息、快照和决策。

[`ProjectManager`](../../core/project/manager.py)创建/打开/保存项目、导入数据、登记产物、管理数据组、开始/结束运行及执行可恢复删除。
整份JSON状态通过临时文件替换写入；追加日志和审计流逐条保留事件。

## 3. 处理后端协议

[`ProcessingBackend`](../../backend/base.py)是可运行时检查的Protocol。
`BackendCapabilities`声明`provider`、`supports_nus`、`supports_phase_optimization`和`features`。

```python
health_check() -> dict[str, Any]
process(experiment, plan, *, params=None, progress=None) -> dict[str, Any]
convert_to_fid(experiment, data_dir, progress=None, fid_com_overrides=None) -> dict[str, Any]
reconstruct_nus(experiment, params=None, progress=None) -> dict[str, Any]
```

`progress`是可选`Callable[[str], None]`。转换独立生成NMRPipe时域输入；`process`执行`ProcessingPlan`，
`reconstruct_nus`执行NUS路线。`NMRPipeBackend`通过脚本生成与外部进程实现这些方法，GUI不实现NMRPipe命令语法。
脚本与运行时职责见[Backend架构](backend/architecture.md)。

## 4. 谱图与轴接口

[`SpectrumAxis`](../../viewer/spectrum.py)保存`label`、`size`、`sw_hz`、`obs_mhz`、`carrier_ppm`和可选`orig_hz`。
`ppm`、`index_at`、`index_at_f`、`ppm_at`和`ppm_at_f`转换数组索引与化学位移。`orig_hz=0`有效，只有`None`代表缺失。
ORIG存在时坐标为：

```text
ppm[i] = orig_hz / obs_mhz + (size - 1 - i) * sw_hz / (size * obs_mhz)
```

`Spectrum`保存二维数组、轴和metadata，`x_axis=axes[1]`、`y_axis=axes[0]`。
`Spectrum1D`读取一维谱或FID；`Spectrum3D`支持完整数组与懒加载平面、`slice(axis_idx, index)`、
`project(axis_idx, mode)`、平面块/缓存及噪声估计。NMRPipe逻辑身份按`FDDIMORDER`解释，包括同核重复维度；
核名不能单独唯一定位轴。平面视图保留剩余轴的身份与校准。

## 5. 桌面处理适配

[`ProcessingController`](../../gui/processing.py)把`ProjectManager`与工作流连接起来，提供导入、生成FID/终谱、
执行人工脚本、选峰及支持的SMILE优化/重跑入口。调用携带`exp_id`和`data_id`，选择切换后状态、产物与日志仍归原目标。

转换/生成谱图委托`workflow.stepwise`，人工执行委托`workflow.manual`。控制器记录成功步骤和脚本快照；
后台信号把结果交给GUI线程，绘图及控件更新在该线程执行。见[GUI架构](gui/architecture.md)。

## 6. 计划与参数归属

[`ProcessingPlan`](../../core/planning/processing_plan.py)包含`experiment_id`、`ProcessingDag`、`method_choices`、`rationale`和`confidence`。
DAG提供依赖顺序，后端把已解析参数映射到命令。

- 采集/校准设置决定转换FID，谱图参数作用于该FID。
- 请求参数与实际采用值分别留档，不能把自动值写成用户显式请求值。
- GUI与API调用共享处理组件，各自负责入口编排及持久化。

## 7. 运行结果与错误

后端结果为包含成功状态、产物、消息及阶段诊断的映射。转换稳定键为`success`、`fid_path`、`message`、`logs`。
工作流返回登记后的输出路径或抛出错误；外部进程失败不写为处理成功。运行状态和warnings与产物一同保存。

CLI的stdout输出结构化JSON，日志写stderr。API异常区分输入、参考、处理与组合运行失败，详见
[API参考](external-api/03-api-reference.md)和[故障排查](external-api/10-troubleshooting.md)。

## 8. 分步执行

### 8.1 导入

导入建立数据条目、项目raw副本与metadata。显式多段导入保留有序源身份，并记录转换实际使用的目录。

### 8.2 谱图处理

`workflow.stepwise.generate_spectrum(manager, exp_id, data_id, backend, *, params, work_dir, progress)`处理已转换FID。
默认`phase_route="unified"`协调预览、相位优化和完整终跑；`phase_route="none"`直接执行后端。
uniform走常规处理，NUS走SMILE重建。

### 8.3 FID生成

`workflow.stepwise.generate_fid(manager, exp_id, data_id, backend, *, work_dir, progress, params)`执行转换并返回登记的FID路径。
段偏移是转换时参数；`fid_com_overrides`用于人工转换脚本路线。输出形态与存储几何由输入和后端转换逻辑确定。

## 9. 工作区与数据目录

[`WorkspaceManager`](../../core/workspace.py)建立/列出工作区，并创建、打开、重命名或删除其中的项目。
默认工作区为`~/NMRForgeWorkspace`。`ProjectManager.data_base(exp_id, data_id)`定位数据基座，`data_dir`定位其产物。

```text
project/
  project.json
  <experiment_id>/<data_id>/
    metadata.json
    raw/
    process/
    spectra/
    peaks/
    figures/
    report/
    smile_optimized/       # 使用对应操作时
```

目录按操作需要创建。运行快照与项目级目录映射由manager独立解析，数据组成员仍各自保存产物。

## 10. 三维视图

完整三维结果与平面目录都是支持的存储形态。`Spectrum3D`按谱头映射存储轴，读取/缓存所需平面并提供二维切片。
GUI默认F3–F2视图、固定F1，也可保留选定平面。投影汇总三维体，不建立独立三维峰身份，也不能消除全部重叠。

## 11. 对外nmrforge_api契约

### 11.1 工作流语义

API **v1.1.1**仅在`nmrforge_api.session.API_VERSION`定义。研究按条件标签组织，每条件有独立参考谱/峰表。
参考构建导入、转换、合并指定源，并冻结FID、采样表和转换证据。

组合研究只读复用冻结FID，执行显式参数组合/网格，逐workflow和条件独立检测/定位峰；
不回退到重新转换或合并。冻结证据缺失或改变时必须用`force=True`重建参考。
`segmented=True`显式允许每条件输入至少两个原始目录的有序列表。

### 11.2 入口与支持范围

公开入口包括会话/数据集管理、`build_reference`、`detect_and_localize`、`run_reference_study`、
`run_combination_study`、`run_parameter_study`、状态/报告和兼容函数。精确签名/返回结构见
[API参考](external-api/03-api-reference.md)，`python -m nmrforge_api`见[CLI参考](external-api/04-cli-reference.md)。
API可构建3D NUS参考，3D NUS组合执行抛`SweepError`。桌面批量操作仅2D，脚本组合支持矩阵见
[API支持范围](external-api/09-limitations-and-roadmap.md)。

### 11.3 参数、身份与校准

`params_by_condition`可逐条件覆盖共同参考参数。`sweep_width_hz`、`carrier_ppm`和段偏移等转换校准属于参考构建，组合不能修改。
FT-neg/flip可以作为组合候选，相位路线与FT-alt锁定；修改FT-neg不自动重新优化相位。
峰ID只在单张谱内有效，组合`reference_peak_id`和`assignment`留空；API不做跨条件/候选峰匹配或下游统计推断。

### 11.4 峰表字段

统一峰表为**38 列**，顺序如下：

```text
workflow_id, condition, dataset, peak_id, reference_peak_id,
assignment, H_ppm, N_ppm, intensity, SNR,
detected, localization_method, localization_requested, fallback, fallback_reason,
failure_reason, fit_success, FWHM_H, FWHM_N, boundary_hit,
duplicate_localization, F1_ppm, F1_nucleus, FWHM_F1, F2_ppm,
F2_nucleus, FWHM_F2, F3_ppm, F3_nucleus, FWHM_F3,
cell_low_H, cell_high_H, cell_low_N, cell_high_N, cell_edge,
intensity_ratio_vs_picked, shift_vs_picked_H, shift_vs_picked_N
```

`localization_requested`记录请求，`localization_method`记录实际执行（`parabolic`或`none`）。
targeted定位只精修所选峰，不改变检出/行ID；其它检出峰保留整数格点，未计算的QC写`NaN`。
`failure_reason`描述检出或数值QC失败，`fallback_reason`仅描述实际回退。
`fit_success`与等效FWHM描述三点计算结果，不代表峰身份。H/N别名仅在核身份无歧义时填写，完整F1/F2/F3身份仍保留。
见[输出字段与记录语义](external-api/06-outputs-and-records.md)。

### 11.5 产物与续跑

相对研究根目录，产物包括`study/study.json`、参考产物、`study/workflows/<id>/<condition>/`产物，
以及`study/records/manifest.json`、`study/records/workflows.json`、逐run记录与统一峰表。记录保留请求/采用参数、转换/脚本来源、warnings与引擎版本。
续跑复用成功工作前核对workflow/条件输入、参数和参考证据。CLI的`--study`与`--reference`必须解析到同一根目录。

### 11.6 兼容元数据

`compat_manifest()`提供API/参数/表契约与兼容声明。`behavior_digest`散列处理代码/资源，
`token_digest`去掉注释/docstring后比较可执行代码。声明分为`same`、`additive`、`behavior_changed`或`contract_changed`，
affected步骤说明重算范围。黄金向量的谱图/表哈希标识确定性一致性输出。维护操作见[开发说明](development.md)。
