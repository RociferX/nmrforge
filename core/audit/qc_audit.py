"""Structured, machine-readable audit record for automatic data changes.

Why this exists (Phase 10 of the public-release task): the processing path may repair a bad point,
zero a corrupted sample, shrink a sampling grid or delete a source block. Every one of those is a
change to raw or intermediate data, and a log line is not enough evidence - the task requires the
fields

    issue_detected / location / detection_rule / action_taken / before_state / after_state /
    timestamp / software_version

so that a reader can see *what* was changed, *where*, *under which rule*, and *what the value was
before and after*. The rule this module implements is:

    detect -> flag -> log -> optional correction      (never a silent correction)

Design notes:

- One JSONL file per processing work directory (``qc_audit.jsonl``), appended to and flushed per
  record, so a crashed or killed run still leaves the records of what it had already changed.
- Records are frozen dataclasses; ``before_state``/``after_state`` hold the concrete values, not
  prose, and ``extra`` carries anything a specific site needs (row/column, axis, dataset id, ...).
- The timestamp and ``software_version`` (plus the git commit when it is discoverable) are stamped
  by the log at write time, so a call site cannot forget the provenance.
- Reading back is part of the API (``read_audit``): tests and the QC report both use it, and a
  reviewer can verify that "no record" means "no change".
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "AUDIT_FILENAME",
    "SCHEMA_VERSION",
    "QcAction",
    "QcAuditLog",
    "audit_path",
    "read_audit",
]

#: File name inside a processing work directory.
AUDIT_FILENAME = "qc_audit.jsonl"

#: Bumped when the record layout changes in a way a reader must know about.
SCHEMA_VERSION = 1

#: Fields Phase 10 requires in every record. Kept as a module constant so tests can assert coverage
#: without importing the dataclass field names by hand.
REQUIRED_FIELDS: tuple[str, ...] = (
    "issue_detected",
    "location",
    "detection_rule",
    "action_taken",
    "before_state",
    "after_state",
    "timestamp",
    "software_version",
)


def _utc_now() -> str:
    """Timestamp in the same shape the run records use."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _software_version() -> str:
    from core.version import software_version

    return software_version()


def _git_fields() -> dict[str, str]:
    """Commit provenance when it is available; empty when frozen or outside a repository."""
    from core.version import git_commit, git_commit_dirty

    commit = git_commit()
    if not commit:
        return {}
    return {"git_commit": commit, "git_commit_dirty": "1" if git_commit_dirty() else "0"}


@dataclass(frozen=True)
class QcAction:
    """One automatic change to raw or intermediate data.

    ``before_state`` / ``after_state`` should contain the values that changed (for example
    ``{"point": 123.5}`` -> ``{"point": 0.0}``); ``extra`` is for context that does not belong in
    the "before/after" pair, such as the row and column of a repaired sample.
    """

    issue_detected: str
    location: str
    detection_rule: str
    action_taken: str
    before_state: dict[str, Any] = field(default_factory=dict)
    after_state: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    software_version: str = ""
    schema_version: int = SCHEMA_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": int(self.schema_version),
            "issue_detected": str(self.issue_detected),
            "location": str(self.location),
            "detection_rule": str(self.detection_rule),
            "action_taken": str(self.action_taken),
            "before_state": dict(self.before_state),
            "after_state": dict(self.after_state),
            "timestamp": str(self.timestamp),
            "software_version": str(self.software_version),
        }
        if self.extra:
            data["extra"] = dict(self.extra)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QcAction:
        return cls(
            issue_detected=str(data.get("issue_detected", "")),
            location=str(data.get("location", "")),
            detection_rule=str(data.get("detection_rule", "")),
            action_taken=str(data.get("action_taken", "")),
            before_state=dict(data.get("before_state") or {}),
            after_state=dict(data.get("after_state") or {}),
            timestamp=str(data.get("timestamp", "")),
            software_version=str(data.get("software_version", "")),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION) or SCHEMA_VERSION),
            extra=dict(data.get("extra") or {}),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=False)


def audit_path(work_dir: Path | str) -> Path:
    """Audit file for one work directory."""
    return Path(work_dir) / AUDIT_FILENAME


def read_audit(work_dir: Path | str) -> list[QcAction]:
    """Read the records for one work directory (empty list when nothing was recorded).

    A malformed line is skipped rather than raising: an audit file that cannot be fully parsed must
    not make a finished run unreadable, and the remaining records are still evidence.

    Parameters
    ----------
    work_dir : Path | str
        处理工作目录(一级条目就是它下面的 ``qc_audit.jsonl``)。

    Returns
    -------
    list[QcAction]
        追加顺序的记录列表;文件不存在时返回空列表(等价于「没有改动」)。

    Raises
    ------
    - 不抛异常:损坏行被跳过,不影响其余记录读取。

    Side effects
    ------------
    只读文件:不创建、不修改 ``qc_audit.jsonl``。

    Examples
    --------
        actions = read_audit(work_dir)          # [] 表示没有任何自动改动
        for action in actions:
            print(action.issue_detected, action.action_taken)
    """
    path = audit_path(work_dir)
    if not path.is_file():
        return []
    actions: list[QcAction] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            actions.append(QcAction.from_dict(payload))
    return actions


class QcAuditLog:
    """Append-only writer for :data:`AUDIT_FILENAME` inside one work directory.

    Use :meth:`record` for each change. ``enabled=False`` produces a log that accepts records and
    discards them (used where a caller has no work directory yet); it never raises, so a call site
    does not need a branch.
    """

    def __init__(self, work_dir: Path | str, *, enabled: bool = True) -> None:
        self._path = audit_path(work_dir)
        self._enabled = bool(enabled)
        self._lock = threading.Lock()
        self._count = 0

    @property
    def path(self) -> Path:
        return self._path

    @property
    def enabled(self) -> bool:
        return self._enabled

    def record(self, action: QcAction) -> QcAction:
        """Stamp and append one action, returning what was written."""
        stamped = QcAction(
            issue_detected=action.issue_detected,
            location=action.location,
            detection_rule=action.detection_rule,
            action_taken=action.action_taken,
            before_state=dict(action.before_state),
            after_state=dict(action.after_state),
            timestamp=action.timestamp or _utc_now(),
            software_version=action.software_version or _software_version(),
            schema_version=action.schema_version,
            extra={**_git_fields(), **dict(action.extra)},
        )
        if not self._enabled:
            return stamped
        line = stamped.to_json()
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
            self._count += 1
        return stamped

    def read(self) -> list[QcAction]:
        return read_audit(self._path.parent)

    @property
    def count(self) -> int:
        """Records written through this instance (not the file's total)."""
        return self._count

    def __len__(self) -> int:
        return self._count

    def summary(self) -> str:
        """One-line summary for the run log; empty when nothing was recorded."""
        if not self._enabled or not self._count:
            return ""
        actions = Counter(action.action_taken for action in self.read())
        detail = "、".join(f"{name} {n}" for name, n in sorted(actions.items()))
        return f"QC 审计记录:{self._count} 条({detail}) → {self._path}"

    def __iter__(self) -> Iterator[QcAction]:
        return iter(self.read())
