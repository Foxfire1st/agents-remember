"""The curator-authored applicability and clauses carried by an invariant revision."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH


@dataclass(frozen=True)
class CuratorScope:
    applicability: str
    conditions: tuple[str, ...]
    exclusions: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {
            "applicability": self.applicability,
            "conditions": self.conditions,
            "exclusions": self.exclusions,
        }


def read_curator_scope(value: object) -> CuratorScope | str:
    """Require authored semantic scope; absence is unfinished curation, never a default claim."""

    if not isinstance(value, Mapping) or set(value) != {
        "applicability",
        "conditions",
        "exclusions",
    }:
        return (
            "author scope with applicability, conditions and exclusions before recording the entry"
        )
    applicability = value["applicability"]
    if (
        not isinstance(applicability, str)
        or not applicability.strip()
        or len(applicability) > PROSE_MAX_LENGTH
    ):
        return "scope.applicability must be nonblank authored text within the revision prose limit"
    for key in ("conditions", "exclusions"):
        clauses = value[key]
        if not isinstance(clauses, list) or any(
            not isinstance(clause, str) or not clause.strip() for clause in clauses
        ):
            return f"scope.{key} must be a list of nonblank clauses; [] explicitly means none"
    return CuratorScope(
        applicability.strip(),
        tuple(clause.strip() for clause in value["conditions"]),
        tuple(clause.strip() for clause in value["exclusions"]),
    )
