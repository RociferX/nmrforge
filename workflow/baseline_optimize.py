"""Dimension-by-dimension baseline optimisation (user plan, G2B-007 point 4). Use
core.qc.baseline_quality to score each dimension (direct + indirect), grid mode ∈ {off, auto} x
order ∈ {1,2,3}, select the optimal write-back baseline configuration for each dimension; the
final spectrum is rendered by the real pipeline (baseline write-back -> render_scripts /
backend.process, uniform and NUS are both supported). score Can be injected. 0.2.132 (stripe
artifact repair): - Score axis correction: score_fn uses the numpy axis corresponding to the
logical axis (axis_index), which is consistent with the correction axis of baseline.apply (the
old code misuses the enumerate serial number, evaluates the wrong axis for the dimensions in
front of the direct dimension, and the indirect axis high score correction "flatten" the entire
column is wrongly selected); - Hard stripe rejection: trace-by-trace correction (plain polyfit,
and the real script POLY Consistent) When an obvious trace gap is introduced on the corrected
spectrum (mean jump at adjacent trace ends >> median level), the candidate is not counted in the
selection -> only off; ensure that the written back POLY will not cause vertical lines to appear
in the final spectrum; - no substantial gain remains off: gain <= 0.5 (scoring unit) no longer
writes POLY -auto."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.data.internal_data_model import Experiment
from core.processing import baseline as baseline_proc
from core.processing.axes import file_axis_index
from ui_support.i18n import tr


@dataclass
class BaselineOptimizeResult:
    """Dimension-wise baseline optimisation results."""

    baseline: dict[str, dict[str, Any]]
    scores: dict[str, dict[str, float]]
    spectrum_path: str
    logs: list[str] = field(default_factory=list)
    optimized: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _default_score(data: np.ndarray, axis: int) -> float:
    """Default rating: core.qc.baseline_quality (axis moved to end for evaluation)."""
    from core.qc import baseline_quality

    moved = np.moveaxis(np.asarray(data), axis, -1)
    return float(baseline_quality.evaluate(moved).score)


def _fmt_cfg(cfg: dict[str, Any]) -> str:
    """Compact printing baseline configuration (enabled/mode/order)."""
    return (
        f"enabled={cfg.get('enabled', True)}"
        f",mode={cfg.get('mode', 'auto')}"
        f",order={cfg.get('order', 0)}"
    )


# Hard stripe rejected candidate scores (finite negative numbers, avoid -inf into GUI/JSON
# serialization).
_VETOED_SCORE = -1e9


def _stripe_ratio(data: np.ndarray, axis: int) -> float:
    """Inter-trace fault indicators (stripes) introduced by trace-by-trace correction: the penalty
    terms of adjacent trace end baseline levels p95 jump/median. and core.qc.baseline_quality
    are of the same measure (median sideband + p95 jump, 0.2.199-patch29ek vs. t1 noise band/The
    strong peak at the edge of the axis is stable); here used for hard rejection."""
    from core.qc.baseline_quality import _trace_edge_jumps

    real = np.real(np.asarray(data))
    n = real.shape[axis]
    if n < 8:
        return 0.0
    jumps = _trace_edge_jumps(real, axis)
    if jumps.size == 0:
        return 0.0
    med = float(np.median(jumps))
    floor = float(np.max(np.abs(real))) * 1e-4 + 1e-12
    return float(np.percentile(jumps, 95)) / max(med, floor)


def _has_stripe_artifact(
    data: np.ndarray,
    axis: int,
    threshold: float = 8.0,
    baseline_ratio: float | None = None,
) -> bool:
    """Correction candidate returns True when there are obvious stripes (sparse strong peak pull
    biased trace fitting). 0.2.199-patch9: The default is an absolute threshold; when
    baseline_ratio (original spectrum striping ratio) is passed, it is changed to relative
    rejection -- only candidates that are significantly worse than the original spectrum
    (>original spectrum + 4) and still exceed the threshold (>8) are rejected. Improvement
    correction is allowed when the original spectrum already has stripes (the result may still
    be >8 but is better than the original spectrum, which is also considered valid)."""
    ratio = _stripe_ratio(data, axis)
    if baseline_ratio is not None:
        return ratio > threshold and ratio > baseline_ratio + 4.0
    return ratio > threshold


def _decimated(data: np.ndarray, axis: int, max_traces: int) -> np.ndarray:
    """Non-axis dimensions are sampled by step size (trace sub-sampling) to control the cost of
    scoring each candidate. The scoring indicator is the global mean/fringe ratio, the trace
    sub-sampling is approximately unchanged (0.2.199-patch8); the 3D maximum axis has tens of
    thousands of traces, the full robust fitting cost is large, and the sub-sampling is <=
    max_traces."""
    n = max(data.shape[axis], 1)
    n_traces = max(data.size // n, 1)
    if data.ndim < 2 or n_traces <= max_traces:
        return data
    per = max(
        1,
        int(math.ceil((n_traces / max_traces) ** (1.0 / (data.ndim - 1)))),
    )
    slices = [
        (
            slice(None, None, per)
            if (a != axis and data.shape[a] >= 2 * per)
            else slice(None)
        )
        for a in range(data.ndim)
    ]
    return data[tuple(slices)]


def _peak_free_traces(
    real: np.ndarray,
    axis: int,
    *,
    max_traces: int,
    min_traces: int = 64,
) -> tuple[np.ndarray | None, np.ndarray]:
    """Take traces without strong peaks along the axis (sequence retention) as the basis for
    baseline scoring. User plan (0.2.199-patch29u): baseline optimisation: first find the
    position without peaks and then sample - strong peaks will bias the baseline estimate, only
    in peak-free trace upper fitting/Ratings reflect reality baseline; by the way, the amount of
    fitting is greatly reduced (the number of peak-free traces in a dense spectrum is much
    smaller than that of the full spectrum). Determination of trace containing peaks: | trace |
    maximum value >= max (global 99th percentile x 0.5, global maximum."""
    moved = np.moveaxis(np.real(real), axis, -1)
    flat = moved.reshape(-1, moved.shape[-1])
    amp = np.abs(flat)
    thr = max(
        float(np.percentile(amp, 99.0)) * 0.5,
        float(np.max(amp)) * 0.02,
    )
    keep = np.where(np.max(amp, axis=1) < thr)[0]
    if keep.size < min_traces:
        return None, keep
    if keep.size > max_traces:
        idx = np.linspace(0, keep.size - 1, max_traces).astype(int)
        keep = keep[idx]
    return flat[keep], keep


def optimize_baseline(
    experiment: Experiment,
    spectrum_path: Path | str,
    *,
    grid: list[tuple[str, int]] | None = None,
    score_fn: Callable[[np.ndarray, int], float] | None = None,
    progress: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    max_traces: int = 4096,
) -> BaselineOptimizeResult:
    """Dimension-by-dimension baseline optimisation: the grid of every dimension is
    mode∈{off,auto} × order∈{1,2,3}, scored in memory, and the best configuration per
    dimension is written back to the baseline config. The whole grid is optimised directly; the
    off configuration is kept when there is no substantial gain (<=0.5) or a candidate
    introduces visible striping. The log explains the configuration change and the score gain
    axis by axis. score_fn(data, np_axis) returns the baseline quality score of that axis
    (default baseline_quality); off=no correction. progress outputs progress
    axis-by-axis/candidate. When
    cancel is set, check and throw "task canceled" between candidates (0.2.199-patch7: 3D robust
    trace-by-trace fitting can reach tens of thousands of traces, requiring progress and
    cancellation); max_traces: trace subsampling upper limit of candidate score (0.2.199-patch8,
    default 4096, the smaller the faster, the score is approximately unchanged; off axis with
    score >= 95 is skipped candidate directly remains off). Returns {"baseline", "scores",
    "logs", "optimized", "skipped"}."""
    import nmrglue as ng

    _dic, data = ng.pipe.read(str(spectrum_path))
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        arr = arr.real
    axes = [dim.logical_axis for dim in experiment.dimensions]
    grid = grid if grid is not None else [
        ("off", 0),
        # It is order 1, which is the same as order1. It removes duplicates and does not repeat the
        # scoring.
        ("auto", 1),
        ("order", 2),
        ("order", 3),
    ]
    score_fn = score_fn or _default_score
    off_cfg: dict[str, Any] = {"enabled": False, "mode": "auto", "order": 0}
    # Keep off when there is no substantial gain (scoring unit), avoid nonsense/harmful writeback.
    min_gain = 0.5
    baseline_cfg: dict[str, dict[str, Any]] = {}
    scores: dict[str, dict[str, float]] = {}
    logs: list[str] = []
    optimized: list[str] = []
    unchanged: list[str] = []
    for index, axis in enumerate(axes, start=1):
        if progress is not None:
            progress(
                tr(
                    "Baseline optimisation(memory score): axis {p0}({p1}/{p2}),scoring every "
                    "candidate",
                    p0=axis,
                    p1=index,
                    p2=len(axes),
                )
            )
        _vetoed_count = 0
        _non_off = sum(1 for m, _o in grid if m != "off")
        np_axis = file_axis_index(axis, arr.ndim)

        # 0.2.199-patch9: No subsampling is performed when the original spectrum has obvious stripes
        # (>8) -- thin stripes may be missed by subsampling, and stripe veto/The rating must be
        # fully assessed to be correct.; clean spectrum is subsampled.
        orig_ratio = _stripe_ratio(arr, np_axis)
        # 0.2.199-patch29u: First find the peak-free trace and then sample it as the scoring basis
        # (user plan) to avoid strong peaks biasing the baseline estimate; the peak-free trace is
        # not enough to fall back to full trace downsampling.
        base2d, _keep_idx = _peak_free_traces(
            arr, np_axis, max_traces=max_traces
        )
        if base2d is None:
            base = (
                arr
                if orig_ratio > 8.0
                else _decimated(arr, np_axis, max_traces)
            )
            score_axis = np_axis
            base_ratio = orig_ratio
        else:
            base = base2d
            score_axis = -1
            base_ratio = _stripe_ratio(base, -1)
        current_score = float(score_fn(base, score_axis))
        # When the baseline is good (>= 95), the entire axis candidate is skipped and kept off
        # directly (full grid fitting is omitted).
        if current_score >= 95.0:
            baseline_cfg[axis] = dict(off_cfg)
            scores[axis] = {"off:0": current_score}
            logs.append(
                tr(
                    "{p0}: The baseline is good (score={p1:.1f}≥95),keep off (candidates "
                    "skipped)",
                    p0=axis,
                    p1=current_score,
                )
            )
            unchanged.append(axis)
            continue
        axis_scores: dict[str, float] = {}
        best: tuple[float, str, int] | None = None
        for mode, order in grid:
            if cancel is not None and cancel():
                raise RuntimeError(tr("Task cancelled: Baseline optimisation terminated by user"))
            if mode == "off":
                work = base  # Score directly without correction, no need to copy.
            else:
                work = base.copy()
                work = baseline_proc.apply(
                    work,
                    baseline_proc.BaselineParams(
                        method="polynomial",
                        axis=axis,
                        order=max(order, 1),
                        np_axis=score_axis,
                    ),

                )
                # Hard stripe rejection: If there is an obvious trace gap after correction, the
                # candidate cannot be written back (otherwise, vertical lines will appear in the
                # final spectrum); relative rejection -- only reject candidates that are
                # significantly worse than the base (0.2.199-patch9).
                if _has_stripe_artifact(
                    work, score_axis, baseline_ratio=base_ratio
                ):
                    axis_scores[f"{mode}:{order}"] = _VETOED_SCORE
                    _vetoed_count += 1
                    continue
            value = float(score_fn(work, score_axis))
            axis_scores[f"{mode}:{order}"] = value
            if best is None or value > best[0]:
                best = (value, mode, order)
            if progress is not None:
                progress(
                    tr(
                        "Baseline optimisation(memory score): {p0} {p1}:{p2} "
                        "score={p3:.1f}",
                        p0=axis,
                        p1=mode,
                        p2=order,
                        p3=value,
                    )
                )
        if best is None:
            best = (float(current_score), "off", 0)
            axis_scores["off:0"] = float(current_score)
        score, mode, order = best
        if mode == "off":
            new_cfg: dict[str, Any] = {"enabled": False, "mode": "auto", "order": 0}
        elif mode == "auto":
            new_cfg = {"enabled": True, "mode": "auto", "order": 0}
        else:
            new_cfg = {"enabled": True, "mode": "order", "order": order}
        gain = score - current_score
        if gain <= min_gain:
            # Trace-by-trace correction has no substantial gain -> keep off (0.2.132: no longer
            # write POLY -auto, to avoid trace-by-trace mean correction introducing stripes when
            # there is no baseline problem).
            baseline_cfg[axis] = dict(off_cfg)
            if _vetoed_count == _non_off:
                reason = tr("All candidates were rejected by the striping check")
            elif _vetoed_count > 0:
                reason = (
                    tr(
                    "Some candidates were rejected by striping, and the rest had insufficient "
                    "gain",
                )
                )
            else:
                reason = tr("Candidate is not better than current configuration")
            logs.append(
                tr("{p0}: {p1}, keep off (score={p2:.1f})", p0=axis, p1=reason, p2=score)
            )
            unchanged.append(axis)
        else:
            baseline_cfg[axis] = new_cfg
            logs.append(
                tr(
                    "{p0}: Baseline has been optimised {p1} → {p2} (score={p3:.1f} → {p4:.1f}, "
                    "+{p5:.1f})",
                    p0=axis,
                    p1=_fmt_cfg(off_cfg),
                    p2=_fmt_cfg(new_cfg),
                    p3=current_score,
                    p4=score,
                    p5=gain,
                )
            )
            optimized.append(axis)
        scores[axis] = axis_scores
    logs.append(
        tr("Baseline optimisation summary: ")
        + (tr("optimisation ") + ",".join(optimized) if optimized else tr("optimisation None"))
        + "; "
        + (tr(
            "Not optimisation ",
        ) + ",".join(unchanged) if unchanged else tr(
            "Not optimisation "
            "None",
        ))
    )
    return BaselineOptimizeResult(
        baseline=baseline_cfg,
        scores=scores,
        spectrum_path=str(spectrum_path),
        logs=logs,
        optimized=optimized,
        skipped=[],
    )


__all__ = ["BaselineOptimizeResult", "optimize_baseline"]
