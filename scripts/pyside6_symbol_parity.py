#!/usr/bin/env python
"""Check that every Qt symbol nmrForge imports exists in PySide6.

Migration tool (see ``docs/pyside6-migration/migration-plan.md``). It parses the repository's
``from PyQt6.X import ...`` / ``import PyQt6.X`` statements with ``ast`` - no project import
happens, so this runs in an environment that has **only** PySide6 installed - and then asserts that
each symbol resolves in the equivalent PySide6 module.

The only PyQt-to-PySide name mappings are the three the binding actually renames:

    pyqtSignal   -> Signal
    pyqtSlot     -> Slot
    pyqtProperty -> Property

Anything else that does not resolve is a real finding, not a naming difference.

Usage::

    .venv-pyside/Scripts/python.exe scripts/pyside6_symbol_parity.py     # Windows
    .venv-pyside/bin/python scripts/pyside6_symbol_parity.py             # POSIX

Exit code 0 = every symbol resolves; 1 = at least one is missing.
"""

from __future__ import annotations

import argparse
import ast
import importlib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: PyQt name -> PySide6 name. Everything else keeps its name.
RENAMES = {
    "pyqtSignal": "Signal",
    "pyqtSlot": "Slot",
    "pyqtProperty": "Property",
}

DEFAULT_TARGETS = ("gui", "viewer", "tests")


def _iter_python_files(targets: list[str]) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        base = ROOT / target
        if base.is_dir():
            files.extend(sorted(base.rglob("*.py")))
    return files


def collect_imports(files: list[Path]) -> tuple[dict[str, set[str]], Counter]:
    """Return {submodule: {symbols}} plus a usage counter per 'submodule.symbol'."""
    symbols: dict[str, set[str]] = {}
    usage: Counter = Counter()
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if parts[0] != "PyQt6" or len(parts) != 2:
                    continue
                submodule = parts[1]
                for alias in node.names:
                    symbols.setdefault(submodule, set()).add(alias.name)
                    usage[f"{submodule}.{alias.name}"] += 1
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[0] != "PyQt6" or len(parts) != 2:
                        continue
                    symbols.setdefault(parts[1], set())
                    usage[f"{parts[1]}.*"] += 1
    return symbols, usage




# ---------------------------------------------------------------------------
# Second pass: attribute paths, not just imported names
# ---------------------------------------------------------------------------
# ``from PyQt6.QtGui import QPalette`` only proves ``QPalette`` exists. The code then uses
# ``QPalette.ColorRole.Window`` and ``QStyle.StyleHint.SH_ToolTip_WakeUpDelay`` - nested enum paths
# that are exactly where a binding port breaks quietly. This pass resolves every such chain
# statically against the installed PySide6.

def _module_for_symbol(symbols: dict[str, set[str]], name: str) -> str | None:
    """Which PyQt6 submodule exported this name (first match wins)."""
    for submodule in sorted(symbols):
        if name in symbols[submodule]:
            return submodule
    return None


def _attribute_chain(node: ast.AST) -> list[str] | None:
    """Return the dotted parts of an attribute chain rooted at a Name, or None if not resolvable.

    Chains containing a call or a subscript are skipped: they depend on runtime values, not on the
    binding's static API.
    """
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return list(reversed(parts))
    if isinstance(current, (ast.Call, ast.Subscript, ast.Constant)):
        return None
    return None


def collect_attribute_paths(
    files: list[Path], symbols: dict[str, set[str]]
) -> tuple[Counter, Counter]:
    """Return (path_usage, per_file_count) for attribute chains rooted at imported Qt names."""
    usage: Counter = Counter()
    per_file: Counter = Counter()
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # Local names bound by PyQt6 imports, including the modules themselves.
        bound: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if parts[0] == "PyQt6" and len(parts) == 2:
                    for alias in node.names:
                        bound[alias.asname or alias.name] = parts[1]
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[0] == "PyQt6" and len(parts) == 2:
                        bound[alias.asname or parts[1]] = parts[1]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            chain = _attribute_chain(node)
            if not chain or len(chain) < 2:
                continue
            root = chain[0]
            submodule = bound.get(root) or _module_for_symbol(symbols, root)
            if submodule is None:
                continue
            # Only keep the outermost chain: parent Attributes appear as separate nodes.
            full = ".".join(chain)
            usage[f"{submodule}:{full}"] += 1
            per_file[str(path.relative_to(ROOT))] += 1
    return usage, per_file


def check_attribute_paths(usage: Counter, quiet: bool) -> list[str]:
    missing: list[str] = []
    resolved = 0
    for key in sorted(usage):
        submodule, dotted = key.split(":", 1)
        try:
            obj = importlib.import_module(f"PySide6.{submodule}")
        except ImportError as exc:  # pragma: no cover
            missing.append(f"PySide6.{submodule} (import failed: {exc})")
            continue
        parts = dotted.split(".")
        target = obj
        failed_at: str | None = None
        for index, part in enumerate(parts):
            renamed = RENAMES.get(part, part)
            if not hasattr(target, renamed):
                failed_at = ".".join(parts[: index + 1])
                break
            target = getattr(target, renamed)
        if failed_at is None:
            resolved += 1
            if not quiet:
                print(f"  ok  PySide6.{submodule}.{dotted}")
        else:
            missing.append(
                f"PySide6.{submodule}.{dotted}  (missing at {failed_at}"
                f", {usage[key]} site(s))"
            )
    print()
    print(f"attribute paths checked : {resolved + len(missing)}")
    print(f"attribute paths resolved: {resolved}")
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--targets",
        nargs="*",
        default=list(DEFAULT_TARGETS),
        help="directories to scan (default: gui viewer tests)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="only print the summary and any missing symbols",
    )
    args = parser.parse_args(argv)

    try:
        import PySide6  # noqa: F401
    except ImportError:
        print("PySide6 is not importable in this interpreter; run this with the PySide6 env.")
        return 2

    import PySide6.QtCore  # noqa: F401  (import first: pyqtgraph and friends expect QtCore)
    _ = importlib.import_module("PySide6.QtCore")

    files = _iter_python_files(args.targets)
    symbols, usage = collect_imports(files)

    missing: list[str] = []
    checked = 0
    for submodule in sorted(symbols):
        module_name = f"PySide6.{submodule}"
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:  # pragma: no cover - depends on the install
            missing.append(f"{module_name} (module import failed: {exc})")
            continue
        for symbol in sorted(symbols[submodule]):
            target = RENAMES.get(symbol, symbol)
            checked += 1
            if not hasattr(module, target):
                missing.append(
                    f"{module_name}.{target}"
                    + (f"  (PyQt name: {symbol})" if target != symbol else "")
                    + f"  [{usage[f'{submodule}.{symbol}']} import site(s)]"
                )
            elif not args.quiet:
                note = f"  (renamed from {symbol})" if target != symbol else ""
                print(f"  ok  {module_name}.{target}{note}")

    print()
    print(f"scanned            : {len(files)} files in {', '.join(args.targets)}")
    print(f"Qt submodules      : {', '.join(sorted(symbols))}")
    print(f"symbols checked    : {checked}")
    print(f"distinct symbols   : {sum(len(v) for v in symbols.values())}")

    path_usage, per_file = collect_attribute_paths(files, symbols)
    missing += check_attribute_paths(path_usage, args.quiet)
    print(f"files with Qt paths: {len(per_file)}")

    if missing:
        print()
        print(f"MISSING ({len(missing)}):")
        for item in missing:
            print(f"  - {item}")
        return 1
    print("result             : all imported Qt symbols and attribute paths resolve under PySide6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
