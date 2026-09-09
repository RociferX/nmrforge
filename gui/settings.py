"""GUI 设置读写(阶段 C3):config/nmrforge.local.yaml,重启生效。

默认值:线宽 1H 8 / 15N 15 / 13C 20 Hz(接入生成谱图参数);
对齐容差 1H 0.02 / 15N 0.2 / 13C 0.2 ppm(0.2.199-补29fx,按 Poky kr,
接入选峰参考与对齐导出);未配置时显示默认值。
data_root(0.2.199-补29gg,用户):数据总目录,空=用户主目录;导入浏览默认
定位到该目录再找子文件夹。
AppImage 运行时(0.2.199-补29gb):设置文件改存
~/.config/NMRForge/nmrforge.local.yaml,不再依赖包内 config/
(squashfs 只读且运行时路径不是开发期 config 路径)。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from core.app_paths import resource_path

SETTINGS_FILENAME = "nmrforge.local.yaml"


def is_appimage() -> bool:
    """是否运行在 AppImage/冻结打包环境(0.2.199-补29gb)。"""
    return bool(os.environ.get("APPIMAGE")) or bool(
        getattr(sys, "frozen", False)
    )


def data_root_path() -> Path:
    """数据总目录(0.2.199-补29gg):设置 data_root,空/无效回退用户主目录。"""
    value = str((load_settings().get("data_root") or "") or "").strip()
    if value:
        path = Path(value).expanduser()
        if path.is_dir():
            return path
    return Path.home()

DEFAULTS: dict = {
    "nmrpipe_path": "",
    # 0.2.199-补29gg(用户):数据总目录,空=用户主目录;导入浏览默认起点。
    "data_root": "",
    "linewidth_hz": {"1H": 8, "15N": 15, "13C": 20},
    # 0.2.199-补29fx(用户:设置里改容差,与线宽同处):峰对齐/参考匹配容差
    # ppm,默认 Poky kr(Restricted Peak Pick):1H 0.02、15N/13C 0.2。
    "alignment_tolerance_ppm": {"1H": 0.02, "15N": 0.2, "13C": 0.2},
    "guide": {"first_import_hint_shown": False},
    "pipeline": {"simple_mode": False},
    "smile": {"nthread": 2},
}


def _settings_path() -> Path:
    """本地设置文件:AppImage 用 ~/.config/NMRForge/(0.2.199-补29gb);
    开发期优先包内 config/,不可写时回退用户主目录 dotfile。"""
    if is_appimage():
        return Path.home() / ".config" / "NMRForge" / SETTINGS_FILENAME
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
    align_tol = raw.get("alignment_tolerance_ppm")
    if isinstance(align_tol, dict):
        merged["alignment_tolerance_ppm"] = {
            nucleus: align_tol.get(nucleus, default)
            for nucleus, default in DEFAULTS[
                "alignment_tolerance_ppm"
            ].items()
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


__all__ = [
    "DEFAULTS",
    "data_root_path",
    "is_appimage",
    "load_settings",
    "save_settings",
]
