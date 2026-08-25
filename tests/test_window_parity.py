"""窗向量与 VM nmrPipe 实测逐点一致性测试(0.2.191)。"""

from __future__ import annotations

import numpy as np

from workflow.window_optimize import _axis_sw, _window_vector


def test_window_vector_sp_matches_nmrpipe_measured() -> None:
    """SP 窗向量与 VM nmrPipe 全 1 FID 实测逐点一致(0.2.191)。"""
    w = _window_vector(
        {"type": "sine_bell", "off": 0.5, "end": 0.98, "pow": 2, "c": 0.5},
        1024,
    )
    assert abs(w[0] - 0.5) < 1e-9
    assert abs(w[1] - 0.99999797) < 2e-5
    assert abs(w[1023] - 0.00394264) < 2e-5

    w2 = _window_vector(
        {"type": "sine_bell", "off": 0.45, "end": 0.95, "pow": 1, "c": 0.5},
        1024,
    )
    assert abs(w2[0] - 0.493844) < 2e-5
    assert abs(w2[1] - 0.98792702) < 2e-5
    assert abs(w2[1023] - 0.156434) < 2e-5


def test_window_vector_gm_matches_nmrpipe_measured() -> None:
    """GM 窗向量与 VM 真实 3.fid 直接维(SW=19230.77 Hz)实测逐点一致。

    全 1 FID 逐点读取 NMRPipe GM 8/15 输出:峰值 302/1024、w[302]~1.2179;
    g3=0.5 时峰位 813。常数 k=1/(2*sqrt(ln2))=0.6005612(实测),而非
    nmrglue 源码里的 0.6 近似。
    """
    sw = 19230.77
    w = _window_vector(
        {"type": "gaussian", "g1": 8.0, "g2": 15.0, "g3": 0.0, "c": 1.0},
        1024,
        sw=sw,
    )
    assert abs(w[0] - 1.0) < 1e-9
    assert abs(w[302] - 1.21794093) < 2e-5
    assert abs(w[1023] - 0.39473772) < 2e-5
    assert abs(int(w.argmax()) - 302) <= 1

    w2 = _window_vector(
        {"type": "gaussian", "g1": 8.0, "g2": 15.0, "g3": 0.5, "c": 1.0},
        1024,
        sw=sw,
    )
    assert abs(w2[0] - 0.56743801) < 2e-5
    assert abs(w2[813] - 2.37653232) < 2e-5


def test_window_vector_em_uses_sw() -> None:
    """EM 窗依赖谱宽 SW:w[i]=exp(-pi*lb/sw*i),首点乘 c(0.2.191)。"""
    sw = 19230.77
    w = _window_vector({"type": "exp", "lb": 5.0, "c": 1.0}, 1024, sw=sw)
    assert abs(w[0] - 1.0) < 1e-9
    assert abs(w[1] - float(np.exp(-np.pi * 5.0 / sw))) < 1e-9


def test_window_vector_none_is_ones() -> None:
    """无窗/off 恒为全 1,不乘首点系数。"""
    w = _window_vector({"type": "none"}, 64)
    assert np.all(w == 1.0)
    w2 = _window_vector({"type": "off"}, 64)
    assert np.all(w2 == 1.0)


def test_axis_sw_from_header() -> None:
    """SW 从 fid 头 FDFxSW 读取(0.2.191,GM/EM 精确建模需要谱宽)。"""
    assert _axis_sw({"FDF1SW": 5000.0}, "F1") == 5000.0
    assert abs(_axis_sw({"FDF3SW": "19230.769231"}, "F3") - 19230.769231) < 1e-6
    assert _axis_sw({}, "F1") == 0.0
