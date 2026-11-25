"""The writer's stamp, the history file's product parts, the receipt facts.

The stamp names the writer of a revision; an unresolved value reads ``unknown`` only with its
reason. The history file's ``written`` and ``review`` parts are absent where empty, so a file written
before them keeps its bytes. The receipt reader takes harness, model and effort from a seat's launch
receipt, the host's observed value first, and makes a missing receipt an explained ``unknown``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agents_remember.models.knowledge_files import canonical_text, parse_history_document
from agents_remember.models.knowledge_files.records import InvariantRecord, record_status
from agents_remember.models.knowledge_files.writer_stamp import UNKNOWN, WriterStamp
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.writer_stamps import (
    StampOwner,
    StampRequest,
    leaf_role_receipt,
    receipt_of_request,
    stamp,
)
from pydantic import ValidationError

HISTORY_PATH = "knowledge/history/260101-MAS-L1.json"
LEAF_REF = TaskDocumentRef(repository="repo", path="260101_master/01_leaf.json")
STAMP: dict[str, Any] = {
    "by": "agent",
    "role": "curator",
    "harness": "codex",
    "model": "gpt-6.2-sol",
    "effort": "high",
    "seat": "11111111-1111-4111-8111-111111111111",
    "task": "260101-MAS",
    "leaf": "260101-MAS-L1",
    "at": "2026-11-19T20:00:00+01:00",
}


def _receipt(request_id: str, role: str, **agent: str) -> dict[str, Any]:
    return {
        "schema": "ar-role-execution/v1",
        "requestId": request_id,
        "role": role,
        "selection": {"role": role, "taskDocumentRef": LEAF_REF.model_dump(mode="json")},
        "agent": agent,
    }


def _write_receipt(folder: Path, name: str, receipt: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(receipt), encoding="utf-8")


def test_an_unknown_stamp_value_is_refused_without_its_reason() -> None:
    explained = WriterStamp.model_validate(
        {**STAMP, "model": UNKNOWN, "unknown": {"model": "the launch receipt does not hold it"}}
    )
    assert explained.model == UNKNOWN
    with pytest.raises(ValidationError, match="needs its reason"):
        WriterStamp.model_validate({**STAMP, "model": UNKNOWN})
    with pytest.raises(ValidationError, match="explains only fields"):
        WriterStamp.model_validate({**STAMP, "unknown": {"model": "no reason to give"}})


def test_only_a_product_or_conversion_stamp_names_an_operation() -> None:
    with pytest.raises(ValidationError, match="operation"):
        WriterStamp.model_validate({**STAMP, "operation": "closeout"})
    with pytest.raises(ValidationError, match="operation"):
        WriterStamp.model_validate({**STAMP, "by": "product"})
    product = WriterStamp.model_validate({**STAMP, "by": "product", "operation": "closeout"})
    assert product.writer_key() == ("curator", "codex", "gpt-6.2-sol")


def test_history_parts_round_trip_and_stay_absent_when_empty() -> None:
    old = canonical_text(
        {"schema": "ar-history/v1", "leaf": "260101-MAS-L1", "closed": True, "rows": []}
    )
    assert canonical_text(parse_history_document(HISTORY_PATH, old).to_document()) == old

    document = {
        "schema": "ar-history/v1",
        "leaf": "260101-MAS-L1",
        "closed": True,
        "rows": [],
        "written": [{"record": "INV-ABC123", "revision": 2, "writer": STAMP}],
        "review": {
            "round": 2,
            "verdictRef": "notes/reports/reviewer/verdict-2.md",
            "tree": "a" * 40,
            "reviewer": {**STAMP, "role": "reviewer"},
            "handedOverBy": STAMP,
        },
    }
    history = parse_history_document(HISTORY_PATH, canonical_text(document))
    assert history.written_revision("INV-ABC123", 2) is not None
    assert history.written_revision("INV-ABC123", 1) is None
    assert canonical_text(history.to_document()) == canonical_text(document)
    with pytest.raises(ValidationError, match="written revisions"):
        parse_history_document(
            HISTORY_PATH, canonical_text({**document, "written": document["written"] * 2})
        )


def test_an_old_state_word_still_parses_and_means_nothing() -> None:
    record = {
        "schema": "ar-invariant/v1",
        "id": "INV-ABC123",
        "revision": 1,
        "status": "accepted",
        "statement": "A statement.",
        "applicability": "Everywhere.",
        "conditions": [],
        "exclusions": [],
        "supersedes": [],
        "admission": "legacy-unassessed",
        "origin": {"task": "260101-MAS", "leaf": "260101-MAS-L1"},
    }
    assert record_status(InvariantRecord.model_validate(record)) == ""
    unstated = {key: value for key, value in record.items() if key != "status"}
    assert record_status(InvariantRecord.model_validate(unstated)) == ""
    assert (
        record_status(InvariantRecord.model_validate({**record, "status": "retired"})) == "retired"
    )


def test_receipt_values_prefer_the_hosts_observation_and_explain_what_is_missing(
    tmp_path: Path,
) -> None:
    holder = tmp_path / "notes/reports/paseo-native-executions"
    seat = "22222222-2222-4222-8222-222222222222"
    observed = _receipt(seat, "worker", id="claude", model="requested-model")
    observed["choices"] = {"model": {"value": "requested-model", "observedValue": "served-model"}}
    _write_receipt(holder / "history", f"{seat}.json", observed)
    _write_receipt(holder, "curator-x.json", _receipt("c", "curator", id="codex", model="m"))

    owner = StampOwner(task="260101-MAS", kind="leaf", owner_id="260101-MAS-L1")
    request = StampRequest(by="agent", role="worker", seat=seat, owner=owner, at="t")
    worker = stamp(request, receipt_of_request(tmp_path, seat))
    assert (worker.harness, worker.model, worker.effort) == ("claude", "served-model", UNKNOWN)
    assert worker.unknown == {"effort": "the launch receipt does not hold the effort"}

    curator = leaf_role_receipt(tmp_path, LEAF_REF, "curator")
    assert curator is not None and curator["requestId"] == "c"
    assert leaf_role_receipt(tmp_path, LEAF_REF, "consolidator") is None

    lost = stamp(request, receipt_of_request(tmp_path, "33333333-3333-4333-8333-333333333333"))
    assert (lost.harness, lost.model, lost.effort) == (UNKNOWN, UNKNOWN, UNKNOWN)
    assert set(lost.unknown or {}) == {"harness", "model", "effort"}
