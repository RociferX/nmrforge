import io

p = "scripts/vm_check_pairing.py"
s = io.open(p, encoding="utf-8").read()
s = s.replace('real = _complex("nus3d_1", 1)', 'real = _complex("nus3d_1_r300", 1)')
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("ok")
