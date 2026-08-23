"""NMRPipe 复型拆包测试(重构平面相位搜索已随 workflow.recon_phase_search
删除,0.2.164)。"""

from __future__ import annotations

import numpy as np

from core.data.pipe_io import _unpack_interleaved


def test_unpack_interleaved() -> None:
    real = np.zeros((4, 3))
    real[0] = 1.0
    real[1] = 2.0
    real[2] = 3.0
    real[3] = 4.0
    cx = _unpack_interleaved(real)
    assert cx.shape == (2, 3)
    assert np.allclose(cx[0], 1.0 + 2.0j)
    assert np.allclose(cx[1], 3.0 + 4.0j)
