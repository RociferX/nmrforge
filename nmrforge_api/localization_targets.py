"""Targeted localization: the list of peaks to refine.

By default a combination run's (sweep) ``localization`` acts on **every detected peak**
of that spectrum; in a batch ensemble the cost of Gaussian fitting concentrates on the
threshold-limited noise maxima and companion features, while the peaks that actually
answer the scientific question are often only a dozen percent of the table. This module
is the first-class entry point for "only the named peaks take the chosen method's refinement".
Semantics (2026-09-19 / 2026-09-20 requests; once in the contract, downstream needs no glue):

1. **Detection is untouched**: the target list only decides which peaks take the method's
      refinement; picking, row count and ``peak_id`` numbering are unchanged;
2. Unlisted peaks **stay in the table**, positioned by the detection-stage three-point
      parabola estimate (the method that row actually used); Gaussian-only QC columns are
   ``NaN`` (not fitted, so not a failure);
   3. A per-peak failure is **recorded as usual** (``fallback=true`` + ``fallback_reason``)
   and **never** re-fits another candidate;
4. The record follows ``direct_range.source``'s style: path + SHA-256 + peak count;
   5. An empty list / a missing file / a missing ``peak_id`` column / an unknown ``peak_id``
   **raise** instead of silently degrading to the whole spectrum, and 6. no targets = the
   whole-spectrum behaviour of today, fully compatible.
**Condition granularity** (2026-09-20 request): A and B are two spectra with different
detected peak sets, so one combination row has different target **peak numbers** per
condition and one list cannot serve both. The target list may therefore be written per

- the target CSV may carry a ``condition`` column: when it does, each condition reads
    only the rows where ``condition`` matches its own name; without it, behaviour is
      **bit-for-bit** identical (one list for the batch); the record writes
- a condition with **no rows at all** in the file -> **fails before processing** (the
    default ``on_missing="error"``); to let it through, declare ``on_missing="all"`` (that
    condition is unlimited = whole-spectrum refinement) or ``on_missing="none"`` (that
  condition refines nothing), and the strategy goes into the record;
- a condition name that **does not belong to the study** -> error (never silently ignored);
  - mapping form (one file per condition): ``{"A": "a.csv", "B": "b.csv"}``, or
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nmrforge_api.errors import SweepError
from ui_support.i18n import tr

#: record schema (``run.json.parameters_resolved.detection.localization_targets``)
LOCALIZATION_TARGETS_SCHEMA = "nmrforge_api.localization_targets.v1"

#: required column name in a target-list CSV (case/whitespace insensitive)
PEAK_ID_COLUMN = "peak_id"
#: optional column: the link to the reference peak table, for the record only
REFERENCE_ID_COLUMN = "reference_peak_id"
#: optional column: condition name (with it, each condition reads only its own rows)
CONDITION_COLUMN = "condition"
#: spellings that may be dropped as a header in a single-column file  # i18n: keep
_HEADER_ALIASES = frozenset({"peak_id", "peak", "id", "峰", "峰序号"})

#: policy when a condition has no rows in the target list (default: error, never a silent all)
ON_MISSING_POLICIES: tuple[str, ...] = ("error", "all", "none")

#: reserved keys in a target-spec mapping; any other key reads as condition -> its spec
RESERVED_SPEC_KEYS = frozenset(
    {"path", "peak_ids", "peak_id", "ids", "default", "by_condition", "on_missing"}
)


def _parse_peak_id(value: Any, *, where: str) -> int:
    """A cell -> a peak number of this spectrum (positive integer); bad values raise."""
    if value is None or isinstance(value, bool):
        raise SweepError(tr("{p0}: peak_id is not a positive integer: {p1!r}", p0=where, p1=value))
    token = str(value).strip()
    if not token:
        raise SweepError(tr("{p0}: peak_id is empty (delete the blank row)", p0=where))
    try:
        number = float(token)
    except ValueError:
        raise SweepError(
            tr(
                "{p0}: peak_id is not a positive integer: {p1!r}(this spectrum's peak numbers, "
                "e.g. 12; a reference id such as R0012 is not a "
                "peak_id)",
                p0=where,
                p1=value,
            )
        ) from None
    if not number.is_integer() or number < 1:
        raise SweepError(tr("{p0}: peak_id is not a positive integer: {p1!r}", p0=where, p1=value))
    return int(number)


def _check_on_missing(value: Any) -> str:
    """Validate the ``on_missing`` policy (``error`` / ``all`` / ``none``)."""
    policy = str(value if value is not None else "error").strip().lower()
    if policy not in ON_MISSING_POLICIES:
        raise SweepError(
            tr(
                "unknown on_missing policy: {p0!r}; allowed "
                "{p1}",
                p0=value,
                p1=', '.join(ON_MISSING_POLICIES),
            )
        )
    return policy


def _line_ranges(lines: Iterable[int]) -> tuple[tuple[int, int], ...]:
    """Line numbers -> contiguous ranges (closed; adjacent lines merged) for the record."""
    out: list[list[int]] = []
    for value in sorted({int(item) for item in lines}):
        if out and value == out[-1][1] + 1:
            out[-1][1] = value
            continue
        out.append([value, value])
    return tuple((int(start), int(end)) for start, end in out)


@dataclass(frozen=True)
class LocalizationTargets:
    """The target peak list: only ``peak_ids`` take the chosen method's refinement.

    Condition granularity (2026-09-20): ``condition`` says which condition this list belongs
    to (empty = shared by the batch); ``line_ranges`` records the source CSV's line ranges.
    """

    peak_ids: tuple[int, ...]
    #: path of the source CSV (empty when the caller passes peak numbers directly)
    path: str = ""
    #: SHA-256 of the source CSV (a swapped target list has to stay traceable)
    sha256: str = ""
    #: ``peak_id -> reference_peak_id`` (optional CSV column, for the record only)
    reference_peak_ids: Mapping[int, str] = field(default_factory=dict)
    #: where it came from: ``csv`` / ``combo`` / ``argument``
    source: str = "argument"
    #: which condition this list belongs to (empty = shared by the batch, i.e. no column)
    condition: str = ""
    #: source line ranges (closed; per condition, that condition's own lines)
    line_ranges: tuple[tuple[int, int], ...] = ()

    @property
    def n_targets(self) -> int:
        return len(self.peak_ids)

    @property
    def scope(self) -> str:
        """Scope: ``subset`` (targets given) / ``none`` (explicitly refining nothing).

        The "unlimited" case is not this class: the caller passes ``None`` for it (recorded as
        ``scope=all``).
        """
        return "none" if not self.peak_ids else "subset"

    def to_dict(self) -> dict[str, Any]:
        """The normalised record for the manifest (path + SHA-256 + peak count + id list)."""
        payload: dict[str, Any] = {
            "schema": LOCALIZATION_TARGETS_SCHEMA,
            "scope": self.scope,
            "source": str(self.source),
            "path": str(self.path),
            "sha256": str(self.sha256),
            "n_targets": self.n_targets,
            "peak_ids": [int(value) for value in self.peak_ids],
        }
        if self.reference_peak_ids:
            payload["reference_peak_ids"] = {
                str(key): str(self.reference_peak_ids[key])
                for key in sorted(self.reference_peak_ids)
            }
        if self.condition:
            payload["condition"] = str(self.condition)
        if self.line_ranges:
            payload["line_ranges"] = [
                [int(start), int(end)] for start, end in self.line_ranges
            ]
        return payload

    def describe(self) -> str:
        """One-line description (logs/records): ``targets.csv(sha256=..., 75 peaks)``."""
        where = self.path or tr("(peak_id given by the caller)")
        digest = f", sha256={self.sha256[:12]}…" if self.sha256 else ""
        condition = tr(", condition {p0}", p0=self.condition) if self.condition else ""
        return tr("{p0}{p1}{p2}, {p3} peaks", p0=where, p1=digest, p2=condition, p3=self.n_targets)


@dataclass(frozen=True)
class _TargetFile:
    """A parsed target-list file (path + whole-file SHA-256 + column names + rows)."""

    path: Path
    sha256: str
    columns: tuple[str, ...]
    rows: tuple[tuple[int, dict[str, str]], ...]

    @property
    def has_condition(self) -> bool:
        return CONDITION_COLUMN in self.columns


def _split_columns(line: str) -> list[str]:
    """Header row -> column names (comma / tab / semicolon / single column all work)."""
    for delimiter in (",", "\t", ";"):
        if delimiter in line:
            return [token.strip() for token in line.split(delimiter)]
    return [line.strip()]


def _read_target_rows(
    target: Path, text: str
) -> tuple[tuple[str, ...], list[tuple[int, dict[str, str]]]]:
    """Target-list text -> ``(column names, [(line number, {normalised column: value})])``.

    Blank lines and ``#`` comments are skipped; a single-column file (one peak number per
    """
    lines = [
        (index, line)
        for index, line in enumerate(text.splitlines(), start=1)
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        return (), []
    header = _split_columns(lines[0][1])
    normalized = tuple(token.strip().lower() for token in header)
    if PEAK_ID_COLUMN not in normalized:
        if len(header) > 1:
            raise SweepError(
                tr(
                    "target peak list has no {p0} column: {p1}(header is {p2}; one column of this "
                    "spectrum's peak numbers is "
                    "required)",
                    p0=PEAK_ID_COLUMN,
                    p1=target,
                    p2=header,
                )
            )
        # single-column file: one peak_id per line; drop it when it spells an alias header
        first_is_header = lines[0][1].strip().lower() in _HEADER_ALIASES
        body = lines[1:] if first_is_header else lines
        return (PEAK_ID_COLUMN,), [
            (index, {PEAK_ID_COLUMN: line.strip()}) for index, line in body
        ]
    body = lines[1:]
    rows: list[tuple[int, dict[str, str]]] = []
    for index, line in body:
        values = _split_columns(line)
        if len(header) == 1:
            values = values[:1]
        item: dict[str, str] = {}
        for position, name in enumerate(normalized):
            item[name] = values[position] if position < len(values) else ""
        rows.append((index, item))
    return normalized, rows


def _load_target_file(path: Path | str) -> _TargetFile:
    """Read a target peak list file -> :class:`_TargetFile` (missing/empty/no column raise)."""
    target = Path(path)
    if not target.is_file():
        raise SweepError(
            tr(
                "target peak list does not exist: {p0}(targeted localization needs a CSV with at "
                "least a peak_id "
                "column)",
                p0=target,
            )
        )
    raw = target.read_bytes()
    text = raw.decode("utf-8-sig")
    columns, rows = _read_target_rows(target, text)
    if not rows:
        raise SweepError(tr(
            "target peak list is empty: {p0} (give at least one "
            "peak_id)",
            p0=target,
        ))
    return _TargetFile(
        path=target,
        sha256=hashlib.sha256(raw).hexdigest(),
        columns=columns,
        rows=tuple(rows),
    )


def _targets_from_rows(
    rows: Sequence[tuple[int, dict[str, str]]],
    *,
    path: Path | str,
    sha256: str,
    source: str,
    condition: str = "",
) -> LocalizationTargets:
    """Rows -> :class:`LocalizationTargets` (duplicate ids merged, first-seen order kept)."""
    peak_ids: list[int] = []
    reference_ids: dict[int, str] = {}
    lines: list[int] = []
    for index, row in rows:
        number = _parse_peak_id(
            row.get(PEAK_ID_COLUMN), where=tr("{p0} line {p1}", p0=path, p1=index)
        )
        lines.append(index)
        if number not in peak_ids:
            peak_ids.append(number)
        reference = str(row.get(REFERENCE_ID_COLUMN, "") or "").strip()
        if reference and number not in reference_ids:
            reference_ids[number] = reference
    if not peak_ids:
        raise SweepError(tr("target peak list is empty: {p0} (give at least one peak_id)", p0=path))
    return LocalizationTargets(
        peak_ids=tuple(peak_ids),
        path=str(path),
        sha256=str(sha256),
        reference_peak_ids=reference_ids,
        source=str(source),
        condition=str(condition),
        line_ranges=_line_ranges(lines),
    )


def _unknown_conditions(
    names: Sequence[str], conditions: Sequence[str] | None, *, where: str
) -> None:
    """A condition name in the target list has to belong to the study (typos never slip through)."""
    if conditions is None:
        return
    known = [str(item) for item in conditions]
    unknown = [str(name) for name in names if str(name) not in set(known)]
    if unknown:
        raise SweepError(
            tr(
                "condition name in the target peak list does not belong to the study: {p0}(from "
                "{p1}); the study has {p2}. A misspelled condition is never "
                "ignored.",
                p0=unknown,
                p1=where,
                p2=known,
            )
        )


def _validated(
    result: ConditionalTargets, conditions: Sequence[str] | None
) -> ConditionalTargets:
    """With study conditions given, parse every condition (missing rows fail early)."""
    if conditions is not None:
        for name in conditions:
            result.for_condition(str(name))
    return result


def _missing_targets(
    condition: str,
    *,
    where: str,
    path: str,
    sha256: str,
    on_missing: str,
    source: str,
) -> tuple[LocalizationTargets | None, str]:
    """How to handle a condition with no rows -> ``(list | None, origin)``; error by default."""
    if on_missing == "all":
        return None, "on_missing=all"
    if on_missing == "none":
        return (
            LocalizationTargets(
                peak_ids=(),
                path=str(path),
                sha256=str(sha256),
                source=str(source),
                condition=str(condition),
            ),
            "on_missing=none",
        )
    raise SweepError(
        tr(
            "target peak list {p0} has no rows for condition {p1!r}: every condition needsits own "
            "target rows (a missing row is never read as the whole spectrum). To letthrough, "
            "declare on_missing=\"all\" (that condition is unlimited = whole-spectrumrefinement) "
            "or on_missing=\"none\" (refines nothing); the strategy goes into the "
            "record.",
            p0=where,
            p1=condition,
        )
    )


def _resolve_input_path(path: Path | str, base_dir: Path | str | None) -> Path:
    """Resolve a relative path against ``base_dir`` (the combination table's directory)."""
    target = Path(path)
    if base_dir is not None and not target.is_absolute():
        candidate = Path(base_dir) / target
        if candidate.is_file():
            return candidate
    return target


@dataclass(frozen=True)
class ConditionalTargets:
    """One method's spec -> the **per-condition** resolution.

    ``mode``: ``all`` (one list for the batch, bit-for-bit as before) / ``by_condition``
    (a single file with a ``condition`` column) / ``mapping`` (condition -> its own file/list).
    ``for_condition(condition)`` gives the list actually in force (``None`` = unlimited);
    a missing row follows ``on_missing`` (**error by default**, never a silent degradation).
    """

    mode: str = "all"
    #: each condition's own list (a ``by_condition`` file's rows / a ``mapping`` key)
    entries: Mapping[str, LocalizationTargets | None] = field(default_factory=dict)
    #: the shared list for ``mode="all"``
    shared: LocalizationTargets | None = None
    #: ``mapping``'s ``default`` (used by conditions that are not listed)
    default_targets: LocalizationTargets | None = None
    has_default: bool = False
    #: condition -> origin marker (``rows`` / ``mapping`` / ``default`` / ``on_missing=...``)
    origins: Mapping[str, str] = field(default_factory=dict)
    #: the single file's path (empty for the mapping form)
    path: str = ""
    #: the single file's SHA-256 (empty for the mapping form; per-condition files keep their own)
    sha256: str = ""
    source: str = "argument"
    on_missing: str = "error"
    #: condition names seen in the file/mapping (recorded, in first-seen order)
    declared: tuple[str, ...] = ()
    #: the study conditions given while parsing (used to validate unknown names)
    conditions: tuple[str, ...] = ()

    @property
    def is_conditional(self) -> bool:
        """Whether it is split per condition (``False`` = shared by the batch, as before)."""
        return self.mode != "all"

    def _resolve(self, condition: str) -> tuple[LocalizationTargets | None, str]:
        name = str(condition)
        if self.mode == "all":
            return self.shared, "rows"
        if name in self.entries:
            return self.entries[name], str(self.origins.get(name, "rows"))
        if self.has_default:
            return self.default_targets, "default"
        return _missing_targets(
            name,
            where=self.path or tr("(combination table / call-argument mapping)"),
            path=self.path,
            sha256=self.sha256,
            on_missing=self.on_missing,
            source=self.source,
        )

    def for_condition(self, condition: str) -> LocalizationTargets | None:
        """The list in force for this condition (``None`` = unlimited = whole spectrum)."""
        return self._resolve(condition)[0]

    def describe(self) -> str:
        """One line: the shared list, or per condition its peak count and origin."""
        if self.mode == "all":
            return self.shared.describe() if self.shared is not None else tr("(whole spectrum)")
        names = list(self.declared)
        if not names:
            names = list(self.entries)
        parts: list[str] = []
        for name in names:
            resolved = self.entries.get(name)
            origin = str(self.origins.get(name, ""))
            if resolved is None:
                parts.append(tr("{p0}: unlimited ({p1})", p0=name, p1=origin or 'on_missing=all'))
            elif not resolved.peak_ids:
                parts.append(tr(
                    "{p0}: refines nothing "
                    "({p1})",
                    p0=name,
                    p1=origin or 'on_missing=none',
                ))
            else:
                span = ""
                if resolved.line_ranges:
                    span = tr(" (line ") + ",".join(
                        f"{start}-{end}" if start != end else str(start)
                        for start, end in resolved.line_ranges
                    ) + tr(")")
                parts.append(tr("{p0}: {p1} peaks{p2}", p0=name, p1=resolved.n_targets, p2=span))
        head = tr("per condition ") + (tr("(separate files)") if self.mode == "mapping" else "")
        return f"{head}: " + "; ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """Normalised view (record/self-check): mode + origin + per-condition detail."""
        return {
            "schema": LOCALIZATION_TARGETS_SCHEMA,
            "mode": str(self.mode),
            "source": str(self.source),
            "path": str(self.path),
            "sha256": str(self.sha256),
            "on_missing": str(self.on_missing),
            "declared": list(self.declared),
            "conditions": self.by_condition_record(""),
        }

    def by_condition_record(self, condition: str = "") -> str | dict[str, Any]:
        """Per-condition detail for the record: ``"all"`` when shared, else ``{condition: d}``."""
        if self.mode == "all":
            return "all"
        names = list(self.declared) or list(self.entries)
        if condition and str(condition) not in names:
            names.append(str(condition))
        return {name: self._condition_entry(name) for name in names}

    def _condition_entry(self, name: str) -> dict[str, Any]:
        resolved, origin = self._resolve(name)
        if resolved is None:
            return {
                "peak_ids": [],
                "n_targets": 0,
                "line_ranges": [],
                "path": "",
                "sha256": "",
                "from": origin,
            }
        return {
            "peak_ids": [int(value) for value in resolved.peak_ids],
            "n_targets": resolved.n_targets,
            "line_ranges": [
                [int(start), int(end)] for start, end in resolved.line_ranges
            ],
            "path": str(resolved.path),
            "sha256": str(resolved.sha256),
            "from": origin,
        }

    def record_entry(
        self, *, condition: str = "", info: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Per-method record entry (top-level fields as before, plus the per-condition detail)."""
        meta = dict(info or {})
        applied, _origin = self._resolve(condition) if condition else (None, "")
        if applied is None:
            scope = "all"
            peak_ids: list[int] = []
            n_targets = int(meta.get("n_peaks", meta.get("n_targeted", 0)))
            path = str(self.path)
            sha256 = str(self.sha256)
            reference_ids: dict[str, str] = {}
        else:
            scope = applied.scope
            peak_ids = [int(value) for value in applied.peak_ids]
            n_targets = applied.n_targets
            path = str(applied.path or self.path)
            sha256 = str(applied.sha256 or self.sha256)
            reference_ids = {
                str(key): str(applied.reference_peak_ids[key])
                for key in sorted(applied.reference_peak_ids)
            }
        entry: dict[str, Any] = {
            "schema": LOCALIZATION_TARGETS_SCHEMA,
            "scope": scope,
            "source": str(self.source),
            "path": path,
            "sha256": sha256,
            "n_targets": int(n_targets),
            "n_skipped": int(meta.get("n_skipped", 0)),
            "peak_ids": peak_ids,
        }
        if reference_ids:
            entry["reference_peak_ids"] = reference_ids
        entry["by_condition"] = self.by_condition_record(condition)
        if self.is_conditional:
            entry["condition"] = str(condition)
            entry["on_missing"] = str(self.on_missing)
        return entry

    def fingerprint_view(self, condition: str) -> dict[str, Any]:
        """The **per-condition** view used by the resume fingerprint (that condition's own list).

        - a single file written per condition: no whole-file SHA-256, only that condition's
            ``peak_id`` values and line ranges -- editing A's rows does not make B re-run;
        - the mapping form: carries the SHA-256 of that condition's **own** file;
        - shared by the batch: keeps the whole-file path + SHA-256 (as before).
        """
        resolved, origin = self._resolve(condition)
        view: dict[str, Any] = {
            "mode": str(self.mode),
            "source": str(self.source),
            "on_missing": str(self.on_missing),
            "condition": str(condition),
            "declared": list(self.declared),
            "origin": origin,
            "path": str(self.path),
        }
        if self.mode == "all":
            view["sha256"] = str(self.sha256)
        if resolved is None:
            view["targets"] = None
            return view
        view["path"] = str(resolved.path or self.path)
        if self.mode == "mapping":
            view["sha256"] = str(resolved.sha256)
        view["targets"] = {
            "peak_ids": [int(value) for value in resolved.peak_ids],
            "line_ranges": [
                [int(start), int(end)] for start, end in resolved.line_ranges
            ],
        }
        return view


def read_localization_targets(
    path: Path | str, *, source: str = "csv", condition: str | None = None
) -> LocalizationTargets:
    """Read a target peak CSV: a ``peak_id`` column (``reference_peak_id`` optional).

    A single-column text (one peak number per line, header optional) is accepted too. Duplicate
    ids are merged, first-seen order kept.

    - the file has **no** ``condition`` column: the whole file is one list (existing behaviour);
    - the file **does** have one: ``condition=`` names the condition to take (only its rows);
          without it the call **raises** -- reading the file whole would mix peak numbers.

    Parameters
    ----------
    path : Path | str
        the target peak CSV (UTF-8, BOM allowed).
    source : str, default "csv"
        the origin string written into the record.
    condition : str, optional
        take only this condition's rows (the file must have a ``condition`` column).

    Returns
    -------
    LocalizationTargets
        a tuple of peak numbers + path + SHA-256 + optional reference ids + source line ranges.

    Raises
    ------
    SweepError
        a missing file, empty content, no ``peak_id`` column, a non-positive ``peak_id``,
        or a condition with no rows at all.

    Side effects
    ------------
    Reads the file only.
    """
    parsed = _load_target_file(path)
    if not parsed.has_condition:
        return _targets_from_rows(
            parsed.rows, path=parsed.path, sha256=parsed.sha256, source=source
        )
    if condition is None:
        raise SweepError(
            tr(
                "the target peak list has a {p0} column: {p1} -- readingwould mix peak numbers "
                "across conditions. To take it per condition pass condition=...,or use "
                "resolve_conditional_targets() / sweep's localize_peaks(which resolves per "
                "condition automatically).",
                p0=CONDITION_COLUMN,
                p1=parsed.path,
            )
        )
    rows = [
        (index, row)
        for index, row in parsed.rows
        if str(row.get(CONDITION_COLUMN, "") or "").strip() == str(condition)
    ]
    if not rows:
        raise SweepError(
            tr(
                "target peak list {p0} has no rows for condition {p1!r}:every condition needs its "
                "own rows (a missing row is never read as the whole "
                "spectrum)",
                p0=parsed.path,
                p1=condition,
            )
        )
    return _targets_from_rows(
        rows,
        path=parsed.path,
        sha256=parsed.sha256,
        source=source,
        condition=str(condition),
    )


def conditional_targets_from_file(
    path: Path | str,
    *,
    conditions: Sequence[str] | None = None,
    on_missing: str = "error",
    source: str = "csv",
    base_dir: Path | str | None = None,
) -> ConditionalTargets:
    """One CSV -> the per-condition target sets (split by the ``condition`` column when present)."""
    policy = _check_on_missing(on_missing)
    parsed = _load_target_file(_resolve_input_path(path, base_dir))
    names = tuple(str(item) for item in (conditions or ()))
    if not parsed.has_condition:
        shared = _targets_from_rows(
            parsed.rows, path=parsed.path, sha256=parsed.sha256, source=source
        )
        return ConditionalTargets(
            mode="all",
            shared=shared,
            path=str(parsed.path),
            sha256=parsed.sha256,
            source=str(source),
            on_missing=policy,
            conditions=names,
        )
    grouped: dict[str, list[tuple[int, dict[str, str]]]] = {}
    declared: list[str] = []
    for index, row in parsed.rows:
        name = str(row.get(CONDITION_COLUMN, "") or "").strip()
        if not name:
            raise SweepError(
                tr(
                    "{p0} line {p1} has an empty {p2} (missing condition:fill in the condition "
                    "name, or drop the column to share one "
                    "list)",
                    p0=parsed.path,
                    p1=index,
                    p2=CONDITION_COLUMN,
                )
            )
        if name not in grouped:
            grouped[name] = []
            declared.append(name)
        grouped[name].append((index, row))
    _unknown_conditions(declared, conditions, where=str(parsed.path))
    entries: dict[str, LocalizationTargets | None] = {}
    origins: dict[str, str] = {}
    for name in declared:
        entries[name] = _targets_from_rows(
            grouped[name],
            path=parsed.path,
            sha256=parsed.sha256,
            source=source,
            condition=name,
        )
        origins[name] = "rows"
    result = ConditionalTargets(
        mode="by_condition",
        entries=entries,
        origins=origins,
        path=str(parsed.path),
        sha256=parsed.sha256,
        source=str(source),
        on_missing=policy,
        declared=tuple(declared),
        conditions=names,
    )
    return _validated(result, names)


def _spec_targets(
    spec: Any,
    *,
    condition: str,
    base_dir: Path | str | None,
    source: str,
    on_missing: str,
) -> LocalizationTargets | None:
    """One condition's own spec -> a list (empty string = unlimited; file filtered by condition)."""
    if spec is None:
        return _missing_targets(
            condition,
            where=tr("(mapping form)"),
            path="",
            sha256="",
            on_missing=on_missing,
            source=source,
        )[0]
    if isinstance(spec, LocalizationTargets):
        return spec
    if isinstance(spec, (str, Path)):
        token = str(spec).strip()
        if not token:
            return None  # an explicit empty string = unlimited (distinct from "not given")
        parsed = _load_target_file(_resolve_input_path(token, base_dir))
        if not parsed.has_condition:
            return _targets_from_rows(
                parsed.rows,
                path=parsed.path,
                sha256=parsed.sha256,
                source=source,
                condition=condition,
            )
        rows = [
            (index, row)
            for index, row in parsed.rows
            if str(row.get(CONDITION_COLUMN, "") or "").strip() == str(condition)
        ]
        if not rows:
            return _missing_targets(
                condition,
                where=str(parsed.path),
                path=str(parsed.path),
                sha256=parsed.sha256,
                on_missing=on_missing,
                source=source,
            )[0]
        return _targets_from_rows(
            rows,
            path=parsed.path,
            sha256=parsed.sha256,
            source=source,
            condition=condition,
        )
    if isinstance(spec, Mapping):
        if spec.get("path"):
            return _spec_targets(
                spec["path"],
                condition=condition,
                base_dir=base_dir,
                source=source,
                on_missing=on_missing,
            )
        inside = spec.get("peak_ids", spec.get("peak_id", spec.get("ids")))
        if inside is None:
            raise SweepError(
                tr(
                    "unrecognised target peak list: {p0!r}(needs either path or "
                    "peak_ids)",
                    p0=dict(spec),
                )
            )
        return localization_targets_from_ids(inside, source=source)
    if isinstance(spec, Iterable):
        return localization_targets_from_ids(spec, source=source)
    raise SweepError(tr(
        "unrecognised target peak list: {p0!r} (give a CSV path or peak "
        "numbers)",
        p0=spec,
    ))


def _mapping_conditional_targets(
    entries: Mapping[str, Any],
    default_spec: Any,
    *,
    conditions: Sequence[str] | None,
    on_missing: str,
    source: str,
    base_dir: Path | str | None,
) -> ConditionalTargets:
    """The "one file/list per condition" form -> :class:`ConditionalTargets`."""
    policy = _check_on_missing(on_missing)
    declared = tuple(str(key) for key in entries)
    _unknown_conditions(declared, conditions, where=tr(
        "(combination table / call-argument "
        "mapping)",
    ))
    resolved_entries: dict[str, LocalizationTargets | None] = {}
    origins: dict[str, str] = {}
    for name in declared:
        resolved_entries[name] = _spec_targets(
            entries[name],
            condition=name,
            base_dir=base_dir,
            source=source,
            on_missing=policy,
        )
        origins[name] = "mapping"
    has_default = default_spec is not None
    default_targets: LocalizationTargets | None = None
    if has_default:
        default_targets = _spec_targets(
            default_spec,
            condition="",
            base_dir=base_dir,
            source=source,
            on_missing=policy,
        )
    result = ConditionalTargets(
        mode="mapping",
        entries=resolved_entries,
        default_targets=default_targets,
        has_default=has_default,
        origins=origins,
        source=str(source),
        on_missing=policy,
        declared=declared,
        conditions=tuple(str(item) for item in (conditions or ())),
    )
    return _validated(result, conditions)


def resolve_conditional_targets(
    spec: Any,
    *,
    conditions: Sequence[str] | None = None,
    base_dir: Path | str | None = None,
    source: str = "argument",
    on_missing: str = "error",
) -> ConditionalTargets | None:
    """A target spec -> :class:`ConditionalTargets`; ``None`` means unlimited (whole spectrum).

    Accepted forms: a CSV path (a relative path first resolves against ``base_dir``, the
    combination table's directory), :class:`ConditionalTargets`, :class:`LocalizationTargets`,
    a sequence of peak numbers, ``{"path": ...}`` / ``{"peak_ids": [...]}``,
    and a condition mapping ``{"A": "a.csv", "B": "b.csv"}`` /
    ``{"default": "x.csv", "by_condition": {"A": "a.csv"}}``.

    When ``conditions`` is given, condition names and missing rows are validated too (both raise);
    otherwise validation is deferred to :meth:`ConditionalTargets.for_condition`.
    """
    if spec is None:
        return None
    if isinstance(spec, ConditionalTargets):
        return spec
    if isinstance(spec, LocalizationTargets):
        return ConditionalTargets(
            mode="all",
            shared=spec,
            path=str(spec.path),
            sha256=str(spec.sha256),
            source=str(source or spec.source),
            on_missing=_check_on_missing(on_missing),
            conditions=tuple(str(item) for item in (conditions or ())),
        )
    if isinstance(spec, (str, Path)):
        token = str(spec).strip()
        if not token:
            return None
        return conditional_targets_from_file(
            spec,
            conditions=conditions,
            on_missing=on_missing,
            source=source,
            base_dir=base_dir,
        )
    if isinstance(spec, Mapping):
        policy = _check_on_missing(spec.get("on_missing", on_missing))
        lowered = {str(key).strip().lower() for key in spec}
        if "by_condition" in lowered or "default" in lowered:
            extra = sorted(
                str(key)
                for key in spec
                if str(key).strip().lower()
                not in {"by_condition", "default", "on_missing"}
            )
            if extra:
                raise SweepError(
                    tr(
                        "a condition mapping may only hold by_condition / default / "
                        "on_missing,found {p0} as well (pass path or peak_ids for one shared "
                        "list)",
                        p0=extra,
                    )
                )
            inside = spec.get("by_condition") or {}
            if not isinstance(inside, Mapping):
                raise SweepError(
                    tr("by_condition must map condition -> file/list: {p0!r}", p0=inside)
                )
            return _mapping_conditional_targets(
                inside,
                spec.get("default"),
                conditions=conditions,
                on_missing=policy,
                source=source,
                base_dir=base_dir,
            )
        if spec.get("path"):
            return conditional_targets_from_file(
                spec["path"],
                conditions=conditions,
                on_missing=policy,
                source=source,
                base_dir=base_dir,
            )
        inside_ids = spec.get("peak_ids", spec.get("peak_id", spec.get("ids")))
        if inside_ids is not None:
            return ConditionalTargets(
                mode="all",
                shared=localization_targets_from_ids(inside_ids, source=source),
                source=str(source),
                on_missing=policy,
                conditions=tuple(str(item) for item in (conditions or ())),
            )
        return _mapping_conditional_targets(
            spec,
            None,
            conditions=conditions,
            on_missing=policy,
            source=source,
            base_dir=base_dir,
        )
    if isinstance(spec, Iterable):
        return ConditionalTargets(
            mode="all",
            shared=localization_targets_from_ids(spec, source=source),
            source=str(source),
            on_missing=_check_on_missing(on_missing),
            conditions=tuple(str(item) for item in (conditions or ())),
        )
    raise SweepError(
        tr(
            "unrecognised target peak list: {p0!r}(give a CSV path, a sequence of peak numbers, or "
            "a condition "
            "mapping)",
            p0=spec,
        )
    )


def localization_targets_from_ids(
    peak_ids: Any, *, source: str = "argument"
) -> LocalizationTargets:
    """A sequence of peak numbers -> a target list (an empty list raises, never silently all)."""
    if isinstance(peak_ids, (int, str)) and not isinstance(peak_ids, bool):
        values: Sequence[Any] = [peak_ids]
    else:
        try:
            values = list(peak_ids)
        except TypeError:
            raise SweepError(
                tr(
                    "unrecognised target peak list: {p0!r} (give a CSV path or peak "
                    "numbers)",
                    p0=peak_ids,
                )
            ) from None
    ordered: list[int] = []
    for value in values:
        number = _parse_peak_id(value, where=tr("target peak list ({p0})", p0=source))
        if number not in ordered:
            ordered.append(number)
    if not ordered:
        raise SweepError(
            tr(
                "the target peak list is empty: give at least one peak_id(an empty list raises "
                "instead of degrading to whole-spectrum "
                "localization)",
            )
        )
    return LocalizationTargets(peak_ids=tuple(ordered), source=str(source))


def resolve_localization_targets(
    spec: Any,
    *,
    base_dir: Path | str | None = None,
    source: str = "argument",
) -> LocalizationTargets | None:
    """A target spec -> **one** :class:`LocalizationTargets`; ``None`` means whole spectrum.

    Accepts a CSV path (relative paths first resolve against ``base_dir``, the combination
    table's directory), :class:`LocalizationTargets`, a sequence of peak numbers, and the
    mapping forms ``{"path": ...}`` / ``{"peak_ids": [...]}``. For the **per-condition** forms
    (a ``condition`` column / ``{condition: file}``) use
    :func:`resolve_conditional_targets`; for per-method forms use
    :func:`resolve_localization_targets_by_method`.

    Raises
    ------
    SweepError
        the spec is unrecognised, or a named CSV is invalid (see read_localization_targets).
    """
    if spec is None:
        return None
    if isinstance(spec, LocalizationTargets):
        return spec
    if isinstance(spec, (str, Path)):
        token = str(spec).strip()
        if not token:
            return None
        return read_localization_targets(
            _resolve_input_path(token, base_dir), source=source
        )
    if isinstance(spec, Mapping):
        if spec.get("path"):
            return resolve_localization_targets(
                spec["path"], base_dir=base_dir, source=source
            )
        inside = spec.get("peak_ids", spec.get("peak_id", spec.get("ids")))
        if inside is None:
            raise SweepError(
                tr(
                    "unrecognised target peak list: {p0!r}(needs either path or peak_ids; for the "
                    "per-condition mapping use resolve_conditional_targets())",
                    p0=dict(spec),
                )
            )
        return localization_targets_from_ids(inside, source=source)
    if isinstance(spec, Iterable):
        return localization_targets_from_ids(spec, source=source)
    raise SweepError(tr(
        "unrecognised target peak list: {p0!r} (give a CSV path or peak "
        "numbers)",
        p0=spec,
    ))


#: per-method forms that go straight into a combination table (``localization.targets.<method>``)
METHOD_KEYS: tuple[str, ...] = ("parabolic", "gaussian")
#: the form that applies to every method
ALL_METHOD_KEYS: tuple[str, ...] = ("all", "*", "both")


def split_target_specs(spec: Any) -> tuple[Any, dict[str, Any]]:
    """A target spec -> ``(the method-independent one, {method: that method's spec})``.

    As soon as a method key (``parabolic``/``gaussian``/``all``/``*``/``both``) appears, the
    mapping is read **per method**; otherwise the whole mapping is the method-independent one
    (``{"peak_ids": [...]}``, ``{"path": ...}``, a condition mapping ``{"A": "a.csv"}``).
    """
    if not isinstance(spec, Mapping):
        return spec, {}
    per_method: dict[str, Any] = {}
    general: dict[str, Any] = {}
    for key, value in spec.items():
        name = str(key).strip().lower()
        if name in METHOD_KEYS or name in ALL_METHOD_KEYS:
            per_method["all" if name == "both" else name] = value
        else:
            general[str(key)] = value
    if not per_method:
        return spec, {}
    return (general or None), per_method


def combine_target_specs(general: Any, by_method: Mapping[str, Any] | None) -> Any:
    """Method-independent spec + per-method specs -> one spec (both empty -> ``None``)."""
    if not by_method:
        return general
    merged: dict[str, Any] = {}
    if general not in (None, ""):
        merged["all"] = general
    for key, value in dict(by_method).items():
        merged[str(key).strip().lower()] = value
    return merged or None


def resolve_localization_targets_by_method(
    spec: Any,
    *,
    methods: Sequence[str],
    base_dir: Path | str | None = None,
    source: str = "argument",
    conditions: Sequence[str] | None = None,
    on_missing: str = "error",
) -> dict[str, ConditionalTargets | None]:
    """Resolve targets per method -> ``{method: ConditionalTargets | None}``.

    Priority: ``localization.targets.<method>`` > ``localization.targets.all`` (or an
    ``all`` / ``*`` / ``both`` key) > ``localization.targets`` (method-independent).
    An explicit empty string means **unlimited** for that method (distinct from "not given",
    which inherits the method-independent list). Given ``conditions``, names and missing rows
are validated too (failing before processing).
    The result is **per condition**: the list in force comes from
    :meth:`ConditionalTargets.for_condition`.
    """
    general, per_method = split_target_specs(spec)
    shared = resolve_conditional_targets(
        general,
        conditions=conditions,
        base_dir=base_dir,
        source=source,
        on_missing=on_missing,
    )
    out: dict[str, ConditionalTargets | None] = {}
    for method in methods:
        name = str(method).strip().lower()
        value = per_method[name] if name in per_method else per_method.get("all")
        if value is None:
            out[name] = shared
            continue
        out[name] = resolve_conditional_targets(
            value,
            conditions=conditions,
            base_dir=base_dir,
            source=source,
            on_missing=on_missing,
        )
    return out


def _as_conditional(value: Any) -> ConditionalTargets | None:
    """Compatibility layer for the record entry point: ``None`` / ``LocalizationTargets`` too."""
    if value is None or isinstance(value, ConditionalTargets):
        return value
    if isinstance(value, LocalizationTargets):
        return ConditionalTargets(
            mode="all",
            shared=value,
            path=str(value.path),
            sha256=str(value.sha256),
            source=str(value.source),
        )
    raise SweepError(tr("unrecognised target list: {p0!r}", p0=value))


def localization_targets_record(
    by_method: Mapping[str, Any],
    counts: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    condition: str = "",
) -> dict[str, Any]:
    """Per-method target lists -> ``parameters_resolved.detection.localization_targets``.

    The top level keeps the method-independent view (path/sha256/peak count/``n_skipped`` when
    every chosen method shares one list, otherwise ``mixed``); each method's own details
    (including its ``n_skipped`` and the ``by_condition`` per-condition detail) live in
    ``by_method``. Unlimited writes ``scope=all``; ``by_condition`` is ``"all"`` when shared.
    """
    meta = counts or {}
    entries: dict[str, dict[str, Any]] = {}
    for method in sorted(by_method):
        resolved = _as_conditional(by_method[method])
        info = dict(meta.get(method) or {})
        if resolved is None:
            entries[method] = {
                "schema": LOCALIZATION_TARGETS_SCHEMA,
                "scope": "all",
                "source": "none",
                "path": "",
                "sha256": "",
                "n_targets": int(
                    info.get("n_peaks", info.get("n_targeted", 0))
                ),
                "n_skipped": 0,
                "peak_ids": [],
                "by_condition": "all",
            }
            continue
        entries[method] = resolved.record_entry(condition=condition, info=info)
    restricted = {
        method: entry
        for method, entry in entries.items()
        if entry["scope"] != "all"
    }
    if not restricted:
        scope = "all"
    elif all(entry["scope"] == "none" for entry in restricted.values()):
        scope = "none"
    elif len(restricted) == len(entries):
        distinct = {
            (entry["path"], entry["sha256"], tuple(entry["peak_ids"]))
            for entry in restricted.values()
        }
        scope = "subset" if len(distinct) == 1 else "mixed"
    else:
        scope = "mixed"
    sources = {str(entry["source"]) for entry in restricted.values()}
    record: dict[str, Any] = {
        "schema": LOCALIZATION_TARGETS_SCHEMA,
        "scope": scope,
        "source": (
            (next(iter(sources)) if len(sources) == 1 else "mixed")
            if sources
            else "none"
        ),
        "path": "",
        "sha256": "",
        "n_targets": 0,
        "n_skipped": 0,
        "peak_ids": [],
        "by_method": entries,
    }
    first = entries[sorted(entries)[0]] if entries else {}
    if scope in ("subset", "none"):
        record.update(
            {
                "path": first.get("path", ""),
                "sha256": first.get("sha256", ""),
                "n_targets": int(first.get("n_targets", 0)),
                "n_skipped": int(first.get("n_skipped", 0)),
                "peak_ids": list(first.get("peak_ids", [])),
            }
        )
        if first.get("reference_peak_ids"):
            record["reference_peak_ids"] = dict(first["reference_peak_ids"])
    elif scope == "all":
        record["n_targets"] = max(
            (int(entry["n_targets"]) for entry in entries.values()), default=0
        )
    if scope == "mixed":
        record["by_condition"] = "mixed"
    else:
        record["by_condition"] = first.get("by_condition", "all")
    if scope != "mixed" and "condition" in first:
        # when written per condition, record that condition's strategy too (all/none included)
        record["condition"] = first["condition"]
        record["on_missing"] = first.get("on_missing", "error")
    return record


def validate_localization_targets(
    targets: LocalizationTargets,
    peak_ids: Iterable[Any],
    *,
    context: str = "",
) -> tuple[int, ...]:
    """Check target ids against the ``peak_id`` values this spectrum actually detected.

    Peak numbers are **per spectrum** (each workflow/condition has its own), so they must be
    checked against it: a mismatch raises rather than silently dropping those peaks (which
    would let downstream believe they had been refined).
    """
    detected = {int(value) for value in peak_ids}
    unknown = [value for value in targets.peak_ids if value not in detected]
    if unknown:
        shown = ", ".join(str(value) for value in unknown[:20])
        tail = tr(" ... ({p0} in total)", p0=len(unknown)) if len(unknown) > 20 else ""
        span = f"1..{max(detected)}" if detected else tr("none (no peaks detected)")
        detail = f"; {context}" if context else ""
        condition = tr(" (condition {p0})", p0=targets.condition) if targets.condition else ""
        raise SweepError(
            tr(
                "the target list names peak_id values that were not detected: [{p0}]{p1} -- this "
                "spectrum detected {p2} peaks ({p3}){p4}{p5}The target list must match this "
                "spectrum's numbering; unknown ids are not "
                "ignored.",
                p0=shown,
                p1=tail,
                p2=len(detected),
                p3=span,
                p4=detail,
                p5=condition,
            )
        )
    return targets.peak_ids


__all__ = [
    "ALL_METHOD_KEYS",
    "CONDITION_COLUMN",
    "LOCALIZATION_TARGETS_SCHEMA",
    "METHOD_KEYS",
    "ON_MISSING_POLICIES",
    "PEAK_ID_COLUMN",
    "REFERENCE_ID_COLUMN",
    "RESERVED_SPEC_KEYS",
    "ConditionalTargets",
    "LocalizationTargets",
    "combine_target_specs",
    "conditional_targets_from_file",
    "localization_targets_from_ids",
    "localization_targets_record",
    "read_localization_targets",
    "resolve_conditional_targets",
    "resolve_localization_targets",
    "resolve_localization_targets_by_method",
    "split_target_specs",
    "validate_localization_targets",
]
