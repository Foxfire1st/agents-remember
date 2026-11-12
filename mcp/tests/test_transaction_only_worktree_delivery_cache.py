"""The memory-cache refresh cases of the transaction-only delivery module (CCR-R12).

An existing correct cache is untouched, a missing one is rebuilt, and corrupted or forged
data is repaired from Git alone -- without writing a single object or ref. The fixtures and
the shared assertions are in ``transaction_only_worktree_delivery_test_support.py``.
"""

from __future__ import annotations

from agents_remember.kernel.memory_ledger import (
    LedgerRow,
    ledger_to_text,
    load_ledger,
)
from transaction_only_worktree_delivery_test_support import (
    _ClosedCacheLeaf,
)


def test_cache_refresh_preserves_current_bytes_and_materializes_a_missing_cache(tmp_path):
    """An existing correct cache is untouched, and a missing one is recreated without Git writes."""

    leaf = _ClosedCacheLeaf(tmp_path)
    before_bytes = leaf.cache_path.read_bytes()
    before_git = leaf.git_state()

    current = leaf.refresh()

    assert current["state"] == "current"
    assert leaf.cache_path.read_bytes() == before_bytes
    assert leaf.git_state() == before_git
    leaf.cache_path.unlink()

    rebuilt = leaf.refresh()

    assert rebuilt["state"] == "updated"
    assert leaf.cache_path.read_bytes() == before_bytes
    assert load_ledger(leaf.cache_path) == leaf.canonical
    assert leaf.git_state() == before_git
    leaf.assert_content_unchanged()


def test_cache_refresh_repairs_malformed_forged_and_reordered_data_from_git(tmp_path):
    """Cache corruption changes neither the reconstructed attribution nor any Git object/ref."""

    leaf = _ClosedCacheLeaf(tmp_path)
    own_row = leaf.own_row
    source_row = leaf.source_rows[0]
    malformations = (
        ("malformed text", "<<<<<<< not a ledger\n"),
        (
            "forged memory and code mappings",
            leaf.render(
                [
                    own_row,
                    LedgerRow(own_row.code_commit, "0" * 40),
                    LedgerRow("f" * 40, own_row.memory_commit),
                    *leaf.source_rows,
                ]
            ),
        ),
        ("reordered rows", leaf.render([source_row, own_row])),
        (
            "header disagrees with its first row",
            leaf.render(
                [own_row, *leaf.source_rows],
                header=(source_row.code_commit, source_row.memory_commit),
            ),
        ),
    )
    before_git = leaf.git_state()
    for name, text in malformations:
        leaf.cache_path.write_text(text, encoding="utf-8")

        refreshed = leaf.refresh()

        assert refreshed["state"] == "updated", name
        assert load_ledger(leaf.cache_path) == leaf.canonical, name
        assert leaf.cache_path.read_text(encoding="utf-8") == ledger_to_text(leaf.canonical), name
        assert leaf.git_state() == before_git, name
        leaf.assert_content_unchanged()
