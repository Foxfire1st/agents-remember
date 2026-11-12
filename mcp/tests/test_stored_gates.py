"""Stored gates: the record, the log, and the refusals that name the way out (MIK-R113).

No part of the product raises or decides a gate any more. The gate log and its record stay
because the landing chain still reads them: a gate an earlier installation stored refuses a
closeout, a direct landing or an integration exactly as before. Each of those refusals now
ends with one sentence that names the way out, the command of rule 5 that removes the gate
logs on the user's word, and none of them names a tool the product no longer has. What the
landing chain does on a stored gate is unchanged; only what its refusals say changed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest import mock

import pytest
from agents_remember.controlplane.enforcement import GateGuard
from agents_remember.controlplane.records import (
    GateAnchor,
    GateRecord,
    GateRequest,
    GateVerdict,
    apply_gate,
    create_gate,
    decide_gate,
)
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.primitives.gate_policy import DEFAULT_GATE_POLICY
from agents_remember.kernel.primitives.gate_vocab import STORED_GATE_WAY_OUT, GateKind
from agents_remember.kernel.primitives.observer_paths import observer_logs_root
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from agents_remember.worktrees import direct_landing
from agents_remember.worktrees.activation.atomic_series_activation import (
    AtomicSeriesActivationError,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_errors import (
    DirectLandingError,
)
from agents_remember.worktrees.modules import closeout, integrate, state_refusals
from agents_remember.worktrees.queue import closeout_projection_activation

if TYPE_CHECKING:
    from agents_remember.worktrees.worktree_contract import WorktreeContract

T1 = "2026-06-18T10:00:00+00:00"
T2 = "2026-06-18T10:05:00+00:00"
T3 = "2026-06-18T10:10:00+00:00"
LIFECYCLE = "L1"
MASTER = "260707_master"

# Every tool about gates the product ever registered, and the tools that left with the
# hosting of agents. None is registered any more (pinned below).
REMOVED_TOOLS = (
    "attach_terminal_session_to_task",
    "dispatch_agent",
    "gate_create",
    "gate_decide",
    "gate_decide_internal",
    "gate_list",
    "gate_list_internal",
    "gate_response_wait",
    "gate_wait",
    "hosted_session_readiness",
    "lifecycle_block",
    "lifecycle_gate",
    "lifecycle_gate_internal",
    "operator_inbox_consume",
    "operator_inbox_poll",
    "operator_inbox_post",
    "operator_inbox_supersede",
    "orchestration_nudge_manager",
    "session_rename",
    "session_retire",
    "spawn_agent_session",
)


def test_create_and_decide_are_pure_snapshots() -> None:
    gate = create_gate(
        "closeout-approval",
        gate_id="01H",
        now=T1,
        anchor=GateAnchor(lifecycle_id=LIFECYCLE),
        request=GateRequest(packet={"paths": 3}),
    )
    assert gate.state == "open"

    decided = decide_gate(
        gate,
        GateVerdict(decision="approve", by="developer", via="dashboard", note="lgtm"),
        now=T2,
    )

    assert decided.id == gate.id
    assert decided.ts == T2
    assert decided.state == "approved"
    assert decided.decidedBy == "developer"
    assert decided.decidedVia == "dashboard"
    assert gate.state == "open"


def test_append_keeps_history_and_current_folds_last_wins(tmp_path: Path) -> None:
    store = GateStore(tmp_path)
    gate = create_gate(
        "closeout-approval", gate_id="01H", now=T1, anchor=GateAnchor(lifecycle_id=LIFECYCLE)
    )
    store.append(gate)
    store.append(
        decide_gate(
            gate,
            GateVerdict(decision="approve", by="developer", via="dashboard", note=None),
            now=T2,
        )
    )

    assert len(store.read(LIFECYCLE)) == 2
    assert store.current(LIFECYCLE)["01H"].state == "approved"


def test_gateless_claim_leaves_no_lock_and_existing_approval_is_spent_once(tmp_path: Path) -> None:
    store = GateStore(tmp_path / "observer")
    for size in (8, 64):
        for index in range(size):
            guard = store.claim_approval(f"absent-{size}-{index}", kind="closeout-approval", now=T1)
            assert guard.permitted and guard.gate_id is None
    assert not store.root.exists()

    approved = decide_gate(
        _open_gate("closeout-approval"),
        GateVerdict(decision="approve", by="developer", via="dashboard", note=None),
        now=T2,
    )
    store.append(approved)
    assert store.claim_approval(LIFECYCLE, kind="closeout-approval", now=T3).permitted
    assert not store.claim_approval(LIFECYCLE, kind="closeout-approval", now=T3).permitted
    assert store.current(LIFECYCLE)[approved.id].state == "applied"


def _contract(root: Path) -> Any:
    return SimpleNamespace(
        coordination_root=root,
        lifecycle_id=LIFECYCLE,
        task_name=MASTER,
        parent_task_name="",
    )


def _open_gate(kind: str, *, enclosure: str | None = None) -> GateRecord:
    """A gate an earlier installation raised and nobody decided."""
    return create_gate(
        kind,  # type: ignore[arg-type]
        gate_id="01H",
        now=T1,
        anchor=GateAnchor(lifecycle_id=LIFECYCLE, enclosure=enclosure),
    )


def _store(root: Path, *snapshots: GateRecord) -> None:
    store = GateStore(observer_logs_root(root))
    for snapshot in snapshots:
        store.append(snapshot)


def _closeout_args() -> Any:
    return SimpleNamespace(gate_policy=DEFAULT_GATE_POLICY, operation_key="")


def _closeout_early_read(root: Path) -> dict[str, object]:
    _store(root, _open_gate("closeout-approval"))
    with pytest.raises(RuntimeError) as refused:
        closeout._refuse_unsatisfied_closeout_gate(_contract(root), _closeout_args())
    return {"text": str(refused.value)}


def _closeout_claim(root: Path) -> dict[str, object]:
    _store(root, _open_gate("closeout-approval"))
    with pytest.raises(RuntimeError) as refused:
        closeout._claim_closeout_gate(_contract(root), _closeout_args())
    return {"text": str(refused.value)}


def _direct_landing_claim(root: Path) -> dict[str, object]:
    _store(root, _open_gate("closeout-approval"))
    with pytest.raises(DirectLandingError) as refused:
        direct_landing._claim_direct_review_gate(_contract(root), [])
    return {"text": str(refused.value), **vars(refused.value)}


def _closeout_gate_refusal(root: Path, *, claim: bool) -> dict[str, object]:
    """Exercise the stored-gate read/claim refusals on L56's surviving closeout route."""
    return _closeout_claim(root) if claim else _closeout_early_read(root)


def _integration(root: Path) -> dict[str, object]:
    _store(root, _open_gate("master-handover-approval", enclosure=MASTER))
    args: Any = SimpleNamespace(gate_policy=DEFAULT_GATE_POLICY, dry_run=False)
    sources: Any = SimpleNamespace()
    with (
        mock.patch.object(integrate, "status_payload", return_value={}),
        mock.patch.object(state_refusals, "status_payload", return_value={}),
    ):
        result = integrate._handover_or_apply_integration(_contract(root), args, sources)
    assert result.returncode == 2
    return result.payload


def _closed_guard(kind: GateKind) -> GateGuard:
    return GateGuard(kind=kind, permitted=False, reason=f"{kind} gate 01H is open", gate_id="01H")


def _integration_dry_run(root: Path) -> dict[str, object]:
    """The dry runs of integration and checkpoint answer the same refusal (L111)."""
    preview = integrate.IntegratePreview(
        guard=_closed_guard("master-handover-approval"), handover_warning=None
    )
    args: Any = SimpleNamespace(dry_run=True)
    with mock.patch.object(state_refusals, "status_payload", return_value={}):
        result = integrate._dry_run_result(_contract(root), args, cast(Any, None), preview=preview)
    assert result.returncode == 2
    return result.payload


def _closeout_preview(root: Path) -> dict[str, object]:
    """The closeout preview a closed gate blocks (L111)."""
    with mock.patch.object(closeout, "status_payload", return_value={}):
        return closeout._closeout_gate_blocked_preview(
            _contract(root), _closed_guard("closeout-approval")
        )


REFUSALS: dict[str, Callable[[Path], dict[str, object]]] = {
    "closeout-early-read": _closeout_early_read,
    "closeout-claim": _closeout_claim,
    "closeout-preview": _closeout_preview,
    "direct-landing": _direct_landing_claim,
    "closeout-gate-read": lambda root: _closeout_gate_refusal(root, claim=False),
    "closeout-gate-claim": lambda root: _closeout_gate_refusal(root, claim=True),
    "integration": _integration,
    "integration-dry-run": _integration_dry_run,
}


def _strings(value: object) -> Iterator[str]:
    """Every field name and every text a refusal carries, at any depth."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _named(value: object, field: str) -> Iterator[object]:
    """Every value a refusal carries under ``field``, at any depth."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == field:
                yield item
            yield from _named(item, field)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _named(item, field)


def test_the_sentence_names_the_command_that_removes_the_gate_logs() -> None:
    assert "`agents-remember removed-stores`" in STORED_GATE_WAY_OUT
    assert not STORED_GATE_WAY_OUT.endswith(".")


def test_none_of_the_removed_tools_is_registered() -> None:
    assert not set(REMOVED_TOOLS) & set(TOOL_RESPONSE_MODELS)


@pytest.mark.parametrize("refusal", sorted(REFUSALS))
def test_a_refusal_by_a_stored_gate_names_the_way_out_and_no_removed_tool(
    tmp_path: Path, refusal: str
) -> None:
    fields = REFUSALS[refusal](tmp_path)

    texts = list(_strings(fields))
    assert sum(STORED_GATE_WAY_OUT in text for text in texts) >= 1
    for tool in REMOVED_TOOLS:
        named = re.compile(rf"(?<![A-Za-z0-9_]){tool}(?![A-Za-z0-9_])")
        assert not [text for text in texts if named.search(text)], tool
    # A next tool, where a refusal names one, is a tool the product has.
    assert all(tool in TOOL_RESPONSE_MODELS for tool in _named(fields, "nextTool"))
    # No refusal sends the caller to a gate channel: nobody decides or raises a gate.
    assert not [text for text in texts if "gate channel" in text or "fresh gate" in text]


def test_a_closeout_refused_on_the_early_read_reads_as_before_plus_the_way_out(
    tmp_path: Path,
) -> None:
    assert _closeout_early_read(tmp_path) == {
        "text": "closeout blocked by gate enforcement: closeout-approval gate 01H is open. "
        f"{STORED_GATE_WAY_OUT}"
    }


def test_a_refused_claim_spends_nothing(tmp_path: Path) -> None:
    assert _closeout_claim(tmp_path) == _closeout_early_read(tmp_path / "other")
    assert GateStore(observer_logs_root(tmp_path)).current(LIFECYCLE)["01H"].state == "open"


def test_a_gate_an_earlier_closeout_spent_refuses_with_the_way_out(tmp_path: Path) -> None:
    gate = _open_gate("closeout-approval")
    approved = decide_gate(
        gate, GateVerdict(decision="approve", by="developer", via="dashboard"), now=T2
    )
    _store(tmp_path, gate, approved, apply_gate(approved, now=T3, operation_key="a" * 64))

    with pytest.raises(RuntimeError) as refused:
        closeout._refuse_unsatisfied_closeout_gate(_contract(tmp_path), _closeout_args())

    assert str(refused.value) == (
        "closeout blocked by gate enforcement: closeout-approval gate 01H was already applied. "
        f"{STORED_GATE_WAY_OUT}"
    )


def test_a_direct_landing_refused_by_a_stored_gate_keeps_its_status(tmp_path: Path) -> None:
    fields = _direct_landing_claim(tmp_path)

    assert fields["status"] == "direct-landing-review-refused"
    assert fields["detail"] == (
        "direct landing review record refuses: closeout-approval gate 01H is open. "
        f"{STORED_GATE_WAY_OUT}"
    )
    assert fields["next_action"] is None


@pytest.mark.parametrize("claim", [False, True])
def test_a_closeout_refused_by_a_stored_gate_keeps_its_reason(tmp_path: Path, claim: bool) -> None:
    fields = _closeout_gate_refusal(tmp_path, claim=claim)

    assert fields["text"] == (
        "closeout blocked by gate enforcement: closeout-approval gate 01H is open. "
        f"{STORED_GATE_WAY_OUT}"
    )


def test_an_integration_refused_by_a_stored_handover_gate_names_no_next_tool(
    tmp_path: Path,
) -> None:
    payload = _integration(tmp_path)

    summary = (
        "Integration is blocked by the addressed master-handover-approval gate. "
        f"{STORED_GATE_WAY_OUT}."
    )
    assert payload == {
        "state": "handover-gate-blocked",
        "code": "handover-gate-blocked",
        "error": summary,
        "gateId": "01H",
        "reason": "master-handover-approval gate 01H is open",
        "summary": summary,
        "blockers": [
            {
                "code": "handover-gate-blocked",
                "sentence": "Master-handover-approval gate 01H is undecided "
                f"(master-handover-approval gate 01H is open). {STORED_GATE_WAY_OUT}.",
            }
        ],
        "nextOperation": "review_handover_gate",
        "nextStep": {"summary": summary, "nextOperation": "review_handover_gate"},
    }


def test_the_queues_repair_text_for_a_series_names_no_removed_tool() -> None:
    """The sentence the queue's projection gives out for an unreadable series selection."""

    contract = SimpleNamespace(contract_path=Path("/coordination/series-contract.md"))
    unreadable = AtomicSeriesActivationError("atomic-series-activation-unreadable", "torn")
    with mock.patch.object(
        closeout_projection_activation, "observe_atomic_series", side_effect=unreadable
    ):
        projection = closeout_projection_activation.project_series_activation(
            cast("WorktreeContract", contract)
        )
    problem = projection.problem
    assert problem is not None
    assert problem.repairAction == (
        "select the intended master with worktree_start or worktree_attach; the selecting "
        "transaction archives a malformed activation snapshot and atomically publishes "
        "replacement authority"
    )
    named = set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", problem.repairAction))
    assert named == {"worktree_start", "worktree_attach"}
    assert named <= set(TOOL_RESPONSE_MODELS)
    assert not named & set(REMOVED_TOOLS)
