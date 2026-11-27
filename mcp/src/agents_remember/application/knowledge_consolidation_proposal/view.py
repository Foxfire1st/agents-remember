"""The ``consolidation_proposal`` view of ``knowledge_read``: lists a consolidation pass could take.

The read names at most one subject and so chooses its way: ``masterId`` a finished master (the
prefix its leaves' owner ids share), ``recordId`` a family, ``sourcePath`` a folder, or nothing.
The answer stays inside the tool answer bound: per proposed family its id, title, member count and
the first of its attached rules in no family with their count, at most the list bound of families,
and the totals with their units. The report is a caller-readable file
(:func:`.knowledge_reports.write_report`) that holds every proposed family with all its attached
rules and every rule in no family the way proposes; it is reclaimed with the tool log folder it
lies in. A report that cannot be written refuses the read with the reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import apsw

from agents_remember.application.knowledge_reports import ReportWriteError, write_report
from agents_remember.application.published_intent import SelectedKnowledgeDataset
from agents_remember.memory.knowledge_index.query import IndexMismatchError, KnowledgeIndex
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import (
    TOOL_ANSWER_LIST_MAX,
    TOOL_ANSWER_OVERFLOW_MAX_TOKENS,
    bound_for,
)

from .first_written import first_written
from .history_facts import MasterWrites, last_consolidations, master_writes
from .proposal import (
    PassTimes,
    Proposal,
    ProposedFamily,
    attached_total,
    for_family,
    for_folder,
    for_master,
    oldest_first,
)
from .refusal import ProposalRefusal
from .store import ProposalStore, load_store

PROPOSAL_VIEW: Final = "consolidation_proposal"
REPORT_STEM: Final = "knowledge-consolidation-proposal"
# An answer row names this many attached rules and their count; the report names all of them.
ANSWER_RULES_MAX: Final = 8
ANSWER_TITLE_MAX_CHARS: Final = 80
_SHORTENED_MARK: Final = "…"
_READ_ON: Final = (
    "each row is a family with its member count and the rules in no family realized in its "
    "members' files; the report holds every family and every rule id"
)
# The tool boundary adds the log location and the next step after this answer is built.
_BOUNDARY_RESERVE: Final = TOOL_ANSWER_OVERFLOW_MAX_TOKENS


@dataclass(frozen=True)
class ProposalRequest:
    """What one read names: the memory root, its namespace, the row limit and one subject."""

    memory_root: str
    repository_id: str
    limit: int
    coordination_root: str | None
    master_id: str | None = None
    record_id: str | None = None
    source_path: str | None = None


def proposal_view(selected: SelectedKnowledgeDataset, request: ProposalRequest) -> dict[str, Any]:
    """The proposal of the way the request names, bounded, beside the report of its full lists.

    Raises :class:`ProposalRefusal` when the read names more than one subject, names one the tree
    does not hold, when the index or a history file cannot be read, or the report cannot be written.
    """

    tree = selected.memory_tree
    if tree is None:
        raise ProposalRefusal(
            "snapshot_unavailable",
            "the selection names no converted memory tree, so no family can be proposed",
        )
    store = _store_of(selected.database_path, tree.tree_key)
    proposal, master = _proposal(store, request)
    report = _write_report(proposal, master, request, tree.tree_key)
    shown = proposal.families[: min(max(1, request.limit), TOOL_ANSWER_LIST_MAX)]
    answer: dict[str, Any] = {
        "ok": True,
        "operation": "knowledge_read",
        "view": PROPOSAL_VIEW,
        "state": "view",
        "repositoryId": request.repository_id,
        "memoryRoot": Path(request.memory_root).as_posix(),
        "memoryTree": {"treeKey": tree.tree_key, "indexState": tree.index_state},
        "proposal": _proposal_block(proposal, master, ANSWER_RULES_MAX),
        "rows": [_row(family, ANSWER_RULES_MAX, shorten=True) for family in shown],
        "counts": {
            "families": len(proposal.families),
            "rowsReturned": len(shown),
            "attachedRules": attached_total(proposal.families),
            "rulesInNoFamily": len(proposal.rules_in_no_family),
        },
        "report": report.as_posix(),
        "readOn": {"how": _READ_ON, "familiesRemaining": len(proposal.families) - len(shown)},
    }
    return _fitted(answer, len(proposal.families))


def _proposal(
    store: ProposalStore, request: ProposalRequest
) -> tuple[Proposal, MasterWrites | None]:
    named = [
        name
        for name, value in (
            ("masterId", request.master_id),
            ("recordId", request.record_id),
            ("sourcePath", request.source_path),
        )
        if value
    ]
    if len(named) > 1:
        raise ProposalRefusal(
            "bad_selector",
            f"the read names {', '.join(named)}; name one of masterId, recordId or sourcePath, "
            "or none for the families longest without a consolidation",
        )
    memory_root = Path(request.memory_root)
    if request.master_id:
        writes = master_writes(memory_root, request.master_id, store.rule_origins)
        return for_master(store, request.master_id, writes.rules), writes
    if request.record_id:
        if request.record_id not in store.families:
            raise ProposalRefusal(
                "selector_absent",
                f"the selected tree holds no live family {request.record_id}; name a family id "
                "(FAM-…), a folder as sourcePath, or nothing",
            )
        return for_family(store, request.record_id), None
    if request.source_path:
        return for_folder(store, request.source_path.strip().removeprefix("./").strip("/")), None
    times = PassTimes(
        first_written=first_written(memory_root, set(store.families)),
        last_consolidated=last_consolidations(memory_root),
    )
    return oldest_first(store, times), None


def _row(family: ProposedFamily, rules_max: int | None, *, shorten: bool) -> dict[str, Any]:
    attached = family.attached_rules if rules_max is None else family.attached_rules[:rules_max]
    row: dict[str, Any] = {
        "family": family.family,
        "title": _shortened(family.title) if shorten else family.title,
        "members": family.members,
        "attachedRuleCount": len(family.attached_rules),
        "attachedRules": list(attached),
    }
    if family.touched_members is not None:
        row["membersWrittenByMaster"] = family.touched_members
    if family.first_written is not None:
        row["firstWritten"] = _iso(family.first_written)
    if family.last_consolidated is not None:
        row["lastConsolidated"] = _iso(family.last_consolidated)
    return row


def _proposal_block(
    proposal: Proposal, master: MasterWrites | None, rules_max: int | None
) -> dict[str, Any]:
    rules = proposal.rules_in_no_family
    block: dict[str, Any] = {
        "way": proposal.way,
        "named": proposal.named,
        "rulesInNoFamilyCount": len(rules),
        "rulesInNoFamily": list(rules if rules_max is None else rules[:rules_max]),
    }
    if master is not None:
        block["masterHistoryFiles"] = {"closed": master.closed_files, "open": master.open_files}
        block["masterRulesWritten"] = len(master.rules)
    return block


def _shortened(title: str) -> str:
    if len(title) > ANSWER_TITLE_MAX_CHARS:
        return title[: ANSWER_TITLE_MAX_CHARS - 1] + _SHORTENED_MARK
    return title


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _fitted(answer: dict[str, Any], families: int) -> dict[str, Any]:
    """Leave out the last rows until the answer fits its bound; the rest are counted remaining.

    There are at most the list bound of rows, so the answer is measured at most that many times.
    """

    budget = bound_for("knowledge_read") - _BOUNDARY_RESERVE
    rows: list[dict[str, Any]] = answer["rows"]
    while rows and count_response_tokens(answer) > budget:
        rows.pop()
        answer["counts"]["rowsReturned"] = len(rows)
        answer["readOn"]["familiesRemaining"] = families - len(rows)
    return answer


def _store_of(index_path: Path, tree_key: str) -> ProposalStore:
    try:
        with KnowledgeIndex(index_path, expected_key=tree_key) as index:
            return load_store(index)
    except (IndexMismatchError, apsw.Error) as error:
        raise ProposalRefusal(
            "snapshot_unavailable",
            f"the index of the selected tree {tree_key} cannot be read ({error}); read the view "
            "again",
        ) from error


def _write_report(
    proposal: Proposal, master: MasterWrites | None, request: ProposalRequest, tree_key: str
) -> Path:
    payload = {
        "operation": "knowledge_read",
        "view": PROPOSAL_VIEW,
        "memoryRoot": request.memory_root,
        "treeKey": tree_key,
        "proposal": _proposal_block(proposal, master, None),
        "totals": {
            "families": len(proposal.families),
            "attachedRules": attached_total(proposal.families),
            "rulesInNoFamily": len(proposal.rules_in_no_family),
        },
        "families": [_row(family, None, shorten=False) for family in proposal.families],
    }
    try:
        return write_report(REPORT_STEM, payload, coordination_root=request.coordination_root)
    except ReportWriteError as error:
        detail = error.reason
        if error.residual is None:
            detail += "; nothing partial was left behind"
        raise ProposalRefusal("report-unwritable", detail) from error
