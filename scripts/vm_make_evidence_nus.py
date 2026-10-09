"""Create reproducible pseudo-NUS evidence from fully sampled Bruker 2D/3D raw data.

This is not an acquisition simulator or a product import path. It selects complete
hypercomplex increments without altering retained bytes. Only AQSEQ=0, complex
direct acquisition, and explicitly declared States/States-TPPI/Echo-Antiecho
indirect axes are supported. The 3D row order follows NMRPipe nusCompress.tcl:
for each (y,z), extract both y phases from each of the two z phase planes.
Existing destinations are never overwritten. A manifest identifies the source,
schedule, retained raw bytes, grid, seed, and actual sampling fraction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.data.bruker_dtype import sample_dtype  # noqa: E402
from core.data.ser_layout import solve_row_points  # noqa: E402


def _params(text: str) -> dict[str, str]:
    return dict(re.findall(r"^##\$\s*(\w+)\s*=\s*(\S+)", text, re.MULTILINE))


def _set(text: str, key: str, value: object) -> str:
    pattern = rf"^##\$\s*{re.escape(key)}\s*=\s*[^\r\n]*"
    line = f"##${key}= {value}"
    if re.search(pattern, text, re.MULTILINE):
        return re.sub(pattern, lambda _: line, text, count=1, flags=re.MULTILINE)
    # Keep new scalar parameters before the JCAMP terminator.
    if "##END=" in text:
        return text.replace("##END=", f"{line}\n##END=", 1)
    return text.rstrip() + f"\n{line}\n"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_fingerprint(source: Path) -> dict:
    files = {p.name: _sha(p) for p in sorted(source.iterdir()) if p.is_file()}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {"sha256": digest, "files": files}


def make_nus(source: Path, destination: Path, fraction: float, seed: int) -> dict:
    source, destination = source.resolve(), destination.resolve()
    if destination == source or source in destination.parents or destination in source.parents:
        raise ValueError("source and destination must be disjoint directories")
    if destination.exists():
        raise ValueError("destination already exists; choose a new directory")
    if not math.isfinite(fraction) or not 0 < fraction < 1:
        raise ValueError("fraction must be finite and strictly between zero and one")
    texts = [(source / name).read_text(encoding="utf-8", errors="replace")
             for name in ("acqus", "acqu2s")]
    if (source / "acqu3s").exists():
        texts.append((source / "acqu3s").read_text(encoding="utf-8", errors="replace"))
    if (source / "acqu4s").exists():
        raise ValueError("only 2D/3D evidence inputs are supported")
    params = [_params(text) for text in texts]
    if int(params[0].get("AQSEQ", "0")) != 0:
        raise ValueError("only explicitly supported AQSEQ=0 row order is accepted")
    if int(params[0].get("AQ_mod", "-1")) not in {1, 3}:
        raise ValueError("direct acquisition must explicitly declare complex AQ_mod=1/3")
    if (source / "nuslist").exists() or any(
        int(par.get("FnTYPE", "0")) == 2 for par in params
    ):
        raise ValueError("input must be fully sampled, not an existing NUS dataset")
    td = [int(par["TD"]) for par in params]
    if any(value <= 0 or value % 2 for value in td):
        raise ValueError("all complex dimensions require positive even TD values")
    if any(int(par.get("FnMODE", "-1")) not in {4, 5, 6} for par in params[1:]):
        raise ValueError("indirect FnMODE must explicitly be States/States-TPPI/Echo-Antiecho")
    dtype = sample_dtype(params[0])
    raw = source / "ser"
    size = raw.stat().st_size
    row_points = solve_row_points(td[0], dtype.itemsize, size)
    if row_points is None:
        raise ValueError("cannot determine a nonempty padded raw row")
    row_bytes = row_points * dtype.itemsize
    rows = math.prod(td[1:])
    if size != row_bytes * rows:
        raise ValueError("ser must exactly fill the declared, padded uniform grid")
    grid = [value // 2 for value in td[1:]]
    total = math.prod(grid)
    count = max(2, int(round(total * fraction)))
    if count >= total:
        raise ValueError("requested fraction leaves no omitted increments")
    # Include the origin and final corner, making the declared extent observable.
    rng = np.random.default_rng(seed)
    selected = sorted([0, total - 1, *rng.choice(
        np.arange(1, total - 1), size=count - 2, replace=False
    ).tolist()])
    coordinates = [(index % grid[0], index // grid[0]) if len(grid) == 2 else (index,)
                   for index in selected]
    fingerprint = _source_fingerprint(source)
    data = np.memmap(raw, mode="r", dtype=np.uint8, shape=(rows, row_bytes))
    # Validate everything before creating output; retain failed partial output for diagnosis.
    destination.mkdir(parents=True)
    for path in source.iterdir():
        if path.is_file() and path.name not in {"ser", "fid", "nuslist"}:
            shutil.copy2(path, destination / path.name)
    schedule = "\n".join(" ".join(map(str, coord)) for coord in coordinates) + "\n"
    (destination / "nuslist").write_text(schedule, encoding="ascii")
    with (destination / "ser").open("wb") as stream:
        for coord in coordinates:
            y = coord[0]
            for z_phase in range(2 if len(grid) == 2 else 1):
                base = (2 * coord[1] + z_phase) * td[1] if len(grid) == 2 else 0
                for y_phase in range(2):
                    stream.write(data[base + 2 * y + y_phase].tobytes())
    del data
    names = ("acqus", "acqu2s", "acqu3s")
    texts[0] = _set(_set(texts[0], "FnTYPE", 2), "NUSLIST", "<nuslist>")
    texts[0] = _set(texts[0], "NusAMOUNT", 100 * count / total)
    for index in range(1, len(texts)):
        # Bruker/NMRPipe NusTD is the full TD including both quadrature components.
        # Schedule coordinates use the complex grid (TD/2), not these real-row counts.
        texts[index] = _set(texts[index], "NusTD", td[index])
    for name, text in zip(names, texts, strict=False):
        (destination / name).write_text(text, encoding="utf-8")
    manifest = {
        "kind": "controlled_downsampling_of_uniform_raw_data",
        "ndim": len(texts), "seed": seed, "requested_fraction": fraction,
        "complex_grid": grid, "sample_count": count, "actual_fraction": count / total,
        "raw_row_bytes": row_bytes, "retained_rows": count * 2 ** len(grid),
        "source": fingerprint, "nuslist_sha256": _sha(destination / "nuslist"),
        "ser_sha256": _sha(destination / "ser"),
        "row_order": "AQSEQ=0; y-fast; z-phase outer, y-phase inner",
    }
    if _source_fingerprint(source) != fingerprint:
        raise ValueError("source changed during downsampling; output must not be used")
    (destination / "evidence_nus.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--fraction", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=20261008)
    args = parser.parse_args(argv)
    try:
        result = make_nus(args.source, args.destination, args.fraction, args.seed)
    except (OSError, KeyError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
