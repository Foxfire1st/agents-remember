"""Focused contracts for newest-first external-memory ledger history."""

from agents_remember.kernel.memory_ledger import (
    contains_mapping,
    create_initial_ledger,
    find_mapping,
    ledger_to_text,
    parse_ledger_text,
    prepend_mapping,
)


def test_roundtrip_preserves_newest_same_code_history() -> None:
    ledger = create_initial_ledger("repo-a", "c1", "m1")
    text = ledger_to_text(ledger)

    assert "# Memory Ledger" in text
    assert "trackedCodeBranch" not in text
    assert "memoryBranch" not in text
    parsed = parse_ledger_text(text)
    assert parsed.last_verified_code_commit == "c1"

    updated = prepend_mapping(parsed, "c1", "m2")
    reparsed = parse_ledger_text(ledger_to_text(updated))
    assert reparsed.rows[0] == find_mapping(reparsed, "c1")
    assert reparsed.rows[0].memory_commit == "m2"
    assert contains_mapping(reparsed, "c1", "m1")
    assert reparsed.rows[1].memory_commit == "m1"
