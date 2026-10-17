"""MIK-R173: a card row binds every copy of its source.

Rule 1: a card row's sources are its worklist item's sources, with their blobs from the code
candidate (``absent`` for a deleted file) -- so a card whose item covers a group of identical
copies binds the whole group. Rule 2: a card whose item names only its own path, and a card no item
names, keep today's single-source row; the overview refusal stands; ``RowRequest`` gains no
``sources`` field. Rule 4: the group proof is native end to end -- the leaf's own worklist
(``leaf_worklist``: grouping, item facts and blobs), the product's writer, and the leaf's worklist
again, whose item must come back ``satisfiedBy`` the row the writer wrote. No hand-composed item,
no constructed code snapshot and no mocked gate stand in for that loop.

The group is L56's shape: one canonical ``c-05`` skill file and its nine generated copies.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_worklist.leaf import leaf_worklist
from agents_remember.application.knowledge_writer import Owner, WriteRequest, write_knowledge
from agents_remember.application.knowledge_writer.report import WriteReport
from agents_remember.models.knowledge_files.history import HistoryFile, OnboardingTraceRow
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from knowledge_writer_test_support import (
    CODE_FILE,
    LEAF_ID,
    TASK_ID,
    TEST_FILE,
    World,
    build_world,
    canonical,
    commit_all,
    git,
    write,
)

OWNER = Owner(task=TASK_ID, kind="leaf", id=LEAF_ID)
HISTORY_PATH = f"knowledge/history/{LEAF_ID}.json"
BLOB_NEW = "b" * 40
ABSENT = "absent"

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
CHANGED_BODY = "# the template\n\nOne template, nine generated copies, one wording.\n"


# -- fixtures ------------------------------------------------------------------------------------


def _rebased(world: World, code_head: str, memory_head: str) -> WorktreeContract:
    """The leaf contract re-based on the commits that hold the group and its card.

    The fixture's contract pins the bases it was built with. A leaf whose code base is the commit
    that holds the ten copies (so the change at C is their deletion) and whose memory base is the
    commit that holds their card states both here, as a real leaf's contract does.
    """

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


def _grouped_leaf(world: World, *, deleted: bool) -> WorktreeContract:
    """The ten identical copies at the code base, then deleted (or reworded) at C, with the card.

    The card's memory commit names the code base in its ``Code-Commit`` trailer, so the leaf's own
    worklist pairs the two sides as a landed leaf's do.
    """

    write(world.code, {path: BODY for path in GROUP})
    commit_all(world.code, "the template and its nine copies")
    code_head = git(world.code, "rev-parse", "HEAD")
    if deleted:
        for path in GROUP:
            (world.code / path).unlink()
    else:
        write(world.code, {path: CHANGED_BODY for path in GROUP})
    _card(world, SKILL)
    commit_all(world.memory, f"the card of {SKILL}\n\nCode-Commit: {code_head}")
    return _rebased(world, code_head, git(world.memory, "rev-parse", "HEAD"))


def _blob(world: World, path: str) -> str:
    """``path``'s blob at the code candidate the writer and the worklist both read."""

    return git(world.code, "hash-object", path)


def _writer(
    world: World,
    subject: str,
    worklist: Mapping[str, Any],
    **extra: Any,
) -> WriteReport:
    """One committing leaf run whose whole document is a ``no_impact`` row for ``subject``.

    The code snapshot is the writer's own capture of the worktree — no constructed snapshot is
    handed over, so the row's blobs are the candidate's real ones (``absent`` for a deleted file).
    """

    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="handoff.json",
            document={
                "history": [
                    {
                        "subject": subject,
                        "disposition": "no_impact",
                        "reason": "The card was read and needs no change.",
                        **extra,
                    }
                ]
            },
            commit=True,
            worklist=worklist,
        )
    )


def _stored_row(world: World, subject: str) -> OnboardingTraceRow:
    stored = HistoryFile.model_validate(
        json.loads((world.memory / HISTORY_PATH).read_text(encoding="utf-8"))
    )
    row = stored.row_about(subject)
    assert isinstance(row, OnboardingTraceRow)
    return row


def _trace_item(worklist: Mapping[str, Any], subject: str) -> Mapping[str, Any]:
    """The leaf's own ``onboarding_trace`` item for ``subject``, out of its worklist run."""

    found = [
        item
        for item in worklist["items"]
        if isinstance(item, Mapping)
        and item.get("kind") == "onboarding_trace"
        and item.get("subject") == subject
    ]
    assert len(found) == 1, (subject, [item.get("subject") for item in worklist["items"]])
    return found[0]


def _item(subject: str, sources: tuple[str, ...], blobs: Mapping[str, str]) -> dict[str, Any]:
    """A control item, for the preserved behaviors whose group is not this leaf's subject."""

    return {
        "id": "sha256:" + "d" * 64,
        "kind": "onboarding_trace",
        "subject": subject,
        "facts": {
            "sources": list(sources),
            "countedChange": False,
            "sourceBlobs": dict(blobs),
        },
    }


# -- rule 1 and rule 4: the group binds, and the leaf's worklist discharges it --------------------


def test_a_card_row_binds_every_copy_of_its_source(tmp_path: Path) -> None:
    """L56's shape: ten identical copies deleted at C; the row binds ten and the item closes."""

    world = build_world(tmp_path)
    contract = _grouped_leaf(world, deleted=True)
    subject = f"onboarding:{SKILL}"
    before = leaf_worklist(contract)
    assert before is not None
    item = _trace_item(before, subject)
    sources = tuple(item["facts"]["sources"])
    assert len(sources) == 10 and set(sources) == set(GROUP)
    assert item["facts"]["sourceBlobs"] == {path: ABSENT for path in sources}
    assert item["satisfiedBy"] is None  # the item is open before the row exists
    report = _writer(world, subject, before)
    assert report.state == "written", report.render()
    row = _stored_row(world, subject)
    assert [(source.path, source.blob) for source in row.sources or ()] == [
        (path, ABSENT) for path in sources
    ]
    after = leaf_worklist(contract, persist=False)
    assert after is not None
    assert _trace_item(after, subject)["satisfiedBy"] == row.id  # answered by the row it wrote


def test_a_grouped_card_row_binds_the_copies_blobs_when_they_are_present(tmp_path: Path) -> None:
    """The same group reworded at C: every copy binds its blob, and the item closes."""

    world = build_world(tmp_path)
    contract = _grouped_leaf(world, deleted=False)
    subject = f"onboarding:{SKILL}"
    before = leaf_worklist(contract)
    assert before is not None
    item = _trace_item(before, subject)
    sources = tuple(item["facts"]["sources"])
    blobs = dict(item["facts"]["sourceBlobs"])
    assert len(sources) == 10 and set(sources) == set(GROUP)
    assert blobs == {path: _blob(world, path) for path in GROUP}
    report = _writer(world, subject, before)
    assert report.state == "written", report.render()
    row = _stored_row(world, subject)
    assert [(source.path, source.blob) for source in row.sources or ()] == [
        (path, blobs[path]) for path in sources
    ]
    after = leaf_worklist(contract, persist=False)
    assert after is not None
    assert _trace_item(after, subject)["satisfiedBy"] == row.id


# -- rule 2: nothing else changes -----------------------------------------------------------------


@pytest.mark.parametrize(
    "item_sources",
    [None, (CODE_FILE,)],
    ids=["no item names the card", "the item names only its own path"],
)
def test_a_single_source_card_row_is_unchanged(
    tmp_path: Path, item_sources: tuple[str, ...] | None
) -> None:
    """A card with one source keeps today's row, whichever way its source is named."""

    world = build_world(tmp_path)
    subject = f"onboarding:{CODE_FILE}"
    blob = _blob(world, CODE_FILE)
    items: list[dict[str, Any]] = []
    if item_sources is not None:
        items.append(_item(subject, item_sources, {CODE_FILE: blob}))
    report = _writer(world, subject, {"items": items})
    assert report.state == "written", report.render()
    row = _stored_row(world, subject)
    assert [(source.path, source.blob) for source in row.sources or ()] == [(CODE_FILE, blob)]


def test_an_overview_row_keeps_its_refusal_and_binds_its_items_sources(tmp_path: Path) -> None:
    """The overview branch R173 rule 1 preserves: no item refuses, an item binds its files."""

    world = build_world(tmp_path)
    refused = _writer(world, "onboarding:overview", {"items": []})
    assert refused.state == "refused"
    assert any(
        "no worklist item covers onboarding:overview" in problem.render()
        for problem in refused.problems
    )
    governed = (CODE_FILE, TEST_FILE)
    blobs = {path: _blob(world, path) for path in governed}
    report = _writer(
        world,
        "onboarding:overview",
        {"items": [_item("onboarding:overview", governed, blobs)]},
    )
    assert report.state == "written", report.render()
    row = _stored_row(world, "onboarding:overview")
    assert [(source.path, source.blob) for source in row.sources or ()] == [
        (path, blobs[path]) for path in governed
    ]


def test_an_author_supplied_sources_field_is_still_refused(tmp_path: Path) -> None:
    """``RowRequest`` stays as it is: sources are the writer's, never the curator's input."""

    world = build_world(tmp_path)
    refused = _writer(
        world,
        f"onboarding:{CODE_FILE}",
        {"items": []},
        sources=[{"path": CODE_FILE, "blob": BLOB_NEW}],
    )
    assert refused.state == "refused"
    assert any("the writer owns ['sources']" in problem.render() for problem in refused.problems)
