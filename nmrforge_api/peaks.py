"""峰位测量:在每张候选谱上追踪同一批参考峰的亚像素位置。

为什么不用「重新选峰」做这项研究:选峰是阈值检测,处理参数一变就可能多峰/
漏峰,峰位差里混入「换了另一个峰」的成分。本模块固定参考峰表,在每个峰位附近
的小窗口内找极值并做抛物线亚像素refine,给出**同一批峰的连续位置**,这才是
「处理参数 → 峰位不确定度」的观测量。

方法(可写进论文方法部分):

1. 参考峰表给出每个峰的核素位置(ppm)与 assignment;
2. ppm → 该谱数据轴上的分数索引(与选峰同源:ORIG 优先、回退 CAR,
   并按 FDDIMORDER 把逻辑维映射到数据轴,见 ``workflow.pick_peaks``);
3. 在 ±``window_pts`` 点的窗口内取 ``|强度|``(默认)极值,得到整数索引;
4. 每个参与测量的轴用 ±1 点三点抛物线求顶点偏移 δ∈[-0.5, 0.5],
   分数索引 = 整数索引 + δ;
5. 分数索引 → ppm(轴数组线性插值)。

质量标记:``window_edge``(极值落在窗口边界,真峰可能在窗外)、``boundary``
(极值贴谱边界)、``out_of_range``(参考 ppm 落在谱范围外)、``found``。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from nmrforge_api.errors import MeasurementError
from workflow.pick_peaks import SpectrumAxes, read_spectrum_axes

# 峰表命名键 → 核名(core.peaks.peak_table 的 2D 行约定)
_NAMED_KEYS: tuple[tuple[str, str], ...] = (
    ("H_shift", "1H"),
    ("N_shift", "15N"),
    ("C_shift", "13C"),
)


@dataclass
class PeakMeasurement:
    """一个参考峰在一张谱上的位置测量结果。"""

    peak_id: int
    assignment: str
    reference: dict[str, float] = field(default_factory=dict)
    positions: dict[str, float] = field(default_factory=dict)
    deltas: dict[str, float] = field(default_factory=dict)
    intensity: float = 0.0
    found: bool = True
    window_edge: bool = False
    boundary: bool = False
    out_of_range: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "peak_id": int(self.peak_id),
            "assignment": self.assignment,
            "reference": {k: float(v) for k, v in self.reference.items()},
            "positions": {k: float(v) for k, v in self.positions.items()},
            "deltas": {k: float(v) for k, v in self.deltas.items()},
            "intensity": float(self.intensity),
            "found": bool(self.found),
            "window_edge": bool(self.window_edge),
            "boundary": bool(self.boundary),
            "out_of_range": bool(self.out_of_range),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PeakMeasurement:
        return cls(
            peak_id=int(data.get("peak_id", 0) or 0),
            assignment=str(data.get("assignment", "")),
            reference={k: float(v) for k, v in (data.get("reference") or {}).items()},
            positions={k: float(v) for k, v in (data.get("positions") or {}).items()},
            deltas={k: float(v) for k, v in (data.get("deltas") or {}).items()},
            intensity=float(data.get("intensity", 0.0) or 0.0),
            found=bool(data.get("found", True)),
            window_edge=bool(data.get("window_edge", False)),
            boundary=bool(data.get("boundary", False)),
            out_of_range=bool(data.get("out_of_range", False)),
        )


def read_reference_peaks(path: Path | str) -> list[dict[str, Any]]:
    """读参考峰表(.list / 旧 CSV),只保留至少有一个核位置的峰。"""
    from core.peaks.peak_table import load_peaks

    peaks = load_peaks(Path(path))
    usable = [row for row in peaks if peak_coordinates(row, None)]
    if not usable:
        raise MeasurementError(f"参考峰表没有可测量的峰位: {path}")
    return usable


def peak_coordinates(
    row: dict[str, Any], axes: SpectrumAxes | None
) -> dict[str, float]:
    """峰表行 → {核名: ppm}。

    - 2D 行用命名键 ``H_shift`` / ``N_shift`` / ``C_shift``;
    - ``F{k}_shift`` 按逻辑维 k(FDDIMORDER)映射到数据轴核名;
    - ``axes=None`` 时只做命名键解析(用于筛掉空行)。
    """
    coords: dict[str, float] = {}
    if axes is not None:
        for logical, storage in enumerate(axes.logical_to_storage):
            value = row.get(f"F{logical + 1}_shift")
            if value is None or str(value) == "":
                continue
            nucleus = axes.nuclei[storage] if storage < len(axes.nuclei) else ""
            if nucleus:
                coords.setdefault(nucleus, float(value))
    for key, nucleus in _NAMED_KEYS:
        value = row.get(key)
        if value is not None and str(value) != "":
            coords.setdefault(nucleus, float(value))
    return coords


def _parabolic_offset(y_minus: float, y_zero: float, y_plus: float) -> float:
    """三点抛物线顶点偏移(点为单位,限制在 ±0.5)。"""
    denom = y_minus - 2.0 * y_zero + y_plus
    if not np.isfinite(denom) or abs(denom) < 1e-12:
        return 0.0
    offset = 0.5 * (y_minus - y_plus) / denom
    if not np.isfinite(offset):
        return 0.0
    return float(max(-0.5, min(0.5, offset)))


def measure_peak_positions(
    spectrum_path: Path | str,
    peaks: Sequence[dict[str, Any]],
    *,
    window_pts: int = 3,
    sign: str = "abs",
    refine: str = "parabolic",
    nuclei: Iterable[str] | None = None,
) -> list[PeakMeasurement]:
    """在 ``spectrum_path`` 上测量 ``peaks`` 的亚像素峰位。

    sign:``"abs"``(默认,正负峰都追)| ``"positive"`` | ``"negative"``;
    refine:``"parabolic"``(默认)| ``"none"``(只取整数点极大值)。
    nuclei 非空时只测量这些核(其余核只保留参考值,不参与扫描统计)。
    """
    path = Path(spectrum_path)
    if not path.is_file():
        raise MeasurementError(f"谱图不存在: {path}")
    if window_pts < 0:
        raise MeasurementError("window_pts 不能为负")
    if sign not in ("abs", "positive", "negative"):
        raise MeasurementError(f"未知 sign: {sign}")
    if refine not in ("parabolic", "none"):
        raise MeasurementError(f"未知 refine: {refine}")

    axes = read_spectrum_axes(path)
    data = np.asarray(axes.data, dtype=float)
    wanted = {str(n) for n in nuclei} if nuclei else None
    results: list[PeakMeasurement] = []
    for index, row in enumerate(peaks, start=1):
        peak_id = int(row.get("Peak_ID", index) or index)
        assignment = str(row.get("label", "") or row.get("Assignment", "") or "")
        if assignment in ("?-?", "?-?-?"):
            assignment = ""
        coords = peak_coordinates(row, axes)
        measurement = PeakMeasurement(
            peak_id=peak_id, assignment=assignment, reference=coords
        )
        search_axes: list[int] = []
        centers: list[int] = []
        for nucleus, ppm_value in coords.items():
            if wanted is not None and nucleus not in wanted:
                continue
            axis = axes.storage_of(nucleus)
            if axis is None:
                continue
            axis_ppm = np.asarray(axes.ppm[axis], dtype=float)
            size = int(data.shape[axis])
            if axis_ppm.size and (
                ppm_value < float(np.min(axis_ppm))
                or ppm_value > float(np.max(axis_ppm))
            ):
                # 参考位置落在谱范围外:标记后仍按端点附近搜索,
                # 便于使用者看清偏了多少,而不是直接丢弃该峰。
                measurement.out_of_range = True
            fraction = axes.fraction_from_ppm(axis, ppm_value)
            search_axes.append(axis)
            centers.append(int(min(max(round(fraction), 0), size - 1)))
        if not search_axes:
            measurement.found = False
            results.append(measurement)
            continue

        # bounds 必须按「数据轴」索引:search_axes 的顺序是核在峰表里出现的
        # 顺序(1H 常排在 15N 之前),按 search_axes 顺序建表会让窗口转置。
        bounds: dict[int, tuple[int, int]] = {}
        for axis, center in zip(search_axes, centers):
            size = int(data.shape[axis])
            bounds[axis] = (
                max(0, center - window_pts), min(size - 1, center + window_pts)
            )
        slices = tuple(
            slice(bounds[axis][0], bounds[axis][1] + 1)
            if axis in bounds
            else slice(None)
            for axis in range(data.ndim)
        )
        block = data[slices]
        if block.size == 0 or not np.any(np.isfinite(block)):
            measurement.found = False
            results.append(measurement)
            continue
        if sign == "abs":
            values = np.abs(block)
        elif sign == "positive":
            values = block
        else:
            values = -block
        flat = int(np.nanargmax(values))
        relative = np.unravel_index(flat, values.shape)
        best = [
            (bounds[axis][0] + int(relative[axis]))
            if axis in bounds
            else int(relative[axis])
            for axis in range(data.ndim)
        ]
        measurement.intensity = float(
            data[tuple(best)] if np.isfinite(data[tuple(best)]) else 0.0
        )
        for axis in search_axes:
            lo, hi = bounds[axis]
            size = int(data.shape[axis])
            if best[axis] <= 0 or best[axis] >= size - 1:
                measurement.boundary = True
            if best[axis] in (lo, hi) and not (
                (best[axis] == lo == 0) or (best[axis] == hi == size - 1)
            ):
                measurement.window_edge = True

        # 逐个参与测量的轴做亚像素 refine(其余轴固定在最优点)
        fractions = [float(index_) for index_ in best]
        if refine == "parabolic":
            for axis in search_axes:
                position = best[axis]
                size = int(data.shape[axis])
                if position <= 0 or position >= size - 1:
                    continue
                profile = []
                for offset in (-1, 0, 1):
                    index_tuple = list(best)
                    index_tuple[axis] = position + offset
                    value = float(data[tuple(index_tuple)])
                    if sign == "abs":
                        value = abs(value)
                    elif sign == "negative":
                        value = -value
                    profile.append(value)
                fractions[axis] = position + _parabolic_offset(*profile)

        for nucleus, _ppm_value in coords.items():
            if wanted is not None and nucleus not in wanted:
                continue
            axis = axes.storage_of(nucleus)
            if axis is None:
                continue
            measurement.positions[nucleus] = axes.ppm_at_fraction(
                axis, fractions[axis]
            )
            measurement.deltas[nucleus] = (
                measurement.positions[nucleus] - float(coords[nucleus])
            )
        results.append(measurement)
    return results


def pick_reference_peaks(
    session: Any,
    *,
    sigma_multiplier: float | None = None,
    out_path: Path | str | None = None,
) -> Path:
    """在参考谱上用 NMRForge 选峰,得到固定峰表(供扫描追踪)。

    ``out_path`` 非空时把峰表复制一份到该路径(研究目录内留档)。
    """
    from workflow.pick_peaks import pick_peaks

    dataset = session.dataset
    if dataset is None:
        raise MeasurementError("研究里还没有数据集")
    result = pick_peaks(
        session.manager,
        dataset.exp_id,
        dataset.data_id,
        sigma_multiplier=sigma_multiplier,
    )
    peak_path = Path(str(result.get("peak_path", "")))
    if not peak_path.is_file():
        raise MeasurementError(f"选峰没有产出峰表: {result}")
    if out_path is not None:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(peak_path.read_text(encoding="utf-8"), encoding="utf-8")
        return target
    return peak_path


__all__ = [
    "PeakMeasurement",
    "measure_peak_positions",
    "peak_coordinates",
    "pick_reference_peaks",
    "read_reference_peaks",
]
