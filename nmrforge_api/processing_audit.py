"""FT requests, generator decisions and exact processing-command evidence."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

FT_NEG_KEYS = frozenset({"ft_neg", "ft_neg_f1", "ft_neg_f2", "flip_f1", "flip_f2"})


def merge_condition_params(
    base: Mapping[str, Any], overrides: Mapping[str, Any],
) -> dict[str, Any]:
    """Recursively merge condition overrides; accept public dotted keys too."""
    from nmrforge_api.sweep import merge_overrides

    result = merge_overrides({}, copy.deepcopy(dict(base)))
    for key, value in overrides.items():
        if "." not in str(key) and isinstance(value, Mapping):
            current = result.get(key)
            result[key] = merge_condition_params(
                current if isinstance(current, Mapping) else {}, value
            )
        else:
            result = merge_overrides(result, {str(key): value})
    return result


def validate_ft_options(params: Mapping[str, Any], *, error: type[Exception] = ValueError) -> None:
    sampling = params.get("sampling")
    if sampling is None:
        return
    if not isinstance(sampling, Mapping):
        raise error("sampling must be a mapping")
    for key in FT_NEG_KEYS | {"ft_alt"}:
        value = sampling.get(key)
        if value is not None and not isinstance(value, bool):
            raise error(f"sampling.{key} must be true, false or null (not a string/number)")


def ft_processing_audit(
    experiment: Any, params: Mapping[str, Any], script: Path | str,
) -> dict[str, Any]:
    from backend.script_generator import _fnmode, _ft_flag_line
    from core.experiment.acquisition_mode_detector import ft_neg_for, sign_sampling_flags

    sampling = dict(params.get("sampling") or {})
    text = Path(script).read_text(encoding="utf-8") if Path(script).is_file() else ""
    commands = [line.strip().rstrip("\\").strip() for line in text.splitlines()
                if not line.lstrip().startswith("#") and re.search(r"-fn\s+FT(?:\s|$)", line)]
    resolved = {}
    for logical in range(1, int(experiment.ndim)):
        axis = f"F{logical}"
        fnmode = _fnmode(experiment, axis)
        flags = _ft_flag_line(fnmode, sampling=sampling, axis=axis,
                              force_neg=ft_neg_for(experiment, fnmode, axis)).split()
        resolved[axis] = {"neg": "-neg" in flags, "alt": "-alt" in flags,
                          "source": "generator_acquisition_rules_and_explicit_overrides"}
    return {"requested": sign_sampling_flags(params), "resolved": resolved,
            "ft_commands": commands, "command_evidence": "script" if commands else "unavailable"}
