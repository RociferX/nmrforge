"""人工脚本运行前检测器(0.2.199-补29h):检测常见错误并提示。

动机:用户人工修改脚本后运行报 UnicodeDecodeError——行尾续行符 `\\` 后
误输字符(如 `\\h`),csh 管道断裂,二进制谱数据漏到 stdout,被当文本
解码。检测器在运行前扫描常见问题,给出明确提示而非运行时诡异报错。
"""

from __future__ import annotations

import re

# nmrPipe 常用函数(-fn 取值);未知函数会被 nmrPipe 报错或静默,提前提示
KNOWN_FUNCTIONS = frozenset(
    {
        "SP", "ZF", "FT", "PS", "EXT", "TP", "ZTP", "POLY", "MC", "REV",
        "EM", "GM", "HT", "SINE", "GAUSS", "LP", "NUS", "SMILE", "MAC",
        "ADD", "SUB", "MUL", "DIV", "SQRT", "EXP", "LOG", "ABS", "STAT",
        "WRITE", "READ", "CUBE", "PROJ", "SUM", "ALTP", "COMPLEX", "REAL",
        "FILTER", "DIM", "COPY", "NUSExpand",
    }
)


def check_script(content: str, script_name: str = "") -> list[str]:
    """扫描脚本,返回警告列表(空 = 未发现问题)。

    纯函数、无副作用;按行检查续行符/CRLF/BOM/输出写入/未知函数/
    常用参数合理性。警告不阻断,由调用方决定提示与是否继续。
    """
    warnings: list[str] = []
    if not content.strip():
        return ["脚本内容为空,无法运行"]
    if "\ufeff" in content:
        warnings.append(
            "脚本开头有 BOM 字符,`#!/bin/csh` 会被当成命令的一部分,"
            "建议去掉 BOM 后重新保存"
        )
    if "\r" in content:
        warnings.append(
            "脚本包含 CRLF(Windows 换行):行尾续行符 `\\` 后会跟着 `\\r`,"
            "续行会失效,建议改为 LF(Unix)换行"
        )

    lines = content.splitlines()
    _check_continuations(lines, warnings)
    _check_output_write(content, warnings)
    _check_functions(lines, warnings)
    _check_sp_params(lines, warnings)
    return warnings


def _check_continuations(lines: list[str], warnings: list[str]) -> None:
    """续行符检查:管道行须以 `\\` 结尾;`\\` 后不得有多余字符。"""
    non_empty = [i for i, line in enumerate(lines) if line.strip()]
    for pos, idx in enumerate(non_empty):
        line = lines[idx].rstrip("\r")
        stripped = line.rstrip()
        n = pos + 1
        if not stripped:
            continue
        is_pipeline = "|" in stripped
        # 续行符后有多余字符(如 `\h`):只看管道行尾部 token
        if is_pipeline and stripped:
            token = stripped.split()[-1]
            bs = token.rfind("\\")
            if 0 <= bs < len(token) - 1:
                tail = token[bs + 1:]
                warnings.append(
                    f"第 {idx + 1} 行:续行符 `\\` 后有多余字符 "
                    f"{tail!r}(应为行尾 `\\` 直接换行),管道会断裂"
                )
        next_is_pipe = pos + 1 < len(non_empty) and lines[
            non_empty[pos + 1]
        ].lstrip().startswith("|")
        if next_is_pipe and not stripped.endswith("\\"):
            last = stripped[-1]
            warnings.append(
                f"第 {idx + 1} 行:下一行以 `|` 开头但本行行尾是 "
                f"{last!r} 而非 `\\`(缺续行符),管道会在此断开"
            )
        if not next_is_pipe and stripped.endswith("\\"):
            # 管道最后一行不应以 `\\` 结尾(悬空续行)
            if any(c in stripped for c in ("|", "xyz2pipe", "pipe2xyz")):
                warnings.append(
                    f"第 {idx + 1} 行:管道末尾以 `\\` 结尾(悬空续行),"
                    "csh 会等待后续输入"
                )


def _check_output_write(content: str, warnings: list[str]) -> None:
    """输出写入检查:管道脚本缺 -out/pipe2xyz -out 时数据漏到 stdout。

    仅当脚本看起来是处理管道(xyz2pipe/pipe2xyz/| nmrPipe)才检查,
    避免对纯文本/非管道内容误报。
    """
    looks_pipeline = any(
        token in content for token in ("xyz2pipe", "pipe2xyz", "| nmrPipe")
    )
    if not looks_pipeline:
        return
    has_out = bool(re.search(r"pipe2xyz\s+-out", content)) or bool(
        re.search(r"nmrPipe\s+.*-out\s+\S+", content)
    )
    if not has_out:
        warnings.append(
            "未找到输出写入(-out / pipe2xyz -out):谱数据会写到 stdout,"
            "产生二进制乱码并报解码错误"
        )


def _check_functions(lines: list[str], warnings: list[str]) -> None:
    """未知 -fn 函数名检查。"""
    for i, line in enumerate(lines):
        for m in re.finditer(r"-fn\s+(\S+)", line):
            fn = m.group(1)
            if fn.upper() not in KNOWN_FUNCTIONS:
                warnings.append(
                    f"第 {i + 1} 行:未知 nmrPipe 函数 {fn!r}"
                    "(-fn 拼写检查)"
                )


def _check_sp_params(lines: list[str], warnings: list[str]) -> None:
    """SP 窗参数合理性(off<end, 均在 0..1)。"""
    for i, line in enumerate(lines):
        if "-FN SP" not in line.upper():
            continue
        off = re.search(r"-off\s+([0-9.]+)", line)
        end = re.search(r"-end\s+([0-9.]+)", line)
        if off and end:
            try:
                o, e = float(off.group(1)), float(end.group(1))
            except ValueError:
                continue
            if not (0.0 <= o < e <= 1.0):
                warnings.append(
                    f"第 {i + 1} 行:SP 窗参数异常 off={o} end={e}"
                    "(应 0≤off<end≤1)"
                )


__all__ = ["check_script"]
