"""参考谱、参考脚本与两张参考峰表:用 NMRForge 的自动优化跑一次并冻结。

参考是整项研究的零点(2026-09-13 规范):

1. 自动链(``generate_fid`` → ``generate_spectrum``,含统一相位优化)跑一次,
   拿到「软件认为最好」的谱与**它实际执行的脚本**(``process.com`` + SHA-256);
2. 参考峰表:**由软件自动选峰**(或外部峰表)得到稳定的
   ``reference_peak_id``(R0001…),再在同一条参考谱上分别用
   **parabolic** 与 **2D gaussian** 定位写出**两张结构一致的峰表**;
3. 抽出「扫描基底参数」——剔除运行期派生键,只留可再次喂给后端 ``process()``
   的处理参数,保证 workflow 以参考为起点、只改被扫的轴。

多条件(A/B)语义:每个条件各有一份参考(相位/噪声按该条件自身数据决定),
但**峰身份共享**——非主条件的参考峰表由主条件的峰表复制而来,因此同一个
``reference_peak_id`` 在所有条件、所有 workflow 里指向同一个峰。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.project.manager import sha256_file
from core.project.run_refs import STEP_RUN_REFS
from core.version import software_version, tool_versions
from nmrforge_api.errors import ReferenceError
from nmrforge_api.peak_tables import (
    REFERENCE_WORKFLOW_ID,
    gaussian_fallback_rows,
    peak_table_digest,
    peak_table_rows,
    write_peak_table,
)
from nmrforge_api.peaks import (
    measure_peak_positions,
    pick_reference_peaks,
    read_reference_peaks,
    window_points_by_axis,
)
from nmrforge_api.session import (
    DatasetRef,
    StudySession,
    now_iso,
    open_study,
)
from workflow.pick_peaks import read_spectrum_axes

# 运行期派生/仅 GUI 使用的键:不参与扫描(base 参数里必须剔掉,否则会改变
# 后端分支行为——例如 preview_axis 会把 process() 切到预览渲染)
_NON_SWEEP_KEYS: tuple[str, ...] = (
    "phase_route",
    "preview_axis",
    "projections",
    "backend_runs",
    "diagnostics",
    "fill",
    "nus",
    "final_ext_lo",
    "final_ext_hi",
    "segment_shift_hz",
)

#: 参考运行里由**自动诊断/路由**做出、又必须被组合运行沿用的行为决定:
#: 它们只写在 ``params['diagnostics']`` 里,不提升成顶层键,组合就会落到
#: 后端默认值 → 组合脚本与参考脚本在**没人指定**的参数上不同(真机实例:
#: 直接维 DC 偏置 → 参考脚本含 ``nmrPipe -fn POLY -time``,组合脚本没有)。
_RUNTIME_DECISION_KEYS: tuple[tuple[str, str], ...] = (
    ("direct_poly_time", "apply_poly_time"),
)

REFERENCE_FILENAME = "reference.json"
REFERENCE_PEAK_LIST_FILENAME = "reference.list"
REFERENCE_TABLE_FILENAMES = {
    "parabolic": "reference_peak_table_parabolic.csv",
    "gaussian": "reference_peak_table_gaussian.csv",
}
GAUSSIAN_UNSUPPORTED_NDIM_REASON = "gaussian_unsupported_ndim"


@dataclass
class ReferenceSpectrum:
    """冻结的参考谱 + 参考脚本 + 有效参数 + 两张参考峰表。"""

    dataset_key: str
    exp_id: str
    data_id: str
    condition: str = ""
    run_id: str = ""
    phase_route: str = ""
    ndim: int = 2
    # 有效采样模式:满采样(含「标注 NUS 但实际满采样」)会降级为 uniform
    sampling: str = "uniform"
    sampling_schedule: str = ""      # 来源:nuslist/params/full_sampling/…
    sampling_evidence: list[str] = field(default_factory=list)
    spectrum_path: str = ""            # 项目 spectra/ 下的活动谱
    frozen_spectrum: str = ""          # 研究目录内的副本
    script_path: str = ""              # 研究目录内的参考脚本副本
    script_sha256: str = ""
    spectrum_sha256: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    sweep_params: dict[str, Any] = field(default_factory=dict)
    # 该条件的处理工作目录(参考与全部 workflow 共用:复用同一份 fid,
    # 候选脚本与参考脚本同目录);旧参考为空时按数据级目录回退。
    work_dir: str = ""
    # 参考运行的各轴 PS(p0,p1):扫描时传给后端 direct_phase_override,
    # 让候选谱与参考谱相位一致(自动相位识别的**实际结果**)。
    direct_phase: dict[str, list[float]] = field(default_factory=dict)
    # 参考峰表:reference.list 是身份表(Poky),两张 CSV 是两种定位算法表
    peak_table_path: str = ""
    peak_table_sha256: str = ""
    peak_count: int = 0
    peak_source: str = ""          # auto(NMRForge 选峰)| external | shared:<条件>
    peak_params: dict[str, Any] = field(default_factory=dict)
    peak_created_at: str = ""
    peak_tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    peak_localization: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
    software_version: str = ""
    tool_versions: dict[str, str] = field(default_factory=dict)
    logs_tail: list[str] = field(default_factory=list)

    @property
    def sweep_supported(self) -> bool:
        """扫描能力:uniform(任意维)与 **2D NUS**;3D NUS 未开放。"""
        if str(self.sampling) == "nus" and int(self.ndim) != 2:
            return False
        return True

    @property
    def peak_table_parabolic_path(self) -> str:
        return str((self.peak_tables.get("parabolic") or {}).get("path", ""))

    @property
    def peak_table_gaussian_path(self) -> str:
        return str((self.peak_tables.get("gaussian") or {}).get("path", ""))

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_key": self.dataset_key,
            "exp_id": self.exp_id,
            "data_id": self.data_id,
            "condition": self.condition,
            "run_id": self.run_id,
            "phase_route": self.phase_route,
            "ndim": int(self.ndim),
            "sampling": self.sampling,
            "sampling_schedule": self.sampling_schedule,
            "sampling_evidence": self.sampling_evidence,
            "spectrum_path": self.spectrum_path,
            "frozen_spectrum": self.frozen_spectrum,
            "script_path": self.script_path,
            "script_sha256": self.script_sha256,
            "spectrum_sha256": self.spectrum_sha256,
            "params": self.params,
            "sweep_params": self.sweep_params,
            "work_dir": self.work_dir,
            "direct_phase": self.direct_phase,
            "peak_table_path": self.peak_table_path,
            "peak_table_sha256": self.peak_table_sha256,
            "peak_count": int(self.peak_count),
            "peak_source": self.peak_source,
            "peak_params": self.peak_params,
            "peak_created_at": self.peak_created_at,
            "peak_tables": self.peak_tables,
            "peak_localization": self.peak_localization,
            "created_at": self.created_at,
            "software_version": self.software_version,
            "tool_versions": self.tool_versions,
            "logs_tail": self.logs_tail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReferenceSpectrum:
        return cls(
            dataset_key=str(data.get("dataset_key", "")),
            exp_id=str(data.get("exp_id", "")),
            data_id=str(data.get("data_id", "")),
            condition=str(data.get("condition", "")),
            run_id=str(data.get("run_id", "")),
            phase_route=str(data.get("phase_route", "")),
            ndim=int(data.get("ndim", 2) or 2),
            sampling=str(data.get("sampling", "uniform")),
            sampling_schedule=str(data.get("sampling_schedule", "")),
            sampling_evidence=[
                str(x) for x in (data.get("sampling_evidence") or [])
            ],
            spectrum_path=str(data.get("spectrum_path", "")),
            frozen_spectrum=str(data.get("frozen_spectrum", "")),
            script_path=str(data.get("script_path", "")),
            script_sha256=str(data.get("script_sha256", "")),
            spectrum_sha256=str(data.get("spectrum_sha256", "")),
            params=dict(data.get("params") or {}),
            sweep_params=dict(data.get("sweep_params") or {}),
            work_dir=str(data.get("work_dir", "")),
            direct_phase={
                str(k): [float(v[0]), float(v[1])]
                for k, v in (data.get("direct_phase") or {}).items()
                if isinstance(v, (list, tuple)) and len(v) >= 2
            },
            peak_table_path=str(data.get("peak_table_path", "")),
            peak_table_sha256=str(data.get("peak_table_sha256", "")),
            peak_count=int(data.get("peak_count", 0) or 0),
            peak_source=str(data.get("peak_source", "")),
            peak_params=dict(data.get("peak_params") or {}),
            peak_created_at=str(data.get("peak_created_at", "")),
            peak_tables={
                str(k): dict(v)
                for k, v in (data.get("peak_tables") or {}).items()
                if isinstance(v, dict)
            },
            peak_localization=dict(data.get("peak_localization") or {}),
            created_at=str(data.get("created_at", "")),
            software_version=str(data.get("software_version", "")),
            tool_versions={
                str(k): str(v) for k, v in (data.get("tool_versions") or {}).items()
            },
            logs_tail=[str(x) for x in (data.get("logs_tail") or [])],
        )

    def direct_phase_override(self) -> dict[str, tuple[float, float]] | None:
        """参考相位 → 后端 ``direct_phase_override`` 参数(锁定相位用)。

        后端该参数按轴生效(见 ``script_generator._stage_lines`` 的 phase
        分支),因此这里返回参考运行记录的全部轴 PS:直接维相位搜索被跳过,
        间接维也沿用参考优化结果——否则候选谱与参考谱相位不同,峰位差里
        会混进相位差异。
        """
        if not self.direct_phase:
            return None
        return {
            axis: (float(values[0]), float(values[1]))
            for axis, values in self.direct_phase.items()
        }

    def normalized_direct_phase(self) -> dict[str, list[float]]:
        return {
            axis: [float(values[0]), float(values[1])]
            for axis, values in self.direct_phase.items()
        }

    def phase_record(self) -> dict[str, Any]:
        """相位溯源(规范 G1):``phase_mode`` + 实际使用的 ``actual_p0/p1``。

        自动相位识别(``phase_mode="auto"``)的实际结果就是参考运行记录的
        PS 值;workflow 里相位从参考锁定,偏移经 ``phase_delta.*`` 施加。
        """
        record: dict[str, Any] = {}
        for axis, values in self.direct_phase.items():
            record[str(axis)] = {
                "phase_mode": "auto",
                "actual_p0": float(values[0]),
                "actual_p1": float(values[1]),
                "source": (
                    f"reference_run:{self.run_id}" if self.run_id else "reference_run"
                ),
            }
        return record


@dataclass(frozen=True)
class ReferenceHandle:
    """组合模式**显式**指定的参考(研究根 + 条件,或直接给 reference.json)。

    写法:

    - ``"~/studies/s1"``                → 该研究主条件的参考;
    - ``"~/studies/s1#B"``              → 该研究条件 B 的参考;
    - ``".../study/reference/<key>/reference.json"`` → 直接给参考文件
      (研究根由路径反推)。
    """

    root: str
    condition: str = ""
    reference_json: str = ""

    def describe(self) -> str:
        if self.reference_json:
            return self.reference_json
        if self.condition:
            return f"{self.root}#{self.condition}"
        return self.root


def parse_reference_spec(spec: Any) -> ReferenceHandle:
    """外部参考写法 → :class:`ReferenceHandle`(空/非法直接报错)。"""
    if isinstance(spec, ReferenceHandle):
        return spec
    text = str(spec or "").strip()
    if not text:
        raise ReferenceError(
            "组合模式必须显式指定参考:传研究根(<root> 或 <root>#<条件>)或 "
            "reference.json 路径;参考由参考模式生成"
            "(run_reference_study / CLI reference + peaks)"
        )
    path = Path(text).expanduser()
    if path.is_file() and path.name == REFERENCE_FILENAME:
        # <root>/study/reference/<key>/reference.json → 反推研究根
        root = path.parent.parent.parent.parent
        return ReferenceHandle(
            root=str(root), condition="", reference_json=str(path)
        )
    condition = ""
    if "#" in text:
        text, _, condition = text.partition("#")
        path = Path(text).expanduser()
    if not str(path).strip():
        raise ReferenceError(f"组合模式指定的参考写法不对: {spec!r}")
    return ReferenceHandle(root=str(path), condition=condition.strip())


def resolve_reference(
    spec: Any, *, backend: Any | None = None
) -> tuple[StudySession, DatasetRef, ReferenceSpectrum]:
    """外部参考写法 → (会话, 条件数据集, 参考谱);参考未建好时明确报错。"""
    handle = parse_reference_spec(spec)
    root = Path(handle.root).expanduser().resolve()
    if not (root / "project.json").is_file():
        raise ReferenceError(
            f"组合模式指定的参考不可用:{handle.describe()} "
            "不是 NMRForge 研究根(缺 project.json)"
        )
    session = open_study(root, backend=backend)
    if session.dataset is None:
        raise ReferenceError(f"研究 {root} 里还没有数据:先跑参考模式导入条件数据")
    target: DatasetRef | None = None
    if handle.reference_json:
        try:
            payload = json.loads(
                Path(handle.reference_json).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise ReferenceError(f"参考文件读不动: {handle.reference_json} ({exc})")
        key = str(payload.get("dataset_key", ""))
        target = next((ref for ref in session.datasets if ref.key == key), None)
        if target is None:
            raise ReferenceError(
                f"参考文件 {handle.reference_json} 指向的数据集 {key!r} "
                f"不在研究 {root} 里"
            )
    elif handle.condition:
        target = session.dataset_by_condition(handle.condition)
        if target is None:
            raise ReferenceError(
                f"研究 {root} 里没有条件 {handle.condition!r}:"
                f"可用条件 {session.conditions}"
            )
    else:
        target = session.dataset
    reference = load_reference(session, target)
    if reference is None:
        raise ReferenceError(
            f"组合模式必须显式指定已建好的参考:{root}(条件 {target.condition})"
            "还没有参考谱;先跑参考模式(run_reference_study / CLI reference + peaks)"
        )
    if not (reference.peak_table_path and Path(reference.peak_table_path).is_file()):
        raise ReferenceError(
            f"参考峰表缺失:{root}(条件 {target.condition});"
            "先在参考模式里选峰(ensure_reference_peaks / CLI peaks)"
        )
    return session, target, reference


def reference_runtime_decisions(params: Mapping[str, Any]) -> dict[str, Any]:
    """参考有效参数 → 组合必须沿用的运行期行为决定(顶层键形式)。

    这些决定来自参考运行的自动诊断/路由,只写在 ``diagnostics`` 里;
    不提升就会让组合运行退回后端默认值,于是「没指定的参数」也变了。
    """
    diagnostics = dict((params or {}).get("diagnostics") or {})
    return {
        key: bool(diagnostics[source])
        for key, source in _RUNTIME_DECISION_KEYS
        if source in diagnostics
    }


def sanitize_sweep_params(params: dict[str, Any]) -> dict[str, Any]:
    """有效参数 → 可再次传给后端 process() 的扫描基础参数。

    剔除运行期派生/仅 GUI 的键,但把 ``reference_runtime_decisions()``
    里的行为决定提升为顶层键(顶层已显式给值时不覆盖)。
    """
    cleaned = {
        str(key): value
        for key, value in dict(params or {}).items()
        if key not in _NON_SWEEP_KEYS
    }
    for key, value in reference_runtime_decisions(params).items():
        cleaned.setdefault(key, value)
    return cleaned


def normalize_direct_phase(raw: object) -> dict[str, list[float]]:
    """参考运行记录的 direct_phase → {"F2": [p0, p1]} 规范形式。"""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[float]] = {}
    for axis, values in raw.items():
        if not isinstance(values, (list, tuple)) or len(values) < 2:
            continue
        try:
            out[str(axis)] = [float(values[0]), float(values[1])]
        except (TypeError, ValueError):
            continue
    return out


def _as_phase_pair(raw: object) -> list[float] | None:
    """``[p0, p1]`` / ``(p0, p1)`` → 浮点对;形状不对返回 None。"""
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        return [float(raw[0]), float(raw[1])]
    except (TypeError, ValueError):
        return None


def reference_phase(
    effective: dict[str, Any], *, ndim: int = 2
) -> dict[str, list[float]]:
    """参考运行的有效参数 → 锁定用的各轴 PS(p0, p1)。

    - ``phases``:各轴 PS 字典(统一路线;间接维相位在这里);
    - ``direct_phase``:字典形式(统一路线直接维)或**扁平** ``[p0, p1]``
      (NUS 重构路线;直接维 = ``F{ndim}``)。

    两者合并,直接维以 ``direct_phase`` 为准。
    """
    locked: dict[str, list[float]] = {}
    phases = normalize_direct_phase(effective.get("phases"))
    if phases:
        locked.update(phases)
    direct = normalize_direct_phase(effective.get("direct_phase"))
    if not direct:
        pair = _as_phase_pair(effective.get("direct_phase"))
        if pair is not None:
            direct = {f"F{int(ndim)}": pair}
    locked.update(direct)
    return locked


def dataset_for_reference(
    session: StudySession, reference: ReferenceSpectrum
) -> DatasetRef | None:
    """参考 → 会话里的条件数据集(找不到返回 None)。"""
    for ref in session.datasets:
        if ref.key == reference.dataset_key:
            return ref
    return session.dataset


def reference_work_dir(session: Any, dataset: Any) -> Path:
    """参考/组合共用的**条件级**工作目录 ``study/work/<exp>_<data>/``。

    参考阶段的 fid 转换、自动优化脚本,以及随后每个 workflow 的候选脚本都
    落在这里:组合运行复用参考转换好的 fid(不再重复转换),候选脚本与参考
    脚本同目录;多条件之间也不会因为 ``<data_id>.fid`` 同名而互相覆盖。
    """
    key = f"{getattr(dataset, 'exp_id', '')}_{getattr(dataset, 'data_id', '')}"
    work = session.work_dir / str(key).strip("_")
    work.mkdir(parents=True, exist_ok=True)
    return work


def _find_reference_script(work: Path, data_id: str) -> Path:
    """定位本次参考运行实际执行的脚本。

    后端按 ``script_name`` 或 ``<dataset_id>_process.com`` 落盘(见
    ``backend/nmrpipe_backend._process``);这里按优先级查找,兜底取最新的
    非 fid/候选脚本。找不到即报错,不猜。
    """
    preferred = [
        work / f"{data_id}_process.com",
        work / f"{data_id}_nus.com",
        work / "process.com",
        work / "nus.com",
    ]
    for candidate in preferred:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    candidates = [
        path
        for path in work.glob("*.com")
        if path.is_file()
        and path.name != "fid.com"
        and "_nus_rank" not in path.name
        and not path.name.startswith("preview")
    ]
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime_ns)
    raise ReferenceError(f"参考运行没有留下可用的处理脚本: {work}")


def save_reference(session: StudySession, reference: ReferenceSpectrum) -> Path:
    """把参考状态写到 ``study/reference/<key>/reference.json``。"""
    target = session.reference_dir_for(dataset_for_reference(session, reference))
    state_file = target / REFERENCE_FILENAME
    state_file.write_text(
        json.dumps(reference.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return state_file


def build_reference(
    session: StudySession,
    dataset: DatasetRef | None = None,
    *,
    params: dict[str, Any] | None = None,
    phase_route: str | None = None,
    progress: Callable[[str], None] | None = None,
    force: bool = False,
) -> ReferenceSpectrum:
    """跑一遍自动优化,冻结参考谱与参考脚本(幂等:已有则直接返回)。

    ``params`` 只用于覆盖自动流程的输入(例如 ``ext_lo``);``phase_route``
    缺省走数据类型的默认路线(uniform 通常 ``unified``,自动相位)。
    自动相位识别的**实际结果**(各轴 PS)记进 ``direct_phase``,供 workflow
    锁定相位并留档 ``actual_p0/actual_p1``。
    """
    from workflow.stepwise import generate_fid, generate_spectrum, read_experiment

    target_ref = dataset or session.dataset
    if target_ref is None:
        raise ReferenceError("研究里还没有数据集,先调用 add_dataset()")
    target_dir = session.reference_dir_for(target_ref)
    state_file = target_dir / REFERENCE_FILENAME
    if state_file.is_file() and not force:
        existing = load_reference(session, target_ref)
        if existing is not None:
            return existing

    logs: list[str] = []

    def _log(message: str) -> None:
        logs.append(str(message))
        if progress is not None:
            progress(str(message))

    manager = session.manager
    exp_id, data_id = target_ref.exp_id, target_ref.data_id
    run_params = dict(params or {})
    if phase_route:
        run_params["phase_route"] = phase_route

    # 条件级工作目录:参考与它的全部 workflow 共用(脚本/fid 同源)
    work = reference_work_dir(session, target_ref)
    _log(f"参考谱[{target_ref.condition}]:生成 FID(转换 Bruker 原始数据)")
    generate_fid(
        manager, exp_id, data_id, session.backend,
        work_dir=work, progress=_log,
    )
    _log(f"参考谱[{target_ref.condition}]:生成谱图(自动优化)")
    spectrum_path = generate_spectrum(
        manager, exp_id, data_id, session.backend,
        params=run_params, work_dir=work, progress=_log,
    )
    run = manager.last_run_for_data(exp_id, data_id, STEP_RUN_REFS["spectrum"])
    if run is None or run.status != "success":
        raise ReferenceError("参考谱运行没有成功的 WorkflowRun 记录")
    effective = dict(run.params or {})
    script = _find_reference_script(work, data_id)

    frozen_spectrum = target_dir / f"reference{Path(spectrum_path).suffix}"
    frozen_script = target_dir / "process.com"
    shutil.copy2(spectrum_path, frozen_spectrum)
    frozen_script.write_text(
        script.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )

    experiment = read_experiment(manager, exp_id, data_id)
    # 有效采样:采样检测已把「标注 NUS 但实际满采样」降级为 uniform
    # (满采样走 uniform 常规 FT,不跑 SMILE;证据一并留档)。
    sampling = experiment.sampling
    reference = ReferenceSpectrum(
        dataset_key=target_ref.key,
        exp_id=exp_id,
        data_id=data_id,
        condition=target_ref.condition,
        run_id=run.run_id,
        phase_route=str(effective.get("phase_route", "") or ""),
        ndim=int(experiment.ndim),
        sampling=str(sampling.mode),
        sampling_schedule=str(sampling.schedule_type or ""),
        sampling_evidence=[str(item) for item in (sampling.evidence or [])],
        spectrum_path=str(spectrum_path),
        frozen_spectrum=str(frozen_spectrum),
        script_path=str(frozen_script),
        script_sha256=sha256_file(frozen_script),
        spectrum_sha256=sha256_file(frozen_spectrum),
        params=effective,
        sweep_params=sanitize_sweep_params(effective),
        work_dir=str(work),
        direct_phase=reference_phase(effective, ndim=int(experiment.ndim)),
        peak_table_path="",
        software_version=software_version(),
        tool_versions=tool_versions(),
        logs_tail=logs[-40:],
    )
    save_reference(session, reference)
    session.save_state(reference=reference.to_dict())
    # 项目状态必须落盘:参考构建登记了 fid/活动谱(与 WorkflowRun),
    # 而 CLI 的 reference/workflows 是独立进程——不落盘时下一步读不到。
    session.manager.save()
    return reference


def load_reference(
    session: StudySession, dataset: DatasetRef | None = None
) -> ReferenceSpectrum | None:
    """读取某条件的参考谱(缺失返回 None;产物缺失报错)。"""
    target_ref = dataset or session.dataset
    if target_ref is None:
        return None
    state_file = session.reference_dir_for(target_ref) / REFERENCE_FILENAME
    if not state_file.is_file():
        return None
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReferenceError(f"参考谱状态文件损坏: {state_file} ({exc})") from exc
    reference = ReferenceSpectrum.from_dict(raw)
    if not reference.condition:
        reference.condition = target_ref.condition
    for path in (reference.frozen_spectrum, reference.script_path):
        if not path or not Path(path).is_file():
            raise ReferenceError(f"参考谱产物缺失,请重建(force=True): {path}")
    return reference


def load_references(session: StudySession) -> dict[str, ReferenceSpectrum]:
    """读取全部条件的参考(尚未建参考的条件不出现在结果里)。"""
    out: dict[str, ReferenceSpectrum] = {}
    for ref in session.datasets:
        reference = load_reference(session, ref)
        if reference is not None:
            out[ref.key] = reference
    return out


def set_reference_peaks(
    session: StudySession,
    peak_table: Path | str,
    reference: ReferenceSpectrum | None = None,
    *,
    source: str = "external",
    params: dict[str, Any] | None = None,
) -> ReferenceSpectrum:
    """登记参考身份峰表(记录路径、SHA-256、峰数、来源与选峰参数)。

    ``source``:``auto`` = NMRForge 在参考谱上自动选峰;``external`` = 外部峰表;
    ``shared:<条件>`` = 沿用主条件峰身份(多条件研究)。三者都冻结进
    ``reference.json``,记录里只认这份快照。
    """
    ref = reference or load_reference(session)
    if ref is None:
        raise ReferenceError("还没有参考谱,先调用 build_reference()")
    path = Path(peak_table)
    if not path.is_file():
        raise ReferenceError(f"峰表不存在: {path}")
    ref.peak_table_path = str(path)
    ref.peak_table_sha256 = sha256_file(path)
    ref.peak_count = _count_peaks(path)
    ref.peak_source = str(source or "")
    ref.peak_params = dict(params or {})
    ref.peak_created_at = now_iso()
    save_reference(session, ref)
    return ref


def _count_peaks(path: Path) -> int:
    """峰表行数(读不动按 0,不阻断流程)。"""
    try:
        return len(read_reference_peaks(path))
    except Exception:  # noqa: BLE001 - 记录用途
        return 0


def build_reference_peak_tables(
    session: StudySession,
    reference: ReferenceSpectrum,
    *,
    window_pts: int | None = None,
    window_ppm: float | None = None,
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
) -> ReferenceSpectrum:
    """在同一条参考谱上写两张参考峰表(parabolic + gaussian)。

    两张表行集完全一致(同一条 ``reference.list``、同一 ``reference_peak_id``),
    列结构由 ``peak_tables.PEAK_TABLE_COLUMNS`` 保证一致;非 2D 数据的高斯表
    写 ``fallback=true`` / ``fallback_reason=gaussian_unsupported_ndim``
    (位置回退抛物线),不静默跳过。
    """
    dataset = dataset_for_reference(session, reference)
    condition = dataset.condition if dataset is not None else reference.condition
    dataset_label = dataset.key if dataset is not None else reference.dataset_key
    spectrum_path = Path(reference.frozen_spectrum)
    rows = read_reference_peaks(reference.peak_table_path)
    axes = read_spectrum_axes(spectrum_path)
    parabolic = measure_peak_positions(
        spectrum_path,
        rows,
        axes=axes,
        window_pts=window_pts,
        window_ppm=window_ppm,
        refine="parabolic",
    )
    parabolic_rows = peak_table_rows(
        parabolic,
        workflow_id=REFERENCE_WORKFLOW_ID,
        condition=condition,
        dataset=dataset_label,
        method="parabolic",
    )
    if int(reference.ndim) == 2 and int(axes.ndim) == 2:
        gaussian = measure_peak_positions(
            spectrum_path,
            rows,
            axes=axes,
            window_pts=window_pts,
            window_ppm=window_ppm,
            refine="gaussian",
            roi_f1_ppm=roi_f1_ppm,
            roi_f2_ppm=roi_f2_ppm,
        )
        gaussian_rows = peak_table_rows(
            gaussian,
            workflow_id=REFERENCE_WORKFLOW_ID,
            condition=condition,
            dataset=dataset_label,
            method="gaussian",
        )
    else:
        gaussian_rows = gaussian_fallback_rows(
            parabolic,
            workflow_id=REFERENCE_WORKFLOW_ID,
            condition=condition,
            dataset=dataset_label,
            reason=GAUSSIAN_UNSUPPORTED_NDIM_REASON,
        )
    target_dir = session.reference_dir_for(dataset)
    written: dict[str, Path] = {}
    for method, table_rows in (
        ("parabolic", parabolic_rows),
        ("gaussian", gaussian_rows),
    ):
        written[method] = write_peak_table(
            target_dir / REFERENCE_TABLE_FILENAMES[method], table_rows
        )
    reference.peak_tables = {
        method: peak_table_digest(path) for method, path in written.items()
    }
    reference.peak_localization = {
        "parabolic": _localization_summary(parabolic_rows),
        "gaussian": _localization_summary(gaussian_rows),
        "window_by_axis": {
            str(axis): dict(spec)
            for axis, spec in window_points_by_axis(
                axes, window_pts=window_pts, window_ppm=window_ppm
            ).items()
        },
        "window_ppm": window_ppm,
        "window_pts": window_pts,
    }
    save_reference(session, reference)
    session.save_state(reference=reference.to_dict())
    return reference


def _localization_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """峰表行 → 定位 QC 计数(detected / 回退 / 撞边界)。"""
    total = len(rows)
    detected = sum(1 for row in rows if row.get("detected"))
    fallback = sum(1 for row in rows if row.get("fallback"))
    reasons: dict[str, int] = {}
    for row in rows:
        if not row.get("fallback"):
            continue
        reason = str(row.get("fallback_reason", "") or "")
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "n_peaks": int(total),
        "n_detected": int(detected),
        "n_missing": int(total - detected),
        "n_fallback": int(fallback),
        "fallback_reasons": reasons,
        "n_boundary_hit": sum(1 for row in rows if row.get("boundary_hit")),
    }


def ensure_reference_peaks(
    session: StudySession,
    reference: ReferenceSpectrum | None = None,
    *,
    sigma_multiplier: float | None = None,
    max_peaks: int = 0,
    force: bool = False,
    localization_method: str = "parabolic",
    gaussian_roi_f1_ppm: float | None = None,
    gaussian_roi_f2_ppm: float | None = None,
) -> ReferenceSpectrum:
    """保证参考峰身份表与两张参考峰表都存在(默认由软件自动选峰)。

    - 主条件:调用 ``pick_reference_peaks()`` 选峰并冻结为 ``reference.list``;
    - 其他条件:复制主条件的身份表(峰身份共享),再在本条件参考谱上测两张表;
    - 已有身份表 + 两张表且文件在 → 直接复用(除非 ``force``);
    - **选峰阈值在生成参考时选择,随后锁定**:``sigma_multiplier``(σ 倍数,
      缺省 35σ)可在还没有参考峰表时(即生成参考时)由外部指定;参考一旦定了,
      再给**不同**阈值会直接报 ``ReferenceError``——参数扰动阶段所有 workflow
      只能沿用参考的阈值。改阈值属于重建参考:显式 ``force=True`` 或删掉该条件
      的 ``study/reference/<key>/`` 后重跑;
    - 实际用量写进 ``peak_params``:``sigma_multiplier``(参考选定值)/
      ``previous_sigma_multiplier``(force 重建时的上一版)/
      ``detection.sigma_multiplier`` / ``detection.threshold_source``;
    - ``localization_method`` 只决定**参考峰位**的取法(默认抛物线,与既有
      行为一致);两张参考峰表始终同时生成(2026-09-13 规范 B2)。
    """
    ref = reference or load_reference(session)
    if ref is None:
        raise ReferenceError("还没有参考谱,先调用 build_reference()")
    requested_sigma = (
        float(sigma_multiplier)
        if sigma_multiplier and float(sigma_multiplier) > 0
        else None
    )
    stored_sigma = ref.peak_params.get("sigma_multiplier")
    stored_sigma_value = (
        float(stored_sigma) if stored_sigma not in (None, "") else None
    )
    stored_detection = dict(ref.peak_params.get("detection") or {})
    # 参考峰表的**有效阈值**:没显式记过就是选峰默认值(35σ);用来判断
    # 「参考已冻结」时外部给的阈值是否与参考一致。
    effective_stored_sigma = stored_detection.get("sigma_multiplier")
    if effective_stored_sigma is None:
        effective_stored_sigma = stored_sigma_value
    else:
        effective_stored_sigma = float(effective_stored_sigma)
    reference_peaks_frozen = bool(
        ref.peak_table_path
        and Path(ref.peak_table_path).is_file()
        and ref.peak_tables
    )
    # 2026-09-14(用户):选峰阈值**只在生成参考时选择**;参考一旦定了,后续所有
    # 参数扰动必须沿用参考的阈值 → 显式给不同阈值直接报错,不悄悄重选峰。
    # 要换阈值属于「重建参考」,须显式 force=True(或删掉该条件的 reference/)。
    if (
        requested_sigma is not None
        and effective_stored_sigma is not None
        and requested_sigma != effective_stored_sigma
        and reference_peaks_frozen
        and not force
    ):
        raise ReferenceError(
            f"选峰阈值已锁定在参考峰表({effective_stored_sigma:g}σ;"
            "参考一旦确定,后续所有参数扰动必须使用与参考一致的选峰阈值):"
            f"收到 {requested_sigma:g}σ。要改阈值属于重建参考,请显式 "
            "force=True,或删除该条件的 study/reference/<key>/ 后重跑参考。"
        )
    if (
        not force
        and ref.peak_table_path
        and Path(ref.peak_table_path).is_file()
        and ref.peak_tables
        and all(
            Path(str((ref.peak_tables.get(method) or {}).get("path", ""))).is_file()
            for method in ("parabolic", "gaussian")
        )
    ):
        return ref
    dataset = dataset_for_reference(session, ref)
    primary = load_reference(session, session.dataset) if session.dataset else None
    is_primary = (
        primary is None
        or dataset is None
        or session.dataset is None
        or dataset.key == session.dataset.key
        or primary.dataset_key == ref.dataset_key
    )
    target = session.reference_dir_for(dataset) / REFERENCE_PEAK_LIST_FILENAME
    if is_primary:
        details: dict[str, Any] = {}
        pick_reference_peaks(
            session,
            sigma_multiplier=sigma_multiplier,
            out_path=target,
            details=details,
            localization_method=localization_method,
            gaussian_roi_f1_ppm=gaussian_roi_f1_ppm,
            gaussian_roi_f2_ppm=gaussian_roi_f2_ppm,
            dataset=dataset,
        )
        if max_peaks and max_peaks > 0:
            _keep_top_peaks(target, int(max_peaks))
        ref = set_reference_peaks(
            session,
            target,
            ref,
            source="auto",
            params={
                "sigma_multiplier": sigma_multiplier,
                "previous_sigma_multiplier": stored_sigma_value,
                "max_peaks": int(max_peaks),
                # 选峰边距的物理宽度↔点数换算(用户方案 A):跨分辨率复算用
                "detection": details.get("detection") or {},
                "localization_method": str(localization_method),
                "localization": details.get("localization") or {},
            },
        )
    else:
        assert primary is not None  # is_primary=False 时主参考必然存在
        source_list = Path(primary.peak_table_path)
        if not source_list.is_file():
            raise ReferenceError(
                "非主条件的参考峰身份需要主条件先选峰:请先对主条件调用 "
                "ensure_reference_peaks()"
            )
        target.write_text(
            source_list.read_text(encoding="utf-8"), encoding="utf-8"
        )
        ref = set_reference_peaks(
            session,
            target,
            ref,
            source=f"shared:{primary.condition}",
            params={
                "sigma_multiplier": sigma_multiplier,
                "max_peaks": int(max_peaks),
                "localization_method": str(localization_method),
                "shared_from": primary.dataset_key,
                "shared_peak_count": int(primary.peak_count),
                "shared_peak_table_sha256": primary.peak_table_sha256,
            },
        )
    # 选峰/复制都会登记运行记录:落盘保证跨进程可见
    session.manager.save()
    return build_reference_peak_tables(
        session,
        ref,
        roi_f1_ppm=gaussian_roi_f1_ppm,
        roi_f2_ppm=gaussian_roi_f2_ppm,
    )


def _keep_top_peaks(path: Path, keep: int) -> None:
    """按 Intensity 保留前 keep 个峰(重写同一路径,保持 Poky .list 格式)。"""
    from core.peaks.peak_table import export_peaks_poky, load_peaks

    rows = load_peaks(path)
    if len(rows) <= keep:
        return
    rows.sort(
        key=lambda row: abs(float(row.get("Intensity") or 0.0)), reverse=True
    )
    export_peaks_poky(path, rows[:keep])
    # 被裁的是冻结身份表(通常没有附件);若该路径恰好有定位附件,一并按
    # 行序截断,避免附件与峰表不一致(防御性,正常流程是 no-op)。
    from core.peaks.localize import trim_localization_records

    trim_localization_records(path, keep)


__all__ = [
    "GAUSSIAN_UNSUPPORTED_NDIM_REASON",
    "ReferenceHandle",
    "REFERENCE_FILENAME",
    "REFERENCE_PEAK_LIST_FILENAME",
    "REFERENCE_TABLE_FILENAMES",
    "ReferenceSpectrum",
    "build_reference",
    "build_reference_peak_tables",
    "dataset_for_reference",
    "ensure_reference_peaks",
    "load_reference",
    "load_references",
    "normalize_direct_phase",
    "parse_reference_spec",
    "reference_phase",
    "reference_runtime_decisions",
    "reference_work_dir",
    "resolve_reference",
    "sanitize_sweep_params",
    "save_reference",
    "set_reference_peaks",
]
