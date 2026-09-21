"""Unified logging configuration (Phase 22).

Conventions (see "Logging (Phase 22)" in ``docs/development.md``):

- library code only calls ``logging.getLogger("nmrforge.<module>")`` and never
  configures a handler at import time;
- the entry points own the configuration: the GUI entry ``main.py`` and the command line
  ``python -m nmrforge_api`` both call :func:`configure_logging`;
- the level comes from ``NMRFORGE_LOG_LEVEL`` (DEBUG/INFO/WARNING/ERROR, WARNING by
  default) or from an explicit argument;
- absolute paths inside log text are sanitised by :func:`sanitize_path`, which folds the
  home directory into ``~``;
- the per-run log file is attached with :func:`attach_run_log` as ``<run_dir>/run.log``
  (created lazily: no records means no empty file) and taken back by
  :func:`detach_run_log` when the run ends.

This module is Qt-free and has no third-party dependencies; it is a ``core/`` leaf.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import IO, Any

__all__ = [
    "DEFAULT_LEVEL",
    "LEVEL_ENV",
    "LOG_FORMAT",
    "LOGGER_NAME",
    "append_run_log_line",
    "attach_run_log",
    "configure_logging",
    "detach_run_log",
    "resolve_level",
    "sanitize_path",
]

#: global logger name (library code hangs off this one)
LOGGER_NAME = "nmrforge"
#: level environment variable (DEBUG/INFO/WARNING/ERROR; invalid values fall back to WARNING)
LEVEL_ENV = "NMRFORGE_LOG_LEVEL"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DEFAULT_LEVEL = "WARNING"
#: marks the console handler installed by an entry point, so repeat calls do not stack
_OWN_HANDLER_FLAG = "_nmrforge_handler"
#: marks the per-run FileHandler separately (it must not be confused with the console
#: handler, or configure_logging would think the console handler is already installed,
#: and would stop installing one, while a run is active)
_RUN_HANDLER_FLAG = "_nmrforge_run_handler"


def resolve_level(level: str | int | None = None) -> int:
    """Resolve ``--level``/the environment variable/a number into a logging level
    (invalid or unknown falls back to WARNING)."""
    if isinstance(level, int):
        return int(level)
    name = str(level or os.environ.get(LEVEL_ENV, DEFAULT_LEVEL)).strip().upper()
    value: Any = logging.getLevelName(name)
    return int(value) if isinstance(value, int) else logging.WARNING


def configure_logging(
    level: str | int | None = None,
    *,
    stream: IO[str] | None = None,
) -> logging.Logger:
    """Configure the ``nmrforge`` logger (idempotent: repeat calls only update the level
    and never stack console handlers).

    Logs go to stderr by default -- the CLI stdout carries machine-readable JSON and must
    not be polluted by log lines. Passing ``stream`` explicitly reuses or redirects the
    console handler to that stream (needed by tests and embedded callers). The per-run
    FileHandler is managed by :func:`attach_run_log` and plays no part in this decision.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(resolve_level(level))
    logger.propagate = False
    console = next(
        (h for h in logger.handlers if getattr(h, _OWN_HANDLER_FLAG, False)), None
    )
    if console is None:
        console = logging.StreamHandler(stream if stream is not None else sys.stderr)
        console.setFormatter(logging.Formatter(LOG_FORMAT))
        setattr(console, _OWN_HANDLER_FLAG, True)
        logger.addHandler(console)
    elif stream is not None and isinstance(console, logging.StreamHandler):
        try:
            console.setStream(stream)
        except ValueError:
            # The previous stream was closed by the host (a pytest capture object, a closed
            # stderr): setStream() flushes it first and raises ValueError. Redirecting is a
            # configuration action, so it must not fail because the caller was tidying up -
            # just swap the stream (leaving the other handler state alone).
            console.stream = stream
    return logger


def attach_run_log(
    directory: Path | str, *, logger: logging.Logger | None = None
) -> logging.Handler:
    """Attach ``<directory>/run.log`` to a single run (creating the directory), return
    the handler."""
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    # delay=True: run.log appears only when a record is really written, so a successful run
    # leaves no empty file
    handler = logging.FileHandler(target_dir / "run.log", encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    setattr(handler, _RUN_HANDLER_FLAG, True)
    (logger or logging.getLogger(LOGGER_NAME)).addHandler(handler)
    return handler


def append_run_log_line(path: Path | str, message: str) -> None:
    """Append one line to run.log directly (bypassing the logger level).

    "Every run gets a run.log" cannot depend on the global level: at the default WARNING
    an INFO record would be dropped. The opening and closing lines of a run are therefore
    written straight to the file (formatted like :data:`LOG_FORMAT`), with home-directory
    absolute paths in the message sanitised by :func:`sanitize_path`.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    line = f"{stamp} INFO nmrforge.run: {sanitize_path(message)}\n"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line)


def detach_run_log(handler: logging.Handler | None) -> None:
    """Take back the handler from :func:`attach_run_log` (safe to call repeatedly; it
    always closes the file)."""
    if handler is None:
        return
    for logger in (logging.getLogger(LOGGER_NAME), logging.getLogger()):
        if handler in logger.handlers:
            logger.removeHandler(handler)
    try:
        handler.close()
    except Exception:  # noqa: BLE001 - a failed close does not affect the main flow
        pass


def sanitize_path(value: Any) -> str:
    """Sanitise log text: fold the user home into ``~`` (the Phase 22 "sensitive absolute
    path" requirement)."""
    text = str(value)
    try:
        home = str(Path.home())
    except Exception:  # noqa: BLE001 - without a home directory the text comes back as is
        return text
    if home and home in text:
        text = text.replace(home, "~")
    return text
