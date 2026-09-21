"""SMILE Memory estimation and guardrail testing (0.2.112, calibrated from VM measured)."""

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
    # 3D HNCACB direct dimension 1H:SW≈8196.7Hz,SF=600.13MHz -> SW≈13.66ppm.
    return _Exp(
        dimensions=[_Dim("F3", 8196.721, 600.133), _Dim("F2", 0, 0), _Dim("F1", 0, 0)],
        ndim=3,
    )


def test_direct_points_after_ext() -> None:
    exp = _exp_hncacb()
    # 2048 points, window 4ppm/13.66ppm ≈ 0.293 -> ~600 points (VM measured base 600 -> 665MB).
    pts = direct_points_after_ext(exp, 2048, 10.5, 6.5)
    assert 550 <= pts <= 650, pts
    # Window 1ppm -> ~150 points (VM measured 150 -> 179MB).
    pts2 = direct_points_after_ext(exp, 2048, 8.0, 7.0)
    assert 130 <= pts2 <= 180, pts2
    # Full size without EXT.
    pts3 = direct_points_after_ext(exp, 2048, None, None)
    assert pts3 == 2048


def test_estimate_smile_peak_mb_calibrated() -> None:
    # 0.2.199-patch15: Model = direct dimension points x indirect dimension iteration FT size
    # product x 16B x 1.06, caliber alignment SMILE start self-report Memory Used. HNCACB(NusTD
    # product 4000,FT 256 x 256): 600 points -> ~636MB(measured 665).
    m600 = estimate_smile_peak_mb(3, 600, [80, 50])
    assert 600 <= m600 <= 680, m600
    m2048 = estimate_smile_peak_mb(3, 2048, [80, 50])
    assert 2100 <= m2048 <= 2350, m2048
    # SampleK reference point: 168 points, NusTD(292,290) -> align SMILE self-report 2.8GB.
    ref = estimate_smile_peak_mb(3, 168, [292, 290])
    assert 2700 <= ref <= 3000, ref
    # 2D conservative lower bound.
    assert estimate_smile_peak_mb(2, 640, 256) == 128.0


def test_dev_smile_memory_ceiling() -> None:
    """0.2.199-patch15: Development environment SMILE Test memory upper limit 2.8GB (document
    constraints). Development VM(16GB, host 32GB is unstable) Verified safety peak ≈2.8GB(zero
    filling 1024, sampleK,SMILE self-reported Memory Used); >= 5.6GB(zero filling 2048 direct
    dimension (doubled) will trigger an unexpected power outage of the host. test data/The rerun
    must ensure that the peak value is estimated <= 2.8GB."""
    ref = estimate_smile_peak_mb(3, 168, [292, 290])
    # Alignment SMILE self-reported, does not approach collapse magnitude.
    assert 2700 <= ref <= 2800 * 1.03
    # Test fixture nus_3d(NusTD 48/128) is well below the upper limit.
    fixture = estimate_smile_peak_mb(3, 200, [48, 128])
    assert fixture < 2800
    # The crash magnitude (zero filling 2048 direct dimension doubled) must be significantly higher
    # than the upper limit (documentation warning).
    crash = estimate_smile_peak_mb(3, 336, [292, 290])
    assert crash >= 2 * ref


def test_memory_guard_ok() -> None:
    res = memory_guard(3, 600, 4000, available_mb=4096)
    assert res["ok"] is True
    assert res["message"] == ""


def test_memory_guard_insufficient_message() -> None:
    # Available 512MB,SMILE Peak value ~690MB -> Over limit, needed=1GB.
    res = memory_guard(3, 600, 4000, available_mb=512)
    assert res["ok"] is False
    assert res["needed_gb"] == 1
    assert "Please provide at least 1 GB of memory" in res["message"]
    # Large grid/big direct dimension -> need more.
    res2 = memory_guard(3, 2048, 4000, available_mb=1024)
    assert res2["ok"] is False
    assert res2["needed_gb"] >= 2


def test_memory_guard_respects_safety_factor() -> None:
    # Peak value 690MB, available 812MB x 0.85≈690 -> critical.
    res = memory_guard(3, 600, 4000, available_mb=812)
    assert res["ok"] is (690.0 <= 812 * MEM_SAFETY)
