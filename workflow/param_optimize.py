"""Post-processing parameter optimisation (phase p0/p1, baseline) -- Reconstruction only once, in-
memory optimisation. Design principles (Framework §23/§35): Expensive NUS reconstruction is only
executed once; phase / baseline, etc. Downstream parameters are optimised in numpy memory on the
final spectrum, without triggering re-reconstruction. NUS is logically consistent with non-NUS:
get the final spectrum first (reconstruct_nus or process), then optimisation. Usage: results =
optimize_post_parameters(spectrum_array) print(format_results(results)) # Reuse smile_optimize
form/Report save_report(results, Path("param_report.json"))."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

import numpy as np

from core.processing import baseline, phase
from core.qc import spectrum_quality
from ui_support.i18n import tr
from workflow.smile_optimize import SmileParameterResult, save_report

ParamResult = SmileParameterResult  # Generalized naming: post-processing parameter group results.

__all__ = [
    "ParamResult",
    "default_post_grid",
    "apply_post_params",
    "optimize_post_parameters",
    "format_results",
    "save_report",
]


def format_results(results: list[ParamResult]) -> str:
    """Render the candidate list as a parameter combination + scoring table
    (p0/p1/baseline_order)."""
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
    """Default post-processing parameter grid: p0 x p1 x baseline order (0=no baseline)."""
    grid: list[dict[str, Any]] = []
    for p0 in p0_values:
        for p1 in p1_values:
            for order in baseline_orders:
                grid.append({"p0": p0, "p1": p1, "baseline_order": order})
    return grid


def apply_post_params(data: np.ndarray, params: dict[str, Any]) -> np.ndarray:
    """Apply phase correction + baseline correction in memory (along direct dimension = last
    axis)."""
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
    sign_mode: str = "auto",
    progress: Callable[[int, int, str], None] | None = None,
    on_result: Callable[[ParamResult], None] | None = None,
) -> list[ParamResult]:
    """Apply phase / baseline to the final spectrum group by group in memory and score, returning a
    list of candidates in descending order of total score. No reconstruction / subprocess calls
    are made -- reconstruction (NUS) or processing (uniform) has been completed once
    upstream.

    sign_mode goes to :func:`core.qc.spectrum_quality.evaluate`: a caller that knows the
    experiment type passes ``"uniform"``/``"mixed"`` (the in-pipeline convention),
    otherwise the ``"auto"`` default is used (judge single-sign positive, single-sign
    negative or both signs coexisting from the spectrum itself).
    """
    grid = grid if grid is not None else default_post_grid()
    results: list[ParamResult] = []
    total = len(grid)
    for index, params in enumerate(grid, start=1):
        if progress is not None:
            progress(index, total, tr("parameter group {p0}", p0=params))
        result = ParamResult(params=dict(params))
        try:
            corrected = apply_post_params(data, params)
            quality = spectrum_quality.evaluate(corrected, sign_mode=sign_mode)
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
