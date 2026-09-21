"""Baseline quality: slope / curvature / low-freq drift / peak-free deviation / residual
bias (framework §16)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class BaselineQuality:
    slope: float = 0.0
    curvature: float = 0.0
    drift: float = 0.0
    offset: float = 0.0
    stripe: float = 0.0
    score: float = 0.0
    needs_correction: bool = False


def _trace_edge_jumps(real: np.ndarray, axis: int, edge_fraction: float = 0.08) -> np.ndarray:
    """Adjacent-trace difference of the two end baseline levels (median of a band) along
    ``axis`` -- a measure of the striping introduced by per-trace correction.

    If the baseline of one trace is dragged away by a strong peak, its end level differs
    visibly from the neighbouring traces and forms a stripe across the spectrum; the
    distribution of that difference (median / high-percentile jump) is the stripe metric.
    The side bands use the median rather than the mean because real spectrum edges often
    carry strong peaks or t1 noise, and a mean would be lifted by that edge signal and
    raise a false alarm (0.2.199-patch29ek).
    """
    n = real.shape[axis]
    edge = max(int(n * edge_fraction), 2)
    moved = np.moveaxis(real, axis, -1)
    flat = moved.reshape(-1, n)
    left = np.median(flat[:, :edge], axis=1)
    right = np.median(flat[:, -edge:], axis=1)
    return np.abs(np.diff(0.5 * (left + right)))


def stripe_penalty(data: Any, axis: int | None = None) -> float:
    """Per-trace stripe penalty (0..0.5): p95 jump of the adjacent-trace end baseline level
    relative to the median.

    ratio = p95_jump / max(median_jump, dynamic range x 1e-4). ratio <= 8 is unpenalised;
    a ratio of 16 reaches half the penalty (0.25) and >= 32 saturates at 0.5. Combining
    p95 with median side bands is robust against the usual artefacts of real spectra (t1
    noise bands, first-increment bias, strong peaks at the axis edge) -- they affect only a
    few traces (<5%) and no longer rate nearly every spectrum at the 0.5 stripe ceiling
    (0.2.199-patch29ek); obvious striping introduced by the correction (covering >5% of
    the traces) is still penalised strongly.
    """
    arr = np.asarray(data)
    real = np.real(arr)
    if real.ndim < 2 or real.shape[-1] < 8:
        return 0.0
    axis = real.ndim - 1 if axis is None else int(axis)
    jumps = _trace_edge_jumps(real, axis)
    if jumps.size == 0:
        return 0.0
    med = float(np.median(jumps))
    floor = float(np.max(np.abs(real))) * 1e-4 + 1e-12
    ratio = float(np.percentile(jumps, 95)) / max(med, floor)
    if ratio <= 8.0:
        return 0.0
    return float(np.clip((ratio - 8.0) / 48.0, 0.0, 0.5))


def evaluate(
    data: Any, axis: int | None = None, *, max_traces: int = 8192
) -> BaselineQuality:
    """Evaluate baseline quality (along the chosen axis, the last one by default; uses the
    mean of both ends and of the middle, plus the stripe penalty).

    0.2.170: the axis can be chosen -- spectrum quality assessment runs per stored axis
    and keeps the worst instead of looking at the last axis only (an uneven baseline in
    the indirect dimension of a 2D, or in F2/F3 of a 3D, used to go unreported).
    0.2.199-patch29z: sub-sample the traces (on the non-target axes) for speed -- the full
    edge-mean/stripe computation over hundreds of thousands of 3D traces took minutes,
    while the metrics are global means/stripe values that barely change under trace
    sub-sampling (the same idea as ``_decimated`` in baseline optimisation).
    """
    arr = np.asarray(data)
    real = np.real(arr)
    if real.ndim >= 2:
        axis0 = real.ndim - 1 if axis is None else int(axis)
        n = real.shape[axis0]
        n_traces = max(real.size // n, 1)
        if n_traces > max_traces:
            moved = np.moveaxis(real, axis0, -1)
            per = int(np.ceil(n_traces / max_traces))
            slices = [
                (
                    slice(None, None, per)
                    if (a != moved.ndim - 1 and moved.shape[a] >= 2 * per)
                    else slice(None)
                )
                for a in range(moved.ndim)
            ]
            real = np.moveaxis(moved[tuple(slices)], -1, axis0)
    max_abs = float(np.max(np.abs(real))) + 1e-12
    axis = real.ndim - 1 if axis is None else int(axis)
    n = real.shape[axis]
    edge = max(int(n * 0.08), 2)
    if n < 2 * edge:
        # the axis is too short for end/middle bands: treat as no baseline problem
        return BaselineQuality(score=100.0)
    left = float(np.mean(np.take(real, np.arange(edge), axis=axis)))
    right = float(np.mean(np.take(real, np.arange(n - edge, n), axis=axis)))
    center = slice(n // 2 - edge, n // 2 + edge)
    mid = float(np.mean(np.take(real, np.arange(center.start, center.stop), axis=axis)))
    slope = (right - left) / max_abs
    offset = ((left + right) / 2.0) / max_abs
    curvature = abs(left + right - 2.0 * mid) / max_abs
    stripe = stripe_penalty(real, axis)
    score = float(
        np.clip(
            100.0
            * (
                1.0
                - min(
                    1.0,
                    abs(slope) * 4.0
                    + abs(offset) * 2.0
                    + curvature * 6.0
                    + stripe,
                )
            ),
            0.0,
            100.0,
        )
    )
    needs = abs(offset) > 0.02 or abs(slope) > 0.05 or curvature > 0.05 or stripe > 0.1
    return BaselineQuality(
        slope=float(slope),
        curvature=float(curvature),
        drift=float(abs(slope)),
        offset=float(offset),
        stripe=float(stripe),
        score=score,
        needs_correction=bool(needs),
    )


def worst_axis(data: Any) -> tuple[int, BaselineQuality]:
    """Evaluate the baseline per stored axis and take the worst (lowest score): returns
    (axis number, quality).

    0.2.170: spectrum quality assessment uses the worst axis to represent the baseline of
    the whole spectrum, so a single bad axis cannot hide.
    """
    arr = np.asarray(data)
    real = np.real(arr)
    worst_idx, worst = -1, None
    for axis in range(real.ndim):
        m = evaluate(arr, axis=axis)
        if worst is None or m.score < worst.score:
            worst, worst_idx = m, axis
    if worst is None:
        worst = evaluate(arr)
    return worst_idx, worst
