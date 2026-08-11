"""确定性 NMRPipe 脚本生成（转换 + 处理管道 + NUS SMILE 重构）。

同一输入（实验元数据 + 处理计划）生成字节级一致的 .com 脚本（LF 行尾），
保证可复现性。关键参数遵循 NMRFlow 审计结论（SOFTWARE_SUMMARY §6.2）：
- xMODE DQD / yMODE Echo-AntiEcho|Complex / zMODE Complex；
- DSPFVS=21 → -ws 8 -noi2f；禁用 -DMX；
- -aq2D 数值（FnMODE 4/6→3，5→2）；
- NUS 间接维 TD 用 NusTD（acqu3s TD=1 时 bruker 原生按 NusTD 识别，
  输出单文件 test.fid + mask.fid，SMILE 直接消费，无需切片追加）。
SMILE 重构参数可经 reconstruct_nus params 覆盖（nSigma/thresh/xQ3/scaling/report），
用于对照实验室脚本（data/脚本/smile2.com）调优。
"""

from __future__ import annotations

from typing import Any

from core.data.internal_data_model import Experiment, SamplingMode
from core.planning.processing_plan import ProcessingPlan

_AQ2D_KEYWORDS = {0: "2", 1: "1", 2: "2", 3: "2", 4: "3", 5: "2", 6: "3"}

# FnMODE -> (FT -neg, FT -alt)：States/QF 普通；TPPI/States-TPPI 需 -alt；
# Echo-Antiecho 由 bruk2pipe 转换完成，无需额外标志。
_FT_FLAGS = {
    0: (False, False),
    1: (True, True),
    2: (True, True),
    3: (False, False),
    4: (False, False),
    5: (False, True),
    6: (False, False),
}


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


def effective_td(experiment: Experiment) -> list[int]:
    """每维有效点数：NUS 时间接维取 NusTD（采样网格），否则取 TD。"""
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


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _next_pow2(value: int) -> int:
    return 1 << max(0, int(value) - 1).bit_length()


def select_smile_params(fraction: float) -> tuple[float, float]:
    """按采样率分档选择 SMILE 经验参数（nSigma, thresh）。

    2026-08-11 真实验证（61/63/65/67 合并 3.9%）：低采样用 nSigma=7/thresh=0.85
    得 QC 57.5（SNR 17）；nSigma=5/thresh=0.95 得 74.1（SNR 92）。
    """
    if fraction >= 0.5:
        return 5.0, 0.95
    if fraction >= 0.2:
        return 5.0, 0.95
    return 5.0, 0.95


def build_context(experiment: Experiment) -> dict[str, Any]:
    """脚本占位符上下文（元数据权威值 + NUS 有效 TD）。"""
    td = effective_td(experiment)
    dims = {dim.logical_axis: dim for dim in experiment.dimensions}
    x = dims.get("F2" if experiment.ndim == 2 else "F3") or dims.get("F2")
    y = dims.get("F1" if experiment.ndim == 2 else "F2")
    z = dims.get("F1") if experiment.ndim >= 3 else None
    acqus = experiment.acquisition_parameters.get("acqus", {})

    def _carrier(dim: Any) -> float:
        if dim is None:
            return 0.0
        if dim.o1p:
            return float(dim.o1p)
        return float(dim.o1) / float(dim.sf) if dim.sf else 0.0

    ctx: dict[str, Any] = {
        "meta.td.x": td[0] if td else 0,
        "meta.td.y": td[1] if len(td) > 1 else 0,
        "meta.td.z": td[2] if len(td) > 2 else 0,
        "meta.sw.x": float(x.sw) if x else 0.0,
        "meta.sw.y": float(y.sw) if y else 0.0,
        "meta.sw.z": float(z.sw) if z else 0.0,
        "meta.sfo.x": float(x.sf) if x else 0.0,
        "meta.sfo.y": float(y.sf) if y else 0.0,
        "meta.sfo.z": float(z.sf) if z else 0.0,
        "meta.carrier.x": _carrier(x),
        "meta.carrier.y": _carrier(y),
        "meta.carrier.z": _carrier(z),
        "meta.nucleus.x": x.nucleus if x else "",
        "meta.nucleus.y": y.nucleus if y else "",
        "meta.nucleus.z": z.nucleus if z else "",
        "meta.decim": int(acqus.get("DECIM", 0) or 0),
        "meta.dspfvs": int(acqus.get("DSPFVS", 0) or 0),
        "meta.grpdly": float(acqus.get("GRPDLY", 0.0) or 0.0),
        "meta.ndim": experiment.ndim,
    }
    return ctx


def _group_tokens(tokens: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("-") and i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
            groups.append([token, tokens[i + 1]])
            i += 2
        else:
            groups.append([token])
            i += 1
    return groups


def _bruk2pipe_tokens(experiment: Experiment, ctx: dict[str, Any]) -> list[str]:
    ndim = experiment.ndim
    y_fnmode = _fnmode(experiment, "F1" if ndim == 2 else "F2")
    y_mode = "Echo-AntiEcho" if y_fnmode in (4, 6) else "Complex"
    tokens = [
        "bruk2pipe",
        "-in",
        "./ser",
        "-bad",
        "0.0",
        "-aswap",
        "-AMX",
        "-decim",
        str(ctx["meta.decim"]),
        "-dspfvs",
        str(ctx["meta.dspfvs"]),
        "-grpdly",
        _fmt(ctx["meta.grpdly"]),
        "-ext",
        "-xMODE",
        "DQD",
        "-yMODE",
        y_mode,
    ]
    if int(ctx["meta.dspfvs"] or 0) == 21:
        tokens += ["-ws", "8", "-noi2f"]
    tokens += [
        "-xN",
        str(ctx["meta.td.x"]),
        "-yN",
        str(ctx["meta.td.y"]),
        "-xT",
        str(int(ctx["meta.td.x"]) // 2),
        "-yT",
        str(int(ctx["meta.td.y"]) // 2),
        "-xSW",
        _fmt(ctx["meta.sw.x"]),
        "-ySW",
        _fmt(ctx["meta.sw.y"]),
        "-xOBS",
        _fmt(ctx["meta.sfo.x"]),
        "-yOBS",
        _fmt(ctx["meta.sfo.y"]),
        "-xCAR",
        _fmt(ctx["meta.carrier.x"]),
        "-yCAR",
        _fmt(ctx["meta.carrier.y"]),
        "-xLAB",
        str(ctx["meta.nucleus.x"]),
        "-yLAB",
        str(ctx["meta.nucleus.y"]),
        "-ndim",
        str(ndim),
    ]
    if ndim >= 2:
        keyword = _AQ2D_KEYWORDS.get(y_fnmode, "")
        tokens += ["-aq2D", keyword] if keyword else ["-aq2D"]
    if ndim >= 3:
        tokens += [
            "-zMODE",
            "Complex",
            "-zN",
            str(ctx["meta.td.z"]),
            "-zT",
            str(int(ctx["meta.td.z"]) // 2),
            "-zSW",
            _fmt(ctx["meta.sw.z"]),
            "-zOBS",
            _fmt(ctx["meta.sfo.z"]),
            "-zCAR",
            _fmt(ctx["meta.carrier.z"]),
            "-zLAB",
            str(ctx["meta.nucleus.z"]),
        ]
    tokens += ["-out", "./test.fid"]
    return tokens


def generate_convert_script(
    experiment: Experiment,
    *,
    in_file: str = "./ser",
    out_file: str = "./test.fid",
) -> str:
    """生成 bruk2pipe 转换脚本（LF 行尾，csh 语法；仅均匀采样回退用）。"""
    ctx = build_context(experiment)
    tokens = _bruk2pipe_tokens(experiment, ctx)
    tokens[tokens.index("-in") + 1] = in_file
    tokens[tokens.index("-out") + 1] = out_file
    lines = [
        "#!/bin/csh",
        "# NMRForge conversion script (bruk2pipe)",
        f"# experiment: {experiment.dataset_id}",
    ]
    groups = _group_tokens(tokens[1:])
    lines.append("bruk2pipe \\")
    for index, group in enumerate(groups):
        suffix = " \\" if index < len(groups) - 1 else ""
        lines.append("  " + " ".join(group) + suffix)
    return "\n".join(lines) + "\n"


def _axis_stages(plan: ProcessingPlan, axis: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (plan.dag.nodes[node_id].operation, plan.dag.nodes[node_id].params)
        for node_id in plan.dag.execution_order()
        if node_id.endswith(f"_{axis}")
    ]


def _stage_lines(stages: list[tuple[str, dict[str, Any]]]) -> list[str]:
    lines: list[str] = []
    for op, params in stages:
        if op == "combine_hypercomplex":
            continue  # bruk2pipe 已按 MODE 完成超复数重建
        if op == "apodization":
            window = params.get("params", {})
            lines.append(
                f"| nmrPipe -fn SP -off {_fmt(window.get('off', 0.45))} "
                f"-end {_fmt(window.get('end', 0.95))} "
                f"-pow {_fmt(window.get('pow', 1))} -c {_fmt(window.get('c', 0.5))} \\"
            )
        elif op == "zero_fill":
            size = params.get("size", "auto")
            if size == "auto":
                lines.append("| nmrPipe -fn ZF -auto \\")
            else:
                lines.append(f"| nmrPipe -fn ZF -size {int(size)} \\")
        elif op == "ft":
            flags = []
            if params.get("alt"):
                flags.append("-alt")
            if params.get("neg"):
                flags.append("-neg")
            suffix = (" " + " ".join(flags)) if flags else ""
            lines.append(f"| nmrPipe -fn FT{suffix} \\")
        elif op == "phase":
            lines.append(
                f"| nmrPipe -fn PS -p0 {_fmt(params.get('p0', 0.0))} "
                f"-p1 {_fmt(params.get('p1', 0.0))} -di \\"
            )
        elif op == "baseline":
            lines.append("| nmrPipe -fn POLY -auto \\")
        else:
            raise ValueError(f"不支持映射为 nmrPipe 宏的操作: {op}")
    return lines


def generate_process_script(
    experiment: Experiment,
    plan: ProcessingPlan,
    *,
    in_file: str,
    out_file: str,
) -> str:
    """把处理计划（DAG）翻译为 NMRPipe 管道脚本（直接维 → TP → 间接维）。"""
    axes = [dim.logical_axis for dim in experiment.dimensions]
    lines = [
        "#!/bin/csh",
        "# NMRForge processing script",
        f"# experiment: {experiment.dataset_id}",
        f"xyz2pipe -in {in_file} -x \\",
    ]
    for index, axis in enumerate(axes):
        lines += _stage_lines(_axis_stages(plan, axis))
        if index < len(axes) - 1:
            lines.append("| nmrPipe -fn TP \\")
    lines.append(f"| pipe2xyz -out {out_file} -x")
    return "\n".join(lines) + "\n"


def _ft_flag_line(fnmode: int) -> str:
    neg, alt = _FT_FLAGS.get(int(fnmode), (False, False))
    flags = []
    if neg:
        flags.append("-neg")
    if alt:
        flags.append("-alt")
    suffix = (" " + " ".join(flags)) if flags else ""
    return f"| nmrPipe -fn FT{suffix} \\"


def generate_2d_nus_script(
    experiment: Experiment,
    *,
    in_file: str,
    nuslist: str,
    out_file: str,
    nthread: int = 2,
    nuslist_count: int = 0,
    ext_lo: str = "10.5",
    ext_hi: str = "6.5",
    nsigma: float = 5.0,
    thresh: float = 0.95,
    smile_xq1: float = 0.45,
    smile_xq2: float = 0.95,
    smile_xq3: float = 2.0,
    smile_scaling: bool = True,
    smile_report: int = 1,
) -> str:
    """2D NUS SMILE 重构：直接维 FT+EXT → SMILE -nDim 2 → 间接维 FT（终谱 ft2）。"""
    ctx = build_context(experiment)
    td = effective_td(experiment)
    direct_zf = _next_pow2((int(ctx["meta.td.x"]) // 2) * 2)
    f1_fnmode = _fnmode(experiment, "F1")
    lines = [
        "#!/bin/csh",
        "# NMRForge 2D NUS SMILE reconstruction",
        f"# experiment: {experiment.dataset_id}",
        "mkdir -p nus2d",
        "# step 1: direct dim (F2) FT + EXT",
        f"xyz2pipe -in {in_file} -x \\",
        "| nmrPipe -fn SP -off 0.45 -end 0.98 -pow 1 -c 0.5 \\",
        f"| nmrPipe -fn ZF -zf -size {direct_zf} \\",
        "| nmrPipe -fn FT \\",
        "| nmrPipe -fn PS -p0 0 -p1 0 -di \\",
        f"| nmrPipe -fn EXT -x1 {ext_lo}ppm -xn {ext_hi}ppm -sw -round 2 \\",
        "| pipe2xyz -out nus2d/test%03d.ft1 -z",
        "",
        "# step 2: SMILE reconstruct indirect dim (F1)",
        "xyz2pipe -in nus2d/test%03d.ft1 -x \\",
        "| nmrPipe -fn SMILE -nDim 2 \\",
        f"           -sample {nuslist} -nThread {nthread} \\",
        f"           -sampleCount {nuslist_count} -nSigma {nsigma:g} -off 0 0 "
        f"-report {smile_report} \\",
        *(["           -scaling 1 \\"] if smile_scaling else []),
        f"           -xApod SP -xQ1 {smile_xq1:g} -xQ2 {smile_xq2:g} -xQ3 {smile_xq3:g} \\",
        f"           -xT {max(1, int(td[1]) // 2)} -xP0 0 -xP1 0 \\",
        f"           -xCT 0 -thresh {thresh:g} \\",
        "| pipe2xyz -out nus2d/recon.ft1 -x",
        "",
        "# step 3: indirect dim FT",
        "xyz2pipe -in nus2d/recon.ft1 -x \\",
        "| nmrPipe -fn ZF -zf 1 -auto \\",
        _ft_flag_line(f1_fnmode),
        "| nmrPipe -fn PS -p0 0 -p1 0 -di \\",
        "| nmrPipe -fn TP \\",
        "| nmrPipe -fn ZTP \\",
        f"| pipe2xyz -out {out_file} -x",
    ]
    return "\n".join(lines) + "\n"


def generate_3d_nus_script(
    experiment: Experiment,
    *,
    in_file: str,
    nuslist: str,
    out_file: str,
    nthread: int = 2,
    nuslist_count: int = 0,
    ext_lo: str = "10.5",
    ext_hi: str = "6.5",
    nsigma: float = 5.0,
    thresh: float = 0.95,
    smile_xq1: float = 0.45,
    smile_xq2: float = 0.95,
    smile_xq3: float = 2.0,
    smile_scaling: bool = True,
    smile_report: int = 1,
) -> str:
    """3D NUS SMILE 重构：直接维（F3）FT+EXT → SMILE -nDim 3 → 间接维 FT（ft3）。"""
    ctx = build_context(experiment)
    direct_zf = _next_pow2((int(ctx["meta.td.x"]) // 2) * 2)
    f2_fnmode = _fnmode(experiment, "F2")
    f1_fnmode = _fnmode(experiment, "F1")
    lines = [
        "#!/bin/csh",
        "# NMRForge 3D NUS SMILE reconstruction",
        f"# experiment: {experiment.dataset_id}",
        "mkdir -p nus3d_1 nus3d_rc",
        "# step 1: direct dim (F3) FT + EXT",
        f"xyz2pipe -in {in_file} -x \\",
        "| nmrPipe -fn SP -off 0.45 -end 0.98 -pow 2 -c 0.5 \\",
        f"| nmrPipe -fn ZF -zf -size {direct_zf} \\",
        "| nmrPipe -fn FT \\",
        "| nmrPipe -fn PS -p0 0 -p1 0 -di \\",
        f"| nmrPipe -fn EXT -x1 {ext_lo}ppm -xn {ext_hi}ppm -sw -round 2 \\",
        "| pipe2xyz -out nus3d_1/test%04d.ft1 -z",
        "",
        "# step 2: SMILE reconstruct indirect dims (F2/F1)",
        "xyz2pipe -in nus3d_1/test%04d.ft1 -x \\",
        "| nmrPipe -fn SMILE -nDim 3 \\",
        f"           -sample {nuslist} -nThread {nthread} \\",
        f"           -sampleCount {nuslist_count} -nSigma {nsigma:g} -off 0 0 "
        f"-report {smile_report} \\",
        *(["           -scaling 1 \\"] if smile_scaling else []),
        f"           -xApod SP -xQ1 {smile_xq1:g} -xQ2 {smile_xq2:g} -xQ3 {smile_xq3:g} \\",
        f"           -yApod SP -yQ1 {smile_xq1:g} -yQ2 {smile_xq2:g} -yQ3 {smile_xq3:g} \\",
        "           -xP0 0 -xP1 0 -xNeg -xAlt \\",
        "           -yP0 0 -yP1 0 -yNeg -yAlt \\",
        f"           -xCT 0 -thresh {thresh:g} \\",
        "| pipe2xyz -out nus3d_rc/test%04d.ft1 -x",
        "",
        "# step 3: indirect dims (F2/F1) FT",
        "xyz2pipe -in nus3d_rc/test%04d.ft1 -x \\",
        "| nmrPipe -fn ZF -zf 1 -auto \\",
        _ft_flag_line(f2_fnmode),
        "| nmrPipe -fn PS -p0 0 -p1 0 -di \\",
        "| nmrPipe -fn TP \\",
        "| nmrPipe -fn ZF -zf 1 -auto \\",
        _ft_flag_line(f1_fnmode),
        "| nmrPipe -fn PS -p0 0 -p1 0 -di \\",
        "| nmrPipe -fn TP \\",
        "| nmrPipe -fn ZTP \\",
        f"| pipe2xyz -out {out_file} -x",
    ]
    return "\n".join(lines) + "\n"
