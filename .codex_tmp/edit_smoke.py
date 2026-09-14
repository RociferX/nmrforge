"""Update scripts/vm_api_smoke.py for the 2026-09-14 combination revision."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
T = "scripts/vm_api_smoke.py"


def edit(rel: str, old: str, new: str, count: int = 1) -> None:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    found = text.count(old)
    if found != count:
        raise SystemExit(f"[{rel}] want {count} matches, got {found}:\n{old[:200]!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")
    print(f"ok {rel}: {found}")


edit(
    T,
    "from nmrforge_api import load_combo_table, run_parameter_study\n",
    "from nmrforge_api import (\n"
    "    PEAK_TABLE_COLUMNS,\n"
    "    load_combo_table,\n"
    "    read_peak_table,\n"
    "    run_parameter_study,\n"
    ")\n",
)

edit(
    T,
    '    parser.add_argument(\n'
    '        "--window-ppm",\n'
    "        type=float,\n"
    "        default=None,\n"
    '        help="峰位搜索窗口半径(ppm);缺省=1.5×该轴核素线宽折算 ppm",\n'
    "    )\n"
    '    parser.add_argument(\n'
    '        "--window-pts",\n'
    "        type=int,\n"
    "        default=None,\n"
    '        help="峰位搜索窗口半径(数据点;显式口径,跨分辨率不可比,不推荐)",\n'
    "    )\n",
    '    parser.add_argument(\n'
    '        "--localization",\n'
    '        choices=("parabolic", "gaussian", "both"),\n'
    '        default="parabolic",\n'
    '        help="组合模式精修方式:parabolic(默认)/ gaussian(仅 2D)/ both",\n'
    "    )\n"
    '    parser.add_argument(\n'
    '        "--edge-margin-ppm",\n'
    "        type=float,\n"
    "        default=None,\n"
    '        help="选峰排除边缘轴峰的物理宽度(ppm;缺省=3×该轴核素线宽)",\n'
    "    )\n",
)

edit(
    T,
    "    result = run_parameter_study(\n"
    "        study,\n"
    "        peaks=Path(args.peaks).expanduser() if args.peaks else None,\n"
    "        max_peaks=args.max_peaks,\n"
    "        window_pts=args.window_pts,\n"
    "        window_ppm=args.window_ppm,\n"
    "        progress=log,\n"
    "        **kwargs,\n"
    "    )\n",
    "    result = run_parameter_study(\n"
    "        study,\n"
    "        peaks=Path(args.peaks).expanduser() if args.peaks else None,\n"
    "        max_peaks=args.max_peaks,\n"
    '        localization=args.localization,\n'
    "        edge_margin_ppm=args.edge_margin_ppm,\n"
    "        progress=log,\n"
    "        **kwargs,\n"
    "    )\n",
)

edit(
    T,
    '    payload = {\n'
    '        "elapsed_s": round(time.time() - started, 1),\n',
    "    def _table_report(run: Any, method: str) -> dict:\n"
    '        """峰表结构核对:列头、行数、reference_peak_id 是否留空。"""\n'
    '        path = run.peak_table_path(method)\n'
    "        if not path:\n"
    '            return {"method": method, "present": False}\n'
    "        rows = read_peak_table(path)\n"
    "        return {\n"
    '            "method": method,\n'
    '            "present": True,\n'
    '            "path": path,\n'
    '            "n_rows": len(rows),\n'
    '            "columns_ok": list(rows[0].keys()) == list(PEAK_TABLE_COLUMNS)\n'
    "            if rows\n"
    "            else None,\n"
    '            "reference_peak_id_all_empty": all(\n'
    '                str(row.get("reference_peak_id", "")) == "" for row in rows\n'
    "            ),\n"
    '            "detected_all_true": all(bool(row.get("detected")) for row in rows),\n'
    '            "peak_ids": [\n'
    '                None if row.get("peak_id") != row.get("peak_id") else int(row["peak_id"])\n'
    "                for row in rows\n"
    "            ],\n"
    '            "localization_methods": sorted(\n'
    '                {str(row.get("localization_method", "")) for row in rows}\n'
    "            ),\n"
    "        }\n"
    "\n"
    "    payload = {\n"
    '        "elapsed_s": round(time.time() - started, 1),\n'
    '        "localization_requested": args.localization,\n',
)

edit(
    T,
    '                "peak_localization": run.peak_localization,\n'
    '                "wall_s": run.wall_time_s,\n'
    '                "window": run.window,\n',
    '                "peak_localization": run.peak_localization,\n'
    '                "wall_s": run.wall_time_s,\n'
    '                "window": run.window,\n'
    '                "detection": (run.parameters_resolved or {}).get("detection"),\n'
    '                "peak_tables_report": [\n'
    '                    _table_report(run, method) for method in ("parabolic", "gaussian")\n'
    "                ],\n",
)

edit(
    T,
    "from pathlib import Path\n",
    "from pathlib import Path\nfrom typing import Any\n",
)

print("edit_smoke done")
