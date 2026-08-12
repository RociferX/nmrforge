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
3D 扩展见 §10 契约 v1.4(Spectrum3D 读取/切片/投影)。

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
  auto_phase)、`baseline`(每维基线校正,见下)、`stages`(列表,
  id/tool/macro/params/param_docs);
- 基线校正 `baseline` 键(G2B-007):
  `{"enabled": true, "mode": "auto"|"order", "order": N,
   "axes": "all" | ["F1", "F2"(, "F3")]}`;mode=auto →
  `POLY -auto`,mode=order → `POLY -ord N`,enabled=false 不输出;
  默认全维 auto(与手工 xy.com 对齐);
- SMILE 参数:`nSigma/thresh/xQ3/scaling/report`,经验分档
  (≤20%: 5/0.95;20–30%: 6/0.90;30–40%: 7/0.85);
- 相位:复型 .fid 直接维 p1 共识写脚本 PS;终谱(实型)不做事后调相;
- 峰表:内部 CSV(数字 Peak_ID,2D `Peak_ID,H_shift,N_shift,Intensity,SN,label`;
  3D 加 F1/F2/F3_shift);导出 Poky/Sparky `.list`
  (`Assignment w1 w2 [w3] Data Height Volume`,2D w1=15N/w2=1H,
  未命名 `?-?`/`?-?-?`);导入反向解析。实现见 core/peaks/peak_table.py(G2B-005)。

## 7. 变更流程

1. 请求方在 docs/proposals/<方向>/ 创建 Proposal(模板见 README);
2. Architect 评审接口与兼容性;
3. 批准后在契约文件落地并更新本文件版本号(顶部);
4. 双方测试同步更新;全量回归(本地 + VM)。

## 8. 契约 v1.2 草案(Experiment→Data 层级 + 步骤化处理,待实现)

状态:draft(Architect 设计,2026-08-12)。实现需经两个 Agent 按 Proposal
`gui-to-backend/002-stepwise-processing.md` 与本次层级改造协作。

### 8.1 数据模型变更(core/project/,schema 1.1 → 1.2)

层级:Project → Experiment(可空白)→ Data(可多组,导入数据是实验下的动作)。

```python
@dataclass ExperimentEntry:  # 变更
    id: str                       # exp_001(保持)
    title: str
    status: str                   # registered(空白)/imported(有数据)/...
    sample_id: str
    notes: str
    data: list[DataEntry]         # 新增:0..n 组数据
    metadata: dict
    created_at: str
    # 移除顶层 source/segments/imported_at(迁移到 DataEntry)

@dataclass DataEntry:             # 新增
    id: str                       # d_001
    source: str                   # 外部 Bruker 数据集目录(导入时)
    raw_dir: str                  # 项目内 raw/<exp_id>/<data_id>/ 副本
    segments: list[str]
    status: str                   # imported / fid_ready / processed
    imported_at: str
    metadata_path: str            # metadata/<exp_id>-<data_id>.json
    fid_path: str                 # 生成 FID 后(空串表示未生成)
    spectrum_path: str            # 生成谱后(空串表示未生成)
    checksums: dict[str, str]
```

迁移规则:读取 schema 1.1 的 project.json 时,将旧 ExperimentEntry 的
source/segments/imported_at 迁移为 data[0],并标记 `migrated_from_1_1: true`。

### 8.2 ProjectManager 变更(core/project/)

```python
create_experiment(title="", sample_id="") -> ExperimentEntry   # 新建空白实验
import_data(exp_id, source, segments=None, title="") -> DataEntry
    # 读参数 + 复制 raw/<exp_id>/<data_id>/ + 写 metadata + import WorkflowRun
    # 不生成 FID、不生成谱
set_data_fid(data_id, fid_path)                               # 生成 FID 后登记
set_data_spectrum(data_id, spectrum_path)                     # 生成谱后登记
infer_status(exp_id)                                          # 按 data 聚合
```

兼容:`add_experiment(source=...)` 保留为「导入第一个数据」的便捷入口,
内部等价于 create_experiment + import_data。

### 8.3 处理流程步骤化(Backend + ProcessingController)

```python
# gui/processing.py ProcessingController(实现属 GUI,契约共享)
import_data(entry, source) -> DataEntry            # 读参数+复制,返回数据条目
generate_fid(data) -> str                          # 调后端转换,返回 fid 路径
generate_spectrum(data) -> str                     # 调后端处理(含 NUS 重构),返回谱路径

# backend/base.py ProcessingBackend(契约)
def convert_to_fid(self, experiment, data_dir) -> dict
    # 返回 {"success", "fid_path", "message", "logs"}
def process(self, experiment, plan) -> dict        # 保持;内部自动判断 NUS→重构
```

流程语义:
- 导入数据 = 只读实验参数 + 复制必要文件到 raw,不触发任何处理;
- 生成 FID = bruker -AUTO/fid.com 转换(现有 backend/bruker_workflow 逻辑);
- 生成谱图(原"数据处理")= process,自动包含 SMILE 重构,不需要单独步骤;
- GUI 不暴露单独 SMILE 重构按钮;SMILE 参数优化保留为可选后处理。

### 8.4 产物命名

- raw 副本:raw/<exp_id>/<data_id>/
- metadata:metadata/<exp_id>-<data_id>.json
- fid:processing/<exp_id>/<data_id>.fid(或 data 内)
- 谱:spectra/<exp_id>-<data_id>.ft2|ft3

### 8.5 GUI 树层级

Project(右键:删除项目)→ Experiment(右键:导入数据/重命名/删除)→
Data(右键:生成 FID/生成谱图/打开目录/删除)。树列宽需可读
(最小列宽 + 自适应,禁止只显示首字母)。

## 9. 契约 v1.3(工作区层级 + 数据目录层级)

状态:approved(Architect,2026-08-12;Proposal G2B-003)。
实现:Backend(core/workspace.py + core/project schema 1.3)。

### 9.1 WorkspaceManager(core/workspace.py,Shared)

```python
class WorkspaceManager:
    def __init__(self, root: Path | str | None = None)
        # 默认 ~/NMRForgeWorkspace(Windows/Linux 一致)
    def ensure(self) -> Path                    # 幂等创建工作区
    def list_projects(self) -> list[Path]       # 含 project.json 的项目
    def create_project(self, name, **kwargs) -> ProjectManager
        # workspace/<name>/,非法名/重名抛 WorkspaceError
    def open_project(self, name_or_path) -> ProjectManager
```

### 9.2 数据目录层级(ProjectManager,schema 1.3)

项目 → <exp_id>/ → <data_id>/ → {raw, process, spectra, peaks,
figures, report, metadata.json}

- raw/        导入的数据副本
- process/    fid 与处理中间产物
- spectra/    终谱(ft2/ft3)
- peaks/      峰表 CSV
- figures/    图
- report/     报告
- metadata.json  数据元数据

DataEntry 路径约定(相对项目根):raw_dir = <exp_id>/<data_id>/raw,
metadata_path = <exp_id>/<data_id>/metadata.json,fid_path 在
process/ 内,spectrum_path 在 spectra/ 内。

迁移/兼容:
- schema 1.1/1.2 项目打开时自动迁移到 1.3(旧 source/segments →
  data[0]),旧扁平产物(项目根 raw/metadata/spectra)保留可读,
  infer_status 与删除兼容新旧布局;旧 dir_path 保留为兼容层;
- 不做物理迁移(不搬动旧文件),新导入/处理按 1.3 布局落盘。

## 10. 契约 v1.4(3D 谱查看:读取/切片/投影)

状态:approved(Architect,2026-08-12;实现属 GUI Agent,契约 Shared)。

### 10.1 Spectrum3D(viewer/spectrum.py)

```python
class Spectrum3D:
    data: np.ndarray            # 形状 (F1, F2, F3),float
    axes: list[SpectrumAxis]    # [F1, F2, F3],复用 §4 SpectrumAxis
    source: Path | None
    @classmethod
    def load_from_ft3(cls, path, labels=("F1", "F2", "F3")) -> Spectrum3D
        # nmrglue 读 ft3;复数取实部;轴用 FDF1/FDF2/FDF3 头部
        # (SW/OBS/CAR/ORIG,与 §4 同约定)
    def slice(self, axis_idx: int, index: int) -> Spectrum
        # 固定 axis_idx 的 index,返回其余两轴的二维 Spectrum(切平面)
    def project(self, axis_idx: int, mode: str = "max") -> Spectrum
        # 沿 axis_idx 最大强度投影(MIP,mode="max")或求和
        # (mode="sum"),返回其余两轴二维 Spectrum
    def index_at(self, axis_idx: int, ppm: float) -> int
        # 切片滑块按 ppm 定位(复用 SpectrumAxis.index_at)
```

### 10.2 查看器行为(viewer/app.py + gui/spectrum_panel.py)

- 打开 .ft3 进入 3D 模式:选择查看平面(如 F1-F2),第三轴为切片轴;
- 切片轴滑块按 ppm 定位;投影模式切换 MIP/sum(沿第三轴);
- 切片/投影产物复用现有 SpectrumViewer/ContourLayer 绘制 2D 平面;
- SpectrumWindow 与 GUI 谱图面板均支持 .ft3(文件过滤器、拖放、双击);
- 3D 峰表列(F1/F2/F3_shift)显示不受影响。
