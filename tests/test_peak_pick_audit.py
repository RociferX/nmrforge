"""Regression vectors for scientific peak-picking contracts, not real-spectrum validation."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from nmrforge_api.errors import MeasurementError
from nmrforge_api.peaks import detect_and_localize, measure_peak_positions, peak_coordinates
from workflow.peak_align import filter_by_reference, row_coords
from workflow.pick_peaks import SpectrumAxes, _peak_nucleus_ppm


def _axes(ndim=2):
    shape = (32,) * ndim
    data = np.random.default_rng(20261002).uniform(-0.01, 0.01, shape)
    data[(5,) * ndim] = 100
    data[(12,) * ndim] = 90
    data[(22,) * ndim] = -80
    return SpectrumAxes(
        {},
        data,
        [np.linspace(130, 100, 32)] * ndim,
        ["15N", "1H"] if ndim == 2 else ["15N", "13C", "1H"],
        list(range(ndim)),
    )


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("positive", [1, 1]),
        ("negative", [-1]),
        ("both", [1, 1, -1]),
        ("dominant", [1, 1]),
    ],
)
def test_api_sign_override_is_respected(mode, expected):
    rows, meta = detect_and_localize(
        Path(__file__),
        axes=_axes(),
        sign_mode=mode,
        edge_margin_points=0,
    )
    assert [int(np.sign(row["intensity"])) for row in rows] == expected
    assert meta["sign_mode"] == mode


@pytest.mark.parametrize(
    "name,ndim", [("HNCACB", 3), ("CCNH", 3), ("COSY", 2), ("NOESY", 2), ("ROESY", 2)]
)
def test_api_auto_preserves_mixed_experiment_signs(name, ndim):
    experiment = SimpleNamespace(experiment_type=SimpleNamespace(name=name, confidence=1.0))
    rows, meta = detect_and_localize(
        Path(__file__),
        axes=_axes(ndim),
        experiment=experiment,
        edge_margin_points=0,
    )
    assert len(rows) == 3
    assert meta["sign_mode"] == "both"


def test_api_rejects_unknown_sign_and_nonfinite_input():
    axes = _axes()
    with pytest.raises(MeasurementError):
        detect_and_localize(Path(__file__), axes=axes, sign_mode="invalid", edge_margin_points=0)
    axes.data[0, 0] = np.nan
    with pytest.raises(ValueError):
        detect_and_localize(Path(__file__), axes=axes, edge_margin_points=0)


@pytest.mark.parametrize("offset", [100.0, -100.0])
def test_reference_measurement_uses_same_baseline_as_detection(offset):
    axes = _axes()
    detected, _ = detect_and_localize(
        Path(__file__), axes=axes, sign_mode="both", edge_margin_points=0
    )
    rows = [
        {"N_shift": row["N_ppm"], "H_shift": row["H_ppm"], "Intensity": row["intensity"]}
        for row in detected
    ]
    original = measure_peak_positions(Path(__file__), rows, axes=axes, window_pts=2)
    axes.data[...] += offset
    shifted = measure_peak_positions(Path(__file__), rows, axes=axes, window_pts=2)
    for before, after in zip(original, shifted):
        assert after.positions == pytest.approx(before.positions)
        assert after.intensity == pytest.approx(before.intensity)
        assert after.snr == pytest.approx(before.snr)
        assert after.intensity_ratio == pytest.approx(1.0)


def test_repeated_nuclei_keep_distinct_coordinates_and_matching():
    nuclei = ["13C", "13C", "1H"]
    row = {"F1_shift": 40.0, "F2_shift": 60.0, "F3_shift": 8.0}
    coords = row_coords(row, nuclei)
    assert coords == {"13C:F1": 40.0, "13C:F2": 60.0, "1H": 8.0}
    swapped = {"F1_shift": 60.0, "F2_shift": 40.0, "F3_shift": 8.0}
    kept, _ = filter_by_reference([row, swapped], [row], {}, nuclei, nuclei)
    assert kept == [row]
    axes = SpectrumAxes(
        {},
        np.zeros((8, 8, 8)),
        [np.arange(8)] * 3,
        nuclei,
        [1, 0, 2],
    )
    assert axes.storage_of("13C") is None
    assert axes.storage_of("13C:F1") == 1
    assert peak_coordinates(row, axes) == coords
    from core.qc.peak_detection import Peak

    actual = _peak_nucleus_ppm(Peak(position=(2, 3, 4)), axes.ppm, nuclei, [1, 0, 2])
    assert actual == {"13C:F1": 3.0, "13C:F2": 2.0, "1H": 4.0}


def test_homonuclear_2d_reference_uses_both_axes():
    reference = {"N_shift": 7.0, "H_shift": 8.0}
    unrelated = {"N_shift": 9.0, "H_shift": 8.0}
    nuclei = ["1H", "1H"]
    assert row_coords(reference, nuclei) == {"1H:F1": 7.0, "1H:F2": 8.0}
    kept, _ = filter_by_reference([reference, unrelated], [reference], {}, nuclei, nuclei)
    assert kept == [reference]


@pytest.mark.parametrize("lazy", [False, True])
def test_viewer_repeated_axes_match_peak_output(monkeypatch, lazy):
    import nmrglue as ng

    from viewer.spectrum import Spectrum3D

    data = np.arange(3 * 4 * 5).reshape(3, 4, 5)
    dic = {"FDDIMCOUNT": 3, "FDDIMORDER": [3, 1, 2], "FDPIPEFLAG": 1}
    for logical, label, orig in [(1, "C", 100), (2, "C", 200), (3, "H", 5)]:
        prefix = f"FDF{logical}"
        dic.update(
            {
                prefix + "LABEL": label,
                prefix + "SW": 80,
                prefix + "OBS": 1,
                prefix + "CAR": 0,
                prefix + "ORIG": orig,
            }
        )
    monkeypatch.setattr(ng.pipe, "read", lambda *_args: (dic, data))
    monkeypatch.setattr(ng.pipe, "read_lowmem", lambda *_args: (dic, data))
    spec = Spectrum3D.load_from_ft3(Path(__file__), lazy=lazy)
    assert spec.axes[0].orig_hz == 100
    assert spec.axes[1].orig_hz == 200
    if lazy:
        assert spec._lazy_inv == (1, 0, 2)
    else:
        np.testing.assert_array_equal(spec.data, data.transpose(1, 0, 2))


def test_viewer_zero_origin_is_present_not_missing():
    from viewer.spectrum import SpectrumAxis

    axis = SpectrumAxis("H", 32, 500, 500, 0.96875, orig_hz=0.0)
    assert axis.ppm[-1] == 0
    assert axis.ppm[0] == pytest.approx(0.96875)
