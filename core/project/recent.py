"""Recent-project store: the core layer must not depend on Qt; JSON on disk
(8 entries at most, most-recent first, de-duplicated)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

MAX_RECENT_ENTRIES = 8


def default_config_dir() -> Path:
    """Platform-specific application config directory
    (Windows: LOCALAPPDATA/NMRForge, Linux: ~/.config/NMRForge)."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "NMRForge"
    return Path.home() / ".config" / "NMRForge"


class JsonRecentProjectsStore:
    """JSON-file recent-project store (atomic write, most-recent first, 8 by default)."""

    def __init__(
        self, path: Path | None = None, max_entries: int = MAX_RECENT_ENTRIES
    ) -> None:
        self.path = path or (default_config_dir() / "recent_projects.json")
        self.max_entries = max_entries

    def _read(self) -> list[str]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        entries = data.get("projects", []) if isinstance(data, dict) else data
        if not isinstance(entries, list):
            return []
        return [str(e) for e in entries if isinstance(e, str) and e]

    def list(self) -> list[str]:
        return self._read()

    def push(self, path: str) -> None:
        entries = self._read()
        entries = [p for p in entries if p != path]
        entries.insert(0, path)
        entries = entries[: self.max_entries]
        self._write(entries)

    def remove(self, path: str) -> None:
        entries = [p for p in self._read() if p != path]
        self._write(entries)

    def _write(self, entries: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="recent-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"projects": entries}, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
