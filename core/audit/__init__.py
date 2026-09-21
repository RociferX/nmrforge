"""Audit records: structured evidence of what the software changed and why.

Phase 10 of the public-release task requires every automatic modification of raw or intermediate
data to be recorded with ``issue_detected`` / ``location`` / ``detection_rule`` / ``action_taken`` /
``before_state`` / ``after_state`` / ``timestamp`` / ``software_version``, under the rule
``detect -> flag -> log -> optional correction``, and forbids silent modification.

:mod:`core.audit.qc_audit` holds the record type and the append-only JSONL log; the modules that
actually modify data (``workflow/direct_diagnostics.py``, ``backend/nmrpipe_backend.py``) write into
it.
"""

from __future__ import annotations

__all__: list[str] = []
