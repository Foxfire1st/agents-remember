"""MIK-R177: the gate groups identical copies as the worklist does.

Rule 1 (one construction): the memory-quality gate builds its ``onboarding_trace`` items with the
worklist run's own ``identicalChanges`` groups, from that document -- the same groups
``worklist_onboarding`` passes. A gate entry that cannot read the groups refuses by name and never
falls back to ungrouped items. MIK-R56 rules 1 and 4 retire the certified closeout, so the closeout
validator this file also covered is gone with it and its cases are removed here; the grouped
construction it shared is the one the gate keeps.

Rule 2 (nothing else changes): a source without copies yields the same single-source item as
before, and the worklist, the item contract and the writer stay as they are.

Rule 3 (proof): L56's shape -- ten identical copies deleted at C, one carded owner -- answered by
one row binding every copy, satisfies the worklist and the memory-quality gate alike. The empty-group case is the pre-change behavior: the same row leaves the gate's
singleton item open, which is the mismatch this leaf removes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from agents_remember.application.knowledge_worklist import (
    leaf_onboarding_trace_sides,
    leaf_worklist,
)
from agents_remember.application.knowledge_worklist.onboarding_trace import (
    worktree_code_blobs,
)
from agents_remember.application.knowledge_writer import Owner, WriteRequest, write_knowledge
from agents_remember.application.knowledge_writer.report import WriteReport
from agents_remember.application.memory_quality import controller
from agents_remember.application.memory_scope import MemoryScope
from agents_remember.kernel.coordination_context.models import StorageSettings
from agents_remember.models.knowledge_files.history import HistoryFile, OnboardingTraceRow
from agents_remember.worktrees.modules.onboarding import (
    OnboardingTraceInputs,
    onboarding_trace_gate_for_context,
)
from agents_remember.worktrees.modules.onboarding_trace import (
    CHECK_NAME,
    INCOMPLETE_CODE,
    MISSING_CODE,
    OnboardingTraceResult,
    TraceItem,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from knowledge_writer_test_support import (
    CODE_FILE,
    LEAF_ID,
    TASK_ID,
    World,
    build_world,
    canonical,
    commit_all,
    git,
    write,
)

OWNER = Owner(task=TASK_ID, kind="leaf", id=LEAF_ID)
ABSENT = "absent"
OVERVIEW_SUBJECT = "onboarding:overview"

SKILL = "skills/c-05-write-onboarding/assets/file-level-onboarding-template.md"
COPY_TARGETS = (
    "mcp/src/agents_remember/package_data/runtime/skills",
    ".claude/skills",
    ".codex/skills",
    ".cursor/skills",
    ".github-vscode/skills",
    ".hermes/skills",
    ".openclaw/workspace/skills",
    ".pi/skills",
    ".agents/skills",
)
COPIES = tuple(
    f"{target}/c-05-write-onboarding/assets/file-level-onboarding-template.md"
    for target in COPY_TARGETS
)
GROUP = (SKILL, *COPIES)
BODY = "# the template\n\nOne template, nine generated copies.\n"
GROUPS = "identical-copy groups"


# -- fixtures ------------------------------------------------------------------------------------


def _rebased(world: World, code_head: str, memory_head: str) -> WorktreeContract:
    """The leaf contract re-based on the commits that hold the group and its card."""

    text = world.contract.read_text(encoding="utf-8")
    head, _, rest = text.partition("code:")
    code_block, _, tail = rest.partition("memory:")
    code_block = re.sub(
        r"(?m)^  base_commit: .*$", f"  base_commit: {code_head}", code_block, count=1
    )
    tail = re.sub(r"(?m)^  base_commit: .*$", f"  base_commit: {memory_head}", tail, count=1)
    world.contract.write_text(f"{head}code:{code_block}memory:{tail}", encoding="utf-8")
    return load_contract(world.contract)


def _card(world: World, source: str) -> None:
    """A file card for ``source``: the only carded member of its group, so the group's owner."""

    write(
        world.memory,
        {
            f"onboarding/{source}.md": f"# {source}\n\nIt governs the template.\n",
            f"onboarding/{source}.json": canonical(
                {
                    "schema": "ar-onboarding-file/v1",
                    "path": source,
                    "references": {},
                    "realizes": [],
                }
            ),
        },
    )


def _grouped_leaf(world: World) -> WorktreeContract:
    """Ten identical copies at the code base, deleted at C, with the one card that owns them."""

    write(world.code, {path: BODY for path in GROUP})
    commit_all(world.code, "the template and its nine copies")
    code_head = git(world.code, "rev-parse", "HEAD")
    for path in GROUP:
        (world.code / path).unlink()
    _card(world, SKILL)
    commit_all(world.memory, f"the card of {SKILL}\n\nCode-Commit: {code_head}")
    return _rebased(world, code_head, git(world.memory, "rev-parse", "HEAD"))


def _context(world: World) -> SimpleNamespace:
    """The storage settings and onboarding root the gate reads, as a leaf context carries them."""

    return SimpleNamespace(
        storage=StorageSettings(),
        code_repository_name="agents-remember",
        onboarding_root=world.memory / "onboarding",
    )


def _writer(world: World, rows: list[dict[str, Any]], worklist: Mapping[str, Any]) -> WriteReport:
    """One committing leaf run whose whole document is ``rows``, over ``worklist``'s items."""

    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="handoff.json",
            document={"history": rows},
            commit=True,
            worklist=worklist,
        )
    )


def _row(subject: str) -> dict[str, Any]:
    return {
        "subject": subject,
        "disposition": "no_impact",
        "reason": "The card was read and needs no change.",
    }


def _stored_row(world: World, subject: str) -> OnboardingTraceRow:
    stored = HistoryFile.model_validate(
        json.loads((world.memory / f"knowledge/history/{LEAF_ID}.json").read_text("utf-8"))
    )
    row = stored.row_about(subject)
    assert isinstance(row, OnboardingTraceRow)
    return row


def _trace_item(worklist: Mapping[str, Any], subject: str) -> Mapping[str, Any]:
    found = [
        item
        for item in worklist["items"]
        if isinstance(item, Mapping)
        and item.get("kind") == "onboarding_trace"
        and item.get("subject") == subject
    ]
    assert len(found) == 1, (subject, [item.get("subject") for item in worklist["items"]])
    return found[0]


def _sides(world: World, contract: WorktreeContract) -> Any:
    sides = leaf_onboarding_trace_sides(contract)
    assert sides is not None and sides.incomplete is None
    return sides


def _gate(
    world: World,
    contract: WorktreeContract,
    changed: list[str],
    groups: Mapping[str, list[str]],
    working: list[str],
) -> tuple[OnboardingTraceResult, list[dict[str, str]]]:
    """The memory-quality gate entry, over the leaf's own sides and candidate blobs."""

    return onboarding_trace_gate_for_context(
        _context(world),
        changed,
        _sides(world, contract),
        OnboardingTraceInputs(
            code_blobs=worktree_code_blobs(world.code, changed),
            identical_changes=groups,
            working_paths=working,
        ),
    )


def _answered_group(world: World, contract: WorktreeContract) -> tuple[list[str], dict[str, Any]]:
    """The group deleted at C, and the worklist run plus the two rows the writer wrote for it."""

    before = leaf_worklist(contract)
    assert before is not None and before["state"] == "complete"
    changed = sorted({str(change["path"]) for change in before["changes"]})
    assert set(changed) == set(GROUP)
    report = _writer(world, [_row(f"onboarding:{SKILL}"), _row(OVERVIEW_SUBJECT)], before)
    assert report.state == "written", report.render()
    return changed, before


def _by_subject(result: OnboardingTraceResult) -> dict[str, TraceItem]:
    return {item.subject: item for item in result.items}


# -- rule 1 and rule 3: one construction, and the proof ------------------------------------------


def test_one_row_binding_every_copy_answers_the_worklist_and_the_gate(
    tmp_path: Path,
) -> None:
    """L56's shape: the ten-copy card the worklist closes is the card the memory-quality gate closes."""

    world = build_world(tmp_path)
    contract = _grouped_leaf(world)
    subject = f"onboarding:{SKILL}"
    known = leaf_worklist(contract)
    assert known is not None
    item = _trace_item(known, subject)
    sources = tuple(item["facts"]["sources"])
    assert len(sources) == 10 and set(sources) == set(GROUP)
    assert item["satisfiedBy"] is None  # open before the row exists
    assert item["facts"]["sourceBlobs"] == {path: ABSENT for path in sources}
    groups = dict(known["identicalChanges"])
    assert groups == {SKILL: list(sources)}
    changed, before = _answered_group(world, contract)
    assert before["identicalChanges"] == groups
    row = _stored_row(world, subject)
    assert [(one.path, one.blob) for one in row.sources or ()] == [
        (path, ABSENT) for path in sources
    ]
    after = leaf_worklist(contract, persist=False)
    assert after is not None
    assert _trace_item(after, subject)["satisfiedBy"] == row.id  # the worklist's own item closes
    result, findings = _gate(world, contract, changed, groups, [])
    assert findings == [] and result.ok  # the memory-quality gate raises the same item
    assert set(_by_subject(result)) == {subject, OVERVIEW_SUBJECT}
    assert {one.id for one in result.items} == {
        _trace_item(known, subject)["id"],
        _trace_item(known, OVERVIEW_SUBJECT)["id"],
    }


def test_an_empty_group_is_the_old_singleton_item_and_the_row_does_not_answer_it(
    tmp_path: Path,
) -> None:
    """The pre-change behavior this leaf removes: ungrouped, the row and the item disagree."""

    world = build_world(tmp_path)
    contract = _grouped_leaf(world)
    subject = f"onboarding:{SKILL}"
    known = leaf_worklist(contract)
    assert known is not None
    changed, _before = _answered_group(world, contract)
    row = _stored_row(world, subject)
    assert len(row.sources or ()) == 10
    result, findings = _gate(world, contract, changed, {}, [])
    item = _by_subject(result)[subject]
    assert item.sources == (SKILL,)  # the singleton construction, not the worklist's item
    assert item.id != _trace_item(known, subject)["id"]
    assert not result.ok and item.row == row.id and not item.row_answers
    assert [finding["code"] for finding in findings] == [MISSING_CODE]


def test_the_quality_run_refuses_by_name_when_the_worklist_run_names_no_groups(
    tmp_path: Path,
) -> None:
    """The entry reads the groups from the run's own document, and never judges without them."""

    world = build_world(tmp_path)
    contract = _grouped_leaf(world)
    changed, before = _answered_group(world, contract)
    scope = cast(
        MemoryScope,
        SimpleNamespace(
            contract=contract,
            onboarding_root=world.memory / "onboarding",
            quality_context=_context(world),
        ),
    )

    def gate(worklist: tuple[dict[str, Any], str | None] | None, response: dict[str, object]):
        return controller._onboarding_refresh_gate(
            scope,
            controller._OnboardingGateInputs(
                traced_paths=changed,
                working_paths=[],
                worklist=worklist,
            ),
            response,
        )

    response: dict[str, object] = {}
    findings, report_only = gate((before, "/scratch/worklist.json"), response)
    assert findings == [] and report_only == []
    trace = response["onboardingTrace"]
    assert isinstance(trace, dict) and trace["openCount"] == 0
    refused, report_only = gate(
        ({"state": "incomplete", "items": []}, "/scratch/worklist.json"), {}
    )
    (finding,) = refused
    assert report_only == []
    assert finding["check"] == CHECK_NAME and finding["code"] == INCOMPLETE_CODE
    assert finding["path"] == "/scratch/worklist.json"
    assert GROUPS in finding["message"] and "judges no ungrouped items" in finding["message"]
    absent, _ = gate(None, {})
    (finding,) = absent
    assert finding["code"] == INCOMPLETE_CODE and "no worklist run" in finding["message"]


def test_each_gate_refuses_a_malformed_group_by_name(tmp_path: Path) -> None:
    """L175-F02: a malformed entry is named by the gate's reader, never dropped into a singleton."""

    world = build_world(tmp_path)
    contract = _grouped_leaf(world)
    path = world.task_root / "knowledge-worklist.json"
    persisted = leaf_worklist(contract)
    assert persisted is not None and path.is_file()
    run = json.loads(path.read_text(encoding="utf-8"))
    for malformed, expected in (
        ({SKILL: "not-a-source-list"}, "is not a list of source paths"),
        ({SKILL: [SKILL, 7]}, "holds a member that is not a source path"),
        ({SKILL: [SKILL, ""]}, "holds a member that is not a source path"),
        ({SKILL: [COPIES[0]]}, "does not name its representative"),
        ({SKILL: []}, "names no member"),
        ({"": [SKILL]}, "key is not a source path"),
    ):
        document = {**run, "identicalChanges": malformed}
        path.write_text(json.dumps(document), encoding="utf-8")
        groups, reason = controller._worklist_identical_changes((document, path.as_posix()))
        assert groups is None, malformed
        assert expected in reason, (malformed, reason)


def test_a_run_with_no_groups_is_a_valid_empty_map_for_the_gate(tmp_path: Path) -> None:
    """L175-F02: a genuine empty group map stays valid; only a malformed entry refuses."""

    world = build_world(tmp_path)
    contract = _single_source_leaf(world)
    run = leaf_worklist(contract)
    assert run is not None and run["identicalChanges"] == {}
    assert controller._worklist_identical_changes((run, "knowledge-worklist.json")) == ({}, "")


# -- rule 2: nothing else changes -----------------------------------------------------------------


def _single_source_leaf(world: World) -> WorktreeContract:
    """One source changed at C with no copy and no identical change: the ordinary shape."""

    write(world.code, {CODE_FILE: "X = 0\n"})
    commit_all(world.code, "the code base")
    code_head = git(world.code, "rev-parse", "HEAD")
    write(world.code, {CODE_FILE: "X = 1\n"})  # the leaf's own uncommitted change at C
    git(
        world.memory,
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        f"the card of {CODE_FILE}\n\nCode-Commit: {code_head}",
    )
    return _rebased(world, code_head, git(world.memory, "rev-parse", "HEAD"))


def test_a_single_source_card_is_the_same_item_in_the_worklist_and_the_gate(
    tmp_path: Path,
) -> None:
    """A source without copies keeps the one-item construction it has today."""

    world = build_world(tmp_path)
    contract = _single_source_leaf(world)
    known = leaf_worklist(contract)
    assert known is not None and known["identicalChanges"] == {}
    changed = sorted({str(change["path"]) for change in known["changes"]})
    assert changed == [CODE_FILE]
    subject = f"onboarding:{CODE_FILE}"
    assert _trace_item(known, subject)["facts"]["sources"] == [CODE_FILE]
    report = _writer(world, [_row(subject), _row(OVERVIEW_SUBJECT)], known)
    assert report.state == "written", report.render()
    after = leaf_worklist(contract, persist=False)
    assert after is not None
    assert _trace_item(after, subject)["satisfiedBy"] == _stored_row(world, subject).id
    result, findings = _gate(world, contract, changed, dict(known["identicalChanges"]), changed)
    assert findings == [] and result.ok
    assert _by_subject(result)[subject].sources == (CODE_FILE,)
