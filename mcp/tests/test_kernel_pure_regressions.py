"""Small deterministic regressions retained after retiring Candidate A."""

from __future__ import annotations

import pytest
from agents_remember.kernel.primitives.gate_policy import coerce_decision_role
from agents_remember.kernel.primitives.gate_vocab import coerce_gate_kind


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
