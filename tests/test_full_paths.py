"""全路径端到端回归测试(0.2.163-补8)。

每次修改代码后必须运行本文件:完整模拟走一遍已有所有路径——
自动(2D/3D uniform + NUS)、人工(fid.com/谱图脚本)、批量(数据组)。

策略:FakeBackend 模拟 NMRPipe 产物;谱读取经 monkeypatch 注入合成数组,
验证流程完整性、产物归位、WorkflowRun 登记、优化/诊断阶段真实执行
(fid 用可读格式,诊断/直接维窗优化读真实文件;基线/填零/预览用注入数组)。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import workflow.phase_routes as routes
from core.project import ProjectManager


def _synthetic_preview(axis: int, p0: float) -> np.ndarray:
    """复型 Lorentzian:被评轴复型 + 已知相位,其它轴实型(与 phase_routes 测试同构)。"""
    n0, n1 = 96, 80
    k0 = np.arange(n0, dtype=float)
    k1 = np.arange(n1, dtype=float)
    width = 1.5
    arr = np.zeros((n0, n1), dtype=np.complex128)
    for c0, c1, amp in ((n0 * 0.35, n1 * 0.45, 400.0), (n0 * 0.62, n1 * 0.58, 320.0)):
        z0 = 1.0 / (1.0 + 1j * (k0 - c0) / width)
        z1 = 1.0 / (1.0 + 1j * (k1 - c1) / width)
        r0 = 1.0 / (1.0 + ((k0 - c0) / width) ** 2)
        r1 = 1.0 / (1.0 + ((k1 - c1) / width) ** 2)
        if axis == 0:
            arr += amp * np.outer(z0, r1)
        else:
            arr += amp * np.outer(r0, z1)
    n = arr.shape[axis]
    ramp = np.exp(1j * np.deg2rad(p0))
    shape = [1] * arr.ndim
    shape[axis] = n
    return arr * ramp


def _spectrum_array() -> np.ndarray:
    """可评分的合成谱(带峰)。"""
    arr = np.zeros((64, 128), dtype=float)
    arr[30, 60] = 1000.0
    arr[20, 70] = 600.0
    return arr


class FakeBackend:
    """模拟 NMRPipe 后端:写真实可读 fid;谱路径/预览经 monkeypatch 提供。"""

    def __init__(self, work: Path) -> None:
        self.work = Path(work)
        self.work.mkdir(parents=True, exist_ok=True)
        self.process_calls: list[tuple] = []
        self.reconstruct_params: list[dict] = []
        self.finalize_calls: list[dict] = []

    def _touch(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return path

    def convert_to_fid(
        self, experiment, data_dir, progress=None
    ) -> dict:
        """写真实可读 fid(诊断/直接维窗优化可读)与 fid.com(人工读取)。"""
        work = self.work
        dataset_id = experiment.dataset_id
        work.mkdir(parents=True, exist_ok=True)
        (work / "fid.com").write_text(
            "#!/bin/csh\n# auto fid.com\n", encoding="utf-8"
        )
        n_direct = 128
        n_traces = 64
        if experiment.ndim >= 3:
            slice_dir = work / "fid"
            slice_dir.mkdir(parents=True, exist_ok=True)
            for i in range(4):
                self._write_fid_file(slice_dir / f"test{i + 1:03d}.fid", n_traces, n_direct)
            return {
                "success": True,
                "fid_path": str(slice_dir),
                "message": "ok",
                "logs": [f"切片式 fid: {slice_dir}"],
                "effective_params": {"dataset_id": dataset_id, "ndim": experiment.ndim},
            }
        fid_path = work / f"{dataset_id}.fid"
        self._write_fid_file(fid_path, n_traces, n_direct)
        return {
            "success": True,
            "fid_path": str(fid_path),
            "message": "ok",
            "logs": [],
            "effective_params": {"dataset_id": dataset_id, "ndim": experiment.ndim},
        }

    @staticmethod
    def _write_spectrum_file(path: Path, ndim: int = 2) -> None:
        """写可读 pipe 谱(实型 + 有效头),供峰挑选/质量评分读取。"""
        import nmrglue as ng
        import nmrglue.fileio.pipe as pmod

        dic = {k: 0.0 for k in pmod.fdata_nums}
        n1, n2, n3 = 128, 64, 16
        dic.update(
            {
                "FDSIZE": n1,
                "FDSPECNUM": n2 if ndim == 2 else n2 * n3,
                "FDDIMCOUNT": ndim,
                "FDF2SIZE": n1,
                "FDF1SIZE": n2,
                "FDF3SIZE": n3,
                "FDF2SW": 8000.0,
                "FDF1SW": 2000.0,
                "FDF2OBS": 600.0,
                "FDF1OBS": 60.0,
                "FDF2CAR": 4.7,
                "FDF1CAR": 118.0,
                "FDF2QUADFLAG": 1,
                "FDF1QUADFLAG": 1,
                "FDF2APOD": 0.0,
                "FDF1APOD": 0.0,
                "FDF2FTFLAG": 1,
                "FDF1FTFLAG": 1,
                "FDTRANSPOSED": 0,
                "FDPIPEFLAG": 0,
            }
        )
        for i, lab in enumerate(
            ("FDF2LABEL", "FDF1LABEL", "FDF3LABEL", "FDF4LABEL")
        ):
            dic[lab] = ("1H", "15N", "13C", "")[i]
        dic.update(
            {
                "FDUSERNAME": "test",
                "FDTITLE": "test",
                "FDCOMMENT": "test",
                "FDOPERNAME": "test",
                "FDSRCNAME": "test",
                "FDDIMORDER1": 0.0,
            }
        )
        if ndim == 2:
            data = np.zeros((n2, n1), dtype="<f4")
        else:
            data = np.zeros((n2, n3, n1), dtype="<f4")
        # 放几个峰
        if ndim == 2:
            data[30, 60] = 1000.0
            data[20, 70] = 600.0
        else:
            data[4, 10, 30] = 1000.0
            data[6, 8, 50] = 600.0
        ng.pipe.write(str(path), dic, np.ascontiguousarray(data), overwrite=True)

    def _write_fid_file(self, path: Path, n_traces: int, n_direct: int) -> None:
        """写 nmrglue 可读 fid:512B 头 + (n_traces, n_direct) 复型交错。"""
        import nmrglue as ng
        import nmrglue.fileio.pipe as pmod

        dic = {k: 0.0 for k in pmod.fdata_nums}
        dic.update(
            {
                "FDSIZE": n_direct,
                "FDSPECNUM": n_traces,
                "FDDIMCOUNT": 2,
                "FDF2SIZE": n_direct,
                "FDF1SIZE": n_traces,
                "FDF2SW": 8000.0,
                "FDF1SW": 2000.0,
                "FDF2OBS": 600.0,
                "FDF1OBS": 60.0,
                "FDF2CAR": 4.7,
                "FDF1CAR": 118.0,
                "FDF2P0": 0.0,
                "FDF2P1": 0.0,
                "FDF1P0": 0.0,
                "FDF1P1": 0.0,
                "FDF2QUADFLAG": 1,
                "FDF1QUADFLAG": 1,
                "FDF2APOD": 0.0,
                "FDF1APOD": 0.0,
                "FDF2FTFLAG": 1,
                "FDF1FTFLAG": 1,
                "FDTRANSPOSED": 0,
            }
        )
        for i, lab in enumerate(("FDF2LABEL", "FDF1LABEL", "FDF3LABEL", "FDF4LABEL")):
            dic[lab] = ("1H", "15N", "", "")[i]
        dic.update(
            {
                "FDUSERNAME": "test",
                "FDTITLE": "test",
                "FDCOMMENT": "test",
                "FDOPERNAME": "test",
                "FDSRCNAME": "test",
                "FDDIMORDER1": 0.0,
            }
        )
        data = np.zeros((n_traces, n_direct), dtype=np.complex64)
        data[3, 20] = 100 + 50j
        data[5, 40] = 80 + 20j
        # 复型 → 实/虚交错实型(第一轴翻倍),匹配 NMRPipe fid 布局
        inter = np.empty((2 * n_traces, n_direct), dtype="<f4")
        inter[0::2] = data.real.astype("<f4")
        inter[1::2] = data.imag.astype("<f4")
        dic["FDSIZE"] = n_direct
        dic["FDSPECNUM"] = 2 * n_traces
        dic["FDF1SIZE"] = 2 * n_traces
        ng.pipe.write(str(path), dic, inter, overwrite=True)

    def process(
        self,
        experiment,
        plan,
        params=None,
        direct_phase_override=None,
        out_file=None,
        script_name=None,
        progress=None,
    ) -> dict:
        params = dict(params or {})
        self.process_calls.append(
            (experiment, plan, params, dict(direct_phase_override or {}))
        )
        ext = "ft3" if experiment.ndim >= 3 else "ft2"
        name = out_file or f"{experiment.dataset_id}.{ext}"
        path = self.work / name
        self._write_spectrum_file(path, ndim=experiment.ndim)
        return {"success": True, "spectrum_path": str(path), "logs": []}

    def reconstruct_nus(self, experiment, params, progress=None) -> dict:
        self.reconstruct_params.append(dict(params or {}))
        # 写重构平面(2D nus2d/recon.ft1;3D nus3d_rc/test*.ft1),供 finalize 消费
        if experiment.ndim >= 3:
            plane_dir = self.work / "nus3d_rc"
            plane_dir.mkdir(parents=True, exist_ok=True)
            for i in range(4):
                self._write_spectrum_file(plane_dir / f"test{i + 1:04d}.ft1", ndim=2)
        else:
            recon = self.work / "nus2d" / "recon.ft1"
            self._write_spectrum_file(recon, ndim=2)
        ext = "ft3" if experiment.ndim >= 3 else "ft2"
        path = self.work / f"{experiment.dataset_id}.{ext}"
        self._write_spectrum_file(path, ndim=experiment.ndim)
        return {"success": True, "spectrum_path": str(path), "logs": []}

    def finalize_nus(
        self,
        experiment,
        phases=None,
        work_dir=None,
        planes=None,
        params=None,
        out_file=None,
        script_name=None,
        progress=None,
    ) -> dict:
        self.finalize_calls.append(
            {
                "phases": dict(phases or {}),
                "planes": planes,
                "params": dict(params or {}),
            }
        )
        path = self.work / (out_file or "final.ft2")
        self._write_spectrum_file(path, ndim=experiment.ndim)
        return {"success": True, "spectrum_path": str(path), "logs": []}


def _manager_with_data(
    tmp_path: Path, bruker_dir: Path, dataset: str, exp_title: str = "HSQC"
) -> tuple[ProjectManager, str, str, Path]:
    raw = tmp_path / f"src_{dataset}"
    shutil.copytree(bruker_dir / dataset, raw)
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment(exp_title)
    data = manager.import_data(entry.id, str(raw))
    manager.save()
    return manager, entry.id, data.id, raw


def _install_spectrum_mocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """注入合成预览/谱读取(流程真实,文件读取层模拟)。"""

    def _preview_3d(axis: int, p0: float) -> np.ndarray:
        arr = np.zeros((24, 32, 40), dtype=np.complex128)
        arr[4, 10, 18] = 500.0
        n = arr.shape[axis]
        ramp = np.exp(1j * np.deg2rad(p0))
        shape = [1] * arr.ndim
        shape[axis] = n
        return arr * ramp

    def fake_read(path: str, unpack_axis: int | None = None):
        name = Path(path).name
        is_3d = "ft3" in name or "_F1_" in name or "_F2_" in name
        if is_3d:
            if "F1" in name:
                return _preview_3d(0, -25.0)
            if "F2" in name:
                return _preview_3d(1, -35.0)
            return _preview_3d(2, 0.0)
        if "F1" in name:
            return _synthetic_preview(0, -25.0)
        if "F2" in name:
            return _synthetic_preview(1, -35.0)
        return _synthetic_preview(0, 0.0)

    monkeypatch.setattr(routes, "_read_complex_preview", fake_read)

    def fake_read_pipe_complex(path):
        name = Path(path).name
        if "ft3" in name or ".ft1" in name:
            return _preview_3d(0, 0.0)
        return _synthetic_preview(0, 0.0)

    monkeypatch.setattr(
        "core.data.pipe_io.read_pipe_complex", fake_read_pipe_complex
    )
    # 基线/填零评分:谱文件字节不可读 → 直接读合成数组
    import core.data.pipe_io

    monkeypatch.setattr(core.data.pipe_io, "read_pipe_complex", fake_read_pipe_complex)


# ----------------------------------------------------------------------
# 自动路径
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("dataset", "exp_title"),
    [
        ("hsqc_2d", "HSQC"),  # 2D uniform
        ("nus_2d", "NUS 2D"),  # 2D NUS
        ("hnca_3d", "HNCA"),  # 3D uniform(切片)
        ("nus_3d", "NUS 3D"),  # 3D NUS(切片)
    ],
)
def test_auto_full_path(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch,
    dataset: str, exp_title: str,
) -> None:
    """自动路径:导入 → 生成 FID → 生成谱图(unified,诊断+优化)→ 峰挑选 → 分析。"""
    from workflow.analyze import analyze
    from workflow.pick_peaks import pick_peaks
    from workflow.stepwise import generate_fid, generate_spectrum

    _install_spectrum_mocks(monkeypatch)
    manager, exp_id, data_id, _raw = _manager_with_data(
        tmp_path, bruker_dir, dataset, exp_title
    )
    backend = FakeBackend(manager.data_dir(exp_id, data_id, "process"))
    fid_path = generate_fid(manager, exp_id, data_id, backend)
    assert fid_path
    data = manager.data(exp_id, data_id)
    assert data.status == "fid_ready"
    # 切片 fid 存在性:单文件或切片目录
    fid = Path(data.fid_path)
    assert fid.is_file() or (fid.is_dir() and list(fid.glob("test*.fid")))

    spec = generate_spectrum(manager, exp_id, data_id, backend)
    assert spec
    data = manager.data(exp_id, data_id)
    assert data.status == "processed"
    assert any(
        r.workflow_ref == "phase_optimize_unified" and r.status == "success"
        for r in manager.project.workflow_runs
    )

    peaks = pick_peaks(manager, exp_id, data_id)
    assert peaks.get("status") == "success"
    analysis = analyze(manager, exp_id, data_id)
    assert analysis.get("status") in ("success", "pending")
    assert any(
        r.workflow_ref in ("analyze", "pick_peaks") and r.status == "success"
        for r in manager.project.workflow_runs
    )


def test_auto_uniform_runs_processing_optimization(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """uniform 处理参数优化:诊断 + 基线 + 直接维窗 + 填零/间接窗进终跑。"""
    from workflow.baseline_optimize import BaselineOptimizeResult
    from workflow.stepwise import generate_fid, generate_spectrum
    from workflow.window_optimize import WindowOptimizeResult

    _install_spectrum_mocks(monkeypatch)
    manager, exp_id, data_id, _raw = _manager_with_data(
        tmp_path, bruker_dir, "hsqc_2d"
    )
    backend = FakeBackend(manager.data_dir(exp_id, data_id, "process"))
    generate_fid(manager, exp_id, data_id, backend)
    monkeypatch.setattr(
        "workflow.baseline_optimize.optimize_baseline",
        lambda experiment, path: BaselineOptimizeResult(
            baseline={
                "F1": {"enabled": True, "mode": "order", "order": 2},
                "F2": {"enabled": False},
            },
            scores={},
            spectrum_path=str(path),
            logs=["测试基线"],
            optimized=["F1"],
        ),
    )
    monkeypatch.setattr(
        "workflow.window_optimize.optimize_direct_window_from_work",
        lambda work, experiment, current=None: WindowOptimizeResult(
            choice={"type": "sine_bell", "off": 0.45, "end": 0.95},
            changed=True,
            logs=["测试直接维窗"],
        ),
    )
    spec = generate_spectrum(manager, exp_id, data_id, backend)
    assert spec
    final_params = backend.process_calls[-1][2]
    assert final_params["baseline"]["F1"]["order"] == 2
    assert final_params["window"]["F2"]["type"] == "sine_bell"
    assert "direct_poly_time" in final_params


# ----------------------------------------------------------------------
# 人工路径
# ----------------------------------------------------------------------
def test_manual_full_path_uniform(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """人工路径:manual_fid_com → run_manual_fid_com → manual_scripts → run_manual_spectrum。"""
    from workflow.manual import (
        manual_fid_com,
        manual_scripts,
        run_manual_fid_com,
        run_manual_spectrum,
    )

    class Runtime:
        def run(self, argv, *, cwd=None, timeout=3600):
            name = Path(argv[-1]).name
            work = Path(cwd)
            if name == "fid.com":
                (work / "test.fid").write_bytes(b"fid")
            elif name in ("process.com", "nus.com"):
                # 真实脚本输出 {raw 目录名}.ft2(manual 用 read_dataset dataset_id)
                (work / "src_hsqc_2d.ft2").write_bytes(b"ft2")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: Runtime())
    manager, exp_id, data_id, raw = _manager_with_data(
        tmp_path, bruker_dir, "hsqc_2d"
    )
    backend = FakeBackend(manager.data_dir(exp_id, data_id, "process"))
    content = manual_fid_com(manager, exp_id, data_id, backend)
    assert "fid.com" in content
    fid_path = run_manual_fid_com(manager, exp_id, data_id, content)
    assert Path(fid_path).is_file()
    scripts = manual_scripts(manager, exp_id, data_id)
    assert "process.com" in scripts
    spectrum = run_manual_spectrum(
        manager, exp_id, data_id, {"process.com": scripts["process.com"]}
    )
    assert Path(spectrum).parent == manager.data_dir(exp_id, data_id, "spectra")
    assert any(r.workflow_ref == "manual_process" for r in manager.project.workflow_runs)


def test_manual_spectrum_accepts_slice_fid(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """人工谱图运行:3D 切片 fid(fid/test*.fid)不被误判缺 fid。"""
    from workflow.manual import run_manual_spectrum

    class Runtime:
        def run(self, argv, *, cwd=None, timeout=3600):
            work = Path(cwd)
            (work / "src_hnca_3d.ft3").write_bytes(b"ft3")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: Runtime())
    manager, exp_id, data_id, _raw = _manager_with_data(
        tmp_path, bruker_dir, "hnca_3d"
    )
    work = manager.data_dir(exp_id, data_id, "process")
    slice_dir = work / "fid"
    slice_dir.mkdir(parents=True, exist_ok=True)
    (slice_dir / "test001.fid").write_bytes(b"fid")
    (slice_dir / "test002.fid").write_bytes(b"fid")
    manager.set_data_fid(exp_id, data_id, slice_dir)
    manager.save()
    spectrum = run_manual_spectrum(
        manager, exp_id, data_id, {"process.com": "#!/bin/csh\n# process\n"}
    )
    assert Path(spectrum).parent == manager.data_dir(exp_id, data_id, "spectra")


# ----------------------------------------------------------------------
# 批量路径
# ----------------------------------------------------------------------
def test_batch_full_path_group(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """批量路径:成组导入 → 数据组批量处理(参考数据处理 + 依次优化)。"""
    from gui.processing import ProcessingController
    from workflow.batch import run_batch

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("batch")
    manager.save()
    folders = [
        str(bruker_dir / "hsqc_2d"),
        str(bruker_dir / "nus_2d"),
    ]
    controller = ProcessingController(manager)
    result = controller.batch_import(entry.id, folders, group=True)
    assert result["batch_id"].startswith("G")
    assert all(item["ok"] for item in result["results"])
    group = manager.group(entry.id, result["batch_id"])
    assert group is not None and len(group.data_ids) == 2
    backend = FakeBackend(tmp_path / "batch_work")
    batch_result = run_batch(
        manager,
        entry.id,
        result["batch_id"],
        ["fid", "spectrum"],
        backend,
        params={"phase_route": "none"},
    )
    assert batch_result["summary"]["failed"] == 0
    assert len(batch_result["results"]) == 2
