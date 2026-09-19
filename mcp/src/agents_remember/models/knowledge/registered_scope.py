"""The registered review scope ``Doc13:282-291`` describes: its declaration, its construction inputs.

``KS-R16@v1`` §1 owns the *construction* of the §8 registered review scope, and this module is that
scope's vocabulary. It defines no detection schema, no assessment schema and no composition schema --
those belong to ``KS-R14@v1``, ``KS-R15@v1`` and ``KS-R17@v1`` -- and it redefines nothing the shipped
selective read owns. Five properties are structural here rather than documented:

* **Every followed edge keeps the snapshot it was read from.** A ``source -> invariant`` or
  ``invariant -> family`` mapping is looked up across the declared snapshots, and each result records
  the side whose selection produced it (``mapping_side``). A cross-snapshot union is a *historical
  lookup for candidate discovery*: removing a link in the combined candidate does not erase the
  earlier link from the scan, and nothing here claims the two sides' relationships hold at once
  ``Doc13:299``.
* **Widening is a declared policy or it is nothing.** A composition edge is followed only when the
  scope was constructed under a policy version that admits it, and the resolved
  ``(policy_id, policy_version_id, declared_version)`` triple travels with the scope it produced
  (``Doc13:287``). A request that declares no policy follows no composition edge: absence never
  widens.
* **Membership is recorded, never inferred.** Nothing in this module derives a member from prose, a
  display label, a folder name, a path prefix, a symbol or a route's name (``Doc13:78-79``;
  ``Doc13:89``). A path prefix compared against a stored anchor path is a resolution fact and is not
  representable here at all.
* **The registered review scope is not the R07 read frontier.** No field of this module can hold a
  selected revision set, a page cursor, a count of items remaining or an advertised expansion; the
  read path's frontier is not an input to construction and construction's membership is not an input
  to the read (``read.py``'s advertised frontier; ``notes/PLANNED-EXTENSION-LEAVES.md:199``). The two
  axes are kept apart by the shape of the records rather than by a rule a caller has to remember.
* **Construction is total or it is a refusal.** A constructed scope resolved every input it declared:
  there is no partial scope carrying an unresolved-input list, because "what the run over that scope
  could not resolve" is ``KS-R14@v1``'s half of the split and restating it here is what §1.5 forbids
  each leaf from doing. A scope that cannot resolve a declared input is
  :class:`ScopeConstructionRefusal`, naming that exact input.

**No identity is minted here.** The scope is addressed by its declared ``scope_id`` and carries no
content address, logical digest or fingerprint of its own: ``KS-R16@v1``'s ``## Forbidden Overreach``
refuses a new identity authority, so a ``scope_manifest_ref`` elsewhere points at this record's
``scope_id`` and at nothing derived from it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.result import KnowledgeRefusal

__all__ = [
    "SCOPE_CONSTRUCTION_VERSION",
    "SCOPE_EDGE_KINDS",
    "SCOPE_SIDES",
    "FollowedScopeEdge",
    "RegisteredScopeManifest",
    "RegisteredScopeMembership",
    "RegisteredScopeRequest",
    "ScopeConstructionRefusal",
    "ScopeEdgeKind",
    "ScopeMissingInputKind",
    "ScopeSnapshotDeclaration",
    "ScopeSnapshotSide",
]

# The construction version, in the shipped policy-constant idiom (``DETECTION_POLICY_VERSION`` at
# ``models/knowledge/detection.py``, ``KNOWLEDGE_READ_POLICY_VERSION`` at ``models/knowledge/read.py``).
# It names *how* a scope is built, so a scope built by another construction rule is a different fact
# even over the same declaration. It is not the traversal policy: that identity is an authored row in
# ``KS-R17@v1``'s registry and is resolved from the store rather than declared here.
SCOPE_CONSTRUCTION_VERSION = "registered-review-scope/v1"

# The two declared sides. They are exactly ``KS-R14@v1``'s ``before``/``after`` selection pair under
# the names the scope declaration uses; a scope is built over one declared pair, which is what makes
# "an ambiguous common base" a state the declaration vocabulary can refuse rather than resolve.
ScopeSnapshotSide = Literal["base", "candidate"]

SCOPE_SIDES: tuple[ScopeSnapshotSide, ...] = ("base", "candidate")

# The recorded relationship kinds one construction may follow, in the order ``Doc13:282-291`` states
# them. The first two are recorded repository links; the third is an *authored* edge owned by
# ``KS-R17@v1`` and is followed only under a declared policy version.
ScopeEdgeKind = Literal["source_to_invariant", "invariant_to_family", "composition"]

SCOPE_EDGE_KINDS: tuple[ScopeEdgeKind, ...] = (
    "source_to_invariant",
    "invariant_to_family",
    "composition",
)

# Which declared input a refusal names. ``KS-R16@v1``'s Failure And Recovery Behavior requires the
# refusal to name the exact missing input, so the kind is a closed vocabulary a caller branches on and
# the identity travels beside it.
ScopeMissingInputKind = Literal[
    "declared_snapshot",
    "declared_changed_path",
    "seed_family_revision",
    "traversal_policy",
]


class ScopeSnapshotDeclaration(KnowledgeModel):
    """One side of the declared pair, as an exact identity rather than a description.

    ``snapshot`` is the whole admission the side has -- the repository namespace, the schema version
    the file declares and the logical digest of its content -- so the same declaration over the same
    snapshots under the same construction version is the same scope. ``selector_policy_version``
    records the selection policy the declaration was read under, because which records a side
    *contributes* is a property of that policy and a scope that did not record it could not be
    reproduced.
    """

    side: ScopeSnapshotSide
    snapshot: SnapshotIdentity
    selector_policy_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)


class FollowedScopeEdge(KnowledgeModel):
    """One recorded edge a construction followed, with the snapshot provenance it was read under.

    ``edge_id`` is the recorded row's own identity -- the anchor, the membership or the composition
    edge -- while ``from_record_id`` and ``to_record_id`` are the two endpoints it joined.
    ``mapping_side`` is the side whose selection produced this mapping, and it is required for every
    kind: an edge with no recorded side would be a claim about the union that no single snapshot
    supports. ``policy_identity`` is carried on a composition edge and nowhere else -- the two
    recorded repository link kinds are not traversals and have no policy to name.
    """

    edge_kind: ScopeEdgeKind
    edge_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    mapping_side: ScopeSnapshotSide
    from_record_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    to_record_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    policy_identity: tuple[str, str, str] | None = None

    @model_validator(mode="after")
    def _require_the_policy_half_to_match_the_kind(self) -> FollowedScopeEdge:
        """Refuse a recorded link claiming a traversal policy, or a composition edge without one.

        The two recorded link kinds are looked up, not followed: a policy identity on one of them
        would report a traversal that never happened. A *composition* edge with no policy identity is
        the widened-without-a-recorded-policy state ``KS-R16@v1`` §1.2 refuses, and it must not be
        expressible as a followed edge.
        """

        if self.edge_kind == "composition" and self.policy_identity is None:
            raise ValueError(
                "a followed composition edge records the declared policy version it was followed "
                "under; a composition edge with no policy identity is widening without a recorded "
                "policy, which makes the scope unreproducible"
            )
        if self.edge_kind != "composition" and self.policy_identity is not None:
            raise ValueError(
                f"a {self.edge_kind!r} link is a recorded lookup rather than a traversal, so it "
                "carries no policy identity: reporting one would claim a policy executed where none "
                "was consulted"
            )
        return self


class RegisteredScopeMembership(KnowledgeModel):
    """The membership one construction resolved, as recorded identities and nothing else.

    Every tuple is the recorded result of a declared lookup, in declared order, and no entry here was
    derived from a name, a prefix or a folder. ``recorded_reference_refs`` are the explicit sources the
    reached records were authored from, as those records themselves declare them
    (``models/knowledge/authorship.py``'s ``origin_refs``); the construction *collects* them and
    decides nothing about them.

    That is the whole of "the source and evidence references linked through those records" at this
    base, and the reason is measured rather than assumed: the substrate's dedicated evidence-claim
    record group does not exist yet -- ``memory/knowledge/record_envelope.py:16`` records that
    ``EvidenceClaim`` and its siblings "are later leaves" -- so a construction collects the reference
    channel the reached records actually carry instead of inventing an evidence link or leaving a field
    that no record could ever populate.
    """

    invariant_revision_ids: tuple[str, ...] = ()
    family_revision_ids: tuple[str, ...] = ()
    realization_claim_ids: tuple[str, ...] = ()
    source_anchor_ids: tuple[str, ...] = ()
    recorded_reference_refs: tuple[str, ...] = ()

    def size(self) -> int:
        """Return the number of recorded members, which is what a caller reports as the scope's size."""

        return (
            len(self.invariant_revision_ids)
            + len(self.family_revision_ids)
            + len(self.realization_claim_ids)
            + len(self.source_anchor_ids)
            + len(self.recorded_reference_refs)
        )


class RegisteredScopeRequest(KnowledgeModel):
    """One declaration to construct the registered review scope over.

    The declaration is the whole of §1.1's input list: the paired snapshots, the changed paths whose
    registered links are looked up, the seed family revisions a declared traversal may start from, and
    the policy version that authorises following composition edges. ``policy_id`` and
    ``policy_version_id`` are absent together for a scope that follows no composition edge at all --
    §1.2's default -- and a construction with no policy reports exactly that rather than resolving one
    for the caller.
    """

    scope_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    repository_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    snapshots: tuple[ScopeSnapshotDeclaration, ...] = Field(min_length=1)
    changed_paths: tuple[str, ...] = ()
    seed_family_revision_ids: tuple[str, ...] = ()
    policy_id: str | None = Field(default=None, min_length=1, max_length=LABEL_MAX_LENGTH)
    policy_version_id: str | None = Field(default=None, min_length=1, max_length=LABEL_MAX_LENGTH)
    construction_version: str = Field(
        default=SCOPE_CONSTRUCTION_VERSION, min_length=1, max_length=LABEL_MAX_LENGTH
    )

    @field_validator("scope_id", "repository_id")
    @classmethod
    def _require_nonblank_identity(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("a scope declaration's identity must not be blank")
        return cleaned

    @field_validator("changed_paths", "seed_family_revision_ids")
    @classmethod
    def _require_unique_nonblank_entries(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(item.strip() for item in value)
        if any(not item for item in cleaned):
            raise ValueError("a declared path or seed identity must not be blank")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError(
                "each declared path and seed identity is declared once: a repeated entry is not a "
                "second input and would make the construction's record of its own inputs wrong"
            )
        return cleaned

    @model_validator(mode="after")
    def _require_one_declared_pair(self) -> RegisteredScopeRequest:
        """Refuse a declaration whose base is ambiguous or whose sides repeat.

        §1.7 makes an ambiguous common base an explicit policy or a refusal and never an arbitrary
        base chosen to let the run proceed, so the declaration admits exactly one snapshot per side
        and no second snapshot for a side that is already declared. A base the caller cannot name
        uniquely is a refusal here, where the caller can still fix the declaration.
        """

        declared = tuple(item.side for item in self.snapshots)
        if len(set(declared)) != len(declared):
            raise ValueError(
                "a scope declaration names exactly one snapshot per side: two declarations for one "
                "side are an ambiguous common base, and choosing one of them to let the run proceed "
                "is the arbitrary base the requirement refuses"
            )
        unknown = tuple(side for side in declared if side not in SCOPE_SIDES)
        if unknown:  # pragma: no cover - the Literal refuses an unknown side first
            raise ValueError(
                f"the declared snapshot side {unknown[0]!r} is not one of {SCOPE_SIDES}"
            )
        return self

    @model_validator(mode="after")
    def _require_a_declared_policy_to_name_both_halves(self) -> RegisteredScopeRequest:
        """Refuse a declaration carrying half a policy identity, exactly as an authored edge is refused."""

        if (self.policy_id is None) != (self.policy_version_id is None):
            raise ValueError(
                "a declared traversal policy is an identity *and* a version: one without the other is "
                "not a declared policy, and a construction that carried half of one would report a "
                "policy identity no registry can resolve"
            )
        if self.seed_family_revision_ids and self.policy_id is None:
            raise ValueError(
                "a seed family revision is a traversal's starting point, and a construction with no "
                "declared policy follows no composition edge; naming a seed without the policy that "
                "authorises the walk is a widening the requirement refuses"
            )
        return self

    def declared_side(self, side: ScopeSnapshotSide) -> ScopeSnapshotDeclaration | None:
        """Return the declaration for one side, or ``None`` when that side was not declared."""

        for item in self.snapshots:
            if item.side == side:
                return item
        return None


class RegisteredScopeManifest(KnowledgeModel):
    """The constructed scope: the declared snapshots, the followed edges, the policy, the membership.

    This is the whole of §1.5's sentence "this leaf produces the scope", and it holds nothing else:
    no unresolved-input list (that is the refusal, or ``KS-R14@v1``'s run limitation), no frontier, no
    selection counts, and no conclusion about any member. ``policy_identity`` is the *resolved* triple
    the construction executed under, so a scope built under one policy version is never readable as
    one built under another.
    """

    scope_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    repository_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    construction_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    snapshots: tuple[ScopeSnapshotDeclaration, ...] = Field(min_length=1)
    changed_paths: tuple[str, ...] = ()
    policy_identity: tuple[str, str, str] | None = None
    followed_edges: tuple[FollowedScopeEdge, ...] = ()
    membership: RegisteredScopeMembership

    @model_validator(mode="after")
    def _require_every_edge_to_name_a_declared_side(self) -> RegisteredScopeManifest:
        """Refuse an edge whose provenance names a side the scope did not declare."""

        declared = {item.side for item in self.snapshots}
        for edge in self.followed_edges:
            if edge.mapping_side not in declared:
                raise ValueError(
                    f"the followed edge {edge.from_record_id!r} -> {edge.to_record_id!r} records "
                    f"provenance {edge.mapping_side!r}, which this scope did not declare; an edge "
                    "attributed to a snapshot the run never read is not a construction input"
                )
        return self

    @model_validator(mode="after")
    def _require_one_policy_for_every_followed_composition_edge(self) -> RegisteredScopeManifest:
        """Refuse a composition edge whose policy is not the scope's own resolved policy identity."""

        for edge in self.followed_edges:
            if edge.edge_kind == "composition" and edge.policy_identity != self.policy_identity:
                raise ValueError(
                    "every followed composition edge records the policy identity this scope was "
                    "constructed under; an edge naming another version would make the scope a mix of "
                    "two traversals reported as one"
                )
        return self

    def followed_composition_ids(self) -> tuple[str, ...]:
        """Return the composition edges this scope followed, as their own recorded identities."""

        return tuple(
            edge.edge_id for edge in self.followed_edges if edge.edge_kind == "composition"
        )


class ScopeConstructionRefusal(KnowledgeModel):
    """Why a scope could not be constructed, naming the exact missing input.

    §1.8 and the packet's Failure And Recovery Behavior require the refusal to name the exact missing
    input and to fall back to nothing: not to the read frontier, not to a path-derived membership, and
    not to a partial scope reported silently. ``missing_input`` is that exact input's recorded
    identity, so the refusal is diagnosable rather than merely negative.
    """

    missing_input_kind: ScopeMissingInputKind
    missing_input: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    refusal: KnowledgeRefusal

    @model_validator(mode="after")
    def _require_the_refusal_to_name_the_same_input(self) -> ScopeConstructionRefusal:
        """Refuse a wrapper whose typed refusal names a different input than its own field."""

        observed = self.refusal.observed or ""
        if self.missing_input not in observed and self.missing_input != (
            self.refusal.record_id or ""
        ):
            raise ValueError(
                f"the refusal names {self.missing_input!r} as the missing input, and its typed "
                f"refusal records {observed!r}/{self.refusal.record_id!r}; a refusal that names two "
                "different inputs names neither"
            )
        return self

    def next_action(self) -> str:
        """Return the shipped next action the typed refusal advertises."""

        return self.refusal.next_action


def scope_path_is_recorded(path: str) -> str:
    """Require one declared changed path to be a plain repository-relative path.

    A declared path is looked up by *exact* equality against a stored anchor's path, so a spelling that
    could be normalised, globbed or resolved is a lookup that answers about a different location than
    the one declared. Refusing the spelling is what keeps the lookup an identity comparison.
    """

    cleaned = path.strip().replace("\\", "/")
    if not cleaned:
        raise ValueError("a declared changed path must not be blank")
    if cleaned.startswith("/") or cleaned.startswith(":") or ".." in cleaned.split("/"):
        raise ValueError(
            f"a declared changed path must be repository-relative and free of pathspec magic: "
            f"{path!r}. A path that can be normalised, globbed or resolved is not the path the lookup "
            "compares against a stored anchor"
        )
    if len(cleaned) > PATH_MAX_LENGTH:
        raise ValueError(f"a declared changed path must be at most {PATH_MAX_LENGTH} characters")
    return cleaned
