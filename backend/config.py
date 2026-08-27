"""后端配置加载:config/nmrforge.yaml 的处理/SMILE/nmrpipe 默认值。

- ``load_processing_defaults()`` 供 GUI 设置对话框读取可编辑默认值;
- 后端在未显式传参时按配置回退(显式 params 优先,无效值回退内置默认)。
"""

from __future__ import annotations

import os
from typing import Any

from core.app_paths import resource_path

# 核素默认估计线宽(Hz):配置 processing.linewidth_hz 可覆盖。
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
# 0.2.199-补24:SMILE 自动线程 = 机器线程数 - thread_offset(可在设置改)
DEFAULT_THREAD_OFFSET = 2


def _auto_nthread(config: dict[str, Any] | None = None) -> int:
    """SMILE 默认线程 = 机器线程数 - offset(smile.thread_offset,缺省 2),
    最小 1(0.2.199-补24:offset 可在设置里改)。"""
    smile = load_config(config).get("smile") or {}
    offset = _as_int(smile.get("thread_offset"), DEFAULT_THREAD_OFFSET)
    return max(1, (os.cpu_count() or 4) - offset)


def load_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """读取 config/nmrforge.yaml,再用 config/nmrforge.local.yaml 覆盖
    (0.2.199-补24:设置对话框写入的本地项对后端生效);config 非空时
    直接返回(测试/本地覆盖注入)。"""
    if config is not None:
        return config
    try:
        import yaml

        raw = yaml.safe_load(
            resource_path("config/nmrforge.yaml").read_text(encoding="utf-8")
        )
    except Exception:  # noqa: BLE001 - 配置缺失/损坏按空配置处理(内置默认兜底)
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    try:
        local_path = resource_path("config") / "nmrforge.local.yaml"
        if local_path.is_file():
            local = yaml.safe_load(
                local_path.read_text(encoding="utf-8")
            ) or {}
            if isinstance(local, dict):
                for key, value in local.items():
                    if isinstance(value, dict) and isinstance(raw.get(key), dict):
                        raw[key] = {**raw[key], **value}
                    else:
                        raw[key] = value
    except Exception:  # noqa: BLE001 - 本地配置损坏不影响内置默认
        pass
    return raw


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
        "nthread": int,               # SMILE 线程,缺省/0=自动(机器线程数-offset)
        "thread_offset": int,         # 自动线程预留数(机器线程数 - offset)
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
        "nthread": _as_int(smile.get("nthread"), 0) or _auto_nthread(cfg),
        "thread_offset": _as_int(
            smile.get("thread_offset"), DEFAULT_THREAD_OFFSET
        ),
        "nmrpipe_path": _as_str(nmrpipe.get("path") or nmrpipe.get("nmrpipe_bin")),
        "ext_lo": _as_str(processing.get("ext_lo"), DEFAULT_EXT_LO),
        "ext_hi": _as_str(processing.get("ext_hi"), DEFAULT_EXT_HI),
    }


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
    """显式值优先,否则配置默认(smile.nthread);无效/非正回退自动(机器线程数-2)。"""
    if value is not None:
        try:
            v = int(value)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return int(load_processing_defaults(config)["nthread"])


def resolve_ext_lo(value: Any, config: dict[str, Any] | None = None) -> str:
    """直接维提取窗口高端(EXT -x1):显式值优先,否则配置默认,再否则内置默认。"""
    if value is not None:
        s = str(value).strip()
        if s:
            return s
    return str(load_processing_defaults(config)["ext_lo"])


def resolve_ext_hi(value: Any, config: dict[str, Any] | None = None) -> str:
    """直接维提取窗口低端(EXT -xn):显式值优先,否则配置默认,再否则内置默认。"""
    if value is not None:
        s = str(value).strip()
        if s:
            return s
    return str(load_processing_defaults(config)["ext_hi"])


def nmrpipe_path(config: dict[str, Any] | None = None) -> str:
    """显式 NMRPipe bin 目录/可执行文件(config backend.nmrpipe.path 优先 nmrpipe_bin)。"""
    return load_processing_defaults(config)["nmrpipe_path"]


__all__ = [
    "DEFAULT_LINEWIDTH_HZ",
    "DEFAULT_POINTS_PER_LINE",
    "DEFAULT_EXT_LO",
    "DEFAULT_EXT_HI",
    "DEFAULT_THREAD_OFFSET",
    "load_config",
    "load_processing_defaults",
    "nmrpipe_path",
    "resolve_ext_hi",
    "resolve_ext_lo",
    "resolve_nthread",
    "resolve_points_per_line",
]
