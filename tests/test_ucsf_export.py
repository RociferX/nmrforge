"""终谱 → Sparky UCSF 转换测试(0.2.162-补15)。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from workflow.ucsf_export import export_ucsf


def test_stepwise_export_ucsf_skips_1d_ft1(monkeypatch) -> None:
    """0.2.199-补29gj-修:1D 终谱(.ft1)不调用 pipe2ucsf,直接跳过。"""
    import workflow.stepwise as stepwise

    called: list[str] = []
    manager = SimpleNamespace()

    def _boom(*args, **kwargs):
        called.append("export")
        return "boom", "boom"

    monkeypatch.setattr(stepwise, "export_ucsf", _boom)
    path, message = stepwise._export_ucsf(
        manager, "exp_001", "d_001", "d_001.ft1"
    )
    assert path is None
    assert "1D" in message
    assert not called  # 未真正执行 pipe2ucsf


def test_export_ucsf_runs_pipe2ucsf_and_writes_target(tmp_path: Path) -> None:
    """pipe2ucsf 成功:调用参数正确,UCSF 路径返回。"""
    source = tmp_path / "d_001.ft2"
    source.write_bytes(b"pipe")
    target = tmp_path / "d_001.ucsf"
    calls: list[list[str]] = []

    def fake_run(argv, cwd=None, timeout=None):
        calls.append(argv)
        target.write_bytes(b"ucsf-data")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    path, message = export_ucsf(source, target, run=fake_run)
    assert path == str(target)
    assert "UCSF 已生成" in message
    assert calls[0][0] == "pipe2ucsf"
    assert str(source) in calls[0][1]
    assert str(target) in calls[0][2]


def test_export_ucsf_failure_returns_none_and_cleans_partial(tmp_path: Path) -> None:
    """pipe2ucsf 失败:返回 None,残留的半成品文件被清理。"""
    source = tmp_path / "d_001.ft3"
    source.write_bytes(b"pipe")
    target = tmp_path / "d_001.ucsf"

    def fake_run(argv, cwd=None, timeout=None):
        target.write_bytes(b"partial")
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    path, message = export_ucsf(source, target, run=fake_run)
    assert path is None
    assert "转换失败" in message and "boom" in message
    assert not target.exists()


def test_export_ucsf_exception_removes_stale_target(tmp_path: Path) -> None:
    """SMILE-005:转换启动失败时不得留下旧 UCSF 冒充当前谱伴生产物。"""
    source = tmp_path / "d_001.ft2"
    target = tmp_path / "d_001.ucsf"
    source.write_bytes(b"new-spectrum")
    target.write_bytes(b"stale-ucsf")

    def failed_run(*args, **kwargs):
        raise OSError("pipe2ucsf missing")

    path, message = export_ucsf(source, target, run=failed_run)
    assert path is None
    assert "执行失败" in message
    assert not target.exists()


def test_export_ucsf_missing_source_skips(tmp_path: Path) -> None:
    """源谱不存在:跳过,不调用工具。"""
    path, message = export_ucsf(
        tmp_path / "missing.ft2", tmp_path / "missing.ucsf"
    )
    assert path is None
    assert "源谱不存在" in message


def test_export_ucsf_missing_tool_degrades_gracefully(
    tmp_path: Path, monkeypatch
) -> None:
    """本机无 csh/pipe2ucsf(如 Windows 开发机):降级返回 None,不抛异常。"""
    source = tmp_path / "d_001.ft2"
    source.write_bytes(b"pipe")
    target = tmp_path / "d_001.ucsf"

    class _MissingCsh:
        def run(self, *args, **kwargs):
            raise RuntimeError("本机未找到 tcsh/csh")

    monkeypatch.setattr("backend.runtime.CshRuntime", _MissingCsh)
    path, message = export_ucsf(source, target)
    assert path is None
    assert "跳过 UCSF 转换" in message
