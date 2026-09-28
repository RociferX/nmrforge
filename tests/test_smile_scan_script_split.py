"""SMILE parameter sweep: final-run script splitting and candidate output naming
(0.2.199-patch29hz-fix3, step 1).

The user's plan: SMILE optimization uses the final-run script as a template and only swaps
SMILE parameters -- run the direct dimension once to get the slice files, then use the
slices as input for 25 runs of "SMILE + indirect dimension", delete each candidate
spectrum right after evaluation, and finally keep the ranking table + the top three
scripts. This test pins down three things: the script splits, the split stays
self-consistent, and candidate output names are unique (no NMRPipe dependency, plain text
only).
"""

from __future__ import annotations

from pathlib import Path

from backend.script_generator import (
    generate_2d_nus_script,
    generate_3d_nus_script,
    rename_nus_scan_output,
    split_nus_script,
)
from core.data.bruker_reader import read_dataset

BRUKER = Path(__file__).resolve().parent / "fixtures" / "bruker"


def _script_3d() -> str:
    exp = read_dataset(BRUKER / "nus_3d")
    return generate_3d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft3"
    )


def _script_2d() -> str:
    exp = read_dataset(BRUKER / "nus_2d")
    return generate_2d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft2"
    )


def test_3d_script_splits_at_slice_boundary() -> None:
    """3D: the first half ends at the slice write, the second half starts by reading the
    slice back (SMILE included).
    """
    prefix, suffix = split_nus_script(_script_3d())

    assert prefix.rstrip().splitlines()[-1].strip() == (
        "| pipe2xyz -out nus3d_1/test%04d.ft1 -z"
    )
    assert suffix.splitlines()[0].strip().startswith(
        "xyz2pipe -in nus3d_1/test%04d.ft1 -x"
    )
    assert "-fn SMILE" not in prefix  # the direct-dimension part does not run SMILE
    assert "-fn SMILE" in suffix   # the second part carries SMILE + the indirect dimension
    assert "-out e.ft3" in suffix


def test_2d_single_file_has_no_split_and_falls_back() -> None:
    """The 2D single-file script has no slice stream (direct-dimension processing and SMILE
    share one pipeline), so it is not split.
    """
    prefix, suffix = split_nus_script(_script_2d())
    assert (prefix, suffix) == ("", "")


def test_rename_only_rewrites_final_output() -> None:
    """Renaming a candidate output changes only the final-spectrum line; intermediate
    product names stay unchanged.
    """
    _prefix, suffix = split_nus_script(_script_3d())
    renamed = rename_nus_scan_output(suffix, "cand07.ft3")
    lines = [ln.strip() for ln in renamed.splitlines() if "-out " in ln]

    assert lines[-1].endswith("-out cand07.ft3 -x")
    # SMILE intermediate planes still go back to nus3d_rc (otherwise step3 cannot read them)
    assert any("nus3d_rc/test%04d.ft1" in ln for ln in lines)
    assert "cand07.ft3" not in lines[0]


def test_split_is_stable_for_changed_smile_params() -> None:
    """When only the SMILE parameters change the split point stays the same (the 25 scan
    groups share one set of slices).
    """
    exp = read_dataset(BRUKER / "nus_3d")
    base = dict(in_file="e.fid", nuslist="nuslist", out_file="e.ft3")
    first = split_nus_script(generate_3d_nus_script(exp, nsigma=3.0, thresh=0.90, **base))
    second = split_nus_script(generate_3d_nus_script(exp, nsigma=7.0, thresh=0.99, **base))

    assert first[0] == second[0]  # the direct-dimension part ignores the SMILE parameters
    assert "-nSigma 3" in first[1] and "-nSigma 7" in second[1]
