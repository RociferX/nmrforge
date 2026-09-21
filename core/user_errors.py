"""User-facing translation of error messages (Phase 21).

Rules (see ``docs/development.md``, "user-visible errors and debugging information"):

- a message shown to the user must say what to do about it: path, permission, input format
  and missing-field failures each get their own wording;
- **never** hand the user a bare ``KeyError`` / ``IndexError`` / ``TypeError`` /
  ``NoneType`` type name, or a raw traceback;
- the full traceback goes to the debug channel (CLI ``--debug`` / ``NMRFORGE_DEBUG=1``,
  the log for the GUI).

This is a Qt-free leaf module: ``gui/``, ``viewer/`` and ``nmrforge_api/`` only read it.
"""

from __future__ import annotations

from ui_support.i18n import tr

__all__ = ["describe_exception", "user_error_text"]


def describe_exception(exc: BaseException) -> str:
    """Turn a common exception into one sentence the user can act on.

    Only the failure classes the **user can fix** are mapped: paths and permissions,
    invalid input, a missing required field, and a structure that does not match what was
    expected. Anything else keeps its type name and original text - nothing is swallowed,
    and no cause is invented.
    """
    if isinstance(exc, FileNotFoundError):
        return tr("File or directory not found: {p0}", p0=exc.filename or exc)
    if isinstance(exc, NotADirectoryError):
        return tr("Path is not a directory: {p0}", p0=exc.filename or exc)
    if isinstance(exc, IsADirectoryError):
        return tr("Path is a directory but a file is required here: {p0}", p0=exc.filename or exc)
    if isinstance(exc, PermissionError):
        return tr("Permission denied: {p0}", p0=exc.filename or exc)
    if isinstance(exc, KeyError):
        return (
            tr(
            "Missing required field {p0} (does the input file or run record belong to this "
            "version?)",
            p0=exc,
        )
        )
    if isinstance(exc, (IndexError, TypeError, AttributeError)):
        return tr(
            "Input does not match the expected structure ({p0}): "
            "{p1}",
            p0=type(exc).__name__,
            p1=exc,
        )
    if isinstance(exc, ValueError):
        return tr("Invalid input: {p0}", p0=exc)
    if isinstance(exc, OSError):
        return tr("File or system operation failed: {p0}", p0=exc)
    return f"{type(exc).__name__}: {exc}"


def user_error_text(exc: BaseException) -> str:
    """Semantic alias used by the GUI call sites (same source as :func:`describe_exception`)."""
    return describe_exception(exc)
