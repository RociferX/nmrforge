"""处理操作注册表：DAG 节点 operation -> 可执行函数。

节点 params（dict）经 _as_params 过滤后构造对应 dataclass，
避免计划中携带额外键导致构造失败。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.processing import apodization, baseline, ft, phase, sign_correction, transpose, zero_fill

OperationFn = Callable[[Any, dict[str, Any]], Any]


def _as_params(cls: type, params: dict[str, Any]) -> Any:
    known = {k: v for k, v in params.items() if k in cls.__dataclass_fields__}
    return cls(**known)


def _run_apodization(data: Any, params: dict[str, Any]) -> Any:
    return apodization.apply(data, _as_params(apodization.ApodizationParams, params))


def _run_zero_fill(data: Any, params: dict[str, Any]) -> Any:
    return zero_fill.apply(data, _as_params(zero_fill.ZeroFillParams, params))


def _run_ft(data: Any, params: dict[str, Any]) -> Any:
    return ft.apply(data, _as_params(ft.FtParams, params))


def _run_phase(data: Any, params: dict[str, Any]) -> Any:
    return phase.apply(data, _as_params(phase.PhaseParams, params))


def _run_baseline(data: Any, params: dict[str, Any]) -> Any:
    return baseline.apply(data, _as_params(baseline.BaselineParams, params))


def _run_transpose(data: Any, params: dict[str, Any]) -> Any:
    return transpose.apply(data, _as_params(transpose.TransposeParams, params))


def _run_sign_correction(data: Any, params: dict[str, Any]) -> Any:
    return sign_correction.apply(data, _as_params(sign_correction.SignCorrectionParams, params))


DEFAULT_OPERATIONS: dict[str, OperationFn] = {
    "apodization": _run_apodization,
    "zero_fill": _run_zero_fill,
    "ft": _run_ft,
    "phase": _run_phase,
    "baseline": _run_baseline,
    "transpose": _run_transpose,
    "sign_correction": _run_sign_correction,
}
