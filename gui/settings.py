"""GUI 设置读写：对规范后端配置提供稳定的 GUI 视图。

磁盘中的共享字段统一使用 backend.nmrpipe.path、processing.linewidth_hz、
smile.nthread；GUI 专属字段继续保留顶层。读取旧版顶层 nmrpipe_path /
linewidth_hz 时兼容，下一次保存自动迁移为规范结构。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from core.app_paths import local_config_path

SETTINGS_FILENAME = "nmrforge.local.yaml"


def is_appimage() -> bool:
    """是否运行在 AppImage/冻结打包环境。"""
    return bool(os.environ.get("APPIMAGE")) or bool(getattr(sys, "frozen", False))


DEFAULTS: dict = {
    # 以下两个键是 GUI 视图；落盘时转换到 backend/processing 规范结构。
    "nmrpipe_path": "",
    "linewidth_hz": {"1H": 8, "15N": 15, "13C": 20},
    "data_root": "",
    "alignment_tolerance_ppm": {"1H": 0.02, "15N": 0.2, "13C": 0.2},
    "guide": {"first_import_hint_shown": False},
    "pipeline": {"simple_mode": False},
    "smile": {"nthread": 2},
}


def _settings_path() -> Path:
    """GUI 与 Backend 共用的本地覆盖文件。"""
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


def _safe_nthread(raw: object) -> int:
    try:
        value = int(raw or 2)
    except (TypeError, ValueError):
        return 2
    return value if value > 0 else 2


def load_settings() -> dict:
    """读取共享配置并返回 GUI 稳定视图；兼容旧版顶层键。"""
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
    for key in ("nmrpipe_path", "data_root"):
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
    """保存规范配置；旧顶层共享键会在本次写入时迁移。"""
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

    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def data_root_path() -> Path:
    """数据总目录；空或无效时回退用户主目录。"""
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
