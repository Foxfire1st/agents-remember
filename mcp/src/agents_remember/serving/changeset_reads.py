"""Reusable native Git reads for change-set routes; no second diff or endpoint parser."""

from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

from agents_remember.kernel.git_command import run_git
from agents_remember.serving.changeset_cache import RepositoryRead, cached_read, observe_inputs
from agents_remember.worktrees.modules import git, git_changes


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

    return cached_read(
        ("changed-files", root, base, head),
        (RepositoryRead(root, working=head is None),),
        lambda: git_changes.changed_files_with_counts(root, base, head, observe=observe),
        additional_inputs=lambda: attributes,
    )


def head_commit(root: Path, ref: str = "HEAD") -> str:
    return cached_read(
        ("head", root, ref),
        (RepositoryRead(root, references=(ref,)),),
        lambda: git.head_commit(root, ref),
    )


def branch_exists(root: Path, branch: str) -> bool:
    return cached_read(
        ("branch-exists", root, branch),
        (RepositoryRead(root, references=(f"refs/heads/{branch}",)),),
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
        (RepositoryRead(root),),
        produce,
        retain_when=lambda _: keep,
    )


def read_git(root: Path, arguments: list[str]) -> CompletedProcess[str]:
    """The serving selectors' fixed read-only commands, run by the sole Git runner."""
    return cached_read(
        ("git-read", root, *arguments),
        (RepositoryRead(root),),
        lambda: run_git(root, arguments),
        retain_when=lambda result: result.returncode == 0,
    )


def working_text(path: Path) -> str | None:
    return cached_read(
        ("working-text", path),
        (path,),
        lambda: path.read_text(errors="replace") if path.is_file() else None,
    )
