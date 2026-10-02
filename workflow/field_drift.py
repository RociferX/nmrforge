"""Inter-part field-drift detection and conversion-time correction for multi-part FIDs
(2026-09-23).

Segmented acquisition / repeated-experiment averaging (repeat_uniform / repeat_nus) splits one
experiment into several fids by acquisition time; if the parts drift in frequency (field drift)
before they are merged, the point-by-point time-domain summation of addNMR broadens or even
splits
the peaks - which is exactly how "acquire a few more parts to raise the SNR" gets ruined (the
lab
1stfid.com + 2ndAdd.com flow aligned the parts by hand; this module automates it).

The rules the user locked on 2026-09-23:

- reference = **part 1**; every part's frequency shift is measured against the reference;
- **the criterion is "drift vs line width"** (revised 2026-09-24, user: "if the line-width
  convention is more reasonable, change it"): act only when |Δ| exceeds
  ``max(DRIFT_HZ_MIN, IMPACT_FRACTION × line width)`` - a relative broadening
  ``Var(δ)/(2W²)`` of more than 2% counts as a measurable effect on the line shape (see
  "theory ②"). The old convention tied "correct or not" to the digital resolution through the
  FFT
  point (d_018: point width 66.9 Hz ≈ half a line width), which is not the same thing as a
  physical
  criterion; the point width is still measured and recorded, but serves only as a "can it be
  measured" reference in the report. When the line width cannot be measured (direct dimension
  too
  short / all rows bad) it falls back to ``max(hz_min, points_min × point width)``. ppm is still
  measured and recorded (:data:`DRIFT_PPM_THRESHOLD` is record-only);
- **pairing by physical trace index**: the row indices of the high-energy traces are chosen once
  over all parts (:func:`pool_trace_rows`) - letting each part pick its own traces by energy
  would
  pair the i-th high-energy trace with a different indirect-dimension plane of another part;
- **part consistency**: before merging, compare NS/TD/DS/SW/O1/SFO1/GRPDLY/FnMODE/PULPROG part
by
  part (:func:`check_segment_consistency`); differing TD/SW/O1 etc. = **refuse to merge** (not
  the
  same acquisition parameters), while a differing NS only warns - it is not a weighting issue
  (see
  "theory ①");
- the correction is written into **that part's fid.com**: ``| nmrPipe -fn PS -rs <Δ>Hz \\`` is
  inserted before ``| nmrPipe -fn MULT -c …`` (``MULT -c`` is left alone) and the part is
  re-converted;
- the residual is re-checked after re-conversion, at most :data:`MAX_ROUNDS` rounds; still over
the
  criterion is reported, never escalated. **Consistency rule**: either all parts over the
  criterion
  are corrected or none of them - merging with one part left uncorrected is worse than not
  correcting at all; the caller (backend) executes all-or-nothing and rolls back to "correct
  none"
  on a pre-check or mid-run failure.


Theory (2026-09-24, user: "I asked you to work out the theory, not to fit the data")
------------------------------------------------------------------
The three points below follow from physics and statistics alone, independent of any particular
dataset; the criterion and the report wording may only speak according to them.

**① Adding FIDs directly is the optimal combination - so a differing NS is not a "weighting
issue".**
The converted fid of part i is ``S_i·s(t) + n_i``: the coherently summed amplitude is
``S_i ∝ NS_i`` and the incoherently summed noise is ``σ_i² ∝ NS_i``. Summing with weights
``w_i``,
the matched-filter weight is ``w_i ∝ S_i/σ_i² = constant`` - **equal-weight summation is the
optimal combination for this data**, with zero SNR loss relative to optimal weighting. A
differing
NS only means the parts were not acquired equally long (the per-trace noise differs by
``1/√NS``,
and the signal differs by the same factor), not that the weighting is wrong.
What really makes equal-weight summation suboptimal is a **different noise scale per scan**
(when
the receiver gain ``RG``, the digital filter/decimation ``DS`` or other acquisition-chain
settings
differ, ``σ_i²/NS_i`` is no longer equal). So what has to be compared before merging is whether
the
parts share one and the same set of acquisition parameters, which has nothing to do with an NS
difference.

**② Whether to correct depends on "drift vs line width", not on "drift vs points".**
With a rigid offset ``δ_i`` per part, the line shape of the merged spectrum is ``Σ_i
L(f−δ_i)/n``:
the second moment is ``W_eff² = W² + Var(δ)``, i.e. a **relative broadening ≈ Var(δ)/(2W²)** and
a
peak position moved by ``mean(δ)``. So for ``δ ≪ W`` there is **no measurable effect** on the
line
shape and correcting it only moves the data by the estimation noise; only when ``δ`` is of the
same
order as ``W`` is the distortion real and must be corrected. **The criterion is therefore**
``max(DRIFT_HZ_MIN, IMPACT_FRACTION × W)`` (IMPACT_FRACTION = 0.2 ⇒ relative broadening ≤ 2%).
The
digital resolution (point width = direct-dimension SW / complex points) goes into the report
only
as a "can it be measured" reference - it is **not** the physical criterion for "should it be
corrected" (2026-09-24, user: "if the line-width convention is more reasonable, change it"). The
line width is measured by :func:`direct_linewidth_hz`: the **point-by-point median of the
magnitude
spectra** of the top traces, minus that spectrum's own median floor, then the full width at half
maximum; when the line width cannot be measured it falls back to the old convention
``max(hz_min, points × point width)``.

**③ "Measurable" is judged by the coherent statistic against a scrambled-pairing control;
"not measurable" does not mean "the data are not from the same source".**
The matched filter has two ways to sum the scan curves: for the same physical trace the product
``part·conj(ref)`` has the same phase (the inter-part indirect-dimension phase cancels in the
product), so **summing the complex values first and then taking the squared magnitude**
(coherent)
lets the common signal stack with the number of traces; taking the squared magnitude per trace
and
then summing (incoherent, the old 2026-09-23 convention) is dominated by the **noise×noise**
term
at low SNR - on d_018 the measured correlation peak was only 1.4× the floor and the per-trace
estimates differed by 39-151 Hz, so the measured "drift" was all noise (it describes whether
this
measurement can separate the signal, not a judgement about the origin of the data). Take the
incoherent scan as the **noise ruler of this measurement** (it does not grow with the number of
traces while the signal phases are scattered); the detection statistic =
``max(coherent scan) / median(incoherent scan)``: with no signal it is set only by the
extreme-value
fluctuation over the search grid (1-8 in both synthetic and real-machine measurements,
independent
of the number of traces), and with full coherence its upper bound is the number of traces.
**Scrambling the pairing** (shifting the row numbers between parts, which destroys the common
signal while leaving the noise level, the trace count and the grid unchanged) gives the control
``null``; "measurable" requires ``stat ≥ max(COHERENT_STAT_MIN, COHERENT_NULL_MARGIN × null)``.
Add one more **stability** threshold: the scatter of random half-split resampling
(``uncertainty_hz``) must not exceed the criterion - a "drift value" whose uncertainty is larger
than the criterion is not actionable. If it is not measurable, only report and do not correct,
and
the wording may only describe **whether this measurement can separate the signal**. To judge
whether the parts come from the same source, look at **part consistency** (whether
TD/SW/O1/SFO1/GRPDLY/FnMODE/PULPROG are equal).

Estimator: matched filter (it replaced "strongest-peak position difference" on 2026-09-23). For
a
rigid frequency shift Δf the per-trace product ``part·conj(reference)`` is
``|ref|²·exp(+i2πΔf·t)``; scanning that product over Δf within ±max_ppm gives the peak position
as
Δf (coherent summation, see "theory ③"). **Why not the strongest-peak position difference**:
with
several peaks or low SNR the strongest peak hops lines - on the VM the three synthetic parts of
d_015 (a few Hz apart) gave +320/+350/+450 Hz with the peak-position method (it had hopped to a
neighbouring line) while the matched filter gave -2.9/-7.0 Hz, consistent with the true
relation; a
hop silently moves a whole part by hundreds of Hz.


Sign convention (measured on the real machine 2026-09-23, VM d_015): ``-rs``/``-ls`` of
``nmrPipe -fn PS`` are a **time-domain** frequency shift (``nmrPipe -fn PS -help`` writes
"Time-Domain Phase Correction for Freq Shift"), applied to the converted time-domain fid.
Measured: ``PS -rs 30Hz`` moved the direct-dimension peak from point 141 to point 139 (sweep
width
16129.032 Hz / 806 complex points = 20.01 Hz/point, i.e. -30 Hz), and ``PS -ls 30Hz`` the other
way
by +1 point. The "this part sits ΔHz above the reference" value measured here is exactly the
number
to write into ``PS -rs``: a positive value pulls that part's peak down (to lower frequency) back
onto the reference; the reference part itself gets nothing (value 0). The sign of the matched
filter was calibrated the same way against a known ``PS -rs 30Hz`` copy: -30.00 Hz.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.data.internal_data_model import Experiment
from core.experiment.bruker_parser import parse_dataset_params
from core.project.manager import atomic_write_text
from ui_support.i18n import tr
from workflow.direct_diagnostics import _read_fid_raw

#: Criterion fallback (since 2026-09-24 the main criterion is "drift vs line width", see
#: IMPACT_FRACTION): judge by FFT points only when the line width cannot be measured, and do not
#: act while |Δ| is below that many points. Point width = direct-dimension SW / complex points.
DRIFT_POINTS_MIN = 1.0
#: Absolute lower bound of the criterion (Hz): under any convention |Δ| must exceed it to act.
DRIFT_HZ_MIN = 1.5
#: ppm reference for the record/display only; it does not take part in the criterion.
DRIFT_PPM_THRESHOLD = 0.005
#: Per-part correction limit: a larger ppm is treated as the estimate running to the search
#: boundary (mistracked peak / noise push); report only, never rewrite
MAX_PPM_SHIFT = 0.5
#: Maximum re-conversion rounds per part (round 1 corrects, round 2 re-checks/refines; the real
#: residual is measured again afterwards)
MAX_ROUNDS = 2
#: Number of high-energy traces taking part in the estimate (the top ones by trace energy)
TOP_FRACTION = 0.05
TOP_MIN = 4
TOP_MAX = 20
SHIFT_STEP_HZ = 0.25
FINE_HALF_WIDTH_HZ = 0.50
FINE_STEP_HZ = 0.01
PARABOLIC_REFINEMENT = True
TIME_FRACTION = 0.60
STABILITY_TIME_FRACTIONS = (0.50, 0.60, 0.70)
TIME_FRACTION_SPREAD_HZ = 0.5
COHERENT_STAT_MIN = 10.0
#: "Measurable" criterion: the coherent statistic must also exceed the **scrambled-pairing**
#: control by this factor (the data's own noise is the ruler)
COHERENT_NULL_MARGIN = 1.25
#: Number of draws for the "measurable" control (random phase per trace; the maximum of them is
#: the control, and more draws make the control stricter)
NULL_DRAWS = 5
#: Stability-threshold sampling: draw m/2 traces at random and repeat the estimate
#: STABILITY_DRAWS times; uncertainty = (max−min)/2 (±,Hz). **A single even/odd split is not
#: enough** - measured on the real machine with d_018: the even/odd split gave −32/−27 Hz while 10
#: random splits scattered over −190…+140 Hz (the rows come in blocks along the slices, so even/odd
#: happen to land on the same side) ⇒ a single split reports a false "stable" and the data are moved
#: on that basis.
STABILITY_DRAWS = 6
#: Random seed shared by the control and the stability sampling (fixed → the same measurement is
#: reproducible and the record can be re-checked)
NULL_SEED = 20260924
STABILITY_SEED = 20260924
#: Impact criterion: the **variance** of the per-part rigid offsets must exceed 2% of the line width
#: to have a measurable effect on the line shape (relative broadening
#: = Var(δ)/(2W²) ≤ 2% ⇒ |δ| ≤ 0.2·W). The criterion is max(DRIFT_HZ_MIN, that value).
IMPACT_FRACTION = 0.2
#: Number of top traces used for the line-width estimate: the magnitude spectra are first taken as
#: a point-by-point median over the traces, and more traces make the noise floor flatter
LINEWIDTH_TRACES = 256
#: Upper limit on the number of traces used by the pooled (coherent) estimate: the SNR of the
#: coherent sum grows with the trace count and memory/time follow it, so the cut only caps the cost
#: of a single check (on the real machine 1368 traces still take only the first 512)
POOL_MAX_TRACES = 512

#: Name of the field-drift record file carried in the conversion provenance (same directory as fid)
FIELD_DRIFT_FILENAME = "field_drift.json"
#:
BLOCKING_PARAM_KEYS = (
    "TD",
    "SW_h",
    "O1",
    "SFO1",
    "GRPDLY",
    "FnMODE",
    "PULPROG",
    "DECIM",
    "DSPFVS",
)
#:
#:
BLOCKING_PARAM_KEYS_NUS = (
    "NusTD",
    "SW_h",
    "O1",
    "SFO1",
    "GRPDLY",
    "FnMODE",
    "PULPROG",
    "DECIM",
    "DSPFVS",
)
WARNING_PARAM_KEYS = ("NS", "DS", "RG")
#: Parameter files covered by the consistency comparison (direct-dimension acqus + indirect
#: acqu2s/acqu3s)
CONSISTENCY_FILES = ("acqus", "acqu2s", "acqu3s")


def pool_trace_rows(
    planes: Sequence[np.ndarray | None], *, cap: int = POOL_MAX_TRACES
) -> np.ndarray:
    """Row indices for the pooled estimate: traces finite in every part (top ``cap`` by energy).

    Pairing follows the **physical trace index** (only the same row across the parts is the same
    trace; letting each part pick its own traces by energy would pair the i-th high-energy trace
    with a different indirect-dimension plane of another part). The SNR of the coherent sum
    grows
    with the trace count, so use as many as possible - ``cap`` only caps the cost of a single
    check
    (on the real machine 1368 traces still take only the first :data:`POOL_MAX_TRACES`).
    """
    usable = [p for p in planes if p is not None]
    if not usable:
        return np.zeros(0, dtype=int)
    rows = min(int(p.shape[0]) for p in usable)
    if rows <= 0:
        return np.zeros(0, dtype=int)
    common = np.ones(rows, dtype=bool)
    total = np.zeros(rows, dtype=float)
    for plane in usable:
        block = plane[:rows]
        finite = _finite_rows(block)
        common &= finite
        if finite.any():
            total += np.sum(np.where(finite[:, None], np.abs(block) ** 2, 0.0), axis=-1)
    if not common.any():
        return np.zeros(0, dtype=int)
    # 2026-09-24 review D: the contract is "traces finite in every part" - non-common rows must be
    # **excluded**; previously their energy was only written as -1 (sorted last), so with
    # cap >= rows they were still included and the "comparable trace count" came out too high
    usable_rows = np.flatnonzero(common)
    order = usable_rows[np.argsort(total[usable_rows])[::-1]]
    return np.sort(order[: max(1, min(int(cap), usable_rows.size))])


@dataclass
class PooledShiftEstimate:
    """Everything behind "is it measurable": pooled estimate, control, half-split scatter."""

    shift_hz: float
    stat: float
    null_stat: float
    uncertainty_hz: float | None = None
    traces: int = 0
    span_hz: float | None = None
    resolution_hz: float | None = None
    time_fraction_shifts: dict[float, float] | None = None
    time_fraction_spread_hz: float | None = None
    stable: bool | None = None

    @property
    def significant(self) -> bool:
        """Whether the coherent statistic stands above the scrambled-pairing control."""
        floor = min(COHERENT_STAT_MIN, max(2.0, self.traces / 2.0))
        return self.stat >= max(floor, COHERENT_NULL_MARGIN * self.null_stat)


def _time_fraction_rows(
    reference: np.ndarray, part: np.ndarray, fraction: float
) -> tuple[np.ndarray, np.ndarray]:
    """Keep the first ``fraction`` of each trace, where signal-to-noise is higher.

    The tail is often noise-dominated after the signal has decayed, so including it adds noise
    to ``C(Δf)``. Do not truncate too aggressively: a shorter rectangular time window broadens
    the correlation peak and reduces frequency resolution. The default fraction is 0.60
    (:data:`TIME_FRACTION`), with 0.5–0.8 supported. Keep at least eight points.
    """
    span = int(reference.shape[-1])
    keep = max(8, min(span, int(round(span * float(fraction)))))
    return reference[..., :keep], part[..., :keep]


def _normalize_amplitude(rows: np.ndarray) -> np.ndarray:
    """Normalize each trace's amplitude before coherent combination.

    The coherent correlation ``Σ s₁* s₂ e^{-i2πΔf t}`` scales with the product of the two
    trace amplitudes, so different receiver gain, concentration or scan counts can rescale the
    peak. The peak-to-floor statistic is invariant to a single global scale, but amplitude
    differences between traces make coherent summation depart from a matched filter: strong
    traces dominate while weak traces contribute little. Dividing each trace by its norm gives
    traces equal weight, as required for coherent accumulation to gain signal-to-noise with the
    number of traces.
    """
    norm = np.sqrt(np.sum(np.abs(rows) ** 2, axis=-1, keepdims=True))
    safe = np.where(norm > 0, norm, 1.0)
    return rows / safe


def _coherent_product(reference: np.ndarray, part: np.ndarray) -> np.ndarray:
    """Return the per-trace product ``part * conj(reference)`` after amplitude normalization."""
    return _normalize_amplitude(part) * np.conj(_normalize_amplitude(reference))


def _coarse_fine_shift(
    product: np.ndarray,
    sw_hz: float,
    *,
    span_hz: float,
    coarse_step_hz: float,
    fine_half_width_hz: float,
    fine_step_hz: float,
    refine: bool,
) -> tuple[float, float]:
    """Find ``argmax C(Δf)`` with a coarse scan followed by a local fine scan.

    The coarse scan brackets the peak to a grid point, then the fine scan covers
    ``f_c ± fine_half_width``. Parabolic interpolation is only a final refinement: if it fails,
    ``fine_step_hz`` still provides the guaranteed grid resolution. Return the offset in Hz and
    the maximum peak height from the fine-scan curve.
    """
    coarse = _scan_curves(product, sw_hz, span_hz=span_hz, step_hz=coarse_step_hz, coherent=True)
    if coarse.size == 0:
        return float("nan"), float("nan")
    index = int(np.argmax(coarse))
    center = float(index) * coarse_step_hz - float(span_hz)

    fine_half = max(float(fine_half_width_hz), float(fine_step_hz))
    lo = max(-float(span_hz), center - fine_half)
    hi = min(float(span_hz), center + fine_half)
    if hi <= lo:
        return center, float(coarse[index])
    steps = max(1, int(round((hi - lo) / float(fine_step_hz))))
    grid = lo + np.arange(steps + 1) * float(fine_step_hz)
    time = np.arange(product.shape[-1], dtype=float)
    block = product @ np.exp(-2j * np.pi * np.outer(grid / sw_hz, time)).T
    curve = np.abs(block.sum(axis=0)) ** 2
    best = int(np.argmax(curve))
    shift = float(grid[best])
    peak = float(curve[best])
    if refine and 0 < best < curve.size - 1:
        left, middle, right = curve[best - 1], curve[best], curve[best + 1]
        denom = left - 2.0 * middle + right
        if denom:
            offset = float(np.clip(0.5 * (left - right) / denom, -0.5, 0.5))
            shift += offset * float(fine_step_hz)
    return shift, peak


def _scan_curves(
    product: np.ndarray,
    sw_hz: float,
    *,
    span_hz: float,
    step_hz: float,
    coherent: bool,
) -> np.ndarray:
    """Scan curve of the product over δ (chunked; memory blocked by grid points × traces).

    Coherent (sum the complex values first, then take the squared magnitude - the more traces
    are
    summed the higher the SNR) or incoherent (take the squared magnitude per trace and then sum,
    which grows only as ``√m`` once the signal phases are scattered - so it can serve as the
    **noise
    ruler**, see "theory ③" at the top of the module).
    """
    time = np.arange(product.shape[-1], dtype=float)
    grid_size = int(2 * span_hz / step_hz) + 1
    chunk = max(1, int(2_000_000 // max(product.shape[-1], 1)))
    total = np.zeros(grid_size, dtype=float)
    for start in range(0, grid_size, chunk):
        stop = min(start + chunk, grid_size)
        piece = (np.arange(start, stop) * step_hz - span_hz) / sw_hz
        block = product @ np.exp(-2j * np.pi * np.outer(piece, time)).T
        if coherent:
            total[start:stop] = np.abs(block.sum(axis=0)) ** 2
        else:
            total[start:stop] = np.sum(np.abs(block) ** 2, axis=0)
    return total


def _scrambled_null(
    product: np.ndarray,
    sw_hz: float,
    *,
    span_hz: float,
    step_hz: float,
    baseline: float,
    draws: int = NULL_DRAWS,
    seed: int = NULL_SEED,
) -> float:
    """Control statistic from scrambling the **per-trace phase**.

    How high this statistic can climb when there is no common signal: the phase of the product
    (the
    per-trace product) is the whole information source of the criterion. Under H0 (the two parts
    share no signal) it is random to begin with, so randomising it does not change the
    distribution;
    under H1 (a common signal) it is identical across traces, and randomising scrambles it. The
    control is therefore = multiply each row of the product by ``e^{iθ_i}`` (θ independent and
    uniform) and compute the coherent peak/floor again - exactly the same scale as the data's
    own
    noise level, trace count and search grid (the floor is unchanged, because ``|e^{iθ}p| =
    |p|``).

    **Shifting the row numbers between parts cannot do this**: the indirect-dimension phase is a
    deterministic function of the row number, so shifting whole rows only adds one constant
    phase to
    all the products (on single-peak or regularly sampled data the "control" is as high as the
    observation and the control fails). Random phases hold for any phase structure, and only
    **one**
    combined trace has to be scanned once (``m`` times cheaper than scanning the whole batch).
    """
    rows = int(product.shape[0])
    if rows < 2 or not math.isfinite(baseline) or baseline <= 0:
        return float("nan")
    rng = np.random.default_rng(seed)
    best = 0.0
    for _ in range(max(1, int(draws))):
        phases = np.exp(2j * np.pi * rng.random(rows))
        combined = (product * phases[:, None]).sum(axis=0)[None, :]
        total = _scan_curves(combined, sw_hz, span_hz=span_hz, step_hz=step_hz, coherent=True)
        best = max(best, float(total.max()) / baseline)
    return best


def _scan_shift_stat(
    coherent: np.ndarray, incoherent: np.ndarray, *, span_hz: float, step_hz: float
) -> tuple[float, float]:
    """(peak position Hz, coherent statistic); ``(nan, nan)`` if the curve is too small or the
    floor is not positive.

    The peak position is refined with a three-point parabola; the statistic = coherent peak /
    incoherent floor (median).
    """
    if coherent.size < 3:
        return float("nan"), float("nan")
    baseline = float(np.median(incoherent))
    if not math.isfinite(baseline) or baseline <= 0:
        return float("nan"), float("nan")
    index = int(np.argmax(coherent))
    offset = 0.0
    if 0 < index < coherent.size - 1:
        left, center, right = coherent[index - 1], coherent[index], coherent[index + 1]
        denom = left - 2.0 * center + right
        if denom:
            offset = float(np.clip(0.5 * (left - right) / denom, -0.5, 0.5))
    shift = float(index + offset) * step_hz - span_hz
    return shift, float(coherent[index] / baseline)


def _resampled_uncertainty(
    product: np.ndarray,
    sw_hz: float,
    *,
    span_hz: float,
    step_hz: float,
    rows: int,
    fine_half_width_hz: float = FINE_HALF_WIDTH_HZ,
    fine_step_hz: float = FINE_STEP_HZ,
    refine: bool = PARABOLIC_REFINEMENT,
) -> float | None:
    """Uncertainty of the estimate itself (±,Hz): draw half the traces at random, repeat the
    estimate, and take (max−min)/2.

    See the note on :data:`STABILITY_DRAWS`: a single even/odd split **cannot** stand for the
    uncertainty - the rows come in blocks along the slices, so even/odd land on the same side
    and
    report a false "stable", and moving data on that basis carries noise off as if it were
    signal.
    Returns None when there are too few traces (``< 8``) or fewer than two finite estimates can
    be
    drawn (the caller treats that as "not stable enough").
    """
    if rows < 8:
        return None
    half = max(2, rows // 2)
    if half >= rows:
        return None
    rng = np.random.default_rng(STABILITY_SEED)
    drawn: list[float] = []
    for _ in range(STABILITY_DRAWS):
        pick = rng.choice(rows, half, replace=False)
        block = product[pick]
        value, _peak = _coarse_fine_shift(
            block,
            sw_hz,
            span_hz=span_hz,
            coarse_step_hz=step_hz,
            fine_half_width_hz=fine_half_width_hz,
            fine_step_hz=fine_step_hz,
            refine=refine,
        )
        if math.isfinite(value):
            drawn.append(value)
    if len(drawn) < 2:
        return None
    return (max(drawn) - min(drawn)) / 2.0


def estimate_shift_hz_pooled(
    reference: np.ndarray,
    part: np.ndarray,
    sw_hz: float,
    *,
    span_hz: float,
    step_hz: float = SHIFT_STEP_HZ,
    time_fraction: float = TIME_FRACTION,
    fine_half_width_hz: float = FINE_HALF_WIDTH_HZ,
    fine_step_hz: float = FINE_STEP_HZ,
    refine: bool = PARABOLIC_REFINEMENT,
    stability: bool = True,
) -> PooledShiftEstimate | None:
    """Pooled (coherent) estimate of the rigid frequency shift (Hz) of ``part`` vs ``reference``,
    plus the two criteria.

    - ``stat`` / ``null_stat``: the coherent statistic and the control from **scrambling the
      per-trace phase** (see :func:`_scrambled_null`);
    - ``uncertainty_hz``: the scatter given by random half-split resampling (±,Hz, see
      :func:`_resampled_uncertainty`).

    Returns None when the data are too small or the floor is not positive (the caller only
    reports,
    it does not guess).
    """
    if reference is None or part is None or sw_hz <= 0 or span_hz <= 0:
        return None
    rows = min(int(reference.shape[0]), int(part.shape[0]))
    columns = min(int(reference.shape[-1]), int(part.shape[-1]))
    if rows < 4 or columns < 8:
        return None
    reference, part = reference[:rows, :columns], part[:rows, :columns]
    trimmed_ref, trimmed_part = _time_fraction_rows(reference, part, time_fraction)
    product = _coherent_product(trimmed_ref, trimmed_part)
    shift, peak = _coarse_fine_shift(
        product,
        sw_hz,
        span_hz=span_hz,
        coarse_step_hz=step_hz,
        fine_half_width_hz=fine_half_width_hz,
        fine_step_hz=fine_step_hz,
        refine=refine,
    )
    if not math.isfinite(shift):
        return None
    incoherent = _scan_curves(product, sw_hz, span_hz=span_hz, step_hz=step_hz, coherent=False)
    baseline = float(np.median(incoherent)) if incoherent.size else float("nan")
    if not math.isfinite(baseline) or baseline <= 0:
        return None
    stat = float(peak / baseline)
    null_stat = _scrambled_null(product, sw_hz, span_hz=span_hz, step_hz=step_hz, baseline=baseline)
    uncertainty = _resampled_uncertainty(
        product, sw_hz, span_hz=span_hz, step_hz=step_hz, rows=rows
    )
    shifts: dict[float, float] | None = None
    spread: float | None = None
    stable: bool | None = None
    if stability and rows >= 4:
        shifts = {}
        for fraction in STABILITY_TIME_FRACTIONS:
            sub_ref, sub_part = _time_fraction_rows(reference, part, fraction)
            sub_product = _coherent_product(sub_ref, sub_part)
            value, _peak = _coarse_fine_shift(
                sub_product,
                sw_hz,
                span_hz=span_hz,
                coarse_step_hz=step_hz,
                fine_half_width_hz=fine_half_width_hz,
                fine_step_hz=fine_step_hz,
                refine=refine,
            )
            if math.isfinite(value):
                shifts[float(fraction)] = float(value)
        if len(shifts) >= 2:
            spread = max(shifts.values()) - min(shifts.values())
            stable = spread <= TIME_FRACTION_SPREAD_HZ
    return PooledShiftEstimate(
        shift_hz=shift,
        stat=stat,
        null_stat=null_stat,
        uncertainty_hz=uncertainty,
        traces=rows,
        span_hz=float(span_hz),
        resolution_hz=float(fine_step_hz),
        time_fraction_shifts=shifts,
        time_fraction_spread_hz=spread,
        stable=stable,
    )


def direct_linewidth_hz(
    plane: np.ndarray,
    sw_hz: float,
    *,
    traces: int = LINEWIDTH_TRACES,
    zero_fill: int = 4,
) -> float | None:
    """Direct-dimension line width (Hz): full width at half maximum of the **median magnitude
    spectrum** of the top traces; None when it cannot be measured.

    The impact criterion compares "drift vs line width" (see "theory ②" at the top of the
    module),
    so the line width is the ruler of that criterion. Convention: take the highest-energy traces
    →
    take the **point-by-point median** of their magnitude spectra (the median flattens the noise
    floor, which then does not grow with the trace count) → subtract that median spectrum's own
    median floor → measure the full width at half maximum (zero fill ×4 for interpolation).

    **Do not** measure the line width from the projection obtained by "adding the magnitude
    spectra
    of all traces" - the projection shape is set by the distribution of the individual lines
    (measured 668.8 Hz on one synthetic set against a true line width of 133.8 Hz); nor measure
    it
    per trace and then take a median - at low SNR the per-trace half width is set by noise (on
    the
    same synthetic set it swings from 84 Hz to 234 Hz).
    """
    if plane is None or np.ndim(plane) != 2 or plane.shape[1] < 8 or sw_hz <= 0:
        return None
    data = _clean_rows(plane)
    energy = np.sum(np.abs(data) ** 2, axis=-1)
    order = np.argsort(energy)[::-1][: max(1, int(traces))]
    zf = max(1, int(zero_fill))
    size = int(data.shape[1])
    padded = np.zeros((order.size, size * zf), dtype=complex)
    padded[:, :size] = data[order]
    average = np.median(np.abs(np.fft.fft(padded, axis=-1)), axis=0)
    floor = float(np.median(average))
    peak = int(np.argmax(average))
    height = float(average[peak]) - floor
    if height <= 0:
        return None
    half = floor + height / 2.0
    left = peak
    while left > 0 and average[left] > half:
        left -= 1
    right = peak
    while right < average.size - 1 and average[right] > half:
        right += 1
    return float(right - left) * float(sw_hz) / average.size


def direct_axis_hz(experiment: Experiment) -> tuple[float, float] | None:
    """The direct dimension's (sweep width Hz, observation frequency MHz); None if unavailable.

    ``Dimension.sw`` has the same unit (Hz) as ``-xSW`` in fid.com, and ``Dimension.sf`` is
    ``-xOBS`` (MHz); ppm = Hz / sf.
    """
    dim = experiment.direct_dimension or (
        experiment.dimensions[0] if experiment.dimensions else None
    )
    if dim is None or dim.sw <= 0 or dim.sf <= 0:
        return None
    return float(dim.sw), float(dim.sf)


def load_segment_planes(paths: Sequence[Path | str]) -> np.ndarray | None:
    """Read one part's converted fid (possibly a multi-file slice stream) → (rows, complex points),
    **without filtering traces and preserving the row order**.

    Multi-part estimation must go through this entry point: pairing follows the physical trace
    index
    (the same row of the indirect dimension). When each part picks its own traces by energy
    rank,
    the i-th high-energy trace may come from a different indirect-dimension plane of another
    part -
    the "cross-correlation" then multiplies two different signals and the estimate is pure
    noise.
    Returns None if it cannot be read or is not 2-D (the caller only reports, it does not
    guess).
    """
    blocks: list[np.ndarray] = []
    for path in paths:
        got = _read_fid_raw(Path(path))
        if got is None:
            return None
        blocks.append(np.asarray(got[0]))
    if not blocks:
        return None
    data = np.concatenate(blocks, axis=0)
    if data.ndim != 2 or data.size == 0:
        return None
    return data


def _finite_rows(data: np.ndarray) -> np.ndarray:
    """Boolean mask of entirely finite rows (a NaN/Inf trace is dropped as a whole)."""
    return np.isfinite(data).all(axis=1)


def _clean_rows(data: np.ndarray) -> np.ndarray:
    """Replace non-finite values by 0 (row indices preserved; bad rows do not pollute sums)."""
    return np.where(np.isfinite(data), data, 0.0).astype(np.complex128)


def _top_rows(data: np.ndarray, *, top: int) -> np.ndarray:
    """Take the top rows by row energy (at least :data:`TOP_MIN`, at most ``top``)."""
    energy = np.sum(np.abs(data) ** 2, axis=-1)
    order = np.argsort(energy)[::-1]
    keep = min(max(int(np.ceil(len(order) * TOP_FRACTION)), TOP_MIN), top)
    return data[order[:keep]]


def segment_traces(paths: Sequence[Path | str], *, top: int = TOP_MAX) -> np.ndarray | None:
    """Read the high-energy traces of one converted fid → (rows, complex points).

    For single-part self-checks/estimates (for multi-part estimation use
    :func:`load_segment_planes` + :func:`pool_trace_rows`, see :func:`detect_group_drift`).
    Traces
    with non-finite values are dropped as a whole; None if all are bad or it cannot be read.
    """
    data = load_segment_planes(paths)
    if data is None:
        return None
    finite = _finite_rows(data)
    if not finite.any():
        return None
    if not finite.all():
        data = data[finite]
    return _top_rows(data, top=top)


@dataclass
class GroupDriftResult:
    """Result of one inter-part field-drift check (offsets aligned by part number)."""

    reference_index: int = 0
    offsets_hz: list[float | None] = field(default_factory=list)
    offsets_ppm: list[float | None] = field(default_factory=list)
    #: Coherent statistic: the detection statistic for "can this measurement separate the signal"
    #: (not a source judgement, see "theory ③" at the top of the module)
    quality: list[float | None] = field(default_factory=list)
    #: Control statistic from the scrambled pairing (how high pure noise can climb here)
    null_stat: list[float | None] = field(default_factory=list)
    #: Scatter of random half-split resampling (±,Hz): the uncertainty of the estimate itself
    uncertainty_hz: list[float | None] = field(default_factory=list)
    time_fraction_shifts: list[dict[float, float] | None] = field(default_factory=list)
    time_fraction_spread_hz: list[float | None] = field(default_factory=list)
    stable: list[bool | None] = field(default_factory=list)
    needs_shift: dict[int, float] = field(default_factory=dict)
    trusted: list[int] | None = None
    reports_note: bool = False
    skipped: list[str] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)
    #: Criterion convention (2026-09-24): point width Hz, line width Hz, criterion Hz, number of
    #: traces used, criterion basis (``linewidth`` = max(hz_min, 0.2×line width); ``points`` = the
    #: fallback when the line width cannot be measured)
    point_hz: float | None = None
    linewidth_hz: float | None = None
    criterion_hz: float | None = None
    criterion_basis: str = "linewidth"
    points_min: float = DRIFT_POINTS_MIN
    traces_used: int = 0

    @property
    def corrected(self) -> bool:
        """Whether this round produced any correction value."""
        return bool(self.needs_shift)

    def trusted_indices(self) -> list[int]:
        """Part indices (0-based) usable for a conclusion: falls back to "all measured parts" when
        ``trusted`` is None (legacy record).
        """
        if self.trusted is not None:
            return list(self.trusted)
        return [index for index, ppm in enumerate(self.offsets_ppm) if ppm is not None]

    def largest_offset(self) -> tuple[float, float] | None:
        """(ppm, Hz) pair of the largest |Δ| among the trusted parts (for the residual check and
        the report); None when there is no trusted measurement.

        Fixed 2026-09-24 (measured with d_018): the ``skipped`` parts (the measurement cannot
        separate the signal / the estimation failed) also stay in ``offsets_hz``, and the old
        implementation used them to write "largest offset -24.26 Hz, within the 1.5 Hz
        criterion" -
        self-contradictory. Only parts that passed the "measurable + stable" thresholds are
        counted.
        """
        best: tuple[float, float] | None = None
        for index in self.trusted_indices():
            if not 0 <= index < len(self.offsets_ppm):
                continue
            ppm = self.offsets_ppm[index]
            if ppm is None:
                continue
            hz = self.offsets_hz[index] or 0.0
            if best is None or abs(ppm) > abs(best[0]):
                best = (ppm, hz)
        return best

    def max_abs_ppm(self) -> float:
        """Largest |Δppm| among the trusted parts; 0 when there is no trusted measurement."""
        best = self.largest_offset()
        return abs(best[0]) if best else 0.0

    def max_abs_hz(self) -> float | None:
        """Largest |ΔHz| among the trusted parts; None when there is no trusted measurement (not
        0, which would read as "perfectly aligned").
        """
        best = self.largest_offset()
        return abs(best[1]) if best else None

    def within_criterion(self) -> bool:
        """All **trusted** measurements are within the criterion (no trusted measurement → False).

        Decoupled from "does this round still need correcting": the latter would be written true
        in
        the "every part untrusted" case (the self-contradictory within_threshold_after fixed
        2026-09-24).
        """
        indices = self.trusted_indices()
        if not indices:
            return False
        criterion = float(self.criterion_hz or DRIFT_HZ_MIN)
        for index in indices:
            if not 0 <= index < len(self.offsets_hz):
                return False
            value = self.offsets_hz[index]
            if value is None or abs(float(value)) > criterion:
                return False
        return True

    def as_dict(self) -> dict[str, Any]:
        """Serialisable form for the JSON record (part numbers counted from 1)."""

        def _round(values: list[float | None], digits: int) -> list[float | None]:
            return [None if v is None else round(float(v), digits) for v in values]

        def _round_one(value: float | None, digits: int) -> float | None:
            return None if value is None else round(float(value), digits)

        return {
            "reference": self.reference_index + 1,
            "offsets_hz": _round(self.offsets_hz, 3),
            "offsets_ppm": _round(self.offsets_ppm, 5),
            "quality": _round(self.quality, 2),
            "null_stat": _round(self.null_stat, 2),
            "uncertainty_hz": _round(self.uncertainty_hz, 2),
            "time_fraction_shifts": [
                None
                if entry is None
                else {f"{key:.2f}": round(float(value), 3) for key, value in sorted(entry.items())}
                for entry in self.time_fraction_shifts
            ],
            "time_fraction_spread_hz": _round(self.time_fraction_spread_hz, 3),
            "stable": list(self.stable),
            "shifted_parts_hz": {
                str(index + 1): round(float(value), 4)
                for index, value in sorted(self.needs_shift.items())
            },
            "trusted_parts": (
                None if self.trusted is None else [index + 1 for index in self.trusted]
            ),
            "skipped": list(self.skipped),
            "points_min": self.points_min,
            "traces_used": self.traces_used,
            "point_hz": _round_one(self.point_hz, 4),
            "linewidth_hz": _round_one(self.linewidth_hz, 3),
            "criterion_basis": self.criterion_basis,
            "criterion_hz": _round_one(self.criterion_hz, 4),
        }


def _within_criterion_report(
    result: GroupDriftResult, *, criterion_hz: float, hz_min: float
) -> str:
    """The "within the criterion" line: written with the convention **actually in force** (line
    width / point width / Hz floor).

    2026-09-24 re-check: naming the wrong convention makes one and the same report
    self-contradictory (writing "within the 1.5 Hz criterion" on 66.9 Hz/point data, or still
    writing FFT points after the line-width criterion was adopted).
    """
    largest = result.largest_offset()
    offset_hz = largest[1] if largest else 0.0
    offset_ppm = largest[0] if largest else 0.0
    if result.criterion_basis == "linewidth" and result.linewidth_hz:
        return tr(
            "Inter-part field drift check: largest offset {p0:+.2f} Hz ({p1:+.4f} ppm) is within "
            "the {p2:.2f} Hz criterion ({p3:.2f} linewidth(s); line width {p4:.1f} Hz, FFT point "
            "{p5:.1f} Hz); no shift applied",
            p0=offset_hz,
            p1=offset_ppm,
            p2=criterion_hz,
            p3=abs(offset_hz) / result.linewidth_hz,
            p4=result.linewidth_hz,
            p5=result.point_hz or 0.0,
        )
    if result.point_hz:
        return tr(
            "Inter-part field drift check: largest offset {p0:+.2f} Hz ({p1:+.4f} ppm) is within "
            "the {p2:.2f} Hz criterion ({p3:.2f} FFT point(s) at {p4:.1f} Hz/point); no shift "
            "applied",
            p0=offset_hz,
            p1=offset_ppm,
            p2=criterion_hz,
            p3=criterion_hz / result.point_hz,
            p4=result.point_hz,
        )
    return tr(
        "Inter-part field drift check: largest offset {p0:+.2f} Hz ({p1:+.4f} ppm) is within the "
        "{p2:g} Hz criterion; no shift applied",
        p0=offset_hz,
        p1=offset_ppm,
        p2=hz_min,
    )


def detect_group_drift(
    segments: Sequence[Sequence[Path | str]],
    *,
    sw_hz: float,
    sf_mhz: float,
    reference_index: int = 0,
    hz_min: float = DRIFT_HZ_MIN,
    points_min: float = DRIFT_POINTS_MIN,
    max_ppm: float = MAX_PPM_SHIFT,
    step_hz: float = SHIFT_STEP_HZ,
) -> GroupDriftResult:
    """Compare every part's frequency shift against the reference part and give the ``PS -rs``
    values (Hz) to write into each part's fid.com.

    ``segments`` is the list of converted fid files per part (a slice stream passes its whole
    batch); ``sw_hz``/``sf_mhz`` come from the direct dimension (SW_h and SFO1).

    Criterion = ``max(hz_min, IMPACT_FRACTION × line width)`` (see "theory ②" at the top of the
    module; falls back to ``max(hz_min, points_min × point width)`` when the line width cannot
    be
    measured). Only **measurable** parts (coherent statistic above the scrambled-pairing control
    and
    random half-split scatter not over the criterion) take part in the conclusion: over the
    criterion goes into ``needs_shift``, not over is only reported. Unmeasurable parts go into
    ``skipped`` with their reason spelled out - that is "this measurement cannot separate the
    signal", **not** evidence that "the two parts are not the same experiment".
    """
    result = GroupDriftResult(reference_index=reference_index, points_min=points_min)
    if not segments:
        return result
    result.trusted = []
    planes = [load_segment_planes(paths) for paths in segments]
    count = len(planes)
    result.offsets_hz = [None] * count
    result.offsets_ppm = [None] * count
    result.quality = [None] * count
    result.null_stat = [None] * count
    result.uncertainty_hz = [None] * count
    result.time_fraction_shifts = [None] * count
    result.time_fraction_spread_hz = [None] * count
    result.stable = [None] * count
    reference = planes[reference_index] if 0 <= reference_index < count else None
    if reference is None:
        reason = tr(
            "part 1 (reference): the converted fid could not be read, "
            "inter-part field drift was not checked"
        )
        result.skipped.append(reason)
        result.reports = [reason]
        return result
    rows, points = int(reference.shape[0]), int(reference.shape[1])
    usable: list[int] = []
    for index, plane in enumerate(planes):
        if index == reference_index:
            usable.append(index)
            continue
        if plane is None:
            result.skipped.append(
                tr(
                    "part {p0}: the converted fid could not be read; field drift was not "
                    "checked for this part",
                    p0=index + 1,
                )
            )
            continue
        if plane.shape != reference.shape:
            result.skipped.append(
                tr(
                    "part {p0}: the converted fid shape ({p1} traces x {p2} points) differs from "
                    "part 1 ({p3} x {p4}); the parts cannot be paired trace by trace, so field "
                    "drift was not checked for this part",
                    p0=index + 1,
                    p1=int(plane.shape[0]),
                    p2=int(plane.shape[1]),
                    p3=rows,
                    p4=points,
                )
            )
            continue
        usable.append(index)
    keep = pool_trace_rows([planes[index] for index in usable])
    if keep.size == 0:
        reason = tr(
            "inter-part field drift: no trace holds finite data in every part; the check did "
            "not run"
        )
        result.skipped.append(reason)
        result.reports = [reason]
        return result
    result.traces_used = int(keep.size)
    point_hz = float(sw_hz) / points if (points > 0 and sw_hz > 0) else None
    result.point_hz = point_hz
    linewidth_hz = direct_linewidth_hz(reference, sw_hz) if sw_hz > 0 else None
    result.linewidth_hz = linewidth_hz
    if linewidth_hz:
        # 2026-09-24 (user: "if the line-width convention is more reasonable, change it"):
        # criterion = max(1.5 Hz, 0.2×line width)
        result.criterion_basis = "linewidth"
        criterion_hz = max(float(hz_min), IMPACT_FRACTION * linewidth_hz)
    else:
        result.criterion_basis = "points"
        criterion_hz = max(float(hz_min), points_min * point_hz) if point_hz else float(hz_min)
    result.criterion_hz = criterion_hz
    span_hz = max(max_ppm * sf_mhz, 2.0 * criterion_hz)
    reference_rows = _clean_rows(reference[keep])
    for index in usable:
        if index == reference_index:
            continue
        part_rows = _clean_rows(planes[index][keep])
        estimated = estimate_shift_hz_pooled(
            reference_rows, part_rows, sw_hz, span_hz=span_hz, step_hz=step_hz
        )
        if estimated is None:
            result.skipped.append(
                tr(
                    "part {p0}: the frequency shift could not be estimated; field drift was "
                    "not checked for this part",
                    p0=index + 1,
                )
            )
            continue
        delta = estimated.shift_hz
        result.offsets_hz[index] = delta
        result.offsets_ppm[index] = delta / sf_mhz if sf_mhz else 0.0
        result.quality[index] = estimated.stat
        result.null_stat[index] = estimated.null_stat
        result.uncertainty_hz[index] = estimated.uncertainty_hz
        result.time_fraction_shifts[index] = estimated.time_fraction_shifts
        result.time_fraction_spread_hz[index] = estimated.time_fraction_spread_hz
        result.stable[index] = estimated.stable
        if not estimated.significant:
            # The coherent statistic does not stand above the scrambled-pairing control - this
            # measurement cannot separate the signal (see "theory ③" at the top of the module). The
            # wording may describe the measurement capability **only** and must not claim that "the
            # two parts are not the same experiment".
            result.skipped.append(
                tr(
                    "part {p0}: the coherent statistic {p1:.1f} does not stand above the "
                    "shuffled-pairing control {p2:.1f}, so no drift could be measured; no "
                    "correction was applied",
                    p0=index + 1,
                    p1=estimated.stat,
                    p2=estimated.null_stat,
                )
            )
            result.reports_note = True
            continue
        if abs(delta) >= span_hz - 2.0 * step_hz:
            result.skipped.append(
                tr(
                    "part {p0}: measured drift {p1:.4f} ppm ({p2:.1f} Hz) is beyond the plausible "
                    "range ({p3:.2f} ppm); treated as a mistracked estimate and skipped",
                    p0=index + 1,
                    p1=delta / sf_mhz if sf_mhz else 0.0,
                    p2=delta,
                    p3=max_ppm,
                )
            )
            continue
        if estimated.uncertainty_hz is None:
            result.skipped.append(
                tr(
                    "part {p0}: only {p1} comparable trace(s) - too few to repeat the estimate and "
                    "check that it is reproducible; the measured shift {p2:+.2f} Hz is applied "
                    "anyway (its uncertainty is unknown)",
                    p0=index + 1,
                    p1=estimated.traces,
                    p2=delta,
                )
            )
            result.reports_note = True
        elif estimated.stable is False:
            scatter = " / ".join(
                f"{fraction * 100:.0f}% {value:+.2f}"
                for fraction, value in sorted((estimated.time_fraction_shifts or {}).items())
            )
            result.skipped.append(
                tr(
                    "part {p0}: the estimate swings with the time-domain window ({p1} Hz; spread "
                    "{p2:.2f} Hz > {p3:.2f} Hz) - low confidence, it is sensitive to the noisy "
                    "tail; the measured shift {p4:+.2f} Hz is applied anyway",
                    p0=index + 1,
                    p1=scatter,
                    p2=estimated.time_fraction_spread_hz or 0.0,
                    p3=TIME_FRACTION_SPREAD_HZ,
                    p4=delta,
                )
            )
            result.reports_note = True
        elif estimated.uncertainty_hz > criterion_hz:
            result.skipped.append(
                tr(
                    "part {p0}: random half-split repeats scatter by \u00b1{p1:.1f} Hz, more than "
                    "the {p2:.2f} Hz criterion - the estimate is not reproducible at this "
                    "signal-to-noise; the measured shift {p3:+.2f} Hz is applied anyway",
                    p0=index + 1,
                    p1=estimated.uncertainty_hz,
                    p2=criterion_hz,
                    p3=delta,
                )
            )
            result.reports_note = True
        else:
            result.trusted.append(index)
        if abs(delta) <= criterion_hz:
            continue
        result.needs_shift[index] = delta
    reports: list[str] = []
    if result.needs_shift:
        for index in sorted(result.needs_shift):
            offset = result.offsets_hz[index] or 0.0
            if index in result.trusted:
                reports.append(
                    tr(
                        "Inter-part field drift part {p0}: {p1:+.2f} Hz ({p2:+.4f} ppm) vs part 1 "
                        "is over the {p3:.2f} Hz criterion ({p4:.2f} linewidth(s) at {p5:.1f} Hz "
                        "line width); correcting this part's fid.com (PS -rs) and re-converting",
                        p0=index + 1,
                        p1=offset,
                        p2=result.offsets_ppm[index] or 0.0,
                        p3=criterion_hz,
                        p4=abs(offset) / linewidth_hz if linewidth_hz else 0.0,
                        p5=linewidth_hz or 0.0,
                    )
                )
                continue
            reports.append(
                tr(
                    "Inter-part field drift part {p0}: {p1:+.2f} Hz ({p2:+.4f} ppm) vs part 1 is "
                    "over the {p3:.2f} Hz criterion, but the estimate is less certain than the "
                    "criterion (see the reason below); the measured value is applied anyway - "
                    "correcting this part's fid.com (PS -rs) and re-converting",
                    p0=index + 1,
                    p1=offset,
                    p2=result.offsets_ppm[index] or 0.0,
                    p3=criterion_hz,
                )
            )
    elif result.trusted:
        reports.append(_within_criterion_report(result, criterion_hz=criterion_hz, hz_min=hz_min))
    else:
        reports.append(
            tr(
                "Inter-part field drift: no part gave a reliable measurement against part {p0} "
                "(see the per-part reasons below); no frequency shift was applied",
                p0=reference_index + 1,
            )
        )
    reports += result.skipped
    if result.reports_note:
        # User 2026-09-24: "why does it still say the two parts do not look like the same
        # experiment" - make clear that "not measurable" does not mean "different source"
        reports.append(no_measurement_note())
    result.reports = reports
    return result


def criterion_text(result: GroupDriftResult) -> str:
    """Human-readable text of the criterion convention (for audit records / logs; it states the
    convention and value **actually in force**).

    ``detection_rule`` in ``qc_audit.jsonl`` must match the criterion used at decision time -
    after
    the criterion changed from "1 FFT point" to ``max(1.5 Hz, 0.2×line width)``, an audit that
    still
    states the old convention leaves the record disconnected from the behaviour.
    """
    if result.criterion_basis == "linewidth" and result.linewidth_hz:
        product = IMPACT_FRACTION * float(result.linewidth_hz)
        if product < DRIFT_HZ_MIN:
            # 2026-09-24 review B10: the criterion is max(1.5 Hz, 0.2×line width) - the floor takes
            # over below a 7.5 Hz line width, and printing only "line width × 0.2 = criterion" would
            # put a formula in the record that disagrees with the value in force
            return tr(
                "line width {p0:.1f} Hz x {p1:g} = {p2:.2f} Hz, below the {p3:g} Hz floor "
                "=> criterion {p3:g} Hz",
                p0=result.linewidth_hz,
                p1=IMPACT_FRACTION,
                p2=product,
                p3=DRIFT_HZ_MIN,
            )
        return tr(
            "line width {p0:.1f} Hz x {p1:g} = {p2:.2f} Hz",
            p0=result.linewidth_hz,
            p1=IMPACT_FRACTION,
            p2=float(result.criterion_hz or 0.0),
        )
    if result.point_hz:
        return tr(
            "FFT point width {p0:.1f} Hz x {p1:g} = {p2:.2f} Hz",
            p0=result.point_hz,
            p1=result.points_min,
            p2=float(result.criterion_hz or 0.0),
        )
    return tr("floor {p0:g} Hz", p0=DRIFT_HZ_MIN)


def criterion_rule_text(result: GroupDriftResult) -> str:
    """One sentence for the QC audit ``detection_rule``: the inter-part direct-dimension frequency
    shift plus the criterion **actually in force**.

    2026-09-24 re-check: after the criterion changed from "1 FFT point" to
    ``max(1.5 Hz, 0.2×line width)``, an old convention in ``qc_audit.jsonl`` would leave the
    record
    disconnected from the behaviour - :func:`criterion_text` now supplies the value **actually
    in
    force**.
    """
    return tr(
        "direct-dimension frequency offset of each part vs part 1 over the effective criterion: "
        "{p0:.2f} Hz ({p1})",
        p0=float(result.criterion_hz or DRIFT_HZ_MIN),
        p1=criterion_text(result),
    )


def _param_equal(first: Any, second: Any) -> bool:
    """Whether two acquisition-parameter values are equivalent (numbers with a 1e-4 relative
    tolerance, strings strictly equal).

    The tolerance is necessary: the same physical sweep width may be written as 11904.762 and
    11904.7619047619 in different parts' acqus (the same convention as
    :func:`core.data.bruker_reader.read_segments`); genuinely different experiments differ by
    far
    more than this tolerance.
    """
    if isinstance(first, bool) or isinstance(second, bool):
        return bool(first) == bool(second)
    if isinstance(first, (int, float)) and isinstance(second, (int, float)):
        scale = max(abs(float(first)), abs(float(second)), 1.0)
        return abs(float(first) - float(second)) <= 1e-4 * scale
    return first == second


def _segment_parameters(raw_dir: Path) -> dict[str, Any] | None:
    """Acquisition parameters relevant to merging from one raw directory; None when the parameter
    files cannot be read.
    """
    try:
        params = parse_dataset_params(Path(raw_dir))
    except (OSError, ValueError):
        return None
    if not isinstance(params, dict):
        return None
    found: dict[str, Any] = {}
    keys = set(BLOCKING_PARAM_KEYS) | set(BLOCKING_PARAM_KEYS_NUS) | set(WARNING_PARAM_KEYS)
    for name in CONSISTENCY_FILES:
        block = params.get(name)
        if not isinstance(block, dict):
            continue
        for key in keys:
            if key in block:
                found[f"{name}.{key}"] = block[key]
    return found or None


def _jsonable(value: Any) -> Any:
    """Fold a parameter value into a JSON-recordable form (list/tuple → list, others as is)."""
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def check_segment_consistency(raw_dirs: Sequence[Path | str]) -> dict[str, Any]:
    """Compare the acquisition parameters part by part (NS/TD/DS/SW/O1/SFO1/GRPDLY/FnMODE/PULPROG).

    Returns a dictionary that can go straight into a JSON record: ``available`` (the number of
    parts
    whose parameter files were read), ``values`` (key → per-part value, part order = merge
    order),
    ``warnings`` (non-blocking, e.g. a differing NS - see "theory ①" at the top of the module;
    it is
    not a weighting issue) and ``blocking`` (blocks the merge: differing TD/SW/O1 = not the same
    acquisition parameters). The wording comes from :func:`consistency_report_lines`, so the log
    and
    the step report share one source.

    When the parameter files cannot be read (fake test directories / permission problems)
    ``available < 2``: nothing is guessed and nothing is blocked.
    """
    dirs = [Path(raw) for raw in raw_dirs]
    per_part = [_segment_parameters(raw) for raw in dirs]
    available = sum(1 for entry in per_part if entry)
    keys = sorted({key for entry in per_part if entry for key in entry})
    values: dict[str, list[Any]] = {
        key: [_jsonable(entry.get(key)) if entry else None for entry in per_part] for key in keys
    }
    nus_complementary = False
    if len(dirs) >= 2:
        try:
            from core.data.bruker_reader import classify_segment_kind

            nus_complementary = classify_segment_kind(dirs) == "segmented_nus"
        except Exception:
            nus_complementary = False
    blocking_keys = BLOCKING_PARAM_KEYS_NUS if nus_complementary else BLOCKING_PARAM_KEYS
    warnings: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    for key in keys:
        column = values[key]
        present = [value for value in column if value is not None]
        if len(present) < 2:
            continue
        if all(_param_equal(present[0], other) for other in present[1:]):
            continue
        short = key.split(".", 1)[-1]
        entry = {"key": short, "parameter": key, "values": column}
        if short in blocking_keys:
            blocking.append(entry)
        elif nus_complementary and short == "TD":
            entry["nus_subset_points"] = True
            warnings.append(entry)
        else:
            warnings.append(entry)
    return {
        "available": available,
        "parts": len(dirs),
        "nus_complementary": nus_complementary,
        "values": values,
        "warnings": warnings,
        "blocking": blocking,
    }


def _format_param_values(values: Sequence[Any] | None) -> str:
    """Fold per-part values into a short string such as ``32/16/32/32/16`` (None → "unknown")."""
    parts: list[str] = []
    for value in values or []:
        if value is None:
            parts.append(tr("unknown"))
            continue
        if isinstance(value, float) and value == int(value):
            parts.append(str(int(value)))
            continue
        parts.append(str(value))
    return "/".join(parts) if parts else tr("unknown")


def consistency_report_lines(consistency: dict[str, Any] | None) -> list[str]:
    """Turn the result of :func:`check_segment_consistency` into report lines (the log and the step
    report share one source).

    Blocking entries (differing TD/SW/O1/SFO1/GRPDLY/FnMODE/PULPROG/DECIM/DSPFVS) say "merging
    refused"; warning entries (NS/DS/RG) say "still merged" - a differing NS additionally points
    out
    that it is **not a weighting issue** (the matched weight is constant).
    """
    if not isinstance(consistency, dict) or not consistency.get("available"):
        return []
    lines: list[str] = []
    for entry in consistency.get("blocking") or []:
        lines.append(
            tr(
                "part consistency: {p0} differs between parts ({p1}) - these parts are not the "
                "same experiment; merging is refused",
                p0=str(entry.get("key", "")),
                p1=_format_param_values(entry.get("values")),
            )
        )
    for entry in consistency.get("warnings") or []:
        key = str(entry.get("key", ""))
        values = _format_param_values(entry.get("values"))
        if entry.get("nus_subset_points"):
            lines.append(
                tr(
                    "part consistency: TD differs between parts ({p0}) - this is complementary NUS "
                    "sampling (each part holds a different subset of the grid, so its TD is just "
                    "that subset's point count); the full grid NusTD is the same, the nuslists "
                    "were merged to complete the grid, so the parts were still merged",
                    p0=values,
                )
            )
        elif key == "NS":
            lines.append(
                tr(
                    "part consistency: NS differs between parts ({p0}) - the parts were not "
                    "acquired with the same number of scans, so the per-trace noise differs by "
                    "1/sqrt(NS); adding the FIDs directly is still the matched combination (the "
                    "signal and the noise both scale with NS), i.e. a fact to be aware of, not a "
                    "weighting error",
                    p0=values,
                )
            )
        else:
            lines.append(
                tr(
                    "part consistency: {p0} differs between parts ({p1}); the parts were still "
                    "merged",
                    p0=key,
                    p1=values,
                )
            )
    return lines


def no_measurement_note() -> str:
    """The convention note for "no drift could be measured" (on the real machine the user read the
    old wording as "the data are not from the same experiment").

    The conversion log of ``detect_group_drift`` and
    ``direct_diagnostics._drift_report_lines`` share this sentence, so the log and the GUI
    report
    agree.
    """
    return tr(
        'note: "no drift could be measured" only means the per-trace signal-to-noise is too low '
        "for this measurement - it does not mean the parts come from different experiments (for "
        "that, check the part consistency of NS/TD/SW/O1 etc.)"
    )


def is_identity_claim(text: str) -> bool:
    """Recognise "the parts are not the same experiment"-style wording in legacy records (used to
    replace it with the new convention in reports).
    """
    lowered = text.lower()
    # What is matched is the **already rendered** Chinese/English line in a record, not UI text
    # (i18n: keep)
    # The old English sentence was "the parts do not look like the same experiment(...)"
    # (2026-09-24 review B12: matching only "not the same experiment" misses it, and that sentence
    # would stay as is in legacy English records)
    return (  # i18n: keep
        "不是同一次实验" in text
        or "not the same experiment" in lowered
        or "do not look like the same experiment" in lowered
    )


def _format_hz(value: float) -> str:
    """PS -rs value: 4 decimals with trailing zeros stripped (e.g. ``12.5000Hz`` → ``12.5Hz``)."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{text}Hz" if text else "0Hz"


_PS_SHIFT_RE = re.compile(r"^\s*\|\s*nmrPipe\s+-fn\s+PS\s+-(?:rs|ls)\s+\S+\s*\\\s*$")
_MULT_LINE_RE = re.compile(r"^\s*\|\s*nmrPipe\s+-fn\s+MULT\b")


def insert_ps_shift(text: str, shift_hz: float) -> tuple[str, bool]:
    """Insert ``| nmrPipe -fn PS -rs <shift>Hz \\`` before the ``MULT -c`` line (idempotent).

    ``MULT -c`` is kept (it is only a scalar scaling); when the line immediately before MULT
    already
    holds a shift line inserted by this function, its value is **replaced** rather than a second
    line being added - re-running the processing never accumulates changes. When no MULT line is
    found (not a conversion script generated by bruker -AUTO) the script is left alone and
    ``(original text, False)`` is returned.
    """
    if not math.isfinite(float(shift_hz)):
        return text, False
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not _MULT_LINE_RE.match(line):
            continue
        new_line = f"| nmrPipe -fn PS -rs {_format_hz(shift_hz)} \\"
        if index and _PS_SHIFT_RE.match(lines[index - 1]):
            lines[index - 1] = new_line
        else:
            lines.insert(index, new_line)
        return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), True
    return text, False


def write_field_drift_record(work: Path | str, payload: dict[str, Any]) -> str:
    """Write the check result to ``work/field_drift.json`` (carried by conversion provenance)."""
    path = Path(work) / FIELD_DRIFT_FILENAME
    try:
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        return ""
    return str(path)


def read_field_drift_record(work: Path | str) -> dict[str, Any] | None:
    """Read back ``work/field_drift.json``; None if the file is absent or unreadable."""
    path = Path(work) / FIELD_DRIFT_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


__all__ = [
    "BLOCKING_PARAM_KEYS",
    "BLOCKING_PARAM_KEYS_NUS",
    "COHERENT_NULL_MARGIN",
    "COHERENT_STAT_MIN",
    "DRIFT_HZ_MIN",
    "DRIFT_POINTS_MIN",
    "DRIFT_PPM_THRESHOLD",
    "FIELD_DRIFT_FILENAME",
    "IMPACT_FRACTION",
    "LINEWIDTH_TRACES",
    "MAX_PPM_SHIFT",
    "MAX_ROUNDS",
    "NULL_DRAWS",
    "NULL_SEED",
    "STABILITY_DRAWS",
    "STABILITY_SEED",
    "POOL_MAX_TRACES",
    "SHIFT_STEP_HZ",
    "FINE_HALF_WIDTH_HZ",
    "FINE_STEP_HZ",
    "PARABOLIC_REFINEMENT",
    "TIME_FRACTION",
    "TIME_FRACTION_SPREAD_HZ",
    "STABILITY_TIME_FRACTIONS",
    "WARNING_PARAM_KEYS",
    "GroupDriftResult",
    "PooledShiftEstimate",
    "check_segment_consistency",
    "consistency_report_lines",
    "criterion_rule_text",
    "criterion_text",
    "detect_group_drift",
    "direct_axis_hz",
    "direct_linewidth_hz",
    "estimate_shift_hz_pooled",
    "insert_ps_shift",
    "is_identity_claim",
    "load_segment_planes",
    "no_measurement_note",
    "pool_trace_rows",
    "read_field_drift_record",
    "segment_traces",
    "write_field_drift_record",
]
