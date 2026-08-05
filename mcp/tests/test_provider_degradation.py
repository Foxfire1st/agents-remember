from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.mcp.config import McpRuntimeConfig, load_config
from agents_remember.mcp.provider_degradation_settings import ProviderDegradationSettings
from agents_remember.observer import observer_root
from agents_remember.providers.degradation import (
    ProviderDegradationStore,
    classify_degradation,
    evaluate_provider_degradation,
)
from agents_remember.providers.metrics import (
    PROVIDER_INDEX_STATE_SCHEMA,
    ContainerSample,
    MetricsSnapshot,
    ProviderMetricsStore,
)
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    terminal_catalog_path,
)

T1 = "2026-07-07T12:00:00+00:00"


def unreachable_stopper(_: McpRuntimeConfig) -> dict[str, Any]:
    """The failsafe action for evaluations that must never reach the critical branch.

    ``evaluate_provider_degradation`` requires the stop action rather than defaulting to one,
    so a test that is about some other transition still has to say what would happen if the
    failsafe fired. Saying "this must not fire" is the honest answer, and it fails loudly if
    the evaluation the test is about ever starts firing it.
    """
    raise AssertionError("the critical failsafe must not fire in this evaluation")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def config_payload(root: Path, degradation: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "version": 1,
        "coordinationRoot": str(root / "ar-coordination"),
        "workspaceRoot": str(root / "workspace"),
        "repositories": {"agents-remember": {}},
        "providers": {},
    }
    if degradation is not None:
        payload["providerDegradation"] = degradation
    return payload


def load_test_config(root: Path, degradation: dict[str, Any] | None = None) -> McpRuntimeConfig:
    path = root / ".codex" / "mcp" / "settings.json"
    write_json(path, config_payload(root, degradation))
    return load_config(path)


def record_memory_sample(config: McpRuntimeConfig, *, name: str, ratio: float) -> None:
    ProviderMetricsStore(config.coordination_root).record(
        MetricsSnapshot(
            schema="ar-provider-metrics-sample/v1",
            sampledAt=T1,
            containers=[
                ContainerSample(
                    name=name,
                    provider="grepai-memory",
                    instance="workspace",
                    running=True,
                    restarts=0,
                    mem_bytes=int(ratio * 1_000),
                    mem_limit_bytes=1_000,
                )
            ],
        )
    )


def read_event_rows(config: McpRuntimeConfig) -> list[dict[str, Any]]:
    path = ProviderDegradationStore(config.coordination_root).events_path
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class ProviderDegradationClassifierTests(unittest.TestCase):
    def test_hysteresis_requires_sustained_bad_and_sustained_healthy_samples(self) -> None:
        settings = ProviderDegradationSettings(
            memory_degraded_ratio=0.80,
            memory_critical_ratio=0.95,
            degraded_samples=2,
            critical_samples=2,
            healthy_samples=2,
        )
        degraded_row = {
            "schema": "ar-provider-metrics-sample/v1",
            "containers": [
                {
                    "name": "grepai",
                    "provider": "grepai-memory",
                    "instance": "workspace",
                    "mem_bytes": 850,
                    "mem_limit_bytes": 1_000,
                    "restarts": 0,
                }
            ],
        }
        healthy_row = {
            "schema": "ar-provider-metrics-sample/v1",
            "containers": [
                {
                    "name": "grepai",
                    "provider": "grepai-memory",
                    "instance": "workspace",
                    "mem_bytes": 100,
                    "mem_limit_bytes": 1_000,
                    "restarts": 0,
                }
            ],
        }

        state, _ = classify_degradation([degraded_row], "healthy", settings, now=T1)
        self.assertEqual(state, "healthy")

        state, _ = classify_degradation(
            [degraded_row, degraded_row],
            "healthy",
            settings,
            now=T1,
        )
        self.assertEqual(state, "degraded")

        state, _ = classify_degradation(
            [degraded_row, healthy_row],
            "degraded",
            settings,
            now=T1,
        )
        self.assertEqual(state, "degraded")

        state, _ = classify_degradation(
            [healthy_row, healthy_row],
            "degraded",
            settings,
            now=T1,
        )
        self.assertEqual(state, "healthy")

    def test_setup_failure_streak_uses_central_metric_rows(self) -> None:
        settings = ProviderDegradationSettings(
            setup_failure_degraded_streak=2,
            setup_failure_critical_streak=3,
        )
        rows = [
            {"kind": "provider-setup-summary", "provider": "grepai-memory", "ok": False},
            {"kind": "provider-setup-summary", "provider": "grepai-memory", "ok": False},
            {"kind": "provider-setup-summary", "provider": "grepai-memory", "ok": False},
        ]

        state, evidence = classify_degradation(rows, "healthy", settings, now=T1)

        self.assertEqual(state, "critical")
        self.assertEqual(evidence[0].affected, "grepai-memory")
        self.assertIn("setup failure streak", evidence[0].reason)

    def test_index_staleness_rows_drive_degraded_and_critical_states(self) -> None:
        settings = ProviderDegradationSettings(
            degraded_samples=2,
            critical_samples=2,
            watcher_lag_degraded_commits=5,
            watcher_lag_critical_commits=20,
            watcher_lag_degraded_minutes=10,
            watcher_lag_critical_minutes=30,
        )
        commit_lag = {
            "schema": PROVIDER_INDEX_STATE_SCHEMA,
            "provider": "codegraphcontext-code",
            "repoId": "agents-remember",
            "sampledAt": T1,
            "staleIndex": {"served": False, "behindFiles": 6},
        }
        age_lag = {
            "schema": PROVIDER_INDEX_STATE_SCHEMA,
            "provider": "codegraphcontext-code",
            "repoId": "agents-remember",
            "sampledAt": "2026-07-07T11:00:00+00:00",
            "staleIndex": {"served": True, "behindFiles": 0},
        }

        degraded, degraded_evidence = classify_degradation(
            [commit_lag, commit_lag],
            "healthy",
            settings,
            now=T1,
        )
        critical, critical_evidence = classify_degradation(
            [age_lag],
            "degraded",
            settings,
            now=T1,
        )

        self.assertEqual(degraded, "degraded")
        self.assertEqual(degraded_evidence[0].affected, "codegraphcontext-code:agents-remember")
        self.assertIn("watcher lag 6 files", degraded_evidence[0].reason)
        self.assertEqual(critical, "critical")
        self.assertEqual(critical_evidence[0].affected, "codegraphcontext-code:agents-remember")
        self.assertIn("sustained watcher lag", critical_evidence[0].reason)


class ProviderDegradationEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = load_test_config(
            self.root,
            {
                "memoryDegradedRatio": 0.75,
                "memoryCriticalRatio": 0.90,
                "degradedSamples": 2,
                "criticalSamples": 2,
                "healthySamples": 2,
            },
        )

    def test_critical_transition_records_event_inbox_and_failsafe_once(self) -> None:
        catalog = TerminalCatalog(terminal_catalog_path(self.config.coordination_root))
        catalog.upsert(
            TerminalCatalogEntry(
                id="orchestrator-1",
                label="Orchestrator",
                kind="harness",
                harness="claude",
                lifecycle_id="L1",
                cwd=self.root,
                tmux_name="ar-orchestrator-1",
                command=("claude",),
                created_at=T1,
                last_attached_at=T1,
                status="running",
                spawn_role="orchestrator",
            )
        )
        catalog.upsert(
            TerminalCatalogEntry(
                id="manager-1",
                label="Manager",
                kind="harness",
                harness="codex",
                lifecycle_id="L2",
                cwd=self.root,
                tmux_name="ar-manager-1",
                command=("codex",),
                created_at=T1,
                last_attached_at=T1,
                status="running",
                spawn_role="manager",
            )
        )
        record_memory_sample(self.config, name="grepai-1", ratio=0.95)
        record_memory_sample(self.config, name="grepai-2", ratio=0.96)
        stop_calls: list[Path] = []

        def stop_provider_stacks(config: McpRuntimeConfig) -> dict[str, Any]:
            stop_calls.append(config.config_path)
            return {"ok": True, "stopped": ["grepai-memory"]}

        delivery_attempts: list[dict[str, Any]] = []

        def deliver_entry(log: Any, **kwargs: Any) -> Any:
            delivery_attempts.append({"log": log, **kwargs})
            return log.entry

        with patch(
            "agents_remember.providers.degradation.deliver_inbox_entry", side_effect=deliver_entry
        ):
            result = evaluate_provider_degradation(
                self.config,
                stop_provider_stacks=stop_provider_stacks,
            )
            second = evaluate_provider_degradation(
                self.config,
                stop_provider_stacks=stop_provider_stacks,
            )

        self.assertEqual(result["state"], "critical")
        self.assertEqual(second["event"], None)
        self.assertEqual(len(stop_calls), 1)
        event = result["event"]
        assert isinstance(event, dict)
        self.assertEqual(event["to"], "critical")
        self.assertEqual(event["criticalFailsafe"]["action"], "provider_watchers stop")
        event_rows = read_event_rows(self.config)
        self.assertEqual(len(event_rows), 1)
        self.assertEqual(event_rows[0]["id"], event["id"])

        state_payload = json.loads(
            ProviderDegradationStore(self.config.coordination_root).state_path.read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(state_payload["state"], "critical")
        self.assertEqual(state_payload["lastEventId"], event["id"])

        inbox_entries = OperatorInboxStore(observer_root(self.config)).current().values()
        self.assertEqual({entry.messageKind for entry in inbox_entries}, {"degradation-alert"})
        self.assertEqual(
            {entry.agentId for entry in inbox_entries}, {"orchestrator-1", "manager-1"}
        )
        self.assertEqual(len(delivery_attempts), 2)
        self.assertEqual(
            {attempt["log"].entry.agentId for attempt in delivery_attempts},
            {"orchestrator-1", "manager-1"},
        )
        responses = {entry.recipientRole: entry.response for entry in inbox_entries}
        self.assertIn("system-specialist", responses["orchestrator"])
        self.assertIn("no provider kill authority", responses["manager"])

    def test_critical_stop_failure_still_records_event_inbox_and_state(self) -> None:
        record_memory_sample(self.config, name="grepai-1", ratio=0.95)
        record_memory_sample(self.config, name="grepai-2", ratio=0.96)

        def stop_provider_stacks(_: McpRuntimeConfig) -> dict[str, Any]:
            raise RuntimeError("docker stop failed")

        result = evaluate_provider_degradation(
            self.config,
            stop_provider_stacks=stop_provider_stacks,
        )

        self.assertEqual(result["state"], "critical")
        event = result["event"]
        assert isinstance(event, dict)
        failsafe_result = event["criticalFailsafe"]["result"]
        self.assertEqual(failsafe_result["ok"], False)
        self.assertEqual(failsafe_result["errorType"], "RuntimeError")
        self.assertIn("docker stop failed", failsafe_result["error"])
        event_rows = read_event_rows(self.config)
        self.assertEqual(len(event_rows), 1)
        self.assertEqual(event_rows[0]["id"], event["id"])
        state_payload = json.loads(
            ProviderDegradationStore(self.config.coordination_root).state_path.read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(state_payload["state"], "critical")
        inbox_entries = OperatorInboxStore(observer_root(self.config)).current().values()
        self.assertEqual(
            {entry.recipientRole for entry in inbox_entries}, {"orchestrator", "manager"}
        )

    def test_recovery_transition_survives_restart_and_posts_role_addressed_all_clear(self) -> None:
        record_memory_sample(self.config, name="grepai-1", ratio=0.80)
        record_memory_sample(self.config, name="grepai-2", ratio=0.82)
        degraded = evaluate_provider_degradation(
            self.config, stop_provider_stacks=unreachable_stopper
        )
        self.assertEqual(degraded["state"], "degraded")

        record_memory_sample(self.config, name="grepai-3", ratio=0.20)
        record_memory_sample(self.config, name="grepai-4", ratio=0.20)

        recovered = evaluate_provider_degradation(
            self.config, stop_provider_stacks=unreachable_stopper
        )

        self.assertEqual(recovered["state"], "healthy")
        event = recovered["event"]
        assert isinstance(event, dict)
        self.assertEqual(event["from"], "degraded")
        self.assertEqual(event["to"], "healthy")
        event_rows = read_event_rows(self.config)
        self.assertEqual([row["to"] for row in event_rows], ["degraded", "healthy"])
        inbox_entries = list(OperatorInboxStore(observer_root(self.config)).current().values())
        self.assertEqual(
            {entry.recipientRole for entry in inbox_entries}, {"orchestrator", "manager"}
        )
        self.assertEqual({entry.agentId for entry in inbox_entries}, {None})


if __name__ == "__main__":
    unittest.main()
