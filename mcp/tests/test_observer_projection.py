"""Tests for the observer projection read side (slice 3a).

Covers the pure fold (``project_lifecycle``) and its determinism, the inferred
layer (stale -> paused, dormant fleeting -> abandoned, terminal preserved),
append-only corrections, precomputed action availability, the workspace tree
assembly + metrics, the atomic projection write, and the structural surface
readers (provider current-state + worktree enclosures).
"""

import os
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.observer.events import Event
from agents_remember.observer.projection import (
    EnclosureNode,
    EngineProcessFacts,
    LedgerRefNode,
    ProviderNode,
    SeriesNode,
    SeriesSubTaskNode,
    TaskDocNode,
)
from agents_remember.observer.reducer import (
    AnalyticalInputs,
    WorkspaceStructure,
    enclosure_actions,
    project_lifecycle,
    project_workspace,
)
from agents_remember.observer.ulid import new_ulid
from agents_remember.observer.worktree_provider_admission import (
    active_enclosure_worktree_groups,
    admitted_worktree_groups,
    series_retained_lifecycle_ids,
)

T0 = "2026-06-13T18:00:00+00:00"
FRESH = datetime(2026, 6, 13, 18, 0, 30, tzinfo=UTC)  # 30s after T0  (< STALE)
STALE = datetime(2026, 6, 13, 18, 10, 0, tzinfo=UTC)  # 600s after T0 (> STALE, < TTL)
DORMANT = datetime(2026, 6, 13, 19, 30, 0, tzinfo=UTC)  # 5400s after T0 (> TTL)


@dataclass(frozen=True)
class Attribution:
    """Who produced an event and how far it can be trusted.

    The observer never reads one without the other: ``declared`` from a model is a claim,
    ``observed`` from the system is a measurement, and the projection's trust ladder grades
    the pair. Naming the four combinations the suite uses keeps a case from inventing a
    fifth by accident.
    """

    trust: str = "declared"
    actor: str = "model"


DECLARED_BY_MODEL = Attribution()
OBSERVED_BY_MODEL = Attribution(trust="observed")
OBSERVED_BY_SYSTEM = Attribution(trust="observed", actor="system")
INFERRED_BY_SYSTEM = Attribution(trust="inferred", actor="system")


@dataclass(frozen=True)
class EnclosureRef:
    """The enclosure an event points at: its contract path and the repo that contract governs.

    A promotion carries both or neither -- the path identifies the enclosure and the repo id
    is how the projection joins it to a repository -- so they are one reference.
    """

    path: str
    repo_id: str


def _event(
    kind: str,
    *,
    lifecycle_id: str = "LC1",
    ts: str = T0,
    by: Attribution = DECLARED_BY_MODEL,
    enclosure: EnclosureRef | None = None,
    **data: object,
) -> Event:
    return Event(
        id=new_ulid(),
        ts=ts,
        kind=kind,
        trust=by.trust,  # type: ignore[arg-type]
        actor=by.actor,  # type: ignore[arg-type]
        lifecycleId=lifecycle_id,
        enclosure=enclosure.path if enclosure else None,
        repoId=enclosure.repo_id if enclosure else None,
        data=dict(data),
    )


def _started(
    *, ts: str = T0, fleeting: bool = True, phase: str = "request", lifecycle_id: str = "LC1"
) -> Event:
    return _event(
        "lifecycle.started", ts=ts, lifecycle_id=lifecycle_id, fleeting=fleeting, phase=phase
    )


def _enclosure(**overrides: str) -> EnclosureNode:
    base: dict[str, str] = {
        "enclosure": "/c.md",
        "taskId": "T",
        "taskName": "t",
        "repoName": "r",
        "lifecycleId": "",
        "worktreeGroup": "/g",
        "humanReviewStatus": "approved",
        "closeoutStatus": "completed",
        "integrationStatus": "not-started",
        "cleanup": "pending",
    }
    base.update(overrides)
    return EnclosureNode.model_validate(base)


class WorktreeProviderAdmissionTests(unittest.TestCase):
    def test_admits_active_enclosure_backed_build_lifecycle(self) -> None:
        enclosures = [
            _enclosure(
                lifecycleId="LC1",
                worktreeGroup="/coord/worktrees/repo/active-ar",
                closeoutStatus="not-started",
                integrationStatus="not-started",
                cleanup="pending",
            )
        ]
        logs = [[_started(phase="build", lifecycle_id="LC1")]]

        self.assertEqual(admitted_worktree_groups(enclosures, logs, now=FRESH), {"active-ar"})

    def test_rejects_parked_terminal_and_non_provider_phase_groups(self) -> None:
        enclosures = [
            _enclosure(
                lifecycleId="LC1",
                worktreeGroup="/coord/worktrees/repo/active-ar",
                closeoutStatus="not-started",
                integrationStatus="not-started",
                cleanup="pending",
            ),
            _enclosure(
                lifecycleId="LC2",
                worktreeGroup="/coord/worktrees/repo/parked-ar",
                closeoutStatus="completed",
                integrationStatus="not-started",
                cleanup="pending",
            ),
            _enclosure(
                lifecycleId="LC3",
                worktreeGroup="/coord/worktrees/repo/terminal-ar",
                closeoutStatus="not-started",
                integrationStatus="not-started",
                cleanup="pending",
            ),
            _enclosure(
                lifecycleId="LC4",
                worktreeGroup="/coord/worktrees/repo/close-ar",
                closeoutStatus="not-started",
                integrationStatus="not-started",
                cleanup="pending",
            ),
        ]
        logs = [
            [_started(phase="build", lifecycle_id="LC1")],
            [_started(phase="build", lifecycle_id="LC2")],
            [
                _started(phase="build", lifecycle_id="LC3"),
                _event("lifecycle.ended", lifecycle_id="LC3", outcome="completed"),
            ],
            [
                _started(phase="build", lifecycle_id="LC4"),
                _event("lifecycle.phase-changed", lifecycle_id="LC4", phase="close"),
            ],
        ]

        self.assertEqual(admitted_worktree_groups(enclosures, logs, now=FRESH), {"active-ar"})

    def test_active_enclosure_groups_keep_nonterminal_close_phase_for_engine_room(self) -> None:
        enclosures = [
            _enclosure(
                lifecycleId="LC1",
                worktreeGroup="/coord/worktrees/repo/close-ar",
                closeoutStatus="completed",
                integrationStatus="not-started",
                cleanup="pending",
            ),
            _enclosure(
                lifecycleId="LC2",
                worktreeGroup="/coord/worktrees/repo/done-ar",
                closeoutStatus="completed",
                integrationStatus="completed",
                cleanup="pending",
            ),
        ]
        logs = [
            [
                _started(phase="build", lifecycle_id="LC1"),
                _event("lifecycle.phase-changed", lifecycle_id="LC1", phase="close"),
            ],
            [
                _started(phase="build", lifecycle_id="LC2"),
                _event("lifecycle.ended", lifecycle_id="LC2", outcome="completed"),
            ],
        ]

        self.assertEqual(
            active_enclosure_worktree_groups(enclosures, logs, now=FRESH), {"close-ar"}
        )

    def test_active_group_survives_a_pruned_lifecycle_log(self) -> None:
        # The regression: a running worktree (cleanup pending) whose lifecycle event log was retired for
        # inactivity must STILL be active — admission keys on the durable enclosure, not the prunable
        # log. Both the Engine Room set and the provider set recover the live group with NO log present.
        enclosures = [
            _enclosure(
                lifecycleId="LCGONE",
                worktreeGroup="/coord/worktrees/repo/live-ar",
                closeoutStatus="not-started",
                integrationStatus="not-started",
                cleanup="pending",
            )
        ]
        logs: list[list[Event]] = []  # the log was pruned -> no events project for LCGONE
        self.assertEqual(active_enclosure_worktree_groups(enclosures, logs, now=FRESH), {"live-ar"})
        self.assertEqual(admitted_worktree_groups(enclosures, logs, now=FRESH), {"live-ar"})


class SeriesRetentionTests(unittest.TestCase):
    """`series_retained_lifecycle_ids`: a master series' events survive until the series is retired."""

    def _leaf(
        self,
        lifecycle_id: str,
        master: str,
        cleanup: str,
        *,
        enclosure: str = "/c.md",
        repo: str = "r",
    ) -> EnclosureNode:
        return _enclosure(
            lifecycleId=lifecycle_id,
            taskName=master,
            repoName=repo,
            cleanup=cleanup,
            enclosure=enclosure,
        )

    def test_live_master_protects_every_leaf_including_archived_siblings(self) -> None:
        enclosures = [
            self._leaf("LCA", "260628_x", "pending"),
            self._leaf("LCB", "260628_x", "completed"),  # archived sibling of a LIVE master
            self._leaf("LCC", "260628_y", "pending"),  # a different, also-live master
        ]
        self.assertEqual(
            series_retained_lifecycle_ids(enclosures, now=FRESH), {"LCA", "LCB", "LCC"}
        )

    def test_fully_archived_master_without_readable_timestamp_is_released(self) -> None:
        enclosures = [
            self._leaf("LCA", "260628_x", "completed", enclosure="/does-not-exist.md"),
            self._leaf("LCB", "260628_x", "abandoned", enclosure="/nope.md"),
        ]
        self.assertEqual(series_retained_lifecycle_ids(enclosures, now=FRESH), set())

    def test_archived_master_is_retained_within_grace_then_released_after(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = Path(tmp) / "series-contract.md"
            contract.write_text("x", encoding="utf-8")
            enclosures = [self._leaf("LCA", "260628_z", "completed", enclosure=str(contract))]

            # Finalized one day before now -> inside the one-week grace -> still retained.
            within = (FRESH - timedelta(days=1)).timestamp()
            os.utime(contract, (within, within))
            self.assertEqual(series_retained_lifecycle_ids(enclosures, now=FRESH), {"LCA"})

            # Finalized eight days before now -> past the grace -> released for pruning.
            past = (FRESH - timedelta(days=8)).timestamp()
            os.utime(contract, (past, past))
            self.assertEqual(series_retained_lifecycle_ids(enclosures, now=FRESH), set())

    def test_enclosure_without_taskname_is_not_series_protected(self) -> None:
        # A fleeting/standalone enclosure (no master task) keeps the ordinary inactivity TTL.
        enclosures = [self._leaf("LCA", "", "pending")]
        self.assertEqual(series_retained_lifecycle_ids(enclosures, now=FRESH), set())


class FoldTests(unittest.TestCase):
    def test_seed_from_started(self) -> None:
        proj = project_lifecycle([_started()], now=FRESH)
        self.assertEqual(
            (proj.id, proj.state, proj.phase, proj.fleeting), ("LC1", "running", "request", True)
        )
        self.assertEqual(proj.startedAt, T0)
        self.assertFalse(proj.inferred)
        self.assertEqual(proj.tokens, 0)

    def test_phase_block_resume(self) -> None:
        log = [
            _started(ts=T0),
            _event("lifecycle.phase-changed", ts="2026-06-13T18:00:05+00:00", phase="build"),
            _event("lifecycle.blocked", ts="2026-06-13T18:00:10+00:00", ask={"kind": "question"}),
            _event("lifecycle.resumed", ts="2026-06-13T18:00:20+00:00"),
        ]
        proj = project_lifecycle(log, now=FRESH)
        self.assertEqual((proj.state, proj.phase), ("running", "build"))
        self.assertIsNone(proj.ask)

    def test_blocked_keeps_ask(self) -> None:
        log = [
            _started(),
            _event("lifecycle.blocked", ts="2026-06-13T18:00:10+00:00", ask={"kind": "decision"}),
        ]
        proj = project_lifecycle(log, now=FRESH)
        self.assertEqual(proj.state, "blocked")
        self.assertEqual(proj.ask, {"kind": "decision"})

    def test_awaiting_developer_then_resume(self) -> None:
        # NOTIFY-AND-CONTINUE turn end: non-terminal awaiting-developer
        # carries the summary on the ask carrier; resume clears it back to running.
        await_only = project_lifecycle(
            [
                _started(),
                _event(
                    "lifecycle.awaiting-developer",
                    ts="2026-06-13T18:00:10+00:00",
                    summary="Turn complete; your move.",
                ),
            ],
            now=FRESH,
        )
        self.assertEqual(await_only.state, "awaiting-developer")
        self.assertEqual(await_only.ask, {"summary": "Turn complete; your move."})
        resumed = project_lifecycle(
            [
                _started(),
                _event("lifecycle.awaiting-developer", ts="2026-06-13T18:00:10+00:00", summary="s"),
                _event("lifecycle.resumed", ts="2026-06-13T18:00:20+00:00"),
            ],
            now=FRESH,
        )
        self.assertEqual(resumed.state, "running")
        self.assertIsNone(resumed.ask)

    def test_tokens_aggregate(self) -> None:
        log = [
            _started(),
            _event(
                "tool.completed",
                ts="2026-06-13T18:00:05+00:00",
                by=OBSERVED_BY_MODEL,
                tool="a",
                tokens=100,
                ok=True,
            ),
            _event(
                "tool.completed",
                ts="2026-06-13T18:00:10+00:00",
                by=OBSERVED_BY_MODEL,
                tool="b",
                tokens=50,
                ok=True,
            ),
        ]
        self.assertEqual(project_lifecycle(log, now=FRESH).tokens, 150)

    def test_promote_makes_persistent(self) -> None:
        log = [
            _started(fleeting=True),
            _event(
                "lifecycle.promoted",
                ts="2026-06-13T18:00:05+00:00",
                by=OBSERVED_BY_SYSTEM,
                enclosure=EnclosureRef("/c.md", "repo-a"),
                scope="repo-a",
            ),
        ]
        proj = project_lifecycle(log, now=FRESH)
        self.assertFalse(proj.fleeting)
        self.assertEqual((proj.scope, proj.enclosure, proj.repoId), ("repo-a", "/c.md", "repo-a"))

    def test_ended_outcomes(self) -> None:
        done = project_lifecycle(
            [
                _started(),
                _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="completed"),
            ],
            now=FRESH,
        )
        dropped = project_lifecycle(
            [
                _started(),
                _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="abandoned"),
            ],
            now=FRESH,
        )
        self.assertEqual(done.state, "completed")
        self.assertEqual(dropped.state, "abandoned")

    def test_empty_log_raises(self) -> None:
        with self.assertRaises(ValueError):
            project_lifecycle([], now=FRESH)


class DeterminismTests(unittest.TestCase):
    def test_same_log_same_projection(self) -> None:
        log = [
            _started(),
            _event("lifecycle.phase-changed", ts="2026-06-13T18:00:05+00:00", phase="build"),
            _event(
                "tool.completed",
                ts="2026-06-13T18:00:10+00:00",
                by=OBSERVED_BY_MODEL,
                tool="a",
                tokens=7,
                ok=True,
            ),
        ]
        first = project_lifecycle(log, now=STALE).model_dump()
        second = project_lifecycle(log, now=STALE).model_dump()
        self.assertEqual(first, second)


class InferredLayerTests(unittest.TestCase):
    def test_fresh_running_stays_running(self) -> None:
        proj = project_lifecycle([_started()], now=FRESH)
        self.assertEqual(proj.state, "running")
        self.assertFalse(proj.inferred)
        self.assertEqual(proj.staleSeconds, 30.0)

    def test_stale_running_projects_paused(self) -> None:
        proj = project_lifecycle([_started()], now=STALE)
        self.assertEqual(proj.state, "paused")
        self.assertTrue(proj.inferred)

    def test_fleeting_dormant_projects_abandoned(self) -> None:
        proj = project_lifecycle([_started(fleeting=True)], now=DORMANT)
        self.assertEqual(proj.state, "abandoned")
        self.assertTrue(proj.inferred)

    def test_persistent_dormant_not_abandoned(self) -> None:
        log = [
            _started(fleeting=True),
            _event(
                "lifecycle.promoted",
                ts="2026-06-13T18:00:05+00:00",
                by=OBSERVED_BY_SYSTEM,
                scope="repo-a",
            ),
        ]
        proj = project_lifecycle(log, now=DORMANT)
        self.assertEqual(proj.state, "paused")  # stale, but never auto-abandoned
        self.assertTrue(proj.inferred)

    def test_terminal_survives_staleness(self) -> None:
        proj = project_lifecycle(
            [
                _started(),
                _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="completed"),
            ],
            now=DORMANT,
        )
        self.assertEqual(proj.state, "completed")
        self.assertFalse(proj.inferred)


class CorrectionTests(unittest.TestCase):
    def test_correction_overrides_state(self) -> None:
        ended = _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="completed")
        correction = _event(
            "correction.recorded",
            ts="2026-06-13T18:00:10+00:00",
            by=INFERRED_BY_SYSTEM,
            corrects=ended.id,
            state="abandoned",
        )
        proj = project_lifecycle([_started(), ended, correction], now=FRESH)
        self.assertEqual(proj.state, "abandoned")

    def test_malformed_correction_ignored(self) -> None:
        ended = _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="completed")
        bogus = _event(
            "correction.recorded",
            ts="2026-06-13T18:00:10+00:00",
            by=INFERRED_BY_SYSTEM,
            corrects=ended.id,
            state="not-a-state",
        )
        proj = project_lifecycle([_started(), ended, bogus], now=FRESH)
        self.assertEqual(proj.state, "completed")


class ActionAvailabilityTests(unittest.TestCase):
    def test_resume_enabled_only_when_blocked(self) -> None:
        blocked = project_lifecycle(
            [
                _started(),
                _event(
                    "lifecycle.blocked", ts="2026-06-13T18:00:10+00:00", ask={"kind": "question"}
                ),
            ],
            now=FRESH,
        )
        running = project_lifecycle([_started()], now=FRESH)
        self.assertTrue(_action(blocked.actions, "resume").enabled)
        self.assertFalse(_action(running.actions, "resume").enabled)

    def test_integrate_available_after_closeout(self) -> None:
        actions = enclosure_actions(_enclosure())
        self.assertTrue(_action(actions, "integrate").enabled)
        self.assertFalse(_action(actions, "cleanup").enabled)

    def test_integrate_blocked_before_closeout(self) -> None:
        actions = enclosure_actions(_enclosure(closeoutStatus="not-started"))
        integrate = _action(actions, "integrate")
        self.assertFalse(integrate.enabled)
        self.assertEqual(integrate.disabledReason, "closeout not complete")

    def test_cleanup_available_after_integration(self) -> None:
        actions = enclosure_actions(_enclosure(integrationStatus="completed"))
        self.assertFalse(_action(actions, "integrate").enabled)
        self.assertTrue(_action(actions, "cleanup").enabled)


class WorkspaceTests(unittest.TestCase):
    def test_active_worktree_groups_passthrough_sorted(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1", ts=T0)]],
            structure=WorkspaceStructure(
                enclosures=[_enclosure()],
                providers=[],
                active_worktree_groups=["b-ar", "a-ar"],
            ),
            now=FRESH,
        )
        # The Topology active set is exposed deterministically (sorted) on the projection.
        self.assertEqual(proj.activeWorktreeGroups, ["a-ar", "b-ar"])

    def test_active_worktree_groups_default_empty(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1", ts=T0)]],
            structure=WorkspaceStructure(enclosures=[_enclosure()], providers=[]),
            now=FRESH,
        )
        self.assertEqual(proj.activeWorktreeGroups, [])

    def test_tree_and_metrics(self) -> None:
        logs = [
            [_started(lifecycle_id="LC1", ts=T0)],
            [
                _started(lifecycle_id="LC2", ts=T0),
                _event(
                    "lifecycle.blocked",
                    lifecycle_id="LC2",
                    ts="2026-06-13T18:00:05+00:00",
                    ask={"kind": "question"},
                ),
            ],
        ]
        proj = project_workspace(
            logs,
            structure=WorkspaceStructure(
                enclosures=[_enclosure()],
                providers=[
                    ProviderNode(
                        id="cgc", state="ready", ok=True, watcherUp=True, indexingState="indexed"
                    )
                ],
            ),
            now=FRESH,
        )
        # 2 event-backed (running, blocked) + 1 synthesized persistent paused from the enclosure
        self.assertEqual(len(proj.lifecycles), 3)
        self.assertEqual(proj.metrics.lifecycleCount, 3)
        self.assertEqual(proj.metrics.runningCount, 1)
        self.assertEqual(proj.metrics.blockedCount, 1)
        self.assertEqual(proj.metrics.pausedCount, 1)
        self.assertEqual(proj.generatedAt, FRESH.isoformat())
        self.assertTrue(_action(proj.enclosures[0].actions, "integrate").enabled)
        synthesized = next(lc for lc in proj.lifecycles if lc.state == "paused")
        self.assertEqual(
            (
                synthesized.id,
                synthesized.fleeting,
                synthesized.inferred,
                synthesized.phase,
                synthesized.lastEventTs,
            ),
            ("r/t", False, True, "close", ""),
        )

    def test_series_token_total_sums_linked_leaf_lifecycles(self) -> None:
        logs = [
            [
                _started(lifecycle_id="LC1", ts=T0),
                _event(
                    "tool.completed",
                    lifecycle_id="LC1",
                    ts="2026-06-13T18:00:05+00:00",
                    tool="task_doc",
                    tokens=100,
                    ok=True,
                ),
            ],
            [
                _started(lifecycle_id="LC2", ts=T0),
                _event(
                    "tool.completed",
                    lifecycle_id="LC2",
                    ts="2026-06-13T18:00:06+00:00",
                    tool="read_ar_files",
                    tokens=50,
                    ok=True,
                ),
            ],
        ]
        task_documents = [
            TaskDocNode(
                id="1",
                lifecycleId="LC1",
                repository="repo-a",
                title="Leaf A",
                status="inProgress",
                kind="subTask",
                docPath="/tasks/repo-a/series/01_a.json",
            ),
            TaskDocNode(
                id="2",
                lifecycleId="LC2",
                repository="repo-a",
                title="Leaf B",
                status="inProgress",
                kind="subTask",
                docPath="/tasks/repo-a/series/02_b.json",
            ),
        ]
        series = [
            SeriesNode(
                seriesId="series",
                repository="repo-a",
                title="Series",
                status="inProgress",
                docPath="/tasks/repo-a/series/task.json",
                subTasks=[
                    SeriesSubTaskNode(
                        number="1", name="Leaf A", file="01_a.md", status="inProgress"
                    ),
                    SeriesSubTaskNode(
                        number="2", name="Leaf B", file="02_b.md", status="inProgress"
                    ),
                    SeriesSubTaskNode(
                        number="3", name="Missing doc", file="03_c.md", status="planning"
                    ),
                ],
            )
        ]

        proj = project_workspace(
            logs,
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
            given=AnalyticalInputs(task_documents=task_documents, series=series),
        )

        self.assertEqual(proj.analytics.series[0].seriesTokenTotal, 150)

    def test_persistent_synthesis_skips_enclosure_with_event_lifecycle(self) -> None:
        # An enclosure already represented by an event-backed lifecycle is not duplicated.
        proj = project_workspace(
            [[_started(lifecycle_id="LC1", ts=T0)]],
            structure=WorkspaceStructure(enclosures=[_enclosure(lifecycleId="LC1")], providers=[]),
            now=FRESH,
        )
        self.assertEqual([lc.id for lc in proj.lifecycles], ["LC1"])

    def test_persistent_synthesis_skips_abandoned_and_reopened_enclosures(self) -> None:
        # No worktree, no persistent lifecycle: an abandoned enclosure's worktrees were
        # discarded and a reopened one awaits its next worktree_start — neither may synthesize
        # a paused zombie into the operations tree.
        proj = project_workspace(
            [],
            structure=WorkspaceStructure(
                enclosures=[
                    _enclosure(enclosure="/a.md", lifecycleId="LC-GONE", cleanup="abandoned"),
                    _enclosure(enclosure="/b.md", lifecycleId="", cleanup="reopened"),
                    _enclosure(enclosure="/c.md", lifecycleId="", cleanup="pending"),
                ],
                providers=[],
            ),
            now=FRESH,
        )
        self.assertEqual([lc.enclosure for lc in proj.lifecycles], ["/c.md"])

    def test_abandoned_enclosure_terminalizes_its_event_backed_lifecycle(self) -> None:
        # worktree_abandon records cleanup=abandoned in the contract but may not own the
        # lifecycle's event log (the ambient dies with a server restart), and the store's
        # single-writer invariant forbids a foreign lifecycle.ended append — so the READER
        # projects the terminal state from the contract.
        log = [
            _started(lifecycle_id="LC-DEAD", ts=T0),
            _event(
                "lifecycle.promoted",
                lifecycle_id="LC-DEAD",
                ts="2026-06-13T18:00:05+00:00",
                by=OBSERVED_BY_SYSTEM,
                enclosure=EnclosureRef("/c.md", "r"),
                scope="r",
            ),
        ]
        proj = project_workspace(
            [log],
            structure=WorkspaceStructure(
                enclosures=[_enclosure(lifecycleId="LC-DEAD", cleanup="abandoned")], providers=[]
            ),
            now=FRESH,
        )
        dead = next(lc for lc in proj.lifecycles if lc.id == "LC-DEAD")
        self.assertEqual(dead.state, "abandoned")

    def test_stale_persistent_lifecycle_without_enclosure_is_removed(self) -> None:
        proj = project_workspace(
            [
                [
                    _started(lifecycle_id="LC1", ts=T0),
                    _event(
                        "lifecycle.promoted",
                        lifecycle_id="LC1",
                        ts="2026-06-13T18:00:05+00:00",
                        by=OBSERVED_BY_SYSTEM,
                        enclosure=EnclosureRef("/deleted.md", "repo-a"),
                    ),
                ]
            ],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=STALE,
        )
        self.assertEqual(proj.lifecycles, [])
        self.assertEqual(proj.metrics.lifecycleCount, 0)

    def test_terminal_persistent_lifecycle_without_enclosure_is_removed(self) -> None:
        proj = project_workspace(
            [
                [
                    _started(lifecycle_id="LC1", ts=T0),
                    _event(
                        "lifecycle.promoted",
                        lifecycle_id="LC1",
                        ts="2026-06-13T18:00:05+00:00",
                        by=OBSERVED_BY_SYSTEM,
                        enclosure=EnclosureRef("/deleted.md", "repo-a"),
                    ),
                    _event(
                        "lifecycle.ended",
                        lifecycle_id="LC1",
                        ts="2026-06-13T18:00:10+00:00",
                        outcome="completed",
                    ),
                ]
            ],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
        )
        self.assertEqual(proj.lifecycles, [])
        self.assertEqual(proj.metrics.lifecycleCount, 0)

    def test_fresh_promotion_window_without_enclosure_is_kept(self) -> None:
        proj = project_workspace(
            [
                [
                    _started(lifecycle_id="LC1", ts=T0),
                    _event(
                        "lifecycle.promoted",
                        lifecycle_id="LC1",
                        ts="2026-06-13T18:00:05+00:00",
                        by=OBSERVED_BY_SYSTEM,
                        enclosure=EnclosureRef("/incoming.md", "repo-a"),
                    ),
                ]
            ],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
        )
        self.assertEqual([lc.id for lc in proj.lifecycles], ["LC1"])

    def test_fresh_blocked_promotion_window_without_enclosure_is_kept(self) -> None:
        proj = project_workspace(
            [
                [
                    _started(lifecycle_id="LC1", ts=T0),
                    _event(
                        "lifecycle.promoted",
                        lifecycle_id="LC1",
                        ts="2026-06-13T18:00:05+00:00",
                        by=OBSERVED_BY_SYSTEM,
                        enclosure=EnclosureRef("/incoming.md", "repo-a"),
                    ),
                    _event(
                        "lifecycle.blocked",
                        lifecycle_id="LC1",
                        ts="2026-06-13T18:00:20+00:00",
                        ask={"kind": "decision", "prompt": "Approve?"},
                    ),
                ]
            ],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
        )
        self.assertEqual([(lc.id, lc.state) for lc in proj.lifecycles], [("LC1", "blocked")])

    def test_reowned_enclosure_removes_old_event_lifecycle(self) -> None:
        logs = [
            [
                _started(lifecycle_id="OLD", ts=T0),
                _event(
                    "lifecycle.promoted",
                    lifecycle_id="OLD",
                    ts="2026-06-13T18:00:05+00:00",
                    by=OBSERVED_BY_SYSTEM,
                    enclosure=EnclosureRef("/c.md", "repo-a"),
                ),
            ],
            [
                _started(lifecycle_id="NEW", ts=T0),
                _event(
                    "lifecycle.promoted",
                    lifecycle_id="NEW",
                    ts="2026-06-13T18:00:05+00:00",
                    by=OBSERVED_BY_SYSTEM,
                    enclosure=EnclosureRef("/c.md", "repo-a"),
                ),
            ],
        ]
        proj = project_workspace(
            logs,
            structure=WorkspaceStructure(
                enclosures=[_enclosure(enclosure="/c.md", lifecycleId="NEW")], providers=[]
            ),
            now=FRESH,
        )
        self.assertEqual([lc.id for lc in proj.lifecycles], ["NEW"])

    def test_fleeting_lifecycle_does_not_need_enclosure(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1", ts=T0, fleeting=True)]],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
        )
        self.assertEqual([lc.id for lc in proj.lifecycles], ["LC1"])

    def test_legacy_blank_lifecycle_enclosure_keeps_event_lifecycle(self) -> None:
        proj = project_workspace(
            [
                [
                    _started(lifecycle_id="LC1", ts=T0),
                    _event(
                        "lifecycle.promoted",
                        lifecycle_id="LC1",
                        ts="2026-06-13T18:00:05+00:00",
                        by=OBSERVED_BY_SYSTEM,
                        enclosure=EnclosureRef("/c.md", "repo-a"),
                    ),
                ]
            ],
            structure=WorkspaceStructure(
                enclosures=[_enclosure(enclosure="/c.md", lifecycleId="")], providers=[]
            ),
            now=FRESH,
        )
        self.assertEqual([lc.id for lc in proj.lifecycles], ["LC1"])

    def test_dormant_persistent_worktree_stays_out_of_the_attention_queue(self) -> None:
        # A synthesized paused persistent worktree (no events) is the hangar's job, not the queue.
        proj = project_workspace(
            [], structure=WorkspaceStructure(enclosures=[_enclosure()], providers=[]), now=FRESH
        )
        self.assertEqual([lc.state for lc in proj.lifecycles], ["paused"])
        self.assertEqual(proj.analytics.attentionQueue, [])


def _action(actions: list, name: str):  # type: ignore[type-arg]
    return next(action for action in actions if action.action == name)


def _facts(
    *,
    contract: dict | None = None,  # type: ignore[type-arg]
    status: dict | None = None,  # type: ignore[type-arg]
    guidance: dict | None = None,  # type: ignore[type-arg]
    ledger_rows: list[LedgerRefNode] | None = None,
    ledger_row_count: int = 0,
) -> EngineProcessFacts:
    base_contract: dict[str, object] = {
        "contract_path": "/c.md",
        "task_id": "T",
        "task_name": "demo",
        "repo_name": "r",
        "worktree_group": "/w/r/grp",
        "memory_mode": "external",
        "code_source_branch": "main",
        "code_base_commit": "abc1234",
        "code_repo_path": "/repo",
        "code_work_branch": "ar/x",
        "code_worktree": "/w/r/grp/wt",
        "code_commit": "",
        "memory_source_branch": "main",
        "memory_base_commit": "def5678",
        "memory_repo_path": "/mrepo",
        "memory_work_branch": "ar/x",
        "memory_content_commit": "",
        "memory_worktree": "/w/r/grp/mem",
        "ledger_path": "/w/r/grp/mem/memory.md",
        "human_review_status": "pending-review",
        "closeout_status": "not-started",
        "integration_status": "not-started",
        "cleanup": "pending",
        "lifecycle_id": "LC1",
    }
    base_contract.update(contract or {})
    base_guidance: dict[str, object] = {
        "phase": "worktree-started",
        "summary": "continue",
        "nextOperation": "continue_work",
    }
    base_guidance.update(guidance or {})
    return EngineProcessFacts(
        contract=base_contract,
        guidance=base_guidance,
        status=status,
        ledger_rows=ledger_rows or [],
        ledger_row_count=ledger_row_count,
    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
