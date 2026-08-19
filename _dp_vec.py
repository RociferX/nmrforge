"""窗口指标向量化(一次性脚本):内窗批处理 + 边缘窗标量,结果逐位一致。"""

from __future__ import annotations

from pathlib import Path

path = Path("core/optimization/phase_search.py")
text = path.read_text(encoding="utf-8")

# 1) 向量化指标 helper(放在 _symmetry_sign_metric 后)
anchor = "def _signal_peak_windows("
helpers = (
    "def _symmetry_metric_vectorized(f2d: np.ndarray) -> np.ndarray:\n"
    "    \"\"\"(W, width) 窗口批量对称性指标,与 _symmetry_sign_metric 逐窗一致。\"\"\"\n"
    "    n = f2d.shape[1]\n"
    "    half = n // 2\n"
    "    left = f2d[:, :half]\n"
    "    right = f2d[:, n - half :][:, ::-1]\n"
    "    denom = 2.0 * (left**2 + right**2) + 1e-12\n"
    "    sym = np.mean((left + right) ** 2 / denom, axis=1)\n"
    "    if n % 2 == 1:\n"
    "        c = f2d[:, half]\n"
    "        cterm = c**2 / (c**2 + 1e-12)\n"
    "        sym = (sym * half + cterm) / (half + 1)\n"
    "    sign_ok = f2d.sum(axis=1) >= 0.0\n"
    "    return np.where(sign_ok, sym, sym * 0.05)\n"
    "\n"
    "\n"
    "def _net_metric_vectorized(f2d: np.ndarray) -> np.ndarray:\n"
    "    \"\"\"(W, width) 窗口批量净吸收指标,与 _net_window_metric 逐窗一致。\"\"\"\n"
    "    positive = np.clip(f2d, 0.0, None).sum(axis=1)\n"
    "    negative = np.clip(f2d, None, 0.0).sum(axis=1)\n"
    "    total = np.abs(f2d).sum(axis=1)\n"
    "    return np.where(total > 0, (positive + negative) / total, 0.0)\n"
    "\n"
    "\n"
)
if anchor in text and "def _symmetry_metric_vectorized" not in text:
    text = text.replace(anchor, helpers + anchor, 1)
    print("phase_search: 已加向量化窗口指标")

# 2) _score 改为内窗向量化 + 边缘窗标量
old = (
    "    flat = np.moveaxis(comp, axis, -1).reshape(-1, n)\n"
    "    width = 2 * radius + 1\n"
    "    seg_slices = [\n"
    "        (max(0, peak - radius), min(n, peak + radius + 1))\n"
    "        for _, peak in windows\n"
    "    ]\n"
    "    # 等宽零填充:窗口指标按元素求和/比值,零不贡献,与不等宽切片等价\n"
    "    segments = np.zeros((len(windows), width), dtype=np.complex128)\n"
    "    for j, ((i, _), (lo, hi)) in enumerate(zip(windows, seg_slices)):\n"
    "        segments[j, : hi - lo] = flat[i, lo:hi]\n"
    "\n"
    "    def _score(p0: float, p1: float) -> float:\n"
    "        k = np.arange(n, dtype=float)\n"
    "        base = np.deg2rad(p0 + p1 * k / max(n - 1, 1))\n"
    "        seg_ramp = np.zeros((len(segments), width), dtype=np.complex128)\n"
    "        for j, (lo, hi) in enumerate(seg_slices):\n"
    "            seg_ramp[j, : hi - lo] = np.exp(1j * base[lo:hi])\n"
    "        rot = np.real(segments * seg_ramp)\n"
    "        vals = [window_metric(rot[j]) for j in range(len(segments))]\n"
    "        if metric == \"symmetry\":\n"
    "            return 100.0 * float(np.mean(vals))\n"
    "        return 50.0 * (float(np.median(vals)) + 1.0)\n"
)
new = (
    "    flat = np.moveaxis(comp, axis, -1).reshape(-1, n)\n"
    "    width = 2 * radius + 1\n"
    "    k_arr = np.arange(n, dtype=float) / max(n - 1, 1)\n"
    "    # 内窗(满宽)批处理,边缘窗(不满宽)少量标量循环——结果与逐窗一致\n"
    "    interior_segs: list[np.ndarray] = []\n"
    "    interior_ks: list[np.ndarray] = []\n"
    "    edge_items: list[tuple[np.ndarray, np.ndarray]] = []\n"
    "    for (i, peak), _ in zip(windows, windows):\n"
    "        lo, hi = max(0, peak - radius), min(n, peak + radius + 1)\n"
    "        seg = flat[i, lo:hi]\n"
    "        kseg = k_arr[lo:hi]\n"
    "        if hi - lo == width:\n"
    "            interior_segs.append(seg)\n"
    "            interior_ks.append(kseg)\n"
    "        else:\n"
    "            edge_items.append((seg, kseg))\n"
    "    if interior_segs:\n"
    "        seg_int = np.stack(interior_segs)  # (Wi, width) complex\n"
    "        k_int = np.stack(interior_ks)  # (Wi, width)\n"
    "    else:\n"
    "        seg_int = np.empty((0, width), dtype=np.complex128)\n"
    "        k_int = np.empty((0, width))\n"
    "\n"
    "    def _score(p0: float, p1: float) -> float:\n"
    "        ramp_int = np.exp(1j * np.deg2rad(p0 + p1 * k_int))\n"
    "        rot_int = np.real(seg_int * ramp_int)\n"
    "        if metric == \"symmetry\":\n"
    "            vals = _symmetry_metric_vectorized(rot_int).tolist()\n"
    "            for seg, kseg in edge_items:\n"
    "                r = np.real(seg * np.exp(1j * np.deg2rad(p0 + p1 * kseg)))\n"
    "                vals.append(_symmetry_sign_metric(r))\n"
    "            return 100.0 * float(np.mean(vals))\n"
    "        vals = _net_metric_vectorized(rot_int).tolist()\n"
    "        for seg, kseg in edge_items:\n"
    "            r = np.real(seg * np.exp(1j * np.deg2rad(p0 + p1 * kseg)))\n"
    "            vals.append(_net_window_metric(r))\n"
    "        return 50.0 * (float(np.median(vals)) + 1.0)\n"
)
if old in text:
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8", newline="\n")
    print("phase_search: _score 已向量化(内窗批处理 + 边缘窗标量)")
else:
    print("提示:_score 锚点未匹配")
