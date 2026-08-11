"""处理流程控制:自动化与人工两条路径。

- 自动化:``auto_run`` 从 Bruker 目录构建 Experiment,经 backend + AutoProcessor 执行;
- 人工:``manual_param_table`` / ``manual_script_editor`` 为接口占位
  (后续实现:表格改参数 / 模仿 VSCode 的脚本编辑器)。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from core.app_paths import resource_path
from core.data.bruker_reader import read_dataset
from core.project import ExperimentEntry
from workflow.engine import AutoProcessor


def _load_config() -> dict:
    import yaml

    path = resource_path("config/nmrforge.yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class ProcessingController:
    """GUI 层处理控制;人工路径接口占位,实现后续补充。"""

    def __init__(self) -> None:
        self._backend = None

    # ------------------------------------------------------------------
    # 自动化路径
    # ------------------------------------------------------------------
    def auto_run_sync(self, entry: ExperimentEntry) -> dict:
        """同步执行自动化处理(供后台线程调用):返回状态/报告/日志。"""
        from backend.factory import create_backend

        experiment = read_dataset(Path(entry.source))
        backend = self._backend or create_backend(_load_config())
        result = AutoProcessor(backend).run(experiment)
        logs = list(result.logs or [])
        message = ""
        if result.quality is not None:
            message = f"QC: {result.quality.decision.value}"
        elif result.report is not None:
            message = f"谱图: {result.report}"
        return {
            "status": result.status,
            "message": message,
            "logs": logs,
            "experiment_id": entry.id,
        }

    def auto_run_async(
        self,
        entry: ExperimentEntry,
        on_done: Callable[[dict], None],
        on_error: Callable[[str], None],
    ) -> None:
        """后台线程运行自动化处理(避免阻塞 UI)。"""
        import threading

        def worker() -> None:
            try:
                on_done(self.auto_run_sync(entry))
            except Exception as exc:  # noqa: BLE001 - 错误统一回传 UI
                on_error(f"{type(exc).__name__}: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # 人工路径(接口占位,实现之后再写)
    # ------------------------------------------------------------------
    def manual_param_table(self, entry: ExperimentEntry | None = None) -> str:
        """人工路径 A:表格改参数。

        接口占位:后续实现参数表格编辑器(读取处理计划/参数空间,
        逐阶段改参数并生成确定性 .com 脚本)。
        """
        raise NotImplementedError("人工参数表格编辑器待实现")

    def manual_script_editor(self, entry: ExperimentEntry | None = None) -> str:
        """人工路径 B:直接改脚本(模仿 VSCode)。

        接口占位:后续实现带语法高亮的脚本编辑器,
        编辑 fid.com / process.com / nus*.com 并执行。
        """
        raise NotImplementedError("人工脚本编辑器待实现")
