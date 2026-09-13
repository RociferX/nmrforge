"""2D 高斯峰定位(**仅 2D**):对已检测到的候选峰做局部亚格点精修。

与抛物线定位(``core.qc.peak_detection``)并列、互不替代:检测仍由
``peak_detection.detect`` 负责,本模块只回答「这个候选峰的真正中心/宽度/幅度
在哪」。统一入口见 ``core.peaks.localize``。

模型(不旋转、轴向可分离,无 xy 协方差,无多项式基线):

    I(x, y) = B + A * exp( -(x - x0)^2 / (2 sx^2) - (y - y0)^2 / (2 sy^2) )

其中 **x = 数据轴 1(直接维 F2)、y = 数据轴 0(间接维 F1)**;待解参数
``(A, x0, y0, sx, sy, B)`` 由 ``scipy.optimize.least_squares`` 带 bounds 求解。

设计要点(2026-09-13,用户需求):

- **ROI 由调用方按物理宽度(ppm)换算成点数后传入**:本模块只认点数,便于单测
  与复用,也不会把「固定点数」偷偷写进核心(换算在
  ``core.peaks.localize``/``core.peaks.axis_units``);
- **初始中心 = 既有抛物线结果**(调用方传入 1× 点误差内的 seed),两种方法从同一
  candidate 出发,可独立比较;
- **负峰**:先按 ``sign`` 把 ROI 翻正再拟合(约束 ``A > 0``),返回时幅度/基线
  按原单位给出(``sign * A``)——不破坏 mixed 实验的正负峰支持;
- **失败显式化**:任何异常情况返回 ``success=False`` + ``reason``,由调用方记录
  并回退抛物线(不静默,见 core/peaks/localize.py)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

# sigma 下界(点):< 0.5 点的 Gaussian 是脉冲,拟合必然病态
SIGMA_MIN_POINTS = 0.5
# ROI 每轴最少点数与最少有效点数(否则不拟合)
MIN_ROI_POINTS_PER_AXIS = 3
MIN_FINITE_POINTS = 8
# 拟合失败原因(稳定字符串,写入记录/日志)
REASON_OK = ""
REASON_NOT_2D = "not_2d"
REASON_ROI_TOO_SMALL = "roi_too_small"
REASON_INSUFFICIENT_DATA = "insufficient_data"
REASON_NON_FINITE = "non_finite"
REASON_FLAT_REGION = "flat_region"
REASON_NO_PEAK = "no_peak"
REASON_OPTIMIZER_ERROR = "optimizer_error"
REASON_NOT_CONVERGED = "not_converged"
REASON_CENTER_AT_BOUNDARY = "center_at_boundary"
REASON_SIGMA_AT_BOUND = "sigma_at_bound"
REASON_POOR_FIT = "poor_fit"
REASON_NON_FINITE_PARAMS = "non_finite_params"


@dataclass
class GaussianFitResult:
    """一次 2D 高斯拟合的结果(坐标全部为**数据轴序**:轴 0 = F1,轴 1 = F2)。"""

    success: bool = False
    reason: str = ""
    center: tuple[float, float] = (0.0, 0.0)     # 分数索引(绝对,非 ROI 局部)
    amplitude: float = 0.0                        # 原单位(sign * A)
    sigma: tuple[float, float] = (0.0, 0.0)       # 单位:数据点
    baseline: float = 0.0                         # 原单位(sign * B)
    rmse: float = 0.0                             # 残差 RMS(原单位)
    boundary_hit: bool = False
    n_points: int = 0                             # ROI 内有效点数
    sign: int = 1
    seed: tuple[float, float] = (0.0, 0.0)        # 初始中心(抛物线结果)
    n_iter: int = 0
    # ROI 实际范围(数据点,闭区间;留档复算用)
    roi: tuple[int, int, int, int] = (0, 0, 0, 0)  # (lo0, hi0, lo1, hi1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fit_success": bool(self.success),
            "fit_failure_reason": str(self.reason),
            "center_point_f1": float(self.center[0]),
            "center_point_f2": float(self.center[1]),
            "amplitude": float(self.amplitude),
            "sigma_points_f1": float(self.sigma[0]),
            "sigma_points_f2": float(self.sigma[1]),
            "baseline": float(self.baseline),
            "fit_rmse": float(self.rmse),
            "boundary_hit": bool(self.boundary_hit),
            "n_points": int(self.n_points),
            "sign": int(self.sign),
            "seed_point_f1": float(self.seed[0]),
            "seed_point_f2": float(self.seed[1]),
            "n_iter": int(self.n_iter),
            "roi_points": [int(v) for v in self.roi],
        }


def _roi_bounds(seed: float, half: float, size: int) -> tuple[int, int]:
    """以 seed 为中心、半宽 half(点)的闭区间,裁剪到 [0, size-1]。"""
    lo = int(np.ceil(float(seed) - float(half)))
    hi = int(np.floor(float(seed) + float(half)))
    lo = max(0, min(lo, int(size) - 1))
    hi = max(0, min(hi, int(size) - 1))
    return lo, hi


def _border_median(block: np.ndarray) -> float:
    """ROI 边缘点(第一/最后行与列)的中位数——局部常数基线的稳健初值。"""
    edge = np.concatenate(
        [block[0, :], block[-1, :], block[:, 0], block[:, -1]]
    )
    return float(np.median(edge))


def _profile_sigma(
    profile: np.ndarray, center: int, base: float, amplitude: float, fallback: float
) -> float:
    """沿一轴经中心点的剖面做半高宽估计 → sigma(点);失败回退 fallback。"""
    if amplitude <= 0 or not np.isfinite(amplitude):
        return float(fallback)
    level = base + 0.5 * amplitude
    size = int(profile.size)
    center = int(min(max(center, 0), size - 1))
    if profile[center] < level:
        return float(fallback)
    left = center
    while left - 1 >= 0 and profile[left - 1] >= level:
        left -= 1
    right = center
    while right + 1 < size and profile[right + 1] >= level:
        right += 1
    fwhm = float(right - left) + 1.0
    if fwhm <= 1.0:
        return float(fallback)
    return float(fwhm / 2.3548200450309493)  # 2*sqrt(2 ln 2)


def _model(params: np.ndarray, row: np.ndarray, col: np.ndarray) -> np.ndarray:
    """row/col 为数据轴 0/1 的索引网格(轴 0 = F1 间接,轴 1 = F2 直接)。"""
    amp, c0, c1, s0, s1, base = params
    return base + amp * np.exp(
        -((row - c0) ** 2) / (2.0 * s0 * s0)
        - ((col - c1) ** 2) / (2.0 * s1 * s1)
    )


def fit_gaussian_2d(
    data: Any,
    *,
    seed: tuple[float, float],
    roi: tuple[float, float],
    sign: int = 1,
    max_rmse_ratio: float = 0.0,
    max_nfev: int = 200,
) -> GaussianFitResult:
    """在 ``seed``(数据轴序分数索引)附近拟合 2D 高斯。

    seed: 初始中心(应为既有抛物线定位结果);roi: 每轴 ROI **半宽(点)**;
    sign: ``+1`` 正峰 / ``-1`` 负峰(按 ROI 强度符号自动判定的结果);
    max_rmse_ratio: >0 时 ``rmse > ratio * |amplitude|`` 判 ``poor_fit``;
    max_nfev: ``least_squares`` 最大函数求值次数。

    返回 ``GaussianFitResult``;任何失败都带 ``reason``,不抛异常(除参数本身
    非法),便于调用方记录并回退。
    """
    arr = np.asarray(data)
    if arr.ndim != 2:
        return GaussianFitResult(
            success=False, reason=REASON_NOT_2D, sign=int(sign),
            seed=(float(seed[0]), float(seed[1])),
        )
    real = np.real(arr).astype(float, copy=False)
    size0, size1 = int(real.shape[0]), int(real.shape[1])
    if not (np.isfinite(seed[0]) and np.isfinite(seed[1])):
        # 初值非有限(如谱里有 NaN):不猜,直接判定失败由调用方回退
        return GaussianFitResult(
            success=False, reason=REASON_NON_FINITE, sign=int(sign),
            seed=(float(seed[0]), float(seed[1])),
        )
    lo0, hi0 = _roi_bounds(seed[0], roi[0], size0)
    lo1, hi1 = _roi_bounds(seed[1], roi[1], size1)
    result = GaussianFitResult(
        success=False,
        sign=int(sign),
        seed=(float(seed[0]), float(seed[1])),
        roi=(lo0, hi0, lo1, hi1),
    )
    if (
        hi0 - lo0 + 1 < MIN_ROI_POINTS_PER_AXIS
        or hi1 - lo1 + 1 < MIN_ROI_POINTS_PER_AXIS
    ):
        result.reason = REASON_ROI_TOO_SMALL
        return result

    block = real[lo0 : hi0 + 1, lo1 : hi1 + 1]
    finite = np.isfinite(block)
    n_finite = int(finite.sum())
    result.n_points = n_finite
    if not finite.all():
        result.reason = REASON_NON_FINITE
        return result
    if n_finite < MIN_FINITE_POINTS:
        result.reason = REASON_INSUFFICIENT_DATA
        return result

    value = float(sign) * block
    if float(np.ptp(value)) <= 1e-12:
        result.reason = REASON_FLAT_REGION
        return result

    # 局部常数基线 = ROI 边缘中位数;幅度 = 峰值 - 基线
    base_init = _border_median(value)
    peak_value = float(np.max(value))
    amp_init = peak_value - base_init
    if not np.isfinite(amp_init) or amp_init <= 0.0:
        result.reason = REASON_NO_PEAK
        return result

    # 局部坐标(ROI 内)的小数中心 = seed 相对 ROI 原点
    c0_init = float(seed[0]) - lo0
    c1_init = float(seed[1]) - lo1
    row = int(round(c0_init))
    col = int(round(c1_init))
    row = min(max(row, 0), value.shape[0] - 1)
    col = min(max(col, 0), value.shape[1] - 1)
    fallback_sigma = max(SIGMA_MIN_POINTS * 1.5, 0.5 * float(roi[0]))
    s0_init = _profile_sigma(value[:, col], row, base_init, amp_init, fallback_sigma)
    fallback_sigma1 = max(SIGMA_MIN_POINTS * 1.5, 0.5 * float(roi[1]))
    s1_init = _profile_sigma(value[row, :], col, base_init, amp_init, fallback_sigma1)

    # bounds:中心不出 ROI;sigma ∈ [0.5, ROI 半宽];基线不出 ROI 值域
    c0_lo, c0_hi = 0.0, float(value.shape[0] - 1)
    c1_lo, c1_hi = 0.0, float(value.shape[1] - 1)
    s0_hi = max(SIGMA_MIN_POINTS * 2.0, float(roi[0]))
    s1_hi = max(SIGMA_MIN_POINTS * 2.0, float(roi[1]))
    v_lo, v_hi = float(np.min(value)), float(np.max(value))
    if v_hi <= v_lo:
        result.reason = REASON_FLAT_REGION
        return result
    # 基线 bounds:真基线 ≤ ROI 最小值(峰顶有高度,角点仍在基线上方),
    # 因此下界必须允许往下走一个 ROI 动态范围,否则解被排除在外(实测会把
    # 幅度压小 3%+)。上界不超过 ROI 最大值。
    span = float(v_hi - v_lo)
    b_lo, b_hi = float(v_lo - span), float(v_hi)
    p0 = np.array(
        [
            min(max(amp_init, 0.0), max(v_hi - v_lo, 1e-12)),
            min(max(c0_init, c0_lo), c0_hi),
            min(max(c1_init, c1_lo), c1_hi),
            min(max(s0_init, SIGMA_MIN_POINTS), s0_hi),
            min(max(s1_init, SIGMA_MIN_POINTS), s1_hi),
            min(max(base_init, b_lo), b_hi),
        ],
        dtype=float,
    )
    lower = np.array([0.0, c0_lo, c1_lo, SIGMA_MIN_POINTS, SIGMA_MIN_POINTS, b_lo])
    upper = np.array([np.inf, c0_hi, c1_hi, s0_hi, s1_hi, b_hi])
    # least_squares 要求严格 lo < hi
    for idx in (1, 2, 3, 4, 5):
        if not (lower[idx] < upper[idx]):
            result.reason = REASON_ROI_TOO_SMALL
            return result

    row_grid, col_grid = np.mgrid[0 : value.shape[0], 0 : value.shape[1]]
    row_grid = row_grid.astype(float)
    col_grid = col_grid.astype(float)

    def residual(params: np.ndarray) -> np.ndarray:
        return (_model(params, row_grid, col_grid) - value).ravel()

    try:
        from scipy.optimize import least_squares

        fit = least_squares(
            residual, p0, bounds=(lower, upper), max_nfev=int(max_nfev)
        )
    except Exception as exc:  # noqa: BLE001 - 优化器异常统一记原因
        result.reason = f"{REASON_OPTIMIZER_ERROR}: {type(exc).__name__}: {exc}"
        return result

    amp, c0, c1, s0, s1, base = (float(v) for v in fit.x)
    result.n_iter = int(getattr(fit, "nfev", 0) or 0)
    sv = np.asarray(fit.x, dtype=float)
    if not np.all(np.isfinite(sv)):
        result.reason = REASON_NON_FINITE_PARAMS
        return result
    if not bool(getattr(fit, "success", False)):
        result.reason = REASON_NOT_CONVERGED
        return result

    # 中心/宽度撞 bounds → 显式失败(由调用方回退抛物线)
    tol = 1e-3
    center_hit = (
        c0 <= c0_lo + tol
        or c0 >= c0_hi - tol
        or c1 <= c1_lo + tol
        or c1 >= c1_hi - tol
    )
    sigma_hit = (
        s0 <= SIGMA_MIN_POINTS * (1.0 + 1e-3)
        or s1 <= SIGMA_MIN_POINTS * (1.0 + 1e-3)
        or s0 >= s0_hi * (1.0 - 1e-3)
        or s1 >= s1_hi * (1.0 - 1e-3)
    )
    rmse = float(np.sqrt(np.mean(residual(fit.x) ** 2)))
    result.amplitude = float(sign) * amp
    result.baseline = float(sign) * base
    result.sigma = (s0, s1)
    result.rmse = rmse
    result.center = (lo0 + c0, lo1 + c1)
    result.boundary_hit = bool(center_hit or sigma_hit)
    if center_hit:
        result.reason = REASON_CENTER_AT_BOUNDARY
        return result
    if sigma_hit:
        result.reason = REASON_SIGMA_AT_BOUND
        return result
    if max_rmse_ratio and max_rmse_ratio > 0.0:
        if rmse > float(max_rmse_ratio) * abs(amp):
            result.reason = REASON_POOR_FIT
            return result
    if amp <= 0.0:
        result.reason = REASON_NO_PEAK
        return result
    result.success = True
    result.reason = REASON_OK
    return result


__all__ = [
    "MIN_FINITE_POINTS",
    "MIN_ROI_POINTS_PER_AXIS",
    "REASON_CENTER_AT_BOUNDARY",
    "REASON_FLAT_REGION",
    "REASON_INSUFFICIENT_DATA",
    "REASON_NON_FINITE",
    "REASON_NOT_2D",
    "REASON_NOT_CONVERGED",
    "REASON_NO_PEAK",
    "REASON_OK",
    "REASON_OPTIMIZER_ERROR",
    "REASON_POOR_FIT",
    "REASON_ROI_TOO_SMALL",
    "REASON_SIGMA_AT_BOUND",
    "SIGMA_MIN_POINTS",
    "GaussianFitResult",
    "fit_gaussian_2d",
]
