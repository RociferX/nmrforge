"""Targeted localization (2026-09-19 request): refine only the named peaks.

Acceptance (given by the user):

- **equivalence**: on the same workflow, targeted and whole-spectrum agree per peak on
    ``H_ppm``/``N_ppm`` (1e-9) and on ``fit_success``/fallback reason for **the same peaks**;
- **detection is unchanged**: row count and ``peak_id`` numbering stay, and unlisted peaks
  keep the detection-stage parabola position;
- **record**: ``run.json`` keeps the target origin (path + sha256 + peak count) plus
  n_targeted/n_skipped; **errors**: an empty list / a missing file / an unknown ``peak_id``
  / a missing ``peak_id`` column all raise; **default**: no targets = whole spectrum,
    unchanged behaviour and record (``scope=all``); **condition granularity** (2026-09-20):
    with a ``condition`` column or mapping each condition reads only its own ids, and a
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from nmrforge_api import (
    MeasurementError,
    SweepError,
    detect_and_localize,
    load_combo_table,
    read_peak_table,
    run_combination_study,
    run_parameter_study,
)
from nmrforge_api.cli import build_parser
from nmrforge_api.compat import behavior_digest, compat_status
from nmrforge_api.localization_targets import (
    read_localization_targets,
    resolve_conditional_targets,
    resolve_localization_targets,
)

# synthetic spectrum geometry: data axis 0 = indirect (15N, 64 points), axis 1 = direct
_N15_OBS, _N15_SW, _N15_CAR, _N15_SIZE = 60.8, 2000.0, 118.0, 64
_H1_OBS, _H1_SW, _H1_CAR, _H1_SIZE = 600.0, 6000.0, 4.7, 128
_PEAKS = ((30, 60), (45, 90), (18, 100))


def _write_ft2(path: Path, *, shift_y: float = 0.0, shift_x: float = 0.0) -> Path:
    """Write a 2D spectrum nmrglue can read: 3 strong peaks + fixed-seed noise."""
    from nmrglue.fileio import pipe

    shape = (_N15_SIZE, _H1_SIZE)
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    arr = np.zeros(shape, dtype=float)
    for index, (y, x) in enumerate(_PEAKS, start=1):
        cy, cx = y + shift_y, x + shift_x
        arr += (140.0 - 20.0 * (index - 1)) * np.exp(
            -(((yy - cy) ** 2) / (2 * 1.2**2) + ((xx - cx) ** 2) / (2 * 1.4**2))
        )
    rng = np.random.default_rng(20260919)
    arr = gaussian_filter(arr, sigma=0.5) + rng.normal(0.0, 0.4, shape)
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


class _FakeBackend:
    """Minimal backend double: writes spectra deterministically (as test_nmrforge_api)."""

    def __init__(self) -> None:
        self.work_dir = ""
        self.process_calls: list[dict] = []

    def _work(self) -> Path:
        path = Path(self.work_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def convert_to_fid(self, experiment, data_dir, progress=None, **_kwargs) -> dict:
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
        self.process_calls.append({"params": params})
        work = self._work()
        script = work / (script_name or f"{experiment.dataset_id}_process.com")
        script.write_text("#!/bin/csh\n", encoding="utf-8")
        target = work / (out_file or f"{experiment.dataset_id}.ft2")
        if out_file:
            target = work / "_intermediate" / out_file
        _write_ft2(target)
        return {
            "success": True,
            "message": "ok",
            "spectrum_path": str(target),
            "logs": ["fake process"],
            "effective_params": {},
        }


def _gaussian_rows(spectrum: Path, **kwargs):
    return detect_and_localize(spectrum, method="gaussian", sigma_multiplier=20, **kwargs)


# --------------------------------------------------------------- unit/integration layer
def test_targeted_matches_full_spectrum_on_the_same_peaks(tmp_path: Path) -> None:
    """Equivalence (core acceptance): targets agree per peak, unlisted keep the parabola."""
    spectrum = _write_ft2(tmp_path / "spec.ft2")
    full, full_meta = _gaussian_rows(spectrum)
    ids = [row["peak_id"] for row in full]
    assert len(ids) >= 3, f"the synthetic spectrum should detect at least 3 peaks, found {len(ids)}"
    assert ids == list(range(1, len(ids) + 1))
    assert full_meta["localization_scope"] == "all"
    assert full_meta["n_skipped"] == 0

    targets = (ids[0], ids[-1])
    sub, sub_meta = _gaussian_rows(spectrum, targets=targets)
    assert [row["peak_id"] for row in sub] == ids  # detection and numbering unchanged
    full_by_id = {row["peak_id"]: row for row in full}
    for row in sub:
        reference = full_by_id[row["peak_id"]]
        if row["peak_id"] in targets:
            assert abs(row["H_ppm"] - reference["H_ppm"]) <= 1e-9
            assert abs(row["N_ppm"] - reference["N_ppm"]) <= 1e-9
            assert bool(row["fit_success"]) == bool(reference["fit_success"])
            assert row["fallback_reason"] == reference["fallback_reason"]
        else:
            assert row["fit_success"] is None
            assert row["FWHM_H"] is None and row["FWHM_N"] is None
            assert row["fit_rmse"] is None
            assert row["fallback"] is False

    parabolic, _ = detect_and_localize(
        spectrum, method="parabolic", sigma_multiplier=20
    )
    par_by_id = {row["peak_id"]: row for row in parabolic}
    for row in sub:
        if row["peak_id"] in targets:
            continue
        assert row["H_ppm"] == par_by_id[row["peak_id"]]["H_ppm"]
        assert row["N_ppm"] == par_by_id[row["peak_id"]]["N_ppm"]

    assert sub_meta["n_peaks"] == len(ids)
    assert sub_meta["localization_scope"] == "subset"
    assert sub_meta["n_targeted"] == len(targets)
    assert sub_meta["n_skipped"] == len(ids) - len(targets)
    assert sub_meta["targeted_peak_ids"] == sorted(targets)


def test_target_list_errors_are_explicit(tmp_path: Path) -> None:
    """Empty list / missing file / missing column / bad id / unknown peak_id never pass silently."""
    with pytest.raises(SweepError, match="does not exist"):
        read_localization_targets(tmp_path / "missing.csv")
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(SweepError, match="is empty"):
        read_localization_targets(empty)
    no_column = tmp_path / "no_column.csv"
    no_column.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(SweepError, match="has no peak_id column"):
        read_localization_targets(no_column)
    bad_id = tmp_path / "bad_id.csv"
    bad_id.write_text("peak_id\nR0001\n", encoding="utf-8")
    with pytest.raises(SweepError, match="not a positive integer"):
        read_localization_targets(bad_id)
    with pytest.raises(SweepError, match="is empty"):
        resolve_localization_targets([])
    spectrum = _write_ft2(tmp_path / "spec.ft2")
    with pytest.raises(MeasurementError, match="were not detected"):
        _gaussian_rows(spectrum, targets=(9999,))


def test_target_sources_accept_paths_ids_and_mappings(tmp_path: Path) -> None:
    """CSV (dedup, order kept, reference ids) / peak numbers / mappings work without glue."""
    csv = tmp_path / "targets.csv"
    csv.write_text(
        "peak_id,reference_peak_id\n3,R0003\n1,R0001\n3,R0003\n", encoding="utf-8"
    )
    parsed = read_localization_targets(csv)
    assert parsed.peak_ids == (3, 1)
    assert parsed.reference_peak_ids == {3: "R0003", 1: "R0001"}
    assert len(parsed.sha256) == 64
    assert parsed.scope == "subset"
    assert resolve_localization_targets(csv).peak_ids == (3, 1)
    assert resolve_localization_targets([2, 2, 5]).peak_ids == (2, 5)
    assert resolve_localization_targets({"peak_ids": [4]}).peak_ids == (4,)
    assert resolve_localization_targets(None) is None
    single = tmp_path / "single.txt"
    single.write_text("peak_id\n7\n9\n", encoding="utf-8")
    assert read_localization_targets(single).peak_ids == (7, 9)


def test_per_method_targets_limit_only_that_method(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """A per-method key restricts only the named method (parabolic stays whole-spectrum)."""
    root = tmp_path / "per_method"
    backend = _FakeBackend()
    dataset = bruker_dir / "hsqc_2d"
    probe = run_parameter_study(
        root,
        dataset,
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        localization="both",
        backend=backend,
    )
    rows = read_peak_table(Path(probe.runs[0].peak_table_path("gaussian")))
    assert len(rows) >= 2
    target_id = int(rows[0]["peak_id"])
    targets = tmp_path / "gaussian_targets.csv"
    targets.write_text(f"peak_id\n{target_id}\n", encoding="utf-8")
    result = run_combination_study(
        str(root),
        combos=[{"zero_fill": 1}],
        localization="both",
        localize_peaks={"gaussian": targets},
        resume=False,
        backend=backend,
    )
    run = result.runs[0]
    record = run.parameters_resolved["detection"]["localization_targets"]
    assert record["scope"] == "mixed"
    assert record["by_method"]["gaussian"]["scope"] == "subset"
    assert record["by_method"]["gaussian"]["n_targets"] == 1
    assert record["by_method"]["parabolic"]["scope"] == "all"
    assert record["by_method"]["parabolic"]["n_skipped"] == 0
    assert run.peak_localization["gaussian"]["n_skipped"] == len(rows) - 1
    assert run.peak_localization["parabolic"]["n_skipped"] == 0
    grow = read_peak_table(Path(run.peak_table_path("gaussian")))
    prow = read_peak_table(Path(run.peak_table_path("parabolic")))
    for row in grow:
        if row["peak_id"] == target_id:
            assert not math.isnan(row["fit_success"])
        else:
            assert math.isnan(row["fit_success"])
    assert all(not math.isnan(row["fit_success"]) for row in prow)


def test_combo_table_per_method_target_key(tmp_path: Path, bruker_dir: Path) -> None:
    """Combination table localization.targets.<method>: restricts that method, origin is combo."""
    root = tmp_path / "combo_per_method"
    backend = _FakeBackend()
    probe = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        localization="gaussian",
        backend=backend,
    )
    rows = read_peak_table(Path(probe.runs[0].peak_table_path("gaussian")))
    assert rows
    target_id = int(rows[-1]["peak_id"])
    targets = tmp_path / "gaussian_targets.csv"
    targets.write_text(f"peak_id\n{target_id}\n", encoding="utf-8")
    combos_csv = tmp_path / "combos_per_method.csv"
    combos_csv.write_text(
        "zero_fill,localization,localization.targets.gaussian\n"
        "1,gaussian,gaussian_targets.csv\n",
        encoding="utf-8",
    )
    combos = load_combo_table(combos_csv)
    assert combos[0]["localization.targets.gaussian"].endswith("gaussian_targets.csv")
    result = run_combination_study(
        str(root), combos=combos, resume=False, backend=backend
    )
    run = result.runs[0]
    record = run.parameters_resolved["detection"]["localization_targets"]
    assert record["scope"] == "subset"
    assert record["source"] == "combo"
    assert record["by_method"]["gaussian"]["n_targets"] == 1


def test_cli_sweep_wires_the_localize_peaks_option() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["sweep", "--study", "study", "--reference", "study", "--grid", "g.yaml"]
    )
    assert args.localize_peaks is None
    args = parser.parse_args(
        [
            "sweep",
            "--study",
            "study",
            "--reference",
            "study",
            "--grid",
            "g.yaml",
            "--localize-peaks",
            "targets.csv",
        ]
    )
    assert args.localize_peaks == "targets.csv"
    args = parser.parse_args(
        [
            "sweep",
            "--study",
            "study",
            "--reference",
            "study",
            "--grid",
            "g.yaml",
            "--localize-peaks-gaussian",
            "g.csv",
        ]
    )
    assert args.localize_peaks_gaussian == "g.csv"
    assert args.localize_peaks is None


def test_targeted_run_records_sources_and_matches_full_run(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Same workflow: targeted and whole-spectrum agree per peak, run.json records everything."""
    root = tmp_path / "targeted_run"
    backend = _FakeBackend()
    full = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        localization="gaussian",
        backend=backend,
    )
    full_run = full.runs[0]
    full_rows = read_peak_table(Path(full_run.peak_table_path("gaussian")))
    assert len(full_rows) >= 2
    assert (
        full_run.parameters_resolved["detection"]["localization_targets"]["scope"]
        == "all"
    )
    target_id = int(full_rows[0]["peak_id"])
    csv = tmp_path / "peak_targets.csv"
    csv.write_text(
        f"peak_id,reference_peak_id\n{target_id},R{target_id:04d}\n", encoding="utf-8"
    )

    targeted = run_combination_study(
        str(root),
        combos=[{"zero_fill": 1}],
        localization="gaussian",
        localize_peaks=csv,
        resume=False,
        backend=backend,
    )
    run = targeted.runs[0]
    record = run.parameters_resolved["detection"]["localization_targets"]
    assert record["scope"] == "subset"
    assert Path(record["path"]).resolve() == csv.resolve()
    assert record["sha256"] == hashlib.sha256(csv.read_bytes()).hexdigest()
    assert record["n_targets"] == 1
    assert record["peak_ids"] == [target_id]
    summary = run.peak_localization["gaussian"]
    assert summary["localization_scope"] == "subset"
    run_payload = json.loads(
        (Path(run.run_dir) / "run.json").read_text(encoding="utf-8")
    )
    assert run_payload["behavior_digest"] == behavior_digest()
    assert run_payload["compat_level"] == compat_status()["compat_level"]
    reference_payload = json.loads(
        (targeted.session.records_dir / "reference.json").read_text(
            encoding="utf-8"
        )
    )
    assert reference_payload["behavior_digest"] == behavior_digest()
    assert "compat_affected" in reference_payload
    assert summary["n_targeted"] == 1
    assert summary["n_skipped"] == len(full_rows) - 1

    rows = read_peak_table(Path(run.peak_table_path("gaussian")))
    assert len(rows) == len(full_rows)
    full_by_id = {row["peak_id"]: row for row in full_rows}
    for row in rows:
        reference = full_by_id[row["peak_id"]]
        if row["peak_id"] == target_id:
            assert abs(row["H_ppm"] - reference["H_ppm"]) <= 1e-9
            assert abs(row["N_ppm"] - reference["N_ppm"]) <= 1e-9
            assert bool(row["fit_success"]) == bool(reference["fit_success"])
            assert row["fallback_reason"] == reference["fallback_reason"]
        else:
            assert math.isnan(row["fit_success"])


def test_changing_the_target_file_content_invalidates_the_resume_cache(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """The fingerprint carries the resolved targets: same path, new content -> re-run."""
    root = tmp_path / "targets_fingerprint"
    backend = _FakeBackend()
    dataset = bruker_dir / "hsqc_2d"
    targets = tmp_path / "peak_targets.csv"
    targets.write_text("peak_id\n1\n", encoding="utf-8")
    first = run_parameter_study(
        root,
        dataset,
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        localization="gaussian",
        localize_peaks=targets,
        backend=backend,
    )
    record = first.runs[0].parameters_resolved["detection"][
        "localization_targets"
    ]
    assert record["n_targets"] == 1

    targets.write_text("peak_id\n1\n2\n", encoding="utf-8")
    second = run_combination_study(
        str(root),
        combos=[{"zero_fill": 1}],
        localization="gaussian",
        localize_peaks=targets,
        backend=backend,
    )
    record = second.runs[0].parameters_resolved["detection"][
        "localization_targets"
    ]
    assert record["n_targets"] == 2
    assert record["peak_ids"] == [1, 2]


def test_combo_table_key_resolves_relative_target_path(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """A relative path in the combination table resolves against the table's directory."""
    root = tmp_path / "combo_targets"
    backend = _FakeBackend()
    first = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        localization="gaussian",
        backend=backend,
    )
    rows = read_peak_table(Path(first.runs[0].peak_table_path("gaussian")))
    assert rows
    peak_id = int(rows[-1]["peak_id"])
    targets = tmp_path / "peak_targets.csv"
    targets.write_text(f"peak_id\n{peak_id}\n", encoding="utf-8")
    combos_csv = tmp_path / "combos.csv"
    combos_csv.write_text(
        "zero_fill,localization,localization.targets\n1,gaussian,peak_targets.csv\n",
        encoding="utf-8",
    )
    combos = load_combo_table(combos_csv)
    assert Path(combos[0]["localization.targets"]).resolve() == targets.resolve()

    result = run_combination_study(
        str(root), combos=combos, resume=False, backend=backend
    )
    run = result.runs[0]
    assert run.parameters_resolved["detection"]["methods"] == ["gaussian"]
    record = run.parameters_resolved["detection"]["localization_targets"]
    assert record["scope"] == "subset"
    assert record["source"] == "combo"
    assert record["n_targets"] == 1
    assert Path(record["path"]).resolve() == targets.resolve()

# ----------------------------------------------------- condition granularity (2026-09-20)
def _condition_csv(
    path: Path, rows: Sequence[tuple[str, int]], *, header: str = "condition,peak_id"
) -> Path:
    body = "\n".join(f"{name},{peak_id}" for name, peak_id in rows)
    path.write_text(f"{header}\n{body}\n", encoding="utf-8")
    return path


def _ab_study(tmp_path: Path, bruker_dir: Path, backend: _FakeBackend):
    """A two-condition (A/B) study with the reference built: each condition has its own table."""
    return run_parameter_study(
        tmp_path / "ab",
        datasets={"A": bruker_dir / "hsqc_2d", "B": bruker_dir / "hsqc_small"},
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        localization="gaussian",
        backend=backend,
    )


def _detected_ids(result) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for run in result.runs:
        rows = read_peak_table(Path(run.peak_table_path("gaussian")))
        out.setdefault(run.condition, [int(row["peak_id"]) for row in rows])
    return out


def _rerun(root: Path, backend: _FakeBackend, **kwargs):
    return run_combination_study(
        str(root),
        combos=[{"zero_fill": 1}],
        localization="gaussian",
        backend=backend,
        **kwargs,
    )


def test_condition_column_targets_each_condition_separately(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """A and B share one CSV: each condition reads its rows and the per-run counts are right."""
    backend = _FakeBackend()
    probe = _ab_study(tmp_path, bruker_dir, backend)
    ids = _detected_ids(probe)
    assert len(ids["A"]) >= 2 and len(ids["B"]) >= 3
    expect = {"A": [ids["A"][0]], "B": [ids["B"][0], ids["B"][1]]}
    targets = _condition_csv(
        tmp_path / "targets.csv", [("A", expect["A"][0]), *[("B", i) for i in expect["B"]]]
    )
    result = _rerun(probe.session.root, backend, localize_peaks=targets, resume=False)
    runs = {run.condition: run for run in result.runs}
    assert set(runs) == {"A", "B"}
    for name, run in runs.items():
        summary = run.peak_localization["gaussian"]
        assert summary["localization_scope"] == "subset"
        assert summary["n_targeted"] == len(expect[name])
        assert summary["n_skipped"] == len(ids[name]) - len(expect[name])
        record = run.parameters_resolved["detection"]["localization_targets"]
        assert record["scope"] == "subset"
        assert record["peak_ids"] == expect[name]
        assert Path(record["path"]).resolve() == targets.resolve()
        assert record["condition"] == name
        assert record["on_missing"] == "error"
        assert record["by_condition"]["A"]["peak_ids"] == expect["A"]
        assert record["by_condition"]["B"]["peak_ids"] == expect["B"]
        assert record["by_condition"][name]["from"] == "rows"
        rows = read_peak_table(Path(run.peak_table_path("gaussian")))
        assert len(rows) == len(ids[name])  # detection and row count unchanged
        refined = {
            int(row["peak_id"])
            for row in rows
            if not math.isnan(row["fit_success"])
        }
        assert refined == set(expect[name])
    # line ranges: after the header (line 1) A occupies line 2 and B occupies lines 3-4
    record = runs["A"].parameters_resolved["detection"]["localization_targets"]
    assert record["by_condition"]["A"]["line_ranges"] == [[2, 2]]
    assert record["by_condition"]["B"]["line_ranges"] == [[3, 4]]
    assert record["by_condition"]["A"]["n_targets"] == 1
    assert record["by_condition"]["B"]["n_targets"] == 2


def test_condition_column_absent_keeps_batch_wide_targets(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Without a condition column -> bit-for-bit as before, recorded as by_condition="all"."""
    backend = _FakeBackend()
    probe = _ab_study(tmp_path, bruker_dir, backend)
    ids = _detected_ids(probe)
    targets = tmp_path / "batch_targets.csv"
    targets.write_text(f"peak_id\n{ids['A'][0]}\n", encoding="utf-8")
    result = _rerun(probe.session.root, backend, localize_peaks=targets, resume=False)
    for run in result.runs:
        summary = run.peak_localization["gaussian"]
        assert summary["localization_scope"] == "subset"
        assert summary["n_targeted"] == 1
        assert summary["n_skipped"] == len(ids[run.condition]) - 1
        record = run.parameters_resolved["detection"]["localization_targets"]
        assert record["scope"] == "subset"
        assert record["peak_ids"] == [ids["A"][0]]
        assert record["by_condition"] == "all"
        assert "condition" not in record
        assert record["by_method"]["gaussian"]["by_condition"] == "all"


def test_condition_targets_error_before_processing(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Missing rows / unknown condition names / empty lists fail **before processing**."""
    backend = _FakeBackend()
    probe = _ab_study(tmp_path, bruker_dir, backend)
    ids = _detected_ids(probe)
    before = len(backend.process_calls)
    only_a = _condition_csv(tmp_path / "only_a.csv", [("A", ids["A"][0])])
    with pytest.raises(SweepError, match="has no rows for condition 'B'"):
        _rerun(probe.session.root, backend, localize_peaks=only_a, resume=False)
    assert len(backend.process_calls) == before
    unknown = _condition_csv(
        tmp_path / "unknown.csv", [("A", ids["A"][0]), ("C", ids["B"][0])]
    )
    with pytest.raises(SweepError, match="does not belong to the study"):
        _rerun(probe.session.root, backend, localize_peaks=unknown, resume=False)
    assert len(backend.process_calls) == before
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(SweepError, match="is empty"):
        _rerun(probe.session.root, backend, localize_peaks=empty, resume=False)
    assert len(backend.process_calls) == before
    # a target list on a combination-table key fails before processing too (paths resolve there)
    combos_csv = tmp_path / "combos.csv"
    combos_csv.write_text(
        "zero_fill,localization,localization.targets\n1,gaussian,only_a.csv\n",
        encoding="utf-8",
    )
    with pytest.raises(SweepError, match="has no rows for condition 'B'"):
        run_combination_study(
            str(probe.session.root),
            combos=load_combo_table(combos_csv),
            resume=False,
            backend=backend,
        )
    assert len(backend.process_calls) == before
    # a mapping that misses a condition (and gives no default) raises as well
    b_only = tmp_path / "b.csv"
    b_only.write_text(f"peak_id\n{ids['B'][0]}\n", encoding="utf-8")
    with pytest.raises(SweepError, match="has no rows for condition 'A'"):
        _rerun(
            probe.session.root,
            backend,
            localize_peaks={"B": b_only},
            resume=False,
        )
    assert len(backend.process_calls) == before


@pytest.mark.parametrize(
    ("policy", "scope", "origin"),
    [("none", "none", "on_missing=none"), ("all", "all", "on_missing=all")],
)
def test_condition_on_missing_policies_are_explicit(
    tmp_path: Path, bruker_dir: Path, policy: str, scope: str, origin: str
) -> None:
    """Letting a missing row through needs an explicit all/none, recorded with its origin."""
    backend = _FakeBackend()
    probe = _ab_study(tmp_path, bruker_dir, backend)
    ids = _detected_ids(probe)
    only_a = _condition_csv(tmp_path / "only_a.csv", [("A", ids["A"][0])])
    result = _rerun(
        probe.session.root,
        backend,
        localize_peaks={"path": str(only_a), "on_missing": policy},
        resume=False,
    )
    runs = {run.condition: run for run in result.runs}
    summary_a = runs["A"].peak_localization["gaussian"]
    assert summary_a["localization_scope"] == "subset"
    assert summary_a["n_targeted"] == 1
    summary_b = runs["B"].peak_localization["gaussian"]
    assert summary_b["localization_scope"] == scope
    record_b = runs["B"].parameters_resolved["detection"]["localization_targets"]
    assert record_b["on_missing"] == policy
    assert record_b["by_condition"]["B"]["from"] == origin
    if policy == "none":
        assert summary_b["n_targeted"] == 0
        assert summary_b["n_skipped"] == len(ids["B"])
        assert record_b["scope"] == "none"
        assert record_b["peak_ids"] == []
    else:
        assert summary_b["n_targeted"] == len(ids["B"])
        assert summary_b["n_skipped"] == 0
        assert record_b["scope"] == "all"


def test_condition_mapping_files_and_default(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """One file per condition and the ``default`` + ``by_condition`` mapping both work."""
    backend = _FakeBackend()
    probe = _ab_study(tmp_path, bruker_dir, backend)
    ids = _detected_ids(probe)
    a_csv = tmp_path / "a.csv"
    a_csv.write_text(f"peak_id\n{ids['A'][0]}\n", encoding="utf-8")
    b_csv = tmp_path / "b.csv"
    b_csv.write_text(
        f"peak_id\n{ids['B'][0]}\n{ids['B'][1]}\n", encoding="utf-8"
    )
    result = _rerun(
        probe.session.root,
        backend,
        localize_peaks={"A": a_csv, "B": b_csv},
        resume=False,
    )
    runs = {run.condition: run for run in result.runs}
    assert runs["A"].peak_localization["gaussian"]["n_targeted"] == 1
    assert runs["B"].peak_localization["gaussian"]["n_targeted"] == 2
    record = runs["B"].parameters_resolved["detection"]["localization_targets"]
    assert Path(record["path"]).resolve() == b_csv.resolve()
    assert record["by_condition"]["A"]["path"] == str(a_csv)
    assert record["by_condition"]["B"]["path"] == str(b_csv)
    assert record["by_condition"]["A"]["from"] == "mapping"
    assert record["by_condition"]["A"]["n_targets"] == 1
    assert record["by_condition"]["B"]["n_targets"] == 2

    default = tmp_path / "default.csv"
    default.write_text(f"peak_id\n{ids['A'][0]}\n", encoding="utf-8")
    result2 = _rerun(
        probe.session.root,
        backend,
        localize_peaks={"default": default, "by_condition": {"B": b_csv}},
        resume=False,
    )
    runs2 = {run.condition: run for run in result2.runs}
    assert runs2["A"].peak_localization["gaussian"]["n_targeted"] == 1
    assert runs2["B"].peak_localization["gaussian"]["n_targeted"] == 2
    record2 = runs2["A"].parameters_resolved["detection"]["localization_targets"]
    assert record2["by_condition"]["B"]["from"] == "mapping"
    assert record2["by_condition"]["A"]["from"] == "default"
    assert Path(record2["by_condition"]["A"]["path"]).name == "default.csv"


def test_combo_table_cell_condition_mapping(tmp_path: Path, bruker_dir: Path) -> None:
    """A combination-table cell ``{A: a.csv, B: b.csv}``: relative paths resolve there."""
    backend = _FakeBackend()
    probe = _ab_study(tmp_path, bruker_dir, backend)
    ids = _detected_ids(probe)
    (tmp_path / "a.csv").write_text(
        f"peak_id\n{ids['A'][0]}\n", encoding="utf-8"
    )
    (tmp_path / "b.csv").write_text(
        f"peak_id\n{ids['B'][0]}\n{ids['B'][1]}\n", encoding="utf-8"
    )
    combos_csv = tmp_path / "combos_cond.csv"
    combos_csv.write_text(
        "zero_fill,localization,localization.targets.gaussian\n"
        '1,gaussian,"{A: a.csv, B: b.csv}"\n',
        encoding="utf-8",
    )
    combos = load_combo_table(combos_csv)
    spec = combos[0]["localization.targets.gaussian"]
    assert isinstance(spec, dict) and set(spec) == {"A", "B"}
    assert Path(spec["A"]).name == "a.csv"
    result = run_combination_study(
        str(probe.session.root), combos=combos, resume=False, backend=backend
    )
    runs = {run.condition: run for run in result.runs}
    assert runs["A"].peak_localization["gaussian"]["n_targeted"] == 1
    assert runs["B"].peak_localization["gaussian"]["n_targeted"] == 2
    record = runs["A"].parameters_resolved["detection"]["localization_targets"]
    assert record["source"] == "combo"
    assert record["by_method"]["gaussian"]["condition"] == "A"


def test_condition_targets_resume_only_reruns_the_changed_condition(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """The fingerprint carries the per-condition list: edit one -> only that one re-runs."""
    backend = _FakeBackend()
    probe = _ab_study(tmp_path, bruker_dir, backend)
    ids = _detected_ids(probe)
    assert len(ids["B"]) >= 3
    targets = _condition_csv(
        tmp_path / "targets.csv",
        [("A", ids["A"][0]), ("B", ids["B"][0]), ("B", ids["B"][1])],
    )
    first = _rerun(probe.session.root, backend, localize_peaks=targets, resume=False)
    assert {
        run.condition: run.peak_localization["gaussian"]["n_targeted"]
        for run in first.runs
    } == {"A": 1, "B": 2}
    calls = len(backend.process_calls)
    again = _rerun(probe.session.root, backend, localize_peaks=targets, resume=True)
    assert len(backend.process_calls) == calls  # nothing changed -> everything reused
    assert [run.resume_fingerprint for run in again.runs] == [
        run.resume_fingerprint for run in first.runs
    ]
    # change one peak number of B only (same row count, A's list bit-for-bit unchanged)
    targets.write_text(
        f"condition,peak_id\nA,{ids['A'][0]}\n"
        f"B,{ids['B'][1]}\nB,{ids['B'][2]}\n",
        encoding="utf-8",
    )
    third = _rerun(probe.session.root, backend, localize_peaks=targets, resume=True)
    assert len(backend.process_calls) == calls + 1  # only B re-runs
    runs = {run.condition: run for run in third.runs}
    assert runs["A"].parameters_resolved["detection"]["localization_targets"][
        "peak_ids"
    ] == [ids["A"][0]]
    assert runs["B"].parameters_resolved["detection"]["localization_targets"][
        "peak_ids"
    ] == [ids["B"][1], ids["B"][2]]
    assert runs["B"].peak_localization["gaussian"]["n_targeted"] == 2


def test_condition_aware_readers_and_low_level_api(tmp_path: Path) -> None:
    """Low level: read_localization_targets(condition=) and an explicit empty set."""
    path = _condition_csv(tmp_path / "t.csv", [("A", 3), ("B", 9)])
    with pytest.raises(SweepError, match="has a condition column"):
        read_localization_targets(path)
    parsed = read_localization_targets(path, condition="B")
    assert parsed.peak_ids == (9,)
    assert parsed.condition == "B"
    assert parsed.line_ranges == ((3, 3),)
    with pytest.raises(SweepError, match="has no rows for condition 'C'"):
        read_localization_targets(path, condition="C")
    conditional = resolve_conditional_targets(path, conditions=["A", "B"])
    assert conditional.mode == "by_condition"
    assert conditional.is_conditional
    assert conditional.for_condition("A").peak_ids == (3,)
    assert conditional.describe().startswith("per condition")
    assert conditional.to_dict()["conditions"]["B"]["n_targets"] == 1
    with pytest.raises(SweepError, match="unknown on_missing"):
        resolve_conditional_targets(path, conditions=["A", "B"], on_missing="nope")
    with pytest.raises(SweepError, match="does not belong to the study"):
        resolve_conditional_targets(path, conditions=["A"])

    spectrum = _write_ft2(tmp_path / "spec.ft2")
    rows, _meta = detect_and_localize(spectrum, method="parabolic", sigma_multiplier=20)
    with pytest.raises(MeasurementError, match="target peak list is empty"):
        detect_and_localize(
            spectrum, method="parabolic", sigma_multiplier=20, targets=()
        )
    none_rows, none_meta = detect_and_localize(
        spectrum,
        method="parabolic",
        sigma_multiplier=20,
        targets=(),
        allow_empty_targets=True,
    )
    assert none_meta["localization_scope"] == "none"
    assert none_meta["n_targeted"] == 0
    assert none_meta["n_skipped"] == len(rows) == len(none_rows)
    assert [row["peak_id"] for row in none_rows] == [row["peak_id"] for row in rows]
    assert [row["H_ppm"] for row in none_rows] == [row["H_ppm"] for row in rows]
