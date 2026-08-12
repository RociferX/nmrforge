"""逐维基线优化(用户方案,G2B-007 第 4 点)。

对每维(直接+间接)用 core.qc.baseline_quality 评分,网格
mode ∈ {off, auto} × order ∈ {1,2,3},选每维最优写回 baseline 配置;
最终谱由真实管线渲染(baseline 写回 → render_scripts / backend.process,
uniform 与 NUS 均支持)。score 可注入。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.data.internal_data_model import Experiment
from core.processing import baseline as baseline_proc


@dataclass
class BaselineOptimizeResult:
    """逐维基线优化结果。"""

    baseline: dict[str, dict[str, Any]]
    scores: dict[str, dict[str, float]]
    spectrum_path: str
    logs: list[str] = field(default_factory=list)


def _default_score(data: np.ndarray, axis: int) -> float:
    """默认评分:core.qc.baseline_quality(轴移到末尾评估)。"""
    from core.qc import baseline_quality

    moved = np.moveaxis(np.asarray(data), axis, -1)
    return float(baseline_quality.evaluate(moved).score)


def optimize_baseline(
    experiment: Experiment,
    spectrum_path: Path | str,
    *,
    grid: list[tuple[str, int]] | None = None,
    score_fn: Callable[[np.ndarray, int], float] | None = None,
) -> BaselineOptimizeResult:
    """逐维基线优化:每维网格 mode∈{off,auto}×order∈{1,2,3},内存内评分,
    选每维最优写回 baseline 配置;最终谱由真实管线渲染(baseline 写回)。

    score_fn(data, axis_idx) 返回该轴基线质量分(默认 baseline_quality);
    off=不校正。返回 {"baseline": {axis: {...}}, "scores", "logs"}。
    """
    import nmrglue as ng

    _dic, data = ng.pipe.read(str(spectrum_path))
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        arr = arr.real
    axes = [dim.logical_axis for dim in experiment.dimensions]
    grid = grid if grid is not None else [
        ("off", 0),
        ("auto", 1),
        ("order", 1),
        ("order", 2),
        ("order", 3),
    ]
    score_fn = score_fn or _default_score
    baseline_cfg: dict[str, dict[str, Any]] = {}
    scores: dict[str, dict[str, float]] = {}
    logs: list[str] = []
    for axis_idx, axis in enumerate(axes):
        axis_scores: dict[str, float] = {}
        best: tuple[float, str, int] | None = None
        for mode, order in grid:
            work = arr.copy()
            if mode != "off":
                work = baseline_proc.apply(
                    work,
                    baseline_proc.BaselineParams(
                        method="polynomial", axis=axis, order=max(order, 1)
                    ),
                )
            value = float(score_fn(work, axis_idx))
            axis_scores[f"{mode}:{order}"] = value
            if best is None or value > best[0]:
                best = (value, mode, order)
        score, mode, order = best
        if mode == "off":
            baseline_cfg[axis] = {"enabled": False, "mode": "auto", "order": 0}
        elif mode == "auto":
            baseline_cfg[axis] = {"enabled": True, "mode": "auto", "order": 0}
        else:
            baseline_cfg[axis] = {"enabled": True, "mode": "order", "order": order}
        scores[axis] = axis_scores
        logs.append(f"{axis}: 最优 {mode}:{order} (score={score:.1f})")
    return BaselineOptimizeResult(
        baseline=baseline_cfg,
        scores=scores,
        spectrum_path=str(spectrum_path),
        logs=logs,
    )


__all__ = ["BaselineOptimizeResult", "optimize_baseline"]
