"""2D 留出残差:截不出直接维段时不静默跳过(0.2.199-补29hz-修24,问题7)。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import backend.nmrpipe_backend as npb
import backend.script_generator as script_generator
from backend.nmrpipe_backend import NMRPipeBackend


class _FakeRuntime:
    """假 csh:不真跑,统一 rc=0(产物缺失由候选循环按 ok=False 处理)。"""

    def run(self, args, cwd=None, timeout=None):
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _experiment(root: Path) -> SimpleNamespace:
    """2D NUS 实验:留出集需要 nuslist(单列复点索引)。"""
    (root / "nuslist").write_text(
        "".join(str(i) + chr(10) for i in range(12)), encoding="utf-8"
    )
    return SimpleNamespace(
        ndim=2,
        source_path=str(root),
        sampling=SimpleNamespace(mode="NUS", nus_list=[(i,) for i in range(12)]),
    )


def _backend(monkeypatch) -> NMRPipeBackend:
    """后端:假 csh + 假脚本生成(不跑真实 NMRPipe)。"""
    monkeypatch.setattr(npb, "CshRuntime", _FakeRuntime)

    def fake_reconstruct_nus(self, experiment, params=None, **kwargs):
        return {"success": True, "script": "SMILE -nSigma 5\n", "logs": []}

    monkeypatch.setattr(NMRPipeBackend, "reconstruct_nus", fake_reconstruct_nus)
    return NMRPipeBackend()


def test_missing_direct_segment_logs_skip(tmp_path: Path, monkeypatch) -> None:
    """SMILE 前不是 TP 行 → build_2d_direct_only_script 返回空 → 必须留日志。"""
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
        log.startswith("2D 留出残差:无法从终跑脚本截出")
        for log in result["logs"]
    )
    assert result["holdout_file"]  # 留出集仍生效,只是没有残差指标


def test_direct_segment_present_logs_step1(tmp_path: Path, monkeypatch) -> None:
    """截得出直接维段 → 不出现跳过提示,而是 step1 rc 日志。"""
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
