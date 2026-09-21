"""SMILE parameter scan: final run script segmentation and candidate output naming
(0.2.199-patch29hz-revision 3 step 1). User plan: SMILE optimisation, use the final run script
as the template and only replace SMILE parameter -- first run the direct dimension once to get
the slice file, and then use the slice as input to run 25 times "SMILE + indirect dimension",
delete the candidate spectrum after each evaluation, and finally leave the sorting table + The
first three scripts. This test fixes these three things "script can be cut, it is still
consistent after cutting, and the candidate output name is unique" (it does not depend on
NMRPipe, plain text)."""

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
    """3D: The first half starts when the slice is written out, and the second half starts when the
    slice is read back (including SMILE)."""
    prefix, suffix = split_nus_script(_script_3d())

    assert prefix.rstrip().splitlines()[-1].strip() == (
        "| pipe2xyz -out nus3d_1/test%04d.ft1 -z"
    )
    assert suffix.splitlines()[0].strip().startswith(
        "xyz2pipe -in nus3d_1/test%04d.ft1 -x"
    )
    assert "-fn SMILE" not in prefix  # The direct dimension section is not executed SMILE.
    assert "-fn SMILE" in suffix   # The second paragraph assumes SMILE + indirect dimension.
    assert "-out e.ft3" in suffix


def test_2d_single_file_has_no_split_and_falls_back() -> None:
    """2D single file script does not have a slicing stream (direct dimension processing is the
    same as SMILE in the same pipeline), and no segmentation is performed."""
    prefix, suffix = split_nus_script(_script_2d())
    assert (prefix, suffix) == ("", "")


def test_rename_only_rewrites_final_output() -> None:
    """The candidate output rename only changes the final spectrum line, and the intermediate
    product name remains unchanged."""
    _prefix, suffix = split_nus_script(_script_3d())
    renamed = rename_nus_scan_output(suffix, "cand07.ft3")
    lines = [ln.strip() for ln in renamed.splitlines() if "-out " in ln]

    assert lines[-1].endswith("-out cand07.ft3 -x")
    # SMILE The middle plane is still written back to nus3d_rc (otherwise step3 cannot read it).
    assert any("nus3d_rc/test%04d.ft1" in ln for ln in lines)
    assert "cand07.ft3" not in lines[0]


def test_split_is_stable_for_changed_smile_params() -> None:
    """When only changing the SMILE parameter, the cut point remains unchanged (scanning 25 groups
    share the same slice)."""
    exp = read_dataset(BRUKER / "nus_3d")
    base = dict(in_file="e.fid", nuslist="nuslist", out_file="e.ft3")
    first = split_nus_script(generate_3d_nus_script(exp, nsigma=3.0, thresh=0.90, **base))
    second = split_nus_script(generate_3d_nus_script(exp, nsigma=7.0, thresh=0.99, **base))

    # The direct dimension section has nothing to do with the SMILE parameter.
    assert first[0] == second[0]
    assert "-nSigma 3" in first[1] and "-nSigma 7" in second[1]
