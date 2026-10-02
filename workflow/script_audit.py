"""Read-only script change summaries; never execute or infer physical axis names."""

from __future__ import annotations

import difflib
import hashlib
import re
import shlex
from pathlib import Path

from ui_support.i18n import tr


def _commands(text: str) -> list[dict]:
    commands = []
    for line in re.sub(r"\\\r?\n", " ", text).splitlines():
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars="|;&")
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            tokens = [line.strip()] if line.strip() and not line.lstrip().startswith("#") else []
        chunks = [[]]
        for token in tokens:
            if token and set(token) <= set("|;&"):
                chunks.append([])
            else:
                chunks[-1].append(token)
        for chunk in chunks:
            if not chunk:
                continue
            options = {}
            positional = []
            index = 1
            while index < len(chunk):
                token = chunk[index]
                if re.match(r"^--?[A-Za-z]", token):
                    values = []
                    index += 1
                    while index < len(chunk) and not re.match(r"^--?[A-Za-z]", chunk[index]):
                        values.append(chunk[index])
                        index += 1
                    options[token] = " ".join(values) if values else True
                else:
                    positional.append(token)
                    index += 1
            name = Path(chunk[0]).name
            function = options.get("-fn")
            if isinstance(function, str):
                name += " " + function
            commands.append(
                {
                    "name": name,
                    "executable": chunk[0],
                    "options": options,
                    "positional": positional,
                    "text": shlex.join(chunk),
                }
            )
    return commands


def script_changes(name: str, edited: str, baseline: str | None, source: str = "") -> dict:
    """Compare concrete commands/options; comments/whitespace are not parameter edits."""
    audit = {
        "script": name,
        "baseline_source": source,
        "baseline_available": baseline is not None,
        "script_sha256": hashlib.sha256(edited.encode("utf-8")).hexdigest(),
        "changes": [],
    }
    if baseline is None:
        return audit
    audit["baseline_sha256"] = hashlib.sha256(baseline.encode("utf-8")).hexdigest()
    old = _commands(baseline)
    new = _commands(edited)
    matcher = difflib.SequenceMatcher(
        a=[c["name"] for c in old], b=[c["name"] for c in new], autojunk=False
    )
    changes = audit["changes"]
    for tag, i0, i1, j0, j1 in matcher.get_opcodes():
        if tag == "equal":
            for oi, ni in zip(range(i0, i1), range(j0, j1)):
                before, after = old[oi], new[ni]
                label = {"command": after["name"], "position": ni + 1}
                if before["executable"] != after["executable"]:
                    changes.append(
                        {
                            **label,
                            "parameter": "executable",
                            "before": before["executable"],
                            "after": after["executable"],
                        }
                    )
                for key in sorted(before["options"].keys() | after["options"].keys()):
                    prev, value = before["options"].get(key), after["options"].get(key)
                    if prev != value:
                        changes.append({**label, "parameter": key, "before": prev, "after": value})
                if before["positional"] != after["positional"]:
                    changes.append(
                        {
                            **label,
                            "parameter": "arguments",
                            "before": " ".join(before["positional"]),
                            "after": " ".join(after["positional"]),
                        }
                    )
        else:
            for position, command in enumerate(old[i0:i1], start=i0 + 1):
                changes.append(
                    {
                        "command": command["name"],
                        "position": position,
                        "parameter": "command",
                        "before": command["text"],
                        "after": None,
                    }
                )
            for position, command in enumerate(new[j0:j1], start=j0 + 1):
                changes.append(
                    {
                        "command": command["name"],
                        "position": position,
                        "parameter": "command",
                        "before": None,
                        "after": command["text"],
                    }
                )
    return audit


def script_change_report(audits: list[dict], *, failed: bool = False) -> list[str]:
    """The same audit section can be shown in persisted reports, logs and step details."""
    if not audits:
        return []
    lines = [
        tr("◆ Manual script changes (attempted; run failed)")
        if failed
        else tr("◆ Manual script changes")
    ]

    def display(value):
        if value is None:
            return tr("not set")
        if value is True:
            return tr("enabled")
        return str(value)

    for audit in audits:
        lines.append(tr("Script: {p0}", p0=audit["script"]))
        if not audit.get("baseline_available"):
            lines.append(
                tr("No reliable previous script; changed parameters cannot be determined.")
            )
            for key, value in (audit.get("applied_overrides") or {}).items():
                lines.append(
                    tr(
                        "  {p0}: {p1} (submitted override; previous value unknown)",
                        p0="-" + key,
                        p1=value,
                    )
                )
            continue
        source = audit["baseline_source"]
        source = {
            "automatic_fid": tr("automatic fid.com baseline"),
            "last_successful_run": tr("last successful run"),
            "editor_open": tr("script when the editor was opened"),
        }.get(source, source)
        lines.append(tr("Compared with: {p0}", p0=source))
        if not audit.get("changes"):
            lines.append(
                tr("No processing commands or parameters changed (comments and spacing excluded).")
            )
        for change in audit.get("changes", []):
            lines.append(
                tr(
                    "  {p0} [command {p1}] {p2}: {p3} → {p4}",
                    p0=change["command"],
                    p1=change["position"],
                    p2=change["parameter"],
                    p3=display(change["before"]),
                    p4=display(change["after"]),
                )
            )
    return lines


def previous_script(manager, exp_id: str, data_id: str, name: str, refs) -> str | None:
    """Use a successful snapshot for this exact data, never a saved/overwritten work script."""
    if manager.project is None:
        return None
    for run in reversed(manager.project.workflow_runs):
        if (
            run.experiment_id != exp_id
            or run.workflow_ref not in refs
            or run.status != "success"
            or (run.inputs or {}).get("data_id") != data_id
            or not run.snapshot_dir
        ):
            continue
        try:
            snapshot = (manager.root / run.snapshot_dir).resolve()
            path = (snapshot / name).resolve()
            if not snapshot.is_relative_to(manager.root.resolve()) or not path.is_relative_to(
                snapshot
            ):
                continue
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
    return None
