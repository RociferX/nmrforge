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

from viewer.axis_labels import axis_labels_from_nuclei, infer_nucleus

logger = logging.getLogger("nmrforge.viewer.spectrum")

# 常见核的化学位移范围(ppm),谱加载轴序/引用自检用(0.2.122)
_NUCLEUS_PPM_RANGES: dict[str, tuple[float, float]] = {
    "1H": (-5.0, 20.0),
    "15N": (90.0, 140.0),
    "13C": (10.0, 190.0),
}
_NUCLEI = set(_NUCLEUS_PPM_RANGES) | {"2H", "19F", "31P", "23Na", "29Si"}

# 横坐标显示优先级(0.2.153,用户规则):H > N > C;未知核不参与转置
_NUCLEUS_X_PRIORITY: dict[str, int] = {"1H": 0, "15N": 1, "13C": 2}


def _parse_nmrpipe_label(label: str) -> str:
    """NMRPipe FDF*LABEL('N15'/'H1'/'C13',同核下标'15Nx'/'1Hy') →
    核名('15N'/'1H'/'13C');失败返回 ''。

    0.2.199-补29ai:同核唯一化标签(15Nx/1Hy/1Hz)先去尾部 x/y/z 再
    匹配核名,避免 LABEL 解析失败后全靠 OBS 兜底。
    """
    text = str(label or "").strip().upper()
    if not text:
        return ""
    if text in _NUCLEI:
        return text
    if text[-1:] in ("X", "Y", "Z") and text[:-1] in _NUCLEI:
        return text[:-1]
    digits = "".join(ch for ch in text if ch.isdigit())
    letters = "".join(ch for ch in text if ch.isalpha())
    candidate = f"{digits}{letters}" if digits and letters else ""
    return candidate if candidate in _NUCLEI else ""


def _storage_nuclei(dic: dict, prefixes: tuple[str, ...]) -> list[str]:
    """按 NMRPipe 头部推断各存储轴的核:LABEL 优先,OBS 兜底。"""
    nuclei: list[str] = []
    for prefix in prefixes:
        nucleus = _parse_nmrpipe_label(dic.get(prefix + "LABEL", ""))
        if not nucleus:
            try:
                obs = float(dic.get(prefix + "OBS", 0) or 0)
            except (TypeError, ValueError):
                obs = 0.0
            nucleus = infer_nucleus(obs)
        nuclei.append(nucleus)
    return nuclei



def _fdf_prefix_for_axis(dic: dict, ndim: int, axis_idx: int) -> str:
    """数据轴 axis_idx 对应的 FDF 参数块前缀('FDF1'/'FDF2'/...)。

    nmrglue pipe.read 返回的数据轴序与 NMRPipe 存储序相反,每轴对应的
    逻辑维号由头部 FDDIMORDER 给出(与 nmrglue make_uc/guess_udic
    同源:axis i ↔ FDF{FDDIMORDER[ndim-1-i]});FDDIMORDER 缺失/非法时
    回退旧位置式 FDF{axis_idx+1}(0.2.151 前行为)。
    """
    try:
        order = [int(v) for v in dic.get("FDDIMORDER") or []]
    except (TypeError, ValueError):
        order = []
    if len(order) >= ndim:
        dim = order[ndim - 1 - axis_idx]
        if 1 <= dim <= 4:
            return f"FDF{dim}"
    return f"FDF{axis_idx + 1}"




def _logical_nuclei_from_order(
    dic: dict, ndim: int, storage_nuclei: list[str]
) -> list[str] | None:
    """按 FDDIMORDER 推断逻辑序核列表(F1/F2/F3 序);无法构成排列返回 None。

    nmrglue 数据轴 i 的逻辑维号 = FDDIMORDER[ndim-1-i];据此把存储序核
    映射回逻辑序,使无 metadata 直接打开时也能按头部重排(0.2.152)。
    """
    try:
        order = [int(v) for v in dic.get("FDDIMORDER") or []]
    except (TypeError, ValueError):
        return None
    if len(order) < ndim:
        return None
    logical: list[str | None] = [None] * ndim
    for axis_idx, nucleus in enumerate(storage_nuclei):
        dim = order[ndim - 1 - axis_idx]
        if not (1 <= dim <= ndim) or logical[dim - 1] is not None:
            return None
        logical[dim - 1] = nucleus
    if any(n is None for n in logical):
        return None
    return [n for n in logical if n is not None]  # type: ignore[return-value]


def _labels_from_nuclei(
    nuclei: list[str], labels: tuple[str, ...]
) -> tuple[str, ...]:
    """generic F1/F2/F3 标签且核已知时,替换为核符号标签(N/H/C,Hx/Hy 等)。

    独立查看器无 metadata 直接打开时,轴标签由头部 LABEL/OBS 推断的核
    生成(与 nmrDraw 的 NAME 一致),不再显示 F1/F2/F3(0.2.152)。
    """
    if not labels or len(nuclei) != len(labels):
        return labels
    if any(
        not str(label).startswith("F") or not str(label)[1:].isdigit()
        for label in labels
    ):
        return labels
    if any(not n for n in nuclei):
        return labels
    derived = axis_labels_from_nuclei(nuclei)
    if derived and len(derived) == len(labels):
        return derived
    return labels




def orient_x_priority(spectrum: Spectrum) -> Spectrum:
    """二维谱横坐标按核优先级 H > N > C 定向(0.2.153 显示规则)。

    横坐标核优先级低于纵坐标时转置数据并交换轴(dim_indices 同步交换);
    两轴同核、核未知或已满足优先级时保持原方向。
    """
    if spectrum.data.ndim != 2:
        return spectrum
    x_nuc = infer_nucleus(spectrum.x_axis.obs_mhz)
    y_nuc = infer_nucleus(spectrum.y_axis.obs_mhz)
    px = _NUCLEUS_X_PRIORITY.get(x_nuc, 100)
    py = _NUCLEUS_X_PRIORITY.get(y_nuc, 100)
    if px <= py:
        return spectrum
    transposed = Spectrum(
        np.asarray(spectrum.data).T,
        [spectrum.x_axis, spectrum.y_axis],
        source=spectrum.source,
    )
    dims = getattr(spectrum, "dim_indices", None)
    if dims:
        transposed.dim_indices = (dims[1], dims[0])
    robust = getattr(spectrum, "robust_max", None)
    if robust is not None:
        transposed.robust_max = robust
    return transposed


def _relabel_axes(
    axes: list[SpectrumAxis], labels: tuple[str, ...]
) -> list[SpectrumAxis]:
    """按逻辑序重建轴对象(SpectrumAxis 冻结,label 需重建)。"""
    return [
        SpectrumAxis(
            label=labels[i] if i < len(labels) else axis.label,
            size=axis.size,
            sw_hz=axis.sw_hz,
            obs_mhz=axis.obs_mhz,
            carrier_ppm=axis.carrier_ppm,
            orig_hz=axis.orig_hz,
        )
        for i, axis in enumerate(axes)
    ]


def _permutation_to_logical(
    storage_nuclei: list[str], logical_nuclei: list[str]
) -> list[int] | None:
    """storage 轴 → logical 位置排列;无法构成排列(长度/未知核/不匹配)返回 None。

    0.2.168:同核(如 1H/13C/1H 的两个 1H)按维度位置语义匹配——核种类
    相同的位置可互换,重排结果在显示层面等价(Hx/Hy 由位置决定)。
    """
    n = len(storage_nuclei)
    if n != len(logical_nuclei) or n == 0:
        return None
    if any(not s for s in storage_nuclei) or any(not t for t in logical_nuclei):
        return None
    perm: list[int | None] = [None] * n
    used = [False] * n
    for lpos, target in enumerate(logical_nuclei):
        for spos, source in enumerate(storage_nuclei):
            if source == target and not used[spos]:
                perm[spos] = lpos
                used[spos] = True
                break
        else:
            return None
    return [int(p) for p in perm]  # type: ignore[arg-type]


def _reorder_to_logical(
    data: np.ndarray,
    axes: list[SpectrumAxis],
    storage_nuclei: list[str],
    logical_nuclei: list[str],
    path: Path | str,
) -> tuple[np.ndarray, list[SpectrumAxis], list[str]]:
    """把存储轴序重排到逻辑序(F1,F2,F3);无法确认时保持现状并告警。"""
    perm = _permutation_to_logical(storage_nuclei, logical_nuclei)
    if perm is None:
        logger.warning(
            "轴序校对: 无法确认逻辑轴序,保持存储序(存储 %s, 逻辑 %s): %s",
            storage_nuclei, logical_nuclei, path,
        )
        return data, axes, storage_nuclei
    if perm == list(range(len(perm))):
        return data, axes, storage_nuclei
    inv = [0] * len(perm)
    for spos, lpos in enumerate(perm):
        inv[lpos] = spos
    reordered = np.transpose(data, inv)
    reordered_axes = [axes[spos] for spos in inv]
    reordered_nuclei = [storage_nuclei[spos] for spos in inv]
    logger.info(
        "轴序重排: 存储 (%s) → 逻辑 (%s): %s",
        " ".join(storage_nuclei), " ".join(logical_nuclei), path,
    )
    return reordered, reordered_axes, reordered_nuclei


def _warn_ppm_range_mismatch(
    axes: list[SpectrumAxis], nuclei: list[str], path: Path | str
) -> None:
    """谱加载自检:每轴核与常见化学位移范围不符时告警(可能轴序/引用问题)。"""
    for axis, nucleus in zip(axes, nuclei):
        rng = _NUCLEUS_PPM_RANGES.get(nucleus)
        if rng is None or axis.size == 0:
            continue
        lo = float(np.min(axis.ppm))
        hi = float(np.max(axis.ppm))
        if hi < rng[0] or lo > rng[1]:
            logger.warning(
                "轴序/引用自检: %s 轴(%s) ppm 范围 [%.1f, %.1f] 超出常见范围 %s: %s",
                axis.label, nucleus, lo, hi, rng, path,
            )


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
        nuclei: list[str] | None = None,
    ) -> Spectrum:
        """用 nmrglue 读取 NMRPipe 二维 .ft2 并构建 ppm 轴。

        nuclei 为 metadata 逻辑轴核(F1/F2 序);非空时按存储头
        FDF*LABEL/FDF*OBS 推断存储轴核并重排到逻辑序,并做 ppm 范围
        自检(0.2.122);缺省保持位置序(旧行为)。
        0.2.151:数据轴→FDF 块映射按头部 FDDIMORDER 建立(与 nmrglue
        guess_udic 同源),无 FDDIMORDER 时回退旧位置式。
        0.2.152:无 metadata 时按 FDDIMORDER 重排逻辑序,并由头部核推导
        轴标签(不再显示 F1/F2/F3)。
        """
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

        prefixes = tuple(
            _fdf_prefix_for_axis(dic, data.ndim, i) for i in range(data.ndim)
        )
        axes = [
            _axis(prefix, labels[i], int(data.shape[i]))
            for i, prefix in enumerate(prefixes)
        ]
        storage = _storage_nuclei(dic, prefixes)
        if nuclei:
            data, axes, storage = _reorder_to_logical(
                data, axes, storage, list(nuclei), path
            )
        else:
            logical = _logical_nuclei_from_order(dic, data.ndim, storage)
            if logical:
                data, axes, storage = _reorder_to_logical(
                    data, axes, storage, logical, path
                )
        axes = _relabel_axes(axes, _labels_from_nuclei(storage, labels))
        _warn_ppm_range_mismatch(axes, storage, path)
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
        if getattr(self, "_lazy", False):
            # 0.2.199-补29dd:懒加载不读全量,用样例平面估计
            try:
                plane = self._lazy_cached_plane(0, 0)
                return float(np.max(plane)) if plane.size else 0.0
            except Exception:  # noqa: BLE001 - 估计失败返回 0
                return 0.0
        size = int(np.prod(self.data.shape)) if hasattr(self.data, "shape") else 0
        return float(np.max(self.data)) if size else 0.0

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
        nuclei: list[str] | None = None,
        *,
        lazy: bool = False,
    ) -> Spectrum3D:
        """用 nmrglue 读取 NMRPipe 三维 .ft3 并构建 ppm 轴(契约 §10.1)。

        lazy=True(0.2.199-补29dd):流式 3D(FDPIPEFLAG!=0)用 read_lowmem
        只读头部/轴,切片时按需从文件读对应 2D 平面——不加载全 3D 体,
        打开大谱不再慢/占内存;非流文件回退全量读取。

        nuclei 为 metadata 逻辑轴核(F1/F2/F3 序);非空时按存储头
        FDF*LABEL/FDF*OBS 推断存储轴核,不一致则重排 data/axes 到逻辑
        序并告警日志「轴序重排」,并做 ppm 范围自检(0.2.122);缺省
        保持位置序(旧行为)。
        0.2.151:数据轴→FDF 块映射按头部 FDDIMORDER 建立(与 nmrglue
        guess_udic 同源;真实 3D 输出 ORDER 2 3 1 = 存储 (F2,F3,F1)),
        无 FDDIMORDER 时回退旧位置式。
        0.2.152:无 metadata 时按 FDDIMORDER 重排逻辑序,并由头部核推导
        轴标签(不再显示 F1/F2/F3)。

        单文件 3D 流(xyz2pipe 产物,FDPIPEFLAG=1)读回形状 (F1, F2, F3),
        其中 F1=FDF3SIZE、F2=FDSPECNUM、F3=FDSIZE;非流文件按同约定重塑。
        """
        import nmrglue as ng

        if lazy:
            try:
                dic, lazy_data = ng.pipe.read_lowmem(str(path))
            except Exception:  # noqa: BLE001 - read_lowmem 不支持(头部不完整等)
                return cls.load_from_ft3(path, labels=labels, nuclei=nuclei)
            if cls._header_int(dic, "FDDIMCOUNT") < 3:
                raise ValueError(f"仅支持三维谱图(FDDIMCOUNT<3): {path}")
            flag = float(dic.get("FDPIPEFLAG", 0.0) or 0.0)
            if flag == 0:
                # 非流单文件:布局不同,懒读不可靠,回退全量(通常为小文件)
                return cls.load_from_ft3(path, labels=labels, nuclei=nuclei)
            return cls._build_lazy(path, dic, lazy_data, labels, nuclei)

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

        prefixes = tuple(
            _fdf_prefix_for_axis(dic, data.ndim, i) for i in range(data.ndim)
        )
        axes = [
            _axis(prefix, labels[i], int(data.shape[i]))
            for i, prefix in enumerate(prefixes)
        ]
        storage = _storage_nuclei(dic, prefixes)
        if nuclei:
            data, axes, storage = _reorder_to_logical(
                data, axes, storage, list(nuclei), path
            )
        else:
            logical = _logical_nuclei_from_order(dic, data.ndim, storage)
            if logical:
                data, axes, storage = _reorder_to_logical(
                    data, axes, storage, logical, path
                )
        axes = _relabel_axes(axes, _labels_from_nuclei(storage, labels))
        _warn_ppm_range_mismatch(axes, storage, path)
        logger.info("载入三维谱: %s (%s)", path, data.shape)
        return cls(data, axes, source=Path(path))

    @classmethod
    def _build_lazy(
        cls,
        path: Path | str,
        dic: dict,
        lazy_data,
        labels: tuple[str, str, str],
        nuclei: list[str] | None,
    ) -> Spectrum3D:
        """懒加载构建:只读头部/轴,data 为流式懒对象(0.2.199-补29dd)。"""

        def _axis(prefix: str, label: str, size: int) -> SpectrumAxis:
            return SpectrumAxis(
                label=label,
                size=size,
                sw_hz=float(dic[prefix + "SW"]),
                obs_mhz=float(dic[prefix + "OBS"]),
                carrier_ppm=float(dic[prefix + "CAR"]),
                orig_hz=float(dic.get(prefix + "ORIG", 0.0) or 0.0),
            )

        data_shape = tuple(int(v) for v in lazy_data.shape)
        prefixes = tuple(
            _fdf_prefix_for_axis(dic, 3, i) for i in range(3)
        )
        axes = [
            _axis(prefix, labels[i], data_shape[i])
            for i, prefix in enumerate(prefixes)
        ]
        storage_nuclei = _storage_nuclei(dic, prefixes)
        if nuclei:
            logical_nuclei = list(nuclei)
        else:
            logical_nuclei = (
                _logical_nuclei_from_order(dic, 3, storage_nuclei)
                or storage_nuclei
            )
        perm = _permutation_to_logical(storage_nuclei, logical_nuclei)
        inv = list(range(3))
        if perm is not None and perm != [0, 1, 2]:
            inv = [0] * 3
            for spos, lpos in enumerate(perm):
                inv[lpos] = spos
            logger.info(
                "轴序重排(懒加载): 存储 (%s) → 逻辑 (%s): %s",
                " ".join(storage_nuclei), " ".join(logical_nuclei), path,
            )
        logical_axes = [axes[inv[dim]] for dim in range(3)]
        logical_storage = [storage_nuclei[inv[dim]] for dim in range(3)]
        logical_axes = _relabel_axes(
            logical_axes, _labels_from_nuclei(logical_storage, labels)
        )
        _warn_ppm_range_mismatch(logical_axes, logical_storage, path)
        obj = cls.__new__(cls)
        obj.data = lazy_data  # 流式懒对象(存储序)
        obj.axes = logical_axes
        obj.source = Path(path)
        obj._lazy = True
        obj._lazy_inv = tuple(inv)
        obj._plane_cache: dict[tuple[int, int], np.ndarray] = {}
        logger.info("载入三维谱(懒加载): %s (%s)", path, data_shape)
        return obj

    def _lazy_read_plane(self, axis_idx: int, index: int) -> np.ndarray:
        """懒加载:从流式存储读固定逻辑轴的一帧,转置到逻辑剩余轴序。"""
        inv = self._lazy_inv
        spos = inv[axis_idx]
        idx: list = [slice(None), slice(None), slice(None)]
        idx[spos] = int(index)
        arr = np.asarray(self.data[tuple(idx)])
        if np.iscomplexobj(arr):
            arr = arr.real
        plane = np.asarray(arr, dtype=float)
        logical_remaining = [dim for dim in range(3) if dim != axis_idx]
        storage_remaining = [s for s in range(3) if s != spos]
        axes_perm = [
            storage_remaining.index(inv[dim])
            for dim in logical_remaining
        ]
        if axes_perm != [0, 1]:
            plane = np.transpose(plane, axes_perm)
        return plane

    def _lazy_cached_plane(self, axis_idx: int, index: int) -> np.ndarray:
        """懒切片 + 小缓存(最近 4 帧,滚动回看不重复读盘)。"""
        key = (int(axis_idx), int(index))
        cache = self._plane_cache
        if key in cache:
            return cache[key]
        plane = self._lazy_read_plane(axis_idx, index)
        cache[key] = plane
        while len(cache) > 4:
            cache.pop(next(iter(cache)))
        return plane

    def index_at(self, axis_idx: int, ppm_value: float) -> int:
        """第 axis_idx 维按 ppm 定位下标(供滑块按 ppm 定位)。"""
        return self.axes[axis_idx].index_at(ppm_value)

    def slice(self, axis_idx: int, index: int) -> Spectrum:
        """固定第 axis_idx 维的 index,返回其余两轴的二维 Spectrum。

        轴顺序与固定维后的剩余轴一致:axis 0 -> (F2,F3);axis 1 -> (F1,F3);
        axis 2 -> (F1,F2)。
        """
        if getattr(self, "_lazy", False):
            index = int(index)
            if not (0 <= index < self.axes[axis_idx].size):
                raise IndexError(
                    f"切片索引越界: 第 {axis_idx} 维 index={index} "
                    f"(size={self.axes[axis_idx].size})"
                )
            data2d = self._lazy_cached_plane(axis_idx, index)
            remaining = [i for i in range(3) if i != axis_idx]
            return orient_x_priority(
                Spectrum(
                    data2d, [self.axes[i] for i in remaining],
                    source=self.source,
                )
            )
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
        return orient_x_priority(
            Spectrum(
                np.asarray(data2d), [self.axes[i] for i in remaining],
                source=self.source,
            )
        )

    def project(self, axis_idx: int, mode: str = "max") -> Spectrum:
        """沿第 axis_idx 维投影:MIP(max)/ 求和(sum),轴序同 slice。"""
        if mode == "sum":
            data2d = np.sum(self.data, axis=axis_idx)
        else:
            data2d = np.max(self.data, axis=axis_idx)
        remaining = [i for i in range(3) if i != axis_idx]
        return orient_x_priority(
            Spectrum(
                np.asarray(data2d), [self.axes[i] for i in remaining],
                source=self.source,
            )
        )

    def estimate_noise(self, fraction: float = 0.1) -> float:
        """用角落小块(三维)的标准差估计噪声水平。"""
        if getattr(self, "_lazy", False):
            # 0.2.199-补29dd:懒加载用样例平面角落估计(不读全量)
            try:
                sample = self._lazy_cached_plane(0, 0)
                s0 = max(1, int(sample.shape[0] * fraction))
                s1 = max(1, int(sample.shape[1] * fraction))
                region = sample[-s0:, -s1:]
                return float(np.std(region)) if region.size else 0.0
            except Exception:  # noqa: BLE001 - 估计失败返回 0
                return 0.0
        size = int(np.prod(self.data.shape)) if hasattr(self.data, "shape") else 0
        if size == 0:
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
        return orient_x_priority(
            Spectrum(
                np.asarray(data2d), [self.axes[i] for i in remaining],
                source=self.source,
            )
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
