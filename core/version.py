"""单一版本源与运行溯源信息(REPORT-008/PROV-009,2026-09-12)。

规则:

- **软件版本只有一处**:``core.__version__``(本模块只读它,不再复制字面量);
  ``pyproject.toml`` 通过 ``dynamic = ["version"]`` 从同一属性取值,
  打包脚本 ``packaging/linux/build_appimage.sh`` 也读同一属性;
- ``dependency_versions()`` 记录关键第三方依赖版本(可重新验证);
- NMRPipe / SMILE 版本无法在导入期安全获取:由 ``backend`` 层在真正解析到
  NMRPipe 安装目录后调用 ``register_tool_version`` 登记(见
  ``backend/nmrpipe_version.py``);登记前查询返回空表,不伪造 ``unknown``;
- ``tool_versions()`` 合并以上两类,供 ``ProjectManager`` 在 ``WorkflowRun``
  启动/结束时写入 ``software_version`` / ``tool_versions``。
"""

from __future__ import annotations

import importlib.metadata
import platform

# 关键依赖:记录版本让产物可以回答「由哪套工具生成」
_DEPENDENCIES = ("numpy", "scipy", "nmrglue", "matplotlib", "PyQt6", "PyYAML")

# backend 层探测到的外部工具版本(进程内缓存)
_TOOL_VERSIONS: dict[str, str] = {}


def software_version() -> str:
    """NMRForge 自身版本(唯一来源 ``core.__version__``)。"""
    from core import __version__

    return str(__version__)


def _distribution_version(name: str) -> str:
    try:
        return str(importlib.metadata.version(name))
    except Exception:  # noqa: BLE001 - 未安装/元数据缺失不算错误
        return ""


def dependency_versions() -> dict[str, str]:
    """Python 与关键依赖版本(空串表示该项不可得,不伪造成 "unknown")。"""
    versions = {"python": platform.python_version()}
    for name in _DEPENDENCIES:
        version = _distribution_version(name)
        if version:
            versions[name.lower()] = version
    return versions


def register_tool_version(name: str, version: str) -> None:
    """登记外部工具版本(NMRPipe/SMILE/bruker 等);空值忽略。"""
    key = str(name).strip().lower()
    value = str(version).strip()
    if key and value:
        _TOOL_VERSIONS[key] = value


def registered_tool_versions() -> dict[str, str]:
    """当前进程已探测到的外部工具版本(副本)。"""
    return dict(_TOOL_VERSIONS)


def tool_versions() -> dict[str, str]:
    """写入 WorkflowRun 的完整工具版本表(自身 + 依赖 + 已探测外部工具)。"""
    versions = {"nmrforge": software_version()}
    versions.update(dependency_versions())
    versions.update(registered_tool_versions())
    return versions


def reset_tool_versions() -> None:
    """清空已登记的外部工具版本(测试用)。"""
    _TOOL_VERSIONS.clear()


__all__ = [
    "dependency_versions",
    "register_tool_version",
    "registered_tool_versions",
    "reset_tool_versions",
    "software_version",
    "tool_versions",
]
