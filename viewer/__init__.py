"""独立谱图查看模块(与项目管理 GUI 解耦,可单独运行)。

``python -m viewer [spectrum.ft2]`` 即可打开窗口。

0.2.199-补29ht:包导入本身保持轻量(不再连带 pyqtgraph/scipy)——只用到
``viewer.axis_labels`` 的场景(GUI 注释标签)不受影响;``Spectrum`` /
``SpectrumAxis`` / ``SpectrumViewer`` 首次访问时才真正导入。
"""

from __future__ import annotations

from typing import Any

__all__ = ["Spectrum", "SpectrumAxis", "SpectrumViewer"]


def __getattr__(name: str) -> Any:
    """按需导入包内公开对象(PEP 562)。"""
    if name in ("Spectrum", "SpectrumAxis"):
        from viewer.spectrum import Spectrum, SpectrumAxis

        return Spectrum if name == "Spectrum" else SpectrumAxis
    if name == "SpectrumViewer":
        from viewer.spectrum_viewer import SpectrumViewer

        return SpectrumViewer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
