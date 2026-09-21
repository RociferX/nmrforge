"""Ownership check: confirm that a change does not cross the GUI/backend boundary.

Usage:
python scripts/check_ownership.py --owner gui # front-end work in progress
python scripts/check_ownership.py --owner backend # engine work in progress
python scripts/check_ownership.py --owner all # before integrating the two sides
python scripts/check_ownership.py --base master # baseline (default master)"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Owner -> path prefix (relative to warehouse root, forward slash).
GUI_PREFIXES = ("gui/", "viewer/", "main.py", "scripts/make_icon.py")
BACKEND_PREFIXES = (
    "backend/",
    "workflow/",
    # External interface package: no Qt, processing side implementation (2026-09-13 v0.2 included in
    # the ownership table).
    "nmrforge_api/",
    "core/data/",
    "core/experiment/",
    "core/experiments/",
    "core/processing/",
    "core/planning/",
    # Structured audit records of automatically changed data (Phase 10; processing side writes).
    "core/audit/",
    # User visible error message translation (Phase 21;GUI/CLI read-only reference, changes will be
    # handled by the processing side).
    "core/user_errors.py",
    # Unified logging configuration (Phase 22; entry call, library code only getLogger).
    "core/logging_setup.py",
    "core/optimization/",
    "core/qc/",
    "core/reporting/",
    "scripts/smile_optimize.py",
    "scripts/param_optimize.py",
)

# Shared contract and collaboration infrastructure: these files need both sides to agree.
SHARED_PREFIXES = (
    "core/project/",
    "core/data/internal_data_model.py",
    "backend/base.py",
    "viewer/spectrum.py",
    "gui/processing.py",
    "pyproject.toml",
    ".gitignore",
    "nmrforge_data/",
    ".codex/",
    "docs/",
    "scripts/check_ownership.py",
    "tests/test_ownership.py",
)


def owner_of(path: str) -> str:
    """The return path belongs to: gui/backend/shared/docs (collaboration documents)."""
    path = path.replace("\\", "/")
    if path.startswith("docs/proposals/gui-to-backend/"):
        return "gui"  # GUI Created requirements.
    if path.startswith("docs/proposals/backend-to-gui/"):
        return "backend"  # Backend Created requirements.
    if path in ("CHANGELOG.md", "docs/PROJECT_STATUS.md"):
        return "docs"  # Collaboration documents can be appended by both parties.
    if path.startswith("tests/"):
        if path.startswith(("tests/test_gui", "tests/test_viewer")):
            return "gui"
        if path.startswith("tests/test_ownership"):
            return "shared"
        return "backend"
    for prefix in SHARED_PREFIXES:
        if path == prefix or path.startswith(prefix):
            return "shared"
    for prefix in GUI_PREFIXES:
        if path == prefix or path.startswith(prefix):
            return "gui"
    for prefix in BACKEND_PREFIXES:
        if path == prefix or path.startswith(prefix):
            return "backend"
    return "shared"  # Unmatched paths will be treated as shared (conservative).


def _git(*args: str) -> subprocess.CompletedProcess:
    """Run one git command in the repository root (the caller checks the return code)."""
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )


def _rev_exists(rev: str) -> bool:
    return _git("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").returncode == 0


def default_base() -> str:
    """Default baseline: the current branch's upstream, then the remote default, then local.

    The trunk's default branch is ``master`` and this public repository's is ``main``: hard-coding
    one name makes the "check before committing" command die with a git error in the other tree.
    """
    for rev in (
        _git(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
        ).stdout.strip(),
        _git(
            "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"
        ).stdout.strip(),
    ):
        if rev and _rev_exists(rev):
            return rev
    for rev in ("master", "main"):
        if _rev_exists(rev):
            return rev
    return "HEAD"


def changed_files(base: str) -> list[str]:
    """Files changed against the baseline (**committed and uncommitted**) plus untracked ones.

    ``base...HEAD`` only sees committed work: running it with uncommitted changes reports
    "0 changed files", which is exactly when the check is meant to run. So the working tree is
    compared against the baseline as well and the two lists are merged.
    """
    if not _rev_exists(base):
        raise SystemExit(
            f"baseline {base!r} is not a commit in this repository: pass --base, or fetch first "
            "(the default baseline is the current branch's upstream)"
        )
    files: list[str] = []
    for args in (
        ("diff", "--name-only", "--diff-filter=ACMRT", f"{base}...HEAD"),
        ("diff", "--name-only", "--diff-filter=ACMRT", base),
    ):
        files.extend(line for line in _git(*args).stdout.splitlines() if line.strip())
    untracked = _git("ls-files", "--others", "--exclude-standard")
    files.extend(line for line in untracked.stdout.splitlines() if line.strip())
    return sorted(set(files))


def violations(
    owner: str, base: str, allow_shared: bool = False
) -> list[tuple[str, str]]:
    """Returns entries in (file, actual owner) that are not owned by owner."""
    result: list[tuple[str, str]] = []
    allowed = {owner}
    if owner in ("gui", "backend"):
        # Collaboration documents (CHANGELOG/PROJECT_STATUS) can be written by both parties.
        allowed.add("docs")
    for path in changed_files(base):
        actual = owner_of(path)
        if owner != "all" and actual not in allowed:
            if allow_shared and actual == "shared":
                continue  # shared file: the change belongs to the shared contract
            result.append((path, actual))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ownership check")
    parser.add_argument("--owner", choices=("gui", "backend", "all"), default="all")
    parser.add_argument(
        "--base", default=None, help="git baseline (default: the branch's upstream)"
    )
    parser.add_argument(
        "--allow-shared",
        action="store_true",
        help=
            "Allow shared-contract documents (only when the corresponding contract change is "
            "agreed)"
    )
    args = parser.parse_args(argv)

    base = args.base or default_base()
    files = changed_files(base)
    if args.owner == "all":
        print(f"{len(files)} changed files (relative to {base}):")
        for path in files:
            print(f"  [{owner_of(path):>7}] {path}")
        return 0

    bad = violations(args.owner, base, args.allow_shared)
    if bad:
        print(f"Ownership Violation({args.owner} Cross the line {len(bad)} files):")
        for path, actual in bad:
            print(f"  [{actual}] {path}")
        print("Shared-contract files need the contract change agreed before they can be modified")
        return 1
    print(f"OK: {len(files)} change files belong to {args.owner}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
