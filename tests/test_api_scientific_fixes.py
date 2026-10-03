"""Regressions for independent, full-dimensional and evidence-based API peaks."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.bruker_workflow import parse_fid_com, patch_fid_com, sweep_width_audit
from backend.nmrpipe_backend import NMRPipeBackend
from backend.script_generator import generate_convert_script, generate_process_script
from core.data.bruker_reader import read_dataset, resolve_sweep_width
from core.data.sweep_width import apply_sweep_width_overrides
from core.planning.method_selector import select_method
from nmrforge_api import MeasurementError, detect_and_localize, measure_peak_positions
from nmrforge_api.peak_tables import peak_table_rows
from nmrforge_api.peaks import PeakMeasurement, read_reference_peaks
from nmrforge_api.processing_audit import ft_processing_audit, validate_ft_options
from workflow.pick_peaks import SpectrumAxes


def _axes(data: np.ndarray, nuclei: list[str], order: list[int] | None = None) -> SpectrumAxes:
    return SpectrumAxes({}, data, [np.arange(n, dtype=float) for n in data.shape], nuclei,
                        order or list(range(data.ndim)), [100.0] * data.ndim)


def _spectrum(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic.ft2"
    path.touch()
    return path


def _gaussian(shape: tuple[int, ...], centers: list[tuple[float, ...]]) -> np.ndarray:
    grid = np.indices(shape, dtype=float)
    return sum(100.0 * np.exp(-sum((grid[a] - x) ** 2 / 8
                                 for a, x in enumerate(center))) for center in centers)


def test_negative_peak_qc_is_polarity_symmetric(tmp_path: Path) -> None:
    data = _gaussian((48, 48), [(22.2, 23.3)])
    outputs = []
    for sign in (1, -1):
        rows, _ = detect_and_localize(_spectrum(tmp_path), axes=_axes(sign * data, ["15N", "1H"]),
                                     sigma_multiplier=3, sign_mode="both")
        assert len(rows) == 1 and rows[0]["fit_success"]
        outputs.append(rows[0])
    for column in ("F1_ppm", "F2_ppm", "FWHM_F1", "FWHM_F2", "SNR"):
        assert outputs[0][column] == pytest.approx(outputs[1][column])
    assert outputs[0]["intensity"] == -outputs[1]["intensity"]


def test_3d_coordinates_keep_distinct_carbon_peaks(tmp_path: Path) -> None:
    data = _gaussian((48, 48, 48), [(12.2, 22.3, 23.1), (35.2, 22.3, 23.1)])
    axes = _axes(data, ["13C", "15N", "1H"], [2, 0, 1])
    rows, _ = detect_and_localize(_spectrum(tmp_path), axes=axes, sigma_multiplier=3)
    assert len(rows) == 2
    assert all(row["F2_nucleus"] == "13C" for row in rows)
    assert abs(rows[0]["F2_ppm"] - rows[1]["F2_ppm"]) > 20
    assert all(not row["duplicate_localization"] for row in rows)
    reference = [{f"F{i}_shift": row[f"F{i}_ppm"] for i in range(1, 4)} for row in rows]
    measured = measure_peak_positions(_spectrum(tmp_path), reference, axes=axes, window_pts=3)
    table = peak_table_rows(measured, workflow_id="reference")
    assert all(row["detected"] for row in table)
    assert all(np.isfinite(row[f"F{i}_ppm"]) for row in table for i in range(1, 4))


def test_repeated_nuclei_use_explicit_logical_axes(tmp_path: Path) -> None:
    data = _gaussian((48, 48), [(22.2, 23.3)])
    axes = _axes(data, ["1H", "1H"])
    rows, _ = detect_and_localize(_spectrum(tmp_path), axes=axes, sigma_multiplier=3)
    assert np.isnan(rows[0]["H_ppm"])
    assert rows[0]["F1_nucleus"] == rows[0]["F2_nucleus"] == "1H"
    measured = measure_peak_positions(_spectrum(tmp_path), [
        {"F1_shift": 22, "F2_shift": 23, "H_shift": 8},
    ], axes=axes, window_pts=3)
    assert measured[0].found
    assert set(measured[0].positions) >= {"F1", "F2"}


def test_targeted_localization_really_leaves_others_on_integer_grid(tmp_path: Path) -> None:
    data = _gaussian((48, 48), [(12.2, 13.3), (32.2, 33.3)])
    axes = _axes(data, ["15N", "1H"])
    path = _spectrum(tmp_path)
    full, _ = detect_and_localize(path, axes=axes, sigma_multiplier=3)
    targeted, _ = detect_and_localize(path, axes=axes, sigma_multiplier=3, targets=[1])
    assert targeted[0]["F1_ppm"] == full[0]["F1_ppm"]
    assert targeted[1]["F1_ppm"] == round(targeted[1]["F1_ppm"])
    assert targeted[1]["F1_ppm"] != full[1]["F1_ppm"]
    assert targeted[1]["localization_method"] == "none"
    with pytest.raises(MeasurementError):
        detect_and_localize(path, axes=axes, sigma_multiplier=3, targets=[])


def test_reference_window_competition_uses_joint_coordinates(tmp_path: Path) -> None:
    data = _gaussian((64, 64), [(22, 20), (21, 45)])
    measured = measure_peak_positions(_spectrum(tmp_path), [
        {"N_shift": 20, "H_shift": 20}, {"N_shift": 21, "H_shift": 45},
    ], axes=_axes(data, ["15N", "1H"]), window_pts=4)
    assert all(item.found for item in measured)
    assert measured[0].positions["15N"] == pytest.approx(22)
    assert measured[0].intensity == pytest.approx(100)


def test_zero_and_subthreshold_windows_are_not_detected(tmp_path: Path) -> None:
    for data in (np.zeros((48, 48)), _gaussian((48, 48), [(22, 23)]) * 0.001):
        measured = measure_peak_positions(_spectrum(tmp_path), [{"N_shift": 22, "H_shift": 23}],
                                         axes=_axes(data, ["15N", "1H"]), window_pts=3,
                                         noise_sigma=1)
        assert not measured[0].found and not measured[0].positions
        exported = peak_table_rows(measured, workflow_id="reference")[0]
        assert exported["localization_requested"] == "parabolic"
        assert exported["localization_method"] == "none"
        assert exported["failure_reason"] == "no_local_peak_above_threshold"
        assert exported["fallback"] is False
        row = peak_table_rows(measured, workflow_id="reference")[0]
        assert row["detected"] is False
        assert np.isnan(row["F1_ppm"])


def test_sweep_width_explicit_override_reaches_conversion_and_invalidates_cache(
    tmp_path: Path, bruker_dir: Path,
) -> None:
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    original = experiment.dimensions[1].sw
    changed = apply_sweep_width_overrides(experiment, {"sweep_width_hz": {"F1": 1750}})
    assert experiment.dimensions[1].sw == original
    script = generate_convert_script(changed)
    assert float(parse_fid_com(script)["ySW"]) == pytest.approx(1750)
    patched, _ = patch_fid_com(script.replace("1750", "2000"), changed)
    assert float(parse_fid_com(patched)["ySW"]) == pytest.approx(1750)
    entries = sweep_width_audit(changed)
    assert len(entries) == 2
    assert next(e for e in entries if e["axis"] == "F1")["source"] == "explicit_hz"
    assert resolve_sweep_width(30, 2000, 60)[0] == 1800
    backend = NMRPipeBackend(work_dir=str(tmp_path))
    (tmp_path / f"{experiment.dataset_id}.fid").write_bytes(b"valid")
    backend._record_conversion(tmp_path, experiment.dataset_id, experiment.source_path, [],
                               experiment=experiment)
    assert not backend._converted_fid_is_current(tmp_path, experiment.dataset_id,
                                                experiment.source_path, [], experiment=changed)
    for value in (0, -1, float("nan"), True):
        with pytest.raises(ValueError):
            apply_sweep_width_overrides(experiment, {"sweep_width_hz": {"F1": value}})


def test_two_candidates_differ_only_in_ft_neg_and_keep_command_evidence(
    tmp_path: Path, bruker_dir: Path,
) -> None:
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    scripts = []
    for neg in (False, True):
        params = {"sampling": {"ft_neg_f1": neg}}
        script = generate_process_script(experiment, select_method(experiment),
                                         in_file="test.fid", out_file="test.ft2",
                                         sampling=params["sampling"])
        scripts.append(script)
        path = tmp_path / f"{neg}.com"
        path.write_text(script, encoding="utf-8")
        audit = ft_processing_audit(experiment, params, path)
        assert audit["requested"]["ft_neg_f1"] is neg
        assert audit["resolved"]["F1"]["neg"] is neg
        assert audit["ft_commands"]
    differences = [
        (a, b) for a, b in zip(scripts[0].splitlines(), scripts[1].splitlines()) if a != b
    ]
    assert len(differences) == 1
    assert "-fn FT" in differences[0][0]
    assert differences[0][0].replace(" -neg", "") == differences[0][1].replace(" -neg", "")
    with pytest.raises(ValueError):
        validate_ft_options({"sampling": {"ft_neg_f1": "false"}})


def test_poky_reference_coordinates_follow_the_actual_axis_order(tmp_path: Path) -> None:
    path = tmp_path / "reference.list"
    path.write_text("Assignment w1 w2 w3 Data Height Volume\n?-?-? 120 55 8 0 100 0\n",
                    encoding="utf-8")
    axes = _axes(np.zeros((48, 48, 48)), ["1H", "15N", "13C"], [2, 0, 1])
    rows = read_reference_peaks(path, axes=axes)
    assert float(rows[0]["F1_shift"]) == 55
    assert float(rows[0]["F2_shift"]) == 8
    assert float(rows[0]["F3_shift"]) == 120


def test_missing_identity_stays_empty_when_measurements_reload() -> None:
    measurement = PeakMeasurement.from_dict({"peak_id": 1, "assignment": None,
                                            "reference_peak_id": ""})
    assert measurement.assignment == measurement.reference_peak_id == ""


def test_empty_identity_and_unknown_boolean_survive_csv_roundtrip(tmp_path: Path) -> None:
    from nmrforge_api.peak_tables import read_peak_table, write_peak_table

    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    write_peak_table(first, [{"assignment": float("nan"), "reference_peak_id": "NaN",
                              "fit_success": float("nan"), "detected": True}])
    rows = read_peak_table(first)
    assert rows[0]["assignment"] == rows[0]["reference_peak_id"] == ""
    assert np.isnan(rows[0]["fit_success"])
    write_peak_table(second, rows)
    assert np.isnan(read_peak_table(second)[0]["fit_success"])


def test_legacy_reference_contract_must_be_rebuilt(tmp_path: Path) -> None:
    from nmrforge_api import ReferenceError
    from nmrforge_api.reference import ReferenceSpectrum, validate_reference_peak_contract

    reference = ReferenceSpectrum(dataset_key="test", exp_id="exp", data_id="data")
    reference.peak_source = "shared:A"
    with pytest.raises(ReferenceError, match="shared"):
        validate_reference_peak_contract(reference)
    reference.peak_source = "auto"
    table = tmp_path / "old.csv"
    table.write_text("H_ppm,N_ppm\n8,120\n", encoding="utf-8")
    reference.peak_tables = {"parabolic": {"path": str(table)}}
    with pytest.raises(ReferenceError, match="H/N-only"):
        validate_reference_peak_contract(reference)


@pytest.mark.parametrize("target", [True, 1.5, float("nan")])
def test_target_ids_cannot_be_silently_coerced(tmp_path: Path, target: object) -> None:
    axes = _axes(_gaussian((48, 48), [(22.2, 23.3)]), ["15N", "1H"])
    with pytest.raises(MeasurementError, match="integers"):
        detect_and_localize(_spectrum(tmp_path), axes=axes, sigma_multiplier=3, targets=[target])


def test_per_condition_inputs_are_independent_and_timings_include_peak_work(
    tmp_path: Path, bruker_dir: Path,
) -> None:
    import shutil

    from test_nmrforge_api import _FakeSweepBackend

    from nmrforge_api import run_combination_study, run_reference_study

    second = tmp_path / "second"
    shutil.copytree(bruker_dir / "hsqc_2d", second)
    backend = _FakeSweepBackend()
    result = run_reference_study(
        tmp_path / "study", datasets={"A": bruker_dir / "hsqc_2d", "B": second},
        params={"phase_route": "none", "sampling": {"auto_phase": False}},
        params_by_condition={"A": {"sampling": {"ft_neg_f1": False}},
                             "B": {"sampling.ft_neg_f1": True, "window.F1.off": 0.55}},
        backend=backend,
    )
    references = {ref.condition: ref for ref in result.references.values()}
    assert all(ref.peak_source == "auto" for ref in references.values())
    assert references["A"].peak_table_sha256 != references["B"].peak_table_sha256
    assert references["A"].params["sampling"]["ft_neg_f1"] is False
    assert references["B"].params["sampling"]["ft_neg_f1"] is True
    assert references["B"].params["sampling"]["auto_phase"] is False
    assert set(references["A"].stage_times_s) >= {
        "conversion", "processing", "peak_detection", "peak_measurement_export",
    }
    runs = run_combination_study(str(result.session.root),
                                 combos=[{"sampling.ft_neg_f1": False},
                                         {"sampling.ft_neg_f1": True}], backend=backend).runs
    assert len(runs) == 4
    for run in runs:
        assert run.stage_times_s["total"] == run.wall_time_s
        assert run.wall_time_s >= sum(run.stage_times_s[k]
                                     for k in ("processing", "detection_localization"))
        assert run.parameters_resolved["sampling"]["flags_source"] == "combo"
