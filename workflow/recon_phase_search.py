"""重构输出（复型 nus3d_rc 平面）上的按维相位搜索报告（方案 B）。

以 SMILE 重构完的复型平面为起点，按旧项目方式做内存内相位搜索——
只出报告（每轴 p0/p1/score/gain），不改管线；验证可行性后供方案 A 复用。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from core.optimization.phase_search import (
    _search_axis,
    _trace_profiles,
    _window_metrics,
)


def median_window_absorption(data: np.ndarray, axis: int) -> float:
    """基线吸收度：信号迹线在 (p0=0, p1=0) 的峰窗口吸收度中位数。"""
    moved = np.moveaxis(np.asarray(data), axis, -1)
    traces = moved.reshape(-1, moved.shape[-1])
    real = np.real(data)
    threshold = max(float(np.percentile(real, 99.5)), 0.0)
    magnitude = np.abs(traces)
    signal = np.max(magnitude, axis=-1) > threshold
    if not np.any(signal):
        return float("nan")
    selected = traces[signal]
    positions = np.argmax(np.abs(selected), axis=-1)
    profiles = _trace_profiles(selected, positions)
    absorption, _ = _window_metrics(profiles)
    weights = np.max(np.abs(selected), axis=-1) + 1e-12
    return float(np.average(absorption, weights=weights))


def search_recon_phase(
    planes: np.ndarray,
) -> dict[str, dict[str, float]]:
    """对复型重构平面逐轴做相位搜索，返回 {axisN: {p0,p1,score,gain}}。"""
    data = np.asarray(planes, dtype=np.complex128)
    results: dict[str, dict[str, float]] = {}
    for axis in range(data.ndim):
        baseline = median_window_absorption(data, axis)
        found = _search_axis(data, axis)
        if found is None:
            results[f"axis{axis}"] = {
                "p0": 0.0,
                "p1": 0.0,
                "score": float("nan"),
                "gain": float("nan"),
            }
            continue
        p0, p1, score = found
        results[f"axis{axis}"] = {
            "p0": p0,
            "p1": p1,
            "score": score,
            "gain": round(score - baseline, 6),
        }
    return results


def format_report(results: dict[str, dict[str, float]]) -> str:
    lines = [
        f"{'轴':>6} {'p0':>8} {'p1':>8} {'score':>7} {'baseline':>8} {'gain':>6}"
    ]
    lines.append("-" * len(lines[0]))
    for key, value in results.items():
        gain = value["gain"]
        lines.append(
            f"{key:>6} {value['p0']:>8.1f} {value['p1']:>8.1f} "
            f"{value['score']:>7.3f} {value['score'] - value['gain']:>8.3f} "
            f"{gain:>6.3f}"
        )
    return "\n".join(lines)


def save_report(results: dict[str, dict[str, float]], path: Path | str) -> Path:
    out = Path(path)
    out.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out
