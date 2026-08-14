"""csh 运行时：source ~/.cshrc 后执行 NMRPipe 命令/脚本（Linux）。"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from collections.abc import Callable
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
        on_line: Callable[[str], None] | None = None,
    ) -> CompletedProcess:
        """执行 csh 命令;on_line 非 None 时逐行转发 stdout(阶段日志实时可见)。"""
        shell = shutil_which_csh()
        if shell is None:
            raise ToolError("本机未找到 tcsh/csh（NMRPipe 脚本需要 C-shell）")
        parts = ["if (-e ~/.cshrc) source ~/.cshrc"]
        if cwd:
            parts.append(f"cd '{cwd}'")
        # 管道符 | 保留为 shell 管道，其余参数转义（受控参数，安全）
        parts.append(" ".join(part if part == "|" else shlex.quote(part) for part in argv))
        command = "; ".join(parts)
        try:
            if on_line is None:
                proc = subprocess.run(
                    [shell, "-c", command],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
                return CompletedProcess(
                    command, proc.stdout, proc.stderr, proc.returncode
                )
            proc = subprocess.Popen(
                [shell, "-c", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            assert proc.stdout is not None
            lines: list[str] = []
            for line in proc.stdout:
                lines.append(line)
                on_line(line.rstrip("\n"))
            stdout = "".join(lines)
            returncode = proc.wait(timeout=timeout)
            return CompletedProcess(command, stdout, "", returncode)
        except subprocess.TimeoutExpired as exc:
            return CompletedProcess(
                command,
                exc.stdout or "",
                f"命令超时（>{timeout:.0f}s）",
                124,
            )
        except OSError as exc:
            raise ToolError(f"csh 执行失败: {exc}") from exc


def shutil_which_csh() -> str | None:
    return shutil.which("tcsh") or shutil.which("csh")
