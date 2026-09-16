"""Qt-independence guards for the PySide6 migration target architecture.

The target architecture is a Qt-independent computational core with a GUI-only Qt dependency
(see `docs/pyside6-migration/migration-plan.md`). Three properties make that architecture real,
and each one is easy to break by accident:

1. the computational core never imports a Qt binding, directly or transitively;
2. a process imports exactly one Qt binding, because importing two in one address space is a hard
   failure - and pyqtgraph will choose its own binding unless the choice is forced;
3. the packaging metadata declares exactly one Qt binding.

These tests intentionally pass on `master` as it stands today (PyQt6) **and** after a migration to
PySide6, because they assert the shape rather than the binding.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

QT_BINDINGS = ("PyQt6", "PySide6", "PyQt5", "PySide2")

#: The layers that must stay Qt-free. `nmrforge_api` is the public scripting surface and is the
#: reason the core must be importable on a headless cluster node.
CORE_LAYERS = ("core", "backend", "workflow", "nmrforge_api")

#: The layers allowed to depend on Qt.
UI_LAYERS = ("gui", "viewer")


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def _qt_binding_of(node: ast.AST) -> str | None:
    """Return the binding name imported by an Import/ImportFrom node, if any."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in QT_BINDINGS:
                return root
    elif isinstance(node, ast.ImportFrom):
        if node.module:
            root = node.module.split(".")[0]
            if root in QT_BINDINGS:
                return root
    return None


def _imported_bindings(path: Path) -> set[str]:
    """Bindings imported anywhere in one file (static, includes function-local imports)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        binding = _qt_binding_of(node)
        if binding:
            found.add(binding)
    return found


def test_core_layers_have_no_qt_imports() -> None:
    offenders: list[str] = []
    for layer in CORE_LAYERS:
        for path in sorted((ROOT / layer).rglob("*.py")):
            bindings = _imported_bindings(path)
            if bindings:
                offenders.append(f"{_relative(path)}: {sorted(bindings)}")
    assert not offenders, (
        "the computational core must stay Qt-free so it can run headless; offending files: "
        + "; ".join(offenders)
    )


def test_core_imports_load_no_qt_module() -> None:
    """Runtime complement to the static scan: importing the core must not pull Qt in."""
    program = (
        "import sys; import core, backend, workflow, nmrforge_api; "
        "loaded = sorted(m for m in sys.modules if m.startswith(('PyQt', 'PySide', 'shiboken'))); "
        "print(loaded)"
    )
    done = subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    loaded = done.stdout.strip()
    assert loaded == "[]", f"importing the core loaded Qt modules: {loaded}"


def test_only_one_qt_binding_is_used_across_the_tree() -> None:
    """A single process must never import two bindings; the static tree mirrors that rule."""
    found: dict[str, list[str]] = {}
    for layer in UI_LAYERS + CORE_LAYERS + ("tests", "examples", "benchmarks"):
        directory = ROOT / layer
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            for binding in _imported_bindings(path):
                found.setdefault(binding, []).append(_relative(path))

    assert found, "expected at least one Qt binding to be imported by the UI layers"
    assert len(found) == 1, (
        "exactly one Qt binding may be imported in this repository; found "
        + ", ".join(f"{name} ({len(files)} files)" for name, files in sorted(found.items()))
    )


def _declared_bindings() -> list[str]:
    """Qt bindings declared as runtime dependencies in pyproject.toml."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = data["project"]["dependencies"]
    return sorted(
        {name for name in QT_BINDINGS if any(dep.startswith(name) for dep in dependencies)}
    )

def test_packaging_declares_exactly_one_qt_binding() -> None:
    declared = _declared_bindings()
    assert len(declared) == 1, f"expected exactly one Qt binding in dependencies, got {declared}"


def test_pyqtgraph_binding_is_forced_or_pre_imported() -> None:
    """pyqtgraph picks a binding for itself unless the choice is already made.

    From the installed ``pyqtgraph/Qt/__init__.py``: ``PYQTGRAPH_QT_LIB`` wins if set; otherwise the
    binding already present in ``sys.modules`` wins, searched as PyQt6, PySide6, PyQt5, PySide2.
    So either the environment variable is set, or the chosen binding is imported before pyqtgraph.
    This is the highest-risk item in the migration audit, hence a guard.
    """
    # The files that take Qt symbols from pyqtgraph rather than from the binding directly.
    via_pyqtgraph = [
        path
        for path in sorted((ROOT / "viewer").rglob("*.py"))
        if "from pyqtgraph.Qt import" in path.read_text(encoding="utf-8")
    ]
    assert via_pyqtgraph, "expected viewer/ to obtain Qt symbols through pyqtgraph"

    # Today the safety net is import order: gui/viewer import their binding before pyqtgraph, and
    # the metadata that records the choice lives in pyproject. This test fails if a file that
    # imports pyqtgraph also imports a *different* binding than the declared one.
    declared = _declared_bindings()[0]
    for path in via_pyqtgraph:
        imported = _imported_bindings(path)
        assert not imported or imported == {declared}, (
            f"{_relative(path)} imports {sorted(imported)} but the project declares {declared}"
        )
