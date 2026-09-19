"""The curator's reachable ingest: one hand-off list becomes one admitted batch and one report.

:mod:`agents_remember.application.knowledge_ingest` is the write half -- one resolved entry, one
command list, one batch. This module is the layer above it, and it is the operation the leaf's
objective names: the curator receives the orchestrator's hand-off list and commits it. It admits
the destination, completes each target path, reads the identity each path really holds, verifies
the locator against the bytes at that path, observes each anchor against the recorded tree, authors
the governing routes, commits the whole list through the closed write path, and reports every
entry's outcome so the run is auditable afterwards.

**Three cases, never conflated.** A ruling that carries no target (``target: []``) is **skipped**,
and the report carries the producer's own ``disposition`` and ``disposition_source`` so the ruling
is not lost: there is no construct to cite, so there is no claim about code to record, and
recording one would invent a claim the producer never made. A non-empty target that does not resolve
is **refused**, with the exact reason, because the producer named a place the curator cannot verify.
``CuratorEntry.citations`` may be empty -- that is the write module's documented "authored
obligation recorded with no realization" -- and this operation never reaches that path by accident:
an entry is committed only when every one of its targets resolved, verified and observed, so an
empty citation list here arrives only from an entry with no targets, which is the skipped case and
is not committed at all.

**Identity is derived, not random.** Every identity this module mints is a ``uuid5`` under one fixed
namespace over the enclosure's identity (its recorded code base commit, which is what distinguishes
one leaf's enclosure from another) joined with the entry's own ``id`` from the hand-off list -- and,
for a target, the path it names, because a target carries no identity of its own in revision 1. Two
runs of the same list therefore mint the same ids, and the ids are real UUIDs because the write path
stores UUID-shaped identities.

**The route leg does not travel through the batch, and the report says so.** The closed command
union's only route member is ``SetFamilyRevisionRoute``, which names a *family revision*. An entry
here is an invariant beside source anchors; authoring a family per entry would fabricate grouping
structure the producer never declared, which is the one thing the curator must not do. The route is
therefore recorded through the primitives that reach a governed anchor -- ``author_route``
(idempotent by path: an existing route's id is returned and no second row is written) and
``set_governing_route`` (at most one governing route per governed row; re-stating it is idempotent
and a different route is refused rather than overwritten) -- and the two legs are reported
separately instead of folded into one success. A route that cannot be recorded refuses the list by
name, before anything is committed.

**What this module does not do.** It adds no symbol extractor: a symbol locator is carried and
observed, and the rail answers ``unsupported_locator`` for it, which is reported as that answer and
never as a file resolution. It does not touch onboarding, build an export, or widen the citation
machinery to a third root -- a path outside the two admitted roots is refused as out of scope by
name. Publication is the curator's later act and is not reachable from here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from agents_remember.application.knowledge import (
    open_admitted_knowledge_store,
    write_authorship,
)
from agents_remember.application.knowledge_ingest import (
    CuratorCitation,
    CuratorEntry,
    commit_curator_entries,
    curator_entry_commands,
)
from agents_remember.application.knowledge_snapshot import (
    admitted_candidate_destination,
    candidate_write_destination,
    create_knowledge_candidate,
    open_knowledge_candidate,
)
from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge import routes
from agents_remember.memory.knowledge.read_anchors import observe_anchor
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.memory_quality.style.citations.resolution import Trees
from agents_remember.models.knowledge.candidate import CandidateResolution, MutationResult
from agents_remember.models.knowledge.context import AdmittedKnowledgeDestination
from agents_remember.models.knowledge.graph import RealizationRole
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.snapshot import (
    CANDIDATE_RECEIPT_NAME,
    CandidateResult,
)
from agents_remember.models.knowledge.source import (
    FileLocator,
    GitBlobIdentity,
    LineRangeLocator,
    SourceAnchorDraft,
    SourceLocator,
    SymbolLocator,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

__all__ = [
    "COMMITTED",
    "REFUSED",
    "SKIPPED",
    "EntryOutcome",
    "IngestCounts",
    "IngestReport",
    "RouteOutcome",
    "TargetOutcome",
    "ingest_curator_list",
]

# The three per-entry outcomes, spelled once so a reader of the report and a caller branching on
# it name the same three words, and so "skipped" can never be read as a soft refusal.
COMMITTED = "committed"
REFUSED = "refused"
SKIPPED = "skipped"

# The lane this increment may write. ``task-candidate`` is refused by the write path until the
# resolved task binding the packet requires exists, so the ingest admits the draft lane only rather
# than accepting a lane parameter a caller could use to ask for the other one.
_CANDIDATE_LANE = "draft-candidate"

# The one place a name for a handed-over list is minted. Fixed forever: it is the namespace every
# derived identity is stable within, so changing it would silently re-mint each entry's identity.
_INGEST_NAMESPACE = UUID("6f2a1c94-6b0d-5e77-9a41-2d1f8c3b7e50")

# The completion steps, in the order the resolver answers them. Each is a name the report prints,
# so "which root answered" is never inferred from a path's spelling.
_CODE_TREE = "code-tree"
_MEMORY_ONBOARDING = "memory-onboarding"
_MEMORY_ROOT = "memory-root"

# The exact refusal reasons of step 2. The three are distinct facts, not three spellings of
# "unresolved": a task-tree path exists outside the two admitted roots, a dependency's source is
# absent by construction, and a real top-level entry whose file is gone is the damage a move did.
_REASON_THIRD_ROOT = "third_root_out_of_scope"
_REASON_DEPENDENCY = "dependency_source_not_ours"
_REASON_GONE = "top_level_entry_file_gone"

# The locator kinds, and the one the rail refuses by name. D-41 is the whole of the distinction:
# the rail verifies bytes, and a symbol is carried and honestly refused rather than resolved.
_FILE_KIND = "file"
_RANGE_KIND = "line_range"
_SYMBOL_KIND = "symbol"
_UNSUPPORTED_LOCATOR = "unsupported_locator"

# The observation rail's own answers, as the report prints them. ``exact_recorded_blob`` is the only
# passing answer for a locator the rail can consult; ``unsupported_locator`` passes only for a
# symbol.
_OBSERVED_EXACT = "exact_recorded_blob"
_OBSERVED_UNSUPPORTED = "unsupported_locator"
_OBSERVED_MISMATCH = "recorded_blob_mismatch"
_OBSERVED_ABSENT = "path_absent"
_OBSERVED_UNAVAILABLE = "recorded_object_unavailable"

# The one shape an empty target is reported under. Revision 1 describes the honest encoding as
# ``kind: "decision" | "measurement"``, and the fixture's eight rulings measure that the producer's
# hand is wider than that description: two are ``decision`` and six are ``finding`` or
# ``carried-defect``, each with a verdict that applies nowhere ("nothing to do", "not-code; repaired
# on the frozen tree after the barrier"). An empty target is therefore read as the ruling it is,
# whatever its kind, and the kind travels into the report unchanged rather than gating the read --
# refusing a ruling for wearing the wrong kind would lose the verdict the producer authored.
_RULING_SHAPE = "empty_target_ruling"

# The route-leg states. ``ungoverned`` is a fact and not a default: revision 1 permits an absent
# ``governing_route``, and a route invented to fill the field would be scope the producer never
# declared.
_ROUTE_AUTHORED = "authored"
_ROUTE_REUSED = "reused"
_ROUTE_ATTACHED = "attached"
_ROUTE_UNATTACHED = "authored-not-attached"
_ROUTE_UNGOVERNED = "ungoverned"
_ROUTE_REFUSED = "refused"

# The words the construct check uses to answer "is this name defined in the recorded bytes". The
# check confirms the named file defines the construct; it is never a hint that a range relocated, and
# it never produces an extent the producer did not write.
_PYTHON_DEFINITION = r"^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+{name}\b"
_DECLARED_DEFINITION = (
    r"(?:\b(?:function|def|class|interface|type|const|let|var|enum|struct|trait)[ \t]+"
    r"{name}\b|^[ \t]*{name}\b)"
)
_LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".md": "markdown",
    ".toml": "toml",
    ".json": "json",
    ".sh": "shell",
    ".sql": "sql",
}

# The revision and the one authored field every ingested entry is filed under. The hand-off list
# carries neither, so the ingest states what it authored rather than leaving a reader to infer it.
_INGESTED_VERSION = "v1"
_APPLICABILITY = "Every curator entry ingested from the orchestrator's hand-off list."

# How a blank per-target refusal reads in the report. A target that committed carries no refusal,
# and the empty string would look like one.
_NO_REFUSAL = ""

# The batch state a dry run reports. It is deliberately not "not_attempted", which reads as an
# operation that failed to reach its batch: a dry run reached every step and wrote nothing, and the
# state says exactly that. The entry outcomes and the counts beside it are what the batch *would*
# carry, so the dry report is the run's report with the commit withheld rather than a different one.
_DRY_BATCH_STATE = "dry_run_not_committed"


@dataclass(frozen=True)
class TargetOutcome:
    """What happened to one handed-over target: its path, identity, locator, observation, route."""

    path: str
    step: str | None
    completed_path: str | None
    source_identity: str | None
    locator_kind: str | None
    locator: str | None
    observation: str | None
    route_path: str | None
    route_state: str | None
    refusal: str = ""


@dataclass(frozen=True)
class RouteOutcome:
    """What happened to one governing route: the path, the route's id, and which leg answered."""

    route_path: str | None
    route_id: str | None
    state: str
    refusal: str = ""


@dataclass(frozen=True)
class EntryOutcome:
    """One entry's whole outcome: its ruling, or its targets and the route that governs them."""

    entry_id: str
    kind: str
    disposition: str
    disposition_source: str | None
    state: str
    targets: tuple[TargetOutcome, ...] = ()
    routes: tuple[RouteOutcome, ...] = ()
    refusal: str = ""


@dataclass(frozen=True)
class IngestCounts:
    """The counts that make one run auditable, each a fact about this module's own work."""

    entries_read: int
    rulings: int
    targets_completed: int
    locators_resolved: int
    anchors_observed_exact: int
    anchors_observed_unsupported: int
    anchors_observed_mismatch: int
    anchors_observed_absent: int
    anchors_observed_unavailable: int
    route_paths: int
    routes_authored: int
    routes_reused: int
    commands_sent: int
    records_written: int


@dataclass(frozen=True)
class IngestReport:
    """The ingest's whole result: the destination it used, the batch's state, every outcome.

    ``committed``, ``rulings`` and ``refused`` are the three cases that must never merge: an entry
    appears in exactly one of them. ``batch_refusal`` is the only refusal here that is not per
    entry; it is the batch's own typed refusal when the batch itself refused, and every planned
    entry then appears in ``refused`` with that code, because the operation is all-or-nothing.
    """

    contract_path: str
    candidate_directory: str
    candidate_receipt: str | None
    lane: str
    code_tree_id: str
    memory_tree_id: str
    repository_id: str
    derived_identities: str
    dry_run: bool
    entries_read: tuple[str, ...]
    committed: tuple[EntryOutcome, ...]
    rulings: tuple[EntryOutcome, ...]
    refused: tuple[EntryOutcome, ...]
    counts: IngestCounts
    batch_state: str
    batch_digest_before: str | None
    batch_digest_after: str | None
    batch_refusal: KnowledgeRefusal | None


@dataclass(frozen=True)
class _Plan:
    """One entry as the ingest read it, before anything was written."""

    entry_id: str
    kind: str
    disposition: str
    disposition_source: str | None
    statement: str
    evidence: str
    invariant_id: str
    revision_id: str
    targets: tuple[_TargetPlan, ...]
    ruling: bool


@dataclass(frozen=True)
class _TargetPlan:
    """One resolved target: the step that answered, the blob it holds, the anchor to write."""

    entry_id: str
    path: str
    step: str
    completed_path: str
    blob: str
    locator: SourceLocator
    observation: str
    route_path: str | None
    route_id: str
    anchor_id: UUID
    claim_id: str

    @property
    def anchor(self) -> SourceAnchorDraft:
        """The exact draft the write path stores: path, tree-read identity and the locator."""

        return SourceAnchorDraft(
            anchor_id=self.anchor_id,
            path=self.completed_path,
            source_identity=GitBlobIdentity(object_id=self.blob),
            locator=self.locator,
        )

    def citation(self) -> CuratorCitation:
        """The anchor plus the claim citing it, as the write module's command list expects."""

        return CuratorCitation(
            anchor=self.anchor,
            claim_id=self.claim_id,
            role=_role_for(self.locator),
            rationale=f"The statement is realized at {self.completed_path}.",
        )


@dataclass(frozen=True)
class _TreeIds:
    """The two recorded tree object ids the resolution names and the resolver is bound to."""

    code: str
    memory: str


@dataclass(frozen=True)
class _Paths:
    """The two local inputs the operation names: the enclosure contract and the candidate."""

    contract_path: Path
    candidate: Path


@dataclass(frozen=True)
class _Resolved:
    """One completed target: which root answered, the confined path, and the blob held there."""

    step: str
    path: str
    blob: str
    root: Path
    tree_id: str


@dataclass(frozen=True)
class _EntryFields:
    """The producer-side fields one hand-off entry carries, read once and never re-parsed."""

    entry_id: str
    kind: str
    disposition: str
    disposition_source: str | None
    statement: str
    evidence: str

    @classmethod
    def read(cls, raw: Mapping[str, Any]) -> _EntryFields:
        source = raw.get("disposition_source")
        return cls(
            entry_id=str(raw["id"]),
            kind=str(raw.get("kind", "")),
            disposition=str(raw.get("disposition", "")),
            disposition_source=None if source is None else str(source),
            statement=str(raw.get("statement", "")),
            evidence=str(raw.get("evidence", "")),
        )


@dataclass(frozen=True)
class _Refusal:
    """One refusal before the batch: its exact code, the reason, and what was read before it.

    ``targets`` carries the places this entry's targets did resolve, so the report's entry count and
    its target count describe the same run: an entry refused at its second target still shows the
    first one, rather than the refusal erasing what the read had already established.
    """

    code: str
    reason: str
    targets: tuple[TargetOutcome, ...] = ()

    def at(self, path: str) -> _Refusal:
        return _Refusal(self.code, f"{path}: {self.reason}", self.targets)

    def with_target(self, target: _TargetPlan) -> _Refusal:
        return _Refusal(self.code, self.reason, (*self.targets, _refused_target(target)))

    def entry(self, fields: _EntryFields) -> EntryOutcome:
        outcome = _refused_entry(fields, self.code, self.reason)
        return EntryOutcome(
            entry_id=outcome.entry_id,
            kind=outcome.kind,
            disposition=outcome.disposition,
            disposition_source=outcome.disposition_source,
            state=outcome.state,
            targets=self.targets,
            refusal=outcome.refusal,
        )


def _refused_target(target: _TargetPlan) -> TargetOutcome:
    """One target that resolved and observed cleanly before its entry was refused elsewhere."""

    return TargetOutcome(
        path=target.path,
        step=target.step,
        completed_path=target.completed_path,
        source_identity=target.blob,
        locator_kind=target.locator.kind,
        locator=_locator_text(target.locator),
        observation=target.observation,
        route_path=target.route_path,
        route_state=None,
    )


@dataclass
class _RouteLedger:
    """Which route paths one run touched: what it asked for, and what the candidate answered.

    ``expected`` maps a route path to the id this run derived for it; ``answered`` maps it to the id
    the candidate actually holds. The two differ exactly when the path already had a row, which is
    how "authored" and "reused" stay distinguishable rather than being assumed.
    """

    expected: dict[str, str]
    answered: dict[str, str]
    attached: dict[str, tuple[RouteOutcome, ...]]

    @property
    def reused_paths(self) -> frozenset[str]:
        return frozenset(
            path for path, route_id in self.answered.items() if self.expected.get(path) != route_id
        )


@dataclass(frozen=True)
class _Read:
    """Everything reading the list produced, so a later step names one value instead of six.

    ``rulings``, ``refused`` and ``planned`` are the three cases kept apart all the way through the
    operation: an entry is in exactly one of them, so no later step is handed a list that already
    merged two of the three.
    """

    ids: tuple[str, ...]
    rulings: tuple[EntryOutcome, ...]
    refused: tuple[EntryOutcome, ...]
    planned: tuple[_Plan, ...]

    @property
    def targets(self) -> tuple[_TargetPlan, ...]:
        return tuple(one for plan in self.planned for one in plan.targets)


@dataclass(frozen=True)
class _ReportTarget:
    """The four things one report names: the two local paths, the resolution, and the read."""

    paths: _Paths
    resolution: CandidateResolution
    repository: RepositoryIdentity
    read: _Read


@dataclass(frozen=True)
class _Source:
    """The enclosure's two roots and its recorded trees, bound once so planning names one value."""

    contract: WorktreeContract
    code_root: Path
    memory_root: Path
    tree_ids: _TreeIds


# --------------------------------------------------------------------------------------------
# The operation
# --------------------------------------------------------------------------------------------


def ingest_curator_list(
    contract_path: str | Path,
    entries: Sequence[Mapping[str, Any]] | str | Path,
    *,
    candidate_directory: str | Path,
    authorization_ref: str,
    dry_run: bool = False,
) -> IngestReport:
    """Commit one hand-off list into the enclosure's candidate, or report why it was not.

    ``entries`` is the hand-off list in revision 1's shape -- the parsed list, or the path of the
    file carrying it. ``candidate_directory`` is named by the caller and echoed in the report: a
    durable convention for it belongs in the memory layer's settings, not in this operation.
    ``authorization_ref`` is required because an admitted write needs one: ``Authorship`` refuses a
    blank reference, so a default here would build an envelope that cannot validate. It is also the
    actor the envelope names -- one required reference rather than two optional ones, which is how
    "who authorized this" and "who authored it" stay the same admitted fact instead of two strings
    that can disagree.
    ``dry_run=True`` returns the identical report having written nothing, and is the only mode that
    may run without the developer's commit word.
    """

    _require_authorization(authorization_ref)
    paths = _Paths(contract_path=Path(contract_path), candidate=Path(candidate_directory))
    contract = load_contract(paths.contract_path)
    code_root, memory_root = _roots(contract)
    source = _Source(
        contract=contract,
        code_root=code_root,
        memory_root=memory_root,
        tree_ids=_tree_ids(contract, code_root, memory_root),
    )
    repository = _repository_identity(contract)
    resolution = _resolution(contract, source.tree_ids)
    raw = _read_entries(entries)
    plans, refused = _plan_entries(raw, source)
    read = _Read(
        ids=tuple(str(one["id"]) for one in raw),
        rulings=tuple(_ruling_outcome(plan) for plan in plans if plan.ruling),
        refused=refused,
        planned=tuple(plan for plan in plans if not plan.ruling),
    )
    if dry_run:
        return _report(
            _ReportTarget(paths, resolution, repository, read),
            _projected(read.planned),
            committed=_projected_outcomes(read.planned),
            dry_run=True,
        )
    authorship = write_authorship(
        actor_ref=authorization_ref,
        authorization_ref=authorization_ref,
        origin_refs=("curator-handoff:revision-1",),
    )
    admission = _admitted_candidate(paths.candidate, repository, resolution)
    if admission.state == "refused" or admission.result.identity is None:
        return _report(
            _ReportTarget(paths, resolution, repository, read),
            _Run(batch_state="not_attempted", refusal=admission.refusal),
        )
    destination = candidate_write_destination(
        admitted_candidate_destination(paths.candidate, repository, resolution), authorship
    )
    return _run(paths, destination, resolution, repository, read)


def _run(
    paths: _Paths,
    destination: AdmittedKnowledgeDestination,
    resolution: CandidateResolution,
    repository: RepositoryIdentity,
    read: _Read,
) -> IngestReport:
    """Author the routes, commit the one batch, attach the routes, and report every outcome."""

    ledger = _RouteLedger(expected=_distinct_routes(read.planned), answered={}, attached={})
    route_refusals = _author_routes(destination, repository, ledger, read.planned)
    if route_refusals:
        return _report(
            _ReportTarget(paths, resolution, repository, _with_refused(read, route_refusals)),
            _Run(batch_state="not_attempted"),
        )
    result = _commit(destination, resolution, read.planned)
    if result is None or result.state == "refused":
        return _report(
            _ReportTarget(
                paths,
                resolution,
                repository,
                _with_refused(read, _batch_refused(read.planned, result)),
            ),
            _Run(result=result, ledger=ledger),
        )
    _attach_routes(destination, repository, read.planned, ledger)
    committed = tuple(
        _committed_outcome(plan, ledger.attached.get(plan.entry_id, ())) for plan in read.planned
    )
    commands = sum(
        len(curator_entry_commands(destination, _curator_entry(plan))) for plan in read.planned
    )
    return _report(
        _ReportTarget(paths, resolution, repository, read),
        _Run(result=result, commands=commands, ledger=ledger),
        committed=committed,
    )


def _with_refused(read: _Read, extra: tuple[EntryOutcome, ...]) -> _Read:
    """The same read with more refusals, so a report always sees one value."""

    return _Read(
        ids=read.ids,
        rulings=read.rulings,
        refused=(*read.refused, *extra),
        planned=read.planned,
    )


def _require_authorization(authorization_ref: str) -> None:
    """Refuse a blank authorization by name, rather than letting the envelope fail to build."""

    if not authorization_ref.strip():
        raise ValueError(
            "an admitted write needs the authorization it is made under, so authorization_ref "
            "must not be blank"
        )


# --------------------------------------------------------------------------------------------
# Admission: the candidate directory, its receipt, and the identity the enclosure derives
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Admission:
    """One candidate directory's admission: created, resumed, or refused with the product's answer."""

    state: str
    result: CandidateResult

    @property
    def refusal(self) -> KnowledgeRefusal | None:
        return self.result.refusal


def _admitted_candidate(
    candidate: Path, repository: RepositoryIdentity, resolution: CandidateResolution
) -> _Admission:
    """Resume the candidate this admission names, or create it when its directory is absent.

    An existing destination is a resume attempt, never permission to initialize over it, so the
    product's own open answers first and the operation acts on that answer. A directory the open
    refused and that does exist is this operation's refusal too: the bytes there are unpublished
    authored work, and only an explicitly authorized reconciliation may touch them.
    """

    destination = admitted_candidate_destination(candidate, repository, resolution)
    if candidate.exists():
        opened = open_knowledge_candidate(destination)
        return _Admission(opened.state, opened)
    created = create_knowledge_candidate(destination)
    if created.state == "created":
        return _Admission("created", created)
    return _Admission("refused", created)


def _repository_identity(contract: WorktreeContract) -> RepositoryIdentity:
    """The namespace one enclosure's candidate is created under, derived from the enclosure.

    ``RepositoryIdentity`` is supplied on creation and read from the database afterwards, so it is
    derived once here rather than drawn: two runs of one enclosure must name one namespace, and the
    enclosure's recorded code base commit is the fact that distinguishes it from another's.
    """

    return RepositoryIdentity(
        repository_id=str(uuid5(_INGEST_NAMESPACE, f"repository:{_enclosure(contract)}")),
        authority_home=contract.repo_name,
    )


def _resolution(contract: WorktreeContract, tree_ids: _TreeIds) -> CandidateResolution:
    """The candidate inputs, read from the enclosure's recorded pair rather than asserted."""

    leaf = _enclosure(contract)
    return CandidateResolution(
        lane=_CANDIDATE_LANE,
        code_tree_id=tree_ids.code,
        memory_tree_id=tree_ids.memory,
        snapshot_ref=f"curator-ingest:{leaf}",
        candidate_ref=f"curator-ingest:{leaf}",
        code_commit_id=contract.code_base_commit,
        memory_commit_id=contract.memory_base_commit,
        task_ref=contract.leaf_id or contract.task_name,
    )


def _enclosure(contract: WorktreeContract) -> str:
    """The one string that identifies this enclosure for identity derivation.

    Its recorded code base commit, because that is the fact distinguishing one enclosure's base
    from another's and it is already recorded in the contract the operation was handed.
    """

    return contract.code_base_commit


def _roots(contract: WorktreeContract) -> tuple[Path, Path]:
    """The code worktree and the memory worktree the contract records, both required."""

    if contract.memory_worktree is None:
        raise ValueError(
            f"contract {contract.contract_path} records no memory worktree, so the two roots the "
            "citation machinery admits cannot both be named"
        )
    return contract.code_worktree, contract.memory_worktree


def _tree_ids(contract: WorktreeContract, code_root: Path, memory_root: Path) -> _TreeIds:
    """The tree object id each recorded base commit holds, read from the repository itself."""

    return _TreeIds(
        code=_tree_of(code_root, contract.code_base_commit, contract.contract_path),
        memory=_tree_of(memory_root, contract.memory_base_commit, contract.contract_path),
    )


def _tree_of(root: Path, commit: str, contract_path: Path) -> str:
    """One recorded commit's tree object id, or a named error for an input that cannot answer."""

    if not commit:
        raise ValueError(f"contract {contract_path} records no base commit for {root}")
    result = run_git(root, ["rev-parse", f"{commit}^{{tree}}"])
    if result.returncode != 0:
        raise ValueError(
            f"the recorded base commit {commit} is not available in {root}, so the tree the "
            "citation machinery resolves against cannot be named"
        )
    return result.stdout.strip()


# --------------------------------------------------------------------------------------------
# Step 1: the list as data
# --------------------------------------------------------------------------------------------


def _read_entries(
    entries: Sequence[Mapping[str, Any]] | str | Path,
) -> tuple[Mapping[str, Any], ...]:
    """The hand-off list as data: the parsed list itself, or the file carrying it.

    No prose is parsed. A caller that hands over a path hands over the same JSON a caller that hands
    over the list already holds, so the two spellings cannot disagree about what was read. The
    ``id`` is the curator's handle for an entry, so two entries claiming one id is an input error
    the operation refuses before it writes anything.
    """

    if isinstance(entries, (str, Path)):
        loaded = json.loads(Path(entries).read_text(encoding="utf-8"))
    else:
        loaded = list(entries)
    if not isinstance(loaded, list):
        raise ValueError("the hand-off list is a JSON list of entries")
    seen: set[str] = set()
    for one in loaded:
        if not isinstance(one, Mapping):
            raise ValueError("every hand-off entry is a JSON object")
        entry_id = str(one.get("id", ""))
        if not entry_id:
            raise ValueError("every hand-off entry carries its own id")
        if entry_id in seen:
            raise ValueError(f"two hand-off entries carry the id {entry_id!r}")
        seen.add(entry_id)
    return tuple(loaded)


def _plan_entries(
    entries: Sequence[Mapping[str, Any]], source: _Source
) -> tuple[tuple[_Plan, ...], tuple[EntryOutcome, ...]]:
    """Read every entry into a plan, keeping each entry's own refusals beside the plans."""

    plans: list[_Plan] = []
    refused: list[EntryOutcome] = []
    for raw in entries:
        plan, refusal = _plan_entry(raw, source)
        if refusal is not None:
            refused.append(refusal)
        elif plan is not None:
            plans.append(plan)
    return tuple(plans), tuple(refused)


def _plan_entry(
    raw: Mapping[str, Any],
    source: _Source,
) -> tuple[_Plan | None, EntryOutcome | None]:
    """Read one entry: its ruling, or its targets completed and each anchor observed."""

    fields = _EntryFields.read(raw)
    targets = list(raw.get("target") or [])
    if not targets:
        return _ruling_plan(fields), None
    planned: list[_TargetPlan] = []
    seen: set[str] = set()
    for target in targets:
        plan, refusal = _plan_target(fields.entry_id, target, source)
        if refusal is not None:
            # The refusal carries the places this entry's earlier targets already resolved, so an
            # entry refused at its second target still shows the first one: the report's entry
            # count and its target count then describe the same run.
            return None, _Refusal(
                refusal.code,
                refusal.reason,
                tuple(_refused_target(one) for one in planned),
            ).entry(fields)
        if plan is None:  # pragma: no cover - a plan and its refusal are exclusive
            continue
        if plan.completed_path in seen:
            return None, _Refusal(
                "duplicate_target_path",
                f"the entry names {plan.completed_path!r} twice, so one of the two places the "
                "producer named would be filed under the other's identity",
                tuple(_refused_target(one) for one in planned),
            ).entry(fields)
        seen.add(plan.completed_path)
        planned.append(plan)
    return (
        _Plan(
            entry_id=fields.entry_id,
            kind=fields.kind,
            disposition=fields.disposition,
            disposition_source=fields.disposition_source,
            statement=fields.statement,
            evidence=fields.evidence,
            invariant_id=_identity(source.contract, "invariant", fields.entry_id),
            revision_id=_identity(source.contract, "revision", fields.entry_id),
            targets=tuple(planned),
            ruling=False,
        ),
        None,
    )


def _ruling_plan(fields: _EntryFields) -> _Plan:
    """The empty-target case: a ruling that applies nowhere, carrying its own verdict.

    The entry is reported **skipped**, not refused and not committed: nothing failed, and there is
    no construct to cite, so there is no claim about code to record. ``CuratorEntry.citations`` may
    be empty, but reaching that path here would file a claim about code the producer never made --
    which is why the ruling is reported instead of committed as an unrealized obligation.
    """

    return _Plan(
        entry_id=fields.entry_id,
        kind=fields.kind,
        disposition=fields.disposition,
        disposition_source=fields.disposition_source,
        statement=fields.statement,
        evidence=fields.evidence,
        invariant_id="",
        revision_id="",
        targets=(),
        ruling=True,
    )


def _plan_target(
    entry_id: str,
    target: Mapping[str, Any],
    source: _Source,
) -> tuple[_TargetPlan | None, _Refusal | None]:
    """Complete one target's path, read its identity, verify its locator, and observe the anchor."""

    written = str(target.get("path", ""))
    resolved = _complete_target(
        written, source.code_root, source.memory_root, source.tree_ids, source.contract
    )
    if isinstance(resolved, _Refusal):
        return None, resolved
    locator = _locator(target.get("locator"), resolved)
    if isinstance(locator, _Refusal):
        return None, locator.at(written)
    observation = _observe(resolved, locator)
    if isinstance(observation, _Refusal):
        return None, observation.at(written)
    route_path = target.get("governing_route")
    return (
        _TargetPlan(
            entry_id=entry_id,
            path=written,
            step=resolved.step,
            completed_path=resolved.path,
            blob=resolved.blob,
            locator=locator,
            observation=observation,
            route_path=None if route_path is None else str(route_path),
            route_id=_identity(source.contract, f"route:{written}", entry_id),
            anchor_id=UUID(_identity(source.contract, f"anchor:{written}", entry_id)),
            claim_id=_identity(source.contract, f"claim:{written}", entry_id),
        ),
        None,
    )


# --------------------------------------------------------------------------------------------
# Steps 2 to 5: path completion, identity, locator in the same act, and observation
# --------------------------------------------------------------------------------------------


def _complete_target(
    written: str,
    code_root: Path,
    memory_root: Path,
    tree_ids: _TreeIds,
    contract: WorktreeContract,
) -> _Resolved | _Refusal:
    """Complete one path through the product's own resolver, in the three recorded steps.

    The steps are exactly three and nothing else: ``<code_root>/<path>`` through the recorded code
    tree, then ``<memory_root>/onboarding/<path>``, then ``<memory_root>/<path>``. Membership is the
    tree's answer and the working bytes are verified against it, and the answering root travels out
    with the path so the identity is read from that same root's tree -- a memory path's blob lives
    in the memory tree, and reading it out of the code tree is how a resolved path becomes a crash.

    A path that lands in none of the three is refused with the reason distinguishing the three
    measured cases: it exists outside the two roots, its first segment is no top-level entry of
    either root (a dependency's source, absent by construction), or it is a real top-level entry
    whose file is gone. The first-segment answer alone cannot tell the first from the third, so the
    coordination root is asked as well.
    """

    code_tree = Trees(code_root=code_root, memory_root=memory_root, candidate_tree=tree_ids.code)
    if code_tree.resolve(written) is not None and written in _members(code_tree):
        return _member(code_tree, written, _CODE_TREE, code_root, tree_ids.code)
    memory_tree = Trees(
        code_root=memory_root, memory_root=memory_root, candidate_tree=tree_ids.memory
    )
    for step, joined in ((_MEMORY_ONBOARDING, f"onboarding/{written}"), (_MEMORY_ROOT, written)):
        if joined in _members(memory_tree) and (memory_root / joined).is_file():
            return _member(memory_tree, joined, step, memory_root, tree_ids.memory)
    return _Refusal("target_path_unresolved", _unresolved_reason(written, code_tree, contract))


def _member(
    tree: Trees, relative: str, step: str, root: Path, tree_id: str
) -> _Resolved | _Refusal:
    """One completed target, with the blob read from the tree that answered for it.

    Membership is the tree's own answer, so a path that resolved but is no member of the tree that
    was asked about is refused here rather than read out of the wrong tree: an identity that does
    not belong to the tree the resolution names is exactly the disagreement this step exists to
    catch.
    """

    members = _members(tree)
    if relative not in members:
        return _Refusal(
            "target_path_unresolved",
            f"{relative!r} resolved in the {step} step and the recorded tree holds no member at "
            "that path, so the resolved path and the readable identity disagree",
        )
    return _Resolved(
        step=step, path=relative, blob=members[relative][1], root=root, tree_id=tree_id
    )


def _members(tree: Trees) -> Mapping[str, tuple[str, str]]:
    """One resolver's own membership answer, or nothing when no recorded tree is bound."""

    candidate = tree.source_candidate
    return {} if candidate is None else candidate.members


def _unresolved_reason(written: str, code_tree: Trees, contract: WorktreeContract) -> str:
    """Which of the three ways a path resolves to nothing this path took."""

    outside = _outside_the_roots(written, contract)
    if outside is not None:
        return (
            f"{_REASON_THIRD_ROOT}: {written!r} exists at {outside}, outside the two roots the "
            "citation machinery admits, so it is out of scope by design rather than unresolved"
        )
    if code_tree.ours(written):
        return (
            f"{_REASON_GONE}: {written!r} names a real top-level entry of one of the two roots and "
            "the recorded tree holds no file at that path, which is the damage a move or a deletion "
            "leaves behind"
        )
    return (
        f"{_REASON_DEPENDENCY}: the first segment of {written!r} is not a top-level entry of either "
        "admitted root, so it names a dependency's source, which is absent by construction"
    )


def _outside_the_roots(written: str, contract: WorktreeContract) -> str | None:
    """The coordination-tree file a path names, when it names one at all.

    The third root is not admitted, and denying that the file exists would be a false refusal: the
    honest answer is that it exists somewhere the citation machinery cannot reach. The attempt is
    made against the few places a hand-off entry actually cites -- the coordination root, the task
    root, and the reports and design trees inside the task root -- in the same spirit as the
    resolver's own first-segment probe: a small declared ladder of real roots rather than a scan of
    the filesystem, so "this path exists elsewhere" is cheap and cannot drift into a search.
    """

    for base in (
        contract.coordination_root,
        contract.task_root,
        contract.task_root / "notes" / "reports",
        contract.task_root / "notes" / "design",
    ):
        try:
            candidate = base / written
            if candidate.is_file():
                return str(candidate)
        except OSError:  # pragma: no cover - a base that cannot be joined names no file
            continue
    return None


def _locator(locator: Mapping[str, Any] | None, resolved: _Resolved) -> SourceLocator | _Refusal:
    """The producer's locator, verified against the bytes at the path it names.

    A locator is resolved in the same act as the path, so no second resolution can land somewhere
    else: the file consulted here is the file the resolver admitted. A line range is checked for
    containment and a symbol's written parts are checked to occur in the recorded bytes. Neither
    check invents an extent and neither searches for the construct anywhere else -- a construct that
    is not in the named file is a refusal with that reason, never a citation to wherever a search
    would have landed.
    """

    if locator is None:
        return FileLocator()
    kind = str(locator.get("kind", ""))
    if kind == _FILE_KIND:
        return FileLocator()
    if kind == _RANGE_KIND:
        return _range_locator(locator, resolved)
    if kind == _SYMBOL_KIND:
        return _symbol_locator(locator, resolved)
    return _Refusal(
        "unsupported_locator_kind",
        f"the locator kind {kind!r} is not one of file, line_range or symbol",
    )


def _range_locator(locator: Mapping[str, Any], resolved: _Resolved) -> SourceLocator | _Refusal:
    """A line range, refused when it lies outside the recorded bytes."""

    start = locator.get("start")
    end = locator.get("end")
    if not isinstance(start, int) or not isinstance(end, int):
        return _Refusal(
            "construct_not_in_named_file",
            "a line_range locator must carry integer start and end lines",
        )
    if start < 1 or end < 1 or end < start:
        return _Refusal(
            "construct_not_in_named_file",
            f"the recorded range {start}-{end} is not a one-based ordered range",
        )
    lines = len(_recorded_text(resolved).splitlines())
    if end > lines:
        return _Refusal(
            "construct_not_in_named_file",
            f"the recorded range {start}-{end} reaches past the last line of the recorded blob, "
            f"which holds {lines} lines",
        )
    return LineRangeLocator(start_line=start, end_line=end)


def _symbol_locator(locator: Mapping[str, Any], resolved: _Resolved) -> SourceLocator | _Refusal:
    """A symbol the producer wrote as a bare name, resolved into the model's two-part locator.

    The producers spell a symbol as ``{"kind": "symbol", "value": "<bare name>"}``, and the model
    wants a language and a qualified name. Determining those is curator work of the same kind as
    normalising the producer's ``line_range`` spelling: the language is *derived* from the recorded
    path's own extension, and the qualified name is the name the producer wrote. Nothing here is a
    parser and nothing here searches: the check reads the one recorded blob the path already
    resolved to and confirms the name occurs as a **definition** there, so a name that is merely
    mentioned elsewhere in the file is refused with ``construct_not_in_named_file`` rather than
    cited. Ingest-time verification is the only verification available -- the rail answers
    ``unsupported_locator`` for the symbol kind and cannot re-verify one -- and the report says so.
    """

    written = str(locator.get("value") or locator.get("qualified_name") or "").strip()
    if not written:
        return _Refusal(
            "construct_not_in_named_file", "a symbol locator must carry the name it names"
        )
    language = _language_of(resolved.path)
    if language is None:
        return _Refusal(
            "construct_not_in_named_file",
            f"the symbol {written!r} names no language and the recorded path {resolved.path!r} has "
            "no extension this ingest can derive one from, so the producer's spelling "
            "under-determines the locator",
        )
    text = _recorded_text(resolved)
    if not _is_defined(written, language, text):
        return _Refusal(
            "construct_not_in_named_file",
            f"the symbol {written!r} does not occur as a {language} definition in the recorded "
            "bytes at this path, so the construct is not in the named file",
        )
    return SymbolLocator(language=language, qualified_name=written)


def _language_of(path: str) -> str | None:
    """The language one recorded path's own extension states, or ``None`` when it states none.

    A derivation from a fact already in the target rather than a guess: every extension below is
    one the recorded repository ships, and an extension outside the table yields no language rather
    than a fabricated one.
    """

    suffix = Path(path).suffix.lower()
    return _LANGUAGES.get(suffix)


def _is_defined(name: str, language: str, text: str) -> bool:
    """Whether one name occurs as a definition in the recorded bytes.

    Python is checked against a ``def``/``class`` line, which is what makes "defined here" a
    measurably different claim from "mentioned here". The other languages this repository cites are
    checked with one identifier-boundary pattern covering their declaration forms -- ``function``,
    ``def``, ``class``, ``interface``, ``type``, ``const`` and ``let`` -- plus a bare declaration at
    the start of a line for a TypeScript type alias. A name that satisfies neither is refused, so
    the check errs toward refusing rather than toward citing: it can never report a symbol as
    resolved in a file that does not define it.
    """

    if language == "python":
        return bool(re.search(_PYTHON_DEFINITION.format(name=re.escape(name)), text, re.MULTILINE))
    return bool(re.search(_DECLARED_DEFINITION.format(name=re.escape(name)), text, re.MULTILINE))


def _recorded_text(resolved: _Resolved) -> str:
    """The recorded member's own bytes, read from the working file the tree verified."""

    return (resolved.root / resolved.path).read_text(encoding="utf-8", errors="replace")


def _observe(resolved: _Resolved, locator: SourceLocator) -> str | _Refusal:
    """Observe one anchor against the tree its path resolved in, or report the failing answer.

    The rail is handed exactly the draft the write path will store, so what the report says was
    observed is what the record will hold. ``exact_recorded_blob`` is required for every locator the
    rail can consult, and a symbol is accepted only on the rail's own ``unsupported_locator`` answer
    -- a symbol reported as a file resolution would be a resolution the recorded claim never made.
    """

    draft = SourceAnchorDraft(
        anchor_id=UUID(_observation_id(resolved, locator)),
        path=resolved.path,
        source_identity=GitBlobIdentity(object_id=resolved.blob),
        locator=locator,
    )
    observation = observe_anchor(
        {
            "anchor_id": str(draft.anchor_id),
            "path": draft.path,
            "source_identity": draft.source_identity.model_dump(mode="json"),
            "locator": locator.model_dump(mode="json"),
        },
        repository_root=resolved.root,
        tree_id=resolved.tree_id,
    )
    answer = observation.resolution
    if locator.kind == _SYMBOL_KIND:
        if answer == _UNSUPPORTED_LOCATOR:
            return answer
        return _Refusal(answer, observation.detail)
    if answer == _OBSERVED_EXACT:
        return answer
    return _Refusal(answer, observation.detail)


def _observation_id(resolved: _Resolved, locator: SourceLocator) -> str:
    """A well-formed anchor id for the observation, so the rail sees a real UUID."""

    return str(uuid5(_INGEST_NAMESPACE, f"observation:{resolved.path}|{locator.kind}"))


# --------------------------------------------------------------------------------------------
# Step 6: the governing route, through the primitives that reach a governed anchor
# --------------------------------------------------------------------------------------------


def _author_routes(
    destination: AdmittedKnowledgeDestination,
    repository: RepositoryIdentity,
    ledger: _RouteLedger,
    plans: tuple[_Plan, ...],
) -> tuple[EntryOutcome, ...]:
    """Author every distinct route the list names, before anything is committed.

    The route leg runs first because an entry whose route cannot be recorded is a refusal, and a
    refusal must not arrive after the batch has already committed that entry's citation. The leg is
    one transaction over the candidate -- ``author_route`` is idempotent by path, so the distinct
    scopes cost one row each however many places name them -- and a partial author is rolled back.
    An entry that names no route is not refused: absent is the explicit ungoverned state.
    """

    if not ledger.expected:
        return ()
    store = open_admitted_knowledge_store(destination)
    try:
        with store.exclusive_candidate_lock("author_route") as lock_refusal:
            if lock_refusal is not None:
                return _route_refusals(plans, lock_refusal)
            try:
                _author_route_rows(store, repository, ledger, destination)
            except _RouteRefused as refused:
                return _route_refusals(plans, refused.refusal)
    finally:
        store.close()
    return ()


def _distinct_routes(plans: tuple[_Plan, ...]) -> dict[str, str]:
    """Every distinct route path the list names, with the id this run derived for it."""

    wanted: dict[str, str] = {}
    for plan in plans:
        for target in plan.targets:
            if target.route_path is not None:
                wanted.setdefault(target.route_path, target.route_id)
    return wanted


def _author_route_rows(
    store: OpenedKnowledgeStore,
    repository: RepositoryIdentity,
    ledger: _RouteLedger,
    destination: AdmittedKnowledgeDestination,
) -> None:
    """Author the distinct routes in one transaction, refusing the whole leg if one refuses."""

    with store.immediate_transaction():
        for route_path, route_id in ledger.expected.items():
            answer = routes.author_route(
                store.connection,
                repository.repository_id,
                routes.RouteDraft(route_id=route_id, path=route_path),
                destination.authorship,
            )
            if isinstance(answer, KnowledgeRefusal):
                raise _RouteRefused(answer)
            ledger.answered[route_path] = answer


class _RouteRefused(Exception):
    """One route primitive's refusal, carried out of the transaction it refused in."""

    def __init__(self, refusal: KnowledgeRefusal) -> None:
        super().__init__(refusal.detail)
        self.refusal = refusal


def _route_refusals(
    plans: tuple[_Plan, ...], refusal: KnowledgeRefusal
) -> tuple[EntryOutcome, ...]:
    """One route leg's refusal, attributed to every entry that named a route."""

    return tuple(
        _refused_entry(
            _fields_of(plan),
            "route_not_recorded",
            f"the governing route could not be recorded ({refusal.code}): {refusal.detail}",
        )
        for plan in plans
        if any(target.route_path is not None for target in plan.targets)
    )


def _attach_routes(
    destination: AdmittedKnowledgeDestination,
    repository: RepositoryIdentity,
    plans: tuple[_Plan, ...],
    ledger: _RouteLedger,
) -> None:
    """Attach each committed anchor to its route, in one transaction over the candidate.

    The association is what makes a citation's third element recorded rather than inferred. It runs
    after the batch because the governed row is the anchor the batch wrote: the union carries no
    command that sets a route on an anchor, so the two legs cannot share one transaction, and the
    report names that rather than pretending one call did both.
    """

    if not ledger.answered:
        ledger.attached = {plan.entry_id: _ungoverned(plan) for plan in plans}
        return
    store = open_admitted_knowledge_store(destination)
    try:
        with store.exclusive_candidate_lock("set_governing_route") as lock_refusal:
            if lock_refusal is not None:
                ledger.attached = _unattached(plans, lock_refusal.detail)
                return
            _attach_rows(store, repository, plans, destination, ledger)
    finally:
        store.close()


def _attach_rows(
    store: OpenedKnowledgeStore,
    repository: RepositoryIdentity,
    plans: tuple[_Plan, ...],
    destination: AdmittedKnowledgeDestination,
    ledger: _RouteLedger,
) -> None:
    """Attach every governed anchor inside one transaction, reporting each target's own outcome."""

    with store.immediate_transaction():
        for plan in plans:
            outcomes: list[RouteOutcome] = []
            for target in plan.targets:
                outcomes.append(_attached_outcome(store, repository, target, destination, ledger))
            ledger.attached[plan.entry_id] = tuple(outcomes)


def _attached_outcome(
    store: OpenedKnowledgeStore,
    repository: RepositoryIdentity,
    target: _TargetPlan,
    destination: AdmittedKnowledgeDestination,
    ledger: _RouteLedger,
) -> RouteOutcome:
    """One target's route outcome: ungoverned, attached, or refused with the primitive's reason."""

    if target.route_path is None:
        return RouteOutcome(None, None, _ROUTE_UNGOVERNED)
    route_id = ledger.answered[target.route_path]
    answer = routes.set_governing_route(
        store.connection,
        repository.repository_id,
        routes.GoverningRouteDraft(
            governed_table="source_anchor",
            governed_id=str(target.anchor_id),
            route_id=route_id,
        ),
        destination.authorship,
    )
    if answer is None:
        state = _ROUTE_REUSED if target.route_path in ledger.reused_paths else _ROUTE_AUTHORED
        return RouteOutcome(target.route_path, route_id, state)
    return RouteOutcome(target.route_path, None, _ROUTE_REFUSED, f"{answer.code}: {answer.detail}")


def _ungoverned(plan: _Plan) -> tuple[RouteOutcome, ...]:
    """One entry with no route at all: the explicit ungoverned state, per target."""

    return tuple(
        RouteOutcome(None, None, _ROUTE_UNGOVERNED)
        if target.route_path is None
        else RouteOutcome(target.route_path, target.route_id, _ROUTE_UNATTACHED, "not attached")
        for target in plan.targets
    )


def _unattached(plans: tuple[_Plan, ...], detail: str) -> dict[str, tuple[RouteOutcome, ...]]:
    """The routes that were authored but whose association the candidate refused to take."""

    return {
        plan.entry_id: tuple(
            RouteOutcome(target.route_path, target.route_id, _ROUTE_UNATTACHED, detail)
            if target.route_path is not None
            else RouteOutcome(None, None, _ROUTE_UNGOVERNED)
            for target in plan.targets
        )
        for plan in plans
    }


# --------------------------------------------------------------------------------------------
# Steps 7 and 8: the whole list in one admitted batch
# --------------------------------------------------------------------------------------------


def _curator_entry(plan: _Plan) -> CuratorEntry:
    """One plan as the write module's entry: the invariant, its revision, and its citations."""

    return CuratorEntry(
        invariant_id=plan.invariant_id,
        display_label=plan.entry_id,
        revision_id=plan.revision_id,
        display_version=_INGESTED_VERSION,
        statement=plan.statement,
        applicability=_APPLICABILITY,
        conditions=_conditions(plan),
        citations=tuple(target.citation() for target in plan.targets),
    )


def _conditions(plan: _Plan) -> tuple[str, ...]:
    """The entry's own provenance, carried into the revision rather than left in the report."""

    conditions = [f"Hand-off kind: {plan.kind}.", f"Producer's disposition: {plan.disposition}."]
    if plan.disposition_source is not None:
        conditions.append(f"Disposition source: {plan.disposition_source}")
    if plan.evidence:
        conditions.append(f"Evidence: {plan.evidence}")
    return tuple(conditions)


def _commit(
    destination: AdmittedKnowledgeDestination,
    resolution: CandidateResolution,
    plans: tuple[_Plan, ...],
) -> MutationResult | None:
    """Commit every planned entry as one batch, which is the operation's own boundary."""

    if not plans:
        return None
    return commit_curator_entries(
        destination, resolution, tuple(_curator_entry(one) for one in plans)
    )


def _batch_refused(
    plans: tuple[_Plan, ...], result: MutationResult | None
) -> tuple[EntryOutcome, ...]:
    """The batch's refusal, attributed per entry rather than reported only once.

    The operation is all-or-nothing, so a refused batch means nothing was committed: every planned
    entry is reported refused with the batch's own code, and the batch's typed refusal travels
    beside the report so the reason is readable in full.
    """

    refusal = None if result is None else result.refusal
    code = "batch_refused" if refusal is None else f"batch_{refusal.code}"
    detail = (
        "the batch was not attempted" if refusal is None else f"{refusal.code}: {refusal.detail}"
    )
    return tuple(
        _refused_entry(
            _fields_of(plan),
            code,
            "the one batch that carries the whole list did not commit, so this entry was not "
            f"committed ({detail})",
        )
        for plan in plans
    )


# --------------------------------------------------------------------------------------------
# Identity, outcomes and the report
# --------------------------------------------------------------------------------------------


def _identity(contract: WorktreeContract, kind: str, entry_id: str) -> str:
    """One derived identity: the namespace, the enclosure, which identity it is, and the entry."""

    return str(uuid5(_INGEST_NAMESPACE, f"{_enclosure(contract)}|{kind}|{entry_id}"))


def _role_for(locator: SourceLocator) -> RealizationRole:
    """How the citation realizes the statement, from what the locator names.

    The hand-off list carries no role, so the ingest states the one it authored rather than leaving
    the edge's meaning to a reader. A whole file or a range names the obligation's own place, which
    is its primary authority; a symbol names a construct that carries it.
    """

    return "primary-authority" if locator.kind != _SYMBOL_KIND else "enforcement"


def _fields_of(plan: _Plan) -> _EntryFields:
    """One plan's producer-side fields, for a refusal that must still name them."""

    return _EntryFields(
        entry_id=plan.entry_id,
        kind=plan.kind,
        disposition=plan.disposition,
        disposition_source=plan.disposition_source,
        statement=plan.statement,
        evidence=plan.evidence,
    )


def _ruling_outcome(plan: _Plan) -> EntryOutcome:
    """One ruling, reported skipped with the producer's own verdict carried into the report."""

    return EntryOutcome(
        entry_id=plan.entry_id,
        kind=plan.kind,
        disposition=plan.disposition,
        disposition_source=plan.disposition_source,
        state=SKIPPED,
    )


def _committed_outcome(plan: _Plan, routes: tuple[RouteOutcome, ...]) -> EntryOutcome:
    """One committed entry: its targets as they were measured, and the route each anchor carries."""

    return EntryOutcome(
        entry_id=plan.entry_id,
        kind=plan.kind,
        disposition=plan.disposition,
        disposition_source=plan.disposition_source,
        state=COMMITTED,
        targets=tuple(
            _target_outcome(target, routes, index) for index, target in enumerate(plan.targets)
        ),
        routes=routes,
    )


def _target_outcome(
    target: _TargetPlan, routes: tuple[RouteOutcome, ...], index: int
) -> TargetOutcome:
    """One committed target, with the route outcome its own position earned."""

    route = routes[index] if index < len(routes) else RouteOutcome(None, None, _ROUTE_UNGOVERNED)
    return TargetOutcome(
        path=target.path,
        step=target.step,
        completed_path=target.completed_path,
        source_identity=target.blob,
        locator_kind=target.locator.kind,
        locator=_locator_text(target.locator),
        observation=target.observation,
        route_path=route.route_path,
        route_state=route.state,
        refusal=route.refusal,
    )


def _locator_text(locator: SourceLocator) -> str | None:
    """One locator's own written form, for the report's locator column."""

    if isinstance(locator, SymbolLocator):
        return locator.qualified_name
    if isinstance(locator, LineRangeLocator):
        return f"{locator.start_line}-{locator.end_line}"
    return None


def _refused_entry(fields: _EntryFields, code: str, reason: str) -> EntryOutcome:
    """One refused entry, with the exact reason and no fabricated success beside it."""

    return EntryOutcome(
        entry_id=fields.entry_id,
        kind=fields.kind,
        disposition=fields.disposition,
        disposition_source=fields.disposition_source,
        state=REFUSED,
        refusal=f"{code}: {reason}",
    )


def _projected(planned: tuple[_Plan, ...]) -> _Run:
    """What the batch would carry, for the dry report that committed nothing.

    One entry contributes ``AddInvariant`` and ``AddInvariantRevision``; each citation contributes
    its ``AddSourceAnchor`` and ``AddRealizationClaim``, and writes the same two rows. The route leg
    writes one ``route`` row per distinct scope outside the batch, and attaching writes one
    association per governed anchor. Counting them here is arithmetic over the plans the run already
    built, not a second construction of the batch, so a dry report's command and row counts are the
    counts the real run reports.
    """

    batch_rows = sum(2 + 2 * len(plan.targets) for plan in planned)
    route_paths = {one.route_path for plan in planned for one in plan.targets if one.route_path}
    governed = sum(1 for plan in planned for one in plan.targets if one.route_path is not None)
    return _Run(
        batch_state=_DRY_BATCH_STATE,
        commands=batch_rows,
        records=batch_rows + len(route_paths) + governed,
        dry_run=True,
    )


def _projected_outcomes(planned: tuple[_Plan, ...]) -> tuple[EntryOutcome, ...]:
    """Every entry a dry run would commit, with the route each anchor would carry.

    Each named route is projected as *authored*: the dry run did not read the candidate, so it
    cannot know which scopes already have a row, and claiming "reused" would be a fact the run never
    measured. The projection is labelled by ``dry_run`` on the report for exactly that reason.
    """

    return tuple(
        _committed_outcome(
            plan,
            tuple(
                RouteOutcome(target.route_path, target.route_id, _ROUTE_AUTHORED)
                if target.route_path is not None
                else RouteOutcome(None, None, _ROUTE_UNGOVERNED)
                for target in plan.targets
            ),
        )
        for plan in planned
    )


@dataclass(frozen=True)
class _Run:
    """Everything the report needs from the run itself: the batch, its commands, its routes.

    ``dry_run`` says the batch was never opened, and the two projections then carry what the batch
    *would* have carried. That is what makes a dry report the run's own report with the commit
    withheld: the same entry outcomes, the same counts, and a batch state that names the withholding
    instead of looking like an operation that failed to reach its batch.
    """

    result: MutationResult | None = None
    commands: int = 0
    records: int = 0
    ledger: _RouteLedger | None = None
    batch_state: str | None = None
    refusal: KnowledgeRefusal | None = None
    dry_run: bool = False

    @property
    def state(self) -> str:
        if self.batch_state is not None:
            return self.batch_state
        return "no_change" if self.result is None else self.result.state

    @property
    def written(self) -> int:
        """Every row the run wrote: the batch's own, plus the route leg's two kinds.

        The route leg is not a candidate command, so its rows are not in ``result.changed``: one
        ``route`` row per scope it authored and one association per governed anchor. Counting them
        here is what makes a dry report's ``records_written`` the number the real run reports
        instead of a second, smaller one.
        """

        if self.dry_run:
            return self.records
        if self.result is None:
            return 0
        return len(self.result.changed) + self.route_rows

    @property
    def route_rows(self) -> int:
        """The rows the route leg wrote: one per authored scope, one per attached anchor."""

        ledger = self.ledger
        if ledger is None:
            return 0
        # Only the outcomes that actually attached wrote a row: an ungoverned target has no
        # association, and a refused one left none -- so the count is the associations the
        # candidate accepted, not the number of targets that named a route.
        attached = sum(
            1 for ones in ledger.attached.values() for one in ones if one.state == _ROUTE_ATTACHED
        )
        return len(ledger.answered) + attached


def _report(
    target: _ReportTarget,
    run: _Run,
    *,
    committed: tuple[EntryOutcome, ...] = (),
    dry_run: bool = False,
) -> IngestReport:
    """Assemble the one report every mode returns, from what the run already measured."""

    paths, resolution, repository, read = (
        target.paths,
        target.resolution,
        target.repository,
        target.read,
    )
    return IngestReport(
        contract_path=str(paths.contract_path),
        candidate_directory=str(paths.candidate),
        candidate_receipt=str(paths.candidate / CANDIDATE_RECEIPT_NAME),
        lane=resolution.lane,
        code_tree_id=resolution.code_tree_id,
        memory_tree_id=resolution.memory_tree_id,
        repository_id=repository.repository_id,
        derived_identities=(
            "uuid5 over one fixed curator-ingest namespace, the enclosure's recorded code base "
            "commit, which identity it is (invariant, revision, anchor, claim, route), and the "
            "entry's own id -- plus the written path for a target, because a target carries no "
            "identity of its own in revision 1"
        ),
        dry_run=dry_run,
        entries_read=read.ids,
        committed=committed,
        rulings=read.rulings,
        refused=read.refused,
        counts=_counts(read, run),
        batch_state=run.state,
        batch_digest_before=None if run.result is None else run.result.before.logical_digest,
        batch_digest_after=None if run.result is None else run.result.after.logical_digest,
        batch_refusal=run.refusal if run.refusal is not None else _batch_refusal(run),
    )


def _batch_refusal(run: _Run) -> KnowledgeRefusal | None:
    """The batch's own typed refusal, when there was a batch to refuse."""

    return None if run.result is None else run.result.refusal


def _counts(read: _Read, run: _Run) -> IngestCounts:
    """The auditable counts, each read from the outcome it counts rather than recomputed.

    In a dry run the two route counts are zero, because no route leg ran and no route exists to call
    reused: ``route_paths`` is the distinct scope count the list names, which is the fact a reader
    wants before a commit. The batch's two counts are the commands and rows the batch would carry,
    which is the whole point of a dry report being the run's report with the commit withheld.
    """

    ledger = run.ledger
    reused = 0 if ledger is None else len(ledger.reused_paths)
    answered = 0 if ledger is None else len(ledger.answered)
    targets = read.targets
    return IngestCounts(
        entries_read=len(read.ids),
        rulings=len(read.rulings),
        targets_completed=len(targets),
        locators_resolved=len(targets),
        anchors_observed_exact=sum(1 for one in targets if one.observation == _OBSERVED_EXACT),
        anchors_observed_unsupported=sum(
            1 for one in targets if one.observation == _OBSERVED_UNSUPPORTED
        ),
        anchors_observed_mismatch=sum(
            1 for one in targets if one.observation == _OBSERVED_MISMATCH
        ),
        anchors_observed_absent=sum(1 for one in targets if one.observation == _OBSERVED_ABSENT),
        anchors_observed_unavailable=sum(
            1 for one in targets if one.observation == _OBSERVED_UNAVAILABLE
        ),
        route_paths=len({one.route_path for one in targets if one.route_path is not None}),
        routes_authored=answered - reused,
        routes_reused=reused,
        commands_sent=run.commands,
        records_written=run.written,
    )
