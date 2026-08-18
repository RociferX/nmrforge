"""实验类型识别:核组合优先,排除不匹配模板,再在候选中用 PULPROG 精排。

识别顺序(用户方案):
1. 先识别正确核(各维度核素);
2. 按核组合(含同核重复计数,如 15N/15N)排除不匹配的模板;
3. 在剩余候选中用 PULPROG 关键词精排;无候选时 PULPROG 兜底(降置信)。
固体核磁类型(如 NNH/CCH,液体核磁没有)通过核组合规则自动入选。

置信度约定:>0.9 自动处理;0.6-0.9 自动 + warning;<0.6 走 Generic 并请求用户确认。
"""

from __future__ import annotations

import core.experiments  # noqa: F401  导入即从 presets/*.yaml 注册模板
from core.data.internal_data_model import Experiment, ExperimentType
from core.experiments.registry import REGISTRY

# (pulprog 子串, 模板名)；长/具体关键词在前,避免 hncacb 被 hnca 误匹配
_PULPROG_TYPES: list[tuple[str, str]] = [
    ("hncocannh", "NNH"),
    ("hncannh", "NNH"),
    ("nnh", "NNH"),
    ("cch", "CCH"),
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


def _data_nuclei(experiment: Experiment) -> Counter[str]:
    """数据各维度核组合(含同核重复计数)。"""
    return Counter(d.nucleus for d in experiment.dimensions if d.nucleus)


def _template_nuclei(template) -> Counter[str] | None:
    """模板核组合;generic 模板(direct 为空)不参与核匹配。"""
    if not template.direct_nucleus:
        return None
    return Counter([template.direct_nucleus] + list(template.indirect_nuclei))


def _nuclei_candidates(experiment: Experiment) -> list[str]:
    """按核组合过滤模板:核组合(含同核计数)一致才入选。"""
    data = _data_nuclei(experiment)
    return [
        name
        for name, template in REGISTRY.items()
        if _template_nuclei(template) == data
    ]


def classify(experiment: Experiment) -> ExperimentType:
    """核组合优先:排除不匹配模板 → 候选中 PULPROG 精排 → 兜底。"""
    acqus = experiment.acquisition_parameters.get("acqus", {})
    pulprog = str(acqus.get("PULPROG", "")).lower()
    evidence: list[str] = [f"核组合 {dict(sorted(_data_nuclei(experiment).items()))}"]

    candidates = _nuclei_candidates(experiment)

    # 1) 唯一候选:核组合直接命中(如 NNH 的 1H/15N/15N)
    if len(candidates) == 1:
        name = candidates[0]
        return ExperimentType(
            name=name,
            confidence=0.95,
            evidence=evidence + [f"核组合唯一匹配模板 {name}"],
        )

    # 2) 多候选:PULPROG 关键词精排
    if candidates:
        for keyword, name in _PULPROG_TYPES:
            if keyword in pulprog and name in candidates:
                return ExperimentType(
                    name=name,
                    confidence=0.9,
                    evidence=evidence + [f"PULPROG 含 {keyword!r}(候选核匹配)"],
                )
        generic = "generic_3d" if experiment.ndim == 3 else "generic_2d"
        return ExperimentType(
            name=generic,
            confidence=0.4,
            evidence=evidence + ["核组合有候选模板但 PULPROG 未识别,进入 Generic"],
        )

    # 3) 核组合无候选:PULPROG 兜底(液体类型但核不符,降置信度)
    for keyword, name in _PULPROG_TYPES:
        if keyword in pulprog:
            return ExperimentType(
                name=name,
                confidence=0.6,
                evidence=evidence + [f"核组合无匹配模板,PULPROG 含 {keyword!r} 推断"],
            )

    generic = "generic_3d" if experiment.ndim == 3 else "generic_2d"
    return ExperimentType(
        name=generic,
        confidence=0.3,
        evidence=evidence + ["核组合与 PULPROG 均未识别,进入 Generic 安全管线"],
    )
