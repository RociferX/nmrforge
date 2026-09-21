"""Final spectrum -> Sparky UCSF conversion test (0.2.162-patch15)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from workflow.ucsf_export import export_ucsf


def test_stepwise_export_ucsf_skips_1d_ft1(monkeypatch) -> None:
    """0.2.199-patch29gj-Fixed: 1D final spectrum (.ft1) does not call pipe2ucsf and skips
    directly."""
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
    assert not called  # Pipe2ucsf is not actually executed.


def test_export_ucsf_runs_pipe2ucsf_and_writes_target(tmp_path: Path) -> None:
    """Pipe2ucsf succeeded: calling parameter is correct, UCSF path is returned."""
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
    assert "UCSF has generated" in message
    assert calls[0][0] == "pipe2ucsf"
    assert str(source) in calls[0][1]
    assert str(target) in calls[0][2]


def test_export_ucsf_failure_returns_none_and_cleans_partial(tmp_path: Path) -> None:
    """Pipe2ucsf fails: None is returned, and the remaining semi-finished files are cleaned up."""
    source = tmp_path / "d_001.ft3"
    source.write_bytes(b"pipe")
    target = tmp_path / "d_001.ucsf"

    def fake_run(argv, cwd=None, timeout=None):
        target.write_bytes(b"partial")
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    path, message = export_ucsf(source, target, run=fake_run)
    assert path is None
    assert "conversion failed" in message and "boom" in message
    assert not target.exists()


def test_export_ucsf_exception_removes_stale_target(tmp_path: Path) -> None:
    """SMILE-005: The old UCSF must not be left behind to pretend to be the companion product of
    the current spectrum when the conversion startup fails."""
    source = tmp_path / "d_001.ft2"
    target = tmp_path / "d_001.ucsf"
    source.write_bytes(b"new-spectrum")
    target.write_bytes(b"stale-ucsf")

    def failed_run(*args, **kwargs):
        raise OSError("pipe2ucsf missing")

    path, message = export_ucsf(source, target, run=failed_run)
    assert path is None
    assert "execution failed" in message
    assert not target.exists()


def test_export_ucsf_missing_source_skips(tmp_path: Path) -> None:
    """Source spectrum does not exist: skip, do not call the tool."""
    path, message = export_ucsf(
        tmp_path / "missing.ft2", tmp_path / "missing.ucsf"
    )
    assert path is None
    assert "The source spectrum does not exist" in message


def test_export_ucsf_missing_tool_degrades_gracefully(
    tmp_path: Path, monkeypatch
) -> None:
    """There is no csh/pipe2ucsf on this machine (such as Windows development machine): Downgrade
    returns None and does not throw an exception."""
    source = tmp_path / "d_001.ft2"
    source.write_bytes(b"pipe")
    target = tmp_path / "d_001.ucsf"

    class _MissingCsh:
        def run(self, *args, **kwargs):
            raise RuntimeError("This machine was not found tcsh/csh")

    monkeypatch.setattr("backend.runtime.CshRuntime", _MissingCsh)
    path, message = export_ucsf(source, target)
    assert path is None
    assert "skipping UCSF conversion" in message
