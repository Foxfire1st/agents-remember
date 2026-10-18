"""The one git command runner for this package.

Every ``git`` subprocess goes through :func:`run_git`. It used to go through six
near-identical copies of this function, and the copies had drifted: only this one
stripped the repository-selector variables below, so with ``GIT_DIR`` exported the
kernel's ``git commit`` landed in the real repository while the worktree module's
``git commit`` -- the same function with ``env=`` omitted -- landed in whatever
repository ``GIT_DIR`` named. The runner is singular now so that guard cannot be
absent from one path and present in another.

``mcp/tests/test_git_command.py`` sets the selectors against a decoy repository and
proves the real one is untouched; ``mcp/tests/conftest.py`` also strips them at
import so fixtures are safe, but that guard is deliberately *not* what makes the
production paths safe -- the decoy test re-sets the variables inside its own scope
precisely so it cannot pass on the conftest's account.

"Singular" was true of twenty-one modules and false of one until L6. The benchmark
runner kept its own argv builder and its own spawn as a sanctioned exception, which
meant the guard on this rule had to carry a matching blind spot -- an argv composed by
a helper is invisible at the spawn -- and a sanctioned exception is still a second
owner. The builder is gone; ``work_dir`` and ``core.longpaths=true`` below are the two
things this runner had to grow to take its commands. What keeps the count at one now is
``mcp/test_support/agents_remember_test_support/code_quality/single_owner.py``, which reports the *construction* of a git argv
anywhere in the package rather than only a spawn it can see the argv of.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Literal, overload

from agents_remember.kernel.git_closeout_publication import (
    _CLOSEOUT_PUBLICATION_AUTHORITY,
    GitCloseoutPublicationBinding,
    GitCloseoutPublicationCapability,
    GitCloseoutPublicationError,
    GitCloseoutPublicationObservation,
    GitCloseoutPublicationResult,
)
from agents_remember.kernel.git_object_memo_keys import _memo_cat_file, _memo_rev_parse
from agents_remember.kernel.git_preparation import (
    _PREPARATION_AUTHORITY,
    ExistingGitPreparationBinding,
    GitPreparationCommandError,
    GitPreparationError,
    PreparationAction,
    PrivateGitPreparationBinding,
    PrivateGitPreparationCapability,
    PrivateGitPreparationObservation,
    require_git_object_id,
    require_physical_tree,
)

GIT_REPOSITORY_SELECTOR_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
)

# Timeout classes. One number cannot bound every git command, and picking one
# anyway is how a correctness fix becomes an outage: this runner was hard-coded to
# five seconds, which is a fine bound for `rev-parse` and an absurd one for
# `rebase`, `merge`, `worktree add` or `push --delete`, so consolidating onto it
# unchanged would have killed every integrate at five seconds. Both numbers name
# the slowest *legitimate* case in their band, so tripping one is always a stall
# and never healthy work.
#
# Local work gets the larger bound: a rebase or a status over a large tree can
# legitimately churn for minutes. Remote work gets the smaller one: bytes either
# move or the connection is wedged, and a wedged connection inside an MCP tool call
# has no cancellation path, so it must not be allowed to sit there for minutes.
# A third band for constant-time metadata reads -- `rev-parse`, `branch --show-current`,
# `ls-files` -- that sit on interactive paths. Measured on this repository they return in
# under a millisecond, so 30s is roughly thirty thousand times the observed cost and can
# only be reached when git is blocked on an index lock another process holds. Inheriting
# the local bound would let one wedged `rev-parse` hold an MCP tool call for five minutes,
# which is the wrong failure for a query that is either instant or stuck.
#
# A fourth band for bulk network work that is NOT inside a tool call. The benchmark runner
# clones and fetches whole third-party repositories as a foreground batch step, and the
# remote band's argument does not hold for it: 120s is sized for a wedged connection an MCP
# client cannot cancel, whereas this is a CLI step a developer can interrupt and a large
# repository legitimately takes longer than two minutes to clone. Those spawns had **no**
# timeout at all before they were routed here, so this is the first bound they have ever
# carried rather than a relaxation of an existing one.
GIT_LOCAL_TIMEOUT_SECONDS = 300
GIT_REMOTE_TIMEOUT_SECONDS = 120
GIT_METADATA_TIMEOUT_SECONDS = 30
GIT_BULK_REMOTE_TIMEOUT_SECONDS = 1800

# What a caller that parses ``git diff`` output passes, so the user's Git configuration cannot
# change the format it parses (MIK-R42): ``diff.noprefix``, ``diff.srcPrefix``/``diff.dstPrefix``
# and ``diff.mnemonicPrefix`` change the ``a/``/``b/`` of every file header, ``diff.external``
# replaces the output with a driver's, and forced colour (``color.ui``, ``color.diff``) wraps it in
# escape sequences. Command-line options win over every one of them.
DIFF_PREFIX_OPTIONS = ("--src-prefix=a/", "--dst-prefix=b/")
PARSED_DIFF_OPTIONS = ("--no-color", "--no-ext-diff", *DIFF_PREFIX_OPTIONS)


@dataclass(frozen=True)
class GitCommandPlan:
    """Exact runner input and complete argv for the caller's durable command intent."""

    cwd: Path
    args: tuple[str, ...]
    argv: tuple[str, ...]


@dataclass(frozen=True)
class _GitRun:
    work_dir: Path
    input_text: str | None
    timeout: float
    environment: dict[str, str]
    new_session: bool = False


@dataclass(frozen=True)
class GitRunnerOptions:
    """How one observed command is aimed: where it runs, what it reads, and who it is.

    These are the ways a caller tells a git command something its argv cannot say, and they
    arrived one at a time -- ``work_dir`` for ``git clone``, ``input_text`` for ``git patch-id``,
    ``identity`` for ``git commit-tree``. They are one object because they are one concept, and
    because the runner's own signature should stay at the two facts every call shares.
    """

    work_dir: Path | None = None
    input_text: str | None = None
    timeout: float = GIT_LOCAL_TIMEOUT_SECONDS
    identity: dict[str, str] | None = None
    new_session: bool = False
    object_inputs: Path | None = None
    """A second object store the command may read, never write (MIK-R73 rule 7).

    A working-tree capture stages its blobs outside the repository, because a read may not write
    into it. The captured tree is read back through Git's own ``GIT_ALTERNATE_OBJECT_DIRECTORIES``:
    one mechanism, read-only, no copy of the objects and no second observation path.
    """


@dataclass(frozen=True)
class IsolatedGitState:
    """Disposable Git state with the repository object database as read-only input."""

    index_path: Path
    object_directory: Path
    alternate_object_directory: Path


def git_environment() -> dict[str, str]:
    """Return the ambient environment without Git repository selectors.

    Every command through this kernel additionally runs with ``GIT_OPTIONAL_LOCKS=0`` (MIK-R74
    rule 6): a read must never take a lock the repository did not ask for, and no command here
    needs an optional one -- writers keep the locks their operation requires, which are never
    optional.
    """

    environment = os.environ.copy()
    for name in GIT_REPOSITORY_SELECTOR_ENV:
        environment.pop(name, None)
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    return environment


def run_git(
    repo_root: Path,
    args: list[str],
    options: GitRunnerOptions | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``git args`` against ``repo_root``, never against an inherited selector.

    ``options`` carries the three ways a caller can tell a git command something its argv
    cannot; the two positional facts are what every call has in common.

    ``options.input_text`` feeds git's stdin (``git patch-id`` is the only caller that needs
    it). Without it stdin is ``DEVNULL``: under the stdio MCP transport the parent's
    stdin IS the JSON-RPC request pipe, and a child holding or reading it wedges the
    tool call (GitHub #49).

    ``options.work_dir`` separates *where git runs* from *which repository the command is about*,
    which are the same directory for every caller but one. ``git clone <url> <dest>``
    cannot run inside ``<dest>``, because ``<dest>`` is what it is about to create, and
    ``cwd=`` a directory that does not exist raises before git is ever reached. The
    benchmark runner therefore hands the destination's parent here while ``repo_root``
    stays the repository being cloned, so ``safe.directory`` still names the right tree.
    It is passed as git's own ``-C`` as well as the child's ``cwd``: ``-C`` is the form
    the benchmark runner's argv already used on the most destructive commands in the
    package (``reset --hard``, ``clean -fdx``), and reproducing them as a bare ``cwd=``
    would have silently swapped the mechanism that aims them.

    BOTH PATHS MUST BE ABSOLUTE, and every caller passes absolute paths today: ``-C`` is
    resolved against the child's cwd, which this already sets to ``work``, so a relative
    ``work`` would select ``work/work``. Not fixed with ``Path.resolve`` because that also
    collapses symlinks, which would leave ``-C`` and ``safe.directory`` naming different
    trees on a symlinked worktree.

    ``core.longpaths=true`` was likewise carried by that argv and is now carried for
    every caller. It is what lets git check out a path past Windows' MAX_PATH; git
    ignores it everywhere else, so the cost off Windows is two argv words.

    ``options.identity`` adds environment names on top of the sanitized environment, and it
    exists for exactly one command: ``git commit-tree`` reads the author and committer and their
    timestamps from ``GIT_AUTHOR_*``/``GIT_COMMITTER_*`` and from nowhere else, so a history
    rewrite that must reproduce an existing commit byte for byte has no argv spelling for it. It
    is additive and never subtractive: the selector stripping runs first and this cannot put a
    selector back, because the names it accepts are checked against that same list.

    ``options.new_session`` starts the command alone in its process group, so a timeout stops
    the command with the children it started (MIK-R152 rule 6: a landing's commit hook runs
    inside ``git commit``, and killing git alone would orphan it). It exists for exactly one
    command family: the prepared closeout's commit, whose hook no outer runner watches.

    ``safe.directory`` names ``repo_root`` and not the ``*`` the benchmark runner used.
    That is narrower, not weaker: it is the exact tree every one of these commands
    operates on, and a wildcard additionally disarms the ownership check for any *other*
    repository the command happens to reach.
    """

    settings = options or GitRunnerOptions()
    return _run_git(repo_root, args, _git_run(settings, repo_root))


def run_git_bytes(
    repo_root: Path,
    args: list[str],
    options: GitRunnerOptions | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run ``git args`` and return Git's exact output bytes, never a decoded string.

    A read that compares Git's own bytes -- a ``-z`` pathname enumeration above all -- must not
    pass through the text transport: ``text=True`` decodes with universal newlines, so a pathname
    holding CR is rewritten to LF and two distinct pathnames become one. This form keeps every
    byte the child wrote, so a NUL-delimited record's path is the identity Git gave it.
    """

    settings = options or GitRunnerOptions()
    return _run_git(repo_root, args, _git_run(settings, repo_root), raw_output=True)


def object_input_environment(object_inputs: Path | None) -> dict[str, str]:
    """The environment that makes a read see objects held outside the repository."""

    if object_inputs is None:
        return {}
    return {"GIT_ALTERNATE_OBJECT_DIRECTORIES": object_inputs.as_posix()}


def _git_run(settings: GitRunnerOptions, repo_root: Path) -> _GitRun:
    """How one observed command runs: its sanitized environment, identity and aim."""

    environment = git_environment()
    for name, value in (settings.identity or {}).items():
        if name in GIT_REPOSITORY_SELECTOR_ENV:
            raise ValueError(f"{name} is a Git repository selector and cannot be set as identity")
        environment[name] = value
    environment.update(object_input_environment(settings.object_inputs))
    return _GitRun(
        work_dir=repo_root if settings.work_dir is None else settings.work_dir,
        input_text=settings.input_text,
        timeout=settings.timeout,
        environment=environment,
        new_session=settings.new_session,
    )


def run_git_with_index(
    repo_root: Path, args: list[str], index_path: Path, *, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run Git against one explicit isolated index after stripping ambient selectors.

    ``input_text`` feeds Git's stdin, for the commands that take their paths there
    (``update-index --stdin``); without it stdin is ``DEVNULL``, as for every other command.
    """
    environment = git_environment()
    environment["GIT_INDEX_FILE"] = index_path.as_posix()
    return _run_git(
        repo_root,
        args,
        _GitRun(
            work_dir=repo_root,
            input_text=input_text,
            timeout=GIT_LOCAL_TIMEOUT_SECONDS,
            environment=environment,
        ),
    )


def copy_git_index(source: Path, target: Path) -> None:
    """Copy a Git index for a disposable capture, keeping the index file's modification time.

    Git trusts an index entry's recorded stat data unless the file may have been rewritten in the
    second the index was written. Such an entry is "racily clean" and is compared by content, and
    Git recognises it by its recorded time not being older than the index *file's* own modification
    time. A plain copy is a new file with a new time: no entry is racily clean any more, and a
    same-size rewrite made in that second keeps its old blob in whatever ``git add`` then captures.
    The copy therefore carries the source's time, so Git's own check works on it exactly as on the
    repository's index. Bytes and time are read from one open file, so an index replaced meanwhile
    cannot pair one index's bytes with another's time.
    """

    with source.open("rb") as stream:
        stamp = os.fstat(stream.fileno())
        data = stream.read()
    target.write_bytes(data)
    os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))


def run_git_with_isolated_index_and_objects(
    repo_root: Path,
    args: list[str],
    *,
    state: IsolatedGitState,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Git against disposable index/object storage and a read-only real ODB."""

    state.object_directory.mkdir(parents=True, exist_ok=True)
    environment = git_environment()
    environment["GIT_INDEX_FILE"] = state.index_path.as_posix()
    environment["GIT_OBJECT_DIRECTORY"] = state.object_directory.as_posix()
    environment["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = state.alternate_object_directory.as_posix()
    return _run_git(
        repo_root,
        args,
        _GitRun(
            work_dir=repo_root,
            input_text=input_text,
            timeout=GIT_LOCAL_TIMEOUT_SECONDS,
            environment=environment,
        ),
    )


def _git_argv(repo_root: Path, args: list[str], work_dir: Path) -> list[str]:
    return [
        "git",
        "-C",
        work_dir.as_posix(),
        "-c",
        "core.longpaths=true",
        "-c",
        f"safe.directory={repo_root.as_posix()}",
        *args,
    ]


@overload
def _run_git(
    repo_root: Path, args: list[str], execution: _GitRun, *, raw_output: Literal[True]
) -> subprocess.CompletedProcess[bytes]: ...


@overload
def _run_git(
    repo_root: Path, args: list[str], execution: _GitRun, *, raw_output: Literal[False] = False
) -> subprocess.CompletedProcess[str]: ...


def _run_git(
    repo_root: Path, args: list[str], execution: _GitRun, *, raw_output: bool = False
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    memo = _OBJECT_LOOKUPS.get()
    key = _memo_key(repo_root, args, execution, raw_output=raw_output) if memo is not None else None
    if memo is not None and key is not None and key in memo:
        held = memo[key]
        assert isinstance(held, subprocess.CompletedProcess)
        return held  # type: ignore[return-value]
    stdin_input: str | bytes | None = execution.input_text
    if raw_output and stdin_input is not None:
        stdin_input = stdin_input.encode("utf-8")
    stdin_kwargs: dict[str, object] = (
        {"input": stdin_input} if stdin_input is not None else {"stdin": subprocess.DEVNULL}
    )
    argv = _git_argv(repo_root, args, execution.work_dir)
    cancellation = _GIT_CANCELLATION.get()
    if not execution.new_session and cancellation is None:
        result = subprocess.run(
            argv,
            cwd=execution.work_dir,
            text=not raw_output,
            encoding=None if raw_output else "utf-8",
            errors=None if raw_output else "surrogateescape",
            capture_output=True,
            env=execution.environment,
            timeout=execution.timeout,
            check=False,
            **stdin_kwargs,  # type: ignore[arg-type]
        )
    else:
        # A new session owns its process group, so a timeout -- or a client that
        # went away -- stops the command with the children it started. ``subprocess.run``
        # kills only the direct child; here the group goes first and the error still
        # reads as the timeout every caller already handles.
        process = subprocess.Popen(
            argv,
            cwd=execution.work_dir,
            text=not raw_output,
            encoding=None if raw_output else "utf-8",
            errors=None if raw_output else "surrogateescape",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL if stdin_input is None else subprocess.PIPE,
            env=execution.environment,
            start_new_session=True,
        )
        output, error_output = _wait_process(
            process, stdin_input, execution.timeout, cancellation, argv
        )
        code = process.returncode
        result = subprocess.CompletedProcess(argv, code, output, error_output)
    # A failure is never kept: a missing object created later in the request must
    # read fresh (F20). Only a successful immutable lookup is an immutable answer.
    if memo is not None and key is not None and result.returncode == 0:
        memo[key] = result
    return result


def _kill_process_group(process: subprocess.Popen) -> None:
    """End one Git child with the process group it started (the runner's only kill).

    ``_wait_process`` calls this for a timeout and for an abandoned read, so the
    runner has one child/process-group kill owner (L174-R1-08).
    """

    with suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(process.pid, signal.SIGKILL)


def _wait_process(
    process: subprocess.Popen,
    stdin_input: str | bytes | None,
    timeout: float | None,
    cancellation: Event | None,
    argv: list[str],
) -> tuple[Any, Any]:
    """Wait for one Git child, ending it on its timeout or when its reader abandoned it.

    Without a cancellation this is ``communicate`` with the caller's timeout. With one,
    the wait is polled: the process group is killed as soon as the reader goes away, and
    the child's own timeout still applies when it never does.
    """

    if cancellation is None:
        try:
            return process.communicate(
                input=stdin_input if stdin_input is not None else None, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            assert timeout is not None
            _kill_process_group(process)
            output, error_output = process.communicate()
            raise subprocess.TimeoutExpired(argv, timeout, output, error_output) from None
    deadline = None if timeout is None else time.monotonic() + timeout
    sent = False
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            assert timeout is not None
            _kill_process_group(process)
            output, error_output = process.communicate()
            raise subprocess.TimeoutExpired(argv, timeout, output, error_output)
        remaining = 0.05 if deadline is None else min(0.05, deadline - time.monotonic())
        try:
            return process.communicate(
                input=None if sent else stdin_input,
                timeout=remaining,
            )
        except subprocess.TimeoutExpired:
            sent = True
        if cancellation.is_set():
            _kill_process_group(process)
            return process.communicate()


def _read_git_bytes(root: Path, args: list[str], *, object_inputs: Path | None = None) -> bytes:
    result = _run_git(
        root,
        args,
        _GitRun(
            root,
            None,
            GIT_LOCAL_TIMEOUT_SECONDS,
            {**git_environment(), **object_input_environment(object_inputs)},
        ),
        raw_output=True,
    )
    if result.returncode:
        raise GitPreparationError(f"exact Git observation failed: {result.stderr!r}")
    return result.stdout


def read_git_configuration_bytes(root: Path) -> bytes:
    """Read exact effective config bytes with this runner's normal config/environment."""
    return _read_git_bytes(root, ["config", "--null", "--list"])


def read_git_commit_bytes(root: Path, commit: str) -> bytes:
    """Read one fully named commit's original bytes, without newline normalization."""
    require_git_object_id(commit)
    return _read_git_bytes(root, ["cat-file", "commit", commit])


def read_git_blob_bytes(root: Path, blob_id: str) -> bytes:
    """Read one fully named original blob without decoding or newline normalization."""
    require_git_object_id(blob_id)
    return _read_git_bytes(root, ["cat-file", "blob", blob_id])


_BLOB_READS: ContextVar[dict[tuple[str, str], bytes] | None] = ContextVar(
    "git_blob_reads", default=None
)


_OBJECT_LOOKUPS: ContextVar[
    dict[tuple[str, str, tuple[str, ...], tuple[tuple[str, str], ...], bool], object] | None
] = ContextVar("git_object_lookups", default=None)

# A read the client abandoned must not outlive its client (MIK-R176 rule 4). The
# serving layer sets one cancellation Event around an abandoned read's work; the
# runner then checks it while the child runs and stops the child with the process
# group it started. Outside such a block the runner behaves exactly as before.
_GIT_CANCELLATION: ContextVar[Event | None] = ContextVar("git_cancellation", default=None)


@contextmanager
def cancellable_git_commands(cancellation: Event) -> Iterator[None]:
    """Within the block, setting ``cancellation`` kills a running Git child and its group."""

    token = _GIT_CANCELLATION.set(cancellation)
    try:
        yield
    finally:
        _GIT_CANCELLATION.reset(token)


@contextmanager
def memo_git_object_lookups() -> Iterator[None]:
    """Within the block, the same Git lookup of the same object runs once (MIK-R81 rule 7).

    Only successful lookups whose answer the effective execution determines are kept: an
    object ID is the hash of its bytes, and the worktree's own index location is a pure
    function of the execution, so a repeated successful lookup can only return the same
    answer. A failed lookup is never kept — an object created later in the request reads
    fresh. Anything that names a ref, a worktree, the index or a session (``HEAD``,
    branches, ``for-each-ref``, ``status``, ``cat-file --batch``, ``update-ref``, ``add``,
    ``write-tree`` …) runs every time, exactly as without the block, and so does every
    form whose answer replace refs or ref movement can redirect: pretty-printed, typed,
    and peeled object reads (``cat-file -p``/``-t``/``<type>``, ``<sha>^{...}``,
    ``<sha>:<path>``), tree listings, revision walks, and merge-base (F20). The one
    exception is a peel behind a leading ``--no-replace-objects`` -- git's own global
    flag, which makes the read name the true object behind any replace ref, so those
    answers are immutable and collapse like any other content-addressed read. The cache lives and
    dies with the block and is keyed by the effective execution (repository, working
    directory, isolated index/object storage) and the exact argv, so two different stores
    or working directories never share an answer. Scopes nest: an inner scope inside an
    already-open one joins it, so one subject read keeps one lookup cache no matter how
    many wrapped ports it passes through (F17); the outermost exit still ends it.
    """

    if _OBJECT_LOOKUPS.get() is not None:
        yield
        return
    token = _OBJECT_LOOKUPS.set({})
    try:
        yield
    finally:
        _OBJECT_LOOKUPS.reset(token)


# The only flags a memoized argv may carry, per verb. Anything else runs fresh:
# an unlisted flag may change what the command asks about (F20).
_REV_PARSE_FLAGS = frozenset({"--verify", "--quiet"})
_CAT_FILE_FLAGS = frozenset({"-e"})
# One exact argv naming no object at all: locating the worktree's own index file and top-level
# directory is a pure function of the effective execution (repository, working directory,
# environment), so the same execution asks it once per request scope (F17). Anything with a
# different index file, object directory or working directory is a different key (F20).
_INDEX_LOCATION_ARGV = (
    "rev-parse",
    "--path-format=absolute",
    "--git-path",
    "index",
    "--show-toplevel",
)


def _flags_of(rest: list[str]) -> set[str] | None:
    """The dash-flags in ``rest``, or None when any entry is not a plain flag."""
    flags = {arg for arg in rest if arg.startswith("-")}
    if any(not arg.startswith("--") and len(arg) != 2 for arg in flags):
        return None
    return flags


def _memo_key(
    repo_root: Path, args: list[str], execution: _GitRun, *, raw_output: bool
) -> tuple[str, str, tuple[str, ...], tuple[tuple[str, str], ...], bool] | None:
    """The memo key for an immutable object lookup, else None (run it fresh)."""
    if not args or execution.input_text is not None:
        return None
    # A leading ``--no-replace-objects`` is git's own global flag (it must precede
    # the verb): the read names the true object behind any replace ref, so peels
    # spelled with it are immutable content-addressed answers (F17+F20).
    no_replace = args[0] == "--no-replace-objects"
    body = args[1:] if no_replace else args
    if not body:
        return None
    verb, rest = body[0], body[1:]
    positionals = [arg for arg in rest if not arg.startswith("-")]
    flags = _flags_of(rest)
    if flags is None:
        return None
    allowed = (
        (
            verb == "rev-parse"
            and flags <= _REV_PARSE_FLAGS
            and _memo_rev_parse(positionals, no_replace)
        )
        or (verb == "cat-file" and flags <= _CAT_FILE_FLAGS and _memo_cat_file(rest, no_replace))
        or (not no_replace and tuple(args) == _INDEX_LOCATION_ARGV)
    )
    if not allowed:
        return None
    environment = execution.environment
    return (
        repo_root.as_posix(),
        execution.work_dir.as_posix(),
        tuple(args),
        tuple(sorted(environment.items())),
        raw_output,
    )


@contextmanager
def shared_blob_reads() -> Iterator[None]:
    """Within the block, a blob read from a repository is not read from it a second time.

    A blob ID is the hash of its bytes, so a repeated read can only return the same bytes; the
    block just keeps the first answer. One computation that reads the same Git trees through more
    than one owner (the worklist's snapshots, its onboarding trace and its held base) pays the
    ``git cat-file`` for each blob once. The cache lives and dies with the block, and is keyed by
    repository too, so an object one repository lacks is still missing from it.
    """

    if _BLOB_READS.get() is not None:
        yield
        return
    token = _BLOB_READS.set({})
    try:
        yield
    finally:
        _BLOB_READS.reset(token)


def read_git_blobs_bytes(
    root: Path, blob_ids: Iterable[str], *, object_inputs: Path | None = None
) -> dict[str, bytes]:
    """Read many fully named blobs' original bytes through one ``git cat-file --batch``.

    The result maps each requested ID to its exact bytes (no decoding, no newline
    normalization). A missing object, or one that is not a blob, raises.
    """

    requested = sorted(set(blob_ids))
    for blob_id in requested:
        require_git_object_id(blob_id)
    if not requested:
        return {}
    if object_inputs is not None:
        # A capture's store lives only as long as its read, so its bytes are never pooled with
        # the repository's: the pool is keyed by repository root and blob id alone.
        return _batch_blobs(root, requested, object_inputs=object_inputs)
    shared = _BLOB_READS.get()
    held = {} if shared is None else _held_blobs(shared, root, requested)
    missing = [blob_id for blob_id in requested if blob_id not in held]
    blobs = _batch_blobs(root, missing) if missing else {}
    if shared is not None:
        shared.update({(root.as_posix(), blob_id): data for blob_id, data in blobs.items()})
    return {blob_id: held[blob_id] if blob_id in held else blobs[blob_id] for blob_id in requested}


def _held_blobs(
    shared: dict[tuple[str, str], bytes], root: Path, requested: list[str]
) -> dict[str, bytes]:
    return {
        blob_id: shared[(root.as_posix(), blob_id)]
        for blob_id in requested
        if (root.as_posix(), blob_id) in shared
    }


def _batch_blobs(
    root: Path, requested: list[str], *, object_inputs: Path | None = None
) -> dict[str, bytes]:
    result = _run_git(
        root,
        ["cat-file", "--batch"],
        _GitRun(
            root,
            "".join(f"{blob}\n" for blob in requested),
            GIT_LOCAL_TIMEOUT_SECONDS,
            {**git_environment(), **object_input_environment(object_inputs)},
        ),
        raw_output=True,
    )
    if result.returncode:
        raise GitPreparationError(f"exact Git blob observation failed: {result.stderr!r}")
    output = result.stdout
    blobs: dict[str, bytes] = {}
    offset = 0
    for blob_id in requested:
        header_end = output.index(b"\n", offset)
        header = output[offset:header_end].decode("ascii").split(" ")
        if len(header) != 3 or header[0] != blob_id or header[1] != "blob":
            raise GitPreparationError(f"{blob_id} is not a readable blob: {header!r}")
        start = header_end + 1
        end = start + int(header[2])
        blobs[blob_id] = output[start:end]
        offset = end + 1
    return blobs


def merge_file_bytes(
    root: Path, ours: Path, base: Path, theirs: Path, labels: tuple[str, str, str]
) -> tuple[bytes, int]:
    """Three-way merge three files' exact bytes with ``git merge-file -p``.

    Returns the merged bytes (conflict hunks carry ``labels`` for ours, base and theirs) and the
    number of conflicts. Nothing is written: the result is only printed. A Git failure raises.
    """
    result = _run_git(
        root,
        [
            "merge-file",
            "-p",
            "-L",
            labels[0],
            "-L",
            labels[1],
            "-L",
            labels[2],
            ours.as_posix(),
            base.as_posix(),
            theirs.as_posix(),
        ],
        _GitRun(root, None, GIT_LOCAL_TIMEOUT_SECONDS, git_environment()),
        raw_output=True,
    )
    if result.returncode < 0 or result.returncode > 127:
        raise GitPreparationError(f"git merge-file failed: {result.stderr!r}")
    return result.stdout, result.returncode


def read_git_tree_bytes(root: Path, tree: str, *, object_inputs: Path | None = None) -> bytes:
    """Read recursive NUL-delimited tree rows with original pathname bytes."""
    require_git_object_id(tree)
    return _read_git_bytes(root, ["ls-tree", "-r", "-z", tree], object_inputs=object_inputs)


def _preparation_output(root: Path, args: list[str]) -> str:
    result = run_git(root, args)
    if result.returncode:
        raise GitPreparationError(f"private Git observation failed: {result.stderr.strip()}")
    return result.stdout


def _require_logical_preparation_binding(binding: PrivateGitPreparationBinding) -> None:
    root = binding.logical_root
    _preparation_output(root, ["check-ref-format", binding.logical_ref])
    facts = (
        (["rev-parse", "--show-toplevel"], root.as_posix()),
        (
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
            binding.common_directory.as_posix(),
        ),
        (["symbolic-ref", "--quiet", "HEAD"], binding.logical_ref),
        (["rev-parse", "--verify", binding.logical_ref], binding.expected_logical_commit),
        (["rev-parse", "--verify", f"{binding.parent_commit}^{{commit}}"], binding.parent_commit),
        (["rev-parse", "--verify", f"{binding.admitted_tree}^{{tree}}"], binding.admitted_tree),
    )
    for args, expected in facts:
        if _preparation_output(root, args).strip() != expected:
            raise GitPreparationError("logical Git preparation binding changed")


def _preparation_tree_entries(root: Path, tree: str) -> dict[str, tuple[str, str]]:
    output = read_git_tree_bytes(root, tree)
    entries: dict[str, tuple[str, str]] = {}
    for row in output.split(b"\0"):
        if not row:
            continue
        metadata, path_bytes = row.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ")
        path = os.fsdecode(path_bytes)
        if kind != "blob" or mode not in ("100644", "100755", "120000"):
            raise GitPreparationError("preparation does not admit submodules or special sources")
        if (
            path in entries
            or Path(path).is_absolute()
            or any(part in ("", ".", "..", ".git") for part in path.split("/"))
        ):
            raise GitPreparationError("preparation tree source path is not canonical")
        entries[path] = (mode, object_id)
    return entries


def _require_preparation_index(
    root: Path,
    entries: dict[str, tuple[str, str]],
    *,
    allow_memory_cache: bool = False,
) -> None:
    if allow_memory_cache:
        entries = {path: value for path, value in entries.items() if path != "memory.md"}
    index = Path(
        _preparation_output(
            root, ["rev-parse", "--path-format=absolute", "--git-path", "index"]
        ).strip()
    )
    git_dir = Path(_preparation_output(root, ["rev-parse", "--absolute-git-dir"]).strip())
    if index != git_dir / "index" or index.resolve() != index or not index.is_file():
        raise GitPreparationError("preparation index is not its regular worktree-owned file")
    output = _read_git_bytes(root, ["ls-files", "--stage", "-z"])
    actual: dict[str, tuple[str, str]] = {}
    for row in output.split(b"\0"):
        if not row:
            continue
        metadata, path_bytes = row.split(b"\t", 1)
        path = os.fsdecode(path_bytes)
        if allow_memory_cache and path == "memory.md":
            continue
        mode, object_id, stage = metadata.decode("ascii").split(" ")
        if stage != "0" or path in actual:
            raise GitPreparationError("preparation index contains unmerged entries")
        actual[path] = (mode, object_id)
    if actual != entries:
        raise GitPreparationError("preparation index differs from admitted tree")
    flags = _read_git_bytes(root, ["ls-files", "-v", "-z"])
    if any(
        row and not row.startswith(b"H ") and not (allow_memory_cache and row[2:] == b"memory.md")
        for row in flags.split(b"\0")
    ):
        raise GitPreparationError("preparation index contains hidden source flags")
    require_physical_tree(root, entries, allow_memory_cache=allow_memory_cache)


def _require_logical_git_root(
    root: Path, common_directory: Path, logical_ref: str, commit: str
) -> None:
    for path in (root, common_directory):
        if not path.is_absolute() or path.resolve() != path:
            raise GitPreparationError("existing preparation paths must be absolute and canonical")
    require_git_object_id(commit)
    if not logical_ref.startswith("refs/heads/"):
        raise GitPreparationError("existing preparation requires an exact logical branch ref")
    _preparation_output(root, ["check-ref-format", logical_ref])
    for args, expected in (
        (["rev-parse", "--show-toplevel"], root.as_posix()),
        (["rev-parse", "--path-format=absolute", "--git-common-dir"], common_directory.as_posix()),
        (["symbolic-ref", "--quiet", "HEAD"], logical_ref),
        (["rev-parse", "--verify", logical_ref], commit),
        (["rev-parse", "--verify", "HEAD^{commit}"], commit),
    ):
        if _preparation_output(root, args).strip() != expected:
            raise GitPreparationError("existing logical preparation binding changed")


def _require_existing_preparation(binding: ExistingGitPreparationBinding) -> None:
    _require_logical_git_root(
        binding.root, binding.common_directory, binding.logical_ref, binding.commit
    )
    require_git_object_id(binding.tree)
    if (
        _preparation_output(binding.root, ["rev-parse", "--verify", "HEAD^{tree}"]).strip()
        != binding.tree
    ):
        raise GitPreparationError("existing logical preparation tree changed")
    _require_preparation_index(
        binding.root,
        _existing_preparation_entries(binding),
        allow_memory_cache=binding.allow_memory_cache,
    )


def _existing_preparation_entries(
    binding: ExistingGitPreparationBinding,
) -> dict[str, tuple[str, str]]:
    entries = _preparation_tree_entries(binding.root, binding.tree)
    content_tree = binding.memory_content_tree
    if not binding.allow_memory_cache:
        if content_tree is not None:
            raise GitPreparationError("code preparation cannot carry a memory content tree")
        return entries
    if content_tree is None:
        raise GitPreparationError("memory preparation requires its certified content tree")
    require_git_object_id(content_tree)
    certified = _preparation_tree_entries(binding.root, content_tree)
    if "memory.md" in certified:
        raise GitPreparationError("certified memory content tree includes the derived cache")
    entries.pop("memory.md", None)
    if entries != certified:
        raise GitPreparationError("existing memory content differs from its certified tree")
    return entries


def inspect_existing_git_preparation(binding: ExistingGitPreparationBinding) -> bytes:
    """Prove one existing logical output and return original bytes without private authority.

    The caller owns live journal/config authorization around this read-only observation.
    Both censuses prove the exact original logical ref, HEAD, index and physical tree.
    """
    _require_existing_preparation(binding)
    raw = read_git_commit_bytes(binding.root, binding.commit)
    _require_existing_preparation(binding)
    return raw


def _named_private_registration(binding: PrivateGitPreparationBinding) -> bool:
    output = _preparation_output(binding.logical_root, ["worktree", "list", "--porcelain", "-z"])
    matching = [
        field for field in output.split("\0") if field == f"worktree {binding.private_root}"
    ]
    if len(matching) > 1:
        raise GitPreparationError("private checkout has ambiguous Git registration")
    return bool(matching)


def _require_private_repository(binding: PrivateGitPreparationBinding) -> Path:
    for args, expected in (
        (["rev-parse", "--show-toplevel"], binding.private_root.as_posix()),
        (
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
            binding.common_directory.as_posix(),
        ),
    ):
        if _preparation_output(binding.private_root, args).strip() != expected:
            raise GitPreparationError("private checkout repository identity changed")
    symbolic = run_git(binding.private_root, ["symbolic-ref", "--quiet", "HEAD"])
    if symbolic.returncode != 1 or symbolic.stdout:
        raise GitPreparationError("private checkout must retain a detached HEAD")
    git_dir = Path(
        _preparation_output(binding.private_root, ["rev-parse", "--absolute-git-dir"]).strip()
    )
    if git_dir.resolve() != git_dir or not git_dir.is_relative_to(
        binding.common_directory / "worktrees"
    ):
        raise GitPreparationError("private Git administration is outside linked-worktree storage")
    backlink = git_dir / "gitdir"
    if (
        backlink.is_symlink()
        or backlink.read_bytes() != os.fsencode(binding.private_root / ".git") + b"\n"
    ):
        raise GitPreparationError("private linked-worktree backlink changed")
    if (git_dir / "HEAD").is_symlink():
        raise GitPreparationError("private HEAD must not redirect to another output")
    return git_dir


def _observe_private_preparation(
    binding: PrivateGitPreparationBinding,
) -> PrivateGitPreparationObservation:
    _require_logical_preparation_binding(binding)
    entries = _preparation_tree_entries(binding.logical_root, binding.admitted_tree)
    registered = _named_private_registration(binding)
    if not binding.private_root.exists():
        if registered:
            raise GitPreparationError(
                "named private checkout registration exists without its directory"
            )
        return PrivateGitPreparationObservation("absent")
    if (
        not registered
        or not (binding.private_root / ".git").is_file()
        or (binding.private_root / ".git").is_symlink()
    ):
        raise GitPreparationError("private checkout lacks its exact linked-worktree registration")
    git_dir = _require_private_repository(binding)
    head = _preparation_output(binding.private_root, ["rev-parse", "--verify", "HEAD"]).strip()
    index = Path(
        _preparation_output(
            binding.private_root, ["rev-parse", "--path-format=absolute", "--git-path", "index"]
        ).strip()
    )
    if index != git_dir / "index":
        raise GitPreparationError("private index redirects outside its named worktree")
    if not index.exists():
        if head != binding.parent_commit or set(binding.private_root.iterdir()) != {
            binding.private_root / ".git"
        }:
            raise GitPreparationError("unmaterialized private checkout contains unexpected state")
        return PrivateGitPreparationObservation("created", head=head)
    if index.is_symlink() or not index.is_file():
        raise GitPreparationError("private index is not a regular file")
    _require_preparation_index(binding.private_root, entries)
    if head == binding.parent_commit:
        return PrivateGitPreparationObservation(
            "materialized", head=head, tree=binding.admitted_tree
        )
    return _observe_private_commit(binding, head)


def _observe_private_commit(
    binding: PrivateGitPreparationBinding, head: str
) -> PrivateGitPreparationObservation:
    tree = _preparation_output(
        binding.private_root, ["rev-parse", "--verify", "HEAD^{tree}"]
    ).strip()
    lineage = (
        _preparation_output(binding.private_root, ["rev-list", "--parents", "-n", "1", "HEAD"])
        .strip()
        .split()
    )
    parents = tuple(lineage[1:])
    if tree != binding.admitted_tree or parents != (binding.parent_commit,):
        raise GitPreparationError("private commit differs from the admitted tree or sole parent")
    raw = read_git_commit_bytes(binding.private_root, head)
    return PrivateGitPreparationObservation("committed", head, tree, parents, raw)


def admit_private_git_preparation(
    binding: PrivateGitPreparationBinding,
    *,
    authorize: Callable[[PrivateGitPreparationBinding], None],
) -> PrivateGitPreparationCapability:
    """Bind one journal-authorized detached output; this grants no logical publication.

    The upper owner must hold its actual operation lease, admit the exact durable intent
    and check hook/config equivalence in ``authorize``. Recovery supplies that same intent;
    this factory neither discovers another output nor claims an incomplete command worked.
    """
    capability = PrivateGitPreparationCapability(binding, authorize, _PREPARATION_AUTHORITY)
    inspect_git_preparation(capability)
    return capability


def inspect_git_preparation(
    capability: PrivateGitPreparationCapability,
) -> PrivateGitPreparationObservation:
    """Observe only the named output, preserving original raw Git commit bytes."""
    capability.require_authority()
    try:
        observed = _observe_private_preparation(capability.binding)
        _require_logical_preparation_binding(capability.binding)
        return observed
    finally:
        capability.require_authority()


def preparation_command(
    binding: PrivateGitPreparationBinding, action: PreparationAction
) -> GitCommandPlan:
    """Describe the sole allowed command for a journal to persist before execution."""
    binding.validate()
    if action == "create":
        root = binding.logical_root
        args = [
            "worktree",
            "add",
            "--detach",
            "--no-checkout",
            binding.private_root.as_posix(),
            binding.parent_commit,
        ]
    elif action == "materialize":
        root = binding.private_root
        args = ["read-tree", "--reset", "-u", binding.admitted_tree]
    elif action == "commit":
        root = binding.private_root
        # MIK-R152: the commit hook always runs. ``strict-code-no-verify`` is kept as a
        # journal-compatibility spelling only and never skips it: ``git commit`` runs the
        # hook Git would run for a commit in this worktree, and the caller guards the
        # tree the hook judged against the tree the commit records (rule 3).
        args = ["commit"]
        args.extend(["-m", binding.message])
    else:
        raise GitPreparationError("unknown private preparation action")
    return GitCommandPlan(root, tuple(args), tuple(_git_argv(root, args, root)))


def run_git_preparation(
    capability: PrivateGitPreparationCapability,
    action: PreparationAction,
    *,
    commit_timeout_s: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Perform one admitted command once, with exact before/after readback.

    Timeouts propagate; callers must retain the durable intent and inspect its one named
    output. An existing committed output is observation-only and can never be recommitted.
    Normal identity, signing, prepare-commit-msg and post-commit semantics remain Git-owned.

    ``commit_timeout_s`` bounds only the commit action, alone in its process group so the
    bound stops the commit hook with its children (MIK-R152 rule 6); other actions keep
    the local bound.
    """
    before = inspect_git_preparation(capability)
    binding = capability.binding
    required = {"create": "absent", "materialize": "created", "commit": "materialized"}
    if action not in required or before.state != required[action]:
        raise GitPreparationError(
            f"private preparation action {action!r} is not admitted from {before.state}"
        )
    command = preparation_command(binding, action)
    capability.require_authority()
    if action == "commit" and commit_timeout_s is not None:
        result = run_git(
            command.cwd,
            list(command.args),
            GitRunnerOptions(timeout=commit_timeout_s, new_session=True),
        )
    else:
        result = run_git(command.cwd, list(command.args))
    try:
        after = inspect_git_preparation(capability)
        expected = {"create": "created", "materialize": "materialized", "commit": "committed"}
        if result.returncode == 0 and after.state != expected[action]:
            raise GitPreparationError(
                "successful Git command did not produce its exact private state"
            )
    except GitPreparationError as error:
        raise GitPreparationCommandError(str(error), result) from error
    return result


def _observe_closeout_publication(
    binding: GitCloseoutPublicationBinding,
) -> GitCloseoutPublicationObservation:
    current = _preparation_output(
        binding.root, ["rev-parse", "--verify", binding.logical_ref]
    ).strip()
    if current not in (binding.expected_old_commit, binding.prepared_commit):
        raise GitCloseoutPublicationError(
            "named closeout ref is outside its original old/new publication states"
        )
    _require_logical_git_root(binding.root, binding.common_directory, binding.logical_ref, current)
    binding.require_prepared_bytes(read_git_commit_bytes(binding.root, binding.prepared_commit))
    entries = _preparation_tree_entries(binding.root, binding.prepared_tree)
    if (
        binding.allow_memory_cache
        and binding.prepared_commit != binding.expected_old_commit
        and "memory.md" in entries
    ):
        raise GitCloseoutPublicationError("new memory publication includes the derived cache")
    _require_preparation_index(
        binding.root,
        entries,
        allow_memory_cache=binding.allow_memory_cache,
    )
    _require_logical_git_root(binding.root, binding.common_directory, binding.logical_ref, current)
    state = (
        "existing"
        if binding.expected_old_commit == binding.prepared_commit
        else "new"
        if current == binding.prepared_commit
        else "old"
    )
    return GitCloseoutPublicationObservation(state, current, binding.prepared_tree)


def admit_git_closeout_publication(
    binding: GitCloseoutPublicationBinding,
    *,
    authorize: Callable[[GitCloseoutPublicationBinding], None],
) -> GitCloseoutPublicationCapability:
    """Admit one actual closeout journal/approval owner, never integration authority."""
    capability = GitCloseoutPublicationCapability(
        binding, authorize, _CLOSEOUT_PUBLICATION_AUTHORITY
    )
    inspect_git_closeout_publication(capability)
    return capability


def inspect_git_closeout_publication(
    capability: GitCloseoutPublicationCapability,
) -> GitCloseoutPublicationObservation:
    """Reprove only the named old/new ref and its already-materialized candidate."""
    capability.require_authority()
    try:
        return _observe_closeout_publication(capability.binding)
    except GitPreparationError as error:
        raise GitCloseoutPublicationError(str(error)) from error
    finally:
        capability.require_authority()


def ref_compare_and_swap_args(
    ref: str, new_commit: str, expected_old_commit: str, *, reason: str | None = None
) -> list[str]:
    """The one expected-old ref move every closeout publication uses.

    The queued closeout journals it (:func:`closeout_publication_command`); the ordinary closeout
    and direct landing run it straight after they build their commit from the judged tree. Git
    refuses the move when the ref no longer names ``expected_old_commit``.
    """
    return [
        "update-ref",
        *(() if reason is None else ("-m", reason)),
        ref,
        new_commit,
        expected_old_commit,
    ]


def closeout_publication_command(binding: GitCloseoutPublicationBinding) -> GitCommandPlan:
    """Describe the one expected-old CAS for the caller's durable publication intent."""
    binding.validate()
    if binding.expected_old_commit == binding.prepared_commit:
        raise GitCloseoutPublicationError("existing closeout output is observation-only")
    args = ref_compare_and_swap_args(
        binding.logical_ref, binding.prepared_commit, binding.expected_old_commit
    )
    return GitCommandPlan(
        binding.root, tuple(args), tuple(_git_argv(binding.root, args, binding.root))
    )


def publish_git_closeout_ref(
    capability: GitCloseoutPublicationCapability,
) -> GitCloseoutPublicationResult:
    """CAS one ref once; this neither materializes files nor promises cross-repo atomicity.

    A timeout remains the upper journal owner's unknown command. Already-new and
    no-op existing states return read-only evidence with no command. A failed after
    proof retains an available actual command result on the typed refusal.
    """
    before = inspect_git_closeout_publication(capability)
    if before.state != "old":
        return GitCloseoutPublicationResult(before, before, None)
    command = closeout_publication_command(capability.binding)
    capability.require_authority()
    result = run_git(command.cwd, list(command.args))
    try:
        after = inspect_git_closeout_publication(capability)
        if result.returncode == 0 and after.state != "new":
            raise GitCloseoutPublicationError(
                "successful closeout CAS did not retain the prepared ref"
            )
    except GitCloseoutPublicationError as error:
        raise GitCloseoutPublicationError(str(error), command=result) from error
    return GitCloseoutPublicationResult(before, after, result)
