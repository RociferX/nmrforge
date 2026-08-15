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

import math
from typing import Any

from core.data.internal_data_model import Experiment, SamplingMode
from core.planning.method_selector import select_method
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


def _mult_for(fnmode: int) -> int:
    """超复数分量数:States/TPPI/States-TPPI/Echo-Antiecho 为 2,QF 为 1。"""
    return 2 if int(fnmode) in (0, 1, 2, 4, 5, 6) else 1



def effective_td(experiment: Experiment) -> list[int]:
    """每维有效点数:NUS 时间接维取复点网格,否则取 TD。

    2D NUS:间接维复点网格 = acqu2s TD // 超复数分量(如 TD=256/States→128),
    不直接采信 acqu2s NusTD(部分数据 NusTD 等于 TD,含超复数分量);
    3D NUS:acqu2s/acqu3s NusTD 已是复点数,直接采用。
    """
    td = [dim.td for dim in experiment.dimensions]
    if experiment.sampling.mode is SamplingMode.NUS:
        for index, filename in ((1, "acqu2s"), (2, "acqu3s")):
            if len(td) <= index:
                continue
            block = experiment.acquisition_parameters.get(filename, {})
            if index == 1 and experiment.ndim == 2:
                raw_td = int(td[index] or 0)
                mult = _mult_for(_fnmode(experiment, "F1"))
                if raw_td and mult:
                    td[index] = raw_td // mult
                continue
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


# ----------------------------------------------------------------- 填零规划
# 原则(2026-08-13,用户方案):填零不改变真实频率分辨率(取决于有效采集
# 时间 AQ=TD/SW),只减小频域数字点距(SW/SI)。直接维 F2/F3 默认
# SI=2×TD(稳妥经验起点,1024→2048);间接维按目标数字分辨率动态决定:
# 目标点距 = max(线宽, 1/AQ)/points_per_line(默认 1/2,即每个线宽至少 2 个
# 数字点;精确峰位/线宽/拟合/CSP 可调 1/4 或更细),所需 SI 向上取 2 的幂
# 并夹在 [TD, next_pow2(points_per_line×TD)]——SI 天然不超过
# points_per_line×TD 的 2 的幂上界。线宽来源:params.linewidth_hz[axis]
# → 核素默认表 → 15 Hz。NUS 间接维 TD 用重构后的完整复点网格
# (effective_td),填零只作用于重构后的时间域数据(与 SMILE 重构是两个
# 独立过程)。
DIRECT_ZF_FACTOR = 2
DEFAULT_POINTS_PER_LINE = 2.0
_DEFAULT_LINEWIDTH_HZ = {
    "1H": 8.0,
    "15N": 15.0,
    "13C": 20.0,
    "31P": 15.0,
    "19F": 20.0,
    "": 15.0,
}


def _axis_sw(experiment: Experiment, axis: str) -> float:
    for dim in experiment.dimensions:
        if dim.logical_axis == axis:
            return float(dim.sw or 0.0)
    return 0.0


def _default_linewidth(experiment: Experiment, axis: str) -> float:
    """核素默认估计线宽(Hz);实际线宽可用 params.linewidth_hz 覆盖。"""
    for dim in experiment.dimensions:
        if dim.logical_axis == axis:
            nucleus = str(dim.nucleus or "").strip()
            return _DEFAULT_LINEWIDTH_HZ.get(nucleus, _DEFAULT_LINEWIDTH_HZ[""])
    return _DEFAULT_LINEWIDTH_HZ[""]

def _config_linewidth_by_axis(experiment: Experiment) -> dict[str, float]:
    """从 config 读核素→线宽并映射为轴→线宽(缺省回退核素默认表)。"""
    from backend.config import DEFAULT_LINEWIDTH_HZ, load_processing_defaults

    cfg = load_processing_defaults()["linewidth_hz"]
    mapping: dict[str, float] = {}
    for dim in experiment.dimensions:
        nucleus = str(dim.nucleus or "").strip()
        mapping[dim.logical_axis] = float(
            cfg.get(
                nucleus, DEFAULT_LINEWIDTH_HZ.get(nucleus, DEFAULT_LINEWIDTH_HZ[""])
            )
        )
    return mapping


def _linewidth_for(
    axis: str, linewidth_hz: dict[str, float] | None
) -> float:
    if not linewidth_hz:
        return 0.0
    try:
        value = float(linewidth_hz.get(axis, 0.0))
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0.0 else 0.0


def _indirect_si(
    n: int, sw: float, linewidth: float, points_per_line: float
) -> tuple[int, str]:
    """间接维目标 SI:点距 ≤ max(线宽, 1/AQ)/points_per_line。

    物理约束:线宽不可能窄于真实分辨率下限 1/AQ,因此以
    max(linewidth, SW/TD) 为目标线宽;SI 夹在 [TD, next_pow2(ppl×TD)],
    即默认不超过 2×TD 的 2 的幂上界。
    """
    if n <= 0:
        return 1, "点数无效,SI=1"
    if sw <= 0 or linewidth <= 0:
        si = _next_pow2(2 * n)
        return si, f"SW/线宽缺失,回退 2×({n}→{si})"
    natural = sw / n  # 1/AQ:真实频率分辨率下限(Hz/pt)
    lw_eff = max(linewidth, natural)
    si_req = int(math.ceil(points_per_line * sw / lw_eff))
    si = _next_pow2(max(si_req, n))
    cap = _next_pow2(max(int(math.ceil(points_per_line * n)), n))
    if si > cap:
        si = cap
    current = sw / n
    after = sw / si
    return (
        si,
        f"目标点距 {lw_eff / points_per_line:.2f} Hz/pt"
        f"(线宽 {linewidth:g} Hz,≥1/AQ {natural:.2f}),"
        f"Δν {current:.2f}→{after:.2f} Hz/pt,SI={si}",
    )


def zero_fill_plan(
    experiment: Experiment,
    zero_fill: dict[str, Any] | int | None = None,
    *,
    linewidth_hz: dict[str, float] | None = None,
    points_per_line: float | None = None,
) -> dict[str, dict[str, Any]]:
    """逐维填零计划:{轴: {"mode", "size", "note"}}。

    zero_fill 覆盖(可空):
    - None 或 0 → 全部 auto(直接维 2×TD、间接维按数字分辨率动态);
    - int k≥1 → 直接维保持 2×TD,间接维固定 k×TD(旧 schema 语义);
    - {轴: {"mode": "none"|"auto", "size": N}} → 逐轴覆盖。
    auto 模式返回选定的 SI;mode=none 时 size=None。
    linewidth_hz/points_per_line 未显式传参时读取 config/nmrforge.yaml
    (processing.linewidth_hz/points_per_line,显式 params 优先)。
    """
    from backend.config import load_processing_defaults

    if linewidth_hz is None:
        linewidth_hz = _config_linewidth_by_axis(experiment)
    if points_per_line is None:
        points_per_line = float(load_processing_defaults()["points_per_line"])
    axes = [dim.logical_axis for dim in experiment.dimensions]
    td = effective_td(experiment)
    direct_axis = axes[0] if axes else ''
    plan: dict[str, dict[str, Any]] = {}

    override: dict[str, dict[str, Any]] = {}
    if isinstance(zero_fill, int) and zero_fill > 0:
        for index, axis in enumerate(axes):
            n = max(int(td[index]) if index < len(td) else 0, 1)
            if axis == direct_axis:
                override[axis] = {
                    "mode": "size",
                    "size": _next_pow2(DIRECT_ZF_FACTOR * n),
                }
            else:
                override[axis] = {
                    "mode": "size",
                    "size": _next_pow2(max(int(zero_fill) * n, n)),
                }
    elif isinstance(zero_fill, dict):
        for axis, cfg in zero_fill.items():
            if isinstance(cfg, dict):
                override[axis] = dict(cfg)
            else:
                override[axis] = {"mode": "size", "size": int(cfg)}

    for index, axis in enumerate(axes):
        n = max(int(td[index]) if index < len(td) else 0, 1)
        sw = _axis_sw(experiment, axis)
        cfg = override.get(axis) or {}
        mode = cfg.get("mode", "auto")
        if mode == "none":
            plan[axis] = {"mode": "none", "size": None, "note": "填零关闭"}
            continue
        if mode in ("auto", ""):
            if cfg.get("size") is not None:
                size = int(cfg["size"])
                plan[axis] = {"mode": "size", "size": size, "note": f"显式 SI={size}"}
                continue
            if axis == direct_axis:
                size = _next_pow2(DIRECT_ZF_FACTOR * n)
                note = f"直接维 2×TD({n}→{size})"
            else:
                lw = _linewidth_for(axis, linewidth_hz) or _default_linewidth(
                    experiment, axis
                )
                size, note = _indirect_si(n, sw, lw, points_per_line)
            plan[axis] = {"mode": "auto", "size": size, "note": note}
        else:
            size = int(cfg.get("size", mode))
            plan[axis] = {"mode": "size", "size": size, "note": f"显式 SI={size}"}
    return plan


def zero_fill_report(plan: dict[str, dict[str, Any]]) -> list[str]:
    """把逐维填零计划渲染为日志行(SI 选择依据对用户可见)。"""
    out: list[str] = []
    for axis in plan:
        cfg = plan[axis]
        if cfg.get("mode") == "none":
            out.append(f"填零 {axis}: 关闭")
        else:
            out.append(f"填零 {axis}: {cfg.get('note', '')}")
    return out


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


def _stage_lines(
    stages: list[tuple[str, dict[str, Any]]],
    direct_phase: dict[str, tuple[float, float]] | None = None,
    baseline: dict[str, dict[str, Any]] | None = None,
    window: dict[str, dict[str, Any]] | None = None,
    zero_fill: dict[str, dict[str, Any]] | None = None,
    sampling: dict[str, Any] | None = None,
) -> list[str]:
    lines: list[str] = []
    for op, params in stages:
        if op == "combine_hypercomplex":
            continue  # bruk2pipe 已按 MODE 完成超复数重建
        if op == "apodization":
            axis = str(params.get("axis", ""))
            cfg = dict(params.get("params", {}) or {})
            if window and axis in window:
                cfg.update(window[axis] or {})
            wtype = str(cfg.get("type", "sine_bell"))
            if wtype == "gaussian":
                lines.append(
                    f"| nmrPipe -fn GM -lb {_fmt(cfg.get('lb', 5.0))} "
                    f"-gb {_fmt(cfg.get('gb', 0.1))} \\"
                )
            elif wtype == "exp":
                lines.append(f"| nmrPipe -fn EM -lb {_fmt(cfg.get('lb', 5.0))} \\")
            else:
                powv = 2 if wtype == "sine_bell_squared" else cfg.get("pow", 1)
                lines.append(
                    f"| nmrPipe -fn SP -off {_fmt(cfg.get('off', 0.45))} "
                    f"-end {_fmt(cfg.get('end', 0.95))} "
                    f"-pow {_fmt(powv)} -c {_fmt(cfg.get('c', 0.5))} \\"
                )
        elif op == "zero_fill":
            axis = str(params.get("axis", ""))
            zf = (zero_fill or {}).get(axis, {}) or {}
            mode = zf.get("mode", "auto")
            if mode == "none":
                continue
            size = zf.get("size")
            if size is None and mode not in ("auto", ""):
                size = mode
            if size is None:
                lines.append("| nmrPipe -fn ZF -auto \\")
            else:
                lines.append(f"| nmrPipe -fn ZF -size {int(size)} \\")
        elif op == "ft":
            axis = str(params.get("axis", ""))
            # 采样覆盖与标志装配统一走 _ft_flags(与 NUS/finalize 同源)
            flags = _ft_flags(
                bool(params.get("neg")),
                bool(params.get("alt")),
                sampling=sampling,
                axis=axis,
            )
            suffix = (" " + " ".join(flags)) if flags else ""
            lines.append(f"| nmrPipe -fn FT{suffix} \\")
        elif op == "phase":
            p0 = params.get("p0", 0.0)
            p1 = params.get("p1", 0.0)
            axis = params.get("axis", "")
            if direct_phase and axis in direct_phase:
                p0, p1 = direct_phase[axis]
            lines.append(
                f"| nmrPipe -fn PS -p0 {_fmt(p0)} -p1 {_fmt(p1)} -di \\"
            )
        elif op == "baseline":
            cfg = dict(params)
            axis = str(params.get("axis", ""))
            if baseline and axis in baseline:
                cfg.update(baseline[axis])
            if not _as_bool(cfg.get("enabled", True)):
                continue
            if str(cfg.get("mode", "auto")) == "order":
                order = max(1, int(cfg.get("order", 1) or 1))
                lines.append(f"| nmrPipe -fn POLY -ord {order} \\")
            else:
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
    direct_phase: dict[str, tuple[float, float]] | None = None,
    baseline: dict[str, dict[str, Any]] | None = None,
    window: dict[str, dict[str, Any]] | None = None,
    zero_fill: dict[str, Any] | int | None = None,
    linewidth_hz: dict[str, float] | None = None,
    points_per_line: float = DEFAULT_POINTS_PER_LINE,
    ext_lo: str = "11.0",
    ext_hi: str = "6.0",
    extract: bool = True,
    sampling: dict[str, Any] | None = None,
) -> str:
    """把处理计划（DAG）翻译为 NMRPipe 管道脚本（直接维 → EXT → TP → 间接维）。

    EXT 沿直接维(1H)提取窗口,默认 6-11 ppm(ext_lo=11, ext_hi=6),
    与 NUS 脚本一致;extract=False 可关闭。
    """
    axes = [dim.logical_axis for dim in experiment.dimensions]
    zf_plan = zero_fill_plan(
        experiment,
        zero_fill,
        linewidth_hz=linewidth_hz,
        points_per_line=points_per_line,
    )
    lines = [
        "#!/bin/csh",
        "# NMRForge processing script",
        f"# experiment: {experiment.dataset_id}",
        f"xyz2pipe -in {in_file} -x \\",
    ]
    for index, axis in enumerate(axes):
        lines += _stage_lines(
            _axis_stages(plan, axis),
            direct_phase,
            baseline,
            window,
            zf_plan,
            sampling,
        )
        if extract and index == 0:
            lines.append(
                f"| nmrPipe -fn EXT -x1 {ext_lo}ppm -xn {ext_hi}ppm -sw -round 2 \\"
            )
        if index < len(axes) - 1:
            lines.append("| nmrPipe -fn TP \\")
    if len(axes) == 2:
        # NMRPipe 2D:间接维 FT 后需再 TP 转置回来,否则输出 F1/F2 交换
        lines.append("| nmrPipe -fn TP \\")
    lines.append(f"| pipe2xyz -out {out_file} -x")
    return "\n".join(lines) + "\n"


def _nus_zf_size(cfg: dict[str, Any], td_points: int) -> int:
    """NUS 维度填零尺寸:显式 size 优先,否则 next_pow2(2×TD)。"""
    return int(cfg.get("size") or _next_pow2(2 * max(int(td_points), 1)))


def _ft_flags(
    base_neg: bool,
    base_alt: bool,
    *,
    sampling: dict[str, Any] | None = None,
    axis: str = "",
) -> list[str]:
    """FT 标志列表(sampling 覆盖逻辑唯一实现)。

    base_neg/base_alt 由调用方给定(均匀路径来自 plan 节点;NUS/finalize
    来自 FnMODE 推导);sampling.ft_neg/ft_alt 非 None 时覆盖,
    flip_f1=True 时 F1 轴强制 -neg(翻转);默认保持推导输出不变。
    """
    neg, alt = bool(base_neg), bool(base_alt)
    if sampling:
        if sampling.get("ft_neg") is not None:
            neg = bool(sampling.get("ft_neg"))
        if sampling.get("ft_alt") is False:
            alt = False  # True=按采集方式自动;False=强制关闭
        if axis == "F1" and bool(sampling.get("flip_f1")):
            neg = True
    flags = []
    if neg:
        flags.append("-neg")
    if alt:
        flags.append("-alt")
    return flags


def _ft_flag_line(
    fnmode: int,
    *,
    sampling: dict[str, Any] | None = None,
    axis: str = "",
) -> str:
    """FT 行标志:sampling.ft_neg/ft_alt 非 None 时覆盖 FnMODE 推导,
    flip_f1=True 时 F1 轴强制 -neg(翻转);默认保持推导输出不变。"""
    neg, alt = _FT_FLAGS.get(int(fnmode), (False, False))
    flags = _ft_flags(neg, alt, sampling=sampling, axis=axis)
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
    ext_lo: str = "11.0",
    ext_hi: str = "6.0",
    nsigma: float = 5.0,
    thresh: float = 0.95,
    smile_xq1: float = 0.45,
    smile_xq2: float = 0.95,
    smile_xq3: float = 2.0,
    smile_scaling: bool = True,
    smile_report: int = 1,
    direct_phase: tuple[float, float] = (0.0, 0.0),
    extract: bool = True,
    baseline: dict[str, Any] | None = None,
    zero_fill: dict[str, Any] | int | None = None,
    linewidth_hz: dict[str, float] | None = None,
    points_per_line: float = DEFAULT_POINTS_PER_LINE,
    sampling: dict[str, Any] | None = None,
) -> str:
    """2D NUS SMILE 重构(两阶段,Architect VM 验证 sampleA 25% NUS)。

    stage 1:直接维(F2)FT+EXT+POLY → TP → SMILE(-sample None,
    -xT 复点网格)→ nus2d/recon.ft1;stage 2:间接维(F1)
    ZF/FT -alt/PS/POLY/TP → 终谱 ft2(-out -ov)。
    单文件用 nmrPipe -in(2D 单文件只有 1 平面,不能用 xyz2pipe);
    分段多文件(test%03d.fid)回退 xyz2pipe + -sample nuslist。
    """
    td = effective_td(experiment)
    zf_plan = zero_fill_plan(
        experiment,
        zero_fill,
        linewidth_hz=linewidth_hz,
        points_per_line=points_per_line,
    )
    f2_zf = zf_plan.get("F2", {})
    f1_zf = zf_plan.get("F1", {})
    direct_zf = _nus_zf_size(f2_zf, td[0])
    f1_fnmode = _fnmode(experiment, "F1")
    x_t = max(1, int(td[1])) if len(td) > 1 else 1  # 间接维复点网格
    multi = "%" in in_file
    expanded = expand_baseline(experiment, baseline)
    direct_poly = _baseline_line(expanded, "F2")
    indirect_poly = _baseline_line(expanded, "F1")
    direct_stages = [
        "| nmrPipe -fn SP -off 0.45 -end 0.98 -pow 1 -c 0.5 \\",
    ]
    if f2_zf.get("mode") != "none":
        direct_stages.append(f"| nmrPipe -fn ZF -zf -size {direct_zf} \\")
    direct_stages += [
        "| nmrPipe -fn FT \\",
        f"| nmrPipe -fn PS -p0 {direct_phase[0]:g} -p1 {direct_phase[1]:g} -di \\",
    ]
    if extract:
        direct_stages.append(
            f"| nmrPipe -fn EXT -x1 {ext_lo}ppm -xn {ext_hi}ppm -sw -round 2 \\"
        )
    direct_stages += direct_poly
    smile_tail = [
        f"           -xApod SP -xQ1 {smile_xq1:g} -xQ2 {smile_xq2:g} "
        f"-xQ3 {smile_xq3:g} \\",
        f"           -xT {x_t} -xP0 0 -xP1 0 \\",
        f"           -xCT 0 -thresh {thresh:g} \\",
        "| pipe2xyz -out nus2d/recon.ft1 -x -ov",
    ]
    if multi:
        lines = [
            "#!/bin/csh",
            "# NMRForge 2D NUS SMILE reconstruction (two-stage, multi-file)",
            f"# experiment: {experiment.dataset_id}",
            "mkdir -p nus2d",
            "# stage 1: direct dim (F2) FT + EXT + POLY",
            f"xyz2pipe -in {in_file} -x \\",
            *direct_stages,
            "| pipe2xyz -out nus2d/test%03d.ft1 -z",
            "",
            "# SMILE reconstruct indirect dim (F1)",
            "xyz2pipe -in nus2d/test%03d.ft1 -x \\",
            "| nusPipe -fn SMILE -nDim 2 \\",
            f"           -sample {nuslist} -nThread {nthread} \\",
            f"           -sampleCount {nuslist_count} -nSigma {nsigma:g} "
            f"-off 0 0 -report {smile_report} \\",
            *(["           -scaling 1 \\"] if smile_scaling else []),
            *smile_tail,
        ]
    else:
        lines = [
            "#!/bin/csh",
            "# NMRForge 2D NUS SMILE reconstruction (two-stage)",
            f"# experiment: {experiment.dataset_id}",
            "mkdir -p nus2d",
            "# stage 1: direct dim (F2) FT + EXT + POLY, SMILE reconstruct F1",
            f"nmrPipe -in {in_file} \\",
            *direct_stages,
            "| nmrPipe -fn TP \\",
            "| nusPipe -fn SMILE -nDim 2 \\",
            f"           -sample None -nThread {nthread} \\",
            f"           -sampleCount {nuslist_count} -nSigma {nsigma:g} "
            f"-off 0 0 -report {smile_report} \\",
            *(["           -scaling 1 \\"] if smile_scaling else []),
            *smile_tail,
        ]
    f1_zf_line: list[str] = []
    if f1_zf.get("mode") != "none":
        f1_zf_line = [
            f"| nmrPipe -fn ZF -size {f1_zf.get('size') or _next_pow2(2 * max(int(td[1]), 1))} \\"
        ]
    lines += [
        "",
        "# stage 2: indirect dim (F1) FT -alt + PS + POLY",
        "nmrPipe -in nus2d/recon.ft1 \\",
        *f1_zf_line,
        _ft_flag_line(f1_fnmode, sampling=sampling, axis="F1"),
        "| nmrPipe -fn PS -p0 0 -p1 0 -di \\",
        *indirect_poly,
        "| nmrPipe -fn TP \\",
        f"  -out {out_file} -ov",
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
    ext_lo: str = "11.0",
    ext_hi: str = "6.0",
    nsigma: float = 5.0,
    thresh: float = 0.95,
    smile_xq1: float = 0.45,
    smile_xq2: float = 0.95,
    smile_xq3: float = 2.0,
    smile_scaling: bool = True,
    smile_report: int = 1,
    direct_phase: tuple[float, float] = (0.0, 0.0),
    extract: bool = True,
    baseline: dict[str, Any] | None = None,
    zero_fill: dict[str, Any] | int | None = None,
    linewidth_hz: dict[str, float] | None = None,
    points_per_line: float = DEFAULT_POINTS_PER_LINE,
    sampling: dict[str, Any] | None = None,
) -> str:
    """3D NUS SMILE 重构：直接维（F3）FT+EXT → SMILE -nDim 3 → 间接维 FT（ft3）。"""
    ctx = build_context(experiment)
    zf_plan = zero_fill_plan(
        experiment,
        zero_fill,
        linewidth_hz=linewidth_hz,
        points_per_line=points_per_line,
    )
    f3_zf = zf_plan.get("F3", {})
    f2_zf = zf_plan.get("F2", {})
    f1_zf = zf_plan.get("F1", {})
    direct_zf = _nus_zf_size(f3_zf, ctx["meta.td.x"])
    f2_zf_size = _nus_zf_size(f2_zf, ctx["meta.td.y"])
    f1_zf_size = _nus_zf_size(f1_zf, ctx["meta.td.z"])
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
        *(
            [f"| nmrPipe -fn ZF -zf -size {direct_zf} \\"]
            if f3_zf.get("mode") != "none"
            else []
        ),
        "| nmrPipe -fn FT \\",
        f"| nmrPipe -fn PS -p0 {direct_phase[0]:g} -p1 {direct_phase[1]:g} -di \\",
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
        *(
            [
                f"| nmrPipe -fn ZF -size {f2_zf_size} \\"
            ]
            if f2_zf.get("mode") != "none"
            else []
        ),
        _ft_flag_line(f2_fnmode, sampling=sampling, axis="F2"),
        "| nmrPipe -fn PS -p0 0 -p1 0 -di \\",
        "| nmrPipe -fn TP \\",
        *(
            [
                f"| nmrPipe -fn ZF -size {f1_zf_size} \\"
            ]
            if f1_zf.get("mode") != "none"
            else []
        ),
        _ft_flag_line(f1_fnmode, sampling=sampling, axis="F1"),
        "| nmrPipe -fn PS -p0 0 -p1 0 -di \\",
        "| nmrPipe -fn TP \\",
        "| nmrPipe -fn ZTP \\",
        f"| pipe2xyz -out {out_file} -x",
    ]
    if not extract:
        lines = [line for line in lines if "| nmrPipe -fn EXT" not in line]
    expanded = expand_baseline(experiment, baseline)
    lines = _insert_nus_baseline(lines, expanded, experiment.ndim)
    return "\n".join(lines) + "\n"



def param_schema() -> dict[str, Any]:
    """处理计划参数 JSON schema(API_CONTRACT §6 键 + 默认值/说明)。

    对齐 presets/config:zero_fill、sampling(ft_neg/ft_alt/flip_f1/auto_phase)、
    stages(id/tool/macro/params/param_docs);供 GUI 参数表格编辑器与渲染使用。
    """
    return {
        "type": "object",
        "title": "NMRForge 处理参数",
        "description": "处理计划参数(表格编辑器/脚本渲染共享数据源)",
        "properties": {
            "zero_fill": {
                "type": "integer",
                "default": 2,
                "description": (
                    "填零:0=自动(直接维 2×TD、间接维按目标数字分辨率动态,"
                    "受 1/AQ 约束且不超过 points_per_line×TD);k≥1=间接维"
                    "固定 k×TD(直接维保持 2×TD)。填零不改变真实频率分辨率"
                    "(由 AQ 决定),只减小数字点距。"
                ),
            },
            "linewidth_hz": {
                "type": "object",
                "description": (
                    "逐轴估计线宽(Hz),间接维自动填零的目标线宽;缺省按核素"
                    "默认(1H 8/15N 15/13C 20 Hz 等),并受 1/AQ 下限约束。"
                    "例:{\"F1\": 15.0}"
                ),
                "additionalProperties": {"type": "number"},
            },
            "points_per_line": {
                "type": "number",
                "default": 2.0,
                "description": (
                    "间接维目标数字点距 = max(线宽,1/AQ)/points_per_line"
                    "(默认 1/2,即每个线宽至少 2 个数字点;精确峰位/线宽/拟合/"
                    "CSP 可加大,2D 省内存可减小)"
                ),
            },
            "sampling": {
                "type": "object",
                "description": "采样/采集相关标志",
                "properties": {
                    "ft_neg": {
                        "type": ["boolean", "null"],
                        "default": None,
                        "description": "FT 后翻转该轴(null=按采集方式自动,True/False=强制)",
                    },
                    "ft_alt": {
                        "type": "boolean",
                        "default": True,
                        "description": (
                            "TPPI/States-TPPI ± 交替修正(True=按采集方式自动,"
                            "False=强制关闭)"
                        ),
                    },
                    "flip_f1": {
                        "type": "boolean",
                        "default": False,
                        "description": "F1 轴翻转(FT -neg)",
                    },
                    "auto_phase": {
                        "type": "boolean",
                        "default": True,
                        "description": "直接维 p1 共识自动相位",
                    },
                },
            },
            "ext_lo": {
                "type": "string",
                "default": "11.0",
                "description": "直接维 1H 提取窗口高 ppm(EXT -x1)",
            },
            "ext_hi": {
                "type": "string",
                "default": "6.0",
                "description": "直接维 1H 提取窗口低 ppm(EXT -xn)",
            },
            "extract": {
                "type": "boolean",
                "default": True,
                "description": "直接维提取窗口是否开启",
            },
            "baseline": {
                "type": "object",
                "description": "逐维基线校正(POLY,契约 §6)",
                "properties": {
                    "enabled": {"type": "boolean", "default": True},
                    "mode": {"enum": ["auto", "order"], "default": "auto"},
                    "order": {"type": "integer", "default": 0},
                    "axes": {
                        "oneOf": [
                            {"type": "string", "enum": ["all"]},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "default": "all",
                    },
                },
            },
            "stages": {
                "type": "array",
                "description": "处理阶段列表(表格编辑器逐行展示)",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "阶段唯一 id"},
                        "tool": {"type": "string", "description": "后端工具(nmrpipe/native)"},
                        "macro": {"type": "string", "description": "NMRPipe 宏(SP/ZF/FT/PS)"},
                        "params": {"type": "object", "description": "宏参数"},
                        "param_docs": {"type": "object", "description": "参数说明"},
                    },
                    "required": ["id", "tool", "macro"],
                },
            },
        },
        "default": {
            "zero_fill": 2,
            "linewidth_hz": {},
            "points_per_line": 2.0,
            "ext_lo": "11.0",
            "ext_hi": "6.0",
            "extract": True,
            "baseline": {
                "enabled": True,
                "mode": "auto",
                "order": 0,
                "axes": "all",
            },
            "sampling": {
                "ft_neg": None,
                "ft_alt": True,
                "flip_f1": False,
                "auto_phase": True,
            },
            "stages": [],
        },
    }


def _as_bool(value: Any, default: bool = True) -> bool:
    """宽松布尔转换(GUI 可能传字符串 "false"/"0")。"""
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "")
    return bool(value)


def render_scripts(
    experiment: Experiment,
    params: dict[str, Any] | None = None,
) -> dict[str, str]:
    """确定性渲染 fid.com/process.com/nus*.com(供 GUI 展示与保存执行)。

    同一输入(实验元数据 + 参数)生成字节级一致的脚本;NUS 额外渲染 nus.com,
    uniform 渲染 process.com;fid.com 为 bruk2pipe 确定性转换脚本。
    """
    params = dict(params or {})
    plan = select_method(experiment)
    out_ext = "ft3" if experiment.ndim >= 3 else "ft2"
    direct_phase = params.get("direct_phase")
    scripts: dict[str, str] = {"fid.com": generate_convert_script(experiment)}

    if experiment.sampling.mode is SamplingMode.NUS:
        nus = dict(params.get("nus", {}) or {})
        direct = (0.0, 0.0)
        if direct_phase:
            direct = tuple(direct_phase.get("F2", (0.0, 0.0)))
        kwargs: dict[str, Any] = {
            "in_file": f"{experiment.dataset_id}.fid",
            "baseline": expand_baseline(experiment, params.get("baseline")),
            "nuslist": "nuslist",
            "out_file": f"{experiment.dataset_id}.{out_ext}",
            "nthread": int(nus.get("nthread", 2)),
            "nuslist_count": int(nus.get("nuslist_count", 0)),
            "nsigma": float(nus.get("nsigma", 5.0)),
            "thresh": float(nus.get("thresh", 0.95)),
            "smile_xq3": float(nus.get("smile_xq3", 2.0)),
            "smile_scaling": _as_bool(nus.get("smile_scaling", True)),
            "smile_report": int(nus.get("smile_report", 1)),
            "direct_phase": direct,
            "zero_fill": params.get("zero_fill"),
            "linewidth_hz": params.get("linewidth_hz"),
            "points_per_line": float(params.get("points_per_line", 4.0)),
        }
        if experiment.ndim >= 3:
            scripts["nus.com"] = generate_3d_nus_script(experiment, **kwargs)
        else:
            scripts["nus.com"] = generate_2d_nus_script(experiment, **kwargs)
    else:
        dp = None
        if direct_phase:
            direct_axis = "F2" if experiment.ndim == 2 else "F3"
            dp = {
                direct_axis: tuple(direct_phase.get(direct_axis, (0.0, 0.0)))
            }
        scripts["process.com"] = generate_process_script(
            experiment,
            plan,
            in_file=f"{experiment.dataset_id}.fid",
            out_file=f"{experiment.dataset_id}.{out_ext}",
            direct_phase=dp,
            baseline=expand_baseline(experiment, params.get("baseline")),
            zero_fill=params.get("zero_fill"),
            linewidth_hz=params.get("linewidth_hz"),
            points_per_line=float(params.get("points_per_line", 4.0)),
            ext_lo=str(params.get("ext_lo", "11.0")),
            ext_hi=str(params.get("ext_hi", "6.0")),
            extract=_as_bool(params.get("extract", True)),
        )
    return scripts



def generate_nus_finalize_script(
    experiment: Experiment,
    *,
    planes: str,
    out_file: str,
    phases: dict[str, tuple[float, float]] | None = None,
    baseline: dict[str, Any] | None = None,
    zero_fill: dict[str, Any] | int | None = None,
    linewidth_hz: dict[str, float] | None = None,
    points_per_line: float = DEFAULT_POINTS_PER_LINE,
    sampling: dict[str, Any] | None = None,
) -> str:
    """NUS 重构平面(复型)的间接维 FT 定稿脚本(逐维 PS 可配)。

    planes:重构平面输入(2D nus2d/recon.ft1;3D nus3d_rc/test%04d.ft1);
    phases:{轴 -> (p0, p1)},缺省 0——供逐维相位候选运行,不重跑 SMILE;
    2D 单文件用 nmrPipe -in + -out -ov(与验证 s2.com 一致),F1 POLY 可配。
    """
    phases = phases or {}
    td = effective_td(experiment)
    zf_plan = zero_fill_plan(
        experiment,
        zero_fill,
        linewidth_hz=linewidth_hz,
        points_per_line=points_per_line,
    )
    f1_fnmode = _fnmode(experiment, "F1")
    if experiment.ndim >= 3:
        f2_fnmode = _fnmode(experiment, "F2")
        f2_p0, f2_p1 = phases.get("F2", (0.0, 0.0))
        f1_p0, f1_p1 = phases.get("F1", (0.0, 0.0))
        f2_size = _nus_zf_size(zf_plan.get("F2", {}), td[1])
        f1_size = _nus_zf_size(zf_plan.get("F1", {}), td[2])
        lines = [
            "#!/bin/csh",
            "# NMRForge NUS finalize script (indirect FT from reconstructed planes)",
            f"# experiment: {experiment.dataset_id}",
            f"xyz2pipe -in {planes} -x \\",
            *(
                [
                    f"| nmrPipe -fn ZF -size {f2_size} \\"
                ]
                if zf_plan.get("F2", {}).get("mode") != "none"
                else []
            ),
            _ft_flag_line(f2_fnmode, sampling=sampling, axis="F2"),
            f"| nmrPipe -fn PS -p0 {f2_p0:g} -p1 {f2_p1:g} -di \\",
            "| nmrPipe -fn TP \\",
            *(
                [
                    f"| nmrPipe -fn ZF -size {f1_size} \\"
                ]
                if zf_plan.get("F1", {}).get("mode") != "none"
                else []
            ),
            _ft_flag_line(f1_fnmode, sampling=sampling, axis="F1"),
            f"| nmrPipe -fn PS -p0 {f1_p0:g} -p1 {f1_p1:g} -di \\",
            "| nmrPipe -fn TP \\",
            "| nmrPipe -fn ZTP \\",
            f"| pipe2xyz -out {out_file} -x",
        ]
    else:
        f1_p0, f1_p1 = phases.get("F1", (0.0, 0.0))
        f1_size = _nus_zf_size(zf_plan.get("F1", {}), td[1])
        expanded = expand_baseline(experiment, baseline)
        lines = [
            "#!/bin/csh",
            "# NMRForge NUS finalize script (indirect FT from reconstructed planes)",
            f"# experiment: {experiment.dataset_id}",
            f"nmrPipe -in {planes} \\",
            *(
                [
                    f"| nmrPipe -fn ZF -size {f1_size} \\"
                ]
                if zf_plan.get("F1", {}).get("mode") != "none"
                else []
            ),
            _ft_flag_line(f1_fnmode, sampling=sampling, axis="F1"),
            f"| nmrPipe -fn PS -p0 {f1_p0:g} -p1 {f1_p1:g} -di \\",
            *_baseline_line(expanded, "F1"),
            "| nmrPipe -fn TP \\",
            f"  -out {out_file} -ov",
        ]
    return "\n".join(lines) + "\n"


def expand_baseline(
    experiment: Experiment,
    baseline: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """归一 baseline 配置为 {轴: {enabled, mode, order}}。

    支持 None(全维默认 auto)、表单形态 {enabled,mode,order,axes} 与
    逐轴形态 {轴: {...}}。
    """
    axes = [dim.logical_axis for dim in experiment.dimensions]
    defaults: dict[str, Any] = {"enabled": True, "mode": "auto", "order": 0}
    if not baseline:
        return {axis: dict(defaults) for axis in axes}
    if any(k in baseline for k in ("axes", "enabled", "mode", "order")):
        enabled = _as_bool(baseline.get("enabled", True))
        mode = str(baseline.get("mode", "auto"))
        order = int(baseline.get("order", 0) or 0)
        sel = baseline.get("axes") or "all"
        target = axes if sel == "all" else [str(a) for a in sel]
        return {
            axis: {"enabled": enabled, "mode": mode, "order": order}
            for axis in target
        }
    out: dict[str, dict[str, Any]] = {}
    for axis in axes:
        cfg = dict(defaults)
        cfg.update(baseline.get(axis, {}))
        cfg["enabled"] = _as_bool(cfg.get("enabled", True))
        cfg["mode"] = str(cfg.get("mode", "auto"))
        cfg["order"] = int(cfg.get("order", 0) or 0)
        out[axis] = cfg
    return out


def _baseline_line(
    expanded: dict[str, dict[str, Any]], axis: str
) -> list[str]:
    """按轴配置生成 POLY 行(空列表=关闭)。"""
    cfg = expanded.get(axis) or {}
    if not cfg.get("enabled", True):
        return []
    if str(cfg.get("mode", "auto")) == "order":
        order = max(1, int(cfg.get("order", 1) or 1))
        return [f"| nmrPipe -fn POLY -ord {order} \\"]
    return ["| nmrPipe -fn POLY -auto \\"]


def _insert_nus_baseline(
    lines: list[str],
    expanded: dict[str, dict[str, Any]],
    ndim: int,
) -> list[str]:
    """在 NUS 脚本指定位置插入 POLY:直接维 EXT 后、间接维各 PS 后。"""
    direct_anchor = (
        "| pipe2xyz -out nus3d_1/test%04d.ft1 -z"
        if ndim >= 3
        else "| pipe2xyz -out nus2d/test%03d.ft1 -z"
    )
    recon_mark = (
        "xyz2pipe -in nus3d_rc/test%04d.ft1"
        if ndim >= 3
        else "xyz2pipe -in nus2d/recon.ft1"
    )
    direct_axis = "F3" if ndim >= 3 else "F2"
    indirect_axes = ["F2", "F1"] if ndim >= 3 else ["F1"]
    out: list[str] = []
    direct_done = False
    indirect_done: set[str] = set()
    seen_recon = False
    for line in lines:
        if not direct_done and line == direct_anchor:
            out.extend(_baseline_line(expanded, direct_axis))
            direct_done = True
        elif recon_mark in line:
            seen_recon = True
        elif seen_recon and "| nmrPipe -fn PS" in line:
            for axis in indirect_axes:
                if axis not in indirect_done:
                    out.extend(_baseline_line(expanded, axis))
                    indirect_done.add(axis)
                    break
        out.append(line)
    return out
