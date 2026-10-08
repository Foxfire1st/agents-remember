"""Retirement detects obsolete sentences while admitting preserved role duties."""

from pathlib import Path

from agents_remember_test_support.testing.curation_doctrine import (
    normalize_statement,
    retired_statement_findings,
)
from agents_remember_test_support.testing.leaf_instruction_wording import (
    autonomy_clauses,
    forbidden_autonomy_clauses,
    missing_clauses,
)
from agents_remember_test_support.testing.retired_leaf_handover_wording import (
    RETIRED_LEAF_HANDOVER_WORDING,
)

ROOT = Path(__file__).resolve().parents[2]


def test_bootstrap_restored_fixed_helper_restriction_fails_with_freedom_present() -> None:
    current = (ROOT / "skills/l-01-agent-lifecycles/roles/bootstrap.md").read_text()
    old = (
        "- **Sub-agents** — read/search only, writing durable notes; your main loop owns every "
        "mutating call and the report, and no sub-agent runs a setup surface."
    )
    assert missing_clauses(current + old, autonomy_clauses("bootstrap")) == []
    assert forbidden_autonomy_clauses("bootstrap", current) == []
    assert forbidden_autonomy_clauses("bootstrap", current + old) == [
        "read/search-only helpers",
        "blanket setup-surface prohibition",
    ]
    valid = "Your main loop owns every mutating setup call; helpers may query permitted read-only status."
    assert forbidden_autonomy_clauses("bootstrap", current + valid) == []


def test_preserved_no_role_and_identity_sentences_remain_valid_on_their_sources() -> None:
    for path, sentence in (
        ("roles/curator.md", "A Curator starts no role."),
        ("roles/reviewer.md", "A Reviewer starts no role."),
        ("roles/worker.md", "Do not invent an owner or agent ID."),
        ("operations/curation.md", "A Curator starts no role."),
    ):
        relative = f"l-01-agent-lifecycles/{path}"
        reading = (ROOT / "skills" / relative).read_text() + "\n" + sentence
        assert retired_statement_findings(normalize_statement(reading), relative) == []


def test_standalone_manager_handoff_sentence_is_retired_without_preserved_context() -> None:
    relative = "l-01-agent-lifecycles/core/acceptance.md"
    sentence = "For a leaf handoff that owner is the manager."
    assert retired_statement_findings(normalize_statement(sentence), relative)
    preserved = (
        "An artifact defect is owner-detected. A missing, malformed, or stale artifact is a handoff "
        "defect the owner finds after wake; it nudges, rejects, replaces, or escalates under existing "
        "doctrine instead of waiting for an imaginary notifier artifact check."
    )
    assert retired_statement_findings(normalize_statement(preserved), relative) == []


def test_each_obsolete_sentence_has_independent_source_confined_detection() -> None:
    for source, statement in RETIRED_LEAF_HANDOVER_WORDING:
        relative = source.removeprefix("skills/")
        current = normalize_statement((ROOT / source).read_text())
        assert retired_statement_findings(current, relative) == []
        assert retired_statement_findings(normalize_statement(statement), relative)
        assert (
            retired_statement_findings(normalize_statement(statement), "unrelated/source.md") == []
        )
