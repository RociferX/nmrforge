"""Config default tests (0.2.46): reading / override precedence / invalid-value fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import (
    load_processing_defaults,
    resolve_ext_window,
    resolve_nthread,
    resolve_points_per_line,
    smile_thread_limit,
)
from backend.script_generator import effective_td, zero_fill_plan
from core.data.bruker_reader import read_dataset


def test_load_processing_defaults_empty_config() -> None:
    defaults = load_processing_defaults({})
    assert defaults["points_per_line"] == 2.0
    assert defaults["nthread"] == min(2, smile_thread_limit())
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
    assert defaults["nthread"] == min(4, smile_thread_limit())
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
    assert defaults["linewidth_hz"]["1H"] == 8.0  # invalid → nucleus default
    assert defaults["linewidth_hz"]["13C"] == 20.0
    assert defaults["points_per_line"] == 2.0
    assert defaults["nthread"] == min(2, smile_thread_limit())
    assert defaults["nmrpipe_path"] == "123"


def test_resolve_helpers() -> None:
    assert resolve_points_per_line(None) == 2.0
    assert resolve_points_per_line(4.0) == 4.0
    assert resolve_points_per_line("abc") == 2.0
    limit = smile_thread_limit()
    assert resolve_nthread(None) == min(2, limit)
    assert resolve_nthread(4) == min(4, limit)  # machine cap = cores-2 (CI 4 cores → 2)
    assert resolve_nthread(0) == min(2, limit)


def test_non_proton_direct_dimension_defaults_to_acquired_sweep() -> None:
    "Regression coverage: test non proton direct dimension defaults to acquired sweep."
    from core.data.internal_data_model import AxisRole, Dimension, Experiment

    experiment = Experiment(
        dataset_id="hcc",
        source_path=Path("."),
        dimensions=[
            Dimension(
                "F2",
                "13C",
                sf=150.0,
                sw=27000.0,
                o1p=100.0,
                td=1024,
                role=AxisRole.DIRECT,
            )
        ],
    )

    assert resolve_ext_window(experiment) == ("190", "10")
    assert resolve_ext_window(experiment, "120", "80") == ("120", "80")


def test_smile_thread_limit_follows_core_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thread cap = cores-2; ≤3 cores allow only 1 (user, 2026-09-09); an unreadable core
    count falls back to 4 cores.

    This clamp is product behaviour, so expectations must be computed from the cap: a hosted
    CI runner has only 4 cores, and hard-coding 4 fails on CI (measured 2026-09-17).
    """
    import os as os_mod

    monkeypatch.setattr(os_mod, "cpu_count", lambda: 8)
    assert smile_thread_limit() == 6
    monkeypatch.setattr(os_mod, "cpu_count", lambda: 3)
    assert smile_thread_limit() == 1
    monkeypatch.setattr(os_mod, "cpu_count", lambda: None)
    assert smile_thread_limit() == 2


def test_zero_fill_plan_uses_config_defaults(
    bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read config (linewidth / points per line) when no args are given; explicit params win."""
    from backend import config as config_mod

    exp = read_dataset(bruker_dir / "nus_2d")
    cfg_defaults: dict = {
        "linewidth_hz": {"1H": 8.0, "15N": 15.0, "13C": 20.0},
        "points_per_line": 2.0,
        "nthread": 2,
        "nmrpipe_path": "",
    }
    monkeypatch.setattr(config_mod, "load_processing_defaults", lambda *a, **k: dict(cfg_defaults))
    plan = zero_fill_plan(exp)
    td = effective_td(exp)
    # 0.2.199-patch29dq (user): NUS direct-dimension zero fill defaults to 2xTD (same as
    # uniform); the memory guard drops it to 1xTD with a hint, and only reports low memory
    # if that is still not enough
    assert plan["F2"]["size"] == 1 << max(0, int(2 * td[0]) - 1).bit_length()
    # the uniform path keeps 2xTD
    uniform = read_dataset(bruker_dir / "hsqc_2d")
    plan_uniform = zero_fill_plan(uniform)
    td_u = effective_td(uniform)
    assert plan_uniform["F2"]["size"] == 1 << max(0, int(2 * td_u[0]) - 1).bit_length()
    # finer point spacing (ppl 4.0) → larger or equal target SI (monotonic)
    cfg_defaults["points_per_line"] = 4.0
    plan_fine = zero_fill_plan(exp)
    assert plan_fine["F1"]["size"] >= plan["F1"]["size"]
    # explicit params win: ppl=1.0 overrides config 4.0 → smaller or equal SI
    plan_explicit = zero_fill_plan(exp, points_per_line=1.0)
    assert plan_explicit["F1"]["size"] <= plan_fine["F1"]["size"]
    # linewidth config: wider 15N linewidth → coarser target spacing → smaller or equal SI
    cfg_defaults["linewidth_hz"] = {"15N": 60.0}
    wide = zero_fill_plan(exp)
    cfg_defaults["linewidth_hz"] = {"15N": 5.0}
    narrow = zero_fill_plan(exp)
    assert wide["F1"]["size"] <= narrow["F1"]["size"]


def test_gui_saved_settings_are_loaded_by_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CONF-004: the GUI saves the canonical schema; the backend reads the same file."""
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

    # SMILE thread count is clamped by smile_thread_limit() to cores-2 (≤3 cores give 1); CI
    # runners have only 2-4 cores, so this assertion needs a pinned core count (CI red 2026-09-22)
    monkeypatch.setattr(backend_config.os, "cpu_count", lambda: 8)
    defaults = backend_config.load_processing_defaults(backend_config.load_config())
    assert defaults["nmrpipe_path"] == "/opt/nmrpipe/bin"
    assert defaults["linewidth_hz"]["1H"] == 9.5
    assert defaults["nthread"] == 2


def test_gui_settings_migrate_legacy_top_level_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy config stays readable; the next save drops the old shared keys and writes the
    canonical nested structure."""
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
    backend_defaults = backend_config.load_processing_defaults(backend_config.load_config())
    assert backend_defaults["nmrpipe_path"] == "/legacy/bin"
    assert backend_defaults["linewidth_hz"]["1H"] == 11

    gui_settings.save_settings(loaded)
    raw = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert "nmrpipe_path" not in raw and "linewidth_hz" not in raw
    assert "thread_offset" not in raw["smile"]
    assert raw["backend"]["nmrpipe"]["path"] == "/legacy/bin"
    assert raw["processing"]["linewidth_hz"]["1H"] == 11


def test_gui_saved_language_preference_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The UI language is a scalar key: the value chosen in settings must reach disk.

    2026-09-21 (user, measured): ``_merged_view`` used to merge only dict keys, while
    ``language`` is a string, so ``save_settings({"language": "en"})`` wrote the old on-disk
    value back -- changing the language in settings had no effect after a restart.
    """
    import yaml

    from gui import settings as gui_settings
    from ui_support import i18n

    local = tmp_path / "nmrforge.local.yaml"
    monkeypatch.setattr(gui_settings, "_settings_path", lambda: local)

    gui_settings.save_settings({"language": "en", "nmrpipe_path": "/opt/bin"})
    raw = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert raw["language"] == "en"
    assert gui_settings.load_settings()["language"] == "en"
    assert i18n.read_user_preference(local) == "en"  # the language layer reads the same value

    # switching back to Chinese works too; an invalid value falls back to auto (follow the
    # system) rather than being written through
    gui_settings.save_settings({"language": "zh"})
    assert gui_settings.load_settings()["language"] == "zh"
    gui_settings.save_settings({"language": "de"})
    assert gui_settings.load_settings()["language"] == "auto"

    # a save without language (e.g. the first-import prompt updating the guide) must not wipe
    # the chosen language
    gui_settings.save_settings({"language": "en"})
    gui_settings.save_settings({"nmrpipe_path": "/opt/bin2"})
    assert gui_settings.load_settings()["language"] == "en"
    assert gui_settings.load_settings()["nmrpipe_path"] == "/opt/bin2"


def test_language_value_accepts_locale_style_spellings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Locale-style spellings (zh_CN / zh-Hans / en_US.UTF-8) must show the effective entry
    in settings.

    2026-09-21 review: the language layer recognises these spellings via
    ``normalize_language``, while the settings dialog only does strict whitelist matching, so
    it displayed "follow the system language"; changing any other setting and saving then
    rewrote ``language: zh_CN`` to ``auto``, silently changing the language.
    """
    from gui import settings as gui_settings

    local = tmp_path / "nmrforge.local.yaml"
    monkeypatch.setattr(gui_settings, "_settings_path", lambda: local)
    for raw, expected in (
        ("zh_CN", "zh"),
        ("zh-Hans", "zh"),
        ("en_US.UTF-8", "en"),
        ("auto", "auto"),
        ("de", "auto"),  # unsupported → follow the system, but write back auto not the raw value
    ):
        local.write_text(f"language: {raw}\n", encoding="utf-8")
        assert gui_settings.load_settings()["language"] == expected, raw

    # changing only other settings must not rewrite the effective zh_CN to auto
    local.write_text("language: zh_CN\n", encoding="utf-8")
    gui_settings.save_settings({"linewidth_hz": {"1H": 9.0}})
    assert gui_settings.load_settings()["language"] == "zh"
    assert "language: zh" in local.read_text(encoding="utf-8")


def test_settings_and_ui_state_writes_go_through_the_atomic_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User config and per-data UI state use the atomic helper like other records (not a plain
    overwrite)."""
    import core.project.manager as manager
    from gui import per_data_records
    from gui import settings as gui_settings

    calls: list[Path] = []
    real_text, real_json = manager.atomic_write_text, manager.atomic_write_json

    def _spy_text(path, body):
        calls.append(Path(path))
        return real_text(path, body)

    monkeypatch.setattr(manager, "atomic_write_text", _spy_text)

    def _spy_json(path, data):
        calls.append(Path(path))
        return real_json(path, data)

    monkeypatch.setattr(manager, "atomic_write_json", _spy_json)
    monkeypatch.setattr(gui_settings, "_settings_path", lambda: tmp_path / "nmrforge.local.yaml")

    gui_settings.save_settings({"nmrpipe_path": "/opt/bin"})
    assert calls and calls[-1].name == "nmrforge.local.yaml"
    assert not list(tmp_path.glob("*.tmp"))  # no temp files left behind in the directory

    state_path = tmp_path / "ui_state.json"
    monkeypatch.setattr(per_data_records, "ui_state_path", lambda *a, **k: state_path)
    monkeypatch.setattr(per_data_records, "data_is_trashed", lambda *a, **k: False)
    assert per_data_records.save_ui_state(object(), "exp_001", "d_001", {"threshold": 5}) is True
    assert calls[-1] == state_path
    assert "threshold" in state_path.read_text(encoding="utf-8")


def test_factory_passes_config_nmrpipe_path(tmp_path: Path) -> None:
    """The factory passes config's nmrpipe.path to NMRPipeBackend (nmrpipe_bin)."""
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
    """Per-axis zero fill: a bare scalar = kxTD (same as global); explicit size and disabling
    work per axis."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    scalar_two = zero_fill_plan(exp, 2)
    per_axis_two = zero_fill_plan(exp, {"F1": 2})
    assert per_axis_two["F1"]["size"] == scalar_two["F1"]["size"]
    assert per_axis_two["F2"]["size"] == scalar_two["F2"]["size"]

    explicit = zero_fill_plan(exp, {"F1": {"mode": "size", "size": 512}})
    assert explicit["F1"]["size"] == 512

    off = zero_fill_plan(exp, {"F1": {"mode": "none"}})
    assert off["F1"]["mode"] == "none" and off["F1"]["size"] is None
    assert off["F2"]["size"] is not None  # only F1 is off; direct dimension keeps the default

    auto_f1 = zero_fill_plan(exp, {"F1": {"mode": "auto"}})
    assert auto_f1["F1"]["mode"] == "auto"


def test_zero_fill_plan_per_axis_points_per_line(bruker_dir: Path) -> None:
    """Target digital resolution can be given per axis: points_per_line.F1 affects only that
    dimension's SI."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    base = zero_fill_plan(exp, points_per_line=2.0)
    fine = zero_fill_plan(exp, points_per_line={"F1": 4.0})
    assert fine["F1"]["size"] >= base["F1"]["size"]
    assert fine["F2"]["size"] == base["F2"]["size"]  # direct dimension does not follow ppl
    coarse = zero_fill_plan(exp, points_per_line={"F1": 1.0})
    assert coarse["F1"]["size"] <= base["F1"]["size"]
    # invalid values fall back to the default without crashing
    fallback = zero_fill_plan(exp, points_per_line={"F1": "abc"})
    assert fallback["F1"]["size"] == base["F1"]["size"]
