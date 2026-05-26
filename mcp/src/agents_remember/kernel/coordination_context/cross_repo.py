from __future__ import annotations

import subprocess
from pathlib import Path

from agents_remember.kernel.coordination_context.models import (
    CrossRepoAllowEntry,
    CrossRepoSettings,
)
from agents_remember.kernel.memory_ledger import LedgerError, load_ledger


def run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo_root.as_posix()}", *args],
        cwd=repo_root,
        text=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )


def git_branch(repo_root: Path) -> str:
    result = run_git(repo_root, ["branch", "--show-current"])
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def git_head(repo_root: Path) -> str:
    result = run_git(repo_root, ["rev-parse", "HEAD"])
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def resolve_cross_repo_settings(
    settings: CrossRepoSettings,
    workspace_root: Path,
    coordination_root: Path,
) -> CrossRepoSettings:
    resolved = CrossRepoSettings(errors=list(settings.errors))
    for entry in settings.allow:
        resolved.allow.append(resolve_cross_repo_entry(entry, workspace_root, coordination_root))
    return resolved


def resolve_cross_repo_entry(
    entry: CrossRepoAllowEntry,
    workspace_root: Path,
    coordination_root: Path,
) -> CrossRepoAllowEntry:
    invalid = invalid_cross_repo_entry(entry)
    if invalid is not None:
        return invalid
    code_path = (workspace_root / entry.repo).resolve()
    if not code_path.exists():
        return _entry_with_state(
            entry, "excluded", f"external code path missing: {code_path.as_posix()}"
        )
    code_info = code_repository_info(code_path)
    code_exclusion = code_repo_exclusion(entry, code_info)
    if code_exclusion:
        return _entry_with_state(entry, "excluded", code_exclusion, code_info)
    if not entry.include_memory:
        return _entry_with_state(
            entry, "included-code-only", "memory inclusion disabled for this entry", code_info
        )
    return resolve_cross_repo_memory(entry, coordination_root, code_info)


def invalid_cross_repo_entry(entry: CrossRepoAllowEntry) -> CrossRepoAllowEntry | None:
    if entry.state == "excluded" and entry.reason:
        return entry
    if not entry.repo or not entry.expected_branch:
        return CrossRepoAllowEntry(
            repo=entry.repo,
            expected_branch=entry.expected_branch,
            include_code=entry.include_code,
            include_memory=entry.include_memory,
            state="excluded",
            reason="repo and expectedBranch are required",
        )
    if not entry.include_code:
        return CrossRepoAllowEntry(
            repo=entry.repo,
            expected_branch=entry.expected_branch,
            include_code=entry.include_code,
            include_memory=entry.include_memory,
            state="excluded",
            reason="includeCode=false leaves no minimum code source to validate",
        )
    return None


def code_repository_info(code_path: Path) -> dict[str, str]:
    return {
        "path": code_path.as_posix(),
        "branch": git_branch(code_path),
        "head": git_head(code_path),
    }


def code_repo_exclusion(entry: CrossRepoAllowEntry, code_info: dict[str, str]) -> str:
    if not code_info["branch"]:
        return "external code repo is detached or not a git repository"
    if code_info["branch"] != entry.expected_branch:
        return (
            f"external code repo is on branch {code_info['branch']}, "
            f"expected {entry.expected_branch}"
        )
    return ""


def resolve_cross_repo_memory(
    entry: CrossRepoAllowEntry, coordination_root: Path, code_info: dict[str, str]
) -> CrossRepoAllowEntry:
    memory_path = (coordination_root / "memory-repos" / f"ar-{entry.repo}").resolve()
    if not memory_path.exists():
        return _entry_with_state(
            entry,
            "included-code-only",
            f"external memory repo missing: {memory_path.as_posix()}",
            code_info,
        )
    memory_info = memory_repository_info(memory_path)
    if memory_info["branch"] != entry.expected_branch:
        return _entry_with_state(
            entry,
            "included-code-only",
            f"external memory repo is on branch {memory_info['branch'] or 'detached'}, expected {entry.expected_branch}",
            code_info,
            memory_info,
        )
    return with_memory_ledger_state(entry, memory_path, code_info, memory_info)


def memory_repository_info(memory_path: Path) -> dict[str, str]:
    return {
        "path": memory_path.as_posix(),
        "branch": git_branch(memory_path),
        "ledgerPath": (memory_path / "memory.md").as_posix(),
    }


def with_memory_ledger_state(
    entry: CrossRepoAllowEntry,
    memory_path: Path,
    code_info: dict[str, str],
    memory_info: dict[str, str],
) -> CrossRepoAllowEntry:
    try:
        ledger = load_ledger(memory_path / "memory.md")
    except LedgerError as error:
        return _entry_with_state(entry, "included-code-only", str(error), code_info, memory_info)
    memory_info.update(
        {
            "lastVerifiedCodeCommit": ledger.last_verified_code_commit,
            "lastMemoryContentCommit": ledger.last_memory_content_commit,
        }
    )
    return _entry_with_state(entry, "included", "", code_info, memory_info)


def _entry_with_state(
    entry: CrossRepoAllowEntry,
    state: str,
    reason: str,
    code: dict[str, str] | None = None,
    memory: dict[str, str] | None = None,
) -> CrossRepoAllowEntry:
    return CrossRepoAllowEntry(
        repo=entry.repo,
        expected_branch=entry.expected_branch,
        include_code=entry.include_code,
        include_memory=entry.include_memory,
        state=state,
        reason=reason,
        code=code or {},
        memory=memory or {},
    )
