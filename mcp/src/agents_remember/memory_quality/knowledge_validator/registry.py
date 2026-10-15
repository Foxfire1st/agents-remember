"""The validator's rule registry (MIK-R22 rule 9) and the context every rule reads.

A rule is a :class:`ValidationRule`: a stable ``id``, the packet rule that owns it, a one-line
summary, a ``check`` over the :class:`ValidationContext`, and, since MIK-R53 rule 10, one of two
named reasons to report instead of refusing at a route:

* ``merge_reports``: the rule refuses at every commit and landing route, and only reports at a
  sync merge (two or more bases, no leaf publication). A merge can create the state from two
  valid sides, and it can be repaired only after the merge exists. The next leaf publication or
  landing refuses it.
* ``migration_debt``: a finding that a comparison base already holds (``Finding.carried``) is
  reported while the tree's migration is not complete, and refused at every route once it is. A
  finding that no base holds is new and refuses at once. The migration is complete when every
  onboarding route has the latest census status ``migrated`` or ``excluded`` (the reading of
  ``knowledge-census check --final``, MIK-R77). The conversion writes these states, not a leaf.

A rule can no longer be registered as plainly report-only: :func:`register_rule` refuses a rule
with ``report_only`` set. Every violation still says whether it refuses, through
:attr:`Violation.report_only <agents_remember.memory_quality.knowledge_validator.report.Violation.report_only>`,
and every reported finding names its reason and the route that will refuse it (MIK-R53 rule 12).

``writer_reports`` marks a rule that **refuses at every commit route but is only reported inside the
writer**: a leaf may break it mid-way and repair it before closeout. MIK-R04's family route rules
(Coverage, Non-empty, an added route's directory, and non-nesting) carry it (MIK-R04 rule 6,
MIK-R54 rule 11), as do MIK-R53's designated refusing rules. The validator's own
report is unchanged -- :func:`validate_tree` and :func:`require_valid_commit` still refuse such a
finding -- so a writer (MIK-R12) reads :func:`writer_reported_rule_ids` and treats those violations
as reports; no commit route may.

MIK-R22 registers its own rules when :mod:`.rules` is imported. Later packets add theirs with
:func:`register_rule` -- MIK-R04 (family Coverage, Non-empty and ``route_unassigned``), MIK-R20 (the
census schemas and their append-only rules), MIK-R27 (admission) and MIK-R13 (decision content) --
and every registered rule then runs wherever the validator runs (rule 8). There is no way to run a
subset at a commit route.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import cached_property
from typing import Final

from agents_remember.memory_quality.knowledge_census.files import onboarding_routes, read_censuses
from agents_remember.memory_quality.knowledge_validator.family_routes import (
    RoutedFamily,
    family_route_states,
)
from agents_remember.memory_quality.knowledge_validator.parsed import (
    ParsedTree,
    SidecarFile,
    parse_sidecars_leniently,
)
from agents_remember.memory_quality.knowledge_validator.trees import CodeTree, KnowledgeTree
from agents_remember.models.knowledge_files.census import governing_status
from agents_remember.models.knowledge_files.shapes import Anchor, AnchorTarget
from agents_remember.models.knowledge_files.sidecars import (
    FileSidecar,
    ProofEntry,
    RealizationEntry,
)


@dataclass(frozen=True)
class Finding:
    """What a check reports; the validator adds the rule's ID and its route's report/refuse."""

    path: str
    field: str
    message: str
    carried: bool = False
    """A comparison base already holds this finding's state (MIK-R53 rule 10): a ``migration_debt``
    rule's validator reports it while the migration is not complete, and a finding no base holds
    is new and refuses at once."""


RuleCheck = Callable[["ValidationContext"], Iterable[Finding]]


@dataclass(frozen=True)
class ValidationRule:
    id: str
    owner: str
    summary: str
    check: RuleCheck
    report_only: bool = False
    """Legacy flag, always ``False``: MIK-R53 rule 10 refuses a plainly report-only registration,
    so a rule reports at a route only for the named reason ``merge_reports`` or
    ``migration_debt`` carries."""
    writer_reports: bool = False
    merge_reports: bool = False
    """MIK-R53 rule 10: refuse at every commit and landing route, report only at a sync merge."""
    migration_debt: bool = False
    """MIK-R53 rule 10: report a base-carried finding until the migration is complete."""
    merge_requires_carried: bool = False
    """MIK-R54 rule 11: only an equal-base target gets the sync-merge reporting treatment."""


_REGISTRY: dict[str, ValidationRule] = {}


def register_rule(rule: ValidationRule) -> ValidationRule:
    """Add ``rule`` to the registry; an ID is registered once.

    MIK-R53 rule 10: a rule is no longer registered as plainly report-only -- a violation
    reports at a route only for the named reason ``merge_reports`` or ``migration_debt`` carries.
    """

    if rule.report_only:
        raise ValueError(
            f"validator rule {rule.id!r} is plainly report-only: register it with "
            "merge_reports or migration_debt (MIK-R53 rule 10)"
        )
    if rule.merge_reports and rule.migration_debt:
        raise ValueError(
            f"validator rule {rule.id!r} carries both merge_reports and migration_debt: "
            "a rule reports for one named reason (MIK-R53 rule 10)"
        )
    if rule.id in _REGISTRY:
        raise ValueError(f"validator rule {rule.id!r} is already registered")
    _REGISTRY[rule.id] = rule
    return rule


def registered_rules() -> tuple[ValidationRule, ...]:
    """Every registered rule, in registration order."""

    return tuple(_REGISTRY.values())


def writer_reported_rule_ids() -> frozenset[str]:
    """The IDs of the refusing rules a writer reports instead of refusing (``writer_reports``)."""

    return frozenset(
        rule.id for rule in _REGISTRY.values() if rule.writer_reports and not rule.report_only
    )


def sidecar_entries(sidecar: SidecarFile) -> tuple[RealizationEntry | ProofEntry, ...]:
    """The realization and proof entries of a file sidecar (none for a route sidecar)."""

    if isinstance(sidecar.sidecar, FileSidecar):
        return (*sidecar.sidecar.realizes, *(sidecar.sidecar.proves or ()))
    return ()


def anchor_key(anchor: Anchor, sidecar: SidecarFile) -> str:
    """The comparison key of an anchor as it sits in ``sidecar``, its own path filled in."""

    document = anchor.to_document()
    document.setdefault("path", sidecar.sidecar.path)
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class BaseAnchors:
    """The anchors of the comparison bases: per entry ID, and per sidecar for reference targets."""

    entries: frozenset[tuple[str, str]]
    references: frozenset[tuple[str, str]]

    def carries_entry(self, entry_id: str, key: str) -> bool:
        return (entry_id, key) in self.entries

    def carries_reference(self, sidecar_path: str, key: str) -> bool:
        return (sidecar_path, key) in self.references


def _base_anchors(bases: Iterable[KnowledgeTree]) -> BaseAnchors:
    entries: set[tuple[str, str]] = set()
    references: set[tuple[str, str]] = set()
    for base in bases:
        for sidecar in parse_sidecars_leniently(base):
            for reference in sidecar.sidecar.references.values():
                for target in reference.targets:
                    if isinstance(target, AnchorTarget):
                        references.add((sidecar.path, anchor_key(target.anchor, sidecar)))
            for entry in sidecar_entries(sidecar):
                entries.add((entry.id, anchor_key(entry.anchor, sidecar)))
    return BaseAnchors(frozenset(entries), frozenset(references))


MIGRATED_ROUTE_STATUSES: Final = frozenset({"migrated", "excluded"})
"""A migration is complete when every onboarding route holds one of these latest statuses."""


@dataclass(frozen=True)
class MigrationState:
    """How far the candidate tree's migration has come (MIK-R53 rule 10).

    ``routes`` are every onboarding route the candidate's censuses record; ``unmigrated`` are the
    ones whose latest status entry across every census is not ``migrated`` or ``excluded``. The
    migration is complete only when at least one route is recorded and none is left: a tree with
    no census, or one whose census cannot be read, is not complete, so its debt still reports.
    """

    complete: bool
    routes: tuple[str, ...] = ()
    unmigrated: tuple[str, ...] = ()


def migration_state(candidate: KnowledgeTree) -> MigrationState:
    """Read the candidate's migration state from its census files (MIK-R77's ``--final`` reading).

    Every onboarding route is a route that governs at least one inventory row, or that a status
    file names; its governing status is the latest entry across every census (MIK-R10 rule 1).
    """

    tree = read_censuses(candidate.files)
    routes: set[str] = set()
    for census in tree.censuses.values():
        if census.inventory is not None:
            routes.update(census.inventory.routes)
        routes.update(route.route for route in census.routes.values())
    # The population is the union of the candidate tree's own onboarding routes and every
    # census route: a pinned inventory cannot list a route the candidate added later, and a
    # route with no status entry stays not migrated (MIK-R53 rule 10).
    routes.update(onboarding_routes(candidate.files))
    histories = list(tree.route_histories())
    unmigrated = sorted(
        route
        for route in routes
        if governing_status(route, histories).status not in MIGRATED_ROUTE_STATUSES
    )
    return MigrationState(
        complete=bool(routes) and not tree.problems and not unmigrated,
        routes=tuple(sorted(routes)),
        unmigrated=tuple(unmigrated),
    )


def merge_report_note(context: ValidationContext) -> str:
    """The reason a ``merge_reports`` finding reports at this route, and what refuses it.

    A standalone conversion names itself: it authors the states its later routes judge.
    A sync merge names the merge. Both name the next refusing leaf publication/landing.
    """

    if context.conversion and not context.is_sync_merge:
        return (
            "reported at this standalone conversion: the conversion authors the states "
            "its later routes judge; "
            "the next leaf publication or landing refuses it (MIK-R53 rule 10)"
        )
    return (
        "reported at this sync merge: a merge can create this state from two valid sides, "
        "and it is repaired only after the merge exists; "
        "the next leaf publication or landing refuses it (MIK-R53 rule 10)"
    )


def debt_report_note(state: MigrationState) -> str:
    """The reason a ``migration_debt`` finding reports, and how many routes are left."""

    if not state.routes:
        return (
            "reported as migration debt: the conversion writes these states, not a leaf; "
            "refused at every route once every onboarding route is migrated or excluded -- "
            "no census records any onboarding route yet, so the migration is not complete "
            "(MIK-R53 rule 10)"
        )
    return (
        "reported as migration debt: the conversion writes these states, not a leaf; "
        "refused at every route once every onboarding route is migrated or excluded -- "
        f"{len(state.unmigrated)} of {len(state.routes)} onboarding route(s) not yet migrated "
        "(MIK-R53 rule 10)"
    )


def merge_reports_at(context: ValidationContext) -> bool:
    """Whether a ``merge_reports`` finding reports at this route (MIK-R53 rule 10).

    Only at a sync merge -- and at a standalone conversion, which authors the tree its later
    routes judge. Every commit and landing route refuses.
    """

    return context.is_sync_merge or context.conversion


def debt_reports_at(context: ValidationContext, *, carried: bool) -> bool:
    """Whether a ``migration_debt`` finding with this carried state reports (MIK-R53 rule 10).

    A standalone conversion authors the debt its later routes judge, so it reports there. At
    every other route a finding a comparison base already holds reports while the migration is
    not complete; a new finding refuses at once, and every finding refuses once complete.
    """

    if context.conversion:
        return True
    return carried and not context.migration.complete


@dataclass(frozen=True)
class ValidationContext:
    """One validation: the candidate K_C, its comparison bases and its paired code tree.

    ``bases`` is K_B at a commit route and every parent at a merge. ``code`` is the paired code
    tree anchors are checked against; it is ``None`` only for a standalone conversion, which checks
    no anchor for path existence (``conversion``).
    """

    candidate: KnowledgeTree
    parsed: ParsedTree
    bases: tuple[KnowledgeTree, ...] = ()
    code: CodeTree | None = None
    conversion: bool = False
    leaf_publication: bool = False
    """The commit publishes a leaf (its closeout, direct landing or recorded landing): every history
    file not closed in a base is its own and is checked whatever its ``closed`` flag (MIK-R09)."""
    frozen: tuple[KnowledgeTree, ...] = ()
    """The history files of the commit(s) the candidate sits on, when they are not comparison bases
    (a leaf's base is its parent line's tip, so that every record it made is judged new). A history
    file closed there was closed by an earlier closeout of the same leaf that was not integrated:
    it is frozen and historical exactly like one closed in a base, and the leaf's later rows go to
    its next attempt file (L37 ruling of 2026-10-01T17:17:07, B)."""

    @property
    def closed_before(self) -> tuple[KnowledgeTree, ...]:
        """Every tree whose closed history files are frozen: the bases and :attr:`frozen`."""

        return (*self.bases, *self.frozen)

    @property
    def is_sync_merge(self) -> bool:
        """This validation judges a sync merge: two or more bases, no leaf publication.

        Only at a sync merge does a ``merge_reports`` rule report (MIK-R53 rule 10); every
        commit and landing route refuses.
        """

        return len(self.bases) >= 2 and not self.leaf_publication

    @cached_property
    def migration(self) -> MigrationState:
        """The candidate's migration state, read from its census files (MIK-R53 rule 10)."""

        return migration_state(self.candidate)

    @cached_property
    def route_families(self) -> tuple[RoutedFamily, ...]:
        """Each family's route state, computed once for every route rule in this validation."""
        return family_route_states(self.parsed)

    @cached_property
    def record_ids(self) -> frozenset[str]:
        parsed = frozenset(record.record.id for record in self.parsed.records)
        return parsed | self.parsed.unparsed_record_ids

    @cached_property
    def base_anchors(self) -> BaseAnchors:
        return _base_anchors(self.bases)
