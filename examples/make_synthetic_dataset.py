#!/usr/bin/env python
"""Create a small synthetic Bruker dataset for trying nmrForge without real data.

The generator writes a Bruker-style directory layout (``acqus``, ``acqu2s``, ``acqu3s``,
``ser``, optional ``nuslist`` and ``pdata/1/title``) containing **synthetic** time-domain
data: a few exponentially decaying sinusoids plus a little noise. Nothing here is real
spectroscopic data, and it must not be used for scientific conclusions.

``core.data.bruker_reader`` is the authority on layout, so the byte count written here
follows the same conventions the reader enforces.

Examples
--------
2D HSQC-shaped dataset (indirect 15N, direct 1H)::

    python examples/make_synthetic_dataset.py --out example_data/hsqc_2d

3D dataset, non-uniformly sampled::

    python examples/make_synthetic_dataset.py --out example_data/hnca_3d \
        --ndim 3 --nuclei 13C,15N,1H --nus
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np

# 核素 -> (谱宽 Hz, 谱中心 ppm)。仅用于生成合理的表头数值。
NUCLEUS_DEFAULTS: dict[str, tuple[float, float]] = {
    "1H": (10000.0, 4.7),
    "13C": (22000.0, 50.0),
    "15N": (2600.0, 118.0),
    "19F": (30000.0, -120.0),
    "31P": (8000.0, 0.0),
    "2H": (3000.0, 4.7),
}

SFO1_DEFAULTS: dict[str, float] = {
    "1H": 599.89,
    "13C": 150.91,
    "15N": 60.80,
    "19F": 564.5,
    "31P": 242.9,
}

# FnMODE 取值决定超复数分量数（与 core.data.bruker_reader._mult_for 一致）。
FN_MODE_STATES_TPPI = 5
DTYPE_INT32 = 0
BYTORDA_LITTLE_ENDIAN = 0


def _spin_system(n_points: int, *, seed: int) -> np.ndarray:
    """一个直接维的合成复 FID：两个衰减正弦 + 噪声。"""
    rng = np.random.default_rng(seed)
    n = np.arange(n_points, dtype=np.float64)
    signal = np.zeros(n_points, dtype=np.complex128)
    for freq, decay, amplitude in ((0.031, 90.0, 1.0), (0.117, 45.0, 0.6)):
        signal += amplitude * np.exp(2j * math.pi * freq * n) * np.exp(-n / decay)
    noise = rng.normal(0.0, 0.01, n_points) + 1j * rng.normal(0.0, 0.01, n_points)
    return signal + noise


def _modulate(rows: np.ndarray) -> np.ndarray:
    """按间接维行号给每一行加相位调制。"""
    out = rows.copy()
    for index in range(out.shape[0]):
        out[index] = out[index] * np.exp(2j * math.pi * 0.13 * (index // 2))
    return out


def _param_block(pairs: list[tuple[str, object]]) -> str:
    lines = []
    for key, value in pairs:
        rendered = f"{value:.6f}" if isinstance(value, float) else str(value)
        lines.append(f"##${key}= {rendered}")
    lines.append("##END=")
    return "\n".join(lines) + "\n"


def _acqus(direct_nucleus: str, td: int, pulprog: str, title: str, parmode: int) -> str:
    sw_h, o1p = NUCLEUS_DEFAULTS.get(direct_nucleus, (10000.0, 4.7))
    sf = SFO1_DEFAULTS.get(direct_nucleus, 500.0)
    header = [
        "##OWNER= nmrforge",
        f"##TITLE= {title}",
        "##JCAMPDX= 5.0",
        "##DATE= 2026/09/16",
        f"##$PULPROG= {pulprog}",
    ]
    body = _param_block(
        [
            ("TD", td),
            ("SW_h", sw_h),
            ("SW", float(td)),
            ("SFO1", sf),
            ("O1", o1p * sf),
            ("O1P", o1p),
            ("NUC1", direct_nucleus),
            ("NS", 4),
            ("DS", 16),
            ("AQ_mod", 0),
            ("DIGMOD", "digit"),
            ("DECIM", 32),
            ("DSPFVS", 21),
            ("GRPDLY", 48.0),
            ("BYTORDA", BYTORDA_LITTLE_ENDIAN),
            ("DTYPE", DTYPE_INT32),
            ("PARMODE", parmode),
            ("FnMODE", 0),
        ]
    )
    return "\n".join(header) + "\n" + body


def _indirect_block(nucleus: str, td: int, *, highest: bool) -> str:
    sw_h, o1p = NUCLEUS_DEFAULTS.get(nucleus, (3000.0, 100.0))
    sf = SFO1_DEFAULTS.get(nucleus, 500.0)
    pairs: list[tuple[str, object]] = [
        ("TD", td),
        ("SW_h", sw_h),
        ("SW", float(td)),
        ("SFO1", sf),
        ("O1", o1p * sf),
        ("O1P", o1p),
        ("NUC1", nucleus),
        ("PARMODE", 1),
        ("FnMODE", FN_MODE_STATES_TPPI),
        ("FnTYPE", 1),
    ]
    if highest:
        pairs.append(("NusTD", td))
    return _param_block(pairs)


def _multiplier(fnmode: int) -> int:
    return 2 if fnmode in (0, 1, 2, 4, 5, 6) else 1


def build(out: Path, *, ndim: int, nuclei: list[str], td: list[int], nus: bool, seed: int) -> None:
    if ndim not in (2, 3):
        raise SystemExit("--ndim must be 2 or 3")
    if len(nuclei) != ndim:
        raise SystemExit(f"--nuclei needs {ndim} entries for a {ndim}D dataset")

    out.mkdir(parents=True, exist_ok=True)
    direct_nucleus = nuclei[-1]
    title = f"synthetic {ndim}D {'/'.join(nuclei)} dataset"
    pulprog = "hsqcetfpf3gpsi" if ndim == 2 else "hncagp3d"

    td_direct = td[-1]
    mult = [_multiplier(FN_MODE_STATES_TPPI) for _ in range(ndim - 1)]
    row = _spin_system(td_direct, seed=seed)

    if ndim == 2:
        n_rows = td[0] * mult[0]
        data = _modulate(np.tile(row, (n_rows, 1)))
    else:
        n_f1 = td[0] * mult[0]
        n_f2 = td[1] * mult[1]
        # 存储顺序（与 core.data.bruker_reader 一致）：[F1 行][F2 行][直接维]
        data = np.tile(_modulate(np.tile(row, (n_f1, 1))), (n_f2, 1, 1))

    scale = 3.0e7
    limit = 2.0e9
    # 实部与虚部分别裁剪后再交错写入:复数整体 astype 会丢掉虚部。
    scaled_re = np.clip(data.real * scale, -limit, limit)
    scaled_im = np.clip(data.imag * scale, -limit, limit)
    interleaved = np.empty(data.size * 2, dtype=np.int32)
    interleaved[0::2] = scaled_re.reshape(-1).astype(np.int32)
    interleaved[1::2] = scaled_im.reshape(-1).astype(np.int32)

    (out / "acqus").write_text(
        _acqus(direct_nucleus, td_direct, pulprog, title, ndim - 1), encoding="latin-1"
    )
    (out / "acqu2s").write_text(
        _indirect_block(nuclei[1] if ndim == 3 else nuclei[0], td[1] if ndim == 3 else td[0],
                        highest=False),
        encoding="latin-1",
    )
    if ndim == 3:
        (out / "acqu3s").write_text(
            _indirect_block(nuclei[0], td[0], highest=True), encoding="latin-1"
        )
    interleaved.tofile(out / "ser")

    pdata = out / "pdata" / "1"
    pdata.mkdir(parents=True, exist_ok=True)
    (pdata / "title").write_text(title + "\n", encoding="latin-1")

    if nus:
        if ndim == 2:
            grid = td[0] * mult[0]
            keep = sorted({0, *range(2, grid, 4)})
            lines = [f"{index + 1} 1" for index in keep]
        else:
            lines = ["1 1 1" for _ in range(max(1, (td[0] * td[1]) // 4))]
        (out / "nuslist").write_text("\n".join(lines) + "\n", encoding="latin-1")

    (out / "README.txt").write_text(
        "SYNTHETIC DATA - NOT FOR SCIENTIFIC USE.\n"
        f"Generated by examples/make_synthetic_dataset.py ({ndim}D, nuclei={'/'.join(nuclei)}).\n"
        "Headers are Bruker-style; the time-domain data is a sum of synthetic sinusoids.\n"
        + ("A deliberately incomplete nuslist is included.\n" if nus else ""),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="output dataset directory")
    parser.add_argument("--ndim", type=int, default=2, choices=(2, 3), help="dimensionality")
    parser.add_argument(
        "--nuclei",
        default="15N,1H",
        help="comma separated nuclei ordered from the indirect (highest numbered) axis to "
        "the direct axis, e.g. '15N,1H' for 2D or '13C,15N,1H' for 3D",
    )
    parser.add_argument("--nus", action="store_true", help="also write a (partial) nuslist")
    parser.add_argument("--seed", type=int, default=20260916, help="random seed")
    parser.add_argument("--force", action="store_true", help="overwrite a non-empty directory")
    args = parser.parse_args(argv)

    nuclei = [item.strip() for item in args.nuclei.split(",") if item.strip()]
    td = [32, 256] if args.ndim == 2 else [16, 24, 128]

    out = args.out
    if out.exists() and any(out.iterdir()) and not args.force:
        raise SystemExit(f"{out} already exists and is not empty; pass --force to overwrite")

    build(out, ndim=args.ndim, nuclei=nuclei, td=td, nus=args.nus, seed=args.seed)

    print(f"dataset:        {out}")
    print(f"dimensionality: {args.ndim}D   nuclei (indirect -> direct): {'/'.join(nuclei)}")
    print(f"ser bytes:      {(out / 'ser').stat().st_size}")
    if args.nus:
        print("nuslist:        written (deliberately partial schedule)")
    print()
    print("Next step:")
    print(f"  python examples/quickstart.py {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
