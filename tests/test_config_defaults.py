"""Configure default value test(0.2.46):read/Override priority/Fallback on invalid value."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import (
    load_processing_defaults,
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
    assert defaults["linewidth_hz"]["1H"] == 8.0  # Invalid -> nuclide default.
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
    # Machine upper limit = number of cores - 2 (CI 4 cores -> 2).
    assert resolve_nthread(4) == min(4, limit)
    assert resolve_nthread(0) == min(2, limit)


def test_smile_thread_limit_follows_core_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The upper limit of threads = number of cores - 2, <= 3 cores only allow 1 (2026-09-09 user);
    if the core number cannot be read, use 4 cores. This clamp is a product behaviour, so the
    expected value must be calculated according to the upper limit: CI The managed runner only
    has 4 cores, and hardcoding 4 will fail on CI (measured on 2026-09-17)."""
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
    """Read the configuration (Line width/point distance) when parameters are not passed
    explicitly; explicit params take precedence."""
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
    # 0.2.199-patch29dq(user):NUS direct dimension zero filling default 2 x TD (consistent with
    # uniform); insufficient memory is reduced from memory guard to 1 x TD and prompts, if it is
    # still insufficient, it will report insufficient memory.
    assert plan["F2"]["size"] == 1 << max(0, int(2 * td[0]) - 1).bit_length()
    # Uniform paths remain 2 x TD.
    uniform = read_dataset(bruker_dir / "hsqc_2d")
    plan_uniform = zero_fill_plan(uniform)
    td_u = effective_td(uniform)
    assert plan_uniform["F2"]["size"] == 1 << max(0, int(2 * td_u[0]) - 1).bit_length()
    # Point pitch is finer (ppl 4.0) -> target SI is larger or equal (monotone).
    cfg_defaults["points_per_line"] = 4.0
    plan_fine = zero_fill_plan(exp)
    assert plan_fine["F1"]["size"] >= plan["F1"]["size"]
    # Explicit params take precedence: ppl=1.0 override configuration 4.0 -> SI smaller or equal.
    plan_explicit = zero_fill_plan(exp, points_per_line=1.0)
    assert plan_explicit["F1"]["size"] <= plan_fine["F1"]["size"]
    # Line width configuration: 15N The wider the line width -> The thicker the target point
    # distance -> SI is smaller or equal.
    cfg_defaults["linewidth_hz"] = {"15N": 60.0}
    wide = zero_fill_plan(exp)
    cfg_defaults["linewidth_hz"] = {"15N": 5.0}
    narrow = zero_fill_plan(exp)
    assert wide["F1"]["size"] <= narrow["F1"]["size"]


def test_gui_saved_settings_are_loaded_by_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CONF-004:GUI Save the specification schema and Backend reads from the same file."""
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
    """The old configuration is readable. The next save removes the old shared keys and writes the
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


def test_gui_saved_language_preference_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interface language is a scalar key: the value picked in the settings must hit disk.

    2026-09-21 (reported by the user): ``_merged_view`` used to merge dict-typed keys only, and
    ``language`` is a string, so ``save_settings({"language": "en"})`` wrote the previous value
    back to the file -- the symptom was "I changed the language, restarted, and it is unchanged".
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

    # Switching back works too; an unsupported value falls back to auto (follow the system)
    gui_settings.save_settings({"language": "zh"})
    assert gui_settings.load_settings()["language"] == "zh"
    gui_settings.save_settings({"language": "de"})
    assert gui_settings.load_settings()["language"] == "auto"

    # A save without "language" (e.g. the first-import hint updating "guide") must not drop it
    gui_settings.save_settings({"language": "en"})
    gui_settings.save_settings({"nmrpipe_path": "/opt/bin2"})
    assert gui_settings.load_settings()["language"] == "en"
    assert gui_settings.load_settings()["nmrpipe_path"] == "/opt/bin2"


def test_language_value_accepts_locale_style_spellings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Locale-style spellings (zh_CN / zh-Hans / en_US.UTF-8) must show as the effective choice.

    The language layer honours them through ``normalize_language``, but the dialog used a strict
    allow-list match, so it displayed "follow the system"; saving any unrelated setting then
    rewrote ``language: zh_CN`` to ``auto`` and silently changed the interface language.
    """
    from gui import settings as gui_settings

    local = tmp_path / "nmrforge.local.yaml"
    monkeypatch.setattr(gui_settings, "_settings_path", lambda: local)
    for raw, expected in (
        ("zh_CN", "zh"),
        ("zh-Hans", "zh"),
        ("en_US.UTF-8", "en"),
        ("auto", "auto"),
        ("de", "auto"),  # unsupported -> follow the system, but stored back as "auto"
    ):
        local.write_text(f"language: {raw}\n", encoding="utf-8")
        assert gui_settings.load_settings()["language"] == expected, raw

    # Changing another setting must not rewrite an effective zh_CN into auto
    local.write_text("language: zh_CN\n", encoding="utf-8")
    gui_settings.save_settings({"linewidth_hz": {"1H": 9.0}})
    assert gui_settings.load_settings()["language"] == "zh"
    assert "language: zh" in local.read_text(encoding="utf-8")


def test_settings_and_ui_state_writes_go_through_the_atomic_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user config and the per-data UI state use atomic replacement like every other record."""
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
    monkeypatch.setattr(
        gui_settings, "_settings_path", lambda: tmp_path / "nmrforge.local.yaml"
    )

    gui_settings.save_settings({"nmrpipe_path": "/opt/bin"})
    assert calls and calls[-1].name == "nmrforge.local.yaml"
    assert not list(tmp_path.glob("*.tmp"))  # no temporary file left behind

    state_path = tmp_path / "ui_state.json"
    monkeypatch.setattr(per_data_records, "ui_state_path", lambda *a, **k: state_path)
    monkeypatch.setattr(per_data_records, "data_is_trashed", lambda *a, **k: False)
    assert per_data_records.save_ui_state(object(), "exp_001", "d_001", {"threshold": 5}) is True
    assert calls[-1] == state_path
    assert "threshold" in state_path.read_text(encoding="utf-8")


def test_factory_passes_config_nmrpipe_path(tmp_path: Path) -> None:
    """Factory passes nmrpipe.path of config to NMRPipeBackend(nmrpipe_bin)."""
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
    """Axis-by-axis zero filling: naked scalar = k x TD (synonymous with global); explicit size /
    turns off the axis-by-axis effect."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    scalar_two = zero_fill_plan(exp, 2)
    per_axis_two = zero_fill_plan(exp, {"F1": 2})
    assert per_axis_two["F1"]["size"] == scalar_two["F1"]["size"]
    assert per_axis_two["F2"]["size"] == scalar_two["F2"]["size"]

    explicit = zero_fill_plan(exp, {"F1": {"mode": "size", "size": 512}})
    assert explicit["F1"]["size"] == 512

    off = zero_fill_plan(exp, {"F1": {"mode": "none"}})
    assert off["F1"]["mode"] == "none" and off["F1"]["size"] is None
    assert off["F2"]["size"] is not None  # Only turn off F1, direct dimension remains as default.

    auto_f1 = zero_fill_plan(exp, {"F1": {"mode": "auto"}})
    assert auto_f1["F1"]["mode"] == "auto"


def test_zero_fill_plan_per_axis_points_per_line(bruker_dir: Path) -> None:
    """The target number resolution can be given axis by axis: points_per_line.F1 only affects this
    dimension SI."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    base = zero_fill_plan(exp, points_per_line=2.0)
    fine = zero_fill_plan(exp, points_per_line={"F1": 4.0})
    assert fine["F1"]["size"] >= base["F1"]["size"]
    assert fine["F2"]["size"] == base["F2"]["size"]  # Direct dimension does not change with ppl.
    coarse = zero_fill_plan(exp, points_per_line={"F1": 1.0})
    assert coarse["F1"]["size"] <= base["F1"]["size"]
    # If an illegal value returns to the default value, it will not crash.
    fallback = zero_fill_plan(exp, points_per_line={"F1": "abc"})
    assert fallback["F1"]["size"] == base["F1"]["size"]
