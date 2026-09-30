"""MIK-R14@v2: reconsideration surfacing over real Git code and converted memory repositories.

The fixture is MIK-R08's (``test_knowledge_worklist.world``) plus two decisions in K_B: D18's
(``DEC-D18TXT``: the rejected "Canonical SQLite" reconsidered on an assumption record, the deferred
"Both" on a code anchor and a requirement endpoint) and a second decision (``DEC-D12RTE``) whose
rejected alternative is reconsidered on a family and on D18 itself. A case commits a code candidate
C and a memory candidate K_C and computes the leaf's worklist; the writer cases write the rows.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_worklist import ITEM_KINDS
from agents_remember.application.knowledge_writer.memory_state import Owner
from agents_remember.application.knowledge_writer.open_questions import TaskDocOpenQuestions
from agents_remember.application.knowledge_writer.writer import WriteRequest, write_knowledge
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.memory.knowledge.requirement_endpoint import (
    latest_approved_requirement_version,
    requirement_approval,
)
from agents_remember.memory_quality.knowledge_validator import (
    CodePathSet,
    KnowledgeTree,
    validate_tree,
)
from agents_remember.memory_quality.knowledge_worklist_section import knowledge_worklist_lines
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.history import HISTORY_ROW_KINDS, row_kind_for_subject
from agents_remember.models.knowledge_files.reconsideration import (
    parse_reconsider_subject,
    reconsideration_item_open,
)
from agents_remember.tasks import TaskDocument
from test_knowledge_worklist import (
    DATA,
    DATA_V1,
    LINES,
    LINES_V1,
    OTHER,
    OTHER_V1,
    REVIEW,
    REVIEW_V1,
    TESTS,
    TESTS_V1,
    World,
    _init,
    base_memory,
    git,
)

LEAF = "260928-MIK-L99"
D18, D12, ASSUMPTION = "DEC-D18TXT", "DEC-D12RTE", "ASM-100K0R"
S18, S18B, S12 = f"reconsider:{D18}#1", f"reconsider:{D18}#2", f"reconsider:{D12}#1"
D18_PATH = f"knowledge/decisions/{D18}-text-files.json"
TASK = {"repository": "agents-remember", "path": "260928_mik"}
PACKET = "requirements/MIK-R21-v1-formats.md"
R21 = {"task": TASK, "packet": PACKET, "id": "MIK-R21", "version": "v1"}
ORIGIN = {"task": "260928-MIK", "leaf": "260928-MIK-L13"}
ADMISSION = {"criteria": ["real_alternatives"], "justification": "Weighed and chosen."}
EDITED_THIRD = REVIEW_V1.replace("    return value * 2\n", "    return value * 5\n")
EDITED_FOURTH = REVIEW_V1.replace("    return 4\n", "    return 44\n")


def _alternatives(*triples: tuple[str, str, str | None]) -> list[dict[str, Any]]:
    found = []
    for option, status, when in triples:
        one: dict[str, Any] = {"option": option, "status": status, "reason": f"{option} weighed."}
        if when is not None:
            one["reconsider_when"] = when
        found.append(one)
    return found


def decision(decision_id: str, alternatives: list[dict[str, Any]], links: list[Any]) -> str:
    return canonical_text(
        {
            "schema": "ar-decision/v1",
            "id": decision_id,
            "revision": 1,
            "status": "active",
            "context": "A choice that binds later work.",
            "alternatives": alternatives,
            "consequences": ["Later work follows it."],
            "decider": "developer",
            "supersedes": [],
            "links": [{"relation": "constrains", "target": "route:pkg"}, *links],
            "admission": ADMISSION,
            "origin": ORIGIN,
        }
    )


def assumption(proposition: str = "Knowledge stays under about 100k records.") -> str:
    return canonical_text(
        {
            "schema": "ar-assumption/v1",
            "id": ASSUMPTION,
            "revision": 1,
            "status": "accepted",
            "proposition": proposition,
            "basis": "A few thousand records today.",
            "links": [],
            "origin": ORIGIN,
        }
    )


def d18(world: World, **replace: Any) -> str:
    anchor = {**world.anchor(world.code_base, REVIEW, {"kind": "symbol", "name": "third"})}
    links = [
        {"relation": "reconsider_on", "target": ASSUMPTION, "alternative": 1},
        {"relation": "reconsider_on", "target": {**anchor, "path": REVIEW}, "alternative": 2},
        {"relation": "reconsider_on", "target": R21, "alternative": 2},
    ]
    alternatives = _alternatives(
        ("Text files in Git", "chosen", None),
        ("Canonical SQLite", "rejected", "Records pass about 100k."),
        ("Both, kept in sync", "deferred", "A reader needs SQL."),
    )
    return decision(D18, replace.get("alternatives", alternatives), replace.get("links", links))


def d12() -> str:
    return decision(
        D12,
        _alternatives(
            ("Several local routes", "chosen", None),
            ("One owning route", "rejected", "Families rarely span subtrees."),
        ),
        [
            {"relation": "reconsider_on", "target": "FAM-F00001", "alternative": 1},
            {"relation": "reconsider_on", "target": D18, "alternative": 1},
        ],
    )


@pytest.fixture
def world(tmp_path: Path) -> World:
    built = World(root=tmp_path, code=tmp_path / "code", memory=tmp_path / "memory")
    _init(built.code)
    built.code_base = built.code_commit(
        {REVIEW: REVIEW_V1, OTHER: OTHER_V1, TESTS: TESTS_V1, LINES: LINES_V1, DATA: DATA_V1}
    )
    _init(built.memory)
    files = base_memory(built, built.code_base)
    files |= {
        f"knowledge/decisions/{D18}-text-files.json": d18(built),
        f"knowledge/decisions/{D12}-local-routes.json": d12(),
        f"knowledge/assumptions/{ASSUMPTION}-under-100k.json": assumption(),
    }
    built.memory_base = built.memory_commit(files, built.code_base)
    return built


def _manifest(task_root: Path, *entries: tuple[str, ...]) -> None:
    (task_root / "requirements").mkdir(parents=True, exist_ok=True)
    packets = [
        {"id": one[0], "version": one[1], "state": one[2], **({"file": one[3]} if one[3:] else {})}
        for one in entries
    ]
    (task_root / "requirements" / "manifest.json").write_text(
        json.dumps({"format": "approved-requirement-corpus", "packets": packets}), encoding="utf-8"
    )


def _task(root: Path) -> Path:
    """The owning task of ``R21``: its v1 packet, resolvable through the requirement owner."""

    task_root = root / "coordination" / "tasks" / TASK["repository"] / TASK["path"]
    (task_root / "requirements").mkdir(parents=True)
    (task_root / PACKET).write_text(
        "# MIK-R21 @ v1\n\n| Field | Value |\n| --- | --- |\n| Stable ID | MIK-R21 |\n"
        "| Version | v1 |\n",
        encoding="utf-8",
    )
    return task_root


def _history(*rows: dict[str, Any]) -> str:
    return canonical_text({"schema": "ar-history/v1", "leaf": LEAF, "closed": False, "rows": rows})


def _candidates(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assert document["state"] == "complete", document.get("incomplete")
    return {
        item["subject"]: item
        for item in document["items"]
        if item["kind"] == "reconsideration_candidate"
    }


def _triggers(item: dict[str, Any]) -> list[tuple[str, str]]:
    return [(one["target"], one["trigger"]) for one in item["facts"]["changed"]]


def _links(document: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    return {(one["subject"], one["link"]): one for one in document["reconsideration"]["links"]}


# --------------------------------------------------------------------------------------------------
# Rule 2: the manifest lookup
# --------------------------------------------------------------------------------------------------


def test_the_manifest_lookup_returns_the_highest_approved_version(tmp_path: Path) -> None:
    task_root = tmp_path / "task"
    assert latest_approved_requirement_version(task_root, "MIK-R21") is None
    missing = requirement_approval(task_root, "MIK-R21")
    assert (missing.state, "no requirements/manifest.json" in missing.detail) == ("unknown", True)
    _manifest(
        task_root,
        ("MIK-R21", "v1", "superseded"),
        ("MIK-R21", "v2", "approved"),
        ("MIK-R21", "v10", "approved"),
        ("MIK-R21", "v11", "draft"),
        ("MIK-R07", "v12", "approved"),
    )
    # The integer after "v" is compared: v10 is newer than v2, and a draft v11 does not count.
    assert latest_approved_requirement_version(task_root, "MIK-R21") == "v10"
    approval = requirement_approval(task_root, "MIK-R21")
    assert approval.newer_than("v2") and not approval.newer_than("v10")
    assert requirement_approval(task_root, "MIK-R99").state == "not_approved"
    (task_root / "requirements" / "manifest.json").write_text('{"packets": []}', encoding="utf-8")
    assert requirement_approval(task_root, "MIK-R21").state == "unknown"


# --------------------------------------------------------------------------------------------------
# Rule 1: the triggers (and the packet's conforming and non-conforming examples)
# --------------------------------------------------------------------------------------------------


def test_a_revised_assumption_surfaces_the_rejected_alternative(world: World) -> None:
    """Conforming: the D18 example. Non-conforming guard: an unchanged tree raises nothing."""

    assert _candidates(world.worklist(world.code_base)) == {}
    world.memory_commit(
        {
            f"knowledge/assumptions/{ASSUMPTION}-under-100k.json": assumption(
                "Knowledge stays under about 1M records."
            )
        },
        world.code_base,
    )
    document = world.worklist(world.code_base)
    found = _candidates(document)
    assert list(found) == [S18]
    item = found[S18]
    assert _triggers(item) == [(ASSUMPTION, "record_file")]
    facts = item["facts"]
    assert (facts["decision"], facts["alternative"], facts["option"]) == (
        D18,
        1,
        "Canonical SQLite",
    )
    assert (facts["alternativeStatus"], facts["decisionStatus"]) == ("rejected", "active")
    assert facts["reconsiderWhen"] == "Records pass about 100k."
    assert item["satisfiedBy"] is None
    assert ITEM_KINDS["reconsideration_candidate"].accepts_subject(S18)
    assert any(f"| reconsideration_candidate | {S18} |" in one for one in _lines(document))


def _lines(document: dict[str, Any]) -> list[str]:
    return knowledge_worklist_lines(document, "w.json")


def test_a_triggering_history_row_surfaces_and_other_dispositions_do_not(world: World) -> None:
    examined = [{"id": "INV-AAAAAA", "revision": 1}, {"id": "INV-BBBBBB", "revision": 1}]
    row = {"id": "ROW-AAAAAA", "subject": "FAM-F00001", "reason": "Judged.", "items": []}
    path = f"knowledge/history/{LEAF}.json"
    world.memory_commit(
        {path: _history({**row, "disposition": "no_impact", "examined": examined})},
        world.code_base,
    )
    assert _candidates(world.worklist(world.code_base)) == {}
    world.memory_commit(
        {path: _history({**row, "disposition": "rerouted", "examined": examined})},
        world.code_base,
    )
    found = _candidates(world.worklist(world.code_base))
    assert list(found) == [S12]
    (changed,) = found[S12]["facts"]["changed"]
    assert (changed["trigger"], changed["row"], changed["disposition"]) == (
        "history_row",
        "ROW-AAAAAA",
        "rerouted",
    )


def test_a_linked_anchor_triggers_when_touched_or_absent_and_not_otherwise(world: World) -> None:
    untouched = world.code_commit({REVIEW: EDITED_FOURTH})
    document = world.worklist(untouched)
    assert _candidates(document) == {}
    assert _links(document)[(S18B, 2)]["class"] == "carried"
    touched = world.code_commit({REVIEW: EDITED_THIRD})
    found = _candidates(world.worklist(touched))
    assert list(found) == [S18B]
    (changed,) = found[S18B]["facts"]["changed"]
    assert (changed["trigger"], changed["entry"]["class"]) == ("anchor", "touched")
    assert changed["entry"]["kind"] == "link"
    # The link anchor is not an entry: it never joins the run's classified entries.
    assert all(one["id"].startswith(("RLZ-", "PRF-")) for one in world.worklist(touched)["entries"])
    gone = world.code_commit({REVIEW: None})
    (changed,) = _candidates(world.worklist(gone))[S18B]["facts"]["changed"]
    assert changed["entry"]["class"] == "moved_or_absent"


def test_a_requirement_endpoint_triggers_only_on_a_newer_approved_version(
    world: World, tmp_path: Path
) -> None:
    coordination = tmp_path / "coordination"
    task_root = _task(tmp_path)

    def run() -> dict[str, Any]:
        return world.worklist(world.code_base, coordination_root=coordination)

    # No manifest: no trigger, and the endpoint shows approval_state unknown.
    document = run()
    assert _candidates(document) == {}
    link = _links(document)[(S18B, 3)]
    assert (link["endpoint"], link["approvalState"]) == ("resolved", "unknown")
    _manifest(task_root, ("MIK-R21", "v1", "approved"))
    assert _candidates(run()) == {}
    assert _links(run())[(S18B, 3)]["latestApproved"] == "v1"
    _manifest(task_root, ("MIK-R21", "v1", "superseded"), ("MIK-R21", "v2", "approved"))
    found = _candidates(run())
    assert list(found) == [S18B]
    (changed,) = found[S18B]["facts"]["changed"]
    assert (changed["trigger"], changed["version"], changed["latestApproved"]) == (
        "requirement_version",
        "v1",
        "v2",
    )
    # An endpoint that does not resolve never triggers, whatever the manifest says.
    (task_root / PACKET).unlink()
    document = run()
    assert _candidates(document) == {}
    assert _links(document)[(S18B, 3)]["endpoint"] == "unresolved"
    # No coordination root: nothing resolves.
    assert _links(world.worklist(world.code_base))[(S18B, 3)]["endpoint"] == "unresolved"


def test_one_hop_a_decision_change_never_chains_on(world: World) -> None:
    raised = json.loads(d18(world))
    raised["status"] = "under_reconsideration"
    world.memory_commit(
        {f"knowledge/decisions/{D18}-text-files.json": canonical_text(raised)}, world.code_base
    )
    document = world.worklist(world.code_base)
    # D12 reconsiders on D18, whose record changed: no candidate for D12, and none for D18.
    assert _candidates(document) == {}
    assert _links(document)[(S12, 2)]["note"].startswith("one hop")


# --------------------------------------------------------------------------------------------------
# Rules 4 and 5: the rows, the satisfying rule, and the raise
# --------------------------------------------------------------------------------------------------


class FakeQuestions:
    def __init__(self, refuse_check: str | None = None, refuse_append: str | None = None) -> None:
        self.refuse_check, self.refuse_append = refuse_check, refuse_append
        self.checked: list[str] = []
        self.appended: list[str] = []

    def check(self, key: str, question: str) -> str | None:
        assert question.startswith(key)
        self.checked.append(question)
        return self.refuse_check

    def append(self, key: str, question: str) -> str | None:
        assert question.startswith(key)
        if self.refuse_append is None:
            self.appended.append(question)
        return self.refuse_append


def _write(
    world: World,
    rows: list[dict[str, Any]],
    questions: Any,
    commit: bool = True,
    worklist: dict[str, Any] | None = None,
) -> Any:
    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=Owner(task="260928-MIK", kind="leaf", id=LEAF),
            handoff_path="handoff.json",
            document={"history": rows},
            commit=commit,
            questions=questions,
            worklist=worklist,
        )
    )


def _revise_assumption(world: World) -> None:
    world.memory_commit(
        {f"knowledge/assumptions/{ASSUMPTION}-under-100k.json": assumption("Up to 1M records.")},
        world.code_base,
    )


def _decision_status(world: World) -> str:
    path = world.memory / f"knowledge/decisions/{D18}-text-files.json"
    return json.loads(path.read_text(encoding="utf-8"))["status"]


def test_still_rejected_answers_the_candidate_and_the_stored_predicate_agrees(
    world: World,
) -> None:
    _revise_assumption(world)
    (item,) = _candidates(world.worklist(world.code_base)).values()
    row = {"subject": S18, "disposition": "still_rejected", "reason": "1M is still small."}
    report = _write(world, [{**row, "items": [item["id"]]}], None)
    assert report.state == "written", report.render()
    assert _decision_status(world) == "active"  # still_rejected changes nothing else
    git(world.memory, "add", "-A")
    git(world.memory, "commit", "-q", "-m", f"rows\n\nCode-Commit: {world.code_base}")
    answered = _candidates(world.worklist(world.code_base))[S18]
    (row_id,) = [one.id for one in report.rows]
    assert answered["satisfiedBy"] == row_id and answered["id"] == item["id"]
    assert not reconsideration_item_open(answered, {S18: row_id})
    assert reconsideration_item_open(item, {})
    assert reconsideration_item_open(item, {S18B: row_id})
    refusals = {
        "not allowed": [{**row, "disposition": "no_impact"}],
        "carries no ['effect']": [{**row, "effect": "retire"}],
        "only a rejected or deferred": [{**row, "subject": f"reconsider:{D18}#0"}],
        "has no alternative 7": [{**row, "subject": f"reconsider:{D18}#7"}],
        "names no decision": [{**row, "subject": "reconsider:DEC-ZZZZZZ#1"}],
    }
    for expected, rows in refusals.items():
        refused = _write(world, rows, None, commit=False)
        assert refused.state == "refused" and expected in refused.render(), expected


def _assert_raise_refusals_write_nothing(world: World, row: dict[str, Any]) -> None:
    """Without a task owner, a check refusal or an append refusal, nothing is written."""

    before = (world.memory / D18_PATH).read_bytes()
    for questions, why in (
        (None, "no task owner"),
        (FakeQuestions(refuse_check="doc unreadable"), "doc unreadable"),
        (FakeQuestions(refuse_append="publication refused"), "publication refused"),
    ):
        refused = _write(world, [row], questions)
        assert refused.state == "refused" and why in refused.render(), why
        assert refused.written == ()
        assert (world.memory / D18_PATH).read_bytes() == before


def test_raise_sets_the_status_and_appends_the_question_or_is_refused(world: World) -> None:
    _revise_assumption(world)
    row = {"subject": S18, "disposition": "raise", "reason": "1M records is in sight."}
    _assert_raise_refusals_write_nothing(world, row)
    planned = _write(world, [row], FakeQuestions(), commit=False)
    assert planned.state == "planned", planned.render()
    questions = FakeQuestions()
    written = _write(world, [row], questions)
    assert written.state == "written", written.render()
    assert _decision_status(world) == "under_reconsideration"
    (question,) = questions.appended
    assert question.startswith(f"[{S18} raised by {LEAF}]")
    assert "'Canonical SQLite'" in question and "1M records is in sight." in question
    revision = json.loads((world.memory / D18_PATH).read_text(encoding="utf-8"))["revision"]
    assert revision == 1  # status carries no meaning
    git(world.memory, "add", "-A")
    git(world.memory, "commit", "-q", "-m", f"raise\n\nCode-Commit: {world.code_base}")
    answered = _candidates(world.worklist(world.code_base))[S18]
    assert answered["satisfiedBy"] == written.rows[0].id


def _config(coordination: Path) -> McpRuntimeConfig:
    repo = coordination / "repo"
    repo.mkdir(parents=True)
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "t@example.invalid"),
        ("config", "user.name", "t"),
        ("commit", "-q", "--allow-empty", "-m", "base"),
        ("update-ref", "refs/remotes/origin/main", "HEAD"),
        ("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    return McpRuntimeConfig(
        config_path=coordination / "settings.json",
        coordination_root=coordination,
        workspace_root=coordination,
        transcript_root=coordination / "logs" / "mcp",
        repositories={"agents-remember": RepositoryScope(repo_id="agents-remember", path=repo)},
    )


def test_the_task_document_append_preserves_questions_through_task_doc(tmp_path: Path) -> None:
    coordination = tmp_path / "coordination"
    task_root = coordination / "tasks" / "agents-remember" / "260928_case"
    task_root.mkdir(parents=True)
    leaf = TaskDocument.model_validate(
        {
            "id": LEAF,
            "slug": "99_leaf",
            "title": "Leaf",
            "kind": "subTask",
            "repo": "agents-remember",
            "createdAt": "2026-09-29T00:00+02:00",
            "objective": "Surface reconsideration.",
            "openQuestions": ["An earlier question?"],
        }
    )
    document_path = task_root / "99_leaf.json"
    document_path.write_text(leaf.model_dump_json(by_alias=True, exclude_none=True), "utf-8")
    port = TaskDocOpenQuestions(
        config=_config(coordination),
        repo_id="agents-remember",
        contract_path=task_root / "series-contract.md",
        task_root=task_root,
        leaf=LEAF,
    )
    key = f"[{S18} raised by {LEAF}]"
    question = f"{key} Reconsider 'Canonical SQLite'?"
    before = document_path.read_bytes()
    assert port.check(key, question) is None
    assert document_path.read_bytes() == before  # the check is a dry run
    assert port.append(key, question) is None
    stored = json.loads(document_path.read_text(encoding="utf-8"))["openQuestions"]
    assert stored == ["An earlier question?", question]
    assert "Reconsider 'Canonical SQLite'?" in (task_root / "99_leaf.md").read_text("utf-8")
    assert port.append(key, f"{key} reworded") is None  # one question per subject and leaf
    assert json.loads(document_path.read_text(encoding="utf-8"))["openQuestions"] == stored
    other = TaskDocOpenQuestions(
        port.config, "agents-remember", port.contract_path, task_root, "260928-MIK-L98"
    )
    assert "has no task document" in str(other.append(key, question))


# --------------------------------------------------------------------------------------------------
# The L13 carried decision: a reorder never silently retargets a link; the row registry
# --------------------------------------------------------------------------------------------------


def _tree(label: str, document: str) -> KnowledgeTree:
    files = {
        "knowledge/layout.json": canonical_text(
            {"schema": "ar-memory-layout/v2", "conversion": "1"}
        ),
        f"knowledge/assumptions/{ASSUMPTION}-under-100k.json": assumption(),
        f"knowledge/decisions/{D18}-text-files.json": document,
    }
    return KnowledgeTree(label, {path: text.encode() for path, text in files.items()})


def test_a_reorder_of_linked_alternatives_is_refused(world: World) -> None:
    base_document = d18(world)
    base = _tree("base", base_document)
    code = CodePathSet("code", frozenset({REVIEW}))
    alternatives = json.loads(base_document)["alternatives"]

    def rule(document: str) -> list[str]:
        report = validate_tree(_tree("candidate", document), bases=(base,), code=code)
        return [one.message for one in report.refusals if one.rule.startswith("R14")]

    swapped = d18(world, alternatives=[alternatives[0], alternatives[2], alternatives[1]])
    moves = rule(swapped)
    assert len(moves) == 2
    assert any("'Canonical SQLite' moved from index 1 to 2" in one for one in moves)
    assert any("'Both, kept in sync' moved from index 2 to 1" in one for one in moves)
    reworded = copy.deepcopy(alternatives)
    reworded[1]["option"] = "Canonical SQLite database"
    appended = [*alternatives, {**alternatives[2], "option": "Neither"}]
    assert rule(d18(world, alternatives=reworded)) == []  # edited in place: its links still mean it
    assert rule(d18(world, alternatives=appended)) == []
    # Moving an unlinked alternative into a linked index is a move too.
    unlinked = d18(
        world, links=[{"relation": "reconsider_on", "target": ASSUMPTION, "alternative": 1}]
    )
    base = _tree("base", unlinked)
    moved = d18(
        world,
        alternatives=[alternatives[0], alternatives[2], alternatives[1]],
        links=[{"relation": "reconsider_on", "target": ASSUMPTION, "alternative": 1}],
    )
    assert any("moved from index 1 to 2" in one for one in rule(moved))
    # A link only the candidate adds, on an alternative moved into its index (the second loop).
    plain = d18(world, links=[])
    base = _tree("base", plain)
    added = d18(
        world,
        alternatives=[alternatives[0], alternatives[2], alternatives[1]],
        links=[{"relation": "reconsider_on", "target": ASSUMPTION, "alternative": 1}],
    )
    (only,) = rule(added)
    assert "'Both, kept in sync' moved from index 2 to 1" in only


def test_the_row_kind_and_the_item_kind_are_registered() -> None:
    assert "reconsideration" in {kind.name for kind in HISTORY_ROW_KINDS}
    assert row_kind_for_subject(S18).owner == "MIK-R14"
    assert parse_reconsider_subject(S18) == (D18, 1)
    assert parse_reconsider_subject(f"reconsider:{D18}#01") is None
    kind = ITEM_KINDS["reconsideration_candidate"]
    assert (kind.owner, kind.facts[-1]) == ("MIK-R14", "changed")
    assert "still_rejected" in kind.satisfying_row and "raise" in kind.satisfying_row


# --------------------------------------------------------------------------------------------------
# Rulings of 2026-09-30T04:37:56: route targets (Q1), the still_rejected refresh (Q2/Q3),
# superseded decisions (Q5)
# --------------------------------------------------------------------------------------------------

ROUTED = "DEC-RT0PKG"


def _next_leaf(world: World, code: str, files: dict[str, str | bytes | None] | None = None) -> str:
    """A later leaf's base: this leaf's memory landed, its history file set aside."""

    world.memory_base = world.memory_commit(
        {f"knowledge/history/{LEAF}.json": None, **(files or {})}, code
    )
    world.code_base = code
    return world.memory_base


def _d18_links(world: World) -> list[dict[str, Any]]:
    return json.loads((world.memory / D18_PATH).read_text(encoding="utf-8"))["links"]


def test_a_route_target_fires_only_through_a_rerouting_or_retiring_row(world: World) -> None:
    routed = decision(
        ROUTED,
        _alternatives(("Local", "chosen", None), ("Global", "rejected", "The tree flattens.")),
        [{"relation": "reconsider_on", "target": "route:pkg", "alternative": 1}],
    )
    world.memory_base = world.memory_commit(
        {f"knowledge/decisions/{ROUTED}-routes.json": routed}, world.code_base
    )
    subject = f"reconsider:{ROUTED}#1"
    examined = [{"id": "INV-CCCCCC", "revision": 1}]
    row = {"id": "ROW-AAAAAA", "subject": "FAM-F00002", "reason": "Judged.", "items": []}
    path = f"knowledge/history/{LEAF}.json"
    for disposition, fires in (("no_impact", False), ("changed", False), ("retired", True)):
        world.memory_commit(
            {path: _history({**row, "disposition": disposition, "examined": examined})},
            world.code_base,
        )
        found = _candidates(world.worklist(world.code_base))
        assert (subject in found) is fires, disposition
    (changed,) = found[subject]["facts"]["changed"]
    assert (changed["trigger"], changed["rowSubject"], changed["disposition"]) == (
        "history_row",
        "FAM-F00002",
        "retired",
    )
    # No directory-absence trigger: the route's code gone, no row, nothing raised.
    world.memory_commit({path: None}, world.code_base)
    gone = world.code_commit({REVIEW: None, OTHER: None, LINES: None, DATA: None})
    assert subject not in _candidates(world.worklist(gone))


def test_a_superseded_decision_raises_nothing_and_its_links_are_listed_skipped(
    world: World,
) -> None:
    successor = json.loads(d18(world))
    successor.update(id="DEC-NEWD18", supersedes=[D18], links=successor["links"][:1])
    world.memory_base = world.memory_commit(
        {"knowledge/decisions/DEC-NEWD18-successor.json": canonical_text(successor)},
        world.code_base,
    )
    _revise_assumption(world)
    document = world.worklist(world.code_base)
    assert S18 not in _candidates(document)
    link = _links(document)[(S18, 1)]
    assert (link["skipped"], link["trigger"]) == ("superseded", None)


def _assert_answered(document: dict[str, Any], item: dict[str, Any], row_id: str) -> None:
    """The item keeps its ID, ``satisfiedBy`` names the row, and the stored predicate agrees."""

    answered = _candidates(document)[item["subject"]]
    assert answered["id"] == item["id"]
    assert answered["satisfiedBy"] == row_id
    assert not reconsideration_item_open(answered, {item["subject"]: row_id})


def _v2_approved(tmp_path: Path) -> Path:
    """The owning task with a v2 packet that its manifest approves; returns the coordination root."""

    task_root = _task(tmp_path)
    v2 = "MIK-R21-v2-formats.md"
    (task_root / "requirements" / v2).write_text(
        (task_root / PACKET).read_text(encoding="utf-8").replace("| v1 |", "| v2 |"), "utf-8"
    )
    _manifest(
        task_root,
        ("MIK-R21", "v1", "superseded", "MIK-R21-v1-formats.md"),
        ("MIK-R21", "v2", "approved", v2),
    )
    return tmp_path / "coordination"


def test_still_rejected_repoints_a_persistent_requirement_link_so_it_raises_once(
    world: World, tmp_path: Path
) -> None:
    coordination = _v2_approved(tmp_path)
    v2 = "MIK-R21-v2-formats.md"

    def run() -> dict[str, Any]:
        return world.worklist(world.code_base, coordination_root=coordination)

    worklist = run()
    (item,) = _candidates(worklist).values()
    assert item["subject"] == S18B and item["facts"]["changed"][0]["latestApproved"] == "v2"
    row = {"subject": S18B, "disposition": "still_rejected", "reason": "v2 keeps SQL out."}
    report = _write(world, [row], None, worklist=worklist)
    assert report.state == "written", report.render()
    linked = _d18_links(world)[3]["target"]
    assert (linked["version"], linked["packet"]) == ("v2", f"requirements/{v2}")
    # Links are meaning: the decision is placed through the record path, revision 1 -> 2 (F2).
    assert json.loads((world.memory / D18_PATH).read_text("utf-8"))["revision"] == 2
    assert [(one.id, one.revision) for one in report.records] == [(D18, 2)]
    memory = world.memory_commit({}, world.code_base)
    _assert_answered(
        world.worklist(
            world.code_base, memory_base=world.memory_base, coordination_root=coordination
        ),
        item,
        report.rows[0].id,
    )
    # The next leaf starts from the refreshed link: the persistent condition does not raise again.
    _next_leaf(world, world.code_base)
    assert memory != world.memory_base
    document = run()
    assert _candidates(document) == {}
    assert _links(document)[(S18B, 3)]["latestApproved"] == "v2"


def test_still_rejected_reanchors_a_linked_anchor_so_it_stays_live(world: World) -> None:
    touched = world.code_commit({REVIEW: EDITED_THIRD})
    worklist = world.worklist(touched)
    (first,) = _candidates(worklist).values()
    assert first["subject"] == S18B
    row = {"subject": S18B, "disposition": "still_rejected", "reason": "A tweak, not SQL."}
    before = _d18_links(world)[2]["target"]
    # A raise leaves the links as they are: the reconsidered revision re-authors them.
    raised = _write(world, [{**row, "disposition": "raise"}], FakeQuestions(), worklist=worklist)
    assert raised.state == "written" and _d18_links(world)[2]["target"] == before
    report = _write(world, [row], None, worklist=worklist)
    assert report.state == "written", report.render()
    after = _d18_links(world)[2]["target"]
    assert after["content"] != before["content"] and after["path"] == REVIEW
    # The next leaf: unchanged code raises nothing; a second change raises again (live anchor).
    _next_leaf(world, touched)
    assert _candidates(world.worklist(touched)) == {}
    again = world.code_commit({REVIEW: EDITED_THIRD.replace("value * 5", "value * 7")})
    (second,) = _candidates(world.worklist(again)).values()
    assert second["subject"] == S18B and second["id"] != first["id"]
    assert second["facts"]["changed"][0]["entry"]["class"] == "touched"


# --------------------------------------------------------------------------------------------------
# Review R1 rulings of 2026-09-30T05:31:11: the refresh maps line ranges and touches only fired links
# (F1), stale link anchors raise (F3), the re-point takes the item's facts (F4)
# --------------------------------------------------------------------------------------------------

SHIFTED = "# one\n# two\n# three\n" + REVIEW_V1


def _lines_at(world: World, anchor: dict[str, Any]) -> list[str]:
    text = git(world.code, "cat-file", "-p", anchor["blob"]).splitlines()
    return text[anchor["locator"]["start"] - 1 : anchor["locator"]["end"]]


def test_the_refresh_maps_a_line_range_and_refreshes_only_the_fired_links(world: World) -> None:
    ranged = world.anchor(world.code_base, REVIEW, {"kind": "line_range", "start": 16, "end": 17})
    links = [
        {"relation": "reconsider_on", "target": ASSUMPTION, "alternative": 2},
        {"relation": "reconsider_on", "target": {**ranged, "path": REVIEW}, "alternative": 2},
    ]
    world.memory_base = world.memory_commit({D18_PATH: d18(world, links=links)}, world.code_base)
    row = {"subject": S18B, "disposition": "still_rejected", "reason": "Still no SQL."}
    # Leaf 1: three lines inserted above the range and the assumption revised. Only the
    # assumption fired, so the (carried) range link is left exactly as it was.
    shifted = world.code_commit({REVIEW: SHIFTED})
    _revise_assumption(world)
    worklist = world.worklist(shifted)
    assert _triggers(_candidates(worklist)[S18B]) == [(ASSUMPTION, "record_file")]
    assert _write(world, [row], None, worklist=worklist).state == "written"
    assert _d18_links(world)[2]["target"] == {**ranged, "path": REVIEW}
    # Leaf 2: the function's body changes. The range fires, and the refresh maps it through the
    # diff: it still covers ``def third``, now at lines 19-20.
    _next_leaf(world, shifted)
    edited = world.code_commit({REVIEW: SHIFTED.replace("value * 2", "value * 9")})
    worklist = world.worklist(edited)
    assert _triggers(_candidates(worklist)[S18B])[0][1] == "anchor"
    assert _write(world, [row], None, worklist=worklist).state == "written"
    refreshed = _d18_links(world)[2]["target"]
    assert refreshed["locator"] == {"kind": "line_range", "start": 19, "end": 20}
    assert _lines_at(world, refreshed) == ["def third(value):", "    return value * 9"]
    # Leaf 3: the next edit of ``third`` raises again.
    _next_leaf(world, edited)
    again = world.code_commit({REVIEW: SHIFTED.replace("value * 2", "value * 4")})
    assert _triggers(_candidates(world.worklist(again))[S18B])[0][1] == "anchor"


def test_a_range_that_cannot_be_mapped_refuses_the_row_naming_the_link(world: World) -> None:
    ranged = world.anchor(world.code_base, REVIEW, {"kind": "line_range", "start": 16, "end": 17})
    links = [{"relation": "reconsider_on", "target": {**ranged, "path": REVIEW}, "alternative": 2}]
    world.memory_base = world.memory_commit({D18_PATH: d18(world, links=links)}, world.code_base)
    gone = world.code_commit(
        {REVIEW: REVIEW_V1.replace("def third(value):\n    return value * 2\n", "")}
    )
    worklist = world.worklist(gone)
    row = {"subject": S18B, "disposition": "still_rejected", "reason": "Gone anyway."}
    refused = _write(world, [row], None, worklist=worklist)
    assert refused.state == "refused" and "links.1: the linked anchor" in refused.render()
    # A row naming another item than the worklist's current one is refused too.
    stale = _write(world, [{**row, "items": ["sha256:" + "0" * 64]}], None, worklist=worklist)
    assert stale.state == "refused" and "recompute the worklist" in stale.render()


def test_a_stale_link_anchor_raises_and_still_rejected_reanchors_it(world: World) -> None:
    touched = world.code_commit({REVIEW: EDITED_THIRD})
    # K_B still records the anchor at the fixture's blob, and B already holds other content: the
    # link anchor is stale at base. It raises (F3) instead of going silent.
    worklist = world.worklist(touched, base=touched)
    item = _candidates(worklist)[S18B]
    (changed,) = [one for one in item["facts"]["changed"] if one["target"] != _r21_key()]
    assert (changed["trigger"], changed["class"]) == ("anchor_stale", "stale_at_base")
    row = {"subject": S18B, "disposition": "still_rejected", "reason": "Unrelated tweak."}
    report = _write(world, [row], None, worklist=worklist)
    assert report.state == "written", report.render()
    world.memory_commit({}, touched)
    answered = _candidates(world.worklist(touched, base=touched))[S18B]
    assert answered["satisfiedBy"] == report.rows[0].id
    assert not reconsideration_item_open(answered, {S18B: report.rows[0].id})
    _next_leaf(world, touched)
    assert _candidates(world.worklist(touched)) == {}


def _r21_key() -> str:
    return f"{TASK['repository']}/{TASK['path']}#MIK-R21@v1"


def test_the_repoint_takes_the_items_approved_version_not_a_fresh_read(
    world: World, tmp_path: Path
) -> None:
    coordination = tmp_path / "coordination"
    task_root = _task(tmp_path)
    _manifest(task_root, ("MIK-R21", "v2", "approved", "MIK-R21-v2-formats.md"))
    worklist = world.worklist(world.code_base, coordination_root=coordination)
    # v3 is approved after the worklist was computed: the curator judged v2, so v2 it is.
    _manifest(
        task_root,
        ("MIK-R21", "v2", "approved", "MIK-R21-v2-formats.md"),
        ("MIK-R21", "v3", "approved", "MIK-R21-v3-formats.md"),
    )
    row = {"subject": S18B, "disposition": "still_rejected", "reason": "v2 keeps SQL out."}
    assert _write(world, [row], None, worklist=worklist).state == "written"
    linked = _d18_links(world)[3]["target"]
    assert (linked["version"], linked["packet"]) == ("v2", "requirements/MIK-R21-v2-formats.md")


# --------------------------------------------------------------------------------------------------
# Review R2 ruling of 2026-09-30T06:17:11: the refresh edits only the link that fired (N1, N2)
# --------------------------------------------------------------------------------------------------


def _reauthor_link(world: World, position: int, **changes: Any) -> bytes:
    """Edit K_C's D18 link at ``position`` in the working tree, as a same-leaf records edit would."""

    document = json.loads((world.memory / D18_PATH).read_text(encoding="utf-8"))
    document["links"][position] = {**document["links"][position], **changes}
    (world.memory / D18_PATH).write_text(canonical_text(document), encoding="utf-8")
    return (world.memory / D18_PATH).read_bytes()


def test_a_refresh_refuses_a_fired_link_re_authored_or_moved_in_the_leaf(
    world: World, tmp_path: Path
) -> None:
    coordination = _v2_approved(tmp_path)
    worklist = world.worklist(world.code_base, coordination_root=coordination)
    assert _triggers(_candidates(worklist)[S18B]) == [(_r21_key(), "requirement_version")]
    row = {"subject": S18B, "disposition": "still_rejected", "reason": "v2 keeps SQL out."}
    r07 = {**R21, "id": "MIK-R07", "packet": "requirements/MIK-R07-v1-history.md"}
    # N1: links[3] re-authored to MIK-R07@v1 after the worklist fired on MIK-R21@v1.
    before = _reauthor_link(world, 3, target=r07)
    refused = _write(world, [row], None, worklist=worklist)
    assert refused.state == "refused", refused.render()
    assert "links.3: the link was re-authored" in refused.render()
    assert (world.memory / D18_PATH).read_bytes() == before
    # R3: a link that only looks refreshed (the approved version and packet, but another endpoint
    # or another packet) is not the refresh of the fired target, so it stays refused too.
    v2 = {"version": "v2", "packet": "requirements/MIK-R21-v2-formats.md"}
    lookalikes = (
        {**R21, **v2, "id": "MIK-R07"},
        {**R21, **v2, "packet": "requirements/x.md"},
        {**R21, "packet": "requirements/x.md"},  # the fired key (MIK-R21@v1), another packet
    )
    for lookalike in lookalikes:
        before = _reauthor_link(world, 3, target=lookalike)
        again = _write(world, [row], None, worklist=worklist)
        assert again.state == "refused" and "links.3: the link was re-authored" in again.render()
        assert (world.memory / D18_PATH).read_bytes() == before
    # N2: the fired link, unchanged, moved to another alternative: refused as well.
    _reauthor_link(world, 3, target=R21, alternative=1)
    moved = _write(world, [row], None, worklist=worklist)
    assert moved.state == "refused", moved.render()
    assert "no longer a reconsider_on link of alternative 2" in moved.render()


# --------------------------------------------------------------------------------------------------
# Review R3 ruling of 2026-09-30T07:13:57 (R3-1/N5; reconciled 09:18:48): a rerun after a written
# refresh is accepted and changes nothing
# --------------------------------------------------------------------------------------------------


RERUN_ROW = {"subject": S18B, "disposition": "still_rejected", "reason": "Still no SQL."}


def _write_twice(world: World, worklist: dict[str, Any]) -> tuple[Any, Any]:
    return _write(world, [RERUN_ROW], None, worklist=worklist), _write(
        world, [RERUN_ROW], None, worklist=worklist
    )


def _revision(world: World) -> int:
    return json.loads((world.memory / D18_PATH).read_text(encoding="utf-8"))["revision"]


def _assert_refreshed_once(world: World, worklist: dict[str, Any]) -> None:
    """The same row written twice: both written, the decision placed (and bumped) only once."""

    first, second = _write_twice(world, worklist)
    assert (first.state, second.state) == ("written", "written"), second.render()
    assert [one.id for one in first.records] == [D18]
    assert second.records == ()
    assert _revision(world) == 2


def _assert_rerun_after_recompute(
    world: World, worklist: dict[str, Any], recomputed: dict[str, Any]
) -> None:
    """Recomputed from the unchanged base, the item is the same and one more run changes nothing."""

    assert _candidates(recomputed)[S18B]["id"] == _candidates(worklist)[S18B]["id"]
    links = _d18_links(world)
    third = _write(world, [RERUN_ROW], None, worklist=recomputed)
    assert third.state == "written", third.render()
    assert third.records == ()
    assert _revision(world) == 2
    assert _d18_links(world) == links


def test_a_rerun_after_a_requirement_refresh_is_written_and_bumps_nothing(
    world: World, tmp_path: Path
) -> None:
    coordination = _v2_approved(tmp_path)
    worklist = world.worklist(world.code_base, coordination_root=coordination)
    _assert_refreshed_once(world, worklist)
    assert _d18_links(world)[3]["target"]["version"] == "v2"
    world.memory_commit({}, world.code_base)
    recomputed = world.worklist(
        world.code_base, memory_base=world.memory_base, coordination_root=coordination
    )
    _assert_rerun_after_recompute(world, worklist, recomputed)
    # With the refresh committed, the base no longer holds the fired link; a link re-authored now
    # is still refused, and the committed refresh is left as it is.
    before = _reauthor_link(world, 3, target={**_d18_links(world)[3]["target"], "id": "MIK-R07"})
    refused = _write(world, [RERUN_ROW], None, worklist=recomputed)
    assert refused.state == "refused" and "links.3: the link was re-authored" in refused.render()
    assert (world.memory / D18_PATH).read_bytes() == before


def test_a_rerun_after_an_anchor_refresh_is_written_and_bumps_nothing(world: World) -> None:
    shifted = world.code_commit({REVIEW: SHIFTED.replace("value * 2", "value * 9")})
    worklist = world.worklist(shifted)
    assert _triggers(_candidates(worklist)[S18B])[0][1] == "anchor"
    _assert_refreshed_once(world, worklist)
    refreshed = _d18_links(world)[2]["target"]
    assert refreshed["locator"] == {"kind": "symbol", "name": "third"}
    assert refreshed["blob"] == git(world.code, "rev-parse", f"{shifted}:{REVIEW}")
    world.memory_commit({}, shifted)
    _assert_rerun_after_recompute(world, worklist, world.worklist(shifted))


# --------------------------------------------------------------------------------------------------
# Review R4 rulings of 2026-09-30T10:05:18: a rerun after more code changes (R4-1), committed
# lookalikes and re-authors (R4-2), a version approved after the refresh (R4-3)
# --------------------------------------------------------------------------------------------------


def test_a_rerun_after_more_code_changes_carries_the_refreshed_anchor(world: World) -> None:
    first = world.code_commit({REVIEW: EDITED_THIRD})
    worklist = world.worklist(first)
    written = _write(world, [RERUN_ROW], None, worklist=worklist)
    assert written.state == "written", written.render()
    assert _revision(world) == 2
    at_first = _d18_links(world)[2]["target"]
    # C2: the leaf edits the same file again, outside ``third``, and reruns the same list with the
    # old worklist and with one recomputed at C2.
    second = world.code_commit({REVIEW: EDITED_THIRD.replace("    return 4\n", "    return 44\n")})
    for answered in (worklist, world.worklist(second)):
        rerun = _write(world, [RERUN_ROW], None, worklist=answered)
        assert rerun.state == "written", rerun.render()
        assert rerun.records == ()
        assert _revision(world) == 2
    carried = _d18_links(world)[2]["target"]
    assert carried["blob"] == git(world.code, "rev-parse", f"{second}:{REVIEW}")
    assert (carried["locator"], carried["content"]) == (at_first["locator"], at_first["content"])


def test_a_committed_lookalike_or_re_author_stays_refused(world: World, tmp_path: Path) -> None:
    coordination = _v2_approved(tmp_path)
    worklist = world.worklist(world.code_base, coordination_root=coordination)
    assert _write(world, [RERUN_ROW], None, worklist=worklist).state == "written"
    # The refresh to v2 is committed, so the base no longer holds the link the item fired on.
    world.memory_commit({}, world.code_base)
    # A packet-only lookalike at v2: refused, the decision untouched.
    lookalike = {**_d18_links(world)[3]["target"], "packet": "requirements/x.md"}
    before = _reauthor_link(world, 3, target=lookalike)
    refused = _write(world, [RERUN_ROW], None, worklist=worklist)
    assert refused.state == "refused" and "links.3: the link was re-authored" in refused.render()
    assert (world.memory / D18_PATH).read_bytes() == before
    # A re-author to MIK-R07@v1, committed: refused, the decision byte-identical.
    r07 = {**R21, "id": "MIK-R07", "packet": "requirements/MIK-R07-v1-history.md"}
    _reauthor_link(world, 3, target=r07)
    world.memory_commit({}, world.code_base)
    before = (world.memory / D18_PATH).read_bytes()
    refused = _write(world, [RERUN_ROW], None, worklist=worklist)
    assert refused.state == "refused" and "links.3: the link was re-authored" in refused.render()
    assert (world.memory / D18_PATH).read_bytes() == before


def test_a_link_reverted_after_a_committed_refresh_is_refreshed_again(
    world: World, tmp_path: Path
) -> None:
    coordination = _v2_approved(tmp_path)
    worklist = world.worklist(world.code_base, coordination_root=coordination)
    assert _write(world, [RERUN_ROW], None, worklist=worklist).state == "written"
    world.memory_commit({}, world.code_base)
    # The curator puts the fired target (MIK-R21@v1) back: it is the K_B target, known by its key
    # now that the base holds the refresh, so it is refreshed again, with no further bump.
    _reauthor_link(world, 3, target=R21)
    again = _write(world, [RERUN_ROW], None, worklist=worklist)
    assert again.state == "written", again.render()
    assert _d18_links(world)[3]["target"]["version"] == "v2"
    assert _revision(world) == 2


def _approve_v3(world: World, coordination: Path) -> dict[str, Any]:
    """v3 is approved after the refresh to v2; the worklist is recomputed."""

    _manifest(
        coordination / "tasks" / TASK["repository"] / TASK["path"],
        ("MIK-R21", "v2", "approved", "MIK-R21-v2-formats.md"),
        ("MIK-R21", "v3", "approved", "MIK-R21-v3-formats.md"),
    )
    return world.worklist(world.code_base, coordination_root=coordination)


def _assert_names_both_versions(refused: Any, item: dict[str, Any]) -> None:
    text = refused.render()
    assert refused.state == "refused", text
    assert "links.3: this link was refreshed to v2 earlier in the leaf, and v3 has been" in text
    assert f"answer the new item by naming {item['id']} in the row's items" in text
    assert "re-authored" not in text


def test_a_version_approved_after_the_refresh_is_named_and_needs_the_new_item(
    world: World, tmp_path: Path
) -> None:
    coordination = _v2_approved(tmp_path)
    worklist = world.worklist(world.code_base, coordination_root=coordination)
    assert _write(world, [RERUN_ROW], None, worklist=worklist).state == "written"
    recomputed = _approve_v3(world, coordination)
    item = _candidates(recomputed)[S18B]
    assert item["id"] != _candidates(worklist)[S18B]["id"]
    before = (world.memory / D18_PATH).read_bytes()
    _assert_names_both_versions(_write(world, [RERUN_ROW], None, worklist=recomputed), item)
    assert (world.memory / D18_PATH).read_bytes() == before
    # Answering the new item by name re-points the link to v3; the revision stays bumped once.
    answered = _write(world, [{**RERUN_ROW, "items": [item["id"]]}], None, worklist=recomputed)
    assert answered.state == "written", answered.render()
    assert _d18_links(world)[3]["target"]["version"] == "v3"
    assert _revision(world) == 2


# --------------------------------------------------------------------------------------------------
# Review R5 rulings of 2026-09-30T11:01:18: a second change inside the anchored range needs a new
# answer (R5-1); a curator-set unapproved version is re-authored (R5-2)
# --------------------------------------------------------------------------------------------------

EDITED_AGAIN = EDITED_THIRD.replace("value * 5", "value * 7")


def _assert_refused(report: Any, *expected: str) -> None:
    text = report.render()
    assert report.state == "refused", text
    for one in expected:
        assert one in text, (one, text)


def test_a_second_change_inside_the_anchored_range_needs_the_new_item(world: World) -> None:
    first = world.code_commit({REVIEW: EDITED_THIRD})
    first_worklist = world.worklist(first)
    assert _write(world, [RERUN_ROW], None, worklist=first_worklist).state == "written"
    at_first = _d18_links(world)[2]["target"]
    # C2 edits ``third`` again. The recomputed item is a new one; a plain rerun is refused naming
    # the change and the new item, and a rerun with the old worklist must recompute first.
    again = world.code_commit({REVIEW: EDITED_AGAIN})
    worklist = world.worklist(again)
    item = _candidates(worklist)[S18B]
    assert item["id"] != _candidates(first_worklist)[S18B]["id"]
    before = (world.memory / D18_PATH).read_bytes()
    _assert_refused(
        _write(world, [RERUN_ROW], None, worklist=worklist),
        "links.2: the code this link anchors changed again after it was refreshed",
        f"answer the new item by naming {item['id']} in the row's items",
    )
    old = _candidates(first_worklist)[S18B]["id"]
    for row in (RERUN_ROW, {**RERUN_ROW, "items": [old]}):
        _assert_refused(
            _write(world, [row], None, worklist=first_worklist),
            "links.2: the code this link anchors changed after the worklist was computed",
        )
    assert (world.memory / D18_PATH).read_bytes() == before
    # A row naming the new item is a new judgment: the link takes C2's content, bumped once.
    answered = _write(world, [{**RERUN_ROW, "items": [item["id"]]}], None, worklist=worklist)
    assert answered.state == "written", answered.render()
    refreshed = _d18_links(world)[2]["target"]
    assert refreshed["content"] != at_first["content"]
    assert refreshed["blob"] == git(world.code, "rev-parse", f"{again}:{REVIEW}")
    assert _revision(world) == 2
    _next_leaf(world, again)
    assert _candidates(world.worklist(again)) == {}


def test_a_curator_set_unapproved_version_is_re_authored(world: World, tmp_path: Path) -> None:
    coordination = _v2_approved(tmp_path)
    worklist = world.worklist(world.code_base, coordination_root=coordination)
    assert _write(world, [RERUN_ROW], None, worklist=worklist).state == "written"
    recomputed = _approve_v3(world, coordination)
    item = _candidates(recomputed)[S18B]
    v4 = {**_d18_links(world)[3]["target"], "version": "v4", "packet": "requirements/v4.md"}
    before = _reauthor_link(world, 3, target=v4)
    for row in (RERUN_ROW, {**RERUN_ROW, "items": [item["id"]]}):
        refused = _write(world, [row], None, worklist=recomputed)
        _assert_refused(refused, "links.3: the link was re-authored")
        assert "refreshed to v4" not in refused.render()
    assert (world.memory / D18_PATH).read_bytes() == before


def test_an_anchor_link_re_authored_to_other_code_is_refused(world: World) -> None:
    touched = world.code_commit({REVIEW: EDITED_THIRD})
    worklist = world.worklist(touched)
    item = _candidates(worklist)[S18B]
    # Before answering, the link is pointed at ``fourth``: not the fired anchor, nor its mapping.
    fourth = world.anchor(touched, REVIEW, {"kind": "symbol", "name": "fourth"})
    before = _reauthor_link(world, 2, target={**fourth, "path": REVIEW})
    for row in (RERUN_ROW, {**RERUN_ROW, "items": [item["id"]]}):
        _assert_refused(
            _write(world, [row], None, worklist=worklist), "links.2: the link was re-authored"
        )
    assert (world.memory / D18_PATH).read_bytes() == before
