"""SMILE 内存估计与护栏测试(0.2.112,标定自 VM 实测)。"""

from __future__ import annotations

from dataclasses import dataclass

from backend.memory_guard import (
    MEM_SAFETY,
    direct_points_after_ext,
    estimate_smile_peak_mb,
    memory_guard,
)


@dataclass
class _Dim:
    logical_axis: str
    sw: float
    sf: float


@dataclass
class _Exp:
    dimensions: list
    ndim: int


def _exp_hncacb() -> _Exp:
    # 3D HNCACB 直接维 1H:SW≈8196.7Hz,SF=600.13MHz → SW≈13.66ppm
    return _Exp(
        dimensions=[_Dim("F3", 8196.721, 600.133), _Dim("F2", 0, 0), _Dim("F1", 0, 0)],
        ndim=3,
    )


def test_direct_points_after_ext() -> None:
    exp = _exp_hncacb()
    # 2048 点、窗口 4ppm/13.66ppm ≈ 0.293 → ~600 点(VM 实测基准 600→665MB)
    pts = direct_points_after_ext(exp, 2048, 10.5, 6.5)
    assert 550 <= pts <= 650, pts
    # 窗口 1ppm → ~150 点(VM 实测 150→179MB)
    pts2 = direct_points_after_ext(exp, 2048, 8.0, 7.0)
    assert 130 <= pts2 <= 180, pts2
    # 无 EXT 时全尺寸
    pts3 = direct_points_after_ext(exp, 2048, None, None)
    assert pts3 == 2048


def test_estimate_smile_peak_mb_calibrated() -> None:
    # 3D 网格 4000:600 点 → ~690MB(实测 665);2048 点 → ~2355MB(实测 2284)
    m600 = estimate_smile_peak_mb(3, 600, 4000)
    assert 650 <= m600 <= 730, m600
    m2048 = estimate_smile_peak_mb(3, 2048, 4000)
    assert 2200 <= m2048 <= 2450, m2048
    # 2D 保守下限
    assert estimate_smile_peak_mb(2, 640, 256) == 128.0


def test_memory_guard_ok() -> None:
    res = memory_guard(3, 600, 4000, available_mb=4096)
    assert res["ok"] is True
    assert res["message"] == ""


def test_memory_guard_insufficient_message() -> None:
    # 可用 512MB,SMILE 峰值 ~690MB → 超限,needed=1GB
    res = memory_guard(3, 600, 4000, available_mb=512)
    assert res["ok"] is False
    assert res["needed_gb"] == 1
    assert "请至少提供 1 GB 内存" in res["message"]
    # 大网格/大直接维 → 需要更多
    res2 = memory_guard(3, 2048, 4000, available_mb=1024)
    assert res2["ok"] is False
    assert res2["needed_gb"] >= 2


def test_memory_guard_respects_safety_factor() -> None:
    # 峰值 690MB,可用 812MB×0.85≈690 → 临界
    res = memory_guard(3, 600, 4000, available_mb=812)
    assert res["ok"] is (690.0 <= 812 * MEM_SAFETY)


def test_memory_guard_block_false_warns_not_blocks() -> None:
    """0.2.124:block=False 时超限仅警告(不阻断),可强制运行。"""
    res = memory_guard(3, 2048, 4000, available_mb=1024, block=False)
    assert res["ok"] is False
    assert res["blocked"] is False
    assert res["needed_gb"] >= 2
    assert "请至少提供" not in res["message"]
    assert "已超可用预算" in res["message"]


def test_memory_guard_block_true_default() -> None:
    """0.2.124:block 默认 True,保持原阻断语义。"""
    res = memory_guard(3, 2048, 4000, available_mb=1024)
    assert res["blocked"] is True
    assert "请至少提供" in res["message"]
