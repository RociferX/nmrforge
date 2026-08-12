"""分析步骤占位(峰归属/统计,后续实现;G2B-004 阶段 C)。"""

from __future__ import annotations

from typing import Any


def analyze(
    manager: Any,
    exp_id: str,
    data_id: str,
    **kwargs: Any,
) -> dict[str, str]:
    """分析步骤接口占位:返回 pending,不抛异常。

    后续实现:峰归属(按实验类型先验)、峰表统计与报告接入
    (core/reporting),并登记 workflow_ref="analyze" 的 WorkflowRun。
    """
    return {"status": "pending", "message": "待实现"}


__all__ = ["analyze"]
