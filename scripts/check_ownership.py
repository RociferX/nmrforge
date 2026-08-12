"""Ownership 检查:确认 Agent 分支的改动没有越界。

用法:
    python scripts/check_ownership.py --owner gui      # GUI Agent 提交前
    python scripts/check_ownership.py --owner backend  # Backend Agent 提交前
    python scripts/check_ownership.py --owner all      # Architect 合并核对
    python scripts/check_ownership.py --base master    # 指定基线(默认 master)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# owner -> 路径前缀(相对仓库根,正斜杠)
GUI_PREFIXES = ("gui/", "viewer/", "main.py", "scripts/make_icon.py")
BACKEND_PREFIXES = (
    "backend/",
    "workflow/",
    "core/data/",
    "core/experiment/",
    "core/experiments/",
    "core/processing/",
    "core/planning/",
    "core/optimization/",
    "core/qc/",
    "core/reporting/",
    "scripts/smile_optimize.py",
    "scripts/param_optimize.py",
    "scripts/recon_phase_search.py",
)

# Shared Contract / Architect 基础设施:任何 Agent 不得随意修改
SHARED_PREFIXES = (
    "core/project/",
    "core/data/internal_data_model.py",
    "backend/base.py",
    "viewer/spectrum.py",
    "gui/processing.py",
    "pyproject.toml",
    ".gitignore",
    "config/",
    ".codex/",
    "docs/",
    "scripts/check_ownership.py",
    "tests/test_ownership.py",
)


def owner_of(path: str) -> str:
    """返回路径所属:gui / backend / shared。"""
    path = path.replace("\\", "/")
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
    return "shared"  # 未匹配的路径一律按 shared 处理(保守)


def changed_files(base: str) -> list[str]:
    """相对基线到 HEAD 的改动文件 + 未跟踪文件。"""
    diff = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMRT", f"{base}...HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    files = [line for line in diff.stdout.splitlines() if line.strip()]
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    files.extend(line for line in untracked.stdout.splitlines() if line.strip())
    return sorted(set(files))


def violations(owner: str, base: str) -> list[tuple[str, str]]:
    """返回 (文件, 实际 owner) 中不属于 owner 的条目。"""
    result: list[tuple[str, str]] = []
    for path in changed_files(base):
        actual = owner_of(path)
        if owner != "all" and actual != owner:
            result.append((path, actual))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ownership 检查")
    parser.add_argument("--owner", choices=("gui", "backend", "all"), default="all")
    parser.add_argument("--base", default="master", help="git 基线(默认 master)")
    args = parser.parse_args(argv)

    files = changed_files(args.base)
    if args.owner == "all":
        print(f"共 {len(files)} 个改动文件(相对 {args.base}):")
        for path in files:
            print(f"  [{owner_of(path):>7}] {path}")
        return 0

    bad = violations(args.owner, args.base)
    if bad:
        print(f"Ownership 违规({args.owner} 越界 {len(bad)} 个文件):")
        for path, actual in bad:
            print(f"  [{actual}] {path}")
        print("Shared Contract 文件必须经 Architect 批准的 Proposal 才能修改。")
        return 1
    print(f"OK: {len(files)} 个改动文件均属于 {args.owner}。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
