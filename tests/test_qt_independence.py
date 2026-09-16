"""Qt-independence guards for the architecture.

Target architecture (see ``docs/pyside6-migration/migration-plan.md``):

    core/ backend/ workflow/ nmrforge_api/   Qt-free, importable headless
    gui/  viewer/ ui_support/                Qt users, but never import a binding directly
    qtcompat/                                the single place a binding is named (PySide6)

PyQt6 is gone: the project depends on PySide6 only, and the port is complete. Each property below
is easy to break with one careless lazy import, so each one is asserted rather than documented.
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
UI_LAYERS = ("gui", "viewer", "ui_support")

#: The single module permitted to name a binding.
COMPAT_LAYER = "qtcompat"

#: Directories that are not project source: the virtual environment, caches and build output.
IGNORED_DIR_NAMES = {"nmrforge", "build", "dist", "nmrforge.egg-info"}


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def _iter_repository_python_files() -> list[Path]:
    """All project ``*.py`` files, pruning non-source directories *during* the walk."""
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
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        binding = _qt_binding_of(node)
        if binding:
            found.add(binding)
    return found


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def _declared_bindings() -> list[str]:
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


def test_only_qtcompat_names_a_binding() -> None:
    """Across the whole repository, exactly one module may import a Qt binding."""
    offenders: list[str] = []
    for path in _iter_repository_python_files():
        if path.relative_to(ROOT).parts[0] == COMPAT_LAYER:
            continue
        bindings = sorted(_imported_bindings(path))
        if bindings:
            offenders.append(f"{_relative(path)}: {bindings}")
    assert not offenders, (
        f"only {COMPAT_LAYER}/ may import a Qt binding; offending files: " + "; ".join(offenders)
    )


def test_qtcompat_is_the_boundary_the_ui_uses() -> None:
    importers = [
        _relative(path)
        for layer in UI_LAYERS
        for path in sorted((ROOT / layer).rglob("*.py"))
        if COMPAT_LAYER in _imported_modules(path)
    ]
    assert importers, "expected gui/, viewer/ or ui_support/ to import qtcompat"


def test_viewer_does_not_import_the_gui_package() -> None:
    """``viewer/`` must not depend on ``gui/``: the two UI packages are siblings."""
    offenders = [
        _relative(path)
        for path in sorted((ROOT / "viewer").rglob("*.py"))
        if "gui" in _imported_modules(path)
    ]
    assert not offenders, "viewer/ must not import gui/: " + "; ".join(offenders)


def test_packaging_declares_exactly_one_qt_binding() -> None:
    declared = _declared_bindings()
    assert declared == ["PySide6"], f"expected PySide6 as the only Qt binding, got {declared}"


def test_declared_binding_matches_qtcompat() -> None:
    """The dependency and the code must not disagree about which binding this project uses."""
    import qtcompat

    assert _declared_bindings() == [qtcompat.BINDING_MODULE]


def test_appimage_spec_collects_the_binding_plugins() -> None:
    """The PyInstaller spec must collect the plugin of the binding that is actually declared."""
    import qtcompat

    spec = (ROOT / "packaging" / "linux" / "NMRForge.spec").read_text(encoding="utf-8")
    assert f"{qtcompat.BINDING_MODULE}.QtSvg" in spec, (
        "the spec's hiddenimports must name the declared binding's QtSvg module"
    )


def test_new_packages_are_declared_for_packaging() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    include = data["tool"]["setuptools"]["packages"]["find"]["include"]
    for package in (COMPAT_LAYER, "ui_support"):
        assert any(pattern.rstrip("*") == package for pattern in include), (
            f"{package} is missing from [tool.setuptools.packages.find].include: {include}"
        )
