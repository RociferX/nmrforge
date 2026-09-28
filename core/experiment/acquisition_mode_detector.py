"""Acquisition-mode detection: States / States-TPPI / Echo-Antiecho / QF / magnitude.

Decided from FnMODE/FnTYPE/AQ_mod and reported as a per-dimension acquisition_mode, which the
FT flags (-alt/-neg), the flip and the phase handling build on (framework §6.1).
"""

from __future__ import annotations

from typing import Any

from core.data.internal_data_model import Experiment
from ui_support.i18n import tr

# official Bruker TopSpin FnMODE enum (the nmrglue/acquNs implementation, matching the lab
# Acqua): 0 = undefined, 1 = QF (magnitude), 2 = QSEQ (magnitude), 3 = TPPI (real),
# 4 = States, 5 = States-TPPI, 6 = Echo-Antiecho
_FNMODE_TO_MODE = {
    0: "States",  # Bruker acqus defaults the direct dimension to 0 (placeholder, kept for output)
    1: "Magnitude",  # QF
    2: "Magnitude",  # QSEQ
    3: "TPPI",
    4: "States",
    5: "States-TPPI",
    6: "Echo-Antiecho",
}

# official bruk2pipe ACQUISITION MODES table (see nmrPipe/format and bruk2pipe -help):
#   States/DQD/Complex -> no FT flag; States-TPPI -> FT -alt;
#   States-N/Complex-N/States-TPPI-N -> add -neg; TPPI -> FT -real;
#   Sequential/QSEQ -> FT -alt (together with -real, equivalent to FT -bruk);
#   Echo-Antiecho -> the shuffling happens during conversion, no FT flag needed (and -alt
#   must **not** be added mechanically just because alternating gradients are seen, user
#   2026-09-24).
# Indirect FnMODEs that need alternation: States-TPPI (=5) gives -alt directly;
# Sequential/QSEQ (=2) is handled by ``FT -bruk`` (= -alt -real), so the semantic set is
# {2, 5} but it appears in only one place in the script; TPPI (=3) is -real rather than -alt.
# The NMRPipe real processing path for the real/magnitude families (1/2/3) is not implemented
# and the entry points report it explicitly (see unsupported_real_mode_error).
_FNMODE_FT_ALT = {2, 5}

# real/magnitude indirect dimensions (QF/QSEQ/TPPI): uniform processing supports them
# (-yMODE Real/TPPI/Sequential + FT -real/-bruk/MC, see FT_KIND); SMILE reconstruction only
# supports complex encoding (States/States-TPPI/E-A) and the NUS entry points reject the real
# ones explicitly (see unsupported_real_mode_error).
_REAL_FNMODE = {1, 2, 3}

# FnMODE -> NMRPipe processing shape:
#   complex:    FT [-neg] [-alt] (the current Complex family)
#   magnitude:  FT + MC (QF, aq2D Magnitude)
#   sequential: FT -bruk (= -alt -real, QSEQ/Sequential detected directly)
#   tppi:       FT -real (phase-sensitive TPPI)
FT_KIND = {
    0: "complex",
    1: "magnitude",
    2: "sequential",
    3: "tppi",
    4: "complex",
    5: "complex",
    6: "complex",
}


def ft_kind_for(fnmode: int) -> str:
    """The NMRPipe FT processing shape of an FnMODE (complex/magnitude/sequential/tppi)."""
    return FT_KIND.get(fnmode, "complex")


#: real-family FT processing shapes: the indirect dimension holds the real part only, so
#: ``-yT/-zT`` takes ``N`` instead of ``N/2``.
_REAL_KINDS = ("magnitude", "tppi", "sequential")


def is_real_kind(fnmode: int) -> bool:
    """Whether the indirect dimension of this FnMODE is a real family (QF/QSEQ/TPPI): ``T = N``."""
    return ft_kind_for(fnmode) in _REAL_KINDS


#: Known upper bound of the FnMODE table (0..6). ``7`` is outside it: ``com/pprog.tcl`` treats
#: it as ``Real``, but real instruments carry no such data, so this module **does not claim** a
#: conversion mode for it (``expected_values`` gives no MODE target and ``mode_audit`` marks it
#: unconfirmed) and conservatively handles its hypercomplex components as complex. The three
#: "hypercomplex tables" used to be written out separately (``{0,1,2,4,5,6}`` / ``{0,4,5,6}`` /
#: ``_REAL_FNMODE``); they now all go through :func:`hypercomplex_mult` (2026-09-24 re-check D).
KNOWN_FNMODE_MAX = 6


def hypercomplex_mult(fnmode: int) -> int:
    """Hypercomplex component count of this FnMODE (real families = 1, complex = 2); the sole
    source for the whole project."""
    return 1 if is_real_kind(fnmode) else 2


def time_domain_points(fnmode: int, points: int) -> int:
    """The `-yT/-zT` value of this FnMODE (the rule from NMRPipe `com/nih.tcl`).

    Rule (single source): the ``Real``/``Sequential`` families -> ``T = N`` (real data is not
    halved); the complex families (States/States-TPPI/E-A) -> ``T = N/2``. Previously
    `expected_values` always halved while `script_generator` did not for the real families, so
    the same experiment came out with an indirect-dimension size off by 2x depending on whether
    it went through the AUTO patch or the fallback script (2026-09-24 re-check C1). Both places
    now call this function.
    """
    value = int(points)
    return value if is_real_kind(fnmode) else value // 2


#: FnMODE -> bruk2pipe conversion keyword (``-yMODE``/``-zMODE``). **Write the concrete
#: keyword** (user 2026-09-24, second revision): collapsing everything into ``Complex``
#: swallows the information carried by ``-N`` (imaginary part negation) and loses the
#: TPPI/Sequential/States distinction. The bruk2pipe ``-xMODE`` keyword table
#: (``~/pipe/format/parsehdr.c`` modeList plus ``bruk2pipe -help``):
#: ``0=Complex,States,Complex-N,States-N,States-TPPI,States-TPPI-N`` /
#: ``1=Real,TPPI`` / ``2=Sequential,Bruker`` / ``4=Echo-AntiEcho``.
#: **No sign adjustment during conversion**: ``-xMODE`` only writes header fields
#: (``FDF1QUADFLAG``/``FDF1AQSIGN``); the actual alternation/negation happens at processing
#: time in ``nmrPipe -fn FT -alt/-neg`` (``nmruser/userproc.c uFT()``). So writing a concrete
#: keyword here and adding FT flags at processing time will not "alternate twice".
_BRUK2PIPE_MODE_BY_FNMODE = {
    0: "Complex",  # Bruker undefined / direct-dimension placeholder
    1: "Real",  # QF (magnitude, the real part only)
    2: "Sequential",  # QSEQ (Bruker sequential)
    3: "TPPI",  # phase-sensitive real TPPI
    4: "States",
    5: "States-TPPI",
    6: "Echo-AntiEcho",  # y dimension only (bruk2pipe does the shuffling in y)
}

#: Direct-dimension conversion keyword (bruk2pipe -xMODE): always DQD in Bruker
DIRECT_BRUK2PIPE_MODE = "DQD"

#: bruk2pipe ``-xMODE`` keyword -> mode number (copied from the ``bruk2pipe`` help; the binary
#: knows ``Echo-AntiEcho[-N]`` as well). **The same number is not the same string**:
#: ``States-TPPI`` and ``Complex`` are both 0, but the former makes bruk2pipe do complex
#: alternation (ALT=2) while the latter does not (ALT=0) -- two different things.
#: 2026-09-24, while validating against a public deposition script: the depositor writes
#: ``States-TPPI`` for the z dimension of FnMODE=5, and the old implementation rewrote it to
#: ``Complex`` because the strings differed -- the wording of the data owner must not be
#: silently changed.
BRUK2PIPE_MODE_CODES: dict[str, int] = {
    "Complex": 0,
    "States": 0,
    "Complex-N": 0,
    "States-N": 0,
    "States-TPPI": 0,
    "States-TPPI-N": 0,
    "Real": 1,
    "TPPI": 1,
    "Sequential": 2,
    "Bruker": 2,
    "DQD": 3,
    "Echo-AntiEcho": 4,
    "Echo-AntiEcho-N": 4,
    "Rance-Kay": 4,
}


def same_mode_family(first: str | None, second: str | None) -> bool:
    """Whether two MODE keywords share the same **mode number** (family); unknown keywords
    return False (treated as different)."""
    if first is None or second is None:
        return False
    if str(first) == str(second):
        return True
    a = BRUK2PIPE_MODE_CODES.get(str(first))
    b = BRUK2PIPE_MODE_CODES.get(str(second))
    return a is not None and a == b


def bruk2pipe_mode_for(fnmode: int, *, axis: str = "y") -> str:
    """FnMODE -> bruk2pipe conversion keyword (``-xMODE``/``-yMODE``/``-zMODE``).

    Single source: ``backend.bruker_workflow.expected_values`` (fid.com cross-check and
    patch, acqus is authoritative) and ``backend.script_generator`` (fallback conversion
    script) both call this function. Before the 2026-09-23 merge the two places each had
    their own table and they disagreed: bruker_workflow mapped FnMODE=4 and 6 to
    ``Echo-AntiEcho`` and 3/2/1 to ``Complex``, so it rewrote the fid.com that
    ``bruker -AUTO`` had written correctly (datasets with FnMODE 1/2/3/4).

    2026-09-24, second revision: **write the concrete keyword** (0 -> ``Complex``, 1 ->
    ``Real``, 2 -> ``Sequential``, 3 -> ``TPPI``, 4 -> ``States``, 5 -> ``States-TPPI``, 6 ->
    ``Echo-AntiEcho``) instead of collapsing everything into ``Complex`` -- the depositor's
    ``States-TPPI`` and the ``-N`` variants carry an "imaginary part negated" meaning that must
    not be swallowed silently. ``patch_fid_com`` likewise no longer rewrites a same-mode-number
    spelling into ``Complex`` (it only reports it).

    With ``axis="z"`` the result is never ``Echo-AntiEcho``: the E-A shuffling happens
    in the y dimension of bruk2pipe only (see the z branch of script_generator), so z
    always stays ``Complex``.
    """
    try:
        value = int(fnmode)
    except (TypeError, ValueError):
        return "Complex"
    if value == 6 and axis == "z":
        return "Complex"
    return _BRUK2PIPE_MODE_BY_FNMODE.get(value, "Complex")


def unsupported_real_mode_error(fnmode: int, *, logical_axis: str) -> str:
    """Rejection text for a real indirect dimension on the SMILE (NUS) path.

    Uniform processing already supports the real modes; this is limited to SMILE
    reconstruction, where the real/magnitude encodings (TPPI/QSEQ/QF) carry no quadrature
    phase information and the Bruker NUS sampler never produces such data (in practice the
    FnMODE is always States-TPPI/Echo-Antiecho).
    """
    name = _FNMODE_TO_MODE.get(fnmode, f"unknown({fnmode})")
    return (
        tr(
            "dimension {p0} acquisition mode FnMODE={p1}({p2}) is real/magnitude, while NUS/SMILE "
            "reconstruction only supports complex encoding(States/States-TPPI/Echo-Antiecho); the "
            "uniform sampling path supports this "
            "mode",
            p0=logical_axis,
            p1=fnmode,
            p2=name,
        )
    )

#: The FT ``-alt`` set lives in ``_FNMODE_FT_ALT`` at the top of the file (single source, not
#: repeated here).


def ft_alt_for(fnmode: int) -> bool:
    """Whether this FnMODE needs alternation (``-alt``; ``FnMODE=2`` is handled by FT -bruk)."""
    return fnmode in _FNMODE_FT_ALT


#: Switch for the automatic `-neg` criterion (user 2026-09-25, second revision).
#:
#: **The default ``True`` = use the simple criterion with the lowest error probability** (see
#: :func:`core.experiment.acquisition_encoding.simple_neg_rule`): an already-decided layer A or
#: a canonical ``-N`` wins; otherwise the **NMRPipe ``y`` dimension** of a 3D (recognised from
#: ``AQSEQ``, not hard-wired to the logical F2) with the States family => add ``-neg``; the
#: ``z`` dimension of a 3D / the E/A family => do not add it; everything else (2D States,
#: QF/Real, a missing ``FnMODE``, an undecidable ``AQSEQ``, a family conflict) =>
#: **``ask_user``**: no ``-neg`` plus the same reminder in the log, the report and the import,
#: leaving it to the user to decide with the "indirect-dimension flip" control in the spectrum
#: step (the spectrum is checked by hand in the end anyway).
#: The evidence set and the sources of this empirical criterion are in
#: ``docs/backend/fid_com_parameter_sources.md`` §8.2/§8.3 (lab 3D data plus the data owner's
#: own script: ``-neg`` lands on the first indirect dimension only; the two 3D NUS datasets
#: compared word by word).
#:
#: Setting it to ``False`` = **fully manual** (the earlier C plan of 2026-09-25): the automatic
#: decision leaves the chain entirely, ``-neg`` is never added and everything relies on the flip
#: control in the spectrum step. The decision implementation (Layer A/B/C, the annotation
#: table, ``simple_neg_rule``) is always kept.
AUTO_NEG_JUDGEMENT = True


def ft_neg_for(experiment: Experiment, fnmode: int, logical_axis: str) -> bool:
    """Whether the FT of this dimension needs ``-neg`` (imaginary part negation).

    With ``AUTO_NEG_JUDGEMENT`` on (the default) the full decision chain runs: an already
    decided layer A / a fid.com that already is a canonical ``-N`` variant => adopt it;
    **otherwise the simple criterion with the lowest error probability is used** (the ``y``
    dimension of a 3D plus the States family => add it; the ``z`` dimension / E-A => do not;
    an undecidable case => do not add it plus the same reminder in all three places). With the
    switch off it is always ``False`` (fully manual).

    The direct dimension and everything that is not an indirect dimension never get it -- only
    indirect dimensions have a quadrature-handedness concept.

    Call sites (the single assembly point is still
    :func:`backend.script_generator._ft_flags`): the F2/F1 FT lines and the SMILE direction
    flag of ``backend/script_generator``, and the per-axis FT nodes of
    ``core/planning/method_selector``.
    """
    from core.data.internal_data_model import AxisRole
    from core.experiment.acquisition_encoding import ft_neg_decision

    if experiment.ndim < 2 or not AUTO_NEG_JUDGEMENT:
        # C plan: the automatic decision is disabled => never add it; the flip follows only the
        # user's sampling.flip_f1/flip_f2 choice
        return False
    dimension = next(
        (
            dim
            for dim in experiment.dimensions
            if dim.logical_axis == logical_axis
        ),
        None,
    )
    if dimension is None or dimension.role is AxisRole.DIRECT:
        return False
    return ft_neg_decision(experiment, logical_axis, fnmode=fnmode).ft_neg


#: Sampling flags that affect the FT **sign/direction** (per axis they decide ``-neg``, and
#: globally ``-alt``/``ft_neg``). Single source: the backend script assembly reads it, and
#: reference/derived runs must carry it through as a whole (user 2026-09-25: a choice made by
#: hand when the API builds the reference has to reach every step; combination mode must not
#: treat it as a sweep axis).
#: ``flip_f1``/``flip_f2`` are historical aliases with the same meaning as
#: ``ft_neg_f1``/``ft_neg_f2`` (both "decide directly whether to add it", not a negation).
SIGN_SAMPLING_KEYS: tuple[str, ...] = (
    "ft_neg",
    "ft_neg_f1",
    "ft_neg_f2",
    "flip_f1",
    "flip_f2",
    "ft_alt",
)


def sign_sampling_flags(params: Any) -> dict[str, Any]:
    """Pull the sampling overrides that "affect the FT sign/direction" out of the processing
    parameters (for derived runs and for the reference record).

    Only the keys in :data:`SIGN_SAMPLING_KEYS` are taken out of ``params["sampling"]`` (e.g.
    ``auto_phase`` belongs to the switches of a single step and is not listed); without any the
    result is an empty dict.
    """
    sampling = dict((params or {}).get("sampling") or {})
    return {
        key: sampling[key] for key in SIGN_SAMPLING_KEYS if key in sampling
    }
