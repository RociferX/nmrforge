"""统一相位途径(unified_route)编排测试。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

import workflow.phase_routes as routes
from core.data.bruker_reader import read_dataset


def _synthetic_preview(axis: int, p0: float) -> np.ndarray:
    """复型 Lorentzian:被评轴复型 + 已知相位,其它轴实型(与内存搜索测试同构)。"""
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
    k = np.arange(n, dtype=float)
    ramp = np.exp(1j * np.deg2rad(p0 + 0.0 * k / max(n - 1, 1)))
    shape = [1] * arr.ndim
    shape[axis] = n
    return arr * ramp.reshape(shape)


class _FakeBackend:
    def __init__(self, work: Path) -> None:
        self.work = Path(work)
        self.work.mkdir(parents=True, exist_ok=True)
        self.process_calls: list[tuple] = []
        self.reconstruct_params: list[dict] = []
        self.finalize_calls: list[dict] = []
        self.apply_direct_calls: list[tuple] = []

    def process(
        self,
        experiment,
        plan,
        params=None,
        direct_phase_override=None,
        out_file=None,
        script_name=None,
        progress=None,
    ):
        params = dict(params or {})
        self.process_calls.append((experiment, plan, params, dict(direct_phase_override or {})))
        path = str(self.work / (out_file or "final.ft2"))
        Path(path).write_bytes(b"x")
        return {"success": True, "spectrum_path": path, "logs": []}

    def reconstruct_nus(self, experiment, params, progress=None):
        self.reconstruct_params.append(dict(params or {}))
        return {"success": True, "spectrum_path": str(self.work / "out.ft2"), "logs": []}

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
    ):
        self.finalize_calls.append(
            {
                "phases": dict(phases or {}),
                "planes": planes,
                "params": dict(params or {}),
                "progress": progress,
            }
        )
        path = str(self.work / (out_file or "final.ft2"))
        Path(path).write_bytes(b"x")
        return {"success": True, "spectrum_path": path, "logs": []}

    def _apply_direct_phase(self, experiment, work, p0, p1, logs, progress=None):
        self.apply_direct_calls.append((p0, p1))
        return True


def test_unified_route_uniform_order_and_phases(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """uniform:先 F1 后 F2(旧算法顺序);F2 预览携带已固定 F1 相位;终跑带全相位。"""
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _FakeBackend(tmp_path / "pv_work")
    work = backend.work

    def fake_read(path: str, unpack_axis: int | None = None):
        name = Path(path).name
        if "F1" in name:
            return _synthetic_preview(0, -25.0)
        if "F2" in name:
            return _synthetic_preview(1, -35.0)
        return _synthetic_preview(0, 0.0)

    monkeypatch.setattr(routes, "_read_complex_preview", fake_read)
    result = routes.unified_route(experiment, backend, work_dir=work)
    assert len(backend.process_calls) == 3  # F1 预览 + F2 预览 + 终跑
    preview1, preview2, final = backend.process_calls
    assert preview1[2].get("preview_axis") == "F1"
    assert preview1[3] == {}  # 首个轴无固定相位
    assert preview2[2].get("preview_axis") == "F2"
    assert set(preview2[3]) == {"F1"}  # F1 已固定后传给 F2 预览
    assert set(final[3]) == {"F1", "F2"}
    phases = result["phases"]
    assert abs((phases["F1"][0] - 25.0 + 180.0) % 360.0 - 180.0) <= 8.0, phases
    assert abs((phases["F2"][0] - 35.0 + 180.0) % 360.0 - 180.0) <= 8.0, phases
    assert result["backend_runs"] == 3
    assert "spectrum_path" in result


def test_disambiguate_180_mixed_uses_region_sign_convention() -> None:
    """±180° 化学位移分区消歧:预设 Cα 负/Cβ 正——相位 0 不翻,相位 180 翻回。"""
    from core.data.internal_data_model import (
        AxisRole,
        Dimension,
        Experiment,
        ExperimentType,
        Sampling,
        SamplingMode,
    )
    from workflow.phase_routes import _disambiguate_180_mixed

    n = 64
    dims = [
        Dimension(logical_axis="F3", nucleus="1H", sf=600.0, sw=8196.0,
                  o1p=4.7, role=AxisRole.DIRECT),
        Dimension(logical_axis="F2", nucleus="15N", sf=60.8, sw=2189.0,
                  o1p=118.0),
        Dimension(logical_axis="F1", nucleus="13C", sf=150.9, sw=11312.0,
                  o1p=39.0, td=n, ft_size=n),
    ]
    exp = Experiment(
        dataset_id="x",
        source_path=Path("x"),
        ndim=3,
        acquisition_order=["F3", "F2", "F1"],
        dimensions=dims,
        sampling=Sampling(mode=SamplingMode.NUS),
        experiment_type=ExperimentType(name="HNCACB", confidence=1.0),
    )
    k = np.arange(n)
    ppm = 39.0 + (n / 2.0 - k) * (11312.0 / (n * 150.9))
    arr = np.zeros((4, n), dtype=np.complex128)
    for target in (50.0, 55.0, 60.0, 65.0):  # Cα 区(40-70),期望负
        i = int(np.argmin(np.abs(ppm - target)))
        arr[:, i] += -1.0
    for target in (18.0, 25.0, 30.0, 40.0):  # Cβ 区(15-45),期望正
        i = int(np.argmin(np.abs(ppm - target)))
        arr[:, i] += 1.0
    assert _disambiguate_180_mixed(arr, 1, (0.0, 0.0), exp, "F1") == (0.0, 0.0)
    flipped = _disambiguate_180_mixed(arr, 1, (180.0, 0.0), exp, "F1")
    assert abs((flipped[0] - 0.0 + 180.0) % 360.0 - 180.0) < 1e-6, flipped
    # 15N 轴无分区先验,不消歧
    assert _disambiguate_180_mixed(arr, 0, (90.0, 0.0), exp, "F2") == (90.0, 0.0)


def test_unified_route_nus_reconstruct_then_finalize(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """NUS:SMILE 一次(关搜索)→ 直接维对称性调相 → 间接维 finalize 复型预览
    (该轴不加 -di)+ 内存搜索 → 应用直接维相位 → finalize 终跑。"""
    experiment = read_dataset(bruker_dir / "nus_2d")
    backend = _FakeBackend(tmp_path / "nus_work")
    work = backend.work
    n_direct, n_t1 = 64, 32
    k0 = np.arange(n_direct, dtype=float)
    t1 = np.arange(n_t1, dtype=float)
    direct = 1.0 / (1.0 + 1j * (k0 - 22) / 1.5)
    direct = direct * np.exp(-1j * np.deg2rad(30.0))
    fid1 = np.exp(-t1 / 8.0) * np.cos(2.0 * np.pi * 8.0 * t1 / n_t1)
    planes = np.outer(direct, fid1)  # (direct, f1_time)
    monkeypatch.setattr(routes, "_load_recon_planes", lambda exp, wk: planes)
    # NUS 直接维沿用旧权威对称性搜索(0.2.96 机制)
    monkeypatch.setattr(
        "core.optimization.phase_search.search_direct_phase_on_spectrum",
        lambda arr, axis=0, metric="symmetry", progress=None: (30.0, 0.0, 80.0),
    )
    # finalize 复型预览产物:按文件名给 F1 已知相位 -10°
    def fake_read(path: str, unpack_axis: int | None = None):
        name = Path(path).name
        if "preview_F1" in name:
            return _synthetic_preview(0, -30.0)
        return _synthetic_preview(0, 0.0)

    monkeypatch.setattr(routes, "_read_complex_preview", fake_read)
    result = routes.unified_route(experiment, backend, work_dir=work)
    assert len(backend.reconstruct_params) == 1
    assert backend.reconstruct_params[0].get("display_phase_search") is False
    # finalize 调用 2 次:间接维预览(带 preview_axis) + 终跑(带全部相位)
    assert len(backend.finalize_calls) == 2
    preview_call, final_call = backend.finalize_calls
    assert preview_call["params"]["preview_axis"] == "F1"
    assert preview_call["phases"] == {}
    assert final_call["planes"] == "nus2d/recon_ph.ft1"
    assert abs((result["direct_phase"][0] - 30.0 + 180.0) % 360.0 - 180.0) <= 8.0
    # 预览基底含 F1=-30° → 校正 +30°
    assert abs((result["phases"]["F1"][0] - 30.0 + 180.0) % 360.0 - 180.0) <= 8.0
    assert backend.apply_direct_calls  # 直接维相位已应用
    assert result["backend_runs"] == 3  # SMILE + 预览 + 终跑


def test_unified_route_nus_progress_stages(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """NUS:progress 覆盖 第一遍 SMILE/F1 复型预览/finalize 终跑 各阶段。"""
    experiment = read_dataset(bruker_dir / "nus_2d")
    backend = _FakeBackend(tmp_path / "nus_prog_work")
    work = backend.work
    n_direct, n_t1 = 64, 32
    k0 = np.arange(n_direct, dtype=float)
    t1 = np.arange(n_t1, dtype=float)
    direct = 1.0 / (1.0 + 1j * (k0 - 22) / 1.5)
    fid1 = np.exp(-t1 / 8.0) * np.cos(2.0 * np.pi * 8.0 * t1 / n_t1)
    planes = np.outer(direct, fid1)
    monkeypatch.setattr(routes, "_load_recon_planes", lambda exp, wk: planes)
    monkeypatch.setattr(
        "core.optimization.phase_search.search_direct_phase_on_spectrum",
        lambda arr, axis=0, metric="symmetry", progress=None: (30.0, 0.0, 80.0),
    )
    monkeypatch.setattr(
        routes, "_read_complex_preview",
        lambda path, unpack_axis=None: _synthetic_preview(0, -30.0),
    )
    messages: list[str] = []
    result = routes.unified_route(experiment, backend, work_dir=work, progress=messages.append)
    joined = "\n".join(messages)
    assert "第一遍 SMILE 完成" in joined, messages
    assert "F1 复型预览中" in joined, messages
    assert "F1 复型预览完成" in joined, messages
    assert "finalize 终跑中" in joined, messages
    assert "finalize 终跑完成" in joined, messages
    assert all(call["progress"] is not None for call in backend.finalize_calls)
    assert result["backend_runs"] == 3


def test_unified_route_uniform_progress_stages(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """uniform:progress 覆盖 F1/F2 复型预览与终跑。"""
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _FakeBackend(tmp_path / "uni_prog_work")
    work = backend.work

    def fake_read(path: str, unpack_axis: int | None = None):
        name = Path(path).name
        if "F1" in name:
            return _synthetic_preview(0, -25.0)
        return _synthetic_preview(1, -35.0)

    monkeypatch.setattr(routes, "_read_complex_preview", fake_read)
    messages: list[str] = []
    routes.unified_route(experiment, backend, work_dir=work, progress=messages.append)
    joined = "\n".join(messages)
    assert "F1 复型预览中" in joined, messages
    assert "F1 复型预览完成" in joined, messages
    assert "F2 复型预览中" in joined, messages
    assert "F2 复型预览完成" in joined, messages
    assert "终跑(完整重跑)中" in joined, messages
    assert "终跑完成" in joined, messages


def test_finalize_nus_progress_callback(tmp_path: Path, monkeypatch, bruker_dir: Path) -> None:
    """finalize_nus 直接调用:progress 覆盖 开始 finalize 与 finalize 完成。"""
    from backend.nmrpipe_backend import NMRPipeBackend
    from backend.runtime import CompletedProcess

    experiment = read_dataset(bruker_dir / "nus_2d")
    work = tmp_path / "fw_work"
    (work / "nus2d").mkdir(parents=True)
    (work / "nus2d" / "recon.ft1").write_bytes(b"x")

    class _FakeCsh:
        def run(self, argv, *, cwd=None, timeout=3600, on_line=None):
            (Path(cwd) / f"{experiment.dataset_id}.ft2").write_bytes(b"x")
            return CompletedProcess("", "", "", 0)

    monkeypatch.setattr("backend.nmrpipe_backend.CshRuntime", _FakeCsh)
    monkeypatch.setattr(
        "backend.nmrpipe_backend.find_nmrpipe_bin", lambda explicit="": Path("/bin")
    )
    messages: list[str] = []
    backend = NMRPipeBackend(nmrpipe_bin="")
    resp = backend.finalize_nus(experiment, work_dir=work, progress=messages.append)
    assert resp["success"] is True, resp
    assert "开始 finalize(复型预览/终跑)" in messages, messages
    assert "finalize 完成" in messages, messages

def test_direct_phase_cache_roundtrip(tmp_path: Path) -> None:
    '''直接维相位缓存:保存→加载命中;shape/参数变化则失效。'''
    from core.data.internal_data_model import (
        AxisRole,
        Dimension,
        Experiment,
        Sampling,
    )
    from workflow.phase_routes import (
        _direct_phase_params_fp,
        _load_direct_phase_cache,
        _save_direct_phase_cache,
    )

    dims = [
        Dimension(logical_axis="F3", nucleus="1H", role=AxisRole.DIRECT),
        Dimension(logical_axis="F2", nucleus="15N", role=AxisRole.INDIRECT),
        Dimension(logical_axis="F1", nucleus="13C", role=AxisRole.INDIRECT),
    ]
    exp = Experiment(
        dataset_id="x",
        source_path=Path("x"),
        ndim=3,
        dimensions=dims,
        sampling=Sampling(),
        acquisition_parameters={},
        processing_state={},
        segments=[],
    )
    params = {"ext_lo": 10.5, "ext_hi": 6.5, "extract": True}
    shape = (20, 16, 12)
    fp = _direct_phase_params_fp(exp, params)
    assert fp == _direct_phase_params_fp(exp, dict(params))
    assert fp != _direct_phase_params_fp(exp, {"ext_lo": 9.0, "ext_hi": 7.0})

    _save_direct_phase_cache(
        tmp_path, exp, params, shape, 12.5, -3.0, 40.0, 22.3
    )
    data = _load_direct_phase_cache(tmp_path, exp, params, shape)
    assert data is not None
    assert float(data["p0"]) == 12.5
    assert float(data["p1"]) == -3.0
    assert float(data["duration_s"]) == 22.3
    assert _load_direct_phase_cache(tmp_path, exp, params, (21, 16, 12)) is None
    assert (
        _load_direct_phase_cache(
            tmp_path, exp, {"ext_lo": 9.0, "ext_hi": 7.0}, shape
        )
        is None
    )
