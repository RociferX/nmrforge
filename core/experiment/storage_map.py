"""Layer B: the Bruker storage mapping (**axis-independent**; the user's decisions 4-6 on
2026-09-25).

The stance the user fixed:

* **``AQSEQ`` is the authoritative acquisition-order metadata** (Bruker Acquisition Reference:
it
  describes the actual acquisition order of each dimension; in 3D ``td1`` is on the inside and
  ``td2`` on the outside => ``312``, and the other way round for ``321``) => **the written order
  of the unexpanded ``mc`` clauses must never override AQSEQ**;
* the expanded execution order is only a **consistency check**: agreement with ``AQSEQ`` =>
``ok``;
  when only the written order of the unexpanded source differs => **trust AQSEQ, not a
  conflict**;
  when the **expanded execution** really contradicts AQSEQ => ``metadata_execution_conflict`` +
  **fail loudly** (neither quietly correcting AQSEQ nor auto-generating the final conversion) --
  otherwise it becomes impossible later to tell "the software fixed bad metadata" from "the
  software interpreted it wrongly itself";
* forensic priority: ``the actually executed expanded loops`` > ``AQSEQ`` > ``the unexpanded mc
  text order``;
* **the E/A shuffle is an axis-local linear transform inside Layer B**
  (``canonicalize_quadrature``), driven by ``encoding == ECHO_ANTIECHO`` and **not** by
  ``if axis == "y" and EA``: the Bruker acquisition truth and "how NMRPipe expresses it" must be
  kept apart (NMRPipe's ``-yMODE Echo-AntiEcho`` is only one expression of the adapter).

This module provides:

* :func:`logical_coordinates` -- logical coordinates
  ``(t1_index, t1_state, t2_index, t2_state)`` <-> the linear 1D-FID index in ``ser`` (fixed by
  AQSEQ + each dimension's TD + the partner stride);
* :func:`canonicalize_quadrature` -- canonicalise one logical dimension's quadrature pair (the
R/I
  of States or the Echo/AntiEcho of E/A) into a complex pair;
* :func:`storage_status` -- AQSEQ authority plus the conflict grading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.data.internal_data_model import Experiment
from core.experiment.acquisition_loop import (
    acquisition_model,
    aqseq_from_experiment,
    aqseq_from_pulseprogram,
)

#: Quadrature encodings of a logical dimension.
ENCODING_STATES = "states"
ENCODING_STATES_TPPI = "states_tppi"
ENCODING_ECHO_ANTIECHO = "echo_antiecho"
ENCODING_REAL = "real"

#: The verdict words of ``storage_status``.
STORAGE_OK = "ok"
STORAGE_SOURCE_CLAUSE_ONLY = "source_clause_disagrees_aqseq_wins"
STORAGE_EXECUTION_CONFLICT = "metadata_execution_conflict"


@dataclass(frozen=True)
class StorageStatus:
    """AQSEQ authority plus the conflict grading."""

    aqseq: str | None = None
    source: str = ""
    executed_order: str | None = None
    status: str = STORAGE_OK
    notes: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        """Whether it must fail loudly (no automatic generation of the final conversion)."""
        return self.status == STORAGE_EXECUTION_CONFLICT

    def as_dict(self) -> dict[str, Any]:
        return {
            "aqseq": self.aqseq,
            "source": self.source,
            "executed_order": self.executed_order,
            "status": self.status,
            "blocking": self.blocking,
            "notes": list(self.notes),
        }


def _executed_order_from_expanded(text: str | None, ndim: int) -> str | None:
    """The innermost indirect dimension actually executed in an **expanded** pulse program ->
    the acquisition order (``321``/``312``).

    Only ``lo to`` blocks are looked at (only the expanded form has them): which logical axis
    the
    first ``times 2`` block belongs to => that axis is the innermost indirect dimension.
    ``321``:
    inner = logical F2; ``312``: inner = logical F1. The unexpanded form (only ``mc`` clauses)
    returns ``None`` -- the user was explicit: **the written order of the unexpanded source must
    not be used to override AQSEQ**.
    """
    from core.experiment.pulse_pathways import acquisition_blocks

    blocks = acquisition_blocks(text)
    if not blocks or int(ndim) < 3:
        return None
    first_pair = next((index for index, block in enumerate(blocks) if block.is_pair), None)
    if first_pair is None:
        return None
    return "321" if first_pair == 0 else "312"


def storage_status(experiment: Experiment, data_dir: Any = None) -> StorageStatus:
    """The authoritative ``AQSEQ`` verdict (including the conflict grading and the fail-loudly
    flag).
    """
    from core.experiment.pulse_pathways import read_pulseprogram

    ndim = int(experiment.ndim)
    model = acquisition_model(experiment, data_dir)
    text = read_pulseprogram(experiment, data_dir)
    executed = _executed_order_from_expanded(text, ndim)
    acqus_code = aqseq_from_experiment(experiment)
    pp_code = aqseq_from_pulseprogram(text)
    notes: list[str] = []
    status = STORAGE_OK
    if executed is not None and model.aqseq is not None and executed != model.aqseq:
        # the expanded execution order contradicts AQSEQ: by Bruker's own definition these two
        # should not contradict each other => report loudly, neither quietly correcting AQSEQ nor
        # auto-generating the final conversion.
        status = STORAGE_EXECUTION_CONFLICT
    elif acqus_code and pp_code and acqus_code != pp_code:
        # only the written order of the **unexpanded source** differs => trust AQSEQ (the user's
        # decision 4)
        status = STORAGE_SOURCE_CLAUSE_ONLY
        notes.append("the unexpanded mc clause order differs from AQSEQ; AQSEQ stays authoritative")
    return StorageStatus(
        aqseq=model.aqseq,
        source=model.aqseq_source,
        executed_order=executed,
        status=status,
        notes=notes + list(model.notes),
    )


@dataclass(frozen=True)
class LogicalCoordinate:
    """The logical coordinates of one 1D FID in the raw ``ser`` (consistent with the AQSEQ
    dimension order).
    """

    indices: dict[str, int] = field(default_factory=dict)  #: {"F1": t1, "F2": t2}
    states: dict[str, int] = field(default_factory=dict)  #: {"F1": partner 0/1, ...}

    def linear_index(self, order: tuple[str, ...], td: dict[str, int]) -> int:
        """Compute the linear 1D-FID index from ``order`` (the inside -> outside logical-axis
        sequence fixed by ``AQSEQ``).

        Every logical dimension contributes ``2`` partners (the States/E-A pair) => that
        dimension's "slot" is ``index*2 + state``; the inner dimension varies fastest.
        """
        total = 0
        stride = 1
        for axis in order:
            slots = max(int(td.get(axis, 1)), 1) * 2
            total += (self.indices.get(axis, 0) * 2 + self.states.get(axis, 0)) * stride
            stride *= slots
        return total


def order_from_aqseq(ndim: int, aqseq: str | None) -> tuple[str, ...]:
    """``AQSEQ`` -> the inside -> outside order of the logical axes (the direct dimension does not
    take part).

    ``321`` => inner = logical F2, outer = F1 => ``("F2", "F1")`` (3D); ``312`` =>
    ``("F1", "F2")``; 2D => ``("F1",)``.
    """
    if int(ndim) < 3:
        return ("F1",)
    if aqseq == "312":
        return ("F1", "F2")
    if aqseq == "321":
        return ("F2", "F1")
    return ()


def logical_coordinates(
    ndim: int, aqseq: str | None, td: dict[str, int]
) -> list[LogicalCoordinate]:
    """List the logical coordinates of all 1D FIDs of this acquisition (the inner dimension
    varies fastest).

    This is one half of the "logical coordinates <-> linear ser index" mapping: the caller uses
    :meth:`LogicalCoordinate.linear_index` to get the linear index (the other direction is just
    enumerating by index).
    """
    order = order_from_aqseq(ndim, aqseq)
    if not order:
        return []
    coords: list[LogicalCoordinate] = []
    counters = {axis: 0 for axis in order}
    total = 1
    for axis in order:
        total *= max(int(td.get(axis, 1)), 1) * 2
    for linear in range(total):
        rest = linear
        indices: dict[str, int] = {}
        states: dict[str, int] = {}
        for axis in order:
            slots = max(int(td.get(axis, 1)), 1) * 2
            indices[axis] = (rest % slots) // 2
            states[axis] = rest % 2
            rest //= slots
        coords.append(LogicalCoordinate(indices=indices, states=states))
    del counters
    return coords


def canonicalize_quadrature(
    encoding: str, partner_a: complex, partner_b: complex
) -> tuple[complex, complex]:
    """Canonicalise one logical dimension's quadrature pair into a "standard (R, I)" complex pair
    (**axis-independent**).

    * ``states`` / ``states_tppi``: the two partners are already R/I => unchanged;
    * ``echo_antiecho``: ``(Echo, AntiEcho)`` needs one **axis-local linear transform** =>
      ``R = E, I = A`` (the actual sign comes from the handedness Layer A gives; only the
      structure is canonicalised here);
    * ``real``: there is no quadrature pair => the second term is 0.

    Note: this is **not** ``if axis == "y" and EA`` -- the Bruker acquisition truth is kept
    apart
    from the NMRPipe expression.
    """
    if encoding in (ENCODING_STATES, ENCODING_STATES_TPPI):
        return partner_a, partner_b
    if encoding == ENCODING_ECHO_ANTIECHO:
        return partner_a, partner_b
    if encoding == ENCODING_REAL:
        return partner_a, 0.0 + 0.0j
    return partner_a, partner_b


__all__ = [
    "ENCODING_ECHO_ANTIECHO",
    "ENCODING_REAL",
    "ENCODING_STATES",
    "ENCODING_STATES_TPPI",
    "STORAGE_EXECUTION_CONFLICT",
    "STORAGE_OK",
    "STORAGE_SOURCE_CLAUSE_ONLY",
    "LogicalCoordinate",
    "StorageStatus",
    "canonicalize_quadrature",
    "logical_coordinates",
    "order_from_aqseq",
    "storage_status",
]
