"""MIK-R09 at every public route entry, and the fixes of the L09 review R1 (2026-09-30).

Each route test enters through the route's own entry point, so removing the route's gate call makes
it fail: worktree closeout (``external_closeout_commits``), direct landing apply and preview
(``direct_landing``), record landing (``record_landing_result``), and the master and checkpoint
landing (``_handover_or_apply_integration``). The fixture world is the gate module's.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application import review_tree_entries
from agents_remember.application.knowledge_currentness import observe
from agents_remember.application.knowledge_gate import (
    GateResult,
    KnowledgeGate,
    evaluate_leaf_gate,
    memo,
)
from agents_remember.application.knowledge_gate import gate as gate_module
from agents_remember.application.knowledge_gate.landing import net_stale_entries
from agents_remember.application.knowledge_worklist.code import CodeReadError, CodeTrees
from agents_remember.application.knowledge_worklist.leaf import (
    CandidateTrees,
    leaf_onboarding_trace_sides,
    leaf_worklist,
)
from agents_remember.application.knowledge_worklist.onboarding_trace import trace_context
from agents_remember.errors import GrammarUnavailableError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.kernel.recorded_reads import (
    ABSENT,
    CONFLICTING,
    bytes_identity,
    record_read,
    recorded_reads,
)
from agents_remember.memory.conversion import code_objects
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory_quality.knowledge_validator.commit_route import GitKnowledgeValidation
from agents_remember.memory_quality.style.citations import extents
from agents_remember.models.closeout.input import EffectiveCloseoutInput
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.worktrees import direct_landing as route
from agents_remember.worktrees import knowledge_gate, knowledge_validation
from agents_remember.worktrees.integration.direct_landing.direct_landing_operation import (
    direct_landing_store,
)
from agents_remember.worktrees.integration.integration_ref_transaction import (
    IntegratedCommits,
    IntegrationSources,
)
from agents_remember.worktrees.integration.lifecycle.control import cancellation
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.knowledge_gate import (
    DIRECT_CLOSING_RECEIPTS,
    close_owner_history,
    direct_closing_receipt,
    keep_direct_closing,
    leaf_gate_refusal,
    settle_direct_closing,
)
from agents_remember.worktrees.modules import closeout_external, integrate, record_landing
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import VerifiedChange
from agents_remember.worktrees.services import (
    DirectGateVerdict,
    LandingGateRequest,
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    default_series_contract,
    load_contract,
    write_contract,
)
from test_knowledge_closeout_gate import (
    CODE_A,
    LEAF,
    TRACES,
    A,
    Gated,
    _answered,
    _edit,
    _series,
    build_gated,
    commit,
    family_row,
    git,
    invariant,
    sidecar,
    trace_rows,
    write,
)

HISTORY = f"knowledge/history/{LEAF}.json"
GHOST = {"subject": "INV-ZZZZZZ", "disposition": "no_impact", "reason": "x", "covers": []}


@pytest.fixture
def world(tmp_path: Path) -> Gated:
    return build_gated(tmp_path)


@pytest.fixture
def ports() -> Any:
    providers = mock.Mock()
    providers.setup_status.return_value = {}
    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=providers,
            memory_quality=cast(Any, None),
            citation_guard=cast(Any, None),
            knowledge_validation=GitKnowledgeValidation(base_converter=GitBaseConverter()),
            knowledge_gate=KnowledgeGate(),
        )
    )
    yield
    reset_worktree_services()


def _answer(world: Gated) -> None:
    """The leaf's code change, answered by current rows in its open history file."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(*TRACES))


def _record(world: Gated, memory_commit: str) -> Any:
    """``record_landing_result`` as a dry run: the leaf's landing on ``main`` is recorded."""

    return record_landing.record_landing_result(
        WorktreeArgs(
            contract_path=world.contract_path(),
            dry_run=True,
            landed_code_commit=world.code_base,
            landed_memory_content_commit=memory_commit,
        )
    )


# --------------------------------------------------------------------------------------------------
# Finding 1: a leaf's own history file closed by hand is no waiver
# --------------------------------------------------------------------------------------------------


def test_a_hand_closed_leaf_file_is_refused_at_closeout_validation_and_record_landing(
    world: Gated, ports: None
) -> None:
    """R1 finding 1: the leaf's own file, closed by hand, is still re-anchor-checked at leaf routes.

    Its row's ``after`` is the entry's old anchor, so it contradicts K_C; rule 2 alone would accept
    it (the entry is current at C). Only the leaf-publication re-anchor check refuses it.
    """

    _edit(world, CODE_A.replace("return value", "return -value"))
    before = world.reanchor("RLZ-A00001")
    stale_after = world.invariant_row("INV-AAAAAA", before)
    stale_after["covers"][0]["after"] = before["RLZ-A00001"]
    examined = {"INV-AAAAAA": 1, "INV-BBBBBB": 1}
    world.rows(
        stale_after,
        {
            "subject": "FAM-F00001",
            "disposition": "no_impact",
            "reason": "Every member still holds.",
            "examined": [{"id": one, "revision": revision} for one, revision in examined.items()],
        },
        *trace_rows(*TRACES),
        closed=True,
    )
    candidate = world.candidate()
    refusal = leaf_gate_refusal(
        world.contract, code_tree=candidate.code, memory_tree=candidate.memory
    )
    assert refusal is not None and "R09-history-rows" in refusal and "RLZ-A00001" in refusal

    landed = commit(world.memory, {}, trailer=world.code_base)
    with pytest.raises(RuntimeError, match="R09-history-rows") as refused:
        _record(world, landed)
    assert "RLZ-A00001" in str(refused.value)

    # L37 review R5-1: the same file landed one commit later is refused all the same. A parent
    # that holds the file closed freezes nothing: only a closeout the contract records does.
    child = commit(world.memory, {}, trailer=world.code_base)
    git(world.memory, "checkout", "-q", "-b", "side", world.memory_base)
    commit(world.memory, {"onboarding/unrelated.md": "# Unrelated\n"})
    git(world.memory, "checkout", "-q", "leaf")
    git(
        world.memory, "merge", "-q", "--no-ff", "-m", f"m\n\nCode-Commit: {world.code_base}", "side"
    )
    for later in (child, git(world.memory, "rev-parse", "HEAD")):
        with pytest.raises(RuntimeError, match="R09-history-rows") as refused:
            _record(world, later)
        assert "RLZ-A00001" in str(refused.value)


# --------------------------------------------------------------------------------------------------
# Finding 5: one refusal test per public route entry
# --------------------------------------------------------------------------------------------------


def _closeout(world: Gated, code_commit: str) -> Any:
    """``external_closeout_commits`` with only the journal hooks stubbed."""

    effective = EffectiveCloseoutInput.model_validate(
        {
            "route": "worktree",
            "contractKind": "leaf",
            "memoryMode": "external",
            "code": {"state": "enabled", "reason": "leaf", "message": "code"},
            "memory": {"state": "enabled", "reason": "leaf", "message": "memory"},
        }
    )
    change = VerifiedChange(
        commit=code_commit, commit_date="2026-09-30T00:00:00+00:00", changed_paths=[A]
    )
    refresh = closeout_external._ExternalMemoryRefresh([], [], [], {})
    with (
        mock.patch.object(closeout_external, "_refresh_external_memory", return_value=refresh),
        mock.patch.object(closeout_external, "report_operation_progress"),
        mock.patch.object(closeout_external, "prove_git_commit"),
        mock.patch.object(closeout_external, "refresh_memory_cache", return_value={}),
    ):
        return closeout_external.external_closeout_commits(
            world.contract, WorktreeArgs(contract_path=world.contract_path()), effective, change
        )


def test_the_worktree_closeout_refuses_restores_the_file_and_commits_it_closed_once_valid(
    world: Gated, ports: None
) -> None:
    """Findings 2 and 5 (M05, M21): the route closes, validates, and restores on a refusal."""

    _answer(world)
    history = world.memory / HISTORY
    open_bytes = history.read_bytes()
    code_commit = commit(world.code, {})
    write(world.memory, {"onboarding/pkg/a.py.md": "# a [4]\n"})
    never = mock.Mock(side_effect=AssertionError("committed an invalid tree"))
    with (
        mock.patch.object(closeout_external, "begin_git_mutation", never),
        pytest.raises(RuntimeError, match=r"R22\.3-markers"),
    ):
        _closeout(world, code_commit)
    never.assert_not_called()
    assert history.read_bytes() == open_bytes  # the refused closeout left the file as written

    write(world.memory, {"onboarding/pkg/a.py.md": "# a\n"})
    with mock.patch.object(closeout_external, "begin_git_mutation", return_value=None):
        outcome = _closeout(world, code_commit)
    committed = json.loads(git(world.memory, "show", f"{outcome.memory_commit}:{HISTORY}"))
    assert committed["closed"] is True
    assert committed["rows"] == json.loads(open_bytes)["rows"]


def test_record_landing_refuses_through_its_route_entry(world: Gated, ports: None) -> None:
    """M01: an invalid converted memory commit is refused before anything is recorded."""

    broken = commit(world.memory, {"onboarding/pkg/a.py.md": "# a [4]\n"}, trailer=world.code_base)
    with pytest.raises(RuntimeError, match=r"R22\.3-markers"):
        _record(world, broken)


def test_the_master_and_checkpoint_landings_refuse_through_the_integration_route(
    world: Gated, ports: None
) -> None:
    """M02: preview and apply share ``_handover_or_apply_integration``; both routes refuse there."""

    code_commit = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
    memory_commit = commit(world.memory, {}, trailer=code_commit)  # nothing re-anchored: stale
    series = replace(_series(world), code_commit=code_commit, memory_content_commit=memory_commit)
    sources = IntegrationSources(world.code_base, world.memory_base, False, False)
    args = WorktreeArgs(contract_path=series.contract_path, dry_run=True)
    commits = IntegratedCommits(code=code_commit, memory_content=memory_commit)
    landing = integrate.CheckpointLanding(refs=cast(Any, None), commits=commits, sources=sources)
    for checkpoint in (None, landing):
        blocked = integrate._handover_or_apply_integration(
            series, args, sources, checkpoint=checkpoint
        )
        assert blocked.returncode == 2
        assert blocked.payload["state"] == "knowledge-gate-refused", blocked.payload
        assert "knowledge-stale-at-landing" in str(blocked.payload["reason"])


# -- direct landing: its public entry, with a configured series ----------------------------------


def _configured(world: Gated) -> tuple[McpRuntimeConfig, Any]:
    """The series contract written and located, and the runtime config that names its repos."""

    workspace = world.root.with_name(f"{world.root.name}-workspace")  # beside the coordination root
    workspace.mkdir(exist_ok=True)
    (workspace / "agents-remember").symlink_to(world.code, target_is_directory=True)
    memory = world.root / "memory-repos" / "ar-agents-remember"
    memory.parent.mkdir(exist_ok=True)
    memory.symlink_to(world.memory, target_is_directory=True)
    config_path = workspace / "settings.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": world.root.as_posix(),
                "workspaceRoot": workspace.as_posix(),
                "repositories": {"agents-remember": {}},
                "directExecutionEnabled": True,
            }
        ),
        encoding="utf-8",
    )
    task_root = world.root / "tasks" / "agents-remember" / "gate_case"
    task_root.mkdir(parents=True)
    for name in ("task.md", "leaf.json"):
        (task_root / name).write_bytes((world.task_root / name).read_bytes())
    series = default_series_contract(
        ContractTask(
            name="gate_case",
            repo_name="agents-remember",
            coordination_root=world.root,
            workflow_kind="light-task",
            memory_mode="external",
        ),
        code=RepoBranchPlan(world.code, "main", "leaf", world.code_base),
        memory=RepoBranchPlan(world.memory, "main", "leaf", world.memory_base),
        task_root=task_root,
    )
    write_contract(series.contract_path, series)
    text = series.contract_path.read_text(encoding="utf-8")
    publish_new_lifecycle_operation_location(series, contract_text=text)
    return load_config(config_path), load_contract(series.contract_path)


def _land(world: Gated, config: McpRuntimeConfig, series: Any, **fields: Any) -> Any:
    code_commit = git(world.code, "rev-parse", "HEAD")
    request = route.DirectLandingRequest(
        contract_path=series.contract_path.as_posix(),
        code_commit=code_commit,
        candidate_tree=git(world.code, "rev-parse", "HEAD^{tree}"),
        memory_commit_message="direct memory",
        intent_note=fields.pop("intent_note", "approved"),
        **fields,
    )
    return route.direct_landing(config, request, series)


def test_direct_landing_preview_and_apply_refuse_through_their_route_entry(
    world: Gated, ports: None
) -> None:
    """M03, M04 and M20: no open history file, or two, names no leaf; both entries refuse."""

    commit(world.code, {A: CODE_A.replace("return value", "return -value")})
    config, series = _configured(world)
    for dry_run in (True, False):
        with pytest.raises(route.DirectLandingError) as refused:
            _land(world, config, series, dry_run=dry_run)
        assert refused.value.status == "direct-landing-knowledge-gate-refused"
        assert "0 open leaf history file(s)" in refused.value.detail
    assert direct_landing_store(series).read() is None  # nothing was admitted

    world.rows()
    other = {"schema": "ar-history/v1", "leaf": "260928-MIK-L96", "closed": False, "rows": []}
    write(world.memory, {"knowledge/history/260928-MIK-L96.json": canonical_text(other)})
    with pytest.raises(route.DirectLandingError) as two:
        _land(world, config, series, dry_run=True)
    assert "2 open leaf history file(s)" in two.value.detail


# --------------------------------------------------------------------------------------------------
# Finding 3: the direct landing's closing is kept until its generation is decided
# --------------------------------------------------------------------------------------------------


def _admitted(world: Gated) -> tuple[McpRuntimeConfig, Any, bytes]:
    """An answered leaf whose direct landing was admitted, then interrupted: in flight."""

    _answer(world)
    open_bytes = (world.memory / HISTORY).read_bytes()
    git(world.code, "add", "-A")
    git(world.code, "commit", "-q", "-m", "leaf")
    config, series = _configured(world)
    interrupted = mock.Mock(side_effect=RuntimeError("interrupted"))
    with (
        mock.patch.object(route, "execute_or_require_direct_landing_recovery", interrupted),
        pytest.raises(RuntimeError, match="interrupted"),
    ):
        _land(world, config, series)
    return config, series, open_bytes


def _receipts(series: Any) -> list[Path]:
    return sorted((series.worktree_group / "reports" / DIRECT_CLOSING_RECEIPTS).glob("*.json"))


def test_an_input_conflict_or_a_failed_create_restores_the_closed_file(
    world: Gated, ports: None
) -> None:
    _answer(world)
    history = world.memory / HISTORY
    open_bytes = history.read_bytes()
    git(world.code, "add", "-A")
    git(world.code, "commit", "-q", "-m", "leaf")
    config, series = _configured(world)
    conflict = route.DirectLandingError("direct-landing-input-conflict", "an accepted generation")
    for failure in (conflict, OSError("the journal cannot be written")):
        with (
            mock.patch.object(route, "_create_direct_landing", side_effect=failure),
            pytest.raises(type(failure)),
        ):
            _land(world, config, series)
        assert history.read_bytes() == open_bytes
        assert not _receipts(series)


def test_an_exact_retry_of_an_in_flight_generation_reaches_it_before_the_gate(
    world: Gated, ports: None
) -> None:
    config, series, _open_bytes = _admitted(world)
    record = direct_landing_store(series).read()
    assert record is not None and record.status == "running"
    assert json.loads((world.memory / HISTORY).read_text())["closed"] is True
    ungated = mock.Mock(side_effect=AssertionError("the gate ran before the existing generation"))
    resumed = mock.Mock(return_value={"ok": True, "state": "landed"})
    with (
        mock.patch.object(route, "_close_gated_leaf", ungated),
        mock.patch.object(route, "execute_or_require_direct_landing_recovery", resumed),
    ):
        result = _land(world, config, series)
    ungated.assert_not_called()
    assert resumed.call_count == 1 and result["state"] == "landed"
    assert result["lifecycleOperation"]["kind"] == "direct-landing"


def _cancel(series: Any) -> Any:
    """``cancel_operation`` on the series' current generation; worker, proof and publish stubbed."""

    store = direct_landing_store(series)
    record = store.read()
    assert record is not None
    cancelled = record.model_copy(update={"status": "cancelled", "phase": "cancelled"})
    with (
        mock.patch.object(cancellation, "_terminate_worker", return_value=record),
        mock.patch.object(cancellation, "prove_cancellable_git", return_value=(None, record)),
        mock.patch.object(
            cancellation, "_publish_cancelled_outcome", return_value=(cancelled, series)
        ),
        mock.patch.object(cancellation, "operation_projection"),
        mock.patch.object(cancellation, "project_closeout_refresh", return_value="projected"),
    ):
        return cancellation.cancel_operation(series, store, record, dry_run=False)


def test_cancelling_the_generation_restores_the_file_it_closed_but_never_a_later_edit(
    world: Gated, ports: None
) -> None:
    """R1 finding 3, and R2 N04: the restore happens only while the file is what it closed."""

    _config, series, open_bytes = _admitted(world)
    record = direct_landing_store(series).read()
    assert record is not None
    receipt = direct_closing_receipt(series, record.fingerprint)
    assert json.loads(receipt.read_text())["fingerprint"] == record.fingerprint
    assert _cancel(series) == "projected"
    assert (world.memory / HISTORY).read_bytes() == open_bytes
    assert not receipt.exists()

    history = world.memory / HISTORY  # a second admitted closing, then a hand edit of the file
    closing = close_owner_history(world.memory, LEAF)
    keep_direct_closing(series, closing, record.fingerprint)
    edited = history.read_bytes() + b"\n"
    history.write_bytes(edited)
    _cancel(series)
    assert history.read_bytes() == edited  # never overwritten
    assert not receipt.exists()


def test_another_generation_s_request_never_replaces_a_kept_closing(
    world: Gated, ports: None
) -> None:
    """R2 N12: leaf L97 is in flight; a request for another leaf conflicts; L97 still restores."""

    config, series, open_bytes = _admitted(world)
    kept = _receipts(series)
    other = "knowledge/history/260928-MIK-L96.json"
    document = {"schema": "ar-history/v1", "leaf": "260928-MIK-L96", "closed": False, "rows": []}
    write(world.memory, {other: canonical_text(document)})
    other_bytes = (world.memory / other).read_bytes()
    passing = DirectGateVerdict(True, "260928-MIK-L96", None)
    with (
        mock.patch.object(route, "direct_gate_verdict", return_value=passing),
        pytest.raises(route.DirectLandingError) as conflict,
    ):
        _land(world, config, series, intent_note="the other leaf")
    assert conflict.value.status == "direct-landing-input-conflict"
    assert (world.memory / other).read_bytes() == other_bytes  # its own closing undone
    assert _receipts(series) == kept  # the in-flight generation's receipt is intact
    record = direct_landing_store(series).read()
    assert record is not None
    written = kept[0].read_bytes()  # nor does a later closing under the same generation replace it
    assert not keep_direct_closing(
        series, close_owner_history(world.memory, LEAF), record.fingerprint
    )
    assert kept[0].read_bytes() == written
    _cancel(series)
    assert (world.memory / HISTORY).read_bytes() == open_bytes


def test_a_closing_kept_by_a_call_that_ended_before_the_create_is_restored_at_the_next_apply(
    world: Gated, ports: None
) -> None:
    """R2 N05: every apply first settles the kept closings, before anything else is read."""

    _answer(world)
    history = world.memory / HISTORY
    open_bytes = history.read_bytes()
    git(world.code, "add", "-A")
    git(world.code, "commit", "-q", "-m", "leaf")
    config, series = _configured(world)
    keep_direct_closing(series, close_owner_history(world.memory, LEAF), "sha256:" + "9" * 64)
    stop = mock.Mock(side_effect=RuntimeError("stopped after the settle"))
    with (
        mock.patch.object(route, "_in_flight_retry", stop),
        pytest.raises(RuntimeError, match="stopped after the settle"),
    ):
        _land(world, config, series)
    assert history.read_bytes() == open_bytes
    assert not _receipts(series)


def test_an_unreadable_closing_receipt_is_a_named_refusal_at_apply_and_at_cancel(
    world: Gated, ports: None
) -> None:
    """R2-5: the restore it holds is never lost silently; the refusal names the file and the fix."""

    config, series, _open_bytes = _admitted(world)
    receipt = _receipts(series)[0]
    receipt.write_text("{not json", encoding="utf-8")
    with pytest.raises(route.DirectLandingError) as refused:
        _land(world, config, series)
    assert refused.value.status == "direct-landing-closing-receipt-unreadable"
    assert str(receipt) in refused.value.detail and "then delete" in refused.value.detail
    store = direct_landing_store(series)
    record = store.read()
    assert record is not None
    untouched = mock.Mock(side_effect=AssertionError("the worker was terminated"))
    with (
        mock.patch.object(cancellation, "_terminate_worker", untouched),
        pytest.raises(cancellation.LifecycleControlError) as cancel,
    ):
        cancellation.cancel_operation(series, store, record, dry_run=False)
    assert cancel.value.status == "direct-landing-closing-receipt-unreadable"
    untouched.assert_not_called()  # refused before anything moved
    assert receipt.exists()


# --------------------------------------------------------------------------------------------------
# Finding 4: the master's net staleness refuses on unverifiable entries too
# --------------------------------------------------------------------------------------------------


def test_a_timed_out_blob_read_or_an_unverifiable_entry_refuses_a_master_landing(
    world: Gated, ports: None
) -> None:
    code_commit = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
    world.reanchor("RLZ-A00001")
    memory_commit = commit(world.memory, {}, trailer=code_commit)  # RLZ-A00002's blob moved too
    series = replace(_series(world), code_commit=code_commit, memory_content_commit=memory_commit)
    sources = IntegrationSources(world.code_base, world.memory_base, False, False)
    args = WorktreeArgs(contract_path=series.contract_path, dry_run=True)
    assert integrate._knowledge_gate_block(series, args, _commits(series), sources) is None

    timed_out = subprocess.TimeoutExpired(["git", "cat-file"], 30)
    with mock.patch.object(observe, "_observed_content", side_effect=timed_out):
        blocked = integrate._knowledge_gate_block(series, args, _commits(series), sources)
    assert blocked is not None
    reason = str(blocked.payload["reason"])
    assert "knowledge-worklist-incomplete" in reason and "a Git read failed" in reason

    unsupported = "the locator kind 'symbol' is unsupported for 'pkg/a.py': no shipped grammar"
    with mock.patch.object(observe, "_unsupported", return_value=unsupported):
        blocked = integrate._knowledge_gate_block(series, args, _commits(series), sources)
    assert blocked is not None
    reason = str(blocked.payload["reason"])
    assert "knowledge-unverifiable-at-landing" in reason and "RLZ-A00002" in reason
    assert "no shipped grammar" in reason


def _commits(series: Any) -> IntegratedCommits:
    return IntegratedCommits(code=series.code_commit, memory_content=series.memory_content_commit)


# --------------------------------------------------------------------------------------------------
# Review R2: every history file names real records; the scoping and the exact trees are pinned
# --------------------------------------------------------------------------------------------------


def _history(leaf: str, *rows: dict[str, Any], closed: bool = True) -> str:
    numbered = [{"id": f"ROW-{index:06d}", "items": [], **row} for index, row in enumerate(rows)]
    return canonical_text(
        {"schema": "ar-history/v1", "leaf": leaf, "closed": closed, "rows": numbered}
    )


def test_a_hand_committed_closed_history_file_with_a_ghost_subject_is_refused_at_master_landing(
    world: Gated, ports: None
) -> None:
    """R2-1: the reviewer's probe. No AR route wrote the file; the master landing still refuses."""

    ghost = _history("260928-MIK-L94", {**GHOST, "revision": 1})
    landed = commit(world.memory, {"knowledge/history/260928-MIK-L94.json": ghost}, world.code_base)
    series = replace(_series(world), code_commit=world.code_base, memory_content_commit=landed)
    sources = IntegrationSources(world.code_base, world.memory_base, False, False)
    args = WorktreeArgs(contract_path=series.contract_path, dry_run=True)
    blocked = integrate._handover_or_apply_integration(series, args, sources)
    reason = str(blocked.payload["reason"])
    assert blocked.payload["state"] == "knowledge-gate-refused", blocked.payload
    assert "R09-history-rows" in reason and "INV-ZZZZZZ" in reason


def test_a_sibling_s_file_closed_in_the_base_is_not_re_anchor_checked_at_a_leaf_route(
    world: Gated, ports: None
) -> None:
    """R2 N01, the accepted scoping: an earlier leaf's closed rows describe the tree it closed on."""

    anchor = world.anchor("RLZ-A00001")
    covers = [{"id": "RLZ-A00001", "before": anchor, "after": anchor}]
    held = {
        "subject": "INV-AAAAAA",
        "disposition": "no_impact",
        "reason": "Held.",
        "covers": covers,
    }
    git(world.memory, "checkout", "-q", "main")
    sibling = {
        "knowledge/history/260928-MIK-L95.json": _history("260928-MIK-L95", {**held, "revision": 1})
    }
    world.memory_base = commit(world.memory, sibling, trailer=world.code_base)
    git(world.memory, "checkout", "-q", "leaf")
    git(world.memory, "merge", "-q", "--no-edit", "main")
    _answer(world)  # this leaf re-anchors RLZ-A00001, so the sibling's row no longer matches it
    candidate = world.candidate()
    assert (
        leaf_gate_refusal(world.contract, code_tree=candidate.code, memory_tree=candidate.memory)
        is None
    )


def _contradicting_row(world: Gated) -> bytes:
    """The leaf's open file with a row whose ``after`` is the old anchor, contradicting K_C."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    before = world.reanchor("RLZ-A00001")
    row = world.invariant_row("INV-AAAAAA", before)
    row["covers"][0]["after"] = before["RLZ-A00001"]
    world.rows(row)
    return (world.memory / HISTORY).read_bytes()


def test_the_closeout_s_exact_tree_re_anchor_checks_the_file_it_has_just_closed(
    world: Gated, ports: None
) -> None:
    """R2 N08: the closeout closes the leaf's own file; the exact tree still checks its rows."""

    open_bytes = _contradicting_row(world)
    code_commit = commit(world.code, {})
    never = mock.Mock(side_effect=AssertionError("committed an invalid tree"))
    with (
        mock.patch.object(closeout_external, "begin_git_mutation", never),
        pytest.raises(RuntimeError, match="R09-history-rows"),
    ):
        _closeout(world, code_commit)
    assert (world.memory / HISTORY).read_bytes() == open_bytes


def test_the_direct_landing_s_exact_tree_re_anchor_checks_the_file_it_has_just_closed(
    world: Gated, ports: None
) -> None:
    """R2 N09: with the gate passing, the exact tree after the closing still checks the rows."""

    open_bytes = _contradicting_row(world)
    code_commit = commit(world.code, {})
    passing = DirectGateVerdict(True, LEAF, None)
    with (
        mock.patch.object(route, "direct_gate_verdict", return_value=passing),
        pytest.raises(route.DirectLandingError) as refused,
    ):
        route._close_gated_leaf(_series(world), code_commit)
    assert refused.value.status == "direct-landing-knowledge-validation-refused"
    assert "R09-history-rows" in refused.value.detail
    assert (world.memory / HISTORY).read_bytes() == open_bytes


def test_a_code_object_that_is_unavailable_is_named_and_keeps_the_item_findings(
    world: Gated, ports: None
) -> None:
    """R2-3: not a Git failure. It still blocks, under its own reason, and the items stay visible."""

    memo.GATE_MEMO.clear()
    _edit(world, CODE_A.replace("return value", "return -value").replace("return 1", "return 3"))
    before = world.reanchor("RLZ-A00001", "RLZ-A00002")
    _answered(world, before, *trace_rows(*TRACES))
    _edit(world, CODE_A.replace("return value", "return +value").replace("return 1", "return 3"))
    missing = observe.CodeObjectUnavailable(
        "the blob e1 the line range was recorded against is unavailable"
    )
    with mock.patch.object(observe, "_observed_content", side_effect=missing):
        result = evaluate_leaf_gate(
            world.contract,
            world.candidate(),
            parent_memory_tip=git(world.memory, "rev-parse", "main"),
        )
    assert result is not None and not result.ok
    assert not result.count("knowledge-worklist-incomplete")
    opened = [one.message for one in result.findings if one.code == "knowledge-item-open"]
    assert any("is unverifiable (the blob e1 the line range" in one for one in opened)

    code_commit = commit(world.code, {})
    memory_commit = commit(world.memory, {}, trailer=code_commit)
    request = LandingGateRequest(
        world.memory, memory_commit, (world.memory_base,), world.code, code_commit, world.code_base
    )
    observe.OBSERVATIONS.clear()
    no_grammar = GrammarUnavailableError("the Python grammar wheel is not installed")
    with mock.patch.object(extents, "definitions", side_effect=no_grammar):
        findings = net_stale_entries(request)
    assert findings and {one.code for one in findings} == {"knowledge-unverifiable-at-landing"}
    assert all("the Python grammar wheel is not installed" in one.message for one in findings)
    assert not any("a Git read failed" in one.message for one in findings)


# --------------------------------------------------------------------------------------------------
# Finding 6: the admission base at the routes is the parent tip, and the memo keys on it
# --------------------------------------------------------------------------------------------------


def test_the_routes_judge_a_leaf_s_earlier_record_new_against_the_parent_line(
    world: Gated, ports: None
) -> None:
    """M19: no tip is passed; the route reads the parent line's tip itself (carried from L27)."""

    bare = {"criteria": ["prevents_costly_mistake"], "justification": f"Added in {LEAF}."}
    origin = {"task": "260928-MIK", "leaf": LEAF}
    path = "knowledge/invariants/INV-CCCCCC-new.json"
    commit(world.memory, {path: invariant("INV-CCCCCC", "New.", admission=bare, origin=origin)})
    candidate = world.candidate()
    refusal = leaf_gate_refusal(
        world.contract, code_tree=candidate.code, memory_tree=candidate.memory
    )
    assert refusal is not None and "R27.2-new-record" in refusal and "INV-CCCCCC" in refusal
    result = evaluate_leaf_gate(world.contract, candidate)
    assert result is not None
    assert any("R27.2-new-record" in finding.message for finding in result.findings)


def test_the_memo_key_holds_the_parent_tip(world: Gated) -> None:
    """M09: the same trees against another parent tip are another evaluation."""

    memo.GATE_MEMO.clear()
    leaf = commit(world.memory, {})  # the leaf's own line moves past the parent tip
    candidate = world.candidate()
    main = git(world.memory, "rev-parse", "main")
    assert memo.memo_key(world.contract, candidate, main) != memo.memo_key(
        world.contract, candidate, leaf
    )
    evaluated = mock.Mock(wraps=gate_module._evaluate)
    with mock.patch.object(gate_module, "_evaluate", evaluated):
        for tip in (main, main, leaf):
            evaluate_leaf_gate(world.contract, candidate, parent_memory_tip=tip)
    assert evaluated.call_count == 2


# --------------------------------------------------------------------------------------------------
# Finding 7: record landing probes before it reads, so unconverted memory records as before
# --------------------------------------------------------------------------------------------------


def test_record_landing_on_unconverted_memory_never_reads_the_landed_commit(
    world: Gated, ports: None
) -> None:
    unreadable = "f" * 40
    refused = pytest.raises(RuntimeError, match="landed memory commit cannot be read")
    with refused:
        _record(world, unreadable)  # converted: an unreadable commit refuses, named
    for branch in ("main", "leaf"):
        git(world.memory, "checkout", "-q", branch)
        git(world.memory, "rm", "-q", "knowledge/layout.json")
        commit(world.memory, {})
    world.memory_base = git(world.memory, "rev-parse", "main")
    recorded = _record(world, unreadable)
    assert recorded.returncode == 0 and recorded.payload["state"] == "would-record"


# --------------------------------------------------------------------------------------------------
# Finding 9 and notes: a Git failure is incomplete, never a verdict, and never kept
# --------------------------------------------------------------------------------------------------


def _incomplete(result: GateResult | None, name: str) -> list[str]:
    assert result is not None and not result.memoisable
    return [
        finding.message
        for finding in result.findings
        if finding.code == "knowledge-worklist-incomplete" and finding.path == name
    ]


def test_a_git_read_that_fails_inside_a_predicate_or_the_validator_is_never_a_verdict(
    world: Gated, ports: None
) -> None:
    memo.GATE_MEMO.clear()
    _edit(world, CODE_A.replace("return value", "return -value").replace("return 1", "return 3"))
    before = world.reanchor("RLZ-A00001", "RLZ-A00002")
    _answered(world, before, *trace_rows(*TRACES))
    _edit(world, CODE_A.replace("return value", "return +value").replace("return 1", "return 3"))
    candidate = world.candidate()
    tip = git(world.memory, "rev-parse", "main")
    timed_out = subprocess.TimeoutExpired(["git", "cat-file"], 30)
    evaluated = mock.Mock(wraps=gate_module._evaluate)
    with (
        mock.patch.object(gate_module, "_evaluate", evaluated),
        mock.patch.object(observe, "_observed_content", side_effect=timed_out),
    ):
        for _ in range(2):
            failed = evaluate_leaf_gate(world.contract, candidate, parent_memory_tip=tip)
            assert any("a Git read failed" in one for one in _incomplete(failed, "git"))
    assert evaluated.call_count == 2  # the incomplete verdict was not kept

    with mock.patch.object(gate_module, "validate_tree", side_effect=timed_out):
        failed = evaluate_leaf_gate(world.contract, candidate, parent_memory_tip=tip)
    assert any("TimeoutExpired" in one for one in _incomplete(failed, "git"))


def test_a_marker_probe_git_cannot_answer_is_never_unconverted_memory(
    world: Gated, ports: None
) -> None:
    code = world.candidate().code
    unreadable = CandidateTrees(code=code, memory="0" * 40)
    document = leaf_worklist(world.contract, persist=False, candidate=unreadable)
    assert document is not None and document["state"] == "incomplete"
    assert document["incomplete"][0]["input"] == "layout marker"
    tip = git(world.memory, "rev-parse", "main")
    assert _incomplete(
        evaluate_leaf_gate(world.contract, unreadable, parent_memory_tip=tip), "layout marker"
    )

    path = world.contract_path()
    path.write_text(
        path.read_text().replace(
            "  source_branch: main\n  work_branch: leaf\n  base_commit: " + world.memory_base,
            "  source_branch: gone\n  work_branch: leaf\n  base_commit: " + world.memory_base,
        )
    )
    bare = world.root / "bare-memory"
    bare.mkdir()
    sides = leaf_onboarding_trace_sides(load_contract(path), memory_tree=bare)
    assert sides is not None and str(sides.incomplete).startswith("layout marker")

    timed_out = subprocess.TimeoutExpired(["git", "ls-tree"], 30)
    with mock.patch.object(knowledge_validation, "run_git", side_effect=timed_out):
        refusal = leaf_gate_refusal(world.contract, code_tree=code, memory_tree="0" * 40)
    assert refusal is not None and "failed or timed out" in refusal


def test_every_file_read_is_in_the_read_set_and_a_conflicting_read_is_never_kept(
    world: Gated,
) -> None:
    with recorded_reads() as reads:
        trace_context(world.contract)
    coordination = (world.root / "system" / "settings.md").as_posix()
    assert world.contract.memory_worktree is not None
    settings = (world.contract.memory_worktree / "system" / "settings.md").as_posix()
    assert (
        reads.get(f"exists:{settings}") == ABSENT
    )  # the actual settings selection observed its absence
    assert reads.get(world.contract.contract_path.as_posix()) == bytes_identity(
        world.contract.contract_path.read_bytes()
    )
    assert coordination not in reads  # resolution stopped before consuming this unrelated file

    with recorded_reads() as reads:
        record_read(Path("manifest.json"), "sha256:1")
        record_read(Path("manifest.json"), "sha256:1")
        record_read(Path("packet.md"), "sha256:1")
        record_read(Path("packet.md"), "sha256:2")
    assert reads == {"manifest.json": "sha256:1", "packet.md": CONFLICTING}

    memo.GATE_MEMO.clear()
    candidate = world.candidate()
    tip = git(world.memory, "rev-parse", "main")
    key = memo.memo_key(world.contract, candidate, tip)
    result = evaluate_leaf_gate(world.contract, candidate, parent_memory_tip=tip)
    assert key is not None and result is not None and result.memoisable
    memo.GATE_MEMO.clear()
    memo.remember(key, result, {"packet.md": CONFLICTING})
    assert memo.GATE_MEMO.get(key) is None  # never kept: it takes no slot
    memo.remember(key, result, dict(reads, **{"packet.md": "sha256:2"}))
    assert memo.remembered(key) is None  # a recorded file that changed since is never reused


# --------------------------------------------------------------------------------------------------
# Review R3: the landed backstop in the repository's own object format; a missing object vs Git
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_a_closing_the_memory_line_already_holds_is_never_restored(
    tmp_path: Path, object_format: str
) -> None:
    """R3-1 (N16): a receipt whose closed file ``HEAD`` holds is forgotten, never restored.

    It is not the current generation's (``current=None``), so only the landed check -- the closed
    file's blob ID in the repository's own object format -- keeps the landed file closed.
    """

    memory = tmp_path / "memory"
    memory.mkdir()
    git(memory, "init", "-q", f"--object-format={object_format}", "-b", "main")
    git(memory, "config", "user.email", "fixture@example.invalid")
    git(memory, "config", "user.name", "gate fixture")
    commit(memory, {HISTORY: _history(LEAF, closed=False)})
    contract = cast(
        Any, SimpleNamespace(worktree_group=tmp_path / "group", memory_repo_path=memory)
    )
    fingerprint = "sha256:" + "7" * 64
    assert keep_direct_closing(contract, close_owner_history(memory, LEAF), fingerprint)
    closed_bytes = (memory / HISTORY).read_bytes()
    landed = commit(memory, {})  # the generation's memory commit holds the closed file
    receipt = direct_closing_receipt(contract, fingerprint)
    assert json.loads(receipt.read_text())["closedBlob"] == git(
        memory, "rev-parse", f"{landed}:{HISTORY}"
    )
    assert settle_direct_closing(contract, current=None, state="cancelled") == []
    assert (memory / HISTORY).read_bytes() == closed_bytes
    assert not receipt.exists()


def _lost_line_range(world: Gated) -> None:
    """An answered leaf whose row covers a line range recorded against a blob the store lacks."""

    _edit(world, CODE_A.replace("return 1", "return 3"))
    anchor = {
        "locator": {"kind": "line_range", "start": 1, "end": 2},
        "blob": "e" * 40,
        "content": "sha256:" + "1" * 64,
    }
    lost = {
        "id": "RLZ-A00003",
        "invariant": "INV-AAAAAA",
        "role": "primary-authority",
        "rationale": "It is the rule.",
        "anchor": anchor,
    }
    write(world.memory, {f"onboarding/{A}.json": sidecar(A, [*world.sidecar_entries(A), lost])})
    row = world.invariant_row("INV-AAAAAA", world.reanchor("RLZ-A00002"))
    lost_after = {**anchor, "path": A}
    row["covers"].append({"id": "RLZ-A00003", "before": lost_after, "after": lost_after})
    world.rows(row, family_row({"INV-AAAAAA": 1, "INV-BBBBBB": 1}), *trace_rows(*TRACES))


def test_a_recorded_blob_the_store_lacks_is_named_at_its_real_raise_site(
    world: Gated, ports: None
) -> None:
    """R3-2 (N20): ``has_blob`` answers "not found", so the entry is unavailable, not a Git failure."""

    memo.GATE_MEMO.clear()
    _lost_line_range(world)
    tip = git(world.memory, "rev-parse", "main")
    result = evaluate_leaf_gate(world.contract, world.candidate(), parent_memory_tip=tip)
    assert result is not None and not result.ok
    assert result.worklist["state"] == "complete" and not result.count(
        "knowledge-worklist-incomplete"
    )
    opened = [one.message for one in result.findings if one.code == "knowledge-item-open"]
    assert any(f"RLZ-A00003 is unverifiable (the blob {'e' * 40}" in one for one in opened)


def test_a_git_failure_asking_for_a_recorded_blob_is_incomplete_and_never_kept(
    world: Gated, ports: None
) -> None:
    """R3-2: only Git's not-found exit means absent; any other failure is an unreadable input."""

    memo.GATE_MEMO.clear()
    _lost_line_range(world)
    real = code_objects.run_git

    def failing(repository: Path, args: list[str], *rest: Any, **named: Any) -> Any:
        if args[:2] == ["cat-file", "-e"]:
            return subprocess.CompletedProcess(
                args, 128, "", "fatal: the object store is unreadable"
            )
        return real(repository, args, *rest, **named)

    candidate = world.candidate()
    tip = git(world.memory, "rev-parse", "main")
    evaluated = mock.Mock(wraps=gate_module._evaluate)
    with (
        mock.patch.object(code_objects, "run_git", failing),
        mock.patch.object(gate_module, "_evaluate", evaluated),
    ):
        for _ in range(2):
            failed = evaluate_leaf_gate(world.contract, candidate, parent_memory_tip=tip)
            assert failed is not None and not failed.memoisable
            assert failed.count("knowledge-worklist-incomplete")
            assert any("the object store is unreadable" in one.message for one in failed.findings)
    assert evaluated.call_count == 2  # nothing was kept


# --------------------------------------------------------------------------------------------------
# L09 review R4 notes, carried to L37 (2026-09-30T19:53:29): N23, N24 and N25 pinned
# --------------------------------------------------------------------------------------------------


_REAL_RUN_GIT = code_objects.run_git


def _object_store_fails(repository: Path, args: list[str], *rest: Any, **named: Any) -> Any:
    if args[:2] == ["cat-file", "-e"]:
        return subprocess.CompletedProcess(args, 128, "", "fatal: the object store is unreadable")
    return _REAL_RUN_GIT(repository, args, *rest, **named)


def test_a_git_failure_asking_for_a_recorded_blob_is_named_by_the_trees_and_the_lane(
    world: Gated,
) -> None:
    """N24: ``CodeTrees.has_blob`` names a Git failure as its own input; N23: the lane reads it
    ``unavailable`` with that reason, never raising."""

    tree = git(world.code, "rev-parse", "HEAD^{tree}")
    trees = CodeTrees.open(world.code, tree, tree)
    recorded = "e" * 40
    entry = SimpleNamespace(
        path=A,
        document={
            "anchor": {"locator": {"kind": "line_range", "start": 1, "end": 2}, "blob": recorded}
        },
    )
    blob = git(world.code, "rev-parse", f"HEAD:{A}")
    with mock.patch.object(code_objects, "run_git", _object_store_fails):
        with pytest.raises(CodeReadError, match="the object store is unreadable"):
            trees.has_blob(recorded)
        placed = review_tree_entries._in_blob(cast(Any, entry), trees, blob)
    assert placed["state"] == "unavailable"
    assert "the object store is unreadable" in placed["reason"]


def test_an_unwritable_closing_receipt_restores_the_file_and_admits_nothing(world: Gated) -> None:
    """N25: direct landing restores the history file to its open bytes when the receipt fails."""

    series = _series(world)
    world.rows(*trace_rows(*TRACES))
    open_bytes = (world.memory / HISTORY).read_bytes()
    closing = close_owner_history(world.memory, LEAF)
    assert json.loads((world.memory / HISTORY).read_text())["closed"] is True
    fingerprint = "f" * 64
    unwritable = knowledge_gate.ClosingReceiptError("git hash-object failed (exit 128)")
    never = mock.Mock(side_effect=AssertionError("a generation was admitted"))
    with (
        mock.patch.object(knowledge_gate, "_closed_blob", side_effect=unwritable),
        mock.patch.object(route, "_create_direct_landing", never),
        pytest.raises(route.DirectLandingError) as refused,
    ):
        route._admit_direct_landing(
            series,
            cast(Any, None),
            cast(Any, (None, SimpleNamespace(fingerprint=fingerprint))),
            closing,
        )
    assert refused.value.status == "direct-landing-closing-receipt-unwritable"
    assert (world.memory / HISTORY).read_bytes() == open_bytes
    never.assert_not_called()
    assert not knowledge_gate.direct_closing_receipt(series, fingerprint).exists()
