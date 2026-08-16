"""NMRPipe 后端行为测试（Windows 上无 NMRPipe，验证优雅降级）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.factory import create_backend
from backend.nmrpipe_backend import NMRPipeBackend
from backend.nmrpipe_finder import find_nmrpipe_bin
from core.data.bruker_reader import read_dataset
from core.planning.method_selector import select_method

_NO_NMRPIPE = find_nmrpipe_bin() is None


def test_factory_returns_nmrpipe_backend() -> None:
    backend = create_backend({"backend": {"provider": "nmrpipe"}})
    assert isinstance(backend, NMRPipeBackend)


@pytest.mark.skipif(not _NO_NMRPIPE, reason="本机已安装 NMRPipe，跳过缺失路径测试")
def test_health_check_missing_nmrpipe() -> None:
    backend = NMRPipeBackend(nmrpipe_bin="")
    health = backend.health_check()
    assert health["ok"] is False
    assert "nmrPipe" in health["message"]


@pytest.mark.skipif(not _NO_NMRPIPE, reason="本机已安装 NMRPipe，跳过缺失路径测试")
def test_process_missing_nmrpipe_graceful(bruker_dir: Path, tmp_path: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_small")
    plan = select_method(exp)
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.process(exp, plan)
    assert result["success"] is False
    assert "未找到" in result["message"]


def test_process_bad_explicit_bin_graceful(bruker_dir: Path, tmp_path: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_small")
    plan = select_method(exp)
    backend = NMRPipeBackend(nmrpipe_bin=str(tmp_path))
    result = backend.process(exp, plan)
    assert result["success"] is False


@pytest.mark.skipif(not _NO_NMRPIPE, reason="本机已安装 NMRPipe，跳过缺失路径测试")
def test_reconstruct_nus_missing_nmrpipe_graceful(bruker_dir: Path, tmp_path: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.reconstruct_nus(exp, {})
    assert result["success"] is False


def test_process_accepts_extract_params(bruker_dir: Path) -> None:
    """process 接受 params(extract/ext_lo/ext_hi),无 NMRPipe 时优雅降级。"""
    exp = read_dataset(bruker_dir / "hsqc_small")
    plan = select_method(exp)
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.process(
        exp,
        plan,
        params={"extract": False, "ext_lo": "9.0", "ext_hi": "7.5"},
    )
    assert result["success"] is False


def test_reconstruct_nus_accepts_extract(bruker_dir: Path) -> None:
    """reconstruct_nus 接受 extract 参数,无 NMRPipe 时优雅降级。"""
    exp = read_dataset(bruker_dir / "nus_3d")
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.reconstruct_nus(exp, {"extract": False})
    assert result["success"] is False


def test_reconstruct_nus_rejects_uniform(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_small")
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.reconstruct_nus(exp, {})
    assert result["success"] is False
    assert "非 NUS" in result["message"]


@pytest.mark.skipif(not _NO_NMRPIPE, reason="本机已安装 NMRPipe，跳过缺失路径测试")
def test_finalize_converted_fid_slice_form(tmp_path: Path) -> None:
    """0.2.80:bruker 切片式输出(fid/test%03d.fid)被接受并归位到 work/fid/。"""
    backend = NMRPipeBackend(nmrpipe_bin="")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "fid").mkdir()
    for i in (1, 2, 3):
        (raw / "fid" / f"test{i:03d}.fid").write_bytes(b"x")
    dest = tmp_path / "work"
    dest.mkdir()
    logs: list[str] = []
    assert backend._finalize_converted_fid(raw, dest, "exp", logs)
    assert len(list((dest / "fid").glob("test*.fid"))) == 3
    assert not (dest / "exp.fid").exists()
    assert any("切片式 fid" in line for line in logs)


def test_finalize_converted_fid_single_file(tmp_path: Path) -> None:
    """单文件 test.fid 路径保持兼容(非切片式 bruker 输出)。"""
    backend = NMRPipeBackend(nmrpipe_bin="")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "test.fid").write_bytes(b"x")
    dest = tmp_path / "work"
    dest.mkdir()
    logs: list[str] = []
    assert backend._finalize_converted_fid(raw, dest, "exp", logs)
    assert (dest / "exp.fid").is_file()


def _write_plane(
    out: Path, arr: np.ndarray, *, f1_size: int = 30, f3_size: int = 4
) -> None:
    """用 nmrglue 写一个 3D 重构平面(第一轴实/虚交错,与 nus3d_rc 一致)。

    nus3d_rc 平面为复型 (n_dir, n_f1) 实/虚交错存储,读回为 (2·n_dir,
    n_f1) 实型;头部用 FDSIZE=n_f1、FDSPECNUM=n_dir(nmrglue 2D 平面
    读取约定,已按真实平面头部核对)。
    """
    import nmrglue as ng

    dic = ng.pipe.create_empty_dic()
    dic.update(
        {
            "FDSIZE": float(arr.shape[1]),
            "FDSPECNUM": float(arr.shape[0]),
            "FDREALSIZE": float(2 * arr.shape[0]),
            "FDF1LABEL": "15N",
            "FDF1TDSIZE": float(f1_size),
            "FDF2LABEL": "1H",
            "FDF2TDSIZE": 1024.0,
            "FDF3LABEL": "13C",
            "FDF3TDSIZE": 75.0,
            "FDF3SIZE": float(f3_size),
            "FDFILECOUNT": float(f3_size),
            "FDDIMCOUNT": 3.0,
            "FDF2FTFLAG": 1.0,
            "FDF2QUADFLAG": 1.0,
        }
    )
    ng.pipe.write(str(out), dic, arr.astype(np.complex64), overwrite=True)


def _synthetic_3d_planes(
    work: Path, n_planes: int = 4, *, theta: float = 0.0
) -> Path:
    """合成 3D 复型平面:直接维 axis 0,干净信号峰,可带已知相位旋转。"""
    import nmrglue as ng

    rng = np.random.default_rng(7)
    n_dir, n_f1 = 64, 12
    plane_dir = work / "nus3d_rc"
    plane_dir.mkdir(parents=True, exist_ok=True)
    k = np.arange(n_dir, dtype=float)
    for p in range(n_planes):
        base = np.zeros((n_dir, n_f1), dtype=np.complex128)
        for j in range(0, n_f1, 4):
            center = 16 + p * 2
            for jj in range(j, j + 4):
                base[:, jj] = 400.0 / (1.0 + ((k - center) / 4.0) ** 2)
        if theta:
            ramp = np.exp(
                1j * np.deg2rad(theta + 12.0 * k / (n_dir - 1))
            )
            base = base * ramp[:, None]
        base = base + rng.normal(0, 0.05, size=base.shape)
        base = base + 1j * rng.normal(0, 0.05, size=base.shape)
        _write_plane(plane_dir / f"test{p + 1:04d}.ft1", base, f3_size=n_planes)
    dic, data = ng.pipe.read(str(plane_dir / "test0001.ft1"))
    assert np.asarray(data).dtype == np.float32, "平面应为实型交错存储"
    assert np.asarray(data).shape == (2 * n_dir, n_f1), "交错复型布局错误"
    return plane_dir


def test_display_phase_search_3d_unpacks_interleaved(
    tmp_path: Path,
) -> None:
    """0.2.98:3D 显示层相位搜索必须先把实型交错平面拆包为复型。

    主重构按 PS(0,0) 输出,干净峰近零相位时最小修正应返回 (0,0)
    (与 2D sampleA 100% 实测一致);拆包失败/交错数据直接评分会报错或
    返回无意义结果。
    """
    from backend.nmrpipe_backend import NMRPipeBackend

    backend = NMRPipeBackend(nmrpipe_bin="")
    plane_dir = _synthetic_3d_planes(tmp_path)
    logs: list[str] = []
    experiment = type("Exp", (), {"ndim": 3})()
    est = backend._display_phase_search(experiment, tmp_path, logs)
    assert est is not None, f"logs={logs}"
    p0, p1, score = est
    assert score >= 30.0
    assert abs(p0) <= 5.0, est
    assert abs(p1) <= 5.0, est
    assert plane_dir.is_dir()


def test_apply_direct_phase_3d_rotates_copy_not_source(
    tmp_path: Path,
) -> None:
    """0.2.98:3D 填相位写 nus3d_rc_ph/ 副本并 finalize,源平面保持不动。"""
    import nmrglue as ng

    from backend.nmrpipe_backend import NMRPipeBackend

    backend = NMRPipeBackend(nmrpipe_bin="")
    plane_dir = _synthetic_3d_planes(tmp_path)
    before = {
        path.name: np.asarray(ng.pipe.read(str(path))[1]).copy()
        for path in sorted(plane_dir.glob("test*.ft1"))
    }
    calls: list[tuple[Path | str | None, str | None]] = []

    def fake_finalize(experiment, *, work_dir=None, planes=None, **kwargs):
        calls.append((work_dir, planes))
        return {"success": True, "spectrum_path": str(tmp_path / "out.ft3")}

    backend.finalize_nus = fake_finalize  # type: ignore[method-assign]
    logs: list[str] = []
    experiment = type("Exp", (), {"ndim": 3})()
    ok = backend._apply_direct_phase(experiment, tmp_path, 33.0, 12.0, logs)
    assert ok
    assert calls and calls[0][0] == tmp_path
    assert calls[0][1] == "nus3d_rc_ph/test%04d.ft1"
    out_dir = tmp_path / "nus3d_rc_ph"
    assert out_dir.is_dir()
    assert len(list(out_dir.glob("test*.ft1"))) == len(
        list(plane_dir.glob("test*.ft1"))
    )
    # 源平面逐字节不动
    for path in sorted(plane_dir.glob("test*.ft1")):
        after = np.asarray(ng.pipe.read(str(path))[1])
        assert np.array_equal(after, before[path.name])



def test_finalize_converted_fid_missing(tmp_path: Path) -> None:
    """既无 test.fid 也无切片时失败(不静默)。"""
    backend = NMRPipeBackend(nmrpipe_bin="")
    raw = tmp_path / "raw"
    raw.mkdir()
    dest = tmp_path / "work"
    dest.mkdir()
    assert not backend._finalize_converted_fid(raw, dest, "exp", [])


def test_reconstruct_nus_segments_missing_nmrpipe(bruker_dir: Path, tmp_path: Path) -> None:
    import shutil

    from core.data.bruker_reader import read_segments

    dst_a = tmp_path / "seg_a"
    dst_b = tmp_path / "seg_b"
    shutil.copytree(bruker_dir / "nus_3d", dst_a)
    shutil.copytree(bruker_dir / "nus_3d", dst_b)
    exp = read_segments([dst_a, dst_b])
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.reconstruct_nus(exp, {})
    assert result["success"] is False
