from __future__ import annotations

from agents_remember.serving.harness_adapters import (
    CLAUDE_CODE_ADAPTER,
    CODEX_ADAPTER,
    GENERIC_ADAPTER,
    get_adapter,
)


def test_known_and_generic_adapters_are_stable() -> None:
    assert get_adapter("claude") is CLAUDE_CODE_ADAPTER
    assert get_adapter("codex") is CODEX_ADAPTER
    assert get_adapter(None) is GENERIC_ADAPTER
    assert get_adapter("future").harness_id == "future"


def test_blocked_reason_is_failure_diagnostic_only() -> None:
    assert CODEX_ADAPTER.blocked_reason("Approaching rate limits — switch model?") == (
        "codex-quota-limit"
    )
    assert CLAUDE_CODE_ADAPTER.blocked_reason("Do you want to proceed? (y/n)") == (
        "permission-prompt"
    )
    assert CLAUDE_CODE_ADAPTER.blocked_reason("ordinary transcript") is None
