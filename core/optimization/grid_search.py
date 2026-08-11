"""网格搜索（小规模，例如 nSigma × thresh 手动优化入口）。"""

from __future__ import annotations

from core.optimization.candidate_generator import CandidateGenerator
from core.optimization.parameter_space import ParameterSpace


class GridSearch(CandidateGenerator):
    """参数网格枚举。"""

    def coarse(self, space: ParameterSpace) -> list[dict[str, object]]:
        raise NotImplementedError("Phase 3: 实现网格搜索")
