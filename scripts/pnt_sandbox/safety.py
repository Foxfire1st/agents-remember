"""The safety check: every root the PNT build would use lies inside the sandbox directory.

The roots are not computed here. ``build_roots.py`` resolves them inside the Python environment
of the build under test, with that build's own configuration loader and resolver, once as the
dashboard and once as the tool server. This module only judges the result, and it fails closed:
a root that is missing, unresolved or outside the sandbox fails the check and is named.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .layout import PASEO_VERSION, SandboxLayout, embed_entries

SETTINGS_KEY = "configPath"
# What the check must have seen at least (PNT-R11 item 4), whatever the resolver says it
# reports. Repository roots are required per configured repository, and at least one repository
# must be configured. The resolver's own ``expected`` list is required on top of this.
REQUIRED_ROOTS = (
    SETTINGS_KEY,
    "coordinationRoot",
    "workspaceRoot",
    "transcriptRoot",
    "harnessSkillRoot",
    "receipts.taskless",
    "receipts.messageBindings",
    "reports.taskless",
    "daggerAuthorityRoot",
    "paseoRuntime.home",
    "paseoRuntime.installPrefix",
)
REQUIRED_REPOSITORY_ROOTS = (
    "path",
    "memoryRoot",
    "taskRoot (receipts and reports of task-bound roles)",
    "leafEnclosures",
)
PROCESS_MODES = {"dashboard": "dashboard", "mcp": "tool server"}


@dataclass(frozen=True)
class Finding:
    key: str
    problem: str

    def __str__(self) -> str:
        return f"{self.key}: {self.problem}"


@dataclass(frozen=True)
class SafetyReport:
    roots: Mapping[str, str | None]
    findings: tuple[Finding, ...]

    @property
    def ok(self) -> bool:
        return not self.findings


def _inside(path: str, sandbox: Path) -> bool:
    resolved = Path(path).resolve(strict=False)
    return resolved == sandbox or sandbox in resolved.parents


def _required_keys(report: Mapping[str, Any]) -> list[str]:
    values = report.get("values")
    repositories = values.get("repositories") if isinstance(values, dict) else None
    keys: list[str] = list(REQUIRED_ROOTS)
    for repo_id in repositories if isinstance(repositories, list) else []:
        keys.extend(f"repositories.{repo_id}.{name}" for name in REQUIRED_REPOSITORY_ROOTS)
    expected = report.get("expected")
    if isinstance(expected, list):
        keys.extend(key for key in expected if isinstance(key, str))
    return keys


def expected_values(layout: SandboxLayout) -> dict[str, Any]:
    """Settings that are not paths and still decide where the sandbox's processes reach."""
    return {
        "dashboard.port": layout.dashboard_port,
        "dashboard.autoStart": False,
        "paseoRuntime.listen": layout.paseo_listen,
        "paseoRuntime.version": PASEO_VERSION,
        "paseoRuntime.embed": embed_entries(layout),
    }


def evaluate(layout: SandboxLayout, checkout: Path, report: Mapping[str, Any]) -> SafetyReport:
    """Judge one resolution of the build's roots against the sandbox directory."""
    sandbox = layout.root.resolve(strict=False)
    raw_roots = report.get("roots")
    roots: dict[str, str | None] = dict(raw_roots) if isinstance(raw_roots, dict) else {}
    raw_errors = report.get("errors")
    errors: dict[str, str] = dict(raw_errors) if isinstance(raw_errors, dict) else {}
    values = report.get("values")
    values = values if isinstance(values, dict) else {}
    findings: list[Finding] = []

    expected_package = (checkout / "mcp" / "src" / "agents_remember").resolve(strict=False)
    if report.get("packageRoot") != expected_package.as_posix():
        findings.append(
            Finding(
                "build",
                f"the roots were resolved by {report.get('packageRoot')!r}, not by the checkout's "
                f"own source {expected_package.as_posix()!r}",
            )
        )
    if SETTINGS_KEY in errors:
        # The settings could not be loaded: nothing else was resolved, and that is the finding.
        findings.append(Finding(SETTINGS_KEY, f"cannot be resolved: {errors[SETTINGS_KEY]}"))
        return SafetyReport({SETTINGS_KEY: None}, tuple(findings))
    for key in _required_keys(report):
        roots.setdefault(key, None)
    if not values.get("repositories"):
        findings.append(Finding("repositories", "cannot be resolved: no repository is configured"))
    for key, path in roots.items():
        if not isinstance(path, str) or not path:
            reason = errors.get(key) or "the build reported no path"
            findings.append(Finding(key, f"cannot be resolved: {reason}"))
        elif not _inside(path, sandbox):
            findings.append(Finding(key, f"resolves outside the sandbox directory: {path}"))
    for key, expected in expected_values(layout).items():
        if values.get(key) != expected:
            findings.append(
                Finding(key, f"is {values.get(key)!r}; the sandbox requires {expected!r}")
            )
    return SafetyReport(roots, tuple(findings))


def check(
    layout: SandboxLayout,
    checkout: Path,
    resolve: Callable[[str], Mapping[str, Any]],
) -> SafetyReport:
    """Resolve as each process the sandbox runs and judge both; any failure fails the check.

    ``resolve`` runs the build's resolver for one process mode and raises when it cannot.
    """
    roots: dict[str, str | None] = {}
    findings: list[Finding] = []
    for mode, label in PROCESS_MODES.items():
        try:
            report = resolve(mode)
        except Exception as error:  # an unreadable resolution is an unresolved one
            findings.append(Finding(f"{label} roots", f"cannot be resolved: {error}"))
            continue
        judged = evaluate(layout, checkout, report)
        for key, path in judged.roots.items():
            if roots.setdefault(key, path) != path:
                findings.append(
                    Finding(key, f"the {label} resolves {path!r}, another process {roots[key]!r}")
                )
        for finding in judged.findings:
            labelled = Finding(finding.key, f"{finding.problem} (as the {label})")
            if all(known.key != finding.key for known in findings):
                findings.append(labelled)
    return SafetyReport(roots, tuple(findings))
