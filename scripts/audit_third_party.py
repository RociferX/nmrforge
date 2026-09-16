#!/usr/bin/env python
"""Rerun the full third-party licence audit on the environment that gets bundled.

Why this exists: an AppImage distributes every library it contains, so the licence question is about
*the whole installed set*, not just the packages named in ``pyproject.toml``. This script audits the
interpreter it runs in - which is the build venv during an AppImage build - and reports, per
distribution:

* version, declared licence expression (or classifier fallback), and whether licence files ship;
* a classification of the *best available option* in the expression, because
  "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only" is not the same situation as "GPL-3.0-only";
* whether the package is a direct dependency of this project.

Exit code is 0 when nothing blocks a permissive project licence, and 1 when a package offers only
strong copyleft or its licence cannot be determined - both of which need a human decision.

Usage::

    python scripts/audit_third_party.py                 # audit the current interpreter
    python scripts/audit_third_party.py --csv audit.csv # also write a machine-readable table
    .venv-pyside/Scripts/python scripts/audit_third_party.py   # audit the build environment
"""

from __future__ import annotations

import argparse
import csv
import importlib.metadata as metadata
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PERMISSIVE_MARKERS = (
    "MIT",
    "BSD",
    "APACHE",
    "ISC",
    "PSF",
    "PYTHON SOFTWARE FOUNDATION",
    "ZLIB",
    "UNLICENSE",
    "CC0",
    "0BSD",
    "PUBLIC DOMAIN",
)
WEAK_COPYLEFT_MARKERS = ("LGPL", "MPL", "EPL", "CDDL", "EUPL")
STRONG_COPYLEFT_MARKERS = ("AGPL", "SSPL", "GPL")

#: Distinguishes "GPL-3.0" from "LGPL-3.0": the latter is weak copyleft.
_GPL_RE = re.compile(r"(?<!L)GPL")

_RANK = {"permissive": 0, "weak-copyleft": 1, "strong-copyleft": 2, "unknown": 3, "unclassified": 3}
_RANK_BY_VALUE = {rank: name for name, rank in _RANK.items()}


def _classify_option(option: str) -> str:
    """Classify one licence option: permissive / weak-copyleft / strong-copyleft / unknown."""
    text = option.upper().strip()
    if not text or text in {"UNKNOWN", "NONE", "NULL"}:
        return "unknown"
    if any(marker in text for marker in WEAK_COPYLEFT_MARKERS):
        return "weak-copyleft"
    for marker in STRONG_COPYLEFT_MARKERS:
        if marker == "GPL":
            if _GPL_RE.search(text):
                return "strong-copyleft"
        elif marker in text:
            return "strong-copyleft"
    if any(marker in text for marker in PERMISSIVE_MARKERS):
        return "permissive"
    return "unclassified"


def _best_option(expression: str) -> tuple[str, str]:
    """Split an SPDX-style OR-expression and return (best classification, chosen option)."""
    options = [part.strip() for part in re.split(r"\s+OR\s+|/", expression) if part.strip()]
    if not options:
        return "unknown", ""
    scored = sorted((_RANK[_classify_option(option)], option) for option in options)
    best_rank, best_option = scored[0]
    return _RANK_BY_VALUE.get(best_rank, "unclassified"), best_option


def _licence_candidates(dist: metadata.Distribution) -> list[tuple[str, str]]:
    """Return [(licence text, provenance)] in preference order.

    Metadata is inconsistent across the ecosystem: some wheels declare an SPDX expression, some an
    older free-text ``License`` field (which is sometimes as vague as "Dual License"), and some only
    a trove classifier. All of them are collected so that a vague field cannot hide a licence that a
    classifier states precisely - ``python-dateutil`` is the example that motivated this.
    """
    meta = dist.metadata
    candidates: list[tuple[str, str]] = []
    for key in ("License-Expression", "License"):
        value = meta.get(key)
        if value:
            text = " ".join(str(value).split())
            if not text.upper().startswith("SEE LICENSE") and len(text) < 200:
                candidates.append((text, key))
    classifiers = [
        item.split("::")[-1].strip()
        for item in (meta.get_all("Classifier") or [])
        if item.startswith("License ::")
    ]
    if classifiers:
        candidates.append((" OR ".join(dict.fromkeys(classifiers)), "Classifier"))
    return candidates


def _licence_of(dist: metadata.Distribution) -> tuple[str, str]:
    """Return (best licence text, its provenance) for one distribution.

    "Best" means the candidate whose *classification* is most usable: a precise classifier beats a
    vague free-text field that resolves to nothing.
    """
    candidates = _licence_candidates(dist)
    if not candidates:
        return "", ""
    # Best classification first, then the earliest (most authoritative) source that states it.
    scored = sorted(
        (_RANK[_classify_option(candidate[0])], candidates.index(candidate), candidate)
        for candidate in candidates
    )
    _, _, (text, source) = scored[0]
    return text, source


def _license_files(dist: metadata.Distribution) -> int:
    """Count licence files recorded for a distribution.

    ``Distribution.locate_file`` resolves relative to site-packages, not to the ``.dist-info``
    directory, so the recorded file list (``dist.files``) is what actually tells us whether the
    wheel shipped licence texts - which is exactly what matters for redistribution.
    """
    names = ("license", "copying", "notice")
    count = 0
    for entry in dist.files or []:
        parts = [part.lower() for part in str(entry).split("/")]
        if any(part.startswith(names) for part in parts):
            count += 1
    return count


def _direct_dependencies() -> set[str]:
    import tomllib as _tomllib

    data = _tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = set()
    groups = [data["project"]["dependencies"]] + list(
        data["project"].get("optional-dependencies", {}).values()
    )
    for group in groups:
        for entry in group:
            name = re.split(r"[<>=!~\[; ]", entry.strip(), maxsplit=1)[0]
            if name:
                names.add(name.lower().replace("_", "-"))
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path, default=None, help="also write a CSV table")
    parser.add_argument(
        "--quiet", action="store_true", help="print only the summary and the flagged packages"
    )
    args = parser.parse_args(argv)

    direct = _direct_dependencies()
    rows: list[dict[str, object]] = []
    for dist in sorted(
        metadata.distributions(), key=lambda d: (d.metadata["Name"] or "").lower()
    ):
        name = dist.metadata["Name"] or "?"
        expression, source = _licence_of(dist)
        best, chosen = _best_option(expression)
        rows.append(
            {
                "package": name,
                "version": dist.version or "",
                "licence_expression": expression or "(none declared)",
                "licence_source": source or "(none)",
                "best_option": best,
                "chosen_option": chosen,
                "license_files": _license_files(dist),
                "direct_dependency": "yes" if name.lower().replace("_", "-") in direct else "no",
            }
        )

    if not args.quiet:
        print(f"{'package':<28}{'version':<14}{'class':<18}licence")
        print("-" * 108)
        for row in rows:
            print(
                f"{row['package']:<28}{row['version']:<14}{row['best_option']:<18}"
                f"{row['licence_expression']}"
            )
        print()

    own = [row for row in rows if str(row["package"]).lower() == "nmrforge"]
    blocked = [
        row
        for row in rows
        if row["best_option"] in {"strong-copyleft", "unknown", "unclassified"}
        and str(row["package"]).lower() != "nmrforge"
    ]
    weak = [row for row in rows if row["best_option"] == "weak-copyleft"]
    no_files = [row for row in rows if row["license_files"] == 0]

    print(f"audited {len(rows)} installed distributions")
    print(
        "  permissive                 : "
        f"{sum(1 for r in rows if r['best_option'] == 'permissive')}"
    )
    print(
        f"  weak copyleft (LGPL/MPL/..): {len(weak)}"
        + (f"  -> {', '.join(str(r['package']) for r in weak)}" if weak else "")
    )
    print(
        "  strong-copyleft only       : "
        f"{sum(1 for r in rows if r['best_option'] == 'strong-copyleft')}"
    )
    print(
        "  licence not determinable   : "
        f"{sum(1 for r in rows if r['best_option'] in {'unknown', 'unclassified'})}"
    )
    print(
        f"  distributions with no licence file collected: {len(no_files)}"
        + (f"  -> {', '.join(str(r['package']) for r in no_files)}" if no_files else "")
    )

    if args.csv is not None:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {args.csv}")

    if weak:
        print()
        print("Weak copyleft: fine for this project's licence, but any binary that bundles these")
        print("must ship the licence texts and allow the library to be replaced or relinked.")
    if own:
        print()
        print("This project's own distribution declares no licence, which is the pending decision")
        print("documented in LICENSE_OPTIONS.md (not a third-party finding):")
        for row in own:
            print(f"  - {row['package']} {row['version']}: {row['licence_expression']}")
    if blocked:
        print()
        print(f"NEEDS A DECISION ({len(blocked)} third-party packages):")
        for row in blocked:
            print(f"  - {row['package']} {row['version']}: {row['licence_expression']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
