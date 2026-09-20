"""The common-base merge vocabulary: explicit inputs, coverage facts and one structural outcome.

A merge is a **structural** operation over three datasets that already exist: an explicit common
base, two sides derived from it, and a private candidate produced from the first side. Nothing in
this module can carry a judgement about whether the merged knowledge is *correct*: there is no
compatibility, acceptance, approval or "harmless" field, and the one non-refusal state is named
``structurally_merged`` because that is the entire claim it makes.

Three splits are load-bearing:

* **An explicit input versus a resolved one.** :class:`MergeInput` names a dataset *and* the exact
  logical identity the caller admitted for it. A caller cannot hand over a path and let the
  operation decide which dataset it meant; the identity is re-read and compared before any byte is
  copied.
* **A base claim versus a base fact.** :data:`MergeBaseClaim` is a closed union: either the caller
  states the commit it resolved as the unique common base, or it delegates that resolution and the
  ancestry evidence is consulted. There is no third member -- "the operation may pick one" is not
  expressible, which is what keeps an arbitrary merge-base result out of reach.
* **A measurement versus a verdict.** :class:`MergeCoverage` reports which tables the changeset
  touched, which tables the replay proved covered, and how many operations of each kind were
  materialised. :class:`MergeOutcome` reports the identities it observed. Neither can express an
  opinion about the data.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from agents_remember.models.knowledge.base import (
    GIT_OBJECT_PATTERN,
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.snapshot import SnapshotDestinationRequest

__all__ = [
    "MERGE_STATES",
    "AuthoredDecision",
    "AuthoredReconciliation",
    "ChangeOperationKind",
    "MergeBaseClaim",
    "MergeBaseRequest",
    "MergeBaseResolution",
    "MergeConflict",
    "MergeCoverage",
    "MergeInput",
    "MergeInputRole",
    "MergeOutcome",
    "MergeRequest",
    "ResolvedGitBase",
    "SuppliedGitBase",
    "TableCoverage",
    "expressible_decisions",
]

# What one materialised changeset operation did to its row. These are SQLite's own three
# operation names, restated as the vocabulary this package compares against rather than as
# strings a caller has to recognise.
ChangeOperationKind = Literal["INSERT", "UPDATE", "DELETE"]

# Which of the three positions one dataset occupies. The role is not decoration: the merge
# applies the *right* side onto a private copy of the *left*, so a caller that swaps them gets a
# different operation, and the role is what makes the request say which one it asked for.
MergeInputRole = Literal["base", "left", "right"]

# The exact non-refusal state of a merge. It is deliberately not "merged", "successful" or
# "compatible": the operation proved that every intended change reached a structurally valid
# candidate, and it proved nothing at all about whether the combined knowledge is correct.
MERGE_STATES = ("structurally_merged",)


class SuppliedGitBase(KnowledgeModel):
    """The caller already resolved the common base and names the exact commit it found."""

    kind: Literal["supplied"] = "supplied"
    commit_id: str = Field(pattern=GIT_OBJECT_PATTERN)
    tree_id: str = Field(pattern=GIT_OBJECT_PATTERN)


class ResolvedGitBase(KnowledgeModel):
    """The caller claims one commit is the *unique* common base and asks for the evidence.

    The claim is not trusted. It is checked against the repository: the named commit must be an
    ancestor of both sides, and it must be the only common base the history has. A history with
    zero or several common bases refuses rather than having this operation choose between them.
    """

    kind: Literal["resolved"] = "resolved"
    repository_root: Path
    base_commit_id: str = Field(pattern=GIT_OBJECT_PATTERN)
    left_commit_id: str = Field(pattern=GIT_OBJECT_PATTERN)
    right_commit_id: str = Field(pattern=GIT_OBJECT_PATTERN)


MergeBaseClaim = Annotated[
    SuppliedGitBase | ResolvedGitBase,
    Field(discriminator="kind"),
]


class MergeInput(KnowledgeModel):
    """One explicitly selected dataset of the merge, with the identity the caller admitted.

    ``role`` says which position the dataset occupies; ``reference`` is the caller's own durable
    anchor for it (a Git tree, a commit pair, a snapshot reference). The reference is carried as a
    fact and never resolved here: this layer does not read Git objects to decide what a dataset is.
    """

    role: MergeInputRole
    reference: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    database_path: Path
    expected_identity: SnapshotIdentity


# The two authored decisions one refused conflict admits. They are named after the sides the
# refusal itself prints -- ``expected`` is "the left side's stored value" and ``observed`` is "the
# right side's supplied value" -- so an agent that reads the refusal already knows what each one
# means. ``keep-left`` retracts the arriving (right) change for that conflict, so the left's state
# stands; ``keep-right`` applies the arriving change over the left's stored value. Neither is a
# policy: each names one conflict, and the merge still refuses every conflict the caller did not
# name.
AuthoredDecision = Literal["keep-left", "keep-right"]

# The one conflict code whose overwrite direction this package has proven. ``keep-left`` is offered
# for every conflict that names a row, because retracting the arriving change is always a legal
# application; ``keep-right`` is offered only here, so no caller is invited into an overwrite the
# engine has not shown it can perform.
_OVERWRITE_CONFLICT_CODES = frozenset({"conflicting_values"})

# The one conflict code SQLite reports *without* a row, and therefore the one code a decision that
# names no row can answer. See :class:`AuthoredReconciliation`.
_ROWLESS_CONFLICT_CODE = "delete_reference_conflict"


def expressible_decisions(conflict: MergeConflict | None) -> tuple[AuthoredDecision, ...]:
    """Return the authored decisions one refused conflict admits, in the order to read them.

    Three answers, and the shape of the conflict decides which:

    * a conflict that named no row at all -- a schema disagreement above all -- admits nothing: there
      is no row to reconcile, and the refusal's own next action says the difference is reported
      rather than reconciled;
    * the referential conflict SQLite reports without a row admits ``keep-left`` only, which is the
      retraction the refusal advertises;
    * every conflict that named a row admits ``keep-left``, and ``keep-right`` where the overwrite
      direction is proven.

    This is the one place that judgement lives, so the refusal, the public response and the merge's
    conflict policy cannot answer it differently.
    """

    if conflict is None:
        return ()
    if conflict.code == _ROWLESS_CONFLICT_CODE:
        return ("keep-left",)
    if conflict.table is None or conflict.record_id is None:
        return ()
    if conflict.code in _OVERWRITE_CONFLICT_CODES:
        return ("keep-left", "keep-right")
    return ("keep-left",)


class AuthoredReconciliation(KnowledgeModel):
    """One explicit authored decision about one conflict a merge refused.

    This is the *authored* half of the explicit-reconciliation requirement, and its shape is what
    keeps it from becoming an automatic resolution policy: a caller cannot say "prefer my side",
    cannot name a table without a row, and cannot leave the decision out.

    It has exactly two shapes, and which one a caller means is structural rather than a mode flag:

    * ``table`` and ``record_id`` name the exact row the refusal printed -- the identity rendered
      exactly as the refusal rendered it -- and the decision applies to that row and to nothing else;
    * both are absent, and then the decision applies only to a conflict the engine reported *without
      a row*: the referential shape, where SQLite hands the conflict callback no change at all. Only
      ``keep-left`` is expressible there, and it is not a weaker answer -- retracting the arriving
      change is what either restores the removed row or drops the new reference, depending on which
      side did the removing.
    """

    table: str | None = Field(default=None, min_length=1, max_length=LABEL_MAX_LENGTH)
    record_id: str | None = Field(default=None, min_length=1, max_length=REFERENCE_MAX_LENGTH)
    decision: AuthoredDecision

    @model_validator(mode="after")
    def _require_one_decidable_shape(self) -> AuthoredReconciliation:
        if (self.table is None) != (self.record_id is None):
            raise ValueError(
                "an authored reconciliation names the table and the record identity together, or "
                "neither of them: half a row identity names no row"
            )
        if self.table is None and self.decision != "keep-left":
            raise ValueError(
                "a decision that names no row can only retract the arriving change; an overwrite "
                "needs the exact row the engine refused"
            )
        return self


class MergeBaseRequest(KnowledgeModel):
    """One base-resolution request: the namespace, the Git claim and the three exact datasets."""

    repository: RepositoryIdentity
    git_base: MergeBaseClaim
    inputs: tuple[MergeInput, MergeInput, MergeInput] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def _require_one_input_per_role(self) -> MergeBaseRequest:
        roles = [item.role for item in self.inputs]
        if sorted(roles) != ["base", "left", "right"]:
            raise ValueError(
                "a merge names exactly one base, one left and one right dataset; this request "
                f"names {roles}"
            )
        for item in self.inputs:
            if item.expected_identity.repository_id != self.repository.repository_id:
                raise ValueError(
                    f"the {item.role} dataset identity belongs to another repository namespace"
                )
        return self

    def input_for(self, role: MergeInputRole) -> MergeInput:
        """Return the one dataset occupying ``role``.

        The validator above has already refused a request whose roles are not exactly one of each,
        so this accessor has an answer for every member of the union and needs no failure branch.
        """

        found = [item for item in self.inputs if item.role == role]
        return found[0]


class MergeBaseResolution(KnowledgeModel):
    """What base resolution proved, in the terms the merge operation consumes.

    ``git_base_commit`` is the exact commit the resolution admitted, or ``None`` when the caller
    supplied the base directly and no ancestry check applied. ``uniqueness_checked`` records which
    of the two it was, so a reader can tell "the history had one common base" from "the caller said
    which commit it was" without re-deriving it from the claim.
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    base_reference: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    base_identity: SnapshotIdentity
    left_identity: SnapshotIdentity
    right_identity: SnapshotIdentity
    git_base_commit: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    uniqueness_checked: bool

    @model_validator(mode="after")
    def _require_one_namespace(self) -> MergeBaseResolution:
        identities = (self.base_identity, self.left_identity, self.right_identity)
        if any(item.repository_id != self.repository_id for item in identities):
            raise ValueError(
                "a merge resolution names one repository namespace and three datasets inside it"
            )
        if self.uniqueness_checked and self.git_base_commit is None:
            raise ValueError(
                "a checked common base names the commit that was proven unique; a resolution that "
                "checked nothing must not claim it did"
            )
        return self


class MergeRequest(KnowledgeModel):
    """One complete merge request: the proven base, the exact datasets, and the destination.

    The paths are restated here beside the resolution's identities rather than being carried inside
    it, because they are local operation facts: a dataset *identity* is what the resolution proves,
    and the file it is read from is an input this request names. A dataclass is used rather than a
    third model member so the paths cannot be serialized into anything durable.

    ``destination`` is optional on purpose. A caller may run the merge and inspect the structural
    outcome without publishing anything; when it does publish, the install goes through the same
    destination-admitted contract every other closed snapshot uses.

    ``reconciliation`` is the one place an *authored* decision enters the merge, and it is optional
    because a merge that resolves nothing by itself is the whole point of this operation. When it is
    present the caller has named the exact record it refuses to let the engine refuse, and has said
    which side's authored value is the reconciled one. It cannot express a policy: it names one
    row, and every conflict it does not name is still refused exactly as it is today.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    resolution: MergeBaseResolution
    databases: Mapping[MergeInputRole, Path]
    destination: SnapshotDestinationRequest | None = None
    reconciliation: AuthoredReconciliation | None = None

    @model_validator(mode="after")
    def _require_every_role(self) -> MergeRequest:
        if set(self.databases) != {"base", "left", "right"}:
            raise ValueError(
                "a merge request names the base, left and right datasets exactly once; this one "
                f"names {sorted(self.databases)}"
            )
        return self


class TableCoverage(KnowledgeModel):
    """How one canonical table was covered by one base-to-side changeset and its replay.

    ``table_changed`` is read from the two datasets, not from the changeset: it is whether the
    side's rows differ from the base's rows for this table. ``operations`` counts the materialised
    changeset operations for the table. A table that changed but contributed no operation is the
    silent omission this coverage exists to catch, and ``replayed`` records whether applying the
    changeset to a fresh copy of the base reproduced the side's whole logical dataset.
    """

    table: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    table_changed: bool
    operations: int = Field(ge=0)
    replayed: bool

    @model_validator(mode="after")
    def _require_operation_when_changed(self) -> TableCoverage:
        if self.table_changed and self.replayed and self.operations == 0:
            raise ValueError(
                f"table {self.table} changed between the base and its side but the changeset "
                "carried no operation for it; a replay cannot have covered it"
            )
        return self


class MergeCoverage(KnowledgeModel):
    """The complete coverage fact of one base-to-side delta.

    ``changeset_digest`` is the sha256 of the exact changeset bytes, so two runs that produced the
    same delta are comparable without keeping the bytes. ``tables`` covers **every** canonical
    table, including the unchanged ones, because "this table was examined and had nothing to carry"
    is a different fact from "this table was never attached".
    """

    side: MergeInputRole
    source_digest: str = Field(pattern=SHA256_PATTERN)
    base_digest: str = Field(pattern=SHA256_PATTERN)
    changeset_digest: str = Field(pattern=SHA256_PATTERN)
    changeset_bytes: int = Field(ge=0)
    replayed_digest: str = Field(pattern=SHA256_PATTERN)
    operations: int = Field(ge=0)
    tables: tuple[TableCoverage, ...]

    @model_validator(mode="after")
    def _require_every_canonical_table(self) -> MergeCoverage:
        names = [entry.table for entry in self.tables]
        if len(set(names)) != len(names):
            raise ValueError("a coverage fact names each canonical table exactly once")
        counted = sum(entry.operations for entry in self.tables)
        if counted != self.operations:
            raise ValueError(
                f"the coverage total {self.operations} is not the sum of its per-table counts "
                f"{counted}"
            )
        return self

    @property
    def changed_tables(self) -> tuple[str, ...]:
        """Return the tables whose rows differ between the base and this side."""

        return tuple(entry.table for entry in self.tables if entry.table_changed)

    @property
    def table_operations(self) -> dict[str, int]:
        """Return the per-table operation count, in canonical table order."""

        return {entry.table: entry.operations for entry in self.tables}


# How much of the conflicting row the engine actually attributed. A caller reads this instead of
# inferring attribution from a field being absent, and ``engine_attributed`` is a claim the
# operation can only make when SQLite handed the callback the change itself.
ConflictAttribution = Literal["engine_attributed", "engine_reported_without_row"]


class MergeConflict(KnowledgeModel):
    """The first blocking conflict of an application, with exactly the facts SQLite supplied.

    Two shapes reach here and they are different facts:

    * a **row-level** conflict, where SQLite hands the callback the operation in hand. ``attribution``
      is ``engine_attributed`` and ``table``, ``operation`` and ``record_id`` are all present;
      ``record_id`` is the exact key of the row the engine refused, copied out of the change the
      callback received. It is read from the operation's *old* values, because a changeset supplies
      only the columns an operation changes and an ``UPDATE`` leaves its key columns unchanged -- a
      key read from the new side would be the not-supplied marker rather than a row.
    * a **foreign-key** conflict, where SQLite hands the callback *no change at all* and reports
      only that the application could not be completed. ``attribution`` is
      ``engine_reported_without_row``, the row fields are absent, and ``detail`` says so.

    Nothing is fabricated to fill either gap. A row-level conflict whose change carried no readable
    key columns reports no ``record_id`` and must say so in ``detail`` rather than substitute a row
    identity, and no violation count is reported for the foreign-key shape: the pinned binding raises
    ``ConstraintError`` with no count in its arguments, so a number there would be the operation's own
    inference rather than the engine's report.
    """

    code: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    attribution: ConflictAttribution
    table: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    operation: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    record_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_consistent_conflict_facts(self) -> MergeConflict:
        row = (self.table, self.operation, self.record_id)
        if self.attribution == "engine_attributed":
            if self.table is None or self.operation is None:
                raise ValueError(
                    "an engine-attributed conflict names the table and the operation SQLite handed "
                    "the callback"
                )
            if self.record_id is None and self.detail is None:
                raise ValueError(
                    "a row-level conflict that cannot name the row it refused must say so in "
                    "detail, rather than leaving a caller to read a missing field as absent data"
                )
            return self
        if any(item is not None for item in row):
            raise ValueError(
                "the engine reported this conflict without a row, so it must not name one"
            )
        if self.detail is None:
            raise ValueError(
                "a conflict the engine reported without a row must say so, or a caller cannot tell "
                "an unattributed conflict from a missing field"
            )
        return self


class MergeOutcome(KnowledgeModel):
    """The factual outcome of one merge attempt, with the structural state it reached.

    ``structurally_merged`` carries the merged identity and the whole coverage record of the
    applied side. A refusal carries the typed code, the first blocking conflict when the failure
    was a conflict, and the coverage measured *before* application -- a merge that refused still
    knows what it did not apply, and the two coverage facts are what let a caller see that the
    inputs were read completely even though nothing was published.
    """

    state: Literal["structurally_merged", "refused"]
    resolution: MergeBaseResolution
    operation: str = Field(default="merge_knowledge_datasets", max_length=LABEL_MAX_LENGTH)
    merged_identity: SnapshotIdentity | None = None
    destination_ref: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    publication_state: Literal["published", "no_change", "not_requested"] | None = None
    changeset_digests: dict[str, str] = Field(default_factory=dict)
    coverage: tuple[MergeCoverage, ...] = ()
    conflict: MergeConflict | None = None
    refusal: KnowledgeRefusal | None = None
    next_action: str = Field(default="", max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_consistent_merge_outcome(self) -> MergeOutcome:
        if self.state == "refused":
            if self.refusal is None:
                raise ValueError("a refused merge must carry its refusal")
            if self.merged_identity is not None:
                raise ValueError(
                    "a refused merge published no candidate and reports no merged identity"
                )
            return self
        if self.refusal is not None or self.conflict is not None:
            raise ValueError("a structurally merged result cannot also carry a refusal")
        if self.merged_identity is None:
            raise ValueError(
                "a structurally merged result names the identity of the candidate it produced"
            )
        if not self.coverage:
            raise ValueError(
                "a structurally merged result carries the coverage of the delta it applied"
            )
        return self
