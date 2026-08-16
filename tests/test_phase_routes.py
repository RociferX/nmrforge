"""简单/进阶相位途径分派测试。"""

from __future__ import annotations

from pathlib import Path

import workflow.phase_routes as routes
from core.data.bruker_reader import read_dataset


class _FakeBackend:
    def __init__(self) -> None:
        self.process_calls: list[tuple] = []
        self.reconstruct_params: list[dict] = []
        self.finalize_phases: list[dict] = []

    def process(self, experiment, plan, params=None, direct_phase_override=None):
        self.process_calls.append(
            (experiment, plan, dict(params or {}), dict(direct_phase_override or {}))
        )
        return {"success": True, "spectrum_path": "out.ft2", "logs": []}

    def reconstruct_nus(self, experiment, params):
        self.reconstruct_params.append(dict(params or {}))
        return {"success": True, "spectrum_path": "out.ft3", "logs": []}

    def finalize_nus(self, experiment, phases=None, work_dir=None, params=None):
        self.finalize_phases.append(dict(phases or {}))
        return {"success": True, "spectrum_path": "out_final.ft3", "logs": []}

    def hilbert_spectrum(self, spectrum_path, work_dir=None, out_file=None, timeout=None):
        return {"success": True, "spectrum_path": "out_ht.ft2", "logs": []}


def test_simple_route_uniform_two_passes(monkeypatch, bruker_dir: Path) -> None:
    experiment = read_dataset(bruker_dir / "hsqc_small")
    backend = _FakeBackend()
    monkeypatch.setattr(
        routes,
        "estimate_all_axes",
        lambda path, exp: {"F1": (10.0, -2.0), "F2": (20.0, 3.0)},
    )
    result = routes.simple_route(experiment, backend)
    assert len(backend.process_calls) == 2
    assert backend.process_calls[0][2].get("direct_phase_search") is False
    assert backend.process_calls[1][3] == {
        "F1": (10.0, -2.0),
        "F2": (20.0, 3.0),
    }
    assert result["backend_runs"] == 2


def test_simple_route_nus_direct_override_and_indirect_finalize(
    monkeypatch, bruker_dir: Path
) -> None:
    experiment = read_dataset(bruker_dir / "nus_3d")
    backend = _FakeBackend()
    monkeypatch.setattr(
        routes,
        "estimate_all_axes",
        lambda path, exp: {"F3": (30.0, -5.0), "F2": (11.0, 0.0), "F1": (22.0, 0.0)},
    )
    result = routes.simple_route(experiment, backend)
    assert len(backend.reconstruct_params) == 2
    assert backend.reconstruct_params[1].get("direct_phase_override") == (30.0, -5.0)
    assert backend.finalize_phases and "F3" not in backend.finalize_phases[-1]
    assert result["backend_runs"] == 3


def test_advanced_route_uniform_delegates_to_old_optimizer(monkeypatch, bruker_dir: Path) -> None:
    experiment = read_dataset(bruker_dir / "hsqc_small")
    backend = _FakeBackend()
    called = []

    def fake_optimize(exp, be, **kwargs):
        called.append((exp, be))
        return type(
            "R",
            (),
            {
                "phases": {"F1": (1.0, 0.0), "F2": (2.0, 0.0)},
                "spectrum_path": "adv.ft2",
                "backend_runs": 46,
                "logs": [],
            },
        )()

    monkeypatch.setattr("workflow.phase_optimize.optimize_phase_sequential", fake_optimize)
    result = routes.advanced_route(experiment, backend)
    assert called and called[0][0] is experiment
    assert result["phases"]["F2"] == (2.0, 0.0)
    assert result["backend_runs"] == 46
