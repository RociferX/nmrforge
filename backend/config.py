"""后端配置加载:config/nmrforge.yaml 的处理/SMILE/nmrpipe 默认值。

- ``load_processing_defaults()`` 供 GUI 设置对话框读取可编辑默认值;
- 后端在未显式传参时按配置回退(显式 params 优先,无效值回退内置默认)。
"""

from __future__ import annotations

from typing import Any

from core.app_paths import resource_path

# 核素默认估计线宽(Hz):与 backend.script_generator._DEFAULT_LINEWIDTH_HZ 一致,
# 配置 processing.linewidth_hz 可覆盖。
DEFAULT_LINEWIDTH_HZ: dict[str, float] = {
    "1H": 8.0,
    "15N": 15.0,
    "13C": 20.0,
    "31P": 15.0,
    "19F": 20.0,
    "": 15.0,
}
DEFAULT_POINTS_PER_LINE = 2.0
DEFAULT_SMILE_NTHREAD = 2


def load_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """读取 config/nmrforge.yaml;config 非空时直接返回(测试/本地覆盖注入)。"""
    if config is not None:
        return config
    try:
        import yaml

        raw = yaml.safe_load(
            resource_path("config/nmrforge.yaml").read_text(encoding="utf-8")
        )
    except Exception:  # noqa: BLE001 - 配置缺失/损坏按空配置处理(内置默认兜底)
        return {}
    return raw if isinstance(raw, dict) else {}


def _as_float(value: Any, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def _as_int(value: Any, default: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def _as_str(value: Any, default: str = "") -> str:
    return str(value).strip() if value not in (None, "") else default


def load_processing_defaults(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """处理/SMILE/nmrpipe 可编辑默认值(GUI 设置对话框数据源)。

    返回:
    {
        "linewidth_hz": {核素: Hz},   # 无效值回退核素默认
        "points_per_line": float,     # 无效/非正回退 2.0
        "nthread": int,               # SMILE 线程,无效/非正回退 2
        "nmrpipe_path": str,          # 显式 NMRPipe bin 目录/可执行文件,可空
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
        "points_per_line": _as_float(
            processing.get("points_per_line"), DEFAULT_POINTS_PER_LINE
        ),
        "nthread": _as_int(smile.get("nthread"), DEFAULT_SMILE_NTHREAD),
        "nmrpipe_path": _as_str(nmrpipe.get("path") or nmrpipe.get("nmrpipe_bin")),
    }


def linewidth_hz_for(nucleus: str, config: dict[str, Any] | None = None) -> float:
    """核素默认估计线宽(Hz);配置覆盖核素默认表。"""
    key = str(nucleus or "").strip()
    defaults = load_processing_defaults(config)["linewidth_hz"]
    return float(
        defaults.get(key, DEFAULT_LINEWIDTH_HZ.get(key, DEFAULT_LINEWIDTH_HZ[""]))
    )


def resolve_points_per_line(value: Any, config: dict[str, Any] | None = None) -> float:
    """显式值优先,否则配置默认;无效/非正回退 2.0。"""
    if value is not None:
        try:
            v = float(value)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return float(load_processing_defaults(config)["points_per_line"])


def resolve_nthread(value: Any, config: dict[str, Any] | None = None) -> int:
    """显式值优先,否则配置默认(smile.nthread);无效/非正回退 2。"""
    if value is not None:
        try:
            v = int(value)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return int(load_processing_defaults(config)["nthread"])


def nmrpipe_path(config: dict[str, Any] | None = None) -> str:
    """显式 NMRPipe bin 目录/可执行文件(config backend.nmrpipe.path 优先 nmrpipe_bin)。"""
    return load_processing_defaults(config)["nmrpipe_path"]


__all__ = [
    "DEFAULT_LINEWIDTH_HZ",
    "DEFAULT_POINTS_PER_LINE",
    "DEFAULT_SMILE_NTHREAD",
    "linewidth_hz_for",
    "load_config",
    "load_processing_defaults",
    "nmrpipe_path",
    "resolve_nthread",
    "resolve_points_per_line",
]
