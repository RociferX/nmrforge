"""Guard for the single source of FnMODE -> NMRPipe conversion keywords (2026-09-23).

Background: the same "FnMODE -> bruk2pipe keyword" mapping used to exist in two places, and the
copy in ``bruker_workflow.expected_values`` disagreed with the one in ``script_generator`` - it
mapped FnMODE=4 and 6 to ``Echo-AntiEcho`` and 3/2/1 to ``Complex``, so ``patch_fid_com`` rewrote
the ``-yMODE`` that ``bruker -AUTO`` had written correctly (datasets with FnMODE 1/2/3/4 therefore
convert differently than before).

This file pins the three places together so any drift fails:

1. ``acquisition_mode_detector.bruk2pipe_mode_for`` (the single source);
2. ``bruker_workflow.expected_values`` (the target for the fid.com cross-check/patch);
3. what ``script_generator.generate_convert_script`` actually writes into the script (parsed back
   with ``parse_fid_com``, not read from the table);

and it checks the -alt/-neg flags against the recorded table (2D gets no -neg; only the first
indirect dimension of a 3D States-family dataset does).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.bruker_workflow import expected_values, parse_fid_com, patch_fid_com
from backend.script_generator import _FT_FLAGS, generate_convert_script
from core.data.bruker_reader import read_dataset
from core.experiment.acquisition_mode_detector import (
    bruk2pipe_mode_for,
    ft_alt_for,
    ft_neg_for,
)

#: Official TopSpin FnMODE enumeration -> bruk2pipe conversion keyword (finalised 2026-09-24:
#: **write the specific keyword**, no longer unified to ``Complex``; see the mode policy section
#: of ``.codex/AGENTS.md``).
#: FnMODE=0 is the direct-dimension placeholder (the acqus default); it is meaningless for an
#: indirect dimension and is handled conservatively as Complex.
EXPECTED_MODE = {
    0: "Complex",
    1: "Real",  # QF:FT + MC
    2: "Sequential",  # QSEQ:FT -bruk(= -alt -real)
    3: "TPPI",  # TPPI:FT -real
    4: "States",  # States: FT carries no flag
    5: "States-TPPI",  # States-TPPI:FT -alt
    6: "Echo-AntiEcho",  # Echo-Antiecho: shuffled during conversion
}

#: ``-aq2D`` (keyword changed 2026-09-24; the numeric form is deprecated by bruk2pipe):
#: magnitude->Magnitude (0), TPPI->TPPI (1), everything else->Complex (2, == States);
#: FnMODE=6 (E-A) changed from the old 3 (Image) to Complex -- the depositor script writes
#: States.
EXPECTED_AQ2D = {
    0: "Complex",
    1: "Magnitude",
    2: "Complex",
    3: "TPPI",
    4: "Complex",
    5: "Complex",
    6: "Complex",
}

_AQ2D_RE = re.compile(r"-aq2D\s+(\S*)")


def _with_f1_fnmode(experiment, fnmode: int):
    """Write FnMODE into the 2D acqu2s block (logical axis F1)."""
    experiment.acquisition_parameters["acqu2s"]["FnMODE"] = fnmode
    return experiment


def _with_z_fnmode(experiment, fnmode: int):
    """Write FnMODE into the 3D acqu3s block (logical axis F1, the second indirect dimension)."""
    experiment.acquisition_parameters["acqu3s"]["FnMODE"] = fnmode
    return experiment


def test_y_mode_is_the_same_in_all_three_places(bruker_dir: Path) -> None:
    """detector keyword == expected_values target == the -yMODE in the script (2D)."""
    for fnmode, expected in EXPECTED_MODE.items():
        exp = _with_f1_fnmode(read_dataset(bruker_dir / "hsqc_2d"), fnmode)
        script = generate_convert_script(exp, direct_points=2048)
        parsed = parse_fid_com(script)
        assert bruk2pipe_mode_for(fnmode) == expected
        assert expected_values(exp)["yMODE"][0] == expected
        assert parsed["yMODE"] == expected, (fnmode, parsed["yMODE"])
        assert parsed["xMODE"] == "DQD"
        assert _AQ2D_RE.search(script).group(1) == EXPECTED_AQ2D[fnmode], fnmode


def test_z_mode_is_the_same_in_all_three_places(bruker_dir: Path) -> None:
    """z dimension shares the source: E-A only exists in y, so FnMODE=6 falls back to Complex."""
    for fnmode, expected_y in EXPECTED_MODE.items():
        expected_z = "Complex" if expected_y == "Echo-AntiEcho" else expected_y
        exp = _with_z_fnmode(read_dataset(bruker_dir / "hnca_3d"), fnmode)
        script = generate_convert_script(exp, direct_points=2048)
        parsed = parse_fid_com(script)
        assert bruk2pipe_mode_for(fnmode, axis="z") == expected_z
        assert expected_values(exp)["zMODE"][0] == expected_z
        assert parsed["zMODE"] == expected_z, (fnmode, parsed["zMODE"])


def test_patch_fid_com_keeps_the_mode_bruker_auto_wrote(bruker_dir: Path) -> None:
    """Regression: the -yMODE of FnMODE 1/2/3/4 is no longer rewritten to a wrong value.

    Before the fix expected_values returned ``Complex`` for FnMODE 1/2/3 and
    ``Echo-AntiEcho`` for 4/6, so a user's FnMODE=4 (States) dataset was stamped
    ``-yMODE Echo-AntiEcho`` and the indirect-dimension quadrature handling went wrong.
    """
    template = (
        "bruk2pipe -in ./ser \\\n"
        "  -xN 2048 -yN 256 -xMODE DQD -yMODE @MODE@ \\\n"
        "  -out ./d_001.fid\n"
    )
    for fnmode, mode in sorted(EXPECTED_MODE.items()):
        exp = _with_f1_fnmode(read_dataset(bruker_dir / "hsqc_2d"), fnmode)
        patched, warnings = patch_fid_com(template.replace("@MODE@", mode), exp)
        assert f"-yMODE {mode}" in patched, (fnmode, patched)
        if fnmode == 0:
            # FnMODE undefined: per the conflict table, flag low confidence only, no rewrite
            # (2026-09-24, second round)
            continue
        # Only look at **rewrite** lines (``->``); the "mode/sign unconfirmed => no -neg" note
        # does not count as a rewrite
        assert not [w for w in warnings if "yMODE" in w and "->" in w], (fnmode, warnings)


def test_y_and_z_t_follow_the_mode_in_all_three_places(bruker_dir: Path) -> None:
    """``-yT``/``-zT`` share the source: real families (QF/QSEQ/TPPI) get ``T = N``, complex
    families get ``T = N/2``.

    2026-09-24 review C1: ``expected_values`` used to always halve, while ``script_generator``
    wrote ``T = N`` for the real families => the same experiment differs by 2x between the AUTO
    patch path and the fallback script (the NMRPipe ``nih.tcl`` rule is ``Real``/``Sequential``
    -> ``T = N``).
    """
    real_fmodes = {1, 2, 3}
    for fnmode in EXPECTED_MODE:
        exp2 = _with_f1_fnmode(read_dataset(bruker_dir / "hsqc_2d"), fnmode)
        values = expected_values(exp2)
        y_n = int(values["yN"][0])
        expected_y_t = y_n if fnmode in real_fmodes else y_n // 2
        assert values["yT"][0] == expected_y_t, (fnmode, values["yT"][0])
        script = generate_convert_script(exp2, direct_points=2048)
        assert int(parse_fid_com(script)["yT"]) == expected_y_t, fnmode

        exp3 = _with_z_fnmode(read_dataset(bruker_dir / "hnca_3d"), fnmode)
        values3 = expected_values(exp3)
        z_n = int(values3["zN"][0])
        expected_z_t = z_n if fnmode in real_fmodes else z_n // 2
        assert values3["zT"][0] == expected_z_t, (fnmode, values3["zT"][0])
        script3 = generate_convert_script(exp3, direct_points=2048)
        assert int(parse_fid_com(script3)["zT"]) == expected_z_t, fnmode
    # The direct dimension is halved as complex (whether it is real depends on
    # acqus AQ_mod, which is not in this table)
    exp2 = read_dataset(bruker_dir / "hsqc_2d")
    values = expected_values(exp2)
    assert values["xT"][0] == int(values["xN"][0]) // 2


def test_mode_family_equivalence_writes_the_specific_keyword(bruker_dir: Path) -> None:
    """2026-09-24 second finalisation: the same mode number must still write the **specific
    keyword**; sign handling is left to the FT flags.

    bruk2pipe help: `0 = Complex, States, Complex-N, States-N` / `0 = States-TPPI,
    States-TPPI-N` -- the depositor writes `States-TPPI` for the z dimension of FnMODE=5, the
    same name (same family) as our derived value `States-TPPI`. The old convention unified both
    to `Complex`; now the **specific keyword is kept/written** (the "negate the imaginary part"
    carried by `-N` must not be swallowed again), while `-alt`/`-neg` are still decided by the
    processing-time FT flags (conversion only writes the header, does no sign adjustment, so
    nothing alternates twice).
    """
    from core.experiment.acquisition_mode_detector import same_mode_family

    assert same_mode_family("States-TPPI", "Complex")
    assert same_mode_family("States", "Complex")
    assert not same_mode_family("Complex", "TPPI")
    assert not same_mode_family("Echo-AntiEcho", "Complex")

    exp = _with_z_fnmode(read_dataset(bruker_dir / "hnca_3d"), 5)
    template = (
        "bruk2pipe -in ./ser \\\n  -xMODE DQD -yMODE Complex -zMODE Complex \\\n  -out fid\\n"
    )
    patched, warnings = patch_fid_com(template, exp)
    assert "-zMODE States-TPPI" in patched
    assert any(w.startswith("zMODE:") for w in warnings)  # why rewritten (language-agnostic)


def test_patch_fid_com_keeps_a_canonical_negated_mode(bruker_dir: Path) -> None:
    """The ``-N`` variants (option B canonical "negate the imaginary part") are kept as they
    are, never rewritten to a non-``-N`` form.

    The same mode number in ``States-TPPI-N``/``Complex-N``/``States-N`` is not the same thing
    (NMRPipe ``parsehdr.c``: ``-N`` => ``-xALT 16/18`` = With Negation of Imaginaries); rewriting
    them to ``States-TPPI`` would silently drop the imaginary-negation information.
    """
    exp = _with_z_fnmode(read_dataset(bruker_dir / "hnca_3d"), 5)
    template = (
        "bruk2pipe -in ./ser \\\n  -xMODE DQD -yMODE Complex -zMODE States-TPPI-N \\\n  -out fid\\n"
    )
    patched, warnings = patch_fid_com(template, exp)
    assert "-zMODE States-TPPI-N" in patched
    assert not [w for w in warnings if w.startswith("zMODE:") and "->" in w]
    # Same family as the derived value => the cross-check no longer reports "script vs acqus"
    assert not [w for w in warnings if w.startswith("zMODE:") and "vs acqus" in w]


def test_fallback_script_writes_full_precision(bruker_dir: Path) -> None:
    """The fallback script writes spectral width/observation frequency at full precision (as do
    AUTO and the depositor; `%g` keeps only 6 digits).
    """
    exp = read_dataset(bruker_dir / "hsqc_2d")
    script = generate_convert_script(exp)
    parsed = parse_fid_com(script)
    direct = exp.direct_dimension
    # Relative 1e-9: the old %g (6 significant digits) is off by 1e-6 at 11160.7142857, so it
    # fails
    assert float(parsed["xSW"]) == pytest.approx(direct.sw, rel=1e-9)
    assert float(parsed["xOBS"]) == pytest.approx(direct.sf, rel=1e-9)


def test_alt_and_neg_flags_match_the_record(bruker_dir: Path) -> None:
    """-alt is decided by FnMODE; -neg only by the coherence-pathway criterion (never added when
    undecidable).

    2026-09-24 second finalisation: ``-neg`` is no longer guessed from "the first indirect
    dimension of a 3D States family is always negated" (the twentieth-round review found that
    rule of thumb invalid) but comes from the ``qphase`` criterion in
    ``core.experiment.pulse_pathways``; the synthetic fixtures have no ``pulseprogram`` =>
    undecidable => never added (``review_lines`` asks for manual review).
    """
    exp2 = read_dataset(bruker_dir / "hsqc_2d")
    exp3 = read_dataset(bruker_dir / "hnca_3d")
    for fnmode in EXPECTED_MODE:
        # -alt: States-TPPI (5) needs it directly; QSEQ (2) goes through -bruk (= -alt -real)
        assert ft_alt_for(fnmode) is (fnmode in (2, 5))
        # -neg: undecidable (no pulse program) => not added for any dimension
        assert ft_neg_for(exp3, fnmode, "F2") is False
        assert ft_neg_for(exp3, fnmode, "F1") is False
        assert ft_neg_for(exp2, fnmode, "F1") is False
    # the FT flag table used by the SMILE/NUS scripts (script_generator._FT_FLAGS) still only
    # covers alt: 4=States and 6=E-A carry no flag, 5=States-TPPI adds -alt; 1/2/3 (the real
    # families) are rejected at the entry points
    assert _FT_FLAGS[4] == (False, False)
    assert _FT_FLAGS[5] == (False, True)
    assert _FT_FLAGS[6] == (False, False)


def test_hypercomplex_mult_is_the_single_source() -> None:
    """D: the three "hypercomplex component counts" are unified on
    ``acquisition_mode_detector.hypercomplex_mult``.

    The real families (QF/QSEQ/TPPI = 1,2,3) give 1 and the complex families give 2; values
    outside the table (>=7) are handled conservatively as complex.
    """
    from backend.script_generator import _mult_for as script_mult
    from core.data.bruker_reader import _mult_for as reader_mult
    from core.experiment.acquisition_mode_detector import hypercomplex_mult

    for fnmode in range(0, 8):
        expected = 1 if fnmode in (1, 2, 3) else 2
        assert hypercomplex_mult(fnmode) == expected, fnmode
        assert reader_mult(fnmode) == expected, fnmode
        assert script_mult(fnmode) == expected, fnmode


def test_fnmode_outside_the_table_does_not_claim_a_mode(bruker_dir: Path) -> None:
    """D: FnMODE=7 is outside the table (NMRPipe treats it as Real, no real-machine data) --
    `expected_values` gives no MODE target (the script value is not rewritten) and `mode_audit`
    flags it as unverified.
    """
    from backend.bruker_workflow import mode_audit

    exp = _with_f1_fnmode(read_dataset(bruker_dir / "hsqc_2d"), 7)
    assert "yMODE" not in expected_values(exp)
    template = "bruk2pipe -in ./ser \\\n  -xMODE DQD -yMODE Complex -out fid\\n"
    patched, _warnings = patch_fid_com(template, exp)
    assert "-yMODE Complex" in patched
    plan = mode_audit(exp, parse_fid_com(template))
    assert {r["axis"]: r["decision"] for r in plan["dims"]}["y"] in (
        "low_confidence",
        "unverified",
        "inferred",
    )
