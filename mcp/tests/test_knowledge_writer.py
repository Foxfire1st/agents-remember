"""MIK-R12: the curator writer creates and updates every knowledge kind as validated files.

Every test runs against real Git repositories (:mod:`knowledge_writer_test_support`): anchors are
resolved at the code candidate tree C, and the memory worktree's ``HEAD`` is the base a revision and a
history row's ``before`` anchor are read from.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agents_remember.application.knowledge_writer import (
    Owner,
    WriteRequest,
    write_knowledge,
)
from agents_remember.cli import knowledge_bootstrap
from agents_remember.cli.__main__ import main
from agents_remember.cli.knowledge_write_route import converted_contract, run_wave_write
from agents_remember.memory_quality.knowledge_validator import (
    CodeDirectory,
    KnowledgeValidationError,
    knowledge_tree_from_directory,
    knowledge_tree_from_git,
    require_valid_commit,
    validate_tree,
)
from agents_remember.models.knowledge_files import Anchor, parse_document_text
from agents_remember.models.knowledge_files.history import InvariantRow
from knowledge_writer_test_support import (
    ADMISSION,
    BASE_FAMILY,
    BASE_INVARIANT,
    BASE_REALIZATION,
    CODE,
    CODE_FILE,
    LEAF_ID,
    SCOPE,
    TASK_ID,
    TEST_FILE,
    World,
    build_world,
    commit_all,
    entry,
    git,
    read_json,
    target,
    tree_bytes,
    write,
)

OWNER = Owner(task=TASK_ID, kind="leaf", id=LEAF_ID)
TEST_ID = f"{TEST_FILE}::LandingTests::test_pair_lands_together"


def _write(world: World, document: Any, *, commit: bool = True):
    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="notes/handoff.json",
            document=document,
            commit=commit,
        )
    )


def _decision(key: str = "D-1", **fields: Any) -> dict[str, Any]:
    return {
        "key": key,
        "kind": "decision",
        "entry": "A-1",
        "slug": "pair-landing",
        "fields": {
            "context": "A landing needs both halves.",
            "alternatives": [
                {"option": "Land together", "status": "chosen", "reason": "One pair."},
                {
                    "option": "Land apart",
                    "status": "rejected",
                    "reason": "Drift.",
                    "reconsider_when": "Never.",
                },
            ],
            "consequences": ["One pair per leaf."],
            "decider": "developer",
            "supersedes": [],
            "admission": {"criteria": ["real_alternatives"], "justification": "Two designs."},
            "links": [{"relation": "constrains", "target": "handoff:A-1"}],
            **fields,
        },
    }


def _conforming() -> dict[str, Any]:
    return {
        "entries": [
            entry(
                "A-1",
                kind="decision",
                target=[target("land_pair", role="primary-authority")],
                evidence=f"pytest {TEST_ID}",
                scope=SCOPE,
                admission=ADMISSION,
                proofs=[{"test": TEST_ID, "facet": "a pair lands as one value"}],
            )
        ],
        "records": [_decision()],
    }


def _record_path(world: World, directory: str) -> str:
    (found,) = sorted((world.memory / "knowledge" / directory).glob("*.json"))
    return found.relative_to(world.memory).as_posix()


def _new_invariant_path(world: World) -> str:
    found = [
        path
        for path in sorted((world.memory / "knowledge/invariants").glob("*.json"))
        if BASE_INVARIANT not in path.name
    ]
    return found[0].relative_to(world.memory).as_posix()


def test_a_decision_a_realization_and_a_tested_evidence_produce_validated_files(
    tmp_path: Path,
) -> None:
    """The packet's conforming example: a decision record, a realization, a proof; all valid."""

    world = build_world(tmp_path)
    report = _write(world, _conforming())
    assert report.state == "written", report.render()
    decision = read_json(world.memory, _record_path(world, "decisions"))
    invariant_id = decision["links"][0]["target"]
    assert decision["origin"]["handoff"]["evidence"] == [f"pytest {TEST_ID}"]
    sidecar = read_json(world.memory, f"onboarding/{CODE_FILE}.json")
    (realization,) = [one for one in sidecar["realizes"] if one["invariant"] == invariant_id]
    assert realization["anchor"]["blob"] == git(world.code, "rev-parse", f"HEAD:{CODE_FILE}")
    body = "".join(CODE.splitlines(keepends=True)[3:6]).encode()
    assert realization["anchor"]["content"] == f"sha256:{hashlib.sha256(body).hexdigest()}"
    assert realization["origin"] == {"leaf": LEAF_ID, "handoffEntry": "A-1"}
    (proof,) = read_json(world.memory, f"onboarding/{TEST_FILE}.json")["proves"]
    assert proof["invariant"] == invariant_id
    assert proof["anchor"]["locator"]["name"] == "LandingTests.test_pair_lands_together"
    assert proof["facet"] == "a pair lands as one value"
    (evidence,) = report.evidence
    assert [test.state for test in evidence.tests] == ["proof_written"]
    validation = validate_tree(
        knowledge_tree_from_directory(world.memory), code=CodeDirectory("code", world.code)
    )
    assert validation.ok, validation.render()


def _every_kind(world: World) -> dict[str, Any]:
    facets = {
        "incident": {
            "occurrence": "A leaf landed half a pair.",
            "observed_at": "260101-OLD-L1",
            "observed_effect": "Memory lagged code.",
            "detection": "The closeout gate.",
            "cause": "A manual push.",
            "cause_uncertainty": "Low.",
            "applicability": "unresolved",
            "links": [{"relation": "violated", "target": BASE_INVARIANT}],
        },
        "assumption": {"proposition": "Git is available.", "basis": "Every worktree is Git."},
        "limitation": {"limited": "Landing", "boundary": "Local only.", "unsupported": ["Remote"]},
        "failure_mode": {
            "failure": "Half landing.",
            "condition": "A crash.",
            "observable_effect": "Lag.",
        },
        "scenario": {
            "situation": "A leaf closes.",
            "preconditions": ["Clean tree."],
            "outcome": "One pair.",
        },
        "diagnostic": {
            "condition": "Lagging memory.",
            "signal": "The ledger row.",
            "interpretation": "A half landing.",
            "interpretation_limit": "Not for crossings.",
        },
        "term": {"term": "pair", "definition": "Code and memory commits.", "scope": "Landing."},
    }
    records = [
        {
            "key": f"R-{kind}",
            "kind": kind,
            "slug": kind.replace("_", "-"),
            "fields": {"links": [], **fields},
        }
        for kind, fields in facets.items()
    ]
    records.append(_decision())
    records.append(
        {
            "key": "F-1",
            "kind": "family",
            "slug": "pairing",
            "fields": {
                "title": "Pairing",
                "guarantee": "Every landing is paired.",
                "members": ["handoff:A-1", BASE_INVARIANT],
                "routes": ["pkg"],
                "admission": {"criteria": ["joint_guarantee"], "justification": "Joint."},
            },
        }
    )
    write(world.code, {CODE_FILE: "# a moved definition\n\n" + CODE})
    return {
        "entries": _conforming()["entries"],
        "records": records,
        "history": [
            {
                "subject": BASE_INVARIANT,
                "disposition": "moved",
                "reason": "Two lines were added above it.",
                "covers": [BASE_REALIZATION],
            },
            {
                "subject": BASE_FAMILY,
                "disposition": "no_impact",
                "reason": "Its member moved; the guarantee holds.",
                "examined": [BASE_INVARIANT],
            },
        ],
    }


def _ingest(world: World, listed: Path, *extra: str) -> int:
    argv = ["knowledge-ingest", "--contract", str(world.contract), "--list", str(listed)]
    return main([*argv, "--authorization-ref", "curator", "--json", *extra])


def test_the_command_line_round_trips_every_kind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``knowledge-ingest`` on converted memory writes all ten record kinds, entries and rows."""

    world = build_world(tmp_path)
    listed = world.task_root / "notes" / "handoff.json"
    write(listed.parent, {"handoff.json": json.dumps(_every_kind(world))})
    assert _ingest(world, listed, "--commit") == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["state"] == "written", printed
    kinds = {record["kind"] for record in printed["records"]}
    assert kinds == {
        "invariant",
        "family",
        "decision",
        "incident",
        "assumption",
        "limitation",
        "failure_mode",
        "scenario",
        "diagnostic",
        "term",
    }
    for record in printed["records"]:
        document = parse_document_text((world.memory / record["path"]).read_text())
        assert document.to_document()["origin"]["handoff"]["path"] == "notes/handoff.json"
    history = read_json(world.memory, f"knowledge/history/{LEAF_ID}.json")
    rows = {row["subject"]: row for row in history["rows"]}
    moved = InvariantRow.model_validate(rows[BASE_INVARIANT])
    (cover,) = moved.covers
    sidecar = read_json(world.memory, f"onboarding/{CODE_FILE}.json")
    written = next(one for one in sidecar["realizes"] if one["id"] == BASE_REALIZATION)
    assert isinstance(cover.after, Anchor) and cover.after != cover.before
    assert cover.after.to_document() == {**written["anchor"], "path": CODE_FILE}
    assert written["anchor"]["blob"] == git(world.code, "hash-object", CODE_FILE)
    assert rows[BASE_FAMILY]["examined"] == [{"id": BASE_INVARIANT, "revision": 1}]
    assert _ingest(world, listed, "--commit") == 0
    capsys.readouterr()


def test_a_rerun_of_the_same_list_writes_the_same_files_with_the_same_ids(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    document = _every_kind(world)
    first = _write(world, document)
    before = tree_bytes(world.memory)
    second = _write(world, document)
    assert first.state == second.state == "written", second.render()
    assert tree_bytes(world.memory) == before
    assert second.written == ()
    assert {record.id for record in first.records} == {record.id for record in second.records}
    assert {one.action for one in (*second.records, *second.entries)} == {"unchanged"}


def test_evidence_that_names_no_resolvable_test_is_reported_and_kept(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    evidence = f"ran {TEST_FILE}::test_plain and tests/test_gone.py::test_missing"
    report = _write(
        world,
        [
            entry(
                "A-2",
                target=[target("land_pair")],
                evidence=evidence,
                scope=SCOPE,
                admission=ADMISSION,
            )
        ],
    )
    assert report.state == "written", report.render()
    (outcome,) = report.evidence
    states = {test.test: test.state for test in outcome.tests}
    assert states == {
        f"{TEST_FILE}::test_plain": "needs_facet",
        "tests/test_gone.py::test_missing": "unresolvable",
    }
    invariant = read_json(world.memory, _new_invariant_path(world))
    assert invariant["origin"]["handoff"]["evidence"] == [evidence]
    assert outcome.stored_in == (invariant["id"],)
    assert not (world.memory / f"onboarding/{TEST_FILE}.json").exists()


def test_a_validator_refusal_writes_nothing_and_names_every_violation(tmp_path: Path) -> None:
    """A hand-edited ID that collides with a stored one is never written anyway."""

    world = build_world(tmp_path)
    clash = read_json(world.memory, f"knowledge/families/{BASE_FAMILY}-landing.json")
    copy = f"knowledge/families/{BASE_FAMILY}-copy.json"
    write(world.memory, {copy: json.dumps(clash, indent=2) + "\n"})
    before = tree_bytes(world.memory)
    report = _write(world, _conforming())
    assert report.state == "refused"
    rules = {violation.rule for violation in report.violations if not violation.report_only}
    assert rules == {"R22.2-identity"}, report.render()
    assert tree_bytes(world.memory) == before


def test_problems_refuse_the_whole_operation_and_each_is_named(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    before = tree_bytes(world.memory)
    unexplained = {**target("land_pair"), "rationale": ""}
    document = {
        "entries": [
            entry("A-1", target=[target("check")], scope=SCOPE, admission=ADMISSION),
            entry("A-2", target=[target("absent_function")], scope=SCOPE, admission=ADMISSION),
            entry("A-3", target=[unexplained], scope=SCOPE, admission=ADMISSION),
        ],
        "history": [
            {"subject": "INV-ZZZZZZ", "disposition": "no_impact", "reason": "r"},
            {"subject": BASE_INVARIANT, "disposition": "moved", "reason": "r", "cover": []},
            {"subject": BASE_INVARIANT, "disposition": "moved", "reason": "r", "because": "D1"},
        ],
    }
    report = _write(world, document)
    assert report.state == "refused"
    rendered = report.render()
    assert "bound more than once" in rendered
    assert "does not define 'absent_function'" in rendered
    assert "realization_rationale_absent" in rendered
    assert "unknown subject" in rendered
    assert "unknown row key(s) ['cover']" in rendered
    assert "'because' is a list" in rendered
    assert tree_bytes(world.memory) == before


def test_a_meaning_change_increments_the_revision_once_against_the_base(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    changed = entry(
        "A-9",
        invariant_id=BASE_INVARIANT,
        statement="Code and memory land together.",
        evidence="review note R-7",
    )
    row = {
        "subject": BASE_INVARIANT,
        "disposition": "changed",
        "reason": "The statement was sharpened.",
        "effect": "clarify",
    }
    invariant_path = f"knowledge/invariants/{BASE_INVARIANT}-landing-pair.json"
    for _ in range(2):
        report = _write(world, {"entries": [changed], "history": [row]})
        assert report.state == "written", report.render()
        invariant = read_json(world.memory, invariant_path)
        assert invariant["revision"] == 2
        # Another leaf's origin is kept exactly; the updater's evidence goes into its own row.
        assert invariant["origin"] == {
            "task": "260101-OLD",
            "leaf": "260101-OLD-L1",
            "legacyId": "legacy-invariant-landing-pair",
        }
        (written,) = read_json(world.memory, f"knowledge/history/{LEAF_ID}.json")["rows"]
        assert written["revision"] == 2
        assert written["reason"] == ("The statement was sharpened. Evidence (A-9): review note R-7")
        assert report.evidence[0].stored_in == (
            f"knowledge/history/{LEAF_ID}.json#{BASE_INVARIANT}",
        )
    unrowed = _write(world, {"entries": [changed]})
    assert unrowed.state == "refused"
    assert f"add a row with subject {BASE_INVARIANT} to 'history'" in unrowed.render()
    stale = _write(world, {"history": [{**row, "disposition": "no_impact", "effect": None}]})
    assert stale.state == "refused"
    assert "keeps the K_B revision 1" in stale.render()


def test_incidental_is_written_as_support_and_a_closed_history_is_frozen(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    incidental = target("land_pair", role="incidental")
    document = [entry("A-4", target=[incidental], scope=SCOPE, admission=ADMISSION)]
    assert _write(world, document).state == "written"
    (realization,) = [
        one
        for one in read_json(world.memory, f"onboarding/{CODE_FILE}.json")["realizes"]
        if one["id"] != BASE_REALIZATION
    ]
    assert realization["role"] == "support"
    history = f"knowledge/history/{LEAF_ID}.json"
    kept = {
        "id": "ROW-AAAAAA",
        "subject": BASE_INVARIANT,
        "disposition": "no_impact",
        "reason": "r",
        "items": [],
        "covers": [],
        "revision": 1,
    }
    closed = {"schema": "ar-history/v1", "leaf": LEAF_ID, "closed": True, "rows": [kept]}
    write(world.memory, {history: json.dumps(closed, indent=2, sort_keys=True) + "\n"})
    commit_all(world.memory, "closed")
    refused = _write(
        world, {"history": [{"subject": BASE_INVARIANT, "disposition": "no_impact", "reason": "r"}]}
    )
    assert refused.state == "refused"
    assert "frozen" in refused.render()
    sharpened = entry("A-9", invariant_id=BASE_INVARIANT, statement="Pairs land together.")
    contradicted = _write(world, [sharpened]).render()
    assert "row ROW-AAAAAA" in contradicted and "closed and frozen" in contradicted
    assert "name this row again" not in contradicted


def test_a_planning_run_writes_nothing_and_unconverted_memory_is_not_this_route(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    world = build_world(tmp_path)
    before = tree_bytes(world.memory)
    planned = _write(world, _conforming(), commit=False)
    assert planned.state == "planned" and planned.written, planned.render()
    assert tree_bytes(world.memory) == before
    listed = world.task_root / "handoff.json"
    listed.write_text(json.dumps(_conforming()), encoding="utf-8")
    assert _ingest(world, listed, "--baseline", str(listed)) == 2
    assert "--baseline belong to the database candidate" in capsys.readouterr().out
    assert converted_contract(str(world.contract)) is not None
    (world.memory / "knowledge/layout.json").unlink()
    assert converted_contract(str(world.contract)) is None
    refused = _write(world, _conforming())
    assert refused.state == "refused"
    assert "unconverted" in refused.render()


def test_a_bootstrap_of_converted_memory_writes_as_a_wave(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    world = build_world(tmp_path)
    listed = world.root / "handoff.json"
    joined = {
        "subject": BASE_FAMILY,
        "disposition": "assigned",
        "reason": "Joined.",
        "examined": ["handoff:B-1"],
    }
    document = {
        "entries": [entry("B-1", target=[target("land_pair")], scope=SCOPE, admission=ADMISSION)],
        "history": [joined],
    }
    listed.write_text(json.dumps(document), encoding="utf-8")
    args = argparse.Namespace(hand_off_list=str(listed), commit=True, as_json=True, wave="")
    task = "knowledge-bootstrap:agents-remember"
    assert run_wave_write(args, memory_root=world.memory, code_root=world.code, task=task) == 2
    args.wave = "bootstrap-1"
    assert run_wave_write(args, memory_root=world.memory, code_root=world.code, task=task) == 0
    printed = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert printed["state"] == "written"
    invariant = read_json(world.memory, _new_invariant_path(world))
    assert invariant["origin"]["task"] == task
    assert invariant["origin"]["wave"] == "bootstrap-1"
    history = read_json(world.memory, "knowledge/history/bootstrap-1.json")
    assert history["wave"] == "bootstrap-1"
    assert history["rows"][0]["examined"] == [{"id": invariant["id"], "revision": 1}]


def test_a_row_the_candidate_contradicts_refuses_until_it_is_named_again(tmp_path: Path) -> None:
    """Every row of the leaf's history file must agree with the candidate, not only this run's."""

    world = build_world(tmp_path)
    authored = entry("A-1", target=[target("land_pair")], scope=SCOPE, admission=ADMISSION)
    row = {
        "subject": "handoff:A-1",
        "disposition": "extended",
        "reason": "The pair gained its function.",
        "covers": [{"handoff": "A-1"}],
    }
    assert _write(world, {"entries": [authored], "history": [row]}).state == "written"
    write(world.code, {CODE_FILE: "# two lines\n\n" + CODE})
    before = tree_bytes(world.memory)
    stale = _write(world, {"entries": [authored]})
    assert stale.state == "refused"
    assert "no longer carry the row's 'after' anchor" in stale.render()
    assert tree_bytes(world.memory) == before
    assert _write(world, {"entries": [authored], "history": [row]}).state == "written"
    family = {
        "subject": BASE_FAMILY,
        "disposition": "no_impact",
        "reason": "Members examined.",
        "examined": [BASE_INVARIANT],
    }
    assert _write(world, {"history": [row, family]}).state == "written"
    sharpened = entry("A-9", invariant_id=BASE_INVARIANT, statement="Pairs land together.")
    changed = {
        "subject": BASE_INVARIANT,
        "disposition": "changed",
        "reason": "r",
        "effect": "clarify",
    }
    refused = _write(world, {"entries": [sharpened], "history": [changed]})
    assert refused.state == "refused"
    assert f"examined members ['{BASE_INVARIANT}'] changed revision" in refused.render()
    fixed = _write(world, {"entries": [sharpened], "history": [changed, family]})
    assert fixed.state == "written", fixed.render()


def test_an_entry_that_names_no_record_is_refused_and_nothing_of_it_dropped(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    ruling = entry(
        "R-1",
        kind="decision",
        evidence=f"ran {TEST_ID}",
        proofs=[{"test": TEST_ID, "facet": "a pair lands"}],
    )
    refused = _write(world, [ruling])
    assert refused.state == "refused"
    rendered = refused.render()
    assert "entry R-1: the entry names no record" in rendered
    assert "keep it task-local" in rendered
    assert "'proofs' prove an invariant" in rendered
    attached = {
        **_decision(links=[{"relation": "constrains", "target": BASE_INVARIANT}]),
        "entry": "R-1",
    }
    written = _write(world, {"entries": [{**ruling, "proofs": []}], "records": [attached]})
    assert written.state == "written", written.render()
    assert written.rulings == ("R-1",)
    decision = read_json(world.memory, _record_path(world, "decisions"))
    assert decision["origin"]["handoff"]["evidence"] == [f"ran {TEST_ID}"]


def test_a_rerun_that_changes_a_locator_removes_this_leafs_old_entry(tmp_path: Path) -> None:
    """Line-range and whole-file anchors; the replaced entry goes, other leaves' entries stay."""

    world = build_world(tmp_path)
    first = entry("A-5", target=[target("land_pair")], scope=SCOPE, admission=ADMISSION)
    assert _write(world, [first]).state == "written"
    ranged = {"path": CODE_FILE, "locator": {"kind": "line_range", "start": 4, "end": 6}}
    whole = {"path": TEST_FILE, "locator": {"kind": "file"}}
    second = {**first, "target": [{**ranged, "rationale": "r"}, {**whole, "rationale": "w"}]}
    report = _write(world, [second])
    assert report.state == "written", report.render()
    assert [one.action for one in report.entries] == ["created", "created", "removed"]
    realizes = read_json(world.memory, f"onboarding/{CODE_FILE}.json")["realizes"]
    assert {one["id"] for one in realizes} >= {BASE_REALIZATION}
    (mine,) = [one for one in realizes if one["id"] != BASE_REALIZATION]
    body = "".join(CODE.splitlines(keepends=True)[3:6]).encode()
    assert mine["anchor"]["locator"] == {"kind": "line_range", "start": 4, "end": 6}
    assert mine["anchor"]["content"] == f"sha256:{hashlib.sha256(body).hexdigest()}"
    (file_entry,) = read_json(world.memory, f"onboarding/{TEST_FILE}.json")["realizes"]
    whole_bytes = (world.code / TEST_FILE).read_bytes()
    assert file_entry["anchor"]["content"] == f"sha256:{hashlib.sha256(whole_bytes).hexdigest()}"


def test_the_bootstrap_command_dispatches_converted_memory_to_the_file_writer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path)
    listed = world.root / "handoff.json"
    listed.write_text(
        json.dumps([entry("B-2", target=[target("land_pair")], scope=SCOPE, admission=ADMISSION)]),
        encoding="utf-8",
    )
    admission = SimpleNamespace(
        memory_worktree=world.memory, code_worktree=world.code, scope="knowledge-bootstrap:repo"
    )
    monkeypatch.setattr(
        knowledge_bootstrap, "_admitted", lambda _args: SimpleNamespace(admission=admission)
    )
    argv = ["knowledge-bootstrap", "--repo", "repo", "--list", str(listed), "--json"]
    assert main([*argv, "--authorization-ref", "boot", "--wave", "wave-1", "--commit"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["state"] == "written" and printed["authorization"] == "boot"
    assert read_json(world.memory, _new_invariant_path(world))["origin"]["wave"] == "wave-1"


def test_the_leaf_file_route_refuses_a_blank_authorization_and_reports_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    world = build_world(tmp_path)
    listed = world.task_root / "handoff.json"
    listed.write_text(json.dumps(_conforming()), encoding="utf-8")
    argv = ["knowledge-ingest", "--contract", str(world.contract), "--list", str(listed)]
    assert main([*argv, "--authorization-ref", "  "]) == 2
    assert "--authorization-ref must not be blank" in capsys.readouterr().out
    assert main([*argv, "--authorization-ref", "curator-7", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["authorization"] == "curator-7"


def test_family_route_rules_are_reports_in_the_writer_and_refusals_at_a_commit_route(
    tmp_path: Path,
) -> None:
    """MIK-R04 rule 6: a mid-leaf coverage break is written; the commit route still refuses it."""

    world = build_world(tmp_path)
    outside = {
        "path": TEST_FILE,
        "locator": {"kind": "symbol", "value": "test_plain"},
        "rationale": "It exercises the pair outside the family's route.",
    }
    joined = {
        "key": "F-join",
        "kind": "family",
        "id": BASE_FAMILY,
        "fields": {"members": [BASE_INVARIANT, "handoff:A-6"]},
    }
    examined = {
        "subject": BASE_FAMILY,
        "disposition": "assigned",
        "reason": "A new member joined; its route follows in a later run.",
        "examined": [BASE_INVARIANT, "handoff:A-6"],
    }
    document = {
        "entries": [entry("A-6", target=[outside], scope=SCOPE, admission=ADMISSION)],
        "records": [joined],
        "history": [examined],
    }
    report = _write(world, document)
    assert report.state == "written", report.render()
    coverage = [one for one in report.violations if one.rule == "R04.2-coverage"]
    assert coverage and all(one.report_only for one in coverage), report.render()
    base = knowledge_tree_from_git(world.memory, "HEAD")
    with pytest.raises(KnowledgeValidationError, match=r"R04\.2-coverage"):
        require_valid_commit(
            knowledge_tree_from_directory(world.memory),
            bases=(base,),
            code=CodeDirectory("code", world.code),
        )


def test_the_writer_refuses_a_new_invariant_whose_claim_the_tree_does_not_support(
    tmp_path: Path,
) -> None:
    """MIK-R27: the validator's admission rule refuses inside the writer, and nothing is written."""

    world = build_world(tmp_path)
    before = tree_bytes(world.memory)
    claim = {"criteria": ["guarded_by_test"], "justification": "test_plain proves the landing."}
    authored = entry("G-1", target=[target("land_pair")], scope=SCOPE, admission=claim)

    refused = _write(world, [authored])
    assert refused.state == "refused"
    assert "[R27.2-new-record]" in refused.render() and "guarded_by_test" in refused.render()
    assert tree_bytes(world.memory) == before

    proven = {**authored, "proofs": [{"test": f"{TEST_FILE}::test_plain", "facet": "it lands"}]}
    assert _write(world, [proven]).state == "written"


def test_a_moved_row_whose_after_names_another_path_relocates_the_entry(tmp_path: Path) -> None:
    """L06 ruling Q6 (MIK-R07 rule 4, MIK-R12 rule 2): a moved row re-anchors an entry across files.

    The file moves; the curator's ``moved`` row names the entry and its new ``path``. The writer
    moves the entry into that file's sidecar with its ID and authored fields, and fills ``blob``
    and ``content`` at C. Any other disposition naming a path is refused.
    """

    world = build_world(tmp_path)
    moved = "pkg/moved/landing.py"
    (world.code / "pkg/moved").mkdir(parents=True)
    git(world.code, "mv", CODE_FILE, moved)
    cover = {"id": BASE_REALIZATION, "path": moved}
    refused = _write(
        world,
        {
            "history": [
                {
                    "subject": BASE_INVARIANT,
                    "disposition": "extended",
                    "reason": "r",
                    "covers": [cover],
                }
            ]
        },
    )
    assert refused.state == "refused"
    assert any("only on a moved row" in one.message for one in refused.problems)
    # Review F1 and N7: a path that is empty, absolute or escapes the repository, or a path on a
    # cover that also removes its entry, is a named problem -- never an uncaught error.
    untouched = tree_bytes(world.memory)
    for bad, named in (
        ({"id": BASE_REALIZATION, "path": "../outside.py"}, "not a repository path"),
        ({"id": BASE_REALIZATION, "path": "/abs/landing.py"}, "not a repository path"),
        ({"id": BASE_REALIZATION, "path": ""}, "not a repository path"),
        ({"id": BASE_REALIZATION, "path": moved, "remove": True}, "either removes"),
    ):
        row = {"subject": BASE_INVARIANT, "disposition": "moved", "reason": "r", "covers": [bad]}
        report = _write(world, {"history": [row]})
        assert report.state == "refused", bad
        assert any(named in one.message for one in report.problems), report.render()
    assert tree_bytes(world.memory) == untouched

    document = {
        "history": [
            {
                "subject": BASE_INVARIANT,
                "disposition": "moved",
                "reason": "The module moved into pkg/moved.",
                "covers": [cover],
            }
        ]
    }
    report = _write(world, document)
    assert report.state == "written", report.render()
    old_sidecar = read_json(world.memory, f"onboarding/{CODE_FILE}.json")
    assert BASE_REALIZATION not in {one["id"] for one in old_sidecar["realizes"]}
    new_sidecar = read_json(world.memory, f"onboarding/{moved}.json")
    (relocated,) = new_sidecar["realizes"]
    assert relocated["id"] == BASE_REALIZATION and relocated["role"] == "primary-authority"
    blob = git(world.code, "hash-object", moved)
    assert relocated["anchor"]["blob"] == blob
    history = read_json(world.memory, f"knowledge/history/{LEAF_ID}.json")
    (row,) = history["rows"]
    (covered,) = row["covers"]
    assert covered["before"]["path"] == CODE_FILE and covered["after"]["path"] == moved
    assert covered["after"]["content"] == covered["before"]["content"]  # same symbol body
    assert InvariantRow.model_validate(row).disposition == "moved"
    # A rerun of the same list finds the entry already there and changes nothing.
    before = tree_bytes(world.memory)
    assert _write(world, document).state == "written"
    assert tree_bytes(world.memory) == before
