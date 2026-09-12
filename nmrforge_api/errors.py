"""对外接口异常层次(便于下游项目区分失败原因)。"""

from __future__ import annotations


class SensitivityError(RuntimeError):
    """参数敏感性接口的基类异常。"""


class DatasetError(SensitivityError):
    """数据集导入/识别失败(路径非 Bruker 原始目录、类型不支持等)。"""


class ReferenceError(SensitivityError):
    """参考谱/参考脚本构建或加载失败。"""


class SweepError(SensitivityError):
    """参数扫描计划/执行失败。"""


class MeasurementError(SensitivityError):
    """峰位测量失败(谱不可读、峰表为空等)。"""


__all__ = [
    "DatasetError",
    "MeasurementError",
    "ReferenceError",
    "SensitivityError",
    "SweepError",
]
