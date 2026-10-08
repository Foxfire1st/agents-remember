from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from agents_remember.kernel import filesystem
from agents_remember.kernel.git_command import (
    GitRunnerOptions,
    copy_git_index,
    ref_compare_and_swap_args,
    run_git,
    run_git_with_index,
)
from agents_remember.kernel.memory_ledger import LEDGER_RELATIVE_PATH, MEMORY_CACHE_EXCLUDE
from agents_remember.models.lifecycles.mutation_evidence import GitMutationSnapshot
from agents_remember.worktrees.worktree_contract import WorktreeContract

# This module used to define its own `run_git` -- the kernel's function with the
# environment guard, the timeout and the explicit encoding all dropped -- and every
# destructive worktree operation (commit, merge --ff-only, reset --hard, rebase,
# branch -f, branch -D, worktree remove --force, push origin --delete) ran through
# it. With GIT_DIR exported those landed in whatever repository GIT_DIR named. The
# helpers below now call the one guarded runner; nothing else about them changed.


def _transport_safe_git_diagnostic(text: str) -> str:
    """Render surrogateescaped Git bytes without leaking invalid Unicode to MCP."""

    return text.encode("utf-8", errors="backslashreplace").decode("utf-8")


def require_git(repo: Path, args: list[str]) -> str:
    result = run_git(repo, args)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git {' '.join(args)} failed"
        raise RuntimeError(_transport_safe_git_diagnostic(detail))
    return result.stdout.strip()


def _excluded_pathspec(path: str) -> str:
    return MEMORY_CACHE_EXCLUDE if path == LEDGER_RELATIVE_PATH else f":(top,exclude){path}"


class _IndexCopyUnusable(Exception):
    """The capture from a copy of the worktree's index cannot answer; the full capture does."""


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
    the files Git cannot prove unchanged (:func:`_tree_from_index_copy`). When that copy cannot be
    made or Git reports an error on the way, the capture is taken again from an empty index, which
    hashes every file (:func:`_tree_from_head`): slower, the same tree, never a guess.
    """
    # The caller selects a scratch namespace; every observation owns its physical index.
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f".{index_path.name}-", dir=index_path.parent) as temporary:
        scratch = Path(temporary)
        try:
            return _tree_from_index_copy(repo, scratch / "index", exclude_paths)
        except _IndexCopyUnusable:
            return _tree_from_head(repo, scratch / "full-index", exclude_paths)


def _tree_from_index_copy(repo: Path, index: Path, exclude_paths: tuple[str, ...]) -> str:
    """Capture through a private copy of the worktree's own index, trusting Git's own evidence.

    The copy is brought to exactly the state the full capture starts from -- ``HEAD``'s entries and
    nothing else -- before any file is added, so both captures add the same files to the same
    entries and return the same tree for every worktree state:

    * **The copy keeps the index file's time** (:func:`copy_git_index`). Git re-checks by content
      every entry recorded in the second the index was written, and it recognises those entries by
      the index file's own time; a plain copy would lose that and keep the old blob of a same-size
      rewrite made in that second.
    * **A one-way merge of ``HEAD`` into the copy** (``read-tree -m -i HEAD``) leaves ``HEAD``'s
      entries: an entry whose path and content equal ``HEAD``'s keeps the file times the index
      recorded, and every other entry -- a staged change, a staged addition, a removal -- becomes
      ``HEAD``'s entry with no file times, or is dropped. A staged path that is ignored and not in
      ``HEAD`` is therefore not in the tree, exactly as in the full capture. ``-i`` keeps the merge
      from consulting the working tree, which the capture reads only through ``git add``.
    * **No entry is trusted over its file.** ``assume-unchanged`` and ``skip-worktree`` tell
      ``git add`` to keep the index's blob whatever the file holds; a kept entry carries them over
      from the real index, so both are cleared on the copy. The real index keeps them.

    Whatever is not proven unchanged by the recorded file times is hashed by ``git add``, as in the
    full capture. An index whose file times are stale costs what the full capture costs.

    **What trusting Git's evidence leaves out.** A file whose recorded times still match is not
    read, so a conversion rule that changed after the file was recorded -- a ``.gitattributes``
    line, ``core.autocrlf``, a filter -- is not applied to it again. That is what ``git add -A``
    does on the worktree's own index, and therefore what a commit made from that index records;
    the full capture converted such a file again and so named a tree the index does not stage.
    Once the file itself is touched, both captures read and convert it.
    """

    real_index, top = _real_index(repo)
    try:
        copy_git_index(real_index, index)
    except OSError as error:
        raise _IndexCopyUnusable(f"the worktree's index cannot be copied: {error}") from error
    _index_step(repo, index, ["read-tree", "-m", "-i", "HEAD"])
    _clear_trust_flags(top, index)
    if exclude_paths:
        _index_step(repo, index, ["update-index", "--force-remove", "--", *exclude_paths])
    _index_step(repo, index, _add_all_args(exclude_paths))
    return _index_step(repo, index, ["write-tree"]).strip()


def _real_index(repo: Path) -> tuple[Path, Path]:
    """The worktree's own index file and its top-level directory.

    The index file is only ever read, as bytes, to be copied: no Git command of the capture reads
    it, so none can refresh it or leave a lock beside it.
    """

    located = run_git(
        repo, ["rev-parse", "--path-format=absolute", "--git-path", "index", "--show-toplevel"]
    )
    lines = located.stdout.splitlines()
    if located.returncode != 0 or len(lines) != 2 or not all(lines):
        raise _IndexCopyUnusable(f"the worktree's index cannot be located: {located.stderr}")
    return Path(lines[0]), Path(lines[1])


def _index_step(repo: Path, index: Path, args: list[str], *, paths: str | None = None) -> str:
    """One Git command against the private copy; a failure hands the capture to the full one."""

    result = run_git_with_index(repo, args, index, input_text=paths)
    if result.returncode != 0:
        raise _IndexCopyUnusable(f"git {args[0]} failed on the index copy: {result.stderr.strip()}")
    return result.stdout


def _clear_trust_flags(top: Path, index: Path) -> None:
    """Clear ``assume-unchanged`` and ``skip-worktree`` on every entry of the private copy.

    It runs in the worktree's top-level directory: ``ls-files`` lists only the entries under the
    directory it runs in, and ``git add -A`` updates the whole worktree wherever it runs.
    """

    listing = _index_step(top, index, ["ls-files", "-v", "-z"])
    flagged = [
        row[2:]
        for row in listing.split("\0")
        if len(row) > 2 and (row[0].islower() or row[0] == "S")
    ]
    if not flagged:
        return
    paths = "".join(f"{path}\0" for path in flagged)
    # One flag per command: ``update-index`` applies only the last of several such options.
    for option in ("--no-assume-unchanged", "--no-skip-worktree"):
        _index_step(top, index, ["update-index", "-z", option, "--stdin"], paths=paths)


def _add_all_args(exclude_paths: tuple[str, ...]) -> list[str]:
    args = ["add", "-A"]
    if exclude_paths:
        args.extend(["--", ".", *map(_excluded_pathspec, exclude_paths)])
    return args


def _tree_from_head(repo: Path, isolated_index: Path, exclude_paths: tuple[str, ...]) -> str:
    """The full capture: an empty private index seeded from ``HEAD``, so every file is hashed."""

    actions = [("seed candidate index", ["read-tree", "HEAD"])]
    if exclude_paths:
        actions.append(
            ("exclude derived files", ["update-index", "--force-remove", "--", *exclude_paths])
        )
    actions.append(("materialize candidate tree", _add_all_args(exclude_paths)))
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
    return result.stdout.strip()


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
        args.extend(["--", ".", *map(_excluded_pathspec, exclude_paths)])
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
    return run_git(repo, ["merge-base", "--is-ancestor", ancestor, descendant]).returncode == 0


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


def stage_worktree_content(repo: Path, *, exclude_paths: tuple[str, ...] = ()) -> None:
    """Stage real content without reading or retaining explicitly derived index entries."""
    args = ["add", "-A"]
    if exclude_paths:
        require_git(repo, ["update-index", "--force-remove", "--", *exclude_paths])
        args.extend(["--", ".", *map(_excluded_pathspec, exclude_paths)])
    require_git(repo, args)


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


def _index_shape(repo: Path) -> _IndexShape:
    """Read the index's shape. It runs in the top-level directory, where ``ls-files`` lists all."""

    top = Path(require_git(repo, ["rev-parse", "--show-toplevel"]))
    listing = _nul_records(_nul_git(top, ["ls-files", "-v", "-z"]))
    return _IndexShape(
        top, require_git(top, ["write-tree"]), {row[2:]: row[0] for row in listing if len(row) > 2}
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


class CommitPublicationRefusal(RuntimeError):
    """The commit was not published: the admitted branch, its tip or the worktree state differs."""


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


_CLEANUP_MODES = ("default", "whitespace", "scissors", "strip", "verbatim")
_SCISSORS = " ------------------------ >8 ------------------------\n"
_AUTO_COMMENT_CANDIDATES = "#;@!$%^&|:"
_AS_GIT_COMMIT = "so nothing was published (git commit refuses it too)"


def _cleaned_message(repo: Path, message: str) -> str:
    """The message as ``git commit -m`` stores it, or a refusal where that command aborts.

    These are the steps of ``git commit -m`` in Git 2.54 (``builtin/commit.c``), and the tests
    compare the result with a real ``git commit -m`` for every mode and comment setting. The
    message ends with a newline. Unless ``commit.cleanup`` is ``verbatim`` its whitespace is
    cleaned (:func:`_stripspace`); without an editor ``default`` and ``scissors`` do no more than
    ``whitespace``. A true ``commit.verbose`` cuts the message at the scissors line. ``strip``
    drops the lines that begin with the comment string: ``core.commentChar`` or
    ``core.commentString``, whichever is set last, and for ``auto`` the character Git selects
    (:func:`_auto_comment_character`).

    Git aborts the commit for an invalid mode, for a message that is empty after these steps (only
    blank and ``Signed-off-by`` lines; under ``verbatim`` only a message of no bytes) and for an
    ``auto`` with no free character. Each of them is refused here, before anything is touched.
    """

    settings = _message_settings(repo)
    mode = settings.get("cleanup", "default")
    if mode not in _CLEANUP_MODES:
        raise CommitPublicationRefusal(
            f"Invalid cleanup mode {mode} (commit.cleanup), {_AS_GIT_COMMIT}"
        )
    cleaning = mode != "verbatim"
    text = message if not message or message.endswith("\n") else f"{message}\n"
    if cleaning:
        text = _stripspace(text)
    comment = settings.get("comment", "#")
    if comment.lower() == "auto":
        comment = _auto_comment_character(text)
    if _commit_verbose(repo):
        text = text[: _scissors_start(text, comment)]
    if cleaning:
        text = _stripspace(text, comment if mode == "strip" else None)
    lines = text.split("\n")
    if not text or (
        cleaning and all(line.startswith("Signed-off-by: ") or not line for line in lines)
    ):
        raise CommitPublicationRefusal(f"the commit message is empty, {_AS_GIT_COMMIT}")
    return text


def _message_settings(repo: Path) -> dict[str, str]:
    """``commit.cleanup`` and the comment string as Git reads them: the value set last wins."""

    pattern = r"^(commit\.cleanup|core\.comment(char|string))$"
    found = run_git(repo, ["config", "-z", "--get-regexp", pattern])
    settings: dict[str, str] = {}
    for entry in _nul_records(found.stdout):
        key, _, value = entry.partition("\n")
        settings["cleanup" if key == "commit.cleanup" else "comment"] = value
    return settings


def _commit_verbose(repo: Path) -> bool:
    """Whether ``commit.verbose`` is on, which makes ``git commit`` cut at the scissors line."""

    found = run_git(repo, ["config", "--type=bool-or-int", "--get", "commit.verbose"])
    if found.returncode not in (0, 1):
        detail = _transport_safe_git_diagnostic(found.stderr.strip())
        raise CommitPublicationRefusal(f"{detail}, {_AS_GIT_COMMIT}")
    value = found.stdout.strip()
    return value == "true" or (value.isdigit() and int(value) > 0)


def _stripspace(text: str, comment: str | None = None) -> str:
    """Git's ``strbuf_stripspace``: the cleaning ``git commit`` and ``git stripspace`` share.

    Trailing whitespace leaves every line (Git counts space, tab, carriage return and newline),
    runs of empty lines become one, empty lines at both ends go, and the text ends with a newline.
    With ``comment`` the lines that begin with it are dropped. It is done here and not by
    ``git stripspace`` because the shared runner reads Git's output as text, which would turn a
    carriage return inside a line into a newline.
    """

    lines: list[str] = []
    gap = False
    for raw in text.removesuffix("\n").split("\n") if text else []:
        if comment is not None and raw.startswith(comment):
            continue
        line = raw.rstrip(" \t\r")
        if line and gap and lines:
            lines.append("")
        gap = not line
        if line:
            lines.append(line)
    return "".join(f"{line}\n" for line in lines)


def _scissors_start(text: str, comment: str) -> int:
    """Where Git cuts ``text`` at the scissors line of ``comment`` (``wt_status_locate_end``)."""

    cut = f"{comment}{_SCISSORS}"
    if text.startswith(cut):
        return 0
    found = text.find(f"\n{cut}")
    return len(text) if found < 0 else found + 1


def _auto_comment_character(text: str) -> str:
    """The character ``core.commentChar=auto`` selects for ``text`` (``adjust_comment_line_char``).

    ``#`` when the message holds none. Otherwise the first of Git's candidates that begins no line
    before the tail Git ignores (:func:`_ignored_tail_start`). Git aborts the commit when every
    candidate is in use.
    """

    if "#" not in text:
        return "#"
    end = _ignored_tail_start(text)
    used = {text[0]} | {text[at + 1] for at in range(end - 1) if text[at] in "\n\r"}
    free = [candidate for candidate in _AUTO_COMMENT_CANDIDATES if candidate not in used]
    if not free:
        raise CommitPublicationRefusal(
            "core.commentChar is auto and every character Git could select begins a line of the "
            f"commit message, {_AS_GIT_COMMIT}"
        )
    return free[0]


def _ignored_tail_start(text: str) -> int:
    """Where the tail begins that Git ignores when it selects (``ignored_log_message_bytes``).

    The tail is the trailing run of ``#`` lines and empty lines, with an old ``Conflicts:`` block
    and its tab-indented paths, and everything from a ``#`` scissors line on. The branches follow
    Git's function one for one, including that a run beginning at the first byte is not a tail.
    """

    end = _scissors_start(text, "#")
    tail, line, conflicts = 0, 0, False
    while line < end:
        if text.startswith("#", line, end) or text[line] == "\n":
            tail = tail or line
        elif text.startswith("Conflicts:\n", line):
            tail, conflicts = tail or line, True
        elif conflicts and text[line] == "\t":
            pass
        elif tail:
            tail, conflicts = 0, False
        line = text.find("\n", line) + 1 or len(text)
    return tail or end


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

    This function writes through ``commit-tree`` and runs no Git commit hook. The knowledge gate
    and validator have run before it is called. This describes the ordinary closeout and direct
    landing callers of this primitive, not the prepared closeout's independent policies. Prepared
    code commits use ``--no-verify``: ``pre-commit`` and ``commit-msg`` are skipped, while
    ``prepare-commit-msg`` and ``post-commit`` still run. Prepared memory commits use ordinary Git
    hook behavior. No policy of those routes is changed here. The ``reference-transaction`` hook
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
            commit, subject = _write_commit(repo, cleaned, staged, before.head)
            _move_admitted_ref(repo, commit, subject, before)
            return commit
        except BaseException:
            if staged is not None:
                unstage_bound_tree(repo, staged, found)
            else:  # staging itself failed part-way: this call holds the lock, so put the index back
                with suppress(RuntimeError):
                    _give_index_back(found)
            raise


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


def _nul_records(raw: str) -> list[str]:
    """The NUL-delimited records of one ``-z`` read. No record of these commands is empty."""

    return [record for record in raw.split("\0") if record]


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


def _name_status_letters(repo: Path, rng: list[str]) -> dict[str, str]:
    """Each changed path's status letter, from one NUL-delimited ``--name-status`` read.

    ``-z`` is what makes the pairing unambiguous: a record is the status and then its path, and a
    rename or copy status is followed by **two** path fields -- the source and the destination. The
    line-oriented form cannot be split on tabs without also splitting a name that contains one, and
    reading it as lines is how Git's quoting of such a name reached a caller as a different path.
    The reported path is the destination, which is where the change lands. A record whose fields do
    not fit its own status is a refusal rather than a path dropped from the inventory in silence.
    """

    records = _nul_records(_nul_git(repo, ["diff", "--name-status", "-z", "--find-renames", *rng]))
    letters: dict[str, str] = {}
    cursor = 0
    while cursor < len(records):
        letter = records[cursor][:1]
        cursor += 1
        if letter in {"R", "C"}:
            if cursor + 1 >= len(records):
                raise RuntimeError(
                    "git reported a rename or copy without both of its paths, so no path's status "
                    "could be paired with it"
                )
            letters[records[cursor + 1]] = letter
            cursor += 2
            continue
        if cursor >= len(records):
            raise RuntimeError(
                "git reported a status without the path it belongs to, so the change inventory "
                "would have silently omitted that path"
            )
        letters[records[cursor]] = letter
        cursor += 1
    return letters


def _numstat_rows(repo: Path, rng: list[str]) -> list[tuple[str, int | None, int | None]]:
    """``(path, insertions, deletions)`` per file, from one NUL-delimited ``--numstat`` read.

    A record is ``insertions<TAB>deletions<TAB>path``; for a rename or a copy the path field is
    **empty** and the source and the destination follow as two further records, so the pairing is
    positional. That is why a name containing a tab needs no re-splitting here and no ``a => b``
    reconstruction, and why the reported path is the destination. Binary files report ``-`` for
    both counts and keep it as ``None`` rather than as a measured zero.
    """

    records = _nul_records(_nul_git(repo, ["diff", "--numstat", "-z", "--find-renames", *rng]))
    rows: list[tuple[str, int | None, int | None]] = []
    cursor = 0
    while cursor < len(records):
        fields = records[cursor].split("\t", 2)
        cursor += 1
        if len(fields) != 3:
            raise RuntimeError(
                f"git's numstat record {fields!r} is not insertions, deletions and one path"
            )
        insertions, deletions, path = fields
        if not path:
            if cursor + 1 >= len(records):
                raise RuntimeError(
                    "git reported a rename or copy without both of its paths, so its counts could "
                    "not be paired with a path"
                )
            path = records[cursor + 1]
            cursor += 2
        rows.append(
            (
                path,
                None if insertions == "-" else int(insertions),
                None if deletions == "-" else int(deletions),
            )
        )
    return rows


def changed_files_with_counts(
    repo: Path, base: str, head: str | None = None
) -> list[dict[str, Any]]:
    """Per-file change-set ``base``->``head`` (``head=None`` -> the working tree).

    Each entry is ``{path, insertions, deletions, status}``. Unlike
    :func:`changed_worktree_paths` / :func:`committed_changed_paths` this KEEPS
    deletions (status ``D``) and reports per-file insertion/deletion counts (``None``
    for binary files, whose numstat shows ``-``); in working-tree mode untracked files
    are reported as additions (status ``A``). ``status`` is the git letter
    (``A``/``M``/``D``/``R``/``C``).

    Both reads are NUL-delimited and nothing is rewritten afterwards, so ``path`` is the address
    of the file it names: a name holding a tab, a newline or a backslash is reported as itself
    rather than as a quoted or separator-substituted variant that resolves to nothing. Git's
    ``core.quotePath`` quoting never reaches a caller here, because ``-z`` is precisely the
    interface that turns it off. Records are sorted by path.
    """
    rng = [base] if head is None else [base, head]
    letters = _name_status_letters(repo, rng)
    out: list[dict[str, Any]] = [
        {
            "path": path,
            "insertions": insertions,
            "deletions": deletions,
            "status": letters.get(path, "M"),
        }
        for path, insertions, deletions in _numstat_rows(repo, rng)
    ]
    if head is None:
        for rel in _nul_records(
            _nul_git(repo, ["ls-files", "--others", "--exclude-standard", "-z"])
        ):
            if filesystem.is_file(repo / rel):
                line_count = len((repo / rel).read_text(errors="replace").splitlines())
                out.append({"path": rel, "insertions": line_count, "deletions": 0, "status": "A"})
    return sorted(out, key=lambda entry: str(entry["path"]))
