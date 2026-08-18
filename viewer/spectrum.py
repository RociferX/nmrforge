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

class Spectrum3D:
    """三维谱(契约 §10.1):``data`` 形状 (F1, F2, F3);axes=[F1,F2,F3]。"""

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
    def max_intensity(self) -> float:
        return float(np.max(self.data)) if self.data.size else 0.0

    @staticmethod
    def _normalize_data(data: np.ndarray, source: str) -> np.ndarray:
        """复数取实部并强制三维。"""
        data = np.asarray(data)
        if np.iscomplexobj(data):
            data = data.real
        if data.ndim != 3:
            raise ValueError(
                f"仅支持三维谱图(当前 {data.ndim} 维,形状 {data.shape}): {source}"
            )
        return data

    @staticmethod
    def _header_int(dic: dict, key: str) -> int:
        try:
            return int(float(dic.get(key, 0) or 0))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def load_from_ft3(
        cls,
        path: Path | str,
        labels: tuple[str, str, str] = ("F1", "F2", "F3"),
    ) -> Spectrum3D:
        """用 nmrglue 读取 NMRPipe 三维 .ft3 并构建 ppm 轴(契约 §10.1)。

        单文件 3D 流(xyz2pipe 产物,FDPIPEFLAG=1)读回形状 (F1, F2, F3),
        其中 F1=FDF3SIZE、F2=FDSPECNUM、F3=FDSIZE;非流文件按同约定重塑。
        """
        import nmrglue as ng

        dic, data = ng.pipe.read(str(path))
        data = np.asarray(data)
        if cls._header_int(dic, "FDDIMCOUNT") < 3:
            raise ValueError(f"仅支持三维谱图(FDDIMCOUNT<3): {path}")
        if data.ndim == 2:
            f1 = cls._header_int(dic, "FDF3SIZE")
            f3 = cls._header_int(dic, "FDSIZE")
            if f1 <= 0 or f3 <= 0 or data.shape[0] % f1 or data.shape[1] != f3:
                raise ValueError(
                    f"无法从二维存储还原三维谱(FDF3SIZE={f1}, FDSIZE={f3}): "
                    f"{path}"
                )
            data = data.reshape((f1, data.shape[0] // f1, f3))
        data = cls._normalize_data(data, str(path))

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
            _axis("FDF3", labels[2], int(data.shape[2])),
        ]
        logger.info("载入三维谱: %s (%s)", path, data.shape)
        return cls(data, axes, source=Path(path))

    def index_at(self, axis_idx: int, ppm_value: float) -> int:
        """第 axis_idx 维按 ppm 定位下标(供滑块按 ppm 定位)。"""
        return self.axes[axis_idx].index_at(ppm_value)

    def slice(self, axis_idx: int, index: int) -> Spectrum:
        """固定第 axis_idx 维的 index,返回其余两轴的二维 Spectrum。

        轴顺序与固定维后的剩余轴一致:axis 0 -> (F2,F3);axis 1 -> (F1,F3);
        axis 2 -> (F1,F2)。
        """
        index = int(index)
        size = self.data.shape[axis_idx]
        if not (0 <= index < size):
            raise IndexError(
                f"切片索引越界: 第 {axis_idx} 维 index={index} (size={size})"
            )
        remaining = [i for i in range(3) if i != axis_idx]
        if axis_idx == 0:
            data2d = self.data[index, :, :]
        elif axis_idx == 1:
            data2d = self.data[:, index, :]
        else:
            data2d = self.data[:, :, index]
        return Spectrum(
            np.asarray(data2d), [self.axes[i] for i in remaining],
            source=self.source,
        )

    def project(self, axis_idx: int, mode: str = "max") -> Spectrum:
        """沿第 axis_idx 维投影:MIP(max)/ 求和(sum),轴序同 slice。"""
        if mode == "sum":
            data2d = np.sum(self.data, axis=axis_idx)
        else:
            data2d = np.max(self.data, axis=axis_idx)
        remaining = [i for i in range(3) if i != axis_idx]
        return Spectrum(
            np.asarray(data2d), [self.axes[i] for i in remaining],
            source=self.source,
        )

    def estimate_noise(self, fraction: float = 0.1) -> float:
        """用角落小块(三维)的标准差估计噪声水平。"""
        if self.data.size == 0:
            return 0.0
        s0 = max(1, int(self.data.shape[0] * fraction))
        s1 = max(1, int(self.data.shape[1] * fraction))
        s2 = max(1, int(self.data.shape[2] * fraction))
        region = self.data[-s0:, -s1:, -s2:]
        return float(np.std(region)) if region.size else 0.0

    def project_nmrpipe(self, axis_idx: int, thresh: float) -> Spectrum:
        """nmrPipe projZ 式投影:低于阈值的点置零后沿轴求和。

        projZ.M 的做法:每张平面先做 ±阈值截断(噪声置零),再把平面
        累加——峰强度保留、噪声不累积,投影谱观感接近常规二维谱
        (如 HNCA 沿 13C 投影得到类似 HSQC 的 HN 平面)。
        """
        data = np.asarray(self.data, dtype=float)
        if thresh > 0:
            data = np.where(np.abs(data) < thresh, 0.0, data)
        data2d = np.sum(data, axis=axis_idx)
        remaining = [i for i in range(3) if i != axis_idx]
        return Spectrum(
            np.asarray(data2d), [self.axes[i] for i in remaining],
            source=self.source,
        )

class Spectrum1D:
    """一维谱(时间域 FID 或二维切片):``data`` 形状 (N,),一个 SpectrumAxis。"""

    def __init__(
        self,
        data: np.ndarray,
        axis: SpectrumAxis,
        source: Path | str | None = None,
    ) -> None:
        self.data = np.asarray(data, dtype=float)
        self.axis = axis
        self.source = Path(source) if source else None

    @property
    def max_intensity(self) -> float:
        return float(np.max(self.data)) if self.data.size else 0.0

    @property
    def ppm_valid(self) -> bool:
        """是否有可用 ppm 轴(sw/obs 头部齐全)。"""
        return self.axis.sw_hz > 0 and self.axis.obs_mhz > 0

    def x_values(self) -> np.ndarray:
        """绘图 x 坐标:有效 ppm 轴用 ppm,否则用点序号。"""
        if self.ppm_valid:
            return self.axis.ppm
        return np.arange(self.axis.size, dtype=float)

    @staticmethod
    def _axis_from_dic(dic: dict, label: str, size: int) -> SpectrumAxis:
        """从 NMRPipe 头部取直接维频率参数(FDF2*/FS*),缺失时退化为点轴。"""

        def _first(*keys: str) -> float:
            for key in keys:
                value = dic.get(key)
                if value in (None, "", 0, 0.0):
                    continue
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
            return 0.0

        return SpectrumAxis(
            label=label,
            size=size,
            sw_hz=_first("FDF2SW", "FSSW", "FSW"),
            obs_mhz=_first("FDF2OBS", "FSOBS"),
            carrier_ppm=_first("FDF2CAR", "FSCAR"),
            orig_hz=_first("FDF2ORIG"),
        )

    @classmethod
    def load_from_fid(
        cls, path: Path | str, label: str = "FID"
    ) -> Spectrum1D | Spectrum:
        """用 nmrglue 读取 NMRPipe .fid(时间域)。

        - 一维 FID:返回 Spectrum1D(实部迹线);
        - 二维及以上 FID:按 nmrDraw 方式显示整块二维时域平面
          (行=各 FID/间接维增量,列=直接维时点),返回 Spectrum;
          3D+ FID 显示首个间接增量的二维平面(与 nmrDraw 一致)。
        时间域以数据点(序号)为轴,不使用 ppm(ppm 只对频域谱有意义)。
        """
        import nmrglue as ng

        _dic, data = ng.pipe.read(str(path))
        data = np.asarray(data)
        if np.iscomplexobj(data):
            data = data.real
        while data.ndim > 2:
            data = data[0]  # 3D+ FID:显示首个间接增量的二维时域平面
        if data.ndim == 1:
            axis = SpectrumAxis(
                label=label or "FID 数据点",
                size=int(data.shape[0]),
                sw_hz=0.0,
                obs_mhz=0.0,
                carrier_ppm=0.0,
                orig_hz=0.0,
            )
            logger.info("载入 FID: %s (%s)", path, data.shape)
            return cls(data, axis, source=Path(path))
        fid_axis = SpectrumAxis(
            label=label or "FID",
            size=int(data.shape[0]),
            sw_hz=0.0,
            obs_mhz=0.0,
            carrier_ppm=0.0,
            orig_hz=0.0,
        )
        point_axis = SpectrumAxis(
            label="Points",
            size=int(data.shape[1]),
            sw_hz=0.0,
            obs_mhz=0.0,
            carrier_ppm=0.0,
            orig_hz=0.0,
        )
        spectrum = Spectrum(data, [fid_axis, point_axis], source=Path(path))
        # FID 动态范围大(ADC 累积值),等高线默认基准取高分位数,
        # 避免被个别尖峰淹没,看不到大部分 FID 的时域包络。
        robust_max = float(np.percentile(np.abs(data), 99.0))
        if robust_max > 0:
            spectrum.robust_max = robust_max
        logger.info("载入 FID(二维时域): %s (%s)", path, data.shape)
        return spectrum
