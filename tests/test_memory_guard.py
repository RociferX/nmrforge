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
    # 0.2.199-补15:模型 = 直接维点数 × 间接维迭代 FT 尺寸乘积 × 16B × 1.06,
    # 口径对齐 SMILE 启动自报 Memory Used。
    # HNCACB(NusTD 积 4000,FT 256×256):600 点 → ~636MB(实测 665)
    m600 = estimate_smile_peak_mb(3, 600, [80, 50])
    assert 600 <= m600 <= 680, m600
    m2048 = estimate_smile_peak_mb(3, 2048, [80, 50])
    assert 2100 <= m2048 <= 2350, m2048
    # sampleK 参考点:168 点、NusTD(292,290)→ 对齐 SMILE 自报 2.8GB
    ref = estimate_smile_peak_mb(3, 168, [292, 290])
    assert 2700 <= ref <= 3000, ref
    # 2D 保守下限
    assert estimate_smile_peak_mb(2, 640, 256) == 128.0


def test_dev_smile_memory_ceiling() -> None:
    """0.2.199-补15:开发环境 SMILE 测试内存上限 2.8GB(文档约束)。

    开发 VM(16GB,宿主 32GB 不稳)已验证安全峰值 ≈2.8GB(填零1024,
    sampleK,SMILE 自报 Memory Used);≥5.6GB(填零2048 直接维翻倍)会
    触发宿主意外断电。测试数据/复跑必须保证估计峰值 ≤ 2.8GB。
    """
    ref = estimate_smile_peak_mb(3, 168, [292, 290])
    assert 2700 <= ref <= 2800 * 1.03  # 对齐 SMILE 自报,不逼近崩溃量级
    # 测试夹具 nus_3d(NusTD 48/128)远低于上限
    fixture = estimate_smile_peak_mb(3, 200, [48, 128])
    assert fixture < 2800
    # 崩溃量级(填零2048 直接维翻倍)必须显著高于上限(文档警示)
    crash = estimate_smile_peak_mb(3, 336, [292, 290])
    assert crash >= 2 * ref


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
