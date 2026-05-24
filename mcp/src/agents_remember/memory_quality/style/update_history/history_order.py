"""Check Update History sections for newest-first ordering."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CHECK_NAME = "style.update_history.history_order"
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


@dataclass(frozen=True)
class QualityFinding:
    check: str
    path: str
    line: int
    severity: str
    code: str
    message: str
    timestamp: str | None = None
    previous_timestamp: str | None = None
    previous_line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "check": self.check,
            "path": self.path,
            "line": self.line,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.timestamp is not None:
            payload["timestamp"] = self.timestamp
        if self.previous_timestamp is not None:
            payload["previousTimestamp"] = self.previous_timestamp
        if self.previous_line is not None:
            payload["previousLine"] = self.previous_line
        return payload


def check_onboarding_root(onboarding_root: Path) -> dict[str, Any]:
    findings: list[QualityFinding] = []
    files_checked = 0
    for path in sorted(onboarding_root.rglob("*.md")):
        if not path.is_file():
            continue
        files_checked += 1
        findings.extend(check_file(path, onboarding_root))
    return {
        "ok": not findings,
        "check": CHECK_NAME,
        "filesChecked": files_checked,
        "findingCount": len(findings),
        "findings": [finding.to_dict() for finding in findings],
    }


def check_file(path: Path, onboarding_root: Path) -> list[QualityFinding]:
    lines = path.read_text(encoding="utf-8").splitlines()
    findings: list[QualityFinding] = []
    for start, end in update_history_sections(lines):
        entries = parse_entries(lines, start, end)
        findings.extend(order_findings(entries, path, onboarding_root))
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
            )
        )
    return entries


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
) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    previous: HistoryEntry | None = None
    for entry in entries:
        if not entry.timestamp:
            findings.append(
                QualityFinding(
                    check=CHECK_NAME,
                    path=relative_path(path, onboarding_root),
                    line=entry.line,
                    severity="warning",
                    code="update_history_timestamp_missing",
                    message="Update History bullet must start with an ISO timestamp.",
                )
            )
            continue
        if not entry.is_valid:
            findings.append(
                QualityFinding(
                    check=CHECK_NAME,
                    path=relative_path(path, onboarding_root),
                    line=entry.line,
                    severity="warning",
                    code="update_history_timestamp_invalid",
                    message="Update History timestamp must be a valid ISO datetime.",
                    timestamp=entry.timestamp,
                )
            )
            continue
        if previous is not None and entry.value > previous.value:
            findings.append(
                QualityFinding(
                    check=CHECK_NAME,
                    path=relative_path(path, onboarding_root),
                    line=entry.line,
                    severity="warning",
                    code="update_history_not_newest_first",
                    message="Update History entries are not newest-first.",
                    timestamp=entry.timestamp,
                    previous_timestamp=previous.timestamp,
                    previous_line=previous.line,
                )
            )
        previous = entry
    return findings


def relative_path(path: Path, onboarding_root: Path) -> str:
    try:
        return path.relative_to(onboarding_root).as_posix()
    except ValueError:
        return path.as_posix()
