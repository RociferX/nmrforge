"""Pipeline state machine tests: OUTDATED + fingerprint verification (gui/pipeline_state)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtcompat.QtWidgets import QApplication

from core.project import ProjectManager
from core.project.artifacts import find_primary_spectrum
from gui.pipeline_panel import PipelinePanel, compute_step_statuses
from gui.pipeline_state import (
    file_fingerprint,
    input_fingerprint,
    load_pipeline_state,
    record_step_success,
)


def _manager_with_artifacts(tmp_path: Path):
    """Project + experiment type + sample data + the full artifact set
    (fid/spectrum/peak table/report, no fingerprint state)."""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/bruker/1")
    exp_id, data_id = entry.id, data.id
    process = manager.data_dir(exp_id, data_id, "process")
    process.mkdir(parents=True, exist_ok=True)
    fid = process / f"{exp_id}-{data_id}.fid"
    fid.write_bytes(b"fid-v1")
    manager.set_data_fid(exp_id, data_id, fid)
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    ft2 = spectra / f"{exp_id}-{data_id}.ft2"
    ft2.write_bytes(b"ft2-v1")
    manager.set_data_spectrum(exp_id, data_id, ft2)
    peaks = manager.data_dir(exp_id, data_id, "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    csv_path = peaks / f"{exp_id}-{data_id}.csv"
    csv_path.write_text(
        "Peak_ID,H_shift,N_shift,Intensity,SN,label\n1,8.0,115.0,100,20,G1\n",
        encoding="utf-8",
    )
    report = manager.data_dir(exp_id, data_id, "report")
    report.mkdir(parents=True, exist_ok=True)
    (report / "report.html").write_text("<html>ok</html>", encoding="utf-8")
    manager.save()
    return manager, exp_id, data_id, {"fid": fid, "ft2": ft2, "csv": csv_path}


def _record_all(manager: ProjectManager, exp_id: str, data_id: str) -> None:
    for step in ("fid", "spectrum", "smile", "peaks"):
        record_step_success(manager, exp_id, data_id, step)


def test_file_fingerprint_changes_with_content(tmp_path: Path) -> None:
    """File fingerprint: content change → fingerprint change; a missing file returns None."""
    path = tmp_path / "a.txt"
    path.write_bytes(b"abc")
    first = file_fingerprint(path)
    path.write_bytes(b"abd")
    second = file_fingerprint(path)
    assert first != second
    assert file_fingerprint(tmp_path / "missing") is None


def test_raw_fingerprint_ignores_mtime_touch() -> None:
    """0.2.84 regression: touching only the mtime of a small file (content
    unchanged) does not change the raw fingerprint -- the backend conversion
    touches auxiliary files such as profYZ.dat, and a pure mtime fingerprint
    once made both import and "Generate FID" report OUTDATED after a 3D FID
    generation."""
    import os
    import tempfile
    from pathlib import Path as _Path

    from core.project import ProjectManager
    from gui import pipeline_state as ps

    with tempfile.TemporaryDirectory() as td:
        root = _Path(td)
        manager = ProjectManager.create_project(root / "proj", "demo")
        entry = manager.create_experiment(title="e")
        data = manager.import_data(entry.id, str(root / "src"))
        raw = manager.data_dir(entry.id, data.id, "raw")
        raw.mkdir(parents=True, exist_ok=True)
        data.raw_dir = str(raw)  # point at the in-project raw copy (simulates a real import)
        manager.save()
        # 0.2.89: the input fingerprint only counts authoritative input files
        # (acqus etc.); touch/content changes are judged from that file, while
        # processing artifacts (auxiliary files such as profYZ.dat) are excluded
        (raw / "acqus").write_text("payload-v1", encoding="utf-8")
        f1 = ps.raw_fingerprint(manager, entry.id, data.id)
        st = (raw / "acqus").stat()
        os.utime(raw / "acqus", (st.st_atime + 1, st.st_mtime + 1))
        f2 = ps.raw_fingerprint(manager, entry.id, data.id)
        assert f1 == f2, "mtime touch 不应改变 raw 指纹"
        (raw / "acqus").write_text("payload-v2", encoding="utf-8")
        f3 = ps.raw_fingerprint(manager, entry.id, data.id)
        assert f1 != f3, "内容变化应改变 raw 指纹"


def test_raw_processing_artifacts_ignored(tmp_path: Path) -> None:
    """0.2.89: processing that writes/moves intermediates under raw/ (fid/, mask/
    etc.) does not change the input fingerprint."""
    from gui import pipeline_state as ps

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment(title="e")
    data = manager.import_data(entry.id, str(tmp_path / "src"))
    raw = manager.data_dir(entry.id, data.id, "raw")
    raw.mkdir(parents=True, exist_ok=True)
    data.raw_dir = str(raw)
    manager.save()
    (raw / "acqus").write_text("acqus-v1", encoding="utf-8")
    (raw / "ser").write_text("ser-v1", encoding="utf-8")
    f1 = ps.raw_fingerprint(manager, entry.id, data.id)
    # simulate 3D NUS processing: write fid/ and mask/ intermediates under raw/ and move some files
    (raw / "fid").mkdir()
    (raw / "mask").mkdir()
    (raw / "fid" / "test001.fid").write_text("x", encoding="utf-8")
    (raw / "mask" / "test001.fid").write_text("y", encoding="utf-8")
    (raw / "test.fid").write_text("z", encoding="utf-8")
    (raw / "test.fid").unlink()  # simulate the processing moving files
    f2 = ps.raw_fingerprint(manager, entry.id, data.id)
    assert f1 == f2, "处理产物不应改变 raw 输入指纹"


def test_raw_fingerprint_tracks_schedule_named_by_acqus(tmp_path: Path) -> None:
    "Regression coverage: test raw fingerprint tracks schedule named by acqus."
    from gui import pipeline_state as ps

    manager = ProjectManager.create_project(tmp_path / "proj_named", "demo")
    entry = manager.create_experiment(title="e")
    data = manager.import_data(entry.id, str(tmp_path / "src"))
    raw = manager.data_dir(entry.id, data.id, "raw")
    raw.mkdir(parents=True, exist_ok=True)
    data.raw_dir = str(raw)
    (raw / "acqus").write_text("##$NUSLIST= <CANH>\n", encoding="utf-8")
    (raw / "CANH").write_text("0 0\n1 1\n", encoding="utf-8")
    manager.save()

    first = ps.raw_fingerprint(manager, entry.id, data.id)
    (raw / "CANH").write_text("0 0\n1 2\n", encoding="utf-8")
    second = ps.raw_fingerprint(manager, entry.id, data.id)

    assert first != second


def test_statuses_success_without_state(tmp_path: Path, qapp: QApplication) -> None:
    """Old data (no fingerprint state): an existing artifact is SUCCESS, not OUTDATED."""
    manager, exp_id, _data_id, _artifacts = _manager_with_artifacts(tmp_path)
    statuses = compute_step_statuses(manager, exp_id)
    for step in ("fid", "spectrum", "peaks"):
        assert statuses[step] == "SUCCESS"


def test_upstream_regen_marks_downstream_outdated(tmp_path: Path, qapp: QApplication) -> None:
    """Re-running spectrum generation and recording it → peak picking becomes
    OUTDATED (fingerprint check)."""
    manager, exp_id, data_id, artifacts = _manager_with_artifacts(tmp_path)
    _record_all(manager, exp_id, data_id)
    statuses = compute_step_statuses(manager, exp_id)
    assert all(st == "SUCCESS" for st in statuses.values())

    artifacts["ft2"].write_bytes(b"ft2-v2")
    record_step_success(manager, exp_id, data_id, "spectrum")
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["fid"] == "SUCCESS"
    assert statuses["spectrum"] == "SUCCESS"
    assert statuses["peaks"] == "OUTDATED"

    # re-pick peaks (peak table content updated) and record → peaks back to SUCCESS
    artifacts["csv"].write_text(
        "Peak_ID,H_shift,N_shift,Intensity,SN,label\n1,8.0,115.0,100,20,G1\n2,7.5,118.0,80,15,A2\n",
        encoding="utf-8",
    )
    record_step_success(manager, exp_id, data_id, "peaks")
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["peaks"] == "SUCCESS"


def test_smile_rank_scripts_do_not_invalidate_active_spectrum(
    tmp_path: Path, qapp: QApplication
) -> None:
    """STATE-003: when a scan only adds Rank templates, the active spectrum and
    downstream statuses stay successful."""
    manager, exp_id, data_id, _artifacts = _manager_with_artifacts(tmp_path)
    process = manager.data_dir(exp_id, data_id, "process")
    spectrum_script = process / "spectrum.com"
    spectrum_script.write_text("#!/bin/csh\nnmrPipe -fn FT\n", encoding="utf-8")
    _record_all(manager, exp_id, data_id)

    for rank in range(1, 4):
        (process / f"{data_id}_nus_rank{rank}.com").write_text(
            f"#!/bin/csh\n# candidate {rank}\n",
            encoding="utf-8",
        )

    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["spectrum"] == "SUCCESS"
    assert statuses["smile"] == "SUCCESS"
    assert statuses["peaks"] == "SUCCESS"

    spectrum_script.write_text("#!/bin/csh\nnmrPipe -fn FT -auto\n", encoding="utf-8")
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["spectrum"] == "OUTDATED"


def test_projection_only_is_not_primary_spectrum(tmp_path: Path, qapp: QApplication) -> None:
    """STATE-006: a projection leftover must not make any Pipeline view treat
    the main spectrum as present."""
    manager = ProjectManager.create_project(tmp_path / "proj_projection", "demo")
    entry = manager.create_experiment("3D")
    data = manager.import_data(entry.id, "/fake/bruker/1")
    process = manager.data_dir(entry.id, data.id, "process")
    process.mkdir(parents=True, exist_ok=True)
    fid = process / f"{data.id}.fid"
    fid.write_bytes(b"fid")
    manager.set_data_fid(entry.id, data.id, fid)
    spectra = manager.data_dir(entry.id, data.id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / f"{data.id}_15N-1H.ft2").write_bytes(b"projection")

    assert find_primary_spectrum(manager, entry.id, data.id) is None
    statuses = compute_step_statuses(manager, entry.id)
    assert statuses["fid"] == "SUCCESS"
    assert statuses["spectrum"] == "READY"

    main = spectra / f"{data.id}.ft3"
    main.write_bytes(b"main")
    assert find_primary_spectrum(manager, entry.id, data.id) == main
    assert compute_step_statuses(manager, entry.id)["spectrum"] == "SUCCESS"


def test_raw_change_marks_fid_outdated_and_propagates(tmp_path: Path, qapp: QApplication) -> None:
    """Raw data change → FID OUTDATED, propagated to downstream along dependencies."""
    manager, exp_id, data_id, _artifacts = _manager_with_artifacts(tmp_path)
    _record_all(manager, exp_id, data_id)
    meta = manager.data_metadata_path(exp_id, data_id)
    meta.write_text("changed", encoding="utf-8")
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["fid"] == "OUTDATED"
    assert statuses["spectrum"] == "OUTDATED"
    assert statuses["peaks"] == "OUTDATED"


def test_segmented_merged_fid_directory_counts_as_done(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.108: when the segmented merged FID is a process/merged/fid
    directory, the FID step is SUCCESS."""
    manager, exp_id, data_id, _artifacts = _manager_with_artifacts(tmp_path)
    process = manager.data_dir(exp_id, data_id, "process")
    merged_fid = process / "merged" / "fid"
    merged_fid.mkdir(parents=True, exist_ok=True)
    (merged_fid / "test001.fid").write_bytes(b"x")
    manager.set_data_fid(exp_id, data_id, merged_fid)
    record_step_success(manager, exp_id, data_id, "fid")
    manager.save()
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["fid"] == "SUCCESS"


def test_simple_mode_disables_outdated(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.91: simple mode -- judged only by artifact files, no OUTDATED."""
    manager, exp_id, data_id, artifacts = _manager_with_artifacts(tmp_path)
    _record_all(manager, exp_id, data_id)
    # the spectrum step input is the FID file: rewriting the FID → OUTDATED by default
    artifacts["fid"].write_bytes(b"fid-v2")
    assert compute_step_statuses(manager, exp_id)["spectrum"] == "OUTDATED"

    monkeypatch.setattr(
        "gui.settings.load_settings",
        lambda: {"pipeline": {"simple_mode": True}},
    )
    statuses = compute_step_statuses(manager, exp_id)
    assert "OUTDATED" not in statuses.values()
    assert statuses["spectrum"] == "SUCCESS"


def test_mtime_fallback_without_state(tmp_path: Path, qapp: QApplication) -> None:
    """Old data (no fingerprint state): an upstream artifact newer than the
    downstream one → OUTDATED (mtime heuristic)."""
    manager, exp_id, data_id, artifacts = _manager_with_artifacts(tmp_path)
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["spectrum"] == "SUCCESS"
    assert statuses["peaks"] == "SUCCESS"
    artifacts["ft2"].write_bytes(b"ft2-new")
    # explicitly set the ft2 mtime after peaks (avoids the same-second mtime
    # making the heuristic unstable in a full run)
    _peaks_mtime = artifacts["csv"].stat().st_mtime
    os.utime(artifacts["ft2"], (_peaks_mtime + 5, _peaks_mtime + 5))
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["spectrum"] == "SUCCESS"
    assert statuses["peaks"] == "OUTDATED"


def test_panel_shows_outdated_and_rerun_button(tmp_path: Path, qapp: QApplication) -> None:
    'Panel: an OUTDATED step shows the "run again" entry and the next-step hint.'
    manager, exp_id, data_id, artifacts = _manager_with_artifacts(tmp_path)
    _record_all(manager, exp_id, data_id)
    artifacts["ft2"].write_bytes(b"ft2-v2")
    record_step_success(manager, exp_id, data_id, "spectrum")

    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    assert panel._rows["peaks"].status_label.text().startswith("!")
    assert panel._rows["peaks"].run_button.text() == "更新结果"
    assert not panel._rows["peaks"].run_button.isHidden()
    assert "更新" in panel.next_label.text()
    panel.close()


def test_save_peaks_manual_records_state(tmp_path: Path) -> None:
    """Saving the peak table manually records the peaks fingerprint."""
    from gui.processing import ProcessingController

    manager, exp_id, data_id, _artifacts = _manager_with_artifacts(tmp_path)
    controller = ProcessingController(manager)
    peaks = [
        {
            "Peak_ID": 1,
            "H_shift": 8.0,
            "N_shift": 115.0,
            "Intensity": 100.0,
            "SN": 20.0,
            "label": "G1",
        }
    ]
    controller.save_peaks_manual(None, peaks, exp_id=exp_id, data_id=data_id)
    state = load_pipeline_state(manager, exp_id, data_id)
    assert "peaks" in state["steps"]
    assert state["steps"]["peaks"]["input_hash"] == input_fingerprint(
        manager, exp_id, data_id, "peaks"
    )


class _FakeController:
    """Minimal fake controller for building PipelinePanel (tests only refresh
    state, never run steps)."""

    def _read_experiment(self, *args, **kwargs):
        """Return NUS sampling (0.2.199-patch29gd: the SMILE row is shown only for NUS)."""
        from types import SimpleNamespace

        from core.data.internal_data_model import SamplingMode

        return SimpleNamespace(sampling=SimpleNamespace(mode=SamplingMode.NUS))

    def data_facts(self, *args, **kwargs) -> dict:
        """Public interface (0.2.199-patch29hz): Pipeline gating reads a dict,
        no longer touching privates."""
        return {
            "ndim": 2,
            "direct_nucleus": "1H",
            "is_nus": True,
            "sampling_mode": "NUS",
        }


def test_next_label_skips_optional_smile(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.199-patch29as: when SMILE is not done the next step points to peak
    picking and shows "optional" separately."""
    from gui.pipeline_panel import PipelinePanel
    from gui.pipeline_state import record_step_success

    manager, exp_id, data_id, _ft2 = _manager_with_artifacts(tmp_path)
    # remove the peak table so peaks stays READY (the fixture has the full artifact set by default)
    for f in manager.data_dir(exp_id, data_id, "peaks").glob("*"):
        f.unlink()
    for f in manager.data_dir(exp_id, data_id, "report").glob("*"):
        f.unlink()
    record_step_success(manager, exp_id, data_id, "spectrum")
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    text = panel.next_label.text()
    assert "峰挑选" in text
    assert "SMILE" in text
    assert "可选做" in text
    # once SMILE is done "optional" no longer appears; the next step is peak picking
    record_step_success(manager, exp_id, data_id, "smile")
    panel.refresh()
    text = panel.next_label.text()
    assert "可选做" not in text
    assert "峰挑选" in text
    panel.close()


def test_pipeline_peaks_reference_selection(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.199-patch29dl: peak-picking "Reference spectrum" button, candidate
    data and reference loading."""
    from core.peaks.peak_table import export_peaks_poky
    from gui.pipeline_panel import PipelinePanel

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    exp = manager.create_experiment("HSQC")
    d1 = manager.import_data(exp.id, "/fake/1")
    d2 = manager.import_data(exp.id, "/fake/2")
    peaks_dir = manager.data_dir(exp.id, d1.id, "peaks")
    peaks_dir.mkdir(parents=True, exist_ok=True)
    export_peaks_poky(
        peaks_dir / f"{exp.id}-{d1.id}.list",
        [{"N_shift": 118.0, "H_shift": 8.0, "Intensity": 1, "label": ""}],
        ndim=2,
    )
    manager.save()
    panel = PipelinePanel(manager)
    row = panel._rows["peaks"]
    assert row.step_id == "peaks"
    assert row.ref_button.text() == "参考谱"
    assert not row.ref_button.isHidden()
    candidates = panel._reference_candidates()
    assert any(did == d1.id for _n, _e, did in candidates)
    assert not any(did == d2.id for _n, _e, did in candidates)
    info = panel._load_reference(exp.id, d1.id)
    assert info is not None and info["peaks"]
    assert info["nuclei"] == ["15N", "1H"]
    # 0.2.199-patch29fx: reference isolated per (exp, data); switching data leaves no leftover
    panel.set_selection("data", exp.id, d1.id)
    panel._ref_info[(exp.id, d1.id)] = info
    panel.refresh()
    assert not row.clear_ref_button.isHidden()
    panel.set_selection("data", exp.id, d2.id)
    assert row.clear_ref_button.isHidden()
    panel.set_selection("data", exp.id, d1.id)
    assert not row.clear_ref_button.isHidden()
    panel._on_clear_reference("peaks")
    assert panel._ref_info == {}
    assert row.clear_ref_button.isHidden()
    panel.close()


def test_pipeline_peak_threshold_isolated_per_data(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.199-patch29fz: the peak-picking threshold is isolated per data set --
    changing it for d_001 does not affect d_002, and switching back to d_001
    restores each value."""
    from core.project import ProjectManager
    from gui.pipeline_panel import PipelinePanel

    manager = ProjectManager.create_project(tmp_path / "proj_t", "demo")
    exp = manager.create_experiment("HSQC")
    d1 = manager.import_data(exp.id, "/fake/1")
    d2 = manager.import_data(exp.id, "/fake/2")
    panel = PipelinePanel(manager)
    row = panel._rows["peaks"]
    panel.set_selection("data", exp.id, d1.id)
    row.threshold_spin.setValue(22.5)
    assert panel._threshold_by_data[(exp.id, d1.id)] == 22.5
    panel.set_selection("data", exp.id, d2.id)
    assert row.threshold_spin.value() == 35.0
    row.threshold_spin.setValue(9.0)
    panel.set_selection("data", exp.id, d1.id)
    assert row.threshold_spin.value() == 22.5
    panel.set_selection("data", exp.id, d2.id)
    assert row.threshold_spin.value() == 9.0
    panel.close()


def test_pipeline_threshold_persisted_in_data_folder(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.199-patch29ga: the threshold is persisted to d_xxx/ui_state.json and
    restored after a restart."""
    import json

    from core.project import ProjectManager
    from gui.pipeline_panel import PipelinePanel

    manager = ProjectManager.create_project(tmp_path / "proj_p", "demo")
    exp = manager.create_experiment("HSQC")
    d1 = manager.import_data(exp.id, "/fake/1")
    panel = PipelinePanel(manager)
    panel.set_selection("data", exp.id, d1.id)
    panel._rows["peaks"].threshold_spin.setValue(18.0)
    path = manager.data_base(exp.id, d1.id) / "ui_state.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["peaks"]["threshold"] == 18.0
    # new panel (simulating a restart) restores from the file
    panel2 = PipelinePanel(manager)
    panel2.set_selection("data", exp.id, d1.id)
    assert panel2._rows["peaks"].threshold_spin.value() == 18.0
    panel.close()
    panel2.close()


def test_threshold_legacy_default15_migrates_to35(tmp_path: Path, qapp: QApplication) -> None:
    """Legacy default 15σ (not explicitly customized) migrates to the new default 35σ."""
    import json

    from core.project import ProjectManager
    from gui.pipeline_panel import PipelinePanel

    manager = ProjectManager.create_project(tmp_path / "proj_m", "demo")
    exp = manager.create_experiment("HSQC")
    d1 = manager.import_data(exp.id, "/fake/1")
    base = manager.data_base(exp.id, d1.id)
    base.mkdir(parents=True, exist_ok=True)
    (base / "ui_state.json").write_text(
        json.dumps({"version": 1, "peaks": {"threshold": 15.0}}),
        encoding="utf-8",
    )
    panel = PipelinePanel(manager)
    panel.set_selection("data", exp.id, d1.id)
    assert panel._rows["peaks"].threshold_spin.value() == 35.0
    panel.close()


def test_threshold_explicit_15_preserved_when_custom(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.199-patch29gc: an explicitly customized 15σ (custom=True) is not
    overwritten by the migration."""
    import json

    from core.project import ProjectManager
    from gui.pipeline_panel import PipelinePanel

    manager = ProjectManager.create_project(tmp_path / "proj_c", "demo")
    exp = manager.create_experiment("HSQC")
    d1 = manager.import_data(exp.id, "/fake/1")
    base = manager.data_base(exp.id, d1.id)
    base.mkdir(parents=True, exist_ok=True)
    (base / "ui_state.json").write_text(
        json.dumps({"version": 1, "peaks": {"threshold": 15.0, "custom": True}}),
        encoding="utf-8",
    )
    panel = PipelinePanel(manager)
    panel.set_selection("data", exp.id, d1.id)
    assert panel._rows["peaks"].threshold_spin.value() == 15.0
    panel.close()


def test_pipeline_smile_step_hidden_for_uniform_data(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.199-patch29gd: non-NUS (fully sampled) data hides the SMILE optimization step."""
    from types import SimpleNamespace

    from core.data.internal_data_model import SamplingMode
    from core.project import ProjectManager
    from gui.pipeline_panel import PipelinePanel

    manager = ProjectManager.create_project(tmp_path / "proj_su", "demo")
    exp = manager.create_experiment("HSQC")
    d1 = manager.import_data(exp.id, "/fake/1")
    panel = PipelinePanel(manager)
    panel.controller._read_experiment = lambda *a, **k: SimpleNamespace(
        sampling=SimpleNamespace(mode=SamplingMode.UNIFORM)
    )
    panel.set_selection("data", exp.id, d1.id)
    assert panel._rows["smile"].isHidden()
    panel.close()


def test_pipeline_smile_step_shown_for_2d_nus_data(tmp_path: Path, qapp: QApplication) -> None:
    """2D NUS data shows the SMILE optimization step (0.2.199-patch29gd + fix21 limits it to 2D)."""
    from types import SimpleNamespace

    from core.data.internal_data_model import SamplingMode
    from core.project import ProjectManager
    from gui.pipeline_panel import PipelinePanel

    manager = ProjectManager.create_project(tmp_path / "proj_sn", "demo")
    exp = manager.create_experiment("HNCA")
    d1 = manager.import_data(exp.id, "/fake/1")
    panel = PipelinePanel(manager)
    panel.controller._read_experiment = lambda *a, **k: SimpleNamespace(
        ndim=2, sampling=SimpleNamespace(mode=SamplingMode.NUS)
    )
    panel.set_selection("data", exp.id, d1.id)
    assert not panel._rows["smile"].isHidden()
    panel.close()


def test_pipeline_smile_step_hidden_for_3d_nus_data(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.199-patch29hz-fix21 (user): 3D NUS temporarily hides the SMILE optimization entry."""
    from types import SimpleNamespace

    from core.data.internal_data_model import SamplingMode
    from core.project import ProjectManager
    from gui.pipeline_panel import PipelinePanel

    manager = ProjectManager.create_project(tmp_path / "proj_s3", "demo")
    exp = manager.create_experiment("HNCA")
    d1 = manager.import_data(exp.id, "/fake/1")
    panel = PipelinePanel(manager)
    panel.controller._read_experiment = lambda *a, **k: SimpleNamespace(
        ndim=3, sampling=SimpleNamespace(mode=SamplingMode.NUS)
    )
    panel.set_selection("data", exp.id, d1.id)
    assert panel._rows["smile"].isHidden()
    panel.close()
