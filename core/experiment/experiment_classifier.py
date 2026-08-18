"""实验类型识别：规则 + 参数 + pulse program + 命名的多证据系统。

优先级：pulse program → 核组合 → 维度顺序 → FnMODE → 实验参数 → 数据集命名。
置信度约定：>0.9 自动处理；0.6-0.9 自动 + warning；<0.6 走 Generic 并请求用户确认。
当前实现：PULPROG 关键词（最具体优先）+ 核组合与模板比对。
"""

from __future__ import annotations

import core.experiments  # noqa: F401  导入即从 presets/*.yaml 注册模板
from core.data.internal_data_model import Experiment, ExperimentType
from core.experiments.registry import REGISTRY

# (pulprog 子串, 模板名)；长/具体关键词在前，避免 hncacb 被 hnca 误匹配
_PULPROG_TYPES: list[tuple[str, str]] = [
    ("hncacb", "HNCACB"),
    ("cbcaconh", "CBCA(CO)NH"),
    ("cbcanh", "CBCANH"),
    ("hncoca", "HN(CO)CA"),
    ("hnco", "HNCO"),
    ("hnca", "HNCA"),
    ("hnha", "HNHA"),
    ("hsqc", "HSQC"),
    ("hmqc", "HMQC"),
    ("hmbc", "HMBC"),
    ("tocsy", "TOCSY"),
    ("noesy", "NOESY"),
    ("roesy", "ROESY"),
    ("cosy", "COSY"),
]


def classify(experiment: Experiment) -> ExperimentType:
    """返回实验类型（名称 + 置信度 + 证据链）。"""
    acqus = experiment.acquisition_parameters.get("acqus", {})
    pulprog = str(acqus.get("PULPROG", "")).lower()
    evidence: list[str] = []

    matched: str | None = None
    for keyword, name in _PULPROG_TYPES:
        if keyword in pulprog:
            matched = name
            evidence.append(f"PULPROG 含 {keyword!r}")
            break

    if matched is None:
        generic = "generic_3d" if experiment.ndim == 3 else "generic_2d"
        return ExperimentType(
            name=generic,
            confidence=0.3,
            evidence=evidence + ["PULPROG 未识别，进入 Generic 安全管线"],
        )

    template = REGISTRY.get(matched)
    expected = set(template.indirect_nuclei + [template.direct_nucleus]) if template else set()
    nuclei = {d.nucleus for d in experiment.dimensions if d.nucleus}
    if expected and nuclei and nuclei == expected:
        confidence = 0.95
        evidence.append(f"核组合 {sorted(nuclei)} 与模板一致")
    else:
        confidence = 0.7
        evidence.append(f"核组合 {sorted(nuclei) if nuclei else '空'} 与模板不完全一致")
    return ExperimentType(name=matched, confidence=confidence, evidence=evidence)
