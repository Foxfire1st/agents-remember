"""Bounded checkpoint and failure evidence for the ambient-role scenario."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass

from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.harness_control_client import read_control_snapshot
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_paste import capture_pane
from codex_driver import codex_log_evidence
from fixture import E2EFixture
from reporting import CheckpointDefinition, CheckpointRecorder
from responses_server import ScriptedResponses

C09 = CheckpointDefinition(
    "L5-C09",
    requirement="L5-R1,L5-R2,L5-R7",
    expected=(
        "ambient and hosted chain use one normally advertised dispatch_agent tool with one "
        "canonical schema digest"
    ),
    owner="Codex Responses tool advertisement",
)

REQUIRED_DISPATCH_ROUTES = frozenset(
    {
        "ambient-launcher",
        "ambient-repeat",
        "architect",
        "orchestrator-initial",
        "manager-a",
        "orchestrator-replace",
    }
)
REQUIRED_NEGATIVE_SENTINELS = frozenset(
    {
        "missing-ambient-description",
        "missing-brief-property",
    }
)
_CODEX_OUTPUT_SEPARATOR = "\nOutput:\n"
_CODEX_WALL_TIME_PREFIX = "Wall time: "
_CODEX_WALL_TIME_SUFFIX = " seconds"


@dataclass(frozen=True)
class DiscoveryEvidence:
    routes: frozenset[str]
    tools: frozenset[str]
    schema_digests: frozenset[str]
    negative_sentinels: frozenset[str]
    successful_routes: frozenset[str]

    @property
    def passed(self) -> bool:
        return all(
            (
                self.routes >= REQUIRED_DISPATCH_ROUTES,
                self.successful_routes >= REQUIRED_DISPATCH_ROUTES,
                len(self.tools) == 1,
                len(self.schema_digests) == 1,
                self.negative_sentinels == REQUIRED_NEGATIVE_SENTINELS,
            )
        )

    def model_dump(self) -> dict[str, list[str]]:
        return {
            "routes": sorted(self.routes),
            "tools": sorted(self.tools),
            "schemaDigests": sorted(self.schema_digests),
            "negativeSentinels": sorted(self.negative_sentinels),
            "successfulDispatchRoutes": sorted(self.successful_routes),
        }


def is_canonical_manager_message(row: OperatorInboxEntry, master: TaskDocumentRef) -> bool:
    return (
        row.taskDocumentRef == master
        and row.recipientRole == "manager"
        and row.agentId is None
        and row.lifecycleId is None
    )


def message_evidence(row: OperatorInboxEntry) -> dict[str, object]:
    return {
        "entryId": row.id,
        "taskDocumentRef": row.taskDocumentRef.model_dump() if row.taskDocumentRef else None,
        "recipientRole": row.recipientRole,
        "agentId": row.agentId,
        "lifecycleId": row.lifecycleId,
        "deliveryState": row.deliveryState,
        "adapterDeliveryState": row.adapterDeliveryState,
        "deliveredToSession": row.deliveredToSession,
    }


def check_discovery(script: ScriptedResponses, recorder: CheckpointRecorder) -> None:
    evidence = _discovery_evidence(script.events)
    recorder.check(
        C09,
        actual=evidence.model_dump(),
        passed=evidence.passed,
    )


def _discovery_evidence(events: list[dict[str, object]]) -> DiscoveryEvidence:
    dispatches = [event for event in events if _is_dispatch_event(event)]
    return DiscoveryEvidence(
        routes=_string_values(dispatches, "route"),
        tools=_string_values(dispatches, "discoveredTool"),
        schema_digests=_string_values(dispatches, "schemaDigest"),
        negative_sentinels=_passed_negative_sentinels(events),
        successful_routes=_successful_dispatch_routes(events),
    )


def _string_values(events: list[dict[str, object]], field: str) -> frozenset[str]:
    return frozenset(value for event in events if isinstance(value := event.get(field), str))


def _passed_negative_sentinels(
    events: list[dict[str, object]],
) -> frozenset[str]:
    passed = [
        event
        for event in events
        if event.get("kind") == "negative-sentinel" and event.get("status") == "passed"
    ]
    return _string_values(passed, "sentinel")


def _successful_dispatch_routes(
    events: list[dict[str, object]],
) -> frozenset[str]:
    successful = [
        event
        for event in events
        if event.get("kind") == "tool-result" and _tool_result_ok(event.get("result"))
    ]
    return _string_values(successful, "route")


def _tool_result_ok(value: object) -> bool:
    return _nested_ok(value) is True


def _nested_ok(value: object) -> bool | None:
    if isinstance(value, dict):
        return _mapping_ok(value)
    if isinstance(value, list):
        return _children_ok(value)
    return _encoded_ok(value) if isinstance(value, str) else None


def _mapping_ok(value: dict[object, object]) -> bool | None:
    direct = value.get("ok")
    return direct if isinstance(direct, bool) else _children_ok(value.values())


def _children_ok(values: Iterable[object]) -> bool | None:
    for child in values:
        nested = _nested_ok(child)
        if nested is not None:
            return nested
    return None


def _encoded_ok(value: str) -> bool | None:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        payload = _codex_output_payload(value)
        if payload is None:
            return None
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if decoded == value:
        return None
    return _nested_ok(decoded)


def _codex_output_payload(value: str) -> str | None:
    header, separator, payload = value.partition(_CODEX_OUTPUT_SEPARATOR)
    if not separator or "\n" in header:
        return None
    if not header.startswith(_CODEX_WALL_TIME_PREFIX) or not header.endswith(
        _CODEX_WALL_TIME_SUFFIX
    ):
        return None
    duration = header[len(_CODEX_WALL_TIME_PREFIX) : -len(_CODEX_WALL_TIME_SUFFIX)]
    try:
        float(duration)
    except ValueError:
        return None
    return payload.strip()


def _is_dispatch_event(event: dict[str, object]) -> bool:
    tool = event.get("discoveredTool")
    return bool(
        event.get("kind") == "tool-call"
        and isinstance(tool, str)
        and (
            tool == "dispatch_agent"
            or tool.endswith("::dispatch_agent")
            or tool.endswith("__dispatch_agent")
        )
    )


def tmux_evidence(entry: TerminalCatalogEntry) -> dict[str, object]:
    dead = subprocess.run(
        ["tmux", "display-message", "-p", "-t", entry.tmux_name, "#{pane_dead}"],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    return {
        "name": entry.tmux_name,
        "sessionExists": TerminalHost().has_session(entry.tmux_name),
        "paneDead": dead.stdout.strip() if dead.returncode == 0 else None,
        "probeStderr": dead.stderr[-2000:],
        "captureTail": capture_pane(entry.tmux_name)[-6000:],
        "sessionEnvironment": _tmux_environment_evidence(entry.tmux_name),
    }


def _tmux_environment_evidence(name: str) -> dict[str, object]:
    evidence: dict[str, object] = {}
    for variable in ("CODEX_HOME", "AR_SPAWN_ROLE", "AR_HOSTED_SESSION_ID"):
        result = subprocess.run(
            ["tmux", "show-environment", "-t", name, variable],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        prefix = f"{variable}="
        evidence[variable] = (
            result.stdout.strip()[len(prefix) :]
            if result.returncode == 0 and result.stdout.strip().startswith(prefix)
            else None
        )
    api_key = subprocess.run(
        ["tmux", "show-environment", "-t", name, "OPENAI_API_KEY"],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    evidence["OPENAI_API_KEY_present"] = (
        api_key.returncode == 0 and api_key.stdout.strip().startswith("OPENAI_API_KEY=")
    )
    return evidence


def failure_evidence(
    *,
    fixture: E2EFixture,
    catalog: TerminalCatalog,
    inbox: OperatorInboxStore,
    script: ScriptedResponses,
) -> dict[str, object]:
    entries = catalog.list(include_terminated=True)
    return {
        "responseEvents": list(script.events),
        "catalog": [entry.to_json() for entry in entries],
        "inbox": [row.model_dump(mode="json") for row in inbox.current().values()],
        "hosted": [
            {
                "id": entry.id,
                "role": entry.seat_role,
                "taskDocumentRef": (
                    entry.task_document_ref.model_dump(mode="json")
                    if entry.task_document_ref is not None
                    else None
                ),
                "tmux": tmux_evidence(entry),
                "control": _control_evidence(entry),
            }
            for entry in entries
        ],
        "codexLogs": codex_log_evidence(fixture.codex_home),
    }


def _control_evidence(entry: TerminalCatalogEntry) -> object:
    try:
        snapshot = read_control_snapshot(entry)
        return {
            "identity": snapshot.identity.to_json(),
            "control": snapshot.control,
            "activity": snapshot.activity,
            "acceptance": snapshot.acceptance,
            "vendorSessionId": snapshot.vendor_session_id,
            "lastEventSequence": snapshot.last_event_sequence,
            "raw": dict(snapshot.raw),
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
