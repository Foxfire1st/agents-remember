"""The sentence a landing answer carries when a landed leaf's history holds no review fact.

A route that answers from a memory commit it published earlier -- a resumed closeout, or a direct
landing whose answer is built after its journal completed -- no longer holds the closing it made.
It reads the leaf's latest history file in that commit instead: a closed file without a review
fact means no passed memory review was recorded when the leaf landed, so the revisions it wrote
stay proposed. A commit without the owner's history file, or one whose file is
open or unreadable, is no claim either way and gives no sentence.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.models.knowledge_files.documents import KNOWLEDGE_ROOT, owner_history_attempt
from agents_remember.worktrees.review_fact_source import NO_PASS_SENTENCE

_HISTORY_DIRECTORY = f"{KNOWLEDGE_ROOT}/history"


def committed_review_note(repository: Path, commit: str, owner: str) -> str:
    """:data:`NO_PASS_SENTENCE` when ``owner``'s latest history file at ``commit`` is closed
    without a review fact; otherwise the empty string."""

    if not owner or not commit:
        return ""
    listed = _git(repository, ["ls-tree", "--name-only", f"{commit}:{_HISTORY_DIRECTORY}"])
    attempts = {
        attempt: name
        for name in (listed or "").splitlines()
        if (attempt := owner_history_attempt(f"{_HISTORY_DIRECTORY}/{name}", owner))
    }
    if not attempts:
        return ""
    text = _git(repository, ["show", f"{commit}:{_HISTORY_DIRECTORY}/{attempts[max(attempts)]}"])
    try:
        document = json.loads(text) if text is not None else None
    except json.JSONDecodeError:
        return ""
    if not isinstance(document, dict) or document.get("closed") is not True:
        return ""
    return "" if "review" in document else NO_PASS_SENTENCE


def _git(repository: Path, args: list[str]) -> str | None:
    try:
        result = run_git(repository, args, GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS))
    except (ValueError, subprocess.SubprocessError, OSError):
        return None
    return result.stdout if result.returncode == 0 else None
