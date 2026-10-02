"""Backend configuration loading: the processing/SMILE/nmrpipe defaults from
nmrforge_data/config/nmrforge.yaml.

- ``load_processing_defaults()`` feeds the editable defaults of the GUI settings dialog;
- the backend falls back to the configuration when no argument is passed explicitly (explicit
params
  win, invalid values fall back to the built-in defaults).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from core.app_paths import local_config_path, resource_path

# default estimated linewidth per nucleus (Hz): config processing.linewidth_hz overrides it.
DEFAULT_LINEWIDTH_HZ: dict[str, float] = {
    "1H": 8.0,
    "15N": 15.0,
    "13C": 20.0,
    "31P": 15.0,
    "19F": 20.0,
    "": 15.0,
}
DEFAULT_POINTS_PER_LINE = 2.0
DEFAULT_EXT_LO = "10.5"
DEFAULT_EXT_HI = "6.5"
# 0.2.199-patch29hq (user): the SMILE thread count defaults to 2 (too many can trigger a load-based
# shutdown); the cap is cores-2.
DEFAULT_SMILE_THREADS = 2


def load_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read the packaged default configuration and overlay the user configuration shared by the GUI
    and the backend.

    Old GUI top-level shared keys are migrated while reading; a non-empty ``config`` is returned
    as is,
    so tests or callers can inject one.
    """
    if config is not None:
        return config
    try:
        import yaml

        raw = yaml.safe_load(resource_path("config/nmrforge.yaml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - missing/corrupt config counts as empty (built-in defaults apply)
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    try:
        local_path = local_config_path("nmrforge.local.yaml")
        if local_path.is_file():
            local = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
            if isinstance(local, dict):
                raw = _deep_merge(raw, _normalize_legacy_local(local))
    except Exception:  # noqa: BLE001 - a corrupt local config never affects the built-in defaults
        pass
    return raw


def _normalize_legacy_local(local: dict[str, Any]) -> dict[str, Any]:
    """Convert the old GUI top-level shared keys into the canonical structure, effective from the
    first backend read.
    """
    normalized = dict(local)
    legacy_path = normalized.pop("nmrpipe_path", None)
    if legacy_path:
        backend = dict(normalized.get("backend") or {})
        nmrpipe = dict(backend.get("nmrpipe") or {})
        nmrpipe.setdefault("path", legacy_path)
        backend["nmrpipe"] = nmrpipe
        normalized["backend"] = backend
    legacy_linewidth = normalized.pop("linewidth_hz", None)
    if isinstance(legacy_linewidth, dict):
        processing = dict(normalized.get("processing") or {})
        processing.setdefault("linewidth_hz", legacy_linewidth)
        normalized["processing"] = processing
    return normalized


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge a local override recursively, keeping nested defaults that are not overridden."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _as_float(value: Any, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def _as_str(value: Any, default: str = "") -> str:
    return str(value).strip() if value not in (None, "") else default


def load_processing_defaults(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Editable processing/SMILE/nmrpipe defaults (the data source of the GUI settings dialog).

    Returns:
    {
        "linewidth_hz": {nucleus: Hz},   # an invalid value falls back to the nucleus default
        "points_per_line": float,        # invalid/non-positive falls back to 2.0
        "nthread": int,                  # SMILE threads; missing/0 = the safe default of 2
        "nmrpipe_path": str,             # explicit NMRPipe bin directory/executable, may be
        empty
    }
    """
    cfg = load_config(config)
    processing = cfg.get("processing") or {}
    smile = cfg.get("smile") or {}
    backend_cfg = cfg.get("backend") or {}
    nmrpipe = backend_cfg.get("nmrpipe") or {}
    linewidth_raw = processing.get("linewidth_hz") or {}
    linewidth_hz: dict[str, float] = {}
    if isinstance(linewidth_raw, dict):
        for nucleus, value in linewidth_raw.items():
            key = str(nucleus).strip()
            fallback = DEFAULT_LINEWIDTH_HZ.get(key, DEFAULT_LINEWIDTH_HZ[""])
            linewidth_hz[key] = _as_float(value, fallback)
    return {
        "linewidth_hz": linewidth_hz,
        "points_per_line": _as_float(processing.get("points_per_line"), DEFAULT_POINTS_PER_LINE),
        "nthread": resolve_nthread(smile.get("nthread"), cfg),
        "nmrpipe_path": _as_str(nmrpipe.get("path") or nmrpipe.get("nmrpipe_bin")),
        "ext_lo": _as_str(processing.get("ext_lo"), DEFAULT_EXT_LO),
        "ext_hi": _as_str(processing.get("ext_hi"), DEFAULT_EXT_HI),
    }


def resolve_points_per_line(value: Any, config: dict[str, Any] | None = None) -> Any:
    """An explicit value wins, otherwise the configured default; invalid or non-positive falls
    back to
    2.0.

    Supports the **per-axis** form ``{"F1": 2.0, "F2": 4.0}`` (user 2026-09-14: "let the
    parameter
    combination table specify the two dimensions separately"): the mapping is passed through as
    is and
    ``zero_fill_plan`` decides the per-axis value and the fallback.
    """
    if isinstance(value, Mapping):
        cleaned: dict[str, float] = {}
        for axis, item in value.items():
            try:
                number = float(item)
            except (TypeError, ValueError):
                continue
            if number > 0:
                cleaned[str(axis)] = number
        if cleaned:
            return cleaned
    if value is not None:
        try:
            v = float(value)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return float(load_processing_defaults(config)["points_per_line"])


def resolve_nthread(value: Any, config: dict[str, Any] | None = None) -> int:
    """SMILE threads: an explicit value (>0) wins, otherwise 2; always clamped to the machine cap
    (cores-2, and 1 when there are 3 or fewer).
    """
    limit = smile_thread_limit(config)
    if value is not None:
        try:
            v = int(value)
            if v > 0:
                return min(v, limit)
        except (TypeError, ValueError):
            pass
    return min(DEFAULT_SMILE_THREADS, limit)


def smile_thread_limit(_config: dict[str, Any] | None = None) -> int:
    """SMILE thread cap = machine cores - 2; with 3 cores or fewer only 1 is allowed (user,
    2026-09-09).
    """
    cores = os.cpu_count() or 4
    if cores <= 3:
        return 1
    return max(1, cores - 2)


def resolve_ext_lo(value: Any, config: dict[str, Any] | None = None) -> str:
    """High end of the direct-dimension extraction window (EXT -x1): explicit, configured,
    built-in.
    """
    if value is not None:
        s = str(value).strip()
        if s:
            return s
    return str(load_processing_defaults(config)["ext_lo"])


def resolve_ext_hi(value: Any, config: dict[str, Any] | None = None) -> str:
    """Low end of the direct-dimension extraction window (EXT -xn): explicit, configured,
    built-in.
    """
    if value is not None:
        s = str(value).strip()
        if s:
            return s
    return str(load_processing_defaults(config)["ext_hi"])


def resolve_ext_window(
    experiment: Any,
    ext_lo: Any = None,
    ext_hi: Any = None,
    config: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Resolve the direct-dimension extraction range.

    If neither endpoint is configured and the direct nucleus is not ``1H``, use the full
    acquired spectral width. The global 10.5–6.5 ppm window is a proton default and must not
    be applied to directly detected nuclei such as ``13C``, ``15N`` or ``31P``. If either
    endpoint is explicit, retain the existing per-endpoint resolution rules. Otherwise,
    compute the full range as ``O1P ± SW/(2·SF)``.
    """
    explicit_lo = ext_lo is not None and bool(str(ext_lo).strip())
    explicit_hi = ext_hi is not None and bool(str(ext_hi).strip())
    if explicit_lo or explicit_hi:
        return resolve_ext_lo(ext_lo, config), resolve_ext_hi(ext_hi, config)

    direct = getattr(experiment, "direct_dimension", None)
    nucleus = str(getattr(direct, "nucleus", "") or "").upper().replace(" ", "")
    if nucleus and nucleus not in {"1H", "H1", "H"}:
        try:
            sf = abs(float(getattr(direct, "sf", 0.0) or 0.0))
            sw = abs(float(getattr(direct, "sw", 0.0) or 0.0))
            center = float(getattr(direct, "o1p", 0.0) or 0.0)
        except (TypeError, ValueError):
            sf = sw = 0.0
            center = 0.0
        if sf > 0.0 and sw > 0.0:
            half_width = sw / sf / 2.0
            return f"{center + half_width:.9g}", f"{center - half_width:.9g}"

    return resolve_ext_lo(None, config), resolve_ext_hi(None, config)


def nmrpipe_path(config: dict[str, Any] | None = None) -> str:
    """Explicit NMRPipe bin directory/executable (config backend.nmrpipe.path before
    nmrpipe_bin).
    """
    return load_processing_defaults(config)["nmrpipe_path"]


__all__ = [
    "DEFAULT_LINEWIDTH_HZ",
    "DEFAULT_POINTS_PER_LINE",
    "DEFAULT_EXT_LO",
    "DEFAULT_EXT_HI",
    "DEFAULT_SMILE_THREADS",
    "load_config",
    "load_processing_defaults",
    "nmrpipe_path",
    "resolve_ext_hi",
    "resolve_ext_lo",
    "resolve_ext_window",
    "resolve_nthread",
    "smile_thread_limit",
    "resolve_points_per_line",
]
