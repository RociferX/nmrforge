"""Early stopping：连续 n 次 improvement < 阈值即停止。

也用于 NUS 迭代收敛监控（框架 §62）。
"""

from __future__ import annotations


class EarlyStopping:
    def __init__(self, improvement_threshold: float = 0.01, max_no_improvement: int = 2) -> None:
        self._threshold = improvement_threshold
        self._max_no_improvement = max_no_improvement
        self._stale = 0

    def should_stop(self, previous_score: float, current_score: float) -> bool:
        """返回是否应停止：相对改进连续低于阈值达到上限。"""
        improvement = (current_score - previous_score) / max(abs(previous_score), 1e-9)
        self._stale = self._stale + 1 if improvement < self._threshold else 0
        return self._stale >= self._max_no_improvement
