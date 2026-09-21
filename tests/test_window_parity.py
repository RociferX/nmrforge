"""Window vector and VM nmrPipe measured point-by-point consistency test (0.2.191)."""

from __future__ import annotations

import numpy as np

from workflow.window_optimize import _axis_sw, _window_vector


def test_window_vector_sp_matches_nmrpipe_measured() -> None:
    """The SP window vector is identical to the VM nmrPipe all 1 FID measured point by point
    (0.2.191)."""
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
    """GM window vector is consistent with VM real 3.fid direct dimension (SW=19230.77 Hz) measured
    point by point. All 1 FID point by point reading NMRPipe GM 8/15 output: peak value
    302/1024, w[302]~1.2179; peak position when g3=0.5 813. The constant
    k=1/(2*sqrt(ln2))=0.6005612(measured), rather than the 0.6 approximation in the nmrglue
    source code."""
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
    """EM Window-dependent spectrum width SW: w[i]=exp(-pi*lb/sw*i), first point multiplied by
    c(0.2.191)."""
    sw = 19230.77
    w = _window_vector({"type": "exp", "lb": 5.0, "c": 1.0}, 1024, sw=sw)
    assert abs(w[0] - 1.0) < 1e-9
    assert abs(w[1] - float(np.exp(-np.pi * 5.0 / sw))) < 1e-9


def test_window_vector_none_is_ones() -> None:
    """No window/off is always all 1, and is not multiplied by the first point coefficient."""
    w = _window_vector({"type": "none"}, 64)
    assert np.all(w == 1.0)
    w2 = _window_vector({"type": "off"}, 64)
    assert np.all(w2 == 1.0)


def test_axis_sw_from_header() -> None:
    """SW reads from fid header FDFxSW (0.2.191,GM/EM requires spectral width for accurate
    modeling)."""
    assert _axis_sw({"FDF1SW": 5000.0}, "F1") == 5000.0
    assert abs(_axis_sw({"FDF3SW": "19230.769231"}, "F3") - 19230.769231) < 1e-6
    assert _axis_sw({}, "F1") == 0.0
