"""Exact Git mutations and proofs for resumable code and memory-content sync.

The memory side excludes its root memory.md cache from candidate, conflict, and merge-tree
identity. Code-side files keep ordinary Git semantics, including a file named memory.md.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.git_preparation import GitPreparationError
from agents_remember.worktrees.knowledge_crossing import (
    CrossingSyncError,
    apply_crossing,
    crossing_plan,
    merge_base,
    merge_structure,
)
from agents_remember.worktrees.knowledge_validation import PairedCode
from agents_remember.worktrees.modules.git import head_commit
from agents_remember.worktrees.sync_transaction_git._base import (
    _OBJECT_ID,
    _REGULAR_BLOB_MODES,
    ExpectedReappliedContent,
    SyncGitProofError,
)
from agents_remember.worktrees.sync_transaction_git._read import (
    _dirty_names,
    _fingerprint_at,
    _is_untracked,
    _parked_path_content,
    _parked_raw_content,
    _regular_modes,
    _rev_mode,
    _spec_content,
    _untracked_names,
    merge_head,
    renamed_parked_paths,
    stash_entry_live,
    stashed_parked_paths,
    worktree_dirty_paths,
    worktree_entry_content,
)
from agents_remember.worktrees.sync_transaction_state import SyncSideRecord

logger = logging.getLogger(__name__)


def prove_parked_wip_restored(side: SyncSideRecord, carried_head: str) -> bool:
    """Prove the parked candidate is back in the worktree, not silently lost.

    For each parked path the proof establishes that the worktree holds what the
    reapply of the parked change onto the carried head gives. An edit the
    carried line already holds is restored; a parked path whose content is in
    neither the worktree nor the carried line fails it.
    """

    return not parked_wip_failures(side, carried_head)


def applied_restore_failures(side: SyncSideRecord, carried_head: str) -> tuple[str, ...]:
    """Paths a clean reapply did not provably restore (MIK-R61 rule 8).

    The stash is dropped only after the full stash inventory is proven restored, never
    the journal's path sample. A path no construction computes (a type git cannot merge,
    a removal on one side) still passes when the worktree holds exactly the parked
    bytes: the reapply returned them although no merge computes them. Anything else
    fails, and the stash is kept.
    """

    failures: list[str] = []
    for path in parked_wip_failures(side, carried_head):
        raw = _parked_raw_content(side, path)
        actual = worktree_entry_content(side, path)
        if raw is None:
            if actual.kind != "absent":
                failures.append(path)
        elif (actual.kind, actual.content) != ("bytes", raw):
            failures.append(path)
    return tuple(failures)


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
    renames = renamed_parked_paths(side, carried_head)
    for path in stashed_parked_paths(side):
        expected = expected_reapplied_content(side, path, carried_head, merge)
        if expected.kind == "uncomputable" and path in renames:
            # The carried line renamed a file the parked work edits: git's reapply
            # writes the parked change under the new name, so the proof follows it
            # there instead of failing on the old name (MIK-R61 rule 13). A failure
            # names the new name, where the bytes were looked for.
            if _renamed_match(side, renames[path], merge):
                continue
            failures.append(renames[path])
            continue
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


def _renamed_match(side: SyncSideRecord, new: str, merge: _ReapplyMerge) -> bool:
    """Whether the worktree holds the reapply's merged bytes at a rename's new name.

    The expected content is git's own merged tree at the new name (the construction
    MIK-R162 built): nothing else computes what the reapply of a renamed parked edit
    gives.
    """

    merged = merge.content(new)
    if merged.kind != "bytes":
        return False
    actual = worktree_entry_content(side, new)
    return (actual.kind, actual.content) == ("bytes", merged.content)


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


def _expected_added_path(parked: bytes | None, carried: bytes | None) -> ExpectedReappliedContent:
    """What the reapply of a path the pre-sync head lacks gives."""

    if carried is None:
        if parked is None:
            return ExpectedReappliedContent("uncomputable")
        return ExpectedReappliedContent("bytes", parked)
    if parked is not None and carried == parked:
        return ExpectedReappliedContent("bytes", carried)
    return ExpectedReappliedContent("uncomputable")
