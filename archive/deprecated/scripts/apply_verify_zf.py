import io

p = "scripts/vm_verify_consensus.py"
s = io.open(p, encoding="utf-8").read()
old = '    zf_none = {"zero_fill": {a: {"mode": "none"} for a in axes}}'
new = (
    '    zf_none = {"zero_fill": {a: {"mode": "none"} for a in axes}}\n'
    '    zf_real = {"zero_fill": {"F2": {"mode": "size", "size": 128},\n'
    '                              "F1": {"mode": "size", "size": 256},\n'
    '                              "F3": {"mode": "none"}}}'
)
assert old in s
s = s.replace(old, new, 1)
old2 = '        params={**zf_none, "keep_complex": True},'
new2 = '        params={**zf_real, "keep_complex": True},'
assert old2 in s
s = s.replace(old2, new2, 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("verify uses zero-filled complex finalize")
