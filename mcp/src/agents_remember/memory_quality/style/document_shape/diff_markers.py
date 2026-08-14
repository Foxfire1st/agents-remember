"""Report diff prefixes left at column zero in memory Markdown.

``+#`` is a broken heading and ``+ `` is a spurious list item. Both are reported with
the remediation to remove the prefix and, for a spurious bullet, rejoin the preceding
sentence.

False-positive boundaries:

1. Fenced code blocks may quote diff lines and are skipped.
2. A repository that intentionally uses ``+`` list markers would be reported; normalize
   those markers before enabling this check.
3. Indented plus markers are ordinary nested list items and are outside the rule.
4. A leading plus followed by neither space nor `#` is literal prose and is not reported.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.memory_quality.integrity.onboarding_drift_check.discovery import rel
from agents_remember.memory_quality.style.document_shape import inline_scan
from agents_remember.memory_quality.style.finding import QualityFinding, check_result

CHECK_NAME = "style.document_shape.diff_markers"
HEADING_PREFIX = "+#"
BULLET_PREFIX = "+ "


def check_onboarding_root(onboarding_root: Path) -> dict[str, Any]:
    findings: list[QualityFinding] = []
    files_checked = 0
    for path in sorted(onboarding_root.rglob("*.md")):
        if not path.is_file():
            continue
        files_checked += 1
        findings.extend(check_file(path, onboarding_root))
    return check_result(check=CHECK_NAME, files_checked=files_checked, findings=findings)


def check_file(path: Path, onboarding_root: Path) -> list[QualityFinding]:
    lines = path.read_text(encoding="utf-8").splitlines()
    findings: list[QualityFinding] = []
    for index, line in inline_scan.unfenced_lines(lines):
        finding = line_finding(line, path=path, onboarding_root=onboarding_root, index=index)
        if finding is not None:
            findings.append(finding)
    return findings


def line_finding(
    line: str,
    *,
    path: Path,
    onboarding_root: Path,
    index: int,
) -> QualityFinding | None:
    if line.startswith(HEADING_PREFIX):
        return marker_finding(
            path=path,
            onboarding_root=onboarding_root,
            index=index,
            code="leaked_diff_marker_heading",
            message=(
                "Line begins with '+#', so the heading renders as literal text. "
                "Delete the leading '+' left by a paste from a diff."
            ),
        )
    if line.startswith(BULLET_PREFIX):
        return marker_finding(
            path=path,
            onboarding_root=onboarding_root,
            index=index,
            code="leaked_diff_marker_bullet",
            message=(
                "Line begins with '+ ', which renders as a new list item mid-sentence. "
                "Delete the leading '+' and rejoin the line to the sentence above it."
            ),
        )
    return None


def marker_finding(
    *,
    path: Path,
    onboarding_root: Path,
    index: int,
    code: str,
    message: str,
) -> QualityFinding:
    return QualityFinding(
        check=CHECK_NAME,
        path=rel(path, onboarding_root),
        line=index + 1,
        severity="warning",
        code=code,
        message=message,
    )
