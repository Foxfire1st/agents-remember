"""Memory, drift, route-index, baseline, and carryover payload builders."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agents_remember.application.memory_quality.answer import (
    quality_poll_answer,
    quality_result_answer,
    quality_start_answer,
)
from agents_remember.application.memory_quality.controller import (
    poll_memory_quality_request,
    run_memory_quality_request,
    start_memory_quality_request,
)
from agents_remember.application.memory_tools import (
    DEFAULT_CARRYOVER_MESSAGES,
    DEFAULT_CITATION_OPERATION_SCOPE,
    DEFAULT_MEMORY_BRANCHES,
    CarryoverCommitMessages,
    CarryoverSelection,
    CitationOperationScope,
    MemoryBranches,
    citation_fix_tool,
    memory_baseline_adopt_tool,
    memory_baseline_status_tool,
    memory_carryover_apply_tool,
    memory_carryover_plan_tool,
    memory_init_tool,
    route_index_refresh_tool,
)
from agents_remember.errors import ConfiguredContractAuthorityError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.primitives.tool_reports import write_tool_report
from agents_remember.models.memory import (
    MemoryQualityPollRequest,
    MemoryQualityStartRequest,
    MemoryQualitySyncRequest,
)
from agents_remember.models.tools.tool_answer import (
    ENVELOPE_STRIPPED_FIELDS,
    TOOL_ANSWER_LIST_MAX,
    TOOL_ANSWER_STATUS_ENTRY_MAX,
)
from agents_remember.worktrees.cutover_lock import LEGACY_FORMAT_CODE

from .base import _tool_payload

# MIK-R112 retired `drift_check` as a registered tool (no response model),
# so the MCP payload builder is gone with it; the status projection below
# stays for the builder-level row proofs and any future rewiring.
_DRIFT_STATUS_KEYS = (
    "ok",
    "onboardingRoot",
    "status",
    "error",
    "basis",
    "memoryRoot",
    "codeRoot",
    "sidecarCount",
    "count",
    "states",
    "actionableCount",
    "violationCount",
    "reportOnlyCount",
    "reportPath",
)
_DRIFT_ACTIONABLE_KEYS = ("path", "field", "target", "state")
_DRIFT_VIOLATION_KEYS = ("path", "rule", "message")


_DRIFT_SUCCESS_ROOT_KEYS = ("onboardingRoot", "memoryRoot", "codeRoot")


def _drift_answer(full: Mapping[str, Any]) -> dict[str, Any]:
    """MIK-R110 rules 1 and 7: one drift whole result answered as a status.

    The outcome, the measurement counts, and the locations -- never the root
    paths on a success; a refusal keeps the root context the recovery is read
    against. A flagged run additionally carries the stale anchors and the
    refusing findings as slim work orders up to the status entry cap -- what
    to open and what blocks, first entries first. The verbose finding rows
    (whose refresh verbs are constant per sample kind) stay in the drift
    report and in the whole result the log records: refresh each stale anchor
    through the onboarding gate.
    """

    answer: dict[str, Any] = {}
    succeeded = bool(full.get("ok", False))
    for key in _DRIFT_STATUS_KEYS:
        if succeeded and key in _DRIFT_SUCCESS_ROOT_KEYS:
            continue
        if full.get(key) is not None:
            answer[key] = full[key]
    stale = full.get("actionableSample")
    if isinstance(stale, list) and stale:
        answer["actionableSample"] = [
            {key: entry.get(key) for key in _DRIFT_ACTIONABLE_KEYS}
            for entry in stale[:TOOL_ANSWER_STATUS_ENTRY_MAX]
            if isinstance(entry, dict)
        ]
    violations = full.get("violationSample")
    if isinstance(violations, list) and violations:
        answer["violationSample"] = [
            {key: entry.get(key) for key in _DRIFT_VIOLATION_KEYS}
            for entry in violations[:TOOL_ANSWER_STATUS_ENTRY_MAX]
            if isinstance(entry, dict)
        ]
    unreadable = full.get("unreadableSidecars")
    if isinstance(unreadable, list) and unreadable:
        answer["unreadableSidecars"] = unreadable[:TOOL_ANSWER_STATUS_ENTRY_MAX]
    return answer


def memory_quality_check_payload(
    config: McpRuntimeConfig,
    request: MemoryQualitySyncRequest,
) -> dict[str, Any]:
    whole = run_memory_quality_request(config, request)
    return _tool_payload("memory_quality_check", whole, answer=quality_result_answer(whole))


def memory_quality_check_start_payload(
    config: McpRuntimeConfig,
    request: MemoryQualityStartRequest,
) -> dict[str, Any]:
    """Start one bounded request; a successful admission carries its run id."""

    whole = start_memory_quality_request(config, request)
    return _tool_payload("memory_quality_check", whole, answer=quality_start_answer(whole, request))


def memory_quality_check_poll_payload(
    config: McpRuntimeConfig,
    request: MemoryQualityPollRequest,
) -> dict[str, Any]:
    """Poll one started check through the repository recorded on its run."""

    whole = poll_memory_quality_request(config, request)
    return _tool_payload("memory_quality_check", whole, answer=quality_poll_answer(whole, request))


def citation_fix_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    contract_path: str | None = None,
    operation_scope: CitationOperationScope = DEFAULT_CITATION_OPERATION_SCOPE,
    dry_run: bool = False,
) -> dict[str, Any]:
    try:
        whole = citation_fix_tool(
            config,
            repo_id=repo_id,
            contract_path=contract_path,
            dry_run=dry_run,
            operation_scope=operation_scope,
        )
    except ConfiguredContractAuthorityError as error:
        whole = _citation_authority_refusal(repo_id, contract_path, error)
    if whole.get("status") == "configured-contract-authority-invalid":
        answer = None
    else:
        answer = _citation_fix_answer(whole)
    return _tool_payload("citation_fix", whole, answer=answer)


_CITATION_STATUS_KEYS = (
    "ok",
    "dryRun",
    "repoId",
    "onboardingRoot",
    "documentCount",
    "rewrittenCount",
    "staleCount",
    "unreadableCount",
    "refusedCount",
)
_CITATION_CONVERTED_ENTRY_KEYS = ("stale", "unreadableSidecars", "refused")
_CITATION_UNCONVERTED_ENTRY_KEYS = ("declined", "refusedDocuments")


def _carry_legacy_refusal(full: Mapping[str, Any], answer: dict[str, Any]) -> None:
    """MIK-R46 rule 2: the legacy refusal's central code and conversion message.

    The public answer itself carries both -- the logged whole result alone
    does not satisfy the rule, because the caller never learns how it
    converts from the log.
    """

    if full.get("code") == LEGACY_FORMAT_CODE:
        answer["code"] = full["code"]
        if full.get("detail") is not None:
            answer["detail"] = full["detail"]


def _citation_fix_answer(full: Mapping[str, Any]) -> dict[str, Any]:
    """MIK-R110 rules 1-2: one ``citation_fix`` whole result answered as a status.

    Successes carry only the outcome and the document and repair counts --
    never the root paths. Failures additionally carry the bounded
    actionable entries -- the stale, unreadable, refused, declined, or
    refused-document rows the curator must work, first entries up to the
    status entry cap -- beside the root context the recovery is read against. The
    rewritten sidecars, the authoring report, the source index, and the repair
    rows stay in the whole result the log records. Failure entries are slim
    work orders -- the document, the place, the machine code, and the
    message -- capped at the status entry cap with the total counts beside
    them.
    """

    answer: dict[str, Any] = {}
    succeeded = bool(full.get("ok", False))
    for key in _CITATION_STATUS_KEYS:
        if succeeded and key == "onboardingRoot":
            continue
        if full.get(key) is not None:
            answer[key] = full[key]
    _carry_legacy_refusal(full, answer)
    if bool(answer.get("ok", False)):
        return answer
    # A failed run is recovered by working the entries below, then running the
    # tool again.
    answer["nextAction"] = "citation_fix"
    if full.get("status") == "converted":
        authoring = full.get("authoring")
        refused = authoring.get("refused") if isinstance(authoring, dict) else None
        entries: dict[str, Any] = {
            "stale": full.get("stale"),
            "unreadableSidecars": full.get("unreadableSidecars"),
            "refused": refused,
        }
    else:
        entries = {
            "declined": full.get("declined"),
            "refusedDocuments": full.get("refusedDocuments"),
        }
        if full.get("findingsRemaining") is not None:
            answer["findingsRemaining"] = full["findingsRemaining"]
    for key in (
        _CITATION_CONVERTED_ENTRY_KEYS
        if full.get("status") == "converted"
        else _CITATION_UNCONVERTED_ENTRY_KEYS
    ):
        rows = entries.get(key)
        if isinstance(rows, list) and rows:
            answer[key] = [_slim_citation_entry(row) for row in rows[:TOOL_ANSWER_STATUS_ENTRY_MAX]]
    return answer


_CITATION_ENTRY_KEYS = ("path", "document", "line", "code", "message", "reason")


def _slim_citation_entry(row: Any) -> Any:
    """One failure entry as a slim work order: document, place, code, message.

    The status-answer ruling caps the answer at the default bound, and an
    entry holds only what MIK-R110 names for it: what the curator opens
    (path/document), the place within it (line), the machine-readable code,
    and the message. Anchors ride in the message; elaboration stays in the
    whole result the log records. Non-mapping rows pass through unchanged.
    """

    if not isinstance(row, dict):
        return row
    return {key: row[key] for key in _CITATION_ENTRY_KEYS if row.get(key) is not None}


def _citation_authority_refusal(
    repo_id: str, contract_path: str | None, error: ConfiguredContractAuthorityError
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "configured-contract-authority-invalid",
        "repoId": repo_id,
        "contractPath": contract_path,
        "detail": (
            f"The citation operation was refused because the contract's {error.side} {error.name} "
            "does not match the configured repository authority."
        ),
        "nextAction": "developer-decision",
    }


MAX_INLINE_CITATION_ITEMS = 50
"""How many entries of each list a converted ``citation_fix`` response carries."""

_CITATION_FIX_LISTS = ("stale", "rewrittenSidecars", "unreadableSidecars")
_AUTHORING_LISTS = ("authoredCards", "createdSidecars", "unresolvedTargets", "refused")


def _bounded_lists(payload: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    """``payload`` with each named list capped: its full count, and ``truncated`` when capped."""

    bounded = dict(payload)
    truncated = []
    for name in names:
        items = payload.get(name)
        if not isinstance(items, list):
            continue
        bounded[f"{name}Count"] = len(items)
        if len(items) > MAX_INLINE_CITATION_ITEMS:
            bounded[name] = items[:MAX_INLINE_CITATION_ITEMS]
            truncated.append(name)
    if truncated:
        bounded["truncated"] = truncated
        bounded["truncatedNote"] = (
            f"each of {', '.join(truncated)} shows its first {MAX_INLINE_CITATION_ITEMS} entries; "
            "the matching ...Count field is the full number. Name one card (document) for its own "
            "list, or run `agents-remember memory-citations --fix --dry-run` for the full lists"
        )
    return bounded


def _bounded_citation_fix(payload: dict[str, Any]) -> dict[str, Any]:
    """A converted tree's ``citation_fix`` result, bounded for transport (L37 P1c, C1).

    A tree-wide run on converted memory names every stale reference, every rewritten sidecar and
    every authored card: hundreds of entries on a real tree. The response keeps the counts and the
    first :data:`MAX_INLINE_CITATION_ITEMS` entries of each list and says which lists it capped.
    An unconverted tree's refusal (MIK-R46 rule 1) has no such lists and is passed through.
    """

    if payload.get("status") != "converted":
        return payload
    bounded = _bounded_lists(payload, _CITATION_FIX_LISTS)
    authoring = payload.get("authoring")
    if isinstance(authoring, dict):
        bounded["authoring"] = _bounded_lists(authoring, _AUTHORING_LISTS)
    return bounded


# MIK-R112 retired `citation_migrate` as a registered tool (no response
# model), so the MCP payload builder is gone with it; the status projection
# below stays for the builder-level row proofs and any future rewiring.
_MIGRATE_ORDER_KEYS = ("document", "cardPath", "itemCount", "codes")
_MIGRATE_ITEM_KEYS = ("line", "code", "message")


def _slim_migrate_order(order: Any) -> Any:
    """One declined work order as a slim entry: document, codes, first rows.

    The status-answer ruling caps the answer at the default bound: each order
    keeps its document, its codes, and its first nested rows slimmed to line,
    code, and message, with the item count beside them. The full rows stay in
    the whole result the log records.
    """

    if not isinstance(order, dict):
        return order
    slimmed: dict[str, Any] = {
        key: order[key] for key in _MIGRATE_ORDER_KEYS if order.get(key) is not None
    }
    items = order.get("items")
    if isinstance(items, list):
        slimmed["items"] = [
            {key: item.get(key) for key in _MIGRATE_ITEM_KEYS}
            for item in items[:TOOL_ANSWER_STATUS_ENTRY_MAX]
            if isinstance(item, dict)
        ]
    return slimmed


def _migrate_answer(full: Mapping[str, Any]) -> dict[str, Any]:
    """MIK-R110 rule 7: one migration whole result answered within the default bound.

    The outcome, the conversion counts, and the first declined work orders
    slimmed to document, codes, and first rows, with the declined count and
    the source generation beside them. The envelope fields stay with the
    whole result, as on every other projection. The complete decline list is
    that migration's contract and lives in the whole result the log records.
    """

    answer = {
        key: value
        for key, value in full.items()
        if key not in ENVELOPE_STRIPPED_FIELDS and value is not None
    }
    orders = full.get("workOrders")
    if isinstance(orders, list):
        slimmed = [_slim_migrate_order(order) for order in orders[:TOOL_ANSWER_STATUS_ENTRY_MAX]]
        answer["workOrders"] = (
            {"count": len(orders), "items": slimmed, "truncated": True}
            if len(orders) > TOOL_ANSWER_STATUS_ENTRY_MAX
            else slimmed
        )
    elif isinstance(orders, dict):
        items = orders.get("items")
        if isinstance(items, list):
            answer["workOrders"] = {
                **orders,
                "items": [
                    _slim_migrate_order(order) for order in items[:TOOL_ANSWER_STATUS_ENTRY_MAX]
                ],
                "truncated": bool(len(items) > TOOL_ANSWER_STATUS_ENTRY_MAX),
            }
    source = full.get("sourceIndex")
    if isinstance(source, dict):
        answer["sourceIndex"] = {
            key: source[key]
            for key in ("path", "snapshotId", "state")
            if source.get(key) is not None
        }
    return answer


def route_index_refresh_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    dry_run: bool = False,
    contract_path: str | None = None,
) -> dict[str, Any]:
    whole = route_index_refresh_tool(
        config,
        repo_id=repo_id,
        dry_run=dry_run,
        contract_path=contract_path,
    )
    return _tool_payload("route_index_refresh", whole, answer=_route_index_answer(whole))


_ROUTE_STATUS_KEYS = (
    "ok",
    "repoId",
    "onboardingRoot",
    "dryRun",
    "routes",
    "written",
    "unchanged",
    "refreshedCount",
    "unchangedCount",
    "staleCount",
)


def _route_index_answer(full: Mapping[str, Any]) -> dict[str, Any]:
    """MIK-R110 rules 1 and 5: one route-index whole result answered as a status.

    The outcome, the route counts, and the locations -- never the root paths
    on a success; a refusal keeps the root context the recovery is read
    against. A stale run additionally carries the stale indexes up to the
    list bound. The successfully written indexes stay in the whole result the
    log records -- the caller refreshes what the stale entries name, not what
    already wrote.
    """

    answer: dict[str, Any] = {}
    succeeded = bool(full.get("ok", False))
    for key in _ROUTE_STATUS_KEYS:
        if succeeded and key == "onboardingRoot":
            continue
        if full.get(key) is not None:
            answer[key] = full[key]
    stale = full.get("staleIndexes")
    if isinstance(stale, list) and stale:
        answer["staleIndexes"] = stale[:TOOL_ANSWER_LIST_MAX]
    return answer


def memory_init_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    dry_run: bool = False,
    initialize_git: bool = True,
    initial_branch: str | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "memory_init",
        memory_init_tool(
            config,
            repo_id=repo_id,
            dry_run=dry_run,
            initialize_git=initialize_git,
            initial_branch=initial_branch,
        ),
    )


def memory_baseline_status_payload(config: McpRuntimeConfig, repo_id: str) -> dict[str, Any]:
    return _tool_payload(
        "memory_baseline_status",
        memory_baseline_status_tool(config, repo_id=repo_id),
    )


def memory_baseline_adopt_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    accept_drift: bool = False,
    branches: MemoryBranches = DEFAULT_MEMORY_BRANCHES,
    dry_run: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "memory_baseline_adopt",
        memory_baseline_adopt_tool(
            config,
            repo_id=repo_id,
            accept_drift=accept_drift,
            branches=branches,
            dry_run=dry_run,
        ),
    )


def compact_carryover_payload(full: dict[str, Any], report_path: str | None) -> dict[str, Any]:
    """Keep the decisions, file the evidence.

    Every candidate record repeats two onboarding paths derivable from
    ``source_path`` plus the memory roots, and the same evidence/reason strings
    per entry; apply additionally duplicates every candidate verbatim in
    ``carried``. The response keeps per-decision ``source_path`` lists whole
    (the facts the model acts on); the delivery boundary still caps what the
    caller receives, and the report keeps the full records. No per-tool cap of
    its own survives beside the boundary: a capped list would recount there
    and lose the total.
    """
    compact = {key: value for key, value in full.items() if key not in {"candidates", "carried"}}
    decisions: dict[str, list[str]] = {}
    for candidate in full.get("candidates", []):
        decisions.setdefault(str(candidate.get("decision")), []).append(
            str(candidate.get("source_path"))
        )
    compact["decisions"] = decisions
    if "carried" in full:
        compact["carriedPaths"] = [
            str(candidate.get("source_path")) for candidate in full["carried"]
        ]
    if report_path is not None:
        compact["reportPath"] = report_path
    return compact


def memory_carryover_plan_payload(
    config: McpRuntimeConfig,
    selection: CarryoverSelection,
) -> dict[str, Any]:
    # A plan is a read and files no per-call report (rule 6); the answer is the plan with
    # no ``reportPath`` key. Only the mutating apply files its report (not adjudicated here).
    full = memory_carryover_plan_tool(config, selection)
    return _tool_payload(
        "memory_carryover_plan",
        compact_carryover_payload(full, None),
    )


def memory_carryover_apply_payload(
    config: McpRuntimeConfig,
    selection: CarryoverSelection,
    *,
    intent_note: str,
    include_review_required: list[str] | None = None,
    messages: CarryoverCommitMessages = DEFAULT_CARRYOVER_MESSAGES,
) -> dict[str, Any]:
    full = memory_carryover_apply_tool(
        config,
        selection,
        intent_note=intent_note,
        include_review_required=include_review_required,
        messages=messages,
    )
    report_path = write_tool_report(
        config.coordination_root, "memory_carryover_apply", full, label="apply"
    )
    return _tool_payload(
        "memory_carryover_apply",
        compact_carryover_payload(full, report_path.as_posix()),
    )
