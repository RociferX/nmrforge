"""NMRPipe bruker 工作流辅助：fid.com 参数解析与 acqus 交叉检查。

移植自 NMRFlow 的 processing/bruker_workflow.py，适配 NMRForge 内部数据模型。
用于 bruker -AUTO 生成 fid.com 后的核对/修补（acqus 为权威参数源）。
"""

from __future__ import annotations

import re
from typing import Any

from core.data.internal_data_model import Experiment, SamplingMode

_KEY_RE = re.compile(
    r"-(xN|yN|zN|xT|yT|zT|xSW|ySW|zSW|xOBS|yOBS|zOBS|xCAR|yCAR|zCAR|"
    r"xLAB|yLAB|zLAB|xMODE|yMODE|zMODE|decim|dspfvs|grpdly)\s+(\S+)"
)


def _fnmode(experiment: Experiment, logical_axis: str) -> int:
    if experiment.ndim >= 3:
        mapping = {"F3": "acqus", "F2": "acqu2s", "F1": "acqu3s"}
    else:
        mapping = {"F2": "acqus", "F1": "acqu2s"}
    block = experiment.acquisition_parameters.get(mapping.get(logical_axis, ""), {})
    try:
        return int(block.get("FnMODE", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _dim(experiment: Experiment, logical_axis: str):
    for dim in experiment.dimensions:
        if dim.logical_axis == logical_axis:
            return dim
    return None


def _effective_td(experiment: Experiment) -> list[int]:
    """转换/核对使用的每维点数：NUS 数据间接维取 NusTD（采样网格），否则取 TD。"""
    td = [dim.td for dim in experiment.dimensions]
    if experiment.sampling.mode is SamplingMode.NUS:
        for index, filename in ((1, "acqu2s"), (2, "acqu3s")):
            if len(td) <= index:
                continue
            block = experiment.acquisition_parameters.get(filename, {})
            try:
                nus_td = int(block.get("NusTD", 0) or 0)
            except (TypeError, ValueError):
                continue
            if nus_td:
                td[index] = nus_td
    return td


def parse_fid_com(text: str) -> dict[str, str]:
    """从 bruker 命令生成的 fid.com 提取 bruk2pipe 关键参数。"""
    return {match.group(1): match.group(2) for match in _KEY_RE.finditer(text)}


def _num(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def expected_values(experiment: Experiment) -> dict[str, tuple[Any, float | None]]:
    """fid.com 目标参数（值, 容差）：供 cross_check_fid_com 与 patch_fid_com 共用。

    字符串参数（LAB/MODE）容差为 None；N/T/decim/dspfvs 精确匹配；
    SW/OBS/CAR/grpdly 容差 1e-3。
    """
    td = _effective_td(experiment)
    x = _dim(experiment, "F2" if experiment.ndim == 2 else "F3")
    values: dict[str, tuple[Any, float | None]] = {
        "xN": (float(td[0]), 0.0),
        "xT": (float(td[0] // 2), 0.0),
        "xSW": (float(x.sw), 1e-3) if x else (None, 1e-3),
        "xOBS": (float(x.sf), 1e-3) if x else (None, 1e-3),
        "xCAR": (float(x.o1p), 1e-3) if x else (None, 1e-3),
        "xLAB": (x.nucleus, None) if x else ("", None),
        "xMODE": ("DQD", None),
    }
    if experiment.ndim >= 2 and len(td) > 1:
        y = _dim(experiment, "F1" if experiment.ndim == 2 else "F2")
        y_fnmode = _fnmode(experiment, y.logical_axis) if y else 0
        values.update(
            {
                "yN": (float(td[1]), 0.0),
                "yT": (float(td[1] // 2), 0.0),
                "ySW": (float(y.sw), 1e-3) if y else (None, 1e-3),
                "yOBS": (float(y.sf), 1e-3) if y else (None, 1e-3),
                "yCAR": (float(y.o1p), 1e-3) if y else (None, 1e-3),
                "yLAB": (y.nucleus, None) if y else ("", None),
                "yMODE": ("Echo-AntiEcho" if y_fnmode in (4, 6) else "Complex", None),
            }
        )
    if experiment.ndim >= 3 and len(td) > 2:
        z = _dim(experiment, "F1")
        values.update(
            {
                "zN": (float(td[2]), 0.0),
                "zT": (float(td[2] // 2), 0.0),
                "zSW": (float(z.sw), 1e-3) if z else (None, 1e-3),
                "zOBS": (float(z.sf), 1e-3) if z else (None, 1e-3),
                "zCAR": (float(z.o1p), 1e-3) if z else (None, 1e-3),
                "zLAB": (z.nucleus, None) if z else ("", None),
                "zMODE": ("Complex", None),
            }
        )
    acqus = experiment.acquisition_parameters.get("acqus", {})
    if acqus.get("DECIM"):
        values["decim"] = (float(acqus["DECIM"]), 0.0)
    if acqus.get("DSPFVS"):
        values["dspfvs"] = (float(acqus["DSPFVS"]), 0.0)
    if acqus.get("GRPDLY"):
        values["grpdly"] = (float(acqus["GRPDLY"]), 1e-3)
    return values


def cross_check_fid_com(
    fid_params: dict[str, str],
    experiment: Experiment,
) -> list[str]:
    """对照 acqus/acqu2s 元数据检查 fid.com 参数（含 MODE/DSP 标志），返回差异警告。"""
    warnings: list[str] = []
    for key, (desired, tolerance) in expected_values(experiment).items():
        if key not in fid_params:
            continue
        if isinstance(desired, str):
            if fid_params[key] != desired:
                warnings.append(f"{key}: fid.com={fid_params[key]} vs acqus={desired}")
            continue
        actual = _num(fid_params[key])
        if actual is None or desired is None:
            continue
        if abs(actual - desired) > (tolerance if tolerance is not None else 0.0):
            warnings.append(f"{key}: fid.com={fid_params[key]} vs acqus={desired:g}")
    return warnings


def _acqus_values(experiment: Experiment) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, (desired, _tolerance) in expected_values(experiment).items():
        if desired is None:
            continue
        if isinstance(desired, str):
            values[key] = desired
        elif key in ("xSW", "ySW", "zSW", "xOBS", "yOBS", "zOBS", "xCAR", "yCAR", "zCAR"):
            values[key] = f"{desired:.3f}"
        elif key in ("decim", "dspfvs", "grpdly"):
            values[key] = f"{desired:g}"
        else:
            values[key] = str(int(desired))
    return values


def patch_fid_com(
    text: str,
    experiment: Experiment,
) -> tuple[str, list[str]]:
    """把 fid.com 中与 acqus/acqu2s 不一致的参数修正为 acqus 值。

    返回 (修正后的文本, 修正项列表)。
    """
    target = _acqus_values(experiment)
    warnings: list[str] = []

    def replace(match: re.Match) -> str:
        key = match.group(1)
        current = match.group(2)
        desired = target.get(key)
        if desired is None or desired == "":
            return match.group(0)
        if current != desired:
            warnings.append(f"{key}: fid.com={current} → acqus={desired}（已修正）")
            return f"-{key} {desired}"
        return match.group(0)

    patched = _KEY_RE.sub(replace, text)
    return patched, warnings
