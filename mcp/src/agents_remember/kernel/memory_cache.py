"""The consumer ledger is a disposable view of memory commit attribution."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_write_bytes, atomic_write_text
from agents_remember.kernel.git_command import run_git, run_git_with_index
from agents_remember.kernel.memory_attribution import (
    MemoryAttributionError,
    attributed_commits,
    ledger_rows_from_attribution,
)
from agents_remember.kernel.memory_ignore import with_briefs_ignore
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


def ignore_memory_cache(repository: Path) -> bytes:
    """Record the cache's ignore rule: the one write :func:`prepare_memory_cache` makes to content.

    A route that judges the exact tree it is about to commit records the rule first, so the tree it
    judges already holds the line, and restores ``.gitignore`` when it refuses.

    Returns the exact bytes written, so a refusing route can tell its own change apart from a
    concurrent external edit without re-reading the file.
    """

    ignore = repository / ".gitignore"
    text = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    updated = _with_ledger_ignore(text)
    if updated != text:
        atomic_write_text(ignore, updated)
    return updated.encode("utf-8")


def _with_ledger_ignore(text: str) -> str:
    if f"/{LEDGER_RELATIVE_PATH}" not in text.splitlines():
        separator = "" if not text or text.endswith("\n") else "\n"
        return f"{text}{separator}/{LEDGER_RELATIVE_PATH}\n"
    return text


def memory_publication_ignore_bytes(previous: bytes | None) -> bytes:
    """Compute the exact ignore bytes owned by a durable memory publication."""

    text = previous.decode("utf-8") if previous is not None else ""
    return with_briefs_ignore(_with_ledger_ignore(text)).encode("utf-8")


def ignore_memory_publication(repository: Path) -> bytes:
    """Prepare both derived surfaces in one write before a publication's gate."""

    ignore = repository / ".gitignore"
    try:
        before = ignore.read_bytes()
    except FileNotFoundError:
        before = None
    prepared = memory_publication_ignore_bytes(before)
    if prepared != before:
        atomic_write_bytes(ignore, prepared)
    return prepared


def prepare_memory_cache(repository: Path, *, index_path: Path | None = None) -> bytes:
    """Exclude the cache from the next memory-content commit, retaining its disk copy.

    Removing a previously tracked cache and recording its ignore rule belong to the ordinary
    memory-content change. They never require a separate ledger publication. This also removes
    cache-only index conflicts; genuine content conflicts remain in the index for Git to refuse.

    With ``index_path`` the exclusion writes a private index file instead of
    the worktree's own index; without it the worktree's own index is prepared.

    Returns the prepared `.gitignore` bytes (see :func:`ignore_memory_cache`).
    """

    prepared = ignore_memory_cache(repository)
    _exclude_memory_cache(repository, index_path)
    return prepared


def prepare_memory_publication(repository: Path, *, index_path: Path) -> bytes:
    """Prepare a private index and both ignore rules for an admitted publication."""

    prepared = ignore_memory_publication(repository)
    _exclude_memory_cache(repository, index_path)
    return prepared


def _exclude_memory_cache(repository: Path, index_path: Path | None) -> None:
    args = ["rm", "--cached", "--force", "--ignore-unmatch", "--", LEDGER_RELATIVE_PATH]
    if index_path is not None:
        result = run_git_with_index(repository, args, index_path)
    else:
        result = run_git(repository, args)
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
