"""ppm/Hz <-> data point conversion: turn a **physical width** parameter into the
number of points of the current spectrum.

Motivation (2026-09-13, user): parameters such as the "axis peak exclusion margin" of
peak picking and the "search window" of peak measurement express a **physical width**
(how many ppm / Hz). Written as a fixed number of points they would drift, because
zero filling changes the point spacing: the ppm width covered by the same "5 points"
changes with the fill factor (k-fold zero filling -> point spacing 1/k -> covered width
1/k), so the **peak set and the measurement window would follow the processing
parameters**. The conversion therefore lives here:

    points = max(minimum, round(width_ppm / ppm_per_point(axis)))

Note the two kinds of "point counts":

- **structural point counts** (the 3-point neighbourhood of a local maximum, the
  +-1-point parabolic template): these must be the grid step itself and **do not scale
  with zero filling** -- zero filling only makes the grid finer and the interpolation
  more accurate;
- **physical point counts** (what this module serves): axis peak exclusion margins,
  search windows and linewidth/step thresholds. These must be defined in ppm/Hz and
  converted at runtime from the current spectrum.

The default physical width is "a multiple of the linewidth of that nucleus" (linewidth
in Hz, see ``DEFAULT_LINEWIDTH_HZ``, aligned with ``processing.linewidth_hz`` in
``config/nmrforge.yaml``; the caller may pass the configured value to override it).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

# default linewidth per nucleus (Hz), aligned with processing.linewidth_hz in config/nmrforge.yaml
DEFAULT_LINEWIDTH_HZ: dict[str, float] = {
    "1H": 8.0,
    "15N": 15.0,
    "13C": 20.0,
    "31P": 15.0,
    "19F": 20.0,
    "": 15.0,
}

# axis peak exclusion margin (above and below axis 0) = multiple of that nucleus linewidth
EDGE_MARGIN_LINEWIDTH_FACTOR = 3.0
# **radius** of the peak-position measurement window = multiple of that nucleus linewidth
MEASUREMENT_WINDOW_LINEWIDTH_FACTOR = 1.5


def ppm_per_point(axis_ppm: Sequence[float]) -> float:
    """Point spacing of an axis array (ppm/point); the median of adjacent differences is
    robust against endpoints and uneven spacing."""
    values = np.asarray(axis_ppm, dtype=float)
    if values.size < 2:
        return 0.0
    steps = np.abs(np.diff(values))
    steps = steps[np.isfinite(steps) & (steps > 0)]
    if steps.size == 0:
        return 0.0
    return float(np.median(steps))


def points_for_ppm(
    axis_ppm: Sequence[float], width_ppm: float, *, minimum: int = 1
) -> int:
    """Physical width (ppm) -> data points (>= ``minimum``); 0 when it cannot be converted."""
    step = ppm_per_point(axis_ppm)
    if step <= 0:
        return 0
    try:
        width = float(width_ppm)
    except (TypeError, ValueError):
        return 0
    if not np.isfinite(width) or width <= 0:
        return 0
    return max(int(minimum), int(round(width / step)))


def ppm_for_points(axis_ppm: Sequence[float], points: int) -> float:
    """Points -> physical width (ppm) (for records/logs)."""
    step = ppm_per_point(axis_ppm)
    return float(max(0, int(points)) * step)


def hz_to_ppm(width_hz: float, obs_mhz: float) -> float:
    """Hz -> ppm (requires the observation frequency of that axis in MHz)."""
    try:
        obs = float(obs_mhz)
        hz = float(width_hz)
    except (TypeError, ValueError):
        return 0.0
    if obs <= 0 or not np.isfinite(obs) or not np.isfinite(hz):
        return 0.0
    return hz / obs


def linewidth_hz(
    nucleus: str,
    linewidth_hz_by_nucleus: Mapping[str, float] | None = None,
) -> float:
    """Estimated linewidth of that nucleus in Hz; the built-in default when the
    configuration does not define one."""
    table = dict(DEFAULT_LINEWIDTH_HZ)
    if linewidth_hz_by_nucleus:
        for key, value in linewidth_hz_by_nucleus.items():
            try:
                table[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
    key = str(nucleus or "")
    return float(table.get(key, table.get("", 15.0)) or 15.0)


def physical_width_ppm(
    nucleus: str,
    obs_mhz: float,
    *,
    factor: float,
    linewidth_hz_by_nucleus: Mapping[str, float] | None = None,
) -> float:
    """Linewidth factor -> physical width (ppm)."""
    return hz_to_ppm(
        float(factor) * linewidth_hz(nucleus, linewidth_hz_by_nucleus), obs_mhz
    )


def edge_margin_ppm(
    nucleus: str,
    obs_mhz: float,
    *,
    linewidth_hz_by_nucleus: Mapping[str, float] | None = None,
    factor: float = EDGE_MARGIN_LINEWIDTH_FACTOR,
) -> float:
    """Default physical width of the axis peak exclusion margin (ppm; one band above and
    one below axis 0)."""
    return physical_width_ppm(
        nucleus,
        obs_mhz,
        factor=factor,
        linewidth_hz_by_nucleus=linewidth_hz_by_nucleus,
    )


def measurement_window_ppm(
    nucleus: str,
    obs_mhz: float,
    *,
    linewidth_hz_by_nucleus: Mapping[str, float] | None = None,
    factor: float = MEASUREMENT_WINDOW_LINEWIDTH_FACTOR,
) -> float:
    """Default physical width of the peak-position measurement search-window radius (ppm)."""
    return physical_width_ppm(
        nucleus,
        obs_mhz,
        factor=factor,
        linewidth_hz_by_nucleus=linewidth_hz_by_nucleus,
    )


def describe_axis(
    axis_ppm: Sequence[float],
    *,
    nucleus: str = "",
    width_ppm: float | None = None,
) -> dict[str, Any]:
    """For records: the nucleus, point spacing, physical width and equivalent number of
    points of one axis."""
    step = ppm_per_point(axis_ppm)
    out: dict[str, Any] = {
        "nucleus": str(nucleus or ""),
        "points": int(np.asarray(axis_ppm).size),
        "ppm_per_point": round(step, 6),
    }
    if width_ppm is not None:
        points = points_for_ppm(axis_ppm, width_ppm)
        out.update(
            {
                "requested_ppm": round(float(width_ppm), 6),
                "points_for_width": int(points),
                "effective_ppm": round(ppm_for_points(axis_ppm, points), 6),
            }
        )
    return out


__all__ = [
    "DEFAULT_LINEWIDTH_HZ",
    "EDGE_MARGIN_LINEWIDTH_FACTOR",
    "MEASUREMENT_WINDOW_LINEWIDTH_FACTOR",
    "describe_axis",
    "edge_margin_ppm",
    "hz_to_ppm",
    "linewidth_hz",
    "measurement_window_ppm",
    "physical_width_ppm",
    "points_for_ppm",
    "ppm_for_points",
    "ppm_per_point",
]
