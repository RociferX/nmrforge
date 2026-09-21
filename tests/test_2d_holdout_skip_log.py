"""2D leaves residuals: the direct dimension segment is not skipped silently when it cannot be cut
off (0.2.199-patch29hz - fix 24, question 7)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import backend.nmrpipe_backend as npb
import backend.script_generator as script_generator
from backend.nmrpipe_backend import NMRPipeBackend


class _FakeRuntime:
    """False csh: Do not run, unify rc=0 (missing products are handled by the candidate loop with
    ok=False)."""

    def run(self, args, cwd=None, timeout=None):
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _experiment(root: Path) -> SimpleNamespace:
    """2D NUS Experiment: Holdout sets require nuslist (single column complex point index)."""
    (root / "nuslist").write_text(
        "".join(str(i) + chr(10) for i in range(12)), encoding="utf-8"
    )
    return SimpleNamespace(
        ndim=2,
        source_path=str(root),
        sampling=SimpleNamespace(mode="NUS", nus_list=[(i,) for i in range(12)]),
    )


def _backend(monkeypatch) -> NMRPipeBackend:
    """Backend: fake csh + fake script generation (do not run real NMRPipe)."""
    monkeypatch.setattr(npb, "CshRuntime", _FakeRuntime)

    def fake_reconstruct_nus(self, experiment, params=None, **kwargs):
        return {"success": True, "script": "SMILE -nSigma 5\n", "logs": []}

    monkeypatch.setattr(NMRPipeBackend, "reconstruct_nus", fake_reconstruct_nus)
    return NMRPipeBackend()


def test_missing_direct_segment_logs_skip(tmp_path: Path, monkeypatch) -> None:
    """The line before SMILE is not TP -> build_2d_direct_only_script returns empty -> log must be
    left."""
    monkeypatch.setattr(
        script_generator, "build_2d_direct_only_script", lambda text: ""
    )
    backend = _backend(monkeypatch)
    result = backend.smile_scan(
        _experiment(tmp_path),
        {"nthread": 1},
        [{"nsigma": 5.0, "thresh": 0.95}],
        work_dir=tmp_path / "scan",
        holdout_ratio=0.25,
    )
    assert result["success"] is True
    assert any(
        log.startswith("2D hold-out residual: cannot extract")
        for log in result["logs"]
    )
    # The holdout set is still in effect, but there is no residual indicator.
    assert result["holdout_file"]


def test_direct_segment_present_logs_step1(tmp_path: Path, monkeypatch) -> None:
    """The direct dimension segment is intercepted -> no skip prompt appears, but step1 rc log."""
    monkeypatch.setattr(
        script_generator,
        "build_2d_direct_only_script",
        lambda text: "# direct 2D" + chr(10),
    )
    backend = _backend(monkeypatch)
    result = backend.smile_scan(
        _experiment(tmp_path),
        {"nthread": 1},
        [{"nsigma": 5.0, "thresh": 0.95}],
        work_dir=tmp_path / "scan",
        holdout_ratio=0.25,
    )
    assert not any(
        log.startswith("2D hold-out residual: cannot extract")
        for log in result["logs"]
    )
    assert any(
        log.startswith("step1 2D direct dimension:") for log in result["logs"]
    )
