"""Inter-part field-drift detection and conversion-time correction for multi-part FIDs (2026-09-23).

Segmented acquisition and repeated-experiment averaging (repeat_uniform / repeat_nus) split one
experiment into several FIDs in time; when the parts drift in frequency (field drift), the
point-by-point time-domain summation of addNMR broadens or even splits the peaks - which is exactly
how "acquire a few more parts to raise the SNR" gets ruined (the lab 1stfid.com + 2ndAdd.com flow
aligned the parts by hand; this module automates it).

The rules the user locked on 2026-09-23 (the same day they were changed back from
"0.005 ppm and 1.5 Hz" to **Hz only**):

- reference = **part 1**; every part's frequency shift is measured against it;
- criterion: **|d-Hz| > :data:`DRIFT_HZ_MIN`**. Why the ppm condition was dropped: 0.005 ppm at
  800 MHz equals 4.00 Hz, which lets a real inter-part drift through untouched - on the VM the
  three repeated parts of d_015 measured about 3 Hz each (2.9 / 3.0 Hz), so the old criterion would
  never have corrected anything. ppm is still measured and recorded
  (:data:`DRIFT_PPM_THRESHOLD` is record/display only and does not gate anything);
- the correction goes into **that part's fid.com**: ``| nmrPipe -fn PS -rs <d>Hz \\`` is inserted
  before ``| nmrPipe -fn MULT -c ...`` (``MULT -c`` is kept) and the part is re-converted;
- the residual is re-checked after re-conversion, at most :data:`MAX_ROUNDS` rounds; a residual
  that is still over the threshold is reported, never escalated.

Estimator: matched filter (it replaced "strongest peak position" on 2026-09-23). For a rigid shift
d-f the per-trace product ``part*conj(reference)`` is ``|ref|^2*exp(+i2*pi*d-f*t)``, so the product
is scanned over d-f within +-max_ppm and the per-trace power is summed; the peak gives d-f.
**Why not the strongest peak position**: with several peaks or a poor SNR the strongest peak
jumps lines - on
the VM the three synthetic parts of d_015 (a few Hz apart) gave +320/+350/+450 Hz with the
peak-position method (it had hopped to a neighbouring line) while the matched filter gave
-2.9/-7.0 Hz, matching the real relation; a hop silently moves a whole part by hundreds of Hz. The
correlation peak to baseline ratio (quality) also tells whether the two parts come from the same
experiment: if they do not, the part is reported and never corrected.

Sign convention (measured on the real machine on 2026-09-23, VM d_015): ``-rs``/``-ls`` of
``nmrPipe -fn PS`` are a **time-domain** frequency shift (``nmrPipe -fn PS -help`` calls it
"Time-Domain Phase Correction for Freq Shift") applied to the converted time-domain fid. Measured:
``PS -rs 30Hz`` moved the direct-dimension peak from point 141 to point 139 (sweep width
16129.032 Hz / 806 complex points = 20.01 Hz/point, i.e. -30 Hz) and ``PS -ls 30Hz`` the other way.
The "this part sits d Hz above the reference" offset measured here is therefore exactly the value
to write into ``PS -rs``: a positive value pulls that part's peak down (to lower frequency) onto the
reference; the reference part itself gets nothing (value 0). The sign of the matched filter was
calibrated the same way against a copy shifted with ``PS -rs 30Hz``: -30.00 Hz.
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
from core.project.manager import atomic_write_text
from ui_support.i18n import tr
from workflow.direct_diagnostics import _read_fid_raw

#: Criterion (set by the user on 2026-09-23: **Hz only**): correct only when |d-Hz| > 1.5
DRIFT_HZ_MIN = 1.5
#: ppm reference kept for the record/display only; it does not gate anything. It used to be the
#: threshold: 0.005 ppm @800.3 MHz = 4.00 Hz, which let a real 3 Hz inter-part drift (measured on
#: the VM with d_015) through untouched - changed back to Hz on the same day.
DRIFT_PPM_THRESHOLD = 0.005
#: Per-part correction limit: a larger drift is treated as a mistracked peak / not the same
#: experiment, reported only
MAX_PPM_SHIFT = 0.5
#: Maximum number of re-conversions per part (round 1 corrects, round 2 re-checks/refines)
MAX_ROUNDS = 2
#: Number of high-energy traces used for the estimate (the strongest ones by trace energy)
TOP_FRACTION = 0.05
TOP_MIN = 4
TOP_MAX = 20
#: Matched-filter search step (Hz): the criterion is 1.5 Hz, 0.25 Hz is fine enough
SHIFT_STEP_HZ = 0.25
#: Minimum correlation-peak to baseline ratio: below it the two parts share no signal (not the same
#: experiment)
MIN_PEAK_RATIO = 1.5
#: Limit on the median absolute deviation of the per-trace estimates (the smaller of the Hz value
#: and a fraction of the span): above it the traces disagree about the shift and the parts are not
#: copies of one experiment (measured on the VM: real low-SNR parts about 5 Hz, pure noise about
#: 79 Hz)
MAX_ROW_MAD_HZ = 2.0
MAX_ROW_MAD_FRACTION = 0.1
#: Name of the field-drift record that conversion provenance carries (same directory as the fid)
FIELD_DRIFT_FILENAME = "field_drift.json"


def direct_axis_hz(experiment: Experiment) -> tuple[float, float] | None:
    """Direct-dimension (sweep width Hz, observe frequency MHz); None when unavailable.

    ``Dimension.sw`` has the same unit as ``-xSW`` in fid.com (Hz) and ``Dimension.sf`` is
    ``-xOBS`` (MHz); ppm = Hz / sf.
    """
    dim = experiment.direct_dimension or (
        experiment.dimensions[0] if experiment.dimensions else None
    )
    if dim is None or dim.sw <= 0 or dim.sf <= 0:
        return None
    return float(dim.sw), float(dim.sf)


def segment_traces(paths: Sequence[Path | str], *, top: int = TOP_MAX) -> np.ndarray | None:
    """High-energy traces of one part (a slice stream may give several files) -> (rows, complex).

    Traces with non-finite values (NaN/Inf) are dropped entirely; all-bad or unreadable input
    returns None (the caller reports instead of guessing).
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
    finite = np.isfinite(data).all(axis=1)
    if not finite.any():
        return None
    if not finite.all():
        data = data[finite]
    energy = np.sum(np.abs(data) ** 2, axis=-1)
    order = np.argsort(energy)[::-1]
    keep = min(max(int(np.ceil(len(order) * TOP_FRACTION)), TOP_MIN), top)
    return data[order[:keep]]


@dataclass
class ShiftEstimate:
    """One part's shift against the reference plus two confidence indicators."""

    shift_hz: float
    peak_ratio: float
    row_mad_hz: float


def estimate_shift_hz(
    reference: np.ndarray,
    part: np.ndarray,
    sw_hz: float,
    *,
    span_hz: float,
    step_hz: float = SHIFT_STEP_HZ,
) -> ShiftEstimate | None:
    """Matched-filter estimate of the rigid frequency shift of part against reference (Hz).

    - ``peak_ratio``: overall correlation peak / baseline (the more the parts look like the same
      experiment, the higher);
    - ``row_mad_hz``: median absolute deviation of the per-trace estimates - true copies give
      nearly the same number on every trace (a few Hz at most in practice), while unrelated parts
      disagree wildly (pure noise is about a quarter of half the search range).

    ``span_hz`` is the search half-width (set by max_ppm); an estimate sitting on the boundary means
    the real drift is out of the plausible range and the caller treats it as "not the same
    experiment". Returns None when it cannot be computed (data too small).
    """
    if reference is None or part is None or sw_hz <= 0 or span_hz <= 0:
        return None
    rows = min(int(reference.shape[0]), int(part.shape[0]))
    n = min(int(reference.shape[-1]), int(part.shape[-1]))
    if rows < 1 or n < 8:
        return None
    product = part[:rows, :n] * np.conj(reference[:rows, :n])
    time = np.arange(n, dtype=float)
    grid = np.arange(-span_hz, span_hz + step_hz, step_hz)
    chunk = max(1, int(2_000_000 // max(n, 1)))
    per_row = np.zeros((rows, grid.size), dtype=float)
    for start in range(0, grid.size, chunk):
        piece = grid[start : start + chunk] / sw_hz
        phases = np.exp(-2j * np.pi * np.outer(piece, time))
        block = np.abs(product @ phases.T) ** 2
        per_row[:, start : start + piece.size] = block
    total = per_row.sum(axis=0)
    index = int(np.argmax(total))
    peak = float(total[index])
    baseline = float(np.median(total))
    delta = 0.0
    if 0 < index < total.size - 1:
        left, center, right = total[index - 1], total[index], total[index + 1]
        denom = left - 2.0 * center + right
        if denom:
            delta = float(np.clip(0.5 * (left - right) / denom, -0.5, 0.5))
    shift = float(grid[index] + delta * step_hz)
    if not math.isfinite(shift):
        return None
    ratio = peak / baseline if baseline > 0 else float("inf")
    row_best = [float(grid[int(np.argmax(row))]) for row in per_row]
    median_row = float(np.median(row_best))
    mad = float(np.median(np.abs(np.asarray(row_best) - median_row)))
    return ShiftEstimate(shift_hz=shift, peak_ratio=ratio, row_mad_hz=mad)


@dataclass
class GroupDriftResult:
    """Result of one inter-part drift check (offsets/confidence aligned with the part numbers)."""

    reference_index: int = 0
    offsets_hz: list[float | None] = field(default_factory=list)
    offsets_ppm: list[float | None] = field(default_factory=list)
    quality: list[float | None] = field(default_factory=list)
    row_mad_hz: list[float | None] = field(default_factory=list)
    needs_shift: dict[int, float] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)

    @property
    def corrected(self) -> bool:
        """Whether this round produced any correction value."""
        return bool(self.needs_shift)

    def largest_offset(self) -> tuple[float, float] | None:
        """The current largest |d| as a (ppm, Hz) pair (residual criterion and reports); None if it
        could not be measured."""
        best: tuple[float, float] | None = None
        for index, ppm in enumerate(self.offsets_ppm):
            if ppm is None:
                continue
            hz = self.offsets_hz[index] or 0.0
            if best is None or abs(ppm) > abs(best[0]):
                best = (ppm, hz)
        return best

    def max_abs_ppm(self) -> float:
        """The current largest |d-ppm|; 0 when no offset could be measured."""
        best = self.largest_offset()
        return abs(best[0]) if best else 0.0

    def as_dict(self) -> dict[str, Any]:
        """Serialisable form written into the JSON record (part numbers start at 1)."""

        def _round(values: list[float | None], digits: int) -> list[float | None]:
            return [None if v is None else round(float(v), digits) for v in values]

        return {
            "reference": self.reference_index + 1,
            "offsets_hz": _round(self.offsets_hz, 3),
            "offsets_ppm": _round(self.offsets_ppm, 5),
            "quality": _round(self.quality, 2),
            "row_mad_hz": _round(self.row_mad_hz, 3),
            "shifted_parts_hz": {
                str(index + 1): round(float(value), 4)
                for index, value in sorted(self.needs_shift.items())
            },
            "skipped": list(self.skipped),
        }


def detect_group_drift(
    segments: Sequence[Sequence[Path | str]],
    *,
    sw_hz: float,
    sf_mhz: float,
    reference_index: int = 0,
    hz_min: float = DRIFT_HZ_MIN,
    max_ppm: float = MAX_PPM_SHIFT,
    step_hz: float = SHIFT_STEP_HZ,
) -> GroupDriftResult:
    """Compare every part with the reference and give the ``PS -rs`` values (Hz) to write.

    ``segments`` holds the converted fid files of each part (a slice stream gives the whole batch);
    ``sw_hz``/``sf_mhz`` come from the direct dimension (SW_h and SFO1). Only parts whose |d-Hz| is
    over ``hz_min`` end up in ``needs_shift`` (ppm is recorded but never gates); parts with a too
    low correlation-peak/baseline ratio (not the same experiment) or an offset beyond ``max_ppm``
    are listed in ``skipped`` only.
    """
    result = GroupDriftResult(reference_index=reference_index)
    if not segments:
        return result
    loaded = [segment_traces(paths) for paths in segments]
    reference = (
        loaded[reference_index] if 0 <= reference_index < len(loaded) else None
    )
    result.offsets_hz = [None] * len(loaded)
    result.offsets_ppm = [None] * len(loaded)
    result.quality = [None] * len(loaded)
    result.row_mad_hz = [None] * len(loaded)
    if reference is None:
        reason = tr(
            "part 1 (reference): the converted fid could not be read, "
            "inter-part field drift was not checked"
        )
        result.skipped.append(reason)
        result.reports = [reason]
        return result
    span_hz = max(max_ppm * sf_mhz, 2.0 * hz_min)
    for index, part in enumerate(loaded):
        if index == reference_index:
            continue
        if part is None:
            result.skipped.append(
                tr(
                    "part {p0}: the converted fid could not be read; field drift was not "
                    "checked for this part",
                    p0=index + 1,
                )
            )
            continue
        estimated = estimate_shift_hz(
            reference, part, sw_hz, span_hz=span_hz, step_hz=step_hz
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
        result.quality[index] = estimated.peak_ratio
        result.row_mad_hz[index] = estimated.row_mad_hz
        mad_limit = max(MAX_ROW_MAD_HZ, MAX_ROW_MAD_FRACTION * span_hz)
        if estimated.peak_ratio < MIN_PEAK_RATIO or estimated.row_mad_hz > mad_limit:
            result.skipped.append(
                tr(
                    "part {p0}: the parts do not look like the same experiment (correlation peak "
                    "{p1:.1f}x the background, per-trace disagreement {p2:.1f} Hz); field drift "
                    "was not corrected",
                    p0=index + 1,
                    p1=estimated.peak_ratio,
                    p2=estimated.row_mad_hz,
                )
            )
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
        if abs(delta) <= hz_min:
            continue
        result.needs_shift[index] = delta
    reports: list[str] = []
    if result.needs_shift:
        for index in sorted(result.needs_shift):
            reports.append(
                tr(
                    "Inter-part field drift part {p0}: {p1:+.2f} Hz ({p2:+.4f} ppm) vs part 1 "
                    "is over the {p3} Hz criterion; correcting this part's fid.com (PS -rs) "
                    "and re-converting",
                    p0=index + 1,
                    p1=result.offsets_hz[index] or 0.0,
                    p2=result.offsets_ppm[index] or 0.0,
                    p3=hz_min,
                )
            )
    else:
        largest = result.largest_offset()
        reports.append(
            tr(
                "Inter-part field drift check: largest offset {p0:+.2f} Hz ({p1:+.4f} ppm) is "
                "within the {p2} Hz criterion; no shift applied",
                p0=largest[1] if largest else 0.0,
                p1=largest[0] if largest else 0.0,
                p2=hz_min,
            )
        )
    reports += result.skipped
    result.reports = reports
    return result


def _format_hz(value: float) -> str:
    """The number for PS -rs: 4 decimals, trailing zeros dropped (``12.5000Hz`` -> ``12.5Hz``)."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{text}Hz" if text else "0Hz"


_PS_SHIFT_RE = re.compile(r"^\s*\|\s*nmrPipe\s+-fn\s+PS\s+-(?:rs|ls)\s+\S+\s*\\\s*$")
_MULT_LINE_RE = re.compile(r"^\s*\|\s*nmrPipe\s+-fn\s+MULT\b")


def insert_ps_shift(text: str, shift_hz: float) -> tuple[str, bool]:
    """Insert ``| nmrPipe -fn PS -rs <shift>Hz \\`` before the ``MULT -c`` line (idempotent).

    ``MULT -c`` is kept (it is only a scalar scaling factor); when a shift line inserted by this
    function already sits right before MULT its number is **replaced** instead of stacking a second
    line - running the pipeline again never accumulates changes. When there is no MULT line (not a
    script produced by bruker -AUTO) the text is returned unchanged with False.
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
    """Write the check result to ``work/field_drift.json`` (conversion provenance carries it)."""
    path = Path(work) / FIELD_DRIFT_FILENAME
    try:
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        return ""
    return str(path)


def read_field_drift_record(work: Path | str) -> dict[str, Any] | None:
    """Read back ``work/field_drift.json``; returns None when missing or unreadable."""
    path = Path(work) / FIELD_DRIFT_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


__all__ = [
    "DRIFT_HZ_MIN",
    "DRIFT_PPM_THRESHOLD",
    "FIELD_DRIFT_FILENAME",
    "MAX_PPM_SHIFT",
    "MAX_ROUNDS",
    "MIN_PEAK_RATIO",
    "ShiftEstimate",
    "SHIFT_STEP_HZ",
    "GroupDriftResult",
    "detect_group_drift",
    "direct_axis_hz",
    "estimate_shift_hz",
    "insert_ps_shift",
    "read_field_drift_record",
    "segment_traces",
    "write_field_drift_record",
]
