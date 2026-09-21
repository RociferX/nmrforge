"""SMILE optimisation step test: state inference (optional) + ProcessingController wiring."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.project import ProjectManager
from gui.pipeline_panel import PIPELINE_STEPS, compute_step_statuses
from gui.pipeline_state import record_step_success
from gui.processing import ProcessingController


def _manager_with_artifacts(tmp_path: Path):
    """Experiment type + sample data + fid/ spectrum (No peak table/Report)."""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("NUS")
    data = manager.import_data(entry.id, "/fake/1")
    exp_id, data_id = entry.id, data.id
    process = manager.data_dir(exp_id, data_id, "process")
    process.mkdir(parents=True, exist_ok=True)
    fid = process / f"{exp_id}-{data_id}.fid"
    fid.write_bytes(b"fid")
    manager.set_data_fid(exp_id, data_id, fid)
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    ft2 = spectra / f"{exp_id}-{data_id}.ft2"
    ft2.write_bytes(b"ft2")
    manager.set_data_spectrum(exp_id, data_id, ft2)
    manager.save()
    return manager, exp_id, data_id, ft2


def test_smile_step_position_in_pipeline() -> None:
    ids = [step[0] for step in PIPELINE_STEPS]
    # After spectrum generation, before peak picking (0.2.162-patch12 remove import).
    assert ids.index("smile") == 2


def test_smile_status_optional_and_outdated(tmp_path: Path) -> None:
    manager, exp_id, data_id, ft2 = _manager_with_artifacts(tmp_path)
    record_step_success(manager, exp_id, data_id, "spectrum")
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["smile"] == "READY"  # Spectrum can be run after completion.
    assert statuses["peaks"] == "READY"  # Optional: Peak picking does not rely on smile.
    # After running smile SUCCESS.
    record_step_success(manager, exp_id, data_id, "smile")
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["smile"] == "SUCCESS"
    # Spectrum regeneration -> smile expiration (input fingerprint changes).
    ft2.write_bytes(b"ft2-v2")
    record_step_success(manager, exp_id, data_id, "spectrum")
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["smile"] == "OUTDATED"


def test_optimize_smile_rejects_uniform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.data.internal_data_model import SamplingMode

    manager, exp_id, data_id, _ft2 = _manager_with_artifacts(tmp_path)
    controller = ProcessingController(manager)
    experiment = SimpleNamespace(
        sampling=SimpleNamespace(mode=SamplingMode.UNIFORM)
    )
    monkeypatch.setattr(controller, "_read_experiment", lambda *a, **k: experiment)
    with pytest.raises(RuntimeError, match="NUS"):
        controller.optimize_smile(None, exp_id=exp_id, data_id=data_id)


def test_optimize_smile_rejects_3d_nus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29hz-Xiu 21(user): 3D NUS is not currently available SMILE optimisation (the
    entrance is hidden and the controller is blocked)."""
    from core.data.internal_data_model import SamplingMode

    manager, exp_id, data_id, _ft2 = _manager_with_artifacts(tmp_path)
    controller = ProcessingController(manager)
    experiment = SimpleNamespace(
        ndim=3, sampling=SimpleNamespace(mode=SamplingMode.NUS)
    )
    monkeypatch.setattr(controller, "_read_experiment", lambda *a, **k: experiment)
    with pytest.raises(RuntimeError, match="2D NUS"):
        controller.optimize_smile(None, exp_id=exp_id, data_id=data_id)


def test_optimize_smile_progress_and_concise_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.162-Supplement: Progress callback is transparently transmitted (optimizing x/N), and the
    completion log is a simplified string."""
    import workflow.smile_optimize as sm
    from core.data.internal_data_model import SamplingMode

    manager, exp_id, data_id, _ft2 = _manager_with_artifacts(tmp_path)
    controller = ProcessingController(manager)
    experiment = SimpleNamespace(sampling=SimpleNamespace(mode=SamplingMode.NUS))
    monkeypatch.setattr(controller, "_read_experiment", lambda *a, **k: experiment)
    monkeypatch.setattr(
        controller, "_last_spectrum_params", lambda *a, **k: {"nthread": 4}
    )
    def fake_scan(
        exp, backend, base_params=None, *, scan_dir, grid=None, progress=None, **kw
    ):
        assert base_params == {"nthread": 4}
        progress(1, 25, "Optimizing 1/25: {'nsigma': [[0]], 'thresh': [[1]]}")
        return {
            "success": True,
            "message": "Completed 1 set of scans",
            "logs": [],
            "rows": [
                {
                    "index": 1,
                    "nsigma": 5.0,
                    "thresh": 0.95,
                    "stable_count": 3,
                    "mean_snr": 12.5,
                    "quality": 80.0,
                    "rank": 1,
                }
            ],
            "scripts": {1: "# rank1 script\n"},
            "scan_dir": str(scan_dir),
            "n_combos": 25,
        }

    monkeypatch.setattr(sm, "scan_smile_parameters", fake_scan)
    monkeypatch.setattr(
        sm,
        "write_smile_scan_output",
        lambda manager, exp_id, data_id, rows, scripts: {
            "csv": "/x_ranking.csv",
            "json": "/x_ranking.json",
            "rank1": "/x_rank1.com",
        },
    )
    import backend.memory_disk as memory_disk

    monkeypatch.setattr(
        memory_disk,
        "prepare_intermediate",
        lambda work, exp, params=None: (work, None),
    )
    monkeypatch.setattr(
        memory_disk, "teardown_intermediate", lambda work, mem: None
    )
    received: list[str] = []
    out = controller.optimize_smile(
        None, exp_id=exp_id, data_id=data_id, progress=received.append
    )
    assert received == ["Optimizing 1/25: {'nsigma': [[0]], 'thresh': [[1]]}"]
    assert isinstance(out, str)
    assert "Group scan completed" in out
    assert "Rank1" in out
    assert "/x_ranking.csv" in out
