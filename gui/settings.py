"""GUI Settings read and write: Provide a stable GUI view of the canonical backend configuration.
Shared fields in the disk are uniformly used backend.nmrpipe.path, processing.linewidth_hz,
smile.nthread; GUI exclusive fields continue to retain the top level. It is compatible when
reading the old top level nmrpipe_path / linewidth_hz, and will automatically migrate to the
canonical structure the next time it is saved."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from core.app_paths import local_config_path
from ui_support.i18n import USER_CHOICES, USER_CONFIG_KEY, normalize_language

SETTINGS_FILENAME = "nmrforge.local.yaml"


def is_appimage() -> bool:
    """Whether to run on AppImage/Freeze packaging environment."""
    return bool(os.environ.get("APPIMAGE")) or bool(getattr(sys, "frozen", False))


DEFAULTS: dict = {
    # The following two keys are the GUI view; switch to the backend/processing canonical structure
    # when loading.
    "nmrpipe_path": "",
    "linewidth_hz": {"1H": 8, "15N": 15, "13C": 20},
    "data_root": "",
    "alignment_tolerance_ppm": {"1H": 0.02, "15N": 0.2, "13C": 0.2},
    "guide": {"first_import_hint_shown": False},
    # interface-language preference (user, 2026-09-21): auto = follow the system, zh/en = pin it
    "language": "auto",
    "pipeline": {"simple_mode": False},
    "smile": {"nthread": 2},
}


def _settings_path() -> Path:
    """GUI Local overlay file shared with Backend."""
    return local_config_path(SETTINGS_FILENAME, packaged=is_appimage())


def _read_raw() -> dict:
    import yaml

    try:
        raw = yaml.safe_load(_settings_path().read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _map_view(raw: object, defaults: dict) -> dict:
    values = raw if isinstance(raw, dict) else {}
    return {key: values.get(key, default) for key, default in defaults.items()}


def _language_value(raw: object) -> str:
    """Interface-language preference: auto (follow the system) / zh / en.

    Locale-style values (``zh_CN`` / ``zh-Hans`` / ``en_US.UTF-8``) go through
    :func:`ui_support.i18n.normalize_language`, the same ruler the language layer uses. Without
    it the dialog showed "follow the system" for a value the language layer honoured, and saving
    any other setting rewrote ``language: zh_CN`` as ``auto`` - silently changing the language the
    user sees.
    """
    text = str(raw or "").strip()
    if not text or text.lower() == "auto":
        return "auto"
    code = normalize_language(text)
    return code if code in USER_CHOICES else "auto"


def _safe_nthread(raw: object) -> int:
    try:
        value = int(raw or 2)
    except (TypeError, ValueError):
        return 2
    return value if value > 0 else 2


def load_settings() -> dict:
    """Reads shared configuration and returns GUI stable view; compatible with legacy top-level
    keys."""
    raw = _read_raw()
    backend = raw.get("backend") if isinstance(raw.get("backend"), dict) else {}
    nmrpipe = (
        backend.get("nmrpipe") if isinstance(backend.get("nmrpipe"), dict) else {}
    )
    processing = (
        raw.get("processing") if isinstance(raw.get("processing"), dict) else {}
    )

    nmrpipe_value = nmrpipe.get("path") or nmrpipe.get("nmrpipe_bin")
    if not nmrpipe_value:
        nmrpipe_value = raw.get("nmrpipe_path", DEFAULTS["nmrpipe_path"])
    linewidth = processing.get("linewidth_hz")
    if not isinstance(linewidth, dict):
        linewidth = raw.get("linewidth_hz")

    return {
        "nmrpipe_path": str(nmrpipe_value or ""),
        "linewidth_hz": _map_view(linewidth, DEFAULTS["linewidth_hz"]),
        "data_root": str(raw.get("data_root", DEFAULTS["data_root"]) or ""),
        "alignment_tolerance_ppm": _map_view(
            raw.get("alignment_tolerance_ppm"),
            DEFAULTS["alignment_tolerance_ppm"],
        ),
        "guide": {
            "first_import_hint_shown": bool(
                (raw.get("guide") or {}).get("first_import_hint_shown", False)
                if isinstance(raw.get("guide"), dict)
                else False
            )
        },
        "pipeline": {
            "simple_mode": bool(
                (raw.get("pipeline") or {}).get("simple_mode", False)
                if isinstance(raw.get("pipeline"), dict)
                else False
            )
        },
        USER_CONFIG_KEY: _language_value(raw.get(USER_CONFIG_KEY)),
        "smile": {
            "nthread": _safe_nthread(
                (raw.get("smile") or {}).get("nthread", 2)
                if isinstance(raw.get("smile"), dict)
                else 2
            )
        },
    }


def _merged_view(settings: dict) -> dict:
    current = load_settings()
    merged = dict(current)
    # Scalar keys are replaced as a whole. The interface language is a scalar key as well: an
    # earlier version merged dict-typed keys only, so the language chosen in the settings dialog
    # was dropped and the stale value on disk was written back, which looked like "I changed it,
    # restarted the program, and it is the same as before".
    for key in ("nmrpipe_path", "data_root", USER_CONFIG_KEY):
        if key in settings:
            merged[key] = settings[key]
    for key in (
        "linewidth_hz",
        "alignment_tolerance_ppm",
        "guide",
        "pipeline",
        "smile",
    ):
        if isinstance(settings.get(key), dict):
            merged[key] = {**current.get(key, {}), **settings[key]}
    return merged


def save_settings(settings: dict) -> Path:
    """Save the canonical configuration; old top-level shared keys will be migrated on this
    write."""
    import yaml

    merged = _merged_view(settings)
    raw = _read_raw()
    raw.pop("nmrpipe_path", None)
    raw.pop("linewidth_hz", None)

    backend = dict(raw.get("backend") or {})
    nmrpipe = dict(backend.get("nmrpipe") or {})
    nmrpipe["path"] = str(merged["nmrpipe_path"] or "").strip()
    backend["nmrpipe"] = nmrpipe
    raw["backend"] = backend

    processing = dict(raw.get("processing") or {})
    processing["linewidth_hz"] = dict(merged["linewidth_hz"])
    raw["processing"] = processing
    smile = {**dict(raw.get("smile") or {}), **merged["smile"]}
    smile.pop("thread_offset", None)
    raw["smile"] = smile
    for key in ("data_root", "alignment_tolerance_ppm", "guide", "pipeline"):
        raw[key] = merged[key]
    raw[USER_CONFIG_KEY] = _language_value(merged.get(USER_CONFIG_KEY))

    # Atomic replacement: an interrupted write leaves either the complete old configuration or
    # the complete new one. The previous direct overwrite could leave half a YAML file, which
    # _read_raw() silently swallows, dropping every setting back to its default.
    from core.project.manager import atomic_write_text

    path = _settings_path()
    atomic_write_text(
        path, yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)
    )
    # let the language layer see the new preference right away (the dialog still says the
    # interface itself takes effect after a restart)
    from ui_support.i18n import reload_user_preference

    reload_user_preference()
    return path


def data_root_path() -> Path:
    """Data directory; fallback to user's main directory when empty or invalid."""
    value = str(load_settings().get("data_root") or "").strip()
    if value:
        path = Path(value).expanduser()
        if path.is_dir():
            return path
    return Path.home()


__all__ = [
    "DEFAULTS",
    "data_root_path",
    "is_appimage",
    "load_settings",
    "save_settings",
]
