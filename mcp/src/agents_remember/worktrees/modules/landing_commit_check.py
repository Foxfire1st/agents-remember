"""The repository's own pre-commit check, run by a landing route (MIK-R152).

A landing route stages the exact tree it will publish, then calls
:func:`run_landing_commit_check` before any ref names a commit. The runner
resolves the hook exactly as Git would for a commit in that worktree
(``core.hooksPath``, else the hooks folder of the repository), starts it in the
worktree with the index holding the tree, and refuses the landing on a failure,
on a change the hook's run made, or on a run that does not end within the
bound. The product copies no step of the hook and knows none by name: the only
thing it parses out of the hook's output is the last ``result: <step> FAIL``
line for the refusal's one line.

The fact a landing passed travels in the commit itself
(:func:`commit_check_trailer`, read back with :func:`commit_check_fact`), so
the integration's master-line move (MIK-R152 rule 8) can tell a checked commit
from one with no such fact without any new durable record.
"""

from __future__ import annotations

import json
import os
import re
import select
import signal
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel._agentic_settings_core import (
    DEFAULT_COMMIT_CHECK_TIMEOUT_SECONDS,
)
from agents_remember.kernel.git_command import git_environment
from agents_remember.worktrees.modules.models import PATH_SAMPLE_LIMIT, CommitPublicationRefusal

#: The trailer a landing commit carries for MIK-R152 rule 8:
#: ``Commit-Check-Pass: tree=<tree> hook=<hook>``. ``hook`` is ``none`` where
#: the repository had no pre-commit hook (MIK-R152 rule 5).
COMMIT_CHECK_TRAILER_PREFIX = "Commit-Check-Pass:"

#: A stash entry this runner's hook run left behind is recognised by it: the
#: gate isolates staged content with ``git stash push --keep-index``, whose
#: message ends here (``.githooks/_gate.sh`` ``STASH_MESSAGE``).
_GATE_STASH_MESSAGE_SUFFIX = "gate: staged-content isolation"

#: After the bound, the hook's process group is asked to stop before it is
#: stopped: the gate restores its isolation stash on a trapped exit.
_HOOK_TERM_GRACE_SECONDS = 30.0
#: Bound for asking an escaped pipe holder to die before stopping it; the drain
#: grace already expired, so this is short patience, not a second grace.
_HOOK_ESCAPED_TERM_SECONDS = 2.0

#: Rounds of scan-ask-stop-verify for escaped pipe holders before survivors
#: are named alive instead of being claimed stopped; a holder the scan missed
#: or forked mid-round is caught by the next.
_HOOK_ESCAPED_ROUNDS = 3

#: Bound verifying a stopped holder is really dead before it counts as such;
#: a zombie holds no file end, so reaping lag never blocks the pipe — but the
#: return must not claim a live holder stopped.
_HOOK_ESCAPED_KILL_VERIFY_SECONDS = 5.0

#: Hook output is copied to the log in chunks; no run holds more than one
#: chunk in memory, whatever the hook writes (standing code-seam CS-6).
_HOOK_OUTPUT_CHUNK_BYTES = 65536

#: The step parser reads only the log's tail, never the whole output.
_HOOK_LOG_TAIL_BYTES = 65536

#: Retention: newest per-repo hook logs kept; older ones are reclaimed.
_HOOK_LOG_KEEP_COUNT = 20

#: Bound for draining the pipe after the process group is stopped; a writer
#: that escaped the group and still holds the pipe past this bound is cut off
#: (its file end is closed) and named in the log footer.
_HOOK_DRAIN_GRACE_SECONDS = 10.0

#: Quantum for the output-copy wait and the group-death poll.
_HOOK_COPY_QUANTUM_SECONDS = 0.05

_RESULT_FAIL_RE = re.compile(r"^.*result:\s+(?P<step>\S+)\s+FAIL\s*$")


@dataclass(frozen=True)
class CheckedTree:
    """The tree one hook run judged: where, what, and which hook."""

    repo: Path
    tree: str
    hook: str


class LandingCommitCheckRefusal(CommitPublicationRefusal):
    """The repository's commit check refused a landing (MIK-R152 rule 4).

    A subclass of :class:`CommitPublicationRefusal`, so every route that
    already cancels its generation on a publication refusal treats this one
    the same way: no ref names a new commit, the index is given back as the
    call found it, and the same call after the repair is the next action.
    """

    def __init__(  # noqa: PLR0913 -- the refusal carries the whole check evidence
        self,
        reason: str,
        *,
        checked: CheckedTree,
        step: str | None,
        log: Path | None,
        changed_paths: tuple[str, ...] = (),
        escaped_survivors: tuple[int, ...] = (),
    ) -> None:
        self.reason = reason
        self.repo = checked.repo
        self.tree = checked.tree
        self.hook = checked.hook
        self.step = step
        self.log = log
        self.changed_paths = changed_paths
        self.escaped_survivors = escaped_survivors
        super().__init__(str(self))

    def __str__(self) -> str:
        line = f"the repository's commit check failed for {self.repo}: "
        if self.reason == "changed":
            paths = ", ".join(self.changed_paths[:PATH_SAMPLE_LIMIT])
            line += (
                f"the hook's run changed the index or a tracked file ({paths}); "
                "nothing was published"
            )
        elif self.reason == "timeout":
            if self.escaped_survivors:
                pids = ", ".join(f"pid={pid}" for pid in self.escaped_survivors)
                line += (
                    "the hook did not end within the bound; its process group was "
                    f"stopped but escaped descendant(s) are still alive ({pids}); "
                    "nothing was published"
                )
            else:
                line += (
                    "the hook did not end within the bound; it was stopped with its "
                    "children and nothing was published"
                )
        elif self.reason == "unrunnable":
            line += f"the hook cannot run ({self.step}); nothing was published"
        elif self.reason == "stash-left":
            line += (
                "the stopped hook left its isolation stash; nothing was published; "
                f"restore it with: git stash apply --index {self.step}"
            )
        else:
            line += "the hook ended with a failure; nothing was published"
        if self.step is not None and self.reason == "failed":
            line += f"; failing step: {self.step}"
        if self.log is not None:
            line += f"; hook log: {self.log.as_posix()}"
        line += "; repair the cause and make the same call again"
        return line


@dataclass(frozen=True)
class CommitCheckPass:
    """One judged tree: the hook that judged it, the log, and the time it took."""

    tree: str
    hook: str
    log: Path
    seconds: float
    admitted: TreeSnapshot | None = None


def commit_check_trailer(tree: str, hook: str) -> str:
    """The ``Commit-Check-Pass`` trailer line for a tree the hook judged."""
    return f"{COMMIT_CHECK_TRAILER_PREFIX} tree={tree} hook={hook}"


def append_commit_check_trailer(message: str, tree: str, hook: str) -> str:
    """The message with the ``Commit-Check-Pass`` trailer in its trailer block.

    The trailer joins the message's last paragraph with no blank line of its
    own: the memory commit's ``Code-Commit`` trailer must stay in the same
    trailer block, because git's trailer readers (``%(trailers:...)``,
    ``interpret-trailers``) stop at a blank line, and the attribution readers
    built on them must keep seeing it. A one-line message's ``%s`` subject
    therefore carries the fact; readers that need the bare subject take the
    first line.
    """
    text = message if message.endswith("\n") else f"{message}\n"
    return f"{text}{commit_check_trailer(tree, hook)}\n"


def commit_check_fact(message: str) -> tuple[str, str] | None:
    """Read back the last ``Commit-Check-Pass`` trailer as ``(tree, hook)``."""
    found: tuple[str, str] | None = None
    for line in message.splitlines():
        text = line.strip()
        if not text.startswith(COMMIT_CHECK_TRAILER_PREFIX):
            continue
        fields = dict(
            part.split("=", 1)
            for part in text[len(COMMIT_CHECK_TRAILER_PREFIX) :].split()
            if "=" in part
        )
        if "tree" in fields and "hook" in fields:
            found = (fields["tree"], fields["hook"])
    return found


def _git(
    repo: Path, args: list[str], *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    merged = {**git_environment(), **env} if env else None
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        check=False,
        env=merged,
    )


def _require_git(repo: Path, args: list[str]) -> str:
    result = _git(repo, args)
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def resolve_commit_check_hook(repo: Path) -> Path | None:
    """The pre-commit hook Git would run for a commit in ``repo``, if runnable.

    ``core.hooksPath`` first, else the hooks folder of the repository. A
    repository with no hook file answers ``None`` (MIK-R152 rule 5); a hook
    file Git would not run is reported by :func:`unrunnable_hook_reason`.
    """
    return _hook_candidate(repo)


def _hook_candidate(repo: Path) -> Path | None:
    configured = _git(repo, ["config", "core.hooksPath"])
    if configured.returncode == 0 and configured.stdout.strip():
        candidate = Path(configured.stdout.strip())
        if not candidate.is_absolute():
            candidate = repo / candidate
        hook = candidate / "pre-commit" if candidate.is_dir() else candidate
    else:
        common = _require_git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
        hook = Path(common) / "hooks" / "pre-commit"
    if hook.is_file() and os.access(hook, os.X_OK):
        return hook
    return None


def unrunnable_hook_reason(repo: Path) -> str | None:
    """Why the repository's hook file would not run, or ``None`` where none exists.

    A hook file Git would not run (present but not a runnable file) refuses the
    landing (MIK-R152 rule 6); an absent hook is rule 5, not a refusal.
    """
    configured = _git(repo, ["config", "core.hooksPath"])
    if configured.returncode == 0 and configured.stdout.strip():
        candidate = Path(configured.stdout.strip())
        if not candidate.is_absolute():
            candidate = repo / candidate
        hook = candidate / "pre-commit" if candidate.is_dir() else candidate
    else:
        common = _git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
        if common.returncode != 0:
            return None
        hook = Path(common.stdout.strip()) / "hooks" / "pre-commit"
    if not hook.exists() and not hook.is_symlink():
        return None
    if not hook.is_file():
        return f"the hook is not a file: {hook.as_posix()}"
    if not os.access(hook, os.X_OK):
        return f"the hook is not executable: {hook.as_posix()}"
    return None


def _hook_display(repo: Path, hook: Path) -> str:
    """The hook's stable display path: repo-relative where it fits.

    A linked worktree resolves its hooks through the common git dir, so the
    hook may sit outside the worktree's own top level; shown from the common
    dir's parent it reads exactly as in the main checkout (``.git/hooks/...``).
    """
    try:
        top = _require_git(repo, ["rev-parse", "--show-toplevel"])
        try:
            return hook.relative_to(top).as_posix()
        except ValueError:
            pass
        common = _require_git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
        return hook.relative_to(Path(common).parent).as_posix()
    except (RuntimeError, ValueError):
        return hook.as_posix()


def _refuse_unreadable_settings(repo: Path, step: str) -> LandingCommitCheckRefusal:
    """The settings refusal: the landing cannot read the bound it must honor."""
    return LandingCommitCheckRefusal(
        "unrunnable",
        checked=CheckedTree(repo=repo, tree="", hook=""),
        step=step,
        log=None,
    )


def _merge_landing_block(merged: dict[str, object], repo: Path, path: Path, raw: object) -> None:
    """Merge one settings file's ``orchestration.landing`` block, failing loud."""
    if not isinstance(raw, dict):
        raise _refuse_unreadable_settings(repo, f"the settings file holds no object: {path}")
    orchestration = raw.get("orchestration")
    if orchestration is None:
        return
    if not isinstance(orchestration, dict):
        raise _refuse_unreadable_settings(repo, f"orchestration is no object: {path}")
    landing = orchestration.get("landing")
    if landing is None:
        return
    if not isinstance(landing, dict):
        raise _refuse_unreadable_settings(repo, f"orchestration.landing is no object: {path}")
    unknown = set(landing) - {"commitCheckTimeoutSeconds"}
    if unknown:
        raise _refuse_unreadable_settings(
            repo, f"unknown orchestration.landing key: {sorted(unknown)}: {path}"
        )
    merged.update(landing)


def commit_check_hook_id(repo: Path) -> str:
    """The hook identity for the pass trailer: the hook's display path, or ``"none"``."""
    hook = _hook_candidate(repo)
    if hook is None or not hook.is_file():
        return "none"
    return _hook_display(repo, hook)


def message_with_commit_check_trailer(repo: Path, message: str, tree: str) -> str:
    """The commit message with the ``Commit-Check-Pass`` trailer for ``tree``.

    The trailer is prospective at intent time: it names the tree the hook will
    judge and the hook that will judge it. A commit carrying it passed, because
    a failed check publishes no commit; the integration reads it back as the
    fact of the landing's record (MIK-R152 rule 8).
    """
    text = message if message.endswith("\n") else f"{message}\n"
    return append_commit_check_trailer(text, tree, commit_check_hook_id(repo))


def commit_check_timeout_seconds(repo: Path) -> int:
    """The bound on one hook run: the settings, else the default (MIK-R152 rule 6)."""
    top = _git(repo, ["rev-parse", "--show-toplevel"])
    candidates = (
        [Path(os.environ["XDG_CONFIG_HOME"]) / "agents-remember" / "settings.json"]
        if os.environ.get("XDG_CONFIG_HOME")
        else [Path.home() / ".config" / "agents-remember" / "settings.json"]
    )
    if top.returncode == 0:
        candidates.append(Path(top.stdout.strip()) / "system" / "settings.json")
        # A linked worktree holds no settings of its own: the repo-local layer
        # lives in the main checkout, found through the common git dir as the
        # hook itself does (`.githooks/_gate.sh` `main_root`).
        common = _git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
        if common.returncode == 0:
            main_root = Path(common.stdout.strip()).parent
            if main_root != Path(top.stdout.strip()):
                candidates.append(main_root / "system" / "settings.json")
    merged: dict[str, object] = {}
    for path in candidates:
        try:
            raw = json.loads(path.read_bytes().decode("utf-8"))
        except OSError:
            continue
        except ValueError as error:
            raise _refuse_unreadable_settings(
                repo, f"the settings file cannot be read: {path}: {error}"
            ) from error
        _merge_landing_block(merged, repo, path, raw)
    if "commitCheckTimeoutSeconds" not in merged:
        return DEFAULT_COMMIT_CHECK_TIMEOUT_SECONDS
    value = merged["commitCheckTimeoutSeconds"]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise _refuse_unreadable_settings(
            repo,
            "orchestration.landing.commitCheckTimeoutSeconds must be a positive whole number",
        )
    return value


def _default_log_path(repo: Path) -> Path:
    common = _require_git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    return Path(common) / f"agents-remember-commit-check-{uuid.uuid4().hex[:12]}.log"


def _reclaim_hook_logs(repo: Path) -> None:
    """Keep the newest per-repo hook logs; reclaim older ones (standing code-seam CS-6).

    Every run writes one log file; without an owner the common git dir would
    accumulate one per landing. Best effort: a failure to list or remove never
    refuses the landing the check judges.
    """
    try:
        common = _require_git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
        logs = sorted(
            Path(common).glob("agents-remember-commit-check-*.log"),
            key=lambda path: (path.stat().st_mtime, path.name),
        )
    except (RuntimeError, OSError):
        return
    for stale in logs[: max(0, len(logs) - _HOOK_LOG_KEEP_COUNT)]:
        with suppress(OSError):
            stale.unlink()


def _log_tail_text(log: Path) -> str:
    """The log's tail for the step parser: bounded, never the whole output."""
    try:
        size = log.stat().st_size
        with log.open("rb") as handle:
            if size > _HOOK_LOG_TAIL_BYTES:
                handle.seek(size - _HOOK_LOG_TAIL_BYTES)
            tail = handle.read()
    except OSError:
        return ""
    return tail.decode("utf-8", "surrogateescape")


def _index_tree(repo: Path, *, env: dict[str, str] | None = None) -> str:
    if env is None:
        return _require_git(repo, ["write-tree"])
    result = _git(repo, ["write-tree"], env=env)
    if result.returncode != 0:
        raise RuntimeError(
            f"git write-tree failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


#: The empty tree: the diff base where a repository has no HEAD yet (a first
#: adoption judges its tree against nothing, so everything staged is new).
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _diff_base_ref(repo: Path) -> str:
    """The ref a hook run diffs against: HEAD, or the empty tree before it exists."""

    probe = _git(repo, ["rev-parse", "--verify", "HEAD"])
    return "HEAD" if probe.returncode == 0 else _EMPTY_TREE_SHA


def _diff_head_raw(repo: Path, *, env: dict[str, str] | None = None, against: str = "HEAD") -> str:
    """Staged and unstaged tracked-file bytes against a base, as raw diff lines.

    Status labels cannot see a hook rewrite that keeps them (an already-dirty
    path rewritten dirty reads the same); blob identities can (MIK-R152 rule 3).
    The refresh first settles the index's stat cache: a fresh ``read-tree``
    index reports zero blob identities until git hashes the worktree once, so
    two consecutive diffs would otherwise disagree with no byte changed.
    """
    _git(repo, ["update-index", "--refresh", "-q"], env=env)
    if env is None:
        return _require_git(repo, ["diff", against, "--raw", "--no-abbrev", "--no-renames"])
    result = _git(repo, ["diff", against, "--raw", "--no-abbrev", "--no-renames"], env=env)
    if result.returncode != 0:
        raise RuntimeError(
            f"git diff {against} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


def _untracked_status(repo: Path, *, env: dict[str, str] | None = None) -> list[str]:
    out = _require_git(repo, ["status", "--porcelain=v1", "--untracked-files=all"])
    if env is not None:
        result = _git(repo, ["status", "--porcelain=v1", "--untracked-files=all"], env=env)
        if result.returncode != 0:
            raise RuntimeError(
                f"git status failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        out = result.stdout
    return sorted(line for line in out.splitlines() if line.startswith("??"))


def _unmerged_paths(repo: Path, *, env: dict[str, str] | None = None) -> list[str]:
    out = _git(repo, ["diff", "--name-only", "--diff-filter=U"], env=env)
    if out.returncode != 0:
        return []
    return sorted(out.stdout.splitlines())


_UNHASHABLE_BLOB = "<unhashable>"


@dataclass
class TreeSnapshot:
    """The three lenses one hook run must give back unchanged (MIK-R152 rule 3)."""

    index_tree: str
    diff_head: str
    blobs: dict[str, str]


def worktree_blob_map(repo: Path, *, env: dict[str, str] | None = None) -> dict[str, str]:
    """Blob identity of every tracked worktree file's current bytes (MIK-R152 rule 3).

    ``git diff --raw`` does not hash dirty worktree files (it reports zeros),
    so a hook rewrite that keeps the status labels is invisible to it; hashing
    the bytes directly sees every rewrite. Entries git cannot hash (submodules,
    absent files) map to a stable sentinel: appearing or disappearing still
    names the path. Compared as a whole map: only byte identities matter, not
    their order.
    """
    listed = _git(repo, ["ls-files", "-z"], env=env)
    if listed.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {listed.stderr.strip() or listed.stdout.strip()}")
    names = [name for name in listed.stdout.split("\0") if name]
    present_names = {name for name in names if (repo / name).exists() or (repo / name).is_symlink()}
    digests: dict[str, str] = {
        name: _UNHASHABLE_BLOB for name in names if name not in present_names
    }
    present = [name for name in names if name in present_names]
    for offset in range(0, len(present), 500):
        chunk = present[offset : offset + 500]
        hashed = _git(repo, ["hash-object", "--", *chunk], env=None)
        if hashed.returncode != 0:
            for name in chunk:
                single = _git(repo, ["hash-object", "--", name], env=None)
                digests[name] = (
                    single.stdout.split()[0]
                    if single.returncode == 0 and single.stdout.split()
                    else _UNHASHABLE_BLOB
                )
            continue
        for name, line in zip(chunk, hashed.stdout.splitlines(), strict=True):
            digests[name] = line.split()[0] if line.split() else _UNHASHABLE_BLOB
    return digests


def _stash_top(repo: Path) -> str | None:
    result = _git(repo, ["rev-parse", "--verify", "--quiet", "refs/stash"])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _stash_message(repo: Path, entry: str) -> str:
    result = _git(repo, ["log", "-1", "--format=%s", entry])
    return result.stdout.strip() if result.returncode == 0 else ""


def _stash_entry_is_ours(repo: Path, entry: str) -> bool:
    return _stash_message(repo, entry).endswith(_GATE_STASH_MESSAGE_SUFFIX)


def _last_failing_step(output: str, *, hook_path: Path | None = None) -> str | None:
    """The last step the hook reported, or what its silence means.

    A ``result: <step> FAIL`` line names the step. Where the hook reports none,
    a refused exec names the hook's interpreter; otherwise the last output line
    stands in, so a missing interpreter or package refuses with its reason.
    """
    step: str | None = None
    for line in output.splitlines():
        match = _RESULT_FAIL_RE.match(line.strip())
        if match:
            step = match.group("step")
    if step is not None:
        return step
    if hook_path is not None and "cannot exec" in output:
        try:
            first = hook_path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        except OSError:
            first = ""
        if first.startswith("#!"):
            return f"the hook's interpreter cannot run: {first[2:].strip() or first}"
    for line in reversed(output.splitlines()):
        if line.strip():
            text = line.strip()
            return text if len(text) <= 200 else f"{text[:200]}…"
    return None


@dataclass(frozen=True)
class _CheckRequest:
    """One hook run's inputs: what to judge, how long, where to log, what to restore."""

    tree: str
    timeout_s: int | None = None
    log_path: Path | None = None
    restore_index: Callable[[], None] | None = None
    index_file: Path | None = None


@dataclass(frozen=True)
class _HookRunState:
    """The worktree state one hook run must give back unchanged."""

    index_tree: str
    diff_head: str
    blobs: dict[str, str]
    untracked: list[str]
    unmerged: list[str]
    stash: str | None
    env: dict[str, str] | None = None


def run_landing_commit_check(
    repo: Path,
    *,
    tree: str,
    timeout_s: int | None = None,
    log_path: Path | None = None,
    restore_index: Callable[[], None] | None = None,
) -> CommitCheckPass:
    """Run the repository's pre-commit hook on the staged ``tree`` (MIK-R152 rules 2-6).

    The index must hold ``tree`` when this is called; the hook judges exactly
    that staged content. A repository without a runnable hook passes with hook
    ``"none"`` and the log saying so (rule 5). Anything else that is not a
    clean pass raises :class:`LandingCommitCheckRefusal`: a failing hook, a
    hook the run changed the index or a tracked file through (rule 3, naming
    the paths), an unrunnable hook, or a run past the bound, stopped with its
    children (rule 6). Where the hook left its isolation stash behind,
    ``restore_index`` (which must give the caller's index back) runs before
    the refusal, and a stash entry the hook's run provably created is applied
    back first; anything older is named, never touched. The log holds the whole
    hook output and the elapsed time on every path (rules 4, 9); hook logs are
    reclaimed to the newest twenty per repository.
    """
    return _run_checked(
        repo,
        _CheckRequest(
            tree=tree, timeout_s=timeout_s, log_path=log_path, restore_index=restore_index
        ),
    )


def restore_hook_rewritten_worktree(
    repo: Path, tree: str, paths: tuple[str, ...]
) -> tuple[str, ...]:
    """Give back worktree bytes a hook run rewrote, from the judged tree.

    The hook runs isolated from the caller's index but not from the worktree
    files: a hook that rewrites a tracked file leaves its bytes behind even
    though the run is refused. For every named path the judged tree still
    carries, the worktree file is rewritten with exactly those bytes, so the
    worktree holds what it held before the run began. Paths the tree does not
    carry (hook droppings, newly added files) are left in place and answered
    for the operator. Never raises: a path that cannot be given back stays
    one and stays named.
    """
    restored: list[str] = []
    for path in paths:
        if not path or path == "the index":
            continue
        shown = subprocess.run(
            ["git", "show", f"{tree}:{path}"],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        if shown.returncode != 0:
            continue
        target = repo / path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(shown.stdout)
        except OSError:
            continue
        restored.append(path)
    return tuple(restored)


def check_published_tree(repo: Path, tree: str, *, timeout_s: int | None = None) -> CommitCheckPass:
    """Run the repository's hook on an exact tree without touching any live index.

    Stages ``tree`` into a scratch index, judges exactly that content, and
    discards the index. A refusal publishes nothing. Callers that move refs
    onto rewritten-but-identical trees (baseline adoption, history migration)
    enforce the same check/no-bypass/refusal contract as leaf landings through
    this entry point (MIK-R152 rules 1, 7).
    """
    with tempfile.TemporaryDirectory(prefix="ar-commit-check-") as temporary:
        index = Path(temporary) / "index"
        staged = _git(
            repo,
            ["read-tree", tree],
            env={**git_environment(), "GIT_INDEX_FILE": index.as_posix()},
        )
        if staged.returncode != 0:
            raise RuntimeError(
                f"could not stage tree {tree} for the commit check: "
                f"{staged.stderr.strip() or staged.stdout.strip()}"
            )
        return run_landing_commit_check_on_index(repo, index, tree=tree, timeout_s=timeout_s)


def run_landing_commit_check_on_index(
    repo: Path,
    index_file: Path,
    *,
    tree: str,
    timeout_s: int | None = None,
    log_path: Path | None = None,
) -> CommitCheckPass:
    """Run the check where the caller stages through ``GIT_INDEX_FILE`` (baseline adoption).

    The file ``index_file`` must hold ``tree`` when this is called; the stash
    recovery still uses the worktree's own state, and there is no caller index
    to restore (the private index is discarded by the caller). No pass trailer
    is written: baseline adoption keeps its own message.
    """
    return _run_checked(
        repo,
        _CheckRequest(tree=tree, timeout_s=timeout_s, log_path=log_path, index_file=index_file),
    )


def _run_checked(repo: Path, request: _CheckRequest) -> CommitCheckPass:
    """One hook run for either public entry point above."""
    bound = (
        request.timeout_s if request.timeout_s is not None else commit_check_timeout_seconds(repo)
    )
    log = request.log_path if request.log_path is not None else _default_log_path(repo)
    if request.log_path is None:
        _reclaim_hook_logs(repo)
    hook = resolve_commit_check_hook(repo)
    started = time.monotonic()

    def footer(outcome: str) -> float:
        seconds = time.monotonic() - started
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"elapsed: {seconds:.3f}s {outcome}\n")
        return seconds

    if hook is None:
        reason = unrunnable_hook_reason(repo)
        if reason is not None:
            log.write_text(f"landing commit check refused: {reason}\n", encoding="utf-8")
            footer("unrunnable")
            raise LandingCommitCheckRefusal(
                "unrunnable",
                checked=CheckedTree(repo=repo, tree=request.tree, hook=""),
                step=reason,
                log=log,
            )
        log.write_text(
            f"landing commit check: no pre-commit hook in {repo}; the landing goes on\n",
            encoding="utf-8",
        )
        seconds = footer("no-hook")
        return CommitCheckPass(tree=request.tree, hook="none", log=log, seconds=seconds)

    hook_id = _hook_display(repo, hook)
    checked = CheckedTree(repo=repo, tree=request.tree, hook=hook_id)
    env = (
        {**git_environment(), "GIT_INDEX_FILE": request.index_file.as_posix()}
        if request.index_file
        else None
    )
    before = _HookRunState(
        index_tree=_index_tree(repo, env=env),
        diff_head=_diff_head_raw(repo, env=env, against=_diff_base_ref(repo)),
        blobs=worktree_blob_map(repo, env=env),
        untracked=_untracked_status(repo, env=env),
        unmerged=_unmerged_paths(repo, env=env),
        stash=_stash_top(repo),
        env=env,
    )
    log.write_text(
        f"landing commit check: hook={hook_id} tree={request.tree} bound={bound}s\n",
        encoding="utf-8",
    )
    try:
        process = _spawn_hook(repo, env)
    except OSError as error:
        footer("unrunnable(spawn)")
        raise LandingCommitCheckRefusal(
            "unrunnable", checked=checked, step=str(error), log=log
        ) from error
    outcome = _run_hook_to_log(process, log, bound)
    footer("timeout" if outcome.timed_out else f"exit={outcome.returncode}")
    _judge_hook_result(
        outcome,
        before,
        checked,
        log,
        request.restore_index,
    )
    left_untracked = [
        line for line in _untracked_status(repo, env=env) if line not in set(before.untracked)
    ]
    if left_untracked:
        with log.open("a", encoding="utf-8") as handle:
            handle.write(
                "untracked paths the hook's run left behind: "
                + ", ".join(line[3:] for line in left_untracked[:PATH_SAMPLE_LIMIT])
                + "\n"
            )
    return CommitCheckPass(
        tree=request.tree,
        hook=hook_id,
        log=log,
        seconds=footer("pass"),
        admitted=TreeSnapshot(
            index_tree=before.index_tree, diff_head=before.diff_head, blobs=before.blobs
        ),
    )


def _spawn_hook(repo: Path, env: dict[str, str] | None) -> subprocess.Popen[bytes]:
    """Start the hook Git would run, alone in its process group (MIK-R152 rule 2).

    Output streams as bytes: the caller copies it to the log in chunks, so no
    run buffers the hook's whole output in memory whatever it writes.
    """
    merged = {**git_environment(), **env} if env else git_environment()
    return subprocess.Popen(
        ["git", "hook", "run", "pre-commit"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=merged,
    )


def _run_hook_to_log(process: subprocess.Popen[bytes], log: Path, bound: int) -> _HookOutcome:
    """Copy the hook's output to the log within the bound; stop it past the bound.

    Returns the exit status and whether the bound stopped the run. Past the
    bound the process group is stopped with its children (MIK-R152 rule 6) and
    the pipe is drained to the log; a writer that escaped the group is asked,
    stopped, and verified dead over bounded rounds, else named alive in the
    footer and the outcome — the return never claims a live holder stopped.
    """
    deadline = time.monotonic() + bound
    assert process.stdout is not None
    stopped: list[int] = []
    survivors: list[int] = []
    try:
        with log.open("ab") as handle:
            reached_eof = _copy_hook_output(process, handle, deadline)
            timed_out = not reached_eof
            if timed_out:
                _stop_hook_tree(process)
                drained = _copy_hook_output(
                    process, handle, time.monotonic() + _HOOK_DRAIN_GRACE_SECONDS
                )
                if not drained:
                    stopped, survivors = _stop_escaped_pipe_holders(process)
                    drained = _copy_hook_output(
                        process, handle, time.monotonic() + _HOOK_DRAIN_GRACE_SECONDS
                    )
                    if stopped:
                        handle.write(
                            (
                                "\n[escaped hook descendants holding the hook output pipe were "
                                f"stopped: {', '.join(f'pid={pid}' for pid in stopped)}]\n"
                            ).encode()
                        )
                    for pid in survivors:
                        handle.write(
                            (
                                "\n[escaped hook descendant holding the hook output pipe is "
                                f"STILL ALIVE: pid={pid}]\n"
                            ).encode()
                        )
                    if not drained:
                        handle.write(
                            b"\n[a writer outside the stopped process group still held the hook output pipe; it was cut off]\n"
                        )
            else:
                # EOF with the hook still running (it closed its output and computes
                # on) still ends at the bound: the landing cannot wait past it.
                try:
                    process.wait(timeout=max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _stop_hook_tree(process)
            returncode = process.wait()
    finally:
        # The pipe reader is owned here on every path: a pass that returns
        # without closing it leaks the BufferedReader past teardown.
        with suppress(OSError):
            process.stdout.close()
    return _HookOutcome(
        returncode=returncode,
        timed_out=timed_out,
        escaped_stopped=tuple(stopped),
        escaped_survivors=tuple(survivors),
    )


def _copy_hook_output(process: subprocess.Popen[bytes], handle: object, deadline: float) -> bool:
    """Copy the hook's pipe to the log file until EOF or the deadline.

    Answers whether EOF was reached. Reads through the file descriptor, never
    through the process object's buffer, and never holds more than one chunk.
    """
    assert process.stdout is not None
    fd = process.stdout.fileno()
    write = handle.write  # type: ignore[attr-defined]
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        ready, _, _ = select.select([fd], [], [], min(remaining, _HOOK_COPY_QUANTUM_SECONDS))
        if not ready:
            # No bytes yet: the hook may still be running, or only an escaped
            # writer still holds the pipe. Either way the deadline decides.
            continue
        try:
            chunk = os.read(fd, _HOOK_OUTPUT_CHUNK_BYTES)
        except OSError:
            return True
        if not chunk:
            return True
        write(chunk)


def _process_group_dead(pid: int) -> bool:
    """Whether no process survives in the group the hook run owns."""
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False
    return False


@dataclass(frozen=True)
class _HookOutcome:
    """What one hook run said: its status, and whether the bound stopped it."""

    returncode: int
    timed_out: bool
    escaped_stopped: tuple[int, ...] = ()
    escaped_survivors: tuple[int, ...] = ()


def _judge_hook_result(
    outcome: _HookOutcome,
    before: _HookRunState,
    checked: CheckedTree,
    log: Path,
    restore_index: Callable[[], None] | None,
) -> None:
    """Raise the refusal a hook run earned, or return for a clean pass."""
    repo = checked.repo
    env = before.env
    if outcome.timed_out:
        _recover_isolation_stash(repo, before)
        if restore_index is not None:
            restore_index()
        raise LandingCommitCheckRefusal(
            "timeout",
            checked=checked,
            step=None,
            log=log,
            escaped_survivors=outcome.escaped_survivors,
        )
    if outcome.returncode != 0:
        if restore_index is not None:
            restore_index()
        raise LandingCommitCheckRefusal(
            "failed",
            checked=checked,
            step=_last_failing_step(_log_tail_text(log), hook_path=resolve_commit_check_hook(repo)),
            log=log,
        )
    after_tree = _index_tree(repo, env=env)
    after_raw = _diff_head_raw(repo, env=env, against=_diff_base_ref(repo))
    after_blobs = worktree_blob_map(repo, env=env)
    if (
        after_tree == before.index_tree
        and set(after_raw.splitlines()) == set(before.diff_head.splitlines())
        and after_blobs == before.blobs
    ):
        return
    missing = _recover_isolation_stash(repo, before)
    if restore_index is not None:
        restore_index()
    changed = _changed_paths(
        TreeSnapshot(index_tree=before.index_tree, diff_head=before.diff_head, blobs=before.blobs),
        TreeSnapshot(index_tree=after_tree, diff_head=after_raw, blobs=after_blobs),
    )
    if missing is not None:
        raise LandingCommitCheckRefusal(
            "stash-left",
            checked=checked,
            step=missing,
            log=log,
            changed_paths=tuple(changed),
        )
    raise LandingCommitCheckRefusal(
        "changed",
        checked=checked,
        step=None,
        log=log,
        changed_paths=tuple(changed),
    )


def _changed_paths(before: TreeSnapshot, after: TreeSnapshot) -> list[str]:
    """The tracked paths the hook's run changed, from blob identities (MIK-R152 rule 3).

    Two lenses: the raw diff lines (mode changes, staged transitions) and the
    worktree bytes hashed directly. The second lens is the one that sees a
    rewrite keeping the status labels: ``git diff --raw`` reports zeros for
    dirty worktree files instead of hashing them, so an already-dirty path
    rewritten dirty reads identical there but differs here. A changed index
    tree with no path lens is answered as the index itself.
    """
    changed_lines = set(before.diff_head.splitlines()) ^ set(after.diff_head.splitlines())
    paths = set()
    for line in changed_lines:
        _meta, _, path = line.partition("\t")
        if path:
            paths.add(path)
    for path in set(before.blobs) | set(after.blobs):
        if before.blobs.get(path) != after.blobs.get(path):
            paths.add(path)
    if not paths and after.index_tree != before.index_tree:
        return ["the index"]
    return sorted(paths)


def _pipe_write_end_holders(pipe_inode: int) -> list[int]:
    """Live PIDs holding the pipe's write end open, other than this process.

    The hook output pipe is private to one run: only the hook subtree inherits
    its write end. A descendant that escaped the process group (new session)
    survives the group kill still holding it; the inode names exactly those
    processes. Best effort: unreadable entries are skipped, never fatal.
    """
    holders: list[int] = []
    me = os.getpid()
    proc = Path("/proc")
    if not proc.is_dir():
        return holders
    with suppress(OSError):
        entries = list(proc.glob("[0-9]*"))
    for entry in entries:
        try:
            pid = int(entry.name)
        except ValueError:
            continue
        if pid == me:
            continue
        try:
            fds = list((entry / "fd").iterdir())
        except OSError:
            continue
        for fd in fds:
            with suppress(OSError):
                if os.readlink(fd) == f"pipe:[{pipe_inode}]":
                    holders.append(pid)
                    break
    return sorted(set(holders))


def _holder_alive(pid: int) -> bool:
    """Whether a pipe holder is live rather than a lingering zombie.

    A zombie holds no file end, so only a running (or uninterruptibly
    sleeping) process keeps the pipe open; state ``Z`` from its stat entry
    counts as dead for pipe purposes. Where ``/proc`` cannot be read, the
    existence probe decides.
    """

    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, ValueError, IndexError):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except (PermissionError, OSError):
            return True
        return True
    return state != "Z"


def _live_pipe_holders(pipe_inode: int) -> list[int]:
    """Live PIDs holding the pipe open now, zombies excluded."""

    return [pid for pid in _pipe_write_end_holders(pipe_inode) if _holder_alive(pid)]


def _ask_pipe_holders_to_close(pipe_inode: int) -> bool:
    """Ask live holders to die and wait out the TERM grace; True when none holds."""

    for pid in _live_pipe_holders(pipe_inode):
        with suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + _HOOK_ESCAPED_TERM_SECONDS
    while time.monotonic() < deadline:
        if not _live_pipe_holders(pipe_inode):
            return True
        time.sleep(_HOOK_COPY_QUANTUM_SECONDS)
    return not _live_pipe_holders(pipe_inode)


def _stop_pipe_holders(pipe_inode: int) -> None:
    """KILL live holders and wait out the death verify; survivors stay visible."""

    holders = _live_pipe_holders(pipe_inode)
    for pid in holders:
        with suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + _HOOK_ESCAPED_KILL_VERIFY_SECONDS
    while time.monotonic() < deadline:
        # F02 native trace: closing the pipe can precede PID death. Retain
        # identity before KILL, so the caller can verify and name that PID.
        if not any(_holder_alive(pid) for pid in holders):
            break
        time.sleep(_HOOK_COPY_QUANTUM_SECONDS)


def _stop_escaped_pipe_holders(process: subprocess.Popen[bytes]) -> tuple[list[int], list[int]]:
    """Stop hook descendants that escaped the process group holding the pipe.

    Up to ``_HOOK_ESCAPED_ROUNDS`` rounds of scan-ask-stop-verify: a TERM is
    asked first, then KILL for survivors, and a KILL counts only after the
    PID is verified dead (zombies excluded). A holder the scan missed or one
    forked mid-round is caught by the next round. Answers ``(stopped,
    survivors)``: survivors are named alive by the caller — the return never
    claims a live holder stopped.
    """
    assert process.stdout is not None
    try:
        pipe_inode = os.fstat(process.stdout.fileno()).st_ino
    except OSError:
        return [], []
    stopped: list[int] = []
    for _ in range(_HOOK_ESCAPED_ROUNDS):
        holders = _live_pipe_holders(pipe_inode)
        if not holders:
            return stopped, []
        _ask_pipe_holders_to_close(pipe_inode)
        if not _live_pipe_holders(pipe_inode):
            return stopped, []
        kill_set = _live_pipe_holders(pipe_inode)
        _stop_pipe_holders(pipe_inode)
        for pid in dict.fromkeys([*holders, *kill_set]):
            if pid not in stopped and not _holder_alive(pid):
                stopped.append(pid)
    return stopped, _live_pipe_holders(pipe_inode)


def _stop_hook_tree(process: subprocess.Popen[bytes]) -> None:
    """Stop the hook run with its children: ask first, then stop (MIK-R152 rule 6)."

    The term grace is spent waiting for the owned process GROUP to die, not
    just the direct child: a child that ignores TERM and keeps the output pipe
    open must not complete work after the bound. Past the grace the group is
    stopped and the direct child reaped.
    """
    with suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + _HOOK_TERM_GRACE_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None and _process_group_dead(process.pid):
            break
        time.sleep(_HOOK_COPY_QUANTUM_SECONDS)
    with suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def _recover_isolation_stash(repo: Path, before: _HookRunState) -> str | None:
    """Apply back a stash entry this run provably created; name anything older.

    Answers the entry left behind, or ``None`` when the stash is as found.
    An entry that was already there before the run is never touched: the
    refusal names it for the operator instead. Where taking our entry back
    fails and leaves newly unmerged index entries, those are reset to the
    HEAD version (worktree files untouched) so the caller's index restore can
    run; the leftover stash is still named.
    """
    top = _stash_top(repo)
    if top == before.stash:
        return None
    if top is not None and (before.stash is None or top != before.stash):
        if _stash_entry_is_ours(repo, top):
            applied = _git(repo, ["stash", "pop", "--quiet"])
            if applied.returncode == 0 and _stash_top(repo) == before.stash:
                return None
            newly_unmerged = [
                path for path in _unmerged_paths(repo) if path not in set(before.unmerged)
            ]
            if newly_unmerged:
                _require_git(repo, ["reset", "--quiet", "--", *newly_unmerged])
            return top
        return top
    return None
