"""MIK-R54 writer corrections, exercised against real paired Git trees."""

from __future__ import annotations

from typing import Any

from agents_remember.application.knowledge_writer import Owner, WriteRequest
from knowledge_writer_test_support import BASE_INVARIANT, LEAF_ID, TASK_ID, World, read_json
from test_knowledge_writer import _conforming
from writer_worklist_test_support import write_knowledge

OWNER = Owner(task=TASK_ID, kind="leaf", id=LEAF_ID)


def run(world: World, document: Any, **kwargs: Any):
    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="notes/handoff.json",
            document=document,
            commit=True,
            worklist=kwargs.pop("worklist", None),
            **kwargs,
        )
    )


def linked_decision(world: World) -> tuple[str, dict[str, Any]]:
    document = _conforming()
    document["records"][0]["fields"]["links"].append(
        {"relation": "reconsider_on", "target": BASE_INVARIANT, "alternative": 1}
    )
    report = run(world, document)
    assert report.state == "written", report.render()
    outcome = next(record for record in report.records if record.kind == "decision")
    return outcome.id, read_json(world.memory, outcome.path)


def missing_requirement() -> dict[str, Any]:
    return {
        "task": {"repository": "agents-remember", "path": "260928_missing"},
        "packet": "requirements/MIK-R01-v1.md",
        "id": "MIK-R01",
        "version": "v1",
    }
