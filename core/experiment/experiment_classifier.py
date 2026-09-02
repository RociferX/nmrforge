"""实验类型识别:核组合优先,排除不匹配模板,再在候选中用 PULPROG 精排。

识别顺序(用户方案):
1. 先识别正确核(各维度核素);
2. 按维度位置核指纹(直接维 x 下标精确 + 间接维 y/z 核计数,0.2.168)
   排除不匹配的模板——同核在直接维 vs 间接维不视为同一指纹(如
   HETCOR 13C@直接维 vs HSQC-13C 13C@间接维),间接维按核种类计数
   匹配、内部顺序不强制;
3. 在剩余候选中用 PULPROG 关键词精排;无候选时 PULPROG 兜底(降置信)。
固体核磁类型(如 NNH/CCH/NCA/NCACB/DARR,液体核磁没有)通过核组合规则
自动入选,同一核组合(如 15N/13C/13C 的 NCACX/NCOCX/NCACB/NCOCACB)由
PULPROG 区分。

置信度约定:>0.9 自动处理;0.6-0.9 自动 + warning;<0.6 走 Generic 并请求用户确认。
"""

from __future__ import annotations

from collections import Counter

import core.experiments  # noqa: F401  导入即从 presets/*.yaml 注册模板
from core.data.internal_data_model import Experiment, ExperimentType
from core.experiments.registry import REGISTRY

# (pulprog 子串, 模板名)；长/具体关键词在前,避免 hnca 被 nca、hncacb 被
# ncacb、ncocx 被 nco、cbcanco 被 canco 误匹配;同一关键词(如 hetcor/fslg)
# 对应多个模板时由「候选核匹配」过滤——FSLGhetcor 按核组合区分
# HETCOR(1H-13C)/HNHETCOR(1H-15N)。1D 类型(CP13C/CP15N/PROTON1D/
# C13_1D)暂无 presets YAML(test_gui_presets 仅允许 ndim=2/3),关键词待
# GUI 侧放开 ndim=1 后补。
_PULPROG_TYPES: list[tuple[str, str]] = [
    # —— 溶液核磁:HNN(hncannh/hncocannh 为溶液梯度脉冲程序;须排在
    # "nnh" 之前,否则 hncocannh 的子串 nnh 抢先匹配固体 NNH)——
    ("hncocannh", "HNN"),
    ("hncannh", "HNN"),
    # —— 固体核磁:15N/13C 主链与 1H 检测同核 ——
    ("nnh", "NNH"),
    ("nhhc", "NHHC"),
    ("chhc", "CHHC"),
    # 0.2.167:cchtocsy 必须排在 cch 之前(子串前缀抢先匹配问题)
    ("cchtocsy", "CCH-TOCSY"),
    ("cch", "CCH"),
    ("cbcanco", "CBCANCO"),
    ("cancoca", "CAN(CO)CA"),
    ("canco", "CANCO"),
    ("ncocacb", "NCOCACB"),
    ("ncocb", "NCOCACB"),
    ("ncocx", "NCOCX"),
    ("ncacb", "NCACB"),
    ("ncacx", "NCACX"),
    ("nco", "NCO"),
    ("nca", "NCA"),
    # —— 固体核磁:13C-13C / 同核 / 距离约束 ——
    ("cshi.hcc", "HCC"),
    ("ccc", "CCC"),
    ("inadequate", "INADEQUATE"),
    ("darr", "DARR"),
    ("pdsd", "PDSD"),
    ("rfdr", "RFDR"),
    ("cord", "CORD"),
    ("tedor", "TEDOR"),
    ("redor", "REDOR"),
    ("pain", "PAIN-CP"),
    ("fslg", "HNHETCOR"),
    ("fslg", "HETCOR"),
    ("hetcor", "HNHETCOR"),
    ("hetcor", "HETCOR"),
    ("nn", "NN"),
    # —— 液体核磁 ——
    # 0.2.167:补充模板关键词——具体/长关键词必须排在同前缀通用词之前
    # (hsqctocsy 在 hsqc 前、noesyhsqc/tocsyhsqc 在 noesy/tocsy 前、
    # hcchco 在 hcch 前、hncaco/hncacb 在 hnca 前),否则被子串抢先匹配
    ("hsqc19", "HSQC-19F"),
    ("hmqc31", "HMQC-31P"),
    ("hmbc31", "HMBC-31P"),
    ("hsqctocsy", "HSQC-TOCSY-15N"),
    ("hsqctocsy", "HSQC-TOCSY-13C"),
    ("tocsyhsqc", "TOCSY-HSQC-15N"),
    ("noesyhsqc", "NOESY-HSQC-15N"),
    ("noesyhsqc", "NOESY-HSQC-13C"),
    ("hcchco", "HCCH-COSY"),
    ("hcch", "HCCH-TOCSY"),
    ("hcaco", "HCACO"),
    ("hbhaconh", "HBHA(CO)NH"),
    ("hcaconh", "H(CA)NH"),
    ("hcconh", "H(CCO)NH"),
    ("ccconh", "C(CCO)NH"),
    ("hncaco", "HN(CA)CO"),
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


# 0.2.199-补29fb(用户):同核组合族在 PULPROG 未细分时的智能回退。
# 仅收录「族内成员处理性质一致(peak_sign 同 uniform/mixed 且区域先验一致)」
# 的组合——回退结果不会改变相位/符号规则;族内混合 uniform/mixed(如 3D
# 13C/15N/13C 的 NCACX(uni)/NCACB(mixed)/CBCANCO(mixed))必须保留 PULPROG
# 判定,不能靠核组合猜,仍走 Generic 并列出候选供确认。
_FAMILY_FALLBACK: dict[tuple[int, str, tuple[str, ...]], str] = {
    (2, "13C", ("15N",)): "NCA",      # NCA/NCO/TEDOR/PAIN-CP(uniform)
    (2, "13C", ("13C",)): "DARR",     # DARR/PDSD/RFDR/CORD/INADEQUATE/HCC(uniform)
    (2, "1H", ("1H",)): "CHHC",       # CHHC/NHHC(uniform)
    (2, "1H", ("13C",)): "HSQC-13C",  # HSQC/HMQC/TOCSY/NOESY 13C 族(uniform)
    (2, "1H", ("15N",)): "HSQC",      # HSQC/HMQC/TOCSY/NOESY 15N 族(uniform)
    (2, "1H", ("31P",)): "HMQC-31P",  # 溶液 31P 族(uniform)
    (2, "1H", ("19F",)): "HSQC-19F",  # 溶液 19F 族(uniform)
    (3, "1H", ("13C", "13C")): "CCH",  # CCH/CCH-TOCSY(uniform)
}


def _family_fallback(experiment: Experiment) -> str | None:
    """同核组合族 PULPROG 未知时的代表模板(核组合 → 族代表);无安全代表返回 None。"""
    indirect = tuple(sorted(d.nucleus for d in experiment.dimensions[1:]))
    return _FAMILY_FALLBACK.get(
        (experiment.ndim, experiment.dimensions[0].nucleus, indirect)
    )


def _data_nuclei(experiment: Experiment) -> tuple[str, ...]:
    """数据各维度核序列(按维度位置 x/y/z 序)。

    0.2.168:同核不同位置视为不同指纹——如 HETCOR 的 13C 在直接维、
    HSQC-13C 的 13C 在间接维,不再因无序计数相同而撞核。
    """
    return tuple(d.nucleus for d in experiment.dimensions)


def _template_nuclei(template) -> tuple[str, Counter[str]] | None:
    """模板核指纹:(直接维核, 间接维核计数);generic 模板(direct 为空)
    不参与核匹配。间接维按核种类计数、不计顺序(presets 历史顺序约定
    不统一,且同核位置可互换,如 1H/13C/1H 的两个 1H;计数保留同核
    出现次数,如 NNH 两个 15N 与 HSQC 一个 15N 可区分)。"""
    if not template.direct_nucleus:
        return None
    return (template.direct_nucleus, Counter(template.indirect_nuclei))


def _nuclei_candidates(experiment: Experiment) -> list[str]:
    """按核指纹过滤模板:直接维核相同且间接维核集合一致才入选。

    0.2.168:直接维(x 下标)精确匹配——同核在直接维 vs 间接维视为不同
    指纹(如 HETCOR 13C@直接维 vs HSQC-13C 13C@间接维);间接维(y/z
    下标)按核集合匹配,不强制内部顺序。
    REGISTRY 按显示名 + stem 双注册(0.2.111 起),同一模板会重复出现,
    按模板对象去重后再返回,保证「唯一候选」语义成立。
    """
    data = _data_nuclei(experiment)
    if not data:
        return []
    seen: set[int] = set()
    out: list[str] = []
    for name, template in REGISTRY.items():
        t = _template_nuclei(template)
        if t is None:
            continue
        direct, indirect = t
        if (
            data[0] == direct
            and Counter(data[1:]) == indirect
            and id(template) not in seen
        ):
            seen.add(id(template))
            out.append(name)
    return out


def classify(experiment: Experiment) -> ExperimentType:
    """核组合优先:排除不匹配模板 → 候选中 PULPROG 精排 → 兜底。"""
    acqus = experiment.acquisition_parameters.get("acqus", {})
    pulprog = str(acqus.get("PULPROG", "")).lower()
    evidence: list[str] = [f"核组合 {'/'.join(_data_nuclei(experiment))}"]

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
        # 0.2.199-补29fb:同族安全代表优先(保留核组合信息);族内符号语义
        # 不一致(3D 13C/15N/13C 混 uniform/mixed)或无可安全代表时仍 Generic,
        # 但证据列出候选与原因,便于界面/日志说明而非「纯粹 generic」。
        canonical = _family_fallback(experiment)
        if canonical:
            return ExperimentType(
                name=canonical,
                confidence=0.5,
                evidence=evidence
                + [
                    "PULPROG 未细分同核组合族,取安全族代表 "
                    + canonical
                    + "(符号规则一致;可在注释中改精确类型)"
                ],
            )
        generic = "generic_3d" if experiment.ndim == 3 else "generic_2d"
        return ExperimentType(
            name=generic,
            confidence=0.4,
            evidence=evidence
            + [
                "PULPROG 未识别且同族符号语义不一致(候选: "
                + ", ".join(sorted(set(candidates)))
                + "),需人工确认类型"
            ],
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
