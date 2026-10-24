"""MIK-R199 round-1 finding L192-F02: a retired provider gate must not come back as a current block.

The durable pre-contract progress file is read by one owning boundary
(``read_start_progress``) and its entries feed two surfaces: the attention queue
(``build_attention_queue``) and the Engine Room process map (``build_engine_processes``). A file
written before the search providers were retired carries ``phase: provider-blocked``; no surviving
producer writes that phase, so it is an obsolete record and must reach neither surface. Real
pre-contract blocks (stale base, source lineage, memory compatibility) must still arrive, with the
actionable-drift alarm still ranked above them.

Before the repair the reader spread every schema-valid payload into the projection, so both surfaces
rendered the retired gate. These cases fail on that candidate and pass after the reader retires the
phase at its owning boundary.

The preservation controls below pin the other side of that repair: a schema-valid beat whose phase
is *not* retired must still reach both surfaces, and retirement must stay a named set -- the real
blocks, the live progress beats, and even a phase no producer writes yet all survive the filter.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from agents_remember.controlplane.attention_dismissals import AttentionObservation
from agents_remember.observer.projection import AttentionItem, DriftSnapshotNode
from agents_remember.observer.reducer_impl._attention import build_attention_queue
from agents_remember.observer.reducer_impl._processes import build_engine_processes
from agents_remember.observer.reducer_impl._types import AnalyticalInputs
from agents_remember.serving.projections.snapshots_impl._analytics import (
    read_start_progress_entries,
)
from agents_remember.worktrees.start_progress import (
    RETIRED_START_PHASES,
    START_PROGRESS_SCHEMA,
    is_retired_start_progress,
)

NOW = datetime(2026, 10, 22, tzinfo=UTC)


def _beat(root: Path, entry: dict[str, object]) -> Path:
    """Write one start-progress file: ``entry`` overrides the payload's own fields."""
    fields: dict[str, object] = {
        "schema": START_PROGRESS_SCHEMA,
        "repoName": "agents-remember",
        "taskName": "leaf",
        "worktreeName": "leaf",
        "worktreeGroup": "worktrees/one",
        "phase": "preflight",
        "memoryMode": "external",
        "codeSourceBranch": "main",
        "codeBaseCommit": "26d5915f94e02533b9c6b377bbfb8c0e3dfc5175",
        "codeRepoPath": "/code",
        "codeWorktree": "/code-worktree",
        "completedPhases": ["preflight"],
        "choices": [],
        "updatedAt": "2026-10-22T00:00:00+00:00",
    }
    fields.update(entry)
    path = (
        root
        / "temp"
        / "worktree-start"
        / str(fields["repoName"])
        / f"{fields['worktreeName']}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields), encoding="utf-8")
    return path


def _drift() -> DriftSnapshotNode:
    return DriftSnapshotNode(
        repository="repo-a",
        branch="main",
        actionableCount=3,
        checkedAt="2026-10-22T00:00:00+00:00",
        actionableFingerprint="l192",
    )


def _observation() -> AttentionObservation:
    return AttentionObservation(kind="blocked-start", observed_before=NOW)


def _queue(entries: list[dict[str, Any]], *, with_drift: bool = False) -> list[AttentionItem]:
    """The attention queue for these start-progress entries, optionally beside actionable drift."""
    return build_attention_queue(
        AnalyticalInputs(
            engine_start_progress=entries,
            drift_snapshots=[_drift()] if with_drift else [],
        )
    )


def test_a_persisted_provider_gate_reaches_neither_attention_nor_the_process_map(
    tmp_path: Path,
) -> None:
    root = tmp_path / "coord"
    _beat(
        root,
        {
            "taskName": "provider-gated",
            "worktreeName": "provider-gated",
            "phase": "provider-blocked",
            "blockedReason": "provider runtime setup config is missing",
            "choices": ["retry", "disabled"],
        },
    )
    observation = _observation()

    entries = read_start_progress_entries(root, now=NOW, observation=observation)

    assert entries == [], "the retired provider gate was read back as a current start"
    assert observation.complete is True, "an obsolete record must not mark the read incomplete"
    assert observation.existing == set(), "the obsolete gate must not be observed"
    assert observation.healthy == set(), "the obsolete gate must not be observed"

    queue = build_attention_queue(
        AnalyticalInputs(engine_start_progress=entries, drift_snapshots=[_drift()])
    )
    assert [item.kind for item in queue] == ["actionable-drift"]
    assert build_engine_processes([], [], entries) == []


def test_real_pre_contract_blocks_still_reach_both_surfaces(tmp_path: Path) -> None:
    root = tmp_path / "coord"
    _beat(
        root,
        {
            "taskName": "memory-gated",
            "worktreeName": "memory-gated",
            "phase": "memory-blocked",
            "blockedReason": "memory worktree does not match the ledger base",
            "worktreeGroup": "worktrees/memory",
        },
    )
    _beat(
        root,
        {
            "taskName": "stale-gated",
            "worktreeName": "stale-gated",
            "phase": "stale-base-blocked",
            "blockedReason": "base moved; fast-forward needed",
            "worktreeGroup": "worktrees/stale",
        },
    )

    entries = read_start_progress_entries(root, now=NOW, observation=_observation())
    assert {entry["phase"] for entry in entries} == {"memory-blocked", "stale-base-blocked"}

    queue = build_attention_queue(
        AnalyticalInputs(engine_start_progress=entries, drift_snapshots=[_drift()])
    )
    assert [item.kind for item in queue] == ["actionable-drift", "blocked-start", "blocked-start"]
    assert {item.severity for item in queue[1:]} == {"warn"}
    assert {item.detail for item in queue[1:]} == {
        "memory worktree does not match the ledger base",
        "base moved; fast-forward needed",
    }

    nodes = build_engine_processes([], [], entries)
    assert {node.phase for node in nodes} == {"memory-compatibility", "preflight"}
    assert {node.health for node in nodes} == {"blocked"}


def test_a_happy_path_beat_is_progress_not_an_alarm(tmp_path: Path) -> None:
    root = tmp_path / "coord"
    _beat(
        root,
        {
            "taskName": "in-flight",
            "worktreeName": "in-flight",
            "phase": "preflight",
            "worktreeGroup": "worktrees/in-flight",
        },
    )

    entries = read_start_progress_entries(root, now=NOW, observation=_observation())
    assert len(entries) == 1
    assert build_attention_queue(AnalyticalInputs(engine_start_progress=entries)) == []
    nodes = build_engine_processes([], [], entries)
    assert [node.health for node in nodes] == ["running"]


#: Every phase a surviving producer writes, with what it becomes on the two surfaces. The two
#: ``*-blocked`` rows carry a ``blockedReason``; ``preflight`` and ``code-worktree`` are the live
#: progress beats the start writes on its way to a contract. ``source-lineage-blocked`` is in the
#: producer's vocabulary but ``write_start_progress`` refuses to persist it, so it can never appear
#: in a file and is covered by the retirement-set case instead.
_REAL_PHASES: tuple[tuple[str, str | None, str, str], ...] = (
    (
        "memory-blocked",
        "memory worktree does not match the ledger base",
        "memory-compatibility",
        "blocked",
    ),
    ("stale-base-blocked", "base moved; fast-forward needed", "preflight", "blocked"),
    ("preflight", None, "preflight", "running"),
    ("code-worktree", None, "code-worktree", "running"),
)


@pytest.mark.parametrize(
    ("phase", "blocked_reason", "process_phase", "health"),
    _REAL_PHASES,
    ids=[row[0] for row in _REAL_PHASES],
)
def test_a_schema_valid_beat_whose_phase_is_not_retired_still_reaches_both_surfaces(
    tmp_path: Path,
    phase: str,
    blocked_reason: str | None,
    process_phase: str,
    health: str,
) -> None:
    """Suppression is keyed on the retired phase, never on "does not parse" or "looks stale".

    A persisted beat whose phase a producer still writes is a current start: the read boundary
    must hand it on, the observation must still see it (blocked or healthy), and the two surfaces
    must render it -- a blocked-start ``warn`` plus a blocked node for a ``blockedReason`` beat,
    progress only for a happy-path one. Drift stays ranked above the warn.
    """
    root = tmp_path / "coord"
    group = f"worktrees/{phase}"
    payload: dict[str, object] = {
        "taskName": phase,
        "worktreeName": phase,
        "worktreeGroup": group,
        "phase": phase,
    }
    if blocked_reason is not None:
        payload["blockedReason"] = blocked_reason
    _beat(root, payload)

    observation = _observation()
    entries = read_start_progress_entries(root, now=NOW, observation=observation)

    assert [read_back["phase"] for read_back in entries] == [phase], (
        "a phase a current producer writes was suppressed at the read boundary"
    )
    assert observation.complete is True, "a schema-valid live beat is not an unreadable file"
    item_id = f"blocked-start:{group}"
    assert observation.existing == {item_id}, "a live beat must still be observed"
    if blocked_reason is not None:
        assert observation.healthy == set(), "a blocked beat is present, not recovered"
    else:
        assert observation.healthy == {item_id}, "a progress beat is present and healthy"

    queue = _queue(entries, with_drift=blocked_reason is not None)
    nodes = build_engine_processes([], [], entries)

    assert [(node.phase, node.health) for node in nodes] == [(process_phase, health)]
    if blocked_reason is not None:
        assert [item.kind for item in queue] == ["actionable-drift", "blocked-start"], (
            "drift must stay ranked above a live pre-contract block"
        )
        assert [item.severity for item in queue] == ["warn", "warn"]
        assert queue[1].detail == blocked_reason
        assert queue[1].enclosure == group
    else:
        assert queue == [], "a progress beat is observability, not an alarm"


def test_retirement_is_the_named_provider_phase_not_a_broad_filter() -> None:
    """Retirement is a named set, not a heuristic: only ``provider-blocked`` is obsolete.

    Every phase a surviving producer writes reads as current, whether or not it carries a
    ``blockedReason`` -- so the filter can never swallow a real block by accident, and adding a new
    phase does not require touching it.
    """
    producer_phases = {
        "stale-base-blocked",
        "source-lineage-blocked",
        "preflight",
        "code-worktree",
        "memory-blocked",
    }
    assert set(RETIRED_START_PHASES) == {"provider-blocked"}
    assert is_retired_start_progress({"phase": "provider-blocked"}) is True
    assert {
        phase
        for phase in {"provider-blocked"} | producer_phases
        if is_retired_start_progress({"phase": phase})
    } == {"provider-blocked"}
    for phase in producer_phases:
        # Retirement keys on the phase name alone: a real block is a current block.
        assert is_retired_start_progress({"phase": phase, "blockedReason": "gate"}) is False
    assert is_retired_start_progress({}) is False, "a payload without a phase is not retired"


def test_a_future_gate_phase_the_producer_does_not_write_is_not_suppressed(tmp_path: Path) -> None:
    """``admission-blocked`` and ``lock-blocked`` are a future admission/lock gate, not a supported
    phase today: no current producer writes either name, and the process map has no phase for them.

    This case exists so a later gate cannot be silently dropped. The read boundary suppresses only
    the named retired set, so a beat carrying one of these names still reaches attention and the
    process map, ranked below drift, exactly like a real block. That they arrive as
    ``phase == "unknown"`` nodes is the process map honestly saying it has no lane for the name yet
    -- reaching the human is independent of being fully modelled.
    """
    root = tmp_path / "coord"
    future_gates = {
        "admission-blocked": "awaiting admission arbiter",
        "lock-blocked": "another start holds the worktree lock",
    }
    for phase, reason in future_gates.items():
        _beat(
            root,
            {
                "taskName": phase,
                "worktreeName": phase,
                "worktreeGroup": f"worktrees/{phase}",
                "phase": phase,
                "blockedReason": reason,
            },
        )

    entries = read_start_progress_entries(root, now=NOW, observation=_observation())
    assert {read_back["phase"] for read_back in entries} == set(future_gates), (
        "an unknown blocked phase was dropped as if it were retired"
    )

    queue = _queue(entries, with_drift=True)
    assert queue[0].kind == "actionable-drift", (
        "drift must stay ranked above an unknown-phase block"
    )
    warns = queue[1:]
    assert [item.kind for item in warns] == ["blocked-start", "blocked-start"]
    assert {item.detail for item in warns} == set(future_gates.values())
    assert {item.enclosure for item in warns} == {f"worktrees/{phase}" for phase in future_gates}

    nodes = build_engine_processes([], [], entries)
    assert {node.worktreeGroup for node in nodes} == {
        f"worktrees/{phase}" for phase in future_gates
    }
    assert {node.health for node in nodes} == {"blocked"}
    assert {node.phase for node in nodes} == {"unknown"}
