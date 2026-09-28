"""Guards for the ``-neg`` coherence-pathway criterion and the explicit fid.com
MODE keywords (finalised 2026-09-24).

Conventions (user, 2026-09-24; task record of rounds 20-23):

1. fid.com's ``-yMODE/-zMODE`` writes **explicit keywords** (0→Complex, 1→Real,
   2→Sequential, 3→TPPI, 4→States, 5→States-TPPI, 6→Echo-AntiEcho; ``-N`` variants
   are preserved verbatim), no longer a uniform ``Complex``;
2. ``-alt`` is decided by ``FnMODE`` alone (5→``-alt``; 4→none; 2→``-bruk``;
   3→``-real``; 1→magnitude; 6→E-A and **no** ``-alt``);
3. ``-neg = (handedness == "conjugated")``; handedness is decided by the coherence
   pathway phase ``qphase = −Σ Δp·Δφ − Δφ_receiver`` (``+90°``⇒normal,
   ``−90°``⇒conjugated); ``F1EA``/unlisted sequence/missing pulse program/family
   conflict ⇒ ``unknown`` ⇒ **no -neg** + the same reminder in all three places.

The fixtures use fragments of **real deposited pulse programs** (the expanded ``mc``
tail of ``hncogp3d``), not invented patterns — see
``docs/backend/fid_com_parameter_sources.md`` §7.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.bruker_workflow import parse_fid_com, patch_fid_com
from backend.script_generator import (
    _FT_FLAGS,
    generate_convert_script,
    generate_process_script,
)
from core.data.bruker_reader import read_dataset
from core.experiment.acquisition_mode_detector import (
    bruk2pipe_mode_for,
    ft_alt_for,
    ft_neg_for,
)
from core.experiment.pulse_pathways import (
    HANDEDNESS_CONJUGATED,
    HANDEDNESS_UNKNOWN,
    PULSE_PATHWAY_ANNOTATIONS,
    acquisition_family,
    canonical_negated,
    handedness_for,
    mode_symbol_audit,
    pulse_sequence_name,
    quadrature_block,
    review_lines,
)
from core.planning.method_selector import select_method

#: the expanded ``mc`` tail of ``hncogp3d`` (a verbatim fragment of the real
#: deposited pulse program; only pulse bodies irrelevant to the criterion were
#: dropped).
HNCO_PULSEPROGRAM = """# 1 "/opt/topspin/exp/stan/nmr/lists/pp/hncogp3d"
# 1 "/opt/topspin/exp/stan/nmr/lists/pp/Avance.incl" 1
"ST1CNT = td1 / (2)"
"ST2CNT = td2 / (2)"
2 MCWRK  * 2 do:f3
LBLSTS2, MCWRK  * 3
LBLF2, MCWRK  * 4
LBLSTS1, MCWRK
LBLF1, MCREST
  (p13:sp2 ph4):f2
  d0
  (center (p14:sp5 ph1):f2 (p22 ph1):f3 )
  d0
  (p14:sp3 ph1):f2
  go=2 ph31 cpd3:f3
  MCWRK  do:f3 wr #0 if #0 zd igrad EA  MCWRK  ip6*2
  lo to LBLSTS2 times 2
  MCWRK id10  MCWRK  id29  MCWRK  dd30
  lo to LBLF2 times ST2CNT
  MCWRK rd10  MCWRK  rd29  MCWRK  rd30  MCWRK  ip4
  lo to LBLSTS1 times 2
  MCWRK id0
  lo to LBLF1 times ST1CNT
ph1=0
ph4=0 2
ph6=3 3 1 1
ph31=0 2 2 0 0 2 2 0 2 0 0 2 2 0 0 2
"""

#: the **unexpanded** pulse-program fragment of one of the data owner's 3D NUS
#: datasets (``cbcaconhgpwg3d``): the increment is written inside the mc clause's
#: ``calph(phN, ±90)`` (unlike the expanded form of deposited pulse programs).
CBCACONH_PULSEPROGRAM = """;cbcaconhgpwg3d
;avance-version (18/04/16)
;CBCACONH
;      F1(H) -> F2(Caliph.,t1 -> Ca) -> F2(C=O) -> F3(N,t2) -> F1(H,t3)
;phase sensitive (t1)
;phase sensitive (t2)
  (p1 ph1):f1
  (p13:sp2 ph3):f2
  d0
  (center (p14:sp5 ph1):f2 (p22 ph1):f3 )
  d20
  (p21 ph5):f3
  d30
  (p14:sp7 ph1):f2
  d30
  d10
  d29
  go=2 ph31 cpd3:f3
  d11 do:f3 mc #0 to 2
     F1PH(calph(ph3, +90), caldel(d0, +in0) & caldel(d20, -in20))
     F2PH(calph(ph5, +90), caldel(d10, +in10) & caldel(d29, +in29) & caldel(d30, -in30))
exit
ph1=0
ph3=0 2
ph5=0 0 2 2
ph31=0 2 2 0 0 2 2 0 2 0 0 2 2 0 0 2
"""

#: same family but with an E/A first indirect dimension (a ``hncacbgp3d.x``
#: fragment from another 3D NUS dataset).
HNCACB_EA_CLAUSE = """  go=2 ph31 cpd3:f3
  d11 do:f3 mc #0 to 2
     F1PH(calph(ph5, -90) & calph(ph4, -90), caldel(d0, +in0) & calph(ph5, +180))
     F2EA(calgrad(EA) & calph(ph6, +180), caldel(d10, +in10) & calph(ph31, +180))
exit
ph4=0 2
ph5=3
ph6=3
ph31=0 2 2 0
"""

#: same family but a 2D E/A version **without** a 3D second indirect dimension
#: (a HSQC echo/antiecho fragment).
HSQC_EA_PULSEPROGRAM = """# 1 "/opt/topspin/exp/stan/nmr/lists/pp/""" + "hsqc_ea_fixture" + """"
1 ze
2 MCWRK  * 2 do:f3
LBLSTS1, MCWRK  * 4
LBLF1, MCREST
  (center (p1 ph2) (p21 ph5):f3 )
  go=2 ph31 cpd3:f3
  MCWRK  do:f3 wr #0 if #0 zd igrad EA  MCWRK  ip5*2
  lo to LBLSTS1 times 2
  MCWRK id0  MCWRK  ip3*2  MCWRK  ip6*2  MCWRK  ip31*2
  lo to LBLF1 times ST1CNT
ph5=1 1 3 3
ph6=0
ph31=0 2 2 0
"""


def _experiment(bruker_dir: Path, name: str = "hnca_3d"):
    return read_dataset(bruker_dir / name)


def _copy_dataset(experiment, tmp_path: Path):
    """Copy the dataset into this case's ``tmp_path`` (the shared fixture directory is
    **not written**, avoiding cross-test contamination)."""
    source = Path(experiment.source_path)
    target = Path(tmp_path) / "dataset"
    if not target.exists():
        shutil.copytree(source, target)
    experiment.source_path = target
    return experiment


def _with_pulseprogram(experiment, tmp_path: Path, text: str):
    """Attach the pulse program to the experiment's source directory (copy into
    tmp_path first, leaving the shared fixture untouched)."""
    _copy_dataset(experiment, tmp_path)
    (Path(experiment.source_path) / "pulseprogram").write_text(text, encoding="utf-8")
    return experiment


def _set_fnmode(experiment, logical_axis: str, value):
    ndim = int(experiment.ndim)
    if ndim >= 3:
        key = {"F1": "acqu3s", "F2": "acqu2s"}[logical_axis]
    else:
        key = {"F1": "acqu2s", "F2": "acqus"}[logical_axis]
    experiment.acquisition_parameters[key]["FnMODE"] = value
    return experiment


# --------------------------------------------------------------------------- #
# 1. Pulse-program parsing (real fragments)
# --------------------------------------------------------------------------- #
def test_sequence_name_comes_from_the_expanded_pulse_program() -> None:
    assert pulse_sequence_name(HNCO_PULSEPROGRAM) == "hncogp3d"
    assert pulse_sequence_name(HSQC_EA_PULSEPROGRAM) == "hsqc_ea_fixture"
    assert pulse_sequence_name(";hncogp3d\n1 ze\n") == "hncogp3d"
    assert pulse_sequence_name("") is None


def test_mc_clause_form_is_parsed_alongside_the_expanded_form() -> None:
    """An unexpanded pulse program (only the ``mc`` clause) must be decidable too:
    the increment is written inside ``calph(phN, ±90)``.

    Both forms occur in the real corpus: deposited pulse programs are **expanded**
    (``lo to`` blocks + ``ipN``), while the laboratory's own 3D datasets
    (``cbcaconhgpwg3d``/``hncacbgp3d.x``) are **unexpanded**. The clause name shares
    the numbering of ``acqu3s`` (F1)/``acqu2s`` (F2) (manual §11.2), and only the
    **first argument** enters the quadrature pair loop.
    """
    from core.experiment.pulse_pathways import mc_clause_for, mc_clauses

    clauses = mc_clauses(CBCACONH_PULSEPROGRAM)
    assert set(clauses) == {"F1", "F2"}
    f1 = mc_clause_for(CBCACONH_PULSEPROGRAM, "F1")
    f2 = mc_clause_for(CBCACONH_PULSEPROGRAM, "F2")
    assert f1 is not None and f1.clause == "F1PH" and f1.family == "F1PH"
    assert f2 is not None and f2.clause == "F2PH" and f2.family == "F1PH"
    assert [(i.phase, i.delta_deg) for i in f1.increments] == [("ph3", 90.0)]
    assert [(i.phase, i.delta_deg) for i in f2.increments] == [("ph5", 90.0)]
    # a calph +180 in the second argument (the outer evolution loop) does **not**
    # count as a quadrature increment
    assert "caldel(d0, +in0)" in f1.second_argument
    # the E/A clause (F2EA) steps the gradient + ph6 only in the first argument
    # ⇒ family = F1EA
    ea = mc_clauses(HNCACB_EA_CLAUSE)
    assert ea["F2"].family == "F1EA"
    assert [(i.phase, i.delta_deg) for i in ea["F1"].increments] == [
        ("ph5", -90.0),
        ("ph4", -90.0),
    ]


def test_cbcaconh_facts_match_the_data_owners_script_but_no_neg_is_guessed(
    bruker_dir: Path, tmp_path: Path
) -> None:
    """``cbcaconhgpwg3d``: the **content facts** of both indirect dimensions can be
    read; the sign is still withheld (pathway unsolved).

    The data owner's own two ``smile.com`` files (the E/A one has ``FT`` on the first
    indirect dimension; the States-TPPI one has ``FT -alt -neg`` in the same place,
    otherwise identical word for word) serve **only as verification material**, not as
    a basis for the verdict — the verdict must be theoretical (Layer A→B→C); the
    ground truth only answers "is the computation right".
    """
    exp = _with_pulseprogram(_experiment(bruker_dir), tmp_path, CBCACONH_PULSEPROGRAM)
    _set_fnmode(exp, "F1", 5)
    _set_fnmode(exp, "F2", 5)
    f1 = handedness_for(exp, "F1", fnmode=5)
    f2 = handedness_for(exp, "F2", fnmode=5)
    # content facts: each dimension has one stepped phase (calph +90), both on the
    # 90° pulse right next to the evolution delay
    assert (f1.stepped_phases, f1.quadrature_phase_deg, f1.pulse_role) == (("ph3",), 90.0, "create")
    assert (f2.stepped_phases, f2.quadrature_phase_deg, f2.pulse_role) == (("ph5",), 90.0, "create")
    # sign: pathway unsolved ⇒ unknown ⇒ **no** -neg (both dimensions get a reminder)
    assert (f1.handedness, f2.handedness) == (HANDEDNESS_UNKNOWN, HANDEDNESS_UNKNOWN)
    assert ft_neg_for(exp, 5, "F1") is False
    assert ft_neg_for(exp, 5, "F2") is False
    script = generate_process_script(
        exp, select_method(exp), in_file="t.fid", out_file="o.ft3"
    )
    assert "-neg" not in script
    assert "| nmrPipe -fn FT -alt \\" in script  # -alt is still decided by FnMODE (States-TPPI)
    # same sequence but the acquisition mode is changed to E/A (contradicting the F2PH
    # clause in the pulse program) ⇒ family conflict ⇒ no -neg
    _set_fnmode(exp, "F2", 6)
    conflict = handedness_for(exp, "F2", fnmode=6)
    assert conflict.reason == "family_conflict"
    assert ft_neg_for(exp, 6, "F2") is False
    # switch to a genuinely E/A-acquired pulse-program fragment (F2EA clause)
    # ⇒ F1EA ⇒ unknown ⇒ no -neg
    (exp.source_path / "pulseprogram").write_text(
        HNCACB_EA_CLAUSE, encoding="utf-8"
    )
    assert handedness_for(exp, "F2", fnmode=6).reason == "f1ea"
    assert ft_neg_for(exp, 6, "F2") is False


def test_quadrature_block_belongs_to_the_right_axis() -> None:
    """3D: the two ``times 2`` blocks, inner to outer, are logical F2 (acqu2s) and
    F1 (acqu3s).

    Basis = the ``mc`` manual §11.2 (the first argument goes into the inner layer) +
    a per-file cross-check of the ``FnMODE`` of 14 deposited pulse programs (the F2
    block carries ``igrad EA`` and that dimension has FnMODE=6; the F1 block is
    ``ip4`` and that dimension has FnMODE=5).
    """
    f2_block = quadrature_block(HNCO_PULSEPROGRAM, "F2", 3)
    f1_block = quadrature_block(HNCO_PULSEPROGRAM, "F1", 3)
    assert f2_block is not None and f2_block.label == "LBLSTS2"
    assert f2_block.has_gradient  # E/A block: stepped gradient table
    assert [inc.phase for inc in f2_block.increments] == ["ph6"]
    assert f1_block is not None and f1_block.label == "LBLSTS1"
    assert [inc.phase for inc in f1_block.increments] == ["ph4"]
    assert acquisition_family(HNCO_PULSEPROGRAM, "F2", 3) == "F1EA"
    assert acquisition_family(HNCO_PULSEPROGRAM, "F1", 3) == "F1PH"
    # 2D has only one quadrature block, belonging to logical F1
    assert quadrature_block(HSQC_EA_PULSEPROGRAM, "F1", 2) is not None
    assert quadrature_block(HSQC_EA_PULSEPROGRAM, "F2", 2) is None


# --------------------------------------------------------------------------- #
# 2. The authoritative table, line by line
# --------------------------------------------------------------------------- #
def test_phase_increment_uses_the_manual_divisor_rule() -> None:
    """Δφ is computed per the manual §3.3.2/§3.3.7: with no ``(d)`` written, the
    **divisor defaults to 4** ⇒ ``ip`` = +90°.

    - ``ph4 = 0 2`` (default 4) ⇒ ``ip4`` = +90°, ``ip4*2`` = +180°, ``dp4`` = −90°;
    - ``ph4 = (5) 0 1 2 3`` ⇒ ``ip4`` = 360/5 = +72° (**not** 90°);
    - a float phase table ``ph4 = (float, 90.0) …`` ⇒ the increment is the second
      argument of ``ip``, not guessed ⇒ None.
    """
    from core.experiment.pulse_pathways import (
        DEFAULT_PHASE_DIVISOR,
        PhaseIncrement,
        phase_program_divisor,
        quadrature_block,
    )

    assert DEFAULT_PHASE_DIVISOR == 4
    text = HNCO_PULSEPROGRAM.replace("ph4=0 2", "ph4=(5) 0 1 2 3")
    assert phase_program_divisor(text, "ph4") == 5
    assert phase_program_divisor(HNCO_PULSEPROGRAM, "ph4") == 4
    block = quadrature_block(text, "F1", 3)
    assert block is not None
    increment = block.increments[0]
    assert PhaseIncrement(
        phase=increment.phase,
        operator=increment.operator,
        steps=increment.steps,
        divisor=5,
    ).delta_deg == pytest.approx(72.0)
    assert increment.delta_deg == pytest.approx(90.0)  # default divisor
    assert PhaseIncrement("ph4", "dp", 2, 4).delta_deg == pytest.approx(-180.0)
    assert PhaseIncrement("ph4", "ip", 1, 8).delta_deg == pytest.approx(45.0)
    # float list: undecidable ⇒ None (the caller treats it as unsupported_phase_program)
    floaty = HNCO_PULSEPROGRAM.replace("ph4=0 2", "ph4=(float, 90.0) 30 60 95.5")
    assert phase_program_divisor(floaty, "ph4") is None


def test_divisor_changes_the_read_dphi(bruker_dir: Path, tmp_path: Path) -> None:
    """Δφ must use the phase-table divisor: the default gives 90°, ``(5)`` gives 72°
    (the fact follows the file)."""
    exp = _with_pulseprogram(_experiment(bruker_dir), tmp_path, HNCO_PULSEPROGRAM)
    assert handedness_for(exp, "F1", fnmode=5).quadrature_phase_deg == pytest.approx(90.0)
    (exp.source_path / "pulseprogram").write_text(
        HNCO_PULSEPROGRAM.replace("ph4=0 2", "ph4=(5) 0 1 2 3"), encoding="utf-8"
    )
    result = handedness_for(exp, "F1", fnmode=5)
    assert result.quadrature_phase_deg == pytest.approx(72.0)
    # the sign is still unsolved ⇒ both files only get a reminder (a different Δφ
    # value does not "guess" a sign)
    assert result.reason == "pathway_not_solved"
    assert review_lines(exp)


def test_ft_neg_decision_uses_the_simple_rule_and_asks_the_user(
    bruker_dir: Path, tmp_path: Path
) -> None:
    """Layer C: **apply the simple criterion wherever it is reliable, hand the rest to
    the user** (the product convention set by the user on 2026-09-25).

    Empirical criterion (not a physical derivation): 3D first indirect dimension
    (NMRPipe ``y``, decided by AQSEQ) + States family ⇒ add; 3D second indirect
    dimension ⇒ do not add; E/A (QF) family **consistent** with ``FnMODE`` ⇒ do not
    add; **family contradicting FnMODE**, 2D States, undefined FnMODE, or an unclear
    y-z split ⇒ ``ask_user`` (default no -neg + reminder text; the user's 2026-09-25
    second revision: a contradiction goes to the user — neither guessed nor silently
    decided as "do not add"). The canonical ``-N`` is still a fact of Layer C itself.
    Layer B reports AQSEQ/axis order as it is.
    """
    from core.experiment.acquisition_encoding import ft_neg_decision, storage_encoding

    exp = _with_pulseprogram(_experiment(bruker_dir), tmp_path, HNCO_PULSEPROGRAM)
    _set_fnmode(exp, "F1", 5)
    exp.acquisition_parameters["acqus"]["AQSEQ"] = 0  # ⇒ 321
    storage, notes = storage_encoding(exp, "F1")
    assert "aqseq=321" in storage and "y=F2" in storage and "F1:separated" in storage
    assert not notes  # 321 agrees with this project's assumption ⇒ no reminder
    layers = ft_neg_decision(exp, "F1")
    assert layers.ft_neg is False
    assert layers.decision == "none"
    assert layers.basis == "simple_rule"
    assert layers.reason == "second_indirect_dimension_in_the_states_family"
    assert layers.as_dict()["stepped_phases"] == ["ph4"]
    assert layers.as_dict()["storage"] == storage
    # **family conflict**: hncogp3d's F2 pulse-program block is ``F1EA`` while
    # ``FnMODE=5`` (States-TPPI) here ⇒ contradiction ⇒ hand it to the user (neither
    # guessed nor silently decided as "do not add"; same convention as
    # handedness_for's family_conflict)
    conflict = ft_neg_decision(exp, "F2", fnmode=5)
    assert (conflict.decision, conflict.ft_neg) == ("ask_user", False)
    assert conflict.reason == "family_conflict"
    assert conflict.ask  # reminder text is present
    # when the E/A family is **consistent** with FnMODE (ground truth: the data
    # owner's E/A dimension is ``FT``) ⇒ a definite "do not add"
    assert ft_neg_decision(exp, "F2", fnmode=6).decision == "none"
    # 3D first indirect dimension + States family ⇒ **add -neg**: use the dimension
    # without a family conflict (AQSEQ=312 ⇒ y is logical F1, and hncogp3d's F1
    # pulse-program block is F1PH, which does not contradict FnMODE=5)
    exp.acquisition_parameters["acqus"]["AQSEQ"] = 1  # ⇒ 312 ⇒ y=F1
    first = ft_neg_decision(exp, "F1", fnmode=5)
    assert first.decision == "add" and first.ft_neg is True
    assert first.reason == "first_indirect_dimension_in_the_states_family"
    exp.acquisition_parameters["acqus"]["AQSEQ"] = 0  # restore 321 (y=F2) for the assertions below
    # 2D States: not covered by the simple criterion ⇒ **hand it to the user**
    # (default no -neg + reminder text)
    exp2d = _set_fnmode(_experiment(bruker_dir, "hsqc_2d"), "F1", 5)
    ask = ft_neg_decision(exp2d, "F1", fnmode=5)
    assert (ask.decision, ask.ft_neg) == ("ask_user", False)
    # reminder text is rendered in the UI language ⇒ assert only the
    # language-independent parts (the reason code and criterion name are substituted)
    from core.experiment.acquisition_encoding import SIMPLE_RULE_NAME

    assert ask.ask and ask.reason in ask.ask and SIMPLE_RULE_NAME in ask.ask
    # the canonical ``-N`` keyword is a fact of Layer C itself (independent of the
    # pulse program)
    canonical = ft_neg_decision(exp, "F1", mode_keyword="States-TPPI-N")
    assert canonical.ft_neg is True and canonical.basis == "canonical_N"
    # with AQSEQ=312 the y axis should be logical F1 ⇒ Layer B must flag the fact
    # that the conversion layer assumes F2
    exp.acquisition_parameters["acqus"]["AQSEQ"] = 1
    _storage312, notes312 = storage_encoding(exp, "F1")
    assert notes312
    # the direct dimension never adds it
    assert ft_neg_for(exp, 5, "F3") is False


def test_family_conflict_is_handed_to_the_user_with_a_reminder(
    bruker_dir: Path, tmp_path: Path
) -> None:
    """A family conflict ⇒ ``ask_user``: no ``-neg`` **and** a reminder (never a silent
    "do not add").

    User, 2026-09-25 (addition to the second revision, "hand it to the user"): when the
    pulse program says ``F1EA`` but ``FnMODE`` says States-TPPI, neither guess "add" nor
    stay silent with "do not add" — leave it to the indirect-dimension flip control of
    the spectrum step and emit the same reminder in the log/report/import; only when
    ``FnMODE`` agrees with the family is "do not add" definite (no reminder).
    """
    from core.experiment.acquisition_encoding import ft_neg_decision

    exp = _with_pulseprogram(_experiment(bruker_dir), tmp_path, HNCO_PULSEPROGRAM)
    _set_fnmode(exp, "F2", 5)  # contradicts hncogp3d's F2 pulse-program block (F1EA)
    exp.acquisition_parameters["acqus"]["AQSEQ"] = 0
    decision = ft_neg_decision(exp, "F2", fnmode=5)
    assert (decision.decision, decision.ft_neg) == ("ask_user", False)
    assert decision.reason == "family_conflict"
    assert ft_neg_for(exp, 5, "F2") is False
    assert any(line.startswith("yMODE") for line in review_lines(exp))
    # consistent with the family (FnMODE=6 = E/A) ⇒ a definite "do not add"; neither
    # dimension reminds any more
    _set_fnmode(exp, "F2", 6)
    assert ft_neg_decision(exp, "F2", fnmode=6).decision == "none"
    assert review_lines(exp) == []


def test_mode_keywords_are_specific() -> None:
    """MODE writes explicit keywords (no longer a uniform Complex); ``-N`` variants
    are preserved verbatim by the caller."""
    assert bruk2pipe_mode_for(0) == "Complex"
    assert bruk2pipe_mode_for(1) == "Real"
    assert bruk2pipe_mode_for(2) == "Sequential"
    assert bruk2pipe_mode_for(3) == "TPPI"
    assert bruk2pipe_mode_for(4) == "States"
    assert bruk2pipe_mode_for(5) == "States-TPPI"
    assert bruk2pipe_mode_for(6) == "Echo-AntiEcho"
    # the E-A shuffle is only performed on bruk2pipe's y dimension
    assert bruk2pipe_mode_for(6, axis="z") == "Complex"


def test_alt_is_decided_by_fnmode_only() -> None:
    """``-alt`` is decided by FnMODE alone; E-A (6) gets **no** ``-alt``; 2 is handled
    by ``-bruk``."""
    assert ft_alt_for(5) is True
    assert ft_alt_for(4) is False
    assert ft_alt_for(6) is False  # seeing alternating gradients does not mechanically add -alt
    assert ft_alt_for(3) is False  # TPPI → FT -real
    assert ft_alt_for(1) is False  # QF → magnitude
    assert _FT_FLAGS[4] == (False, False)
    assert _FT_FLAGS[5] == (False, True)
    assert _FT_FLAGS[6] == (False, False)


def test_qseq_uses_bruk_and_tppi_uses_real(bruker_dir: Path) -> None:
    """QSEQ(2)→``FT -bruk``; TPPI(3)→``FT -real``; QF(1)→``FT`` + ``MC``."""
    exp = _set_fnmode(_experiment(bruker_dir, "hsqc_2d"), "F1", 2)
    script = generate_process_script(
        exp, select_method(exp), in_file="t.fid", out_file="o.ft2"
    )
    assert "| nmrPipe -fn FT -bruk \\" in script
    assert "| nmrPipe -fn FT -alt" not in script

    exp = _set_fnmode(_experiment(bruker_dir, "hsqc_2d"), "F1", 3)
    script = generate_process_script(
        exp, select_method(exp), in_file="t.fid", out_file="o.ft2"
    )
    assert "| nmrPipe -fn FT -real \\" in script
    assert "| nmrPipe -fn FT -alt" not in script

    exp = _set_fnmode(_experiment(bruker_dir, "hsqc_2d"), "F1", 1)
    script = generate_process_script(
        exp, select_method(exp), in_file="t.fid", out_file="o.ft2"
    )
    assert "| nmrPipe -fn MC \\" in script


def test_echo_antiecho_gets_no_alt(bruker_dir: Path, tmp_path: Path) -> None:
    """E/A data (FnMODE=6) gets no ``-alt`` — alternating gradients alone must not
    add it mechanically."""
    exp = _with_pulseprogram(
        _experiment(bruker_dir, "hsqc_2d"), tmp_path, HSQC_EA_PULSEPROGRAM
    )
    _set_fnmode(exp, "F1", 6)
    script = generate_process_script(
        exp, select_method(exp), in_file="t.fid", out_file="o.ft2"
    )
    assert "| nmrPipe -fn FT -alt" not in script
    assert "| nmrPipe -fn FT -neg" not in script
    assert generate_convert_script(exp, direct_points=2048).find(
        "-yMODE Echo-AntiEcho"
    ) > 0


# --------------------------------------------------------------------------- #
# 3. handedness: listed sequences / F1EA / no pulse program / conflict / unlisted
# --------------------------------------------------------------------------- #
def test_hnco_facts_are_read_from_content_and_pathway_is_unsolved(
    bruker_dir: Path, tmp_path: Path
) -> None:
    """``hncogp3d`` F1: the content facts can be read, but the **sign is withheld**
    (pathway unsolved) ⇒ no ``-neg``.

    Architecture set by the user on 2026-09-25: Layer A (pulse physics) only supplies
    the "designed" quadrature facts and Layer B (Bruker storage) is not modelled yet ⇒
    Layer C gives no ``-neg``, only a reminder. The verdict is **independent of the
    sequence name**.
    """
    exp = _with_pulseprogram(_experiment(bruker_dir), tmp_path, HNCO_PULSEPROGRAM)
    _set_fnmode(exp, "F2", 6)

    f1 = handedness_for(exp, "F1", fnmode=5)
    assert f1.family == "F1PH"
    assert f1.stepped_phases == ("ph4",)
    assert f1.quadrature_phase_deg == pytest.approx(90.0)
    assert f1.pulse_role == "create"
    assert f1.handedness == HANDEDNESS_UNKNOWN
    assert f1.reason == "pathway_not_solved"
    assert ft_neg_for(exp, 5, "F1") is False

    f2 = handedness_for(exp, "F2", fnmode=6)
    assert f2.handedness == HANDEDNESS_UNKNOWN
    assert f2.reason == "f1ea"
    assert ft_neg_for(exp, 6, "F2") is False

    # 2026-09-25 second revision (automatic criterion = the simplest rule with the
    # lowest error probability): F2 is in the E/A family, so the criterion is
    # **definite** — no -neg and no more user prompts; only F1, whose missing AQSEQ
    # leaves NMRPipe's y/z ambiguous, keeps a reminder (pointing at the spectrum
    # step's flip control)
    lines = review_lines(exp)
    assert len(lines) == 1
    assert lines[0].startswith("zMODE")


def test_verdict_does_not_depend_on_the_sequence_name(
    bruker_dir: Path, tmp_path: Path
) -> None:
    """**Renaming does not affect the verdict**: replace the sequence name in the pulse
    program with any name and the facts read and the conclusion must stay the same.

    This directly guards against "never look up by sequence name" — the verdict uses
    only the file contents (the stepped phases, Δφ, pulse shape, acquisition family)
    and ``FnMODE``.
    """
    renamed = CBCACONH_PULSEPROGRAM.replace(";cbcaconhgpwg3d", ";zzzunknownseq")
    exp = _with_pulseprogram(_experiment(bruker_dir), tmp_path, renamed)
    _set_fnmode(exp, "F1", 5)
    _set_fnmode(exp, "F2", 5)
    f2 = handedness_for(exp, "F2", fnmode=5)
    f1 = handedness_for(exp, "F1", fnmode=5)
    assert f2.sequence == "zzzunknownseq"
    assert (f2.family, f2.stepped_phases, f2.quadrature_phase_deg) == ("F1PH", ("ph5",), 90.0)
    assert (f1.family, f1.stepped_phases, f1.quadrature_phase_deg) == ("F1PH", ("ph3",), 90.0)
    # the name changed but the conclusion is the same (pathway unsolved ⇒ both
    # dimensions unknown + a reminder)
    assert {f1.handedness, f2.handedness} == {HANDEDNESS_UNKNOWN}
    assert {f1.reason, f2.reason} == {"pathway_not_solved"}
    assert len(review_lines(exp)) == 2


def test_explicit_override_reaches_ft_neg_once_layer_a_is_determined(
    bruker_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicit override (Layer A solved) ⇒ Layer C yields ``-neg`` and **applies it
    automatically** to the script; Layer B only determines the axis order and does not
    veto.

    2026-09-25 second revision (user: "since the user checks everything anyway, use the
    rule with the lowest error probability"): the automatic criterion applies `-neg` by
    default (where Layer A is settled or the simple rule can decide), and the spectrum
    step's flip control can both add and remove it (``sampling.flip_f1=False`` = force
    off ⇒ removes the automatically added one). Layer B (AQSEQ/axis order/partner
    stride) is recorded as it is; per the user's 2026-09-25 convention it does **not**
    decide whether to negate.
    """
    exp = _with_pulseprogram(_experiment(bruker_dir), tmp_path, HNCO_PULSEPROGRAM)
    monkeypatch.setitem(
        PULSE_PATHWAY_ANNOTATIONS,
        "hncogp3d",
        {
            "F1": {
                "steps": ({"phase": "ph4", "dp": 1},),
                "receiver": "ph31",
                "source": "test fixture",
            }
        },
    )
    f1 = handedness_for(exp, "F1", fnmode=5)
    assert f1.handedness == HANDEDNESS_CONJUGATED
    assert f1.needs_neg
    from core.experiment.acquisition_encoding import ft_neg_decision

    decision = ft_neg_decision(exp, "F1")
    assert decision.ft_neg is True
    assert "aqseq=" in decision.storage
    # the automatic criterion applies by default: F1's FT line carries -neg directly
    # (the hnca_3d fixture's F1 is FnMODE=4 ⇒ only -neg, no -alt)
    plain = generate_process_script(
        exp, select_method(exp), in_file="t.fid", out_file="o.ft3"
    )
    assert "| nmrPipe -fn FT -neg \\" in plain
    # the user has the final say: flip_f1=False (force off) removes the automatically
    # added -neg
    forced_off = generate_process_script(
        exp, select_method(exp), in_file="t.fid", out_file="o.ft3",
        sampling={"flip_f1": False},
    )
    assert "-neg" not in forced_off
    # an explicit True agrees with the automatic conclusion (idempotent)
    flipped = generate_process_script(
        exp, select_method(exp), in_file="t.fid", out_file="o.ft3",
        sampling={"flip_f1": True},
    )
    assert "| nmrPipe -fn FT -neg \\" in flipped


def test_f1ea_never_gets_neg(bruker_dir: Path, tmp_path: Path) -> None:
    """``F1EA(...)`` is always unknown: the raw E/A pair is shuffled first, so the
    handedness depends on the encoding."""
    exp = _with_pulseprogram(
        _experiment(bruker_dir, "hsqc_2d"), tmp_path, HSQC_EA_PULSEPROGRAM
    )
    result = handedness_for(exp, "F1", fnmode=6)
    assert result.handedness == HANDEDNESS_UNKNOWN
    assert result.reason == "f1ea"
    assert ft_neg_for(exp, 6, "F1") is False


def test_missing_pulseprogram_is_unknown_with_reminder(
    bruker_dir: Path, tmp_path: Path
) -> None:
    """No pulse program ⇒ unknown (not a "guess") + a reminder to review manually."""
    from ui_support.i18n import tr

    exp = _copy_dataset(_experiment(bruker_dir), tmp_path)
    assert not (Path(exp.source_path) / "pulseprogram").exists()
    result = handedness_for(exp, "F2", fnmode=5)
    assert result.handedness == HANDEDNESS_UNKNOWN
    assert result.reason == "no_pulseprogram"
    # reminder text is compared against the UI language: the tr() version of the same
    # sentence (the language is pinned by conftest)
    expected = tr("there is no pulse program for this dataset")
    lines = review_lines(exp)
    assert lines and all(expected in line for line in lines)


def test_sequence_outside_the_table_is_unknown(bruker_dir: Path, tmp_path: Path) -> None:
    """**Decidable without a name**: unregistered/renamed sequences still yield facts
    from their content (the name is not consulted); undecidable ones get a reminder."""
    text = HNCO_PULSEPROGRAM.replace("/pp/hncogp3d", "/pp/zzzunknownpp").replace(
        ";hncogp3d", ";zzzunknownpp"
    )
    exp = _with_pulseprogram(_experiment(bruker_dir), tmp_path, text)
    result = handedness_for(exp, "F1", fnmode=5)
    assert result.sequence == "zzzunknownpp"
    assert (result.family, result.stepped_phases) == ("F1PH", ("ph4",))
    assert result.reason == "pathway_not_solved"  # an unknown name still yields readable content
    assert [line for line in review_lines(exp) if "zMODE" in line]
    # several phases stepped at once ⇒ even the facts are not unique ⇒ unknown +
    # a reminder (a different reason word)
    multi = HNCO_PULSEPROGRAM.replace(
        "MCWRK rd10  MCWRK  rd29  MCWRK  rd30  MCWRK  ip4",
        "MCWRK ip4  MCWRK  ip6",
    )
    (exp.source_path / "pulseprogram").write_text(multi, encoding="utf-8")
    unclear = handedness_for(exp, "F1", fnmode=5)
    assert unclear.handedness == HANDEDNESS_UNKNOWN
    assert unclear.reason == "multiple_stepped_phases"
    assert unclear.stepped_phases == ("ph4", "ph6")
    assert review_lines(exp)


def test_2d_states_is_left_undecided_with_a_notice(bruker_dir: Path, tmp_path: Path) -> None:
    """A 2D States/States-TPPI indirect dimension: pathway unsolved ⇒ ``unknown`` + a
    reminder (no ``-neg``)."""
    exp = _with_pulseprogram(
        _experiment(bruker_dir, "hsqc_2d"), tmp_path, CBCACONH_PULSEPROGRAM
    )
    _set_fnmode(exp, "F1", 5)
    result = handedness_for(exp, "F1", fnmode=5)
    assert result.handedness == HANDEDNESS_UNKNOWN
    assert result.reason == "pathway_not_solved"
    assert ft_neg_for(exp, 5, "F1") is False
    assert review_lines(exp)


def test_pulseprogram_fnmode_conflict_is_unknown(bruker_dir: Path, tmp_path: Path) -> None:
    """The pulse program's loop shape and the ``FnMODE`` family tell different stories
    ⇒ take no side, add no flag + a reminder.

    Example: ``hncogp3d``'s F2 block alternates gradients (E/A) while that dimension's
    ``FnMODE`` says States-TPPI (5).
    """
    exp = _with_pulseprogram(_experiment(bruker_dir), tmp_path, HNCO_PULSEPROGRAM)
    _set_fnmode(exp, "F2", 5)  # the pulse program is F1EA, acqu2s says F1PH
    result = handedness_for(exp, "F2", fnmode=5)
    assert result.handedness == HANDEDNESS_UNKNOWN
    assert result.reason == "family_conflict"
    assert ft_neg_for(exp, 5, "F2") is False


def test_acquisition_loop_model_is_deterministic(bruker_dir: Path, tmp_path: Path) -> None:
    """TopSpin acquisition-loop model: AQSEQ decoding, y/z ↔ logical axes, partner
    stride, Δφ_rec = 0.

    Basis (confirmed by the user on 2026-09-25): ``go`` advances the phase-program
    pointers that were not explicitly taken over on every scan; the ``zd`` at the ``mc``
    boundary resets **all** pointers to index 0, and only then are the ``F1PH/F2PH``
    phase values modified ⇒ both States partners have exactly the same receiver phase
    cycle ⇒ ``Δφ_rec = 0`` when comparing handedness; ``aqseq 312`` ⇒ td1 inner,
    ``321`` ⇒ the opposite (``acqus`` ``AQSEQ`` 0/1 corresponds one-to-one with these,
    as this corpus self-confirms).
    """
    from core.experiment.acquisition_loop import (
        acquisition_model,
        decode_aqseq,
        y_z_axes,
    )

    assert decode_aqseq(0) == "321" and decode_aqseq(1) == "312"
    assert decode_aqseq("321") == "321" and decode_aqseq("312") == "312"
    assert decode_aqseq(None) is None and decode_aqseq(7) is None
    assert y_z_axes(3, "321") == ("F2", "F1")
    assert y_z_axes(3, "312") == ("F1", "F2")
    assert y_z_axes(2, "321") == ("F1", None)
    assert y_z_axes(3, None) == (None, None)

    exp = _with_pulseprogram(_experiment(bruker_dir), tmp_path, HNCO_PULSEPROGRAM)
    exp.acquisition_parameters["acqus"]["AQSEQ"] = 0  # ⇒ 321
    model = acquisition_model(exp)
    assert (model.aqseq, model.y_axis, model.z_axis) == ("321", "F2", "F1")
    assert model.pair_stride == {"F2": "adjacent", "F1": "separated"}
    assert model.receiver_dphi_deg == 0.0
    assert not model.notes
    assert [level.kind for level in model.levels].count("pair") == 2


def test_aqseq_312_swaps_y_z_and_flags_a_conflict(
    bruker_dir: Path, tmp_path: Path
) -> None:
    """``AQSEQ=312``: the y axis should be logical F1; if the pulse program's mc order
    contradicts this ⇒ no confirmation + a notice."""
    from core.experiment.acquisition_loop import acquisition_model

    exp = _with_pulseprogram(_experiment(bruker_dir), tmp_path, HNCO_PULSEPROGRAM)
    exp.acquisition_parameters["acqus"]["AQSEQ"] = 1  # ⇒ 312
    model = acquisition_model(exp)
    assert model.aqseq == "312"
    # the first pair block of this (expanded) pulse program belongs to logical F2 ⇒ it
    # contradicts the F1 inner layer implied by 312
    assert (model.y_axis, model.z_axis) == (None, None)
    # reminder text is rendered in the UI language (conftest pins this tree's default)
    # ⇒ assert only language-independent content
    assert model.notes and any("312" in note for note in model.notes)


def test_acquisition_loop_on_a_real_2d_pulseprogram(
    bruker_dir: Path, tmp_path: Path
) -> None:
    """2D: only one indirect dimension (logical F1), the y axis is always F1 and the
    partners are adjacent; no 3D AQSEQ cross-check."""
    from core.experiment.acquisition_loop import acquisition_model

    exp = _with_pulseprogram(
        _experiment(bruker_dir, "hsqc_2d"), tmp_path, HSQC_EA_PULSEPROGRAM
    )
    exp.acquisition_parameters["acqus"]["AQSEQ"] = 0
    model = acquisition_model(exp)
    assert (model.y_axis, model.z_axis) == ("F1", None)
    assert model.pair_stride == {"F1": "adjacent"}
    assert not model.notes


def test_mode_symbol_audit_carries_lines_and_dims(bruker_dir: Path, tmp_path: Path) -> None:
    exp = _with_pulseprogram(_experiment(bruker_dir), tmp_path, HNCO_PULSEPROGRAM)
    # real hncogp3d acquNs: acqu2s (logical F2)=6 (E/A), acqu3s (logical F1)=5
    _set_fnmode(exp, "F2", 6)
    _set_fnmode(exp, "F1", 5)
    audit = mode_symbol_audit(exp)
    # 2026-09-25 second revision: the F2 (E/A) criterion is **definite**: no -neg, so
    # no reminder; F1 lacks AQSEQ, so y/z cannot be told apart ⇒ a reminder
    assert len(audit["lines"]) == 1
    assert audit["lines"][0].startswith("zMODE")
    assert {item["axis"] for item in audit["dims"]} == {"y", "z"}
    by_axis = {item["axis"]: item for item in audit["dims"]}
    assert by_axis["z"]["handedness"] == HANDEDNESS_UNKNOWN
    assert by_axis["z"]["reason"] == "pathway_not_solved"
    assert by_axis["z"]["quadrature_phase_deg"] == 90.0
    assert by_axis["y"]["reason"] == "f1ea"
    # the automatic criterion's conclusion is recorded too (so a third-party review
    # need not guess whether the software added -neg)
    assert by_axis["y"]["neg_decision"] == "none"
    assert by_axis["y"]["neg_applied"] is False
    assert by_axis["z"]["neg_decision"] == "ask_user"
    assert by_axis["z"]["neg_reason"] == (
        "aqseq_unknown_so_the_first_indirect_dimension_is_not_identified"
    )


def test_canonical_negated_keywords() -> None:
    assert canonical_negated("States-TPPI-N")
    assert canonical_negated("Complex-N")
    assert canonical_negated("States-N")
    assert not canonical_negated("States-TPPI")
    assert not canonical_negated(None)


# --------------------------------------------------------------------------- #
# 4. The same text in three places (import warning / conversion log / step report)
# --------------------------------------------------------------------------- #
def test_review_line_text_is_shared_by_every_reader(
    bruker_dir: Path, tmp_path: Path
) -> None:
    """The sentence written into the fid.com correction list during conversion ==
    ``mode_symbol_audit``'s lines."""
    exp = _with_pulseprogram(_experiment(bruker_dir), tmp_path, HNCO_PULSEPROGRAM)
    text = "bruk2pipe -in ./ser \\\n  -xMODE DQD -yMODE Echo-AntiEcho -out fid\\n"
    _patched, warnings = patch_fid_com(text, exp)
    expected = mode_symbol_audit(exp)["lines"]
    assert expected
    for line in expected:
        assert line in warnings


def test_convert_script_and_report_agree_on_the_mode(bruker_dir: Path) -> None:
    """The fallback script's ``-yMODE`` and the fid.com audit target share one source
    (no longer a uniform Complex)."""
    exp = _experiment(bruker_dir, "hsqc_2d")  # acqu2s FnMODE=5
    script = generate_convert_script(exp, direct_points=2048)
    assert parse_fid_com(script)["yMODE"] == "States-TPPI"
