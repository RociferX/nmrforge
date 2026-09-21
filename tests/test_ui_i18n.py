"""Guards for the run-time interface language (2026-09-21).

Model: the code writes the **English original** and wraps it in ``tr()`` (the public English
tree and the private repository share one code base, so the English tree carries no
Chinese); Chinese lives in ``ui_support/locales/zh.json`` (key = the English original).
This file guards:

1. ``tr()`` is the identity in English (the source language) and substitutes from the
   catalogue in Chinese, with ``str.format`` placeholders;
2. where the language comes from: explicit setting > environment > injected system language
   > the source language (English); an unknown language falls back to the source;
3. ``ui_support/locales/source.json`` matches the ``tr()`` strings in the code (a new string
   must be registered first);
4. files that have been converted must not contain Chinese literals outside ``tr()``
   (a ratchet: it only grows);
5. every key in the Chinese catalogue must be a ``tr()`` string that really exists (no dead
   keys left behind by a rename).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from ui_support import i18n

ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "scripts" / "i18n_extract_ui.py"
LOCALES = ROOT / "ui_support" / "locales"


def _tool() -> Any:
    """Load the extraction tool by path (it is not a package module)."""
    spec = importlib.util.spec_from_file_location("i18n_extract_ui", TOOL_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _restore_language(monkeypatch: pytest.MonkeyPatch):
    """Reset the language state and the catalogue cache after every test."""
    saved_language = i18n._language
    saved_resolver = i18n._system_resolver
    for key in ("NMRFORGE_LANG", "NMRFORGE_LANGUAGE", "LANG", "LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(key, raising=False)
    i18n.reset_cache()
    yield
    i18n._language = saved_language
    i18n._system_resolver = saved_resolver
    i18n.reset_cache()


def test_english_is_the_source_language() -> None:
    """Without a Chinese entry the English original comes back (identity in the source)."""
    i18n.set_language("en")
    assert i18n.tr("Determined") == "Determined"
    i18n.set_language("zh")
    assert i18n.tr("Determined") == "Determined"
    assert "Determined" in i18n.missing_keys()


def test_chinese_catalogue_is_used() -> None:
    """In Chinese the catalogue is substituted."""
    i18n._catalogues["zh"] = {"Determined": "确定"}
    i18n.set_language("zh")
    assert i18n.tr("Determined") == "确定"
    assert i18n.missing_keys() == []


def test_tr_interpolates_placeholders() -> None:
    """Placeholders go through str.format: a translation may reorder them."""
    i18n._catalogues["zh"] = {"Workspace: {p0}": "工作区: {p0}"}
    i18n.set_language("zh")
    assert i18n.tr("Workspace: {p0}", p0="/tmp/x") == "工作区: /tmp/x"


def test_language_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit env and the system locale both normalise to zh / en; else the tree default."""
    monkeypatch.setenv("NMRFORGE_LANG", "zh_CN.UTF-8")
    assert i18n.language_from_environment() == "zh"
    assert i18n.get_language() == "zh"
    monkeypatch.delenv("NMRFORGE_LANG")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    # The system locale is not an explicit pin: it ranks below the settings preference
    assert i18n.language_from_environment() is None
    assert i18n.language_from_locale_environment() == "en"
    i18n.set_system_language_resolver(lambda: None)
    assert i18n.get_language() == "en"
    monkeypatch.delenv("LANG")
    assert i18n.language_from_locale_environment() is None
    i18n.set_system_language_resolver(lambda: "zh-CN")
    assert i18n.get_language() == "zh"
    i18n.set_system_language_resolver(lambda: None)
    assert i18n.get_language() == i18n.default_language() == "en"


def test_explicit_language_wins_over_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit setting beats the environment; an unknown language falls back."""
    monkeypatch.setenv("NMRFORGE_LANG", "zh")
    assert i18n.set_language("en") == "en"
    assert i18n.tr("Determined") == "Determined"
    monkeypatch.delenv("NMRFORGE_LANG")
    i18n.set_system_language_resolver(lambda: None)  # 别让别的用例留下的系统语言解析器影响判定
    assert i18n.set_language("de") == i18n.default_language()


def test_user_preference_from_config(tmp_path: Path) -> None:
    """The ``language`` key in the user config can pin the language; auto/unsupported/broken
    files all fall back to "follow the system"."""
    config = tmp_path / "nmrforge.local.yaml"
    config.write_text("language: zh_CN\n", encoding="utf-8")
    assert i18n.read_user_preference(config) == "zh"
    config.write_text("language: auto\n", encoding="utf-8")
    assert i18n.read_user_preference(config) is None
    config.write_text("language: de\n", encoding="utf-8")  # unsupported -> follow the system
    assert i18n.read_user_preference(config) is None
    config.write_text("language: [bad yaml\n", encoding="utf-8")
    assert i18n.read_user_preference(config) is None
    assert i18n.read_user_preference(tmp_path / "missing.yaml") is None


def test_user_preference_sits_between_environment_and_system(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolver order: explicit setting > NMRFORGE_LANG > the settings preference > system.

    The environment variable deliberately comes first: the README and the packaging docs use
    it to pin the language for one run, and a stale setting must not override that.
    """
    config = tmp_path / "nmrforge.local.yaml"
    config.write_text("language: en\n", encoding="utf-8")
    monkeypatch.setattr("core.app_paths.local_config_path", lambda *a, **k: config)
    i18n.reset_cache()
    i18n.set_language(None)
    i18n.set_system_language_resolver(lambda: "zh-CN")
    assert i18n.get_language() == "en"  # the setting beats the system language
    monkeypatch.setenv("NMRFORGE_LANG", "zh")
    assert i18n.get_language() == "zh"  # the environment beats the setting
    monkeypatch.delenv("NMRFORGE_LANG")
    assert i18n.set_language("en") == "en"  # an explicit setting wins outright
    monkeypatch.setenv("NMRFORGE_LANG", "zh")
    assert i18n.get_language() == "en"


def test_system_locale_does_not_override_the_user_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``LANG`` / ``LC_ALL`` are the system locale and must not beat the settings choice.

    Almost every Linux machine runs a supported ``en_US.UTF-8``; ranking it above the user
    setting would make "Settings -> Interface language = Chinese" a no-op (the same "I changed
    it and nothing happened" symptom as the config-persistence bug).
    """
    config = tmp_path / "nmrforge.local.yaml"
    config.write_text("language: zh\n", encoding="utf-8")
    monkeypatch.setattr("core.app_paths.local_config_path", lambda *a, **k: config)
    for key in ("NMRFORGE_LANG", "NMRFORGE_LANGUAGE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    i18n.reset_cache()
    i18n.set_language(None)
    i18n.set_system_language_resolver(lambda: "en-US")
    try:
        assert i18n.get_language() == "zh"  # the setting beats the locale and QLocale
        monkeypatch.setenv("NMRFORGE_LANG", "en")
        assert i18n.get_language() == "en"  # an explicit pin still wins
    finally:
        i18n.set_system_language_resolver(None)
        i18n.reset_cache()


def test_chinese_catalogue_keys_are_registered() -> None:
    """Every key in zh.json must be a tr() string that exists (no dead keys after renames)."""
    source = json.loads((LOCALES / "source.json").read_text(encoding="utf-8"))
    catalogue = json.loads((LOCALES / "zh.json").read_text(encoding="utf-8"))
    unknown = sorted(set(catalogue) - set(source["keys"]))
    assert not unknown, f"zh.json has keys that are not registered: {unknown[:5]}"


def test_ui_string_snapshot_is_current() -> None:
    """The tr() strings and the converted-file list must match the snapshot (run --write)."""
    assert _tool().check() == 0
