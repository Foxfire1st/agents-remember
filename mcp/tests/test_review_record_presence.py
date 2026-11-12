"""A missing review record is unknown only when that record's own file failed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents_remember.application.review_record_changes import RecordChanges


@dataclass
class Side:
    name: str
    index: Any
    problems: dict[str, str]
    detail: str | None = None


class EmptyIndex:
    def record(self, _record_id: str) -> Any:
        @dataclass
        class Answer:
            value: None = None

        return Answer()


def test_unrelated_parse_failure_does_not_hide_an_added_or_removed_record() -> None:
    changes = RecordChanges()
    side = Side(
        name="before",
        index=EmptyIndex(),
        problems={"knowledge/invariants/INV-OTHER0-broken.json": "invalid JSON"},
    )
    assert changes.record(side, "INV-TARGET", "invariant").unknown is None
    side.problems["knowledge/invariants/INV-TARGET-broken.json"] = "invalid JSON"
    own = RecordChanges().record(side, "INV-TARGET", "invariant")
    assert own.unknown is not None
    assert "knowledge/invariants/INV-TARGET-broken.json" in own.unknown
    assert "INV-OTHER0" not in own.unknown
