import io

p = "scripts/vm_check_quadrature.py"
s = io.open(p, encoding="utf-8").read()
old = "    cplx = cplx_raw[0::2] + 1j * cplx_raw[1::2]"
new = (
    "    if np.iscomplexobj(cplx_raw):\n"
    "        cplx = cplx_raw\n"
    "    else:\n"
    "        cplx = cplx_raw[0::2] + 1j * cplx_raw[1::2]"
)
assert old in s
s = s.replace(old, new, 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("ok")
