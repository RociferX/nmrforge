"""SMILE 参数优化模块测试（可选优化项，不进入自动流程）。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from core.data.bruker_reader import read_dataset
from workflow.smile_optimize import (
    SmileParameterResult,
    default_smile_grid,
    format_results,
    optimize_smile_parameters,
    save_report,
)


def _fake_reader(path: str):
    """测试用谱图读取器：噪声水平随路径中的 nSigma 变化，评分可区分。"""
    nsigma = float(Path(path).stem)
    rng = np.random.default_rng(0)
    data = rng.normal(0, max(0.2, 2.0 - nsigma * 0.4), size=(32, 64)).astype(np.float32)
    data[8, 20] += 20.0
    return {}, data


def _fake_backend(results_by_params: dict | None = None):
    """测试用后端桩：成功返回合成谱。"""
    seen: list[dict] = []

    class FakeBackend:
        def reconstruct_nus(self, experiment, params: dict) -> dict:
            seen.append(dict(params))
            return {"success": True, "spectrum_path": f"/fake/{params['nsigma']}.ft3"}

    return FakeBackend(), seen


def test_default_smile_grid() -> None:
    grid = default_smile_grid()
    assert len(grid) == 9
    assert grid[0] == {
        "nsigma": 3.0,
        "thresh": 0.90,
        "smile_xq3": 2.0,
        "smile_scaling": True,
    }
    combos = {(g["nsigma"], g["thresh"]) for g in grid}
    assert len(combos) == 9


def test_optimize_scores_and_sorts(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    backend, seen = _fake_backend()
    grid = [
        {"nsigma": 3.0, "thresh": 0.90},
        {"nsigma": 5.0, "thresh": 0.99},
    ]
    results = optimize_smile_parameters(
        exp, backend, grid, reader=_fake_reader
    )
    assert len(results) == 2
    assert len(seen) == 2
    assert results[0].overall > results[1].overall  # 降序
    assert results[0].decision in ("accept", "warning", "rollback")
    assert results[0].params["nsigma"] == 5.0  # 噪声更低 → 评分更高 → 排前面
    assert results[0].components["snr"] > 0


def test_optimize_failure_graceful() -> None:
    exp = read_dataset(Path("tests/fixtures/bruker/nus_3d"))

    class FailingBackend:
        def reconstruct_nus(self, experiment, params: dict) -> dict:
            return {"success": False, "message": "缺 nuslist"}

    results = optimize_smile_parameters(
        exp, FailingBackend(), grid=[{"nsigma": 5.0, "thresh": 0.95}]
    )
    assert len(results) == 1
    assert results[0].decision == "failed"
    assert results[0].message == "缺 nuslist"


def test_save_report_and_format(tmp_path: Path) -> None:
    results = [
        SmileParameterResult(
            params={"nsigma": 5.0, "thresh": 0.95},
            decision="accept",
            overall=74.1,
            components={"snr": 92.0, "phase": 78.0, "baseline": 86.0, "artifact": 40.0},
        )
    ]
    out = save_report(results, tmp_path / "report.json")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload[0]["params"]["nsigma"] == 5.0
    assert payload[0]["overall"] == 74.1
    table = format_results(results)
    assert "nSigma" in table and "thresh" in table
    assert "5.0" in table and "74.1" in table


def test_on_result_callback(bruker_dir: Path) -> None:
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(bruker_dir / "nus_3d")
    backend, _seen = _fake_backend()
    received: list[dict] = []

    def on_result(result) -> None:
        received.append((result.params["nsigma"], result.overall))

    optimize_smile_parameters(
        exp,
        backend,
        grid=[{"nsigma": 3.0, "thresh": 0.90}, {"nsigma": 5.0, "thresh": 0.99}],
        reader=_fake_reader,
        on_result=on_result,
    )
    assert len(received) == 2  # 每组评分后都立即回调
    assert received[0][0] == 3.0 and received[1][0] == 5.0
