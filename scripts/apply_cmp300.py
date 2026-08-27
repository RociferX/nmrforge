import io

p = "scripts/vm_compare_recon2.py"
s = io.open(p, encoding="utf-8").read()
old = '    for sub in ("nus3d_rc_r50", "nus3d_rc_imag"):'
new = '    for sub in ("nus3d_rc_r300", "nus3d_rc_imag300"):'
assert old in s
s = s.replace(old, new, 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("ok")
