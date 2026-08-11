"""NMRPipe 查找逻辑测试。"""

from __future__ import annotations

from pathlib import Path

from backend.nmrpipe_finder import find_nmrpipe_bin, find_tool


def test_find_nmrpipe_bin_explicit(tmp_path: Path) -> None:
    bin_dir = tmp_path / "nmrbin.test"
    bin_dir.mkdir()
    (bin_dir / "nmrPipe").write_text("#!/bin/csh\n", encoding="utf-8")
    found = find_nmrpipe_bin(explicit=str(bin_dir))
    assert found is not None
    assert (found / "nmrPipe").is_file()


def test_find_nmrpipe_bin_explicit_missing(tmp_path: Path) -> None:
    assert find_nmrpipe_bin(explicit=str(tmp_path)) is None


def test_find_tool_via_com_dir(tmp_path: Path) -> None:
    bin_dir = tmp_path / "nmrbin.test"
    com_dir = tmp_path / "com"
    bin_dir.mkdir()
    com_dir.mkdir()
    (bin_dir / "nmrPipe").write_text("#!/bin/csh\n", encoding="utf-8")
    (com_dir / "bruker").write_text("#!/bin/csh\n", encoding="utf-8")
    found = find_tool("bruker", nmrpipe_bin=bin_dir)
    assert found is not None
    assert found.name == "bruker"
