"""Actual effective parameter write-back test (0.2.46):effective_params -> WorkflowRun params."""

from __future__ import annotations

from pathlib import Path

from core.project import ProjectManager
from workflow.stepwise import generate_fid, generate_spectrum


class _EffectiveBackend:
    """Returns a fake backend for effective_params."""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = str(work_dir)

    def _touch(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    def convert_to_fid(self, experiment, data_dir, progress=None) -> dict:
        fid_path = Path(self.work_dir) / f"{experiment.dataset_id}.fid"
        self._touch(fid_path)
        return {
            "success": True,
            "fid_path": str(fid_path),
            "message": "ok",
            "logs": [],
            "effective_params": {
                "dataset_id": experiment.dataset_id,
                "ndim": experiment.ndim,
                "segments": len(experiment.segments),
            },
        }

    def process(
        self,
        experiment,
        plan,
        direct_phase_override=None,
        params=None,
        progress=None,
    ) -> dict:
        spectrum = Path(self.work_dir) / f"{experiment.dataset_id}.ft2"
        self._touch(spectrum)
        return {
            "success": True,
            "spectrum_path": str(spectrum),
            "message": "ok",
            "logs": [],
            "effective_params": {
                "extract": True,
                "ext_lo": "11.0",
                "ext_hi": "6.0",
                "zero_fill": {
                    "F2": {"mode": "auto", "size": 2048},
                    "F1": {"mode": "auto", "size": 256},
                },
                "baseline": {},
                "window": None,
                "direct_phase": {"F2": (0.0, 0.0)},
                "points_per_line": 2.0,
                "sampling": {"ft_neg": False, "ft_alt": None, "auto_phase": True},
            },
        }


def _manager_with_data(
    tmp_path: Path, source: Path
) -> tuple[ProjectManager, str, str]:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment(title="eff")
    data = manager.import_data(entry.id, str(source))
    manager.save()
    return manager, entry.id, data.id


def _last_run(manager: ProjectManager, exp_id: str, workflow_ref: str):
    runs = [
        r
        for r in manager.project.workflow_runs
        if r.experiment_id == exp_id and r.workflow_ref == workflow_ref
    ]
    assert runs
    return runs[-1]


def test_generate_spectrum_records_effective_params(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """New run params = effective_params + caller params (caller takes precedence)."""
    manager, exp_id, data_id = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d"
    )
    backend = _EffectiveBackend(tmp_path / "work")
    generate_fid(manager, exp_id, data_id, backend)
    generate_spectrum(
        manager,
        exp_id,
        data_id,
        backend,
        params={"phase_route": "none", "extract": False},
    )
    run = _last_run(manager, exp_id, "process")
    params = run.params
    assert params["extract"] is False  # Caller takes precedence.
    assert params["ext_lo"] == "11.0"  # effective Effective parameter.
    assert params["zero_fill"]["F2"]["size"] == 2048
    assert params["direct_phase"] == {"F2": (0.0, 0.0)}
    assert params["points_per_line"] == 2.0
    assert params["sampling"]["auto_phase"] is True
    assert params["sampling"]["ft_alt"] is None
    assert "baseline" in params and "window" in params


def test_generate_fid_records_effective_params(
    tmp_path: Path, bruker_dir: Path
) -> None:
    manager, exp_id, data_id = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d"
    )
    backend = _EffectiveBackend(tmp_path / "work")
    generate_fid(manager, exp_id, data_id, backend)
    run = _last_run(manager, exp_id, "convert_to_fid")
    assert run.params["dataset_id"]
    assert run.params["ndim"] == 2
    assert run.params["segments"] == 0


def test_legacy_backend_params_unchanged(tmp_path: Path, bruker_dir: Path) -> None:
    """Old backend (none effective_params) params keep caller params (compatible)."""

    class _LegacyBackend(_EffectiveBackend):
        def process(
        self,
        experiment,
        plan,
        direct_phase_override=None,
        params=None,
        progress=None,
    ) -> dict:
            spectrum = Path(self.work_dir) / f"{experiment.dataset_id}.ft2"
            self._touch(spectrum)
            return {
                "success": True,
                "spectrum_path": str(spectrum),
                "message": "ok",
                "logs": [],
            }

    manager, exp_id, data_id = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d"
    )
    backend = _LegacyBackend(tmp_path / "work")
    generate_fid(manager, exp_id, data_id, backend)
    generate_spectrum(
        manager,
        exp_id,
        data_id,
        backend,
        params={"phase_route": "none", "extract": False},
    )
    run = _last_run(manager, exp_id, "process")
    assert run.params == {"extract": False}


def test_generate_spectrum_records_ucsf_output(
    tmp_path: Path, bruker_dir: Path, monkeypatch
) -> None:
    """0.2.162-patch15: After generating spectrum, UCSF is output and registered to run outputs."""
    import workflow.stepwise as stepwise_mod

    manager, exp_id, data_id = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d"
    )
    backend = _EffectiveBackend(tmp_path / "work")
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    fake_ucsf = spectra / f"{data_id}.ucsf"
    monkeypatch.setattr(
        stepwise_mod,
        "_export_ucsf",
        lambda mgr, e, d, spec: (str(fake_ucsf), f"UCSF has generated: {fake_ucsf}"),
    )
    generate_fid(manager, exp_id, data_id, backend)
    generate_spectrum(
        manager,
        exp_id,
        data_id,
        backend,
        params={"phase_route": "none"},
    )
    run = _last_run(manager, exp_id, "process")
    assert run.outputs.get("ucsf_path") == str(fake_ucsf)


def test_generate_spectrum_none_route_maps_final_ext(
    tmp_path: Path, bruker_dir: Path, monkeypatch
) -> None:
    """0.2.162-patch15: The escape hatch only runs once, final_ext is directly mapped to ext."""
    import workflow.stepwise as stepwise_mod

    captured: dict = {}

    class _CaptureBackend(_EffectiveBackend):
        def process(
            self,
            experiment,
            plan,
            direct_phase_override=None,
            params=None,
            progress=None,
        ) -> dict:
            captured["params"] = dict(params or {})
            return super().process(
                experiment,
                plan,
                direct_phase_override=direct_phase_override,
                params=params,
                progress=progress,
            )

    manager, exp_id, data_id = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d"
    )
    backend = _CaptureBackend(tmp_path / "work")
    monkeypatch.setattr(
        stepwise_mod,
        "_export_ucsf",
        lambda mgr, e, d, spec: (None, "Skip UCSF conversion"),
    )
    generate_fid(manager, exp_id, data_id, backend)
    generate_spectrum(
        manager,
        exp_id,
        data_id,
        backend,
        params={
            "phase_route": "none",
            "final_ext_lo": "11.0",
            "final_ext_hi": "5.5",
        },
    )
    assert captured["params"]["ext_lo"] == "11.0"
    assert captured["params"]["ext_hi"] == "5.5"
    assert "final_ext_lo" not in captured["params"]
    run = _last_run(manager, exp_id, "process")
    assert "final_ext_lo" not in run.params


def test_zf_summary_compact() -> None:
    """Zero filling plan summary only retains mode/size (for WorkflowRun params)."""
    from backend.nmrpipe_backend import zf_summary

    plan = {"F2": {"mode": "auto", "size": 2048, "note": "direct dimension 2 x TD"}}
    assert zf_summary(plan) == {"F2": {"mode": "auto", "size": 2048}}
