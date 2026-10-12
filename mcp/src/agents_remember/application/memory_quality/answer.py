"""MIK-R110 rule 4: the memory-quality status answers, computed once.

The controller computes the agent-facing answer from the whole check result
and keeps it in the whole result the log records; the MCP boundary delivers
that same selection. Computing once keeps the log, the command line, and the
delivered status on one reason: every not-ok answer carries its code, a
one-sentence error, and the next action beside the bounded blocking entries
in repair order.
"""

from __future__ import annotations

import textwrap
from collections.abc import Mapping
from typing import Any

from agents_remember.models.memory import (
    MemoryQualityPollRequest,
    MemoryQualityStartRequest,
)
from agents_remember.models.tools.tool_answer import (
    TOOL_ANSWER_REASON_MAX_CHARS,
    TOOL_ANSWER_STATUS_ENTRY_MAX,
    is_valid_tool_code,
)

_QUALITY_FINDING_KEYS = ("check", "code", "path", "document", "place", "reason")
_QUALITY_OUTCOME_KEYS = (
    "checklistStatus",
    "qualityChecklistStatus",
    "coherenceStatus",
    "closeoutReady",
    "curatorActionableCount",
    "coherenceRecordDigest",
    "coherenceCanonicalPath",
    "reportPath",
    "attestationPath",
)
_QUALITY_KIND_COUNT_KEYS = (
    ("memoryRepairCount", "memoryRepair"),
    ("missingOnboardingCount", "missingOnboarding"),
    ("staleRouteIndexCount", "staleRouteIndexes"),
    ("closeoutOwnedFindingCount", "closeoutOwned"),
    ("noteworthyFindingCount", "noteworthy"),
)


def project_quality_finding(entry: Any) -> dict[str, str]:
    """One blocking finding as the caller acts on it: what, where, and why.

    The repair entry carries what to open (document), what motivated it
    (place), and why it blocks (reason); entries from checks that predate
    those keys read them off the finding's own path and message. The message
    is not carried beside the reason: where they differ the reason keeps the
    fuller sentence, so nothing the curator acts on is lost.
    """

    if not isinstance(entry, dict):
        return {
            "check": "",
            "code": "",
            "path": "",
            "document": "",
            "place": "",
            "reason": str(entry),
        }
    projected = {key: str(entry.get(key, "")) for key in _QUALITY_FINDING_KEYS}
    if not projected["document"]:
        projected["document"] = projected["path"]
    if not projected["reason"]:
        projected["reason"] = str(entry.get("message", ""))
    return projected


def project_missing_onboarding(row: Any) -> dict[str, str]:
    """One missing-onboarding row as a blocking repair entry."""

    source = str(row.get("sourceFile", "")) if isinstance(row, dict) else ""
    expected = str(row.get("expectedOnboarding", "")) if isinstance(row, dict) else ""
    note = str(row.get("note", "")) if isinstance(row, dict) else ""
    return {
        "check": "missing-onboarding",
        "code": "missing-onboarding",
        "path": expected,
        "document": expected,
        "place": source,
        "reason": note or f"Source {source} has no onboarding card at {expected}.",
    }


def project_stale_route_index(path: Any) -> dict[str, str]:
    """One stale route index as a blocking repair entry."""

    index = str(path)
    return {
        "check": "stale-route-index",
        "code": "stale-route-index",
        "path": index,
        "document": index,
        "place": index,
        "reason": f"Route index {index} is stale: refresh it with route_index_refresh.",
    }


def quality_finding_counts(whole: Mapping[str, Any]) -> dict[str, int]:
    """The count of findings by kind: checklist categories on a full run, checks otherwise."""

    kinds: dict[str, int] = {}
    for source, kind in _QUALITY_KIND_COUNT_KEYS:
        count = whole.get(source)
        if isinstance(count, bool):
            continue
        if isinstance(count, int) and count >= 0:
            kinds[kind] = count
    if kinds:
        return kinds
    checks = whole.get("checks")
    if isinstance(checks, dict):
        for name, result in checks.items():
            if isinstance(result, dict):
                count = result.get("findingCount")
                if isinstance(count, int) and count > 0:
                    kinds[str(name)] = count
    return kinds


def _shorten_reason(reason: str) -> str:
    """Shorten one blocking reason at a word boundary for the status answer.

    Reasons at most ``TOOL_ANSWER_REASON_MAX_CHARS`` pass through unchanged;
    longer ones are cut with an ellipsis marker. The full sentence stays in
    the whole result the log records.
    """

    return textwrap.shorten(reason, TOOL_ANSWER_REASON_MAX_CHARS, placeholder="...")


def quality_blocking(whole: Mapping[str, Any]) -> tuple[int, list[dict[str, str]]]:
    """The blocking total with its first entries in the order the repair loop needs.

    The entries are projected from the complete checklist blocking
    populations -- repairable findings (including census and gate blockers),
    missing onboarding, then stale route indexes -- first entries up to the
    status entry cap. Results from checks that ran without the checklist fall
    back to the whole result's own findings. The total is the curator loop's
    gate number where the checklist ran, else the measured finding total. The
    checklist report the answer names holds every row. Each entry is a slim
    work order -- code, document, place, reason -- under the status-answer
    ruling's default bound; the full rows stay in the whole result the log
    records.
    """

    entries: list[dict[str, str]] = []
    repair = whole.get("repairFindings")
    if isinstance(repair, list):
        entries.extend(
            project_quality_finding(entry) for entry in repair if isinstance(entry, dict)
        )
    missing = whole.get("missingOnboardingRows")
    if isinstance(missing, list):
        entries.extend(project_missing_onboarding(row) for row in missing)
    stale = whole.get("staleRouteIndexes")
    if isinstance(stale, list):
        entries.extend(project_stale_route_index(path) for path in stale)
    if not entries:
        findings = whole.get("findings")
        if isinstance(findings, list):
            entries.extend(project_quality_finding(entry) for entry in findings)
    entries = [
        {
            key: (_shorten_reason(entry[key]) if key == "reason" else entry[key])
            for key in ("check", "code", "document", "place", "reason")
        }
        for entry in entries[:TOOL_ANSWER_STATUS_ENTRY_MAX]
    ]
    actionable = whole.get("curatorActionableCount")
    if isinstance(actionable, bool):
        actionable = None
    total = (
        actionable if isinstance(actionable, int) and actionable >= 0 else whole.get("findingCount")
    )
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        total = len(entries)
    return total, entries


_QUALITY_LANDING_CUT_KEYS = ("path", "linesRemoved", "linesAdded")
_QUALITY_LANDING_ADDED_KEYS = ("path", "sizeBytes")


def _landing_rows(rows: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """The first landing rows as the caller reads them: what and how much."""

    slimmed: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows[:TOOL_ANSWER_STATUS_ENTRY_MAX]:
            if isinstance(row, dict):
                slimmed.append({key: row.get(key) for key in keys})
    return slimmed


def carry_landing_answer(whole: Mapping[str, Any], answer: dict[str, Any]) -> None:
    """MIK-R120 rule 3: a converted run answers its two landing counts.

    The counts ride every converted answer, including a clean leaf (a zero
    cut is still the answer to "what did this landing cut"). The first rows
    ride only when there is something to show; the full lists stay in the
    whole result the log records, which the answer's ``log`` names.
    """

    checks = whole.get("checks")
    if not isinstance(checks, dict):
        return
    landed = False
    cut_count = 0
    added_count = 0
    cut_rows: list[dict[str, Any]] = []
    added_rows: list[dict[str, Any]] = []
    for result in checks.values():
        if not isinstance(result, dict):
            continue
        cut = result.get("cutCardCount")
        if isinstance(cut, int) and not isinstance(cut, bool):
            landed = True
            cut_count += cut
            cut_rows.extend(_landing_rows(result.get("cutCards"), _QUALITY_LANDING_CUT_KEYS))
        added = result.get("addedFileCount")
        if isinstance(added, int) and not isinstance(added, bool):
            landed = True
            added_count += added
            added_rows.extend(_landing_rows(result.get("addedFiles"), _QUALITY_LANDING_ADDED_KEYS))
    if not landed:
        return
    answer["cutCardCount"] = cut_count
    answer["addedFileCount"] = added_count
    if cut_rows:
        answer["cutCards"] = cut_rows[:TOOL_ANSWER_STATUS_ENTRY_MAX]
    if added_rows:
        answer["addedFiles"] = added_rows[:TOOL_ANSWER_STATUS_ENTRY_MAX]


def quality_result_answer(whole: dict[str, Any]) -> dict[str, Any]:
    """MIK-R110 rule 4: a sync (or completed poll) whole result answered as a status.

    The outcome, the count of findings by kind, the blocking findings up to the list
    bound, and the locations of the checklist, census and coherence record. The census,
    the samples and the catalogue stay in the files the run wrote. A converted run
    additionally answers its two landing counts (MIK-R120): cut cards and added files,
    with the first rows; the full lists stay in the whole result the log records.
    A refusal keeps its reason as a code with the next action; a taken-over coherence
    refusal keeps that check's code and next action.
    """

    ok = bool(whole.get("ok", False))
    answer: dict[str, Any] = {"ok": ok}
    if whole.get("status") == "scope-refused":
        return quality_scope_refused_answer(whole, answer)
    answer["status"] = "passed" if ok else "failed"
    carry_quality_keys(whole, answer, ("repoId", "onboardingRoot"))
    carry_quality_keys(whole, answer, _QUALITY_OUTCOME_KEYS)
    if whole.get("state") is not None:
        answer["state"] = whole["state"]
    carry_quality_keys(
        whole, answer, ("code", "guidance", "error", "detail", "nextAction", "nextArgs")
    )
    kinds = quality_finding_counts(whole)
    if kinds:
        answer["findingCountsByKind"] = kinds
    carry_landing_answer(whole, answer)
    total, entries = quality_blocking(whole)
    answer["blockingCount"] = total
    if entries:
        answer["blocking"] = entries
    if not ok and answer["status"] == "failed":
        carry_failed_quality_reason(whole, answer, total)
    if "memoryCensus" in whole and isinstance(answer.get("reportPath"), str):
        parent = answer["reportPath"].rpartition("/")[0]
        answer["censusPath"] = f"{parent}/memory-census.json" if parent else "memory-census.json"
    return answer


def carry_failed_quality_reason(
    whole: Mapping[str, Any], answer: dict[str, Any], blocking_total: int
) -> None:
    """Give a failed check answer its code, one-sentence error, and next action.

    A taken-over coherence refusal keeps that check's code and next action
    (including ``task_doc`` when the task document has no parent) and gains
    the one-sentence error beside its guidance. An ordinary failed checklist
    is named by its checklist status; a failure with no checklist status is a
    bare check failure. A coherence-required check with no take-over is
    recovered through ``curator_coherence``.
    """
    if "code" not in answer:
        checklist = whole.get("checklistStatus")
        if checklist in ("action-required", "not-ready", "coherence-required"):
            answer["code"] = checklist
        else:
            answer["code"] = "check-failed"
    if "error" not in answer:
        answer["error"] = failed_quality_error(whole, answer, blocking_total)
    if "nextAction" not in answer:
        if whole.get("checklistStatus") == "coherence-required":
            answer["nextAction"] = "curator_coherence"
        else:
            answer["nextAction"] = quality_default_next_action(whole)


_QUALITY_TAKEOVER_ERRORS = {
    "task-document-parent-missing": (
        "The leaf has no task document: author it with task_doc, then rerun the check."
    ),
    "curator-coherence-task-missing": (
        "The leaf has no task document: author it with task_doc, then rerun the check."
    ),
}


def failed_quality_error(
    whole: Mapping[str, Any], answer: dict[str, Any], blocking_total: int
) -> str:
    """The one sentence a failed check answer carries beside its code."""

    code = answer.get("code")
    if isinstance(code, str) and code in _QUALITY_TAKEOVER_ERRORS:
        return _QUALITY_TAKEOVER_ERRORS[code]
    guidance = whole.get("guidance")
    if isinstance(guidance, str) and guidance:
        return guidance
    if code == "coherence-required":
        return (
            "Closeout is not ready on coherence grounds: publish or refresh the "
            "curator-coherence authority, then rerun the check."
        )
    return (
        f"Memory quality found {blocking_total} curator-actionable findings; repair the "
        "blocking findings and rerun the check."
    )


def carry_quality_keys(
    whole: Mapping[str, Any], answer: dict[str, Any], keys: tuple[str, ...]
) -> None:
    """Copy the whole result's present keys onto the answer, in order."""

    for key in keys:
        if whole.get(key) is not None:
            answer[key] = whole[key]


def quality_scope_refused_answer(whole: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
    """A scope refusal answered with its reason as a code and the next action."""

    answer["status"] = "scope-refused"
    code = whole.get("code")
    if not is_valid_tool_code(code):
        code = whole.get("pairStatus")
    answer["code"] = code if is_valid_tool_code(code) else "scope-refused"
    carry_quality_keys(
        whole,
        answer,
        (
            "repoId",
            "onboardingRoot",
            "pairField",
            "detail",
            "error",
            "nextAction",
            "nextArgs",
        ),
    )
    return answer


def quality_default_next_action(whole: Mapping[str, Any]) -> str:
    """The one following call where the whole result names no next action.

    An unconverted tree crosses through the sync the refusal text names; every other
    failed check is re-run after its blocking findings are repaired.
    """

    if whole.get("code") == "unconverted-memory":
        return "worktree_sync"
    return "memory_quality_check"


def quality_run_next_args(request: Any, *, mode: str, run_id: str | None = None) -> dict[str, Any]:
    """The follow-up poll or start with the scope the admitted request carried."""

    args: dict[str, Any] = {"mode": mode, "repo_id": request.repo_id}
    contract_path = getattr(request, "contract_path", None)
    if isinstance(contract_path, str) and contract_path:
        args["contract_path"] = contract_path
    if run_id is not None:
        args["run_id"] = run_id
    return args


def quality_start_answer(
    whole: dict[str, Any], request: MemoryQualityStartRequest
) -> dict[str, Any]:
    """MIK-R110 rule 4: an admission answered running with the operation to poll."""

    status = whole.get("status")
    if status == "capacity-reached":
        answer: dict[str, Any] = {
            "ok": False,
            "status": "capacity-reached",
            "code": "capacity-reached",
        }
        for key in ("repoId", "error", "guidance"):
            if whole.get(key) is not None:
                answer[key] = whole[key]
        answer["nextAction"] = "memory_quality_check"
        args: dict[str, Any] = {"mode": "start", "repo_id": request.repo_id}
        if isinstance(request.contract_path, str) and request.contract_path:
            args["contract_path"] = request.contract_path
        if request.checks:
            args["checks"] = list(request.checks)
        answer["nextArgs"] = args
        return answer
    answer = {
        "ok": True,
        "status": "running",
        "runId": whole.get("runId"),
        "nextAction": "memory_quality_check",
        "nextArgs": quality_run_next_args(request, mode="poll", run_id=str(whole.get("runId"))),
    }
    if whole.get("repoId") is not None:
        answer["repoId"] = whole["repoId"]
    return answer


def quality_poll_answer(whole: dict[str, Any], request: MemoryQualityPollRequest) -> dict[str, Any]:
    """MIK-R110 rule 4: a poll answered running, failed, refused, or as the completed result."""

    status = whole.get("status")
    if status == "run-not-found":
        answer: dict[str, Any] = {
            "ok": False,
            "status": "run-not-found",
            "code": "run-not-found",
        }
        for key in ("repoId", "runId", "error", "guidance"):
            if whole.get(key) is not None:
                answer[key] = whole[key]
        answer["nextAction"] = "memory_quality_check"
        args: dict[str, Any] = {"mode": "start", "repo_id": request.repo_id}
        if isinstance(request.contract_path, str) and request.contract_path:
            args["contract_path"] = request.contract_path
        answer["nextArgs"] = args
        return answer
    if status in ("running", "started"):
        return {
            "ok": True,
            "status": "running",
            "runId": whole.get("runId"),
            "repoId": whole.get("repoId"),
            "nextAction": "memory_quality_check",
            "nextArgs": quality_run_next_args(request, mode="poll", run_id=str(whole.get("runId"))),
        }
    if status == "failed":
        answer = {
            "ok": False,
            "status": "failed",
            "code": "quality-run-failed",
            "runId": whole.get("runId"),
            "nextAction": "memory_quality_check",
            "nextArgs": quality_run_next_args(request, mode="start"),
        }
        for key in ("repoId", "error"):
            if whole.get(key) is not None:
                answer[key] = whole[key]
        return answer
    return quality_result_answer(whole)
