"""The usage tutorial: the document window opened from the Help menu (Qt layer).

The tutorial text is **shipped data** (``nmrforge_data/tutorial/{zh,en}.md``) and follows the
same language rule as the interface strings: the file for the current language is loaded at run
time, and if that language has no text the other one is used. Why not ``ui_support/locales``:
that is the lookup table for **individual** short strings (keyed by the English source), while
the tutorial is a whole document (a dozen sections, lists and examples) - keeping it in the data
package means it ships with the program and can be switched as a document.

Resources are located through ``core.app_paths.resource_path``: in a source checkout it lives
under ``nmrforge_data/``, in an installed copy under ``site-packages/nmrforge_data/`` and in a
frozen build under ``_MEIPASS/nmrforge_data/`` - the same relative path in all three shapes
(see packaging/linux/NMRForge.spec and docs/packaging.md).
"""

from __future__ import annotations

from pathlib import Path

from qtcompat.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.app_paths import resource_path
from ui_support.i18n import get_language, tr

#: tutorial directory (relative to the resource root)
TUTORIAL_DIR = "tutorial"
#: languages that have tutorial text; the order is also the fallback order
TUTORIAL_LANGUAGES: tuple[str, ...] = ("zh", "en")


def tutorial_path(language: str | None = None) -> Path | None:
    """Path of the tutorial file; falls back to the other language, ``None`` if neither exists."""
    code = str(language or get_language() or "").strip().lower()
    for candidate in (code, *(item for item in TUTORIAL_LANGUAGES if item != code)):
        if not candidate:
            continue
        path = resource_path(f"{TUTORIAL_DIR}/{candidate}.md")
        if path.is_file():
            return path
    return None


def load_tutorial_text(language: str | None = None) -> str:
    """Read the tutorial text (UTF-8); empty string when it is missing or unreadable."""
    path = tutorial_path(language)
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


class TutorialDialog(QDialog):
    """The usage tutorial dialog: markdown text plus a close button.

    A plain ``QDialog`` (not the Popup used by ``InfoDialog``): the tutorial is long, so it has
    to be resizable, scrollable and selectable, and clicking outside must not close it. Centring
    is handled by the application-wide filter (``install_dialog_centering`` in gui/dialogs.py).
    """

    def __init__(self, parent: QWidget | None = None, language: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("NMRForge usage tutorial"))
        self.resize(960, 720)
        layout = QVBoxLayout(self)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        text = load_tutorial_text(language)
        if text:
            self.view.setMarkdown(text)
        else:
            self.view.setPlainText(
                tr("The tutorial document is missing from this installation.")
            )
        layout.addWidget(self.view, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr("close"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
