"""Small deterministic regressions retained after retiring Candidate A."""

from __future__ import annotations

import pytest
from agents_remember.kernel.onboarding_doc import normalize_route
from agents_remember.kernel.primitives.gate_policy import coerce_decision_role
from agents_remember.kernel.primitives.gate_vocab import coerce_gate_kind
from agents_remember.kernel.primitives.identity import stable_provider_id


@pytest.fixture
def provider_id_examples() -> tuple[str, str]:
    """Existing provider-id edge inputs, isolated from provider integration tests."""

    return ("TensorFlow++ Repo", "   ")


def _normalized_provider_ids(values: tuple[str, str]) -> tuple[str, str]:
    first, second = values
    return stable_provider_id(first), stable_provider_id(second)


def test_stable_provider_id_never_returns_empty(
    provider_id_examples: tuple[str, str],
) -> None:
    assert _normalized_provider_ids(provider_id_examples) == ("tensorflow-repo", "repo")


def test_known_gate_kind_passes_through() -> None:
    assert coerce_gate_kind("plan-approval") == "plan-approval"


def test_unknown_gate_kind_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown gate kind"):
        coerce_gate_kind("not-a-kind")


def test_known_decision_role_passes_through() -> None:
    assert coerce_decision_role("manager") == "manager"


def test_unknown_decision_role_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="unknown decision role 'mangaer'"):
        coerce_decision_role("mangaer")


def test_normalize_route_root_forms() -> None:
    for raw in ("", ".", "<repo-root>", "/", "`.`"):
        assert normalize_route(raw) == "."


def test_normalize_route_strips_slashes_and_backticks() -> None:
    assert normalize_route("`mcp/src/`") == "mcp/src"
