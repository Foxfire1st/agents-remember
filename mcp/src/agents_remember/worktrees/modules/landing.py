"""Successful-landing arc observation for the Engine Room (slice 5h).

The cockpit's landing tail (T13-T17) draws a worktree retiring cleanly back into the official
line: the source branch advancing on origin, the PR opening then merging, the memory main carried
over. Those refs live on *remotes the worktree projection has no node for*, so -- unlike the
fetch-free :func:`agents_remember.worktrees.modules.guidance.base_freshness` drift check --
observing them needs a real probe. This module is that probe: best-effort, honest, and gated to the
landing window so the status poll stays network-free during the build phase.

Honesty (slice 5h; 5f sec 2): a ref we could not observe is reported ``planned`` (expected but not
yet) or ``missing`` (the probe could not run), never invented. Branch tips come from
``git ls-remote`` (reliable); PR state from a best-effort ``gh`` shell-out -- the package's first --
which degrades to ``missing`` when ``gh`` is absent or unauthed.

Slice 5l P2 hardens the probe to *follow* a real remote landing: ``origin/main`` (the protected
target) is now probed directly via ``ls-remote`` -- visible across the whole landing window, not
only inferred from a PR's base once gh resolves one -- and the PR ref carries gh's own open/merge
timestamps. The dashboard's bounded landing-state refresher now invokes this probe outside the
recurring projection tick and publishes the latest exact-contract observation.
"""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import cast

from agents_remember.kernel.git_command import GitRunnerOptions, git_environment, run_git
from agents_remember.worktrees.worktree_contract import WorktreeContract

_PROBE_TIMEOUT_SECONDS = 8


_LS_REMOTE_SWEEP: ContextVar[dict[tuple[str, str], object] | None] = ContextVar(
    "ls_remote_sweep", default=None
)
_LS_REMOTE_LOCK = threading.Lock()


class _RemotePending:
    """One in-flight sweep computation another thread is already running (F12)."""

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: object = None


def _sweep_memo[R](key: tuple[str, str], compute: Callable[[], R]) -> R:
    """Run ``compute`` once per sweep for ``key``, sharing the result across threads.

    Identical remote commands from different contracts in one sweep run once; the owner
    computes while the other threads wait, and every waiter shares the owner's answer --
    failures included. The next sweep computes again, because a remote moves. ``compute``
    must not raise: expected remote failures are answers, and anything unexpected escapes
    to the observation boundary while the wait stays bounded by the probe timeout.
    """

    scope = _LS_REMOTE_SWEEP.get()
    if scope is None:
        return compute()
    with _LS_REMOTE_LOCK:
        if key in scope:
            seen = scope[key]
            mine = False
            pending = seen if isinstance(seen, _RemotePending) else None
        else:
            pending = _RemotePending()
            scope[key] = pending
            mine = True
    if not mine:
        if isinstance(seen, _RemotePending):
            if not seen.event.wait(timeout=_PROBE_TIMEOUT_SECONDS + 10):
                raise RuntimeError(f"landing sweep computation for {key[1]} timed out")
            return cast("R", seen.result)
        return cast("R", seen)
    result = compute()
    assert pending is not None
    with _LS_REMOTE_LOCK:
        scope[key] = result
        pending.result = result
        pending.event.set()
    return result


@contextmanager
def sweep_remote_observations() -> Iterator[None]:
    """Within the block, one ``ls-remote`` per repository and ref runs once (F12).

    A sweep's contracts usually watch the same origin branches; without the scope each
    contract probes them again. The scope lives and dies with the sweep: the next sweep
    probes again, because a remote moves. Failures are remembered too -- a dead remote
    fails once per sweep, not once per contract -- and anything unrememberable (no
    branch, no repository) never enters the scope. Scopes nest like the other request
    scopes: an inner scope joins the open one.
    """

    if _LS_REMOTE_SWEEP.get() is not None:
        yield
        return
    token = _LS_REMOTE_SWEEP.set({})
    try:
        yield
    finally:
        _LS_REMOTE_SWEEP.reset(token)


def current_remote_scope() -> dict[tuple[str, str], object] | None:
    """The open sweep's remote-probe table, if any, for handoff into worker threads."""

    return _LS_REMOTE_SWEEP.get()


def observe_with_remote_scope[R](
    observe: Callable[[WorktreeContract], R],
    scope: dict[tuple[str, str], object] | None,
    contract: WorktreeContract,
) -> R:
    """Run ``observe`` with the sweep's probe table joined (F12).

    Landing observations run in worker threads, where the sweep's context does not reach;
    the caller captures :func:`current_remote_scope` in the event loop and rejoins it here
    so one repository and ref still probes once per sweep across threads.
    """

    if scope is None:
        return observe(contract)
    token = _LS_REMOTE_SWEEP.set(scope)
    try:
        return observe(contract)
    finally:
        _LS_REMOTE_SWEEP.reset(token)


def landing_active(contract: WorktreeContract) -> bool:
    """The landing arc is only meaningful from closeout-completed onward.

    Before closeout there is nothing pushed, merged, or carried over to observe, and gating here
    keeps the polling status payload network-free for the whole build phase.
    """
    return (
        contract.closeout_status == "completed"
        or contract.integration_status not in ("", "not-started")
        or contract.cleanup not in ("", "pending")
    )


def _remote_branch(repo: Path, branch: str) -> tuple[str, str | None]:
    """``(factState, sha)`` for ``origin/<branch>``.

    ``("observed", sha)`` the branch is on origin; ``("observed", None)`` origin was reachable but
    the branch is not there yet; ``("missing", None)`` the probe could not run (offline / no origin).
    Within a sweep scope one repository and ref probe once, whatever the answer (F12).
    """
    if not branch or not repo.exists():
        return ("missing", None)
    key = (repo.as_posix(), branch)
    return _sweep_memo(key, lambda: _probe_remote_branch(repo, branch))


def _probe_remote_branch(repo: Path, branch: str) -> tuple[str, str | None]:
    """One ``ls-remote`` for ``origin/<branch>``: answers, never raises for remote failure."""

    try:
        result = run_git(
            repo,
            ["ls-remote", "--heads", "origin", branch],
            GitRunnerOptions(timeout=_PROBE_TIMEOUT_SECONDS),
        )
    except (OSError, subprocess.SubprocessError):
        return ("missing", None)
    if result.returncode != 0:
        return ("missing", None)
    line = result.stdout.strip()
    if not line:
        return ("observed", None)
    return ("observed", line.split()[0])


def _default_branch(repo: Path) -> str:
    """origin's default branch (e.g. ``main``) via the remote HEAD symref; ``"main"`` on any failure.

    Used to name the protected target when no PR has resolved a base yet, so ``origin/main`` is
    observable from the start of the landing window (slice 5l P2). ``ls-remote`` queries the remote
    directly, so no ``fetch`` is needed and a stale local tracking ref can never mislead it.
    """
    if not repo.exists():
        return "main"
    # One symref probe per repository per sweep (F12): every contract names the same
    # protected target, so the first contract's answer serves the whole sweep.
    return _sweep_memo((repo.as_posix(), "\0default-branch"), lambda: _probe_default_branch(repo))


def _probe_default_branch(repo: Path) -> str:
    """One ``ls-remote --symref`` for ``origin/HEAD``; ``"main"`` on any failure."""

    try:
        result = run_git(
            repo,
            ["ls-remote", "--symref", "origin", "HEAD"],
            GitRunnerOptions(timeout=_PROBE_TIMEOUT_SECONDS),
        )
    except (OSError, subprocess.SubprocessError):
        return "main"
    if result.returncode != 0:
        return "main"
    for line in result.stdout.splitlines():
        if line.startswith("ref:") and "HEAD" in line:
            # "ref: refs/heads/main\tHEAD" -> "main"
            return line.split()[1].rsplit("/", 1)[-1] or "main"
    return "main"


def _pr_for(repo: Path, head: str) -> dict[str, str] | None:
    """Best-effort PR lookup via ``gh`` for the branch ``head``.

    ``None`` -> gh is absent/unauthed/errored (caller renders ``missing``); ``{}`` -> gh ran and
    there is no PR yet (caller renders ``planned``); otherwise the PR's number/state/url/base plus
    gh's own ``createdAt``/``mergedAt`` timestamps (slice 5l P2) so the open->merged transition
    carries its timing.
    """
    if not head or not repo.exists():
        return None
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--head",
                head,
                "--state",
                "all",
                "--json",
                "number,state,url,baseRefName,createdAt,mergedAt",
                "--limit",
                "1",
            ],
            cwd=repo,
            # `gh` is not git, but it resolves the repository *through* git, so an inherited
            # GIT_DIR would have it list another repository's pull requests under this
            # worktree's branch name. `cwd=repo` does not outrank the selectors for gh any
            # more than it does for git, and this is the package's only non-git spawn that
            # reads a repository, so it takes the same scrubbed environment.
            env=git_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        rows = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not rows:
        return {}
    row = rows[0]
    return {
        "number": str(row.get("number", "")),
        "state": str(row.get("state", "")).lower(),
        "url": str(row.get("url", "")),
        "base": str(row.get("baseRefName", "")),
        # gh returns JSON null for mergedAt on an open PR -> coerce to "" (not "None").
        "createdAt": str(row.get("createdAt") or ""),
        "mergedAt": str(row.get("mergedAt") or ""),
    }


def _branch_ref(kind: str, branch: str, fact: str, sha: str | None) -> dict[str, object]:
    state = "pushed" if sha else ("planned" if fact == "observed" else "unknown")
    return {
        "kind": kind,
        "label": f"origin/{branch}",
        "state": state,
        "factState": fact,
        "detail": sha[:10] if sha else None,
    }


def _main_ref(repo: Path, pr: dict[str, str] | None) -> dict[str, object]:
    """The protected target ``origin/<base>``, probed directly (slice 5l P2).

    Visible across the whole landing window -- not only after ``gh`` resolves a PR base -- so the
    dashboard can show the line a worktree is landing into before any PR exists, even when ``gh`` is
    unavailable (the probe is ``ls-remote``, independent of gh). ``state`` tracks whether *this* work
    has landed (the PR merged), not merely whether ``origin/main`` exists, so a pre-merge target
    reads honestly as ``planned`` rather than a misleading ``tip``/done; the current main tip rides
    along in ``detail``.
    """
    base = (pr or {}).get("base") or _default_branch(repo)
    fact, sha = _remote_branch(repo, base)
    if pr and pr.get("state") == "merged":
        state = "merged"
    elif fact == "observed":
        state = "planned"
    else:
        state = "unknown"
    return {
        "kind": "origin-main",
        "label": f"origin/{base}",
        "state": state,
        "factState": fact,
        "detail": sha[:10] if sha else None,
    }


def _pr_ref(pr: dict[str, str] | None) -> list[dict[str, object]]:
    """Render the PR participant from a best-effort lookup (origin-main is now its own direct probe)."""
    if pr is None:
        return [
            {
                "kind": "pr",
                "label": "PR",
                "state": "unknown",
                "factState": "missing",
                "detail": "gh unavailable",
            }
        ]
    if not pr:
        return [
            {
                "kind": "pr",
                "label": "PR",
                "state": "planned",
                "factState": "observed",
                "detail": "no PR opened yet",
            }
        ]
    number = pr.get("number") or ""
    merged = pr.get("state") == "merged"
    # gh's own milestone time: when it merged, else when it opened (slice 5l P2).
    at = (pr.get("mergedAt") or "") if merged else (pr.get("createdAt") or "")
    return [
        {
            "kind": "pr",
            "label": f"PR #{number}" if number else "PR",
            "state": pr.get("state") or "open",
            "factState": "observed",
            "detail": pr.get("url") or None,
            "at": at or None,
        }
    ]


def landing_refs(contract: WorktreeContract) -> list[dict[str, object]] | None:
    """Observe the successful-landing arc (slice 5h; hardened 5l P2), best-effort.

    ``None`` -> no landing key in the status payload (the node defaults to an empty ``landing``).
    Otherwise one dict per remote/PR participant, each carrying an honest ``factState``. Called by
    the bounded background refresher while the landing window is active; the recurring projection
    only consumes its latest immutable result.
    """
    if not landing_active(contract):
        return None

    refs: list[dict[str, object]] = [
        _branch_ref(
            "origin-feat",
            contract.code_source_branch,
            *_remote_branch(contract.code_repo_path, contract.code_source_branch),
        )
    ]

    if contract.memory_mode == "external" and contract.memory_repo_path is not None:
        mem = contract.memory_source_branch
        refs.append(
            _branch_ref("origin-mem-main", mem, *_remote_branch(contract.memory_repo_path, mem))
        )

    # The PR drives both its own ref and the protected target's merged state, so look it up once.
    pr = _pr_for(contract.code_repo_path, contract.code_source_branch)
    refs.append(_main_ref(contract.code_repo_path, pr))
    refs.extend(_pr_ref(pr))
    return refs


def unobserved_landing_refs(contract: WorktreeContract) -> list[dict[str, object]] | None:
    """Expected landing participants before a remote observation is available.

    This is the network-free startup/fallback shape consumed by the recurring projection. Every
    participant is explicit ``missing``/``unknown`` truth; the background landing refresher replaces
    it with observed facts once its first exact-contract probe completes.
    """
    if not landing_active(contract):
        return None
    refs: list[dict[str, object]] = [
        {
            "kind": "origin-feat",
            "label": f"origin/{contract.code_source_branch}",
            "state": "unknown",
            "factState": "missing",
            "detail": "observation pending",
        }
    ]
    if contract.memory_mode == "external" and contract.memory_repo_path is not None:
        refs.append(
            {
                "kind": "origin-mem-main",
                "label": f"origin/{contract.memory_source_branch}",
                "state": "unknown",
                "factState": "missing",
                "detail": "observation pending",
            }
        )
    refs.extend(
        [
            {
                "kind": "origin-main",
                "label": "origin/main",
                "state": "unknown",
                "factState": "missing",
                "detail": "observation pending",
            },
            {
                "kind": "pr",
                "label": "PR",
                "state": "unknown",
                "factState": "missing",
                "detail": "observation pending",
            },
        ]
    )
    return refs
