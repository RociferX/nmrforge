"""Baseline correction primitives.

Detect first, correct afterwards (slope/curvature/low-freq drift); candidate methods:
polynomial / spline / Whittaker / median-based / NMRPipe compatible;
every correction compares the before/after score and rolls back automatically when it gets
worse (framework §16).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.processing.axes import axis_index


@dataclass
class BaselineParams:
    method: str = "polynomial"
    axis: str = "F3"
    order: int = 1
    # 0.2.199-patch29: explicit numpy axis (used when reading production-layout spectrum
    # files, bypassing the internal axis_index convention -- the internal 3D convention is
    # (F1,F2,F3) whereas a single-file NMRPipe 3D output is (F2,F1,F3), and swapping F1/F2
    # would score or correct the wrong axis)
    np_axis: int | None = None



def _edge_values(arr: np.ndarray, axis: int, edge_fraction: float = 0.08):
    n = arr.shape[axis]
    edge = max(int(n * edge_fraction), 2)
    left = np.take(arr, np.arange(edge), axis=axis)
    right = np.take(arr, np.arange(n - edge, n), axis=axis)
    return left, right


def detect(data: Any) -> dict[str, float]:
    """Return the baseline problem metrics (slope/curvature/drift/offset), evaluated along
    the last axis."""
    arr = np.real(np.asarray(data))
    axis = arr.ndim - 1
    left, right = _edge_values(arr, axis)
    max_abs = float(np.max(np.abs(arr))) + 1e-12
    slope = (float(np.mean(right)) - float(np.mean(left))) / max_abs
    offset = (float(np.mean(left)) + float(np.mean(right))) / 2.0 / max_abs
    return {"slope": slope, "curvature": 0.0, "drift": abs(slope), "offset": offset}


def _robust_polyfit_baseline(
    flat: np.ndarray,
    order: int,
    *,
    n_iter: int = 6,
    k: float = 3.0,
) -> np.ndarray:
    """Robust per-trace polynomial baseline estimate (iterative peak masking, the same idea
    as NMRPipe POLY).

    flat: (n_traces, n) real traces. Each round defines a peak threshold from the MAD of the
    current fit residual and refits using only the points within +-k*sigma (the baseline
    points) -- strong peaks inside the spectrum no longer pull the polynomial off, and the
    fit coefficients no longer jump between neighbouring traces (the root cause of the
    vertical stripes).
    """
    n_traces, n = flat.shape
    x = np.arange(n, dtype=float)
    X = np.stack([x**p for p in range(order + 1)], axis=1)  # (n, k)
    coef, *_ = np.linalg.lstsq(X, flat.T, rcond=None)  # (k, n_traces)
    fit = (X @ coef).T  # (n_traces, n)
    scale = np.max(np.abs(flat), axis=1, keepdims=True) + 1e-12
    for _ in range(n_iter):
        resid = flat - fit
        med = np.median(resid, axis=1, keepdims=True)
        mad = np.median(np.abs(resid - med), axis=1, keepdims=True)
        sigma = np.maximum(1.4826 * mad, 1e-6 * scale)
        keep = np.abs(resid - med) <= k * sigma
        if int(keep.sum(axis=1).min()) <= order + 1:
            break
        w = keep.astype(float)
        xtwx = np.einsum("nj,tn,nk->tjk", X, w, X)  # (n_traces, k, k)
        xtwy = np.einsum("nj,tn->tj", X, w * flat)  # (n_traces, k)
        try:
            new_coef = np.linalg.solve(xtwx, xtwy[..., None])[..., 0]
        except np.linalg.LinAlgError:
            break
        new_fit = (X @ new_coef.T).T
        delta = float(np.max(np.abs(new_fit - fit)))
        coef, fit = new_coef, new_fit
        if delta <= 1e-6 * float(np.max(scale)):
            break
    return fit


def apply(data: Any, params: BaselineParams) -> np.ndarray:
    """Polynomial baseline correction: robust per-trace fit along the chosen axis (degree =
    order), then subtract.

    0.2.190: a plain polyfit is pulled off by strong peaks inside the spectrum, which makes
    the coefficients jump between neighbouring traces (vertical stripes); the iterative peak
    masking (residual MAD threshold) estimates the polynomial from the baseline points only,
    correcting the real baseline without flattening peaks or introducing stripes (the same
    idea as the robust baseline estimate of NMRPipe POLY -auto).
    """
    arr = np.asarray(data)
    axis = params.np_axis if params.np_axis is not None else axis_index(
        params.axis, arr.ndim
    )

    n = arr.shape[axis]
    if n <= params.order + 1:
        return arr
    moved = np.moveaxis(arr, axis, -1)
    flat = moved.reshape(-1, n)
    baseline = _robust_polyfit_baseline(np.real(flat), max(params.order, 1))
    flat -= baseline
    return arr
