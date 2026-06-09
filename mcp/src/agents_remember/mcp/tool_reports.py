"""Bulk tool diagnostics written to temp report files with retention.

Verbose passthrough payloads (raw provider status trees, watcher rebind runs,
command transcripts) flooded tool responses with thousands of tokens per call.
The compact response keeps the outcome plus a ``reportPath``; the full payload
lands here, under ``temp/tool-reports/<tool>/``, where the caller opens it only
when forensic depth is needed.

Retention is write-time and deterministic — keep the newest ``KEEP_LAST`` files
per tool and nothing older than ``MAX_AGE_DAYS`` — so disk stays bounded with
no daemons, timers, or hidden background behavior.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

KEEP_LAST = 5
MAX_AGE_DAYS = 7

# Provider commands embed credentials inline (e.g. PGPASSWORD=...); reports are
# files on disk and must never contain them, success or failure.
_SECRET_PATTERN = re.compile(r"(PASSWORD=)[^\s\"']+")


def write_tool_report(
    coordination_root: Path,
    tool: str,
    payload: Any,
    *,
    label: str = "report",
) -> Path:
    folder = coordination_root / "temp" / "tool-reports" / tool
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = folder / f"{stamp}-{label}.json"
    counter = 1
    while path.exists():
        path = folder / f"{stamp}-{label}-{counter}.json"
        counter += 1
    path.write_text(
        json.dumps(redact_secrets(payload), indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    prune_tool_reports(folder)
    return path


def prune_tool_reports(
    folder: Path,
    *,
    keep_last: int = KEEP_LAST,
    max_age_days: int = MAX_AGE_DAYS,
    now: float | None = None,
) -> list[Path]:
    """Keep the newest ``keep_last`` reports younger than ``max_age_days``."""
    current = time.time() if now is None else now
    cutoff = current - max_age_days * 86400
    removed: list[Path] = []
    reports = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for index, report in enumerate(reports):
        if index >= keep_last or report.stat().st_mtime < cutoff:
            report.unlink(missing_ok=True)
            removed.append(report)
    return removed


def redact_secrets(payload: Any) -> Any:
    if isinstance(payload, str):
        return _SECRET_PATTERN.sub(r"\1***", payload)
    if isinstance(payload, dict):
        return {key: redact_secrets(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [redact_secrets(item) for item in payload]
    return payload
