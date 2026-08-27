"""逐维共识相位搜索(0.2.199-补29i,人工投影调相思路)。

用户方案:3D 人工调相看三个投影面——直接维的 1D 谱是全部 (F1,F2) 组合
沿直接轴的复型迹线(两个间接维同理);把这些 1D 谱逐条调好相位,再取
统计上的最佳相位。每维独立、互不干扰:其它维相位只贡献逐峰常数偏移,
跨迹线/跨峰统计对称抵消。

配合:上游必须保留复型数据(nmrPipe 全链 PS 不加 -di 或自持复型),
每条 1D 迹线带虚部,供吸收/色散判别与逐维相位拟合。
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from core.optimization.phase_search import (
    _row_absorption,
    _row_p1_fit,
    _row_peak_positions,
)


def _row_p0_raw(
    arr: np.ndarray,
    positions: np.ndarray,
    heights: np.ndarray,
    p1_signal: float,
) -> float:
    """p1(信号斜坡)固定后单条迹线的原始 p0(不做 ±180 消歧)。

    各峰相位 = 公共 p0 + 该迹线常数(t1/其它维相位);取加权圆均值取反即
    PS 校正 p0。±180 由跨迹线共识统一消歧,单条不提前翻。
    """
    n = arr.shape[-1]
    vals = arr[positions]
    ramp = np.exp(-1j * np.deg2rad(p1_signal * positions / max(n - 1, 1)))
    unit = np.exp(1j * np.angle(vals * ramp))
    weights = heights + 1e-12
    vec = np.sum(weights * unit) / max(float(np.sum(weights)), 1e-12)
    return float((-np.rad2deg(np.angle(vec))) % 360.0)


def _circular_mean(angles: np.ndarray) -> float:
    """角度(度)圆均值。"""
    rad = np.deg2rad(np.asarray(angles, dtype=float))
    return float(
        (np.rad2deg(np.arctan2(np.mean(np.sin(rad)), np.mean(np.cos(rad)))))
        % 360.0
    )


def _lock_trace_peaks(
    row: np.ndarray,
    *,
    margin: int = 8,
    max_peaks: int = 8,
    snr: float = 10.0,
    global_frac: float = 0.005,
    global_max: float = 0.0,
) -> tuple[np.ndarray, np.ndarray] | None:
    """迹线局部极大锁峰(自定义,适配短迹线)。

    99 分位阈值对短迹线(如 48 点)会砍到只剩最强峰;这里用局部 MAD
    噪音 × snr 与全局最大 × global_frac 的较大者做门槛,多峰迹线能
    全部锁定,纯噪声迹线(峰值 ~3-4×MAD < 8×MAD)返回 None。
    """
    mag = np.abs(row)
    n = mag.size
    margin = min(margin, max(2, n // 6))  # 短轴自适应(如 16 点 → 2)
    lo, hi = margin, n - margin
    if hi <= lo + 2:
        return None
    local = np.zeros(n, dtype=bool)
    local[lo:hi] = (mag[lo:hi] >= mag[lo - 1:hi - 1]) & (
        mag[lo:hi] > mag[lo + 1:hi + 1]
    )
    med = float(np.median(mag))
    mad = float(np.median(np.abs(mag - med))) * 1.4826 + 1e-12
    thr = max(snr * mad, global_frac * global_max)
    idx = np.where(local & (mag > thr))[0]
    if not idx.size:
        return None
    order = np.argsort(mag[idx])[::-1][:max_peaks]
    return idx[order], mag[idx[order]]


def search_axis_phase_consensus(
    complex_arr: np.ndarray,
    axis: int,
    *,
    max_rows: int = 256,
    min_peaks: int = 2,
    margin: int = 8,
    max_peaks: int = 8,
    sign_mode: str = "uniform",
    progress: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> tuple[float, float, float] | None:
    """沿指定轴的全部 1D 复型迹线逐条调相 + 统计共识。

    逐条迹线:
      - 锁干净峰(`_row_peak_positions`,行噪音/全局阈值 + 首尾 margin);
      - p1: 多峰相位集中度拟合(`_row_p1_fit`,与迹线常数解耦);
      - p0: 该 p1 下各峰相位圆均值取反(`_row_p0_raw`,不提前 ±180)。
    跨迹线共识:
      - p1 = 各迹线 p1 中位数(信号斜坡,PS 校正取反);
      - p0 = 各迹线原始 p0 折入 ±180 对齐后圆均值(迭代 2 次);最后按
        共识相位下全部峰窗吸收度做全局 ±180 消歧(sign_mode=mixed 跳过
        正峰偏好,负吸收峰同样计分)。
      - score = 共识相位下各迹线峰窗吸收度中位数 ×100(0..100,与现有
        门控一致;正确相位通常 >60)。
    返回 (p0, p1, score);无足够干净峰返回 None。
    """
    if cancel is not None and cancel():
        raise RuntimeError("任务已取消:逐维共识相位搜索被用户终止")
    moved = np.moveaxis(np.asarray(complex_arr, dtype=np.complex128), axis, -1)
    flat = moved.reshape(-1, moved.shape[-1])
    n = flat.shape[-1]
    if flat.ndim != 2 or n < 8:
        return None
    if flat.shape[0] > max_rows:
        index = np.linspace(0, flat.shape[0] - 1, max_rows).astype(int)
        flat = flat[index]
    global_max = float(np.max(np.abs(flat))) if flat.size else 0.0
    infos: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for row in flat:
        peaks = _lock_trace_peaks(
            row,
            margin=margin,
            max_peaks=max_peaks,
            global_max=global_max,
        )
        if peaks is None:
            continue
        pos, heights = peaks
        if pos.size >= 1:
            infos.append((row, pos, heights))
    if not infos:
        return None
    if progress is not None:
        progress(f"逐维共识相位: 轴 {axis} 锁定 {len(infos)} 条干净迹线")
    p1_rows: list[float] = []
    for arr, pos, heights in infos:
        fit = _row_p1_fit(arr, pos, heights)
        if fit is not None:
            p1_rows.append(fit[0])
    p1_signal = float(np.median(p1_rows)) if p1_rows else 0.0
    p1 = -p1_signal
    raw_p0 = np.array(
        [_row_p0_raw(arr, pos, heights, p1_signal) for arr, pos, heights in infos],
        dtype=float,
    )

    # p0 规范角:倍增折叠到 [0,180)(恰 180 双峰/任意 ±180 偏移都稳健)
    folded = raw_p0 % 180.0
    p0 = (
        0.5
        * (
            np.rad2deg(
                np.arctan2(
                    np.mean(np.sin(2.0 * np.deg2rad(folded))),
                    np.mean(np.cos(2.0 * np.deg2rad(folded))),
                )
            )
        )
    ) % 180.0

    def _median_abs_and_sign(phase: float) -> tuple[float, float]:
        abs_vals: list[float] = []
        sign_vals: list[float] = []
        for arr, pos, heights in infos:
            a, sgn = _row_absorption(arr, pos, heights, phase, p1, radius=1)
            abs_vals.append(a)
            sign_vals.append(sgn)
        return float(np.median(abs_vals)), float(np.median(sign_vals))

    if sign_mode != "mixed":
        a0, s0 = _median_abs_and_sign(p0)
        a180, s180 = _median_abs_and_sign((p0 + 180.0) % 360.0)
        # ±180 消歧用正峰符号(吸收度对正负不敏感,不能用于翻转判定)
        if s180 > s0:
            p0 = (p0 + 180.0) % 360.0
            a0 = a180
    else:
        a0, _s0 = _median_abs_and_sign(p0)
    score = 100.0 * a0
    if cancel is not None and cancel():
        raise RuntimeError("任务已取消:逐维共识相位搜索被用户终止")
    return p0, p1, score


__all__ = ["search_axis_phase_consensus"]
