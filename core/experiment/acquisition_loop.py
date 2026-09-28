"""TopSpin acquisition-loop execution model (**deterministic**; no CTP physics).

The official semantics confirmed by the user on 2026-09-25 (the basis of this implementation):

* ``go``: at the end of every scan it advances all phase-program pointers that are **not
  explicitly taken over** (Bruker Pulse Program Reference: a phase program that is explicitly
  manipulated with pointer operations is no longer advanced automatically);
* the ``mc`` expansion contains ``wr``/``if``/``zd``; at an mc boundary ``zd`` resets **all**
  phase-program pointers to index 0, and only afterwards are the phase values of
  ``F1PH(...)``/``F2PH(...)`` modified => the **receiver phase cycle of the two States partners is
  exactly the same** => when comparing handedness **``dphi_rec = 0``** (it is not that "the
  receiver has no phase cycling");
* ``ipN``/``dpN``/``calph`` change the **phase values** of that phase program (adding a constant
  overall), and only ``rpN`` resets; **``zd`` only resets the pointers** and does not clear the
  offset accumulated by ``ip``;
* the write order of a 3D raw ``ser`` is fixed by ``AQSEQ``: ``312`` => td1 on the inside and td2
  on the outside; ``321`` => the other way round; the smallest unit written to ``ser`` is **one 1D
  FID** (the direct dimension).

The two spellings of ``AQSEQ`` (self-evidenced by this corpus + lab data): the pulse program says
``aqseq 321`` / ``aqseq 312``, and ``acqus`` says ``##$AQSEQ= 0`` (=>321) / ``= 1`` (=>312).

**One decidable conclusion that follows from this** (this project had implicitly assumed 321
before):

* ``AQSEQ=321``: the 1D FIDs are written as direct dimension (3) -> dimension 2 -> dimension 1 =>
  the innermost indirect dimension is **Bruker dimension 2** (= ``acqu2s`` = logical F2) =>
  NMRPipe's ``y`` axis <-> **logical F2**;
* ``AQSEQ=312``: the innermost indirect dimension is **Bruker dimension 1** (= ``acqu3s`` =
  logical F1) => ``y`` <-> **logical F1** -- i.e. **the correspondence between y/z and F2/F1 flips
  with AQSEQ**, and the conversion script's ``-yMODE/-zMODE`` (as well as ``-yN/-yT/-zN/-zT``)
  must flip with it, otherwise the two dimensions get each other's parameters.

Note: this module does **not** claim that "the y or z axis is conjugated because of this"
(NMRPipe's acquisition-mode/sign-adjustment semantics are axis-independent, and ``-neg`` is a
property of the ``-N`` acquisition mode rather than an axis convention); AQSEQ only fixes the
**dimension order/stride**, and the quadrature handedness still comes from the Layer A pathway
solution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.data.internal_data_model import Experiment
from core.experiment.pulse_pathways import (
    acquisition_blocks,
    mc_clause_for,
    read_pulseprogram,
)
from ui_support.i18n import tr

#: Values of ``AQSEQ`` in ``acqus`` -> the pulse-program spelling (self-evidenced by this corpus:
#: 0<->321, 1<->312).
AQSEQ_CODES = {0: "321", 1: "312"}

#: The two acquisition-order spellings (as written in the MANUAL/documentation).
AQSEQ_ORDERS = ("321", "312")


def decode_aqseq(value: Any) -> str | None:
    """``acqus AQSEQ`` (0/1) or ``aqseq 321/312`` in the pulse program -> ``"321"`` / ``"312"``.

    0 => ``321`` (the natural order), 1 => ``312``; a literal ``321``/``312`` is accepted too.
    Anything else => ``None`` (never guessed).
    """
    if value is None:
        return None
    text = str(value).strip()
    if text in AQSEQ_ORDERS:
        return text
    try:
        code = int(float(text))
    except (TypeError, ValueError):
        return None
    return AQSEQ_CODES.get(code)


def aqseq_from_pulseprogram(text: str | None) -> str | None:
    """The ``aqseq 321/312`` line in the pulse program."""
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("aqseq"):
            parts = stripped.split()
            if len(parts) > 1:
                return decode_aqseq(parts[1])
    return None


def aqseq_from_experiment(experiment: Experiment) -> str | None:
    """``AQSEQ`` from ``acqus``."""
    block = experiment.acquisition_parameters.get("acqus") or {}
    return decode_aqseq(block.get("AQSEQ"))


@dataclass(frozen=True)
class LoopLevel:
    """One loop level in the acquisition tail (``lo to <label> times <count>``)."""

    label: str
    count: str
    kind: str  #: "pair" (times 2 = a quadrature pair) / "evolution"
    inner: bool  #: whether it is the innermost level (the two acquisitions are adjacent)

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "count": self.count, "kind": self.kind, "inner": self.inner}


@dataclass(frozen=True)
class AcquisitionModel:
    """The acquisition-loop model of one dataset (audit record + Layer B input)."""

    aqseq: str | None = None
    aqseq_source: str = ""
    levels: tuple[LoopLevel, ...] = ()
    pair_stride: dict[str, str] = field(default_factory=dict)  #: logical axis -> adjacent/separated
    y_axis: str | None = None  #: the logical axis the NMRPipe y axis corresponds to
    z_axis: str | None = None
    receiver_dphi_deg: float = 0.0  #: dphi_rec between the two States partners (always 0)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "aqseq": self.aqseq,
            "aqseq_source": self.aqseq_source,
            "levels": [level.as_dict() for level in self.levels],
            "pair_stride": dict(self.pair_stride),
            "y_axis": self.y_axis,
            "z_axis": self.z_axis,
            "receiver_dphi_deg": self.receiver_dphi_deg,
            "notes": list(self.notes),
        }


def y_z_axes(ndim: int, aqseq: str | None) -> tuple[str | None, str | None]:
    """``AQSEQ`` + dimensionality -> which **logical** axis each NMRPipe ``y``/``z`` axis is.

    ``321``: direct dimension (3) -- dimension 2 -- dimension 1 => ``y`` = logical F2, ``z`` =
    logical F1 (3D); ``312``: direct dimension (3) -- dimension 1 -- dimension 2 => ``y`` =
    logical F1, ``z`` = logical F2; in 2D there is only one indirect dimension (logical F1) =>
    always ``y`` = F1, ``z`` = None.
    Bruker dimension 1 = ``acqu3s`` = logical F1 and dimension 2 = ``acqu2s`` = logical F2 (the
    project's existing convention).
    """
    if int(ndim) < 3:
        return "F1", None
    if aqseq == "312":
        return "F1", "F2"
    if aqseq == "321":
        return "F2", "F1"
    return None, None


def _levels(text: str | None, ndim: int) -> tuple[LoopLevel, ...]:
    """The loop levels of the acquisition tail (inside -> outside, the same order as
    :func:`acquisition_blocks`).
    """
    blocks = acquisition_blocks(text)
    if not blocks:
        # unexpanded form: the axis comes from the `mc` clause name and the level order is the
        # order the clauses are written in (the expansion order of the manual §11.2)
        clauses = [mc_clause_for(text, axis) for axis in (["F2", "F1"] if ndim >= 3 else ["F1"])]
        return tuple(
            LoopLevel(label=clause.clause, count="2", kind="pair", inner=index == 0)
            for index, clause in enumerate(clause for clause in clauses if clause is not None)
        )
    return tuple(
        LoopLevel(
            label=block.label,
            count=block.count,
            kind="pair" if block.is_pair else "evolution",
            inner=index == 0,
        )
        for index, block in enumerate(blocks)
    )


def _pair_stride(text: str | None, ndim: int) -> tuple[dict[str, str], str]:
    """The stride of each logical dimension's States partner in the 1D-FID sequence.

    Returns ``(stride, inner_axis_hint)``: ``stride[axis]`` = ``adjacent`` (the two partners are
    neighbouring 1D FIDs) / ``separated`` (a whole block of another indirect dimension in
    between); ``inner_axis_hint`` = which logical axis the pulse program itself considers the
    innermost indirect dimension (used for a cross-check against ``AQSEQ``).

    * expanded form: the first ``times 2`` block in the tail is the **innermost** one => its two
      partners are adjacent and the rest are separated;
    * unexpanded form: manual §11.2 -- the clause written **first** inside ``mc`` is on the inside
      => the axis written first is the adjacent one.
    """
    axes = ["F2", "F1"] if ndim >= 3 else ["F1"]
    blocks = acquisition_blocks(text)
    if blocks:
        pairs = [index for index, block in enumerate(blocks) if block.is_pair]
        stride: dict[str, str] = {}
        for slot, axis in enumerate(axes):
            if slot >= len(pairs):
                continue
            stride[axis] = "adjacent" if pairs[slot] == 0 else "separated"
        inner_axis = next((axis for axis in axes if stride.get(axis) == "adjacent"), None)
        return stride, (inner_axis or "")
    written = [axis for axis in ("F1", "F2") if mc_clause_for(text, axis) is not None]
    if not written:
        return {}, ""
    inner_axis = written[0]
    stride = {
        axis: ("adjacent" if axis == inner_axis else "separated")
        for axis in axes
        if axis in written
    }
    return stride, inner_axis


def acquisition_model(
    experiment: Experiment, data_dir: Path | str | None = None
) -> AcquisitionModel:
    """Build the acquisition-loop model of this dataset (deterministic; no CTP physics involved)."""
    text = read_pulseprogram(experiment, data_dir)
    ndim = int(experiment.ndim)
    model = AcquisitionModel()
    acqus_code = aqseq_from_experiment(experiment)
    pp_code = aqseq_from_pulseprogram(text)
    if acqus_code and pp_code and acqus_code != pp_code:
        model = AcquisitionModel(
            aqseq=None,
            aqseq_source="conflict",
            notes=[
                tr(
                    "AQSEQ disagrees between acqus ({p0}) and the pulse program ({p1}); the "
                    "acquisition order is not confirmed",
                    p0=acqus_code,
                    p1=pp_code,
                )
            ],
        )
        return model
    code = acqus_code or pp_code
    source = "acqus" if acqus_code else ("pulseprogram" if pp_code else "")
    y_axis, z_axis = y_z_axes(ndim, code)
    stride, inner_hint = _pair_stride(text, ndim)
    expected_inner = (
        {"321": "F2", "312": "F1"}.get(code or "") if int(ndim) >= 3 else None
    )
    notes: list[str] = []
    if code is None:
        notes.append(
            tr(
                "AQSEQ is missing: the 1D-FID write order (and therefore which Bruker "
                "dimension maps to the NMRPipe y/z axis) is not confirmed"
            )
        )
    elif code == "312" and ndim >= 3:
        notes.append(
            tr(
                "AQSEQ=312: the innermost indirect dimension is Bruker dimension 1 (acqu3s), so "
                "the NMRPipe y axis is logical F1 (not F2) - conversion must keep -yMODE/-zMODE "
                "in step with this order",
            )
        )
    if inner_hint and expected_inner and inner_hint != expected_inner:
        notes.append(
            tr(
                "the pulse program's innermost indirect dimension ({p0}) disagrees with the "
                "acquisition order AQSEQ={p1} (which implies {p2}); the acquisition order is not "
                "confirmed",
                p0=inner_hint,
                p1=code,
                p2=expected_inner,
            )
        )
        y_axis, z_axis = None, None
    return AcquisitionModel(
        aqseq=code,
        aqseq_source=source,
        levels=_levels(text, ndim),
        pair_stride=stride,
        y_axis=y_axis,
        z_axis=z_axis,
        receiver_dphi_deg=0.0,
        notes=notes,
    )


__all__ = [
    "AQSEQ_CODES",
    "AQSEQ_ORDERS",
    "AcquisitionModel",
    "LoopLevel",
    "acquisition_model",
    "aqseq_from_experiment",
    "aqseq_from_pulseprogram",
    "decode_aqseq",
    "y_z_axes",
]
