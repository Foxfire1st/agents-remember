"""Answer projections for the task and landing tools (MIK-R111 rule 1).

A tool that moves a task along answers with the state it reached, what
blocks, and the one step that follows -- plus the values the caller must
carry to a later call or into its report. It does not hold
``source_lineage``, ``freshness``, the detail of a route index refresh or of
an onboarding refresh, the archives, the directory lists, the projection
effects or a review rebinding: each of those stays in the whole result the
function computed, which the log's detail file keeps byte for byte.

The projection only drops keys: every kept key is intersected with the whole
result, so a delivered key always sits in the whole result at the same place
(MIK-R130 rule 2), and the overflow short form, the list cap and the log
location keep working downstream. Framework keys (``ok``, ``code``,
``error``, ``next``, ``log`` and the overflow bookkeeping) always ride;
``nextStep`` is always retained so the lifecycle engine's one following call
still translates to ``next``. The producers' own ``nextTool``/``nextArgs``
triple is kept where the tool emits one: it is the executable form of the
same following call, and the landing chain is driven through it verbatim.
"""

from __future__ import annotations

from typing import Any

from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import (
    FRAMEWORK_ANSWER_KEYS,
    TOOL_ANSWER_EXCEPTION_MAX_TOKENS,
)

# Rule-7 refusal keys, free on every projected tool: a refusal is all
# framework keys plus the state it reached.
_REFUSAL_KEYS = frozenset({"state", "code", "error", "blockers"})

# Kept whole-result keys per tool, beside the framework keys, ``nextStep``
# and the refusal keys. ``contract_path``/``contractPath`` is the address the
# caller carries to the next call; the *_status cells are the state a status
# answer reached; the commit fields are what a step produced.
LANDING_KEPT_KEYS: dict[str, frozenset[str]] = {
    "worktree_start": frozenset(
        {
            "state",
            "status",
            "contract_path",
            "providers",
            "memory",
            "leaf_id",
            "task_name",
            "admission",
            "detail",
            "nextTool",
            "nextArgs",
            "nextOperation",
            "nextRequiredArgs",
            "summary",
        }
    ),
    "worktree_attach": frozenset(
        {
            "state",
            "phase",
            "contract_path",
            "nextTool",
            "nextArgs",
            "nextOperation",
            "nextRequiredArgs",
            "summary",
        }
    ),
    "worktree_status": frozenset(
        {
            "state",
            "status",
            "phase",
            "closeout_status",
            "integration_status",
            "cleanup",
            "approved_for_commit",
            "human_review_status",
            "code_worktree_dirty",
            "memory_worktree_dirty",
            "providers",
            "contract_path",
            "taskDocumentCopies",
            "task_document_copies",
            "atomicSeriesActivation",
            "admission",
            "detail",
            "lifecycleOperations",
            "syncOperation",
            "nextTool",
            "nextAction",
            "nextArgs",
            "nextOperation",
            "nextRequiredArgs",
            "summary",
        }
    ),
    "worktree_sync": frozenset(
        {
            "state",
            "status",
            "contract_path",
            "atomicSeriesActivation",
            "admission",
            "retryPrecondition",
            "detail",
            "resolution",
            "cancelArgs",
            "phase",
            "evidencePath",
            "manualRepair",
            "codeBaseCommit",
            "memoryBaseCommit",
            "nextTool",
            "nextArgs",
            "nextOperation",
            "nextRequiredArgs",
            "summary",
        }
    ),
    "worktree_pause": frozenset(
        {
            "state",
            "contract_path",
            "nextTool",
            "nextArgs",
            "nextOperation",
            "nextRequiredArgs",
            "summary",
        }
    ),
    "worktree_closeout_preview": frozenset(
        {
            "state",
            "status",
            "detail",
            "invalidFields",
            "resolvedPlan",
            "correctedCall",
            "commit_approval_required",
            "approval_question",
            "changed_code_paths",
            "proposed_commits",
            "closeout_status",
            "closeout_gate",
            "contract_path",
            "nextTool",
            "nextArgs",
            "nextOperation",
            "nextRequiredArgs",
            "summary",
        }
    ),
    "worktree_closeout_apply": frozenset(
        {
            "state",
            "status",
            "detail",
            "invalidFields",
            "resolvedPlan",
            "correctedCall",
            "code_commit",
            "memory_content_commit",
            "closeout_status",
            "contract_path",
            "nextTool",
            "nextArgs",
            "nextOperation",
            "nextRequiredArgs",
            "summary",
        }
    ),
    "worktree_integrate": frozenset(
        {
            "state",
            "integrated_code_commit",
            "integrated_memory_content_commit",
            "strategy",
            "staleDocuments",
            "gateId",
            "handover_gate",
            "contract_path",
            "nextTool",
            "nextArgs",
            "nextOperation",
            "nextRequiredArgs",
            "summary",
        }
    ),
    "worktree_checkpoint_landing": frozenset(
        {
            "state",
            "strategy",
            "integrated_code_commit",
            "integrated_memory_content_commit",
            "gateId",
            "staleDocuments",
            "handover_gate",
            "contractPath",
            "contract_path",
            "nextTool",
            "nextArgs",
            "nextOperation",
            "nextRequiredArgs",
            "summary",
        }
    ),
    "worktree_record_landing": frozenset(
        {
            "state",
            "landedCodeCommit",
            "landingTargets",
            "staleDocuments",
            "integrationStrategy",
            "contractPath",
            "contract_path",
            "nextTool",
            "nextArgs",
            "nextOperation",
            "nextRequiredArgs",
            "summary",
        }
    ),
    "worktree_operation_control": frozenset(
        {
            "state",
            "status",
            "detail",
            "expected",
            "observed",
            "nextAction",
            "lifecycleOperation",
            "pollTool",
            "contractPath",
            "contract_path",
            "nextTool",
            "nextArgs",
            "nextOperation",
            "nextRequiredArgs",
            "summary",
        }
    ),
    "worktree_cleanup": frozenset(
        {
            "state",
            "contract_path",
            "nextTool",
            "nextArgs",
            "summary",
        }
    ),
    "worktree_abandon": frozenset(
        {
            "state",
            "contract_path",
            "nextTool",
            "nextArgs",
            "nextOperation",
            "nextRequiredArgs",
            "summary",
        }
    ),
    "direct_landing": frozenset(
        {
            "state",
            "status",
            "detail",
            "invalidFields",
            "resolvedPlan",
            "correctedCall",
            "expected",
            "observed",
            "nextAction",
            "staleDocuments",
            "contract_path",
            "nextTool",
            "nextArgs",
            "nextOperation",
            "nextRequiredArgs",
            "summary",
        }
    ),
    "lifecycle_finalize_task": frozenset(
        {
            "state",
            "landedCommit",
            "targetBranch",
            "contractPath",
            "contract_path",
            "nextTool",
            "nextArgs",
            "nextOperation",
            "nextRequiredArgs",
            "summary",
        }
    ),
    "closeout_queue": frozenset(
        {
            "state",
            "action",
            "sourceProblems",
            "nextAction",
            "summary",
        }
    ),
    "task_reopen": frozenset(
        {
            "state",
            "contractPath",
            "contract_path",
            "nextTool",
            "nextArgs",
            "nextOperation",
            "nextRequiredArgs",
            "summary",
        }
    ),
    "worktree_enclosure_adopt": frozenset(
        {
            "state",
            "status",
            "detail",
            "dryRun",
            "contractPath",
            "worktreeGroup",
            "locatorPath",
            "manifestPath",
            "publicationRequestId",
            "expected",
            "observed",
            "developerDecisionRequired",
            "decisionSurface",
            "nextAction",
            "nextTool",
            "nextArgs",
            "summary",
        }
    ),
}


def project_landing_answer(
    tool_name: str, whole: dict[str, Any], arguments: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Project one whole result onto its answer keys, or ``None`` to pass through.

    Tools this leaf does not own pass through untouched (``task_doc`` has its
    own projection). The role tools project here: ``role_start`` to its rule-4
    triple, ``role_message`` to its rule-5 shape, which needs the call's own
    arguments (role-addressed, wait).
    """

    if tool_name == "role_start":
        return project_role_start_answer(whole)
    if tool_name == "role_message":
        return project_role_message_answer(whole, arguments=arguments or {})
    kept = LANDING_KEPT_KEYS.get(tool_name)
    if kept is None:
        return None
    allowed = kept | _REFUSAL_KEYS | {"nextStep"} | set(FRAMEWORK_ANSWER_KEYS)
    answer = {key: value for key, value in whole.items() if key in allowed}
    _refusal_envelope(answer, whole)
    if not whole.get("ok", True) and whole.get("code") != "raised":
        # Admission tables and duplicated investigation/guidance stay in the
        # whole log, not in the refusal's code, sentence and executable step.
        for key in (
            "admission",
            "detail",
            "expected",
            "observed",
            "eligibility",
            "retryPrecondition",
            "statusAction",
            "atomicSeriesActivation",
        ):
            if key == "detail" and not (
                isinstance(whole.get("summary"), str) and whole["summary"].strip()
            ):
                # Some producers (direct_landing) already use detail as their
                # one reason slot. Keep it when there is no summary to keep.
                continue
            answer.pop(key, None)
    _strip_redundant_answer_addresses(answer)
    if tool_name == "worktree_closeout_preview":
        _trim_preview_commit_locations(answer)
    return answer


def _refusal_envelope(answer: dict[str, Any], whole: dict[str, Any]) -> None:
    """Complete a normal refusal without using the raised-error channel.

    Producers keep their one WHY sentence in ``summary`` or ``detail``
    (D279); verbose investigation stays in the detail log. The existing code
    or producer status identifies the refusal. Actual ``raised`` answers keep ``error``.
    """
    if whole.get("ok", True) or whole.get("code") == "raised":
        return
    if "code" not in answer:
        status = whole.get("status")
        if isinstance(status, str) and status.strip():
            answer["code"] = status
    answer.pop("error", None)


def _strip_redundant_answer_addresses(answer: dict[str, Any]) -> None:
    """Drop top-level addresses the answer repeats, and empty blockers (MIK-R111 rule 1).

    The step's guidance names the enclosure twice: ``enclosure_path`` repeats
    ``contract_path`` byte for byte, and no tool takes it as an argument, so
    the top-level ``nextArgs`` keeps one copy. A carried-out answer that
    addresses its next step with the contract repeats its own top-level
    contract address the same way: the next step reads ``nextArgs``, so the
    echo goes and the detail file keeps the whole result. A refusal keeps
    its code, reason sentence and next step; only the redundant echo goes, so a
    deep-root refusal still fits its bound with its blockers.
    An empty ``blockers`` list says nothing blocks. The nested ``nextStep``
    triple rides verbatim for the guidance machinery that reads it. Copies
    are filtered, never mutated: the whole result keeps everything for the
    detail file.
    """
    if answer.get("blockers") == []:
        del answer["blockers"]
    args = answer.get("nextArgs")
    if (
        isinstance(args, dict)
        and isinstance(args.get("enclosure_path"), str)
        and args.get("enclosure_path") == args.get("contract_path")
    ):
        answer["nextArgs"] = {key: value for key, value in args.items() if key != "enclosure_path"}
    if isinstance(args, dict):
        # Ruling 218d585d point 2: an answer names at most its detail file
        # and the one document its next step reads. The top-level contract
        # echo repeats the next-step address exactly, ok or refused.
        for key in ("contract_path", "contractPath"):
            if isinstance(answer.get(key), str) and answer.get(key) == args.get(key):
                del answer[key]


def _trim_preview_commit_locations(answer: dict[str, Any]) -> None:
    """Drop the filesystem paths a preview answer's proposed commits name.

    Ruling 218d585d point 2: the worktree directories and ledger path are
    further locations beyond the detail file and the contract the next step
    reads, so they ride the whole result only. The commit substance
    (would-commit flags, intents, messages) stays: only location-valued
    leaves go. Copies are filtered, never mutated.
    """
    proposed = answer.get("proposed_commits")
    if not isinstance(proposed, dict):
        return
    trimmed = False
    for leg in ("code", "memory"):
        entry = proposed.get(leg)
        if isinstance(entry, dict) and isinstance(entry.get("worktree"), str):
            entry = dict(entry)
            del entry["worktree"]
            proposed = dict(proposed)
            proposed[leg] = entry
            trimmed = True
    ledger = proposed.get("ledger_cache")
    if isinstance(ledger, dict) and isinstance(ledger.get("path"), str):
        ledger = dict(ledger)
        del ledger["path"]
        if not trimmed:
            proposed = dict(proposed)
        proposed["ledger_cache"] = ledger
        trimmed = True
    if trimmed:
        answer["proposed_commits"] = proposed


# MIK-R111 rule 4: a carried-out start answers with ok, the agent's id, and
# the location of the handover. A warning rides only when the start has
# something the caller must know (rule 3: requirement files the leaf names
# but that do not exist). Anything else (rejected, unknown, refused) keeps
# its shape: the caller reads why and what to do from the detail file, and
# the refusal keeps its reason.
_ROLE_START_SUCCESS_KEPT = frozenset({"ok", "agentId", "handoverArtifactPath", "warning"})


def project_role_start_answer(whole: dict[str, Any]) -> dict[str, Any]:
    """Project one role_start whole result onto its rule-4 answer keys."""

    if whole.get("ok") is not True:
        return dict(whole)
    allowed = _ROLE_START_SUCCESS_KEPT | set(FRAMEWORK_ANSWER_KEYS)
    answer = {key: value for key, value in whole.items() if key in allowed}
    if not answer.get("warning"):
        answer.pop("warning", None)
    return answer


# MIK-R111 rule 5: without wait the answer is ok, status and how the
# recipient took the message. The recipient's id rides only a role-addressed
# call; detail rides only when the caller has to do something (a wait outcome
# that is not a plain finished turn); warning rides when the host cancelled
# the recipient's turn. With wait the answer adds the reply; a reply over the
# exception bound rides as its beginning, whole in the detail file.
_ROLE_MESSAGE_BASE_KEPT = frozenset({"ok", "status", "taken"})

# A wait reply is cut so the projected answer stays this far under the
# role_message exception bound (4,000): the framework attaches the log
# location afterwards, and the guard must never see the reply's beginning go
# missing into a short form.
_ROLE_MESSAGE_REPLY_HEADROOM_TOKENS = 100


def _role_message_is_wait(whole: dict[str, Any], arguments: dict[str, Any]) -> bool:
    """Whether one role_message answer carries a waited turn's outcome."""

    if arguments.get("wait") is True:
        return True
    return "waitedSeconds" in whole or "text" in whole


def _cut_reply(
    answer: dict[str, Any], *, headroom: int = _ROLE_MESSAGE_REPLY_HEADROOM_TOKENS
) -> dict[str, Any]:
    """Cut an over-bound wait reply to its beginning, marking the cut."""

    bound = TOOL_ANSWER_EXCEPTION_MAX_TOKENS - headroom
    text = answer.get("text")
    if not isinstance(text, str) or count_response_tokens(answer) <= bound:
        return answer
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        answer["text"] = text[:mid]
        if count_response_tokens(answer) <= bound:
            low = mid
        else:
            high = mid - 1
    answer["text"] = text[:low]
    answer["textTruncated"] = True
    return answer


def project_role_message_answer(
    whole: dict[str, Any], *, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Project one role_message whole result onto its rule-5 answer keys."""

    if whole.get("ok") is not True:
        return dict(whole)
    allowed = _ROLE_MESSAGE_BASE_KEPT | set(FRAMEWORK_ANSWER_KEYS)
    answer = {key: value for key, value in whole.items() if key in allowed}
    if arguments.get("role") is not None and isinstance(whole.get("recipientAgentId"), str):
        answer["recipientAgentId"] = whole["recipientAgentId"]
    if isinstance(whole.get("warning"), str) and whole["warning"]:
        answer["warning"] = whole["warning"]
    if not _role_message_is_wait(whole, arguments):
        return answer
    if isinstance(whole.get("text"), str):
        answer["text"] = whole["text"]
    if whole.get("textTruncated") is True:
        answer["textTruncated"] = True
    detail = whole.get("detail")
    if isinstance(detail, str) and (
        whole.get("status") != "turn-finished" or "read later" in detail
    ):
        answer["detail"] = detail
    return _cut_reply(answer)


# MIK-R111 rule 2: a write answers with ok, the unit it changed and its new
# value in a phrase, and reviewState when the write changed it. It names the
# rendered file only for create. masterSync rides only a failed sync.
_TASK_DOC_REVIEW_OPS = frozenset({"begin_review", "record_review", "record_route_review"})

_TASK_DOC_WRITE_KEPT = frozenset(
    {
        "ok",
        "updated",
        "status",
        "code",
        "error",
        "detail",
        "nextAction",
        "nextTool",
        "nextArgs",
        "nextRequiredArgs",
        "retirementState",
        "archiveState",
        "masterSync",
        "inputFile",
        "inputFileSha256",
        "factKinds",
        "reportPath",
    }
)

# wouldLose rides dry-run answers only (the validated model defaults it to
# False on every whole; carrying it on real writes would spend tokens on no
# information).

# A dry-run diff is cut to this many tokens; the whole diff stays in the
# detail file, and the cut marker names it -- but only when the detail was
# actually written (complete_tool_response rewrites the marker when the log
# write fails, so a lost log never fabricates a retained-diff promise).
TASK_DOC_DRY_RUN_DIFF_MAX_TOKENS = 1000
TASK_DOC_DIFF_TRUNCATED_MARKER = "... (truncated; the whole diff is in the log detail file)\n"


def _unretained_diff_marker(log_error: str | None) -> str:
    """The cut marker when the detail file was not written."""
    reason = f": {log_error}" if log_error else ""
    return f"... (truncated; the whole diff was not retained{reason})\n"


def _cut_diff(diff: str) -> str:
    """Cut a dry-run diff to the token budget, naming the whole diff."""

    if count_response_tokens({"diff": diff}) <= TASK_DOC_DRY_RUN_DIFF_MAX_TOKENS:
        return diff
    lines = diff.splitlines(keepends=True)
    kept: list[str] = []
    for line in lines:
        candidate = "".join(kept) + line
        if count_response_tokens({"diff": candidate}) > TASK_DOC_DRY_RUN_DIFF_MAX_TOKENS:
            break
        kept.append(line)
    kept.append(TASK_DOC_DIFF_TRUNCATED_MARKER)
    return "".join(kept)


def _project_task_doc_dry(whole: dict[str, Any]) -> dict[str, Any]:
    """Project one task_doc dry run: loss, cut diff, and measured file input."""
    answer = {
        key: value
        for key, value in whole.items()
        if key in ({"ok", "wouldLose", "updated"} | set(FRAMEWORK_ANSWER_KEYS))
    }
    if isinstance(whole.get("diff"), str):
        answer["diff"] = _cut_diff(whole["diff"])
    # The dry run measured its file input all the same: the digest the
    # handler read rides the answer beside the file that produced it.
    for location_key in ("inputFile", "inputFileSha256"):
        if isinstance(whole.get(location_key), str):
            answer[location_key] = whole[location_key]
    return answer


def project_task_doc_answer(
    whole: dict[str, Any], *, operation: str | None, dry_run: bool
) -> dict[str, Any]:
    """Project one task_doc whole result onto its answer keys (MIK-R111 rule 2)."""

    if whole.get("ok") is False:
        allowed = _TASK_DOC_WRITE_KEPT | {"nextStep"} | set(FRAMEWORK_ANSWER_KEYS)
        answer = {key: value for key, value in whole.items() if key in allowed}
    elif dry_run:
        answer = _project_task_doc_dry(whole)
    else:
        allowed = _TASK_DOC_WRITE_KEPT | {"nextStep"} | set(FRAMEWORK_ANSWER_KEYS)
        answer = {key: value for key, value in whole.items() if key in allowed}
        if operation == "create" and isinstance(whole.get("renderedPath"), str):
            answer["renderedPath"] = whole["renderedPath"]
        if operation in _TASK_DOC_REVIEW_OPS:
            answer.update(
                {
                    state_key: whole[state_key]
                    for state_key in ("reviewState", "memoryReviewState", "reviewKind")
                    if state_key in whole
                }
            )
        if operation == "record_route_review" and isinstance(whole.get("taskDocumentPath"), str):
            answer["taskDocumentPath"] = whole["taskDocumentPath"]
    master_sync = answer.get("masterSync")
    if (
        isinstance(master_sync, dict)
        and master_sync.get("status") != "failed"
        and whole.get("ok") is not False
    ):
        del answer["masterSync"]
    # Optional locations ride only when the call produced them: a null would
    # name nothing and spend the caller's tokens on it.
    for location_key in ("reportPath", "inputFile", "inputFileSha256"):
        if answer.get(location_key) is None:
            answer.pop(location_key, None)
    return answer
