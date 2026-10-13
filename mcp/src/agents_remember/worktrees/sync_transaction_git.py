"""Exact Git mutations and proofs for resumable code and memory-content sync.

The memory side excludes its root memory.md cache from candidate, conflict, and merge-tree
identity. Code-side files keep ordinary Git semantics, including a file named memory.md.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from agents_remember.kernel.git_command import read_git_blob_bytes, run_git
from agents_remember.kernel.git_preparation import GitPreparationError
from agents_remember.kernel.memory_cache import prepare_memory_cache, refresh_memory_cache
from agents_remember.worktrees.knowledge_crossing import (
    CrossingSyncError,
    apply_crossing,
    close_crossing_history,
    crossing_plan,
    merge_base,
    merge_structure,
    write_crossing_report,
)
from agents_remember.worktrees.knowledge_validation import PairedCode, memory_commit_refusal
from agents_remember.worktrees.modules.git import (
    branch_commit,
    current_branch,
    head_commit,
    is_ancestor,
    repository_identity,
)
from agents_remember.worktrees.services import CrossingPlanView
from agents_remember.worktrees.sync_transaction_state import SyncSideRecord

logger = logging.getLogger(__name__)

#: How many paths a refusal names inline; the full set always reaches the log.
MAX_NAMED_SYNC_PATHS = 10


@dataclass(frozen=True)
class ExpectedReappliedContent:
    """What the reapply of one parked path onto the carried head gives.

    ``kind`` is ``"bytes"`` (the worktree must hold ``content``), ``"absent"``
    (the path must be gone), ``"uncomputable"`` (no construction proves it: the
    proof fails the path and the cancel leads back), or ``"unreadable"`` (the
    live worktree entry, never an expectation: a directory or device where a
    file belongs, which matches nothing).
    """

    kind: str
    content: bytes = b""


@dataclass(frozen=True)
class CancelTreeAssessment:
    """Whether a cancel may discard one side's tree before rolling its head back.

    ``later_work`` names dirty paths no stash and no commit holds: the cancel is
    refused while it is non-empty. ``unproven_paths`` names dirty paths no
    reference shows to be the transaction's own after an interrupted step: the
    cancel is refused while it is non-empty, and the refusal says so instead of
    claiming later work. ``owned_untracked`` names untracked paths the
    recorded stash holds, removed explicitly because ``reset --hard`` keeps them.
    ``needs_discard`` says owned dirt is present at all. ``merge_fault`` holds
    git's words where it could not make the reapply's merge again: a path both
    sides changed then cannot be shown to be the reapply's own and stands in
    ``later_work``, and the refusal says why.
    """

    later_work: tuple[str, ...] = ()
    owned_untracked: tuple[str, ...] = ()
    unproven_paths: tuple[str, ...] = ()
    needs_discard: bool = False
    merge_fault: str = ""


#: Blob modes whose reapply the proof computes; a symlink or submodule change the
#: proof cannot compute fails its path instead of passing it.
_REGULAR_BLOB_MODES = frozenset({"100644", "100755"})

#: A full object name, as ``git merge-tree`` writes the merged tree's.
_OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


class SyncGitProofError(RuntimeError):
    """Live Git state cannot be attributed exactly to the journaled sync."""


class SyncKnowledgeValidationError(SyncGitProofError):
    """The knowledge validator (MIK-R22) refuses the staged memory merge; it stays staged."""


@dataclass(frozen=True)
class SideMergeOutcome:
    """What one side's merge attempt reached.

    ``state`` is ``completed`` or ``resolution-required``; ``conflicts`` names the paths still
    unmerged; ``message`` is Git's own text for a conflict. ``crossing_report`` is the path of the
    structural knowledge merge's report, when it left items for the curator or converted a side.
    """

    state: str
    conflicts: tuple[str, ...] = ()
    message: str = ""
    crossing_report: str = ""


def read_ref(repository: Path, ref: str) -> str | None:
    valid = run_git(repository, ["check-ref-format", ref])
    if valid.returncode != 0:
        raise SyncGitProofError(valid.stderr.strip() or f"invalid sync authority ref {ref!r}")
    result = run_git(
        repository,
        ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"],
    )
    if result.returncode == 0:
        return result.stdout.strip()
    if result.returncode == 1:
        return None
    raise SyncGitProofError(result.stderr.strip() or f"could not inspect sync authority ref {ref}")


def create_pinned_ref(repository: Path, ref: str, commit: str) -> None:
    current = read_ref(repository, ref)
    if current is not None:
        if current != commit:
            raise SyncGitProofError(f"sync authority ref {ref} pins another commit")
        return
    zeros = "0" * len(commit)
    result = run_git(repository, ["update-ref", ref, commit, zeros])
    if result.returncode != 0:
        raise SyncGitProofError(result.stderr.strip() or f"could not create {ref}")


def delete_pinned_ref(repository: Path, ref: str, expected: str) -> None:
    current = read_ref(repository, ref)
    if current is None:
        return
    if current != expected:
        raise SyncGitProofError(f"sync authority ref {ref} changed before cleanup")
    result = run_git(repository, ["update-ref", "-d", ref, expected])
    if result.returncode != 0:
        raise SyncGitProofError(result.stderr.strip() or f"could not delete {ref}")


def ensure_temporary_worktree(side: SyncSideRecord) -> None:
    if not side.temporary:
        require_side_checkout(side)
        return
    repository = Path(side.repository)
    worktree = Path(side.worktree)
    if worktree.exists():
        require_side_checkout(side)
        return
    worktree.parent.mkdir(parents=True, exist_ok=True)
    result = run_git(repository, ["worktree", "add", str(worktree), side.workBranch])
    if result.returncode != 0:
        raise SyncGitProofError(
            result.stderr.strip() or f"could not create temporary {side.side} sync worktree"
        )
    require_side_checkout(side)


def require_side_checkout(side: SyncSideRecord) -> None:
    repository = Path(side.repository)
    worktree = Path(side.worktree)
    if repository_identity(worktree) != repository_identity(repository):
        raise SyncGitProofError(f"{side.side} sync worktree changed repository identity")
    if current_branch(worktree) != side.workBranch:
        raise SyncGitProofError(f"{side.side} sync worktree changed its journaled branch")


def remove_temporary_worktree(side: SyncSideRecord) -> None:
    if not side.temporary:
        return
    repository = Path(side.repository)
    worktree = Path(side.worktree)
    if not worktree.exists():
        return
    require_side_checkout(side)
    if git_status(side):
        raise SyncGitProofError(f"temporary {side.side} sync worktree is not clean")
    discard_memory_cache_changes(side)
    result = run_git(repository, ["worktree", "remove", str(worktree)])
    if result.returncode != 0:
        raise SyncGitProofError(
            result.stderr.strip() or f"could not remove temporary {side.side} sync worktree"
        )


def git_status(side: SyncSideRecord) -> str:
    result = run_git(Path(side.worktree), ["status", "--porcelain", *_content_pathspec(side)])
    if result.returncode != 0:
        raise SyncGitProofError(result.stderr.strip() or "could not read sync worktree status")
    return result.stdout.strip()


def _content_spec_paths(side_name: str) -> tuple[str, ...]:
    if side_name == "memory":
        return (".", ":!memory.md")
    return (".",)


def _dirty_names(worktree: Path, side_name: str) -> tuple[str, ...]:
    """Every dirty path one worktree holds, untracked included, NUL-parsed."""

    result = run_git(
        worktree, ["status", "--porcelain", "-z", "-uall", *_content_spec_paths(side_name)]
    )
    if result.returncode != 0:
        raise SyncGitProofError(result.stderr.strip() or "could not read sync worktree status")
    entries = result.stdout.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status = entry[:2]
        paths.append(entry[3:])
        if "R" in status or "C" in status:
            index += 1
    return tuple(paths)


def _untracked_names(worktree: Path, side_name: str) -> frozenset[str]:
    """Every untracked path one worktree holds, minus the derived memory cache."""

    result = run_git(worktree, ["status", "--porcelain", "-z", "-uall"])
    if result.returncode != 0:
        raise SyncGitProofError(result.stderr.strip() or "could not read sync worktree status")
    entries = result.stdout.split("\0")
    names: set[str] = set()
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status = entry[:2]
        path = entry[3:]
        if "R" in status or "C" in status:
            index += 1
            continue
        if status == "??" and not (side_name == "memory" and path == "memory.md"):
            names.add(path)
    return frozenset(names)


def _fingerprint_at(worktree: Path, path: str) -> str:
    """One path's state at one worktree: worktree bytes plus full index entries."""

    actual = _entry_content_at(worktree, path)
    if actual.kind == "bytes":
        worktree_part = f"bytes:{hashlib.sha256(actual.content).hexdigest()}"
    else:
        worktree_part = actual.kind
    listed = run_git(worktree, ["ls-files", "-s", "-z", "--", path])
    if listed.returncode != 0:
        raise SyncGitProofError(f"could not read the index state of {path}")
    index_part = hashlib.sha256(listed.stdout.encode("utf-8", "surrogateescape")).hexdigest()
    return f"{worktree_part}:{index_part}"


def worktree_dirty_paths(side: SyncSideRecord) -> tuple[str, ...]:
    """Every dirty path the worktree holds, untracked included.

    NUL separation is the only porcelain form that never quotes a path, so the reported
    paths are exactly the paths git reported.
    """

    return _dirty_names(Path(side.worktree), side.side)


def park_worktree_wip(side: SyncSideRecord, *, message: str) -> str:
    """Stash the exact worktree candidate with its untracked files; return its identity."""

    worktree = Path(side.worktree)
    require_side_checkout(side)
    discard_memory_cache_changes(side)
    result = run_git(
        worktree,
        ["stash", "push", "--include-untracked", "--message", message, *_content_pathspec(side)],
    )
    if result.returncode != 0:
        raise SyncGitProofError(
            result.stderr.strip() or result.stdout.strip() or "git stash push failed"
        )
    if git_status(side):
        raise SyncGitProofError("git stash push left the sync worktree dirty")
    parked = run_git(worktree, ["rev-parse", "--verify", "refs/stash^{commit}"])
    if parked.returncode != 0:
        raise SyncGitProofError("git stash push created no stash entry")
    return parked.stdout.strip()


def apply_parked_wip(side: SyncSideRecord) -> tuple[str, tuple[str, ...]]:
    """Reapply the parked content and retain every genuine content conflict."""

    worktree = Path(side.worktree)
    require_side_checkout(side)
    clean_before = not git_status(side)
    discard_memory_cache_changes(side)
    result = run_git(worktree, ["stash", "apply", "--quiet", side.wipStash])
    raw_conflicts = unmerged_paths(worktree)
    cache_conflict = side.side == "memory" and raw_conflicts == ("memory.md",)
    if side.side == "memory" and "memory.md" in raw_conflicts:
        _remove_memory_cache_from_index(side)
    conflicts = content_conflicts(side)
    if result.returncode == 0 and not conflicts:
        _refresh_memory_cache(side)
        return "applied", ()
    if conflicts:
        return "conflict", conflicts
    if (
        result.returncode == 1
        and cache_conflict
        and clean_before
        and prove_parked_wip_restored(side, head_commit(worktree))
    ):
        discard_memory_cache_changes(side)
        _refresh_memory_cache(side)
        return "applied", ()
    raise SyncGitProofError(
        result.stderr.strip() or result.stdout.strip() or "the parked worktree WIP did not reapply"
    )


def prove_parked_wip_restored(side: SyncSideRecord, carried_head: str) -> bool:
    """Prove the parked candidate is back in the worktree, not silently lost.

    For each parked path the proof establishes that the worktree holds what the
    reapply of the parked change onto the carried head gives. An edit the
    carried line already holds is restored; a parked path whose content is in
    neither the worktree nor the carried line fails it.
    """

    return not parked_wip_failures(side, carried_head)


def parked_wip_failures(side: SyncSideRecord, carried_head: str) -> tuple[str, ...]:
    """The parked paths whose bytes the worktree does not hold.

    A path fails when the worktree differs from the reapplied content, and when
    no construction computes that content (a file type git cannot merge, a path
    removed on one side, a merge git cannot make again): both fail the proof and
    the cancel leads back. Every path the stash holds is proved, including
    entries beyond the journal sample. An unreadable stash inventory raises
    instead of passing on the sample: the sample names failures, it never passes
    a proof.
    """

    failures: list[str] = []
    merge = _ReapplyMerge(side, carried_head)
    for path in stashed_parked_paths(side):
        expected = expected_reapplied_content(side, path, carried_head, merge)
        if expected.kind == "uncomputable":
            failures.append(path)
            continue
        actual = worktree_entry_content(side, path)
        if (actual.kind, actual.content) != (expected.kind, expected.content):
            failures.append(path)
    if merge.fault:
        logger.error(
            "sync %s proof could not make the reapply's merge again: %s", side.side, merge.fault
        )
    return tuple(failures)


def reapply_merge_fault(side: SyncSideRecord, carried_head: str, paths: tuple[str, ...]) -> str:
    """Git's words where the proof of these paths needed a merge git could not make.

    Empty where git made the merge, and where none of the paths asked for one:
    the failed proof says why a path both sides changed could not be computed.
    """

    merge = _ReapplyMerge(side, carried_head)
    with contextlib.suppress(SyncGitProofError, GitPreparationError):
        for path in paths:
            expected_reapplied_content(side, path, carried_head, merge)
    return merge.fault


def named_merge_rules(side: SyncSideRecord, paths: tuple[str, ...]) -> dict[str, str]:
    """The merge the carried head's attributes prescribe for each path that has one.

    A failed proof names the rule beside its path, so a path git merges by a
    driver of its own is read as such. Attributes git cannot read name nothing.
    """

    if not paths:
        return {}
    listed = run_git(
        Path(side.worktree), ["check-attr", "--source=HEAD", "-z", "merge", "--", *paths]
    )
    if listed.returncode != 0:
        return {}
    cells = listed.stdout.split("\0")
    rules = zip(cells[0::3], cells[2::3], strict=False)
    return {path: rule for path, rule in rules if rule != "unspecified"}


class _ReapplyMerge:
    """Git's own merge of the parked candidate onto the carried head.

    The reapply is ``git stash apply`` in the side's worktree: git merges the
    stash onto the head with the stash's parent as base, each path by the driver
    the head's attributes name for it and the head's side first. ``git
    merge-tree`` makes that merge again without an index or a checkout. It runs
    in that same worktree, so the configuration git's merge reads there (a
    driver's program, the rename and renormalize settings, also where they are
    set for that worktree alone) is the reapply's; the attributes are the carried
    head's. One setting the reapply reads and ``merge-tree`` does not, so the
    proof hands it over: ``diff.algorithm``. Measured on git 2.43.0, 2.45.4,
    2.47.3, 2.49.1, 2.52.0 and 2.55.0: the last two follow it, the older ones
    merge with histogram diffs whatever they are told, and on each the reapply
    and ``merge-tree`` do the same.

    There is one merge and one expected content per path. Where git cannot make
    the merge, ``fault`` holds its words and no path has an expected content:
    the proof never merges by a construction of its own. The merge is made once
    per proof, and only when a path changed on both sides asks for it. Its only
    writes are the merge's own objects, the ones a reapply writes: no ref names
    them, and git's garbage collection removes them.
    """

    def __init__(self, side: SyncSideRecord, carried_head: str) -> None:
        self._side = side
        self._carried_head = carried_head
        self._asked = False
        self._tree: str | None = None
        self._conflicted: frozenset[str] = frozenset()
        self.fault = ""

    def content(self, path: str) -> ExpectedReappliedContent:
        """One both-changed path's merged content; uncomputable where git gives none."""

        if not self._asked:
            self._asked = True
            self._make()
        if self._tree is None or path in self._conflicted:
            return ExpectedReappliedContent("uncomputable")
        merged = _spec_content(Path(self._side.repository), (f"{self._tree}:{path}",))
        if merged is None:
            return ExpectedReappliedContent("uncomputable")
        return ExpectedReappliedContent("bytes", merged)

    def _make(self) -> None:
        """Make the merge where the reapply ran, under the diff algorithm it read."""

        worktree = Path(self._side.worktree)
        configured = run_git(worktree, ["config", "--get", "diff.algorithm"])
        if configured.returncode not in {0, 1}:
            self.fault = _git_words(configured, "git config --get diff.algorithm")
            return
        algorithm = configured.stdout.strip() if configured.returncode == 0 else ""
        merged = run_git(
            worktree,
            [
                f"--attr-source={self._carried_head}",
                "merge-tree",
                "--write-tree",
                "--no-messages",
                "--name-only",
                "-z",
                *([f"-Xdiff-algorithm={algorithm}"] if algorithm else []),
                f"--merge-base={self._side.wipStash}^1",
                self._carried_head,
                self._side.wipStash,
            ],
        )
        cells = [cell for cell in merged.stdout.split("\0") if cell]
        if merged.returncode in {0, 1} and cells and _OBJECT_ID.fullmatch(cells[0]):
            self._tree, self._conflicted = cells[0], frozenset(cells[1:])
        else:
            self.fault = _git_words(merged, "git merge-tree")


def _git_words(result: subprocess.CompletedProcess[str], command: str) -> str:
    """One line for a refusal: the command, its exit code and git's first words."""

    lines = (result.stderr.strip() or result.stdout.replace("\0", " ").strip()).splitlines()
    said = f": {lines[0].strip()}" if lines else ""
    return f"{command} ended with {result.returncode}{said}"


def expected_reapplied_content(
    side: SyncSideRecord, path: str, carried_head: str, merge: _ReapplyMerge | None = None
) -> ExpectedReappliedContent:
    """What the reapply of one parked path onto the carried head gives.

    ``merge`` is git's merge of the candidate onto that head; the paths of one
    proof share it, so it is made once.
    """

    repository = Path(side.repository)
    parked_rev, parked = _parked_path_content(side, path)
    base = _spec_content(repository, (f"{side.preSyncHead}:{path}",))
    carried = _spec_content(repository, (f"{carried_head}:{path}",))
    holders: list[tuple[str, bool]] = [
        (side.preSyncHead, base is not None),
        (carried_head, carried is not None),
    ]
    if parked_rev is not None:
        holders.append((parked_rev, parked is not None))
    if not _regular_modes(repository, path, holders):
        return ExpectedReappliedContent("uncomputable")
    if base is None:
        return _expected_added_path(parked, carried)
    if parked is None:
        return _expected_deleted_path(base, carried)
    if carried is None:
        return ExpectedReappliedContent("uncomputable")
    contents = (parked, base, carried)
    return _expected_changed_path(path, contents, merge or _ReapplyMerge(side, carried_head))


def _expected_deleted_path(base: bytes, carried: bytes | None) -> ExpectedReappliedContent:
    """What the reapply of a parked deletion gives."""

    if carried is None or carried == base:
        return ExpectedReappliedContent("absent")
    return ExpectedReappliedContent("uncomputable")


def _expected_changed_path(
    path: str, contents: tuple[bytes, bytes, bytes], merge: _ReapplyMerge
) -> ExpectedReappliedContent:
    """What the reapply of a parked change to a carried-changed file gives.

    Git merges content only where both sides changed the file differently: that
    merge is git's own, and where git cannot make it the path is uncomputable.
    """

    parked, base, carried = contents
    if parked in {base, carried}:
        return ExpectedReappliedContent("bytes", carried)
    if carried == base:
        return ExpectedReappliedContent("bytes", parked)
    return merge.content(path)


def parked_path_content(side: SyncSideRecord, path: str) -> ExpectedReappliedContent:
    """The parked candidate's own content for one path: bytes, absent, or uncomputable."""

    repository = Path(side.repository)
    parked_rev, parked = _parked_path_content(side, path)
    if parked is None:
        base = _spec_content(repository, (f"{side.preSyncHead}:{path}",))
        if base is None:
            return ExpectedReappliedContent("uncomputable")
        return ExpectedReappliedContent("absent")
    if parked_rev is None or _rev_mode(repository, parked_rev, path) not in _REGULAR_BLOB_MODES:
        return ExpectedReappliedContent("uncomputable")
    return ExpectedReappliedContent("bytes", parked)


def _entry_content_at(worktree: Path, path: str) -> ExpectedReappliedContent:
    target = worktree / path
    if target.is_symlink():
        return ExpectedReappliedContent("bytes", os.fsencode(os.readlink(target)))
    if target.is_file():
        return ExpectedReappliedContent("bytes", target.read_bytes())
    if not os.path.lexists(target):
        return ExpectedReappliedContent("absent")
    return ExpectedReappliedContent("unreadable")


def worktree_entry_content(side: SyncSideRecord, path: str) -> ExpectedReappliedContent:
    """The live worktree's exact bytes for one path, without following checkouts."""

    return _entry_content_at(Path(side.worktree), path)


def stashed_parked_paths(side: SyncSideRecord) -> tuple[str, ...]:
    """Every path the recorded stash holds: tracked changes plus untracked files."""

    if not side.wipStash:
        return ()
    repository = Path(side.repository)
    tracked = run_git(
        repository, ["diff", "--name-only", "-z", f"{side.wipStash}^1", side.wipStash]
    )
    if tracked.returncode != 0:
        raise SyncGitProofError("could not read the parked worktree WIP paths")
    paths = [entry for entry in tracked.stdout.split("\0") if entry]
    third = run_git(repository, ["rev-parse", "--verify", "--quiet", f"{side.wipStash}^3"])
    if third.returncode == 0:
        listed = run_git(repository, ["ls-tree", "-r", "--name-only", "-z", f"{side.wipStash}^3"])
        if listed.returncode != 0:
            raise SyncGitProofError("could not read the parked worktree untracked paths")
        paths.extend(entry for entry in listed.stdout.split("\0") if entry)
    ordered: list[str] = []
    for path in paths:
        if path not in ordered:
            ordered.append(path)
    return tuple(ordered)


def stash_entry_live(side: SyncSideRecord) -> bool:
    """Whether the recorded stash entry is still on the live stash stack.

    Assessment trusts only a live entry: a dropped stash can leave readable ghost
    objects behind, and retiring authority on those would discard the tree's only copy.
    """

    if not side.wipStash:
        return False
    listed = run_git(Path(side.repository), ["stash", "list", "--format=%H"])
    return listed.returncode == 0 and side.wipStash in listed.stdout.split()


def sample_paths(side: SyncSideRecord) -> tuple[str, ...]:
    """The journaled path sample, minus the derived memory cache.

    It names failure paths only when the full stash inventory is unreadable; it
    never passes a proof.
    """

    return tuple(
        path for path in side.wipPaths if not (side.side == "memory" and path == "memory.md")
    )


def _scratch_tree_matches(side: SyncSideRecord, scratch: Path) -> bool:
    """Whether a scratch reproduction holds exactly the live side's state."""

    worktree = Path(side.worktree)
    if _untracked_names(worktree, side.side) != _untracked_names(scratch, side.side):
        return False
    live_dirty = _dirty_names(worktree, side.side)
    if live_dirty != _dirty_names(scratch, side.side):
        return False
    return all(
        _fingerprint_at(worktree, path) == _fingerprint_at(scratch, path) for path in live_dirty
    )


def _reproduce_in_scratch(side: SyncSideRecord, base: str, setup: Callable[[Path], None]) -> bool:
    """Whether a scratch worktree running the same setup holds the live state.

    A crash window may hold owner bytes the live tree cannot distinguish from
    operation output; only an exact reproduction earns trust. Any scratch that
    cannot be built or compared fails safe to False.
    """

    repository = Path(side.repository)
    parent = Path(tempfile.mkdtemp(prefix="ar-sync-verify-"))
    scratch = parent / "worktree"
    try:
        added = run_git(repository, ["worktree", "add", "--detach", str(scratch), base])
        if added.returncode != 0:
            return False
        try:
            setup(scratch)
        except (OSError, SyncGitProofError, CrossingSyncError):
            return False
        return _scratch_tree_matches(side, scratch)
    except OSError:
        return False
    finally:
        run_git(repository, ["worktree", "remove", "--force", str(scratch)])
        shutil.rmtree(parent, ignore_errors=True)


def _git_setup(command: list[str]) -> Callable[[Path], None]:
    """A scratch setup running one git command; the comparison decides the rest."""

    def run(scratch: Path) -> None:
        run_git(scratch, command)

    return run


def verify_crashed_reapply(side: SyncSideRecord) -> bool:
    """Whether the live unmerged state is exactly a fresh reapply of the stash.

    A crash between a reapply and its retain journal leaves unmerged output with
    no reference; owner bytes added in the window must never be blessed. Only an
    exact scratch reproduction earns a fingerprint capture.
    """

    if not side.wipStash or not stash_entry_live(side):
        return False
    return _reproduce_in_scratch(
        side,
        head_commit(Path(side.worktree)),
        _git_setup(["stash", "apply", "--quiet", side.wipStash]),
    )


def _merge_base_and_structure(side: SyncSideRecord) -> tuple[str, str] | None:
    """The admitted merge's base and structure, or None where unreadable."""

    try:
        repository = Path(side.repository)
        base = merge_base(repository, side.preSyncHead, side.sourceCommit)
        structure = merge_structure(Path(side.worktree), base, side.preSyncHead, side.sourceCommit)
        return base, structure
    except (SyncGitProofError, CrossingSyncError):
        return None


def _crossing_setup(
    side: SyncSideRecord,
    base: str,
    paired_code: PairedCode | None,
    owner: tuple[Literal["leaf", "master"], str],
) -> Callable[[Path], None]:
    """A scratch setup repeating the admitted merge plus its structural crossing."""

    def run(scratch: Path) -> None:
        merged = run_git(scratch, ["merge", "--no-commit", "--no-edit", side.sourceCommit])
        if merged.returncode not in {0, 1} or merge_head(scratch) != side.sourceCommit:
            raise SyncGitProofError("scratch crossing merge failed")
        plan = crossing_plan(
            scratch,
            (base, side.preSyncHead, side.sourceCommit),
            paired_code=paired_code,
            owner=owner,
        )
        apply_crossing(scratch, plan)

    return run


def recapture_resumed_merge(
    side: SyncSideRecord,
    *,
    paired_code: PairedCode | None,
    owner: tuple[Literal["leaf", "master"], str],
) -> dict[str, str]:
    """Fingerprint a resumed merge only where its output is exactly reproducible.

    A merge that was already active when this call started crossed a crash
    window; capturing its tree blindly would bless owner edits. Code and plain
    memory merges reproduce as pure git; a converted-memory merge reproduces the
    admitted merge plus its structural crossing, recomputed from the same
    commits with the same paired code and owner. Anything else stays empty and
    its cancel refuses safely while continue still settles it.
    """

    git_merge = _git_setup(["merge", "--no-commit", "--no-edit", side.sourceCommit])
    if side.side != "memory":
        if not _reproduce_in_scratch(side, side.preSyncHead, git_merge):
            return {}
        return {path: path_fingerprint(side, path) for path in worktree_dirty_paths(side)}
    founded = _merge_base_and_structure(side)
    if founded is None:
        return {}
    base, structure = founded
    setup = git_merge if structure == "plain" else _crossing_setup(side, base, paired_code, owner)
    if not _reproduce_in_scratch(side, side.preSyncHead, setup):
        return {}
    return {path: path_fingerprint(side, path) for path in worktree_dirty_paths(side)}


def path_fingerprint(side: SyncSideRecord, path: str) -> str:
    """One path's operation-created state: worktree bytes plus full index entries."""

    try:
        return _fingerprint_at(Path(side.worktree), path)
    except SyncGitProofError as error:
        raise SyncGitProofError(f"could not read the {side.side} index state of {path}") from error


def capture_retained_digests(side: SyncSideRecord, *, include_untracked: bool) -> dict[str, str]:
    """Fingerprint every dirty path of an operation-created retained state.

    Untracked entries are captured only for a same-call reapply: after any crash
    window an untracked file may be its owner's, and blessing it would discard it.
    """

    digests: dict[str, str] = {}
    for path in worktree_dirty_paths(side):
        if not include_untracked and _is_untracked(side, path):
            continue
        digests[path] = path_fingerprint(side, path)
    return digests


def assess_stash_tree(side: SyncSideRecord, carried_head: str | None) -> CancelTreeAssessment:
    """Decide whether a cancel may discard one side's tree before rolling back.

    ``carried_head`` is the head the reapply ran onto, or ``None`` where the head
    never moved and the reference is the parked content itself. Dirty paths the
    live recorded stash does not hold, parked paths whose worktree bytes differ
    from the reference, parked paths whose index bytes differ from the head (or
    from identical parked bytes), and uncomputable paths whose bytes differ from
    the parked content are later work: the cancel is refused while any remains.
    """

    if side.wipStash and not stash_entry_live(side):
        raise SyncGitProofError(
            f"{side.side} recorded stash {side.wipStash} is missing; "
            "cancel cannot prove tree ownership"
        )
    dirty = set(worktree_dirty_paths(side))
    parked = set(stashed_parked_paths(side)) if side.wipStash else set()
    later: list[str] = []
    owned_untracked: list[str] = []
    merge = _ReapplyMerge(side, carried_head) if carried_head is not None else None
    for path in sorted(dirty):
        if path not in parked:
            later.append(path)
            continue
        if _path_owned_by_stash(side, path, carried_head, merge):
            actual = worktree_entry_content(side, path)
            if actual.kind == "bytes" and _is_untracked(side, path):
                owned_untracked.append(path)
        else:
            later.append(path)
    for path in _index_later_paths(side, carried_head, parked):
        if path not in later:
            later.append(path)
    later_sorted = sorted(later)
    return CancelTreeAssessment(
        later_work=tuple(later_sorted),
        owned_untracked=tuple(owned_untracked),
        needs_discard=bool(dirty - set(later_sorted)),
        merge_fault=merge.fault if merge is not None else "",
    )


def _assess_unretained(
    side: SyncSideRecord, carried_head: str | None, dirty: set[str]
) -> CancelTreeAssessment:
    """Assess a retained state whose crash window left no reference map.

    With no active merge and a live stash the stash proof decides: a tree that
    holds exactly the parked bytes is the transaction's own and cancels, anything
    else is later work. Everywhere else — an active merge, a missing stash —
    nothing shows the bytes to be the transaction's own, so every dirty path is
    unproven, never later work.
    """

    if merge_head(Path(side.worktree)) is None and side.wipStash and stash_entry_live(side):
        return assess_stash_tree(side, carried_head)
    return CancelTreeAssessment(unproven_paths=tuple(sorted(dirty)))


def assess_retained_tree(side: SyncSideRecord, carried_head: str | None) -> CancelTreeAssessment:
    """Decide whether a cancel may discard a retained merge or reapply state.

    Every dirty path must fingerprint-match the operation-created state captured
    at retain time: merge output, conflict markers and stages included. A
    fingerprint mismatch falls back to the stash's own ownership proof only
    where the retained side carries no active merge: the live stash enumerates
    the owned paths, and a mismatched path whose worktree and index bytes the
    stash still owns is a crash-stale fingerprint, not later work. Paths no
    Paths no reference shows to be the transaction's own — an unverified crash
    window, or a recorded stash that is gone — are unproven, never later work:
    discarding them could destroy the only copy. The one exception is a retained
    reapply with no reference map, no active merge and a live stash: there the
    stash proof itself decides, exactly as for an unretained tree. Anything else
    is later work and refuses before journal, index, heads, stash or tree change.
    """

    dirty = set(worktree_dirty_paths(side))
    retained = side.retainedTreeDigests
    if not retained:
        return _assess_unretained(side, carried_head, dirty)
    if side.wipStash and not stash_entry_live(side):
        return CancelTreeAssessment(unproven_paths=tuple(sorted(dirty)))
    matched = {
        path
        for path in dirty
        if retained.get(path) is not None and path_fingerprint(side, path) == retained[path]
    }
    mismatched = set(sorted(dirty)) - matched
    fallback_owned: set[str] = set()
    merge = _ReapplyMerge(side, carried_head) if carried_head is not None else None
    if mismatched and merge_head(Path(side.worktree)) is None:
        parked = set(stashed_parked_paths(side))
        index_later = set(_index_later_paths(side, carried_head, parked))
        for path in sorted(mismatched):
            if (
                path in parked
                and path not in index_later
                and _path_owned_by_stash(side, path, carried_head, merge)
            ):
                fallback_owned.add(path)
    owned = matched | fallback_owned
    owned_untracked = [
        path
        for path in sorted(owned)
        if worktree_entry_content(side, path).kind == "bytes" and _is_untracked(side, path)
    ]
    later_sorted = sorted(dirty - owned)
    return CancelTreeAssessment(
        later_work=tuple(later_sorted),
        owned_untracked=tuple(owned_untracked),
        needs_discard=bool(owned),
        merge_fault=merge.fault if merge is not None else "",
    )


def _path_owned_by_stash(
    side: SyncSideRecord, path: str, carried_head: str | None, merge: _ReapplyMerge | None
) -> bool:
    """Whether one dirty parked path's worktree bytes are exactly the reapply's.

    The cancel judges with the proof's own construction: ``merge`` is git's
    merge of the candidate onto the carried head, shared by one assessment.
    """

    if carried_head is None:
        expected = parked_path_content(side, path)
    else:
        expected = expected_reapplied_content(side, path, carried_head, merge)
    if expected.kind != "uncomputable":
        actual = worktree_entry_content(side, path)
        return (actual.kind, actual.content) == (expected.kind, expected.content)
    raw = _parked_raw_content(side, path)
    if raw is None:
        return False
    actual = worktree_entry_content(side, path)
    return (actual.kind, actual.content) == ("bytes", raw)


def _parked_raw_content(side: SyncSideRecord, path: str) -> bytes | None:
    """The stashed bytes for one path without any type gate, if the stash holds them."""

    _, content = _parked_path_content(side, path)
    return content


def _index_later_paths(
    side: SyncSideRecord, carried_head: str | None, parked: set[str]
) -> list[str]:
    """Parked paths whose staged index bytes the operation did not create.

    A clean reapply leaves the index at the head; any staged deviation — later
    content, a staged deletion, foreign unmerged stages — is bytes no admitted
    stash or commit holds. Staged bytes identical to the parked content are owned:
    discarding them loses nothing the reapply does not return.
    """

    head = carried_head if carried_head is not None else side.preSyncHead
    later: list[str] = []
    for path in sorted(parked):
        if side.side == "memory" and path == "memory.md":
            continue
        state, indexed = _index_state(side, path)
        if state == "unmerged":
            later.append(path)
            continue
        head_blob = _spec_content(Path(side.repository), (f"{head}:{path}",))
        if state == "absent":
            if head_blob is None or _parked_deleted(side, path):
                continue
            later.append(path)
            continue
        if head_blob is not None and indexed == head_blob:
            continue
        raw = _parked_raw_content(side, path)
        if raw is not None and indexed == raw:
            continue
        later.append(path)
    return later


def _index_state(side: SyncSideRecord, path: str) -> tuple[str, bytes]:
    """One path's staged state: ("blob", bytes), ("absent", b"") or ("unmerged", b"").

    The staged bytes are the blob's own: a text pipe would turn every carriage
    return into a line feed, and a staged picture would never equal its stash.
    """

    worktree = Path(side.worktree)
    listed = run_git(worktree, ["ls-files", "-s", "--", path])
    if listed.returncode != 0:
        raise SyncGitProofError(f"could not read the {side.side} index state of {path}")
    if not listed.stdout.strip():
        return ("absent", b"")
    stages = {line.split()[2] for line in listed.stdout.splitlines()}
    if stages != {"0"}:
        return ("unmerged", b"")
    try:
        staged = _spec_content(worktree, (f":0:{path}",))
    except GitPreparationError:
        staged = None
    if staged is None:
        raise SyncGitProofError(f"could not read the {side.side} staged bytes of {path}")
    return ("blob", staged)


def _parked_deleted(side: SyncSideRecord, path: str) -> bool:
    """Whether the parked candidate deletes a path the pre-sync head holds."""

    repository = Path(side.repository)
    _, parked = _parked_path_content(side, path)
    if parked is not None:
        return False
    return _spec_content(repository, (f"{side.preSyncHead}:{path}",)) is not None


def cancelled_wip_restored(side: SyncSideRecord) -> bool:
    """Whether the tree holds the parked candidate as parked (heads already back)."""

    for path in stashed_parked_paths(side):
        if side.side == "memory" and path == "memory.md":
            continue
        expected = parked_path_content(side, path)
        if expected.kind == "uncomputable":
            return False
        actual = worktree_entry_content(side, path)
        if (actual.kind, actual.content) != (expected.kind, expected.content):
            return False
    return True


def discard_owned_reapply(side: SyncSideRecord, owned_untracked: tuple[str, ...]) -> None:
    """Drop the transaction-owned reapply; the candidate itself stays in its stash.

    Only call after :func:`cancel_tree_assessment` found no later work: the reset
    discards tracked owned changes, and the scoped clean removes exactly the owned
    untracked paths ``reset --hard`` keeps.
    """

    worktree = Path(side.worktree)
    require_side_checkout(side)
    reset = run_git(worktree, ["reset", "--hard"])
    if reset.returncode != 0:
        raise SyncGitProofError(
            reset.stderr.strip() or f"could not clear the {side.side} candidate reapply"
        )
    if owned_untracked:
        cleaned = run_git(worktree, ["clean", "-f", "--", *owned_untracked])
        if cleaned.returncode != 0:
            raise SyncGitProofError(
                cleaned.stderr.strip() or f"could not clear the {side.side} owned untracked paths"
            )


def _expected_added_path(parked: bytes | None, carried: bytes | None) -> ExpectedReappliedContent:
    """What the reapply of a path the pre-sync head lacks gives."""

    if carried is None:
        if parked is None:
            return ExpectedReappliedContent("uncomputable")
        return ExpectedReappliedContent("bytes", parked)
    if parked is not None and carried == parked:
        return ExpectedReappliedContent("bytes", carried)
    return ExpectedReappliedContent("uncomputable")


def _parked_path_content(side: SyncSideRecord, path: str) -> tuple[str | None, bytes | None]:
    """The stashed bytes for one path with the rev that held them, if any."""

    repository = Path(side.repository)
    for spec in (f"{side.wipStash}^3:{path}", f"{side.wipStash}:{path}"):
        content = _spec_content(repository, (spec,))
        if content is not None:
            rev, _, _ = spec.rpartition(":")
            return rev, content
    return None, None


def _spec_content(repository: Path, specs: tuple[str, ...]) -> bytes | None:
    """The exact blob bytes of the first resolvable spec, or ``None`` when absent."""

    for spec in specs:
        resolved = run_git(repository, ["rev-parse", "--verify", "--quiet", spec])
        if resolved.returncode == 0:
            return read_git_blob_bytes(repository, resolved.stdout.strip())
    return None


def _rev_mode(repository: Path, rev: str, path: str) -> str | None:
    """The tree mode one rev holds for a path, or ``None`` when it holds none."""

    listed = run_git(repository, ["ls-tree", rev, "--", path])
    if listed.returncode != 0:
        return None
    lines = listed.stdout.splitlines()
    if not lines:
        return None
    return lines[0].split()[0]


def _regular_modes(repository: Path, path: str, holders: list[tuple[str, bool]]) -> bool:
    """Whether every present revision holds a blob ``merge-file`` can reapply."""

    for rev, present in holders:
        if present and _rev_mode(repository, rev, path) not in _REGULAR_BLOB_MODES:
            return False
    return True


def _is_untracked(side: SyncSideRecord, path: str) -> bool:
    """Whether a dirty path is untracked rather than modified."""

    listed = run_git(Path(side.worktree), ["ls-files", "--", path])
    return listed.returncode == 0 and not listed.stdout.strip()


def drop_parked_wip(side: SyncSideRecord) -> bool:
    """Drop exactly the recorded stash entry, and never another one."""

    repository = Path(side.repository)
    listed = run_git(repository, ["stash", "list", "--format=%gd %H"])
    if listed.returncode != 0:
        raise SyncGitProofError(listed.stderr.strip() or "could not list the stash stack")
    for line in listed.stdout.splitlines():
        cells = line.split()
        if len(cells) == 2 and cells[1] == side.wipStash:
            dropped = run_git(repository, ["stash", "drop", cells[0]])
            if dropped.returncode != 0:
                raise SyncGitProofError(
                    dropped.stderr.strip() or "could not drop the parked worktree WIP"
                )
            return True
    return False


def merge_head(worktree: Path) -> str | None:
    result = run_git(worktree, ["rev-parse", "--verify", "MERGE_HEAD"])
    return result.stdout.strip() if result.returncode == 0 else None


def unmerged_paths(worktree: Path) -> tuple[str, ...]:
    result = run_git(worktree, ["diff", "--name-only", "--diff-filter=U"])
    if result.returncode != 0:
        raise SyncGitProofError(result.stderr.strip() or "could not inspect merge conflicts")
    return tuple(path for path in result.stdout.splitlines() if path)


def side_merge_completed(side: SyncSideRecord) -> bool:
    worktree = Path(side.worktree)
    if merge_head(worktree) is not None:
        return False
    current = head_commit(worktree)
    return exact_created_head(side, current)


def _content_pathspec(side: SyncSideRecord) -> list[str]:
    return ["--", *_content_spec_paths(side.side)] if side.side == "memory" else []


def content_conflicts(side: SyncSideRecord) -> tuple[str, ...]:
    """Read unresolved content without assigning authority to a memory-side cache."""

    return tuple(
        path
        for path in unmerged_paths(Path(side.worktree))
        if side.side != "memory" or path != "memory.md"
    )


def discard_memory_cache_changes(side: SyncSideRecord) -> None:
    """Restore only the derived path to Git's pre-operation state; discard no content."""

    if side.side != "memory":
        return
    worktree = Path(side.worktree)
    tracked = _require_sync_git(worktree, ["ls-tree", "--name-only", "HEAD", "--", "memory.md"])
    _require_sync_git(worktree, ["clean", "-fdx", "--", "memory.md"])
    if tracked:
        _require_sync_git(
            worktree, ["restore", "--source=HEAD", "--staged", "--worktree", "--", "memory.md"]
        )
    else:
        _remove_memory_cache_from_index(side)


def _remove_memory_cache_from_index(side: SyncSideRecord) -> None:
    _require_sync_git(
        Path(side.worktree), ["rm", "--cached", "--force", "--ignore-unmatch", "--", "memory.md"]
    )


def _require_sync_git(worktree: Path, args: list[str]) -> str:
    result = run_git(worktree, args)
    if result.returncode != 0:
        raise SyncGitProofError(
            result.stderr.strip() or result.stdout.strip() or "sync Git operation failed"
        )
    return result.stdout.strip()


def _refresh_memory_cache(side: SyncSideRecord) -> None:
    if side.side != "memory":
        return
    worktree = Path(side.worktree)
    if not _require_sync_git(worktree, ["ls-tree", "--name-only", "HEAD", "--", "memory.md"]):
        refresh_memory_cache(worktree)


def _require_active_merge(side: SyncSideRecord) -> None:
    worktree = Path(side.worktree)
    if head_commit(worktree) != side.preSyncHead or merge_head(worktree) != side.sourceCommit:
        raise SyncGitProofError(f"{side.side} active merge is not the pinned sync merge")


def _continue_memory_merge(
    side: SyncSideRecord, paired_code: PairedCode | None
) -> SideMergeOutcome:
    """Settle the memory merge: commit it when nothing is unmerged, else hand it to the agent.

    Knowledge is text. A merge with a converted side has already had its ``knowledge/`` and
    ``onboarding/`` paths merged structurally (:func:`_apply_knowledge_merge`), so whatever is still
    unmerged here is an item both sides authored differently, or a file outside those roots, and is
    the agent's to resolve and ``continue``. The staged tree is validated (MIK-R22) before it is
    committed.
    """

    _require_active_merge(side)
    _remove_memory_cache_from_index(side)
    conflicts = content_conflicts(side)
    if conflicts:
        return SideMergeOutcome(state="resolution-required", conflicts=conflicts)
    validate_staged_resolution(side)
    return SideMergeOutcome(
        state="completed", conflicts=(), message=_finish_staged_memory_merge(side, paired_code)
    )


def _existing_side_merge(
    side: SyncSideRecord, paired_code: PairedCode | None
) -> SideMergeOutcome | None:
    """Resume only the admitted merge or its exact completed output."""
    worktree = Path(side.worktree)
    if merge_head(worktree) is not None:
        _require_active_merge(side)
        if side.side == "memory" and side.plan == "merge":
            return _continue_memory_merge(side, paired_code)
        return SideMergeOutcome(state="resolution-required", conflicts=unmerged_paths(worktree))
    if side_merge_completed(side):
        return SideMergeOutcome(state="completed", message=head_commit(worktree))
    return None


def start_side_merge(
    side: SyncSideRecord,
    *,
    paired_code: PairedCode | None = None,
    crossing_owner: tuple[Literal["leaf", "master"], str] | None = None,
) -> SideMergeOutcome:
    """Attempt the pinned merge; only genuine content conflicts require resolution.

    ``paired_code`` is the code commit the memory merge is paired with (the code side's result):
    the knowledge validator checks the merged memory tree against it (MIK-R22 rule 8).
    ``crossing_owner`` names who performs the sync (the leaf, or the master line's task): a memory
    merge with a converted side has its knowledge paths merged structurally (MIK-R24 rule 8 step 3,
    in every managed sync by MIK-R26 rule 2), and a conflicted record's row is that owner's.
    """

    worktree = Path(side.worktree)
    require_side_checkout(side)
    existing = _existing_side_merge(side, paired_code)
    if existing is not None:
        return existing
    if head_commit(worktree) != side.preSyncHead:
        raise SyncGitProofError(f"{side.side} work branch moved after sync admission")
    if git_status(side):
        raise SyncGitProofError(f"{side.side} sync requires a clean worktree before merging")
    discard_memory_cache_changes(side)
    memory_merge = side.side == "memory" and side.plan == "merge"
    knowledge = _knowledge_merge(side, paired_code, crossing_owner, memory_merge=memory_merge)
    # A memory merge stops before committing so the structural merge and the validator see it staged.
    no_commit = ["--no-commit"] if memory_merge else []
    result = run_git(worktree, ["merge", *no_commit, "--no-edit", side.sourceCommit])
    if knowledge is not None:
        return _apply_knowledge_merge(side, knowledge, result, paired_code)
    if result.returncode == 0:
        if memory_merge:
            return _continue_memory_merge(side, paired_code)
        result_head = head_commit(worktree)
        if not exact_created_head(side, result_head):
            raise SyncGitProofError(f"{side.side} merge did not create the exact admitted head")
        _refresh_memory_cache(side)
        return SideMergeOutcome(state="completed", message=result_head)
    conflicts = unmerged_paths(worktree)
    if result.returncode == 1 and merge_head(worktree) == side.sourceCommit and conflicts:
        if memory_merge:
            return _continue_memory_merge(side, paired_code)
        return SideMergeOutcome(
            state="resolution-required",
            conflicts=conflicts,
            message=(result.stderr or result.stdout).strip(),
        )
    raise SyncGitProofError(
        (result.stderr or result.stdout).strip() or f"{side.side} source merge failed"
    )


def _needs_crossing_report(plan: CrossingPlanView) -> bool:
    """Whether the structural plan leaves something for the curator to read.

    An item for the curator, a converted side, a renumbered reference, a kept
    evidence line, or a skipped card (MIK-R66 rules 7-9): every nonzero alignment
    diagnostic persists a report, even with no conflict.
    """

    alignment = plan.report.get("alignment") or {}
    return bool(
        plan.conflict_versions
        or plan.report.get("converted")
        or alignment.get("renumberedReferences")
        or alignment.get("evidenceLinesKept")
        or alignment.get("skippedCards")
    )


def _apply_knowledge_merge(
    side: SyncSideRecord,
    plan: CrossingPlanView,
    result: subprocess.CompletedProcess[str],
    paired_code: PairedCode | None,
) -> SideMergeOutcome:
    """Replace the started merge's knowledge and onboarding paths with the structural plan.

    The report is written when the merge leaves something to read: an item for the curator, or a
    side it converted (a crossing). A clean merge of converted lines writes none, unless the
    reference alignment left diagnostics (MIK-R66 rules 7-9): renumbered references, kept
    evidence lines, or skipped cards are reported too.
    """

    worktree = Path(side.worktree)
    if result.returncode not in {0, 1} or merge_head(worktree) != side.sourceCommit:
        raise SyncGitProofError(
            (result.stderr or result.stdout).strip() or "memory knowledge merge failed"
        )
    report = ""
    try:
        apply_crossing(worktree, plan)
        if _needs_crossing_report(plan):
            report = write_crossing_report(
                worktree, side.preSyncHead, side.sourceCommit, plan
            ).as_posix()
    except CrossingSyncError as error:
        raise SyncGitProofError(str(error)) from error
    outcome = _continue_memory_merge(side, paired_code)
    return replace(outcome, crossing_report=report)


def _knowledge_merge(
    side: SyncSideRecord,
    paired_code: PairedCode | None,
    owner: tuple[Literal["leaf", "master"], str] | None,
    *,
    memory_merge: bool,
) -> CrossingPlanView | None:
    """The structural plan of a memory merge with a converted side, or ``None`` for a plain merge.

    MIK-R24 rule 8 step 3 in every managed sync (MIK-R26 rule 2): three converted trees merge as
    they are, and a crossing converts its unconverted trees first. Only three unconverted trees are
    left to Git. It runs before Git touches the worktree, so a failing step leaves the line
    unchanged.
    """

    if not memory_merge:
        return None
    worktree = Path(side.worktree)
    try:
        base = merge_base(worktree, side.preSyncHead, side.sourceCommit)
        if merge_structure(worktree, base, side.preSyncHead, side.sourceCommit) == "plain":
            return None
        if owner is None:
            raise CrossingSyncError(
                "crossing sync step 'markers' failed: the sync names no leaf or master owner"
            )
        return crossing_plan(
            worktree,
            (base, side.preSyncHead, side.sourceCommit),
            paired_code=paired_code,
            owner=owner,
        )
    except CrossingSyncError as error:
        raise SyncGitProofError(str(error)) from error


def _finish_staged_memory_merge(side: SyncSideRecord, paired_code: PairedCode | None) -> str:
    """Publish one ordinary memory merge with exact parents and no cache in its tree.

    The staged tree is validated first against both parents (MIK-R22 rule 8); a refused merge is
    left staged for the agent to repair and continue.
    """

    worktree = Path(side.worktree)
    _require_active_merge(side)
    prepare_memory_cache(worktree)
    _require_sync_git(worktree, ["add", "--", ".gitignore"])
    try:
        close_crossing_history(worktree, (side.preSyncHead, side.sourceCommit))
    except CrossingSyncError as error:
        raise SyncGitProofError(str(error)) from error
    refusal = memory_commit_refusal(
        memory_repository=worktree,
        candidate_tree=_require_sync_git(worktree, ["write-tree"]),
        bases=(side.preSyncHead, side.sourceCommit),
        paired_code=paired_code,
    )
    if refusal is not None:
        raise SyncKnowledgeValidationError(refusal)
    _require_sync_git(worktree, ["commit", "--no-edit"])
    result_head = head_commit(worktree)
    if not exact_created_head(side, result_head):
        raise SyncGitProofError("memory merge commit does not have the pinned parents")
    _refresh_memory_cache(side)
    return result_head


def continue_side_merge(side: SyncSideRecord, *, paired_code: PairedCode | None = None) -> str:
    """Validate a staged agent resolution and commit the exact retained merge."""

    worktree = Path(side.worktree)
    require_side_checkout(side)
    if merge_head(worktree) is None:
        if not exact_created_head(side, head_commit(worktree)):
            raise SyncGitProofError(
                f"{side.side} merge state disappeared without an operation-owned commit"
            )
        _refresh_memory_cache(side)
        return head_commit(worktree)
    _require_active_merge(side)
    validate_staged_resolution(side)
    if side.side == "memory":
        _remove_memory_cache_from_index(side)
        return _finish_staged_memory_merge(side, paired_code)
    _require_sync_git(worktree, ["commit", "--no-edit"])
    result_head = head_commit(worktree)
    if not exact_created_head(side, result_head):
        raise SyncGitProofError(f"{side.side} merge commit does not have the pinned parents")
    return result_head


def validate_staged_resolution(side: SyncSideRecord) -> None:
    """Read-only proof of the exact retained content merge, excluding the memory cache."""

    worktree = Path(side.worktree)
    require_side_checkout(side)
    _require_active_merge(side)
    conflicts = content_conflicts(side)
    if conflicts:
        raise SyncGitProofError(
            f"{side.side} resolution still has unmerged paths: {', '.join(conflicts[:30])}"
        )
    unstaged = run_git(worktree, ["diff", "--quiet", *_content_pathspec(side)])
    if unstaged.returncode != 0:
        raise SyncGitProofError(f"{side.side} resolution has unstaged changes")
    checked = run_git(worktree, ["diff", "--cached", "--check", *_content_pathspec(side)])
    if checked.returncode != 0:
        raise SyncGitProofError(
            checked.stdout.strip() or checked.stderr.strip() or "staged resolution is invalid"
        )


def _abort_failure(
    side: SyncSideRecord, aborted: subprocess.CompletedProcess[str]
) -> SyncGitProofError:
    """The refusal of a merge abort that did not restore the head, naming Git's own reason."""

    detail = (aborted.stderr or aborted.stdout).strip()
    suffix = f": {detail}" if detail else ""
    return SyncGitProofError(f"{side.side} exact merge abort did not restore its head{suffix}")


def rollback_side(side: SyncSideRecord) -> None:
    """Restore one side only when live history proves the exact sync-owned delta."""

    ensure_temporary_worktree(side)
    worktree = Path(side.worktree)
    current = head_commit(worktree)
    current_merge = merge_head(worktree)
    if current_merge is not None:
        if current != side.preSyncHead or current_merge != side.sourceCommit:
            raise SyncGitProofError(f"{side.side} active merge is outside sync authority")
        # The memory merge removed a tracked cache from the index (_continue_memory_merge); put the
        # derived path back to HEAD first, or `git merge --abort` meets it as an untracked file and
        # refuses (L37: cancel after a memory conflict on a line that still tracks memory.md).
        discard_memory_cache_changes(side)
        aborted = run_git(worktree, ["merge", "--abort"])
        if aborted.returncode != 0 or head_commit(worktree) != side.preSyncHead:
            raise _abort_failure(side, aborted)
    elif current != side.preSyncHead:
        if not exact_created_head(side, current):
            raise SyncGitProofError(
                f"{side.side} has later or unrelated commits; automatic rollback is unsafe"
            )
        if git_status(side):
            raise SyncGitProofError(f"{side.side} has post-sync work; automatic rollback is unsafe")
        discard_memory_cache_changes(side)
        reset = run_git(worktree, ["reset", "--hard", side.preSyncHead])
        if reset.returncode != 0:
            raise SyncGitProofError(reset.stderr.strip() or f"could not restore {side.side} head")
    if head_commit(worktree) != side.preSyncHead:
        raise SyncGitProofError(f"{side.side} rollback did not reach its pinned pre-sync head")
    if git_status(side):
        raise SyncGitProofError(
            f"{side.side} branch is restored but post-sync work remains for manual repair"
        )


def exact_created_head(side: SyncSideRecord, current: str) -> bool:
    """Prove the only two heads the admitted merge can create."""
    if current == side.sourceCommit and is_ancestor(
        Path(side.repository), side.preSyncHead, side.sourceCommit
    ):
        return True
    parents = run_git(Path(side.repository), ["rev-list", "--parents", "-n", "1", current])
    cells = parents.stdout.split()
    return parents.returncode == 0 and cells[1:] == [side.preSyncHead, side.sourceCommit]


def side_branch_head(side: SyncSideRecord) -> str:
    return branch_commit(Path(side.repository), side.workBranch)
