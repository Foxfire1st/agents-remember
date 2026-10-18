"""Reusable native Git reads for change-set routes; no second diff or endpoint parser."""

from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

from agents_remember.kernel.git_command import run_git
from agents_remember.serving.changeset_cache import (
    ReadInput,
    RepositoryRead,
    Retention,
    cached_read,
    observe_inputs,
)
from agents_remember.worktrees.modules import git, git_changes


def _object_id(value: str) -> bool:
    """Whether a revision names one immutable object rather than a ref or a range."""
    return len(value) in (40, 64) and all(character in "0123456789abcdef" for character in value)


def _object_ids(arguments: list[str]) -> tuple[str, ...]:
    """The object ids an argument list reads, including ``<id>^{commit}`` and ``<id>:<path>``."""
    found: list[str] = []
    for argument in arguments:
        head = argument.split("^", 1)[0].split(":", 1)[0]
        if _object_id(head):
            found.append(head)
    return tuple(dict.fromkeys(found))


def change_counters(files: list[dict[str, Any]]) -> dict[str, int]:
    values = tuple((file["insertions"], file["deletions"]) for file in files)
    return cached_read(("change-counters", values), (), lambda: _measure_counters(values))


def _measure_counters(values: tuple[tuple[int | None, int | None], ...]) -> dict[str, int]:
    return {
        "files": len(values),
        "insertions": sum(int(added or 0) for added, _ in values),
        "deletions": sum(int(removed or 0) for _, removed in values),
    }


def changed_files_with_counts(
    root: Path, base: str, head: str | None = None
) -> list[dict[str, Any]]:
    if head is not None and _object_id(base) and _object_id(head):
        # f0607391 / b84bf836: the entire id-keyed hit has no observations,
        # including attribute paths discovered by the native read on a miss.
        return cached_read(
            ("changed-files", root, base, head),
            (),
            lambda: git_changes.changed_files_with_counts(root, base, head),
        )

    attributes: tuple[Path, ...] = ()

    def observe(paths: list[str]) -> None:
        nonlocal attributes
        attributes = tuple(
            sorted(
                {
                    root / parent / ".gitattributes"
                    for path in paths
                    for parent in Path(path).parents
                }
            )
        )
        observe_inputs(attributes)

    if head is None:
        # Rule 5: a working view's freshness check would cost about what the read
        # costs, so it has no cache at all (L174-R1-06). The diff is Git's own and is
        # computed for every request; only the counters over its result are reused.
        return git_changes.changed_files_with_counts(root, base, None, observe=observe)

    return cached_read(
        ("changed-files", root, base, head),
        _range_inputs(root, base, head),
        lambda: git_changes.changed_files_with_counts(root, base, head, observe=observe),
        additional_inputs=lambda: attributes,
    )


def _range_inputs(root: Path, base: str, head: str) -> tuple[ReadInput, ...]:
    """The freshness inputs of one commit range (MIK-R176 rule 5).

    A diff between two commit ids is fixed by those ids: it is served again **from its
    key with no observation on a hit** — no Git child, no layout read, no stat of any
    object file (architect ruling 2026-10-18, notes/run3/L174-COMMIT-PAIRS-ARE-SERVED-
    WITHOUT-OBSERVATION-R1-05-WITHDRAWN, which also withdrew sealed finding L174-R1-05).

    Accepted gap, stated here and in the module docstring: a kept commit-pair answer
    outlives the loss of the objects it was computed from until the cache evicts it
    (L174-R1-07's bound) or the server restarts; and a working-tree input that could
    change the answer — ``.gitattributes``, diff configuration — is not observed per
    hit. Such a change is a repository/configuration change outside the key, not a
    change of what the pair's diff is.

    A revision that is not a plain object id (a ref, a revision expression) keeps the
    general repository observation. A working view never reaches here: it is read
    natively, uncached (L174-R1-06).
    """

    if _object_id(base) and _object_id(head):
        return ()
    return (RepositoryRead(root),)


def head_commit(root: Path, ref: str = "HEAD") -> str:
    return cached_read(
        ("head", root, ref),
        (RepositoryRead(root, references=(ref,), observe_objects=False),),
        lambda: git.head_commit(root, ref),
    )


def branch_exists(root: Path, branch: str) -> bool:
    return cached_read(
        ("branch-exists", root, branch),
        (RepositoryRead(root, references=(f"refs/heads/{branch}",), observe_objects=False),),
        lambda: git.branch_exists(root, branch),
    )


def commit_text_or_none(root: Path, ref: str, path: str) -> str | None:
    keep = True

    def produce() -> str | None:
        nonlocal keep
        value = git.commit_text_or_none(root, ref, path)
        if value is None:
            # The native text reader also returns None on a failed show. Retain
            # only absence proved by a readable tree, never a missing object.
            proof = run_git(root, ["ls-tree", "-z", ref, "--", path])
            keep = proof.returncode == 0 and not proof.stdout
        return value

    return cached_read(
        ("commit-text", root, ref, path),
        _revision_inputs(root, ref),
        produce,
        retention=Retention(keep=lambda _: keep),
    )


def _revision_inputs(root: Path, ref: str) -> tuple[ReadInput, ...]:
    """A fixed object id is served from its key without observation; a ref is observed.

    The named gap above applies to an id-keyed read here as well: its kept answer
    outlives object loss until eviction or restart.
    """

    if _object_id(ref):
        return ()
    return (RepositoryRead(root, observe_objects=False),)


def read_git(root: Path, arguments: list[str]) -> CompletedProcess[str]:
    """The serving selectors' fixed read-only commands, run by the sole Git runner."""
    keyed = bool(_object_ids(arguments))
    return cached_read(
        ("git-read", root, *arguments),
        () if keyed else (RepositoryRead(root, observe_objects=False),),
        lambda: run_git(root, arguments),
        retention=Retention(keep=lambda result: result.returncode == 0),
    )


def working_text(path: Path) -> str | None:
    return cached_read(
        ("working-text", path),
        (path,),
        lambda: path.read_text(errors="replace") if path.is_file() else None,
    )
