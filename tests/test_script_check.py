"""脚本运行前检测器测试(0.2.199-补29h)。"""

from __future__ import annotations

from workflow.script_check import check_script


GOOD_SCRIPT = """#!/bin/csh
# NMRForge processing script
xyz2pipe -in d_001.fid -x \\
| nmrPipe -fn SP -off 0.45 -end 0.98 -pow 1 -c 0.5 \\
| nmrPipe -fn ZF -size 4096 \\
| nmrPipe -fn FT \\
| nmrPipe -fn PS -p0 305 -p1 5 -di \\
| nmrPipe -fn EXT -x1 10.5ppm -xn 6.5ppm -sw -round 2 \\
| nmrPipe -fn TP \\
| nmrPipe -fn ZF -size 256 \\
| nmrPipe -fn FT \\
| nmrPipe -fn PS -p0 95 -p1 0 -di \\
| nmrPipe -fn TP \\
| pipe2xyz -out d_001.ft2 -x
"""


def test_good_script_no_warnings() -> None:
    assert check_script(GOOD_SCRIPT) == []


def test_continuation_typo_backslash_h() -> None:
    """行尾续行符误输 `\\h`(用户实测场景)→ 明确提示。"""
    bad = GOOD_SCRIPT.replace(
        "| nmrPipe -fn ZF -size 4096 \\\n",
        "| nmrPipe -fn ZF -size 4096 \\h\n",
    )
    warnings = check_script(bad)
    assert any("续行符" in w and "h" in w for w in warnings), warnings


def test_missing_continuation_before_pipe() -> None:
    """上一行缺 `\\`,下一行以 | 开头 → 管道断裂提示。"""
    bad = GOOD_SCRIPT.replace(
        "| nmrPipe -fn ZF -size 4096 \\\n",
        "| nmrPipe -fn ZF -size 4096\n",
    )
    warnings = check_script(bad)
    assert any("缺续行符" in w for w in warnings), warnings


def test_missing_output_write() -> None:
    bad = GOOD_SCRIPT.replace("| pipe2xyz -out d_001.ft2 -x\n", "")
    warnings = check_script(bad)
    assert any("stdout" in w for w in warnings), warnings


def test_crlf_and_bom() -> None:
    bad = "\ufeff" + GOOD_SCRIPT.replace("\n", "\r\n")
    warnings = check_script(bad)
    assert any("BOM" in w for w in warnings)
    assert any("CRLF" in w for w in warnings)


def test_unknown_function() -> None:
    bad = GOOD_SCRIPT.replace("-fn SP", "-fn SPX")
    warnings = check_script(bad)
    assert any("SPX" in w for w in warnings), warnings


def test_sp_params_out_of_range() -> None:
    bad = GOOD_SCRIPT.replace("-off 0.45 -end 0.98", "-off 0.9 -end 0.1")
    warnings = check_script(bad)
    assert any("SP 窗参数异常" in w for w in warnings), warnings


def test_empty_script() -> None:
    assert check_script("   ") != []
