"""局部搜索（坐标上升等，框架 §31）。"""

from __future__ import annotations

from core.optimization.candidate_generator import CandidateGenerator
from core.optimization.parameter_space import ParameterSpace


class LocalSearch(CandidateGenerator):
    """基于相邻候选的局部优化。"""

    def refine(self, space: ParameterSpace, best: dict[str, object]) -> list[dict[str, object]]:
        raise NotImplementedError("Phase 3: 实现局部搜索")
