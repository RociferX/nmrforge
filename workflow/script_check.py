r"""Manual script pre-run detector (0.2.199-patch29h): detects common errors and prompts.
Motivation: The user manually modifies the script and then runs it and reports a
UnicodeDecodeError -- mistyped characters after the end-of-line continuation character `\` (such
as `\h`), the csh pipeline breaks, and the binary spectrum data leaks to stdout and is decoded
as text. The detector scans common problems before running and gives clear prompts instead of
strange errors during runtime."""

from __future__ import annotations

import re

from ui_support.i18n import tr

# NmrPipe common functions (-fn value); unknown functions will be reported by nmrPipe with an error
# or silent, prompting in advance.
KNOWN_FUNCTIONS = frozenset(
    {
        "SP", "ZF", "FT", "PS", "EXT", "TP", "ZTP", "POLY", "MC", "REV",
        "EM", "GM", "HT", "SINE", "GAUSS", "LP", "NUS", "SMILE", "MAC",
        "ADD", "SUB", "MUL", "DIV", "SQRT", "EXP", "LOG", "ABS", "STAT",
        "WRITE", "READ", "CUBE", "PROJ", "SUM", "ALTP", "COMPLEX", "REAL",
        "FILTER", "DIM", "COPY", "NUSExpand",
    }
)


def check_script(content: str, script_name: str = "") -> list[str]:
    """Scan the script and return the warning list (empty = no problem found). Pure function, no
    side effects; Check line continuation character by line/CRLF/BOM/output write/unknown
    function/ Common parameter rationality. Warnings are not blocked, and the caller decides
    whether to prompt and continue."""
    warnings: list[str] = []
    if not content.strip():
        return [tr("The script content is empty and cannot be run")]
    if "\ufeff" in content:
        warnings.append(
            tr(
                "the script starts with a BOM, so `#!/bin/csh` is treated as part of the "
                "command;remove the BOM and save the file "
                "again",
            )
        )
    if "\r" in content:
        warnings.append(
            tr(
                "the script contains CRLF (Windows) line endings: the trailing continuation `\\` "
                "is followed by `\\r`,so the continuation fails; convert the file to LF (Unix) "
                "line endings",
            )
        )

    lines = content.splitlines()
    _check_continuations(lines, warnings)
    _check_output_write(content, warnings)
    _check_functions(lines, warnings)
    _check_sp_params(lines, warnings)
    return warnings


def _check_continuations(lines: list[str], warnings: list[str]) -> None:
    r"""Line continuation character check: The pipeline line must end with `\`; there must be no
    extra characters after `\`. Comment lines (starting with `#`) are skipped transparently:
    Generate `#| nmrPipe... \` comments that are common in scripts and do not affect the csh
    continuation flow. Hanging line continuations are only reported for the last line of the
    script."""
    pipeline_lines: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        stripped = line.rstrip("\r").rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            continue
        pipeline_lines.append((idx, stripped))
    for pos, (idx, stripped) in enumerate(pipeline_lines):
        n = idx + 1
        is_pipeline = any(
            token in stripped for token in ("|", "xyz2pipe", "pipe2xyz")
        )
        if is_pipeline and stripped:
            token = stripped.split()[-1]
            bs = token.rfind("\\")
            if 0 <= bs < len(token) - 1:
                tail = token[bs + 1:]
                warnings.append(
                    tr(
                        "No. {p0} Line: There are extra characters after the line continuation "
                        "character `\\` {p1!r}(should be `\\` at the end of the line and wrap "
                        "directly), the pipe will "
                        "break",
                        p0=n,
                        p1=tail,
                    )
                )
        next_is_pipe = pos + 1 < len(pipeline_lines) and pipeline_lines[
            pos + 1
        ][1].lstrip().startswith("|")
        if next_is_pipe and not stripped.endswith("\\"):
            last = stripped[-1]
            warnings.append(
                tr(
                    "No. {p0} line: the next line starts with `|`, but this line ends with {p1!r} "
                    "instead of `\\` (missing line continuation character); the pipeline breaks "
                    "here",
                    p0=n,
                    p1=last,
                )
            )
        if (
            pos == len(pipeline_lines) - 1
            and stripped.endswith("\\")
            and is_pipeline
        ):
            warnings.append(
                tr(
                    "line {p0}: the script ends with a trailing `\\` (dangling continuation),and "
                    "csh waits for further "
                    "input",
                    p0=n,
                )
            )


def _check_output_write(content: str, warnings: list[str]) -> None:
    """Output write check: data leaks to stdout when pipe script is missing -out/pipe2xyz -out.
    Only checked if script appears to be processing pipe (xyz2pipe/pipe2xyz/| nmrPipe), Avoid
    plain text/False positive for non-pipeline content."""
    looks_pipeline = any(
        token in content for token in ("xyz2pipe", "pipe2xyz", "| nmrPipe")
    )
    if not looks_pipeline:
        return
    has_out = bool(re.search(r"pipe2xyz\s+-out", content)) or bool(
        re.search(r"nmrPipe\s+.*-out\s+\S+", content)
    )
    if not has_out:
        warnings.append(
            tr(
                "no output sink found (-out / pipe2xyz -out): the spectrum data goes to stdout, "
                "producing binary garbage and a decoding "
                "error",
            )
        )


def _check_functions(lines: list[str], warnings: list[str]) -> None:
    """Unknown -fn Function name check."""
    for i, line in enumerate(lines):
        for m in re.finditer(r"-fn\s+(\S+)", line):
            fn = m.group(1)
            if fn.upper() not in KNOWN_FUNCTIONS:
                warnings.append(
                    tr(
                        "No. {p0} Row: Unknown nmrPipe function {p1!r}(-fn spell "
                        "check)",
                        p0=i + 1,
                        p1=fn,
                    )
                )


def _check_sp_params(lines: list[str], warnings: list[str]) -> None:
    """SP Window parameter rationality (off<end, both at 0..1)."""
    for i, line in enumerate(lines):
        if "-FN SP" not in line.upper():
            continue
        off = re.search(r"-off\s+([0-9.]+)", line)
        end = re.search(r"-end\s+([0-9.]+)", line)
        if off and end:
            try:
                o, e = float(off.group(1)), float(end.group(1))
            except ValueError:
                continue
            if not (0.0 <= o < e <= 1.0):
                warnings.append(
                    tr(
                        "No. {p0} Line:SP window parameter exception off={p1} end={p2}(Should 0 <= "
                        "off<end <= "
                        "1)",
                        p0=i + 1,
                        p1=o,
                        p2=e,
                    )
                )


__all__ = ["check_script"]
