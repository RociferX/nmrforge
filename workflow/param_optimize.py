"""后处理参数优化（相位 p0/p1、基线）——只重构一次，内存内优化。

设计原则（框架 §23/§35）：昂贵的 NUS reconstruction 只执行一次；相位/基线等
下游参数在最终谱上以 numpy 内存内优化，不触发重新重构。
NUS 与非 NUS 逻辑一致：先得到终谱（reconstruct_nus 或 process），再优化。

用法：
    results = optimize_post_parameters(spectrum_array)
    print(format_results(results))      # 复用 smile_optimize 的表格/报告
    save_report(results, Path("param_report.json"))
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

import numpy as np

from core.processing import baseline, phase
from core.qc import spectrum_quality
from workflow.smile_optimize import SmileParameterResult, save_report

ParamResult = SmileParameterResult  # 泛化命名：后处理参数组结果

__all__ = [
    "ParamResult",
    "default_post_grid",
    "apply_post_params",
    "optimize_post_parameters",
    "format_results",
    "save_report",
]


def format_results(results: list[ParamResult]) -> str:
    """把候选列表渲染为参数组合 + 评分表格（p0/p1/baseline_order）。"""
    header = (
        f"{'p0':>7} {'p1':>6} {'base':>5} "
        f"{'decision':>8} {'overall':>7} {'snr':>5} "
        f"{'phase':>5} {'baseQ':>5} {'art':>5}"
    )
    lines = [header, "-" * len(header)]
    for result in results:
        comp = result.components
        lines.append(
            f"{result.params.get('p0', 0):>7} {result.params.get('p1', 0):>6} "
            f"{result.params.get('baseline_order', 0):>5} "
            f"{result.decision:>8} {result.overall:>7.1f} "
            f"{comp.get('snr', 0):>5.0f} {comp.get('phase', 0):>5.0f} "
            f"{comp.get('baseline', 0):>5.0f} {comp.get('artifact', 0):>5.0f}"
        )
    return "\n".join(lines)


def default_post_grid(
    p0_values: tuple[float, ...] = (-45.0, -22.5, 0.0, 22.5, 45.0),
    p1_values: tuple[float, ...] = (-20.0, 0.0, 20.0),
    baseline_orders: tuple[int, ...] = (0, 1),
) -> list[dict[str, Any]]:
    """默认后处理参数网格：p0 × p1 × 基线阶数（0=不做基线）。"""
    grid: list[dict[str, Any]] = []
    for p0 in p0_values:
        for p1 in p1_values:
            for order in baseline_orders:
                grid.append({"p0": p0, "p1": p1, "baseline_order": order})
    return grid


def apply_post_params(data: np.ndarray, params: dict[str, Any]) -> np.ndarray:
    """内存内应用相位校正 + 基线校正（沿直接维=最后一个轴）。"""
    axis = f"F{data.ndim}"
    out = phase.apply(
        data,
        phase.PhaseParams(
            p0=float(params.get("p0", 0.0)),
            p1=float(params.get("p1", 0.0)),
            axis=axis,
            source="post_optimizer",
        ),
    )
    order = int(params.get("baseline_order", 0))
    if order > 0:
        out = baseline.apply(
            out,
            baseline.BaselineParams(method="polynomial", axis=axis, order=order),
        )
    return out


def optimize_post_parameters(
    data: np.ndarray,
    grid: list[dict[str, Any]] | None = None,
    *,
    progress: Callable[[int, int, str], None] | None = None,
    on_result: Callable[[ParamResult], None] | None = None,
) -> list[ParamResult]:
    """对终谱在内存内逐组应用相位/基线并评分，返回按总分降序的候选列表。

    不做任何重构/子进程调用——重构（NUS）或处理（uniform）已在上游完成一次。
    """
    grid = grid if grid is not None else default_post_grid()
    results: list[ParamResult] = []
    total = len(grid)
    for index, params in enumerate(grid, start=1):
        if progress is not None:
            progress(index, total, f"参数组 {params}")
        result = ParamResult(params=dict(params))
        try:
            corrected = apply_post_params(data, params)
            quality = spectrum_quality.evaluate(corrected)
            result.decision = quality.decision.value
            result.overall = quality.score.overall
            result.components = asdict(quality.score.components)
        except Exception as exc:  # noqa: BLE001
            result.decision = "error"
            result.message = str(exc)
        results.append(result)
        if on_result is not None:
            on_result(result)
    results.sort(key=lambda r: r.overall, reverse=True)
    return results
