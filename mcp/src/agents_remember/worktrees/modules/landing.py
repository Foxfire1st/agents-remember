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
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agents_remember.worktrees.worktree_contract import WorktreeContract

_PROBE_TIMEOUT_SECONDS = 8


def _landing_active(contract: WorktreeContract) -> bool:
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
    """
    if not branch or not repo.exists():
        return ("missing", None)
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={repo.as_posix()}",
                "ls-remote",
                "--heads",
                "origin",
                branch,
            ],
            cwd=repo,
            text=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ("missing", None)
    if result.returncode != 0:
        return ("missing", None)
    line = result.stdout.strip()
    if not line:
        return ("observed", None)
    return ("observed", line.split()[0])


def _pr_for(repo: Path, head: str) -> dict[str, str] | None:
    """Best-effort PR lookup via ``gh`` for the branch ``head``.

    ``None`` -> gh is absent/unauthed/errored (caller renders ``missing``); ``{}`` -> gh ran and
    there is no PR yet (caller renders ``planned``); otherwise the PR's number/state/url/base.
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
                "number,state,url,baseRefName",
                "--limit",
                "1",
            ],
            cwd=repo,
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


def _pr_ref(pr: dict[str, str] | None) -> list[dict[str, object]]:
    """Render the PR (and, when gh resolved it, its base ``origin-main``) from a best-effort lookup."""
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
    refs: list[dict[str, object]] = [
        {
            "kind": "pr",
            "label": f"PR #{number}" if number else "PR",
            "state": pr.get("state") or "open",
            "factState": "observed",
            "detail": pr.get("url") or None,
        }
    ]
    base = pr.get("base") or ""
    if base:
        refs.append(
            {
                "kind": "origin-main",
                "label": f"origin/{base}",
                "state": "merged" if pr.get("state") == "merged" else "tip",
                "factState": "observed",
                "detail": None,
            }
        )
    return refs


def landing_refs(contract: WorktreeContract) -> list[dict[str, object]] | None:
    """Observe the successful-landing arc (slice 5h), best-effort.

    ``None`` -> no landing key in the status payload (the node defaults to an empty ``landing``).
    Otherwise one dict per remote/PR participant, each carrying an honest ``factState``.
    """
    if not _landing_active(contract):
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

    refs.extend(_pr_ref(_pr_for(contract.code_repo_path, contract.code_source_branch)))
    return refs
