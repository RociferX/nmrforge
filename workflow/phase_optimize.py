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

# 相位评分「平坦」阈值(0.2.45 VM 真实数据标定):±5° 余量 2D sampleA ≈0.11、
# 3D NUS sampleB ≈0.04(评分面近乎平坦);原 <1 分过严(真实数据必然触发),
# 下调为 0.05 以区分「最优较明确」与「评分面平坦」。0.2.47 起 margin 低于
# 该阈值不再只记日志,而是回退 (0,0)(方案 B)。
PHASE_SCORE_FLAT_MARGIN = 0.05

# 可复现性检查(0.2.47,方案 B):top-K 强迹线奇偶子采样两次最优 p1 差超过
# 该值判低置信并回退 (0,0)。
PHASE_REPRODUCIBILITY_TOL = 10.0

# 0.2.75 评分改为「基线固定迹线中位数净吸收」(0-100 = 50×(median+1)):
# 阈值按旧方案净吸收语义换算——旧平台容差 0.02(净吸收)≈ 1.0 分;
# 旧 ±90° 对称性容差 0.05(净吸收)≈ 2.5 分。
PHASE_PLATEAU_TOL = 1.0
PHASE_SYMMETRY_TOL = 2.5


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


def _default_phase_score_axis(path: str, axis: str) -> tuple[float, dict[str, float]]:
    """相位专用评分(逐轴):沿被优化轴的一维剖面峰窗负面积。

    0.2.63 起默认评分改用旧项目(NMRFlow)的逐轴方式:相位误差的色散
    负瓣沿被调轴方向展开,取峰位置沿该轴的一维剖面统计负值占比(1-na);
    二维峰窗会把已调好方向的峰形纳入,稀释目标轴信号(VM sampleF 实测)。
    """
    import nmrglue as ng

    from core.qc import phase_quality

    _dic, data = ng.pipe.read(path)
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        arr = arr.real
    axis_idx = _axis_index(axis)
    na = phase_quality.negative_area_axis(arr, axis_idx)
    score = 100.0 * (1.0 - na)
    return score, {"negative_area_fraction": na}


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


def _spectrum_real(path: str) -> np.ndarray:
    import nmrglue as ng

    _dic, data = ng.pipe.read(path)
    arr = np.asarray(data)
    return arr.real if np.iscomplexobj(arr) else arr


def _axis_traces(real: np.ndarray, axis: int) -> np.ndarray:
    """把谱沿 axis 展开为 (n_trace, axis_len),任意维度通用。"""
    moved = np.moveaxis(np.asarray(real, dtype=float), axis, -1)
    return moved.reshape(-1, moved.shape[-1])


def _trace_indices_fixed(
    real: np.ndarray, axis: int, threshold: float = 0.0
) -> tuple[list[int], list[int]]:
    """返回沿 axis 的信号迹线下标及每条迹线最强点位置(旧方案 _trace_indices)。

    阈值过滤(≤threshold 的迹线跳过);threshold<=0 时取全部。
    2D 返回 (另一维下标, 沿 axis 最强点);3D+ 返回 (扁平迹线行号, 最强点)。
    """
    traces = _axis_traces(real, axis)
    indices: list[int] = []
    positions: list[int] = []
    for index in range(traces.shape[0]):
        trace = traces[index]
        if float(np.max(np.abs(trace))) <= threshold:
            continue
        indices.append(index)
        positions.append(int(np.argmax(np.abs(trace))))
    return indices, positions


def _window_metric(profile: np.ndarray) -> float:
    """一维剖面净吸收(正面积+负面积)/总绝对面积。

    吸收≈+1、色散≈0、负吸收(180° 反转)≈-1,天然惩罚负峰,保留反转惩罚。
    """
    positive = float(np.clip(profile, 0.0, None).sum())
    negative = float(np.clip(profile, None, 0.0).sum())
    total = float(np.abs(profile).sum())
    return (positive + negative) / total if total else 0.0


def _profile_metric_fixed(
    real: np.ndarray, iy: int, ix: int, axis: int = 0
) -> float:
    """峰窗 ±5 一维剖面净吸收(2D 便捷入口;3D+ 走 _trace_metrics_median)。"""
    if axis == 0:
        profile = real[max(0, iy - 5): iy + 6, ix]
    else:
        profile = real[iy, max(0, ix - 5): ix + 6]
    return _window_metric(profile)


def _trace_metrics_median(
    real: np.ndarray,
    axis: int,
    indices: list[int],
    positions: list[int],
) -> float:
    """固定迹线/峰位上逐条净吸收的中位数(旧方案 _trace_metrics_fixed 取中位数)。

    2D 窗口语义与 _profile_metric_fixed 等价;3D+ 用扁平迹线行 ±5 窗口,
    与 _trace_indices_fixed 的返回一致。
    """
    traces = _axis_traces(real, axis)
    scores: list[float] = []
    for index, peak in zip(indices, positions):
        if index < 0 or index >= traces.shape[0]:
            continue
        profile = traces[index, max(0, peak - 5): peak + 6]
        scores.append(_window_metric(profile))
    return float(np.median(scores)) if scores else 0.0


def _score_fixed_traces(
    path: str,
    axis: str,
    indices: list[int],
    positions: list[int],
) -> tuple[float, dict[str, float]]:
    """候选谱在基线固定迹线/峰位上的评分(0-100 = 50×(中位数净吸收+1))。"""
    real = _spectrum_real(path)
    median = _trace_metrics_median(real, _axis_index(axis), indices, positions)
    return 50.0 * (median + 1.0), {"trace_median": median}


def _axis_index(axis: str) -> int:
    """轴名 → 谱数组下标(F1=0, F2=1, F3=2)。"""
    return {"F1": 0, "F2": 1, "F3": 2}.get(axis, 0)


def _subsampled_score(
    path: str, axis: str, k: int = 500, group: str = "even"
) -> float:
    """沿 axis 取峰高 top-K 强迹线的半组子采样,内存内评估 phase_quality。"""
    import nmrglue as ng

    from core.qc import phase_quality

    _dic, data = ng.pipe.read(path)
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        arr = arr.real
    moved = np.moveaxis(arr, _axis_index(axis), -1)
    traces = moved.reshape(-1, moved.shape[-1])
    peak_mag = np.max(np.abs(traces), axis=-1)
    order = np.argsort(peak_mag)[::-1][:k]
    selected = order[0::2] if group == "even" else order[1::2]
    if len(selected) == 0:
        return 0.0
    return float(phase_quality.evaluate(traces[selected]).score)


def _reproducibility_check(
    neighbor_paths: list[tuple[tuple[float, float], str]],
    axis: str,
    *,
    k: int = 500,
) -> tuple[bool, float, float]:
    """可复现性:top-K 强迹线奇偶两组子采样,各自取邻域最优 p1。

    两次最优 p1 差 > PHASE_REPRODUCIBILITY_TOL(10°)判低置信。
    返回 (是否一致, p1_even, p1_odd)。"""
    p1s: list[float] = []
    for group in ("even", "odd"):
        best_phase: tuple[float, float] | None = None
        best_score = -1.0
        for phase, path in neighbor_paths:
            try:
                s = _subsampled_score(path, axis, k=k, group=group)
            except Exception:  # noqa: BLE001 - 子采样失败按 0 处理
                s = -1.0
            if s > best_score:
                best_score, best_phase = s, phase
        p1s.append(float(best_phase[1]) if best_phase is not None else 0.0)
    consistent = abs(p1s[0] - p1s[1]) <= PHASE_REPRODUCIBILITY_TOL
    return consistent, p1s[0], p1s[1]


def _joint_recheck(
    experiment: Experiment,
    backend: Any,
    plan: Any,
    is_nus: bool,
    fixed: dict[str, tuple[float, float]],
    search_axes: list[str],
    final_step: float,
    work_dir: Path | str | None,
    score_fn: Callable[[str], tuple[float, dict[str, float]]] | None = None,
    backend_runs: list[int] | None = None,
    phase_cache: dict[
        tuple[tuple[str, tuple[float, float]], ...], tuple[float, str]
    ] | None = None,
    *,
    trace_map: dict[str, tuple[list[int], list[int]]] | None = None,
) -> tuple[dict[str, tuple[float, float]], float, str, int, float, float, str]:
    """全部轴固定的联合 ±final_step 邻域复核(p1 每轴 3 值,含全零组合)。

    返回 (最优 phases, 最优 score, 谱路径, 新增后端运行数,
    顺序固定组合 score, 全零组合 score, 全零谱路径)。
    0.2.75:默认评分(score_fn=None)时与顺序搜索同用「基线固定迹线中位数
    净吸收」逐轴取均值,避免联合复核退回旧全谱负面积指标、与顺序搜索
    不一致(sampleI F2 曾被带回 210°)。
    """
    import itertools

    if backend_runs is None:
        backend_runs = [0]
    if phase_cache is None:
        phase_cache = {}

    def _joint_score_path(path: str) -> float:
        if trace_map:
            vals: list[float] = []
            for ax, (idx, pos) in trace_map.items():
                if not idx:
                    continue
                vals.append(_score_fixed_traces(path, ax, idx, pos)[0])
            return float(np.mean(vals)) if vals else -1.0
        if score_fn is None:
            return -1.0
        overall, _components = score_fn(path)
        return float(overall)

    offsets = (-final_step, 0.0, final_step)
    combos: list[dict[str, tuple[float, float]]] = []
    for combo in itertools.product(offsets, repeat=len(search_axes)):
        phases = dict(fixed)
        for axis, offset in zip(search_axes, combo):
            p0, p1 = fixed[axis]
            phases[axis] = (p0, p1 + offset)
        combos.append(phases)
    all_zero = {axis: (0.0, 0.0) for axis in search_axes}
    combos.append(all_zero)
    best_phases = dict(fixed)
    best_score = -1.0
    best_path = ""
    fixed_score = -1.0
    zero_score = -1.0
    zero_path = ""
    runs = 0
    for phases in combos:
        cache_key = tuple(sorted(phases.items()))
        cached = phase_cache.get(cache_key)
        if cached is not None:
            # 复用顺序搜索已产出的后端产物,重新评分(评分基准一致)
            path = cached[1]
        else:
            if is_nus:
                resp = backend.finalize_nus(
                    experiment, phases=phases, work_dir=work_dir
                )
            else:
                resp = backend.process(
                    experiment, plan, direct_phase_override=phases
                )
            runs += 1
            if not resp.get("success"):
                continue
            path = str(resp.get("spectrum_path", ""))
        try:
            score = _joint_score_path(path)
        except Exception:  # noqa: BLE001 - 单候选失败不影响其它
            continue
        if phases == fixed:
            fixed_score = float(score)
        if phases == all_zero:
            zero_score = float(score)
            zero_path = path
        if score > best_score:
            best_phases, best_score, best_path = dict(phases), float(score), path
    backend_runs[0] += runs
    return best_phases, best_score, best_path, runs, fixed_score, zero_score, zero_path


def optimize_phase_sequential(
    experiment: Experiment,
    backend: Any,
    *,
    axes: list[str] | None = None,
    # 0.2.75:p0 默认全圆 0-360 30° 步(旧方案粗搜 12 点覆盖全象限,
    # sampleI 真实 F2 p0=-120° 在网格内);p0 取模 360
    p0_values: tuple[float, ...] = tuple(
        float(v) for v in range(0, 360, 30)
    ),
    p1_values: tuple[float, ...] = (-90.0, -60.0, -30.0, 0.0, 30.0, 60.0, 90.0),
    score_fn: Callable[[str], tuple[float, dict[str, float]]] | None = None,
    work_dir: Path | str | None = None,
    refine: bool = True,
    final_step: float = 5.0,
) -> SequentialPhaseResult:
    """逐维相位优化:粗网格 + 多尺度细化(均匀:间接维 → 直接维,依次固定;NUS:逐间接维)。

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
    is_nus = experiment.sampling.mode is SamplingMode.NUS
    direct_axis = "F2" if experiment.ndim == 2 else "F3"
    if axes is None:
        dims = [dim.logical_axis for dim in experiment.dimensions]
        if is_nus:
            axes = [a for a in dims if a != direct_axis]
        else:
            # 0.2.75:均匀路径先间接后直接(旧项目 NMRFlow 顺序)——直接维在
            # 间接维校正后的谱上锁点,避免 (0,0) 伪影脊导致反转相位
            # (sampleI F2 先搜得 180° 反转,F1 先修后 F2 收敛 300° 主峰纯吸收)
            axes = [a for a in dims if a != direct_axis] + [direct_axis]
    if p1_values is None:
        # 0.2.63:默认范围 ±180°(真实数据最优相位可超出 ±90°,sampleF F1=150°)
        p1_values = tuple(float(v) for v in range(-180, 181, 30))
    custom_score = score_fn is not None
    # 0.2.75:默认评分(固定迹线中位数)±5 窗口内 p1 天然弱,粗搜/细化只搜
    # p0(p1 固定 0,旧项目方案),p1 在末尾 {0,±22.5} 精修——避免退化 p1 维
    # 在联合网格里刷分(sampleI F1 曾被 p1=82.9° 带偏)。自定义 score_fn
    # 保留原 p0×p1 联合网格(其评分按设计对 p1 敏感)。
    coarse = (
        [(float(p0), 0.0) for p0 in p0_values]
        if not custom_score
        else [
            (float(p0), float(p1))
            for p1 in p1_values
            for p0 in p0_values
        ]
    )
    score_fn = score_fn or _default_phase_score
    search_axes = [a for a in axes if a != direct_axis] if is_nus else axes
    fixed: dict[str, tuple[float, float]] = {}
    backend_runs = 0
    logs: list[str] = []
    spectrum_path = ""
    optimized: list[str] = []
    unchanged: list[str] = []

    # 候选缓存(联合复核复用顺序搜索已评分组合,性能优化)
    phase_cache: dict[
        tuple[tuple[str, tuple[float, float]], ...], tuple[float, str]
    ] = {}
    # 每轴基线锁定迹线(默认评分),供联合复核同基准
    axis_traces: dict[str, tuple[list[int], list[int]]] = {}

    if is_nus:
        work = Path(work_dir) if work_dir else default_work_dir(experiment, backend)
        planes_dir = work / ("nus3d_rc" if experiment.ndim >= 3 else "nus2d")
        params_file = work / ".nus_params.json"
        if experiment.ndim >= 3:
            planes_ready = planes_dir.is_dir() and list(planes_dir.glob("test*.ft1"))
        else:
            planes_ready = (planes_dir / "recon.ft1").is_file()
        reuse = False
        if planes_ready:
            try:
                prev_params = json.loads(params_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                prev_params = None
            reuse = prev_params == {}
        if reuse:
            logs.append("复用已存在的 SMILE 重构平面(参数一致),跳过重构")
        else:
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
            # p0 全圆:p0 取模 360(常数相位 360° 周期,谱不变,仅 key/记录归一)
            phase = (float(phase[0]) % 360.0, float(phase[1]))
            if phase in scored:
                return
            override = {**fixed, axis: phase}
            cache_key = tuple(sorted(override.items()))
            cached = phase_cache.get(cache_key)
            if cached is not None:
                scored[phase] = cached
                return
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
                if custom_score:
                    score, _components = score_fn(str(path))
                else:
                    # 0.2.75:默认评分 = 基线固定迹线中位数净吸收(旧 NMRFlow),
                    # 强负峰(折叠)只影响单条迹线,中位数聚合不受其主导
                    score, _components = _score_fixed_traces(
                        str(path), axis, trace_indices, trace_positions
                    )
            except Exception as exc:  # noqa: BLE001 - 单候选失败不影响其它
                logs.append(f"{axis} 候选 {phase}: 评分失败 {exc}")
                return
            scored[phase] = (float(score), str(path))
            phase_cache[cache_key] = (float(score), str(path))

        axis_idx = _axis_index(axis)
        trace_indices: list[int] = []
        trace_positions: list[int] = []
        # 基线谱锁定信号迹线(旧方案 _search_one_dim):基线 = 当前轴 (0,0)
        # 候选谱(其它轴已固定)。首个轴 spectrum_path 尚为空,必须先跑一次
        # (0,0) 作基线——0.2.75 修复:此前首个轴落入 zeros 退化分支,迹线
        # 位置全部 argmax=0,评分失真(F2 被带偏到 210°)。
        # 仅默认评分需要(自定义 score_fn 由调用方决定评分对象,不强制读谱)
        if not custom_score:
            if (0.0, 0.0) not in scored:
                _run_phase((0.0, 0.0))
            if (0.0, 0.0) in scored:
                baseline_real = _spectrum_real(scored[(0.0, 0.0)][1])
            elif spectrum_path and Path(spectrum_path).is_file():
                baseline_real = _spectrum_real(spectrum_path)
            else:
                # 基线谱不可得(后端全部失败等):保持空迹线,由 coarse 阶段
                # 的「候选全部失败」统一报错
                baseline_real = np.zeros((2, 2))
            noise = (
                float(np.std(baseline_real[:80, :40]))
                if baseline_real.size
                else 0.0
            )
            threshold = max(
                float(np.percentile(baseline_real, 99.5)), noise * 5.0
            )
            trace_indices, trace_positions = _trace_indices_fixed(
                baseline_real, axis_idx, threshold
            )
            if not trace_indices:
                trace_indices, trace_positions = _trace_indices_fixed(
                    baseline_real, axis_idx, -1.0
                )
            # (0,0) 若在迹线锁定前已评分(空迹线占位 50 分),用锁定迹线重评,
            # 保证基线与后续候选同基准
            if trace_indices and (0.0, 0.0) in scored:
                _score0, _c0 = _score_fixed_traces(
                    scored[(0.0, 0.0)][1], axis, trace_indices, trace_positions
                )
                scored[(0.0, 0.0)] = (_score0, scored[(0.0, 0.0)][1])
                _key0 = tuple(sorted({**fixed, axis: (0.0, 0.0)}.items()))
                phase_cache[_key0] = (_score0, scored[(0.0, 0.0)][1])
            if trace_indices:
                axis_traces[axis] = (list(trace_indices), list(trace_positions))

        for phase in coarse:
            _run_phase(phase)
        if not scored:
            raise ValueError(f"轴 {axis} 相位候选全部失败")
        # 粗网格最优与判别力(供门控回退:细网格平坦时保留粗定位)
        coarse_done = [p for p in coarse if p in scored]
        coarse_sorted = sorted(coarse_done, key=lambda p: -scored[p][0])
        coarse_best = coarse_sorted[0]
        # 0.2.75:粗网格 margin 只看「常数相位 p0」判别——新评分在 ±5 窗口内
        # p1(频率相关相位)天然弱,若沿用全体候选分差会因 p1 平坦误判整面
        # 平坦并回退 (0,0)(合成/真实单峰上 p0=120 的 p1=-90..90 同分)。
        # p0 是决定谱观感的强维度:不同 p0 的最佳分差 >= 阈值才算粗网格可靠。
        coarse_best_score = scored[coarse_best][0]
        p0_rivals = [
            s
            for p, (s, _path) in scored.items()
            if p in coarse_done
            and abs((p[0] - coarse_best[0] + 180.0) % 360.0 - 180.0) > 1e-6
        ]
        coarse_margin = (
            coarse_best_score - max(p0_rivals) if p0_rivals else 0.0
        )
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
                if custom_score:
                    w1 = _refine_window(best[1], prev1, s1) if steps1 else [best[1]]
                else:
                    w1 = [best[1]]  # 默认:p1 固定 0,仅细化 p0(旧方案)
                for p0 in w0:
                    for p1 in w1:
                        _run_phase((p0, p1))
                prev0, prev1 = s0, s1
        best_phase = max(scored, key=lambda p: scored[p][0])
        best_score, best_path = scored[best_phase]
        # 相位置信度:与 ±final_step 内已评分 p1 邻域的最优分差(评分面陡峭度)。
        # p1 是频率相关相位,判别主导;p0 为弱维度(实型谱上对负面积/熵影响小),
        # 纳入邻居会把 margin 拉平导致误判平坦。
        neighbor_scores = [
            s
            for p, (s, _path) in scored.items()
            if p != best_phase and abs(p[1] - best_phase[1]) <= final_step
        ]
        flat = False
        if neighbor_scores:
            margin = best_score - max(neighbor_scores)
            if margin < PHASE_SCORE_FLAT_MARGIN:
                flat = True
                logs.append(
                    f"{axis}: 相位评分余量 {margin:.2f} 分"
                    f"(<{PHASE_SCORE_FLAT_MARGIN:g}),评分面"
                    f"平坦,最佳相位置信度低(±{final_step:g}° 内差异不显著)"
                )
            else:
                logs.append(f"{axis}: 相位评分余量 {margin:.2f} 分,最优较明确")
        if not flat:
            # 可复现性:top-K 强迹线奇偶子采样各取邻域最优 p1,差 >10° 判低置信
            neighbor_paths = [
                (p, path)
                for p, (s, path) in scored.items()
                if abs(p[1] - best_phase[1]) <= final_step
            ]
            if len(neighbor_paths) >= 2:
                consistent, p1a, p1b = _reproducibility_check(
                    neighbor_paths, axis
                )
                if not consistent:
                    flat = True
                    logs.append(
                        f"{axis}: 可复现性检查未通过(top-K 子采样最优 p1 "
                        f"{p1a:g}/{p1b:g},差>{PHASE_REPRODUCIBILITY_TOL:g}°),"
                        f"回退 (0,0)"
                    )
        if flat:
            if coarse_best == (0.0, 0.0):
                # 粗网格最优即零相位 → 回退 (0,0)(SMILE 内建相位)
                if (0.0, 0.0) in scored:
                    best_phase = (0.0, 0.0)
                    best_score, best_path = scored[(0.0, 0.0)]
                else:
                    _run_phase((0.0, 0.0))
                    if (0.0, 0.0) in scored:
                        best_phase = (0.0, 0.0)
                        best_score, best_path = scored[(0.0, 0.0)]
                logs.append(f"{axis}: 已回退 (0,0)(粗网格最优为零)")
            elif coarse_margin >= PHASE_SCORE_FLAT_MARGIN:
                # 0.2.63:细网格平坦只说明 ±5° 内评分不敏感,粗网格最优仍是
                # 可靠相位定位(否则会把正确相位如 sampleF F1=150° 丢弃)
                if best_phase != coarse_best:
                    logs.append(
                        f"{axis}: 细网格评分平坦,采用粗网格最优 {coarse_best} "
                        f"(粗 margin={coarse_margin:.2f} 分)"
                    )
                    best_phase = coarse_best
                    best_score, best_path = scored[coarse_best]
                else:
                    logs.append(
                        f"{axis}: 细网格评分平坦,保持粗网格最优 {coarse_best} "
                        f"(粗 margin={coarse_margin:.2f} 分)"
                    )
            else:
                # 粗网格 p0 也平坦(新评分 ±5 窗口内 p1 天然弱,同 p0 的 p1
                # 同分不构成平坦):仅当零相位与最优同属平坦带才回退 (0,0),
                # 否则仍取粗网格最优——避免把显著优于零相位的正确相位丢弃
                # (合成/真实单峰上最优附近多候选饱和到同分,但 (0,0) 仍远低)
                zero_score = scored.get((0.0, 0.0))
                if (
                    zero_score is not None
                    and coarse_best_score - zero_score[0]
                    < PHASE_SCORE_FLAT_MARGIN
                ):
                    best_phase = (0.0, 0.0)
                    best_score, best_path = zero_score
                    logs.append(
                        f"{axis}: 已回退 (0,0)(粗网格平坦且零相位不劣于最优)"
                    )
                else:
                    if best_phase != coarse_best:
                        best_phase = coarse_best
                        best_score, best_path = scored[coarse_best]
                    logs.append(
                        f"{axis}: 粗网格 p0 平坦但最优显著优于零相位,"
                        f"采用粗网格最优 {coarse_best}"
                    )
        if refine:
            if custom_score:
                # 自定义评分:保留 p1 平台圆中位数(0.2.63 起)
                plateau_p1 = [
                    p[1]
                    for p, (s, _path) in scored.items()
                    if s >= best_score - PHASE_PLATEAU_TOL
                ]
                if len(plateau_p1) >= 2:
                    angles = np.deg2rad(plateau_p1)
                    center = float(
                        np.rad2deg(
                            np.arctan2(
                                np.mean(np.sin(angles)), np.mean(np.cos(angles))
                            )
                        )
                    )
                    center = ((center + 180.0) % 360.0) - 180.0
                    refined = (best_phase[0], center)
                    if abs(center - best_phase[1]) > 0.5 and refined not in scored:
                        _run_phase(refined)
                    if refined in scored:
                        r_score, r_path = scored[refined]
                        if r_score >= best_score - PHASE_PLATEAU_TOL:
                            best_phase, best_score, best_path = (
                                refined,
                                r_score,
                                r_path,
                            )
                            logs.append(
                                f"{axis}: 平台圆中位数 p1 → {center:.2f}° "
                                f"(score={r_score:.2f})"
                            )
            else:
                # 0.2.75 默认:固定 p1=0 搜 p0,平台圆中位数作用于 p0(旧方案,
                # 把常数相位从 5° 网格精修到亚度精度,如旧项目 -52°)
                plateau_p0 = [
                    p[0]
                    for p, (s, _path) in scored.items()
                    if s >= best_score - PHASE_PLATEAU_TOL
                ]
                if len(plateau_p0) >= 2:
                    angles = np.deg2rad(plateau_p0)
                    center = float(
                        np.rad2deg(
                            np.arctan2(
                                np.mean(np.sin(angles)), np.mean(np.cos(angles))
                            )
                        )
                    )
                    center = center % 360.0
                    refined = (center, best_phase[1])
                    if (
                        abs((center - best_phase[0] + 180.0) % 360.0 - 180.0) > 0.5
                        and refined not in scored
                    ):
                        _run_phase(refined)
                    if refined in scored:
                        r_score, r_path = scored[refined]
                        if r_score >= best_score - PHASE_PLATEAU_TOL:
                            best_phase, best_score, best_path = (
                                refined,
                                r_score,
                                r_path,
                            )
                            logs.append(
                                f"{axis}: 平台圆中位数 p0 → {center:.2f}° "
                                f"(score={r_score:.2f})"
                            )
            # ±90° 对称性消歧(旧项目 NMRFlow):p0 为弱维度,吸收度相近时
            # 选峰形更对称者(吸收≈+1,色散≈-1),避免落偏 90°
            from core.qc import phase_quality

            axis_idx = _axis_index(axis)

            def _sym_of(path: str) -> float:
                # 读谱失败(如测试 fake 产物无效)时按 0 处理,消歧跳过
                try:
                    return phase_quality.profile_symmetry_axis(
                        _spectrum_real(path), axis_idx
                    )
                except Exception:  # noqa: BLE001
                    return 0.0

            best_sym = _sym_of(best_path)
            for offset in (90.0, -90.0):
                cand = (best_phase[0] + offset, best_phase[1])
                if cand not in scored:
                    _run_phase(cand)
                if cand in scored:
                    c_score, c_path = scored[cand]
                    if c_score >= best_score - PHASE_SYMMETRY_TOL:
                        c_sym = _sym_of(c_path)
                        if c_sym > best_sym + 0.05:
                            logs.append(
                                f"{axis}: ±90° 对称性消歧 {best_phase} → "
                                f"{cand} (sym {best_sym:.2f}→{c_sym:.2f})"
                            )
                            best_phase, best_score, best_path = (
                                cand,
                                c_score,
                                c_path,
                            )
                            best_sym = c_sym
        if refine:
            # p1 精修(旧方案):仅试 {0, ±22.5},有提升才替换(更省)
            for p1 in (0.0, 22.5, -22.5):
                cand = (best_phase[0] % 360.0, p1)
                if cand not in scored:
                    _run_phase(cand)
                if cand in scored:
                    c_score, c_path = scored[cand]
                    if c_score > best_score:
                        logs.append(
                            f"{axis}: p1 精修 {best_phase[1]:g}° → {p1:g}° "
                            f"(score={c_score:.2f})"
                        )
                        best_phase, best_score, best_path = (
                            cand,
                            c_score,
                            c_path,
                        )
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

    if len(search_axes) >= 2 and refine and fixed:
        (
            joint_phases,
            joint_score,
            joint_path,
            joint_runs,
            fixed_score,
            zero_score,
            zero_path,
        ) = _joint_recheck(
            experiment,
            backend,
            plan,
            is_nus,
            fixed,
            search_axes,
            final_step,
            work_dir,
            None if not custom_score else score_fn,
            [backend_runs],
            phase_cache,
            trace_map=axis_traces,
        )
        if joint_phases == fixed:
            logs.append(
                f"联合复核: 顺序固定 {fixed} 即联合最优 "
                f"(score={joint_score:.2f}, 新增 {joint_runs} 次后端)"
            )
        elif joint_score - fixed_score >= PHASE_SCORE_FLAT_MARGIN:
            logs.append(
                f"联合复核: 联合最优 {joint_phases} (score={joint_score:.2f}) "
                f"优于顺序固定 {fixed} (score={fixed_score:.2f}, "
                f"+{joint_score - fixed_score:.2f}),已更新"
            )
            fixed = joint_phases
            spectrum_path = joint_path
        else:
            # 联合面平坦:顺序固定与联合最优差异不显著。per-axis 平坦门控
            # 已回退低置信轴(方案 B),此处保持顺序结果,不再整体回退——
            # 避免在评分面明确(per-axis margin 通过)时误伤。
            logs.append(
                f"联合复核: 联合面平坦(顺序 {fixed} score={fixed_score:.2f} "
                f"vs 联合最优 {joint_phases} score={joint_score:.2f}, "
                f"差 <{PHASE_SCORE_FLAT_MARGIN:g}),保持顺序固定"
            )

    logs.append(
        "相位优化总结: "
        + ("已优化 " + ",".join(optimized) if optimized else "已优化 无")
        + "; "
        + ("未优化 " + ",".join(unchanged) if unchanged else "未优化 无")
    )
    # 注意:不做 p1 归一化!NMRPipe PS 的 p1 是频率相关线性相位
    # (相位 = p0 + p1·k/max),p1+360 在中间点 k 处不等价(-200° 与 160° 谱
    # 不同);0.2.64 曾归一化导致基线重渲用错误相位、峰形劣化,已移除。
    return SequentialPhaseResult(
        phases={axis: (float(p0), float(p1)) for axis, (p0, p1) in fixed.items()},
        spectrum_path=spectrum_path,
        backend_runs=backend_runs,
        method="sequential_brute_force",
        logs=logs,
        optimized=optimized,
        skipped=[],
    )
