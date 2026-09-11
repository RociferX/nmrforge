"""NMRPipe bruker 工作流辅助：fid.com 参数解析与 acqus 交叉检查。

移植自 NMRFlow 的 processing/bruker_workflow.py，适配 NMRForge 内部数据模型。
用于 bruker -AUTO 生成 fid.com 后的核对/修补（acqus 为权威参数源）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.data.internal_data_model import Experiment, SamplingMode

_KEY_RE = re.compile(
    r"-(xN|yN|zN|xT|yT|zT|xSW|ySW|zSW|xOBS|yOBS|zOBS|xCAR|yCAR|zCAR|"
    r"xLAB|yLAB|zLAB|xMODE|yMODE|zMODE|decim|dspfvs|grpdly)\s+(\S+)"
)

_OUT_RE = re.compile(r"(-out\s+)(\S+)")


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


_NUSEXPAND_RE = re.compile(r"nusExpand\.tcl[^\n]*?-sampleCount\s+(\d+)")


def patch_nus_expand_count(text: str, nuslist_count: int) -> tuple[str, list[str]]:
    """把 nusExpand.tcl 的 -sampleCount 修正为实际 nuslist 行数。

    bruker -AUTO 对部分数据集（如 acqu2s TD 与 NusTD 矛盾）会误判采样点数，
    导致 ser_full 只展开少量切片、bruk2pipe 读数据失败卡死（2026-08-11 实测）。
    """
    warnings: list[str] = []

    def replace(match: re.Match) -> str:
        current = int(match.group(1))
        if current == nuslist_count:
            return match.group(0)
        warnings.append(
            f"sampleCount: fid.com={current} → nuslist={nuslist_count}（已修正）"
        )
        return match.group(0).replace(
            f"-sampleCount {current}", f"-sampleCount {nuslist_count}", 1
        )

    patched = _NUSEXPAND_RE.sub(replace, text)
    return patched, warnings


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
    if experiment.sampling.mode is SamplingMode.NUS:
        # NUS:xN/xT 是 nusExpand 按 serPadSize 补齐后的 ser 行大小(如
        # 908→1024),不是 acqus TD,不能覆盖(0.2.195);yN/yT/zN/zT 仍按
        # NusTD 网格修正,并与 nusExpand 强制一致
        values.pop("xN", None)
        values.pop("xT", None)
    return values


def patch_fid_com(
    text: str,
    experiment: Experiment,
) -> tuple[str, list[str]]:
    """把 fid.com 中与 acqus/acqu2s 不一致的参数修正为 acqus 值，
    并顺带把单文件输出名从 bruker 默认 test.fid 改为 {dataset_id}.fid
    （0.2.163-补13：自动/人工路径 fid 命名对齐，fid.com 输出即最终名）。

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
    # 0.2.199-补23:移除 fid.com 的 nusExpand -mask 阶段——SMILE 只读
    # nuslist,不需要 mask;直接不生成(而非转换后再删产物)。ser_full
    # 保留(bruk2pipe 输入)。
    patched, mask_removed = _MASK_STAGE_RE.subn("", patched)
    if mask_removed:
        warnings.append("已移除 fid.com 的 nusExpand -mask 阶段(SMILE 不需要 mask)")
    if experiment.sampling.mode is SamplingMode.NUS:
        patched, grid_warnings = _force_nus_expand_grid(patched, experiment)
        warnings += grid_warnings
        # 0.2.199-补27:不强制单文件——bruker 自动判断输出形态(TD=1 单
        # 文件、TD>1 切片),程序兼容两种输入(见 reconstruct_nus 切片回退)
    patched, out_warnings = patch_fid_out_name(patched, experiment.dataset_id)
    warnings += out_warnings
    return patched, warnings


# 0.2.199-补23:fid.com 的 mask 阶段(nusExpand.tcl -mask,含其 xyz2pipe 喂入
# 行)整块移除;支持单文件(-out ./mask.fid)与旧切片式(-out ./mask/test%03d.fid)。
# 注意:nusExpand 行尾的反斜杠续行后的缩进行也必须一并移除(不能用
# [^\n]* 贪婪吞掉行尾反斜杠,否则续行组匹配失败)。
_MASK_STAGE_RE = re.compile(
    r"\n(?:[ \t]*\|?[ \t]*xyz2pipe -in [^\n]*? -noWr[ \t]*\\\n)?"
    r"[ \t]*\|?[ \t]*nusExpand\.tcl -mask[^\n]*"
    r"(?:\n[ \t][^\n]*)*"
)

_NUS_EXPAND_RE = re.compile(r"(nusExpand\.tcl[^\n]*?)\\\n")


def _force_nus_expand_grid(
    text: str, experiment: Experiment
) -> tuple[str, list[str]]:
    """强制 nusExpand 与 bruk2pipe 使用同一 NusTD 网格(0.2.195)。

    nusExpand 缺省按 nuslist 推导网格(yTNUS/zTNUS),与按 NusTD 打补丁的
    bruk2pipe 不一致时(如 cc/63:83 vs 85)会把 fid 错位放置,重构错误;
    显式传入 -yT/-zT 后两段使用同一网格。只改第一条(展开)调用,mask
    调用不动。
    """
    td = _effective_td(experiment)
    grid: list[str] = []
    if len(td) > 1:
        grid.append(f"-yT {int(td[1] // 2)}")
    if len(td) > 2:
        grid.append(f"-zT {int(td[2] // 2)}")
    if not grid:
        return text, []
    warnings: list[str] = []

    def replace(match: re.Match) -> str:
        line = match.group(1)
        if "-yT" in line or "-zT" in line:
            return match.group(0)
        warnings.append(
            "nusExpand 网格: 强制 " + " ".join(grid) + "(与 bruk2pipe 一致)"
        )
        return line[:-1] + " " + " ".join(grid) + " \\\n"

    patched, _count = _NUS_EXPAND_RE.subn(replace, text, count=1)
    return patched, warnings


def patch_fid_out_name(text: str, dataset_id: str) -> tuple[str, list[str]]:
    """把 fid.com 的 -out 单文件输出名改写为 {dataset_id}.fid。

    bruker -AUTO 生成的 fid.com 固定输出 ./test.fid；改写后 fid.com
    直接产出最终名，自动/人工路径无需再在归位时改名。切片式输出
    （fid/test%03d.fid，3D uniform/NUS）保持 bruker 固定行为——
    切片名在自动/人工链路中本就一致（test%03d.fid），不在此改写。
    返回 (文本, 修正项列表)。
    """
    warnings: list[str] = []

    def replace(match: re.Match) -> str:
        current = match.group(2)
        if "%" in current:
            return match.group(0)  # 切片式：保持 bruker 固定命名
        name = Path(current).name
        if name != "test.fid":
            return match.group(0)
        prefix = current[: -len(name)]
        desired = f"{prefix}{dataset_id}.fid"
        warnings.append(f"out: {current} → {desired}（已修正）")
        return f"{match.group(1)}{desired}"

    patched = _OUT_RE.sub(replace, text)
    # 0.2.199:主输出 test.fid 改名后,mask 阶段的 `-in ./test.fid` 同步
    # 改名,否则单文件输出(如部分分段)会在 mask 阶段找不到输入而失败
    patched = re.sub(
        r"(-in\s+)(?:\./)?test\.fid\b",
        r"\g<1>" + f"{dataset_id}.fid",
        patched,
    )
    return patched, warnings


def apply_fid_com_overrides(
    text: str,
    overrides: dict[str, str],
) -> tuple[str, list[str]]:
    """把人工修改的 fid.com 参数覆盖应用到脚本（分段数据逐段使用）。

    人工途径只是给人调参：用户在参考段 fid.com 上改的参数
    （parse_fid_com 的键，如 ySW/-yCAR），逐段 fid.com 应用相同值，
    数据转换/切片/合并仍由后端统一保证。只替换已有参数、不新增。
    返回 (文本, 修正项列表)。
    """
    seen: set[str] = set()
    warnings: list[str] = []

    def replace(match: re.Match) -> str:
        key = match.group(1)
        seen.add(key)
        desired = overrides.get(key)
        if desired is None:
            return match.group(0)
        current = match.group(2)
        if current != desired:
            warnings.append(f"{key}: fid.com={current} → 人工={desired}（已应用）")
            return f"-{key} {desired}"
        return match.group(0)

    patched = _KEY_RE.sub(replace, text)
    for key in overrides:
        if key not in seen:
            warnings.append(f"{key}: fid.com 中未找到对应参数,已跳过")
    return patched, warnings
