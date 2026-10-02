"""Conservative axial-artifact filtering: acquisition prior, then spectral evidence.

An experiment name never proves that an axial artifact is present. Unknown or
conflicting metadata leaves peaks untouched. This policy operates on this
project's final, processed spectra: inspect original boundaries only, never
remove interior carrier peaks based on a raw-acquisition States prior. The
States-TPPI boundary mechanism is described in Keeler, section 9.4.4.4. Known
indirect encodings permit inspection, not a claim that an artifact must exist.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from core.data.internal_data_model import AxisRole, Experiment
from core.experiments.registry import get as get_template
from core.peaks import axis_units
from core.qc.peak_detection import Peak

_MODES = {
    "QF": 1,
    "Magnitude": 1,
    "Real": 1,
    "QSEQ": 2,
    "Sequential": 2,
    "TPPI": 3,
    "States": 4,
    "States-N": 4,
    "States-TPPI": 5,
    "States-TPPI-N": 5,
    "Echo-Antiecho": 6,
    "Echo-AntiEcho": 6,
}
MIN_ALIGNED_PEAKS = 6


def _number(value: Any) -> float:
    try:
        number = float(value)
        return number if np.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _fnmode(experiment: Experiment, dimension: Any) -> int:
    block_name = f"acqu{experiment.ndim - int(dimension.logical_axis[1:]) + 1}s"
    raw = experiment.acquisition_parameters.get(block_name, {}).get("FnMODE")
    stored = dimension.acquisition_mode
    mode = _MODES.get(str(stored), int(_number(stored)))
    if raw is not None:
        parsed = int(_number(raw))
        if mode and parsed != mode:
            return 0
        return parsed
    return mode


def _family(experiment: Experiment, axis: str, text: str | None) -> str | None:
    from core.experiment.pulse_pathways import acquisition_family, mc_clause_for

    family = acquisition_family(text, axis, experiment.ndim)
    clause = mc_clause_for(text, axis) if text else None
    return family or (clause.family if clause else None)


def _narrow_peak(
    data: np.ndarray,
    peak: Peak,
    axis: int,
    center: float,
    max_width: float,
    *,
    alignment_tolerance: float,
    baseline: float,
) -> bool:
    """Require a narrow, centered ridge component, not simply a point in a band."""
    if abs(float(peak.position[axis]) - center) > alignment_tolerance:
        return False
    indices = [min(data.shape[i] - 1, max(0, round(v))) for i, v in enumerate(peak.position)]
    indices[axis] = slice(None)
    trace = np.abs(np.asarray(data[tuple(indices)], dtype=float) - baseline)
    top = min(trace.size - 1, max(0, round(peak.position[axis])))
    height = float(trace[top])
    if height <= 0 or not np.isfinite(height):
        return False
    lo = hi = top
    while lo > 0 and trace[lo - 1] >= height * 0.5:
        lo -= 1
    while hi + 1 < trace.size and trace[hi + 1] >= height * 0.5:
        hi += 1
    # Linear half-height crossings express width in the same physical units as
    # the configured linewidth. At an edge mirror only the observed half-width.
    left = float(lo)
    right = float(hi)
    half = height * 0.5
    if lo > 0 and trace[lo] != trace[lo - 1]:
        left -= (trace[lo] - half) / (trace[lo] - trace[lo - 1])
    if hi + 1 < trace.size and trace[hi] != trace[hi + 1]:
        right += (trace[hi] - half) / (trace[hi] - trace[hi + 1])
    width = right - left if lo > 0 and hi + 1 < trace.size else 2.0 * max(top - left, right - top)
    # One sample of uncertainty for an unresolved line, not a fixed deletion band.
    if width > max_width + 1.0:
        return False
    # A broad baseline/stripe must not become a "narrow axial peak" by clipping.
    probe = max(1, int(np.ceil(max_width * 2)))
    neighbors = [trace[i] for i in (top - probe, top + probe) if 0 <= i < trace.size]
    return bool(neighbors) and max(neighbors) < height * 0.25


def filter_axial_peaks(
    data: np.ndarray,
    peaks: list[Peak],
    experiment: Experiment | None,
    *,
    dic: dict[str, Any],
    axes_ppm: list[np.ndarray],
    linewidth_hz_by_nucleus: dict[str, float] | None = None,
) -> tuple[list[Peak], dict[str, Any]]:
    """Keep peaks unless both a mapped acquisition prior and a narrow ridge exist.

    Only aligned edge candidates are removed; no entire edge band/plane is
    masked. Six independent direct-dimension positions spanning a quarter of
    that axis are required. This is a conservative heuristic, not a proof of
    artifact identity. Reasons and the removed count are recorded for review.
    """
    audit: dict[str, Any] = {
        "mode": "automatic",
        "rejected": 0,
        "axes": [],
        "identity": "heuristic_not_artifact_proof",
    }
    if experiment is None:
        audit["reason"] = "missing_experiment_parameters"
        return peaks, audit
    kind = experiment.experiment_type
    template = get_template(kind.name)
    audit["experiment_type"] = kind.name
    if (
        template is None
        or not np.isfinite(kind.confidence)
        or kind.confidence < 0.6
        or kind.name.lower().startswith("generic")
        or experiment.ndim != data.ndim
        or data.ndim not in (2, 3)
        or int(template.constraints.get("ndim", data.ndim)) != data.ndim
    ):
        audit["reason"] = "unknown_or_inconsistent_experiment_type"
        return peaks, audit
    expected_nuclei = [template.direct_nucleus, *template.indirect_nuclei]
    if len(experiment.dimensions) != data.ndim or Counter(
        d.nucleus for d in experiment.dimensions
    ) != Counter(expected_nuclei):
        audit["reason"] = "experiment_type_and_nuclei_disagree"
        return peaks, audit
    if (
        {d.logical_axis for d in experiment.dimensions} != {f"F{i + 1}" for i in range(data.ndim)}
        or sum(d.role is AxisRole.DIRECT for d in experiment.dimensions) != 1
        or len(axes_ppm) != data.ndim
    ):
        audit["reason"] = "inconsistent_dimension_mapping"
        return peaks, audit
    try:
        order = [int(v) for v in dic.get("FDDIMORDER", [])][: data.ndim]
    except (TypeError, ValueError):
        order = []
    if sorted(order) != list(range(1, data.ndim + 1)):
        audit["reason"] = "unknown_spectrum_axis_order"
        return peaks, audit
    from core.experiment.pulse_pathways import read_pulseprogram

    text = read_pulseprogram(experiment)
    direct = experiment.direct_dimension
    if direct is None or int(direct.logical_axis[1:]) not in order:
        audit["reason"] = "unknown_direct_dimension"
        return peaks, audit
    direct_storage = data.ndim - 1 - order.index(int(direct.logical_axis[1:]))
    if direct.nucleus != template.direct_nucleus or direct.logical_axis != f"F{data.ndim}":
        audit["reason"] = "inconsistent_direct_dimension"
        return peaks, audit
    direct_ppm = np.asarray(axes_ppm[direct_storage], dtype=float)
    direct_step = axis_units.ppm_per_point(direct_ppm)
    direct_obs = _number(dic.get(f"FDF{data.ndim}OBS"))
    separation_ppm = axis_units.hz_to_ppm(
        axis_units.linewidth_hz(direct.nucleus, linewidth_hz_by_nucleus),
        direct_obs,
    )
    if direct_step <= 0 or separation_ppm <= 0 or not np.all(np.isfinite(direct_ppm)):
        audit["reason"] = "unknown_direct_axis_units"
        return peaks, audit
    baseline = float(np.median(data))
    rejected: set[int] = set()
    for dim in experiment.dimensions:
        if dim.role is not AxisRole.INDIRECT:
            continue
        logical = int(dim.logical_axis[1:])
        if logical not in order:
            continue
        axis = data.ndim - 1 - order.index(logical)
        item: dict[str, Any] = {"logical_axis": dim.logical_axis, "storage_axis": axis}
        audit["axes"].append(item)
        mode = _fnmode(experiment, dim)
        family = _family(experiment, dim.logical_axis, text)
        item.update(fnmode=mode, pulse_family=family)
        conflict = (
            family == "F1EA"
            and mode != 6
            or family == "F1QF"
            and mode not in (1, 2)
            or family == "F1PH"
            and mode == 6
        )
        if mode not in range(1, 7) or conflict:
            item["reason"] = "encoding_unknown_unsupported_or_conflicting"
            continue
        prefix = f"FDF{logical}"
        obs = _number(dic.get(prefix + "OBS"))
        ppm = np.asarray(axes_ppm[axis], dtype=float)
        step = axis_units.ppm_per_point(ppm)
        if (
            obs <= 0
            or not np.isfinite(dim.sf)
            or dim.sf <= 0
            or abs(obs / dim.sf - 1) > 0.01
            or step <= 0
            or ppm.size < 4
            or not np.all(np.isfinite(ppm))
        ):
            item["reason"] = "unknown_or_inconsistent_axis_units"
            continue
        linewidth = (
            axis_units.hz_to_ppm(
                axis_units.linewidth_hz(dim.nucleus, linewidth_hz_by_nucleus),
                obs,
            )
            / step
        )
        if linewidth <= 0 or linewidth > data.shape[axis] / 8:
            item["reason"] = "insufficient_axis_resolution"
            continue
        # User's final-spectrum contract: never exclude the interior carrier.
        # EXT can create a new edge anywhere; require the original acquisition
        # span before interpreting either displayed boundary as an axial site.
        original_width = dim.sw / dim.sf if np.isfinite(dim.sw) and dim.sw > 0 else 0.0
        ft_size = _number(dic.get(prefix + "FTSIZE"))
        x1, xn = _number(dic.get(prefix + "X1")), _number(dic.get(prefix + "XN"))
        cropped = x1 > 1 or ft_size > 0 and (abs(ft_size - ppm.size) > 0.5 or 0 < xn < ft_size)
        if cropped or original_width <= 0 or abs(step * ppm.size / original_width - 1) > 0.02:
            item["reason"] = "cropped_or_unknown_original_boundaries"
            continue
        centers = [0.0, float(ppm.size - 1)]
        alignment = 0.1 * linewidth
        item.update(
            sites_points=centers,
            minimum_aligned_peaks=MIN_ALIGNED_PEAKS,
            alignment_tolerance_ppm=0.1 * linewidth * step,
            alignment_tolerance_points=alignment,
            maximum_width_ppm=linewidth * step,
            maximum_width_points=linewidth,
            direct_separation_ppm=separation_ppm,
            width_uncertainty_ppm=step,
        )
        removed_here: set[int] = set()
        for center in centers:
            aligned = [
                i
                for i, p in enumerate(peaks)
                if _narrow_peak(
                    data,
                    p,
                    axis,
                    center,
                    linewidth,
                    alignment_tolerance=alignment,
                    baseline=baseline,
                )
            ]
            direct_positions = sorted(
                {
                    float(
                        np.interp(
                            peaks[i].position[direct_storage],
                            np.arange(direct_ppm.size),
                            direct_ppm,
                        )
                    )
                    for i in aligned
                }
            )
            independent: list[float] = []
            for position in direct_positions:
                if not independent or position - independent[-1] >= separation_ppm * (1 - 1e-9):
                    independent.append(position)
            if len(independent) < MIN_ALIGNED_PEAKS or independent[-1] - independent[
                0
            ] < direct_step * direct_ppm.size * 0.25 * (1 - 1e-9):
                continue
            removed_here.update(aligned)
        rejected.update(removed_here)
        item.update(
            rejected=len(removed_here),
            reason=(
                "narrow_aligned_axial_like_ridge"
                if removed_here
                else "insufficient_spectral_evidence"
            ),
        )
    audit["rejected"] = len(rejected)
    audit["reason"] = "axial_like_candidates_removed" if rejected else "insufficient_axial_evidence"
    return [p for i, p in enumerate(peaks) if i not in rejected], audit
