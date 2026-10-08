"""Typed index-read refusal values and their shared storage error."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.errors import AgentsRememberError
from agents_remember.models.knowledge.result import (
    KnowledgeOperation,
    KnowledgeRefusal,
    KnowledgeRefusalCode,
)


class KnowledgeStorageError(AgentsRememberError):
    """A storage failure that no contract refusal code describes.

    It is a defect report, not an expected outcome: a reachable expected failure returns a
    :class:`KnowledgeRefusal` with one of the contract codes.
    """


@dataclass(frozen=True)
class RefusalFacts:
    """The optional identifying facts a refusal can carry about the offending record."""

    table: str | None = None
    record_id: str | None = None
    expected: str | None = None
    observed: str | None = None


def refusal(
    code: KnowledgeRefusalCode,
    operation: KnowledgeOperation,
    detail: str,
    *,
    next_action: str,
    facts: RefusalFacts | None = None,
) -> KnowledgeRefusal:
    """Build one refusal with the exact code, offending record and next action."""

    resolved = facts or RefusalFacts()
    return KnowledgeRefusal(
        code=code,
        operation=operation,
        detail=detail,
        table=resolved.table,
        record_id=resolved.record_id,
        expected=resolved.expected,
        observed=resolved.observed,
        next_action=next_action,
    )


def selected_input_unavailable_refusal(
    operation: KnowledgeOperation, detail: str, *, record_id: str | None = None
) -> KnowledgeRefusal:
    """Refuse when one explicitly selected input is absent or unreadable.

    A missing input is an input error, never an empty dataset: the alternative -- answering an
    absent selection with a fresh schema -- is how a reader comes to report "no knowledge" for a
    candidate whose file simply was not there.
    """

    return refusal(
        "selected_input_unavailable",
        operation,
        detail,
        facts=RefusalFacts(record_id=record_id),
        next_action=(
            "Restore the exact recorded memory tree or rebuild its derived index, then retry the "
            "selected read. No current tree is substituted."
        ),
    )
