"""一步式示例:参考谱/参考峰位/参数扫描/汇总。

环境变量(或直接改下面的常量):
    NMRFORGE_API_STUDY  研究根(默认 ~/studies/hsqc_params)
    NMRFORGE_API_DATA   Bruker 原始数据目录(必需)
    NMRFORGE_API_GRID   可选,网格 JSON;默认用下面的 DEFAULT_AXES
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from nmrforge_api import SensitivityError, run_parameter_study

STUDY = Path(os.environ.get("NMRFORGE_API_STUDY", "~/studies/hsqc_params")).expanduser()
DATA = os.environ.get("NMRFORGE_API_DATA", "")
DEFAULT_AXES = {
    "window.F1.off": [0.35, 0.45, 0.55],   # 间接维窗函数
    "zero_fill": [1, 2, 4],                # 填零(数字分辨率)
}


def main() -> int:
    if not DATA:
        print("请设置 NMRFORGE_API_DATA=<Bruker 数据目录>")
        return 2
    grid_env = os.environ.get("NMRFORGE_API_GRID", "")
    axes = (
        json.loads(Path(grid_env).read_text(encoding="utf-8"))["axes"]
        if grid_env
        else DEFAULT_AXES
    )
    try:
        result = run_parameter_study(
            STUDY,
            DATA,
            axes=axes,
            progress=lambda message: print(message, flush=True),
        )
    except SensitivityError as exc:
        print(f"失败: {exc}")
        return 2

    print("\n=== 参考 ===")
    print("参考谱     :", result.reference.frozen_spectrum)
    print("参考脚本   :", result.reference.script_path, result.reference.script_sha256[:12])
    print("参考峰表   :", result.reference.peak_table_path,
          f"({result.reference.peak_count} 峰, 来源 {result.reference.peak_source})")
    print("相位锁定   :", bool(result.reference.direct_phase), result.reference.direct_phase)

    print("\n=== 扫描 ===")
    print(f"组合 {len(result.runs)} 个, 失败 {len(result.failed_runs)} 个")
    for run in result.runs[:5]:
        print(f"  {run.run_id} {run.combo} -> {run.status} ({run.wall_time_s}s)")

    print("\n=== 不确定度(CSP 判据下限) ===")
    print(json.dumps(result.summary.get("delta_std_ppm", {}), ensure_ascii=False))
    print("\n记录文件:")
    for name, path in result.records.items():
        print(f"  {name:20s} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
