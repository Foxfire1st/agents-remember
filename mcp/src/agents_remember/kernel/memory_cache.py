"""The consumer ledger is a disposable view of memory commit attribution."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_attribution import (
    MemoryAttributionError,
    attributed_commits,
    ledger_rows_from_attribution,
)
from agents_remember.kernel.memory_ledger import (
    LEDGER_RELATIVE_PATH,
    LedgerError,
    MemoryLedger,
    ledger_to_text,
)


def derive_memory_ledger(
    repository: Path,
    tip: str = "HEAD",
    *,
    repo_name: str | None = None,
) -> MemoryLedger:
    """Read mappings from Git without consulting a cached file or a ledger commit."""

    rows = ledger_rows_from_attribution(attributed_commits(repository, tip=tip))
    first = rows[0] if rows else None
    oldest = rows[-1] if rows else None
    return MemoryLedger(
        repo_name=repo_name or repository.name,
        base_code_commit=oldest.code_commit if oldest else "",
        base_memory_commit=oldest.memory_commit if oldest else "",
        last_verified_code_commit=first.code_commit if first else "",
        last_memory_content_commit=first.memory_commit if first else "",
        sort_order="newest-first",
        rows=rows,
    )


def prepare_memory_cache(repository: Path) -> None:
    """Exclude the cache from the next memory-content commit, retaining its disk copy.

    Removing a previously tracked cache and recording its ignore rule belong to the ordinary
    memory-content change. They never require a separate ledger publication. This also removes
    cache-only index conflicts; genuine content conflicts remain in the index for Git to refuse.
    """

    ignore = repository / ".gitignore"
    text = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    if f"/{LEDGER_RELATIVE_PATH}" not in text.splitlines():
        separator = "" if not text or text.endswith("\n") else "\n"
        atomic_write_text(ignore, f"{text}{separator}/{LEDGER_RELATIVE_PATH}\n")
    result = run_git(
        repository,
        ["rm", "--cached", "--force", "--ignore-unmatch", "--", LEDGER_RELATIVE_PATH],
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "could not exclude the memory cache")


def refresh_memory_cache(
    repository: Path,
    tip: str = "HEAD",
    *,
    path: Path | None = None,
    repo_name: str | None = None,
) -> dict[str, object]:
    """Materialize the derived view; cache availability cannot decide Git transaction success."""

    target = path or repository / LEDGER_RELATIVE_PATH
    try:
        ledger = derive_memory_ledger(repository, tip, repo_name=repo_name)
        intended = ledger_to_text(ledger)
        try:
            before = target.read_bytes()
        except OSError:
            before = None
        changed = before != intended.encode("utf-8")
        if changed:
            atomic_write_text(target, intended)
        return {
            "state": "updated" if changed else "current",
            "path": target.as_posix(),
            "rows": len(ledger.rows),
        }
    except (OSError, UnicodeError, LedgerError, MemoryAttributionError) as error:
        return {"state": "unavailable", "path": target.as_posix(), "reason": str(error)}


def discard_memory_cache_changes(repository: Path) -> None:
    """Discard only the cache before an authorized memory-worktree removal.

    Git still checks every other path during ordinary, non-forced worktree removal.
    A tracked cache is restored from HEAD; an untracked cache is removed.
    """
    tracked = run_git(repository, ["ls-tree", "--name-only", "HEAD", "--", LEDGER_RELATIVE_PATH])
    if tracked.returncode:
        raise RuntimeError(tracked.stderr.strip() or "could not inspect memory cache tracking")
    command = (
        ["restore", "--source=HEAD", "--staged", "--worktree", "--", LEDGER_RELATIVE_PATH]
        if tracked.stdout.strip()
        else ["rm", "--cached", "--force", "--ignore-unmatch", "--", LEDGER_RELATIVE_PATH]
    )
    for args in (command, ["clean", "--force", "--", LEDGER_RELATIVE_PATH]):
        result = run_git(repository, args)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "could not discard memory cache changes")
