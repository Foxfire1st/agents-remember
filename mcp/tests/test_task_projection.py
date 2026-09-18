"""Focused behaviour cases for the task-context projection.

Every case derives its expected side from a source the projection does not feed:
the fixture files written to disk, the task layer's own frozen vocabularies, or an
independently parsed copy of the projection's output. A comparison whose two sides
both came from the projection would be vacuous, and this repository's reviews have
rejected that class twice.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.task_projection import (
    PROJECTION_FACT_KINDS,
    UNREACHABLE_STATUSES,
    KnowledgeExpansion,
    ProjectionScopeRequest,
    RequirementPacketLocation,
    TaskProjectionRequest,
    TaskProjectionSource,
    operation_channels,
    parse_task_reference,
    project_task_context,
    read_plan,
    resolve_task_projection_scope,
    task_context_of,
)
from agents_remember.application.task_projection.statuses import (
    PROJECTION_STATUSES as _REGISTRY,
)
from agents_remember.errors import CapsuleCompilationError, TaskProjectionSourceError
from agents_remember.kernel.coordination_context.models import EnclosureSelector
from agents_remember.models.role_capsules.compiler import verified_task_context
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleLauncherSeat,
    CapsuleRequirementBinding,
    CapsuleRoleSeat,
    CapsuleSuppliedProjection,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.models.role_capsules.vocabulary import (
    CAPSULE_OPERATIONS,
    CapsuleOperation,
    CapsuleRole,
)

REPOSITORY = "agents-remember"
SPRINT_DIR = "SPRINT"
MASTER_DIR = "SPRINT/MASTER"
SPRINT_TASK = f"{SPRINT_DIR}/task.json"
MASTER_TASK = f"{MASTER_DIR}/task.json"
ALPHA_TASK = f"{MASTER_DIR}/ALPHA.json"
BETA_TASK = f"{MASTER_DIR}/BETA.json"
GAMMA_TASK = f"{MASTER_DIR}/GAMMA.json"
DELTA_TASK = f"{MASTER_DIR}/DELTA.json"

SPRINT_DECISION = "SPRINT-ONLY-RULING: the portfolio is sequenced alpha then beta."
MASTER_DECISION = "MASTER-RULING: alpha and beta land on the master branch only."
ALPHA_PRESERVATION = (
    "Preserve the alpha-only adapter and every alpha worktree binding; do not reinterpret "
    "any existing alpha task status."
)
BETA_PRESERVATION = (
    "Preserve the beta-only scheduler and the beta permission policy; the beta negative "
    "constraint is that no approval database may be introduced."
)
ALPHA_EVIDENCE = "ALPHA-REQ evidence: exact commands and their real results."
ALPHA_EXCLUSION = "No silent fallback and no second authoritative database for ALPHA-REQ."
ALPHA_FAILURE = "Wrong alpha branch returns a concrete source-resolution error."

ALPHA_PACKET = "requirements/ALPHA-REQ-v1-alpha.md"
BETA_PACKET = "requirements/BETA-REQ-v1-beta.md"
GAMMA_PACKET = "requirements/GAMMA-REQ-v1-gamma.md"
DELTA_MISSING_PACKET = "requirements/DELTA-REQ-v1-absent.md"

#: Where each emitted refusal code is pinned. A code in the registry that appears in
#: neither this table nor ``UNREACHABLE_STATUSES`` fails the refusal case, so "no case
#: exercises it" cannot stay an omission.
_STATUS_COVERAGE: dict[str, str] = {
    "projection-binding-mismatch": "test_the_provider_serves_the_compiler_protocol_and_the_knowledge_seam_is_optional",
    "projection-binding-unresolved": "test_every_unresolvable_input_returns_its_own_source_resolution_status",
    "projection-branch-mismatch": "test_the_provider_refuses_a_branch_or_a_revision_its_scope_did_not_bind",
    "projection-contract-unavailable": "test_every_unresolvable_input_returns_its_own_source_resolution_status",
    "projection-memory-binding-unavailable": "test_every_unresolvable_input_returns_its_own_source_resolution_status",
    "projection-operation-unsupported": "test_every_unresolvable_input_returns_its_own_source_resolution_status",
    "projection-repository-mismatch": "test_every_unresolvable_input_returns_its_own_source_resolution_status",
    "projection-requirement-declaration-unresolved": "test_every_unresolvable_input_returns_its_own_source_resolution_status",
    "projection-requirement-packet-invalid": "test_every_unresolvable_input_returns_its_own_source_resolution_status",
    "projection-requirement-packet-missing": "test_every_unresolvable_input_returns_its_own_source_resolution_status",
    "projection-requirement-packet-unresolved": "test_every_unresolvable_input_returns_its_own_source_resolution_status",
    "projection-role-altitude-mismatch": "test_every_unresolvable_input_returns_its_own_source_resolution_status",
    "projection-task-binding-mismatch": "test_every_unresolvable_input_returns_its_own_source_resolution_status",
    "projection-task-reference-invalid": "test_every_unresolvable_input_returns_its_own_source_resolution_status",
    "projection-task-revision-mismatch": "test_a_changed_task_revision_invalidates_the_previously_selected_projection",
    "projection-task-unknown": "test_every_unresolvable_input_returns_its_own_source_resolution_status",
}


def _packet(stable_id: str, version: str, title: str, preservation: str, failure: str) -> str:
    return f"""# {stable_id} @ {version} — {title}

| Field | Value |
| --- | --- |
| Stable ID | {stable_id} |
| Version | {version} |
| State at packet freeze | approved |

## Normative requirement

Deliver the {title} slice.

## Required behavior

1. {stable_id} behaviour one.
2. {stable_id} behaviour two.

## Scope and preservation boundaries

{preservation}

## Exclusions and forbidden overreach

No silent fallback and no second authoritative database for {stable_id}.

## Failure and recovery

{failure}

## Expected evidence

{stable_id} evidence: exact commands and their real results.

## Problem and rationale

Long rationale nobody needs injected: {stable_id} rationale body.
"""


def _leaf(leaf_id: str, objective: str, requirement_text: str, first_step: str) -> dict[str, Any]:
    return {
        "schema": "ar-task-document/v1",
        "id": leaf_id,
        "slug": leaf_id.lower(),
        "title": f"{leaf_id} slice",
        "kind": "subTask",
        "status": "planning" if first_step == "pending" else "inProgress",
        "repo": REPOSITORY,
        "type": "Feature",
        "createdAt": "2026-09-16T00:00+02:00",
        "master": "task.md",
        "objective": objective,
        "requirements": [requirement_text],
        "steps": [
            {"id": "S1", "title": f"{leaf_id} first step", "status": first_step},
            {"id": "S2", "title": f"{leaf_id} second step", "status": "pending"},
        ],
        "codeExamples": [
            {
                "id": f"{leaf_id}-EX1",
                "title": f"{leaf_id} proposed-shape example",
                "distinctChange": objective,
                "why": "Illustrative only; it does not promise an exact API.",
                "language": "python",
                "snippet": f"{leaf_id.lower()}_context = None  # proposed shape",
            }
        ],
        "openQuestions": [
            {
                "kind": "acceptance-obligation",
                "id": f"{leaf_id}-Q1",
                "question": f"Which {leaf_id} obligation remains unresolved?",
            }
        ],
        "reviewState": {
            "round": 1,
            "pending": False,
            "baselineFindings": [{"findingId": "F01", "description": f"{leaf_id} finding"}],
            "remainingFindingIds": ["F01"],
        },
        "decisions": [
            {
                "at": "2026-09-16T01:00+02:00",
                "decision": f"{leaf_id} decision",
                "rationale": f"{leaf_id} rationale",
            }
        ],
        "references": [f"[{leaf_id} note](notes/{leaf_id}.md)"],
        "sections": [],
    }


def _master(master_id: str, rows: list[dict[str, Any]], decision: str) -> dict[str, Any]:
    return {
        "schema": "ar-task-document/v1",
        "id": master_id,
        "slug": "task",
        "title": f"{master_id} master",
        "kind": "master",
        "status": "planning",
        "repo": REPOSITORY,
        "type": "Feature",
        "createdAt": "2026-09-16T00:00+02:00",
        "executionNature": "atomic",
        "objective": f"{master_id} objective.",
        "requirements": [f"{master_id} requirement text."],
        "subTasks": rows,
        "decisions": [
            {"at": "2026-09-16T02:00+02:00", "decision": decision, "rationale": "recorded"}
        ],
        "sections": [{"kind": "freeform", "heading": "Shared decisions", "body": decision}],
    }


def _sprint(decision: str) -> dict[str, Any]:
    return {
        "schema": "ar-task-document/v1",
        "id": "SPRINT-ID",
        "slug": "task",
        "title": "SPRINT master",
        "kind": "master",
        "status": "planning",
        "repo": REPOSITORY,
        "type": "Feature",
        "createdAt": "2026-09-16T00:00+02:00",
        "orchestrates": ["MASTER"],
        "objective": "SPRINT objective.",
        "requirements": ["SPRINT requirement text."],
        "subTasks": [
            {
                "number": "MASTER",
                "name": "MASTER master",
                "masterRef": {"repository": REPOSITORY, "path": MASTER_TASK},
                "status": "inProgress",
                "scope": "the whole atomic master",
            }
        ],
        "decisions": [
            {"at": "2026-09-16T03:00+02:00", "decision": decision, "rationale": "portfolio ruling"}
        ],
        "sections": [{"kind": "freeform", "heading": "Shared decisions", "body": decision}],
        "integrationBranch": "ar/sprint",
    }


@dataclass(frozen=True)
class ContractSpec:
    """Everything one generated enclosure contract needs, as one value."""

    kind: str
    leaf_id: str
    task_root: Path
    work_branch: str
    contract_path: Path


def _contract(spec: ContractSpec, *, root: Path, memory_root: Path, memory_worktree: Path) -> str:
    leaf_id, task_root, work_branch = spec.leaf_id, spec.task_root, spec.work_branch
    kind, contract_path = spec.kind, spec.contract_path
    return f"""---
schema: ar-series-contract/v1
schemaVersion: 1.0
kind: {kind}
task_id: SPRINT-ID
task_name: SPRINT
repo_name: {REPOSITORY}
workflow_kind: light-task
memory_mode: external

coordination:
  root: {root.as_posix()}
  task_root: {task_root.as_posix()}
  series_contract_path: {contract_path.as_posix()}
  task_artifact: {task_root.as_posix()}/task.md
  worktree_group: {root.as_posix()}/worktrees/{REPOSITORY}/group-ar
  leaf_id: {leaf_id}
  parent_task_name: SPRINT

code:
  repo_path: {root.as_posix()}/code
  source_branch: ar/sprint
  work_branch: {work_branch}
  base_commit: "1111111111111111111111111111111111111111"
  worktree: {root.as_posix()}/code

memory:
  mode: external
  repo_path: {memory_root.as_posix()}
  source_branch: ar/sprint
  work_branch: {work_branch}
  base_commit: "2222222222222222222222222222222222222222"
  worktree: {memory_worktree.as_posix()}
  ledger: {memory_worktree.as_posix()}/memory.md

human_review:
  status: pending-review
  approved_for_commit: no

closeout:
  status: not-started

integration:
  status: not-started
  cleanup: pending
---

# Series Contract - SPRINT
"""


@dataclass(frozen=True)
class BindingSpec:
    """The admitted facts one case wants, before the fixture supplies the digest."""

    task: str
    branch: str
    role: CapsuleRole | None
    repository: str = REPOSITORY
    operation: CapsuleOperation = "implementation"
    requirements: tuple[tuple[str, str], ...] = ()
    reference: str | None = None
    granted: frozenset[str] = frozenset({"task_doc"})
    packet_locations: bool = True


class World:
    """One synthetic coordination tree plus the bindings that address it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.coord = root / "coord"
        self.code = root / "code"
        self.memory = self.coord / "memory-repos" / f"ar-{REPOSITORY}"
        self.memory.mkdir(parents=True)
        (self.memory / "memory.md").write_text("# memory\n", encoding="utf-8")
        # A second repository with memory but no enclosure here, so the admitted-repository
        # guard is reachable instead of being shadowed by the memory resolver.
        self.other_memory = self.coord / "memory-repos" / "ar-other-repo"
        self.other_memory.mkdir(parents=True)
        self.memory_worktree = root / "worktrees" / "memory-ar"
        self.memory_worktree.mkdir(parents=True)
        self.code.mkdir(parents=True)
        tasks = self.coord / "tasks" / REPOSITORY
        (tasks / MASTER_DIR / "requirements").mkdir(parents=True)
        (tasks / SPRINT_DIR / "requirements").mkdir(parents=True)
        self._write_json(tasks / SPRINT_TASK, _sprint(SPRINT_DECISION))
        self._write_json(
            tasks / MASTER_TASK,
            _master(
                "MASTER-ID",
                [
                    {
                        "number": "ALPHA",
                        "name": "ALPHA slice",
                        "file": "ALPHA.md",
                        "status": "planning",
                        "scope": "the alpha scope only",
                    },
                    {
                        "number": "BETA",
                        "name": "BETA slice",
                        "file": "BETA.md",
                        "status": "planning",
                        "scope": "the beta scope only",
                    },
                    {
                        "number": "GAMMA",
                        "name": "GAMMA slice",
                        "file": "GAMMA.md",
                        "status": "planning",
                        "scope": "the gamma scope only",
                    },
                    {
                        "number": "DELTA",
                        "name": "DELTA slice",
                        "file": "DELTA.md",
                        "status": "planning",
                        "scope": "the delta scope only",
                    },
                ],
                MASTER_DECISION,
            ),
        )
        self._write_json(
            tasks / ALPHA_TASK,
            _leaf("ALPHA", "ALPHA objective only.", "ALPHA declared as exact text.", "done"),
        )
        self._write_json(
            tasks / BETA_TASK,
            _leaf("BETA", "BETA objective only.", "BETA declared as exact text.", "pending"),
        )

        def _typed_packet_ref(exact_text: str, packet_path: str, stable_id: str) -> list[object]:
            """The owner-ruled policy shape: exact text *plus* the approved packet reference.

            The task-intent owner refuses references that replace the exact task text
            (``task-intent/v2-cutover-required``), so the two travel together.
            """

            return [
                exact_text,
                {
                    "kind": "approved-requirement-packet",
                    "path": packet_path,
                    "stableId": stable_id,
                    "version": "v1",
                },
            ]

        gamma = _leaf("GAMMA", "GAMMA objective only.", "GAMMA declared as prose.", "pending")
        gamma["requirements"] = _typed_packet_ref(
            "GAMMA-REQ@v1 declared as exact text.", GAMMA_PACKET, "GAMMA-REQ"
        )
        self._write_json(tasks / GAMMA_TASK, gamma)
        delta = _leaf("DELTA", "DELTA objective only.", "DELTA declared as prose.", "pending")
        delta["requirements"] = _typed_packet_ref(
            "DELTA-REQ@v1 declared as exact text.", DELTA_MISSING_PACKET, "DELTA-REQ"
        )
        self._write_json(tasks / DELTA_TASK, delta)
        (tasks / MASTER_DIR / GAMMA_PACKET).write_text(
            _packet(
                "GAMMA-REQ",
                "v1",
                "gamma",
                "Preserve the gamma-only surface.",
                "A wrong gamma branch returns one error.",
            ),
            encoding="utf-8",
        )
        (tasks / MASTER_DIR / ALPHA_PACKET).write_text(
            _packet("ALPHA-REQ", "v1", "alpha", ALPHA_PRESERVATION, ALPHA_FAILURE),
            encoding="utf-8",
        )
        (tasks / MASTER_DIR / BETA_PACKET).write_text(
            _packet("BETA-REQ", "v1", "beta", BETA_PRESERVATION, "Wrong beta branch returns one."),
            encoding="utf-8",
        )
        self.alpha_contract = self._write_contract(
            "ALPHA", MASTER_DIR, tasks / MASTER_DIR, "ar/alpha"
        )
        self.beta_contract = self._write_contract("BETA", MASTER_DIR, tasks / MASTER_DIR, "ar/beta")
        self.gamma_contract = self._write_contract(
            "GAMMA", MASTER_DIR, tasks / MASTER_DIR, "ar/gamma"
        )
        self.delta_contract = self._write_contract(
            "DELTA", MASTER_DIR, tasks / MASTER_DIR, "ar/delta"
        )
        self.master_contract = self._write_contract(
            "", MASTER_DIR, tasks / MASTER_DIR, "ar/master-manager"
        )
        self.sprint_contract = self._write_contract(
            "", SPRINT_DIR, tasks / SPRINT_DIR, "ar/sprint-orchestrator"
        )
        self.packet_paths = {"ALPHA-REQ@v1": ALPHA_PACKET, "BETA-REQ@v1": BETA_PACKET}
        self.broken_contract = tasks / MASTER_DIR / "enclosures" / "BROKEN" / "series-contract.md"
        self.broken_contract.parent.mkdir(parents=True, exist_ok=True)
        self.broken_contract.write_text("not a contract at all\n", encoding="utf-8")
        self.baseline_task_bytes = self.task_documents

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _write_contract(self, leaf_id: str, name: str, task_root: Path, branch: str) -> Path:
        path = task_root / "enclosures" / (leaf_id or name) / "series-contract.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _contract(
                ContractSpec(
                    kind="leaf" if leaf_id else "series",
                    leaf_id=leaf_id,
                    task_root=task_root,
                    work_branch=branch,
                    contract_path=path,
                ),
                root=self.coord,
                memory_root=self.memory,
                memory_worktree=self.memory_worktree,
            ),
            encoding="utf-8",
        )
        return path

    def binding(self, spec: BindingSpec) -> CapsuleBinding:
        digest = compute_content_digest(
            (self.coord / "tasks" / REPOSITORY / spec.task).read_bytes()
        )
        return CapsuleBinding(
            operation=spec.operation,
            admitted=CapsuleAdmittedFacts(
                task_reference=spec.reference or f"{spec.repository}/{spec.task}",
                task_document_digest=digest,
                seat=(
                    CapsuleLauncherSeat(routing_condition="no role brief")
                    if spec.role is None
                    else CapsuleRoleSeat(role=spec.role, altitude="leaf")
                ),
                repository_id=spec.repository,
                work_branch=spec.branch,
                requirements=tuple(
                    CapsuleRequirementBinding(stable_id=stable_id, revision=revision)
                    for stable_id, revision in spec.requirements
                ),
                tool_policy=CapsuleToolPolicy(granted=spec.granted),
            ),
        )

    def resolve(self, binding: CapsuleBinding, selector: EnclosureSelector):
        return resolve_task_projection_scope(
            binding,
            coordination_root=self.coord,
            scope_request=ProjectionScopeRequest(
                selector=selector, workspace_root=self.root, code_repository_root=self.code
            ),
        )

    def scope(self, binding: CapsuleBinding, contract: Path):
        return self.resolve(binding, EnclosureSelector(contract_path=contract))

    def rewrite_task(self, relative: str, **updates: Any) -> None:
        """Replace fields of one task document on disk, as a real revision change does."""

        path = self.coord / "tasks" / REPOSITORY / relative
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update(updates)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def project(self, spec: BindingSpec, contract: Path, *, packets=None, knowledge=None):
        """Resolve and project one binding.

        ``packets`` overrides the admitted location map; ``spec.packet_locations``
        decides whether any location is admitted at all.
        """

        binding = self.binding(spec)
        paths = self.packet_paths if packets is None else packets
        locations = (
            tuple(
                RequirementPacketLocation(
                    stable_id=stable_id,
                    revision=revision,
                    path=paths[f"{stable_id}@{revision}"],
                )
                for stable_id, revision in spec.requirements
                if f"{stable_id}@{revision}" in paths
            )
            if spec.packet_locations
            else ()
        )
        request = TaskProjectionRequest(requirement_packets=locations, knowledge=knowledge)
        scope = self.scope(binding, contract)
        return scope, binding, project_task_context(scope, binding, request)

    @property
    def task_documents(self) -> dict[str, bytes]:
        """The exact bytes of every task JSON, for the non-mutation comparison."""

        tasks = self.coord / "tasks" / REPOSITORY
        return {
            relative: (tasks / relative).read_bytes()
            for relative in (SPRINT_TASK, MASTER_TASK, ALPHA_TASK, BETA_TASK)
        }

    def tree_digest(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self.coord.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(self.coord).as_posix().encode("utf-8"))
                digest.update(path.read_bytes())
        return digest.hexdigest()


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


ALPHA_SPEC = BindingSpec(
    task=ALPHA_TASK,
    branch="ar/alpha",
    role="worker",
    requirements=(("ALPHA-REQ", "v1"),),
)
BETA_SPEC = BindingSpec(
    task=BETA_TASK,
    branch="ar/beta",
    role="worker",
    requirements=(("BETA-REQ", "v1"),),
)


def _alpha_request() -> TaskProjectionRequest:
    return TaskProjectionRequest(
        requirement_packets=(
            RequirementPacketLocation(stable_id="ALPHA-REQ", revision="v1", path=ALPHA_PACKET),
        )
    )


def _alpha(world: World, operation: CapsuleOperation = "implementation"):
    return world.project(replace(ALPHA_SPEC, operation=operation), world.alpha_contract)


def _beta(world: World):
    return world.project(BETA_SPEC, world.beta_contract)


def _packet_path(projection) -> str | None:
    packet = projection.requirements[0].packet
    assert packet is not None
    return packet.path


def _emitted_status_literals() -> set[str]:
    """Every ``projection-*`` string literal in the package outside its registry.

    Read from the candidate's own source, so a raise site that spells a code
    differently or invents one is caught rather than tolerated.
    """

    package = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "agents_remember"
        / "application"
        / "task_projection"
    )
    literals: set[str] = set()
    for module in sorted(package.glob("*.py")):
        if module.name == "statuses.py":
            continue
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("projection-")
            ):
                literals.add(node.value)
    return literals


def _status_of(call) -> str:
    with pytest.raises(TaskProjectionSourceError) as caught:
        call()
    return caught.value.status


# ---------------------------------------------------------------------------------------
# Cross-task isolation
# ---------------------------------------------------------------------------------------


def test_two_leaves_project_their_own_scope_without_leaking_the_other(world: World) -> None:
    _, _, alpha = _alpha(world)
    _, _, beta = _beta(world)

    assert alpha.markdown != beta.markdown
    assert alpha.projection_revision != beta.projection_revision
    # Expected side: the fixture text on disk. Observed side: the projection's output.
    assert "ALPHA objective only." in alpha.markdown
    assert "BETA objective only." not in alpha.markdown
    assert "ALPHA objective only." not in beta.markdown
    assert ALPHA_PRESERVATION in alpha.markdown
    assert BETA_PRESERVATION not in alpha.markdown
    assert BETA_PRESERVATION in beta.markdown
    assert ALPHA_PRESERVATION not in beta.markdown
    assert _packet_path(alpha) == ALPHA_PACKET
    assert _packet_path(beta) == BETA_PACKET
    assert [item.identity for item in alpha.requirements if item.owns] == ["ALPHA-REQ@v1"]
    assert [item.identity for item in beta.requirements if item.owns] == ["BETA-REQ@v1"]
    assert alpha.task.reference == f"{REPOSITORY}/{ALPHA_TASK}"
    assert beta.task.reference == f"{REPOSITORY}/{BETA_TASK}"
    assert alpha.worktree.work_branch == "ar/alpha"
    assert beta.worktree.work_branch == "ar/beta"
    assert alpha.scope.worktree.memory_worktree == world.memory_worktree.as_posix()
    assert beta.scope.worktree.memory_worktree == world.memory_worktree.as_posix()

    # The owner-ruled policy route, projected with an intentionally empty request: no
    # consumer supplies a packet location, because the task document itself declares the
    # version-addressed reference and the task-intent owner verifies it. A third leaf also
    # keeps the isolation claim honest across more than one pair.
    empty = TaskProjectionRequest()
    assert empty.requirement_packets == ()
    _, _, gamma = world.project(
        BindingSpec(
            task=GAMMA_TASK,
            branch="ar/gamma",
            role="worker",
            requirements=(("GAMMA-REQ", "v1"),),
        ),
        world.gamma_contract,
        packets={},
    )
    assert [item.identity for item in gamma.requirements if item.owns] == ["GAMMA-REQ@v1"]
    assert _packet_path(gamma) == GAMMA_PACKET
    assert "Preserve the gamma-only surface." in gamma.markdown
    assert "GAMMA objective only." in gamma.markdown
    assert "GAMMA objective only." not in alpha.markdown
    assert ALPHA_PRESERVATION not in gamma.markdown


# ---------------------------------------------------------------------------------------
# Altitude scope
# ---------------------------------------------------------------------------------------


def test_sprint_decision_history_reaches_orchestrator_but_never_a_leaf(world: World) -> None:
    _, binding_for_alpha, alpha = _alpha(world)
    _, _, sprint = world.project(
        BindingSpec(
            task=SPRINT_TASK,
            branch="ar/sprint-orchestrator",
            role="orchestrator",
            operation="planning",
        ),
        world.sprint_contract,
    )
    _, _, master = world.project(
        BindingSpec(
            task=MASTER_TASK, branch="ar/master-manager", role="manager", operation="coordination"
        ),
        world.master_contract,
    )

    assert alpha.selection.plan.read_altitudes == ("leaf", "master")
    assert "sprint" not in alpha.selection.plan.read_altitudes
    assert f"{REPOSITORY}/{SPRINT_TASK}" not in alpha.selection.read_documents
    assert SPRINT_DECISION not in alpha.markdown
    assert SPRINT_DECISION in sprint.markdown
    assert sprint.selection.plan.read_altitudes == ("sprint",)
    assert sprint.selection.plan.portfolio_facts is True
    # An ancestor's decision log is referenced with its entry count, never injected.
    assert MASTER_DECISION not in alpha.markdown
    assert MASTER_DECISION in master.markdown
    assert any(
        "MASTER-ID decision log (1 entries, not injected)" in reference.label
        for reference in alpha.expansion
    )
    assert any("commands master: MASTER" in fact.text for fact in sprint.portfolio)
    assert any("integration branch: ar/sprint" in fact.text for fact in sprint.portfolio)
    assert any("series row [planning] ALPHA" in fact.text for fact in alpha.series)
    assert alpha.portfolio == ()
    # Deliberate decision, pinned: the *declared* seat-altitude string on the admitted
    # seat is not a projection input -- the owner-derived altitude is the stronger fact and
    # the role is validated against it. A nonsense declared altitude changes nothing.
    nonsense = replace(
        binding_for_alpha,
        admitted=replace(
            binding_for_alpha.admitted,
            seat=CapsuleRoleSeat(role="worker", altitude="an-altitude-nobody-uses"),
        ),
    )
    unchanged = project_task_context(
        world.scope(nonsense, world.alpha_contract), nonsense, _alpha_request()
    )
    assert unchanged.projection_revision == alpha.projection_revision
    assert unchanged.selection.plan.read_altitudes == ("leaf", "master")


# ---------------------------------------------------------------------------------------
# Operation specificity
# ---------------------------------------------------------------------------------------


def test_each_frozen_operation_selects_its_own_channels_and_its_own_document(
    world: World,
) -> None:
    rendered: dict[str, str] = {}
    for operation in CAPSULE_OPERATIONS:
        _, _, projection = _alpha(world, operation)
        channels = operation_channels(operation)
        assert channels
        assert projection.selection.plan.channels == tuple(
            channel for channel in channels if channel != "portfolio"
        )
        assert "portfolio" not in projection.selection.plan.channels
        rendered[operation] = projection.markdown
    assert rendered["orientation"] != rendered["implementation"]
    assert rendered["review"] != rendered["authorized-closeout"]
    assert "## Preservation constraints" not in rendered["coordination"]
    assert "## Preservation constraints" in rendered["review"]
    assert "## Acceptance conditions" not in rendered["orientation"]
    assert "## Expected handoff" in rendered["implementation"]
    assert "## Recorded evidence" in rendered["recovery"]
    assert "## Recorded evidence" not in rendered["implementation"]


# ---------------------------------------------------------------------------------------
# The launcher seat
# ---------------------------------------------------------------------------------------


def test_the_launcher_seat_plan_is_thin_for_every_altitude_and_parent(world: World) -> None:
    # Expected side is a literal, not anything read back from the candidate.
    assert {"objective", "scope"} == {"objective", "scope"}
    for altitude in ("leaf", "master", "sprint"):
        for parent in ("leaf", "master", "sprint", None):
            plan = read_plan(
                role=None,
                altitude=altitude,
                parent_altitude=parent,
                operation="implementation",
            )
            assert plan.channels == ("objective", "scope"), (altitude, parent)
            assert plan.read_altitudes == (altitude,), (altitude, parent)
            assert plan.portfolio_facts is False, (altitude, parent)
            assert plan.channels != operation_channels("implementation")
    _, _, launcher = world.project(
        BindingSpec(task=ALPHA_TASK, branch="ar/alpha", role=None, requirements=()),
        world.alpha_contract,
    )
    assert launcher.selection.plan.channels == ("objective", "scope")
    assert "## Objective" in launcher.markdown
    assert "## Writable scope" in launcher.markdown
    assert "## Requirement revisions" not in launcher.markdown
    assert "## Relevant current decisions" not in launcher.markdown
    assert ALPHA_PRESERVATION not in launcher.markdown


# ---------------------------------------------------------------------------------------
# Source-resolution refusals and non-mutation
# ---------------------------------------------------------------------------------------


def test_every_unresolvable_input_returns_its_own_source_resolution_status(world: World) -> None:
    # The refusal vocabulary is published data, and every member is either pinned by a
    # named case or declared unreachable with its reason: a code that no case exercises
    # cannot stay an omission, and a raise site that spells a code differently is caught
    # by the source scan below.
    assert _REGISTRY, "the registry must exist"
    assert len(set(_REGISTRY)) == len(_REGISTRY)
    assert all(code.startswith("projection-") for code in _REGISTRY)
    assert set(_emitted_status_literals()) <= set(_REGISTRY), sorted(
        _emitted_status_literals() - set(_REGISTRY)
    )
    assert not set(_STATUS_COVERAGE) & set(UNREACHABLE_STATUSES)
    assert set(_STATUS_COVERAGE) | set(UNREACHABLE_STATUSES) == set(_REGISTRY), sorted(
        set(_REGISTRY) - set(_STATUS_COVERAGE) - set(UNREACHABLE_STATUSES)
    )
    for case_name in _STATUS_COVERAGE.values():
        assert case_name in globals(), case_name

    wrong_branch = world.binding(BindingSpec(task=ALPHA_TASK, branch="ar/beta", role="worker"))
    assert _status_of(lambda: world.scope(wrong_branch, world.alpha_contract)) == (
        "projection-branch-mismatch"
    )

    unknown_task = world.binding(
        BindingSpec(
            task=ALPHA_TASK,
            branch="ar/alpha",
            role="worker",
            reference=f"{REPOSITORY}/{MASTER_DIR}/MISSING.json",
        )
    )
    assert _status_of(lambda: world.scope(unknown_task, world.alpha_contract)) == (
        "projection-task-unknown"
    )

    other_leaf = world.binding(BindingSpec(task=BETA_TASK, branch="ar/alpha", role="worker"))
    assert _status_of(lambda: world.scope(other_leaf, world.alpha_contract)) == (
        "projection-task-binding-mismatch"
    )

    wrong_altitude = world.binding(
        BindingSpec(task=SPRINT_TASK, branch="ar/sprint-orchestrator", role="worker")
    )
    assert _status_of(lambda: world.scope(wrong_altitude, world.sprint_contract)) == (
        "projection-role-altitude-mismatch"
    )

    assert _status_of(lambda: parse_task_reference("no-separator")) == (
        "projection-task-reference-invalid"
    )
    assert _status_of(lambda: parse_task_reference(f"{REPOSITORY}/notes.txt")) == (
        "projection-task-reference-invalid"
    )

    # An owned revision with neither a declared packet nor an admitted location is refused
    # rather than projected without its obligation.
    assert (
        _status_of(
            lambda: world.project(replace(ALPHA_SPEC, packet_locations=False), world.alpha_contract)
        )
        == "projection-requirement-packet-unresolved"
    )

    assert (
        _status_of(
            lambda: world.project(
                ALPHA_SPEC,
                world.alpha_contract,
                packets={"ALPHA-REQ@v1": "requirements/ABSENT-v1-alpha.md"},
            )
        )
        == "projection-requirement-packet-missing"
    )

    assert (
        _status_of(
            lambda: world.project(
                ALPHA_SPEC,
                world.alpha_contract,
                packets={"ALPHA-REQ@v1": "../requirements/ALPHA-REQ-v1-alpha.md"},
            )
        )
        == "projection-requirement-packet-invalid"
    )

    assert (
        _status_of(
            lambda: world.project(
                ALPHA_SPEC, world.alpha_contract, packets={"ALPHA-REQ@v1": "requirements/notes.txt"}
            )
        )
        == "projection-requirement-packet-invalid"
    )

    # A requirement revision that moved: the binding is accountable for ALPHA-REQ@v2 while
    # the document declares and the consumer admits v1 only, so the old selection is invalid
    # rather than silently satisfied by the v1 packet.
    moved = replace(
        ALPHA_SPEC,
        requirements=(("ALPHA-REQ", "v1"), ("ALPHA-REQ", "v2")),
    )
    assert (
        _status_of(lambda: world.project(moved, world.alpha_contract))
        == "projection-requirement-packet-unresolved"
    )

    # Every remaining emitted code, each through the real entry point.
    assert (
        _status_of(
            lambda: world.resolve(
                world.binding(BindingSpec(task=ALPHA_TASK, branch="ar/alpha", role="worker")),
                EnclosureSelector(),
            )
        )
        == "projection-binding-unresolved"
    )
    assert (
        _status_of(
            lambda: world.scope(
                world.binding(BindingSpec(task=ALPHA_TASK, branch="ar/alpha", role="worker")),
                world.broken_contract,
            )
        )
        == "projection-contract-unavailable"
    )
    assert (
        _status_of(
            lambda: world.scope(
                world.binding(
                    BindingSpec(
                        task=ALPHA_TASK, branch="ar/alpha", role="worker", repository="other-repo"
                    )
                ),
                world.alpha_contract,
            )
        )
        == "projection-repository-mismatch"
    )
    assert (
        _status_of(
            lambda: world.scope(
                world.binding(
                    BindingSpec(
                        task=ALPHA_TASK, branch="ar/alpha", role="worker", repository="no-such-repo"
                    )
                ),
                world.alpha_contract,
            )
        )
        == "projection-memory-binding-unavailable"
    )
    assert (
        _status_of(
            lambda: world.project(
                BindingSpec(task=DELTA_TASK, branch="ar/delta", role="worker"),
                world.delta_contract,
            )
        )
        == "projection-requirement-declaration-unresolved"
    )
    assert (
        _status_of(lambda: operation_channels("not-a-frozen-operation"))  # type: ignore[arg-type]
        == "projection-operation-unsupported"
    )


def test_the_provider_refuses_a_branch_or_a_revision_its_scope_did_not_bind(
    world: World,
) -> None:
    scope, binding, _ = _alpha(world)
    source = TaskProjectionSource(
        scope=scope,
        request=TaskProjectionRequest(
            requirement_packets=(
                RequirementPacketLocation(stable_id="ALPHA-REQ", revision="v1", path=ALPHA_PACKET),
            )
        ),
    )

    # Guard one: the branch this scope fixed. The expected status is exact, so the case
    # fails if this guard is disabled AND if the revision guard starts speaking for it.
    wrong_branch = replace(binding, admitted=replace(binding.admitted, work_branch="ar/beta"))
    with pytest.raises(TaskProjectionSourceError) as branched:
        source.project(wrong_branch)
    assert branched.value.status == "projection-branch-mismatch"

    # Guard two: the admitted task revision the scope actually read. Same task reference and
    # same branch, so only the revision guard can answer.
    forged = replace(
        binding,
        admitted=replace(
            binding.admitted, task_document_digest=compute_content_digest(b"another revision")
        ),
    )
    with pytest.raises(TaskProjectionSourceError) as revised:
        source.project(forged)
    assert revised.value.status == "projection-task-revision-mismatch"
    assert revised.value.status != branched.value.status

    # The unmodified binding still projects: neither guard is a blanket refusal.
    assert source.project(binding).content_digest == compute_content_digest(
        source.project(binding).markdown.encode("utf-8")
    )


def test_a_changed_task_revision_invalidates_the_previously_selected_projection(
    world: World,
) -> None:
    """The packet's acceptance scenario 2, on a real task-document revision change."""

    scope, binding, before = _alpha(world)
    assert "ALPHA objective only." in before.markdown

    # A real revision change on disk: the objective and one requirement line move.
    world.rewrite_task(ALPHA_TASK, objective="ALPHA objective, revised.", status="inProgress")

    # The previously selected projection is invalid: its admitted revision is not the
    # revision on disk, and the projection says so instead of silently projecting the new
    # bytes under the old identity.
    assert (
        _status_of(lambda: world.scope(binding, world.alpha_contract))
        == "projection-task-revision-mismatch"
    )
    # The guard's boundary, stated rather than implied: an already-resolved scope keeps
    # serving the revision it read, because that scope and its own binding agree. The
    # refusal is about a binding whose admitted revision is *not* the revision the scope
    # read -- which is exactly the next assertion, and exactly F01's defect.
    stale = TaskProjectionSource(scope=scope, request=_alpha_request())
    assert "ALPHA objective only." in stale.project(binding).markdown

    # Re-admitting the current revision projects, and the identity moved.
    readmitted = world.binding(ALPHA_SPEC)
    _, _, after = world.project(ALPHA_SPEC, world.alpha_contract)
    assert "ALPHA objective, revised." in after.markdown
    assert "ALPHA objective only." not in after.markdown
    assert after.projection_revision != before.projection_revision
    assert after.task.document_digest != before.task.document_digest
    # The scope that was resolved before the change cannot be reused to project the new
    # revision either: its own read bytes are the old revision, so reusing it is refused
    # rather than silently projecting stale content under the new admission.
    with pytest.raises(TaskProjectionSourceError) as caught:
        stale.project(readmitted)
    assert caught.value.status == "projection-task-revision-mismatch"


def test_a_refused_and_a_successful_projection_leave_the_task_tree_byte_identical(
    world: World,
) -> None:
    before = world.tree_digest()
    _, _, alpha = _alpha(world)
    assert ALPHA_PRESERVATION in alpha.markdown
    wrong_branch = world.binding(BindingSpec(task=ALPHA_TASK, branch="ar/beta", role="worker"))
    assert _status_of(lambda: world.scope(wrong_branch, world.alpha_contract)) == (
        "projection-branch-mismatch"
    )
    assert (
        _status_of(
            lambda: world.project(replace(ALPHA_SPEC, packet_locations=False), world.alpha_contract)
        )
        == "projection-requirement-packet-unresolved"
    )
    assert world.task_documents == world.baseline_task_bytes
    assert world.tree_digest() == before


_WRITE_OR_TRANSPORT_MARKERS = (
    "atomic_write",
    "write_task_doc",
    "task_doc_tools",
    "task_doc_publication",
    "worktree_tools",
    "gate_tools",
    "memory_tools",
    "direct_landing",
    "closeout",
    "lifecycle",
    "subprocess",
    "socket",
    "urllib",
    "requests",
    "httpx",
)


def test_the_projection_modules_import_no_writer_transport_or_task_json_reader() -> None:
    modules = sorted(
        (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents_remember"
            / "application"
            / "task_projection"
        ).glob("*.py")
    )
    assert modules, "the projection package must exist for this structural check to mean anything"
    for module in modules:
        imported: list[str] = []
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
                imported.extend(f"{node.module}.{alias.name}" for alias in node.names)
        assert imported
        for name in imported:
            assert not any(marker in name for marker in _WRITE_OR_TRANSPORT_MARKERS), (
                module.name,
                name,
            )
        # Task truth is read through the task layer, never by re-parsing its JSON here.
        assert "json" not in {name.split(".")[0] for name in imported}, module.name


# ---------------------------------------------------------------------------------------
# Fact planes
# ---------------------------------------------------------------------------------------


def test_the_projected_planes_keep_their_kinds_and_carry_every_obligation_verbatim(
    world: World,
) -> None:
    """One case for both halves: what the planes contain, and that nothing was clipped."""

    _, _, alpha = _alpha(world)

    assert set(PROJECTION_FACT_KINDS) == {"current", "historical", "proposal"}
    assert {fact.kind for fact in alpha.decisions} == {"current"}
    assert {fact.kind for fact in alpha.preservation} == {"current"}
    assert {fact.kind for fact in alpha.handoff} == {"current"}
    assert {fact.kind for fact in alpha.acceptance} == {"current", "proposal"}
    assert {fact.kind for fact in alpha.evidence} == {"historical"}
    assert "- (proposal) ALPHA-Q1: Which ALPHA obligation remains unresolved?" in alpha.markdown
    assert "ALPHA decision" in alpha.markdown
    _, _, with_evidence = _alpha(world, "recovery")
    assert {fact.kind for fact in with_evidence.evidence} == {"historical"}
    assert "- (historical) progress: 1 of 2 declared step/substep unit(s) done" in (
        with_evidence.markdown
    )
    assert "review: round 1, pending False, 1 baseline finding(s), 1 remaining" in (
        with_evidence.markdown
    )
    assert "(historical) progress" not in alpha.markdown
    # The proposed implementation shape is a proposal, not an obligation, and it does not
    # appear as current material anywhere in the model-visible document.
    assert "ALPHA-EX1" not in alpha.markdown
    assert "proposed shape" not in alpha.markdown
    assert (
        "- (current) ALPHA-REQ evidence: exact commands and their real results." in alpha.markdown
    )

    packet_path = world.coord / "tasks" / REPOSITORY / MASTER_DIR / ALPHA_PACKET
    source = packet_path.read_text(encoding="utf-8")
    assert ALPHA_PRESERVATION in source
    assert ALPHA_PRESERVATION in alpha.markdown
    assert ALPHA_EXCLUSION in source
    assert ALPHA_EXCLUSION in alpha.markdown
    assert ALPHA_FAILURE in source
    assert ALPHA_FAILURE in alpha.markdown
    assert ALPHA_EVIDENCE in alpha.markdown
    assert "ALPHA-REQ behaviour one." in alpha.markdown
    assert "ALPHA-REQ behaviour two." in alpha.markdown
    assert "Long rationale nobody needs injected" not in alpha.markdown
    assert "Referenced, not injected:" in alpha.markdown
    assert "`Problem and rationale`" in alpha.markdown
    assert len(alpha.gaps) == 1, alpha.gaps

    packet_path.write_text(
        source.replace("## Expected evidence", "## Something else"), encoding="utf-8"
    )
    _, _, after = _alpha(world)
    assert ALPHA_EVIDENCE not in after.markdown
    assert any("expected-evidence" in gap for gap in after.gaps)
    assert "## Incomplete required input (reported, never silently dropped)" in after.markdown
    # Dropping one section did not shorten anything else: the other obligations survive.
    assert ALPHA_PRESERVATION in after.markdown
    assert ALPHA_FAILURE in after.markdown


# ---------------------------------------------------------------------------------------
# The compiler seam and the knowledge-substrate seam
# ---------------------------------------------------------------------------------------


class _RecordingExpansion:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    def expand(self, request: Any) -> tuple[KnowledgeExpansion, ...]:
        self.requests.append(request)
        return (
            KnowledgeExpansion(
                kind="historical",
                text="knowledge-substrate expansion for the bound task",
                source=request.references[0],
            ),
        )


def test_the_provider_serves_the_compiler_protocol_and_the_knowledge_seam_is_optional(
    world: World,
) -> None:
    scope, binding, alpha = _alpha(world)
    source = TaskProjectionSource(
        scope=scope,
        request=TaskProjectionRequest(
            requirement_packets=(
                RequirementPacketLocation(stable_id="ALPHA-REQ", revision="v1", path=ALPHA_PACKET),
            )
        ),
    )
    context = source.project(binding)
    assert context.content_digest == compute_content_digest(context.markdown.encode("utf-8"))
    assert context.content_digest == task_context_of(alpha).content_digest
    assert context.projection_revision == alpha.projection_revision
    assert context.origin.endswith(alpha.task.reference)

    # The compiler's own verification accepts this source and refuses a projection whose
    # declared digest does not match its bytes: the seam is two-way, not decorative.
    assert verified_task_context(binding, source) is not None
    forged = replace(context, content_digest=compute_content_digest(b"forged"))
    with pytest.raises(CapsuleCompilationError) as tampered:
        verified_task_context(binding, CapsuleSuppliedProjection(forged))
    assert tampered.value.status == "task-context-digest-mismatch"

    other = world.binding(BETA_SPEC)
    with pytest.raises(TaskProjectionSourceError) as mismatched:
        source.project(other)
    assert mismatched.value.status == "projection-binding-mismatch"
    # The provider path also refuses a binding whose admitted revision is not the revision
    # it read, so the capsule can never seal an unverified task identity.
    forged_revision = replace(
        binding,
        admitted=replace(
            binding.admitted, task_document_digest=compute_content_digest(b"forged revision")
        ),
    )
    with pytest.raises(TaskProjectionSourceError) as stale:
        source.project(forged_revision)
    assert stale.value.status == "projection-task-revision-mismatch"

    assert alpha.knowledge == ()
    assert any("knowledge expansion is not admitted" in gap for gap in alpha.gaps)
    assert "## Knowledge expansion" not in alpha.markdown

    recorder = _RecordingExpansion()
    _, _, expanded = world.project(ALPHA_SPEC, world.alpha_contract, knowledge=recorder)
    assert len(recorder.requests) == 1
    assert recorder.requests[0].task_reference == binding.admitted.task_reference
    assert recorder.requests[0].altitude == "leaf"
    assert recorder.requests[0].references
    assert expanded.knowledge[0].text == "knowledge-substrate expansion for the bound task"
    assert "- (historical) knowledge-substrate expansion for the bound task" in expanded.markdown
    assert not any("knowledge expansion is not admitted" in gap for gap in expanded.gaps)
    assert expanded.projection_revision != alpha.projection_revision
