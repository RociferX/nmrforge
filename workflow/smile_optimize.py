"""SMILE 参数优化(可选,用户后选优化项;不进入自动处理流程)。

目标:在已有生成谱图参数(base_params)基础上只调整 SMILE 参数
(nSigma/thresh),尽可能保留真峰、剔除伪峰:
  1) 网格扫描:各参数组合各重构 1 次(SMILE 为确定性算法,同参数同输入
     逐位一致,故扫描不做组内重复),跨组合统计每个峰出现的组合数评估
     真伪(真峰应在多个参数组合下都出现,伪峰只在个别组合出现)——即
     跨组合去伪(0.2.162-补10 起不再做噪声注入重复去伪);
  2) 最终保留真峰数(可信度≥true_conf_min)最多的前 keep_top 个谱,
     谱与峰表写入与 raw 同级的 smile_optimized/ 目录。
逐峰可信度(0.2.162-补6/补8,Peak Confidence Score 规范):
score = snr_points(0~40) + stability_points(-15~+40)
+ shape_points(-5~+5) + local_noise_points(-5~+5),理论最大 90,
×100/90 归一化后 clamp 0~100(完美峰=100);S/N 与 SMILE 重构稳定性
为同等级一级核心证据;稳定性证据(出现率/强度 CV/位置漂移)来自
nSigma×thresh 双参数网格(保持现有网格)下的多次重构;同峰判定容差
每轴 4 点(VM 实测真峰位移 p95 约 4 点)。等级:A≥85/B 70-84/
C 55-69/D 40-54/E<40。

用法:
    results = optimize_smile_parameters(experiment, backend, base_params=run_params)
    print(format_results(results))
    save_report(results, Path("smile_optimize_report.json"))
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.data.internal_data_model import Experiment
from core.qc import noise, peak_detection, spectrum_quality


@dataclass
class SmileParameterResult:
    """一个参数组(SMILE 参数组合)的稳定性/跨组合评分与保留峰。"""

    params: dict[str, Any]
    repeats: int = 1
    n_combos: int = 0
    rank: int = 0
    true_peak_count: int = 0
    decision: str = ""
    overall: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    spectrum_path: str = ""
    stable_peaks: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# 优化程度 2x2..5x5:从 5 档默认值均匀取样(0.2.199-补29hz-修4)
_NSIGMA_FULL: tuple[float, ...] = (3.0, 4.0, 5.0, 6.0, 7.0)
_THRESH_FULL: tuple[float, ...] = (0.90, 0.93, 0.95, 0.97, 0.99)
SMILE_GRID_MIN, SMILE_GRID_MAX = 2, 5
SMILE_GRID_DEFAULT = 4  # 0.2.199-补29hz-修5(用户):默认 4x4=16 组


def _subsample(values: tuple[float, ...], count: int) -> tuple[float, ...]:
    """从 values 均匀取 count 个(含首尾)。"""
    if count <= 1:
        return (values[len(values) // 2],)
    if count >= len(values):
        return tuple(values)
    picked = [round(i * (len(values) - 1) / (count - 1)) for i in range(count)]
    return tuple(values[i] for i in dict.fromkeys(picked))


def smile_grid(size: int = SMILE_GRID_DEFAULT) -> list[dict[str, Any]]:
    """按优化程度生成 n×n 网格(2x2..5x5,默认 5x5=25 组)。

    2x2 最快(4 组),5x5 最细(25 组);耗时大致与组数成正比。
    """
    count = max(SMILE_GRID_MIN, min(SMILE_GRID_MAX, int(size or SMILE_GRID_DEFAULT)))
    return default_smile_grid(
        _subsample(_NSIGMA_FULL, count), _subsample(_THRESH_FULL, count)
    )


def estimate_scan_seconds(
    experiment: Experiment, n_combos: int
) -> tuple[float, float]:
    """按数据规模粗估每组/总耗时(秒);第一组跑完由实测覆盖。

    经验模型(2026-09-10,sampleC 3D NUS 250 点/直接维 TD 2048 实测 86s/组):
    每组 ≈ 85s × (采样点数/250) × (直接维 TD/2048);2D 再 ×0.3。
    """
    sampling = getattr(experiment, "sampling", None)
    nus_points = len(getattr(sampling, "nus_list", None) or []) or 250
    direct_td = 2048.0
    for dim in getattr(experiment, "dimensions", None) or []:
        role = str(getattr(getattr(dim, "role", None), "name", ""))
        if role.startswith("DIRECT"):
            try:
                direct_td = float(getattr(dim, "td", 0) or 0) or direct_td
            except (TypeError, ValueError):
                pass
    per_group = 85.0 * (float(nus_points) / 250.0) * (direct_td / 2048.0)
    if int(getattr(experiment, "ndim", 2) or 2) <= 2:
        per_group *= 0.3
    per_group = max(5.0, per_group)
    return per_group, per_group * max(1, int(n_combos))

def default_smile_grid(
    nsigma_values: tuple[float, ...] = (3.0, 4.0, 5.0, 6.0, 7.0),
    thresh_values: tuple[float, ...] = (0.90, 0.93, 0.95, 0.97, 0.99),
) -> list[dict[str, Any]]:
    """默认参数网格:nSigma × thresh(0.2.162 起仅优化 SMILE 参数;
    0.2.162-补加密为 5×5=25 组,调参更细)。"""
    return [
        {"nsigma": nsigma, "thresh": thresh}
        for nsigma in nsigma_values
        for thresh in thresh_values
    ]


def _read_spectrum(path: str):
    """读取终谱(dic, data)。"""
    import nmrglue as ng

    return ng.pipe.read(str(path))


def _detect_peaks(spectrum_path: str) -> list[peak_detection.Peak]:
    """读取终谱并检测峰(2D/3D 通用)。"""
    _dic, data = _read_spectrum(spectrum_path)
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        arr = arr.real
    return peak_detection.detect(arr)


def _ppm_at_fraction(axis_ppm: np.ndarray, value: float) -> float:
    """亚像素索引 → ppm 线性插值(0.2.199-补29eo 峰位亚像素修正后)。"""
    n = axis_ppm.size
    if n < 2:
        return float(axis_ppm[0]) if n else 0.0
    i0 = max(0, int(np.floor(value)))
    i1 = min(i0 + 1, n - 1)
    i0 = min(i0, i1)
    frac = value - i0
    return float(axis_ppm[i0] * (1.0 - frac) + axis_ppm[i1] * frac)


def _snap_key(position: tuple[float, ...], tol_pts: float) -> tuple[float, ...]:
    """峰位置快照键:按容差网格取整,供组内/跨组合匹配。

    0.2.199-补29eo:先归一到整数像素——亚像素修正使位置在 ±0.5px 内抖动,
    若不先取整,峰恰在 tol 网格边界(如 10pt/4pt 桶)时抖动会翻桶,
    跨组合匹配断裂(稳定峰被误判为伪峰)。
    """
    ipos = tuple(round(float(v)) for v in position)
    if tol_pts > 0:
        return tuple(round(float(v) / tol_pts) * tol_pts for v in ipos)
    return ipos


def _score_candidate(
    stable: list[peak_detection.Peak],
    union_count: int,
    cross_support: float,
    quality: Any,
) -> tuple[float, dict[str, float]]:
    """科学评分:稳定性 + 跨组合支持 + 信噪比 + 峰数 + 伪影(0.2.162)。"""
    stability = len(stable) / max(union_count, 1)
    mean_snr = float(np.mean([p.snr for p in stable])) if stable else 0.0
    artifact = float(quality.score.components.artifact)
    snr_norm = min(mean_snr / 10.0, 1.0)
    count_norm = min(len(stable) / 20.0, 1.0)
    overall = 100.0 * (
        0.30 * stability
        + 0.25 * cross_support
        + 0.25 * snr_norm
        + 0.10 * count_norm
        + 0.10 * (artifact / 100.0)
    )
    return overall, {
        "stability": round(stability, 3),
        "cross_support": round(cross_support, 3),
        "snr": round(mean_snr, 1),
        "peak_count": len(stable),
        "artifact": round(artifact, 1),
    }


# ---------------------------------------------------------------------------
# Peak Confidence Score(0.2.162-补6,Peak Confidence Score 规范):
#   score = snr_points(0~40) + stability_points(-15~+40)
#           + shape_points(-5~+5) + local_noise_points(-5~+5),clamp 0~100
#   stability = existence(-5~+15) + intensity_cv(-5~+15) + position(-5~+10),
#   证据来自 nSigma×thresh 双参数网格(保持现有网格)下的多次重构。
# ---------------------------------------------------------------------------


def _snr_points(snr: float) -> float:
    """S/N 基础分(0~40):按规范分段(1.5/2/2.5/3/3.5/4/5/6/8/10)。"""
    if snr >= 10.0:
        return 40.0
    if snr >= 8.0:
        return 37.0
    if snr >= 6.0:
        return 33.0
    if snr >= 5.0:
        return 28.0
    if snr >= 4.0:
        return 23.0
    if snr >= 3.5:
        return 18.0
    if snr >= 3.0:
        return 14.0
    if snr >= 2.5:
        return 10.0
    if snr >= 2.0:
        return 6.0
    if snr >= 1.5:
        return 3.0
    return 0.0


def _existence_points(rate: float) -> float:
    """峰存在性(-5~+15):出现率=support/n_combos,按 5/5 表等比推广。"""
    if rate >= 0.9:
        return 15.0
    if rate >= 0.8:
        return 12.0
    if rate >= 0.6:
        return 7.0
    if rate >= 0.4:
        return 2.0
    if rate >= 0.2:
        return -2.0
    return -5.0


def _intensity_cv_points(cv: float | None) -> float:
    """峰强稳定性(-5~+15):CV=std(height)/mean(height);<2 次样本记 0。"""
    if cv is None:
        return 0.0
    if cv < 0.05:
        return 15.0
    if cv < 0.10:
        return 13.0
    if cv < 0.15:
        return 10.0
    if cv < 0.20:
        return 7.0
    if cv < 0.30:
        return 4.0
    if cv < 0.50:
        return 1.0
    if cv < 0.75:
        return -2.0
    return -5.0


def _position_shift_points(shift: float | None) -> float:
    """峰位稳定性(-5~+10):shift=位置极差/线宽;<2 次样本记 0。"""
    if shift is None:
        return 0.0
    if shift < 0.10:
        return 10.0
    if shift < 0.25:
        return 8.0
    if shift < 0.50:
        return 5.0
    if shift < 1.00:
        return 2.0
    if shift < 1.50:
        return -2.0
    return -5.0


def _fwhm_axis(arr: np.ndarray, pos: tuple[int, ...], axis: int) -> float:
    """沿某轴半高全宽(点);无法测量时回退 2 点(规范 fallback)。"""
    max_v = float(arr[pos])
    if max_v <= 0.0:
        return 2.0
    half = max_v / 2.0
    size = arr.shape[axis]
    lo = pos[axis]
    while lo > 0 and float(arr[pos[:axis] + (lo - 1,) + pos[axis + 1 :]]) >= half:
        lo -= 1
    hi = pos[axis]
    while hi < size - 1 and float(arr[pos[:axis] + (hi + 1,) + pos[axis + 1 :]]) >= half:
        hi += 1
    fwhm = float(hi - lo)
    return fwhm if fwhm >= 1.0 else 2.0


def _position_shift(
    positions: list[tuple[float, ...]], arr: np.ndarray, pos: tuple[int, ...]
) -> float | None:
    """峰位漂移:各轴 position_range/linewidth 的最大值;不足 2 次样本返回 None。"""
    if len(positions) < 2:
        return None
    shifts: list[float] = []
    for axis in range(arr.ndim):
        vals = [p[axis] for p in positions if axis < len(p)]
        if len(vals) < 2:
            continue
        rng = float(max(vals) - min(vals))
        shifts.append(rng / _fwhm_axis(arr, pos, axis))
    if not shifts:
        return None
    return float(max(shifts))


def _shape_score(arr: np.ndarray, pos: tuple[int, ...]) -> float:
    """峰形修正(-5~+5):逐轴正向证据(对称/单峰/合理宽度/干净衰减)归一化,
    异常(不对称/次级峰/异常尖峰/振铃)扣分;干净峰可达 +5(0.2.162-补7)。"""
    max_v = float(arr[pos])
    if max_v <= 0.0:
        return 0.0
    per_axis: list[float] = []
    for axis in range(arr.ndim):
        size = arr.shape[axis]
        lo0 = max(0, pos[axis] - 3)
        trace = [
            float(arr[pos[:axis] + (p,) + pos[axis + 1 :]])
            for p in range(lo0, min(size, pos[axis] + 4))
        ]
        center = pos[axis] - lo0
        if len(trace) < 2:
            continue
        axis_score = 0.0
        asym = 0.0
        n = 0
        for k in (1, 2, 3):
            lo_i = center - k
            hi_i = center + k
            if 0 <= lo_i < len(trace) and 0 <= hi_i < len(trace):
                asym += abs(trace[lo_i] - trace[hi_i]) / max(max_v, 1e-12)
                n += 1
        if n:
            asym_norm = asym / n
            if asym_norm < 0.10:
                axis_score += 1.0
            elif asym_norm < 0.20:
                axis_score += 0.5
            elif asym_norm > 0.35:
                axis_score -= 1.0
        extra = 0
        for k in (-2, -1, 1, 2):
            i = center + k
            if 0 < i < len(trace) - 1 and trace[i] >= trace[center] * 0.3:
                if trace[i] > trace[i - 1] and trace[i] > trace[i + 1]:
                    extra += 1
        if extra == 0:
            axis_score += 1.0
        elif extra == 1:
            axis_score -= 0.5
        else:
            axis_score -= 1.5
        fwhm = _fwhm_axis(arr, pos, axis)
        if 1.5 <= fwhm <= 6.0:
            axis_score += 1.0
        elif fwhm < 1.2:
            axis_score -= 0.5
        elif fwhm > 12.0:
            axis_score -= 0.5
        eps = 0.05 * max_v
        mono = True
        for k in range(1, 3):
            if center + k < len(trace) and trace[center + k] > trace[center + k - 1] + eps:
                mono = False
                break
            if center - k >= 0 and trace[center - k] > trace[center - k + 1] + eps:
                mono = False
                break
        axis_score += 1.0 if mono else -0.5
        per_axis.append(axis_score)
    if not per_axis:
        return 0.0
    max_total = float(arr.ndim * 4.0)
    return round(max(-5.0, min(5.0, sum(per_axis) * 5.0 / max_total)), 1)


def _local_noise_score(
    arr: np.ndarray, pos: tuple[int, ...], global_sigma: float
) -> float:
    """局部噪声/基线修正(-5~+5):排除峰核心的环形区域噪声 vs 全谱噪声;
    干净区域可达 +5(0.2.162-补7)。"""
    outer_starts = tuple(max(0, p - 8) for p in pos)
    outer = tuple(
        slice(lo, min(s, p + 9))
        for p, s, lo in zip(pos, arr.shape, outer_starts)
    )
    region = arr[outer]
    if region.size == 0:
        return 0.0
    core = tuple(
        slice(max(0, p - 2) - lo, min(s, p + 3) - lo)
        for p, s, lo in zip(pos, arr.shape, outer_starts)
    )
    mask = np.ones(region.shape, dtype=bool)
    mask[core] = False
    ring = region[mask]
    if ring.size == 0:
        return 0.0
    local_sigma = float(np.median(np.abs(ring - np.median(ring)))) * 1.4826
    local_base = float(np.median(ring))
    base_ratio = abs(local_base) / max(local_sigma, 1e-12)
    ratio = local_sigma / max(global_sigma, 1e-12)
    if ratio < 0.8:
        score = 5.0 if base_ratio < 1.0 else 3.0
    elif ratio < 1.0:
        score = 3.0 if base_ratio < 2.0 else 1.0
    elif ratio <= 1.3:
        score = 1.0 if base_ratio < 2.0 else -2.0
    elif ratio <= 1.7:
        score = -2.0 if base_ratio < 3.0 else -4.0
    elif ratio <= 2.3:
        score = -4.0
    else:
        score = -5.0
    if base_ratio > 5.0:
        score = min(score, -3.0)
    return float(max(-5.0, min(5.0, score)))


def _grade(confidence: float) -> str:
    """等级(0~100 归一化后):A≥85,B 70-84,C 55-69,D 40-54,E<40。"""
    if confidence >= 85.0:
        return "A"
    if confidence >= 70.0:
        return "B"
    if confidence >= 55.0:
        return "C"
    if confidence >= 40.0:
        return "D"
    return "E"


def _compose_confidence(
    snr_points: float,
    stability_points: float,
    shape_points: float,
    local_noise_points: float,
) -> tuple[float, str]:
    """score = (snr + stability + shape + local_noise) × 100/90,clamp 0~100。

    四分量理论最大 90,归一化到 0~100(完美峰=100),避免 A 档(≥85)苛刻
    到需拿理论最大值的 94%(0.2.162-补8)。"""
    raw = (
        snr_points + stability_points + shape_points + local_noise_points
    )
    confidence = round(max(0.0, min(100.0, raw * 100.0 / 90.0)), 1)
    return confidence, _grade(confidence)


def _confidence_flags(
    snr: float,
    rate: float,
    intensity_cv: float | None,
    position_shift: float | None,
    shape_points: float,
    local_noise_points: float,
) -> list[str]:
    """硬性风险标记(不替代分数,随分数输出)。"""
    flags: list[str] = []
    if snr < 2.0:
        flags.append("very_low_snr")
    if rate <= 0.2:
        flags.append("reconstruction_unstable")
    if intensity_cv is not None and intensity_cv > 0.50:
        flags.append("intensity_unstable")
    if position_shift is not None and position_shift > 1.0:
        flags.append("position_unstable")
    if shape_points <= -4.0:
        flags.append("abnormal_peak_shape")
    if local_noise_points <= -4.0:
        flags.append("high_local_noise")
    return flags


def _peak_confidence_entry(
    peak: dict[str, Any],
    arr: np.ndarray,
    global_sigma: float,
    n_combos: int,
    key_to_combos: dict[tuple[float, ...], set[int]],
    key_heights: dict[tuple[float, ...], list[float]],
    key_positions: dict[tuple[float, ...], list[tuple[float, ...]]],
    peak_tol_pts: float,
) -> dict[str, Any]:
    """逐峰四分量可信度条目(扫描候选共用)。"""
    key = _snap_key(tuple(peak["position"]), peak_tol_pts)
    support = len(key_to_combos.get(key, set()))
    rate = support / n_combos if n_combos else 0.0
    heights = list(key_heights.get(key, []))
    positions = list(key_positions.get(key, []))
    snr = float(peak["snr"])
    snr_pts = _snr_points(snr)
    existence_pts = _existence_points(rate)
    cv = None
    if len(heights) >= 2:
        mean_h = float(np.mean(heights))
        if mean_h > 0:
            cv = float(np.std(heights)) / mean_h
    intensity_pts = _intensity_cv_points(cv)
    pos_i = tuple(round(v) for v in peak["position"])
    shift = _position_shift(positions, arr, pos_i)
    position_pts = _position_shift_points(shift)
    stability_pts = existence_pts + intensity_pts + position_pts
    shape_pts = _shape_score(arr, pos_i)
    local_pts = _local_noise_score(arr, pos_i, global_sigma)
    confidence, grade = _compose_confidence(
        snr_pts, stability_pts, shape_pts, local_pts
    )
    flags = _confidence_flags(snr, rate, cv, shift, shape_pts, local_pts)
    return {
        **dict(peak),
        "support": support,
        "cross_rate": round(rate, 3),
        "snr_points": round(snr_pts, 1),
        "existence_points": round(existence_pts, 1),
        "intensity_cv": round(cv, 3) if cv is not None else None,
        "intensity_points": round(intensity_pts, 1),
        "position_shift": round(shift, 3) if shift is not None else None,
        "position_points": round(position_pts, 1),
        "stability_points": round(stability_pts, 1),
        "shape_points": round(shape_pts, 1),
        "local_noise_points": round(local_pts, 1),
        "confidence": confidence,
        "grade": grade,
        "flags": flags,
    }


def _rank_by_true_peaks(scanned: list[dict[str, Any]], keep_top: int) -> None:
    """按真峰数(trusted true_peak_count)降序取前 keep_top 个候选,赋 rank 1..N。

    真峰数并列时按 overall 排序(0.2.162-补9)。"""
    scanned.sort(
        key=lambda e: (e["result"].true_peak_count, e["result"].overall),
        reverse=True,
    )
    for rank, entry in enumerate(scanned[: max(1, int(keep_top))], start=1):
        entry["result"].rank = rank


def optimize_smile_parameters(
    experiment: Experiment,
    backend: Any,
    base_params: dict[str, Any] | None = None,
    grid: list[dict[str, Any]] | None = None,
    *,
    cross_min: int = 2,
    peak_tol_pts: float = 4.0,
    keep_top: int = 3,
    true_conf_min: float = 55.0,
    fid_noise: float = 0.15,
    progress: Callable[[int, int, str], None] | None = None,
    on_result: Callable[[SmileParameterResult], None] | None = None,
) -> list[SmileParameterResult]:
    """网格扫描选优:每组 1 次重构(SMILE 确定性,不做组内重复),跨组合
    评估峰真伪(真峰应在多个参数组合下都出现,伪峰只在个别组合出现)。
    逐峰按 Peak Confidence Score 规范四分量评分:snr(0~40) + 重构稳定性
    (-15~+40) + 峰形(-5~+5) + 局部噪声(-5~+5),理论最大 90,×100/90
    归一化后 clamp 0~100(0.2.162-补8)。真峰 = 可信度 ≥ true_conf_min
    (默认 55,即 A/B/C);最终保留真峰数最多的前 keep_top 个谱
    (0.2.162-补9/补10:不做噪声注入重复去伪)。peak_tol_pts 为同峰
    判定容差(每轴点数,0.2.162-补5 由 2 放宽到 4)。backend 需提供
    reconstruct_nus(experiment, params);每组保留 base_params 的非
    SMILE 参数,只覆盖 nsigma/thresh 与 fid_noise/seed。"""
    grid = grid if grid is not None else default_smile_grid()
    base = dict(base_params or {})
    n_combos = len(grid)
    results: list[SmileParameterResult] = []
    key_to_combos: dict[tuple[float, ...], set[int]] = defaultdict(set)
    key_heights: dict[tuple[float, ...], list[float]] = defaultdict(list)
    key_positions: dict[tuple[float, ...], list[tuple[float, ...]]] = defaultdict(list)
    scanned: list[dict[str, Any]] = []
    total = len(grid)
    for index, params in enumerate(grid, start=1):
        if progress is not None:
            progress(index, total, f"正在优化 {index}/{total}: {params}")
        result = SmileParameterResult(params=dict(params), repeats=1)
        try:
            run_params = {
                **base,
                **params,
                "direct_phase_search": False,
                "display_phase_search": False,
                "fid_noise": float(fid_noise or 0.0),
                "fid_noise_seed": index * 1000,
            }
            resp = backend.reconstruct_nus(experiment, run_params)
            if not resp.get("success"):
                result.decision = "failed"
                result.message = str(resp.get("message", "重构失败"))
            else:
                last_spec = str(resp["spectrum_path"])
                peaks = _detect_peaks(last_spec)
                seen_keys: set[tuple[float, ...]] = set()
                for peak in peaks:
                    key = _snap_key(peak.position, peak_tol_pts)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    key_to_combos[key].add(index)
                    key_heights[key].append(float(peak.height))
                    key_positions[key].append(tuple(float(v) for v in peak.position))
                _dic, data = _read_spectrum(last_spec)
                quality = spectrum_quality.evaluate(data)
                result.spectrum_path = last_spec
                result.stable_peaks = [
                    {
                        "position": [float(v) for v in peak.position],
                        "height": float(peak.height),
                        "snr": float(peak.snr),
                    }
                    for peak in peaks
                ]
                scanned.append(
                    {
                        "result": result,
                        "keys": seen_keys,
                        "quality": quality,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - 单组失败不阻断其余候选
            result.decision = "error"
            result.message = str(exc)
        results.append(result)
        if on_result is not None:
            on_result(result)

    # 跨组合峰真伪:某峰出现过的参数组合数(真峰应在多个组合下都出现)
    def _support(key: tuple[float, ...]) -> int:
        return len(key_to_combos.get(key, set()))

    for entry in scanned:
        result = entry["result"]
        keys = entry["keys"]
        if n_combos > 1:
            true_keys = [k for k in keys if _support(k) >= cross_min]
        else:
            true_keys = list(keys)
        true_ratio = len(true_keys) / max(len(keys), 1) if keys else 0.0
        supports = [_support(k) for k in true_keys]
        cross = float(np.mean(supports)) if supports else 0.0
        if n_combos > 1 and cross > 0:
            cross_norm = min((cross - 1.0) / (n_combos - 1), 1.0)
        else:
            cross_norm = 1.0 if cross > 0 else 0.0
        true_set = set(true_keys)
        true_peaks = [
            p
            for p in result.stable_peaks
            if _snap_key(tuple(p["position"]), peak_tol_pts) in true_set
        ]
        overall, components = _score_candidate(
            [
                peak_detection.Peak(
                    position=tuple(p["position"]),
                    height=p["height"],
                    snr=p["snr"],
                )
                for p in true_peaks
            ],
            len(keys),
            cross_norm,
            entry["quality"],
        )
        components["stability"] = round(true_ratio, 3)
        result.overall = overall
        result.components = components
        result.decision = "accept" if overall >= 60.0 and true_peaks else "warning"
        result.n_combos = n_combos
        _dic, _data = _read_spectrum(result.spectrum_path)
        arr = np.asarray(_data)
        if np.iscomplexobj(arr):
            arr = arr.real
        global_sigma = float(noise.estimate(arr).global_sigma)
        result.stable_peaks = [
            _peak_confidence_entry(
                dict(peak),
                arr,
                global_sigma,
                n_combos,
                key_to_combos,
                key_heights,
                key_positions,
                peak_tol_pts,
            )
            for peak in true_peaks
        ]
        result.true_peak_count = sum(
            1 for p in result.stable_peaks if p["confidence"] >= true_conf_min
        )

    # 0.2.162-补9:真峰 = 可信度 ≥ true_conf_min(默认 55,即 A/B/C);
    # 最终保留真峰数最多的前 keep_top 个谱(并列按 overall)
    _rank_by_true_peaks(scanned, keep_top)

    results.sort(key=lambda r: (r.rank if r.rank > 0 else 999, -r.overall))
    return results

def scan_smile_parameters(
    experiment: Experiment,
    backend: Any,
    base_params: dict[str, Any] | None = None,
    *,
    scan_dir: Path | str,
    grid: list[dict[str, Any]] | None = None,
    grid_size: int = SMILE_GRID_DEFAULT,
    cross_min: int = 2,
    peak_tol_pts: float = 4.0,
    keep_top: int = 3,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """SMILE 参数扫描(0.2.199-补29hz-修3,用户方案)。

    以终跑脚本为模板**只换 SMILE 参数**:① 直接维跑一次得到切片文件;
    ② 每组参数跑一次「SMILE + 间接维」得到终谱;③ 立即取指标;④ 删除该谱
(候选谱只短暂存在于 scan_dir,通常位于内存盘)。

    指标(无需保留谱):检出峰数、跨参数组合稳定峰数(出现 ≥cross_min 组)、
    稳定峰平均 S/N、谱图综合质量分;排序按(稳定峰数, 平均 S/N, 质量分)。
    返回 {success, message, logs, rows(按名次), scripts({名次: 脚本文本}),
    scan_dir, n_combos}。
    """
    combos = list(grid) if grid is not None else smile_grid(grid_size)
    base = dict(base_params or {})
    est_group, est_total = estimate_scan_seconds(experiment, len(combos))
    started = time.time()
    _first_done: list[float] = []

    def _progress(index: int, total: int, message: str) -> None:
        if progress is None:
            return
        if index == 0:
            progress(
                index,
                total,
                f"{message} | 按数据规模估算约 {est_group:.0f}s/组、"
                f"合计约 {est_total / 60.0:.1f} 分钟(第一组完成后更新)",
            )
            return
        if index >= 2 and not _first_done:
            measured = time.time() - started
            _first_done.append(measured)
            remain = measured * max(0, total - index + 1)
            progress(
                index,
                total,
                f"{message} | 实测约 {measured:.0f}s/组,"
                f"预计剩余 {remain / 60.0:.1f} 分钟",
            )
            return
        progress(index, total, message)

    def _evaluate(path: str) -> dict[str, Any]:
        """候选谱评估:峰 + 质量分(此刻谱还在,评完即被删)。"""
        _dic, data = _read_spectrum(path)
        arr = np.asarray(data)
        if np.iscomplexobj(arr):
            arr = arr.real
        peaks = peak_detection.detect(arr)
        quality = spectrum_quality.evaluate(arr)
        # 0.2.199-补29hz-修4:综合分在 QualityResult.score.overall 上,
        # QualityResult 本身没有 overall(此前取值恒为 0)
        _qscore = getattr(quality, "score", None)
        return {
            "peak_count": len(peaks),
            "quality": float(getattr(_qscore, "overall", 0.0) or 0.0),
            "peaks": [
                {
                    "position": [float(v) for v in peak.position],
                    "height": float(peak.height),
                    "snr": float(peak.snr),
                }
                for peak in peaks
            ],
        }

    scan = backend.smile_scan(
        experiment,
        base,
        combos,
        work_dir=scan_dir,
        evaluate=_evaluate,
        progress=_progress,
    )
    if not scan.get("success"):
        raise RuntimeError(str(scan.get("message", "SMILE 扫描失败")))
    candidates = list(scan.get("candidates") or [])
    n_combos = len(candidates) or 1
    effective_cross = cross_min if n_combos >= cross_min else 1
    key_to_combos: dict[tuple[float, ...], set[int]] = defaultdict(set)
    keys_by_index: dict[int, set[tuple[float, ...]]] = {}
    for entry in candidates:
        metrics = dict(entry.get("metrics") or {})
        seen: set[tuple[float, ...]] = set()
        for peak in metrics.get("peaks") or []:
            key = _snap_key(tuple(peak["position"]), peak_tol_pts)
            seen.add(key)
            key_to_combos[key].add(int(entry["index"]))
        keys_by_index[int(entry["index"])] = seen
    rows: list[dict[str, Any]] = []
    for entry in candidates:
        metrics = dict(entry.get("metrics") or {})
        params = dict(entry.get("params") or {})
        stable = 0
        suspect = 0
        snr_values: list[float] = []
        for peak in metrics.get("peaks") or []:
            key = _snap_key(tuple(peak["position"]), peak_tol_pts)
            if len(key_to_combos.get(key, set())) < effective_cross:
                # 只在个别参数组合里出现的峰:疑伪峰
                suspect += 1
                continue
            stable += 1
            try:
                snr_values.append(float(peak.get("snr", 0.0) or 0.0))
            except (TypeError, ValueError):
                continue
        mean_snr = float(np.mean(snr_values)) if snr_values else 0.0
        quality = float(metrics.get("quality", 0.0) or 0.0)
        rows.append(
            {
                "index": int(entry["index"]),
                "nsigma": float(params.get("nsigma", 0.0) or 0.0),
                "thresh": float(params.get("thresh", 0.0) or 0.0),
                "peak_count": int(metrics.get("peak_count", 0) or 0),
                "stable_count": int(stable),
                "suspect_count": int(suspect),
                "net_peaks": int(stable - suspect),
                "mean_snr": round(mean_snr, 3),
                "quality": round(quality, 2),
                "holdout_rmse": float(metrics.get("holdout_rmse", 0.0) or 0.0),
                "holdout_corr": float(metrics.get("holdout_corr", 0.0) or 0.0),
                "smile_rms_ratio": float(
                    metrics.get("smile_rms_ratio", 0.0) or 0.0
                ),
                "composite": round(stable - suspect + 0.01 * mean_snr + 0.01 * quality, 3),
                "ok": bool(entry.get("ok")),
                "error": str(metrics.get("error", "") or ""),
                "_script": str(entry.get("script", "")),
            }
        )
    # 用户目标(2026-09-10):尽量少伪峰 + 尽量多真峰 → 先按「净真峰」
    # (稳定峰 − 疑伪峰),再按稳定峰数、平均 S/N、质量分
    rows.sort(
        key=lambda r: (
            r["net_peaks"],
            r["stable_count"],
            r["mean_snr"],
            -float(r.get("smile_rms_ratio", 0.0) or 0.0),  # 拟合残差越小越好
            r["quality"],
        ),
        reverse=True,
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    scripts = {
        int(row["rank"]): str(row.get("_script", ""))
        for row in rows[: max(1, int(keep_top))]
    }
    for row in rows:
        row.pop("_script", None)
    return {
        "success": True,
        "message": str(scan.get("message", "")),
        "logs": list(scan.get("logs") or []),
        "rows": rows,
        "scripts": scripts,
        "scan_dir": str(scan.get("scan_dir", scan_dir)),
        "n_combos": n_combos,
    }


def write_smile_scan_output(
    manager: Any,
    exp_id: str,
    data_id: str,
    rows: list[dict[str, Any]],
    scripts: dict[int, str],
) -> dict[str, str]:
    """写「参数组合排序表」(CSV+JSON)与前三名脚本(0.2.199-补29hz-修3)。

    排序表落 `<data>/smile_optimized/`;前三脚本落 `<data>/process/
    <data_id>_nus_rankN.com`(与终跑脚本同处,可直接运行)。
    返回 {csv, json, rank1, rank2, rank3}(缺项不出现)。
    """
    import csv
    import json

    out_dir = manager.data_dir(exp_id, data_id, "smile_optimized")
    out_dir.mkdir(parents=True, exist_ok=True)
    proc_dir = manager.data_dir(exp_id, data_id, "process")
    proc_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{exp_id}-{data_id}_smile_ranking.csv"
    json_path = out_dir / f"{exp_id}-{data_id}_smile_ranking.json"
    fields = [
        "rank", "index", "nsigma", "thresh", "net_peaks", "stable_count",
        "suspect_count", "peak_count",
        "mean_snr", "quality", "smile_rms_ratio", "holdout_rmse", "holdout_corr",
        "composite", "ok", "error",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})
    json_path.write_text(
        json.dumps({"rows": rows, "count": len(rows)}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    paths: dict[str, str] = {
        "csv": str(csv_path),
        "json": str(json_path),
    }
    for rank, script in sorted(scripts.items()):
        if not script or rank < 1 or rank > 3:
            continue
        target = proc_dir / f"{data_id}_nus_rank{rank}.com"
        target.write_text(script, encoding="utf-8", newline="\n")
        paths[f"rank{rank}"] = str(target)
    return paths

def write_smile_optimized_output(
    manager: Any,
    exp_id: str,
    data_id: str,
    spectrum_path: str,
    result: SmileParameterResult,
    rank: int = 1,
) -> tuple[Path, Path, Path]:
    """把稳定峰写为契约 §6 峰表 CSV + 参数/评分 JSON + 逐峰可靠性 JSON。

    rank>1 时文件名带 _top{rank} 后缀(0.2.162-补9:保留 Top-N 谱);
    smile_optimized/ 与 raw/ 同级(数据基座下)。返回
    (csv_path, json_path, reliability_path)。"""
    from workflow.pick_peaks import _axes_ppm

    dic, data = _read_spectrum(spectrum_path)
    arr = np.asarray(data)
    axes = _axes_ppm(dict(dic), arr)
    out_dir = manager.data_dir(exp_id, data_id, "smile_optimized")
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if rank <= 1 else f"_top{rank}"
    csv_path = out_dir / f"{exp_id}-{data_id}_smile_optimized{suffix}.csv"
    rows: list[dict[str, Any]] = []
    for i, peak in enumerate(result.stable_peaks, start=1):
        pos = [float(v) for v in peak.get("position", [])]
        row: dict[str, Any] = {
            "Peak_ID": i,
            "Intensity": float(peak.get("height", 0.0)),
            "SN": float(peak.get("snr", 0.0)),
            "label": "",
        }
        if arr.ndim == 2:
            row["H_shift"] = (
                _ppm_at_fraction(axes[1], pos[1])
                if len(axes) > 1 and len(pos) > 1
                else 0.0
            )
            row["N_shift"] = (
                _ppm_at_fraction(axes[0], pos[0]) if len(pos) > 0 else 0.0
            )
        else:
            for k in range(3):
                row[f"F{k + 1}_shift"] = (
                    _ppm_at_fraction(axes[k], pos[k])
                    if k < len(axes) and k < len(pos)
                    else 0.0
                )
        row["Reliability(%)"] = float(peak.get("confidence", 0.0) or 0.0)
        rows.append(row)
    # 0.2.199-补29ar:save_peaks 已改 Poky .list(用户峰表);SMILE 优化
    # 内部产物(含 Reliability 列,非用户峰表)仍写 CSV
    import csv as _csv

    if arr.ndim != 2:
        columns = [
            "Peak_ID", "F1_shift", "F2_shift", "F3_shift",
            "Intensity", "SN", "label", "Reliability(%)",
        ]
    else:
        columns = [
            "Peak_ID", "H_shift", "N_shift",
            "Intensity", "SN", "label", "Reliability(%)",
        ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = _csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in columns})
    json_path = out_dir / f"{exp_id}-{data_id}_smile_optimized{suffix}.json"
    json_path.write_text(
        json.dumps(
            {
                "params": result.params,
                "repeats": result.repeats,
                "n_combos": int(result.n_combos or 0),
                "decision": result.decision,
                "overall": result.overall,
                "components": result.components,
                "peak_count": len(result.stable_peaks),
                "spectrum_path": result.spectrum_path,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    reliability_path = out_dir / f"{exp_id}-{data_id}_smile_reliability{suffix}.json"
    rel_peaks: list[dict[str, Any]] = []
    for peak in result.stable_peaks:
        pos = [float(v) for v in peak.get("position", [])]
        shifts: dict[str, float] = {}
        if arr.ndim == 2:
            shifts["H_shift"] = (
                _ppm_at_fraction(axes[1], pos[1])
                if len(axes) > 1 and len(pos) > 1
                else 0.0
            )
            shifts["N_shift"] = (
                _ppm_at_fraction(axes[0], pos[0]) if len(pos) > 0 else 0.0
            )
        else:
            for k in range(3):
                shifts[f"F{k + 1}_shift"] = (
                    _ppm_at_fraction(axes[k], pos[k])
                    if k < len(axes) and k < len(pos)
                    else 0.0
                )
        rel_peaks.append(
            {
                "position_pts": pos,
                "shifts": shifts,
                "snr": float(peak.get("snr", 0.0) or 0.0),
                "support": int(peak.get("support", 0) or 0),
                "n_combos": int(result.n_combos or 0),
                "cross_rate": float(peak.get("cross_rate", 0.0) or 0.0),
                "snr_points": float(peak.get("snr_points", 0.0) or 0.0),
                "existence_points": float(
                    peak.get("existence_points", 0.0) or 0.0
                ),
                "intensity_cv": peak.get("intensity_cv"),
                "intensity_points": float(
                    peak.get("intensity_points", 0.0) or 0.0
                ),
                "position_shift": peak.get("position_shift"),
                "position_points": float(
                    peak.get("position_points", 0.0) or 0.0
                ),
                "stability_points": float(
                    peak.get("stability_points", 0.0) or 0.0
                ),
                "shape_points": float(peak.get("shape_points", 0.0) or 0.0),
                "local_noise_points": float(
                    peak.get("local_noise_points", 0.0) or 0.0
                ),
                "confidence": float(peak.get("confidence", 0.0) or 0.0),
                "grade": str(peak.get("grade", "") or ""),
                "flags": list(peak.get("flags", []) or []),
            }
        )
    reliability_path.write_text(
        json.dumps(
            {
                "schema": "smile_reliability_v3",
                "exp_id": exp_id,
                "data_id": data_id,
                "params": result.params,
                "n_combos": int(result.n_combos or 0),
                "peaks": rel_peaks,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return csv_path, json_path, reliability_path


def format_results(results: list[SmileParameterResult]) -> str:
    """把候选列表渲染为对齐的参数组合 + 稳定性评分表格。"""
    header = "{:>6} {:>6} {:>8} {:>7} {:>9} {:>6} {:>6} {:>6} {:>6}".format(
        "nSigma", "thresh", "decision", "overall", "stability", "cross", "snr",
        "peaks", "artifact"
    )
    lines = [header, "-" * len(header)]
    for result in results:
        comp = result.components
        lines.append(
            "{:>6} {:>6} {:>8} {:>7.1f} {:>9.2f} {:>6.2f} {:>6.1f} {:>6} {:>6.1f}".format(
                result.params.get("nsigma", 0),
                result.params.get("thresh", 0),
                result.decision,
                result.overall,
                comp.get("stability", 0),
                comp.get("cross_support", 0),
                comp.get("snr", 0),
                comp.get("peak_count", 0),
                comp.get("artifact", 0),
            )
        )
    return "\n".join(lines)


def save_report(results: list[SmileParameterResult], path: Path | str) -> Path:
    """保存参数组 + 评分报告(JSON)。"""
    out = Path(path)
    out.write_text(
        json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return out
