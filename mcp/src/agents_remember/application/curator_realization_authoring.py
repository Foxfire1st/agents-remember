"""The realization one hand-off target authors: its role and its rationale, stated and never made up.

A realization claim is the author's account of what one cited place does for the obligation. Both
halves of it are authored facts. The **rationale** says why this place carries the obligation, and
the **role** says how it does (primary authority, enforcement, propagation, presentation, support).
Neither can be read off the path or the locator: a locator says where to look, never what the thing
found there means.

**Per target first, then the entry's explicit default.** Two constructs cited by one entry usually
do different things, so a target may carry its own ``rationale`` and ``role``. An entry may also state
``realization_rationale`` / ``realization_role`` once, as the explicit default for every target that
does not state its own. A target's own value always wins over the entry's. The two halves resolve
independently, so a target may state its own rationale and inherit the entry's role. A target that
omits ``role`` inherits the entry's; when neither states one the claim is ``unclassified``.

**The authored realization is checked at admission.** The writer used to fill a missing rationale
with a generated sentence naming the path, and stored it as if someone had authored it. Measured in
a published dataset, most stored realization claims held nothing but that sentence, and a reader
could not tell it from an explanation. The stored claim cannot hold an absent rationale either: the
model and the table both require non-blank text. So an entry that would write a realization is
refused, by name and before any identity is minted, when any of its values is not a string (it is
never rendered into text), when a role is outside the shipped vocabulary, when a target has no
rationale at either level, or when a rationale is longer than a stored claim can hold. Each refusal
names the entry and every offending target by position, path and locator, so the producer can
correct them all in one pass, and the entry beside it is unaffected.

**No route is spelled by omitting it.** A target's ``governing_route`` and the entry's
``authority.governing_route`` name a real route as a string, or are left out. The word ``absent``,
in any case and once trimmed, and a value that is not a string are refused at the level where they are
written: the writer treats a target's route as a path, so a copied placeholder ``"absent"`` would author
a route named ``absent``, and a number or a list would become a route named by its printed form.

**What this does not cover.** It checks that an explanation was *stated*, not that it is a good one:
a producer can still author a vague sentence, and that is a review question, not an admission one.
An exact retry of an operation the candidate already committed writes no realization, so its caller
does not ask these questions of it; rows already stored are never re-read or rewritten here,
including rows that hold the old generated sentence.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast, get_args

from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH
from agents_remember.models.knowledge.graph import UNCLASSIFIED_ROLE, RealizationRole

CODE_RATIONALE_ABSENT = "realization_rationale_absent"
CODE_NOT_TEXT = "realization_value_not_text"
CODE_ROLE_UNKNOWN = "realization_role_unknown"
CODE_RATIONALE_TOO_LONG = "realization_rationale_too_long"
CODE_ROUTE_ABSENT_LITERAL = "realization_governing_route_absent_literal"

# The keys a producer writes. The entry-level pair keeps the spelling it has always had; the target
# pair drops the prefix because the target is already the realization being described.
ENTRY_ROLE_KEY = "realization_role"
ENTRY_RATIONALE_KEY = "realization_rationale"
TARGET_ROLE_KEY = "role"
TARGET_RATIONALE_KEY = "rationale"
ROUTE_KEY = "governing_route"
AUTHORITY_KEY = "authority"
_ABSENT_LITERAL = "absent"

_ROLES: tuple[str, ...] = get_args(RealizationRole)


def _stated(raw: Mapping[str, Any], key: str) -> str:
    """One authored text field, trimmed; a missing, null, blank or non-string field is ``""``.

    A non-string value is never rendered into text here. Admission refuses it by name, and a retry
    of an already committed operation, which is not admitted again, reads it as nothing stated.
    """

    value = raw.get(key)
    return value.strip() if isinstance(value, str) else ""


def _vocabulary_role(stated: str) -> RealizationRole | None:
    """A stated role checked against the SHIPPED vocabulary rather than a second copy of it."""

    return cast("RealizationRole", stated) if stated in _ROLES else None


def _field_problem(raw: Mapping[str, Any], key: str, role_key: str) -> tuple[str, str] | None:
    """The refusal one authored field earns for its own value: not a string, or an unknown role."""

    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        return CODE_NOT_TEXT, f"{key!r} is a JSON {_json_type(value)}, not a string"
    stated = _stated(raw, key)
    if key == role_key and stated and _vocabulary_role(stated) is None:
        return CODE_ROLE_UNKNOWN, f"{key!r} is {stated!r}"
    return None


def _names_absent_route(raw: object) -> bool:
    """Whether a mapping writes the placeholder word ``absent``, in any case, as its governing route."""

    value = raw.get(ROUTE_KEY) if isinstance(raw, Mapping) else None
    return isinstance(value, str) and value.strip().casefold() == _ABSENT_LITERAL


def _authority_route_problem(raw: Mapping[str, Any]) -> tuple[str, str] | None:
    """The refusal an entry's ``authority.governing_route`` earns: not a string, or the word ``absent``."""

    authority = raw.get(AUTHORITY_KEY)
    if not isinstance(authority, Mapping):
        return None
    named = f"'{AUTHORITY_KEY}.{ROUTE_KEY}'"
    value = authority.get(ROUTE_KEY)
    if value is not None and not isinstance(value, str):
        return CODE_NOT_TEXT, f"{named} is a JSON {_json_type(value)}, not a string"
    if isinstance(value, str) and _names_absent_route(authority):
        return CODE_ROUTE_ABSENT_LITERAL, f"{named} is {value.strip()!r}"
    return None


@dataclass(frozen=True)
class TargetRealization:
    """The role and rationale one target's claim is written with, and what the target itself stated.

    ``stated`` holds only the target's own authored keys, for the entry's content digest. It is empty
    when the target inherits everything, so an entry authored once at the entry level digests exactly
    as it did before targets could state their own.
    """

    role: RealizationRole
    rationale: str
    stated: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class EntryRealization:
    """The entry-level defaults: the role and rationale a producer states once for every target.

    ``rationale`` is kept verbatim as authored because the entry's content digest has always carried
    this spelling. Trimming it here would turn an exact replay of an existing list into a changed
    operation. ``problem`` is the refusal the entry-level fields earn by their own values, if any.
    """

    role: RealizationRole | None = None
    rationale: str = ""
    problem: tuple[str, str] | None = None

    @classmethod
    def read(cls, raw: Mapping[str, Any]) -> EntryRealization:
        value = raw.get(ENTRY_RATIONALE_KEY)
        return cls(
            role=_vocabulary_role(_stated(raw, ENTRY_ROLE_KEY)),
            rationale=value if isinstance(value, str) else "",
            problem=_field_problem(raw, ENTRY_RATIONALE_KEY, ENTRY_ROLE_KEY)
            or _field_problem(raw, ENTRY_ROLE_KEY, ENTRY_ROLE_KEY)
            or _authority_route_problem(raw),
        )

    def for_target(self, target: Mapping[str, Any]) -> TargetRealization:
        """One target's realization: its own stated values first, then this entry's defaults."""

        own_role = _stated(target, TARGET_ROLE_KEY)
        own_rationale = _stated(target, TARGET_RATIONALE_KEY)
        if own_role:
            role = _vocabulary_role(own_role) or UNCLASSIFIED_ROLE
        else:
            role = self.role or UNCLASSIFIED_ROLE
        stated = tuple(
            (key, value)
            for key, value in ((TARGET_ROLE_KEY, own_role), (TARGET_RATIONALE_KEY, own_rationale))
            if value
        )
        return TargetRealization(
            role=role, rationale=own_rationale or self.rationale.strip(), stated=stated
        )


def _own_value_problem(code: str) -> Callable[[EntryRealization, Mapping[str, Any]], str | None]:
    """A target check for one of the two refusals a target's own field values can earn."""

    def check(_entry: EntryRealization, target: Mapping[str, Any]) -> str | None:
        for key in (TARGET_RATIONALE_KEY, TARGET_ROLE_KEY, ROUTE_KEY):
            problem = _field_problem(target, key, TARGET_ROLE_KEY)
            if problem is not None and problem[0] == code:
                return f": {problem[1]}"
        return None

    return check


def _absent(entry: EntryRealization, target: Mapping[str, Any]) -> str | None:
    return "" if not entry.for_target(target).rationale else None


def _absent_route(_entry: EntryRealization, target: Mapping[str, Any]) -> str | None:
    if not _names_absent_route(target):
        return None
    written: str = target[ROUTE_KEY]
    return f": '{ROUTE_KEY}' is {written.strip()!r}"


def _too_long(entry: EntryRealization, target: Mapping[str, Any]) -> str | None:
    length = len(entry.for_target(target).rationale)
    return f": {length} characters" if length > PROSE_MAX_LENGTH else None


# Each target check in the order it is asked, with the sentence that opens its refusal and the remedy
# that closes it. The first check any target fails decides the entry's refusal, which then names
# every target that fails that same check.
_TARGET_CHECKS: tuple[tuple[str, Callable[..., str | None], str, str], ...] = (
    (
        CODE_NOT_TEXT,
        _own_value_problem(CODE_NOT_TEXT),
        "has a realization value that is not a string in",
        "Write each rationale, role and governing route as one JSON string; the writer never "
        "renders another value "
        "as text",
    ),
    (
        CODE_ROLE_UNKNOWN,
        _own_value_problem(CODE_ROLE_UNKNOWN),
        "names a role outside the realization vocabulary in",
        f"Use one of {', '.join(_ROLES)}, or omit '{TARGET_ROLE_KEY}' to inherit the entry's "
        f"'{ENTRY_ROLE_KEY}'",
    ),
    (
        CODE_ROUTE_ABSENT_LITERAL,
        _absent_route,
        f"writes the word '{_ABSENT_LITERAL}' as its governing route in",
        f"Omit '{ROUTE_KEY}' from a target no route governs; the writer reads the value as a route "
        f"path and would author a route named '{_ABSENT_LITERAL}'",
    ),
    (
        CODE_RATIONALE_ABSENT,
        _absent,
        "has no authored rationale for",
        f"Author why each place carries the obligation as the target's '{TARGET_RATIONALE_KEY}', "
        f"or state the entry's '{ENTRY_RATIONALE_KEY}' as the explicit default for every target "
        "without one; the writer does not generate rationale text",
    ),
    (
        CODE_RATIONALE_TOO_LONG,
        _too_long,
        f"has a rationale longer than the {PROSE_MAX_LENGTH} characters a stored claim holds for",
        "Shorten each one; nothing was written for this entry",
    ),
)


def realization_refusal(
    entry_id: str, entry: EntryRealization, targets: Sequence[Any]
) -> tuple[str, str] | None:
    """The refusal an entry that would write realizations earns for what it authored, if any.

    The entry-level fields are asked first, because every target that inherits them inherits their
    defect. A target that is not a mapping is skipped: the target-shape check refuses it and names
    what it actually is, and a realization question about it would be answering the wrong defect.
    """

    if entry.problem is not None:
        code, detail = entry.problem
        return code, f"entry {entry_id!r} has an entry-level value it cannot admit: {detail}. " + (
            f"Write '{AUTHORITY_KEY}.{ROUTE_KEY}' as a route path, or omit it when no route governs "
            "the entry"
            if detail.startswith(f"'{AUTHORITY_KEY}.")
            else f"Write '{ENTRY_RATIONALE_KEY}' as one JSON string and '{ENTRY_ROLE_KEY}' as one "
            f"of {', '.join(_ROLES)}, or omit them"
        )
    placed = [(n, one) for n, one in enumerate(targets, start=1) if isinstance(one, Mapping)]
    for code, check, opening, remedy in _TARGET_CHECKS:
        failing = [
            _describe(position, target) + detail
            for position, target in placed
            if (detail := check(entry, target)) is not None
        ]
        if failing:
            return code, (
                f"entry {entry_id!r} {opening} {len(failing)} of its {len(targets)} target(s): "
                f"{'; '.join(failing)}. {remedy}"
            )
    return None


def _describe(position: int, target: Mapping[str, Any]) -> str:
    """One target, named the way the producer wrote it: position, path and locator."""

    locator = json.dumps(target.get("locator"), sort_keys=True, default=str)
    return f"target {position} (path {str(target.get('path', ''))!r}, locator {locator})"


def _json_type(value: object) -> str:
    """The JSON name of a non-string value, as the producer wrote it."""

    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, Mapping):
        return "object"
    return "array" if isinstance(value, (list, tuple)) else type(value).__name__
