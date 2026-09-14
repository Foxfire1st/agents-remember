"""Project the external-memory ledger from its source plus the branch's own true mappings.

The ledger is derived state, and closeout computes it. Developer ruling on the 260713 super
line: every closeout that follows a ``worktree_sync`` merges two ledgers by hand, and the
mechanics of that merge produced three real errors in one day -- a superseded row kept, rows
ordered so the validator rejects them, and a header that disagreed with its own first row.
None of the three needed judgement, so closeout recomputes the table instead of reading and
re-stamping whatever the file currently says, and re-running closeout repairs it.

The deterministic form is the one ``validate_ledger`` and the integration-side check already
enforced, reused here rather than reinvented:

1. the complete source ledger is the projection's trailing rows, in source order;
2. the branch's own true mappings sit ahead of that tail, newest first;
3. ``lastVerifiedCodeCommit``/``lastMemoryContentCommit`` name the projection's first row.

A mapping the branch claims is *true* only when the code repository really holds its code
commit and its memory commit is reachable from the memory state the ledger is written for.
Untrue rows are dropped, which is how a superseded row leaves the table; duplicated source
rows are collapsed; a table whose tail is not the source is reordered. Only an input that
cannot be read at all refuses, and each refusal names its remedy.

Nothing here reads or writes onboarding prose. The projection owns the mapping table and its
header alone: which cards exist and what they claim stays with the agent and the developer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_attribution import (
    MemoryAttributionError,
    attributed_commits,
    ledger_rows_from_attribution,
)
from agents_remember.kernel.memory_attribution import (
    code_commit_exists as memory_code_commit_exists,
)
from agents_remember.kernel.memory_ledger import (
    LEDGER_SCHEMA,
    LedgerError,
    LedgerRow,
    MemoryLedger,
    ledger_to_text,
    parse_ledger_text_unvalidated,
)
from agents_remember.worktrees.modules.git import (
    branch_commit,
    head_commit,
    is_ancestor,
    require_git,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

LEDGER_RELATIVE_PATH = "memory.md"

# Rows rendered into an operator payload are capped and counted: the lists are bounded so a
# pathological ledger cannot inflate a tool response, and the count says what was elided.
_PAYLOAD_ROW_LIMIT = 20

CODE_COMMIT_MISSING = "code-commit-missing"
MEMORY_COMMIT_UNREACHABLE = "memory-commit-unreachable"

_REPAIR_REMEDY = (
    "Remedy: re-run worktree_closeout_apply for this contract -- closeout recomputes memory.md "
    "from the source ledger plus the branch's own true mappings, so a malformed or "
    "partially-merged table needs no hand edit."
)

_SOURCE_REMEDY = (
    "Remedy: restore the named memory source commit or branch and retry; if memory.md was "
    "hand-edited, re-run worktree_closeout_apply for this contract to recompute it from its "
    "source."
)


class LedgerProjectionRefusal(RuntimeError):
    """An input the projection cannot read at all, carrying its named remedy.

    Only unreadable inputs refuse. A table whose *shape* is wrong -- a superseded row, a wrong
    order, a header that disagrees with its own first row -- is repaired, not refused, because
    the projection can still enumerate the branch's own mappings from it.
    """


@dataclass(frozen=True)
class LedgerSource:
    """The complete source ledger and the exact commit it was read from.

    ``excluded_rows`` are the rows the source's own table records that the exact source commit
    cannot prove: their memory content is not reachable from it, so no projection of that source
    can carry them and every read would drop them as untrue. They travel with the read rather
    than disappearing inside it, because a row that vanishes without a word is the "looks like no
    attribution exists" failure the trailer rule exists to prevent.

    ``trailered_commits`` is how many commits on the source line carry the ``Code-Commit:``
    attribution. It travels with the read because a partially backfilled line and a line the
    trailer rule never reached look identical in the rows alone, and only the count tells them
    apart -- which is what a caller needs to say whether a source is mid-transition.
    """

    commit: str
    ledger: MemoryLedger
    excluded_rows: tuple[LedgerRowRemoval, ...] = ()
    trailered_commits: int = 0


@dataclass(frozen=True)
class LedgerWorld:
    """Where each of a row's two facts must be provable before the row is kept."""

    memory_repository: Path
    memory_reachable_from: str
    code_repository: Path


@dataclass(frozen=True)
class LedgerRowRemoval:
    """One observed row the projection dropped, and why it is not a true mapping."""

    row: LedgerRow
    reason: str

    def evidence(self) -> str:
        """The refusing sentence for this row, in the vocabulary the validator uses."""

        if self.reason == CODE_COMMIT_MISSING:
            return (
                f"code commit {self.row.code_commit} does not exist in the code repository: an "
                "added row must name a commit that repository really holds"
            )
        return (
            f"memory commit {self.row.memory_commit} is not an ancestor of the landed ledger "
            "commit: an added row must name memory content the landed ledger commit carries"
        )


@dataclass(frozen=True)
class LedgerProjection:
    """The deterministic ledger for one source, and the difference from what was observed."""

    source_commit: str
    source_rows: tuple[LedgerRow, ...]
    observed_rows: tuple[LedgerRow, ...]
    observed_text: str
    projected: MemoryLedger
    projected_rows: tuple[LedgerRow, ...]
    intended_text: str
    added_rows: tuple[LedgerRow, ...]
    removed_rows: tuple[LedgerRow, ...]
    removals: tuple[LedgerRowRemoval, ...]
    missing_source_rows: tuple[LedgerRow, ...]
    reordered_rows: tuple[LedgerRow, ...]
    observed_source_rows: tuple[LedgerRow, ...]
    header_before: tuple[str, str]
    header_after: tuple[str, str]
    source_excluded_rows: tuple[LedgerRow, ...] = ()
    source_excluded_reasons: tuple[LedgerRowRemoval, ...] = ()
    source_trailered_commits: int = 0

    @property
    def is_fixed_point(self) -> bool:
        """Whether the observed table already *is* its own projection, rows and header.

        This is the integration-side question -- does the ledger equal the projection for this
        source and these commits -- and it is deliberately semantic rather than byte-exact, so
        a ledger that differs only in serialization is not refused for a cosmetic reason.
        """

        return not self.rows_differ and not self.header_changed

    @property
    def is_interleaved_projection(self) -> bool:
        """Whether the observed table is this projection with its own rows placed elsewhere.

        A master's ledger does not arrive the way a leaf's does. A leaf's closeout *writes* the
        table, so its own mappings stand above the source rows by construction. A master's line
        instead accumulates one closeout per leaf and can absorb its own source through a merge:
        LOCR's took the IAS line in three times, and because both sides had prepended a row to
        the same table the merge conflicted, so the union that resolved it holds the two sides'
        rows interleaved rather than stacked.

        The content promise is untouched by that placement, and this is the weaker question a
        caller asks when it lands the ledger it captured instead of rewriting it: the rows are
        exactly the projection's -- none added, dropped, replaced, duplicated or untrue -- and
        the source rows still stand in source order, with only where the branch's own rows sit
        left to the merge.

        Order is not otherwise free, because a reader resolves a code commit to the FIRST row that
        names it. Two rows for one code commit are normal -- a later closeout supersedes an earlier
        mapping without deleting it -- so a merge that moved the older one up would republish it as
        current, and nothing downstream would say so. The accepted table therefore resolves every
        code commit to the same memory commit the projection does; the header check is that same
        promise for the one row it names.
        """

        return (
            not self.added_rows
            and not self.removed_rows
            and not self.header_changed
            and self.observed_source_rows == self.source_rows
            and _current_mappings(self.observed_rows) == _current_mappings(self.projected_rows)
        )

    @property
    def rows_differ(self) -> bool:
        """Whether the table itself differs, ignoring serialization-only differences."""

        return self.projected_rows != self.observed_rows

    @property
    def header_changed(self) -> bool:
        return self.header_before != self.header_after

    @property
    def needs_write(self) -> bool:
        """Whether the exact bytes on disk are not the projection's canonical rendering.

        Only meaningful when the caller supplied ``observed_text``; the closeout writer is the
        one caller that must decide whether to touch the file at all, and it compares bytes so
        an already-correct ledger stays byte-identical and produces no ledger commit.
        """

        return self.intended_text != self.observed_text

    def operator_payload(self) -> dict[str, object]:
        """What recomputation changed, in row terms, and never silently.

        This is the closeout writer's report, so it needs the observed bytes: whether the file
        must be written at all is a byte question. A read-only inspection uses the semantic
        ``is_fixed_point`` instead.
        """

        return {
            "state": "repaired" if self.needs_write else "already-correct",
            "sourceCommit": self.source_commit,
            "summary": self._summary(),
            "rowsAdded": _bounded_rows(self.added_rows),
            "rowsRemoved": _bounded_rows(self.removed_rows),
            "rowsReordered": _bounded_rows(self.reordered_rows),
            "removedReasons": _bounded_removal_reasons(self.removals),
            "headerChanged": self.header_changed,
            "headerBefore": _header_payload(self.header_before),
            "headerAfter": _header_payload(self.header_after),
            "rowsBefore": len(self.observed_rows),
            "rowsAfter": len(self.projected_rows),
            "sourceRowsExcluded": len(self.source_excluded_rows),
            "sourceExcludedRows": _bounded_rows(self.source_excluded_rows),
            "sourceExcludedReasons": _bounded_removal_reasons(self.source_excluded_reasons),
            "sourceTraileredCommits": self.source_trailered_commits,
        }

    def _summary(self) -> str:
        counts = (
            f"added {len(self.added_rows)} row(s), removed {len(self.removed_rows)} row(s), "
            f"reordered {len(self.reordered_rows)} row(s), header "
            f"{'updated' if self.header_changed else 'unchanged'}"
        )
        if not self.needs_write:
            return (
                f"the memory ledger already equals the projection for source "
                f"{self.source_commit}: {counts}"
            )
        return f"repaired the memory ledger for source {self.source_commit}: {counts}"


def resolve_memory_source_commit(contract: WorktreeContract) -> str:
    """The exact memory source this contract's ledger must be a projection of.

    Closeout already requires the memory source branch head to be one of the heads its
    recorded base pair admits, and it is the same commit integration later names as the
    memory source, so the projection and the integration check measure one world.
    """

    if contract.memory_repo_path is None:
        raise LedgerProjectionRefusal(
            "external-memory ledger projection requires a memory repository. " + _SOURCE_REMEDY
        )
    if not contract.memory_source_branch:
        if not contract.memory_base_commit:
            raise LedgerProjectionRefusal(
                "the contract records neither a memory source branch nor a memory base commit, "
                "so the ledger's source cannot be resolved. " + _SOURCE_REMEDY
            )
        return contract.memory_base_commit
    try:
        return branch_commit(contract.memory_repo_path, contract.memory_source_branch)
    except RuntimeError as error:
        raise LedgerProjectionRefusal(
            f"memory source branch {contract.memory_source_branch!r} cannot be resolved in "
            f"{contract.memory_repo_path.as_posix()}: {error}. " + _SOURCE_REMEDY
        ) from error


def read_ledger_source(
    repository: Path,
    commit: str,
    relative: str = LEDGER_RELATIVE_PATH,
) -> LedgerSource:
    """The complete source ledger at one exact commit, projected from its own attribution.

    The ledger is derived state: it is the memory commits' ``Code-Commit:`` trailers, read back
    from the history that hashes them. This is where every reader of the source ledger now goes,
    so ``memory.md`` is no longer the *authority* for what the source says.

    A commit written before the trailer rule carries no trailer, and its attribution is still
    whatever the ledger it carried said, so *that commit alone* is read from its own blob. The
    fallback is per commit rather than a mode, which is what keeps it from becoming the
    compatibility layer this change removes: backfilling the history writes the trailer onto
    those commits, and then there is nothing left for the fallback to read and nothing to turn
    off. A source that resolves and carries neither -- no trailer anywhere, no ledger blob --
    contributes no rows instead of refusing, because that is the bootstrap state the
    ledger-creation paths start from and an empty tail is exactly what they need.

    The blob read is the COMMON case and not a fallback *mode*, which is the one thing this
    reader got wrong before: it returned the trailers alone as soon as the history carried a
    single one, so a line with 479 recorded rows and one trailered commit read as a one-row
    source and every pre-rule row vanished from its tail. The trailers are merged INTO the blob's
    rows now, so a partially backfilled history reads as the union its own table records.

    The read is faithful and does not adjudicate: every row the source's own table records and
    every row its trailered commits name are returned, in the order the table records them. What
    the projection can prove is the projection's question, and it asks it of the tail by the same
    rule it asks of the branch's own rows -- a tail row whose memory content the landing does not
    carry is dropped there, with its reason and its count in the payload, never here in silence.

    Two inputs still refuse: a history that cannot be walked, and a ledger blob that exists and
    cannot be parsed. Both name their remedy, and neither is repaired by editing a table.
    """

    try:
        commits = attributed_commits(repository, tip=commit)
    except MemoryAttributionError as error:
        raise LedgerProjectionRefusal(f"{error}. " + _SOURCE_REMEDY) from error
    rows = _rows_the_source_records(repository, commit, relative)
    trailered = ledger_rows_from_attribution(commits)
    kept, excluded = _tail_rows(rows, trailered, repository, commit)
    return LedgerSource(
        commit,
        _source_ledger_with_rows(kept),
        excluded_rows=tuple(excluded),
        trailered_commits=len(trailered),
    )


def _tail_rows(
    recorded: Sequence[LedgerRow],
    trailered: Sequence[LedgerRow],
    repository: Path,
    commit: str,
) -> tuple[list[LedgerRow], list[LedgerRowRemoval]]:
    """The tail the exact source commit can prove, and the rows it cannot.

    The source's own table is offered first because its order is the order the tail must keep,
    and a trailered mapping is normally a row that table already records. When it is not -- a
    partially backfilled line, where the trailer names a commit whose row never reached the file
    -- it joins the tail rather than displacing the recorded order.

    A row is kept when the exact source commit carries the memory content it names. One class is
    therefore not kept, and it is the class that made a real leaf's ledger read as damaged: a row
    whose memory commit the source does not carry describes content the source never had, so the
    projection would drop it from the tail as untrue on every read. Removing it here makes the
    tail equal to what the source can prove -- which is what makes an already-correct ledger a
    fixed point -- and the removals travel back to the caller, so the class is counted and
    reported instead of vanishing. An abbreviated object name is resolved by git's ancestry test
    rather than compared as a string, so a short cell is not mistaken for a dead one.
    """

    kept: list[LedgerRow] = []
    excluded: list[LedgerRowRemoval] = []
    for row in _distinct([*recorded, *trailered]):
        if is_ancestor(repository, row.memory_commit, commit):
            kept.append(row)
        else:
            excluded.append(LedgerRowRemoval(row, MEMORY_COMMIT_UNREACHABLE))
    return kept, excluded


def _distinct(rows: Sequence[LedgerRow]) -> list[LedgerRow]:
    """The rows in the order given, first occurrence kept, no copy counted twice."""

    seen: set[LedgerRow] = set()
    return [row for row in rows if not (row in seen or seen.add(row))]


def _rows_the_source_records(repository: Path, commit: str, relative: str) -> list[LedgerRow]:
    """The rows the source's own table records, read whether or not its history carries trailers.

    The blob is the *record* the pre-trailer history left, and it is read even when the history
    carries trailers, because a partially backfilled line records almost every row there and a
    reader that skipped it would report a complete line as a nearly empty one.

    Deliberately the unvalidated parse: the header is recomputed from row one by the projection
    that writes the ledger, and one of the shapes that recomputation repairs is a header that
    disagrees with its own first row. Refusing the read instead would hide the repair.
    """

    shown = run_git(repository, ["show", f"{commit}:{relative}"])
    if shown.returncode != 0:
        if _commit_carries_no_ledger(repository, commit, relative):
            return []
        raise LedgerProjectionRefusal(
            f"memory ledger source {commit}:{relative} is not readable in "
            f"{repository.as_posix()}: {shown.stderr.strip() or 'git show failed'}. "
            + _SOURCE_REMEDY
        )
    try:
        return list(parse_ledger_text_unvalidated(shown.stdout).rows)
    except LedgerError as error:
        raise LedgerProjectionRefusal(
            f"memory ledger source {commit}:{relative} is not a parseable ledger: {error}. "
            + _SOURCE_REMEDY
        ) from error


def _source_ledger_with_rows(rows: Sequence[LedgerRow]) -> MemoryLedger:
    """The ledger the attributed history projects, carrying only what git told us.

    The metadata fields name the source's own provenance and are not facts any trailer records,
    so they are left empty here rather than invented: the projection reads ``rows`` and nothing
    else, and the header of the ledger it *writes* is recomputed from row one, which is the one
    promise ``validate_ledger`` makes about it.
    """

    return MemoryLedger(
        schema=LEDGER_SCHEMA,
        repo_name="",
        base_code_commit="",
        base_memory_commit="",
        last_verified_code_commit="",
        last_memory_content_commit="",
        sort_order="newest-first",
        rows=list(rows),
    )


def _commit_carries_no_ledger(repository: Path, commit: str, relative: str) -> bool:
    """Whether the commit itself resolves and simply has no blob at the ledger path."""

    if run_git(repository, ["cat-file", "-e", f"{commit}^{{commit}}"]).returncode != 0:
        return False
    return run_git(repository, ["cat-file", "-e", f"{commit}:{relative}"]).returncode != 0


def _empty_source_ledger() -> MemoryLedger:
    """The empty tail for a memory source that carries no ledger yet."""

    return MemoryLedger(
        schema=LEDGER_SCHEMA,
        repo_name="",
        base_code_commit="",
        base_memory_commit="",
        last_verified_code_commit="",
        last_memory_content_commit="",
        sort_order="newest-first",
        rows=[],
    )


def read_ledger_text(text: str, *, label: str) -> MemoryLedger:
    """Parse a ledger the caller already holds, refusing legibly when it is unreadable.

    Deliberately the unvalidated parse: the table this function reads is the one being
    repaired, and a header that disagrees with its own first row is one of the shapes the
    repair exists to fix. Structural damage still refuses.
    """

    try:
        return parse_ledger_text_unvalidated(text)
    except LedgerError as error:
        raise LedgerProjectionRefusal(
            f"{label} is not a parseable ledger: {error}. " + _REPAIR_REMEDY
        ) from error


def code_commit_exists(repository: Path, commit: str) -> bool:
    """Whether the code repository really holds ``commit`` as a commit object."""

    return memory_code_commit_exists(repository, commit)


def inspect_ledger_projection(contract: WorktreeContract) -> dict[str, object]:
    """Report whether a landed ledger still equals its projection, without writing anything.

    The re-run and already-closed recovery paths do not touch the ledger, and they must still
    answer for it: a closeout payload that says nothing about the ledger is exactly the silence
    the operator-legibility rule forbids. Failures are reported, never raised, because this is
    evidence about a completed step rather than a new gate on it.
    """

    try:
        repair = contract_ledger_projection(contract)
    except (OSError, RuntimeError) as error:
        return {
            "state": "not-recomputed",
            "summary": (
                "this closeout path recorded the existing ledger commits without recomputing "
                "the ledger, and the projection could not be read for this payload"
            ),
            "reason": str(error),
        }
    payload = repair.operator_payload()
    payload["state"] = "already-correct" if repair.is_fixed_point else "diverged"
    return payload


def contract_ledger_projection(
    contract: WorktreeContract,
    additions: Sequence[LedgerRow] = (),
) -> LedgerProjection:
    """The projection for one live external-memory contract, and the difference from the file.

    Every fact comes from the world rather than from the table being repaired: the source rows
    from the memory source commit, the truth of each claimed row from the code repository and
    the memory work branch, and the exact bytes from the ledger file itself.
    """

    if contract.memory_repo_path is None or contract.ledger_path is None:
        raise LedgerProjectionRefusal(
            "external-memory ledger projection requires a memory repository and a ledger. "
            + _REPAIR_REMEDY
        )
    observed_text, memory_reachable_from = observed_ledger_state(contract)
    return project_ledger(
        source=read_ledger_source(
            contract.memory_repo_path,
            resolve_memory_source_commit(contract),
        ),
        observed=read_ledger_text(observed_text, label="the live memory.md"),
        observed_text=observed_text,
        world=LedgerWorld(
            memory_repository=contract.memory_repo_path,
            memory_reachable_from=memory_reachable_from,
            code_repository=contract.code_repo_path,
        ),
        additions=additions,
    )


def observed_ledger_state(contract: WorktreeContract) -> tuple[str, str]:
    """The ledger bytes this contract's closeout owed, and the state its rows must be true against.

    A leaf writes its ledger inside its own memory worktree, so the file on disk is the observed
    table and the worktree HEAD is the memory state its rows are proved reachable from. A series
    owns no memory worktree at all: its ledger is the blob at the exact memory work branch tip,
    which is the artifact its closeout records and its integration lands, so that tip is both the
    observed table and the reachable state. Reading the live file for a series is not an option --
    there is none -- and reading a *stale* one would be worse: the evidence would describe a table
    the contract no longer names.
    """

    if contract.memory_repo_path is None or contract.ledger_path is None:
        raise LedgerProjectionRefusal(
            "external-memory ledger projection requires a memory repository and a ledger. "
            + _REPAIR_REMEDY
        )
    if contract.memory_worktree is not None:
        return (
            contract.ledger_path.read_text(encoding="utf-8"),
            head_commit(contract.memory_worktree),
        )
    tip = branch_commit(contract.memory_repo_path, contract.memory_work_branch)
    return (
        require_git(
            contract.memory_repo_path,
            ["show", f"{tip}:{LEDGER_RELATIVE_PATH}"],
        ),
        tip,
    )


def project_ledger(
    *,
    source: LedgerSource,
    observed: MemoryLedger,
    world: LedgerWorld,
    observed_text: str = "",
    additions: Sequence[LedgerRow] = (),
) -> LedgerProjection:
    """The projection for one source, plus every difference from the observed table.

    ``additions`` are the mappings this closeout just proved into existence and therefore
    wants recorded; they are subject to the same truth test as the rows already observed, so
    a re-run that adds the row it already carried changes nothing. ``observed_text`` is the
    exact bytes the caller is looking at, and only ``needs_write`` depends on it.
    """

    source_rows = tuple(source.ledger.rows)
    source_set = set(source_rows)
    candidates = _own_row_candidates(observed.rows, additions, source_set)
    kept, removals = _kept_true_rows(candidates, world)
    ordered = _newest_first(world, kept)
    projected_rows = (*ordered, *source_rows)
    if not projected_rows:
        raise LedgerProjectionRefusal(
            "not one mapping in memory.md is true: every row names a code commit the code "
            "repository does not hold or memory content that is not reachable, and the source "
            "ledger has no rows to fall back on. " + _REPAIR_REMEDY
        )
    header_after = (projected_rows[0].code_commit, projected_rows[0].memory_commit)
    projected = MemoryLedger(
        schema=LEDGER_SCHEMA,
        repo_name=observed.repo_name,
        base_code_commit=observed.base_code_commit,
        base_memory_commit=observed.base_memory_commit,
        last_verified_code_commit=header_after[0],
        last_memory_content_commit=header_after[1],
        sort_order="newest-first",
        rows=list(projected_rows),
    )
    observed_set = set(observed.rows)
    return LedgerProjection(
        source_commit=source.commit,
        source_rows=source_rows,
        observed_rows=tuple(observed.rows),
        observed_text=observed_text,
        projected=projected,
        projected_rows=projected_rows,
        intended_text=ledger_to_text(projected),
        added_rows=tuple(_multiset_difference(projected_rows, observed.rows)),
        removed_rows=tuple(_multiset_difference(observed.rows, projected_rows)),
        removals=tuple(removals),
        missing_source_rows=tuple(row for row in source_rows if row not in observed_set),
        reordered_rows=tuple(_reordered_rows(observed.rows, projected_rows)),
        observed_source_rows=tuple(row for row in observed.rows if row in source_set),
        header_before=(
            observed.last_verified_code_commit,
            observed.last_memory_content_commit,
        ),
        header_after=header_after,
        source_excluded_rows=tuple(removal.row for removal in source.excluded_rows),
        source_excluded_reasons=tuple(source.excluded_rows),
        source_trailered_commits=source.trailered_commits,
    )


def _own_row_candidates(
    observed_rows: Sequence[LedgerRow],
    additions: Sequence[LedgerRow],
    source_set: set[LedgerRow],
) -> list[LedgerRow]:
    """Every row the branch can claim as its own, deduplicated, ahead of the source rows.

    The distinction from the preserved source rows is membership, not a count: a row the
    complete source ledger already carries belongs to the source and is placed by the tail,
    and everything else is a mapping some closeout of this branch added.
    """

    candidates: list[LedgerRow] = []
    for row in (*observed_rows, *additions):
        if row in source_set or row in candidates:
            continue
        candidates.append(row)
    return candidates


def _kept_true_rows(
    candidates: Sequence[LedgerRow],
    world: LedgerWorld,
) -> tuple[list[LedgerRow], list[LedgerRowRemoval]]:
    """Split the branch's claimed rows into the true ones and the removals with reasons."""

    kept: list[LedgerRow] = []
    removals: list[LedgerRowRemoval] = []
    for row in candidates:
        reason = _untrue_reason(row, world)
        if reason is None:
            kept.append(row)
        else:
            removals.append(LedgerRowRemoval(row, reason))
    return kept, removals


def _untrue_reason(row: LedgerRow, world: LedgerWorld) -> str | None:
    if not code_commit_exists(world.code_repository, row.code_commit):
        return CODE_COMMIT_MISSING
    if not is_ancestor(world.memory_repository, row.memory_commit, world.memory_reachable_from):
        return MEMORY_COMMIT_UNREACHABLE
    return None


def _newest_first(world: LedgerWorld, rows: Sequence[LedgerRow]) -> list[LedgerRow]:
    """Order the branch's own rows newest first by memory-commit ancestry.

    Closeout records one mapping per closeout, each memory content commit descending from the
    one before it, so ancestry is the branch's own order and it is read from the repository
    rather than from the order the possibly-hand-edited table happens to carry. Rows with no
    ancestry relation to each other keep the order they were observed in, which keeps the
    result stable instead of inventing an order the world does not state.
    """

    ordered: list[LedgerRow] = []
    for row in rows:
        position = len(ordered)
        for index, existing in enumerate(ordered):
            if is_ancestor(world.memory_repository, existing.memory_commit, row.memory_commit):
                position = index
                break
        ordered.insert(position, row)
    return ordered


def _current_mappings(rows: Sequence[LedgerRow]) -> dict[str, str]:
    """What each code commit actually resolves to, for every code commit the table names.

    ``find_mapping`` returns the FIRST row naming a code commit, so the table's order decides which
    memory commit that code commit is current for. Two rows for one code commit are legitimate -- a
    later closeout supersedes an earlier mapping without deleting it -- which is exactly why the
    relative order of such a pair is a content promise rather than a cosmetic one.
    """

    current: dict[str, str] = {}
    for row in rows:
        current.setdefault(row.code_commit, row.memory_commit)
    return current


def _multiset_difference(left: Sequence[LedgerRow], right: Sequence[LedgerRow]) -> list[LedgerRow]:
    """The copies in ``left`` that ``right`` does not account for, in ``left`` order."""

    used = Counter(right)
    difference: list[LedgerRow] = []
    for row in left:
        if used[row] > 0:
            used[row] -= 1
        else:
            difference.append(row)
    return difference


def _reordered_rows(
    observed_rows: Sequence[LedgerRow],
    projected_rows: Sequence[LedgerRow],
) -> list[LedgerRow]:
    """The rows that survive recomputation but at a different position in the table."""

    observed_common = _common_rows(observed_rows, set(projected_rows))
    projected_common = _common_rows(projected_rows, set(observed_rows))
    if observed_common == projected_common:
        return []
    moved: list[LedgerRow] = []
    for position, row in enumerate(observed_common):
        if (position >= len(projected_common) or projected_common[position] != row) and (
            row not in moved
        ):
            moved.append(row)
    return moved


def _common_rows(rows: Sequence[LedgerRow], present: set[LedgerRow]) -> list[LedgerRow]:
    return [row for row in rows if row in present]


def _header_payload(header: tuple[str, str]) -> dict[str, str]:
    return {
        "lastVerifiedCodeCommit": header[0],
        "lastMemoryContentCommit": header[1],
    }


def _bounded_rows(rows: Sequence[LedgerRow]) -> list[str]:
    return [f"{row.code_commit} -> {row.memory_commit}" for row in rows[:_PAYLOAD_ROW_LIMIT]] + (
        [f"and {len(rows) - _PAYLOAD_ROW_LIMIT} more"] if len(rows) > _PAYLOAD_ROW_LIMIT else []
    )


def _bounded_removal_reasons(removals: Sequence[LedgerRowRemoval]) -> list[dict[str, str]]:
    return [
        {
            "row": f"{removal.row.code_commit} -> {removal.row.memory_commit}",
            "reason": removal.reason,
        }
        for removal in removals[:_PAYLOAD_ROW_LIMIT]
    ]
