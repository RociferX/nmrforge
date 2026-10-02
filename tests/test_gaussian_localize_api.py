"""API-side regression for the peak localization method: measure refine='gaussian', non-2D
rejection, config default.

These cases reuse the synthetic spectrum/peak table/fake backend fixtures from
``tests/test_nmrforge_api.py`` (same ppm convention) to verify that the downstream
research interface can select the localization algorithm through the API -- that is, the
entry point for "comparing the peak-position difference the two algorithms bring".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_nmrforge_api import (  # noqa: E402
    _FakeSweepBackend,
    _write_ft2,
    _write_peak_table,
)

import nmrforge_api  # noqa: E402
import nmrforge_api.peaks as api_peaks  # noqa: E402
import workflow.pick_peaks as wf  # noqa: E402
from core.peaks import localize as lz  # noqa: E402
from core.peaks.peak_table import load_peaks  # noqa: E402
from nmrforge_api import (  # noqa: E402
    PEAK_TABLE_COLUMNS,
    add_dataset,
    measure_peak_positions,
    open_study,
)
from nmrforge_api.compat import default_snapshot  # noqa: E402
from nmrforge_api.errors import MeasurementError, SweepError  # noqa: E402
from nmrforge_api.peaks import detect_and_localize  # noqa: E402
from nmrforge_api.reference import REFERENCE_TABLE_FILENAMES  # noqa: E402
from ui_support import i18n  # noqa: E402


@pytest.fixture(autouse=True)
def _english_messages():
    """Regression coverage:  english messages."""
    saved = i18n._language
    i18n.set_language("en")
    yield
    i18n._language = saved
    i18n.reset_cache()


def test_measure_peak_positions_rejects_gaussian_refine(tmp_path: Path) -> None:
    """Regression coverage: test measure peak positions rejects gaussian refine."""
    rows = load_peaks(_write_peak_table(tmp_path / "ref.list"))
    spectrum = _write_ft2(tmp_path / "shift.ft2", shift_y=1.25, shift_x=0.625)
    for refine in ("parabolic", "none"):
        measured = measure_peak_positions(spectrum, rows, window_ppm=1.0, refine=refine)
        assert len(measured) == 2
    with pytest.raises(MeasurementError) as exc:
        measure_peak_positions(spectrum, rows, window_ppm=1.0, refine="gaussian")
    assert "removed" in str(exc.value)

    with pytest.raises(MeasurementError):
        measure_peak_positions(spectrum, rows, refine="lorentzian")


def test_parabolic_measurement_records_qc(tmp_path: Path) -> None:
    """Regression coverage: test parabolic measurement records qc."""
    rows = load_peaks(_write_peak_table(tmp_path / "ref.list"))
    spectrum = _write_ft2(tmp_path / "ref.ft2")
    measured = measure_peak_positions(spectrum, rows, window_ppm=1.0)
    assert measured
    for item in measured:
        record = item.localization
        assert record["requested_method"] == "parabolic"
        assert record["actual_method"] == "parabolic"
        assert "fit_rmse" not in record


def test_detect_and_localize_has_no_method_choice(tmp_path: Path) -> None:
    """Regression coverage: test detect and localize has no method choice."""
    spectrum = _write_ft2(tmp_path / "ref.ft2")
    rows, meta = detect_and_localize(spectrum, sigma_multiplier=20.0, edge_margin_ppm=0.5)
    assert rows
    assert meta["localization_method"] == "parabolic"
    assert meta["n_fallback"] == 0
    assert all(row["localization_method"] == "parabolic" for row in rows)
    assert all("localization_requested" not in row for row in rows)
    assert all("fit_rmse" not in row for row in rows)
    with pytest.raises(TypeError):
        detect_and_localize(spectrum, method="parabolic")


def test_api_no_longer_exposes_gaussian_surface() -> None:
    """Regression coverage: test api no longer exposes gaussian surface."""
    assert not hasattr(nmrforge_api, "GAUSSIAN_ONLY_COLUMNS")
    assert not hasattr(nmrforge_api, "gaussian_fallback_rows")
    assert not hasattr(api_peaks, "GAUSSIAN_UNSUPPORTED_MESSAGE")
    assert not hasattr(lz, "GAUSSIAN_SUPPORTED_NDIM")
    assert not hasattr(lz, "DEFAULT_GAUSSIAN_MAX_NFEV")
    assert not hasattr(lz, "localize_peak_gaussian_2d")
    assert REFERENCE_TABLE_FILENAMES == {"parabolic": "reference_peak_table_parabolic.csv"}
    assert "gaussian" not in PEAK_TABLE_COLUMNS

    for column in ("fit_rmse", "localization_requested"):
        assert column not in PEAK_TABLE_COLUMNS
    for column in ("fit_success", "FWHM_H", "FWHM_N", "boundary_hit"):
        assert column in PEAK_TABLE_COLUMNS


def test_compat_defaults_have_no_gaussian_roi() -> None:
    """Regression coverage: test compat defaults have no gaussian roi."""
    defaults = default_snapshot()
    assert defaults["localization"]["default_method"] == "parabolic"
    assert "gaussian_roi" not in defaults
    assert not any("gaussian" in key for key in defaults["localization"])


def test_reference_build_has_no_localization_choice(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression coverage: test reference build has no localization choice."""
    captured: dict = {}

    def fake_pick_peaks(manager, exp_id, data_id, **kwargs):
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
            "localization": {"peak_localization_method": "parabolic"},
        }

    monkeypatch.setattr(wf, "pick_peaks", fake_pick_peaks)
    session = open_study(tmp_path / "study", name="p", backend=_FakeSweepBackend())
    add_dataset(session, bruker_dir / "hsqc_2d")
    details: dict = {}
    peak_path = api_peaks.pick_reference_peaks(
        session,
        out_path=tmp_path / "reference.list",
        details=details,
        sigma_multiplier=25.0,
    )
    assert Path(peak_path).is_file()

    assert set(captured) == {"sigma_multiplier"}
    assert details["localization"]["peak_localization_method"] == "parabolic"
    for removed in ("localization_method", "gaussian_roi_f1_ppm", "gaussian_roi_f2_ppm"):
        with pytest.raises(TypeError):
            api_peaks.pick_reference_peaks(session, **{removed: 1.0})


def test_localization_choice_is_rejected_at_api_level(tmp_path: Path) -> None:
    """Regression coverage: test localization choice is rejected at api level."""
    from nmrforge_api.sweep import _localization_methods

    assert _localization_methods("parabolic") == ["parabolic"]
    assert _localization_methods(None) == []
    for value in ("gaussian", "both", "gauss", "all"):
        with pytest.raises(SweepError) as exc:
            _localization_methods(value)
        assert "Gaussian" in str(exc.value) or "parabolic" in str(exc.value)
    with pytest.raises(SweepError):
        _localization_methods("lorentzian")


def test_per_method_target_keys_are_rejected() -> None:
    """Regression coverage: test per method target keys are rejected."""
    from nmrforge_api.localization_targets import split_target_specs
    from nmrforge_api.sweep import split_combo

    for spec in ({"gaussian": "g.csv"}, {"both": "b.csv"}):
        with pytest.raises(SweepError) as exc:
            split_target_specs(spec)
        assert "removed" in str(exc.value)
    with pytest.raises(SweepError) as exc:
        split_combo({"localization.targets.gaussian": "g.csv"})
    assert "per-method" in str(exc.value)

    _, _, detection = split_combo({"localization.targets": "t.csv"})
    assert detection["targets"] == "t.csv"
