"""终跑直接维范围(final_ext_*)→ 优化/重构窗口的映射(0.2.199-补29hz-修17)。

用户 2026-09-11:「smile 优化没有用终脚本的直接维范围吗,为什么会显示内存不够,
我设置的范围是够的」——SMILE 优化/扫描直接调 reconstruct_nus,参数里带的是 GUI 的
`final_ext_lo`/`final_ext_hi`,不映射就退回默认宽窗(10.5-6.5),内存护栏按宽窗
估算 → 误报「内存不够」/被自动降直接维填零。
"""

from __future__ import annotations

from backend.nmrpipe_backend import apply_final_ext_params


def test_maps_final_range_to_ext() -> None:
    params = {"final_ext_lo": "8.5", "final_ext_hi": "7.5"}

    note = apply_final_ext_params(params)

    assert params["ext_lo"] == "8.5"
    assert params["ext_hi"] == "7.5"
    assert note and "ext_lo=8.5" in note and "ext_hi=7.5" in note


def test_partial_range_only_maps_given_side() -> None:
    """只填一端时只映射该端(另一端保持默认,与终跑同一语义)。"""
    params = {"final_ext_lo": "9.5"}

    note = apply_final_ext_params(params)

    assert params["ext_lo"] == "9.5"
    assert "ext_hi" not in params
    assert note and "ext_hi" not in note


def test_apply_ext_to_opt_off_keeps_default_window() -> None:
    """「仅终跑」(apply_ext_to_opt=0):优化/重构保持默认窗口,不做映射。"""
    params = {"final_ext_lo": "8.5", "final_ext_hi": "7.5", "apply_ext_to_opt": "0"}

    assert apply_final_ext_params(params) is None
    assert "ext_lo" not in params and "ext_hi" not in params


def test_explicit_ext_wins() -> None:
    """显式 ext_lo/ext_hi 优先,不被终跑范围覆盖。"""
    params = {"ext_lo": "11.0", "final_ext_lo": "8.5", "final_ext_hi": "7.5"}

    note = apply_final_ext_params(params)

    assert params["ext_lo"] == "11.0"  # 保持显式值
    assert params["ext_hi"] == "7.5"  # hi 仍来自终跑范围
    assert note and "ext_lo=" not in note


def test_no_final_range_is_noop() -> None:
    params: dict = {}
    assert apply_final_ext_params(params) is None
    assert params == {}

    blank = {"final_ext_lo": "", "final_ext_hi": "  "}
    assert apply_final_ext_params(blank) is None
    assert "ext_lo" not in blank and "ext_hi" not in blank
