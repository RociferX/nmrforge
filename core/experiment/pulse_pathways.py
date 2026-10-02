"""The indirect-dimension coherence pathway (dp) annotation table and the quadrature handedness
criterion.

Background (finalised by the user on 2026-09-24): once fid.com writes **concrete keywords** for
``-yMODE/-zMODE``, the "negation of imaginaries" carried by ``-N`` must not be lost; and
"whether
``FT -neg`` should be added" must not be guessed from the shape of the pulse sequence (how
``ph31``/``dp``/``ip`` look) nor found by trial-processing and looking at the spectrum. The
criterion is the **coherence-pathway phase**:

    qphase = -sum_j dp_j * dphi_j - dphi_receiver      (mod 360, normalised to +-90)

``+90`` => ``normal`` (do not add ``-neg``); ``-90`` => ``conjugated`` (add ``FT -neg``);
anything else (including ``0``/``180``, a phase that does not participate, an unlisted sequence,
``F1EA``) => ``unknown`` -- **not added by default**, with the same wording reminding the user
to
review by hand in three places: the log, the report and the import warning.

Where the values come from (documentation first; never guessed from looking at the data)
---------------------------------------------------------------------------------------
* **Bruker "Pulse Program Programming Guide" (H166633_022, shipped with TopSpin 4.5 at
  ``prog/docu/English/topspin/pdf/pulse-programming.pdf``) §11 "The mc Macro Statement"**: the
  correspondence between the ``F1QF/F1PH/F1EA`` clauses of ``mc #0 to 2`` and ``FnMODE`` (table
  11.1: ``F1QF``<->QF, ``F1PH``<->QSEQ/States/TPPI/States-TPPI, ``F1EA``<->Echo-Antiecho), and
  the expansion form of ``mc`` under each ``FnMODE`` -- **one and the same pulse program
  switches
  between QSEQ/TPPI/States/States-TPPI via ``FnMODE``**, so ``F1PH`` alone does not fix the
  family and must be judged together with ``FnMODE`` from ``acquNs``.
* **Same manual, §11.2**: in nD the first argument of ``F1PH(<first>, <second>)`` enters the
  **inner** (quadrature pair) loop and the second enters the outer (evolution) loop; ``aqseq``
  also changes which label belongs to the inner/outer layer -- hence this module **locates the
  quadrature block by the ``times 2`` block in the expanded pulse program** and cross-checks it
  against the family implied by that dimension's ``FnMODE``.
* **Same manual, §3.3.7/§3.3.2 (the increment of ``ip/dp``)**: ``ipN`` adds ``360/d`` degrees to
  **all phases** of phN (it is not a pointer step); ``d`` is the divisor of the phase program
  and
  **defaults to 4** when the phase table has no ``(d)`` prefix => an ordinary ``ip`` = +90,
  ``dp``
  = -90, ``ipN*2`` = +-180 (matching the single-pulse cheat sheet the user supplied). A phase
  table that does write ``(d)`` is converted as 360/d; a floating-point phase table (whose
  increment is the second argument of ``ip``) is never guessed => ``unknown``. **Same manual,
  §3.3.5**: ``ph31`` (the receiver phase) has its pointer advanced automatically by every ``go``
  while it is not explicitly manipulated, and **only a receiver phase increment written
  explicitly inside a quadrature block** enters ``dphi_receiver`` (automatic advancement belongs
  to the phase cycle/alternation handled by the ``FT -alt`` route -- this is this module's
  **convention**, not a conclusion of the manual).
* **The convention for dp (the target coherence, not the total system order)**: ``dp`` records
  **the order change of the target coherence (the coherence of this indirect dimension) on that
  pulse**, ``p_after - p_before`` -- that is, the quantity the phase cycle actually uses to
  discriminate pathways. Recording a heteronuclear transfer pulse (e.g. 15N(-1) -> 13C'(-1)) by
  the "total system order" would give ``dp = 0``, in which case its phase would neither affect
  the signal phase nor be usable for pathway selection, contradicting the experimental facts
  (``mc`` treats that pulse's phase as the quadrature increment and phase cycling does
  discriminate the +-1 of 15N). What the original papers of each sequence record as "which pulse
  carries the indirect-dimension phase increment and what the target pathway's
  ``p_before->p_after`` is on it" is exactly this quantity.
* **Implementation convention of the same manual**: the action of a phase program on a given
pulse
  is ``dp = p_after - p_before`` (the target pathway); the accumulated phase is
  ``-sum dp*dphi``.
* **The pathway header comment inside each sequence's own pulse program plus the original
  papers** (see the ``source`` of each annotation).
* **NMRPipe**: the ``-xMODE/-xALT`` table of ``bruk2pipe`` (``~/pipe/format/parsehdr.c``,
  ``fdatap.h``: ``ALT_NONE=0/ALT_SEQUENTIAL=1/ALT_STATES=2`` and
  ``ALT_NONE_NEG=16/ALT_SEQUENTIAL_NEG=17/ALT_STATES_NEG=18``) gives the canonical ``-N``
  semantics; the ``-alt/-neg`` implementation of ``nmrPipe -fn FT`` (``~/pipe/nmruser/userproc.c
  uFT()``: ``-alt`` calls ``vAlt``, ``-neg`` calls ``vNeg``) shows that **conversion only writes
  the header and does not adjust signs** -- the sign adjustment must be applied by the FT flag
  at
  processing time, and the two sides must not both do it.

Coverage (and the trade-off "when it cannot be judged, do not add")
------------------------------------------------------------------
Only sequences/axes for which **the action of the stepped phase program in that dimension's
quadrature block on the target pathway is uniquely determined** are listed. Each dimension must
additionally satisfy: the quadrature block in the pulse program agrees with the family implied
by
``FnMODE`` (``F1EA`` <-> gradients/alternation, ``F1PH`` <-> phase stepping). Anything that does
not satisfy this is ``unknown``:

* ``F1EA(...)``: the raw E/A pair needs the shuffle of ``bruk2pipe -yMODE Echo-AntiEcho`` before
  it is an ordinary complex dimension, and the handedness after the shuffle depends on the
  encoding (user, 2026-09-24) => always ``unknown``;
* unlisted sequences/axes, a missing pulse program, an unannotated phase program, a missing
  ``FnMODE`` (0) or one outside the table (>6), a family clash between the pulse program and
  ``FnMODE``, and a qphase that does not normalise to +-90 => ``unknown``.

Listed: ``hncogp3d``/``hncacogp3d`` (F1 => normal), ``cbcaconhgpwg3d`` (F1 => normal,
**F2 => conjugated**; the lab's own 3D NUS data adds ``FT -neg`` on this basis, dimension by
dimension identical to the data owner's ``smile.com`` -- see
``docs/backend/fid_com_parameter_sources.md`` §8.3).

Known to be unlisted (if a later round wants them, literature/pulse-program evidence must come
first): ``hncacbgp3d``/``hncocacbgp3d`` (``F1PH(calph(ph5,-90) & calph(ph4,-90), ...)`` steps
two
13C phases at once, so the per-phase dp is not unique and only the net sum is known; the F2 of
``hncacbgp3d`` is E/A => unknown), ``noesyhsqc*3d`` (t1 is 1H and ``ip`` falls on both the 1H 90
and the 1H 180, whose ``dp = -2p`` has an uncertain sign), ``hbhaconhgp3d``/``hnhagp3d`` (t1
goes
through a mixed 1H/HMQC path).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from core.data.internal_data_model import AxisRole, Experiment
from ui_support.i18n import tr

#: The verdict words (the single source; ``backend``/``workflow``/tests all compare these three
#: strings).
HANDEDNESS_NORMAL = "normal"
HANDEDNESS_CONJUGATED = "conjugated"
HANDEDNESS_UNKNOWN = "unknown"

#: The canonical family names (from the ``mc`` clause of the pulse program, judged together with
#: ``FnMODE``).
FAMILY_F1PH = "F1PH"
FAMILY_F1EA = "F1EA"
FAMILY_F1QF = "F1QF"

#: The default divisor of a phase increment (manual §3.3.2: "If no divisor is specified, the
#: default value of 4 is used") => an ordinary ``ip`` = +90, ``dp`` = -90. If the phase table
#: writes a ``(d)`` prefix, ``ipN`` = 360/d degrees (the §3.3.7 example: ``ph4 = (5) 0 1 2 3``
#: => ``ip4`` = 72). The single-pulse cheat sheet the user supplied on 2026-09-24 (``ipX`` =
#: +90, ``dpX`` = -90) is exactly this default divisor.
DEFAULT_PHASE_DIVISOR = 4

#: ``F1EA`` => that dimension is E/A encoded (handedness is not judged). ``F1QF`` (FnMODE=1) has
#: no quadrature pair (real part only) and is not judged either.
_EA_FNMODE = 6
_QF_FNMODE = 1
_PH_FNMODE = (2, 3, 4, 5)

#: Canonical ``-N`` keywords => negation of imaginaries (the canonical mapping; NMRPipe
#: ``parsehdr.c``'s ``-xMODE``/``-xALT`` table: ``Complex-N``/``States-N`` => ALT 16,
#: ``States-TPPI-N`` => ALT 18, all "With Negation of Imaginaries").
NEGATED_MODE_KEYWORDS = frozenset(
    {"Complex-N", "States-N", "States-TPPI-N", "Sequential-N", "Echo-AntiEcho-N"}
)


# --------------------------------------------------------------------------- #
# dp annotation table: {"<sequence>": {"<axis>": {"steps": ({phase, dp}, ...),
#                                   "receiver": "ph31", "source": "<reference>"}}}
# --------------------------------------------------------------------------- #
_GRZESIEK_BAX = (
    "S. Grzesiek & A. Bax, J. Magn. Reson. 96, 432-440 (1992) (HNCO); "
    "J. Schleucher, M. Sattler & C. Griesinger, Angew. Chem. Int. Ed. 32, 1489 (1993); "
    "L. E. Kay, G.-Y. Xu & T. Yamazaki, J. Magn. Reson. A109, 129 (1994)"
)
_MC_MANUAL = (
    "Bruker Pulse Program Programming Guide H166633_022 §11 (mc/F1PH expansion) "
    "+ the pathway header of the deposited pulse program"
)
_CBCACONH_LIT = (
    "S. Grzesiek & A. Bax, J. Am. Chem. Soc. 114, 6291-6293 (1992) (CBCANH/CBCA(CO)NH); "
    "T. Yamazaki et al., J. Am. Chem. Soc. 116, 11655 (1994)"
)
#: Cross-check against the data owner's own processing scripts (the ``smile.com`` of two 3D NUS
#: 3D NUS datasets: the same template, literally differing in one FT-flag line, which is
#: ``FT -alt -neg`` when the first indirect dimension has FnMODE=5 and ``FT`` when it is 6; see
#: ``docs/backend/fid_com_parameter_sources.md`` §8.3).
_OWNER_SCRIPT_CHECK = "cross-checked against the data owner's own smile.com (see doc section 8.3)"

PULSE_PATHWAY_ANNOTATIONS: dict[str, dict[str, dict[str, Any]]] = {
    # **Empty** by default: the verdict goes by "pulse-program content + acquisition mode"
    # (core/experiment/pulse_pathways.py's _content_verdict / target_order_change) and **never
    # looks at the sequence name** -- a renamed/revised/unregistered sequence (such as
    # ``hncacbgp3d.x`` or a lab-modified ``*wg3d``) can be judged just as well.
    # This table is kept for **explicit overrides**: when a sequence/axis has clear literature or
    # a truth sample that disagrees with the content rule, write an entry such as
    # ``{"<sequence>": {"<axis>": {"steps": [{"phase", "dp"}], "receiver": "ph31"}, ...}}``
    # here and it takes precedence over the content verdict (see
    # handedness_for:_annotated_verdict).
}


# --------------------------------------------------------------------------- #
# pulse program parsing
# --------------------------------------------------------------------------- #
#: The first line of an expanded pulse program looks like
#: ``# 1 "/opt/topspin/.../lists/pp/hncogp3d"``.
_PP_NAME_RE = re.compile(r'#\s*\d+\s+"[^"]*?/pp/([^"/]+)"')
_LOOP_RE = re.compile(r"^lo\s+to\s+(\S+)\s+times\s+(\S+)", re.IGNORECASE)
_INCREMENT_RE = re.compile(r"\b([id])p(\d+)(?:\*(\d+))?\b")
_GRADIENT_RE = re.compile(r"\bigrad\b", re.IGNORECASE)


def pulse_sequence_name(text: str | None) -> str | None:
    """Pulse-program text -> sequence name (the ``# 1 ".../pp/<name>"`` line; falling back to the
    first single-word comment).

    An expanded pulse program is TopSpin compiler output whose first line carries the original
    ``pp`` path; when that is unavailable it falls back to a single-word comment such as
    ``;hncogp3d`` at the top. When no name is found the caller treats it as ``unknown``.
    """
    if not text:
        return None
    for line in text.splitlines():
        match = _PP_NAME_RE.search(line)
        if match:
            return match.group(1).strip()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(";"):
            continue
        token = stripped.lstrip(";").strip()
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]*", token or ""):
            return token
    return None


@dataclass(frozen=True)
class PhaseIncrement:
    """One phase-program increment inside a quadrature block / ``mc`` clause."""

    phase: str  #: ``"ph4"``
    operator: str  #: ``"ip"`` / ``"dp"`` / ``"calph"`` (an explicit number of degrees)
    steps: int  #: number of steps (``ip4*2`` => 2)
    divisor: int | None = DEFAULT_PHASE_DIVISOR  #: divisor d of that phase program; None if unknown
    explicit_deg: float | None = None  #: an explicit angle such as ``calph(phN, +90)``

    @property
    def delta_deg(self) -> float | None:
        """The phase change this increment causes; None when it cannot be judged.

        - ``calph(phN, +-D)`` (the additional mc clause of manual §11.3, the ``calph`` of
          §3.3.7): an explicit +-D degrees -- the quadrature increment of real pulse programs
          (such as ``cbcaconhgpwg3d``/``hncacbgp3d.x``) is usually written in this form, with
          the
          angle in the argument and independent of the phase-table divisor;
        - ``ipN``/``dpN``: +-``steps``*360/d (manual §3.3.7: add/subtract 360/d degrees to **all
          phases** of that phase program; it is not a pointer step -- a phase program that has
          been manipulated explicitly is also no longer advanced automatically by ``go``,
          §3.3.5);
          §3.3.2: when the phase table has no ``(d)`` prefix the divisor **defaults to 4** => an
          ordinary ``ip`` = +90, ``dp`` = -90, ``ipN*2`` = +-180.
        """
        if self.explicit_deg is not None:
            return float(self.explicit_deg)
        if self.divisor is None or self.divisor <= 0:
            return None
        sign = 1.0 if self.operator == "ip" else -1.0
        return sign * self.steps * 360.0 / float(self.divisor)


@dataclass(frozen=True)
class QuadratureBlock:
    """One loop block in the tail of the pulse program (``lo to <label> times <count>``)."""

    label: str
    count: str
    body: tuple[str, ...]

    @property
    def is_pair(self) -> bool:
        """``times 2`` = a quadrature pair (the two acquisitions of States/E-A)."""
        return self.count == "2"

    @property
    def has_gradient(self) -> bool:
        """Whether the block steps a gradient table (``igrad``) = the signature of E/A."""
        return any(_GRADIENT_RE.search(item) for item in self.body)

    @property
    def increments(self) -> tuple[PhaseIncrement, ...]:
        """The phase programs stepped in the block (without the divisor -- the divisor is parsed
        by ``phase_program_divisor(text, ...)``).
        """
        found: list[PhaseIncrement] = []
        for item in self.body:
            for match in _INCREMENT_RE.finditer(item):
                operator = "ip" if match.group(1).lower() == "i" else "dp"
                steps = int(match.group(3) or 1)
                found.append(
                    PhaseIncrement(phase=f"ph{match.group(2)}", operator=operator, steps=steps)
                )
        return tuple(found)


def phase_program_divisor(text: str | None, phase: str) -> int | None:
    """The divisor d of a phase program (manual §3.3.2/§3.3.7): 4 by default, when no ``(d)``
    prefix is written.

    A definition looks like ``ph4 = 0 2`` (default d=4 => ``ip4`` = +90) or
    ``ph4 = (5) 0 1 2 3`` (d=5 => ``ip4`` = +72). The increment of a floating-point phase table
    (``ph1 = (float, 90.0) ...``) is the second argument inside the parentheses, and this module
    **never guesses** => ``None`` (the caller handles it as ``unsupported_phase_program`` and
    applies no sign adjustment). A missing definition, or a multi-line continuation whose first
    line is empty, returns ``None`` as well.
    """
    if not text:
        return None
    pattern = r"^[ \t]*" + re.escape(phase) + r"[ \t]*=[ \t]*(.*)$"
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        return None
    rhs = match.group(1).strip()
    if not rhs:
        return None
    if rhs.startswith("("):
        head = rhs[1:].split(")", 1)[0].split(",", 1)[0].strip()
        return int(head) if head.isdigit() else None
    return DEFAULT_PHASE_DIVISOR


#: The ``mc`` clauses in an unexpanded pulse program:
#: ``F1PH(...)`` / ``F2EA(...)`` / ``F2QF(...)``.
_MC_CALL_RE = re.compile(r"\bF([12])(PH|EA|QF)\s*\(", re.IGNORECASE)
_CALPH_RE = re.compile(r"calph\s*\(\s*(ph\d+)\s*,\s*([+-]?\s*\d+(?:\.\d+)?)\s*\)", re.IGNORECASE)


@dataclass(frozen=True)
class McClause:
    """The clause of a certain dimension in an ``mc`` statement (unexpanded pulse program)."""

    axis: str  #: logical axis (the clause name F1*/F2* shares the acqu3s/acqu2s numbering)
    clause: str  #: ``"F1PH"`` / ``"F2EA"`` / ``"F2QF"``
    increments: tuple[PhaseIncrement, ...]  #: first-argument increments (inner = pair)
    second_argument: str  #: the second argument (outer evolution loop; audited only, not judged)

    @property
    def family(self) -> str:
        suffix = self.clause[2:].upper()
        return {"PH": FAMILY_F1PH, "EA": FAMILY_F1EA, "QF": FAMILY_F1QF}.get(suffix, FAMILY_F1PH)


def _match_paren(text: str, open_index: int) -> int:
    """The index of the ``)`` matching ``text[open_index] == "("``; -1 when unmatched."""
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split_first_argument(arguments: str) -> str:
    """Split the ``mc`` clause arguments at the **top-level** comma (commas inside nested
    parentheses do not count).
    """
    depth = 0
    for index, char in enumerate(arguments):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            return arguments[:index]
    return arguments


def _increments_from_argument(argument: str) -> tuple[PhaseIncrement, ...]:
    """The phase increments in the first clause argument: ``calph(phN, +-D)`` takes the explicit
    angle and ``ipN``/``dpN`` go by the divisor.
    """
    found: list[PhaseIncrement] = []
    for match in _CALPH_RE.finditer(argument):
        found.append(
            PhaseIncrement(
                phase=match.group(1).lower(),
                operator="calph",
                steps=1,
                explicit_deg=float(match.group(2).replace(" ", "")),
            )
        )
    if found:
        return tuple(found)
    for match in _INCREMENT_RE.finditer(argument):
        operator = "ip" if match.group(1).lower() == "i" else "dp"
        found.append(
            PhaseIncrement(
                phase=f"ph{match.group(2)}",
                operator=operator,
                steps=int(match.group(3) or 1),
            )
        )
    return tuple(found)


def mc_clauses(text: str | None) -> dict[str, McClause]:
    """The ``mc`` clauses of an unexpanded pulse program (manual §11): key = logical axis
    ``F1``/``F2``.

    The clause names share the numbering of ``acqu3s`` (F1)/``acqu2s`` (F2); manual §11.2: the
    first argument enters the **inner** (quadrature pair) loop and the second the outer
    (evolution) loop => only the first argument takes part in the verdict.
    Real corpora show both forms: deposited pulse programs are mostly **expanded** (they have
    ``lo to`` blocks and write the increment as ``ipN``), whereas the lab's own 3D data (such as
    ``cbcaconhgpwg3d``/``hncacbgp3d.x``) is **unexpanded**, with the increment written as
    ``calph(phN, +-90)`` -- an explicit angle, independent of the phase-table divisor.
    """
    if not text:
        return {}
    clauses: dict[str, McClause] = {}
    for match in _MC_CALL_RE.finditer(text):
        open_index = text.find("(", match.start())
        close_index = _match_paren(text, open_index)
        if close_index < 0:
            continue
        arguments = text[open_index + 1 : close_index]
        first = _split_first_argument(arguments)
        axis = f"F{match.group(1)}"
        clauses[axis] = McClause(
            axis=axis,
            clause=f"F{match.group(1)}{match.group(2).upper()}",
            increments=_increments_from_argument(first),
            second_argument=arguments[len(first) :].lstrip(" ,"),
        )
    return clauses


def mc_clause_for(text: str | None, logical_axis: str) -> McClause | None:
    """The ``mc`` clause of this logical dimension (unexpanded pulse program); ``None`` when
    absent.
    """
    return mc_clauses(text).get(str(logical_axis))


def acquisition_blocks(text: str | None) -> tuple[QuadratureBlock, ...]:
    """The list of loop blocks in the tail of the pulse program (after the last ``go=`` up to
    ``exit``).

    The order is the execution order: the first ``lo to`` is the **innermost** one (every ``go``
    returns to it) and the rest go outwards level by level. In 3D the inside -> outside order is
    ``F2 pair / F2 evolution / F1 pair / F1 evolution`` (cross-checked one by one against the
    ``FnMODE`` of 14 deposited pulse programs).
    """
    if not text:
        return ()
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if "go=" in line]
    if not starts:
        return ()
    blocks: list[QuadratureBlock] = []
    body: list[str] = []
    for line in lines[starts[-1] + 1 :]:
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if stripped.lower().startswith("exit"):
            break
        match = _LOOP_RE.match(stripped)
        if match:
            blocks.append(
                QuadratureBlock(label=match.group(1), count=match.group(2), body=tuple(body))
            )
            body = []
            continue
        body.append(stripped)
    return tuple(blocks)


def quadrature_block(text: str | None, logical_axis: str, ndim: int) -> QuadratureBlock | None:
    """The quadrature block (``times 2``) of this logical dimension; ``None`` when unavailable.

    In 2D the only quadrature block is logical F1. In 3D the two quadrature blocks from inside
    to
    outside are logical **F2** (``acqu2s``) and **F1** (``acqu3s``) -- based on the
    ``F1PH(inner)/F2PH(outer)`` expansion of ``mc`` and TopSpin's ``td1/td2`` attribution, and
    checked one by one against the ``FnMODE`` family of 14 deposited pulse programs.
    """
    pairs = [block for block in acquisition_blocks(text) if block.is_pair]
    if not pairs:
        return None
    if ndim >= 3:
        index = {"F2": 0, "F1": 1}.get(logical_axis)
        return pairs[index] if index is not None and index < len(pairs) else None
    return pairs[0] if logical_axis == "F1" else None


def evolution_block(text: str | None, logical_axis: str, ndim: int) -> QuadratureBlock | None:
    """The **evolution block** of this logical dimension (a non-quadrature block such as
    ``times ST1CNT``); ``None`` when unavailable.

    It uses the same inside -> outside order as :func:`quadrature_block`: in 2D the block right
    after the quadrature block; in 3D, F2's evolution block follows F2's quadrature block and
    F1's
    follows F1's quadrature block.
    """
    blocks = acquisition_blocks(text)
    pairs = [i for i, block in enumerate(blocks) if block.is_pair]
    if not pairs:
        return None
    if ndim >= 3:
        index = {"F2": 0, "F1": 1}.get(logical_axis)
        if index is None or index >= len(pairs):
            return None
        follower = pairs[index] + 1
        return blocks[follower] if follower < len(blocks) else None
    if logical_axis != "F1":
        return None
    follower = pairs[0] + 1
    return blocks[follower] if follower < len(blocks) else None


def acquisition_family(text: str | None, logical_axis: str, ndim: int) -> str | None:
    """The acquisition family of this dimension (``F1PH``/``F1EA``/``F1QF``); ``None`` when it
    cannot be judged.

    The family comes from the pulse program itself (whether the quadrature block steps a
    gradient
    table = the E/A signature) and is cross-checked by the caller against that dimension's
    ``FnMODE`` from ``acquNs`` -- neither the family alone nor ``FnMODE`` alone is enough
    (manual
    table 11.1: one and the same ``F1PH`` switches between QSEQ/TPPI/States/States-TPPI via
    ``FnMODE``).
    """
    block = quadrature_block(text, logical_axis, ndim)
    if block is None:
        return None
    return FAMILY_F1EA if block.has_gradient else FAMILY_F1PH


def _family_for_fnmode(fnmode: int | None) -> str | None:
    if fnmode is None:
        return None
    if int(fnmode) == _EA_FNMODE:
        return FAMILY_F1EA
    if int(fnmode) == _QF_FNMODE:
        return FAMILY_F1QF
    if int(fnmode) in _PH_FNMODE:
        return FAMILY_F1PH
    return None


# --------------------------------------------------------------------------- #
# content verdict: look at the content **inside** the pulse program (which pulse the phase
# increment falls on, and whether that pulse is before or after the evolution period)
# --------------------------------------------------------------------------- #
#: A phase reference inside a pulse statement, e.g. ``(p13:sp2 ph4):f2`` /
#: ``(center (p1 ph2) (p21 ph5):f3 )``.
_PULSE_PHASE_RE = re.compile(r"\([^()]*\b(ph\d+)\b[^()]*\)")
_DELAY_TOKEN_RE = r"\b{name}\b"


def sequence_body(text: str | None) -> tuple[list[tuple[int, str]], int]:
    """The pulse-program body lines ``[(line number, text)]`` and the line number where the tail
    starts.

    The body = the code lines from the first statement line to the **last ``go=``**; comments,
    ``"..."`` relation lines and ``phN = ...`` phase definitions are skipped (they do not take
    part in judging whether a pulse is before/after the evolution period).
    """
    if not text:
        return [], 0
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if "go=" in line]
    body: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if stripped.startswith('"') or re.match(r"^ph\d+\s*=", stripped):
            continue
        if starts and index > starts[-1]:
            break
        body.append((index, stripped))
    return body, (starts[-1] if starts else len(lines))


def evolution_delays(text: str | None, logical_axis: str, ndim: int) -> tuple[str, ...]:
    """The names of the delays that are **incremented** for this logical dimension in the pulse
    program (the criterion for the evolution period).

    Both forms are read: for an unexpanded one the ``caldel(dN, +-inN)`` in the ``mc`` clause's
    second argument; for an expanded one the ``idN``/``ddN``/``rdN`` in that dimension's
    evolution
    block. When nothing is found it cannot be judged (the caller treats it as ``unknown``).
    """
    clause = mc_clause_for(text, logical_axis)
    if clause is not None:
        names = re.findall(r"caldel\s*\(\s*(d\d+)", clause.second_argument)
        if names:
            return tuple(dict.fromkeys(names))
    block = evolution_block(text, logical_axis, ndim)
    if block is not None:
        names = [
            f"d{match.group(1)}"
            for item in block.body
            for match in re.finditer(r"\b(?:i|r|d)d(\d+)\b", item)
        ]
        if names:
            return tuple(dict.fromkeys(names))
    return ()


def pulses_using_phase(text: str | None, phase: str) -> tuple[tuple[int, str], ...]:
    """The pulse lines ``[(line number, text)]`` in the pulse-program body that **use this phase
    program**.
    """
    body, _tail = sequence_body(text)
    found: list[tuple[int, str]] = []
    for index, line in body:
        if phase not in line:
            continue
        if any(match.group(1) == phase for match in _PULSE_PHASE_RE.finditer(line)):
            found.append((index, line))
    return tuple(found)


def _pulse_label(line: str, phase: str) -> str:
    """The pulse label carrying that phase on the pulse line (``p13``/``sp2`` ...); the first one
    found is returned.
    """
    label = re.search(r"\(([^()]*\b" + re.escape(phase) + r"\b[^()]*)\)", line)
    return label.group(1) if label else ""


def pulse_flip_angle_hint(comments: str, line: str, phase: str) -> int | None:
    """Read the flip angle of that pulse from the pulse program's **own parameter comments**:
    180 / 90 / None (the comment does not say).

    A comment looks like ``;p13: f2 channel - 90 degree shaped pulse`` or
    ``;sp2: f2 channel - shaped pulse 90 degree (C=O on resonance)``. It is only used to
    **veto**: when the comment explicitly says 180 degrees the pulse must not be judged by
    "creating/handing over coherence" (the ``dp`` of a 180 is not +-1).
    """
    if not comments:
        return None
    inside = _pulse_label(line, phase)
    labels = [token for token in re.findall(r"[A-Za-z]+\d*", inside)]
    for token in labels:
        for comment in comments.splitlines():
            if not comment.strip().startswith(";"):
                continue
            if not re.search(rf"\b{re.escape(token)}\b", comment):
                continue
            if re.search(r"\b180\b", comment):
                return 180
            if re.search(r"\b90\b", comment):
                return 90
    return None


def pulse_role_in_evolution(
    text: str | None, logical_axis: str, ndim: int, phase: str
) -> tuple[str, str]:
    """The position of the pulse carrying this phase program relative to this dimension's
    **evolution period** => the sign of ``dp``.

    Returns ``("create"|"handover"|"unknown", reason word)``:

    * ``create``: the single pulse falls **before the evolution delay** => it creates the
      coherence that enters the evolution period (target coherence 0 -> -1) => ``dp = -1`` =>
      ``qphase = +90`` => normal;
    * ``handover``: the single pulse falls **after the evolution delay** => it hands the
    coherence
      over (-1 -> 0) => ``dp = +1`` => ``qphase = -90`` => conjugated;
    * anything else (several phases stepped at once, the same phase used by several pulses, a
      pulse **inside** the evolution period, a comment that explicitly says 180 degrees, no
      evolution delay found) => cannot be judged -- always ``unknown`` + a reminder, never a
      guess.
    """
    body, _tail = sequence_body(text)
    if not body:
        return "unknown", "no_pulse_sequence_body"
    delays = evolution_delays(text, logical_axis, ndim)
    if not delays:
        return "unknown", "no_evolution_delay"
    region = [
        index
        for index, line in body
        for name in delays
        if re.search(_DELAY_TOKEN_RE.format(name=re.escape(name)), line)
    ]
    if not region:
        return "unknown", "evolution_delay_not_used"
    low, high = min(region), max(region)
    pulses = pulses_using_phase(text, phase)
    if len(pulses) > 1:
        return "unknown", "phase_used_by_several_pulses"
    if not pulses:
        return "unknown", "phase_not_in_any_pulse"
    index, line = pulses[0]
    if pulse_flip_angle_hint(text, line, phase) == 180:
        return "unknown", "stepped_pulse_is_180"
    if index < low:
        return "create", ""
    if index > high:
        return "handover", ""
    return "unknown", "stepped_pulse_inside_evolution"


def canonical_negated(mode_keyword: str | None) -> bool:
    """Whether this MODE keyword is a canonical "negation of imaginaries" variant (``-N``)."""
    return str(mode_keyword or "") in NEGATED_MODE_KEYWORDS


# --------------------------------------------------------------------------- #
# verdict
# --------------------------------------------------------------------------- #
def _fnmode_of(experiment: Experiment, logical_axis: str) -> int | None:
    """The ``FnMODE`` of this logical dimension (3D: F1<->acqu3s, F2<->acqu2s; 2D:
    F1<->acqu2s).
    """
    if experiment.ndim >= 3:
        filename = {"F3": "acqus", "F2": "acqu2s", "F1": "acqu3s"}.get(logical_axis)
    else:
        filename = {"F2": "acqus", "F1": "acqu2s"}.get(logical_axis)
    if not filename:
        return None
    block = experiment.acquisition_parameters.get(filename) or {}
    raw = block.get("FnMODE")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def read_pulseprogram(experiment: Experiment, data_dir: Path | str | None = None) -> str | None:
    """Read this dataset's ``pulseprogram`` (the expanded pulse program); ``None`` when it is
    absent or unreadable.
    """
    candidates: list[Path] = []
    if data_dir is not None:
        candidates.append(Path(data_dir))
    source = getattr(experiment, "source_path", None)
    if source:
        candidates.append(Path(source))
    candidates += [Path(segment) for segment in (experiment.segments or [])]
    for directory in candidates:
        try:
            path = directory / "pulseprogram"
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return None


@dataclass(frozen=True)
class Handedness:
    """The quadrature handedness verdict for one dimension (including the facts read by Layer
    A).
    """

    axis: str
    handedness: str  #: normal / conjugated / unknown
    sequence: str | None = None
    family: str | None = None
    fnmode: int | None = None
    qphase_deg: float | None = None
    reason: str = ""  #: the reason word when unknown (see ``review_reason_text``)
    source: str = ""
    #: the facts read by Layer A (recorded truthfully even when the verdict is unknown, for manual
    #: review and later pathway solving)
    stepped_phases: tuple[str, ...] = ()
    quadrature_phase_deg: float | None = None  #: dphi of the stepped phase (degrees)
    pulse_role: str = ""  #: create / handover / unknown (position vs the evolution period)

    @property
    def determined(self) -> bool:
        return self.handedness in (HANDEDNESS_NORMAL, HANDEDNESS_CONJUGATED)

    @property
    def needs_neg(self) -> bool:
        """Whether to apply ``FT -neg``: it is added only for ``conjugated`` (False while the
        pathway is unsolved).
        """
        return self.handedness == HANDEDNESS_CONJUGATED


def _normalise(degrees: float) -> float:
    """Normalise to (-180, 180]."""
    value = float(degrees) % 360.0
    if value > 180.0:
        value -= 360.0
    return value


def handedness_for(
    experiment: Experiment,
    logical_axis: str,
    *,
    fnmode: int | None = None,
    data_dir: Path | str | None = None,
) -> Handedness:
    """Judge the quadrature handedness of this indirect dimension (normal / conjugated /
    unknown).

    The criterion is in the module docs: ``qphase = -sum dp*dphi - dphi_receiver``; once
    normalised, ``+90`` => ``normal`` and ``-90`` => ``conjugated``; ``F1EA``, an unlisted
    sequence, a missing pulse program, a family clash with ``FnMODE``, or a value that cannot be
    normalised => ``unknown`` (**not added by default**).
    """
    axis = str(logical_axis)
    dimension = next((dim for dim in experiment.dimensions if dim.logical_axis == axis), None)
    if dimension is None or dimension.role is AxisRole.DIRECT:
        return Handedness(axis=axis, handedness=HANDEDNESS_UNKNOWN, reason="not_indirect")
    ndim = int(experiment.ndim)
    resolved = _fnmode_of(experiment, axis) if fnmode is None else int(fnmode)
    known_fnmode = resolved if resolved is not None and 0 < resolved <= 6 else None
    expected_family = _family_for_fnmode(known_fnmode)

    text = read_pulseprogram(experiment, data_dir)
    if text is None:
        return Handedness(
            axis=axis,
            handedness=HANDEDNESS_UNKNOWN,
            fnmode=known_fnmode,
            reason="no_pulseprogram",
        )
    sequence = pulse_sequence_name(text)
    family = acquisition_family(text, axis, ndim)
    clause = mc_clause_for(text, axis)
    if family is None and clause is not None:
        # unexpanded pulse program (only `mc` clauses, no `lo to` blocks): both the family and the
        # quadrature increment come from the clause
        family = clause.family
    base = Handedness(
        axis=axis,
        handedness=HANDEDNESS_UNKNOWN,
        sequence=sequence,
        family=family,
        fnmode=known_fnmode,
    )
    if expected_family is not None and family is not None and expected_family != family:
        # the pulse program's loop shape and acquNs FnMODE do not tell the same story (e.g.
        # FnMODE=5 with a gradient/alternation block) => take no side and apply no sign
        # adjustment; remind the user to review by hand.
        return replace(base, reason="family_conflict")
    if expected_family == FAMILY_F1QF or family == FAMILY_F1QF:
        # QF (FnMODE=1) has a real part only and no quadrature pair.
        return replace(base, reason="f1qf")
    if family == FAMILY_F1EA or known_fnmode == _EA_FNMODE:
        return replace(base, reason="f1ea")
    if known_fnmode is None:
        return replace(base, reason="undefined_fnmode")
    annotation = (PULSE_PATHWAY_ANNOTATIONS.get(sequence or "") or {}).get(axis)
    block = quadrature_block(text, axis, ndim)
    increments = block.increments if block is not None else ()
    if not increments and clause is not None:
        # unexpanded form: the first argument of the `mc` clause is the only source of the
        # quadrature increment (manual §11.2)
        increments = clause.increments
    if not increments:
        return replace(base, reason="no_quadrature_pair" if family is None else "no_increment")
    if annotation is not None and annotation.get("steps"):
        # (1) **explicit annotations take precedence**: use them when the sequence/axis is in the
        #     table and has dp steps (settled by literature or data)
        return _annotated_verdict(text, base, annotation, increments)
    # (2) otherwise **look at the content inside the pulse program**: which pulse the stepped
    #     phase falls on and whether that pulse is before or after the evolution period
    #     (creating coherence => dp=-1 => +90 normal; handing coherence over => dp=+1 => -90
    #     conjugated). The sequence name is not looked at => renamed/revised/unregistered
    #     sequences can be judged just as well.
    return _content_verdict(text, base, increments, axis, ndim)


def _annotated_verdict(
    text: str,
    base: Handedness,
    annotation: dict[str, Any],
    increments: tuple[PhaseIncrement, ...],
) -> Handedness:
    """Compute ``qphase`` from the explicit annotation's ``steps`` (the per-phase dp)."""
    deltas = {str(step["phase"]): int(step["dp"]) for step in annotation["steps"]}
    qphase = 0.0
    for increment in increments:
        if increment.phase not in deltas:
            return replace(base, reason="unannotated_phase")
        divisor = phase_program_divisor(text, increment.phase)
        delta_deg = replace(increment, divisor=divisor).delta_deg
        if delta_deg is None:
            # floating-point phase table (the increment is ip's second argument) => no guessing
            return replace(base, reason="unsupported_phase_program")
        qphase -= deltas[increment.phase] * delta_deg
    receiver = str(annotation.get("receiver") or "")
    if receiver:
        for increment in increments:
            if increment.phase == receiver:
                # the receiver phase's dphi enters the formula separately (it has no dp; it is the
                # reference phase)
                delta_deg = replace(
                    increment, divisor=phase_program_divisor(text, increment.phase)
                ).delta_deg
                if delta_deg is None:
                    return replace(base, reason="unsupported_phase_program")
                qphase -= delta_deg
    return _verdict_from_qphase(replace(base, source=str(annotation.get("source") or "")), qphase)


def _content_verdict(
    text: str,
    base: Handedness,
    increments: tuple[PhaseIncrement, ...],
    axis: str,
    ndim: int,
) -> Handedness:
    """What **Layer A (pulse-sequence physics)** can conclude: it reports facts only and never
    guesses a sign.

    What can be established is "which phase program this dimension's quadrature increment falls
    on, how large dphi is, the acquisition family, and the shape of that pulse"; a **determined
    handedness needs the ``p`` of the coherence pathway selected by the phase cycle**, which is
    a
    pathway-solving problem (see the "three-layer architecture" in the module docs and
    ``docs/backend/fid_com_parameter_sources.md`` §9) that this module **has not solved yet**,
    so
    it always returns ``unknown`` + ``pathway_not_solved`` (user, 2026-09-25: "do not force a
    neg
    on it"; truth samples are used for verification only, never as evidence).
    """
    facts = {
        "stepped_phases": tuple(increment.phase for increment in increments),
        "quadrature_phase_deg": None,
        "pulse_role": "",
    }
    if len(increments) == 1:
        increment = increments[0]
        delta_deg = replace(
            increment, divisor=phase_program_divisor(text, increment.phase)
        ).delta_deg
        facts["quadrature_phase_deg"] = delta_deg
        role, _why = pulse_role_in_evolution(text, axis, ndim, increment.phase)
        facts["pulse_role"] = role
        pulses = pulses_using_phase(text, increment.phase)
        if len(pulses) > 1:
            return replace(base, reason="phase_used_by_several_pulses", **facts)
        if not pulses:
            return replace(base, reason="phase_not_in_any_pulse", **facts)
        if pulse_flip_angle_hint(text, pulses[0][1], increment.phase) == 180:
            return replace(base, reason="stepped_pulse_is_180", **facts)
        if delta_deg is None:
            return replace(base, reason="unsupported_phase_program", **facts)
        return replace(
            base,
            reason="pathway_not_solved",
            source=tr(
                "read from the pulse program (stepped phase {p0}, dphi={p1:g} deg, {p2} pulse); "
                "the coherence pathway selected by the phase cycle is not solved yet",
                p0=increment.phase,
                p1=delta_deg,
                p2=role,
            ),
            **facts,
        )
    return replace(
        base,
        reason="multiple_stepped_phases",
        source=tr(
            "read from the pulse program (stepped phases {p0}); the per-phase coherence-order "
            "change is not unique",
            p0=", ".join(facts["stepped_phases"]),
        ),
        **facts,
    )


def _verdict_from_qphase(base: Handedness, qphase: float) -> Handedness:
    """``qphase`` => normal / conjugated / ambiguous (anything outside +-90 cannot be judged)."""
    qphase = _normalise(qphase)
    determined = replace(base, qphase_deg=qphase)
    if abs(qphase - 90.0) < 1e-6:
        return replace(determined, handedness=HANDEDNESS_NORMAL)
    if abs(qphase + 90.0) < 1e-6:
        return replace(determined, handedness=HANDEDNESS_CONJUGATED)
    return replace(determined, reason="ambiguous_qphase")


# --------------------------------------------------------------------------- #
# the same wording in three places (log / step report / import warning)
# --------------------------------------------------------------------------- #
#: Axis letters: the same convention as ``bruker_workflow._axis_letters`` (F3/F2/F1 -> x/y/z).
_AXIS_LETTERS = {
    2: {"F2": "x", "F1": "y"},
    3: {"F3": "x", "F2": "y", "F1": "z"},
}


def axis_letter(ndim: int, logical_axis: str) -> str:
    """Logical axis -> the letter axis of fid.com/FT (x/y/z)."""
    return _AXIS_LETTERS.get(int(ndim), {}).get(logical_axis, logical_axis.lower())


def review_reason_text(result: Handedness) -> str:
    """One sentence explaining the ``unknown`` reason (an English source string rendered by
    ``tr``).
    """
    reason = result.reason
    if reason == "no_pulseprogram":
        return tr("there is no pulse program for this dataset")
    if reason == "f1ea":
        return tr(
            "the pulse program uses the echo/anti-echo clause (F1EA); the handedness after "
            "the shuffle depends on the encoding"
        )
    if reason == "undefined_fnmode":
        return tr("acquNs FnMODE is undefined (0)")
    if reason == "f1qf":
        return tr("the acquisition is phase insensitive (F1QF/QF): there is no quadrature pair")
    if reason == "family_conflict":
        return tr("the pulse program loop and acquNs FnMODE={p0} disagree", p0=result.fnmode)
    if reason == "unknown_sequence":
        return tr(
            "the pulse program {p0} is not in the coherence-pathway table", p0=result.sequence
        )
    if reason == "unannotated_phase":
        return tr("a stepped phase program of this sequence is not annotated")
    if reason == "no_increment":
        return tr("no quadrature phase increment found in the pulse program")
    if reason == "no_quadrature_pair":
        return tr("no quadrature loop (times 2) found for this dimension")
    if reason == "ambiguous_qphase":
        return tr(
            "the coherence-pathway phase does not normalise to +-90 degrees (qphase={p0:g})",
            p0=result.qphase_deg,
        )
    if reason == "unsupported_phase_program":
        return tr(
            "a stepped phase program uses a floating-point phase list, where the increment is "
            "given as the second argument of the ip statement"
        )
    if reason == "multiple_stepped_phases":
        return tr(
            "the pulse program steps more than one phase program for this dimension, so the "
            "coherence-order change per phase is not unique"
        )
    if reason == "phase_used_by_several_pulses":
        return tr("the stepped phase program is used by several pulses")
    if reason == "phase_not_in_any_pulse":
        return tr("the stepped phase program is not used by any pulse")
    if reason == "stepped_pulse_is_180":
        return tr("the stepped pulse is a 180 degree pulse (its coherence-order change is not +-1)")
    if reason == "no_evidence_for_2d_states":
        return tr(
            "no real Data has been verified for a 2D States/States-TPPI indirect dimension yet, "
            "so the sign of the target coherence order is not established"
        )
    if reason == "pathway_not_solved":
        return tr(
            "the quadrature facts were read from the pulse program, but the coherence pathway "
            "selected by the phase cycle has not been solved (so the sign of the evolving "
            "coherence order is not established)"
        )
    return tr("the acquisition mode/sign of this dimension was not confirmed")


def review_line(result: Handedness, ndim: int) -> str:
    """One reminder line (the same sentence in ``format_fid_step_report`` / the import warning /
    the log).

    Finalised by the user on 2026-09-25 for the second time ("we let the user check it
    themselves
    anyway, so use the rule with the lowest error probability"): the automatic criterion only
    gets
    it right **as far as it can** (``acquisition_encoding.simple_neg_rule``); a dimension it
    cannot judge is reported truthfully as "no ``-neg`` added automatically" plus **where to
    decide** -- the indirect-dimension flip control of the spectrum step (``sampling.flip_f1`` /
    ``flip_f2``). The verdict facts in ``result`` are still carried along for comparison.
    """
    letter = axis_letter(ndim, result.axis)
    return tr(
        "{p0}MODE ({p1}): no FT -neg was applied automatically ({p2}); first look at the "
        "spectrum: if this dimension's peaks come out mirrored relative to what you expect, "
        "add the flip with the indirect-dimension flip control in the spectrum step (it "
        "re-applies the matching phase automatically)",
        p0=letter,
        p1=result.axis,
        p2=review_reason_text(result),
    )


def neg_applied_line(decision: Any, ndim: int) -> str:
    """The notice line for an automatically **applied** ``-neg`` (the same wording as
    :func:`review_line` in the log/report/import).

    A sign change must be told to the user (user, 2026-09-25: the spectrum is checked by hand in
    the end) together with a way to undo it -- the indirect-dimension flip control of the
    spectrum
    step (untick it / re-select the same entry). The criterion and reason code stay auditable in
    ``mode_symbol_audit()["dims"]``.
    """
    letter = axis_letter(ndim, decision.axis)
    return tr(
        "{p0}MODE ({p1}): FT -neg was applied automatically; check that this "
        "indirect dimension's direction is correct - if it is not, choose "
        "the indirect-dimension flip after running the spectrum "
        "generation and run the script again",
        p0=letter,
        p1=decision.axis,
    )


def is_neg_notice(line: str) -> bool:
    """Return whether a line is an automatic ``-neg`` notice or reminder.

    These notices appear in the import log, the FID-generation report and the conversion log.
    They should not also open a popup, which would interrupt the user with duplicate
    information.
    The GUI uses this predicate to omit the notices from popups while keeping them in logs and
    reports.

    Identify the line by its source format, not merely by the presence of ``-neg``: the stable
    prefix is ``<axis letter>MODE (<axis name>):`` (for example, ``xMODE (F2):`` or
    ``yMODE (F1):``). No other report line uses this form. The notice text is produced in this
    module, so keeping the predicate beside its source prevents a separate GUI regular
    expression
    from silently drifting.
    """
    text = str(line or "").lstrip()
    return bool(re.match(r"^[xyz]MODE \([^)]+\):", text))


def _neg_notice_lines(
    experiment: Experiment,
    order: list[str],
    by_axis: dict[str, Handedness],
    *,
    data_dir: Path | str | None = None,
) -> list[str]:
    """The mode/sign notice lines: ``-neg`` was applied automatically (notice + undo entry) or it
    could not be judged (reminder for the user to decide).

    Only the ``NEG_ADD`` / ``NEG_ASK`` conclusions appear here; ``NEG_NONE`` (the E/A family,
    the
    second indirect dimension of a 3D) is a **definite conclusion** of the criterion and no
    longer
    disturbs the user.
    """
    from core.experiment.acquisition_encoding import (
        NEG_ADD,
        NEG_ASK,
        ft_neg_decision,
    )

    ndim = int(experiment.ndim)
    lines: list[str] = []
    for axis in order:
        if axis not in by_axis:
            continue
        decision = ft_neg_decision(experiment, axis, data_dir=data_dir)
        if decision.decision == NEG_ADD:
            lines.append(neg_applied_line(decision, ndim))
        elif decision.decision == NEG_ASK:
            lines.append(review_line(by_axis[axis], ndim))
    return lines


def handedness_by_axis(
    experiment: Experiment, *, data_dir: Path | str | None = None
) -> dict[str, Handedness]:
    """Judge every **indirect dimension** (key = logical axis F1/F2)."""
    results: dict[str, Handedness] = {}
    for dimension in experiment.dimensions:
        if dimension.role is AxisRole.DIRECT:
            continue
        results[dimension.logical_axis] = handedness_for(
            experiment, dimension.logical_axis, data_dir=data_dir
        )
    return results


def review_lines(experiment: Experiment, *, data_dir: Path | str | None = None) -> list[str]:
    """The reminder lines for all dimensions that need a notice/review (in the F2->F1 fid.com
    axis order).

    After the second finalisation on 2026-09-25 there are two kinds: dimensions where the
    automatic criterion **added** `-neg` (notice + undo entry) and dimensions it **cannot
    judge**
    (``ask_user``, not added by default); a definite "do not add" no longer produces a reminder.
    """
    ndim = int(experiment.ndim)
    order = ["F2", "F1"] if ndim >= 3 else ["F1"]
    by_axis = handedness_by_axis(experiment, data_dir=data_dir)
    return _neg_notice_lines(experiment, order, by_axis, data_dir=data_dir)


def mode_symbol_audit(
    experiment: Experiment, *, data_dir: Path | str | None = None
) -> dict[str, Any]:
    """The "mode/sign" audit record of this dataset (carried into ``mode_symbol`` of
    ``*.fid.conversion.json``).

    The same approach as the spectral width/carrier frequency: a single source produces
    **lines**
    here (the reminder text, the same in the log/report/import) plus **dims** (the per-dimension
    verdict details and the automatic criterion's conclusion, for later review).
    """
    from core.experiment.acquisition_encoding import ft_neg_decision

    ndim = int(experiment.ndim)
    order = ["F2", "F1"] if ndim >= 3 else ["F1"]
    by_axis = handedness_by_axis(experiment, data_dir=data_dir)
    dims = [
        {
            "axis": axis_letter(ndim, axis),
            "logical_axis": axis,
            "handedness": by_axis[axis].handedness,
            "fnmode": by_axis[axis].fnmode,
            "sequence": by_axis[axis].sequence,
            "family": by_axis[axis].family,
            "qphase_deg": by_axis[axis].qphase_deg,
            "reason": by_axis[axis].reason,
            "source": by_axis[axis].source,
            # the facts read by Layer A (recorded even when the verdict is unknown, for manual
            # review / later pathway solving)
            "stepped_phases": list(by_axis[axis].stepped_phases),
            "quadrature_phase_deg": by_axis[axis].quadrature_phase_deg,
            "pulse_role": by_axis[axis].pulse_role,
            # finalised again on 2026-09-25: the automatic criterion's **conclusion** is recorded
            # too (add / none / ask_user + the basis + the reason code), so a report or a
            # third-party review no longer has to guess "did the software add -neg or not"
            "neg_decision": decision.decision,
            "neg_basis": decision.basis,
            "neg_reason": decision.reason,
            "neg_applied": bool(decision.ft_neg),
        }
        for axis in order
        if axis in by_axis
        for decision in [ft_neg_decision(experiment, axis, data_dir=data_dir)]
    ]
    return {
        "lines": _neg_notice_lines(experiment, order, by_axis, data_dir=data_dir),
        "dims": dims,
    }


__all__ = [
    "FAMILY_F1EA",
    "FAMILY_F1PH",
    "FAMILY_F1QF",
    "HANDEDNESS_CONJUGATED",
    "HANDEDNESS_NORMAL",
    "HANDEDNESS_UNKNOWN",
    "DEFAULT_PHASE_DIVISOR",
    "NEGATED_MODE_KEYWORDS",
    "PULSE_PATHWAY_ANNOTATIONS",
    "Handedness",
    "McClause",
    "PhaseIncrement",
    "QuadratureBlock",
    "acquisition_blocks",
    "acquisition_family",
    "axis_letter",
    "canonical_negated",
    "handedness_by_axis",
    "handedness_for",
    "is_neg_notice",
    "mc_clause_for",
    "mc_clauses",
    "mode_symbol_audit",
    "pulse_sequence_name",
    "quadrature_block",
    "read_pulseprogram",
    "review_line",
    "review_lines",
    "review_reason_text",
    "phase_program_divisor",
]
