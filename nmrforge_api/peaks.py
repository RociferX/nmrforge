"""峰位测量:在每张候选谱上追踪同一批参考峰的亚像素位置。

为什么不用「重新选峰」做这项研究:选峰是阈值检测,处理参数一变就可能多峰/
漏峰,峰位差里混入「换了另一个峰」的成分。本模块固定参考峰表,在每个峰位附近
的小窗口内找极值并做抛物线亚像素refine,给出**同一批峰的连续位置**,这才是
「处理参数 → 峰位不确定度」的观测量。

方法(可写进论文方法部分):

1. 参考峰表给出每个峰的核素位置(ppm)与 assignment;
2. ppm → 该谱数据轴上的分数索引(与选峰同源:ORIG 优先、回退 CAR,
   并按 FDDIMORDER 把逻辑维映射到数据轴,见 ``workflow.pick_peaks``);
3. 在**物理宽度**定义的窗口内取 ``|强度|``(默认)极值,得到整数索引——
   窗口默认按该轴核素线宽折算 ppm(``core.peaks.axis_units``),再按当前谱
   点距换算成点数;也可用 ``window_ppm`` 显式给 ppm,或用 ``window_pts``
   强制点数(点数口径会随零填零改变覆盖宽度,不推荐跨分辨率比较);
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

from core.peaks import axis_units
from nmrforge_api.errors import MeasurementError
from workflow.pick_peaks import SpectrumAxes, read_spectrum_axes

# 峰表命名键 → 核名(core.peaks.peak_table 的 2D 行约定)
_NAMED_KEYS: tuple[tuple[str, str], ...] = (
    ("H_shift", "1H"),
    ("N_shift", "15N"),
    ("C_shift", "13C"),
)


def reference_peak_id(peak_id: Any) -> str:
    """峰序号 → 稳定身份 ``R0001``(参考峰表建立后不再变化)。

    参考峰表给每个峰建立 reference_peak_id;后续所有 workflow 的峰表都带这一
    列(未检测到的峰 detected=false 但保留记录),下游据此把峰匹配回同一身份。
    """
    try:
        number = int(peak_id)
    except (TypeError, ValueError):
        return str(peak_id or "")
    return f"R{number:04d}"


@dataclass
class PeakMeasurement:
    """一个参考峰在一张谱上的位置测量结果。"""

    peak_id: int
    assignment: str
    # 稳定峰身份(参考峰表建立的 reference_peak_id,如 R0001)
    reference_peak_id: str = ""
    reference: dict[str, float] = field(default_factory=dict)
    positions: dict[str, float] = field(default_factory=dict)
    deltas: dict[str, float] = field(default_factory=dict)
    intensity: float = 0.0
    # 该谱噪声 σ(robust MAD)与逐峰 SNR = |intensity| / σ(留档可复算)
    noise_sigma: float = 0.0
    snr: float = 0.0
    found: bool = True
    window_edge: bool = False
    boundary: bool = False
    out_of_range: bool = False
    # 峰定位诊断(2026-09-13):实际方法/是否回退/高斯拟合 QC;空=仅参考值
    localization: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "peak_id": int(self.peak_id),
            "assignment": self.assignment,
            "reference_peak_id": self.reference_peak_id,
            "reference": {k: float(v) for k, v in self.reference.items()},
            "positions": {k: float(v) for k, v in self.positions.items()},
            "deltas": {k: float(v) for k, v in self.deltas.items()},
            "intensity": float(self.intensity),
            "noise_sigma": float(self.noise_sigma),
            "snr": float(self.snr),
            "found": bool(self.found),
            "window_edge": bool(self.window_edge),
            "boundary": bool(self.boundary),
            "out_of_range": bool(self.out_of_range),
            "localization": {
                str(k): v for k, v in (self.localization or {}).items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PeakMeasurement:
        return cls(
            peak_id=int(data.get("peak_id", 0) or 0),
            assignment=str(data.get("assignment", "")),
            reference_peak_id=str(
                data.get("reference_peak_id")
                or reference_peak_id(data.get("peak_id", 0))
            ),
            reference={k: float(v) for k, v in (data.get("reference") or {}).items()},
            positions={k: float(v) for k, v in (data.get("positions") or {}).items()},
            deltas={k: float(v) for k, v in (data.get("deltas") or {}).items()},
            intensity=float(data.get("intensity", 0.0) or 0.0),
            noise_sigma=float(data.get("noise_sigma", 0.0) or 0.0),
            snr=float(data.get("snr", 0.0) or 0.0),
            found=bool(data.get("found", True)),
            window_edge=bool(data.get("window_edge", False)),
            boundary=bool(data.get("boundary", False)),
            out_of_range=bool(data.get("out_of_range", False)),
            localization=dict(data.get("localization") or {}),
        )


def _read_ppm_csv(path: Path) -> list[dict[str, Any]] | None:
    """读「研究项目 CSV」格式(``peak_id,H_ppm,N_ppm[,height,linewidth,volume]``)。

    下游研究项目从公开库导出的参考峰表常是这个格式;不是该格式时返回 None,
    交给 Poky/旧 CSV 解析器处理。
    """
    import csv

    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    header = [cell.strip().lower() for cell in lines[0].split(",")]
    if "h_ppm" not in header or "n_ppm" not in header:
        return None
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(csv.DictReader(lines), start=1):
        normalized = {
            str(key).strip().lower(): value
            for key, value in row.items()
            if key
        }

        def _num(name: str) -> str:
            value = normalized.get(name, "")
            return "" if value in (None, "") else str(value)

        raw_id = str(normalized.get("peak_id", "") or "").strip()
        label = raw_id.split(":", 1)[1] if ":" in raw_id else raw_id
        rows.append(
            {
                "Peak_ID": index,
                "label": label,
                "H_shift": _num("h_ppm"),
                "N_shift": _num("n_ppm"),
                "Intensity": _num("height") or _num("intensity"),
                "peak_id_raw": raw_id,
            }
        )
    return rows


def read_reference_peaks(path: Path | str) -> list[dict[str, Any]]:
    """读参考峰表,只保留至少有一个核位置的峰。

    支持三种格式:

    - Poky/Sparky ``.list``(NMRForge 写出的标准峰表);
    - NMRForge 旧 CSV(``H_shift``/``N_shift`` 列);
    - 研究项目 CSV(``peak_id,H_ppm,N_ppm,height,linewidth,volume``,
      公开库导出的参考峰表)。
    """
    from core.peaks.peak_table import load_peaks

    target = Path(path)
    if not target.is_file():
        raise MeasurementError(f"参考峰表不存在: {target}")
    peaks = _read_ppm_csv(target)
    if peaks is None:
        peaks = load_peaks(target)
    # 稳定峰身份:参考峰表建立 reference_peak_id,后续 workflow 峰表沿用
    for position, row in enumerate(peaks, start=1):
        if not row.get('reference_peak_id'):
            row['reference_peak_id'] = reference_peak_id(
                row.get('Peak_ID') or position
            )
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


def _add_nucleus_localization(
    record: dict[str, Any], axes: SpectrumAxes
) -> None:
    """把高斯诊断按**核名**补进记录(FWHM_H/FWHM_N 不能按数据轴序猜)。

    ``core.peaks.localize`` 的派生量按数据轴序给出(``fwhm_f1`` = 轴 0),
    而统一峰表的 H/N 列按核名取值:这里按 ``axes.nuclei`` 显式映射,未知核
    名不写(表里回落 NaN),不猜。
    """
    if str(record.get('actual_method', '')) != 'gaussian':
        return
    fwhm: dict[str, float] = {}
    sigma: dict[str, float] = {}
    for index, nucleus in enumerate(axes.nuclei[:2]):
        if not nucleus:
            continue
        value = record.get(f'fwhm_f{index + 1}')
        if value is not None:
            fwhm[nucleus] = float(value)
        value = record.get(f'sigma_f{index + 1}')
        if value is not None:
            sigma[nucleus] = float(value)
    if fwhm:
        record['fwhm_by_nucleus'] = fwhm
    if sigma:
        record['sigma_by_nucleus'] = sigma


def _parabolic_offset(y_minus: float, y_zero: float, y_plus: float) -> float:
    """三点抛物线顶点偏移(点为单位,限制在 ±0.5)。"""
    denom = y_minus - 2.0 * y_zero + y_plus
    if not np.isfinite(denom) or abs(denom) < 1e-12:
        return 0.0
    offset = 0.5 * (y_minus - y_plus) / denom
    if not np.isfinite(offset):
        return 0.0
    return float(max(-0.5, min(0.5, offset)))


DEFAULT_WINDOW_PTS_FALLBACK = 3  # 无法按 ppm 换算时的点数回退


def window_points_by_axis(
    axes: SpectrumAxes,
    *,
    window_pts: int | None = None,
    window_ppm: float | None = None,
) -> dict[int, dict[str, Any]]:
    """每轴搜索窗口 → {轴: {points, ppm, source, nucleus, obs_mhz, ppm_per_point}}。

    优先级:``window_ppm``(物理宽度,逐轴换算)> ``window_pts``(显式点数,
    跨分辨率不可比)> 自动(该轴核素线宽 ×
    ``axis_units.MEASUREMENT_WINDOW_LINEWIDTH_FACTOR`` 折算 ppm)。
    每轴返回 ``ppm``(请求宽度)与 ``effective_ppm``(换算后实际覆盖宽度,
    受整数点数取整影响),以及该轴点距 ``ppm_per_point``——跨分辨率对照用。
    """
    out: dict[int, dict[str, Any]] = {}
    for axis in range(axes.ndim):
        nucleus = axes.nuclei[axis] if axis < len(axes.nuclei) else ""
        obs = axes.obs[axis] if axis < len(axes.obs) else 0.0
        axis_ppm = axes.ppm[axis]
        step = axis_units.ppm_per_point(axis_ppm)
        if window_pts is not None:
            points = max(0, int(window_pts))
            width = axis_units.ppm_for_points(axis_ppm, points)
            source = "points(显式)"
        else:
            width = (
                float(window_ppm)
                if window_ppm is not None
                else axis_units.measurement_window_ppm(nucleus, obs)
            )
            source = (
                "ppm(显式)" if window_ppm is not None else "ppm(自动:1.5×线宽)"
            )
            points = axis_units.points_for_ppm(axis_ppm, width)
            if points <= 0:
                points = int(DEFAULT_WINDOW_PTS_FALLBACK)
                width = axis_units.ppm_for_points(axis_ppm, points)
                source = "points(回退:无法换算)"
        out[axis] = {
            "points": int(points),
            "ppm": round(float(width), 6),
            "effective_ppm": round(
                axis_units.ppm_for_points(axis_ppm, points), 6
            ),
            "source": source,
            "nucleus": nucleus,
            "obs_mhz": round(float(obs), 4),
            "ppm_per_point": round(step, 6),
        }
    return out


def measure_peak_positions(
    spectrum_path: Path | str,
    peaks: Sequence[dict[str, Any]],
    *,
    window_pts: int | None = None,
    window_ppm: float | None = None,
    axes: SpectrumAxes | None = None,
    sign: str = "abs",
    refine: str = "parabolic",
    nuclei: Iterable[str] | None = None,
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
    noise_sigma: float | None = None,
) -> list[PeakMeasurement]:
    """在 ``spectrum_path`` 上测量 ``peaks`` 的亚像素峰位。

    sign:``"abs"``(默认,正负峰都追)| ``"positive"`` | ``"negative"``;
    refine:``"parabolic"``(默认,3 点抛物线)| ``"none"``(只取整数点极大
    值)| ``"gaussian"``(2D 高斯拟合,**仅 2D**;ROI 为 ppm 物理宽度
    ``roi_f1_ppm``/``roi_f2_ppm``,缺省读 config ``peaks.localization``;
    拟合失败回退抛物线且逐峰记录 requested/actual/原因,不静默)。
    nuclei 非空时只测量这些核(其余核只保留参考值,不参与扫描统计)。

    搜索窗口:默认按物理宽度(该轴核素线宽 ×1.5 折算 ppm)逐轴换算成点数,
    因此**零填零不会改变窗口覆盖的 ppm 宽度**;``window_ppm`` 显式给物理
    宽度,``window_pts`` 强制点数(不推荐)。``axes`` 可传入已读好的谱轴
(避免重复读谱)。

    ``noise_sigma``:该谱噪声 σ。缺省用 ``core.qc.noise`` 的 robust MAD 估计;
    每峰的 ``SNR = |intensity| / σ`` 与 σ 一起写进测量结果(统一峰表 SNR 列),
    便于下游复算「峰强是否足以判定 detected」。

    Parameters
    ----------
    spectrum_path : Path | str
        要测量的谱(2D ``.ft2``;高斯精修仅支持 2D)。
    peaks : Sequence[dict[str, Any]]
        候选峰(统一峰表字段:``N_shift``/``H_shift``/``label``…)。
    window_pts : int, optional
        测量窗口半宽(点);与 ``window_ppm`` 二选一。
    window_ppm : float, optional
        测量窗口半宽(ppm)。物理量优先——按当前谱点距换算,填零后不漂移。
    axes : SpectrumAxes, optional
        预先读好的轴(多峰复用,省重复读谱)。
    sign : str, default "abs"
        取峰符号口径。
    refine : str, default "parabolic"
        ``parabolic`` 或 ``gaussian``;非 2D 给 ``gaussian`` 直接报错,不静默降级。
    nuclei : Iterable[str], optional
        轴核名;缺省按谱头推断。
    roi_f1_ppm, roi_f2_ppm : float, optional
        高斯拟合 ROI 半径(ppm)。
    noise_sigma : float, optional
        已知噪声 σ;缺省从谱估计。

    Returns
    -------
    list[PeakMeasurement]
        逐峰结果:位置、强度/SNR、是否检出、实际定位方法与拟合诊断。

    Raises
    ------
    MeasurementError
        谱不可读、非 2D 却要求高斯,或峰参数非法。

    Side effects
    ------------
    只读谱,不写文件(记录由调用方写)。

    Examples
    --------
        rows = measure_peak_positions("spectra/a.ft2", peaks, window_ppm=1.0)
    """
    path = Path(spectrum_path)
    if not path.is_file():
        raise MeasurementError(f"谱图不存在: {path}")
    if window_pts is not None and int(window_pts) < 0:
        raise MeasurementError("window_pts 不能为负")
    if sign not in ("abs", "positive", "negative"):
        raise MeasurementError(f"未知 sign: {sign}")
    if refine not in ("parabolic", "none", "gaussian"):
        raise MeasurementError(f"未知 refine: {refine}")

    spectrum_axes = axes if axes is not None else read_spectrum_axes(path)
    axes = spectrum_axes
    data = np.asarray(axes.data, dtype=float)
    # 高斯拟合仅 2D(读谱后再判维度:与峰定位同一口径,不静默降级)
    if refine == "gaussian" and data.ndim != 2:
        from core.peaks.localize import GAUSSIAN_UNSUPPORTED_MESSAGE

        raise MeasurementError(GAUSSIAN_UNSUPPORTED_MESSAGE)
    window_by_axis = window_points_by_axis(
        axes, window_pts=window_pts, window_ppm=window_ppm
    )
    from core.qc import noise as _noise

    sigma = (
        float(noise_sigma)
        if noise_sigma is not None
        else float(_noise.estimate(data).global_sigma)
    )
    wanted = {str(n) for n in nuclei} if nuclei else None
    results: list[PeakMeasurement] = []
    for index, row in enumerate(peaks, start=1):
        peak_id = int(row.get("Peak_ID", index) or index)
        assignment = str(row.get("label", "") or row.get("Assignment", "") or "")
        if assignment in ("?-?", "?-?-?"):
            assignment = ""
        coords = peak_coordinates(row, axes)
        measurement = PeakMeasurement(
            peak_id=peak_id,
            assignment=assignment,
            reference_peak_id=str(
                row.get('reference_peak_id') or reference_peak_id(peak_id)
            ),
            reference=coords,
            noise_sigma=sigma,
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
            half = int(window_by_axis.get(axis, {}).get("points", 0))
            bounds[axis] = (
                max(0, center - half), min(size - 1, center + half)
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
        measurement.snr = (
            abs(measurement.intensity) / sigma if sigma > 0 else 0.0
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
        if refine == "gaussian":
            # 2D 高斯定位(core.peaks.localize):ROI 物理宽度 + 失败回退
            from core.peaks.localize import localize_peak

            peak_sign = 1 if float(data[tuple(best)]) >= 0 else -1
            if sign == "positive":
                peak_sign = 1
            elif sign == "negative":
                peak_sign = -1
            ppm_axes = [np.asarray(a, dtype=float) for a in axes.ppm]
            loc = localize_peak(
                data,
                best,
                method="gaussian",
                sign=peak_sign,
                ppm_axes=ppm_axes,
                roi_f1_ppm=roi_f1_ppm,
                roi_f2_ppm=roi_f2_ppm,
                logical_axes=list(axes.logical_to_storage),
            )
            fractions = [float(v) for v in loc.position]
            measurement.localization = loc.to_dict(ppm_axes)
            _add_nucleus_localization(measurement.localization, axes)
        elif refine == "parabolic":
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
    details: dict[str, Any] | None = None,
    localization_method: str = "parabolic",
    gaussian_roi_f1_ppm: float | None = None,
    gaussian_roi_f2_ppm: float | None = None,
    dataset: Any | None = None,
) -> Path:
    """在参考谱上用 NMRForge 选峰,得到固定峰表(供扫描追踪)。

    ``out_path`` 非空时把峰表复制一份到该路径(研究目录内留档);
    ``details`` 非空时把选峰口径(``detection``:边距物理宽度/点数/
    各轴点距)写进该字典,供研究记录留档;``dataset`` 指定条件数据集
    (缺省用会话主条件)。

    Parameters
    ----------
    session : StudySession
        会话。
    sigma_multiplier : float, optional
        选峰阈值(σ 倍数);缺省取 config 默认。
    out_path : Path | str, optional
        目标 ``.list`` 路径;缺省写在参考目录。
    details : dict[str, Any], optional
        传入的空字典会填入选峰细节(峰数、阈值来源、定位方法)。
    localization_method : str, default "parabolic"
        亚格点精修方式(``gaussian`` 仅 2D)。
    gaussian_roi_f1_ppm, gaussian_roi_f2_ppm : float, optional
        高斯 ROI 半径(ppm)。
    dataset : Any, optional
        指定数据集(多条件时)。

    Returns
    -------
    Path
        生成的 Poky ``.list`` 路径。

    Raises
    ------
    MeasurementError
        选峰失败或参数非法;不静默产出空峰表。

    Side effects
    ------------
    写峰表并登记 ``pick_peaks`` 运行记录;不修改谱。

    Examples
    --------
        details: dict = {}
        path = pick_reference_peaks(study, sigma_multiplier=35, details=details)
    """
    from workflow.pick_peaks import pick_peaks

    dataset = dataset or session.dataset
    if dataset is None:
        raise MeasurementError("研究里还没有数据集")
    result = pick_peaks(
        session.manager,
        dataset.exp_id,
        dataset.data_id,
        sigma_multiplier=sigma_multiplier,
        localization_method=localization_method,
        gaussian_roi_f1_ppm=gaussian_roi_f1_ppm,
        gaussian_roi_f2_ppm=gaussian_roi_f2_ppm,
    )
    if details is not None:
        details["detection"] = result.get("detection") or {}
        details["localization"] = result.get("localization") or {}
        details["peak_count"] = int(result.get("peak_count", 0) or 0)
    peak_path = Path(str(result.get("peak_path", "")))
    if not peak_path.is_file():
        raise MeasurementError(f"选峰没有产出峰表: {result}")
    if out_path is not None:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(peak_path.read_text(encoding="utf-8"), encoding="utf-8")
        return target
    return peak_path


#: 组合模式独立选峰的默认 detection 阈值(σ 倍数);参考层给了就用参考的实际值
DEFAULT_DETECTION_SIGMA = 35.0


def detect_and_localize(
    spectrum_path: Path | str,
    *,
    sigma_multiplier: float | None = None,
    edge_margin_ppm: float | None = None,
    edge_margin_points: int | None = None,
    method: str = "parabolic",
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
    sign_mode: str = "dominant",
    axes: SpectrumAxes | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """在**该组合自己的谱**上按给定 detection 阈值独立选峰(2026-09-14 规范)。

    - 检测口径与项目选峰同源:物理边距(`axis_units`)+ `peak_detection.detect`
      (`sigma_multiplier` 同时作为 `min_snr`)+ dominant 符号口径;
    - 精修 `method`:`"parabolic"`(用检测阶段的三点抛物线位置)| `"gaussian"`
      (逐峰 2D 高斯拟合,仅 2D);
    - 返回 `(rows, meta)`:`rows` 是该谱自己的峰(``peak_id`` = 本谱序号,
      ``reference_peak_id`` 留空——与参考峰表的匹配是**外部**工作);`meta` 记录
      实际阈值/边距/符号口径/噪声 σ/峰数/定位 QC。

    没有 `max_peaks`:锁定阈值下检出多少峰就写多少峰(用户 2026-09-14)。

    Parameters
    ----------
    spectrum_path : Path | str
        要选峰的谱。
    sigma_multiplier : float, optional
        选峰阈值(σ 倍数);缺省取 config。
    edge_margin_ppm : float, optional
        边缘排除半径(物理宽度);与 ``edge_margin_points`` 二选一。
    edge_margin_points : int, optional
        边缘排除半径(点)。
    method : str, default "parabolic"
        亚格点精修方式(``gaussian`` 仅 2D)。
    roi_f1_ppm, roi_f2_ppm : float, optional
        高斯 ROI 半径(ppm)。
    sign_mode : str, default "dominant"
        峰符号口径。
    axes : SpectrumAxes, optional
        预先读好的轴。

    Returns
    -------
    tuple[list[dict[str, Any]], dict[str, Any]]
        ``(rows, details)``:峰行(与统一峰表同字段口径)与选峰细节(阈值/边距/方法/计数)。

    Raises
    ------
    MeasurementError
        谱不可读或参数非法。

    Side effects
    ------------
    只读谱;是否写峰表由调用方决定。

    Examples
    --------
        rows, details = detect_and_localize("spectra/a.ft2", sigma_multiplier=35)
    """
    from core.qc import noise as _noise
    from core.qc import peak_detection as _detect
    from workflow.pick_peaks import read_spectrum_axes as _read_axes

    path = Path(spectrum_path)
    if not path.is_file():
        raise MeasurementError(f"谱图不存在: {path}")
    spectrum_axes = axes if axes is not None else _read_axes(path)
    data = np.asarray(spectrum_axes.data, dtype=float)
    threshold = (
        float(sigma_multiplier)
        if sigma_multiplier is not None and float(sigma_multiplier) > 0
        else float(DEFAULT_DETECTION_SIGMA)
    )
    # 精修方式先校验(不支持的算法/维度直接报错,不静默换算法)
    wanted = str(method).strip().lower() or "parabolic"
    if wanted not in ("parabolic", "gaussian"):
        raise MeasurementError(f"未知峰定位方法: {method!r}(parabolic / gaussian)")
    if wanted == "gaussian" and data.ndim != 2:
        from core.peaks.localize import GAUSSIAN_UNSUPPORTED_MESSAGE as _msg

        raise MeasurementError(_msg)
    nuclei = list(spectrum_axes.nuclei)
    axis0_nucleus = nuclei[0] if nuclei else ""
    obs0 = float(spectrum_axes.obs[0]) if spectrum_axes.obs else 0.0
    try:
        from backend.config import load_processing_defaults

        linewidths = load_processing_defaults().get("linewidth_hz") or {}
    except Exception:  # noqa: BLE001 - 配置不可读用内置默认
        linewidths = {}
    if edge_margin_points is not None:
        edge_points = max(0, int(edge_margin_points))
        edge_ppm = axis_units.ppm_for_points(spectrum_axes.ppm[0], edge_points)
        edge_source = "points(显式)"
    else:
        if edge_margin_ppm is not None:
            edge_ppm = float(edge_margin_ppm)
            edge_source = "ppm(显式)"
        else:
            edge_ppm = axis_units.edge_margin_ppm(
                axis0_nucleus, obs0, linewidth_hz_by_nucleus=linewidths
            )
            edge_source = "ppm(物理宽度)"
        edge_points = axis_units.points_for_ppm(spectrum_axes.ppm[0], edge_ppm)
        if edge_points <= 0:
            edge_points = 1
            edge_ppm = axis_units.ppm_for_points(spectrum_axes.ppm[0], edge_points)
            edge_source = "points(回退:无法换算)"
    sigma = float(_noise.estimate(data).global_sigma)
    peaks = _detect.detect(
        data,
        _detect.PeakDetectionParams(
            sign_mode="both",
            sigma_multiplier=threshold,
            min_snr=threshold,
            edge_margin=edge_points,
        ),
    )
    resolved_sign = str(sign_mode or "dominant")
    if resolved_sign in ("dominant", "auto"):
        peaks = _detect.keep_dominant(peaks)
        resolved_sign = "dominant"
    axis_h = spectrum_axes.storage_of("1H")
    axis_n = spectrum_axes.storage_of("15N")
    rows: list[dict[str, Any]] = []
    fallback_reasons: dict[str, int] = {}
    n_fallback = 0
    n_boundary = 0
    for index, peak in enumerate(peaks, start=1):
        position = tuple(float(v) for v in peak.position)
        record: dict[str, Any] = {}
        if wanted == "gaussian":
            from core.peaks.localize import localize_peak as _localize

            loc = _localize(
                data,
                [int(round(v)) for v in peak.position],
                method="gaussian",
                sign=int(peak.sign),
                ppm_axes=list(spectrum_axes.ppm),
                roi_f1_ppm=roi_f1_ppm,
                roi_f2_ppm=roi_f2_ppm,
                logical_axes=list(spectrum_axes.logical_to_storage),
            )
            position = tuple(float(v) for v in loc.position)
            record = loc.to_dict(spectrum_axes.ppm)
            _add_nucleus_localization(record, spectrum_axes)
            if record.get("fallback"):
                n_fallback += 1
                reason = str(record.get("fallback_reason", "") or "")
                fallback_reasons[reason] = fallback_reasons.get(reason, 0) + 1
            if record.get("boundary_hit"):
                n_boundary += 1
        h_ppm = (
            float(spectrum_axes.ppm_at_fraction(axis_h, position[axis_h]))
            if axis_h is not None
            else float("nan")
        )
        n_ppm = (
            float(spectrum_axes.ppm_at_fraction(axis_n, position[axis_n]))
            if axis_n is not None
            else float("nan")
        )
        fwhm_map = {
            str(k): float(v) for k, v in (record.get("fwhm_by_nucleus") or {}).items()
        }
        rows.append(
            {
                "peak_id": int(index),
                "reference_peak_id": "",
                "assignment": "",
                "H_ppm": h_ppm,
                "N_ppm": n_ppm,
                "intensity": float(peak.height),
                "SNR": float(peak.snr),
                "detected": True,
                "localization_method": wanted,
                "localization_requested": str(record.get("requested_method") or wanted),
                "fallback": bool(record.get("fallback")),
                "fallback_reason": str(record.get("fallback_reason", "") or ""),
                "fit_success": (
                    bool(record.get("fit_success")) if wanted == "gaussian" else None
                ),
                "FWHM_H": fwhm_map.get("1H") if wanted == "gaussian" else None,
                "FWHM_N": fwhm_map.get("15N") if wanted == "gaussian" else None,
                "fit_rmse": (
                    float(record["fit_rmse"])
                    if wanted == "gaussian" and record.get("fit_rmse") is not None
                    else None
                ),
                "boundary_hit": (
                    bool(record.get("boundary_hit")) if wanted == "gaussian" else None
                ),
            }
        )
    meta = {
        "sigma_multiplier": threshold,
        "min_snr": threshold,
        "sign_mode": resolved_sign,
        "edge_margin_ppm": round(float(edge_ppm), 6),
        "edge_margin_points": int(edge_points),
        "edge_margin_source": edge_source,
        "noise_sigma": sigma,
        "localization_method": wanted,
        "n_peaks": len(rows),
        "n_fallback": int(n_fallback),
        "fallback_reasons": fallback_reasons,
        "n_boundary_hit": int(n_boundary),
    }
    return rows, meta


__all__ = [
    "DEFAULT_DETECTION_SIGMA",
    "DEFAULT_WINDOW_PTS_FALLBACK",
    "PeakMeasurement",
    "detect_and_localize",
    "measure_peak_positions",
    "peak_coordinates",
    "pick_reference_peaks",
    "read_reference_peaks",
    "reference_peak_id",
    "window_points_by_axis",
]
