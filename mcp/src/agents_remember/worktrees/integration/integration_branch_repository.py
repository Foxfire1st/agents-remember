"""Exact Git repository and branch facts used by integration authority."""

from __future__ import annotations

from pathlib import Path

from agents_remember.errors import AgentsRememberError
from agents_remember.kernel.git_command import run_git


class BranchAuthorityUnavailable(AgentsRememberError):
    """A repository's default-branch authority was never RECORDED.

    This is a CONDITION, not a crash: the repository exists and simply does not yet carry the
    authority a branch mutation requires, and the error's own message names the remedy
    (``memory_init`` records it; ``origin/HEAD`` supplies it for a code repository). It is a
    typed member of the product's error family so a caller can answer it in the response
    envelope instead of letting it escape as a traceback in which the caller loses
    ``ok``/``status``/``nextAction`` -- the class `260918-TSIP` `T34` recorded, measured on
    ``memory_baseline_adopt``.

    Deliberately NOT this class: a recorded authority that is malformed, or that names a ref
    which does not resolve. Those are refusals of a state a caller must understand and change
    rather than an absence a sibling tool already reports, and they keep raising -- widening
    this type to cover them would move the boundary the authority tests hold.
    """


def canonical_local_branch(repository: Path, branch: str) -> str:
    """Normalize a local branch spelling and resolve any existing symbolic aliases."""

    normalized = branch.strip().removeprefix("refs/heads/")
    if not normalized:
        raise RuntimeError("branch identity is blank")
    seen: set[str] = set()
    for _step in range(32):
        if normalized in seen:
            raise RuntimeError(f"symbolic branch alias cycle at {normalized!r}")
        seen.add(normalized)
        symbolic = run_git(
            repository,
            ["symbolic-ref", "--quiet", f"refs/heads/{normalized}"],
        )
        if symbolic.returncode == 1 and not symbolic.stderr.strip():
            return normalized
        if symbolic.returncode != 0:
            raise RuntimeError("local branch authority is unreadable")
        target = symbolic.stdout.strip()
        prefix = "refs/heads/"
        if not target.startswith(prefix) or len(target) == len(prefix):
            raise RuntimeError(
                f"local branch alias {normalized!r} has a non-local target {target!r}"
            )
        normalized = target.removeprefix(prefix)
    raise RuntimeError(f"symbolic branch alias chain is too deep at {normalized!r}")


def repository_default_branch(repository: Path) -> str:
    """Return the remote default; code repositories never trust a local override."""

    branch = _remote_repository_default_branch(repository)
    if branch is None:
        raise BranchAuthorityUnavailable(
            f"repository default-branch authority is unavailable for {repository}; "
            "configure origin/HEAD before task branch mutation"
        )
    return branch


def memory_repository_default_branch(repository: Path) -> str:
    """Return remote authority, or memory_init's exact existing local default.

    The local default is whatever ``memory_init`` recorded, validated against itself:
    the recorded name must resolve to a real local branch. It is deliberately **not**
    compared to a fixed name — the memory repository is founded on the code branch the
    developer chose, and a name comparison here would refuse every repository whose
    memory is founded on anything but ``main``.
    """

    remote = _remote_repository_default_branch(repository)
    if remote is not None:
        return remote
    local = run_git(
        repository,
        ["config", "--get", "agents-remember.defaultBranch"],
    )
    branch = local.stdout.strip()
    if local.returncode != 0 or not branch:
        raise BranchAuthorityUnavailable(
            f"memory repository default-branch authority is unavailable for {repository}; "
            "initialize it through memory_init before task branch mutation"
        )
    normalized = branch.removeprefix("refs/heads/")
    if not normalized:
        raise RuntimeError(
            f"memory repository local default-branch authority is malformed for {repository}: "
            f"{branch!r}"
        )
    verified = run_git(repository, ["rev-parse", "--verify", f"refs/heads/{normalized}"])
    if verified.returncode != 0:
        raise RuntimeError(
            f"memory repository local default-branch authority target does not exist for "
            f"{repository}: {branch!r}"
        )
    return canonical_local_branch(repository, normalized)


def _remote_repository_default_branch(repository: Path) -> str | None:
    result = run_git(
        repository,
        ["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
    )
    if result.returncode != 0 or not result.stdout.strip():
        present = run_git(
            repository,
            ["show-ref", "--verify", "--quiet", "refs/remotes/origin/HEAD"],
        )
        if present.returncode == 0:
            raise RuntimeError(
                f"repository default-branch authority is malformed for {repository}: "
                "origin/HEAD is not symbolic"
            )
        return None
    target = result.stdout.strip()
    prefix = "refs/remotes/origin/"
    if not target.startswith(prefix) or len(target) == len(prefix):
        raise RuntimeError(
            f"repository default-branch authority is malformed for {repository}: {target!r}"
        )
    verified = run_git(repository, ["rev-parse", "--verify", target])
    if verified.returncode != 0:
        raise RuntimeError(
            f"repository default-branch authority target does not exist for {repository}: "
            f"{target!r}"
        )
    return canonical_local_branch(repository, target.removeprefix(prefix))


def branch_worktree_owners(repository: Path, branch: str) -> tuple[Path, ...]:
    """Return every linked checkout that currently owns one canonical local branch."""

    result = run_git(repository, ["worktree", "list", "--porcelain"])
    if result.returncode != 0:
        raise RuntimeError("linked-worktree branch authority is unreadable")
    expected = canonical_local_branch(repository, branch)
    owners: list[Path] = []
    for block in result.stdout.strip().split("\n\n"):
        rows = block.splitlines()
        path_row = next((row for row in rows if row.startswith("worktree ")), None)
        branch_row = next((row for row in rows if row.startswith("branch refs/heads/")), None)
        if path_row is None or branch_row is None:
            continue
        found = canonical_local_branch(
            repository,
            branch_row.removeprefix("branch refs/heads/"),
        )
        if found == expected:
            owners.append(Path(path_row.removeprefix("worktree ")).resolve())
    return tuple(owners)
