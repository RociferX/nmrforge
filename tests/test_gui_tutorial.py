"""GUI usage tutorial: the document opened from the Help menu (2026-09-22, user request).

Three things are guarded: the tutorial ships in both languages; the names it uses are the ones
the interface shows (the four pipeline steps, the two Tools entries, software settings); and the
first entry of the Help menu really opens the tutorial dialog. The wording of the document itself
is maintained by hand - this file only makes sure the two do not drift apart when the interface
is renamed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from qtcompat.QtWidgets import QApplication, QTextBrowser

from core.app_paths import resource_path
from gui.main_window import MainWindow
from gui.pipeline_panel import STEP_LABEL
from gui.tutorial import (
    TUTORIAL_LANGUAGES,
    TutorialDialog,
    load_tutorial_text,
    tutorial_path,
)
from ui_support.i18n import load_catalogue, tr

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _plain(label: str) -> str:
    """Menu labels without the ellipsis (the tutorial names things, it is not a menu dumps)."""
    return label.rstrip(". ").strip()


def _mentions(document: str, label: str) -> bool:
    """Whether the tutorial mentions this name; case-insensitive (it may start a sentence)."""
    return label.lower() in document.lower()


def test_both_languages_ship_a_tutorial_document() -> None:
    for language in TUTORIAL_LANGUAGES:
        path = resource_path(f"tutorial/{language}.md")
        assert path.is_file(), path
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# "), language
        assert text.count("\n## ") >= 8, language


def test_tutorial_falls_back_to_the_other_language() -> None:
    assert tutorial_path("zh").name == "zh.md"
    assert tutorial_path("en").name == "en.md"
    # an unsupported language (or a typo) falls back to the text that is there, never crashes
    assert tutorial_path("fr").is_file()
    assert load_tutorial_text("fr") == load_tutorial_text(tutorial_path("fr").stem)


def test_tutorial_uses_the_names_the_interface_shows() -> None:
    """Four steps + two Tools entries + software settings must be named as the interface does."""
    english = load_tutorial_text("en")
    chinese = load_tutorial_text("zh")
    catalogue = load_catalogue("zh")
    to_english = {value: key for key, value in catalogue.items()}  # Chinese -> English source
    fixed = (
        "Software settings...",
        "Data quality inspection...",
        "spectrum quality assessment...",
    )
    for label in (*STEP_LABEL.values(), *fixed):
        source = to_english.get(label, label)
        assert _mentions(english, _plain(source)), source
        assert _mentions(chinese, _plain(catalogue.get(source, source))), source


def test_tutorial_strings_are_translated() -> None:
    """The new interface strings must have a Chinese entry, or the Chinese UI shows English."""
    catalogue = load_catalogue("zh")
    for key in (
        "Usage tutorial...",
        "NMRForge usage tutorial",
        "The tutorial document is missing from this installation.",
    ):
        assert catalogue.get(key), key


def test_tutorial_dialog_renders_the_document(qapp: QApplication) -> None:
    """Asking for a language loads that document and really renders it into the QTextBrowser."""
    dialog = TutorialDialog(None, language="en")
    try:
        browser = dialog.findChild(QTextBrowser)
        assert browser is not None
        rendered = browser.toPlainText()
        assert "NMRForge usage tutorial" in rendered
        assert "Generate FID" in rendered
    finally:
        dialog.close()


def test_tutorial_dialog_survives_a_missing_document(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An installation without the data file shows one message line - not an empty window."""
    monkeypatch.setattr("gui.tutorial.load_tutorial_text", lambda language=None: "")
    dialog = TutorialDialog(None, language="zh")
    try:
        browser = dialog.findChild(QTextBrowser)
        assert browser is not None
        assert browser.toPlainText().strip()
    finally:
        dialog.close()


def test_help_menu_opens_the_tutorial(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last menu (Help) opens with the tutorial as its first entry, and that opens it."""
    opened: list[object] = []

    class _Recorder:
        """Stand-in: record the call instead of opening a modal window in a test."""

        def __init__(self, parent=None, language=None) -> None:
            opened.append(parent)

        def exec(self) -> int:
            return 0

    monkeypatch.setattr("gui.main_window.TutorialDialog", _Recorder)
    window = MainWindow()
    try:
        bar_menus = [action.menu() for action in window.menuBar().actions()]
        assert all(menu is not None for menu in bar_menus)
        help_menu = bar_menus[-1]
        tutorial_action = help_menu.actions()[0]
        assert tutorial_action.text() == tr("Usage tutorial...")
        tutorial_action.trigger()
        assert opened == [window]
    finally:
        window.close()


def test_both_tutorial_documents_share_one_section_structure() -> None:
    """The two documents have the same sections: changing one language must not forget the other."""
    zh = load_tutorial_text("zh")
    en = load_tutorial_text("en")
    zh_heads = [line for line in zh.splitlines() if line.startswith("#")]
    en_heads = [line for line in en.splitlines() if line.startswith("#")]
    assert len(zh_heads) == len(en_heads), (len(zh_heads), len(en_heads))
    assert [h.count("#") for h in zh_heads] == [h.count("#") for h in en_heads]
