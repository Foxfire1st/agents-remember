"""Focused proof for direct-landing ledger commit recovery helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.worktrees.integration.direct_landing import (
    direct_landing_execution as execution,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_errors import (
    DirectLandingError,
)


def _value(**fields: object) -> Any:
    return cast(Any, SimpleNamespace(**fields))


def test_advanced_ledger_commit_runs_every_exact_proof_stage() -> None:
    before = _value(head="before")
    evidence = _value(before=before)
    facts = _value()
    intent = _value()
    current = _value(head="after")
    proven = _value(state="proven")
    with (
        mock.patch.object(execution, "_require_single_clean_ledger_commit") as clean,
        mock.patch.object(execution, "_head_ledger", return_value=("text", "ledger")),
        mock.patch.object(execution, "_require_intended_ledger_mapping") as mapping,
        mock.patch.object(execution, "_proven_ledger_evidence", return_value=proven),
    ):
        assert (
            execution._prove_advanced_ledger_commit(_value(), facts, intent, evidence, current)
            is proven
        )
    clean.assert_called_once_with(facts, before, current)
    mapping.assert_called_once_with(facts, intent, "text", "ledger")


def test_single_clean_ledger_commit_and_mapping_fail_closed() -> None:
    repository = Path("/tmp/memory")
    facts = _value(memory_repo=repository, code_commit="code", memory_commit="memory")
    before = _value(head="before", headRef="refs/heads/main")
    current = _value(
        head="after",
        headRef="refs/heads/main",
        indexTree="tree",
        candidateTree="tree",
        headTree="tree",
        statusFingerprint=hashlib.sha256(b"").hexdigest(),
        model_dump=lambda **_kwargs: {"head": "after"},
    )
    with mock.patch.object(execution, "require_git", return_value="before"):
        execution._require_single_clean_ledger_commit(facts, before, current)
        current.statusFingerprint = "drift"
        with pytest.raises(DirectLandingError, match="one clean commit"):
            execution._require_single_clean_ledger_commit(facts, before, current)

    intent = _value(intendedText="ledger text", intendedSha256="a" * 64)
    mapping = _value(memory_commit="memory")
    with mock.patch.object(execution, "find_mapping", return_value=mapping):
        execution._require_intended_ledger_mapping(facts, intent, "ledger text", _value())
    with (
        mock.patch.object(execution, "find_mapping", return_value=None),
        pytest.raises(DirectLandingError, match="exact intended mapping"),
    ):
        execution._require_intended_ledger_mapping(facts, intent, "other", _value())
