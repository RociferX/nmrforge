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
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.data.internal_data_model import Experiment, SamplingMode
from core.data.pipe_io import read_pipe_planes
from core.optimization.phase_search import direct_ft_traces, search_phase
from core.planning.method_selector import select_method
from core.qc import spectrum_quality
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


# ------------------------------------------------------------- 等价性验证


@dataclass
class CandidateScore:
    """一个相位候选的评分(暴力参考或内存内代理)。"""

    params: dict[str, float]
    score: float
    components: dict[str, float] = field(default_factory=dict)
    spectrum_path: str = ""
    source: str = ""  # brute_force / in_memory
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EquivalenceReport:
    """内存内筛选 vs 暴力逐候选后端评分的等价性对比。"""

    candidates: list[dict[str, float]]
    brute_force: dict[str, float]
    in_memory: dict[str, float]
    best_brute_force: dict[str, float]
    best_in_memory: dict[str, float]
    top_match: bool
    correlation: float
    passed: bool
    backend_runs: int
    logs: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (
            "等价性验证: 最优候选一致="
            + str(self.top_match)
            + " 排序相关="
            + f"{self.correlation:.2f}"
            + " 通过="
            + str(self.passed)
            + f" 后端运行={self.backend_runs} 次"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": list(self.candidates),
            "brute_force": dict(self.brute_force),
            "in_memory": dict(self.in_memory),
            "best_brute_force": dict(self.best_brute_force),
            "best_in_memory": dict(self.best_in_memory),
            "top_match": self.top_match,
            "correlation": self.correlation,
            "passed": self.passed,
            "backend_runs": self.backend_runs,
            "logs": list(self.logs),
        }


def direct_phase_candidates(
    p1_values: tuple[float, ...] = (-90.0, -60.0, -30.0, 0.0, 30.0, 60.0, 90.0),
    p0: float = 0.0,
) -> list[dict[str, float]]:
    """直接维候选网格(默认 p0=0, p1 每 30° 一档)。"""
    return [{"p0": p0, "p1": p1} for p1 in p1_values]


def _phase_key(params: dict[str, float]) -> str:
    return f"p0={params.get('p0', 0.0):g}/p1={params.get('p1', 0.0):g}"


def _traces_metric(traces: np.ndarray, p0: float, p1: float) -> tuple[float, float]:
    """固定 (p0, p1) 在 FT 迹线上的吸收度/符号。"""
    n = traces.shape[-1]
    k = np.arange(n, dtype=float)
    magnitude = np.abs(traces)
    mask = magnitude >= 0.3 * np.max(magnitude, axis=-1, keepdims=True)
    base = traces * np.exp(1j * np.deg2rad(p1 * k / max(n - 1, 1)))
    rot = base * np.exp(1j * np.deg2rad(p0))
    re = np.real(rot) * mask
    im = np.imag(rot) * mask
    re_abs = np.abs(re)
    absorption = np.sum(re_abs, axis=-1) / (
        np.sum(re_abs + np.abs(im), axis=-1) + 1e-12
    )
    sign = np.sum(re, axis=-1) / (np.sum(re_abs, axis=-1) + 1e-12)
    return float(np.median(absorption)), float(np.median(sign))


def _p1_score(traces: np.ndarray, p1: float) -> tuple[float, float]:
    """给定 p1 的迹线评分:每迹线吸收最优 p0 后取吸收度中位数。

    与 search_phase._evaluate 同族(直接维 p0 不可靠,先被吸收),
    保证内存内代理与真实搜索使用的指标一致。
    """
    n = traces.shape[-1]
    k = np.arange(n, dtype=float)
    magnitude = np.abs(traces)
    mask = magnitude >= 0.3 * np.max(magnitude, axis=-1, keepdims=True)
    base = traces * np.exp(1j * np.deg2rad(p1 * k / max(n - 1, 1)))
    absorptions: list[np.ndarray] = []
    signs: list[np.ndarray] = []
    for p0 in DIRECT_P0_VALUES:
        rot = base * np.exp(1j * np.deg2rad(p0))
        re = np.real(rot) * mask
        im = np.imag(rot) * mask
        re_abs = np.abs(re)
        absorptions.append(
            np.sum(re_abs, axis=-1)
            / (np.sum(re_abs + np.abs(im), axis=-1) + 1e-12)
        )
        signs.append(np.sum(re, axis=-1) / (np.sum(re_abs, axis=-1) + 1e-12))
    abs_arr = np.array(absorptions)
    sign_arr = np.array(signs)
    best_idx = np.argmax(abs_arr, axis=0)
    rows = np.arange(abs_arr.shape[1])
    return (
        float(np.median(abs_arr[best_idx, rows])),
        float(np.median(sign_arr[best_idx, rows])),
    )


def score_in_memory_direct(
    fid: np.ndarray,
    candidates: list[dict[str, float]],
    *,
    zf_size: int | None = None,
) -> dict[str, float]:
    """单次 FT 后对每个候选相位做内存内吸收度评分(不重跑后端)。"""
    arr = np.asarray(fid)
    n_points = arr.shape[-1] if arr.ndim >= 1 else 0
    if n_points < 8:
        return {}
    if zf_size is None:
        zf_size = 1
        while zf_size < 2 * n_points:
            zf_size *= 2
    traces = direct_ft_traces(arr, zf_size=zf_size, sp_off=0.45, sp_end=0.95, sp_pow=1)
    scores: dict[str, float] = {}
    for params in candidates:
        absorption, _sign = _p1_score(
            traces, float(params.get("p1", 0.0))
        )
        scores[_phase_key(params)] = absorption
    return scores


def _default_spectrum_quality(path: str) -> tuple[float, dict[str, float]]:
    """默认终谱评分:nmrglue 读 ft2/ft3 + spectrum_quality.evaluate。"""
    import nmrglue as ng

    _dic, data = ng.pipe.read(path)
    quality = spectrum_quality.evaluate(data)
    return quality.score.overall, asdict(quality.score.components)


def _default_phase_score(path: str) -> tuple[float, dict[str, float]]:
    """相位专用评分(逐维相位优化的默认评估)。

    复用 core.qc.phase_quality(0.2.38 起):吸收度 25% + 连续负面积 40%
    (de Brouwer 2009)+ 正部谱熵 20%(Ernst 1966)+ 负峰计数 15%;
    终谱为实型(D005)时吸收度恒 1,区分主要靠负面积与熵——合成多峰+噪声
    实测 5° 相位误差的评分余量由旧公式 ~0.1 提升到 ~0.5-0.7(约 5 倍)。
    不用综合 QC(SNR/基线/伪影与相位基本无关,加权后稀释相位排名信号)。
    """
    import nmrglue as ng

    from core.qc import phase_quality

    _dic, data = ng.pipe.read(path)
    quality = phase_quality.evaluate(np.asarray(data))
    return quality.score, asdict(quality)


def brute_force_direct_scores(
    experiment: Experiment,
    backend: Any,
    candidates: list[dict[str, float]] | None = None,
    *,
    score_fn: Callable[[str], tuple[float, dict[str, float]]] | None = None,
) -> list[CandidateScore]:
    """暴力参考:逐候选把相位写进脚本重跑后端,用终谱 QC 评分。

    后端运行次数 = 候选数;仅供准确性验证,不进入默认自动路径。
    """
    candidates = candidates if candidates is not None else direct_phase_candidates()
    score_fn = score_fn or _default_spectrum_quality
    direct_axis = "F2" if experiment.ndim == 2 else "F3"
    results: list[CandidateScore] = []
    for params in candidates:
        p0 = float(params.get("p0", 0.0))
        p1 = float(params.get("p1", 0.0))
        if experiment.sampling.mode is SamplingMode.NUS:
            resp = backend.reconstruct_nus(
                experiment, {"direct_phase_override": (p0, p1)}
            )
        else:
            override = {direct_axis: (p0, p1)}
            resp = backend.process(
                experiment, select_method(experiment), direct_phase_override=override
            )
        if not resp.get("success"):
            results.append(
                CandidateScore(
                    params=dict(params),
                    score=float("-inf"),
                    source="brute_force",
                    message=str(resp.get("message", "后端运行失败")),
                )
            )
            continue
        path = resp.get("spectrum_path", "")
        try:
            overall, components = score_fn(str(path))
        except Exception as exc:  # noqa: BLE001
            results.append(
                CandidateScore(
                    params=dict(params),
                    score=float("-inf"),
                    source="brute_force",
                    message=f"终谱评分失败: {exc}",
                )
            )
            continue
        results.append(
            CandidateScore(
                params=dict(params),
                score=overall,
                components=components,
                spectrum_path=str(path),
                source="brute_force",
            )
        )
    return results


def _same_phase(a: dict[str, float], b: dict[str, float], tol: float) -> bool:
    """相位一致(±180 符号歧义视为等价:p0 可翻转,p1 幅值一致)。"""
    p0_close = abs((a.get("p0", 0.0) - b.get("p0", 0.0) + 180.0) % 360.0 - 180.0) <= tol
    p1_close = abs(a.get("p1", 0.0) - b.get("p1", 0.0)) <= tol
    p1_abs_close = abs(abs(a.get("p1", 0.0)) - abs(b.get("p1", 0.0))) <= tol
    return p0_close and (p1_close or p1_abs_close)


def validate_direct_phase_equivalence(
    experiment: Experiment,
    backend: Any,
    candidates: list[dict[str, float]] | None = None,
    *,
    work_dir: Path | str | None = None,
    min_correlation: float = 0.5,
    top_tolerance: float = 30.0,
    score_fn: Callable[[str], tuple[float, dict[str, float]]] | None = None,
) -> EquivalenceReport:
    """对比内存内筛选与暴力逐候选后端评分的等价性。

    流程:
    1) 中性运行一次后端(相位覆盖 0/0)取转换 .fid;
    2) 内存内对每个候选计算吸收度代理分(score_in_memory_direct);
    3) 暴力参考:逐候选重跑后端 + 终谱 QC(backend_runs = 候选数);
    4) 比较:最优候选一致(±top_tolerance,允许 ±180 符号歧义)+ 排序相关。

    passed = 最优候选一致 且 Spearman 相关 >= min_correlation;
    未通过时上层应回退暴力搜索或保持默认相位(D005),不得直接采信内存内筛选。
    """
    candidates = candidates if candidates is not None else direct_phase_candidates()
    work = Path(work_dir) if work_dir else default_work_dir(experiment, backend)
    logs: list[str] = []

    direct_axis = "F2" if experiment.ndim == 2 else "F3"
    neutral = {direct_axis: (0.0, 0.0)}
    resp = backend.process(
        experiment, select_method(experiment), direct_phase_override=neutral
    )
    if not resp.get("success"):
        raise ValueError(f"中性后端运行失败: {resp.get('message')}")
    logs.append("中性运行完成(相位 0/0,取转换 .fid)")

    fid_path = _direct_fid_path(work, experiment)
    if fid_path is None:
        raise ValueError(f"未找到转换后 .fid: {work}")
    import nmrglue as ng

    _dic, fid = ng.pipe.read(str(fid_path))

    in_memory = score_in_memory_direct(fid, candidates)
    brute = brute_force_direct_scores(experiment, backend, candidates, score_fn=score_fn)
    brute_map = {_phase_key(c.params): c.score for c in brute if c.score > float("-inf")}

    if not brute_map:
        raise ValueError("暴力参考全部失败,无法验证等价性")
    best_bf = max(brute, key=lambda c: c.score)
    best_bf_params = best_bf.params
    if in_memory:
        best_mem_key = max(in_memory, key=in_memory.get)
        best_mem_params = next(
            c for c in candidates if _phase_key(c) == best_mem_key
        )
    else:
        best_mem_params = {"p0": 0.0, "p1": 0.0}
    top_match = _same_phase(best_bf_params, best_mem_params, top_tolerance)

    common = [k for k in brute_map if k in in_memory]
    correlation = 0.0
    if len(common) >= 3:
        from scipy.stats import spearmanr

        correlation, _pvalue = spearmanr(
            [brute_map[k] for k in common], [in_memory[k] for k in common]
        )
        correlation = float(correlation)  # np.float64 -> float(JSON 可序列化)
        if correlation != correlation:  # nan(常数序列)
            correlation = 0.0

    passed = top_match and correlation >= min_correlation
    logs.append(f"暴力最优: {_phase_key(best_bf_params)} (score={best_bf.score:.1f})")
    logs.append(f"内存内最优: {_phase_key(best_mem_params)}")
    logs.append(
        f"最优候选一致={top_match}, Spearman 相关={correlation:.2f}, "
        f"通过={passed}"
    )
    return EquivalenceReport(
        candidates=[dict(c) for c in candidates],
        brute_force=dict(brute_map),
        in_memory=dict(in_memory),
        best_brute_force=dict(best_bf_params),
        best_in_memory=dict(best_mem_params),
        top_match=top_match,
        correlation=float(correlation),
        passed=passed,
        backend_runs=1 + len(candidates),
        logs=logs,
    )


def produce_phased_spectrum(
    experiment: Experiment,
    backend: Any,
    phases: dict[str, tuple[float, float]] | None = None,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把选中相位写回后端重跑一次产出最终谱(生产路径)。

    总后端运行 = 估计 1 次 + 生产 1 次(而非逐候选 N 次);
    最终谱由真实管线(process / reconstruct_nus)产出,与暴力搜索产出一致。
    """
    params = dict(params or {})
    direct_axis = "F2" if experiment.ndim == 2 else "F3"
    direct = phases.get(direct_axis) if phases else None
    if experiment.sampling.mode is SamplingMode.NUS:
        if direct is not None:
            params["direct_phase_override"] = direct
        return backend.reconstruct_nus(experiment, params)
    plan = select_method(experiment)
    return backend.process(experiment, plan, direct_phase_override=phases)



def select_direct_phase_refined(
    experiment: Experiment,
    backend: Any,
    center: dict[str, float],
    *,
    span: float = 60.0,
    step: float = 30.0,
    score_fn: Callable[[str], tuple[float, dict[str, float]]] | None = None,
) -> CandidateScore:
    """围绕中心相位做小邻域暴力细化(有界后端运行,选择与暴力搜索一致)。"""
    p0 = float(center.get("p0", 0.0))
    p1_center = float(center.get("p1", 0.0))
    offsets = np.arange(-span, span + 1e-9, step)
    candidates = direct_phase_candidates(
        p1_values=tuple(p1_center + float(o) for o in offsets), p0=p0
    )
    scores = brute_force_direct_scores(
        experiment, backend, candidates, score_fn=score_fn
    )
    valid = [c for c in scores if c.score > float("-inf")]
    if not valid:
        raise ValueError("邻域暴力细化全部失败")
    return max(valid, key=lambda c: c.score)


def decide_refinement(
    report: EquivalenceReport,
    refined: CandidateScore | None,
    *,
    min_correlation: float = 0.5,
) -> tuple[dict[str, float], str]:
    """验证门控决策:通过 -> 内存内最优;未通过 -> 邻域细化最优;无细化 -> 默认。"""
    if report.top_match and report.correlation >= min_correlation:
        return dict(report.best_in_memory), "validated"
    if refined is not None:
        return dict(refined.params), "refined"
    return {"p0": 0.0, "p1": 0.0}, "default"


@dataclass
class PhaseSelectionResult:
    """验证门控的自动相位选择结果。"""

    phase: dict[str, float]
    spectrum_path: str
    method: str  # validated / refined / default
    backend_runs: int
    report: EquivalenceReport | None = None
    logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": dict(self.phase),
            "spectrum_path": self.spectrum_path,
            "method": self.method,
            "backend_runs": self.backend_runs,
            "report": self.report.to_dict() if self.report is not None else None,
            "logs": list(self.logs),
        }


def optimize_direct_phase_guaranteed(
    experiment: Experiment,
    backend: Any,
    candidates: list[dict[str, float]] | None = None,
    *,
    work_dir: Path | str | None = None,
    min_correlation: float = 0.5,
    refine_span: float = 60.0,
    refine_step: float = 30.0,
    score_fn: Callable[[str], tuple[float, dict[str, float]]] | None = None,
) -> PhaseSelectionResult:
    """验证门控的自动相位选择(准确性保证是前提)。

    1) validate_direct_phase_equivalence:内存内筛选 vs 小网格暴力参考;
    2) 通过 -> 采信内存内最优(method=validated);
    3) 未通过 -> 围绕内存内最优做小邻域暴力细化(method=refined,
       有界成本,选择与暴力搜索在该邻域一致);
    4) 最终谱由真实管线产出(produce_phased_spectrum)。

    无论哪条路径,最终谱都来自真实后端(process/reconstruct_nus),
    能力与「反复跑后端」一致;内存内代理只用于缩小候选范围,
    准确性由验证门槛与暴力细化兜底。当前仅支持 uniform(process)。
    """
    if experiment.sampling.mode is SamplingMode.NUS:
        raise ValueError(
            "自动相位门控选择当前仅支持 uniform(process);"
            "NUS 需 reconstruct_nus 路径,待 VM 验证后支持"
        )
    candidates = candidates if candidates is not None else direct_phase_candidates()
    work = Path(work_dir) if work_dir else default_work_dir(experiment, backend)
    logs: list[str] = []

    report = validate_direct_phase_equivalence(
        experiment,
        backend,
        candidates,
        work_dir=work,
        min_correlation=min_correlation,
        score_fn=score_fn,
    )
    logs.extend(report.logs)
    backend_runs = report.backend_runs

    refined: CandidateScore | None = None
    if not (report.top_match and report.correlation >= min_correlation):
        refined = select_direct_phase_refined(
            experiment,
            backend,
            report.best_in_memory,
            span=refine_span,
            step=refine_step,
            score_fn=score_fn,
        )
        n_refine = len(np.arange(-refine_span, refine_span + 1e-9, refine_step))
        backend_runs += n_refine
        logs.append(
            f"等价性验证未通过(top_match={report.top_match}, "
            f"corr={report.correlation:.2f}),邻域暴力细化 → "
            f"{_phase_key(refined.params)} (score={refined.score:.1f})"
        )

    phase, method = decide_refinement(
        report, refined, min_correlation=min_correlation
    )
    direct_axis = "F2" if experiment.ndim == 2 else "F3"
    final_resp = produce_phased_spectrum(
        experiment, backend, {direct_axis: (phase["p0"], phase["p1"])}
    )
    if not final_resp.get("success"):
        raise ValueError(f"最终谱生产失败: {final_resp.get('message')}")
    backend_runs += 1
    logs.append(
        f"最终谱(真实管线) → {final_resp.get('spectrum_path')} "
        f"[method={method}]"
    )
    return PhaseSelectionResult(
        phase=phase,
        spectrum_path=str(final_resp.get("spectrum_path", "")),
        method=method,
        backend_runs=backend_runs,
        report=report,
        logs=logs,
    )



@dataclass
class SequentialPhaseResult:
    """逐维暴力相位优化结果(用户方案)。"""

    phases: dict[str, tuple[float, float]]
    spectrum_path: str
    backend_runs: int
    method: str = "sequential_brute_force"
    logs: list[str] = field(default_factory=list)
    optimized: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _grid_step(values: tuple[float, ...]) -> float:
    """等距网格的步长(相邻差的中位数);不足 2 点返回 0。"""
    if len(values) < 2:
        return 0.0
    diffs = sorted(
        float(values[i + 1]) - float(values[i]) for i in range(len(values) - 1)
    )
    return float(diffs[len(diffs) // 2])


def _refine_steps(coarse_step: float, final_step: float) -> list[float]:
    """从粗步长到目标步长的细化序列(约 1/3 递减,最后一级为目标步长)。"""
    steps: list[float] = []
    s = float(coarse_step)
    while True:
        nxt = s / 3.0
        if nxt <= final_step:
            steps.append(float(final_step))
            break
        steps.append(nxt)
        s = nxt
    return steps


def _refine_window(center: float, prev_step: float, new_step: float) -> list[float]:
    """围绕 center 的细化窗口:覆盖 ±prev_step/2,按 new_step 取点。"""
    half = prev_step / 2.0
    n = int(math.ceil(half / new_step))
    return [center + k * new_step for k in range(-n, n + 1)]


def optimize_phase_sequential(
    experiment: Experiment,
    backend: Any,
    *,
    axes: list[str] | None = None,
    p0_values: tuple[float, ...] = (-45.0, 0.0, 45.0),
    p1_values: tuple[float, ...] = (-90.0, -60.0, -30.0, 0.0, 30.0, 60.0, 90.0),
    score_fn: Callable[[str], tuple[float, dict[str, float]]] | None = None,
    work_dir: Path | str | None = None,
    refine: bool = True,
    final_step: float = 5.0,
) -> SequentialPhaseResult:
    """逐维相位优化:粗网格 + 多尺度细化(直接维 → 间接维,依次固定)。

    传统采样:每候选相位重跑一次后端管线(process,direct_phase_override 覆盖
    该轴 PS),对终谱做整体 QC 评分(全数据集);NUS:先 SMILE 重构一次
    (reconstruct_nus,直接维随重构固化),再从重构平面逐间接维候选跑
    finalize_nus(不重跑 SMILE)。

    搜索策略(用户方案:粗到细,而非固定步长全搜索):粗网格 p1 30°(-90..90)
    / p0 45°(-45..45)取最优,再逐级细化(约 1/3 递减)到 final_step(默认 5°):
    每级在上一级最优 ±上一步长/2 窗口内按新步长联合扫描 p0×p1;单峰假设下
    结果与最优相位偏差 ≤ final_step/2(默认 ≤2.5°)。实测 uniform 2D 多尺度
    ~13s、NUS 2D ~6s(VM sampleA),直接优化足够快,不做「够好即停」前置过滤。
    refine=False 时仅跑粗网格。日志逐轴说明相位变化与分数增益。
    """
    plan = select_method(experiment)
    if axes is None:
        axes = [dim.logical_axis for dim in experiment.dimensions]  # 直接维在前
    if p1_values is None:
        p1_values = (-90.0, -60.0, -30.0, 0.0, 30.0, 60.0, 90.0)
    coarse = [
        (float(p0), float(p1))
        for p1 in p1_values
        for p0 in p0_values
    ]
    score_fn = score_fn or _default_phase_score
    is_nus = experiment.sampling.mode is SamplingMode.NUS
    direct_axis = "F2" if experiment.ndim == 2 else "F3"
    search_axes = [a for a in axes if a != direct_axis] if is_nus else axes
    fixed: dict[str, tuple[float, float]] = {}
    backend_runs = 0
    logs: list[str] = []
    spectrum_path = ""
    optimized: list[str] = []
    unchanged: list[str] = []

    if is_nus:
        resp = backend.reconstruct_nus(experiment, {})
        backend_runs += 1
        if not resp.get("success"):
            raise ValueError(f"NUS SMILE 重构失败: {resp.get('message')}")
        logs.append("NUS:SMILE 重构完成(直接维随重构固化),逐间接维候选跑 finalize")

    p0_step = _grid_step(tuple(p0_values))
    p1_step = _grid_step(tuple(p1_values))
    steps0 = _refine_steps(p0_step, final_step) if refine and p0_step > 0 else []
    steps1 = _refine_steps(p1_step, final_step) if refine and p1_step > 0 else []
    levels = max(len(steps0), len(steps1))

    for axis in search_axes:
        scored: dict[tuple[float, float], tuple[float, str]] = {}

        def _run_phase(phase: tuple[float, float]) -> None:
            nonlocal backend_runs
            if phase in scored:
                return
            override = {**fixed, axis: phase}
            if is_nus:
                resp = backend.finalize_nus(
                    experiment, phases=override, work_dir=work_dir
                )
            else:
                resp = backend.process(
                    experiment, plan, direct_phase_override=override
                )
            backend_runs += 1
            if not resp.get("success"):
                logs.append(f"{axis} 候选 {phase}: 运行失败 {resp.get('message')}")
                return
            path = resp.get("spectrum_path", "")
            try:
                score, _components = score_fn(str(path))
            except Exception as exc:  # noqa: BLE001 - 单候选失败不影响其它
                logs.append(f"{axis} 候选 {phase}: 评分失败 {exc}")
                return
            scored[phase] = (float(score), str(path))

        for phase in coarse:
            _run_phase(phase)
        if levels:
            prev0, prev1 = p0_step, p1_step
            for level in range(levels):
                if not scored:
                    break
                s0 = steps0[level] if level < len(steps0) else (
                    steps0[-1] if steps0 else 0.0
                )
                s1 = steps1[level] if level < len(steps1) else (
                    steps1[-1] if steps1 else 0.0
                )
                best = max(scored, key=lambda p: scored[p][0])
                w0 = _refine_window(best[0], prev0, s0) if steps0 else [best[0]]
                w1 = _refine_window(best[1], prev1, s1) if steps1 else [best[1]]
                for p0 in w0:
                    for p1 in w1:
                        _run_phase((p0, p1))
                prev0, prev1 = s0, s1
        if not scored:
            raise ValueError(f"轴 {axis} 相位候选全部失败")
        best_phase = max(scored, key=lambda p: scored[p][0])
        best_score, best_path = scored[best_phase]
        # 相位置信度:与 ±final_step 内已评分邻域的最优分差(评分面陡峭度)。
        neighbor_scores = [
            s
            for p, (s, _path) in scored.items()
            if p != best_phase
            and abs(p[0] - best_phase[0]) <= final_step
            and abs(p[1] - best_phase[1]) <= final_step
        ]
        if neighbor_scores:
            margin = best_score - max(neighbor_scores)
            if margin < 1.0:
                logs.append(
                    f"{axis}: 相位评分余量 {margin:.2f} 分(<1),评分面"
                    f"平坦,最佳相位置信度低(±{final_step:g}° 内差异不显著)"
                )
            else:
                logs.append(f"{axis}: 相位评分余量 {margin:.2f} 分,最优较明确")
        fixed[axis] = best_phase
        spectrum_path = best_path
        baseline_score = scored.get((0.0, 0.0))
        if baseline_score is not None and best_phase == (0.0, 0.0):
            logs.append(
                f"{axis}: 候选未优于当前相位,保持 (0,0) "
                f"(score={best_score:.1f}),已固定"
            )
            unchanged.append(axis)
        elif baseline_score is not None:
            gain = best_score - baseline_score[0]
            logs.append(
                f"{axis}: 相位已优化 (0,0) → {best_phase} "
                f"(score={baseline_score[0]:.1f} → {best_score:.1f}, +{gain:.1f}),已固定"
            )
            optimized.append(axis)
        else:
            logs.append(f"{axis}: 最优 {best_phase} (score={best_score:.1f}),已固定")
            optimized.append(axis)

    logs.append(
        "相位优化总结: "
        + ("已优化 " + ",".join(optimized) if optimized else "已优化 无")
        + "; "
        + ("未优化 " + ",".join(unchanged) if unchanged else "未优化 无")
    )
    return SequentialPhaseResult(
        phases=dict(fixed),
        spectrum_path=spectrum_path,
        backend_runs=backend_runs,
        method="sequential_brute_force",
        logs=logs,
        optimized=optimized,
        skipped=[],
    )
