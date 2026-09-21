"""Wire Qt's system language into ``ui_support.i18n`` (the single Qt <-> language seam).

The interface text itself has nothing to do with Qt (see ``ui_support/i18n.py``); all this
module does is register the ``QLocale`` system language as a default source once the
QApplication exists. An explicit setting or an environment variable still wins, so an
AppImage can pin its language with ``NMRFORGE_LANG``.
"""

from __future__ import annotations

from ui_support.i18n import get_language, set_system_language_resolver


def install_qt_language() -> str:
    """Register the QLocale system language and return the language now in force."""
    from qtcompat.QtCore import QLocale

    set_system_language_resolver(lambda: QLocale.system().name())
    return get_language()
