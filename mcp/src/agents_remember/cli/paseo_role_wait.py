"""The wait of a role message: the outcome of the turn that consumed it.

A sequence of bounded bridge calls inside one tool call. The bridge follows the message, so each
call names the turn that holds it now; nothing here polls outside a call and nothing is stored.
The answer is a plain mapping of the fields the role-message tool adds to its result.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from agents_remember.cli.orca_task_preparation import ROLE_MESSAGE_TOOL
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

# One bridge call of a wait: well inside the limit every bridge call has to end within.
WAIT_SLICE_SECONDS = 40

_monotonic = time.monotonic


@dataclass(frozen=True, slots=True)
class SentMessage:
    """A delivered message: its recipient, its id, and the turn that took it when one ran.

    ``steered`` says that the message was handed to a turn that was already running. Such a
    message is consumed by that turn or by the one that follows it; a message that began its
    turn is consumed by that turn.
    """

    agent_id: str
    message_id: str
    turn_id: str | None
    steered: bool


READ_LATER = (
    "The message stays delivered; the reply must be read later: the recipient can answer with "
    f"{ROLE_MESSAGE_TOOL}, or ask it again."
)


# What a text rests on when it is the text of the turn that was running as the message arrived.
RUNNING_TURN_NOTE = (
    "The recipient was mid-turn when the message arrived, and this is the text of that turn: if "
    "it does not answer the message, the reply comes in the recipient's next turn and must be "
    "read later. "
)


def wait_for_turn(
    config: McpRuntimeConfig, sent: SentMessage, timeout_seconds: int
) -> dict[str, Any]:
    """Wait for the turn that consumed the message, as a sequence of bounded bridge calls.

    The bridge follows the message: a recipient can take a message that was handed to its
    running turn up only in the turn after it, and each answer names the turn that holds the
    message now. When the bridge cannot say which turn consumed the message, the call answers
    ``accepted`` and returns no text.
    """

    started = _monotonic()
    deadline = started + timeout_seconds
    turn_id = sent.turn_id

    def waited() -> int:
        return int(_monotonic() - started)

    def later(reason: str) -> dict[str, Any]:
        return {
            "status": "timeout",
            "detail": f"{reason} {READ_LATER}",
            "waitedSeconds": waited(),
        }

    while True:
        remaining = deadline - _monotonic()
        if remaining <= 0:
            return later(f"The recipient's turn did not end within {timeout_seconds} seconds.")
        try:
            reply = bridge_call(
                config,
                "agent-wait",
                {
                    "agentId": sent.agent_id,
                    "messageId": sent.message_id,
                    **({"turnId": turn_id} if turn_id else {}),
                    **({"steered": True} if sent.steered else {}),
                    "waitMs": max(1, int(min(remaining, WAIT_SLICE_SECONDS) * 1000)),
                },
            )
        except PaseoBridgeFailure as error:
            return later(f"The wait ended early because the host gave no answer ({error.code}).")
        answer = reply.get("wait")
        state = answer.get("state") if isinstance(answer, dict) else None
        if isinstance(answer, dict) and state == "running":
            followed = answer.get("turnId")
            turn_id = followed if isinstance(followed, str) and followed else turn_id
            continue
        if isinstance(answer, dict) and state == "undecided":
            # No text is returned that cannot be said to answer the message.
            return {
                "status": "accepted",
                "detail": "The message was delivered, but the turn that consumed it could not be "
                f"told from the recipient's timeline ({answer.get('reason')}). {READ_LATER}",
                "waitedSeconds": waited(),
            }
        if not isinstance(answer, dict) or state not in {"permission", "ended", "unavailable"}:
            return later("The wait ended early because the host's answer could not be read.")
        return {**_turn_result(answer, sent.steered), "waitedSeconds": waited()}


def _turn_result(answer: dict[str, Any], steered: bool) -> dict[str, Any]:
    """The result of a wait that ended: the turn's outcome, or the pending permission.

    A finished turn that was already running when the message arrived (``steered``, and no later
    turn was followed) may not have read the message: the detail says what the text rests on.
    """

    state = answer["state"]
    if state == "permission":
        name = str(answer.get("permission") or "a tool")
        return {
            "status": "permission-pending",
            "detail": f"The recipient waits for a permission decision: {name}. The developer "
            "answers it in the recipient's chat.",
            "permission": name,
        }
    if state == "unavailable":
        return {
            "status": "turn-cancelled",
            "detail": "The recipient's turn did not finish: during the wait the agent became "
            f"{_UNAVAILABLE.get(str(answer.get('reason')), 'unavailable')}.",
        }
    text = answer.get("text")
    reply = {
        "text": text if isinstance(text, str) else None,
        "textTruncated": answer.get("textTruncated") is True,
    }
    outcome = answer.get("outcome")
    if outcome == "finished":
        running_turn = steered and answer.get("laterTurn") is not True
        return {
            "status": "turn-finished",
            "detail": "The recipient's turn finished; text is its final text. "
            f"{RUNNING_TURN_NOTE if running_turn else ''}"
            "A finished turn is not AR acceptance of any requirement.",
            **reply,
        }
    if outcome == "failed":
        return {
            "status": "turn-failed",
            "detail": f"The recipient's turn failed: {answer.get('error') or 'no reason given'}",
            **reply,
        }
    return {
        "status": "turn-cancelled",
        "detail": "The recipient's turn was cancelled before it gave a final text.",
        **reply,
    }


_UNAVAILABLE = {
    "not-found": "unknown to the host",
    "archived": "archived",
    "closed": "a closed session",
}


__all__ = ["READ_LATER", "WAIT_SLICE_SECONDS", "SentMessage", "wait_for_turn"]
