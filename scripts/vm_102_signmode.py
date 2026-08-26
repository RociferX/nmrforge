"""sampleK sign_mode 与模板查询(0.2.199-补20)。"""

from __future__ import annotations

import sys
from pathlib import Path

from core.data.bruker_reader import read_dataset
from workflow.phase_routes import _sign_mode, _template


def main() -> int:
    exp = read_dataset(Path("/home/<lab-user>/Desktop/sampleK"))
    print("sign_mode:", _sign_mode(exp))
    tpl = _template(exp)
    print(
        "template:",
        getattr(tpl, "name", None) if tpl else None,
        "| peak_sign:",
        getattr(tpl, "peak_sign", None) if tpl else None,
    )
    print("exp type:", exp.experiment_type)
    return 0


if __name__ == "__main__":
    sys.exit(main())
