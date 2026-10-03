from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from agents_remember.kernel import filesystem
from agents_remember.kernel.git_command import copy_git_index, run_git, run_git_with_index
from agents_remember.kernel.memory_ledger import LEDGER_RELATIVE_PATH, MEMORY_CACHE_EXCLUDE
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


def stage_tree(repo: Path, tree: str) -> None:
    """Make the index exactly ``tree``, reading nothing from the working tree.

    A commit staged this way records ``tree`` whatever was written to the working tree since the
    tree was read from it; such a file stays an uncommitted change. The index keeps no stat data,
    so Git's next comparison with the working tree reads every file once.
    """

    require_git(repo, ["read-tree", tree])


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


def commit_verified_staged(repo: Path, message: str, *, exclude_paths: tuple[str, ...] = ()) -> str:
    """Commit exactly the staged tree without invoking repository hooks.

    The caller has already staged and verified the intended index. In particular, this
    helper must neither restage the working tree nor rerun a hook after the caller's
    transaction or quality preparation.
    """
    if exclude_paths:
        require_git(repo, ["update-index", "--force-remove", "--", *exclude_paths])
    diff_args = ["diff", "--cached", "--quiet"]
    if exclude_paths:
        diff_args.extend(["--", ".", *map(_excluded_pathspec, exclude_paths)])
    if run_git(repo, diff_args).returncode == 0:
        return head_commit(repo)
    require_git(repo, ["commit", "--no-verify", "-m", message])
    return head_commit(repo)


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


def head_text_or_none(repo: Path, relative_path: str) -> str | None:
    """Text of relative_path at the repo's HEAD, or None when absent at HEAD."""
    return commit_text_or_none(repo, "HEAD", relative_path)


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
