"""Regression coverage for signed HN projections in the four-path figure tool."""

from __future__ import annotations

import importlib.util
import itertools
from pathlib import Path

import numpy as np
import pytest


def _load_tool():
    path = Path(__file__).resolve().parents[1] / "scripts" / "vm_four_path_figure.py"
    spec = importlib.util.spec_from_file_location("vm_four_path_figure", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("nuclei", [("H", "N"), ("N", "H")])
def test_2d_hn_plane_preserves_axis_identity_and_sign(nuclei):
    tool = _load_tool()
    data = np.arange(6, dtype=float).reshape(2, 3) - 4
    axes = (np.array([7, 8]), np.array([110, 120, 130]))
    by_nucleus = {nucleus: axis for nucleus, axis in zip(nuclei, axes)}
    plane, ppm_h, ppm_n = tool._hn_plane(
        {"data": data, "ppm": [by_nucleus[n] for n in nuclei], "nuclei": list(nuclei)}
    )
    expected = data if nuclei == ("N", "H") else data.T
    np.testing.assert_array_equal(plane, expected)
    np.testing.assert_array_equal(ppm_h, by_nucleus["H"])
    np.testing.assert_array_equal(ppm_n, by_nucleus["N"])
    assert np.min(plane) < 0


@pytest.mark.parametrize("pair", ["HC", "NC"])
def test_common_spectra_accepts_explicit_non_hn_2d_axes(pair):
    tool = _load_tool()
    spectrum = {"data": np.arange(9, dtype=float).reshape(3, 3),
                "ppm": [np.array([3., 2., 1.]), np.array([6., 5., 4.])],
                "nuclei": list(pair[::-1])}
    left, right = tool._common_spectra(spectrum, spectrum)
    assert tool._available_pairs(left) == [pair]
    np.testing.assert_array_equal(left["data"], right["data"])
    np.testing.assert_array_equal(tool._projection_plane(left, pair)[0], spectrum["data"])


@pytest.mark.parametrize("nuclei", list(itertools.permutations(("H", "N", "C"))))
def test_3d_projection_selects_signed_absolute_max_and_hn_coordinates(nuclei):
    tool = _load_tool()
    axes = {nucleus: index for index, nucleus in enumerate(nuclei)}
    data = np.zeros((3, 3, 3), dtype=float)
    strongest = [0, 0, 0]
    strongest[axes["H"]] = 1
    strongest[axes["N"]] = 2
    strongest[axes["C"]] = 0
    weaker = strongest.copy()
    weaker[axes["C"]] = 1
    data[tuple(strongest)] = -9
    data[tuple(weaker)] = 8
    ppm_by_nucleus = {
        "H": np.array([7.0, 8.0, 9.0]),
        "N": np.array([110.0, 120.0, 130.0]),
        "C": np.array([40.0, 50.0, 60.0]),
    }
    spectrum = {
        "data": data,
        "ppm": [ppm_by_nucleus[nucleus] for nucleus in nuclei],
        "nuclei": list(nuclei),
    }
    plane, ppm_h, ppm_n = tool._hn_plane(spectrum)
    assert plane[2, 1] == -9
    assert np.max(np.abs(plane)) == 9
    np.testing.assert_array_equal(ppm_h, ppm_by_nucleus["H"])
    np.testing.assert_array_equal(ppm_n, ppm_by_nucleus["N"])


@pytest.mark.parametrize("nuclei", list(itertools.permutations(("H", "N", "C"))))
@pytest.mark.parametrize("pair", ["HN", "HC", "NC"])
def test_projection_plane_preserves_signed_maximum_and_pair_axes(nuclei, pair):
    tool = _load_tool()
    positions = {nucleus: index for index, nucleus in enumerate(nuclei)}
    data = np.zeros((3, 3, 3), dtype=float)
    peak = [0, 0, 0]
    collapsed = next(nucleus for nucleus in "HNC" if nucleus not in pair)
    peak[positions[pair[0]]] = 1
    peak[positions[pair[1]]] = 2
    data[tuple(peak)] = -9.0
    weaker = peak.copy()
    weaker[positions[collapsed]] = 1
    data[tuple(weaker)] = 8.0
    ppm_by_nucleus = {
        "H": np.array([7.0, 8.0, 9.0]),
        "N": np.array([110.0, 120.0, 130.0]),
        "C": np.array([40.0, 50.0, 60.0]),
    }
    spectrum = {
        "data": data,
        "ppm": [ppm_by_nucleus[nucleus] for nucleus in nuclei],
        "nuclei": list(nuclei),
    }

    plane, ppm_x, ppm_y = tool._projection_plane(spectrum, pair)
    assert plane[peak[positions[pair[1]]], peak[positions[pair[0]]]] == -9.0
    assert np.max(np.abs(plane)) == 9.0
    np.testing.assert_array_equal(ppm_x, ppm_by_nucleus[pair[0]])
    np.testing.assert_array_equal(ppm_y, ppm_by_nucleus[pair[1]])


@pytest.mark.parametrize(
    "spectrum, message",
    [
        ({"data": np.zeros((2, 2)), "ppm": [np.arange(2)] * 2,
          "nuclei": ["H", "H"]}, "repeated"),
        ({"data": np.zeros((2, 2)), "ppm": [np.arange(2)] * 2,
          "nuclei": ["H", "C"]}, "lacks HN axes"),
        ({"data": np.zeros((2, 2)), "ppm": [np.array([1.0, np.nan]), np.arange(2)],
          "nuclei": ["H", "N"]}, "finite and strictly monotonic"),
        ({"data": np.zeros((2, 2)), "ppm": [np.array([1.0, 1.0]), np.arange(2)],
          "nuclei": ["H", "N"]}, "finite and strictly monotonic"),
        ({"data": np.zeros((2, 2)), "ppm": [np.arange(3), np.arange(2)],
          "nuclei": ["H", "N"]}, "does not match spectrum shape"),
    ],
)
def test_projection_plane_rejects_invalid_axis_metadata(spectrum, message):
    with pytest.raises(SystemExit, match=message):
        _load_tool()._projection_plane(spectrum, "HN")


def test_common_spectra_crops_collapsed_axis_before_projection():
    tool = _load_tool()
    left_nuclei = ["H", "N", "C"]
    right_nuclei = ["C", "H", "N"]
    ppm_by_nucleus = {
        "H": np.array([7., 8., 9., 10., 11.]),
        "N": np.array([110., 111., 112., 113., 114.]),
        "C": np.array([40., 41., 42., 43., 44.]),
    }
    right_ppm_by_nucleus = dict(ppm_by_nucleus)
    right_ppm_by_nucleus["C"] = np.array([41., 42., 43., 44., 45.])
    left_data = np.zeros((5, 5, 5), dtype=float)
    right_data = np.zeros((5, 5, 5), dtype=float)
    # Largest magnitudes lie outside the shared carbon range and must be cropped.
    left_data[1, 2, 0] = -99.0
    left_data[1, 2, 1] = -9.0
    right_data[4, 1, 2] = 88.0  # right storage order is C,H,N; C index 4 is 45 ppm
    right_data[0, 1, 2] = -8.0  # C index 0 is shared; signed maximum should survive
    left = {"data": left_data, "ppm": [ppm_by_nucleus[n] for n in left_nuclei],
            "nuclei": left_nuclei}
    right = {"data": right_data,
             "ppm": [right_ppm_by_nucleus[n] for n in right_nuclei],
             "nuclei": right_nuclei}

    cropped_left, cropped_right = tool._common_spectra(left, right)
    assert cropped_left["data"].shape == (5, 5, 4)
    assert cropped_right["data"].shape == (4, 5, 5)
    left_projection, *_ = tool._projection_plane(cropped_left, "HN")
    right_projection, *_ = tool._projection_plane(cropped_right, "HN")
    assert np.max(np.abs(left_projection)) == 9.0
    assert np.max(np.abs(right_projection)) == 8.0


@pytest.mark.parametrize("nuclei", list(itertools.permutations(("H", "N", "C"))))
def test_3d_nc_slice_uses_nearest_h_grid_and_preserves_coordinates(nuclei):
    tool = _load_tool()
    positions = {nucleus: index for index, nucleus in enumerate(nuclei)}
    data = np.zeros((3, 3, 3), dtype=float)
    peak = [0, 0, 0]
    peak[positions["H"]] = 1
    peak[positions["N"]] = 2
    peak[positions["C"]] = 1
    data[tuple(peak)] = -7.0
    ppm_by_nucleus = {
        "H": np.array([7.1, 8.1, 9.1]),
        "N": np.array([110.0, 120.0, 130.0]),
        "C": np.array([40.0, 50.0, 60.0]),
    }
    spectrum = {
        "data": data,
        "ppm": [ppm_by_nucleus[nucleus] for nucleus in nuclei],
        "nuclei": list(nuclei),
    }

    plane, ppm_n, ppm_c, selected_h = tool._nc_slice(spectrum, 8.2)

    assert plane.shape == (3, 3)
    assert plane[1, 2] == -7.0
    np.testing.assert_array_equal(ppm_n, ppm_by_nucleus["N"])
    np.testing.assert_array_equal(ppm_c, ppm_by_nucleus["C"])
    assert selected_h == 8.1


@pytest.mark.parametrize(
    "spectrum, h_ppm, message",
    [
        (
            {"data": np.zeros((2, 2)), "ppm": [np.arange(2)] * 2, "nuclei": ["H", "N"]},
            1.0,
            "H slice requires",
        ),
        (
            {
                "data": np.zeros((2, 2, 2)),
                "ppm": [np.array([7.0, 8.0]), np.arange(2), np.arange(2)],
                "nuclei": ["H", "N", "C"],
            },
            8.1,
            "outside the spectrum",
        ),
        (
            {
                "data": np.zeros((2, 2, 2)),
                "ppm": [np.array([7.0, 8.0]), np.arange(2), np.arange(2)],
                "nuclei": ["H", "N", "C"],
            },
            np.nan,
            "must be finite",
        ),
    ],
)
def test_nc_slice_rejects_unsupported_or_invalid_request(spectrum, h_ppm, message):
    with pytest.raises(SystemExit, match=message):
        _load_tool()._nc_slice(spectrum, h_ppm)


@pytest.mark.parametrize(
    "spectrum, message",
    [
        (
            {"data": np.zeros((2, 2)), "ppm": [np.arange(2)] * 2,
             "nuclei": ["H", "C"]},
            "lacks HN axes",
        ),
        ({"data": np.zeros((2, 2)), "ppm": [np.arange(2)] * 2, "nuclei": ["H", "H"]}, "repeated"),
        (
            {
                "data": np.array([[0.0, np.nan], [1.0, 2.0]]),
                "ppm": [np.arange(2)] * 2,
                "nuclei": ["H", "N"],
            },
            "non-finite",
        ),
    ],
)
def test_invalid_2d_spectra_fail_explicitly(spectrum, message):
    with pytest.raises(SystemExit, match=message):
        _load_tool()._hn_plane(spectrum)


def test_pdata_nuclei_require_explicit_unique_labels():
    tool = _load_tool()
    ppm = [np.array([0.0, 1.0]), np.array([100.0, 101.0])]
    assert tool._assign_pdata_nuclei(ppm, [{"label": "1H"}, {"nuc1": "15N"}]) == ["H", "N"]
    for udic in ([{}, {}], [{"label": "1H"}, {"label": "1H"}], [{"label": "1H"}, {}]):
        with pytest.raises(SystemExit, match="ppm ranges cannot identify nuclei"):
            tool._assign_pdata_nuclei(ppm, udic)


def test_crop_preserves_negative_values_and_empty_common_window_fails():
    tool = _load_tool()
    plane = np.array([[0.0, -1.0, -2.0], [3.0, -4.0, 5.0], [6.0, 7.0, -8.0]])
    block, _, _ = tool._crop(
        plane,
        np.array([7.0, 8.0, 9.0]),
        np.array([110.0, 120.0, 130.0]),
        7,
        9,
        110,
        130,
    )
    assert np.min(block) == -8
    with pytest.raises(SystemExit, match="share no ppm window"):
        tool._window(np.array([7.0, 8.0]), np.array([9.0, 10.0]))


@pytest.mark.parametrize("nuclei", list(itertools.permutations(("H", "N", "C"))))
def test_shift_spectrum_moves_only_unique_requested_ppm_axes(nuclei):
    tool = _load_tool()
    data = np.arange(27, dtype=float).reshape(3, 3, 3)
    axes = {
        "H": np.array([7.0, 8.0, 9.0]),
        "N": np.array([110.0, 111.0, 112.0]),
        "C": np.array([40.0, 41.0, 42.0]),
    }
    spectrum = {"data": data, "ppm": [axes[n] for n in nuclei], "nuclei": list(nuclei)}
    shifted = tool._shift_spectrum(spectrum, {"H": 0.25, "C": -1.5})

    assert shifted is not spectrum
    assert shifted["data"] is data
    np.testing.assert_array_equal(spectrum["ppm"][nuclei.index("H")], axes["H"])
    for nucleus in nuclei:
        np.testing.assert_array_equal(
            shifted["ppm"][nuclei.index(nucleus)],
            axes[nucleus] + ({"H": 0.25, "C": -1.5}.get(nucleus, 0.0)),
        )
    np.testing.assert_array_equal(shifted["data"], data)


@pytest.mark.parametrize(
    "spectrum, offsets",
    [
        ({"data": np.zeros((2, 2)), "ppm": [np.arange(2)] * 2,
          "nuclei": ["H", "N"]}, {"X": 1.0}),
        ({"data": np.zeros((2, 2)), "ppm": [np.arange(2)] * 2,
          "nuclei": ["H", "N"]}, {"H": np.inf}),
        ({"data": np.zeros((2, 2)), "ppm": [np.arange(2)] * 2,
          "nuclei": ["H", "N"]}, {"C": 1.0}),
        ({"data": np.zeros((2, 2)), "ppm": [np.arange(2)] * 2,
          "nuclei": ["H", "H"]}, {"H": 1.0}),
    ],
)
def test_shift_spectrum_rejects_invalid_nonzero_axis_offsets(spectrum, offsets):
    with pytest.raises(ValueError):
        _load_tool()._shift_spectrum(spectrum, offsets)


@pytest.mark.parametrize("offset", [float("nan"), float("inf"), float("-inf")])
def test_main_rejects_nonfinite_reference_shifts(tmp_path, capsys, offset):
    tool = _load_tool()
    with pytest.raises(SystemExit):
        tool.main(["--case", "unused,a,b", "--out", str(tmp_path / "figure.png"),
                   f"--reference-shift-h={offset}"])
    assert "reference offsets must be finite" in capsys.readouterr().err


def test_main_smoke_draws_negative_peak_with_real_matplotlib(tmp_path, monkeypatch):
    tool = _load_tool()
    auto_path = tmp_path / "auto.ft2"
    ref_path = tmp_path / "reference.ft2"
    auto_path.touch()
    ref_path.touch()
    ppm_h = np.linspace(7.0, 9.0, 9)
    ppm_n = np.linspace(110.0, 118.0, 9)
    data = np.zeros((9, 9), dtype=float)
    data[4, 4] = -10.0
    spectrum = {"data": data, "ppm": [ppm_n, ppm_h], "nuclei": ["N", "H"]}
    monkeypatch.setattr(tool, "_load", lambda path: spectrum)
    output = tmp_path / "nested" / "figure.png"
    assert tool.main(["--case", f"smoke,{auto_path},{ref_path}", "--out", str(output)]) == 0
    assert output.is_file()
    assert output.stat().st_size > 0


def test_main_h_slice_smoke_draws_mocked_3d_spectra(tmp_path, monkeypatch):
    tool = _load_tool()
    auto_path = tmp_path / "auto.ft3"
    ref_path = tmp_path / "reference.ft3"
    auto_path.touch()
    ref_path.touch()
    data = np.zeros((9, 9, 9), dtype=float)
    data[4, 4, 4] = -10.0
    spectrum = {
        "data": data,
        "ppm": [np.linspace(7.0, 9.0, 9), np.linspace(110.0, 118.0, 9), np.linspace(40.0, 48.0, 9)],
        "nuclei": ["H", "N", "C"],
    }
    monkeypatch.setattr(tool, "_load", lambda path: spectrum)
    output = tmp_path / "nested" / "nc-slice.png"

    assert tool.main(
        ["--case", f"smoke,{auto_path},{ref_path}", "--out", str(output), "--h-slice", "8"]
    ) == 0
    assert output.is_file()
    assert output.stat().st_size > 0


def test_main_mark_peaks_detects_cropped_plane_and_passes_markers(
    tmp_path, monkeypatch,
):
    import scripts.vm_projection_report as report_module

    tool = _load_tool()
    auto_path = tmp_path / "auto.ft2"
    ref_path = tmp_path / "reference.ft2"
    auto_path.touch()
    ref_path.touch()
    axes = [np.linspace(7.0, 9.0, 9), np.linspace(110.0, 118.0, 9)]
    data = np.zeros((9, 9), dtype=float)
    data[4, 4] = -10.0
    spectrum = {"data": data, "ppm": axes, "nuclei": ["H", "N"]}
    monkeypatch.setattr(tool, "_load", lambda path: spectrum)
    marker_sizes = []
    original_draw = tool._draw

    def capture_draw(*args, **kwargs):
        marker_sizes.append(kwargs["peak_marker_size"])
        original_draw(*args, **kwargs)

    monkeypatch.setattr(tool, "_draw", capture_draw)
    detections = []

    def fake_detect(path, plane, x_ppm, y_ppm, pair, sigma, fraction, *, sign_mode):
        detections.append((Path(path).name, plane.shape, pair, sigma, fraction, sign_mode))
        return ([{"intensity": -1.0}], np.array([[8.0, 114.0]]),
                np.array([-1]), {"count": 1})

    monkeypatch.setattr(report_module, "_detect_plane", fake_detect)
    output = tmp_path / "marked.png"
    assert tool.main([
        "--case", f"smoke,{auto_path},{ref_path}", "--out", str(output),
        "--mark-peaks", "--peak-height-fraction", "0.2", "--peak-marker-size", "9",
    ]) == 0
    assert output.is_file() and output.stat().st_size > 0
    assert detections == [
        ("auto.ft2", (9, 9), "HN", 0.0, 0.2, "dominant"),
        ("reference.ft2", (9, 9), "HN", 0.0, 0.2, "dominant"),
    ]
    detections.clear()
    explicit_both = tmp_path / "marked-both.png"
    assert tool.main([
        "--case", f"smoke,{auto_path},{ref_path}", "--out", str(explicit_both),
        "--mark-peaks", "--peak-sign-mode", "both",
    ]) == 0
    assert explicit_both.is_file() and explicit_both.stat().st_size > 0
    assert [call[-1] for call in detections] == ["both", "both"]
    assert marker_sizes == [9, 9, 4, 4]


def test_draws_positive_and_negative_candidates_as_red_dots_and_keeps_unmatched_x():
    import matplotlib.pyplot as plt

    tool = _load_tool()
    figure, ax = plt.subplots()
    calls = []
    scatter = ax.scatter

    def capture_scatter(x, y, *args, **kwargs):
        calls.append((np.column_stack((x, y)), kwargs.get("marker"), kwargs.get("color"),
                      kwargs.get("s"), kwargs.get("linewidths")))
        return scatter(x, y, *args, **kwargs)

    ax.scatter = capture_scatter
    points = np.array([[7., 110.], [8., 111.], [9., 112.], [10., 113.]])
    tool._draw(
        ax, np.array([[1., 0., 0.], [0., 0., 0.], [0., 0., -1.]]),
        np.array([7., 8., 9.]), np.array([110., 111., 112.]), "matched test",
        peak_points=points, peak_signs=np.array([1, 1, -1, -1]),
        peak_matched=np.array([True, False, True, False]),
    )

    assert len(calls) == 2
    np.testing.assert_array_equal(calls[0][0], points[[0, 2]])
    assert calls[0][1:] == (".", "#d62728", 4, 0)
    np.testing.assert_array_equal(calls[1][0], points[[1, 3]])
    assert calls[1][1:] == ("x", "#b12cbd", 4, .5)
    plt.close(figure)


def test_default_contour_levels_start_at_7_5_percent_for_both_signs():
    import matplotlib.pyplot as plt

    tool = _load_tool()
    assert tool.LEVELS == (0.075, 0.1, 0.2, 0.35, 0.5, 0.7, 0.9)
    figure, ax = plt.subplots()
    calls = []

    def capture_contour(*args, **kwargs):
        calls.append((kwargs["levels"], kwargs.get("linestyles", "solid"), kwargs["linewidths"]))

    ax.contour = capture_contour
    tool._draw(ax, np.array([[100.0, 0.0], [0.0, -100.0]]),
               np.array([7.0, 8.0]), np.array([110.0, 111.0]), "signed")

    assert len(calls) == 2
    positive_levels, positive_style, positive_widths = calls[0]
    negative_levels, negative_style, negative_widths = calls[1]
    np.testing.assert_allclose(positive_levels, np.array(tool.LEVELS) * 100.0)
    np.testing.assert_allclose(np.sort(np.abs(negative_levels)), np.array(tool.LEVELS) * 100.0)
    assert positive_style == "solid"
    assert negative_style == "dashed"
    np.testing.assert_allclose(positive_widths, [0.25, 0.25, 0.4, 0.4, 0.4, 0.4, 0.4])
    np.testing.assert_allclose(negative_widths, positive_widths[::-1])
    plt.close(figure)


def test_peak_marker_default_is_small():
    import inspect

    tool = _load_tool()
    assert inspect.signature(tool._draw).parameters["peak_marker_size"].default == 4


@pytest.mark.parametrize("area", [2, 4, 16])
def test_peak_marker_size_only_changes_dot_area(area):
    import matplotlib.pyplot as plt

    tool = _load_tool()
    figure, ax = plt.subplots()
    tool._draw(
        ax, np.eye(3), np.arange(3.), np.arange(3.), "small peaks",
        peak_points=np.array([[1., 1.], [2., 2.]]), peak_signs=np.array([1, -1]),
        peak_matched=np.array([True, True]), peak_marker_size=area,
    )
    dots_collections = [artist for artist in ax.collections
                        if hasattr(artist, "get_sizes") and len(artist.get_offsets()) == 2]
    assert len(dots_collections) == 1
    dots = dots_collections[0]
    np.testing.assert_allclose(dots.get_sizes(), [area])
    np.testing.assert_allclose(dots.get_linewidths(), [0])
    np.testing.assert_array_equal(dots.get_facecolors(), np.array([[214, 39, 40, 255]]) / 255)
    plt.close(figure)


def test_custom_cli_tolerance_changes_matched_marker_mask(tmp_path, monkeypatch):
    import scripts.vm_projection_report as report_module

    tool = _load_tool()
    auto_path, ref_path = tmp_path / "auto.ft2", tmp_path / "reference.ft2"
    auto_path.touch()
    ref_path.touch()
    ppm = [np.linspace(7.0, 9.0, 9), np.linspace(110.0, 118.0, 9)]
    data = np.zeros((9, 9), dtype=float)
    data[4, 4] = 10.0
    spectrum = {"data": data, "ppm": ppm, "nuclei": ["H", "N"]}
    monkeypatch.setattr(tool, "_load", lambda path: spectrum)

    def fake_detect(path, plane, x_ppm, y_ppm, pair, sigma, fraction, *, sign_mode):
        x = 8.0 if Path(path) == auto_path else 8.015
        y = 114.0 if Path(path) == auto_path else 114.1
        return ([{"intensity": 10.0}], np.array([[x, y]]), np.array([1]),
                {"count": 1, "sign_mode": sign_mode})

    monkeypatch.setattr(report_module, "_detect_plane", fake_detect)
    panels = []
    original_draw = tool._draw

    def capture_draw(ax, *args, **kwargs):
        mask = kwargs["peak_matched"]
        scatter_colors = []
        original_scatter = ax.scatter

        def capture_scatter(*scatter_args, **scatter_kwargs):
            scatter_colors.append((
                scatter_kwargs.get("color"), len(np.asarray(scatter_args[0]))
            ))
            return original_scatter(*scatter_args, **scatter_kwargs)

        ax.scatter = capture_scatter
        original_draw(ax, *args, **kwargs)
        del ax.scatter
        panels.append((args[3], None if mask is None else np.asarray(mask).copy(),
                       scatter_colors))

    monkeypatch.setattr(tool, "_draw", capture_draw)
    default_out = tmp_path / "default-tolerance.png"
    assert tool.main([
        "--case", f"smoke,{auto_path},{ref_path}", "--out", str(default_out), "--mark-peaks",
    ]) == 0
    assert panels[0][1] is None
    assert "unmatched" not in panels[0][0].lower()
    assert not any(color == "#b12cbd" and count for color, count in panels[0][2])
    np.testing.assert_array_equal(panels[1][1], [True])
    assert "0 unmatched" in panels[1][0].lower()
    assert "reference coverage 1/1" in panels[1][0].lower()

    panels.clear()
    tight_out = tmp_path / "tight-tolerance.png"
    assert tool.main([
        "--case", f"smoke,{auto_path},{ref_path}", "--out", str(tight_out), "--mark-peaks",
        "--tol-h", "0.01",
    ]) == 0
    assert panels[0][1] is None
    assert "unmatched" not in panels[0][0].lower()
    assert not any(color == "#b12cbd" and count for color, count in panels[0][2])
    np.testing.assert_array_equal(panels[1][1], [False])
    assert "1 unmatched" in panels[1][0].lower()
    assert "reference coverage 0/1" in panels[1][0].lower()
    assert any(color == "#b12cbd" and count for color, count in panels[1][2])


@pytest.mark.parametrize(
    "option, value, message",
    [
        ("--peak-height-fraction", "0", "peak height fraction must be finite"),
        ("--peak-height-fraction", "1.1", "peak height fraction must be finite"),
        ("--peak-height-fraction", "nan", "peak height fraction must be finite"),
        ("--peak-marker-size", "0", "peak marker size must be positive and finite"),
        ("--peak-marker-size", "inf", "peak marker size must be positive and finite"),
    ],
)
def test_main_rejects_invalid_peak_marking_parameters(
    tmp_path, capsys, option, value, message,
):
    with pytest.raises(SystemExit):
        _load_tool().main([
            "--case", "unused,a,b", "--out", str(tmp_path / "figure.png"),
            f"{option}={value}",
        ])
    assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    "option, value",
    [("--tol-h", "0"), ("--tol-n", "inf"), ("--tol-c", "nan")],
)
def test_main_rejects_nonpositive_or_nonfinite_peak_match_tolerances(
    tmp_path, capsys, option, value,
):
    with pytest.raises(SystemExit):
        _load_tool().main([
            "--case", "unused,a,b", "--out", str(tmp_path / "figure.png"),
            f"{option}={value}",
        ])
    assert "tolerances must be positive and finite" in capsys.readouterr().err


def test_projection_all_smoke_creates_three_rows_and_two_columns(
    tmp_path, monkeypatch,
):
    import matplotlib.pyplot as plt

    tool = _load_tool()
    auto_path = tmp_path / "auto.ft3"
    ref_path = tmp_path / "reference.ft3"
    auto_path.touch()
    ref_path.touch()
    axes = [
        np.linspace(7.0, 9.0, 9),
        np.linspace(110.0, 118.0, 9),
        np.linspace(40.0, 48.0, 9),
    ]
    data = np.zeros((9, 9, 9), dtype=float)
    data[4, 4, 4] = -10.0
    spectrum = {"data": data, "ppm": axes, "nuclei": ["H", "N", "C"]}
    monkeypatch.setattr(tool, "_load", lambda path: spectrum)
    original_subplots = plt.subplots
    layouts = []

    def capture_layout(nrows, ncols, *args, **kwargs):
        layouts.append((nrows, ncols))
        return original_subplots(nrows, ncols, *args, **kwargs)

    monkeypatch.setattr(plt, "subplots", capture_layout)
    output = tmp_path / "nested" / "all-projections.png"
    assert tool.main([
        "--case", f"smoke,{auto_path},{ref_path}", "--out", str(output),
        "--projection", "all",
    ]) == 0
    assert layouts == [(3, 2)]
    assert output.is_file() and output.stat().st_size > 0
