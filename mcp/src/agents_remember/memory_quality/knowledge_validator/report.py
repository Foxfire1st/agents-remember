"""What the validator returns: violations, the report, and the refusal (MIK-R22, Failure behavior).

Every violation names its file, its field and its rule. A violation of a rule the registry marks
report-only is carried in the report but never refuses. A refusal lists every refusing violation,
and a formatting violation names the formatter command that fixes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, order=True)
class Violation:
    """One broken rule: ``path`` and ``field`` locate it, ``rule`` names the registry entry."""

    path: str
    field: str
    rule: str
    message: str
    report_only: bool = False

    def render(self) -> str:
        marker = " (report-only)" if self.report_only else ""
        return f"{self.path}: {self.field or '-'}: [{self.rule}]{marker} {self.message}"

    def to_document(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "field": self.field,
            "rule": self.rule,
            "message": self.message,
            "reportOnly": self.report_only,
        }


@dataclass(frozen=True)
class ValidationReport:
    """Every violation found in one candidate tree, refusing and report-only, sorted."""

    candidate: str
    violations: tuple[Violation, ...]

    @property
    def refusals(self) -> tuple[Violation, ...]:
        return tuple(violation for violation in self.violations if not violation.report_only)

    @property
    def reports(self) -> tuple[Violation, ...]:
        return tuple(violation for violation in self.violations if violation.report_only)

    @property
    def ok(self) -> bool:
        return not self.refusals

    def render(self) -> str:
        return "\n".join(violation.render() for violation in self.violations)

    def to_document(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate,
            "ok": self.ok,
            "refusalCount": len(self.refusals),
            "reportCount": len(self.reports),
            "violations": [violation.to_document() for violation in self.violations],
        }


class KnowledgeValidationError(ValueError):
    """A memory commit is refused: the candidate tree fails the knowledge validator."""

    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        lines = "\n".join(violation.render() for violation in report.refusals)
        super().__init__(
            "the knowledge validator (MIK-R22) refuses this memory commit: "
            f"{len(report.refusals)} violation(s) in {report.candidate}\n{lines}"
        )
