"""Authority-resolved identity of the candidate a review result was checked against.

MIK-R116 rule 5 (L116-F01): a same-round record-only result is admitted only on
the actual byte-identical candidate the round was recorded on. A caller-supplied
tree string is an assertion, not proof: the guard compares an identity the
product resolves itself through the task/contract authority — the candidate
worktree's HEAD tree, its staged and unstaged tracked modifications, and its
untracked entries. Two candidates share an identity only where Git would record
the same tree for them: a path's entry is taken WITHOUT following a link — a
link's entry is its kind and its target, a regular file's entry is its kind,
its executable bit and its content, and anything else neither is its own kind.
``None`` means unresolvable or unproven, never equality. Any failed component
read propagates as unproven: an unreadable marker is never digested into a
matchable identity, so a transient Git failure refuses a same-round record
(fail-closed) instead of risking false equality.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

_GIT_TIMEOUT_SECONDS = 60


def _git(root: Path, *args: str) -> bytes | None:
    """Run one read-only git command inside ``root``; ``None`` on any failure."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _head_tree(root: Path) -> bytes | None:
    """The HEAD tree when the lookup returns 0, unproven otherwise.

    MIK-R116 names no repository without a commit, and a task's worktree
    always has one: there is no 'no commit exists' state, and no state is
    ever read from the text of an error. With an unproven identity the tool
    answers that a round is to be begun, as rule 5 says for a changed
    candidate.
    """
    head = _git(root, "rev-parse", "HEAD^{tree}")
    if head is None:
        return None
    return head.strip()


def _fold_untracked_entry(root: Path, rel: str, digest: hashlib._Hash) -> bool:
    """Fold one untracked entry's representation, never following a link."""
    digest.update(b"\npath:")
    digest.update(rel.encode("utf-8", "surrogateescape"))
    try:
        mode = os.lstat(root / rel).st_mode
    except OSError:
        return False
    if stat.S_ISLNK(mode):
        return _fold_link_entry(root / rel, digest)
    if stat.S_ISREG(mode):
        return _fold_regular_entry(root / rel, mode, digest)
    for check, tag in (
        (stat.S_ISDIR, b"\nkind:dir"),
        (stat.S_ISFIFO, b"\nkind:fifo"),
        (stat.S_ISSOCK, b"\nkind:sock"),
    ):
        if check(mode):
            digest.update(tag)
            return True
    digest.update(b"\nkind:other")
    return True


def _fold_link_entry(full: Path, digest: hashlib._Hash) -> bool:
    """Fold a link's kind and target without following it."""
    try:
        target = os.readlink(full)
    except OSError:
        return False
    digest.update(b"\nkind:link\ntarget:")
    digest.update(target.encode("utf-8", "surrogateescape"))
    return True


def _fold_regular_entry(full: Path, mode: int, digest: hashlib._Hash) -> bool:
    """Fold a regular file's kind, executable bit and content."""
    digest.update(b"\nkind:file\nebit:1" if mode & 0o111 else b"\nkind:file\nebit:0")
    digest.update(b"\nhash:")
    try:
        with open(full, "rb") as handle:
            file_digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(65536), b""):
                file_digest.update(chunk)
        digest.update(file_digest.hexdigest().encode("ascii"))
    except OSError:
        return False
    return True


def _hash_untracked(root: Path, digest: hashlib._Hash) -> bool:
    """Fold every untracked entry's path and representation into ``digest``.

    No link is followed: a link's entry is its kind and its target, a regular
    file's entry is its kind, its executable bit and its content, and anything
    else neither is its own kind. Returns ``False`` when any component read
    fails: the caller propagates the whole identity as unproven rather than
    digesting an unreadable marker.
    """
    status = _git(root, "status", "--porcelain=v1", "-uall", "-z")
    if status is None:
        return False
    for entry in status.split(b"\0"):
        if not entry.startswith(b"??"):
            continue
        rel = entry[3:].decode("utf-8", "surrogateescape")
        if not _fold_untracked_entry(root, rel, digest):
            return False
    return True


def _candidate_root(root: Path | None) -> Path | None:
    """Resolve ``root`` to a directory, or ``None`` when there is none."""
    if root is None:
        return None
    try:
        resolved = root.resolve()
    except OSError:
        return None
    if not resolved.is_dir():
        return None
    return resolved


def resolve_candidate_identity(root: Path | None) -> str | None:
    """Resolve the current candidate identity for ``root``.

    Returns ``candidate-v1:<sha256>`` over the HEAD tree, staged and unstaged
    tracked modifications, and untracked file bytes, or ``None`` when the
    identity cannot be proven (no root, no repository, or any Git/read
    failure). Callers treat ``None`` as unproven, never as unchanged.
    """
    resolved = _candidate_root(root)
    if resolved is None:
        return None
    head = _head_tree(resolved)
    if head is None:
        return None
    digest = hashlib.sha256()
    digest.update(b"head-tree:")
    digest.update(head)
    for label, args in (
        (b"unstaged:", ("diff", "--no-color", "--no-ext-diff")),
        (b"staged:", ("diff", "--cached", "--no-color", "--no-ext-diff")),
    ):
        diff = _git(resolved, *args)
        if diff is None:
            return None
        digest.update(b"\n" + label)
        digest.update(diff)
    digest.update(b"\nuntracked:")
    if not _hash_untracked(resolved, digest):
        return None
    return "candidate-v1:" + digest.hexdigest()


__all__ = ["resolve_candidate_identity"]
