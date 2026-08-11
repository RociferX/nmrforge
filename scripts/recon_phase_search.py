"""NMRForge 重构输出相位搜索报告（方案 B，验证用，不改管线）。

用法（VM）：
    PYTHONPATH=$HOME/NMRForge python scripts/recon_phase_search.py \
      <nus3d_rc 目录> [--out report.json]
输出：每轴 p0/p1/score/gain 表格 + JSON。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from core.data.pipe_io import read_pipe_header, read_pipe_planes
from workflow.recon_phase_search import (
    format_report,
    save_report,
    search_recon_phase,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重构输出相位搜索报告（验证用）")
    parser.add_argument("planes_dir", help="包含 test*.ft1 的复型重构平面目录")
    parser.add_argument("--out", default="", help="JSON 报告输出路径")
    args = parser.parse_args(argv)

    directory = Path(args.planes_dir)
    planes = read_pipe_planes(directory)
    print(f"复型平面: {planes.shape[0]} 个，单平面 {planes.shape[1:]}，dtype={planes.dtype}")
    first = sorted(directory.glob("test*.ft1"))[0]
    header = read_pipe_header(first)
    labels = {k: header.get(k) for k in ("FDF1LABEL", "FDF2LABEL", "FDF3LABEL")}
    print(f"头部标签: {labels}（axis0←FDF3, axis1←FDF1, axis2←FDF2，NMRPipe 轴序怪癖）")

    results = search_recon_phase(planes)
    print()
    print(format_report(results))

    out = Path(args.out) if args.out else directory.parent / "recon_phase_report.json"
    save_report(results, out)
    print(f"\n报告已保存: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
