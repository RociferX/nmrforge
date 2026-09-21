"""SMILE Scan Rank1 entry (0.2.199-patch29hz-fix 3 step 3)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from qtcompat.QtWidgets import QApplication

from core.project import ProjectManager  # noqa: E402
from gui.pipeline_panel import PipelinePanel  # noqa: E402
from gui.processing import ProcessingController  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _manager(tmp_path: Path):
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    exp = manager.create_experiment("HNCA")
    data = manager.import_data(exp.id, "/fake/1")
    return manager, exp, data


def test_rank1_button_visible_only_after_success(
    tmp_path: Path, qapp: QApplication
) -> None:
    """SMILE Only after the step is successful can "Rerun according to Rank1" (Option B:
    optimisation does not automatically generate scores)."""
    from qtcompat.QtWidgets import QWidget

    host = QWidget()
    manager, _exp, _data = _manager(tmp_path)
    panel = PipelinePanel(manager, parent=host)
    row = panel._rows["smile"]

    row.set_status("READY")
    assert not row.rank1_button.isVisibleTo(row)
    row.set_status("SUCCESS")
    assert row.rank1_button.isVisibleTo(row)
    # Do not leave this button for other steps.
    assert not panel._rows["fid"].rank1_button.isVisibleTo(panel._rows["fid"])
    panel.close()
    host.deleteLater()
    QApplication.processEvents()


def test_rerun_rank1_requires_script(tmp_path: Path) -> None:
    """When there is no Rank1 script, an error will be explicitly reported instead of silently
    rerunning other scripts."""
    manager, exp, data = _manager(tmp_path)
    controller = ProcessingController(manager)
    with pytest.raises(RuntimeError, match="Rank1"):
        controller.rerun_smile_rank1(exp.id, data.id)

def test_rerun_rank1_refreshes_companions_and_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SMILE-005: Use Rank1 to synchronize FT2/UCSF/QC, and snapshot parameter and script."""
    import json
    from types import SimpleNamespace

    manager, exp, data = _manager(tmp_path)
    controller = ProcessingController(manager)
    process = manager.data_dir(exp.id, data.id, "process")
    process.mkdir(parents=True, exist_ok=True)
    script = process / f"{data.id}_nus_rank1.com"
    script.write_text("#!/bin/csh\n# nSigma=3 thresh=0.5\n", encoding="utf-8")
    ranking_dir = manager.data_dir(exp.id, data.id, "smile_optimized")
    ranking_dir.mkdir(parents=True, exist_ok=True)
    ranking = ranking_dir / f"{exp.id}-{data.id}_smile_ranking.json"
    ranking.write_text(
        json.dumps({"rows": [{"rank": 1, "nsigma": 3.0, "thresh": 0.5}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        controller, "_read_experiment", lambda *args: SimpleNamespace(ndim=2)
    )

    class _Runtime:
        def run(self, argv, *, cwd, timeout):
            Path(cwd, f"{data.id}.ft2").write_bytes(b"rank1-spectrum")
            return SimpleNamespace(returncode=0, stdout="rank1 ok", stderr="")

    monkeypatch.setattr("backend.runtime.CshRuntime", _Runtime)

    def _export(source, target):
        Path(target).write_bytes(b"rank1-ucsf")
        return str(target), f"UCSF has generated: {target}"

    monkeypatch.setattr("workflow.ucsf_export.export_ucsf", _export)
    monkeypatch.setattr(
        "workflow.optimization_report.spectrum_quality_report_lines",
        lambda *args, **kwargs: [
            "◆ The best final spectrum image quality", "Comprehensive judgment: test"],
    )

    target = Path(controller.rerun_smile_rank1(exp.id, data.id))
    assert target.read_bytes() == b"rank1-spectrum"
    assert target.with_suffix(".ucsf").read_bytes() == b"rank1-ucsf"
    quality = Path(f"{target}.quality.json")
    assert quality.is_file()

    run = manager.project.workflow_runs[-1]
    assert run.workflow_ref == "smile_optimize_rank1"
    assert run.status == "success"
    assert run.params["smile_rank"] == 1
    assert run.params["smile_candidate"]["nsigma"] == 3.0
    assert set(run.outputs) == {"spectrum_path", "ucsf_path", "quality_record"}
    snapshot = manager.root / run.snapshot_dir
    assert (snapshot / script.name).read_text(encoding="utf-8") == script.read_text(
        encoding="utf-8"
    )
    saved_params = json.loads((snapshot / "params.json").read_text(encoding="utf-8"))
    assert saved_params["smile_candidate"]["thresh"] == 0.5


def test_rank1_run_ref_is_a_spectrum_run() -> None:
    from core.project.run_refs import STEP_RUN_REFS

    assert "smile_optimize_rank1" in STEP_RUN_REFS["spectrum"]

def test_rerun_rank1_failure_keeps_failed_run_and_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execution failures also retain the Rank1 parameter / script snapshot and failure status."""
    from types import SimpleNamespace

    manager, exp, data = _manager(tmp_path)
    controller = ProcessingController(manager)
    process = manager.data_dir(exp.id, data.id, "process")
    process.mkdir(parents=True, exist_ok=True)
    script = process / f"{data.id}_nus_rank1.com"
    script.write_text("#!/bin/csh\nexit 1\n", encoding="utf-8")
    monkeypatch.setattr(
        controller, "_read_experiment", lambda *args: SimpleNamespace(ndim=2)
    )

    class _Runtime:
        def run(self, argv, *, cwd, timeout):
            return SimpleNamespace(returncode=1, stdout="failed", stderr="boom")

    monkeypatch.setattr("backend.runtime.CshRuntime", _Runtime)
    with pytest.raises(RuntimeError, match="Rank1 rerun failed"):
        controller.rerun_smile_rank1(exp.id, data.id)

    run = manager.project.workflow_runs[-1]
    assert run.workflow_ref == "smile_optimize_rank1"
    assert run.status == "failed"
    assert run.snapshot_dir
    assert (manager.root / run.snapshot_dir / script.name).is_file()
