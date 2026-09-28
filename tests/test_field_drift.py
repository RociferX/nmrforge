"""Tests for inter-part field-drift detection and conversion-time correction (2026-09-23).

Covers:

- the matched-filter estimator: a known time-domain modulation δ Hz ↔ the estimate δ (sign and
  magnitude), including the "-rs 30Hz"-style calibration;
- the criterion: **by FFT points** (late on 2026-09-23, d_018) -- when |Δ| is below 1 point
  (point width = SW / complex points) it only reports "cannot be resolved", and neither corrects
  nor writes "within the criterion"; 1.5 Hz serves only as the fallback floor for
  high-resolution data;
- pairing by **physical trace index** (``pool_trace_rows``): every part shares the same set of
  rows instead of each picking traces by energy;
- part consistency (``check_segment_consistency``): a differing NS -> warn (not a weighting
  issue); differing TD/SW/O1 -> merging is refused;
- parts whose correlation-peak/floor ratio is too low, whose per-trace scatter is too large (the
  measurement cannot separate the signal) or whose offset is beyond the plausible range are only
  reported, never rewritten; the wording must not say "the two parts are not the same experiment"
  (user 2026-09-24); the criterion line must state the criterion actually in force;
- ``fid.com`` ``PS -rs`` insertion: placed before ``MULT``, MULT kept, idempotent;
- ``field_drift.json`` record round trip.

Why the estimator is not the "strongest-peak position difference": with multi-peak data the
strongest peak hops lines (on the VM the three synthetic parts of d_015 gave +320/+350/+450 Hz
with the peak-position method against -2.9/-7.0 Hz with the matched filter). The sign and
convergence check on the real machine was also reproduced on the VM (two synthetic parts
+40.00 Hz -> residual 0.001 Hz; +3.0 Hz -> estimated +3.000 Hz).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.bruker_workflow import parse_fid_com
from workflow.field_drift import (
    IMPACT_FRACTION,
    check_segment_consistency,
    consistency_report_lines,
    criterion_rule_text,
    detect_group_drift,
    direct_linewidth_hz,
    estimate_shift_hz_pooled,
    insert_ps_shift,
    load_segment_planes,
    pool_trace_rows,
    read_field_drift_record,
    segment_traces,
    write_field_drift_record,
)

SW_HZ = 8000.0
SF_MHZ = 800.304          # 1H direct dimension (0.005 ppm = 4.00 Hz; criterion now in Hz)
SPAN_HZ = 0.5 * SF_MHZ    # matches the search half-width of detect_group_drift


#: Both parts share one and the same indirect-dimension modulation (same experiment: the t1
#: phase is a deterministic function of the row number) and only the noise is independent --
#: that is the physical model of "split into two parts in time". The per-row peak shapes must
#: be **all different** (in a real 2D/3D each t1 row is a weighted sum of several
#: direct-dimension peaks): if every row had exactly the same shape, the per-trace product
#: would degenerate to rank 1 and no scrambled-phase control could be fooled (the control
#: would be as high as the observation).
MODULATION_SEED = 13
PEAK_OFFSETS = (0.0, 96.0, 205.0)


def _write_fid(
    path: Path,
    *,
    n: int = 1024,
    rows: int = 8,
    peak_bins: float = 100.0,
    noise: float = 0.002,
    seed: int = 7,
    offset_hz: float = 0.0,
    sw_hz: float = SW_HZ,
    modulation_seed: int = MODULATION_SEED,
) -> Path:
    """Write a synthetic 2D fid (same layout as a real conversion product, parseable by
    ``_read_fid_raw``).

    ``offset_hz`` is realised as a non-integer-bin time-domain modulation (the same physical
    quantity as a "rigid frequency shift"); ``seed`` feeds the noise only (different between the
    parts), ``modulation_seed`` feeds the per-row complex amplitudes (the same in both parts --
    the t1 modulation of one and the same experiment).
    """
    import nmrglue as ng

    dic = ng.pipe.create_empty_dic()
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = n
    dic["FDSPECNUM"] = rows
    dic["FDQUADFLAG"] = 0
    dic["FDF2QUADFLAG"] = 0
    t = np.arange(n, dtype=float)
    modulation = np.random.default_rng(modulation_seed)
    amps = modulation.uniform(0.4, 1.0, (rows, len(PEAK_OFFSETS)))
    amps = amps * np.exp(2j * np.pi * modulation.random((rows, len(PEAK_OFFSETS))))
    data = np.zeros((rows, n), dtype=complex)
    for column, shift in enumerate(PEAK_OFFSETS):
        bins = peak_bins + shift + offset_hz / (sw_hz / n)
        line = np.exp(-t / 120.0) * np.exp(2j * np.pi * bins * t / n)
        data += amps[:, column][:, None] * line[None, :]
    rng = np.random.default_rng(seed)
    data = data + rng.normal(0, noise, (rows, n)) + 1j * rng.normal(0, noise, (rows, n))
    ng.pipe.write(str(path), dic, data.astype(np.complex64), overwrite=True)
    return path


def _two_segments(
    tmp_path: Path,
    offset_hz: float,
    *,
    n: int = 1024,
    rows: int = 8,
    sw_hz: float = SW_HZ,
    peak_bins: float = 100.0,
):
    base = _write_fid(tmp_path / "seg1.fid", n=n, rows=rows, sw_hz=sw_hz, peak_bins=peak_bins)
    shifted = _write_fid(
        tmp_path / "seg2.fid",
        n=n,
        rows=rows,
        seed=11,
        offset_hz=offset_hz,
        sw_hz=sw_hz,
        peak_bins=peak_bins,
    )
    return [[base], [shifted]]


def test_estimate_shift_recovers_a_known_offset(tmp_path: Path) -> None:
    """A known +12.5 Hz time-domain modulation -> estimate +12.5 Hz (magnitude/sign); a zero
    offset estimates 0.

    The coherent (pooled-trace) estimate plus the scrambled per-trace phase control: with a
    common signal the observed statistic is far above the control.
    """
    base = _write_fid(tmp_path / "a.fid")
    same = _write_fid(tmp_path / "b.fid", seed=11)
    moved = _write_fid(tmp_path / "c.fid", seed=11, offset_hz=12.5)
    ref_plane = load_segment_planes([base])
    same_plane = load_segment_planes([same])
    moved_plane = load_segment_planes([moved])
    assert ref_plane is not None and same_plane is not None and moved_plane is not None
    rows = pool_trace_rows([ref_plane, same_plane, moved_plane])
    ref = ref_plane[rows]
    same_rows = same_plane[rows]
    moved_rows = moved_plane[rows]

    same_estimate = estimate_shift_hz_pooled(ref, same_rows, SW_HZ, span_hz=SPAN_HZ)
    assert same_estimate is not None
    assert same_estimate.shift_hz == pytest.approx(0.0, abs=0.3)
    assert same_estimate.significant
    assert same_estimate.uncertainty_hz is not None
    assert same_estimate.uncertainty_hz < 1.0

    moved_estimate = estimate_shift_hz_pooled(ref, moved_rows, SW_HZ, span_hz=SPAN_HZ)
    assert moved_estimate is not None
    assert moved_estimate.shift_hz == pytest.approx(12.5, abs=0.3)
    assert moved_estimate.significant
    # the control statistic grows more slowly with the trace count than the observation:
    # on the same rows the observation must be clearly above the control
    assert moved_estimate.stat > moved_estimate.null_stat


def _structured_planes(rows: int = 8, *, offset_hz: float = 25.0, good: int | None = None):
    """Synthesise a (ref, part) pair: the per-row complex amplitudes differ, the two parts share
    one and the same t1 modulation, and part is shifted as a whole by offset_hz.

    ``good`` gives a signal to only the first few rows (the rest are pure noise) -- this
    reproduces the real-machine structure where "the rows come in blocks along the slices and
    some rows hold no comparable signal".
    """
    n = 1024
    t = np.arange(n, dtype=float)
    mod = np.random.default_rng(13)
    amps = mod.uniform(0.5, 2.0, (rows, 3)) * np.exp(2j * np.pi * mod.random((rows, 3)))
    noise = 0.02

    def plane(seed: int, shift_hz: float):
        sig = np.zeros((rows, n), dtype=complex)
        for column, peak in enumerate((0.0, 96.0, 205.0)):
            bins = 100.0 + peak + shift_hz / (SW_HZ / n)
            line = np.exp(-t / 120.0) * np.exp(2j * np.pi * bins * t / n)
            sig += amps[:, column][:, None] * line[None, :]
        peak = np.abs(np.fft.fft(sig, axis=-1)).max(axis=-1, keepdims=True)
        sig = sig * (6.0 * noise * np.sqrt(2 * n) / np.maximum(peak, 1e-30))
        if good is not None:
            sig[good:] = 0.0
        local = np.random.default_rng(seed)
        return sig + local.normal(0, noise, (rows, n)) + 1j * local.normal(0, noise, (rows, n))

    return plane(7, 0.0), plane(11, offset_hz)


def test_stability_uses_resampling_not_one_even_odd_split() -> None:
    """The stability threshold must use **random half-split resampling**, not a single even/odd
    split.

    Measured on the real machine with d_018: the even/odd split gave −32/−27 Hz (it looked
    "very stable") while 10 random splits scattered over −190…+140 Hz (the rows come in blocks
    along the slices, so even/odd happen to land on the same side) ⇒ judging by even/odd alone
    reports a false stability and the data are moved on that basis. This reproduces the same
    phenomenon with a synthetic set where "half the rows carry a signal, half are pure noise".
    """
    ref, part = _structured_planes(good=4)
    pooled = estimate_shift_hz_pooled(ref, part, SW_HZ, span_hz=SPAN_HZ)
    assert pooled is not None and pooled.uncertainty_hz is not None
    mask = np.arange(ref.shape[0]) % 2 == 0
    even = estimate_shift_hz_pooled(ref[mask], part[mask], SW_HZ, span_hz=SPAN_HZ)
    odd = estimate_shift_hz_pooled(ref[~mask], part[~mask], SW_HZ, span_hz=SPAN_HZ)
    assert even is not None and odd is not None
    split = abs(even.shift_hz - odd.shift_hz) / 2.0
    assert split < 3.0                              # a single split "looks very stable"
    assert pooled.uncertainty_hz > 3.0 * split      # resampling gives the real uncertainty


def test_linewidth_is_measured_on_the_median_spectrum(tmp_path: Path) -> None:
    """Line-width convention: the full width at half maximum of the **point-by-point median** of
    the top traces' magnitude spectra (minus that spectrum's own median floor).

    The synthetic line width is set by the decay constant (``exp(-t/120)`` @ SW 8000 / 1024
    points = 7.81 Hz/point); the median-spectrum convention must match the true magnitude, and
    it must not be "measure per trace and then take the median" (at low SNR that convention is
    set by the noise).
    """
    fid = _write_fid(tmp_path / "lw.fid", peak_bins=100.0)
    plane = load_segment_planes([fid])
    assert plane is not None
    width = direct_linewidth_hz(plane, SW_HZ)
    assert width is not None
    # exp(-t/120)·8000/1024: line width ≈ 2·(1/π)·(SW·120/1024) ≈ the order of 2 point widths
    point_hz = SW_HZ / 1024
    assert 2.0 * point_hz < width < 8.0 * point_hz


def test_criterion_is_a_fraction_of_the_line_width(tmp_path: Path) -> None:
    """Criterion = max(1.5 Hz, 0.2×line width) (user 2026-09-24: "if the line-width convention is
    more reasonable, change it").

    - 31.25 Hz (≈ 0.8 line width) -> over the criterion -> correct;
    - 3.0 Hz (≈ 0.08 line width) -> only writes "within the criterion", the data are not moved.
    """
    result = detect_group_drift(
        _two_segments(tmp_path / "a", 31.25), sw_hz=SW_HZ, sf_mhz=SF_MHZ
    )
    assert result.criterion_basis == "linewidth"
    assert result.linewidth_hz is not None and result.linewidth_hz > 0
    assert result.criterion_hz == pytest.approx(
        max(1.5, IMPACT_FRACTION * result.linewidth_hz), abs=1e-6
    )
    assert result.needs_shift[1] == pytest.approx(31.25, abs=0.5)
    assert result.offsets_ppm[1] == pytest.approx(0.039, abs=0.001)
    assert result.trusted == [1]
    assert any("线宽" in line for line in result.reports)

    small = detect_group_drift(
        _two_segments(tmp_path / "b", 3.0), sw_hz=SW_HZ, sf_mhz=SF_MHZ
    )
    assert small.offsets_hz[1] == pytest.approx(3.0, abs=0.5)
    assert small.needs_shift == {}
    assert small.trusted == [1]
    assert any("判据以内" in line for line in small.reports)


def test_coarse_resolution_uses_the_line_width_criterion(tmp_path: Path) -> None:
    """d_018 regression: at SW 11904.76 Hz / 178 complex points (66.9 Hz/point) the criterion is
    **no longer equal to one FFT point** but ``max(1.5 Hz, 0.2×line width)``; a 4 Hz drift is
    within the criterion -> no correction (relative broadening ≪ 1%).

    The old convention set the criterion to 66.88 Hz (one point) and tied "correct or not" to
    the digital resolution; the point width is now only a reference quantity in the report.
    """
    sw_hz = 11904.7619
    result = detect_group_drift(
        _two_segments(tmp_path, 4.0, n=178, sw_hz=sw_hz, peak_bins=30.0),
        sw_hz=sw_hz,
        sf_mhz=600.1351,
    )
    assert result.point_hz == pytest.approx(66.88, abs=0.2)
    assert result.criterion_basis == "linewidth"
    assert result.linewidth_hz is not None
    assert result.criterion_hz == pytest.approx(
        max(1.5, IMPACT_FRACTION * result.linewidth_hz), abs=1e-6
    )
    assert result.offsets_hz[1] == pytest.approx(4.0, abs=1.0)
    assert result.needs_shift == {}
    assert result.trusted == [1]
    line = next(text for text in result.reports if "判据以内" in text)
    assert "线宽" in line


def test_within_criterion_report_quotes_the_real_criterion(tmp_path: Path) -> None:
    """The criterion line must state the criterion **actually in force** (the line-width
    convention), not the fallback floor of 1.5 Hz.

    2026-09-24 re-check: writing "within the 1.5 Hz criterion" in a report on 66.9 Hz/point data
    contradicts the "point width / line width" in the same report.
    """
    sw_hz = 11904.7619
    result = detect_group_drift(
        _two_segments(tmp_path, 0.5, n=178, sw_hz=sw_hz, peak_bins=30.0),
        sw_hz=sw_hz,
        sf_mhz=600.1351,
    )
    assert result.criterion_basis == "linewidth"
    line = next(text for text in result.reports if "判据以内" in text)
    assert f"{result.criterion_hz:.2f} Hz" in line   # the criterion in force, not 1.5 Hz
    assert "线宽" in line


def test_audit_rule_quotes_the_effective_criterion(tmp_path: Path) -> None:
    """The QC audit ``detection_rule`` must state the criterion **actually in force** (the
    line-width convention) and must not keep the old one.

    2026-09-24 re-check: after the criterion changed from "1 FFT point" to
    ``max(1.5 Hz, 0.2×line width)``, an audit record that still writes "over 1 FFT point" leaves
    the record disconnected from the behaviour.
    """
    result = detect_group_drift(
        _two_segments(tmp_path, 25.0), sw_hz=SW_HZ, sf_mhz=SF_MHZ
    )
    rule = criterion_rule_text(result)
    assert f"{result.criterion_hz:.2f} Hz" in rule          # the value of the actual criterion
    assert "线宽" in rule                                    # the source of the convention
    assert "FFT 点" not in rule


def test_too_few_traces_is_reported_as_no_stability_check(tmp_path: Path) -> None:
    """With too few traces (<8, so resampling is impossible) say plainly "too few comparable
    traces"; do not write "scatter ±0.0 Hz over the criterion".

    2026-09-24 re-check: on 4–7 traces the old wording read "scatter ±0.0 Hz, over the 7.81 Hz
    criterion" -- self-contradictory (0 cannot exceed 7.81) -- and it turned "no test was done"
    into "the test failed".
    """
    result = detect_group_drift(
        _two_segments(tmp_path, 25.0, rows=6), sw_hz=SW_HZ, sf_mhz=SF_MHZ
    )
    assert result.traces_used == 6
    assert result.uncertainty_hz[1] is None
    assert result.trusted == []
    assert result.needs_shift == {}
    reason = "\n".join(result.skipped)
    assert "可比迹" in reason and "太少" in reason
    assert "±0.0" not in reason
    assert "不代表各段不是同一次实验" in "\n".join(result.reports)


def test_out_of_range_offset_is_reported_not_corrected(tmp_path: Path) -> None:
    """A 0.6 ppm "drift" is beyond the search half-width (0.5 ppm): it only goes into
    ``skipped``, with no correction value."""
    result = detect_group_drift(
        _two_segments(tmp_path, 480.0), sw_hz=SW_HZ, sf_mhz=SF_MHZ
    )
    assert result.needs_shift == {}
    assert result.skipped and "合理范围" in result.skipped[0]
    assert any("合理范围" in line for line in result.reports)


def test_parts_without_common_signal_are_not_corrected(tmp_path: Path) -> None:
    """When the two parts share no signal component (signal vs pure noise) the coherent
    statistic cannot beat the control -> no correction."""
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
    assert result.trusted == []
    assert result.quality[1] is not None                 # measured, just not over the control
    assert any("测不出" in line for line in result.skipped)
    # the wording may only describe "the measurement cannot be done"; it must not assert
    # "the two parts are not the same experiment" (pointed out by the user 2026-09-24)
    assert not any("看起来不是同一次实验" in line for line in result.reports)
    assert any("不代表各段不是同一次实验" in line for line in result.reports)
    # a skipped part leaves a noise-level estimate behind: it must not be used to write
    # "largest offset X Hz, within the criterion"
    assert not any("判据以内" in line for line in result.reports)
    assert any("没有任何一段给出可信测量" in line for line in result.reports)
    assert result.max_abs_hz() is None
    assert result.within_criterion() is False


def test_unreadable_reference_is_reported(tmp_path: Path) -> None:
    """When the reference part cannot be read, do not guess: report it and clear the offsets."""
    broken = tmp_path / "seg1.fid"
    broken.write_bytes(b"not a fid")
    other = _write_fid(tmp_path / "seg2.fid")
    result = detect_group_drift([[broken], [other]], sw_hz=SW_HZ, sf_mhz=SF_MHZ)
    assert result.needs_shift == {}
    assert result.offsets_ppm == [None, None]
    assert result.reports and "基准" in result.reports[0]
    assert segment_traces([broken]) is None


def _write_rowwise_fid(
    path: Path,
    *,
    amps,
    bins,
    offset_hz: float = 0.0,
    n: int = 256,
    noise: float = 0.0005,
    seed: int = 3,
) -> Path:
    """A 2D fid with an independent peak position per row (simulating data where the high-energy
    trace of each plane differs); the row amplitudes can be given row by row."""
    import nmrglue as ng

    dic = ng.pipe.create_empty_dic()
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = n
    dic["FDSPECNUM"] = len(amps)
    dic["FDQUADFLAG"] = 0
    dic["FDF2QUADFLAG"] = 0
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=float)
    rows = [
        amp * np.exp(-t / 60.0) * np.exp(2j * np.pi * (bin_ + offset_hz / (SW_HZ / n)) * t / n)
        for amp, bin_ in zip(amps, bins)
    ]
    data = np.asarray(rows, dtype=complex)
    data = data + rng.normal(0, noise, data.shape) + 1j * rng.normal(0, noise, data.shape)
    ng.pipe.write(str(path), dic, data.astype(np.complex64), overwrite=True)
    return path


def test_pairing_uses_the_same_physical_rows(tmp_path: Path) -> None:
    """High-energy traces are paired by **physical row**: even when the two parts' "strong
    traces" are completely different, the offsets are still estimated on the same set of rows.

    The old implementation took the top few traces of each part by energy: the i-th selected
    trace was one indirect-dimension plane in part A and another plane in part B, so the
    "cross-correlation" multiplied two different signals -- the estimate (or the verdict "not
    the same experiment") was pure pairing mismatch. Here part A's strong traces are the first
    4 rows and part B's the last 4, and the row peak positions differ from row to row.
    """
    rows = 40
    bins = [40.0 + 3.0 * index for index in range(rows)]
    strong_first = [10.0 if index < 4 else 0.05 for index in range(rows)]
    strong_last = [0.05 if index < rows - 4 else 10.0 for index in range(rows)]
    ref = _write_rowwise_fid(tmp_path / "a.fid", amps=strong_first, bins=bins)
    part = _write_rowwise_fid(
        tmp_path / "b.fid", amps=strong_last, bins=bins, offset_hz=62.5, seed=5
    )
    ref_plane = load_segment_planes([ref])
    part_plane = load_segment_planes([part])
    assert ref_plane is not None and part_plane is not None
    chosen = pool_trace_rows([ref_plane, part_plane])
    # pairing follows the row number (A's strong traces are 0-3, B's are 36-39), not each part
    # picking its own highest-energy traces
    assert chosen.size == rows
    assert set(chosen.tolist()) == set(range(rows))
    result = detect_group_drift([[ref], [part]], sw_hz=SW_HZ, sf_mhz=SF_MHZ)
    assert result.traces_used == rows
    # the estimate must land on the true value (the row peak positions differ by thousands of
    # Hz: a pairing mismatch would give completely unrelated numbers)
    assert result.offsets_hz[1] == pytest.approx(62.5, abs=1.0)
    assert result.trusted == [1]
    assert result.skipped == []


def _write_acqus(
    raw: Path,
    *,
    ns: int = 32,
    td: int = 356,
    sw: float = 11904.762,
    o1: float = 2350.5,
    sfo1: float = 600.1351,
    grpdly: float = 68.0,
    fnmode: int = 0,
    ds: int = 4,
    pulprog: str = "<XH.3D.CNH_top3.shex>",
) -> Path:
    """Write a minimal acqus (numeric and angle-bracket values, same format as a real dataset)."""
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "acqus").write_text(
        "##TITLE= test\n"
        f"##$NS= {ns}\n"
        f"##$DS= {ds}\n"
        f"##$TD= {td}\n"
        f"##$SW_h= {sw}\n"
        f"##$O1= {o1}\n"
        f"##$SFO1= {sfo1}\n"
        f"##$GRPDLY= {grpdly}\n"
        f"##$FnMODE= {fnmode}\n"
        f"##$PULPROG= {pulprog}\n",
        encoding="utf-8",
    )
    return raw


def test_segment_consistency_reports_ns_difference(tmp_path: Path) -> None:
    """A differing NS (32/16/32/32/16 for d_018) -> warn; but it must not be written as a
    "weighting problem with equal-weight summation".

    User 2026-09-24: "adding the fids directly has no weighting issue either" -- per part
    S∝NS and σ∝√NS, so the matched weight w ∝ S/σ² is constant and direct summation is the
    optimal combination for this data. A differing NS only means the parts were not acquired
    equally long (the per-trace noise differs by 1/√NS).
    """
    first = _write_acqus(tmp_path / "s1", ns=32)
    second = _write_acqus(tmp_path / "s2", ns=16)
    report = check_segment_consistency([first, second])
    assert report["available"] == 2
    assert report["blocking"] == []
    assert [entry["key"] for entry in report["warnings"]] == ["NS"]
    assert report["warnings"][0]["values"] == [32, 16]
    text = "\n".join(consistency_report_lines(report))
    assert "32/16" in text
    assert "不是权重错误" in text
    assert "匹配" in text
    assert "1/√NS" in text
    # the old wording (calling a differing NS a weighting defect) must not appear any more
    assert "高于其信噪比的权重" not in text


def test_segment_consistency_blocks_different_layouts(tmp_path: Path) -> None:
    """Differing TD/SW/O1 = not the same experiment: report "merging is refused" instead of
    leaving only a warning line."""
    first = _write_acqus(tmp_path / "s1", td=356)
    second = _write_acqus(tmp_path / "s2", td=512)
    report = check_segment_consistency([first, second])
    assert [entry["key"] for entry in report["blocking"]] == ["TD"]
    assert report["warnings"] == []
    text = "\n".join(consistency_report_lines(report))
    assert "TD" in text and "拒绝合并" in text
    # the sweep width differs only in write precision (11904.762 vs 11904.7619047619) -> does
    # not count as a different experiment
    third = _write_acqus(tmp_path / "s3", sw=11904.7619047619)
    exact = _write_acqus(tmp_path / "s4", td=356)
    assert check_segment_consistency([third, exact])["blocking"] == []


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
    """Inserted before MULT, MULT kept, and the rest of the script untouched character by
    character."""
    patched, applied = insert_ps_shift(FID_COM, 12.5)
    assert applied
    lines = patched.splitlines()
    mult = next(i for i, line in enumerate(lines) if "-fn MULT" in line)
    assert lines[mult - 1] == "| nmrPipe -fn PS -rs 12.5Hz \\"
    assert lines[mult] == "| nmrPipe -fn MULT -c 3.12500e+01 \\"
    assert parse_fid_com(patched)["xN"] == "1664"
    assert "-out ./d_015.fid -ov" in patched


def test_insert_ps_shift_is_idempotent() -> None:
    """Re-running does not accumulate: the second run only replaces the value, so there is always
    exactly one ``PS -rs`` line."""
    first, _ = insert_ps_shift(FID_COM, 12.5)
    second, applied = insert_ps_shift(first, -8.25)
    assert applied
    assert second.count("-fn PS -rs") == 1
    assert "-fn PS -rs -8.25Hz" in second
    again, _ = insert_ps_shift(second, 0.0)
    assert again.count("-fn PS -rs") == 1
    assert "-fn PS -rs 0Hz" in again


def test_insert_ps_shift_without_mult_is_a_no_op() -> None:
    """A script with no MULT line (not a bruker -AUTO product) is left alone; returns False."""
    text = "bruk2pipe -in ./ser \\n  -out ./x.fid\n"
    patched, applied = insert_ps_shift(text, 5.0)
    assert applied is False
    assert patched == text


def test_non_finite_data_never_reaches_the_script(tmp_path: Path) -> None:
    """NaN/Inf guard: bad traces are dropped, all-bad means "cannot be read", and NaN never
    reaches fid.com."""
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
    """The check result is written to / read back from work/field_drift.json."""
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


def test_criterion_text_states_the_floor_when_it_dominates() -> None:
    """B10: the criterion is max(1.5 Hz, 0.2×line width) -- below a 7.5 Hz line width the
    recorded formula must state that the floor takes over."""
    from workflow.field_drift import GroupDriftResult, criterion_text

    narrow = GroupDriftResult(
        criterion_basis="linewidth", linewidth_hz=3.0, criterion_hz=1.5
    )
    text = criterion_text(narrow)
    assert "0.60" in text  # 3.0 × 0.2
    assert "1.5" in text  # the floor

    wide = GroupDriftResult(
        criterion_basis="linewidth", linewidth_hz=20.0, criterion_hz=4.0
    )
    assert "4.00" in criterion_text(wide)


def test_identity_claim_matches_the_old_english_wording() -> None:
    """B12: the old English sentence was "the parts do not look like the same experiment"."""
    from workflow.field_drift import is_identity_claim

    assert is_identity_claim("part 2: the parts do not look like the same experiment (corr)")
    assert is_identity_claim("第 2 段:两段看起来不是同一次实验")
    assert not is_identity_claim("part 2: no drift could be measured")


def test_pool_trace_rows_excludes_rows_that_are_not_finite_everywhere() -> None:
    """D: the contract is "traces finite in every part" -- non-common rows must be excluded,
    not merely sorted last."""
    import numpy as np

    from workflow.field_drift import pool_trace_rows

    first = np.zeros((5, 8), dtype=complex)
    second = np.zeros((5, 8), dtype=complex)
    first[:, 0] = 1.0
    second[:, 0] = 1.0
    second[4, 0] = np.nan  # row 5 is non-finite in the second part
    keep = pool_trace_rows([first, second], cap=10)
    assert 4 not in keep
    assert keep.size == 4
