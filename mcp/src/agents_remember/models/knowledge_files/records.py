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

Facet records other than decisions and incidents carry the fields of today's facet payload models
(:mod:`agents_remember.models.knowledge.facet`) under the same names, plus ``links``. Content rules
(two alternatives with one chosen, when ``reconsider_when`` is required, what an admission criterion
means) belong to MIK-R13 and MIK-R27, not here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Final, Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge_files.ids import RECORD_PREFIXES, RecordKind
from agents_remember.models.knowledge_files.shapes import (
    DecisionAdmission,
    DecisionId,
    FamilyAdmission,
    FamilyId,
    FileModel,
    IncidentId,
    InvariantAdmission,
    InvariantId,
    Label,
    LegacyUnassessed,
    Link,
    Origin,
    RecordId,
    Relation,
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
    """``ar-assumption/v1``: fields of ``AssumptionPayload``."""

    record_kind: ClassVar[RecordKind] = "assumption"

    schema_: Literal["ar-assumption/v1"] = Field(default="ar-assumption/v1", alias="schema")
    proposition: Text
    basis: Text


class LimitationRecord(_FacetRecord):
    """``ar-limitation/v1``: fields of ``LimitationPayload``."""

    record_kind: ClassVar[RecordKind] = "limitation"

    schema_: Literal["ar-limitation/v1"] = Field(default="ar-limitation/v1", alias="schema")
    limited: Text
    boundary: Text
    unsupported: tuple[Text, ...] = Field(min_length=1)


class FailureModeRecord(_FacetRecord):
    """``ar-failure-mode/v1``: fields of ``FailureModePayload``."""

    record_kind: ClassVar[RecordKind] = "failure_mode"

    schema_: Literal["ar-failure-mode/v1"] = Field(default="ar-failure-mode/v1", alias="schema")
    failure: Text
    condition: Text
    observable_effect: Text


class ScenarioRecord(_FacetRecord):
    """``ar-scenario/v1``: fields of ``ScenarioPayload``."""

    record_kind: ClassVar[RecordKind] = "scenario"

    schema_: Literal["ar-scenario/v1"] = Field(default="ar-scenario/v1", alias="schema")
    situation: Text
    preconditions: tuple[Text, ...] = Field(min_length=1)
    outcome: Text


class DiagnosticRecord(_FacetRecord):
    """``ar-diagnostic/v1``: fields of ``DiagnosticGuidancePayload``."""

    record_kind: ClassVar[RecordKind] = "diagnostic"

    schema_: Literal["ar-diagnostic/v1"] = Field(default="ar-diagnostic/v1", alias="schema")
    condition: Text
    signal: Text
    interpretation: Text
    interpretation_limit: Text


class TermRecord(_FacetRecord):
    """``ar-term/v1``: fields of ``TerminologyPayload``."""

    record_kind: ClassVar[RecordKind] = "term"

    schema_: Literal["ar-term/v1"] = Field(default="ar-term/v1", alias="schema")
    term: Label
    definition: Text
    scope: Text


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
)

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
}
