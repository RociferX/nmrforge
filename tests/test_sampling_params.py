"""Sampling parameter block landing test (0.2.67): ft_neg/ft_alt/flip_f1 script consumption."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.script_generator import (
    generate_2d_nus_script,
    generate_process_script,
)
from core.data.bruker_reader import read_dataset
from core.planning.method_selector import select_method


def test_sampling_default_preserves_script(bruker_dir: Path) -> None:
    """Defaults (ft_neg/ft_alt=None + flip_*=None = follow the automatic criterion) keep the output
    unchanged with no sampling."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    base = generate_process_script(exp, plan, in_file="f.fid", out_file="o.ft2")
    with_sampling = generate_process_script(
        exp,
        plan,
        in_file="f.fid",
        out_file="o.ft2",
        sampling={"ft_neg": None, "ft_alt": None, "flip_f1": None, "flip_f2": None},
    )
    assert base == with_sampling


def test_sampling_ft_alt_off(bruker_dir: Path) -> None:
    """ft_alt=False -> the indirect-dimension FT adds no -alt (this dataset's F1=States-TPPI
    would add it by default)."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    script = generate_process_script(
        exp,
        plan,
        in_file="f.fid",
        out_file="o.ft2",
        sampling={"ft_alt": False},
    )
    assert "| nmrPipe -fn FT -alt" not in script
    assert "| nmrPipe -fn FT \\" in script or "| nmrPipe -fn FT -neg" in script


def test_sampling_ft_neg_on(bruker_dir: Path) -> None:
    """ft_neg=True -> FT lines carry -neg."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    script = generate_process_script(
        exp,
        plan,
        in_file="f.fid",
        out_file="o.ft2",
        sampling={"ft_neg": True},
    )
    assert " -neg" in script


def test_sampling_flip_f1(bruker_dir: Path) -> None:
    """flip_f1=True -> the F1 axis FT line carries -neg (flip)."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    script = generate_process_script(
        exp,
        plan,
        in_file="f.fid",
        out_file="o.ft2",
        sampling={"flip_f1": True},
    )
    assert " -neg" in script


def test_sampling_flip_f2_only_touches_the_f2_indirect_dimension(
    bruker_dir: Path,
) -> None:
    """Manual flip for 3D: ``flip_f2`` makes only the **indirect dimension F2** carry ``-neg``,
    leaving F1 untouched.

    The user's control semantics of 2026-09-25: for 2D the indirect dimension is F1 (use
    ``flip_f1``); the three 3D options are indirect (F2) / indirect (F1) / F1 and F2 ⇒ set
    ``flip_f2`` / ``flip_f1`` / both. That same flag set also feeds ``-yNeg``/``-xNeg`` (via
    ``_smile_direction_args``).
    """
    exp = read_dataset(bruker_dir / "hnca_3d")
    plan = select_method(exp)
    base = generate_process_script(exp, plan, in_file="f.fid", out_file="o.ft3")
    only_f2 = generate_process_script(
        exp, plan, in_file="f.fid", out_file="o.ft3", sampling={"flip_f2": True}
    )
    both = generate_process_script(
        exp,
        plan,
        in_file="f.fid",
        out_file="o.ft3",
        sampling={"flip_f1": True, "flip_f2": True},
    )
    base_neg = base.count("| nmrPipe -fn FT")
    assert only_f2.count(" -neg") >= 1
    # Flip F2 only -> one -neg fewer than flipping both; both exceed base
    assert both.count(" -neg") > only_f2.count(" -neg") >= base.count(" -neg")
    assert base_neg >= 2  # a 3D has at least two indirect-dimension FT lines


def test_sampling_nus_ft_alt_off(bruker_dir: Path) -> None:
    """The NUS script consumes ft_alt too (with it off the F1 FT has no -alt)."""
    exp = read_dataset(bruker_dir / "nus_2d")
    script = generate_2d_nus_script(
        exp,
        in_file="e.fid",
        nuslist="nuslist",
        out_file="e.ft2",
        sampling={"ft_alt": False},
    )
    assert "| nmrPipe -fn FT -alt" not in script


# ---------------------------------------------------------------------------
# Default automatic criterion (second finalization 2026-09-25): the simplest rule with the
# lowest error probability
#
# User: "the user checks it all anyway, so let's use the rule with the lowest error rate"
# ⇒ the condition for applying `-neg` automatically = the **NMRPipe y dimension** of a 3D
#    (recognized from AQSEQ) + a States family; anything other than y/z, an E/A family, or an
#    undecidable case (missing AQSEQ / 2D States) ⇒ no add + a review note.
# Evidence set: the lab's 3D data and the data owner's own scripts add -neg only on the first
# indirect dimension (docs/backend/fid_com_parameter_sources.md §8.2/§8.3).
# ---------------------------------------------------------------------------
def _ft_lines(script: str) -> list[str]:
    """FT lines in the script (in order of appearance: direct dimension -> F2 -> F1)."""
    return [line for line in script.splitlines() if "-fn FT" in line]


def _three_d(fixture: str, bruker_dir: Path, aqseq: int):
    """Read a 3D fixture and set ``AQSEQ`` in memory (real acqus always has the field; the
    synthetic fixture does not write it)."""
    exp = read_dataset(bruker_dir / fixture)
    exp.acquisition_parameters["acqus"]["AQSEQ"] = aqseq
    return exp


def test_default_neg_rule_puts_neg_on_the_y_axis_only(bruker_dir: Path) -> None:
    """Automatic criterion: AQSEQ=321 (y=F2) -> the F2 FT line carries ``-neg``, while the z
    dimension (F1) and the direct dimension carry none."""
    exp = _three_d("hnca_3d", bruker_dir, 0)
    plan = select_method(exp)
    script = generate_process_script(exp, plan, in_file="f.fid", out_file="o.ft3")
    ft = _ft_lines(script)
    assert len(ft) == 3
    assert "-neg" not in ft[0]  # direct dimension is never added
    # F2: acqu2s FnMODE=5 (States-TPPI) -> -alt -neg (same shape as the owner's ``smile.com``)
    assert ft[1].rstrip().endswith("-alt -neg \\")
    assert "-neg" not in ft[2]  # F1: acqu3s FnMODE=4 (States) -> no flags


def test_default_neg_rule_follows_aqseq_not_the_logical_name(bruker_dir: Path) -> None:
    """With AQSEQ=312 the NMRPipe y axis is logical **F1** -> ``-neg`` lands on F1, not blindly on
    F2."""
    exp = _three_d("hnca_3d", bruker_dir, 1)
    plan = select_method(exp)
    script = generate_process_script(exp, plan, in_file="f.fid", out_file="o.ft3")
    ft = _ft_lines(script)
    assert len(ft) == 3
    assert "-neg" not in ft[1]  # F2 is the z dimension here -> not added
    assert ft[2].rstrip().endswith("-neg \\")  # F1 is the y dimension -> added


def test_default_neg_rule_asks_the_user_when_aqseq_is_unknown(bruker_dir: Path) -> None:
    """Missing AQSEQ -> y/z cannot be told apart -> **no add** + a review note (handed to the
    spectrum step's flip controls)."""
    from core.experiment.pulse_pathways import review_lines

    exp = read_dataset(bruker_dir / "hnca_3d")
    exp.acquisition_parameters["acqus"].pop("AQSEQ", None)
    plan = select_method(exp)
    script = generate_process_script(exp, plan, in_file="f.fid", out_file="o.ft3")
    assert "-neg" not in script
    assert review_lines(exp)  # undecidable -> a review note exists (same text in three places)


def test_manual_flip_can_force_the_automatic_neg_off(bruker_dir: Path) -> None:
    """A manual flip follows the user: ``flip_f2=False`` removes the automatically added ``-neg``
    (three-state override)."""
    exp = _three_d("hnca_3d", bruker_dir, 0)
    plan = select_method(exp)
    auto = generate_process_script(exp, plan, in_file="f.fid", out_file="o.ft3")
    assert auto.count("-neg") == 1
    forced_off = generate_process_script(
        exp, plan, in_file="f.fid", out_file="o.ft3", sampling={"flip_f2": False}
    )
    assert forced_off.count("-neg") == 0
    # Turning off F2 only does not affect the other axes (an explicit True adds it back)
    back_on = generate_process_script(
        exp, plan, in_file="f.fid", out_file="o.ft3", sampling={"flip_f2": True}
    )
    assert back_on.count("-neg") == 1


def test_default_neg_rule_reaches_the_3d_nus_script_and_smile(
    bruker_dir: Path,
) -> None:
    """The 3D NUS final script + SMILE direction flags follow the automatic criterion too (the
    y-dimension ``-neg`` / ``-xNeg`` share one source)."""
    from backend.script_generator import generate_3d_nus_script

    exp = _three_d("nus_3d", bruker_dir, 0)
    script = generate_3d_nus_script(exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft3")
    ft = _ft_lines(script)
    assert len(ft) == 3  # step1 direct dimension + step3's F2/F1
    assert "-neg" not in ft[0]
    assert ft[1].rstrip().endswith("-alt -neg \\")  # F2(y, States-TPPI)
    assert "-neg" not in ft[2]  # F1(z, States)
    # SMILE internal directions share one source with the step3 FT: F2 -alt/-neg -> -xAlt/-xNeg
    assert "-xNeg" in script and "-yNeg" not in script


def test_global_ft_neg_outranks_the_per_axis_flip() -> None:
    """``sampling.ft_neg`` (global) has the **highest** priority: per-axis ``flip_*`` must not
    override it.

    Regression: previously ``ft_neg=False`` set no override and was overridden back to ``-neg``
    by a later ``flip_f1=True`` (contradicting ``docs/API_CONTRACT.md``: "``ft_neg`` global
    beats per-axis").
    """
    from backend.script_generator import _ft_flags

    # Global off + per-axis on -> global wins (not added)
    assert _ft_flags(False, False, sampling={"ft_neg": False, "flip_f1": True}, axis="F1") == []
    # Global on + per-axis off -> global wins (added)
    assert _ft_flags(False, False, sampling={"ft_neg": True, "flip_f1": False}, axis="F1") == [
        "-neg"
    ]
    # Global not given (None) -> per-axis applies as usual
    assert _ft_flags(False, False, sampling={"flip_f1": True}, axis="F1") == ["-neg"]
    assert _ft_flags(False, False, sampling={"flip_f1": False}, axis="F1") == []


def test_global_ft_neg_off_also_beats_the_automatic_rule() -> None:
    """A global ``ft_neg=False`` switches the automatic criterion off, even when ``force_neg``
    (the automatic conclusion) is true."""
    from backend.script_generator import _ft_flags

    assert _ft_flags(False, False, sampling={"ft_neg": False}, axis="F1", force_neg=True) == []
    # When the global value is not given, the automatic criterion still applies
    assert _ft_flags(False, False, sampling={}, axis="F1", force_neg=True) == ["-neg"]


def test_simple_neg_rule_honours_the_acquisition_family() -> None:
    """The ``family`` argument of ``simple_neg_rule`` must take part in the decision (E/A and QF
    families ⇒ no add).

    Regression: the argument was declared but never read (while the docstring claimed the
    criterion follows the "acquisition family"); now the family fact wins and a missing
    ``FnMODE`` still yields "no add".
    """
    from core.experiment.acquisition_encoding import simple_neg_rule

    for family in ("F1EA", "F1QF"):
        decision, why = simple_neg_rule(3, "F2", None, family, "321")
        assert decision == "none", (family, decision)
        assert why == "acquisition_family_takes_no_neg"
    # The F1PH family is unaffected: y dimension + States family still gives "add"
    assert simple_neg_rule(3, "F2", 5, "F1PH", "321")[0] == "add"


def test_storage_encoding_and_decision_take_data_dir() -> None:
    """Layer B/C ``data_dir`` must be passed all the way to ``acquisition_model`` (the report and
    the script share one source).

    Regression: ``ft_neg_decision`` called ``acquisition_model(experiment)`` without
    ``data_dir``, so when the pulse program lived only in the import directory AQSEQ was
    unavailable ⇒ the decision in the report disagreed with the script.
    """
    import inspect

    from core.experiment.acquisition_encoding import (
        ft_neg_decision,
        storage_encoding,
    )

    assert "data_dir" in inspect.signature(storage_encoding).parameters
    assert "data_dir" in inspect.signature(ft_neg_decision).parameters
    source = " ".join(inspect.getsource(ft_neg_decision).split())  # line wrapping is irrelevant
    assert "acquisition_model(experiment, data_dir=data_dir)" in source
    assert "storage_encoding(experiment, logical_axis, data_dir=data_dir)" in source


# ---------------------------------------------------------------------------
# Explicit "add or not" = a **direct decision** (absolute), not a "flip/invert the automatic
# criterion" (confirmed by name by the user on 2026-09-25)
#
# Primary names ft_neg_f1/ft_neg_f2 (same semantics as the global ft_neg: None=auto / True=add
# / False=no add); flip_f1/flip_f2 are legacy aliases with the same meaning.
# ---------------------------------------------------------------------------
def test_axis_neg_keys_primary_name_and_legacy_alias(bruker_dir: Path) -> None:
    """``ft_neg_f1``/``ft_neg_f2`` (primary names) and ``flip_f1``/``flip_f2`` (aliases) are
    synonymous; when both are given the **primary name wins**."""
    exp = read_dataset(bruker_dir / "hnca_3d")  # no AQSEQ -> the automatic criterion stays out
    plan = select_method(exp)

    def script(sampling: dict | None) -> str:
        return generate_process_script(
            exp, plan, in_file="f.fid", out_file="o.ft3", sampling=sampling
        )

    primary = script({"ft_neg_f2": True})
    alias = script({"flip_f2": True})
    assert primary == alias  # the alias is synonymous (scripts identical byte for byte)
    assert primary.count("-neg") == 1
    # Both given with opposite values -> the primary name wins (the alias does not override it)
    assert "-neg" not in script({"ft_neg_f2": False, "flip_f2": True})


def test_explicit_axis_neg_is_absolute_not_a_toggle(
    bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit value = "directly decide add / no add": with the automatic criterion on or off,
    the same ``True`` yields the same script.

    (If it were implemented as "flip the result of the automatic criterion", ``True`` would
    become "no add" once that criterion is off -- this guard pins down that it is not that
    semantics.)
    """
    from core.experiment import acquisition_mode_detector as amd

    exp = _three_d("hnca_3d", bruker_dir, 0)  # automatic criterion (on) decides F2 gets -neg

    def script(sampling: dict | None) -> str:
        # The automatic criterion lands when select_method builds the plan -> rebuild each time with
        # the current switch
        return generate_process_script(
            exp, select_method(exp), in_file="f.fid", out_file="o.ft3", sampling=sampling
        )

    monkeypatch.setattr(amd, "AUTO_NEG_JUDGEMENT", True)
    auto_on_true = script({"ft_neg_f2": True})
    auto_on_false = script({"ft_neg_f2": False})
    monkeypatch.setattr(amd, "AUTO_NEG_JUDGEMENT", False)
    auto_off_true = script({"ft_neg_f2": True})
    auto_off_false = script({"ft_neg_f2": False})

    assert auto_on_true == auto_off_true and "-neg" in auto_on_true  # True always means "add"
    assert auto_on_false == auto_off_false and "-neg" not in auto_on_false  # False = "no add"
    # Control: without an explicit value the automatic criterion applies (on -> add / off -> none)
    monkeypatch.setattr(amd, "AUTO_NEG_JUDGEMENT", True)
    assert "-neg" in script(None)
    monkeypatch.setattr(amd, "AUTO_NEG_JUDGEMENT", False)
    assert "-neg" not in script(None)


def test_neg_sign_keys_are_wired_everywhere() -> None:
    """The sign keys must agree in three places: ``param_schema`` / the single source / the sweep
    lock table.

    Drift guard (lesson from the 2026-09-25 rename round): adding or renaming a sign key while
    forgetting to register it in the schema or the lock table silently causes two kinds of
    error: "passed through the API but ineffective" or "can be used as a sweep axis".
    """
    from backend.script_generator import param_schema
    from core.experiment.acquisition_mode_detector import SIGN_SAMPLING_KEYS
    from nmrforge_api.errors import SweepError
    from nmrforge_api.sweep import _LOCKED_AXIS_KEYS, validate_axes

    schema = param_schema()
    props = schema["properties"]["sampling"]["properties"]
    defaults = schema["default"]["sampling"]
    for key in SIGN_SAMPLING_KEYS:
        assert key in props, f"param_schema 漏了 {key}"
        assert key in defaults, f"param_schema 默认值漏了 {key}"
    # Explicit FT-neg names and aliases accept boolean candidates; alt and auto_phase remain locked.
    neg_keys = ("ft_neg", "ft_neg_f1", "ft_neg_f2", "flip_f1", "flip_f2")
    for key in (*neg_keys, "ft_alt"):
        assert key in SIGN_SAMPLING_KEYS, f"单一来源漏了 {key}(参考/派生运行不会沿用)"
    for key in neg_keys:
        axis = f"sampling.{key}"
        assert axis not in _LOCKED_AXIS_KEYS, f"{key} 应允许显式 bool 候选"
        assert validate_axes({axis: [False, True]}) == []
    for key in ("ft_alt", "auto_phase"):
        axis = f"sampling.{key}"
        assert axis in _LOCKED_AXIS_KEYS, f"{key} 不应允许作为参数组合轴"
        with pytest.raises(SweepError):
            validate_axes({axis: [False, True]})


def test_neg_rule_is_wired_in_all_four_paths(bruker_dir: Path) -> None:
    """**All four paths** (2D/3D × uniform/NUS) install ``-neg`` by the same criterion.

    · 3D (both paths): ``AQSEQ=321`` ⇒ the ``y`` dimension (logical F2) carries ``-neg`` and
      the ``z`` dimension (F1) does not -- the paths differ only in "where the run starts"
      (the whole uniform script / NUS step3); the sign convention must agree.
    · 2D (both paths): for 2D the criterion answers ``ask_user`` for States/uncovered cases
      ⇒ **no add by default**; only an explicit ``ft_neg_f1=True`` (**direct decision**) adds
      it -- the 2D indirect dimension is F1 (= acqu2s = y).
    """
    from backend.script_generator import generate_3d_nus_script

    # 3D uniform
    exp3 = _three_d("hnca_3d", bruker_dir, 0)
    ft = _ft_lines(
        generate_process_script(exp3, select_method(exp3), in_file="f.fid", out_file="o.ft3")
    )
    assert "-neg" not in ft[0] and "-neg" in ft[1] and "-neg" not in ft[2]
    # 3D NUS (final script: step1 direct dimension + step3's F2/F1)
    exp3n = _three_d("nus_3d", bruker_dir, 0)
    ft = _ft_lines(
        generate_3d_nus_script(exp3n, in_file="e.fid", nuslist="nuslist", out_file="e.ft3")
    )
    assert "-neg" not in ft[0] and "-neg" in ft[1] and "-neg" not in ft[2]
    # 2D NUS: no add by default, only explicit (also synced to the SMILE flags via _ft_flags)
    exp2n = read_dataset(bruker_dir / "nus_2d")
    base = generate_2d_nus_script(exp2n, in_file="e.fid", nuslist="nuslist", out_file="e.ft2")
    assert "-neg" not in base
    forced = generate_2d_nus_script(
        exp2n,
        in_file="e.fid",
        nuslist="nuslist",
        out_file="e.ft2",
        sampling={"ft_neg_f1": True},
    )
    assert "-neg" in forced
    # 2D uniform: likewise no add by default, only explicit
    exp2u = read_dataset(bruker_dir / "hsqc_2d")
    assert "-neg" not in generate_process_script(
        exp2u, select_method(exp2u), in_file="f.fid", out_file="o.ft2"
    )
    assert "-neg" in generate_process_script(
        exp2u,
        select_method(exp2u),
        in_file="f.fid",
        out_file="o.ft2",
        sampling={"ft_neg_f1": True},
    )
