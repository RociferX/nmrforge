"""Explicit logical-axis CAR requests applied to actual conversion scripts."""

from __future__ import annotations

import hashlib
import math
import shlex
from typing import Any

from backend.bruker_workflow import _axis_letters, apply_fid_com_overrides
from backend.conversion_provenance import _bruk2pipe_commands
from core.data.carrier import normalize_carrier_ppm
from core.data.internal_data_model import Experiment


def apply_carrier_request(
    text: str, experiment: Experiment, requested: dict[str, float],
) -> tuple[str, dict[str, Any]]:
    """Fail before execution if any requested axis cannot be represented in the script."""
    requested = normalize_carrier_ppm(
        requested, axes={dim.logical_axis for dim in experiment.dimensions},
    )
    letters = _axis_letters(experiment.ndim)
    overrides = {f"{letters[axis]}CAR": format(ppm, ".17g") for axis, ppm in requested.items()}
    commands = _bruk2pipe_commands(text)
    if not commands:
        raise ValueError("Cannot apply carrier_ppm: conversion script lacks a bruk2pipe command")
    patched = text
    axes = {}

    def arguments_in(line: str) -> list[str]:
        joined = " ".join(part.strip().removesuffix("\\").strip() for part in line.splitlines())
        return shlex.split(joined, comments=True)[1:]

    for command in commands:
        arguments = arguments_in(command["line"])
        for key in overrides:
            if arguments.count(f"-{key}") != 1:
                raise ValueError(f"Cannot apply carrier_ppm: command lacks a unique -{key} option")
        new_command, _ = apply_fid_com_overrides(command["line"], overrides)
        actual_arguments = arguments_in(new_command)
        for axis, ppm in requested.items():
            key = f"{letters[axis]}CAR"
            index = actual_arguments.index(f"-{key}") + 1
            if index >= len(actual_arguments):
                raise ValueError(f"Cannot apply carrier_ppm.{axis}: option has no value")
            actual = float(actual_arguments[index])
            if not math.isfinite(actual) or actual != ppm:
                raise ValueError(f"Cannot apply carrier_ppm.{axis}: {ppm} resolved as {actual}")
            axes[axis] = {"requested": ppm, "resolved": actual,
                          "source": "explicit_ppm", "conversion_key": key}
        patched = patched.replace(command["line"], new_command, 1)
    return patched, {"axes": axes, "script_sha256": hashlib.sha256(patched.encode()).hexdigest(),
                     "command_evidence": patched}
