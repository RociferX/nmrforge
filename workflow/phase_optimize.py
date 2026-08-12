"""自动相位优化:单次后端运行 + 内存内候选评分。

背景(docs/PROJECT_STATUS 待办「自动相位性能重构」):早期设想是逐相位候选
重跑完整后端(重构/处理)再评分;本模块按 D005「终谱实型不做事后调相」与
「只重构一次」约束改为:

- 昂贵的重构/处理只运行一次(``backend.process`` / ``reconstruct_nus``);
- 相位候选(p0/p1 网格)在内存内对复型中间数据评分,绝不触发后端重跑;
- 直接维:复用转换期 ``phase.json`` 缓存,否则对转换后的 .fid 做内存内
  FT + p1 共识搜索(core.optimization.phase_search.search_phase);
- 间接维(NUS):对复型重构平面 test*.ft1 按轴搜索
  (workflow.recon_phase_search.search_recon_phase,全部 numpy);
- 终谱为实型:按 D005 不做事后调相,仅记录提示。

结果中的 ``backend_runs``(=1,run_with_auto_phase)与 ``candidates_scored``
直接量化「逐候选重跑后端 N 次 → 单次运行 + 内存内 N 次评分」的性能收益。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.data.internal_data_model import Experiment, SamplingMode
from core.data.pipe_io import read_pipe_planes
from core.optimization.phase_search import direct_ft_traces, search_phase
from core.planning.method_selector import select_method
from workflow.recon_phase_search import search_recon_phase

# 直接维 p1 共识搜索的默认候选网格(search_phase 默认值,显式传入保证计数一致)
DIRECT_P0_VALUES = np.arange(-180.0, 181.0, 20.0)  # 19 个
DIRECT_P1_VALUES = np.arange(-180.0, 181.0, 30.0)  # 13 个


@dataclass
class AxisPhaseEstimate:
    """一个轴的相位估计结果。"""

    axis: str
    p0: float
    p1: float
    score: float
    gain: float
    source: str  # direct_fid / direct_fid(cache) / recon_planes
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PhaseOptimizeResult:
    """自动相位优化结果:相位估计 + 性能计数。"""

    phases: list[AxisPhaseEstimate]
    candidates_scored: int
    backend_runs: int
    spectrum_path: str
    work_dir: str
    logs: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [f"{p.axis}={p.p0:g}/{p.p1:g}({p.source})" for p in self.phases]
        head = (
            f"自动相位(内存内): 候选 {self.candidates_scored} 个, "
            f"后端运行 {self.backend_runs} 次"
        )
        return head + (f"; {', '.join(parts)}" if parts else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "phases": [p.to_dict() for p in self.phases],
            "candidates_scored": self.candidates_scored,
            "backend_runs": self.backend_runs,
            "spectrum_path": self.spectrum_path,
            "work_dir": self.work_dir,
            "logs": list(self.logs),
        }


def default_work_dir(experiment: Experiment, backend: Any | None = None) -> Path:
    """后端工作目录:backend.work_dir 优先,否则按 NMRPipeBackend 约定推导。"""
    explicit = getattr(backend, "work_dir", "") if backend is not None else ""
    if explicit:
        return Path(explicit)
    raw = Path(experiment.source_path)
    return raw.parent / f"{experiment.dataset_id}.nmrpipe"


def _direct_fid_path(work_dir: Path, experiment: Experiment) -> Path | None:
    """转换后的复型 .fid 路径(多段实验取 seg_001)。"""
    if experiment.segments:
        candidate = work_dir / "seg_001" / f"{experiment.dataset_id}.fid"
        if candidate.is_file():
            return candidate
    candidate = work_dir / f"{experiment.dataset_id}.fid"
    return candidate if candidate.is_file() else None


def search_direct_phase(
    fid: np.ndarray,
    *,
    zf_size: int | None = None,
    p0_values: np.ndarray | None = None,
    p1_values: np.ndarray | None = None,
) -> AxisPhaseEstimate:
    """在转换后的复型 .fid 上做内存内 p1 共识搜索(不重跑后端)。"""
    arr = np.asarray(fid)
    n_points = arr.shape[-1] if arr.ndim >= 1 else 0
    if n_points < 8:
        return AxisPhaseEstimate(
            axis="",
            p0=0.0,
            p1=0.0,
            score=0.0,
            gain=0.0,
            source="direct_fid",
            note="FID 点数不足,跳过",
        )
    if zf_size is None:
        zf_size = 1
        while zf_size < 2 * n_points:
            zf_size *= 2
    p0_values = p0_values if p0_values is not None else DIRECT_P0_VALUES
    p1_values = p1_values if p1_values is not None else DIRECT_P1_VALUES
    traces = direct_ft_traces(arr, zf_size=zf_size, sp_off=0.45, sp_end=0.95, sp_pow=1)
    p0, p1, score, gain = search_phase(
        traces, p0_values=p0_values, p1_values=p1_values
    )
    return AxisPhaseEstimate(
        axis="", p0=p0, p1=p1, score=score, gain=gain, source="direct_fid"
    )



def estimate_direct_axis(
    experiment: Experiment,
    work_dir: Path,
    *,
    min_gain: float = 0.02,
) -> tuple[AxisPhaseEstimate | None, int]:
    """直接维相位估计:phase.json 缓存优先,否则内存内 FT 搜索。

    返回 (估计, 评估候选数);缓存命中候选数记 0(未重复评估)。
    """
    direct_axis = "F2" if experiment.ndim == 2 else "F3"
    cache = Path(work_dir) / "phase.json"
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            return (
                AxisPhaseEstimate(
                    axis=direct_axis,
                    p0=float(data.get("p0", 0.0)),
                    p1=float(data.get("p1", 0.0)),
                    score=float(data.get("score", 0.0)),
                    gain=float(data.get("gain", 0.0)),
                    source="direct_fid(cache)",
                    note="复用转换期相位搜索缓存,不重复评估候选",
                ),
                0,
            )
        except (OSError, TypeError, ValueError, KeyError):
            pass  # 缓存损坏则重新搜索
    fid_path = _direct_fid_path(Path(work_dir), experiment)
    if fid_path is None:
        return None, 0
    try:
        import nmrglue as ng

        _dic, fid = ng.pipe.read(str(fid_path))
        est = search_direct_phase(fid)
        est.axis = direct_axis
        if est.gain < min_gain:
            est.note = (
                f"相位信息弱(gain={est.gain:.3f} < {min_gain:g}),保持 p0=p1=0"
            )
            est.p0 = 0.0
            est.p1 = 0.0
        n_candidates = int(len(DIRECT_P0_VALUES) * len(DIRECT_P1_VALUES))
        return est, n_candidates
    except Exception as exc:  # noqa: BLE001
        return (
            AxisPhaseEstimate(
                axis=direct_axis,
                p0=0.0,
                p1=0.0,
                score=0.0,
                gain=0.0,
                source="direct_fid",
                note=f"相位搜索失败: {exc}",
            ),
            0,
        )


def _find_planes_dir(work_dir: Path) -> Path | None:
    """定位复型重构平面目录(工作目录或其直接子目录中的 test*.ft1)。"""
    work = Path(work_dir)
    if list(work.glob("test*.ft1")):
        return work
    if work.is_dir():
        for child in sorted(work.iterdir()):
            if child.is_dir() and list(child.glob("test*.ft1")):
                return child
    return None


def estimate_recon_planes(
    planes: np.ndarray,
    *,
    candidates: list[int] | None = None,
) -> list[AxisPhaseEstimate]:
    """复型重构平面上按轴内存内相位搜索(无任何后端调用)。"""
    counted: list[int] = []
    results = search_recon_phase(planes, candidates=counted)
    if candidates is not None:
        candidates.extend(counted)
    estimates: list[AxisPhaseEstimate] = []
    for key in sorted(results):
        value = results[key]
        estimates.append(
            AxisPhaseEstimate(
                axis=key,
                p0=float(value["p0"]),
                p1=float(value["p1"]),
                score=float(value["score"]),
                gain=float(value["gain"]),
                source="recon_planes",
            )
        )
    return estimates


def estimate_auto_phase(
    experiment: Experiment,
    work_dir: Path | str,
    *,
    spectrum_path: Path | str | None = None,
    min_gain: float = 0.02,
) -> PhaseOptimizeResult:
    """基于一次后端运行已产出的中间产物做内存内相位估计(backend_runs=0)。"""
    work = Path(work_dir)
    phases: list[AxisPhaseEstimate] = []
    logs: list[str] = []
    candidates_scored = 0

    direct, n_direct = estimate_direct_axis(experiment, work, min_gain=min_gain)
    candidates_scored += n_direct
    if direct is not None:
        phases.append(direct)
        note = f" ({direct.note})" if direct.note else ""
        logs.append(
            f"直接维({direct.axis}) 相位: p0={direct.p0:g} p1={direct.p1:g} "
            f"score={direct.score:.3f} gain={direct.gain:.3f} "
            f"[{direct.source}]{note}"
        )

    planes_dir = _find_planes_dir(work)
    if planes_dir is not None:
        try:
            planes = read_pipe_planes(planes_dir)
        except Exception as exc:  # noqa: BLE001
            logs.append(f"复型平面读取失败({planes_dir}): {exc}")
        else:
            counted: list[int] = []
            plane_ests = estimate_recon_planes(planes, candidates=counted)
            candidates_scored += sum(counted)
            phases.extend(plane_ests)
            for est in plane_ests:
                logs.append(
                    f"重构平面 {est.axis}: p0={est.p0:g} p1={est.p1:g} "
                    f"score={est.score:.3f} gain={est.gain:.3f} [{est.source}]"
                )
    else:
        logs.append("未发现复型重构平面(test*.ft1),跳过间接维相位估计")

    if spectrum_path is not None:
        logs.append(f"终谱 {Path(spectrum_path).name} 为实型,按 D005 不做事后调相")

    result = PhaseOptimizeResult(
        phases=phases,
        candidates_scored=candidates_scored,
        backend_runs=0,
        spectrum_path=str(spectrum_path or ""),
        work_dir=str(work),
        logs=logs,
    )
    result.logs.insert(
        0,
        f"自动相位估计(内存内): 候选 {candidates_scored} 个,复用已有产物"
        "(后端 0 次)",
    )
    return result


def run_with_auto_phase(
    experiment: Experiment,
    backend: Any,
    params: dict[str, Any] | None = None,
    *,
    work_dir: Path | str | None = None,
    min_gain: float = 0.02,
) -> PhaseOptimizeResult:
    """自动处理一次 + 内存内相位估计:后端(重构/处理)只运行一次。

    uniform → ``backend.process(experiment, select_method(experiment))``;
    NUS → ``backend.reconstruct_nus(experiment, params)``。
    """
    params = dict(params or {})
    if experiment.sampling.mode is SamplingMode.NUS:
        resp = backend.reconstruct_nus(experiment, params)
    else:
        plan = select_method(experiment)
        resp = backend.process(experiment, plan)
    backend_runs = 1
    if not resp.get("success"):
        return PhaseOptimizeResult(
            phases=[],
            candidates_scored=0,
            backend_runs=backend_runs,
            spectrum_path="",
            work_dir="",
            logs=[str(resp.get("message", "后端运行失败"))],
        )
    spectrum = resp.get("spectrum_path") or ""
    work = Path(work_dir) if work_dir else default_work_dir(experiment, backend)
    result = estimate_auto_phase(
        experiment, work, spectrum_path=spectrum, min_gain=min_gain
    )
    result.backend_runs = backend_runs
    result.logs.insert(
        0, f"后端(重构/处理)运行 {backend_runs} 次;相位候选全部内存内评分"
    )
    return result


def format_phase_report(result: PhaseOptimizeResult) -> str:
    """渲染相位估计表格 + 性能计数。"""
    header = f"{'轴':>8} {'p0':>8} {'p1':>8} {'score':>7} {'gain':>7} {'来源':>16}"
    lines = [header, "-" * len(header)]
    for est in result.phases:
        lines.append(
            f"{est.axis:>8} {est.p0:>8.1f} {est.p1:>8.1f} {est.score:>7.3f} "
            f"{est.gain:>7.3f} {est.source:>16}"
        )
    lines.append(
        f"候选评分: {result.candidates_scored} 个(内存内),"
        f"后端运行: {result.backend_runs} 次"
    )
    return "\n".join(lines)


def save_report(result: PhaseOptimizeResult, path: Path | str) -> Path:
    """保存相位优化报告(JSON)。"""
    out = Path(path)
    out.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out


__all__ = [
    "AxisPhaseEstimate",
    "DIRECT_P0_VALUES",
    "DIRECT_P1_VALUES",
    "PhaseOptimizeResult",
    "default_work_dir",
    "estimate_auto_phase",
    "estimate_direct_axis",
    "estimate_recon_planes",
    "format_phase_report",
    "run_with_auto_phase",
    "save_report",
    "search_direct_phase",
]
