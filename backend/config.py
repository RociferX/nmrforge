"""后端配置加载:config/nmrforge.yaml 的处理/SMILE/nmrpipe 默认值。

- ``load_processing_defaults()`` 供 GUI 设置对话框读取可编辑默认值;
- 后端在未显式传参时按配置回退(显式 params 优先,无效值回退内置默认)。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from core.app_paths import local_config_path, resource_path

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
# 0.2.199-补29hq(用户):SMILE 线程数默认 2(过高可能触发高负载关机);上限=机器核数-2。
DEFAULT_SMILE_THREADS = 2


def load_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """读取打包默认配置,再用 GUI/Backend 共用的用户配置覆盖。

    旧 GUI 顶层共享键在读取时迁移;config 非空时直接返回,供测试或调用方注入。
    """
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
        local_path = local_config_path("nmrforge.local.yaml")
        if local_path.is_file():
            local = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
            if isinstance(local, dict):
                raw = _deep_merge(raw, _normalize_legacy_local(local))
    except Exception:  # noqa: BLE001 - 本地配置损坏不影响内置默认
        pass
    return raw


def _normalize_legacy_local(local: dict[str, Any]) -> dict[str, Any]:
    """把旧 GUI 顶层共享键转换为规范结构,首次 Backend 读取即生效。"""
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
    """递归合并本地覆盖,保留未覆盖的嵌套默认项。"""
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
    """处理/SMILE/nmrpipe 可编辑默认值(GUI 设置对话框数据源)。

    返回:
    {
        "linewidth_hz": {核素: Hz},   # 无效值回退核素默认
        "points_per_line": float,     # 无效/非正回退 2.0
        "nthread": int,               # SMILE 线程,缺省/0=安全默认 2
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
        "nthread": resolve_nthread(smile.get("nthread"), cfg),
        "nmrpipe_path": _as_str(nmrpipe.get("path") or nmrpipe.get("nmrpipe_bin")),
        "ext_lo": _as_str(processing.get("ext_lo"), DEFAULT_EXT_LO),
        "ext_hi": _as_str(processing.get("ext_hi"), DEFAULT_EXT_HI),
    }


def resolve_points_per_line(
    value: Any, config: dict[str, Any] | None = None
) -> Any:
    """显式值优先,否则配置默认;无效/非正回退 2.0。

    支持**逐轴**写法 ``{"F1": 2.0, "F2": 4.0}``(2026-09-14 用户「参数组合表
    按两个维度分别指定」):映射原样透传,逐轴取值/回退由 ``zero_fill_plan`` 决定。
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
    """SMILE 线程数:显式值(>0)优先,否则默认 2;一律 clamp 到机上限(核数-2,≤3核=1)。"""
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
    """SMILE 线程上限 = 机器核数-2;核数≤3 只允许 1(用户,2026-09-09)。"""
    cores = os.cpu_count() or 4
    if cores <= 3:
        return 1
    return max(1, cores - 2)


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
    "DEFAULT_SMILE_THREADS",
    "load_config",
    "load_processing_defaults",
    "nmrpipe_path",
    "resolve_ext_hi",
    "resolve_ext_lo",
    "resolve_nthread",
    "smile_thread_limit",
    "resolve_points_per_line",
]
