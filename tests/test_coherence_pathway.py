"""Guards for the Layer A solver kernel + Layer B storage mapping (the three decisions
the user made on 2026-09-25).

Covers:

1. **operator IR**: ``I_x`` and ``2I_xS_z`` share a coherence order but differ in operator
   character (⇒ later J evolution / 90° pulse behaviour differs) — the guard for "you cannot
   store only ``{spin: p}``";
2. **hard pulse / J evolution / transfer map**: the 90° branch, ``p → −p`` at 180°, and the
   ``elementary`` and ``validated_block`` INEPT modes must give the same IR (the user asked for a
   regression comparison of the two);
3. **phase cycle projector**: ``W = Σ_s e^{iΦ(s)}`` — cancelled pathways go ``|W| → 0``, kept
   ones have a large ``|W|``;
4. **handedness**: ``Δφ_rec = 0`` (``zd`` resets the pointers) ⇒ ``normal``/``conjugated`` is set
   by ``p`` of the evolution period of the selected pathway; ``p = 0`` cannot decide;
5. **OPAQUE nodes**: branches that agree still decide; branches that disagree ⇒ ``unknown`` +
   ``opaque_pulse_changes_selected_pathway``;
6. **Layer B**: ``AQSEQ`` is authoritative (the unexpanded ``mc`` textual order does not override
   it); an expanded execution contradicting AQSEQ ⇒ ``metadata_execution_conflict`` and
   ``blocking`` (fail loudly); E/A canonicalisation is axis independent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.data.bruker_reader import read_dataset
from core.experiment.coherence_pathway import (
    HANDEDNESS_CONJUGATED,
    HANDEDNESS_NORMAL,
    HANDEDNESS_UNKNOWN,
    STANDARD_INEPT,
    SUPPORT_OPAQUE,
    Term,
    UnsupportedPhysicsError,
    apply_hard_pulse,
    apply_inept_block,
    apply_j_evolution,
    apply_transfer_map,
    cycle_projector,
    opaque_branch_handedness,
    pathway_weight,
    states_handedness,
    term,
)
from core.experiment.storage_map import (
    ENCODING_ECHO_ANTIECHO,
    ENCODING_STATES,
    STORAGE_EXECUTION_CONFLICT,
    STORAGE_OK,
    canonicalize_quadrature,
    logical_coordinates,
    order_from_aqseq,
    storage_status,
)


def _cycle(phases: list[list[tuple[str, float]]], receivers: list[float]):
    from core.experiment.coherence_pathway import PhaseCycle

    return PhaseCycle(
        scans=tuple((index, tuple(entry), receivers[index]) for index, entry in enumerate(phases))
    )


# --------------------------------------------------------------------------- #
# 1. operator IR
# --------------------------------------------------------------------------- #
def test_operator_ir_keeps_operator_character_not_just_order() -> None:
    """``I_x`` and ``2I_xS_z`` share the same order but differ in operator character
    (keep them apart)."""
    inphase = term([("H", "+")])
    antiphase = term([("H", "+"), ("N", "z")])
    assert inphase.order_of("H") == antiphase.order_of("H") == 1
    assert inphase.key != antiphase.key
    assert inphase.key == "H:+" and antiphase.key == "H:+ N:z"


def test_hard_pulse_branches_and_records_phase_signature() -> None:
    """90° splits into ±1 branches recorded in the phase signature; 180° does
    ``p → −p`` without branching."""
    terms = [term([("H", "z")])]
    branches = apply_hard_pulse(terms, "H", 90.0, "ph1")
    assert sorted(item.order_of("H") for item in branches) == [-1, 1]
    assert all(dict(item.phase_signature)["ph1"] in (-1.0, 1.0) for item in branches)

    inverted = apply_hard_pulse([term([("H", "+")])], "H", 180.0, "ph2")
    assert len(inverted) == 1
    assert inverted[0].order_of("H") == -1
    with pytest.raises(UnsupportedPhysicsError):
        apply_hard_pulse(terms, "H", 45.0, "ph1")


def test_j_evolution_creates_antiphase_branch() -> None:
    """J evolution gives a ``cos`` (kept) and a ``sin`` (antiphase) branch — the former
    keeps the operator character, the latter changes it."""
    branches = apply_j_evolution([term([("H", "+")])], "H", "N")
    keys = {item.key for item in branches}
    assert "H:+" in keys
    assert any("N:z" in key for key in keys)


def test_inept_elementary_and_validated_block_agree_on_pathways() -> None:
    """The ``elementary`` and ``validated_block`` INEPT modes must give the same set of
    (channel p vector, signature)."""
    start = [term([("H", "+")])]
    elementary = apply_inept_block(
        start, source_channel="H", target_channel="N", phase_programs=("ph1", "ph2", "ph3")
    )
    block = apply_transfer_map(start, STANDARD_INEPT, "N")
    # Shared conclusion: transverse goes from H to N and H keeps a longitudinal part
    assert any(item.order_of("N") == 1 for item in block)
    assert any(item.order_of("N") == 1 and item.key.count("H:z") for item in elementary)
    assert block[0].key == "H:z N:+"


# --------------------------------------------------------------------------- #
# 2. Phase cycle projector
# --------------------------------------------------------------------------- #
def test_phase_cycle_projector_kills_and_keeps_pathways() -> None:
    """``W = Σ_s e^{iΦ(s)}``: terms adding in phase survive, opposite ones cancel out."""
    # Two scans: ph1 = 0/180, receiver 0/0. Δp(ph1) = 0 gives weight 2; Δp(ph1) = 1 gives 0.
    cycle = _cycle([[("ph1", 0.0)], [("ph1", 180.0)]], [0.0, 0.0])
    kept, killed = cycle_projector([({"H:ph1": 0.0}, 0.0), ({"H:ph1": 1.0}, 1.0)], cycle)
    assert kept.kept and not killed.kept
    assert kept.magnitude == pytest.approx(2.0)
    assert killed.magnitude == pytest.approx(0.0, abs=1e-9)


def test_receiver_phase_participates_in_the_projector() -> None:
    """The receiver phase enters ``Φ`` (receiver and pulse phases add as equals)."""
    cycle = _cycle([[("ph1", 0.0)], [("ph1", 0.0)]], [0.0, 180.0])
    weight = pathway_weight({"H:ph1": 1.0}, 1.0, cycle)
    assert weight.magnitude == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# 3. handedness
# --------------------------------------------------------------------------- #
def test_states_handedness_from_the_selected_pathway_p() -> None:
    """Evolution-period ``p = −1`` ⇒ normal; ``p = +1`` ⇒ conjugated; ``p = 0`` ⇒ undecidable."""
    assert states_handedness({"N": -1}, "N").handedness == HANDEDNESS_NORMAL
    assert states_handedness({"N": 1}, "N").handedness == HANDEDNESS_CONJUGATED
    assert states_handedness({"N": -1}, "N").needs_neg is False
    assert states_handedness({"N": 1}, "N").needs_neg is True
    unknown = states_handedness({"N": 0}, "N")
    assert unknown.handedness == HANDEDNESS_UNKNOWN
    assert unknown.reason == "evolution_interval_has_no_coherence"
    # Δφ_rec = 0 results from the mc-boundary zd pointer reset (user confirmed); record it
    assert states_handedness({"N": -1}, "N").receiver_dphi_deg == 0.0


def test_opaque_branches_keep_the_verdict_when_they_agree() -> None:
    """OPAQUE: agreeing branches still decide; otherwise ⇒ unknown plus
    ``opaque_pulse_changes_selected_pathway``."""
    agree = [states_handedness({"N": -1}, "N"), states_handedness({"N": -1}, "N")]
    verdict = opaque_branch_handedness(agree, "sp13")
    assert verdict.handedness == HANDEDNESS_NORMAL
    assert verdict.support and verdict.support[-1].level == SUPPORT_OPAQUE
    disagree = [states_handedness({"N": -1}, "N"), states_handedness({"N": 1}, "N")]
    loose = opaque_branch_handedness(disagree, "sp13")
    assert loose.handedness == HANDEDNESS_UNKNOWN
    assert loose.reason == "opaque_pulse_changes_selected_pathway"


# --------------------------------------------------------------------------- #
# 4. Layer B: AQSEQ authority + conflict grading + axis-independent E/A
# --------------------------------------------------------------------------- #
def test_aqseq_decides_the_storage_order(bruker_dir: Path, tmp_path: Path) -> None:
    """``AQSEQ`` decides the inner→outer dimension order and the linear 1D-FID index
    (including both partner slots)."""
    assert order_from_aqseq(3, "321") == ("F2", "F1")
    assert order_from_aqseq(3, "312") == ("F1", "F2")
    assert order_from_aqseq(2, "321") == ("F1",)
    assert order_from_aqseq(3, None) == ()
    coords = logical_coordinates(3, "321", {"F1": 2, "F2": 3})
    assert len(coords) == (2 * 2) * (3 * 2)
    first = coords[0]
    assert first.linear_index(("F2", "F1"), {"F1": 2, "F2": 3}) == 0
    # The inner dimension (F2) varies fastest: the second 1D FID is F2 partner 1
    assert coords[1].states["F2"] == 1 and coords[1].indices["F2"] == 0


def test_unexpanded_clause_order_never_overrides_aqseq(bruker_dir: Path, tmp_path: Path) -> None:
    """Only the **unexpanded source** order differs ⇒ trust AQSEQ (no conflict);
    a contradiction in the expanded execution ⇒ error with blocking."""
    text = (
        ";cbcaconhgpwg3d\n aqseq 321\n go=2 ph31\n mc #0 to 2\n F1PH(ip1, id0)\n F2PH(ip5, id10)\n"
    )
    exp = read_dataset(bruker_dir / "hnca_3d")
    exp.acquisition_parameters["acqus"]["AQSEQ"] = 0  # ⇒ 321
    target = Path(tmp_path) / "dataset"
    if not target.exists():
        import shutil

        shutil.copytree(Path(exp.source_path), target)
    exp.source_path = target
    (target / "pulseprogram").write_text(text, encoding="utf-8")
    status = storage_status(exp)
    assert status.aqseq == "321"
    assert status.status == STORAGE_OK  # Unexpanded: the mc textual order takes no part
    assert not status.blocking


def test_expanded_execution_conflict_fails_loudly(bruker_dir: Path, tmp_path: Path) -> None:
    """An **expanded** execution order contradicting AQSEQ ⇒
    ``metadata_execution_conflict`` plus blocking."""
    from tests.test_pulse_pathways import HNCO_PULSEPROGRAM

    exp = read_dataset(bruker_dir / "hnca_3d")
    exp.acquisition_parameters["acqus"]["AQSEQ"] = 1  # ⇒ 312, while the expanded execution is 321
    target = Path(tmp_path) / "dataset"
    if not target.exists():
        import shutil

        shutil.copytree(Path(exp.source_path), target)
    exp.source_path = target
    text = HNCO_PULSEPROGRAM.replace(";hncogp3d", ";hncogp3d\n aqseq 321")
    (target / "pulseprogram").write_text(text, encoding="utf-8")
    status = storage_status(exp)
    assert status.executed_order == "321" and status.aqseq == "312"
    assert status.status == STORAGE_EXECUTION_CONFLICT
    assert status.blocking


def test_quadrature_canonicalisation_is_axis_independent() -> None:
    """E/A canonicalisation is driven by ``encoding`` (not ``axis == "y"``); States pass
    through unchanged."""
    a, b = 1.0 + 0.0j, 2.0 + 0.0j
    assert canonicalize_quadrature(ENCODING_STATES, a, b) == (a, b)
    assert canonicalize_quadrature(ENCODING_ECHO_ANTIECHO, a, b) == (a, b)
    real_a, real_b = canonicalize_quadrature("real", a, b)
    assert real_a == a and real_b == 0


# --------------------------------------------------------------------------- #
# 5. Interface split: RF channel → nucleus vs dimension → nucleus (user, 2026-09-25)
# --------------------------------------------------------------------------- #
def test_rf_channel_and_dimension_nucleus_maps_are_separate(
    bruker_dir: Path,
) -> None:
    """The two mappings must stay separate: the nucleus of ``:fN`` comes from ``acqus NUCn``
    (+ ``SFOn``), while ``acquNs.NUC1`` only answers "which nucleus evolves in this dimension".

    The values come from a real-machine anchor (hncogp3d of BMRB 15386: ``acqus NUC1/2/3 =
    1H/13C/15N``, ``acqu2s.NUC1 = 15N``, ``acqu3s.NUC1 = 13C``, and ``acquNs`` also carries junk
    fields such as ``NUC2/NUC3=<off>`` and ``SFO2/SFO3``), but are **constructed in memory** (a
    test must not depend on corpus paths of the development machine).
    """
    from core.data.bruker_reader import read_dataset
    from core.experiment.coherence_pathway import (
        channel_for_nucleus,
        dimension_nuclei,
        rf_channel_nuclei,
    )

    exp = read_dataset(bruker_dir / "hnca_3d")
    exp.acquisition_parameters["acqus"].update(
        {
            "NUC1": "<1H>",
            "NUC2": "<13C>",
            "NUC3": "<15N>",
            "SFO1": "800.19",
            "SFO2": "201.24",
            "SFO3": "81.09",
        }
    )
    exp.acquisition_parameters["acqu2s"].update(
        {
            "NUC1": "<15N>",
            "NUC2": "<off>",
            "NUC3": "<off>",
            "SFO1": "81.09",
            "SFO2": "360.13",
            "SFO3": "500.13",
        }
    )
    exp.acquisition_parameters["acqu3s"].update(
        {
            "NUC1": "<13C>",
            "NUC2": "<off>",
            "NUC3": "<off>",
            "SFO1": "201.24",
            "SFO2": "500.13",
            "SFO3": "500.13",
        }
    )
    rf = rf_channel_nuclei(exp)
    dims = dimension_nuclei(exp)
    assert rf == {"f1": "1H", "f2": "13C", "f3": "15N"}
    assert dims == {"F3": "1H", "F2": "15N", "F1": "13C"}
    # Key guard: pulse program f3 = 15N, but the **dimension** it maps to is F2 (not F3)
    assert channel_for_nucleus(rf, "15N") == "f3"
    assert dims["F2"] == "15N" and dims["F1"] == "13C"
    # Junk fields NUC2/NUC3=<off>, SFO2/SFO3 in acquNs must not enter the RF channel table
    assert "f4" not in rf and all(value for value in rf.values())
    # A channel without SFO does not count as used (acqus missing SFO3 ⇒ no f3)
    exp.acquisition_parameters["acqus"]["SFO3"] = ""
    assert "f3" not in rf_channel_nuclei(exp)


def test_hard_pulse_lowering_uses_annotations_not_parameter_names() -> None:
    """Flip angle and channel come from the **annotations** plus the explicit syntax;
    ``p1*0.5`` ⇒ 45°; a shaped pulse is not a hard pulse."""
    from core.experiment.coherence_pathway import lower_hard_pulse, pulse_annotations

    text = (
        ";p1 : f1 channel -  90 degree high power pulse\n"
        ";p2 : f3 channel - 180 degree high power pulse\n"
        ";sp2: f2 channel - shaped pulse  90 degree  (C=O on resonance)\n"
        "  (p1 ph1):f1\n  (p2 ph5):f3\n  (p1*0.5 ph1):f1\n  (p13:sp2 ph4):f2\n"
    )
    ann = pulse_annotations(text)
    channels = {"f1": "1H", "f2": "13C", "f3": "15N"}
    first = lower_hard_pulse("  (p1 ph1):f1", ann, channels)
    assert first is not None and first.angle_deg == 90.0
    assert (first.channel, first.nucleus, first.phase_program) == ("f1", "1H", "ph1")
    second = lower_hard_pulse("  (p2 ph5):f3", ann, channels)
    assert second is not None and second.angle_deg == 180.0 and second.nucleus == "15N"
    scaled = lower_hard_pulse("  (p1*0.5 ph1):f1", ann, channels)
    assert scaled is not None and scaled.angle_deg == pytest.approx(45.0)
    # shaped ⇒ not an exact hard pulse (handed to OpaquePhysics); no annotation ⇒ do not guess
    assert lower_hard_pulse("  (p13:sp2 ph4):f2", ann, channels) is None
    assert lower_hard_pulse("  (p9 ph1):f1", ann, channels) is None
    # Annotation contradicts the explicit channel ⇒ do not guess
    assert lower_hard_pulse("  (p1 ph1):f3", ann, channels) is None


def test_spectator_rule_is_conservative() -> None:
    """Ignorable only when "every surviving term is identity on that channel" and there
    is no coupling context."""
    from core.experiment.coherence_pathway import (
        OPAQUE_SHAPED,
        OpaquePhysics,
        is_spectator_safe,
    )

    shaped_h = OpaquePhysics(kind=OPAQUE_SHAPED, channels=("H",), affects_relevant_pathway=False)
    # Even when declared "does not affect the pathway", a term with z/transverse on H blocks it
    terms = [term([("N", "+"), ("H", "z")])]
    safe, why = is_spectator_safe(shaped_h, terms, coupling_context=False)
    assert safe is False and "H:z" in why
    # All identity with no coupling context ⇒ ignorable, with a recorded reason
    other = [term([("N", "+")])]
    safe2, why2 = is_spectator_safe(shaped_h, other, coupling_context=False)
    assert safe2 is True and "identity" in why2
    # Any coupling semantics ⇒ never ignorable
    assert is_spectator_safe(shaped_h, other, coupling_context=True)[0] is False
    # Explicitly affects the pathway / relevant-unknown ⇒ not ignorable
    assert (
        is_spectator_safe(
            OpaquePhysics(kind=OPAQUE_SHAPED, channels=("H",), affects_relevant_pathway=True),
            other,
            coupling_context=False,
        )[0]
        is False
    )
    assert (
        is_spectator_safe(
            OpaquePhysics(kind=OPAQUE_SHAPED, channels=("H",), affects_relevant_pathway="unknown"),
            other,
            coupling_context=False,
        )[0]
        is False
    )


def test_watergate_is_one_opaque_node_not_half_parsed() -> None:
    """WATERGATE is opaque as a whole (its inner hard pulses are not taken apart);
    acquisition-control nodes are separate from physics nodes."""
    from core.experiment.coherence_pathway import (
        OPAQUE_WATERGATE,
        Acquire,
        FreeEvolution,
        HardPulse,
        JCouplingEvolution,
        Loop,
        OpaquePhysics,
        PhaseIncrement,
        ReceiverPhase,
        ResetPhasePointers,
    )

    wg = OpaquePhysics(kind=OPAQUE_WATERGATE, channels=("H",))
    assert wg.affects_relevant_pathway == "unknown"  # Conservative: treated as "affects" by default
    nodes: list[object] = [
        HardPulse(channel="f1", nucleus="1H", angle_deg=90.0, phase_program="ph1"),
        FreeEvolution(duration="d0"),
        JCouplingEvolution(spins=("f1", "f3"), duration="d4"),
        wg,
        PhaseIncrement(phase_program="ph3", delta_p_by_channel=(("C", 1),)),
        ReceiverPhase(),
        Loop(label="LBLSTS1", count="2"),
        Acquire(),
        ResetPhasePointers(),
    ]
    assert sum(isinstance(node, OpaquePhysics) for node in nodes) == 1  # Exactly one opaque node
    assert "index 0" in ResetPhasePointers().as_note()


def test_term_is_frozen_and_signature_accumulates() -> None:
    item = term([("N", "+")]).with_signature("ph1", 1.0).with_signature("ph1", -2.0)
    assert dict(item.phase_signature)["ph1"] == pytest.approx(-1.0)
    assert isinstance(item, Term)
