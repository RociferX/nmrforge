"""Regression coverage for signed 2D projection matching reports."""

from __future__ import annotations

import hashlib
import importlib.util
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


def _load_report():
    path = Path(__file__).resolve().parents[1] / "scripts" / "vm_projection_report.py"
    spec = importlib.util.spec_from_file_location("vm_projection_report", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_match_indices_requires_same_polarity_and_is_one_to_one():
    report = _load_report()
    left = np.array([[0.0, 0.0], [0.3, 0.0], [2.0, 2.0]])
    right = np.array([[0.1, 0.0], [0.4, 0.0], [2.01, 2.0]])
    pairs = report._match_indices(
        left, right, np.array([1, 1, -1]), np.array([1, 1, 1]),
        np.array([1.0, 1.0]),
    )
    assert pairs == [(0, 0), (1, 1)]
    assert len({i for i, _ in pairs}) == len(pairs)
    assert len({j for _, j in pairs}) == len(pairs)


def test_agreement_correlates_absolute_intensity_and_maps_widths_to_nuclei():
    report = _load_report()
    left = [
        {"intensity": value, "FWHM_F2": width, "FWHM_F1": 1.0}
        for value, width in ((1., 2.), (2., 4.), (4., 100.))
    ]
    right = [
        {"intensity": value, "FWHM_F2": width, "FWHM_F1": 2.0}
        for value, width in ((2., 1.), (4., 2.), (8., 0.))
    ]
    result = report._agreement(left, right, [(0, 0), (1, 1), (2, 2)], "HN")
    assert result["intensity_pearson"] == pytest.approx(1.0)
    assert result["median_fwhm_ratio_by_nucleus"] == {"H": 2.0, "N": 0.5}
    empty = report._agreement(left, right, [], "NC")
    assert empty == {
        "intensity_pearson": None,
        "median_fwhm_ratio_by_nucleus": {"N": None, "C": None},
    }


def test_detect_plane_passes_2d_axes_and_applies_baseline_centered_height_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    report = _load_report()
    synthetic_path = tmp_path / "synthetic.ft2"
    synthetic_path.write_bytes(b"synthetic spectrum placeholder")
    observed = {}
    rows = [
        {"F2_ppm": 7.1, "F1_ppm": 110.1, "intensity": 4.9},
        {"F2_ppm": 7.2, "F1_ppm": 110.2, "intensity": 5.0},
        {"F2_ppm": 7.3, "F1_ppm": 110.3, "intensity": -5.0},
    ]

    def fake_detect(path, *, axes, sigma_multiplier, sign_mode):
        observed["path"] = path
        observed["axes"] = axes
        observed["sigma"] = sigma_multiplier
        observed["sign_mode"] = sign_mode
        selected_rows = rows if sign_mode == "both" else rows[:2]
        return selected_rows, {"n_peaks": len(rows), "sign_mode": sign_mode}

    monkeypatch.setattr("nmrforge_api.peaks.detect_and_localize", fake_detect)
    monkeypatch.setattr(
        "core.qc.noise.estimate",
        lambda plane: SimpleNamespace(global_sigma=2.0, baseline=10.0),
    )
    plane = np.array([[10.0, 110.0], [-90.0, 10.0]])
    ppm_x = np.array([7.0, 8.0])
    ppm_y = np.array([110.0, 120.0])
    selected, points, signs, meta = report._detect_plane(
        synthetic_path, plane, ppm_x, ppm_y, "HN", 0.0, 0.05, sign_mode="dominant"
    )
    assert observed["path"] == synthetic_path
    assert observed["axes"].data.shape == (2, 2)
    assert observed["axes"].nuclei == ["15N", "1H"]
    assert observed["axes"].ppm == [ppm_y, ppm_x]
    assert observed["sigma"] == pytest.approx(2.5)  # 5% height / global noise sigma
    assert observed["sign_mode"] == "dominant"
    assert [row["intensity"] for row in selected] == [5.0]
    np.testing.assert_array_equal(points, [[7.2, 110.2]])
    np.testing.assert_array_equal(signs, [1])
    assert meta["max_abs_centered_voxel"] == 100.0
    assert meta["height_cutoff"] == 5.0
    assert meta["requested_sigma_floor"] == 0.0
    assert meta["resolved_sigma_multiplier"] == pytest.approx(2.5)
    assert meta["requested_sign_mode"] == "dominant"
    assert meta["sign_mode"] == "dominant"
    assert meta["positive_candidates"] == 1
    assert meta["negative_candidates"] == 0


def test_detect_plane_uses_actual_api_dominant_and_explicit_both_modes(tmp_path: Path):
    report = _load_report()
    synthetic_path = tmp_path / "synthetic.ft2"
    synthetic_path.write_bytes(b"path marker; the explicit synthetic axes are passed to the API")
    plane = np.zeros((64, 64), dtype=float)
    plane[10, 10] = 20.0
    plane[20, 20] = 18.0
    plane[30, 30] = 16.0
    plane[45, 45] = -19.0
    x_ppm = np.linspace(7.0, 9.0, 64)
    y_ppm = np.linspace(110.0, 120.0, 64)

    _, _, dominant_signs, dominant_meta = report._detect_plane(
        synthetic_path, plane, x_ppm, y_ppm, "HN", 0.0, 0.1,
        sign_mode="dominant",
    )
    _, _, both_signs, both_meta = report._detect_plane(
        synthetic_path, plane, x_ppm, y_ppm, "HN", 0.0, 0.1,
        sign_mode="both",
    )

    assert dominant_meta["sign_mode"] == "dominant"
    np.testing.assert_array_equal(dominant_signs, [1, 1, 1])
    assert both_meta["sign_mode"] == "both"
    np.testing.assert_array_equal(np.sort(both_signs), [-1, 1, 1, 1])

    negative_dominant = plane.copy()
    negative_dominant[10, 10] = -20.0
    negative_dominant[20, 20] = -18.0
    negative_dominant[30, 30] = -16.0
    negative_dominant[45, 45] = 19.0
    _, _, negative_signs, negative_meta = report._detect_plane(
        synthetic_path, negative_dominant, x_ppm, y_ppm, "HN", 0.0, 0.1,
        sign_mode="dominant",
    )
    assert negative_meta["sign_mode"] == "dominant"
    np.testing.assert_array_equal(negative_signs, [-1, -1, -1])


def test_compare_reselects_peaks_on_each_2d_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    report = _load_report()
    auto_path, ref_path = tmp_path / "auto.ft3", tmp_path / "ref.ft3"
    auto_path.write_bytes(b"auto")
    ref_path.write_bytes(b"reference")
    axes = {
        "H": np.linspace(7., 9., 5),
        "N": np.linspace(110., 114., 5),
        "C": np.linspace(40., 44., 5),
    }
    data = np.zeros((5, 5, 5), dtype=float)
    data[2, 2, 2] = 20.0
    spectrum = {"data": data, "ppm": [axes[n] for n in "HNC"], "nuclei": list("HNC")}
    monkeypatch.setattr(report, "_load", lambda path: spectrum)
    detector_calls = []

    def fake_detect(path, plane, x_ppm, y_ppm, pair, sigma, fraction, *, sign_mode):
        detector_calls.append(
            (Path(path).name, pair, plane.shape, len(x_ppm), len(y_ppm), sign_mode)
        )
        row = {"intensity": 10.0, "FWHM_F2": 1.0, "FWHM_F1": 1.0}
        point = np.array([[(x_ppm[0] + x_ppm[-1]) / 2,
                           (y_ppm[0] + y_ppm[-1]) / 2]])
        return [row], point, np.array([1]), {
            "count": 1, "sign_mode": sign_mode,
            "positive_candidates": 1, "negative_candidates": 0,
        }

    monkeypatch.setattr(report, "_detect_plane", fake_detect)
    result = report.compare(auto_path, ref_path, pairs="all")
    assert result["requested_sigma_floor"] == 0.0
    assert result["requested_sign_mode"] == "dominant"
    assert all(
        plane[side]["sign_mode"] == "dominant"
        for plane in result["planes"].values()
        for side in ("automatic", "comparison")
    )
    assert Counter(pair for _, pair, *_ in detector_calls) == {
        "HN": 2, "HC": 2, "NC": 2,
    }
    assert {mode for *_, mode in detector_calls} == {"dominant"}
    assert len(detector_calls) == 6
    assert all(shape == (y_len, x_len)
               for _, _, shape, x_len, y_len, _ in detector_calls)
    assert set(result["planes"]) == {"HN", "HC", "NC"}
    assert all(plane["matched"] == 1 for plane in result["planes"].values())


@pytest.mark.parametrize("pair, nuclei", [("HC", ["C", "H"]), ("NC", ["C", "N"])])
def test_compare_all_detects_and_matches_available_2d_non_hn_plane(
    pair, nuclei, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    report = _load_report()
    auto_path, ref_path = tmp_path / "auto.ft2", tmp_path / "ref.ft2"
    auto_path.write_bytes(b"auto")
    ref_path.write_bytes(b"reference")
    ppm = {
        "H": np.array([7.0, 8.0, 9.0]),
        "N": np.array([110.0, 111.0, 112.0]),
        "C": np.array([40.0, 41.0, 42.0]),
    }
    spectrum = {
        "data": np.arange(9.0).reshape(3, 3),
        "ppm": [ppm[n] for n in nuclei],
        "nuclei": nuclei,
    }
    monkeypatch.setattr(report, "_load", lambda path: spectrum)
    detector_calls = []

    def fake_detect(path, plane, x_ppm, y_ppm, detected_pair, sigma, fraction,
                    *, sign_mode):
        detector_calls.append((Path(path).name, detected_pair, plane.copy(),
                               x_ppm.copy(), y_ppm.copy()))
        return ([{"intensity": 10.0, "FWHM_F2": 1.0, "FWHM_F1": 2.0}],
                np.array([[x_ppm[1], y_ppm[1]]]), np.array([1]),
                {"count": 1, "sign_mode": sign_mode})

    monkeypatch.setattr(report, "_detect_plane", fake_detect)
    result = report.compare(auto_path, ref_path, pairs="all")

    assert set(result["planes"]) == {pair}
    assert len(detector_calls) == 2
    assert all(call[1] == pair for call in detector_calls)
    for _, _, plane, x_ppm, y_ppm in detector_calls:
        assert plane.shape == (len(ppm[pair[1]]), len(ppm[pair[0]]))
        np.testing.assert_array_equal(x_ppm, ppm[pair[0]])
        np.testing.assert_array_equal(y_ppm, ppm[pair[1]])
    plane_result = result["planes"][pair]
    assert plane_result["matched"] == 1
    assert plane_result["median_abs_delta_ppm"] == {n: 0.0 for n in pair}
    assert plane_result["median_fwhm_ratio_by_nucleus"] == {
        pair[0]: 1.0, pair[1]: 1.0,
    }


def test_compare_reports_asymmetric_coverage_and_unmatched_candidate_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    report = _load_report()
    auto_path, ref_path = tmp_path / "auto.ft2", tmp_path / "ref.ft2"
    auto_path.write_bytes(b"auto")
    ref_path.write_bytes(b"reference")
    spectrum = {
        "data": np.zeros((4, 4)),
        "ppm": [np.linspace(110.0, 113.0, 4), np.linspace(7.0, 10.0, 4)],
        "nuclei": ["N", "H"],
    }
    monkeypatch.setattr(report, "_load", lambda path: spectrum)
    auto_rows = [
        {"intensity": intensity, "FWHM_F2": 1.0, "FWHM_F1": 1.0}
        for intensity in (10.0, -8.0, 7.0, 6.0)
    ]
    ref_rows = [
        {"intensity": intensity, "FWHM_F2": 1.0, "FWHM_F1": 1.0}
        for intensity in (9.0, 8.0, -5.0)
    ]
    candidates = {
        "auto.ft2": (auto_rows, np.array([
            [7.0, 110.0], [8.0, 111.0], [9.0, 112.0], [10.0, 113.0],
        ]), np.array([1, -1, 1, 1])),
        "ref.ft2": (ref_rows, np.array([
            [7.005, 110.05], [8.0, 111.0], [11.0, 113.0],
        ]), np.array([1, 1, -1])),
    }

    def fake_detect(path, *args, sign_mode):
        rows, points, signs = candidates[Path(path).name]
        return rows, points, signs, {
            "sign_mode": sign_mode, "count": len(rows),
            "positive_candidates": int((signs > 0).sum()),
            "negative_candidates": int((signs < 0).sum()),
        }

    monkeypatch.setattr(report, "_detect_plane", fake_detect)
    plane = report.compare(auto_path, ref_path, pairs="HN")["planes"]["HN"]

    assert plane["matched"] == 1
    assert plane["automatic_matched_fraction"] == pytest.approx(1 / 4)
    assert plane["reference_candidate_coverage"] == pytest.approx(1 / 3)
    assert plane["automatic_unmatched_candidates"] == [
        {"candidate_index_1based": 2, "ppm": {"H": 8.0, "N": 111.0},
         "intensity": -8.0, "sign": -1},
        {"candidate_index_1based": 3, "ppm": {"H": 9.0, "N": 112.0},
         "intensity": 7.0, "sign": 1},
        {"candidate_index_1based": 4, "ppm": {"H": 10.0, "N": 113.0},
         "intensity": 6.0, "sign": 1},
    ]
    assert plane["reference_unmatched_candidates"] == [
        {"candidate_index_1based": 2, "ppm": {"H": 8.0, "N": 111.0},
         "intensity": 8.0, "sign": 1},
        {"candidate_index_1based": 3, "ppm": {"H": 11.0, "N": 113.0},
         "intensity": -5.0, "sign": -1},
    ]


def test_compare_empty_reference_candidates_have_no_coverage(tmp_path: Path, monkeypatch):
    report = _load_report()
    auto_path, ref_path = tmp_path / "auto.ft2", tmp_path / "ref.ft2"
    auto_path.write_bytes(b"auto")
    ref_path.write_bytes(b"reference")
    spectrum = {
        "data": np.zeros((3, 3)),
        "ppm": [np.array([110., 111., 112.]), np.array([7., 8., 9.])],
        "nuclei": ["N", "H"],
    }
    monkeypatch.setattr(report, "_load", lambda path: spectrum)

    def fake_detect(path, plane, x_ppm, y_ppm, pair, sigma, fraction, *, sign_mode):
        if Path(path) == auto_path:
            return ([{"intensity": 1.0}], np.array([[8.0, 111.0]]), np.array([1]),
                    {"count": 1, "sign_mode": sign_mode})
        return [], np.empty((0, 2)), np.empty(0, dtype=int), {
            "count": 0, "sign_mode": sign_mode,
        }

    monkeypatch.setattr(report, "_detect_plane", fake_detect)
    plane = report.compare(auto_path, ref_path, pairs="HN")["planes"]["HN"]

    assert plane["reference_candidate_coverage"] is None
    assert plane["automatic_unmatched_candidates"] == [{
        "candidate_index_1based": 1, "ppm": {"H": 8.0, "N": 111.0},
        "intensity": 1.0, "sign": 1,
    }]
    assert plane["reference_unmatched_candidates"] == []


def test_compare_applies_explicit_reference_shift_before_shared_crop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    report = _load_report()
    auto_path, ref_path = tmp_path / "auto.ft3", tmp_path / "ref.ft3"
    auto_path.write_bytes(b"auto bytes")
    ref_path.write_bytes(b"reference bytes")
    axes = {
        "H": np.linspace(7.0, 11.0, 5),
        "N": np.linspace(110.0, 114.0, 5),
        "C": np.linspace(40.0, 44.0, 5),
    }
    reference_axes = {n: values.copy() for n, values in axes.items()}
    reference_axes["H"] -= 1.0
    data = np.zeros((5, 5, 5), dtype=float)
    automatic = {"data": data.copy(), "ppm": [axes[n] for n in "HNC"],
                 "nuclei": list("HNC")}
    reference = {"data": data.copy(), "ppm": [reference_axes[n] for n in "HNC"],
                 "nuclei": list("HNC")}
    monkeypatch.setattr(report, "_load", lambda path: automatic if path == auto_path else reference)
    seen = []

    def fake_detect(path, plane, x_ppm, y_ppm, pair, sigma, fraction, *, sign_mode):
        seen.append((Path(path).name, pair, x_ppm.copy(), y_ppm.copy(), plane.shape))
        assert sign_mode == "dominant"
        return [], np.empty((0, 2)), np.empty(0, dtype=int), {"count": 0}

    monkeypatch.setattr(report, "_detect_plane", fake_detect)
    result = report.compare(auto_path, ref_path, pairs="HN", reference_offsets={"H": 1.0})

    assert len(seen) == 2
    assert all(np.min(x) == pytest.approx(7.0) and np.max(x) == pytest.approx(11.0)
               for _, _, x, _, _ in seen)
    assert all(shape == (5, 5) for _, _, _, _, shape in seen)
    assert result["reference_offset_ppm"] == {"H": 1.0}
    assert result["offset_source"] == "explicit"
    assert result["offset_fitted"] is False
    assert result["spectrum_sha256"]["comparison"] == hashlib.sha256(
        b"reference bytes"
    ).hexdigest()


def test_compare_without_reference_shift_keeps_legacy_report_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    report = _load_report()
    auto_path, ref_path = tmp_path / "auto.ft2", tmp_path / "ref.ft2"
    auto_path.write_bytes(b"auto")
    ref_path.write_bytes(b"reference")
    spectrum = {
        "data": np.zeros((3, 3)),
        "ppm": [np.array([110.0, 111.0, 112.0]), np.array([7.0, 8.0, 9.0])],
        "nuclei": ["N", "H"],
    }
    monkeypatch.setattr(report, "_load", lambda path: spectrum)
    monkeypatch.setattr(
        report, "_detect_plane",
        lambda *args, **kwargs: (
            [], np.empty((0, 2)), np.empty(0, dtype=int), {"count": 0}
        ),
    )

    result = report.compare(auto_path, ref_path, pairs="HN")

    assert result["reference_offset_ppm"] == {}
    assert result["offset_source"] == "none"
    assert result["offset_fitted"] is False


def test_report_cli_forwards_explicit_sign_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    report = _load_report()
    seen = {}

    def fake_compare(spectrum, reference, **kwargs):
        seen.update(kwargs)
        return {"requested_sign_mode": kwargs["sign_mode"]}

    monkeypatch.setattr(report, "compare", fake_compare)
    output = tmp_path / "report.json"
    assert report.main([
        "--spectrum", str(tmp_path / "auto.ft2"),
        "--reference", str(tmp_path / "ref.ft2"),
        "--pairs", "HN", "--sign-mode", "both", "--tol-h", "0.04",
        "--tol-n", "0.3", "--tol-c", "0.5", "--json", str(output),
    ]) == 0
    assert seen["sign_mode"] == "both"
    assert seen["tolerances"] == {"H": 0.04, "N": 0.3, "C": 0.5}
    assert '"requested_sign_mode": "both"' in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("option, value", [("--tol-h", "0"), ("--tol-n", "inf"),
                                             ("--tol-c", "nan")])
def test_report_cli_rejects_invalid_tolerances(tmp_path: Path, capsys, option, value):
    report = _load_report()
    with pytest.raises(SystemExit):
        report.main([
            "--spectrum", str(tmp_path / "auto.ft2"),
            "--reference", str(tmp_path / "ref.ft2"),
            f"{option}={value}", "--json", str(tmp_path / "report.json"),
        ])
    assert "all nuclear tolerances must be positive and finite" in capsys.readouterr().err


def test_compare_empty_candidates_use_null_matched_fractions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    report = _load_report()
    auto_path, ref_path = tmp_path / "auto.ft2", tmp_path / "ref.ft2"
    auto_path.write_bytes(b"auto")
    ref_path.write_bytes(b"reference")
    data = np.zeros((4, 4), dtype=float)
    spectrum = {
        "data": data,
        "ppm": [np.linspace(110., 113., 4), np.linspace(7., 10., 4)],
        "nuclei": ["N", "H"],
    }
    monkeypatch.setattr(report, "_load", lambda path: spectrum)
    monkeypatch.setattr(
        report, "_detect_plane",
        lambda *args, **kwargs: (
            [], np.empty((0, 2)), np.empty(0, dtype=int), {"count": 0}
        ),
    )
    plane = report.compare(auto_path, ref_path, pairs="HN")["planes"]["HN"]
    assert plane["automatic_matched_fraction"] is None
    assert plane["comparison_matched_fraction"] is None
    assert plane["median_abs_delta_ppm"] == {"H": None, "N": None}


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"sigma": -0.1}, "sigma floor must be nonnegative and finite"),
        ({"sigma": np.inf}, "sigma floor must be nonnegative and finite"),
        ({"min_height_fraction": -0.01}, "height fraction must be finite"),
        ({"min_height_fraction": 1.01}, "height fraction must be finite"),
        ({"min_height_fraction": np.nan}, "height fraction must be finite"),
        ({"sign_mode": "unknown"}, "unknown peak sign mode"),
        ({"tolerances": {"H": 0., "N": .2, "C": .1}},
         "nuclear tolerances must be positive and finite"),
        ({"tolerances": {"H": .02, "N": np.nan, "C": .1}},
         "nuclear tolerances must be positive and finite"),
        ({"sigma": 0.0, "min_height_fraction": 0.0},
         "at least one positive detection threshold is required"),
    ],
)
def test_compare_rejects_invalid_parameters(kwargs, message):
    with pytest.raises(ValueError, match=message):
        _load_report().compare(Path("unused-a"), Path("unused-b"), **kwargs)
