"""fid.com cross-check / patch tests (following the NMRFlow implementation)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.bruker_workflow import (
    apply_fid_com_overrides,
    carrier_audit,
    carrier_overrides,
    carrier_patch_notes,
    cross_check_fid_com,
    expected_values,
    gamma_mapped_car,
    parse_fid_com,
    patch_fid_com,
    patch_fid_out_name,
    physical_direct_points,
    sweep_width_audit,
    sweep_width_log_lines,
)
from core.data.bruker_reader import read_dataset
from core.data.internal_data_model import AxisRole, Dimension, Experiment

FID_COM = (
    "bruk2pipe -in ./ser \\\n"
    "  -bad 0.0 -aswap -AMX -decim 32 -dspfvs 21 -grpdly 48 \\\n"
    "  -xN 1024 -yN 128 -xT 512 -yT 64 \\\n"
    "  -xSW 10000.000 -ySW 2834.467 \\\n"
    "  -xOBS 599.894 -yOBS 60.798 \\\n"
    "  -xCAR 4.703 -yCAR 118.500 \\\n"
    "  -xLAB 1H -yLAB 15N -xMODE DQD -yMODE Complex \\\n"
    "  -out fid\n"
)


def test_parse_fid_com() -> None:
    parsed = parse_fid_com(FID_COM)
    assert parsed["xN"] == "1024"
    assert parsed["yLAB"] == "15N"
    assert parsed["decim"] == "32"


def test_expected_values_2d(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    values = expected_values(exp)
    assert values["xN"][0] == 2048.0
    assert values["xT"][0] == 1024.0
    assert values["xLAB"][0] == "1H"
    assert values["yN"][0] == 256.0
    assert values["yMODE"][0] == "States-TPPI"  # FnMODE=5 -> the specific keyword (2026-09-24)
    assert values["decim"][0] == 32.0
    assert values["dspfvs"][0] == 21.0
    assert values["grpdly"][0] == 48.0


def test_expected_values_3d(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hnca_3d")
    values = expected_values(exp)
    assert values["xN"][0] == 2048.0
    assert values["yN"][0] == 96.0
    assert values["zN"][0] == 128.0
    assert values["yMODE"][0] == "States-TPPI"  # F2=acqu2s FnMODE=5
    assert values["zMODE"][0] == "States"  # F1=acqu3s FnMODE=4


def test_cross_check_finds_diff(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    parsed = parse_fid_com(FID_COM)
    warnings = cross_check_fid_com(parsed, exp)
    assert any("xN" in w for w in warnings)
    assert any("yN" in w for w in warnings)


def test_patch_fid_com(tmp_path: Path, bruker_dir: Path) -> None:
    """``-xN`` verifies the script's present value first: it is rewritten, with a warning, only
    when it is shorter than the direct-dimension TD and the row length can be solved from the
    file.

    2026-09-24 (maintainer): the ser row length and sample word size are decided by TopSpin case
    by case, **not by a fixed byte rule**; the values ``bruker -AUTO`` writes are correct on
    real
    d_015 (1664) and real 2D NUS (1024) and must not be recomputed away.
    """
    exp, data_dir = _padded_2d(tmp_path)  # TD=1612, physical row 1664, 210 rows
    patched, warnings = patch_fid_com(FID_COM, exp, data_dir=data_dir)
    assert "-xN 1024" in patched
    assert "-xN 1664" not in patched

    assert "-yT " in patched


def _padded_2d(tmp_path: Path, td: int = 1612, rows: int = 210) -> tuple[Experiment, Path]:
    """Build a 2D dataset directory whose direct-dimension row is not aligned (padded by
    ``serPadSize``).
    """
    data_dir = tmp_path / "raw"
    data_dir.mkdir(parents=True)
    row = ((td + 127) // 128) * 128
    with open(data_dir / "ser", "wb") as handle:
        handle.truncate(rows * row * 8)
    exp = Experiment(
        dataset_id="d_015",
        source_path=data_dir,
        ndim=2,
        dimensions=[
            Dimension(logical_axis="F2", nucleus="1H", td=td, role=AxisRole.DIRECT),
            Dimension(logical_axis="F1", nucleus="15N", td=rows),
        ],
        acquisition_parameters={
            # real d_015 shape: DTYPA=2 => 8 bytes per sample value (16 bytes per complex point);
            # the row length counts "sample values" (the -xN convention): 1664 values x 8 B =
            # 13312 B (a multiple of 1024)
            "acqus": {"DTYPA": 2, "BYTORDA": 0},
            "acqu2s": {"TD": rows, "FnMODE": 5},
        },
    )
    return exp, data_dir


def test_physical_direct_points_padded_and_aligned(tmp_path: Path) -> None:
    """2026-09-23: two layouts -- d_015 (TD=1612 -> row 1664) and the regular one (TD=2048,
    already aligned).
    """
    exp, data_dir = _padded_2d(tmp_path)
    assert physical_direct_points(exp, data_dir) == 1664

    exp2, data_dir2 = _padded_2d(tmp_path / "aligned", td=2048, rows=128)
    assert physical_direct_points(exp2, data_dir2) == 2048


def test_physical_direct_points_prefers_the_physical_row_over_a_smaller_divisor(
    tmp_path: Path,
) -> None:
    """Review C3 2026-09-24: when a smaller candidate also divides the file size, the smaller
    value must not be taken.

    The old implementation tried ``acqus TD`` first in ascending order: with ``TD=2000``, a
    physical row of 2048 and 125 rows, ``125 x 2048 = 256000`` is also divisible by 2000 =>
    it silently returned 2000 (the "the contents are all wrong" case it warned about itself).
    Now the value is solved as "the multiple of 1024 not below TD that divides the file size"
    => 2048.
    """
    exp, data_dir = _padded_2d(tmp_path, td=2000, rows=125)
    assert physical_direct_points(exp, data_dir) == 2048


def test_physical_direct_points_handles_eight_byte_samples(tmp_path: Path) -> None:
    """DTYPE=1 (float64) => 8 bytes per sample value => the row is aligned to 1024/8 = 128
    values.
    """
    exp, data_dir = _padded_2d(tmp_path, td=1536, rows=200)
    exp.acquisition_parameters["acqus"]["DTYPE"] = 1
    exp.acquisition_parameters["acqus"].pop("DTYPA", None)
    # 1536 lies on the 128-value grid => row length 1536 values x 8 B = 12288 B
    # (a multiple of 1024)
    (Path(data_dir) / "ser").write_bytes(b"\x00" * (200 * 1536 * 8))
    assert physical_direct_points(exp, data_dir) == 1536

    # TD=1630 does not lie on the 128-value grid => padded to 1664
    exp2, data_dir2 = _padded_2d(tmp_path / "unaligned", td=1630, rows=100)
    exp2.acquisition_parameters["acqus"]["DTYPE"] = 1
    exp2.acquisition_parameters["acqus"].pop("DTYPA", None)
    (Path(data_dir2) / "ser").write_bytes(b"\x00" * (100 * 1664 * 8))
    assert physical_direct_points(exp2, data_dir2) == 1664


def test_patch_fid_com_keeps_padded_direct_row(tmp_path: Path) -> None:
    """Measured on d_015: acqus TD=1612 but the ser row is 1664 -> -xN must not become 1612.

    Correcting it from acqus makes bruk2pipe read the file with a wrong stride (same output
    size,
    no error, contents all wrong). -xT stays TD//2 = 806 (the effective points; the padding is
    dropped here).
    """
    exp, data_dir = _padded_2d(tmp_path)
    text = (
        "bruk2pipe -in ./ser \\\n"
        "  -xN              1664  -yN               210  \\\n"
        "  -xT               806  -yT               105  \\\n"
        "  -out fid\n"
    )
    patched, warnings = patch_fid_com(text, exp, data_dir=data_dir)
    parsed = parse_fid_com(patched)
    assert parsed["xN"] == "1664"  # the physical row length, not acqus TD=1612
    assert parsed["xT"] == "806"  # effective points = TD//2
    assert parsed["yN"] == "210"
    assert not any("xN" in w for w in warnings), warnings


def test_patch_fid_com_unverified_row_keeps_value(tmp_path: Path) -> None:
    """When the row length cannot be derived from the file size (not divisible) there is no xN
    target and the fid.com value is kept.
    """
    exp, data_dir = _padded_2d(tmp_path)
    with open(data_dir / "ser", "r+b") as handle:
        handle.truncate(12345)
    text = "bruk2pipe -in ./ser \\\n  -xN 999 -out fid\n"
    patched, warnings = patch_fid_com(text, exp, data_dir=data_dir)
    assert parse_fid_com(patched)["xN"] == "999"

    assert any("xN" in w and "999" in w for w in warnings), warnings


def test_patch_fid_out_name_single(bruker_dir: Path) -> None:
    """0.2.163-patch13: single-file output name test.fid -> {dataset_id}.fid (the automatic and
    manual paths agree).
    """
    exp = read_dataset(bruker_dir / "hsqc_2d")
    text = "bruk2pipe -in ./ser \\n  -out ./test.fid\n"
    patched, warnings = patch_fid_com(text, exp)
    assert f"-out ./{exp.dataset_id}.fid" in patched
    assert any("out" in w and "test.fid" in w for w in warnings)


def test_patch_fid_out_name_slice_kept() -> None:
    """The sliced output form (fid/test%03d.fid) keeps bruker's naming and is not rewritten."""
    text = "bruk2pipe -in ./ser \\n  -out fid/test%03d.fid\n"
    patched, warnings = patch_fid_out_name(text, "d_001")
    assert "-out fid/test%03d.fid" in patched
    assert warnings == []


def test_apply_fid_com_overrides() -> None:
    """Manual parameter overrides: only existing parameters are replaced, the output name and
    structure stay untouched, unknown keys are reported as skipped.
    """
    text = "bruk2pipe -in ./ser \\n  -ySW 2834.467 -yCAR 118.500 \\n  -out ./d_001.fid\n"
    patched, warnings = apply_fid_com_overrides(text, {"ySW": "2800.000", "nope": "1"})
    assert "-ySW 2800.000" in patched
    assert "-yCAR 118.500" in patched
    assert "-out ./d_001.fid" in patched
    assert any("ySW" in w and "已应用" in w for w in warnings)
    assert any("nope" in w and "未找到" in w for w in warnings)


def test_patch_fid_com_nus_keeps_x_force_grid(
    bruker_dir: Path,
) -> None:
    """0.2.195: under NUS, xN/xT keep the fid.com values (the ser row size after padding),
    yN/zN are corrected to the NusTD grid, and nusExpand is forced onto the same grid.
    """
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(bruker_dir / "nus_3d")
    text = (
        "nusExpand.tcl -mode bruker -sampleCount 2 -off 0 \\\n"
        " -in ./ser -out ./ser_full -sample ./nuslist\n\n"
        "bruk2pipe -in ./ser_full \\\n"
        "  -xN 1024 -yN 166 -zN 4702 -xT 454 -yT 83 -zT 2351 \\\n"
        "  -out fid\n"
    )
    patched, warnings = patch_fid_com(text, exp)

    assert "-xN 1024" in patched
    assert "-xT 454" in patched
    assert "-yN 166" in patched
    assert "-zN 4702" in patched

    from backend.bruker_workflow import _effective_td

    td = _effective_td(exp)
    first = patched.splitlines()[0]
    assert f"-yT {td[1] // 2}" in first
    assert f"-zT {td[2] // 2}" in first
    assert any("NUS 数据展开网格" in w for w in warnings)


def test_patch_nus_expand_count() -> None:
    from backend.bruker_workflow import patch_nus_expand_count

    text = (
        "nusExpand.tcl -mode bruker -sampleCount 2 -off 0 \\n"
        " -in ./ser -out ./ser_full -sample ./nuslist\n\n"
        "nusExpand.tcl -mask -noexpand -sampleCount 2 -in ./test.fid \\n"
        " -out ./mask.fid -sample ./nuslist\n"
    )
    patched, warnings = patch_nus_expand_count(text, 700)
    assert len(warnings) == 2
    assert "-sampleCount 700" in patched
    assert "-sampleCount 2" not in patched
    assert all("sampleCount" in w for w in warnings)


def _stale_indirect_sweep_width(tmp_path: Path, bruker_dir: Path) -> Path:
    """Rewrite the 2D fixture's indirect dimension into the "SW_h copied in as a constant" case
    of BMRB deposited data.
    """
    import shutil

    dst = tmp_path / "stale_sw"
    shutil.copytree(bruker_dir / "hsqc_2d", dst)
    acqu2s = dst / "acqu2s"
    keep = [
        line
        for line in acqu2s.read_text(encoding="utf-8").splitlines()
        if not line.startswith(("##$SW_h=", "##$SFO1="))
    ]
    keep += ["##$SW= 30", "##$SW_h= 2000", "##$SFO1= 60.81782065611"]
    acqu2s.write_text("\n".join(keep) + "\n", encoding="utf-8", newline="\n")
    return dst


def test_patch_fid_com_rewrites_a_stale_indirect_sweep_width(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """When ``SW_h`` contradicts ``SW x SFO1``, the ``-ySW`` of fid.com is rewritten from the
    ppm convention.

    2026-09-24: the ``acqu2s`` of BMRB deposited data writes ``SW=30 ppm`` and ``SW_h=2000 Hz``
    (the same 2000 Hz appears in 600/800 MHz data => a constant copied across); the true value
    is 1824.5346 Hz.
    """
    exp = read_dataset(_stale_indirect_sweep_width(tmp_path, bruker_dir))
    assert expected_values(exp)["ySW"][0] == pytest.approx(1824.5346196833, rel=1e-9)

    patched, warnings = patch_fid_com("bruk2pipe -in ./ser \\\n  -ySW 2000.000 -yT 128\n", exp)
    assert "-ySW 1824.535" in patched
    assert any("ySW" in w for w in warnings)


def test_sweep_width_audit_covers_only_the_decided_axes(tmp_path: Path, bruker_dir: Path) -> None:
    """The record/log lists only dimensions whose value was re-judged (or whose SW_h is
    missing); a self-consistent direct dimension does not appear.
    """
    exp = read_dataset(_stale_indirect_sweep_width(tmp_path, bruker_dir))
    entries = sweep_width_audit(exp)
    assert [entry["axis"] for entry in entries] == ["F1"]
    assert entries[0]["source"] == "ppm_x_sfo"
    assert entries[0]["sw_hz_raw"] == 2000.0
    assert entries[0]["sw_ppm"] == 30.0
    assert entries[0]["sw_hz_used"] == pytest.approx(1824.5346196833, rel=1e-9)
    lines = sweep_width_log_lines(exp)
    assert lines == [entries[0]["note"]]
    assert "F2" not in lines[0]


def _or8c_like() -> Experiment:
    """OR8C_600 shape: 1H carrier 4.7133 ppm, water peak (TE=299.98 K) = 4.754 ppm; 15N carrier
    118.000.
    """
    return Experiment(
        dataset_id="or8c_like",
        source_path=Path("."),
        ndim=2,
        dimensions=[
            Dimension(
                logical_axis="F2",
                nucleus="1H",
                sf=600.13282861,
                sw=8389.26174496644,
                o1=2828.61000005141,
                o1p=4.71333,
                td=2048,
                role=AxisRole.DIRECT,
            ),
            Dimension(
                logical_axis="F1",
                nucleus="15N",
                sf=60.81782065611,
                sw=1824.5346196833,
                o1=7175.65611,
                o1p=118.0,
                td=256,
            ),
        ],
        acquisition_parameters={"acqus": {"TE": "299.9845"}, "acqu2s": {"FnMODE": 6}},
    )


def test_gamma_mapped_car_reproduces_bruker_auto() -> None:
    """The gamma mapping reproduces the indirect-dimension carrier of bruker -AUTO (three real
    datasets, measured error <= 0.001 ppm).

    OR8C ``-yCAR 118.050``, RhoA (the depositor's fid.com) ``-yCAR 118.080``,
    d_018 ``-zCAR 52.530`` (13C).
    """
    assert gamma_mapped_car(4.754, 600.13282861, 60.81782065611, "1H", "15N") == (
        pytest.approx(118.05, abs=2e-3)
    )
    assert gamma_mapped_car(4.773, 600.2428202, 60.8289680, "1H", "15N") == (
        pytest.approx(118.080, abs=2e-3)
    )
    assert gamma_mapped_car(5.355, 600.1350951, 150.9108068, "1H", "13C") == (
        pytest.approx(52.530, abs=2e-3)
    )


def test_carrier_audit_uses_the_acquisition_centre_and_reports_the_delta() -> None:
    """OR8C shape: the script's present values are 4.754/118.050 (water peak + gamma ratio
    convention), while the adopted values come from O1/BF1.

    As designed by the maintainer, the report and the record give the four-line block and
    ``REFERENCE_OVERRIDE``, and say that the script's present value came from the water-peak
    convention.
    """
    audit = carrier_audit(_or8c_like(), {"xCAR": "4.754", "yCAR": "118.050"})
    assert audit["rule"] == "acquisition_center"
    assert audit["convention"] == "o1bf1"
    assert carrier_overrides(audit) == {"xCAR": "4.713", "yCAR": "118.000"}
    assert {record["axis"]: record["status"] for record in audit["dims"]} == {
        "x": "REFERENCE_OVERRIDE",
        "y": "REFERENCE_OVERRIDE",
    }
    deltas = {record["axis"]: record["delta_ppm"] for record in audit["dims"]}
    assert deltas["x"] == pytest.approx(0.04, abs=1e-3)
    assert deltas["y"] == pytest.approx(0.05, abs=1e-3)

    assert audit["blocks"] == []
    assert audit["notes"] == []

    summary = audit["summary"]
    assert "water-peak" in summary or "水峰" in summary
    assert "\n" not in summary

    assert "118.05" not in summary and "4.754" not in summary


def test_carrier_summary_names_the_convention_whichever_one_is_in_use() -> None:
    """Regression coverage: test carrier summary names the convention whichever one is in use."""
    exp = _or8c_like()

    ok = carrier_audit(exp, {"xCAR": "4.713", "yCAR": "118.000"})
    assert "O1/BF1" in ok["summary"]
    assert "water-peak" not in ok["summary"]
    assert ok["blocks"] == [] and ok["notes"] == []

    manual = carrier_audit(exp, {"xCAR": "4.700"}, manual_keys={"xCAR"})
    assert "x" in manual["summary"]

    assert "y" in manual["summary"]
    assert manual["blocks"] == [] and manual["notes"] == []


def test_carrier_audit_keeps_the_script_value_when_it_is_the_acquisition_centre() -> None:
    """The script's present value already is that dimension's O1/BF1 (the difference is within
    display precision) -> REFERENCE_KEPT: not rewritten, not listed as a correction.
    """
    audit = carrier_audit(_or8c_like(), {"xCAR": "4.713", "yCAR": "118.000"})
    assert carrier_overrides(audit) == {}
    assert carrier_patch_notes(audit) == []
    assert [record["status"] for record in audit["dims"]] == [
        "REFERENCE_KEPT",
        "REFERENCE_KEPT",
    ]
    assert audit["blocks"] == []
    assert "O1/BF1" in audit["summary"]


def test_carrier_audit_overrides_a_dimension_off_the_water_convention() -> None:
    """The direct 1H dimension sits on the water peak, but the 13C carrier is in the carbonyl
    region (actually 174 ppm, while the water-peak convention gives a gamma value of ~55.7).

    This is exactly the hole found in the 2026-09-24 review: the old implementation only
    accepted the value when "the script's present value equals O1/BF1 exactly", while the
    present value AUTO generates is the gamma value => the old implementation kept it and the
    whole 13C axis was off by hundreds of ppm. Now the value comes from the metadata: it is
    overridden to 174.000 and marked ``REFERENCE_OVERRIDE``.
    """
    experiment = _or8c_like()
    experiment.dimensions[1].nucleus = "13C"
    experiment.dimensions[1].sf = 150.9108068
    experiment.dimensions[1].o1p = 174.0
    auto_gamma_value = gamma_mapped_car(4.754, 600.13282861, 150.9108068, "1H", "13C")
    assert auto_gamma_value is not None and abs(auto_gamma_value - 55.705) < 0.05
    configured = float(f"{auto_gamma_value:.3f}")
    audit = carrier_audit(experiment, {"xCAR": "4.754", "yCAR": f"{auto_gamma_value:.3f}"})
    assert carrier_overrides(audit)["yCAR"] == "174.000"
    record = audit["dims"][1]
    assert record["status"] == "REFERENCE_OVERRIDE"
    # AUTO/the script writes exactly the "water peak + gamma ratio" value -- the report has to
    # be able to point that out
    assert record["configured_source"] == "water_gamma"
    assert record["delta_ppm"] == pytest.approx(configured - 174.0, abs=1e-6)


def test_carrier_audit_marks_manual_overrides_and_missing_keys() -> None:
    """A dimension whose CAR was edited by hand is marked ``REFERENCE_MANUAL`` (the value
    belongs to the manual path); a missing key gets the spectral centre written and is marked
    MISSING.
    """
    experiment = _or8c_like()
    manual = carrier_audit(experiment, {"xCAR": "4.754", "yCAR": "118.050"}, manual_keys={"yCAR"})
    assert {record["axis"]: record["status"] for record in manual["dims"]}["y"] == (
        "REFERENCE_MANUAL"
    )
    assert "yCAR" not in carrier_overrides(manual)
    assert manual["fix_lines"] == []

    missing = carrier_audit(experiment, {"xCAR": "4.713"})
    assert {record["axis"]: record["status"] for record in missing["dims"]}["y"] == (
        "REFERENCE_MISSING"
    )

    assert carrier_overrides(missing) == {"yCAR": "118.000"}

    assert missing["fix_lines"] == []
    assert missing["blocks"] == []
    assert "y" in missing["summary"]


def test_carrier_audit_marks_a_dimension_without_an_acquisition_centre() -> None:
    """That dimension's O1/BF1 is unavailable (0) -> ``REFERENCE_UNKNOWN``: the script value is
    kept and the reason is given in the report.
    """
    experiment = _or8c_like()
    experiment.dimensions[1].o1p = 0.0
    audit = carrier_audit(experiment, {"xCAR": "4.713", "yCAR": "118.050"})
    assert {record["axis"]: record["status"] for record in audit["dims"]}["y"] == (
        "REFERENCE_UNKNOWN"
    )
    assert "yCAR" not in carrier_overrides(audit)

    assert audit["notes"] == []
    assert "y" in audit["summary"]


def test_carrier_audit_ignores_an_absurd_temperature_for_the_water_note() -> None:
    """When TE is written with a clearly wrong order of magnitude, the water-peak evidence is
    treated as missing (no non-physical water peak is produced).
    """
    experiment = _or8c_like()
    experiment.acquisition_parameters["acqus"]["TE"] = "2981.5"
    audit = carrier_audit(experiment, {"xCAR": "4.754", "yCAR": "118.050"})
    assert audit["dims"][0]["water_value"] is None
    assert audit["dims"][0]["configured_source"] == "script"

    assert "4.754" not in audit["summary"], audit["summary"]
    assert "\n" not in audit["summary"]


def test_patch_fid_com_keeps_grpdly_when_the_acqus_value_is_negative(
    bruker_dir: Path,
) -> None:
    """GRPDLY < 0 (the dataset has no usable digital-filter group delay) no longer overwrites the
    fid.com value (2026-09-24).

    On real OR8C/ACP the ``acqu2s`` and the or8c ``acqus`` both write ``GRPDLY=-1``; the
    parameter-source table says: only ``GRPDLY >= 0`` uses acqus directly, a negative or missing
    value is left to fid.com (AUTO copies acqus as well).
    """
    text = "bruk2pipe -in ./ser \\\n  -grpdly 68 -out fid\n"
    negative = read_dataset(bruker_dir / "hsqc_2d")
    negative.acquisition_parameters["acqus"]["GRPDLY"] = -1
    patched, warnings = patch_fid_com(text, negative)
    assert parse_fid_com(patched)["grpdly"] == "68"  # keep the original value
    assert not any("grpdly" in warning for warning in warnings)

    positive = read_dataset(bruker_dir / "hsqc_2d")
    positive.acquisition_parameters["acqus"]["GRPDLY"] = 67.98
    patched2, _warnings2 = patch_fid_com(text, positive)

    assert parse_fid_com(patched2)["grpdly"] == "68"


def test_patch_fid_com_never_downgrades_auto_echo_antiecho() -> None:
    """An Echo-AntiEcho AUTO recognised from the pulse program is never silently downgraded to
    Complex by FnMODE=4.

    Corrected in the 2026-09-24 review: the E-A decision of bruker -AUTO lives in
    ``com/pprog.tcl`` (it reads the pulse program and ``acquNs(FnMODE)``), so the 33 real
    deposited datasets with a pulse program get Echo-AntiEcho; it degrades to Complex only when
    the pulse program is missing (the synthetic probes). Conversely (FnMODE is not 6 and AUTO
    already recognised E-A) it must never be rewritten to Complex -- that would make downstream
    process the whole dataset with a wrong encoding -- so the value is kept and a warning is
    emitted.
    """
    experiment = _or8c_like()
    experiment.acquisition_parameters["acqu2s"]["FnMODE"] = 4
    text = "bruk2pipe -in ./ser\n  -xMODE DQD -yMODE Echo-AntiEcho -out fid\n"
    patched, warnings = patch_fid_com(text, experiment)
    assert parse_fid_com(patched)["yMODE"] == "Echo-AntiEcho"
    # the assertions use only key names/values, not tr() text (in a full run the language may
    # already have been switched to Chinese by another test)
    assert any("yMODE" in warning for warning in warnings)


def test_patch_fid_com_ignores_differences_within_tolerance() -> None:
    """A difference inside the tolerance does not count as a correction (so that AUTO's
    high-precision spelling is not taken for a mistake).

    Real ACP_A: acqus `GRPDLY=67.9841` while `bruker -AUTO` computes `67.9841461181641` itself
    (the same quantity); this line used to go into the "parameter corrections" list and became
    noise in the report.
    """
    experiment = _or8c_like()
    experiment.acquisition_parameters["acqus"]["GRPDLY"] = "67.9841"
    text = "bruk2pipe -in ./ser\n  -grpdly 67.9841461181641 -out fid\n"
    patched, warnings = patch_fid_com(text, experiment)
    assert parse_fid_com(patched)["grpdly"] == "67.9841461181641"
    assert not any("grpdly" in warning for warning in warnings)


def test_patch_fid_com_writes_the_acquisition_centre() -> None:
    """CAR is always overwritten with that dimension's acqus O1/BF1 (the software design fixed by
    the maintainer 2026-09-24).

    The deposited script of real RhoA writes a third 1H carrier, 4.7995, and AUTO writes the
    water-peak value 4.754; neither is the operator's spectral centre -- per this design the
    value becomes 4.713 (the `%.3f` of O1/BF1 4.71333) and goes into the "parameter corrections"
    list.
    """
    experiment = _or8c_like()
    text = "bruk2pipe -in ./ser\n  -xCAR 4.7995 -yCAR 118.500 -out fid\n"
    patched, warnings = patch_fid_com(text, experiment)
    parsed = parse_fid_com(patched)
    assert parsed["xCAR"] == "4.7995"
    assert parsed["yCAR"] == "118.500"
    assert not any("xCAR" in warning for warning in warnings)
    assert not any("yCAR" in warning for warning in warnings)


# ---------------------------------------------------------- acquisition mode (MODE) conflict table
# Round two 2026-09-24 (table from the maintainer): mode_audit decides per dimension; the
# direct dimension is always DQD and does not take this table.


def test_mode_audit_forces_ea_when_fnmode_is_6() -> None:
    """Conflict table row 1: FnMODE=6 while the script is not spelled E-A -> force
    Echo-AntiEcho + warn.
    """
    from backend.bruker_workflow import mode_audit, mode_writes

    experiment = _or8c_like()  # acqu2s FnMODE=6
    plan = mode_audit(experiment, {"xMODE": "DQD", "yMODE": "Complex"})
    assert plan["dims"][1]["decision"] == "force_ea"
    assert mode_writes(plan) == {"yMODE": "Echo-AntiEcho"}
    text = "bruk2pipe -in ./ser\n  -xMODE DQD -yMODE Complex -out fid\n"
    patched, warnings = patch_fid_com(text, experiment)
    assert parse_fid_com(patched)["yMODE"] == "Echo-AntiEcho"
    assert any("yMODE" in warning for warning in warnings)


def test_mode_audit_accepts_the_ea_the_script_already_has() -> None:
    """Conflict table row 2: FnMODE=6 and the script already is E-A -> keep, no MODE line is
    produced.
    """
    from backend.bruker_workflow import mode_audit

    experiment = _or8c_like()
    plan = mode_audit(experiment, {"xMODE": "DQD", "yMODE": "Echo-AntiEcho"})
    assert [record["decision"] for record in plan["dims"]] == ["ok", "ok"]
    assert plan["fix_lines"] == [] and plan["notes"] == [] and plan["conflicts"] == []


def test_mode_audit_keeps_the_script_value_on_a_hard_conflict() -> None:
    """Conflict table row 4: FnMODE=4 hard-conflicts with the script's E-A -> no side is picked
    automatically, keep the script value + warn.
    """
    from backend.bruker_workflow import mode_audit, mode_writes

    experiment = _or8c_like()
    experiment.acquisition_parameters["acqu2s"]["FnMODE"] = 4  # States
    plan = mode_audit(experiment, {"xMODE": "DQD", "yMODE": "Echo-AntiEcho"})
    assert plan["dims"][1]["decision"] == "conflict"
    assert mode_writes(plan) == {}
    assert any("yMODE" in line for line in plan["conflicts"])


def test_mode_audit_writes_the_fnmode_derived_value_otherwise() -> None:
    """Conflict table row 5: FnMODE=2 (QSEQ) while AUTO writes Complex -> rewrite from FnMODE to
    Sequential.
    """
    from backend.bruker_workflow import bruk2pipe_mode_for, mode_audit, mode_writes

    experiment = _or8c_like()
    experiment.acquisition_parameters["acqu2s"]["FnMODE"] = 2
    plan = mode_audit(experiment, {"xMODE": "DQD", "yMODE": "Complex"})
    assert plan["dims"][1]["decision"] == "write"
    assert mode_writes(plan) == {"yMODE": bruk2pipe_mode_for(2)}


def test_mode_audit_marks_undefined_fnmode_but_keeps_the_script() -> None:
    """Conflict table rows 6/8: FnMODE missing -> keep the script value; mark inferred when a
    pulse program exists, unverified otherwise.
    """
    from backend.bruker_workflow import mode_audit, mode_writes

    experiment = _or8c_like()
    experiment.acquisition_parameters["acqu2s"].pop("FnMODE")
    plan = mode_audit(experiment, {"xMODE": "DQD", "yMODE": "Echo-AntiEcho"})
    assert plan["dims"][1]["decision"] == "inferred"
    assert mode_writes(plan) == {}
    no_program = mode_audit(
        experiment, {"xMODE": "DQD", "yMODE": "Complex"}, data_dir=Path("no-such-dir")
    )
    assert no_program["dims"][1]["decision"] == "unverified"


def test_mode_audit_leaves_the_direct_dimension_alone() -> None:
    """The direct dimension does not take the conflict table: acqus FnMODE=0 is a placeholder and
    no xMODE hint should appear in the report.

    2026-09-24 review: the acqus FnMODE of all 59 real datasets is 0, so every conversion report
    used to gain an extra "xMODE ... not metadata-confirmed" line; the direct dimension is
    always DQD, so AUTO only has to write it correctly.
    """
    from backend.bruker_workflow import mode_audit

    experiment = _or8c_like()  # acqus carries only TE, no FnMODE
    text = "bruk2pipe -in ./ser\n  -xMODE DQD -yMODE Echo-AntiEcho -out fid\n"
    plan = mode_audit(experiment, parse_fid_com(text))
    assert [record["decision"] for record in plan["dims"]] == ["ok", "ok"]
    _patched, warnings = patch_fid_com(text, experiment)
    assert not [warning for warning in warnings if "xMODE" in warning]


def test_patch_fid_com_leaves_manually_edited_keys_alone() -> None:
    """Keys of MODE/CAR edited by hand belong to the manual path (round two 2026-09-24): patch
    no longer rewrites them from a derived convention.

    The backend's manual path is "patch first, then write the manual values back from the manual
    diff" (apply_fid_com_overrides); if the patch step still rewrote them from FnMODE/derived
    values, the log would say "corrected" while the final script held the manual value.
    """
    experiment = _or8c_like()  # acqu2s FnMODE=6
    text = "bruk2pipe -in ./ser\n  -xMODE DQD -yMODE Complex -xCAR 4.700 -out fid\n"
    patched, warnings = patch_fid_com(text, experiment, manual_keys={"yMODE", "xCAR"})
    parsed = parse_fid_com(patched)
    assert parsed["yMODE"] == "Complex"
    assert parsed["xCAR"] == "4.700"
    # only look at **rewrite** lines (``->``); the "mode/sign not confirmed => no -neg applied"
    # reminder is unrelated to manual keys
    assert not [w for w in warnings if ("yMODE" in w or "xCAR" in w) and "->" in w]


def test_mode_audit_corrects_a_wrong_direct_mode() -> None:
    """When the direct dimension is written with another mode it is still corrected to DQD as a
    fallback (same as the old behaviour).
    """
    from backend.bruker_workflow import mode_audit, mode_writes

    experiment = _or8c_like()
    plan = mode_audit(experiment, {"xMODE": "Complex", "yMODE": "Complex"})
    assert plan["dims"][0]["decision"] == "write"
    assert mode_writes(plan)["xMODE"] == "DQD"


def test_grpdly_zero_is_a_real_value() -> None:
    """GRPDLY=0 is a **real value** (no digital-filter group delay) and must not be treated as
    missing by truthiness.
    """
    experiment = _or8c_like()
    experiment.acquisition_parameters["acqus"]["GRPDLY"] = 0
    assert expected_values(experiment)["grpdly"][0] == 0.0

    experiment.acquisition_parameters["acqus"]["GRPDLY"] = -1
    assert "grpdly" not in expected_values(experiment)


def test_sweep_width_tolerance_accepts_a_two_decimal_script() -> None:
    """The SW tolerance follows the magnitude (a relative 1e-5): a legitimate `-ySW 1824.53` is no
    longer flagged as "corrected".
    """
    experiment = _or8c_like()
    text = "bruk2pipe -in ./ser\\\n  -ySW 1824.53 -out fid\\n"
    patched, warnings = patch_fid_com(text, experiment)
    assert "-ySW 1824.53" in patched
    assert not [w for w in warnings if "ySW" in w]
    # a real convention error is still caught (2000 vs 1824.5)
    bad, bad_warnings = patch_fid_com(
        "bruk2pipe -in ./ser\\\n  -ySW 2000.000 -out fid\\n", experiment
    )
    assert "-ySW 1824.535" in bad
    assert any("ySW" in w for w in bad_warnings)


def test_patch_fid_com_leaves_every_manual_key_alone(tmp_path: Path, bruker_dir: Path) -> None:
    """2.4: keys edited by hand are **not** only CAR/MODE -- the generic replacement must not
    rewrite them either, let alone report "corrected".
    """
    exp = read_dataset(_stale_indirect_sweep_width(tmp_path, bruker_dir))
    text = "bruk2pipe -in ./ser \\\n  -ySW 1900.000 -out fid\\n"
    patched, warnings = patch_fid_com(text, exp, manual_keys={"ySW"})
    assert "-ySW 1900.000" in patched
    assert not [w for w in warnings if "ySW" in w]
    # without the manual marker it is still corrected from the acqus convention (so this is only
    # "manual wins")
    patched2, warnings2 = patch_fid_com(text, exp)
    assert "-ySW 1824.535" in patched2
    assert any("ySW" in w for w in warnings2)


def test_row_geometry_audit_respects_manual_keys(tmp_path: Path) -> None:
    """Regression coverage: test row geometry audit respects manual keys."""
    exp, data_dir = _padded_2d(tmp_path)  # TD=1612

    text = "bruk2pipe -in ./ser \\\n  -xN 1600 -xT 800 -out fid\n"

    _patched, auto_warnings = patch_fid_com(text, exp, data_dir=data_dir)
    assert any("xN" in w for w in auto_warnings), auto_warnings
    assert any("xT" in w for w in auto_warnings), auto_warnings

    patched, manual_warnings = patch_fid_com(text, exp, data_dir=data_dir, manual_keys={"xN", "xT"})

    assert parse_fid_com(patched)["xN"] == "1600"
    assert parse_fid_com(patched)["xT"] == "800"
    assert not [w for w in manual_warnings if "xN" in w], manual_warnings
    assert not [w for w in manual_warnings if "xT" in w], manual_warnings


def test_patch_fid_com_keeps_a_verified_row_length(tmp_path: Path, bruker_dir: Path) -> None:
    """``-xN`` passes verification (>=TD, divides the file, row bytes a multiple of 1024) => kept
    as is, not listed as a correction.
    """
    exp, data_dir = _padded_2d(tmp_path)  # TD=1612, physical row 1664, 210 rows
    text = (
        "bruk2pipe -in ./ser \\\n"
        "  -xN 1664 -yN 210 -xT 806 -yT 105 -xMODE DQD -yMODE Complex \\\n"
        "  -out fid\\n"
    )
    patched, warnings = patch_fid_com(text, exp, data_dir=data_dir)
    assert "-xN 1664" in patched
    assert not [w for w in warnings if w.startswith("xN")]


def test_direct_row_points_reports_the_source(tmp_path: Path) -> None:
    """Three sources for the row length: verified / derived / unknown (the report and the log
    explain it accordingly).
    """
    from backend.bruker_workflow import direct_row_points

    exp, data_dir = _padded_2d(tmp_path)
    assert direct_row_points(exp, data_dir, 1664) == (1664, "verified")
    assert direct_row_points(exp, data_dir, 1024) == (1664, "derived")
    assert direct_row_points(exp, None, 1664)[1] == "unknown"


def test_sample_dtype_prefers_dtype_then_dtypa() -> None:
    """Word size: `DTYPE` when present, otherwise `DTYPA` (287 of 292 real datasets carry only
    DTYPA), and int32 when neither is present.
    """
    import numpy as np

    from core.data.bruker_dtype import point_bytes, sample_dtype

    assert sample_dtype({"DTYPA": 2}) == np.dtype("<f8")
    assert point_bytes({"DTYPA": 2}) == 16
    assert sample_dtype({"DTYPA": 0}) == np.dtype("<i4")
    assert point_bytes({"DTYPA": 0}) == 8
    assert sample_dtype({"DTYPA": 1, "BYTORDA": 1}) == np.dtype(">i4")
    # DTYPE takes precedence over DTYPA
    assert sample_dtype({"DTYPE": 0, "DTYPA": 2}) == np.dtype("<i4")
    assert sample_dtype({}) == np.dtype("<i4")
