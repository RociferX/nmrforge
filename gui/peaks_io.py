"""峰表 IO 与 Poky 导出(G2B-005)。

0.2.164 起直接使用 Shared Contract 的 core.peaks.peak_table(删除本地
等价实现与 _use_core_peaks 回退分支,避免两套格式漂移)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.peaks.peak_table import (
    export_peaks_poky as core_export,
)
from core.peaks.peak_table import (
    import_peaks_poky as core_import,
)
from core.peaks.peak_table import (
    load_peaks as core_load,
)
from core.peaks.peak_table import (
    normalize_poky_label as core_normalize_label,
)
from core.peaks.peak_table import (
    poky_label_is_valid as core_label_valid,
)
from core.peaks.peak_table import (
    save_peaks as core_save,
)


def load_peaks(path: Path | str) -> list[dict[str, Any]]:
    """读取峰表 CSV(core.peaks 实现)。"""
    return list(core_load(path))


def save_peaks(path: Path | str, peaks: list[dict[str, Any]]) -> Path:
    """写回峰表 CSV(core.peaks 实现)。"""
    return Path(core_save(path, peaks))


def export_peaks_poky(
    path: Path | str, peaks: list[dict[str, Any]], ndim: int = 2
) -> Path:
    """导出 Poky/Sparky .list(core.peaks 实现)。"""
    return Path(core_export(path, peaks, ndim=ndim))


def import_peaks_poky(path: Path | str) -> list[dict[str, Any]]:
    """导入 Poky/Sparky .list(core.peaks 实现)。"""
    return list(core_import(path))


def normalize_poky_label(text: str | None, ndim: int = 2) -> str:
    """Poky assignment 按维度分段规范化(core.peaks 实现,2D 两段/3D 三段)。"""
    return core_normalize_label(text, ndim=ndim)


def poky_label_is_valid(text: str | None, ndim: int = 2) -> bool:
    """判断文本是否为当前维度的 Poky assignment(core.peaks 实现)。"""
    return core_label_valid(text, ndim=ndim)
