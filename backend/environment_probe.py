"""Probe and register the NMRPipe toolchain on first launch.

Probing requires ``csh`` because NMRPipe is configured in the user's shell startup file.
Repeating that work on every launch would slow startup, so this module owns the "probe once,
fill settings, then use settings" flow.

Policy:

- On a new machine, probe at first launch and write a discovered path only when its setting is
  empty. Never replace a path the user has entered.
- Probe the ``nmrPipe`` binary directory, the ``bruker`` executable, ``proj3D.tcl``, and the
  availability of the SMILE plugin.
- Missing ``nmrPipe`` or ``bruker`` is fatal: report that the tool was not found and ask the
  user to install it or set its path.
- A missing SMILE plugin or ``proj3D.tcl`` is nonfatal and only disables the corresponding
  feature.

SMILE is an NMRPipe plugin, not a standalone executable. The ``NMR_PLUGIN_EXE`` (``nusPipe``)
and ``NMR_PLUGIN_FN`` (``SMILE``) environment variables register ``-fn SMILE`` with ``nmrPipe``.
Detection must use ``csh``: a plain shell reports an unknown function, while the configured
``csh`` environment reports the plugin version. SMILE has no path setting; settings show only
its status.

This module belongs to ``backend`` and may depend on ``backend.nmrpipe_finder``. ``core`` does
not depend on it; callers pass the ``bruker`` path into core (see ``bruker_cmd`` in
:func:`core.data.nus_reader.probe_auto_sampling`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from backend.nmrpipe_finder import csh_run, find_nmrpipe_bin, find_tool

REQUIRED_TOOLS = ("nmrPipe", "bruker")

OPTIONAL_TOOLS = ("proj3D.tcl",)

_SMILE_PROBE_COMMAND = "nmrPipe -fn SMILE -help"
_SMILE_VERSION_RE = re.compile(r"SMILE\s+Plugin:?\s+Version\s+([^\n*]+)", re.IGNORECASE)


@dataclass
class SmileStatus:
    """Status of the SMILE plugin, which is not a standalone executable.

    Attributes
    ----------
    available : bool
        Whether ``nmrPipe -fn SMILE`` is available, as determined in the ``csh`` environment.
    version : str
        Version reported by the plugin, or an empty string when unavailable.
    detail : str
        Status detail for the settings UI and startup log.
    """

    available: bool = False
    version: str = ""
    detail: str = ""


def probe_smile_plugin(timeout: float = 20.0) -> SmileStatus:
    """Probe whether the SMILE plugin is available; this must run through ``csh``.

    SMILE is an NMRPipe plugin rather than a standalone program. Its ``NMR_PLUGIN_EXE``
    (``nusPipe``) and ``NMR_PLUGIN_FN`` (``SMILE``) variables register ``-fn SMILE`` with
    ``nmrPipe``. A plain shell reports ``unknown function SMILE``; the configured ``csh``
    environment recognizes it, so this probe always uses
    :func:`backend.nmrpipe_finder.csh_run`.

    Returns
    -------
    SmileStatus
        ``available`` is true when ``-fn SMILE`` works; a version string is usually available.

    Raises
    ------
    - Does not raise: missing ``csh``, a timeout, or an unrecognized function yields
      ``available=False``.

    Side effects
    ------------
    Read-only: starts a ``csh`` subprocess with ``stdin`` connected to ``/dev/null`` so that
    ``nmrPipe`` cannot wait for interactive input.
    """
    code, output = csh_run(_SMILE_PROBE_COMMAND, timeout=timeout)
    if code < 0 and not output:
        return SmileStatus(detail="csh unavailable or timed out")
    lowered = output.lower()
    if "unknown function" in lowered:
        return SmileStatus(detail="nmrPipe does not recognise -fn SMILE")
    match = _SMILE_VERSION_RE.search(output)
    if match:
        version = match.group(1).strip().rstrip("*").strip()
        return SmileStatus(available=True, version=version, detail=version)
    if "smile" in lowered:
        return SmileStatus(available=True, detail="plugin present")
    return SmileStatus(detail="SMILE plugin not detected")


@dataclass
class EnvironmentReport:
    """Result of one environment probe.

    Attributes
    ----------
    nmrpipe_bin : str
        The bin directory containing ``nmrPipe``, or empty when it was not found.
    tools : dict[str, str]
        Tool name to absolute path; missing tools are omitted.
    language : str
        Reserved UI-language value for future localized probe messages; currently unused.
    """

    nmrpipe_bin: str = ""
    tools: dict[str, str] = field(default_factory=dict)
    language: str = ""
    smile: SmileStatus = field(default_factory=SmileStatus)

    def path_of(self, name: str) -> str:
        """Return a tool path, or an empty string when it was not found."""
        return str(self.tools.get(name) or "")

    def missing_required(self) -> list[str]:
        """Return missing fatal tools in :data:`REQUIRED_TOOLS` order."""
        return [name for name in REQUIRED_TOOLS if not self.path_of(name)]

    def missing_optional(self) -> list[str]:
        """Return missing nonfatal tools in :data:`OPTIONAL_TOOLS` order.

        SMILE is not a file-based tool, so its state is represented by :attr:`smile` and
        included by :meth:`missing_features`.
        """
        return [name for name in OPTIONAL_TOOLS if not self.path_of(name)]

    def missing_features(self) -> list[str]:
        """Return unavailable feature names for nonfatal warnings.

        When the SMILE plugin is absent or unregistered, NUS reconstruction is unavailable.
        """
        features: list[str] = []
        if not self.smile.available:
            features.append("SMILE")
        features.extend(self.missing_optional())
        return features

    @property
    def usable(self) -> bool:
        """Return whether all required tools are present and the main workflow can run."""
        return not self.missing_required()


def probe_environment(explicit_nmrpipe: str = "") -> EnvironmentReport:
    """Probe the local NMRPipe toolchain; this may run ``csh`` and should be called once.

    Parameters
    ----------
    explicit_nmrpipe : str
        An explicitly configured NMRPipe bin directory or executable. When nonempty, it is
        searched first; ``find_tool`` also checks its parent ``com/`` directory for companion
        tools.

    Returns
    -------
    EnvironmentReport
        Paths that were found; missing entries remain empty for the caller to report.

    Raises
    ------
    - Does not raise: any probe failure is represented by an empty entry.

    Side effects
    ------------
    Read-only: may start a ``csh`` subprocess to run ``which``; writes no files.

    Examples
    --------
        report = probe_environment()
        if not report.usable:
            ...  # Show a top-level message to install the tool or set its path.
    """
    bin_dir = find_nmrpipe_bin(explicit_nmrpipe)
    tools: dict[str, str] = {}
    if bin_dir is not None:
        tools["nmrPipe"] = str(bin_dir / "nmrPipe")
    for name in REQUIRED_TOOLS[1:] + OPTIONAL_TOOLS:
        found = find_tool(name, bin_dir)
        if found is not None:
            tools[name] = str(found)
    report = EnvironmentReport(
        nmrpipe_bin=str(bin_dir or ""),
        tools=tools,
        smile=probe_smile_plugin() if bin_dir is not None else SmileStatus(),
    )
    try:
        from core.data.nus_reader import set_bruker_executable

        set_bruker_executable(report.path_of("bruker"))
    except Exception:
        pass
    return report


def missing_message(name: str, *, required: bool) -> str:
    """Build a missing-tool message using fatal or nonfatal wording.

    Fatal tools (``nmrPipe`` and ``bruker``) prevent normal operation and require installation
    or a manually configured path. Missing nonfatal tools (``smile`` or ``proj3D.tcl``) disable
    only their associated feature.
    """
    from ui_support.i18n import tr

    if required:
        return tr(
            "the environment did not provide {p0} automatically; the program cannot work "
            "properly - please install it or set the path manually",
            p0=name,
        )
    return tr(
        "the environment did not provide {p0} automatically; the {p0} feature is unavailable "
        "- please install it or set the path manually",
        p0=name,
    )


def smile_missing_message() -> str:
    """Build the SMILE-missing warning, which says only that its feature is unavailable.

    SMILE has no path setting because it is an NMRPipe plugin; ask the user to install the
    plugin rather than set a path.
    """
    from ui_support.i18n import tr

    return tr(
        "the SMILE plugin was not detected; the NUS reconstruction feature is unavailable - "
        "please install the SMILE plugin for NMRPipe",
    )


def environment_warning(report: EnvironmentReport) -> tuple[str, bool]:
    """Convert a probe result into one top-level warning.

    Returns
    -------
    tuple[str, bool]
        ``(message, is_fatal)``. If all tools are found, the message is empty and the fatal flag
        is false. Fatal issues take precedence; when one exists, nonfatal warnings are omitted.

        A missing SMILE plugin is nonfatal and affects only NUS reconstruction. Its wording
        differs from a missing-file warning such as ``proj3D.tcl``; see
        :func:`smile_missing_message`.
    """
    required = report.missing_required()
    if required:
        return "; ".join(missing_message(n, required=True) for n in required), True
    parts: list[str] = []
    if not report.smile.available:
        parts.append(smile_missing_message())
    parts.extend(missing_message(n, required=False) for n in report.missing_optional())
    return "; ".join(parts), False


def apply_to_settings(report: EnvironmentReport) -> dict[str, str]:
    """Write discovered paths to settings only when their fields are empty.

    Parameters
    ----------
    report : EnvironmentReport
        Result returned by :func:`probe_environment`.

    Returns
    -------
    dict[str, str]
        Key-value pairs actually written, or an empty dictionary if no change was needed. This
        can be recorded in the startup log.

    Raises
    ------
    - Does not raise: settings read/write failures return an empty or partial result and do not
      block startup.

    Side effects
    ------------
    Reads and may write ``nmrforge.local.yaml`` when an empty field needs to be filled.
    """
    from gui.settings import load_settings, save_settings

    try:
        settings = load_settings()
    except Exception:
        return {}
    updates: dict[str, str] = {}
    if not str(settings.get("nmrpipe_path") or "").strip() and report.nmrpipe_bin:
        updates["nmrpipe_path"] = report.nmrpipe_bin
    for key, tool in (
        ("bruker_path", "bruker"),
        ("proj3d_path", "proj3D.tcl"),
    ):
        if str(settings.get(key) or "").strip():
            continue
        found = report.path_of(tool)
        if found:
            updates[key] = found
    smile_state = "available" if report.smile.available else "missing"
    if str(settings.get("smile_status") or "") != smile_state:
        updates["smile_status"] = smile_state
        updates["smile_version"] = report.smile.version
    if not updates:
        return {}
    try:
        save_settings({**settings, **updates})
    except Exception:
        return {}
    return updates


def tool_path_from_settings(settings: dict, name: str) -> str:
    """Return a tool's explicit path from settings, or an empty string when unset.

    Setting names differ from tool names (``bruker_path`` and ``proj3d_path``), so this mapping
    keeps callers consistent. ``smile`` is not mapped because the plugin has no path.
    """
    mapping = {
        "nmrPipe": "nmrpipe_path",
        "bruker": "bruker_path",
        "proj3D.tcl": "proj3d_path",
    }
    key = mapping.get(name)
    if key is None:
        return ""
    return str(settings.get(key) or "").strip()


def resolve_tool(name: str, settings: dict | None = None) -> Path | None:
    """Resolve a tool path, preferring an explicit setting and otherwise searching locally.

    Use a configured value when its file exists. If it no longer exists, search the local
    environment again because NMRPipe may have moved or the home directory may have changed.
    """
    if settings is not None:
        explicit = tool_path_from_settings(settings, name)
        if explicit:
            path = Path(explicit).expanduser()
            if path.is_file():
                return path.resolve()
    bin_dir = None
    nmrpipe_explicit = ""
    if settings is not None:
        nmrpipe_explicit = str(settings.get("nmrpipe_path") or "").strip()
    bin_dir = find_nmrpipe_bin(nmrpipe_explicit)
    if name == "nmrPipe":
        return (bin_dir / "nmrPipe") if bin_dir is not None else None
    return find_tool(name, bin_dir)


__all__ = [
    "OPTIONAL_TOOLS",
    "REQUIRED_TOOLS",
    "EnvironmentReport",
    "SmileStatus",
    "apply_to_settings",
    "environment_warning",
    "missing_message",
    "probe_environment",
    "probe_smile_plugin",
    "resolve_tool",
    "smile_missing_message",
    "tool_path_from_settings",
]
