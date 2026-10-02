"""Collect installed dependency licence texts, without machine metadata."""

from __future__ import annotations

import json
import re
import sys
from importlib import metadata
from pathlib import Path, PurePosixPath


def collect_license_data(output: Path, distributions=None, python_prefix=None):
    """Return PyInstaller DATA entries and a relocatable licence manifest."""
    distributions = metadata.distributions() if distributions is None else distributions
    entries = []
    manifest = []
    seen = set()
    for dist in distributions:
        name = re.sub(r"[^a-z0-9_.-]", "_", dist.metadata["Name"].lower())
        if name == "nmrforge" or name in seen:
            continue
        seen.add(name)
        files = []
        for item in dist.files or ():
            relative = PurePosixPath(str(item).replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts:
                continue
            if not any(
                word in relative.name.lower()
                for word in ("license", "licence", "copying", "notice")
            ):
                continue
            source = Path(dist.locate_file(item))
            if not source.is_file():
                raise FileNotFoundError(f"Missing recorded licence: {name}/{relative}")
            target = f"third_party_licenses/{name}/{relative}"
            entries.append((target, str(source), "DATA"))
            files.append(target)
        manifest.append({"name": name, "version": dist.version, "files": files})
    prefix = Path(sys.base_prefix if python_prefix is None else python_prefix)
    python_license = (
        prefix / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/LICENSE.txt"
    )
    if not python_license.is_file():
        raise FileNotFoundError("Python runtime licence is missing from the build interpreter")
    entries.append(("third_party_licenses/python/LICENSE.txt", str(python_license), "DATA"))
    output.mkdir(parents=True, exist_ok=True)
    index = output / "manifest.json"
    index.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    entries.append(("third_party_licenses/manifest.json", str(index), "DATA"))
    return entries
