"""GUI usage tutorial: the tutorial document opened from the Help menu (2026-09-22, user request).

It guards three things: the tutorial is present and readable in both languages; the names it uses
come from the same source as the real interface text (the four pipeline steps, the two Tools menu
items, software settings); and the first Help menu item really carries the entry and opens the
tutorial dialog. The tutorial body is maintained by hand, so this only guards against drifting
apart when the interface is renamed.
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
    """Strip the ellipsis from menu item text (the tutorial writes the name, not the full menu
    label)."""
    return label.rstrip(". ").strip()


def _mentions(document: str, label: str) -> bool:
    """Whether the tutorial mentions this name; case-insensitive (a name inside a sentence may start
    lowercase)."""
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
    # An unlisted language (or a mistyped code) falls back to the one that is present, instead of
    # raising or returning blank
    assert tutorial_path("fr").is_file()
    assert load_tutorial_text("fr") == load_tutorial_text(tutorial_path("fr").stem)


def test_tutorial_uses_the_names_the_interface_shows() -> None:
    """Four steps + two Tools items + software settings: the tutorial and the interface must use the
    same names (renaming later turns this red)."""
    english = load_tutorial_text("en")
    chinese = load_tutorial_text("zh")
    catalogue = load_catalogue("zh")
    to_english = {value: key for key, value in catalogue.items()}  # Chinese -> English source text
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
    """Newly added interface text must have a Chinese counterpart, otherwise the Chinese interface
    would show English."""
    catalogue = load_catalogue("zh")
    for key in (
        "Usage tutorial...",
        "NMRForge usage tutorial",
        "The tutorial document is missing from this installation.",
    ):
        assert catalogue.get(key), key


def test_tutorial_dialog_renders_the_document(qapp: QApplication) -> None:
    """The requested language loads that document and really renders it into the QTextBrowser."""
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
    """An installed copy that somehow lacks the tutorial data shows a one-line hint -- never an
    empty window and never an exception."""
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
    """The first item of the Help menu (the last entry) is the usage tutorial; clicking it opens the
    tutorial dialog."""
    opened: list[object] = []

    class _Recorder:
        """Stand-in: records that it was opened, avoiding a real modal window that would hang the
        test."""

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
    """The two tutorials have a one-to-one section structure (only the language differs): this
    prevents editing the Chinese one and forgetting the English one."""
    zh = load_tutorial_text("zh")
    en = load_tutorial_text("en")
    zh_heads = [line for line in zh.splitlines() if line.startswith("#")]
    en_heads = [line for line in en.splitlines() if line.startswith("#")]
    assert len(zh_heads) == len(en_heads), (len(zh_heads), len(en_heads))
    assert [h.count("#") for h in zh_heads] == [h.count("#") for h in en_heads]
