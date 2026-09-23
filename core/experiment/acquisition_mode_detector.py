"""Acquisition-mode detection: States / States-TPPI / Echo-Antiecho / QF / magnitude.

Decided from FnMODE/FnTYPE/AQ_mod and reported as a per-dimension acquisition_mode, which the
FT flags (-alt/-neg), the flip and the phase handling build on (framework §6.1).
"""

from __future__ import annotations

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

# official bruk2pipe ACQUISITION MODES table (see the nmrPipe/format documentation):
#   States/DQD/Complex -> no FT flag; States-TPPI -> FT -alt;
#   States-N/Complex-N/States-TPPI-N -> add -neg; TPPI -> FT -real;
#   Sequential/QSEQ -> FT -alt (together with -real, equivalent to FT -bruk);
#   Echo-Antiecho -> the shuffling happens during conversion, no FT flag needed.
# Indirect dimensions that need -alt: States-TPPI (=5) and Sequential/QSEQ (=2, combined with
# -real); TPPI (=3) is -real rather than -alt. The NMRPipe real processing path for the
# real/magnitude families (1/2/3) is not implemented and the entry points report it explicitly
# (see unsupported_real_mode_error), so in practice only 5 reaches the script with -alt.
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


#: FT processing shape -> bruk2pipe conversion keyword (-yMODE/-zMODE)
_BRUK2PIPE_MODE_BY_KIND = {
    "complex": "Complex",
    "magnitude": "Real",
    "sequential": "Sequential",
    "tppi": "TPPI",
}

#: Direct-dimension conversion keyword (bruk2pipe -xMODE): always DQD in Bruker
DIRECT_BRUK2PIPE_MODE = "DQD"


def bruk2pipe_mode_for(fnmode: int, *, axis: str = "y") -> str:
    """FnMODE -> bruk2pipe conversion keyword (``-xMODE``/``-yMODE``/``-zMODE``).

    Single source: ``backend.bruker_workflow.expected_values`` (fid.com cross-check and
    patch, acqus is authoritative) and ``backend.script_generator`` (fallback conversion
    script) both call this function. Before the 2026-09-23 merge the two places each had
    their own table and they disagreed: bruker_workflow mapped FnMODE=4 and 6 to
    ``Echo-AntiEcho`` and 3/2/1 to ``Complex``, so it rewrote the fid.com that
    ``bruker -AUTO`` had written correctly (datasets with FnMODE 1/2/3/4).

    With ``axis="z"`` the result is never ``Echo-AntiEcho``: the E-A shuffling happens
    in the y dimension of bruk2pipe only (see the z branch of script_generator), so z
    always stays ``Complex``.
    """
    kind = ft_kind_for(fnmode)
    if kind == "complex" and int(fnmode) == 6 and axis != "z":
        return "Echo-AntiEcho"
    return _BRUK2PIPE_MODE_BY_KIND.get(kind, "Complex")


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

# Modes needing -neg on the first indirect dimension of a 3D (acqu2s/F2): States (=4) /
# States-TPPI (=5).
# Basis: the Bruker 3D ser layout inverts the frequency of the F2 dimension of States-family
# data (lab sampleC with hand-set FnMODE=5 -> FT -alt -neg; the HNCA 15N mirror image on the
# nmrpipe forum is likewise fixed with FT -alt -neg; sampleB FnMODE=6 E-A needs none).
_FNMODE_FT_NEG_F2_3D = {4, 5}


def ft_alt_for(fnmode: int) -> bool:
    """The indirect-dimension FT needs -alt when FnMODE=5 (States-TPPI)."""
    return fnmode in _FNMODE_FT_ALT


def ft_neg_for(experiment: Experiment, fnmode: int, logical_axis: str) -> bool:
    """Whether the FT of the first indirect dimension of a 3D (acqu2s/F2) needs -neg.

    In the official bruk2pipe ACQ MODE table -neg corresponds to
    States-N/Complex-N/States-TPPI-N (a reversed spectrum); the Bruker 3D ser ordering makes
    States/States-TPPI data on the F2 dimension appear mirrored in the NMRPipe FT, which -neg
    corrects (equivalent to the States-TPPI-N handling). E-A (6) is already shuffled during
    conversion and TPPI (3) is a real mode, so neither gets -neg; the second indirect dimension
    (F1) and 2D data never do.

    Verified on real data: sampleC with hand-set FnMODE=5 (F2=15N) -> FT -alt -neg; sampleB
    FnMODE=6 (E-A) -> no -neg; F1 (13C) with the same FnMODE=5 -> -alt only.
    """
    if experiment.ndim < 3 or logical_axis != "F2":
        return False
    return fnmode in _FNMODE_FT_NEG_F2_3D
