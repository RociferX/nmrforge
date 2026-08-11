"""NUS 采样表读取（nuslist / ser / ser_full 联合判断）。"""

from __future__ import annotations

from pathlib import Path


def read_nuslist(path: Path) -> list[tuple[int, ...]]:
    """读取 nuslist 采样点。"""
    raise NotImplementedError("Phase 1: 实现 nuslist 读取")
