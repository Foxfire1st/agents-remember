"""The one-time conversion of a memory tree to derived states and stamped writers.

Each test writes its own memory tree and task root: records typed ``proposed``, ``accepted`` and
``retired``, the history files of leaves (one reopened), a wave and a crossing, and the task
documents (one archived) whose memory review rounds say which owner passed.
"""

from __future__ import annotations

import json
import random
import re
import zlib
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_writer.revision_stamps import meaning
from agents_remember.cli import knowledge_convert_states
from agents_remember.cli.__main__ import main
from agents_remember.errors import StateConversionError
from agents_remember.kernel import atomic_write
from agents_remember.memory.conversion import state_files
from agents_remember.memory.conversion.state_conversion import plan_state_conversion
from agents_remember.memory.conversion.state_conversion_run import (
    StateConversionRequest,
    prepare_state_conversion,
)
from agents_remember.memory.conversion.state_files import read_knowledge_json
from agents_remember.memory.conversion.task_review_facts import read_task_owners
from agents_remember.memory_quality.knowledge_validator.parsed import parse_tree
from agents_remember.memory_quality.knowledge_validator.trees import KnowledgeTree
from agents_remember.models.knowledge_files import canonical_text, parse_history_document
from agents_remember.models.knowledge_files.converted_reviews import (
    CONVERTED_REVIEWS_PATH,
    ConvertedReviews,
)
from agents_remember.models.knowledge_files.documents import parse_document_text
from agents_remember.models.knowledge_files.history import frozen_history_violation
from agents_remember.models.knowledge_files.record_state import RevisionStates
from agents_remember.models.knowledge_files.records import record_status
from agents_remember.tasks import TaskDocument
from agents_remember.tasks.route_review import passed_memory_round
from pydantic import ValidationError

AT = "2026-11-19T21:00:00+01:00"
TASK, L1, L2, L3, WAVE = (
    "260101-MAS",
    "260101-MAS-L1",
    "260101-MAS-L2",
    "260101-MAS-L3",
    "260101-W1",
)
CROSSING, OLD = "260101-MAS-crossing-1", "250101-OLD-L1"
PATHS = {
    "made_by_l1": "knowledge/invariants/INV-A00001-made.json",
    "changed_by_l2": "knowledge/invariants/INV-A00002-reopened.json",
    "changed_by_l3": "knowledge/invariants/INV-A00003-unpassed.json",
    "no_row": "knowledge/invariants/INV-A00004-nobody.json",
    "crossing": "knowledge/invariants/INV-A00005-crossed.json",
    "wave": "knowledge/invariants/INV-A00006-waved.json",
    "retired": "knowledge/invariants/INV-A00007-retired.json",
    "old": "knowledge/invariants/INV-A00008-old.json",
    "accepted": "knowledge/assumptions/ASM-A00001-accepted.json",
}


def _invariant(record_id: str, revision: int, origin: dict[str, str], status: str) -> dict:
    return {
        "schema": "ar-invariant/v1",
        "id": record_id,
        "revision": revision,
        "status": status,
        "statement": f"{record_id} holds.",
        "applicability": "Always.",
        "conditions": ["The value is set."],
        "exclusions": [],
        "supersedes": [],
        "admission": "legacy-unassessed",
        "origin": origin,
    }


def _changed_row(record_id: str, revision: int) -> dict[str, Any]:
    return {
        "id": f"ROW-{record_id[4:]}",
        "subject": record_id,
        "disposition": "changed",
        "reason": "The meaning changed.",
        "items": [],
        "covers": [],
        "revision": revision,
        "effect": "clarify",
    }


def _history(owner: tuple[str, str], rows: list[dict], attempt: int = 1) -> dict[str, Any]:
    document: dict[str, Any] = {"schema": "ar-history/v1", owner[0]: owner[1], "closed": True}
    document["rows"] = rows
    if attempt > 1:
        document["attempt"] = attempt
    return document


def _task_document(
    doc_id: str,
    kind: str,
    rounds: list[tuple[int, bool]],
    *,
    reopened: bool = False,
    verdicts: bool = True,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": "ar-task-document/v1",
        "id": doc_id,
        "slug": doc_id.lower(),
        "title": doc_id,
        "kind": kind,
        "repo": "repo",
        "createdAt": "2026-01-01",
    }
    if rounds:
        document["memoryReviewState"] = {
            "round": max(number for number, _ in rounds),
            "candidateTree": "a" * 40,
            "recordedRounds": [
                {
                    "round": n,
                    "kind": "memory",
                    "findingsRemain": not ok,
                    **({"verdictRef": f"v{n}.md"} if verdicts else {}),
                }
                for n, ok in rounds
            ],
        }
    if reopened:
        reopening = f"Leaf {doc_id} reopened under its original id."
        document["decisions"] = [
            {"at": "2026-01-02T10:00", "decision": reopening, "rationale": "."}
        ]
    return document


def _write(root: Path, relative: str, document: dict[str, Any]) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(canonical_text(document), encoding="utf-8")


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    memory, tasks = tmp_path / "memory", tmp_path / "tasks"
    by_l1 = {"task": TASK, "leaf": L1}
    records = {
        "made_by_l1": _invariant("INV-A00001", 1, by_l1, "proposed"),
        "changed_by_l2": _invariant("INV-A00002", 2, by_l1, "proposed"),
        "changed_by_l3": _invariant("INV-A00003", 2, by_l1, "proposed"),
        "no_row": _invariant("INV-A00004", 2, by_l1, "proposed"),
        "crossing": _invariant("INV-A00005", 3, by_l1, "proposed"),
        "wave": _invariant("INV-A00006", 1, {"task": TASK, "wave": WAVE}, "proposed"),
        "retired": _invariant("INV-A00007", 1, by_l1, "retired"),
        "old": _invariant("INV-A00008", 1, {"task": "250101-OLD", "leaf": OLD}, "accepted"),
        "accepted": {
            "schema": "ar-assumption/v1",
            "id": "ASM-A00001",
            "revision": 1,
            "status": "accepted",
            "proposition": "The host is reachable.",
            "basis": "Observed.",
            "links": [],
            "origin": by_l1,
        },
    }
    for name, document in records.items():
        _write(memory, PATHS[name], document)
    _write(memory, "knowledge/layout.json", {"schema": "ar-memory-layout/v2", "conversion": "1"})
    histories = {
        L1: _history(("leaf", L1), []),
        L2: _history(("leaf", L2), []),
        f"{L2}-attempt-2": _history(("leaf", L2), [_changed_row("INV-A00002", 2)], attempt=2),
        L3: _history(("leaf", L3), [_changed_row("INV-A00003", 2)]),
        WAVE: _history(("wave", WAVE), []),
        CROSSING: _history(("crossing", CROSSING), [_changed_row("INV-A00005", 3)]),
        OLD: _history(("leaf", OLD), []),
    }
    for name, document in histories.items():
        _write(memory, f"knowledge/history/{name}.json", document)
    _write(tasks, "260101_master/task.json", _task_document(TASK, "master", []))
    for number, (leaf, rounds) in enumerate(
        ((L1, [(1, False), (2, True)]), (L2, [(1, True)]), (L3, [(1, False)])), start=1
    ):
        document = _task_document(leaf, "subTask", rounds, reopened=leaf == L2)
        _write(tasks, f"260101_master/0{number}_leaf.json", document)
    _write(tasks, "260101_wave/task.json", _task_document(WAVE, "light", [(1, True)]))
    _write(tasks, "0_archive/250101_old/task.json", _task_document("250101-OLD", "master", []))
    _write(tasks, "0_archive/250101_old/01_leaf.json", _task_document(OLD, "subTask", [(3, True)]))
    return memory, tasks


def _convert(memory: Path, tasks: Path) -> Any:
    run = prepare_state_conversion(StateConversionRequest(memory, tasks, apply=True), at=AT)
    run.apply()
    return run


def _read(memory: Path, name: str) -> dict[str, Any]:
    return json.loads((memory / PATHS[name]).read_text(encoding="utf-8"))


def _states(memory: Path) -> dict[str, str | None]:
    files = read_knowledge_json(memory)
    histories = [
        parse_history_document(path, data.decode())
        for path, data in files.items()
        if path.startswith("knowledge/history/") and path != CONVERTED_REVIEWS_PATH
    ]
    converted = parse_document_text(files[CONVERTED_REVIEWS_PATH].decode())
    assert isinstance(converted, ConvertedReviews)
    states = RevisionStates.of(histories, converted)
    result = {}
    for name, path in PATHS.items():
        record: Any = parse_document_text(files[path].decode())
        retired = record_status(record) == "retired"
        result[name] = states.state_of(
            record.id, record.revision, retired=retired, writer=record.writer
        )
    return result


def test_the_typed_words_leave_and_retired_stays(roots: tuple[Path, Path]) -> None:
    memory, tasks = roots
    _convert(memory, tasks)
    assert "status" not in _read(memory, "made_by_l1")
    assert "status" not in _read(memory, "accepted")
    assert _read(memory, "retired")["status"] == "retired"


def test_passed_reviews_become_facts_and_revisions_read_their_state(
    roots: tuple[Path, Path],
) -> None:
    memory, tasks = roots
    run = _convert(memory, tasks)
    facts = ConvertedReviews.model_validate_json((memory / CONVERTED_REVIEWS_PATH).read_bytes())
    by_owner = {(one.owner, one.attempt_number): one.fact for one in facts.reviews}
    assert set(by_owner) == {(L1, 1), (WAVE, 1), (OLD, 1)}
    assert all(fact.verdict_ref is not None for fact in by_owner.values())
    assert by_owner[(L1, 1)].round == 2 and by_owner[(L1, 1)].tree == "a" * 40
    assert by_owner[(L1, 1)].reviewer.unknown is not None
    assert by_owner[(L1, 1)].reviewer.by == "conversion"
    assert _states(memory) == {
        "made_by_l1": "reviewed",
        "changed_by_l2": "proposed",
        "changed_by_l3": "proposed",
        "no_row": "proposed",
        "crossing": "proposed",
        "wave": "reviewed",
        "retired": None,
        "old": "reviewed",
        "accepted": "reviewed",
    }
    counts = run.plan.counts.to_document()
    assert (counts["reviewed"], counts["proposed"], counts["noLeafFound"]) == (4, 4, 1)
    assert counts["retiredNoState"] == 1
    assert set(run.plan.owners_without_fact) == {L2}


def test_a_reopened_leaf_closed_without_a_fresh_pass_gets_no_fact(
    roots: tuple[Path, Path],
) -> None:
    # L2 passed round 1, was reopened, and its second attempt was closed with a changed row but
    # no further passed round. One round sequence serves both attempts, so the pass is proven
    # for neither: the later attempt must not inherit it, and the report says why.
    memory, tasks = roots
    run = _convert(memory, tasks)
    facts = ConvertedReviews.model_validate_json((memory / CONVERTED_REVIEWS_PATH).read_bytes())
    assert {one.owner for one in facts.reviews}.isdisjoint({L2})
    assert _states(memory)["changed_by_l2"] == "proposed"
    reason = run.plan.owners_without_fact[L2]
    assert "reopened" in reason and "round 1" in reason
    assert run.report_document()["ownersWithoutReviewFact"][L2] == reason


def test_a_passed_round_without_a_verdict_reference_gives_no_fact(
    roots: tuple[Path, Path],
) -> None:
    memory, tasks = roots
    _write(
        tasks, "260101_wave/task.json", _task_document(WAVE, "light", [(1, True)], verdicts=False)
    )
    run = _convert(memory, tasks)
    facts = ConvertedReviews.model_validate_json((memory / CONVERTED_REVIEWS_PATH).read_bytes())
    assert WAVE not in {one.owner for one in facts.reviews}
    assert _states(memory)["wave"] == "proposed"
    assert "verdict reference" in run.plan.owners_without_fact[WAVE]


@pytest.mark.parametrize(
    "rounds",
    [
        [{"round": 1, "verdict": "block", "findingsRemain": False}],
        [
            {"round": 1, "verdict": "pass", "findingsRemain": False},
            {"round": 2, "verdict": "block", "findingsRemain": True},
        ],
        [
            {"round": 1, "verdict": "pass", "findingsRemain": False},
            {"round": 1, "verdict": "block", "findingsRemain": True},
        ],
        [
            {"round": 1, "verdict": "block", "findingsRemain": True},
            {"round": 1, "verdict": "pass", "findingsRemain": False},
        ],
    ],
    ids=[
        "explicit-block-naming-no-finding",
        "pass-then-block",
        "tied-pass-first",
        "tied-block-first",
    ],
)
def test_a_last_memory_round_that_did_not_pass_gives_no_fact(
    roots: tuple[Path, Path], rounds: list[dict[str, Any]]
) -> None:
    """Only a proven pass is a fact: a stored block never is, also when it names no finding.

    Catches a conversion that reads ``findingsRemain`` alone, or keeps an earlier pass that a later
    round took back.
    """

    memory, tasks = roots
    document = _task_document(WAVE, "light", [(1, True)])
    document["memoryReviewState"]["round"] = max(one["round"] for one in rounds)
    document["memoryReviewState"]["recordedRounds"] = [
        {"kind": "memory", "verdictRef": f"v{one['round']}.md", **one} for one in rounds
    ]
    _write(tasks, "260101_wave/task.json", document)
    run = _convert(memory, tasks)
    facts = ConvertedReviews.model_validate_json((memory / CONVERTED_REVIEWS_PATH).read_bytes())
    assert WAVE not in {one.owner for one in facts.reviews}
    assert _states(memory)["wave"] == "proposed"
    if len(rounds) == 2 and rounds[0]["round"] == rounds[1]["round"]:
        assert "conflicting facts" in run.plan.owners_without_fact[WAVE]


_ABSENT = object()


@pytest.mark.parametrize(
    ("field", "value", "said"),
    [
        ("verdictRef", _ABSENT, "verdict reference"),
        ("verdictRef", None, "verdict reference"),
        ("verdictRef", "", "verdict reference"),
        ("verdictRef", "  ", "verdict reference"),
        ("verdictRef", 5, "recordedRounds.0.verdictRef"),
        ("verdictRef", ["v1.md"], "recordedRounds.0.verdictRef"),
        ("round", "one", "recordedRounds.0.round"),
        ("round", 0, "recordedRounds.0.round"),
        ("round", _ABSENT, "recordedRounds.0.round"),
        ("kind", "memories", "recordedRounds.0.kind"),
        ("kind", _ABSENT, "recordedRounds.0.kind"),
        ("verdict", "passed", "recordedRounds.0.verdict"),
        ("verdict", 1, "recordedRounds.0.verdict"),
        ("findingsRemain", "maybe", "recordedRounds.0.findingsRemain"),
        ("memoryTree", "not-a-tree", "recordedRounds.0.memoryTree"),
        ("memoryTree", 5, "recordedRounds.0.memoryTree"),
        ("candidateTree", "not-a-tree", "candidateTree"),
    ],
)
def test_every_stored_form_of_an_unproven_pass_is_reported_and_the_run_goes_on(
    roots: tuple[Path, Path], field: str, value: object, said: str
) -> None:
    """A fact only from a proven pass with a usable verdict reference: any other stored value of
    the round is an unproven pass, reported with its reason, and the valid owners still convert.

    Catches a blank reference taken as one, and a stored value that stops the whole conversion.
    The reference is a statement and is not resolved to a file, as the closeout reads it.
    """

    memory, tasks = roots
    document = _task_document(WAVE, "light", [(1, True)])
    state = document["memoryReviewState"]
    target = state if field == "candidateTree" else state["recordedRounds"][0]
    target["verdict"] = "pass"
    if value is _ABSENT:
        target.pop(field, None)
    else:
        target[field] = value
    _write(tasks, "260101_wave/task.json", document)

    run = _convert(memory, tasks)

    facts = ConvertedReviews.model_validate_json((memory / CONVERTED_REVIEWS_PATH).read_bytes())
    owners = {one.owner for one in facts.reviews}
    assert WAVE not in owners
    assert {L1, OLD} <= owners  # the valid owners are converted all the same
    assert said in run.plan.owners_without_fact[WAVE]
    assert (
        run.report_document()["ownersWithoutReviewFact"][WAVE]
        == (run.plan.owners_without_fact[WAVE])
    )
    assert _states(memory)["wave"] == "proposed"


def _generated_values(seed: int, count: int) -> list[object]:
    """Bad and odd stored values: strings of every length class with padding, controls and
    non-ASCII text, numbers, booleans, nulls, and nested junk, then seeded random mixtures."""

    texts = ["", " ", "\t\n", "v1.md", "  v1.md  ", "\x00", "a\nb", "ü" * 600, "../../x", "unknown"]
    texts += ["\u200b", "\ufeffv1.md", "v1.md\x7f", "\u00a0", "v\u200b1.md", "\u2028"]
    texts += ["x" * n for n in (1023, 1024, 1025, 5000)] + [" " + "x" * 1024, "x" * 1024 + " "]
    plain: list[object] = [*texts, None, 0, -1, 1, 2**40, 1.5, True, False, [], {}, ["v1.md"]]
    plain += [[[[[[[[[[[[]]]]]]]]]]]]
    plain += [{"a": 1}, [[None]], {"x": {"y": []}}, "f" * 40, "F" * 40, "f" * 39]
    chooser = random.Random(seed)

    def mixed(depth: int) -> object:
        pick = chooser.choice(plain)
        if depth and chooser.random() < 0.3:
            return [mixed(depth - 1) for _ in range(chooser.randint(0, 3))]
        if depth and chooser.random() < 0.3:
            return {chooser.choice(texts[:5]): mixed(depth - 1)}
        if isinstance(pick, str) and chooser.random() < 0.5:
            pad = " " * chooser.randint(0, 3)
            return pad + pick * chooser.randint(1, 3) + pad
        return pick

    return plain + [mixed(3) for _ in range(count)]


def _records_a_pass(document: dict[str, Any]) -> bool:
    """Whether the stored review state, where it parses, ends in a passed memory round; one
    that does not parse may hold one, so it must be reported."""

    try:
        state = TaskDocument.model_validate(document).memoryReviewState
    except ValidationError:
        return True
    rounds = [one for one in state.recordedRounds if one.kind == "memory"] if state else []
    return bool(rounds) and passed_memory_round(max(rounds, key=lambda one: one.round))


_ROUND_FIELDS = (
    "verdictRef",
    "round",
    "kind",
    "verdict",
    "findingsRemain",
    "memoryTree",
    "recordedBy",
    "handedOverBy",
)
_STATE_FIELDS = ("candidateTree", "round", "pending", "recordedRounds", "limit")


def test_one_owner_with_any_generated_stored_value_never_stops_the_conversion_of_the_others(
    roots: tuple[Path, Path],
) -> None:
    """Every field of a passed memory round and of its review state, given generated bad values:
    the conversion plans without stopping, still converts the valid owners, and either converts
    the bad owner's fact (with a usable, trimmed reference) or reports it with a reason.

    Catches any stored value that stops the whole conversion, and a fact taken from a reference
    the fact cannot hold.
    """

    memory, tasks = roots
    files = read_knowledge_json(memory)
    targets = [("round", name) for name in _ROUND_FIELDS] + [("state", n) for n in _STATE_FIELDS]
    targets += [("document", name) for name in _DOCUMENT_FIELDS]
    checked = 0
    for place, name in targets:
        for value in _generated_values(seed=zlib.crc32(name.encode()), count=40):
            document = _task_document(WAVE, "light", [(1, True)])
            state = document["memoryReviewState"]
            state["recordedRounds"][0]["verdict"] = "pass"
            if place == "document":
                document[name] = value
            else:
                (state if place == "state" else state["recordedRounds"][0])[name] = value
            _write_text(tasks, "260101_wave/task.json", document)
            plan = plan_state_conversion(files, read_task_owners(tasks), at=AT)
            facts = ConvertedReviews.model_validate_json(plan.changes[CONVERTED_REVIEWS_PATH])
            owners = {one.owner: one.fact for one in facts.reviews}
            assert {L1, OLD} <= set(owners), (name, value)
            assert not (WAVE in owners and WAVE in plan.owners_without_fact), (name, value)
            if not _records_a_pass(document):
                assert WAVE not in owners and WAVE not in plan.owners_without_fact, (name, value)
            elif WAVE in owners:
                assert _usable(owners[WAVE].verdict_ref), (name, value)
            else:
                assert plan.owners_without_fact.get(WAVE, "").strip(), (name, value)
            checked += 1
    assert checked > 900


_DOCUMENT_FIELDS = ("memoryReviewState", "kind", "status", "decisions", "title", "reviewState")


def _usable(reference: str | None) -> bool:
    """A reference a reader can open: printable, trimmed, not empty."""

    return bool(reference) and reference == reference.strip() and reference.isprintable()


def _write_text(root: Path, relative: str, document: dict[str, Any]) -> None:
    """Written as JSON text whatever the values are; the canonical writer refuses some."""

    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def test_a_task_id_no_stamp_can_hold_reads_unknown_and_the_others_convert(
    roots: tuple[Path, Path],
) -> None:
    """The task of the leaves of a task folder whose id no stamp can hold: unknown, with why."""

    memory, tasks = roots
    for value in _generated_values(seed=zlib.crc32(b"task id"), count=40):
        if not isinstance(value, str):
            continue
        _write_text(tasks, "260101_master/task.json", _task_document(value, "master", []))
        plan = plan_state_conversion(read_knowledge_json(memory), read_task_owners(tasks), at=AT)
        facts = ConvertedReviews.model_validate_json(plan.changes[CONVERTED_REVIEWS_PATH])
        stamp = {one.owner: one.fact for one in facts.reviews}[L1].reviewer
        assert {one.owner for one in facts.reviews} == {L1, WAVE, OLD}, repr(value)
        assert (value != "unknown" and stamp.task == value) or (
            stamp.task == "unknown" and stamp.unknown and stamp.unknown.get("task")
        ), repr(value)


@pytest.mark.parametrize(
    ("relative", "text"),
    [
        ("knowledge/history/converted-reviews.json", "{not json"),
        ("knowledge/history/converted-reviews.json", '{"schema": "x", "reviews": [{"owner": 5}]}'),
        (f"knowledge/history/{L1}.json", '{"schema": "ar-history/v1", "leaf": 7}\n'),
        (f"knowledge/history/{L1}.json", "]["),
        (PATHS["made_by_l1"], "\xff not text"),
    ],
    ids=["facts-not-json", "facts-refused", "history-refused", "history-not-json", "record-bytes"],
)
def test_a_memory_file_that_does_not_parse_stops_the_run_named_and_changes_nothing(
    roots: tuple[Path, Path], relative: str, text: str
) -> None:
    """Only a document that cannot be parsed may stop the conversion, and it is named."""

    memory, tasks = roots
    target = memory / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("latin-1") if relative.endswith(".json") else text.encode())
    before = _tree_bytes(memory)

    with pytest.raises(StateConversionError, match=re.escape(f"{relative} does not parse")):
        _convert(memory, tasks)
    assert _tree_bytes(memory) == before


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ('{"schema": "ar-task-document/v1", "id": "260101-W1"', "does not parse as JSON"),
        ('{"schema": "ar-task-document/v1", "id": "a", "id": "b"}', "does not parse as JSON"),
        ('{"schema": "ar-task-document/v1", "id": ' + "9" * 5001 + "}", "does not parse as JSON"),
        ("[" * 100_000 + "]" * 100_000, "does not parse as JSON"),
    ],
    ids=["truncated", "duplicate-key", "huge-number", "deep"],
)
def test_a_task_file_that_does_not_parse_stops_the_run_named(
    roots: tuple[Path, Path], text: str, said: str
) -> None:
    memory, tasks = roots
    path = tasks / "260101_wave/task.json"
    path.write_text(text, encoding="utf-8")
    before = _tree_bytes(memory)

    with pytest.raises(StateConversionError, match=re.escape(str(path))) as stopped:
        _convert(memory, tasks)
    assert said in str(stopped.value)
    assert _tree_bytes(memory) == before


def test_a_directory_or_a_dangling_link_named_json_is_no_document(roots: tuple[Path, Path]) -> None:
    memory, tasks = roots
    (tasks / "260101_master/05_leaf.json").mkdir()
    (tasks / "260101_master/06_leaf.json").symlink_to(tasks / "nowhere.json")

    facts = _convert(memory, tasks).plan.changes[CONVERTED_REVIEWS_PATH]
    assert {one.owner for one in ConvertedReviews.model_validate_json(facts).reviews} == {
        L1,
        WAVE,
        OLD,
    }


def test_a_passed_owner_with_a_second_document_that_does_not_parse_is_reported_not_converted(
    roots: tuple[Path, Path],
) -> None:
    """One owner, two documents: one passes, one does not fit the model. Reported, not converted,
    and the others are converted."""

    memory, tasks = roots
    broken = {**_task_document(WAVE, "light", [(1, True)]), "kind": 5}
    _write(tasks, "0_archive/260101_wave/task.json", broken)

    run = _convert(memory, tasks)

    facts = ConvertedReviews.model_validate_json((memory / CONVERTED_REVIEWS_PATH).read_bytes())
    assert {one.owner for one in facts.reviews} == {L1, OLD}
    assert "does not parse" in run.report_document()["ownersWithoutReviewFact"][WAVE]


def test_each_current_revision_is_stamped_with_the_owner_rule_8_finds(
    roots: tuple[Path, Path],
) -> None:
    memory, tasks = roots
    _convert(memory, tasks)
    owners = {
        name: {key: _read(memory, name)["writer"].get(key) for key in ("leaf", "wave", "crossing")}
        for name in PATHS
    }
    assert owners["made_by_l1"]["leaf"] == L1
    assert owners["changed_by_l2"]["leaf"] == L2
    assert owners["changed_by_l3"]["leaf"] == L3
    assert owners["no_row"] == {"leaf": None, "wave": None, "crossing": None}
    assert owners["wave"]["wave"] == WAVE
    assert owners["crossing"]["crossing"] == CROSSING
    stamp = _read(memory, "no_row")["writer"]
    assert (stamp["by"], stamp["operation"], stamp["task"], stamp["at"]) == (
        "conversion",
        "state-conversion",
        TASK,
        AT,
    )
    assert {stamp[key] for key in ("role", "harness", "model", "effort", "seat")} == {"unknown"}
    assert _read(memory, "crossing")["writer"]["task"] == TASK


def test_a_revision_a_history_file_names_keeps_its_recorded_writer(
    roots: tuple[Path, Path],
) -> None:
    """A record without ``writer`` whose revision a ``written`` row names takes that stamp."""

    memory, tasks = roots
    recorded = {
        "by": "agent",
        "role": "curator",
        "harness": "claude",
        "model": "model-c",
        "effort": "low",
        "seat": "seat-l3",
        "task": TASK,
        "leaf": L3,
        "at": "2026-01-02T10:00:00+01:00",
    }
    history = _history(("leaf", L3), [_changed_row("INV-A00003", 2)])
    history["written"] = [{"record": "INV-A00003", "revision": 2, "writer": recorded}]
    _write(memory, f"knowledge/history/{L3}.json", history)
    before = (memory / f"knowledge/history/{L3}.json").read_bytes()
    _convert(memory, tasks)
    assert _read(memory, "changed_by_l3")["writer"] == recorded
    assert (memory / f"knowledge/history/{L3}.json").read_bytes() == before
    assert _read(memory, "no_row")["writer"]["by"] == "conversion"


def test_a_record_typed_accepted_loses_the_word_and_reads_a_derived_state(
    roots: tuple[Path, Path],
) -> None:
    memory, tasks = roots
    before = _read(memory, "accepted")
    run = _convert(memory, tasks)
    converted = _read(memory, "accepted")
    assert "accepted" not in json.dumps(converted)
    assert set(converted) == (set(before) - {"status"}) | {"writer"}
    assert {key: converted[key] for key in before if key != "status"} == {
        key: value for key, value in before.items() if key != "status"
    }
    assert _states(memory)["accepted"] == "reviewed"  # its leaf L1 passed, like INV-A00001
    assert run.plan.counts.typed_accepted == 2


def test_no_meaning_changes_and_frozen_history_stays_byte_identical(
    roots: tuple[Path, Path],
) -> None:
    memory, tasks = roots
    before = read_knowledge_json(memory)
    _convert(memory, tasks)
    after = read_knowledge_json(memory)
    for path in PATHS.values():
        old, new = json.loads(before[path]), json.loads(after[path])
        assert meaning(old) == meaning(new)
        for field in ("statement", "conditions", "links", "proposition"):
            assert old.get(field) == new.get(field)
        assert set(new) - set(old) <= {"writer"}
        assert set(old) - set(new) <= {"status"}
    histories = [path for path in before if path.startswith("knowledge/history/")]
    assert histories
    assert not any(frozen_history_violation([before[path]], after.get(path)) for path in histories)
    parsed = parse_tree(KnowledgeTree(label="converted", files=after))
    assert [problem for problem in parsed.problems if problem.path == CONVERTED_REVIEWS_PATH] == []
    assert len(parsed.histories) == len(histories)


def test_a_second_run_changes_nothing(roots: tuple[Path, Path]) -> None:
    memory, tasks = roots
    _convert(memory, tasks)
    after_first = read_knowledge_json(memory)
    second = plan_state_conversion(after_first, read_task_owners(tasks), at="2026-11-20T09:00:00Z")
    assert second.changes == {}
    assert second.counts.reviewed == 4


def _command_arguments(memory: Path, tasks: Path) -> list[str]:
    return ["knowledge-convert-states", "--memory-root", str(memory), "--task-root", str(tasks)]


def test_the_command_reports_without_writing_and_writes_with_apply(
    roots: tuple[Path, Path], tmp_path: Path
) -> None:
    memory, tasks = roots
    before = read_knowledge_json(memory)
    report = tmp_path / "report.json"
    arguments = _command_arguments(memory, tasks)
    assert main([*arguments, "--report", str(report)]) == 0
    assert read_knowledge_json(memory) == before
    planned = json.loads(report.read_text(encoding="utf-8"))
    assert planned["applied"] is False
    assert CONVERTED_REVIEWS_PATH in planned["changedFiles"]
    assert main([*arguments, "--apply"]) == 0
    after = read_knowledge_json(memory)
    changed = sorted(path for path, data in after.items() if data != before.get(path))
    assert changed == planned["changedFiles"]


@pytest.mark.parametrize("reference", ["", "  "], ids=["empty", "whitespace"])
def test_the_command_reports_a_blank_verdict_reference_and_converts_the_rest(
    roots: tuple[Path, Path], tmp_path: Path, reference: str
) -> None:
    """Through the command: exit 0, a report that names the owner, the valid owners converted."""

    memory, tasks = roots
    document = _task_document(WAVE, "light", [(1, True)])
    document["memoryReviewState"]["recordedRounds"][0]["verdictRef"] = reference
    _write(tasks, "260101_wave/task.json", document)
    report = tmp_path / "report.json"
    arguments = _command_arguments(memory, tasks)

    assert main([*arguments, "--apply", "--report", str(report)]) == 0

    answer = json.loads(report.read_text(encoding="utf-8"))
    assert "verdict reference" in answer["ownersWithoutReviewFact"][WAVE]
    facts = ConvertedReviews.model_validate_json((memory / CONVERTED_REVIEWS_PATH).read_bytes())
    assert {one.owner for one in facts.reviews} >= {L1, OLD}
    assert WAVE not in {one.owner for one in facts.reviews}


@pytest.mark.parametrize("owner", ["bad", "bad-\ud800"], ids=["ascii", "escaped-surrogate"])
def test_the_command_publishes_a_report_for_every_readable_task_document(
    roots: tuple[Path, Path], tmp_path: Path, owner: str
) -> None:
    memory, tasks = roots
    folder = tasks / "260101_master"
    malformed = _task_document(owner, "subTask", [(1, True)])
    malformed["memoryReviewState"]["recordedRounds"][0]["findingsRemain"] = "invalid-value"
    (folder / "bad.json").write_text(json.dumps(malformed), encoding="utf-8")
    ownerless = folder / "ownerless.json"
    ownerless.write_text(json.dumps({**malformed, "id": 7}), encoding="utf-8")
    report = tmp_path / "report.json"
    arguments = [*_command_arguments(memory, tasks), "--report", str(report)]
    before = _tree_bytes(memory)

    assert main(arguments) == 0
    assert _tree_bytes(memory) == before
    planned = json.loads(report.read_text(encoding="utf-8"))
    assert "findingsRemain" in planned["ownersWithoutReviewFact"][owner]
    assert planned["documentsWithoutOwner"][str(ownerless)] == "the task document names no text id"
    assert main([*arguments, "--apply"]) == 0
    applied = json.loads(report.read_text(encoding="utf-8"))
    assert applied["applied"] is True
    assert applied["ownersWithoutReviewFact"] == planned["ownersWithoutReviewFact"]
    assert {
        one.owner
        for one in ConvertedReviews.model_validate_json(
            (memory / CONVERTED_REVIEWS_PATH).read_bytes()
        ).reviews
    } == {L1, WAVE, OLD}


@pytest.mark.parametrize("is_knowledge_path", [False, True], ids=["directory", "fresh-record-path"])
def test_an_unwritable_report_leaves_the_memory_unchanged(
    roots: tuple[Path, Path], tmp_path: Path, is_knowledge_path: bool
) -> None:
    memory, tasks = roots
    destination = (
        memory / "knowledge/invariants/new.json" if is_knowledge_path else tmp_path / "dir"
    )
    if not is_knowledge_path:
        destination.mkdir()
    before = _tree_bytes(memory)
    assert main([*_command_arguments(memory, tasks), "--report", str(destination), "--apply"]) == 2
    assert _tree_bytes(memory) == before


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _converted_copy(memory: Path, tasks: Path, tmp_path: Path) -> dict[str, bytes]:
    """The tree a conversion without failure makes of ``memory``'s current bytes."""

    reference = tmp_path / "reference"
    for relative, data in _tree_bytes(memory).items():
        (reference / relative).parent.mkdir(parents=True, exist_ok=True)
        (reference / relative).write_bytes(data)
    _convert(reference, tasks)
    return _tree_bytes(reference)


def test_a_failure_after_a_replacement_restores_every_target_and_absence(
    roots: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The second write in path order is the new converted-reviews file. The directory sync that
    # follows its replacement fails, so that file already stands when the error is raised.
    memory, tasks = roots
    before = _tree_bytes(memory)
    assert CONVERTED_REVIEWS_PATH not in before
    expected = _converted_copy(memory, tasks, tmp_path)
    real_sync, calls = atomic_write._fsync_directory, []

    def failing_sync(directory: Path) -> None:
        calls.append(directory)
        if len(calls) == 2:
            raise OSError("injected: directory sync after the replacement failed")
        real_sync(directory)

    monkeypatch.setattr(atomic_write, "_fsync_directory", failing_sync)
    with pytest.raises(Exception) as caught:
        _convert(memory, tasks)
    after = _tree_bytes(memory)
    assert (
        sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
        == []
    )
    assert isinstance(caught.value, StateConversionError) and "restored" in str(caught.value)
    monkeypatch.setattr(atomic_write, "_fsync_directory", real_sync)
    _convert(memory, tasks)
    assert _tree_bytes(memory) == expected
    assert (
        plan_state_conversion(read_knowledge_json(memory), read_task_owners(tasks), at=AT).changes
        == {}
    )


def test_a_failed_restore_reports_the_exact_remainder_and_a_later_run_is_consistent(
    roots: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(knowledge_convert_states, "clock_now", lambda: AT)
    memory, tasks = roots
    before = _tree_bytes(memory)
    expected = _converted_copy(memory, tasks, tmp_path)
    real_write, calls = state_files.atomic_write_bytes, []

    def write(path: Path, data: bytes, **options: Any) -> None:
        calls.append(path)
        if len(calls) >= 4:  # every restoring write fails before it replaces anything
            raise OSError("injected: the restoring write failed")
        real_write(path, data, **options)
        if len(calls) == 3:  # the report followed by two memory writes
            raise OSError("injected: failure after the second memory replacement")

    monkeypatch.setattr(state_files, "atomic_write_bytes", write)
    report = tmp_path / "report.json"
    arguments = [*_command_arguments(memory, tasks), "--report", str(report)]
    assert main([*arguments, "--apply"]) == 3
    out = capsys.readouterr().err
    after = _tree_bytes(memory)
    left = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    assert left == [PATHS["accepted"]]  # the first write; the new reviews file was removed again
    assert PATHS["accepted"] in out and CONVERTED_REVIEWS_PATH not in out
    assert "1 file(s)" in out
    answer = json.loads(report.read_text(encoding="utf-8"))
    assert answer["at"] == AT and PATHS["accepted"] in answer["changedFiles"]
    monkeypatch.setattr(state_files, "atomic_write_bytes", real_write)
    assert main([*arguments, "--apply"]) == 0
    assert _tree_bytes(memory) == expected
    assert (
        plan_state_conversion(read_knowledge_json(memory), read_task_owners(tasks), at=AT).changes
        == {}
    )
