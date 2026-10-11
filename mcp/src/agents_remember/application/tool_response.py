"""Complete one tool result at the application boundary.

The MCP adapter supplies the tool name and raw use-case result.  This service
selects the wire model, attaches lifecycle-wide state, finalizes the token
count, and records the completed call.  Domain observation therefore remains
below the application boundary; the adapter receives a protocol-ready mapping.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from agents_remember.application.next_step import next_step_for
from agents_remember.application.tool_answer_log import (
    DetailRequest,
    LineRequest,
    ToolCallRecord,
    _call_line_dict,
    append_call_line,
    current_tool_call,
    prepare_call_detail,
    prepare_log_folder,
    read_tool_caller,
    resolve_log_roots,
)
from agents_remember.kernel.agentic_settings import DEFAULT_AGENT_NOTIFIER_STALE_CUTOFF_SECONDS
from agents_remember.models.base import NextStep, ResponseEnvelope
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import (
    TOOL_ANSWER_OVERFLOW_MAX_TOKENS,
    bound_for,
    next_from_next_step,
    short_form,
    strip_envelope,
    truncate_lists,
)
from agents_remember.models.tools.tool_response import finalize_tool_response
from agents_remember.observer.ambient import AmbientLifecycle, ambient
from agents_remember.observer.tool_events import record_tool_completed
from agents_remember.serving.agent_notifier_heartbeat import agent_notifier_staleness_banner

_RESPONSE_PATH_FIELDS = ("contractPath", "enclosurePath")
_ARGUMENT_PATH_FIELDS = (
    "contract_path",
    "enclosure_path",
    "contractPath",
    "enclosurePath",
)


def _names_the_same_place(observed: str, expected: str) -> bool:
    """Whether two path spellings name the same contract file, or its immediate directory.

    **What the product actually emits.** Both spellings are set to the contract FILE:
    ``worktrees/modules/guidance.py::contract_next_args`` (:171-176) and
    ``_status_payload_with_landing`` (:443-444), ``application/lifecycle/start_result.py:121`` and
    ``…/lifecycle/reopen.py:104`` all pass ``contract.contract_path.as_posix()`` for
    ``enclosure_path``, and a real ``worktree_status`` response carries both spellings equal and
    the camelCase pair ``None``. So equality is the shape in force today.

    **Why the directory tolerance stays, and how far it goes.** ``enclosure_path`` is the
    *enclosure* address by name, and the enclosure IS the directory that holds the contract
    (``tasks/<repo>/<master>/enclosures/<leaf>/``) -- a future producer that emits the directory
    instead of the file is emitting the same task's address, and refusing it would withhold every
    hint from that producer. The tolerance is therefore exactly the immediate container:
    ``<file>`` and ``dirname(<file>)``. It is NOT "any ancestor": an earlier form of this
    function accepted any directory above the contract (measured: ``/coord/tasks/repo`` → True,
    ``/coord/tasks/repo/own-master`` → True), which is wider than the rule it claimed to be, is
    emitted by no producer, and is `260918-TSIP-L6`'s `F3`. A different task's contract still
    fails both spellings, which is the whole point of the guard.
    """

    if observed.rstrip("/") == expected.rstrip("/"):
        return True
    return PurePosixPath(observed.rstrip("/")) == PurePosixPath(expected.rstrip("/")).parent


def bound_next_step(response: ResponseEnvelope, step: NextStep | None) -> NextStep | None:
    """Omit guidance whose task address contradicts the response's exact address.

    The guidance a response carries is derived from the *process-global* ambient lifecycle
    (``application/next_step.py::next_step_for`` reads ``LifecycleState.enclosure``), while the
    response has its own address. When they disagree the guidance is about a different task, and
    a seat following it is sent into another task's enclosure -- which is what ``260918-TSIP``
    `T54` recorded. So the address check is a *guard*, and its two rules are:

    * a response that declares no contract path of its own cannot be validated, and its guidance
      is withheld rather than emitted unchecked. The old form returned the step here
      (``if not response_paths: return step``), which is precisely the hole `T54` came through;
    * every path spelling the guidance carries must name the response's own place -- the same
      contract file, or the directory that immediately contains it -- so one stale spelling is
      enough to withhold the hint. The old form required the guidance's path set to be exactly
      ``{expected}``, which let a hint carrying both spellings through whenever only one of them
      was stale.
    """

    if step is None or step.nextArgs is None:
        return step
    response_paths = {
        value
        for field in _RESPONSE_PATH_FIELDS
        if isinstance((value := getattr(response, field, None)), str) and value
    }
    observed = {
        str(step.nextArgs[field]) for field in _ARGUMENT_PATH_FIELDS if field in step.nextArgs
    }
    if not observed:
        # Guidance that names no artifact cannot contradict anything.
        return step
    if not response_paths:
        return None
    if not all(
        any(_names_the_same_place(path, expected) for expected in response_paths)
        for path in observed
    ):
        return None
    return step


def _agent_notifier_banner(amb: AmbientLifecycle) -> str | None:
    """Return the stale-agent-notifier banner without blocking a tool response."""
    try:
        return agent_notifier_staleness_banner(
            amb.root,
            now=datetime.now(UTC),
            stale_cutoff_seconds=DEFAULT_AGENT_NOTIFIER_STALE_CUTOFF_SECONDS,
        )
    except Exception:
        return None


def _attach_lifecycle_tail(
    response: ResponseEnvelope, amb: AmbientLifecycle, tool_name: str
) -> None:
    if (
        amb.current is not None
        and amb.current.state == "awaiting-developer"
        and tool_name != "lifecycle_turn_end_notification"
    ):
        amb.resume_from_await()
    # A refusal/recovery producer may supply an explicit nextStep alongside its top-level
    # recovery keys. Preserve that one authority instead of overwriting it with ambient phase
    # guidance derived from a contract the operation intentionally refused or just rewrote.
    step = response.nextStep or next_step_for(amb, tool_name)
    response.nextStep = bound_next_step(response, step)
    banner = _agent_notifier_banner(amb)
    response.agentNotifierBanner = banner
    response.supervisorBanner = banner


@dataclass(frozen=True)
class _LoggedAnswer:
    """Where one call's record went: detail location, errors, line budget."""

    log: str | None
    log_error: str | None
    unwritten_digest: str | None
    budget_bytes: int
    log_dir: Path


def _record_answer_log(record: ToolCallRecord, log_dir: Path, budget_bytes: int) -> _LoggedAnswer:
    """Run the folder and detail phases for one call (rule 3)."""
    is_enclosure = record.enclosure_dir is not None
    folder_error = prepare_log_folder(
        log_dir, record.keep_days, is_enclosure, day=record.day, now=record.now
    )
    if folder_error is not None:
        return _LoggedAnswer(None, folder_error, None, budget_bytes, log_dir)
    log, detail_error, unwritten_digest = prepare_call_detail(
        DetailRequest(
            log_dir=log_dir,
            budget_bytes=budget_bytes,
            tool_name=record.tool_name,
            whole_result=record.whole_result,
            result_digest=record.result_digest,
            detail_id=record.detail_id,
        ),
        day=record.day,
        now=record.now,
    )
    return _LoggedAnswer(log, detail_error, unwritten_digest, budget_bytes, log_dir)


def _append_answer_line(
    record: ToolCallRecord, logged: _LoggedAnswer, *, detail_name: str | None
) -> str | None:
    """Append one call's line whole under the folder budget (rule 3)."""
    line = _call_line_dict(
        record,
        detail_name=detail_name,
        unwritten_digest=logged.unwritten_digest,
        dropped=0,
    )
    return append_call_line(
        LineRequest(log_dir=logged.log_dir, budget_bytes=logged.budget_bytes, line=line),
        day=record.day,
        now=record.now,
    )


def _apply_answer_guard(
    answer: dict[str, Any],
    *,
    final_tokens: int,
    bound: int,
    log: str | None,
    log_error: str | None,
) -> tuple[dict[str, Any], bool, int]:
    """Run the guard on the final mapping the caller would get.

    Returns the delivered answer, whether the guard cut, and the delivered
    token count. The guard measures the final mapping including every
    log/logError change: attaching the location can itself take an
    exact-edge answer over its bound.
    """
    guard_cut = final_tokens > bound
    if not guard_cut:
        return answer, False, final_tokens
    short, _dropped_fields, _error_cut = short_form(
        answer,
        answer_tokens=final_tokens,
        log=log,
        log_error=log_error,
        count=count_response_tokens,
    )
    return short, True, count_response_tokens(short)


def _answer_bound(tool_name: str, arguments: Mapping[str, Any]) -> int:
    """The answer bound for one tool call (rule 4)."""
    task_operation = arguments.get("operation")
    return bound_for(
        tool_name, operation=task_operation if isinstance(task_operation, str) else None
    )


def _write_delivered_line(
    record: ToolCallRecord,
    logged: _LoggedAnswer,
    answer: dict[str, Any],
    delivered_tokens: int,
    log_error: str | None,
) -> tuple[dict[str, Any], int, bool]:
    """Write the delivered line; on failure attach, re-guard, retry once.

    Returns the delivered answer, its token count, and whether the guard
    cut. Every late failure re-runs the full guard on the final mapping: a
    late logError can push an ordinary exact-edge answer over its bound,
    and a second failure sentence can push a shed one back over. Once the
    answer is cut, the re-guard ceiling is the 400-token short-form ceiling,
    not the original tool bound: a late addition above 400 re-triggers the
    short form. The first attached error stays; later failures only add to
    the dropped count. A retried line always names the current delivered
    count and cut flag.
    """
    bound = _answer_bound(record.tool_name, record.arguments)
    detail_name = Path(logged.log).name if logged.log is not None else None
    guard_cut = record.guard_cut
    if guard_cut:
        bound = min(bound, TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
    lined = replace(record, answer_tokens=delivered_tokens, guard_cut=guard_cut)
    line_error = _append_answer_line(lined, logged, detail_name=detail_name)
    if line_error is None:
        return answer, delivered_tokens, guard_cut
    if log_error is None:
        answer["logError"] = line_error
    guarded, cut_now, delivered_tokens = _apply_answer_guard(
        answer,
        final_tokens=count_response_tokens(answer),
        bound=bound,
        log=logged.log,
        log_error=answer.get("logError") if isinstance(answer.get("logError"), str) else None,
    )
    answer = guarded
    guard_cut = guard_cut or cut_now
    if guard_cut:
        bound = min(bound, TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
    lined = replace(record, answer_tokens=delivered_tokens, guard_cut=guard_cut)
    retry_error = _append_answer_line(lined, logged, detail_name=detail_name)
    if retry_error is not None:
        # No replacement: the first failure stays attached; the retry only
        # counts as dropped. Re-guard in case the retry changed the mapping.
        guarded, cut_now, delivered_tokens = _apply_answer_guard(
            answer,
            final_tokens=count_response_tokens(answer),
            bound=bound,
            log=logged.log,
            log_error=answer.get("logError") if isinstance(answer.get("logError"), str) else None,
        )
        answer = guarded
        guard_cut = guard_cut or cut_now
    return answer, delivered_tokens, guard_cut


def complete_tool_response(
    tool_name: str,
    payload: dict[str, Any],
    *,
    config: Any | None = None,
    answer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate, enrich, count, shape the answer, log, and observe one result.

    The validated whole result keeps everything the function computed
    (``finalize_tool_response`` is unchanged, so the dashboard and the
    command line, which call the same functions, keep getting the whole
    result). The answer that leaves the server for an agent holds the
    outcome, what the caller needs for its next step, and where the record
    lies -- nothing else (MIK-R108 rules 1-2): the four envelope fields are
    stripped (the count goes to the log line), ``nextStep`` rides as
    ``next``, long lists are cut, and an answer over its bound gets the
    overflow short form with the whole result in the detail file (rule 4).
    Every call is recorded to the daily log (rule 3); a log write that fails
    does not fail the call.

    ``answer``, when given, is the tool's own compact status projection of
    that whole result: the caller receives the projection, while the detail
    file still records the whole result it was projected from. The guard
    still binds the projection, and the log line still carries the whole
    result's digest and byte size.
    """
    amb = ambient()
    finalized = finalize_tool_response(
        tool_name,
        payload,
        enrich=(
            (lambda response: _attach_lifecycle_tail(response, amb, tool_name))
            if amb is not None
            else None
        ),
    )
    call = current_tool_call()
    arguments: dict[str, Any] = dict(call.arguments) if call is not None else {}
    effective_config = config if config is not None else (call.config if call is not None else None)
    operation = finalized.get("operation")
    if not isinstance(operation, str):
        operation = None
    bound = _answer_bound(tool_name, arguments)
    shaped: dict[str, Any] = (
        dict(answer) if answer is not None else truncate_lists(strip_envelope(finalized))
    )
    answer = shaped
    raw_next_step = answer.pop("nextStep", None)
    next_value = next_from_next_step(raw_next_step if isinstance(raw_next_step, dict) else None)
    if next_value is not None:
        answer["next"] = next_value
    answer_tokens = count_response_tokens(answer)
    whole_text = _canonical_json(finalized)
    result_bytes = len(whole_text.encode("utf-8"))
    result_digest = hashlib.sha256(whole_text.encode("utf-8")).hexdigest()
    caller = read_tool_caller()
    dest = resolve_log_roots(effective_config, tool_name)
    log_dir = dest.enclosure_dir if dest.enclosure_dir is not None else dest.coord_log_dir
    ok = bool(finalized.get("ok", False))
    # Rule 3: the outcome is (ok, code). Only a refusal or a failure
    # carries a code (rule 1); a successful answer's state is not a code.
    code = str(finalized["code"]) if isinstance(finalized.get("code"), str) else None
    duration_ms = (time.perf_counter() - call.started_perf) * 1000.0 if call is not None else 0.0
    record = ToolCallRecord(
        tool_name=tool_name,
        operation=operation,
        arguments=arguments,
        caller=caller,
        ok=ok,
        code=code,
        duration_ms=duration_ms,
        answer_tokens=answer_tokens,
        whole_result=finalized,
        result_bytes=result_bytes,
        result_digest=result_digest,
        guard_cut=False,
        needs_detail=True,
        enclosure_dir=dest.enclosure_dir,
        coord_log_dir=dest.coord_log_dir,
        keep_days=dest.keep_days,
        max_bytes=dest.max_bytes,
        enclosure_max_bytes=dest.enclosure_max_bytes,
    )
    logged: _LoggedAnswer | None = None
    if log_dir is not None:
        logged = _record_answer_log(
            record,
            log_dir,
            dest.enclosure_max_bytes if dest.enclosure_dir is not None else dest.max_bytes,
        )
    log = logged.log if logged is not None else None
    log_error = logged.log_error if logged is not None else None
    # The guard measures the final mapping the caller would get, including
    # every log/logError change: attaching the location can itself take an
    # exact-edge answer over its bound.
    if log is not None:
        answer["log"] = log
    if log_error is not None:
        answer["logError"] = log_error
    final_tokens = count_response_tokens(answer)
    answer, guard_cut, delivered_tokens = _apply_answer_guard(
        answer, final_tokens=final_tokens, bound=bound, log=log, log_error=log_error
    )
    if logged is not None:
        lined = replace(record, answer_tokens=delivered_tokens, guard_cut=guard_cut)
        answer, delivered_tokens, _line_guard_cut = _write_delivered_line(
            lined, logged, answer, delivered_tokens, log_error
        )
    if amb is not None and amb.current is not None:
        # The earlier-kind active lifecycle owns this call: its event is written
        # as it is today, and no second one goes to the workspace river. (This
        # case ends when MIK-R113 removes the session lifecycle.)
        amb.emit_tool(tool_name, finalized, answer_tokens=delivered_tokens)
    else:
        # MIK-R114 rule 1: every other call is written to the workspace river,
        # with no active lifecycle and no call before it. The writer counts a
        # failed write instead of breaking the call. The count the completed
        # answer carries moved here to this leaf's delivered-answer count
        # (`delivered_tokens`).
        record_tool_completed(tool_name, finalized, delivered_tokens=delivered_tokens)
    return answer


def _canonical_json(payload: dict[str, Any]) -> str:
    """The canonical JSON form used for whole-result byte accounting."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
