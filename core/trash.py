"""删除进系统回收站(0.2.199-补29ex):优先系统回收站,失败回退应用内回收站。

- 系统回收站:send2trash(Linux ~/.local/share/Trash / Windows 回收站 /
  macOS 废纸篓),用户可在系统回收站恢复;
- 回退:移动到调用方指定的 fallback_dir(应用内回收站,保持相对结构、
  自动避重名),同样可恢复;
- 任何情况下都不直接销毁数据;恢复只需把目录移回原路径,项目打开/刷新
  时自动还原(见 ProjectManager.recover_trashed)。
"""

from __future__ import annotations

import shutil
from pathlib import Path


class TrashError(Exception):
    """回收站写入失败(保持原样,不执行删除)。"""


def _unique_target(fallback_dir: Path, rel: Path) -> Path:
    """回退回收站内的唯一目标路径(已存在时加序号后缀)。"""
    target = fallback_dir / rel
    if not target.exists():
        return target
    for i in range(1, 10000):
        candidate = fallback_dir / f"{rel.name}.trashed{i}"
        if not candidate.exists():
            return candidate
    raise TrashError(f"回收站目标冲突: {fallback_dir / rel}")


def send_to_trash(path: Path, fallback_dir: Path, rel: Path | None = None) -> Path:
    """把文件/目录移入系统回收站,返回最终所在位置。

    - 系统回收站成功:返回原路径(实际位置由系统管理);
    - 回退成功:返回回退目录内的目标路径;
    - 路径不存在:原样返回,不做任何事。
    """
    target = Path(path).resolve()
    if not target.exists():
        return target
    rel = rel or Path(target.name)
    try:
        from send2trash import send2trash
    except Exception:  # noqa: BLE001 - 依赖缺失时回退
        send2trash = None
    if send2trash is not None:
        try:
            send2trash(str(target))
            return target
        except Exception:  # noqa: BLE001 - 无桌面回收站/跨盘等
            pass
    fallback_dir.mkdir(parents=True, exist_ok=True)
    destination = _unique_target(fallback_dir, rel)
    shutil.move(str(target), str(destination))
    return destination


__all__ = ["TrashError", "send_to_trash"]
