"""MIK-R13: decision records with rejected alternatives.

The content rules run in the knowledge validator over a small converted tree built here (the D12 and
D18 decisions of the packet's examples), the derived reads are checked on the models, and requirement
endpoints are resolved by the requirement owner against a real task directory. The writer's round
trip is in ``test_knowledge_writer.py``.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from agents_remember.memory.knowledge.requirement_endpoint import (
    TASK_OUTSIDE_TASKS,
    TASK_PLANE_UNAVAILABLE,
    resolve_requirement_endpoint,
)
from agents_remember.memory_quality.knowledge_validator import (
    CodePathSet,
    KnowledgeTree,
    ValidationReport,
    validate_tree,
)
from agents_remember.models.knowledge_files import (
    canonical_text,
    derived_record_id,
    parse_document,
)
from agents_remember.models.knowledge_files.decisions import (
    derived_status,
    governs_links,
    reconsider_links,
    superseded_by,
)
from agents_remember.models.knowledge_files.records import DecisionRecord
from agents_remember.models.knowledge_files.shapes import RequirementReference

D12, D18, ASSUMPTION = "DEC-D12RTE", "DEC-D18TXT", "ASM-100K0R"
TASK = {"repository": "agents-remember", "path": "260928_maintained-invariant-knowledge"}
R04 = {
    "task": TASK,
    "packet": "requirements/MIK-R04-v2-family-routes.md",
    "id": "MIK-R04",
    "version": "v2",
}
ORIGIN = {"task": "260928-MIK", "leaf": "260928-MIK-L13", "handoff": {"evidence": ["D12"]}}
CODE = CodePathSet("code", frozenset({"pkg/landing.py", "mcp/src/agents_remember/memory/x.py"}))


def _d12() -> dict[str, Any]:
    return {
        "schema": "ar-decision/v1",
        "id": D12,
        "revision": 1,
        "status": "active",
        "context": "Families span several subtrees; they need homes that stay accurate.",
        "alternatives": [
            {
                "option": "Several local routes per family",
                "status": "chosen",
                "reason": "Local routes make every place a family's code lives easy to find.",
            },
            {
                "option": "One owning route",
                "status": "rejected",
                "reason": "Cross-tree families collapse to the repository root.",
                "reconsider_when": "Families rarely span subtrees.",
            },
        ],
        "consequences": ["Coverage and non-empty checks run per family route set."],
        "decider": "developer (260928-MIK DEVELOPER-DIRECTION D12)",
        "supersedes": [],
        "links": [
            {"relation": "constrains", "target": "route:mcp/src/agents_remember/memory"},
            {"relation": "motivated_change_to", "target": R04},
        ],
        "admission": {
            "criteria": ["real_alternatives", "constrains_future_work"],
            "justification": "Two routing designs were weighed and the choice binds every family.",
        },
        "origin": ORIGIN,
    }


def _d18() -> dict[str, Any]:
    return {
        "schema": "ar-decision/v1",
        "id": D18,
        "revision": 1,
        "status": "active",
        "context": "Knowledge needs one source of truth that people and Git can read.",
        "alternatives": [
            {
                "option": "Text files in Git",
                "status": "chosen",
                "reason": "Git already gives history, diff, merge and review for text.",
            },
            {
                "option": "Canonical SQLite",
                "status": "rejected",
                "reason": "About 22,000 lines of storage-only machinery and unreadable knowledge.",
                "reconsider_when": "Records pass about 100,000 or writers appear outside Git.",
            },
            {
                "option": "Both, kept in sync",
                "status": "deferred",
                "reason": "Two sources of truth disagree.",
                "reconsider_when": "A reader needs SQL over live knowledge.",
            },
        ],
        "consequences": ["A validator guards the memory commit."],
        "decider": "developer (260928-MIK DEVELOPER-DIRECTION D18)",
        "supersedes": [],
        "links": [
            {"relation": "constrains", "target": "route:pkg"},
            {"relation": "reconsider_on", "target": ASSUMPTION, "alternative": 1},
            {"relation": "reconsider_on", "target": ASSUMPTION, "alternative": 2},
        ],
        "admission": {
            "criteria": ["real_alternatives"],
            "justification": "A database store was built, measured and rejected for files.",
        },
        "origin": ORIGIN,
    }


def _assumption() -> dict[str, Any]:
    return {
        "schema": "ar-assumption/v1",
        "id": ASSUMPTION,
        "revision": 1,
        "status": "accepted",
        "proposition": "Knowledge stays under about 100k records and every writer uses Git.",
        "basis": "Today's memory holds a few thousand records, all committed through Git.",
        "links": [],
        "origin": ORIGIN,
    }


def _tree(*decisions: dict[str, Any]) -> KnowledgeTree:
    files = {
        "knowledge/layout.json": canonical_text(
            {"schema": "ar-memory-layout/v2", "conversion": "1"}
        ),
        f"knowledge/assumptions/{ASSUMPTION}-under-100k.json": canonical_text(_assumption()),
    }
    for document in decisions:
        files[f"knowledge/decisions/{document['id']}-decision.json"] = canonical_text(document)
    return KnowledgeTree("candidate", {path: text.encode() for path, text in files.items()})


def _validate(*decisions: dict[str, Any]) -> ValidationReport:
    return validate_tree(_tree(*decisions), code=CODE)


def _found(report: ValidationReport, prefix: str = "R13") -> list[tuple[str, str, str, bool]]:
    return [
        (one.path.rsplit("/", 1)[-1][:10], one.field, one.rule, one.report_only)
        for one in report.violations
        if one.rule.startswith(prefix)
    ]


def _variant(document: dict[str, Any], **changes: Any) -> dict[str, Any]:
    changed = copy.deepcopy(document)
    changed.update(changes)
    return changed


def test_the_packets_d12_and_d18_decisions_pass_every_rule() -> None:
    """Conforming: D12 and D18 as records, with rejected and deferred alternatives and links."""

    report = _validate(_d12(), _d18())
    assert report.ok, report.render()
    assert _found(report) == []


def test_alternative_counts_and_the_one_chosen_are_refused_by_rule() -> None:
    chosen = _d12()["alternatives"][0]
    rejected = _d12()["alternatives"][1]
    report = _validate(
        _variant(_d12(), alternatives=[chosen]),
        _variant(_d18(), id="DEC-N0CH0S", alternatives=[rejected, rejected], links=[]),
        _variant(_d18(), id="DEC-TW0CHS", alternatives=[chosen, chosen], links=[]),
    )
    assert not report.ok
    assert _found(report, "R13.1") == [
        ("DEC-D12RTE", "alternatives", "R13.1-alternatives", False),
        ("DEC-N0CH0S", "alternatives", "R13.1-alternatives", False),
        ("DEC-TW0CHS", "alternatives", "R13.1-alternatives", False),
    ]
    messages = {one.path[20:30]: one.message for one in report.refusals}
    assert "at least 2 considered alternatives; this one has 1" in messages["DEC-D12RTE"]
    assert "exactly one alternative is chosen; chosen: none" in messages["DEC-N0CH0S"]
    assert "chosen: 0, 1" in messages["DEC-TW0CHS"]


def test_a_rejected_or_deferred_alternative_without_reconsider_when_is_refused() -> None:
    document = _d18()
    for alternative in document["alternatives"][1:]:
        del alternative["reconsider_when"]
    report = _validate(document)
    assert _found(report) == [
        ("DEC-D18TXT", "alternatives.1.reconsider_when", "R13.1-reconsider-when", False),
        ("DEC-D18TXT", "alternatives.2.reconsider_when", "R13.1-reconsider-when", False),
    ]
    assert "the deferred alternative 'Both, kept in sync'" in report.refusals[1].message
    # A blank condition does not parse at all: the shape rule refuses it.
    blank = _d18()
    blank["alternatives"][1]["reconsider_when"] = "   "
    assert [one.rule for one in _validate(blank).refusals] == ["R22.1-shape"]


def test_a_stored_superseded_status_is_refused_naming_the_field() -> None:
    document = _variant(_d12(), status="superseded")
    document["alternatives"][1]["status"] = "superseded"
    report = _validate(document)
    rules = {(one.rule, one.field) for one in report.refusals}
    assert ("R13.2-superseded-derived", "status") in rules
    assert ("R13.2-superseded-derived", "alternatives.1.status") in rules
    assert any(one.rule == "R22.1-shape" for one in report.refusals)
    assert "another decision's 'supersedes' names it" in report.refusals[0].message


def test_reconsider_on_names_an_existing_rejected_or_deferred_alternative() -> None:
    document = _d18()
    document["links"] += [
        {"relation": "reconsider_on", "target": ASSUMPTION, "alternative": 0},
        {"relation": "reconsider_on", "target": ASSUMPTION, "alternative": 3},
    ]
    report = _validate(document)
    assert _found(report) == [
        ("DEC-D18TXT", "links.3.alternative", "R13.3-reconsider-on", False),
        ("DEC-D18TXT", "links.4.alternative", "R13.3-reconsider-on", False),
    ]
    first, second = report.refusals
    assert "which is chosen" in first.message and "has 3 (indexes 0 to 2)" in second.message


def test_a_decision_that_governs_nothing_is_reported_not_refused() -> None:
    document = _variant(
        _d18(), links=[{"relation": "reconsider_on", "target": ASSUMPTION, "alternative": 1}]
    )
    report = _validate(document)
    assert report.ok, report.render()
    assert _found(report) == [("DEC-D18TXT", "links", "R13.3-governs", True)]


def test_superseded_is_derived_and_reconsider_links_carry_their_subject() -> None:
    old = parse_document(_d12())
    new = parse_document(_variant(_d12(), id="DEC-NEWD12", supersedes=[D12]))
    d18 = parse_document(_d18())
    assert isinstance(old, DecisionRecord) and isinstance(new, DecisionRecord)
    assert isinstance(d18, DecisionRecord)
    superseding = superseded_by([old, new, d18])
    assert superseding == {D12: ("DEC-NEWD12",)}
    assert [derived_status(one, superseding) for one in (old, new)] == ["superseded", "active"]
    assert _validate(_d12(), _variant(_d12(), id="DEC-NEWD12", supersedes=[D12])).ok
    links = reconsider_links(d18)
    assert [(one.subject, one.alternative.status, one.link_index) for one in links] == [
        (f"reconsider:{D18}#1", "rejected", 1),
        (f"reconsider:{D18}#2", "deferred", 2),
    ]
    assert [link.relation for link in governs_links(old)] == ["constrains", "motivated_change_to"]


PACKET = """# MIK-R04 @ v2 — Family routes

| Field | Value |
| --- | --- |
| Stable ID | MIK-R04 |
| Version | v2 |

## Normative Requirement
"""


def test_requirement_endpoints_resolve_through_the_owner_and_never_refuse(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks" / TASK["repository"] / TASK["path"]
    (task_root / "requirements").mkdir(parents=True)
    (task_root / R04["packet"]).write_text(PACKET, encoding="utf-8")

    def resolve(root: Path | None, **changes: Any):
        return resolve_requirement_endpoint(
            root, RequirementReference.model_validate({**R04, **changes})
        )

    resolved = resolve(tmp_path)
    assert (resolved.state, resolved.task_root, resolved.code) == ("resolved", task_root, "")
    assert resolved.key == f"agents-remember/{TASK['path']}#MIK-R04@v2"
    newer = resolve(tmp_path, version="v3")
    assert (newer.state, newer.code) == (
        "unresolved",
        "task-intent-requirement-packet-version-mismatch",
    )
    assert newer.task_root == task_root
    missing = resolve(tmp_path, packet="requirements/MIK-R99-v1-gone.md", id="MIK-R99")
    assert missing.code == "task-intent-requirement-packet-missing"
    outside = resolve(tmp_path, task={"repository": "..", "path": TASK["path"]})
    assert (outside.state, outside.code, outside.task_root) == (
        "unresolved",
        TASK_OUTSIDE_TASKS,
        None,
    )
    assert resolve(None).code == TASK_PLANE_UNAVAILABLE
    # The validator never resolves: the same record validates whether its endpoint resolves or not.
    assert _validate(_d12()).ok
    (task_root / R04["packet"]).unlink()
    assert _validate(_d12()).ok


def test_a_decision_is_never_an_export_so_a_legacy_id_exempts_it_from_nothing() -> None:
    """L13 review F6: the conversion exports no decisions, so admission judges every new one."""

    forged = _variant(
        _d12(),
        id=derived_record_id("decision", "legacy-decision-1"),
        admission="legacy-unassessed",
        origin={**ORIGIN, "legacyId": "legacy-decision-1"},
    )
    report = _validate(forged)
    assert [(one.rule, one.field) for one in report.refusals] == [("R27.2-new-record", "admission")]
    assert "is legacy-unassessed, which only exported records carry" in report.refusals[0].message
