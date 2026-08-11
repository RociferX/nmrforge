"""独立谱图查看模块(与项目管理 GUI 解耦,可单独运行)。

``python -m viewer [spectrum.ft2]`` 即可打开窗口。
"""

from viewer.spectrum import Spectrum, SpectrumAxis
from viewer.spectrum_viewer import SpectrumViewer

__all__ = ["Spectrum", "SpectrumAxis", "SpectrumViewer"]
