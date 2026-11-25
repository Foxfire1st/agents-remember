"""Every rule has a title, exercised through the writer on real paired Git trees.

A new rule is refused without a title of at most 80 characters, and the refusal says what is
missing. A rule written before titles existed is revised without one, and the write's answer says
that it has none. A title is a name, not meaning: a write that changes it alone raises no revision.
"""

from __future__ import annotations

from typing import Any

import pytest
from agents_remember.application.knowledge_write_answer import write_answer
from agents_remember.models.knowledge_files.history_product import (
    crossing_selection_digest,
    record_content_digest,
)
from knowledge_writer_test_support import (
    ADMISSION,
    BASE_INVARIANT,
    SCOPE,
    build_world,
    entry,
    read_json,
    tree_bytes,
)
from writer_corrections_test_support import run

MISSING_TITLE_TEXT = (
    "a new rule needs a `title`: a name of at most 80 characters on one line; "
    "add it beside the rule's statement"
)
BASE_PATH = f"knowledge/invariants/{BASE_INVARIANT}-landing-pair.json"
BASE_STATEMENT = "Code and memory land as one pair."


def _answer(report: Any) -> dict[str, Any]:
    """The mounted ``knowledge_write`` answer for a report, without a report file."""

    return write_answer(report.to_document(), None, None)


def _revision_of_base(statement: str, **curator: Any) -> dict[str, Any]:
    """A hand-off that revises the base rule, which was written before titles existed."""

    return {
        "entries": [entry("A-9", invariant_id=BASE_INVARIANT, statement=statement, **curator)],
        "history": [
            {
                "subject": BASE_INVARIANT,
                "disposition": "changed",
                "reason": "The statement was sharpened.",
                "effect": "clarify",
            }
        ],
    }


def _record_invariant(**fields: Any) -> dict[str, Any]:
    """A new rule authored in the ``records`` section."""

    return {
        "key": "R-1",
        "kind": "invariant",
        "slug": "landing-pair",
        "fields": {
            "statement": "A landing names its pair.",
            "applicability": "Every landing.",
            "conditions": [],
            "exclusions": [],
            "supersedes": [],
            "admission": ADMISSION,
            **fields,
        },
    }


@pytest.mark.parametrize("section", ["entries", "records"])
def test_a_new_rule_without_a_title_is_refused_with_its_text(tmp_path: Any, section: str) -> None:
    world = build_world(tmp_path)
    before = tree_bytes(world.memory)
    if section == "entries":
        document: Any = [entry("A1", scope=SCOPE, admission=ADMISSION, title=None)]
        where = "entries[1] (A1)"
    else:
        document = {"records": [_record_invariant()]}
        where = "records[1].fields"
    report = run(world, document)
    assert report.state == "refused"
    assert f"{where}: {MISSING_TITLE_TEXT}" in report.render()
    assert {"where": where, "message": MISSING_TITLE_TEXT} in _answer(report)["problems"]
    assert tree_bytes(world.memory) == before


@pytest.mark.parametrize("title", ["x" * 81, "two\nlines", " padded", ""])
def test_a_title_that_is_not_one_line_of_at_most_80_characters_is_refused(
    tmp_path: Any, title: str
) -> None:
    world = build_world(tmp_path)
    before = tree_bytes(world.memory)
    report = run(world, [entry("A1", scope=SCOPE, admission=ADMISSION, title=title)])
    assert report.state == "refused"
    assert "`title` is a name of at most 80 characters on one line" in report.render()
    assert tree_bytes(world.memory) == before


def test_a_new_rule_is_written_with_its_title(tmp_path: Any) -> None:
    world = build_world(tmp_path)
    title = "x" * 80
    report = run(world, [entry("A1", scope=SCOPE, admission=ADMISSION, title=title)])
    assert report.state == "written", report.render()
    (outcome,) = report.records
    assert read_json(world.memory, outcome.path)["title"] == title
    assert _answer(report)["notes"] == []


def test_a_new_revision_of_an_untitled_rule_is_taken_and_the_answer_says_it_has_none(
    tmp_path: Any,
) -> None:
    world = build_world(tmp_path)
    assert "title" not in read_json(world.memory, BASE_PATH)
    report = run(world, _revision_of_base("Code and memory land together."))
    assert report.state == "written", report.render()
    invariant = read_json(world.memory, BASE_PATH)
    assert invariant["revision"] == 2
    assert invariant["statement"] == "Code and memory land together."
    assert "title" not in invariant
    note = (
        f"{BASE_INVARIANT} has no title: it is shown by the first words of its statement until a "
        "write names it with `title` (a name of at most 80 characters on one line)"
    )
    assert note in report.notes
    assert note in _answer(report)["notes"]


def test_a_write_of_a_title_alone_raises_no_revision(tmp_path: Any) -> None:
    world = build_world(tmp_path)
    named = {"entries": [entry("A-9", invariant_id=BASE_INVARIANT, statement=BASE_STATEMENT)]}
    for title in ("Landing pair", "Code and memory land together"):
        named["entries"][0]["title"] = title
        report = run(world, named)
        assert report.state == "written", report.render()
        (outcome,) = report.records
        assert (outcome.action, outcome.revision) == ("updated", 1)
        invariant = read_json(world.memory, BASE_PATH)
        assert (invariant["title"], invariant["revision"]) == (title, 1)
        assert invariant["statement"] == BASE_STATEMENT
        assert _answer(report)["notes"] == []


def test_an_invariant_title_keeps_review_and_crossing_identity() -> None:
    invariant = {"schema": "ar-invariant/v1", "statement": BASE_STATEMENT}
    family = {"schema": "ar-family/v1", "statement": BASE_STATEMENT}
    for digest in (record_content_digest, crossing_selection_digest):
        assert digest(invariant) == digest(dict(invariant, title="Landing pair"))
        assert digest(invariant) != digest(dict(invariant, statement="A different guarantee."))
        assert digest(dict(family, title="Landing")) != digest(dict(family, title="Pair"))
