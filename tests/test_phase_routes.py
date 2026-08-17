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
    ):
        self.finalize_calls.append(
            {"phases": dict(phases or {}), "planes": planes, "params": dict(params or {})}
        )
        path = str(self.work / (out_file or "final.ft2"))
        Path(path).write_bytes(b"x")
        return {"success": True, "spectrum_path": path, "logs": []}

    def _apply_direct_phase(self, experiment, work, p0, p1, logs):
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
        lambda arr, axis=0, metric="symmetry": (30.0, 0.0, 80.0),
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
