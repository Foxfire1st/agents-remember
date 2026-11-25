"""The writer stamps each revision it raises with its caller, never from its input.

Each write route -- a leaf (its contract), a wave and a crossing -- resolves the caller once from the
tool server's launch binding and the seat's launch receipt under the coordination root. The stamp
stands in the record and in the owner's history file (``written``); a value typed into the hand-off
never reaches either. A second write of a revision renews its stamp, a write that raises no number
keeps the base's, and a caller that cannot be resolved reads ``unknown`` with the reason.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_write_route import (
    WaveTarget,
    WriteSubmission,
    crossing_write_report,
    leaf_write_report,
    wave_write_report,
    write_stamp,
)
from agents_remember.application.knowledge_writer import Owner, WriteRequest
from agents_remember.application.role_launch_context import selection_binding
from agents_remember.cli.role_launch_receipts import _taskless_session_directory
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.history_product import record_content_digest
from agents_remember.models.knowledge_files.writer_stamp import WriterStamp
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.worktrees.worktree_contract import load_contract
from knowledge_writer_test_support import (
    ADMISSION,
    BASE_INVARIANT,
    LEAF_ID,
    SCOPE,
    TASK_ID,
    World,
    build_crossing_world,
    build_world,
    commit_all,
    entry,
    read_json,
    target,
    tree_bytes,
)
from test_reconsideration_repoint import GONE, REPOINT
from test_reconsideration_surfacing import D18_PATH, EDITED_THIRD, RERUN_ROW, REVIEW, S18B
from test_reconsideration_surfacing import _write as _surfaced_write
from writer_worklist_test_support import write_knowledge

pytest_plugins = ("test_reconsideration_surfacing",)

SEAT = "22222222-2222-4222-8222-222222222222"
AGENT = "33333333-3333-4333-8333-333333333333"
TASK_REF = {"repository": "agents-remember", "path": "260928_writer/01_leaf.json"}
MASTER_REF = {"repository": "agents-remember", "path": "260928_writer/task.json"}
FORGED = {"by": "agent", "role": "architect", "model": "forged", "at": "2000-01-01T00:00:00Z"}
CALLER = {"by": "agent", "role": "curator", "harness": "codex", "model": "gpt-x", "effort": "high"}


def _written_row(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "record": record["id"],
        "revision": record["revision"],
        "writer": record["writer"],
        "contentDigest": record_content_digest(record),
    }


def _bind(monkeypatch: pytest.MonkeyPatch, world: World, *, agent: dict[str, str]) -> None:
    """A curator seat's launch binding in the environment and its receipt in the task folder."""

    for name, value in {
        "AR_PASEO_AGENT_ID": AGENT,
        "AR_ROLE": "curator",
        "AR_REQUEST_ID": SEAT,
        "AR_REPORT_PATH": str(world.root / "report.md"),
        "AR_SPRINT_REF": "",
        "AR_MASTER_REF": json.dumps(MASTER_REF),
        "AR_TASK_REF": json.dumps(TASK_REF),
    }.items():
        monkeypatch.setenv(name, value)
    holder = (
        world.root / "tasks/agents-remember/260928_writer/notes/reports/paseo-native-executions"
    )
    holder.mkdir(parents=True)
    receipt = {
        "schema": "ar-role-execution/v1",
        "requestId": SEAT,
        "role": "curator",
        "selection": {"taskDocumentRef": TASK_REF},
        "agent": agent,
    }
    (holder / "worker-x.json").write_text(json.dumps(receipt), encoding="utf-8")


def _new_invariant(world: World) -> tuple[str, dict[str, Any]]:
    (path,) = [
        one
        for one in sorted((world.memory / "knowledge/invariants").glob("*.json"))
        if BASE_INVARIANT not in one.name
    ]
    return path.relative_to(world.memory).as_posix(), json.loads(path.read_text())


def _authored(**curator: Any) -> dict[str, Any]:
    return entry("B-1", target=[target("land_pair")], scope=SCOPE, admission=ADMISSION, **curator)


def _caller(owner: dict[str, str]) -> dict[str, str]:
    return {**CALLER, "seat": SEAT, **owner}


def _without_time(stamp: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in stamp.items() if key != "at"}


def test_a_leaf_write_stamps_its_new_record_from_binding_and_receipt_not_from_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path)
    _bind(monkeypatch, world, agent={"id": "codex", "model": "gpt-x", "effort": "high"})
    (world.task_root / "knowledge-worklist.json").write_text('{"items": []}', encoding="utf-8")
    report = leaf_write_report(
        load_contract(world.contract),
        WriteSubmission(
            document={"entries": [_authored(writer=FORGED)]},
            handoff_path="handoff.json",
            authorization_ref="authorized-by-test",
            commit=True,
        ),
    )
    assert not isinstance(report, str) and report.state == "written", report
    path, invariant = _new_invariant(world)
    assert "status" not in invariant  # no default state is typed into a new record
    stamp = invariant["writer"]
    assert _without_time(stamp) == _caller({"task": TASK_ID, "leaf": LEAF_ID})
    history = read_json(world.memory, f"knowledge/history/{LEAF_ID}.json")
    assert history["written"] == [_written_row(invariant)]
    assert "forged" not in (world.memory / path).read_text()


def test_a_wave_write_names_its_revisions_and_refuses_a_typed_written_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path)
    _bind(monkeypatch, world, agent={"id": "codex", "model": "gpt-x", "effort": "high"})
    wave = WaveTarget(world.memory, world.code, "bootstrap-1", "bootstrap:x", world.root)
    typed = {"record": BASE_INVARIANT, "revision": 1, "writer": FORGED}
    before = tree_bytes(world.memory)
    forged = {
        "entries": [_authored()],
        "written": [typed],
        "history": [{"subject": BASE_INVARIANT, "disposition": "no_impact", "reason": "-"} | typed],
    }
    refused = wave_write_report(wave, WriteSubmission(forged, "handoff.json", "by-test", True))
    assert not isinstance(refused, str) and refused.refused
    assert "unknown section(s) ['written']" in refused.render()
    assert "unknown row key(s) ['record', 'writer']" in refused.render()
    assert tree_bytes(world.memory) == before
    written = wave_write_report(
        wave, WriteSubmission({"entries": [_authored()]}, "handoff.json", "by-test", True)
    )
    assert not isinstance(written, str) and written.state == "written", written
    _path, invariant = _new_invariant(world)
    stamp = invariant["writer"]
    assert _without_time(stamp) == _caller({"task": "bootstrap:x", "wave": "bootstrap-1"})
    history = read_json(world.memory, "knowledge/history/bootstrap-1.json")
    assert history["written"] == [_written_row(invariant)]


def test_a_crossing_write_stamps_the_record_it_resolves_and_refuses_a_typed_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path)
    _bind(monkeypatch, world, agent={"id": "codex", "model": "gpt-x", "effort": "high"})
    parked = build_crossing_world(world)
    parked.store.write(parked.record)
    crossing = parked.owner
    resolved = {"key": "R", "kind": "invariant", "id": BASE_INVARIANT, "fields": {}}
    resolved["fields"] = {"statement": "Code and memory land as one recorded pair."}

    def run(fields: dict[str, Any]) -> Any:
        document = {"records": [{**resolved, "fields": {**resolved["fields"], **fields}}]}
        return crossing_write_report(
            parked.contract, crossing, WriteSubmission(document, "rows.json", "by-test", True)
        )

    refused = run({"writer": FORGED})
    assert not isinstance(refused, str) and refused.refused
    assert "the writer owns ['writer']" in refused.render()
    written = run({})
    assert not isinstance(written, str) and written.state == "written", written
    invariant = read_json(parked.memory, f"knowledge/invariants/{BASE_INVARIANT}-landing-pair.json")
    stamp = invariant["writer"]
    assert invariant["revision"] == 2 and "status" not in invariant  # the old word left with it
    assert _without_time(stamp) == _caller({"task": TASK_ID, "crossing": crossing})
    history = read_json(parked.memory, f"knowledge/history/{crossing}.json")
    assert history["written"] == [_written_row(invariant)]


def _stamped(seat: str, at: str) -> WriterStamp:
    return WriterStamp.model_validate(
        {**CALLER, "seat": seat, "task": TASK_ID, "leaf": LEAF_ID, "at": at}
    )


def test_a_revision_keeps_one_stamp_per_number_renewed_by_its_last_writer(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    owner = Owner(task=TASK_ID, kind="leaf", id=LEAF_ID)

    def run(stamp: WriterStamp, statement: str | None = None) -> None:
        authored = _authored() if statement is None else _authored(statement=statement)
        report = write_knowledge(
            WriteRequest(
                memory_root=world.memory,
                code_root=world.code,
                owner=owner,
                handoff_path="handoff.json",
                document={"entries": [authored]},
                commit=True,
                stamp=stamp,
            )
        )
        assert report.state == "written", report.render()

    def written() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        history = read_json(world.memory, f"knowledge/history/{LEAF_ID}.json")
        return _new_invariant(world)[1], history["written"]

    first = _stamped(SEAT, "2026-11-19T10:00:00+01:00").to_document()
    run(_stamped(SEAT, "2026-11-19T10:00:00+01:00"))
    before = tree_bytes(world.memory)
    run(_stamped(SEAT, "2026-11-19T10:05:00+01:00"))  # the same writer, nothing changed
    assert tree_bytes(world.memory) == before and written()[0]["writer"] == first
    other = _stamped(AGENT, "2026-11-19T10:10:00+01:00").to_document()
    run(_stamped(AGENT, "2026-11-19T10:10:00+01:00"))  # a second write of revision 1
    invariant, rows = written()
    assert (invariant["revision"], invariant["writer"]) == (1, other)
    prior_row = _written_row(invariant)
    assert rows == [prior_row]

    commit_all(world.memory, "the leaf's revision 1 in the base")
    run(_stamped(SEAT, "2026-11-19T11:00:00+01:00"))  # raises no number against the base
    assert written()[0]["writer"] == other and written()[1] == rows
    third = _stamped(SEAT, "2026-11-19T12:00:00+01:00").to_document()
    run(_stamped(SEAT, "2026-11-19T12:00:00+01:00"), "Statement of B-1: both halves land.")
    invariant, rows = written()
    assert (invariant["revision"], invariant["writer"]) == (2, third)
    assert rows == [prior_row, _written_row(invariant)]
    assert rows[0]["contentDigest"] != rows[1]["contentDigest"]


def test_an_unresolved_caller_or_receipt_value_reads_unknown_with_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path)
    owner = Owner(task=TASK_ID, kind="leaf", id=LEAF_ID)
    monkeypatch.delenv("AR_PASEO_AGENT_ID", raising=False)
    unbound = write_stamp(owner, world.root)
    assert (unbound.role, unbound.model) == ("unknown", "unknown")
    assert unbound.unknown == {
        "role": "this tool server was not launched for an agent",
        "seat": "this tool server was not launched for an agent",
        "harness": "no launch receipt of the caller was found",
        "model": "no launch receipt of the caller was found",
        "effort": "no launch receipt of the caller was found",
    }
    _bind(monkeypatch, world, agent={"id": "codex", "model": "gpt-x"})
    partial = write_stamp(owner, world.root)
    assert (partial.role, partial.model, partial.effort) == ("curator", "gpt-x", "unknown")
    assert partial.unknown == {"effort": "the launch receipt does not hold the effort"}
    monkeypatch.setenv("AR_ROLE", "no-such-role")
    unreadable = write_stamp(owner, world.root)
    assert unreadable.unknown is not None
    assert unreadable.unknown["role"].startswith("the launch binding is unreadable")


def _bootstrap_receipt(
    monkeypatch: pytest.MonkeyPatch, world: World, agent: dict[str, str]
) -> None:
    """A Bootstrap seat's binding, which names no task, and its receipt where its launch keeps it."""

    for name, value in {
        "AR_PASEO_AGENT_ID": AGENT,
        "AR_ROLE": "bootstrap",
        "AR_REQUEST_ID": SEAT,
        "AR_REPORT_PATH": str(world.root / "report.md"),
        "AR_SPRINT_REF": "",
        "AR_MASTER_REF": "",
        "AR_TASK_REF": "",
    }.items():
        monkeypatch.setenv(name, value)
    config = McpRuntimeConfig(
        config_path=world.root / "config.toml",
        coordination_root=world.root,
        workspace_root=world.root,
        transcript_root=world.root,
    )
    sessions = _taskless_session_directory(config, "bootstrap")
    sessions.mkdir(parents=True)
    receipt = {
        "schema": "ar-role-execution/v1",
        "requestId": SEAT,
        "role": "bootstrap",
        "selection": selection_binding(RoleSelection(role="bootstrap")),
        "agent": agent,
    }
    (sessions / f"{SEAT}.json").write_text(json.dumps(receipt), encoding="utf-8")


@pytest.mark.parametrize(
    ("agent", "unknown"),
    [
        ({"id": "claude", "model": "opus", "effort": "max"}, None),
        (
            {"id": "claude", "model": "opus"},
            {"effort": "the launch receipt does not hold the effort"},
        ),
    ],
)
def test_a_bootstrap_wave_write_is_stamped_from_its_taskless_launch_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent: dict[str, str],
    unknown: dict[str, str] | None,
) -> None:
    world = build_world(tmp_path)
    _bootstrap_receipt(monkeypatch, world, agent)
    wave = WaveTarget(world.memory, world.code, "bootstrap-1", "bootstrap:x", world.root)
    written = wave_write_report(
        wave, WriteSubmission({"entries": [_authored()]}, "handoff.json", "by-test", True)
    )
    assert not isinstance(written, str) and written.state == "written", written
    stamp = _new_invariant(world)[1]["writer"]
    expected = {
        "by": "agent",
        "role": "bootstrap",
        "seat": SEAT,
        "harness": "claude",
        "model": "opus",
        "effort": agent.get("effort", "unknown"),
        "task": "bootstrap:x",
        "wave": "bootstrap-1",
    }
    assert _without_time(stamp) == expected | ({"unknown": unknown} if unknown else {})
    history = read_json(world.memory, "knowledge/history/bootstrap-1.json")
    assert history["written"][0]["writer"] == stamp


def _judged(world: Any, report: Any, revision: int) -> dict[str, Any]:
    """The persisted history of a judgment: its row, and the decision's stamped revision."""

    assert report.state == "written", report.render()
    (row,) = report.rows
    history = json.loads((world.memory / row.history).read_text(encoding="utf-8"))
    decision = json.loads((world.memory / D18_PATH).read_text(encoding="utf-8"))
    assert decision["revision"] == revision
    assert [one["id"] for one in history["rows"]] == [row.id]
    assert history["written"] == [_written_row(decision)]
    return history


def test_a_judged_refresh_keeps_its_stamped_revision_beside_its_row(world: Any) -> None:
    edited = world.code_commit({REVIEW: EDITED_THIRD})
    report = _surfaced_write(world, [RERUN_ROW], None, worklist=world.worklist(edited))
    _judged(world, report, 2)


def test_a_repoint_keeps_its_stamped_revision_beside_its_row(world: Any) -> None:
    removed = world.code_commit({REVIEW: GONE})
    row = {"subject": S18B, "disposition": "still_rejected", "reason": "-", "repoint": [REPOINT]}
    report = _surfaced_write(world, [row], None, worklist=world.worklist(removed))
    history = _judged(world, report, 2)
    assert history["rows"][0]["repoint"][0]["after"]["locator"]["name"] == "fourth"


def test_a_reopened_amendment_keeps_the_earlier_attempts_stamp(world: Any) -> None:
    edited = world.code_commit({REVIEW: EDITED_THIRD})
    first = _surfaced_write(world, [RERUN_ROW], None, worklist=world.worklist(edited))
    earlier = _judged(world, first, 2)
    closed_path = first.rows[0].history
    closed = world.memory_commit({closed_path: canonical_text({**earlier, "closed": True})}, edited)
    frozen = (world.memory / closed_path).read_bytes()
    again = world.code_commit({REVIEW: EDITED_THIRD.replace("value * 5", "value * 7")})
    worklist = world.worklist(again, base=edited, memory_base=closed)
    second = _surfaced_write(world, [RERUN_ROW], None, worklist=worklist)
    _judged(world, second, 3)
    assert second.rows[0].history != closed_path
    assert (world.memory / closed_path).read_bytes() == frozen
    assert json.loads(frozen)["written"] == earlier["written"]
