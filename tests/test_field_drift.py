"""Tests for inter-part field drift detection and conversion-time correction (2026-09-23).

Covered:

- matched-filter estimator: a known time-domain modulation of d Hz comes back as d (sign and
  magnitude), including the -rs 30Hz style calibration;
- criterion: a correction value is produced only when ``|d-Hz| > 1.5`` (Hz only; changed back from
  "0.005 ppm **and** 1.5 Hz" on 2026-09-23);
- a part with no common signal (too low correlation peak/baseline) or a drift beyond the plausible
  range is reported, never corrected;
- ``PS -rs`` insertion into fid.com: before ``MULT``, ``MULT`` kept, idempotent;
- the ``field_drift.json`` record round-trips.

Why the estimator is not "strongest peak position": with several peaks the strongest peak jumps
lines (on the VM the three synthetic parts of d_015 gave +320/+350/+450 Hz with the peak-position
method while the matched filter gave -2.9/-7.0 Hz). The real-machine sign/convergence verification
lives on the VM as well (two synthetic parts +40.00 Hz -> residual 0.001 Hz; +3.0 Hz -> estimated
+3.000 Hz).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.bruker_workflow import parse_fid_com
from workflow.field_drift import (
    MIN_PEAK_RATIO,
    detect_group_drift,
    estimate_shift_hz,
    insert_ps_shift,
    read_field_drift_record,
    segment_traces,
    write_field_drift_record,
)

SW_HZ = 8000.0
SF_MHZ = 800.304          # 1H direct dimension (0.005 ppm = 4.00 Hz; the criterion is Hz only now)
SPAN_HZ = 0.5 * SF_MHZ    # same search half-width as detect_group_drift


def _write_fid(
    path: Path,
    *,
    n: int = 1024,
    rows: int = 8,
    peak_bins: float = 100.0,
    noise: float = 0.002,
    seed: int = 7,
    offset_hz: float = 0.0,
) -> Path:
    """Write a synthetic 2D fid (same layout as a real conversion product, readable by the parser).

    ``offset_hz`` is applied as a non-integer-bin time-domain modulation (the same physical quantity
    as a rigid frequency shift).
    """
    import nmrglue as ng

    dic = ng.pipe.create_empty_dic()
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = n
    dic["FDSPECNUM"] = rows
    dic["FDQUADFLAG"] = 0
    dic["FDF2QUADFLAG"] = 0
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=float)
    bins = peak_bins + offset_hz / (SW_HZ / n)
    signal = np.exp(-t / 120.0) * np.exp(2j * np.pi * bins * t / n)
    data = np.tile(signal, (rows, 1)) * rng.uniform(0.5, 2.0, (rows, 1))
    data = data + rng.normal(0, noise, (rows, n)) + 1j * rng.normal(0, noise, (rows, n))
    ng.pipe.write(str(path), dic, data.astype(np.complex64), overwrite=True)
    return path


def _two_segments(tmp_path: Path, offset_hz: float, *, n: int = 1024, rows: int = 8):
    base = _write_fid(tmp_path / "seg1.fid", n=n, rows=rows)
    shifted = _write_fid(tmp_path / "seg2.fid", n=n, rows=rows, seed=11, offset_hz=offset_hz)
    return [[base], [shifted]]


def test_estimate_shift_recovers_a_known_offset(tmp_path: Path) -> None:
    """A known +12.5 Hz modulation is estimated as +12.5 Hz (magnitude and sign); zero gives 0."""
    base = _write_fid(tmp_path / "a.fid")
    same = _write_fid(tmp_path / "b.fid", seed=11)
    moved = _write_fid(tmp_path / "c.fid", seed=11, offset_hz=12.5)
    ref = segment_traces([base])
    same_estimate = estimate_shift_hz(ref, segment_traces([same]), SW_HZ, span_hz=SPAN_HZ)
    assert same_estimate is not None
    assert same_estimate.shift_hz == pytest.approx(0.0, abs=0.3)
    assert same_estimate.peak_ratio > MIN_PEAK_RATIO
    assert same_estimate.row_mad_hz < 1.0

    moved_estimate = estimate_shift_hz(ref, segment_traces([moved]), SW_HZ, span_hz=SPAN_HZ)
    assert moved_estimate is not None
    assert moved_estimate.shift_hz == pytest.approx(12.5, abs=0.3)
    assert moved_estimate.peak_ratio > MIN_PEAK_RATIO


def test_drift_correction_triggers_on_hz_alone(tmp_path: Path) -> None:
    """The criterion looks at Hz only (``|d-Hz| > 1.5``): a ~3 Hz per-part drift must be corrected.

    The criterion used to be "0.005 ppm **and** 1.5 Hz", and 0.005 ppm @800.3 MHz = 4.00 Hz: a real
    3 Hz inter-part drift was let through untouched (on the VM the three repeated parts of d_015
    measured 2.9 / 3.0 Hz per part, which the old criterion never triggered on). ppm is still
    measured and recorded, it just does not gate anything.
    """
    # A: 31.25 Hz (= 0.039 ppm) -> corrected
    result = detect_group_drift(
        _two_segments(tmp_path / "a", 31.25), sw_hz=SW_HZ, sf_mhz=SF_MHZ
    )
    assert result.needs_shift[1] == pytest.approx(31.25, abs=0.5)
    assert result.offsets_ppm[1] == pytest.approx(0.039, abs=0.001)
    assert any("1.5 Hz" in line for line in result.reports)

    # B: 3.0 Hz but only 0.0037 ppm (under 0.005) -> still corrected (the d_015 magnitude)
    result = detect_group_drift(
        _two_segments(tmp_path / "b", 3.0), sw_hz=SW_HZ, sf_mhz=SF_MHZ
    )
    assert result.offsets_hz[1] == pytest.approx(3.0, abs=0.3)
    assert result.offsets_ppm[1] == pytest.approx(0.0037, abs=0.0005)
    assert result.needs_shift[1] == pytest.approx(3.0, abs=0.3)
    assert any("1.5 Hz" in line for line in result.reports)

    # C: 1.2 Hz is under 1.5 Hz -> not corrected (even though it is 0.006 ppm on a 13C direct axis)
    result = detect_group_drift(
        _two_segments(tmp_path / "c", 1.2), sw_hz=SW_HZ, sf_mhz=200.0
    )
    assert result.offsets_ppm[1] == pytest.approx(0.006, abs=0.001)
    assert result.needs_shift == {}
    assert any("1.5 Hz" in line for line in result.reports)


def test_out_of_range_offset_is_reported_not_corrected(tmp_path: Path) -> None:
    """A 0.6 ppm "drift" is beyond the search half-width (0.5 ppm): skipped, no correction value."""
    result = detect_group_drift(
        _two_segments(tmp_path, 480.0), sw_hz=SW_HZ, sf_mhz=SF_MHZ
    )
    assert result.needs_shift == {}
    assert result.skipped and "plausible" in result.skipped[0]
    assert any("plausible" in line for line in result.reports)


def test_parts_without_common_signal_are_not_corrected(tmp_path: Path) -> None:
    """Two parts without a common signal component (noise vs signal) give a too low confidence."""
    import nmrglue as ng

    signal = _write_fid(tmp_path / "seg1.fid")
    noise_file = tmp_path / "seg2.fid"
    dic = ng.pipe.create_empty_dic()
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = 1024
    dic["FDSPECNUM"] = 8
    dic["FDQUADFLAG"] = 0
    dic["FDF2QUADFLAG"] = 0
    rng = np.random.default_rng(4242)
    noise = rng.normal(0, 1.0, (8, 1024)) + 1j * rng.normal(0, 1.0, (8, 1024))
    ng.pipe.write(str(noise_file), dic, noise.astype(np.complex64), overwrite=True)

    result = detect_group_drift([[signal], [noise_file]], sw_hz=SW_HZ, sf_mhz=SF_MHZ)
    assert result.needs_shift == {}
    assert result.row_mad_hz[1] is not None
    assert result.row_mad_hz[1] > 2.0          # the traces disagree -> "not the same experiment"
    assert any("same experiment" in line for line in result.skipped)


def test_unreadable_reference_is_reported(tmp_path: Path) -> None:
    """An unreadable reference part is never guessed at: report it and clear the offsets."""
    broken = tmp_path / "seg1.fid"
    broken.write_bytes(b"not a fid")
    other = _write_fid(tmp_path / "seg2.fid")
    result = detect_group_drift([[broken], [other]], sw_hz=SW_HZ, sf_mhz=SF_MHZ)
    assert result.needs_shift == {}
    assert result.offsets_ppm == [None, None]
    assert result.reports and "reference" in result.reports[0].lower()
    assert segment_traces([broken]) is None


FID_COM = (
    "#!/bin/csh\n"
    "\n"
    "bruk2pipe -verb -in ./ser \\\n"
    "  -xN 1664  -yN 210  -xT 806  -yT 105 \\\n"
    "  -xMODE DQD  -yMODE Complex \\\n"
    "| nmrPipe -fn MULT -c 3.12500e+01 \\\n"
    "  -out ./d_015.fid -ov\n"
)


def test_insert_ps_shift_lands_just_before_mult() -> None:
    """Inserted before MULT, MULT kept, nothing else in the script touched."""
    patched, applied = insert_ps_shift(FID_COM, 12.5)
    assert applied
    lines = patched.splitlines()
    mult = next(i for i, line in enumerate(lines) if "-fn MULT" in line)
    assert lines[mult - 1] == "| nmrPipe -fn PS -rs 12.5Hz \\"
    assert lines[mult] == "| nmrPipe -fn MULT -c 3.12500e+01 \\"
    # parameter parsing is unaffected (xN/-out still there)
    assert parse_fid_com(patched)["xN"] == "1664"
    assert "-out ./d_015.fid -ov" in patched


def test_insert_ps_shift_is_idempotent() -> None:
    """Re-running never stacks: the second call only replaces the value, one PS -rs line stays."""
    first, _ = insert_ps_shift(FID_COM, 12.5)
    second, applied = insert_ps_shift(first, -8.25)
    assert applied
    assert second.count("-fn PS -rs") == 1
    assert "-fn PS -rs -8.25Hz" in second
    again, _ = insert_ps_shift(second, 0.0)
    assert again.count("-fn PS -rs") == 1
    assert "-fn PS -rs 0Hz" in again


def test_insert_ps_shift_without_mult_is_a_no_op() -> None:
    """A script without a MULT line (not a bruker -AUTO product) stays untouched, returns False."""
    text = "bruk2pipe -in ./ser \\\n  -out ./x.fid\n"
    patched, applied = insert_ps_shift(text, 5.0)
    assert applied is False
    assert patched == text


def test_non_finite_data_never_reaches_the_script(tmp_path: Path) -> None:
    """NaN/Inf guard: bad traces are dropped, all-bad means "unreadable", NaN never gets written."""
    import nmrglue as ng

    good = _write_fid(tmp_path / "good.fid")
    bad_file = tmp_path / "bad.fid"
    dic = ng.pipe.create_empty_dic()
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = 256
    dic["FDSPECNUM"] = 4
    dic["FDQUADFLAG"] = 0
    dic["FDF2QUADFLAG"] = 0
    values = np.full((4, 256), complex(float("nan"), 0.0), dtype=np.complex64)
    ng.pipe.write(str(bad_file), dic, values, overwrite=True)

    assert segment_traces([bad_file]) is None
    assert segment_traces([good]) is not None
    result = detect_group_drift([[good], [bad_file]], sw_hz=SW_HZ, sf_mhz=SF_MHZ)
    assert result.needs_shift == {}
    assert result.offsets_ppm[1] is None
    assert insert_ps_shift(FID_COM, float("nan")) == (FID_COM, False)
    assert insert_ps_shift(FID_COM, float("inf")) == (FID_COM, False)


def test_field_drift_record_round_trip(tmp_path: Path) -> None:
    """The check result is written to and read back from work/field_drift.json."""
    result = detect_group_drift(
        _two_segments(tmp_path / "seg", 31.25), sw_hz=SW_HZ, sf_mhz=SF_MHZ
    )
    path = write_field_drift_record(tmp_path, {"reference": 1, "round_1": result.as_dict()})
    assert path and Path(path).is_file()
    data = read_field_drift_record(tmp_path)
    assert data is not None
    assert data["round_1"]["shifted_parts_hz"] == {"2": pytest.approx(31.25, abs=0.5)}
    assert data["round_1"]["offsets_ppm"][1] == pytest.approx(0.039, abs=0.001)
    assert read_field_drift_record(tmp_path / "nowhere") is None
