"""Refresh the fingerprints and the golden vector in ``nmrforge_api/compat_declaration.py``.

After changing ``core/`` / ``backend/`` / ``workflow/`` / ``nmrforge_api/`` or the shipped
data, ``tests/test_compat.py`` demands an updated declaration; this script recomputes both
from the **current working tree** and writes them back, and lets you set the level
explicitly:

python scripts/update_compat_declaration.py                       # fingerprints/golden only
python scripts/update_compat_declaration.py --level additive      # declare a new capability
    --affected localization,sweep_detection --note "what changed and why"
python scripts/update_compat_declaration.py --no-golden           # skip the golden vector

``--level`` / ``--affected`` / ``--note`` keep the current values when omitted; allowed
``--affected`` values are in ``nmrforge_api.compat.AFFECTED_STEPS``. The file is validated
with ``ast.parse`` before writing, and combining ``level=same`` with "the code tokens
changed" is rejected here up front (the guard would catch it too).
"""

from __future__ import annotations

import argparse
import ast
import pprint
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nmrforge_api import compat as compat_module  # noqa: E402

TARGET = ROOT / "nmrforge_api" / "compat_declaration.py"
MARKER = "DECLARATION: dict[str, Any] = "
TAIL_MARKER = "\n\n__all__"


def _display_width(text: str) -> int:
    """Display width: East Asian Wide/Fullwidth counts as 2 columns (matching ruff E501)."""
    return sum(
        2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        for char in text
    )


def _load() -> dict:
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "DECLARATION":
            return ast.literal_eval(node.value)
    raise SystemExit("DECLARATION not found in compat_declaration.py")


def _write(declaration: dict) -> None:
    text = TARGET.read_text(encoding="utf-8")
    head, _, tail = text.partition(MARKER)
    assert tail, "the declaration block changed shape"
    _, _, rest = tail.partition(TAIL_MARKER)
    body = pprint.pformat(declaration, width=88, sort_dicts=False)
    new_text = f"{head}{MARKER}{body}{TAIL_MARKER}{rest}"
    ast.parse(new_text)
    too_long = [
        (index, _display_width(line))
        for index, line in enumerate(new_text.splitlines(), start=1)
        if _display_width(line) > 100
    ]
    if too_long:
        # ruff measures line width by display width (CJK counts as 2), so a long note trips E501
        raise SystemExit(
            f"the declaration has an over-wide line {too_long} (>100 columns); shorten --note"
        )
    TARGET.write_text(new_text, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--level",
        default=None,
        help="same / additive / behavior_changed / contract_changed",
    )
    parser.add_argument(
        "--affected",
        default=None,
        help="comma-separated downstream steps (reference, localization, ...)",
    )
    parser.add_argument(
        "--note", default=None,
        help="one line saying what changed; keeps the current value when omitted",
    )
    parser.add_argument(
        "--updated", default=None, help="date (keeps the current value when omitted)"
    )
    parser.add_argument(
        "--no-golden", action="store_true", help="do not re-run the golden vector"
    )
    args = parser.parse_args(argv)

    declaration = _load()
    if args.level is not None:
        if args.level not in compat_module.COMPAT_LEVELS:
            raise SystemExit(
                f"unknown level {args.level!r}; allowed: {compat_module.COMPAT_LEVELS}"
            )
        declaration["compat_level"] = args.level
    if args.affected is not None:
        declaration["affected"] = [
            item.strip() for item in args.affected.split(",") if item.strip()
        ]
    if args.note is not None:
        declaration["note"] = args.note
    if args.updated is not None:
        declaration["updated"] = args.updated

    digest = compat_module.behavior_digest()
    tokens = compat_module.token_digest()
    if declaration.get("compat_level") == "same" and declaration.get("token_digest"):
        if tokens != declaration["token_digest"]:
            raise SystemExit(
                "level=same but the code tokens changed (so this is not comments or "
                "wording only);"
                "use additive / behavior_changed / contract_changed"
            )
    declaration["digest"] = digest
    declaration["token_digest"] = tokens
    if not args.no_golden:
        from nmrforge_api.conformance import GOLDEN_NAME, golden_hashes

        golden = golden_hashes()
        assert golden["name"] == GOLDEN_NAME
        declaration["golden"] = golden
    _write(declaration)
    compat_module.reset_status_cache()
    print(f"updated {TARGET}")
    print(f"  compat_level = {declaration.get('compat_level')}")
    print(f"  affected     = {declaration.get('affected')}")
    print(f"  digest       = {digest[:16]}…")
    print(f"  token_digest = {tokens[:16]}…")
    if not args.no_golden:
        print(f"  golden       = {declaration['golden']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
