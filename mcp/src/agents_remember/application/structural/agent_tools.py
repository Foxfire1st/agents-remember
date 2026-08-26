"""Agent operations whose addresses are structural and whose runtime ids stay private."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from agents_remember.application.terminal_tools import (
    RetiredSpawnInputs,
    SpawnedBy,
    SpawnOverrides,
    SpawnSeat,
    session_rename_tool,
    session_retire_tool,
    spawn_agent_session_tool,
)
from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.errors import AuthorityError, HarnessControlError
from agents_remember.kernel.authority import require_repo, require_within_coordination
from agents_remember.kernel.primitives.observer_paths import observer_root
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.operator_inbox import AgentRole
from agents_remember.models.structural.agent import (
    DispatchAgentRequest,
    RenameChildRequest,
    RetireChildRequest,
    StructuralMessageRequest,
    StructuralRole,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.observer.events import now_iso
from agents_remember.serving.ambient_seat import (
    AmbientSeatError,
    resolve_ambient_caller,
    resolve_ambient_seat,
)
from agents_remember.serving.dispatch_brief import HostedDelivery
from agents_remember.serving.operator_inbox_posts import (
    OperatorInboxPostContext,
    post_operator_inbox_entry,
)
from agents_remember.serving.retire import SeatClosure, retire_entry
from agents_remember.serving.seat_events import log_retire_event
from agents_remember.serving.structural_seats import StructuralSeatError, StructuralSeatResolver
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from agents_remember.serving.terminal_paste import TerminalPaster
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.tasks.leaf_doc import (
    TerminalLeafResolutionError,
    resolve_terminal_leaf_doc,
)
from agents_remember.worktrees.integration.integration_branch_authority import (
    repository_default_branch,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.startup.start_contract import (
    MasterSeriesContractSpec,
    ensure_master_series_contract,
)
from agents_remember.worktrees.route_review import RouteReviewError, require_current_route_review
from agents_remember.worktrees.scheduling_mode import effective_execution_nature
from agents_remember.worktrees.worktree_contract import ContractError, load_contract


@dataclass(frozen=True)
class StructuralAgentRuntime:
    """Substitutable plane collaborators; never part of the public agent request."""

    host: TerminalHost | None = None
    paster: TerminalPaster | None = None
    spawn_overrides: SpawnOverrides | None = None
    environ: dict[str, str] | None = None


_DEFAULT_AGENT_RUNTIME = StructuralAgentRuntime()


@dataclass(frozen=True)
class StructuralOutcome:
    operation: str
    ok: bool
    status: str
    document: TaskDocumentRef | None
    role: str
    detail: str | None = None
    delivery_state: str | None = None
    adapter_delivery_state: str | None = None


@dataclass(frozen=True)
class StructuralMessageTarget:
    document: TaskDocumentRef
    role: str
    exact_dispatch_target: str | None = None


@dataclass(frozen=True)
class StructuralMessageContext:
    catalog: TerminalCatalog
    sender: TerminalCatalogEntry | None = None
    runtime: StructuralAgentRuntime = _DEFAULT_AGENT_RUNTIME


@dataclass(frozen=True)
class UnbriefedChild:
    caller_id: str | None
    session_id: str
    document: TaskDocumentRef
    role: StructuralRole


def _catalog(config: McpRuntimeConfig) -> TerminalCatalog:
    return TerminalCatalog(terminal_catalog_path(config.coordination_root))


def _target_payload(outcome: StructuralOutcome) -> dict[str, Any]:
    """Return only stable work identity and structural delivery state."""

    payload: dict[str, Any] = {
        "ok": outcome.ok,
        "operation": outcome.operation,
        "status": outcome.status,
        "role": outcome.role,
    }
    if outcome.document is not None:
        payload["taskDocumentRef"] = outcome.document.model_dump()
    if outcome.detail is not None:
        payload["detail"] = outcome.detail
    if outcome.delivery_state is not None:
        payload["deliveryState"] = outcome.delivery_state
    if outcome.adapter_delivery_state is not None:
        payload["adapterDeliveryState"] = outcome.adapter_delivery_state
    return payload


def _structural_context(
    config: McpRuntimeConfig,
) -> tuple[TerminalCatalog, TaskDocumentTopology, StructuralSeatResolver]:
    catalog = _catalog(config)
    topology = TaskDocumentTopology(config.coordination_root)
    return catalog, topology, StructuralSeatResolver(catalog, topology)


def _caller_error(
    operation: str,
    document: TaskDocumentRef | None,
    role: str,
    error: AmbientSeatError | StructuralSeatError,
) -> dict[str, Any]:
    return _target_payload(
        StructuralOutcome(operation, False, error.status, document, role, str(error))
    )


def _level_for_role(role: str) -> str:
    if role in {"worker", "reviewer", "curator"}:
        return "leaf"
    if role == "manager":
        return "master"
    return "portfolio"


def _post_structural_message(
    config: McpRuntimeConfig,
    context: StructuralMessageContext,
    target: StructuralMessageTarget,
    message: InboxMessage,
) -> dict[str, Any]:
    if (
        message.message_kind in {"dispatch-brief", "state-signal"}
        and target.exact_dispatch_target is None
    ):
        raise ValueError(
            f"{message.message_kind} is emitted by the control plane, not an agent message"
        )
    address = InboxAddress(
        task_document_ref=target.document,
        agent_id=target.exact_dispatch_target,
        recipient_role=cast(AgentRole, target.role),
    )
    return post_operator_inbox_entry(
        OperatorInboxPostContext(
            config=config,
            store=OperatorInboxStore(observer_root(config)),
            delivery=HostedDelivery(
                enabled=True,
                catalog=context.catalog,
                host=context.runtime.host,
                paster=context.runtime.paster,
            ),
        ),
        address=address,
        message=message,
        poster=InboxPoster(
            created_by="model",
            created_via="cli",
            sender_agent_id=context.sender.id if context.sender is not None else None,
            sender_role=(
                cast(AgentRole, context.sender.binding_role) if context.sender is not None else None
            ),
        ),
    )


def _retire_unbriefed_child(
    config: McpRuntimeConfig,
    *,
    caller_id: str | None,
    child_id: str,
    host: TerminalHost | None,
) -> str:
    """Roll back a spawn whose exact initial brief never became durable.

    A plane caller retires as the authority-gated actor. An ambient caller has no catalog
    row to act as the retiring seat; the child it just spawned in this same transaction is
    retired as a system closure instead -- the child id is the spawn result, never caller
    input, so an ambient caller cannot retire an arbitrary session.
    """

    if caller_id is None:
        catalog = TerminalCatalog(terminal_catalog_path(config.coordination_root))
        entry = catalog.get(child_id)
        if entry is None:
            return "child retirement also failed"
        if entry.status == "terminated":
            return "child retired"
        retire_host = host if host is not None else TerminalHost()
        try:
            updated = retire_entry(
                catalog,
                retire_host,
                entry,
                SeatClosure(
                    at=now_iso(),
                    reason="initial dispatch brief persistence failed",
                    edge="ambient-dispatch-rollback",
                ),
            )
        except (HarnessControlError, OSError):
            return "child retirement also failed"
        if updated is None:
            return "child retirement also failed"
        log_retire_event(config, updated)
        return "child retired"
    retired = session_retire_tool(
        config,
        actor_session_id=caller_id,
        session_id=child_id,
        reason="initial dispatch brief persistence failed",
        host=host,
    )
    return "child retired" if retired.get("ok") else "child retirement also failed"


def _spawn_dispatch_child(
    config: McpRuntimeConfig,
    request: DispatchAgentRequest,
    runtime: StructuralAgentRuntime,
    *,
    spawned_by: SpawnedBy,
    document: TaskDocumentRef,
) -> dict[str, Any]:
    """Create the exact occupant for one already-authorized structural child seat."""

    return spawn_agent_session_tool(
        config,
        seat=SpawnSeat(
            task_document_ref=document,
            level=_level_for_role(request.role),
            label=request.label,
            env={"AR_SPAWN_ROLE": request.role},
        ),
        retired=RetiredSpawnInputs(),
        spawned_by=spawned_by,
        overrides=runtime.spawn_overrides or SpawnOverrides(host=runtime.host),
    )


def _post_initial_dispatch_brief(
    config: McpRuntimeConfig,
    context: StructuralMessageContext,
    target: StructuralMessageTarget,
    brief: str,
) -> dict[str, Any]:
    """Persist the initial brief pinned to the occupant that was just spawned."""

    return _post_structural_message(
        config,
        context,
        target,
        InboxMessage(
            ask="Begin the delegated task described in this dispatch brief.",
            response=brief,
            message_kind="dispatch-brief",
        ),
    )


def _failed_initial_dispatch(
    config: McpRuntimeConfig,
    runtime: StructuralAgentRuntime,
    child: UnbriefedChild,
    status: str,
    detail: str,
) -> dict[str, Any]:
    """Retire an unbriefed occupant and return the structural refusal."""

    rollback = _retire_unbriefed_child(
        config,
        caller_id=child.caller_id,
        child_id=child.session_id,
        host=runtime.host,
    )
    return _target_payload(
        StructuralOutcome(
            "dispatch_agent",
            False,
            status,
            child.document,
            child.role,
            f"{detail}; {rollback}",
        )
    )


def _resolve_dispatch_caller(
    structural: tuple[TerminalCatalog, TaskDocumentTopology, StructuralSeatResolver],
    runtime: StructuralAgentRuntime,
    request: DispatchAgentRequest,
    resolved_document: ResolvedTaskDocument,
) -> tuple[TerminalCatalogEntry | None, dict[str, Any] | None]:
    """Resolve the dispatching caller and authorize the dispatch, or return the refusal.

    Returns ``(caller, refusal)`` with exactly one side set: ``caller=None`` with no refusal
    means the ambient launcher (no plane identity) -- the role is still validated against
    the document's altitude. Every other outcome is a real plane caller or a refusal; a
    stale, invalid, mismatched, or unbound plane identity refuses exactly as before instead
    of silently downgrading to ambient.
    """
    catalog, topology, resolver = structural
    document = resolved_document.ref
    if resolve_ambient_caller(environ=runtime.environ) is not None:
        try:
            topology.validate_role(document, request.role)
        except TaskDocumentRefError as exc:
            return None, _caller_error(
                "dispatch_agent",
                request.task_document_ref,
                request.role,
                StructuralSeatError(exc.status, str(exc)),
            )
        return None, None
    try:
        caller = resolve_ambient_seat(catalog, environ=runtime.environ)
    except AmbientSeatError as exc:
        return None, _caller_error("dispatch_agent", request.task_document_ref, request.role, exc)
    try:
        resolver.authorize_child(caller, document=document, role=request.role)
    except StructuralSeatError as exc:
        return None, _caller_error("dispatch_agent", request.task_document_ref, request.role, exc)
    return caller, None


def dispatch_agent_tool(
    config: McpRuntimeConfig,
    request: DispatchAgentRequest,
    runtime: StructuralAgentRuntime = _DEFAULT_AGENT_RUNTIME,
) -> dict[str, Any]:
    """Spawn and durably brief one authorized child without exposing its occupant id."""

    structural = _structural_context(config)
    catalog, topology, _ = structural
    try:
        resolved_document = topology.resolve(request.task_document_ref)
        document = resolved_document.ref
    except TaskDocumentRefError as exc:
        return _target_payload(
            StructuralOutcome(
                "dispatch_agent",
                False,
                exc.status,
                request.task_document_ref,
                request.role,
                str(exc),
            )
        )

    caller, refusal = _resolve_dispatch_caller(
        structural,
        runtime,
        request,
        resolved_document,
    )
    if refusal is not None:
        return refusal

    spawned = _admitted_dispatch_spawn(
        config,
        request,
        runtime,
        spawned_by=(
            SpawnedBy(
                session_id=caller.id,
                lifecycle_id=caller.lifecycle_id,
                caller_kind="plane",
            )
            if caller is not None
            else SpawnedBy(caller_kind="ambient")
        ),
        resolved_document=resolved_document,
    )
    if spawned.get("status") != "spawned-unbriefed":
        return _target_payload(
            StructuralOutcome(
                "dispatch_agent",
                False,
                str(spawned.get("status", "spawn-refused")),
                document,
                request.role,
                cast(str | None, spawned.get("detail")),
            )
        )
    return _brief_spawned_child(
        config,
        request,
        StructuralMessageContext(catalog, caller, runtime),
        document=document,
        target_session_id=cast(str, spawned["session"]),
    )


def _brief_spawned_child(
    config: McpRuntimeConfig,
    request: DispatchAgentRequest,
    context: StructuralMessageContext,
    *,
    document: TaskDocumentRef,
    target_session_id: str,
) -> dict[str, Any]:
    child = UnbriefedChild(
        context.sender.id if context.sender is not None else None,
        target_session_id,
        document,
        request.role,
    )
    try:
        posted = _post_initial_dispatch_brief(
            config,
            context,
            StructuralMessageTarget(document, request.role, target_session_id),
            request.brief,
        )
    except (OSError, ValueError):
        return _failed_initial_dispatch(
            config,
            context.runtime,
            child,
            status="dispatch-persistence-refused",
            detail="durable initial brief was refused",
        )
    if posted.get("ok") is not True:
        return _failed_initial_dispatch(
            config,
            context.runtime,
            child,
            status=str(posted.get("status", "dispatch-persistence-refused")),
            detail="durable initial brief was not accepted",
        )
    delivery_state = cast(str | None, posted.get("deliveryState"))
    adapter_state = cast(str | None, posted.get("adapterDeliveryState"))
    delivered = delivery_state == "delivered" and adapter_state in {
        "accepted",
        "queued",
        "completed",
    }
    return _target_payload(
        StructuralOutcome(
            "dispatch_agent",
            True,
            "dispatched" if delivered else "dispatch-queued",
            document,
            request.role,
            cast(str | None, posted.get("deliveryDetail")),
            delivery_state,
            adapter_state,
        )
    )


def _admitted_dispatch_spawn(
    config: McpRuntimeConfig,
    request: DispatchAgentRequest,
    runtime: StructuralAgentRuntime,
    *,
    spawned_by: SpawnedBy,
    resolved_document: ResolvedTaskDocument,
) -> dict[str, Any]:
    if request.role in {"manager", "worker"}:
        refusal = _implementation_series_admission_refusal(
            config,
            resolved_document,
            request.role,
        )
        if refusal is not None:
            return {"status": refusal.status, "detail": refusal.detail}
    refusal = (
        _curator_route_review_refusal(config, resolved_document)
        if request.role == "curator"
        else None
    )
    if refusal is not None:
        return {"status": refusal.status, "detail": refusal.detail}
    return _spawn_dispatch_child(
        config,
        request,
        runtime,
        spawned_by=spawned_by,
        document=resolved_document.ref,
    )


def _implementation_series_admission_refusal(
    config: McpRuntimeConfig,
    resolved: ResolvedTaskDocument,
    role: str,
) -> StructuralOutcome | None:
    """Select an atomic master before its manager or worker can expose work."""

    try:
        topology = TaskDocumentTopology(config.coordination_root)
        master = _dispatch_owning_master(topology, resolved, role)
        parent_ref = topology.parent(master.ref)
        parent = topology.resolve(parent_ref) if parent_ref is not None else None
        nature = effective_execution_nature(
            master.document, parent.document if parent is not None else None
        )
        if nature == "organizational":
            return None
        repo = require_repo(config, master.ref.repository)
        parent_task_name, protected_branch = _series_source_spec(parent, repo.path)
        series = ensure_master_series_contract(
            MasterSeriesContractSpec(
                coordination_root=config.coordination_root,
                repo_name=repo.repo_id,
                code_repo=repo.path,
                memory_root=repo.memory_root,
                task_root=master.path.parent,
                task_name=master.path.parent.name,
                parent_task_name=parent_task_name,
                protected_branch=protected_branch,
            ),
            leaf_admission_operation=("atomic worker dispatch" if role == "worker" else None),
        )
        if isinstance(series, WorktreeCommandResult):
            return StructuralOutcome(
                "dispatch_agent",
                False,
                str(series.payload.get("state", "series-admission-blocked")),
                resolved.ref,
                role,
                json.dumps(series.payload, sort_keys=True, default=str),
            )
    except (AuthorityError, OSError, RuntimeError, TaskDocumentRefError, ValueError) as exc:
        return StructuralOutcome(
            "dispatch_agent",
            False,
            "series-admission-refused",
            resolved.ref,
            role,
            str(exc),
        )
    return None


def _dispatch_owning_master(
    topology: TaskDocumentTopology,
    resolved: ResolvedTaskDocument,
    role: str,
) -> ResolvedTaskDocument:
    altitude = topology.altitude(resolved.ref)
    if role == "manager" and altitude == "master":
        return resolved
    if role == "worker" and altitude == "leaf":
        master_ref = topology.parent(resolved.ref)
        if master_ref is not None:
            return topology.resolve(master_ref)
        raise ValueError("worker dispatch leaf has no canonical owning master")
    expected = "master" if role == "manager" else "leaf"
    raise ValueError(
        f"{role} dispatch does not address its required canonical {expected} task altitude"
    )


def _series_source_spec(
    parent: ResolvedTaskDocument | None,
    repository: Path,
) -> tuple[str, str]:
    if parent is None:
        return "", repository_default_branch(repository)
    if parent.document.integrationBranch:
        return parent.path.parent.name, parent.document.integrationBranch
    raise ValueError(
        f"commanding sprint {parent.ref.path} does not declare integrationBranch; "
        "the orchestrator must preview and apply task_doc(operation='set_field', "
        f"repo_id='{parent.ref.repository}', task_name='{parent.path.parent.name}', "
        "fields={'integrationBranch': '<exact existing super branch>'}) before manager dispatch"
    )


def _curator_route_review_refusal(
    config: McpRuntimeConfig, resolved: ResolvedTaskDocument
) -> StructuralOutcome | None:
    """Admit a curator only after the plane proves review of the current code tree."""
    enclosures = resolved.document.enclosures
    if len(enclosures) != 1:
        return StructuralOutcome(
            "dispatch_agent",
            False,
            "route-review-contract-ambiguous",
            resolved.ref,
            "curator",
            "curator dispatch requires exactly one leaf enclosure contract",
        )
    try:
        contract_path = require_within_coordination(
            config, enclosures[0].enclosurePath, "enclosure_path"
        )
        contract = load_contract(contract_path)
        resolved_leaf = resolve_terminal_leaf_doc(
            contract.task_root,
            contract.leaf_id,
            asserted_path=resolved.path,
        )
        if (
            resolved_leaf is None
            or resolved_leaf[0].resolve() != resolved.path.resolve()
            or enclosures[0].leafId != contract.leaf_id
            or contract.contract_path.resolve() != contract_path.resolve()
        ):
            raise ValueError("curator task document is not bound to its canonical leaf contract")
        require_current_route_review(contract)
    except RouteReviewError as exc:
        return StructuralOutcome(
            "dispatch_agent", False, exc.status, resolved.ref, "curator", str(exc)
        )
    except (
        AuthorityError,
        ContractError,
        OSError,
        TerminalLeafResolutionError,
        ValueError,
    ) as exc:
        return StructuralOutcome(
            "dispatch_agent",
            False,
            "route-review-contract-invalid",
            resolved.ref,
            "curator",
            str(exc),
        )
    return None


def _message_tool(
    operation: str,
    config: McpRuntimeConfig,
    request: StructuralMessageRequest,
    runtime: StructuralAgentRuntime,
) -> dict[str, Any]:
    catalog, topology, resolver = _structural_context(config)
    caller_document = request.task_document_ref
    caller_role = request.role or "parent"
    try:
        caller = resolve_ambient_seat(catalog, environ=runtime.environ)
        if operation == "message_parent":
            target = resolver.parent(caller)
        else:
            assert request.task_document_ref is not None and request.role is not None
            document = topology.resolve(request.task_document_ref).ref
            target = resolver.child(caller, document=document, role=request.role)
        target_document = target.binding_task_document_ref
        assert target_document is not None
        posted = _post_structural_message(
            config,
            StructuralMessageContext(catalog, caller, runtime),
            StructuralMessageTarget(target_document, target.binding_role),
            InboxMessage(
                ask=request.ask,
                response=request.response,
                message_kind=request.message_kind,
                artifact_path=request.artifact_path,
            ),
        )
    except (AmbientSeatError, StructuralSeatError) as exc:
        return _caller_error(operation, caller_document, caller_role, exc)
    except ValueError as exc:
        return _target_payload(
            StructuralOutcome(
                operation,
                False,
                "message-refused",
                caller_document,
                caller_role,
                str(exc),
            )
        )
    delivery_state = cast(str | None, posted.get("deliveryState"))
    adapter_state = cast(str | None, posted.get("adapterDeliveryState"))
    return _target_payload(
        StructuralOutcome(
            operation,
            True,
            "posted",
            target_document,
            target.binding_role,
            cast(str | None, posted.get("deliveryDetail")),
            delivery_state,
            adapter_state,
        )
    )


def message_parent_tool(
    config: McpRuntimeConfig,
    request: StructuralMessageRequest,
    runtime: StructuralAgentRuntime = _DEFAULT_AGENT_RUNTIME,
) -> dict[str, Any]:
    return _message_tool("message_parent", config, request, runtime)


def message_child_tool(
    config: McpRuntimeConfig,
    request: StructuralMessageRequest,
    runtime: StructuralAgentRuntime = _DEFAULT_AGENT_RUNTIME,
) -> dict[str, Any]:
    return _message_tool("message_child", config, request, runtime)


def retire_child_tool(
    config: McpRuntimeConfig,
    request: RetireChildRequest,
    runtime: StructuralAgentRuntime = _DEFAULT_AGENT_RUNTIME,
) -> dict[str, Any]:
    catalog, topology, resolver = _structural_context(config)
    try:
        document = topology.resolve(request.task_document_ref).ref
        caller = resolve_ambient_seat(catalog, environ=runtime.environ)
        target = resolver.child(caller, document=document, role=request.role)
    except (AmbientSeatError, StructuralSeatError) as exc:
        return _caller_error("retire_child", request.task_document_ref, request.role, exc)
    except TaskDocumentRefError as exc:
        return _target_payload(
            StructuralOutcome(
                "retire_child",
                False,
                exc.status,
                request.task_document_ref,
                request.role,
                str(exc),
            )
        )
    retired = session_retire_tool(
        config,
        actor_session_id=caller.id,
        session_id=target.id,
        reason=request.reason,
        host=runtime.host,
    )
    return _target_payload(
        StructuralOutcome(
            "retire_child",
            bool(retired["ok"]),
            str(retired["status"]),
            document,
            request.role,
            cast(str | None, retired.get("detail")),
        )
    )


def rename_child_tool(
    config: McpRuntimeConfig,
    request: RenameChildRequest,
    runtime: StructuralAgentRuntime = _DEFAULT_AGENT_RUNTIME,
) -> dict[str, Any]:
    catalog, topology, resolver = _structural_context(config)
    try:
        document = topology.resolve(request.task_document_ref).ref
        caller = resolve_ambient_seat(catalog, environ=runtime.environ)
        target = resolver.child(caller, document=document, role=request.role)
    except (AmbientSeatError, StructuralSeatError) as exc:
        return _caller_error("rename_child", request.task_document_ref, request.role, exc)
    except TaskDocumentRefError as exc:
        return _target_payload(
            StructuralOutcome(
                "rename_child",
                False,
                exc.status,
                request.task_document_ref,
                request.role,
                str(exc),
            )
        )
    renamed = session_rename_tool(config, session_id=target.id, label=request.label)
    return _target_payload(
        StructuralOutcome(
            "rename_child",
            bool(renamed["ok"]),
            str(renamed["status"]),
            document,
            request.role,
        )
    )


def rename_self_tool(
    config: McpRuntimeConfig,
    *,
    label: str,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    catalog = _catalog(config)
    try:
        caller = resolve_ambient_seat(catalog, environ=environ)
    except AmbientSeatError as exc:
        return _caller_error("rename_self", None, "self", exc)
    document = caller.binding_task_document_ref
    assert document is not None
    renamed = session_rename_tool(config, session_id=caller.id, label=label)
    return _target_payload(
        StructuralOutcome(
            "rename_self",
            bool(renamed["ok"]),
            str(renamed["status"]),
            document,
            caller.binding_role,
        )
    )
