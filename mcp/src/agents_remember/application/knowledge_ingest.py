"""The curator's reachable ingest: one hand-off entry becomes one admitted batch, or nothing.

This is the first production caller of the knowledge write plane. Until this module existed, every
importer of :mod:`agents_remember.application.knowledge` was a test and the mounted change tool
refused every record kind, so the closed write path had no reachable entry point at all. The seam
below is that entry point: a curator entry -- the requirement-shaped item the orchestrator hands over
-- is turned into the commands one batch carries and committed through
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
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

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

__all__ = [
    "CuratorCitation",
    "CuratorEntry",
    "commit_curator_entries",
    "commit_curator_entry",
    "curator_batch",
    "curator_entry_commands",
]


@dataclass(frozen=True)
class CuratorCitation:
    """One resolved target, and the claim that cites it on the revision.

    The anchor is the shipped draft, unmodified: a path, the source identity it was resolved at and
    the locator. The claim identity, its role and its rationale are authored alongside it, because a
    stored anchor with nothing citing it attributes nothing to the statement.
    """

    anchor: SourceAnchorDraft
    claim_id: str
    role: RealizationRole
    rationale: str


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
    """

    commands: list[ProposedCommand] = []
    if entry.declares_invariant:
        commands.append(
            AddInvariant(invariant_id=entry.invariant_id, display_label=entry.display_label)
        )
    commands.append(AddInvariantRevision(revision=_revision_draft(destination, entry)))
    for citation in entry.citations:
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
    """

    return ChangeBatch(
        expected=resolve_candidate_context(destination, resolution),
        commands=tuple(
            command for entry in entries for command in curator_entry_commands(destination, entry)
        ),
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
