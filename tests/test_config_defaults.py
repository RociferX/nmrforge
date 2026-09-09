"""配置默认值测试(0.2.46):读取/覆盖优先级/无效值回退。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import (
    load_processing_defaults,
    resolve_nthread,
    resolve_points_per_line,
)
from backend.script_generator import effective_td, zero_fill_plan
from core.data.bruker_reader import read_dataset


def _auto_nthread_expected(config=None) -> int:
    import os

    # 0.2.199-补24 起 thread_offset 可配置(本地 config 可设非 2,如 VM 2 线程
    # 约束);期望值必须与 backend.config._auto_nthread 同源——读同一
    # load_config(config) 的 smile.thread_offset(0.2.199-补29ekb)。
    from backend.config import DEFAULT_THREAD_OFFSET, _as_int, load_config

    smile = load_config(config).get("smile") or {}
    offset = _as_int(smile.get("thread_offset"), DEFAULT_THREAD_OFFSET)
    return max(1, (os.cpu_count() or 4) - offset)


def test_load_processing_defaults_empty_config() -> None:
    defaults = load_processing_defaults({})
    assert defaults["points_per_line"] == 2.0
    assert defaults["nthread"] == 2
    assert defaults["nmrpipe_path"] == ""
    assert isinstance(defaults["linewidth_hz"], dict)


def test_load_processing_defaults_from_config() -> None:
    cfg = {
        "processing": {
            "linewidth_hz": {"1H": 10.0, "15N": 12.0},
            "points_per_line": 3.0,
        },
        "smile": {"nthread": 4},
        "backend": {"nmrpipe": {"path": "/opt/nmrpipe"}},
    }
    defaults = load_processing_defaults(cfg)
    assert defaults["linewidth_hz"]["1H"] == 10.0
    assert defaults["linewidth_hz"]["15N"] == 12.0
    assert defaults["points_per_line"] == 3.0
    assert defaults["nthread"] == 4
    assert defaults["nmrpipe_path"] == "/opt/nmrpipe"


def test_load_processing_defaults_invalid_fallback() -> None:
    cfg = {
        "processing": {
            "linewidth_hz": {"1H": "abc", "13C": -5},
            "points_per_line": -3,
        },
        "smile": {"nthread": 0},
        "backend": {"nmrpipe": {"path": 123}},
    }
    defaults = load_processing_defaults(cfg)
    assert defaults["linewidth_hz"]["1H"] == 8.0  # 无效 → 核素默认
    assert defaults["linewidth_hz"]["13C"] == 20.0
    assert defaults["points_per_line"] == 2.0
    assert defaults["nthread"] == 2
    assert defaults["nmrpipe_path"] == "123"


def test_resolve_helpers() -> None:
    assert resolve_points_per_line(None) == 2.0
    assert resolve_points_per_line(4.0) == 4.0
    assert resolve_points_per_line("abc") == 2.0
    assert resolve_nthread(None) == 2
    assert resolve_nthread(4) == 4
    assert resolve_nthread(0) == 2


def test_zero_fill_plan_uses_config_defaults(
    bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未显式传参时读取配置(线宽/点距);显式 params 优先。"""
    from backend import config as config_mod

    exp = read_dataset(bruker_dir / "nus_2d")
    cfg_defaults: dict = {
        "linewidth_hz": {"1H": 8.0, "15N": 15.0, "13C": 20.0},
        "points_per_line": 2.0,
        "nthread": 2,
        "nmrpipe_path": "",
    }
    monkeypatch.setattr(
        config_mod, "load_processing_defaults", lambda *a, **k: dict(cfg_defaults)
    )
    plan = zero_fill_plan(exp)
    td = effective_td(exp)
    # 0.2.199-补29dq(用户):NUS 直接维填零默认 2×TD(与 uniform 一致);
    # 内存不足由内存护栏降为 1×TD 并提示,仍不足才报内存不够
    assert plan["F2"]["size"] == 1 << max(0, int(2 * td[0]) - 1).bit_length()
    # 均匀路径保持 2×TD
    uniform = read_dataset(bruker_dir / "hsqc_2d")
    plan_uniform = zero_fill_plan(uniform)
    td_u = effective_td(uniform)
    assert plan_uniform["F2"]["size"] == 1 << max(0, int(2 * td_u[0]) - 1).bit_length()
    # 点距更细(ppl 4.0)→ 目标 SI 更大或相等(单调)
    cfg_defaults["points_per_line"] = 4.0
    plan_fine = zero_fill_plan(exp)
    assert plan_fine["F1"]["size"] >= plan["F1"]["size"]
    # 显式 params 优先:ppl=1.0 覆盖配置 4.0 → SI 更小或相等
    plan_explicit = zero_fill_plan(exp, points_per_line=1.0)
    assert plan_explicit["F1"]["size"] <= plan_fine["F1"]["size"]
    # 线宽配置:15N 线宽越宽 → 目标点距越粗 → SI 越小或相等
    cfg_defaults["linewidth_hz"] = {"15N": 60.0}
    wide = zero_fill_plan(exp)
    cfg_defaults["linewidth_hz"] = {"15N": 5.0}
    narrow = zero_fill_plan(exp)
    assert wide["F1"]["size"] <= narrow["F1"]["size"]


def test_factory_passes_config_nmrpipe_path(tmp_path: Path) -> None:
    """factory 把 config 的 nmrpipe.path 传给 NMRPipeBackend(nmrpipe_bin)。"""
    from backend.factory import create_backend

    backend = create_backend(
        {
            "backend": {
                "provider": "nmrpipe",
                "nmrpipe": {"path": str(tmp_path)},
            }
        }
    )
    assert backend.nmrpipe_bin == str(tmp_path)
