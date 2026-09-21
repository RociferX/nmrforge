"""Per-dimension phase correction on projection planes (0.2.199-patch29k, user scheme).

Manual phase adjustment of a 3D spectrum looks at three projection planes: the 1D spectrum of
the direct dimension = number of indirect-1 points + number of indirect-2 points (every F1
gives one F3 trace in the F1-F3 projection and every F2 one F3 trace in the F2-F3 projection),
and each trace is phased individually before the statistically best value is taken. Each
dimension is independent: while projecting, the other two dimensions are phase-corrected with
the current values and summed as complex, otherwise the projection is incoherent (uncorrected
phase on the other dimensions cancels the complex sum).

Implementation: given a fully complex 3D spectrum (already rotated by the current phase of the
other dimensions), for the target dimension:
- complex-sum along the other two axes to obtain two projection planes;
- extract the target-dimension traces from both planes (one per point of the other dimension);
- per trace, lock the peaks -> p1 phase concentration (multi-peak) -> p0 circular mean; take the
  statistical consensus across the traces.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from core.optimization.phase_consensus import (
    search_axis_phase_consensus,
)


def projected_traces(
    complex3d: np.ndarray,
    axis: int,
) -> np.ndarray:
    """The set of projected traces for the target dimension (``axis``).

    Complex-sum along each of the other two axes to get two projection planes; from each plane
    take one trace along ``axis`` per point of the other dimension and concatenate them into
    (n_traces, n_axis).
    """
    arr = np.asarray(complex3d, dtype=np.complex128)
    other = [a for a in range(arr.ndim) if a != axis]
    traces: list[np.ndarray] = []
    for keep in other:
        proj = arr.sum(axis=keep)
        # after dropping ``keep``, the target axis number inside the plane must be remapped
        proj_axis = axis if axis < keep else axis - 1
        moved = np.moveaxis(proj, proj_axis, -1)
        traces.append(moved.reshape(-1, moved.shape[-1]))
    return np.concatenate(traces, axis=0)


def search_projected_axis(
    complex3d: np.ndarray,
    axis: int,
    *,
    max_traces: int = 512,
    min_peaks: int = 2,
    margin: int = 8,
    max_peaks: int = 8,
    sign_mode: str = "uniform",
    progress: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> tuple[float, float, float] | None:
    """Phase every projected trace individually, then take the statistical consensus (the manual
    projection idea).

    Returns (p0, p1, score); None when there is no clean trace.
    """
    traces = projected_traces(complex3d, axis)
    if traces.shape[0] > max_traces:
        idx = np.linspace(0, traces.shape[0] - 1, max_traces).astype(int)
        traces = traces[idx]
    # hand the trace set to the consensus search as a 2D array (axis=-1)
    return search_axis_phase_consensus(
        traces,
        -1,
        min_peaks=min_peaks,
        margin=margin,
        max_peaks=max_peaks,
        sign_mode=sign_mode,
        progress=progress,
        cancel=cancel,
    )


__all__ = ["projected_traces", "search_projected_axis"]
