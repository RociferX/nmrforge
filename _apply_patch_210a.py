"""通用部件文件补丁脚本(0.2.199-补10 切片原位更新):按 ===FILE:/===REPLACE=== 应用替换。"""

from __future__ import annotations

import pathlib
import sys


def main() -> int:
    applied = 0
    parts_path = pathlib.Path("_patch_parts_210a.txt")
    text = parts_path.read_text(encoding="utf-8")
    chunks = text.split("===FILE:")
    if not chunks or chunks[0].strip():
        print("部件文件开头不是 ===FILE:")
        return 1
    for chunk in chunks[1:]:
        path_line, _, rest = chunk.partition("\n")
        target = pathlib.Path(path_line.strip().rstrip("="))
        if not target.is_file():
            print(f"目标不存在: {target}")
            return 1
        if "===REPLACE===" not in rest:
            print(f"缺少 ===REPLACE===: {target}")
            return 1
        _, _, content = rest.partition("===REPLACE===")
        marker = "<<<END>>>"
        if marker not in content:
            print(f"缺少 {marker}: {target}")
            return 1
        old, _, new = content.partition(marker)
        old = old.strip("\n")
        new = new.strip("\n")
        old += "\n"
        new += "\n"
        src = target.read_text(encoding="utf-8")
        count = src.count(old)
        if count != 1:
            print(f"旧文本出现 {count} 次(期望 1): {target} -> {old[:60]!r}")
            return 1
        target.write_text(src.replace(old, new), encoding="utf-8")
        applied += 1
        print(f"已应用: {target}")
    print(f"共应用 {applied} 个替换")
    return 0


if __name__ == "__main__":
    sys.exit(main())
