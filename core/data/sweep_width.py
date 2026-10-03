"""Explicit Hz overrides, without changing raw acquisition metadata."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import Any

from core.data.internal_data_model import Experiment


def apply_sweep_width_overrides(
    experiment: Experiment, params: Mapping[str, Any] | None,
) -> Experiment:
    values = (params or {}).get("sweep_width_hz")
    if values is None:
        return experiment
    if not isinstance(values, Mapping) or not values:
        raise ValueError("sweep_width_hz must be a non-empty logical-axis to Hz mapping")
    known = {dim.logical_axis for dim in experiment.dimensions}
    if set(values) - known:
        raise ValueError(f"Unknown sweep-width axes: {sorted(set(values) - known)}")
    result = copy.deepcopy(experiment)
    for dim in result.dimensions:
        if dim.logical_axis not in values:
            continue
        raw = values[dim.logical_axis]
        if isinstance(raw, bool):
            raise ValueError("Sweep width must be a positive finite number in Hz")
        value = float(raw)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Sweep width must be a positive finite number in Hz")
        dim.sw = value
        dim.sw_source = "explicit_hz"
        dim.sw_note = f"sweep width {dim.logical_axis}: explicit override {value:g} Hz"
    return result
