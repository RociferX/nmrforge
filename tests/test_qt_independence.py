"""Qt-independence guards for the PySide6 migration target architecture.

Target architecture (see ``docs/pyside6-migration/migration-plan.md``):

    core/ backend/ workflow/ nmrforge_api/   Qt-free, importable headless
    gui/  viewer/                            Qt users, but never import a binding directly
    qtcompat/                                the single place a binding is named
    ui_support/                              shared theme/colours; Qt part goes through qtcompat

Each property below is easy to break with one careless lazy import, so each one is asserted rather
than documented. The tests assert the *shape*, not the binding, so the same file passes whether the
project runs on PyQt6 (today) or PySide6 (after the port).
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

QT_BINDINGS = ("PyQt6", "PySide6", "PyQt5", "PySide2")

#: Layers that must stay Qt-free, including the compat boundary itself: importing ``qtcompat`` from
#: the core would drag a binding in transitively.
CORE_LAYERS = ("core", "backend", "workflow", "nmrforge_api")

#: Layers allowed to use Qt, but only through ``qtcompat``.
UI_LAYERS = ("gui", "viewer")

#: The single module permitted to name a binding.
COMPAT_LAYER = "qtcompat"

#: Directories making up the application and its tests. ``scripts/pyside6_*`` are excluded
#: deliberately: they are migration tooling that has to be able to name both bindings, and they
#: only ever run in the PySide6 environment. A later test makes that exclusion explicit.
SCANNED_ROOTS = UI_LAYERS + CORE_LAYERS + (COMPAT_LAYER, "ui_support", "tests", "examples")

#: Migration tooling allowed to reference the target binding.
MIGRATION_TOOLING_PREFIX = "scripts/pyside6_"

#: Known leftover at the repository root that still imports PyQt6 directly: a one-off development
#: script with no importer, no test and no documentation reference. PUBLIC_RELEASE_AUDIT.md
#: recommends deleting it, which needs the repository owner's approval. It is named explicitly, so
#: that any *new* offender still fails this test.
KNOWN_UNMIGRATED_FILES = {".measure_gap.py"}

#: Directories that are not project source: virtual environments (the project keeps two, one per Qt
#: binding), caches and build output.
IGNORED_DIR_NAMES = {"nmrforge", "build", "dist", "nmrforge.egg-info"}


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def _iter_repository_python_files() -> list[Path]:
    """All project ``*.py`` files, pruning non-source directories *during* the walk.

    ``Path.rglob`` would descend into the two virtual environments (``nmrforge/`` for PyQt6,
    ``.venv-pyside/`` for PySide6) before any filter could reject them, which adds thousands of
    files and minutes of I/O to the suite. ``os.walk`` with pruned ``dirnames`` never enters them.
    """
    files: list[Path] = []
    for current, dirnames, filenames in os.walk(ROOT, topdown=True):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".") and name not in IGNORED_DIR_NAMES
        )
        files.extend(Path(current) / name for name in sorted(filenames) if name.endswith(".py"))
    return files


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


def _imported_modules(path: Path) -> set[str]:
    """Top-level module names imported by one file (``qtcompat``, ``gui``, ...)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def _declared_bindings() -> list[str]:
    """Qt bindings declared as runtime dependencies in pyproject.toml."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = data["project"]["dependencies"]
    return sorted(
        {name for name in QT_BINDINGS if any(dep.startswith(name) for dep in dependencies)}
    )


def test_core_layers_have_no_qt_imports() -> None:
    """The computational core must stay Qt-free, including the compat boundary itself."""
    offenders: list[str] = []
    for layer in CORE_LAYERS:
        for path in sorted((ROOT / layer).rglob("*.py")):
            found = sorted(_imported_bindings(path) | (_imported_modules(path) & {COMPAT_LAYER}))
            if found:
                offenders.append(f"{_relative(path)}: {found}")
    assert not offenders, (
        "the computational core must stay Qt-free (no binding, no qtcompat) so it can run "
        "headless; offending files: " + "; ".join(offenders)
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


def test_ui_and_test_layers_import_qt_only_through_qtcompat() -> None:
    """``gui/``, ``viewer/`` and ``tests/`` must not name a binding: they go through qtcompat."""
    offenders: list[str] = []
    for layer in UI_LAYERS + ("tests",):
        for path in sorted((ROOT / layer).rglob("*.py")):
            bindings = sorted(_imported_bindings(path))
            if bindings:
                offenders.append(f"{_relative(path)}: {bindings}")
    assert not offenders, (
        "Qt names must come from qtcompat, so the binding is selected in exactly one place; "
        "offending files: " + "; ".join(offenders)
    )


def test_the_binding_is_named_only_by_the_compat_layer() -> None:
    """Only ``qtcompat/`` (and the migration tooling) may name a binding anywhere."""
    offenders: list[str] = []
    for path in _iter_repository_python_files():
        relative = path.relative_to(ROOT)
        if relative.parts[0] == COMPAT_LAYER:
            continue
        as_posix = str(relative).replace("\\", "/")
        if as_posix.startswith(MIGRATION_TOOLING_PREFIX):
            continue
        if as_posix in KNOWN_UNMIGRATED_FILES:
            continue
        bindings = sorted(_imported_bindings(path))
        if bindings:
            offenders.append(f"{_relative(path)}: {bindings}")
    assert not offenders, (
        f"only {COMPAT_LAYER}/ may import a Qt binding directly; offending files: "
        + "; ".join(offenders)
    )


def test_qtcompat_is_the_boundary_the_ui_uses() -> None:
    """Sanity check the other direction: the UI layers really do go through the boundary."""
    importers = [
        _relative(path)
        for layer in UI_LAYERS
        for path in sorted((ROOT / layer).rglob("*.py"))
        if COMPAT_LAYER in _imported_modules(path) or "ui_support" in _imported_modules(path)
    ]
    assert importers, "expected gui/ and viewer/ to import qtcompat or ui_support"


def test_viewer_does_not_import_the_gui_package() -> None:
    """``viewer/`` must not depend on ``gui/``: the two UI packages are siblings.

    Before this rule, ``viewer/app.py`` imported ``gui.theme`` for its startup theme, which made the
    standalone viewer unable to exist without the project-management GUI.
    """
    offenders: list[str] = []
    for path in sorted((ROOT / "viewer").rglob("*.py")):
        if "gui" in _imported_modules(path):
            offenders.append(_relative(path))
    assert not offenders, "viewer/ must not import gui/: " + "; ".join(offenders)


def test_packaging_declares_exactly_one_qt_binding() -> None:
    declared = _declared_bindings()
    assert len(declared) == 1, f"expected exactly one Qt binding in dependencies, got {declared}"


def test_new_packages_are_declared_for_packaging() -> None:
    """``qtcompat`` and ``ui_support`` must be installed, not only importable from a checkout."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    include = data["tool"]["setuptools"]["packages"]["find"]["include"]
    for package in (COMPAT_LAYER, "ui_support"):
        assert any(pattern.rstrip("*") == package for pattern in include), (
            f"{package} is missing from [tool.setuptools.packages.find].include: {include}"
        )
