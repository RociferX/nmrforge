"""Collect evidence about Bruker-to-NMRPipe conversion inputs and scripts.

Acquisition parameters describe the source data. They do not prove which digital
filter correction was applied, so this module records that result as unknown
unless a future caller supplies explicit execution evidence.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path
from typing import Any

from core.data.internal_data_model import Experiment

_FILTER_KEYS = ("GRPDLY", "DECIM", "DSPFVS", "BYTORDA", "DTYPA")
_OPTION_NAMES = {
    "-grpdly": "grpdly",
    "-decim": "decim",
    "-dspfvs": "dspfvs",
    "-skip": "skip",
}
_FLAG_NAMES = {"-ext": "ext", "-noext": "noext", "-amx": "amx", "-dmx": "dmx"}
_SIDECAR_PATTERN = "*.fid.conversion.json"


def _acquisition_filter_parameters(experiment: Experiment) -> dict[str, dict[str, Any]]:
    params = getattr(experiment, "acquisition_parameters", None) or {}
    result: dict[str, dict[str, Any]] = {}
    for block_name in ("acqus", "acqu2s", "acqu3s"):
        block = params.get(block_name)
        if not isinstance(block, dict):
            continue
        result[block_name] = {
            key: block.get(key) for key in _FILTER_KEYS
        }
    return result


def _parse_bruk2pipe_command(lines: list[str]) -> dict[str, Any] | None:
    raw = "\n".join(lines)
    joined = " ".join(line.strip().removesuffix("\\").strip() for line in lines)
    try:
        tokens = shlex.split(joined, comments=False, posix=True)
    except ValueError:
        tokens = []
    if not tokens or Path(tokens[0]).name.lower() != "bruk2pipe":
        return None

    arguments = tokens[1:]
    parsed_values: dict[str, Any] = {}
    index = 0
    while index < len(arguments):
        token = arguments[index]
        option = token.lower()
        if option in _OPTION_NAMES:
            if index + 1 < len(arguments):
                parsed_values[_OPTION_NAMES[option]] = arguments[index + 1]
                index += 1
        elif option in _FLAG_NAMES:
            parsed_values[_FLAG_NAMES[option]] = True
        index += 1
    return {
        "command": "bruk2pipe",
        "line": raw,
        "arguments": arguments,
        "parsed_values": parsed_values,
    }


def _bruk2pipe_commands(text: str) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not current:
            if not stripped or Path(stripped.split(maxsplit=1)[0]).name.lower() != "bruk2pipe":
                continue
        current.append(line)
        if not stripped.endswith("\\"):
            command = _parse_bruk2pipe_command(current)
            if command is not None:
                commands.append(command)
            current = []
    if current:
        command = _parse_bruk2pipe_command(current)
        if command is not None:
            commands.append(command)
    return commands


def _work_conversion_scripts(work: Path) -> list[Path]:
    if not work.is_dir():
        return []
    scripts = []
    for path in work.rglob("fid.com"):
        try:
            relative = path.relative_to(work)
        except ValueError:
            continue
        if any(part.lower() == "raw" for part in relative.parts[:-1]):
            continue
        if path.is_file():
            scripts.append(path)
    return sorted(scripts, key=lambda item: item.relative_to(work).as_posix().lower())


def _script_evidence(path: Path, work: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    return {
        "path": path.relative_to(work).as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bruk2pipe_commands": _bruk2pipe_commands(text),
    }


def conversion_audit(experiment: Experiment, work: Path) -> dict[str, Any]:
    """Return source digital-filter metadata and actual work-tree ``fid.com`` evidence.

    The acquisition values and command options are evidence of inputs only; they
    do not establish whether or how a digital-filter correction was executed.
    """
    work = Path(work)
    return {
        "digital_filter": {
            "status": "unknown",
            "method": None,
            "acquisition_parameters": _acquisition_filter_parameters(experiment),
            "reason": (
                "Acquisition metadata and fid.com arguments do not prove which digital-filter "
                "correction was actually applied."
            ),
        },
        "scripts": [
            _script_evidence(path, work) for path in _work_conversion_scripts(work)
        ],
    }


def read_conversion_provenance(work: Path) -> dict[str, Any]:
    """Read conversion sidecar snapshots beneath ``work`` without replacing history."""
    work = Path(work)
    snapshots: list[dict[str, Any]] = []
    if work.is_dir():
        for path in sorted(work.rglob(_SIDECAR_PATTERN)):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            relative = path.relative_to(work).as_posix()
            snapshot = {
                "path": relative,
                "record": payload,
            }
            # Keep evidence frozen in the sidecar. Current fid.com contents may
            # have changed since the conversion and must not rewrite that history.
            audit = payload.get("conversion_provenance")
            if isinstance(audit, dict):
                snapshot["conversion_provenance"] = audit
            snapshots.append(snapshot)
    return {"sidecars": snapshots}


__all__ = ["conversion_audit", "read_conversion_provenance"]
