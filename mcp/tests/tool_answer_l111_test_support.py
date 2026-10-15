"""MIK-R111 shaped answer rows (Expected Evidence 6).

The task and landing tools answer with the state and the next step. Row
fields follow the projections in application/landing_answer_shapes.py.
Extracted from test_tool_answer_table.py under the file-size rail
(F01.19); the table builder applies ``_apply_l111_shaped_rows``. This
module also owns the shared ``ToolAnswerRow`` type the builder fills, the
location-separating target comparator (F01.16: revision 218d585d point 4)
and the whole-result identity check (F01.17).
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import mock

from agents_remember.application import tool_response as _response_module
from agents_remember.application.landing_answer_shapes import LANDING_KEPT_KEYS
from agents_remember.application.task_docs.task_doc_tools import VALID_OPERATIONS
from agents_remember.application.tool_answer_log import TOOL_LOG_DETAIL_MAX_BYTES
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import (
    FRAMEWORK_ANSWER_KEYS,
    L111_SHAPED_TOOLS,
    bound_for,
    next_from_next_step,
    shaped_bound_for,
)


@dataclass(frozen=True)
class ToolAnswerRow:
    """One rule-6 table row: the fields an answer may hold and its bound.

    ``fields=None`` with empty ``uses`` is an interim row (rule 7) or an
    unshaped exception passthrough: no field list, held to the bound by the
    guard alone. A shaped row names every field, the caller step using it
    (a non-empty string), and its ordinary/largest case payloads, measured
    by the product counter -- never claimed integers. Rows whose owning
    leaf proves cases through live tool runs instead name the real tool
    paths in ``production_paths``.
    """

    tool: str
    operations: tuple[str | None, ...]
    fields: frozenset[str] | None
    uses: dict[str, str]
    bound: int
    interim_leaf: str | None
    ordinary_case: dict[str, Any] | None
    largest_case: dict[str, Any] | None
    production_paths: tuple[str, ...] = ()


# ---------------------------------------------------------------------------------------
# F01.16: the location-separating target comparator (revision 218d585d point 4).
#
# A packet target counts the answer without its locations; the whole result
# rides the detail file under the unchanged R108 bound. A location is any
# absolute-path string value in a mapping, at every depth -- the same rule
# the independent review measures by, so the candidate's oracles and the
# reviewer's agree on what was removed and what was counted.

#: An answer carries at most this many distinct locations (218d585d).
MAX_DISTINCT_LOCATIONS = 2


def separate_locations(value: Any) -> tuple[Any, list[dict[str, str]]]:
    """Split a value into its location-free shape and its locations.

    Every mapping value that is an absolute-path string is a location and
    is removed, at every depth, in mappings and in the mappings inside
    lists; a bare string inside a list is content, not a location. Each
    removed location is recorded as ``{"field": key, "value": path}``.
    """
    locations: list[dict[str, str]] = []

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            kept: dict[Any, Any] = {}
            for key, sub in node.items():
                if isinstance(sub, str) and Path(sub).is_absolute():
                    locations.append({"field": str(key), "value": sub})
                else:
                    kept[key] = walk(sub)
            return kept
        if isinstance(node, list):
            return [walk(sub) for sub in node]
        return node

    return walk(value), locations


def distinct_location_values(locations: list[dict[str, str]]) -> list[str]:
    """The sorted distinct location values one answer carried."""
    return sorted({entry["value"] for entry in locations})


def assert_location_separated_target(
    case: Any, tool: str, answer: dict[str, Any], target: int
) -> tuple[Any, list[str]]:
    """The three F01.16 measures on one delivered answer.

    (1) the answer without its locations is at or under its packet target;
    (2) the answer carries at most two distinct locations; (3) the whole
    delivered answer, locations included, stays under its unchanged bound.
    Returns the separated shape and the distinct locations.
    """
    without, locations = separate_locations(answer)
    distinct = distinct_location_values(locations)
    with case.subTest("location-separated target"):
        case.assertLessEqual(
            count_response_tokens(without),
            target,
            f"{tool} answer without locations over its {target}-token target",
        )
    with case.subTest("distinct location count"):
        case.assertLessEqual(
            len(distinct),
            MAX_DISTINCT_LOCATIONS,
            f"{tool} answer carries {len(distinct)} distinct locations: {distinct}",
        )
    with case.subTest("unchanged whole bound"):
        case.assertLessEqual(
            count_response_tokens(answer),
            bound_for(tool),
            f"{tool} whole answer over its unchanged bound",
        )
    return without, distinct


@contextlib.contextmanager
def captured_validated_wholes() -> Iterator[dict[str, list[dict[str, Any]]]]:
    """Capture each call's post-validation whole at the unchanged seam.

    A delegating spy on ``application.tool_response.finalize_tool_response``
    records tool name to its validated wholes in call order and returns them
    untouched: production behavior is identical, and the recorded whole is
    what the detail file must hold byte for byte (validation done,
    projection not yet run).
    """
    captured: dict[str, list[dict[str, Any]]] = {}
    real = _response_module.finalize_tool_response

    def spy(tool_name: str, payload: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        finalized = real(tool_name, payload, *args, **kwargs)
        captured.setdefault(tool_name, []).append(finalized)
        return finalized

    with mock.patch.object(_response_module, "finalize_tool_response", spy):
        yield captured


def assert_detail_holds_whole_result(
    case: Any,
    tool: str,
    answer: dict[str, Any],
    expected_whole: dict[str, Any],
) -> dict[str, Any]:
    """The F01.17 measure: the detail file holds the complete validated whole.

    ``expected_whole`` is captured from ``finalize_tool_response`` after
    validation and before projection (see ``captured_validated_wholes``). The
    parsed detail file must equal it completely: deleting or changing any
    retained field -- including one the compact answer legitimately drops --
    fails. Token ceilings apply only to the delivered answer; the file keeps
    the MIK-R108 rule 3 byte budget. Returns the whole result from the file.
    """
    log = answer.get("log")
    case.assertIsInstance(log, str, f"{tool} answer names no whole-result file")
    assert isinstance(log, str)
    raw = Path(log).read_bytes()
    with case.subTest("detail byte budget (MIK-R108 rule 3)"):
        case.assertLessEqual(
            len(raw),
            TOOL_LOG_DETAIL_MAX_BYTES,
            f"{tool} detail file over the 8 MiB byte budget",
        )
    whole_back = json.loads(raw.decode("utf-8"))
    case.assertIsInstance(whole_back, dict, f"{tool} detail file holds no whole-result mapping")
    with case.subTest("whole-result outcome"):
        case.assertEqual(
            whole_back.get("ok"), answer.get("ok"), f"{tool} detail file lost the outcome"
        )
        if "code" in answer:
            case.assertEqual(
                whole_back.get("code"), answer.get("code"), f"{tool} detail file lost the code"
            )
    # Bare (not a subTest): the identity failure must propagate so a tampered
    # file fails its caller instead of being recorded and suppressed.
    case.assertEqual(
        whole_back,
        expected_whole,
        f"{tool} detail file is not the complete validated whole",
    )
    if "next" in answer:
        with case.subTest("derived next kept its source"):
            raw_step = whole_back.get("nextStep")
            expected = next_from_next_step(raw_step if isinstance(raw_step, dict) else None)
            if expected is not None:
                expected = dict(expected)
                if (
                    isinstance(expected.get("args"), dict)
                    and answer.get("nextArgs") == expected["args"]
                ):
                    del expected["args"]
            case.assertEqual(
                answer["next"], expected, f"{tool} answer next is not the whole nextStep"
            )
    with case.subTest("retained dropped content"):
        case.assertTrue(
            set(expected_whole) - set(answer),
            f"{tool} validated whole holds nothing the answer dropped",
        )
    return whole_back


# ---------------------------------------------------------------------------------------
# MIK-R111 shaped rows (Expected Evidence 6): the task and landing tools answer with
# the state and the next step. Row fields follow the projections in
# application/landing_answer_shapes.py: the kept keys beside the framework keys,
# ``nextStep`` and the refusal keys (``state``, ``blockers``). ``task_doc`` rows share
# one common list; the file/review/linkage/retirement extras ride only the operations
# whose answers may hold them. Cases are fixed literals (no random segments), measured
# by the product counter in the gates below.
_L111_CONTRACT = "tasks/repo-a/m/1_leaf/enclosures/w/series-contract.md"
_L111_SHA_A = "a" * 40
_L111_SHA_B = "b" * 40

_L111_REFUSAL_FIELDS = frozenset({"state", "status", "blockers", "nextStep"})


def _l111_landing_fields(tool: str) -> frozenset[str]:
    """The fields one landing answer may hold beside the framework keys."""
    return frozenset((LANDING_KEPT_KEYS[tool] | _L111_REFUSAL_FIELDS) - FRAMEWORK_ANSWER_KEYS)


_L111_TASK_DOC_COMMON_FIELDS = frozenset(
    {
        "updated",
        "detail",
        "nextAction",
        "nextTool",
        "nextArgs",
        "nextRequiredArgs",
        "nextStep",
        "diff",
        "wouldLose",
        "status",
    }
)

# Extras only the named operations' answers may hold: the rendered file rides
# create alone, the review state rides the review writes, the linkage counts
# and report ride linkage_report, the retirement states ride their writes, the
# failed sync rides the master writes, and the file inputs ride the by-file
# writes (MIK-R111 rule 2).
_L111_TASK_DOC_EXTRAS: dict[str, frozenset[str]] = {
    "create": frozenset({"renderedPath", "inputFile", "inputFileSha256"}),
    "replace": frozenset({"inputFile", "inputFileSha256"}),
    "set_field": frozenset({"inputFile", "inputFileSha256"}),
    "set_section": frozenset({"inputFile", "inputFileSha256"}),
    "begin_review": frozenset({"reviewState"}),
    "record_review": frozenset({"reviewState"}),
    "record_route_review": frozenset({"reviewState", "taskDocumentPath"}),
    "linkage_report": frozenset({"factKinds", "reportPath"}),
    "retire_master": frozenset({"retirementState"}),
    "archive_sprint": frozenset({"archiveState"}),
    "attach_master": frozenset({"masterSync"}),
    "detach_master": frozenset({"masterSync"}),
}


def _l111_task_doc_fields(operation: str) -> frozenset[str]:
    """The fields one task_doc answer for an operation may hold."""
    return _L111_TASK_DOC_COMMON_FIELDS | _L111_TASK_DOC_EXTRAS.get(operation, frozenset())


_L111_ROLE_START_FIELDS = frozenset({"agentId", "handoverArtifactPath", "warning"})
_L111_ROLE_MESSAGE_FIELDS = frozenset(
    {"status", "taken", "recipientAgentId", "warning", "text", "textTruncated", "detail"}
)
_L111_DIRECT_EXECUTION_GRANT_FIELDS = frozenset(
    {
        "operation",
        "state",
        "summary",
        "status",
        "detail",
        "nextAction",
        "changed",
        "grant",
        "reclaimed",
        "preserved",
    }
)

_L111_USES: dict[str, str] = {
    "state": "the state the call reached",
    "summary": "the check's own sentence of what happened",
    "blockers": "each blocker with its code and one sentence",
    "nextStep": "the one call that follows, with its arguments",
    "nextTool": "the call that follows",
    "nextArgs": "the arguments the following call needs",
    "nextOperation": "the operation the following call runs",
    "nextRequiredArgs": "the arguments the following call must name",
    "nextAction": "what to do next",
    "contract_path": "the address the caller carries to the next call",
    "contractPath": "the address the caller carries to the next call",
    "phase": "the lifecycle phase the worktree is in",
    "gateId": "the gate blocking the landing",
    "handover_gate": "the gate verdict the preview evaluated",
    "task_document_copies": "the stale requirement copies, in the producer's own words",
    "lifecycleOperations": "the operations the status polls",
    "syncOperation": "the sync operation the status polls",
    "resolution": "the conflict to resolve, with files and worktree",
    "cancelArgs": "the call that cancels the operation",
    "evidencePath": "where the failure evidence lies",
    "manualRepair": "how to repair by hand",
    "codeBaseCommit": "the code base commit the sync resolved",
    "memoryBaseCommit": "the memory base commit the sync resolved",
    "invalidFields": "the input fields that failed validation",
    "resolvedPlan": "the corrected plan the input resolves to",
    "correctedCall": "the corrected call to retry",
    "expected": "what the control expected",
    "observed": "what the control observed",
    "lifecycleOperation": "the operation to poll",
    "pollTool": "the tool that polls the operation",
    "closeout_status": "the closeout the worktree has reached",
    "closeout_gate": "the closeout-approval gate verdict",
    "taskDocumentPath": "the task document the route review read",
    "dryRun": "whether the call only previewed",
    "worktreeGroup": "the enclosure group the adoption addresses",
    "locatorPath": "the locator the adoption wrote",
    "manifestPath": "the manifest the adoption wrote",
    "publicationRequestId": "the adoption request identity to approve",
    "developerDecisionRequired": "whether a developer must decide",
    "decisionSurface": "what the developer must decide",
    "integration_status": "the landing the worktree has reached",
    "cleanup": "the cleanup the enclosure has reached",
    "approved_for_commit": "whether the closeout may commit",
    "human_review_status": "the review the closeout waits on",
    "code_worktree_dirty": "whether the code worktree has uncommitted changes",
    "memory_worktree_dirty": "whether the memory worktree has uncommitted changes",
    "providers": "the providers the step ran",
    "memory": "the memory side the step planned",
    "admission": "the admission the step observed, with what it expected",
    "retryPrecondition": "what is not sufficient to continue, when the sync refuses",
    "atomicSeriesActivation": "the activation the status observed",
    "leaf_id": "the leaf the contract belongs to",
    "task_name": "the task the contract belongs to",
    "taskDocumentCopies": "whether the document copies differ from their sources",
    "commit_approval_required": "whether the apply waits on an approval",
    "approval_question": "the approval question the apply waits on",
    "changed_code_paths": "how many code paths changed",
    "proposed_commits": "the commits an apply would make, with subjects",
    "code_commit": "the code commit the closeout produced",
    "memory_content_commit": "the memory commit the closeout produced",
    "integrated_code_commit": "the code commit the landing produced",
    "integrated_memory_content_commit": "the memory commit the landing produced",
    "integratedCodeCommit": "the code commit the landing produced",
    "integratedMemoryContentCommit": "the memory commit the landing produced",
    "strategy": "the strategy the landing uses",
    "integrationStrategy": "the strategy the landing used",
    "landedCodeCommit": "the commit the landing target already held",
    "landingTargets": "where the commit had to be",
    "landedCommit": "the landed commit the edge proves",
    "targetBranch": "the branch the edge landed on",
    "action": "what the queue call did",
    "operation": "the operation the call ran",
    "changed": "whether the call changed anything",
    "grant": "the stored grant record, when one exists after the call",
    "reclaimed": "the grants the reclaim took back, per leaf",
    "preserved": "the grants the reclaim preserved, each with its reason",
    "sourceProblems": "what keeps the projection from admitting",
    "detail": "what the caller must do, when the answer alone is not enough",
    "updated": "the unit the write changed and its new value, in a phrase",
    "retirementState": "the retirement the master has reached",
    "archiveState": "the archive the sprint has reached",
    "masterSync": "the failed master sync, only when the sync failed",
    "inputFile": "the file the text was read from",
    "inputFileSha256": "the digest of the file the text was read from",
    "factKinds": "the kinds of facts the report holds, with counts",
    "reportPath": "where the linkage report lies",
    "renderedPath": "the rendered file, named only for create",
    "reviewState": "the review the write changed",
    "diff": "the diff the dry run would write, up to the bound",
    "wouldLose": "what the dry run would overwrite",
    "agentId": "the id of the started agent",
    "handoverArtifactPath": "where the handover lies",
    "warning": "what the caller must know although the call succeeded",
    "status": "how the host took the message",
    "taken": "how the recipient took the message",
    "recipientAgentId": "the recipient's id, only when the call addressed a role",
    "text": "the waited turn's reply",
    "textTruncated": "whether the reply rides cut, whole in the detail file",
}

_L111_NEXT_ARGS = {"contract_path": _L111_CONTRACT}

# Ordinary answers: small real shapes, one per tool.
_L111_ORDINARY: dict[tuple[str, str | None], dict[str, Any]] = {
    ("worktree_start", None): {
        "state": "started",
        "contract_path": _L111_CONTRACT,
        "leaf_id": "L1",
        "task_name": "1_leaf",
        "nextTool": "worktree_status",
        "nextArgs": dict(_L111_NEXT_ARGS),
        "summary": "Worktree started for leaf 1_leaf.",
    },
    ("worktree_attach", None): {
        "state": "attached",
        "contract_path": _L111_CONTRACT,
        "nextTool": "worktree_status",
        "nextArgs": dict(_L111_NEXT_ARGS),
        "summary": "Re-attached to the leaf contract.",
    },
    ("worktree_status", None): {
        "phase": "worktree-started",
        "contract_path": _L111_CONTRACT,
        "nextTool": "worktree_sync",
        "nextArgs": dict(_L111_NEXT_ARGS),
        "summary": "Worktree started; sync next.",
    },
    ("worktree_sync", None): {
        "state": "synced",
        "contract_path": _L111_CONTRACT,
        "nextTool": "worktree_status",
        "nextArgs": dict(_L111_NEXT_ARGS),
        "summary": "Pulled the moved official line.",
    },
    ("worktree_pause", None): {
        "state": "paused",
        "contract_path": _L111_CONTRACT,
        "nextTool": "worktree_status",
        "nextArgs": dict(_L111_NEXT_ARGS),
        "summary": "Paused the master; control is back.",
    },
    ("worktree_closeout_preview", None): {
        "state": "would-closeout",
        "changed_code_paths": {"count": 2},
        "proposed_commits": [{"subject": "leaf change"}],
        "contract_path": _L111_CONTRACT,
        "nextTool": "worktree_closeout_apply",
        "nextArgs": dict(_L111_NEXT_ARGS),
        "summary": "Two paths would close out as one commit.",
    },
    ("worktree_closeout_apply", None): {
        "state": "closed",
        "code_commit": _L111_SHA_A,
        "memory_content_commit": _L111_SHA_B,
        "closeout_status": "closed",
        "contract_path": _L111_CONTRACT,
        "nextTool": "worktree_status",
        "nextArgs": dict(_L111_NEXT_ARGS),
        "summary": "Closeout applied; two commits made.",
    },
    ("worktree_integrate", None): {
        "state": "integrated",
        "integrated_code_commit": _L111_SHA_A,
        "integrated_memory_content_commit": _L111_SHA_B,
        "strategy": "ff-only",
        "contract_path": _L111_CONTRACT,
        "nextTool": "worktree_cleanup",
        "nextArgs": dict(_L111_NEXT_ARGS),
        "summary": "Integrated ff-only; cleanup next.",
    },
    ("worktree_checkpoint_landing", None): {
        "state": "checkpointed",
        "integrated_code_commit": _L111_SHA_A,
        "integrated_memory_content_commit": _L111_SHA_B,
        "strategy": "ff-only",
        "contractPath": _L111_CONTRACT,
        "nextTool": "worktree_status",
        "nextArgs": dict(_L111_NEXT_ARGS),
        "summary": "Finished leaves landed; master still open.",
    },
    ("worktree_record_landing", None): {
        "state": "recorded",
        "landedCodeCommit": _L111_SHA_A,
        "landingTargets": ["main"],
        "integrationStrategy": "ff-only",
        "contractPath": _L111_CONTRACT,
        "nextTool": "worktree_status",
        "nextArgs": dict(_L111_NEXT_ARGS),
        "summary": "Recorded the commit the target holds.",
    },
    ("worktree_operation_control", None): {
        "ok": False,
        "state": "refused",
        "nextAction": "correct-request",
    },
    ("worktree_cleanup", None): {
        "state": "cleanup-completed",
        "contract_path": _L111_CONTRACT,
        "summary": "Evidence archived; enclosure removed.",
    },
    ("worktree_abandon", None): {
        "state": "abandoned",
        "contract_path": _L111_CONTRACT,
        "nextTool": "worktree_status",
        "nextArgs": dict(_L111_NEXT_ARGS),
        "summary": "Abandoned with terminal archive proof.",
    },
    ("direct_landing", None): {
        "state": "landed",
        "contract_path": _L111_CONTRACT,
        "nextTool": "worktree_status",
        "nextArgs": dict(_L111_NEXT_ARGS),
        "summary": "Landed the verified commit.",
    },
    ("lifecycle_finalize_task", None): {
        "state": "finalized",
        "landedCommit": _L111_SHA_A,
        "targetBranch": "main",
        "contractPath": _L111_CONTRACT,
        "nextTool": "worktree_cleanup",
        "nextArgs": dict(_L111_NEXT_ARGS),
        "summary": "Edge proved and completed.",
    },
    ("closeout_queue", None): {
        "state": "valid-built",
        "action": "status",
        "summary": "Projection exact-current and admitting.",
    },
    ("task_reopen", None): {
        "state": "reopened",
        "contractPath": _L111_CONTRACT,
        "nextTool": "task_doc",
        "nextArgs": {"operation": "set_step"},
        "summary": "Leaf reopened under its own id.",
    },
    ("worktree_enclosure_adopt", None): {
        "state": "enclosure-adopted",
        "status": "enclosure-adopted",
        "contractPath": _L111_CONTRACT,
        "locatorPath": "tasks/repo-a/m/1_leaf/enclosures/w/locator.json",
        "manifestPath": "tasks/repo-a/m/1_leaf/enclosures/w/manifest.json",
        "publicationRequestId": "01951f3a-7c2e-7b1a-9c3d-4e5f6a7b8c9d",
        "nextTool": "worktree_status",
        "nextArgs": dict(_L111_NEXT_ARGS),
    },
    ("role_start", None): {
        "ok": True,
        "agentId": "0dfd57f7-1b4b-4b71-ac30-20698eaca57f",
        "handoverArtifactPath": "role-reports/worker/0dfd57f7/handover.md",
    },
    ("role_message", None): {
        "ok": True,
        "status": "accepted",
        "taken": "steered",
    },
    ("direct_execution_grant", None): {
        "ok": True,
        "operation": "direct_execution_grant",
        "state": "unchanged",
        "summary": "grant for leaf 1_leaf unchanged: it already exists",
        "changed": False,
        "grant": None,
    },
}

# Ordinary task_doc answers: ok and the changed unit in a phrase.
_L111_TASK_DOC_UPDATED: dict[str, str] = {
    "create": "created leaf 1_leaf",
    "replace": "replaced leaf 1_leaf",
    "set_status": "status set to doing",
    "set_step": "step s2 set to done",
    "add_step": "added step s4",
    "remove_step": "removed step s4",
    "skip_step": "skipped step s3: out of scope",
    "set_subtask": "sub-task 03c_x set to done",
    "remove_subtask": "removed sub-task 03c_x",
    "set_section": "section Objective set",
    "append_decision": "appended decision d3",
    "put_open_question": "put_open_question applied",
    "begin_review": "began review",
    "record_review": "recorded review",
    "record_route_review": "recorded route review",
    "author_execution_graph": "authored execution graph",
    "attach_master": "attached master m",
    "detach_master": "detached master m",
    "retire_master": "retired master m-a",
    "archive_sprint": "archived sprint s",
    "linkage_report": "linkage reported: 3 facts",
    "set_field": "field title set",
}

_L111_TASK_DOC_ORDINARY_EXTRAS: dict[str, dict[str, Any]] = {
    "create": {"renderedPath": "tasks/repo-a/m/1_leaf/task.md"},
    "retire_master": {"retirementState": "hook-finished"},
    "archive_sprint": {"archiveState": "folders-archived"},
    "linkage_report": {
        "factKinds": {"seat-doc-row": 2, "membership-without-row": 1},
        "reportPath": "notes/reports/linkage.json",
    },
}


def _l111_task_doc_ordinary(operation: str) -> dict[str, Any]:
    """The ordinary write answer for one task_doc operation."""
    return {
        "ok": True,
        "updated": _L111_TASK_DOC_UPDATED[operation],
        **_L111_TASK_DOC_ORDINARY_EXTRAS.get(operation, {}),
    }


# Largest answers: the most the tool's own limits allow. A landing largest is
# a refusal with blockers up to the list bound (MIK-R108 rule 2); each blocker
# carries its code and one sentence (MIK-R111 rule 1).
_L111_BLOCKER_SENTENCE = (
    "The worktree folder is still present although the contract says the step is done; "
    "finish the step or remove the folder, then call again with the same arguments. "
    "The detail file holds the whole result and the exact paths. "
    "The census of the enclosure shows the same remaining files on a second read, "
    "so the refusal is stable until the step is finished and re-observed. "
    "The step's own sentence names the exact unfinished work; the next call finishes it. "
    "Twenty is the list bound: a longer blockage still answers twenty, whole in the file. "
)


def _l111_blockers(code: str, count: int = 20) -> list[dict[str, str]]:
    """Blockers up to the list bound, each with its code and one sentence."""
    return [
        {"code": code, "sentence": f"Blocker {index + 1}: {_L111_BLOCKER_SENTENCE}"}
        for index in range(count)
    ]


# Genuine per-tool largest refusals: each tool's synthetic largest carries
# only as many blockers as fit its measured bound (one blocker ≈ 117 tokens
# over a ≈ 205-token refusal base), so every row's largest case lands above
# half its bound and within it. The bounds themselves come from live
# measurements, never from these illustrations.
_L111_LARGEST_BLOCKER_COUNT: dict[str, int] = {
    "worktree_sync": 1,
    "worktree_attach": 2,
    "worktree_pause": 2,
    "worktree_operation_control": 2,
    "direct_landing": 2,
    "worktree_integrate": 2,
    "worktree_cleanup": 2,
    "worktree_start": 3,
    "worktree_status": 3,
    "worktree_checkpoint_landing": 3,
    "worktree_abandon": 3,
    "lifecycle_finalize_task": 3,
    "worktree_closeout_apply": 3,
    "worktree_record_landing": 3,
    "worktree_closeout_preview": 6,
    "task_reopen": 5,
    "worktree_enclosure_adopt": 5,
}


_L111_LARGEST_CODE: dict[str, str] = {
    "worktree_start": "start-reservation-contract-conflict",
    "worktree_sync": "sync-operation-refused",
    "worktree_integrate": "closeout-incomplete",
    "worktree_checkpoint_landing": "contract-not-a-series",
    "worktree_record_landing": "commit-not-on-landing-target",
    "worktree_cleanup": "integration-incomplete",
    "worktree_abandon": "abandon-blocked",
    "lifecycle_finalize_task": "task-document-resolution-blocked",
    "worktree_enclosure_adopt": "enclosure-adoption-approval-required",
}

_L111_LARGEST_NEXT: dict[str, str] = {
    "worktree_start": "worktree_status",
    "worktree_attach": "worktree_status",
    "worktree_status": "worktree_sync",
    "worktree_sync": "worktree_status",
    "worktree_pause": "worktree_status",
    "worktree_closeout_preview": "worktree_closeout_apply",
    "worktree_closeout_apply": "worktree_status",
    "worktree_integrate": "worktree_closeout_preview",
    "worktree_checkpoint_landing": "worktree_status",
    "worktree_record_landing": "worktree_status",
    "worktree_operation_control": "worktree_operation_control",
    "worktree_cleanup": "worktree_integrate",
    "worktree_abandon": "worktree_status",
    "direct_landing": "worktree_operation_control",
    "lifecycle_finalize_task": "worktree_cleanup",
    "closeout_queue": "closeout_queue",
    "task_reopen": "worktree_status",
    "worktree_enclosure_adopt": "worktree_enclosure_adopt",
}


def _l111_landing_largest(tool: str) -> dict[str, Any]:
    """The largest refusal one landing tool's limits allow."""
    code = _L111_LARGEST_CODE.get(tool, "blocked")
    next_tool = _L111_LARGEST_NEXT[tool]
    blockers = _l111_blockers(code, _L111_LARGEST_BLOCKER_COUNT.get(tool, 2))
    return {
        "ok": False,
        "state": "blocked",
        "code": code,
        "error": f"Refused with {code}: {_L111_BLOCKER_SENTENCE.strip()}",
        "blockers": blockers,
        "nextStep": {
            "nextTool": next_tool,
            "nextArgs": dict(_L111_NEXT_ARGS),
            "summary": f"Finish the blocking step, then call {next_tool}.",
        },
        "summary": f"Blocked with {code}; {len(blockers)} blockers listed.",
    }


# A task_doc largest is a dry run at the diff cap with the failed sync beside
# it: the diff alone may ride 1,000 tokens (MIK-R111 rule 2).
_L111_DIFF_CHUNK = "@@ chunk @@\n master task line changed by the dry run\n" * 4


def _l111_closeout_queue_largest() -> dict[str, Any]:
    """The largest queue answer: source problems at the genuine bound's scale.

    Fourteen modeled problems sit inside the live-derived 500-token bound
    past its half: the live status case (82-167 tokens) proves reachability,
    this case proves the bound is not gratuitous. The 256-problem model
    ceiling stays under the guard alone.
    """
    problems = [
        {
            "kind": "projection-source-stale",
            "detail": f"Member {index + 1}: the projection source changed under review; rebuild the queue to re-admit it.",
        }
        for index in range(14)
    ]
    return {
        "ok": True,
        "state": "valid-built",
        "action": "status",
        "sourceProblems": problems,
        "summary": "Projection exact-current with fourteen source problems listed.",
    }


def _l111_task_doc_diff() -> str:
    """A dry-run diff at the scale the diff cap allows, built deterministically."""
    return "".join(f"@@ hunk {index} @@\n{_L111_DIFF_CHUNK}" for index in range(20))


def _l111_task_doc_largest(operation: str) -> dict[str, Any]:
    """The largest dry run one task_doc operation's limits allow."""
    case: dict[str, Any] = {
        "ok": True,
        "updated": f"dry run: {_L111_TASK_DOC_UPDATED[operation]}",
        "wouldLose": "section Objective",
        "diff": _l111_task_doc_diff(),
        "detail": "The whole diff is in the log detail file.",
        "nextAction": "Send the write again without dry_run to apply it.",
    }
    if operation == "create":
        case["renderedPath"] = "tasks/repo-a/m/1_leaf/task.md"
    return case


def _l111_role_start_largest() -> dict[str, Any]:
    """The largest start: missing requirement files ride the warning."""
    missing = ", ".join(f"requirements/req-{index:03d}.md" for index in range(25))
    return {
        "ok": True,
        "agentId": "0dfd57f7-1b4b-4b71-ac30-20698eaca57f",
        "handoverArtifactPath": "role-reports/worker/0dfd57f7/handover.md",
        "warning": f"The leaf names requirement files that do not exist: {missing}.",
    }


def _l111_direct_execution_grant_largest() -> dict[str, Any]:
    """The largest grant answer: a reclaim taking back two grants, keeping one."""
    return {
        "ok": True,
        "operation": "direct_execution_grant",
        "state": "reclaimed",
        "summary": "reclaimed 2 grant(s), preserved 1",
        "changed": True,
        "grant": None,
        "reclaimed": [
            "tasks/repo-a/m/1_leaf/111_task-and-landing-tools.json",
            "tasks/repo-a/m/1_leaf/112_other-leaf.json",
        ],
        "preserved": [
            {
                "ref": "tasks/repo-a/m/1_leaf/113_third-leaf.json",
                "reason": "a leaf that still requires its grant",
            }
        ],
    }


def _l111_role_message_largest() -> dict[str, Any]:
    """The largest non-wait message: warning and detail ride a delivery."""
    return {
        "ok": True,
        "status": "accepted",
        "taken": "steered",
        "recipientAgentId": "0dfd57f7-1b4b-4b71-ac30-20698eaca57f",
        "warning": "The turn is still running; the reply arrives on the next poll.",
        "detail": "The handover names the seat and its task; read it with the file tool.",
    }


def _l111_role_message_wait_ordinary() -> dict[str, Any]:
    """The ordinary waited message: a short reply beginning."""
    return {
        "ok": True,
        "status": "turn-finished",
        "taken": "started",
        "recipientAgentId": "0dfd57f7-1b4b-4b71-ac30-20698eaca57f",
        "text": "The waited turn finished the review.",
    }


def _l111_role_message_wait_largest() -> dict[str, Any]:
    """The largest waited message: a long reply rides as its beginning."""
    line = "The waited turn finished the review and wrote its findings. "
    return {
        "ok": True,
        "status": "turn-finished",
        "taken": "steered",
        "recipientAgentId": "0dfd57f7-1b4b-4b71-ac30-20698eaca57f",
        "text": "".join(f"Reply part {index}: {line}" for index in range(230)),
    }


def _l111_shaped_row_content(
    tool: str, operation: str | None, key: tuple[str, str | None]
) -> tuple[frozenset[str], dict[str, Any], dict[str, Any]]:
    """The fields with uses and measured cases for one L111 shaped row."""
    if tool == "task_doc":
        assert isinstance(operation, str)
        content = (
            _l111_task_doc_fields(operation),
            _l111_task_doc_ordinary(operation),
            _l111_task_doc_largest(operation),
        )
    elif tool == "role_start":
        content = (
            _L111_ROLE_START_FIELDS,
            dict(_L111_ORDINARY[key]),
            _l111_role_start_largest(),
        )
    elif tool == "role_message" and operation == "wait":
        content = (
            _L111_ROLE_MESSAGE_FIELDS,
            _l111_role_message_wait_ordinary(),
            _l111_role_message_wait_largest(),
        )
    elif tool == "role_message":
        content = (
            _L111_ROLE_MESSAGE_FIELDS,
            dict(_L111_ORDINARY[key]),
            _l111_role_message_largest(),
        )
    elif tool == "direct_execution_grant":
        content = (
            _L111_DIRECT_EXECUTION_GRANT_FIELDS,
            dict(_L111_ORDINARY[key]),
            _l111_direct_execution_grant_largest(),
        )
    elif tool == "closeout_queue":
        content = (
            _l111_landing_fields(tool),
            dict(_L111_ORDINARY[key]),
            _l111_closeout_queue_largest(),
        )
    else:
        content = (
            _l111_landing_fields(tool),
            dict(_L111_ORDINARY[key]),
            _l111_landing_largest(tool),
        )
    return content


def _apply_l111_shaped_rows(table: dict[tuple[str, str | None], ToolAnswerRow]) -> None:
    """Shape the MIK-R111 rows: fields with uses and measured cases, no interim leaf.

    Each row carries its governed shaped bound; the wait row documents the
    exception guard of MIK-R111 rule 5.
    """
    for tool in sorted(L111_SHAPED_TOOLS):
        if tool == "task_doc":
            operations: tuple[str | None, ...] = tuple(sorted(set(VALID_OPERATIONS)))
        elif tool == "role_message":
            # The wait-specific exception row carries the waited reply; the
            # plain row carries the small shaped non-wait answers.
            operations = (None, "wait")
        else:
            operations = (None,)
        for operation in operations:
            shaped = shaped_bound_for(tool, operation=operation)
            assert shaped is not None, f"{tool} {operation} has no shaped bound"
            key = (tool, operation)
            fields, ordinary, largest = _l111_shaped_row_content(tool, operation, key)
            table[key] = ToolAnswerRow(
                tool=tool,
                operations=operations,
                fields=fields,
                uses={field: _L111_USES[field] for field in sorted(fields)},
                bound=shaped,
                interim_leaf=None,
                ordinary_case=ordinary,
                largest_case=largest,
            )
