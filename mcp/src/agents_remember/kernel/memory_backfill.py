"""Rewrite pre-trailer memory commits so their messages carry the ``Code-Commit:`` attribution.

``memory_attribution`` reads the ledger out of the memory commits that carry a trailer, and
writes one at the commit site. Every commit written before that rule carries none, so the
ledger's rows for that history are read from each commit's own ``memory.md`` blob through the
per-commit fallback -- a fallback that exists only until this migration runs and then has
nothing left to read. This module is the migration: it derives which commits must receive a
trailer from the table the history already records, and rewrites exactly those commit objects
with the trailer appended.

THREE PROPERTIES ARE THE WHOLE CONTRACT, AND EACH IS FORCED BY A WAY THE OBVIOUS
IMPLEMENTATION IS WRONG.

1. *Classified by the row, never by the subject.* A commit subject is prose: it can say
   ``ledger: map ...`` while touching onboarding, or claim index work while doing policy. What
   a commit is *for* is decided by the table's own ``Code | Memory`` rows, which are data.

2. *Every pairing the format can hold is carried, chosen by a declared rule.*
   ``%(trailers:key=Code-Commit,valueonly)`` renders one line per matching trailer and the reader
   takes the LAST, so a commit carrying two trailers silently loses one. ONE memory commit
   therefore carries ONE code commit, and the table records more pairings than that allows: a
   code commit appears against several memory commits and a memory commit against several code
   commits. The rule that picks WHICH pairings survive is declared here rather than left to
   chance, and it has two halves. Every memory commit the table names receives a trailer, because
   each of them had somewhere to live and leaving one out drops a pairing. A memory commit claimed
   by several code commits resolves on the table's own order: the table is newest-first, so the
   bottom-most row naming a pair is the pairing recorded FIRST and it wins. Every row the rule
   passes over is reported as a skip with its reason; every code commit left with no trailer at
   all is named as a lost claim with the code commit that took its memory commit, because the
   size of that census is the evidence the rule ran and a migration that silently collapsed rows
   would look exactly like one that had nothing to do.

3. *The rule chooses, so the rule says what it chose.* The skip vocabulary is closed and splits
   into two families: holes in the history or the code repository, which no selection could
   repair, and rows the rule DECLINED. A declined row is only honest when the pairing it holds is
   carried by another row or when the row lost a conflict, and both cases carry their own
   literal, so a caller can tell a duplicate from a loss without re-deriving the matching. On top
   of that, ``lost_code_commits`` makes the plan nonempty: a plan that cannot name every code
   commit the table records reports that fact rather than ``is_empty``.

4. *Idempotent twice over, and the second reason is the stronger one.* ``plan_memory_backfill``
   is a pure function of the current history, so the plan IS the dry run, and a second run over
   a migrated history plans nothing. Underneath that, the rewrite itself is a no-op when nothing
   needs writing: replaying a commit's own tree, parents, message, identity and timestamps
   through ``git commit-tree`` reproduces that commit's object id exactly, so even a run that
   rebuilt every commit would move nothing. The plan-level check is what avoids 500-odd pointless
   rebuilds; the byte-faithful replay is what makes the result correct.

The rewrite changes messages and nothing else. The tree, the parents, the author and committer
identities and both timestamps are replayed exactly, so a rebuilt commit differs from its
original in its trailer block alone and no two trees anywhere in the repository differ. Every
original commit stays reachable from a rescue ref written before the first ref moves, which is
also the only thing that can undo this: no ``git revert`` restores a commit object.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from agents_remember.kernel.git_command import GitRunnerOptions, run_git
from agents_remember.kernel.memory_attribution import (
    parse_code_commit_trailer,
    render_memory_content_message,
)
from agents_remember.kernel.memory_ledger import (
    LEDGER_RELATIVE_PATH,
    LedgerRow,
    ledger_to_text,
    parse_ledger_text_unvalidated,
)

# Why a row contributes no trailer. One literal per cause, so the skip census is a closed
# vocabulary a caller can branch on rather than free text a reader has to interpret. The causes
# split into two families and the split is the point: the first four are holes the history or the
# code repository has, and the last two are the selection rule declining a row it CAN carry. A
# caller that only counted skips could not tell a migration that declined a duplicate from one
# that lost a pairing, which is exactly the failure this census exists to expose.
SKIP_MEMORY_COMMIT_MISSING = "memory-commit-cell-does-not-name-an-object"
SKIP_MEMORY_COMMIT_UNREACHABLE = "memory-commit-not-reachable-from-the-tip"
SKIP_CODE_COMMIT_NOT_HELD = "code-commit-not-held-by-the-code-repository"
SKIP_MEMORY_COMMIT_CLAIMED = "memory-commit-already-carries-another-code-commit"
SKIP_CODE_COMMIT_ALREADY_NAMED = "code-commit-already-named-by-an-earlier-row"

SKIP_REASONS = (
    SKIP_MEMORY_COMMIT_MISSING,
    SKIP_MEMORY_COMMIT_UNREACHABLE,
    SKIP_CODE_COMMIT_NOT_HELD,
    SKIP_MEMORY_COMMIT_CLAIMED,
    SKIP_CODE_COMMIT_ALREADY_NAMED,
)

# The causes above that are the rule DECLINING a row, as opposed to a hole in the history. They
# are the ones a caller must check before calling a plan complete: a declined row is a pairing the
# migration chose not to carry, so it is only honest when the row it declined is a duplicate of a
# pairing that IS carried.
SKIP_REASONS_THE_RULE_CHOSE = (SKIP_MEMORY_COMMIT_CLAIMED, SKIP_CODE_COMMIT_ALREADY_NAMED)

# The identity fields, read from the commit and replayed into ``git commit-tree``. The dates are
# taken in git's own internal ``<timestamp> <tzoffset>`` spelling (``%ai``/``%ci``) and NOT in the
# strict ISO-8601 one (``%aI``/``%cI``): strict ISO renders a zero offset as ``Z``, so a commit
# whose header says ``+0000`` would come back saying ``Z`` and the replay would no longer be
# byte-for-byte the object it replaces.
_IDENTITY_FIELDS = ("%an", "%ae", "%ai", "%cn", "%ce", "%ci")


class MemoryBackfillRefusal(RuntimeError):
    """A backfill this history or this invocation cannot support, with its reason."""


@dataclass(frozen=True)
class TrailerToWrite:
    """One commit that must receive ``Code-Commit: <code_commit>``, and nothing else."""

    memory_commit: str
    code_commit: str
    subject: str


@dataclass(frozen=True)
class SkippedRow:
    """One table row that contributes no trailer, with the rule that skipped it."""

    row: LedgerRow
    reason: str


@dataclass(frozen=True)
class LostClaim:
    """One code commit no trailer can name, and the memory commit that took its pairing.

    ``winner`` is the code commit the contested memory commit's trailer names instead. It is the
    fact that makes the loss adjudicable: a reader can see that the pairing was not dropped but
    reassigned, and can check the two code commits against the diff that recorded them.
    """

    code_commit: str
    memory_commit: str
    winner: str


@dataclass(frozen=True)
class MemoryBackfillPlan:
    """What one run would rewrite at one tip, all of it derived and none of it guessed.

    ``assigned_attributions`` and ``trailers`` are two different counts and both are needed,
    because a commit carries at most one effective trailer. The first is how many of the table's
    rows passed the history's own checks and so could be carried at all -- the eligible set, before
    the one-trailer-per-commit rule takes its share -- and the second is how many COMMITS must be
    rewritten to carry the selection. They differ when a commit already carries the trailer it was
    assigned, and the eligible count stays larger than both because the table records more pairings
    than the format can hold. Reporting either alone would misstate the work: the second would hide
    how much of the table the format could reach, and the first would read as if every eligible row
    were written.

    ``lost_claims`` is the third count, and it is the one that keeps ``is_empty`` honest. Every
    pairing a trailer cannot hold is either a row the rule declined -- a duplicate of a pairing
    that IS carried, or a row that lost a conflict -- or a code commit no trailer names at all.
    The second kind is an attribution the migration could not preserve, so it is named here rather
    than absorbed into a skip total, and :attr:`is_empty` cannot be true while one exists.

    ``named_code_commits`` is the census the conflict rule is judged by, and it is deliberately NOT
    ``len(trailers)``: the table records one code commit against several memory commits, so several
    trailers legitimately name one code commit, and the two numbers answer different questions --
    how much writing is left, and how much of the table's attribution the writing reaches.
    """

    tip: str
    row_count: int
    distinct_code_commits: int
    already_attributed: tuple[str, ...]
    trailers: tuple[TrailerToWrite, ...]
    skipped: tuple[SkippedRow, ...]
    digest: str
    assigned_attributions: int = 0
    lost_claims: tuple[LostClaim, ...] = ()
    named_code_commits: int = 0
    lost_code_commits: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Whether this run has nothing left to write, including nothing it must report.

        NOT merely "there are no trailers": a history can be fully written and still have lost a
        mapping, and a plan that reported that as empty would be claiming a completeness the
        history does not have. A code commit named by no trailer -- in this run or by one already
        written -- is the one case where the rewrite cannot deliver what the table records, so it
        is a nonempty result.
        """

        return not self.trailers and not self.lost_code_commits

    def count_of(self, reason: str) -> int:
        """How many rows one skip rule accounted for."""

        return sum(1 for item in self.skipped if item.reason == reason)

    def render(self) -> list[str]:
        """The plan as stable report lines, so two runs compare by eye and by diff."""

        lines = [
            f"tip: {self.tip}",
            f"ledger rows: {self.row_count}",
            f"distinct code commits: {self.distinct_code_commits}",
            f"rows a trailer could carry: {self.assigned_attributions}",
            f"commits to rewrite: {len(self.trailers)}",
            f"commits already carrying their trailer: {len(self.already_attributed)}",
            f"code commits named by every trailer: {self.named_code_commits}",
            f"code commits no trailer can name: {len(self.lost_code_commits)}",
            f"skipped rows: {len(self.skipped)}",
            f"plan digest: {self.digest}",
        ]
        lines.extend(f"  skip {reason}: {self.count_of(reason)}" for reason in SKIP_REASONS)
        lines.extend(
            f"  lost {item.code_commit} lost {item.memory_commit} to {item.winner}"
            for item in self.lost_claims
        )
        lines.extend(
            f"  write {item.memory_commit} {item.code_commit} {item.subject}"
            for item in self.trailers
        )
        return lines


@dataclass(frozen=True)
class MemoryBackfillRequest:
    """What one run rewrites, where it reads the ledger from, and what it may move.

    These four facts travel together because no rewriting run is meaningful without all of them,
    and the two that are easy to leave implicit are the two that make it safe: ``rescue_ref`` is
    the only undo a message rewrite has, and ``update_refs`` is the complete list of refs the run
    is allowed to move, so a branch nobody named cannot be touched by accident.
    """

    memory_repo: Path
    tip: str
    code_repo: Path
    rescue_ref: str
    update_refs: tuple[str, ...] = ()
    relative: str = LEDGER_RELATIVE_PATH


@dataclass(frozen=True)
class MemoryBackfillResult:
    """What one run actually did: the rewritten commits and the rescue the caller can use."""

    old_tip: str
    new_tip: str
    rewritten: tuple[TrailerToWrite, ...]
    rescue_refs: tuple[str, ...]
    rewritten_ids: dict[str, str]
    updated_refs: tuple[str, ...]


def plan_memory_backfill(request: MemoryBackfillRequest) -> MemoryBackfillPlan:
    """Derive the rewrite this history needs, reading the table the exact tip carries.

    Read-only: no ref moves and no object is written. Calling it twice against an unchanged
    history returns the same digest; calling it after :func:`apply_memory_backfill` returns an
    empty plan, which is what makes the plan its own idempotence proof.

    ``request.tip`` may be a ref NAME -- the CLI passes the contract's memory work branch -- and it
    is resolved to the commit it names BEFORE anything else, so every fact downstream (the table,
    the digest, the rescue set) is stated about one exact object rather than about a name that
    could move under the run.
    """

    tip = _resolve_commit(request.memory_repo, request.tip)
    rows = ledger_rows_at(request.memory_repo, tip, request.relative)
    ordered = [_resolved_row(request.memory_repo, row) for row in rows]
    chosen, skipped, assigned, lost = _choose_trailers(
        request.memory_repo, request.code_repo, tip, ordered
    )
    trailers: list[TrailerToWrite] = []
    already: list[str] = []
    for memory_commit, code_commit in sorted(chosen.items()):
        if parse_code_commit_trailer(_message(request.memory_repo, memory_commit)) == code_commit:
            already.append(memory_commit)
            continue
        trailers.append(
            TrailerToWrite(memory_commit, code_commit, _subject(request.memory_repo, memory_commit))
        )
    unnamed = tuple(item.code_commit for item in lost)
    named = set(chosen.values())
    return MemoryBackfillPlan(
        tip=tip,
        row_count=len(ordered),
        distinct_code_commits=len({row.code_commit for row in ordered}),
        already_attributed=tuple(already),
        trailers=tuple(trailers),
        skipped=tuple(skipped),
        digest=_plan_digest(
            PlanMaterial(
                tip=tip,
                rows=ordered,
                chosen=sorted(chosen.items()),
                trailers=tuple(trailers),
                skipped=tuple(skipped),
                lost=lost,
            )
        ),
        assigned_attributions=assigned,
        lost_claims=lost,
        named_code_commits=len(named),
        lost_code_commits=unnamed,
    )


def _resolved_row(memory_repo: Path, row: LedgerRow) -> LedgerRow:
    """One table row with both cells named in full, so no cell is compared as a string.

    The tracked table carries abbreviated cells: a memory commit recorded as ``684c33b2`` next
    to the 40-character name of the same object is a real row in this history, and the two are
    the same commit. Resolving both cells here means the rest of the module only ever sees full
    object names, which is what lets the map from old to rewritten ids be a plain dictionary
    lookup rather than a prefix match that would silently miss.
    """

    return LedgerRow(
        _full_name(memory_repo, row.code_commit),
        _full_name(memory_repo, row.memory_commit),
    )


def _choose_trailers(
    memory_repo: Path,
    code_repo: Path,
    tip: str,
    rows: Sequence[LedgerRow],
) -> tuple[dict[str, str], list[SkippedRow], int, tuple[LostClaim, ...]]:
    """The trailer each memory commit must carry, every row that carries none, the total, and
    every code commit the selection could not name.

    The selection has exactly one hard limit and the limit is Git's, not a preference: a commit
    renders one value per trailer key and the reader takes the LAST, so ONE memory commit carries
    ONE code commit. The table, though, records 472 pairings across 455 memory commits and 428 code
    commits, and both of those are smaller than 472, so the rows cannot all become trailers and the
    only question is which ones do.

    The answer is: as many as the format can hold, chosen by the table's own recorded order.

    1. *Every memory commit the table names carries a trailer.* There are 455 of them and each can
       hold one, so a plan that left some of them without one would be dropping a pairing that had
       somewhere to live -- the failure this migration exists to prevent. Where one code commit is
       named by several memory commits, all of them receive it: two memory commits recording the
       same onboarding repair are two facts, and the ledger is a lookup in which both belong.

    2. *A memory commit claimed by several CODE commits names the claim the table recorded first.*
       The table is newest-first (``sortOrder: newest-first``), so the bottom-most row naming a
       pair is the pairing recorded FIRST -- the one the memory commit was created to record -- and
       the rows above it are later re-recordings of the same content against other code commits.
       Only one claim can be carried, so the conflict resolves on the table's own order, never on a
       hash ordering and never by the last write winning.

    ``_select_pairings`` computes both: a maximum matching first, so that no code commit is left
    unnamed while a memory commit that could have named it stands empty, and then the oldest row of
    every memory commit the matching did not reach.

    The fourth return value is the census that keeps the conflict rule honest, and it is the one
    this function was missing. A conflict resolves by naming a winner, which means some code commit
    is NOT named by the memory commit its row pointed at. Reporting a count is not enough: the plan
    has to say WHICH code commit lost its mapping and WHICH memory commit took it instead, because
    an attribution that cannot be carried is a fact about the history a reader has to adjudicate,
    not a number to subtract. :class:`LostClaim` carries exactly those two facts.

    A row is additionally skipped -- with its own reason, never merged into another -- when the
    code commit it names is not in the code repository at all, or when its memory commit is named
    nowhere in this tip's ancestry, because a trailer pointing at either would be a row the
    projection can only drop.
    """

    candidates = [row for row in rows if _row_skip_reason(memory_repo, code_repo, tip, row) is None]
    holes = [
        SkippedRow(row, reason)
        for row in rows
        if (reason := _row_skip_reason(memory_repo, code_repo, tip, row)) is not None
    ]
    selected = _select_pairings(candidates)
    chosen = {row.memory_commit: row.code_commit for row in selected}
    named = set(chosen.values())
    carried = set(selected)
    declined = [
        SkippedRow(row, _decline_reason(row, named)) for row in candidates if row not in carried
    ]
    return chosen, [*holes, *declined], len(candidates), _lost_claims(candidates, chosen)


def _decline_reason(row: LedgerRow, named: set[str]) -> str:
    """Why a carryable row still contributes no trailer: it lost its memory or its code commit.

    The two are kept apart because they are different facts, and the difference is the whole
    reason the vocabulary is closed. A row whose code commit some carried row already names is a
    DUPLICATE: the pairing is in the trailer set under another memory commit, so declining this row
    costs the history nothing. A row whose code commit no carried row names is a CONFLICT: the
    memory commit it names carries a different code commit, so this mapping is genuinely gone.
    Only the second kind is a loss, and :func:`_lost_claims` reports it by name.
    """

    if row.code_commit in named:
        return SKIP_CODE_COMMIT_ALREADY_NAMED
    return SKIP_MEMORY_COMMIT_CLAIMED


def _lost_claims(
    candidates: Sequence[LedgerRow], chosen: Mapping[str, str]
) -> tuple[LostClaim, ...]:
    """Every code commit the selection left with no memory commit, with the claim that took it.

    Only code commits that no carried row names are lost, because that is the condition that makes
    a mapping disappear: a code commit named by any trailer is still attributable even when some
    of its rows were declined. Each is reported once, against the memory commit that displaced it,
    so the census names the losers rather than counting them.
    """

    lost: list[LostClaim] = []
    claimed = set(chosen.values())
    for code_commit in dict.fromkeys(row.code_commit for row in candidates):
        if code_commit in claimed:
            continue
        contested = next(
            (
                row.memory_commit
                for row in candidates
                if row.code_commit == code_commit and row.memory_commit in chosen
            ),
            "",
        )
        lost.append(LostClaim(code_commit, contested, chosen.get(contested, "")))
    return tuple(lost)


def _select_pairings(candidates: Sequence[LedgerRow]) -> list[LedgerRow]:
    """The rows that become trailers: a maximum matching, then the oldest row of every leftover
    memory commit.

    This is the decision the old rule made by keeping one row per code commit and writing the
    result into a dictionary keyed by memory commit, which meant the last assignment silently won
    and the winner depended on hash ordering. The decision is computed here once, in two steps that
    answer two different questions.

    Step one is a MAXIMUM MATCHING, and it is the largest set of rows sharing no memory commit and
    no code commit. It answers "which pairings can coexist without a code commit being left
    unnamed while a memory commit that could have named it stands empty" -- the shape the old rule
    got wrong, where a later assignment overwrote an earlier one and 16 code commits lost their
    mapping. Among the largest sets the memory commit's OLDEST row is preferred, and the preference
    is stated as the order the rows are offered rather than as a tie-break after the fact: each
    code commit's rows are offered oldest-first, because the table is newest-first and the
    bottom-most row naming a pair is the pairing recorded first. ``_augment`` may still displace a
    pairing when re-placing its owner is the only way to name another code commit, which is why the
    preference is an ordering and not a rule about which row is dropped.

    The order the CODE COMMITS are offered is the other half of the preference and it is not
    cosmetic. A conflict is settled by whoever claims the memory commit first, so a code commit
    offered early keeps its oldest row and a code commit offered late has to find somewhere else --
    or, when it has nowhere else, loses its mapping entirely. The code commits are therefore
    offered MOST CONSTRAINED FIRST: the one with the fewest alternative memory commits goes first,
    because a claim with nowhere else to go is the one a conflict must not displace. Between two
    claims that are equally constrained the OLDEST row wins, and that second half is the module's
    stated rule rather than a convenience: the alternative tie-break is the object name, and a
    winner chosen by hash order is precisely what the review found the old rule doing.

    On the history this migration was written for the matching carries 418 of the table's 428 code
    commits -- the maximum, so ten code commits have no trailer and each is reported -- and 455 of
    its memory commits carry a trailer. Of the review's 60 omitted pairings, 52 are carried: the 5
    it cannot carry are the ones two equally constrained code commits claimed with nothing to fall
    back on, which the rule settles by the table's order and reports as losses.

    Step two places every memory commit the matching did not reach -- 37 of the 455 in this
    history -- with its own oldest row. Those rows all name code commits the matching already named,
    so they add no new code commit; what they add is the memory commit's OWN attribution, which is
    the whole record of a memory commit created to repair onboarding whose code counterpart was
    already attributed by an earlier pairing. Skipping them was the largest single source of the
    omissions the review measured, and the reason is structural rather than accidental: a matching
    is symmetric, while the format is not -- a code commit can be named by several memory commits
    and a memory commit can carry only one -- so one matching alone can never fill every memory
    commit, and the second step is what makes the selection complete rather than merely maximal.
    """

    by_code: dict[str, list[str]] = {}
    for row in reversed(candidates):
        if row.code_commit not in by_code:
            by_code[row.code_commit] = []
        if row.memory_commit not in by_code[row.code_commit]:
            by_code[row.code_commit].append(row.memory_commit)
    for memory_commits in by_code.values():
        memory_commits.reverse()
    oldest_row = _oldest_row_rank(candidates)
    owner = _maximum_matching(by_code, oldest_row)
    by_memory: dict[str, str] = {}
    for row in candidates:
        if row.memory_commit not in by_memory:
            by_memory[row.memory_commit] = row.code_commit
    for memory_commit, code_commit in by_memory.items():
        owner.setdefault(memory_commit, code_commit)
    return [row for row in candidates if owner.get(row.memory_commit) == row.code_commit]


def _oldest_row_rank(candidates: Sequence[LedgerRow]) -> dict[str, int]:
    """How deep in the table each code commit's OLDEST row sits, newest-first tables included.

    The table is newest-first, so the highest index of a code commit's rows is its oldest pairing
    and a HIGHER rank means an older claim. That rank is what settles a tie between two code
    commits with the same number of alternatives: the pairing recorded first wins, exactly as the
    module's rule says, and it is read off the rows rather than off the object names.
    """

    ranks = {row.code_commit: 0 for row in candidates}
    for index, row in enumerate(candidates):
        ranks[row.code_commit] = max(ranks[row.code_commit], index)
    return ranks


def _maximum_matching(
    edges: Mapping[str, Sequence[str]],
    oldest_row: Mapping[str, int],
) -> dict[str, str]:
    """A largest set of code-to-memory pairs sharing no endpoint, by augmenting paths.

    Kuhn's algorithm, which is the standard construction and is also what makes the SIZE of the
    result independent of the order the pairs are offered: a code commit that finds its memory
    commits taken re-places their owners rather than surrendering, so only WHICH pairs pay for the
    maximum follows the offered order -- and the offered order is where both halves of the
    preference live.

    The code commits are offered MOST CONSTRAINED FIRST: fewest alternative memory commits, then
    the OLDEST claim. The first half is not cosmetic. A conflict is settled by whoever claims a
    memory commit first, so a code commit with nowhere else to go must go first; offering them in
    the table's own bottom-up order instead settles conflicts by which commit happens to sit lower,
    which is no signal at all, and on the history this migration was written for it left three
    more of the review's omitted pairings unretained. The second half is the module's stated rule
    for a genuine tie -- two claims with equally many alternatives -- and it reads the table's own
    row order rather than any object name.
    """

    owner: dict[str, str] = {}
    for code_commit in sorted(edges, key=lambda code: (len(edges[code]), -oldest_row[code], code)):
        _augment(code_commit, edges, owner, set())
    return owner


def _augment(
    code_commit: str,
    edges: Mapping[str, Sequence[str]],
    owner: dict[str, str],
    visited: set[str],
) -> bool:
    """Try to place one code commit, re-placing whoever holds a memory commit it wants."""

    for memory_commit in edges[code_commit]:
        if memory_commit in visited:
            continue
        visited.add(memory_commit)
        holder = owner.get(memory_commit)
        if holder is None or _augment(holder, edges, owner, visited):
            owner[memory_commit] = code_commit
            return True
    return False


def _row_skip_reason(
    memory_repo: Path,
    code_repo: Path,
    tip: str,
    row: LedgerRow,
) -> str | None:
    """Why this row's oldest pairing cannot become a trailer, or ``None`` when it can.

    The three causes are kept apart on purpose. A memory cell that names no object at all and a
    memory commit that exists but sits outside this tip's ancestry are different facts about the
    history -- one is a hole in the table, the other is a mapping recorded somewhere this line
    never reached -- and a migration that reported them as one number would hide which repair
    the history needs.
    """

    if not _resolves(memory_repo, row.memory_commit):
        return SKIP_MEMORY_COMMIT_MISSING
    if not _is_ancestor(memory_repo, row.memory_commit, tip):
        return SKIP_MEMORY_COMMIT_UNREACHABLE
    if not _code_commit_is_held(code_repo, row.code_commit):
        return SKIP_CODE_COMMIT_NOT_HELD
    return None


@dataclass(frozen=True)
class PlanMaterial:
    """Everything the digest is taken over: the plan's inputs and the decision they produced.

    These travel as one value because they are one fact -- the state the plan was derived from and
    the selection it made -- and because passing them separately made the digest's signature a
    list a caller could silently reorder. ``chosen`` is in here as well as ``trailers`` because the
    two are not the same decision: a commit that already carries its trailer is chosen and not
    rewritten, so a digest over the rewrites alone would call two different histories -- one
    migrated, one not -- the same plan. ``lost`` is in it for the same reason in the other
    direction: a pairing the selection could not carry changes what the plan claims, so two runs
    that lose different mappings must not share a digest.
    """

    tip: str
    rows: Sequence[LedgerRow]
    chosen: Sequence[tuple[str, str]]
    trailers: Sequence[TrailerToWrite]
    skipped: Sequence[SkippedRow]
    lost: Sequence[LostClaim]


def _plan_digest(material: PlanMaterial) -> str:
    """One stable digest over the plan's inputs and the decision they produced."""

    lines = [
        f"tip={material.tip}",
        *(f"row={row.code_commit} {row.memory_commit}" for row in material.rows),
        *(
            f"chosen={memory_commit} {code_commit}"
            for memory_commit, code_commit in material.chosen
        ),
        *(f"write={item.memory_commit} {item.code_commit}" for item in material.trailers),
        *(f"skip={item.row.memory_commit} {item.reason}" for item in material.skipped),
        *(f"lost={item.code_commit} {item.memory_commit} {item.winner}" for item in material.lost),
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def apply_memory_backfill(
    request: MemoryBackfillRequest,
    *,
    expected_digest: str | None = None,
) -> MemoryBackfillResult:
    """Write the plan's trailers onto the history, moving only the refs the caller named.

    The order is what makes this recoverable rather than merely careful. The plan is derived
    and optionally pinned against the caller's previewed digest; the rescue ref is written and
    read back while every original commit is still the tip of a live ref; and only then are new
    objects created and the named refs moved, in ONE ``update-ref --stdin`` transaction, so a
    refusal part-way through leaves either every ref moved or none of them.

    ``request.rescue_ref`` must not exist whenever this run would rewrite something. That is not a
    formality: it records the history being replaced, and an existing rescue ref means an earlier
    rewrite of this same history that nobody has looked at yet. The check comes AFTER the plan,
    because a run with nothing to write rescues nothing and a second run over an already-migrated
    history must therefore be a no-op rather than a refusal about refs the first run created.
    """

    plan = plan_memory_backfill(request)
    _require_expected_digest(plan, expected_digest)
    if plan.is_empty:
        return MemoryBackfillResult(plan.tip, plan.tip, (), (), {}, ())
    targets = tuple(dict.fromkeys(request.update_refs))
    _refuse_unsafe_rescue(request.memory_repo, request.rescue_ref, targets)
    rescue_refs = _write_rescue_refs(request.memory_repo, request.rescue_ref, plan.tip, targets)
    identity = _rewrite_history(request.memory_repo, plan)
    moved = _move_targets(request.memory_repo, targets, identity)
    return MemoryBackfillResult(
        old_tip=plan.tip,
        new_tip=identity.get(plan.tip, plan.tip),
        rewritten=plan.trailers,
        rescue_refs=rescue_refs,
        rewritten_ids=identity,
        updated_refs=moved,
    )


def _require_expected_digest(plan: MemoryBackfillPlan, expected: str | None) -> None:
    if expected is not None and plan.digest != expected:
        raise MemoryBackfillRefusal(
            f"the plan at {plan.tip} has digest {plan.digest} but {expected} was expected; "
            "the history or the code repository moved between the preview and the apply"
        )


def _refuse_unsafe_rescue(memory_repo: Path, rescue_ref: str, targets: Sequence[str]) -> None:
    if not rescue_ref.startswith("refs/"):
        raise MemoryBackfillRefusal(
            f"rescue ref {rescue_ref!r} is not a full ref name; it must live under refs/ to be "
            "durable, because an unreferenced commit can be garbage collected"
        )
    if any(name == rescue_ref or name.startswith(f"{rescue_ref}-") for name in targets):
        raise MemoryBackfillRefusal(
            f"rescue ref {rescue_ref!r} is also a ref this run would move; the rescue records "
            "the history being replaced and must survive the rewrite untouched"
        )
    result = run_git(memory_repo, ["rev-parse", "--verify", "--quiet", rescue_ref])
    if result.returncode == 0:
        raise MemoryBackfillRefusal(
            f"rescue ref {rescue_ref!r} already exists. It records an earlier rewrite of this "
            "history: read it, decide, delete it explicitly, and run again"
        )


def _write_rescue_refs(
    memory_repo: Path,
    rescue_ref: str,
    tip: str,
    targets: Sequence[str],
) -> tuple[str, ...]:
    """Pin the tip and every named ref, and read each one back, before anything is rewritten.

    Every name is resolved to the COMMIT it points at before a single ref is written, and the hash
    is what the readback is compared against. That is not tidiness: ``request.tip`` is a ref NAME
    on the ordinary path -- the CLI passes the contract's memory work branch -- and ``git
    update-ref`` accepts a name while ``git rev-parse`` answers with a hash, so a rescue set
    verified against the name it was built from can never read back equal to itself. The run would
    write its rescue refs and then refuse, which is the worst of both: a refusal that has already
    mutated the repository, and a second attempt that trips the existing-ref check on refs the
    first attempt created.
    """

    pinned = [
        _resolve_commit(memory_repo, tip),
        *(_resolve_commit(memory_repo, name) for name in targets),
    ]
    names = [_rescue_name(rescue_ref, index) for index, _ in enumerate(dict.fromkeys(pinned))]
    for name, commit in zip(names, dict.fromkeys(pinned), strict=True):
        result = run_git(memory_repo, ["update-ref", name, commit, ""])
        if result.returncode != 0:
            raise MemoryBackfillRefusal(
                f"rescue ref {name} could not be written: "
                f"{result.stderr.strip() or 'git update-ref failed'}. Nothing was rewritten"
            )
    for name, commit in zip(names, dict.fromkeys(pinned), strict=True):
        readback = run_git(memory_repo, ["rev-parse", "--verify", "--quiet", name])
        if readback.returncode != 0 or readback.stdout.strip() != commit:
            raise MemoryBackfillRefusal(
                f"rescue ref {name} did not read back as {commit}; refusing to rewrite a history "
                "that cannot be restored"
            )
    return tuple(names)


def _resolve_commit(repo: Path, rev: str) -> str:
    """The commit a name resolves to, in full, or a refusal -- never the name passed through.

    A rescue ref that recorded an unresolved name would be a ref whose content is not the object
    the rewrite is about, and ``_resolved_row``'s rule of keeping a recorded spelling for a cell
    that resolves to nothing is wrong here for the same reason in the opposite direction: a table
    cell that names no object still has to be REPORTED, while a rescue ref that names no object
    cannot restore anything and must refuse before the first ref moves.
    """

    result = run_git(repo, ["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"])
    resolved = result.stdout.strip()
    if result.returncode != 0 or not resolved:
        raise MemoryBackfillRefusal(
            f"{rev!r} does not resolve to a commit in {repo.as_posix()}; refusing to build a "
            "rescue set from a name that cannot be restored"
        )
    return resolved


def _rescue_name(rescue_ref: str, index: int) -> str:
    return rescue_ref if index == 0 else f"{rescue_ref}-{index}"


def _rewrite_history(memory_repo: Path, plan: MemoryBackfillPlan) -> dict[str, str]:
    """Every reachable commit mapped to its id after this run, rewritten or not.

    The map is TOTAL rather than sparse, so a caller holding an original id -- the table it just
    read, a contract cell, a saved plan -- always finds its replacement and never has to know
    which commits happened to move. Unchanged commits map to themselves, which is what makes
    "the identity map is the idempotence proof" literally true: on an already-migrated history
    every entry is its own key.

    ``git rev-list --reverse --all`` is topological and oldest-first, so each commit's parents
    are already decided when it is reached and no second pass is needed. The whole reachable
    history is walked rather than one branch, because a trailer written on a commit reached only
    through a side branch still changes that commit's id, so its descendants move with it
    whether or not this run names their ref.
    """

    wanted = {item.memory_commit: item.code_commit for item in plan.trailers}
    identity: dict[str, str] = {}
    for commit in _walk(memory_repo):
        original_parents = _parents(memory_repo, commit)
        parents = [identity.get(parent, parent) for parent in original_parents]
        code_commit = wanted.get(commit)
        if code_commit is None and parents == original_parents:
            identity[commit] = commit
            continue
        identity[commit] = _rebuild(memory_repo, commit, parents, code_commit)
    return identity


def _walk(memory_repo: Path) -> list[str]:
    result = run_git(memory_repo, ["rev-list", "--reverse", "--all"])
    if result.returncode != 0:
        raise MemoryBackfillRefusal(
            f"the history in {memory_repo.as_posix()} cannot be walked: "
            f"{result.stderr.strip() or 'git rev-list failed'}"
        )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _rebuild(
    memory_repo: Path,
    commit: str,
    parents: Sequence[str],
    code_commit: str | None,
) -> str:
    """One commit object, identical to its original but for the appended trailer."""

    message = _message(memory_repo, commit)
    if code_commit is not None:
        message = render_memory_content_message(message, code_commit)
    argv = ["commit-tree", _commit_field(memory_repo, commit, "%T")]
    for parent in parents:
        argv.extend(["-p", parent])
    result = run_git(
        memory_repo,
        argv,
        GitRunnerOptions(input_text=f"{message}\n", identity=_identity(memory_repo, commit)),
    )
    if result.returncode != 0:
        raise MemoryBackfillRefusal(
            f"commit {commit} could not be rebuilt: "
            f"{result.stderr.strip() or 'git commit-tree failed'}"
        )
    return result.stdout.strip()


def _identity(memory_repo: Path, commit: str) -> dict[str, str]:
    """The original author and committer, replayed so the rewrite adds nothing of its own."""

    author_name, author_email, author_date, committer_name, committer_email, committer_date = (
        _commit_fields(memory_repo, commit, _IDENTITY_FIELDS)
    )
    return {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_AUTHOR_DATE": author_date,
        "GIT_COMMITTER_NAME": committer_name,
        "GIT_COMMITTER_EMAIL": committer_email,
        "GIT_COMMITTER_DATE": committer_date,
    }


def _move_targets(
    memory_repo: Path,
    targets: Sequence[str],
    identity: dict[str, str],
) -> tuple[str, ...]:
    """Move each named ref whose tip actually changed, in one transaction, or move none.

    ``identity`` is total, so a ref is skipped when its commit maps to itself: that is how a
    second run over an already-migrated history reports having moved nothing rather than
    reporting a move onto the same object.

    Each target is resolved to its FULL ref name before the transaction is built, for the reason
    the rescue set resolves them: ``--stdin`` takes ref names for git to write, and a branch short
    name is only resolved against ``refs/heads/`` where git chooses to, so ``update ar/leaf ...``
    fails where ``update refs/heads/ar/leaf ...`` succeeds. The tip is compared by RESOLVED commit
    and the transaction is guarded by the value it expects, so a ref that moved between the plan
    and the move is refused by git rather than overwritten.
    """

    commands: list[str] = []
    moved: list[str] = []
    for name in targets:
        full_name = _full_ref_name(memory_repo, name)
        current = _full_name(memory_repo, full_name)
        replacement = identity.get(current)
        if replacement is None or replacement == current:
            continue
        commands.append(f"update {full_name} {replacement} {current}")
        moved.append(name)
    if not commands:
        return ()
    result = run_git(
        memory_repo,
        ["update-ref", "--stdin"],
        GitRunnerOptions(input_text="\n".join(commands) + "\n"),
    )
    if result.returncode != 0:
        raise MemoryBackfillRefusal(
            f"the rewritten refs could not be moved atomically: "
            f"{result.stderr.strip() or 'git update-ref --stdin failed'}. "
            "The rescue refs still hold every original commit"
        )
    return tuple(moved)


def _full_ref_name(repo: Path, name: str) -> str:
    """One ref as git's own full spelling, so a short branch name is not left to git's guess.

    ``git rev-parse`` already answers a short branch name with the ref it means, so this reads
    that answer back instead of reconstructing it: ``refs/heads/ar/leaf`` and ``refs/tags/v1``
    both come out of the same lookup, and a name that is not a ref at all is refused here rather
    than inside a transaction that has already been given half its commands.
    """

    result = run_git(repo, ["rev-parse", "--symbolic-full-name", "--verify", "--quiet", name])
    full_name = result.stdout.strip()
    if result.returncode != 0 or not full_name:
        raise MemoryBackfillRefusal(
            f"{name!r} is not a ref in {repo.as_posix()}; this run moves named refs only and "
            "will not guess which one was meant"
        )
    return full_name


def ledger_rows_at(
    memory_repo: Path,
    tip: str,
    relative: str = LEDGER_RELATIVE_PATH,
) -> list[LedgerRow]:
    """The rows the tracked table records at one commit, read without validating its header.

    Deliberately the unvalidated structural parse, for the reason the source reader gives: a
    header disagreeing with its own first row is one of the shapes the projection repairs, and
    refusing to read the history instead would make that repair impossible.
    """

    shown = run_git(memory_repo, ["show", f"{tip}:{relative}"])
    if shown.returncode != 0:
        raise MemoryBackfillRefusal(
            f"memory ledger source {tip}:{relative} is not readable in "
            f"{memory_repo.as_posix()}: {shown.stderr.strip() or 'git show failed'}"
        )
    try:
        return list(parse_ledger_text_unvalidated(shown.stdout).rows)
    except Exception as error:
        raise MemoryBackfillRefusal(
            f"memory ledger source {tip}:{relative} is not a parseable ledger: {error}"
        ) from error


def carry_ledger_cells(
    memory_repo: Path,
    text: str,
    identity: Mapping[str, str],
) -> str:
    """The ledger's table rewritten to name the commits that exist after a rewrite.

    This helper rewrites an explicitly requested historical table artifact after message
    migration. It is not used by runtime source readers: they read commit trailers without
    consulting the table. ``apply_memory_backfill`` returns the complete old-to-new identity
    map needed to carry each memory cell, including unchanged commits.

    Cells are resolved through git BEFORE they are mapped, and that is not tidiness. The tracked
    table really does carry an abbreviated cell beside full ones, and a textual find-and-replace
    over the file cannot tell an eight-character prefix from the first eight characters of some
    other full id -- it silently leaves the abbreviation behind, which is exactly one excluded row
    surviving a migration that otherwise reads clean. Resolving first and rendering full names
    also keeps the migration artifact consistent with its rewritten commit identities.

    The header is not recomputed from scratch. ``baseCodeCommit`` and every code cell are carried
    through untouched, because this migration rewrites memory commits and never code ones;
    ``baseMemoryCommit`` and the two ``last*`` fields are memory-side references, so they move
    with the column they name, and the ``last*`` pair is taken from row one because the ledger's
    own validation is the promise that they agree.
    """

    ledger = parse_ledger_text_unvalidated(text)

    def carried(rev: str) -> str:
        resolved = _full_name(memory_repo, rev)
        return identity.get(resolved, resolved)

    rows = [LedgerRow(row.code_commit, carried(row.memory_commit)) for row in ledger.rows]
    return ledger_to_text(
        replace(
            ledger,
            base_memory_commit=carried(ledger.base_memory_commit),
            last_verified_code_commit=rows[0].code_commit,
            last_memory_content_commit=rows[0].memory_commit,
            rows=rows,
        )
    )


def _message(memory_repo: Path, commit: str) -> str:
    return _commit_field(memory_repo, commit, "%B").rstrip("\n")


def _subject(memory_repo: Path, commit: str) -> str:
    return _commit_field(memory_repo, commit, "%s")


def _parents(memory_repo: Path, commit: str) -> list[str]:
    raw = _commit_field(memory_repo, commit, "%P")
    return raw.split() if raw else []


def _commit_field(memory_repo: Path, commit: str, field: str) -> str:
    return _commit_fields(memory_repo, commit, (field,))[0]


def _commit_fields(memory_repo: Path, commit: str, fields: Sequence[str]) -> list[str]:
    """Several fields of one commit in a single walk, one line each, in the order asked."""

    result = run_git(memory_repo, ["log", "-1", f"--format={'\x1f'.join(fields)}", commit])
    if result.returncode != 0:
        raise MemoryBackfillRefusal(
            f"commit {commit} cannot be read in {memory_repo.as_posix()}: "
            f"{result.stderr.strip() or 'git log failed'}"
        )
    return result.stdout.rstrip("\n").split("\x1f")


def _full_name(repo: Path, rev: str) -> str:
    """One cell resolved to the commit it names, so two spellings are one row.

    A cell that resolves to nothing keeps its recorded spelling and is classified by the rule
    that owns it, rather than being rewritten into a name that would look resolvable.
    """

    result = run_git(repo, ["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"])
    return result.stdout.strip() if result.returncode == 0 else rev


def _resolves(repo: Path, rev: str) -> bool:
    return run_git(repo, ["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"]).returncode == 0


def _is_ancestor(repo: Path, rev: str, tip: str) -> bool:
    return run_git(repo, ["merge-base", "--is-ancestor", rev, tip]).returncode == 0


def _code_commit_is_held(code_repo: Path, commit: str) -> bool:
    return run_git(code_repo, ["cat-file", "-e", f"{commit}^{{commit}}"]).returncode == 0
