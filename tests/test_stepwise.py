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
    optimize_phase_brute_force,
)


class _FakeBackend:
    """记录调用的假后端(convert_to_fid / process / reconstruct_nus)。"""

    def __init__(self, work_dir: Path, *, success: bool = True) -> None:
        self.work_dir = str(work_dir)
        self.success = success
        self.calls: list[str] = []

    def _touch(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    def convert_to_fid(self, experiment, data_dir) -> dict:
        self.calls.append("convert_to_fid")
        if not self.success:
            return {"success": False, "message": "转换失败", "logs": []}
        fid_path = Path(self.work_dir) / f"{experiment.dataset_id}.fid"
        self._touch(fid_path)
        return {"success": True, "fid_path": str(fid_path), "message": "ok", "logs": []}

    def process(
        self, experiment, plan, direct_phase_override=None, params=None
    ) -> dict:
        self.calls.append("process")
        self.last_params = params
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

    def reconstruct_nus(self, experiment, params) -> dict:
        self.calls.append("reconstruct_nus")
        spectrum = Path(self.work_dir) / "out_nus.ft2"
        self._touch(spectrum)
        return {
            "success": True,
            "spectrum_path": str(spectrum),
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
    spectrum = generate_spectrum(manager, exp_id, data_id, backend)
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
        params={"extract": False, "ext_lo": "9.0"},
    )
    assert backend.last_params == {"extract": False, "ext_lo": "9.0"}


def test_generate_spectrum_nus_uses_reconstruct(tmp_path: Path, bruker_dir: Path) -> None:
    manager, exp_id, data_id, work = _manager_with_data(
        tmp_path, bruker_dir / "nus_2d"
    )
    backend = _FakeBackend(work)
    generate_fid(manager, exp_id, data_id, backend)
    generate_spectrum(manager, exp_id, data_id, backend)
    assert "reconstruct_nus" in backend.calls
    assert any(r.workflow_ref == "reconstruct_nus" for r in manager.project.workflow_runs)


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


def test_optimize_phase_brute_force(tmp_path: Path, bruker_dir: Path) -> None:
    """相位优化:先 SMILE 重构(生成谱),再逐候选反复跑后端(暴力)。"""
    manager, exp_id, data_id, work = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d"
    )
    backend = _FakeBackend(work)
    result = optimize_phase_brute_force(
        manager,
        exp_id,
        data_id,
        backend,
        score_fn=_score_from_path,
    )
    assert result["method"] == "sequential_brute_force"
    # 逐维暴力:直接维 F2 → 间接维 F1,各粗 21 候选 + 多尺度细化(默认 5°)
    assert result["phase"]["F2"][1] == 30.0
    assert result["phase"]["F1"][1] == 30.0
    assert result["spectrum_path"].endswith("out_p00_p130.ft2")
    assert backend.calls.count("process") >= 42
    assert result["optimized"] == ["F2", "F1"]
    assert result["skipped"] == []
    data = manager.data(exp_id, data_id)
    assert data.spectrum_path == result["spectrum_path"]
    assert any(r.workflow_ref == "phase_optimize" for r in manager.project.workflow_runs)


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

def test_optimize_phase_brute_force_embeds_baseline(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """嵌入基线优化:最优谱内存内优化基线(0 次后端),配置变化时重渲 1 次。"""
    import numpy as np

    manager, exp_id, data_id, work = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d"
    )

    class _Ft2Backend(_FakeBackend):
        def _touch(self, path: Path) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # 曲率基线:auto(order 1)修不掉,order 2 能修 → 触发基线重渲
            x = np.linspace(-1.0, 1.0, 64)
            data = np.zeros((32, 64))
            data += (x**2) * 120.0
            data[16, 30] = 500.0
            from nmrglue.fileio import pipe

            dic = {k: "0" for k in pipe.fdata_dic}
            dic["FDMAGIC"] = 9.2330230000000007e14
            dic["FDDIMCOUNT"] = 2
            dic["FDSIZE"] = 64
            dic["FDSPECNUM"] = 32
            dic["FDQUADFLAG"] = 1
            dic["FDF1QUADFLAG"] = 1
            dic["FDF2QUADFLAG"] = 1
            for prefix in ("FDF1", "FDF2"):
                dic[prefix + "SW"] = "6000.0"
                dic[prefix + "OBS"] = "600.0"
                dic[prefix + "CAR"] = "4.7"
                dic[prefix + "ORIG"] = "1000.0"
            pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)

    backend = _Ft2Backend(work)
    result = optimize_phase_brute_force(
        manager, exp_id, data_id, backend, score_fn=_score_from_path
    )
    assert result["baseline"] is not None
    assert "F2" in result["baseline"]["optimized"]  # 曲率 → order 2 校正
    assert result["baseline"]["config"]["F2"]["mode"] == "order"
    # 基线配置变化 → 以「最优相位+最优基线」重渲 1 次
    assert backend.calls.count("process") >= 42 + 1
    assert any("基线(嵌入)" in line for line in result["logs"])

