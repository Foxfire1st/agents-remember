"""Derive memory mappings from Git and report differences from the disposable cache.

Only committed Code-Commit attribution contributes mappings. Cache rows and headers are
observations, including when they name real objects; absent or malformed cache is a miss.
Unreadable Git history remains a separate error, and invalid code attributions are reported.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from agents_remember.kernel.memory_attribution import code_commit_exists
from agents_remember.kernel.memory_cache import derive_memory_ledger
from agents_remember.kernel.memory_ledger import (
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

# Rows rendered into an operator payload are capped and counted: the lists are bounded so a
# pathological ledger cannot inflate a tool response, and the count says what was elided.
_PAYLOAD_ROW_LIMIT = 20

CODE_COMMIT_MISSING = "code-commit-missing"
MEMORY_COMMIT_UNREACHABLE = "memory-commit-unreachable"
ATTRIBUTION_MISSING = "memory-attribution-missing"

_SOURCE_REMEDY = (
    "Remedy: restore access to the named Git repository, commit or branch and retry. "
    "Editing the memory cache cannot repair unreadable Git history."
)


class LedgerProjectionRefusal(RuntimeError):
    """Git authority the projection cannot read, distinct from a disposable cache miss."""


@dataclass(frozen=True)
class LedgerSource:
    """Attributed source history, its exact ref, and any invalid code attributions.

    ``trailered_commits`` counts attribution before code-existence filtering, so invalid
    attributions remain distinguishable from a history that never recorded them.
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
                "attribution must name a commit that repository really holds"
            )
        if self.reason == ATTRIBUTION_MISSING:
            return "the memory commit does not attribute this code commit in Git"
        if self.reason == "duplicate-mapping":
            return "the cached mapping occurs more than once"
        return (
            f"memory commit {self.row.memory_commit} is not reachable from the selected memory tip"
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
    cache_hit: bool = True

    @property
    def is_fixed_point(self) -> bool:
        """Whether cached rows and their current header match committed attribution."""

        return self.cache_hit and not self.rows_differ and not self.header_changed

    @property
    def is_interleaved_projection(self) -> bool:
        """Whether the cache preserves the same current mappings and source order."""

        return (
            self.cache_hit
            and not self.added_rows
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
        """Whether the disposable cache differs from the canonical computed rendering."""

        return self.intended_text != self.observed_text

    def operator_payload(self) -> dict[str, object]:
        """Informational cache differences and invalid attributions; no Git work is decided here."""

        return {
            "state": (
                "cache-miss"
                if not self.cache_hit
                else "diverged"
                if self.needs_write
                else "already-correct"
            ),
            "cacheState": "hit" if self.cache_hit else "miss",
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
        return f"the memory cache differs from Git history at {self.source_commit}: {counts}"


def resolve_memory_source_commit(contract: WorktreeContract) -> str:
    """Resolve the named source Git history used to describe the consumer projection."""

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
    *,
    code_repository: Path | None = None,
    repo_name: str | None = None,
) -> LedgerSource:
    """Derive one source solely from committed attribution, reporting invalid code objects."""

    try:
        ledger = derive_memory_ledger(repository, commit, repo_name=repo_name)
        if code_repository is not None:
            require_git(code_repository, ["rev-parse", "--git-dir"])
    except (OSError, RuntimeError) as error:
        raise LedgerProjectionRefusal(f"{error}. " + _SOURCE_REMEDY) from error
    rows = ledger.rows
    excluded = tuple(
        LedgerRowRemoval(row, CODE_COMMIT_MISSING)
        for row in rows
        if code_repository is not None and not code_commit_exists(code_repository, row.code_commit)
    )
    rejected = {removal.row for removal in excluded}
    return LedgerSource(
        commit,
        _ledger_with_rows(ledger, [row for row in rows if row not in rejected]),
        excluded_rows=excluded,
        trailered_commits=len(rows),
    )


def _ledger_with_rows(ledger: MemoryLedger, rows: list[LedgerRow]) -> MemoryLedger:
    """Recompute all revision metadata from the retained attributed rows."""

    newest = rows[0] if rows else None
    oldest = rows[-1] if rows else None
    return replace(
        ledger,
        base_code_commit=oldest.code_commit if oldest else "",
        base_memory_commit=oldest.memory_commit if oldest else "",
        last_verified_code_commit=newest.code_commit if newest else "",
        last_memory_content_commit=newest.memory_commit if newest else "",
        rows=rows,
    )


def read_ledger_text(text: str) -> MemoryLedger | None:
    """Observe a parseable cache, or report a cache miss without affecting Git-derived data."""

    try:
        return parse_ledger_text_unvalidated(text)
    except LedgerError:
        return None


def inspect_ledger_projection(contract: WorktreeContract) -> dict[str, object]:
    """Report computed mappings and cache differences without writing or deciding Git work."""

    try:
        projection = contract_ledger_projection(contract)
    except (OSError, RuntimeError) as error:
        return {
            "state": "not-recomputed",
            "summary": "the selected Git history could not be read for the consumer ledger",
            "reason": str(error),
        }
    return projection.operator_payload()


def contract_ledger_projection(contract: WorktreeContract) -> LedgerProjection:
    """Derive the selected memory history and compare it with an optional local cache."""

    if contract.memory_repo_path is None:
        raise LedgerProjectionRefusal(
            "external-memory ledger projection requires a memory repository. " + _SOURCE_REMEDY
        )
    observed_text, memory_reachable_from = observed_ledger_state(contract)
    return project_ledger(
        source=read_ledger_source(
            contract.memory_repo_path,
            resolve_memory_source_commit(contract),
            code_repository=contract.code_repo_path,
            repo_name=contract.repo_name,
        ),
        observed=read_ledger_text(observed_text),
        observed_text=observed_text,
        world=LedgerWorld(
            memory_repository=contract.memory_repo_path,
            memory_reachable_from=memory_reachable_from,
            code_repository=contract.code_repo_path,
        ),
    )


def observed_ledger_state(contract: WorktreeContract) -> tuple[str, str]:
    """Resolve the actual memory tip; cache availability never controls that Git read."""

    if contract.memory_repo_path is None:
        raise LedgerProjectionRefusal(
            "external-memory ledger projection requires a memory repository. " + _SOURCE_REMEDY
        )
    tip = (
        head_commit(contract.memory_worktree)
        if contract.memory_worktree is not None
        else branch_commit(contract.memory_repo_path, contract.memory_work_branch)
    )
    try:
        text = contract.ledger_path.read_text(encoding="utf-8") if contract.ledger_path else ""
    except (OSError, UnicodeError):
        text = ""
    return text, tip


def project_ledger(
    *,
    source: LedgerSource,
    observed: MemoryLedger | None,
    world: LedgerWorld,
    observed_text: str = "",
) -> LedgerProjection:
    """Compare cache observations with actual attributed history, never adding cached pairs."""

    history = read_ledger_source(
        world.memory_repository,
        world.memory_reachable_from,
        code_repository=world.code_repository,
        repo_name=source.ledger.repo_name,
    )
    projected = history.ledger
    projected_rows = tuple(projected.rows)
    expected_set = set(projected_rows)
    kept_source, source_removals = _kept_true_rows(source.ledger.rows, world)
    source_rows = tuple(kept_source)
    source_set = set(source_rows)
    source_excluded = tuple(dict.fromkeys([*source.excluded_rows, *source_removals]))
    cache_hit = observed is not None
    observed = observed if observed is not None else _ledger_with_rows(projected, [])
    removed_rows = tuple(_multiset_difference(observed.rows, projected_rows))
    removals = tuple(
        dict.fromkeys(
            [
                *history.excluded_rows,
                *(
                    LedgerRowRemoval(
                        row,
                        "duplicate-mapping"
                        if row in expected_set
                        else _untrue_reason(row, world) or ATTRIBUTION_MISSING,
                    )
                    for row in removed_rows
                ),
            ]
        )
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
        removed_rows=removed_rows,
        removals=removals,
        missing_source_rows=tuple(row for row in source_rows if row not in observed_set),
        reordered_rows=tuple(_reordered_rows(observed.rows, projected_rows)),
        observed_source_rows=tuple(row for row in observed.rows if row in source_set),
        header_before=(observed.last_verified_code_commit, observed.last_memory_content_commit),
        header_after=(projected.last_verified_code_commit, projected.last_memory_content_commit),
        source_excluded_rows=tuple(removal.row for removal in source_excluded),
        source_excluded_reasons=source_excluded,
        source_trailered_commits=source.trailered_commits,
        cache_hit=cache_hit,
    )


def _kept_true_rows(
    candidates: Sequence[LedgerRow],
    world: LedgerWorld,
) -> tuple[list[LedgerRow], list[LedgerRowRemoval]]:
    """Filter attributed source rows against the selected Git history and code repository."""

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
