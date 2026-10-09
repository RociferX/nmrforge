"""Full-path end-to-end regression test (0.2.163-patch8).

Must be run after every code change: it walks every existing path end to end --
automatic (2D/3D uniform + NUS), manual (fid.com / spectrum script), batch (data
group).

Strategy: FakeBackend simulates the NMRPipe products; spectrum reading is
injected with synthetic arrays through monkeypatch, to verify process integrity,
product placement, WorkflowRun registration and real execution of the
optimisation/diagnostic phase (fid in a readable format, diagnostics and
direct-dimension window optimisation read real files; baseline/zero
filling/preview use injected arrays).
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
    """Complex Lorentzian: complex on the evaluated axis + known phase, the other
    axes real (isomorphic to the phase_routes test)."""
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
    """Scoreable synthetic spectrum (with peaks)."""
    arr = np.zeros((64, 128), dtype=float)
    arr[30, 60] = 1000.0
    arr[20, 70] = 600.0
    return arr


class FakeBackend:
    """Simulated NMRPipe backend: writes a real readable fid; spectrum
    paths/previews are provided via monkeypatch."""

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
        self, experiment, data_dir, progress=None, params=None, fid_com_overrides=None
    ) -> dict:
        """Write a real readable fid (readable by diagnostics/direct-dimension
        window optimisation) and fid.com (read manually)."""
        work = self.work
        dataset_id = experiment.dataset_id
        work.mkdir(parents=True, exist_ok=True)
        (work / "fid.com").write_text("#!/bin/csh\n# auto fid.com\n", encoding="utf-8")
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
        """Write a readable pipe spectrum (real values + valid header) for peak
        picking and quality scoring to read."""
        import nmrglue as ng
        import nmrglue.fileio.pipe as pmod

        dic = {k: 0.0 for k in pmod.fdata_nums}
        n1, n2, n3 = 128, 64, 16
        dic.update(
            {
                "FDSIZE": n1,
                "FDSPECNUM": 1 if ndim == 1 else n2,
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
                "FDF3SW": 11300.0,
                "FDF3OBS": 150.0,
                "FDF3CAR": 45.0,
                "FDF2QUADFLAG": 1,
                "FDF1QUADFLAG": 1,
                "FDF3QUADFLAG": 1,
                "FDF2APOD": 0.0,
                "FDF1APOD": 0.0,
                "FDF2FTFLAG": 1,
                "FDF1FTFLAG": 1,
                "FDF3FTFLAG": 1,
                "FDTRANSPOSED": 0,
                "FDPIPEFLAG": 1 if ndim == 3 else 0,
                "FDQUADFLAG": 1,
                "FDDIMORDER": [2.0, 1.0, 3.0, 4.0],
                "FDDIMORDER1": 2.0,
                "FDDIMORDER2": 1.0,
                "FDDIMORDER3": 3.0,
                "FDDIMORDER4": 4.0,
            }
        )
        for i, lab in enumerate(("FDF2LABEL", "FDF1LABEL", "FDF3LABEL", "FDF4LABEL")):
            dic[lab] = ("1H", "15N", "13C", "")[i]
        dic.update(
            {
                "FDUSERNAME": "test",
                "FDTITLE": "test",
                "FDCOMMENT": "test",
                "FDOPERNAME": "test",
                "FDSRCNAME": "test",
            }
        )
        if ndim == 1:
            data = np.zeros(n1, dtype="<f4")
        elif ndim == 2:
            data = np.zeros((n2, n1), dtype="<f4")
        else:
            # NMRPipe stream data is (FDF3SIZE, FDSPECNUM, FDSIZE).
            data = np.zeros((n3, n2, n1), dtype="<f4")

        if ndim == 1:
            data[60] = 1000.0
            data[70] = 600.0
        elif ndim == 2:
            data[30, 60] = 1000.0
            data[20, 70] = 600.0
        else:
            data[4, 30, 60] = 1000.0
            data[6, 20, 70] = 600.0
        ng.pipe.write(str(path), dic, np.ascontiguousarray(data), overwrite=True)

    def _write_fid_file(self, path: Path, n_traces: int, n_direct: int) -> None:
        """Write an nmrglue-readable fid: 512B header + (n_traces, n_direct) complex
        interleaved."""
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
        # Complex -> real/imaginary interleaved reals (first axis doubled),
        # matching the NMRPipe fid layout
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
        self.process_calls.append((experiment, plan, params, dict(direct_phase_override or {})))
        ext = {1: "ft1", 2: "ft2"}.get(experiment.ndim, "ft3")
        name = out_file or f"{experiment.dataset_id}.{ext}"
        path = self.work / name
        self._write_spectrum_file(path, ndim=experiment.ndim)
        return {"success": True, "spectrum_path": str(path), "logs": []}

    def reconstruct_nus(self, experiment, params, progress=None) -> dict:
        self.reconstruct_params.append(dict(params or {}))
        # Write the reconstruction planes (2D nus2d/recon.ft1; 3D
        # nus3d_rc/test*.ft1) for finalize to consume
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
    """Inject the synthetic preview/spectrum readers (real process, simulated
    file-reading layer)."""

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

    monkeypatch.setattr("core.data.pipe_io.read_pipe_complex", fake_read_pipe_complex)
    # Baseline/zero-fill scoring: the spectrum file bytes are unreadable ->
    # read the synthetic array directly
    import core.data.pipe_io

    monkeypatch.setattr(core.data.pipe_io, "read_pipe_complex", fake_read_pipe_complex)


# ----------------------------------------------------------------------
# Automatic path
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("dataset", "exp_title"),
    [
        ("hsqc_2d", "HSQC"),  # 2D uniform
        ("nus_2d", "NUS 2D"),  # 2D NUS
        ("hnca_3d", "HNCA"),  # 3D uniform (slices)
        ("nus_3d", "NUS 3D"),  # 3D NUS (slices)
    ],
)
def test_auto_full_path(
    tmp_path: Path,
    bruker_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    dataset: str,
    exp_title: str,
) -> None:
    """Automatic path: import -> generate FID -> generate spectrum (unified,
    diagnostics + optimisation) -> peak picking.
    The analysis (HSQC CSP) feature was removed by user decision (2026-09-12,
    REPORT-008), so this path ends at peak picking."""
    from workflow.pick_peaks import pick_peaks
    from workflow.stepwise import generate_fid, generate_spectrum

    _install_spectrum_mocks(monkeypatch)
    manager, exp_id, data_id, _raw = _manager_with_data(tmp_path, bruker_dir, dataset, exp_title)
    backend = FakeBackend(manager.data_dir(exp_id, data_id, "process"))
    fid_path = generate_fid(manager, exp_id, data_id, backend)
    assert fid_path
    data = manager.data(exp_id, data_id)
    assert data.status == "fid_ready"
    # Slice fid existence: a single file or a slice directory
    fid = Path(data.fid_path)
    assert fid.is_file() or (fid.is_dir() and list(fid.glob("test*.fid")))

    spec = generate_spectrum(manager, exp_id, data_id, backend)
    assert spec
    data = manager.data(exp_id, data_id)
    assert data.status == "processed"
    if backend.reconstruct_params:
        first = backend.reconstruct_params[0]
        if dataset == "nus_2d":
            assert first.get("_initialize_2d_nus_phase") is True
        else:
            assert "_initialize_2d_nus_phase" not in first
        assert "_initialize_2d_nus_phase" not in backend.reconstruct_params[-1]
    assert any(
        r.workflow_ref == "phase_optimize_unified" and r.status == "success"
        for r in manager.project.workflow_runs
    )

    peaks = pick_peaks(manager, exp_id, data_id)
    assert peaks.get("status") == "success"
    assert any(
        r.workflow_ref == "pick_peaks" and r.status == "success"
        for r in manager.project.workflow_runs
    )


def test_compact_2d_nus_full_path_preserves_legal_high_coordinates(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A compact 50% schedule keeps its declared grid and legal high coordinates."""
    import hashlib
    import re

    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_dataset
    from core.data.nus_reader import schedule_grid_shape
    from workflow.stepwise import generate_fid, generate_spectrum

    raw = tmp_path / "compact_nus"
    shutil.copytree(bruker_dir / "nus_2d", raw)
    acqu2s = (raw / "acqu2s").read_text(encoding="utf-8")
    acqu2s = re.sub(r"##\$TD=\s*\d+", "##$TD= 62", acqu2s)
    acqu2s = re.sub(r"##\$FnMODE=\s*\d+", "##$FnMODE= 6", acqu2s)
    (raw / "acqu2s").write_text(acqu2s + "\n##$NusTD= 124\n", encoding="utf-8")
    acqus = (raw / "acqus").read_text(encoding="utf-8")
    acqus = re.sub(r"##\$NusAMOUNT=\s*\d+", "##$NusAMOUNT= 50", acqus)
    (raw / "acqus").write_text(acqus + "\n##$FnTYPE= 2\n", encoding="utf-8")
    points = [
        0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 14, 17, 19, 22,
        25, 27, 30, 33, 35, 37, 39, 40, 44, 47, 49, 53, 55, 57, 60,
    ]
    (raw / "nuslist").write_text("".join(f"{p}\n" for p in points), encoding="utf-8")
    np.ones((62, 2048), dtype="<i4").tofile(raw / "ser")
    before = {name: hashlib.sha256((raw / name).read_bytes()).hexdigest()
              for name in ("ser", "nuslist")}
    exp = read_dataset(raw)
    assert schedule_grid_shape(exp) == (62,)
    assert exp.sampling.sampling_fraction == pytest.approx(0.5)
    count, bad, removed = NMRPipeBackend(tmp_path / "clean")._clean_source_nus(exp, [raw], [])
    assert count == 31 and bad == [] and not removed
    assert before == {name: hashlib.sha256((raw / name).read_bytes()).hexdigest()
                      for name in ("ser", "nuslist")}

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HC-HSQC NUS")
    data = manager.import_data(entry.id, str(raw))
    _install_spectrum_mocks(monkeypatch)
    backend = FakeBackend(manager.data_dir(entry.id, data.id, "process"))
    assert generate_fid(manager, entry.id, data.id, backend)
    assert generate_spectrum(manager, entry.id, data.id, backend)
    assert manager.data(entry.id, data.id).status == "processed"


def test_auto_full_path_1d_generates_ft1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    "Regression coverage: test auto full path 1d generates ft1."
    from core.data.bruker_reader import read_dataset
    from workflow.stepwise import generate_fid, generate_spectrum

    raw = tmp_path / "src_1d"
    raw.mkdir()
    (raw / "acqus").write_text(
        "\n".join(
            (
                "##$PARMODE= 0",
                "##$TD= 1024",
                "##$SW_h= 8000",
                "##$SFO1= 600",
                "##$O1= 2820",
                "##$NUC1= <1H>",
                "##$PULPROG= <zg30>",
                "##$BYTORDA= 0",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    experiment = read_dataset(raw)
    assert experiment.ndim == 1
    assert experiment.sampling.mode.value == "uniform"

    manager = ProjectManager.create_project(tmp_path / "proj_1d", "demo")
    entry = manager.create_experiment("1H-1D")
    data = manager.import_data(entry.id, str(raw))
    manager.save()
    backend = FakeBackend(manager.data_dir(entry.id, data.id, "process"))
    _install_spectrum_mocks(monkeypatch)

    assert generate_fid(manager, entry.id, data.id, backend)
    spectrum = Path(generate_spectrum(manager, entry.id, data.id, backend))

    assert spectrum.suffix == ".ft1"
    assert spectrum.is_file()
    assert backend.process_calls
    assert backend.reconstruct_params == []


def test_auto_uniform_runs_processing_optimization(
    tmp_path: Path,
    bruker_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """uniform processing parameter optimisation: diagnostics + baseline +
    direct-dimension window + zero filling/indirect window go into the final run."""
    from workflow.baseline_optimize import BaselineOptimizeResult
    from workflow.stepwise import generate_fid, generate_spectrum
    from workflow.window_optimize import (
        MultiWindowOptimizeResult,
        WindowOptimizeResult,
    )

    _install_spectrum_mocks(monkeypatch)
    manager, exp_id, data_id, _raw = _manager_with_data(tmp_path, bruker_dir, "hsqc_2d")
    backend = FakeBackend(manager.data_dir(exp_id, data_id, "process"))
    generate_fid(manager, exp_id, data_id, backend)
    monkeypatch.setattr(
        "workflow.baseline_optimize.optimize_baseline",
        lambda experiment, path, **kwargs: BaselineOptimizeResult(
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
    monkeypatch.setattr(
        "workflow.window_optimize.optimize_indirect_windows_from_work",
        lambda work, experiment, current=None: MultiWindowOptimizeResult(
            choice={"F1": {"type": "none"}},
            changed=True,
            logs=["测试间接维窗"],
        ),
    )
    spec = generate_spectrum(manager, exp_id, data_id, backend)
    assert spec
    final_params = backend.process_calls[-1][2]
    assert final_params["baseline"]["F1"]["order"] == 2
    assert final_params["window"]["F2"]["type"] == "sine_bell"
    assert "direct_poly_time" in final_params


# ----------------------------------------------------------------------
# Manual path
# ----------------------------------------------------------------------
def test_manual_full_path_uniform(
    tmp_path: Path,
    bruker_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual path: manual_fid_com -> run_manual_fid_com -> manual_scripts ->
    run_manual_spectrum."""
    from workflow.manual import (
        manual_fid_com,
        manual_scripts,
        run_manual_fid_com,
        run_manual_spectrum,
    )
    from workflow.stepwise import generate_fid

    class Runtime:
        def run(self, argv, *, cwd=None, timeout=3600, on_line=None):
            name = Path(argv[-1]).name
            work = Path(cwd)
            if name == "fid.com":
                (work / "test.fid").write_bytes(b"fid")
            elif name in ("process.com", "nus.com"):
                # The real script outputs {raw directory name}.ft2 (manual uses
                # read_dataset dataset_id)
                (work / "d_001.ft2").write_bytes(b"ft2")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: Runtime())
    manager, exp_id, data_id, raw = _manager_with_data(tmp_path, bruker_dir, "hsqc_2d")
    backend = FakeBackend(manager.data_dir(exp_id, data_id, "process"))
    # 0.2.199-patch29dm: the manual path must auto-generate the FID first
    # (fid.com written to disk) before it can be read
    generate_fid(manager, exp_id, data_id, backend)
    content = manual_fid_com(manager, exp_id, data_id, backend)
    assert "fid.com" in content
    fid_path = run_manual_fid_com(manager, exp_id, data_id, content, backend=backend)
    assert Path(fid_path).is_file()
    scripts = manual_scripts(manager, exp_id, data_id)
    assert "process.com" in scripts
    spectrum = run_manual_spectrum(
        manager, exp_id, data_id, {"process.com": scripts["process.com"]}
    )
    assert Path(spectrum).parent == manager.data_dir(exp_id, data_id, "spectra")
    assert any(r.workflow_ref == "manual_process" for r in manager.project.workflow_runs)


def test_manual_scripts_uses_data_id_for_single_fid(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.163-patch10: for a 2D manual spectrum script, in_file uses the data_id
    (d_001) rather than the raw directory name (src_*), consistent with the
    generate_fid product."""
    from workflow.manual import manual_scripts

    manager, exp_id, data_id, _raw = _manager_with_data(tmp_path, bruker_dir, "hsqc_2d")
    work = manager.data_dir(exp_id, data_id, "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / f"{data_id}.fid").write_bytes(b"fid")
    manager.save()
    scripts = manual_scripts(manager, exp_id, data_id)
    content = scripts["process.com"]
    assert f"-in {data_id}.fid" in content
    assert "src_hsqc_2d.fid" not in content


def test_manual_reads_segmented_container(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.163-patch12: manual reading of a segmented acquisition container
    directory (without acqus) no longer raises an error."""
    from workflow.manual import manual_scripts

    # Segmented container: the root directory has no acqus, the two
    # sub-segments contain acqus
    container = tmp_path / "seg_container"
    container.mkdir()
    for seg in ("s1", "s2"):
        shutil.copytree(bruker_dir / "hsqc_2d", container / seg)
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(
        entry.id, str(container), segments=[str(container / "s1"), str(container / "s2")]
    )
    manager.save()
    work = manager.data_dir(entry.id, data.id, "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / f"{data.id}_process.com").write_text("# existing script\n", encoding="utf-8")
    # manual_scripts prefers the existing script; if it can be read, no
    # acqus error is reported
    scripts = manual_scripts(manager, entry.id, data.id)
    assert f"{data.id}_process.com" in scripts


def test_manual_scripts_slice_in_file(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.163-patch9: after the 3D uniform manual spectrum script detects the
    slice fid, the rendered in_file is rewritten to fid/test%03d.fid (otherwise
    the manual run fails)."""
    from workflow.manual import manual_scripts

    manager, exp_id, data_id, _raw = _manager_with_data(tmp_path, bruker_dir, "hnca_3d")
    work = manager.data_dir(exp_id, data_id, "process")
    slice_dir = work / "fid"
    slice_dir.mkdir(parents=True, exist_ok=True)
    (slice_dir / "test001.fid").write_bytes(b"fid")
    (slice_dir / "test002.fid").write_bytes(b"fid")
    manager.save()
    scripts = manual_scripts(manager, exp_id, data_id)
    content = scripts["process.com"]
    assert "-in fid/test%03d.fid" in content
    assert "-in src_hnca_3d.fid" not in content


def test_manual_scripts_merged_fid_in_file(tmp_path: Path, bruker_dir: Path) -> None:
    """2026-09-24 (user): for multi-segment merged products (merged/...) the -in
    must be rewritten to the actual location.

    For segmented data the fid is not at ``work/{dataset_id}.fid`` -- without
    that rewrite the manual spectrum script points at a non-existent input (it
    reports a missing fid and produces no spectrum).
    """
    from workflow.manual import manual_scripts

    manager, exp_id, data_id, _raw = _manager_with_data(tmp_path, bruker_dir, "hnca_3d")
    work = manager.data_dir(exp_id, data_id, "process")
    merged = work / "merged" / "fid"
    merged.mkdir(parents=True, exist_ok=True)
    for name in ("test001.fid", "test002.fid"):
        (merged / name).write_bytes(b"fid")
    manager.save()
    content = manual_scripts(manager, exp_id, data_id)["process.com"]
    assert "-in merged/fid/test%03d.fid" in content


def test_manual_scripts_merged_single_fid_in_file(tmp_path: Path, bruker_dir: Path) -> None:
    """2026-09-24: when merged into a single file, -in is written as
    merged/{dataset_id}.fid."""
    from workflow.manual import manual_scripts

    manager, exp_id, data_id, _raw = _manager_with_data(tmp_path, bruker_dir, "hnca_3d")
    work = manager.data_dir(exp_id, data_id, "process")
    (work / "merged").mkdir(parents=True, exist_ok=True)
    (work / "merged" / f"{data_id}.fid").write_bytes(b"fid")
    manager.save()
    content = manual_scripts(manager, exp_id, data_id)["process.com"]
    assert f"-in merged/{data_id}.fid" in content


def test_manual_spectrum_accepts_slice_fid(
    tmp_path: Path,
    bruker_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual spectrum run: a 3D slice fid (fid/test*.fid) is not misjudged as a
    missing fid."""
    from workflow.manual import run_manual_spectrum

    class Runtime:
        def run(self, argv, *, cwd=None, timeout=3600, on_line=None):
            work = Path(cwd)
            (work / "d_001.ft3").write_bytes(b"ft3")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: Runtime())
    manager, exp_id, data_id, _raw = _manager_with_data(tmp_path, bruker_dir, "hnca_3d")
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
# Batch path
# ----------------------------------------------------------------------
def test_batch_full_path_group(
    tmp_path: Path,
    bruker_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batch path: group import -> batch processing of the data group (reference
    data processing + optimisations in order)."""
    from gui.processing import ProcessingController
    from workflow.batch import run_batch

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("batch")
    manager.save()
    folders = [
        str(bruker_dir / "hsqc_2d"),
        str(bruker_dir / "nus_2d"),
    ]
    # 0.2.199-patch29gl: batch import validates the raw data files; back-fill
    # ser temporarily for the test
    (bruker_dir / "hsqc_2d" / "ser").write_bytes(b"")
    (bruker_dir / "nus_2d" / "ser").write_bytes(b"")
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
