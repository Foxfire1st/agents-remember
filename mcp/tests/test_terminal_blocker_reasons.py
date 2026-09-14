"""A cleanup blocker always names its reason, and a torn-down provider never invents one.

The defect this lane pins: ``lifecycle_finalize_task`` on leaf L6's enclosure returned
``state: cleanup-blocked`` with ``blockers: [{"provider": "providerRuntime", "reason": null}]``
-- while the same payload proved the terminal archive, reported the providers ``torn-down``, and
said in its own summary that enclosure deletion may continue. Cleanup stopped on a blockage that
named no reason, preserved the citation-source index with ``terminal-operation-failed``, and left
the leaf un-finalized; an immediate retry then reclaimed everything. A blockage an operator cannot
read cannot be told apart from a spurious one, so the contract is now explicit in two places:

* ``terminal_validation._blocker`` is the only way a terminal result reports a blockage, and it
  refuses -- raising at the detecting call site -- unless both the component and a non-empty
  reason are present;
* ``provider_runtime.remove_tree`` answers with a reason whenever it reclaimed nothing, so the
  provider runtime result cannot be blocked without saying why.

The cases below drive the real application tools over real Git repositories and real worktrees.
The first two reproduce the L6 shape and its genuine counterpart at the whole-tool boundary; the
last three pin the two invariant owners directly, including the reasonless result no current
producer can emit and the empty reason one still can.
"""

from __future__ import annotations

import os
import shutil
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.provider_runtime import remove_tree
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.memory_quality.style.citations.source_index_cache import (
    TerminalNamespaceGuard,
)
from agents_remember.tasks import read_task_doc
from agents_remember.tasks.leaf_doc import resolve_terminal_leaf_doc
from agents_remember.worktrees.modules.terminal_validation import (
    TerminalResult,
    _blocker,
    terminal_result_blockers,
)
from agents_remember.worktrees.services import bind_worktree_services, reset_worktree_services
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from integration_branch_authority_test_support import (
    _authority_fixture,
    _closed_external_leaf_worktrees,
)
from test_transaction_only_worktree_delivery import _public_config

pytestmark = pytest.mark.integration

PROVIDER_RUNTIME_SETUP_FILE = "setup-progress.json"


class _NoManagedCitationCache:
    """The citation guard port answering with production's own no-authority guard.

    The fixture leaf has no lifecycle-bound managed cache namespace, which is exactly the case
    the production guard resolves by yielding an authority-free guard. Only the cache reservation
    is a double, and it is the same value production yields.
    """

    def guard(self, contract: WorktreeContract, *, requested_contract_path: Path):
        del contract, requested_contract_path
        return nullcontext(TerminalNamespaceGuard(None, None, None))


@pytest.fixture
def bound_worktree_services():
    """The bound services this module patches, so its doubles are the ones under test."""

    services = replace(build_default_worktree_services(), citation_guard=_NoManagedCitationCache())
    bind_worktree_services(services)
    try:
        yield services
    finally:
        reset_worktree_services()


def _landed_leaf(tmp_path: Path) -> WorktreeContract:
    fixture = _authority_fixture(tmp_path, external_memory=True)
    return _closed_external_leaf_worktrees(fixture, tmp_path, publish_closeout_evidence=True)


def _landed_leaf_config(tmp_path: Path, contract: WorktreeContract):
    """The MCP authority for a fixture leaf whose landing has already been applied."""

    config = _public_config(tmp_path, contract)
    applied = worktree_tools.worktree_integrate_tool(
        replace(config, retirement=replace(config.retirement, auto_land_on_integration=True)),
        contract_path=contract.contract_path.as_posix(),
        strategy="ff-only",
        dry_run=False,
    )
    assert applied["state"] == "integrated", applied
    return config


def _landed_leaf_document(contract: WorktreeContract):
    resolved = resolve_terminal_leaf_doc(contract.task_root, contract.leaf_id)
    assert resolved is not None, "the fixture leaf must own a task document"
    return read_task_doc(resolved[0])


def _finalize(config, contract: WorktreeContract):
    return worktree_tools.lifecycle_finalize_task_tool(
        config,
        contract_path=contract.contract_path.as_posix(),
    )


def test_a_torn_down_provider_runtime_finalizes_on_the_first_call(
    tmp_path: Path, bound_worktree_services
) -> None:
    """The L6 shape finalizes first time: a provider already torn down blocks nothing.

    L6's enclosure reached this exact state -- every provider resource already gone, the terminal
    archive proven, nothing left to reclaim -- and cleanup still stopped on a blockage naming no
    reason, which left the leaf open until a retry. This case pins the operator-visible outcome:
    the first and only call finalizes the edge, and the report says what was reclaimed.
    """

    closed = _landed_leaf(tmp_path)
    config = _landed_leaf_config(tmp_path, closed)
    provider_runtime = closed.worktree_group / "provider-runtime"
    if provider_runtime.exists():
        shutil.rmtree(provider_runtime)
    assert not provider_runtime.exists(), "the L6 shape is a provider runtime already gone"

    torn_down = {
        "state": "torn-down",
        "settingsFound": False,
        "containers": [],
        "networks": [],
        "providerRuntime": {
            "path": provider_runtime.as_posix(),
            "removed": False,
            "reason": "already-absent",
        },
    }
    with mock.patch.object(
        bound_worktree_services.provider_lifecycle, "teardown", return_value=torn_down
    ):
        finalized = _finalize(config, closed)

    assert finalized["state"] == "finalized", finalized
    assert finalized["cleanup"]["state"] == "cleanup-completed", finalized["cleanup"]
    assert finalized["cleanup"]["notRemoved"] == {
        "worktrees": [],
        "localBranches": [],
        "reports": [],
        "enclosureRoot": [],
    }
    assert not closed.code_worktree.exists()
    assert not closed.worktree_group.exists()
    assert load_contract(closed.contract_path).cleanup == "completed"
    assert _landed_leaf_document(closed).status == "Completed"


def test_a_provider_runtime_that_cannot_be_torn_down_blocks_with_its_own_reason(
    tmp_path: Path, bound_worktree_services
) -> None:
    """A genuine reclamation failure refuses, names itself, and no retry turns it into success.

    The provider runtime is left behind by a real permission failure: the host user cannot remove
    the tree and no reclaim image is configured, which is the production failure ``remove_tree``
    already reports a reason for. The refusal must carry that reason, must not close the task
    edge, and must refuse again on the identical retry -- a retry may only converge when it
    re-observes the cause as resolved.
    """

    closed = _landed_leaf(tmp_path)
    config = _landed_leaf_config(tmp_path, closed)
    provider_runtime = closed.worktree_group / "provider-runtime"
    provider_runtime.mkdir(parents=True, exist_ok=True)
    (provider_runtime / PROVIDER_RUNTIME_SETUP_FILE).write_text(
        '{"state": "ok"}\n', encoding="utf-8"
    )
    os.chmod(provider_runtime, 0o500)

    try:
        refused = _finalize(config, closed)
    finally:
        os.chmod(provider_runtime, 0o700)

    assert refused["state"] == "cleanup-blocked", refused
    assert refused["cleanup"]["state"] == "blocked", refused["cleanup"]
    assert refused["cleanup"]["providers"]["providerRuntime"]["removed"] is False
    assert refused["cleanup"]["providers"]["providerRuntime"]["reason"].startswith(
        "permission denied"
    )
    assert refused["cleanup"]["blockers"] == [
        {
            "provider": "providerRuntime",
            "reason": refused["cleanup"]["providers"]["providerRuntime"]["reason"],
        }
    ], refused["cleanup"]["blockers"]

    # Nothing closed and nothing was reclaimed: the edge, its targets and the provider tree
    # are exactly where they were.
    assert _landed_leaf_document(closed).status != "Completed"
    assert load_contract(closed.contract_path).cleanup == "pending"
    assert closed.code_worktree.exists()
    assert provider_runtime.exists()

    # The identical retry re-observes the same cause and refuses the same way; it is not
    # convergence, and it does not become success.
    os.chmod(provider_runtime, 0o500)
    try:
        retried = _finalize(config, closed)
    finally:
        os.chmod(provider_runtime, 0o700)

    assert retried["state"] == "cleanup-blocked", retried
    assert retried["cleanup"]["blockers"] == refused["cleanup"]["blockers"]
    assert _landed_leaf_document(closed).status != "Completed"


def test_a_reasonless_provider_result_is_named_instead_of_becoming_a_null_reason() -> None:
    """No terminal result reaches an operator as a blockage with no reason at all.

    This is the exact shape L6 reported -- a provider runtime that reclaimed nothing and said
    nothing -- and it is the case that produced ``{"provider": "providerRuntime", "reason": null}``.
    Every terminal blocker is built through :func:`_blocker`, which refuses an unnameable reason,
    and the reasonless result is answered in operator language instead of being passed through.
    """

    blockers = terminal_result_blockers(
        TerminalResult(
            providers={"state": "torn-down", "providerRuntime": {"removed": False}},
            worktrees={},
            branches={},
            directories={},
        )
    )

    assert blockers == [
        {"provider": "providerRuntime", "reason": "no reason reported by the terminal result"}
    ]


def test_an_unnameable_blocker_reason_is_refused_at_its_own_source() -> None:
    """A producer whose reason is not operator language is a defect that fails loudly.

    ``_blocked_reason`` answers every reasonless result, so this is the last line: a caller that
    reaches the blocker builder with a reason no operator could read -- ``None``, a blank string,
    a non-string -- is refused at that call site instead of emitting an anonymous blockage.
    """

    for unnameable in (None, "", "   ", 17):
        with pytest.raises(RuntimeError) as refusal:
            _blocker({"provider": "providerRuntime"}, unnameable)

        assert "providerRuntime" in str(refusal.value)
        assert "carries no reason" in str(refusal.value)


def test_an_empty_provider_reason_is_replaced_by_a_named_statement() -> None:
    """A producer that reports a blockage with a blank reason cannot produce a blank blocker.

    ``_blocked`` reads an empty reason as a blockage, which is correct -- the resource was not
    removed and nothing said it was already absent -- so the blocker still has to name what it
    stopped on. The blank string becomes operator language instead of reaching an operator as
    ``reason: ""``.
    """

    blockers = terminal_result_blockers(
        TerminalResult(
            providers={
                "state": "torn-down",
                "providerRuntime": {"removed": False, "reason": ""},
            },
            worktrees={},
            branches={},
            directories={},
        )
    )

    assert blockers == [
        {"provider": "providerRuntime", "reason": "no reason reported by the terminal result"}
    ]


def test_remove_tree_answers_with_a_reason_whenever_it_reclaimed_nothing(
    tmp_path: Path,
) -> None:
    """Every provider-runtime removal result either removed the tree or says why it did not.

    ``remove_tree`` is the field's only producer and a terminal blocker is built from what it
    reports, so a result of it that reclaimed nothing may not arrive reasonless -- which is what
    the L6 payload's ``providerRuntime`` was.
    """

    absent = tmp_path / "already-gone"
    absent_result = remove_tree(absent, dry_run=False)
    assert absent_result == {
        "path": absent.as_posix(),
        "removed": False,
        "reason": "already-absent",
    }

    present = tmp_path / "present"
    (present / "data").mkdir(parents=True)
    (present / "data" / "file").write_text("content\n", encoding="utf-8")
    removed = remove_tree(present, dry_run=False)
    assert removed == {"path": present.as_posix(), "removed": True}
    assert not present.exists()


def test_a_reclaimed_but_surviving_provider_runtime_reports_why_it_survived(
    tmp_path: Path,
) -> None:
    """The one branch that could answer ``removed: False`` silently now names its own cause.

    Provider data written root-owned makes the plain removal fail; ownership is reclaimed through
    a one-shot container and the removal is retried. When the tree survives that retry, the result
    is what a terminal blocker is built from, and it says so in operator language instead of
    arriving as a reasonless ``{"removed": False}``.
    """

    provider_runtime = tmp_path / "provider-runtime"
    provider_runtime.mkdir()
    (provider_runtime / "data").write_text("root-owned\n", encoding="utf-8")
    calls = {"plain": 0}

    def _rmtree(target: Path, *args: object, **kwargs: object) -> None:
        calls["plain"] += 1
        if calls["plain"] == 1:
            raise PermissionError(13, "Permission denied", target.as_posix())

    with (
        mock.patch(
            "agents_remember.application.provider_runtime._reclaim_ownership",
            return_value={"ok": True, "image": "falkordb/falkordb:latest", "owner": "1000:1000"},
        ),
        mock.patch("agents_remember.application.provider_runtime.shutil.rmtree", _rmtree),
    ):
        result = remove_tree(
            provider_runtime,
            dry_run=False,
            reclaim_image="falkordb/falkordb:latest",
            reclaim_cwd=tmp_path,
        )

    assert calls["plain"] == 2, calls
    assert result["removed"] is False
    assert result["reclaimedViaDocker"] is True
    assert result["reason"] == "still present after docker ownership reclaim"
