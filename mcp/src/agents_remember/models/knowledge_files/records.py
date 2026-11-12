"""Global knowledge records: one JSON file per truth under ``knowledge/<kind-dir>/`` (MIK-R21 rule 4).

Each record declares ``schema: ar-<kind>/v1`` and carries its stable ``id`` (:mod:`.ids`), its
``origin``, a ``revision`` (an integer that increments only when meaning changes) and a ``status``.
Invariant, family and facet records use ``proposed`` | ``accepted`` | ``retired``; a decision uses
``active`` | ``under_reconsideration`` and leaves use by being superseded. Only invariant, family
and decision records carry ``admission``.

**Ownership (Doc14 §2).** A relationship is written exactly once, on its owner's side:

* an invariant record lists no realizations, tests, families or decisions -- ``extra="forbid"``
  refuses a field that would give a relationship a second owner;
* a family record owns ``members`` (invariant IDs) and ``routes`` (repository directories, ``.``
  for the repository root route);
* decision, incident and facet records own their outgoing ``links``, whose relations are the
  closed per-kind vocabulary :data:`RELATIONS_BY_KIND`.

Facet records other than decisions and incidents carry the payload fields the retired database's
facet models held, under the same names, plus ``links``. Content rules
(two alternatives with one chosen, when ``reconsider_when`` is required, what an admission criterion
means) belong to MIK-R13 and MIK-R27, not here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Annotated, ClassVar, Final, Literal, get_args

from pydantic import Field, model_validator

from agents_remember.models.knowledge_files.ids import RECORD_PREFIXES, RecordKind
from agents_remember.models.knowledge_files.shapes import (
    Anchor,
    DecisionAdmission,
    DecisionId,
    FamilyAdmission,
    FamilyId,
    FileModel,
    GitBlobId,
    IncidentId,
    InvariantAdmission,
    InvariantId,
    Label,
    LegacyUnassessed,
    Link,
    OneSentence,
    Origin,
    RecordId,
    Relation,
    RepositoryPath,
    Text,
    require_unique,
)
from agents_remember.models.knowledge_files.sidecars import RoutePath

RecordStatus = Literal["proposed", "accepted", "retired"]
DecisionStatus = Literal["active", "under_reconsideration"]
AlternativeStatus = Literal["chosen", "rejected", "deferred"]
IncidentApplicability = Literal["unresolved", "resolved", "historical_only"]

RELATIONS_BY_KIND: Final[Mapping[RecordKind, frozenset[Relation]]] = {
    "invariant": frozenset(),
    "family": frozenset(),
    "decision": frozenset({"explains", "constrains", "motivated_change_to", "reconsider_on"}),
    "incident": frozenset({"violated", "exposed_gap_in", "occurred_at", "led_to"}),
    "failure_mode": frozenset({"threatens", "occurs_at"}),
    "assumption": frozenset({"conditions", "underlies"}),
    "limitation": frozenset({"bounds"}),
    "scenario": frozenset({"exercises"}),
    "diagnostic": frozenset({"diagnoses"}),
    "term": frozenset({"defines_term_in"}),
    "weakness": frozenset({"burdens"}),
    "dependent": frozenset(),
}


def schema_name(kind: RecordKind) -> str:
    """Return a record kind's schema name: ``ar-<kind>/v1`` with ``-`` for ``_``."""

    return f"ar-{kind.replace('_', '-')}/v1"


class _Record(FileModel):
    """Fields and checks every record shares."""

    record_kind: ClassVar[RecordKind]

    id: RecordId
    origin: Origin

    @model_validator(mode="after")
    def _require_own_prefix_and_relations(self) -> _Record:
        prefix = RECORD_PREFIXES[self.record_kind]
        if not self.id.startswith(f"{prefix}-"):
            raise ValueError(f"a {self.record_kind} record's id must start with {prefix}-")
        allowed = RELATIONS_BY_KIND[self.record_kind]
        for link in getattr(self, "links", ()):
            if link.relation not in allowed:
                raise ValueError(
                    f"relation {link.relation!r} is not allowed on a {self.record_kind} record; "
                    f"allowed: {sorted(allowed)}"
                )
        return self


class _LinkedRecord(_Record):
    links: tuple[Link, ...]


class _FacetRecord(_LinkedRecord):
    """Incident and the other facet records: revisable and retirable, never deleted.

    ``revision`` increments only when meaning changes (a crossing sync resolves a conflicting record
    to one more than the higher side, MIK-R24 rule 8), and ``status: retired`` is how a record
    leaves use (MIK-R22 rule 3). Facets carry no ``admission``: the admission rule covers
    invariants, families and decisions only.
    """

    revision: int = Field(ge=1)
    status: RecordStatus


class InvariantRecord(_Record):
    """``ar-invariant/v1``: one normative guarantee. Its locations, tests and families live elsewhere."""

    record_kind: ClassVar[RecordKind] = "invariant"

    schema_: Literal["ar-invariant/v1"] = Field(default="ar-invariant/v1", alias="schema")
    id: InvariantId
    revision: int = Field(ge=1)
    status: RecordStatus
    statement: Text
    applicability: Text
    conditions: tuple[Text, ...]
    exclusions: tuple[Text, ...]
    supersedes: tuple[InvariantId, ...]
    admission: InvariantAdmission | LegacyUnassessed

    @model_validator(mode="after")
    def _require_distinct_supersedes(self) -> InvariantRecord:
        require_unique(self.supersedes, what="supersedes")
        if self.id in self.supersedes:
            raise ValueError("a record must not supersede itself")
        return self


class FamilyRecord(_Record):
    """``ar-family/v1``: a joint guarantee, its member invariants and the routes it governs."""

    record_kind: ClassVar[RecordKind] = "family"

    schema_: Literal["ar-family/v1"] = Field(default="ar-family/v1", alias="schema")
    id: FamilyId
    revision: int = Field(ge=1)
    status: RecordStatus
    title: Label
    guarantee: Text
    members: tuple[InvariantId, ...]
    routes: tuple[RoutePath, ...]
    admission: FamilyAdmission | LegacyUnassessed

    @model_validator(mode="after")
    def _require_distinct_members_and_routes(self) -> FamilyRecord:
        require_unique(self.members, what="members")
        require_unique(self.routes, what="routes")
        return self


class Alternative(FileModel):
    """One decision alternative: ``{ option, status, reason, reconsider_when? }``."""

    option: Text
    status: AlternativeStatus
    reason: Text
    reconsider_when: Text | None = None


class DecisionRecord(_LinkedRecord):
    """``ar-decision/v1``. ``superseded`` is never stored: it is derived from later ``supersedes``."""

    record_kind: ClassVar[RecordKind] = "decision"

    schema_: Literal["ar-decision/v1"] = Field(default="ar-decision/v1", alias="schema")
    id: DecisionId
    revision: int = Field(ge=1)
    status: DecisionStatus
    context: Text
    alternatives: tuple[Alternative, ...]
    consequences: tuple[Text, ...]
    decider: Label
    supersedes: tuple[DecisionId, ...]
    admission: DecisionAdmission | LegacyUnassessed

    @model_validator(mode="after")
    def _require_distinct_supersedes(self) -> DecisionRecord:
        require_unique(self.supersedes, what="supersedes")
        if self.id in self.supersedes:
            raise ValueError("a record must not supersede itself")
        return self


class IncidentRecord(_FacetRecord):
    """``ar-incident/v1``: what happened, how it was found, its cause and its recovery."""

    record_kind: ClassVar[RecordKind] = "incident"

    schema_: Literal["ar-incident/v1"] = Field(default="ar-incident/v1", alias="schema")
    id: IncidentId
    occurrence: Text
    observed_at: Label
    observed_effect: Text
    detection: Text
    cause: Text
    cause_uncertainty: Text
    applicability: IncidentApplicability
    recovery: Text | None = None
    corrective_actions: tuple[Text, ...] | None = None

    @model_validator(mode="after")
    def _require_recovery_once_not_unresolved(self) -> IncidentRecord:
        if self.applicability != "unresolved" and (
            self.recovery is None or self.corrective_actions is None
        ):
            raise ValueError(
                "recovery and corrective_actions are required unless applicability is unresolved"
            )
        return self


class AssumptionRecord(_FacetRecord):
    """``ar-assumption/v1``: a facet record with its own payload fields and ``links``."""

    record_kind: ClassVar[RecordKind] = "assumption"

    schema_: Literal["ar-assumption/v1"] = Field(default="ar-assumption/v1", alias="schema")
    proposition: Text
    basis: Text


class LimitationRecord(_FacetRecord):
    """``ar-limitation/v1``: a facet record with its own payload fields and ``links``."""

    record_kind: ClassVar[RecordKind] = "limitation"

    schema_: Literal["ar-limitation/v1"] = Field(default="ar-limitation/v1", alias="schema")
    limited: Text
    boundary: Text
    unsupported: tuple[Text, ...] = Field(min_length=1)


class FailureModeRecord(_FacetRecord):
    """``ar-failure-mode/v1``: a facet record with its own payload fields and ``links``."""

    record_kind: ClassVar[RecordKind] = "failure_mode"

    schema_: Literal["ar-failure-mode/v1"] = Field(default="ar-failure-mode/v1", alias="schema")
    failure: Text
    condition: Text
    observable_effect: Text


class ScenarioRecord(_FacetRecord):
    """``ar-scenario/v1``: a facet record with its own payload fields and ``links``."""

    record_kind: ClassVar[RecordKind] = "scenario"

    schema_: Literal["ar-scenario/v1"] = Field(default="ar-scenario/v1", alias="schema")
    situation: Text
    preconditions: tuple[Text, ...] = Field(min_length=1)
    outcome: Text


class DiagnosticRecord(_FacetRecord):
    """``ar-diagnostic/v1``: a facet record with its own payload fields and ``links``."""

    record_kind: ClassVar[RecordKind] = "diagnostic"

    schema_: Literal["ar-diagnostic/v1"] = Field(default="ar-diagnostic/v1", alias="schema")
    condition: Text
    signal: Text
    interpretation: Text
    interpretation_limit: Text


class TermRecord(_FacetRecord):
    """``ar-term/v1``: a facet record with its own payload fields and ``links``."""

    record_kind: ClassVar[RecordKind] = "term"

    schema_: Literal["ar-term/v1"] = Field(default="ar-term/v1", alias="schema")
    term: Label
    definition: Text
    scope: Text


# --------------------------------------------------------------------------------------------------
# MIK-R118: a weakness of the code, recorded where it occurs
# --------------------------------------------------------------------------------------------------

# The closed list of weakness kinds (MIK-R118 rule 1): the four wastes, a fault, and a debt. One
# definition, the model literal; the runtime tuple is derived from it, so a reader, a writer and a
# validator cannot drift from each other by editing one of two lists.
WeaknessKind = Literal["context", "compute", "storage", "time", "fault", "debt"]
WEAKNESS_KINDS: Final[tuple[str, ...]] = get_args(WeaknessKind)
WeaknessState = Literal["open", "closed"]


class Measurement(FileModel):
    """``{ number, unit, how, commit, date }``: a weakness' measurement (MIK-R118 rule 1)."""

    number: float
    unit: Label
    how: Text
    commit: GitBlobId
    date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]


class SeenBy(FileModel):
    """``{ task, role }``: who saw the weakness (MIK-R118 rule 1)."""

    task: Label
    role: Label


class Closure(FileModel):
    """``{ task, commit, reason? }``: the change that removed a weakness' cause (rule 7).

    ``reason`` is the one sentence a task writes when it finds a weakness does not stand.
    """

    task: Label
    commit: GitBlobId
    reason: Text | None = None


class WeaknessRecord(_Record):
    """``ar-weakness/v1``: a weakness of the code, anchored where it occurs (MIK-R118).

    The ``kind`` is one of :data:`WEAKNESS_KINDS`. ``anchor`` names the file and, where it has one,
    the symbol: the same anchor a realization entry carries, recorded at the commit that saw it.
    ``measurement`` is present only where there is one. ``seen`` is who saw it. ``state`` is
    ``open`` or ``closed``; a closed record names the ``task`` and ``commit`` that removed its
    cause. ``links`` may carry ``burdens`` towards an invariant or a family. There is no priority
    field and no proposal field: a weakness that belongs to no file is not recorded here.
    """

    record_kind: ClassVar[RecordKind] = "weakness"

    schema_: Literal["ar-weakness/v1"] = Field(default="ar-weakness/v1", alias="schema")
    id: RecordId
    revision: int = Field(ge=1)
    kind: WeaknessKind
    statement: OneSentence
    anchor: Anchor
    seen: SeenBy
    state: WeaknessState
    measurement: Measurement | None = None
    closed: Closure | None = None
    links: tuple[Link, ...] = ()

    @model_validator(mode="after")
    def _require_place_and_close(self) -> WeaknessRecord:
        if self.anchor.path is None:
            raise ValueError("a weakness anchors to a file: 'anchor.path' is required")
        if (self.state == "closed") != (self.closed is not None):
            raise ValueError(
                "a closed weakness names its 'closed' task and commit, and an open one carries none"
            )
        return self


# --------------------------------------------------------------------------------------------------
# MIK-R124: a document that describes code, and what it stands on
# --------------------------------------------------------------------------------------------------


class DependentAnchorLink(FileModel):
    """One link to a place in a file: the anchor a realization entry carries (MIK-R124 rule 2)."""

    kind: Literal["anchor"] = "anchor"
    anchor: Anchor
    heading: Label | None = None

    @model_validator(mode="after")
    def _require_path(self) -> DependentAnchorLink:
        if self.anchor.path is None:
            raise ValueError("an anchor link names its file: 'anchor.path' is required")
        return self


class DependentFileLink(FileModel):
    """One link to a whole file: the recorded value is the file's blob (MIK-R124 rule 2)."""

    kind: Literal["file"] = "file"
    path: RepositoryPath
    blob: GitBlobId
    heading: Label | None = None


class DependentRecordLink(FileModel):
    """One link to an invariant or a family: the recorded value is its ``revision``."""

    kind: Literal["record"] = "record"
    id: RecordId
    revision: int = Field(ge=1)
    heading: Label | None = None


DependentLink = Annotated[
    DependentAnchorLink | DependentFileLink | DependentRecordLink,
    Field(discriminator="kind"),
]


class Confirmation(FileModel):
    """``{ task, role, commit, codeTree?, uncommitted?, statement? }``: who last confirmed a
    document, at what code state.

    ``statement`` is the one sentence a Curator writes when it confirms a document it did not
    change (MIK-R124 rule 5). ``codeTree`` is the tree the recorded link values were read from,
    and ``uncommitted`` says that this tree is not the named commit's own tree -- the links then
    describe the candidate's worktree, and the commit names only the line it sits on. Nothing
    here is typed: the writer fills all four (MIK-R124 rule 1).
    """

    task: Label
    role: Label
    commit: GitBlobId
    codeTree: GitBlobId | None = None
    uncommitted: bool = False
    statement: Text | None = None


def _dependent_link_key(link: DependentLink) -> str:
    """One link's identity for the distinctness check: its kind and what it stands on."""

    document = link.to_document()
    document.pop("heading", None)
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


class DependentRecord(_Record):
    """``ar-dependent/v1``: one document, and every place it stands on (MIK-R124 rule 1).

    ``document`` is a tracked file of the code repository; at most one record names it. Each
    ``stands_on`` link records what stood at the anchor at the last confirmation. ``confirmed``
    names the task, role and code commit of that confirmation. The record holds no text of the
    document and no summary of it. Whether the document is stale is derived, never stored.
    """

    record_kind: ClassVar[RecordKind] = "dependent"

    schema_: Literal["ar-dependent/v1"] = Field(default="ar-dependent/v1", alias="schema")
    id: RecordId
    revision: int = Field(ge=1)
    document: RepositoryPath
    stands_on: tuple[DependentLink, ...] = Field(min_length=1)
    confirmed: Confirmation

    @model_validator(mode="after")
    def _require_distinct_links(self) -> DependentRecord:
        keys = [_dependent_link_key(one) for one in self.stands_on]
        if len(set(keys)) != len(keys):
            raise ValueError("a dependent's 'stands_on' must not repeat a link")
        return self


KnowledgeRecord = (
    InvariantRecord
    | FamilyRecord
    | DecisionRecord
    | IncidentRecord
    | AssumptionRecord
    | LimitationRecord
    | FailureModeRecord
    | ScenarioRecord
    | DiagnosticRecord
    | TermRecord
    | WeaknessRecord
    | DependentRecord
)


def record_status(record: KnowledgeRecord) -> str:
    """The record's lifecycle word, for the rows that carry one (MIK-R118, MIK-R124).

    The older kinds store ``status``; a weakness stores ``state`` (``open`` or ``closed``), and a
    dependent stores neither, because whether its document is stale is derived from what the document
    stands on and never written into the record (MIK-R124 rule 3). The empty word is that fact, not a
    default status: a caller that needs currency must derive it.
    """

    if isinstance(record, WeaknessRecord):
        return record.state
    return getattr(record, "status", "")


RECORD_MODELS: Final[Mapping[RecordKind, type[KnowledgeRecord]]] = {
    "invariant": InvariantRecord,
    "family": FamilyRecord,
    "decision": DecisionRecord,
    "incident": IncidentRecord,
    "assumption": AssumptionRecord,
    "limitation": LimitationRecord,
    "failure_mode": FailureModeRecord,
    "scenario": ScenarioRecord,
    "diagnostic": DiagnosticRecord,
    "term": TermRecord,
    "weakness": WeaknessRecord,
    "dependent": DependentRecord,
}
