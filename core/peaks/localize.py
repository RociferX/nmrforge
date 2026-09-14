"""峰定位(peak localization)统一入口:检测之后的**亚格点精修**方法分派。

流程与职责边界(2026-09-13,用户需求):

    peak detection(core.qc.peak_detection)
              ↓  候选峰整数格极大值
        peak localization(本模块)
          ├── parabolic  既有 3 点抛物线(默认,行为**完全不变**)
          └── gaussian   2D 高斯拟合(**仅 2D**;见 core.peaks.gaussian_fit)

高斯拟合**不做峰检测**:它只在已检出的 candidate 附近做局部拟合,阈值/噪声
估计/符号模式等检测逻辑一律不动。

ROI 一律按**物理宽度(ppm)**给出(config ``peaks.localization.gaussian_roi_f*_ppm``
或函数参数),运行时用 ``core.peaks.axis_units`` 按当前谱点距换算成点数——与
选峰边距/测量窗口同一口径,填零不会改变 ROI 覆盖的 ppm 范围。

失败不静默:``PeakLocalization`` 同时带 ``requested_method``/``actual_method``/
``success``/``fallback``/``reason``,调用方负责把它们写进运行记录与峰表附件。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.peaks import axis_units
from core.peaks.gaussian_fit import (
    GaussianFitResult,
    fit_gaussian_2d,
)
from core.qc import peak_detection

LOCALIZATION_METHODS: tuple[str, ...] = ("parabolic", "gaussian")
DEFAULT_LOCALIZATION_METHOD = "parabolic"
#: 高斯拟合仅支持 2D(用户需求:GUI/API 必须明确拒绝其它维度)
GAUSSIAN_SUPPORTED_NDIM = 2
GAUSSIAN_UNSUPPORTED_MESSAGE = (
    "Gaussian peak fitting is currently supported only for 2D spectra."
)
#: ROI 半径默认值(ppm)。直接维峰窄、间接维峰宽,取量级合理的缺省;
#: 实际值可通过 config `peaks.localization` 或函数参数覆盖。
DEFAULT_GAUSSIAN_ROI_F1_PPM = 1.5
DEFAULT_GAUSSIAN_ROI_F2_PPM = 0.25
#: 拟合窗口每轴**最大半宽(点)**:细网格(填零)下限制拟合规模,
#: 使单峰拟合成本不再随点数线性增长(2026-09-14 用户:填零翻倍后高斯拟合 26×)。
DEFAULT_GAUSSIAN_ROI_MAX_POINTS = 48
#: 单峰最小二乘最大函数求值次数:失败拟合不再跑满(默认 400 → 200)。
DEFAULT_GAUSSIAN_MAX_NFEV = 200
FWHM_FACTOR = 2.3548200450309493  # 2*sqrt(2 ln 2)

LOCALIZATION_LABELS: dict[str, str] = {
    "parabolic": "抛物线",
    "gaussian": "2D 高斯拟合",
}

# 峰表附件(与 Poky .list 同目录同名,加后缀):逐峰高斯诊断
LOCALIZATION_RECORDS_SUFFIX = ".localization.json"


class LocalizationError(ValueError):
    """峰定位参数非法(未知方法/维度不支持)。"""


@dataclass
class PeakLocalization:
    """一个候选峰的定位结果(requested/actual 分离,便于记录与比较)。"""

    requested_method: str
    actual_method: str
    position: tuple[float, ...] = ()
    success: bool = True
    fallback: bool = False
    reason: str = ""
    gaussian: GaussianFitResult | None = None
    ppm: dict[str, float] = field(default_factory=dict)
    # 预计算的中心/宽度(ppm):按 F1/F2 逻辑轴给出,避免 to_dict 再猜轴序
    derived: dict[str, float] = field(default_factory=dict)
    # 拟合规模留档(ROI 点数/是否被上限截断/迭代预算)
    fit_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, ppm_axes: Sequence[Any] | None = None) -> dict[str, Any]:
        """记录用字典:方法溯源 + 高斯诊断(用户需求 §8 的字段名)。"""
        out: dict[str, Any] = {
            "localization_method": str(self.actual_method),
            "requested_method": str(self.requested_method),
            "actual_method": str(self.actual_method),
            "gaussian_fit_success": bool(
                self.success and self.requested_method == "gaussian"
            ),
            "fallback": bool(self.fallback),
            "fallback_reason": str(self.reason),
        }
        if self.fit_meta:
            out.update({str(k): v for k, v in self.fit_meta.items()})
        if self.ppm:
            out.update({f"position_ppm_{k}": float(v) for k, v in self.ppm.items()})
        if self.position:
            out["position_points"] = [float(v) for v in self.position]
        fit = self.gaussian
        if fit is None:
            return out
        out.update(fit.to_dict())
        out["fit_failure_reason"] = str(fit.reason)
        if self.derived:
            out.update({k: float(v) for k, v in self.derived.items()})
            return out
        if ppm_axes is not None and len(ppm_axes) >= 2 and len(fit.center) >= 2:
            # 数据轴序:0 = F1(间接),1 = F2(直接)
            axes = list(ppm_axes)
            step0 = axis_units.ppm_per_point(axes[0])
            step1 = axis_units.ppm_per_point(axes[1])
            out.update(
                {
                    "center_f1": ppm_from_point(axes[0], fit.center[0]),
                    "center_f2": ppm_from_point(axes[1], fit.center[1]),
                    "sigma_f1": float(fit.sigma[0]) * step0,
                    "sigma_f2": float(fit.sigma[1]) * step1,
                    "fwhm_f1": float(fit.sigma[0]) * step0 * FWHM_FACTOR,
                    "fwhm_f2": float(fit.sigma[1]) * step1 * FWHM_FACTOR,
                }
            )
        return out


def ppm_from_point(axis_ppm: Sequence[float], point: float) -> float:
    """分数索引 → ppm(线性插值,与选峰写表同一约定)。"""
    arr = np.asarray(axis_ppm, dtype=float)
    if arr.size == 0:
        return 0.0
    if arr.size == 1:
        return float(arr[0])
    return float(np.interp(float(point), np.arange(arr.size, dtype=float), arr))


def normalize_localization_method(method: Any) -> str:
    """方法名规范化(大小写/中文别名不敏感);未知方法报错。"""
    name = str(method or DEFAULT_LOCALIZATION_METHOD).strip().lower()
    aliases = {
        "parabolic": "parabolic",
        "parabola": "parabolic",
        "抛物线": "parabolic",
        "gaussian": "gaussian",
        "gaussian_fit": "gaussian",
        "gauss": "gaussian",
        "高斯": "gaussian",
        "高斯拟合": "gaussian",
    }
    if name not in aliases:
        raise LocalizationError(
            f"未知峰定位方法: {method!r}(可用: parabolic / gaussian)"
        )
    return aliases[name]


def localization_supported(method: Any, ndim: int) -> bool:
    """该维度是否支持该方法(高斯仅 2D;抛物线任意维)。"""
    try:
        name = normalize_localization_method(method)
    except LocalizationError:
        return False
    if name == "gaussian":
        return int(ndim) == GAUSSIAN_SUPPORTED_NDIM
    return True


def localization_label(method: Any) -> str:
    """方法显示名(日志/GUI 用)。"""
    try:
        return LOCALIZATION_LABELS[normalize_localization_method(method)]
    except LocalizationError:
        return str(method or "")


def load_localization_defaults(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """config ``peaks.localization`` 缺省值(读不到时用内置默认)。

    返回 ``{"method": "parabolic", "gaussian_roi_f1_ppm": float,
    "gaussian_roi_f2_ppm": float}``;非法值一律回退默认,不抛异常。
    """
    section: dict[str, Any] = {}
    try:
        from backend.config import load_config

        cfg = load_config(config) or {}
        section = (cfg.get("peaks") or {}).get("localization") or {}
        if not isinstance(section, dict):
            section = {}
    except Exception:  # noqa: BLE001 - 配置不可读用内置默认
        section = {}
    try:
        method = normalize_localization_method(
            section.get("method", DEFAULT_LOCALIZATION_METHOD)
        )
    except LocalizationError:
        method = DEFAULT_LOCALIZATION_METHOD

    def _positive(key: str, fallback: float) -> float:
        try:
            value = float(section.get(key, fallback))
        except (TypeError, ValueError):
            return float(fallback)
        if not np.isfinite(value) or value <= 0:
            return float(fallback)
        return value

    return {
        "method": method,
        "gaussian_roi_f1_ppm": _positive(
            "gaussian_roi_f1_ppm", DEFAULT_GAUSSIAN_ROI_F1_PPM
        ),
        "gaussian_roi_f2_ppm": _positive(
            "gaussian_roi_f2_ppm", DEFAULT_GAUSSIAN_ROI_F2_PPM
        ),
        "gaussian_roi_max_points": _positive_int(
            section.get("gaussian_roi_max_points"),
            DEFAULT_GAUSSIAN_ROI_MAX_POINTS,
        ),
        "gaussian_max_nfev": _positive_int(
            section.get("gaussian_max_nfev"), DEFAULT_GAUSSIAN_MAX_NFEV
        ),
    }


def _positive_int(value: Any, fallback: int) -> int:
    """正值整数(非法/非正回退 fallback)。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return int(fallback)
    return number if number > 0 else int(fallback)


def _finite_position(
    position: Sequence[float], index: Sequence[float]
) -> tuple[float, ...]:
    """位姿里的非有限分量(谱含 NaN 时抛物线会给 NaN)回退整数格候选。

    新增的高斯路径不能让 NaN 流进峰表/记录;既有抛物线函数本身保持原样
    (向后兼容),只在**高斯的回退出口**做这道防护。
    """
    return tuple(
        float(v) if np.isfinite(v) else float(index[i])
        for i, v in enumerate(position)
    )


def localize_peak_parabolic(data: Any, index: Sequence[int]) -> PeakLocalization:
    """既有抛物线定位(与 ``peak_detection._refined_index`` 同一实现)。"""
    real = np.real(np.asarray(data)).astype(float, copy=False)
    idx = [
        int(min(max(int(v), 0), real.shape[axis] - 1))
        for axis, v in enumerate(index)
    ]
    position = tuple(
        peak_detection.refine_parabolic(real, idx, axis)
        for axis in range(real.ndim)
    )
    return PeakLocalization(
        requested_method="parabolic",
        actual_method="parabolic",
        position=position,
        success=True,
    )


def localize_peak_gaussian_2d(
    data: Any,
    index: Sequence[int],
    *,
    sign: int = 1,
    ppm_axes: Sequence[Any] | None = None,
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
    logical_axes: Sequence[int] | None = None,
    max_rmse_ratio: float = 0.0,
    max_nfev: int = DEFAULT_GAUSSIAN_MAX_NFEV,
    roi_max_points: int | None = None,
) -> PeakLocalization:
    """2D 高斯定位:ROI(ppm)→点数 → 以抛物线结果为初值做局部拟合。

    非 2D 谱直接 ``LocalizationError``(不降级、不静默);拟合失败/不收敛/撞边界
    时**回退抛物线**并在 ``reason`` 里说明(不静默)。
    """
    real = np.real(np.asarray(data)).astype(float, copy=False)
    if real.ndim != GAUSSIAN_SUPPORTED_NDIM:
        raise LocalizationError(GAUSSIAN_UNSUPPORTED_MESSAGE)
    if ppm_axes is None or len(ppm_axes) < 2:
        raise LocalizationError("2D 高斯拟合需要各数据轴的 ppm 轴数组")
    axes_map = (
        [int(v) for v in logical_axes]
        if logical_axes is not None
        else list(range(real.ndim))
    )
    if len(axes_map) < 2:
        axes_map = list(range(real.ndim))
    defaults = load_localization_defaults()
    roi1 = float(
        roi_f1_ppm if roi_f1_ppm is not None else defaults["gaussian_roi_f1_ppm"]
    )
    roi2 = float(
        roi_f2_ppm if roi_f2_ppm is not None else defaults["gaussian_roi_f2_ppm"]
    )
    axis_f1, axis_f2 = int(axes_map[0]), int(axes_map[1])
    # 初值中心 = 抛物线结果(用户需求 §4):从同一 candidate 出发,可独立比较
    parabolic = localize_peak_parabolic(real, index)
    idx = [
        float(min(max(int(v), 0), real.shape[axis] - 1))
        for axis, v in enumerate(index)
    ]
    seed = (
        float(parabolic.position[0])
        if np.isfinite(parabolic.position[0])
        else idx[0],
        float(parabolic.position[1])
        if np.isfinite(parabolic.position[1])
        else idx[1],
    )
    # ROI 是**物理宽度**:F1/F2 各自的 ppm 半径按该轴的当前点距换算点数
    roi_points = [
        axis_units.points_for_ppm(ppm_axes[axis_f1], roi1, minimum=1),
        axis_units.points_for_ppm(ppm_axes[axis_f2], roi2, minimum=1),
    ]
    # 拟合规模上限:细网格(填零)下 ROI 点数随点距换算线性增长 → 单峰成本线性增长;
    # 每轴半宽设上限(默认 48 点),只影响比上限更细的网格(留档 roi_capped)。
    cap = int(
        roi_max_points
        if roi_max_points is not None
        else defaults["gaussian_roi_max_points"]
    )
    fit_points = [min(int(p), max(cap, 1)) for p in roi_points]
    roi_capped = [
        int(capped) != int(raw)
        for capped, raw in zip(fit_points, roi_points)
    ]
    if min(roi_points) <= 0:
        return PeakLocalization(
            requested_method="gaussian",
            actual_method="parabolic",
            position=_finite_position(parabolic.position, idx),
            success=False,
            fallback=True,
            reason="roi_unavailable",
        )
    roi_by_axis = [0.0, 0.0]
    roi_by_axis[axis_f1] = float(fit_points[0])
    roi_by_axis[axis_f2] = float(fit_points[1])
    fit = fit_gaussian_2d(
        real,
        seed=seed,
        roi=(roi_by_axis[0], roi_by_axis[1]),
        sign=1 if int(sign) >= 0 else -1,
        max_rmse_ratio=float(max_rmse_ratio or 0.0),
        max_nfev=int(max_nfev),
    )
    if not fit.success:
        # 回退抛物线:位置仍可用,但 requested/actual/原因全部留档(不静默)
        return PeakLocalization(
            requested_method="gaussian",
            actual_method="parabolic",
            position=_finite_position(parabolic.position, idx),
            success=False,
            fallback=True,
            reason=fit.reason,
            gaussian=fit,
        )
    position = tuple(
        fit.center[axis] if axis in (0, 1) else parabolic.position[axis]
        for axis in range(real.ndim)
    )
    ppm = {
        "F1": ppm_from_point(ppm_axes[axis_f1], position[axis_f1]),
        "F2": ppm_from_point(ppm_axes[axis_f2], position[axis_f2]),
    }
    step_f1 = axis_units.ppm_per_point(ppm_axes[axis_f1])
    step_f2 = axis_units.ppm_per_point(ppm_axes[axis_f2])
    sigma_f1 = float(fit.sigma[axis_f1])
    sigma_f2 = float(fit.sigma[axis_f2])
    derived = {
        "center_f1": ppm["F1"],
        "center_f2": ppm["F2"],
        "sigma_f1": sigma_f1 * step_f1,
        "sigma_f2": sigma_f2 * step_f2,
        "fwhm_f1": sigma_f1 * step_f1 * FWHM_FACTOR,
        "fwhm_f2": sigma_f2 * step_f2 * FWHM_FACTOR,
    }
    return PeakLocalization(
        requested_method="gaussian",
        actual_method="gaussian",
        position=position,
        success=True,
        gaussian=fit,
        ppm=ppm,
        derived=derived,
        fit_meta=_fit_meta(fit_points, roi_points, roi_capped, max_nfev),
    )


def _fit_meta(
    fit_points: list[int],
    roi_points: list[int],
    roi_capped: list[bool],
    max_nfev: int,
) -> dict[str, Any]:
    """拟合规模留档:实际半宽点数、被上限截断的轴、迭代预算。"""
    return {
        "roi_half_points": [int(p) for p in fit_points],
        "roi_half_points_uncapped": [int(p) for p in roi_points],
        "roi_capped": bool(any(roi_capped)),
        "roi_capped_axes": [
            axis for axis, flag in zip(("F1", "F2"), roi_capped) if flag
        ],
        "max_nfev": int(max_nfev),
    }


def localize_peak(
    data: Any,
    index: Sequence[int],
    *,
    method: Any = DEFAULT_LOCALIZATION_METHOD,
    sign: int = 1,
    ppm_axes: Sequence[Any] | None = None,
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
    logical_axes: Sequence[int] | None = None,
    max_rmse_ratio: float = 0.0,
    max_nfev: int = DEFAULT_GAUSSIAN_MAX_NFEV,
    roi_max_points: int | None = None,
) -> PeakLocalization:
    """统一峰定位入口:``method="parabolic"``(默认)或 ``"gaussian"``(仅 2D)。

    ``index`` 为候选峰的整数格极大值(每轴一个);抛物线方法逐轴独立精修,
    高斯方法把抛物线结果作为初值做 2D 局部拟合。两者对完全相同的 candidate
    独立运行,结果可直接比较。
    """
    name = normalize_localization_method(method)
    real = np.real(np.asarray(data))
    if name == "gaussian":
        if real.ndim != GAUSSIAN_SUPPORTED_NDIM:
            raise LocalizationError(GAUSSIAN_UNSUPPORTED_MESSAGE)
        return localize_peak_gaussian_2d(
            real,
            index,
            sign=sign,
            ppm_axes=ppm_axes,
            roi_f1_ppm=roi_f1_ppm,
            roi_f2_ppm=roi_f2_ppm,
            logical_axes=logical_axes,
            max_rmse_ratio=max_rmse_ratio,
            max_nfev=max_nfev,
            roi_max_points=roi_max_points,
        )
    return localize_peak_parabolic(real, index)


def localization_records_path(peak_path: Path | str) -> Path:
    """峰表附件路径:``<峰表>.localization.json``(与 .list 同目录)。"""
    path = Path(peak_path)
    return path.with_name(path.name + LOCALIZATION_RECORDS_SUFFIX)


def write_localization_records(
    peak_path: Path | str,
    records: Sequence[dict[str, Any]],
    *,
    meta: dict[str, Any] | None = None,
) -> Path:
    """写逐峰定位诊断(JSON 附件;Poky .list 本身格式不变)。"""
    target = localization_records_path(peak_path)
    payload = {
        "peak_table": str(Path(peak_path)),
        "meta": dict(meta or {}),
        "peaks": [dict(record) for record in records],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def trim_localization_records(peak_path: Path | str, keep: int) -> bool:
    """**同一路径**的峰表被就地裁剪后,同步截断附件(保持行序对齐)。

    附件行序与峰表行序一致(都按 |Intensity| 降序),保留前 ``keep`` 行即可。
    注意:接口的 ``max_peaks`` 裁的是**冻结参考表**(``reference.list``,从
    data 侧 `.list` 复制而来、本身没有附件),因此正常流程下本函数是防御性
    的 no-op——真机核对:data 侧 `.list` 76 峰 / 附件 76 条、参考表 60 峰,
    两边本来就各自自洽。附件不存在/损坏返回 False(不抛异常)。
    """
    target = localization_records_path(peak_path)
    if not target.is_file():
        return False
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    peaks = payload.get("peaks") if isinstance(payload, dict) else None
    if not isinstance(peaks, list) or len(peaks) <= int(keep):
        return False
    payload["peaks"] = peaks[: int(keep)]
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def read_localization_records(peak_path: Path | str) -> list[dict[str, Any]]:
    """读逐峰定位诊断;不存在/损坏返回空表(不抛异常)。"""
    target = localization_records_path(peak_path)
    if not target.is_file():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    peaks = payload.get("peaks") if isinstance(payload, dict) else None
    if not isinstance(peaks, list):
        return []
    return [dict(row) for row in peaks if isinstance(row, dict)]


def summarize_localization(
    records: Sequence[dict[str, Any]],
    *,
    method: str,
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
) -> dict[str, Any]:
    """逐峰记录 → 运行记录摘要(用户需求 §13:方法 + ROI + 计数)。

    抛物线模式不做高斯拟合,三个 gaussian_*_count 一律 0(不虚报)。
    """
    total = len(records)
    if str(method) != "gaussian":
        return {
            "peak_localization_method": str(method),
            "n_peaks": int(total),
            "gaussian_fit_success_count": 0,
            "gaussian_fit_failure_count": 0,
            "gaussian_fallback_count": 0,
            "fallback_reasons": {},
        }
    success = sum(
        1
        for row in records
        if row.get("actual_method") == "gaussian" and row.get("gaussian_fit_success")
    )
    fallback = sum(1 for row in records if row.get("fallback"))
    failures: dict[str, int] = {}
    for row in records:
        if not row.get("fallback"):
            continue
        reason = str(row.get("fallback_reason", "") or "")
        failures[reason] = failures.get(reason, 0) + 1
    summary: dict[str, Any] = {
        "peak_localization_method": str(method),
        "n_peaks": total,
        "gaussian_fit_success_count": int(success),
        "gaussian_fit_failure_count": int(total - success),
        "gaussian_fallback_count": int(fallback),
        "fallback_reasons": failures,
    }
    if roi_f1_ppm is not None:
        summary["gaussian_roi_f1_ppm"] = float(roi_f1_ppm)
    if roi_f2_ppm is not None:
        summary["gaussian_roi_f2_ppm"] = float(roi_f2_ppm)
    return summary


__all__ = [
    "DEFAULT_GAUSSIAN_MAX_NFEV",
    "DEFAULT_GAUSSIAN_ROI_F1_PPM",
    "DEFAULT_GAUSSIAN_ROI_F2_PPM",
    "DEFAULT_GAUSSIAN_ROI_MAX_POINTS",
    "DEFAULT_GAUSSIAN_ROI_F2_PPM",
    "DEFAULT_LOCALIZATION_METHOD",
    "FWHM_FACTOR",
    "GAUSSIAN_SUPPORTED_NDIM",
    "GAUSSIAN_UNSUPPORTED_MESSAGE",
    "LOCALIZATION_LABELS",
    "LOCALIZATION_METHODS",
    "LOCALIZATION_RECORDS_SUFFIX",
    "LocalizationError",
    "PeakLocalization",
    "localization_label",
    "localization_records_path",
    "localization_supported",
    "load_localization_defaults",
    "localize_peak",
    "localize_peak_gaussian_2d",
    "localize_peak_parabolic",
    "normalize_localization_method",
    "ppm_from_point",
    "read_localization_records",
    "summarize_localization",
    "trim_localization_records",
    "write_localization_records",
]
