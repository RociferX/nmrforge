"""批处理：串行复用 AutoProcessor，单实验失败不中断，实时进度/日志。"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from core.data.internal_data_model import Experiment
from workflow.engine import AutoProcessor, RunResult


def run_batch(
    processor: AutoProcessor,
    experiments: Iterable[Experiment],
    progress: Callable[[int, int, str], None] | None = None,
) -> list[RunResult]:
    """串行处理多个实验，返回每个实验的 RunResult。"""
    raise NotImplementedError("Phase 1: 实现批处理")
