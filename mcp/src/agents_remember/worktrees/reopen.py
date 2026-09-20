"""Reopen a completed leaf TASK under its exact same leaf id (the ``task_reopen`` tool).

Reopen resets a leaf's task state, and it does that by REWRITING THE LEAF'S ENCLOSURE
CONTRACT — which is why it lives here rather than in ``tasks``. It reads and amends the
contract, emits a ``WorktreeCommandResult``, and renders its report through the worktree
status payload; the document reset is the smaller half, and it goes through the ``tasks``
package the same way every other worktree operation does. Ranked the other way round it
made ``tasks`` and ``worktrees`` mutually dependent (``layers.toml``): the task document
store could not be loaded without loading the whole worktree lifecycle, for one function
that is a worktree lifecycle operation wearing a task-shaped name.

Historically, restarting a finished leaf meant minting a suffixed leaf id (``…-r1``):
a new enclosure and lifecycle while the doc, chats, and dashboard rows stayed keyed to
the original leaf — a forked identity no join could follow. ``task_reopen`` resets the
one true leaf instead:

- contract: the three progress blockers back to their virgin state (``human_review``
  pending / unapproved, ``closeout`` and ``integration`` not-started), the stale
  ``lifecycle.id`` cleared, and ``cleanup: reopened`` as the marker ``worktree_start``
  recreates fresh from (same tombstone semantics as ``abandoned``);
- doc: status back to ``planning``, its ``lifecycleId`` cleared (the next
  ``worktree_start`` restamps it with the fresh lifecycle), the master's sub-task
  index entry flipped back, and an audit decision appended.

The agent then edits steps via ``task_doc`` and runs a NORMAL ``worktree_start`` with
the same leaf id: worktrees and branches are recreated off the current source tips, a
fresh lifecycle is promoted/minted, and every binding holds by construction because
the leaf id never changed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.primitives.observer_paths import LANDING_FINAL_BASENAME
from agents_remember.tasks import ReviewState
from agents_remember.tasks.document import TaskDocument
from agents_remember.tasks.leaf_doc import find_leaf_doc
from agents_remember.tasks.master_sync import demote_completed_master_if_unresolved
from agents_remember.tasks.store import (
    json_path_for,
    markdown_path_for,
    read_task_doc,
    write_task_docs,
)

from .integration.integration_branch_authority import require_parent_series
from .integration.integration_ref_transaction import (
    IntegratedCommits,
    require_integrated_memory_ancestry,
)
from .integration.lifecycle.lifecycle_enclosure_terminal import (
    restartable_predecessor_contract,
)
from .integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocationError,
    inspect_lifecycle_operation_locator,
    require_terminal_lifecycle_predecessor,
    reserve_new_lifecycle_operation_location,
    resume_new_lifecycle_operation_location,
)
from .modules.git import branch_commit, branch_exists, is_ancestor, require_git, run_git
from .modules.guidance import (
    RecoveryOperation,
    RecoveryTool,
    recovery_guidance,
    status_payload,
)
from .modules.models import WorktreeCommandResult
from .scheduling_mode import TERMINAL_SERIES_CLEANUP
from .source_lineage import lineage_block_payload, lineage_refusal, parent_source_lineage
from .task_fact_publication import (
    contract_projection_scopes,
    preview_contract_task_facts,
    publish_task_fact_mutation,
)
from .worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    contract_publication_text,
    load_contract,
    write_contract,
)


class ReopenTaskDocumentError(ValueError):
    """The leaf and its parent index could not be prevalidated for one reset."""


class _ReopenTransitionRefusal(RuntimeError):
    """The locked reopen authority no longer matches its reviewed terminal leaf."""


def _contract_reopen_facts(contract: WorktreeContract) -> dict[str, object]:
    """Pure contract facts for reopen responses.

    This deliberately does not call ``status_payload``: that interactive projection reads
    providers, landing state, Git freshness, and descendant source lineage. A cleaned leaf has
    no branches left for those probes to inspect, and the only actionable fact at this boundary
    is its stable task identity plus the explicit recovery call.
    """
    return {
        "task_id": contract.task_id,
        "task_name": contract.task_name,
        "code_repository_name": contract.repo_name,
        "workflow_kind": contract.workflow_kind,
        "memory_mode": contract.memory_mode,
        "kind": contract.kind,
        "leaf_id": contract.leaf_id,
        "enclosure_path": contract.contract_path.as_posix(),
        "contract_path": contract.contract_path.as_posix(),
        "parent_contract_path": (
            contract.parent_contract_path.as_posix() if contract.parent_contract_path else ""
        ),
        "worktree_group": contract.worktree_group.as_posix(),
        "human_review_status": contract.human_review_status,
        "approved_for_commit": contract.approved_for_commit,
        "closeout_status": contract.closeout_status,
        "integration_status": contract.integration_status,
        "cleanup": contract.cleanup,
        "lifecycle_id": contract.lifecycle_id,
    }


def _reopen_preview_args(contract: WorktreeContract) -> dict[str, object]:
    return {"contract_path": contract.contract_path.as_posix(), "dry_run": True}


def _reopen_apply_args(contract: WorktreeContract) -> dict[str, object]:
    return {"contract_path": contract.contract_path.as_posix(), "dry_run": False}


def _start_preview_args(contract: WorktreeContract) -> dict[str, object]:
    """Reconstruct one task-addressed start from durable contract identity.

    ``worktree_name`` is the persisted code-worktree basename. ``parent_task`` is only an
    input discriminator for a genuinely nested task root; a direct master's contract happens
    to record its own series name as ``parent_task_name`` and must not feed that back as a
    nested path.
    """
    repo_task_root = contract.coordination_root / "tasks" / contract.repo_name
    relative = contract.task_root.relative_to(repo_task_root)
    args: dict[str, object] = {
        "repo_id": contract.repo_name,
        "task_name": contract.task_name,
        "worktree_name": contract.code_worktree.name,
        "leaf_id": contract.leaf_id,
        "workflow_kind": contract.workflow_kind,
        "source_branch": contract.code_source_branch,
        "work_branch": contract.code_work_branch,
        "memory_mode": contract.memory_mode,
        "dry_run": True,
    }
    if len(relative.parts) > 1:
        args["parent_task"] = relative.parts[-2]
    return args


def _response_guidance(
    operation: RecoveryOperation,
    *,
    tool: RecoveryTool,
    args: dict[str, object],
    summary: str,
) -> dict[str, object]:
    guidance = recovery_guidance(operation, tool=tool, args=args)
    return {**guidance, "nextStep": {"summary": summary, **guidance}}


def reopen_required_start_result(contract: WorktreeContract) -> WorktreeCommandResult:
    """Route ``worktree_start`` on a cleaned leaf to its explicit state reset."""
    summary = (
        "This leaf completed cleanup. Preview and apply task_reopen for this exact "
        "contract before retrying worktree_start; start will then recreate its "
        "contract-owned branches and worktrees from the current source tips."
    )
    return WorktreeCommandResult(
        2,
        {
            **_contract_reopen_facts(contract),
            "state": "reopen-required",
            "summary": summary,
            **_response_guidance(
                "reopen_completed_task",
                tool="task_reopen",
                args=_reopen_preview_args(contract),
                summary=summary,
            ),
        },
    )


def _reopened_contract(contract: WorktreeContract) -> WorktreeContract:
    """The contract with every review/closeout/integration cell reset (task reopen)."""

    return amend_contract(
        replace(
            contract,
            approved_for_commit=False,
            commit_approval_note="",
            code_commit="",
            memory_content_commit="",
            integration_strategy="",
            integrated_code_commit="",
            integrated_memory_content_commit="",
            lifecycle_id="",
            memory_state="",
        ),
        # The vocabulary cells go through the typed record, which is what puts them in front
        # of pyright: `dataclasses.replace` is `**changes: Any` in typeshed, so the `reopened`
        # marker below crossed the boundary unchecked for as long as it was spelled as a
        # `replace` keyword -- and it was one of the six values the packet then rejected.
        ContractCells(
            human_review_status="pending-review",
            closeout_status="not-started",
            integration_status="not-started",
            cleanup="reopened",
        ),
    )


def reopen_task(contract_path: Path, *, dry_run: bool = False) -> WorktreeCommandResult:
    contract = load_contract(contract_path)
    if contract.kind == "series":
        return reopen_series(contract, dry_run=dry_run)
    refusal = _reopen_preflight_refusal(contract)
    if refusal is not None:
        return refusal
    try:
        require_terminal_lifecycle_predecessor(contract)
    except LifecycleOperationLocationError as error:
        return WorktreeCommandResult(
            2,
            {
                "state": error.status,
                "status": error.status,
                **_contract_reopen_facts(contract),
                "summary": error.detail,
                "detail": error.detail,
                "expected": error.expected,
                "observed": error.observed,
                "nextAction": "developer-decision",
                "developerDecisionRequired": True,
                "decisionSurface": error.detail,
            },
        )
    updated = _reopened_contract(contract)
    try:
        _, doc_reset = _plan_leaf_doc_reset(contract, dry_run=dry_run)
    except ReopenTaskDocumentError as exc:
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                **status_payload(contract),
                "blockers": [f"task-document-reset: {exc}"],
                "summary": f"Reopen refused before any reset was written: {exc}",
            },
        )
    try:
        frozen_cleared, published_doc_reset, projection_effects = _publish_reopen_transition(
            contract,
            updated,
            dry_run=dry_run,
        )
    except (
        OSError,
        _ReopenTransitionRefusal,
    ) as exc:
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                **_contract_reopen_facts(contract),
                "blockers": [f"reopen-transition: {exc}"],
                "summary": (
                    "Reopen refused and restored every contract, task-document, and landing "
                    f"artifact to its pre-call bytes: {exc}"
                ),
            },
        )
    if published_doc_reset is not None:
        doc_reset = published_doc_reset
    summary = (
        "Reopen preview: the contract state and leaf doc would be reset as listed."
        if dry_run
        else (
            "Leaf task reopened under its original id: contract review/closeout/"
            "integration reset, lifecycle binding cleared, doc back to planning. "
            "Edit the doc's steps via task_doc, then preview worktree_start with this "
            "same leaf id to recreate the worktrees off the current source tips."
        )
    )
    if dry_run:
        operation: RecoveryOperation = "apply_task_reopen"
        tool: RecoveryTool = "task_reopen"
        next_args = _reopen_apply_args(contract)
    else:
        operation = "start_reopened_task"
        tool = "worktree_start"
        next_args = _start_preview_args(updated)
    return WorktreeCommandResult(
        0,
        {
            **_contract_reopen_facts(updated),
            "state": "would-reopen" if dry_run else "reopened",
            "doc": doc_reset,
            "frozenLanding": frozen_cleared,
            "projectionEffects": projection_effects,
            "summary": summary,
            **_response_guidance(
                operation,
                tool=tool,
                args=next_args,
                summary=summary,
            ),
        },
    )


def _reopen_preflight_refusal(contract: WorktreeContract) -> WorktreeCommandResult | None:
    blockers = _reopen_blockers(contract)
    if blockers:
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                **status_payload(contract),
                "blockers": blockers,
                "summary": (
                    "Reopen refused: only a fully landed leaf (closeout, integration, and "
                    "cleanup completed, worktrees gone) can be reopened. " + " ".join(blockers)
                ),
            },
        )

    try:
        require_parent_series(contract, operation="task_reopen")
    except RuntimeError as exc:
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                **status_payload(contract),
                "blockers": [str(exc)],
                "summary": f"Reopen refused before resetting task state: {exc}",
            },
        )

    if contract.memory_mode == "external":
        try:
            require_integrated_memory_ancestry(
                contract,
                IntegratedCommits(
                    code=contract.integrated_code_commit,
                    memory_content=contract.integrated_memory_content_commit,
                ),
                memory_source_commit=contract.memory_base_commit,
            )
        except RuntimeError as exc:
            return WorktreeCommandResult(
                2,
                {
                    "state": "blocked",
                    **status_payload(contract),
                    "blockers": [f"integrated-memory-landing: {exc}"],
                    "summary": f"Reopen refused before resetting task state: {exc}",
                },
            )

    # A terminal leaf's source already contains that leaf's exact landed commits.
    # Pre-start lineage compares against the recorded base, which is intentionally
    # older after a successful integration. Reopen instead proves that the current
    # source tips are the exact durable landing recorded by this completed leaf.
    landed = replace(
        contract,
        code_base_commit=contract.integrated_code_commit,
        memory_base_commit=(
            contract.integrated_memory_content_commit if contract.memory_mode == "external" else ""
        ),
    )
    lineage = parent_source_lineage(landed)
    if lineage_refusal(lineage) is not None:
        assert lineage is not None
        return WorktreeCommandResult(
            2,
            {
                **status_payload(contract),
                **lineage_block_payload(lineage),
                "summary": ("Reopen refused before resetting task state: " + lineage.summary),
            },
        )
    return None


def _clear_frozen_landing(contract: WorktreeContract, *, dry_run: bool) -> str:
    """Delete the frozen landing-final.json so the reopened arc starts clean.

    The landing freeze persists a finished leaf's landing facts beside its
    contract and pulls it out of the landing sweep permanently. A reopen makes those facts a
    lie: the leaf re-enters the sweep, but until this file is gone its second finish cannot
    re-freeze (a stale-but-loadable file would keep the leaf out of the sweep and serve the
    first-finish facts forever). Removing it here is what lets the re-finished leaf freeze with
    fresh facts.
    """
    final_path = contract.contract_path.parent / LANDING_FINAL_BASENAME
    if not final_path.exists():
        return "absent"
    if dry_run:
        return "would-delete"
    final_path.unlink()
    return "deleted"


def _reopen_blockers(contract: WorktreeContract) -> list[str]:
    blockers: list[str] = []
    if contract.kind != "leaf":
        blockers.append(f"contract kind is {contract.kind!r}, not a leaf enclosure.")
        return blockers
    if contract.closeout_status != "completed":
        blockers.append(f"closeout is {contract.closeout_status!r}, not completed.")
    if contract.integration_status != "completed":
        blockers.append(f"integration is {contract.integration_status!r}, not completed.")
    if contract.cleanup != "completed":
        blockers.append(f"cleanup is {contract.cleanup!r}, not completed.")
    for label, worktree in (("code", contract.code_worktree), ("memory", contract.memory_worktree)):
        if worktree is not None and worktree.exists():
            blockers.append(f"the {label} worktree still exists at {worktree.as_posix()}.")
    return blockers


def _plan_leaf_doc_reset(
    contract: WorktreeContract, *, dry_run: bool
) -> tuple[list[TaskDocument], dict | None]:
    """Prepare the doc side without publishing any part of the reopen transition."""
    found = find_leaf_doc(contract.task_root, contract.leaf_id)
    if found is None:
        raise ReopenTaskDocumentError(
            f"leaf {contract.leaf_id!r} has no canonical task document; "
            "task_reopen cannot produce an actionable worktree_start identity"
        )
    json_path, doc = found
    stamp = datetime.now(UTC).astimezone().strftime("%Y-%m-%dT%H:%M")
    data = doc.model_dump(by_alias=True)
    data["status"] = "planning"
    data["lifecycleId"] = None
    data.setdefault("decisions", []).append(
        {
            "at": stamp,
            "decision": f"Leaf {contract.leaf_id} reopened under its original id.",
            "rationale": (
                "task_reopen reset the enclosure contract (review/closeout/integration "
                "cleared, cleanup=reopened) and this document back to planning; the next "
                "worktree_start on the same leaf id recreates the worktrees and restamps "
                "the fresh lifecycle."
            ),
        }
    )
    updated = TaskDocument.model_validate(data)
    master, master_state = _plan_master_index_reset(contract, json_path, doc)
    report: dict = {
        "docPath": json_path.as_posix(),
        "status": "planning",
        "lifecycleId": None,
        "reopenedAt": stamp,
        "masterIndex": f"would-{master_state}"
        if dry_run and master_state == "reset"
        else master_state,
    }
    docs = [updated]
    if master is not None:
        docs.append(master)
    return (docs, report)


def _publish_reopen_transition(
    contract: WorktreeContract,
    updated: WorktreeContract,
    *,
    dry_run: bool,
) -> tuple[str, dict | None, list[dict[str, object]]]:
    """Publish the reopen batch under task CAS, then report projection refresh independently."""
    if dry_run:
        return _preview_reopen_transition(contract)
    publication = _ReopenPublication(contract, updated)
    published = publish_task_fact_mutation(
        contract.coordination_root,
        validate=publication.validate,
        projection_scopes=publication.projection_scopes,
        publication=publication.publish,
    )
    frozen_cleared, doc_reset = published.result
    return (
        frozen_cleared,
        doc_reset,
        [effect.model_dump(by_alias=True) for effect in published.projection_effects],
    )


def _preview_reopen_transition(
    contract: WorktreeContract,
) -> tuple[str, dict | None, list[dict[str, object]]]:
    documents, _report = _plan_leaf_doc_reset(contract, dry_run=True)
    return (
        _clear_frozen_landing(contract, dry_run=True),
        None,
        [
            effect.model_dump(by_alias=True)
            for effect in preview_contract_task_facts(contract, tuple(documents))
        ],
    )


@dataclass
class _ReopenPublication:
    contract: WorktreeContract
    updated: WorktreeContract
    documents: tuple[TaskDocument, ...] | None = None
    doc_reset: dict | None = None

    def prepared_documents(self) -> tuple[TaskDocument, ...]:
        if self.documents is None:
            raise _ReopenTransitionRefusal("reopen task batch was not prepared under task CAS")
        return self.documents

    def validate(self) -> None:
        current = load_contract(self.contract.contract_path)
        if current != self.contract:
            raise _ReopenTransitionRefusal(
                "the completed leaf contract changed after reopen preflight"
            )
        refusal = _reopen_preflight_refusal(current)
        if refusal is not None:
            raise _ReopenTransitionRefusal(str(refusal.payload["summary"]))
        try:
            docs, doc_reset = _plan_leaf_doc_reset(current, dry_run=False)
        except ReopenTaskDocumentError as exc:
            raise _ReopenTransitionRefusal(f"task-document-reset: {exc}") from exc
        self.documents = tuple(docs)
        self.doc_reset = doc_reset

    def projection_scopes(self) -> tuple:
        return contract_projection_scopes(self.contract, self.prepared_documents())

    def publish(self) -> tuple[str, dict | None]:
        docs = self.prepared_documents()
        if self.doc_reset is None:
            raise _ReopenTransitionRefusal("reopen task batch was not prepared under task CAS")
        originals = self._original_artifacts(docs)
        try:
            frozen_cleared = _clear_frozen_landing(self.contract, dry_run=False)
            write_task_docs(self.contract.task_root, list(docs))
            write_contract(self.contract.contract_path, self.updated)
        except BaseException as publish_error:
            try:
                _restore_reopen_artifacts(originals)
            except BaseException as rollback_error:
                raise RuntimeError(
                    f"reopen publication and rollback both failed: {rollback_error}"
                ) from publish_error
            raise
        return frozen_cleared, self.doc_reset

    def _original_artifacts(
        self,
        docs: tuple[TaskDocument, ...],
    ) -> dict[Path, bytes | None]:
        final_path = self.contract.contract_path.parent / LANDING_FINAL_BASENAME
        paths = {self.contract.contract_path, final_path}
        for doc in docs:
            paths.add(json_path_for(self.contract.task_root, doc))
            paths.add(markdown_path_for(self.contract.task_root, doc))
        return {path: path.read_bytes() if path.exists() else None for path in paths}


def _restore_reopen_artifacts(originals: dict[Path, bytes | None]) -> None:
    for path, payload in originals.items():
        if payload is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_bytes(path, payload)


def _plan_master_index_reset(
    contract: WorktreeContract,
    leaf_path: Path,
    doc: TaskDocument,
) -> tuple[TaskDocument | None, str]:
    """Prevalidate the parent reset so leaf and parent publish from one prepared batch."""
    master_path = _reopen_master_path(contract.task_root, doc)
    if master_path is None:
        return None, "no-master"
    if not master_path.exists():
        if doc.master:
            raise ReopenTaskDocumentError(
                f"explicit parent master task document does not exist: {master_path}"
            )
        return None, "no-master"
    try:
        master = read_task_doc(master_path)
    except (OSError, ValueError) as exc:
        raise ReopenTaskDocumentError(
            f"cannot read parent master task document {master_path}: {exc}"
        ) from exc
    if master.kind != "master":
        raise ReopenTaskDocumentError(f"parent task document is not a master: {master_path}")
    data = master.model_dump(by_alias=True)
    refs = data.get("subTasks", [])
    rows = [ref for ref in refs if ref.get("number") == doc.id]
    if not rows:
        if doc.master:
            raise ReopenTaskDocumentError(
                f"explicit parent master contains no exact row {doc.id!r}"
            )
        return None, "no-index-entry"
    if len(rows) != 1:
        raise ReopenTaskDocumentError(
            f"parent master must contain exactly one row {doc.id!r}; found {len(rows)}"
        )
    _validate_reopen_row_path(master_path, leaf_path, doc.id, rows[0])
    rows[0]["status"] = "planning"
    updated = demote_completed_master_if_unresolved(TaskDocument.model_validate(data))
    return updated, "reset"


def _validate_reopen_row_path(
    master_path: Path,
    leaf_path: Path,
    leaf_id: str,
    row: dict,
) -> None:
    file_name = str(row.get("file") or "")
    if not file_name:
        return
    row_path = (master_path.parent / Path(file_name).with_suffix(".json")).resolve(strict=False)
    if row_path != leaf_path.resolve(strict=False):
        raise ReopenTaskDocumentError(
            f"parent row {leaf_id!r} points at {row_path}, not leaf {leaf_path}"
        )


def _reopen_master_path(task_root: Path, doc: TaskDocument) -> Path | None:
    if not doc.master:
        default = task_root / "task.json"
        return default if default.exists() else None
    root = task_root.resolve(strict=False)
    ref = Path(doc.master)
    candidate = (root / ref.with_suffix(".json")).resolve(strict=False)
    if candidate.parent != root:
        raise ReopenTaskDocumentError(
            f"leaf master reference must resolve to a direct child of {root}: {doc.master!r}"
        )
    return candidate


# ---------------------------------------------------------------------------
# The series half: reopening a terminal atomic series (a master).
#
# A completed series was a dead end. AR models ``reopened`` as a legal series cleanup state
# (``scheduling_mode.TERMINAL_SERIES_CLEANUP``) and `series_attach_result` tells the reader to
# "reopen the task with task_reopen", but ``_reopen_blockers`` returned at its leaf-only kind gate
# before any other check, so no tool could set that state: on 2026-09-19 starting one repair leaf
# in this sprint required hand-editing three cells of the master's ``series-contract.md`` and
# injecting a ``parent_task_name`` edge, leaving `worktree_status` reporting a contract mismatch
# against the terminal archive -- an unreviewed, unrepeatable transition, which is the one thing
# this plane exists to prevent (D-49).
#
# The reopen is the series spelling of what ``task_reopen`` already does for a leaf, and it uses
# the same machinery rather than a second implementation of it:
#
# 1. the contract becomes the exact restartable tombstone (``cleanup: reopened`` and every
#    progress cell virgin) -- the state whose predicate the enclosure publication already reads;
# 2. both integration branches are re-cut at the recorded source tips (never moved, and only
#    when the cleanup that retired them left them absent), so a child leaf's lineage resolves;
# 3. the master document is demoted out of ``Completed`` with an audit decision naming the
#    reopen, and a review counter the completion spent is cleared;
# 4. the enclosure root, manifest and journal are re-published as the successor generation of the
#    archived one, which is what tells the terminal archive that this transition was sanctioned
#    instead of tampering -- the new locator carries the exact archived predecessor, and the
#    generation's accepted contract bytes are the LIVE series contract (``cleanup: pending``),
#    because ``reopened`` is itself a terminal series state and would leave the series unable to
#    own the lane.
#
# Steps 1 and 3 publish under the task CAS; step 4 is the guarded enclosure publication that
# proves the tombstone on disk is the predecessor it accepts. If step 4 is interrupted, the
# tombstone is already durable and the reopened series' own predicate lets the same call resume
# exactly that publication rather than rewriting the reset.
#
# There is a second way to arrive here, and it is the one D-58 named. A series reopened by hand
# before step 4 existed -- or by a reopen whose publication was interrupted and then forgotten --
# is already live on disk: ``cleanup: pending``, every progress cell virgin, its integration
# branches carrying the series' own landed work, and a locator that is still terminal-archived.
# The gates above refuse it, correctly for a reset (there is nothing terminal to cut) and
# uselessly in fact, because the only missing half is step 4. So the same call recognises that
# state -- ``_series_is_live_unaddressed`` reads the locator, which is the one fact that separates
# "the generation was collected" from "somebody is mid-transition" -- leaves the branches exactly
# where the series' own work put them, and publishes the successor generation. The publication
# itself is unchanged: the same tombstone proof, the same guards, the same archive citation.

SERIES_REOPEN_AUDIT_INTENT = (
    "task_reopen reset this terminal atomic series and re-published its successor enclosure "
    "generation"
)


@dataclass(frozen=True)
class _SeriesRefRecut:
    """One repository side's integration ref as the reopen found it and would set it."""

    side: str
    repo: Path
    branch: str
    source_branch: str
    tip: str
    action: str
    recorded_landing: str

    def payload(self) -> dict[str, object]:
        return {
            "side": self.side,
            "repository": self.repo.as_posix(),
            "branch": self.branch,
            "sourceBranch": self.source_branch,
            "tip": self.tip,
            "action": self.action,
            "recordedLanding": self.recorded_landing,
        }


@dataclass(frozen=True)
class _SeriesReopenPlan:
    """One reviewed series reopen: the tombstone, the live contract, and the ref plan."""

    contract: WorktreeContract
    tombstone: WorktreeContract
    live: WorktreeContract
    mode: str
    recuts: tuple[_SeriesRefRecut, ...]
    document: TaskDocument | None
    reopened_at: str
    previous_status: str
    frozen_landing: str
    document_note: str = ""

    def previous_landing(self) -> dict[str, str]:
        """The four landing cells the reopen is about to blank, read from the live contract.

        ``_reopened_contract`` clears all four -- correctly, because a reopened series has not
        landed -- so this capture is the only place the successor contract's reader can still
        learn what the re-cut branch was re-cut *from* without opening the terminal archive.
        """

        return {
            "code": self.contract.code_commit or self.contract.integrated_code_commit,
            "memory": (
                self.contract.memory_content_commit
                or self.contract.integrated_memory_content_commit
            ),
            "integratedCode": self.contract.integrated_code_commit,
            "integratedMemory": self.contract.integrated_memory_content_commit,
        }

    def payload(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "seriesRefs": [recut.payload() for recut in self.recuts],
            "previousLanding": self.previous_landing(),
            "reopenedAt": self.reopened_at,
            "previousStatus": self.previous_status,
            "frozenLanding": self.frozen_landing,
            "documentNote": self.document_note or None,
            "enclosureGeneration": {
                "predecessorState": "terminal-archived",
                "publicationKind": "successor-enclosure",
                "auditIntent": SERIES_REOPEN_AUDIT_INTENT,
            },
            "documentReset": (
                {
                    "docPath": (self.contract.task_root / "task.json").as_posix(),
                    "status": "inProgress",
                }
                if self.document is not None
                else None
            ),
        }


def _series_is_reset_tombstone(contract: WorktreeContract) -> bool:
    """Whether this series already carries the exact reopened reset rather than an abandonment."""

    return contract.cleanup == "reopened" and restartable_predecessor_contract(contract)


def _series_recorded_landing(contract: WorktreeContract, side: str) -> str:
    """The exact commit this side's landing is recorded at, closeout cell first.

    A fully integrated series records the landing twice: the closeout cell and the
    integration cell carry the same commit. The integration cell is the fallback for a
    contract whose closeout leg was never stamped, and an empty result is a defect in the
    record rather than a licence -- ``_series_ref_recut`` refuses on it.
    """

    if side == "code":
        return contract.code_commit or contract.integrated_code_commit
    return contract.memory_content_commit or contract.integrated_memory_content_commit


@dataclass(frozen=True)
class _SeriesSide:
    """One repository side of a series line: the branches, and where its landing landed."""

    side: str
    repo: Path
    source_branch: str
    work_branch: str
    recorded_landing: str


def _series_reopen_sides(contract: WorktreeContract) -> tuple[_SeriesSide, ...]:
    """The repository sides a series integration line spans, in code-then-memory order."""

    sides: list[_SeriesSide] = [
        _SeriesSide(
            side="code",
            repo=contract.code_repo_path,
            source_branch=contract.code_source_branch,
            work_branch=contract.code_work_branch,
            recorded_landing=_series_recorded_landing(contract, "code"),
        )
    ]
    if contract.memory_mode == "external" and contract.memory_repo_path is not None:
        sides.append(
            _SeriesSide(
                side="memory",
                repo=contract.memory_repo_path,
                source_branch=contract.memory_source_branch,
                work_branch=contract.memory_work_branch,
                recorded_landing=_series_recorded_landing(contract, "memory"),
            )
        )
    return tuple(sides)


def _series_landing_reachability(side: _SeriesSide) -> str | None:
    """The blocker when this side's landing cannot be placed on the line, or ``None``.

    A *missing* recorded landing is a defect in the record, and there is nothing to rebuild from:
    that is a blocker on every arm that has to **build** a ref, because a branch created from
    nothing would be a fiction. A landing that is present but not an ancestor of the source tip is
    a different fact -- the line moved out from under it -- and it is answered below rather than
    refused here, because the series' own work still exists in this repository.

    The blocker is asked for only on the creating arm. A branch that already exists and is left
    exactly where it stands needs no landing to stand on, which is what lets a series reopened by
    hand -- whose reset blanked all four landing cells -- be re-addressed rather than refused.
    """

    if not side.recorded_landing:
        return (
            f"the {side.side} series contract records no landing commit; the reopen cannot prove "
            "or rebuild the landing this line landed on."
        )
    return None


def _series_landing_reconstruction(
    side: _SeriesSide, tip: str, *, in_flight: bool
) -> _SeriesRefRecut | str | None:
    """What to do with an *existing* integration branch, or ``None`` when there is none.

    The reopen never moves an existing ref, and that promise is one rule in both worlds this route
    serves -- which is why both decisions are taken here, on the same three facts:

    * A **completed** series (``in_flight`` false) recorded its own integration and its cleanup
      retired these branches, so a branch that exists and does not stand on the recorded source tip
      can only be a ref somebody moved. Rewinding or advancing it is exactly what a reset must not
      do silently, so it is refused whatever the landing says.
    * A series that has **not** closed out is in flight: its integration branches are the line it is
      standing on, so a branch ahead of its source tip is the series' own committed work rather
      than a moved ref. ``advance`` records that it is left exactly where it is and nothing is
      created; refusing there would strand the very line the series is landing on.

    Ancestry is what separates the facts: standing on the source tip is ``present``, ahead of it is
    ``advance`` when the series is in flight, and diverged or lagging is refused in both worlds.
    """

    if not branch_exists(side.repo, side.work_branch):
        return None
    observed = branch_commit(side.repo, side.work_branch)
    if observed == tip:
        return _SeriesRefRecut(
            side.side,
            side.repo,
            side.work_branch,
            side.source_branch,
            tip,
            "present",
            side.recorded_landing,
        )
    if in_flight and is_ancestor(side.repo, tip, observed):
        return _SeriesRefRecut(
            side.side,
            side.repo,
            side.work_branch,
            side.source_branch,
            observed,
            "advance",
            side.recorded_landing,
        )
    return (
        f"the {side.side} integration branch {side.work_branch!r} stands at {observed}, not the "
        f"recorded source tip {tip} of {side.source_branch!r}; the reopen never moves an existing "
        "ref."
    )


def _series_ref_recut(side: _SeriesSide, *, in_flight: bool) -> _SeriesRefRecut | str:
    """Plan one side's re-cut, reconstruction, or adoption -- or return the blocker forbidding it.

    Four decisions, in this order, and the order is the whole design:

    1. **The source branch must exist and the work branch must be recorded.** Nothing below can be
       decided without them, and each refusal names which side is missing what.
    2. **An existing ref is never moved** (``_series_landing_reconstruction``): adopted as
       ``present`` when it stands on the source tip, reported as ``advance`` when an in-flight
       series is standing on its own committed work, and refused otherwise. The landing is *not*
       consulted here, because a branch that is left where it stands needs nothing to stand on.
    3. **A missing landing is a blocker** on the creating arm: there is nothing to rebuild from.
    4. **A retired branch is re-created at the source tip when the landing is still on that line**
       -- the case the reopen was built for -- and **at the recorded landing when it is not**
       (``reconstructed``). The re-cut target is the source tip because a work branch recreated at
       the old landing would sit *behind* the source and the next integration could not
       fast-forward. When the source line no longer contains the landing, that reasoning inverts:
       re-cutting at the source tip would publish a branch that does not contain the work the series
       already landed, so the reopen rebuilds the branch at the landing instead and reports it. The
       series' work is preserved, the branch is visibly behind its source, and the ordinary
       ``worktree_sync`` remedy applies to that -- refusing here would strand a master whose only
       fault is that its source line moved.
    """

    if not side.source_branch or not branch_exists(side.repo, side.source_branch):
        return (
            f"the {side.side} series source branch {side.source_branch!r} does not exist in "
            f"{side.repo}."
        )
    if not side.work_branch:
        return f"the {side.side} series integration branch is not recorded in the contract."
    tip = branch_commit(side.repo, side.source_branch)
    decision = _series_landing_reconstruction(side, tip, in_flight=in_flight)
    if decision is not None:
        return decision
    blocker = _series_landing_reachability(side)
    if blocker is not None:
        return blocker
    if is_ancestor(side.repo, side.recorded_landing, tip):
        return _SeriesRefRecut(
            side.side,
            side.repo,
            side.work_branch,
            side.source_branch,
            tip,
            "re-cut",
            side.recorded_landing,
        )
    return _SeriesRefRecut(
        side.side,
        side.repo,
        side.work_branch,
        side.source_branch,
        tip,
        "reconstructed",
        side.recorded_landing,
    )


def _landing_rationale(previous_status: str, previous_landing: dict[str, str]) -> str:
    """The audit sentence recording what the reopen reset, and what it landed on.

    Both arrivals write this same decision, so both are named in it: the reset cuts, rebuilds or
    advances the integration branches without ever moving an existing ref, and the re-addressing
    arrival leaves them exactly where the series' own landed work put them. The review counter the
    completion spent is cleared with the status, so the next round is an ordinary first round rather
    than one billed against a budget the reopened series never spent.
    """

    return (
        f"task_reopen reset the terminal atomic series contract (review/closeout/integration "
        f"cleared, cleanup=reopened) so it owns the lane again, re-cut, rebuilt or advanced its "
        f"integration branches without ever moving an existing ref, cleared the review counter the "
        f"completion had spent, and re-published its enclosure generation with the archived one as "
        f"its predecessor. Child leaves start under this master exactly as they did before it "
        f"completed. Document status before the reopen: {previous_status!r}. Recorded landing "
        f"before the reset: code {previous_landing['code']!r}, memory "
        f"{previous_landing['memory']!r}; the reset blanks those cells, so they are recorded here "
        f"rather than lost."
    )


def _series_in_flight(contract: WorktreeContract) -> bool:
    """Whether the series still owns its integration line rather than having completed it.

    Read from the two progress cells a completion itself writes. ``cleanup`` deliberately cannot
    decide this: the reopen rewrites that marker as its own first durable step, so keying the ref
    rule on it would make the rule's answer depend on whether the reset had already been written --
    which is exactly the difference between the first attempt and its resume.
    """

    return contract.closeout_status != "completed" and contract.integration_status != "completed"


def _series_is_live_unaddressed(contract: WorktreeContract) -> bool:
    """Whether this series is live but the enclosure generation at its address was collected.

    ``cleanup: pending`` is not a terminal series state, so the ordinary gate refuses it -- and
    that is right for a reset, which may only cut a retired line. But there is a second way to
    arrive here: the series *was* reopened (by hand, before this route existed, or by a reopen
    whose successor publication was interrupted) and then kept landing work. Its contract is live,
    its integration branches carry that work, and the only thing missing is the successor
    generation the reopen's last step publishes.

    The locator is what tells the two apart. A series is only in this state when the generation at
    its exact address is terminal-archived -- collected after terminal archive proof -- and its
    closeout and integration are both untouched, so nothing is mid-transition underneath the
    publication. Every other live contract is somebody else's business and stays refused.
    """

    if contract.kind != "series" or contract.cleanup != "pending":
        return False
    if contract.closeout_status != "not-started" or contract.integration_status != "not-started":
        return False
    observation = inspect_lifecycle_operation_locator(
        contract.coordination_root,
        contract.contract_path,
    )
    return observation.state == "terminal-archived"


def _review_state_carries_history(state: ReviewState | None) -> bool:
    """Whether a series reopen has a review counter to clear.

    The rule is that a reopened master's counter is 0: the rounds that ran belong to the
    completion being reopened, and carrying them forward would bill the next review against a
    budget it never spent -- and at the cap would make the ordinary three rounds read as an
    exhausted one, which is the difference between an ordinary first round and a round the
    developer had to authorize. A document that carries no review state, or an all-zero one, has
    nothing to clear and is left exactly as it is.
    """

    if state is None:
        return False
    return bool(
        state.round
        or state.pending
        or state.baselineFindings
        or state.remainingFindingIds
        or state.developerApproval is not None
        or state.additionalRounds
    )


def _plan_series_document_reset(
    contract: WorktreeContract,
    *,
    previous_landing: dict[str, str],
) -> tuple[TaskDocument | None, str | None, str, str, str]:
    """Prevalidate the demotion of the master document the series belongs to.

    Returns ``(document, blocker, reopened_at, previous_status, document_note)``. The stamp and the
    status are report facts only: nothing writes ``reopenedAt`` into the document, because it is not
    a declared field and ``TaskDocument`` is strict.

    A missing or malformed document is a **report fact, not a blocker** (ratchet sweep, `R3`). The
    reopen's object is the contract, the refs and the enclosure generation; the document is only
    what the master's own status is written through. Blocking the whole reopen on the document made
    a master unrecoverable for a reason that does not hold -- a seat can repair ``task.json``
    afterwards, and the branches, the successor generation and the lane are what it needs in hand to
    do that. A document that exists and is *not* a master is still refused, because a series
    contract whose task root holds a leaf document is a different task than the one this reopen
    would reset.

    A document that is already open and whose review state carries no history is left exactly as it
    is. One that carries history is written even when its status needs no change, to clear that
    history: the reopened master's counter is zero, and a counter is not the lane.
    """

    master_path = contract.task_root / "task.json"
    if not master_path.exists():
        return (None, None, "", "", f"series task document is missing: {master_path}")
    try:
        master = read_task_doc(master_path)
    except (OSError, ValueError) as exc:
        return (None, None, "", "", f"series task document is unreadable: {master_path}: {exc}")
    if master.kind != "master":
        return (None, f"series task document is not a master: {master_path}", "", "", "")
    stamp = datetime.now(UTC).astimezone().strftime("%Y-%m-%dT%H:%M")
    reopen_document = master.status == "Completed"
    clear_review = _review_state_carries_history(master.reviewState)
    if not reopen_document and not clear_review:
        # Already open with nothing spent: the reopen still publishes its successor generation,
        # and the master's own status is not this operation's to change.
        return (None, None, stamp, master.status, "")
    data = master.model_dump(by_alias=True)
    data["status"] = "inProgress"
    if clear_review:
        data["reviewState"] = ReviewState().model_dump(by_alias=True)
    data.setdefault("decisions", []).append(
        {
            "at": stamp,
            "decision": f"Series {contract.task_name} reopened under its original id.",
            "rationale": _landing_rationale(master.status, previous_landing),
        }
    )
    return (TaskDocument.model_validate(data), None, stamp, master.status, "")


def _series_reopen_plan(
    contract: WorktreeContract,
) -> _SeriesReopenPlan | WorktreeCommandResult:
    """Prevalidate one series reopen without writing any part of it."""

    blockers: list[str] = []
    if contract.leaf_id:
        blockers.append(f"series contract carries a leaf id {contract.leaf_id!r}.")
    live = _series_is_live_unaddressed(contract)
    if (
        not live
        and not _series_is_reset_tombstone(contract)
        and contract.cleanup not in TERMINAL_SERIES_CLEANUP
    ):
        blockers.append(
            f"series cleanup is {contract.cleanup!r}, not a terminal series state "
            f"({', '.join(sorted(TERMINAL_SERIES_CLEANUP))})."
        )
    in_flight = _series_in_flight(contract)
    recuts: list[_SeriesRefRecut] = []
    for side in _series_reopen_sides(contract):
        planned = _series_ref_recut(side, in_flight=in_flight)
        if isinstance(planned, str):
            blockers.append(planned)
        else:
            recuts.append(planned)
    previous_landing = {
        "code": contract.code_commit or contract.integrated_code_commit,
        "memory": contract.memory_content_commit or contract.integrated_memory_content_commit,
        "integratedCode": contract.integrated_code_commit,
        "integratedMemory": contract.integrated_memory_content_commit,
    }
    (
        document,
        document_blocker,
        reopened_at,
        previous_status,
        document_note,
    ) = _plan_series_document_reset(contract, previous_landing=previous_landing)
    if document_blocker is not None:
        blockers.append(document_blocker)
    if blockers:
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                **_contract_reopen_facts(contract),
                "blockers": blockers,
                "summary": (
                    "Reopen refused: a terminal atomic series is reopened when its enclosure "
                    "root was collected after terminal archive proof and its recorded landing can "
                    "be rebuilt, and a series that is already live at an address whose generation "
                    "was collected is re-addressed instead; this one is neither. "
                    + " ".join(blockers)
                ),
            },
        )
    tombstone = contract if _series_is_reset_tombstone(contract) else _reopened_contract(contract)
    return _SeriesReopenPlan(
        contract=contract,
        tombstone=tombstone,
        live=amend_contract(tombstone, ContractCells(cleanup="pending")),
        mode="publish" if (live or tombstone == contract) else "reset",
        recuts=tuple(recuts),
        document=document,
        reopened_at=reopened_at,
        previous_status=previous_status,
        frozen_landing=_clear_frozen_landing(contract, dry_run=True),
        document_note=document_note,
    )


@dataclass
class _SeriesReopenPublication:
    """The CAS-guarded half of one series reopen: refs, tombstone contract, master document."""

    plan: _SeriesReopenPlan
    documents: tuple[TaskDocument, ...] | None = None
    recuts: tuple[_SeriesRefRecut, ...] | None = None

    def prepared_documents(self) -> tuple[TaskDocument, ...]:
        return tuple(self.documents or ())

    def validate(self) -> None:
        current = load_contract(self.plan.contract.contract_path)
        if current != self.plan.contract:
            raise _ReopenTransitionRefusal(
                "the terminal series contract changed after reopen preflight"
            )
        replanned = _series_reopen_plan(current)
        if isinstance(replanned, WorktreeCommandResult):
            raise _ReopenTransitionRefusal(str(replanned.payload["summary"]))
        if replanned.recuts != self.plan.recuts:
            raise _ReopenTransitionRefusal(
                "the series integration refs moved after reopen preflight"
            )
        self.documents = (replanned.document,) if replanned.document is not None else ()
        self.recuts = replanned.recuts

    def projection_scopes(self) -> tuple:
        return contract_projection_scopes(self.plan.contract, self.prepared_documents())

    def publish(self) -> tuple[_SeriesRefRecut, ...]:
        if self.recuts is None:
            raise _ReopenTransitionRefusal("series reopen batch was not prepared under task CAS")
        contract = self.plan.contract
        originals = self._original_artifacts()
        created: list[tuple[Path, str]] = []
        try:
            for recut in self.recuts:
                if recut.action == "re-cut":
                    require_git(recut.repo, ["branch", recut.branch, recut.tip])
                elif recut.action == "reconstructed":
                    # At the recorded landing, not at the source tip: this branch has to carry the
                    # work the series already landed, and the tip no longer contains it.
                    require_git(recut.repo, ["branch", recut.branch, recut.recorded_landing])
                    created.append((recut.repo, recut.branch))
            write_contract(contract.contract_path, self.plan.tombstone)
            docs = self.prepared_documents()
            if docs:
                write_task_docs(contract.task_root, list(docs))
        except BaseException as publish_error:
            try:
                self._restore(originals, created)
            except BaseException as rollback_error:
                raise RuntimeError(
                    f"series reopen publication and rollback both failed: {rollback_error}"
                ) from publish_error
            raise
        return self.recuts

    def _original_artifacts(self) -> dict[Path, bytes | None]:
        contract = self.plan.contract
        paths = {contract.contract_path}
        master_path = contract.task_root / "task.json"
        paths.add(master_path)
        paths.add(master_path.with_suffix(".md"))
        return {path: path.read_bytes() if path.exists() else None for path in paths}

    def _restore(
        self, originals: dict[Path, bytes | None], created: list[tuple[Path, str]]
    ) -> None:
        for repo, branch in created:
            run_git(repo, ["branch", "-D", branch])
        for path, payload in originals.items():
            if payload is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_bytes(path, payload)


def _publish_series_successor(plan: _SeriesReopenPlan) -> WorktreeCommandResult | None:
    """Re-publish the enclosure generation the archived one authorizes, or report the failure."""

    live = plan.live
    text = contract_publication_text(live.contract_path, live)
    try:
        observed = inspect_lifecycle_operation_locator(
            live.coordination_root,
            live.contract_path,
        )
        if observed.state == "terminal-archived":
            reserve_new_lifecycle_operation_location(
                live,
                contract_text=text,
                predecessor_contract=plan.tombstone,
                audit_intent=SERIES_REOPEN_AUDIT_INTENT,
            )
        resume_new_lifecycle_operation_location(
            live,
            contract_text=text,
            audit_intent=SERIES_REOPEN_AUDIT_INTENT,
        )
    except LifecycleOperationLocationError as error:
        return WorktreeCommandResult(
            2,
            {
                "state": "reopen-publication-interrupted",
                "status": error.status,
                **_contract_reopen_facts(plan.tombstone),
                "summary": (
                    "The series reset is durable but its enclosure generation was not published: "
                    + error.detail
                ),
                "detail": error.detail,
                "expected": error.expected,
                "observed": error.observed,
                "nextAction": "resume-reopen-publication",
                "nextTool": "task_reopen",
                "nextArgs": {"contract_path": live.contract_path.as_posix(), "dry_run": False},
            },
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return WorktreeCommandResult(
            2,
            {
                "state": "reopen-publication-interrupted",
                **_contract_reopen_facts(plan.tombstone),
                "summary": (
                    "The series reset is durable but its enclosure generation could not be "
                    f"published: {type(exc).__name__}: {exc}"
                ),
                "nextAction": "resume-reopen-publication",
                "nextTool": "task_reopen",
                "nextArgs": {"contract_path": live.contract_path.as_posix(), "dry_run": False},
            },
        )
    return None


def _series_start_guidance(plan: _SeriesReopenPlan, summary: str) -> dict[str, object]:
    """The next move out of a reopened series: a normal child-leaf start under this master.

    The order is load-bearing and is stated rather than left to be learned by refusal: the new
    leaf's master row must be authored (with its ``file`` cell -- ``set_subtask`` does not
    derive one) while the master is open, and the leaf starts after the reopen. Starting
    before the reopen is what reads this contract as a stale terminal artifact.
    """

    contract = plan.live
    repo_task_root = contract.coordination_root / "tasks" / contract.repo_name
    relative = contract.task_root.relative_to(repo_task_root)
    args: dict[str, object] = {"repo_id": contract.repo_name, "task_name": contract.task_name}
    if len(relative.parts) > 1:
        args["parent_task"] = relative.parts[-2]
    guidance = recovery_guidance(
        "start_reopened_task",
        tool="worktree_start",
        args=args,
        required_args=["worktree_name", "leaf_id"],
    )
    return {
        **guidance,
        "nextStep": {"summary": summary, **guidance},
        "reopenOrder": [
            "task_reopen (this call) -- the master is open again and owns the lane",
            "task_doc set_subtask with the row's `file` cell supplied",
            "task_doc create for the new leaf's own task document",
            "worktree_start for that leaf under this master",
        ],
    }


def reopen_series(contract: WorktreeContract, *, dry_run: bool = False) -> WorktreeCommandResult:
    """Reopen one terminal atomic series as a single journaled, CAS-guarded operation."""

    plan = _series_reopen_plan(contract)
    if isinstance(plan, WorktreeCommandResult):
        return plan
    if dry_run:
        summary = (
            "Reopen preview: this series would be reset where a reset is due, its integration "
            "branches advanced or re-cut without moving an existing ref, and its enclosure "
            "generation re-published as the successor of the archived one."
        )
        return WorktreeCommandResult(
            0,
            {
                **_contract_reopen_facts(plan.live),
                "state": "would-reopen",
                **plan.payload(),
                "summary": summary,
                **_series_start_guidance(plan, summary),
            },
        )
    publication = _SeriesReopenPublication(plan)
    try:
        published = publish_task_fact_mutation(
            contract.coordination_root,
            validate=publication.validate,
            projection_scopes=publication.projection_scopes,
            publication=publication.publish,
        )
    except (OSError, _ReopenTransitionRefusal, RuntimeError) as exc:
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                **_contract_reopen_facts(contract),
                "blockers": [f"reopen-transition: {exc}"],
                "summary": (
                    "Reopen refused and restored every contract, ref, and task-document artifact "
                    f"to its pre-call state: {exc}"
                ),
            },
        )
    recuts, projection_effects = published.result, published.projection_effects
    frozen_cleared = _clear_frozen_landing(contract, dry_run=False)
    interrupted = _publish_series_successor(plan)
    if interrupted is not None:
        return interrupted
    if plan.mode == "publish":
        summary = (
            "Atomic series re-addressed under its original id: its enclosure generation was "
            "re-published with the archived one as its predecessor, and its integration branches "
            "were left exactly where the series' own landed work put them. Start a child leaf "
            "with worktree_start under this master."
        )
    else:
        summary = (
            "Atomic series reopened under its original id: contract review/closeout/integration "
            "reset, lifecycle binding cleared, review counter cleared, integration branches re-cut "
            "at the recorded source tips, and the enclosure generation re-published with the "
            "archived one as its predecessor. Start a child leaf with worktree_start under this "
            "master."
        )
    return WorktreeCommandResult(
        0,
        {
            **_contract_reopen_facts(plan.live),
            "state": "reopened",
            "mode": plan.mode,
            "seriesRefs": [recut.payload() for recut in recuts],
            "previousLanding": plan.previous_landing(),
            "reopenedAt": plan.reopened_at,
            "previousStatus": plan.previous_status,
            "frozenLanding": frozen_cleared,
            "documentNote": plan.document_note or None,
            "doc": plan.payload()["documentReset"],
            "enclosureGeneration": plan.payload()["enclosureGeneration"],
            "projectionEffects": [
                effect.model_dump(by_alias=True) for effect in projection_effects
            ],
            "summary": summary,
            **_series_start_guidance(plan, summary),
        },
    )
