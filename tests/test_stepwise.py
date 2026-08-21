"""步骤化处理测试:generate_fid / generate_spectrum / 相位暴力优化。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.data.bruker_reader import read_dataset
from core.project import ExperimentStatus, ProjectManager
from workflow.stepwise import (
    StepwiseError,
    generate_fid,
    generate_spectrum,
)


class _FakeBackend:
    """记录调用的假后端(convert_to_fid / process / reconstruct_nus / project_3d)。"""

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
            return {"success": False, "message": "转换失败", "logs": []}
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
            # 逐维搜索时覆盖含多个轴,取末轴(正在搜索的轴)的 p0/p1
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
        """按 0.2.133 实测几何返回三个投影:xy=(固定第三轴 F3,F1)、
        xz=(F2,F1)、yz=(F2,F3),头为平面实际两核。"""
        self.calls.append("project_3d")
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        by_axis = {
            dim.logical_axis: dim.nucleus for dim in self.experiment.dimensions
        }
        pairs = {  # 生产流几何(实测):xy=(F3,F1), xz=(F2,F1), yz=(F2,F3)
            "xy": (by_axis["F3"], by_axis["F1"]),
            "xz": (by_axis["F2"], by_axis["F1"]),
            "yz": (by_axis["F2"], by_axis["F3"]),
        }
        fixed = {  # 被求和的第三轴核
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
    # 契约 §9.2:fid 落盘 data_dir(..., "process")
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
    # 契约 §9.2:终谱落盘 data_dir(..., "spectra")
    assert Path(spectrum).parent == manager.data_dir(exp_id, data_id, "spectra")
    # G2B-009:终谱只存 spectra/,process/ 不留副本
    assert not (manager.data_dir(exp_id, data_id, "process") / Path(spectrum).name).exists()
    data = manager.data(exp_id, data_id)
    assert data.spectrum_path == spectrum
    assert data.status == "processed"
    assert "process" in backend.calls
    assert manager.infer_status(exp_id) is ExperimentStatus.PROCESSED


def test_generate_spectrum_passes_params(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """均匀分支把 params 透传给 backend.process(G2B-006)。"""
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
    """0.2.133:3D 投影文件名含平面实际两核,注册按固定轴逻辑轴;兼容旧名。"""
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
    # nus_3d fixture 逻辑轴:F1=13C, F2=15N, F3=1H(direct)
    spectra_dir = manager.data_dir(exp_id, data_id, "spectra")
    assert sorted(p.name for p in spectra_dir.glob("d_001_*.ft2")) == [
        "d_001_15N-13C.ft2",
        "d_001_15N-1H.ft2",
        "d_001_1H-13C.ft2",
    ]
    # 无旧式 *_proj_*.ft2 名
    assert not list(spectra_dir.glob("d_001_proj_*.ft2"))
    run = next(
        r
        for r in reversed(manager.project.workflow_runs)
        if r.workflow_ref == "phase_optimize_unified"
    )
    projections = run.params.get("projections", {})
    # 注册键 = 被求和第三轴(logical):xy→F2(15N)、xz→F3(1H)、yz→F1(13C)
    assert projections["F2"].endswith("d_001_1H-13C.ft2")
    assert projections["F3"].endswith("d_001_15N-13C.ft2")
    assert projections["F1"].endswith("d_001_15N-1H.ft2")


def test_generate_spectrum_3d_projections_fallback_old_naming(
    tmp_path: Path, bruker_dir: Path, monkeypatch
) -> None:
    """后端未返回 nuclei 时回退 d_001_proj_<logical|tag>.ft2(兼容旧名)。"""
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
    """默认 phase_route=unified,生成谱图走统一方案(复型预览+内存调相+终跑)。"""
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
    # 2026-08-19:终谱命名前缀为数据 id(d_001)
    assert spectrum.endswith("d_001.ft2")
    assert seen["base_params"] == {}
    assert any(r.workflow_ref == "phase_optimize_unified" for r in manager.project.workflow_runs)


def test_generate_spectrum_unknown_route_raises(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """旧 simple/advanced 分派已删除,未知 phase_route 抛错。"""
    manager, exp_id, data_id, work = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d"
    )
    backend = _FakeBackend(work)
    generate_fid(manager, exp_id, data_id, backend)
    with pytest.raises(StepwiseError, match="未知 phase_route"):
        generate_spectrum(
            manager, exp_id, data_id, backend, params={"phase_route": "advanced"}
        )


def test_generate_fid_failure_raises(tmp_path: Path, bruker_dir: Path) -> None:
    manager, exp_id, data_id, work = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d"
    )
    backend = _FakeBackend(work, success=False)
    with pytest.raises(StepwiseError, match="转换失败"):
        generate_fid(manager, exp_id, data_id, backend)


def _score_from_path(path: str) -> tuple[float, dict[str, float]]:
    p0 = float(path.split("_p0")[1].split("_")[0])
    p1 = float(path.split("_p1")[1].split(".")[0])
    # p0 为弱维度(真实相位评分中 p0 影响小但非零)
    return 100.0 - abs(p1 - 30.0) - 0.02 * abs(p0), {"snr": 0.0}



def test_read_experiment_prefers_raw_copy(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """raw_dir 存在时优先读项目内副本。"""
    from workflow.import_workflow import import_data
    from workflow.stepwise import _read_experiment

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    result = import_data(manager, entry.id, bruker_dir / "hsqc_2d")
    exp = _read_experiment(manager, entry.id, result.data_id)
    raw_dir = manager.data(entry.id, result.data_id).raw_dir
    assert exp.source_path == manager.root / raw_dir
    assert read_dataset(Path(result.raw_dir)).ndim == 2


