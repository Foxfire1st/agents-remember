"""Dependent-record deferral operations and unedited-confirmation guard (L52-R1..R4).

Owns ``defer``, ``redefer``, ``lift``, and ``resolveDeferrals`` authoring on ``ar-dependent/v1``
records, validation against candidate code blobs and coordination task facts, the refusal of
unedited confirmations while open deferrals remain, and ``RecordRow`` ``deferralIds`` wiring.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from agents_remember.models.knowledge_files.history import DEFERRAL_RECORD_DISPOSITIONS
from agents_remember.models.knowledge_files.ids import RECORD_PREFIXES, mint_id
from agents_remember.models.knowledge_files.records import DependentDeferral
from agents_remember.tasks.task_facts import TaskFact, load_task_facts

if TYPE_CHECKING:
    from agents_remember.application.knowledge_writer.authoring import Authoring
    from agents_remember.application.knowledge_writer.authoring_rows import HistoryRowAuthoring
    from agents_remember.application.knowledge_writer.handoff import RowRequest

DEFERRAL_OP_KEYS: Final[frozenset[str]] = frozenset(
    {"defer", "redefer", "lift", "resolveDeferrals"}
)
_DEFER_KEYS: Final[frozenset[str]] = frozenset({"passage", "reason", "owner", "condition"})
_REDEFER_KEYS: Final[frozenset[str]] = frozenset(
    {"targetId", "reason", "condition", "passage", "owner"}
)
_LIFT_KEYS: Final[frozenset[str]] = frozenset({"targetId", "reason"})
_CONDITION_KEYS: Final[frozenset[str]] = frozenset({"task", "file", "note"})
_DEPENDENT_PREFIX: Final[str] = f"{RECORD_PREFIXES['dependent']}-"


@dataclass
class DeferralRecordOps:
    """Deferral IDs created, replaced, lifted, or resolved for one dependent in a writer run."""

    deferred: list[str] = field(default_factory=list)
    re_deferred: list[str] = field(default_factory=list)
    lifted: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    confirmed: bool = False

    def all_ids(self) -> tuple[str, ...]:
        combined = (*self.deferred, *self.re_deferred, *self.lifted, *self.resolved)
        return tuple(dict.fromkeys(combined))


@dataclass
class _DeferralBatch:
    """Mutable working state while applying one record's deferral operations."""

    authoring: Authoring
    deferrals: list[dict[str, Any]]
    ops: DeferralRecordOps
    taken: set[str]
    where: str

    @classmethod
    def start(
        cls, authoring: Authoring, before: Mapping[str, Any] | None, where: str
    ) -> _DeferralBatch:
        open_list = _copy_open_deferrals(before)
        taken = {str(one.get("id")) for one in open_list if isinstance(one.get("id"), str)}
        return cls(authoring, open_list, DeferralRecordOps(), taken, where)

    def finish(self, document: dict[str, Any], record_id: str) -> None:
        if self.deferrals:
            document["deferrals"] = self.deferrals
        else:
            document.pop("deferrals", None)
        if self.ops.all_ids():
            self.authoring._deferral_ops[record_id] = self.ops


def is_deferral_only_fields(fields: Mapping[str, Any]) -> bool:
    """Whether ``fields`` carries only deferral operations without confirming the dependent."""

    if "confirmed" in fields or "stands_on" in fields or "document" in fields:
        return False
    return bool(DEFERRAL_OP_KEYS & set(fields))


def deferral_condition_files(fields: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """Every ``(field_path, file_value)`` named in ``defer``/``redefer`` conditions for checks."""

    found: list[tuple[str, Any]] = []
    for op_name in ("defer", "redefer"):
        raw = fields.get(op_name)
        items: Sequence[Any] = (
            (raw,) if isinstance(raw, Mapping) else raw if isinstance(raw, list) else ()
        )
        indexed = isinstance(raw, list)
        for position, item in enumerate(items, start=1):
            condition = item.get("condition") if isinstance(item, Mapping) else None
            if isinstance(condition, Mapping) and condition.get("file") is not None:
                prefix = f"{op_name}[{position}]" if indexed else op_name
                found.append((f"{prefix}.condition.file", condition.get("file")))
    return found


def author_dependent(
    authoring: Authoring,
    document: dict[str, Any],
    where: str,
    before: Mapping[str, Any] | None,
    authored: Mapping[str, Any],
) -> None:
    """Author a dependent confirmation or deferral-only update (L52-R1..R3)."""

    for key in DEFERRAL_OP_KEYS:
        document.pop(key, None)
    if "deferrals" in authored and authoring.owner.kind != "crossing":
        authoring.problem(
            f"{where}.deferrals",
            "the writer manages 'deferrals' through deferral operations; use 'defer', 'redefer', "
            "'lift', or 'resolveDeferrals' instead of setting 'deferrals' directly",
        )
        return
    if before is not None and is_deferral_only_fields(authored):
        _author_deferral_only(authoring, document, where, before, authored)
        return
    _author_confirmation(authoring, document, where, before, authored)


def _author_deferral_only(
    authoring: Authoring,
    document: dict[str, Any],
    where: str,
    before: Mapping[str, Any],
    authored: Mapping[str, Any],
) -> None:
    """Update only ``deferrals`` on an existing dependent, leaving confirmation untouched."""

    if "resolveDeferrals" in authored:
        authoring.problem(
            f"{where}.resolveDeferrals",
            "'resolveDeferrals' applies only to a confirmation write after editing the document; "
            "use 'lift' to remove a deferral without editing the document, or supply 'confirmed' "
            "after editing the document",
        )
        return
    batch = _DeferralBatch.start(authoring, before, where)
    _apply_lifts(batch, authored.get("lift"))
    _apply_redefers(batch, authored.get("redefer"))
    _apply_defers(batch, authored.get("defer"))
    batch.finish(document, str(document["id"]))


def _author_confirmation(
    authoring: Authoring,
    document: dict[str, Any],
    where: str,
    before: Mapping[str, Any] | None,
    authored: Mapping[str, Any],
) -> None:
    """Confirm a dependent, refusing unedited confirmations while open deferrals remain."""

    record_id = str(document["id"])
    batch = _DeferralBatch.start(authoring, before, where)
    doc_path = document.get("document")
    doc_edited = (
        isinstance(doc_path, str)
        and bool(doc_path)
        and authoring.base_code_blobs is not None
        and authoring.base_code_blobs.get(doc_path) != authoring.code.blobs.get(doc_path)
    )
    if before is not None and batch.deferrals and not doc_edited:
        authoring.problem(
            where, _unedited_deferred_refusal(record_id, str(doc_path or ""), batch.deferrals)
        )
        return
    if "lift" in authored and authored.get("lift") is not None:
        authoring.problem(
            f"{where}.lift",
            "'lift' ends a deferral without editing the document and must be recorded as its own "
            "'deferral_lifted' operation; use 'resolveDeferrals' when confirming an edited "
            "document, or run 'lift' in a deferral-only write (without 'confirmed')",
        )
        return
    authoring._confirm_dependent(document, record_id, where, authored)
    if "resolveDeferrals" in authored:
        _apply_resolves(batch, authored.get("resolveDeferrals"))
    _apply_redefers(batch, authored.get("redefer"))
    _apply_defers(batch, authored.get("defer"))
    if batch.deferrals and not doc_edited:
        authoring.problem(
            where, _unedited_deferred_refusal(record_id, str(doc_path or ""), batch.deferrals)
        )
        return
    batch.ops.confirmed = True
    batch.finish(document, record_id)


def _unedited_deferred_refusal(
    record_id: str, doc_path: str, open_deferrals: Sequence[Mapping[str, Any]]
) -> str:
    details = "; ".join(
        f"{one.get('id')} (owner={one.get('owner')}, passage={one.get('passage')!r}, "
        f"reason={one.get('reason')!r})"
        for one in open_deferrals
    )
    return (
        f"cannot confirm {record_id} ({doc_path!r}) without editing the document while open "
        f"deferral(s) remain: {details}. Remedy: edit the document and confirm with "
        "'resolveDeferrals', or run a deferral-only write with 'lift' or 'redefer' (without "
        "'confirmed')"
    )


def _copy_open_deferrals(before: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    raw = before.get("deferrals") if before is not None else None
    return [dict(one) for one in raw if isinstance(one, Mapping)] if isinstance(raw, list) else []


def _read_op_items(
    authoring: Authoring, raw: Any, name: str, where: str
) -> list[tuple[str, Mapping[str, Any]]]:
    if raw is None:
        return []
    if isinstance(raw, Mapping):
        return [(f"{where}.{name}", raw)]
    if not isinstance(raw, list):
        authoring.problem(f"{where}.{name}", f"'{name}' must be a JSON object or list of objects")
        return []
    items: list[tuple[str, Mapping[str, Any]]] = []
    for position, one in enumerate(raw, start=1):
        at = f"{where}.{name}[{position}]"
        if not isinstance(one, Mapping):
            authoring.problem(at, f"each '{name}' item must be a JSON object")
        else:
            items.append((at, one))
    return items


def _nonblank(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _task_facts(authoring: Authoring) -> Mapping[str, TaskFact]:
    if authoring._task_facts is None:
        authoring._task_facts = load_task_facts(authoring.coordination_root, authoring.repo_name)
    return authoring._task_facts


def _read_optional_text(
    authoring: Authoring, mapping: Mapping[str, Any], key: str, where: str
) -> tuple[bool, str | None]:
    if key not in mapping or mapping[key] is None:
        return True, None
    text = _nonblank(mapping[key])
    if text is None:
        authoring.problem(f"{where}.{key}", f"'{key}' must be a non-empty string")
        return False, None
    return True, text


def _validate_condition(
    authoring: Authoring, raw: Any, where: str
) -> tuple[dict[str, str], str | None] | None:
    """Validate ``condition`` and return ``(condition_doc, file_blob)`` or ``None`` on refusal."""

    at = f"{where}.condition"
    if not isinstance(raw, Mapping):
        authoring.problem(at, "a deferral condition is an object with 'task' and/or 'file'")
        return None
    if unknown := sorted(set(raw) - _CONDITION_KEYS):
        authoring.problem(
            at, f"unknown condition key(s) {unknown}; allowed: {sorted(_CONDITION_KEYS)}"
        )
        return None
    task_valid, task = _read_optional_text(authoring, raw, "task", at)
    file_valid, file_path = _read_optional_text(authoring, raw, "file", at)
    note_valid, note = _read_optional_text(authoring, raw, "note", at)
    if not (task_valid and file_valid and note_valid):
        return None
    if task is None and file_path is None:
        authoring.problem(
            at,
            "a deferral condition requires at least one of 'task' or 'file'; "
            "name an open leaf task ID, a tracked file path, or both",
        )
        return None
    task_ok = task is None or _validate_condition_task(authoring, task, f"{at}.task")
    file_blob = _validate_condition_file(authoring, file_path, f"{at}.file") if file_path else None
    if not task_ok or (file_path is not None and file_blob is None):
        return None
    condition_doc = {
        k: v for k, v in (("task", task), ("file", file_path), ("note", note)) if isinstance(v, str)
    }
    return condition_doc, file_blob


def _validate_condition_file(authoring: Authoring, file_path: str, where: str) -> str | None:
    blob = authoring.code.blobs.get(file_path)
    if blob is None:
        authoring.problem(
            where,
            f"{file_path!r} is not a tracked file in the code candidate; "
            "name a tracked repository-relative path present in the candidate tree",
        )
    return blob


def _validate_condition_task(authoring: Authoring, task_id: str, where: str) -> bool:
    if authoring.coordination_root is None:
        authoring.problem(
            where,
            f"cannot verify deferral target task {task_id!r} because coordination task facts "
            "are unavailable (no coordination_root); supply a coordination root or watch a "
            "'condition.file' instead",
        )
        return False
    fact = _task_facts(authoring).get(task_id)
    if fact is None:
        authoring.problem(
            where,
            f"deferral target task {task_id!r} does not exist in coordination tasks; "
            "create the follow-up leaf task first or name an existing open leaf task ID",
        )
        return False
    if fact.is_master:
        authoring.problem(
            where,
            f"deferral target {task_id!r} is a master task, not a leaf task; "
            "name a concrete open leaf task ID under that master",
        )
        return False
    if fact.is_terminal:
        authoring.problem(
            where,
            f"deferral target task {task_id!r} has terminal status {fact.status!r} and cannot "
            "receive a deferral; name an open leaf task ID or lift the deferral",
        )
        return False
    return True


def _mint_deferral_id(taken: set[str]) -> str:
    while True:
        candidate = mint_id("deferral")
        if candidate not in taken:
            taken.add(candidate)
            return candidate


def _build_deferral(
    batch: _DeferralBatch,
    draft: Mapping[str, Any],
    condition: dict[str, str],
    file_blob: str | None,
    where: str,
) -> dict[str, Any] | None:
    deferral: dict[str, Any] = {
        "id": _mint_deferral_id(batch.taken),
        "passage": draft["passage"],
        "reason": draft["reason"],
        "condition": condition,
        "owner": draft["owner"],
        "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "createdByLeaf": batch.authoring.owner.id,
    }
    if file_blob is not None:
        deferral["fileBlob"] = file_blob
    try:
        DependentDeferral.model_validate(deferral)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in one['loc']) or '-'}: {one['msg']}"
            for one in error.errors()
        )
        batch.authoring.problem(where, details)
        return None
    return deferral


def _find_open_index(deferrals: Sequence[Mapping[str, Any]], target_id: str) -> int | None:
    return next((i for i, one in enumerate(deferrals) if one.get("id") == target_id), None)


def _open_ids_hint(deferrals: Sequence[Mapping[str, Any]]) -> str:
    ids = [str(one.get("id")) for one in deferrals if isinstance(one.get("id"), str)]
    return f"open deferrals: {', '.join(ids)}" if ids else "the record has no open deferrals"


def _apply_resolves(batch: _DeferralBatch, raw: Any) -> None:
    if raw is None:
        return
    if not isinstance(raw, list) or not all(isinstance(one, str) and one.strip() for one in raw):
        batch.authoring.problem(
            f"{batch.where}.resolveDeferrals",
            "'resolveDeferrals' must be a list of open deferral IDs (DFR-...)",
        )
        return
    for position, target_id in enumerate(raw, start=1):
        index = _find_open_index(batch.deferrals, target_id)
        if index is None:
            batch.authoring.problem(
                f"{batch.where}.resolveDeferrals[{position}]",
                f"{target_id!r} is not an open deferral on this record "
                f"({_open_ids_hint(batch.deferrals)}); name an open deferral ID to resolve",
            )
            continue
        batch.deferrals.pop(index)
        batch.ops.resolved.append(target_id)


def _apply_lifts(batch: _DeferralBatch, raw: Any) -> None:
    for at, item in _read_op_items(batch.authoring, raw, "lift", batch.where):
        if unknown := sorted(set(item) - _LIFT_KEYS):
            batch.authoring.problem(
                at, f"unknown lift key(s) {unknown}; allowed: {sorted(_LIFT_KEYS)}"
            )
            continue
        target_id, reason = _nonblank(item.get("targetId")), _nonblank(item.get("reason"))
        if target_id is None:
            batch.authoring.problem(f"{at}.targetId", "a lift names the open deferral 'targetId'")
        if reason is None:
            batch.authoring.problem(f"{at}.reason", "a lift names a non-empty 'reason'")
        if target_id is None or reason is None:
            continue
        index = _find_open_index(batch.deferrals, target_id)
        if index is None:
            batch.authoring.problem(
                f"{at}.targetId",
                f"{target_id!r} is not an open deferral on this record "
                f"({_open_ids_hint(batch.deferrals)}); name an open deferral ID to lift",
            )
            continue
        batch.deferrals.pop(index)
        batch.ops.lifted.append(target_id)


def _apply_redefers(batch: _DeferralBatch, raw: Any) -> None:
    for at, item in _read_op_items(batch.authoring, raw, "redefer", batch.where):
        _redefer_one(batch, item, at)


def _redefer_one(batch: _DeferralBatch, item: Mapping[str, Any], at: str) -> None:
    if unknown := sorted(set(item) - _REDEFER_KEYS):
        batch.authoring.problem(
            at, f"unknown redefer key(s) {unknown}; allowed: {sorted(_REDEFER_KEYS)}"
        )
        return
    target_id, reason = _nonblank(item.get("targetId")), _nonblank(item.get("reason"))
    if target_id is None:
        batch.authoring.problem(
            f"{at}.targetId", "a re-deferral names the open deferral 'targetId'"
        )
    if reason is None:
        batch.authoring.problem(f"{at}.reason", "a re-deferral names a non-empty 'reason'")
    if target_id is None or reason is None:
        return
    index = _find_open_index(batch.deferrals, target_id)
    if index is None:
        batch.authoring.problem(
            f"{at}.targetId",
            f"{target_id!r} is not an open deferral on this record "
            f"({_open_ids_hint(batch.deferrals)}); name an open deferral ID to re-defer",
        )
        return
    old = batch.deferrals[index]
    passage = _defaulted_text(batch.authoring, item, "passage", str(old.get("passage", "")), at)
    owner = _defaulted_text(batch.authoring, item, "owner", str(old.get("owner", "")), at)
    validated = _validate_condition(batch.authoring, item.get("condition"), at)
    if passage is None or owner is None or validated is None:
        return
    new_condition, new_blob = validated
    if not _redefer_allowed(batch.authoring, old, new_condition):
        batch.authoring.problem(
            at,
            f"re-deferral of {target_id} is refused because neither 'condition.task' nor "
            "'condition.file' changed and the watched file's blob is unchanged; re-defer with "
            "a different target task or watched file, wait until the watched file changes, or "
            "'lift' the deferral",
        )
        return
    draft = {"passage": passage, "reason": reason, "owner": owner}
    if (replacement := _build_deferral(batch, draft, new_condition, new_blob, at)) is not None:
        batch.deferrals.pop(index)
        batch.deferrals.append(replacement)
        batch.ops.re_deferred.append(str(replacement["id"]))


def _defaulted_text(
    authoring: Authoring, item: Mapping[str, Any], key: str, fallback: str, where: str
) -> str | None:
    if key not in item or item[key] is None:
        return fallback
    value = _nonblank(item[key])
    if value is None:
        authoring.problem(f"{where}.{key}", f"'{key}' must be a non-empty string when supplied")
    return value


def _redefer_allowed(
    authoring: Authoring, old: Mapping[str, Any], new_condition: Mapping[str, str]
) -> bool:
    """L52-R2: re-deferral needs a new task/file or a watched file whose blob moved."""

    raw_condition = old.get("condition")
    old_condition = raw_condition if isinstance(raw_condition, Mapping) else {}
    old_task, old_file = old_condition.get("task"), old_condition.get("file")
    if new_condition.get("task") != old_task or new_condition.get("file") != old_file:
        return True
    return isinstance(old_file, str) and authoring.code.blobs.get(old_file) != old.get("fileBlob")


def _apply_defers(batch: _DeferralBatch, raw: Any) -> None:
    for at, item in _read_op_items(batch.authoring, raw, "defer", batch.where):
        if unknown := sorted(set(item) - _DEFER_KEYS):
            batch.authoring.problem(
                at, f"unknown defer key(s) {unknown}; allowed: {sorted(_DEFER_KEYS)}"
            )
            continue
        passage = _nonblank(item.get("passage"))
        reason = _nonblank(item.get("reason"))
        owner = _nonblank(item.get("owner"))
        if passage is None:
            batch.authoring.problem(f"{at}.passage", "a deferral names the stale 'passage'")
        if reason is None:
            batch.authoring.problem(f"{at}.reason", "a deferral names a non-empty 'reason'")
        if owner is None:
            batch.authoring.problem(f"{at}.owner", "a deferral names its 'owner'")
        validated = _validate_condition(batch.authoring, item.get("condition"), at)
        if passage is None or reason is None or owner is None or validated is None:
            continue
        condition_doc, file_blob = validated
        draft = {"passage": passage, "reason": reason, "owner": owner}
        if (created := _build_deferral(batch, draft, condition_doc, file_blob, at)) is not None:
            batch.deferrals.append(created)
            batch.ops.deferred.append(str(created["id"]))


def _lift_row_problem(request: RowRequest, subject: str, lifted: Sequence[str]) -> str | None:
    if request.disposition != "deferral_lifted":
        return (
            f"record {subject} lifted deferral(s) {', '.join(lifted)}: a write that lifts a "
            f"deferral must use disposition 'deferral_lifted' (not {request.disposition!r}) "
            "so memory_quality_check surfaces the lift for Memory Reviewer"
        )
    if request.deferral_ids is not None:
        missing = [d for d in lifted if d not in request.deferral_ids]
        if missing:
            return (
                f"record {subject} lifted deferral(s) {', '.join(missing)} which must "
                "appear in 'deferralIds'"
            )
    return None


def _non_lift_row_problem(
    request: RowRequest,
    subject: str,
    ops: DeferralRecordOps | None,
    *,
    prior_lifted: bool,
) -> str | None:
    disp = request.disposition
    if prior_lifted and disp in ("deferred", "re_deferred"):
        return (
            f"record {subject} already recorded a 'deferral_lifted' row in this leaf; keep "
            f"disposition 'deferral_lifted' (not {disp!r}) so the lifted deferral stays visible "
            "to Memory Reviewer"
        )
    if ops is None or ops.confirmed:
        return None
    problem: str | None = None
    if disp == "deferral_lifted" and not prior_lifted:
        problem = (
            f"record {subject} did not lift any deferral in this write; use 'deferred' or "
            "'re_deferred' instead of 'deferral_lifted'"
        )
    elif disp not in DEFERRAL_RECORD_DISPOSITIONS:
        allowed = ", ".join(DEFERRAL_RECORD_DISPOSITIONS)
        problem = (
            f"record {subject} changed only its deferrals and requires one of ({allowed}), "
            f"not {disp!r}"
        )
    elif ops.deferred and not ops.re_deferred and disp == "re_deferred":
        problem = f"record {subject} deferred; use disposition 'deferred' instead of 're_deferred'"
    elif ops.re_deferred and not ops.deferred and disp == "deferred":
        problem = (
            f"record {subject} re-deferred; use disposition 're_deferred' instead of 'deferred'"
        )
    return problem


def _prior_lifted_ids(existing: Mapping[str, Any] | None, disposition: str) -> tuple[str, ...]:
    if existing is None or disposition != "deferral_lifted":
        return ()
    if existing.get("disposition") != "deferral_lifted":
        return ()
    raw = existing.get("deferralIds")
    return tuple(one for one in raw if isinstance(one, str)) if isinstance(raw, list) else ()


def apply_record_row_deferrals(
    authoring: HistoryRowAuthoring,
    request: RowRequest,
    row: dict[str, Any],
    where: str,
    *,
    existing: Mapping[str, Any] | None = None,
) -> bool:
    """Validate dependent deferral dispositions and populate ``deferralIds`` on ``row``."""

    subject = str(row["subject"])
    is_dependent = subject.startswith(_DEPENDENT_PREFIX)
    if request.disposition in DEFERRAL_RECORD_DISPOSITIONS and not is_dependent:
        authoring.problem(
            where,
            f"disposition {request.disposition!r} applies only to a dependent record "
            f"({_DEPENDENT_PREFIX}...); use 'changed', 'retired', or 'no_impact' instead",
        )
        return False
    if request.deferral_ids is not None and not is_dependent:
        authoring.problem(
            where,
            f"'deferralIds' applies only to a dependent record row ({_DEPENDENT_PREFIX}...); "
            "remove 'deferralIds' from this row",
        )
        return False
    if not is_dependent:
        return True
    ops = authoring._deferral_ops.get(subject)
    prior_lifted = existing is not None and existing.get("disposition") == "deferral_lifted"
    problem = (
        _lift_row_problem(request, subject, ops.lifted)
        if ops is not None and ops.lifted
        else _non_lift_row_problem(request, subject, ops, prior_lifted=prior_lifted)
    )
    if problem is not None:
        authoring.problem(where, problem)
        return False
    carried = _prior_lifted_ids(existing, request.disposition)
    if request.deferral_ids is not None:
        row["deferralIds"] = list(dict.fromkeys((*carried, *request.deferral_ids)))
        return True
    auto_ids = _auto_deferral_ids(request.disposition, ops) if ops is not None else ()
    if combined := (*carried, *auto_ids):
        row["deferralIds"] = list(dict.fromkeys(combined))
    return True


def verify_lifted_deferral_rows(authoring: Authoring) -> None:
    """Refuse any lift that lacks a ``deferral_lifted`` history row in the same write."""

    for record_id, ops in sorted(authoring._deferral_ops.items()):
        if not ops.lifted:
            continue
        if any(
            row.subject == record_id and row.disposition == "deferral_lifted"
            for row in authoring.rows
        ):
            continue
        if any(
            problem.where.startswith("history[") and record_id in problem.message
            for problem in authoring.problems
        ):
            continue
        authoring.problem(
            f"record {record_id}",
            f"{record_id} lifted deferral(s) {', '.join(ops.lifted)} and requires a "
            "'deferral_lifted' row in 'history' in the same write so memory_quality_check "
            "surfaces the lift for Memory Reviewer",
        )


def _auto_deferral_ids(disposition: str, ops: DeferralRecordOps) -> Sequence[str]:
    if disposition == "deferred":
        return ops.deferred or ops.all_ids()
    if disposition == "re_deferred":
        return ops.re_deferred or ops.all_ids()
    if disposition == "deferral_lifted":
        return ops.lifted or ops.all_ids()
    if disposition == "changed":
        return ops.all_ids()
    return ()
