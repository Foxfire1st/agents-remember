"""The curator's reachable ingest: one hand-off entry becomes one admitted batch, or nothing.

This is the first production caller of the knowledge write plane. Until this module existed, every
importer of :mod:`agents_remember.application.knowledge` was a test and the mounted change tool
refused every record kind, so the closed write path had no reachable entry point at all. The seam
below is the entry point this module provided — one of the two routes the write plane is reachable
from today, the other being the taskless repository-foundation route: a curator entry -- the
requirement-shaped item the orchestrator hands over -- is turned into the commands one batch carries
and committed through
:func:`~agents_remember.application.knowledge.change_knowledge_candidate`.

**Where the citation lives, and why nothing is sealed.** An invariant is a citation beside code, so
the entry carries its resolved targets. Each one is recorded the way the model already records a
source: an :class:`~agents_remember.models.knowledge.source.SourceAnchorDraft` -- path, source
identity and locator -- written by its own ``AddSourceAnchor`` command, and cited by a
``RealizationClaimDraft`` on the revision, which is the authored edge
(``invariant_revision_id -> anchor_id``) the read path already walks. No digest, seal, schema
generation or payload version is touched: the citation is a second record the revision cites rather
than a field inside the revision's preimage, so an existing revision's digest bytes do not move.

**What this module does not decide.** Resolution and validation happen before a call arrives here: a
curator entry carries targets that were already resolved to a confined repository-relative path with
one source identity and one locator, and the write path would accept a target that names nothing --
it stores what was authored. Whether a recorded target actually holds its recorded bytes is a fact
``memory.knowledge.read_anchors`` observes, not a condition this module can establish, and the
locator's extent is a rendering of the recorded construct rather than the record's identity.

**Provenance is not a parameter.** The entry carries no author, authorization or instant. The
revision draft is built here with the admitted destination's own envelope, exactly as
:func:`~agents_remember.application.knowledge.admitted_revision_request` states the split, and the
batch operation re-stamps it, so nothing a caller authors can become the stored provenance.

**The family plane is delegated, not re-implemented.** An entry may carry the curator's resolved
family authoring -- the family identities and joint-guarantee revisions it declares, the exact
memberships it places, and the stored memberships it retires. Those commands come from
:func:`~agents_remember.application.curator_family_authoring.family_commands`, which is the one
implementation of that mapping, and they join the same command list so one entry's family plane and
its citations commit or refuse together under the batch's one lock.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agents_remember.application.curator_family_planning import (
    CuratorFamilyAuthoring,
    family_commands,
)
from agents_remember.application.knowledge import (
    change_knowledge_candidate,
    resolve_candidate_context,
)
from agents_remember.models.knowledge.candidate import (
    AddInvariant,
    AddInvariantRevision,
    AddRealizationClaim,
    AddSourceAnchor,
    AnchorReference,
    CandidateResolution,
    ChangeBatch,
    MutationResult,
    ProposedCommand,
)
from agents_remember.models.knowledge.context import AdmittedKnowledgeDestination
from agents_remember.models.knowledge.graph import RealizationClaimDraft, RealizationRole
from agents_remember.models.knowledge.result import RevisionDraft
from agents_remember.models.knowledge.source import SourceAnchorDraft

# The two commands that create a family identity and its guarantee revision. They are the only
# commands a *membership* can cite an endpoint of without the citing entry having written it, which is
# why they are the ones hoisted to the front of the batch.
_FAMILY_IDENTITY_KINDS = frozenset({"add_family", "add_family_revision"})

__all__ = [
    "CuratorCitation",
    "CuratorEntry",
    "commit_curator_entries",
    "commit_curator_entry",
    "curator_batch",
    "curator_command_list",
    "curator_entry_commands",
]


@dataclass(frozen=True)
class CuratorCitation:
    """One resolved target, and the claim that cites it on the revision.

    The anchor is the shipped draft, unmodified: a path, the source identity it was resolved at and
    the locator. The claim identity, its role and its rationale are authored alongside it, because a
    stored anchor with nothing citing it attributes nothing to the statement.

    ``declares_anchor`` says whether this citation is writing that anchor or citing one the dataset
    already holds. A producer that means to reuse a stored anchor names its identity outright, and
    re-declaring a row that is already there is refused outright by the batch -- so the reuse half has
    to be expressible, exactly as an entry revising an invariant does not re-declare it.
    """

    anchor: SourceAnchorDraft
    claim_id: str
    role: RealizationRole
    rationale: str
    declares_anchor: bool = True


@dataclass(frozen=True)
class CuratorEntry:
    """One requirement-shaped entry of the orchestrator's hand-off list.

    The invariant identity is declared once and used for both the identity row and the revision, so a
    batch cannot file a revision under an invariant its own command did not record. ``citations`` may
    be empty: an entry whose targets are not yet resolved is still an authored obligation, and the
    write path records it with no realization rather than inventing one.
    """

    invariant_id: str
    display_label: str
    revision_id: str
    display_version: str
    statement: str
    applicability: str
    conditions: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    predecessors: tuple[str, ...] = ()
    citations: tuple[CuratorCitation, ...] = ()
    declares_invariant: bool = True
    family: CuratorFamilyAuthoring | None = None
    # Whether this entry's invariant revision and citations are ALREADY stored, so the only thing it
    # can still owe the dataset is its family plane. A replayed entry that emitted its invariant
    # commands again would be refused by the batch's own insert-absence precondition, and skipping it
    # entirely is what let a new family decision travel on a replayed entry and never be written.
    replayed: bool = False


def curator_entry_commands(
    destination: AdmittedKnowledgeDestination, entry: CuratorEntry
) -> tuple[ProposedCommand, ...]:
    """Every command one curator entry contributes, in the order the batch applies them.

    The order is load-bearing and is the batch's own contract: an anchor is written before the claim
    that cites it, so the claim's anchor endpoint resolves against a row this same batch declared.

    ``AddInvariant`` is emitted only when the entry is actually *declaring* the invariant. An entry
    that names predecessor revisions is authoring a successor of an invariant the repository already
    holds, and re-declaring that invariant is not a harmless no-op -- it is refused outright with
    ``batch_stale_precondition``, because the batch's precondition for creating an invariant is that
    the invariant is ABSENT. Emitting both commands unconditionally is what made the ingest unable to
    evolve an obligation it already had: the second run over a changed statement was refused, so the
    repository could accumulate new invariants but never revise one. A successor therefore carries
    its revision and its predecessor edges, and the invariant row it revises is left as it stands.

    ``AddSourceAnchor`` is emitted on the same rule and for the same reason: a citation whose
    ``declares_anchor`` is false names an anchor the dataset already holds, so the batch writes the
    claim that cites it and leaves the anchor row as it stands. Re-declaring it would be refused with
    the same ``batch_stale_precondition``, which is what makes "reuse a stored anchor" expressible at
    all rather than a second spelling of "insert a duplicate of it".

    The family plane is appended last and comes from its own module: the declaration commands an entry
    authored, the memberships it places (skipping the ones the candidate already records), and the
    retirements it names. One implementation of that mapping lives in
    :mod:`~agents_remember.application.curator_family_authoring`; this function only asks it for the
    commands and keeps them in the one batch.

    A **replayed** entry contributes its family plane alone. Its invariant revision and its citations
    are already in the dataset -- that is what "replayed" means -- so re-issuing them would be refused
    with ``batch_stale_precondition``, while dropping the entry wholesale is what let an entry whose
    revision already existed carry a *new* family decision that was never written and was still
    reported as authored. The family module's own guards decide what is new there: a stored revision,
    a stored membership and a stored identity each contribute nothing.
    """

    commands: list[ProposedCommand] = []
    if not entry.replayed:
        if entry.declares_invariant:
            commands.append(
                AddInvariant(invariant_id=entry.invariant_id, display_label=entry.display_label)
            )
        commands.append(AddInvariantRevision(revision=_revision_draft(destination, entry)))
        commands.extend(_citation_commands(entry))
    if entry.family is not None:
        commands.extend(family_commands(destination, entry.family))
    return tuple(commands)


def _citation_commands(entry: CuratorEntry) -> tuple[ProposedCommand, ...]:
    """Every anchor and claim one entry's resolved citations contribute, in the order they apply.

    An anchor is written before the claim that cites it, so the claim's anchor endpoint resolves
    against a row this same batch declared; a citation that is *reusing* a stored anchor
    (``declares_anchor`` false) contributes only its claim.
    """

    commands: list[ProposedCommand] = []
    for citation in entry.citations:
        if citation.declares_anchor:
            commands.append(AddSourceAnchor(anchor=citation.anchor))
        commands.append(
            AddRealizationClaim(
                claim=RealizationClaimDraft(
                    claim_id=citation.claim_id,
                    invariant_revision_id=entry.revision_id,
                    role=citation.role,
                    rationale=citation.rationale,
                ),
                anchor=AnchorReference(anchor_id=str(citation.anchor.anchor_id)),
            )
        )
    return tuple(commands)


def curator_batch(
    destination: AdmittedKnowledgeDestination,
    resolution: CandidateResolution,
    entries: Sequence[CuratorEntry],
) -> ChangeBatch:
    """One batch for a whole hand-off list, resolved against the candidate as it stands.

    The context is resolved here rather than accepted, so the batch is compared against the identity
    the candidate actually holds. The whole list travels in the one batch because the operation is
    all-or-nothing under one lock: a batch per entry pays the commit boundary once per entry, and a
    list that fails halfway leaves a partially recorded ingest that nothing can reconcile.

    The command order is load-bearing across entries, not only inside one. A membership write checks
    its family-revision endpoint against the rows that exist at that instant, so a membership that
    cites a family another entry in the same list declares must be applied after that declaration --
    and the entries arrive in the producer's order, which says nothing about that. The two identity
    commands are therefore hoisted to the front of the batch, in their own relative order (a family
    before the revision that belongs to it); every other command keeps the order its entry states,
    and the invariant revision a membership cites still precedes it because one entry contributes
    both.
    """

    return ChangeBatch(
        expected=resolve_candidate_context(destination, resolution),
        commands=curator_command_list(destination, entries),
    )


def curator_command_list(
    destination: AdmittedKnowledgeDestination, entries: Sequence[CuratorEntry]
) -> tuple[ProposedCommand, ...]:
    """Every command a whole hand-off list becomes, in the order the batch applies them.

    This is the one composition, and it is separable from the batch because the operation asks it a
    question the batch cannot answer: *is there anything left to write?* A list whose every entry
    replays still owes whatever family plane its entries carry, so the question is a fact about these
    commands and not about the replay set.

    The order is load-bearing across entries, not only inside one. A membership write checks its
    family-revision endpoint against the rows that exist at that instant, so a membership citing a
    family another entry in the same list declares must be applied after that declaration -- and the
    entries arrive in the producer's order, which says nothing about that. The two identity commands
    are therefore hoisted to the front, in their own relative order (a family before the revision that
    belongs to it); every other command keeps the order its entry states, and the invariant revision a
    membership cites still precedes it because one entry contributes both.
    """

    commands = tuple(
        command for entry in entries for command in curator_entry_commands(destination, entry)
    )
    return tuple(command for command in commands if command.kind in _FAMILY_IDENTITY_KINDS) + tuple(
        command for command in commands if command.kind not in _FAMILY_IDENTITY_KINDS
    )


def commit_curator_entry(
    destination: AdmittedKnowledgeDestination,
    resolution: CandidateResolution,
    entry: CuratorEntry,
) -> MutationResult:
    """Commit one curator entry as one admitted batch, in one operation."""

    return commit_curator_entries(destination, resolution, (entry,))


def commit_curator_entries(
    destination: AdmittedKnowledgeDestination,
    resolution: CandidateResolution,
    entries: Sequence[CuratorEntry],
) -> MutationResult:
    """Commit the whole hand-off list as one admitted batch through the closed write path.

    The receipt is the operation's own: it reports the rows written and the logical identity before
    and after, and a refusal carries the typed refusal with the dataset left exactly as it was.
    """

    return change_knowledge_candidate(destination, curator_batch(destination, resolution, entries))


def _revision_draft(
    destination: AdmittedKnowledgeDestination, entry: CuratorEntry
) -> RevisionDraft:
    """One entry's authored revision, sealed under the admitted provenance by the write path."""

    return RevisionDraft(
        revision_id=entry.revision_id,
        invariant_id=entry.invariant_id,
        display_version=entry.display_version,
        statement=entry.statement,
        applicability=entry.applicability,
        conditions=entry.conditions,
        exclusions=entry.exclusions,
        predecessors=entry.predecessors,
        provenance=destination.authorship,
    )
