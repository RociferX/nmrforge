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
    optimized: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _default_score(data: np.ndarray, axis: int) -> float:
    """默认评分:core.qc.baseline_quality(轴移到末尾评估)。"""
    from core.qc import baseline_quality

    moved = np.moveaxis(np.asarray(data), axis, -1)
    return float(baseline_quality.evaluate(moved).score)


def _fmt_cfg(cfg: dict[str, Any]) -> str:
    """紧凑打印基线配置(enabled/mode/order)。"""
    return (
        f"enabled={cfg.get('enabled', True)}"
        f",mode={cfg.get('mode', 'auto')}"
        f",order={cfg.get('order', 0)}"
    )


def optimize_baseline(
    experiment: Experiment,
    spectrum_path: Path | str,
    *,
    grid: list[tuple[str, int]] | None = None,
    score_fn: Callable[[np.ndarray, int], float] | None = None,
    good_enough: float | None = 80.0,
    current_baseline: dict[str, dict[str, Any]] | None = None,
) -> BaselineOptimizeResult:
    """逐维基线优化:每维网格 mode∈{off,auto}×order∈{1,2,3},内存内评分,
    选每维最优写回 baseline 配置;最终谱由真实管线渲染(baseline 写回)。

    good_enough:前置判断阈值(0-100,默认 80)——每轴先对当前谱评分,已够好
    则跳过网格搜索并保持当前配置(current_baseline,缺省全维 auto);
    传 None 关闭前置判断(总是网格搜索)。日志逐轴说明「未优化 / 已优化 +
    配置变化 + 分数增益」。score_fn(data, axis_idx) 返回该轴基线质量分
    (默认 baseline_quality);off=不校正。返回 {"baseline", "scores",
    "logs", "optimized", "skipped"}。
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
    current = current_baseline or {}
    default_cfg: dict[str, Any] = {"enabled": True, "mode": "auto", "order": 0}
    baseline_cfg: dict[str, dict[str, Any]] = {}
    scores: dict[str, dict[str, float]] = {}
    logs: list[str] = []
    optimized: list[str] = []
    skipped: list[str] = []
    for axis_idx, axis in enumerate(axes):
        old_cfg = dict(default_cfg)
        old_cfg.update(current.get(axis, {}))
        current_score = float(score_fn(arr, axis_idx))
        if good_enough is not None and current_score >= good_enough:
            baseline_cfg[axis] = old_cfg
            scores[axis] = {"current": current_score}
            skipped.append(axis)
            logs.append(
                f"{axis}: 基线已够好(score={current_score:.1f} >= {good_enough:g}),"
                f"保持当前配置 {_fmt_cfg(old_cfg)},未优化"
            )
            continue
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
            new_cfg: dict[str, Any] = {"enabled": False, "mode": "auto", "order": 0}
        elif mode == "auto":
            new_cfg = {"enabled": True, "mode": "auto", "order": 0}
        else:
            new_cfg = {"enabled": True, "mode": "order", "order": order}
        baseline_cfg[axis] = new_cfg
        scores[axis] = axis_scores
        gain = score - current_score
        if new_cfg == old_cfg and gain <= 1e-9:
            logs.append(
                f"{axis}: 候选未优于当前配置,保持 {_fmt_cfg(new_cfg)} "
                f"(score={score:.1f})"
            )
        else:
            logs.append(
                f"{axis}: 基线已优化 {_fmt_cfg(old_cfg)} → {_fmt_cfg(new_cfg)} "
                f"(score={current_score:.1f} → {score:.1f}, +{gain:.1f})"
            )
            optimized.append(axis)
    logs.append(
        "基线优化总结: "
        + ("未优化 " + ",".join(skipped) if skipped else "未优化 无")
        + "; "
        + ("已优化 " + ",".join(optimized) if optimized else "已优化 无")
    )
    return BaselineOptimizeResult(
        baseline=baseline_cfg,
        scores=scores,
        spectrum_path=str(spectrum_path),
        logs=logs,
        optimized=optimized,
        skipped=skipped,
    )


__all__ = ["BaselineOptimizeResult", "optimize_baseline"]
