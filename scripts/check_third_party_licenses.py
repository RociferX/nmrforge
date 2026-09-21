#!/usr/bin/env python
"""Verify the third-party licence texts that must ship inside the AppImage.

The AppImage bundles Qt through PySide6, so it *distributes* LGPL-covered libraries. That makes the
shipped licence texts part of the compliance story rather than an afterthought, and this script is
what keeps them honest:

* ``packaging/linux/THIRD_PARTY_LICENSES/PROVENANCE.txt`` is the single source of truth - it records
  where each text came from, its size and its SHA-256;
* every listed file must exist and match, so a text cannot be silently replaced, truncated or
  dropped;
* ``NOTICE.md`` must still describe what is bundled, which licence option is relied on, and how a
  recipient can replace or relink the LGPL libraries.

It runs on any platform, so CI checks it without needing a Linux build machine, and
``packaging/linux/build_appimage.sh`` runs it before the expensive build steps.

Usage::

    python scripts/check_third_party_licenses.py [--quiet]

Exit code 0 = everything matches; 1 = a problem, printed with the reason.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LICENSE_DIR = ROOT / "packaging" / "linux" / "THIRD_PARTY_LICENSES"
PROVENANCE = LICENSE_DIR / "PROVENANCE.txt"
NOTICE = LICENSE_DIR / "NOTICE.md"

ROW_RE = re.compile(
    r"^\|\s*`(?P<name>[^`]+)`\s*\|\s*(?P<url>\S+)\s*\|\s*(?P<size>\d+)\s*\|"
    r"\s*(?P<sha>[0-9a-f]{64})\s*\|\s*$"
)

#: Statements the notice must keep making. Checked by substring because the wording may evolve;
#: removing the *content* is what must fail.
NOTICE_REQUIREMENTS = (
    ("PySide6", "the notice must name the binding that is bundled"),
    ("LGPL-3.0", "the notice must name the licence option relied on"),
    ("Qt", "the notice must name Qt"),
    ("replace", "the notice must explain how the LGPL libraries can be replaced"),
    ("relink", "the notice must explain the relink mechanism"),
    ("corresponding source", "the notice must state where the corresponding source comes from"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_provenance() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in PROVENANCE.read_text(encoding="utf-8").splitlines():
        match = ROW_RE.match(line.strip())
        if match:
            rows.append(match.groupdict())
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quiet", action="store_true", help="only report problems")
    args = parser.parse_args(argv)

    problems: list[str] = []

    if not LICENSE_DIR.is_dir():
        problems.append(f"missing directory: {LICENSE_DIR.relative_to(ROOT)}")
    if not PROVENANCE.is_file():
        problems.append(f"missing provenance file: {PROVENANCE.relative_to(ROOT)}")
    if problems:
        for item in problems:
            print(f"FAIL  {item}")
        return 1

    rows = parse_provenance()
    if not rows:
        problems.append(
            f"{PROVENANCE.relative_to(ROOT)} lists no licence files; expected a table row per text"
        )

    for row in rows:
        path = LICENSE_DIR / row["name"]
        if not path.is_file():
            problems.append(f"missing licence text: {path.relative_to(ROOT)}")
            continue
        actual_size = path.stat().st_size
        actual_sha = _sha256(path)
        if str(actual_size) != row["size"]:
            problems.append(
                f"{row['name']}: size {actual_size} != recorded {row['size']}"
            )
        if actual_sha != row["sha"]:
            problems.append(
                f"{row['name']}: sha256 {actual_sha} != recorded {row['sha']}"
            )
        if not args.quiet and str(actual_size) == row["size"] and actual_sha == row["sha"]:
            print(f"ok    {row['name']}  {actual_size} bytes  sha256 {actual_sha[:16]}...")

    if not NOTICE.is_file():
        problems.append(f"missing notice: {NOTICE.relative_to(ROOT)}")
    else:
        notice_text = NOTICE.read_text(encoding="utf-8")
        for needle, why in NOTICE_REQUIREMENTS:
            if needle not in notice_text:
                problems.append(f"NOTICE.md no longer contains {needle!r}: {why}")

    # The build script must actually stage these files into the AppDir.
    build = (ROOT / "packaging" / "linux" / "build_appimage.sh").read_text(encoding="utf-8")
    if "THIRD_PARTY_LICENSES" not in build:
        problems.append("build_appimage.sh does not reference THIRD_PARTY_LICENSES")
    if "check_third_party_licenses.py" not in build:
        problems.append("build_appimage.sh does not run this check before building")
    if "--licenses" not in build:
        problems.append("the generated AppRun no longer offers --licenses")

    if problems:
        print()
        print(f"FAILED ({len(problems)}):")
        for item in problems:
            print(f"  - {item}")
        return 1

    print()
    print(f"checked {len(rows)} licence files, the notice, and the build script: all consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
