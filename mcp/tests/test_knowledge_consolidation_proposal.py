"""The ``consolidation_proposal`` view proposes the list of a consolidation pass in three ways.

One committed memory tree holds three families and three rules in no family:

* ``FAM-F00001`` -- ``INV-A00001`` in ``pkg/alpha/a.py`` and ``INV-A00002`` in ``pkg/alpha/b.py``,
  with loose ``INV-N00001`` in ``pkg/alpha/a.py`` (same file -> attached) and loose
  ``INV-N00003`` in ``pkg/alpha/d.py`` (same folder, different file -> not attached);
* ``FAM-F00002`` -- ``INV-B00001`` in ``pkg/beta/c.py``, with no loose rule there; a
  consolidator's reviewed pass reached it;
* ``FAM-F00003`` -- ``INV-C00001`` in ``pkg/gamma/f.py``, with loose ``INV-N00002`` in the same
  file ``pkg/gamma/f.py``.

The finished master ``261112-FIX`` changed ``INV-A00001`` and ``INV-N00001``, wrote a revision of
``INV-C00001`` and only read ``INV-B00001``. ``FAM-F00002`` and ``FAM-F00003`` were committed before
``FAM-F00001``. Each case reads the view through the read tool's builder, as a caller gets it.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from agents_remember.mcp.tools.knowledge import ReadToolRequest, knowledge_read_payload
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    file_sidecar_path,
    record_path,
)
from knowledge_index_test_support import anchor, write_document
from knowledge_review_fact_test_support import history_document, review_fact, written

MASTER = "261112-FIX"
MASTER_LEAF = "261112-FIX-L1"
PASS_LEAF = "261120-CON-L1"
OTHER_LEAF = "260928-MIK-L1"
FAMILIES = {
    "FAM-F00001": ("Alpha rules", ("INV-A00001", "INV-A00002")),
    "FAM-F00002": ("Beta rules", ("INV-B00001",)),
    "FAM-F00003": ("Gamma rules", ("INV-C00001",)),
}
REALIZED_AT = {
    "INV-A00001": "pkg/alpha/a.py",
    "INV-A00002": "pkg/alpha/b.py",
    "INV-N00001": "pkg/alpha/a.py",
    "INV-N00003": "pkg/alpha/d.py",
    "INV-B00001": "pkg/beta/c.py",
    "INV-C00001": "pkg/gamma/f.py",
    "INV-N00002": "pkg/gamma/f.py",
}


def _invariant(record_id: str) -> tuple[str, dict[str, Any]]:
    return record_path("invariant", record_id, record_id.lower()), {
        "schema": "ar-invariant/v1",
        "id": record_id,
        "origin": {"task": "260928-MIK", "leaf": OTHER_LEAF},
        "revision": 1,
        "statement": f"{record_id} holds.",
        "applicability": "Every fixture read.",
        "conditions": [],
        "exclusions": [],
        "supersedes": [],
        "admission": "legacy-unassessed",
    }


def _family(record_id: str) -> tuple[str, dict[str, Any]]:
    title, members = FAMILIES[record_id]
    return record_path("family", record_id, record_id.lower()), {
        "schema": "ar-family/v1",
        "id": record_id,
        "origin": {"task": "260928-MIK", "leaf": OTHER_LEAF},
        "revision": 1,
        "title": title,
        "guarantee": f"{title} hold together.",
        "members": list(members),
        "routes": ["pkg"],
        "admission": "legacy-unassessed",
    }


def _sidecars() -> list[tuple[str, dict[str, Any]]]:
    by_path: dict[str, list[dict[str, Any]]] = {}
    for number, (record_id, path) in enumerate(REALIZED_AT.items(), start=1):
        by_path.setdefault(path, []).append(
            {
                "id": f"RLZ-R0000{number}",
                "invariant": record_id,
                "anchor": anchor(f"symbol{number}"),
                "role": "enforcement",
                "rationale": f"symbol{number} enforces {record_id}.",
            }
        )
    return [
        (
            file_sidecar_path(path),
            {
                "schema": "ar-onboarding-file/v1",
                "path": path,
                "references": {},
                "realizes": realizations,
            },
        )
        for path, realizations in by_path.items()
    ]


def _row(number: int, subject: str, disposition: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"ROW-W0000{number}",
        "subject": subject,
        "disposition": disposition,
        "reason": f"{subject} {disposition} in the fixture.",
        "items": [],
        **extra,
    }


def _histories() -> dict[str, dict[str, Any]]:
    master_path, master = history_document(
        MASTER_LEAF,
        written_rows=(written("INV-C00001", 1, MASTER_LEAF),),
        review=review_fact(MASTER_LEAF),
        rows=(
            _row(1, "INV-A00001", "changed", covers=[], revision=1, effect="clarify"),
            _row(2, "INV-N00001", "changed", covers=[], revision=1, effect="clarify"),
            _row(3, "INV-B00001", "no_impact", covers=[], revision=1),
        ),
    )
    pass_path, consolidation = history_document(
        PASS_LEAF,
        review=review_fact(PASS_LEAF, handed_over_by="consolidator"),
        rows=(_row(4, "FAM-F00002", "no_impact", examined=[]),),
    )
    return {master_path: master, pass_path: consolidation}


def _commit(memory: Path, date: str) -> None:
    environment = {**os.environ, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date}
    identity = ("-c", "user.name=t", "-c", "user.email=t@t")
    subprocess.run(["git", "-C", str(memory), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(memory), *identity, "commit", "-qm", date],
        check=True,
        env=environment,
    )


@pytest.fixture(name="memory")
def _memory(tmp_path: Path) -> Path:
    """The fixture tree, committed in two steps so the families differ in their first writing."""

    memory = tmp_path / "memory"
    memory.mkdir()
    subprocess.run(["git", "init", "-q", str(memory)], check=True)
    write_document(memory, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "1"})
    early = [_family("FAM-F00002"), _family("FAM-F00003")]
    early += [_invariant(rule) for rule in REALIZED_AT]
    early += _sidecars()
    early += list(_histories().items())
    for path, document in early:
        write_document(memory, path, document)
    _commit(memory, "2026-10-01T10:00:00+00:00")
    write_document(memory, *_family("FAM-F00001"))
    _commit(memory, "2026-10-05T10:00:00+00:00")
    return memory


def _answer(
    memory: Path,
    *,
    master_id: str | None = None,
    record_id: str | None = None,
    source_path: str | None = None,
) -> dict[str, Any]:
    request = ReadToolRequest(
        memory_root=str(memory),
        view="consolidation_proposal",
        master_id=master_id,
        record_id=record_id,
        source_path=source_path,
    )
    return knowledge_read_payload(request, coordination_root=str(memory.parent / "coord"))


def _read(
    memory: Path,
    *,
    master_id: str | None = None,
    record_id: str | None = None,
    source_path: str | None = None,
) -> dict[str, Any]:
    answer = _answer(memory, master_id=master_id, record_id=record_id, source_path=source_path)
    assert answer["state"] == "view", answer
    return answer


def _proposed(answer: dict[str, Any]) -> list[tuple[str, list[str]]]:
    return [(row["family"], row["attachedRules"]) for row in answer["rows"]]


def test_the_three_ways_propose_families_with_the_loose_rules_of_their_files(
    memory: Path,
) -> None:
    after_master = _read(memory, master_id=MASTER)
    assert _proposed(after_master) == [
        ("FAM-F00001", ["INV-N00001"]),
        ("FAM-F00003", ["INV-N00002"]),
    ], (
        "only same-file loose rules attach (INV-N00003 in pkg/alpha/d.py does not); read-only INV-B00001 brings no family"
    )
    assert after_master["proposal"]["rulesInNoFamily"] == ["INV-N00001"]

    assert _proposed(_read(memory, record_id="FAM-F00001")) == [("FAM-F00001", ["INV-N00001"])]
    assert _proposed(_read(memory, source_path="pkg/gamma")) == [("FAM-F00003", ["INV-N00002"])]

    oldest = _read(memory)
    assert [row["family"] for row in oldest["rows"]] == ["FAM-F00003", "FAM-F00001", "FAM-F00002"]
    assert "lastConsolidated" in oldest["rows"][2]
    assert oldest["counts"] == {
        "families": 3,
        "rowsReturned": 3,
        "attachedRules": 2,
        "rulesInNoFamily": 0,
    }
    report = json.loads(Path(oldest["report"]).read_text(encoding="utf-8"))
    assert [row["family"] for row in report["families"]] == [
        "FAM-F00003",
        "FAM-F00001",
        "FAM-F00002",
    ]


def test_a_read_naming_two_subjects_or_an_unknown_master_is_refused(memory: Path) -> None:
    both = _answer(memory, master_id=MASTER, record_id="FAM-F00001")
    unknown = _answer(memory, master_id="261112-NONE")
    assert (both["state"], both["refusalCode"]) == ("refused", "bad_selector")
    assert (unknown["state"], unknown["refusalCode"]) == ("refused", "selector_absent")
