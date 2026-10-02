"""Make frozen Python build metadata independent of the build machine."""
from __future__ import annotations

import pprint
import runpy
import sys
from pathlib import Path


def render_config(values: dict, python_prefix: str, user_home: str) -> str:
    """Keep ABI values while resolving installation paths at runtime."""
    replacements = sorted(
        ((python_prefix, "@@PYTHON_PREFIX@@"), (user_home, "@@USER_HOME@@")),
        key=lambda pair: len(pair[0]), reverse=True,
    )
    cleaned = {}
    for key, value in values.items():
        if isinstance(value, str):
            for old, marker in replacements:
                if old and old not in {"/", "."}:
                    value = value.replace(old, marker)
        cleaned[key] = value
    return (
        "import os as _os\nimport sys as _sys\n"
        "build_time_vars = " + pprint.pformat(cleaned, sort_dicts=True) + "\n"
        "for _key, _value in list(build_time_vars.items()):\n"
        "    if isinstance(_value, str):\n"
        "        build_time_vars[_key] = _value.replace('@@PYTHON_PREFIX@@', "
        "_sys.base_prefix).replace('@@USER_HOME@@', _os.path.expanduser('~'))\n"
    )


def sanitize_python_config(pure: list, code_cache: dict, output: Path) -> int:
    """Replace collected stdlib configuration modules, including cached code."""
    count = 0
    for index, (name, source_path, kind) in enumerate(pure):
        if not name.startswith("_sysconfigdata_"):
            continue
        values = runpy.run_path(source_path)["build_time_vars"]
        text = render_config(values, sys.base_prefix, str(Path.home()))
        output.mkdir(parents=True, exist_ok=True)
        destination = output / (name + ".py")
        destination.write_text(text, encoding="utf-8")
        pure[index] = (name, str(destination), kind)
        code_cache[name] = compile(text, name + ".py", "exec")
        count += 1
    return count
