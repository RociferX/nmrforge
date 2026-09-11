"""从全采样 Bruker 2D 数据构造 NUS 数据集(用户方案:全采样 → 合成 NUS)。

思路(与 3D NUS 真实数据同构):
- 目录放 nuslist(sampling_detector 据此判 NUS);
- ser 只保留采样点的 FID 对(States 超复数:每 t1 复点 2 个 FID);
- acqu2s 设 NusTD(单位:增量行,与真实 NUS 数据一致;nuslist 索引才是复点),
  acqus 设 NusAMOUNT(<100)。

用法(VM):
    ~/NMRForge/nmrforge/bin/python scripts/vm_sample_make_nus.py \
        <全采样目录> <输出目录> [--points 32] [--seed 42]

输出:构造完成的 NUS 数据集(供 vm_sample_regression.py 处理)。
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import numpy as np


def _param(text: str, key: str) -> int:
    match = re.search(r"^##\$\s*" + re.escape(key) + r"\s*=\s*(\S+)", text, re.M)
    return int(match.group(1)) if match else 0


def _set_param(text: str, key: str, value: int) -> str:
    if re.search(r"^##\$\s*" + re.escape(key) + r"\s*=", text, re.M):
        text = re.sub(
            r"^##\$\s*" + re.escape(key) + r"\s*=\s*\S+",
            f"##${key}= {value}",
            text,
            count=1,
            flags=re.M,
        )
    else:
        text += f"\n##${key}= {value}\n"
    return text


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--points", type=int, default=32)
    parser.add_argument("--seed", type=int, default=42)
    opts = parser.parse_args(args)

    src = Path(opts.dataset)
    dst = Path(opts.out_dir)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    acqu2s = (dst / "acqu2s").read_text(encoding="utf-8", errors="replace")
    acqus = (dst / "acqus").read_text(encoding="utf-8", errors="replace")
    td_rows = _param(acqu2s, "TD")  # 增量(行)总数
    fnmode = _param(acqu2s, "FnMODE")
    mult = 2 if fnmode in (0, 1, 2, 4, 5, 6) else 1  # States/TPPI 超复数分量
    grid = td_rows // mult  # 间接维复点网格 = nuslist 索引范围
    # 0.2.199-补29hz-修11(VM 实测):真实 NUS 数据的 NusTD 是**行(增量)**单位
    # (sampleJ acqu2s NusTD=292 ↔ nuslist 列 max 145 = 292/2-1;sampleC NusTD=40 ↔ max 19)。
    # 之前这里写成复点数(TD/mult),程序按 -yT NusTD//2 传给 nusExpand 就小于
    # nuslist 的最大索引 → nusExpand 越界崩溃(实测 rc=139 / stderr 通道报错),
    # 转换卡在 nmrPipe -fn MULT。
    nus_td = grid * mult  # = td_rows(全采样源:整格都在)
    if grid <= 0:
        print(f"acqu2s TD 无效: {td_rows}")
        return 1
    if opts.points >= grid:
        print(f"采样点数 {opts.points} 需小于网格 {grid}")
        return 1

    rng = np.random.default_rng(opts.seed)
    points = sorted(int(v) for v in rng.choice(grid, size=opts.points, replace=False))
    # nusExpand -off 取 nuslist 首点:真实 Bruker nuslist 首点为 0,
    # 首点非 0 会导致展开偏移(2D NUS 验证发现 -off 19 → 15N 偏移 ~2.6 ppm)。
    if 0 not in points:
        points[0] = 0
    (dst / "nuslist").write_text(
        "\n".join(str(p) for p in points) + "\n", encoding="utf-8"
    )

    ser = np.fromfile(src / "ser", dtype="<i4")
    if ser.size % 2:
        print("ser 字节数非偶")
        return 1
    complex_data = ser.reshape(-1, 2)[:, 0] + 1j * ser.reshape(-1, 2)[:, 1]
    n_fids_total = nus_td  # = mult * grid:States 超复数的行总数
    points_per_fid = complex_data.size // n_fids_total
    if complex_data.size != n_fids_total * points_per_fid:
        print(f"ser 大小与网格不符: {complex_data.size} != {n_fids_total}×{points_per_fid}")
        return 1
    fids = complex_data.reshape(n_fids_total, points_per_fid)
    rows = [mult * p + k for p in points for k in range(mult)]
    new_fids = fids[np.array(rows, dtype=int)]
    interleaved = np.stack([new_fids.real, new_fids.imag], axis=-1)
    interleaved.astype("<i4").tofile(dst / "ser")

    acqu2s = _set_param(acqu2s, "NusTD", nus_td)
    acqus = _set_param(acqus, "NusAMOUNT", round(100 * opts.points / grid))
    (dst / "acqu2s").write_text(acqu2s, encoding="utf-8")
    (dst / "acqus").write_text(acqus, encoding="utf-8")

    new_size = (dst / "ser").stat().st_size
    print(f"构造完成: {dst}")
    print(
        f"  复点网格={grid}(NusTD={nus_td} 行) 采样点={opts.points} "
        f"({100*opts.points/grid:.1f}%)"
    )
    print(f"  nuslist 前 8 行: {(dst / 'nuslist').read_text().splitlines()[:8]}")
    print(f"  ser: {len(rows)} FIDs × {points_per_fid} 点 = {new_size} 字节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
