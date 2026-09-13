"""参考谱与参考脚本:用 NMRForge 的自动优化跑一次,并冻结下来当基准。

参考谱是整项研究的零点:后续所有参数组合都相对于它测量峰位移。因此这里做三件事:

1. 走完整自动链(``generate_fid`` → ``generate_spectrum``,含统一相位优化),
   拿到「软件认为最好」的谱与**它实际执行的那个脚本**;
2. 把谱、脚本、有效参数、软件/工具版本一起冻结到 ``study/reference/<key>/``,
   并记录脚本与谱的 SHA-256(下游报告可直接引用);
3. 抽出「扫描基础参数」——把运行期派生的键(诊断/预览/投影等)剔除,只留下
   可再次喂给后端 ``process()`` 的处理参数,保证扫描以参考为起点、只改被扫的轴。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.project.manager import sha256_file
from core.project.run_refs import STEP_RUN_REFS
from core.version import software_version, tool_versions
from nmrforge_api.errors import ReferenceError
from nmrforge_api.peaks import pick_reference_peaks
from nmrforge_api.session import StudySession, now_iso

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

REFERENCE_FILENAME = "reference.json"


@dataclass
class ReferenceSpectrum:
    """冻结的参考谱 + 参考脚本 + 有效参数。"""

    dataset_key: str
    exp_id: str
    data_id: str
    run_id: str = ""
    phase_route: str = ""
    ndim: int = 2
    sampling: str = "uniform"
    spectrum_path: str = ""            # 项目 spectra/ 下的活动谱
    frozen_spectrum: str = ""          # 研究目录内的副本
    script_path: str = ""              # 研究目录内的参考脚本副本
    script_sha256: str = ""
    spectrum_sha256: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    sweep_params: dict[str, Any] = field(default_factory=dict)
    # 参考运行的各轴 PS(p0,p1):扫描时传给后端 direct_phase_override,
    # 让候选谱与参考谱相位一致(后端该参数按轴生效,名字沿用后端 API)。
    direct_phase: dict[str, list[float]] = field(default_factory=dict)
    peak_table_path: str = ""
    peak_table_sha256: str = ""
    peak_count: int = 0
    peak_source: str = ""          # auto(NMRForge 选峰) | external(外部峰表)
    peak_params: dict[str, Any] = field(default_factory=dict)
    peak_created_at: str = ""
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_key": self.dataset_key,
            "exp_id": self.exp_id,
            "data_id": self.data_id,
            "run_id": self.run_id,
            "phase_route": self.phase_route,
            "ndim": int(self.ndim),
            "sampling": self.sampling,
            "spectrum_path": self.spectrum_path,
            "frozen_spectrum": self.frozen_spectrum,
            "script_path": self.script_path,
            "script_sha256": self.script_sha256,
            "spectrum_sha256": self.spectrum_sha256,
            "params": self.params,
            "sweep_params": self.sweep_params,
            "direct_phase": self.direct_phase,
            "peak_table_path": self.peak_table_path,
            "peak_table_sha256": self.peak_table_sha256,
            "peak_count": int(self.peak_count),
            "peak_source": self.peak_source,
            "peak_params": self.peak_params,
            "peak_created_at": self.peak_created_at,
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
            run_id=str(data.get("run_id", "")),
            phase_route=str(data.get("phase_route", "")),
            ndim=int(data.get("ndim", 2) or 2),
            sampling=str(data.get("sampling", "uniform")),
            spectrum_path=str(data.get("spectrum_path", "")),
            frozen_spectrum=str(data.get("frozen_spectrum", "")),
            script_path=str(data.get("script_path", "")),
            script_sha256=str(data.get("script_sha256", "")),
            spectrum_sha256=str(data.get("spectrum_sha256", "")),
            params=dict(data.get("params") or {}),
            sweep_params=dict(data.get("sweep_params") or {}),
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


def sanitize_sweep_params(params: dict[str, Any]) -> dict[str, Any]:
    """有效参数 → 可再次传给后端 process() 的扫描基础参数。"""
    return {
        str(key): value
        for key, value in dict(params or {}).items()
        if key not in _NON_SWEEP_KEYS
    }


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


def build_reference(
    session: StudySession,
    *,
    params: dict[str, Any] | None = None,
    phase_route: str | None = None,
    progress: Callable[[str], None] | None = None,
    force: bool = False,
) -> ReferenceSpectrum:
    """跑一遍自动优化,冻结参考谱与参考脚本(幂等:已有则直接返回)。

    ``params`` 只用于覆盖自动流程的输入(例如 ``ext_lo``);``phase_route``
    缺省走数据类型的默认路线(uniform 通常 ``unified``,自动相位)。
    """
    from workflow.stepwise import generate_fid, generate_spectrum, read_experiment

    dataset = session.dataset
    if dataset is None:
        raise ReferenceError("研究里还没有数据集,先调用 add_dataset()")
    target_dir = session.reference_dir_for()
    state_file = target_dir / REFERENCE_FILENAME
    if state_file.is_file() and not force:
        return load_reference(session)  # type: ignore[return-value]

    logs: list[str] = []

    def _log(message: str) -> None:
        logs.append(str(message))
        if progress is not None:
            progress(str(message))

    manager = session.manager
    exp_id, data_id = dataset.exp_id, dataset.data_id
    run_params = dict(params or {})
    if phase_route:
        run_params["phase_route"] = phase_route

    _log("参考谱:生成 FID(转换 Bruker 原始数据)")
    generate_fid(
        manager, exp_id, data_id, session.backend,
        work_dir=session.work_dir, progress=_log,
    )
    _log("参考谱:生成谱图(自动优化)")
    spectrum_path = generate_spectrum(
        manager, exp_id, data_id, session.backend,
        params=run_params, work_dir=session.work_dir, progress=_log,
    )
    run = manager.last_run_for_data(exp_id, data_id, STEP_RUN_REFS["spectrum"])
    if run is None or run.status != "success":
        raise ReferenceError("参考谱运行没有成功的 WorkflowRun 记录")
    effective = dict(run.params or {})
    script = _find_reference_script(session.work_dir, data_id)

    frozen_spectrum = target_dir / f"reference{Path(spectrum_path).suffix}"
    frozen_script = target_dir / "process.com"
    shutil.copy2(spectrum_path, frozen_spectrum)
    frozen_script.write_text(
        script.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )

    experiment = read_experiment(manager, exp_id, data_id)
    reference = ReferenceSpectrum(
        dataset_key=dataset.key,
        exp_id=exp_id,
        data_id=data_id,
        run_id=run.run_id,
        phase_route=str(effective.get("phase_route", "") or ""),
        ndim=int(experiment.ndim),
        sampling=str(experiment.sampling.mode),
        spectrum_path=str(spectrum_path),
        frozen_spectrum=str(frozen_spectrum),
        script_path=str(frozen_script),
        script_sha256=sha256_file(frozen_script),
        spectrum_sha256=sha256_file(frozen_spectrum),
        params=effective,
        sweep_params=sanitize_sweep_params(effective),
        direct_phase=reference_phase(effective, ndim=int(experiment.ndim)),
        peak_table_path="",
        software_version=software_version(),
        tool_versions=tool_versions(),
        logs_tail=logs[-40:],
    )
    state_file.write_text(
        json.dumps(reference.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    session.save_state(reference=reference.to_dict())
    # 项目状态必须落盘:参考构建登记了 fid/活动谱(与 WorkflowRun),
    # 而 CLI 的 reference/peaks/sweep 是三个独立进程——不落盘时下一步
    # 读到的 spectrum_path 为空,选峰会报「谱图缺失」。
    session.manager.save()
    return reference


def load_reference(session: StudySession) -> ReferenceSpectrum | None:
    """读取已冻结的参考谱(缺失返回 None;文件缺失报错)。"""
    dataset = session.dataset
    if dataset is None:
        return None
    state_file = session.reference_dir_for() / REFERENCE_FILENAME
    if not state_file.is_file():
        return None
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReferenceError(f"参考谱状态文件损坏: {state_file} ({exc})") from exc
    reference = ReferenceSpectrum.from_dict(raw)
    for path in (reference.frozen_spectrum, reference.script_path):
        if not path or not Path(path).is_file():
            raise ReferenceError(f"参考谱产物缺失,请重建(force=True): {path}")
    return reference


def set_reference_peaks(
    session: StudySession,
    peak_table: Path | str,
    reference: ReferenceSpectrum | None = None,
    *,
    source: str = "external",
    params: dict[str, Any] | None = None,
) -> ReferenceSpectrum:
    """登记参考峰表(记录路径、SHA-256、峰数、来源与选峰参数)。

    ``source``:``auto`` = NMRForge 在参考谱上自动选峰;``external`` = 外部峰表
    (公开库/既有指认)。两者都冻结进 ``reference.json``,记录里只认这份快照。
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
    state_file = session.reference_dir_for() / REFERENCE_FILENAME
    state_file.write_text(
        json.dumps(ref.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ref


def _count_peaks(path: Path) -> int:
    """峰表行数(读不动按 0,不阻断流程)。"""
    try:
        from nmrforge_api.peaks import read_reference_peaks

        return len(read_reference_peaks(path))
    except Exception:  # noqa: BLE001 - 记录用途
        return 0


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
    """保证参考峰表存在:**默认由 NMRForge 在参考谱上自动选峰**。

    - 已有峰表且文件在 → 直接复用(除非 ``force``);
    - 否则调用 ``pick_reference_peaks()`` 选峰并冻结到
      ``study/reference/<key>/reference.list``;
    - ``max_peaks > 0`` 时按强度保留前 N 个峰(用于剔除明显弱峰/噪声峰);
    - ``localization_method``(2026-09-13):``parabolic``(默认)或
      ``gaussian``(2D 高斯拟合,仅 2D),写入 ``peak_params['localization']``。
    """
    ref = reference or load_reference(session)
    if ref is None:
        raise ReferenceError("还没有参考谱,先调用 build_reference()")
    if not force and ref.peak_table_path and Path(ref.peak_table_path).is_file():
        return ref
    target = session.reference_dir_for() / "reference.list"
    details: dict[str, Any] = {}
    pick_reference_peaks(
        session,
        sigma_multiplier=sigma_multiplier,
        out_path=target,
        details=details,
        localization_method=localization_method,
        gaussian_roi_f1_ppm=gaussian_roi_f1_ppm,
        gaussian_roi_f2_ppm=gaussian_roi_f2_ppm,
    )
    if max_peaks and max_peaks > 0:
        _keep_top_peaks(target, int(max_peaks))
    updated = set_reference_peaks(
        session,
        target,
        ref,
        source="auto",
        params={
            "sigma_multiplier": sigma_multiplier,
            "max_peaks": int(max_peaks),
            # 选峰边距的物理宽度↔点数换算(用户方案 A):跨分辨率复算用
            "detection": details.get("detection") or {},
            "localization_method": str(localization_method),
            "localization": details.get("localization") or {},
        },
    )
    # 选峰会登记一条 pick_peaks 运行记录:同样要落盘(跨进程可见)
    session.manager.save()
    return updated


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
    # 被裁的是冻结参考表(通常没有附件);若该路径恰好有定位附件,一并按
    # 行序截断,避免附件与峰表不一致(防御性,正常流程是 no-op)。
    from core.peaks.localize import trim_localization_records

    trim_localization_records(path, keep)


__all__ = [
    "REFERENCE_FILENAME",
    "ReferenceSpectrum",
    "build_reference",
    "ensure_reference_peaks",
    "load_reference",
    "normalize_direct_phase",
    "reference_phase",
    "sanitize_sweep_params",
    "set_reference_peaks",
]
