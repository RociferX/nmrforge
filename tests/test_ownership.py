"""Ownership-boundary checks (purely functional tests)."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest


def _load_module() -> object:
    path = Path(__file__).resolve().parent.parent / "scripts" / "check_ownership.py"
    spec = importlib.util.spec_from_file_location("check_ownership", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


module = _load_module()


def test_gui_paths() -> None:
    assert module.owner_of("gui/main_window.py") == "gui"
    assert module.owner_of("viewer/spectrum_viewer.py") == "gui"
    assert module.owner_of("viewer/app.py") == "gui"
    assert module.owner_of("main.py") == "gui"
    assert module.owner_of("scripts/make_icon.py") == "gui"
    assert module.owner_of("tests/test_gui_project.py") == "gui"
    assert module.owner_of("tests/test_viewer.py") == "gui"


def test_backend_paths() -> None:
    assert module.owner_of("backend/nmrpipe_backend.py") == "backend"
    assert module.owner_of("workflow/engine.py") == "backend"
    assert module.owner_of("core/data/bruker_reader.py") == "backend"
    assert module.owner_of("core/processing/phase.py") == "backend"
    assert module.owner_of("core/experiment/bruker_parser.py") == "backend"
    assert module.owner_of("scripts/smile_optimize.py") == "backend"
    assert module.owner_of("nmrforge_api/peak_tables.py") == "backend"
    assert module.owner_of("nmrforge_api/sweep.py") == "backend"
    assert module.owner_of("tests/test_bruker_parser.py") == "backend"


def test_shared_paths() -> None:
    assert module.owner_of("core/project/manager.py") == "shared"
    assert module.owner_of("core/data/internal_data_model.py") == "shared"
    assert module.owner_of("backend/base.py") == "shared"
    assert module.owner_of("viewer/spectrum.py") == "shared"
    assert module.owner_of("gui/processing.py") == "shared"
    assert module.owner_of("docs/ARCHITECTURE.md") == "shared"
    assert module.owner_of("CHANGELOG.md") == "docs"
    assert module.owner_of("docs/PROJECT_STATUS.md") == "docs"
    assert module.owner_of("docs/proposals/gui-to-backend/001-x.md") == "gui"
    assert module.owner_of("docs/proposals/backend-to-gui/001-x.md") == "backend"
    assert module.owner_of("pyproject.toml") == "shared"
    assert module.owner_of("tests/test_ownership.py") == "shared"


def test_unknown_path_is_shared() -> None:
    assert module.owner_of("some/new_thing.py") == "shared"


def test_violations_logic() -> None:
    # Pure logic: Given a file list, manually determine the ownership without triggering git.
    files = {
        "gui/main_window.py": "gui",
        "backend/base.py": "shared",
        "core/processing/phase.py": "backend",
    }
    allowed = {"gui", "docs"}
    bad = [(p, o) for p, o in files.items() if o not in allowed]
    assert bad == [("backend/base.py", "shared"), ("core/processing/phase.py", "backend")]


def test_default_base_resolves_in_this_repository() -> None:
    """The default baseline must resolve here (trunk master / public main, no hard-coded name)."""
    base = module.default_base()
    assert module._rev_exists(base), base


def test_changed_files_sees_uncommitted_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-commit check must see uncommitted edits: base...HEAD only looks at commits."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    git("add", "tracked.txt")
    git(
        "-c", "user.email=t@example.com", "-c", "user.name=t",
        "commit", "-q", "-m", "init",
    )

    monkeypatch.setattr(module, "ROOT", repo)
    assert module.changed_files("HEAD") == []  # clean working tree

    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    assert "tracked.txt" in module.changed_files("HEAD")  # uncommitted edit must appear
