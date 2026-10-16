"""Load the tree's agents after the host starts, before anyone clicks (MIK-R145).

After its start the host holds no agent loaded: the first request for an agent resumes
it from persistence, about 10 s on a quiet machine. This module loads every agent the
dashboard's tree lists in the background, once per start of the host, so the first
showing of any chat is as fast as with a host that has been running.

The only request that ever reaches the host is the timeline read that loads an agent:
``agent-resume`` refetches one entry of the agent's timeline, which is the runtime's
resume. No message is sent, no turn is interrupted, no archived agent is touched, and
``refresh_agent`` is never called (it unarchives and interrupts a running turn).

The product notices the host's start, not a page load: the serving loop asks the host
for its live listing, and a ``serverId`` never seen in this process begins one run.
The run also starts with no page open. Two servers of the product that notice the same
start race for one record file; the loser finds it and does nothing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from agents_remember.cli.paseo_bridge import (
    PaseoBridgeFailure,
    bridge_call,
    require_bridge_runtime,
)
from agents_remember.cli.paseo_status import HostStartIdentity, LiveAgents, read_live_agents
from agents_remember.cli.paseo_warmup_run import (
    WarmupRun,
    collect_warmup_candidates,
)
from agents_remember.cli.paseo_warmup_state import (
    TRACKER,
    WarmupClaim,
    WarmupTracker,
    claim_warmup_record,
    find_warmup_record,
    read_warmup_record,
)
from agents_remember.cli.role_activity import publish_receipt_role_agent
from agents_remember.cli.role_document_chat_tree import _RequestReads, _selection_records
from agents_remember.cli.role_document_chats import _hosted_agent_id
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import (
    TaskDocumentRefError,
    TaskDocumentTopology,
    repository_master_documents,
)

# How often the watch loop asks the host for its live listing: host starts are rare
# and a pass takes minutes, so one light listing a minute is prompt enough and costs
# the background account one subprocess spawn a minute.
WARMUP_POLL_SECONDS = 60.0


def resume_agent(config: McpRuntimeConfig, agent_id: str, receipt: dict[str, Any]) -> None:
    """Load one agent through the timeline read that is the runtime's resume.

    Exactly one bridge command reaches the host: ``"agent-resume"``, which
    refetches one timeline entry and nothing else. A refusal or a resume the
    runtime declines raises with the host's own words for the run to note.
    """
    publish_receipt_role_agent(config, {**receipt, "agentId": agent_id})
    try:
        reply = bridge_call(config, "agent-resume", {"agentId": agent_id})
    except PaseoBridgeFailure as error:
        raise RuntimeError(f"{error.code}: {error}") from error
    resume = reply.get("resume")
    if not isinstance(resume, dict):
        raise RuntimeError("the host answered the resume without a resume report")
    if reply.get("agent") is None:
        raise RuntimeError("the host has no such agent")
    if resume.get("attempted") and not resume.get("resumed"):
        detail = resume.get("error") or "the host did not resume the agent"
        raise RuntimeError(str(detail)[:800])


def _repository_names(coordination_root: Path) -> list[str]:
    """The task repositories under the coordination root, sorted and bounded."""
    try:
        entries = sorted((coordination_root / "tasks").iterdir())
    except OSError:
        return []
    return [
        entry.name
        for entry in entries
        if entry.is_dir() and not entry.is_symlink() and entry.name != "0_archive"
    ]


def tree_selections(coordination_root: Path) -> list[RoleSelection]:
    """The selections of the documents the dashboard's tree lists, in tree order.

    The dashboard builds the same rows from the same documents
    (``documentBindings``): each sprint, each master a sprint commands, and each
    master's tasks. A document whose commanding line cannot be resolved is not a
    tree document and contributes nothing.
    """
    topology = TaskDocumentTopology(coordination_root)
    selections: list[RoleSelection] = []
    for repository in _repository_names(coordination_root):
        try:
            masters = repository_master_documents(topology, repository)
        except (TaskDocumentRefError, OSError):
            continue
        for master in masters:
            try:
                altitude = topology.altitude(master.ref)
            except (TaskDocumentRefError, OSError):
                continue
            if altitude == "sprint":
                selections.append(RoleSelection(role="orchestrator", sprintDocumentRef=master.ref))
                try:
                    commanded = topology.commanded_masters(master)
                except (TaskDocumentRefError, OSError):
                    continue
                for commanded_master in commanded:
                    selections.extend(_master_rows(topology, commanded_master.ref, master.ref))
    return selections


def _master_rows(
    topology: TaskDocumentTopology, master_ref: TaskDocumentRef, sprint_ref: TaskDocumentRef
) -> list[RoleSelection]:
    """One master's row and its tasks' rows, or nothing when unresolvable."""
    try:
        children = topology.children(master_ref)
    except (TaskDocumentRefError, OSError):
        return []
    rows = [
        RoleSelection(role="manager", sprintDocumentRef=sprint_ref, masterDocumentRef=master_ref)
    ]
    for leaf_ref in children:
        rows.append(
            RoleSelection(
                role="worker",
                sprintDocumentRef=sprint_ref,
                masterDocumentRef=master_ref,
                taskDocumentRef=leaf_ref,
            )
        )
    return rows


def tree_memory_readable(coordination_root: Path) -> bool:
    """Whether the tree's memory can be read at all, distinctly from empty (F27).

    An unreadable task root is unknown memory: the intake must not start, must say so
    distinctly, and must retry next poll. A readable root with no documents is genuinely
    empty and intakes to zero candidates.
    """

    try:
        with os.scandir(coordination_root / "tasks"):
            pass
    except OSError:
        return False
    return True


def tree_agent_ids(
    config: McpRuntimeConfig,
    live: LiveAgents | None = None,
    *,
    receipts: dict[str, dict[str, Any]],
) -> list[str]:
    """The hosted agent ids of the tree's documents, in tree order, each once.

    The same bounded reads the tree route uses: each launch record once within
    the record and request byte limits, and one read of the host's agents. A
    document the reads cannot tell contributes nothing. The caller hands its own
    live listing so one watch step asks the host once.
    """
    selections = tree_selections(config.coordination_root)
    reads = _RequestReads()
    records = [_selection_records(config, selection, reads) for selection in selections]
    live = read_live_agents(config) if live is None else live
    if not live.reachable:
        return []
    agent_ids: list[str] = []
    for found in records:
        if found is None:
            continue
        for record in found:
            agent_id = _hosted_agent_id(record)
            if agent_id is not None and agent_id not in agent_ids and agent_id in live.agent_ids:
                agent_ids.append(agent_id)
                receipts[agent_id] = record
    return agent_ids


def _read_live_or_note_away(tracker: WarmupTracker, config: McpRuntimeConfig) -> LiveAgents | None:
    """Read the host's live agents, noting an away host distinctly (F25)."""
    try:
        live = read_live_agents(config)
    except PaseoBridgeFailure as error:
        tracker.host_detail = f"{error.code}: {error}"
        tracker.note_host_away()
        return None
    if not live.reachable or not live.server_id:
        tracker.host_detail = live.unreachable_reason or "the host is unreachable"
        tracker.note_host_away()
        return None
    tracker.host_detail = None
    tracker.note_host_back()
    return live


def _intake_home_or_note_failure(tracker: WarmupTracker, config: McpRuntimeConfig) -> Path | None:
    """The daemon home when intake may proceed, else the noted failure."""
    try:
        home = require_bridge_runtime(config).home
    except PaseoBridgeFailure as error:
        tracker.note_intake_failure(f"the daemon home cannot be read: {error}")
        return None
    tracker.home = home
    if not tree_memory_readable(config.coordination_root):
        tracker.note_intake_failure(
            "the tree's memory cannot be read: no start is taken while the task root is "
            "unreadable; the next poll retries"
        )
        return None
    return home


def _consume_foreign_record(
    tracker: WarmupTracker,
    home: Path,
    live: LiveAgents,
) -> None:
    """Observe only this exact process start's progress or settled outcome."""
    observed = find_warmup_record(home, live.server_id)
    tracker.observed_path = observed
    tracker.observed_start_identity = live.start_identity
    outcome = read_warmup_record(observed) if observed is not None else None
    if (
        outcome is not None
        and live.start_identity is not None
        and HostStartIdentity.from_record(outcome.get("startIdentity")) == live.start_identity
        and outcome.get("state") in ("finished", "given-up")
    ):
        tracker.last_server_id = live.server_id
        tracker.last_start_identity = live.start_identity
        tracker.last_record = outcome
        tracker.observed_path = None
        tracker.reset_intake()
    else:
        tracker.intake_detail = "another server owns the loading pass; this server starts no load"


def _start_needs_intake(tracker: WarmupTracker, live: LiveAgents) -> bool:
    """Separate unknown identity, active ownership and settled exact starts."""
    if live.start_identity is None:
        tracker.host_detail = (
            live.start_detail or "the host process-start identity cannot be read; retrying"
        )
        tracker.retire_reason = "the host's process-start identity became unreadable"
        return False
    if tracker.run is not None:
        if tracker.claim is not None and (
            tracker.claim.start_identity != live.start_identity
            or tracker.claim.server_id != live.server_id
        ):
            tracker.retire_reason = "a newer process start of the host retired it"
        return False
    return not (
        tracker.last_server_id == live.server_id
        and tracker.last_start_identity == live.start_identity
    )


def check_host_once(
    config: McpRuntimeConfig,
    *,
    tracker: WarmupTracker | None = None,
) -> tuple[WarmupRun, WarmupClaim] | None:
    """Intake one exact OS process start; admit only after older loads drain.

    The existing listing carries pid, process birth tick and OS boot identity.
    Missing identity is reported/retried without consuming any start. The same
    process birth after a transient outage remains the same start. One OS hold
    across product servers bounds total resumes to the pass's two lanes.
    """
    if tracker is None:
        tracker = TRACKER
    live = _read_live_or_note_away(tracker, config)
    if live is None or not _start_needs_intake(tracker, live):
        return None
    assert live.start_identity is not None  # _start_needs_intake established authoritative birth.
    home = _intake_home_or_note_failure(tracker, config)
    if home is None:
        if tracker.intake_given_up():
            tracker.give_up_intake(
                live.server_id,
                live.start_identity,
                tracker.intake_detail or "the intake kept failing",
            )
        return None
    tracker.home = home
    try:
        claim = claim_warmup_record(home, live.server_id, live.start_identity)
    except OSError as error:
        tracker.note_intake_failure(f"the loading claim cannot be published: {error}")
        return None
    if claim is None:
        _consume_foreign_record(tracker, home, live)
        return None
    try:
        receipts: dict[str, dict[str, Any]] = {}
        candidates, skipped = collect_warmup_candidates(
            tree_agent_ids(config, live, receipts=receipts),
            live.agent_ids,
        )
    except (PaseoBridgeFailure, TaskDocumentRefError, OSError) as error:
        try:
            claim.path.unlink(missing_ok=True)
        finally:
            claim.close()
        tracker.note_intake_failure(f"the intake failed and releases its claim: {error}")
        return None
    run = WarmupRun(server_id=live.server_id, agent_ids=candidates)
    run.role_receipts = receipts
    return _admit_run(tracker, live, claim, run, skipped)


def _admit_run(
    tracker: WarmupTracker,
    live: LiveAgents,
    claim: WarmupClaim,
    run: WarmupRun,
    skipped: dict[str, str],
) -> tuple[WarmupRun, WarmupClaim] | None:
    run.skipped.update(skipped)
    run.retain_completed(claim.previous)
    tracker.run = run
    tracker.claim = claim
    tracker.observed_path = None
    tracker.last_server_id = live.server_id
    tracker.reset_intake()
    try:
        tracker.write_progress()
    except OSError as error:
        claim.close()
        tracker.run = None
        tracker.claim = None
        tracker.last_server_id = None
        tracker.note_intake_failure(f"initial progress publication failed: {error}")
        return None
    return run, claim


def register_warmup_route(app: FastAPI) -> None:
    """Serve the one line the dashboard shows while the loading runs (rule 6)."""

    def endpoint() -> JSONResponse:
        return JSONResponse(TRACKER.status())

    app.add_api_route("/api/role-launch/warmup", endpoint, methods=["GET"])


__all__ = [
    "WARMUP_POLL_SECONDS",
    "check_host_once",
    "register_warmup_route",
    "resume_agent",
    "tree_agent_ids",
    "tree_memory_readable",
    "tree_selections",
]
