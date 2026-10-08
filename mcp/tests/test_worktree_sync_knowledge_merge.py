"""MIK-R26 rule 2: every managed sync of converted memory merges its knowledge structurally.

Knowledge is text in Git. A memory merge whose three trees are converted runs the structural merge
of MIK-R24 rule 8 step 3 -- records field by field, sidecars item by item, cards by Git's three-way
text merge -- and the file validator runs before the merge is committed. These cases drive the real
``worktree_sync`` transaction over two converted lines that both changed the same file:

* mechanical anchor fields never conflict (the incoming side's are taken);
* an authored change beats a mechanical one, whole;
* two different authored changes are left for the agent as an explicit marker, which the validator
  refuses until it is resolved;
* a reference number both sides added conflicts although the Markdown merges;
* a record both sides changed is resolved through the writer at one more than the higher revision;
* an item deleted on one side and re-anchored on the other is deleted;
* ``cancel`` restores the line;
* a master line's merge opens its crossing history file;
* unconverted lines still merge as plain Git, without the structural merge being asked;
* a journal a build before MIK-R26 wrote still loads.

The fixture is the conversion test support's memory (two cards, a route card, a legacy database with
two invariants and a family), converted on the official line and fast-forwarded into the leaf.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from agents_remember.application.knowledge_writer import Owner, WriteRequest, write_knowledge
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.memory.conversion import crossing_port, inputs
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.conversion.convert import convert_memory
from agents_remember.memory.conversion.inputs import memory_from_directory, write_changed
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.ids import derived_record_id
from agents_remember.worktrees.knowledge_crossing import (
    apply_crossing,
    crossing_plan,
    merge_base,
    merge_structure,
)
from agents_remember.worktrees.services import bind_worktree_services
from agents_remember.worktrees.services import worktree_services as bound_worktree_services
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncOperationStore,
    SyncSideRecord,
    sync_operation_path,
)
from agents_remember_test_support.testing.database_retirement_guard import (
    CONNECT_READ_ONLY,
    events_beneath,
    observed_database_events,
)
from knowledge_conversion_test_support import (
    APP,
    APP_CARD,
    APP_SIDECAR,
    APP_SOURCE,
    DELETED,
    GUIDE,
    TEST_SOURCE,
    CodeFixture,
    commit_files,
    git,
    legacy_database,
    memory_files,
)
from test_worktree_sync import SyncFixture

RECORD = f"knowledge/invariants/{derived_record_id('invariant', 'inv-one')}-FIX-I-1.json"


@dataclass(frozen=True)
class Lines:
    """Two converted lines of one memory repository: the official line and a leaf in sync with it."""

    fixture: SyncFixture
    leaf: Path
    line: Path

    def on_leaf(self, files: dict[str, bytes | None], message: str = "leaf change") -> str:
        return _commit(self.leaf, files, message)

    def on_line(self, files: dict[str, bytes | None], message: str = "line change") -> str:
        """Commit on the official line, attributed to a code commit that moved with it."""

        code = self.fixture.code_repo
        moves = len(git(code, "rev-list", "main").split())
        code_tip = commit_files(code, {"src/moved.py": f"MOVES = {moves}\n"}, "code moves")
        return _commit(self.line, files, render_memory_content_message(message, code_tip))

    def sync(self, **kwargs: Any) -> dict[str, Any]:
        return dict(self.fixture.sync(**kwargs).payload)

    def merge(self) -> dict[str, Any]:
        return self.sync(memory_sync_choice="merge-memory")

    def read(self, relative: str) -> dict[str, Any]:
        return json.loads((self.leaf / relative).read_text(encoding="utf-8"))

    def journal(self) -> dict[str, Any]:
        path = sync_operation_path(self.fixture.contract.worktree_group)
        return json.loads(path.read_text(encoding="utf-8"))


def _commit(root: Path, files: dict[str, bytes | None], message: str) -> str:
    for relative, data in files.items():
        target = root / relative
        if data is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    git(root, "add", "-A", "--", *files)
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


def _unconverted_line(tmp_path: Path) -> tuple[SyncFixture, CodeFixture]:
    """The official memory line holding the conversion fixture's unconverted memory."""

    fixture = SyncFixture(tmp_path)
    code = fixture.code_repo
    first = commit_files(
        code,
        {
            DELETED: "GONE = 1\n",
            APP: APP_SOURCE,
            "tests/test_app.py": TEST_SOURCE,
            "docs/guide.md": GUIDE,
        },
        "first",
    )
    deleted_blob = git(code, "rev-parse", f"{first}:{DELETED}")
    head = commit_files(code, {DELETED: None}, "second")
    held = CodeFixture(
        root=code,
        first=first,
        head=head,
        deleted_blob=deleted_blob,
        app_blob=git(code, "rev-parse", f"{head}:{APP}"),
    )
    for relative, text in memory_files(first).items():
        target = fixture.memory_repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    legacy_database(fixture.memory_repo / "knowledge.sqlite", held)
    git(fixture.memory_repo, "add", "-A")
    git(fixture.memory_repo, "commit", "-q", "-m", render_memory_content_message("cards", head))
    return fixture, held


def _converted_lines(tmp_path: Path) -> Lines:
    fixture, code = _unconverted_line(tmp_path)
    outcome = convert_memory(
        memory_from_directory(fixture.memory_repo), CodeObjects(code.root), paired_commit=code.head
    )
    write_changed(fixture.memory_repo, outcome.changed)
    git(fixture.memory_repo, "add", "-A")
    git(
        fixture.memory_repo,
        "commit",
        "-q",
        "-m",
        render_memory_content_message("convert", code.head),
    )
    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    lines = Lines(fixture=fixture, leaf=worktree, line=fixture.memory_repo)
    first = lines.merge()
    assert first["state"] == "synced", first
    assert (worktree / "knowledge/layout.json").is_file() and (worktree / RECORD).is_file()
    return lines


def _sidecar(root: Path) -> dict[str, Any]:
    return json.loads((root / APP_SIDECAR).read_text(encoding="utf-8"))


def _edited(document: dict[str, Any], number: str, **changes: Any) -> bytes:
    """The sidecar with reference ``number`` changed: ``note`` (authored), or anchor fields."""

    edited = json.loads(json.dumps(document))
    reference = edited["references"][number]
    for key, value in changes.items():
        if key == "note":
            reference["note"] = value
        else:
            reference["targets"][0]["anchor"][key] = value
    return canonical_text(edited).encode("utf-8")


def _without(document: dict[str, Any], number: str) -> bytes:
    edited = json.loads(json.dumps(document))
    del edited["references"][number]
    return canonical_text(edited).encode("utf-8")


def _anchor(root: Path, number: str = "1") -> dict[str, Any]:
    return _sidecar(root)["references"][number]["targets"][0]["anchor"]


def test_two_lines_that_re_anchored_one_entry_merge_without_a_conflict(tmp_path: Path) -> None:
    """Rule 2: mechanical anchor fields never conflict, and the incoming side's are taken.

    Catches a sync that left converted memory to Git's line merge: both lines changed the same two
    lines of the sidecar, which Git reports as a conflict.
    """

    with observed_database_events() as while_converting:
        lines = _converted_lines(tmp_path)
    base = _sidecar(lines.leaf)
    base_commit = git(lines.leaf, "rev-parse", "HEAD")
    assert git(lines.leaf, "rev-parse", f"{base_commit}:knowledge.sqlite")
    lines.on_leaf(
        {
            "knowledge.sqlite": None,
            APP_SIDECAR: _edited(base, "1", blob="b" * 40, content="sha256:" + "1" * 64),
        }
    )
    lines.on_line(
        {
            "knowledge.sqlite": None,
            APP_SIDECAR: _edited(base, "1", blob="c" * 40, content="sha256:" + "2" * 64),
        }
    )
    assert not (lines.leaf / "knowledge.sqlite").exists()
    assert not (lines.line / "knowledge.sqlite").exists()
    untouched = lines.leaf / APP_CARD
    before = untouched.stat().st_mtime_ns
    loaded = []
    read_memory = crossing_port.memory_from_git

    def observe(*args, **kwargs):
        memory = read_memory(*args, **kwargs)
        loaded.append((memory.converted, memory.database))
        return memory

    with (
        mock.patch.object(crossing_port, "memory_from_git", side_effect=observe),
        mock.patch.object(inputs, "run_git", wraps=inputs.run_git) as database_resolution,
        mock.patch.object(
            inputs, "read_git_blobs_bytes", wraps=inputs.read_git_blobs_bytes
        ) as copies,
        observed_database_events() as during_the_merge,
    ):
        merged = lines.merge()

    assert merged["state"] == "synced", merged
    # The guard was watching: it saw the conversion read the legacy database of the fixture.
    assert CONNECT_READ_ONLY in {event.operation for event in while_converting}
    # The merge base still holds the old database blob; the merge neither opened a database nor
    # wrote, copied or moved a database file beneath this test's directory. (An event elsewhere
    # may be a thread another test left behind; the two mocks below pin that no blob was copied.)
    assert [event.render() for event in events_beneath(tmp_path, during_the_merge)] == []
    assert merged["memory"]["plan"] == "merge"
    assert loaded == [(True, None), (True, None), (True, None)]
    database_resolution.assert_not_called()
    copies.assert_not_called()
    assert _anchor(lines.leaf)["blob"] == "c" * 40
    assert _anchor(lines.leaf)["content"] == "sha256:" + "2" * 64
    assert git(lines.leaf, "status", "--porcelain") == ""
    # A clean structural merge leaves nothing to read: no report, and the journal says so.
    assert "crossingReport" not in lines.journal()["memory"]
    # Only what the merge changed is written: a card neither line touched keeps its file time.
    assert untouched.stat().st_mtime_ns == before


def test_an_authored_change_beats_a_mechanical_one_whole(tmp_path: Path) -> None:
    """One line reworded the entry, the other only re-anchored it: the reworded item is kept whole.

    Catches a field-wise mix (the authored note with the other side's anchor), and a conflict.
    """

    lines = _converted_lines(tmp_path)
    base = _sidecar(lines.leaf)
    note = "alpha adds one, exactly."
    lines.on_leaf({APP_SIDECAR: _edited(base, "1", note=note)})
    lines.on_line({APP_SIDECAR: _edited(base, "1", blob="c" * 40)})

    merged = lines.merge()

    assert merged["state"] == "synced", merged
    reference = _sidecar(lines.leaf)["references"]["1"]
    assert reference["note"] == note
    assert reference["targets"][0]["anchor"] == base["references"]["1"]["targets"][0]["anchor"]


def test_two_authored_changes_are_left_marked_and_refused_until_resolved(tmp_path: Path) -> None:
    """Both lines reworded one entry: the item is an explicit marker, and ``continue`` validates.

    Catches a silent ours or theirs, a marker the validator lets through, and a cancel that leaves
    the merge in place.
    """

    lines = _converted_lines(tmp_path)
    base = _sidecar(lines.leaf)
    leaf_head = lines.on_leaf({APP_SIDECAR: _edited(base, "1", note="Leaf wording.")})
    line_head = lines.on_line({APP_SIDECAR: _edited(base, "1", note="Line wording.")})

    conflicted = lines.merge()

    assert conflicted["state"] == "sync-resolution-required", conflicted
    resolution = conflicted["resolution"]
    assert resolution["files"] == [APP_SIDECAR]
    crossing = resolution["crossing"]
    assert [(one["path"], one["item"]) for one in crossing["conflicts"]] == [
        (APP_SIDECAR, "references.1")
    ]
    assert crossing["converted"] == []  # nothing was converted: this is not a crossing
    assert "structural merge of knowledge files" in str(conflicted["summary"])
    report = json.loads(Path(crossing["reportPath"]).read_text(encoding="utf-8"))
    item = report["conflicts"][0]
    assert (item["own"]["note"], item["incoming"]["note"]) == ("Leaf wording.", "Line wording.")
    assert set(_sidecar(lines.leaf)["references"]["1"]) == {"crossing-conflict"}
    assert git(lines.leaf, "rev-parse", "MERGE_HEAD") == line_head

    # Cancel restores the leaf's line; the same sync then conflicts again.
    cancelled = lines.sync(resolution_action="cancel")
    assert cancelled["state"] == "sync-cancelled", cancelled
    assert git(lines.leaf, "rev-parse", "HEAD") == leaf_head
    assert git(lines.leaf, "status", "--porcelain") == ""
    assert _sidecar(lines.leaf)["references"]["1"]["note"] == "Leaf wording."
    assert lines.merge()["state"] == "sync-resolution-required"

    # Staging the marker is refused by the validator: never a silent choice.
    git(lines.leaf, "add", APP_SIDECAR)
    refused = lines.sync(resolution_action="continue")
    assert refused["state"] == "sync-knowledge-validation-refused", refused
    assert APP_SIDECAR in str(refused["summary"])

    (lines.leaf / APP_SIDECAR).write_bytes(_edited(base, "1", note="Both wordings."))
    git(lines.leaf, "add", APP_SIDECAR)
    resolved = lines.sync(resolution_action="continue")
    assert resolved["state"] == "synced", resolved
    assert _sidecar(lines.leaf)["references"]["1"]["note"] == "Both wordings."
    assert git(lines.leaf, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:] == [
        leaf_head,
        line_head,
    ]


def test_a_reference_number_both_lines_added_conflicts_though_the_markdown_merges(
    tmp_path: Path,
) -> None:
    """Both lines added ``[n]`` with different targets in different paragraphs: a conflict.

    Catches a merge that kept one side's reference under a number the other side's text cites.
    """

    lines = _converted_lines(tmp_path)
    base = _sidecar(lines.leaf)
    card = (lines.leaf / APP_CARD).read_text(encoding="utf-8")
    number = str(max(int(key) for key in base["references"]) + 1)
    first, last = card.index("\n\n") + 2, card.rstrip("\n").rindex("\n") + 1

    def added(note: str, at: int) -> dict[str, bytes | None]:
        document = json.loads(json.dumps(base))
        document["references"][number] = {**base["references"]["1"], "note": note}
        text = card[:at] + f"See [{number}].\n\n" + card[at:]
        if at == last:
            text = card.rstrip("\n") + f"\n\nSee [{number}].\n"
        return {
            APP_SIDECAR: canonical_text(document).encode("utf-8"),
            APP_CARD: text.encode("utf-8"),
        }

    lines.on_leaf(added("The leaf's reference.", first))
    lines.on_line(added("The line's reference.", last))

    conflicted = lines.merge()

    assert conflicted["state"] == "sync-resolution-required", conflicted
    crossing = conflicted["resolution"]["crossing"]
    assert [(one["path"], one["item"]) for one in crossing["conflicts"]] == [
        (APP_SIDECAR, f"references.{number}")
    ]
    assert "renumber one side" in crossing["conflicts"][0]["reason"]
    merged_card = (lines.leaf / APP_CARD).read_text(encoding="utf-8")
    assert merged_card.count(f"See [{number}].") == 2 and "<<<<<<<" not in merged_card


def test_a_record_both_lines_changed_is_resolved_through_the_writer(tmp_path: Path) -> None:
    """MIK-R24 rule 8 step 4 in an ordinary sync: the record's resolution is one more than the
    higher side's revision, and its row is in the leaf's history file.

    Catches a record merged by text lines, and a resolution committed without a row.
    """

    lines = _converted_lines(tmp_path)
    record = lines.read(RECORD)

    def changed(statement: str, revision: int) -> dict[str, bytes | None]:
        document = {**record, "statement": statement, "revision": revision}
        return {RECORD: canonical_text(document).encode("utf-8")}

    lines.on_leaf(changed("Leaf statement.", record["revision"] + 1))
    lines.on_line(changed("Line statement.", record["revision"] + 2))

    conflicted = lines.merge()

    assert conflicted["state"] == "sync-resolution-required", conflicted
    crossing = conflicted["resolution"]["crossing"]
    assert (RECORD, "statement") in {(one["path"], one["item"]) for one in crossing["conflicts"]}
    leaf_id = lines.fixture.contract.task_id
    assert crossing["recordConflictHistoryOwner"] == leaf_id
    sides = [json.loads(git(lines.leaf, "show", f":{stage}:{RECORD}")) for stage in "23"]
    assert [one["statement"] for one in sides] == ["Leaf statement.", "Line statement."]

    written = write_knowledge(
        WriteRequest(
            memory_root=lines.leaf,
            code_root=lines.fixture.contract.code_worktree,
            owner=Owner(task=leaf_id, kind="leaf", id=leaf_id),
            handoff_path="sync-rows.json",
            document={
                "records": [
                    {
                        "key": "R-1",
                        "kind": "invariant",
                        "id": record["id"],
                        "fields": {"statement": "Both statements."},
                    }
                ],
                "history": [
                    {
                        "subject": record["id"],
                        "disposition": "changed",
                        "effect": "clarify",
                        "reason": "Both lines reworded it; the resolution keeps both points.",
                        "covers": [],
                    }
                ],
            },
            commit=True,
            coordination_root=tmp_path / "coordination",
        )
    )
    assert written.state == "written", written.render()
    resolved = lines.read(RECORD)
    assert resolved["statement"] == "Both statements."
    assert resolved["revision"] == record["revision"] + 3

    git(lines.leaf, "add", "-A", "--", "knowledge", "onboarding")
    finished = lines.sync(resolution_action="continue")
    assert finished["state"] == "synced", finished
    history = lines.read(f"knowledge/history/{leaf_id}.json")
    assert [(row["subject"], row["revision"]) for row in history["rows"]] == [
        (record["id"], record["revision"] + 3)
    ]


def test_an_entry_deleted_on_one_line_and_re_anchored_on_the_other_is_deleted(
    tmp_path: Path,
) -> None:
    """Deleted against a mechanical change: deleted, with no conflict.

    Catches a re-anchoring that brings back an entry the other line removed.
    """

    lines = _converted_lines(tmp_path)
    base = _sidecar(lines.leaf)
    extra = json.loads(json.dumps(base))
    number = str(max(int(key) for key in base["references"]) + 1)
    extra["references"][number] = {**base["references"]["1"], "note": "A second entry."}
    held = canonical_text(extra).encode("utf-8")
    lines.on_line({APP_SIDECAR: held}, "line adds an entry")
    assert lines.merge()["state"] == "synced"  # the leaf fast-forwards: both hold the entry

    lines.on_leaf({APP_SIDECAR: _without(extra, number)})
    lines.on_line({APP_SIDECAR: _edited(extra, number, blob="d" * 40)})

    merged = lines.merge()

    assert merged["state"] == "synced", merged
    assert number not in _sidecar(lines.leaf)["references"]


def test_a_master_lines_record_conflict_opens_its_crossing_history_file(tmp_path: Path) -> None:
    """A master line has no leaf: the plan names ``<task-id>-crossing-<n>`` and opens the file.

    The transaction passes the owner it derived from the contract kind; this drives the plan and
    its application for a master owner over three converted trees, which need no paired code.
    """

    lines = _converted_lines(tmp_path)
    record = lines.read(RECORD)

    def changed(statement: str) -> dict[str, bytes | None]:
        document = {**record, "statement": statement, "revision": record["revision"] + 1}
        return {RECORD: canonical_text(document).encode("utf-8")}

    own = lines.on_leaf(changed("Own statement."))
    incoming = lines.on_line(changed("Incoming statement."))
    git(lines.leaf, "fetch", "-q", str(lines.line), "main")
    base = merge_base(lines.leaf, own, incoming)
    assert merge_structure(lines.leaf, base, own, incoming) == "converted"

    plan = crossing_plan(
        lines.leaf, (base, own, incoming), paired_code=None, owner=("master", "260101-FIX")
    )

    crossing = "knowledge/history/260101-FIX-crossing-1.json"
    assert plan.report["recordConflictHistoryOwner"] == "260101-FIX-crossing-1"
    assert plan.report["converted"] == [] and plan.report["markerRows"] == 0
    assert json.loads(plan.files[crossing] or b"{}")["closed"] is False
    assert apply_crossing(lines.leaf, plan) == (RECORD,)
    assert (lines.leaf / crossing).is_file()


def test_unconverted_lines_merge_as_plain_git_and_converted_ones_never_do(
    tmp_path: Path,
) -> None:
    """The structural merge is asked for exactly the merges with a converted side.

    With no knowledge merge bound in the process, a merge of unconverted lines completes (it is not
    asked), and a merge of converted lines is refused before Git touches the worktree instead of
    falling back to Git's line merge.
    """

    bound = bound_worktree_services()
    fixture, _ = _unconverted_line(tmp_path / "unconverted")
    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    assert fixture.sync(memory_sync_choice="merge-memory").payload["state"] == "synced"
    _commit(worktree, {"onboarding/leaf.md": b"# leaf\n"}, "leaf card")
    plain = Lines(fixture=fixture, leaf=worktree, line=fixture.memory_repo)
    plain.on_line({"onboarding/line.md": b"# line\n"})
    lines = _converted_lines(tmp_path / "converted")
    base = _sidecar(lines.leaf)
    leaf_head = lines.on_leaf({APP_SIDECAR: _edited(base, "1", blob="b" * 40)})
    lines.on_line({APP_SIDECAR: _edited(base, "1", blob="c" * 40)})

    try:
        bind_worktree_services(replace(bound, knowledge_crossing=None))
        merged = plain.merge()
        refused = lines.merge()
    finally:
        bind_worktree_services(bound)

    assert merged["state"] == "synced", merged
    assert (worktree / "onboarding/line.md").is_file()
    assert not (worktree / "knowledge/layout.json").exists()
    assert refused["state"] == "sync-git-proof-failed", refused
    assert "converted memory is never merged as plain Git" in str(refused["summary"])
    assert git(lines.leaf, "rev-parse", "HEAD") == leaf_head
    with pytest.raises(AssertionError):
        git(lines.leaf, "rev-parse", "-q", "--verify", "MERGE_HEAD")


def test_a_journal_written_before_the_database_merge_was_retired_still_loads(
    tmp_path: Path,
) -> None:
    """A sync in flight across the upgrade resumes from its own journal.

    The earlier build wrote ``knowledgeConflict`` and ``knowledgeReconciliations`` on each side;
    the side record forbids unknown keys, so without the read rule the journal would be
    quarantined and the retained merge lost to the agent.
    """

    lines = _converted_lines(tmp_path)
    base = _sidecar(lines.leaf)
    lines.on_leaf({APP_SIDECAR: _edited(base, "1", note="Leaf wording.")})
    lines.on_line({APP_SIDECAR: _edited(base, "1", note="Line wording.")})
    assert lines.merge()["state"] == "sync-resolution-required"
    path = sync_operation_path(lines.fixture.contract.worktree_group)
    journal = json.loads(path.read_text(encoding="utf-8"))
    for side in ("code", "memory"):
        journal[side] = {**journal[side], "knowledgeConflict": None, "knowledgeReconciliations": []}
    path.write_text(json.dumps(journal, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    loaded = SyncOperationStore(lines.fixture.contract.worktree_group).read()

    assert isinstance(loaded, SyncOperationRecord) and loaded.memory is not None
    assert loaded.memory.state == "resolution-required"
    assert "knowledgeConflict" not in loaded.memory.model_dump()
    with pytest.raises(ValueError, match="unknownKey"):
        SyncSideRecord.model_validate({**journal["memory"], "unknownKey": 1})
    cancelled = lines.sync(resolution_action="cancel")
    assert cancelled["state"] == "sync-cancelled", cancelled


def test_the_retired_reconcile_action_is_refused_as_an_input_error(tmp_path: Path) -> None:
    """``reconcile`` settled a database conflict; knowledge is text, so the action no longer exists.

    Catches a call that still names it reaching the transaction as if it were ``continue``.
    """

    fixture, _ = _unconverted_line(tmp_path)

    refused = fixture.sync(resolution_action="reconcile").payload

    assert refused["state"] == "sync-input-invalid", refused
    assert refused["invalidField"] == "resolution_action"
    assert "continue" in str(refused["summary"]) and "cancel" in str(refused["summary"])
