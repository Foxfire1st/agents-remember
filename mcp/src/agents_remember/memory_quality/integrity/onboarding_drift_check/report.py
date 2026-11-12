"""Drift report rendering (markdown/text/json/csv) and report path resolution."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.primitives.drift_snapshot import sanitize_report_token
from agents_remember.memory_quality.integrity.onboarding_drift_check.git_ops import (
    current_branch_name,
)


def default_report_filename(repo_root: Path) -> str:
    repo_name = sanitize_report_token(repo_root.name)
    branch_name = sanitize_report_token(current_branch_name(repo_root))
    return f"{repo_name}_{branch_name}_drift-report.md"


def default_report_dir(temp_root: Path, repo_root: Path) -> Path:
    return temp_root / "drift-reports" / sanitize_report_token(repo_root.name)


def default_report_path(temp_root: Path, repo_root: Path) -> Path:
    return default_report_dir(temp_root, repo_root) / default_report_filename(repo_root)


def resolve_report_path(
    report_path: Path | None,
    coordination_root: Path,
    temp_root: Path,
    repo_root: Path,
    memory_root: Path | None = None,
) -> Path:
    if report_path is None:
        return default_report_path(temp_root, repo_root)
    if (
        memory_root is not None
        and report_path.is_absolute()
        and report_path.resolve().is_relative_to(memory_root.resolve())
    ):
        return default_report_dir(temp_root, repo_root) / report_path.name
    if report_path.is_absolute():
        if report_path.resolve().is_relative_to(coordination_root.resolve()):
            return report_path
        return default_report_dir(temp_root, repo_root) / report_path.name
    candidate = temp_root / report_path
    if candidate.resolve().is_relative_to(temp_root.resolve()):
        return candidate
    return default_report_dir(temp_root, repo_root) / report_path.name
