"""Deterministic NMRPipe script generation (conversion + processing pipeline + NUS SMILE
reconstruction). The same input (experiment metadata + processing plan) yields byte-
identical.com scripts (LF line endings), which keeps runs reproducible. The key parameters
follow the NMRFlow audit (SOFTWARE_SUMMARY section 6.2): - xMODE DQD / yMODE Echo-
AntiEcho|Complex / zMODE Complex; - DSPFVS=21 -> -ws 8 -noi2f; -DMX stays disabled; - -aq2D
values (FnMODE 6->3 E-A, 4->2 States, 5->2 States-TPPI); - NUS indirect-dimension TD uses NusTD;
when 3D NUS acqu3s TD is written as 1 (as in sampleB) the conversion layer first corrects TD to
NusTD on a staging copy before running bruker, which outputs the slice form fid/test%03d.fid
(SMILE consumes it as a slice stream; see nmrpipe_backend._convert_dir). SMILE reconstruction
parameters may be overridden through reconstruct_nus params (nSigma/thresh/scaling/report); the
SMILE command carries no window or phase parameters (step3 post-processing handles windowing and
phase), which keeps it usable for tuning against the lab script (data/ script /smile2.com)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from core.data.internal_data_model import Experiment, SamplingMode
from core.experiment.acquisition_mode_detector import (
    _REAL_FNMODE,
    ft_kind_for,
    ft_neg_for,
    unsupported_real_mode_error,
)
from core.planning.method_selector import select_method
from core.planning.processing_plan import ProcessingPlan
from ui_support.i18n import tr

_AQ2D_KEYWORDS = {0: "2", 1: "1", 2: "2", 3: "2", 4: "2", 5: "2", 6: "3"}

# FnMODE -> (FT -neg, FT -alt): the official Bruker TopSpin enumeration plus
# the official bruk2pipe ACQUISITION MODES table (nmrPipe/format docs):
#   0=undefined, 1/2=Magnitude(QF/QSEQ) and 3=TPPI are real/magnitude classes
#   and need the -yMODE Real + FT -real/-bruk/MC path (not implemented; the
#   entry point raises),
#   4=States needs no flag, 5=States-TPPI needs -alt, 6=Echo-Antiecho is
#   completed by shuffling inside the bruk2pipe conversion and needs no flag.
# The States -neg of the 3D first indirect dimension (F2) is added by the
# caller's force_neg (see core.experiment.acquisition_mode_detector.ft_neg_for).
_FT_FLAGS = {
    0: (False, False),
    1: (False, False),
    2: (False, False),
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
    """Hypercomplex component count: 2 for States/TPPI/States-TPPI/Echo-Antiecho
    and 1 for QF."""
    return 2 if int(fnmode) in (0, 1, 2, 4, 5, 6) else 1



def effective_td(experiment: Experiment) -> list[int]:
    """Effective point count per dimension: for NUS the indirect dimensions take
    the complex-point grid, everything else takes TD.

    2D NUS: the indirect-dimension complex-point grid = acqu2s TD // hypercomplex
    component count (e.g. TD=256/States -> 128), because acqu2s NusTD is not
    trusted directly (in some data NusTD equals TD and already includes the
    hypercomplex components);
    3D NUS: the acqu2s/acqu3s NusTD values are already complex point counts and
    are taken as they are.
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


# ------------------------------------------------------------ zero-fill planning
# Principle (2026-08-13, the user's plan): zero filling does not change the real
# frequency resolution (which depends on the effective acquisition time AQ=TD/SW);
# it only reduces the digital point spacing in the frequency domain (SW/SI). The
# direct dimensions F2/F3 default to SI=2xTD (a safe empirical starting point,
# 1024->2048); the indirect dimensions follow the target digital resolution:
# target spacing = max(linewidth, 1/AQ)/points_per_line (1/2 by default, i.e. at
# least 2 digital points per linewidth; precise peak positions/linewidths/fitting/
# CSP may want 1/4 or finer), the required SI is rounded up to a power of two and
# clamped to [TD, next_pow2(points_per_line x TD)] -- SI can never exceed the
# power-of-two bound of points_per_line x TD. Linewidth sources:
# params.linewidth_hz[axis] -> the per-nucleus default table -> 15 Hz. The NUS
# indirect-dimension TD uses the reconstructed full complex-point grid
# (effective_td), and zero filling applies only to the reconstructed time-domain
# data (an independent process from the SMILE reconstruction).
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
    """Default estimated linewidth per nucleus (Hz); params.linewidth_hz overrides
    the actual value."""
    for dim in experiment.dimensions:
        if dim.logical_axis == axis:
            nucleus = str(dim.nucleus or "").strip()
            return _DEFAULT_LINEWIDTH_HZ.get(nucleus, _DEFAULT_LINEWIDTH_HZ[""])
    return _DEFAULT_LINEWIDTH_HZ[""]

def _config_linewidth_by_axis(experiment: Experiment) -> dict[str, float]:
    """Read nucleus -> linewidth from config and map it to axis -> linewidth
    (falling back to the per-nucleus default table)."""
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
    """Target SI for an indirect dimension: spacing <= max(linewidth,
    1/AQ)/points_per_line.

    Physical constraint: a linewidth cannot be narrower than the real resolution
    floor 1/AQ, so max(linewidth, SW/TD) is used as the target linewidth; SI is
    clamped to [TD, next_pow2(ppl x TD)], i.e. by default no more than the
    power-of-two bound of 2xTD.
    """
    if n <= 0:
        return 1, tr("Invalid points, SI=1")
    if sw <= 0 or linewidth <= 0:
        si = _next_pow2(2 * n)
        return si, tr("SW/linewidth missing, falling back to 2x ({p0}->{p1})", p0=n, p1=si)
    natural = sw / n  # 1/AQ: the real frequency-resolution floor (Hz/pt)
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
        tr(
            "Target point distance {p0:.2f} Hz/pt(linewidth {p1:g} Hz, >= 1/AQ {p2:.2f}),Δν "
            "{p3:.2f}→{p4:.2f} Hz/pt, "
            "SI={p5}",
            p0=lw_eff / points_per_line,
            p1=linewidth,
            p2=natural,
            p3=current,
            p4=after,
            p5=si,
        ),
    )


def _points_per_line_for(points_per_line: Any, axis: str) -> float:
    """Target points per linewidth: a scalar or a per-axis mapping
    ``{"F1": 2.0}``; an invalid value falls back to the default."""
    if isinstance(points_per_line, Mapping):
        value = points_per_line.get(axis)
    else:
        value = points_per_line
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(DEFAULT_POINTS_PER_LINE)
    return number if number > 0 else float(DEFAULT_POINTS_PER_LINE)


def _scalar_points_per_line(value: Any, default: float) -> float:
    """Take a scalar (from a per-axis mapping, the first valid value among
    F1/F2/F3) for paths that leave per-axis semantics alone."""
    if isinstance(value, Mapping):
        for axis in ("F1", "F2", "F3"):
            if axis in value:
                try:
                    number = float(value[axis])
                except (TypeError, ValueError):
                    continue
                if number > 0:
                    return number
        return float(default)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if number > 0 else float(default)


def zero_fill_plan(
    experiment: Experiment,
    zero_fill: dict[str, Any] | int | None = None,
    *,
    linewidth_hz: dict[str, float] | None = None,
    points_per_line: Any = None,
) -> dict[str, dict[str, Any]]:
    """Per-dimension zero-fill plan: {axis: {"mode", "size", "note"}}.

    zero_fill override (may be empty):
    - None or 0 -> everything auto (direct dimensions 2xTD, indirect dimensions by
      the digital resolution);
    - int k>=1 -> the direct dimensions keep 2xTD and the indirect dimensions use a
      fixed kxTD (the old schema semantics);
    - {axis: {"mode": "none"|"auto"|"size"|"factor", "size"|"factor": N}}
      -> per-axis override; a bare scalar ``{axis: k}`` means the same as the
      global ``zero_fill=k`` (kxTD), and for an explicit SI write
      ``{axis: {"mode": "size", "size": N}}``.
    ``points_per_line`` may be a scalar or a per-axis mapping
    (``{"F1": 2.0, "F2": 4.0}``).
    Auto mode returns the chosen SI; with mode=none, size=None.
    linewidth_hz/points_per_line read nmrforge_data/config/nmrforge.yaml when not passed
    explicitly (processing.linewidth_hz/points_per_line; explicit params win).
    """
    from backend.config import load_processing_defaults

    if linewidth_hz is None:
        linewidth_hz = _config_linewidth_by_axis(experiment)
    if points_per_line is None:
        points_per_line = float(load_processing_defaults()["points_per_line"])
    elif not isinstance(points_per_line, Mapping):
        try:
            points_per_line = float(points_per_line)
        except (TypeError, ValueError):
            points_per_line = float(load_processing_defaults()["points_per_line"])
    axes = [dim.logical_axis for dim in experiment.dimensions]
    td = effective_td(experiment)
    direct_axis = axes[0] if axes else ''
    # 0.2.199-patch29dq (user): the NUS direct-dimension zero fill defaults to
    # 2xTD like uniform (resolution first); when memory is short the reconstruct_nus
    # memory guard (0.2.112) reduces it to 1xTD and says so, and only if that is
    # still not enough does it report "not enough memory to process this spectrum"
    direct_factor = DIRECT_ZF_FACTOR
    plan: dict[str, dict[str, Any]] = {}

    override: dict[str, dict[str, Any]] = {}
    if isinstance(zero_fill, int) and zero_fill > 0:
        for index, axis in enumerate(axes):
            n = max(int(td[index]) if index < len(td) else 0, 1)
            if axis == direct_axis:
                override[axis] = {
                    "mode": "size",
                    "size": _next_pow2(direct_factor * n),
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
                # A bare per-axis scalar means the factor kxTD (the same as the
                # global zero_fill=k).
                override[axis] = {"mode": "factor", "factor": int(cfg)}

    for index, axis in enumerate(axes):
        n = max(int(td[index]) if index < len(td) else 0, 1)
        sw = _axis_sw(experiment, axis)
        cfg = override.get(axis) or {}
        mode = cfg.get("mode", "auto")
        if mode == "none":
            plan[axis] = {"mode": "none", "size": None, "note": tr("zero filling close")}
            continue
        if mode == "factor":
            factor = max(int(cfg.get("factor", 1) or 1), 1)
            if axis == direct_axis:
                size = _next_pow2(direct_factor * n)
                note = tr("direct dimension {p0}×TD({p1}→{p2})", p0=direct_factor, p1=n, p2=size)
            else:
                size = _next_pow2(max(factor * n, n))
                note = tr("indirect dimension {p0}×TD({p1}→{p2})", p0=factor, p1=n, p2=size)
            plan[axis] = {"mode": "size", "size": size, "note": note}
            continue
        if mode in ("auto", ""):
            if cfg.get("size") is not None:
                size = int(cfg["size"])
                plan[axis] = {"mode": "size", "size": size, "note": tr("Explicit SI={p0}", p0=size)}
                continue
            if axis == direct_axis:
                size = _next_pow2(direct_factor * n)
                note = tr("direct dimension {p0}×TD({p1}→{p2})", p0=direct_factor, p1=n, p2=size)
            else:
                lw = _linewidth_for(axis, linewidth_hz) or _default_linewidth(
                    experiment, axis
                )
                size, note = _indirect_si(
                    n, sw, lw, _points_per_line_for(points_per_line, axis)
                )
            plan[axis] = {"mode": "auto", "size": size, "note": note}
        else:
            size = int(cfg.get("size", mode))
            plan[axis] = {"mode": "size", "size": size, "note": tr("Explicit SI={p0}", p0=size)}
    return plan


def zero_fill_report(plan: dict[str, dict[str, Any]]) -> list[str]:
    """Render the per-dimension zero-fill plan as log lines (so the SI choice is
    visible to the user)."""
    out: list[str] = []
    for axis in plan:
        cfg = plan[axis]
        if cfg.get("mode") == "none":
            out.append(tr("zero filling {p0}: closure", p0=axis))
        else:
            out.append(tr("zero filling {p0}: {p1}", p0=axis, p1=cfg.get('note', '')))
    return out


def select_smile_params(fraction: float) -> tuple[float, float]:
    """SMILE params (nSigma, thresh): one tier for every sampling fraction.

    2026-08-11 validation (61/63/65/67 merged 3.9%): low sampling with
    nSigma=7/thresh=0.85 gave QC 57.5 (SNR 17); nSigma=5/thresh=0.95 gave 74.1
    (SNR 92), so 5.0/0.95 is used for every fraction. The signature keeps the
    sampling fraction because callers pass it and smile_max_iter() still tiers on it.
    """
    return 5.0, 0.95


def smile_max_iter(fraction: float) -> int:
    """SMILE -maxIter by sampling fraction (final reconstruction).

    Higher sampling converges faster: >0.5 -> 300, >0.3 -> 600,
    >0.15 -> 1000, otherwise 1500 (user rule, 0.2.137).
    """
    if fraction > 0.5:
        return 300
    if fraction > 0.3:
        return 600
    if fraction > 0.15:
        return 1000
    return 1500


def _is_constant_time(experiment: Experiment) -> bool:
    # Constant-time experiment test: PULPROG values such as
    # hsqcct/cthsqc/cthmqc/hmqcct/ctet.
    acqus = experiment.acquisition_parameters.get("acqus", {}) or {}
    pulprog = str(acqus.get("PULPROG", "")).lower()
    return any(
        k in pulprog
        for k in ("cthsqc", "cthmqc", "hsqcct", "hmqcct", "ctet")
    )


def smile_cross_term_args(experiment: Experiment) -> str:
    # Whether SMILE -xCT/-yCT is added depends only on the experiment type: the
    # indirect dimensions of a constant-time experiment need the cross terms
    # switched off explicitly (-xCT 1/-yCT 1, because no coupling evolves during
    # the constant-time period and cross terms would introduce spurious peaks);
    # ordinary experiments omit them and keep SMILE's default behaviour.
    if not _is_constant_time(experiment):
        return ""
    axes = ["x", "y"] if experiment.ndim >= 3 else ["x"]
    return "".join(f"-{axis}CT 1 " for axis in axes)


def _smile_fraction(
    experiment: Experiment, grid: int, nuslist_count: int
) -> float:
    """SMILE sampling fraction: nuslist_count/grid, fallback metadata."""
    if nuslist_count and grid:
        return nuslist_count / grid
    try:
        frac = float(
            getattr(experiment.sampling, "sampling_fraction", 0.0) or 0.0
        )
    except (TypeError, ValueError):
        frac = 0.0
    return frac


def build_context(experiment: Experiment) -> dict[str, Any]:
    """Template placeholder context (authoritative metadata values + the effective
    NUS TD)."""
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
    y_kind = ft_kind_for(y_fnmode)
    y_mode = {
        "complex": "Echo-AntiEcho" if y_fnmode == 6 else "Complex",
        "magnitude": "Real",
        "tppi": "TPPI",
        "sequential": "Sequential",
    }[y_kind]
    y_real = y_kind in ("magnitude", "tppi", "sequential")  # real classes: no /2
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
        str(int(ctx["meta.td.y"]) if y_real else int(ctx["meta.td.y"]) // 2),
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
        if y_kind != "complex":
            keyword = {"magnitude": "0", "tppi": "1", "sequential": "2"}[y_kind]
        tokens += ["-aq2D", keyword] if keyword else ["-aq2D"]
    if ndim >= 3:
        z_fnmode = _fnmode(experiment, "F1")
        z_kind = ft_kind_for(z_fnmode)
        z_mode = {
            "complex": "Complex",
            "magnitude": "Real",
            "tppi": "TPPI",
            "sequential": "Sequential",
        }[z_kind]
        z_real = z_kind in ("magnitude", "tppi", "sequential")
        tokens += [
            "-zMODE",
            z_mode,
            "-zN",
            str(ctx["meta.td.z"]),
            "-zT",
            str(int(ctx["meta.td.z"]) if z_real else int(ctx["meta.td.z"]) // 2),
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
    out_file: str | None = None,
    direct_points: int | None = None,
) -> str:
    """Generate the bruk2pipe conversion script (LF line endings, csh syntax; used
    only by the uniform-sampling fallback).

    out_file defaults to ./{dataset_id}.fid (0.2.163-patch13: this matches the
    naming of the fid.com patched by bruker -AUTO, so the default test.fid is no
    longer written).
    """
    if out_file is None:
        out_file = f"./{experiment.dataset_id}.fid"
    ctx = build_context(experiment)
    tokens = _bruk2pipe_tokens(experiment, ctx)
    tokens[tokens.index("-in") + 1] = in_file
    tokens[tokens.index("-out") + 1] = out_file
    if direct_points:
        # 0.2.199-patch30: -xN must match the physical ser row (Bruker pads to
        # serPadSize), not the acqus TD; see physical_direct_points
        tokens[tokens.index("-xN") + 1] = str(int(direct_points))
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
    *,
    keep_direct_complex: bool = False,
    direct_axis: str = "",
    complex_axes: frozenset[str] | None = None,
    skip_baseline_axes: frozenset[str] | None = None,
) -> list[str]:
    lines: list[str] = []
    for op, params in stages:
        if op == "combine_hypercomplex":
            continue  # bruk2pipe already completed the hypercomplex reconstruction
        if op == "apodization":
            axis = str(params.get("axis", ""))
            cfg = dict(params.get("params", {}) or {})
            if window and axis in window:
                cfg.update(window[axis] or {})
            wtype = str(cfg.get("type", "sine_bell"))
            # 0.2.189: an explicit window[axis] type=none adds no apodisation for
            # that axis (no SP is inserted); this is how the fixed "no window" on
            # the indirect dimensions takes effect (uniform takes this branch too)
            if wtype in ("none", "off"):
                continue
            if wtype == "gaussian":
                # NMRPipe: GM accepts only -g1/-g2/-g3 (0.2.170). History: GM
                # -lb/-gb was silently ignored (the window had no effect), while
                # GMB -lb/-gb blew up towards the end of the FID in practice
                # (0.2.165 user report of a completely wrong spectrum). The
                # NMRPipe-native g1/g2 are used throughout (defaults 8/15; measured
                # behaviour is gentle, peak about 1.2 with a smoothly decaying
                # tail), and lb/gb (Bruker semantics) are no longer mapped directly.
                lines.append(
                    f"| nmrPipe -fn GM -g1 {_fmt(cfg.get('g1', 8.0))} "
                    f"-g2 {_fmt(cfg.get('g2', 15.0))} \\"
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
            kind = str(params.get("kind", "complex") or "complex")
            if kind == "tppi":
                # TPPI(phase-sensitive real):FT -real
                lines.append("| nmrPipe -fn FT -real \\")
            elif kind == "sequential":
                # QSEQ/Sequential: detected directly by Bruker; FT -bruk
                # (= -alt -real)
                lines.append("| nmrPipe -fn FT -bruk \\")
            elif kind == "magnitude":
                # QF/magnitude: complex FT of the indirect dimension (direction
                # via -neg), followed by MC
                neg = bool(params.get("neg"))
                suffix = " -neg" if neg else ""
                lines.append(f"| nmrPipe -fn FT{suffix} \\")
            else:
                # Sampling overrides and flag assembly both go through _ft_flags
                # (the same source as NUS/finalize)
                flags = _ft_flags(
                    bool(params.get("neg")),
                    bool(params.get("alt")),
                    sampling=sampling,
                    axis=axis,
                )
                suffix = (" " + " ".join(flags)) if flags else ""
                lines.append(f"| nmrPipe -fn FT{suffix} \\")
        elif op == "magnitude":
            lines.append("| nmrPipe -fn MC \\")
        elif op == "phase":
            p0 = params.get("p0", 0.0)
            p1 = params.get("p1", 0.0)
            axis = params.get("axis", "")
            if direct_phase and axis in direct_phase:
                p0, p1 = direct_phase[axis]
            keep_cplx = (complex_axes and axis in complex_axes) or (
                keep_direct_complex and axis == direct_axis
            )
            di = "" if keep_cplx else " -di"
            lines.append(
                f"| nmrPipe -fn PS -p0 {_fmt(p0)} -p1 {_fmt(p1)}{di} \\"
            )
        elif op == "baseline":
            axis = str(params.get("axis", ""))
            if skip_baseline_axes and axis in skip_baseline_axes:
                continue  # preview search axis: skip POLY (see generate_preview_script)
            cfg = dict(params)
            if baseline and axis in baseline:
                cfg.update(baseline[axis])
            if not _as_bool(cfg.get("enabled", True)):
                continue
            if str(cfg.get("mode", "auto")) == "order":
                order = max(1, int(cfg.get("order", 1) or 1))
                # 2026-09-16 (real hardware): a bare NMRPipe ``POLY -ord N`` has
                # no baseline nodes (-nc 0 and no -first/-last), so it is the
                # identity operation; -auto is needed for baseline points to be
                # picked and fitted automatically, and only then does -ord N take
                # effect.
                lines.append(f"| nmrPipe -fn POLY -ord {order} -auto \\")
            else:
                lines.append("| nmrPipe -fn POLY -auto \\")
        else:
            raise ValueError(tr(
                "Operations mapped to the nmrPipe macro are not supported: "
                "{p0}",
                p0=op,
            ))
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
    ext_lo: str = "10.5",
    ext_hi: str = "6.5",
    extract: bool = True,
    sampling: dict[str, Any] | None = None,
    keep_direct_complex: bool = False,
    complex_axes: frozenset[str] | None = None,
    skip_baseline_axes: frozenset[str] | None = None,
    direct_poly_time: bool = False,
) -> str:
    """Translate the processing plan (DAG) into an NMRPipe pipeline script (direct
    dimension -> EXT -> TP -> indirect dimensions).

    EXT extracts a window along the direct dimension (1H), 6.5-10.5 ppm by
    default (ext_lo=10.5, ext_hi=6.5), as in the NUS script; extract=False
    switches it off.
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
        # DC-offset correction (POLY -time) acts on the direct-dimension time-domain
        # FID and must precede windowing/FT, as in NUS script step1
        # (0.2.155/0.2.160: it goes only into the full final-run script)
        if direct_poly_time and index == 0:
            lines.append("| nmrPipe -fn POLY -time " + "\\")
        lines += _stage_lines(
            _axis_stages(plan, axis),
            direct_phase,
            baseline,
            window,
            zf_plan,
            sampling,
            keep_direct_complex=keep_direct_complex and index == 0,
            direct_axis=axes[0],
            complex_axes=complex_axes,
            skip_baseline_axes=skip_baseline_axes,
        )
        if extract and index == 0:
            lines.append(
                f"| nmrPipe -fn EXT -x1 {ext_lo}ppm -xn {ext_hi}ppm -sw -round 2 \\"
            )
        if index < len(axes) - 1:
            if len(axes) >= 3 and index == len(axes) - 2:
                # 3D single pass: before the last dimension (F1) ZTP moves the slow
                # dimension onto the current FT axis (aligned with the NUS
                # finalize); otherwise the third FT acts on an already
                # frequency-domain dimension, transposing the complex intermediate
                # dimension gives a broken pipe, and F1 stays in the time domain.
                lines.append("| nmrPipe -fn ZTP \\")
            else:
                lines.append("| nmrPipe -fn TP \\")
    if len(axes) == 2:
        # NMRPipe 2D: after the indirect-dimension FT another TP must transpose
        # back, otherwise the output has F1/F2 swapped
        lines.append("| nmrPipe -fn TP \\")
    elif len(axes) >= 3:
        # 3D single pass: one more TP after the last FT gives the (F2,F1,F3) file
        # layout (consistent with file_axis_index/_axis_index, so complex previews
        # read the axes correctly).
        lines.append("| nmrPipe -fn TP \\")
    lines.append(f"| pipe2xyz -out {out_file} -x")
    return "\n".join(lines) + "\n"


def generate_preview_script(
    experiment: Experiment,
    plan: ProcessingPlan,
    *,
    in_file: str,
    out_file: str,
    preview_axis: str,
    fixed_phases: dict[str, tuple[float, float]] | None = None,
    baseline: dict[str, dict[str, Any]] | None = None,
    window: dict[str, dict[str, Any]] | None = None,
    zero_fill: dict[str, dict[str, Any]] | None = None,
    ext_lo: str = "10.5",
    ext_hi: str = "6.5",
    extract: bool = True,
    sampling: dict[str, Any] | None = None,
) -> str:
    """First complex preview script (uniform): the whole production pipeline,
    except that only the preview_axis PS omits -di (that dimension outputs real
    imaginary data) while the other axes add -di using fixed_phases (0 by
    default).

    zero_fill defaults to no zero fill (the same parameters as the old phase
    candidates, which keeps the in-memory rotated candidates and the old
    backend's candidates from the same source); 0.2.199-patch29dn (option A,
    user): zero filling comes before phase optimisation -- the phase-search
    preview may pass the same auto full zero fill as the final run, avoiding
    disagreement between the best score at low resolution and the final spectrum
    (sampleI F1 scored 105 deg on a 431-point no-zero-fill preview while the
    1024-point final spectrum was optimal at 90 deg).

    The output is a production-layout complex file (pipe2xyz -x) and the display
    layer reads its array axis along preview_axis for in-memory rotation scoring.
    F2/F1 previews of 3D data use the production layout as well, avoiding
    transposition ambiguity.

    preview_axis itself skips POLY: the old candidates were "rotate, take the
    real part, then re-fit POLY per candidate"; baking the POLY fitted on the
    (0,0) state into the preview would contaminate the baseline after the
    in-memory rotation and bias the score (VM sampleF F1 was off by 30 deg, and
    removing POLY matched the old scheme). The POLY of the other axes (whose
    phase is fixed) is kept, as in the old candidates.
    """
    axes = [dim.logical_axis for dim in experiment.dimensions]
    zf_none = zero_fill if zero_fill is not None else {
        axis: {"mode": "none"} for axis in axes
    }
    phases = {
        axis: value
        for axis, value in (fixed_phases or {}).items()
        if axis != preview_axis
    }
    for axis in axes:
        if axis != preview_axis:
            phases.setdefault(axis, (0.0, 0.0))
    return generate_process_script(
        experiment,
        plan,
        in_file=in_file,
        out_file=out_file,
        direct_phase=phases,
        baseline=baseline,
        window=window,
        zero_fill=zf_none,
        ext_lo=ext_lo,
        ext_hi=ext_hi,
        extract=extract,
        sampling=sampling,
        complex_axes=frozenset({preview_axis}),
        skip_baseline_axes=frozenset({preview_axis}),
    )



def _nus_zf_size(cfg: dict[str, Any], td_points: int) -> int:
    """NUS dimension zero-fill size: an explicit size wins, otherwise
    next_pow2(2xTD)."""
    return int(cfg.get("size") or _next_pow2(2 * max(int(td_points), 1)))


def _ft_flags(
    base_neg: bool,
    base_alt: bool,
    *,
    sampling: dict[str, Any] | None = None,
    axis: str = "",
    force_neg: bool = False,
) -> list[str]:
    """FT flag list (the single implementation of the sampling override logic).

    base_neg/base_alt come from the caller (the uniform path takes them from the
    plan node; NUS/finalize derive them from FnMODE); sampling.ft_neg/ft_alt
    override them when not None. With flip_f1=True the F1 axis is forced to -neg
    (flipped); with force_neg=True the default state gains -neg (the correction
    for States-type 3D first indirect dimensions, see ft_neg_for), while an
    explicit sampling.ft_neg still wins; by default the derived output is
    unchanged.
    """
    neg, alt = bool(base_neg), bool(base_alt)
    if sampling:
        if sampling.get("ft_neg") is not None:
            neg = bool(sampling.get("ft_neg"))
        if sampling.get("ft_alt") is False:
            alt = False  # True = auto by acquisition mode; False = force off
        if axis == "F1" and bool(sampling.get("flip_f1")):
            neg = True
    if force_neg and (not sampling or sampling.get("ft_neg") is None):
        neg = True
    flags = []
    # The order is fixed as -alt -neg (matching the lab's hand-made templates,
    # which makes diffing easy)
    if alt:
        flags.append("-alt")
    if neg:
        flags.append("-neg")
    return flags


_SMILE_DIRECTION_FLAG = {"-alt": "Alt", "-neg": "Neg"}


def _smile_direction_args(flags: list[str], prefix: str) -> str:
    """_ft_flags output -> SMILE dimension-direction arguments
    (-xAlt/-xNeg/-yAlt/-yNeg).

    The direction flags inside the SMILE command come from the same source as the
    step3 FT lines (the same sampling override and force_neg derivation), which
    keeps the FT inside the reconstruction consistent with the post-processing
    direction.
    """
    parts = [
        f"-{prefix}{_SMILE_DIRECTION_FLAG[f]}"
        for f in flags
        if f in _SMILE_DIRECTION_FLAG
    ]
    return " ".join(parts)


def _ft_flag_line(
    fnmode: int,
    *,
    sampling: dict[str, Any] | None = None,
    axis: str = "",
    force_neg: bool = False,
) -> str:
    """FT line flags: sampling.ft_neg/ft_alt override the FnMODE derivation when
    not None; flip_f1=True forces -neg on the F1 axis (flip); force_neg=True adds
    -neg in the default state (the correction for States-type 3D first indirect
    dimensions); by default the derived output is unchanged."""
    neg, alt = _FT_FLAGS.get(int(fnmode), (False, False))
    flags = _ft_flags(
        neg, alt, sampling=sampling, axis=axis, force_neg=force_neg
    )
    suffix = (" " + " ".join(flags)) if flags else ""
    return f"| nmrPipe -fn FT{suffix} \\"


def _ps_line(phases: dict[str, tuple[float, float]] | None, axis: str) -> str:
    """Indirect-dimension PS line: fills in the optimised phase (0 by default) and
    takes the real part (-di); the 2026-08-19 full-script final run."""
    p0, p1 = (phases or {}).get(axis, (0.0, 0.0))
    return f"| nmrPipe -fn PS -p0 {p0:g} -p1 {p1:g} -di \\"


def _nus_direct_window_line(
    cfg: dict[str, Any] | None, default_pow: int = 2
) -> str:
    """NUS direct-dimension window: SMILE requires the direct dimension to be
    apodised with a tail decaying to zero, so SP is used throughout (as in the
    lab's smile.com). The sine_bell family is generated from the config and
    everything else (none/gaussian/exp) falls back to the default SP -- otherwise
    SMILE reports "direct dim not apodized" and the final spectrum is wrong
    (0.2.199-patch11)."""
    wtype = str((cfg or {}).get("type", ""))
    if wtype in ("sine_bell", "sine_bell_squared", "sp"):
        line = _window_line(cfg)
        if line:
            return line
    return (
        f"| nmrPipe -fn SP -off 0.45 -end 0.98 "
        f"-pow {default_pow} -c 0.5 \\"
    )


def _window_line(cfg: dict[str, Any] | None) -> str | None:
    """NUS window-function line (the same mapping as uniform _stage_lines
    apodization); None = no window inserted.

    The direct dimension is windowed in step1 SP (before the FT) and the indirect
    dimensions in step3 SP (before ZF/FT); no window is inserted by default
    (keeping the historical script structure) and only an explicit configuration
    generates one. gaussian -> GM (-g1/-g2, 0.2.170), exp -> EM, and everything
    else follows sine_bell (off/end/pow/c).
    """
    if not cfg:
        return None
    wtype = str(cfg.get("type", "sine_bell"))
    if wtype in ("none", "off"):
        return None  # explicit no-window candidate: no SP is inserted
    if wtype == "gaussian":
        # 0.2.170: GM -g1/-g2 are NMRPipe's native Gaussian window parameters;
        # GMB -lb/-gb blew up at the tail in practice and GM -lb/-gb is ignored,
        # so neither is usable
        return (
            f"| nmrPipe -fn GM -g1 {_fmt(cfg.get('g1', 8.0))} "
            f"-g2 {_fmt(cfg.get('g2', 15.0))} \\"
        )
    if wtype == "exp":
        return f"| nmrPipe -fn EM -lb {_fmt(cfg.get('lb', 5.0))} \\"
    powv = 2 if wtype == "sine_bell_squared" else cfg.get("pow", 1)
    return (
        f"| nmrPipe -fn SP -off {_fmt(cfg.get('off', 0.45))} "
        f"-end {_fmt(cfg.get('end', 0.95))} -pow {_fmt(powv)} "
        f"-c {_fmt(cfg.get('c', 0.5))} \\"
    )


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
    smile_scaling: bool = True,
    smile_report: int = 1,
    max_mem: float | None = None,
    direct_phase: tuple[float, float] = (0.0, 0.0),
    phases: dict[str, tuple[float, float]] | None = None,
    window: dict[str, dict[str, Any]] | None = None,
    extract: bool = True,
    baseline: dict[str, Any] | None = None,
    zero_fill: dict[str, Any] | int | None = None,
    linewidth_hz: dict[str, float] | None = None,
    points_per_line: float = DEFAULT_POINTS_PER_LINE,
    sampling: dict[str, Any] | None = None,
    direct_poly_time: bool = False,
) -> str:
    """2D NUS SMILE reconstruction (two stages; verified on real data (sampleA)
    25% NUS).

    stage 1: direct dimension (F2) FT+EXT+POLY -> TP -> SMILE (-sample None, -xT
    the complex-point grid) -> nus2d/recon.ft1; stage 2: indirect dimension (F1)
    ZF/FT -alt/PS/POLY/TP -> the final spectrum ft2 (-out -ov).
    A single file uses nmrPipe -in (a 2D single file holds 1 plane, so xyz2pipe
    cannot be used); segmented multiple files (test%03d.fid) fall back to
    xyz2pipe plus -sample nuslist.
    """
    _check_real_modes(experiment)
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
    x_t = max(1, int(td[1])) if len(td) > 1 else 1  # indirect-dimension complex grid
    # SMILE's internal direction flags come from the same source as the stage-2
    # finalize (the same _FT_FLAGS derivation plus the sampling override); the 2D
    # single indirect dimension (F1) has no force_neg
    f1_dir_flags = _ft_flags(
        *_FT_FLAGS.get(int(f1_fnmode), (False, False)),
        sampling=sampling,
        axis="F1",
    )
    f1_dir_arg = _smile_direction_args(f1_dir_flags, "x")
    _grid2 = max(int(td[1]), 1)
    _frac2 = _smile_fraction(experiment, _grid2, nuslist_count)
    max_iter = smile_max_iter(_frac2)
    xct_arg = smile_cross_term_args(experiment)
    multi = "%" in in_file
    expanded = expand_baseline(experiment, baseline)
    direct_poly = _baseline_line(expanded, "F2")
    indirect_poly = _baseline_line(expanded, "F1")
    direct_window_cfg = (window or {}).get("F2")
    direct_stages = []
    if direct_poly_time:
        direct_stages.append("| nmrPipe -fn POLY -time " + "\\")
    # 0.2.199-patch11: the direct-dimension window is fixed to SP (SMILE requires
    # the direct dimension to be apodised with a decaying tail)
    direct_stages.append(_nus_direct_window_line(direct_window_cfg, 1))
    if f2_zf.get("mode") != "none":
        direct_stages.append(f"| nmrPipe -fn ZF -zf -size {direct_zf} \\")
    direct_stages.append("| nmrPipe -fn FT \\")
    if extract:
        direct_stages.append(
            f"| nmrPipe -fn EXT -x1 {ext_lo}ppm -xn {ext_hi}ppm -sw -round 2 \\"
        )
    # The direct-dimension phase is applied after EXT: p1 is normalised to the
    # extracted size (consistent with the in-memory rotation of the recon planes)
    direct_stages.append(
        f"| nmrPipe -fn PS -p0 {direct_phase[0]:g} -p1 {direct_phase[1]:g} -di \\"
    )
    direct_stages += direct_poly
    smile_tail = [
        # SMILE carries no window or phase (0.2.134): windowing and phase are
        # handled by the later finalize/step3 post-processing
        f"           -maxIter {max_iter} \\",
        f"           -xT {x_t} \\",
        *([f"           {f1_dir_arg} \\"] if f1_dir_arg else []),
        f"           {xct_arg}-thresh {thresh:g} \\",
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
            "| nmrPipe -fn SMILE -nDim 2 \\",
            f"           -sample {nuslist} -nThread {nthread} \\",
            f"           -sampleCount {nuslist_count} -nSigma {nsigma:g} "
            f"-off 0 0 -report {smile_report} \\",
            *(
                []
                if max_mem is None
                else [f"           -maxMem {max_mem:g} \\"]
            ),
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
            "| nmrPipe -fn SMILE -nDim 2 \\",
            f"           -sample {nuslist or 'None'} -nThread {nthread} \\",
            f"           -sampleCount {nuslist_count} -nSigma {nsigma:g} "
            f"-off 0 0 -report {smile_report} \\",
            *(
                []
                if max_mem is None
                else [f"           -maxMem {max_mem:g} \\"]
            ),
            *(["           -scaling 1 \\"] if smile_scaling else []),
            *smile_tail,
        ]
    f1_zf_line: list[str] = []
    if f1_zf.get("mode") != "none":
        f1_zf_line = [
            f"| nmrPipe -fn ZF -size {f1_zf.get('size') or _next_pow2(2 * max(int(td[1]), 1))} \\"
        ]
    f1_window = _window_line((window or {}).get("F1"))
    lines += [
        "",
        "# stage 2: indirect dim (F1) window + ZF + FT -alt + PS + POLY",
        "nmrPipe -in nus2d/recon.ft1 \\",
        *([f1_window] if f1_window else []),
        *f1_zf_line,
        _ft_flag_line(f1_fnmode, sampling=sampling, axis="F1"),
        _ps_line(phases, "F1"),
        *indirect_poly,
        "| nmrPipe -fn TP \\",
        f"  -out {out_file} -ov",
    ]
    return "\n".join(lines) + "\n"



# ---------------------------------------------------------------------------
# SMILE parameter sweep: splitting the final-run script (0.2.199-patch29hz-fix3)
#
# The sweep uses the final-run script as its template and changes only the SMILE
# parameters:
#   the first part (direct dimension -> slice files) runs once;
#   the second part (SMILE + indirect dimension -> final spectrum) runs again with
#   different SMILE parameters, and each candidate spectrum is evaluated and then
#   deleted.
# 3D and 2D multi-file scripts write and read back slices themselves, so they can
# be split between the two parts. A 2D single-file script has no slice stream
# (the direct-dimension processing and SMILE live in one pipeline), so the split
# returns empty and the caller (smile_scan) falls back to "run the whole script
# per combination, each candidate writing its own output" -- the user's
# 2026-09-11 constraint "2D should not have a slice stream, mind the
# compatibility".
# ---------------------------------------------------------------------------
_NUS_SLICE_WRITES = (
    "pipe2xyz -out nus3d_1/test%04d.ft1 -z",
    "pipe2xyz -out nus2d/test%03d.ft1 -z",
)


def rename_nus_scan_output(script: str, out_name: str) -> str:
    """Rewrite the **last line** of the sweep script to the candidate's own output
    name (so 25 combinations do not overwrite each other).

    Only the final-spectrum write changes: in 3D it is `| pipe2xyz -out <file>
    -x` and in 2D `-out <file> -ov`. Intermediate products (such as nus3d_rc/...
    and nus2d/recon.ft1) stay as they are, otherwise later statements could not
    read them. When no output line is found the script is returned unchanged.
    """
    import re

    lines = script.split("\n")
    index = next(
        (i for i in range(len(lines) - 1, -1, -1) if "-out " in lines[i]),
        None,
    )
    if index is None:
        return script
    lines[index] = re.sub(r"(-out\s+)\S+", rf"\g<1>{out_name}", lines[index], count=1)
    return "\n".join(lines)


def _statement_start(lines: list[str], index: int) -> int:
    """Return the start line of the pipeline statement containing line index
    (continuation lines end with a backslash)."""
    start = index
    while start > 0:
        prev = lines[start - 1].rstrip()
        if prev.endswith("\\") or prev.lstrip().startswith("|"):
            start -= 1
            continue
        break
    return start


def split_nus_script(script: str) -> tuple[str, str]:
    """Split the NUS final-run script into (direct-dimension part,
    SMILE+indirect part).

    When it cannot be split it returns (", ") and the caller should fall back
    (rather than silently splitting in the wrong place).
    """
    lines = script.split("\n")
    write_index = None
    for i, line in enumerate(lines):
        if any(marker in line for marker in _NUS_SLICE_WRITES):
            write_index = i
            break
    if write_index is not None:
        for j in range(write_index + 1, len(lines)):
            if lines[j].strip().startswith("xyz2pipe -in "):
                prefix = "\n".join(lines[: write_index + 1]) + "\n"
                return prefix, "\n".join(lines[j:])
        return "", ""
    # A 2D single-file script has no slice stream (the direct-dimension processing
    # and SMILE live in one pipeline), and cutting an artificial slice stream would
    # change SMILE's sampling semantics, so it is not split here; the caller
    # (smile_scan) falls back to "run the whole script per combination, each output
    # named individually" (the user's 2026-09-11 constraint).
    return "", ""


_NUS_2D_DIRECT_OUT = "| pipe2xyz -out nus2d/direct.ft1 -x -ov"
_NUS_2D_TP_MARKER = "-fn TP"


def build_2d_direct_only_script(script: str) -> str:
    """Cut a "direct-dimension only" script out of the 2D final-run script
    (archiving the 2D holdout residual).

    2D has no slice stream: the direct-dimension processing and SMILE share one
    pipeline, so SMILE and everything after it are removed and a single-file
    output nus2d/direct.ft1 is appended (archiving SMILE's input). Measured
    correspondence (VM 2026-09-11, the 2D NUS made by sampleF): complex point k
    -> row 2k (real) / 2k+1 (imaginary) <-> column k of SMILE's nus2d/recon.ft1,
    correlation 1.000. When it cannot be cut (SMILE is not preceded by a TP line)
    an empty string is returned and the caller skips the holdout residual.
    """
    lines = script.split("\n")
    smile = next((i for i, line in enumerate(lines) if "-fn SMILE" in line), None)
    if smile is None:
        return ""
    prefix = lines[:smile]
    if not prefix or _NUS_2D_TP_MARKER not in prefix[-1]:
        return ""
    prefix[-1] = _NUS_2D_DIRECT_OUT
    return "\n".join(prefix) + "\n"

def _check_real_modes(experiment: Experiment) -> None:
    """Raise explicitly for real/magnitude indirect dimensions (FnMODE 1/2/3),
    which prevents silently wrong scripts.

    The indirect dimension picks the matching acquire file by logical axis
    (F1=acqu3s/F2=acqu2s; in 2D the indirect dimension is F1=acqu2s, handled
    uniformly by _fnmode).
    """
    for dim in experiment.dimensions:
        if dim.role.name.startswith("DIRECT"):
            continue
        fnmode = _fnmode(experiment, dim.logical_axis)
        if fnmode in _REAL_FNMODE:
            raise NotImplementedError(
                unsupported_real_mode_error(fnmode, logical_axis=dim.logical_axis)
            )


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
    smile_scaling: bool = True,
    smile_report: int = 1,
    max_mem: float | None = None,
    direct_phase: tuple[float, float] = (0.0, 0.0),
    phases: dict[str, tuple[float, float]] | None = None,
    window: dict[str, dict[str, Any]] | None = None,
    extract: bool = True,
    baseline: dict[str, Any] | None = None,
    zero_fill: dict[str, Any] | int | None = None,
    linewidth_hz: dict[str, float] | None = None,
    points_per_line: float = DEFAULT_POINTS_PER_LINE,
    sampling: dict[str, Any] | None = None,
    direct_poly_time: bool = False,
) -> str:
    """3D NUS SMILE reconstruction: direct dimension (F3) FT+EXT -> SMILE -nDim 3
    -> indirect-dimension FT (ft3)."""
    _check_real_modes(experiment)
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
    f2_window = _window_line((window or {}).get("F2"))
    f1_window = _window_line((window or {}).get("F1"))
    step1_direct: list[str] = []
    if direct_poly_time:
        step1_direct.append("| nmrPipe -fn POLY -time " + "\\")
    # 0.2.199-patch11: the direct-dimension window is fixed to SP (SMILE requires
    # the direct dimension to be apodised with a decaying tail)
    step1_direct.append(_nus_direct_window_line((window or {}).get("F3"), 2))
    # SMILE's internal direction flags come from the same source as step3: the same
    # _FT_FLAGS derivation plus the sampling override; F2 adds force_neg (the
    # States-type 3D first indirect dimension, see ft_neg_for)
    x_dir_flags = _ft_flags(
        *_FT_FLAGS.get(int(f2_fnmode), (False, False)),
        sampling=sampling,
        axis="F2",
        force_neg=ft_neg_for(experiment, f2_fnmode, "F2"),
    )
    y_dir_flags = _ft_flags(
        *_FT_FLAGS.get(int(f1_fnmode), (False, False)),
        sampling=sampling,
        axis="F1",
    )
    x_dir_arg = _smile_direction_args(x_dir_flags, "x")
    y_dir_arg = _smile_direction_args(y_dir_flags, "y")
    _grid = max(int(ctx["meta.td.y"]) * int(ctx["meta.td.z"]), 1)
    _frac = _smile_fraction(experiment, _grid, nuslist_count)
    max_iter = smile_max_iter(_frac)
    xct_arg = smile_cross_term_args(experiment)
    lines = [
        "#!/bin/csh",
        "# NMRForge 3D NUS SMILE reconstruction",
        f"# experiment: {experiment.dataset_id}",
        "mkdir -p nus3d_1 nus3d_rc",
        "# step 1: direct dim (F3) FT + EXT + PS",
        f"xyz2pipe -in {in_file} -x \\",
        *step1_direct,
        *(
            [f"| nmrPipe -fn ZF -zf -size {direct_zf} \\"]
            if f3_zf.get("mode") != "none"
            else []
        ),
        "| nmrPipe -fn FT \\",
        f"| nmrPipe -fn EXT -x1 {ext_lo}ppm -xn {ext_hi}ppm -sw -round 2 \\",
        # The direct-dimension phase is applied after EXT: p1 is normalised to the
        # extracted size (consistent with the in-memory rotation of recon planes)
        f"| nmrPipe -fn PS -p0 {direct_phase[0]:g} -p1 {direct_phase[1]:g} -di \\",
        "| pipe2xyz -out nus3d_1/test%04d.ft1 -z",
        "",
        "# step 2: SMILE reconstruct indirect dims (F2/F1)",
        "xyz2pipe -in nus3d_1/test%04d.ft1 -x \\",
        "| nmrPipe -fn SMILE -nDim 3 \\",
        f"           -sample {nuslist} -nThread {nthread} \\",
        f"           -sampleCount {nuslist_count} -nSigma {nsigma:g} -off 0 0 "
        f"-report {smile_report} \\",
        *(
            []
            if max_mem is None
            else [f"           -maxMem {max_mem:g} \\"]
        ),
        *(["           -scaling 1 \\"] if smile_scaling else []),
        f"           -maxIter {max_iter} \\",
        # SMILE carries no window or phase (0.2.134): step3 post-processing handles
        # them; the direction flags (0.2.135) come from the same source as the step3
        # FT: F2 is derived from FnMODE with force_neg added (the States -neg of the
        # 3D first indirect dimension) and F1 follows the FnMODE derivation, both
        # subject to the sampling override -- so the direction inside the
        # reconstruction cannot disagree with the post-processing
        *([f"           {x_dir_arg} \\"] if x_dir_arg else []),
        *([f"           {y_dir_arg} \\"] if y_dir_arg else []),
        f"           {xct_arg}-thresh {thresh:g} \\",
        "| pipe2xyz -out nus3d_rc/test%04d.ft1 -x",
        "",
        "# step 3: indirect dims (F2/F1) window + ZF + FT + PS",
        "xyz2pipe -in nus3d_rc/test%04d.ft1 -x \\",
        *([f2_window] if f2_window else []),
        *(
            [
                f"| nmrPipe -fn ZF -size {f2_zf_size} \\"
            ]
            if f2_zf.get("mode") != "none"
            else []
        ),
        _ft_flag_line(
            f2_fnmode,
            sampling=sampling,
            axis="F2",
            force_neg=ft_neg_for(experiment, f2_fnmode, "F2"),
        ),
        _ps_line(phases, "F2"),
        "| nmrPipe -fn TP \\",
        *([f1_window] if f1_window else []),
        *(
            [
                f"| nmrPipe -fn ZF -size {f1_zf_size} \\"
            ]
            if f1_zf.get("mode") != "none"
            else []
        ),
        _ft_flag_line(f1_fnmode, sampling=sampling, axis="F1"),
        _ps_line(phases, "F1"),
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
    """JSON schema for the processing-plan parameters (API_CONTRACT section 6 keys
    plus defaults/descriptions).

    Aligned with presets/config: zero_fill, sampling
    (ft_neg/ft_alt/flip_f1/auto_phase) and stages
    (id/tool/macro/params/param_docs); used by the GUI parameter-table editor and
    by the renderer.
    """
    return {
        "type": "object",
        "title": tr("NMRForge processing parameter"),
        "description": (
            tr(
            "Processing plan parameter (table editor/script rendering shared data "
            "source)",
        )
        ),
        "properties": {
            "zero_fill": {
                "type": "integer",
                "default": 2,
                "description": (
                    tr(
                        "Zero-fill: 0 = automatic (direct dimension 2xTD, indirect dimension "
                        "driven by the target digital resolution, bounded by 1/AQ and not "
                        "exceeding points_per_line x TD); k >= 1 = indirect dimension fixed at k x "
                        "TD (the direct dimension stays 2xTD). Zero-fill does not change the true "
                        "frequency resolution (set by AQ); it only reduces the digital point "
                        "spacing.",
                    )
                ),
            },
            "linewidth_hz": {
                "type": "object",
                "description": (
                    tr(
                        "Per-axis estimated linewidth (Hz); the target linewidth for automatic "
                        "indirect zero-fill. Defaults by nucleus (1H 8 / 15N 15 / 13C 20 Hz and so "
                        "on) and is bounded below by 1/AQ.Example: {\"F1\": "
                        "15.0}",
                    )
                ),
                "additionalProperties": {"type": "number"},
            },
            "points_per_line": {
                "type": "number",
                "default": 2.0,
                "description": (
                    tr(
                        "Indirect-dimension target digital point spacing = max(linewidth, 1/AQ) / "
                        "points_per_line (default 1/2, i.e. at least 2 digital points per "
                        "linewidth; raise it for accurate peak positions / linewidths / fitting / "
                        "CSP, lower it to save memory in "
                        "2D)",
                    )
                ),
            },
            "sampling": {
                "type": "object",
                "description": tr("sampling / acquisition related flags"),
                "properties": {
                    "ft_neg": {
                        "type": ["boolean", "null"],
                        "default": None,
                        "description": (
                            tr(
                            "FT and then flip the axis (null=automatic according to acquisition "
                            "mode, True/False=mandatory)",
                        )
                        ),
                    },
                    "ft_alt": {
                        "type": "boolean",
                        "default": True,
                        "description": (
                            tr(
                                "TPPI / States-TPPI +/- alternation correction (True = automatic "
                                "from the acquisition mode, False = forced "
                                "off)",
                            )
                        ),
                    },
                    "flip_f1": {
                        "type": "boolean",
                        "default": False,
                        "description": tr("F1 axis flip (FT -neg)"),
                    },
                    "auto_phase": {
                        "type": "boolean",
                        "default": True,
                        "description": tr("direct dimension p1 consensus automatic phase"),
                    },
                },
            },
            "ext_lo": {
                "type": "string",
                "default": "10.5",
                "description": tr("direct dimension 1H extraction window height ppm(EXT -x1)"),
            },
            "ext_hi": {
                "type": "string",
                "default": "6.5",
                "description": tr("direct dimension 1H extraction window low ppm(EXT -xn)"),
            },
            "extract": {
                "type": "boolean",
                "default": True,
                "description": tr("Is the direct dimension extraction window open?"),
            },
            "baseline": {
                "type": "object",
                "description": tr("Dimension-wise baseline correction (POLY, Contract §6)"),
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
                "description": (
                    tr(
                    "List of processing stages (displayed line by line in the table "
                    "editor)",
                )
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": tr("stage unique id")},
                        "tool": {"type": "string", "description": tr(
                            "Backend tools(nmrpipe/native)",
                        )},
                        "macro": {"type": "string", "description": tr(
                            "NMRPipe macro(SP/ZF/FT/PS)",
                        )},
                        "params": {"type": "object", "description": tr("macro parameter")},
                        "param_docs": {"type": "object", "description": tr(
                            "parameter description",
                        )},
                    },
                    "required": ["id", "tool", "macro"],
                },
            },
        },
        "default": {
            "zero_fill": 2,
            "linewidth_hz": {},
            "points_per_line": 2.0,
            "ext_lo": "10.5",
            "ext_hi": "6.5",
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
    """Lenient boolean conversion (the GUI may pass the strings "false"/"0")."""
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "")
    return bool(value)


def render_scripts(
    experiment: Experiment,
    params: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Deterministically render fid.com/process.com/nus*.com (for the GUI to
    display and to save for execution).

    The same input (experiment metadata + parameters) yields byte-identical
    scripts; NUS additionally renders nus.com and uniform renders process.com;
    fid.com is the deterministic bruk2pipe conversion script.
    """
    params = dict(params or {})
    plan = select_method(experiment)
    out_ext = "ft3" if experiment.ndim >= 3 else "ft2"
    direct_phase = params.get("direct_phase")
    scripts: dict[str, str] = {"fid.com": generate_convert_script(experiment)}

    if experiment.sampling.mode is SamplingMode.NUS:
        nus = dict(params.get("nus", {}) or {})
        direct = (0.0, 0.0)
        direct_axis = "F3" if experiment.ndim >= 3 else "F2"
        if direct_phase:
            direct = tuple(direct_phase.get(direct_axis, (0.0, 0.0)))
        kwargs: dict[str, Any] = {
            "in_file": f"{experiment.dataset_id}.fid",
            "baseline": expand_baseline(experiment, params.get("baseline")),
            "nuslist": "nuslist",
            "out_file": f"{experiment.dataset_id}.{out_ext}",
            "nthread": int(nus.get("nthread", 2)),
            "nuslist_count": int(nus.get("nuslist_count", 0)),
            "nsigma": float(nus.get("nsigma", 5.0)),
            "thresh": float(nus.get("thresh", 0.95)),
            "smile_scaling": _as_bool(nus.get("smile_scaling", True)),
            "smile_report": int(nus.get("smile_report", 1)),
            "direct_phase": direct,
            "phases": params.get("phases"),
            "window": params.get("window"),
            "sampling": params.get("sampling"),
            "extract": _as_bool(params.get("extract", True)),
            "ext_lo": str(params.get("ext_lo", "10.5")),
            "ext_hi": str(params.get("ext_hi", "6.5")),
            "direct_poly_time": _as_bool(params.get("direct_poly_time", False)),
            "zero_fill": params.get("zero_fill"),
            "linewidth_hz": params.get("linewidth_hz"),
            "points_per_line": _scalar_points_per_line(
                params.get("points_per_line"), 4.0
            ),
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
            points_per_line=_scalar_points_per_line(
                params.get("points_per_line"), 4.0
            ),
            ext_lo=str(params.get("ext_lo", "10.5")),
            ext_hi=str(params.get("ext_hi", "6.5")),
            extract=_as_bool(params.get("extract", True)),
            window=params.get("window"),
            sampling=params.get("sampling"),
            direct_poly_time=_as_bool(params.get("direct_poly_time", False)),
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
    preview_axis: str | None = None,
    window: dict[str, dict[str, Any]] | None = None,
    keep_complex: bool = False,
) -> str:
    """Indirect-dimension FT finalize script for the NUS reconstruction planes
    (complex; per-dimension PS configurable).

    With a non-empty preview_axis this is complex preview mode: that axis' PS omits
    -di (keeping the real imaginary part for in-memory phase tuning) while the
    other axes add -di from phases, isomorphic to the uniform preview.

    keep_complex (0.2.199-patch18): every PS omits -di, so the final spectrum stays
    fully complex (the imaginary part is kept after the indirect-dimension FT) --
    this serves the direct-dimension phase search "tune the phase on the final
    spectrum" (peaks separated in the frequency domain, avoiding the t1 aliasing
    of the recon planes in the indirect time domain).

    planes: reconstruction-plane input (2D nus2d/recon.ft1; 3D
    nus3d_rc/test%04d.ft1); phases: {axis -> (p0, p1)}, 0 by default -- used by
    the per-dimension phase candidate runs without re-running SMILE; a 2D single
    file uses nmrPipe -in with -out -ov (as in the verified s2.com) and F1 POLY
    is configurable.
    """
    _check_real_modes(experiment)
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
        f2_di = "" if keep_complex or preview_axis == "F2" else " -di"
        f1_di = "" if keep_complex or preview_axis == "F1" else " -di"
        f2_size = _nus_zf_size(zf_plan.get("F2", {}), td[1])
        f1_size = _nus_zf_size(zf_plan.get("F1", {}), td[2])
        f2_window = _window_line((window or {}).get("F2"))
        f1_window = _window_line((window or {}).get("F1"))
        lines = [
            "#!/bin/csh",
            "# NMRForge NUS finalize script (indirect FT from reconstructed planes)",
            f"# experiment: {experiment.dataset_id}",
            f"xyz2pipe -in {planes} -x \\",
            *([f2_window] if f2_window else []),
            *(
                [
                    f"| nmrPipe -fn ZF -size {f2_size} \\"
                ]
                if zf_plan.get("F2", {}).get("mode") != "none"
                else []
            ),
            _ft_flag_line(
                f2_fnmode,
                sampling=sampling,
                axis="F2",
                force_neg=ft_neg_for(experiment, f2_fnmode, "F2"),
            ),
            f"| nmrPipe -fn PS -p0 {f2_p0:g} -p1 {f2_p1:g}{f2_di} \\",
            "| nmrPipe -fn TP \\",
            *([f1_window] if f1_window else []),
            *(
                [
                    f"| nmrPipe -fn ZF -size {f1_size} \\"
                ]
                if zf_plan.get("F1", {}).get("mode") != "none"
                else []
            ),
            _ft_flag_line(f1_fnmode, sampling=sampling, axis="F1"),
            f"| nmrPipe -fn PS -p0 {f1_p0:g} -p1 {f1_p1:g}{f1_di} \\",
            "| nmrPipe -fn TP \\",
            "| nmrPipe -fn ZTP \\",
            f"| pipe2xyz -out {out_file} -x",
        ]
    else:
        f1_p0, f1_p1 = phases.get("F1", (0.0, 0.0))
        f1_di = "" if keep_complex or preview_axis == "F1" else " -di"
        f1_size = _nus_zf_size(zf_plan.get("F1", {}), td[1])
        f1_window = _window_line((window or {}).get("F1"))
        expanded = expand_baseline(experiment, baseline)
        lines = [
            "#!/bin/csh",
            "# NMRForge NUS finalize script (indirect FT from reconstructed planes)",
            f"# experiment: {experiment.dataset_id}",
            f"nmrPipe -in {planes} \\",
            *([f1_window] if f1_window else []),
            *(
                [
                    f"| nmrPipe -fn ZF -size {f1_size} \\"
                ]
                if zf_plan.get("F1", {}).get("mode") != "none"
                else []
            ),
            _ft_flag_line(f1_fnmode, sampling=sampling, axis="F1"),
            f"| nmrPipe -fn PS -p0 {f1_p0:g} -p1 {f1_p1:g}{f1_di} \\",
            *_baseline_line(expanded, "F1"),
            "| nmrPipe -fn TP \\",
            f"  -out {out_file} -ov",
        ]
    return "\n".join(lines) + "\n"


def expand_baseline(
    experiment: Experiment,
    baseline: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Normalise the baseline configuration to {axis: {enabled, mode, order}}.

    Accepts None (auto for every dimension), the form shape
    {enabled,mode,order,axes} and the per-axis shape {axis: {...}}.
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
    """Generate POLY lines from the per-axis configuration (an empty list = off)."""
    cfg = expanded.get(axis) or {}
    if not cfg.get("enabled", True):
        return []
    if str(cfg.get("mode", "auto")) == "order":
        order = max(1, int(cfg.get("order", 1) or 1))
        # Same rule as uniform: a bare -ord N is the identity, so -auto is mandatory
        # (2026-09-16).
        return [f"| nmrPipe -fn POLY -ord {order} -auto \\"]
    return ["| nmrPipe -fn POLY -auto \\"]


def _insert_nus_baseline(
    lines: list[str],
    expanded: dict[str, dict[str, Any]],
    ndim: int,
) -> list[str]:
    """Insert POLY at the designated places in the NUS script: after the
    direct-dimension EXT and after each indirect-dimension PS."""
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
