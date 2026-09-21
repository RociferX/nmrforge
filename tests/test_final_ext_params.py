"""Final run direct dimension range (final_ext_*) -> optimisation/reconstruction window mapping
(0.2.199-patch29hz-revision 17). user 2026-09-11: "Smile optimisation does not use the direct
dimension range of the final script? Why does it show that there is insufficient memory? The
range I set is enough" -- SMILE optimisation/scan direct adjustment reconstruct_nus, the
parameter contains GUI `final_ext_lo`/`final_ext_hi`, if not mapped, it will return to the
default wide window (10.5-6.5), memory guard estimates according to the wide window -> false
alarm "insufficient memory"/will be automatically reduced to direct dimension zero filling."""

from __future__ import annotations

from backend.nmrpipe_backend import apply_final_ext_params


def test_maps_final_range_to_ext() -> None:
    params = {"final_ext_lo": "8.5", "final_ext_hi": "7.5"}

    note = apply_final_ext_params(params)

    assert params["ext_lo"] == "8.5"
    assert params["ext_hi"] == "7.5"
    assert note and "ext_lo=8.5" in note and "ext_hi=7.5" in note


def test_partial_range_only_maps_given_side() -> None:
    """When only one end is filled in, only that end is mapped (the other end remains the default,
    which has the same semantics as the final run)."""
    params = {"final_ext_lo": "9.5"}

    note = apply_final_ext_params(params)

    assert params["ext_lo"] == "9.5"
    assert "ext_hi" not in params
    assert note and "ext_hi" not in note


def test_apply_ext_to_opt_off_still_maps() -> None:
    """Even if "final run only" (apply_ext_to_opt=0), template also uses the final run range --
    template must be equal to the final run script ("refer to the final script and only change
    the SMILE parameter"); this switch only affects the first pass of the same route."""
    params = {"final_ext_lo": "8.5", "final_ext_hi": "7.5", "apply_ext_to_opt": "0"}

    note = apply_final_ext_params(params)

    assert params["ext_lo"] == "8.5" and params["ext_hi"] == "7.5"
    assert note is not None


def test_explicit_ext_wins() -> None:
    """Explicit ext_lo/ext_hi takes precedence and is not covered by the final run range."""
    params = {"ext_lo": "11.0", "final_ext_lo": "8.5", "final_ext_hi": "7.5"}

    note = apply_final_ext_params(params)

    assert params["ext_lo"] == "11.0"  # Keep explicit value.
    assert params["ext_hi"] == "7.5"  # hi Still from the final run range.
    assert note and "ext_lo=" not in note


def test_no_final_range_is_noop() -> None:
    params: dict = {}
    assert apply_final_ext_params(params) is None
    assert params == {}

    blank = {"final_ext_lo": "", "final_ext_hi": "  "}
    assert apply_final_ext_params(blank) is None
    assert "ext_lo" not in blank and "ext_hi" not in blank
