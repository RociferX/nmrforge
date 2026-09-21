"""Step processing test: generate_fid / generate_spectrum / phase brute force optimisation."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.data.bruker_reader import read_dataset
from core.project import ExperimentStatus, ProjectManager
from ui_support.i18n import tr
from workflow.stepwise import (
    StepwiseError,
    generate_fid,
    generate_spectrum,
)


class _FakeBackend:
    """Fake backend that logs calls (convert_to_fid / process / reconstruct_nus / project_3d)."""

    def __init__(self, work_dir: Path, *, success: bool = True) -> None:
        self.work_dir = str(work_dir)
        self.success = success
        self.calls: list[str] = []
        self.process_params: list[dict | None] = []
        self.experiment = None

    def _touch(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    def convert_to_fid(self, experiment, data_dir, progress=None) -> dict:
        self.experiment = experiment
        self.calls.append("convert_to_fid")
        if not self.success:
            return {"success": False, "message": "Conversion failed", "logs": []}
        fid_path = Path(self.work_dir) / f"{experiment.dataset_id}.fid"
        self._touch(fid_path)
        return {"success": True, "fid_path": str(fid_path), "message": "ok", "logs": []}

    def process(
        self,
        experiment,
        plan,
        direct_phase_override=None,
        params=None,
        progress=None,
        out_file=None,
        script_name=None,
    ) -> dict:
        self.calls.append("process")
        self.last_params = params
        self.process_params.append(params)
        p0, p1 = 0, 0
        if direct_phase_override:
            # When searching dimension by dimension, covering multiple axes, take p0/p1 of the last
            # axis (the axis being searched).
            p0, p1 = list(direct_phase_override.values())[-1]
        spectrum = Path(self.work_dir) / f"out_p0{int(p0)}_p1{int(p1)}.ft2"
        self._touch(spectrum)
        return {
            "success": True,
            "spectrum_path": str(spectrum),
            "message": "ok",
            "logs": [],
        }

    def reconstruct_nus(self, experiment, params, progress=None) -> dict:
        self.calls.append("reconstruct_nus")
        spectrum = Path(self.work_dir) / "out_nus.ft2"
        self._touch(spectrum)
        return {
            "success": True,
            "spectrum_path": str(spectrum),
            "message": "ok",
            "logs": [],
        }

    def project_3d(
        self,
        spectrum_path,
        out_dir,
        *,
        prefix="proj",
        timeout=900,
        labels=None,
    ) -> dict:
        """Press 0.2.133 measured geometry to return three projections: xy=(fixed third axis
        F3,F1), xz=(F2,F1), yz=(F2,F3), the head is a plane with two actual cores."""
        self.calls.append("project_3d")
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        by_axis = {
            dim.logical_axis: dim.nucleus for dim in self.experiment.dimensions
        }
        pairs = {  # Production flow geometry (measured): xy=(F3,F1), xz=(F2,F1), yz=(F2,F3).
            "xy": (by_axis["F3"], by_axis["F1"]),
            "xz": (by_axis["F2"], by_axis["F1"]),
            "yz": (by_axis["F2"], by_axis["F3"]),
        }
        fixed = {  # Summed third axis kernel.
            "xy": by_axis["F2"],
            "xz": by_axis["F3"],
            "yz": by_axis["F1"],
        }
        paths, nuclei, fixed_labels = {}, {}, {}
        for tag, (n1, n2) in pairs.items():
            path = out_dir / f"{prefix}_{tag}.ft2"
            self._touch(path)
            paths[tag] = str(path)
            nuclei[tag] = [n1, n2]
            fixed_labels[tag] = fixed[tag]
        return {
            "success": True,
            "paths": paths,
            "labels": fixed_labels,
            "nuclei": nuclei,
            "message": "ok",
            "logs": [],
        }


def _manager_with_data(
    tmp_path: Path, source: Path
) -> tuple[ProjectManager, str, str, Path]:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment(title="HSQC")
    data = manager.import_data(entry.id, str(source))
    return manager, entry.id, data.id, tmp_path / "work"


def test_generate_fid_registers(tmp_path: Path, bruker_dir: Path) -> None:
    manager, exp_id, data_id, work = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d"
    )
    backend = _FakeBackend(work)
    fid_path = generate_fid(manager, exp_id, data_id, backend)
    assert fid_path.endswith(".fid")
    # Contract §9.2:fid placement data_dir(..., "process").
    assert Path(fid_path).parent == manager.data_dir(exp_id, data_id, "process")
    data = manager.data(exp_id, data_id)
    assert data.fid_path == fid_path
    assert data.status == "fid_ready"
    assert backend.calls == ["convert_to_fid"]
    assert any(r.workflow_ref == "convert_to_fid" for r in manager.project.workflow_runs)


def test_generate_spectrum_uniform(tmp_path: Path, bruker_dir: Path) -> None:
    manager, exp_id, data_id, work = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d"
    )
    backend = _FakeBackend(work)
    generate_fid(manager, exp_id, data_id, backend)
    spectrum = generate_spectrum(manager, exp_id, data_id, backend, params={"phase_route": "none"})
    assert spectrum.endswith(".ft2")
    # Contract §9.2: final spectrum placement data_dir(..., "spectra").
    assert Path(spectrum).parent == manager.data_dir(exp_id, data_id, "spectra")
    # G2B-009: final spectrum only saves spectra/,process/ without leaving a copy.
    assert not (manager.data_dir(exp_id, data_id, "process") / Path(spectrum).name).exists()
    data = manager.data(exp_id, data_id)
    assert data.spectrum_path == spectrum
    assert data.status == "processed"
    assert "process" in backend.calls
    assert manager.infer_status(exp_id) is ExperimentStatus.PROCESSED


def test_generate_spectrum_passes_params(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Uniform branching transparently passes params to backend.process (G2B-006)."""
    manager, exp_id, data_id, work = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d"
    )
    backend = _FakeBackend(work)
    generate_fid(manager, exp_id, data_id, backend)
    generate_spectrum(
        manager,
        exp_id,
        data_id,
        backend,
        params={"phase_route": "none", "extract": False, "ext_lo": "9.0"},
    )
    assert backend.last_params == {"extract": False, "ext_lo": "9.0"}


def test_generate_spectrum_nus_uses_reconstruct(tmp_path: Path, bruker_dir: Path) -> None:
    manager, exp_id, data_id, work = _manager_with_data(
        tmp_path, bruker_dir / "nus_2d"
    )
    backend = _FakeBackend(work)
    generate_fid(manager, exp_id, data_id, backend)
    generate_spectrum(manager, exp_id, data_id, backend, params={"phase_route": "none"})
    assert "reconstruct_nus" in backend.calls
    assert any(r.workflow_ref == "reconstruct_nus" for r in manager.project.workflow_runs)


def test_generate_spectrum_3d_projections_new_naming(
    tmp_path: Path, bruker_dir: Path, monkeypatch
) -> None:
    """0.2.133: The 3D projection file name contains the actual two cores of the plane, and the
    registration is based on the fixed axis logical axis; compatible with the old name."""
    import workflow.phase_routes as phase_routes

    manager, exp_id, data_id, work = _manager_with_data(
        tmp_path, bruker_dir / "nus_3d"
    )
    backend = _FakeBackend(work)
    generate_fid(manager, exp_id, data_id, backend)

    def fake_unified(
        experiment, backend_, plan=None, work_dir=None, base_params=None, progress=None
    ):
        return {
            "spectrum_path": str(Path(work) / "d_001.ft3"),
            "phases": {"F1": (0.0, 0.0), "F2": (0.0, 0.0), "F3": (0.0, 0.0)},
            "backend_runs": 1,
            "logs": [],
        }

    monkeypatch.setattr(phase_routes, "unified_route", fake_unified)
    result = generate_spectrum(manager, exp_id, data_id, backend)
    assert result.endswith("d_001.ft3")
    # Nus_3d fixture logical axis: F1=13C, F2=15N, F3=1H(direct).
    spectra_dir = manager.data_dir(exp_id, data_id, "spectra")
    assert sorted(p.name for p in spectra_dir.glob("d_001_*.ft2")) == [
        "d_001_15N-13C.ft2",
        "d_001_15N-1H.ft2",
        "d_001_1H-13C.ft2",
    ]
    # No old style *_proj_*.ft2 names.
    assert not list(spectra_dir.glob("d_001_proj_*.ft2"))
    run = next(
        r
        for r in reversed(manager.project.workflow_runs)
        if r.workflow_ref == "phase_optimize_unified"
    )
    projections = run.params.get("projections", {})
    # Registration key = third axis to be summed (logical): xy -> F2(15N), xz -> F3(1H), yz ->
    # F1(13C).
    assert projections["F2"].endswith("d_001_1H-13C.ft2")
    assert projections["F3"].endswith("d_001_15N-13C.ft2")
    assert projections["F1"].endswith("d_001_15N-1H.ft2")


def test_generate_spectrum_3d_projections_fallback_old_naming(
    tmp_path: Path, bruker_dir: Path, monkeypatch
) -> None:
    """When the backend does not return nuclei, it falls back to d_001_proj_<logical|tag>.ft2
    (compatible with the old name)."""
    import workflow.phase_routes as phase_routes

    manager, exp_id, data_id, work = _manager_with_data(
        tmp_path, bruker_dir / "nus_3d"
    )
    backend = _FakeBackend(work)
    generate_fid(manager, exp_id, data_id, backend)

    def fake_unified(
        experiment, backend_, plan=None, work_dir=None, base_params=None, progress=None
    ):
        return {
            "spectrum_path": str(Path(work) / "d_001.ft3"),
            "phases": {"F1": (0.0, 0.0), "F2": (0.0, 0.0), "F3": (0.0, 0.0)},
            "backend_runs": 1,
            "logs": [],
        }

    def fake_project_3d(spectrum_path, out_dir, *, prefix="proj", timeout=900, labels=None):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = {}
        for tag in ("xy", "xz", "yz"):
            path = out_dir / f"{prefix}_{tag}.ft2"
            path.write_bytes(b"x")
            paths[tag] = str(path)
        return {"paths": paths, "labels": {"xy": "15N", "xz": "1H", "yz": "13C"}}

    monkeypatch.setattr(phase_routes, "unified_route", fake_unified)
    backend.project_3d = fake_project_3d  # type: ignore[method-assign]
    generate_spectrum(manager, exp_id, data_id, backend)
    spectra_dir = manager.data_dir(exp_id, data_id, "spectra")
    assert sorted(p.name for p in spectra_dir.glob("d_001_proj_*.ft2")) == [
        "d_001_proj_F1.ft2",
        "d_001_proj_F2.ft2",
        "d_001_proj_F3.ft2",
    ]


def test_generate_spectrum_defaults_to_unified_route(
    tmp_path: Path, bruker_dir: Path, monkeypatch
) -> None:
    """Default phase_route=unified, generate spectrum using a unified solution (duplicate preview +
    memory phase modulation + final run)."""
    import workflow.phase_routes as phase_routes

    manager, exp_id, data_id, work = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d"
    )
    backend = _FakeBackend(work)
    generate_fid(manager, exp_id, data_id, backend)
    seen = {}

    def fake_unified(
        experiment, backend_, plan=None, work_dir=None, base_params=None, progress=None
    ):
        seen["base_params"] = base_params
        return {
            "spectrum_path": str(Path(work) / "unified.ft2"),
            "phases": {"F1": (0.0, 0.0), "F2": (10.0, 0.0)},
            "backend_runs": 3,
            "logs": [],
        }

    monkeypatch.setattr(phase_routes, "unified_route", fake_unified)
    spectrum = generate_spectrum(manager, exp_id, data_id, backend)
    # 2026-08-19: The final spectrum naming prefix is data id(d_001).
    assert spectrum.endswith("d_001.ft2")
    assert seen["base_params"] == {}
    assert any(r.workflow_ref == "phase_optimize_unified" for r in manager.project.workflow_runs)


def test_generate_spectrum_falls_back_when_replica_preview_fails(
    tmp_path: Path, bruker_dir: Path, monkeypatch
) -> None:
    """0.2.199-patch29gy: when the unified phase replication preview fails, fall back to
    phase_route=none instead of aborting; the fallback must recognise the message the phase
    routes really raise."""
    import workflow.phase_routes as phase_routes

    manager, exp_id, data_id, work = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d"
    )
    backend = _FakeBackend(work)
    generate_fid(manager, exp_id, data_id, backend)

    def failing_unified(
        experiment, backend_, plan=None, work_dir=None, base_params=None, progress=None
    ):
        # The phase routes really raise this wording; the escape hatch matches it verbatim.
        raise RuntimeError(tr("Replica preview ({p0}) failed: {p1}", p0="F1", p1="broken pipe"))

    monkeypatch.setattr(phase_routes, "unified_route", failing_unified)
    spectrum = generate_spectrum(manager, exp_id, data_id, backend)
    assert spectrum.endswith("d_001.ft2")


def test_generate_spectrum_unknown_route_raises(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Old simple/advanced dispatch deleted, unknown phase_route throws error."""
    manager, exp_id, data_id, work = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d"
    )
    backend = _FakeBackend(work)
    generate_fid(manager, exp_id, data_id, backend)
    with pytest.raises(StepwiseError, match="Unknown phase_route"):
        generate_spectrum(
            manager, exp_id, data_id, backend, params={"phase_route": "advanced"}
        )


def test_generate_fid_failure_raises(tmp_path: Path, bruker_dir: Path) -> None:
    manager, exp_id, data_id, work = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d"
    )
    backend = _FakeBackend(work, success=False)
    with pytest.raises(StepwiseError, match="Conversion failed"):
        generate_fid(manager, exp_id, data_id, backend)


def _score_from_path(path: str) -> tuple[float, dict[str, float]]:
    p0 = float(path.split("_p0")[1].split("_")[0])
    p1 = float(path.split("_p1")[1].split(".")[0])
    # P0 is a weak dimension (the impact of p0 on the real phase score is small but non-zero).
    return 100.0 - abs(p1 - 30.0) - 0.02 * abs(p0), {"snr": 0.0}



def test_read_experiment_prefers_raw_copy(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """When raw_dir exists, read the complex data within the project first."""
    from workflow.import_workflow import import_data
    from workflow.stepwise import _read_experiment

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    result = import_data(manager, entry.id, bruker_dir / "hsqc_2d")
    exp = _read_experiment(manager, entry.id, result.data_id)
    raw_dir = manager.data(entry.id, result.data_id).raw_dir
    assert exp.source_path == manager.root / raw_dir
    assert read_dataset(Path(result.raw_dir)).ndim == 2

def test_rewrite_duplicate_nucleus_labels(tmp_path: Path) -> None:
    """0.2.199-patch29af: Double 15N (HNN/NNH) label uniqueization -- F2 -> 15Nx, F1 -> 15Ny."""
    import nmrglue as ng
    import numpy as np
    from nmrglue.fileio import pipe as ngpipe

    from core.data.internal_data_model import (
        AxisRole,
        Dimension,
        Experiment,
        ExperimentType,
        Sampling,
        SamplingMode,
    )
    from workflow.stepwise import _rewrite_duplicate_nucleus_labels

    data = np.zeros((16, 16, 32), dtype=np.float32)
    dic = {k: "0" for k in ngpipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 3
    dic["FDPIPEFLAG"] = 1  # 3D Single file stream.
    dic["FDSIZE"] = 32
    dic["FDSPECNUM"] = 16
    dic["FDF3SIZE"] = 16
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDF3QUADFLAG"] = 1
    dic["FDDIMORDER"] = [2.0, 3.0, 1.0]
    # Position formula: FDF1=Axis 0(F2), FDF2=Axis 1(F1), FDF3=Axis 2(F3); FDDIMORDER will be
    # cleared after writing to the disk, and the reading end will return to the position formula, so
    # the direct positions are consistent.
    dic["FDF1LABEL"] = "15N"
    dic["FDF2LABEL"] = "15N"
    dic["FDF3LABEL"] = "1H"
    path = tmp_path / "dup.ft3"
    ngpipe.write(str(path), dic, data, overwrite=True)

    dims = [
        Dimension(logical_axis="F3", role=AxisRole.DIRECT, nucleus="1H"),
        Dimension(logical_axis="F2", role=AxisRole.INDIRECT, nucleus="15N"),
        Dimension(logical_axis="F1", role=AxisRole.INDIRECT, nucleus="15N"),
    ]
    exp = Experiment(
        dataset_id="x",
        source_path=str(tmp_path),
        dimensions=dims,
        acquisition_order=["F3", "F2", "F1"],
        sampling=Sampling(mode=SamplingMode.NUS),
        experiment_type=ExperimentType(name="HNN", confidence=1.0),
        ndim=3,
    )
    assert _rewrite_duplicate_nucleus_labels(str(path), exp) is True
    rdic, _ = ng.pipe.read(str(path))
    assert rdic.get("FDF1LABEL") == "15Nx"  # F2 → Nx(HSQC Of N).
    assert rdic.get("FDF2LABEL") == "15Ny"  # F1 → Ny
    assert rdic.get("FDF3LABEL") == "1H"


def test_rewrite_duplicate_nucleus_labels_2d(tmp_path: Path) -> None:
    """0.2.199-patch29ag: 2D double 1H label uniqueization -- direct dimension F2 -> Hx, indirect
    dimension F1 -> Hy."""
    import nmrglue as ng
    import numpy as np
    from nmrglue.fileio import pipe as ngpipe

    from core.data.internal_data_model import (
        AxisRole,
        Dimension,
        Experiment,
        ExperimentType,
        Sampling,
        SamplingMode,
    )
    from workflow.stepwise import _rewrite_duplicate_nucleus_labels

    data = np.zeros((16, 32), dtype=np.float32)
    dic = {k: "0" for k in ngpipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = 32
    dic["FDSPECNUM"] = 16
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDF1LABEL"] = "1H"
    dic["FDF2LABEL"] = "1H"
    path = tmp_path / "dup2.ft2"
    ngpipe.write(str(path), dic, data, overwrite=True)

    dims = [
        Dimension(logical_axis="F2", role=AxisRole.DIRECT, nucleus="1H"),
        Dimension(logical_axis="F1", role=AxisRole.INDIRECT, nucleus="1H"),
    ]
    exp = Experiment(
        dataset_id="x",
        source_path=str(tmp_path),
        dimensions=dims,
        acquisition_order=["F2", "F1"],
        sampling=Sampling(mode=SamplingMode.UNIFORM),
        experiment_type=ExperimentType(name="TOCSY", confidence=1.0),
        ndim=2,
    )
    assert _rewrite_duplicate_nucleus_labels(str(path), exp) is True
    rdic, _ = ng.pipe.read(str(path))
    assert rdic.get("FDF1LABEL") == "1Hy"  # Indirect dimension F1 -> Hy (display layer goes to 1).
    assert rdic.get("FDF2LABEL") == "1Hx"  # Direct dimension F2 -> Hx (display layer goes to 1).

def test_rewrite_duplicate_nucleus_labels_3d_triple(tmp_path: Path) -> None:
    """0.2.199-patch29ah: 3D triple homonuclear (1H-1H-1H) label uniqueization -- direct dimension
    F3 -> 1Hx, F2(acqu2) -> 1Hy, F1(acqu3) -> 1Hz."""
    import nmrglue as ng
    import numpy as np
    from nmrglue.fileio import pipe as ngpipe

    from core.data.internal_data_model import (
        AxisRole,
        Dimension,
        Experiment,
        ExperimentType,
        Sampling,
        SamplingMode,
    )
    from workflow.stepwise import _rewrite_duplicate_nucleus_labels

    data = np.zeros((8, 8, 16), dtype=np.float32)
    dic = {k: "0" for k in ngpipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 3
    dic["FDPIPEFLAG"] = 1
    dic["FDSIZE"] = 16
    dic["FDSPECNUM"] = 8
    dic["FDF3SIZE"] = 8
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDF3QUADFLAG"] = 1
    dic["FDF1LABEL"] = "1H"
    dic["FDF2LABEL"] = "1H"
    dic["FDF3LABEL"] = "1H"
    path = tmp_path / "dup3.ft3"
    ngpipe.write(str(path), dic, data, overwrite=True)

    dims = [
        Dimension(logical_axis="F3", role=AxisRole.DIRECT, nucleus="1H"),
        Dimension(logical_axis="F2", role=AxisRole.INDIRECT, nucleus="1H"),
        Dimension(logical_axis="F1", role=AxisRole.INDIRECT, nucleus="1H"),
    ]
    exp = Experiment(
        dataset_id="x",
        source_path=str(tmp_path),
        dimensions=dims,
        acquisition_order=["F3", "F2", "F1"],
        sampling=Sampling(mode=SamplingMode.UNIFORM),
        experiment_type=ExperimentType(name="NOESY", confidence=1.0),
        ndim=3,
    )
    assert _rewrite_duplicate_nucleus_labels(str(path), exp) is True
    rdic, _ = ng.pipe.read(str(path))
    assert rdic.get("FDF1LABEL") == "1Hy"  # F2(acqu2)→1Hy
    assert rdic.get("FDF2LABEL") == "1Hz"  # F1(acqu3)→1Hz
    assert rdic.get("FDF3LABEL") == "1Hx"  # Direct dimension F3 -> 1Hx.


def test_generate_spectrum_cleans_intermediates_on_error(
    tmp_path: Path, bruker_dir: Path, monkeypatch
) -> None:
    """Interruption residue (0.2.199-patch29gi): Clean up the last residue before running; finally
    clean up when exception occurs. When unified routing throws an error in the middle,
    intermediate products such as preview/joint/nus3d_* are not left; reserved items
    (process/fid, etc.) are not affected."""
    import workflow.phase_routes as phase_routes

    manager, exp_id, data_id, work = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d"
    )
    backend = _FakeBackend(work)
    generate_fid(manager, exp_id, data_id, backend)
    proc = manager.data_dir(exp_id, data_id, "process")
    fallback = proc.parent / f"{data_id}.nmrpipe"

    # Remains of the last interruption (should be deleted by cleaning before this run).
    stale = [
        proc / "nus3d_rc" / "test0001.ft1",
        proc / f"{data_id}_preview_F1.ft2",
        proc / f"{data_id}_joint.ft3",
        proc / "_intermediate" / "prev.ft2",
    ]
    for p in stale:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    fallback_file = fallback / f"{data_id}_preview_F2.ft2"
    fallback_file.parent.mkdir(parents=True, exist_ok=True)
    fallback_file.write_text("x")

    def fake_unified(
        experiment, backend_, plan=None, work_dir=None, base_params=None, progress=None
    ):
        # The cleanup before running should have deleted the last remnants (_intermediate will be
        # rebuilt as an empty directory).
        assert not (proc / "nus3d_rc").exists()
        assert not (proc / f"{data_id}_preview_F1.ft2").exists()
        assert not (proc / f"{data_id}_joint.ft3").exists()
        assert not (proc / "_intermediate" / "prev.ft2").exists()
        assert not fallback.exists()
        # Simulate the intermediate products left before the failure of this run.
        created = [
            proc / "nus3d_1" / "stage1.ft1",
            proc / "nus3d_rc" / "test0001.ft1",
            proc / f"{data_id}_preview_F1.ft2",
            proc / f"{data_id}_joint.ft3",
            proc / "_intermediate" / "cur.ft2",
        ]
        for p in created:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")
        raise RuntimeError("Simulation failed midway")

    monkeypatch.setattr(phase_routes, "unified_route", fake_unified)
    with pytest.raises(RuntimeError, match="Simulation failed midway"):
        generate_spectrum(manager, exp_id, data_id, backend)

    # Finally cleaning: the remnants of this failure have been deleted.
    assert not (proc / "nus3d_1").exists()
    assert not (proc / "nus3d_rc").exists()
    assert not (proc / f"{data_id}_preview_F1.ft2").exists()
    assert not (proc / f"{data_id}_joint.ft3").exists()
    assert not (proc / "_intermediate").exists()
    # Reserved items are not affected (fid is written to process/ by generate_fid).
    assert (proc / f"{data_id}.fid").exists()

