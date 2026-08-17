"""内存相位搜索引擎(统一方案,2026-08-17)。

把 workflow.phase_optimize.optimize_phase_sequential 的逐轴搜索/门控/联合
复核原样搬到内存:候选谱不再由后端生成,而是对第一遍复型预览数据做频域
旋转取实部,再用同一套「固定迹线中位数净吸收」评分判断。

- 评分:50 × (锁定迹线 ±5 窗口净吸收中位数 + 1),与旧方案同源;
- 搜索:粗网格 p0 0-330° 步 30°(p1=0)→ 1/3 递减细化到 5°(p1 固定)→
  平台圆中位数 p0 亚度精修 → ±90° 对称性消歧 → 末尾 p1 {0,±22.5};
- 门控:评分余量 <0.05 → 可复现性(奇偶子采样)→ 三级回退;
- 联合复核:各轴 p1 ±5° 组合 + 全零,逐轴固定迹线评分取均值。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from workflow.phase_optimize import (
    P1_REFINE_MIN_GAIN,
    PHASE_PLATEAU_TOL,
    PHASE_REPRODUCIBILITY_TOL,
    PHASE_SCORE_FLAT_MARGIN,
    PHASE_SYMMETRY_TOL,
    _grid_step,
    _refine_steps,
    _refine_window,
    _trace_indices_fixed,
)


def rotate_real(
    complex_arr: np.ndarray, axis: int, p0: float, p1: float
) -> np.ndarray:
    """内存频域旋转取实部,与 nmrPipe PS 同约定:
    phase(k) = p0 + p1·k/(n-1),逐点复乘 exp(i·phase),取实部(= -di 语义)。
    """
    arr = np.asarray(complex_arr, dtype=np.complex128)
    n = arr.shape[axis]
    k = np.arange(n, dtype=float)
    ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
    shape = [1] * arr.ndim
    shape[axis] = n
    return np.real(arr * ramp.reshape(shape))


def _window_nets(
    real: np.ndarray, axis: int, indices: list[int], positions: list[int]
) -> list[float]:
    """每窗签名净吸收 (pos+neg)/total(与旧 _window_metric 同公式)。"""
    moved = np.moveaxis(real, axis, -1)
    traces = moved.reshape(-1, moved.shape[-1])
    nets: list[float] = []
    for index, peak in zip(indices, positions):
        if index < 0 or index >= traces.shape[0]:
            continue
        profile = traces[index, max(0, peak - 5) : peak + 6]
        positive = float(np.clip(profile, 0.0, None).sum())
        negative = float(np.clip(profile, None, 0.0).sum())
        total = float(np.abs(profile).sum())
        nets.append((positive + negative) / total if total else 0.0)
    return nets


def score_axis_memory(
    complex_arr: np.ndarray,
    axis: int,
    p0: float,
    p1: float,
    indices: list[int],
    positions: list[int],
    *,
    sign_mode: str = "uniform",
) -> float:
    """候选相位评分(0-100)。

    sign_mode="mixed"(HNCACB 等正负峰共存):吸收度 = |各窗净吸收| 的
    中位数——正峰/负峰都能得高分;再要求强弱峰正负共存(缺一种符号
    惩罚 ×0.7),避免色散/全同号假解。
    sign_mode="uniform"(HSQC/CBCACONH 等同号峰):保留旧签名净吸收中位数
    (正峰偏好消解 ±180 歧义)。
    """
    real = rotate_real(complex_arr, axis, p0, p1)
    nets = _window_nets(real, axis, indices, positions)
    if not nets:
        return 50.0
    if sign_mode == "mixed":
        score = 50.0 * (float(np.median(np.abs(nets))) + 1.0)
        strong = [n for n in nets if abs(n) > 0.35]
        if strong:
            pos = sum(1 for n in strong if n > 0)
            neg = sum(1 for n in strong if n < 0)
            if pos >= 1 and neg >= 1:
                return score
            return score * 0.7
        return score
    median = float(np.median(nets))
    return 50.0 * (median + 1.0)


def _lock_discrete_traces(
    complex_arr: np.ndarray,
    axis: int,
    *,
    prominence_min: float = 2.5,
    window: int = 10,
    max_duty: float = 0.5,
    threshold_pct: float = 95.0,
) -> tuple[list[int], list[int]]:
    """锁定「离散峰」迹线用于调相评分。

    与人工 nmrDraw「挑离散峰调相」一致:阈值取低分位(默认 95,中央大团会
    抬高 99.5 分位把离散峰淘汰);再用峰形尖锐性过滤——峰位 ±window 内
    ≥半高点的占比(duty)≤ max_duty 且峰顶相对局部背景低分位显著
    (prominence)。中央混杂峰团(密集峰簇)占窗口比例大、被排除;无离散峰时
    逐级降阈值,最后回退全部迹线。
    """
    real0 = np.real(complex_arr)
    moved = np.moveaxis(real0, axis, -1)
    traces = moved.reshape(-1, moved.shape[-1])
    n = moved.shape[-1]
    noise = float(np.std(real0[:80, :40])) if real0.size else 0.0
    for pct in (threshold_pct, 75.0):
        threshold = max(float(np.percentile(real0, pct)), noise * 5.0)
        indices: list[int] = []
        positions: list[int] = []
        for i in range(traces.shape[0]):
            mag = np.abs(traces[i])
            peak = int(np.argmax(mag))
            if float(mag[peak]) <= threshold:
                continue
            half = 0.5 * float(mag[peak])
            lo = max(0, peak - window)
            hi = min(n, peak + window + 1)
            seg = mag[lo:hi]
            if seg.size == 0:
                continue
            duty = float(np.mean(seg >= half))
            if duty > max_duty:
                continue
            bg = float(np.percentile(seg, 25.0))
            if float(mag[peak]) / (bg + 1e-12) >= prominence_min:
                indices.append(i)
                positions.append(peak)
        if indices:
            return indices, positions
    return _trace_indices_fixed(real0, axis, -1.0)


def _subsampled_score_memory(
    real: np.ndarray, axis: int, k: int = 500, group: str = "even"
) -> float:
    """沿 axis 取峰高 top-K 强迹线的半组子采样,内存内评估 phase_quality
    (与旧方案 _subsampled_score 同指标,只是输入是内存数组)。"""
    from core.qc import phase_quality

    moved = np.moveaxis(real, axis, -1)
    traces = moved.reshape(-1, moved.shape[-1])
    peak_mag = np.max(np.abs(traces), axis=-1)
    order = np.argsort(peak_mag)[::-1][:k]
    selected = order[0::2] if group == "even" else order[1::2]
    if selected.size == 0:
        return 0.0
    return float(phase_quality.evaluate(traces[selected]).score)


def _symmetry_memory(real: np.ndarray, axis: int) -> float:
    """±90° 消歧用对称性指标(与旧方案 phase_quality.profile_symmetry_axis
    相同)。"""
    from core.qc import phase_quality

    return float(phase_quality.profile_symmetry_axis(real, axis))


@dataclass
class MemoryAxisResult:
    """一个维度的内存相位搜索结果。"""

    axis: int
    phase: tuple[float, float]
    score: float
    coarse_best: tuple[float, float]
    coarse_margin: float
    flat: bool
    traces: tuple[list[int], list[int]] = field(default_factory=lambda: ([], []))
    score_map: dict[tuple[float, float], float] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)


def search_axis_memory(
    complex_arr: np.ndarray,
    axis: int,
    *,
    p0_values: tuple[float, ...] = tuple(float(v) for v in range(0, 360, 30)),
    final_step: float = 5.0,
    refine: bool = True,
    sign_mode: str = "uniform",
) -> MemoryAxisResult | None:
    """在复型数据的指定轴上做内存相位搜索(旧算法判断标准,零后端)。"""
    arr = np.asarray(complex_arr, dtype=np.complex128)
    n = arr.shape[axis]
    if arr.ndim < 2 or n < 8:
        return None
    logs: list[str] = []
    scored: dict[tuple[float, float], float] = {}

    def _score(p0: float, p1: float) -> float:
        return score_axis_memory(
            arr, axis, p0, p1, trace_indices, trace_positions, sign_mode=sign_mode
        )

    # 基线 (0,0) 锁定「离散峰」迹线(阈值同旧方案,另按峰位显著性过滤
    # 中央混杂峰团,VM sampleB 校准:混杂大团会带偏相位,离散峰可调到
    # F2=(90,0)/F1=(0,0))
    trace_indices, trace_positions = _lock_discrete_traces(arr, axis)
    if not trace_indices:
        trace_indices, trace_positions = _trace_indices_fixed(
            np.real(arr), axis, -1.0
        )
    if not trace_indices:
        return None

    def _run_batch(phases: list[tuple[float, float]]) -> None:
        for raw in phases:
            phase = (float(raw[0]) % 360.0, float(raw[1]))
            if phase in scored:
                continue
            scored[phase] = _score(phase[0], phase[1])

    _run_batch([(0.0, 0.0)])
    p0_step = _grid_step(tuple(p0_values))
    coarse = [(float(p0), 0.0) for p0 in p0_values]
    _run_batch(coarse)
    if not scored:
        return None
    coarse_done = [p for p in coarse if p in scored]
    coarse_sorted = sorted(coarse_done, key=lambda p: -scored[p])
    coarse_best = coarse_sorted[0]
    coarse_best_score = scored[coarse_best]
    p0_rivals = [
        s
        for p, s in scored.items()
        if p in coarse_done
        and abs((p[0] - coarse_best[0] + 180.0) % 360.0 - 180.0) > 1e-6
    ]
    coarse_margin = coarse_best_score - max(p0_rivals) if p0_rivals else 0.0
    steps0 = (
        _refine_steps(p0_step, final_step) if refine and p0_step > 0 else []
    )
    levels = len(steps0)
    if levels:
        prev0 = p0_step
        for level in range(levels):
            s0 = steps0[level]
            best = max(scored, key=lambda p: scored[p])
            w0 = _refine_window(best[0], prev0, s0)
            _run_batch([(p0, 0.0) for p0 in w0])
            prev0 = s0
    best_phase = max(scored, key=lambda p: scored[p])
    best_score = scored[best_phase]
    neighbor_scores = [
        s
        for p, s in scored.items()
        if p != best_phase and abs(p[1] - best_phase[1]) <= final_step
    ]
    flat = False
    if neighbor_scores:
        margin = best_score - max(neighbor_scores)
        if margin < PHASE_SCORE_FLAT_MARGIN:
            flat = True
            logs.append(
                f"轴{axis}: 相位评分余量 {margin:.2f} 分"
                f"(<{PHASE_SCORE_FLAT_MARGIN:g}),评分面平坦"
            )
        else:
            logs.append(f"轴{axis}: 相位评分余量 {margin:.2f} 分,最优较明确")
    if not flat:
        neighbor_phases = [
            p for p in scored if abs(p[1] - best_phase[1]) <= final_step
        ]
        if len(neighbor_phases) >= 2:
            p1s: list[float] = []
            for group in ("even", "odd"):
                best_group: tuple[float, float] | None = None
                best_s = -1.0
                for p in neighbor_phases:
                    real = rotate_real(arr, axis, p[0], p[1])
                    s = _subsampled_score_memory(real, axis, group=group)
                    if s > best_s:
                        best_s, best_group = s, p
                p1s.append(float(best_group[1]) if best_group is not None else 0.0)
            if abs(p1s[0] - p1s[1]) > PHASE_REPRODUCIBILITY_TOL:
                flat = True
                logs.append(
                    f"轴{axis}: 可复现性检查未通过(top-K 子采样最优 p1 "
                    f"{p1s[0]:g}/{p1s[1]:g},差>{PHASE_REPRODUCIBILITY_TOL:g}°),"
                    f"回退 (0,0)"
                )
    if flat:
        zero_score = scored.get((0.0, 0.0))
        if coarse_best == (0.0, 0.0) and zero_score is not None:
            best_phase = (0.0, 0.0)
            best_score = zero_score
            logs.append(f"轴{axis}: 已回退 (0,0)(粗网格最优为零)")
        elif coarse_margin >= PHASE_SCORE_FLAT_MARGIN:
            if best_phase != coarse_best:
                logs.append(
                    f"轴{axis}: 细网格评分平坦,采用粗网格最优 {coarse_best} "
                    f"(粗 margin={coarse_margin:.2f} 分)"
                )
            else:
                logs.append(
                    f"轴{axis}: 细网格评分平坦,保持粗网格最优 {coarse_best} "
                    f"(粗 margin={coarse_margin:.2f} 分)"
                )
            best_phase = coarse_best
            best_score = scored[coarse_best]
        elif zero_score is not None and coarse_best_score - zero_score < PHASE_SCORE_FLAT_MARGIN:
            best_phase = (0.0, 0.0)
            best_score = zero_score
            logs.append(f"轴{axis}: 已回退 (0,0)(粗网格平坦且零相位不劣于最优)")
        else:
            best_phase = coarse_best
            best_score = scored[coarse_best]
            logs.append(f"轴{axis}: 粗网格 p0 平坦但最优显著优于零相位,采用粗网格最优")
    if refine:
        # 平台圆中位数 p0(亚度精修,旧方案默认评分路径)
        plateau_p0 = [
            p[0] for p, s in scored.items() if s >= best_score - PHASE_PLATEAU_TOL
        ]
        if len(plateau_p0) >= 2:
            angles = np.deg2rad(plateau_p0)
            center = float(
                np.rad2deg(
                    np.arctan2(np.mean(np.sin(angles)), np.mean(np.cos(angles)))
                )
            )
            center = center % 360.0
            refined = (center, best_phase[1])
            if (
                abs((center - best_phase[0] + 180.0) % 360.0 - 180.0) > 0.5
                and refined not in scored
            ):
                _run_batch([refined])
            if refined in scored:
                r_score = scored[refined]
                if r_score >= best_score - PHASE_PLATEAU_TOL:
                    best_phase, best_score = refined, r_score
                    logs.append(
                        f"轴{axis}: 平台圆中位数 p0 → {center:.2f}° "
                        f"(score={r_score:.2f})"
                    )
        # ±90° 对称性消歧(旧方案 NMRFlow)
        best_real = rotate_real(arr, axis, best_phase[0], best_phase[1])
        best_sym = _symmetry_memory(best_real, axis)
        for offset in (90.0, -90.0):
            cand = (best_phase[0] + offset, best_phase[1])
            if cand not in scored:
                _run_batch([cand])
            if cand in scored:
                c_score = scored[cand]
                if c_score >= best_score - PHASE_SYMMETRY_TOL:
                    c_sym = _symmetry_memory(
                        rotate_real(arr, axis, cand[0], cand[1]), axis
                    )
                    if c_sym > best_sym + 0.05:
                        logs.append(
                            f"轴{axis}: ±90° 对称性消歧 {best_phase} → {cand} "
                            f"(sym {best_sym:.2f}→{c_sym:.2f})"
                        )
                        best_phase, best_score = cand, c_score
                        best_sym = c_sym
        # p1 精修 {0, ±22.5}(饱和 100 分短路)
        p1_refine_candidates = [0.0, 22.5, -22.5]
        if best_score >= 100.0 - 1e-9:
            p1_refine_candidates = []
            logs.append(f"轴{axis}: 评分饱和(100 分),跳过 p1 精修")
        for p1 in p1_refine_candidates:
            cand = (best_phase[0] % 360.0, p1)
            if cand not in scored:
                _run_batch([cand])
            if cand in scored:
                c_score = scored[cand]
                if c_score >= best_score + P1_REFINE_MIN_GAIN:
                    logs.append(
                        f"轴{axis}: p1 精修 {best_phase[1]:g}° → {p1:g}° "
                        f"(score={c_score:.2f})"
                    )
                    best_phase, best_score = cand, c_score
    return MemoryAxisResult(
        axis=axis,
        phase=(float(best_phase[0]), float(best_phase[1])),
        score=float(best_score),
        coarse_best=(float(coarse_best[0]), float(coarse_best[1])),
        coarse_margin=float(coarse_margin),
        flat=flat,
        traces=(list(trace_indices), list(trace_positions)),
        score_map=dict(scored),
        logs=logs,
    )


def joint_recheck_memory(
    axis_arrays: dict[str, np.ndarray],
    axis_index: dict[str, int],
    axis_traces: dict[str, tuple[list[int], list[int]]],
    fixed: dict[str, tuple[float, float]],
    *,
    final_step: float = 5.0,
    sign_mode: str = "uniform",
) -> tuple[dict[str, tuple[float, float]], float, float, float]:
    """联合 ±final_step 邻域复核(内存版):各轴 p1 3 值组合 + 全零,
    逐轴在各自复型数组上旋转评分取均值(与旧方案 trace_map 同基准)。

    返回 (最优 phases, 最优 score, 固定组合 score, 全零组合 score)。
    """
    import itertools

    search_axes = [a for a in fixed]
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

    def _score_combo(phases: dict[str, tuple[float, float]]) -> float:
        vals: list[float] = []
        for axis in search_axes:
            idx, pos = axis_traces.get(axis, ([], []))
            if not idx or axis not in axis_arrays:
                continue
            p0, p1 = phases[axis]
            vals.append(
                score_axis_memory(
                    axis_arrays[axis],
                    axis_index[axis],
                    p0,
                    p1,
                    idx,
                    pos,
                    sign_mode=sign_mode,
                )
            )
        return float(np.mean(vals)) if vals else -1.0

    best_phases = dict(fixed)
    best_score = -1.0
    fixed_score = -1.0
    zero_score = -1.0
    for phases in combos:
        s = _score_combo(phases)
        if phases == fixed:
            fixed_score = s
        if phases == all_zero:
            zero_score = s
        if s > best_score:
            best_score, best_phases = s, dict(phases)
    return best_phases, best_score, fixed_score, zero_score
