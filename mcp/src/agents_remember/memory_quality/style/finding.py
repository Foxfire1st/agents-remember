"""Shared finding record for memory-layer style checks.

``report_only`` findings are counted and rendered but never affect ``ok``. Enforcing rules
must emit ordinary findings; report-only is reserved for review rankings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
    report_only: bool = False

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
        if self.report_only:
            payload["reportOnly"] = True
        return payload


def check_result(
    *,
    check: str,
    files_checked: int,
    findings: list[QualityFinding],
) -> dict[str, Any]:
    """Package findings into the uniform runner result ``memory_quality.check`` expects.

    Report-only findings are split out of ``findings``/``findingCount`` and into
    ``reportOnlyFindings``/``reportOnlyFindingCount``, and are absent from ``ok``, so no
    caller has to know which codes are advisory to decide whether the gate passed.
    """
    enforced = [finding for finding in findings if not finding.report_only]
    advisory = [finding for finding in findings if finding.report_only]
    return {
        "ok": not enforced,
        "check": check,
        "filesChecked": files_checked,
        "findingCount": len(enforced),
        "findings": [finding.to_dict() for finding in enforced],
        "reportOnlyFindingCount": len(advisory),
        "reportOnlyFindings": [finding.to_dict() for finding in advisory],
    }
