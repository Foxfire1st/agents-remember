"""Focused, branch-correct task and preservation context for one admitted capsule.

This package computes the smallest complete task projection the bound role and
operation need, from authority that already exists: the JSON-primary task
document, its requirement packets, the worktree enclosure contract, and the
admitted capsule binding. It is the ``CapsuleTaskProjectionSource`` half of the
capsule compiler's task-context seam.

**The consumer contract (L4, L5, L7).** One resolution per admitted binding, one
projection per operation::

    scope = resolve_task_projection_scope(
        binding,
        coordination_root=<coordination root>,
        scope_request=ProjectionScopeRequest(
            selector=EnclosureSelector(contract_path=<enclosure contract>),
            code_repository_root=<code checkout>,
        ),
    )
    projection = project_task_context(scope, binding, TaskProjectionRequest(...))
    context = task_context_of(projection)
    outcome = compile_admitted_capsule(binding, admission_request,
                                       projection=CapsuleSuppliedProjection(context))

``TaskProjectionSource(scope, request)`` is the same thing behind the compiler's
one-method protocol. Two inputs are the consumer's to supply, and both are
admissions rather than guesses:

* ``CapsuleAdmittedFacts.task_reference`` must be the task layer's canonical
  reference key, ``"<repository>/<path-under-tasks/<repository>>"``.
* ``TaskProjectionRequest.requirement_packets`` carries a task-root-relative,
  version-addressed packet location **only** for a requirement the bound task
  document declares as exact text instead of as an
  ``approved-requirement-packet`` reference. Declaring the packet on the task
  document is the preferred route and needs no consumer input.

**What this package never does.** It does not mutate task status, raise a gate,
create an approval, write memory, touch Git, or publish a second authoritative
task database. It reads through the existing owners and returns a value. Nothing
here depends on the Knowledge Substrate master: a later knowledge view plugs in
through :class:`TaskKnowledgeExpansionSource`, and with no such source the
projection says so instead of quietly omitting the channel.
"""

from __future__ import annotations

from .declarations import RequirementDeclaration, read_declarations
from .packets import (
    INJECTED_PACKET_ROLES,
    PACKET_SECTION_HEADINGS,
    PacketSectionRole,
    read_packet,
    referenced_headings,
    role_section,
    split_sections,
)
from .projection import project_task_context
from .provider import PROJECTION_ORIGIN, TaskProjectionSource, task_context_of
from .revision import projection_revision
from .scope import (
    ProjectionScopeRequest,
    ResolvedProjectionScope,
    parse_task_reference,
    read_documents,
    referenced_documents,
    require_admitted_revision,
    resolve_task_projection_scope,
)
from .selection import operation_channels, read_plan
from .statuses import PROJECTION_STATUSES, UNREACHABLE_STATUSES
from .types import (
    PROJECTION_FACT_KINDS,
    BoundTask,
    ExpansionReference,
    KnowledgeExpansion,
    KnowledgeExpansionRequest,
    PacketSection,
    ProjectedFact,
    ProjectionChannel,
    ProjectionFactKind,
    ProjectionReadPlan,
    ProjectionSelection,
    RequirementPacketLocation,
    RequirementPacketProjection,
    RequirementProjection,
    TaskKnowledgeExpansionSource,
    TaskProjection,
    TaskProjectionRequest,
    WorktreeBinding,
    WritableScope,
)

__all__ = [
    "INJECTED_PACKET_ROLES",
    "PACKET_SECTION_HEADINGS",
    "PROJECTION_FACT_KINDS",
    "PROJECTION_ORIGIN",
    "PROJECTION_STATUSES",
    "UNREACHABLE_STATUSES",
    "BoundTask",
    "ExpansionReference",
    "KnowledgeExpansion",
    "KnowledgeExpansionRequest",
    "PacketSection",
    "PacketSectionRole",
    "ProjectedFact",
    "ProjectionChannel",
    "ProjectionFactKind",
    "ProjectionReadPlan",
    "ProjectionScopeRequest",
    "ProjectionSelection",
    "RequirementDeclaration",
    "RequirementPacketLocation",
    "RequirementPacketProjection",
    "RequirementProjection",
    "ResolvedProjectionScope",
    "TaskKnowledgeExpansionSource",
    "TaskProjection",
    "TaskProjectionRequest",
    "TaskProjectionSource",
    "WorktreeBinding",
    "WritableScope",
    "operation_channels",
    "parse_task_reference",
    "project_task_context",
    "projection_revision",
    "read_declarations",
    "read_documents",
    "read_packet",
    "read_plan",
    "referenced_documents",
    "referenced_headings",
    "require_admitted_revision",
    "resolve_task_projection_scope",
    "role_section",
    "split_sections",
    "task_context_of",
]
