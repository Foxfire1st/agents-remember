from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agents_remember.worktrees.modules.landing_commit_check import CommitCheckPass

from agents_remember.kernel import filesystem
from agents_remember.kernel.git_command import (
    GitRunnerOptions,
    ref_compare_and_swap_args,
    run_git,
    run_git_with_index,
)
from agents_remember.models.lifecycles.mutation_evidence import GitMutationSnapshot
from agents_remember.worktrees.modules import capture_index
from agents_remember.worktrees.modules.git_message import _cleaned_message
from agents_remember.worktrees.modules.git_text import (
    _nul_records,
    _transport_safe_git_diagnostic,
)
from agents_remember.worktrees.modules.models import CommitPublicationRefusal
from agents_remember.worktrees.worktree_contract import WorktreeContract

# This module used to define its own `run_git` -- the kernel's function with the
# environment guard, the timeout and the explicit encoding all dropped -- and every
# destructive worktree operation (commit, merge --ff-only, reset --hard, rebase,
# branch -f, branch -D, worktree remove --force, push origin --delete) ran through
# it. With GIT_DIR exported those landed in whatever repository GIT_DIR named. The
# helpers below now call the one guarded runner; nothing else about them changed.


def require_git(repo: Path, args: list[str]) -> str:
    result = run_git(repo, args)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git {' '.join(args)} failed"
        raise RuntimeError(_transport_safe_git_diagnostic(detail))
    return result.stdout.strip()


def _require_git_with_index(repo: Path, args: list[str], index: Path) -> str:
    """Run one Git command against a private index file; a failure keeps its name."""

    result = run_git_with_index(repo, args, index)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git {' '.join(args)} failed"
        raise RuntimeError(_transport_safe_git_diagnostic(detail))
    return result.stdout.strip()


def worktree_candidate_tree(
    repo: Path, index_path: Path, *, exclude_paths: tuple[str, ...] = ()
) -> str:
    """The tree of ``repo``'s working files as ``git add -A`` over ``HEAD`` would commit them.

    The tree is ``HEAD`` with every staged, unstaged and eligible untracked change applied; ignored
    paths stay out, and ``exclude_paths`` are omitted as explicitly owned derived files. Its objects
    are written into the repository, because an uncommitted candidate is pinned by a ref that names
    this tree. The worktree's real index, its files and the user's work are never changed: every Git
    command here runs against a private index under ``index_path``'s directory, and none reads the
    real one.

    **Work in proportion to what changed (MIK-R40 rule 1).** The private index starts as a copy of
    the worktree's own index, which carries the file times Git recorded, so ``git add`` hashes only
    the files Git cannot prove unchanged (:func:`capture_index._tree_from_index_copy`). When that copy cannot be
    made or Git reports an error on the way, the capture is taken again from an empty index, which
    hashes every file (:func:`_tree_from_head`): slower, the same tree, never a guess.
    """
    # The caller selects a scratch namespace; every observation owns its physical index.
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f".{index_path.name}-", dir=index_path.parent) as temporary:
        scratch = Path(temporary)
        try:
            return capture_index._tree_from_index_copy(repo, scratch / "index", exclude_paths)
        except capture_index._IndexCopyUnusable:
            return _tree_from_head(repo, scratch / "full-index", exclude_paths)


def _tree_from_head(repo: Path, isolated_index: Path, exclude_paths: tuple[str, ...]) -> str:
    """The full capture: an empty private index seeded from ``HEAD``, so every file is hashed."""

    actions = [("seed candidate index", ["read-tree", "HEAD"])]
    if exclude_paths:
        actions.append(
            ("exclude derived files", ["update-index", "--force-remove", "--", *exclude_paths])
        )
    actions.append(("materialize candidate tree", capture_index._add_all_args(exclude_paths)))
    for action, args in actions:
        result = run_git_with_index(repo, args, isolated_index)
        if result.returncode != 0:
            raise RuntimeError(
                f"could not {action}: "
                f"{_transport_safe_git_diagnostic(result.stderr.strip() or result.stdout.strip())}"
            )
    result = run_git_with_index(repo, ["write-tree"], isolated_index)
    if result.returncode != 0:
        raise RuntimeError(
            "could not resolve candidate tree: "
            f"{_transport_safe_git_diagnostic(result.stderr.strip() or result.stdout.strip())}"
        )
    tree = result.stdout.strip()
    return tree


def current_branch(repo: Path) -> str:
    return require_git(repo, ["branch", "--show-current"])


def head_commit(repo: Path, ref: str = "HEAD") -> str:
    return require_git(repo, ["rev-parse", ref])


def local_branch_ref(branch: str) -> str:
    """Return the explicit local-ref spelling for a branch authority cell."""

    normalized = branch.removeprefix("refs/heads/")
    if not normalized or normalized == "HEAD" or normalized.startswith("refs/"):
        raise RuntimeError(f"invalid local branch authority: {branch!r}")
    return f"refs/heads/{normalized}"


def branch_commit(repo: Path, branch: str) -> str:
    """Resolve one exact local branch, never a same-named tag or remote ref."""

    return require_git(repo, ["rev-parse", "--verify", f"{local_branch_ref(branch)}^{{commit}}"])


def branch_exists(repo: Path, branch: str) -> bool:
    return (
        run_git(repo, ["show-ref", "--verify", "--quiet", local_branch_ref(branch)]).returncode == 0
    )


def checkout_holding_branch(repo_path: Path, branch: str) -> Path | None:
    """The checkout whose HEAD is the named branch, if any holds it (F01).

    Branch tips are repository-global: any holding checkout observes the same
    refs, so the direct-landing code leg journals its intent and proof against
    this checkout while the contract anchors the code repository itself.
    """

    try:
        listing = require_git(repo_path, ["worktree", "list", "--porcelain"])
    except RuntimeError:
        return None
    wanted = f"refs/heads/{branch.removeprefix('refs/heads/')}"
    path: str | None = None
    current: str | None = None
    detached = False
    bare = False
    found: Path | None = None

    def flush() -> None:
        nonlocal found, path, current, detached, bare
        if found is None and path is not None and current == wanted and not detached and not bare:
            found = Path(path)
        path, current = None, None
        detached, bare = False, False

    for line in listing.splitlines():
        if not line.strip():
            flush()
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            path = value.strip()
        elif key == "branch":
            current = value.strip()
        elif key == "detached":
            detached = True
        elif key == "bare":
            bare = True
    flush()
    return found


def repository_identity(repo: Path | None) -> Path | None:
    """Resolve Git's shared object-store identity for a checkout or linked worktree."""

    if repo is None or not repo.is_dir():
        return None
    result = run_git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    common_dir = result.stdout.strip()
    if result.returncode != 0 or not common_dir:
        return None
    return Path(common_dir).resolve()


def _status_args(exclude_paths: tuple[str, ...]) -> list[str]:
    args = ["status", "--porcelain"]
    if exclude_paths:
        args.extend(["--", ".", *map(capture_index._excluded_pathspec, exclude_paths)])
    return args


def has_changes(repo: Path, *, exclude_paths: tuple[str, ...] = ()) -> bool:
    return bool(require_git(repo, _status_args(exclude_paths)))


def worktree_dirty(repo: Path | None, *, exclude_paths: tuple[str, ...] = ()) -> bool:
    return bool(repo and repo.exists() and has_changes(repo, exclude_paths=exclude_paths))


def contract_has_worktree_changes(contract: WorktreeContract) -> bool:
    return worktree_dirty(contract.code_worktree) or worktree_dirty(
        contract.memory_worktree, exclude_paths=("memory.md",)
    )


def require_clean(repo: Path, label: str, *, exclude_paths: tuple[str, ...] = ()) -> None:
    changes = require_git(repo, _status_args(exclude_paths))
    if changes:
        raise RuntimeError(f"{label} is not clean:\n{changes}")


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    if ancestor == descendant:
        # A commit is its own ancestor exactly when Git can read it; the
        # verify form is the read's own memoized existence probe (F17), so a
        # repeated self-check costs no new child.
        return (
            run_git(repo, ["rev-parse", "--verify", "--quiet", f"{ancestor}^{{commit}}"]).returncode
            == 0
        )
    return run_git(repo, ["merge-base", "--is-ancestor", ancestor, descendant]).returncode == 0


def require_readable_commit(repo: Path, commit: str, *, what: str) -> None:
    """Refuse when Git cannot read ``commit``; the caller names it through ``what``.

    A landing never judges a commit it cannot read: an unreadable input refuses by name
    instead of passing as absent or non-member (MIK-R52 F01).
    """

    result = run_git(repo, ["rev-parse", "--verify", "--quiet", commit])
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        raise RuntimeError(
            f"{what} ({commit}) cannot be read"
            + (f" (git rev-parse: {detail})" if detail else "")
            + "; the landing is not judged"
        )


def require_ancestor(repo: Path, ancestor: str, descendant: str, *, what: str) -> bool:
    """Like :func:`is_ancestor`, but only a plain non-ancestry answers ``False``.

    rc 0 answers True and rc 1 answers False (a legitimate non-ancestor keeps its policy).
    Any other Git failure -- an unreadable object, a failed read -- raises naming ``what``,
    so a failed read never passes as ordinary non-membership (MIK-R52 F01).
    """

    result = run_git(repo, ["merge-base", "--is-ancestor", ancestor, descendant])
    if result.returncode in (0, 1):
        return result.returncode == 0
    detail = (result.stderr or "").strip()
    raise RuntimeError(
        f"{what} cannot be read (git merge-base rc {result.returncode}"
        + (f": {detail}" if detail else "")
        + "); the landing is not judged"
    )


def ensure_git_identity(repo: Path) -> None:
    if not run_git(repo, ["config", "--get", "user.email"]).stdout.strip():
        require_git(repo, ["config", "user.email", "agents-remember@example.invalid"])
    if not run_git(repo, ["config", "--get", "user.name"]).stdout.strip():
        require_git(repo, ["config", "user.name", "Agents Remember"])


def ensure_worktree(
    contract: WorktreeContract,
    *,
    side: Literal["code", "memory"],
    dry_run: bool,
) -> str:
    """Create one exact contract-owned ordinary worktree after live authority validation."""

    from agents_remember.worktrees.integration.integration_branch_authority import (  # noqa: PLC0415
        require_ordinary_worktree,
    )

    if contract.kind != "leaf":
        raise RuntimeError("worktree creation requires an ordinary leaf contract")
    require_ordinary_worktree(contract, operation="worktree_start")
    if side == "code":
        repo = contract.code_repo_path
        worktree = contract.code_worktree
        branch = contract.code_work_branch
        source_branch = contract.code_source_branch
    elif contract.memory_mode == "external" and contract.memory_repo_path is not None:
        if contract.memory_worktree is None:
            raise RuntimeError("external-memory worktree authority is missing its target path")
        repo = contract.memory_repo_path
        worktree = contract.memory_worktree
        branch = contract.memory_work_branch
        source_branch = contract.memory_source_branch
    else:
        raise RuntimeError("memory worktree creation requires an external-memory contract")
    if worktree.exists():
        return "existing"
    if dry_run:
        return "would-create"
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if branch_exists(repo, branch):
        require_git(repo, ["worktree", "add", str(worktree), branch])
    else:
        require_git(repo, ["worktree", "add", "-b", branch, str(worktree), source_branch])
    return "created"


def stage_worktree_content(
    repo: Path, *, exclude_paths: tuple[str, ...] = (), index_path: Path | None = None
) -> None:
    """Stage real content without reading or retaining explicitly derived index entries.

    With ``index_path`` the staging writes a private index file instead of the
    worktree's own index; without it the worktree's own index is staged.
    """

    def step(args: list[str]) -> str:
        if index_path is not None:
            return _require_git_with_index(repo, args, index_path)
        return require_git(repo, args)

    args = ["add", "-A"]
    if exclude_paths:
        step(["update-index", "--force-remove", "--", *exclude_paths])
        args.extend(["--", ".", *map(capture_index._excluded_pathspec, exclude_paths)])
    step(args)


@dataclass(frozen=True)
class _IndexShape:
    """An index as a publication found it: where it lives, its tree, and what no tree records.

    ``tags`` holds the ``git ls-files -v`` letter of every entry: a lower-case letter is an
    assume-unchanged entry, ``S`` or ``s`` a skip-worktree one. A path listed here that ``tree``
    does not hold is an intent-to-add entry, because ``git write-tree`` leaves those out.
    """

    top: Path
    tree: str
    tags: dict[str, str]
    index_path: Path | None = None
    index_bytes: bytes | None = None
    index_mode: int = 0o644
    index_times: tuple[int, int] | None = None


def _index_shape(repo: Path) -> _IndexShape:
    """Read the index's shape. It runs in the top-level directory, where ``ls-files`` lists all."""

    top = Path(require_git(repo, ["rev-parse", "--show-toplevel"]))
    index = Path(require_git(top, ["rev-parse", "--path-format=absolute", "--git-path", "index"]))
    found = index.stat() if index.exists() else None
    raw = index.read_bytes() if found is not None else None
    mode = found.st_mode & 0o777 if found is not None else 0o644
    times = (found.st_atime_ns, found.st_mtime_ns) if found is not None else None
    listing = _nul_records(_nul_git(top, ["ls-files", "-v", "-z"]))
    return _IndexShape(
        top,
        require_git(top, ["write-tree"]),
        {row[2:]: row[0] for row in listing if len(row) > 2},
        index,
        raw,
        mode,
        times,
    )


def _index_edit(top: Path, args: list[str], records: list[str]) -> None:
    """One Git command that writes the index from NUL-terminated records on its standard input."""

    if not records:
        return
    text = "".join(f"{record}\0" for record in records)
    result = run_git(top, args, GitRunnerOptions(input_text=text))
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git {' '.join(args)} failed"
        raise RuntimeError(_transport_safe_git_diagnostic(detail))


def _retarget_index(shape: _IndexShape, tree: str) -> None:
    """Replace exactly the index entries that differ from ``tree``, reading no working file.

    ``git diff-index --cached`` names the differing paths. An intent-to-add entry is invisible to
    it, as it is to ``git write-tree``, so one that ``tree`` does not hold stays in the index.
    ``git update-index --index-info`` then writes ``tree``'s entry for each of those paths (mode 0
    removes the path). Every other entry keeps its file times and its flags. A replaced entry loses
    its assume-unchanged and skip-worktree flags, so both are set again from ``shape``.
    """

    diff = ["diff-index", "--cached", "--raw", "-z", "--no-renames", "--ita-invisible-in-index"]
    raw = _nul_records(_nul_git(shape.top, [*diff, tree, "--"]))
    # A raw record is ``:<tree mode> <index mode> <tree blob> <index blob> <status>``, then a path.
    changes = [
        (header.split()[0].lstrip(":"), header.split()[2], path)
        for header, path in zip(raw[::2], raw[1::2], strict=True)
    ]
    _index_edit(
        shape.top,
        ["update-index", "-z", "--index-info"],
        [f"{mode} {blob}\t{path}" for mode, blob, path in changes],
    )
    _set_index_flags(shape, [path for mode, _, path in changes if int(mode, 8)])


def _set_index_flags(shape: _IndexShape, paths: list[str]) -> None:
    """Set on ``paths`` the assume-unchanged and skip-worktree flags ``shape`` recorded for them."""

    flags = {"--assume-unchanged": str.islower, "--skip-worktree": lambda tag: tag in "Ss"}
    for option, flagged in flags.items():
        # One flag per command: ``update-index`` applies only the last of several such options.
        _index_edit(
            shape.top,
            ["update-index", "-z", option, "--stdin"],
            [path for path in paths if flagged(shape.tags.get(path, "H"))],
        )


def stage_tree(repo: Path, tree: str) -> None:
    """Make the index record exactly ``tree``, reading nothing from the working tree.

    A commit staged this way records ``tree`` whatever was written to the working tree since the
    tree was read from it; such a file stays an uncommitted change. Only the entries that differ
    from ``tree`` are replaced (:func:`_retarget_index`), so afterwards ``git write-tree`` answers
    ``tree``, an entry keeps its assume-unchanged and skip-worktree flags, an intent-to-add entry
    that ``tree`` does not hold is still there, and a sparse checkout shows no file outside its
    cone as deleted.
    """

    _retarget_index(_index_shape(repo), tree)


@contextmanager
def staged_publication_tree(repo: Path, tree: str) -> Iterator[str]:
    """Stage ``tree`` for a publication, giving the index back on a refusal.

    The yielded tree is what the index holds inside the block. When the block
    raises, the index is given back exactly as found (tree, flags and
    intent-to-add entries); when it returns, the staged tree stays. A landing
    route runs the repository's commit check inside this block (MIK-R152), so a
    refused check leaves files and index exactly as found.
    """

    found = _index_shape(repo)
    stage_tree(repo, tree)
    try:
        yield tree
    except BaseException:
        _give_index_back(found)
        raise


def unstage_bound_tree(repo: Path, staged_tree: str, before_index: str | _IndexShape) -> bool:
    """Give the index back when it still holds exactly ``staged_tree``.

    An index someone else changed meanwhile is left alone and ``False`` is returned, so no edit of
    theirs is overwritten. ``before_index`` is the tree the index held before, or the shape a
    publication recorded of it; with the shape the entries that were intent-to-add come back too
    (:func:`_give_index_back`). Entries keep their flags either way (:func:`stage_tree`).
    """

    current = run_git(repo, ["write-tree"])
    if current.returncode != 0 or current.stdout.strip() != staged_tree:
        return False
    if isinstance(before_index, str):
        stage_tree(repo, before_index)
    else:
        _give_index_back(before_index)
    return True


def _give_index_back(shape: _IndexShape) -> None:
    """Make the index what ``shape`` recorded: its tree, its flags and its intent-to-add entries.

    A path ``shape`` lists that the index no longer holds once its tree is back was an
    intent-to-add entry (no tree records those), which the staging replaced. Git has no plumbing
    that writes such an entry, so the path is entered as an empty file and ``git reset -N`` turns
    it into one. Git records the regular file mode for it until the path is added.
    """

    if shape.index_path is not None:
        # Git's index cache/stat bytes are part of what the caller gave us;
        # reconstructing entries alone loses them after a hook rewrites a file.
        lock = shape.index_path.with_name(shape.index_path.name + ".lock")
        handle = lock.open("xb")  # Never acquire or remove another actor's lock.
        try:
            with handle:
                if shape.index_bytes is not None:
                    handle.write(shape.index_bytes)
            if shape.index_bytes is None:
                shape.index_path.unlink(missing_ok=True)
            else:
                lock.chmod(shape.index_mode)
                if shape.index_times is not None:
                    # Preserve Git's racy-clean boundary, not only index bytes.
                    os.utime(lock, ns=shape.index_times)
                lock.replace(shape.index_path)
        finally:
            lock.unlink(missing_ok=True)
        return
    _retarget_index(shape, shape.tree)
    listed = set(_nul_records(_nul_git(shape.top, ["ls-files", "-z"])))
    intended = sorted(shape.tags.keys() - listed)
    if intended:
        empty = require_git(shape.top, ["hash-object", "-w", "--stdin"])
        entries = [f"100644 {empty}\t{path}" for path in intended]
        _index_edit(shape.top, ["update-index", "-z", "--index-info"], entries)
        reset = ["--literal-pathspecs", "reset", "-q", "-N", shape.tree]
        _index_edit(shape.top, [*reset, "--pathspec-from-file=-", "--pathspec-file-nul"], intended)
        _set_index_flags(shape, intended)


def commit_if_dirty(repo: Path, message: str, *, exclude_paths: tuple[str, ...] = ()) -> str:
    if not has_changes(repo, exclude_paths=exclude_paths):
        return head_commit(repo)
    stage_worktree_content(repo, exclude_paths=exclude_paths)
    if exclude_paths:
        require_git(repo, ["update-index", "--force-remove", "--", *exclude_paths])
    require_git(repo, ["commit", "-m", message])
    return head_commit(repo)


def run_pre_commit_hook_if_configured(repo: Path) -> bool:
    """Run the checkout's configured pre-commit hook, or report that none exists."""
    hook_path = Path(require_git(repo, ["rev-parse", "--git-path", "hooks/pre-commit"]))
    if not hook_path.is_absolute():
        hook_path = repo / hook_path
    if not hook_path.is_file():
        return False
    require_git(repo, ["hook", "run", "pre-commit"])
    return True


try:  # POSIX advisory locks; where they do not exist publications are not serialized
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    _fcntl = None  # type: ignore[assignment]

_PUBLICATION_LOCK_SECONDS = 30.0
_UNFINISHED_ACTIONS = (
    ("MERGE_HEAD", "a merge"),
    ("CHERRY_PICK_HEAD", "a cherry-pick"),
    ("REVERT_HEAD", "a revert"),
    ("rebase-merge", "a rebase"),
    ("rebase-apply", "a rebase or an am session"),
)


@contextmanager
def _publication_lock(repo: Path) -> Iterator[None]:
    """Serialize publications that share one worktree index (the lock is per worktree git dir).

    Only "would block" means that another publication holds the lock, and only that is waited
    for. Any other error of the lock file (a file system without locks, a file that cannot be
    opened) is refused at once and named, because waiting does not cure it.
    """

    if _fcntl is None:
        yield
        return
    path = Path(require_git(repo, ["rev-parse", "--absolute-git-dir"])) / "ar-publication.lock"
    try:
        handle = path.open("a")
    except OSError as error:
        raise _lock_refusal(path, error) from error
    with handle:
        deadline = time.monotonic() + _PUBLICATION_LOCK_SECONDS
        while True:
            try:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                break
            except BlockingIOError as busy:
                if time.monotonic() > deadline:
                    raise CommitPublicationRefusal(
                        "another publication in this worktree did not finish within "
                        f"{_PUBLICATION_LOCK_SECONDS:.0f} seconds, so nothing was published; "
                        "rerun the closeout"
                    ) from busy
                time.sleep(0.05)
            except OSError as error:
                raise _lock_refusal(path, error) from error
        try:
            yield
        finally:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)


def _lock_refusal(path: Path, error: OSError) -> CommitPublicationRefusal:
    return CommitPublicationRefusal(
        f"the publication lock {path} cannot be taken ({error}), so nothing was published; "
        "repair the lock file or its file system and rerun the closeout"
    )


def _refuse_unfinished_action(repo: Path) -> None:
    """A commit made while a merge, pick, revert or rebase is unfinished would break that action."""

    for name, action in _UNFINISHED_ACTIONS:
        marker = Path(
            require_git(repo, ["rev-parse", "--path-format=absolute", "--git-path", name])
        )
        if marker.exists():
            raise CommitPublicationRefusal(
                f"{marker.name} exists in {marker.parent}: {action} is unfinished in this "
                "worktree, so nothing was published; finish or abort it and rerun the closeout"
            )


def _write_commit(repo: Path, message: str, tree: str, parent: str) -> tuple[str, str]:
    """The commit object (not yet on any ref) and the reflog subject."""

    signing = run_git(repo, ["config", "--type=bool", "--get", "commit.gpgsign"])
    sign = ["-S"] if signing.stdout.strip() == "true" else []
    written = run_git(
        repo,
        ["commit-tree", tree, "-p", parent, *sign],
        GitRunnerOptions(input_text=message),
    )
    commit = written.stdout.strip()
    if written.returncode != 0 or not commit:
        raise RuntimeError(_transport_safe_git_diagnostic(written.stderr.strip()))
    subject = next((line.strip() for line in message.splitlines() if line.strip()), "")
    return commit, subject


def _move_admitted_ref(repo: Path, commit: str, subject: str, before: GitMutationSnapshot) -> None:
    """Publish ``commit`` with one expected-old move of the admitted branch.

    Guarantees. The branch moves only from the admitted tip (Git's compare-and-swap), so no
    other writer's commit is ever overwritten. ``HEAD`` is checked before the move and again after
    it. Git's ``update-ref --stdin`` can verify a symbolic ref (``symref-verify``, tested on Git
    2.54), but not together with an update of the branch ``HEAD`` points at: it refuses the pair as
    "multiple updates for 'HEAD'". So the check after the move is the guarantee for a ``HEAD``
    switched between the first check and the move: the move is taken back with a compare-and-swap
    to the admitted tip, and the publication is refused. Between those two ref updates the branch
    briefly names the new commit; a reader in that window sees it, and no other writer can lose a
    commit to it.

    What a failed move says. Git refuses a move for more than one reason, so the branch is read
    again: "the branch moved" is said only when it names another commit now, and otherwise the
    refusal carries Git's own text (a refusing ``reference-transaction`` hook, a ref that cannot
    be locked). A take-back that Git refuses is reported with Git's text too, and whether the new
    commit is still on the branch is read from the branch, not assumed.
    """

    moved = run_git(
        repo,
        ref_compare_and_swap_args(before.headRef, commit, before.head, reason=f"commit: {subject}"),
    )
    if moved.returncode != 0:
        detail = _transport_safe_git_diagnostic(moved.stderr.strip())
        if _ref_commit(repo, before.headRef) != before.head:
            raise CommitPublicationRefusal(
                f"{before.headRef} moved while this closeout published, so nothing was published; "
                f"rerun the closeout ({detail})"
            )
        raise CommitPublicationRefusal(
            f"Git refused to move {before.headRef}, which is still at {before.head}, so nothing "
            f"was published: {detail}"
        )
    head_now = run_git(repo, ["symbolic-ref", "--quiet", "HEAD"]).stdout.strip()
    if head_now == before.headRef:
        return
    undone = run_git(
        repo,
        ref_compare_and_swap_args(
            before.headRef, before.head, commit, reason="commit: taken back, HEAD switched"
        ),
    )
    where = head_now or "no branch (it is detached)"
    if undone.returncode != 0:
        kept = "is still on" if is_ancestor(repo, commit, before.headRef) else "is no longer on"
        raise RuntimeError(
            f"HEAD switched to {where} while this closeout published, and the new commit {commit} "
            f"could not be taken back ({_transport_safe_git_diagnostic(undone.stderr.strip())}); "
            f"it {kept} {before.headRef}: check that branch, then rerun the closeout"
        )
    raise CommitPublicationRefusal(
        f"HEAD switched to {where} while this closeout published; the new commit was taken back "
        f"and {before.headRef} is at {before.head} again, so nothing was published; return to "
        f"{before.headRef} and rerun the closeout"
    )


@dataclass(frozen=True)
class PublicationHooks:
    """What a caller adds to the shared publication: how to stage, and a stricter condition.

    ``stage`` returns the tree it staged (needed when the tree is not known beforehand);
    ``confirm`` runs after staging and before the commit object is written, and whatever it
    raises leaves nothing published (direct landing: nothing changed since its judged snapshot).
    """

    stage: Callable[[], str] | None = None
    confirm: Callable[[], None] | None = None


def publish_tree_commit(
    repo: Path,
    message: str,
    *,
    tree: str | None,
    before: GitMutationSnapshot,
    hooks: PublicationHooks | None = None,
) -> str:
    """Stage ``tree`` and publish it as one commit on the branch ``before`` admitted, or neither.

    The message is settled first (:func:`_cleaned_message`), so an empty message refuses before
    anything is read. Then, under one per-worktree lock (so two publishers never interleave their
    index writes): refuse an unfinished merge, pick, revert or rebase, before the index is asked
    for its tree; refuse a ``HEAD`` that is not the admitted branch or a tip that moved; stage; run
    ``confirm``; write the commit object with ``git commit-tree`` from the staged tree and the
    admitted tip as its only parent; move the branch with the shared expected-old move
    (:func:`_move_admitted_ref`). Any refusal or failure after staging gives the index back as this
    call found it (only while it still holds exactly what this call staged), and no ref names the
    new commit.

    ``tree`` is the tree to publish; it is staged by :func:`stage_tree` unless ``hooks.stage`` is
    given, and ``hooks.stage`` must be given when ``tree`` is ``None``. The commit is the tree
    itself, so a working-tree or index edit made after the tree was judged cannot enter it; such a
    file stays an uncommitted change. The index keeps what no tree records, after a publication
    and after a refusal alike: the assume-unchanged and skip-worktree flags (so a sparse checkout
    shows no file outside its cone as deleted) and the intent-to-add entries that are not part of
    the commit.

    Compared with the porcelain commit this replaces: author, committer and ``commit.gpgsign``
    behave as before (``commit-tree`` ignores that setting, so ``-S`` is passed when it is true),
    and the message is the one ``git commit -m`` stores in the same configuration.

    Before the commit object is written, the repository's own pre-commit hook runs on the
    staged tree (MIK-R152): the hook Git would run for a commit in this worktree, started
    here with the index holding exactly the tree to be published. A failure, a change the
    hook's run made to the index or a tracked file, an unrunnable hook, or a run past the
    bound refuses with :class:`LandingCommitCheckRefusal`; the index is given back and no
    ref moves. A passed check is recorded on the commit itself as a ``Commit-Check-Pass``
    trailer (tree and hook), which is the fact the integration's master-line move reads
    (MIK-R152 rule 8); a repository without a hook lands as before.

    This function writes through ``commit-tree`` and runs the hook itself because no Git
    commit hook runs for a ``commit-tree`` object. The knowledge gate
    and validator have run before it is called. This describes the ordinary closeout and direct
    landing callers of this primitive. The ``reference-transaction`` hook
    still runs for this primitive's ref transaction, and its refusal retains Git's own text.
    """

    hooks = hooks or PublicationHooks()
    stage, confirm = hooks.stage, hooks.confirm
    cleaned = _cleaned_message(repo, message)
    if tree is None and stage is None:
        raise ValueError("publish_tree_commit needs a tree or a stage function")
    with _publication_lock(repo):
        _refuse_unfinished_action(repo)
        head_ref = run_git(repo, ["symbolic-ref", "--quiet", "HEAD"]).stdout.strip()
        if head_ref != before.headRef:
            raise CommitPublicationRefusal(
                f"HEAD names {head_ref or 'no branch (it is detached)'}, not the branch "
                f"{before.headRef} this closeout admitted, so nothing was published; return to "
                f"{before.headRef} and rerun the closeout"
            )
        tip = _ref_commit(repo, before.headRef)
        if tip != before.head:
            raise CommitPublicationRefusal(
                f"{before.headRef} moved from {before.head} to {tip or 'nothing'} after this "
                "closeout admitted it, so nothing was published; rerun the closeout"
            )
        if tree is not None and tree == before.headTree:
            return before.head
        found = _index_shape(repo)
        staged: str | None = None
        try:
            staged = (stage or _read_tree_stage(repo, tree))()
            if staged == before.headTree:
                unstage_bound_tree(repo, staged, found)
                return before.head
            if confirm is not None:
                confirm()
            from agents_remember.worktrees.modules.landing_commit_check import (  # noqa: PLC0415
                append_commit_check_trailer,
                run_landing_commit_check,
            )

            def _give_staged_back() -> None:
                # Forced, not conditional: the hook's run is what changed the index, and
                # MIK-R152 rule 4 gives it back as this call found it. The publication
                # lock serializes product actors; a human race keeps its worktree files.
                _give_index_back(found)

            checked = run_landing_commit_check(repo, tree=staged, restore_index=_give_staged_back)
            _refuse_unadmitted_index(repo, staged, checked=checked, give_back=_give_staged_back)
            commit, subject = _write_commit(
                repo,
                append_commit_check_trailer(cleaned, staged, checked.hook),
                staged,
                before.head,
            )
            _move_admitted_ref(repo, commit, subject, before)
            return commit
        except BaseException:
            if staged is not None:
                unstage_bound_tree(repo, staged, found)
            else:  # staging itself failed part-way: this call holds the lock, so put the index back
                with suppress(RuntimeError):
                    _give_index_back(found)
            raise


def _refuse_unadmitted_index(
    repo: Path, staged: str, *, checked: CommitCheckPass, give_back: Callable[[], None]
) -> None:
    """Restore the ordinary publication index when the checked state moved."""
    from agents_remember.worktrees.modules.landing_commit_check import (  # noqa: PLC0415
        LandingCommitCheckRefusal,
    )

    try:
        require_checked_publication_tree(repo, staged, checked=checked)
    except LandingCommitCheckRefusal:
        give_back()
        raise


def require_checked_publication_tree(repo: Path, staged: str, *, checked: CommitCheckPass) -> None:
    """Refuse where tracked state moved after the check passed (MIK-R152 rule 3).

    The commit object is written from the admitted tree hash, so hook bytes can
    never enter it; but a tracked edit between the pass and the commit — staged
    or unstaged — means the judged state is gone, and the landing refuses with
    the index given back instead of publishing. The index tree alone cannot see
    an unstaged rewrite, so the admitted worktree bytes are rechecked too.
    """

    from agents_remember.worktrees.modules.landing_commit_check import (  # noqa: PLC0415
        CheckedTree,
        LandingCommitCheckRefusal,
        TreeSnapshot,
        _changed_paths,
        _diff_head_raw,
        worktree_blob_map,
    )

    admitted = checked.admitted
    restaged = run_git(repo, ["write-tree"]).stdout.strip()
    if admitted is None:
        if restaged == staged:
            return
        changed: tuple[str, ...] = ("the index",)
    else:
        current = TreeSnapshot(
            index_tree=restaged,
            diff_head=_diff_head_raw(repo),
            blobs=worktree_blob_map(repo),
        )
        if current == admitted:
            return
        changed = tuple(_changed_paths(admitted, current))
    raise LandingCommitCheckRefusal(
        "changed",
        checked=CheckedTree(repo=repo, tree=staged, hook=checked.hook),
        step=None,
        log=checked.log,
        changed_paths=changed,
    )


def _ref_commit(repo: Path, ref: str) -> str:
    """The commit ``ref`` names now, or nothing when it names none."""

    return run_git(repo, ["rev-parse", "--verify", f"{ref}^{{commit}}"]).stdout.strip()


def _read_tree_stage(repo: Path, tree: str | None) -> Callable[[], str]:
    def stage() -> str:
        assert tree is not None
        stage_tree(repo, tree)
        return tree

    return stage


def commit_date(repo: Path, commit: str) -> str:
    return require_git(repo, ["show", "-s", "--format=%cI", commit])


def longest_tracked_path_length(repo: Path, ref: str = "HEAD") -> int:
    """Length of the longest tracked relative path at ref (HEAD fallback)."""
    result = run_git(repo, ["ls-tree", "-r", "--name-only", ref])
    if result.returncode != 0 and ref != "HEAD":
        result = run_git(repo, ["ls-tree", "-r", "--name-only", "HEAD"])
    if result.returncode != 0:
        return 0
    return max(
        (len(line.strip()) for line in result.stdout.splitlines() if line.strip()), default=0
    )


def commit_text_or_none(repo: Path, ref: str, relative_path: str) -> str | None:
    """Text of relative_path at ref in the repo, or None when absent at that ref."""
    result = run_git(repo, ["show", f"{ref}:{relative_path}"])
    return result.stdout if result.returncode == 0 else None


def _nul_git(repo: Path, args: list[str]) -> str:
    """Git output read verbatim, for a caller that parses a NUL-delimited interface.

    :func:`require_git` answers with the *stripped* text, which is the right shape for the
    single-value reads it serves and the wrong one here: a space is part of a path, and the only
    thing separating one record from the next is the NUL after it. The NUL interface is this
    repository's house rule for path enumeration -- ``-z`` is used across ``memory_quality/``,
    ``certification/`` and the worktree mutation, sync and terminal-validation reads -- and this
    family was the exception.
    """

    result = run_git(repo, args)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git {' '.join(args)} failed"
        raise RuntimeError(_transport_safe_git_diagnostic(detail))
    return result.stdout


def changed_worktree_paths(repo: Path) -> list[str]:
    """Every changed path of the working tree -- tracked and untracked -- as Git reports it.

    Both halves are read NUL-delimited. Read as lines instead, Git quotes a name containing a tab,
    a newline or a non-ASCII byte, and the trailing backslash-to-slash normalisation this function
    used to apply then rewrote the *escape's* backslash into a separator -- so the address returned
    named a path no file held and the ``is_file`` guard below dropped the real file from the answer
    with nothing said about it. ``is_file`` remains the deliberate filter (a submodule, a directory
    entry, or a path a commit deleted is not a path this worklist carries); what it can no longer
    do is drop a file whose name Git would quote.
    """

    tracked = _nul_records(_nul_git(repo, ["diff", "--name-only", "-z", "HEAD", "--"]))
    untracked = _nul_records(_nul_git(repo, ["ls-files", "--others", "--exclude-standard", "-z"]))
    return sorted({path for path in [*tracked, *untracked] if filesystem.is_file(repo / path)})


def _diff_paths(repo: Path, from_commit: str) -> set[str]:
    """Every path of the ``from_commit..HEAD`` tree diff, exactly as Git reports it."""

    records = _nul_records(
        _nul_git(repo, ["diff", "--name-only", "-z", f"{from_commit}..HEAD", "--"])
    )
    return set(records)


def committed_changed_paths(repo: Path, base_commit: str, verified_commit: str) -> list[str]:
    """Paths changed by commits on the work branch that closeout has not yet verified.

    Tree-diff against the recorded base, intersected with the tree-diff against
    the last verified commit when one exists: content the synced source branch
    already carries and content a previous closeout already verified both drop
    out of the worklist. Every path is reported exactly as Git reports it, so a
    name holding a tab or a newline is the address of the file it names rather
    than a rewritten one the guard below would then discard.
    """
    changed = _diff_paths(repo, base_commit)
    if verified_commit and verified_commit != base_commit:
        changed &= _diff_paths(repo, verified_commit)
    return sorted(path for path in changed if filesystem.is_file(repo / path))
