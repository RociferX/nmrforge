"""Experiment type identification: nuclei combination first, then drop non-matching templates and
finally rank the remaining candidates by PULPROG.

Identification order (user scheme):
1. identify the correct nuclei first (the nucleus of every dimension);
2. drop non-matching templates by the positional nucleus fingerprint (the direct dimension by its x
   index exactly, the indirect ones by y/z nucleus count, 0.2.168) -- the same nucleus in the direct
   versus an indirect dimension is not the same fingerprint (e.g. 13C in the direct dimension of
   HETCOR versus 13C in an indirect dimension of HSQC-13C), and the indirect dimensions are matched
   by nucleus kind without forcing their internal order;
3. rank the remaining candidates by PULPROG keywords; when there is no candidate, fall back to the
   PULPROG alone (with lower confidence).
Solid-state types (NNH/CCH/NCA/NCACB/DARR and similar, absent from solution NMR) enter automatically
through the nucleus combination, and combinations sharing the same nuclei (e.g. NCACX/NCOCX/NCACB/
NCOCACB with 15N/13C/13C) are told apart by PULPROG.

Confidence convention: >0.9 process automatically; 0.6-0.9 automatic plus a warning; <0.6 fall back
to Generic and ask the user to confirm.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import core.experiments  # noqa: F401  importing registers the templates from presets/*.yaml
from core.data.internal_data_model import Experiment, ExperimentType
from core.experiments.registry import REGISTRY
from ui_support.i18n import tr

# (pulprog substring, template name); longer/more specific keywords come first so that hnca is not
# caught by nca, hncacb by ncacb, ncocx by nco or cbcanco by canco; when one keyword (e.g.
# hetcor/fslg) maps to several templates the "candidate nucleus match" filters them -- FSLGhetcor is
# separated by its nuclei into HETCOR (1H-13C) and HNHETCOR (1H-15N). The 1D types (CP13C/CP15N/
# PROTON1D/C13_1D) have no preset YAML yet (test_gui_presets only allows ndim=2/3), so their
# keywords await the GUI allowing ndim=1.
# 0.2.199-patch29hb (user): decide liquid versus solid first; these are the solid/liquid PULPROG
# feature substrings.
_SOLID_HINTS: tuple[str, ...] = (
    "shex", "cnh", "canh", "ccnh", "conh", "cch", "nnh", "nhhc", "chhc",
    "ncacx", "ncocx", "ncacb", "ncocacb", "ncoca", "ncaco", "darr", "pdsd",
    "rfdr", "tedor", "redor", "ccc", "inadequate", "nn", "nca", "nco",
    "cbcanco", "canco", "pain", "hetcor", "fslg", "cp_",
)
_LIQUID_HINTS: tuple[str, ...] = (
    "gp_", "gpph", "gradient", "fhsqc", "hsqc", "hmqc", "hmbc", "tocsy",
    "noesy", "roesy", "cosy", "sfg", "zg",
)


# 0.2.199-patch29hm (user): identify kinetics / variable-delay series (pseudo-2D) -- the source
# directory holds a vdlist, acqus.VDLIST is non-empty, or PULPROG holds a kinetics keyword; the
# classification result lets the import strategy intercept them.
_KINETICS_PULPROG_HINTS: tuple[str, ...] = (
    "kinetic", "relax", "t1ir", "t2ir", "vdlist", "pseudo2d",
)


def _is_kinetics(experiment) -> bool:
    """Identify a kinetics / variable-delay series (vdlist file / acqus.VDLIST / PULPROG
    keyword)."""
    src = getattr(experiment, "source_path", None) or ""
    try:
        if src and Path(src).is_dir() and (Path(src) / "vdlist").is_file():
            return True
    except OSError:
        pass
    acqus = experiment.acquisition_parameters.get("acqus", {})
    vd = str(acqus.get("VDLIST", "") or "").strip()
    # when Bruker does not use variable delays acqus.VDLIST is often a pure-D placeholder
    # (DDD.../0/none), which is not a variable-delay series
    if vd and (
        vd.upper().strip("D") == ""
        or vd.strip().lower() in {"0", "0.0", "none", "n/a", "null", "off"}
    ):
        vd = ""
    if vd:
        return True
    pulprog = str(acqus.get("PULPROG", "")).lower()
    return any(kw in pulprog for kw in _KINETICS_PULPROG_HINTS)


def is_kinetics(experiment) -> bool:
    """Public: quick test for a kinetics / variable-delay series (used by upstream import
    interception)."""
    return _is_kinetics(experiment)


def _pulprog_state_hint(pulprog: str) -> str | None:
    """Decide liquid/solid from the PULPROG substring; None when it cannot be decided."""
    for kw in _SOLID_HINTS:
        if kw in pulprog:
            return "solid"
    for kw in _LIQUID_HINTS:
        if kw in pulprog:
            return "liquid"
    return None


_PULPROG_TYPES: list[tuple[str, str]] = [
    # -- solution NMR: HNN (hncannh/hncocannh are solution gradient pulse programs; they must come
    # before "nnh", otherwise the substring nnh inside hncocannh matches the solid NNH first) --
    ("hncocannh", "HNN"),
    ("hncannh", "HNN"),
    # -- solid-state NMR: 15N/13C backbone and 1H-detected homonuclear --
    ("nnh", "NNH"),
    ("nhhc", "NHHC"),
    ("chhc", "CHHC"),
    # 0.2.167: cchtocsy must come before cch (a substring prefix would match first)
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
    # 0.2.199-patch29gz (user): solid 3D 1H detection -- CoNH/CCNH and similar are already
    # registered presets and therefore candidates, but the substring conh shadows the solution
    # cbcaconh/hcconh/ccconh, so there is no rough substring mapping; the nucleus-combination
    # family fallback picks them up instead (1H/15N/13C).
    # -- solid-state NMR: 13C-13C / homonuclear / distance restraints --
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
    # -- solution NMR --
    # 0.2.167: added template keywords -- specific/long keywords must come before the generic word
    # sharing their prefix (hsqctocsy before hsqc, noesyhsqc/tocsyhsqc before noesy/tocsy, hcchco
    # before hcch, hncaco/hncacb before hnca), otherwise a substring matches first
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
    # 0.2.199-patch29gx (user): the CANH solid experiment (PULPROG holding cnh/canh, e.g.
    # xh.3d.cnh_top3.shex) is identified as its own CANH (1H-detected 15N/13C 3D, Ca only, uniform);
    # it comes after cbcanh so that the canh substring inside cbcanh is not matched first.
    ("cnh", "CANH"),
    ("canh", "CANH"),
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


# 0.2.199-patch29fb (user): smart fallback for homonuclear-combination families that PULPROG does
# not subdivide.
# Only combinations whose family members behave identically in processing (the same peak_sign
# uniform/mixed and the same region priors) are listed -- the fallback cannot change the phase/sign
# rules; a family mixing uniform and mixed (e.g. NCACX (uni) / NCACB (mixed) / CBCANCO (mixed) in
# 13C/15N/13C 3D) must keep the PULPROG decision rather than guessing from the nuclei, and still
# goes Generic listing the candidates for confirmation.
_FAMILY_FALLBACK: dict[tuple[int, str, tuple[str, ...]], str] = {
    (2, "13C", ("15N",)): "NCA",      # NCA/NCO/TEDOR/PAIN-CP(uniform)
    (2, "13C", ("13C",)): "DARR",     # DARR/PDSD/RFDR/CORD/INADEQUATE/HCC(uniform)
    (2, "1H", ("1H",)): "CHHC",       # CHHC/NHHC(uniform)
    (2, "1H", ("13C",)): "HSQC-13C",  # HSQC/HMQC/TOCSY/NOESY 13C family (uniform)
    (2, "1H", ("15N",)): "HSQC",      # HSQC/HMQC/TOCSY/NOESY 15N family (uniform)
    (2, "1H", ("31P",)): "HMQC-31P",  # solution 31P family (uniform)
    (2, "1H", ("19F",)): "HSQC-19F",  # solution 19F family (uniform)
    (3, "1H", ("13C", "13C")): "CCH",  # CCH/CCH-TOCSY(uniform)
}


_GENERIC_BY_NDIM: dict[int, str] = {
    1: "generic_1d",
    2: "generic_2d",
    3: "generic_3d",
}


def _normalize_type_name(name: str) -> str:
    """Normalise a type name/title: lower-case and drop anything non-alphanumeric."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _title_template_name(title: str) -> str | None:
    """Parse the content of a Bruker pdata/title into a template type name; None when
    unrecognised."""
    text = str(title or "").strip()
    if not text:
        return None
    normalized = {}
    seen = set()
    for tpl in REGISTRY.values():
        if id(tpl) in seen:
            continue
        seen.add(id(tpl))
        normalized.setdefault(_normalize_type_name(tpl.name), tpl.name)
    whole = _normalize_type_name(text)
    if whole in normalized:
        return normalized[whole]
    for token in re.split(r"[\s,;:_\-/()]+", text):
        n = _normalize_type_name(token)
        if len(n) >= 3 and n in normalized:
            return normalized[n]
    return None


def _family_fallback(experiment: Experiment) -> str | None:
    """Representative template for a homonuclear family with unknown PULPROG (combination -> family
    representative); None when there is no safe representative."""
    indirect = tuple(sorted(d.nucleus for d in experiment.dimensions[1:]))
    return _FAMILY_FALLBACK.get(
        (experiment.ndim, experiment.dimensions[0].nucleus, indirect)
    )


def _data_nuclei(experiment: Experiment) -> tuple[str, ...]:
    """The nucleus sequence of the data, in dimension order x/y/z.

    0.2.168: the same nucleus in different positions counts as a different fingerprint -- 13C in the
    direct dimension of HETCOR versus 13C in an indirect dimension of HSQC-13C no longer collide
    merely because their unordered counts agree.
    """
    return tuple(d.nucleus for d in experiment.dimensions)


def _template_nuclei(template) -> tuple[str, Counter[str]] | None:
    """Template nucleus fingerprint: (direct nucleus, indirect nucleus counts); a generic template
    (empty direct) takes no part in nucleus matching. The indirect dimensions are counted by nucleus
    kind without ordering (the historical order in presets is inconsistent and the same nucleus can
    swap positions, e.g. the two 1H of 1H/13C/1H; the counts keep how often a nucleus occurs, so the
    two 15N of NNH differ from the single 15N of HSQC)."""
    if not template.direct_nucleus:
        return None
    return (template.direct_nucleus, Counter(template.indirect_nuclei))


def _nuclei_candidates(experiment: Experiment) -> list[str]:
    """Filter templates by the nucleus fingerprint: the direct nucleus must match and the indirect
    nucleus set must be identical.

    0.2.168: the direct dimension (x index) matches exactly -- the same nucleus in the direct versus
    an indirect dimension counts as a different fingerprint (13C in the direct dimension of HETCOR
    versus 13C in an indirect dimension of HSQC-13C) -- while the indirect dimensions (y/z indices)
    match as a nucleus set without forcing their internal order.
    REGISTRY registers under both the display name and the stem (since 0.2.111), so one template
    appears twice; duplicates are removed by template object before returning, which keeps the
    "unique candidate" semantics true.
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


def classify(experiment: Experiment, *, user_title: str = "") -> ExperimentType:
    """Experiment type identification (pdata/title user types supported since 0.2.199-patch29fd)."""
    base = _classify_base(experiment)
    tname = _title_template_name(user_title)
    if tname is None:
        return base
    candidates = set(_nuclei_candidates(experiment))
    if tname not in candidates:
        base.evidence.append(
            tr(
                "pdata/title says {p0!r}, but the nucleus combination/dimension does not match; "
                "ignoring the title and keeping the "
                "classification",
                p0=tname,
            )
        )
        return base
    if base.name == tname:
        if not any(
            tr("pdata/title agrees with the classification") in e for e in base.evidence
        ):
            base.evidence.append(
                tr("pdata/title agrees with the classification: {p0}", p0=tname)
            )
        return base
    return ExperimentType(
        name=tname,
        confidence=0.75,
        evidence=base.evidence
        + [
            tr(
                "classified as {p0} (confidence {p1:.2f}), but pdata/title says {p2}; the nucleus "
                "combination is compatible, so the user title wins (please "
                "check)",
                p0=base.name,
                p1=base.confidence,
                p2=tname,
            )
        ],
    )


def _classify_base(experiment: Experiment) -> ExperimentType:
    """Nuclei combination first: drop non-matching templates, rank candidates by PULPROG, then fall
    back."""
    acqus = experiment.acquisition_parameters.get("acqus", {})
    pulprog = str(acqus.get("PULPROG", "")).lower()
    evidence: list[str] = [tr("nucleus combination {p0}", p0='/'.join(_data_nuclei(experiment)))]

    state_hint = _pulprog_state_hint(pulprog)
    if state_hint is not None:
        evidence.append(
            tr(
                "suspected {p0} NMR (PULPROG signature)",
                p0=tr("solid-state") if state_hint == "solid" else tr("liquid-state"),
            )
        )

    # 0.2.199-patch29hm (user): identify kinetics / variable-delay series
    if _is_kinetics(experiment):
        return ExperimentType(
            name="Kinetics",
            confidence=0.9,
            evidence=evidence
            + [tr(
                "kinetics / variable-delay series detected (pseudo-2D, VDLIST or related "
                "PULPROG)",
            )],
        )

    candidates = _nuclei_candidates(experiment)

    # 1) a single candidate: the nucleus combination hits directly (e.g. 1H/15N/15N of NNH)
    if len(candidates) == 1:
        name = candidates[0]
        return ExperimentType(
            name=name,
            confidence=0.95,
            evidence=evidence
            + [tr(
                "the nucleus combination matches exactly one template: "
                "{p0}",
                p0=name,
            )],
        )

    # 2) several candidates: rank by PULPROG keywords
    if candidates:
        for keyword, name in _PULPROG_TYPES:
            if keyword in pulprog and name in candidates:
                return ExperimentType(
                    name=name,
                    confidence=0.9,
                    evidence=evidence
                    + [tr(
                        "PULPROG contains {p0!r} (candidate core "
                        "match)",
                        p0=keyword,
                    )],
                )
        # 0.2.199-patch29fb: prefer the safe family representative (which keeps the nucleus
        # combination information); when the sign semantics inside the family disagree (13C/15N/13C
        # 3D mixing uniform and mixed) or there is no safe representative, Generic is still
        # used, but
        # the evidence lists the candidates and the reason, so the GUI/log can explain it rather
        # than looking "purely generic".
        canonical = _family_fallback(experiment)
        if canonical:
            return ExperimentType(
                name=canonical,
                confidence=0.5,
                evidence=evidence
                + [
                    tr(
                        "PULPROG: the nucleus-combination family is not subdivided; using the safe "
                        "family representative {p0} (same sign rules; the exact type can be "
                        "changed in the "
                        "comments)",
                        p0=canonical,
                    )
                ],
            )
        generic = _GENERIC_BY_NDIM.get(experiment.ndim, "generic_2d")
        return ExperimentType(
            name=generic,
            confidence=0.4,
            evidence=evidence
            + [
                (
                    tr(
                    "PULPROG Unrecognized and semantically inconsistent symbols of the same family "
                    "(candidates: ",
                )
                )
                + ", ".join(sorted(set(candidates)))
                + tr("), need to manually confirm the type")
            ],
        )

    # 3) no candidate from the nuclei: fall back to PULPROG (a liquid type with mismatching nuclei,
    # lower confidence)
    for keyword, name in _PULPROG_TYPES:
        if keyword in pulprog:
            return ExperimentType(
                name=name,
                confidence=0.6,
                evidence=evidence + [(
                    tr(
                    "nucleus combination has no matching template, PULPROG contains {p0!r} "
                    "inference",
                    p0=keyword,
                )
                )],
            )

    generic = _GENERIC_BY_NDIM.get(experiment.ndim, "generic_2d")
    return ExperimentType(
        name=generic,
        confidence=0.3,
        evidence=evidence + [(
            tr(
            "Nucleus combination and PULPROG are not recognized and enter the Generic safety "
            "pipeline",
        )
        )],
    )
