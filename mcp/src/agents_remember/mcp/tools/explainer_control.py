"""Payload builder for ``explainer_control`` (MIK-R186 rule 6, MIK-R189 rule 7).

The architect's one tool over a leaf's explainer. It answers three actions, and the caller check is
the whole point of two of them:

* ``update`` and ``retire`` are accepted only from the hosted architect seat the master-to-architect
  record names for the leaf's master (MIK-R186 rule 6). Any other caller, and every
  call when no record names a seat, is refused ``not-this-masters-architect``, and the refusal says
  how to take the master over.
* ``claim`` is open to any hosted architect seat (MIK-R189 rule 7 step 3). It writes the record with
  act ``claim`` and then moves that master's reports: each report still held for the previous seat
  is withdrawn from that seat's store and sent once to the claiming seat, even while the previous
  seat is live.

The caller is this tool server's own agent binding -- an environment, never an argument -- so a
seat can no more name another agent than it can name itself a different role. What ``update`` and
``retire`` then do is the seat service's work; this module composes it exactly as
:mod:`agents_remember.serving.explainer` composes the page's routes and calls it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.application.agent_binding import AgentBinding, read_agent_binding
from agents_remember.application.master_architect_record import (
    BRIDGE_REFUSED,
    BRIDGE_TIMEOUT,
    CLAIM,
    EXECUTIONS_DIRECTORY,
    HELD_STORE_SCHEMA,
    PROVIDER_LIMIT_REASON,
    SEND_OUTCOME_UNKNOWN,
    HeldMessage,
    HeldMessageCommitted,
    HeldStoreFull,
    MasterReportTransition,
    append_master_report_transition,
    explainer_control_bridge_send,
    explainer_control_host,
    extract_report_file_path,
    foreign_logged_report_ids,
    hold_message,
    mark_report_undelivered,
    master_architect_path,
    master_report_log_path,
    provider_limit_hold,
    read_execution_receipt,
    read_master_report_states,
    read_record,
    read_record_architect,
    store_path_for,
    waiting_total,
    withdraw_message,
    write_record,
)
from agents_remember.application.review_planned_comparison import (
    explainer_application_ports,
)
from agents_remember.application.review_tree_knowledge import read_review_trees
from agents_remember.canvas.explainer_paths import receipt_id_segment
from agents_remember.canvas.explainer_service import (
    ExplainerDeps,
    ExplainerHost,
    ExplainerRefused,
    LeafScope,
    retire_seat,
    update_seat,
)
from agents_remember.canvas.paths import PathRefused
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_identity import canonical_role
from agents_remember.serving.explainer import (
    ExplainerComposition,
    ExplainerWiring,
    explainer_composition,
)
from agents_remember.serving.projections.host_activity import agent_in_turn

from .base import _tool_payload

__all__ = [
    "ACTION_CLAIM",
    "ACTION_RETIRE",
    "ACTION_UPDATE",
    "ExplainerControlCall",
    "explainer_control_payload",
]

ACTION_UPDATE = "update"
ACTION_RETIRE = "retire"
ACTION_CLAIM = CLAIM
ACTIONS = (ACTION_UPDATE, ACTION_RETIRE, ACTION_CLAIM)

#: The reasons this tool answers by name.
NOT_AN_ARCHITECT = "not-an-architect"
NOT_THIS_MASTERS_ARCHITECT = "not-this-masters-architect"
UNKNOWN_ACTION = "unknown-action"
LEAF_REQUIRED = "leaf-required"
RECEIPT_REQUIRED = "receipt-id-required"
EXPLAINER_UNAVAILABLE = "explainer-unavailable"
SELECTION_UNAVAILABLE = "selection-unavailable"

ARCHITECT_ROLE = "architect"
#: What a claimed report carries as why it waited, in the claiming seat's own store.
CLAIMED_REPORT_REASON = "claimed-by-this-master's-architect"
SENDER_LINE = "From explainer · {task} · agent "
#: One held store holds at most twenty reports; anything larger is not one this product wrote.
MAX_HELD_STORE_BYTES = 1024 * 1024
#: One send's answer: the host took the text, or it did not (or returned a named state).
SendReport = Callable[[str, str, str], Any]


@dataclass(frozen=True)
class ExplainerControlCall:
    """One call: the action, and the leaf/master/repository selection it names."""

    action: str
    repo_id: str
    task_name: str
    leaf_id: str | None = None
    #: A Retire's receipt id, which the same retire repeats (MIK-R186 rule 8).
    receipt_id: str | None = None


@dataclass(frozen=True)
class _Collaborators:
    """What a call is answered through: the caller's environment, a sender, and the clock."""

    environment: Mapping[str, str] | None = None
    send: SendReport | None = None
    now: Callable[[], str] | None = None


class _Refused(Exception):
    """A refusal the answer carries by name, never a server error."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def explainer_control_payload(
    config: McpRuntimeConfig,
    call: ExplainerControlCall,
    *,
    environment: Mapping[str, str] | None = None,
    send: SendReport | None = None,
    now: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Run one ``explainer_control`` call and answer it in the shared tool envelope."""

    collaborators = _Collaborators(environment=environment, send=send, now=now)
    return _tool_payload("explainer_control", _answer(config, call, collaborators))


def _answer(
    config: McpRuntimeConfig, call: ExplainerControlCall, collaborators: _Collaborators
) -> dict[str, Any]:
    try:
        _require_action(call.action)
        caller = _caller(collaborators.environment)
        if call.action == ACTION_CLAIM:
            return _claim(config, call, caller, collaborators)
        return _seat_action(config, call, caller)
    except _Refused as refused:
        return _refusal(call, refused)
    except ExplainerRefused as refused:
        return _refusal(call, _Refused(refused.reason, refused.detail))
    except ValueError as error:
        # A selection that cannot be resolved is a refusal, never a server error (MIK-R185 v2
        # rule 3): the seat service's own path and document readers raise this way.
        return _refusal(call, _Refused(SELECTION_UNAVAILABLE, str(error)))


def _require_action(action: object) -> str:
    """MIK-R186 rule 6 / MIK-R189 rule 7: refuse any unknown action before lookup or mutation."""

    if isinstance(action, str) and action in ACTIONS:
        return action
    raise _Refused(
        UNKNOWN_ACTION,
        f"unknown explainer_control action {action!r}; expected one of {', '.join(ACTIONS)}",
    )


def _caller(environment: Mapping[str, str] | None) -> AgentBinding | None:
    """The hosted seat this tool server was started for, or ``None`` for a process without one."""

    try:
        return read_agent_binding(environment)
    except ValueError:
        return None


# -- claim (MIK-R189 rule 7 step 3) --------------------------------------------------------


def _claim(
    config: McpRuntimeConfig,
    call: ExplainerControlCall,
    caller: AgentBinding | None,
    collaborators: _Collaborators,
) -> dict[str, Any]:
    """Take one master over: write the record, then move the reports the last seat still holds."""

    if caller is None or canonical_role(caller.role) != ARCHITECT_ROLE:
        raise _Refused(NOT_AN_ARCHITECT, _not_an_architect(call, caller))
    root = config.coordination_root
    path = master_architect_path(root, call.repo_id, call.task_name)
    previous = read_record_architect(path)
    stamp = _stamp(collaborators)
    record = write_record(
        path,
        architect_agent_id=caller.agent_id,
        act=ACTION_CLAIM,
        at=stamp,
    )
    moved = _move_held_reports(
        config,
        call,
        previous,
        caller.agent_id,
        collaborators,
    )
    return {
        "ok": True,
        "operation": "explainer_control",
        "action": ACTION_CLAIM,
        "state": "claimed",
        "repository": call.repo_id,
        "master": call.task_name,
        "architectAgentId": caller.agent_id,
        "record": record,
        "claim": moved,
    }


def _not_an_architect(call: ExplainerControlCall, caller: AgentBinding | None) -> str:
    if caller is None:
        return (
            f"this call reached the master's record with no agent binding, and `claim` on "
            f"{call.task_name} is open to a hosted architect seat alone; start the claim from an "
            "architect's own tool server"
        )
    return (
        f"agent {caller.agent_id} is a {caller.role}, not an architect: `claim` on "
        f"{call.task_name} is open to a hosted architect seat alone, and the record names a seat "
        "only for the seat that claims it"
    )


@dataclass(frozen=True)
class _HeldReportCandidate:
    holder_id: str
    holder_folder: Path
    row: dict[str, Any]
    logged: dict[str, Any] | None
    log_path: Path


@dataclass(frozen=True)
class _ClaimTransferContext:
    config: McpRuntimeConfig
    claimer: str
    landing: Path | None
    claimer_archived: bool
    send: SendReport | None
    moment: str


def _move_held_reports(
    config: McpRuntimeConfig,
    call: ExplainerControlCall,
    previous: str | None,
    claimer: str,
    collaborators: _Collaborators,
) -> dict[str, Any]:
    """Withdraw this master's held reports from earlier seats and deliver or hold each once."""

    moment = _stamp(collaborators)
    candidates = _collect_held_report_candidates(config, call, previous, claimer)
    if not candidates:
        if previous is None or previous == claimer:
            return _claim_move(0, [], 0, 0, "no earlier seat holds a report of this master")
        return _claim_move(0, [], 0, 0, f"agent {previous} holds no report of this master")
    landing, claimer_archived = _claimer_destination(config, claimer)
    transfer_ctx = _ClaimTransferContext(
        config=config,
        claimer=claimer,
        landing=landing,
        claimer_archived=claimer_archived,
        send=collaborators.send,
        moment=moment,
    )
    withdrawn: list[str] = []
    counts = {"reported": 0, "held": 0, "sent, outcome unknown": 0, "not reported": 0}
    for item in candidates:
        ended = _withdraw(item.holder_folder, item.holder_id, item.row)
        if ended is None:
            continue
        withdrawn.append(str(item.row["id"]))
        outcome = _transfer_withdrawn_report(transfer_ctx, item, ended)
        counts[outcome] = counts.get(outcome, 0) + 1
    result = _claim_move(len(candidates), withdrawn, counts["reported"], counts["held"], None)
    result["unknown"] = counts["sent, outcome unknown"]
    result["notReported"] = counts["not reported"]
    return result


def _claim_move(
    found: int,
    withdrawn: list[str],
    sent: int,
    held: int,
    detail: str | None,
) -> dict[str, Any]:
    return {
        "found": found,
        "withdrawn": withdrawn,
        "sent": sent,
        "held": held,
        "unknown": 0,
        "notReported": 0,
        "detail": detail,
    }


def _collect_held_report_candidates(
    config: McpRuntimeConfig,
    call: ExplainerControlCall,
    previous: str | None,
    claimer: str,
) -> list[_HeldReportCandidate]:
    """Resolve this master's held reports from durable report state and earlier seats' stores."""

    root = config.coordination_root
    default_log = master_report_log_path(root, call.repo_id, call.task_name)
    logged_states = read_master_report_states(root, call.repo_id, call.task_name)
    foreign_ids = foreign_logged_report_ids(root, call.repo_id, call.task_name)
    holders = _candidate_holders(previous, claimer, logged_states)
    candidates: list[_HeldReportCandidate] = []
    seen_ids: set[str] = set()
    for holder_id in holders:
        folder = _agent_workspace(config, holder_id)
        for row in _held_reports(folder, holder_id, call.task_name):
            report_id = str(row["id"])
            if report_id in seen_ids or not _admitted_held_row(
                row, report_id, call.repo_id, foreign_ids, logged_states
            ):
                continue
            logged = logged_states.get(report_id)
            assert folder is not None
            log_path = (
                Path(str(logged["_logPath"]))
                if logged is not None and isinstance(logged.get("_logPath"), str)
                else default_log
            )
            seen_ids.add(report_id)
            candidates.append(
                _HeldReportCandidate(
                    holder_id=holder_id,
                    holder_folder=folder,
                    row=row,
                    logged=logged,
                    log_path=log_path,
                )
            )
    return candidates


def _candidate_holders(
    previous: str | None, claimer: str, logged_states: Mapping[str, dict[str, Any]]
) -> list[str]:
    holders: list[str] = []
    if previous is not None and previous != claimer:
        holders.append(previous)
    for entry in logged_states.values():
        holder = entry.get("architect")
        if (
            entry.get("state") == "held"
            and isinstance(holder, str)
            and holder
            and holder != claimer
            and holder not in holders
        ):
            holders.append(holder)
    return holders


def _admitted_held_row(
    row: Mapping[str, Any],
    report_id: str,
    repo_id: str,
    foreign_ids: set[str],
    logged_states: Mapping[str, dict[str, Any]],
) -> bool:
    if report_id in foreign_ids:
        return False
    row_repo = row.get("repository")
    if isinstance(row_repo, str) and row_repo and row_repo != repo_id:
        return False
    logged = logged_states.get(report_id)
    if logged is not None:
        return logged.get("state") == "held"
    return not logged_states


def _claimer_destination(config: McpRuntimeConfig, claimer: str) -> tuple[Path | None, bool]:
    """The claiming architect's workspace folder and whether its launch record is archived."""

    receipts = _receipts(config, claimer)
    if not receipts:
        return None, False
    current = receipts[0]
    workspace = current.get("workspace")
    folder = workspace.get("path") if isinstance(workspace, dict) else None
    path = Path(folder) if isinstance(folder, str) and folder else None
    archived = str(current.get("status") or "") == "archived"
    return path, archived


def _pre_send_hold(
    config: McpRuntimeConfig, claimer: str, landing: Path | None
) -> tuple[str, str | None, str | None] | None:
    """Shared limit/busy/permission check before calling the host sender (MIK-R190 rule 9)."""

    limited = provider_limit_hold(config, claimer)
    if limited is not None:
        until = str(limited.get("limitedUntil") or "") or None
        return PROVIDER_LIMIT_REASON, None, until
    receipts = _receipts(config, claimer)
    current = receipts[0] if receipts else {}
    status = str(current.get("status") or "")
    status_reasons = {
        "held": "held-seat-unopened",
        "starting": "start-unfinished",
        "unknown": "start-unfinished",
    }
    if status in status_reasons:
        return status_reasons[status], None, None
    permission = current.get("pendingPermission")
    if isinstance(permission, str) and permission:
        return "permission-pending", permission, None
    if landing is not None and waiting_total(landing, claimer) > 0:
        return "held-behind-waiting", None, None
    if agent_in_turn(config, claimer):
        return "recipient-in-turn", None, None
    return None


def _transfer_withdrawn_report(
    ctx: _ClaimTransferContext,
    item: _HeldReportCandidate,
    ended: dict[str, Any],
) -> str:
    """Deliver, hold at destination, record unknown outcome, or fall back to tray/restore."""

    report_id = str(ended["id"])
    if ctx.claimer_archived:
        return _fallback_or_restore(
            item,
            ended,
            reason="recipient-archived",
            detail=f"claiming architect {ctx.claimer} is archived",
            moment=ctx.moment,
        )
    pre_hold = _pre_send_hold(ctx.config, ctx.claimer, ctx.landing)
    if pre_hold is not None:
        return _hold_at_claimer_or_fallback(ctx, item, ended, pre_hold)
    outcome, reason, detail = _send_to_claimer(
        ctx.config, ctx.send, ctx.claimer, str(ended.get("text") or ""), report_id
    )
    if outcome in {"reported", "sent, outcome unknown"}:
        append_master_report_transition(
            item.log_path,
            MasterReportTransition(
                report_id=report_id,
                state=outcome,
                at=ctx.moment,
                logged=item.logged,
                row=ended,
                architect=ctx.claimer,
                reason=reason if outcome == "sent, outcome unknown" else None,
            ),
        )
        return outcome
    if outcome == "held":
        return _hold_at_claimer_or_fallback(
            ctx, item, ended, (reason or CLAIMED_REPORT_REASON, None, None)
        )
    return _fallback_or_restore(
        item,
        ended,
        reason=reason or "bridge-error",
        detail=detail or "report delivery to claiming architect failed",
        moment=ctx.moment,
    )


def _classify_send_exception(error: Exception) -> tuple[str, str | None, str | None]:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        if code in {BRIDGE_TIMEOUT, SEND_OUTCOME_UNKNOWN}:
            return "sent, outcome unknown", code, str(error)
        if code == BRIDGE_REFUSED:
            return "not reported", "bridge-error", str(error)
        return "held", "host-unreachable", str(error)
    if isinstance(error, TimeoutError):
        return "sent, outcome unknown", SEND_OUTCOME_UNKNOWN, str(error)
    return "not reported", "bridge-error", str(error)


def _classify_custom_answer(answer: object) -> tuple[str, str | None, str | None]:
    if answer in {"sent, outcome unknown", "unknown", SEND_OUTCOME_UNKNOWN}:
        return "sent, outcome unknown", SEND_OUTCOME_UNKNOWN, None
    if answer == "not reported":
        return "not reported", "bridge-error", "sender answered not reported"
    if answer is True or answer == "reported":
        return "reported", None, None
    return "held", CLAIMED_REPORT_REASON, None


def _classify_bridge_reply(reply: dict[str, object]) -> tuple[str, str | None, str | None]:
    delivery = reply.get("delivery") if isinstance(reply, dict) else None
    if isinstance(delivery, dict) and delivery.get("delivered") is True:
        return "reported", None, None
    refused = str(delivery.get("refused") or "") if isinstance(delivery, dict) else ""
    if refused in {"archived", "not_found", "missing"}:
        return "not reported", "recipient-archived", f"the host answered {refused!r}"
    if refused in {"busy", "in_turn", "permission", "starting"}:
        return "held", "recipient-busy", f"the host answered {refused!r}"
    return "held", CLAIMED_REPORT_REASON, None


def _send_to_claimer(
    config: McpRuntimeConfig,
    send: SendReport | None,
    claimer: str,
    text: str,
    message_id: str,
) -> tuple[str, str | None, str | None]:
    """Run one delivery attempt and classify ``reported``, ``held``, ``sent, outcome unknown``, or ``not reported``."""

    if send is not None:
        try:
            answer = send(claimer, text, message_id)
        except Exception as error:
            return _classify_send_exception(error)
        return _classify_custom_answer(answer)
    try:
        reply = explainer_control_bridge_send(
            config, "agent-send", {"agentId": claimer, "text": text, "messageId": message_id}
        )
    except Exception as error:
        return _classify_send_exception(error)
    return _classify_bridge_reply(reply)


def _hold_at_claimer_or_fallback(
    ctx: _ClaimTransferContext,
    item: _HeldReportCandidate,
    ended: dict[str, Any],
    hold_spec: tuple[str, str | None, str | None],
) -> str:
    """Hold at the claiming seat's store; on missing/full store, fall back to tray or restore custody."""

    reason, permission, limited_until = hold_spec
    if ctx.landing is None or not ctx.landing.is_dir():
        return _fallback_or_restore(
            item,
            ended,
            reason="destination-unavailable",
            detail=f"claiming architect {ctx.claimer} has no writable workspace for held messages",
            moment=ctx.moment,
        )
    try:
        taken = hold_message(
            ctx.landing,
            ctx.claimer,
            HeldMessage(
                sender_line=str(ended.get("senderLine") or ""),
                sender_agent_id=str(ended["senderAgentId"]),
                text=str(ended.get("text") or ""),
                reason=reason,
                permission=permission,
                attempted_message_id=str(ended["id"]),
                limited_until=limited_until,
            ),
        )
    except HeldStoreFull as full:
        return _fallback_or_restore(
            item,
            ended,
            reason="held-store-full",
            detail=str(full),
            moment=ctx.moment,
        )
    except (ValueError, OSError) as error:
        return _fallback_or_restore(
            item,
            ended,
            reason="destination-unwritable",
            detail=str(error),
            moment=ctx.moment,
        )
    append_master_report_transition(
        item.log_path,
        MasterReportTransition(
            report_id=str(ended["id"]),
            state="held",
            at=ctx.moment,
            logged=item.logged,
            row=ended,
            architect=ctx.claimer,
            reason=reason,
            limited_until=str(taken.get("limitedUntil") or limited_until or "") or None,
        ),
    )
    return "held"


def _fallback_or_restore(
    item: _HeldReportCandidate,
    ended: dict[str, Any],
    *,
    reason: str,
    detail: str,
    moment: str,
) -> str:
    """Return the report to the developer's tray (`not reported`), or restore previous custody."""

    report_id = str(ended["id"])
    report_file = extract_report_file_path(ended, item.logged)
    if report_file is not None and report_file.is_file():
        kept = mark_report_undelivered(report_file, refusal=reason, refusal_detail=detail)
        append_master_report_transition(
            item.log_path,
            MasterReportTransition(
                report_id=report_id,
                state="not reported",
                at=moment,
                logged=item.logged,
                row=ended,
                file_path=kept,
                refusal=reason,
            ),
        )
        return "not reported"
    # If no report file exists on disk (e.g. a bare store fixture), restore the row to the
    # previous seat's store and keep its state as ``held`` so a retry can still move it.
    restored = False
    with suppress(HeldStoreFull, ValueError, OSError):
        hold_message(
            item.holder_folder,
            item.holder_id,
            HeldMessage(
                sender_line=str(ended.get("senderLine") or ""),
                sender_agent_id=str(ended["senderAgentId"]),
                text=str(ended.get("text") or ""),
                reason=str(ended.get("reason") or "recipient-busy"),
                permission=(
                    str(ended["permission"]) if isinstance(ended.get("permission"), str) else None
                ),
                attempted_message_id=report_id,
                limited_until=(
                    str(ended["limitedUntil"])
                    if isinstance(ended.get("limitedUntil"), str)
                    else None
                ),
            ),
        )
        restored = True
    append_master_report_transition(
        item.log_path,
        MasterReportTransition(
            report_id=report_id,
            state="held" if restored else "not reported",
            at=moment,
            logged=item.logged,
            row=ended,
            architect=item.holder_id if restored else None,
            reason=reason if restored else None,
            refusal=None if restored else reason,
        ),
    )
    return "not reported"


def _withdraw(folder: Path | None, agent_id: str, row: Mapping[str, Any]) -> dict[str, Any] | None:
    """End one waiting report in the previous seat's store; ``None`` when it is not there."""

    if folder is None:
        return None
    try:
        return withdraw_message(folder, agent_id, str(row["id"]), str(row["senderAgentId"]))
    except (HeldMessageCommitted, ValueError):
        return None


def _held_reports(folder: Path | None, agent_id: str, master: str) -> list[dict[str, Any]]:
    """Every report of one master that still waits in one seat's store (rule 7 step 3)."""

    if folder is None:
        return []
    try:
        path = store_path_for(folder, agent_id)
        if path.stat().st_size > MAX_HELD_STORE_BYTES:
            return []
        store = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    if not isinstance(store, dict) or store.get("schema") != HELD_STORE_SCHEMA:
        return []
    prefix = SENDER_LINE.format(task=master)
    return [row for row in store.get("messages") or [] if _is_report_of(row, prefix)]


def _is_report_of(row: object, prefix: str) -> bool:
    """Whether one waiting row is a report this master's explainer sent."""

    if not isinstance(row, dict):
        return False
    line = row.get("senderLine")
    return (
        isinstance(line, str)
        and line.startswith(prefix)
        and isinstance(row.get("id"), str)
        and isinstance(row.get("senderAgentId"), str)
        and isinstance(row.get("text"), str)
    )


# -- update and retire (MIK-R186 rule 6) ---------------------------------------------------


def _seat_action(
    config: McpRuntimeConfig, call: ExplainerControlCall, caller: AgentBinding | None
) -> dict[str, Any]:
    """Update or retire one leaf's explainer, accepted only from the seat the record names."""

    root = config.coordination_root
    record = read_record(master_architect_path(root, call.repo_id, call.task_name))
    if record is None:
        raise _Refused(
            NOT_THIS_MASTERS_ARCHITECT,
            f"no master-to-architect record names a seat for {call.task_name}, so `{call.action}` "
            "is accepted from none; take the master over with explainer_control action `claim`",
        )
    architect = record["architectAgentId"]
    if caller is None or caller.agent_id != architect:
        raise _Refused(
            NOT_THIS_MASTERS_ARCHITECT,
            f"the master-to-architect record names agent {architect} for {call.task_name}, and "
            f"this call is from {_caller_name(caller)}: `{call.action}` is accepted from that seat "
            "alone; take the master over with explainer_control action `claim`",
        )
    if not call.leaf_id:
        raise _Refused(
            LEAF_REQUIRED, f"`{call.action}` names the leaf whose explainer it acts on (leaf_id)"
        )
    scope = LeafScope(repository_id=call.repo_id, task=call.task_name, leaf_id=call.leaf_id)
    answer: dict[str, Any] = {
        "ok": True,
        "operation": "explainer_control",
        "action": call.action,
        "repository": call.repo_id,
        "master": call.task_name,
        "leaf": call.leaf_id,
        "architectAgentId": architect,
        "record": record,
    }
    deps = _deps(config, call)
    if call.action == ACTION_UPDATE:
        update = update_seat(deps, scope)
        return {
            **answer,
            "state": "current" if update.current else "updated",
            "update": update.as_record(),
        }
    if call.action != ACTION_RETIRE:
        raise _Refused(
            UNKNOWN_ACTION,
            f"unknown explainer_control action {call.action!r}; expected one of {', '.join(ACTIONS)}",
        )
    if not call.receipt_id:
        raise _Refused(
            RECEIPT_REQUIRED,
            "a Retire carries its receipt id, so a repeat finishes the same retire (rule 8)",
        )
    # MIK-R186 rule 8: the id names a file beside the leaf's receipts, so it is one path segment and
    # never a path -- the same guard the HTTP route applies, applied again at the architect's tool.
    try:
        receipt_id = receipt_id_segment(call.receipt_id)
    except PathRefused as refused:
        raise _Refused(RECEIPT_REQUIRED, str(refused)) from refused
    retire = retire_seat(deps, scope, receipt_id=receipt_id)
    return {**answer, "state": "retired", "retire": retire.as_record()}


def _caller_name(caller: AgentBinding | None) -> str:
    return "a process with no agent binding" if caller is None else f"agent {caller.agent_id}"


def _deps(config: McpRuntimeConfig, call: ExplainerControlCall) -> ExplainerDeps:
    """The seat service, composed exactly as the dashboard's review routes compose it."""

    host = _explainer_host(config)
    composition: ExplainerComposition = explainer_composition(
        config,
        lambda query: read_review_trees(config, query),
        wiring=None
        if host is None
        else ExplainerWiring(
            host=host,
            application=explainer_application_ports(),
            contract_for=lambda _scope: _contract(config, call),
        ),
    )
    if composition.deps is None:
        raise _Refused(
            EXPLAINER_UNAVAILABLE,
            "no explainer service can be composed in this process: it needs the comparison owner "
            "and the host, and one of them is missing",
        )
    return composition.deps


def _explainer_host(config: McpRuntimeConfig) -> ExplainerHost | None:
    """The host adapter the dashboard composes for the same seat service."""

    return explainer_control_host(config)


def _contract(config: McpRuntimeConfig, call: ExplainerControlCall) -> Path | None:
    """This master's series contract, read for a planning leaf's planned comparison (rule 4).

    The master document names its own contract (`seriesContractPath`); a master that predates that field
    falls back to the file at its folder's root. Nothing here borrows another line's contract.
    """

    folder = config.coordination_root / "tasks" / call.repo_id / call.task_name
    named = folder / "task.json"
    if named.is_file():
        try:
            document = json.loads(named.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            document = {}
        declared = document.get("seriesContractPath") if isinstance(document, dict) else None
        if isinstance(declared, str) and declared and Path(declared).is_file():
            return Path(declared)
    fallback = folder / "series-contract.md"
    return fallback if fallback.is_file() else None


# -- shared: the answer, the clock, the receipt lookup --------------------------------------


def _refusal(call: ExplainerControlCall, refused: _Refused) -> dict[str, Any]:
    """One refusal: its own name in ``reason`` and ``code``, and how to take the master over."""

    return {
        "ok": False,
        "operation": "explainer_control",
        "action": call.action,
        "state": "refused",
        "repository": call.repo_id,
        "master": call.task_name,
        "leaf": call.leaf_id,
        "reason": refused.reason,
        "detail": refused.detail,
        "code": refused.reason,
    }


def _stamp(collaborators: _Collaborators) -> str:
    if collaborators.now is None:
        return datetime.now(UTC).isoformat()
    return collaborators.now()


def _agent_workspace(config: McpRuntimeConfig, agent_id: str) -> Path | None:
    """The working folder one agent's launch record names, so its held store can be found."""

    for receipt in _receipts(config, agent_id):
        workspace = receipt.get("workspace")
        folder = workspace.get("path") if isinstance(workspace, dict) else None
        if isinstance(folder, str) and folder:
            return Path(folder)
    return None


def _receipts(config: McpRuntimeConfig, agent_id: str) -> list[dict[str, Any]]:
    """Every readable execution receipt that names this agent, a current one before a history one."""

    found: list[tuple[bool, dict[str, Any]]] = []
    for path in _receipt_paths(config):
        try:
            receipt = read_execution_receipt(path)
        except (OSError, ValueError):
            continue
        if isinstance(receipt, dict) and receipt.get("agentId") == agent_id:
            found.append((path.parent.name != "history", receipt))
    found.sort(key=lambda row: row[0], reverse=True)
    return [receipt for _current, receipt in found]


def _receipt_paths(config: McpRuntimeConfig) -> Iterator[Path]:
    """Every execution receipt of this line, current ones and archived ones."""

    coordination = config.coordination_root
    roots = [coordination / "notes" / "reports" / EXECUTIONS_DIRECTORY]
    roots.extend(sorted((coordination / "tasks").glob(f"**/notes/reports/{EXECUTIONS_DIRECTORY}")))
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.json")):
            if "message-bindings" not in path.relative_to(root).parts:
                yield path
