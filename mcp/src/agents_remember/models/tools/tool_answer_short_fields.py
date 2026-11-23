"""Declared values that the shared tool-answer guard keeps whole when cutting an answer."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

TOOL_ANSWER_OVERFLOW_MAX_TOKENS = 400
# Rule 2: the detail keeps the whole list when the answer carries its first entries.
TOOL_ANSWER_LIST_MAX = 20

_DECLARED_FIELDS = {
    ("memory_carryover_plan", True): ("state", "next.summary", "counts"),
    ("memory_carryover_plan", False): ("nextAction", "message"),
    ("knowledge_integrity_check", True): ("state", "validation.candidate", "bases"),
    ("memory_init", True): ("git.repairAttempted",),
    ("memory_init", False): ("memoryRoot", "initialBranch"),
    ("worktree_record_landing", True): (
        "state",
        "next",
        "staleEntries.count",
        "staleEntries.record",
    ),
}
_OUTCOME_AND_ACTION_FIELDS = frozenset({"state", "next", "next.summary", "nextAction"})


@dataclass(frozen=True)
class ShortFormField:
    name: str
    value: object

    @property
    def root(self) -> str:
        return self.name.partition(".")[0]

    def payload(self, short: Mapping[str, object]) -> dict[str, object]:
        root, _, child = self.name.partition(".")
        if not child:
            return {root: self.value}
        current = short.get(root)
        siblings = current if isinstance(current, dict) else {}
        return {root: {**siblings, child: self.value}}

    def present_in(self, answer: Mapping[str, object]) -> bool:
        return _field_value(self.name, answer) is not None


def _field_value(name: str, source: Mapping[str, object]) -> object:
    root, _, child = name.partition(".")
    value = source.get(root)
    if child:
        return value.get(child) if isinstance(value, dict) else None
    return value


def _has_declared_type(name: str, value: object) -> bool:
    if name == "counts":
        return isinstance(value, dict) and all(
            isinstance(key, str) and type(count) is int for key, count in value.items()
        )
    if name in ("bases", "staleEntries.record"):
        return isinstance(value, list) and all(isinstance(base, str) for base in value)
    if name == "staleEntries.count":
        return type(value) is int
    if name == "next":
        return isinstance(value, dict) and all(
            isinstance(value.get(key), str) and value[key] for key in ("summary", "tool")
        )
    if name == "git.repairAttempted":
        return isinstance(value, bool)
    return isinstance(value, str)


def _declared_value(name: str, whole_result: Mapping[str, object]) -> object:
    if name == "next":
        # The detail carries the producer's next step. Keep its sentence and tool on a refit too.
        step = whole_result.get("nextStep")
        if isinstance(step, dict):
            return {"summary": step.get("summary"), "tool": step.get("nextTool")}
    value = _field_value(name, whole_result)
    if name == "staleEntries.record" and isinstance(value, list):
        return value[:TOOL_ANSWER_LIST_MAX]
    return value


def declared_short_fields(
    tool_name: str,
    answer: Mapping[str, object],
    whole_result: Mapping[str, object],
) -> tuple[ShortFormField, ...]:
    """Read only declared paths and types from the validated, uncut tool result."""
    ok = answer.get("ok")
    if not isinstance(ok, bool):
        return ()
    if tool_name == "worktree_record_landing" and whole_result.get("state") not in (
        "recorded",
        "would-record",
    ):
        return ()
    return tuple(
        ShortFormField(name, value)
        for name in _DECLARED_FIELDS.get((tool_name, ok), ())
        if _has_declared_type(name, value := _declared_value(name, whole_result))
    )


def leading_declared_fields(
    short: Mapping[str, object], fields: tuple[ShortFormField, ...]
) -> dict[str, object]:
    """Return the fitting outcome and next action before the result locator."""
    return {
        field.root: short[field.root]
        for field in fields
        if field.name in _OUTCOME_AND_ACTION_FIELDS and field.root in short
    }


def fit_declared_short_fields(
    short: dict[str, object],
    fields: tuple[ShortFormField, ...],
    hint: dict[str, object],
    count: Callable[[Mapping[str, object]], int],
    dropped_budget: int,
) -> int:
    """Reserve space for each declared value or its omission before considering the next."""

    def fits(form: Mapping[str, object]) -> bool:
        return count({**form, "droppedFields": dropped_budget}) <= TOOL_ANSWER_OVERFLOW_MAX_TOKENS

    dropped = 0
    named: list[dict[str, str | int]] = []
    for field in fields:
        value = field.payload(short)
        if fits({**short, **value, "cut": hint}):
            short.update(value)
            continue
        dropped += 1
        candidate = [*named, {"field": field.name, "sizeTokens": count(field.payload({}))}]
        if fits({**short, "cut": {**hint, "omittedFields": candidate}}):
            named = candidate
            hint["omittedFields"] = named
    return dropped
