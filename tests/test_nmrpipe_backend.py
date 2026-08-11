"""NMRPipe 后端行为测试（Windows 上无 NMRPipe，验证优雅降级）。"""

from __future__ import annotations

from pathlib import Path

from backend.factory import create_backend
from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
from core.planning.method_selector import select_method


def test_factory_returns_nmrpipe_backend() -> None:
    backend = create_backend({"backend": {"provider": "nmrpipe"}})
    assert isinstance(backend, NMRPipeBackend)


def test_health_check_missing_nmrpipe() -> None:
    backend = NMRPipeBackend(nmrpipe_bin="")
    health = backend.health_check()
    assert health["ok"] is False
    assert "nmrPipe" in health["message"]


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
