"""Runtime interface language: the lookup layer for interface text (Qt-free).

Model (since 2026-09-21):

- **the source language is English** - the code writes ``tr("English text")``; the public
  English tree and the private repository share one code base, so the public tree never
  contains Chinese;
- Chinese comes from ``ui_support/locales/zh.json``, one entry per string, **keyed by the
  English original**; ``ui_support/locales/en.json`` need not carry anything (English is the
  original, so looking it up is the identity);
- the language this tree defaults to lives in ``ui_support/locales/default.json`` (private
  repository = zh, public English tree = en);
- a key with no translation falls back to the English original: nothing goes blank and
  nothing raises;
- where the language comes from, in order: ``set_language()`` -> the environment variables
  ``NMRFORGE_LANG`` / ``NMRFORGE_LANGUAGE`` -> **the preference in the user config**
  (``language: auto|zh|en`` in ``nmrforge.local.yaml``, written by the GUI's software
  settings; ``auto`` means "do not take part") -> the system-language resolver the caller
  injects (``set_system_language_resolver``; the GUI injects QLocale) -> ``LANG`` /
  ``LC_ALL`` -> the default declared in :data:`DEFAULT_LANGUAGE_PATH` (private repository =
  zh, public English tree = en; the source language when that file is missing).
  ``NMRFORGE_LANG`` / ``NMRFORGE_LANGUAGE`` come **before** the user config on purpose: they
  are the documented way to pin the language for one run (``NMRFORGE_LANG=zh
  ./NMRForge.AppImage``) and should not be quietly overridden by a stale setting; use the GUI
  setting to change the language for good. ``LANG`` / ``LC_ALL`` / ``LC_MESSAGES`` are only the
  **system locale** and come *after* the setting - otherwise the ``LANG=en_US.UTF-8`` that
  almost every Linux machine has would silently defeat "Settings -> Interface language =
  Chinese" (measured 2026-09-21: a config saying ``language: zh`` still resolved to ``en``).

This layer does not depend on Qt: it works headless (tests, command line). The GUI injects
the system language once at start-up and, when the language is not English, sets the
``QApplication`` interface language as well.

Tooling: ``scripts/i18n_extract_ui.py`` extracts the ``tr()`` literals, watches the Chinese
coverage and refuses bare Chinese literals in files that have already been converted.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

#: The source language (the language the interface strings are written in). Both trees write
#: their code in English.
SOURCE_LANGUAGE = "en"
#: The default language of one tree (**not** counting environment and system language):
#: private repository / Chinese edition = zh, public English tree = en.
DEFAULT_LANGUAGE_PATH = Path(__file__).resolve().parent / "locales" / "default.json"
#: Supported language codes -> catalogue file name. Anything else falls back to the source.
SUPPORTED_LANGUAGES: dict[str, str] = {"en": "en.json", "zh": "zh.json"}
#: Key in the user config (``nmrforge.local.yaml``) that selects the interface language;
#: written by the GUI's software settings.
USER_CONFIG_KEY = "language"
#: The values that key may take; ``auto`` (or a missing/invalid value) follows the system.
USER_CHOICES: tuple[str, ...] = ("auto", *SUPPORTED_LANGUAGES)
#: Catalogue directory (inside the package, so it ships with the code).
CATALOGUE_DIR = Path(__file__).resolve().parent / "locales"

#: Explicit pins (temporary; they come before the user setting)
_ENV_KEYS = ("NMRFORGE_LANG", "NMRFORGE_LANGUAGE")
#: System-locale variables (equivalent to "follow the system"; they come after the user setting)
_LOCALE_ENV_KEYS = ("LC_ALL", "LC_MESSAGES", "LANG")

_language: str | None = None
_catalogues: dict[str, dict[str, str]] = {}
_missing: set[str] = set()
_system_resolver: Callable[[], str | None] | None = None
_user_preference: str | None = None
_user_preference_loaded = False


def normalize_language(code: str | None) -> str | None:
    """Reduce ``zh_CN`` / ``en-US`` / ``en_US.UTF-8`` to a supported language code."""
    if not code:
        return None
    text = str(code).strip().replace("-", "_")
    if not text:
        return None
    for part in (text, text.split("_")[0], text.split(".")[0].split("_")[0]):
        key = part.lower()
        if key in SUPPORTED_LANGUAGES:
            return key
    return None


def catalogue_path(language: str) -> Path:
    """Catalogue path (the caller decides whether a missing file is an error)."""
    name = SUPPORTED_LANGUAGES.get(normalize_language(language) or "", "")
    return CATALOGUE_DIR / name if name else CATALOGUE_DIR / f"{language}.json"


def load_catalogue(language: str) -> dict[str, str]:
    """Read a catalogue (key = the English original); missing or broken files give {}."""
    code = normalize_language(language) or SOURCE_LANGUAGE
    cached = _catalogues.get(code)
    if cached is not None:
        return cached
    data: dict[str, str] = {}
    path = catalogue_path(code)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str) and value.strip():
            data[key] = value
    _catalogues[code] = data
    return data


def set_system_language_resolver(resolver: Callable[[], str | None] | None) -> None:
    """Inject the "system language" resolver (the GUI injects QLocale; tests a fixed value)."""
    global _system_resolver
    _system_resolver = resolver


def language_from_environment() -> str | None:
    """Read ``NMRFORGE_LANG`` / ``NMRFORGE_LANGUAGE`` (a temporary pin); else ``None``."""
    for key in _ENV_KEYS:
        found = normalize_language(os.environ.get(key))
        if found:
            return found
    return None


def language_from_locale_environment() -> str | None:
    """Read the system-locale variables (``LC_ALL`` / ``LC_MESSAGES`` / ``LANG``); else ``None``.

    These are **not** an explicit choice by the user, so they rank *below* the preference stored
    in the user config: on Linux the default locale is almost always a supported ``en_US.UTF-8``,
    and ranking it first would make "Settings -> Interface language" a no-op forever.
    """
    for key in _LOCALE_ENV_KEYS:
        found = normalize_language(os.environ.get(key))
        if found:
            return found
    return None


def read_user_preference(path: Path | None = None) -> str | None:
    """Read the interface-language preference from the user config.

    ``auto``, a missing key, an unsupported code or a broken file all give ``None`` (follow
    the system). ``path`` is for tests; the default is the local override shared by the GUI
    and the backend (``core.app_paths.local_config_path("nmrforge.local.yaml")``), so the
    language pinned in the settings also applies to the viewer and the command line.
    """
    import yaml

    from core.app_paths import local_config_path

    target = Path(path) if path is not None else local_config_path("nmrforge.local.yaml")
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, yaml.YAMLError):
        # a broken or unreadable config must not affect language resolution: follow the system
        return None
    value = ""
    if isinstance(raw, dict):
        value = str(raw.get(USER_CONFIG_KEY) or "").strip()
    if not value or value.lower() == "auto":
        return None
    return normalize_language(value)


def user_preference() -> str | None:
    """The cached preference from the user config (``reload_user_preference`` refreshes it)."""
    global _user_preference, _user_preference_loaded
    if not _user_preference_loaded:
        _user_preference = read_user_preference()
        _user_preference_loaded = True
    return _user_preference


def reload_user_preference() -> str | None:
    """Re-read the user config (the settings dialog calls this after saving)."""
    global _user_preference_loaded
    _user_preference_loaded = False
    return user_preference()


def default_language() -> str:
    """The default this tree declares (``ui_support/locales/default.json``); else the source."""
    try:
        raw = json.loads(DEFAULT_LANGUAGE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return normalize_language(raw.get("language")) or SOURCE_LANGUAGE


def resolve_language() -> str:
    """The language in force: explicit -> NMRFORGE_LANG -> setting -> system locale -> default."""
    explicit = normalize_language(_language)
    if explicit:
        return explicit
    from_env = language_from_environment()
    if from_env:
        return from_env
    stored = user_preference()
    if stored:
        return stored
    if _system_resolver is not None:
        try:
            injected = normalize_language(_system_resolver())
        except Exception:  # pragma: no cover - the resolver comes from outside
            injected = None
        if injected:
            return injected
    from_locale = language_from_locale_environment()
    if from_locale:
        return from_locale
    return default_language()


def set_language(code: str | None) -> str:
    """Set the language explicitly; ``None`` or an unknown code goes back to the resolvers."""
    global _language
    _language = normalize_language(code)
    _missing.clear()
    return resolve_language()


def get_language() -> str:
    """The current language code."""
    return resolve_language()


def tr(text: str, **kwargs: object) -> str:
    """Translate an interface string into the current language; the original when unknown.

    Placeholders go through ``str.format``: ``tr("Workspace: {path}", path=path)`` - a
    translation may reorder them.
    """
    out = _lookup(text)
    if kwargs:
        try:
            return out.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return out
    return out


def _lookup(text: str) -> str:
    language = resolve_language()
    if language == SOURCE_LANGUAGE:
        return text
    catalogue = load_catalogue(language)
    if text in catalogue:
        return catalogue[text]
    _missing.add(text)
    return text


def missing_keys() -> list[str]:
    """Keys looked up in this run that had no entry (only what was actually used)."""
    return sorted(_missing)


def reset_cache() -> None:
    """Drop the catalogue cache, the user-preference cache and the run's missing-key record."""
    global _user_preference_loaded
    _catalogues.clear()
    _missing.clear()
    _user_preference_loaded = False
