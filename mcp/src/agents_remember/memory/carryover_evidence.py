"""Read-only code-landing evidence shared by carryover planning and admission."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.git_command import GitRunnerOptions, run_git


def require_git(repo: Path, args: list[str], *, input_text: str | None = None) -> str:
    result = run_git(
        repo,
        args,
        GitRunnerOptions(input_text=input_text),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def head_commit(repo: Path, ref: str) -> str:
    return require_git(repo, ["rev-parse", ref])


def commit_date(repo: Path, commit: str) -> str:
    return require_git(repo, ["show", "-s", "--format=%cI", commit])


def ensure_clean(repo: Path, label: str, *pathspecs: str) -> None:
    status = require_git(repo, ["status", "--porcelain", "--", *pathspecs])
    if status:
        raise RuntimeError(f"{label} is not clean:\n{status}")


def has_changes(repo: Path, *pathspecs: str) -> bool:
    return bool(require_git(repo, ["status", "--porcelain", "--", *pathspecs]))


def changed_paths(repo: Path, base_ref: str, head_ref: str) -> set[str]:
    output = require_git(
        repo, ["diff", "--name-only", "--diff-filter=ACMRT", base_ref, head_ref, "--"]
    )
    return {line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()}


def path_exists_at_ref(repo: Path, ref: str, source_path: str) -> bool:
    return run_git(repo, ["cat-file", "-e", f"{ref}:{source_path}"]).returncode == 0


def blob_at_ref(repo: Path, ref: str, source_path: str) -> str | None:
    result = run_git(repo, ["show", f"{ref}:{source_path}"])
    if result.returncode != 0:
        return None
    return result.stdout


def path_commits(repo: Path, base_ref: str, head_ref: str, source_path: str) -> list[str]:
    output = require_git(repo, ["log", "--format=%H", f"{base_ref}..{head_ref}", "--", source_path])
    return [line.strip() for line in output.splitlines() if line.strip()]


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return run_git(repo, ["merge-base", "--is-ancestor", ancestor, descendant]).returncode == 0


def patch_id(repo: Path, base_ref: str, head_ref: str, source_path: str) -> str | None:
    diff_text = require_git(repo, ["diff", base_ref, head_ref, "--", source_path])
    if not diff_text.strip():
        return None
    result = run_git(
        repo,
        ["patch-id", "--stable"],
        GitRunnerOptions(input_text=diff_text),
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.split()[0]


def evidence_for_path(
    repo: Path, base_ref: str, official_ref: str, source_ref: str, source_path: str
) -> tuple[str, str]:
    if not path_exists_at_ref(repo, official_ref, source_path):
        return "not-landed", "source path is not present on official code ref"
    path_commit_list = path_commits(repo, base_ref, source_ref, source_path)
    if path_commit_list and all(
        is_ancestor(repo, commit, official_ref) for commit in path_commit_list
    ):
        # Only the strongest proof when EVERY source-branch commit touching this
        # path has landed on the official ref. A single landed commit is not
        # enough: a later, unlanded commit to the same path would otherwise be
        # silently carried over as if it had landed.
        return (
            "exact-landed-commit",
            f"all {len(path_commit_list)} source branch commit(s) touching this path "
            "are ancestors of official code ref",
        )
    source_patch = patch_id(repo, base_ref, source_ref, source_path)
    official_patch = patch_id(repo, base_ref, official_ref, source_path)
    if source_patch and official_patch and source_patch == official_patch:
        return (
            "patch-id-match",
            "old-base-to-source-branch patch matches old-base-to-official patch",
        )
    if blob_at_ref(repo, source_ref, source_path) == blob_at_ref(repo, official_ref, source_path):
        return (
            "final-content-match",
            "source branch and official source content match at the selected refs",
        )
    return (
        "same-path-changed",
        "official and source branch changed this path but no equivalence was proven",
    )


def object_id_at_ref(repo: Path, ref: str, source_path: str) -> str | None:
    """Git object id of a file blob or directory tree at a ref; None if absent.

    The repo-root route ('.') resolves to the commit's root tree because
    ``<ref>:.`` is not valid rev syntax.
    """
    target = f"{ref}^{{tree}}" if source_path in {"", "."} else f"{ref}:{source_path}"
    result = run_git(repo, ["rev-parse", target])
    return result.stdout.strip() if result.returncode == 0 else None


def memory_merge_base(target_memory: Path, source_memory: Path) -> str | None:
    source_head = run_git(source_memory, ["rev-parse", "HEAD"])
    if source_head.returncode != 0:
        return None
    result = run_git(target_memory, ["merge-base", "HEAD", source_head.stdout.strip()])
    return result.stdout.strip() if result.returncode == 0 else None
