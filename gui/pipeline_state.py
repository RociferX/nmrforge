"""GUI 侧 Pipeline 步骤指纹状态(OUTDATED 判定)。

为「文件存在」状态推断补充指纹校验(GUI_ARCHITECTURE_VISION §14/§16/§27):
每次步骤成功执行后在数据目录记录输入/脚本指纹;刷新状态时重算当前指纹,
不一致即 OUTDATED(上游重新运行、外部修改、脚本变化)。

状态文件仅 GUI 使用,位于数据目录基座 <exp>/<data>/.pipeline_state.json
(随数据删除一并清理,不属于契约 §9.2 产物目录):
{
  "version": 1,
  "steps": {
    "fid": {"input_hash": ..., "script_hash": ..., "output": ..., "updated_at": ...},
    ...
  }
}
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

STATE_VERSION = 1
STATE_FILENAME = ".pipeline_state.json"

# 大于该尺寸的产物(终谱等)用 (size, mtime_ns) 摘要,避免每次刷新全量哈希大文件
_HASH_LIMIT = 8 * 1024 * 1024


def file_fingerprint(path: Path | str) -> str | None:
    """文件内容指纹:≤8MiB 全量 SHA-256;大文件用 size+mtime_ns 摘要。"""
    target = Path(path)
    try:
        st = target.stat()
    except OSError:
        return None
    if st.st_size <= _HASH_LIMIT:
        digest = hashlib.sha256()
        try:
            with target.open("rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    digest.update(chunk)
        except OSError:
            return None
        return digest.hexdigest()
    digest = hashlib.sha256()
    digest.update(f"stat:{st.st_size}:{st.st_mtime_ns}".encode())
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def pipeline_state_path(manager: Any, exp_id: str, data_id: str) -> Path:
    """状态文件位置:<数据目录基座>/.pipeline_state.json。"""
    return manager.data_base(exp_id, data_id) / STATE_FILENAME


def load_pipeline_state(manager: Any, exp_id: str, data_id: str) -> dict:
    """读取指纹状态(缺失/损坏返回空状态,不抛异常)。"""
    path = pipeline_state_path(manager, exp_id, data_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "steps": {}}
    steps = raw.get("steps") if isinstance(raw, dict) else None
    return {
        "version": STATE_VERSION,
        "steps": steps if isinstance(steps, dict) else {},
    }


def save_pipeline_state(manager: Any, exp_id: str, data_id: str, state: dict) -> None:
    """原子写指纹状态。"""
    path = pipeline_state_path(manager, exp_id, data_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.stem + "-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _raw_dir(manager: Any, exp_id: str, data_id: str) -> Path | None:
    """解析原始数据目录(优先项目内 raw 副本,回退 source)。"""
    try:
        entry = manager.data(exp_id, data_id)
    except Exception:  # noqa: BLE001 - 目录解析失败按无原始目录处理
        return None
    raw = Path(entry.raw_dir) if getattr(entry, "raw_dir", "") else Path(entry.source)
    if not raw.is_absolute():
        raw = manager.root / raw
    return raw if raw.is_dir() else None


def raw_fingerprint(manager: Any, exp_id: str, data_id: str) -> str | None:
    """原始数据指纹:metadata.json 内容 + raw 文件 (路径, size, mtime) 清单。

    fid.com 属处理脚本(fid 步骤单独以 script_hash 校验),不计入输入指纹,
    避免 bruker 重新生成脚本头导致误判。
    """
    digest = hashlib.sha256()
    meta = manager.data_metadata_path(exp_id, data_id)
    try:
        digest.update(b"metadata:")
        digest.update(meta.read_bytes())
    except OSError:
        pass
    raw = _raw_dir(manager, exp_id, data_id)
    files: list[Path] = []
    if raw is not None:
        try:
            files = sorted(p for p in raw.rglob("*") if p.is_file())
        except OSError:
            files = []
    digest.update(f"|files={len(files)}".encode())
    for path in files:
        if path.name == "fid.com":
            continue
        try:
            st = path.stat()
            rel = path.relative_to(raw).as_posix()
            digest.update(f"|{rel}:{st.st_size}:{st.st_mtime_ns}".encode())
        except (OSError, ValueError):
            continue
    return digest.hexdigest()


def _fid_file(manager: Any, exp_id: str, data_id: str) -> Path | None:
    try:
        entry = manager.data(exp_id, data_id)
    except Exception:  # noqa: BLE001
        return None
    candidate = getattr(entry, "fid_path", "") or ""
    if candidate:
        path = Path(candidate)
        if not path.is_absolute():
            path = manager.root / path
        if path.is_file():
            return path
    proc = manager.data_dir(exp_id, data_id, "process")
    try:
        fids = sorted(proc.glob("*.fid"))
    except OSError:
        fids = []
    return fids[0] if fids else None


def _spectrum_file(manager: Any, exp_id: str, data_id: str) -> Path | None:
    try:
        entry = manager.data(exp_id, data_id)
    except Exception:  # noqa: BLE001
        return None
    candidate = getattr(entry, "spectrum_path", "") or ""
    if candidate:
        path = Path(candidate)
        if not path.is_absolute():
            path = manager.root / path
        if path.is_file():
            return path
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    for ext in ("ft2", "ft3"):
        path = spectra / f"{exp_id}-{data_id}.{ext}"
        if path.is_file():
            return path
    flat = manager.dir_path("spectra")
    for ext in ("ft2", "ft3"):
        path = flat / f"{exp_id}.{ext}"
        if path.is_file():
            return path
    return None


def _peaks_file(manager: Any, exp_id: str, data_id: str) -> Path | None:
    path = manager.data_dir(exp_id, data_id, "peaks") / f"{exp_id}-{data_id}.csv"
    if path.is_file():
        return path
    flat = manager.dir_path("peaks") / f"{exp_id}.csv"
    return flat if flat.is_file() else None


def input_fingerprint(
    manager: Any, exp_id: str, data_id: str, step_id: str
) -> str | None:
    """当前步骤输入指纹(状态校验用)。"""
    if step_id in ("import", "fid"):
        return raw_fingerprint(manager, exp_id, data_id)
    if step_id == "spectrum":
        fid = _fid_file(manager, exp_id, data_id)
        return file_fingerprint(fid) if fid is not None else None
    if step_id == "peaks":
        spectrum = _spectrum_file(manager, exp_id, data_id)
        return file_fingerprint(spectrum) if spectrum is not None else None
    if step_id == "analysis":
        peaks = _peaks_file(manager, exp_id, data_id)
        return file_fingerprint(peaks) if peaks is not None else None
    return None


def script_fingerprint(
    manager: Any, exp_id: str, data_id: str, step_id: str
) -> str | None:
    """步骤脚本指纹(fid.com 在 raw/,谱图脚本在 process/)。"""
    if step_id == "fid":
        raw = _raw_dir(manager, exp_id, data_id)
        if raw is None:
            return None
        fid_com = raw / "fid.com"
        return file_fingerprint(fid_com) if fid_com.is_file() else None
    if step_id == "spectrum":
        proc = manager.data_dir(exp_id, data_id, "process")
        try:
            coms = sorted(p for p in proc.glob("*.com") if p.name != "fid.com")
        except OSError:
            coms = []
        if not coms:
            return None
        digest = hashlib.sha256()
        for path in coms:
            digest.update(path.name.encode())
            digest.update((file_fingerprint(path) or "").encode())
        return digest.hexdigest()
    return None


def record_step_success(
    manager: Any,
    exp_id: str,
    data_id: str,
    step_id: str,
    params: dict[str, Any] | None = None,
) -> dict:
    """登记一次成功步骤的指纹(输入/脚本/参数哈希),返回合并后的状态。"""
    state = load_pipeline_state(manager, exp_id, data_id)
    steps = state.setdefault("steps", {})
    output = ""
    if step_id == "fid":
        fid = _fid_file(manager, exp_id, data_id)
        output = str(fid) if fid is not None else ""
    elif step_id == "spectrum":
        spectrum = _spectrum_file(manager, exp_id, data_id)
        output = str(spectrum) if spectrum is not None else ""
    elif step_id == "peaks":
        peaks = _peaks_file(manager, exp_id, data_id)
        output = str(peaks) if peaks is not None else ""
    entry = {
        "input_hash": input_fingerprint(manager, exp_id, data_id, step_id),
        "script_hash": script_fingerprint(manager, exp_id, data_id, step_id),
        "output": output,
    }
    if params:
        entry["params_hash"] = _sha256_text(json.dumps(params, sort_keys=True))
    steps[step_id] = entry
    save_pipeline_state(manager, exp_id, data_id, state)
    return state


__all__ = [
    "STATE_FILENAME",
    "file_fingerprint",
    "input_fingerprint",
    "load_pipeline_state",
    "pipeline_state_path",
    "raw_fingerprint",
    "record_step_success",
    "save_pipeline_state",
    "script_fingerprint",
]
