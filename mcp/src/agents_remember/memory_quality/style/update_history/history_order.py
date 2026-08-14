"""Check Update History ordering and timestamp frames.

Entries must be newest first. Offset-bearing stamps are normalized to UTC; naive stamps
are compared only with other naive stamps so two frames are never ordered against each
other.

A naive timestamp added or edited by the current closeout produces
``update_history_timestamp_naive``. Historical naive stamps remain untouched because
assigning an unknown offset would fabricate provenance. The fixer refuses to sort a
section that mixes offset-bearing and naive timestamps; state offsets first, then sort.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.memory_quality.integrity.onboarding_drift_check.discovery import rel
from agents_remember.memory_quality.style.changed_lines import ChangedLines, changed_lines
from agents_remember.memory_quality.style.finding import QualityFinding, check_result

CHECK_NAME = "style.update_history.history_order"
OFFSET_PATTERN = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")
HEADING_PATTERN = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")
BULLET_PATTERN = re.compile(r"^\s*[-*]\s+(?P<body>.+?)\s*$")
TIMESTAMP_PATTERN = re.compile(
    r"^(?P<timestamp>"
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"
    r"(?::\d{2})?"
    r"(?:Z|[+-]\d{2}:?\d{2})?"
    r")(?::|\s|$)"
)


@dataclass(frozen=True)
class HistoryEntry:
    line: int
    timestamp: str
    value: dt.datetime
    is_valid: bool = True
    has_offset: bool = True


def check_onboarding_root(onboarding_root: Path) -> dict[str, Any]:
    findings: list[QualityFinding] = []
    files_checked = 0
    touched = changed_lines(onboarding_root)
    for path in sorted(onboarding_root.rglob("*.md")):
        if not path.is_file():
            continue
        files_checked += 1
        findings.extend(check_file(path, onboarding_root, touched))
    return check_result(check=CHECK_NAME, files_checked=files_checked, findings=findings)


def check_file(path: Path, onboarding_root: Path, touched: ChangedLines) -> list[QualityFinding]:
    lines = path.read_text(encoding="utf-8").splitlines()
    findings: list[QualityFinding] = []
    for start, end in update_history_sections(lines):
        entries = parse_entries(lines, start, end)
        findings.extend(order_findings(entries, path, onboarding_root, touched))
    return findings


def update_history_sections(lines: list[str]) -> list[tuple[int, int]]:
    sections: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        match = HEADING_PATTERN.match(line)
        if (
            match is None
            or len(match.group("marks")) != 2
            or match.group("title").strip() != "Update History"
        ):
            continue
        end = len(lines)
        for next_index in range(index + 1, len(lines)):
            next_match = HEADING_PATTERN.match(lines[next_index])
            if next_match is not None and len(next_match.group("marks")) <= 2:
                end = next_index
                break
        sections.append((index + 1, end))
    return sections


def parse_entries(
    lines: list[str],
    start: int,
    end: int,
) -> list[HistoryEntry]:
    entries: list[HistoryEntry] = []
    for index in range(start, end):
        match = BULLET_PATTERN.match(lines[index])
        if match is None:
            continue
        timestamp = parse_timestamp(match.group("body"))
        if timestamp is None:
            entries.append(malformed_entry(line=index + 1))
            continue
        try:
            value = datetime_value(timestamp)
        except ValueError:
            entries.append(invalid_entry(line=index + 1, timestamp=timestamp))
            continue
        entries.append(
            HistoryEntry(
                line=index + 1,
                timestamp=timestamp,
                value=value,
                has_offset=has_offset(timestamp),
            )
        )
    return entries


def has_offset(timestamp: str) -> bool:
    """Whether this timestamp states the frame it is written in."""
    return OFFSET_PATTERN.search(timestamp) is not None


def malformed_entry(*, line: int) -> HistoryEntry:
    return HistoryEntry(
        line=line,
        timestamp="",
        value=dt.datetime.max,
        is_valid=False,
    )


def invalid_entry(*, line: int, timestamp: str) -> HistoryEntry:
    return HistoryEntry(
        line=line,
        timestamp=timestamp,
        value=dt.datetime.max,
        is_valid=False,
    )


def parse_timestamp(text: str) -> str | None:
    match = TIMESTAMP_PATTERN.match(text.strip())
    if match is None:
        return None
    return match.group("timestamp")


def datetime_value(timestamp: str) -> dt.datetime:
    """A comparable value for one timestamp, VALID ONLY WITHIN ITS OWN FRAME.

    An offset-bearing stamp is converted to UTC and returned naive; a stamp with no offset
    is returned as written, because there is nothing to convert it from. Two values from
    this function are therefore comparable only when both stamps stated an offset or
    neither did -- see :func:`order_findings`, which is what enforces that, and this
    module's docstring for the population that made it necessary.
    """
    if not timestamp:
        return dt.datetime.max
    text = timestamp.replace("Z", "+00:00")
    if re.search(r"[+-]\d{4}$", text):
        text = f"{text[:-2]}:{text[-2:]}"
    value = dt.datetime.fromisoformat(text)
    if value.tzinfo is not None:
        return value.astimezone(dt.UTC).replace(tzinfo=None)
    return value


def order_findings(
    entries: list[HistoryEntry],
    path: Path,
    onboarding_root: Path,
    touched: ChangedLines,
) -> list[QualityFinding]:
    """Findings for one Update History section, comparing only within a frame.

    ``previous`` is keyed on whether the entry stated an offset, so an offset-bearing entry
    is only ever compared to the last offset-bearing entry above it and a naive one to the
    last naive entry.
    """
    relative = rel(path, onboarding_root)
    findings: list[QualityFinding] = []
    previous: dict[bool, HistoryEntry] = {}
    for entry in entries:
        findings.extend(
            entry_findings(
                entry,
                relative,
                previous.get(entry.has_offset),
                touched.covers(path, entry.line),
            )
        )
        if entry.timestamp and entry.is_valid:
            previous[entry.has_offset] = entry
    return findings


def entry_findings(
    entry: HistoryEntry,
    relative: str,
    previous: HistoryEntry | None,
    is_touched: bool,
) -> list[QualityFinding]:
    if not entry.timestamp:
        return [
            QualityFinding(
                check=CHECK_NAME,
                path=relative,
                line=entry.line,
                severity="warning",
                code="update_history_timestamp_missing",
                message="Update History bullet must start with an ISO timestamp.",
            )
        ]
    if not entry.is_valid:
        return [
            QualityFinding(
                check=CHECK_NAME,
                path=relative,
                line=entry.line,
                severity="warning",
                code="update_history_timestamp_invalid",
                message="Update History timestamp must be a valid ISO datetime.",
                timestamp=entry.timestamp,
            )
        ]
    findings: list[QualityFinding] = []
    if not entry.has_offset and is_touched:
        findings.append(naive_finding(entry, relative))
    if previous is not None and entry.value > previous.value:
        findings.append(order_finding(entry, relative, previous))
    return findings


def naive_finding(entry: HistoryEntry, relative: str) -> QualityFinding:
    return QualityFinding(
        check=CHECK_NAME,
        path=relative,
        line=entry.line,
        severity="warning",
        code="update_history_timestamp_naive",
        message=(
            "Update History timestamp states no UTC offset, so it cannot be ordered "
            "against the offset-bearing entries around it. Append the author's offset "
            "(for example '+02:00')."
        ),
        timestamp=entry.timestamp,
    )


def order_finding(
    entry: HistoryEntry,
    relative: str,
    previous: HistoryEntry,
) -> QualityFinding:
    return QualityFinding(
        check=CHECK_NAME,
        path=relative,
        line=entry.line,
        severity="warning",
        code="update_history_not_newest_first",
        message="Update History entries are not newest-first.",
        timestamp=entry.timestamp,
        previous_timestamp=previous.timestamp,
        previous_line=previous.line,
    )
