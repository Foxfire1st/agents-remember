"""The shared shapes of the text knowledge format: anchors, references, links, admission, origin.

These are the building blocks every record (:mod:`.records`) and every onboarding sidecar
(:mod:`.sidecars`) is written in (MIK-R21 rules 3, 4, 6 and 7). They check **shape only**: field
presence, closed vocabularies and spellings. Whether an ID resolves, a marker is used, a symbol
exists or an admission criterion is justified belongs to the validator (MIK-R22) and the content
rules (MIK-R13, MIK-R27).

Two conventions hold for every model here, because each file must round-trip byte for byte through
the canonical formatter (:mod:`.canonical`):

* **Absent is absent.** An optional field is omitted from the file when it has no value; an
  explicit ``null`` is refused rather than read as absent, so a file has one spelling per value.
* **Text is refused, never cleaned.** A value that is blank, or an identifier with surrounding
  whitespace, is refused; nothing is stripped, so what is parsed is exactly what was written.

JSON keys follow the packet's spelling: ``snake_case`` for content fields and the camelCase
``handoffEntry`` and ``legacyId`` in ``origin``. Python attribute names that would shadow Pydantic
(``schema``) or differ from the JSON key carry an alias, and :meth:`FileModel.to_document` always
serializes by alias.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, ConfigDict, Field, model_validator

from agents_remember.models.knowledge.base import (
    GIT_OBJECT_PATTERN,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
    require_plain_git_path,
)
from agents_remember.models.knowledge.requirement import REQUIREMENT_PACKET_VERSION_PATTERN
from agents_remember.models.knowledge_files.ids import (
    RECORD_ID_PATTERN,
    RECORD_PREFIXES,
    id_pattern,
)

CONTENT_HASH_PATTERN: Final = r"^sha256:[0-9a-f]{64}$"
REFERENCE_NUMBER_PATTERN: Final = r"^[1-9][0-9]*$"
ROUTE_TARGET_PREFIX: Final = "route:"


class FileModel(KnowledgeModel):
    """Base of every knowledge-file model: frozen, extra-forbidden, alias-serialized, no nulls."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )

    @model_validator(mode="before")
    @classmethod
    def _refuse_explicit_null(cls, data: Any) -> Any:
        if isinstance(data, dict):
            nulls = sorted(str(key) for key, value in data.items() if value is None)
            if nulls:
                raise ValueError(
                    f"explicit null is not a knowledge-file value; omit the key instead: {nulls}"
                )
        return data

    def to_document(self) -> dict[str, Any]:
        """Return the JSON document this model is written as (aliases, absent fields omitted)."""

        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must not be blank")
    return value


def _exact_token(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    if value != value.strip():
        raise ValueError(f"value must not carry surrounding whitespace: {value!r}")
    return value


def require_repository_path(value: str) -> str:
    """Refuse anything that is not a plain repository-relative POSIX path, without cleaning it."""

    _exact_token(value)
    if value.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"path must be repository-relative: {value!r}")
    if "\\" in value or "\x00" in value:
        raise ValueError(f"path must use POSIX separators and no NUL byte: {value!r}")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError(f"path must not contain empty, '.' or '..' segments: {value!r}")
    return require_plain_git_path(value, what="path")


Text = Annotated[str, Field(min_length=1, max_length=PROSE_MAX_LENGTH), AfterValidator(_nonblank)]
Label = Annotated[
    str, Field(min_length=1, max_length=REFERENCE_MAX_LENGTH), AfterValidator(_exact_token)
]
RepositoryPath = Annotated[
    str, Field(min_length=1, max_length=PATH_MAX_LENGTH), AfterValidator(require_repository_path)
]
RecordId = Annotated[str, Field(pattern=RECORD_ID_PATTERN)]
InvariantId = Annotated[str, Field(pattern=id_pattern(RECORD_PREFIXES["invariant"]))]
FamilyId = Annotated[str, Field(pattern=id_pattern(RECORD_PREFIXES["family"]))]
DecisionId = Annotated[str, Field(pattern=id_pattern(RECORD_PREFIXES["decision"]))]
IncidentId = Annotated[str, Field(pattern=id_pattern(RECORD_PREFIXES["incident"]))]
ContentHash = Annotated[str, Field(pattern=CONTENT_HASH_PATTERN)]
GitBlobId = Annotated[str, Field(pattern=GIT_OBJECT_PATTERN)]


def require_unique(values: tuple[Any, ...], *, what: str) -> None:
    """Refuse a list that repeats a value."""

    if len(set(values)) != len(values):
        raise ValueError(f"{what} must not repeat a value")


# --------------------------------------------------------------------------------------------------
# Anchors (rule 3)
# --------------------------------------------------------------------------------------------------


class SymbolLocator(FileModel):
    """A symbol name, resolved by the shipped extractor (``memory_quality/style/citations``)."""

    kind: Literal["symbol"] = "symbol"
    name: Label


class LineRangeLocator(FileModel):
    """A one-based inclusive line range in the recorded blob."""

    kind: Literal["line_range"] = "line_range"
    start: int = Field(ge=1)
    end: int = Field(ge=1)

    @model_validator(mode="after")
    def _require_ordered_range(self) -> LineRangeLocator:
        if self.end < self.start:
            raise ValueError("a line range's end must not precede its start")
        return self


class WholeFileLocator(FileModel):
    """The whole file at the recorded blob."""

    kind: Literal["file"] = "file"


Locator = Annotated[
    SymbolLocator | LineRangeLocator | WholeFileLocator, Field(discriminator="kind")
]


class Anchor(FileModel):
    """``{ path?, locator, blob, content }``: where a fact sits in code, and the bytes it named.

    ``blob`` is the Git blob id of the file the anchor was recorded against; ``content`` is
    ``sha256:`` of the bytes of the resolved range in that blob. ``path`` is omitted inside a file
    sidecar for the sidecar's own source file and required everywhere else; the owning model
    enforces which case applies.
    """

    path: RepositoryPath | None = None
    locator: Locator
    blob: GitBlobId
    content: ContentHash


# --------------------------------------------------------------------------------------------------
# Requirement references and external documents (rule 6)
# --------------------------------------------------------------------------------------------------


class TaskIdentity(FileModel):
    """The task a requirement packet belongs to: its repository and its task path.

    ``path`` is relative to the repository task root ``tasks/<repository>/`` of the coordination
    root (for example ``260928_maintained-invariant-knowledge``), the same convention as
    :class:`agents_remember.models.task_document_ref.TaskDocumentRef`: the root stays out of the
    value so the reference survives a coordination tree mounted elsewhere.
    """

    repository: Label
    path: RepositoryPath


class RequirementReference(FileModel):
    """``{ task, packet, id, version }``, resolved by ``consume_owner_resolution`` at the task root.

    ``packet`` is the task-relative packet path, ``id`` the stable ID (for example ``MIK-R21``) and
    ``version`` the packet version (``v1``).
    """

    task: TaskIdentity
    packet: RepositoryPath
    id: Label
    version: str = Field(pattern=REQUIREMENT_PACKET_VERSION_PATTERN)


class DocumentIdentity(FileModel):
    """An external document: its identity (URL or name) and, when known, its version."""

    document: Label
    version: Label | None = None


# --------------------------------------------------------------------------------------------------
# Reference targets and references (rule 6)
# --------------------------------------------------------------------------------------------------

IdTargetKind = Literal["invariant", "family", "decision", "incident", "record"]
_ID_TARGET_PREFIX: Final[dict[str, str]] = {
    "invariant": RECORD_PREFIXES["invariant"],
    "family": RECORD_PREFIXES["family"],
    "decision": RECORD_PREFIXES["decision"],
    "incident": RECORD_PREFIXES["incident"],
}


class AnchorTarget(FileModel):
    """A ``code`` or ``test`` target: an anchor."""

    kind: Literal["code", "test"]
    anchor: Anchor


class IdTarget(FileModel):
    """An ``invariant``, ``family``, ``decision``, ``incident`` or ``record`` target: an ID.

    The four named kinds require their own prefix; ``record`` takes any record ID.
    """

    kind: IdTargetKind
    id: RecordId

    @model_validator(mode="after")
    def _require_matching_prefix(self) -> IdTarget:
        prefix = _ID_TARGET_PREFIX.get(self.kind)
        if prefix is not None and not self.id.startswith(f"{prefix}-"):
            raise ValueError(f"a {self.kind} target must name a {prefix}- ID, not {self.id!r}")
        return self


class RequirementTarget(FileModel):
    """A ``requirement`` target: a requirement reference."""

    kind: Literal["requirement"] = "requirement"
    requirement: RequirementReference


class ExternalTarget(FileModel):
    """An ``external`` target: a document identity."""

    kind: Literal["external"] = "external"
    document: DocumentIdentity


class UnresolvedTarget(FileModel):
    """An ``unresolved`` target: a legacy citation's text. Only the conversion writes it."""

    kind: Literal["unresolved"] = "unresolved"
    text: Text


ReferenceTarget = Annotated[
    AnchorTarget | IdTarget | RequirementTarget | ExternalTarget | UnresolvedTarget,
    Field(discriminator="kind"),
]


class Reference(FileModel):
    """One numbered reference: ``{ targets: [ … ], note? }`` with at least one target."""

    targets: tuple[ReferenceTarget, ...] = Field(min_length=1)
    note: Text | None = None


ReferenceNumber = Annotated[str, Field(pattern=REFERENCE_NUMBER_PATTERN)]
References = dict[ReferenceNumber, Reference]


def anchors_of(references: References) -> list[Anchor]:
    """Return every anchor a reference table names, in table order."""

    return [
        target.anchor
        for reference in references.values()
        for target in reference.targets
        if isinstance(target, AnchorTarget)
    ]


# --------------------------------------------------------------------------------------------------
# Links (rule 7)
# --------------------------------------------------------------------------------------------------


def _require_link_string_target(value: str) -> str:
    if value.startswith(ROUTE_TARGET_PREFIX):
        require_repository_path(value[len(ROUTE_TARGET_PREFIX) :])
        return value
    if re.match(RECORD_ID_PATTERN, value) is None:
        raise ValueError(f"a link target string is a record ID or 'route:<path>', not {value!r}")
    return value


LinkStringTarget = Annotated[
    str, Field(max_length=PATH_MAX_LENGTH), AfterValidator(_require_link_string_target)
]

Relation = Literal[
    "explains",
    "constrains",
    "motivated_change_to",
    "reconsider_on",
    "violated",
    "exposed_gap_in",
    "occurred_at",
    "led_to",
    "threatens",
    "occurs_at",
    "conditions",
    "underlies",
    "bounds",
    "exercises",
    "diagnoses",
    "defines_term_in",
]


class Link(FileModel):
    """``{ target, relation, alternative? }``: one outgoing relationship, owned by its record.

    ``target`` is a record ID, ``route:<path>``, an anchor (with its path) or a requirement
    reference. ``alternative`` is the index of the decision alternative a ``reconsider_on`` link
    belongs to; it is required for that relation and refused for every other one. Which relations a
    record kind may use is checked by the record (:data:`.records.RELATIONS_BY_KIND`).
    """

    target: LinkStringTarget | Anchor | RequirementReference
    relation: Relation
    alternative: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _require_consistent_link(self) -> Link:
        if isinstance(self.target, Anchor) and self.target.path is None:
            raise ValueError("an anchor link target must name its path")
        if (self.relation == "reconsider_on") != (self.alternative is not None):
            raise ValueError("'alternative' is required for reconsider_on and refused otherwise")
        return self


# --------------------------------------------------------------------------------------------------
# Admission and origin (rule 4)
# --------------------------------------------------------------------------------------------------

LEGACY_UNASSESSED: Final = "legacy-unassessed"
LegacyUnassessed = Literal["legacy-unassessed"]
InvariantCriterion = Literal[
    "spans_locations", "guarded_by_test", "family_guarantee", "prevents_costly_mistake"
]
FamilyCriterion = Literal["joint_guarantee"]
DecisionCriterion = Literal["real_alternatives", "constrains_future_work"]


class _AdmissionBase(FileModel):
    justification: Text

    @model_validator(mode="after")
    def _require_unique_criteria(self) -> _AdmissionBase:
        require_unique(tuple(getattr(self, "criteria", ())), what="admission criteria")
        return self


class InvariantAdmission(_AdmissionBase):
    """``{ criteria, justification }`` with the invariant criteria (meaning: MIK-R27)."""

    criteria: tuple[InvariantCriterion, ...] = Field(min_length=1)


class FamilyAdmission(_AdmissionBase):
    """``{ criteria, justification }`` with the family criterion."""

    criteria: tuple[FamilyCriterion, ...] = Field(min_length=1)


class DecisionAdmission(_AdmissionBase):
    """``{ criteria, justification }`` with the decision criteria."""

    criteria: tuple[DecisionCriterion, ...] = Field(min_length=1)


class HandoffOrigin(FileModel):
    """The hand-off list a record came from, and any hand-off evidence text it carried.

    At least one part is present. An exported record has no list path but may carry the legacy
    ``Hand-off kind:``, ``Producer's disposition:`` and ``Evidence:`` lines (MIK-R24 rule 4).
    """

    path: Label | None = None
    evidence: tuple[Text, ...] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _require_some_part(self) -> HandoffOrigin:
        if self.path is None and self.evidence is None:
            raise ValueError("a hand-off origin names a list path, evidence text, or both")
        return self


class Origin(FileModel):
    """Who authored a record: ``task``, plus ``leaf`` or ``wave``, hand-off and legacy identity."""

    task: Label
    leaf: Label | None = None
    wave: Label | None = None
    handoff: HandoffOrigin | None = None
    handoff_entry: Label | None = Field(default=None, alias="handoffEntry")
    legacy_id: Label | None = Field(default=None, alias="legacyId")

    @model_validator(mode="after")
    def _leaf_or_wave(self) -> Origin:
        if self.leaf is not None and self.wave is not None:
            raise ValueError("an origin names a leaf or a wave, not both")
        return self


class EntryOrigin(FileModel):
    """``{ leaf, handoffEntry? }``: which leaf authored a realization or proof entry."""

    leaf: Label
    handoff_entry: Label | None = Field(default=None, alias="handoffEntry")
