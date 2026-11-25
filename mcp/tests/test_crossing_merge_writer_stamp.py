"""A crossing merge stores a combined record under the product's stamp, never under one agent's.

Both lines raise one record from the same base to the same revision number, each by another
meaning field, and each line's write stamped the record with its own writer. The merge combines the
two contents without a conflict: ``writer`` is not merged as a field. The stored record equals
neither line's, so it carries the product's stamp for the merge, and the syncing leaf's history
file names that revision with the same stamp. The history files that name the two lines' revisions
belong to other owners, so each is taken whole from its line with its ``written`` rows unchanged.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from agents_remember.memory.conversion.crossing_writer import (
    CROSSING_OPERATION,
    merge_with_writers,
    product_write,
)
from agents_remember.models.knowledge_files.history_product import record_content_digest

RECORD = "knowledge/invariants/INV-7K3F9Q-holds.json"
OURS_HISTORY = "knowledge/history/260101-MAS-L1.json"
THEIRS_HISTORY = "knowledge/history/260101-MAS-L2.json"
SYNCING_LEAF = "260101-MAS-L1"
MERGED_AT = "2026-01-03T12:00:00+01:00"


def _stamp(leaf: str, seat: str, at: str) -> dict[str, Any]:
    return {
        "by": "agent",
        "role": "curator",
        "harness": "claude",
        "model": "model-a",
        "effort": "low",
        "seat": seat,
        "task": "260101-MAS",
        "leaf": leaf,
        "at": at,
    }


def _record(revision: int, writer: dict[str, Any] | None, **fields: str) -> bytes:
    document: dict[str, Any] = {
        "schema": "ar-invariant/v1",
        "id": "INV-7K3F9Q",
        "origin": {"task": "260101-MAS", "leaf": "260101-MAS-L0"},
        "revision": revision,
        "statement": "The record holds.",
        "applicability": "Every call.",
        "conditions": [],
        "exclusions": [],
        "supersedes": [],
        "admission": "legacy-unassessed",
        **fields,
    }
    if writer is not None:
        document["writer"] = writer
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _history(leaf: str, writer: dict[str, Any], *, closed: bool = True) -> bytes:
    document = {
        "schema": "ar-history/v1",
        "leaf": leaf,
        "closed": closed,
        "rows": [],
        "written": [{"record": "INV-7K3F9Q", "revision": 2, "writer": writer}],
    }
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def test_two_stamped_lines_raising_one_record_combine_under_the_products_stamp(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    ours_writer = _stamp("260101-MAS-L1", "seat-1", "2026-01-02T10:00:00+01:00")
    theirs_writer = _stamp("260101-MAS-L2", "seat-2", "2026-01-03T10:00:00+01:00")
    base = {RECORD: _record(1, _stamp("260101-MAS-L0", "seat-0", "2026-01-01T10:00:00+01:00"))}
    ours = {
        RECORD: _record(
            2,
            ours_writer,
            statement="The record holds on every line.",
        ),
        OURS_HISTORY: _history("260101-MAS-L1", ours_writer, closed=False),
    }
    theirs = {
        RECORD: _record(2, theirs_writer, applicability="Every call, also a retried one."),
        THEIRS_HISTORY: _history("260101-MAS-L2", theirs_writer),
    }

    product = product_write("leaf", SYNCING_LEAF, f"{SYNCING_LEAF}-crossing-1", ours, MERGED_AT)

    merged = merge_with_writers(base, ours, theirs, repository=tmp_path, product=product)

    assert merged.conflicts == []
    document = json.loads(merged.files[RECORD] or b"{}")
    assert (document["statement"], document["applicability"]) == (
        "The record holds on every line.",
        "Every call, also a retried one.",
    )
    assert document["writer"] == product.stamp
    assert (document["writer"]["by"], document["writer"]["operation"]) == (
        "product",
        CROSSING_OPERATION,
    )
    # The syncing leaf's file names the product's revision; it is the own line's history file.
    syncing = json.loads(merged.files[OURS_HISTORY] or b"{}")
    assert syncing["written"] == [
        {
            "record": "INV-7K3F9Q",
            "revision": 2,
            "writer": product.stamp,
            "contentDigest": record_content_digest(document),
        }
    ]
    # The other line's history file is taken whole, its written row unchanged.
    assert merged.files[THEIRS_HISTORY] == theirs[THEIRS_HISTORY]


def test_a_record_only_one_line_raised_is_that_lines_write_with_its_stamp(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    base_writer = _stamp("260101-MAS-L0", "seat-0", "2026-01-01T10:00:00+01:00")
    ours_writer = _stamp("260101-MAS-L1", "seat-1", "2026-01-02T10:00:00+01:00")
    base = {RECORD: _record(1, base_writer)}
    ours = {RECORD: _record(2, ours_writer, statement="The record holds on every line.")}
    theirs = {RECORD: _record(1, base_writer)}

    product = product_write("leaf", SYNCING_LEAF, f"{SYNCING_LEAF}-crossing-1", ours, MERGED_AT)

    merged = merge_with_writers(base, ours, theirs, repository=tmp_path, product=product)

    assert merged.conflicts == []
    assert merged.files[RECORD] == ours[RECORD]
