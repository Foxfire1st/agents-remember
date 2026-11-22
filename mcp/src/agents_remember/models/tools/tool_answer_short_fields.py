"""Declared values that the shared tool-answer guard keeps whole when cutting an answer."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

TOOL_ANSWER_OVERFLOW_MAX_TOKENS = 400

_DECLARED_FIELDS = {
    ("memory_carryover_plan", True): ("state", "next.summary", "counts"),
    ("memory_carryover_plan", False): ("nextAction", "message"),
    ("knowledge_integrity_check", True): ("state", "validation.candidate", "bases"),
    ("memory_init", True): ("git.repairAttempted",),
    ("memory_init", False): ("memoryRoot", "initialBranch"),
}
_OUTCOME_AND_ACTION_FIELDS = frozenset({"state", "next.summary", "nextAction"})


@dataclass(frozen=True)
class ShortFormField:
    name: str
    value: object

    @property
    def root(self) -> str:
        return self.name.partition(".")[0]

    def payload(self) -> dict[str, object]:
        root, _, child = self.name.partition(".")
        return {root: {child: self.value} if child else self.value}


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
    if name == "bases":
        return isinstance(value, list) and all(isinstance(base, str) for base in value)
    if name == "git.repairAttempted":
        return isinstance(value, bool)
    return isinstance(value, str)


def declared_short_fields(
    tool_name: str,
    answer: Mapping[str, object],
    whole_result: Mapping[str, object],
) -> tuple[ShortFormField, ...]:
    """Read only declared paths and types from the validated, uncut tool result."""
    ok = answer.get("ok")
    if not isinstance(ok, bool):
        return ()
    return tuple(
        ShortFormField(name, value)
        for name in _DECLARED_FIELDS.get((tool_name, ok), ())
        if _has_declared_type(name, value := _field_value(name, whole_result))
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
        value = field.payload()
        if fits({**short, **value, "cut": hint}):
            short.update(value)
            continue
        dropped += 1
        candidate = [*named, {"field": field.name, "sizeTokens": count(value)}]
        if fits({**short, "cut": {**hint, "omittedFields": candidate}}):
            named = candidate
            hint["omittedFields"] = named
    return dropped
