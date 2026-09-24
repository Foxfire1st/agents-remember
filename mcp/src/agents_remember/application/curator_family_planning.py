"""Resolving the curator's authored family plane into exact identities, plans and batch commands.

:mod:`…curator_family_authoring` reads what the curator declared. This module resolves it against the
candidate: it allocates the identity pair each declared guarantee holds (recorded in the candidate's
own journal so a retry resolves to the same family), reads what the dataset already records -- which
is how a reused guarantee is *examined* rather than restated -- and turns each entry's decision into
the shipped commands ``AddFamily``, ``AddFamilyRevision``, ``AddFamilyMember`` and
``RemoveFamilyMember``. Nothing here writes: the commands travel into the one batch
:mod:`…knowledge_ingest` builds, so the one writer and the one transaction stay the only
implementations.

Four properties are load-bearing:

* **Identity is allocated, never derived from the key.** Two independent tasks that spell one local
  key alike mint two families; a repeat of one operation finds its own allocation and is handed back
  what it already holds, while the same key arriving with a *changed* guarantee is refused rather
  than rewritten.
* **A named identity is checked, not trusted.** A declaration that reuses a ``family_id`` must find
  that identity in the candidate, and a membership that names a stored ``family_revision_id`` must
  find that revision, so an invented canonical ID cannot become a stored family.
* **A stored row contributes no command.** Re-declaring or re-inserting what the dataset already
  holds is refused by the batch's own insert-absence preconditions, which is the right answer to
  "write this again" and the wrong answer to "repeat the operation that already wrote it".
* **A retirement names the row this run read.** The expected-row digest comes from the dataset's own
  stored row through the shipped owner, so a removal cannot be authored against a row the run never
  read.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4, uuid5

from agents_remember.application.curator_family_authoring import (
    FAMILY_ABSENT_STATE,
    EntryFamilyRefusal,
    FamilyAssignment,
    FamilyGuarantee,
    FamilyMembership,
    FamilyPlaneRead,
    FamilyRetirement,
    family_refusal,
)
from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.canonical_json import canonical_json_bytes, decoded_json, sha256_digest
from agents_remember.memory.knowledge import records
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.models.knowledge.candidate import (
    AddFamily,
    AddFamilyMember,
    AddFamilyRevision,
    ProposedCommand,
    RemoveFamilyMember,
)
from agents_remember.models.knowledge.context import AdmittedKnowledgeDestination
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from agents_remember.models.knowledge.graph import FamilyMemberDraft

__all__ = [
    "CuratorFamilyAuthoring",
    "DeclarationPlan",
    "FamilyAllocation",
    "FamilyAllocations",
    "GuaranteePlan",
    "MembershipPlan",
    "RetirementPlan",
    "StoredFamilyFacts",
    "family_commands",
    "plan_declarations",
    "plan_entry_family",
    "projected_family_commands",
    "read_family_allocations",
    "read_stored_family_facts",
    "record_family_allocations",
]

# The candidate-local journal this module's allocated identities are recorded in. It is deliberately
# separate from the invariant journal ``knowledge_curator_ingest`` owns: one operation's allocation
# records are not another's, and a family key and an entry id are different namespaces.
_FAMILY_ALLOCATION_JOURNAL_NAME = "curator-family-allocation-journal.json"

# The namespace a membership identity is derived under. A membership is not an authored truth of its
# own -- it is the recorded edge between two exact revisions -- so its identity is derived from exactly
# those endpoints, and repeating one operation derives the same row rather than a second one.
_MEMBERSHIP_NAMESPACE = UUID("7c2c1b64-7d5a-4c0f-9a2f-6a1d1f4f6d21")


# --------------------------------------------------------------------------------------------
# Step 2: identity allocation, deliberate reuse, and the stored facts reuse is checked against
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FamilyAllocation:
    """The identity pair one family declaration holds, and the content it was minted for.

    Both identities are **allocated**: fresh ``uuid4`` values that carry no task, no enclosure and no
    key, so a stored family stays usable from any later task. What makes a repeat of one operation
    resolve to them is the retry key recorded beside them -- the enclosure's own scope joined with the
    family key -- and ``content_digest`` is the guard that refuses a different guarantee arriving under
    one key.
    """

    retry_key: str
    family_id: str
    family_revision_id: str
    content_digest: str

    def as_record(self) -> dict[str, str]:
        """The exact JSON object the journal stores for this allocation."""

        return {
            "retryKey": self.retry_key,
            "familyId": self.family_id,
            "familyRevisionId": self.family_revision_id,
            "contentDigest": self.content_digest,
        }


@dataclass(frozen=True)
class FamilyAllocations:
    """What one candidate's family journal holds, and whether it could be read at all.

    An absent journal is the ordinary first-run case and reads as no records. A journal that is there
    and cannot be read is not the same fact: every declaring entry is then refused with
    ``family_allocation_unreadable``, because minting without answering "does this operation already
    hold an identity?" is how a retry becomes a second family.
    """

    path: Path
    records: Mapping[str, FamilyAllocation]
    unreadable: str | None = None


def read_family_allocations(directory: Path) -> FamilyAllocations:
    """Read the family journal this candidate holds, and say plainly when it cannot be read."""

    path = Path(directory) / _FAMILY_ALLOCATION_JOURNAL_NAME
    if not path.is_file():
        return FamilyAllocations(path, {})
    try:
        loaded = decoded_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return FamilyAllocations(path, {}, f"the family journal is not readable JSON: {error}")
    if not isinstance(loaded, list):
        return FamilyAllocations(path, {}, "the family journal is not a list of allocations")
    records: dict[str, FamilyAllocation] = {}
    for one in loaded:
        record = _allocation_record(one)
        if record is None:
            return FamilyAllocations(
                path, {}, "the family journal carries a record this code cannot read"
            )
        records[record.retry_key] = record
    return FamilyAllocations(path, records)


def _allocation_record(raw: object) -> FamilyAllocation | None:
    """One journal entry as a family allocation, or ``None`` when it is not one this code wrote."""

    if not isinstance(raw, Mapping):
        return None
    names = ("retryKey", "familyId", "familyRevisionId", "contentDigest")
    values = [raw.get(name) for name in names]
    if not all(isinstance(value, str) and value for value in values):
        return None
    return FamilyAllocation(*(cast("str", value) for value in values))


def record_family_allocations(
    allocations: FamilyAllocations, plans: Sequence[GuaranteePlan]
) -> None:
    """Record the family identities this run allocated, **before** the batch that could still refuse.

    The order is the one the invariant journal keeps and for the same reason: a run whose batch refuses
    has still made this operation's allocation, and the retry of that operation has to resolve to it
    rather than mint a second family for one guarantee.
    """

    fresh = {
        one.retry_key: FamilyAllocation(
            retry_key=one.retry_key,
            family_id=one.family_id,
            family_revision_id=one.family_revision_id,
            content_digest=one.content_digest,
        )
        for one in plans
    }
    merged = {**allocations.records, **fresh}
    if merged.keys() == allocations.records.keys():
        return
    payload = canonical_json_bytes([merged[key].as_record() for key in sorted(merged)])
    atomic_write_bytes(allocations.path, payload)
    if allocations.path.read_bytes() != payload:
        raise ValueError(
            f"the family journal at {allocations.path} did not read back as it was written, so the "
            "identities this run allocated are not durably recorded and a retry could not find them"
        )


@dataclass(frozen=True)
class StoredFamilyFacts:
    """What one dataset already records about families, read once and reported as measured.

    Every mapping here is a fact read from the dataset rather than a claim about it. ``member_rows`` is
    keyed by membership identity and ``member_ids`` by the exact endpoint pair, because that pair is
    what the dataset's own unique tuple means; ``guarantees`` carries each stored revision's authored
    text, which is how a reused guarantee is *examined* -- the run reports the recorded text it read
    rather than restating what it expected.
    """

    guarantees: Mapping[str, str]
    versions: Mapping[str, str]
    family_of: Mapping[str, str]
    families: frozenset[str]
    member_rows: Mapping[str, tuple[str, str]]
    member_digests: Mapping[str, str]
    member_ids: Mapping[tuple[str, str], str]
    member_count: Mapping[str, int]

    def recorded_version(self, family_revision_id: str) -> str:
        """The display version this dataset records for one family revision."""

        return self.versions.get(family_revision_id, "")

    def recorded_guarantee(self, family_revision_id: str) -> str | None:
        """The text this dataset records for one family revision, or ``None`` when it holds none."""

        return self.guarantees.get(family_revision_id)

    def members_of(self, family_revision_id: str) -> int:
        """How many memberships this dataset records for one family revision."""

        return self.member_count.get(family_revision_id, 0)

    def recorded_membership(
        self, family_revision_id: str, invariant_revision_id: str
    ) -> str | None:
        """The membership identity this dataset records for one exact endpoint pair, or ``None``."""

        return self.member_ids.get((family_revision_id, invariant_revision_id))

    def endpoints_of(self, member_id: str) -> tuple[str, str] | None:
        """One stored membership's two endpoints, as the dataset's own row records them."""

        return self.member_rows.get(member_id)


def read_stored_family_facts(database_path: Path) -> StoredFamilyFacts | None:
    """Read one dataset's recorded families, or ``None`` when there is no dataset at that path.

    ``None`` is the honest answer for a candidate that does not exist yet: an empty mapping would say
    "this dataset records no family", which is a different fact from "there is no dataset here".
    """

    if not Path(database_path).is_file():
        return None
    connection = open_read_only_database(database_path)
    try:
        guarantees: dict[str, str] = {}
        versions: dict[str, str] = {}
        family_of: dict[str, str] = {}
        families: set[str] = set()
        rows: dict[str, tuple[str, str]] = {}
        digests: dict[str, str] = {}
        ids: dict[tuple[str, str], str] = {}
        counts: dict[str, int] = {}
        for row in connection.execute(
            "SELECT family_id, revision_id, joint_guarantee, display_version FROM family_revision"
        ):
            family_of[str(row[1])] = str(row[0])
            guarantees[str(row[1])] = str(row[2])
            versions[str(row[1])] = str(row[3])
        for row in connection.execute("SELECT family_id FROM family"):
            families.add(str(row[0]))
        for row in connection.execute(
            "SELECT repository_id, member_id, family_revision_id, invariant_revision_id, provenance "
            "FROM family_member"
        ):
            member = records.decode_member_row(row)
            rows[member.member_id] = (member.family_revision_id, member.invariant_revision_id)
            ids[(member.family_revision_id, member.invariant_revision_id)] = member.member_id
            digests[member.member_id] = member.row_digest
            counts[member.family_revision_id] = counts.get(member.family_revision_id, 0) + 1
    finally:
        connection.close()
    return StoredFamilyFacts(
        guarantees=guarantees,
        versions=versions,
        family_of=family_of,
        families=frozenset(families),
        member_rows=rows,
        member_digests=digests,
        member_ids=ids,
        member_count=counts,
    )


@dataclass(frozen=True)
class GuaranteePlan:
    """One family guarantee this run authored or examined, resolved against the candidate."""

    key: str
    retry_key: str
    content_digest: str
    family_id: str
    family_revision_id: str
    display_label: str
    display_version: str
    joint_guarantee: str
    predecessor_revision_ids: tuple[str, ...]
    declares_identity: bool
    declares_revision: bool
    declared_by: str

    @property
    def examined(self) -> bool:
        """Whether the candidate already held this revision, so this run read rather than wrote it."""

        return not self.declares_revision


@dataclass(frozen=True)
class DeclarationPlan:
    """Every key this list declares, resolved once for the whole list, with the refusals beside them.

    ``refused_keys`` is what lets a membership that rests on a refused declaration be refused with a
    reason of its own instead of silently placing nothing.
    """

    plan_by_key: Mapping[str, GuaranteePlan]
    declarations: tuple[GuaranteePlan, ...]
    refusals: Mapping[str, EntryFamilyRefusal]
    refused_keys: frozenset[str]
    stored: StoredFamilyFacts | None


def plan_declarations(
    read: FamilyPlaneRead,
    *,
    retry_scope: str,
    allocations: FamilyAllocations,
    stored: StoredFamilyFacts | None,
) -> DeclarationPlan:
    """Resolve every declared key to the exact identity pair this run will cite, or refuse it.

    A declaration is resolved for the whole list before any entry is planned, because two entries in
    one list may name one family and only one of them authors its guarantee. The identity pair is
    allocated here -- never derived from the key -- and the content digest recorded beside it is what
    refuses a changed guarantee arriving under one key on a later run.
    """

    plan_by_key: dict[str, GuaranteePlan] = {}
    refusals: dict[str, EntryFamilyRefusal] = {}
    refused_keys: set[str] = set()
    for key in sorted(read.declarations):
        plan, refusal = _plan_declaration(read.declarations[key], retry_scope, allocations, stored)
        if refusal is not None:
            refusals[refusal.entry_id] = refusal
            refused_keys.add(key)
            continue
        assert plan is not None
        plan_by_key[key] = plan
    return DeclarationPlan(
        plan_by_key=plan_by_key,
        declarations=tuple(plan_by_key[key] for key in sorted(plan_by_key)),
        refusals=refusals,
        refused_keys=frozenset(refused_keys),
        stored=stored,
    )


def _plan_declaration(
    declaration: FamilyGuarantee,
    retry_scope: str,
    allocations: FamilyAllocations,
    stored: StoredFamilyFacts | None,
) -> tuple[GuaranteePlan | None, EntryFamilyRefusal | None]:
    """One declared key's identity pair: reused when this operation holds one, else minted."""

    key = declaration.key
    content = _declaration_digest(declaration)
    retry_key = f"{retry_scope}:family:{key}"
    held = allocations.records.get(retry_key)
    if held is None and allocations.unreadable is not None:
        return None, family_refusal(
            declaration.declared_by,
            "family_allocation_unreadable",
            f"{allocations.unreadable}, so this declaration cannot be told whether its operation "
            f"already holds an identity and no new one is minted over an answer that is not known "
            f"({allocations.path})",
        )
    if held is not None and held.content_digest != content:
        return None, family_refusal(
            declaration.declared_by,
            "family_allocation_conflict",
            f"the family key {key!r} was allocated "
            f"{held.family_id}/{held.family_revision_id} for a guarantee whose digest is "
            f"{held.content_digest}, and this declaration arrives under the same key with the "
            f"different digest {content}; one key names one creation operation, and a changed "
            "guarantee is a successor: declare it under a new key, naming the stored family_id "
            "and the revision it supersedes in predecessor_revision_ids",
        )
    family_id = held.family_id if held is not None else declaration.family_id or str(uuid4())
    family_revision_id = held.family_revision_id if held is not None else str(uuid4())
    refusal = _reusefamily_refusal(declaration, family_id, held is not None, stored)
    if refusal is not None:
        return None, refusal
    recorded = None if stored is None else stored.recorded_guarantee(family_revision_id)
    return (
        GuaranteePlan(
            key=key,
            retry_key=retry_key,
            content_digest=content,
            family_id=family_id,
            family_revision_id=family_revision_id,
            display_label=declaration.display_label,
            display_version=declaration.display_version,
            joint_guarantee=declaration.joint_guarantee,
            predecessor_revision_ids=declaration.predecessor_revision_ids,
            declares_identity=stored is None or family_id not in stored.families,
            declares_revision=recorded is None,
            declared_by=declaration.declared_by,
        ),
        None,
    )


def _reusefamily_refusal(
    declaration: FamilyGuarantee,
    family_id: str,
    held: bool,
    stored: StoredFamilyFacts | None,
) -> EntryFamilyRefusal | None:
    """Why a declaration that *reuses* an identity cannot be honoured, or ``None`` when it can.

    Naming an identity is a claim about what the repository already holds, so each named identity is
    checked against the candidate rather than trusted: an invented family identity, or a predecessor
    that no dataset holds or that belongs to another family, is refused by name instead of being
    written as if the claim were true.
    """

    if held or stored is None:
        return None
    if declaration.family_id is not None and family_id not in stored.families:
        return family_refusal(
            declaration.declared_by,
            "family_identity_not_stored",
            f"the declaration of {declaration.key!r} reuses the family identity {family_id}, and this "
            "candidate holds no such family: an authored identity names a record the repository "
            "already has",
        )
    for predecessor in declaration.predecessor_revision_ids:
        if stored.recorded_guarantee(predecessor) is None or (
            stored.family_of.get(predecessor) != family_id
        ):
            return family_refusal(
                declaration.declared_by,
                "family_predecessor_not_stored",
                f"the declaration of {declaration.key!r} names {predecessor} as a predecessor of "
                f"{family_id}, and this candidate holds no such revision of that family",
            )
    return None


def _declaration_digest(declaration: FamilyGuarantee) -> str:
    """The content one allocated pair stands for: the authored guarantee, never the local key."""

    return sha256_digest(
        {
            "familyId": declaration.family_id,
            "label": declaration.display_label,
            "version": declaration.display_version,
            "guarantee": declaration.joint_guarantee,
            "predecessors": list(declaration.predecessor_revision_ids),
        }
    )


@dataclass(frozen=True)
class MembershipPlan:
    """One membership this run will place, resolved to its two exact endpoints."""

    entry_id: str
    key: str
    family_revision_id: str
    invariant_revision_id: str
    member_id: str
    basis: str
    stored: bool


@dataclass(frozen=True)
class RetirementPlan:
    """One stored membership this run will retire, with the digest read from the dataset."""

    entry_id: str
    member_id: str
    family_revision_id: str
    invariant_revision_id: str
    expected_row_digest: str


@dataclass(frozen=True)
class CuratorFamilyAuthoring:
    """One entry's family plane, resolved and command-ready.

    ``no_family_basis`` is the deliberate outcome's own text, which travels into the revision's
    recorded conditions so a reader of the dataset -- not only a reader of the report -- can tell a
    family-free obligation from an unexamined one.
    """

    entry_id: str
    invariant_revision_id: str
    keys: tuple[str, ...] = ()
    declarations: tuple[GuaranteePlan, ...] = ()
    memberships: tuple[MembershipPlan, ...] = ()
    retirements: tuple[RetirementPlan, ...] = ()
    no_family_basis: str | None = None

    @property
    def examined(self) -> bool:
        """Whether the curator authored any family decision for this entry.

        A decision is a deliberate no-family outcome, a membership, or a retirement: all three are
        authored facts about where this obligation belongs, and an entry with none of them is the
        unexamined case the report names rather than an empty family result.
        """

        return self.no_family_basis is not None or bool(self.memberships) or bool(self.retirements)


def plan_entry_family(
    entry_id: str,
    assignment: FamilyAssignment | None,
    *,
    revision_id: str,
    declarations: DeclarationPlan,
    stored: StoredFamilyFacts | None,
) -> tuple[CuratorFamilyAuthoring | None, EntryFamilyRefusal | None]:
    """Resolve one entry's family decision against the declarations and the dataset.

    The entry's own revision is the invariant endpoint of every membership it authors, so this runs
    after the revision identity is settled and never invents one. A membership is a *stored* one when
    the exact endpoint pair is already recorded: an exact retry then reports the membership it already
    placed instead of re-issuing an insert the dataset's own unique tuple would refuse.
    """

    if assignment is None:
        # No family key at all is a *third* outcome and it is reported as its own: the entry is
        # carried with nothing authored, so the coverage names it as not examined rather than
        # dropping it out of a list that would then look complete.
        return (
            CuratorFamilyAuthoring(entry_id=entry_id, invariant_revision_id=revision_id),
            None,
        )
    if assignment.state == FAMILY_ABSENT_STATE:
        return (
            CuratorFamilyAuthoring(
                entry_id=entry_id,
                invariant_revision_id=revision_id,
                no_family_basis=assignment.basis,
            ),
            None,
        )
    memberships: list[MembershipPlan] = []
    keys: list[str] = []
    for membership in assignment.memberships:
        plan, refusal = _membership_endpoints(
            entry_id, membership, revision_id, declarations, stored
        )
        if refusal is not None:
            return None, refusal
        assert plan is not None
        memberships.append(plan)
        keys.append(membership.key)
    retirements, refusal = _retirement_plans(entry_id, assignment.retirements, stored)
    if refusal is not None:
        return None, refusal
    return (
        CuratorFamilyAuthoring(
            entry_id=entry_id,
            invariant_revision_id=revision_id,
            keys=tuple(keys),
            declarations=tuple(
                one for one in declarations.declarations if one.declared_by == entry_id
            ),
            memberships=tuple(memberships),
            retirements=retirements,
        ),
        None,
    )


def _membership_endpoints(
    entry_id: str,
    membership: FamilyMembership,
    revision_id: str,
    declarations: DeclarationPlan,
    stored: StoredFamilyFacts | None,
) -> tuple[MembershipPlan | None, EntryFamilyRefusal | None]:
    """One membership's exact family revision endpoint, or the refusal that names why it has none."""

    if membership.stored_family_revision_id is not None:
        family_revision_id = membership.stored_family_revision_id
        if stored is None or stored.recorded_guarantee(family_revision_id) is None:
            return None, family_refusal(
                entry_id,
                "family_revision_not_stored",
                f"the membership of {membership.key!r} names the stored family revision "
                f"{family_revision_id}, and this candidate holds no such revision",
            )
    else:
        plan = declarations.plan_by_key.get(membership.key)
        if plan is None:
            return None, _declarationfamily_refusal(entry_id, membership.key, declarations)
        family_revision_id = plan.family_revision_id
    already = stored is not None and (
        stored.recorded_membership(family_revision_id, revision_id) is not None
    )
    return (
        MembershipPlan(
            entry_id=entry_id,
            key=membership.key,
            family_revision_id=family_revision_id,
            invariant_revision_id=revision_id,
            member_id=str(uuid5(_MEMBERSHIP_NAMESPACE, f"{family_revision_id}:{revision_id}")),
            basis=membership.basis,
            stored=already,
        ),
        None,
    )


def _declarationfamily_refusal(
    entry_id: str, key: str, declarations: DeclarationPlan
) -> EntryFamilyRefusal:
    """Why a membership that joins a key has no family revision to cite."""

    if key in declarations.refused_keys:
        return family_refusal(
            entry_id,
            "family_declaration_refused",
            f"the declaration of {key!r} was refused, so the membership that rests on it is refused "
            "with it rather than placed in a family this run did not author",
        )
    return family_refusal(
        entry_id,
        "family_not_declared",
        f"the membership of {key!r} joins a family no entry in this list declares and names no "
        "stored revision",
    )


def _retirement_plans(
    entry_id: str,
    retirements: Sequence[FamilyRetirement],
    stored: StoredFamilyFacts | None,
) -> tuple[tuple[RetirementPlan, ...], EntryFamilyRefusal | None]:
    """Every retired membership resolved to the row this run just read, or the refusal that names it."""

    plans: list[RetirementPlan] = []
    for retirement in retirements:
        endpoints = None if stored is None else stored.endpoints_of(retirement.member_id)
        digest = None if stored is None else stored.member_digests.get(retirement.member_id)
        if endpoints is None or digest is None:
            return (), family_refusal(
                entry_id,
                "membership_not_stored",
                f"the retirement names the membership {retirement.member_id}, and this candidate "
                "holds no such membership: a removal names a row this run read, never one it assumed",
            )
        plans.append(
            RetirementPlan(
                entry_id=entry_id,
                member_id=retirement.member_id,
                family_revision_id=endpoints[0],
                invariant_revision_id=endpoints[1],
                expected_row_digest=digest,
            )
        )
    return tuple(plans), None


# --------------------------------------------------------------------------------------------
# Step 3: the commands, and the coverage the report carries
# --------------------------------------------------------------------------------------------


def family_commands(
    destination: AdmittedKnowledgeDestination, authoring: CuratorFamilyAuthoring
) -> tuple[ProposedCommand, ...]:
    """Every command one entry's family plane contributes, in the batch's own order.

    The order is the batch's contract and not an accident: an identity is written before the revision
    that belongs to it, and a membership after the revision it cites. A plan the candidate already
    holds contributes nothing for that row -- re-declaring a stored family, revision or membership is
    refused outright by the batch's own insert-absence preconditions, which is the right answer to
    "write this again" and the wrong answer to "repeat the operation that already wrote it".
    """

    commands: list[ProposedCommand] = []
    for declaration in authoring.declarations:
        if declaration.declares_identity:
            commands.append(
                AddFamily(family_id=declaration.family_id, display_label=declaration.display_label)
            )
        if declaration.declares_revision:
            commands.append(
                AddFamilyRevision(
                    revision=FamilyRevisionDraft(
                        family_id=declaration.family_id,
                        revision_id=declaration.family_revision_id,
                        display_version=declaration.display_version,
                        joint_guarantee=declaration.joint_guarantee,
                        predecessors=declaration.predecessor_revision_ids,
                        provenance=destination.authorship,
                    )
                )
            )
    for membership in authoring.memberships:
        if membership.stored:
            continue
        commands.append(
            AddFamilyMember(
                member=FamilyMemberDraft(
                    member_id=membership.member_id,
                    family_revision_id=membership.family_revision_id,
                    invariant_revision_id=membership.invariant_revision_id,
                    provenance=destination.authorship,
                )
            )
        )
    for retirement in authoring.retirements:
        commands.append(
            RemoveFamilyMember(
                member_id=retirement.member_id,
                expected_row_digest=retirement.expected_row_digest,
            )
        )
    return tuple(commands)


def projected_family_commands(authoring: CuratorFamilyAuthoring | None) -> int:
    """How many commands one entry's family plane would contribute, counted without a destination.

    A dry run reports the batch it would carry, and the family plane's commands are part of that
    batch. Counting them here -- from the same plans :func:`family_commands` writes from -- keeps the
    projection arithmetic over what the run already resolved instead of a second construction of the
    command list, which is the rule the rest of the dry report follows.
    """

    if authoring is None:
        return 0
    declarations = sum(
        (1 if one.declares_identity else 0) + (1 if one.declares_revision else 0)
        for one in authoring.declarations
    )
    return (
        declarations
        + sum(1 for one in authoring.memberships if not one.stored)
        + len(authoring.retirements)
    )
