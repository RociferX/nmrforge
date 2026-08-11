"""谱图领域对象:读取 NMRPipe 二维谱并构建 ppm 坐标轴。

轴约定(与旧项目 NMRFlow 一致):``data[0]`` = F1(行/y 轴),``data[1]`` = F2(列/x 轴)。
ppm 轴由 NMRPipe 头部 FDF*ORIG 定义(EXT 后仅更新 ORIG),缺失时回退 CAR。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import numpy as np

logger = logging.getLogger("nmrforge.viewer.spectrum")


@dataclass(frozen=True)
class SpectrumAxis:
    """一维谱轴:数据点下标 <-> ppm。"""

    label: str
    size: int
    sw_hz: float
    obs_mhz: float
    carrier_ppm: float
    orig_hz: float = 0.0

    @cached_property
    def ppm(self) -> np.ndarray:
        """每个数据点的 ppm 坐标(NMRPipe 约定:ppm 随索引递减,ORIG 为轴末点频率)。"""
        idx = np.arange(self.size)
        if self.orig_hz:
            return self.orig_hz / self.obs_mhz + (self.size - 1 - idx) * (
                self.sw_hz / (self.size * self.obs_mhz)
            )
        return self.carrier_ppm + (self.size / 2 - idx) * self.sw_hz / (
            self.size * self.obs_mhz
        )

    def index_at(self, ppm_value: float) -> int:
        return int(np.argmin(np.abs(self.ppm - ppm_value)))

    def ppm_at(self, index: int) -> float:
        return float(self.ppm[index])

    def ppm_at_f(self, value: float) -> float:
        """亚像素 ppm:线性插值(峰质心定位用)。"""
        i0 = max(0, int(np.floor(value)))
        i1 = min(i0 + 1, self.size - 1)
        i0 = min(i0, i1)
        frac = value - i0
        return float(self.ppm[i0] * (1.0 - frac) + self.ppm[i1] * frac)


class Spectrum:
    """二维谱:``data`` 形状 (F1, F2);axes[0]=F1,axes[1]=F2。"""

    def __init__(
        self,
        data: np.ndarray,
        axes: list[SpectrumAxis],
        source: Path | str | None = None,
    ) -> None:
        self.data = np.asarray(data, dtype=float)
        self.axes = list(axes)
        self.source = Path(source) if source else None

    @property
    def x_axis(self) -> SpectrumAxis:
        return self.axes[1]

    @property
    def y_axis(self) -> SpectrumAxis:
        return self.axes[0]

    @property
    def max_intensity(self) -> float:
        return float(np.max(self.data)) if self.data.size else 0.0

    @staticmethod
    def _normalize_data(data: np.ndarray, source: str) -> np.ndarray:
        """复数取实部、压平伪三维(首维为 1)、强制二维。"""
        data = np.asarray(data)
        if np.iscomplexobj(data):
            data = data.real
        while data.ndim > 2 and data.shape[0] == 1:
            data = data[0]
        if data.ndim != 2:
            raise ValueError(
                f"仅支持二维谱图(当前 {data.ndim} 维,形状 {data.shape}): {source}"
            )
        return data

    def estimate_noise(self, fraction: float = 0.1) -> float:
        """用右下角区域的标准差估计噪声水平。"""
        ny, nx = self.data.shape
        r0 = int(ny * (1 - fraction))
        c0 = int(nx * (1 - fraction))
        region = self.data[r0:, c0:]
        return float(np.std(region)) if region.size else 0.0

    @classmethod
    def load_from_ft2(
        cls,
        path: Path | str,
        labels: tuple[str, str] = ("F1", "F2"),
    ) -> Spectrum:
        """用 nmrglue 读取 NMRPipe 二维 .ft2 并构建 ppm 轴。"""
        import nmrglue as ng

        dic, data = ng.pipe.read(str(path))
        data = cls._normalize_data(data, str(path))
        if int(dic.get("FDDIMCOUNT", 2)) < 2:
            raise ValueError(f"仅支持二维谱图(FDDIMCOUNT<2): {path}")

        def _axis(prefix: str, label: str, size: int) -> SpectrumAxis:
            return SpectrumAxis(
                label=label,
                size=size,
                sw_hz=float(dic[prefix + "SW"]),
                obs_mhz=float(dic[prefix + "OBS"]),
                carrier_ppm=float(dic[prefix + "CAR"]),
                orig_hz=float(dic.get(prefix + "ORIG", 0.0) or 0.0),
            )

        axes = [
            _axis("FDF1", labels[0], int(data.shape[0])),
            _axis("FDF2", labels[1], int(data.shape[1])),
        ]
        logger.info("载入谱图: %s (%s)", path, data.shape)
        return cls(data, axes, source=Path(path))
