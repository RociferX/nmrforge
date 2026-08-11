"""后处理参数优化测试（相位/基线，内存内、只重构一次）。"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter

from workflow.param_optimize import (
    apply_post_params,
    default_post_grid,
    optimize_post_parameters,
)


def _synthetic_spectrum(shape=(64, 128), seed=7) -> np.ndarray:
    """密集强峰 + 低噪声合成谱（贴近真实谱，避免稀疏峰触发孤立峰簇误判）。"""
    rng = np.random.default_rng(seed)
    real = np.zeros(shape)
    for (y, x), amp in [((30, 60), 500), ((33, 55), 350), ((27, 52), 250)]:
        real[y, x] = amp
    real = gaussian_filter(real, sigma=(1.5, 1.5))
    return real + rng.normal(0, 0.1, size=shape)


def test_default_post_grid() -> None:
    grid = default_post_grid()
    assert len(grid) == 5 * 3 * 2  # p0 × p1 × baseline_order
    assert grid[0]["p0"] == -45.0 and grid[0]["baseline_order"] == 0


def test_apply_post_params_phase() -> None:
    spec = _synthetic_spectrum()
    corrected = apply_post_params(spec, {"p0": 90.0, "p1": 0.0, "baseline_order": 0})
    # 90° 相位后实部能量转到虚部
    assert np.abs(np.mean(np.abs(np.real(corrected)))) < np.abs(
        np.mean(np.abs(np.real(spec)))
    )


def test_optimize_recovers_phase_error() -> None:
    """45° 相位误差应被优化器以 p0≈-45 恢复。"""
    spec = _synthetic_spectrum() * np.exp(1j * np.deg2rad(45))
    grid = [
        {"p0": -45.0, "p1": 0.0, "baseline_order": 0},
        {"p0": 0.0, "p1": 0.0, "baseline_order": 0},
        {"p0": 45.0, "p1": 0.0, "baseline_order": 0},
    ]
    results = optimize_post_parameters(spec, grid)
    assert results[0].params["p0"] == -45.0
    assert results[0].overall > results[1].overall
    assert results[0].components["phase"] > results[1].components["phase"]


def test_optimize_on_result_callback() -> None:
    spec = _synthetic_spectrum() + 0j
    received: list[dict] = []

    def on_result(result) -> None:
        received.append((result.params["p0"], result.overall))

    optimize_post_parameters(
        spec, grid=[{"p0": 0.0, "p1": 0.0, "baseline_order": 0}], on_result=on_result
    )
    assert len(received) == 1
