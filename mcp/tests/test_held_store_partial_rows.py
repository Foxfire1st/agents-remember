"""A partly malformed held store never replaces a complete earlier reading (L190-R1-F02).

The sealed finding's counterexample: a warm store read as (valid A, malformed row) must not replace
the complete (A, B) the reader already held, because B has neither been delivered nor withdrawn --
dropping it reports an end the store never stated. The reader may only replace a store's rows with a
read it could understand whole; a partly understood read keeps the last rows and says it is
incomplete.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_remember.cli.role_held_messages import HeldMessage, hold_message
from agents_remember.controlplane import held_store_layout as layout

AGENT = "22222222-2222-4222-8222-222222222222"
SENDER = "44444444-4444-4444-8444-444444444444"
RECEIPTS = Path("tasks") / "repo" / "master" / "notes" / "reports" / "paseo-native-executions"


def _root(tmp_path: Path) -> Path:
    return tmp_path / "coordination"


def _folder(tmp_path: Path) -> Path:
    folder = tmp_path / "worktrees" / "leaf"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _receipt(root: Path, folder: Path, agent_id: str) -> Path:
    path = root / RECEIPTS / "receipt.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "ar-role-execution/v1",
                "workspace": {"path": str(folder)},
                "execution": {"agentId": agent_id},
            }
        ),
        encoding="utf-8",
    )
    return path


def _queue(root: Path) -> Path:
    path = root / "temp" / layout.HELD_QUEUE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": layout.HELD_QUEUE_SCHEMA, "entries": {}}), encoding="utf-8"
    )
    return path


def _hold(folder: Path, agent_id: str, text: str) -> dict[str, Any]:
    message = HeldMessage(
        sender_line="Manager:",
        sender_agent_id=SENDER,
        text=text,
        reason="recipient in a turn the host cannot write into",
    )
    return hold_message(folder, agent_id, message)


def _corrupt_one_row(folder: Path, agent_id: str, keep: str) -> list[str]:
    """Leave the store holding one valid row plus a row the product could not have written."""

    path = layout.store_path_for(folder, agent_id)
    body = json.loads(path.read_text(encoding="utf-8"))
    kept = [row for row in body["messages"] if row.get("id") == keep]
    body["messages"] = [*kept, "not a row the product could write"]
    path.write_text(json.dumps(body), encoding="utf-8")
    return [row["id"] for row in kept]


def test_a_partly_malformed_store_keeps_the_complete_reading_it_had(tmp_path: Path) -> None:
    root, folder = _root(tmp_path), _folder(tmp_path)
    first = _hold(folder, AGENT, "held-one")
    second = _hold(folder, AGENT, "held-two")
    _receipt(root, folder, AGENT)
    _queue(root)

    reader = layout.HeldAuthorityReader()
    warm = reader.read(root)
    assert warm.complete is True
    assert {row.message_id for row in warm.rows} == {
        first["heldMessageId"],
        second["heldMessageId"],
    }, "both held rows reach the view from one complete read"

    kept = _corrupt_one_row(folder, AGENT, first["heldMessageId"])
    assert kept == [first["heldMessageId"]], "the fixture leaves exactly one valid row"

    torn = reader.read(root)

    assert torn.complete is False, "a store with a row the product could not write is incomplete"
    assert {row.message_id for row in torn.rows} == {
        first["heldMessageId"],
        second["heldMessageId"],
    }, "the malformed read neither drops the valid row nor silently withdraws the other one"
