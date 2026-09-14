"""ROI cap: default OFF (result-preserving); opt-in guardrail + retry-uncapped."""

from __future__ import annotations

from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if new in text and old not in text:
        print("skip (already applied):", path)
        return
    if text.count(old) != 1:
        raise SystemExit(f"{path}: anchor found {text.count(old)}x:\n{old[:220]}")
    target.write_text(text.replace(old, new), encoding="utf-8")
    print("patched", path)


LOC = "core/peaks/localize.py"

replace(
    LOC,
    "#: 拟合窗口每轴**最大半宽(点)**:细网格(填零)下限制拟合规模,\n"
    "#: 使单峰拟合成本不再随点数线性增长(2026-09-14 用户:填零翻倍后高斯拟合 26×)。\n"
    "DEFAULT_GAUSSIAN_ROI_MAX_POINTS = 48\n",
    "#: 拟合窗口每轴**最大半宽(点)**:0 = 不限制(默认,结果与旧版一致)。\n"
    "#: 设为正值(如 48)可限制细网格(填零)下的拟合规模、换取速度;触发时逐峰留档\n"
    "#: (roi_capped),失败会自动用完整 ROI 重试(见 localize_peak_gaussian_2d)。\n"
    "DEFAULT_GAUSSIAN_ROI_MAX_POINTS = 0\n",
)

replace(
    LOC,
    '        "gaussian_roi_max_points": _positive_int(\n'
    '            section.get("gaussian_roi_max_points"),\n'
    "            DEFAULT_GAUSSIAN_ROI_MAX_POINTS,\n"
    "        ),\n",
    '        "gaussian_roi_max_points": _nonnegative_int(\n'
    '            section.get("gaussian_roi_max_points"),\n'
    "            DEFAULT_GAUSSIAN_ROI_MAX_POINTS,\n"
    "        ),\n",
)
replace(
    LOC,
    "def _positive_int(value: Any, fallback: int) -> int:\n"
    '    """正值整数(非法/非正回退 fallback)。"""\n'
    "    try:\n"
    "        number = int(value)\n"
    "    except (TypeError, ValueError):\n"
    "        return int(fallback)\n"
    "    return number if number > 0 else int(fallback)\n",
    "def _positive_int(value: Any, fallback: int) -> int:\n"
    '    """正值整数(非法/非正回退 fallback)。"""\n'
    "    try:\n"
    "        number = int(value)\n"
    "    except (TypeError, ValueError):\n"
    "        return int(fallback)\n"
    "    return number if number > 0 else int(fallback)\n"
    "\n"
    "\n"
    "def _nonnegative_int(value: Any, fallback: int) -> int:\n"
    '    """非负整数(0 合法 = 不限制;非法/负数回退 fallback)。"""\n'
    "    try:\n"
    "        number = int(value)\n"
    "    except (TypeError, ValueError):\n"
    "        return int(fallback)\n"
    "    return number if number >= 0 else int(fallback)\n",
)

replace(
    LOC,
    "    fit_points = [min(int(p), max(cap, 1)) for p in roi_points]\n",
    "    fit_points = (\n"
    "        [int(p) for p in roi_points]\n"
    "        if cap <= 0\n"
    "        else [min(int(p), cap) for p in roi_points]\n"
    "    )\n",
)

retry_block = (
    "    if not fit.success and any(roi_capped):\n"
    "        # 上限可能缩小了 ROI → 用完整 ROI 重试一次(成本只在难收敛峰上付);\n"
    "        # 仍失败才回退抛物线,并把两次尝试都留在 fit_meta 里。\n"
    "        retry = fit_gaussian_2d(\n"
    "            real,\n"
    "            seed=seed,\n"
    "            roi=(roi_by_axis_raw[0], roi_by_axis_raw[1]),\n"
    "            sign=1 if int(sign) >= 0 else -1,\n"
    "            max_rmse_ratio=float(max_rmse_ratio or 0.0),\n"
    "            max_nfev=int(max_nfev),\n"
    "        )\n"
    "        if retry.success:\n"
    "            position = tuple(\n"
    "                retry.center[axis] if axis in (0, 1) else parabolic.position[axis]\n"
    "                for axis in range(real.ndim)\n"
    "            )\n"
    "            ppm = {\n"
    '                "F1": ppm_from_point(ppm_axes[axis_f1], position[axis_f1]),\n'
    '                "F2": ppm_from_point(ppm_axes[axis_f2], position[axis_f2]),\n'
    "            }\n"
    "            meta = _fit_meta(fit_points, roi_points, roi_capped, max_nfev)\n"
    '            meta["fit_retry_uncapped"] = True\n'
    '            meta["roi_half_points"] = [int(p) for p in roi_points]\n'
    '            meta["retry_n_iter"] = int(retry.n_iter)\n'
    "            step_f1 = axis_units.ppm_per_point(ppm_axes[axis_f1])\n"
    "            step_f2 = axis_units.ppm_per_point(ppm_axes[axis_f2])\n"
    "            return PeakLocalization(\n"
    '                requested_method="gaussian",\n'
    '                actual_method="gaussian",\n'
    "                position=position,\n"
    "                success=True,\n"
    "                gaussian=retry,\n"
    "                ppm=ppm,\n"
    "                derived={\n"
    '                    "center_f1": ppm["F1"],\n'
    '                    "center_f2": ppm["F2"],\n'
    '                    "sigma_f1": float(retry.sigma[axis_f1]) * step_f1,\n'
    '                    "sigma_f2": float(retry.sigma[axis_f2]) * step_f2,\n'
    '                    "fwhm_f1": float(retry.sigma[axis_f1]) * step_f1 * FWHM_FACTOR,\n'
    '                    "fwhm_f2": float(retry.sigma[axis_f2]) * step_f2 * FWHM_FACTOR,\n'
    "                },\n"
    "                fit_meta=meta,\n"
    "            )\n"
)
replace(
    LOC,
    "    if not fit.success:\n"
    "        # 回退抛物线:位置仍可用,但 requested/actual/原因全部留档(不静默)\n"
    "        return PeakLocalization(\n",
    retry_block
    + "    if not fit.success:\n"
    "        # 回退抛物线:位置仍可用,但 requested/actual/原因全部留档(不静默)\n"
    "        return PeakLocalization(\n",
)

replace(
    LOC,
    "    roi_by_axis = [0.0, 0.0]\n"
    "    roi_by_axis[axis_f1] = float(fit_points[0])\n"
    "    roi_by_axis[axis_f2] = float(fit_points[1])\n",
    "    roi_by_axis = [0.0, 0.0]\n"
    "    roi_by_axis[axis_f1] = float(fit_points[0])\n"
    "    roi_by_axis[axis_f2] = float(fit_points[1])\n"
    "    roi_by_axis_raw = [0.0, 0.0]\n"
    "    roi_by_axis_raw[axis_f1] = float(roi_points[0])\n"
    "    roi_by_axis_raw[axis_f2] = float(roi_points[1])\n",
)

replace(
    "config/nmrforge.yaml",
    "    gaussian_roi_max_points: 48 # 拟合窗口每轴**最大半宽(点)**:细网格(填零)下限制\n"
    "                                # 拟合规模,单峰成本不再随点数线性增长(留档 roi_capped)\n",
    "    gaussian_roi_max_points: 0  # 拟合窗口每轴**最大半宽(点)**:0 = 不限制(默认,\n"
    "                                # 结果与旧版一致);设 48 等正值可限制细网格(填零)\n"
    "                                # 下的拟合规模、显著提速(触发时逐峰留档 roi_capped,\n"
    "                                # 拟合失败会自动用完整 ROI 重试)\n",
)

print("roi cap default-off patched")
