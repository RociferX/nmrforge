"""内存相位搜索引擎(统一方案,2026-08-17)。

把旧 optimize_phase_sequential 的逐轴搜索/门控/联合
复核原样搬到内存:候选谱不再由后端生成,而是对第一遍复型预览数据做频域
旋转取实部,再用同一套「固定迹线中位数净吸收」评分判断。

- 评分:50 × (锁定迹线 ±5 窗口净吸收中位数 + 1),与旧方案同源;
- 搜索:粗网格 p0 0-330° 步 30°(p1=0)→ 1/3 递减细化到 5°(p1 固定)→
  平台圆中位数 p0 亚度精修 → ±90° 对称性消歧 → 末尾 p1 {0,±22.5};
- 门控:评分余量 <0.05 → 可复现性(奇偶子采样)→ 三级回退;
- 联合复核:各轴 p1 ±5° 组合 + 全零,逐轴固定迹线评分取均值。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

# 相位评分常量与网格辅助函数(0.2.164 从 workflow.phase_optimize 迁入;
# 旧暴力优化模块已删除,内存搜索为唯一实现)。
PHASE_SCORE_FLAT_MARGIN = 0.05
PHASE_REPRODUCIBILITY_TOL = 10.0
PHASE_PLATEAU_TOL = 1.0
PHASE_SYMMETRY_TOL = 2.5


def _axis_traces(real: np.ndarray, axis: int) -> np.ndarray:
    """把谱沿 axis 展开为 (n_trace, axis_len),任意维度通用。"""
    moved = np.moveaxis(np.asarray(real, dtype=float), axis, -1)
    return moved.reshape(-1, moved.shape[-1])


def _trace_indices_fixed(
    real: np.ndarray, axis: int, threshold: float = 0.0
) -> tuple[list[int], list[int]]:
    """返回沿 axis 的信号迹线下标及每条迹线最强点位置(阈值过滤)。"""
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


def rotate_real(
    complex_arr: np.ndarray, axis: int, p0: float, p1: float
) -> np.ndarray:
    """内存频域旋转取实部,与 nmrPipe PS 同约定:
    phase(k) = p0 + p1·k/(n-1),逐点复乘 exp(i·phase),取实部(= -di 语义)。
    """
    arr = np.asarray(complex_arr, dtype=np.complex128)
    # 0.2.199-补29dp:p1=0 时相位与 k 无关,标量旋转(避免整轴 ramp 数组
    # 逐点复乘——3D NUS 大数组 ~50 次候选评分的主要开销)
    if abs(p1) < 1e-9:
        c = float(np.cos(np.deg2rad(p0)))
        s = float(np.sin(np.deg2rad(p0)))
        return arr.real * c - arr.imag * s
    n = arr.shape[axis]
    k = np.arange(n, dtype=float)
    ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
    shape = [1] * arr.ndim
    shape[axis] = n
    return np.real(arr * ramp.reshape(shape))


def _window_nets(
    real: np.ndarray,
    axis: int,
    indices: list[int],
    positions: list[int],
    *,
    half_width: int | None = None,
) -> list[float]:
    """每窗签名净吸收 (pos+neg)/total(与旧 _window_metric 同公式)。

    正负判定先拉平基线——以峰两侧基线区中位数均值(±6..18 点)为基线
    水平,窗口减基线后再分正负;基线整体偏移(正或负)不再污染净吸收
    (0.2.175 用户方案)。
    half_width(点)可调窗口半宽(0.2.199-补29dn,方案B测试):缺省 5 与旧
    行为一致;按线宽/分辨率归一化(高分辨率窗口更大)可消除「低分辨率下
    ±5 点覆盖过大线宽导致相位最优偏移」(sampleI F1 在 431 点预览 105°、
    1024 点终谱最优 90°)。
    """
    hw = 5 if half_width is None else max(int(half_width), 1)
    moved = np.moveaxis(real, axis, -1)
    traces = moved.reshape(-1, moved.shape[-1])
    nets: list[float] = []
    for index, peak in zip(indices, positions):
        if index < 0 or index >= traces.shape[0]:
            continue
        n = traces.shape[1]
        left_base = traces[index, max(0, peak - 3 * hw - 3) : max(0, peak - hw - 1)]
        right_base = traces[
            index, min(n, peak + hw + 2) : min(n, peak + 3 * hw + 4)
        ]
        if left_base.size >= 4 and right_base.size >= 4:
            baseline = 0.5 * (
                float(np.median(left_base)) + float(np.median(right_base))
            )
        else:
            baseline = float(np.median(traces[index]))
        profile = traces[index, max(0, peak - hw) : peak + hw + 1]
        peak_h = float(np.max(np.abs(profile)))
        # 基线偏移相对峰高显著(≥1%)才拉平;基线平的谱保持零界
        if peak_h > 1e-12 and abs(baseline) / peak_h >= 0.01:
            profile = profile - baseline
        positive = float(np.clip(profile, 0.0, None).sum())
        negative = float(np.clip(profile, None, 0.0).sum())
        total = float(np.abs(profile).sum())
        nets.append((positive + negative) / total if total else 0.0)
    return nets


def _window_nets_from_rows(
    rows: np.ndarray,
    positions: list[int],
    *,
    half_width: int | None = None,
) -> list[float]:
    """锁定迹线行(rows:(n_trace, n))的每行净吸收,与 _window_nets 同公式
    (0.2.199-补29dp)。"""
    hw = 5 if half_width is None else max(int(half_width), 1)
    nets: list[float] = []
    for row, peak in enumerate(positions):
        trace = rows[row]
        n = trace.shape[0]
        left_base = trace[max(0, peak - 3 * hw - 3) : max(0, peak - hw - 1)]
        right_base = trace[
            min(n, peak + hw + 2) : min(n, peak + 3 * hw + 4)
        ]
        if left_base.size >= 4 and right_base.size >= 4:
            baseline = 0.5 * (
                float(np.median(left_base)) + float(np.median(right_base))
            )
        else:
            baseline = float(np.median(trace))
        profile = trace[max(0, peak - hw) : peak + hw + 1]
        peak_h = float(np.max(np.abs(profile)))
        if peak_h > 1e-12 and abs(baseline) / peak_h >= 0.01:
            profile = profile - baseline
        positive = float(np.clip(profile, 0.0, None).sum())
        negative = float(np.clip(profile, None, 0.0).sum())
        total = float(np.abs(profile).sum())
        nets.append((positive + negative) / total if total else 0.0)
    return nets


def score_locked_memory(
    rows: np.ndarray,
    positions: list[int],
    p0: float,
    p1: float,
    *,
    sign_mode: str = "uniform",
    net_half_width: int | None = None,
) -> float:
    """锁定迹线行评分(0.2.199-补29dp):只旋转锁定行,不再每次候选对全
    数组做复型旋转(3D NUS (256,256,586) 每轴搜索 23-25s → 亚秒级)。"""
    real = rotate_real(rows, -1, p0, p1)
    nets = _window_nets_from_rows(
        real, positions, half_width=net_half_width
    )
    if not nets:
        return 50.0
    if sign_mode == "mixed":
        score = 50.0 * (float(np.median(np.abs(nets))) + 1.0)
        strong = [nn for nn in nets if abs(nn) > 0.35]
        if strong:
            pos = sum(1 for nn in strong if nn > 0)
            neg = sum(1 for nn in strong if nn < 0)
            if pos >= 1 and neg >= 1:
                return score
            return score * 0.7
        return score
    return 50.0 * (float(np.median(nets)) + 1.0)


def score_axis_memory(
    complex_arr: np.ndarray,
    axis: int,
    p0: float,
    p1: float,
    indices: list[int],
    positions: list[int],
    *,
    sign_mode: str = "uniform",
    net_half_width: int | None = None,
) -> float:
    """候选相位评分(0-100)。

    sign_mode="mixed"(HNCACB 等正负峰共存):吸收度 = |各窗净吸收| 的
    中位数——正峰/负峰都能得高分;再要求强弱峰正负共存(缺一种符号
    惩罚 ×0.7),避免色散/全同号假解。
    sign_mode="uniform"(HSQC/CBCACONH 等同号峰):保留旧签名净吸收中位数
    (正峰偏好消解 ±180 歧义)。
    """
    real = rotate_real(complex_arr, axis, p0, p1)
    nets = _window_nets(
        real, axis, indices, positions, half_width=net_half_width
    )
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
    discrete: bool = True,
) -> tuple[list[int], list[int]]:
    """锁定「离散峰」迹线用于调相评分。

    与人工 nmrDraw「挑离散峰调相」一致:离散峰选择(discrete=True)阈值取
    低分位(默认 95,中央大团会抬高 99.5 分位把离散峰淘汰),再用峰形尖锐性
    过滤——峰位 ±window 内 ≥半高点的占比(duty)≤ max_duty 且峰顶相对
    局部背景低分位显著(prominence)。中央混杂峰团(密集峰簇)占窗口比例大、
    被排除;无离散峰时逐级降阈值,最后回退全部迹线。

    该选择只用于 mixed(HNCACB 等正负共存)实验——uniform 谱用旧锁定
    (99.5 分位全部强迹线)即可,离散过滤会改变迹线集把 d103 等带偏 180°
    (VM d103 回归校准)。
    """
    real0 = np.real(complex_arr)
    if not discrete:
        noise_old = float(np.std(real0[:80, :40])) if real0.size else 0.0
        thr_old = max(float(np.percentile(real0, 99.5)), noise_old * 5.0)
        out = _trace_indices_fixed(real0, axis, thr_old)
        if out[0]:
            return out
        return _trace_indices_fixed(real0, axis, -1.0)
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


def _subsampled_score_rows(
    rows: np.ndarray, k: int = 500, group: str = "even"
) -> float:
    """锁定迹线行的 top-K 半组子采样评分(0.2.199-补29dp,行式变体)。"""
    from core.qc import phase_quality

    peak_mag = np.max(np.abs(rows), axis=-1)
    order = np.argsort(peak_mag)[::-1][:k]
    selected = order[0::2] if group == "even" else order[1::2]
    if selected.size == 0:
        return 0.0
    return float(phase_quality.evaluate(rows[selected]).score)


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
    discrete: bool | None = None,
    net_half_width: int | None = None,
    cancel: Callable[[], bool] | None = None,
) -> MemoryAxisResult | None:
    """在复型数据的指定轴上做内存相位搜索(旧算法判断标准,零后端)。"""
    arr = np.asarray(complex_arr, dtype=np.complex128)
    n = arr.shape[axis]
    if arr.ndim < 2 or n < 8:
        return None
    logs: list[str] = []
    scored: dict[tuple[float, float], float] = {}

    def _score(p0: float, p1: float) -> float:
        return score_locked_memory(
            locked_rows, trace_positions, p0, p1,
            sign_mode=sign_mode, net_half_width=net_half_width,
        )

    # 基线 (0,0) 锁定迹线:mixed(HNCACB 等)用离散峰选择过滤中央混杂峰团
    # (VM sampleB 校准:大团会带偏相位,离散峰调到 F2=90°/F1≈0°);
    # uniform 用旧锁定(离散过滤会改变迹线集,VM d103 曾带偏 180°)
    use_discrete = (sign_mode == "mixed") if discrete is None else discrete
    trace_indices, trace_positions = _lock_discrete_traces(
        arr, axis, discrete=use_discrete
    )
    if not trace_indices:
        trace_indices, trace_positions = _trace_indices_fixed(
            np.real(arr), axis, -1.0
        )
    if not trace_indices:
        return None
    # 0.2.199-补29dp:只抽取锁定迹线行;候选评分只旋转这些行
    _moved = np.moveaxis(arr, axis, -1)
    _flat = _moved.reshape(-1, _moved.shape[-1])
    locked_rows = _flat[trace_indices]

    def _run_batch(phases: list[tuple[float, float]]) -> None:
        for raw in phases:
            if cancel is not None and cancel():
                raise RuntimeError("任务已取消:内存相位搜索被用户终止")
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
                    real = rotate_real(locked_rows, -1, p[0], p[1])
                    s = _subsampled_score_rows(real, group=group)
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
    if refine and not flat:
        # 平台圆中位数 p0(亚度精修,旧方案默认评分路径)
        # 0.2.199-补29dn:平坦面不回退——平坦区中位数会漂移
        # (如 sampleI F1 粗网格最优 90° 被改成 80°),保持粗网格最优
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
        # ±90° 对称性消歧(旧方案 NMRFlow;0.2.199-补29dp 用锁定行)
        best_real = rotate_real(locked_rows, -1, best_phase[0], best_phase[1])
        best_sym = _symmetry_memory(best_real, -1)
        for offset in (90.0, -90.0):
            cand = (best_phase[0] + offset, best_phase[1])
            if cand not in scored:
                _run_batch([cand])
            if cand in scored:
                c_score = scored[cand]
                if c_score >= best_score - PHASE_SYMMETRY_TOL:
                    c_sym = _symmetry_memory(
                        rotate_real(locked_rows, -1, cand[0], cand[1]), -1
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
                if c_score > best_score:
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
