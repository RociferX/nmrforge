"""Layer A: the coherence pathway solver (fine-grained operator IR + phase-cycle projector).

The three implementation decisions the user fixed on 2026-09-25 (this module follows them):

1. **The internal state is a fine-grained operator IR**, not ``{spin: p}``: every ``Term``
   carries a coefficient (sign/complex), per-channel operator characters (``H:I+`` / ``N:Sz``
   ...), a **channel-decomposed coherence-order vector**, and a **phase signature** (the
   coefficient accumulated by each phase program). The reason: ``I_x`` and ``2I_xS_z`` have the
   same coherence order but behave differently under the later J evolution / 90 degree pulses.
2. **The phase cycle does not "pick one pathway by hand"**; the projector is computed directly:
   for a candidate pathway ``k`` in every scan ``s``,
   ``Phi_k(s) = -sum_j dp_{k,j}*phi_j(s) - phi_rec(s)`` and ``W_k = sum_s e^{i*Phi_k(s)}``;
   those with ``W ~ 0`` are cancelled and those with ``W != 0`` survive. It naturally supports
   multi-element phase tables, the same phase program used several times, ``ph31`` and longer
   nested loops.
3. **Non-standard pulses are supported at three levels**: ``EXACT`` (ideal hard 90/180, free
   evolution, weak-coupling J, validated transfer maps) / ``SEMANTIC`` (the physical meaning is
   known, e.g. "this channel is broadband inverted" => ``p -> -p``, but there **must be a
   source**, and a comment saying 180 degrees is not enough) / ``OPAQUE`` (the rotation/transfer
   is unknown). OPAQUE does not immediately make the whole dimension unknown: it produces a
   "set of uncertain transfers", and **if all branches give the same handedness it is still
   determinate**; otherwise ``unknown`` + ``opaque_pulse_changes_selected_pathway``. The first
   version is conservative: an unknown shaped/adiabatic pulse lying on this indirect dimension's
   pathway => ``unsupported_physics``; one that only belongs to a decoupling / water-suppression
   / spectator channel can be ignored, but the reason is recorded.

The first version's support surface (specified by the user): spin-1/2 H/N/C, ideal hard 90/180,
free chemical-shift evolution, weak-coupling ``IzSz`` J evolution, phase programs, receiver
phase, States/States-TPPI, and the standard INEPT transfer maps **already lowered into the same
IR** (both the ``elementary`` and the ``validated_block`` modes, the latter of which must be
regression-compared against the former). Out of scope (shaped coherence transfer, adiabatic
transfer, strong coupling, unimplemented MQ blocks, non-standard CP) => ``unsupported_physics``.
"""

from __future__ import annotations

import cmath
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace

# --------------------------------------------------------------------------- #
# operator IR
# --------------------------------------------------------------------------- #
#: Channel names (the minimal H/N/C set; the order is fixed for auditability).
CHANNELS = ("H", "N", "C")

#: Supported operator characters (on a given channel): ``+``/``-`` (raising/lowering), ``z``
#: (longitudinal), ``1`` (identity).
OPERATOR_SYMBOLS = ("+", "-", "z", "1")


@dataclass(frozen=True)
class Term:
    """One operator product (the fine-grained IR).

    ``coefficient`` carries **sign/phase information only** (a real coefficient,
    ``e^{i*phase}``); it does no quantitative intensity calculation -- the aim of this solver is
    the **allowed pathways and their phase signature**, not a Bloch simulation.
    """

    coefficient: complex = 1.0 + 0.0j
    operators: tuple[tuple[str, str], ...] = ()  #: ((channel, symbol), ...); missing => "1"
    #: channel-decomposed coherence-order vector (``+`` => +1, ``-`` => -1, ``z``/``1`` => 0)
    orders: tuple[tuple[str, int], ...] = ()
    #: accumulated phase coefficient per phase program: ``{phase program: coefficient}`` (used as
    #: the pathway's phase signature)
    phase_signature: tuple[tuple[str, float], ...] = ()

    # -- convenience accessors ----------------------------------------------
    def order_of(self, channel: str) -> int:
        for name, value in self.orders:
            if name == channel:
                return value
        return 0

    @property
    def total_order(self) -> int:
        return sum(value for _name, value in self.orders)

    @property
    def key(self) -> str:
        """The operator characters of this term (for deduplication/aggregation, without the
        coefficient).
        """
        return " ".join(f"{channel}:{symbol}" for channel, symbol in self.operators) or "1"

    def with_signature(self, phase: str, delta: float) -> Term:
        """Add ``delta`` to the coefficient of the ``phase`` program (accumulating per program)."""
        found: dict[str, float] = dict(self.phase_signature)
        found[phase] = found.get(phase, 0.0) + delta
        return replace(self, phase_signature=tuple(sorted(found.items())))


def _orders_from_operators(operators: tuple[tuple[str, str], ...]) -> tuple[tuple[str, int], ...]:
    """Derive the coherence-order vector from the operator characters (``+``/``-`` => +-1)."""
    found: dict[str, int] = {}
    for channel, symbol in operators:
        value = {"+": 1, "-": -1}.get(symbol, 0)
        if value:
            found[channel] = found.get(channel, 0) + value
    return tuple(sorted(found.items()))


def term(operators: Iterable[tuple[str, str]], coefficient: complex = 1.0 + 0.0j) -> Term:
    """Build a Term (filling in ``orders`` automatically)."""
    ops = tuple(operators)
    return Term(coefficient=coefficient, operators=ops, orders=_orders_from_operators(ops))


# --------------------------------------------------------------------------- #
# support levels (the third decision)
# --------------------------------------------------------------------------- #
SUPPORT_EXACT = "exact"
SUPPORT_SEMANTIC = "semantic"
SUPPORT_OPAQUE = "opaque"


@dataclass(frozen=True)
class SupportNote:
    """A record of one physics support level (audit: which pulse/block, the level, the
    source/reason).
    """

    label: str
    level: str
    reason: str = ""
    source: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "label": self.label,
            "level": self.level,
            "reason": self.reason,
            "source": self.source,
        }


class UnsupportedPhysicsError(Exception):
    """Outside the first version's support surface (shaped CT / adiabatic transfer / strong
    coupling / unimplemented MQ / non-standard CP).
    """


# --------------------------------------------------------------------------- #
# basic operations: hard pulses / free evolution / weak-coupling J evolution
# --------------------------------------------------------------------------- #
def apply_hard_pulse(
    terms: Sequence[Term],
    channel: str,
    flip_deg: float,
    phase: str,
) -> list[Term]:
    """Ideal hard pulse: flip ``flip_deg`` (90/180) on ``channel``, with phase program ``phase``.

    * 90 degrees: the coherence order on that channel changes by +-1 (longitudinal ``z`` and
      transverse ``+-`` convert into each other) => two branches, each of which books ``phase``
      into the phase signature as ``-dp*phi`` (phi comes from the phase cycle; only the
      "coefficient" is accumulated here);
    * 180 degrees: ``p -> -p`` (the longitudinal part is unchanged) => no branching; it is
    booked
      into the phase signature the same way (``-dp*phi``);
    * any other flip angle / anything that is not 90/180 => :class:`UnsupportedPhysicsError`
      (the first version only supports ideal 90/180).
    """
    angle = float(flip_deg) % 360.0
    if abs(angle - 180.0) < 1e-9:
        return [
            _invert(item, channel).with_signature(phase, _signature_delta(channel, 0))
            for item in terms
        ]
    if abs(angle - 90.0) < 1e-9:
        out: list[Term] = []
        for item in terms:
            for delta in (+1, -1):
                out.append(
                    _raise_lower(item, channel, delta).with_signature(
                        phase, _signature_delta(channel, delta)
                    )
                )
        return out
    raise UnsupportedPhysicsError(
        f"hard pulse with flip angle {flip_deg} deg is outside the first solver scope (90/180 only)"
    )


def _signature_delta(channel: str, delta_p: int) -> float:
    """The phase signature stores ``dp`` (multiplying it by the pulse's actual phase phi gives
    ``-dp*phi``).

    Only ``dp`` itself is stored here (at channel granularity); phi is fixed by the phase-cycle
    values -- that way the same pathway can reuse one signature across many scans and the
    projector just substitutes phi into it.
    """
    return float(delta_p)


def _invert(item: Term, channel: str) -> Term:
    """180 degree pulse: ``p -> -p`` on that channel (``z``/``1`` unchanged)."""
    ops = dict(_operators_with_defaults(item))
    symbol = ops.get(channel, "1")
    if symbol in ("+", "-"):
        ops[channel] = "-" if symbol == "+" else "+"
    return _rebuild(item, ops)


def _raise_lower(item: Term, channel: str, delta: int) -> Term:
    """90 degree pulse: the coherence order on that channel changes by ``delta`` (``z`` <->
    transverse).
    """
    ops = dict(_operators_with_defaults(item))
    symbol = ops.get(channel, "1")
    if symbol == "z":
        ops[channel] = "+" if delta > 0 else "-"
    elif symbol in ("+", "-"):
        ops[channel] = "z"
    else:  # identity => becomes transverse
        ops[channel] = "+" if delta > 0 else "-"
    return _rebuild(item, ops)


def _operators_with_defaults(item: Term) -> tuple[tuple[str, str], ...]:
    """Fill in the missing channels with ``1`` (so every channel can be handled uniformly)."""
    present = {channel for channel, _symbol in item.operators}
    defaults = tuple((channel, "1") for channel in CHANNELS if channel not in present)
    return tuple(item.operators) + defaults


def _compact(ops: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """The operator characters after dropping identity terms (``X:1``) (internal defaults do not
    stay in the result).
    """
    return tuple(sorted((channel, symbol) for channel, symbol in ops.items() if symbol != "1"))


def _rebuild(item: Term, ops: dict[str, str], *, coefficient: complex | None = None) -> Term:
    """Rebuild a Term from a per-channel dict (dropping identity terms and recomputing orders)."""
    compact = _compact(ops)
    return Term(
        coefficient=item.coefficient if coefficient is None else coefficient,
        operators=compact,
        orders=_orders_from_operators(compact),
        phase_signature=item.phase_signature,
    )


def apply_free_precession(terms: Sequence[Term], channel: str) -> list[Term]:
    """Free chemical-shift evolution: it does not change the operator characters and only adds a
    phase to that channel's coherence (handled by the caller at the pathway level via
    ``p*omega*t``). The terms are returned unchanged here -- the function exists so that an
    "evolution interval" is explicit in the IR.
    """
    return list(terms)


def apply_j_evolution(terms: Sequence[Term], channel_a: str, channel_b: str) -> list[Term]:
    """Weak-coupling ``IzSz``-type J evolution (the standard product-operator rules):

    * the ``cos(pi*J*tau)`` branch: the operator characters are unchanged;
    * the ``sin(pi*J*tau)`` branch: **the channel carrying the transverse phase stays
      transverse** and the ``z`` on/off state of the other channel is toggled -- ``I_x ->
      2I_yS_z`` (in-phase -> antiphase), ``2I_xS_z -> I_y`` (antiphase -> in-phase);
    * the coefficient records the sign only (``cos`` gives ``1``, ``sin`` gives ``i``); no
      quantitative intensity is computed.
    """
    out: list[Term] = []
    for item in terms:
        out.append(item)  # cos branch
        ops = dict(_operators_with_defaults(item))
        a_sym = ops.get(channel_a, "1")
        b_sym = ops.get(channel_b, "1")
        if a_sym in ("+", "-") and b_sym == "1":
            ops[channel_b] = "z"
        elif a_sym in ("+", "-") and b_sym == "z":
            ops[channel_b] = "1"
        elif b_sym in ("+", "-") and a_sym == "1":
            ops[channel_a] = "z"
        elif b_sym in ("+", "-") and a_sym == "z":
            ops[channel_a] = "1"
        else:
            continue  # longitudinal/identity => not affected by J evolution
        out.append(_rebuild(item, ops, coefficient=item.coefficient * 1j))
    return out


# --------------------------------------------------------------------------- #
# transfer maps (INEPT etc.): semantic macros that must be lowered into the same IR
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TransferMap:
    """A validated transfer semantic macro (``INEPT``, ``CP`` ...).

    ``premises`` records the conditions of use (ideal pulses, channels, refocusing topology);
    ``rules`` is the mapping of ``(source operator characters, target operator characters)`` --
    everything is lowered into a :class:`Term`, and no black box such as "``INEPT`` =>
    ``p_N = -1``" is used.
    """

    name: str
    source_channel: str
    target_channel: str
    premises: tuple[str, ...] = ()
    source: str = ""


#: Standard INEPT: ``I_x -> 2I_yS_z`` (equivalently, source-channel transverse -> target-channel
#: transverse + source-channel longitudinal), assuming ideal hard pulses + weak coupling + the
#: standard refocusing topology.
STANDARD_INEPT = TransferMap(
    name="INEPT",
    source_channel="H",
    target_channel="X",
    premises=(
        "ideal hard 90 deg pulses",
        "weak coupling (IzSz)",
        "standard refocusing topology (delays 1/(4J))",
    ),
    source="Bruker PP Reference / standard INEPT (Morris & Freeman 1979)",
)


def apply_transfer_map(
    terms: Sequence[Term], mapping: TransferMap, target_channel: str
) -> list[Term]:
    """Lower a transfer macro into the same IR: source-channel transverse => target-channel
    transverse + source-channel longitudinal.

    Only the standard premises are allowed (ideal pulses / weak coupling / standard topology);
    the phase dependence of INEPT is **kept**: the phase signature of the pulses inside the
    macro
    is supplied by the caller from ``mapping``'s actual phase programs (see
    :func:`apply_inept_block`).
    """
    out: list[Term] = []
    for item in terms:
        ops = dict(_operators_with_defaults(item))
        if ops.get(mapping.source_channel, "1") not in ("+", "-"):
            out.append(item)  # not transverse => the map does not apply; keep it (the caller
            #                   decides whether it is unsupported)
            continue
        source_sign = ops[mapping.source_channel]
        ops[mapping.source_channel] = "z"
        ops[target_channel] = source_sign
        out.append(_rebuild(item, ops))
    return out


def apply_inept_block(
    terms: Sequence[Term],
    *,
    source_channel: str,
    target_channel: str,
    phase_programs: Sequence[str] = (),
    mode: str = "elementary",
) -> list[Term]:
    """The INEPT block (an expandable semantic macro).

    ``mode="elementary"`` (the default): expand it operation by operation -- source-channel 90,
    J evolution, source/target 180, J evolution, target 90 (the phase programs come from
    ``phase_programs``; when absent no phase signature is written);
    ``mode="validated_block"``: use :data:`STANDARD_INEPT`'s TransferMap directly (for speed),
    and it **must** be regression-compared against the elementary mode (the user's requirement).
    """
    if mode == "validated_block":
        return apply_transfer_map(terms, STANDARD_INEPT, target_channel)
    if mode != "elementary":
        raise ValueError(f"unknown solver mode: {mode!r}")
    phases = list(phase_programs)
    out = list(terms)
    out = apply_hard_pulse(out, source_channel, 90.0, phases[0] if phases else "")
    out = apply_j_evolution(out, source_channel, target_channel)
    if len(phases) > 1:
        out = apply_hard_pulse(out, source_channel, 180.0, phases[1])
        out = apply_hard_pulse(out, target_channel, 180.0, phases[1])
    out = apply_j_evolution(out, source_channel, target_channel)
    out = apply_hard_pulse(out, target_channel, 90.0, phases[2] if len(phases) > 2 else "")
    return out


# --------------------------------------------------------------------------- #
# phase-cycle projector (the second decision)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PhaseCycle:
    """The phase values of one scan sequence:
    ``[(scan_index, {phase program: angle}, receiver angle), ...]``.
    """

    scans: tuple[tuple[int, tuple[tuple[str, float], ...], float], ...]

    def __len__(self) -> int:
        return len(self.scans)

    def as_dict(self) -> dict[str, object]:
        return {
            "n_scans": len(self.scans),
            "scans": [
                {"scan": index, "phases": dict(phases), "receiver": receiver}
                for index, phases, receiver in self.scans
            ],
        }


@dataclass(frozen=True)
class PathwayWeight:
    """The phase-cycle weight of one candidate pathway."""

    total_order_change: int
    weight: complex
    kept: bool

    @property
    def magnitude(self) -> float:
        return abs(self.weight)

    def as_dict(self) -> dict[str, object]:
        return {
            "delta_p_total": self.total_order_change,
            "weight": [self.weight.real, self.weight.imag],
            "kept": self.kept,
        }


#: The relative threshold at which ``W`` counts as 0 (relative to the largest weight).
WEIGHT_EPS = 1e-6


def pathway_weight(
    delta_p_by_phase: dict[str, float],
    delta_p_total: float,
    cycle: PhaseCycle,
) -> PathwayWeight:
    """``W = sum_s exp(i*Phi(s))``, ``Phi(s) = -sum_j dp_j*phi_j(s) - phi_rec(s)``.

    ``delta_p_by_phase`` uses **channel-decomposed** keys such as ``"H:ph1"`` (channel + phase
    program); ``delta_p_total`` is the total order change (for the audit record only, it does
    not
    enter the sum -- the phases contribute per channel).
    """
    total = 0.0 + 0.0j
    for _index, phases, receiver in cycle.scans:
        phi = 0.0
        for name, angle in phases:
            for channel in CHANNELS:
                coefficient = delta_p_by_phase.get(f"{channel}:{name}")
                if coefficient:
                    phi -= coefficient * math.radians(angle)
        phi -= math.radians(receiver)
        total += cmath.exp(1j * phi)
    magnitude = abs(total)
    kept = magnitude > WEIGHT_EPS * max(1, len(cycle))
    return PathwayWeight(total_order_change=int(delta_p_total), weight=total, kept=kept)


def cycle_projector(
    candidates: Sequence[tuple[dict[str, float], float]],
    cycle: PhaseCycle,
) -> list[PathwayWeight]:
    """Compute the projector for a batch of candidate pathways and return their weights (sorted by
    |W| descending).
    """
    weights = [
        pathway_weight(delta_p_by_phase, total, cycle) for delta_p_by_phase, total in candidates
    ]
    return sorted(weights, key=lambda item: item.magnitude, reverse=True)


# --------------------------------------------------------------------------- #
# handedness (step 5): the p of the selected pathway's evolution period -> normal / conjugated
# --------------------------------------------------------------------------- #
HANDEDNESS_NORMAL = "normal"
HANDEDNESS_CONJUGATED = "conjugated"
HANDEDNESS_UNKNOWN = "unknown"


@dataclass(frozen=True)
class HandednessVerdict:
    """The solver result."""

    handedness: str
    p_evolution: dict[str, int] = field(default_factory=dict)
    quadrature_phase_deg: float | None = None
    receiver_dphi_deg: float = 0.0
    reason: str = ""
    support: tuple[SupportNote, ...] = ()

    @property
    def needs_neg(self) -> bool:
        return self.handedness == HANDEDNESS_CONJUGATED

    def as_dict(self) -> dict[str, object]:
        return {
            "handedness": self.handedness,
            "p_evolution": dict(self.p_evolution),
            "quadrature_phase_deg": self.quadrature_phase_deg,
            "receiver_dphi_deg": self.receiver_dphi_deg,
            "reason": self.reason,
            "support": [note.as_dict() for note in self.support],
        }


def states_handedness(
    p_evolution: dict[str, int],
    evolution_channel: str,
    *,
    quadrature_phase_deg: float = 90.0,
    receiver_dphi_deg: float = 0.0,
) -> HandednessVerdict:
    """The complex handedness of a States pair.

    ``Phi_2 - Phi_1 = -(dp*dphi_pulse + dphi_rec)`` with ``dphi_rec = 0`` (the ``zd`` at the
    ``mc`` boundary resets the pointers => the two partners have the same receiver phase cycle,
    confirmed by the user on 2026-09-25). The ``p`` of the selected pathway during the evolution
    period fixes ``dp``: ``p = -1`` => ``+90`` => normal; ``p = +1`` => ``-90`` => conjugated;
    ``p = 0`` (that channel has no coherence during the evolution period) => cannot be told.
    """
    p_value = int(p_evolution.get(evolution_channel, 0))
    if p_value == 0:
        return HandednessVerdict(
            handedness=HANDEDNESS_UNKNOWN,
            p_evolution=dict(p_evolution),
            quadrature_phase_deg=quadrature_phase_deg,
            receiver_dphi_deg=receiver_dphi_deg,
            reason="evolution_interval_has_no_coherence",
        )
    qphase = -p_value * quadrature_phase_deg - receiver_dphi_deg
    qphase = ((qphase + 180.0) % 360.0) - 180.0
    if abs(qphase - 90.0) < 1e-6:
        verdict = HANDEDNESS_NORMAL
        reason = ""
    elif abs(qphase + 90.0) < 1e-6:
        verdict = HANDEDNESS_CONJUGATED
        reason = ""
    else:
        verdict = HANDEDNESS_UNKNOWN
        reason = "qphase_not_pm90"
    return HandednessVerdict(
        handedness=verdict,
        p_evolution=dict(p_evolution),
        quadrature_phase_deg=quadrature_phase_deg,
        receiver_dphi_deg=receiver_dphi_deg,
        reason=reason,
    )


def opaque_branch_handedness(
    verdicts: Sequence[HandednessVerdict], label: str
) -> HandednessVerdict:
    """The set of uncertain transfers at an OPAQUE node: if all branches agree it is still
    determinate, otherwise unknown.

    The user's third decision on 2026-09-25: ``opaque`` must not immediately make the whole
    dimension unknown; only when "two different handedness values are possible" does it become
    ``unknown`` + ``opaque_pulse_changes_selected_pathway``.
    """
    found = {item.handedness for item in verdicts if item.handedness != HANDEDNESS_UNKNOWN}
    if len(found) == 1:
        only = next(iter(found))
        note = SupportNote(label, SUPPORT_OPAQUE, "all branches agree")
        return replace(
            verdicts[0],
            handedness=only,
            reason="",
            support=tuple(verdicts[0].support) + (note,),
        )
    return HandednessVerdict(
        handedness=HANDEDNESS_UNKNOWN,
        reason="opaque_pulse_changes_selected_pathway",
        support=(SupportNote(label, SUPPORT_OPAQUE, "branches disagree"),),
    )


# --------------------------------------------------------------------------- #
# interface split (user, 2026-09-25): **RF channel -> nucleus** and **dimension -> nucleus** are
# two different mappings
# --------------------------------------------------------------------------- #
#: The nucleus of an RF channel comes from ``acqus``'s ``NUC1/NUC2/NUC3`` (+ ``SFO1/SFO2/SFO3``);
#: ``:fN`` in the pulse program is the physical RF frequency channel N (a ``pN`` without a channel
#: defaults to f1).
#: **Do not** use ``acqu2s/acqu3s.NUC1`` to decide which nucleus ``p21:f3`` is -- that is the
#: "acquisition dimension -> evolving nucleus" mapping (and ``acquNs`` also holds fields such as
#: ``NUC2/NUC3=<off>`` and ``SFO2/SFO3`` that cannot be used as RF channels).
RF_CHANNEL_KEYS = ("f1", "f2", "f3", "f4")


def _clean_nucleus(value: object) -> str:
    text = str(value or "").strip().strip("<>").strip()
    return "" if text.lower() in ("", "off") else text


def rf_channel_nuclei(experiment: object) -> dict[str, str]:
    """**RF channel -> nucleus**: ``NUC1/NUC2/NUC3`` (+``SFO1/2/3``) from ``acqus``.

    Returns a mapping such as ``{"f1": "1H", "f2": "13C", "f3": "15N"}`` (channels that are
    missing/``off`` do not appear). This is the **only** basis for deciding which nucleus ``f3``
    is in ``(p21:sp3 ph1):f3``.
    """
    acqus = (getattr(experiment, "acquisition_parameters", {}) or {}).get("acqus") or {}
    found: dict[str, str] = {}
    for index, key in enumerate(RF_CHANNEL_KEYS, start=1):
        nucleus = _clean_nucleus(acqus.get(f"NUC{index}"))
        if not nucleus:
            continue
        if not str(acqus.get(f"SFO{index}") or "").strip():
            continue  # no SFO for this channel => the channel is not used
        found[key] = nucleus
    return found


#: Logical axis <-> acquisition parameter block (both the 3D and the 2D set; the same convention
#: as ``bruker_reader._build_dimensions``).
_DIMENSION_BLOCKS = {
    3: {"F3": "acqus", "F2": "acqu2s", "F1": "acqu3s"},
    2: {"F2": "acqus", "F1": "acqu2s"},
}


def dimension_nuclei(experiment: object) -> dict[str, str]:
    """**acquisition dimension -> evolving nucleus**: ``NUC1`` of each ``acquNs`` block.

    Kept **separate** from :func:`rf_channel_nuclei`: this answers "which nucleus this logical
    spectral dimension evolves" and not "which nucleus ``fN`` in the pulse program is".
    """
    params = getattr(experiment, "acquisition_parameters", {}) or {}
    ndim = int(getattr(experiment, "ndim", 2) or 2)
    mapping = _DIMENSION_BLOCKS.get(3 if ndim >= 3 else 2, {})
    found: dict[str, str] = {}
    for axis, block_name in mapping.items():
        nucleus = _clean_nucleus((params.get(block_name) or {}).get("NUC1"))
        if nucleus:
            found[axis] = nucleus
    return found


def channel_for_nucleus(channels: dict[str, str], nucleus: str) -> str | None:
    """Look the channel of a nucleus up in the RF channel table (e.g. ``"13C"`` -> ``"f2"``);
    ``None`` when it is absent.
    """
    wanted = _clean_nucleus(nucleus)
    for channel, name in channels.items():
        if _clean_nucleus(name) == wanted:
            return channel
    return None


# --------------------------------------------------------------------------- #
# frozen first-version IR nodes (user, 2026-09-25): physics nodes are separate from acquisition
# control nodes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class HardPulse:
    """An ideal hard pulse (**exact** level): channel / nucleus / flip angle / phase program /
    power context.
    """

    channel: str  #: RF channel (f1/f2/f3)
    nucleus: str  #: the nucleus of that channel (**from the RF channel map**, not a dim map)
    angle_deg: float  #: the first version allows only 90/180 multiples (``p1*0.5`` => 45)
    phase_program: str = ""
    power_context: str = "hard"  #: hard / unknown; anything but hard => not exact


@dataclass(frozen=True)
class FreeEvolution:
    """Free evolution (chemical shift); ``duration`` keeps the original text (e.g. ``d0``)."""

    duration: str = ""
    chemical_shift: bool = True


@dataclass(frozen=True)
class JCouplingEvolution:
    """Weak-coupling ``IzSz`` J evolution; ``spins`` are the two **RF channels** involved."""

    spins: tuple[str, str] = ()
    duration: str = ""


#: The kind values of OpaquePhysics (the first version never does a semi-analytic treatment).
OPAQUE_SHAPED = "shaped"
OPAQUE_ADIABATIC = "adiabatic"
OPAQUE_CROSS_POLARIZATION = "cross_polarization"
OPAQUE_WATERGATE = "watergate"
OPAQUE_UNKNOWN_COMPOSITE = "unknown_composite"


@dataclass(frozen=True)
class OpaquePhysics:
    """A physics node whose semantics cannot be propagated automatically (shaped/adiabatic/CP/
    WATERGATE/unknown composite).

    ``affects_relevant_pathway``: ``True`` => ``unsupported_physics``; ``False`` =>
    ``ignore_for_pathway`` (``why_ignored`` must be recorded); ``"unknown"`` (the default) =>
    conservatively treated as True.
    """

    kind: str
    channels: tuple[str, ...] = ()
    affects_relevant_pathway: bool | str = "unknown"
    why_ignored: str = ""


@dataclass(frozen=True)
class PhaseIncrement:
    """A phase-program increment (an acquisition control node, not mixed with physics nodes)."""

    phase_program: str
    delta_p_by_channel: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class ReceiverPhase:
    """The receiver phase ``ph31`` (an acquisition control node)."""

    phase_program: str = "ph31"


@dataclass(frozen=True)
class Loop:
    """A loop (``lo to``; an acquisition control node)."""

    label: str
    count: str


@dataclass(frozen=True)
class Acquire:
    """``go=`` acquisition (an acquisition control node)."""

    receiver: str = "ph31"


@dataclass(frozen=True)
class ResetPhasePointers:
    """``zd`` (mc boundary; resets all phase-program pointers to index 0)."""

    def as_note(self) -> str:
        return "mc-boundary zd: all phase-program pointers reset to index 0"


# --------------------------------------------------------------------------- #
# pulse-program comments -> hard-pulse semantics (never guess from parameter names; a missing or
# contradictory comment => no guess)
# --------------------------------------------------------------------------- #
#: Comments look like ``;p1 : f1 channel -  90 degree high power pulse`` /
#: ``;sp2: f2 channel - shaped pulse  90 degree  (C=O on resonance)``.
_PULSE_COMMENT_RE = re.compile(r"^\s*;\s*(?P<label>[A-Za-z]+\d*)\s*:\s*(?P<rest>.*)$")
_CHANNEL_RE = re.compile(r"\bf(?P<num>\d)\s*channel\b", re.IGNORECASE)
_ANGLE_RE = re.compile(r"\b(?P<angle>\d{1,3})\s*degree\b", re.IGNORECASE)


def pulse_annotations(text: str | None) -> dict[str, dict[str, object]]:
    """The pulse program's own comments -> ``{pulse label: {channel, angle_deg, shaped,
    hard_power}}``.

    Only the comments are trusted: with no comment, or a comment without a channel or an angle,
    the label does not enter the table (the caller treats it as "unknown" and never guesses
    90/180 from **parameter names** such as ``p1``/``p2``).
    """
    found: dict[str, dict[str, object]] = {}
    if not text:
        return found
    for line in text.splitlines():
        match = _PULSE_COMMENT_RE.match(line)
        if not match:
            continue
        label = match.group("label")
        rest = match.group("rest")
        channel = _CHANNEL_RE.search(rest)
        angle = _ANGLE_RE.search(rest)
        entry: dict[str, object] = {}
        if channel:
            entry["channel"] = f"f{channel.group('num')}"
        if angle:
            entry["angle_deg"] = float(angle.group("angle"))
        entry["shaped"] = "shaped" in rest.lower()
        entry["hard_power"] = "high power" in rest.lower()
        if entry:
            found.setdefault(label, {}).update(entry)
    return found


#: A pulse statement: ``(p13:sp2 ph4):f2`` / ``(p1*0.5 ph1)``; it captures the label, shape,
#: scaling, phase and the explicit channel.
_PULSE_STATEMENT_RE = re.compile(
    r"\(\s*(?:center\s*)?\(?\s*(?P<label>[A-Za-z]+\d*)"
    r"(?::(?P<shape>[A-Za-z]+\d*))?"
    r"(?:\*(?P<scale>\d+(?:\.\d+)?))?"
    r"\s+(?P<phase>ph\d+)[^()]*\)"
    r"(?::(?P<channel>f\d))?",
    re.IGNORECASE,
)


def lower_hard_pulse(
    statement: str,
    annotations: dict[str, dict[str, object]],
    channels: dict[str, str],
) -> HardPulse | None:
    """Lower one pulse statement into a :class:`HardPulse`; ``None`` when it cannot be judged (the
    caller treats that as unknown).

    It must determine **all** of: the RF channel (explicit ``:fN`` or the comment), that
    channel's
    nucleus (the RF channel map), the flip angle (comment + the ``pN*n`` scaling), the phase
    program and the hard-pulse power context. The condition is that ``explicit syntax`` + the
    comment semantics + the power context of the same channel agree; a shaped pulse (``:spX``)
    is
    not a hard pulse => ``None`` (handed to :class:`OpaquePhysics`); a comment that contradicts
    the explicit channel => ``None`` (no guess).
    """
    match = _PULSE_STATEMENT_RE.search(statement or "")
    if match is None:
        return None
    label = match.group("label").lower()
    annotation = annotations.get(label)
    if not annotation:
        return None
    if match.group("shape"):
        return None  # shaped => not an exact hard pulse
    explicit = (match.group("channel") or "").lower()
    annotated = str(annotation.get("channel") or "").lower()
    if explicit and annotated and explicit != annotated:
        return None  # contradiction => no guess
    channel = explicit or annotated
    if not channel or channel not in channels:
        return None
    angle = annotation.get("angle_deg")
    if angle is None:
        return None
    scale = match.group("scale")
    if scale:
        angle = float(angle) * float(scale)
    if not annotation.get("hard_power"):
        # not a hard-power context => not exact (it may still be semantic; the caller decides)
        return None
    return HardPulse(
        channel=channel,
        nucleus=channels[channel],
        angle_deg=float(angle),
        phase_program=match.group("phase").lower(),
        power_context="hard",
    )


def is_spectator_safe(
    opaque: OpaquePhysics, terms: Sequence[Term], *, coupling_context: bool
) -> tuple[bool, str]:
    """A **conservative** spectator-channel decision (stressed by the user on 2026-09-25).

    It can only be ignored, with a reason, when (1) the channels this node involves are
    identities in **all** surviving terms (neither ``+-`` nor ``z``) **and** (2) it has no
    coupling semantics with a later transfer block (``coupling_context=False``). Otherwise it is
    never ignorable (=> ``unsupported_physics``).
    A pulse on another nucleus often changes the antiphase operator (``2I_xS_z``), so it must
    not
    be ignored just "because it is not the nucleus evolving right now".
    """
    if opaque.affects_relevant_pathway is True:
        return False, "explicitly affects the relevant pathway"
    if opaque.affects_relevant_pathway == "unknown":
        return False, "relevance to the pathway is unknown (conservative: not ignorable)"
    if coupling_context:
        return False, "the node participates in a coupling/transfer context"
    for item in terms:
        present = dict(_operators_with_defaults(item))
        for channel in opaque.channels:
            if present.get(channel, "1") != "1":
                return False, f"surviving term has {channel}:{present.get(channel)}"
    return True, "all surviving terms are identity on the affected channels"


__all__ = [
    "CHANNELS",
    "HANDEDNESS_CONJUGATED",
    "HANDEDNESS_NORMAL",
    "HANDEDNESS_UNKNOWN",
    "OPAQUE_ADIABATIC",
    "OPAQUE_CROSS_POLARIZATION",
    "OPAQUE_SHAPED",
    "OPAQUE_UNKNOWN_COMPOSITE",
    "OPAQUE_WATERGATE",
    "OPERATOR_SYMBOLS",
    "RF_CHANNEL_KEYS",
    "STANDARD_INEPT",
    "SUPPORT_EXACT",
    "SUPPORT_OPAQUE",
    "SUPPORT_SEMANTIC",
    "Acquire",
    "FreeEvolution",
    "HandednessVerdict",
    "HardPulse",
    "JCouplingEvolution",
    "Loop",
    "OpaquePhysics",
    "PathwayWeight",
    "PhaseCycle",
    "PhaseIncrement",
    "ReceiverPhase",
    "ResetPhasePointers",
    "SupportNote",
    "Term",
    "TransferMap",
    "UnsupportedPhysicsError",
    "apply_free_precession",
    "apply_hard_pulse",
    "apply_inept_block",
    "apply_j_evolution",
    "apply_transfer_map",
    "channel_for_nucleus",
    "cycle_projector",
    "dimension_nuclei",
    "is_spectator_safe",
    "lower_hard_pulse",
    "opaque_branch_handedness",
    "pathway_weight",
    "pulse_annotations",
    "rf_channel_nuclei",
    "states_handedness",
    "term",
]
