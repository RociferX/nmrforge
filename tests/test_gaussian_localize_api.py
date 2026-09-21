"""API side regression of the peak positioning method: measure refine='gaussian', non-2D rejection,
config default. These use cases reuse ``tests/test_nmrforge_api.py`` The synthetic spectrum/peak
table/Fake backend construct (the same ppm caliber), verifying that the downstream research
interface can select the positioning algorithm through API -- that is, the entrance to "compare
the peak position difference brought by the two algorithms"."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_nmrforge_api import (  # noqa: E402
    _FakeSweepBackend,
    _h1_step,
    _n15_step,
    _write_ft2,
    _write_peak_table,
)

import nmrforge_api.peaks as api_peaks  # noqa: E402
import workflow.pick_peaks as wf  # noqa: E402
from core.peaks import localize as lz  # noqa: E402
from core.peaks.peak_table import load_peaks  # noqa: E402
from nmrforge_api import (  # noqa: E402
    add_dataset,
    build_reference,
    measure_peak_positions,
    open_study,
)
from nmrforge_api.errors import MeasurementError  # noqa: E402
from nmrforge_api.peaks import pick_reference_peaks  # noqa: E402


def test_localization_defaults_come_from_config() -> None:
    """ROI is not hard-coded into the core function: config ``peaks.localization`` can be
    overridden."""
    defaults = lz.load_localization_defaults()
    assert defaults["method"] == "parabolic"
    assert defaults["gaussian_roi_f1_ppm"] > 0
    assert defaults["gaussian_roi_f2_ppm"] > 0
    custom = lz.load_localization_defaults(
        {
            "peaks": {
                "localization": {
                    "method": "gaussian",
                    "gaussian_roi_f1_ppm": 0.8,
                    "gaussian_roi_f2_ppm": 0.1,
                }
            }
        }
    )
    assert custom["method"] == "gaussian"
    assert custom["gaussian_roi_f1_ppm"] == pytest.approx(0.8)
    assert custom["gaussian_roi_f2_ppm"] == pytest.approx(0.1)
    # If an illegal value returns to the default value, no exception will be thrown.
    broken = lz.load_localization_defaults(
        {"peaks": {"localization": {"method": "nope", "gaussian_roi_f1_ppm": -3}}}
    )
    assert broken["method"] == "parabolic"
    assert broken["gaussian_roi_f1_ppm"] == pytest.approx(
        lz.DEFAULT_GAUSSIAN_ROI_F1_PPM
    )


def test_measure_peak_positions_gaussian_refine(tmp_path: Path) -> None:
    """Refine='gaussian':The same sheet of music/Same batch peak conversion method,Peak-by-peak
    retention method/QC."""
    rows = load_peaks(_write_peak_table(tmp_path / "ref.list"))
    spectrum = _write_ft2(tmp_path / "shift.ft2", shift_y=1.25, shift_x=0.625)
    parabolic = measure_peak_positions(
        spectrum, rows, window_ppm=1.0, refine="parabolic"
    )
    gaussian = measure_peak_positions(
        spectrum,
        rows,
        window_ppm=1.0,
        refine="gaussian",
        roi_f1_ppm=1.0,
        roi_f2_ppm=0.2,
    )
    assert len(gaussian) == len(parabolic) == 2
    for fine, coarse in zip(gaussian, parabolic):
        record = fine.localization
        assert record["requested_method"] == "gaussian"
        assert record["actual_method"] in ("gaussian", "parabolic")
        assert record["fit_success"] is (record["actual_method"] == "gaussian")
        if record["actual_method"] == "gaussian":
            assert record["fwhm_f1"] > 0 and record["fwhm_f2"] > 0
            assert record["amplitude"] > 0
        # Same candidate: the two methods will not jump to other peaks.
        assert abs(fine.positions["15N"] - coarse.positions["15N"]) < 0.5 * _n15_step()
        assert abs(fine.positions["1H"] - coarse.positions["1H"]) < 0.5 * _h1_step()
    # At least one peak really went through Gaussian fitting (otherwise this use case did not cover
    # the fitting path).
    assert (
        sum(1 for m in gaussian if m.localization["actual_method"] == "gaussian") >= 1
    )


class _Fake3D:
    """Minimal 3D spectral double: only provides properties where read_spectrum_axes will be
    used."""

    data = np.zeros((4, 4, 4))
    ppm = [np.linspace(0.0, 1.0, 4)] * 3
    nuclei = ["15N", "13C", "1H"]
    logical_to_storage = [0, 1, 2]
    obs = [60.8, 151.0, 600.0]

    @property
    def ndim(self) -> int:
        return 3


def test_measure_peak_positions_gaussian_rejects_non_2d(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refine='gaussian' only supports 2D: non-2D spectra must be explicitly rejected to not
    silently run the wrong algorithm."""
    spectrum = _write_ft2(tmp_path / "ref.ft2")
    rows = [{"N_shift": 119.0, "H_shift": 5.0, "label": "G1"}]
    monkeypatch.setattr(api_peaks, "read_spectrum_axes", lambda path: _Fake3D())
    with pytest.raises(MeasurementError) as exc:
        measure_peak_positions(spectrum, rows, refine="gaussian")
    assert "only for 2D spectra" in str(exc.value)
    # Unknown refine still reports an error explicitly (it will not run silently by default).
    with pytest.raises(MeasurementError):
        measure_peak_positions(spectrum, rows, refine="lorentzian")


def test_pick_reference_peaks_passes_localization_method(
    tmp_path: Path,
    bruker_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gaussian positioning is optional for the reference peak table: method/ROI is transparently
    transmitted to workflow.pick_peaks and saved."""
    captured: dict = {}

    def fake_pick_peaks(manager, exp_id, data_id, *args, **kwargs):
        captured.update(kwargs)
        peak_path = tmp_path / "fake.list"
        peak_path.write_text(
            "Assignment w1 w2 Data Height Volume\nG1  119.000  8.000  0  100  0\n",
            encoding="utf-8",
        )
        return {
            "status": "success",
            "peak_path": str(peak_path),
            "peak_count": 1,
            "detection": {"edge_margin_points": 1},
            "localization": {"peak_localization_method": "gaussian"},
        }

    monkeypatch.setattr(wf, "pick_peaks", fake_pick_peaks)
    session = open_study(tmp_path / "study", name="gauss", backend=_FakeSweepBackend())
    add_dataset(session, bruker_dir / "hsqc_2d")
    build_reference(session)
    details: dict = {}
    peak_path = pick_reference_peaks(
        session,
        out_path=tmp_path / "reference.list",
        details=details,
        localization_method="gaussian",
        gaussian_roi_f1_ppm=1.2,
        gaussian_roi_f2_ppm=0.3,
    )
    assert captured["localization_method"] == "gaussian"
    assert captured["gaussian_roi_f1_ppm"] == pytest.approx(1.2)
    assert captured["gaussian_roi_f2_ppm"] == pytest.approx(0.3)
    assert details["localization"]["peak_localization_method"] == "gaussian"
    assert Path(peak_path).is_file()
