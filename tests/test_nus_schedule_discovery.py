"Regression coverage: module."

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.data.internal_data_model import AxisRole, Dimension, Experiment
from core.data.nus_reader import (
    find_schedule_file,
    indirect_grid_2d,
    parse_auto_sampling,
    probe_auto_sampling,
    scan_whole_trace_zeros,
)
from core.experiment.sampling_detector import detect


def _write_acqus(
    directory: Path,
    *,
    nus_amount: int = 100,
    nuslist: str = "",
    parmode: int = 1,
    nus_marker: bool = True,
) -> None:
    lines = [
        "##TITLE= test",
        "##JCAMPDX= 5.0",
        "##DATATYPE= Parameter Values",
        "##$TD= 64",
        f"##$PARMODE= {parmode}",
        f"##$NusAMOUNT= {nus_amount}",
        f"##$NusT2= {1 if nus_marker else 0}",
    ]
    if nuslist:
        lines.append(f"##$NUSLIST= <{nuslist}>")
    (directory / "acqus").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_ser(directory: Path, *, rows: int, nonzero_rows: int, points: int = 8) -> None:
    "Regression coverage:  write ser."
    table = np.zeros((rows, points), dtype=np.int32)
    if nonzero_rows:
        table[:nonzero_rows] = 1
    table.tofile(directory / "ser")


# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
def test_find_schedule_does_not_guess_integer_lists(tmp_path: Path) -> None:
    "Regression coverage: test find schedule does not guess integer lists."
    (tmp_path / "vclist").write_text("1\n2\n3\n", encoding="utf-8")
    (tmp_path / "CANH").write_text("0 0\n1 1\n2 2\n", encoding="utf-8")
    path, source = find_schedule_file(tmp_path)

    assert path is None
    assert source


def test_find_schedule_prefers_the_standard_filename(tmp_path: Path) -> None:
    "Regression coverage: test find schedule prefers the standard filename."
    (tmp_path / "nuslist").write_text("0\n1\n2\n", encoding="utf-8")
    (tmp_path / "CANH").write_text("0 0\n1 1\n2 2\n", encoding="utf-8")

    path, source = find_schedule_file(tmp_path)

    assert path is not None
    assert path.name == "nuslist"
    assert source


def test_find_schedule_accepts_the_name_declared_in_acqus(tmp_path: Path) -> None:
    "Regression coverage: test find schedule accepts the name declared in acqus."
    (tmp_path / "schedule_a").write_text("0\n1\n2\n", encoding="utf-8")

    path, source = find_schedule_file(tmp_path, acqus={"NUSLIST": "schedule_a"})

    assert path is not None
    assert path.name == "schedule_a"
    assert "schedule_a" in source


def test_find_schedule_requires_the_dimension_specific_column_count(
    tmp_path: Path,
) -> None:
    "Regression coverage: test find schedule requires the dimension specific column count."
    schedule = tmp_path / "nuslist"
    schedule.write_text("0 0\n1 1\n", encoding="utf-8")
    path_2d, reason_2d = find_schedule_file(tmp_path, expected_columns=1)
    path_3d, _reason_3d = find_schedule_file(tmp_path, expected_columns=2)

    assert path_2d is None
    assert reason_2d
    assert path_3d == schedule


def test_find_schedule_rejects_declared_path_escape(tmp_path: Path) -> None:
    "Regression coverage: test find schedule rejects declared path escape."
    outside = tmp_path.parent / "outside_schedule"
    outside.write_text("0\n1\n", encoding="utf-8")

    path, reason = find_schedule_file(
        tmp_path, acqus={"NUSLIST": "../outside_schedule"}, expected_columns=1
    )

    assert path is None
    assert reason


def test_find_schedule_rejects_parameter_and_binary_files(tmp_path: Path) -> None:
    "Regression coverage: test find schedule rejects parameter and binary files."
    (tmp_path / "acqus").write_text(
        "##TITLE= t\n##JCAMPDX= 5.0\n##DATATYPE= Parameter Values\n",
        encoding="utf-8",
    )
    (tmp_path / "big.dat").write_bytes(b"\x00" * (128 * 1024))

    path, source = find_schedule_file(tmp_path)

    assert path is None

    assert source


def test_find_schedule_reports_when_nothing_matches(tmp_path: Path) -> None:
    "Regression coverage: test find schedule reports when nothing matches."
    (tmp_path / "notes.txt").write_text("hello world\n", encoding="utf-8")

    path, source = find_schedule_file(tmp_path)

    assert path is None
    assert source


# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
def test_scan_whole_trace_zeros_counts_only_fully_zero_rows(tmp_path: Path) -> None:
    "Regression coverage: test scan whole trace zeros counts only fully zero rows."

    table = np.zeros((6, 8), dtype=np.int32)
    table[0, :4] = 1
    table[1, :] = 1
    table[2, 0] = 1

    table.tofile(tmp_path / "ser")

    scan = scan_whole_trace_zeros(tmp_path, acqus={}, direct_points=8)

    assert scan["kind"] == "whole_trace_scanned"
    assert scan["rows"] == 6
    assert scan["zero_rows"] == 3
    assert scan["nonzero_rows"] == 3


def test_scan_whole_trace_zeros_floors_a_partial_trailing_row(tmp_path: Path) -> None:
    "Regression coverage: test scan whole trace zeros floors a partial trailing row."
    table = np.zeros((5, 8), dtype=np.int32)
    table[:2] = 1
    table.tofile(tmp_path / "ser")

    with (tmp_path / "ser").open("ab") as handle:
        handle.write(b"\x01" * 20)

    scan = scan_whole_trace_zeros(tmp_path, acqus={}, direct_points=8)

    assert scan["kind"] == "whole_trace_scanned"
    assert scan["rows"] == 5
    assert scan["zero_rows"] == 3
    assert scan["remainder_bytes"] == 20


def test_scan_whole_trace_zeros_reports_contiguous_blocks(tmp_path: Path) -> None:
    "Regression coverage: test scan whole trace zeros reports contiguous blocks."
    table = np.zeros((10, 4), dtype=np.int32)
    table[0:3] = 1
    table[7:9] = 1
    table.tofile(tmp_path / "ser")

    scan = scan_whole_trace_zeros(tmp_path, acqus={}, direct_points=4)

    assert scan["blocks"] == [(0, 2), (7, 8)]


def test_scan_whole_trace_zeros_handles_a_missing_ser(tmp_path: Path) -> None:
    "Regression coverage: test scan whole trace zeros handles a missing ser."
    scan = scan_whole_trace_zeros(tmp_path, acqus={}, direct_points=8)

    assert scan["kind"] == "missing"


# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
def _make_experiment(tmp_path: Path, *, ndim: int = 2, **kwargs):
    "Regression coverage:  make experiment."
    from core.data.bruker_reader import read_dataset

    directory = tmp_path / "ds"
    directory.mkdir()
    _write_acqus(directory, parmode=ndim - 1, **kwargs)
    nus_marker = bool(kwargs.get("nus_marker", True))
    (directory / "acqu2s").write_text(
        f"##$TD= 20\n##$FnMODE= 5\n##$NusTD= {20 if nus_marker else 0}\n",
        encoding="utf-8",
    )
    if ndim == 3:
        (directory / "acqu3s").write_text(
            f"##$TD= 20\n##$FnMODE= 5\n##$NusTD= {20 if nus_marker else 0}\n",
            encoding="utf-8",
        )
    _write_ser(directory, rows=64, nonzero_rows=32)
    return read_dataset(directory), directory


def test_detect_reports_nus_from_whole_trace_zeros_only(tmp_path: Path) -> None:
    "Regression coverage: test detect reports nus from whole trace zeros only."
    experiment, directory = _make_experiment(tmp_path)
    table = np.ones((8, 256), dtype=np.int32)
    table[1::2] = 0
    table.tofile(directory / "ser")

    sampling = detect(experiment)

    assert sampling.mode.value == "nus"
    assert sampling.schedule_type == "zero_trace"
    assert sampling.schedule_file == ""

    assert sampling.evidence
    assert any("8" in line and "4" in line for line in sampling.evidence)


def test_detect_names_the_schedule_file_it_used(tmp_path: Path) -> None:
    "Regression coverage: test detect names the schedule file it used."
    experiment, directory = _make_experiment(tmp_path, ndim=3, nus_amount=25, nuslist="CANH")
    (directory / "CANH").write_text(
        "\n".join(f"{r} {c}" for r in range(3) for c in range(4)) + "\n",
        encoding="utf-8",
    )

    sampling = detect(experiment)

    assert sampling.mode.value == "nus"
    assert sampling.schedule_file == "CANH"
    assert "CANH" in sampling.schedule_source
    assert len(sampling.nus_list) == 12


def test_detect_named_3d_schedule_covering_complex_grid_is_uniform(
    tmp_path: Path,
) -> None:
    "Regression coverage: test detect named 3d schedule covering complex grid is uniform."
    experiment, directory = _make_experiment(tmp_path, ndim=3, nus_amount=100, nuslist="CANH")
    (directory / "CANH").write_text(
        "".join(f"{row} {column}\n" for row in range(10) for column in range(10)),
        encoding="utf-8",
    )

    sampling = detect(experiment, allow_auto_probe=False)

    assert sampling.mode.value == "uniform"
    assert sampling.schedule_type == "full_sampling"
    assert sampling.schedule_file == "CANH"
    assert sampling.sampling_fraction == pytest.approx(1.0)


def test_detect_full_but_shuffled_schedule_still_requires_reordering(
    tmp_path: Path,
) -> None:
    "Regression coverage: test detect full but shuffled schedule still requires reordering."
    experiment, directory = _make_experiment(tmp_path, ndim=3, nus_amount=100, nuslist="CANH")
    points = [(row, column) for row in range(10) for column in range(10)]
    points = points[::2] + points[1::2]
    (directory / "CANH").write_text(
        "".join(f"{row} {column}\n" for row, column in points),
        encoding="utf-8",
    )

    sampling = detect(experiment, allow_auto_probe=False)

    assert sampling.mode.value == "nus"
    assert sampling.schedule_type == "nuslist"
    assert sampling.sampling_fraction == pytest.approx(1.0)
    assert sampling.nus_list == points


def test_detect_uniform_2d_ignores_trailing_zero_padding(tmp_path: Path) -> None:
    "Regression coverage: test detect uniform 2d ignores trailing zero padding."
    import re
    import shutil

    from core.data.bruker_dtype import sample_dtype
    from core.data.bruker_reader import read_dataset

    source = Path(__file__).parent / "fixtures" / "bruker" / "hsqc_2d"
    directory = tmp_path / "uniform_padded"
    shutil.copytree(source, directory)
    acqu2s = (directory / "acqu2s").read_text(encoding="utf-8")
    acqu2s = re.sub(r"##\$TD=\s*\d+", "##$TD= 10", acqu2s, count=1)
    acqu2s = re.sub(r"##\$FnMODE=\s*\d+", "##$FnMODE= 6", acqu2s, count=1)
    (directory / "acqu2s").write_text(acqu2s, encoding="utf-8")
    experiment = read_dataset(directory)
    grid, mult, direct_points = indirect_grid_2d(experiment)
    declared_rows = grid * mult
    padded_rows = declared_rows + 7
    table = np.zeros(
        (padded_rows, direct_points),
        dtype=sample_dtype(experiment.acquisition_parameters.get("acqus", {})),
    )
    table[:declared_rows] = 1
    table.tofile(directory / "ser")

    experiment = read_dataset(directory)
    sampling = detect(
        experiment,
        auto_sampling={
            "ok": True,
            "grid": declared_rows,
            "sampled": grid,
            "fraction": 0.5,
        },
    )

    assert sampling.mode.value == "uniform"
    assert sampling.schedule_type == "full_sampling"


def test_detect_2d_uses_nustd_as_planned_grid_when_larger_than_td(
    tmp_path: Path,
) -> None:
    "Regression coverage: test detect 2d uses nustd as planned grid when larger than td."
    directory = tmp_path / "incomplete_2d"
    directory.mkdir()
    _write_ser(directory, rows=116, nonzero_rows=6)
    experiment = Experiment(
        dataset_id="d013",
        source_path=directory,
        ndim=2,
        dimensions=[
            Dimension("F2", "1H", td=8, role=AxisRole.DIRECT),
            Dimension("F1", "15N", td=6, role=AxisRole.INDIRECT),
        ],
        acquisition_parameters={
            "acqus": {},
            "acqu2s": {"TD": 6, "NusTD": 116, "FnMODE": 5},
        },
    )

    sampling = detect(experiment, allow_auto_probe=False)

    assert sampling.mode.value == "nus"
    assert sampling.schedule_type == "zero_trace"
    assert sampling.sampling_fraction == 0.0


def test_detect_3d_compact_tail_with_nus_markers_requires_schedule(
    tmp_path: Path,
) -> None:
    "Regression coverage: test detect 3d compact tail with nus markers requires schedule."
    directory = tmp_path / "compact_3d_nus_without_schedule"
    directory.mkdir()
    _write_ser(directory, rows=1024, nonzero_rows=780)
    experiment = Experiment(
        dataset_id="d024_segment",
        source_path=directory,
        ndim=3,
        dimensions=[
            Dimension("F3", "1H", td=8, role=AxisRole.DIRECT),
            Dimension("F2", "15N", td=30, role=AxisRole.INDIRECT),
            Dimension("F1", "13C", td=26, role=AxisRole.INDIRECT),
        ],
        acquisition_parameters={
            "acqus": {"FnTYPE": 0, "NusAMOUNT": 100},
            "acqu2s": {"TD": 30, "NusTD": 30, "FnMODE": 5},
            "acqu3s": {"TD": 26, "NusTD": 26, "FnMODE": 5},
        },
    )

    sampling = detect(experiment, allow_auto_probe=False)

    assert sampling.mode.value == "nus"
    assert sampling.schedule_type == "params"
    assert sampling.sampling_fraction == pytest.approx(1.0)
    assert any("244" in line and "780" in line for line in sampling.evidence)


def test_detect_100_percent_3d_nus_without_schedule_does_not_become_uniform(
    tmp_path: Path,
) -> None:
    "Regression coverage: test detect 100 percent 3d nus without schedule does not become uniform."
    directory = tmp_path / "full_coverage_3d_nus_without_schedule"
    directory.mkdir()
    _write_ser(directory, rows=1224, nonzero_rows=1224)
    experiment = Experiment(
        dataset_id="canh_72",
        source_path=directory,
        ndim=3,
        dimensions=[
            Dimension("F3", "1H", td=8, role=AxisRole.DIRECT),
            Dimension("F2", "15N", td=34, role=AxisRole.INDIRECT),
            Dimension("F1", "13C", td=36, role=AxisRole.INDIRECT),
        ],
        acquisition_parameters={
            "acqus": {
                "FnTYPE": 2,
                "NusAMOUNT": 100,
                "NusT2": 1,
                "NUSLIST": "missing-canh-schedule.txt",
            },
            "acqu2s": {"TD": 34, "NusTD": 34, "NusT2": 1, "FnMODE": 5},
            "acqu3s": {"TD": 36, "NusTD": 36, "NusT2": 1, "FnMODE": 5},
        },
    )

    sampling = detect(experiment, allow_auto_probe=False)

    assert sampling.mode.value == "nus"
    assert sampling.schedule_type == "params"
    assert sampling.schedule_file == ""
    assert sampling.sampling_fraction == pytest.approx(1.0)


def test_detect_traditional_3d_ignores_dormant_nus_parameters(
    tmp_path: Path,
) -> None:
    "Regression coverage: test detect traditional 3d ignores dormant nus parameters."
    directory = tmp_path / "traditional_3d_with_dormant_nus_values"
    directory.mkdir()
    _write_ser(directory, rows=1224, nonzero_rows=1224)
    experiment = Experiment(
        dataset_id="canh_72",
        source_path=directory,
        ndim=3,
        dimensions=[
            Dimension("F3", "1H", td=8, role=AxisRole.DIRECT),
            Dimension("F2", "15N", td=34, role=AxisRole.INDIRECT),
            Dimension("F1", "13C", td=36, role=AxisRole.INDIRECT),
        ],
        acquisition_parameters={
            "acqus": {
                "FnTYPE": 0,
                "NusAMOUNT": 100,
                "NusT2": 1,
                "NUSLIST": "automatic",
            },
            "acqu2s": {"TD": 34, "NusTD": 34, "NusT2": 1, "FnMODE": 5},
            "acqu3s": {"TD": 36, "NusTD": 36, "NusT2": 1, "FnMODE": 5},
        },
    )

    sampling = detect(experiment, allow_auto_probe=False)

    assert sampling.mode.value == "uniform"
    assert sampling.schedule_type == "full_sampling"


def test_detect_legacy_named_3d_schedule_overrides_stale_fntype_zero(
    tmp_path: Path,
) -> None:
    "Regression coverage: test detect legacy named 3d schedule overrides stale fntype zero."
    directory = tmp_path / "legacy_named_3d_nus_without_schedule"
    directory.mkdir()
    _write_ser(directory, rows=1224, nonzero_rows=1224)
    experiment = Experiment(
        dataset_id="canh_72",
        source_path=directory,
        ndim=3,
        dimensions=[
            Dimension("F3", "1H", td=8, role=AxisRole.DIRECT),
            Dimension("F2", "15N", td=34, role=AxisRole.INDIRECT),
            Dimension("F1", "13C", td=36, role=AxisRole.INDIRECT),
        ],
        acquisition_parameters={
            "acqus": {
                "FnTYPE": 0,
                "NusAMOUNT": 100,
                "NusT2": 1,
                "NUSLIST": "canh-7-1-1-65.txt",
            },
            "acqu2s": {"TD": 34, "NusTD": 34, "NusT2": 1, "FnMODE": 5},
            "acqu3s": {"TD": 36, "NusTD": 36, "NusT2": 1, "FnMODE": 5},
        },
    )

    sampling = detect(experiment, allow_auto_probe=False)

    assert sampling.mode.value == "nus"
    assert sampling.schedule_type == "params"
    assert sampling.schedule_file == ""


def test_detect_traditional_2d_ignores_dormant_nus_padding(
    tmp_path: Path,
) -> None:
    "Regression coverage: test detect traditional 2d ignores dormant nus padding."
    directory = tmp_path / "traditional_2d_with_dormant_nus_values"
    directory.mkdir()
    _write_ser(directory, rows=1024, nonzero_rows=850)
    experiment = Experiment(
        dataset_id="data_5",
        source_path=directory,
        ndim=2,
        dimensions=[
            Dimension("F2", "1H", td=8, role=AxisRole.DIRECT),
            Dimension("F1", "15N", td=850, role=AxisRole.INDIRECT),
        ],
        acquisition_parameters={
            "acqus": {
                "FnTYPE": 0,
                "NusAMOUNT": 100,
                "NusT2": 1,
                "NUSLIST": "automatic",
            },
            "acqu2s": {"TD": 850, "NusTD": 1024, "NusT2": 1, "FnMODE": 6},
        },
    )

    sampling = detect(experiment, allow_auto_probe=False)

    assert sampling.mode.value == "uniform"
    assert sampling.schedule_type == "full_sampling"


def test_detect_plain_uniform_3d_can_ignore_trailing_block_padding(
    tmp_path: Path,
) -> None:
    "Regression coverage: test detect plain uniform 3d can ignore trailing block padding."
    directory = tmp_path / "plain_uniform_3d_padded"
    directory.mkdir()
    _write_ser(directory, rows=1024, nonzero_rows=780)
    experiment = Experiment(
        dataset_id="uniform_segment",
        source_path=directory,
        ndim=3,
        dimensions=[
            Dimension("F3", "1H", td=8, role=AxisRole.DIRECT),
            Dimension("F2", "15N", td=30, role=AxisRole.INDIRECT),
            Dimension("F1", "13C", td=26, role=AxisRole.INDIRECT),
        ],
        acquisition_parameters={
            "acqus": {},
            "acqu2s": {"TD": 30, "FnMODE": 5},
            "acqu3s": {"TD": 26, "FnMODE": 5},
        },
    )

    sampling = detect(experiment, allow_auto_probe=False)

    assert sampling.mode.value == "uniform"
    assert sampling.schedule_type == "full_sampling"
    assert sampling.sampling_fraction == pytest.approx(1.0)


# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
def test_reconstruct_blocks_when_no_schedule_and_3d(tmp_path: Path) -> None:
    "Regression coverage: test reconstruct blocks when no schedule and 3d."
    from backend.nmrpipe_backend import NMRPipeBackend

    experiment, directory = _make_experiment(tmp_path)

    object.__setattr__(experiment, "ndim", 3)

    logs: list[str] = []
    recovered = NMRPipeBackend(nmrpipe_bin="")._recover_dense_nus(directory, experiment, logs)

    assert recovered is None

    assert logs


# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
def test_parse_auto_sampling_reads_conversion_geometry() -> None:
    "Regression coverage: test parse auto sampling reads conversion geometry."
    text = (
        "  -xN               384  -yN                36  -zN                38  \\\n"
        "  -xT               178  -yT                18  -zT                19  \\\n"
    )

    parsed = parse_auto_sampling(text)

    assert parsed["grid"] == 36 * 38 == 1368
    assert parsed["sampled"] == 18 * 19 == 342
    assert parsed["fraction"] == pytest.approx(0.25)


def test_parse_auto_sampling_handles_2d_and_full_sampling() -> None:
    "Regression coverage: test parse auto sampling handles 2d and full sampling."
    two_d = parse_auto_sampling("  -yN 256  -yT 64")
    assert two_d["grid"] == 256
    assert two_d["sampled"] == 64
    assert two_d["fraction"] == pytest.approx(0.25)

    full = parse_auto_sampling("  -yN 256  -zN 128  -yT 256  -zT 128")
    assert full["grid"] == 256 * 128
    assert full["sampled"] == 256 * 128
    assert full["fraction"] == pytest.approx(1.0)


def test_parse_auto_sampling_without_flags_is_quiet() -> None:
    "Regression coverage: test parse auto sampling without flags is quiet."
    parsed = parse_auto_sampling("nothing useful here")

    assert parsed["grid"] == 0
    assert parsed["sampled"] == 0
    assert parsed["fraction"] == 0.0


def test_probe_auto_sampling_without_bruker_returns_not_ok(tmp_path: Path) -> None:
    "Regression coverage: test probe auto sampling without bruker returns not ok."
    _write_acqus(tmp_path)
    (tmp_path / "ser").write_bytes(b"\x00" * 64)

    probed = probe_auto_sampling(tmp_path, bruker_bin=str(tmp_path / "no-such-bruker"))

    assert probed["ok"] is False
    assert probed["detail"]


def test_find_bruker_prefers_the_registered_path(tmp_path: Path) -> None:
    "Regression coverage: test find bruker prefers the registered path."
    from core.data import nus_reader

    fake = tmp_path / "my-bruker"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    original = nus_reader.configured_bruker_executable()
    try:
        nus_reader.set_bruker_executable(str(fake))
        assert nus_reader._find_bruker() == str(fake)
    finally:
        nus_reader.set_bruker_executable(original)


def test_find_bruker_does_not_hardcode_developer_paths() -> None:
    "Regression coverage: test find bruker does not hardcode developer paths."
    from core.data import nus_reader

    for candidate in nus_reader._BRUKER_CANDIDATES:
        assert "/" not in candidate, f"候选表里混进了路径:{candidate!r}"
        assert not candidate.startswith("~")


def test_detect_does_not_treat_auto_geometry_as_nus_fraction(tmp_path: Path) -> None:
    "Regression coverage: test detect does not treat auto geometry as nus fraction."
    experiment, directory = _make_experiment(tmp_path, ndim=3, nus_amount=100, nus_marker=False)

    (directory / "ser").unlink()
    _write_ser(directory, rows=64, nonzero_rows=64, points=256)

    sampling = detect(
        experiment,
        auto_sampling={"ok": True, "grid": 256, "sampled": 64, "fraction": 0.25},
    )

    assert sampling.mode.value == "uniform"
    assert sampling.sampling_fraction == pytest.approx(1.0)
    assert sampling.evidence


def test_detect_falls_back_when_auto_probe_is_off(tmp_path: Path) -> None:
    "Regression coverage: test detect falls back when auto probe is off."
    experiment, _ = _make_experiment(tmp_path)

    sampling = detect(experiment, auto_sampling=None, allow_auto_probe=False)

    assert sampling.mode.value == "nus"
    assert sampling.schedule_type == "zero_trace"


def test_detect_does_not_read_trace_rows_as_sampled_points(tmp_path: Path) -> None:
    "Regression coverage: test detect does not read trace rows as sampled points."
    experiment, directory = _make_experiment(tmp_path, nus_amount=100)
    (directory / "ser").unlink()

    _write_ser(directory, rows=200, nonzero_rows=200)

    sampling = detect(
        experiment,
        auto_sampling={"ok": True, "grid": 64, "sampled": 16, "fraction": 0.25},
    )

    assert sampling.mode.value == "nus"
    assert sampling.sampling_fraction == pytest.approx(1.0)
