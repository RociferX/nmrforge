# API Contract(Shared Contract 定义)

跨 GUI/Backend 边界的接口与数据结构。任何修改必须通过
docs/proposals/ 下的 Proposal 并经 Architect 批准。

## 1. 数据模型(core/data/internal_data_model.py)

```python
class SamplingMode(str, Enum): UNIFORM / NUS / UNCERTAIN
class AxisRole(str, Enum): DIRECT / INDIRECT

@dataclass Dimension: logical_axis, nucleus, sf, sw, o1, o1p, td, ft_size,
                     acquisition_mode, axis_direction, role
@dataclass Sampling: mode, nus_list, sampling_fraction, schedule_type, confidence, evidence
@dataclass ExperimentType: name, confidence, evidence
@dataclass Experiment:
    dataset_id, source_path, ndim, acquisition_order, dimensions, sampling,
    experiment_type, acquisition_parameters, processing_state, segments
    property direct_dimension
```

来源:Backend 的 `core/data/bruker_reader.read_dataset(path) -> Experiment`;
`read_segments(paths) -> Experiment`(多段合并)。

## 2. 项目管理(core/project/)

```python
ProjectManager:
    create_project(root, name, ...) / open_project(root) / save() / close()
    add_experiment(source, title, sample_id, segments, metadata) -> ExperimentEntry
    rename_experiment / delete_experiment(清产物,审计保留) / set_experiment_notes
    infer_status(exp_id) -> ExperimentStatus(registered→imported→processed→picked→analyzed)
    add_sample(**fields) -> SampleEntry / delete_sample(引用保护)
    add_history(action, fields) -> HistoryEntry
    start_run(experiment_id, workflow_ref, inputs, scripts, params) -> WorkflowRun
    finish_run(run_id, status, outputs, message)
    snapshot_run(run_id, scripts, params) -> snapshot 目录
    build_template_from_run(run_id) -> YAML 模板
```

`ExperimentEntry`:id(exp_NNN)/title/source/status/metadata/imported_at/notes/
sample_id/segments。`WorkflowRun`:run_id(R-YYYYMMDD-NNN)/inputs(SHA-256)/
params/outputs/snapshot_dir/status。JSON schema 1.1,原子写。

## 3. ProcessingBackend(backend/base.py)

```python
@dataclass BackendCapabilities: provider, supports_nus, supports_phase_optimization, features

class ProcessingBackend(Protocol):
    capabilities: BackendCapabilities
    def health_check(self) -> dict            # {"ok": bool, "version": ...}
    def process(self, experiment, plan) -> dict
    def reconstruct_nus(self, experiment, params) -> dict
```

返回 dict 稳定键:`success: bool`、`message: str`、`logs: list[str]`、
`spectrum_path: str | None`、`metrics: dict`。
工厂:`backend/factory.create_backend(config) -> ProcessingBackend`
(provider: nmrpipe | native)。

## 4. 谱图展示模型(viewer/spectrum.py)

```python
@dataclass(frozen=True) SpectrumAxis:
    label, size, sw_hz, obs_mhz, carrier_ppm, orig_hz
    ppm: np.ndarray(索引→ppm, ORIG 优先回退 CAR)
    index_at(ppm) -> int / ppm_at(index) / ppm_at_f(value)

class Spectrum:
    data(二维 float,形状 F1,F2), axes[0]=F1(行), axes[1]=F2(列)
    x_axis / y_axis / max_intensity / estimate_noise(fraction)
    load_from_ft2(path, labels=("F1","F2")) -> Spectrum
```

实现位于 GUI 侧(viewer),但结构是契约;Backend 产出 ft2 时必须保证
头部 FDF1*/FDF2*(SW/OBS/CAR/ORIG) 可被该模型正确解析。

## 5. ProcessingController(gui/processing.py,实现属 GUI)

```python
class ProcessingController:
    def auto_run_sync(self, entry: ExperimentEntry) -> dict:
        # {"status", "message", "logs", "experiment_id"}
    def auto_run_async(self, entry, on_done: Callable[[dict], None],
                       on_error: Callable[[str], None]) -> None
    def manual_param_table(self, entry=None) -> NotImplementedError(占位)
    def manual_script_editor(self, entry=None) -> NotImplementedError(占位)
```

自动化内部固定走 `read_dataset → create_backend(config) → AutoProcessor.run`,
GUI 页面不得绕过本控制器直接调 Backend。

## 6. 参数/结果约定

- 处理参数统一 dict 键:`zero_fill`、`sampling`(ft_neg/ft_alt/flip_f1/
  auto_phase)、`stages`(列表,id/tool/macro/params/param_docs);
- SMILE 参数:`nSigma/thresh/xQ3/scaling/report`,经验分档
  (≤20%: 5/0.95;20–30%: 6/0.90;30–40%: 7/0.85);
- 相位:复型 .fid 直接维 p1 共识写脚本 PS;终谱(实型)不做事后调相;
- 峰表 CSV 列:2D `Peak_ID,H_shift,N_shift,Intensity,SN,label`;
  3D 加 F1/F2/F3_shift。

## 7. 变更流程

1. 请求方在 docs/proposals/<方向>/ 创建 Proposal(模板见 README);
2. Architect 评审接口与兼容性;
3. 批准后在契约文件落地并更新本文件版本号(顶部);
4. 双方测试同步更新;全量回归(本地 + VM)。
