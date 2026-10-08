# API Contract(Shared Contract 定义)

跨 GUI/Backend 边界的接口与数据结构。任何修改都必须先写变更提案并经评审批准
(流程见 CONTRIBUTING.md)。

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
    rename_experiment / delete_experiment(移系统回收站,软删除可恢复,审计保留)
    delete_data(移系统回收站,软删除可恢复,审计保留) / recover_trashed(原位自动还原)
    infer_status(exp_id) -> ExperimentStatus(registered→imported→processed→picked→analyzed)
    add_sample(**fields) -> SampleEntry / delete_sample(引用保护)
    add_history(action, fields) -> HistoryEntry
    start_run(experiment_id, workflow_ref, inputs, scripts, params) -> WorkflowRun
    finish_run(run_id, status, outputs, message)
    snapshot_run(run_id, scripts, params) -> snapshot 目录
    run_dir(run_id) -> Path        # processing/<exp_id>/runs/<run_id>/
    run_log_path(run_id) -> Path   # 该目录下的 run.log(每次 run 都有,Phase 22)
```

`ExperimentEntry`:id(exp_NNN)/title/source/status/metadata/imported_at/notes/
sample_id/segments。`WorkflowRun`:run_id(R-YYYYMMDD-NNN)/inputs(SHA-256)/
params/outputs/snapshot_dir/status。JSON schema 1.1,原子写。

run 目录(`processing/<exp_id>/runs/<run_id>/`)含 `snapshot/`(脚本+参数快照)与
`run.log`(开始/结束两行 + 该 run 期间按级别记录的日志;路径可由 run_id 推导,不进 schema)。

**记录/状态一律原子写(强制,2026-09-20)**:整份写出、整份读回的 JSON(研究状态、
`run.json`、`workflow.json`、参考与记录 JSON、定位附件、兼容清单导出、逐 step 的
相位/NUS 参数缓存、`*.quality.json` 等)统一走 `core.project.manager.atomic_write_text`
(同目录临时文件 + `os.replace`)—— 读者/断点续跑看到的只会是完整的旧版本或新版本,
不会读到半截记录;追加型日志(`run.log`、`qc_audit.jsonl`)不受此限。该改造**不改字节**:
序列化格式、文件名与指纹一律不变。

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
(provider: 仅 `nmrpipe`,见 `SUPPORTED_PROVIDERS`;未实现的 `native` 骨架已于
2026-09-12 删除,STUB-013)。

## 4. 谱图展示模型(viewer/spectrum.py)

```python
@dataclass(frozen=True) SpectrumAxis:
    label, size, sw_hz, obs_mhz, carrier_ppm, orig_hz
    ppm: np.ndarray(索引→ppm, ORIG 优先；只有 orig_hz=None 才回退 CAR，0 是有效原点)
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
    def generate_fid(self, data, exp_id=None, data_id=None, progress=None) -> str
        # 经 workflow.stepwise.generate_fid → backend.convert_to_fid
    def generate_spectrum(self, data, exp_id=None, data_id=None, progress=None,
                          params=None) -> str
        # 经 stepwise.generate_spectrum → phase_routes.unified_route(统一相位优化)
    def manual_fid_com(...) / run_manual_fid_com(...) / manual_scripts(...) /
        run_manual_spectrum(..., script_baselines=None)  # 可选编辑前文本，仅用于改参审计
    def optimize_smile(self, data, exp_id=None, data_id=None,
                       progress=None, grid_size=None, rank_mode=None) -> str
        # 可选 SMILE 优化(仅 2D NUS):扫描参数网格,候选谱评估后即删,
        # 产物=排序表(CSV/JSON)+前三脚本,不替换活动谱(登记 smile_optimize 运行)
    def rerun_smile_rank1(self, exp_id, data_id, progress=None) -> str
        # 用 Rank1 脚本重跑终谱并采用(登记 smile_optimize_rank1 运行)
    def pick_peaks(self, data, exp_id=None, data_id=None, *,
                   sigma_multiplier=None, ref_peaks=None, ref_nuclei=None,
                   tolerance_ppm=None) -> dict
        # 参考模式(0.2.199-补29dl):ref_peaks/ref_nuclei/tolerance_ppm 可选,
        # 只保留与参考峰表按核名匹配的峰(2D 匹配全部核;3D+2D 参考第三维自由)
```

自动化固定走 `stepwise.generate_fid/generate_spectrum`(内部 create_backend +
unified 相位路线),GUI 页面不得绕过本控制器直接调 Backend。

## 6. 参数/结果约定

- 处理参数统一 dict 键:`zero_fill`、`sampling`(ft_neg/ft_neg_f1/ft_neg_f2/
  flip_f1/flip_f2/ft_alt/auto_phase,0.2.67 起脚本生成消费:`ft_neg` **全局**、
  `ft_neg_f1`/`ft_neg_f2` **逐轴**,三者都是三态 —— None=按采集方式/自动判据、
  True=**加** `FT -neg`、False=**不加**;`ft_alt` True=按采集方式自动/False=强制关闭;
  `auto_phase` False 关闭直接维自动相位)。
  **逐轴键的语义(2026-09-25 用户点名确认)**:`ft_neg_f1`/`ft_neg_f2` 是**直接决定该轴
  加不加** `-neg`(**绝对**),**不是**"翻转/取反自动判据的结果" —— 同一个 `True` 在自动判据
  开或关时都得到"加";`flip_f1`/`flip_f2` 是这两个键的**历史兼容别名**(同义,新代码请用
  `ft_neg_f*`)。优先级:**`ft_neg`(全局) > `ft_neg_f*`(逐轴) > 自动判据**;
  逐轴键只在 `ft_neg` 为 None(或缺省)时生效。
  **`-neg` 的定案(2026-09-25 用户,二次定稿)**:自动判据**默认开启**
  (`AUTO_NEG_JUDGEMENT=True`),判据是**出错概率最低**的简单规则(canonical `-N` / Layer A 已解 ⇒
  采纳;**3D 的 NMRPipe `y` 维(按 `AQSEQ` 认,不是死认逻辑 F2)+ States 族(`FnMODE` 2/3/4/5)
  ⇒ 加 `-neg`**;3D 的 `z` 维、E/A 族(QF 族)与 `FnMODE` **一致** ⇒ 不加;其余 —— 2D 的 States、
  `FnMODE` 缺失、`AQSEQ` 分不清 y/z、**脉程序族与 `FnMODE` 矛盾** —— ⇒ **不加 + 三处同文提醒**
  ("交给用户":不猜也不静默判不加),让用户在谱图步骤用「间接维翻转」控件定)。
  谱图步骤的「间接维翻转」控件改**已生成的终脚本**(只改间接维 FT 行;控件本身是"命令",
  面板先按终脚本现状算出**显式目标状态**再发出去),API/CLI
  建参考时用 `params={"sampling": {"ft_neg_f1"|"ft_neg_f2": true|false}}`(别名 `flip_f*` 同样可用);
  影响符号/方向的键(`ft_neg`/`ft_neg_f1`/`ft_neg_f2`/`flip_f1`/`flip_f2`/`ft_alt`)是**单一来源**
  (`core.experiment.acquisition_mode_detector.sign_sampling_flags`),参考建立过程中的
  派生运行(逐轴相位预览/联合评估谱)与终跑**必须同一符号约定**;
  当前 v1.1 允许显式布尔 FT-neg/逐轴键及 flip 别名作为组合候选；相位路线、
  `sampling.auto_phase` 与 `sampling.ft_alt` 仍锁定。建立参考时 FT 标志先应用，之后优化相位；
  组合更改 FT-neg 不自动重新优化相位，请在候选中显式指定所需 phase/phase_delta。
  `ReferenceSpectrum.sampling_flags` 是**派生只读**属性(从 `params` 取),随
  `reference.json`、参考记录与 `run.json` 的 `parameters_resolved.sampling.flags`
  (默认 `flags_source: reference(locked)`，显式候选另记请求/解析值)一起留档;自动判据的结论逐维记在
  `*.fid.conversion.json` 的 `mode_symbol.dims`
  (`neg_decision`/`neg_basis`/`neg_reason`/`neg_applied`);
- 处理参数统一 dict 键(其余):`baseline`(每维基线校正,见下)、`stages`(列表,
  id/tool/macro/params/param_docs);
- 基线校正 `baseline` 键(G2B-007):
  `{"enabled": true, "mode": "auto"|"order", "order": N,
   "axes": "all" | ["F1", "F2"(, "F3")]}`;mode=auto →
  `POLY -auto`,mode=order → `POLY -ord N`,enabled=false 不输出;
  默认全维 auto(与手工 xy.com 对齐);
- SMILE 参数:`nSigma/thresh/xQ3/scaling/report`,经验分档
  (≤20%: 5/0.95;20–30%: 6/0.90;30–40%: 7/0.85);
- 相位:复型 .fid 直接维 p1 共识写脚本 PS;终谱(实型)不做事后调相;
- 峰表:峰文件为 Poky/Sparky `.list`(保存经 export_peaks_poky 写
  peaks/<exp>-<data>.list;旧 CSV 兼容读取)。内部字段数字 Peak_ID,
  2D `Peak_ID,H_shift,N_shift,Intensity,SN,label`;3D 加
  F1/F2/F3_shift;`.list` 格式 `Assignment w1 w2 [w3] Data Height
  Volume`(2D w1=15N/w2=1H;3D 按外部约定 w1=15N/w2=13C/w3=1H,
  0.2.199-补29dk,用户:.list 与峰表显示都和外部一致,内部按 F1/F2/F3 逻辑
  解读,导出/导入经 nuclei 做外部 w 列 ↔ 内部 F 列置换;未命名
  `?-?`/`?-?-?`);导入反向解析并替换峰表关联(不覆盖文件)。
  实现见 core/peaks/peak_table.py(G2B-005)。

## 7. 变更流程

1. 请求方先写一份变更提案(流程见 CONTRIBUTING.md);
2. 评审接口与兼容性;
3. 批准后在契约文件落地并更新本文件版本号(顶部);
4. GUI 与后端两侧同步更新、跑全量回归后一起发布。

## 8. 契约 v1.2 草案(Experiment→Data 层级 + 步骤化处理,待实现)

状态:draft(2026-08-12)。与步骤化处理提案
`gui-to-backend/002-stepwise-processing.md` 一起实现。

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
    # 读参数 + 链接/复制 raw/<exp_id>/<data_id>/(G2B-009:硬链接→符号链接→
    # 复制回退)+ 写 metadata + import WorkflowRun
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

- raw 链接/副本:raw/<exp_id>/<data_id>/(G2B-009:默认硬链接→符号链接→复制回退)
- metadata:metadata/<exp_id>-<data_id>.json
- fid/中间产物:process/ 内以 <data_id> 为前缀(d_001.fid、d_001_nus.com、
  d_001_preview_*.ft3、d_001_finalize.com 等;2026-08-19 起,重命名不影响)
- 谱:spectra/<data_id>.ft2|ft3;3D 投影 spectra/<data_id>_proj_F{1,2,3}.ft2

### 8.5 GUI 树层级

Project(右键:删除项目)→ Experiment(右键:导入数据/重命名/删除)→
Data(右键:生成 FID/生成谱图/打开目录/删除)。树列宽需可读
(最小列宽 + 自适应,禁止只显示首字母)。

## 9. 契约 v1.3(工作区层级 + 数据目录层级)

状态:approved(2026-08-12;Proposal G2B-003)。
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

- raw/        导入的数据(G2B-009:链接式,默认硬链接→符号链接→复制回退)
- process/    fid 与处理中间产物
- spectra/    终谱(ft2/ft3)
- peaks/      峰表 Poky/Sparky .list(旧 CSV 仅兼容读取,0.2.199-补29ar)
- figures/    图
- report/     日志与历史报告目录(当前唯一生产者是单数据/组日志 log.txt;
              统计推断 报告页与 Report 面板已于 2026-09-12 删除,REPORT-008)
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

状态:approved(2026-08-12;实现属 GUI 一侧,契约 Shared)。
实现:viewer/spectrum.py Spectrum3D(Shared,实现属 GUI)+ viewer/
spectrum3d_panel.py + SpectrumWindow/gui.spectrum_panel 接线。

### 10.1 Spectrum3D(viewer/spectrum.py)

```python
class Spectrum3D:
    data: np.ndarray            # 形状 (F1, F2, F3),float
    axes: list[SpectrumAxis]    # [F1, F2, F3],复用 §4 SpectrumAxis
    source: Path | None
    @classmethod
    def load_from_ft3(cls, path, labels=("F1", "F2", "F3")) -> Spectrum3D
        # nmrglue 读 ft3;复数取实部;轴用 FDF1/FDF2/FDF3 头部
        # (SW/OBS/CAR/ORIG 同 §4 约定);单文件 3D 流(FDPIPEFLAG=1)
        # 读回形状 (F1,F2,F3),F1=FDF3SIZE、F2=FDSPECNUM、F3=FDSIZE;
        # 非流单文件按同约定重塑。
    def slice(self, axis_idx: int, index: int) -> Spectrum
        # 固定第 axis_idx 维的 index,返回其余两轴的二维 Spectrum;
        # 轴序:axis 0 → (F2,F3);axis 1 → (F1,F3);axis 2 → (F1,F2)。
    def project(self, axis_idx: int, mode: str = "max") -> Spectrum
        # 沿 axis_idx 最大强度投影(MIP,mode="max")或求和
        # (mode="sum"),轴序同 slice。
    def index_at(self, axis_idx: int, ppm: float) -> int
        # 第 axis_idx 维按 ppm 定位下标(滑块按 ppm 定位)。
```

### 10.2 查看器行为(viewer/app.py + gui/spectrum_panel.py)

- 打开 .ft3 进入 3D 模式:选择查看平面(如 F1-F2),第三轴为切片轴;
- 3D 模式控件(viewer/spectrum3d_panel.py):查看平面选择
  (F1-F2 / F1-F3 / F2-F3)、第三轴切片滑块(ppm 显示)、投影模式
  切换(MIP/求和);
- 切片/投影产物复用现有 SpectrumViewer/ContourLayer 绘制 2D 平面
  (正黑负红、框选缩放/平移/滚轮均保留);
- SpectrumWindow 与 GUI 谱图面板均支持 .ft3(文件过滤器、拖放、双击),
  按维度数自动进入 2D/3D 模式;
- 3D 峰表列(F1/F2/F3_shift)按当前切片平面轴标签映射,联动不受影响。

## 11. 对外接口契约:`nmrforge_api`(v1.1, 2026-10-03; 当前源码 API 契约)

状态:implemented。对外文档在 `docs/external-api/`。

定位:**参数组合处理执行器**。输入原始 NMR 数据 + 用户参数组合表,输出可追溯的
参考谱/参考峰表、新谱/新峰表及处理记录。软件只负责自动优化参考、执行参数修改并交接，
**不建立谱或峰表之间的对应关系，不做统计推断与结论**（2026-10-02 用户明确）。
每个条件独立生成参考峰表;统一峰表保留完整逻辑轴坐标。参考峰 ID 只在所属峰表内有效,
不代表跨谱对应关系。

### 11.1 工作流语义

2026-10-08：`add_dataset`、`run_reference_study`、`run_parameter_study` 支持
keyword-only `segmented: bool = False`。开启时 source/dataset 接受至少两个
Bruker 原始目录的完整有序列表；多条件用 `datasets={"A": [a1, a2], ...}`。
不是批量独立导入，不猜容器，不要求同一父目录；CLI init 对应 `--segmented`
和重复 `--dataset`。条件绑定完整段列表及顺序，更换需要新条件/研究根，force 不改源绑定。
共用 workflow 导入预检：逐段先拒绝动力学、缺表 NUS，再验证维数/核/有效 TD/谱宽/
采样模式/轴布局/频率/载频。uniform 重复叠加，NUS 同点叠加或互补合并，沿用后端。
DatasetRef 在多段时额外留档 segmented/segments（原始源列表），DataEntry.segments 指向
实际处理副本；导入 metadata.source_segments 与逐段输入 SHA 保留来源，峰表38列不变。

```text
Raw data(A/B…)
    ↓  参考工作流(各条件独立自动优化与选峰):每个条件各有 reference 脚本 + 峰表
Reference workflow
    ↓  用户参数组合表:每行 = 一个 workflow_id(W0001、W0002…)
User-defined workflow ensemble
    ↓  以参考脚本为模板,只替换该组合指定的参数,自动运行处理
Processed spectra(每 workflow × 每条件)
    ↓  每个组合用参考锁定阈值在自己的谱上独立选峰;只对 targeted localization 的目标峰精修
       (非目标留在检测整数格点,定位方法 none)
Parabolic peak table
    ↓
Complete provenance + QC(参数三层、脚本/谱哈希、完整日志、版本、状态)
```

- 参考只作后续参数扰动的基准,**不声称全局最优**;
- 采样路由：合法日程满覆盖且为标准栅格顺序才直接按 uniform 常规 FT；全覆盖乱序
  仍按日程展开归位。NUS 声明下缺表/位置未知时拒绝导入，不凭非零 `ser` 或 100% 参数
  判 uniform。传统网格之后的全零 padding 不算缺采点；判定证据写入参考/运行记录；
- **逐条件参考参数**:`run_reference_study(params_by_condition=...)` 和 CLI
  `reference --condition-params JSON` 允许按条件覆盖公共参考参数;未知条件报错。
  外部峰表仅应用于主条件,不会传播到其他条件;
- **参考载频(carrier ppm)**:`build_reference`、`run_reference_study` 与
  `run_parameter_study` 接受 keyword-only `carrier_ppm: Mapping[str, float] | None`;
  也可写入 `params["carrier_ppm"]` 或点号键 `carrier_ppm.F1`。公共参数中,显式
  `carrier_ppm=` 按轴覆盖 `params`;`params_by_condition` 再按轴覆盖公共结果。
  键为逻辑 F 轴,不是核名或数组位置:2D `F2=x, F1=y`,3D `F3=x, F2=y, F1=z`。
  值须为有限数值(ppm);零和负值有效,布尔值、NaN/Inf、空映射、未知轴或超出维数均拒绝。
  未提供的轴保留参考转换路径中的 CAR;不修改原始 `acqus`。Bruker `-AUTO` 可用且成功时
  保留实际 `fid.com` CAR(AUTO 规则可基于水峰/γ 比等;API 不强写 O1/BF1、不保证固定值)。
  CAR 是 ppm 轴定标,不改变采集 `SFO1`、不属于相位,也不据此推断科学身份;
  仅 uniform AUTO 不可用或执行失败并走内置 `bruk2pipe` fallback 时,载频才来自解析
  `Dimension.o1p`(采集 `O1P` 非零值优先,否则 `O1/BF1`,BF1 缺失时 `O1/SFO1`,均无值则 0)。
  两条路径口径不同;新建 API 参考不继承 GUI 人工值,仅复用相同输入的冻结参考时沿用原 CAR。
  参考转换时实际使用的脚本、
  SHA-256 与 carrier audit/provenance 冻结,并记录 requested/resolved/source。载频绑定
  `reference_input.v1` 完整缓存指纹;任一载频变化都须 `force=True` / CLI `--force` 重建参考。
  组合模式沿用参考载频,`axes`/`combos`/`base_overrides` 不可覆盖 `carrier_ppm`,因其会改变已转换 FID;
- **参考输入指纹与复用**:参考缓存只在完整规范化处理请求完全相同时复用,
  包含参数(点号键与嵌套字典规范化后等价)、`phase_route` 和规范化的直接维范围。
  `reference.json.input_fingerprint` 结构为
  `{"schema":"nmrforge_api.reference_input.v1","params":{...},"sha256":"..."}`;
  SHA-256 对 schema 与规范化 params 的稳定 JSON 字节计算。任何差异(包括 FT、谱宽、
  直接维范围、窗函数、基线或 zero-fill)以及缺失/无效的旧指纹均抛 `ReferenceError`,
  提示显式 `force=True`(CLI `--force`)重建;绝不自动重建。多条件入口先校验全部条件,
  任何一项不匹配时在引擎处理开始前整体失败,不会只重建部分条件;
- **谱宽**:`params.sweep_width_hz` 在建参考时可按逻辑轴显式指定正有限 Hz 值,更新转换
  输入谱宽并记录原值、采用值、来源及 `consistency_ratio`;组合阶段复用已转换 FID,
  不允许更改已转换谱宽;
- **FT 符号候选**:`sampling.ft_neg` / `ft_neg_f1` / `ft_neg_f2` 和兼容别名
  `flip_f1` / `flip_f2` 可作为显式布尔组合候选;全局 `ft_neg` 优先于逐轴设置。
  参考相位固定,`sampling.ft_alt` 仍为锁定键;
- 多条件数据:同一 workflow 对所有条件使用同一份 `parameters_requested`,但各自采用
  本条件参考基底,独立选参考峰与组合峰;不保证、不记录跨谱峰对应关系;
- 外部参考峰表只作用主条件,不传播到其他条件;每份参考峰表中的 `reference_peak_id`
  是局部身份,不是跨条件或参考/组合表的匹配键;
- **组合独立选峰**(2026-09-14):每个组合在**自己的候选谱**上用参考锁定阈值
  独立选峰 → 该组合自己的完整峰表;`reference_peak_id`/`assignment` 留空,
  与参考峰表的匹配由外部(使用者分析)完成;阈值只在参考模式确定并全程锁定
  (组合表写阈值键 → `SweepError`);峰定位只有三点抛物线一种方法
  (2026-09-26 用户需求⑦:二维高斯拟合算法已删除),`localization` 只接受
  `parabolic`,`gaussian`/`both` → `SweepError`;组合模式没有 `max_peaks`;
- **限定峰的定位(targeted localization)**:组合表 `localization.targets`(CSV 路径,
  相对路径按组合表目录解析)/ CLI `--localize-peaks` / API `localize_peaks=` 只对目标峰
  做三点抛物线精修。**检出、行数、`peak_id` 编号不变**;未列入目标的峰保留检测整数格点,
  `localization_method="none"`,定位 QC 列为 `NaN`(没计算,不是失败);未检出峰实际方法同为
  `none`;目标峰才有精修坐标和 QC。失败写入独立的 `failure_reason`,不作为自动回退;不换候选重拟合;目标列表空 /
  文件不存在 / 缺 `peak_id` 列 / 未知 `peak_id` 一律报错(不静默退化成全谱);
  留档:`parameters_resolved.detection.localization_targets`(scope/source/path/
  sha256/n_targets/peak_ids)与 `peak_localization.parabolic.n_targeted`/
  `n_skipped`/`localization_scope`;不给目标 = 全谱(默认行为不变)。
  **逐方法写法已删除**(2026-09-26):`localize_peaks={"gaussian": csv}` 或组合表键
  `localization.targets.gaussian` 直接报 `SweepError`(单方法下没有「逐方法」这回事);
  写法回归方法无关的那一种 —— `localize_peaks=<CSV>` / `localization.targets = <CSV>`
  (或条件列 / 条件映射 / `on_missing`);映射里只剩 `all`/`*` 作公共默认值。
  `--localize-peaks-gaussian` / `--localize-peaks-parabolic` 两个 CLI 选项一并删除,
  只留 `--localize-peaks`(普通 CSV);
- **条件粒度**(2026-09-20):目标列表可按 (workflow, 条件) 给。CSV 可选
  `condition` 列,每个条件只取自己的行、`peak_id` 按**该条件的谱**校验;没有该列 =
  整批共用(与既有逐位一致,留档 `by_condition: "all"`);某条件缺行默认**处理前**
  报错(`on_missing="all"|"none"` 才放行并写进留档),未知条件名报错,空文件 /
  缺 `peak_id` 列沿用既有报错。映射写法 `{"A": "a.csv", "B": "b.csv"}`、
  `{"default": …, "by_condition": {…}}` 与组合表单元格 `"{A: a.csv, B: b.csv}"`
  等价。留档 `parameters_resolved.detection.localization_targets` 增 `by_condition`
  (逐条件 `peak_ids`/`n_targets`/`line_ranges`/来源文件与 SHA-256),检出与峰表
  `peak_id` 编号不变,`peak_localization.parabolic` 的 `n_targeted`/`n_skipped`
  仍是逐 run 口径;
- **行为兼容指纹与变更分级(2026-09-20)**:`compat_manifest()` / CLI
  `python -m nmrforge_api compat [--out FILE] [--golden]` 给出
  `nmrforge_api.compat.v1`:`behavior_digest`(对 `core/`+`backend/`+`workflow/`+
  `nmrforge_api/` 与随包数据 `nmrforge_data/config/nmrforge.yaml`、`nmrforge_data/presets/` 取内容 SHA-256,**按
  行尾归一化后的字节**计算:同一个提交在 Windows(CRLF 检出)与 Linux/VM 指纹一致)、
  `token_digest`(AST 归一化、去注释/docstring,中英副本可比)、`compat_level`
  (`same`/`additive`/`behavior_changed`/`contract_changed`,另加工作树与声明不一致
  时的 `unverified`)、`affected`(行为变化影响的使用者步骤:`reference`/`processing`/
  `sweep_detection`/`localization`/`records`/`api_surface`/`cli`/`qc`)、`contracts`(峰表列序 +
  记录 schema + 错误码 + 警告码)、`defaults`(内置默认值;`localization` 只有
  `default_method: "parabolic"` 与 `targets_on_missing: "error"`)与 `golden`(黄金向量);
  `run.json` / `records/reference.json` / `records/manifest.json` 与 `versions` 并列
  写 `behavior_digest`/`token_digest`/`compat_level`/`compat_affected`/
  `compat_verified`。声明在 `nmrforge_api/compat_declaration.py`(纯数据,排除在指纹
  之外),`tests/test_compat.py` 守卫「指纹未更新 / 分级与 affected 不自洽 / `same`
  但 token 变了 / 黄金向量不复现」。**历史版本注记(2026-09-26)**:删除高斯拟合时曾由
  29 列改为 27 列；这不是当前契约。当前 v1.1 峰表为 38 列，字段和记录口径见下方
  §11.4；代码仍是列序/错误码/警告码的来源;
- 软件最终边界:**不加入**统计推断、显著性判断与科学结论(这些由使用者自己的分析
  基于统一峰表完成)。σ/Δδ 汇总**不进入处理契约与 records 产物**,但代码
  (`nmrforge_api/uncertainty.py`)保留为**测试/检测辅助**:处理链
  (study/sweep/records/CLI)不调用它。不能直接向它输入未经下游匹配的独立选峰结果，
  也不能将处理参数网格的离散度宣称为 CSP 显著性阈值或误差下限。

### 11.2 公开面(`nmrforge_api/__init__.py`,`API_VERSION = "1.1"`;定义点是 `nmrforge_api.session`)

```python
# 两种模式(2026-09-14):参考模式生成参考;组合模式必须显式给参考
run_reference_study(root, dataset=None, *, datasets=None, name="", params=None,
                    params_by_condition=None,
                    # params 可含 reference_optimize(**仅测试/复现用**,
                    # 真实实验禁用;见 external-api/05 §5.10)
                    phase_route=None, peaks=None, direct_range=None,
                    carrier_ppm: Mapping[str, float] | None = None,
                    ext_lo=None, ext_hi=None, sigma_multiplier=None,
                    max_peaks=0, force=False,
                    backend=None, write=True, progress=None) -> ReferenceResult
run_combination_study(reference, *, combos=None, axes=None, max_runs=256,
                      localization="parabolic", localize_peaks=None,
                      edge_margin_ppm=None, window_pts=None, window_ppm=None,
                      sign="abs", direct_range=None, ext_lo=None, ext_hi=None,
                      allow_ext_override=False, resume=True,
                      backend=None, write=True, progress=None) -> StudyResult
                      # window_pts/window_ppm/sign 保留但不再使用(兼容)
                      # localization 只接受 "parabolic"(2026-09-26 起)
parse_reference_spec(spec) -> ReferenceHandle
resolve_reference(spec, *, backend=None) -> (StudySession, DatasetRef, ReferenceSpectrum)
write_reference_records(session, references) -> dict[str, str]
# 一步式便利入口(内部 = 参考模式 + 组合模式,参考显式传研究根)
run_parameter_study(root, dataset=None, *, datasets=None, axes=None, combos=None,
                    name="", params=None, params_by_condition=None, phase_route=None, peaks=None,
                    carrier_ppm: Mapping[str, float] | None = None,
                    direct_range=None, ext_lo=None, ext_hi=None,
                    sigma_multiplier=None, max_peaks=0, max_runs=256,
                    window_pts=None, window_ppm=None, sign="abs",
                    localization="parabolic",        # 峰定位方式(只有 parabolic)
                    localize_peaks=None,              # 组合模式目标峰列表
                    edge_margin_ppm=None, force=False,
                    resume=True, backend=None, write=True, progress=None)
open_study / add_dataset(session, source, condition="A") / dataset_info
build_reference / load_reference / load_references / set_reference_peaks
ensure_reference_peaks / build_reference_peak_tables /
rebuild_reference_peak_tables / pick_reference_peaks
measure_peak_positions / read_reference_peaks / window_points_by_axis
detect_and_localize        # 组合模式独立选峰(rows: peak_id 本谱序号, reference_peak_id="")
                           # 三点抛物线；targets 只对指定峰做精修
read_localization_targets / resolve_localization_targets / LocalizationTargets
conditional_targets_from_file / resolve_conditional_targets /
localization_targets_from_ids
compat_manifest / compat_status / record_stamp / write_compat_manifest
check_conformance / golden_hashes   # 黄金向量(行为自证)
plan_sweep / run_sweep / load_plan / load_runs / load_workflows
write_records / write_peak_table / read_peak_table / peak_table_rows
expand_grid / combos_from_rows / load_combo_table / write_combo_table
design_diagnostics / infer_axes / merge_overrides / sanitize_sweep_params
workflow_id_for / workflow_summary / reference_peak_id
```

CLI:`python -m nmrforge_api {init,reference,peaks,sweep(=workflows),report,status}`。

### 11.3 稳定返回结构

- `DatasetRef`(exp_id/data_id/**condition**/ndim/nuclei/sampling/source/raw_dir);
- `ReferenceSpectrum`(条件、冻结谱与脚本路径 + SHA-256、有效参数、完整输入指纹 +
  `direct_phase`(各轴 PS,自动相位识别的**实际结果**)+
  `sampling_flags`(**派生只读**:参考建立时定下的 FT 符号/方向选择,如
  `{"ft_neg_f1": true}`(别名 `flip_f1` 同义,2026-09-25 用户「API 建立参考也要接入 neg」)——
  组合沿用同一份,
  不得当扫描轴)+ 参考峰身份表
  (`reference.list` + SHA-256 + 峰数 + 来源 `auto`|`external`;旧记录的 `shared:<条件>` 需重建)+
  各条件独立的参考峰表 `reference_peak_table_parabolic.csv`
  (路径 + SHA-256 + 行数/detected 计数)+
  定位 QC + `stage_times_s` + `processing_audit` + 版本表);参考构建记录 ``software_version`` 与 ``software_commit``
  两个独立字段(后者来自 ``NMRFORGE_GIT_COMMIT`` 或 ``git rev-parse HEAD``);
  ``direct_range`` = ``{ext_lo, ext_hi, unit, source}``(``source`` ∈ ``explicit`` /
  ``params`` / ``default``,P1-4,2026-09-19;``default`` = 调用方没给任何范围、用的是
  后端/配置缺省值,记录附 ``warning``);``rebuild_reference_peak_tables()`` 重算峰表时会
  重盖 ``software_version``/``software_commit``/``tool_versions`` 并刷新研究级聚合
  ``records/reference.json``(2026-09-19 修);逐条件聚合记录另含转换 sidecar snapshot;
- `SweepPlan`(axes/combos/base_overrides/grid_sha256 与参考哈希/design/
  diagnostics/`workflow_ids()`);
- `SweepRun`(一个 workflow × 一个条件):`workflow_id`、`condition`、
  `parameters_requested`、`parameters_used`、`parameters_resolved`
  (phase 的 `phase_mode`/`actual_p0`/`actual_p1`、SMILE 实际 `nsigma`/`thresh`、
  谱噪声 σ)、`base_script`(参考脚本路径 + SHA-256)、脚本/谱路径 + SHA-256、
  `peak_tables`(峰表路径 + SHA-256 + 行数;只有 `parabolic` 一个键)、
  `peak_localization`(只有 `parabolic` 键),
  `script_diff`(参考脚本 vs 本 workflow 脚本的差异留档),
  (detected/回退/撞边界计数)、`window`(逐轴物理宽度↔点数换算)、`log_path`
  (完整日志)、`versions`(nmrforge/python/依赖/NMRPipe/SMILE)、`status`∈
  {`success`, `success_with_warning`, `failed`}、`warnings`(码 + 计数 + 峰);
- `StudyResult`(session/plan/references/runs/workflows/summary/records)。

### 11.4 峰表字段(当前 38 列)

峰定位只有三点抛物线一种方法(2026-09-26 用户需求⑦:二维高斯拟合算法整体删除),
所以参考表与组合表**结构完全一致**。常规精修行的 `localization_method` 为
`parabolic`;未检出的参考身份行和 targeted 中跳过精修的非目标行均为 `none`。
`localization_requested` 记录请求方法(`parabolic`),紧邻实际方法列;`failure_reason`
独立记录定位失败,不等同于 `fallback_reason`。Gaussian 与 `fit_rmse` 仍已删除;
此契约不承诺兼容冻结的旧 29 列峰表或 API v0.2。缓存的旧 36 列参考峰表也会被拒绝;
先用 `rebuild_reference_peak_tables()` 从冻结参考谱重建峰表。缺输入指纹的旧参考在参考请求中
必须显式 `force=True` 重建。

统一 schema 含 H/N 兼容列及 F1/F2/F3 的 ppm、核名与等效 FWHM。重复核素时 H/N
兼容别名不指向任一轴,留空;`duplicate_localization` 按完整逻辑维的轴身份和坐标判定。
targeted 非目标行保留检测整数格点,定位方法为 `none`,未计算的定位 QC 为 NaN。

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

末尾 8 列保留可独立测量的参考身份诊断(`intensity_ratio_vs_picked` 与 H/N 位移),
而 `cell_low_H` / `cell_high_H` / `cell_low_N` / `cell_high_N` / `cell_edge`
始终为 `NaN`:联合多维 Voronoi ownership 不能表示成各轴独立边界。实际物理搜索范围
按逻辑轴记录在 `reference.peak_localization.search_windows` 的
`search_bounds_by_axis`(F 轴标识、storage_axis 及 low/high 零基整数点，闭区间);候选所有权冲突单独记作
`candidate_ownership_conflict`,不使用 `cell_edge` 代替。
- `intensity_ratio_vs_picked`(float):**|测得强度| ÷ |峰身份表的 Height|**(分子分母都取绝对值——负峰数据集的 `.list` Height 是负数,2026-09-19 修);缺 Height 或 0 → `NaN`;
  2026-10-02 起检出/参考测量的 intensity 与 SNR 均相对全局中位基线；新生成 Height 同口径，
  定位 QC 留 baseline_offset/height_reference，不改变源谱。旧峰表需重选，不能混用不同峰高基准。
  ≈1 表示停在自己的峰顶上,明显 >1 说明更可能是强峰的肩峰/伴随峰;
- `shift_vs_picked_H` / `shift_vs_picked_N`(float,ppm,符号 = measured − picked,与
  `H_ppm`/`N_ppm` 同轴同向):逐轴位移;¹⁵N 的 ppm 方向与数据索引方向相反,不要读反;
- **组合(workflow)表这 8 列一律写 `NaN`**:sweep 是「选峰即定位」,没有「先给身份坐标、
  再重定位」这一步,写 1.0/0 是伪造信息;
- `duplicate_localization`(bool,P2-5,2026-09-19):该行与**同表另一行**落在同一完整逻辑维坐标
  (各轴 ppm 精确到 1e-6)时为 `true`,重复组的每一行都标(不删行、不改峰集)。参考表与组合表
  都会标;独立记录可能因存储点分辨率或亚格点精修落在同一坐标;
- 参考冻结记录 `reference.json.peak_localization.parabolic` 另有汇总:`n_cell_edge`(保留字段,
  当前为 `null`(未知,不是 0)、
  `n_duplicate`(= 总行数 − 唯一坐标数)与 `intensity_ratio_vs_picked` 的 `n`/`median`/`max`;
  每条 `run.json.peak_localization.parabolic` 也有 `n_duplicate`,出现同坐标行时
  `run.json.warnings` 另留一条 `duplicate_localization`(码 + 行数)。

- 列序 = 下面代码块 = `nmrforge_api.peak_tables.PEAK_TABLE_COLUMNS`(唯一来源,
  `tests/test_api_docstrings.py` 逐列比对);
- `peak_id` 是本谱(该 workflow × 该条件)的峰序号;
- `localization_requested` 当前为 `parabolic`;`localization_method` 是实际结果:
  有可定位峰时为 `parabolic`,未检出或 targeted 跳过时为 `none`。跳过/未检出时
  QC 为 `NaN`,且 `failure_reason` 为空;定位尝试失败时用 `failure_reason` 说明原因
  (如 `no_local_peak_above_threshold`),不伪装成算法 fallback;
- `fallback`/`fallback_reason` 表示实际发生的回退,与定位失败 `failure_reason` 分开。
  `fit_rmse` 不存在;
- `reference_peak_id`(R0001…)是**本参考峰表内的局部身份**,不代表跨条件或参考/组合峰表
  间的对应;组合模式独立选峰,组合峰表该列与 `assignment` 留空,匹配由使用者完成;
- 抛物线在成功定位时写 QC 实数:`fit_success`/`FWHM_H`/`FWHM_N`/
  `boundary_hit` 由三点抛物线给出(等效线宽 `FWHM = 2.3548σ`,`σ² = H/(2|a|)`,
  顶点偏移贴 ±0.5 点即 `boundary_hit`);`fit_rmse` 已随高斯拟合一起删除
  (抛物线是精确解,没有残差);
- 失败/回退(`failure_reason`/`fallback_reason`/`fit_success`)必须逐峰落表,
  禁止静默;缺键才写 `NaN`。
- 多维候选所有权依据联合空间计算,不会投影成逐轴 Voronoi 边界;可测物理搜索 bounds
  与候选所有权冲突分别审计。`duplicate_localization` 仍如实标记同坐标行,不删除行。

### 11.5 产物布局(相对研究根)

```text
study/
  reference/<exp>_<data>/   reference.json、process.com、reference.list、
                            reference_peak_table_parabolic.csv
  workflows/W0001/
      workflow.json         组合级记录(参数三层/状态/警告/两条件产物/版本)
      log.txt               组合级完整日志
      <条件 A|B>/           process.com、spectrum.ft2、peak_table_parabolic.csv、
                            log.txt、run.json
  records/                  manifest.json、sweep_plan.json、runs.json、
                            workflows.json、measurement.json、
                            peak_table_parabolic.csv(唯一的组合汇总长表)
```

### 11.6 强约束(破坏即视为契约破坏)

1. 不 import Qt/gui;不修改 GUI 状态;
2. 不替换项目活动谱:候选谱只写 `study/workflows/`;
3. 只有参考阶段导入/生成及合并 FID；workflow 只处理该条件已冻结的转换产物，禁止自动重转。
   workflow 之间只允许被扫参数不同
   (相位默认锁定在参考值,偏差用 `phase_delta.<轴>.p0|p1`)。
   FID 缺失/损坏、源输入或冻结转换证据不一致、转换期参数变化时，要求用户 force=True
   重新建立参考。NUS 组合不清理源、不修改参考 FID 或采样表；3D uniform 切片按已有流复用。
   具体边界见[API 限制](external-api/09-limitations-and-roadmap.md#99-fid-复用边界);
4. 以参考脚本为模板:每个条件按“自己的参考有效参数 → 批次
   `base_overrides` → 组合显式键”生成 `parameters_used`;不得复制其它条件的基底;
5. 每 workflow × 每条件必须留:完整脚本、统一峰表(`peak_table_parabolic.csv`)、
   完整日志、参数三层,
   版本、状态(三值)与警告;
6. 自动参数必须记录**实际结果**(`actual_p0/actual_p1`、SMILE 实际
   `nsigma`/`thresh` 与谱噪声 σ),定位回退/边界命中必须显式记录;参考记录含
   `stage_times_s` 与 `processing_audit`(`requested`/`resolved`/`ft_commands`);
7. 单条件失败不中断整轮:状态 `failed` + 原因落盘;
8. **不做** 统计推断、显著性判断或科学结论;不自动生成参数空间
   (`combos=` 原样执行,`axes` 只是便捷入口);
9. 参数非法给 error/warning:锁定键报错,确定性/未知键写 `notes`;
10. 峰位窗口按**物理宽度**(ppm)定义,运行时按当前谱点距换算点数,
    换算结果必须留档(`run.json.window`、`records/measurement.json`)。
11. 转换侧车记录数字滤波采集参数、实际转换脚本 SHA-256 与 bruk2pipe 参数。
    无法确认已执行的数字滤波校正方法时必须记 `unknown`,不得由元数据推断。
12. CLI `sweep --study` 根必须与显式参考根相同,不同根在写入前报错;相同根运行会正常写
    workflow 和 records。结构化结果写 stdout(JSON),进度、日志和错误写 stderr。

变更该契约需按 §7 流程走 Proposal;新增参数轴不需改契约(点号键通用)。
