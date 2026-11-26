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
from agents_remember.models.knowledge_files.converted_reviews import (
    CONVERTED_REVIEWS_PATH,
    ConvertedReviews,
)
from agents_remember.models.knowledge_files.documents import parse_history_document
from agents_remember.models.knowledge_files.history_product import (
    crossing_selection_digest,
    record_content_digest,
    stored_record_document,
)
from agents_remember.models.knowledge_files.record_state import RevisionStates
from agents_remember.models.knowledge_files.records import InvariantRecord

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


def _legacy_record(
    revision: int,
    writer: dict[str, Any] | None,
    *,
    status: str = "accepted",
    **fields: str,
) -> bytes:
    return _record(revision, writer, status=status, **fields)


def _conversion_stamp(at: str = "2026-11-26T10:00:00+01:00") -> dict[str, Any]:
    return {
        "by": "conversion",
        "role": "unknown",
        "harness": "unknown",
        "model": "unknown",
        "effort": "unknown",
        "seat": "unknown",
        "task": "260101-MAS",
        "leaf": "260101-MAS-L0",
        "at": at,
        "operation": "state-conversion",
    }


def _review_fact(owner: str, *, handed_over_by: str = "curator") -> dict[str, Any]:
    return {
        "round": 1,
        "reviewer": {
            **_stamp(owner, "seat-reviewer", "2026-01-03T11:30:00+01:00"),
            "role": "reviewer",
        },
        "handedOverBy": {
            **_stamp(owner, f"seat-{handed_over_by}", "2026-01-03T11:00:00+01:00"),
            "role": handed_over_by,
        },
    }


def _converted_reviews(owner: str) -> bytes:
    document = {
        "schema": "ar-converted-reviews/v1",
        "reviews": [
            {
                "owner": owner,
                "fact": _review_fact(owner),
            }
        ],
    }
    raw = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    ConvertedReviews.model_validate_json(raw)
    return raw


def _consolidator_keep_history(owner: str, record_id: str, revision: int) -> bytes:
    document = {
        "schema": "ar-history/v1",
        "leaf": owner,
        "closed": True,
        "rows": [
            {
                "id": "ROW-3D5E7F",
                "subject": record_id,
                "disposition": "no_impact",
                "reason": "Read at this revision and kept unchanged.",
                "items": [],
                "covers": [],
                "revision": revision,
            }
        ],
        "review": _review_fact(owner, handed_over_by="consolidator"),
    }
    raw = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    parse_history_document(f"knowledge/history/{owner}.json", raw.decode("utf-8"))
    return raw


def test_incoming_state_conversion_kept_for_both_conversion_shapes(tmp_path: Path) -> None:
    """Incoming state-converted records that own never changed keep incoming's bytes."""

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    record_unstamped = RECORD
    record_stamped = "knowledge/invariants/INV-8M4G2P-stamped.json"
    existing_writer = _stamp("260101-MAS-L0", "seat-0", "2026-01-01T10:00:00+01:00")
    converted_writer = _conversion_stamp()

    base = {
        record_unstamped: _legacy_record(1, None),
        record_stamped: _legacy_record(1, existing_writer, id="INV-8M4G2P"),
    }
    ours = dict(base)
    theirs = {
        record_unstamped: _record(1, converted_writer),
        record_stamped: _record(1, existing_writer, id="INV-8M4G2P"),
        CONVERTED_REVIEWS_PATH: _converted_reviews("260101-MAS-L0"),
    }
    crossing_file = f"knowledge/history/{SYNCING_LEAF}-crossing-1.json"
    product = product_write("leaf", SYNCING_LEAF, f"{SYNCING_LEAF}-crossing-1", ours, MERGED_AT)

    merged = merge_with_writers(base, ours, theirs, repository=tmp_path, product=product)

    assert merged.conflicts == []
    assert merged.files[record_unstamped] == theirs[record_unstamped]
    assert merged.files[record_stamped] == theirs[record_stamped]
    for path in (record_unstamped, record_stamped):
        stored = json.loads(merged.files[path] or b"{}")
        assert "status" not in stored
        assert "writer" in stored
    assert crossing_file not in merged.files


def test_own_state_conversion_kept_against_unconverted_incoming(tmp_path: Path) -> None:
    """Reverse direction: own converted records that incoming never changed keep own's bytes."""

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    existing_writer = _stamp("260101-MAS-L0", "seat-0", "2026-01-01T10:00:00+01:00")
    record_stamped = "knowledge/invariants/INV-8M4G2P-stamped.json"

    base = {
        RECORD: _legacy_record(1, None),
        record_stamped: _legacy_record(1, existing_writer, id="INV-8M4G2P"),
    }
    ours = {
        RECORD: _record(1, _conversion_stamp()),
        record_stamped: _record(1, existing_writer, id="INV-8M4G2P"),
        CONVERTED_REVIEWS_PATH: _converted_reviews("260101-MAS-L0"),
    }
    theirs = dict(base)
    crossing_file = f"knowledge/history/{SYNCING_LEAF}-crossing-1.json"
    product = product_write("leaf", SYNCING_LEAF, f"{SYNCING_LEAF}-crossing-1", ours, MERGED_AT)

    merged = merge_with_writers(base, ours, theirs, repository=tmp_path, product=product)

    assert merged.conflicts == []
    assert merged.files[RECORD] == ours[RECORD]
    assert merged.files[record_stamped] == ours[record_stamped]
    assert crossing_file not in merged.files


def test_both_converted_with_different_stamps_join_reviews_as_tied(tmp_path: Path) -> None:
    """Stamp-only tie: changed side or own picks the bytes, ``tied`` is true, and reviews join."""

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    base_writer = _stamp("260101-MAS-L0", "seat-0", "2026-01-01T10:00:00+01:00")
    ours_writer = _stamp("260101-MAS-L1", "seat-1", "2026-01-02T10:00:00+01:00")
    theirs_writer = _stamp("260101-MAS-L2", "seat-2", "2026-01-03T10:00:00+01:00")
    keeper_path = "knowledge/history/260101-MAS-L9.json"
    keeper_history = _consolidator_keep_history("260101-MAS-L9", "INV-7K3F9Q", 1)
    crossing_file = f"knowledge/history/{SYNCING_LEAF}-crossing-1.json"

    # Single-side stamp renewal (incoming renewed the stamp, own kept base) with shared selection:
    # tied=True returns early from _crossing_selection, writing no crossingSelections row.
    base_1 = {RECORD: _record(1, base_writer)}
    ours_1 = {RECORD: _record(1, base_writer)}
    theirs_1 = {RECORD: _record(1, theirs_writer), keeper_path: keeper_history}
    product_1 = product_write("leaf", SYNCING_LEAF, f"{SYNCING_LEAF}-crossing-1", ours_1, MERGED_AT)
    merged_1 = merge_with_writers(base_1, ours_1, theirs_1, repository=tmp_path, product=product_1)

    assert merged_1.conflicts == []
    assert merged_1.files[RECORD] == theirs_1[RECORD]
    assert crossing_file not in merged_1.files

    # Both sides changed the stamp from base, with a peer consolidator review:
    # previously tied=False because raw bytes differed, which wrote excludedReviews for the peer.
    base_2 = {RECORD: _record(1, base_writer)}
    ours_2 = {RECORD: _record(1, ours_writer)}
    theirs_2 = {RECORD: _record(1, theirs_writer), keeper_path: keeper_history}
    product_2 = product_write("leaf", SYNCING_LEAF, f"{SYNCING_LEAF}-crossing-1", ours_2, MERGED_AT)
    merged_2 = merge_with_writers(base_2, ours_2, theirs_2, repository=tmp_path, product=product_2)

    assert merged_2.conflicts == []
    assert merged_2.files[RECORD] == ours_2[RECORD]
    assert crossing_file not in merged_2.files
    merged_states = RevisionStates.of(
        [
            parse_history_document(path, data.decode("utf-8"))
            for path, data in merged_2.files.items()
            if path.startswith("knowledge/history/") and data is not None
        ]
    )
    resolved_record = InvariantRecord.model_validate_json(merged_2.files[RECORD] or b"{}")
    assert merged_states.resolve_current(resolved_record).state == "consolidated"


def test_both_converted_with_different_stamps_and_prior_exclusion_joins_reviews(
    tmp_path: Path,
) -> None:
    """When one side has a prior crossing selection excluding peer's review, a stamp-only tie clears it."""

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    base_writer = _stamp("260101-MAS-L0", "seat-0", "2026-01-01T10:00:00+01:00")
    ours_writer = _stamp("260101-MAS-L1", "seat-1", "2026-01-02T10:00:00+01:00")
    theirs_writer = _stamp("260101-MAS-L2", "seat-2", "2026-01-03T10:00:00+01:00")
    keeper_path = "knowledge/history/260101-MAS-L9.json"
    keeper_bytes = _consolidator_keep_history("260101-MAS-L9", "INV-7K3F9Q", 1)

    ours_bytes = _record(1, ours_writer)
    parsed = InvariantRecord.model_validate_json(ours_bytes)
    doc_digest = crossing_selection_digest(stored_record_document(parsed))
    keeper_states = RevisionStates.of([parse_history_document(keeper_path, keeper_bytes.decode())])
    peer_review_ids = sorted(keeper_states.resolve_current(parsed).reviews)
    assert len(peer_review_ids) == 1

    prior_crossing = "knowledge/history/260101-MAS-crossing-1.json"
    prior_doc = {
        "schema": "ar-history/v1",
        "crossing": "260101-MAS-crossing-1",
        "closed": True,
        "rows": [],
        "crossingSelections": [
            {
                "record": "INV-7K3F9Q",
                "revision": 1,
                "documentDigest": doc_digest,
                "writer": ours_writer,
                "excludedReviews": peer_review_ids,
            }
        ],
    }
    prior_bytes = (json.dumps(prior_doc, indent=2, sort_keys=True) + "\n").encode("utf-8")

    base = {RECORD: _record(1, base_writer)}
    ours = {RECORD: ours_bytes, prior_crossing: prior_bytes}
    theirs = {RECORD: _record(1, theirs_writer), keeper_path: keeper_bytes}
    crossing_file = f"knowledge/history/{SYNCING_LEAF}-crossing-1.json"
    product = product_write("leaf", SYNCING_LEAF, f"{SYNCING_LEAF}-crossing-1", ours, MERGED_AT)

    merged = merge_with_writers(base, ours, theirs, repository=tmp_path, product=product)

    assert merged.conflicts == []
    assert merged.files[RECORD] == ours_bytes
    crossing_doc = json.loads(merged.files[crossing_file] or b"{}")
    selections = crossing_doc["crossingSelections"]
    assert len(selections) == 1
    assert selections[0]["supersedes"] == ["260101-MAS-crossing-1"]
    assert selections[0]["excludedReviews"] == []
    merged_states = RevisionStates.of(
        [
            parse_history_document(path, data.decode("utf-8"))
            for path, data in merged.files.items()
            if path.startswith("knowledge/history/") and data is not None
        ]
    )
    assert merged_states.resolve_current(parsed).state == "consolidated"


def test_meaning_change_on_incoming_keeps_incoming_and_conflict_stays_conflicted(
    tmp_path: Path,
) -> None:
    """One side changing meaning keeps that side; both changing one field differently conflicts."""

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    base_writer = _stamp("260101-MAS-L0", "seat-0", "2026-01-01T10:00:00+01:00")
    ours_writer = _stamp("260101-MAS-L1", "seat-1", "2026-01-02T10:00:00+01:00")
    theirs_writer = _stamp("260101-MAS-L2", "seat-2", "2026-01-03T10:00:00+01:00")

    base = {RECORD: _record(1, base_writer)}
    ours = {RECORD: _record(1, base_writer)}
    theirs = {RECORD: _record(2, theirs_writer, statement="Incoming changed the meaning.")}
    product = product_write("leaf", SYNCING_LEAF, f"{SYNCING_LEAF}-crossing-1", ours, MERGED_AT)
    merged = merge_with_writers(base, ours, theirs, repository=tmp_path, product=product)

    assert merged.conflicts == []
    assert merged.files[RECORD] == theirs[RECORD]

    ours_conflict = {RECORD: _record(2, ours_writer, statement="Own changed the meaning.")}
    product_conflict = product_write(
        "leaf", SYNCING_LEAF, f"{SYNCING_LEAF}-crossing-1", ours_conflict, MERGED_AT
    )
    conflicted = merge_with_writers(
        base, ours_conflict, theirs, repository=tmp_path, product=product_conflict
    )

    assert [(one.path, one.item) for one in conflicted.conflicts] == [(RECORD, "statement")]
    assert "writer" not in json.loads(conflicted.files[RECORD] or b"{}")


def test_record_new_on_both_sides_with_equal_content_uses_state_form_then_own(
    tmp_path: Path,
) -> None:
    """Absent at base counts as changed on both sides; sole state form wins, else own."""

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    ours_writer = _stamp("260101-MAS-L1", "seat-1", "2026-01-02T10:00:00+01:00")
    theirs_writer = _stamp("260101-MAS-L2", "seat-2", "2026-01-03T10:00:00+01:00")
    base: dict[str, bytes] = {}

    # Own is legacy (even with a history row that could attribute a writer); incoming is state form.
    ours_legacy_history = {
        "schema": "ar-history/v1",
        "leaf": "260101-MAS-L1",
        "closed": True,
        "rows": [],
        "written": [
            {
                "record": "INV-7K3F9Q",
                "revision": 1,
                "writer": ours_writer,
                "contentDigest": record_content_digest(
                    json.loads(_record(1, ours_writer).decode("utf-8"))
                ),
            }
        ],
    }
    ours_unconverted = {
        RECORD: _legacy_record(1, None),
        OURS_HISTORY: (json.dumps(ours_legacy_history, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    }
    theirs_state_form = {RECORD: _record(1, theirs_writer)}
    product = product_write(
        "leaf", SYNCING_LEAF, f"{SYNCING_LEAF}-crossing-1", ours_unconverted, MERGED_AT
    )
    merged_state_incoming = merge_with_writers(
        base, ours_unconverted, theirs_state_form, repository=tmp_path, product=product
    )
    assert merged_state_incoming.conflicts == []
    assert merged_state_incoming.files[RECORD] == theirs_state_form[RECORD]

    # Also when own has no legacy status word but lacks a stored writer (history writer does not count).
    ours_no_writer = {
        RECORD: _record(1, None),
        OURS_HISTORY: ours_unconverted[OURS_HISTORY],
    }
    merged_no_writer = merge_with_writers(
        base, ours_no_writer, theirs_state_form, repository=tmp_path, product=product
    )
    assert merged_no_writer.conflicts == []
    assert merged_no_writer.files[RECORD] == theirs_state_form[RECORD]

    # Reverse: own is state form, incoming is legacy -> own kept.
    ours_state_form = {RECORD: _record(1, ours_writer)}
    theirs_legacy = {RECORD: _legacy_record(1, None)}
    product_own = product_write(
        "leaf", SYNCING_LEAF, f"{SYNCING_LEAF}-crossing-1", ours_state_form, MERGED_AT
    )
    merged_state_own = merge_with_writers(
        base, ours_state_form, theirs_legacy, repository=tmp_path, product=product_own
    )
    assert merged_state_own.conflicts == []
    assert merged_state_own.files[RECORD] == ours_state_form[RECORD]

    # Both sides in state form with different stamps fall back to own.
    merged_both_state = merge_with_writers(
        base, ours_state_form, theirs_state_form, repository=tmp_path, product=product_own
    )
    assert merged_both_state.conflicts == []
    assert merged_both_state.files[RECORD] == ours_state_form[RECORD]
