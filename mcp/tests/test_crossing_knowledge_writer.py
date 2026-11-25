"""A crossing write takes its code evidence from the open sync's settled commit."""

from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application import knowledge_write_route
from agents_remember.application.knowledge_write_route import WriteSubmission, crossing_write_report
from agents_remember.application.knowledge_writer import Owner, WriteReport, WriteRequest
from agents_remember.application.knowledge_writer.code_anchors import (
    AnchorResolutionError,
    CodeSnapshot,
)
from knowledge_writer_test_support import (
    CODE_FILE,
    LEAF_ID,
    TASK_ID,
    TEST_FILE,
    build_crossing_world,
    build_world,
    commit_all,
    git,
    read_json,
    tree_bytes,
    write,
)
from writer_worklist_test_support import write_knowledge


def test_crossing_confirmation_uses_the_same_settled_commit_for_snapshot_and_base(
    tmp_path: Path,
) -> None:
    world = build_world(tmp_path)
    record = {
        "key": "dependent",
        "kind": "dependent",
        "slug": "dependent",
        "fields": {
            "document": TEST_FILE,
            "stands_on": [{"kind": "file", "path": CODE_FILE}],
            "confirmed": {
                "task": LEAF_ID,
                "role": "curator",
                "statement": "The test exercises the code it stands on.",
            },
        },
    }
    created = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=Owner(task=TASK_ID, kind="leaf", id=LEAF_ID),
            handoff_path="seed.json",
            document={"records": [record]},
            commit=True,
            worklist={"items": []},
        )
    )
    assert created.state == "written", created.render()
    (seed,) = created.records
    commit_all(world.memory, "Track the dependent before the crossing")
    previous_head = git(world.code, "rev-parse", "HEAD")
    write(world.code, {TEST_FILE: "from pkg.landing import land_pair\n"})
    expected_head = commit_all(world.code, "Fresh code for the dependent judgment")
    assert expected_head != previous_head
    parked = build_crossing_world(world)
    parked.store.write(parked.record)
    record_id = seed.id
    update = {
        "key": "dependent",
        "kind": "dependent",
        "id": record_id,
        "fields": {
            "confirmed": {
                "task": parked.contract.task_id,
                "role": "curator",
                "statement": "The merged test still imports the landing function.",
            },
        },
    }
    row = {
        "subject": record_id,
        "disposition": "changed",
        "reason": "Reconfirmed on the code settled by this sync.",
        "effect": "clarify",
    }
    submission = WriteSubmission(
        document={"records": [update], "history": [row]},
        handoff_path="crossing.json",
        authorization_ref="admitted crossing",
        commit=True,
    )
    wraps = mock.patch.object(knowledge_write_route, "write_knowledge", wraps=write_knowledge)
    with wraps as wrote:
        report = crossing_write_report(parked.contract, parked.owner, submission)
    assert isinstance(report, WriteReport)
    assert report.state == "written", report.render()
    observed = read_json(parked.memory, seed.path)["confirmed"]
    assert observed["commit"] == parked.record.code.resultHead == expected_head
    assert wrote.call_args.args[0].code_base == observed["commit"]
    assert observed["codeTree"] == git(world.code, "rev-parse", f"{expected_head}^{{tree}}")
    assert observed["uncommitted"] is False


def test_crossing_write_refuses_unsettled_or_different_sync_authority(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    parked = build_crossing_world(world)
    before = tree_bytes(parked.memory)
    submission = WriteSubmission(
        document={},
        handoff_path="crossing.json",
        authorization_ref="admitted crossing",
        commit=True,
    )

    def refuse(crossing: str, reason: str) -> None:
        report = crossing_write_report(parked.contract, crossing, submission)
        assert isinstance(report, str) and reason in report, report
        assert tree_bytes(parked.memory) == before

    refuse(parked.owner, "No memory sync is open")
    parked.store.write(parked.record)
    refuse(f"{parked.contract.task_id}-crossing-2", f"owns {parked.owner}")
    pending = parked.record.code.model_copy(update={"state": "pending", "resultHead": ""})
    parked.store.write(parked.record.model_copy(update={"code": pending}))
    refuse(parked.owner, "code side has not completed")
    parked.store.write(parked.record)
    write(world.code, {"new.py": "value = 1\n"})
    moved = commit_all(world.code, "Move off the settled code result")
    refuse(parked.owner, f"moved to {moved}")


def test_code_snapshot_resolves_a_commit_ref_and_refuses_noncommits(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    commit = git(world.code, "rev-parse", "HEAD")
    tree = git(world.code, "rev-parse", "HEAD^{tree}")
    blob = git(world.code, "rev-parse", f"HEAD:{CODE_FILE}")
    snapshot = CodeSnapshot.at_commit(world.code, "refs/heads/main")
    assert (snapshot.commit, snapshot.tree) == (commit, tree)
    for noncommit in (tree, blob):
        with pytest.raises(AnchorResolutionError, match="does not resolve to a Git commit"):
            CodeSnapshot.at_commit(world.code, noncommit)


def test_code_snapshot_requires_a_full_commit_id_without_looking_it_up(tmp_path: Path) -> None:
    for commit in ("refs/heads/main", "a" * 39, "a" * 41, "a" * 63, "g" * 64):
        with pytest.raises(AnchorResolutionError, match="full Git commit ID"):
            CodeSnapshot(root=tmp_path, tree="tree", blobs={}, commit=commit)
    for commit in ("a" * 40, "b" * 64):
        snapshot = CodeSnapshot(root=tmp_path, tree="tree", blobs={}, commit=commit)
        assert snapshot.commit == commit
