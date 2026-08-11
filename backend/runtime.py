"""csh 运行时：source ~/.cshrc 后执行 NMRPipe 命令/脚本（Linux）。"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass


class ToolError(RuntimeError):
    """外部工具执行错误。"""


@dataclass
class CompletedProcess:
    command: str
    stdout: str
    stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CshRuntime:
    """通过 csh（source ~/.cshrc）执行 NMRPipe 工具。"""

    def run(
        self,
        argv: list[str],
        *,
        cwd: str | None = None,
        timeout: float = 3600,
    ) -> CompletedProcess:
        shell = shutil_which_csh()
        if shell is None:
            raise ToolError("本机未找到 tcsh/csh（NMRPipe 脚本需要 C-shell）")
        parts = ["if (-e ~/.cshrc) source ~/.cshrc"]
        if cwd:
            parts.append(f"cd '{cwd}'")
        parts.append(" ".join(shlex.quote(part) for part in argv))
        command = "; ".join(parts)
        try:
            proc = subprocess.run(
                [shell, "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return CompletedProcess(
                command,
                exc.stdout or "",
                f"命令超时（>{timeout:.0f}s）",
                124,
            )
        except OSError as exc:
            raise ToolError(f"csh 执行失败: {exc}") from exc
        return CompletedProcess(command, proc.stdout, proc.stderr, proc.returncode)


def shutil_which_csh() -> str | None:
    return shutil.which("tcsh") or shutil.which("csh")
