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


def test_gui_saved_settings_are_loaded_by_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CONF-004:GUI 保存规范 schema,Backend 从同一文件读取。"""
    import yaml

    from backend import config as backend_config
    from gui import settings as gui_settings

    local = tmp_path / "nmrforge.local.yaml"
    monkeypatch.setattr(gui_settings, "_settings_path", lambda: local)
    monkeypatch.setattr(
        backend_config,
        "local_config_path",
        lambda filename="nmrforge.local.yaml": local,
    )
    gui_settings.save_settings(
        {
            "nmrpipe_path": "/opt/nmrpipe/bin",
            "linewidth_hz": {"1H": 9.5},
            "smile": {"nthread": 2},
        }
    )

    raw = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert "nmrpipe_path" not in raw
    assert "linewidth_hz" not in raw
    assert raw["backend"]["nmrpipe"]["path"] == "/opt/nmrpipe/bin"
    assert raw["processing"]["linewidth_hz"]["1H"] == 9.5

    defaults = backend_config.load_processing_defaults(backend_config.load_config())
    assert defaults["nmrpipe_path"] == "/opt/nmrpipe/bin"
    assert defaults["linewidth_hz"]["1H"] == 9.5
    assert defaults["nthread"] == 2


def test_gui_settings_migrate_legacy_top_level_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧配置可读,下一次保存移除旧共享键并写规范嵌套结构。"""
    import yaml

    from gui import settings as gui_settings

    local = tmp_path / "nmrforge.local.yaml"
    local.write_text(
        "nmrpipe_path: /legacy/bin\nlinewidth_hz:\n  1H: 11\n"
        "smile:\n  nthread: 2\n  thread_offset: 7\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gui_settings, "_settings_path", lambda: local)
    loaded = gui_settings.load_settings()
    assert loaded["nmrpipe_path"] == "/legacy/bin"
    assert loaded["linewidth_hz"]["1H"] == 11

    from backend import config as backend_config

    monkeypatch.setattr(
        backend_config,
        "local_config_path",
        lambda filename="nmrforge.local.yaml": local,
    )
    backend_defaults = backend_config.load_processing_defaults(
        backend_config.load_config()
    )
    assert backend_defaults["nmrpipe_path"] == "/legacy/bin"
    assert backend_defaults["linewidth_hz"]["1H"] == 11

    gui_settings.save_settings(loaded)
    raw = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert "nmrpipe_path" not in raw and "linewidth_hz" not in raw
    assert "thread_offset" not in raw["smile"]
    assert raw["backend"]["nmrpipe"]["path"] == "/legacy/bin"
    assert raw["processing"]["linewidth_hz"]["1H"] == 11


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


def test_zero_fill_plan_per_axis(bruker_dir: Path) -> None:
    """逐轴填零:裸标量 = k×TD(与全局同义);显式 size / 关闭逐轴生效。"""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    scalar_two = zero_fill_plan(exp, 2)
    per_axis_two = zero_fill_plan(exp, {"F1": 2})
    assert per_axis_two["F1"]["size"] == scalar_two["F1"]["size"]
    assert per_axis_two["F2"]["size"] == scalar_two["F2"]["size"]

    explicit = zero_fill_plan(exp, {"F1": {"mode": "size", "size": 512}})
    assert explicit["F1"]["size"] == 512

    off = zero_fill_plan(exp, {"F1": {"mode": "none"}})
    assert off["F1"]["mode"] == "none" and off["F1"]["size"] is None
    assert off["F2"]["size"] is not None  # 只关 F1,直接维仍按默认

    auto_f1 = zero_fill_plan(exp, {"F1": {"mode": "auto"}})
    assert auto_f1["F1"]["mode"] == "auto"


def test_zero_fill_plan_per_axis_points_per_line(bruker_dir: Path) -> None:
    """目标数字分辨率可逐轴给:points_per_line.F1 只影响该维 SI。"""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    base = zero_fill_plan(exp, points_per_line=2.0)
    fine = zero_fill_plan(exp, points_per_line={"F1": 4.0})
    assert fine["F1"]["size"] >= base["F1"]["size"]
    assert fine["F2"]["size"] == base["F2"]["size"]  # 直接维不随 ppl 变
    coarse = zero_fill_plan(exp, points_per_line={"F1": 1.0})
    assert coarse["F1"]["size"] <= base["F1"]["size"]
    # 非法值回退默认,不崩
    fallback = zero_fill_plan(exp, points_per_line={"F1": "abc"})
    assert fallback["F1"]["size"] == base["F1"]["size"]
