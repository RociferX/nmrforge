"""Focused contract tests for reproducible Bruker pseudo-NUS evidence."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.data.nus_reader import schedule_grid_shape
from scripts.vm_make_evidence_nus import make_nus

DIRECT_TD = 256  # already aligned to the 4-byte Bruker row pad


def _write_params(path: Path, *, td: int, **params: object) -> None:
    lines = [f"##${key}= {value}" for key, value in {"TD": td, **params}.items()]
    (path).write_text("\n".join(lines) + "\n##END=\n", encoding="utf-8")


def _make_source(root: Path, ndim: int) -> Path:
    """Write legal 2D/3D complex Bruker headers and row-distinct raw bytes."""
    source = root / f"bruker_{ndim}d"
    source.mkdir()
    _write_params(source / "acqus", td=DIRECT_TD, AQ_mod=3, AQSEQ=0, DTYPE=0)
    y_td = 8 if ndim == 2 else 16
    _write_params(source / "acqu2s", td=y_td, FnMODE=4)
    if ndim == 3:
        _write_params(source / "acqu3s", td=20, FnMODE=5)

    row_count = y_td if ndim == 2 else y_td * 20
    with (source / "ser").open("wb") as stream:
        for row in range(row_count):
            # Every value is filled; each row's repeated byte pattern is unique.
            stream.write(_row_bytes(row))
    return source


def _row_bytes(row: int) -> bytes:
    return row.to_bytes(2, "little") * (DIRECT_TD * 2)


def _source_hashes(source: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(source.iterdir())
        if path.is_file()
    }


def _coordinates(path: Path) -> list[tuple[int, ...]]:
    return [tuple(map(int, line.split())) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("ndim", [2, 3])
def test_make_nus_is_reproducible_and_retains_expected_raw_rows(
    tmp_path: Path, ndim: int
) -> None:
    source = _make_source(tmp_path, ndim)
    before = _source_hashes(source)
    first = tmp_path / "first"
    second = tmp_path / "second"

    manifest = make_nus(source, first, 0.5, 1234)
    again = make_nus(source, second, 0.5, 1234)

    schedule = _coordinates(first / "nuslist")
    assert schedule == _coordinates(second / "nuslist")
    assert (first / "ser").read_bytes() == (second / "ser").read_bytes()
    assert (first / "nuslist").read_bytes() == (second / "nuslist").read_bytes()
    origin = (0, 0) if ndim == 3 else (0,)
    final_corner = (7, 9) if ndim == 3 else (3,)
    assert origin in schedule
    assert final_corner in schedule
    assert _source_hashes(source) == before
    assert manifest["source"]["files"] == before
    assert manifest["actual_fraction"] == pytest.approx(
        manifest["sample_count"] / (4 if ndim == 2 else 80)
    )
    assert again == manifest

    # Each synthetic source row has a unique byte value, so retained ordering is
    # directly visible in the compact SER without interpreting numeric samples.
    row_bytes = DIRECT_TD * 4
    output_rows = [
        (first / "ser").read_bytes()[offset : offset + row_bytes]
        for offset in range(0, (first / "ser").stat().st_size, row_bytes)
    ]
    if ndim == 2:
        expected = [_row_bytes(2 * y + phase)
                    for (y,) in schedule for phase in (0, 1)]
        assert (first / "acqu2s").read_text().find("##$NusTD= 8") >= 0
    else:
        expected = [
            _row_bytes((2 * z + z_phase) * 16 + 2 * y + y_phase)
            for y, z in schedule
            for z_phase in (0, 1)
            for y_phase in (0, 1)
        ]
        assert "##$NusTD= 20" in (first / "acqu3s").read_text()
    assert output_rows == expected


@pytest.mark.parametrize(
    ("ndim", "acq_name", "change", "fraction"),
    [
        (2, "acqus", {"AQSEQ": 1}, 0.5),
        (2, "acqu2s", {"FnMODE": 2}, 0.5),
        (3, "acqu3s", {"FnMODE": 3}, 0.5),
        (2, "acqus", {}, 0.0),
        (2, "acqus", {}, 1.0),
        (2, "acqus", {}, float("nan")),
        (2, "acqus", {}, float("inf")),
    ],
)
def test_invalid_input_is_rejected_without_partial_destination(
    tmp_path: Path,
    ndim: int,
    acq_name: str,
    change: dict[str, object],
    fraction: float,
) -> None:
    source = _make_source(tmp_path, ndim)
    if change:
        text = (source / acq_name).read_text()
        for key, value in change.items():
            text = re.sub(rf"(?m)^##\${key}= .*?$", f"##${key}= {value}", text)
        (source / acq_name).write_text(text, encoding="utf-8")
    destination = tmp_path / "out"

    with pytest.raises((ValueError, OSError)):
        make_nus(source, destination, fraction, 1)

    assert not destination.exists()


@pytest.mark.parametrize("failure", ["truncated", "existing_nus", "destination"])
def test_structural_refusals_do_not_write_output(tmp_path: Path, failure: str) -> None:
    source = _make_source(tmp_path, 2)
    destination = tmp_path / "out"
    if failure == "truncated":
        raw = source / "ser"
        raw.write_bytes(raw.read_bytes()[:-1])
    elif failure == "existing_nus":
        (source / "nuslist").write_text("0\n", encoding="ascii")
    else:
        destination.mkdir()

    with pytest.raises(ValueError):
        make_nus(source, destination, 0.5, 1)

    assert not (destination / "ser").exists()


def test_overlapping_directories_are_refused(tmp_path: Path) -> None:
    source = _make_source(tmp_path, 2)
    destination = source / "nested"

    with pytest.raises(ValueError):
        make_nus(source, destination, 0.5, 1)

    assert not destination.exists()


def test_3d_nustd_retains_real_td_and_schedule_shape_is_complex_grid(
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path, 3)
    destination = tmp_path / "out"

    manifest = make_nus(source, destination, 0.5, 5)

    coordinates = _coordinates(destination / "nuslist")
    assert all(len(coordinate) == 2 for coordinate in coordinates)
    assert (0, 0) in coordinates and (7, 9) in coordinates
    assert manifest["complex_grid"] == [8, 10]
    assert "##$NusTD= 16" in (destination / "acqu2s").read_text()
    assert "##$NusTD= 20" in (destination / "acqu3s").read_text()

    experiment = SimpleNamespace(
        ndim=3,
        acquisition_parameters={
            "acqu2s": {"NusTD": 16, "FnMODE": 4},
            "acqu3s": {"NusTD": 20, "FnMODE": 5},
        },
    )
    assert schedule_grid_shape(experiment) == tuple(manifest["complex_grid"])
