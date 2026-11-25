"""Only the product sets a record's state; no hand-off names one.

``knowledge_write`` refuses a ``status`` that names a state -- proposed, accepted, reviewed or
consolidated -- in an entry and in a record's ``fields``, saying that the product sets it. Retiring
stays the curator's act, and so do a decision's own words; a rewritten record loses an old stored
state word (shown by the crossing case of ``test_knowledge_writer_revision_stamps``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_writer import Owner, WriteRequest
from agents_remember.application.knowledge_writer.handoff import read_handoff
from agents_remember.application.knowledge_writer.typed_state import TYPED_STATE_WORDS
from knowledge_writer_test_support import (
    ADMISSION,
    BASE_FAMILY,
    BASE_INVARIANT,
    LEAF_ID,
    SCOPE,
    TASK_ID,
    World,
    build_world,
    entry,
    target,
    tree_bytes,
)
from writer_worklist_test_support import write_knowledge


def _write(world: World, document: dict[str, Any]) -> Any:
    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=Owner(task=TASK_ID, kind="leaf", id=LEAF_ID),
            handoff_path="handoff.json",
            document=document,
            commit=True,
        )
    )


@pytest.mark.parametrize("word", TYPED_STATE_WORDS)
def test_a_typed_state_is_refused_in_an_entry_and_in_a_records_fields(
    tmp_path: Path, word: str
) -> None:
    world = build_world(tmp_path)
    before = tree_bytes(world.memory)
    authored = entry("B-1", target=[target("land_pair")], scope=SCOPE, admission=ADMISSION)
    family = {"key": "F", "kind": "family", "id": BASE_FAMILY, "fields": {"status": word}}
    report = _write(world, {"entries": [{**authored, "status": word}], "records": [family]})
    assert report.refused
    text = report.render()
    said = f"{word!r} is a record's state, and the product sets a record's state: no tool input"
    assert f"entries[1] (B-1).status: {said}" in text
    assert f"records[1].fields.status: {said}" in text
    assert tree_bytes(world.memory) == before


def test_a_kinds_own_lifecycle_word_is_no_state_and_is_admitted() -> None:
    retiring = entry("R-1", invariant_id=BASE_INVARIANT, status="retired")
    records = [
        {"key": word, "kind": "decision", "id": "DEC-1", "fields": {"status": word}}
        for word in ("retired", "active", "under_reconsideration")
    ]
    _document, problems = read_handoff({"entries": [retiring], "records": records})
    assert not [one for one in problems if one.where.endswith("status")]
