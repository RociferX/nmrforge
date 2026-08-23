"""NUS 采样表读取（nuslist / ser / ser_full 联合判断）。"""

from __future__ import annotations

from pathlib import Path


def read_nuslist(path: Path) -> list[tuple[int, ...]]:
    """读取 nuslist 采样点（每行若干整数索引，跳过注释/空行）。"""
    points: list[tuple[int, ...]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            points.append(tuple(int(tok) for tok in line.split()))
        except ValueError:
            continue
    return points
