"""Which log records may prove a dispatch was accepted, per harness.

Acceptance is decided from harness-owned session JSONL, so the reader for each harness has
to say exactly which record is a *developer submission* and which is bookkeeping that merely
quotes one. A delivery id echoed by the assistant, replayed inside a meta record, or carried
by a tool-result envelope is not evidence the message was submitted -- accepting any of them
would let the seat certify a dispatch it never delivered. These tests pin the negative side
of each reader, plus the harness table itself: a harness with no reader accepts nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agents_remember.serving.harness_logs import HarnessSessionLog

STARTED_AT = datetime.now(UTC) - timedelta(seconds=5)
ENTRY_ID = "E-READER"
DELIVERY = f"[Agents Remember delivery:brief id={ENTRY_ID}]\n\nwork"


def _write(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")


def _log(harness: str, cwd: Path, path: Path, tmp_path: Path) -> HarnessSessionLog:
    return HarnessSessionLog(
        harness=harness,
        cwd=cwd,
        started_at=STARTED_AT,
        bound_path=path,
        claude_root=tmp_path / "claude",
        codex_root=tmp_path / "codex",
    )


def _workspace(tmp_path: Path) -> Path:
    cwd = (tmp_path / "workspace").resolve()
    cwd.mkdir(exist_ok=True)
    return cwd


def test_claude_non_submission_records_never_prove_delivery(tmp_path: Path) -> None:
    """Only a real developer-typed ``user`` message counts -- not an echo of it.

    Every record below contains the delivery id verbatim and none of them is a submission:
    the assistant quoting it back, a meta record replaying it, a ``user`` record whose
    payload is not a message object, and a tool-result envelope carrying an assistant role.
    """

    cwd = _workspace(tmp_path)
    path = tmp_path / "claude" / str(cwd).replace("/", "-") / "session.jsonl"
    echoes: list[dict[str, object]] = [
        {
            "type": "assistant",
            "cwd": str(cwd),
            "message": {"role": "assistant", "content": DELIVERY},
        },
        {
            "type": "user",
            "cwd": str(cwd),
            "isMeta": True,
            "message": {"role": "user", "content": DELIVERY},
        },
        {"type": "user", "cwd": str(cwd), "message": DELIVERY},
        {"type": "user", "cwd": str(cwd), "message": {"role": "assistant", "content": DELIVERY}},
    ]
    _write(path, echoes)
    log = _log("claude", cwd, path, tmp_path)
    assert not log.message_present(ENTRY_ID)

    _write(
        path,
        [
            *echoes,
            {"type": "user", "cwd": str(cwd), "message": {"role": "user", "content": DELIVERY}},
        ],
    )
    assert log.message_present(ENTRY_ID)


def test_codex_accepts_the_event_msg_envelope_of_the_same_submission(tmp_path: Path) -> None:
    """Codex writes one submission twice; the ``event_msg`` copy alone is enough evidence."""

    cwd = _workspace(tmp_path)
    path = tmp_path / "codex" / "rollout.jsonl"
    _write(
        path,
        [
            {"type": "session_meta", "payload": {"cwd": str(cwd)}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": DELIVERY}},
        ],
    )
    assert _log("codex", cwd, path, tmp_path).message_present(ENTRY_ID)


def test_codex_non_submission_records_never_prove_delivery(tmp_path: Path) -> None:
    """A payload-less record, an assistant reply and a structured ``user_message`` are not proof.

    The last one is the shape guard that matters: ``event_msg``/``user_message`` is only read
    when its ``message`` is the submitted string. A structured body is some other event with
    the same envelope, so quoting the delivery id inside it must not certify acceptance.
    """

    cwd = _workspace(tmp_path)
    path = tmp_path / "codex" / "rollout.jsonl"
    _write(
        path,
        [
            {"type": "session_meta", "payload": {"cwd": str(cwd)}},
            {"type": "response_item", "payload": DELIVERY},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": DELIVERY}],
                },
            },
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": {"text": DELIVERY}},
            },
        ],
    )
    assert not _log("codex", cwd, path, tmp_path).message_present(ENTRY_ID)


def test_a_harness_without_a_reader_accepts_nothing(tmp_path: Path) -> None:
    """An unregistered harness has no truthful reader, so its log proves nothing.

    The cwd guard still matches -- the file really is this seat's session log -- and the
    submission text is right there in it. Acceptance still fails closed, because reading a
    third vendor's records with a borrowed reader is how a seat certifies the wrong message.
    """

    cwd = _workspace(tmp_path)
    path = tmp_path / "other" / "rollout.jsonl"
    _write(
        path,
        [
            {"type": "session_meta", "payload": {"cwd": str(cwd)}},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": DELIVERY}],
                },
            },
        ],
    )
    log = _log("gemini", cwd, path, tmp_path)
    assert not log.message_present(ENTRY_ID)
    assert not log.command_evidence_supported
