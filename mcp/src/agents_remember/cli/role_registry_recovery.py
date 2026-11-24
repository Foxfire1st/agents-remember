"""Recover the plug-in's registry before the dashboard reconciles host activity."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from pydantic import AwareDatetime, BaseModel, StrictBool, StrictInt, ValidationError

from agents_remember.cli.paseo_bridge import (
    PaseoBridgeFailure,
    bridge_call,
    require_bridge_runtime,
)
from agents_remember.cli.paseo_role_recipients import _line_receipts, is_minted_agent_id
from agents_remember.cli.role_activity import _role_agent_facts, receipt_role_agent
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.models.task_document import RESOLVED_MASTER_ROW_STATUSES
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.host_activity import (
    HOST_ACTIVITY_FILE,
    read_host_activity_publication,
)
from agents_remember.serving._app_lifespan import _preview_suppresses
from agents_remember.serving.paseo.paseo_plugin_files import (
    RoleAgentReconciliation,
    read_role_agents,
    reconcile_role_agents,
    role_agents_path,
)
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.tasks.store import read_task_doc

_LOG = logging.getLogger(__name__)
_COMMAND = "role-registry"


class _HostAgent(BaseModel):
    id: str
    archivedAt: AwareDatetime | None
    role: str | None


class _HostInventory(BaseModel):
    serverId: str
    complete: StrictBool
    agents: list[_HostAgent]


class _ExactInventory(_HostInventory):
    absent: list[str]
    exactReads: StrictInt


@dataclass(frozen=True)
class RegistryRecovery:
    exactReads: int
    unjudgedAgents: int
    durationMs: int
    qualified_server_id: str | None = None


@dataclass
class _ReceiptFacts:
    registrations: dict[str, dict[str, str]]
    in_flight: set[str]
    active: set[str]


def _task_is_active(
    topology: TaskDocumentTopology,
    selection: RoleSelection,
    cache: dict[TaskDocumentRef, bool],
) -> bool:
    ref = (
        selection.task_document_ref
        or selection.master_document_ref
        or selection.sprint_document_ref
    )
    if ref is None:
        return False
    if ref not in cache:
        try:
            doc = read_task_doc(topology.path_for_ref(ref))
        except (OSError, ValueError, TaskDocumentRefError):
            cache[ref] = False
        else:
            cache[ref] = doc.status not in RESOLVED_MASTER_ROW_STATUSES
    return cache[ref]


def _receipt_facts(config: McpRuntimeConfig, known: set[str], find_active: bool) -> _ReceiptFacts:
    facts = _ReceiptFacts({}, set(), set())
    topology = TaskDocumentTopology(config.coordination_root)
    active_tasks: dict[TaskDocumentRef, bool] = {}
    for path, receipt in _line_receipts(config):
        agent_id = receipt.get("agentId")
        if not isinstance(agent_id, str) or not is_minted_agent_id(agent_id):
            continue
        if receipt.get("status") == "starting" or isinstance(receipt.get("replayRequest"), dict):
            facts.in_flight.add(agent_id)
        try:
            identity = receipt_role_agent(receipt)
        except ValidationError:
            continue
        if identity is None:
            continue
        selection, _, report = identity
        registration = _role_agent_facts(selection, report)
        if path.parent.name != "history" or agent_id not in facts.registrations:
            facts.registrations[agent_id] = registration
        if (
            find_active
            and agent_id not in known
            and _task_is_active(topology, selection, active_tasks)
        ):
            facts.active.add(agent_id)
    return facts


def _observed_agents(inventory: _HostInventory) -> dict[str, _HostAgent]:
    """Keep a positive live observation when one paginated read also saw an archive."""
    agents: dict[str, _HostAgent] = {}
    for agent in inventory.agents:
        if agent.id not in agents or agent.archivedAt is None:
            agents[agent.id] = agent
    return agents


def _exact_agents(
    config: McpRuntimeConfig, ids: set[str], server_id: str
) -> _ExactInventory | None:
    if not ids:
        return _ExactInventory(
            serverId=server_id, complete=True, agents=[], absent=[], exactReads=0
        )
    try:
        result = _ExactInventory.model_validate(
            bridge_call(config, _COMMAND, {"agentIds": sorted(ids)})
        )
    except (PaseoBridgeFailure, ValidationError):
        return None
    observed = [agent.id for agent in result.agents] + result.absent
    if (
        result.serverId != server_id
        or len(observed) != len(set(observed))
        or not ids.issuperset(observed)
        or (result.complete and set(observed) != ids)
    ):
        return None
    return result


def recover_role_registry(config: McpRuntimeConfig) -> RegistryRecovery:
    began = monotonic()
    observed_at = datetime.now(UTC)
    home = require_bridge_runtime(config).home
    baseline = read_role_agents(home)
    try:
        history = _HostInventory.model_validate(bridge_call(config, _COMMAND, {}))
    except (PaseoBridgeFailure, ValidationError):
        return RegistryRecovery(0, len(baseline), round((monotonic() - began) * 1000))

    observed = _observed_agents(history)
    missing = {
        agent.id
        for agent in observed.values()
        if agent.archivedAt is None and agent.role is not None and agent.id not in baseline
    }
    known = baseline.keys() | observed.keys()
    facts = _receipt_facts(config, known, history.complete) if missing else None
    exact_ids = {
        agent_id for agent_id in baseline.keys() - observed.keys() if is_minted_agent_id(agent_id)
    }
    if facts is not None:
        exact_ids.update(facts.active)
    exact = _exact_agents(config, exact_ids, history.serverId) if history.complete else None
    if exact is not None and exact.complete:
        observed.update(_observed_agents(exact))
        if facts is None and exact.absent:
            facts = _receipt_facts(config, known, False)

    recovered, absent, archived = _registry_changes(baseline, observed, facts, exact)
    unapplied: set[str] = set()
    if recovered or absent or archived:
        applied = reconcile_role_agents(
            home, RoleAgentReconciliation(baseline, recovered, absent, archived, observed_at)
        )
        if recovered and not applied:
            unapplied = recovered.keys() - read_role_agents(home).keys()
    decided = observed.keys() | absent
    if facts is not None and exact is not None and exact.complete:
        decided |= set(exact.absent) & facts.in_flight
    unknown = (baseline.keys() | exact_ids) - decided
    unknown |= missing - recovered.keys()
    unknown |= unapplied
    qualified_server_id = (
        history.serverId if exact is not None and exact.complete and not unapplied else None
    )
    return RegistryRecovery(
        exact.exactReads if exact is not None else 0,
        len(unknown),
        round((monotonic() - began) * 1000),
        qualified_server_id,
    )


def _registry_changes(
    baseline: dict[str, dict[str, str]],
    observed: dict[str, _HostAgent],
    facts: _ReceiptFacts | None,
    exact: _ExactInventory | None,
) -> tuple[dict[str, dict[str, str]], set[str], dict[str, datetime]]:
    recovered = (
        {
            agent_id: facts.registrations[agent_id]
            for agent_id, agent in observed.items()
            if agent.archivedAt is None
            and agent_id not in baseline
            and agent_id in facts.registrations
        }
        if facts is not None
        else {}
    )
    if exact is None or not exact.complete:
        return recovered, set(), {}
    absent = (set(exact.absent) & baseline.keys()) - (
        facts.in_flight if facts is not None else set()
    )
    archived = {
        agent_id: agent.archivedAt
        for agent_id, agent in observed.items()
        if agent.archivedAt is not None and agent_id in baseline
    }
    return recovered, absent, archived


class RoleRegistryActivityReader:
    """Heal once per dashboard and plug-in boot, using the existing activity reconciliation."""

    def __init__(self) -> None:
        self._binding: tuple[str, str] | None = None

    def __call__(self, config: McpRuntimeConfig) -> dict[str, Any]:
        if _preview_suppresses():
            return bridge_call(config, "host-activity-snapshot", {})
        home = require_bridge_runtime(config).home
        publication = read_host_activity_publication(
            role_agents_path(home).with_name(HOST_ACTIVITY_FILE)
        )
        boot = publication.bootId if publication is not None else None
        binding = self._binding
        report: dict[str, int] | None = None
        if binding is None or binding[0] != boot:
            recovery = recover_role_registry(config)
            server = recovery.qualified_server_id
            report = {
                "exactReads": recovery.exactReads,
                "unjudgedAgents": recovery.unjudgedAgents,
                "durationMs": recovery.durationMs,
            }
            _LOG.info("Paseo role registry recovery %s", report)
            if server is None:
                # A snapshot cannot qualify here, and its write would wake recovery again.
                self._binding = None
                return {"registryRecovery": report}
            binding = (boot, server) if boot is not None else None

        reply = bridge_call(config, "host-activity-snapshot", {})
        snapshot = reply.get("publication")
        reported_boot = snapshot.get("bootId") if isinstance(snapshot, dict) else None
        if binding is not None and binding == (reported_boot, reply.get("serverId")):
            self._binding = binding
        else:
            # This snapshot cannot certify a boot whose registry we have yet to observe.
            self._binding = None
            reply = {key: value for key, value in reply.items() if key != "publication"}
        return reply if report is None else {**reply, "registryRecovery": report}
