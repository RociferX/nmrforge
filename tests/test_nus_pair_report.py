"""Unit tests for signed, joint-distance NUS peak correspondence."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


def _load_tool():
    path = Path(__file__).resolve().parents[1] / "scripts" / "vm_nus_pair_report.py"
    spec = importlib.util.spec_from_file_location("vm_nus_pair_report", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _row(coordinates, intensity=1.0, families=("H", "N", "C")):
    row = {"intensity": intensity}
    for logical, (family, value) in enumerate(zip(families, coordinates), start=1):
        row[f"F{logical}_nucleus"] = family
        row[f"F{logical}_ppm"] = value
    return row


def test_points_use_logical_f_axis_identity_for_full_hnc_coordinates():
    tool = _load_tool()
    row = {
        "F1_nucleus": "13C",
        "F1_ppm": 52.0,
        "F2_nucleus": "1H",
        "F2_ppm": 8.1,
        "F3_nucleus": "15N",
        "F3_ppm": 121.5,
        "intensity": -4.0,
    }

    assert tool._points([row], ["C", "H", "N"], {"C": [40, 60], "H": [7, 9], "N": [110, 130]}) == [
        ([52.0, 8.1, 121.5], -1)
    ]


def test_points_discards_peaks_outside_any_common_axis_window():
    tool = _load_tool()
    row = _row((8.0, 121.0, 61.0))

    assert tool._points([row], ["H", "N", "C"], {"H": [7, 9], "N": [110, 130], "C": [40, 60]}) == []


@pytest.mark.parametrize(
    "row, message",
    [
        (
            {
                "F1_nucleus": "1H",
                "F1_ppm": 8.0,
                "F2_nucleus": "H",
                "F2_ppm": 8.1,
                "F3_nucleus": "15N",
                "F3_ppm": 120.0,
                "intensity": 1.0,
            },
            "repeated nuclei",
        ),
        ({"F1_nucleus": "H", "F1_ppm": 8.0, "height": 1.0}, "complete logical-axis"),
        (_row((8.0, np.nan, 50.0)), "non-finite"),
        (_row((8.0, 120.0, 50.0), intensity=0.0), "invalid signed height"),
    ],
)
def test_points_rejects_invalid_peak_rows(row, message):
    tool = _load_tool()

    with pytest.raises(ValueError, match=message):
        tool._points([row], ["H", "N", "C"], {"H": [7, 9], "N": [110, 130], "C": [40, 60]})


def test_match_requires_joint_normalized_distance_at_most_one():
    tool = _load_tool()
    source = [([0.8, 0.8], 1)]
    target = [([0.0, 0.0], 1)]

    assert tool._match(source, target, np.array([1.0, 1.0])) == []
    assert tool._match(source, target, np.array([1.0, 2.0]))


def test_match_rejects_opposite_polarity():
    assert _load_tool()._match([([0.0], 1)], [([0.0], -1)], np.array([1.0])) == []


def test_match_uses_closest_first_and_never_reuses_a_peak():
    differences = _load_tool()._match(
        [([0.0], 1), ([0.2], 1)],
        [([0.1], 1), ([0.4], 1)],
        np.array([1.0]),
    )

    assert len(differences) == 2
    np.testing.assert_allclose(differences, [[-0.1], [-0.2]])


def test_match_empty_inputs_return_empty_list():
    assert _load_tool()._match([], [([0.0], 1)], np.array([1.0])) == []
    assert _load_tool()._match([([0.0], 1)], [], np.array([1.0])) == []


def test_3d_match_rejects_carbon_displacement_when_hn_matches():
    tolerance = np.array([0.02, 0.2, 0.15])
    source = [([8.0, 120.0, 50.0], 1)]
    target = [([8.0, 120.0, 50.2], 1)]

    assert _load_tool()._match(source, target, tolerance) == []


def test_compare_reports_all_dimensions_unfitted_fraction_and_hashes(tmp_path, monkeypatch):
    tool = _load_tool()
    uniform = tmp_path / "uniform.ft3"
    nus = tmp_path / "nus.ft3"
    uniform.write_bytes(b"uniform spectrum bytes")
    nus.write_bytes(b"nus spectrum bytes")
    nuclei = ["1H", "15N", "13C"]
    scales = [np.linspace(7, 9, 16), np.linspace(110, 130, 16), np.linspace(40, 60, 16)]
    axes = SimpleNamespace(data=np.zeros((16, 16, 16)), nuclei=nuclei, ppm=scales)
    pick_peaks = importlib.import_module("workflow.pick_peaks")
    api_peaks = importlib.import_module("nmrforge_api.peaks")
    monkeypatch.setattr(pick_peaks, "read_spectrum_axes", lambda path: axes)

    rows_by_path = {
        uniform.name: [_row((8.0, 120.0, 50.0)), _row((8.5, 125.0, 55.0))],
        nus.name: [_row((8.01, 120.05, 50.02))],
    }

    def detect(path, **_kwargs):
        return rows_by_path[Path(path).name], {}

    monkeypatch.setattr(api_peaks, "detect_and_localize", detect)

    report = tool.compare(
        uniform,
        nus,
        35.0,
        {"H": 0.02, "N": 0.2, "C": 0.15},
    )

    assert report["nuclei"] == ["C", "H", "N"]
    assert report["ndim"] == 3
    assert report["offset_fitted"] is False
    assert report["matched"] == 1
    assert report["uniform_matched_fraction"] == 0.5
    assert report["nus_matched_fraction"] == 1.0
    assert set(report["median_abs_delta_ppm"]) == {"C", "H", "N"}
    assert set(report["windows_ppm"]) == {"C", "H", "N"}
    assert set(report["spectrum_sha256"]) == {"uniform", "nus"}
