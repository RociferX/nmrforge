"""Point-by-point parity of the window vectors with VM nmrPipe measurements (0.2.191)."""

from __future__ import annotations

import numpy as np

from workflow.window_optimize import _axis_sw, _window_vector


def test_window_vector_sp_matches_nmrpipe_measured() -> None:
    """The SP window vector matches the VM nmrPipe all-ones FID measurement point by
    point (0.2.191)."""
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
    """The GM window vector matches the VM real 3.fid direct dimension (SW=19230.77 Hz)
    point by point.

    Reading the NMRPipe GM 8/15 output of an all-ones FID point by point: the peak sits at
    302/1024 with w[302]~1.2179; with g3=0.5 the peak moves to 813. The constant is
    k=1/(2*sqrt(ln2))=0.6005612 (measured), not the 0.6 approximation found in the nmrglue
    source.
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
    """The EM window depends on the spectral width SW: w[i]=exp(-pi*lb/sw*i), with the
    first point scaled by c (0.2.191)."""
    sw = 19230.77
    w = _window_vector({"type": "exp", "lb": 5.0, "c": 1.0}, 1024, sw=sw)
    assert abs(w[0] - 1.0) < 1e-9
    assert abs(w[1] - float(np.exp(-np.pi * 5.0 / sw))) < 1e-9


def test_window_vector_none_is_ones() -> None:
    """No window / off is always all ones; the first-point factor is not applied."""
    w = _window_vector({"type": "none"}, 64)
    assert np.all(w == 1.0)
    w2 = _window_vector({"type": "off"}, 64)
    assert np.all(w2 == 1.0)


def test_axis_sw_from_header() -> None:
    """SW comes from the fid header FDFxSW (0.2.191: accurate GM/EM modelling needs it)."""
    assert _axis_sw({"FDF1SW": 5000.0}, "F1") == 5000.0
    assert abs(_axis_sw({"FDF3SW": "19230.769231"}, "F3") - 19230.769231) < 1e-6
    assert _axis_sw({}, "F1") == 0.0
