"""Which seat a passed memory round names as its hand-over: the one registered on the leaf.

The receipts a breaking helper wrote (KNO-L3 round 5): a receipt that names the leaf only by path,
another repository, or a path in another spelling; one seat in two files; receipt values no stamp
can hold. Each reads through :func:`registered_handover_stamp`, the one function that decides it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.writer_stamps import StampOwner
from agents_remember.worktrees.review_handover import (
    NO_REGISTERED_HANDOVER,
    registered_handover_stamp,
    several_registered,
)

AT = "2026-11-20T06:00:00+01:00"
LEAF = TaskDocumentRef(repository="agents-remember", path="review-api/review_api.json")
OWNER = StampOwner(task="260101-MAS", kind="leaf", owner_id="260101-MAS-L1")


def _receipt(request_id: str, ref: Any, **values: Any) -> dict[str, Any]:
    return {
        "schema": "ar-role-execution/v1",
        "requestId": request_id,
        "role": values.pop("role", "curator"),
        "status": values.pop("status", "running"),
        "selection": {"taskDocumentRef": ref},
        "agent": {"id": "codex", "model": values.pop("model", "m1"), "effort": "xhigh"},
        **values,
    }


def _handed(tmp_path: Path, *receipts: dict[str, Any]) -> dict[str, Any]:
    holder = tmp_path / "review-api/notes/reports/paseo-native-executions"
    holder.mkdir(parents=True)
    for number, receipt in enumerate(receipts):
        (holder / f"receipt-{number}.json").write_text(json.dumps(receipt), encoding="utf-8")
    stamp = registered_handover_stamp(tmp_path / "review-api", LEAF, OWNER, AT)
    return stamp.to_document()


OWN = {"repository": "agents-remember", "path": "review-api/review_api.json"}


@pytest.mark.parametrize(
    "ref",
    [
        {"repository": "some-other-repo", "path": "review-api/review_api.json"},
        {"path": "review-api/review_api.json"},
        {"repository": "agents-remember", "path": "other/review_api.json"},
        "review-api/review_api.json",
        None,
    ],
    ids=["other-repository", "no-repository", "other-path", "text", "none"],
)
def test_a_receipt_that_does_not_name_the_leaf_is_no_hand_over(tmp_path: Path, ref: Any) -> None:
    handed = _handed(tmp_path, _receipt("req-other", ref))
    assert handed["seat"] == "unknown"
    assert set(handed["unknown"].values()) == {NO_REGISTERED_HANDOVER}


def test_a_foreign_receipt_beside_the_real_one_leaves_the_real_one_named(tmp_path: Path) -> None:
    foreign = {"repository": "some-other-repo", "path": "review-api/review_api.json"}
    handed = _handed(tmp_path, _receipt("req-real", OWN), _receipt("req-foreign", foreign))
    assert (handed["seat"], handed["at"]) == ("req-real", AT)


@pytest.mark.parametrize(
    "path",
    [
        "review-api//review_api.json",
        "review-api\\review_api.json",
        " review-api/review_api.json",
        "review-api/review_api.json/",
    ],
)
def test_a_path_the_product_reads_as_the_leafs_names_the_leaf(tmp_path: Path, path: str) -> None:
    """Compared as the product reads a reference: where it reads as the leaf's, it is the leaf."""

    ref = {"repository": "agents-remember", "path": path}
    try:
        names_leaf = TaskDocumentRef.model_validate(ref) == LEAF
    except ValueError:
        names_leaf = False
    handed = _handed(tmp_path, _receipt("req-variant", ref))
    assert handed["seat"] == ("req-variant" if names_leaf else "unknown")


def test_one_seat_in_two_receipt_files_is_one_seat(tmp_path: Path) -> None:
    handed = _handed(tmp_path, _receipt("req-dup", OWN), _receipt("req-dup", OWN))
    assert handed["seat"] == "req-dup"


def test_two_seats_are_named_as_several(tmp_path: Path) -> None:
    handed = _handed(tmp_path, _receipt("req-a", OWN), _receipt("req-b", OWN, role="consolidator"))
    assert set(handed["unknown"].values()) == {
        several_registered(["curator req-a", "consolidator req-b"])
    }


@pytest.mark.parametrize(
    "values",
    [
        {"model": " opus "},
        {"model": "unknown"},
        {"model": "x" * 1025},
        {"requestId": "unknown"},
        {"requestId": "  "},
        {"status": ["running"]},
        {"status": {"state": "running"}},
    ],
    ids=[
        "padded-model",
        "unknown-model",
        "long-model",
        "unknown-seat",
        "blank-seat",
        "list-status",
        "object-status",
    ],
)
def test_a_receipt_value_no_stamp_can_hold_reads_unknown_and_never_refuses(
    tmp_path: Path, values: dict[str, Any]
) -> None:
    """The round is still recorded: a stamp is never refused for its receipt."""

    receipt = _receipt(values.pop("requestId", "req-odd"), OWN, **values)
    handed = _handed(tmp_path, receipt)
    assert handed["at"] == AT
    assert handed["leaf"] == OWNER.owner_id
    assert handed["seat"] in {"req-odd", "unknown"}
    if handed["seat"] == "unknown" or handed["model"] == "unknown":
        assert handed["unknown"]
