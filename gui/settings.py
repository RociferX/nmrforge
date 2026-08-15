"""GUI 设置读写(阶段 C3):config/nmrforge.local.yaml,重启生效。

默认值:线宽 1H 8 / 15N 15 / 13C 20 Hz,points_per_line 2,
SMILE 线程上限 2(与后端护栏一致);未配置时显示默认值。
"""

from __future__ import annotations

import os
from pathlib import Path

from core.app_paths import resource_path

SETTINGS_FILENAME = "nmrforge.local.yaml"

DEFAULTS: dict = {
    "nmrpipe_path": "",
    "linewidth_hz": {"1H": 8, "15N": 15, "13C": 20},
    "points_per_line": 2,
    "smile_thread_cap": 2,
    "guide": {"first_import_hint_shown": False},
    "pipeline": {
        "simple_mode": False,
        "fingerprint_check": True,
        "outdated_enabled": True,
    },
}


def _settings_path() -> Path:
    """本地设置文件:优先包内 config/,不可写时回退用户目录。"""
    try:
        candidate = resource_path("config") / SETTINGS_FILENAME
        if os.access(candidate.parent, os.W_OK):
            return candidate
    except Exception:  # noqa: BLE001
        pass
    return Path.home() / f".{SETTINGS_FILENAME}"


def load_settings() -> dict:
    """读取本地设置(缺失/损坏返回默认值)。"""
    import yaml

    path = _settings_path()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    merged = {
        key: raw.get(key, default)
        for key, default in DEFAULTS.items()
    }
    linewidth = raw.get("linewidth_hz")
    if isinstance(linewidth, dict):
        merged["linewidth_hz"] = {
            nucleus: linewidth.get(nucleus, default)
            for nucleus, default in DEFAULTS["linewidth_hz"].items()
        }
    guide = raw.get("guide")
    if isinstance(guide, dict):
        merged["guide"] = {
            "first_import_hint_shown": bool(
                guide.get("first_import_hint_shown", False)
            )
        }
    pipeline = raw.get("pipeline")
    if isinstance(pipeline, dict):
        merged["pipeline"] = {
            "simple_mode": bool(pipeline.get("simple_mode", False)),
            "fingerprint_check": bool(
                pipeline.get("fingerprint_check", True)
            ),
            "outdated_enabled": bool(
                pipeline.get("outdated_enabled", True)
            ),
        }
    return merged


def save_settings(settings: dict) -> Path:
    """保存本地设置(合并默认值后落盘)。"""
    import yaml

    merged = {
        key: settings.get(key, default)
        for key, default in DEFAULTS.items()
    }
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(merged, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


__all__ = ["DEFAULTS", "load_settings", "save_settings", "settings_path"]


def settings_path() -> Path:
    return _settings_path()
