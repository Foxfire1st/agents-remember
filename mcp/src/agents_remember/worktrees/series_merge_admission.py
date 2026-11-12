"""Which merges a master's spine admits, and the proof of each step between two landings.

Split out of ``series_closeout``: the chain proof there walks the spine; this module answers,
for one step, whether the history it adds is exactly official positions, sync-produced heads, a
landed leaf's own history, or a pull-request merge that published one recorded landing.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.coordination_context.models import (
    LANDING_WORKFLOW_LOCAL,
    LANDING_WORKFLOW_PULL_REQUEST,
)
from agents_remember.worktrees.modules.git import require_ancestor, require_git
from agents_remember.worktrees.publication_admission import (
    PublicationMergeUnreadable,
    landing_workflow,
    publication_merge_admitted,
    publication_ranges,
)
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.worktree_contract import WorktreeContract


def _leaf_points(ordered: list[WorktreeContract], *, side: str) -> tuple[list[str], list[str]]:
    """Each ordered leaf's landing and its recorded base on one side."""

    if side == "code":
        return (
            [leaf.integrated_code_commit for leaf in ordered],
            [leaf.code_base_commit for leaf in ordered],
        )
    return (
        [leaf.integrated_memory_content_commit for leaf in ordered],
        [leaf.memory_base_commit for leaf in ordered],
    )


@dataclass(frozen=True)
class _SpineStep:
    """One step on a series spine: the side it is proved on, what lands after it, and the master."""

    side: str
    step: str
    task_id: str


@dataclass(frozen=True)
class _MergeAdmission:
    """Which merges one spine side admits: only those a sync, a leaf or the parent line made.

    ``heads`` are the sync-produced heads the sync log records on this side; ``leaf_ranges`` are
    ``(leaf_id, base, integrated)`` per landed leaf, whose own history a merge may lie inside;
    ``positions`` are every official position (the recorded base and each reconciled parent
    position), which a merge from the parent line descends toward. ``legacy`` is true when some
    sync log entry has no recorded head: an entry written before this leaf cannot name the merge
    it produced, so a merge that is already an ancestor of a recorded landing or position stays
    silent (it predates verifiable history and later judgments covered its content), while a merge
    past every recorded point is still judged.
    """

    heads: tuple[str, ...]
    leaf_ranges: tuple[tuple[str, str, str], ...]
    positions: tuple[str, ...]
    legacy: bool
    authority_ranges: tuple[tuple[str, str, str], ...] = ()
    workflow: str = LANDING_WORKFLOW_LOCAL
    """The repository's landing workflow: ``local`` or ``pull-request``.

    ``local`` admits nothing new: this proof is exactly the one every master line had. ``pull-request``
    adds one admission below -- the merge that published a leaf's landing -- and only that merge.
    """


def _merge_admission(
    series: WorktreeContract,
    ordered: list[WorktreeContract],
    *,
    side: str,
) -> _MergeAdmission:
    """The merge admission for one spine side, from the chain's own sync evidence."""

    landings, bases = _leaf_points(ordered, side=side)
    positions = _landing_source_positions(series, ordered, side=side)
    key = "codeHeadTo" if side == "code" else "memoryHeadTo"
    heads: list[str] = []
    legacy = False
    for contract in (series, *ordered):
        for entry in contract.sync_log:
            head = entry.get(key, "")
            if head:
                heads.append(head)
            else:
                legacy = True
    leaf_ranges = tuple(
        (leaf.leaf_id, base, landing)
        for leaf, base, landing in zip(ordered, bases, landings, strict=True)
        if leaf.leaf_id and base and landing
    )
    return _MergeAdmission(
        heads=tuple(heads),
        leaf_ranges=leaf_ranges,
        authority_ranges=publication_ranges(ordered, side),
        positions=positions,
        legacy=legacy,
        workflow=_landing_workflow(series),
    )


def _landing_workflow(series: WorktreeContract) -> str:
    """The landing workflow this series lands under.

    The value itself is read in ``publication_admission``; here it is converted into this proof's
    own refusal so an unreadable or invalid setting is named rather than quietly becoming ``local``
    (an unreadable setting refuses, never admits). A missing key still means ``local``, and that is
    the parser's, not this wrapper's.
    """

    try:
        return landing_workflow(series)
    except (OSError, ValueError) as error:
        raise CloseoutQueueError(
            "atomic-series-landing-workflow-invalid",
            f"atomic series {getattr(series, 'code_branch', '?')} cannot read its landing workflow "
            f"({error}); the landing is blocked until the repository's settings name one of "
            f"{LANDING_WORKFLOW_LOCAL!r} or {LANDING_WORKFLOW_PULL_REQUEST!r}",
        ) from error


def _publication_merge_admitted(
    repository: Path,
    merge: str,
    admission: _MergeAdmission,
    step: _SpineStep,
) -> bool:
    """Ask the publication rule about one merge, refusing an unreadable input in this proof's words.

    The rule and its git reading live in ``publication_admission``; this keeps
    the chain proof's evaluation order and its refusal text exactly where they were.
    """

    try:
        return publication_merge_admitted(
            repository,
            merge,
            workflow=admission.workflow,
            authority_ranges=admission.authority_ranges,
        )
    except PublicationMergeUnreadable as error:
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            f"atomic series {step.side} ref {error}; the landing is blocked until the trees can "
            "be read",
        ) from error


def _chain_ancestry(
    repository: Path, ancestor: str, descendant: str, *, step: _SpineStep, what: str
) -> bool:
    """Ancestry inside the landing chain proof: rc 1 is a legitimate non-ancestor.

    Any other Git failure refuses as an unreadable input instead of passing as
    non-membership: a failed read never silently admits or condemns.
    """

    try:
        return require_ancestor(repository, ancestor, descendant, what=what)
    except RuntimeError as error:
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            f"atomic series {step.side} ref cannot read its landing ancestry ({error}); "
            "the landing is blocked until the trees can be read",
        ) from error


def _require_admitted_merges(
    repository: Path,
    earlier: str,
    later: str,
    admission: _MergeAdmission,
    step: _SpineStep,
) -> None:
    """Refuse a merge on the line that no sync, leaf history or parent line admits.

    The non-merge walk lists commits with ``--no-merges``, so a hand-made merge commit with
    content of its own used to pass: both parents admitted, the merge itself skipped. Every merge
    in the step is therefore listed too. A merge must be a head that a sync log entry records, lie
    inside a landed leaf's own history, or come from the parent line. The refusal names each
    foreign merge.
    """

    if earlier == later:
        return
    try:
        listed = require_git(
            repository, ["rev-list", "--merges", "--full-history", later, "--not", earlier]
        )
    except (RuntimeError, subprocess.SubprocessError) as error:
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            f"atomic series {step.side} ref cannot list its merges from {earlier} to {later} "
            f"({type(error).__name__}: {error}); the landing is blocked until the trees can be "
            "read",
        ) from error
    candidates = listed.split()
    if admission.legacy:
        # A merge already inside proven history predates verifiable sync heads and stays
        # silent; a merge past every recorded landing and position is new history and is
        # still judged. Without this bound a legacy line would admit a hand-made merge at
        # its tip unexamined.
        points = {position for position in admission.positions if position}
        points.update(landing for _leaf_id, _base, landing in admission.leaf_ranges if landing)
        candidates = [
            merge
            for merge in candidates
            if not any(
                _chain_ancestry(repository, merge, point, step=step, what=f"the merge {merge}")
                for point in points
            )
        ]
    foreign = [
        merge
        for merge in candidates
        if merge not in admission.heads
        and not _publication_merge_admitted(repository, merge, admission, step)
        and _published_landing(repository, merge, admission, side=step.side) is None
        and not any(
            _chain_ancestry(repository, base, merge, step=step, what=f"the merge {merge}")
            and _chain_ancestry(repository, merge, integrated, step=step, what=f"the merge {merge}")
            for _leaf_id, base, integrated in admission.leaf_ranges
        )
        and not any(
            _chain_ancestry(repository, merge, position, step=step, what=f"the merge {merge}")
            for position in admission.positions
            if position
        )
    ]
    if foreign:
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            f"master {step.task_id!r}: the series {step.side} line holds merges this proof "
            f"admits nowhere at {step.step}: {', '.join(foreign)}. A master line holds only leaf "
            "landings and its parent line; take the commit off the master branch and make the "
            "change in a leaf",
        )


def _proof_read(repository: Path, args: list[str], *, side: str, what: str) -> str:
    """One Git read of the chain proof; a failed read refuses by name and never admits."""

    try:
        return require_git(repository, args)
    except (RuntimeError, subprocess.SubprocessError) as error:
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            f"atomic series {side} ref cannot read {what} ({type(error).__name__}: {error}); the "
            "landing is blocked until the trees can be read",
        ) from error


def _commit_tree(repository: Path, commit: str, *, side: str) -> str:
    return _proof_read(
        repository, ["rev-parse", f"{commit}^{{tree}}"], side=side, what=f"the tree of {commit}"
    ).strip()


def _published_landing(
    repository: Path, merge: str, admission: _MergeAdmission, *, side: str = "code"
) -> str | None:
    """The landed leaf commit a pull-request merge publishes exactly, or ``None``.

    The shape, from facts the chain already holds (no record of its own): exactly two parents; the
    second is the landed commit H of a leaf in the exact leaf landing chain; the first is the line's
    previous first-parent commit, which H descends from; and the merge's tree is H's tree, so the
    merge adds no content and no history beyond H. The leaf is found from the merge's own second
    parent, never from the step being proved.
    """

    landed = {landed for _leaf, _base, landed in admission.leaf_ranges if landed}
    parents = _proof_read(
        repository,
        ["rev-list", "--parents", "-n", "1", merge],
        side=side,
        what=f"the parents of {merge}",
    ).split()[1:]
    if len(parents) != 2 or parents[1] not in landed:
        return None
    first, published = parents
    if _commit_tree(repository, merge, side=side) != _commit_tree(repository, published, side=side):
        return None
    try:
        descends = require_ancestor(repository, first, published, what=f"the merge {merge}")
    except RuntimeError as error:
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            f"atomic series {side} ref cannot read the ancestry of the merge {merge} ({error}); "
            "the landing is blocked until the trees can be read",
        ) from error
    return published if descends else None


def _publication_merge_admits(
    repository: Path,
    earlier: str,
    later: str,
    step: _SpineStep,
    merge_admission: _MergeAdmission,
) -> bool:
    """True only for a step whose end is a pull-request merge publishing one recorded landing.

    The step proof asks this before its ancestry refusal. Such a merge introduces no content and
    no history of its own; it only publishes the landing the leaf already recorded. Any deviation
    -- an extra parent, a different second parent, a tree of its own, or a first parent that is
    not the step's start -- falls through to the step's other checks and is refused.

    On a master line the step after a leaf's landing H ends at the merge that published H, so the
    step starts at H itself. A step that starts at the merge's first parent instead admits it only
    when that parent is the published leaf's recorded base, so the step adds exactly that leaf's
    own history.
    """

    published = _published_landing(repository, later, merge_admission, side=step.side)
    if published is None:
        return False
    if earlier == published:
        return True
    first = _proof_read(
        repository, ["rev-parse", f"{later}^1"], side=step.side, what=f"the first parent of {later}"
    ).strip()
    bases = {base for _leaf, base, landed in merge_admission.leaf_ranges if landed == published}
    return (
        earlier == first
        and first in bases
        and _chain_ancestry(
            repository, earlier, published, step=step, what=f"the published landing of {step.step}"
        )
    )


def _require_admitted_step(
    repository: Path,
    earlier: str,
    later: str,
    step: _SpineStep,
    merge_admission: _MergeAdmission,
) -> None:
    """Refuse a step that adds history beyond the earlier landing and the official positions.

    ``--no-merges`` is what makes a merge the reconciliation device rather than a loophole: a merge
    introduces no commit of its own, so only genuinely new non-merge history is refused. The merge
    commit itself is judged separately (:func:`_require_admitted_merges`), because
    a hand-made merge with content of its own passes the non-merge walk while both its parents are
    admitted.

    A published pull-request merge is admitted by exact shape before the ancestry refusal
    (:func:`_publication_merge_admits`); any deviation falls through to the checks below.

    The step's commits are ENUMERATED and each one is tested for membership in the recorded
    positions. They are never subtracted from the revision walk: ``--not <a position>`` removes
    every commit that position reaches, so a single recorded position descending from the step's own
    endpoint removed the whole step and the check passed vacuously -- which is how a genuinely
    foreign commit could be admitted. A position that reaches past the step can no longer erase it,
    and the position still admits the step it actually records.
    """

    if earlier == later:
        return
    if _publication_merge_admits(repository, earlier, later, step, merge_admission):
        return
    if not _chain_ancestry(
        repository,
        earlier,
        later,
        step=step,
        what=f"the step from {earlier} to {later}",
    ):
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            f"atomic series {step.side} ref does not carry {step.step} in the leaf landing order "
            f"({earlier} is not an ancestor of {later}); repair that landing record in the "
            "enclosure's series-contract.md or the ref, then retry closeout",
        )
    revision_args = [
        "rev-list",
        "--no-merges",
        "--full-history",
        later,
        "--not",
        earlier,
    ]
    if step.side == "memory":
        revision_args.extend(["--", ".", ":(top,exclude)memory.md"])
    admitted = set(merge_admission.positions)
    official = _positions_inside_the_step(repository, earlier, later, admitted, step)
    reached = _reached_by_the_official_positions(repository, earlier, official, step)
    listed = _proof_read(
        repository, revision_args, side=step.side, what=f"the commits of {step.step}"
    ).split()
    foreign = [commit for commit in listed if commit not in admitted and commit not in reached]
    if foreign:
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            f"atomic series {step.side} ref adds history beyond the exact leaf landing chain and the "
            f"reconciled source line at {step.step}: {', '.join(foreign)}. Move those "
            "commits off the master line (or land them through a leaf), or record the official "
            "position they came from with worktree_sync, then retry closeout",
        )
    _require_admitted_merges(repository, earlier, later, merge_admission, step)


def _positions_inside_the_step(
    repository: Path,
    earlier: str,
    later: str,
    positions: set[str],
    step: _SpineStep,
) -> tuple[str, ...]:
    """The recorded positions INSIDE this step: reachable from its end and not from its start.

    A position past the step is deliberately excluded: such a position reaches the whole step and
    would erase a commit the step genuinely introduced. A position the start reaches (the start
    itself included) is excluded as well; the revision walk already removes everything it reaches.
    A position need not descend from the start: a line merged in from the side enters the step
    there, so the position admits exactly that line's history.

    "Admits the step it actually records" is why a position INSIDE the step admits its own ancestry
    too. The shape this rule names is one landing merged with an official position the contract
    itself synced with, and when that merge is the step's own endpoint the whole step IS that
    position's history: ``8dfc11b8`` on one real master line is exactly that merge -- its parents are
    the previous landing ``7db50f8f`` and the synced position ``8dd62345``, and every commit the step
    adds is reachable from ``8dd62345``. Admitting the position while refusing the line it introduced
    refused the very shape the rule exists to allow. The admission stays bounded to the positions
    this call was given: a position that merely descends FROM the step is not inside it, so it still
    cannot vacate a commit the step genuinely introduced. A position merged in from the side -- the
    default line's tip a repository-default sync recorded -- is inside the step
    though it does not descend from the step's start; its history is the line the step merged.
    """

    if not positions:
        return ()
    inside = set(
        _proof_read(
            repository,
            ["rev-list", "--full-history", later, "--not", earlier],
            side=step.side,
            what=f"the commits from {earlier} to {later}",
        ).split()
    )
    return tuple(position for position in sorted(positions) if position in inside)


def _reached_by_the_official_positions(
    repository: Path,
    earlier: str,
    official: tuple[str, ...],
    step: _SpineStep,
) -> frozenset[str]:
    """Every commit a position inside this step reaches beyond its start, in one walk."""

    if not official:
        return frozenset()
    return frozenset(
        _proof_read(
            repository,
            ["rev-list", "--full-history", *official, "--not", earlier],
            side=step.side,
            what="the history of the recorded positions",
        ).split()
    )


def _landing_source_positions(
    series: WorktreeContract,
    ordered: list[WorktreeContract],
    *,
    side: str,
) -> tuple[str, ...]:
    """Every official position the chain's own syncs reconciled with, and the recorded base.

    A sync is journaled on the contract it ran for, and that is not always the series contract: the
    master's own reconciliation writes its entry here, while a leaf whose base had to be advanced
    writes its entry on the leaf's contract -- which is the only place that position is recorded.
    Reading the series contract alone therefore hid a leaf-level sync from this check and refused a
    step to a base the leaf's own contract records as synced, so both are read here. The union is
    still bounded by the chain's own evidence: every contract named here is one of the ordered
    leaves this closeout already proved landed, so a position no contract ever synced with stays
    inadmissible. A repository-default sync keeps the base pair and records the default line's tip
    it reconciled with as ``codeSourceCommit``/``memorySourceCommit``; that tip
    is an official position too.
    """

    keys = (
        ("codeBaseTo", "codeSourceCommit")
        if side == "code"
        else ("memoryBaseTo", "memorySourceCommit")
    )
    base = series.code_base_commit if side == "code" else series.memory_base_commit
    positions = {base}
    for contract in (series, *ordered):
        positions.update(entry.get(key, "") for entry in contract.sync_log for key in keys)
    return tuple(sorted(position for position in positions if position))


def _sync_entry_pairs(entry: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """The pairs one sync-log entry records: from, to, and a repository-default sync's source.

    A repository-default sync keeps the base pair, so its from and to are equal; the default line's
    tips it reconciled with are its source pair. Other entries carry no source
    pair and answer empty cells there.
    """

    return (
        (entry.get("codeBaseFrom", ""), entry.get("memoryBaseFrom", "")),
        (entry.get("codeBaseTo", ""), entry.get("memoryBaseTo", "")),
        (entry.get("codeSourceCommit", ""), entry.get("memorySourceCommit", "")),
    )
