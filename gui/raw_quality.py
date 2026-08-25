"""原始数据质量检查(导入后自动执行,GUI 侧只读使用 core.data)。

检查项:acqus 参数存在性与维度/核/温度、ser/fid 时域文件存在性与大小、
小文件时估算首段/尾部信噪。返回报告 dict,不抛异常。
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np


def _raw_dir(project, exp_id: str, data_id: str) -> Path | None:
    """定位原始数据目录(raw/ 优先,未复制时回退 metadata source_path)。"""
    try:
        raw = project.data_dir(exp_id, data_id, "raw")
        if raw.is_dir():
            return raw
    except Exception:  # noqa: BLE001 - 布局不可用时回退
        pass
    try:
        meta_path = project.data_metadata_path(exp_id, data_id)
        if meta_path is not None and meta_path.is_file():
            import json

            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            source = meta.get("source_path") or ""
            if source and Path(source).is_dir():
                return Path(source)
    except Exception:  # noqa: BLE001 - 读不到 metadata 按无原始目录处理
        pass
    return None


def _param(acqus: Path, key: str) -> str:
    """读取 Bruker 参数文件里单个 ``##$KEY= 值``。"""
    try:
        text = acqus.read_text(encoding="latin-1", errors="replace")
    except OSError:
        return ""
    match = re.search(rf"##\${key}=([^\n]+)", text)
    return match.group(1).strip() if match else ""


def _te_to_kelvin(te: str) -> float | None:
    """Bruker TE → 开尔文;自动识别 0.1 K / K / °C,无法解析返回 None。

    - TE 惯例为 0.1 K(如 2980 → 298.0 K);
    - 部分数据直接存 K(如 298.0)或 °C(如 25),按数值范围判定。
    """
    try:
        number = float(te.split()[0])
    except (TypeError, ValueError, IndexError):
        return None
    kelvin_tenths = number / 10.0
    if 240.0 <= kelvin_tenths <= 340.0:
        return kelvin_tenths
    if 240.0 <= number <= 340.0:
        return number
    if -40.0 <= number <= 100.0:
        return number + 273.15
    return None


def _estimate_snr(raw: Path) -> float | None:
    """用 Bruker 读取器读时域数据,估算首段峰值/尾部噪声标准差。"""
    try:
        from core.data.bruker_reader import read_data, read_dataset

        experiment = read_dataset(raw)
        data = read_data(experiment).matrix
        flat = np.abs(np.asarray(data).reshape(-1))
        if flat.size < 64:
            return None
        head = flat[: max(1, flat.size // 20)]
        tail = flat[-max(1, flat.size // 5) :]
        noise = float(np.std(tail))
        if noise <= 0:
            noise = float(np.mean(tail)) or 1e-12
        return float(np.max(head) / noise)
    except Exception:  # noqa: BLE001 - 读不出数据时跳过信噪估算
        return None


def check_raw_quality(project, exp_id: str, data_id: str) -> dict:
    """检查原始数据质量,返回 ``{"ok", "issues", "info"}``。"""
    info: dict[str, str] = {}
    issues: list[str] = []
    raw = _raw_dir(project, exp_id, data_id)
    if raw is None:
        return {"ok": False, "issues": ["找不到原始数据目录(raw/)"], "info": {}}
    # 0.2.199:分段采集数据 raw/ 是容器根目录(无 acqus/ser),质量检查按
    # 首段目录评估(同一实验各段采集参数一致),不误报缺 acqus/ser
    try:
        data_entry = project.data(exp_id, data_id)
        segments = list(getattr(data_entry, "segments", None) or [])
        if segments:
            seg0 = Path(segments[0])
            if not seg0.is_absolute():
                seg0 = Path(project.root) / seg0
            if seg0.is_dir():
                raw = seg0
                info["分段"] = f"按首段评估({seg0.name})"
    except Exception:  # noqa: BLE001 - 读不到 segments 按普通数据评估
        pass

    acqus = raw / "acqus"
    if not acqus.is_file():
        issues.append("缺少 acqus 参数文件")
    else:
        ndim = 1 + (raw / "acqu2s").is_file() + (raw / "acqu3s").is_file()
        info["维度"] = f"{ndim}D"
        def _nuc(file: Path, key: str) -> str:
            return _param(file, key) if file.is_file() else ""

        nuclei = [
            value
            for value in (
                _nuc(acqus, "NUC1"),
                _nuc(raw / "acqu2s", "NUC2") or _nuc(acqus, "NUC2"),
                _nuc(raw / "acqu3s", "NUC3") or _nuc(acqus, "NUC3"),
            )
            if value
        ]
        if nuclei:
            info["核"] = "-".join(nuclei)
        te = _param(acqus, "TE")
        if te:
            kelvin = _te_to_kelvin(te)
            if kelvin is not None:
                info["温度"] = f"{kelvin:.1f} K"
        td = _param(acqus, "TD")
        if td:
            info["直接维 TD"] = td.split()[0]

    data_file = next(
        (raw / name for name in ("ser", "fid") if (raw / name).is_file()),
        None,
    )
    if data_file is None:
        issues.append("缺少时域数据文件(ser/fid)")
    else:
        size = data_file.stat().st_size
        info["时域文件"] = f"{data_file.name} {size} 字节"
        if size < 1024:
            issues.append("时域数据文件过小(<1KB),可能采集失败")
        if size < 64 * 1024 * 1024:
            snr = _estimate_snr(raw)
            if snr is not None:
                info["信噪(首段/尾部)"] = f"{snr:.1f}"
                if snr < 3.0:
                    issues.append(f"原始信噪比过低({snr:.1f})")

    return {"ok": not issues, "issues": issues, "info": info}


def format_quality_report(report: dict) -> str:
    """把质量报告 dict 转成日志/弹窗可读文本。"""
    if not report:
        return "(未执行)"
    lines: list[str] = []
    info = report.get("info") or {}
    if info:
        lines.append("  " + " | ".join(f"{key}={value}" for key, value in info.items()))
    issues = report.get("issues") or []
    if issues:
        lines.append("  警告: " + "；".join(issues))
    else:
        lines.append("  通过")
    return "\n".join(lines)


__all__ = ["check_raw_quality", "format_quality_report"]
