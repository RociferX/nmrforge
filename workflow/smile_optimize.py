"""SMILE 参数优化(可选,用户后选优化项;不进入自动处理流程)。

目标:在已有生成谱图参数(base_params)基础上只调整 SMILE 参数
(nSigma/thresh),尽可能保留真峰、剔除伪峰。两阶段:
  1) 网格扫描:各参数组合各重构 1 次(SMILE 为确定性算法,同参数同输入
     逐位一致,故扫描不做组内重复),跨组合统计每个峰出现的组合数评估
     真伪(真峰应在多个参数组合下都出现,伪峰只在个别组合出现);按
     跨组合支持/信噪比/峰数/伪影 综合评分选出最优参数。
  2) 重复去伪:对最优参数做多次重构(每次向输入 fid 注入小幅高斯噪声
     模拟测量噪声),组内稳定峰为最终保留峰,写入与 raw 同级的
     smile_optimized/ 目录。
逐峰可信度(0.2.162-补6,Peak Confidence Score 规范):
score = snr_points(0~40) + stability_points(-15~+40)
+ shape_points(-5~+5) + local_noise_points(-5~+5),clamp 0~100;
S/N 与 SMILE 重构稳定性为同等级一级核心证据;稳定性证据(出现率/
强度 CV/位置漂移)来自 nSigma×thresh 双参数网格(保持现有网格)下
的多次重构;同峰判定容差每轴 4 点(VM 实测真峰位移 p95 约 4 点)。

用法:
    results = optimize_smile_parameters(experiment, backend, base_params=run_params)
    print(format_results(results))
    save_report(results, Path("smile_optimize_report.json"))
"""

from __future__ import annotations

import json
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
    repeats: int = 3
    n_combos: int = 0
    decision: str = ""
    overall: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    spectrum_path: str = ""
    stable_peaks: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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


def _snap_key(position: tuple[float, ...], tol_pts: float) -> tuple[float, ...]:
    """峰位置快照键:按容差网格取整,供组内/跨组合匹配。"""
    if tol_pts > 0:
        return tuple(round(float(v) / tol_pts) * tol_pts for v in position)
    return tuple(round(float(v), 3) for v in position)


def _match_stable_peaks(
    peak_sets: list[list[peak_detection.Peak]],
    min_stability: int,
    tol_pts: float,
) -> tuple[list[peak_detection.Peak], int, dict[tuple[float, ...], int]]:
    """组内跨重构匹配峰;返回(稳定峰, 去重后总峰数, 键→出现次数)。"""
    keys: dict[tuple[float, ...], int] = {}
    best: dict[tuple[float, ...], peak_detection.Peak] = {}
    for peaks in peak_sets:
        matched: set[tuple[float, ...]] = set()
        for peak in peaks:
            key = _snap_key(peak.position, tol_pts)
            if key in matched:
                continue
            keys[key] = keys.get(key, 0) + 1
            matched.add(key)
            if key not in best or peak.height > best[key].height:
                best[key] = peak
    stable = [best[k] for k, count in keys.items() if count >= min_stability]
    stable.sort(key=lambda p: p.height, reverse=True)
    return stable, len(keys), keys


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
    """等级:A≥85,B 70-84,C 55-69,D 40-54,E<40。"""
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
    """score = snr + stability + shape + local_noise,clamp 0~100;返回(分, 等级)。"""
    confidence = round(
        max(
            0.0,
            min(
                100.0,
                snr_points + stability_points + shape_points + local_noise_points,
            ),
        ),
        1,
    )
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


def optimize_smile_parameters(
    experiment: Experiment,
    backend: Any,
    base_params: dict[str, Any] | None = None,
    grid: list[dict[str, Any]] | None = None,
    *,
    final_repeats: int = 3,
    min_stability: int = 2,
    cross_min: int = 2,
    peak_tol_pts: float = 4.0,
    fid_noise: float = 0.15,
    progress: Callable[[int, int, str], None] | None = None,
    on_result: Callable[[SmileParameterResult], None] | None = None,
) -> list[SmileParameterResult]:
    """两阶段:网格扫描(每组 1 次重构,跨组合评估峰真伪)选优 → 最优参数重复去伪峰。

    SMILE 为确定性算法,同参数同输入逐位一致,故扫描阶段不做组内重复;
    峰真伪由跨参数组合出现数评估(真峰应在多个组合下都出现)。最终阶段
    对最优参数多次重构(每次注入小幅 fid 噪声模拟测量噪声),组内稳定峰
    为保留峰;逐峰可信度按 Peak Confidence Score 规范四分量评分:
    snr(0~40) + 重构稳定性(-15~+40) + 峰形(-5~+5) + 局部噪声(-5~+5),
    clamp 0~100(0.2.162-补6)。peak_tol_pts 为同峰判定容差(每轴点数,
    0.2.162-补5 由 2 放宽到 4)。backend 需提供 reconstruct_nus
    (experiment, params);每组保留 base_params 的非 SMILE 参数,只覆盖
    nsigma/thresh 与 fid_noise/seed。"""
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

    best_entry: dict[str, Any] | None = None
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
        result.stable_peaks = []
        for peak in true_peaks:
            support = _support(_snap_key(tuple(peak["position"]), peak_tol_pts))
            result.stable_peaks.append(
                {
                    **dict(peak),
                    "support": support,
                    "reliability": (
                        round(support / n_combos * 100.0, 1) if n_combos else 0.0
                    ),
                }
            )
        if best_entry is None or overall > best_entry["overall"]:
            best_entry = dict(entry=entry, overall=overall)

    # 第二阶段:对最优参数多次重复重构(注入噪声)去伪峰,确定最终保留峰
    if best_entry is not None:
        best = best_entry["entry"]["result"]
        best_params = best.params
        try:
            peak_sets: list[list[peak_detection.Peak]] = []
            last_spec = ""
            for repeat in range(final_repeats):
                if progress is not None:
                    progress(
                        repeat + 1,
                        final_repeats,
                        f"去伪重复 {repeat + 1}/{final_repeats}: {best_params}",
                    )
                run_params = {
                    **base,
                    **best_params,
                    "direct_phase_search": False,
                    "display_phase_search": False,
                    "fid_noise": float(fid_noise or 0.0),
                    "fid_noise_seed": 9000 + repeat,
                }
                resp = backend.reconstruct_nus(experiment, run_params)
                if not resp.get("success"):
                    break
                last_spec = str(resp["spectrum_path"])
                peak_sets.append(_detect_peaks(last_spec))
            if last_spec:
                stable, _union, _keys = _match_stable_peaks(
                    peak_sets, min_stability, peak_tol_pts
                )
                _dic, _data = _read_spectrum(last_spec)
                arr = np.asarray(_data)
                if np.iscomplexobj(arr):
                    arr = arr.real
                global_sigma = float(noise.estimate(arr).global_sigma)
                best.stable_peaks = []
                for peak in stable:
                    key = _snap_key(tuple(peak.position), peak_tol_pts)
                    support = len(key_to_combos.get(key, set()))
                    rate = support / n_combos if n_combos else 0.0
                    heights = list(key_heights.get(key, []))
                    positions = list(key_positions.get(key, []))
                    snr_pts = _snr_points(float(peak.snr))
                    existence_pts = _existence_points(rate)
                    cv = None
                    if len(heights) >= 2:
                        mean_h = float(np.mean(heights))
                        if mean_h > 0:
                            cv = float(np.std(heights)) / mean_h
                    intensity_pts = _intensity_cv_points(cv)
                    pos_i = tuple(int(v) for v in peak.position)
                    shift = _position_shift(positions, arr, pos_i)
                    position_pts = _position_shift_points(shift)
                    stability_pts = existence_pts + intensity_pts + position_pts
                    shape_pts = _shape_score(arr, pos_i)
                    local_pts = _local_noise_score(arr, pos_i, global_sigma)
                    confidence, grade = _compose_confidence(
                        snr_pts, stability_pts, shape_pts, local_pts
                    )
                    flags = _confidence_flags(
                        float(peak.snr), rate, cv, shift, shape_pts, local_pts
                    )
                    best.stable_peaks.append(
                        {
                            "position": [float(v) for v in peak.position],
                            "height": float(peak.height),
                            "snr": float(peak.snr),
                            "support": support,
                            "cross_rate": round(rate, 3),
                            "snr_points": round(snr_pts, 1),
                            "existence_points": round(existence_pts, 1),
                            "intensity_cv": (
                                round(cv, 3) if cv is not None else None
                            ),
                            "intensity_points": round(intensity_pts, 1),
                            "position_shift": (
                                round(shift, 3) if shift is not None else None
                            ),
                            "position_points": round(position_pts, 1),
                            "stability_points": round(stability_pts, 1),
                            "shape_points": round(shape_pts, 1),
                            "local_noise_points": round(local_pts, 1),
                            "confidence": confidence,
                            "grade": grade,
                            "flags": flags,
                        }
                    )
                best.n_combos = n_combos
                best.spectrum_path = last_spec
                best.repeats = final_repeats
                best.overall = min(best.overall, 99.0) if stable else best.overall
                best.decision = "accept" if stable else "warning"
                best.components["peak_count"] = len(stable)
        except Exception as exc:  # noqa: BLE001
            best.message = f"最终去伪失败: {exc}"

    results.sort(key=lambda r: r.overall, reverse=True)
    return results

def write_smile_optimized_output(
    manager: Any,
    exp_id: str,
    data_id: str,
    spectrum_path: str,
    result: SmileParameterResult,
) -> tuple[Path, Path, Path]:
    """把稳定峰写为契约 §6 峰表 CSV + 参数/评分 JSON + 逐峰可靠性 JSON。

    smile_optimized/ 与 raw/ 同级(数据基座下)。返回
    (csv_path, json_path, reliability_path)。"""
    from core.peaks.peak_table import save_peaks
    from workflow.pick_peaks import _axes_ppm

    dic, data = _read_spectrum(spectrum_path)
    arr = np.asarray(data)
    axes = _axes_ppm(dict(dic), arr)
    out_dir = manager.data_dir(exp_id, data_id, "smile_optimized")
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{exp_id}-{data_id}_smile_optimized.csv"
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
                float(axes[1][int(pos[1])])
                if len(axes) > 1 and len(pos) > 1
                else 0.0
            )
            row["N_shift"] = (
                float(axes[0][int(pos[0])]) if len(pos) > 0 else 0.0
            )
        else:
            for k in range(3):
                row[f"F{k + 1}_shift"] = (
                    float(axes[k][int(pos[k])])
                    if k < len(axes) and k < len(pos)
                    else 0.0
                )
        row["Reliability(%)"] = float(peak.get("confidence", 0.0) or 0.0)
        rows.append(row)
    save_peaks(csv_path, rows, extra_columns=("Reliability(%)",))
    json_path = out_dir / f"{exp_id}-{data_id}_smile_optimized.json"
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
    reliability_path = out_dir / f"{exp_id}-{data_id}_smile_reliability.json"
    rel_peaks: list[dict[str, Any]] = []
    for peak in result.stable_peaks:
        pos = [float(v) for v in peak.get("position", [])]
        shifts: dict[str, float] = {}
        if arr.ndim == 2:
            shifts["H_shift"] = (
                float(axes[1][int(pos[1])])
                if len(axes) > 1 and len(pos) > 1
                else 0.0
            )
            shifts["N_shift"] = (
                float(axes[0][int(pos[0])]) if len(pos) > 0 else 0.0
            )
        else:
            for k in range(3):
                shifts[f"F{k + 1}_shift"] = (
                    float(axes[k][int(pos[k])])
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
