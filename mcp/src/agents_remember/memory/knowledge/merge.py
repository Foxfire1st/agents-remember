"""The guarded common-base merge: one structural candidate, or one explicit conflict.

This module is the operation. It takes a resolution that has already proven which dataset is the
common base (see :mod:`merge_base`), two sides derived from it, and a private workspace, and it
either produces a structurally valid candidate or refuses with a typed code. The sequence below is
the whole contract, and each step exists because the step before it cannot see what it prevents:

1. **Capability.** The selected binding must be able to produce and apply a changeset at all.
2. **Preflight.** Every input's declared structure is compared against the supported schema
   generation, before a session exists. This comparison exists because SQLite's changeset
   application can silently skip a table it cannot match: an input that is not this schema
   generation would produce a green merge over a dropped change set. Comparing each input against
   the one declared generation *is* the whole check -- two accepted inputs are structurally
   identical to it and therefore to each other, so a separate pairwise pass would be unreachable
   code describing an enforcement no caller performs.
3. **Deltas.** ``diff(base, side)`` for both sides, as changesets, with every canonical table
   attached, and every operation materialised while the cursor is still valid.
4. **Coverage.** Each delta is replayed into a fresh copy of the base and must reproduce its side's
   whole logical dataset. This is the check a return code cannot make.
5. **Input integrity.** Each side's sealed revisions must still be the aggregates the base sealed.
6. **Application.** A private target is created from the *left* side and the right delta is applied
   with ``flags=0``, no filter and a conflict callback that copies the facts and always aborts.
7. **Postconditions.** Foreign keys, typed rows, sealed payloads, sealed edges and every
   materialised operation are checked against the result.
8. **Publication.** Only then is the merged candidate frozen through the closed-snapshot procedure
   and installed through the destination-admitted publication contract, when the caller asked for
   one.

**Lock policy.** One resource lock at a time, never nested. The three inputs and the coverage
replays take no lock at all; the merged temporary's lock covers the freeze; the destination's lock
covers the install. The destination lock is taken by the publication operation and never held while
an input is being read.

**What this operation never does.** It does not configure Git, install a merge driver, create a
commit, move a ref, strip a conflicting operation, prefer one side's value, build a patchset, or
attach a verdict about whether the merged knowledge is correct. Its successful state is
``structurally_merged`` and that is the entire claim.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import apsw

from agents_remember.errors import LockCapabilityError
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.memory.knowledge.closed_snapshot import freeze_closed_snapshot
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.merge_base import MERGE_OPERATION
from agents_remember.memory.knowledge.merge_changeset import (
    AppliedChangeset,
    Delta,
    MaterializedChange,
    apply_changeset,
    build_delta,
    replay_delta,
    require_session_capability,
)
from agents_remember.memory.knowledge.merge_refusals import (
    changeset_incomplete_refusal,
    changeset_postcondition_failed_refusal,
    conflicting_values_refusal,
    delete_reference_conflict_refusal,
    duplicate_identity_refusal,
    duplicate_relationship_refusal,
)
from agents_remember.memory.knowledge.merge_schema import (
    require_supported_structure,
    selected_generation,
)
from agents_remember.memory.knowledge.merge_validation import (
    MergeInputs,
    require_immutable_revisions_preserved,
    require_side_inputs_preserved,
    require_structural_validity,
    unapplied_changes,
)
from agents_remember.memory.knowledge.publication import publish_prepared_snapshot
from agents_remember.memory.knowledge.refusals import (
    RefusalFacts,
    lock_capability_refusal,
    refusal,
)
from agents_remember.memory.knowledge.routes import require_acyclic_routes
from agents_remember.memory.knowledge.schema_generations import SchemaGeneration
from agents_remember.memory.knowledge.store import open_existing_knowledge_store
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.merge import (
    AuthoredReconciliation,
    MergeBaseResolution,
    MergeConflict,
    MergeCoverage,
    MergeOutcome,
    MergeRequest,
    RetractionPrecondition,
    TableCoverage,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.snapshot import PreparedKnowledgeSnapshot

# The private file names inside one merge workspace. They are never exposed: the merged candidate
# only becomes public bytes through the snapshot publication contract.
STAGED_LEFT_NAME = "target.sqlite"
REPLAY_LEFT_NAME = "replay-left.sqlite"
REPLAY_RIGHT_NAME = "replay-right.sqlite"
_FROZEN_STAGE_NAME = "snapshot.sqlite"

# The copy a referential refusal's retraction probe is applied to. It is a fourth private name for
# the same reason as the others: the probe is a database of its own, and a probe that overwrote the
# real target would make its answer describe a state nobody ran.
PROBE_LEFT_NAME = "probe-left.sqlite"

# SQLite's conflict codes, as the merge's conflict taxonomy names them. The mapping is by code and
# the table decides between the two constraint meanings, because SQLite reports a duplicate row
# under a unique relationship tuple with the same code it uses for a constraint violation.
_CONFLICT_DATA = int(apsw.SQLITE_CHANGESET_DATA)
_CONFLICT_NOTFOUND = int(apsw.SQLITE_CHANGESET_NOTFOUND)
_CONFLICT_CONFLICT = int(apsw.SQLITE_CHANGESET_CONFLICT)
_CONFLICT_CONSTRAINT = int(apsw.SQLITE_CHANGESET_CONSTRAINT)
_CONFLICT_FOREIGN_KEY = int(apsw.SQLITE_CHANGESET_FOREIGN_KEY)

# The tables whose unique declarations are relationships rather than the row's own identity, so a
# conflict on them means "the union declares this relationship twice", not "two rows claim one id".
_RELATIONSHIP_TABLES = frozenset({"family_member", "realization_claim"})

# What a conflict record carries when the engine handed the callback a change whose key columns were
# not readable. It is a statement about the changeset, not a placeholder for a row identity: the
# conflict callback copies the key out of the change it was given, and a change with no primary-key
# columns names no row.
KEY_NOT_SUPPLIED = "<key not supplied by SQLite>"


def merge_knowledge_datasets(request: MergeRequest) -> MergeOutcome:
    """Merge two sides against an explicit common base, or refuse with a typed code.

    Every failure path returns a typed refusal: a filesystem or library error inside a step this
    operation owns is mapped to the code for that step rather than escaping as a raw ``OSError`` or
    ``apsw.Error``. The caller can therefore branch on a code for every outcome, and no input is
    modified on any path.
    """

    resolution = request.resolution
    unavailable = require_session_capability(MERGE_OPERATION)
    if unavailable is not None:
        return MergeOutcome(state="refused", resolution=resolution, refusal=unavailable)
    workspace = tempfile.mkdtemp(prefix="knowledge-merge-")
    try:
        run = MergeRun(request=request, workspace=Path(workspace))
        databases = _resolve_databases(request)
        if isinstance(databases, KnowledgeRefusal):
            return run.refused(databases)
        run.databases = databases
        return _run_merge(run)
    except (apsw.Error, OSError) as error:
        return MergeOutcome(
            state="refused",
            resolution=resolution,
            refusal=changeset_postcondition_failed_refusal(
                MERGE_OPERATION,
                f"the merge could not complete: {type(error).__name__}: {error}",
                facts=RefusalFacts(record_id=str(workspace)),
            ),
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


@dataclass
class MergeRun:
    """One merge's own state: the request, its workspace, and what it has proven so far.

    The coverage list grows as each delta is measured, so a refusal late in the sequence still
    carries the coverage the operation had already proved. It is a mutable working record rather
    than a published value, and nothing here is durable.
    """

    request: MergeRequest
    workspace: Path
    databases: dict[str, Path] | None = None
    deltas: dict[str, Delta] = field(default_factory=dict)
    coverage: list[MergeCoverage] = field(default_factory=list)
    # The generation the three inputs agreed on, selected once by the preflight and used by every
    # later step in this operation: the structural comparison, the session's attachment set, the
    # delta's table list and the merged dataset's own digest (requirements 2.3, 6.1, 6.2). It is
    # ``None`` only before the preflight has run, and a step that needs it before then is a defect.
    generation: SchemaGeneration | None = None

    def selected_generation(self) -> SchemaGeneration:
        """Return the operation's selected generation, or the defect that it was never selected."""

        if self.generation is None:
            raise KnowledgeMergeDefect(
                "a merge run reached a step before selecting the generation its inputs agree on"
            )
        return self.generation

    @property
    def resolution(self) -> MergeBaseResolution:
        """Return the resolution this run was admitted with."""

        return self.request.resolution

    def refused(
        self,
        denial: KnowledgeRefusal,
        *,
        conflict: MergeConflict | None = None,
    ) -> MergeOutcome:
        """Return a refused outcome carrying everything this run had already proven."""

        return MergeOutcome(
            state="refused",
            resolution=self.resolution,
            coverage=tuple(self.coverage),
            conflict=conflict,
            refusal=denial,
        )

    def merged(
        self,
        identity: SnapshotIdentity,
        digests: dict[str, str],
        *,
        destination_ref: str | None = None,
        publication_state: Literal["published", "no_change", "not_requested"] = "not_requested",
    ) -> MergeOutcome:
        """Return the structural outcome this run reached, with what the publication actually did."""

        return MergeOutcome(
            state="structurally_merged",
            resolution=self.resolution,
            merged_identity=identity,
            destination_ref=destination_ref,
            publication_state=publication_state,
            changeset_digests=digests,
            coverage=tuple(self.coverage),
        )

    def paths(self) -> dict[str, Path]:
        """Return the resolved databases, or the defect that they were not resolved."""

        if self.databases is None:
            raise KnowledgeMergeDefect("a merge run reached a step before resolving its datasets")
        return self.databases

    def acyclic_routes(self, connection: apsw.Connection) -> KnowledgeRefusal | None:
        """Refuse a merged candidate whose route hierarchy reaches itself.

        The walk runs on the connection the right delta was applied on, inside that application's
        own uncommitted transaction, so a candidate that reparents a route into a cycle is rolled
        back instead of published: the rule and the write it judges are one atomic step, which is
        what "inside the same transaction" has to mean for a rule over inserted rows. ``route`` is
        inside the merge's attached table set and a right-side ``route.parent_route_id`` change is
        a real operation the replay performs, so this is a production path that has to run the
        rule rather than a check the authoring operation happens to own. The refusal is attributed
        to this operation, the rule itself stays the one walk in
        :mod:`agents_remember.memory.knowledge.routes`.
        """

        return require_acyclic_routes(
            connection, self.request.resolution.repository_id, MERGE_OPERATION
        )


class KnowledgeMergeDefect(RuntimeError):
    """A merge internal state that the sequence itself makes unreachable."""


def _run_merge(run: MergeRun) -> MergeOutcome:
    """Run the whole sequence inside one owned workspace."""

    distinctness = require_distinct_roles(run.paths())
    if distinctness is not None:
        return run.refused(distinctness)
    moved = require_admitted_identities(run)
    if moved is not None:
        return run.refused(moved)
    preflight = _preflight(run)
    if preflight is not None:
        return run.refused(preflight)
    # Integrity is checked before any delta is derived. A side that rewrote a sealed revision is not
    # an input whose differences can be replayed at all -- the database refuses the rewrite -- so
    # reporting a coverage failure for it would name the wrong cause.

    integrity = _require_side_integrity(run.paths())
    if integrity is not None:
        return run.refused(integrity)
    delta_refusal = _build_covered_deltas(run)
    if delta_refusal is not None:
        return run.refused(delta_refusal)
    return _apply_and_validate(run)


def _apply_and_validate(run: MergeRun) -> MergeOutcome:
    """Create the private target from the left side, apply the right delta and validate it."""

    databases = run.paths()
    merged_path = run.workspace / STAGED_LEFT_NAME
    shutil.copyfile(databases["left"], merged_path)
    right = run.deltas["right"]
    applied = apply_changeset(
        right,
        merged_path,
        within_transaction=run.acyclic_routes,
        reconciliations=run.request.reconciliations,
    )
    if applied.refusal is not None:
        return run.refused(applied.refusal)
    if applied.conflicted or applied.detail:
        return run.refused(
            _conflict_refusal(applied, right),
            conflict=_conflict_facts(
                applied, right, precondition=_retraction_precondition(run, applied)
            ),
        )
    inputs = MergeInputs(
        base=databases["base"],
        left=databases["left"],
        right=databases["right"],
        merged=merged_path,
    )
    # Three checks on the produced candidate, with two different reachability facts, both stated in
    # the callees rather than left implicit: the structural check has a failing case (a candidate
    # carrying a foreign-key violation), while the applied-change check and the merged-candidate
    # immutability check cannot be reached by a black-box case and are exercised through their own
    # policies by test_knowledge_guarded_merge_boundaries.py.
    structural = require_structural_validity(merged_path, MERGE_OPERATION, role="merged")
    if structural is not None:
        return run.refused(structural)
    preserved = require_immutable_revisions_preserved(
        MERGE_OPERATION, base=inputs.base, candidate=inputs.merged, role="merged candidate"
    )
    if preserved is not None:
        return run.refused(preserved)
    postcondition = _authored_postcondition(right, applied, inputs.merged)
    if postcondition is not None:
        return run.refused(postcondition)
    return _publish(run, inputs.merged)


def _retraction_precondition(run: MergeRun, applied: AppliedChangeset) -> RetractionPrecondition:
    """Whether this refusal leaves the row-less retraction available, measured rather than inferred.

    ``keep-left`` on a referential conflict is a *retraction*: it removes the arriving rows that
    break a declared reference, and the retraction is bounded to rows the arriving delta INSERTED.
    The conflict code cannot say whether such a row exists -- the same code arrives in both
    orientations, one where the arriving side added the broken reference and one where it removed a
    row the retained side still cites -- so answering from the code alone advertised a decision that
    could not apply in the second orientation. This measures the answer instead, by running exactly
    the retraction the caller's decision would author.

    The measurement is the shipped application primitive, on the retained side's *own* bytes so
    nothing the real application has applied can leak into it, and it is read for one bit: whether
    that retraction reaches a reference-clean state. A clean state *is* the retraction the offer
    advertises, so the offer is only made where it has already been performed once. Anything else
    withholds the offer, and the referential case that reaches here carries a delta whose INSERTs
    cannot account for the violation -- the arriving side removed a row the retained side still
    references.

    The probe is discarded either way. It cannot publish: a settled probe mutates only the discarded
    copy, and an unsettled one is rolled back inside the application. It is also unreachable for a
    conflict that named a row, because the question only arises for the one row-less code, which is
    why the cheap code test comes first.
    """

    if applied.conflict_code != _CONFLICT_FOREIGN_KEY or applied.conflict_table is not None:
        return "arriving_insertion"
    probe = run.workspace / PROBE_LEFT_NAME
    shutil.copyfile(run.paths()["left"], probe)
    return (
        "arriving_insertion"
        if _retracts_arriving_rows(run.deltas["right"], probe)
        else "no_arriving_insertion"
    )


def _retracts_arriving_rows(delta: Delta, probe: Path) -> bool:
    """Apply the arriving delta's row-less decision to ``probe`` and report whether it settles.

    Only a settled application is reported as a retraction, because it is the only outcome that
    establishes one: the caller's decision was applied and the result holds no broken reference. An
    application that hit a conflict has not shown the retraction to be unavailable, and one that
    could not be applied at all has shown nothing, so the offer is withheld for both -- what this
    answers is whether the offer has been *proven*, not what the conflict is. The application's own
    outcome is deliberately not returned: the outcome of this refusal is the real application's to
    report, and this one is a throwaway.
    """

    probe_result = apply_changeset(
        delta,
        probe,
        reconciliations=(AuthoredReconciliation(decision="keep-left"),),
    )
    return not probe_result.conflicted and not probe_result.detail


def _authored_postcondition(
    right: Delta, applied: AppliedChangeset, merged: Path
) -> KnowledgeRefusal | None:
    """Refuse a result that lost a change no authored decision settled.

    Without an authored decision this is exactly :func:`require_applied_changes`: every operation
    the right delta materialised must be in the candidate, and the first one that is not is refused.
    With one, two things change and nothing else does.

    An operation on a row the caller decided is not required any more -- the caller said the left's
    state stands, or that the right's value is the reconciled one, so "the candidate carries the
    delta's operation" is no longer the intended outcome for that row. Everything else is still
    required, which is what keeps a decision from becoming a way to lose changes silently.

    The referential decision names no row, so it cannot exclude anything by identity. What it does
    give is a count: every retraction it authorised was reported by the engine, and the result may
    be missing exactly that many operations and no more. A candidate missing a different number has
    lost something the caller never authorised, and is refused here as it always was.
    """

    decided = {(item.table, item.record_id) for item in applied.resolved if item.table}
    required = tuple(
        change
        for change in right.operations
        if (change.table, _rendered_operation_key(change)) not in decided
    )
    unapplied = unapplied_changes(
        MERGE_OPERATION, merged=merged, side=right.side_path, operations=required
    )
    if not unapplied:
        return None
    authorised = sum(1 for item in applied.resolved if not item.table)
    if authorised and len(unapplied) == authorised:
        return None
    return unapplied[0]


def _rendered_operation_key(change: MaterializedChange) -> str:
    """Render one materialised operation's key the way the conflict callback renders it."""

    key = change.primary_key()
    if not key or any(value is None for value in key):
        return ""
    return "/".join(str(value) for value in key)


def _resolve_databases(request: MergeRequest) -> dict[str, Path] | KnowledgeRefusal:
    """Return each role's database path, or the refusal for an input that is not there."""

    paths = {role: Path(path) for role, path in request.databases.items()}
    missing = sorted(role for role, path in paths.items() if not path.is_file())
    if not missing:
        return paths
    return refusal(
        "selected_input_unavailable",
        MERGE_OPERATION,
        f"the {', '.join(missing)} dataset is not present",
        facts=RefusalFacts(record_id=", ".join(str(paths[role]) for role in missing)),
        next_action=(
            "Supply the exact selected datasets again; nothing recovers a missing dataset from "
            "HEAD, a branch name or a Markdown source."
        ),
    )


def require_admitted_identities(run: MergeRun) -> KnowledgeRefusal | None:
    """Refuse any dataset whose logical identity is no longer the one the resolution admitted.

    Base resolution already checked this once. The check is repeated here because the merge reads
    the files again: a dataset that moved between the two steps would otherwise be diffed against a
    base the caller never admitted, and the resulting candidate would describe a divergence nobody
    asked for.
    """

    resolution = run.request.resolution
    admitted = {
        "base": resolution.base_identity,
        "left": resolution.left_identity,
        "right": resolution.right_identity,
    }
    for role in ("base", "left", "right"):
        try:
            observed = dataset_identity(run.paths()[role])
        except (apsw.Error, OSError, ValueError) as error:
            return refusal(
                "selected_input_unavailable",
                MERGE_OPERATION,
                f"the {role} dataset could not be read at merge time: {error}",
                facts=RefusalFacts(record_id=str(run.paths()[role])),
                next_action=(
                    "Resolve the merge again against the datasets that are actually present. "
                    "Nothing here recovers a missing input from another revision."
                ),
            )
        if observed != admitted[role]:
            return refusal(
                "stale_precondition",
                MERGE_OPERATION,
                f"the {role} dataset is not the identity the resolution admitted for it",
                facts=RefusalFacts(
                    record_id=str(run.paths()[role]),
                    expected=admitted[role].logical_digest,
                    observed=observed.logical_digest,
                ),
                next_action=(
                    "Reresolve the base and merge the datasets that are actually there. No input "
                    "was modified and no candidate was published."
                ),
            )
    return None


def require_distinct_roles(databases: dict[str, Path]) -> KnowledgeRefusal | None:
    """Refuse a request that names one file as more than one of the three datasets.

    Three different identities at one path is the shape a caller reaches for when it means to say
    "there is no divergence"; a merge of a dataset with itself is not a structural merge, so the
    explicit input requirement refuses it rather than reporting an empty success.
    """

    resolved = {role: str(Path(path).resolve()) for role, path in databases.items()}
    seen: dict[str, str] = {}
    for role in ("base", "left", "right"):
        first = seen.get(resolved[role])
        if first is None:
            seen[resolved[role]] = role
            continue
        return refusal(
            "selected_input_unavailable",
            MERGE_OPERATION,
            f"the {first} and {role} datasets are the same file",
            facts=RefusalFacts(
                record_id=resolved[role], expected=f"{first} dataset", observed=f"{role} dataset"
            ),
            next_action=(
                "Name three distinct datasets. A merge reads three inputs; one file cannot be two "
                "of them."
            ),
        )
    return None


def _preflight(run: MergeRun) -> KnowledgeRefusal | None:
    """Compare every input against the generation the inputs agree on, before any session exists.

    Requirement 6.1 runs first: each input's declared generation is read, and when the inputs
    disagree the refusal happens here -- before a session exists, with no input migrated and no
    input's generation chosen as the winner. When they agree, that agreed generation is recorded on
    the run as the operation's selected generation, and everything downstream uses it.

    Requirement 6.2 then compares each input structurally against **that** generation rather than
    against the running build's. Leaving the live globals in place is the failure this exists to
    prevent: a v1/v1/v1 merge on the generation-2 build would be asked for ``route``,
    ``knowledge_record`` and ``record_revision`` and refused, which is requirement 5.1 broken by the
    mechanism meant to serve it.

    The comparison is against the selected generation rather than input against input, and that is
    the stronger check rather than a weaker one: every accepted input is structurally identical to
    the one generation, so two accepted inputs cannot disagree with each other. A separate pairwise
    structural pass would be unreachable code describing an enforcement no caller performs -- the
    pairwise clause requirement 6.1 restores is the *generation* comparison above.
    """

    selected = selected_generation(run.paths(), MERGE_OPERATION)
    if isinstance(selected, KnowledgeRefusal):
        return selected
    run.generation = selected
    for role in ("base", "left", "right"):
        deny = require_supported_structure(
            run.paths()[role],
            MERGE_OPERATION,
            role=role,
            generation=selected,
        )
        if deny is not None:
            return deny
    return None


def _build_covered_deltas(run: MergeRun) -> KnowledgeRefusal | None:
    """Produce one delta at a time, replay it, and record the coverage it proved."""

    databases = run.paths()
    selected = run.selected_generation()
    for side, replay_name in (("left", REPLAY_LEFT_NAME), ("right", REPLAY_RIGHT_NAME)):
        delta = build_delta(databases[side], databases["base"], side=side, generation=selected)
        run.deltas[side] = delta
        difference = _coverage_refusal(delta, databases, selected)
        if difference is not None:
            run.coverage.append(_coverage_of(delta, replayed=None, generation=selected))
            return difference
        replayed, denial = replay_delta(delta, run.workspace / replay_name, MERGE_OPERATION)
        run.coverage.append(_coverage_of(delta, replayed=replayed, generation=selected))
        if denial is not None:
            return denial
    return None


def _coverage_refusal(
    delta: Delta, databases: dict[str, Path], generation: SchemaGeneration
) -> KnowledgeRefusal | None:
    """Refuse a delta whose operation coverage disagrees with the rows the side changed.

    A table whose rows differ between the base and the side must contribute at least one operation.
    When it contributes none, the changeset omitted it -- the silent-omission class -- and the
    operation refuses here rather than discovering it later as an unexplained unequal dataset.
    """

    changed = _changed_tables(databases["base"], databases[delta.side], generation)
    omitted = [table for table in changed if delta.operation_counts[table] == 0]
    if omitted:
        return changeset_incomplete_refusal(
            MERGE_OPERATION,
            f"the {delta.side} changeset carries no operation for a table whose rows changed",
            table=omitted[0],
            expected=f"operations for {len(changed)} changed table(s)",
            observed=f"operations for {len(changed) - len(omitted)} changed table(s)",
        )
    return None


def _changed_tables(base: Path, side: Path, generation: SchemaGeneration) -> tuple[str, ...]:
    """Return the tables whose rows differ between two datasets, read from the datasets.

    The table set and the column order come from the **selected generation**, not from the build's
    pinned generation-1 manifest: a generation-2 merge whose comparison looped generation 1's ten
    tables would omit the tables generation 2 appends, so a change in one of them would go unseen
    and the coverage would report a table set the merge never actually compared.
    """

    base_reader = _open(base)
    side_reader = _open(side)
    try:
        changed: list[str] = []
        for table in generation.tables:
            columns = ", ".join(generation.columns[table])
            base_rows = sorted(
                tuple(row) for row in base_reader.execute(f"SELECT {columns} FROM {table}")
            )
            side_rows = sorted(
                tuple(row) for row in side_reader.execute(f"SELECT {columns} FROM {table}")
            )
            if base_rows != side_rows:
                changed.append(table)
        return tuple(changed)
    finally:
        base_reader.close()
        side_reader.close()


def _coverage_of(
    delta: Delta, *, replayed: str | None, generation: SchemaGeneration
) -> MergeCoverage:
    """Build the coverage fact for one delta, with or without a successful replay."""

    changed = delta.changed_tables
    tables = tuple(
        TableCoverage(
            table=table,
            table_changed=table in changed,
            operations=delta.operation_counts[table],
            replayed=replayed is not None,
        )
        for table in generation.tables
    )
    return MergeCoverage(
        side=delta.side,  # type: ignore[arg-type]
        source_digest=dataset_identity(delta.side_path).logical_digest,
        base_digest=dataset_identity(delta.base_path).logical_digest,
        changeset_digest=delta.changeset_digest,
        changeset_bytes=len(delta.changeset),
        replayed_digest=replayed if replayed is not None else delta.changeset_digest,
        operations=len(delta.operations),
        tables=tables,
    )


def _require_side_integrity(databases: dict[str, Path]) -> KnowledgeRefusal | None:
    """Require each side's sealed revisions to still be the aggregates the base sealed."""

    for side in ("left", "right"):
        deny = require_side_inputs_preserved(
            MERGE_OPERATION, base=databases["base"], side=databases[side], side_role=side
        )
        if deny is not None:
            return deny
    return None


def _conflict_refusal(applied: AppliedChangeset, delta: Delta) -> KnowledgeRefusal:
    """Map one application failure onto the merge's conflict taxonomy."""

    if not applied.conflicted:
        return changeset_incomplete_refusal(
            MERGE_OPERATION,
            f"the {delta.side} changeset could not be applied to the private target: "
            f"{applied.detail}",
        )
    code = int(applied.conflict_code or 0)
    table = applied.conflict_table or ""
    record_id = _conflict_record(applied)
    if code == _CONFLICT_DATA:
        return conflicting_values_refusal(
            MERGE_OPERATION,
            table=table,
            record_id=record_id,
            expected="the left side's stored value",
            observed="the right side's supplied value",
        )
    if code == _CONFLICT_FOREIGN_KEY:
        return delete_reference_conflict_refusal(
            MERGE_OPERATION,
            facts=RefusalFacts(
                table=table or None,
                record_id=record_id if table else None,
                expected="the row the right side's change refers to",
                observed="the row the left side removed",
            ),
        )
    builder = _TAXONOMY.get(_conflict_key(code, table))
    if builder is not None:
        return builder(table, record_id)
    return changeset_incomplete_refusal(
        MERGE_OPERATION,
        f"the changeset reported conflict code {code} for {table}, which the merge taxonomy "
        "does not name",
    )


def _recorded_row_refusal(table: str, record_id: str) -> KnowledgeRefusal:
    """Refuse a right-side change whose expected left-side row the target does not hold."""

    return refusal(
        "missing_expected_row",
        MERGE_OPERATION,
        f"the right changeset changed a {table} row the left side does not hold",
        facts=RefusalFacts(table=table, record_id=record_id),
        next_action=(
            "Resolve which side's row state is the intended one and merge the reconciled datasets "
            "again. No candidate was published."
        ),
    )


def _independent_insert_refusal(table: str, record_id: str) -> KnowledgeRefusal:
    """Refuse two independent insertions of one identity, equal payloads included."""

    return duplicate_identity_refusal(
        MERGE_OPERATION,
        table=table,
        record_id=record_id,
        expected="the left side's row",
        observed="the right side's independent insert under the same identity",
    )


def _relationship_constraint_refusal(table: str, record_id: str) -> KnowledgeRefusal:
    """Refuse an applied change that violates a declared constraint on a canonical table."""

    return changeset_postcondition_failed_refusal(
        MERGE_OPERATION,
        f"the applied change for {table} violates a declared constraint",
        facts=RefusalFacts(table=table, record_id=record_id),
    )


def _relationship_duplicate_refusal(table: str, record_id: str) -> KnowledgeRefusal:
    """Refuse a union that declares one unique relationship under two identities."""

    return duplicate_relationship_refusal(
        MERGE_OPERATION,
        table=table,
        record_id=record_id,
        expected="the relationship the left side declares",
        observed="the same declared relationship under a second identity",
    )


# The conflict taxonomy as data: one entry per (SQLite code, whether the conflict is the
# relationship-constraint one) pair, so the mapping is readable in one place instead of as a branch
# ladder.
_TAXONOMY: dict[tuple[int, bool], Callable[[str, str], KnowledgeRefusal]] = {
    (_CONFLICT_NOTFOUND, False): _recorded_row_refusal,
    (_CONFLICT_CONFLICT, False): _independent_insert_refusal,
    (_CONFLICT_CONSTRAINT, True): _relationship_duplicate_refusal,
    (_CONFLICT_CONSTRAINT, False): _relationship_constraint_refusal,
}

# What this package calls each SQLite conflict code, for the typed conflict record.
_CONFLICT_NAMES: dict[tuple[int, bool], str] = {
    (_CONFLICT_DATA, False): "conflicting_values",
    (_CONFLICT_NOTFOUND, False): "missing_expected_row",
    (_CONFLICT_CONFLICT, False): "duplicate_identity",
    (_CONFLICT_CONSTRAINT, True): "duplicate_relationship",
    (_CONFLICT_CONSTRAINT, False): "relationship_constraint",
    (_CONFLICT_FOREIGN_KEY, False): "delete_reference_conflict",
}


def _conflict_key(code: int, table: str) -> tuple[int, bool]:
    """The taxonomy key for one conflict: its SQLite code, and whether it is the relationship one.

    **One predicate, because there are two lookups over it.** ``_TAXONOMY`` selects the refusal
    builder and ``_CONFLICT_NAMES`` selects the name the operator reads, and both tables are keyed by
    this pair -- so asking the question differently at the two call sites made them disagree about
    what one conflict *is*. They did: the builder asked
    ``code == _CONFLICT_CONSTRAINT and table in _RELATIONSHIP_TABLES`` while the name lookup asked
    ``table in _RELATIONSHIP_TABLES`` alone. ``realization_claim`` is a relationship table, so a
    ``duplicate_identity`` on it was refused as ``duplicate_identity`` and simultaneously *named*
    ``unmapped_conflict_3`` -- the operator was told the taxonomy does not name a conflict it maps,
    which is a false statement about the tool rather than a fact about the data.

    The pair is what it is because the relationship answer exists only for the constraint code: a
    unique-declaration violation is ``duplicate_relationship``, while the same table colliding on a
    primary key is two independent insertions of one identity like any other table's.
    """

    return (code, code == _CONFLICT_CONSTRAINT and table in _RELATIONSHIP_TABLES)


def _conflict_name(code: int, table: str) -> str:
    """The name one conflict is reported under, or the explicit fallback when nothing names it."""

    return _CONFLICT_NAMES.get(_conflict_key(code, table), f"unmapped_conflict_{code}")


def _conflict_record(applied: AppliedChangeset) -> str:
    """Return the exact key of the row the engine refused, as the callback copied it out.

    The key is the one the engine handed the conflict callback, not one searched for in the
    changeset. Those are different facts: the first operation a changeset carries for a table is not
    necessarily the operation that conflicted, and reconstructing a key from the changeset would
    report a row the engine never refused.
    """

    if applied.conflict_key is None:
        return KEY_NOT_SUPPLIED
    return "/".join(applied.conflict_key)


def _conflict_facts(
    applied: AppliedChangeset,
    delta: Delta,
    *,
    precondition: RetractionPrecondition = "arriving_insertion",
) -> MergeConflict:
    """Build the conflict record a caller can read, from the facts that actually exist.

    ``delta`` is not consulted: the operation the engine refused is the one the callback held, and a
    changeset search would name a different operation whenever a table carries more than one. The
    one fact a delta cannot answer is the retraction precondition, which is why the caller that ran
    the retraction supplies it.
    """

    del delta
    code = int(applied.conflict_code or 0)
    table = applied.conflict_table
    if table is None or applied.conflict_operation is None:
        return MergeConflict(
            code=_conflict_name(code, ""),
            attribution="engine_reported_without_row",
            detail=(
                "the engine reported this conflict without a row: the conflict callback receives a "
                "change only for a row-level conflict, so no table, operation or key was supplied"
            ),
            precondition=precondition,
        )
    return MergeConflict(
        code=_conflict_name(code, table),
        attribution="engine_attributed",
        table=table,
        operation=applied.conflict_operation,
        record_id=(None if applied.conflict_key is None else "/".join(applied.conflict_key)),
        detail=(
            None
            if applied.conflict_key is not None
            else "the engine handed the callback a change whose key columns were not readable, so "
            "the row it refused is not identified"
        ),
    )


def _publish(run: MergeRun, merged_path: Path) -> MergeOutcome:
    """Install the merged candidate at the admitted destination, or report why not."""

    merged_identity = dataset_identity(merged_path)
    digests = {side: delta.changeset_digest for side, delta in sorted(run.deltas.items())}
    destination = run.request.destination
    if destination is None:
        return run.merged(merged_identity, digests)
    frozen = _freeze_merged(merged_path, merged_identity, run.workspace)
    if isinstance(frozen, KnowledgeRefusal):
        return run.refused(frozen)
    published = publish_prepared_snapshot(frozen, destination)
    if published.state == "refused":
        return run.refused(published.refusal)  # type: ignore[arg-type]
    return run.merged(
        merged_identity,
        digests,
        destination_ref=published.destination_ref,
        publication_state=published.state,
    )


def _freeze_merged(
    merged_path: Path, merged_identity: SnapshotIdentity, workspace: Path
) -> PreparedKnowledgeSnapshot | KnowledgeRefusal:
    """Freeze the merged temporary through the closed-snapshot procedure under its own lock.

    The merged temporary is a live database addressed the way every other dataset is: it has a
    resource lock of its own, and it is taken for the freeze and released before the destination
    lock is acquired. The frozen stage is then installed by the publication operation, which owns
    the destination lock -- so the two locks are sequential, never nested.

    **What the freeze contributes, and why no case can falsify it here.** The freeze exists so the
    published file is a complete database on its own: it copies the merged dataset through SQLite,
    normalises the copy's journal mode and re-verifies the closed result. Under this schema that
    contribution is invisible to a case. The merged temporary is a byte copy of the left side, which
    is a closed snapshot with no journal dependency of its own, and the only writer that touches it
    is one changeset application -- so a published file installed *without* the freeze would still
    have no journal peers and would still report the ``delete`` mode, which is exactly what the
    publication node asserts. The one difference the freeze does make is physical: the published
    bytes are the frozen stage's, not the live temporary's. A case could assert that a run's
    published bytes differ from that run's own live temporary, and it deliberately does not:
    physical page layout is not knowledge in this package, the two files agree in size, page count,
    journal mode and logical identity, and an assertion whose only signal is layout would be
    asserting SQLite's backup behaviour rather than the durability property the publication contract
    actually claims. Removing this call is therefore a non-experiment, and it is recorded as one
    rather than presented as a killed mutation.
    """

    stage = workspace / _FROZEN_STAGE_NAME
    store = open_existing_knowledge_store(merged_path, merged_identity.repository_id)
    try:
        try:
            with exclusive_file_lock(store.resource_lock_path, "knowledge merged candidate"):
                return freeze_closed_snapshot(store, merged_identity, stage)
        except LockCapabilityError as error:
            return lock_capability_refusal(MERGE_OPERATION, str(error))
    finally:
        store.connection.close()


def _open(path: Path) -> apsw.Connection:
    """Open one dataset read-only for a comparison."""

    return open_read_only_database(path)
