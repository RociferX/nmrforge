"""NUS 混合相位优化编排测试(直接维显示层 + 间接维后端候选)。"""

from __future__ import annotations

from pathlib import Path

import workflow.display_hybrid_optimize as hybrid
from core.data.bruker_reader import read_dataset


class _FakeBackend:
    def __init__(self, work: Path) -> None:
        self.work = str(work)
        self.reconstruct_params: list[dict] = []
        self.finalize_phases: list[dict] = []

    def reconstruct_nus(self, experiment, params) -> dict:
        self.reconstruct_params.append(dict(params or {}))
        return {"success": True, "spectrum_path": f"{self.work}/out.ft3", "logs": []}

    def finalize_nus(self, experiment, phases=None, work_dir=None, params=None) -> dict:
        phases = dict(phases or {})
        self.finalize_phases.append(phases)
        p1 = 0.0
        if phases:
            p1 = float(list(phases.values())[-1][1])
        return {
            "success": True,
            "spectrum_path": f"{self.work}/fin_{len(self.finalize_phases)}_{int(p1)}.ft3",
            "logs": [],
        }


def _score_from_path(path: str) -> tuple[float, dict]:
    p1 = float(path.split("_")[-1].split(".")[0])
    return 100.0 - abs(p1 - 30.0), {}


def test_direct_axis_from_header() -> None:
    assert hybrid.direct_axis_from_header({"FDF1LABEL": "15N", "FDF2LABEL": "1H"}, "1H") == 1
    assert (
        hybrid.direct_axis_from_header(
            {"FDF1LABEL": "15N", "FDF2LABEL": "1H", "FDF3LABEL": "13C"}, "13C"
        )
        == 2
    )


def test_optimize_nus_hybrid_order_and_phase_selection(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """先重构→显示层估直接维→带直接相位重跑 SMILE→间接候选→最终 finalize。"""
    experiment = read_dataset(bruker_dir / "nus_3d")
    work = tmp_path / "work"
    backend = _FakeBackend(work)
    monkeypatch.setattr(hybrid, "estimate_direct_phase", lambda path, exp: (30.0, -5.0, 80.0))
    result = hybrid.optimize_nus_hybrid(
        experiment,
        backend,
        p0_values=(0.0,),
        p1_values=(-30.0, 0.0, 30.0),
        score_fn=_score_from_path,
        work_dir=work,
    )
    assert len(backend.reconstruct_params) == 2
    assert backend.reconstruct_params[0].get("direct_phase_override") is None
    assert backend.reconstruct_params[1].get("direct_phase_override") == (30.0, -5.0)
    indirect = [ph for ph in backend.finalize_phases[:-1] if len(ph) == 1]
    assert indirect, "应有逐轴间接候选"
    assert result["phases"]["F2"][1] == 30.0
    assert result["phases"]["F1"][1] == 30.0
    assert result["direct_phase"] == (30.0, -5.0)
    assert result["backend_runs"] == 9  # 2 次 SMILE + 6 次间接候选 + 1 次最终 finalize
