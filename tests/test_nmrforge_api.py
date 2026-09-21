"""Nmrforge_api (v1.0 - first version; specification finalised 2026-09-13) regression.

Coverage: reference workflow (1 script + 2 peak table), workflow_id, three-layer parameter
retention, combined mode according to
external selection to output positioning peak table, status three values, complete log and
version, multi-condition independent reference base, automatic parameter actual value, software
boundary (do not do CSP/statistics), CLI And "not dependent on Qt". Specification compliance
ledger: ``docs/reviews/2026-09-13-api-spec-compliance.md``."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from core.peaks.peak_table import export_peaks_poky
from core.version import software_commit, software_version
from nmrforge_api import (
    API_VERSION,
    PEAK_TABLE_COLUMNS,
    STATUS_SUCCESS,
    STATUS_WARNING,
    DatasetError,
    DatasetRef,
    MeasurementError,
    ReferenceError,
    SweepError,
    SweepPlan,
    add_dataset,
    build_reference,
    condition_token,
    detect_and_localize,
    ensure_reference_peaks,
    expand_grid,
    load_combo_table,
    load_plan,
    load_runs,
    load_workflows,
    mark_duplicate_localization,
    measure_peak_positions,
    merge_overrides,
    open_study,
    plan_sweep,
    read_peak_table,
    rebuild_reference_peak_tables,
    run_combination_study,
    run_parameter_study,
    run_reference_study,
    run_sweep,
    window_points_by_axis,
)
from nmrforge_api.cli import main as cli_main
from nmrforge_api.peaks import (
    PeakMeasurement,
    peak_coordinates,
    read_reference_peaks,
    reference_peak_id,
)
from nmrforge_api.records import refresh_reference_records
from nmrforge_api.reference import (
    ReferenceSpectrum,
    load_reference,
    reference_phase,
    sanitize_sweep_params,
)

# Synthetic spectral geometry: data axis 0 = indirect (15N, 64 points), axis 1 = direct (1H, 128
# points); head with CAR(ORIG=0),ppm[i] = CAR + (size/2 - i) * SW/(size*OBS).
_N15_OBS, _N15_SW, _N15_CAR, _N15_SIZE = 60.8, 2000.0, 118.0, 64
_H1_OBS, _H1_SW, _H1_CAR, _H1_SIZE = 600.0, 6000.0, 4.7, 128
_PEAK_A = (30, 60)
_PEAK_B = (45, 90)


def _n15_step() -> float:
    return _N15_SW / (_N15_SIZE * _N15_OBS)


def _h1_step() -> float:
    return _H1_SW / (_H1_SIZE * _H1_OBS)


def _n15_ppm(index: float) -> float:
    return _N15_CAR + (_N15_SIZE / 2 - index) * _n15_step()


def _h1_ppm(index: float) -> float:
    return _H1_CAR + (_H1_SIZE / 2 - index) * _h1_step()


def _write_ft2(path: Path, *, shift_y: float = 0.0, shift_x: float = 0.0) -> Path:
    """Write a 2D spectrum that can be read by nmrglue, with peak positions shifted (points) by
    shift_y/shift_x."""
    return _write_ft2_grid(path, 1, shift_y=shift_y, shift_x=shift_x)


def _write_ft2_grid(
    path: Path, factor: int, *, shift_y: float = 0.0, shift_x: float = 0.0
) -> Path:
    """``factor`` times grid version of the same spectrum (zero filling: point distance 1/factor,
    physical peak position remains unchanged). ``shift_y``/``shift_x`` is calculated as **1."""
    from nmrglue.fileio import pipe

    factor = max(1, int(factor))
    shape = (_N15_SIZE * factor, _H1_SIZE * factor)
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    arr = np.zeros(shape, dtype=float)
    for index, (y, x) in enumerate((_PEAK_A, _PEAK_B), start=1):
        cy, cx = (y + shift_y) * factor, (x + shift_x) * factor
        arr += (120.0 - 20.0 * (index - 1)) * np.exp(
            -(
                ((yy - cy) ** 2) / (2 * (1.2 * factor) ** 2)
                + ((xx - cx) ** 2) / (2 * (1.4 * factor) ** 2)
            )
        )
    rng = np.random.default_rng(20260912)
    arr = gaussian_filter(arr, sigma=0.5) + rng.normal(0.0, 0.5, shape)
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = shape[1]
    dic["FDSPECNUM"] = shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDDIMORDER"] = [2, 1]
    dic["FDF1LABEL"] = "N15"
    dic["FDF2LABEL"] = "H1"
    dic["FDF1SW"] = str(_N15_SW)
    dic["FDF1OBS"] = str(_N15_OBS)
    dic["FDF1CAR"] = str(_N15_CAR)
    dic["FDF1ORIG"] = "0"
    dic["FDF2SW"] = str(_H1_SW)
    dic["FDF2OBS"] = str(_H1_OBS)
    dic["FDF2CAR"] = str(_H1_CAR)
    dic["FDF2ORIG"] = "0"
    path.parent.mkdir(parents=True, exist_ok=True)
    pipe.write(str(path), dic, arr.astype(np.float32), overwrite=True)
    return path


def _write_peak_table(path: Path, sign: float = 1.0) -> Path:
    """Write the peak table for the reference spectrum (shift=0); the positions come from the
    same ppm formulas as above.

    ``sign < 0`` writes negative peaks (a negative ``Height``, as in the synthetic
    ground-truth anchors): this covers the ``|Height|`` denominator of
    ``intensity_ratio_vs_picked``.
    """
    rows = []
    for index, (y, x) in enumerate((_PEAK_A, _PEAK_B), start=1):
        rows.append(
            {
                "N_shift": _n15_ppm(y),
                "H_shift": _h1_ppm(x),
                "Intensity": sign * (120.0 - 20.0 * (index - 1)),
                "label": f"G{index}",
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    export_peaks_poky(path, rows)
    return path


class _FakeSweepBackend:
    """Simulate NMRPipe backend: press ``window.F1.off`` to linearly shift the peak position and
    write the real ft2."""

    def __init__(self) -> None:
        self.work_dir = ""
        self.process_calls: list[dict] = []
        self.reconstruct_calls: list[dict] = []
        self.convert_calls = 0

    def _work(self) -> Path:
        path = Path(self.work_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def convert_to_fid(self, experiment, data_dir, progress=None, **_kwargs) -> dict:
        self.convert_calls += 1
        work = self._work()
        (work / "fid.com").write_text("#!/bin/csh\n", encoding="utf-8")
        fid = work / f"{experiment.dataset_id}.fid"
        fid.write_bytes(b"fid-bytes")
        return {
            "success": True,
            "fid_path": str(fid),
            "message": "ok",
            "logs": [],
            "effective_params": {},
        }

    def reconstruct_nus(
        self,
        experiment,
        params=None,
        progress=None,
        script_only=False,
        out_file=None,
        script_name=None,
    ) -> dict:
        """Simulate NUS reconstruction: candidate output goes to _intermediate, and peak position
        is shifted by nSigma."""
        params = dict(params or {})
        self.reconstruct_calls.append(
            {"params": params, "out_file": out_file, "script_name": script_name}
        )
        work = self._work()
        script = work / (script_name or f"{experiment.dataset_id}_nus.com")
        nsigma = float(params.get("nsigma", params.get("nSigma", 5.0)))
        script.write_text(f"#!/bin/csh\n# nSigma={nsigma}\n", encoding="utf-8")
        if script_only:
            return {
                "success": True,
                "message": "script only",
                "logs": [],
                "script": script.read_text(encoding="utf-8"),
                "script_path": str(script),
            }
        shift = (5.0 - nsigma) * 0.5
        target = work / (out_file or f"{experiment.dataset_id}.ft2")
        if out_file:
            target = work / "_intermediate" / out_file
        _write_ft2(target, shift_y=shift, shift_x=shift / 2.0)
        return {
            "success": True,
            "message": "ok",
            "spectrum_path": str(target),
            "logs": ["fake nus"],
            "effective_params": {
                "nSigma": nsigma,
                "thresh": float(params.get("thresh", 0.95)),
                "direct_phase": [0.0, 0.0],
                "phases": {"F1": [0.0, 0.0]},
            },
        }

    def process(
        self,
        experiment,
        plan,
        *,
        params=None,
        direct_phase_override=None,
        script_name=None,
        out_file=None,
        progress=None,
        **_kwargs,
    ) -> dict:
        params = dict(params or {})
        self.process_calls.append({"params": params, "phase": direct_phase_override})
        work = self._work()
        script = work / (script_name or f"{experiment.dataset_id}_process.com")
        off = float((params.get("window") or {}).get("F1", {}).get("off", 0.40))
        script.write_text(
            f"#!/bin/csh\n# window.F1.off={off}\n", encoding="utf-8"
        )
        # 0.40 is "reference"; deviation from 0.01 translation 0.25 points (non-integer -> must be
        # sub-pixel to restore).
        shift = (0.40 - off) * 25.0
        # Simulate zero filling (motivation of plan A): zero_fill=k -> k times grid, point distance
        # 1/k, the **physical position of the peak (ppm) remains unchanged**; the real backend also
        # only changes the digital resolution.
        try:
            factor = max(1, int(params.get("zero_fill") or 1))
        except (TypeError, ValueError):
            factor = 1
        target = work / (out_file or f"{experiment.dataset_id}.ft2")
        if out_file:
            target = work / "_intermediate" / out_file
        _write_ft2_grid(target, factor, shift_y=shift, shift_x=shift / 2.0)
        return {
            "success": True,
            "message": "ok",
            "spectrum_path": str(target),
            "logs": ["fake process"],
            "effective_params": {
                "window": params.get("window"),
                "zero_fill": params.get("zero_fill"),
                "direct_phase": {"F2": [0.0, 0.0]},
            },
        }




# ------------------------------------------------------------------ Unit layer.
def test_expand_grid_and_merge_overrides() -> None:
    combos = expand_grid({"zero_fill": [1, 2], "window.F1.off": [0.35, 0.45]})
    assert len(combos) == 4
    assert {"zero_fill": 1, "window.F1.off": 0.35} in combos
    merged = merge_overrides(
        {"window": {"F1": {"off": 0.4, "end": 0.98}}, "zero_fill": 2},
        {"window.F1.off": 0.45, "points_per_line": 4.0},
    )
    assert merged["window"]["F1"] == {"off": 0.45, "end": 0.98}
    assert merged["points_per_line"] == 4.0
    assert merged["zero_fill"] == 2
    assert expand_grid({}) == [{}]
    with pytest.raises(SweepError):
        expand_grid({"zero_fill": []})


def test_reference_peak_id_is_stable_and_prefixed() -> None:
    assert reference_peak_id(1) == "R0001"
    assert reference_peak_id(37) == "R0037"


def test_measure_peak_positions_recovers_subpoint_shift(tmp_path: Path) -> None:
    from core.peaks.peak_table import load_peaks

    peaks = _write_peak_table(tmp_path / "ref.list")
    rows = load_peaks(peaks)
    # After translation +1.25 point (15N)/ +0.625 point (1H), the measured value should be restored
    # to within 1/5 point.
    spectrum = _write_ft2(tmp_path / "shift.ft2", shift_y=1.25, shift_x=0.625)
    measured = measure_peak_positions(spectrum, rows, window_pts=3)
    assert len(measured) == 2
    for index, (y, x) in enumerate((_PEAK_A, _PEAK_B)):
        item = measured[index]
        assert item.found
        assert item.reference_peak_id == f"R{index + 1:04d}"
        assert item.snr > 0  # SNR = |intensity| / Spectral noise σ.
        expected_n = _n15_ppm(y + 1.25)
        expected_h = _h1_ppm(x + 0.625)
        assert abs(item.positions["15N"] - expected_n) < 0.2 * _n15_step()
        assert abs(item.positions["1H"] - expected_h) < 0.2 * _h1_step()
        assert not item.window_edge
        assert not item.boundary
        assert not item.out_of_range
    # Integer truncation would be a full 1 point worse; here must be significantly better than that.
    assert abs(measured[0].positions["15N"] - _n15_ppm(_PEAK_A[0])) > 0.5 * _n15_step()


def test_physical_window_is_scale_invariant_across_zero_fill(
    tmp_path: Path,
) -> None:
    """Plan A: window/Margins are defined by physical width -> zero filling only changes the number
    of points, not the coverage ppm. The number of structural points (3 point
    neighborhood/parabola +/-1 points) remains unchanged; the number of **physical width**
    points must be converted with the point distance."""
    from core.peaks.peak_table import load_peaks
    from workflow.pick_peaks import read_spectrum_axes

    _write_ft2(tmp_path / "zf1.ft2")
    _write_ft2_grid(tmp_path / "zf4.ft2", 4)
    axes_one = read_spectrum_axes(tmp_path / "zf1.ft2")
    axes_four = read_spectrum_axes(tmp_path / "zf4.ft2")

    auto_one = window_points_by_axis(axes_one)
    auto_four = window_points_by_axis(axes_four)
    # Default physical radius = 1.5 x nuclide line width conversion of this axis ppm(15N 15 Hz /
    # 60.8 MHz).
    assert auto_one[0]["ppm"] == pytest.approx(1.5 * 15.0 / _N15_OBS, rel=0.02)
    assert auto_one[1]["ppm"] == pytest.approx(1.5 * 8.0 / _H1_OBS, rel=0.02)
    for axis in (0, 1):
        assert auto_four[axis]["ppm"] == pytest.approx(
            auto_one[axis]["ppm"], rel=0.02
        )
        assert auto_four[axis]["nucleus"] == auto_one[axis]["nucleus"]
        # The point distance becomes denser with zero filling -> the number of points is converted
        # according to the point distance.
        assert auto_four[axis]["ppm_per_point"] == pytest.approx(
            auto_one[axis]["ppm_per_point"] / 4.0, rel=0.05
        )

    # Explicit 1 ppm window: 1 x has different number of points than 4 x, same coverage width.
    win_one = window_points_by_axis(axes_one, window_ppm=1.0)
    win_four = window_points_by_axis(axes_four, window_ppm=1.0)
    for axis in (0, 1):
        assert win_one[axis]["source"] == "ppm_explicit"
        assert win_four[axis]["points"] == pytest.approx(
            4 * win_one[axis]["points"], rel=0.15
        )
        assert win_four[axis]["effective_ppm"] == pytest.approx(
            win_one[axis]["effective_ppm"], rel=0.15
        )
        assert win_one[axis]["effective_ppm"] == pytest.approx(1.0, rel=0.1)

    # Peak position: same physical window -> two resolutions give the same ppm.
    rows = load_peaks(_write_peak_table(tmp_path / "reference.list"))
    measured_one = measure_peak_positions(
        tmp_path / "zf1.ft2", rows, window_ppm=1.0
    )
    measured_four = measure_peak_positions(
        tmp_path / "zf4.ft2", rows, window_ppm=1.0
    )
    assert len(measured_one) == len(measured_four) == 2
    for thin, fine in zip(measured_one, measured_four):
        assert abs(thin.positions["15N"] - fine.positions["15N"]) < 0.2 * _n15_step()
        assert abs(thin.positions["1H"] - fine.positions["1H"]) < 0.2 * _h1_step()

    # Point caliber (escape hatch) is still "points are points" and is not converted with zero
    # filling.
    pts_one = window_points_by_axis(axes_one, window_pts=3)
    pts_four = window_points_by_axis(axes_four, window_pts=3)
    for axis in (0, 1):
        assert pts_one[axis]["points"] == pts_four[axis]["points"] == 3
        assert pts_four[axis]["effective_ppm"] == pytest.approx(
            pts_one[axis]["effective_ppm"] / 4.0, rel=0.05
        )


def test_read_reference_peaks_accepts_research_csv(tmp_path: Path) -> None:
    """The peak_id,H_ppm,N_ppm peak tables exported by downstream research projects can be used
    directly."""
    path = tmp_path / "reference_peaks.csv"
    path.write_text(
        "peak_id,H_ppm,N_ppm,height,linewidth,volume\n"
        "1:LEU10,8.211,122.733,0.0,0.0,0.0\n"
        "2:GLY101,8.266,109.496,0.0,0.0,0.0\n",
        encoding="utf-8-sig",
    )
    rows = read_reference_peaks(path)
    assert len(rows) == 2
    assert rows[0]["label"] == "LEU10"
    assert rows[0]["reference_peak_id"] == "R0001"
    assert peak_coordinates(rows[0], None) == {"1H": 8.211, "15N": 122.733}


def test_measure_peak_positions_flags_out_of_range(tmp_path: Path) -> None:
    spectrum = _write_ft2(tmp_path / "ref.ft2")
    rows = [{"N_shift": 999.0, "H_shift": 5.0, "label": "X"}]
    measured = measure_peak_positions(spectrum, rows, window_pts=2)
    assert measured[0].out_of_range


def test_reference_records_closer_than_the_window_stay_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two reference records closer than the window must not land on one point.

    Defect (fixed 2026-09-19): the shared window took the global |intensity| maximum, so a
    weak reference peak was swallowed by its stronger neighbour and the reference tables
    contained rows differing only in reference_peak_id (a real 2D HSQC data set, condition A:
    253 rows, only 184 unique coordinates). Every reference peak now gets its own cell,
    bounded by the midpoints to its neighbours.
    """
    from core.peaks.peak_table import load_peaks

    monkeypatch.setattr(sys.modules[__name__], "_PEAK_A", (30, 60))
    monkeypatch.setattr(sys.modules[__name__], "_PEAK_B", (30, 62))
    spectrum = _write_ft2(tmp_path / "close.ft2")
    rows = load_peaks(_write_peak_table(tmp_path / "reference.list"))

    measured = measure_peak_positions(spectrum, rows, window_pts=3)
    positions = [
        (item.positions["15N"], item.positions["1H"]) for item in measured
    ]
    assert len(positions) == 2
    assert len(set(positions)) == 2, positions
    # each record still lands on its own peak, not on the neighbour's stronger one
    assert abs(measured[0].positions["1H"] - _h1_ppm(60)) < 2.0 * _h1_step()
    assert abs(measured[1].positions["1H"] - _h1_ppm(62)) < 2.0 * _h1_step()

    # the previous shared-window wording can still be reproduced on request
    legacy = measure_peak_positions(
        spectrum, rows, window_pts=3, exclusive_windows=False
    )
    legacy_positions = [
        (item.positions["15N"], item.positions["1H"]) for item in legacy
    ]
    assert legacy_positions[0] == legacy_positions[1]


def test_sanitize_sweep_params_drops_runtime_keys() -> None:
    cleaned = sanitize_sweep_params(
        {
            "window": {"F1": {"off": 0.4}},
            "phase_route": "unified",
            "preview_axis": "F1",
            "diagnostics": {"x": 1},
            "fill": {"F1": 64},
            "nus": {"nthread": 2},
            "points_per_line": 4.0,
        }
    )
    assert set(cleaned) == {"window", "points_per_line"}


def test_reference_phase_uses_all_axes_when_direct_missing() -> None:
    """The unified route writes phase in phases (each axis PS), which must be inherited when
    locked."""
    effective = {"phases": {"F1": [172.5, 0.0], "F2": [27.5, 0.0]}}
    locked = reference_phase(effective)
    assert locked == {"F1": [172.5, 0.0], "F2": [27.5, 0.0]}
    ref = ReferenceSpectrum(
        dataset_key="exp_001/d_001",
        exp_id="exp_001",
        data_id="d_001",
        direct_phase=locked,
    )
    assert ref.direct_phase_override() == {
        "F1": (172.5, 0.0),
        "F2": (27.5, 0.0),
    }
    # The actual result of the automatic phase is dropped (Specification G1).
    record = ref.phase_record()
    assert record["F2"]["phase_mode"] == "auto"
    assert record["F2"]["actual_p0"] == pytest.approx(27.5)
    assert record["F2"]["actual_p1"] == pytest.approx(0.0)
    # Direct_phase merges with phases: direct dimension is subject to direct_phase.
    assert reference_phase(
        {"direct_phase": {"F2": [1.0, 2.0]}, "phases": {"F1": [3.0, 0.0]}}
    ) == {"F1": [3.0, 0.0], "F2": [1.0, 2.0]}


def test_reference_phase_handles_nus_flat_direct_phase() -> None:
    """The NUS reconstruction route records the direct dimension phase as flat [p0, p1], which
    needs to be mapped to F{ndim}."""
    effective = {"phases": {"F1": [172.5, 0.0]}, "direct_phase": [27.5, 0.0]}
    assert reference_phase(effective, ndim=2) == {
        "F1": [172.5, 0.0],
        "F2": [27.5, 0.0],
    }


def test_error_hierarchy() -> None:
    assert issubclass(DatasetError, Exception)
    assert issubclass(SweepError, Exception)


def test_api_version_is_the_first_version() -> None:
    """First version of the public API (2026-09-22): 1.0, with a single definition point."""
    import nmrforge_api
    import nmrforge_api.records as records_module
    import nmrforge_api.session as session_module

    assert API_VERSION == "1.0"
    # single definition point: the public surface re-exports the same object
    assert nmrforge_api.API_VERSION is session_module.API_VERSION
    # the record templates must use that constant instead of a literal
    source = Path(records_module.__file__).read_text(encoding="utf-8")
    assert '"api_version": API_VERSION' in source
    assert '"api_version": "' not in source


# --------------------------------------------------------------- Reference Workflow.
def test_reference_workflow_writes_script_and_two_peak_tables(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Specification B: 1 reference script + 2 reference peak tables (parabolic / gaussian)."""
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        tmp_path / "reference",
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        window_ppm=1.0,
        backend=backend,
    )
    ref = result.reference
    assert ref is not None
    assert Path(ref.script_path).is_file() and ref.script_sha256
    parabolic_path = Path(ref.peak_table_parabolic_path)
    gaussian_path = Path(ref.peak_table_gaussian_path)
    assert parabolic_path.is_file() and gaussian_path.is_file()
    header = parabolic_path.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert header == list(PEAK_TABLE_COLUMNS)
    assert gaussian_path.read_text(encoding="utf-8").splitlines()[0].split(",") == (
        list(PEAK_TABLE_COLUMNS)
    )
    rows_p = read_peak_table(parabolic_path)
    rows_g = read_peak_table(gaussian_path)
    assert [row["reference_peak_id"] for row in rows_p] == ["R0001", "R0002"]
    assert [row["reference_peak_id"] for row in rows_g] == ["R0001", "R0002"]
    assert all(row["workflow_id"] == "reference" for row in rows_p)
    assert all(row["localization_method"] == "parabolic" for row in rows_p)
    # Parabola table: since 2026-09-19 (P3-7) fit_success / FWHM_* / boundary_hit come
    # from the three-point parabola; only fit_rmse stays Gaussian-only (NaN), so the
    # structure is still consistent with the Gaussian table.
    assert rows_p[0]["fit_success"] is True
    assert rows_p[0]["FWHM_H"] > 0 and rows_p[0]["FWHM_N"] > 0
    assert math.isnan(rows_p[0]["fit_rmse"])
    assert rows_p[0]["boundary_hit"] is False
    assert rows_p[0]["fallback"] is False
    # Gaussian table: The peaks that really fit are FWHM/rmse.
    fitted = [row for row in rows_g if row["fit_success"]]
    assert fitted, rows_g
    assert fitted[0]["FWHM_H"] > 0 and fitted[0]["FWHM_N"] > 0
    assert fitted[0]["fit_rmse"] >= 0
    assert ref.peak_localization["parabolic"]["n_peaks"] == 2
    assert ref.peak_localization["gaussian"]["n_peaks"] == 2
    # Refer to the actual results of phase (automatic identification).
    assert ref.phase_record()["F2"]["phase_mode"] == "auto"


def test_reference_peak_tables_have_unique_coordinates(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: neither reference peak table repeats a coordinate (fixed 2026-09-19)."""
    monkeypatch.setattr(sys.modules[__name__], "_PEAK_A", (30, 60))
    monkeypatch.setattr(sys.modules[__name__], "_PEAK_B", (30, 62))
    result = run_parameter_study(
        tmp_path / "reference",
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        window_ppm=1.0,
        backend=_FakeSweepBackend(),
    )
    ref = result.reference
    assert ref is not None
    assert ref.peak_localization["exclusive_windows"] is True
    for path in (ref.peak_table_parabolic_path, ref.peak_table_gaussian_path):
        rows = read_peak_table(path)
        coords = [(row["H_ppm"], row["N_ppm"]) for row in rows]
        assert len(coords) == 2
        assert len(set(coords)) == 2, (path, coords)


def test_reference_peak_tables_carry_cell_qc_columns(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-3: The reference tables carry the eight new cell/identity QC columns as real
    values; ``shift_vs_picked_*`` is measured - picked in ppm."""
    from core.peaks.peak_table import load_peaks

    monkeypatch.setattr(sys.modules[__name__], "_PEAK_A", (30, 60))
    monkeypatch.setattr(sys.modules[__name__], "_PEAK_B", (30, 62))
    identity = load_peaks(_write_peak_table(tmp_path / "reference.list"))
    result = run_parameter_study(
        tmp_path / "cell_qc",
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        peaks=tmp_path / "reference.list",
        window_ppm=1.0,
        backend=_FakeSweepBackend(),
    )
    ref = result.reference
    assert ref is not None
    assert ref.peak_localization["exclusive_windows"] is True
    for method, path in (
        ("parabolic", ref.peak_table_parabolic_path),
        ("gaussian", ref.peak_table_gaussian_path),
    ):
        rows = read_peak_table(path)
        assert len(rows) == 2
        # docs / code / header share one source: the names and order are PEAK_TABLE_COLUMNS
        assert list(rows[0]) == list(PEAK_TABLE_COLUMNS)
        for row in rows:
            # cell bounds: integer grid points of the data axis, a closed interval
            for column in ("cell_low_H", "cell_high_H", "cell_low_N", "cell_high_N"):
                assert isinstance(row[column], int), (method, column, row[column])
            assert row["cell_low_H"] <= row["cell_high_H"]
            assert row["cell_low_N"] <= row["cell_high_N"]
            assert isinstance(row["cell_edge"], bool)
            # ratio = measured intensity / identity-table Height: finite and positive
            assert math.isfinite(row["intensity_ratio_vs_picked"])
            assert row["intensity_ratio_vs_picked"] > 0
            # shift = measured - picked in ppm (same axis and direction, not an absolute value)
            index = int(str(row["reference_peak_id"])[1:]) - 1
            picked = peak_coordinates(identity[index], None)
            assert row["shift_vs_picked_H"] == pytest.approx(
                row["H_ppm"] - picked["1H"], abs=1e-6
            )
            assert row["shift_vs_picked_N"] == pytest.approx(
                row["N_ppm"] - picked["15N"], abs=1e-6
            )
        # the frozen record's summary counts agree with the table (downstream need not recompute)
        summary = ref.peak_localization[method]
        assert summary["n_cell_edge"] == sum(
            1 for row in rows if row["cell_edge"] is True
        )
        ratio = summary["intensity_ratio_vs_picked"]
        ratios = sorted(row["intensity_ratio_vs_picked"] for row in rows)
        assert ratio["n"] == 2
        # two peaks -> the median is the upper order statistic, i.e. the maximum
        assert ratio["median"] == pytest.approx(ratios[1], rel=1e-6)
        assert ratio["max"] == pytest.approx(ratios[1], rel=1e-6)


def test_cell_edge_only_fires_on_the_exclusive_cell_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``cell_edge`` judges only that exclusive-cell edge and is orthogonal to the
    physical window edge (``window_edge``).

    When two reference peaks sit farther apart than the window, the neighbour's cell
    cannot cut the search interval, so ``cell_edge`` stays false and the cell bounds
    equal the window bounds that wording actually used; the historical wording
    (``exclusive_windows=False``) has no neighbour truncation at all, so it is false
    there too.
    """
    from core.peaks.peak_table import load_peaks

    spectrum = _write_ft2(tmp_path / "far.ft2")
    rows = load_peaks(_write_peak_table(tmp_path / "reference.list"))
    for exclusive in (True, False):
        measured = measure_peak_positions(
            spectrum, rows, window_pts=3, exclusive_windows=exclusive
        )
        assert [item.cell_edge for item in measured] == [False, False]
        for item in measured:
            # not truncated by a neighbour: the cell equals the +-3 point window
            assert item.cell_high["1H"] - item.cell_low["1H"] == 6
            assert item.cell_high["15N"] - item.cell_low["15N"] == 6
            # stopped on its own peak top, so the ratio is close to 1 (about 0.89
            # for the smoothed synthetic peak)
            assert item.intensity_ratio == pytest.approx(1.0, abs=0.2)

    # a neighbour closer than the window: the extremum is cut by the neighbour's cell
    # -> cell_edge true, while the physical window was never touched, so window_edge
    # stays false (the two are orthogonal; otherwise window_edge would degenerate)
    monkeypatch.setattr(sys.modules[__name__], "_PEAK_A", (30, 60))
    monkeypatch.setattr(sys.modules[__name__], "_PEAK_B", (30, 62))
    near = _write_ft2(tmp_path / "near.ft2")
    near_rows = load_peaks(_write_peak_table(tmp_path / "near.list"))
    truncated = measure_peak_positions(near, near_rows, window_pts=3)
    assert [item.cell_edge for item in truncated] == [True, True]
    assert all(item.window_edge is False for item in truncated)
    assert all(item.boundary is False for item in truncated)
    # with the two peaks merged into one blob the extremum is pulled towards the
    # neighbour, so the intensity ratio is clearly above 1 (not its own peak top)
    assert all(item.intensity_ratio > 1.1 for item in truncated)


def test_workflow_peak_tables_write_nan_in_cell_columns(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """P1-3: the combination (workflow) tables pick and localize in one step, so all
    eight columns are NaN rather than a fabricated 1.0/0."""
    result = run_parameter_study(
        tmp_path / "cell_nan",
        bruker_dir / "hsqc_2d",
        combos=[{"window.F1.off": 0.35, "zero_fill": 1}],
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        localization="both",
        backend=_FakeSweepBackend(),
    )
    cell_columns = list(PEAK_TABLE_COLUMNS[-8:])
    assert cell_columns[0] == "cell_low_H" and cell_columns[-1] == "shift_vs_picked_N"
    assert result.runs
    for run in result.runs:
        for method in ("parabolic", "gaussian"):
            rows = read_peak_table(Path(run.peak_table_path(method)))
            assert rows, (run.workflow_id, method)
            for row in rows:
                for column in cell_columns:
                    value = row[column]
                    assert value is None or (
                        isinstance(value, float) and math.isnan(value)
                    ), (method, column, value)


def _reference_record_path(root: Path) -> Path:
    """Path of the per-dataset ``reference.json`` (not the ``records/`` snapshot)."""
    for path in sorted(root.rglob("reference.json")):
        if path.parent.parent.name == "reference":
            return path
    raise AssertionError("no per-dataset reference.json found")


def _reference_record(root: Path) -> dict:
    """Read the per-dataset reference.json (not the records/ summary snapshot)."""
    return json.loads(_reference_record_path(root).read_text(encoding="utf-8"))


def test_reference_records_the_direct_range_source(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """P1-4: the reference record states where the direct-dimension range came from."""
    backend = _FakeSweepBackend()
    explicit_root = tmp_path / "explicit_range"
    run_reference_study(
        explicit_root,
        bruker_dir / "hsqc_2d",
        direct_range=(10.5, 6.5),
        backend=backend,
    )
    explicit = _reference_record(explicit_root)
    assert explicit["direct_range"]["source"] == "explicit"
    assert explicit["direct_range"]["ext_lo"] == 10.5
    assert explicit["direct_range"]["ext_hi"] == 6.5
    assert explicit["direct_range"]["requested"] == [10.5, 6.5]
    assert "warning" not in explicit["direct_range"]

    params_root = tmp_path / "params_range"
    run_reference_study(
        params_root,
        bruker_dir / "hsqc_2d",
        params={"ext_lo": "11.0", "ext_hi": "7.0"},
        backend=backend,
    )
    from_params = _reference_record(params_root)
    assert from_params["direct_range"]["source"] == "params"
    assert from_params["direct_range"]["ext_lo"] == 11.0
    assert from_params["direct_range"]["ext_hi"] == 7.0

    default_root = tmp_path / "default_range"
    run_reference_study(default_root, bruker_dir / "hsqc_2d", backend=backend)
    default = _reference_record(default_root)
    # no range given -> recorded as "default" with an explicit warning, never a guess
    assert default["direct_range"]["source"] == "default"
    assert default["direct_range"]["warning"]


def test_combination_mode_refuses_a_silent_direct_range_override(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """P1-4: an override disagreeing with the reference needs an explicit switch."""
    root = tmp_path / "range_gate"
    backend = _FakeSweepBackend()
    run_reference_study(
        root, bruker_dir / "hsqc_2d", direct_range=(10.5, 6.5), backend=backend
    )
    with pytest.raises(SweepError) as excinfo:
        run_combination_study(
            str(root),
            combos=[{"zero_fill": 1}],
            direct_range=(9.0, 7.0),
            backend=backend,
        )
    message = str(excinfo.value)
    assert "allow_ext_override" in message and "direct_range_override" in message

    # the same range as the reference passes and leaves no override warning
    matched = run_combination_study(
        str(root),
        combos=[{"zero_fill": 1}],
        direct_range=(10.5, 6.5),
        backend=backend,
    )
    assert "direct_range_override" not in {
        warning.get("code") for warning in matched.runs[0].warnings
    }

    # with the explicit switch the run proceeds and carries the warning code
    allowed = run_combination_study(
        str(root),
        combos=[{"zero_fill": 1}],
        direct_range=(9.0, 7.0),
        allow_ext_override=True,
        backend=backend,
    )
    run = allowed.runs[0]
    assert "direct_range_override" in {warning.get("code") for warning in run.warnings}
    payload = json.loads(Path(run.run_dir, "run.json").read_text(encoding="utf-8"))
    assert "direct_range_override" in {
        warning["code"] for warning in payload["warnings"]
    }
    assert payload["parameters_resolved"]["direct_range"]["ext_lo"] == "9"


def test_duplicate_localization_marker_flags_every_shared_coordinate() -> None:
    """P2-5: every row of a duplicated group is flagged; missing axes stay out."""
    rows: list[dict] = [
        {"H_ppm": 8.0, "N_ppm": 120.0},
        {"H_ppm": 8.0, "N_ppm": 120.0},
        {"H_ppm": 8.5, "N_ppm": 121.0},
        {"H_ppm": float("nan"), "N_ppm": 121.0},
    ]
    extra = mark_duplicate_localization(rows)
    assert extra == 1
    assert [row["duplicate_localization"] for row in rows] == [
        True,
        True,
        False,
        False,
    ]
    unique = [{"H_ppm": 1.0, "N_ppm": 2.0}, {"H_ppm": 3.0, "N_ppm": 4.0}]
    assert mark_duplicate_localization(unique) == 0
    assert all(row["duplicate_localization"] is False for row in unique)


def test_workflow_tables_carry_parabolic_qc_and_duplicate_flag(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """P3-7 / P2-5: combination parabolic tables carry a linewidth and the duplicate flag."""
    result = run_parameter_study(
        tmp_path / "workflow_qc",
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        window_ppm=1.0,
        localization="both",
        backend=_FakeSweepBackend(),
    )
    run = result.runs[0]
    rows_p = read_peak_table(Path(run.peak_table_path("parabolic")))
    rows_g = read_peak_table(Path(run.peak_table_path("gaussian")))
    assert len(rows_p) == len(rows_g) == 2
    for row in rows_p:
        assert row["fit_success"] is True
        assert row["FWHM_H"] > 0 and row["FWHM_N"] > 0
        assert math.isnan(row["fit_rmse"])  # a three-point parabola has no residual
        assert row["boundary_hit"] is False
        assert row["duplicate_localization"] is False
    # same wording as the Gaussian table: both estimates land in the same range
    assert 0.7 < rows_p[0]["FWHM_H"] / rows_g[0]["FWHM_H"] < 1.4
    assert 0.7 < rows_p[0]["FWHM_N"] / rows_g[0]["FWHM_N"] < 1.4


def test_reference_parabolic_table_estimates_linewidth(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """P3-7: the reference parabolic table carries a linewidth too (was all NaN)."""
    result = run_parameter_study(
        tmp_path / "reference_qc",
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        window_ppm=1.0,
        backend=_FakeSweepBackend(),
    )
    rows_p = read_peak_table(result.reference.peak_table_parabolic_path)
    rows_g = read_peak_table(result.reference.peak_table_gaussian_path)
    assert len(rows_p) == 2
    for row in rows_p:
        assert row["fit_success"] is True
        assert row["FWHM_H"] > 0 and row["FWHM_N"] > 0
        assert math.isnan(row["fit_rmse"])
        assert row["duplicate_localization"] is False
    assert 0.7 < rows_p[0]["FWHM_H"] / rows_g[0]["FWHM_H"] < 1.4
    assert 0.7 < rows_p[0]["FWHM_N"] / rows_g[0]["FWHM_N"] < 1.4


def test_intensity_ratio_handles_negative_peak_heights(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Defect 1: a negative-peak .list has a negative Height, so the ratio needs |Height|."""
    ratios: dict[float, list[float]] = {}
    for sign in (1.0, -1.0):
        peaks = _write_peak_table(
            tmp_path / f"peaks_{sign:+.0f}" / "reference.list", sign=sign
        )
        result = run_reference_study(
            tmp_path / f"ratio_{sign:+.0f}",
            bruker_dir / "hsqc_2d",
            peaks=peaks,
            params={"phase_route": "none"},
            backend=_FakeSweepBackend(),
        )
        reference = result.reference()
        rows = read_peak_table(Path(reference.peak_table_parabolic_path))
        ratios[sign] = [row["intensity_ratio_vs_picked"] for row in rows]
        summary = reference.peak_localization["parabolic"]["intensity_ratio_vs_picked"]
        assert summary["n"] == len(rows), "negative peaks must not be all NaN"
        assert math.isfinite(summary["median"])
    # Same data, only the identity-table Height negated: the ratios must be identical.
    assert all(
        math.isfinite(value) and value > 0 for value in ratios[-1.0]
    ), ratios[-1.0]
    assert ratios[-1.0] == ratios[1.0]


def test_rebuild_peak_tables_refreshes_version_and_records(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Defects 2/3: rebuilding the tables must restamp the version and refresh records."""
    root = tmp_path / "rebuild_refresh"
    run_reference_study(
        root,
        bruker_dir / "hsqc_2d",
        params={"phase_route": "none"},
        backend=_FakeSweepBackend(),
    )
    aggregate = root / "study" / "records" / "reference.json"
    assert aggregate.is_file()

    # Fake a pre-P0-1 study root: old version, stale commit, dead sha in the aggregate
    condition_file = _reference_record_path(root)
    stale = json.loads(condition_file.read_text(encoding="utf-8"))
    stale["software_version"] = "0.9.0"
    stale["software_commit"] = "deadbeef"
    condition_file.write_text(json.dumps(stale), encoding="utf-8", newline="\n")
    aggregate_record = json.loads(aggregate.read_text(encoding="utf-8"))
    aggregate_record["nmrforge_version"] = "0.9.0"
    aggregate_record["references"][0]["peak_tables"]["parabolic"]["sha256"] = "deadbeef"
    aggregate_record["references"][0].pop("peak_localization", None)
    aggregate.write_text(
        json.dumps(aggregate_record), encoding="utf-8", newline="\n"
    )

    session = open_study(root)
    rebuild_reference_peak_tables(session)

    refreshed = _reference_record(root)
    assert refreshed["software_version"] == software_version()
    assert refreshed["software_commit"] == software_commit()
    assert refreshed["software_commit"] != "deadbeef"
    assert refreshed["peak_localization"]["exclusive_windows"] is True

    entry = json.loads(aggregate.read_text(encoding="utf-8"))["references"][0]
    table_path = Path(entry["peak_tables"]["parabolic"]["path"])
    assert entry["peak_tables"]["parabolic"]["sha256"] == hashlib.sha256(
        table_path.read_bytes()
    ).hexdigest()
    assert entry["peak_tables"]["parabolic"]["sha256"] != "deadbeef"
    assert entry["peak_localization"]["exclusive_windows"] is True

    # `report` uses the same refresh path: break it again and let the helper repair it
    aggregate_record = json.loads(aggregate.read_text(encoding="utf-8"))
    aggregate_record["references"][0]["peak_tables"]["parabolic"]["sha256"] = "deadbeef"
    aggregate.write_text(
        json.dumps(aggregate_record), encoding="utf-8", newline="\n"
    )
    refresh_reference_records(session)
    again = json.loads(aggregate.read_text(encoding="utf-8"))
    assert again["references"][0]["peak_tables"]["parabolic"]["sha256"] != "deadbeef"
    assert again["nmrforge_version"] == software_version()


# ------------------------------------------------------------------ workflow
def test_workflows_are_traceable_and_use_both_localizations(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Specification C/D/E/G: W0001… + three-layer parameter + peak table + complete log + version
    + status. 2026-09-14: The combination mode independently selects peaks (does not track the
    reference peak table), and the refinement method is specified by localization; here
    localization='both' -> one parabolic / gaussian peak table is generated for each spectrum."""
    root = tmp_path / "workflows"
    peaks = _write_peak_table(tmp_path / "reference.list")
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        combos=[
            {"window.F1.off": 0.35, "zero_fill": 1},
            {"window.F1.off": 0.45, "zero_fill": 2},
        ],
        params={"phase_route": "none"},
        peaks=peaks,
        localization="both",
        backend=backend,
    )
    ref = result.reference
    assert ref is not None
    assert [run.workflow_id for run in result.runs] == ["W0001", "W0002"]
    assert [run.condition for run in result.runs] == ["A", "A"]
    for run in result.runs:
        run_dir = Path(run.run_dir)
        assert run_dir.name == "A"
        for name in (
            "process.com",
            "spectrum.ft2",
            "peak_table_parabolic.csv",
            "peak_table_gaussian.csv",
            "log.txt",
            "run.json",
        ):
            assert (run_dir / name).is_file(), name
        assert run.status in (STATUS_SUCCESS, STATUS_WARNING)
        # Use reference script as template: record reference script and hash.
        assert run.base_script["sha256"] == ref.script_sha256
        assert run.base_script["path"] == ref.script_path
        # User parameter vs actual parameter.
        assert run.parameters_requested == run.combo
        # Combined mode: Thresholds locked at reference (per workflow archive source).
        detection = run.parameters_resolved["detection"]
        assert detection["source"] == "reference(locked)"
        assert detection["independent"] is True
        assert detection["methods"] == ["parabolic", "gaussian"]
        assert run.parameters_used["zero_fill"] == run.combo["zero_fill"]
        assert run.parameters_used["window"]["F1"]["off"] == pytest.approx(
            run.combo["window.F1.off"]
        )
        # Phase: automatic recognition result + lock.
        assert run.phase_locked
        assert run.phase["F2"]["phase_mode"] == "auto_reference_locked"
        assert run.phase["F2"]["actual_p0"] == pytest.approx(0.0)
        assert run.parameters_resolved["phase"]["F2"]["actual_p1"] == pytest.approx(
            0.0
        )
        # Version table (software + dependencies; the real machine will also come with
        # nmrpipe/smile).
        assert run.versions.get("nmrforge")
        payload = json.loads(Path(run.run_dir, "run.json").read_text(encoding="utf-8"))
        assert payload["workflow_id"] == run.workflow_id
        assert payload["parameters_requested"] and payload["parameters_used"]
        assert payload["versions"].get("nmrforge")
        # Two peak tables: the same set of fields, each of which is an independently detected peak
        # in this spectrum (the reference peak table is not tracked).
        rows_p = read_peak_table(Path(run.peak_table_path("parabolic")))
        rows_g = read_peak_table(Path(run.peak_table_path("gaussian")))
        assert len(rows_p) == len(rows_g) == 2
        assert [int(row["peak_id"]) for row in rows_p] == [1, 2]
        assert all(row["reference_peak_id"] == "" for row in rows_p)
        assert all(row["reference_peak_id"] == "" for row in rows_g)
        assert all(row["assignment"] == "" for row in rows_p)
        assert all(row["workflow_id"] == run.workflow_id for row in rows_p)
        assert all(row["workflow_id"] == run.workflow_id for row in rows_g)
        assert all(row["detected"] for row in rows_p)
        assert all(row["SNR"] > 0 for row in rows_p)
        assert all(row["localization_method"] == "parabolic" for row in rows_p)
        assert all(row["localization_method"] == "gaussian" for row in rows_g)
        assert run.parameters_resolved["peak_counts"] == {
            "parabolic": 2, "gaussian": 2,
        }
        # Complete log (not just the tail).
        log = Path(run.log_path).read_text(encoding="utf-8")
        assert "parameters_used" in log
        assert "--- processing log ---" in log
        assert "fake process" in log
    # Combination-level records: workflow.json + log.txt.
    wf_dir = root / "study" / "workflows" / "W0001"
    record = json.loads((wf_dir / "workflow.json").read_text(encoding="utf-8"))
    assert record["workflow_id"] == "W0001"
    assert record["status"] in (STATUS_SUCCESS, STATUS_WARNING)
    assert record["parameters_requested"] == {
        "window.F1.off": 0.35,
        "zero_fill": 1,
    }
    assert record["conditions"] == ["A"]
    assert (wf_dir / "log.txt").is_file()
    # Parameter -> Peak position: The difference between 0.35 and 0.45 is 2.5 points (15N), sub-
    # pixel measurement should be restored.
    by_off = {
        round(float(run.combo["window.F1.off"]), 3): run for run in result.runs
    }
    assert by_off[0.35].measurements[0].positions["15N"] == pytest.approx(
        _n15_ppm(_PEAK_A[0] + 1.25), abs=0.3 * _n15_step()
    )
    assert by_off[0.45].measurements[0].positions["15N"] == pytest.approx(
        _n15_ppm(_PEAK_A[0] - 1.25), abs=0.3 * _n15_step()
    )
    # Fid is converted only once (refer to runtime).
    assert backend.convert_calls == 1


def test_zero_fill_keeps_physical_edge_margin_and_window(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """End-to-end: Peak selection margins and combination-by-combination measurement windows are
    converted according to physical width and saved (Option A)."""
    root = tmp_path / "zf_study"
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        axes={"zero_fill": [1, 4]},
        params={"phase_route": "none"},
        backend=backend,
    )
    assert len(result.runs) == 2
    assert all(run.status in (STATUS_SUCCESS, STATUS_WARNING) for run in result.runs)

    # Reference spectrum peak selection: margin = 3 x line width of the axis (15N) converted to ppm,
    # and note down the equivalent number of points and point distance.
    detection = result.reference.peak_params["detection"]
    assert detection["edge_margin_source"] == "ppm_physical_width"
    assert detection["axis0_nucleus"] == "15N"
    assert detection["edge_margin_ppm"] == pytest.approx(
        3 * 15.0 / _N15_OBS, rel=0.02
    )
    assert detection["edge_margin_points"] >= 1
    assert detection["axes"][0]["ppm_per_point"] > 0

    # Combination by combination: The physical width of the margins is the same, and the number of
    # points becomes denser with zero filling.
    by_fill = {
        int(round(float(run.combo["zero_fill"]))): run.window
        for run in result.runs
    }
    for factor in (1, 4):
        spec = by_fill[factor]["0"]
        assert spec["source"] == "ppm_physical_width"
        assert spec["nucleus"] == "15N"
        assert spec["ppm"] == pytest.approx(3 * 15.0 / _N15_OBS, rel=0.02)
    assert by_fill[4]["0"]["points"] == pytest.approx(
        4 * by_fill[1]["0"]["points"], rel=0.5
    )
    assert by_fill[4]["0"]["ppm_per_point"] == pytest.approx(
        by_fill[1]["0"]["ppm_per_point"] / 4.0, rel=0.05
    )

    # Record: The conversion process can be read directly in the manifest and measurement.json.
    manifest = json.loads(
        Path(result.records["manifest"]).read_text(encoding="utf-8")
    )
    measurement = manifest["measurement"]
    assert measurement["reference"][0]["edge_margin"] == detection
    seen = measurement["window_points_seen"]["0"]
    assert seen["nucleus"] == "15N"
    assert max(seen["points"]) > min(seen["points"])  # Points do change with zero filling.
    assert seen["ppm"][0] == pytest.approx(3 * 15.0 / _N15_OBS, rel=0.02)
    assert measurement["window_by_axis"]["0"]["source"] == "ppm_physical_width"
    assert Path(result.records["measurement"]).is_file()
    runs_json = json.loads(Path(result.records["runs"]).read_text(encoding="utf-8"))
    assert all(run["window"] for run in runs_json)


def test_records_are_written_with_unified_peak_tables(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Records:manifest/workflows/runs/two long tables;excluding CSP/statistical product."""
    root = tmp_path / "records"
    result = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}, {"zero_fill": 2}],
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        localization="both",
        backend=_FakeSweepBackend(),
    )
    for name in (
        "manifest",
        "sweep_plan",
        "runs",
        "workflows",
        "measurement",
        "peak_table_parabolic",
        "peak_table_gaussian",
    ):
        assert Path(result.records[name]).is_file(), name
    manifest = json.loads(
        Path(result.records["manifest"]).read_text(encoding="utf-8")
    )
    assert "CSP" in manifest["boundary"]  # Boundary declarations are written into the manifest.
    assert manifest["plan"]["workflow_ids"] == ["W0001", "W0002"]
    assert manifest["references"][0]["peak_tables"]["parabolic"]["path"]
    rows = read_peak_table(Path(result.records["peak_table_parabolic"]))
    assert len(rows) == 4  # 2 workflow × 2 Peak.
    assert {row["workflow_id"] for row in rows} == {"W0001", "W0002"}
    grows = read_peak_table(Path(result.records["peak_table_gaussian"]))
    assert {row["localization_method"] for row in grows} == {"gaussian"}
    summary = result.summary
    assert summary["n_workflows"] == 2
    assert summary["workflow_ids"] == ["W0001", "W0002"]
    assert summary["status_counts"]["n_runs"] == 2
    assert summary["per_workflow"]["W0001"]["conditions"]["A"]["status"] in (
        STATUS_SUCCESS,
        STATUS_WARNING,
    )
    # Resume running from breakpoint: run again without adding new processing calls.
    calls_before = len(_backend_calls(result))
    again = run_parameter_study(
        root,
        None,
        combos=[{"zero_fill": 1}, {"zero_fill": 2}],
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        localization="both",
        backend=result.session.backend,
    )
    assert len(again.runs) == 2
    assert len(_backend_calls(again)) == calls_before


def test_resume_invalidates_changed_plan_and_hides_stale_workflows(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """W0001 input changes must be rerun; old W0002 must not be mixed into the summary after
    shortening the plan."""
    root = tmp_path / "resume_changed"
    backend = _FakeSweepBackend()
    peaks = _write_peak_table(tmp_path / "resume.list")
    first = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}, {"zero_fill": 2}],
        params={"phase_route": "none"},
        peaks=peaks,
        backend=backend,
    )
    calls_before = len(backend.process_calls)
    assert all(run.resume_fingerprint for run in first.runs)

    second = run_parameter_study(
        root,
        None,
        combos=[{"zero_fill": 4}],
        params={"phase_route": "none"},
        peaks=peaks,
        backend=backend,
        resume=True,
    )
    assert len(backend.process_calls) == calls_before + 1
    assert [run.workflow_id for run in second.runs] == ["W0001"]
    assert second.runs[0].parameters_requested == {"zero_fill": 4}
    assert len(load_runs(second.session)) == 1
    workflows = load_workflows(second.session)
    assert [item["workflow_id"] for item in workflows] == ["W0001"]
    stored = json.loads(Path(second.records["workflows"]).read_text(encoding="utf-8"))
    assert [item["workflow_id"] for item in stored] == ["W0001"]


def _backend_calls(result) -> list:
    return list(getattr(result.session.backend, "process_calls", []))


def test_combination_mode_picks_peaks_independently(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """2026-09-14 Specification: The combination mode does not perform reference peak tracking, and
    the peaks come from the combination's own spectrum. Each combination uses **reference lock
    threshold** to independently select peaks on its own candidate spectrum -> the combination's
    own complete peak table; reference_peak_id / assignment is left blank (matching with the
    reference peak table is an external job)."""
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "independent", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    plan = plan_sweep(reference, combos=[{"zero_fill": 1}])
    runs = run_sweep(session, plan, reference=reference, resume=False)
    run = runs[0]
    assert run.status in (STATUS_SUCCESS, STATUS_WARNING)
    table = read_peak_table(Path(run.peak_table_path("parabolic")))
    assert table  # The peaks detected by this combination are not the reference peak table rows.
    assert all(row["detected"] for row in table)
    assert all(row["reference_peak_id"] == "" for row in table)
    assert all(row["assignment"] == "" for row in table)
    assert [int(row["peak_id"]) for row in table] == list(
        range(1, len(table) + 1)
    )
    assert all(row["condition"] == "A" for row in table)
    # The old caliber (available in the reference, if this spectrum cannot be detected, just keep
    # one line with detected=false) has been abandoned.
    assert not any(w["code"] == "peak_not_detected" for w in run.warnings)
    detection = run.parameters_resolved["detection"]
    assert detection["source"] == "reference(locked)"
    assert detection["reference_matching"] == "external"
    assert detection["sigma_multiplier"] == pytest.approx(35.0)


def test_gaussian_fallback_is_recorded_not_silent(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Specification G3:Gaussian fitting failed/The rollback must be done step by step + workflow
    warning."""
    from core.peaks import localize as lz

    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "fallback", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    plan = plan_sweep(reference, axes={"zero_fill": [1]})

    real = lz.localize_peak

    def forced(data, index, **kwargs):
        """Failing to force Gaussian paths (deterministic construction, not relying on data
        contingencies)."""
        if str(kwargs.get("method")) == "gaussian":
            return lz.PeakLocalization(
                requested_method="gaussian",
                actual_method="parabolic",
                position=tuple(float(v) for v in index),
                success=False,
                fallback=True,
                reason="forced_test_failure",
            )
        return real(data, index, **kwargs)

    monkeypatch.setattr(lz, "localize_peak", forced)
    runs = run_sweep(
        session,
        plan,
        reference=reference,
        localization="gaussian",
        resume=False,
    )
    run = runs[0]
    table = read_peak_table(Path(run.peak_table_path("gaussian")))
    assert table
    assert all(row["fallback"] is True for row in table)
    assert all(row["fit_success"] is False for row in table)
    assert {row["fallback_reason"] for row in table} == {"forced_test_failure"}
    assert any(w["code"] == "gaussian_fallback" for w in run.warnings)
    assert run.status == STATUS_WARNING
    assert run.peak_localization["gaussian"]["n_fallback"] == len(table)
    assert run.peak_localization["gaussian"]["fallback_reasons"] == {
        "forced_test_failure": len(table)
    }


def test_gaussian_localization_exception_becomes_failed_run(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gaussian refinement exceptions must result in failed run.json, rather than terminating the
    round."""
    from core.peaks import localize as lz

    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "gaussian_error", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    plan = plan_sweep(reference, axes={"zero_fill": [1]})
    real_localize = lz.localize_peak

    def explode_gaussian(data, index, **kwargs):
        if str(kwargs.get("method")) == "gaussian":
            raise RuntimeError("forced gaussian error")
        return real_localize(data, index, **kwargs)

    monkeypatch.setattr(lz, "localize_peak", explode_gaussian)
    runs = run_sweep(
        session,
        plan,
        reference=reference,
        localization="gaussian",
        resume=False,
    )
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert "forced gaussian error" in runs[0].message
    payload = json.loads(
        Path(runs[0].run_dir, "run.json").read_text(encoding="utf-8")
    )
    assert payload["status"] == "failed"
    assert payload["resume_fingerprint"]


def test_non_2d_gaussian_fallback_rows_set_flag() -> None:
    """Non-2D explicit fallback_reason must also have fallback=true."""
    from nmrforge_api.peak_tables import gaussian_fallback_rows

    measurement = PeakMeasurement(
        peak_id=1,
        assignment="G1",
        reference={"1H": 8.0},
        positions={"1H": 8.01},
        found=True,
    )
    rows = gaussian_fallback_rows(
        [measurement],
        workflow_id="W0001",
        condition="A",
        reason="not_2d",
    )
    assert rows[0]["fallback"] is True
    assert rows[0]["fallback_reason"] == "not_2d"

def test_two_conditions_share_parameters_and_peak_identity(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Specification I: The same workflow uses the same set of parameters for A/B, and each
    condition produces a peak table. 2026-09-14: Independent peak selection in combination mode
    -> the peak table belongs to the spectrum of this condition (reference_peak_id is left
    blank); the reference layer still shares the same reference peak identity table for each
    condition."""
    root = tmp_path / "ab"
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        root,
        datasets={
            "A": bruker_dir / "hsqc_2d",
            "B": bruker_dir / "hsqc_small",
        },
        axes={"zero_fill": [1, 2]},
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        window_ppm=1.0,
        backend=backend,
    )
    assert result.conditions == ["A", "B"]
    assert len(result.runs) == 4  # 2 workflow × 2 Condition.
    by_workflow: dict[str, list] = {}
    for run in result.runs:
        by_workflow.setdefault(run.workflow_id, []).append(run)
    assert set(by_workflow) == {"W0001", "W0002"}
    for _workflow_id, runs in by_workflow.items():
        assert {run.condition for run in runs} == {"A", "B"}
        requested = {json.dumps(run.parameters_requested, sort_keys=True) for run in runs}
        assert len(requested) == 1  # Same group of user parameters.
        for run in runs:
            assert Path(run.run_dir).name == run.condition
            assert Path(run.run_dir, "peak_table_parabolic.csv").is_file()
            rows = read_peak_table(Path(run.peak_table_path("parabolic")))
            # Combined independent peak selection: each condition produces its own peak table of the
            # spectrum (reference_peak_id leaves blank).
            assert rows
            assert all(row["reference_peak_id"] == "" for row in rows)
            assert all(row["condition"] == run.condition for row in rows)
    # The reference for the B condition follows the peak identity of the main condition.
    refs = {key: ref for key, ref in result.references.items()}
    shared = [ref for ref in refs.values() if ref.peak_source.startswith("shared:")]
    assert len(shared) == 1
    assert shared[0].condition == "B"
    assert shared[0].peak_count == 2
    # Convert fid once per condition (reference).
    assert backend.convert_calls == 2


def test_two_conditions_use_their_own_reference_parameter_bases(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """A/B Shared combined overrides, but not overridden parameters must inherit their respective
    references separately."""
    from nmrforge_api.reference import save_reference

    root = tmp_path / "ab_reference_bases"
    backend = _FakeSweepBackend()
    references = run_reference_study(
        root,
        datasets={
            "A": bruker_dir / "hsqc_2d",
            "B": bruker_dir / "hsqc_small",
        },
        params={"phase_route": "none"},
        backend=backend,
    )
    expected = {"A": 0.31, "B": 0.77}
    for condition, off in expected.items():
        reference = references.reference(condition)
        assert reference is not None
        reference.sweep_params = merge_overrides(
            reference.sweep_params, {"window.F1.off": off}
        )
        save_reference(references.session, reference)

    result = run_combination_study(
        str(root),
        combos=[{"zero_fill": 2}],
        direct_range=(10.0, 6.5),
        backend=backend,
    )
    assert {run.condition for run in result.runs} == {"A", "B"}
    for run in result.runs:
        assert run.parameters_used["window"]["F1"]["off"] == pytest.approx(
            expected[run.condition]
        )
        assert run.parameters_used["zero_fill"] == 2
        assert run.parameters_used["ext_lo"] == "10"
        assert run.parameters_used["ext_hi"] == "6.5"


def test_external_peak_identity_is_propagated_to_all_conditions(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """After the external peak table replaces the identity of the primary condition, the secondary
    condition must re-copy the same identity table."""
    external = tmp_path / "one-reference.list"
    export_peaks_poky(
        external,
        [
            {
                "N_shift": _n15_ppm(_PEAK_B[0]),
                "H_shift": _h1_ppm(_PEAK_B[1]),
                "Intensity": 100.0,
                "label": "ONLY",
            }
        ],
    )
    result = run_parameter_study(
        tmp_path / "external_ab",
        datasets={
            "A": bruker_dir / "hsqc_2d",
            "B": bruker_dir / "hsqc_small",
        },
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        peaks=external,
        backend=_FakeSweepBackend(),
    )
    refs = list(result.references.values())
    assert [ref.peak_count for ref in refs] == [1, 1]
    assert len({ref.peak_table_sha256 for ref in refs}) == 1
    assert refs[1].peak_source == "shared:A"
    # The reference identity (external.list) is still shared by each condition; the combined peak
    # table is changed to independent peak selection -> the reference identity is not written.
    for run in result.runs:
        rows = read_peak_table(Path(run.peak_table_path("parabolic")))
        assert rows
        assert all(row["reference_peak_id"] == "" for row in rows)


def test_stop_on_error_keeps_running_successful_conditions(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Stop_on_error only stops on failure, B must still be executed after successful A."""
    result = run_parameter_study(
        tmp_path / "stop_success",
        datasets={
            "A": bruker_dir / "hsqc_2d",
            "B": bruker_dir / "hsqc_small",
        },
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        backend=_FakeSweepBackend(),
    )
    plan = plan_sweep(result.reference, combos=[{"zero_fill": 2}])
    runs = run_sweep(
        result.session,
        plan,
        reference=result.reference,
        resume=False,
        stop_on_error=True,
    )
    assert {run.condition for run in runs} == {"A", "B"}
    assert all(run.status in (STATUS_SUCCESS, STATUS_WARNING) for run in runs)


def test_run_parameter_study_nus_2d(tmp_path: Path, bruker_dir: Path) -> None:
    """2D NUS: Both reference and workflow go to reconstruct_nus, candidate isolation + phase
    lock."""
    root = tmp_path / "nus_study"
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        root,
        bruker_dir / "nus_2d",
        axes={"nSigma": [3.0, 5.0, 7.0]},
        params={"phase_route": "none"},
        backend=backend,
    )
    reference = result.reference
    assert reference.sampling == "nus"
    assert reference.ndim == 2
    assert reference.direct_phase == {"F1": [0.0, 0.0], "F2": [0.0, 0.0]}
    assert reference.peak_source == "auto"
    assert len(result.runs) == 3
    assert all(run.status in (STATUS_SUCCESS, STATUS_WARNING) for run in result.runs)
    assert all(run.phase_locked for run in result.runs)
    # Each combination isolates the product through the candidate output parameter of
    # reconstruct_nus.
    candidate_calls = [c for c in backend.reconstruct_calls if c["out_file"]]
    assert len(candidate_calls) == 3
    assert all(c["params"]["direct_phase"] == [0.0, 0.0] for c in candidate_calls)
    assert all(c["script_name"].endswith(".com") for c in candidate_calls)
    # Candidate spectrum does not cover each other, and the reference spectrum remains independent.
    assert len({run.spectrum_path for run in result.runs}) == 3
    assert len({run.spectrum_sha256 for run in result.runs}) == 3
    # SMILE The actual results of automatic binning are dropped (specification G2).
    for run in result.runs:
        smile = run.parameters_resolved["smile"]
        assert smile["nsigma"]["actual"] == pytest.approx(
            float(run.combo["nSigma"])
        )
        assert smile["nsigma"]["source"] == "user"
        assert run.parameters_resolved["spectrum_noise_sigma"]["value"] > 0
    # The default refinement method is parabolic -> only the parabola table is produced (Gaussian
    # must be specified explicitly).
    for run in result.runs:
        assert Path(run.peak_table_path("parabolic")).is_file()
        assert not run.peak_table_path("gaussian")


def test_reference_state_persists_across_sessions(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Step by step CLI (independent process) can see the registered fid/activity spectrum: the
    project status must be placed on the disk."""
    root = tmp_path / "persist"
    backend = _FakeSweepBackend()
    run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        backend=backend,
    )
    # New Session = New Process Perspective: Reload from project.json.
    session2 = open_study(root, backend=backend)
    entry = session2.data_entry()
    assert entry.fid_path and Path(entry.fid_path).exists()
    assert entry.spectrum_path and Path(entry.spectrum_path).is_file()
    reference = load_reference(session2)
    assert reference is not None
    assert reference.peak_source == "auto"
    assert Path(reference.peak_table_path).is_file()
    assert Path(reference.peak_table_parabolic_path).is_file()
    assert Path(reference.peak_table_gaussian_path).is_file()
    # A pick_peaks running record has also been placed on the market.
    assert any(
        run.workflow_ref == "pick_peaks"
        for run in session2.manager.project.workflow_runs
    )
    # Plan and operation records can be read back.
    loaded_plan = load_plan(session2)
    assert loaded_plan is not None
    assert "base_params" not in loaded_plan.to_dict()
    assert len(load_runs(session2)) == 1
    assert len(load_workflows(session2)) == 1


def test_old_absolute_base_sweep_plan_is_rejected() -> None:
    with pytest.raises(SweepError, match="legacy SweepPlan"):
        SweepPlan.from_dict({"combos": [{"zero_fill": 1}], "base_params": {}})


def test_nus_param_key_alias_normalized() -> None:
    """NSigma/nsigma Both writing methods must fall into the backend input key nsigma."""
    from nmrforge_api.sweep import normalize_nus_params

    assert normalize_nus_params({"nSigma": 5, "thresh": 0.95}) == {
        "nsigma": 5,
        "thresh": 0.95,
    }
    # If lowercase is used, keep the original value (not overwrite).
    assert normalize_nus_params({"nsigma": 3, "nSigma": 9}) == {
        "nsigma": 3,
        "nSigma": 9,
    }
    assert normalize_nus_params({"zero_fill": 2}) == {"zero_fill": 2}


def test_plan_sweep_accepts_explicit_combos(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """The design is determined externally: the explicit composition table is executed as-is (order
    preserved), and the interface makes no design decisions."""
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "combos", backend=backend)
    session.dataset = add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    rows = [
        {"window.F1.off": 0.35, "zero_fill": 1},
        {"window.F1.off": 0.35, "zero_fill": 2},
        {"window.F1.off": 0.45, "zero_fill": 1},
        {"window.F1.off": 0.45, "zero_fill": 2},
    ]
    plan = plan_sweep(reference, combos=rows)
    assert plan.design == "explicit"
    assert plan.n_full == 4
    assert plan.n_workflows == 4
    assert plan.workflow_ids() == ["W0001", "W0002", "W0003", "W0004"]
    assert [dict(c) for c in plan.combos] == rows      # As is, in order.
    assert plan.diagnostics["n_runs"] == 4
    assert plan.diagnostics["duplicated_rows"] == 0
    assert plan.diagnostics["max_abs_correlation"] == 0.0
    assert plan.grid_sha256 == plan_sweep(reference, combos=rows).grid_sha256

    calls_before = len(backend.process_calls)   # The reference run itself has been called once.
    runs = run_sweep(session, plan, reference=reference, resume=False)
    assert [run.parameters_requested for run in runs] == rows
    assert all(run.status in (STATUS_SUCCESS, STATUS_WARNING) for run in runs)
    assert len(backend.process_calls) == calls_before + 4


def test_plan_sweep_requires_exactly_one_design_input(
    tmp_path: Path, bruker_dir: Path
) -> None:
    session = open_study(tmp_path / "one_input", backend=_FakeSweepBackend())
    session.dataset = add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    with pytest.raises(SweepError, match="exactly one"):
        plan_sweep(reference)
    with pytest.raises(SweepError, match="exactly one"):
        plan_sweep(reference, axes={"zero_fill": [1]}, combos=[{"zero_fill": 1}])


def test_combo_table_roundtrip_and_validation(tmp_path: Path) -> None:
    from nmrforge_api import combos_from_rows, load_combo_table, write_combo_table

    rows = [
        {"window.F1.off": 0.35, "zero_fill": 1, "phase_delta.F2.p0": -5},
        {"window.F1.off": 0.45, "zero_fill": 2, "phase_delta.F2.p0": 5},
    ]
    table = write_combo_table(tmp_path / "design.csv", rows)
    assert load_combo_table(table) == rows
    with pytest.raises(SweepError, match="outside the declared levels"):
        combos_from_rows([{"a": 3}], axes={"a": [1, 2]})
    with pytest.raises(SweepError, match="not declared in axes"):
        combos_from_rows([{"b": 1}], axes={"a": [1, 2]})


def test_plan_sweep_axis_scope_guards(tmp_path: Path, bruker_dir: Path) -> None:
    """Lock key error; certainty/Unknown keys only prompt (deterministic parameters do not need to
    be entered into the grid)."""
    session = open_study(tmp_path / "scope", backend=_FakeSweepBackend())
    session.dataset = add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    with pytest.raises(SweepError, match="phase_delta"):
        plan_sweep(reference, axes={"direct_phase": [[0.0, 0.0]]})
    plan = plan_sweep(
        reference,
        axes={
            "ext_lo": ["10.5"],
            "points_per_line": [2.0],
            "bogus.key": [1],
            "zero_fill": [1],
        },
    )
    joined = "\n".join(plan.notes)
    assert "deterministic" in joined and "points_per_line" in joined
    assert "direct range" in joined and "ext_lo" in joined
    assert "not in the list the backend reads" in joined and "bogus.key" in joined


def test_phase_delta_axis_shifts_locked_phase(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """The phase identification deviation (+/-5°) is used as the parameter axis: it is applied on
    the reference phase and then passed to the backend and retained."""
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "phase", backend=backend)
    session.dataset = add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    assert reference.direct_phase == {"F2": [0.0, 0.0]}
    plan = plan_sweep(reference, axes={"phase_delta.F2.p0": [-5, 5]})
    runs = run_sweep(session, plan, reference=reference, resume=False)
    assert [run.phase["F2"]["actual_p0"] for run in runs] == [-5.0, 5.0]
    assert all(
        run.phase["F2"]["phase_mode"] == "manual_delta_from_reference"
        for run in runs
    )
    assert all(run.phase_locked for run in runs)
    phases = [call["phase"] for call in backend.process_calls if call["phase"]]
    assert phases[0] == {"F2": (-5.0, 0.0)}
    assert phases[1] == {"F2": (5.0, 0.0)}


def test_sweep_rejects_3d_nus(tmp_path: Path, bruker_dir: Path) -> None:
    """3D NUS is still not supported: NUS only 2D is available."""
    session = open_study(tmp_path / "study", backend=_FakeSweepBackend())
    session.dataset = add_dataset(session, bruker_dir / "nus_2d")
    reference = ReferenceSpectrum(
        dataset_key="exp_001/d_001",
        exp_id="exp_001",
        data_id="d_001",
        ndim=3,
        sampling="nus",
        sweep_params={"window": {"F1": {"off": 0.4}}},
    )
    plan = plan_sweep(reference, axes={"zero_fill": [1, 2]})
    with pytest.raises(SweepError, match="2D NUS"):
        run_sweep(session, plan, reference=reference)


def test_add_dataset_rejects_non_bruker(tmp_path: Path) -> None:
    session = open_study(tmp_path / "study", backend=_FakeSweepBackend())
    bogus = tmp_path / "not_bruker"
    bogus.mkdir()
    with pytest.raises(DatasetError, match="Bruker"):
        add_dataset(session, bogus)
    with pytest.raises(DatasetError, match="does not exist"):
        add_dataset(session, tmp_path / "missing")


def test_add_dataset_rejects_duplicate_condition(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Multi-condition: labels must be unique, or A/B would share one dataset."""
    session = open_study(tmp_path / "dup", backend=_FakeSweepBackend())
    add_dataset(session, bruker_dir / "hsqc_2d", condition="A")
    with pytest.raises(DatasetError, match="condition label"):
        add_dataset(session, bruker_dir / "hsqc_small", condition="A")


def test_condition_tokens_do_not_alias_and_reject_case_collisions(
    tmp_path: Path,
) -> None:
    assert condition_token("A") == "A"
    assert condition_token("A/B") != condition_token("A_B")
    assert condition_token("α") != condition_token("β")  # non-ASCII labels stay distinct

    session = open_study(tmp_path / "token_collision", backend=_FakeSweepBackend())
    session.add_dataset_ref(DatasetRef("exp_001", "d_001", condition="A"))
    with pytest.raises(DatasetError, match="token collision"):
        session.add_dataset_ref(DatasetRef("exp_002", "d_002", condition="a"))


def test_cli_status_and_report(
    tmp_path: Path, bruker_dir: Path, capsys
) -> None:
    root = tmp_path / "study"
    result = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        combos=[{"window.F1.off": 0.40}],
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        window_ppm=1.0,
        backend=_FakeSweepBackend(),
    )
    assert result.runs[0].status in (STATUS_SUCCESS, STATUS_WARNING)
    assert cli_main(["status", "--study", str(root)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["runs"]["n_runs"] == 1
    assert payload["workflows"]["ids"] == ["W0001"]
    assert payload["references"]
    assert cli_main(["report", "--study", str(root)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"]["n_runs"] == 1
    # The combination mode must be explicitly referenced: --reference required.
    assert (
        cli_main(
            [
                "sweep",
                "--study",
                str(root),
                "--reference",
                str(root),
                "--grid",
                "missing.yaml",
            ]
        )
        == 2
    )


def test_api_does_not_import_qt() -> None:
    """The external interface must be able to run without Qt environment/Import on the cluster."""
    code = (
        "import sys, nmrforge_api; "
        "bad = sorted(m for m in sys.modules "
        "if m.startswith(('PyQt', 'PySide', 'shiboken'))); "
        "assert not bad, bad; "
        "assert not any(m.startswith('gui') for m in sys.modules), 'gui imported'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_statistics_helper_is_not_in_processing_contract() -> None:
    """Specification J (2026-09-13 user ruling): σ/Δδ only does **test/Detection aid** and does not
    enter the processing contract. The software itself only performs processing and leaves
    files; the assistant can be used for regression detection "whether parameter is really
    effective" and downstream analysis reference implementation, but the processing chain
    (study/sweep/records/CLI) does not call it, nor does it produce the corresponding file."""
    import nmrforge_api
    from nmrforge_api.uncertainty import (
        position_uncertainty,
        uncertainty_summary,
    )

    assert callable(position_uncertainty) and callable(uncertainty_summary)
    package = Path(nmrforge_api.__file__).parent
    for name in ("study.py", "sweep.py", "records.py", "cli.py", "reference.py"):
        source = (package / name).read_text(encoding="utf-8")
        for token in (
            "position_uncertainty",
            "uncertainty_summary",
            "delta_std",
            "csp_n_weight",
            "uncertainty.csv",
        ):
            assert token not in source, (name, token)
    # the module states its own scope (test/detection), so it is not mistaken for an artefact
    first_line = Path(nmrforge_api.uncertainty.__file__).read_text(
        encoding="utf-8"
    ).splitlines()[0]
    assert "test" in first_line


def test_position_uncertainty_formula() -> None:
    """Helper formula regression: sigma is the sample sd; delta_std = sqrt(sum(w_n*sigma_n)^2)."""

    def measurement(h: float, n: float) -> PeakMeasurement:
        return PeakMeasurement(
            peak_id=1,
            assignment="G1",
            reference={"1H": 5.0, "15N": 119.0},
            positions={"1H": h, "15N": n},
            deltas={"1H": h - 5.0, "15N": n - 119.0},
            found=True,
        )

    from nmrforge_api import position_uncertainty, uncertainty_summary

    runs = {
        "W0001": [measurement(5.0, 119.0)],
        "W0002": [measurement(5.02, 119.2)],
        "W0003": [measurement(4.98, 118.8)],
    }
    items = position_uncertainty(runs, csp_n_weight=0.2)
    assert len(items) == 1
    item = items[0]
    assert item.n_runs == 3
    assert item.sigma["1H"] == pytest.approx(0.02, abs=1e-6)
    assert item.sigma["15N"] == pytest.approx(0.2, abs=1e-6)
    expected = math.sqrt(0.02**2 + (0.2 * 0.2) ** 2)
    assert item.delta_std == pytest.approx(expected, rel=1e-6)
    assert item.delta_max == pytest.approx(expected, rel=1e-6)
    summary = uncertainty_summary(items, csp_n_weight=0.2, n_runs=3)
    assert summary["n_peaks"] == 1
    assert summary["delta_std_ppm"]["median"] == pytest.approx(expected, rel=1e-6)


def test_position_uncertainty_requires_all_nuclei() -> None:
    """The assistant only counts the combinations that are measured by all tested cores (half of
    the data is not involved)."""
    from nmrforge_api import position_uncertainty

    partial = PeakMeasurement(
        peak_id=2,
        assignment="G2",
        reference={"1H": 5.0, "15N": 119.0},
        positions={"1H": 5.0},
        found=True,
    )
    full = PeakMeasurement(
        peak_id=2,
        assignment="G2",
        reference={"1H": 5.0, "15N": 119.0},
        positions={"1H": 5.0, "15N": 119.0},
        found=True,
    )
    items = position_uncertainty({"W0001": [partial], "W0002": [full]})
    assert items[0].n_runs == 1
    assert items[0].missing_runs == 1


def test_helper_detects_parameter_effect_in_end_to_end_run(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Detection purpose: Use the σ/Δδ assistant to confirm that the parameter really takes effect
    (all 0 = parameter is silently ignored). Corresponding to the defects that have appeared in
    the history of real machines: SMILE The parameter key is written incorrectly -> Each
    combination runs the same spectrum, and the peak positions are completely consistent (Δδ is
    all 0) but success is still displayed. Here it is used for regression self-test."""
    from nmrforge_api import position_uncertainty, uncertainty_summary

    backend = _FakeSweepBackend()
    result = run_parameter_study(
        tmp_path / "detect_effect",
        bruker_dir / "hsqc_2d",
        combos=[{"window.F1.off": 0.35}, {"window.F1.off": 0.45}],
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        window_ppm=1.0,
        backend=backend,
    )
    # Parameter really takes effect: candidate spectrum is different from each other.
    assert len({run.spectrum_sha256 for run in result.runs}) == 2
    uncertainties = position_uncertainty(result.runs, csp_n_weight=0.2)
    assert uncertainties
    assert all(item.delta_std > 0 for item in uncertainties)
    summary = uncertainty_summary(uncertainties, n_runs=len(result.runs))
    assert summary["delta_std_ppm"]["max"] > 0
    # The assistant does not process the product: there is no uncertainty in the records file.
    assert not any("uncertainty" in name for name in result.records)


def test_peak_threshold_is_chosen_with_reference_then_locked(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """2026-09-14 (user): The threshold is only optional when generating the reference. After the
    reference is set, it must be consistent with the reference. When generating the reference,
    the σ multiple can be specified externally; once the reference is frozen, subsequent
    parameter perturbations will continue to use the threshold -- Explicitly given different
    thresholds will be rejected (no silent peak reselection). To change the threshold, you can
    only explicitly rebuild the reference (force=True)."""
    session = open_study(tmp_path / "threshold_locked", backend=_FakeSweepBackend())
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    # Externally specified thresholds when generating references.
    reference = ensure_reference_peaks(session, reference, sigma_multiplier=20)
    assert reference.peak_params["sigma_multiplier"] == pytest.approx(20.0)
    assert reference.peak_params["detection"]["sigma_multiplier"] == pytest.approx(
        20.0
    )
    assert reference.peak_params["detection"]["threshold_source"] == "user"

    def _picks() -> int:
        return sum(
            1
            for run in session.manager.project.workflow_runs
            if run.workflow_ref == "pick_peaks"
        )

    picks = _picks()
    frozen_sha = reference.peak_table_sha256
    # Same threshold / no threshold -> multiplex reference, no reselection.
    reference = ensure_reference_peaks(session, reference, sigma_multiplier=20)
    ensure_reference_peaks(session, reference)
    assert _picks() == picks
    assert reference.peak_table_sha256 == frozen_sha
    # Different thresholds -> reject (reference locked).
    with pytest.raises(ReferenceError, match="locked"):
        ensure_reference_peaks(session, reference, sigma_multiplier=60)
    assert _picks() == picks                     # No secret re-election.
    assert reference.peak_table_sha256 == frozen_sha
    # Explicitly rebuilding the reference allows changing the threshold.
    reference = ensure_reference_peaks(
        session, reference, sigma_multiplier=60, force=True
    )
    assert reference.peak_params["sigma_multiplier"] == pytest.approx(60.0)
    assert reference.peak_params["previous_sigma_multiplier"] == pytest.approx(20.0)
    assert _picks() > picks


def test_peak_threshold_defaults_to_35_sigma(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """When no threshold is specified, the existing default of 35σ remains (behaviour is backwards
    compatible)."""
    session = open_study(tmp_path / "default_threshold", backend=_FakeSweepBackend())
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    detection = reference.peak_params["detection"]
    assert reference.peak_params["sigma_multiplier"] is None
    assert detection["sigma_multiplier"] == pytest.approx(35.0)
    assert detection["threshold_source"].startswith("default")


def test_peak_threshold_too_high_reports_error_instead_of_silence(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """If the threshold is too high to select a peak, an error will be clearly reported (an empty
    peak table will not be silently generated when successful)."""
    session = open_study(tmp_path / "bad_threshold", backend=_FakeSweepBackend())
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    with pytest.raises(MeasurementError):
        ensure_reference_peaks(session, reference, sigma_multiplier=100000)


def test_peak_picking_keys_in_combination_table_are_rejected(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """The threshold is part of the reference definition: write it into the workflow combination
    table and report an error directly (not silently invalid)."""
    session = open_study(tmp_path / "grid_hint", backend=_FakeSweepBackend())
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    with pytest.raises(SweepError, match="picking threshold"):
        plan_sweep(reference, axes={"sigma_multiplier": [20], "zero_fill": [1]})


def test_reference_and_run_records_carry_software_commit(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """P0-1: reference.json / run.json / manifest.json each carry software_commit."""
    from core.version import software_commit, software_version

    result = run_parameter_study(
        tmp_path / "provenance",
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        window_ppm=1.0,
        backend=_FakeSweepBackend(),
    )
    ref = result.reference
    assert ref is not None
    assert ref.software_version == software_version()
    assert ref.software_commit == software_commit()
    assert "software_commit" in ref.to_dict()

    study = Path(ref.frozen_spectrum).parents[2]
    reference_json = json.loads(
        (Path(ref.frozen_spectrum).parent / "reference.json").read_text(
            encoding="utf-8"
        )
    )
    assert reference_json["software_version"] == software_version()
    assert "software_commit" in reference_json

    run_jsons = sorted(study.glob("workflows/*/*/run.json"))
    assert run_jsons, "every run writes a run.json"
    run_json = json.loads(run_jsons[0].read_text(encoding="utf-8"))
    assert run_json["software_version"] == software_version()
    assert "software_commit" in run_json
    assert run_json["versions"]["software_commit"] == software_commit()

    manifest = json.loads(
        (study / "records" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["nmrforge_version"] == software_version()
    assert "software_commit" in manifest


def test_rebuild_reference_peak_tables_keeps_spectrum_and_identity(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """P0-2: only the two unified peak tables are rebuilt; spectrum and identity stay."""
    from core.project.manager import sha256_file
    from nmrforge_api import rebuild_reference_peak_tables

    result = run_parameter_study(
        tmp_path / "rebuild",
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        window_ppm=1.0,
        backend=_FakeSweepBackend(),
    )
    session = result.session
    ref = result.reference
    assert ref is not None
    spectrum = Path(ref.frozen_spectrum)
    identity = Path(ref.peak_table_path)
    spectrum_sha = sha256_file(spectrum)
    identity_sha = sha256_file(identity)
    # dirty both tables: the rebuild has to overwrite them
    for method in ("parabolic", "gaussian"):
        Path(ref.peak_tables[method]["path"]).write_text(
            "workflow_id\n", encoding="utf-8"
        )

    updated = rebuild_reference_peak_tables(session, ref)
    assert sha256_file(spectrum) == spectrum_sha
    assert sha256_file(identity) == identity_sha
    assert updated.spectrum_sha256 == spectrum_sha
    for method in ("parabolic", "gaussian"):
        rows = read_peak_table(Path(updated.peak_tables[method]["path"]))
        assert len(rows) == 2
        assert updated.peak_tables[method]["sha256"]
    assert updated.peak_localization["exclusive_windows"] is True

    # the CLI entry rebuilds the tables only and leaves the spectrum alone
    assert (
        cli_main(
            ["reference", "--study", str(session.root), "--rebuild-peak-tables"]
        )
        == 0
    )
    assert sha256_file(spectrum) == spectrum_sha
    assert sha256_file(identity) == identity_sha

    # no reference -> explicit error, never silent
    empty = open_study(tmp_path / "no_reference")
    with pytest.raises(ReferenceError):
        rebuild_reference_peak_tables(empty, None)


def test_cli_peaks_applies_external_threshold_when_reference_is_built(
    tmp_path: Path, bruker_dir: Path, capsys
) -> None:
    """CLI:`peaks --sigma N` Select peaks according to the external threshold when generating the
    reference peak table; subsequent changes to the threshold are rejected."""
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "cli_threshold", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    build_reference(session, params={"phase_route": "none"})   # Only reference spectrum/script.
    root = session.root
    # The reference peak table has not yet been generated -> specify the threshold at this time =
    # select the threshold when generating the reference.
    assert cli_main(["peaks", "--study", str(root), "--sigma", "20"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["conditions"][0]["params"]["sigma_multiplier"] == pytest.approx(20.0)
    detection = payload["conditions"][0]["params"]["detection"]
    assert detection["sigma_multiplier"] == pytest.approx(20.0)
    assert payload["conditions"][0]["params"]["detection"]["threshold_source"] == "user"
    # Reference has been determined (20σ) -> If the threshold is changed again, an error will be
    # reported (exit code 2).
    assert cli_main(["peaks", "--study", str(root), "--sigma", "60"]) == 2
    assert "locked" in capsys.readouterr().out


def test_frozen_default_threshold_cannot_be_changed_later(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """After the reference is frozen at the default 35σ, any other threshold specified will also be
    rejected (only allowed if it is consistent with the reference)."""
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "frozen_default", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)      # Default 35σ.
    assert reference.peak_params["detection"]["sigma_multiplier"] == pytest.approx(
        35.0
    )
    # Consistent with the reference (35σ) can be explicitly given -> multiplexed.
    ensure_reference_peaks(session, reference, sigma_multiplier=35)
    # Inconsistent with reference -> Reject.
    with pytest.raises(ReferenceError, match="locked"):
        ensure_reference_peaks(session, reference, sigma_multiplier=20)


def test_workflow_records_reference_locked_threshold(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Each workflow record states "the peak selection threshold consistent with the reference"."""
    result = run_parameter_study(
        tmp_path / "locked_records",
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        sigma_multiplier=25,
        backend=_FakeSweepBackend(),
    )
    run = result.runs[0]
    locked = run.parameters_resolved["detection"]
    assert locked["sigma_multiplier"] == pytest.approx(25.0)
    assert locked["sigma_multiplier_origin"] == "reference"
    assert locked["source"] == "reference(locked)"
    assert locked["independent"] is True
    assert locked["reference_matching"] == "external"
    assert result.reference.peak_params["sigma_multiplier"] == pytest.approx(25.0)
    payload = json.loads(Path(run.run_dir, "run.json").read_text(encoding="utf-8"))
    assert payload["parameters_resolved"]["detection"]["source"] == (
        "reference(locked)"
    )


def _write_bruker_full_sampling_as_nus(tmp_path: Path) -> Path:
    """Bruker 2D data: Annotation NUS(NusAMOUNT=25) but ser has no zero rows in the entire grid =
    actual full sampling."""
    ds = tmp_path / "full_as_nus"
    ds.mkdir(parents=True, exist_ok=True)
    x_n, td_rows = 64, 16  # FnMODE=5 → Complex point grid 8, declaration full grid 16 lines.
    (ds / "acqus").write_text(
        f"##$TD= {x_n}\n##$FnMODE= 0\n##$NusAMOUNT= 25\n##$NusTD= 0\n"
        "##$DTYPE= 0\n",
        encoding="utf-8",
    )
    (ds / "acqu2s").write_text(
        f"##$TD= {td_rows}\n##$FnMODE= 5\n##$NusTD= {td_rows}\n##$NUC1= <15N>\n",
        encoding="utf-8",
    )
    data = np.full((td_rows, x_n), 5.0, dtype="<i4")
    data.tofile(ds / "ser")
    return ds


def test_disguised_full_sampling_is_processed_as_uniform(tmp_path: Path) -> None:
    """Mark NUS but the actual full sampling -> API is processed as uniform, and valid sampling is
    saved (2026-09-14)."""
    dataset = _write_bruker_full_sampling_as_nus(tmp_path)
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        tmp_path / "fs_study",
        dataset,
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        backend=backend,
    )
    reference = result.reference
    assert reference is not None
    assert reference.sampling == "uniform"
    assert reference.sampling_schedule == "full_sampling"
    assert any("actual full sampling" in line for line in reference.sampling_evidence)
    # Uniform throughout: candidate spectrum is produced by process(), without SMILE reconstruction
    # call.
    assert backend.process_calls
    assert not backend.reconstruct_calls
    run = result.runs[0]
    mapping = run.parameters_resolved["sampling"]
    assert mapping["effective"] == "uniform"
    assert mapping["route"] == "process"
    assert mapping["schedule"] == "full_sampling"
    assert any("actual full sampling" in line for line in mapping["evidence"])
    payload = json.loads(Path(run.run_dir, "run.json").read_text(encoding="utf-8"))
    assert payload["parameters_resolved"]["sampling"]["effective"] == "uniform"
    manifest = json.loads(Path(result.records["manifest"]).read_text(encoding="utf-8"))
    assert manifest["references"][0]["sampling"] == "uniform"
    assert manifest["references"][0]["sampling_schedule"] == "full_sampling"


def test_combination_mode_requires_explicit_reference(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """The combination mode must explicitly specify the reference: empty reference and uncreated
    reference will explicitly report an error (no implicit cover)."""
    with pytest.raises(ReferenceError, match="explicit reference"):
        run_combination_study("", combos=[{"zero_fill": 1}])
    root = tmp_path / "no_reference"
    backend = _FakeSweepBackend()
    session = open_study(root, backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    with pytest.raises(ReferenceError, match="reference mode"):
        run_combination_study(str(root), combos=[{"zero_fill": 1}])


def test_reference_mode_then_combination_mode_with_explicit_reference(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """The reference mode only creates a reference; the combination mode only runs the workflow
    when the reference is explicitly given, and does not rebuild the reference."""
    root = tmp_path / "two_modes"
    backend = _FakeSweepBackend()
    reference_result = run_reference_study(
        root, bruker_dir / "hsqc_2d", backend=backend
    )
    assert reference_result.conditions == ["A"]
    reference = reference_result.reference()
    assert reference is not None
    assert Path(reference.peak_table_parabolic_path).is_file()
    assert Path(reference.peak_table_gaussian_path).is_file()
    assert Path(reference_result.records["reference"]).is_file()
    frozen_script = reference.script_sha256
    frozen_peaks = reference.peak_table_sha256

    def _picks() -> int:
        return sum(
            1
            for run in reference_result.session.manager.project.workflow_runs
            if run.workflow_ref == "pick_peaks"
        )

    picks = _picks()
    result = run_combination_study(
        f"{root}#A", combos=[{"zero_fill": 1}], backend=backend
    )
    assert [run.workflow_id for run in result.runs] == ["W0001"]
    assert result.summary["reference_spec"] == f"{root}#A"
    manifest = json.loads(Path(result.records["manifest"]).read_text(encoding="utf-8"))
    assert manifest["mode"] == "combination"
    assert manifest["reference_spec"] == f"{root}#A"
    # The combined mode does not rebuild the reference (both peak selection and reference hash
    # remain unchanged).
    assert _picks() == picks
    after = load_reference(result.session, result.session.dataset)
    assert after is not None
    assert after.script_sha256 == frozen_script
    assert after.peak_table_sha256 == frozen_peaks
    # References can also be specified explicitly using the reference.json path.
    spec = str(Path(reference.peak_table_path).parent / "reference.json")
    again = run_combination_study(spec, combos=[{"zero_fill": 1}], backend=backend)
    assert again.summary["reference_spec"] == spec


def test_reference_mode_reports_both_peak_tables(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Reference model product: 1 script + 2 reference peak tables, and write
    records/reference.json."""
    result = run_reference_study(
        tmp_path / "reference_mode",
        bruker_dir / "hsqc_2d",
        backend=_FakeSweepBackend(),
    )
    reference = result.reference()
    assert reference is not None
    tables = result.peak_tables
    assert Path(tables["parabolic"]).is_file() and Path(tables["gaussian"]).is_file()
    payload = json.loads(Path(result.records["reference"]).read_text(encoding="utf-8"))
    assert payload["mode"] == "reference"
    assert payload["references"][0]["peak_tables"]["parabolic"]["path"]
    assert payload["references"][0]["sampling"] == "uniform"


def test_direct_range_parser_normalises_order() -> None:
    """The direct dimension range: (high, low) and the reverse order are standardized as
    ext_lo=high end / ext_hi=low end."""
    from nmrforge_api import parse_direct_range

    direct = parse_direct_range((10.5, 6.5))
    assert direct is not None
    assert (direct.lo, direct.hi) == (10.5, 6.5)
    assert direct.swapped is False
    assert direct.params() == {"ext_lo": "10.5", "ext_hi": "6.5"}
    assert direct.to_dict()["requested"] == [10.5, 6.5]

    swapped = parse_direct_range((6.5, 10.5))
    assert swapped is not None
    assert swapped.params() == {"ext_lo": "10.5", "ext_hi": "6.5"}
    assert swapped.swapped is True
    assert swapped.to_dict()["swapped_to_nmrpipe_order"] is True

    # The dict writing method is compatible with the old writing method of explicit
    # ext_lo/ext_hi;params.
    assert parse_direct_range({"lo": 10.0, "hi": 7.0}).params() == {
        "ext_lo": "10",
        "ext_hi": "7",
    }
    assert parse_direct_range(ext_lo="9.5", ext_hi="6.0").params() == {
        "ext_lo": "9.5",
        "ext_hi": "6",
    }
    assert parse_direct_range(params={"ext_lo": "11", "ext_hi": "7"}).lo == 11.0
    # When multiple entries are given at the same time: explicit parameter covers direct_range,
    # direct_range covers params.
    explicit = parse_direct_range(
        (10.5, 6.5), ext_lo="9.5", ext_hi="7.0",
        params={"ext_lo": "12", "ext_hi": "5"},
    )
    assert explicit is not None
    assert explicit.params() == {"ext_lo": "9.5", "ext_hi": "7"}
    partial = parse_direct_range(
        {"lo": 10.0}, ext_hi=6.0, params={"ext_lo": 12.0, "ext_hi": 5.0}
    )
    assert partial is not None
    assert partial.params() == {"ext_lo": "10", "ext_hi": "6"}
    assert parse_direct_range(None) is None
    # Illegal: both ends are the same / only one end is given / non-numeric value.
    with pytest.raises(SweepError):
        parse_direct_range((7.0, 7.0))
    with pytest.raises(SweepError):
        parse_direct_range(ext_lo="9.5")
    with pytest.raises(SweepError):
        parse_direct_range(("a", "b"))


def test_reference_mode_accepts_direct_range(tmp_path: Path, bruker_dir: Path) -> None:
    """The reference mode can specify the direct dimension range: fall to the back-end parameter
    and keep the file; rebuild the reference when it changes."""
    root = tmp_path / "direct_range_reference"
    backend = _FakeSweepBackend()
    result = run_reference_study(
        root,
        bruker_dir / "hsqc_2d",
        direct_range=(10.5, 6.5),
        backend=backend,
    )
    reference = result.reference()
    assert reference is not None
    assert str(reference.params.get("ext_lo")) == "10.5"
    assert str(reference.params.get("ext_hi")) == "6.5"
    calls = [call["params"] for call in backend.process_calls]
    assert calls and str(calls[-1].get("ext_lo")) == "10.5"
    assert str(calls[-1].get("ext_hi")) == "6.5"
    first_run_id = reference.run_id
    # Change to another range -> automatically rebuild the reference (not silent reuse).
    again = run_reference_study(
        root, direct_range=(11.0, 6.0), backend=backend
    )
    rebuilt = again.reference()
    assert rebuilt is not None
    assert str(rebuilt.params.get("ext_lo")) == "11"
    assert str(rebuilt.params.get("ext_hi")) == "6"
    assert rebuilt.run_id != first_run_id  # I really reran it for reference.


def test_combination_mode_direct_range_override(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Combination mode can cover the direct dimension range (base value + combination by
    combination), the reference spectrum is not reconstructed."""
    root = tmp_path / "direct_range_combos"
    backend = _FakeSweepBackend()
    run_reference_study(root, bruker_dir / "hsqc_2d", backend=backend)
    frozen = load_reference(open_study(root, backend=backend), None)
    assert frozen is not None
    frozen_script = frozen.script_sha256
    result = run_combination_study(
        str(root),
        combos=[
            {"zero_fill": 1},
            {"zero_fill": 1, "ext_lo": "9", "ext_hi": "7"},
        ],
        direct_range=(10.0, 6.5),
        backend=backend,
    )
    by_id = {run.workflow_id: run for run in result.runs}
    base = by_id["W0001"].parameters_resolved["direct_range"]
    assert base["ext_lo"] == "10" and base["ext_hi"] == "6.5"
    assert base["source"] == "reference_or_base"
    combo = by_id["W0002"].parameters_resolved["direct_range"]
    assert combo["ext_lo"] == "9" and combo["ext_hi"] == "7"
    assert combo["source"] == "combo"
    # The reference spectrum is not affected by the combination mode.
    after = load_reference(result.session, result.session.dataset)
    assert after is not None and after.script_sha256 == frozen_script
    assert result.summary["direct_range"]["ext_lo"] == 10.0


def test_empty_combo_cells_mean_unspecified(tmp_path: Path, bruker_dir: Path) -> None:
    """Leave the combination table blank = do not overwrite the parameter (the "null value is
    regarded as overwriting" found on the real machine has been corrected)."""
    root = tmp_path / "empty_cells"
    backend = _FakeSweepBackend()
    run_reference_study(
        root, bruker_dir / "hsqc_2d", direct_range=(10.0, 6.5), backend=backend
    )
    table = tmp_path / "combos.csv"
    table.write_text(
        "zero_fill,ext_lo,ext_hi\n1,,\n1,9,7\n", encoding="utf-8"
    )
    result = run_combination_study(
        str(root), combos=load_combo_table(table), backend=backend
    )
    by_id = {run.workflow_id: run for run in result.runs}
    first = by_id["W0001"].parameters_resolved["direct_range"]
    # Empty cell -> inherit the reference base (10 / 6.5), and the source is not combo.
    assert first["ext_lo"] == "10" and first["ext_hi"] == "6.5"
    assert first["source"] == "reference_or_base"
    second = by_id["W0002"].parameters_resolved["direct_range"]
    assert str(second["ext_lo"]) == "9" and str(second["ext_hi"]) == "7"
    assert second["source"] == "combo"


def test_combo_table_supports_per_dimension_keys(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """The combination table is specified by dimension: the dot key of window/baseline/zero_fill
    takes effect axis by axis and is saved."""
    root = tmp_path / "per_axis"
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        combos=[
            {
                "window.F1.off": 0.35,
                "window.F2.off": 0.45,
                "zero_fill.F1": 2,
                "baseline.F2.enabled": False,
            }
        ],
        params={"phase_route": "none"},
        backend=backend,
    )
    run = result.runs[0]
    # Requested retains the dot key of user as it is; used is the merged axis-by-axis structure.
    assert run.parameters_requested["window.F1.off"] == pytest.approx(0.35)
    used = run.parameters_used
    assert used["window"]["F1"]["off"] == pytest.approx(0.35)
    assert used["window"]["F2"]["off"] == pytest.approx(0.45)
    assert used["zero_fill"]["F1"] == 2
    assert used["baseline"]["F2"]["enabled"] is False
    # The backend really receives the axis-by-axis parameter (instead of being flattened).
    sent = backend.process_calls[-1]["params"]
    assert sent["window"]["F1"]["off"] == pytest.approx(0.35)
    assert sent["window"]["F2"]["off"] == pytest.approx(0.45)
    assert sent["zero_fill"]["F1"] == 2
    assert sent["baseline"]["F2"]["enabled"] is False
    payload = json.loads(Path(run.run_dir, "run.json").read_text(encoding="utf-8"))
    assert payload["parameters_used"]["zero_fill"]["F1"] == 2


# ------------------------------------------ Combination mode: Independent peak selection revision
# (2026-09-14).
def test_combination_threshold_keys_are_rejected(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """The threshold is only selected when generating a reference: Threshold key appears in the
    combination table -> SweepError (locked in reference)."""
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "locked_threshold_keys", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    for key, value in (
        ("sigma_multiplier", 20),
        ("min_snr", 20),
        ("threshold_sigma", 20),
        ("detection.sigma_multiplier", 20),
    ):
        with pytest.raises(SweepError, match="picking threshold"):
            plan_sweep(reference, combos=[{"zero_fill": 1, key: value}])
    # The allowed detection keys are only in refinement mode; other detection.* keys are unknown
    # keys (error reports are not silent).
    plan = plan_sweep(reference, combos=[{"zero_fill": 1, "localization": "both"}])
    assert plan.combos[0]["localization"] == "both"
    assert any("refinement" in note for note in plan.notes)
    with pytest.raises(SweepError, match="unknown detection key"):
        plan_sweep(reference, combos=[{"zero_fill": 1, "detection.max_peaks": 5}])


def test_combination_localization_selection_and_per_combo_override(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """By default, parabolic only produces one table; gaussian / both are explicitly specified; it
    can be overridden on a combination-by-combination basis."""
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        tmp_path / "localization_override",
        bruker_dir / "hsqc_2d",
        combos=[
            {"zero_fill": 1, "localization": "gaussian"},
            {"zero_fill": 2, "localization": "parabolic"},
        ],
        params={"phase_route": "none"},
        backend=backend,
    )
    by_id = {run.workflow_id: run for run in result.runs}
    assert by_id["W0001"].peak_table_path("gaussian")
    assert not by_id["W0001"].peak_table_path("parabolic")
    assert by_id["W0002"].peak_table_path("parabolic")
    assert not by_id["W0002"].peak_table_path("gaussian")
    assert by_id["W0001"].parameters_resolved["detection"]["methods"] == ["gaussian"]
    assert by_id["W0002"].parameters_resolved["detection"]["methods"] == ["parabolic"]
    grow = read_peak_table(Path(by_id["W0001"].peak_table_path("gaussian")))
    assert grow and all(row["localization_method"] == "gaussian" for row in grow)
    assert all(row["fit_success"] for row in grow)
    prow = read_peak_table(Path(by_id["W0002"].peak_table_path("parabolic")))
    assert prow and all(row["localization_method"] == "parabolic" for row in prow)
    # P3-7: the three-point parabola carries its own QC (equivalent linewidth
    # and boundary); only fit_rmse stays Gaussian-only.
    assert prow[0]["fit_success"] is True
    assert prow[0]["FWHM_H"] > 0 and prow[0]["FWHM_N"] > 0
    assert math.isnan(prow[0]["fit_rmse"])

    both = run_parameter_study(
        tmp_path / "localization_both",
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        localization="both",
        edge_margin_ppm=0.5,          # Explicit physical margins (ppm).
        backend=_FakeSweepBackend(),
    )
    run = both.runs[0]
    assert Path(run.peak_table_path("parabolic")).is_file()
    assert Path(run.peak_table_path("gaussian")).is_file()
    assert run.parameters_resolved["detection"]["methods"] == ["parabolic", "gaussian"]
    # Margins specified externally: explicit ppm caliber per workflow file.
    detection = run.parameters_resolved["detection"]
    assert detection["edge_margin_source"] == "ppm_explicit"
    assert detection["edge_margin_ppm"] == pytest.approx(0.5, rel=0.3)


def test_localization_rerun_removes_unselected_run_and_record_tables(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Both -> When rerunning a single method, both running directory and summary directory only
    retain the current selection."""
    root = tmp_path / "localization_cleanup"
    backend = _FakeSweepBackend()
    first = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        localization="both",
        backend=backend,
    )
    run_dir = Path(first.runs[0].run_dir)
    stale_sidecar = run_dir / "peak_table_parabolic.csv.localization.json"
    stale_sidecar.write_text("{}\n", encoding="utf-8")

    gaussian = run_combination_study(
        str(root),
        combos=[{"zero_fill": 1}],
        localization="gaussian",
        resume=False,
        backend=backend,
    )
    run_dir = Path(gaussian.runs[0].run_dir)
    assert (run_dir / "peak_table_gaussian.csv").is_file()
    assert not (run_dir / "peak_table_parabolic.csv").exists()
    assert not stale_sidecar.exists()
    assert "peak_table_gaussian" in gaussian.records
    assert "peak_table_parabolic" not in gaussian.records
    records_dir = gaussian.session.records_dir
    assert not (records_dir / "peak_table_parabolic.csv").exists()
    assert "peak_positions" not in gaussian.records
    assert not (records_dir / "peak_positions.csv").exists()

    parabolic = run_combination_study(
        str(root),
        combos=[{"zero_fill": 1}],
        localization="parabolic",
        resume=False,
        backend=backend,
    )
    run_dir = Path(parabolic.runs[0].run_dir)
    assert (run_dir / "peak_table_parabolic.csv").is_file()
    assert not (run_dir / "peak_table_gaussian.csv").exists()
    assert "peak_table_parabolic" in parabolic.records
    assert "peak_table_gaussian" not in parabolic.records
    assert not (parabolic.session.records_dir / "peak_table_gaussian.csv").exists()


def test_combination_detection_keys_do_not_reach_backend_params(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """The detection class key only affects peak selection: it will never be fed to the backend as
    a processing parameter."""
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        tmp_path / "detection_keys",
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1, "localization": "parabolic"}],
        params={"phase_route": "none"},
        backend=backend,
    )
    run = result.runs[0]
    sent = backend.process_calls[-1]["params"]
    for key in ("localization", "sigma_multiplier", "min_snr", "detection"):
        assert key not in sent
    assert run.parameters_requested["localization"] == "parabolic"
    assert "localization" not in run.parameters_used


class _Fake3DAxes:
    """Minimal 3D spectral axis double: only provides properties that detect_and_localize will
    use."""

    data = np.zeros((4, 4, 4))
    ppm = [np.linspace(0.0, 1.0, 4)] * 3
    nuclei = ["15N", "13C", "1H"]
    logical_to_storage = [0, 1, 2]
    obs = [60.8, 151.0, 600.0]


def test_detect_and_localize_gaussian_rejects_non_2d(tmp_path: Path) -> None:
    """Combination mode: asking for gaussian on non-2D data must raise, never swap method."""
    spectrum = _write_ft2(tmp_path / "ref.ft2")
    with pytest.raises(MeasurementError, match="only for 2D spectra"):
        detect_and_localize(spectrum, method="gaussian", axes=_Fake3DAxes())
    with pytest.raises(MeasurementError, match="unknown peak-localisation method"):
        detect_and_localize(spectrum, method="lorentzian", axes=_Fake3DAxes())


def test_combination_zero_peak_warning_is_explicit(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The combination does not detect a single peak under the locking threshold -> clear warning
    peak_count_zero (not silent when successful)."""
    import nmrforge_api.peaks as api_peaks

    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "zero_peaks", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    plan = plan_sweep(reference, combos=[{"zero_fill": 1}])
    real = api_peaks.detect_and_localize

    def empty_detection(*args, **kwargs):
        rows, meta = real(*args, **kwargs)
        meta = dict(meta)
        meta.update({"n_peaks": 0, "n_fallback": 0, "n_boundary_hit": 0})
        return [], meta

    monkeypatch.setattr(api_peaks, "detect_and_localize", empty_detection)
    runs = run_sweep(session, plan, reference=reference, resume=False)
    run = runs[0]
    assert run.status == STATUS_WARNING
    assert any(w["code"] == "peak_count_zero" for w in run.warnings)
    # The empty peak table is still written (only the table header): the product does not disappear
    # silently.
    assert read_peak_table(Path(run.peak_table_path("parabolic"))) == []
    assert run.parameters_resolved["peak_counts"]["parabolic"] == 0
    assert run.peak_localization["parabolic"]["n_peaks"] == 0


# ---------------------------------- Refer to runtime decision inheritance + workflow script
# retention (2026-09-15).
def test_combination_inherits_reference_runtime_decisions(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Automatic determination of references (such as direct dimension POLY -time) must be
    inherited by the composition; values explicitly given by the composition take precedence."""
    from nmrforge_api.reference import (
        reference_runtime_decisions,
        sanitize_sweep_params,
        save_reference,
    )

    cleaned = sanitize_sweep_params(
        {
            "window": {"F1": {"type": "none"}},
            "phase_route": "unified",
            "diagnostics": {"apply_poly_time": True},
        }
    )
    assert cleaned == {"window": {"F1": {"type": "none"}}, "direct_poly_time": True}
    assert reference_runtime_decisions({}) == {}
    # Not overwritten when the top level has been explicitly given a value.
    assert sanitize_sweep_params(
        {"direct_poly_time": False, "diagnostics": {"apply_poly_time": True}}
    ) == {"direct_poly_time": False}

    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "runtime_decisions", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    # Analog reference peak table creation for old records: Decision only in params.diagnostics.
    reference.params["diagnostics"] = {"apply_poly_time": True}
    reference.sweep_params.pop("direct_poly_time", None)
    save_reference(session, reference)

    plan = plan_sweep(reference, combos=[{"zero_fill": 2}])
    runs = run_sweep(session, plan, reference=reference, resume=False)
    used = runs[0].parameters_used
    assert used["direct_poly_time"] is True      # Reference decisions are inherited.
    assert used["zero_fill"] == 2                # The combination specified still takes effect.

    plan2 = plan_sweep(reference, combos=[{"direct_poly_time": False}])
    runs2 = run_sweep(session, plan2, reference=reference, resume=False)
    assert runs2[0].parameters_used["direct_poly_time"] is False


def test_workflow_script_is_saved_in_run_dir_and_reference_work_dir(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Specification D1: Each workflow stores a complete script; script and reference share the
    working directory of this condition."""
    from core.project.manager import sha256_file

    backend = _FakeSweepBackend()
    result = run_parameter_study(
        tmp_path / "script_saved",
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 2, "window.F1.off": 0.45}],
        params={"phase_route": "none"},
        backend=backend,
    )
    reference = result.reference
    assert reference is not None and reference.work_dir
    work = Path(reference.work_dir)
    assert work.is_dir()
    assert (work / "W0001_A.com").is_file()      # Candidate script same as reference directory.

    run = result.runs[0]
    saved = Path(run.script_path)
    assert saved.is_file()
    assert saved.parent == Path(run.run_dir) and saved.name == "process.com"
    assert run.script_sha256 == sha256_file(saved)
    log = Path(run.log_path).read_text(encoding="utf-8")
    assert "processing script" in log and "W0001_A.com" in log
    payload = json.loads(Path(run.run_dir, "run.json").read_text(encoding="utf-8"))
    assert payload["script_path"] == run.script_path and payload["script_sha256"]


def test_legacy_reference_script_found_in_data_level_dir(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """When the old reference does not have work_dir, the combined script can still be found and
    archived from the data level <data>.nmrpipe."""
    from nmrforge_api.reference import save_reference

    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "legacy_script", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    reference.work_dir = ""  # Emulate old reference (undocumented working directory).
    save_reference(session, reference)

    plan = plan_sweep(reference, combos=[{"zero_fill": 1}])
    runs = run_sweep(session, plan, reference=reference, resume=False)
    run = runs[0]
    assert run.status in (STATUS_SUCCESS, STATUS_WARNING)
    assert Path(run.script_path).is_file()
    dataset = run.dataset
    data_level = (
        session.root
        / str(dataset["exp_id"])
        / str(dataset["data_id"])
        / f"{dataset['data_id']}.nmrpipe"
    )
    assert (data_level / "W0001_A.com").is_file()


def test_missing_workflow_script_is_reported_not_silent(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """When script cannot be found, a processing_script_not_found warning must be given (leave
    blank if not silent)."""
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "script_missing", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    plan = plan_sweep(reference, combos=[{"zero_fill": 1}])

    real_process = backend.process

    def process_with_relocated_script(*args, **kwargs):
        out = real_process(*args, **kwargs)
        name = str(kwargs.get("script_name") or "")
        if name:
            src = Path(str(backend.work_dir)) / name
            if src.is_file():
                src.rename(src.with_name(f"moved_{name}"))
        return out

    backend.process = process_with_relocated_script
    runs = run_sweep(session, plan, reference=reference, resume=False)
    run = runs[0]
    assert run.status == STATUS_WARNING
    assert any(w["code"] == "processing_script_not_found" for w in run.warnings)
    assert run.script_path == "" and run.script_sha256 == ""
    assert Path(run.peak_table_path("parabolic")).is_file()   # The peak table still outputs.


# ---------------------------------- 2026-09-16: baseline rendering caliber + reference optimisation
# switch + effective self-test.
def test_baseline_order_renders_with_auto_flag(bruker_dir: Path, tmp_path: Path) -> None:
    """Mode=order must render ``POLY -ord N -auto``: bare -ord N is identical in NMRPipe."""
    from backend.script_generator import generate_process_script
    from core.planning.method_selector import select_method
    from nmrforge_api import add_dataset, open_study
    from workflow.stepwise import read_experiment

    session = open_study(tmp_path / "poly_render", backend=_FakeSweepBackend())
    add_dataset(session, bruker_dir / "hsqc_2d")
    experiment = read_experiment(session.manager, "exp_001", "d_001")
    plan = select_method(experiment)
    script = generate_process_script(
        experiment,
        plan,
        in_file="d_001.fid",
        out_file="out.ft2",
        baseline={
            "F1": {"enabled": True, "mode": "order", "order": 3},
            "F2": {"enabled": True, "mode": "auto", "order": 0},
        },
    )
    assert "| nmrPipe -fn POLY -ord 3 -auto" in script      # Frequency domain order pattern.
    assert "| nmrPipe -fn POLY -auto \\" in script          # auto Mode hold.
    assert "| nmrPipe -fn POLY -ord 3 \\" not in script     # No nudity allowed -ord.
    off = generate_process_script(
        experiment,
        plan,
        in_file="d_001.fid",
        out_file="out.ft2",
        baseline={"F1": {"enabled": False, "mode": "order", "order": 3},
                  "F2": {"enabled": False, "mode": "auto", "order": 0}},
    )
    assert "POLY -ord" not in off and "POLY -auto" not in off


def test_reference_optimize_switch_is_external_and_recorded(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For reference, optimisation can be turned off with params.reference_optimize (for testing
    only), and it is disabled and does not enter the combined basis."""
    import workflow.baseline_optimize as baseline_optimize

    def explode(*args, **kwargs):
        raise AssertionError(
            "Baseline optimisation should no longer be called after external shutdown"
        )

    monkeypatch.setattr(baseline_optimize, "optimize_baseline", explode)
    backend = _FakeSweepBackend()
    result = run_reference_study(
        tmp_path / "ref_opt_off",
        bruker_dir / "hsqc_2d",
        params={
            "phase_route": "none",
            "baseline": {"F1": {"enabled": False, "mode": "auto", "order": 0}},
            "reference_optimize": {"baseline": "off", "window": "off"},
        },
        backend=backend,
    )
    reference = result.reference()
    assert reference is not None
    # The switch is recorded (auditable), and the baseline given by the caller is used.
    assert reference.params["reference_optimize"] == {
        "baseline": "off", "window": "off",
    }
    assert reference.params["baseline"]["F1"]["enabled"] is False
    # The switch in the reference stage does not belong to the processing parameter and must not
    # enter the combined base.
    assert "reference_optimize" not in reference.sweep_params
    assert Path(reference.script_path).is_file()   # Reference script freezes as usual.


def test_window_subparam_without_type_is_rejected(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """The window parameter must be paired with the window type: when the axis type=none, write
    off/end/... to report an error directly (it was idling the whole time)."""
    from nmrforge_api.reference import save_reference

    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "window_gate", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    reference.sweep_params["window"] = {"F1": {"type": "none"}, "F2": {"type": "none"}}
    save_reference(session, reference)

    with pytest.raises(SweepError, match="cannot take effect"):
        plan_sweep(reference, combos=[{"window.F1.off": 0.35}])
    # Writing type in pairs allows.
    plan = plan_sweep(
        reference,
        combos=[{"window.F1.type": "sine_bell", "window.F1.off": 0.35}],
    )
    assert plan.combos[0]["window.F1.type"] == "sine_bell"
    # Baseline.order and mode≠order -> prompt (not blocking).
    plan2 = plan_sweep(reference, combos=[{"baseline.F1.order": 3}])
    assert any("the order does nothing" in note for note in plan2.notes)


def test_no_spectrum_change_warning_and_script_diff(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Parameter has not changed the score -> no_spectrum_change Warning + script_diff Leave file
    (according to conditions)."""
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "no_change", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    # The fake backend only presses window.F1.off / zero_fill to change the score -> If you don't
    # write these two, it means "the score has not been changed".
    plan = plan_sweep(reference, combos=[{"baseline.F1.order": 3}])
    runs = run_sweep(session, plan, reference=reference, resume=False)
    run = runs[0]
    assert any(w["code"] == "no_spectrum_change" for w in run.warnings)
    assert run.status == STATUS_WARNING
    assert run.script_diff  # There are files left.
    payload = json.loads(Path(run.run_dir, "run.json").read_text(encoding="utf-8"))
    assert payload["script_diff"]

    # Really changed the parameter (fake backend press off to shift the peak position) -> spectrum
    # changes -> no longer report this warning.
    plan2 = plan_sweep(reference, combos=[{"window.F1.off": 0.45, "zero_fill": 2}])
    runs2 = run_sweep(session, plan2, reference=reference, resume=False)
    run2 = runs2[0]
    assert not any(w["code"] == "no_spectrum_change" for w in run2.warnings)
    assert run2.script_diff and run2.script_diff["n_changed"] > 0


# ------------------------------------- Phase 12: Batch failure isolation + requested vs actual.
def test_sweep_failure_is_isolated_and_parameters_recorded(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure of a single workflow does not swallow up other workflows; fail/Stay in both
    categories of success requested/actual. Phase 12 "Batch: failure isolation + requested vs
    actual parameters": The back-end processing of the first workflow throws an error, and the
    second one must continue to run; run.json In run.json, requested is always the user
    parameter as it is, and used is the actual parameter after merging (both types of operations
    must be able to be audited)."""
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "isolation", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    plan = plan_sweep(
        reference, combos=[{"zero_fill.F1": 1}, {"zero_fill.F1": 2}]
    )

    real_process = backend.process
    calls = {"n": 0}

    def flaky_process(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("forced workflow failure")
        return real_process(*args, **kwargs)

    monkeypatch.setattr(backend, "process", flaky_process)
    runs = run_sweep(session, plan, reference=reference, resume=False)

    assert len(runs) == 2
    assert calls["n"] == 2, (
        "After failure, you must continue to execute the subsequent workflow (isolation)"
    )
    failed, ok = runs
    assert failed.status == "failed"
    assert "forced workflow failure" in failed.message
    assert ok.status in ("success", "success_with_warning")

    # Requested = user as is; used = actual parameter after merging (retained even if failed).
    assert failed.parameters_requested == {"zero_fill.F1": 1}
    assert ok.parameters_requested == {"zero_fill.F1": 2}
    assert failed.parameters_used["zero_fill"]["F1"] == 1
    assert ok.parameters_used["zero_fill"]["F1"] == 2

    failed_payload = json.loads(
        Path(failed.run_dir, "run.json").read_text(encoding="utf-8")
    )
    assert failed_payload["status"] == "failed"
    assert failed_payload["parameters_requested"] == {"zero_fill.F1": 1}
    assert failed_payload["parameters_used"]["zero_fill"]["F1"] == 1
    assert not failed.peak_table_path("parabolic")

    ok_payload = json.loads(Path(ok.run_dir, "run.json").read_text(encoding="utf-8"))
    assert ok_payload["status"] in ("success", "success_with_warning")
    assert ok_payload["parameters_used"]["zero_fill"]["F1"] == 2
    assert Path(ok.peak_table_path("parabolic")).is_file()

    # Phase 22: A failed run leaves run.log with traceback; a successful run does not generate an
    # empty log.
    failed_log = Path(failed.run_dir, "run.log")
    assert failed_log.is_file(), "A failed run must leave run.log(logging channel)"
    logged = failed_log.read_text(encoding="utf-8")
    assert "forced workflow failure" in logged and "Traceback" in logged
    assert "run end: status=failed" in logged
    # Phase 22: A successful run also has a copy of run.log(start/End two lines, does not depend on
    # the log level).
    ok_log = Path(ok.run_dir, "run.log")
    assert ok_log.is_file()
    ok_text = ok_log.read_text(encoding="utf-8")
    assert "run start" in ok_text and "run end: status=" in ok_text


# ------------------------------------- Phase 21: user-visible errors (CLI exit)
def test_cli_unexpected_error_is_actionable_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unexpected error gets one actionable line plus a debug channel (Phase 21)."""
    from nmrforge_api.cli import DEBUG_ENV, describe_exception

    bad = tmp_path / "not_a_dir.txt"
    bad.write_text("x", encoding="utf-8")
    assert cli_main(["status", "--study", str(bad)]) == 2
    out = capsys.readouterr().out
    assert "Error:" in out and "Hint:" in out and DEBUG_ENV in out
    assert "Traceback" not in out, "a bare traceback must not reach the user by default"

    # exception -> message map: paths, missing fields and invalid input each say what to do
    assert "File or directory not found" in describe_exception(
        FileNotFoundError(2, "no such file", "x.json")
    )
    assert "Missing required field" in describe_exception(KeyError("peak_id"))
    assert "Invalid input" in describe_exception(ValueError("bad axis spec"))
    # structure mismatch: keep the original text, but always add an explanation
    structure = describe_exception(AttributeError("'NoneType' object has no attribute 'x'"))
    assert structure.startswith("Input does not match the expected structure")


def test_cli_debug_flag_and_env_show_the_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only --debug / NMRFORGE_DEBUG=1 print the traceback; by default, the hint only."""
    from nmrforge_api.cli import DEBUG_ENV

    bad = tmp_path / "not_a_dir.txt"
    bad.write_text("x", encoding="utf-8")

    assert cli_main(["status", "--study", str(bad), "--debug"]) == 2
    captured = capsys.readouterr()
    assert "Full traceback (debug)" in captured.out
    assert "Traceback" in captured.err, "the traceback goes to stderr, not to user stdout"

    monkeypatch.setenv(DEBUG_ENV, "1")
    assert cli_main(["status", "--study", str(bad)]) == 2
    assert "Traceback" in capsys.readouterr().err
