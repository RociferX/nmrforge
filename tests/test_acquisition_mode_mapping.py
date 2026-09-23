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

from backend.bruker_workflow import expected_values, parse_fid_com, patch_fid_com
from backend.script_generator import _FT_FLAGS, generate_convert_script
from core.data.bruker_reader import read_dataset
from core.experiment.acquisition_mode_detector import (
    bruk2pipe_mode_for,
    ft_alt_for,
    ft_neg_for,
)

#: Official TopSpin FnMODE enumeration -> bruk2pipe conversion keyword (recorded 2026-09-23).
#: FnMODE=0 is the placeholder that acqus carries for the direct dimension; it has no meaning
#: for an indirect dimension and is handled conservatively as Complex.
EXPECTED_MODE = {
    0: "Complex",
    1: "Real",           # QF: FT + MC
    2: "Sequential",     # QSEQ: FT -bruk (= -alt -real)
    3: "TPPI",           # TPPI: FT -real
    4: "Complex",        # States
    5: "Complex",        # States-TPPI: FT -alt
    6: "Echo-AntiEcho",  # Echo-Antiecho: shuffled during conversion
}

#: ``-aq2D``: the complex family uses 6->3 (E-A) and 4/5->2 (States family); the real families
#: use their own shape codes 0/1/2
EXPECTED_AQ2D = {0: "2", 1: "0", 2: "2", 3: "1", 4: "2", 5: "2", 6: "3"}

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
        assert not [w for w in warnings if "yMODE" in w], (fnmode, warnings)


def test_alt_and_neg_flags_match_the_record(bruker_dir: Path) -> None:
    """-alt/-neg match the record: no -neg in 2D, only the F2 dimension of a 3D States family."""
    exp2 = read_dataset(bruker_dir / "hsqc_2d")
    exp3 = read_dataset(bruker_dir / "hnca_3d")
    for fnmode in EXPECTED_MODE:
        # -alt: States-TPPI (5) needs it directly; QSEQ (2) goes through -bruk (= -alt -real)
        assert ft_alt_for(fnmode) is (fnmode in (2, 5))
        # -neg: first indirect dimension of a 3D only; States (4)/States-TPPI (5); not E-A (6)
        assert ft_neg_for(exp3, fnmode, "F2") is (fnmode in (4, 5))
        assert ft_neg_for(exp3, fnmode, "F1") is False
        assert ft_neg_for(exp2, fnmode, "F1") is False
    # the FT flag table used by the SMILE/NUS scripts (script_generator._FT_FLAGS) agrees with
    # the record: 4=States and 6=E-A carry no flag, 5=States-TPPI adds -alt; 1/2/3 (the real
    # families) are rejected at the entry points
    assert _FT_FLAGS[4] == (False, False)
    assert _FT_FLAGS[5] == (False, True)
    assert _FT_FLAGS[6] == (False, False)
