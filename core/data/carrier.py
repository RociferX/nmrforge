"""Validate explicit carrier positions without changing acquisition metadata."""

from __future__ import annotations

import math
from collections.abc import Mapping
from numbers import Real
from typing import Any


def normalize_carrier_ppm(
    value: Any, *, axes: set[str] | None = None,
) -> dict[str, float]:
    """Return a nonempty logical-axis mapping; zero and negative ppm are valid."""
    if not isinstance(value, Mapping) or not value:
        raise ValueError("carrier_ppm must be a nonempty logical-axis mapping")
    allowed = axes if axes is not None else {"F1", "F2", "F3"}
    result = {}
    for axis, ppm in value.items():
        if axis not in allowed:
            raise ValueError(f"Unknown carrier_ppm axis {axis!r}; available: {sorted(allowed)}")
        if isinstance(ppm, bool) or not isinstance(ppm, Real) or not math.isfinite(ppm):
            raise ValueError(f"carrier_ppm.{axis} must be a finite number in ppm")
        result[axis] = float(ppm)
    return result


def merge_carrier_params(
    params: Mapping[str, Any] | None, carrier_ppm: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Merge an explicit keyword over an already-expanded parameter mapping."""
    result = dict(params or {})
    current = normalize_carrier_ppm(result["carrier_ppm"]) if "carrier_ppm" in result else {}
    if carrier_ppm is not None:
        current.update(normalize_carrier_ppm(carrier_ppm))
    if current:
        result["carrier_ppm"] = current
    return result
