"""Interface text tool: extract the ``tr()`` literals, watch the Chinese coverage.

Usage::

    python scripts/i18n_extract_ui.py --report   # bare Chinese strings per file
    python scripts/i18n_extract_ui.py --write    # refresh source.json / converted.json
    python scripts/i18n_extract_ui.py --check    # guard (CI / sync)

Rules:

- the source language is English, so the key is the English original inside ``tr()`` (see
  ``ui_support/i18n.py``); Chinese lives in ``ui_support/locales/zh.json``;
- ``ui_support/locales/converted.json`` lists the "already converted" files (**a ratchet:
  it only grows**); those files must not contain Chinese literals outside ``tr()``;
- ``source.json`` records the ``tr()`` strings with where they are used, plus the Chinese
  coverage floor; a coverage regression makes ``--check`` fail;
- the first argument of ``tr()`` must be a string constant: an f-string or a variable is
  reported (otherwise the key cannot be registered statically);
- docstrings are developer documentation and do not count; literals that must stay
  untranslated (file headers, internal keys) are exempted with ``# i18n: keep``.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: Packages that take part in run-time interface text (tests/ and scripts/ do not)
SOURCE_ROOTS = ("core", "backend", "workflow", "nmrforge_api", "gui", "viewer", "ui_support")
LOCALES = ROOT / "ui_support" / "locales"
SOURCE_PATH = LOCALES / "source.json"
ZH_PATH = LOCALES / "zh.json"
CONVERTED_PATH = LOCALES / "converted.json"

CJK = re.compile(r"[\u4e00-\u9fff]")
TR_NAMES = {"tr"}


def ui_files() -> list[Path]:
    """Python files that take part in run-time interface text (sorted for a stable snapshot)."""
    files: list[Path] = []
    for root in SOURCE_ROOTS:
        base = ROOT / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            files.append(path)
    return files


def is_tr_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in TR_NAMES
    if isinstance(func, ast.Attribute):
        return func.attr in TR_NAMES
    return False


#: In-line exemption marker for literals that must not be translated
KEEP_MARKER = "# i18n: keep"


def docstring_ids(tree: ast.AST) -> set[int]:
    """Module/class/function docstrings - developer documentation, not interface text."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def scan(path: Path) -> tuple[list[tuple[str, int]], list[tuple[int, str]], list[int]]:
    """Return (tr keys and lines, Chinese literals outside tr(), tr() calls with no constant).

    Module/class/function docstrings and literals carrying ``# i18n: keep`` do not count.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(path))
    docs = docstring_ids(tree)
    keys: list[tuple[str, int]] = []
    dynamic: list[int] = []
    inside: set[int] = set()
    for node in ast.walk(tree):
        if not is_tr_call(node):
            continue
        first = node.args[0] if node.args else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            keys.append((first.value, node.lineno))
            inside.add(id(first))
        else:
            dynamic.append(node.lineno)
    raw: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if not CJK.search(node.value) or id(node) in inside or id(node) in docs:
            continue
        here = lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""
        above = lines[node.lineno - 2] if node.lineno >= 2 else ""
        if KEEP_MARKER in here or KEEP_MARKER in above:
            continue
        raw.append((node.lineno, node.value))
    return keys, raw, dynamic


def collect() -> dict:
    """Scan the interface layer: key -> where, converted files, per-file leftovers."""
    keys: dict[str, list[str]] = {}
    raw_by_file: dict[str, int] = {}
    dynamic_by_file: dict[str, int] = {}
    tr_by_file: dict[str, int] = {}
    total_raw = 0
    for path in ui_files():
        rel = path.relative_to(ROOT).as_posix()
        found, raw, dynamic = scan(path)
        for key, line in found:
            keys.setdefault(key, []).append(f"{rel}:{line}")
        raw_by_file[rel] = len(raw)
        dynamic_by_file[rel] = len(dynamic)
        tr_by_file[rel] = len(found)
        total_raw += len(raw)
    return {
        "keys": keys,
        "raw_by_file": raw_by_file,
        "dynamic_by_file": dynamic_by_file,
        "tr_by_file": tr_by_file,
        "total_raw": total_raw,
    }


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_snapshot() -> int:
    data = collect()
    keys = data["keys"]
    converted = sorted(
        rel
        for rel, n in data["raw_by_file"].items()
        if n == 0 and data["tr_by_file"].get(rel)
    )
    zh = load_json(ZH_PATH, {})
    covered = sum(1 for key in keys if str(zh.get(key, "")).strip())
    LOCALES.mkdir(parents=True, exist_ok=True)
    SOURCE_PATH.write_text(
        json.dumps(
            {
                "_comment": "Generated by i18n_extract_ui.py --write; key = English original.",
                "keys": {key: keys[key] for key in sorted(keys)},
                "converted_files": converted,
                "coverage_floor_zh": covered,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    CONVERTED_PATH.write_text(
        json.dumps(
            {
                "_comment": "Converted files (a ratchet: it only grows): no Chinese literals "
                "outside tr().",
                "files": converted,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"tr() strings {len(keys)}; Chinese covered {covered}; converted files {len(converted)}")
    print(f"bare Chinese literals left {data['total_raw']} (see --report)")
    return 0


def report() -> int:
    data = collect()
    rows = sorted(
        ((n, rel) for rel, n in data["raw_by_file"].items() if n),
        reverse=True,
    )
    for count, rel in rows:
        dynamic = data["dynamic_by_file"].get(rel, 0)
        note = f"  (dynamic tr arguments {dynamic})" if dynamic else ""
        print(f"{count:5d}  {rel}{note}")
    print(f"bare Chinese literals left: {data['total_raw']}, in {len(rows)} files")
    print(f"tr() strings {len(data['keys'])} (registered)")
    return 0


def check() -> int:
    data = collect()
    problems: list[str] = []
    snapshot = load_json(SOURCE_PATH, {})
    known = set(snapshot.get("keys", {}))
    converted = set(load_json(CONVERTED_PATH, {}).get("files", []))
    zh = load_json(ZH_PATH, {})
    live = set(data["keys"])
    for key in sorted(live - known):
        problems.append(f"new tr() string not registered (run --write first): {key[:40]}")
    for key in sorted(known - live):
        problems.append(f"string in source.json no longer exists: {key[:40]}")
    for rel in sorted(converted):
        count = data["raw_by_file"].get(rel)
        if count is None:
            problems.append(f"file in the converted list is gone: {rel}")
        elif count:
            problems.append(f"converted file still has {count} bare Chinese literals: {rel}")
    covered = sum(1 for key in live if str(zh.get(key, "")).strip())
    floor = int(snapshot.get("coverage_floor_zh", 0))
    if covered < floor:
        problems.append(f"Chinese coverage regressed: {covered} < {floor}")
    for rel, count in sorted(data["dynamic_by_file"].items()):
        if count:
            problems.append(f"tr() first argument is not a string constant ({count}): {rel}")
    if problems:
        print("interface text guard failed:", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(
        f"interface text guard passed: tr() strings {len(live)}, converted files "
        f"{len(converted)}, Chinese covered {covered}, bare Chinese literals "
        f"{data['total_raw']}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Interface text extraction and guard")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--report", action="store_true", help="print the per-file leftovers")
    group.add_argument("--write", action="store_true", help="refresh source.json / converted.json")
    group.add_argument("--check", action="store_true", help="guard")
    args = parser.parse_args(argv)
    if args.report:
        return report()
    if args.write:
        return write_snapshot()
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
