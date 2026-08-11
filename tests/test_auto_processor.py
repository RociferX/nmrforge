"""AutoProcessor.process_matrix 最小闭环（uniform 2D 合成 FID 矩阵）。"""

from __future__ import annotations

import numpy as np

from backend.native_backend import NativeBackend
from core.data.bruker_reader import read_dataset
from workflow.engine import AutoProcessor


def test_auto_processor_uniform_2d(bruker_dir: object) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    rng = np.random.default_rng(1)
    n1, n2 = 256, 2048
    f2_bin = 700  # F1 调制频率取 0：alt 修正后峰落在中间
    k = np.arange(n1)[:, None]
    t = np.arange(n2)[None, :]
    s = (
        5000.0
        * np.exp(-t / 300.0)
        * np.exp(-k / 60.0)
        * np.exp(1j * 2 * np.pi * (f2_bin * t / n2))
    )
    s += rng.normal(0, 5, size=(n1, n2)) + 1j * rng.normal(0, 5, size=(n1, n2))
    processor = AutoProcessor(backend=NativeBackend())
    result = processor.process_matrix(exp, s)
    assert result.status == "accept"
    assert result.quality is not None
    assert result.quality.decision.value == "accept"
