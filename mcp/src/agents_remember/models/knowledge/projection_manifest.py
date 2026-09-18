"""Managed external projection: the manifest, the destination profile, and the writer port.

``Doc13:269`` states the whole vault-safety contract in one paragraph, and ``KS-R20@v1`` §5 turns
each of its clauses into a checkable behaviour. The vocabulary lives here; the behaviour lives in
:mod:`agents_remember.memory.knowledge.managed_projection`, and the operation that renders and then
calls it is :mod:`agents_remember.application.knowledge_projection`.

Three properties are structural rather than documented, and each is the reason a field exists:

* **The manifest is the only authority on what the substrate owns.** :class:`ProjectionManifest`
  lists every output with the five things requirement 5.1 names -- the destination-relative path,
  the stable identity of the projected record, the source snapshot, the renderer/profile version,
  and digest material that detects whether the file on disk still matches what the substrate last
  wrote. A file the manifest does not list is not the substrate's to delete, ever, so
  :class:`ProjectionWriter` has no operation that takes a bare path.
* **The destination is configuration, never knowledge.** :class:`DestinationProfile` is
  machine-specific by construction and is not a record: it is never stored in the dataset, never
  returned as a knowledge fact, and changing it changes no identity and no digest (requirement 4.7).
* **Every artifact carries its three recorded values.** :class:`RenderedOutput` requires the stable
  identity, the source snapshot and the renderer/profile version as non-optional fields, so a
  renderer that could not resolve one of them cannot emit an artifact at all -- requirement 4.3 and
  the ``unresolved_projection_input`` failure state are the same rule seen from two sides.

**What the manifest's digest is not.** It is projection bookkeeping about a file on disk. It is not
a knowledge identity, it confers no identity on the record it projects, and nothing in the substrate
reads it as one (requirement 8.4). The only algorithm named here is ``sha256``, chosen because it is
the repository's shipped hashing idiom -- ``SHA256_PATTERN`` already governs every digest that
crosses the knowledge boundary.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    KnowledgeModel,
)

__all__ = [
    "DIGEST_ALGORITHM",
    "PROJECTION_FORMATS",
    "PROJECTION_MANIFEST_FORMAT",
    "PROJECTION_MANIFEST_NAME",
    "PROJECTION_RENDERER_PROFILE",
    "PROJECTION_RENDERER_VERSION",
    "STAGING_DIRECTORY_NAME",
    "Collision",
    "DestinationCollision",
    "DestinationProfile",
    "DiscrepancyKind",
    "ManagedOutput",
    "ProjectionManifest",
    "ProjectionOutcome",
    "ProjectionPlan",
    "ProjectionRefusal",
    "ProjectionRefusalCode",
    "ProjectionReport",
    "ProjectionWriter",
    "RenderedOutput",
    "RetainedOutput",
    "RetentionReason",
    "detect_destination_collisions",
    "manifest_relative_path",
    "require_confined_relative_path",
]

# The manifest's own file name, taken from ``Doc13``'s projection example (``Doc13:260``).
PROJECTION_MANIFEST_NAME = "projection-manifest.json"
PROJECTION_MANIFEST_FORMAT: Literal["projection-manifest/v1"] = "projection-manifest/v1"

# Markdown and JSON are sibling views from the same resolved records (``Doc13:265``). A JSON
# projection is NOT the portable export: that is ``KS-R06@v1``'s contract and a different thing.
PROJECTION_FORMATS: tuple[str, ...] = ("markdown", "json")

# The renderer/profile version, recorded in every artifact. It is a real recorded value and not a
# package version read at display time (requirement 4.3): two artifacts produced by different
# renderer/profile versions are distinguishable from the artifacts themselves, which is only true
# if the value is written into them.
PROJECTION_RENDERER_PROFILE = "knowledge-view"
PROJECTION_RENDERER_VERSION = "knowledge-view-renderer/1"

DIGEST_ALGORITHM: Literal["sha256"] = "sha256"

# Where a render is staged before it is published. It is a single directory inside the destination
# so that a publication is a rename within one filesystem, and it is never swept recursively.
STAGING_DIRECTORY_NAME = ".agents-remember-staging"

DiscrepancyKind = Literal["modified", "replaced", "deleted", "unreadable"]
RetentionReason = Literal[
    "edited-since-last-projection",
    "unreadable",
    "replaced",
    "not-owned-by-prior-manifest",
]


class ProjectionRefusalCode:
    """The closed set of vault-safety refusal codes, as a namespace of literals.

    These are deliberately *not* added to :data:`agents_remember.models.knowledge.result.KnowledgeRefusalCode`.
    That literal is an earlier leaf's closed wire vocabulary for dataset operations, and a projection
    refusal is not a dataset operation: it is a filesystem fact about a directory the substrate does
    not own. Widening the shipped vocabulary would change a contract this leaf is required to
    preserve, so the two closures stay separate and each says what it is.
    """

    DESTINATION_ESCAPE: Literal["destination_escape"] = "destination_escape"
    DESTINATION_COLLISION: Literal["destination_collision"] = "destination_collision"
    ESCAPING_LINK: Literal["escaping_link"] = "escaping_link"
    UNRESOLVED_PROJECTION_INPUT: Literal["unresolved_projection_input"] = (
        "unresolved_projection_input"
    )
    MANIFEST_UNREADABLE: Literal["manifest_unreadable"] = "manifest_unreadable"
    DESTINATION_UNAVAILABLE: Literal["destination_unavailable"] = "destination_unavailable"
    UNAUTHORIZED_OVERWRITE: Literal["unauthorized_overwrite"] = "unauthorized_overwrite"


RefusalCode = Literal[
    "destination_escape",
    "destination_collision",
    "escaping_link",
    "unresolved_projection_input",
    "manifest_unreadable",
    "destination_unavailable",
    "unauthorized_overwrite",
]


class ProjectionRefusal(KnowledgeModel):
    """One vault-safety refusal, naming the offending path and the resolved root it escaped.

    Both the path and the root are named because requirement 5.2 asks for exactly that: a caller
    that cannot see the root the substrate resolved cannot tell whether its own configuration or the
    path was wrong.
    """

    code: RefusalCode
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    offending_path: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    resolved_root: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    expected: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    observed: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    next_action: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class DestinationProfile(KnowledgeModel):
    """One installation's destination configuration: local, machine-specific, never semantic truth.

    ``profile_id`` names the profile the caller configured, and ``destination_root`` is the resolved
    absolute root every output is confined to. Neither is a knowledge field: this model is never
    stored in the dataset and is never returned as a recorded fact about an invariant or a family
    (requirement 4.7). ``formats`` is the set of sibling views this profile projects.
    """

    profile_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    destination_root: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    formats: tuple[Literal["markdown", "json"], ...] = ("markdown",)
    renderer_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_a_declared_format(self) -> DestinationProfile:
        if not self.formats:
            raise ValueError("a destination profile declares at least one projected format")
        if self.formats != tuple(sorted(set(self.formats), key=PROJECTION_FORMATS.index)):
            raise ValueError(
                "a destination profile declares each format once, in the declared sibling order"
            )
        return self


class RenderedOutput(KnowledgeModel):
    """One rendered artifact, carrying the three recorded values ``Doc13:267`` requires.

    There is no constructor for "rendered but missing its identity": ``stable_identity``,
    ``source_snapshot`` and ``renderer_version`` are all required and bounded, so the
    ``unresolved_projection_input`` failure state is enforced by the type rather than by a check a
    later edit could drop. ``text`` is the renderer's bytes and nothing here derives them.
    """

    destination_relative_path: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    stable_identity: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    record_kind: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    format: Literal["markdown", "json"]
    source_snapshot: str = Field(pattern=SHA256_PATTERN)
    renderer_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    text: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class ProjectionPlan(KnowledgeModel):
    """The whole of one projection: where, what, and the only route to an authorized overwrite.

    ``authorized_overwrites`` is the explicit per-path caller authorization requirement 5.8 names as
    the sole route to replacing an externally edited file. It is a list of destination-relative
    paths, so an authorization is scoped to one path and cannot become a mode that authorizes
    everything the plan happens to contain.
    """

    destination: DestinationProfile
    outputs: tuple[RenderedOutput, ...] = ()
    authorized_overwrites: tuple[str, ...] = ()


class ManagedOutput(KnowledgeModel):
    """One output the substrate owns, as the manifest records it.

    The digest and byte count exist to answer one question -- has the file on disk changed since the
    substrate wrote it -- and they answer no other. ``authorized_overwrite`` records that a caller
    explicitly authorized replacing an external edit, which requirement 5.8 requires to be recorded
    in the resulting manifest entry.
    """

    destination_relative_path: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    stable_identity: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    record_kind: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    format: Literal["markdown", "json"]
    source_snapshot: str = Field(pattern=SHA256_PATTERN)
    renderer_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    digest_algorithm: Literal["sha256"] = DIGEST_ALGORITHM
    digest: str = Field(pattern=SHA256_PATTERN)
    byte_count: int = Field(ge=0)
    authorized_overwrite: bool = False


class RetainedOutput(KnowledgeModel):
    """One prior-manifest output the new projection did not produce and did not delete.

    This is requirement 5.6's ``retained-with-reason`` state made explicit: the manifest's new
    generation records the file as no longer produced while it remains on disk. The packet calls
    that "an explicit, permitted state, not an inconsistency", which is why it has its own model
    rather than being an absence from ``outputs``.
    """

    destination_relative_path: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    reason: RetentionReason
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    # The prior generation's own entry for this path, kept whole rather than reduced to its digest.
    # A retained file must stay covered by the externally-edited and unchanged tests when a later
    # projection produces it again, and it must be restorable as a managed output if that projection
    # withholds the write -- otherwise a retained path would look unowned on the next run and would
    # be overwritten, which is the exact file loss requirement 5.8 exists to prevent.
    recorded: ManagedOutput | None = None


class ProjectionManifest(KnowledgeModel):
    """The destination's manifest: the only authority on what the substrate owns there."""

    manifest_format: Literal["projection-manifest/v1"] = PROJECTION_MANIFEST_FORMAT
    generation: int = Field(ge=1)
    renderer_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    outputs: tuple[ManagedOutput, ...] = ()
    retained: tuple[RetainedOutput, ...] = ()

    @model_validator(mode="after")
    def _require_one_entry_per_path(self) -> ProjectionManifest:
        """Refuse a manifest that claims two owners for one path, or two states for one file.

        A manifest with a duplicate path could not answer "does the substrate own this file", and a
        path present as both produced and retained would make the next generation's unchanged test
        depend on which entry a reader happened to consult.
        """

        produced = [output.destination_relative_path for output in self.outputs]
        if len(set(produced)) != len(produced):
            raise ValueError("a manifest records each produced path once")
        retained = [entry.destination_relative_path for entry in self.retained]
        if len(set(retained)) != len(retained):
            raise ValueError("a manifest records each retained path once")
        if set(produced) & set(retained):
            raise ValueError(
                "a path is either produced by this generation or retained from a prior one, never "
                "both"
            )
        return self

    def owned_paths(self) -> frozenset[str]:
        """Every destination-relative path the substrate owns, produced or retained."""

        return frozenset(
            [output.destination_relative_path for output in self.outputs]
            + [entry.destination_relative_path for entry in self.retained]
        )

    def output_for(self, relative_path: str) -> ManagedOutput | None:
        """The manifest entry that owns one path, or ``None`` when the substrate owns nothing."""

        for output in self.outputs:
            if output.destination_relative_path == relative_path:
                return output
        return None

    def recorded_digest_for(self, relative_path: str) -> str | None:
        """The digest the substrate last recorded for one path, produced or retained.

        A retained file keeps its recorded digest, so it is still covered by the unchanged and
        externally-edited tests. A path the manifest does not list at all answers ``None``, which is
        what makes it not the substrate's to write over.
        """

        produced = self.output_for(relative_path)
        if produced is not None:
            return produced.digest
        entry = self.retained_for(relative_path)
        return None if entry is None or entry.recorded is None else entry.recorded.digest

    def recorded_identity_for(self, relative_path: str) -> str | None:
        """The stable identity the substrate last recorded for one path, produced or retained."""

        produced = self.output_for(relative_path)
        if produced is not None:
            return produced.stable_identity
        entry = self.retained_for(relative_path)
        return None if entry is None or entry.recorded is None else entry.recorded.stable_identity

    def retained_for(self, relative_path: str) -> RetainedOutput | None:
        """The retention entry that covers one path, or ``None`` when the path is not retained."""

        for entry in self.retained:
            if entry.destination_relative_path == relative_path:
                return entry
        return None


class ProjectionOutcome(KnowledgeModel):
    """What happened to one output, named per path so a caller never infers it from a total."""

    destination_relative_path: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    subject: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    state: Literal[
        "published", "unchanged", "removed", "retained-with-reason", "reported", "refused"
    ]
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class ProjectionReport(KnowledgeModel):
    """The typed outcome of one projection: per-path outcomes, discrepancies and the new manifest.

    ``discrepancies`` is requirement 5.8's report, and its ``kind`` distinguishes modified, replaced,
    deleted and unreadable because they call for different caller action. ``state == "refused"``
    means nothing was staged and the manifest is unchanged.
    """

    state: Literal["projected", "refused"]
    destination_root: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    renderer_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    manifest_generation: int | None = Field(default=None, ge=0)
    outcomes: tuple[ProjectionOutcome, ...] = ()
    retained: tuple[RetainedOutput, ...] = ()
    discrepancies: tuple[Discrepancy, ...] = ()
    manifest: ProjectionManifest | None = None
    refusal: ProjectionRefusal | None = None

    @model_validator(mode="after")
    def _require_one_outcome(self) -> ProjectionReport:
        if self.state == "refused":
            if self.refusal is None:
                raise ValueError("a refused projection carries its refusal")
            if self.manifest is not None:
                raise ValueError(
                    "a refused projection writes nothing, so it carries no resulting manifest"
                )
        elif self.refusal is not None:
            raise ValueError("a projected report carries no refusal")
        return self


class Discrepancy(KnowledgeModel):
    """One file on disk that differs from what the prior manifest recorded.

    ``recorded_digest`` and ``observed_digest`` are both optional because two of the four kinds
    cannot produce both: a deleted file has no observed bytes and an unreadable one has no observed
    digest. The kind is what the caller acts on; the digests are what makes the report checkable.
    """

    destination_relative_path: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    kind: DiscrepancyKind
    recorded_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    observed_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


@runtime_checkable
class ProjectionWriter(Protocol):
    """``Doc13`` §6's fifth port: the only write path an artifact takes to a destination.

    Requirement 4.10 is a scope statement about the whole leaf: "The projection write path goes
    through it; no view module writes files, and no MCP tool implementation writes files outside the
    writer's contract." A protocol rather than a base class, for the same reason
    :class:`~agents_remember.models.knowledge.view.KnowledgeViewReader` is one -- a producer that
    needed a subclass would be free to add a write path beside it.
    """

    def write(self, plan: ProjectionPlan) -> ProjectionReport:
        """Stage every output and publish it, or refuse and leave the destination as it was."""
        ...


class Collision(KnowledgeModel):
    """Two outputs whose destinations are equivalent on a case- or normalization-insensitive volume.

    ``canonical_form`` is the casefolded, NFC-normalized destination the two paths share. It is
    reported so the caller can see *why* the two collided without the substrate having chosen one.
    """

    destination_relative_paths: tuple[str, ...]
    canonical_form: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    stable_identities: tuple[str, ...] = ()


class DestinationCollision(KnowledgeModel):
    """The collision refusal: both paths and both source records, and neither is written."""

    collision: Collision
    refusal: ProjectionRefusal

    @model_validator(mode="after")
    def _require_the_collision_code(self) -> DestinationCollision:
        if self.refusal.code != ProjectionRefusalCode.DESTINATION_COLLISION:
            raise ValueError("a destination collision is refused as destination_collision")
        return self


def manifest_relative_path() -> str:
    """The destination-relative path of the manifest itself."""

    return PROJECTION_MANIFEST_NAME


def canonical_destination(relative_path: str) -> str:
    """The case-folded, NFC-normalized form two destinations are compared by.

    ``Doc13:269`` names "case/name collisions" and requirement 5.3 extends it to Unicode
    normalization, because a filesystem that would have accepted both names is exactly the case that
    makes the check necessary. The comparison is over the *destination*, never over the record's own
    identity or its symbol name.
    """

    normalized = unicodedata.normalize("NFC", relative_path)
    return normalized.casefold()


def detect_destination_collisions(
    outputs: Sequence[RenderedOutput],
) -> tuple[Collision, ...]:
    """Every pair of outputs whose destinations are equivalent, reported before either is written.

    Collisions are detected over the *whole* plan rather than as each output is staged, so a plan
    that would land two records on one file writes neither of them. Inventions are forbidden here:
    the substrate does not silently pick one, does not overwrite one with the other, and does not
    resolve the collision by appending a suffix the manifest did not record.
    """

    groups: dict[str, list[RenderedOutput]] = {}
    for output in outputs:
        groups.setdefault(canonical_destination(output.destination_relative_path), []).append(
            output
        )
    collisions: list[Collision] = []
    for canonical, members in sorted(groups.items()):
        distinct = {member.destination_relative_path for member in members}
        if len(members) > 1 and len(distinct) > 1:
            collisions.append(
                Collision(
                    destination_relative_paths=tuple(
                        sorted({member.destination_relative_path for member in members})
                    ),
                    canonical_form=canonical,
                    stable_identities=tuple(member.stable_identity for member in members),
                )
            )
        elif len(members) > 1:
            # Two records projecting to one *identical* path is the same failure with no case
            # folding involved, and it is reported through the same channel rather than as an
            # overwrite of one record's artifact by another's.
            collisions.append(
                Collision(
                    destination_relative_paths=(canonical,),
                    canonical_form=canonical,
                    stable_identities=tuple(member.stable_identity for member in members),
                )
            )
    return tuple(collisions)


def require_confined_relative_path(relative_path: str) -> ProjectionRefusal | None:
    """Refuse a destination-relative path that is not purely relative and inside its root.

    This is the *syntactic* half of confinement: an absolute path, a drive-qualified path, or any
    ``..`` segment is refused here. The *resolving* half -- the one requirement 5.2 asks for, since
    "a real path resolution rather than string prefix comparison" is what catches a symlinked
    directory or a case-folded alias -- is
    :func:`agents_remember.memory.knowledge.managed_projection.resolve_inside_destination`, because
    it needs the filesystem and this module is vocabulary.
    """

    candidate = relative_path.strip()
    if not candidate or candidate != relative_path:
        return _escape_refusal(relative_path, "the path is empty or padded with whitespace")
    if candidate.startswith("/") or candidate.startswith("\\"):
        return _escape_refusal(relative_path, "the path is absolute, not destination-relative")
    segments = candidate.replace("\\", "/").split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        return _escape_refusal(
            relative_path, "the path contains an empty, current or parent segment"
        )
    if len(segments) > 1 and segments[0] == STAGING_DIRECTORY_NAME:
        return _escape_refusal(
            relative_path, "the path names the staging directory, which is not a published output"
        )
    return None


def _escape_refusal(offending_path: str, reason: str) -> ProjectionRefusal:
    return ProjectionRefusal(
        code=ProjectionRefusalCode.DESTINATION_ESCAPE,
        detail=f"the requested output path is not confined to the destination root: {reason}",
        offending_path=offending_path or "<empty path>",
        next_action="name a destination-relative path with no parent or absolute segment",
    )
