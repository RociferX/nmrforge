import io

p = "core/optimization/phase_search.py"
s = io.open(p, encoding="utf-8").read()

old = (
    "    best_p1, best_conc = 0.0, -1.0\n"
    "    for p1 in np.arange(-90.0, 91.0, coarse_step):\n"
    "        conc = _concentration(float(p1))\n"
    "        if conc > best_conc:\n"
    "            best_conc, best_p1 = conc, float(p1)\n"
    "    for span, step in ((30.0, 5.0), (10.0, 2.5)):\n"
    "        for offset in np.arange(-span, span + 1e-9, step):\n"
    "            conc = _concentration(best_p1 + offset)\n"
    "            if conc > best_conc:\n"
    "                best_conc, best_p1 = conc, best_p1 + offset\n"
    "    return best_p1, best_conc"
)
new = (
    "    best_p1, best_conc = 0.0, -1.0\n"
    "    for p1 in np.arange(-90.0, 91.0, coarse_step):\n"
    "        conc = _concentration(float(p1))\n"
    "        if conc > best_conc:\n"
    "            best_conc, best_p1 = conc, float(p1)\n"
    "    for span, step in ((30.0, 5.0), (10.0, 2.5)):\n"
    "        for offset in np.arange(-span, span + 1e-9, step):\n"
    "            conc = _concentration(best_p1 + offset)\n"
    "            if conc > best_conc:\n"
    "                best_conc, best_p1 = conc, best_p1 + offset\n"
    "    # 0.2.199-补29i:细化会漂出粗搜范围(短轴上伪峰,实测 ±220),钳回\n"
    "    best_p1 = float(np.clip(best_p1, -90.0, 90.0))\n"
    "    return best_p1, best_conc"
)
assert old in s, "p1 fit anchor"
s = s.replace(old, new, 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("p1 clamp added")
