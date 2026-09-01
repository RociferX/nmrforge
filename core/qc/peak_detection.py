"""峰检测（用于 QC 与峰表，不是 assignment，框架 §19）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.ndimage import maximum_filter

from core.qc import noise


@dataclass
class Peak:
    position: tuple[float, ...] = ()
    height: float = 0.0
    volume: float = 0.0
    width: tuple[float, ...] = ()
    snr: float = 0.0
    sign: int = 1


@dataclass
class PeakDetectionParams:
    sigma_multiplier: float = 3.0
    min_snr: float = 3.0
    neighborhood: int = 3
    # 峰符号模式(0.2.199-补29ap,用户规则):
    #   positive  仅正峰(默认,保持旧行为);
    #   negative  仅负峰;
    #   both      正负峰都选(mixed 实验,如 HNCACB 13Cα/13Cβ 反相);
    #   dominant  只保留占多数的符号峰(uniform 实验;主符号由候选峰计数
    #             决定,平局按绝对强度总和,再平局取正——用户:「不用管
    #             正负,肯定是多的那些」)。
    sign_mode: str = "positive"
    # 轴峰排除(0.2.199-补29at,用户):排除第 0 轴(上下)边缘 edge_margin
    # 点内的峰——轴峰是最上下横着的一条(未演化间接维信号落在 F1 边缘)。
    edge_margin: int = 0


def _refined_index(value: np.ndarray, idx: np.ndarray, axis: int) -> float:
    """亚像素峰位:沿 axis 对局部极大及其两点邻域做抛物线顶点修正。

    真实峰顶常落在像素之间,整数格 argmax 平均偏离 ~0.34px(VM 实测
    71% 峰 >0.25px、最大 0.69px),放大后峰标记明显偏离峰顶;抛物线
    修正后合成高斯峰精度 ~0.03-0.07px(0.2.199-补29eo)。
    """
    i = int(idx[axis])
    if not (0 < i < value.shape[axis] - 1):
        return float(i)
    sl = list(idx)
    vals = []
    for di in (-1, 0, 1):
        sl[axis] = i + di
        vals.append(float(value[tuple(sl)]))
    v0, v1, v2 = vals
    denom = v0 - 2.0 * v1 + v2
    if abs(denom) < 1e-12:
        return float(i)
    offset = 0.5 * (v0 - v2) / denom
    return float(i + float(np.clip(offset, -0.5, 0.5)))


def _line_min_below_zero(value: np.ndarray, p: tuple, q: tuple) -> bool:
    """沿 p→q 直线(整数像素)采样,是否存在 <0 的值(振铃负谷)。

    0.2.199-补29ep:截断/加窗产生的主峰振铃旁瓣与主峰之间隔着负值谷,
    真实重叠峰之间无负谷;该负谷是「旁瓣候选」与「更强峰」的判别依据。
    """
    d = int(np.ceil(np.linalg.norm(np.asarray(q) - np.asarray(p))))
    if d <= 1:
        return False
    ts = np.linspace(0.0, 1.0, d + 1)
    for t in ts:
        idx = tuple(
            int(round(a + (b - a) * t)) for a, b in zip(p, q)
        )
        if (
            all(0 <= v < s for v, s in zip(idx, value.shape))
            and value[idx] < 0
        ):
            return True
    return False


def _reject_ringing_sidelobes(
    value: np.ndarray, candidates: list[Peak], radius: float = 6.0
) -> list[Peak]:
    """剔除强峰振铃旁瓣(0.2.199-补29ep)。

    截断/加窗产生的旁瓣是真实局部极大,但位于更强峰附近,且与主峰之间
    隔着负值谷——用户看到「峰标记明显偏离峰真正的顶」。候选按强度降序
    处理:弱候选若在更强峰 radius 内且连线穿过负值 → 判为旁瓣剔除;
    真实重叠峰之间无负谷,保留。
    """
    ordered = sorted(candidates, key=lambda p: -abs(p.height))
    kept: list[Peak] = []
    for p in ordered:
        pi = tuple(int(round(v)) for v in p.position)
        reject = False
        for s in kept:
            si = tuple(int(round(v)) for v in s.position)
            if np.linalg.norm(np.asarray(pi) - np.asarray(si)) > radius:
                continue
            if _line_min_below_zero(value, pi, si):
                reject = True
                break
        if not reject:
            kept.append(p)
    return kept


def _candidates(
    real: np.ndarray, sigma: float, params: PeakDetectionParams, sign: int
) -> list[Peak]:
    """提取 sign(+1/-1) 方向的候选峰。

    严格局部极大(中心须大于环邻域最大,排除平坦区/脊线上「等于窗口最大」
    的伪峰,0.2.199-补29aq 修)+ 强度>噪声×sigma + S/N 阈值;
    位置做亚像素抛物线修正(0.2.199-补29eo),峰顶落在像素之间也能对齐。
    """
    value = sign * real
    footprint = np.ones([params.neighborhood] * real.ndim, dtype=bool)
    ring = footprint.copy()
    ring[tuple(s // 2 for s in footprint.shape)] = False
    if ring.any():
        neighbor_max = maximum_filter(value, footprint=ring, mode="constant")
        mask = (value > neighbor_max) & (value > sigma * params.sigma_multiplier)
    else:
        maxima = maximum_filter(value, footprint=footprint, mode="constant")
        mask = (value == maxima) & (value > sigma * params.sigma_multiplier)
    peaks: list[Peak] = []
    margin = max(0, int(params.edge_margin))
    for idx in np.argwhere(mask):
        if margin and (idx[0] < margin or idx[0] >= real.shape[0] - margin):
            continue  # 轴峰:上下边缘横条
        val = float(real[tuple(idx)])
        snr_value = abs(val) / sigma if sigma > 0 else 0.0
        if snr_value >= params.min_snr:
            position = tuple(
                _refined_index(value, idx, a) for a in range(real.ndim)
            )
            peaks.append(
                Peak(
                    position=position,
                    height=val,
                    snr=snr_value,
                    sign=sign,
                )
            )
    # 0.2.199-补29ep:剔除强峰振铃旁瓣(与更强峰间有负值谷的弱局部极大)
    return _reject_ringing_sidelobes(value, peaks)


def _keep_dominant(candidates: list[Peak]) -> list[Peak]:
    """dominant 模式:只保留候选峰更多的符号(平局按绝对强度总和,再平局取正)。"""
    counts: dict[int, int] = {1: 0, -1: 0}
    totals: dict[int, float] = {1: 0.0, -1: 0.0}
    for peak in candidates:
        s = 1 if peak.height >= 0 else -1
        counts[s] += 1
        totals[s] += abs(peak.height)
    dominant = 1
    for s in (-1, 1):
        if counts[s] > counts[dominant]:
            dominant = s
        elif counts[s] == counts[dominant] and totals[s] > totals[dominant]:
            dominant = s
    return [
        peak
        for peak in candidates
        if (1 if peak.height >= 0 else -1) == dominant
    ]


def snap_to_peak_top(
    data: Any, row: int, col: int, radius: int = 6
) -> tuple[int, int]:
    """把点击点吸附到附近峰顶(局部 |值| 最大点)。

    在 (row, col) 周围 radius 窗口内找 |值| 最大的点;若该点显著强于
    点击点(存在峰顶)返回吸附点,否则原样返回点击点(用户点击即峰)。
    2D 数据专用(点击加峰场景)。
    """
    real = np.real(np.asarray(data))
    if real.ndim != 2 or real.size == 0:
        return int(row), int(col)
    radius = max(1, int(radius))
    row, col = int(row), int(col)
    r0 = max(0, row - radius)
    r1 = min(real.shape[0], row + radius + 1)
    c0 = max(0, col - radius)
    c1 = min(real.shape[1], col + radius + 1)
    if r1 <= r0 or c1 <= c0:
        return row, col
    window = real[r0:r1, c0:c1]
    pr, pc = np.unravel_index(int(np.argmax(np.abs(window))), window.shape)
    pr += r0
    pc += c0
    if abs(float(real[pr, pc])) > abs(float(real[row, col])):
        return int(pr), int(pc)
    return row, col


def detect(data: Any, params: PeakDetectionParams | None = None) -> list[Peak]:
    """局部极大值 + 强度>噪声×sigma + S/N 阈值(2D/3D 通用)。

    sign_mode 控制峰符号:uniform 实验(HSQC/COSY 等单符号)用 dominant 只
    保留主符号峰;mixed 实验(HNCACB 等正负共存)用 both 正负都选。
    Peak.height 保留真实符号(CSV Intensity 亦带符号),snr 取绝对值。
    """
    arr = np.asarray(data)
    params = params or PeakDetectionParams()
    sigma = noise.estimate(arr).global_sigma
    real = np.real(arr)
    mode = params.sign_mode
    signs = {
        "positive": (1,),
        "negative": (-1,),
        "both": (1, -1),
        "dominant": (1, -1),
    }.get(mode, (1,))
    candidates: list[Peak] = []
    for sign in signs:
        candidates.extend(_candidates(real, sigma, params, sign))
    if mode == "dominant" and candidates:
        candidates = _keep_dominant(candidates)
    candidates.sort(key=lambda peak: abs(peak.height), reverse=True)
    return candidates
