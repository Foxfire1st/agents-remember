"""The answer's dashboardAddress is answered only when something serves it (L188-B24, 7c1153ee).

MIK-R196 rule 1 has the one call return the address at which the user continues setup. On the cold
install the host stage started the daemon on the block's ``listen`` (8766) and nothing on the embed
``dashboardOrigin`` (8765), so the answer published a URL that answered nothing. These tests pin the
two halves of the repair: the address is answered after the product's own dashboard owner has been
asked and the origin answers, and when it does not answer the answer carries the reason instead.
"""

from __future__ import annotations

import types
from typing import Any

import pytest
from agents_remember.cli import dashboard_address as owner

PORT = 8765


def _config() -> Any:
    return types.SimpleNamespace(dashboard=types.SimpleNamespace(port=PORT))


def test_an_address_is_answered_only_after_the_origin_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[Any] = []
    outcome = types.SimpleNamespace(action="started", detail="a dashboard was started")
    monkeypatch.setattr(
        "agents_remember.serving.daemon.ensure", lambda *a, **k: asked.append(a) or outcome
    )
    monkeypatch.setattr(owner, "_origin_answer", lambda host, port: (True, "HTTP 200"))

    answer = owner.dashboard_address_for(_config())

    assert asked, "the dashboard owner is asked before the address is answered"
    assert answer == {"dashboardAddress": f"http://127.0.0.1:{PORT}/"}


def test_a_dashboard_that_does_not_answer_is_reported_not_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome = types.SimpleNamespace(action="failed", detail="nothing came up")
    monkeypatch.setattr("agents_remember.serving.daemon.ensure", lambda *a, **k: outcome)
    monkeypatch.setattr(
        owner, "_origin_answer", lambda host, port: (False, "ConnectionRefusedError")
    )

    answer = owner.dashboard_address_for(_config())

    assert answer["dashboardAddress"] is None, "a dead pointer is never published as the address"
    detail = answer["dashboardAddressDetail"]
    assert f"http://127.0.0.1:{PORT}/" in detail and "failed" in detail
    assert "no dashboard answers" in detail


def test_a_dashboard_owner_that_raises_is_reported_the_same_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("the dashboard owner is unavailable")

    monkeypatch.setattr("agents_remember.serving.daemon.ensure", broken)
    monkeypatch.setattr(
        owner, "_origin_answer", lambda host, port: (False, "ConnectionRefusedError")
    )

    answer = owner.dashboard_address_for(_config())

    assert answer["dashboardAddress"] is None
    assert "OSError" in answer["dashboardAddressDetail"]


def test_a_page_that_is_not_served_is_not_a_continuation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 221: a 503 "no cockpit bundle" page is the origin speaking, not serving."""

    outcome = types.SimpleNamespace(action="adopted", detail="this world's dashboard")
    monkeypatch.setattr("agents_remember.serving.daemon.ensure", lambda *a, **k: outcome)
    monkeypatch.setattr(owner, "_origin_answer", lambda host, port: (False, "HTTP 503"))

    answer = owner.dashboard_address_for(_config())

    assert answer["dashboardAddress"] is None
    assert "HTTP 503" in answer["dashboardAddressDetail"]


def test_an_owner_that_failed_is_not_published_over_a_listening_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The review of 221: a TCP accept must not carry an ensure failure through as an address."""

    outcome = types.SimpleNamespace(action="failed", detail="the dashboard never came up")
    monkeypatch.setattr("agents_remember.serving.daemon.ensure", lambda *a, **k: outcome)
    monkeypatch.setattr(owner, "_origin_answer", lambda host, port: (True, "HTTP 200"))

    answer = owner.dashboard_address_for(_config())

    assert answer["dashboardAddress"] is None
    assert "'failed'" in answer["dashboardAddressDetail"]
    assert "HTTP 200" in answer["dashboardAddressDetail"]
