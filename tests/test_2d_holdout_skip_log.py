"""2D hold-out residual: do not skip silently when the direct dimension segment cannot be
cut off (0.2.199-patch29hz - fix 24, question 7)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import backend.nmrpipe_backend as npb
import backend.script_generator as script_generator
from backend.nmrpipe_backend import NMRPipeBackend


class _FakeRuntime:
    """Fake csh: does not really run, always rc=0 (missing products are handled by the candidate
    loop with ok=False)."""

    def run(self, args, cwd=None, timeout=None):
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _experiment(root: Path) -> SimpleNamespace:
    """2D NUS experiment: the holdout set needs nuslist (single-column complex point indices)."""
    (root / "nuslist").write_text(
        "".join(str(i) + chr(10) for i in range(12)), encoding="utf-8"
    )
    return SimpleNamespace(
        ndim=2,
        source_path=str(root),
        sampling=SimpleNamespace(mode="NUS", nus_list=[(i,) for i in range(12)]),
    )


def _backend(monkeypatch) -> NMRPipeBackend:
    """Backend: fake csh + fake script generation (never runs real NMRPipe)."""
    monkeypatch.setattr(npb, "CshRuntime", _FakeRuntime)

    def fake_reconstruct_nus(self, experiment, params=None, **kwargs):
        return {"success": True, "script": "SMILE -nSigma 5\n", "logs": []}

    monkeypatch.setattr(NMRPipeBackend, "reconstruct_nus", fake_reconstruct_nus)
    return NMRPipeBackend()


def test_missing_direct_segment_logs_skip(tmp_path: Path, monkeypatch) -> None:
    """The line before SMILE is not TP -> build_2d_direct_only_script returns empty -> a log must
    be left."""
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
    assert result["success"] is False  # 0 candidates succeeded: no ranking, no promotion
    assert result["n_ok"] == 0
    assert any(
        log.startswith("2D 留出残差:无法从终跑脚本截出")
        for log in result["logs"]
    )
    assert result["holdout_file"]  # Holdout set still applies, only no residual metric.


def test_direct_segment_present_logs_step1(tmp_path: Path, monkeypatch) -> None:
    """Direct-dim segment can be cut -> no skip prompt appears, but a step1 rc log."""
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
        log.startswith("2D 留出残差:无法从终跑脚本截出")
        for log in result["logs"]
    )
    assert any(log.startswith("step1 2D 直接维:") for log in result["logs"])
