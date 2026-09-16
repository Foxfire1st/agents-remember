"""Fixture world for the eve capsule/worktree binding: a real coordination tree and real worktrees.

Every side of every L7 comparison has to come from somewhere the code under test does not feed. This
module therefore builds, on disk, the things the production path reads rather than simulates:

* a real git repository with real ``git worktree`` checkouts on real branches, so the admitted
  worktree and the workspace git identity are observed rather than asserted;
* a real coordination root with a master, a leaf task document, an enclosure contract, and an
  external memory repo, so the capsule compiler, the task projection and the resolver all run
  against their real inputs;
* a small canonical composition corpus, authored here, so the instruction blocks the capsule
  carries can be compared against bytes this fixture chose.

It is shared by ``test_eve_capsule_binding.py`` (focused cases) and by the live native fixture, which
launches the real pinned eve runtime against a capsule compiled from this world.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.eve_capsule import (
    CARRIER_DIRECTORY,
    CARRIER_FILENAME,
    EveBindingRequest,
    EveBoundLaunch,
    materialize_eve_binding,
)
from agents_remember.application.skill_resources import CapsuleSourceSelectionRequest
from agents_remember.kernel.coordination_context.models import EnclosureSelector
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.models.eve_capsule_carrier import (
    EVE_CAPSULE_CARRIER_SCHEMA,
    EveCapsuleCarrier,
    EveCapsuleIdentity,
    EveCapsuleWorkspace,
    EveCapsuleWriteScope,
    carrier_digest,
)
from agents_remember.serving.eve_runtime_launch import (
    BINDING_REF_ENV,
    CAPSULE_DIGEST_ENV,
    CAPSULE_PATH_ENV,
    WORKSPACE_ROOT_ENV,
)

REPOSITORY = "agents-remember"
SPRINT = "SPRINT"
MASTER_DIR = f"{SPRINT}/MASTER"
LEAF_ID = "ALPHA"
TASK_PATH = f"{MASTER_DIR}/{LEAF_ID}.json"
WORK_BRANCH = "ar/alpha"
SOURCE_BRANCH = "ar/sprint"

#: The frozen vocabularies, spelled here rather than imported: a fixture that imported what the
#: compiler enforces could not disagree with it, and the compiler's refusal on a partial manifest is
#: itself a behaviour worth keeping honest.
ALL_ROLES = (
    "architect",
    "orchestrator",
    "designer",
    "strategist",
    "manager",
    "worker",
    "curator",
    "reviewer",
    "system-specialist",
)
ALL_OPERATIONS = (
    "orientation",
    "planning",
    "implementation",
    "review",
    "curation",
    "coordination",
    "authorized-closeout",
    "recovery",
)
ROLE_ALTITUDES = {
    "architect": "sprint",
    "orchestrator": "sprint",
    "strategist": "sprint",
    "designer": "master",
    "manager": "master",
    "worker": "leaf",
    "curator": "leaf",
    "reviewer": "leaf",
    "system-specialist": "leaf",
}
OPERATIONS_BY_ROLE = {
    "architect": ("orientation", "planning", "coordination", "review", "recovery"),
    "orchestrator": (
        "orientation",
        "planning",
        "coordination",
        "review",
        "authorized-closeout",
        "recovery",
    ),
    "designer": ("orientation", "planning"),
    "strategist": ("orientation", "planning"),
    "manager": (
        "orientation",
        "coordination",
        "review",
        "curation",
        "authorized-closeout",
        "recovery",
    ),
    "worker": ("orientation", "implementation", "recovery"),
    "curator": ("orientation", "curation", "recovery"),
    "reviewer": ("orientation", "review"),
    "system-specialist": ("orientation", "recovery"),
}
CORE_BLOCKS = ("authority", "invariants")


def run_git(root: Path, *args: str) -> str:
    """One git command in ``root``, with an identity and no operator configuration."""

    completed = subprocess.run(
        [
            "git",
            "-c",
            "user.name=AR fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def repository_with_commit(root: Path, *, branch: str) -> str:
    """A real repository on ``branch`` with one commit; returns the commit id.

    Idempotent: a directory that is already a repository is reused as it stands, so a fixture that is
    re-run against the same report directory keeps one workspace and one commit instead of failing on
    an empty second commit.
    """

    root.mkdir(parents=True, exist_ok=True)
    if (root / ".git").exists():
        return run_git(root, "rev-parse", "HEAD")
    run_git(root, "init", "-b", branch)
    (root / "README.md").write_text("# fixture repository\n", encoding="utf-8")
    run_git(root, "add", "README.md")
    run_git(root, "commit", "-m", "fixture base")
    return run_git(root, "rev-parse", "HEAD")


def add_worktree(repository: Path, destination: Path, *, branch: str) -> str:
    """A linked worktree of ``repository`` checked out on a new ``branch``, reusing an existing one."""

    if (destination / ".git").exists():
        return run_git(destination, "rev-parse", "HEAD")
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_git(repository, "worktree", "add", "-b", branch, str(destination))
    return run_git(destination, "rev-parse", "HEAD")


def composition_corpus(
    root: Path, *, origin: str = "fixture/skills"
) -> CapsuleSourceSelectionRequest:
    """The canonical corpus the compiler routes from: core, one role, one operation.

    Every file is written here, so an applied instruction block can be compared against text this
    fixture chose instead of against the compiler's own copy of it.
    """

    (root / "core").mkdir(parents=True, exist_ok=True)
    (root / "roles").mkdir(parents=True, exist_ok=True)
    (root / "operations").mkdir(parents=True, exist_ok=True)
    for block in CORE_BLOCKS:
        (root / "core" / f"{block}.md").write_text(
            f"# Core — {block}\n\n{block.upper()} RULE authored by the L7 fixture.\n",
            encoding="utf-8",
        )
    for role in ALL_ROLES:
        (root / "roles" / f"{role}.md").write_text(
            f"# Role — {role}\n\n{role.upper()} SEAT RULE authored by the L7 fixture.\n",
            encoding="utf-8",
        )
    for operation in ALL_OPERATIONS:
        (root / "operations" / f"{operation}.md").write_text(
            f"# Operation — {operation}\n\n{operation.upper()} RULE authored by the L7 fixture.\n",
            encoding="utf-8",
        )
    manifest = {
        "schema": "ar-role-capsule-composition/v1",
        "role_order": list(ALL_ROLES),
        "core": {
            block: {"source": f"core/{block}.md", "purpose": f"{block} rules"}
            for block in CORE_BLOCKS
        },
        "operations": {
            operation: {
                "source": f"operations/{operation}.md",
                "purpose": f"{operation} rules",
                "applies_to_roles": [
                    role for role in ALL_ROLES if operation in OPERATIONS_BY_ROLE[role]
                ],
            }
            for operation in ALL_OPERATIONS
        },
        "roles": {
            role: {
                "file": f"roles/{role}.md",
                "altitude": ROLE_ALTITUDES[role],
                "seat": f"the {role} seat",
                "core": list(CORE_BLOCKS),
                "operations": list(OPERATIONS_BY_ROLE[role]),
                "tools": ["read_ar_files"],
                "skills": [],
                "templates": [],
                "criteria": [],
            }
            for role in ALL_ROLES
        },
        "launcher": {
            "is_role": False,
            "routing_condition": "ambient-launcher",
            "instruction_source": "core/authority.md",
            "core": ["authority"],
            "operations": ["orientation"],
            "templates": [],
            "criteria": [],
        },
        "skills": {},
    }
    (root / "composition-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return CapsuleSourceSelectionRequest(root=root, origin=origin)


def _leaf_document(objective: str) -> dict[str, object]:
    return {
        "schema": "ar-task-document/v1",
        "id": LEAF_ID,
        "slug": LEAF_ID.lower(),
        "title": "ALPHA slice",
        "kind": "subTask",
        "status": "inProgress",
        "repo": REPOSITORY,
        "type": "Feature",
        "createdAt": "2026-09-16T00:00+02:00",
        "master": "task.md",
        "objective": objective,
        "requirements": ["ALPHA requirement declared as exact text."],
        "steps": [{"id": "S1", "title": "ALPHA step", "status": "pending"}],
    }


def _master_document() -> dict[str, object]:
    return {
        "schema": "ar-task-document/v1",
        "id": "MASTER-ID",
        "slug": "task",
        "title": "MASTER master",
        "kind": "master",
        "status": "planning",
        "repo": REPOSITORY,
        "type": "Feature",
        "createdAt": "2026-09-16T00:00+02:00",
        "executionNature": "atomic",
        "objective": "MASTER objective.",
        "requirements": ["MASTER requirement."],
        "subTasks": [
            {
                "number": LEAF_ID,
                "name": "ALPHA slice",
                "file": f"{LEAF_ID}.md",
                "status": "inProgress",
                "scope": "the ALPHA scope only",
            }
        ],
        "decisions": [],
        "sections": [],
    }


def _sprint_document() -> dict[str, object]:
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
        "requirements": ["SPRINT requirement."],
        "subTasks": [
            {
                "number": "MASTER",
                "name": "MASTER master",
                "masterRef": {"repository": REPOSITORY, "path": f"{MASTER_DIR}/task.json"},
                "status": "inProgress",
                "scope": "the whole atomic master",
            }
        ],
        "decisions": [],
        "sections": [],
        "integrationBranch": SOURCE_BRANCH,
    }


@dataclass(frozen=True)
class ContractAddresses:
    """The five real paths one enclosure contract has to name, each observed on disk."""

    coordination_root: Path
    task_root: Path
    code_worktree: Path
    memory_worktree: Path
    contract_path: Path


def _contract_text(
    addresses: ContractAddresses, *, branch: str, base_commit: str, memory_commit: str
) -> str:
    coordination_root = addresses.coordination_root
    task_root = addresses.task_root
    code_worktree = addresses.code_worktree
    memory_worktree = addresses.memory_worktree
    contract_path = addresses.contract_path
    return f"""---
schema: ar-series-contract/v1
schemaVersion: 1.0
kind: leaf
task_id: SPRINT-ID
task_name: SPRINT
repo_name: {REPOSITORY}
workflow_kind: light-task
memory_mode: external

coordination:
  root: {coordination_root.as_posix()}
  task_root: {task_root.as_posix()}
  series_contract_path: {contract_path.as_posix()}
  task_artifact: {task_root.as_posix()}/task.md
  worktree_group: {(coordination_root / "worktrees" / "group-ar").as_posix()}
  leaf_id: {LEAF_ID}
  parent_task_name: SPRINT

code:
  repo_path: {code_worktree.as_posix()}
  source_branch: {SOURCE_BRANCH}
  work_branch: {branch}
  base_commit: {base_commit}
  worktree: {code_worktree.as_posix()}

memory:
  mode: external
  repo_path: {(coordination_root / "memory-repos" / f"ar-{REPOSITORY}").as_posix()}
  source_branch: {SOURCE_BRANCH}
  work_branch: {branch}
  base_commit: {memory_commit}
  worktree: {memory_worktree.as_posix()}
  ledger: {(memory_worktree / "memory.md").as_posix()}

human_review:
  status: pending-review
  approved_for_commit: no

closeout:
  status: not-started

integration:
  status: not-started
  cleanup: pending
---

# Series Contract - ALPHA
"""


@dataclass(frozen=True)
class FixtureWorld:
    """One synthetic AR world with real git worktrees behind the admitted paths."""

    root: Path
    coordination_root: Path
    repository: Path
    code_worktree: Path
    memory_worktree: Path
    contract: Path
    corpus: Path
    config: McpRuntimeConfig
    work_branch: str
    base_commit: str
    sources: CapsuleSourceSelectionRequest

    @property
    def task_root(self) -> Path:
        return self.coordination_root / "tasks" / REPOSITORY / MASTER_DIR

    @property
    def report_root(self) -> Path:
        root = self.coordination_root / "tasks" / REPOSITORY / SPRINT / "notes" / "reports"
        root.mkdir(parents=True, exist_ok=True)
        return root

    @property
    def sibling_worktree(self) -> Path:
        """A second admitted-looking worktree of the same repository, on another branch."""

        path = self.root / "worktrees" / "group-ar" / "BETA" / REPOSITORY
        if not (path / ".git").exists():
            add_worktree(self.repository, path, branch="ar/beta")
        return path

    def bind(
        self,
        *,
        role: str = "worker",
        operation: str = "implementation",
        carrier_directory: Path | None = None,
        report_root: Path | None = None,
        memory_surface: Path | None = None,
    ) -> EveBoundLaunch:
        """Compile the admitted capsule and write its carrier, through the production path."""

        return materialize_eve_binding(
            self.config,
            EveBindingRequest(
                enclosure=EnclosureSelector(contract_path=self.contract),
                task_path=TASK_PATH,
                role=role,
                operation=operation,
                carrier_directory=carrier_directory or (self.root / "epoch"),
                code_repository_root=self.repository,
                report_root=self.report_root if report_root is None else report_root,
                memory_root=memory_surface,
                sources=self.sources,
            ),
        )


def build_world(
    root: Path,
    *,
    code_workspace: Path | None = None,
    code_branch: str | None = None,
    base_commit: str | None = None,
) -> FixtureWorld:
    """Build the whole fixture world under ``root``.

    ``code_workspace`` admits a worktree this fixture did not create — the live native fixture's own
    temporary checkout — so the capsule a case compiles there binds the workspace the runtime will
    actually execute in rather than a second one the assertion would then have to reconcile.
    """

    coordination_root = root / "ar-coordination"
    repository = root / REPOSITORY
    base_commit = repository_with_commit(repository, branch=SOURCE_BRANCH)
    worktree_group = coordination_root / "worktrees" / "group-ar"
    code_worktree = worktree_group / LEAF_ID / REPOSITORY
    branch = WORK_BRANCH
    if code_workspace is None:
        add_worktree(repository, code_worktree, branch=WORK_BRANCH)
    else:
        code_worktree = code_workspace.resolve()
        branch = code_branch or WORK_BRANCH
    memory_repository = root / "memory-repository"
    repository_with_commit(memory_repository, branch=SOURCE_BRANCH)
    memory_worktree = coordination_root / "memory-repos" / f"ar-{REPOSITORY}"
    add_worktree(memory_repository, memory_worktree, branch=WORK_BRANCH)
    (memory_worktree / "memory.md").write_text("# fixture ledger\n", encoding="utf-8")

    task_root = coordination_root / "tasks" / REPOSITORY / MASTER_DIR
    task_root.mkdir(parents=True, exist_ok=True)
    (coordination_root / "settings.json").write_text("{}\n", encoding="utf-8")
    sprint_root = coordination_root / "tasks" / REPOSITORY / SPRINT
    sprint_root.mkdir(parents=True, exist_ok=True)
    for path, payload in (
        (sprint_root / "task.json", _sprint_document()),
        (task_root / "task.json", _master_document()),
        (task_root / f"{LEAF_ID}.json", _leaf_document("ALPHA objective.")),
    ):
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    contract = task_root / "enclosures" / LEAF_ID / "series-contract.md"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(
        _contract_text(
            ContractAddresses(
                coordination_root=coordination_root,
                task_root=task_root,
                code_worktree=code_worktree,
                memory_worktree=memory_worktree,
                contract_path=contract,
            ),
            branch=branch,
            base_commit=base_commit or run_git(code_worktree, "rev-parse", "HEAD"),
            memory_commit=run_git(memory_worktree, "rev-parse", "HEAD"),
        ),
        encoding="utf-8",
    )

    corpus = root / "corpus"
    sources = composition_corpus(corpus)
    config = McpRuntimeConfig(
        config_path=coordination_root / "settings.json",
        coordination_root=coordination_root,
        workspace_root=root,
        transcript_root=root / "transcripts",
        repositories={
            REPOSITORY: RepositoryScope(
                repo_id=REPOSITORY,
                path=repository,
                memory_root=coordination_root / "memory-repos" / f"ar-{REPOSITORY}",
            )
        },
    )
    return FixtureWorld(
        root=root,
        coordination_root=coordination_root,
        repository=repository,
        code_worktree=code_worktree,
        memory_worktree=memory_worktree,
        contract=contract,
        corpus=corpus,
        config=config,
        work_branch=branch,
        base_commit=base_commit
        if code_workspace is not None
        else run_git(code_worktree, "rev-parse", "HEAD"),
        sources=sources,
    )


@dataclass(frozen=True)
class FixtureCarrierRequest:
    """What one fixture carrier has to name: the workspace it belongs to and the seat it is for."""

    workspace: Path
    carrier_directory: Path
    branch: str
    base_commit: str
    instructions: Sequence[str] = ("FIXTURE CAPSULE INSTRUCTION authored by the L7 fixture.\n",)
    task_context: str = ""
    role: str = "worker"
    operation: str = "implementation"
    binding_ref: str | None = None


def fixture_carrier_for(request: FixtureCarrierRequest) -> tuple[Path, str]:
    """A carrier for a workspace this fixture owns, written through the production carrier model.

    The live native fixture launches a runtime in a temporary workspace of its own: its capsule does
    not need compiled AR content to exercise native protocol behaviour, but it does need to be a real
    carrier — real bytes, a real digest, a real workspace scope — because the runtime verifies it
    before it will execute.
    """

    workspace = request.workspace
    carrier_directory = request.carrier_directory
    instructions = request.instructions
    task_context = request.task_context
    role = request.role
    operation = request.operation
    binding_ref = request.binding_ref or f"ar-binding:{role}:live-fixture:{operation}"
    scopes = [EveCapsuleWriteScope(kind="workspace", path=".", root=str(workspace.resolve()))]
    carrier = EveCapsuleCarrier(
        schema=EVE_CAPSULE_CARRIER_SCHEMA,
        identity=EveCapsuleIdentity(
            role=role,
            task_reference="agents-remember/live-fixture/task.json",
            operation=operation,
            binding_ref=binding_ref,
            semantic_digest="sha256:" + "0" * 64,
        ),
        workspace=EveCapsuleWorkspace(
            root=str(workspace.resolve()),
            repository_id=REPOSITORY,
            work_branch=request.branch,
            base_commit=request.base_commit,
            contract_path=str((carrier_directory / "series-contract.md").resolve()),
        ),
        instructions=tuple(instructions),
        instruction_identities=tuple(
            f"fixture:block-{index}" for index in range(len(instructions))
        ),
        instruction_digests=tuple(
            f"sha256:{hashlib.sha256(item.encode()).hexdigest()}" for item in instructions
        ),
        task_context_markdown=task_context,
        task_context_digest=(
            ""
            if not task_context
            else f"sha256:{hashlib.sha256(task_context.encode()).hexdigest()}"
        ),
        write_scopes=tuple(scopes),
        granted_tools=("ar_workspace_read", "ar_workspace_write"),
        carry_forward=(),
    )
    path = carrier_directory / CARRIER_DIRECTORY / CARRIER_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = carrier.to_bytes()
    path.write_bytes(payload)
    return path, carrier_digest(payload)


def binding_env(
    *, carrier_path: Path, digest: str, workspace_root: Path, binding_ref: str
) -> dict[str, str]:
    """The four launch variables one bound launch declares."""

    return {
        BINDING_REF_ENV: binding_ref,
        CAPSULE_PATH_ENV: str(carrier_path),
        CAPSULE_DIGEST_ENV: digest,
        WORKSPACE_ROOT_ENV: str(workspace_root),
    }


def launch_env(
    launch: EveBoundLaunch,
    *,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """One bound launch's environment, optionally extended, as a plain mapping."""

    return {**dict(launch.env), **dict(extra or {})}


__all__ = [
    "ALL_OPERATIONS",
    "ALL_ROLES",
    "CORE_BLOCKS",
    "LEAF_ID",
    "REPOSITORY",
    "TASK_PATH",
    "WORK_BRANCH",
    "ContractAddresses",
    "FixtureCarrierRequest",
    "FixtureWorld",
    "add_worktree",
    "binding_env",
    "build_world",
    "composition_corpus",
    "fixture_carrier_for",
    "launch_env",
    "repository_with_commit",
    "run_git",
]
