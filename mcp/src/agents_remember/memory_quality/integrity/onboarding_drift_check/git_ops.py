"""Git interaction helpers and source change notes for drift detection."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from agents_remember.kernel.coordination_context_resolver import normalize_rel_path
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import (
    repo_root_placeholder,
)


def run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo_root.as_posix()}", *args],
        cwd=repo_root,
        text=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )


def current_branch_name(repo_root: Path) -> str:
    branch = run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch.returncode != 0:
        return "unknown-branch"
    return branch.stdout.strip() or "unknown-branch"


def local_change_note(repo_root: Path, source_file: str) -> str:
    states: list[str] = []
    unstaged = run_git(repo_root, ["diff", "--quiet", "--", source_file])
    if unstaged.returncode == 1:
        states.append("unstaged")
    elif unstaged.returncode != 0:
        return f"Unable to inspect local unstaged changes: {unstaged.stderr.strip() or 'unknown git error'}."

    staged = run_git(repo_root, ["diff", "--cached", "--quiet", "--", source_file])
    if staged.returncode == 1:
        states.append("staged")
    elif staged.returncode != 0:
        return f"Unable to inspect local staged changes: {staged.stderr.strip() or 'unknown git error'}."

    if not states:
        return ""
    return f"Source has local {' and '.join(states)} changes not represented in HEAD."


def list_repo_sources(repo_root: Path) -> list[str]:
    result = run_git(repo_root, ["ls-files", "-z"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git ls-files failed")
    return [normalize_rel_path(value) for value in result.stdout.split("\0") if value]


def local_route_change_note(repo_root: Path, source_route: str) -> str:
    return local_change_note(
        repo_root, "." if source_route in {"", repo_root_placeholder()} else source_route
    )


def git_stdout(repo_root: Path, args: list[str]) -> str:
    result = run_git(repo_root, args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def git_blob_hash(repo_root: Path, source_path: str, *, ref: str = "HEAD") -> str:
    return git_stdout(repo_root, ["rev-parse", f"{ref}:{source_path}"])


def compute_git_blob_set_fingerprint(
    repo_root: Path, evidence_paths: list[str], *, ref: str = "HEAD"
) -> str:
    lines: list[str] = []
    for source_path in sorted(evidence_paths):
        blob_hash = git_blob_hash(repo_root, source_path, ref=ref)
        lines.append(f"{source_path}\0{blob_hash}\n")
    digest = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def entity_local_change_notes(repo_root: Path, evidence_paths: list[str]) -> list[str]:
    notes: list[str] = []
    for source_path in evidence_paths:
        note = local_change_note(repo_root, source_path)
        if note:
            notes.append(f"{source_path}: {note}")
    return notes
