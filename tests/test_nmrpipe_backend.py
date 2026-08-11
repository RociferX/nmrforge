"""NMRPipe 后端行为测试（Windows 上无 NMRPipe，验证优雅降级）。"""

from __future__ import annotations

from pathlib import Path

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


def test_reconstruct_nus_rejects_uniform(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_small")
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.reconstruct_nus(exp, {})
    assert result["success"] is False
    assert "非 NUS" in result["message"]


@pytest.mark.skipif(not _NO_NMRPIPE, reason="本机已安装 NMRPipe，跳过缺失路径测试")
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
