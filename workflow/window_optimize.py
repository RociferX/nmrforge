"""Window function optimisation: direct dimension + indirect dimension, unified memory scoring
engine (no re-running SMILE/process). User rules (0.2.139) apply to each dimension: 1.
Evaluation only does Fourier in this dimension (window in front of FT), candidates are all
scored in memory on the original FID/reconstruction plane trace; 2. Resolution priority: filter
by FWHM (points) first, and only keep line width <= The candidate of the optimal 1.25x; 3.
Signal-to-noise ratio and linear balance in the standard pool: score = 0.5*snr_norm +
0.5*shape_norm. 4. No window (none) is the first-class candidate: natural decay/FID The fully
sampled axis at the tail should be able to correctly select no window; Need to suppress
truncated ringing/The axis of noise improvement is automatically selected by scoring to select
the appropriate window.. 0.2.190 (restore the true window selection): 0.2.189 The indirect
dimension was hard-coded to be windowless -- that was a misunderstanding of the requirements in
the previous window. The correct behaviour is that the optimizer treats windowless as a
candidate to participate in the scoring, and can correctly select the windowless when the window
is indeed optimal (natural attenuation, apodisation only widens); the direct dimension also
restores 0.5-0.98 and other candidates to participate in the scoring (resolution filtering is
relaxed to 1.25x to avoid excluding the moderate window of user preference in advance). 0.2.192
(added) GM):GM(Lorentz-to-Gauss) formula has been aligned point by point with NMRPipe measured
(0.2.191,k=1/(2*sqrt(ln2))), rejoining the direct dimension default candidate pool (GM g1=8
g2=15). GM/EM Dependence spectrum width SW, score not provided Skip these candidates when SW (to
avoid sw=1.0 values that are garbage and artificially high); the indirect dimension candidate
pool does not add GM -- resolution The limited indirect dimension apodisation signal-to-noise
ratio artificially high will overturn the windowless selection of the natural attenuation axis
(0.2.190 requires retention). The selected configuration is written back to
window[axis](type=none/sine_bell/gaussian/exp etc.), the complete script application is run from
the terminal; any failure downgrade returns to the current configuration and does not block
automatic processing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.data.internal_data_model import Experiment
from ui_support.i18n import tr


@dataclass
class WindowChoice:
    """Candidate window evaluation results."""

    cfg: dict[str, Any]
    label: str
    fwhm: float
    snr: float
    shape: float
    score: float
    selected: bool = False


@dataclass
class WindowOptimizeResult:
    """Uniaxial window optimisation results."""

    choice: dict[str, Any]
    changed: bool
    scores: list[dict[str, Any]] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    optimal_label: str = ""


@dataclass
class MultiWindowOptimizeResult:
    """Multi-indirect dimension window optimisation results: choice = {logical axis:
    configuration}."""

    choice: dict[str, dict[str, Any]]
    changed: bool
    per_axis: dict[str, WindowOptimizeResult] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)


# Direct dimension candidate: user rule (0.2.189)0.5-0.98 is listed as the first choice, and other
# commonly used combinations are compared; c maintains 0.5(NMRPipe SP -c, the memory window model is
# consistent with direct_ft_traces and only models the sin term); gaussian(GM)0.2.192 is added
# (0.2.191 has been measured point by point with NMRPipe Alignment, k=1/(2*sqrt(ln2))), GM/EM
# depends on the spectrum width SW, not provided during scoring SW automatically skipped; indirect
# dimension candidate pool is not added GM (resolution is limited, indirect dimension will be
# overturned due to GM falsely high signal-to-noise ratio without window).
DEFAULT_CANDIDATES: list[dict[str, Any]] = [
    {"type": "sine_bell", "off": 0.50, "end": 0.98, "pow": 2, "c": 0.5},
    {"type": "none"},
    {"type": "sine_bell", "off": 0.30, "end": 0.98, "pow": 1, "c": 0.5},
    {"type": "sine_bell", "off": 0.45, "end": 0.98, "pow": 1, "c": 0.5},
    {"type": "sine_bell", "off": 0.45, "end": 0.90, "pow": 1, "c": 0.5},
    {"type": "sine_bell", "off": 0.45, "end": 0.98, "pow": 2, "c": 0.5},
    {"type": "gaussian", "g1": 8.0, "g2": 15.0, "g3": 0.0, "c": 1.0},
]

# Indirect dimension candidate: No window is listed as the first choice (natural attenuation
# indirect dimension should be able to correctly select no window), and the rest of the same family.
INDIRECT_CANDIDATES: list[dict[str, Any]] = [
    {"type": "none"},
    {"type": "sine_bell", "off": 0.30, "end": 0.98, "pow": 1, "c": 0.5},
    {"type": "sine_bell", "off": 0.45, "end": 0.98, "pow": 1, "c": 0.5},
    {"type": "sine_bell", "off": 0.45, "end": 0.90, "pow": 1, "c": 0.5},
    {"type": "sine_bell", "off": 0.45, "end": 0.98, "pow": 2, "c": 0.5},
    {"type": "sine_bell", "off": 0.50, "end": 0.98, "pow": 2, "c": 0.5},
]

# Resolution priority line width tolerance (1.25x is optimal; 1.15x will exclude the user's
# preferred 0.5-0.98/pow2 mild window in advance, 0.2.190 will relax it).
_RES_TOL = 1.25

# The indirect dimension resolution pool is tighter (1.15x): indirect dimension resolution is
# limited, and apodisation should be correctly dropped without a window when it is only widened.
# After the 0.2.191 window formula is consistent with NMRPipe point by point (SP first point
# multiplied by -c), the false high of the signal-to-noise ratio of apodisation will be amplified by
# the 1.25x pool and overturned. Tightening the pool ensures that the natural attenuation axis is
# deterministic and only leaves no window.
_INDIRECT_RES_TOL = 1.15


def _label(cfg: dict[str, Any]) -> str:
    wtype = str(cfg.get("type", "sine_bell"))
    if wtype == "none":
        return tr("No window (linear)")
    if wtype == "gaussian":
        return f"GM g1={cfg.get('g1', 8.0):g} g2={cfg.get('g2', 15.0):g}"
    if wtype == "exp":
        return f"EM lb={cfg.get('lb', 5.0):g}"
    return (
        f"SP off={cfg.get('off', 0.45):g} end={cfg.get('end', 0.95):g} "
        f"pow={cfg.get('pow', 1):g} c={cfg.get('c', 0.5):g}"
    )


def _window_vector(
    cfg: dict[str, Any], n: int, sw: float = 0.0
) -> np.ndarray:
    """NMRPipe semantic window vector (0.2.191 is consistent with nmrPipe measured/source code
    point by point). Formula source: VM nmrPipe all 1 FID measured + nmrglue pipe_proc/proc_base
    (same semantics as NMRPipe); first point equal multiplication -c(SP script explicit -c 0.5
    Default, GM/EM script is not written -c Press NMRPipe Default 1.0): - SP/sine_bell: w[i] =
    sin(pi*off + pi*(end-off)*i/(n-1))^pow; - GM(Lorentz-to-Gauss): w[i] = exp(pi*g1p*i -
    (k*pi*g2p*(g3*(n-1)-i))^2), k=1/(2*sqrt(ln2))=0.6005612...(VM measured, non-nmrglue's 0.6
    approximation), g1p=g1/SW, g2p=g2/SW(SW As the axis spectrum width Hz, take the fid head
    FDFxSW); - EM: w[i] = exp(-pi*(lb/SW)*i); none/off=all 1."""
    wtype = str(cfg.get("type", "sine_bell"))
    if wtype in ("none", "off"):
        return np.ones(n, dtype=float)
    i = np.arange(n, dtype=float)
    sw_eff = sw if sw > 0.0 else 1.0
    if wtype == "gaussian":
        g1 = float(cfg.get("g1", 0.0))
        g2 = float(cfg.get("g2", 0.0))
        g3 = float(cfg.get("g3", 0.0))
        g1p = g1 / sw_eff
        g2p = g2 / sw_eff
        e = np.pi * i * g1p
        g = (1.0 / (2.0 * np.sqrt(np.log(2.0)))) * np.pi * g2p * (g3 * (n - 1) - i)
        w = np.exp(e - g * g)
        c = float(cfg.get("c", 1.0))
    elif wtype == "exp":
        lb = float(cfg.get("lb", 5.0))
        w = np.exp(-np.pi * (lb / sw_eff) * i)
        c = float(cfg.get("c", 1.0))
    else:
        off = float(cfg.get("off", 0.45))
        end = float(cfg.get("end", 0.95))
        powv = 2 if wtype == "sine_bell_squared" else float(cfg.get("pow", 1))
        w = np.sin(np.pi * off + np.pi * (end - off) * i / max(n - 1, 1)) ** powv
        c = float(cfg.get("c", 0.5))
    w[0] *= c
    return w



def _measure_trace(amp: np.ndarray) -> dict[str, float]:
    """Single trace indicators: resolution (FWHM points), SNR, linear (symmetry + side lobes)."""
    n = amp.size
    if n < 16:
        return {"fwhm": float(n), "snr": 0.0, "shape": 0.0, "ok": False}
    p = int(np.argmax(amp[2 : n - 3])) + 2
    peak = float(amp[p])
    if peak <= 0.0:
        return {"fwhm": float(n), "snr": 0.0, "shape": 0.0, "ok": False}
    half = peak * 0.5
    left = p
    while left > 0 and amp[left] > half:
        left -= 1
    if left > 0:
        frac = (half - amp[left]) / max(amp[left + 1] - amp[left], 1e-12)
        left = left + max(min(frac, 1.0), 0.0)
    right = p
    while right < n - 1 and amp[right] > half:
        right += 1
    if right < n - 1:
        frac = (half - amp[right]) / max(amp[right - 1] - amp[right], 1e-12)
        right = right - max(min(frac, 1.0), 0.0)
    fwhm = max(float(right - left), 1.0)
    lo = max(int(p - 3 * fwhm), 0)
    hi = min(int(p + 3 * fwhm), n)
    noise = amp[list(range(0, lo)) + list(range(hi, n))]
    if noise.size < 4:
        noise = amp[np.concatenate([np.arange(0, max(lo, 4)), np.arange(min(hi, n - 4), n)])]
    med = float(np.median(noise))
    mad = float(np.median(np.abs(noise - med)))
    rms = max(1.4826 * mad, med, np.finfo(float).eps)
    snr = peak / rms
    half_l = float(p - left)
    half_r = float(right - p)
    sym = min(half_l, half_r) / max(max(half_l, half_r), 1e-9)
    span = max(int(fwhm), 2)
    left_sw = amp[max(p - 6 * span, 0) : max(p - 2 * span, 0)]
    right_sw = amp[min(p + 2 * span, n) : min(p + 6 * span, n)]
    swell = 0.0
    if left_sw.size and right_sw.size:
        swell = float(max(np.max(left_sw), np.max(right_sw))) / peak
    shape = sym / (1.0 + 5.0 * max(swell, 0.0))
    return {"fwhm": fwhm, "snr": snr, "shape": shape, "ok": True}


def _aggregate(amp_traces: np.ndarray, cfg: dict[str, Any]) -> dict[str, float]:
    """Take the median index of the energy top trace."""
    per = [_measure_trace(row) for row in amp_traces]
    ok = [m for m in per if m["ok"] and m["snr"] > 3.0]
    if not ok:
        return {"fwhm": float(amp_traces.shape[-1]), "snr": 0.0, "shape": 0.0}
    return {
        "fwhm": float(np.median([m["fwhm"] for m in ok])),
        "snr": float(np.median([m["snr"] for m in ok])),
        "shape": float(np.median([m["shape"] for m in ok])),
    }


def _score_axis(
    arr: np.ndarray,
    axis: int,
    candidates: list[dict[str, Any]],
    *,
    zf_size: int | None = None,
    resolution_penalty: float = 0.0,
    sw: float = 0.0,
    res_tol: float = _RES_TOL,
) -> tuple[list[WindowChoice], WindowChoice | None, str]:
    """Score the candidate window along the specified time axis, returning (choices, optimal, log
    rows). When resolution_penalty>0, an exponential penalty is applied to the broadening of the
    relative optimal FWHM in the standard pool (score *= exp(-k*max(fwhm/min_fwhm-1,0))):
    resolution-limited indirect dimension Use this Let the natural attenuation axis fall
    correctly to no window, and the truncated axis still retains the mild window (0.2.190)."""
    n = arr.shape[axis]
    moved = np.moveaxis(arr, axis, -1)
    flat = moved.reshape(-1, n)
    energy = np.sum(np.abs(flat) ** 2, axis=-1)
    order = np.argsort(energy)[::-1]
    keep = min(max(int(np.ceil(order.size * 0.1)), 4), 12)
    picked = flat[order[:keep]]
    n_zf = zf_size or n
    # GM/EM depends on spectrum width SW; skip when SW is not provided to avoid sw=1.0 numerical
    # garbage virtual high (0.2.192 is required after adding GM).
    sw_dependent = {"gaussian", "exp"}
    skipped_sw = 0
    scorable: list[dict[str, Any]] = []
    for cfg in candidates:
        if str(cfg.get("type", "sine_bell")) in sw_dependent and sw <= 0.0:
            skipped_sw += 1
            continue
        scorable.append(cfg)
    measured: list[WindowChoice] = []
    for cfg in scorable:
        win = _window_vector(cfg, n, sw=sw)
        work = picked * win
        if n_zf > n:
            work = np.pad(work, [(0, 0), (0, n_zf - n)])
        amp = np.abs(np.fft.fft(work, axis=-1))
        # Exclude low-frequency cutoff area and mirror end.
        amp[:, : max(2, n_zf // 80)] = 0.0
        amp[:, n_zf - 3 :] = 0.0
        agg = _aggregate(amp, cfg)
        measured.append(
            WindowChoice(
                cfg=cfg,
                label=_label(cfg),
                fwhm=agg["fwhm"],
                snr=agg["snr"],
                shape=agg["shape"],
                score=0.0,
            )
        )
    valid = [m for m in measured if m.snr > 0.0]
    if not valid:
        return measured, None, tr("trace no valid signal")
    min_fwhm = min(m.fwhm for m in valid)
    pool = [m for m in valid if m.fwhm <= min_fwhm * res_tol] or valid
    max_snr = max(m.snr for m in pool)
    max_shape = max(m.shape for m in pool)
    for m in pool:
        snr_norm = m.snr / max(max_snr, 1e-12)
        shape_norm = m.shape / max(max_shape, 1e-12)
        m.score = 0.5 * snr_norm + 0.5 * shape_norm
        if resolution_penalty > 0.0:
            widen = max(m.fwhm / min_fwhm - 1.0, 0.0)
            m.score *= float(np.exp(-resolution_penalty * widen))
    best = max(pool, key=lambda m: m.score)
    for m in measured:
        m.selected = m is best
    log = (
        tr(
            "optimal {p0} (FWHM {p1:.2f}point, SNR {p2:.1f}, shape {p3:.3f}, score {p4:.3f}); "
            "qualifying pool {p5}/{p6} candidate(s) (resolution >= "
            "{p7:.2f}point)",
            p0=best.label,
            p1=best.fwhm,
            p2=best.snr,
            p3=best.shape,
            p4=best.score,
            p5=len(pool),
            p6=len(measured),
            p7=min_fwhm * res_tol,
        )
    )
    if skipped_sw:
        log += tr(
            "; {p0} spectral width dependent candidates (GM/EM) not provided SW "
            "skip",
            p0=skipped_sw,
        )
    return measured, best, log


def optimize_axis_window(
    arr: np.ndarray,
    axis: int,
    candidates: list[dict[str, Any]] | None = None,
    *,
    zf_size: int | None = None,
    current: dict[str, Any] | None = None,
    axis_label: str = "",
    resolution_penalty: float = 0.0,
    sw: float = 0.0,
    res_tol: float = _RES_TOL,
) -> WindowOptimizeResult:
    """Score candidate windows along arr's axis timeline (shared engine, direct/indirect dimension
    common)."""
    data = np.asarray(arr)
    if data.ndim < 1 or data.shape[axis] < 16:
        return WindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=[tr(
                "{p0}: window optimisation skip: insufficient points in this "
                "axis",
                p0=axis_label,
            )],
        )
    cands = candidates if candidates is not None else DEFAULT_CANDIDATES
    measured, best, message = _score_axis(
        data, axis, cands, zf_size=zf_size,
        resolution_penalty=resolution_penalty,
        sw=sw,
        res_tol=res_tol,
    )
    if best is None:
        return WindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=[tr("{p0}: window optimisation skip:{p1}", p0=axis_label, p1=message)],
        )
    changed = best.cfg != (current or {})
    logs = [tr("{p0}: window(memory score): {p1}", p0=axis_label, p1=message)]
    if not changed:
        logs.append(
            tr(
            "{p0}: Window: The optimal configuration is consistent with the existing configuration "
            "and remains",
            p0=axis_label,
        ))
    return WindowOptimizeResult(
        choice=dict(best.cfg),
        changed=changed,
        scores=[
            {
                "label": m.label,
                "fwhm": m.fwhm,
                "snr": m.snr,
                "shape": m.shape,
                "score": m.score,
                "selected": m.selected,
            }
            for m in measured
        ],
        logs=logs,
        optimal_label=best.label,
    )


def optimize_direct_window(
    fid: np.ndarray,
    *,
    zf_size: int | None = None,
    candidates: list[dict[str, Any]] | None = None,
    current: dict[str, Any] | None = None,
    sw: float = 0.0,
) -> WindowOptimizeResult:
    """Score candidate windows on the original fid direct dimension trace (last axis), returning
    the optimal configuration."""
    return optimize_axis_window(
        fid,
        -1,
        candidates=candidates,
        zf_size=zf_size,
        current=current,
        axis_label=tr("direct dimension"),
        sw=sw,
    )


def optimize_indirect_windows(
    arr: np.ndarray,
    axis_map: dict[str, int],
    *,
    candidates: list[dict[str, Any]] | None = None,
    current: dict[str, dict[str, Any]] | None = None,
    zf_mult: float = 2.0,
    sw_map: dict[str, float] | None = None,
) -> MultiWindowOptimizeResult:
    """Score the candidate window for each indirect dimension timeline (including no window), and
    write back the optimal value for each axis. axis_map: Logical axis -> Time axis index in
    array (the remaining axes in array can be time or frequency, which does not affect the axis-
    by-axis scoring). zf_mult: Use zero-filled multipliers for scoring (the number of indirect
    dimension points is small, and 2x improves FWHM resolution)."""
    data = np.asarray(arr)
    cands = candidates if candidates is not None else INDIRECT_CANDIDATES
    current = dict(current or {})
    choice: dict[str, dict[str, Any]] = dict(current)
    per_axis: dict[str, WindowOptimizeResult] = {}
    logs: list[str] = []
    changed = False
    for axis_name, axis in axis_map.items():
        if axis >= data.ndim or data.shape[axis] < 16:
            logs.append(
                tr(
                "{p0}: indirect dimension window optimisation skip: insufficient "
                "points",
                p0=axis_name,
            ))
            continue
        n = int(data.shape[axis])
        zf_size = max(int(round(n * max(zf_mult, 1.0))), n)
        res = optimize_axis_window(
            data,
            axis,
            cands,
            zf_size=zf_size,
            current=current.get(axis_name),
            axis_label=tr("{p0}(indirect dimension)", p0=axis_name),
            resolution_penalty=1.0,
            sw=(sw_map or {}).get(axis_name, 0.0),
            res_tol=_INDIRECT_RES_TOL,
        )
        per_axis[axis_name] = res
        logs += res.logs
        if res.changed:
            choice[axis_name] = dict(res.choice)
            changed = True
    if not per_axis:
        logs.append(tr("indirect dimension window optimisation skip: no timeline available"))
    return MultiWindowOptimizeResult(
        choice=choice, changed=changed, per_axis=per_axis, logs=logs
    )

def _fid_paths(work: Path, experiment: Experiment) -> list[Path]:
    """The converted fid path: slice stream (fid/test*.fid) or single file (dataset.fid), has the
    same semantics as workflow.direct_diagnostics._collect_fid_paths (0.2.163-patch6: 3D
    uniform/NUS is a slice stream)."""
    if experiment.segments:
        for base in (work / "merged", work):
            d = base / "fid"
            if d.is_dir():
                fs = sorted(d.glob("test*.fid"))
                if fs:
                    return fs
    d2 = work / "fid"
    if d2.is_dir():
        fs = sorted(d2.glob("test*.fid"))
        if fs:
            return fs
    single = work / f"{experiment.dataset_id}.fid"
    if single.is_file():
        return [single]
    return sorted(work.glob("test*.fid"))


def _load_fid(work: Path, experiment: Experiment) -> tuple[np.ndarray, dict] | None:
    """Load the converted fid (single file or 3D slice stream stack) + header (take the first file
    dic)."""
    paths = _fid_paths(work, experiment)
    if not paths:
        return None
    import nmrglue as ng

    arrays: list[np.ndarray] = []
    dic: dict = {}
    for index, path in enumerate(paths):
        d, fid = ng.pipe.read(str(path))
        if index == 0:
            dic = d
        arrays.append(fid)
    fid = arrays[0] if len(arrays) == 1 else np.concatenate(arrays, axis=0)
    return fid, dic



def _load_recon_planes(
    work: Path, experiment: Experiment
) -> tuple[np.ndarray, dict] | None:
    """Load SMILE reconstruction plane (indirect dimension time domain) + head.
    0.2.199-patch29(measured sampleB + sampleJ manual slicing): 3D nus3d_rc/test%04d.ft1 Each
    file = one direct dimension (F3 frequency) point, the plane array is (F1 time, F2 time): 13C
    axis is hypercomplex 4 x TD (300 real), 15N axis is States real data (TD). Window function
    scoring must act on the original real axis consistently with the backend (SP acts directly
    on this axis), so simple axis 0 interleaved unpacking is not possible (will unpack the
    hypercomplex data incorrectly); after stacking (F1, F2, F3). Read only the first plane
    header FDFILECOUNT planes to avoid stale test*.ft1 mixing in (patch29). 2D nus2d/recon.ft1
    Single file (F2 frequency, F1 time), F1 complex is in the last axis -- nmrglue has been
    directly read as the complex data (F2, F1) complex, and cannot be used to split axis 0 with
    read_pipe_complex (it will cut the direct dimension in half; 0.2.199-patch29b demonstrates
    the 2D recon made by sampleF)."""
    import nmrglue as ng

    from core.data.pipe_io import read_pipe_complex

    if experiment.ndim >= 3:
        plane_dir = work / "nus3d_rc"
        paths = sorted(plane_dir.glob("test*.ft1"))
        if not paths:
            return None
        dic: dict = {}
        arrays: list[np.ndarray] = []
        count: int | None = None
        for index, path in enumerate(paths):
            d, raw = ng.pipe.read(str(path))
            if index == 0:
                dic = d
                try:
                    count = int(float(dic.get("FDFILECOUNT") or 0))
                except (TypeError, ValueError):
                    count = None
            if count is not None and len(arrays) >= count:
                break
            arrays.append(np.asarray(raw))
        if not arrays:
            return None
        return np.stack(arrays, axis=-1), dic
    recon = work / "nus2d" / "recon.ft1"
    if not recon.is_file():
        return None
    dic, raw = ng.pipe.read(str(recon))
    arr = np.asarray(raw)
    if np.iscomplexobj(arr):
        planes: np.ndarray = arr.astype(np.complex128)
    else:
        planes = read_pipe_complex(recon)
    return planes, dic



def _uniform_axis_map(experiment: Experiment) -> dict[str, int]:
    """Uniform fid layout: 2D (F1, F2), 3D (F1, F2, F3), indirect dimension takes the axis
    according to internal convention."""
    from core.data.internal_data_model import AxisRole
    from core.processing.axes import axis_index

    return {
        dim.logical_axis: axis_index(dim.logical_axis, experiment.ndim)
        for dim in experiment.dimensions
        if dim.role is not AxisRole.DIRECT
    }


def _nus_axis_map(experiment: Experiment) -> dict[str, int]:
    """NUS reconstruction plane layout (0.2.199-patch29 correction): 2D (F2 frequency, F1) -> F1=1;
    3D stack (F1, F2, F3) -> F1=0, F2=1. Old code F1=2 pointed to the direct dimension axis."""
    if experiment.ndim >= 3:
        return {"F1": 0, "F2": 1}
    return {"F1": 1}


def _axis_sw(
    dic: dict[str, Any], axis: str, experiment: Experiment | None = None
) -> float:
    """From fid/Take the logical axis spectrum width of the plane head(SW Hz). 0.2.199-patch29:
    Prioritize the head core label (FDF{n}LABEL) to match the logical axis core (3D head
    FDF1=15N/FDF2=1H/FDF3=13C, which is different from the logical F2/F3/F1. If you choose by
    numerical suffix, you will get the wrong axis SW); when the experiment is unknown, fall back
    to the numerical suffix (2D) uniform header has the same number as the logic)."""
    if experiment is not None:
        dim = next(
            (d for d in experiment.dimensions if d.logical_axis == axis), None
        )
        nucleus = (dim.nucleus or "").strip() if dim is not None else ""
        # Header LABEL is "15N", Bruker NUC1 is "<15N>", leaving only alphanumeric comparisons.
        norm = lambda v: "".join(ch for ch in v if ch.isalnum())  # noqa: E731
        if nucleus:
            for i in (1, 2, 3):
                label = str(dic.get(f"FDF{i}LABEL") or "").strip()
                if norm(label) == norm(nucleus):
                    try:
                        return float(dic.get(f"FDF{i}SW") or 0.0)
                    except (TypeError, ValueError):
                        return 0.0
    suffix = axis[1:] if axis.startswith("F") else axis
    try:
        return float(dic.get(f"FDF{suffix}SW") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def optimize_direct_window_from_work(
    work_dir: Path | str,
    experiment: Experiment,
    *,
    current: dict[str, Any] | None = None,
    zf_size: int | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> WindowOptimizeResult:
    """Load and optimise the direct dimension window from the converted fid (work directory,
    support slice stream), without re-running SMILE/process. SW (spectrum width) is read from
    the fid header for GM/EM accurate modeling."""
    work = Path(work_dir)
    try:
        loaded = _load_fid(work, experiment)
    except Exception as exc:  # noqa: BLE001
        return WindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=[tr("direct dimension window optimisation failed (read fid): {p0}", p0=exc)],
        )
    if loaded is None:
        return WindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=[tr("direct dimension window optimisation skipped: converted.fid not found")],
        )
    fid, dic = loaded
    direct_axis = f"F{experiment.ndim}"
    return optimize_direct_window(
        fid,
        zf_size=zf_size,
        candidates=candidates,
        current=current,
        sw=_axis_sw(dic, direct_axis, experiment),
    )



def optimize_indirect_windows_from_work(
    work_dir: Path | str,
    experiment: Experiment,
    *,
    current: dict[str, dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> MultiWindowOptimizeResult:
    """After conversion, fid optimizes uniform each indirect dimension window (memory scoring, no
    rerun process). Each axis SW is read from the fid header for GM/EM to accurately model."""
    work = Path(work_dir)
    try:
        loaded = _load_fid(work, experiment)
    except Exception as exc:  # noqa: BLE001
        return MultiWindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=[tr("indirect dimension window optimisation failed (reading fid): {p0}", p0=exc)],
        )
    if loaded is None:
        return MultiWindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=[tr("indirect dimension window optimisation skipped: converted.fid not found")],
        )
    fid, dic = loaded
    axis_map = _uniform_axis_map(experiment)
    sw_map = {axis: _axis_sw(dic, axis, experiment) for axis in axis_map}
    return optimize_indirect_windows(
        fid, axis_map, candidates=candidates, current=current, sw_map=sw_map
    )



def optimize_indirect_windows_from_recon(
    work_dir: Path | str,
    experiment: Experiment,
    *,
    current: dict[str, dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> MultiWindowOptimizeResult:
    """Reconstruct planes from SMILE to optimise NUS for each indirect dimension window (memory
    scoring, without rerunning SMILE). Each axis SW is read from the plane head for GM/EM
    accurate modeling."""
    work = Path(work_dir)
    try:
        loaded = _load_recon_planes(work, experiment)
    except Exception as exc:  # noqa: BLE001
        return MultiWindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=[(
                tr(
                "Indirect dimension window optimisation failed (reading reconstruction plane): "
                "{p0}",
                p0=exc,
            )
            )],
        )
    if loaded is None:
        return MultiWindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=[(
                tr(
                "indirect dimension window optimisation skip: not found SMILE reconstruction "
                "plane",
            )
            )],
        )
    planes, dic = loaded
    axis_map = _nus_axis_map(experiment)
    sw_map = {axis: _axis_sw(dic, axis, experiment) for axis in axis_map}
    return optimize_indirect_windows(
        planes, axis_map, candidates=candidates, current=current, sw_map=sw_map
    )



__all__ = [
    "DEFAULT_CANDIDATES",
    "INDIRECT_CANDIDATES",
    "MultiWindowOptimizeResult",
    "WindowChoice",
    "WindowOptimizeResult",
    "optimize_axis_window",
    "optimize_direct_window",
    "optimize_direct_window_from_work",
    "optimize_indirect_windows",
    "optimize_indirect_windows_from_recon",
    "optimize_indirect_windows_from_work",
]
