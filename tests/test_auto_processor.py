"""AutoProcessor 最小闭环（uniform 2D 合成矩阵）。"""

from __future__ import annotations

import numpy as np

from backend.native_backend import NativeBackend
from core.data.bruker_reader import read_dataset
from workflow.engine import AutoProcessor


def test_auto_processor_uniform_2d(bruker_dir: object) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    rng = np.random.default_rng(1)
    shape = (256, 2048)
    data = rng.normal(0, 0.5, size=shape) + 1j * rng.normal(0, 0.5, size=shape)
    data[128, 1024] += 50000.0
    processor = AutoProcessor(backend=NativeBackend())
    result = processor.process_matrix(exp, data)
    assert result.status == "accept"
    assert result.quality is not None
    assert result.quality.decision.value == "accept"
