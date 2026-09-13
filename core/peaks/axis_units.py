"""ppm/Hz ↔ 数据点数换算:把**物理宽度**参数换算成当前谱的点数。

动机(2026-09-13,用户):选峰的"轴峰排除边距"、峰位测量的"搜索窗口"等参数表达
的是**物理宽度**(多少 ppm / Hz)。若把它们写成固定点数,零填零会改变点距,
同一个"5 点"覆盖的 ppm 宽度就随填零变化(填零 k 倍 → 点距 1/k → 覆盖宽度 1/k),
于是**峰集与测量窗口口径会随处理参数漂移**。这里集中换算:

    points = max(minimum, round(width_ppm / ppm_per_point(axis)))

注意区分两类"点数":

- **结构性点数**(局部极大 3 点邻域、抛物线 ±1 点模板):必须是网格步长本身,
  **不随填零换算**——填零只让网格更密、插值更准;
- **物理含义点数**(本模块服务的对象):轴峰排除边距、搜索窗口、线宽/落差门槛,
  必须以 ppm/Hz 定义,运行时按当前谱换算。

默认物理宽度取"该轴核素线宽的倍数"(线宽 Hz 见 ``DEFAULT_LINEWIDTH_HZ``,
与 ``config/nmrforge.yaml`` 的 ``processing.linewidth_hz`` 对齐;调用方可传入
实际配置值覆盖)。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

# 核素默认线宽(Hz),与 config/nmrforge.yaml 的 processing.linewidth_hz 对齐
DEFAULT_LINEWIDTH_HZ: dict[str, float] = {
    "1H": 8.0,
    "15N": 15.0,
    "13C": 20.0,
    "31P": 15.0,
    "19F": 20.0,
    "": 15.0,
}

# 轴峰排除边距(第 0 轴上下)= 该轴核素线宽的多少倍
EDGE_MARGIN_LINEWIDTH_FACTOR = 3.0
# 峰位测量搜索窗口**半径** = 该轴核素线宽的多少倍
MEASUREMENT_WINDOW_LINEWIDTH_FACTOR = 1.5


def ppm_per_point(axis_ppm: Sequence[float]) -> float:
    """轴数组的点距(ppm/点);取相邻差的中位数,对端点/非均匀稳健。"""
    values = np.asarray(axis_ppm, dtype=float)
    if values.size < 2:
        return 0.0
    steps = np.abs(np.diff(values))
    steps = steps[np.isfinite(steps) & (steps > 0)]
    if steps.size == 0:
        return 0.0
    return float(np.median(steps))


def points_for_ppm(
    axis_ppm: Sequence[float], width_ppm: float, *, minimum: int = 1
) -> int:
    """物理宽度(ppm)→ 数据点数(≥minimum);无法换算时返回 0。"""
    step = ppm_per_point(axis_ppm)
    if step <= 0:
        return 0
    try:
        width = float(width_ppm)
    except (TypeError, ValueError):
        return 0
    if not np.isfinite(width) or width <= 0:
        return 0
    return max(int(minimum), int(round(width / step)))


def ppm_for_points(axis_ppm: Sequence[float], points: int) -> float:
    """点数 → 物理宽度(ppm)(记录/日志用)。"""
    step = ppm_per_point(axis_ppm)
    return float(max(0, int(points)) * step)


def hz_to_ppm(width_hz: float, obs_mhz: float) -> float:
    """Hz → ppm(需要该轴的观测频率 MHz)。"""
    try:
        obs = float(obs_mhz)
        hz = float(width_hz)
    except (TypeError, ValueError):
        return 0.0
    if obs <= 0 or not np.isfinite(obs) or not np.isfinite(hz):
        return 0.0
    return hz / obs


def linewidth_hz(
    nucleus: str,
    linewidth_hz_by_nucleus: Mapping[str, float] | None = None,
) -> float:
    """该核素的估计线宽(Hz);配置缺失时用内置默认。"""
    table = dict(DEFAULT_LINEWIDTH_HZ)
    if linewidth_hz_by_nucleus:
        for key, value in linewidth_hz_by_nucleus.items():
            try:
                table[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
    key = str(nucleus or "")
    return float(table.get(key, table.get("", 15.0)) or 15.0)


def physical_width_ppm(
    nucleus: str,
    obs_mhz: float,
    *,
    factor: float,
    linewidth_hz_by_nucleus: Mapping[str, float] | None = None,
) -> float:
    """线宽倍数 → 物理宽度(ppm)。"""
    return hz_to_ppm(
        float(factor) * linewidth_hz(nucleus, linewidth_hz_by_nucleus), obs_mhz
    )


def edge_margin_ppm(
    nucleus: str,
    obs_mhz: float,
    *,
    linewidth_hz_by_nucleus: Mapping[str, float] | None = None,
    factor: float = EDGE_MARGIN_LINEWIDTH_FACTOR,
) -> float:
    """轴峰排除边距的默认物理宽度(ppm;第 0 轴上下各一条带)。"""
    return physical_width_ppm(
        nucleus,
        obs_mhz,
        factor=factor,
        linewidth_hz_by_nucleus=linewidth_hz_by_nucleus,
    )


def measurement_window_ppm(
    nucleus: str,
    obs_mhz: float,
    *,
    linewidth_hz_by_nucleus: Mapping[str, float] | None = None,
    factor: float = MEASUREMENT_WINDOW_LINEWIDTH_FACTOR,
) -> float:
    """峰位测量搜索窗口半径的默认物理宽度(ppm)。"""
    return physical_width_ppm(
        nucleus,
        obs_mhz,
        factor=factor,
        linewidth_hz_by_nucleus=linewidth_hz_by_nucleus,
    )


def describe_axis(
    axis_ppm: Sequence[float],
    *,
    nucleus: str = "",
    width_ppm: float | None = None,
) -> dict[str, Any]:
    """记录用:某轴的核素、点距、物理宽度与等效点数。"""
    step = ppm_per_point(axis_ppm)
    out: dict[str, Any] = {
        "nucleus": str(nucleus or ""),
        "points": int(np.asarray(axis_ppm).size),
        "ppm_per_point": round(step, 6),
    }
    if width_ppm is not None:
        points = points_for_ppm(axis_ppm, width_ppm)
        out.update(
            {
                "requested_ppm": round(float(width_ppm), 6),
                "points_for_width": int(points),
                "effective_ppm": round(ppm_for_points(axis_ppm, points), 6),
            }
        )
    return out


__all__ = [
    "DEFAULT_LINEWIDTH_HZ",
    "EDGE_MARGIN_LINEWIDTH_FACTOR",
    "MEASUREMENT_WINDOW_LINEWIDTH_FACTOR",
    "describe_axis",
    "edge_margin_ppm",
    "hz_to_ppm",
    "linewidth_hz",
    "measurement_window_ppm",
    "physical_width_ppm",
    "points_for_ppm",
    "ppm_for_points",
    "ppm_per_point",
]
