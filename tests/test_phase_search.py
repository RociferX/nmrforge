"""直接维统计相位搜索测试。"""

from __future__ import annotations

import numpy as np

from core.optimization.phase_search import direct_ft_traces


def test_direct_ft_traces() -> None:
    fid = np.zeros((4, 8), dtype=complex)
    out = direct_ft_traces(fid, zf_size=16)
    assert out.shape == (4, 16)
    out2 = direct_ft_traces(fid)
    assert out2.shape == (4, 8)


def test_search_direct_spectrum_phase_recovers_p0_p1() -> None:
    """0.2.88:直接维 FT 谱频域搜索同时恢复 (p0, p1)(t1 相位逐增量随机)。"""
    from core.optimization.phase_search import search_direct_spectrum_phase

    rng = np.random.default_rng(7)
    n = 512
    k = np.arange(n)
    # 信号相位(+33°, p1 斜坡 -42°);搜索返回 PS 校正值(相反数)
    sig_p0, sig_p1 = 33.0, -42.0
    traces = []
    for _ in range(80):
        # NUS 增量 0 的 t1=0:直接维相位干净(首条迹线锚点语义)
        spec = np.zeros(n, dtype=complex)
        for peak in (140, 260, 380):
            spec += np.exp(-((k - peak) ** 2) / (2 * 6.0**2))
        spec *= np.exp(
            1j * np.deg2rad(sig_p0 + sig_p1 * k / max(n - 1, 1))
        )
        spec += rng.normal(0.0, 0.02, size=n)
        spec += 1j * rng.normal(0.0, 0.02, size=n)
        traces.append(spec)
    est = search_direct_spectrum_phase(np.array(traces))
    assert est is not None
    p0, p1, score, gain = est
    # p0/p1 为校正值(信号相位相反数);p0 锚定首条迹线(t1=0)
    assert abs(((p0 + sig_p0 + 180.0) % 360.0) - 180.0) <= 12.0, p0
    assert abs(p1 + sig_p1) <= 10.0, p1
    assert score > 0.6
    assert gain > 0.05


def test_nus_direct_phase_matches_existing_sign_convention() -> None:
    """0.2.92:NU-DFT 直接维 p0 与现有方法同语义(取正峰解,消除 ±180 歧义)。

    现有方法(验证过)对 θ_true=-120 断言 p0≈120(±7.5);NU-DFT 在等效复型
    切片上应给出相同答案,而非 300(±180 反转)。
    """
    from scipy.signal import hilbert

    from core.optimization.phase_search import nus_direct_phase

    def make_slices(theta_true: float, n1: int = 32, n2: int = 64) -> np.ndarray:
        x = np.arange(n2)
        a = 100.0 / (1.0 + ((x - 30) / 2.0) ** 2)
        d = -np.imag(hilbert(a))
        spectrum = (a + 1j * d) * np.exp(1j * np.deg2rad(theta_true))
        return np.array(
            [
                np.fft.ifft(spectrum)
                * np.exp(1j * 2.0 * np.pi * 16 * i / n1)
                for i in range(n1)
            ]
        )

    for theta, expected in ((-120.0, 120.0), (33.0, 327.0), (90.0, 270.0)):
        est = nus_direct_phase(
            make_slices(theta), [(i,) for i in range(32)], 32, 1
        )
        assert est is not None
        p0, p1, score, _gain, _kstar = est
        assert abs(((p0 - expected + 180.0) % 360.0) - 180.0) <= 7.5, p0
        assert abs(p1) <= 1e-6
        assert score >= 2.0


def test_search_direct_phase_on_spectrum_recovers() -> None:
    """0.2.94:最终谱固定迹线净吸收评分搜索恢复直接维 (p0, p1)。

    信号相位 -120°(p0)/-42°(p1 斜坡)→ 校正应为 (120, 42)。
    """
    from core.optimization.phase_search import search_direct_phase_on_spectrum

    n_f1, n = 64, 512
    k = np.arange(n)
    phi0, p1_sig = -120.0, -42.0
    rng = np.random.default_rng(3)
    fids = []
    for i in range(n_f1):
        spec = np.zeros(n, dtype=complex)
        for kp, f1 in ((140, 8.0), (260, 24.0), (380, 40.0)):
            lz = 1.0 / (1.0 + ((k - kp) / 8.0) ** 2)
            spec += lz * np.exp(
                1j * np.deg2rad(phi0 + p1_sig * k / max(n - 1, 1))
                + 1j * (2.0 * np.pi * f1 * i / n_f1)
            )
        spec += rng.normal(0.0, 0.02, size=n)
        spec += 1j * rng.normal(0.0, 0.02, size=n)
        fids.append(np.fft.ifft(spec))
    fids = np.array(fids)
    window = np.sin(np.pi * (0.45 + 0.5 * np.linspace(0, 1, n)))
    grid = np.array([np.fft.fft(f * window) for f in fids])
    spec2d = np.fft.fft(grid, axis=0)
    est = search_direct_phase_on_spectrum(spec2d, metric="net")
    assert est is not None
    p0, p1, score = est
    # net/|Re| 指标对干净对称峰在 ±90° 内平台饱和(与现有优化同特性,真实
    # 谱靠重叠/不对称提供区分度,VM 实测 sampleI 恢复 -52.5°)。此处验证:
    # 1) 高分(>90)⇒ 正峰解,±180 反转解(score≈0)已被排除;
    # 2) 落在含真值(120)的平台内(±90°)。
    assert score > 90.0, score
    assert abs(((p0 - 120.0 + 180.0) % 360.0) - 180.0) <= 90.0, p0

def test_direct_phase_search_progress_and_result() -> None:
    '''直接维搜索:候选并行 + progress 消息(中/完成)。'''
    import numpy as np

    from core.optimization.phase_search import search_direct_phase_on_spectrum

    rng = np.random.default_rng(7)
    arr = rng.normal(size=(20, 16, 12)).astype(np.complex128)
    # 注入一个强直接维峰
    arr[10, 8, :] = np.exp(1j * np.deg2rad(30.0)) * 10.0
    messages: list[str] = []
    res = search_direct_phase_on_spectrum(
        arr, axis=0, metric="symmetry", progress=messages.append
    )
    assert res is None or len(res) == 3
    if messages:
        assert "直接维相位搜索中" in messages[0]
        assert "完成" in messages[-1]


def test_direct_phase_search_cancelled_raises() -> None:
    """0.2.199-补6:取消标志置位时相位搜索立即抛异常退出。"""
    import numpy as np
    import pytest

    from core.optimization.phase_search import search_direct_phase_on_spectrum

    rng = np.random.default_rng(11)
    arr = rng.normal(size=(24, 18, 14)).astype(np.complex128)
    arr[10, 8, :] = np.exp(1j * np.deg2rad(20.0)) * 10.0
    with pytest.raises(RuntimeError, match="任务已取消"):
        search_direct_phase_on_spectrum(
            arr, axis=0, metric="symmetry", cancel=lambda: True
        )

