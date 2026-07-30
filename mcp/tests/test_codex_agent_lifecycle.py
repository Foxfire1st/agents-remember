from __future__ import annotations

import pytest
from agents_remember.serving.codex_agent_lifecycle import completed_turn_status


@pytest.mark.parametrize(
    ("native_status", "roster_status"),
    (
        ("completed", "completed"),
        ("interrupted", "interrupted"),
        ("cancelled", "interrupted"),
        ("failed", "failed"),
        ("errored", "failed"),
    ),
)
def test_completed_turn_status_uses_roster_vocabulary(
    native_status: str,
    roster_status: str,
) -> None:
    assert completed_turn_status(native_status) == roster_status
