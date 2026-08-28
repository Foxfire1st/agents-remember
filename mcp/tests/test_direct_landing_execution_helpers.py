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
from agents_remember.worktrees.integration.direct_landing import (
    direct_landing_recovery_state as recovery_state,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_errors import (
    DirectLandingError,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    CLEAN_STATUS_FINGERPRINT,
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


def _snapshot(*, head: str, clean: bool) -> Any:
    tree = "b" * 40
    return _value(
        headRef="refs/heads/main",
        head=head,
        headTree=tree,
        refLogFingerprint="c" * 64,
        indexTree=tree,
        candidateTree=tree,
        statusFingerprint=CLEAN_STATUS_FINGERPRINT if clean else "d" * 64,
        model_dump=lambda **_kwargs: {"head": head, "clean": clean},
    )


def test_existing_mapping_requires_exact_head_bytes_and_clean_repository() -> None:
    ledger_commit = "a" * 40
    facts = execution._LedgerExecution(
        memory_repo=Path("/tmp/memory"),
        ledger_path=Path("/tmp/memory/ledger.md"),
        code_commit="code",
        memory_commit="memory",
    )
    progress = mock.Mock()
    runtime = _value(
        contract=_value(worktree_group=Path("/tmp/group")),
        record=_value(
            recoveryCommits=_value(
                codeCommit="code",
                memoryContentCommit="memory",
                ledgerCommit="",
            )
        ),
        progress=progress,
        require_input=mock.Mock(),
    )
    assert (
        execution._existing_direct_mapping(
            runtime,
            facts,
            _value(memory_commit="different-memory"),
            current_text="ledger",
            head_text="ledger",
        )
        is None
    )
    with (
        mock.patch.object(execution, "head_commit", return_value=ledger_commit),
        mock.patch.object(
            execution,
            "git_mutation_snapshot",
            return_value=_snapshot(head=ledger_commit, clean=True),
        ),
    ):
        assert (
            execution._existing_direct_mapping(
                runtime,
                facts,
                _value(memory_commit="memory"),
                current_text="ledger",
                head_text="ledger",
            )
            == ledger_commit
        )
    progress.assert_called_once()

    with (
        mock.patch.object(execution, "head_commit", return_value=ledger_commit),
        mock.patch.object(
            execution,
            "git_mutation_snapshot",
            return_value=_snapshot(head=ledger_commit, clean=False),
        ),
        pytest.raises(DirectLandingError, match="exact clean branch HEAD"),
    ):
        execution._existing_direct_mapping(
            runtime,
            facts,
            _value(memory_commit="memory"),
            current_text="ledger",
            head_text="ledger",
        )


def test_existing_mapping_refuses_changed_head_ledger_bytes() -> None:
    runtime = _value(require_input=mock.Mock())
    execution._require_head_ledger_bytes(
        runtime,
        current_text="same",
        head_text="same",
    )
    with pytest.raises(DirectLandingError, match="working ledger differs"):
        execution._require_head_ledger_bytes(
            runtime,
            current_text="working",
            head_text="head",
        )
    runtime.require_input.assert_called_once()


def test_memory_prestate_allows_only_clean_commit_or_prepared_ledger_intent() -> None:
    memory_commit = "a" * 40
    recovery = _value(memoryContentCommit=memory_commit)
    operation_input = _value(memoryBefore=_snapshot(head="b" * 40, clean=True))
    dirty_live = _snapshot(head=memory_commit, clean=False)
    clean_live = _snapshot(head=memory_commit, clean=True)

    assert recovery_state._memory_prestate_converges(
        _value(mutationEvidence={"ledger": _value(state="mutation-intent")}),
        operation_input,
        recovery,
        dirty_live,
    )
    assert not recovery_state._memory_prestate_converges(
        _value(mutationEvidence={"ledger": _value(state="pre-mutation")}),
        operation_input,
        recovery,
        dirty_live,
    )
    assert recovery_state._memory_prestate_converges(
        _value(mutationEvidence={}),
        operation_input,
        recovery,
        clean_live,
    )
