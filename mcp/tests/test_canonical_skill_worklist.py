"""MIK-R55 canonical-card integration over real code/memory trees and the writer."""

from __future__ import annotations

import json
from pathlib import Path

from agents_remember.application.knowledge_worklist import leaf_worklist
from agents_remember.application.knowledge_writer.memory_state import Owner
from agents_remember.application.knowledge_writer.writer import WriteRequest, write_knowledge
from agents_remember.worktrees.worktree_contract import load_contract
from test_planned_knowledge_effects import _contract
from test_unexplained_change_disposition import LEAF, World, world

__all__ = ["world"]


def test_canonical_skill_card_closes_without_nine_generated_copy_rows(
    world: World, tmp_path: Path
) -> None:
    canonical = "skills/canonical/SKILL.md"
    copies = [f"copy-{n}/skills/canonical/SKILL.md" for n in range(9)]
    ordinary = "docs/ordinary.md"
    declaration = {
        "version": 1,
        "copies": [
            {
                "source": "skills",
                "targets": [{"label": f"copy-{n}", "path": f"copy-{n}/skills"} for n in range(9)],
                "ignoreNames": [],
                "ignoreSuffixes": [],
            }
        ],
    }
    files: dict[str, str | bytes | None] = dict.fromkeys([canonical, *copies, ordinary], "before\n")
    files["generated-copies.json"] = json.dumps(declaration)
    files[ordinary] = "old ordinary\n"
    world.code_base = world.code_commit(files)
    card = f"onboarding/{canonical}.md"
    world.memory_base = world.memory_commit({card: "The old skill.\n"}, world.code_base)
    candidate = world.code_commit(
        {**dict.fromkeys([canonical, *copies], "after\n"), ordinary: "changed ordinary\n"}
    )
    world.memory_commit({card: "The changed skill.\n"}, candidate)
    task = tmp_path / "task"
    task.mkdir()
    (task / "task.md").write_text("# task\n")
    contract = load_contract(_contract(world, task))
    before = leaf_worklist(contract)
    assert before is not None and before["state"] == "complete", before
    raised = [
        item for item in before["items"] if item["kind"] in {"unexplained_hunk", "unexplained_file"}
    ]
    assert {item["facts"]["path"] for item in raised} == {canonical, ordinary}
    trace = next(item for item in before["items"] if item["subject"] == f"onboarding:{canonical}")
    assert trace["facts"]["countedChange"] and trace["satisfiedBy"] is None
    report = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=Owner(task="260928-MIK", kind="leaf", id=LEAF),
            handoff_path="handoff.json",
            document={
                "history": [
                    {
                        "subject": trace["subject"],
                        "disposition": "updated",
                        "reason": "The canonical skill's card carries its changed meaning.",
                    }
                ]
            },
            worklist=before,
            code_base=world.code_base,
            memory_base=world.memory_base,
            commit=True,
        )
    )
    assert report.state == "written", report.render()
    after = leaf_worklist(contract)
    assert after is not None and after["state"] == "complete", after
    trace_after = next(item for item in after["items"] if item["subject"] == trace["subject"])
    assert trace_after["satisfiedBy"] is not None
    canonical_item = next(
        item
        for item in after["items"]
        if item["kind"] == "unexplained_hunk" and item["facts"]["path"] == canonical
    )
    assert canonical_item["satisfiedBy"] == trace_after["satisfiedBy"]
    ordinary_item = next(
        item
        for item in after["items"]
        if item["kind"] == "unexplained_hunk" and item["facts"]["path"] == ordinary
    )
    assert ordinary_item["satisfiedBy"] is None
    assert not any(
        item["kind"].startswith("unexplained_") and item["facts"]["path"] in copies
        for item in after["items"]
    )
    world.code_commit({copies[0]: "manual edit\n"})
    divergent = leaf_worklist(contract)
    assert divergent is not None and divergent["state"] == "complete", divergent
    hand_edited = next(
        item
        for item in divergent["items"]
        if item["kind"] == "unexplained_hunk" and item["facts"]["path"] == copies[0]
    )
    assert hand_edited["satisfiedBy"] is None
    assert hand_edited["facts"]["generatedCopy"] == {"canonicalPath": canonical, "matches": False}
