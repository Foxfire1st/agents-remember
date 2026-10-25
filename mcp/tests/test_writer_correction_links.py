"""MIK-R54 writer corrections, exercised against real paired Git trees."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents_remember.cli.__main__ import main as cli_main
from knowledge_validator_test_support import (
    CODE_PATHS,
    INTEGRATE_CARD,
    INTEGRATE_SIDECAR,
    edit_json,
    fixture_code_contents,
    fixture_tree_files,
)
from knowledge_writer_test_support import build_world, read_json
from test_knowledge_requirement_reference_contract import _write_packet
from test_knowledge_validator_routes import _repository
from test_knowledge_writer import _conforming
from writer_corrections_test_support import linked_decision, missing_requirement, run


def test_reconsider_link_follows_exact_option_when_alternatives_swap(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    decision_id, stored = linked_decision(world)
    report = run(
        world,
        {
            "records": [
                {
                    "key": "update",
                    "id": decision_id,
                    "kind": "decision",
                    "fields": {"alternatives": list(reversed(stored["alternatives"]))},
                }
            ]
        },
    )
    assert report.state == "written", report.render()
    updated = read_json(world.memory, report.records[0].path)
    assert updated["links"][1]["alternative"] == 0


@pytest.mark.parametrize("duplicate", [False, True])
def test_omitted_links_refuse_missing_or_duplicate_old_option(
    tmp_path: Path, duplicate: bool
) -> None:
    world = build_world(tmp_path)
    decision_id, stored = linked_decision(world)
    alternatives = stored["alternatives"]
    replacement = (
        [*alternatives, alternatives[1]]
        if duplicate
        else [alternatives[0], {**alternatives[1], "option": "Reworded"}]
    )
    report = run(
        world,
        {
            "records": [
                {
                    "key": "update",
                    "id": decision_id,
                    "kind": "decision",
                    "fields": {"alternatives": replacement},
                }
            ]
        },
    )
    assert report.refused
    assert (
        "link 1 addressed alternative 'Land apart', which this update no longer holds; restate `links`"
        in report.render()
    )


def test_new_unresolved_requirement_link_refuses_with_coordination_root(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    document = _conforming()
    document["records"][0]["fields"]["links"].append(
        {"relation": "constrains", "target": missing_requirement()}
    )
    report = run(world, document, coordination_root=tmp_path / "coordination")
    assert report.refused
    assert "requirement" in report.render() and "does not resolve" in report.render()


def test_unchanged_unresolved_requirement_is_only_reported(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    document = _conforming()
    document["records"][0]["fields"]["links"].append(
        {"relation": "constrains", "target": missing_requirement()}
    )
    first = run(world, document)
    assert first.state == "written", first.render()
    report = run(world, document, coordination_root=tmp_path / "coordination")
    assert report.state == "written", report.render()
    assert report.requirements[0].state == "unresolved"


def test_deferred_requirement_refuses_when_owner_cannot_resolve(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    item = {
        "id": "sha256:" + "c" * 64,
        "kind": "planned_knowledge_effect",
        "subject": "planned:new:A1#introduce",
        "facts": {},
    }
    report = run(
        world,
        {
            "history": [
                {
                    "subject": "planned:new:A1#introduce",
                    "disposition": "deferred",
                    "reason": "Later task owns this.",
                    "ref": {"requirement": missing_requirement()},
                }
            ]
        },
        worklist={"items": [item]},
        coordination_root=tmp_path / "coordination",
    )
    assert report.refused
    assert "deferred row's requirement does not resolve" in report.render()


@pytest.mark.parametrize("refusing", [False, True])
def test_optional_cli_endpoints_report_bad_utf8_active_and_archive_without_changing_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], refusing: bool
) -> None:
    record = "knowledge/decisions/DEC-D12RTE-local-family-routes.json"
    files = fixture_tree_files()
    valid = json.loads(files[record])["links"][3]["target"]
    invalid = [
        {**valid, "task": {**valid["task"], "path": name}}
        for name in ("260928_bad-active", "260928_bad-archive")
    ]
    files = edit_json(
        files,
        INTEGRATE_SIDECAR,
        lambda doc: doc["references"].update(
            {
                str(number): {"targets": [{"kind": "requirement", "requirement": ref}]}
                for number, ref in enumerate(invalid, 7)
            }
        ),
    )
    files[INTEGRATE_CARD] += b"\nPackets [7] [8].\n"
    memory, code = tmp_path / "memory", tmp_path / "code"
    base = _repository(memory, files)
    # The card anchors hold against the fixture's pinned code blob (MIK-R198 rule 5).
    _repository(code, {**dict.fromkeys(CODE_PATHS, b"# code\n"), **fixture_code_contents()})
    if refusing:
        (memory / INTEGRATE_CARD).write_bytes(files[INTEGRATE_CARD] + b"\nMissing [999].\n")
    coordination = tmp_path / "coordination"
    tasks = coordination / "tasks" / valid["task"]["repository"]
    _write_packet(
        tasks / valid["task"]["path"],
        valid["packet"],
        stable_id=valid["id"],
        version=valid["version"],
    )
    for ref, root in zip(invalid, (tasks, tasks / "0_archive"), strict=True):
        packet = _write_packet(
            root / ref["task"]["path"], ref["packet"], stable_id=ref["id"], version=ref["version"]
        )
        packet.write_bytes(packet.read_bytes() + b"\xff")
    args = ["knowledge-validate", str(memory), "--code", str(code), "--base", base]
    baseline_status = cli_main([*args, "--json"])
    baseline = json.loads(capsys.readouterr().out)
    assert baseline_status == int(refusing)
    diagnostic_args = [*args, "--coordination-root", str(coordination)]
    assert cli_main([*diagnostic_args, "--json"]) == baseline_status
    document = json.loads(capsys.readouterr().out)
    endpoints = document.pop("requirementEndpoints")
    assert document == baseline
    assert len(endpoints) == 3
    assert {(one["state"], one["archived"]) for one in endpoints} == {
        ("resolved", False),
        ("unresolved", False),
        ("unresolved", True),
    }
    for one in endpoints:
        if one["state"] == "unresolved":
            assert one["code"] == "task-intent-requirement-packet-encoding-invalid"
            assert "UTF-8" in one["detail"] and one["reference"]["packet"] in one["detail"]
    assert cli_main(diagnostic_args) == baseline_status
    text = capsys.readouterr().out
    assert "requirement endpoints (3;" in text
    for one in endpoints:
        assert f"{one['path']}: {one['field']}: {one['key']}: {one['state']}" in text
