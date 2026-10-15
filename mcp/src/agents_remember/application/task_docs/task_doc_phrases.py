"""Task-document answer phrases (MIK-R111 rule 2).

The unit one write changed and its new value, in a phrase. Extracted from
task_doc_tools.py under the file-size rail (F01.19): phrase builders have
their single home here; the tools module imports ``_updated_phrase`` (and
``_MUTABLE_FIELDS``, the set_field allowlist the phrases share with the
applier so an ignored field is never claimed changed).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from agents_remember.application.task_docs.task_doc_steps import _STEP_UPDATE_KEYS

if TYPE_CHECKING:
    from agents_remember.application.task_docs.task_doc_tools import TaskDocEdit

# set_field may only touch these (scalars + flat string lists); structural edits
# go through create / replace / the step plane / append_decision.
_MUTABLE_FIELDS = frozenset(
    {
        "title",
        "type",
        "status",
        "objective",
        "design",
        "nonGoals",
        "acceptance",
        "waitsFor",
        "requirements",
        "openQuestions",
        "references",
        "lifecycleId",
        "master",
        "codeExamplesNote",
        "statusNote",
        "orchestrates",
        "integrationBranch",
        "executionNature",
        "executionGraph",
        "knowledgeMaintenanceScope",
        "expectedKnowledgeEffects",
    }
)

# A new value rides the phrase truncated to this many characters: short
# values verbatim (tests assert them exactly), long ones bounded.
_VALUE_MAX_CHARS = 120


def _short(value: object) -> str:
    """One bounded rendering of a new value for a phrase."""
    text = value if isinstance(value, str) else repr(value)
    if len(text) <= _VALUE_MAX_CHARS:
        return text
    return text[:_VALUE_MAX_CHARS] + "…"


def _phrase_create(_edit: TaskDocEdit, result: dict[str, Any]) -> str:
    return f"created {result.get('slug')}".strip()


def _phrase_replace(_edit: TaskDocEdit, result: dict[str, Any]) -> str:
    return f"replaced {result.get('slug')}".strip()


def _phrase_set_status(edit: TaskDocEdit, _result: dict[str, Any]) -> str:
    # The applier takes the status from the fields, so the phrase does too;
    # the result may not carry it.
    return f"status set to {_short((edit.fields or {}).get('status'))}"


def _step_where(payload: dict[str, Any]) -> str:
    step_id = payload.get("id")
    parent = str(payload.get("parent") or "").strip()
    if parent:
        return f"substep {parent}/{step_id}"
    return f"step {step_id}"


def _phrase_step(verb: str, edit: TaskDocEdit, _result: dict[str, Any]) -> str:
    payload = edit.step or {}
    where = _step_where(payload)
    if verb == "updated":
        # Only the applier's update keys ever change; any other payload key
        # (parent, id, unknown fields) is addressing, never a change.
        changed = {key: payload[key] for key in _STEP_UPDATE_KEYS if key in payload}
        if not changed:
            return f"{where} updated"
        bits = ", ".join(f"{key}={_short(value)}" for key, value in changed.items())
        return f"{where} updated ({bits})"
    if verb == "added":
        title = payload.get("title")
        if title is None:
            return f"{where} added"
        return f"{where} added ({_short(title)})"
    return f"{where} {verb}".strip()


def _phrase_subtask(edit: TaskDocEdit, removed: bool, result: dict[str, Any]) -> str:
    subtask = edit.subtask or {}
    action = "removed" if removed else "updated"
    phrase = f"subtask {subtask.get('number')} {action}"
    status = subtask.get("status")
    if not removed and status is not None:
        phrase += f" (status={_short(status)})"
    name = subtask.get("name")
    if not removed and isinstance(name, str) and name:
        phrase += f" name={_short(name)!r}"
    slug = result.get("slug")
    if slug:
        phrase += f" on {slug}"
    return phrase.strip()


def _phrase_set_section(edit: TaskDocEdit, _result: dict[str, Any]) -> str:
    section = edit.section or {}
    phrase = f"section {section.get('heading')} updated"
    body = section.get("body")
    if body is not None:
        phrase += f" ({_short(body)})"
    return phrase.strip()


def _phrase_begin_review(_edit: TaskDocEdit, result: dict[str, Any]) -> str:
    review_state = result.get("reviewState") or {}
    return f"review round {review_state.get('round', '?')} begun"


def _phrase_execution_graph(_edit: TaskDocEdit, result: dict[str, Any]) -> str:
    mutations = result.get("appliedMutations") or []
    return f"applied {len(mutations)} execution-graph mutations"


def _phrase_attach_master(edit: TaskDocEdit, _result: dict[str, Any]) -> str:
    subtask = edit.subtask or {}
    return f"attached master {subtask.get('number') or subtask.get('name')}".strip()


def _phrase_retire_master(_edit: TaskDocEdit, result: dict[str, Any]) -> str:
    return f"retired {result.get('retirementState', 'master')}"


def _phrase_archive_sprint(_edit: TaskDocEdit, result: dict[str, Any]) -> str:
    return f"sprint {result.get('archiveState', 'archived')}"


def _phrase_linkage_report(_edit: TaskDocEdit, result: dict[str, Any]) -> str:
    facts = result.get("linkageFacts") or []
    return f"{len(facts)} linkage facts"


def _phrase_set_field(edit: TaskDocEdit, _result: dict[str, Any]) -> str:
    # Only the allowlist lands; any other passed field is ignored, never claimed.
    applied = {key: value for key, value in (edit.fields or {}).items() if key in _MUTABLE_FIELDS}
    if not applied:
        return "field set"
    bits = ", ".join(f"{key}={_short(value)}" for key, value in applied.items())
    return f"set {bits}"


_UPDATED_PHRASES: dict[str, Callable[[TaskDocEdit, dict[str, Any]], str]] = {
    "create": _phrase_create,
    "replace": _phrase_replace,
    "set_status": _phrase_set_status,
    "set_step": lambda edit, result: _phrase_step("updated", edit, result),
    "add_step": lambda edit, result: _phrase_step("added", edit, result),
    "remove_step": lambda edit, result: _phrase_step("removed", edit, result),
    "skip_step": lambda edit, result: _phrase_step("skipped", edit, result),
    "set_subtask": lambda edit, result: _phrase_subtask(edit, False, result),
    "remove_subtask": lambda edit, result: _phrase_subtask(edit, True, result),
    "set_section": _phrase_set_section,
    "append_decision": lambda _edit, _result: "decision appended",
    "begin_review": _phrase_begin_review,
    "record_review": lambda _edit, _result: "review recorded",
    "record_route_review": lambda _edit, _result: "review recorded",
    "author_execution_graph": _phrase_execution_graph,
    "attach_master": _phrase_attach_master,
    "detach_master": lambda _edit, _result: "detached master",
    "retire_master": _phrase_retire_master,
    "archive_sprint": _phrase_archive_sprint,
    "linkage_report": _phrase_linkage_report,
    "set_field": _phrase_set_field,
}


def _updated_phrase(operation: str, edit: TaskDocEdit, result: dict[str, Any]) -> str:
    """The unit one write changed and its new value, in a phrase (MIK-R111 rule 2)."""

    handler = _UPDATED_PHRASES.get(operation)
    if handler is None:
        return f"{operation} applied"
    return handler(edit, result)
