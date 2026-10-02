"""The **three-layer seam** from acquisition encoding to NMRPipe FT flags (the architecture the
user fixed on 2026-09-25).

On 2026-09-25 the user pointed out that `pulse_pathways.py` previously used the Layer A
(pulse-sequence physics) formula ``qphase`` to **directly predict** Layer C (the NMRPipe flag),
skipping Layer B (Bruker's storage convention) in between; the empirical rule was "valid in one
dimension" precisely because it had quietly absorbed B/C into itself, and it breaks as soon as
the dimension changes.

    Layer A  pulse-sequence physics      pulse program + phase table + FnMODE
             |                            => what quadrature this experiment was designed for
                                          (encoding family, quadrature phase +-90, the phase
                                          that
                                          is stepped)
    Layer B  Bruker storage convention   FnMODE + TD + dimension order + 3D unfolding
             |                            => how this pair is laid out in ser (R/I order,
             whether
                                          it is interleaved)
    Layer C  NMRPipe convention          A + B
                                          => whether ``-alt`` / ``-neg`` is needed before FT
                                          (the
                                          ``-N`` keyword corresponds to ALT 16/18)

This module only does **the layering and the decision entry points**, never the cross-layer
guessing of "infer the sign from a local phase":

* ``pulse_encoding(experiment, axis)`` -- the Layer A facts (from
  :mod:`core.experiment.pulse_pathways`; handedness is ``unknown`` while the pathway is
  unsolved);
* ``storage_encoding(experiment, axis)`` -- the Layer B conclusion; **not modelled yet** =>
  ``"unknown"``, with ``notes`` stating what it needs (FnMODE/TD/dimension order/3D unfolding
  rules, or verification against a truth sample);
* ``ft_neg_decision(experiment, axis, mode_keyword=None)`` -- Layer C: ``FT -neg`` is decided
  only when both A and B give a definite conclusion (or the conversion script uses a canonical
  ``-N`` mode keyword); otherwise ``False`` + a reason (not applied, and
  ``pulse_pathways.review_line`` reminds the user to review by hand in three places).

Layer A still needs completing: follow the user's idea and build a **coherence pathway solver**
(enumerate the ``{spin: coherence order}`` state transitions pulse by pulse, find the pathway
selected by the phase cycle and the receiver phase, take the ``p`` of that dimension's evolution
period), then use the Layer B storage model to map it onto the Layer C flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.data.internal_data_model import Experiment
from core.experiment.pulse_pathways import (
    Handedness,
    canonical_negated,
    handedness_for,
)
from ui_support.i18n import tr

#: The three Layer C conclusions (the product stance the user fixed on 2026-09-25: "give up on
#: judging `-neg` accurately, get the simple ones right as far as possible, and for the rest just
#: remind the user and let them judge"). ``ask_user`` is not added by default.
NEG_ADD = "add"  #: add ``FT -neg``
NEG_NONE = "none"  #: do not add
NEG_ASK = "ask_user"  #: **hand it to the user**: no guessing, just a reminder

#: Self-description of the simple rule (the audit/reminder must say clearly that "this is an
#: empirical criterion, not a physical derivation").
SIMPLE_RULE_NAME = "3D first-indirect States family"

#: Evidence for the simple rule (the truth set: the data owner's own 3D NUS pair plus their
#: the data owner's own scripts).
SIMPLE_RULE_EVIDENCE = (
    "the data owner's two 3D NUS datasets use byte-identical conversion scripts except one "
    "FT line: the same indirect position is 'FT' when acqu2s FnMODE=6 (Echo-AntiEcho) and "
    "'FT -alt -neg' when FnMODE=5 (States-TPPI)",
    "the data owner's 3D scripts put -neg only on the first indirect FT",
    "2D deposited data are all Echo-AntiEcho and process with a plain FT",
)

#: Layer B conclusion words: for now only ``unknown`` (not modelled); later there will be ``ri`` /
#: ``ir`` / ``swapped``.
STORAGE_UNKNOWN = "unknown"

#: What is missing while Layer B is not modelled (audited and written truthfully in the report).
STORAGE_NOTE = (
    "Bruker storage ordering for this dimension is not modelled yet: it needs the "
    "FnMODE + TD + dimension order + 3D unfolding rules (R/I order, interleaving), or a "
    "verified truth sample"
)

#: FnMODE -> acquisition family (the same convention as acquisition_mode_detector; re-listed here
#: to avoid a circular import).
_STATES_FNMODES = (2, 3, 4, 5)  #: QSEQ / TPPI / States / States-TPPI
_EA_FNMODES = (6,)
_QF_FNMODES = (1, 2)  #: QF / QSEQ (both handled as magnitude)


def _family_conflicts_with_fnmode(fnmode: int | None, family: str | None) -> bool:
    """Whether the pulse program's acquisition family **contradicts** ``FnMODE`` (a contradiction
    => hand it to the user, never silently decide "do not add").

    ``F1EA`` should go with ``FnMODE=6`` and ``F1QF`` with ``1/2`` (the magnitude kinds);
    ``F1PH`` is the umbrella kind for quadrature (the States family) and on its own cannot
    decide
    QSEQ/TPPI/States/States-TPPI, so it **does not count as a contradiction**. A missing
    ``FnMODE`` leaves nothing to contradict either (the caller treats that case as "undefined").
    """
    if fnmode is None or not family:
        return False
    from core.experiment.pulse_pathways import FAMILY_F1EA, FAMILY_F1QF

    value = int(fnmode)
    if family == FAMILY_F1EA:
        return value not in _EA_FNMODES
    if family == FAMILY_F1QF:
        return value not in _QF_FNMODES
    return False


def simple_neg_rule(
    ndim: int,
    logical_axis: str,
    fnmode: int | None,
    family: str | None,
    aqseq: str | None,
) -> tuple[str, str]:
    """**The simple empirical criterion** (not a physical derivation): get it right as far as
    possible and say ``ask_user`` when it cannot.

    Rules (all judged from "which indirect dimension + acquisition family + AQSEQ", never from
    the sequence name):

    * **E/A family** (``F1EA`` / FnMODE=6 in agreement) => do not add (truth: the E/A dimension
    of
      the Echo-AntiEcho one is ``FT``);
    * **QF family** (``F1QF`` / FnMODE 1/2 in agreement) => do not add (real part/magnitude
    only,
      so the notion of a sign does not exist);
    * **States family** (``F1PH`` / FnMODE 2/3/4/5) and it is the **first indirect dimension of
    a
      3D** (NMRPipe ``y``, fixed by AQSEQ) => **add** ``-neg``;
    * **States family + the second indirect dimension of a 3D** (``z``) => do not add;
    * everything else (States in 2D, undefined FnMODE, unknown AQSEQ so that y/z cannot be told
      apart) => **``ask_user``**: not added by default + a reminder to judge by hand.

    ``family`` is the **acquisition family** read from the pulse program
    (``F1PH``/``F1EA``/``F1QF``): when it **agrees** with ``FnMODE`` it is used to reinforce
    conclusions such as "do not add" (neither E/A nor QF has a case that wants -neg); as soon as
    they **contradict** (e.g. the pulse program says ``F1EA`` while ``FnMODE=5``), the user's
    2026-09-25 stance is to **hand it to the user** (``ask_user``: neither guess nor silently
    decide "do not add" -- the same convention as ``handedness_for`` recording a family clash as
    ``family_conflict``). Deciding "add" must still go through the ``FnMODE`` + ``AQSEQ`` route:
    the family alone is not enough to fix the sign (see the Layer split in the module docs).
    """
    from core.experiment.pulse_pathways import FAMILY_F1EA, FAMILY_F1QF

    if _family_conflicts_with_fnmode(fnmode, family):
        return NEG_ASK, "family_conflict"
    if family in (FAMILY_F1EA, FAMILY_F1QF):
        # the family agrees with FnMODE (or FnMODE is missing, so there is no evidence of a
        # clash) => "do not add" per the family fact
        return NEG_NONE, "acquisition_family_takes_no_neg"
    if fnmode is None:
        return NEG_ASK, "unknown_fnmode"
    if int(fnmode) in _EA_FNMODES:
        return NEG_NONE, "echo_antiecho_takes_no_neg"
    if int(fnmode) not in _STATES_FNMODES:
        return NEG_ASK, "fnmode_outside_the_states_family"
    if int(ndim) < 3:
        return NEG_ASK, "2d_states_not_covered_by_the_simple_rule"
    if aqseq is None:
        return NEG_ASK, "aqseq_unknown_so_the_first_indirect_dimension_is_not_identified"
    from core.experiment.acquisition_loop import y_z_axes

    y_axis, z_axis = y_z_axes(int(ndim), aqseq)
    if y_axis is None or z_axis is None:
        return NEG_ASK, "aqseq_vs_executed_order_conflict"
    if logical_axis == y_axis:
        return NEG_ADD, "first_indirect_dimension_in_the_states_family"
    if logical_axis == z_axis:
        return NEG_NONE, "second_indirect_dimension_in_the_states_family"
    return NEG_ASK, "indirect_dimension_not_identified"


@dataclass(frozen=True)
class EncodingLayers:
    """Audit record of the three-layer decision (per dimension)."""

    axis: str
    pulse: Handedness  #: Layer A
    storage: str = STORAGE_UNKNOWN  #: Layer B
    ft_neg: bool = False  #: the Layer C conclusion (whether it is actually applied)
    reason: str = ""
    notes: list[str] = field(default_factory=list)
    #: the three-state Layer C conclusion: add / none / ask_user (the user's 2026-09-25 stance)
    decision: str = NEG_NONE
    #: where the conclusion comes from: canonical_N / simple_rule / layer_a / none
    basis: str = ""
    #: the text reminding the user to judge by hand (non-empty only with decision=ask_user)
    ask: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "pulse_family": self.pulse.family,
            "stepped_phases": list(self.pulse.stepped_phases),
            "quadrature_phase_deg": self.pulse.quadrature_phase_deg,
            "pulse_role": self.pulse.pulse_role,
            "pulse_handedness": self.pulse.handedness,
            "storage": self.storage,
            "ft_neg": self.ft_neg,
            "decision": self.decision,
            "basis": self.basis,
            "reason": self.reason,
            "ask": self.ask,
            "notes": list(self.notes),
        }


def pulse_encoding(
    experiment: Experiment,
    logical_axis: str,
    *,
    data_dir: Path | str | None = None,
) -> Handedness:
    """Layer A: the pulse-sequence physics facts of this dimension (unsolved pathway =>
    ``unknown``).
    """
    return handedness_for(experiment, logical_axis, data_dir=data_dir)


def storage_encoding(
    experiment: Experiment,
    logical_axis: str,
    *,
    data_dir: Path | str | None = None,
) -> tuple[str, list[str]]:
    """Layer B: how Bruker stores this quadrature pair in ser.

    The modelled part (deterministic, from ``acquisition_loop``): the 1D-FID write order fixed
    by
    ``AQSEQ``, which **Bruker dimension** it turns into NMRPipe's ``y``/``z`` (=> the
    ``-yMODE/-zMODE`` and ``-yN/-zN`` options must follow it), and whether that dimension's
    States
    partner is the **adjacent** or a **separated** 1D FID.

    **The part that is not modelled/decided (stated by the user on 2026-09-25)**: the
    *permutation* of the storage order itself **does not produce conjugation** (NMRPipe's
    acquisition-mode/sign-adjustment semantics are axis-independent, and ``-neg`` is a property
    of
    the ``-N`` acquisition mode); handedness still comes from the Layer A pathway solution.
    """
    from core.experiment.acquisition_loop import acquisition_model

    model = acquisition_model(experiment, data_dir=data_dir)
    axes = f"y={model.y_axis or '?'}/z={model.z_axis or '?'}"
    stride = ",".join(f"{axis}:{value}" for axis, value in sorted(model.pair_stride.items()))
    description = f"aqseq={model.aqseq or '?'};{axes}"
    if stride:
        description += f";pair_stride={stride}"
    notes = list(model.notes)
    if model.y_axis and model.z_axis:
        expected_y = "F2" if experiment.ndim >= 3 else "F1"
        if model.y_axis != expected_y:
            notes.append(
                tr(
                    "AQSEQ={p0} makes the NMRPipe y axis logical {p1} (the conversion layer "
                    "assumes {p2}); -yMODE/-zMODE and -yN/-zN must follow this order",
                    p0=model.aqseq,
                    p1=model.y_axis,
                    p2=expected_y,
                )
            )
    return description, notes


def ft_neg_decision(
    experiment: Experiment,
    logical_axis: str,
    *,
    mode_keyword: str | None = None,
    fnmode: int | None = None,
    data_dir: Path | str | None = None,
) -> EncodingLayers:
    """Layer C: whether this dimension's FT needs ``-neg`` (simple rule + "hand it to the user
    when it cannot be judged").

    Decision order (the user, 2026-09-25: "we let the user check it themselves anyway, so use
    the
    rule with the lowest error probability"):

    1. fid.com already uses a canonical ``-N`` variant => **add** (a Layer C fact of its own);
    2. Layer A already gives a definite conclusion (explicit override / solved pathway) => adopt
       its handedness;
    3. otherwise use :func:`simple_neg_rule` (**the empirical criterion with the lowest error
       probability**, reading NMRPipe's ``y`` dimension from AQSEQ): **add / do not add /
       ``ask_user``** (cannot tell: not added by default + a reminder to the user to decide with
       the indirect-dimension flip control in the spectrum step).

    ``data_dir`` is only used to look for one more ``pulseprogram`` location (the import-time
    directory) so that the decision in the report and the decision in the script share a source.
    """
    pulse = pulse_encoding(experiment, logical_axis, data_dir=data_dir)
    storage, notes = storage_encoding(experiment, logical_axis, data_dir=data_dir)
    if canonical_negated(mode_keyword):
        return EncodingLayers(
            axis=logical_axis,
            pulse=pulse,
            storage=storage,
            ft_neg=True,
            decision=NEG_ADD,
            basis="canonical_N",
            reason="canonical -N mode keyword (NMRPipe ALT 16/18: negation of imaginaries)",
            notes=notes,
        )
    if pulse.determined:
        return EncodingLayers(
            axis=logical_axis,
            pulse=pulse,
            storage=storage,
            ft_neg=pulse.needs_neg,
            decision=NEG_ADD if pulse.needs_neg else NEG_NONE,
            basis="layer_a",
            reason="" if pulse.needs_neg else "layer_a_says_no_negation",
            notes=notes,
        )
    fnmode = pulse.fnmode if fnmode is None else fnmode
    from core.experiment.acquisition_loop import acquisition_model

    aqseq = acquisition_model(experiment, data_dir=data_dir).aqseq
    decision, why = simple_neg_rule(int(experiment.ndim), logical_axis, fnmode, pulse.family, aqseq)
    if decision == NEG_ASK:
        ask = tr(
            "automatic -neg decision skipped for this dimension (reason: {p0}); the simple rule "
            "'{p1}' does not cover it - please confirm by hand (compare with the data owner's "
            "processing script, or try both and look at the sign/dispersion of the first peak)",
            p0=why,
            p1=SIMPLE_RULE_NAME,
        )
        return EncodingLayers(
            axis=logical_axis,
            pulse=pulse,
            storage=storage,
            ft_neg=False,
            decision=NEG_ASK,
            basis="simple_rule",
            reason=why,
            ask=ask,
            notes=notes + [ask],
        )
    return EncodingLayers(
        axis=logical_axis,
        pulse=pulse,
        storage=storage,
        ft_neg=decision == NEG_ADD,
        decision=decision,
        basis="simple_rule",
        reason=why,
        notes=notes,
    )


__all__ = [
    "STORAGE_NOTE",
    "STORAGE_UNKNOWN",
    "EncodingLayers",
    "ft_neg_decision",
    "pulse_encoding",
    "storage_encoding",
]
