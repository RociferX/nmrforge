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
import subprocess
import sys
from pathlib import Path

# 关键依赖:记录版本让产物可以回答「由哪套工具生成」
_DEPENDENCIES = ("numpy", "scipy", "nmrglue", "matplotlib", "PySide6", "PyYAML")

# backend 层探测到的外部工具版本(进程内缓存)
_TOOL_VERSIONS: dict[str, str] = {}

# 源码提交(进程内缓存;None = 尚未探测)
_GIT_COMMIT: str | None = None
_GIT_DIRTY: bool | None = None


def _git(*args: str) -> tuple[int, str]:
    """在仓库根执行一次 git;失败返回 (非零, "")。"""
    root = Path(__file__).resolve().parent.parent
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:  # noqa: BLE001 - 缺少 git / 超时都不算错误
        return 1, ""
    if done.returncode != 0:
        return done.returncode, ""
    return 0, done.stdout


def _discover_git_commit() -> tuple[str, bool]:
    """探测当前源码提交与工作区是否脏。

    冻结态(AppImage/PyInstaller)内没有 ``.git``,取不到一律返回空串——
    不伪造 ``unknown``,也不猜测。工作区脏只统计已跟踪文件的改动
    (``--untracked-files=no``),避免临时产物把每次运行都标成脏。
    """
    if getattr(sys, "frozen", False):
        return "", False
    code, out = _git("rev-parse", "HEAD")
    if code != 0:
        return "", False
    commit = out.strip()
    code, status = _git("status", "--porcelain", "--untracked-files=no")
    dirty = code == 0 and bool(status.strip())
    return commit, dirty


def git_commit() -> str:
    """当前源码提交哈希(取不到返回空串)。"""
    global _GIT_COMMIT, _GIT_DIRTY
    if _GIT_COMMIT is None:
        _GIT_COMMIT, _GIT_DIRTY = _discover_git_commit()
    return _GIT_COMMIT


def git_commit_dirty() -> bool:
    """工作区是否含未提交的已跟踪文件改动(无 git/冻结态为 False)。"""
    if _GIT_COMMIT is None:
        git_commit()
    return bool(_GIT_DIRTY)


def reset_git_probe() -> None:
    """清空提交探测缓存(测试用)。"""
    global _GIT_COMMIT, _GIT_DIRTY
    _GIT_COMMIT = None
    _GIT_DIRTY = None


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
    # 源码提交:结果可回溯到具体代码状态(取不到时不写该键,不写 unknown)
    commit = git_commit()
    if commit:
        versions["git_commit"] = commit
        versions["git_commit_dirty"] = "1" if git_commit_dirty() else "0"
    return versions


def reset_tool_versions() -> None:
    """清空已登记的外部工具版本(测试用)。"""
    _TOOL_VERSIONS.clear()


__all__ = [
    "dependency_versions",
    "git_commit",
    "git_commit_dirty",
    "register_tool_version",
    "reset_git_probe",
    "registered_tool_versions",
    "reset_tool_versions",
    "software_version",
    "tool_versions",
]
