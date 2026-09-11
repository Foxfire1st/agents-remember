"""Agent-facing gates addressed by task documents; private gate ids stay below this layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from agents_remember.application.gate_tools import gate_decide_tool, raise_lifecycle_gate
from agents_remember.controlplane.records import GateVerdict
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.primitives.observer_paths import observer_root
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.application_requests import LifecycleGateRequest
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.structural.gates import (
    StructuralGateDecisionRequest,
    StructuralLifecycleGateRequest,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.ambient_seat import AmbientSeatError, resolve_ambient_seat
from agents_remember.serving.structural_seats import StructuralSeatError, StructuralSeatResolver
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology


@dataclass(frozen=True)
class StructuralGateRuntime:
    """Plane-injected context that is never accepted from an agent request."""

    environ: dict[str, str] | None = None


_DEFAULT_GATE_RUNTIME = StructuralGateRuntime()


@dataclass(frozen=True)
class DeclaredGateCaller:
    """Duck-typed caller for ambient structural calls with no plane seat.

    Carries only the two binding fields the structural gate tools read; the
    same topology-based authorization validates it exactly like a hosted seat.
    """

    binding_role: str
    binding_task_document_ref: TaskDocumentRef


def _context(
    config: McpRuntimeConfig,
    *,
    environ: dict[str, str] | None,
    declared: DeclaredCaller | None = None,
) -> tuple[TaskDocumentTopology, StructuralSeatResolver, Any]:
    catalog = TerminalCatalog(terminal_catalog_path(config.coordination_root))
    topology = TaskDocumentTopology(config.coordination_root)
    try:
        caller = resolve_ambient_seat(catalog, environ=environ)
    except AmbientSeatError as exc:
        if exc.status != "ambient-seat-unavailable":
            raise
        if declared is None:
            raise AmbientSeatError(
                "structural-caller-required",
                "ambient structural callers must declare caller (role + task_document_ref)",
            ) from exc
        caller = DeclaredGateCaller(
            binding_role=declared.role,
            binding_task_document_ref=declared.task_document_ref,
        )
    _refuse_declared_conflict(caller, declared)
    return topology, StructuralSeatResolver(catalog, topology), caller


def _refuse_declared_conflict(caller: Any, declared: DeclaredCaller | None) -> None:
    """Refuse a request-carried caller that contradicts the hosted seat."""
    if declared is None:
        return
    if (
        declared.role != caller.binding_role
        or declared.task_document_ref != caller.binding_task_document_ref
    ):
        raise AmbientSeatError(
            "structural-caller-conflict",
            "declared caller conflicts with the plane-injected hosted seat",
        )


def _failure(operation: str, status: str, detail: str) -> dict[str, Any]:
    return {"ok": False, "operation": operation, "status": status, "detail": detail}


def _raise_payload(
    raw: dict[str, Any],
    *,
    document: TaskDocumentRef,
    role: str,
) -> dict[str, Any]:
    gate = cast(dict[str, Any], raw["gate"])
    wait = cast(dict[str, Any], raw["wait"])
    payload: dict[str, Any] = {
        "ok": bool(raw["ok"]),
        "operation": "lifecycle_gate",
        "status": "resolved" if gate["state"] != "open" else "raised",
        "taskDocumentRef": document.model_dump(),
        "role": role,
        "kind": gate["kind"],
        "state": gate["state"],
        "waitState": wait["state"],
        "timedOut": bool(wait.get("timedOut", False)),
    }
    if wait.get("note") is not None:
        payload["detail"] = str(wait["note"])
    return payload


def structural_lifecycle_gate_tool(
    config: McpRuntimeConfig,
    request: StructuralLifecycleGateRequest,
    runtime: StructuralGateRuntime = _DEFAULT_GATE_RUNTIME,
) -> dict[str, Any]:
    """Raise one gate on the caller's structural document (seat or declared)."""

    try:
        topology, _resolver, caller = _context(
            config, environ=runtime.environ, declared=getattr(request, "caller", None)
        )
        document = caller.binding_task_document_ref
        assert document is not None
        resolved = topology.resolve(document)
    except (AmbientSeatError, TaskDocumentRefError) as exc:
        return _failure(
            "lifecycle_gate",
            getattr(exc, "status", "structural-gate-refused"),
            str(exc),
        )
    raw = raise_lifecycle_gate(
        config,
        LifecycleGateRequest(
            kind=request.kind,
            ask=request.ask,
            enclosure=resolved.document.id,
            repo_id=document.repository,
            packet=request.packet,
            required_decision=request.required_decision,
            evidence_refs=request.evidence_refs,
            wait=request.wait,
        ),
    )
    return _raise_payload(raw, document=document, role=caller.binding_role)


def _authorize_gate_target(
    resolver: StructuralSeatResolver,
    caller: Any,
    document: TaskDocumentRef,
) -> None:
    role = caller.binding_role
    if role == "orchestrator":
        resolver.authorize_child(caller, document=document, role="manager")
    elif role == "manager":
        resolver.authorize_child(caller, document=document, role="worker")
    elif role == "architect" and document == caller.binding_task_document_ref:
        return
    else:
        raise StructuralSeatError(
            "structural-gate-target-refused",
            f"role {role!r} cannot decide a gate on {document.key}",
        )


def structural_gate_decide_tool(
    config: McpRuntimeConfig,
    request: StructuralGateDecisionRequest,
    runtime: StructuralGateRuntime = _DEFAULT_GATE_RUNTIME,
) -> dict[str, Any]:
    """Decide the one open gate on an authorized child document and kind."""

    try:
        topology, resolver, caller = _context(
            config, environ=runtime.environ, declared=getattr(request, "caller", None)
        )
        resolved = topology.resolve(request.task_document_ref)
        _authorize_gate_target(resolver, caller, resolved.ref)
    except (AmbientSeatError, StructuralSeatError, TaskDocumentRefError) as exc:
        return _failure(
            "gate_decide",
            getattr(exc, "status", "structural-gate-refused"),
            str(exc),
        )
    candidates = [
        gate
        for gate in GateStore(observer_root(config)).all_current().values()
        if gate.state == "open"
        and gate.kind == request.kind
        and gate.enclosure == resolved.document.id
        and gate.repoId in {None, request.task_document_ref.repository}
    ]
    if len(candidates) != 1:
        status = "structural-gate-missing" if not candidates else "structural-gate-ambiguous"
        return _failure(
            "gate_decide",
            status,
            f"{len(candidates)} open {request.kind} gates match {request.task_document_ref.key}",
        )
    gate = candidates[0]
    raw = gate_decide_tool(
        config,
        gate_id=gate.id,
        lifecycle_id=gate.lifecycleId,
        verdict=GateVerdict(
            decision=request.decision,
            via="orchestration",
            note=request.note,
            deciding_role=caller.binding_role,
        ),
        evidence_refs=request.evidence_refs,
    )
    return {
        "ok": bool(raw["ok"]),
        "operation": "gate_decide",
        "status": "decided",
        "taskDocumentRef": request.task_document_ref.model_dump(),
        "role": caller.binding_role,
        "kind": request.kind,
        "state": raw["state"],
        "decidedVia": raw["decidedVia"],
        "decidingRole": raw.get("decidingRole"),
        "evidenceRefs": raw.get("evidenceRefs", []),
        **({"detail": request.note} if request.note is not None else {}),
    }


def structural_gate_list_tool(
    config: McpRuntimeConfig,
    *,
    environ: dict[str, str] | None = None,
    caller: DeclaredCaller | None = None,
) -> dict[str, Any]:
    """List gate state in the caller's document scope without private correlations."""

    try:
        topology, _resolver, seat = _context(config, environ=environ, declared=caller)
        caller_document = seat.binding_task_document_ref
        assert caller_document is not None
        documents = (caller_document, *topology.children(caller_document))
        identities = {
            (document.repository, topology.resolve(document).document.id): document
            for document in documents
        }
    except (AmbientSeatError, TaskDocumentRefError) as exc:
        return _failure(
            "gate_list",
            getattr(exc, "status", "structural-gate-refused"),
            str(exc),
        )
    summaries = []
    for gate in GateStore(observer_root(config)).all_current().values():
        if gate.enclosure is None:
            continue
        document = identities.get((gate.repoId or caller_document.repository, gate.enclosure))
        if document is None:
            continue
        summaries.append(
            {
                "taskDocumentRef": document.model_dump(),
                "kind": gate.kind,
                "state": gate.state,
                "decidingRole": gate.decidingRole,
                "evidenceRefs": [
                    ref.model_dump(mode="json", exclude_none=True) for ref in gate.evidenceRefs
                ],
            }
        )
    return {
        "ok": True,
        "operation": "gate_list",
        "status": "listed",
        "taskDocumentRef": caller_document.model_dump(),
        "role": seat.binding_role,
        "gates": summaries,
    }
