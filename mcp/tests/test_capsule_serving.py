"""Focused behaviour cases for the capsule operation and the SEP-2640 skill surface.

Every expected side of every comparison comes from a source the code under test
does not feed: the fixture files written to disk, the composition manifest parsed
independently, the task layer's own vocabularies, the SDK's own protocol types, or a
second server instance publishing the same skill name. A comparison whose two sides
both came from the served value would be vacuous, and this repository's reviews have
rejected that class twice.

The client/server exchange at the end runs the real entry point over stdio with the
installed SDK's own client, so the wire behaviour is observed rather than asserted.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import pytest
from agents_remember.application.skill_resources import (
    CapsuleCompileRequest,
    CapsuleOperationRequest,
    CapsuleSourceSelectionRequest,
    SkillCatalogRequest,
    SkillSourceTree,
    build_skill_catalog,
    compile_task_capsule,
    read_served_file,
    role_capsule_compile_tool,
    skill_catalog_list_tool,
    skill_catalog_read_tool,
)
from agents_remember.application.skill_resources.operation import (
    skill_catalog_registry,
)
from agents_remember.application.task_projection import (
    ProjectionScopeRequest,
    resolve_task_projection_scope,
)
from agents_remember.kernel.coordination_context.models import EnclosureSelector
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp.registration.capsule_serving import (
    declare_skills_extension,
    declared_extensions,
    index_resource,
)
from agents_remember.models.role_capsules.manifest import parse_composition_manifest
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
)
from agents_remember.models.role_capsules.vocabulary import CAPSULE_ROLES
from agents_remember.models.role_capsules.vocabulary import CAPSULE_ROLES as _ALL_ROLE_NAMES
from agents_remember.models.skill_resources import UnreadableSkill
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.models.tools.tool_registry import PUBLIC_TOOL_RESPONSE_MODELS
from agents_remember.tasks.document_refs import TaskDocumentTopology
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP
from pydantic import AnyUrl, BaseModel

from mcp import ClientSession, StdioServerParameters

REPOSITORY = "agents-remember"
MASTER_DIR = "SPRINT/MASTER"

# The extension's wire vocabulary, taken as literals from the final SEP-2640 rather
# than imported from the module under test: a case that imported the constant it
# asserts on would still pass if the constant were wrong.
EXTENSION_ID = "io.modelcontextprotocol/skills"
INDEX_URI = "skill://index.json"
SKILLS_LIST_METHOD = "skills/list"
SKILLS_GET_METHOD = "skills/get"
SKILL_ROOT_FILE = "SKILL.md"
#: The four keys the specification requires on a skill entry and on each of its files.
SEP_ENTRY_KEYS = {"uri", "frontmatter", "resources"}
SEP_RESOURCE_KEYS = {"uri", "digest", "size"}
ALPHA_TASK = f"{MASTER_DIR}/ALPHA.json"
BETA_TASK = f"{MASTER_DIR}/BETA.json"

MCP_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MCP_ROOT.parent
DEV_SOURCE = MCP_ROOT / "src"

#: The shipped corpus, read here as an independent side of every comparison that
#: asks what a served skill actually contains.
LIFECYCLE_SKILLS = REPOSITORY_ROOT / "skills" / "l-01-agent-lifecycles"
SHIPPED_MANIFEST = LIFECYCLE_SKILLS / "composition-manifest.json"
SHIPPED_SKILLS = REPOSITORY_ROOT / "skills"

CORE_BLOCKS = ("authority", "invariants")
#: The frozen operation set, spelled here rather than imported so the fixture can
#: disagree with the code under test instead of moving with it.
ALL_OPERATIONS = (
    "orientation",
    "planning",
    "implementation",
    "review",
    "curation",
    "coordination",
    "authorized-closeout",
    "recovery",
    "bootstrap",
)
#: The ten frozen roles. Spelled here rather than imported so the fixture can
#: disagree with the code under test instead of moving with it.
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
    "bootstrap",
)
#: Which seat each role occupies, as the task layer spells it.
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
    "bootstrap": "free-agent",
}
#: Each role's applicable operations; the manifest declares all nine and narrows
#: applicability per role, which is the shape the compiler requires.
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
    "bootstrap": ("orientation", "bootstrap", "recovery"),
}
#: Roles that declare the served skill, so the reference plane is exercised.
SKILL_DECLARING_ROLES = frozenset(ALL_ROLES)
#: The three the worker seat may run.
WORKER_OPERATIONS = OPERATIONS_BY_ROLE["worker"]


def _sha256_bytes(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


# ---------------------------------------------------------------------------
# The synthetic world: one coordination tree, one corpus, two publishing servers
# ---------------------------------------------------------------------------


def _contract_text(spec: ContractSpec, *, coordination_root: Path) -> str:
    return f"""---
schema: ar-series-contract/v1
schemaVersion: 1.0
kind: leaf
task_id: SPRINT-ID
task_name: SPRINT
repo_name: {REPOSITORY}
workflow_kind: light-task
memory_mode: disabled

coordination:
  root: {coordination_root.as_posix()}
  task_root: {spec.task_root.as_posix()}
  series_contract_path: {spec.contract_path.as_posix()}
  task_artifact: {spec.task_root.as_posix()}/task.md
  worktree_group: {coordination_root.as_posix()}/worktrees/group-ar
  leaf_id: {spec.leaf_id}
  parent_task_name: SPRINT

code:
  repo_path: {spec.code_root.as_posix()}
  source_branch: ar/sprint
  work_branch: {spec.work_branch}
  base_commit: "{"1" * 40}"
  worktree: {spec.code_root.as_posix()}

memory:
  mode: disabled
  repo_path: {coordination_root.as_posix()}
  source_branch: ar/sprint
  work_branch: {spec.work_branch}
  base_commit: "{"2" * 40}"
  worktree: {coordination_root.as_posix()}

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
class ContractSpec:
    leaf_id: str
    task_root: Path
    code_root: Path
    work_branch: str
    contract_path: Path


def _leaf_document(leaf_id: str, objective: str) -> dict[str, Any]:
    return {
        "schema": "ar-task-document/v1",
        "id": leaf_id,
        "slug": leaf_id.lower(),
        "title": f"{leaf_id} slice",
        "kind": "subTask",
        "status": "inProgress",
        "repo": REPOSITORY,
        "type": "Feature",
        "createdAt": "2026-09-16T00:00+02:00",
        "master": "task.md",
        "objective": objective,
        "requirements": [f"{leaf_id} requirement declared as exact text."],
        "steps": [{"id": "S1", "title": f"{leaf_id} step", "status": "pending"}],
    }


def _master_document(rows: list[dict[str, Any]]) -> dict[str, Any]:
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
        "subTasks": rows,
        "decisions": [],
        "sections": [],
    }


def _sprint_document() -> dict[str, Any]:
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
        "integrationBranch": "ar/sprint",
    }


def _synthetic_corpus(root: Path, *, origin: str, skill_name: str) -> SkillSourceTree:
    """One canonical corpus with the core, one role, the operations and a skill.

    Every file is written from this fixture, so a served revision can be compared
    against bytes that were chosen here rather than against the server's own copy.
    """

    (root / "core").mkdir(parents=True, exist_ok=True)
    for block in CORE_BLOCKS:
        (root / "core" / f"{block}.md").write_text(
            f"# {block}\n\n{block.upper()} shared rule, authored once.\n", encoding="utf-8"
        )
    (root / "roles").mkdir(parents=True, exist_ok=True)
    for role in ALL_ROLES:
        (root / "roles" / f"{role}.md").write_text(
            f"# {role}\n\n{role.upper()} seat authority and boundaries.\n", encoding="utf-8"
        )
    (root / "operations").mkdir(parents=True, exist_ok=True)
    for operation in ALL_OPERATIONS:
        (root / "operations" / f"{operation}.md").write_text(
            f"# {operation}\n\n{operation.upper()} operation rules.\n", encoding="utf-8"
        )
    skill = root / skill_name
    (skill / "references").mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        f"name: {skill_name}\n"
        "description: The synthetic lifecycle skill used by focused tests.\n"
        "allowed-tools: read_ar_files, worktree_cleanup, not-a-published-tool\n"
        "---\n\n# Synthetic lifecycles\n\nSKILL BODY for the synthetic corpus.\n",
        encoding="utf-8",
    )
    (skill / "references" / "GUIDE.md").write_text(
        "# Guide\n\nA supporting file addressed relatively.\n", encoding="utf-8"
    )
    # A skill nested inside another skill: SEP-2640 permits a SKILL.md in a descendant
    # directory and requires the nested skill to be published flat, like any other.
    nested = skill / "nested" / "inner-skill"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "SKILL.md").write_text(
        "---\n"
        "name: inner-skill\n"
        "description: The nested skill the synthetic corpus publishes flat.\n"
        "metadata:\n"
        '  version: "2.1.0"\n'
        "  author: the fixture\n"
        "license: Apache-2.0\n"
        "tags: [nested, fixture]\n"
        "---\n\n# Inner\n\nNESTED BODY inside the enclosing skill.\n",
        encoding="utf-8",
    )
    (nested / "support.md").write_text(
        "# Nested support\n\nA file only the nested skill's entry enumerates on its own.\n",
        encoding="utf-8",
    )
    manifest = {
        "schema": "ar-role-capsule-composition/v1",
        "role_order": list(ALL_ROLES),
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
        "core": {
            block: {"source": f"core/{block}.md", "purpose": f"{block} rules"}
            for block in CORE_BLOCKS
        },
        "roles": {
            role: {
                "file": f"roles/{role}.md",
                "altitude": ROLE_ALTITUDES[role],
                "seat": f"the {role} seat",
                "core": list(CORE_BLOCKS),
                "operations": list(OPERATIONS_BY_ROLE[role]),
                "tools": ["read_ar_files"],
                "skills": [skill_name] if role in SKILL_DECLARING_ROLES else [],
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
        "skills": {
            skill_name: {
                "origin": origin,
                "uri": f"skill://{origin}/{skill_name}",
                # The manifest's declared source is the admitted path, and the
                # admitted root is this corpus, so it is relative to the corpus.
                "source": f"{skill_name}/SKILL.md",
            }
        },
    }
    (root / "composition-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return SkillSourceTree(root=root, origin=origin)


class World:
    """One synthetic coordination tree, one corpus, and the requests that address them."""

    def __init__(self, root: Path) -> None:
        self.root = root
        # The coordination tree and the code checkout are siblings, as they are in the
        # real topology; the resolver derives the task root from the coordination root.
        self.coord = root / "ar-coordination"
        self.code = root / REPOSITORY
        self.code.mkdir(parents=True)
        (self.coord / "memory-repos" / f"ar-{REPOSITORY}" / "system").mkdir(parents=True)
        (self.coord / "settings.json").write_text("{}\n", encoding="utf-8")
        tasks = self.coord / "tasks" / REPOSITORY
        self.master_dir = tasks / MASTER_DIR
        self.master_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(tasks / "SPRINT" / "task.json", _sprint_document())
        self._write_json(self.master_dir / "task.json", _master_document(self._rows()))
        self._write_json(
            self.master_dir / "ALPHA.json", _leaf_document("ALPHA", "ALPHA objective.")
        )
        self._write_json(self.master_dir / "BETA.json", _leaf_document("BETA", "BETA objective."))
        self.alpha_contract = self._write_contract("ALPHA", "ar/alpha")
        self.beta_contract = self._write_contract("BETA", "ar/beta")
        # One tree serves both planes: the capsule's admitted corpus root and the
        # skill catalog's source root are the same directory, so a served revision
        # and a referenced revision are two observations of one file.
        self.corpus = root / "corpus-a"
        self.other_corpus = root / "corpus-b"
        self.tree = _synthetic_corpus(self.corpus, origin="server-a/skills", skill_name="shared")
        self.other_tree = _synthetic_corpus(
            self.other_corpus, origin="server-b/skills", skill_name="shared"
        )
        self.catalog = build_skill_catalog(self.tree)
        self.other_catalog = build_skill_catalog(self.other_tree)

    def _rows(self) -> list[dict[str, Any]]:
        return [
            {
                "number": leaf_id,
                "name": f"{leaf_id} slice",
                "file": f"{leaf_id}.md",
                "status": "inProgress",
                "scope": f"the {leaf_id} scope only",
            }
            for leaf_id in ("ALPHA", "BETA")
        ]

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _write_contract(self, leaf_id: str, branch: str) -> Path:
        path = self.master_dir / "enclosures" / leaf_id / "series-contract.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _contract_text(
                ContractSpec(
                    leaf_id=leaf_id,
                    task_root=self.master_dir,
                    code_root=self.code,
                    work_branch=branch,
                    contract_path=path,
                ),
                coordination_root=self.coord,
            ),
            encoding="utf-8",
        )
        return path

    @property
    def config(self) -> McpRuntimeConfig:
        return McpRuntimeConfig(
            config_path=self.coord / "settings.json",
            coordination_root=self.coord,
            workspace_root=self.root,
            transcript_root=self.root / "transcripts",
            repositories={REPOSITORY: RepositoryScope(repo_id=REPOSITORY, path=self.code)},
        )

    def request(
        self,
        *,
        leaf: str = "ALPHA",
        role: str = "worker",
        operation: str = "implementation",
        contract: Path | None = None,
        task_path: str | None = None,
    ) -> CapsuleOperationRequest:
        return CapsuleOperationRequest(
            enclosure_contract_path=str(
                contract or (self.alpha_contract if leaf == "ALPHA" else self.beta_contract)
            ),
            task_path=task_path or (ALPHA_TASK if leaf == "ALPHA" else BETA_TASK),
            role=role,
            operation=operation,  # type: ignore[arg-type]
            code_repository_root=str(self.code),
        )

    def sources(self, corpus: Path | None = None) -> CapsuleSourceSelectionRequest:
        selected = corpus or self.corpus
        return CapsuleSourceSelectionRequest(
            root=selected,
            manifest="composition-manifest.json",
            origin="server-a/skills" if selected == self.corpus else "server-b/skills",
        )

    def compile(self, request: CapsuleOperationRequest, *, corpus: Path | None = None) -> Any:
        return compile_task_capsule(
            self.config, _as_compile_request(request), sources=self.sources(corpus)
        )

    def response(self, request: CapsuleOperationRequest, *, corpus: Path | None = None) -> Any:
        return role_capsule_compile_tool(self.config, request, sources=self.sources(corpus))

    def tree_digest(self) -> str:
        """The digest of every byte under the coordination root and both corpora."""

        digest = hashlib.sha256()
        for base in (self.coord, self.corpus, self.other_corpus):
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    digest.update(path.relative_to(self.root).as_posix().encode("utf-8"))
                    digest.update(path.read_bytes())
        return digest.hexdigest()

    def catalog_request(self, *, corpus: Path | None = None) -> SkillCatalogRequest:
        selected = self.corpus if corpus is None else corpus
        origin = "server-a/skills" if selected == self.corpus else "server-b/skills"
        return SkillCatalogRequest(origin=origin, root=selected)


def _as_compile_request(request: CapsuleOperationRequest) -> CapsuleCompileRequest:
    return CapsuleCompileRequest(
        enclosure=EnclosureSelector(contract_path=Path(request.enclosure_contract_path)),
        task_path=request.task_path,
        operation=request.operation,
        role=request.role,
        code_repository_root=(
            None if request.code_repository_root is None else Path(request.code_repository_root)
        ),
    )


def _frontmatter_of(text: str) -> dict[str, Any]:
    """The ``SKILL.md`` frontmatter as JSON, derived by this module's own reader.

    The independent side of every frontmatter comparison: a case that compared an
    entry's frontmatter against the same entry would prove nothing, so this parses the
    file on disk with the standard library alone.
    """

    lines = text.splitlines()
    assert lines and lines[0].strip() == "---", "the fixture file has no frontmatter fence"
    body = lines[1 : lines.index("---", 1)]
    fields: dict[str, Any] = {}
    for line in body:
        if not line.strip() or line[0] in {" ", "\t"}:
            continue
        key, separator, value = line.partition(":")
        assert separator, line
        fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields


def _entry_named(catalog: Any, name: str) -> Any:
    """One catalog entry selected by name, so adding a skill cannot retarget a case."""

    for candidate in catalog.entries:
        if candidate.name == name:
            return candidate
    raise AssertionError(f"the catalog publishes no skill named {name!r}")


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


# ---------------------------------------------------------------------------
# CAPS-R04 behaviour 1: the narrow read-only capsule operation
# ---------------------------------------------------------------------------


def test_the_capsule_carries_routed_blocks_with_the_bytes_this_fixture_wrote(world: World) -> None:
    """Content, provenance and the task channel, each compared to the fixture's own bytes."""

    outcome = world.compile(world.request())
    assert outcome.ok, outcome.explanation()
    result = outcome.result
    assert result is not None
    capsule = result.capsule

    composed = {unit.block.source_path: unit.block for unit in capsule.instruction_units}
    # A dispatched role seat's capsule is exactly its own two blocks. The fixture still WRITES and
    # DECLARES the shared core sources, so their absence here is a composition fact rather than a
    # fixture that stopped producing them (developer ruling 2026-09-17). The precondition is read
    # from the fixture's own manifest, not asserted about a module constant: `assert CORE_BLOCKS`
    # would be a statement about a literal in this file and could never fail.
    manifest = json.loads((world.corpus / "composition-manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["roles"]["worker"]["core"]) == set(CORE_BLOCKS)
    for core_block in CORE_BLOCKS:
        assert (world.corpus / f"core/{core_block}.md").read_text(encoding="utf-8").strip()
    expected_paths = {"roles/worker.md", "operations/implementation.md"}
    assert set(composed) == expected_paths
    # Both sides are independent: the served content against the file this fixture wrote.
    for relative, block in composed.items():
        on_disk = (world.corpus / relative).read_bytes()
        assert block.content.encode("utf-8") == on_disk
        assert block.revision == _sha256_bytes(on_disk)
        assert block.content_digest == block.revision
    assert [unit.block.composition_root for unit in capsule.instruction_units] == [
        "role",
        "operation",
    ]
    assert capsule.task_context is not None
    assert "ALPHA" in capsule.task_context.markdown
    assert capsule.task_context.content_digest == _sha256_bytes(
        capsule.task_context.markdown.encode("utf-8")
    )
    assert outcome.binding is not None
    assert outcome.binding.admitted.seat.role == "worker"


def test_the_capsule_operation_writes_nothing_to_the_tree_it_reads(world: World) -> None:
    """Two compiles, and the coordination tree plus both corpora are byte-identical."""

    before = world.tree_digest()
    first = world.compile(world.request())
    second = world.compile(world.request())
    assert first.ok and second.ok
    assert first.result is not None and second.result is not None
    assert first.result.semantic_digest == second.result.semantic_digest
    assert world.tree_digest() == before


def test_a_caller_changing_the_role_string_cannot_acquire_another_role(world: World) -> None:
    """The seeded mutation: `worker` -> `architect` on a leaf document is refused.

    Both sides come from different sources: the refusal's status is read from the
    response, and the admissible role set is read from the task layer's own
    vocabulary parsed independently of the compiler.
    """

    refused = world.compile(world.request(role="architect"))
    assert not refused.ok
    assert refused.refusal is not None
    assert refused.refusal.status == "role-altitude-mismatch"
    # The control: the same document, same operation, same corpus, with the role it
    # can carry. If this control also refused, the mutation above would prove nothing.
    admitted = world.compile(world.request(role="worker"))
    assert admitted.ok, admitted.explanation()
    assert admitted.binding is not None
    assert admitted.binding.admitted.seat.role == "worker"
    assert "architect" in CAPSULE_ROLES


def test_an_unknown_role_is_refused_with_its_own_status(world: World) -> None:
    """A string outside the frozen ten is a typed refusal, not a nearest match."""

    refused = world.compile(world.request(role="not-a-role"))
    assert not refused.ok and refused.refusal is not None
    assert refused.refusal.status == "unknown-role"


def test_a_task_path_that_escapes_the_task_root_is_refused(world: World) -> None:
    """The caller names a relative path; a traversal is refused before any read."""

    refused = world.compile(world.request(task_path="../../../../etc/passwd"))
    assert not refused.ok and refused.refusal is not None
    assert refused.refusal.status == "projection-task-reference-invalid"
    # The control: the same request with a path inside the task root compiles.
    assert world.compile(world.request(task_path=ALPHA_TASK)).ok


def test_the_manifest_decides_the_source_set_not_the_caller(world: World) -> None:
    """Removing a required block from the tree is a refusal, not a thinner capsule."""

    (world.corpus / "core" / "invariants.md").unlink()
    refused = world.compile(world.request())
    assert not refused.ok and refused.refusal is not None
    assert refused.refusal.status == "source-missing"
    # Restoring the exact bytes restores the exact capsule, so the refusal was about
    # this file and nothing else the operation happened to remember.
    (world.corpus / "core" / "invariants.md").write_text(
        "# invariants\n\nINVARIANTS shared rule, authored once.\n", encoding="utf-8"
    )
    assert world.compile(world.request()).ok


def test_a_moved_task_document_is_read_again_and_the_capsule_carries_the_new_digest(
    world: World,
) -> None:
    """What the name says: a rewritten document is re-read, not served stale.

    This case is about re-reading. The *refusal* when a recorded admission no longer
    matches the bytes is a separate guarantee with its own case below, because one case
    asserting ``ok`` cannot also be the case that asserts a refusal.
    """

    request = world.request()
    binding_request = _as_compile_request(request)
    world._write_json(
        world.master_dir / "ALPHA.json", _leaf_document("ALPHA", "A different objective.")
    )
    stale = compile_task_capsule(
        world.config,
        CapsuleCompileRequest(
            enclosure=binding_request.enclosure,
            task_path=binding_request.task_path,
            operation=binding_request.operation,
            role=binding_request.role,
            code_repository_root=binding_request.code_repository_root,
        ),
        sources=world.sources(),
    )
    assert stale.ok, stale.explanation()
    assert stale.binding is not None
    observed = _sha256_bytes((world.master_dir / "ALPHA.json").read_bytes())
    assert stale.binding.admitted.task_document_digest == observed


def test_a_recorded_admission_whose_bytes_moved_is_refused(world: World) -> None:
    """The other half: an admission that no longer matches the bytes on disk refuses.

    ``resolve_task_projection_scope`` is where the comparison lives — the digest
    travels in on the binding and the scope computes the digest of the bytes it
    actually read — so this hands it a binding whose recorded revision is deliberately
    not the current one and observes the refusal. Both digests are real and neither
    came from the other: the observed one is the fixture's own file, the admitted one is
    the sentinel this case chose.
    """

    sentinel = _sha256_bytes(b"bytes that are not the document")
    stale_binding = CapsuleBinding(
        operation="implementation",
        admitted=CapsuleAdmittedFacts(
            task_reference=f"{REPOSITORY}/{ALPHA_TASK}",
            task_document_digest=sentinel,
            seat=CapsuleRoleSeat(role="worker", altitude="leaf"),
            repository_id=REPOSITORY,
            work_branch="ar/alpha",
            tool_policy=CapsuleToolPolicy(
                granted=frozenset(PUBLIC_TOOLS), notes="the published surface"
            ),
        ),
    )
    with pytest.raises(Exception) as raised:
        resolve_task_projection_scope(
            stale_binding,
            coordination_root=world.coord,
            scope_request=ProjectionScopeRequest(
                selector=EnclosureSelector(contract_path=world.alpha_contract),
                workspace_root=world.root,
                code_repository_root=world.code,
            ),
        )
    message = str(raised.value)
    assert "admitted revision" in message
    assert _sha256_bytes((world.master_dir / "ALPHA.json").read_bytes()) in message
    assert sentinel in message


# ---------------------------------------------------------------------------
# CAPS-R04 behaviour 4 and 5: provenance, trust, and the two content planes
# ---------------------------------------------------------------------------


def test_a_served_skill_keeps_its_origin_and_revision(world: World) -> None:
    """The served body's revision equals the digest of the fixture's own file."""

    catalog = world.catalog
    entry = _entry_named(catalog, "shared")
    served = read_served_file(catalog, entry.uri)
    on_disk = (world.corpus / "shared" / "SKILL.md").read_bytes()
    assert served.content == on_disk
    assert served.record.revision == _sha256_bytes(on_disk)
    assert served.entry.origin == "server-a/skills"
    assert served.entry.identity == "server-a/skills#shared"


def test_a_read_refuses_a_body_whose_bytes_changed_since_the_catalog(world: World) -> None:
    """A moved source is a refusal, never a delivery under the catalog's old revision."""

    catalog = world.catalog
    uri = _entry_named(catalog, "shared").uri
    (world.corpus / "shared" / "SKILL.md").write_text(
        "---\nname: shared\ndescription: The synthetic lifecycle skill used by focused tests.\n"
        "---\n\nA DIFFERENT BODY.\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception) as raised:
        read_served_file(catalog, uri)
    assert "changed since the catalog was built" in str(raised.value)


def test_reading_a_skill_does_not_grant_the_tools_its_frontmatter_names(world: World) -> None:
    """An `allowed-tools` declaration is observed and never applied.

    The mutation: the fixture skill declares `worktree_cleanup`, which the synthetic
    composition manifest does not request. A read must not turn it into a permission,
    and the capsule's requested-tool set must still come from the manifest alone.
    """

    catalog = world.catalog
    entry = _entry_named(catalog, "shared")
    assert entry.declared_allowed_tools == (
        "read_ar_files",
        "worktree_cleanup",
        "not-a-published-tool",
    )
    served = read_served_file(catalog, entry.uri)
    text = served.text
    assert text is not None
    assert "allowed-tools: read_ar_files, worktree_cleanup, not-a-published-tool" in text

    outcome = world.compile(world.request())
    assert outcome.ok
    result = outcome.result
    assert result is not None
    requested = {row.tool_id for row in result.capsule.requested_tools}
    # The manifest declares exactly one tool for the worker role; the skill's own
    # declaration must not have added to it.
    assert requested == {"read_ar_files"}
    # The declarations cover both cases: `worktree_cleanup` is a real advertised tool and
    # `not-a-published-tool` is not, so a permission channel would show up either as an
    # extra request or as a name the published surface never carried.
    assert "worktree_cleanup" in PUBLIC_TOOLS
    assert "not-a-published-tool" not in PUBLIC_TOOLS
    assert "worktree_cleanup" not in requested
    assert "not-a-published-tool" not in result.capsule.binding.admitted.tool_policy.granted


def test_a_resource_read_leaves_the_tool_surface_and_response_registry_unchanged(
    world: World,
) -> None:
    """Reading server-supplied content cannot widen what the server advertises."""

    roster_before = tuple(PUBLIC_TOOLS)
    models_before = tuple(sorted(PUBLIC_TOOL_RESPONSE_MODELS))
    catalog = world.catalog
    read_served_file(catalog, catalog.entries[0].uri)
    skill_catalog_read_tool(catalog.entries[0].uri, world.catalog_request(corpus=world.corpus))
    assert tuple(PUBLIC_TOOLS) == roster_before
    assert tuple(sorted(PUBLIC_TOOL_RESPONSE_MODELS)) == models_before


def test_same_named_skills_from_two_servers_remain_distinct(world: World) -> None:
    """One name, two origins: distinct identities, distinct URIs, distinct bodies."""

    here = _entry_named(world.catalog, "shared")
    there = _entry_named(world.other_catalog, "shared")
    assert here.name == there.name == "shared"
    assert here.identity != there.identity
    assert here.uri != there.uri
    assert here.origin == "server-a/skills" and there.origin == "server-b/skills"
    assert (world.other_corpus / "shared" / "SKILL.md").write_text(
        (world.other_corpus / "shared" / "SKILL.md").read_text(encoding="utf-8")
        + "\nAn extra line only server B carries.\n",
        encoding="utf-8",
    )
    fresh = _entry_named(build_skill_catalog(world.other_tree), "shared")
    assert fresh.identity == there.identity
    assert fresh.root is not None and there.root is not None
    assert fresh.root.revision != there.root.revision


def test_the_discovery_registry_is_not_the_model_visible_catalog(world: World) -> None:
    """Listing names skills without their bodies; only a selected read carries one."""

    catalog = world.catalog
    shared = _entry_named(catalog, "shared")
    body = (world.corpus / "shared" / "SKILL.md").read_text(encoding="utf-8")
    nested_body = (world.corpus / "shared" / "nested" / "inner-skill" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    listing = skill_catalog_list_tool(world.catalog_request(corpus=world.corpus))
    payload = listing.to_payload()
    serialized = json.dumps(payload)
    assert "SKILL BODY for the synthetic corpus." not in serialized
    assert "NESTED BODY inside the enclosing skill." not in serialized
    assert "A supporting file addressed relatively." not in serialized
    for row in payload["skills"]:
        assert set(row) >= {"name", "description", "origin", "uri", "revision", "files"}
        assert "content" not in row
    listed_uris = {row["uri"] for row in payload["skills"]}
    assert shared.uri in listed_uris
    assert _entry_named(catalog, "inner-skill").uri in listed_uris

    selected = skill_catalog_read_tool(
        shared.uri, world.catalog_request(corpus=world.corpus)
    ).to_payload()
    assert body == selected["content"]
    assert nested_body != body


def test_a_skill_body_is_not_composed_into_the_instruction_stream(world: World) -> None:
    """Stable reusable modules stay separate from per-task composition.

    The worker role declares the skill, so the capsule must carry a reference to it
    and must not carry its prose as an instruction block. This is the mutation that
    would show up if a skill were silently promoted into the trusted stream.
    """

    outcome = world.compile(world.request())
    assert outcome.ok
    result = outcome.result
    assert result is not None
    composed = "\n".join(unit.block.content for unit in result.capsule.instruction_units)
    assert "SKILL BODY for the synthetic corpus." not in composed
    assert [reference.uri for reference in result.capsule.skill_references] == [
        "skill://server-a/skills/shared"
    ]
    reference = result.capsule.skill_references[0]
    assert reference.origin == "server-a/skills"
    assert reference.revision == _sha256_bytes((world.corpus / "shared" / "SKILL.md").read_bytes())


def test_a_skill_directory_whose_root_file_is_not_conforming_is_recorded_not_served(
    world: World,
) -> None:
    """A skill that cannot describe itself is a recorded gap, and delivery refuses."""

    broken = world.corpus / "broken-skill"
    broken.mkdir(parents=True)
    (broken / "SKILL.md").write_text("# no frontmatter at all\n", encoding="utf-8")
    catalog = build_skill_catalog(world.tree)
    assert [row.skill_path for row in catalog.unreadable] == ["broken-skill"]
    assert "shared" in catalog.names()
    assert "broken-skill" not in catalog.names()
    # The listing still works and says what it could not serve: a discovery surface
    # that failed closed on a sister skill's defect would hide the good ones.
    listing = skill_catalog_list_tool(world.catalog_request(corpus=world.corpus)).to_payload()
    assert [row["skillPath"] for row in listing["unreadable"]] == ["broken-skill"]
    assert "shared" in {row["name"] for row in listing["skills"]}
    # Delivery refuses while an unreadable skill exists, so a tree edit cannot make a
    # published skill disappear from the served surface without saying so.
    with pytest.raises(Exception) as raised:
        skill_catalog_read_tool(
            _entry_named(catalog, "shared").uri, world.catalog_request(corpus=world.corpus)
        )
    assert "cannot be served" in str(raised.value)


# ---------------------------------------------------------------------------
# CAPS-R04 behaviour 2 and 6: the shipped surface and the real protocol exchange
# ---------------------------------------------------------------------------


def test_the_shipped_corpus_parses_and_every_served_skill_has_a_root_revision() -> None:
    """The real corpus: every skill has frontmatter, a body digest and a readable URI."""

    catalog = skill_catalog_registry()
    assert catalog.unreadable == ()
    assert len(catalog.entries) >= 14
    for entry in catalog.entries:
        root = entry.root
        assert root is not None, entry.name
        assert root.revision.startswith("sha256:")
        assert root.byte_length > 0
        assert entry.description
        assert entry.uri.endswith("/SKILL.md")
    # Every entry's frontmatter is compared against the file on disk, parsed here by
    # this case rather than read back from the entry it is checking.
    for entry in catalog.entries:
        on_disk = (Path(catalog.source_root) / entry.skill_path / SKILL_ROOT_FILE).read_text(
            encoding="utf-8"
        )
        declared = _frontmatter_of(on_disk)
        assert entry.frontmatter == declared, entry.name
        assert entry.name == declared["name"]
        assert entry.description == declared["description"]
        # `resources` is complete: every file of the skill, each exactly once.
        uris = [record.uri for record in entry.files]
        assert len(uris) == len(set(uris))
        assert entry.uri in uris
        assert all(record.revision.startswith("sha256:") for record in entry.files)


def test_the_composition_manifest_declares_the_skill_that_is_served() -> None:
    """The two planes agree on the same skill identity and origin."""

    parsed = parse_composition_manifest(SHIPPED_MANIFEST.read_bytes())
    catalog = skill_catalog_registry()
    assert catalog.origin == "agents-remember/skills"
    for name, declared in parsed.skills.items():
        entry = catalog.entry(f"agents-remember/skills#{name}")
        assert entry is not None, name
        assert declared.uri == entry.uri.rsplit("/SKILL.md", 1)[0]
        assert declared.source == "SKILL.md"
    assert set(parsed.skills).issubset(set(catalog.names()))


def test_this_servers_own_index_resource_keeps_the_agent_skills_discovery_shape() -> None:
    """What this case actually pins: *this server's* index resource, not the extension.

    ``skill://index.json`` is served as an ordinary resource in the Agent Skills
    well-known-discovery shape. That shape is not SEP-2640's enumeration result — the
    extension enumerates through the ``skills/list`` method, whose entries carry
    verbatim frontmatter and per-file digests — so the case is named for the artefact it
    asserts and the extension's own shape is pinned separately.
    """

    catalog = skill_catalog_registry()
    document = catalog.index_document()
    assert set(document) == {"$schema", "skills"}
    rows = document["skills"]
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, dict)
        assert row["type"] == "skill-md"
        assert row["name"] and row["description"] and row["url"]
        assert str(row["url"]).startswith("skill://agents-remember/skills/")
        meta = row["_meta"]
        assert isinstance(meta, dict)
        assert meta["io.modelcontextprotocol.skills/origin"] == "agents-remember/skills"
        assert str(meta["io.modelcontextprotocol.skills/revision"]).startswith("sha256:")
    # The two shapes are different artefacts and must not be confused: this server's
    # index row carries `type`/`description`/`url`, the extension's entry carries
    # `uri`/`frontmatter`/`resources`.
    entry = catalog.entry_documents()[0]
    assert set(entry) == SEP_ENTRY_KEYS
    assert set(rows[0]) != set(entry)


def test_every_sep_2640_entry_is_complete_and_carries_verbatim_frontmatter() -> None:
    """The extension's own entry shape, checked against the tree rather than itself."""

    catalog = skill_catalog_registry()
    entries = catalog.entry_documents()
    assert len(entries) == len(catalog.entries)
    for entry in entries:
        assert set(entry) == SEP_ENTRY_KEYS
        uri = entry["uri"]
        assert isinstance(uri, str) and uri.endswith(f"/{SKILL_ROOT_FILE}")
        frontmatter = entry["frontmatter"]
        assert isinstance(frontmatter, dict)
        # Independent side: the frontmatter parsed from the file this URI addresses.
        relative = uri.split("agents-remember/skills/", 1)[1]
        on_disk = (SHIPPED_SKILLS / relative).read_text(encoding="utf-8")
        assert frontmatter == _frontmatter_of(on_disk)
        assert uri.split("/")[-2] == frontmatter["name"]
        resources = entry["resources"]
        assert isinstance(resources, list) and resources
        seen: set[str] = set()
        for record in resources:
            assert isinstance(record, dict)
            assert set(record) == SEP_RESOURCE_KEYS
            assert str(record["digest"]).startswith("sha256:")
            assert isinstance(record["size"], int) and record["size"] > 0
            record_uri = str(record["uri"])
            assert record_uri not in seen
            seen.add(record_uri)
            served = read_served_file(catalog, record_uri)
            assert len(served.content) == record["size"]
            assert _sha256_bytes(served.content) == record["digest"]
        assert uri in seen


def _live_settings(root: Path) -> Path:
    path = root / "mcp-settings.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": str(root / "coord"),
                "workspaceRoot": str(root / "workspace"),
                "repositories": {REPOSITORY: {}},
                "providers": {},
            }
        ),
        encoding="utf-8",
    )
    return path


def _server_parameters(tmp_path: Path) -> StdioServerParameters:
    """The documented argv, in a disposable coordination root, with this worktree's source."""

    settings = _live_settings(tmp_path)
    (tmp_path / "coord").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "agents_remember.mcp", "--config", str(settings)],
        env={**os.environ, "PYTHONPATH": str(DEV_SOURCE), "PYTHONDONTWRITEBYTECODE": "1"},
    )


async def _drive_exchange(parameters: StdioServerParameters) -> dict[str, Any]:
    """One client session against the real server: everything the client observes."""

    observed: dict[str, Any] = {}
    with open(os.devnull, "w") as devnull:
        async with stdio_client(parameters, errlog=devnull) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                observed["capabilities"] = initialized.capabilities.model_dump(
                    mode="json", exclude_none=True
                )
                listed = await session.list_resources()
                observed["resources"] = [str(item.uri) for item in listed.resources]
                observed["resourceNames"] = {str(item.uri): item.name for item in listed.resources}
                observed["resourceMeta"] = {
                    str(item.uri): item.meta or {} for item in listed.resources
                }
                index = await session.read_resource(AnyUrl(INDEX_URI))
                observed["index"] = index.contents[0].text  # type: ignore[union-attr]
                observed["indexMime"] = index.contents[0].mimeType
                for label, uri in (("body", _skill_uri()), ("guide", _guide_uri())):
                    content = (await session.read_resource(AnyUrl(uri))).contents[0]
                    observed[label] = content.text  # type: ignore[union-attr]
                    observed[f"{label}Mime"] = content.mimeType
                    observed[f"{label}Meta"] = content.meta or {}
                try:
                    await session.read_resource(
                        AnyUrl("skill://agents-remember/skills/absent/SKILL.md")
                    )
                except Exception as error:
                    observed["absent"] = type(error).__name__
                    observed["absentMessage"] = str(error)
                tools = await session.list_tools()
                observed["tools"] = [tool.name for tool in tools.tools]
                observed["extension"] = await _drive_extension_methods(session)
    return observed


async def _drive_extension_methods(session: ClientSession) -> dict[str, Any]:
    """Issue the extension's own methods over the transport the declaration rode in on.

    The SDK's client has no skills affordance, so the two requests are sent as the
    protocol messages a conforming client sends. Nothing here can be satisfied by a
    server that only declares the extension: each call either reaches a handler or
    comes back as the SDK's own "Invalid request parameters" error.
    """

    class _ExtensionRequest(BaseModel):
        method: str
        params: dict[str, Any] = {}

    class _ExtensionResult(BaseModel):
        model_config = {"extra": "allow"}

    async def call(method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await session.send_request(
                cast(Any, _ExtensionRequest(method=method, params=params)), _ExtensionResult
            )
            return {"ok": True, "result": result.model_dump(mode="json", exclude_none=True)}
        except Exception as error:
            return {"ok": False, "error": type(error).__name__, "message": str(error)}

    return {
        "list": await call(SKILLS_LIST_METHOD, {}),
        "get": await call(
            SKILLS_GET_METHOD,
            {"uri": "skill://agents-remember/skills/l-01-agent-lifecycles/SKILL.md"},
        ),
        "getAbsent": await call(
            SKILLS_GET_METHOD, {"uri": "skill://agents-remember/skills/absent/SKILL.md"}
        ),
        "unregistered": await call("skills/directory/read", {"uri": "skill://x"}),
    }


def _served_entry() -> Any:
    entry = skill_catalog_registry().entry("agents-remember/skills#l-01-agent-lifecycles")
    assert entry is not None
    return entry


def _skill_uri() -> str:
    return "skill://" + _served_entry().uri.split("skill://", 1)[1]


def _guide_uri() -> str:
    return f"{_skill_uri().rsplit('/SKILL.md', 1)[0]}/roles/worker.md"


@pytest.mark.integration
def test_a_real_client_and_server_exchange_over_the_installed_sdk(tmp_path: Path) -> None:
    """The real entry point over stdio, driven by the installed SDK's own client.

    This is the conformance exchange: the server is started as its own process with
    the documented argv, and every observation below is what the client actually
    received -- the negotiated capabilities, the resource list, an index read, one
    selected skill read, a supporting-file read, and the not-found refusal. Nothing
    here is a unit-level stub, and the request that reads a file is issued by a
    separate implementation from the one that answers it.
    """

    observed = asyncio.run(_drive_exchange(_server_parameters(tmp_path)))

    # 1. The declared extension reached the wire, and the resources primitive did too.
    assert observed["capabilities"]["extensions"] == {EXTENSION_ID: {}}
    assert "resources" in observed["capabilities"]
    # 2. Discovery enumerates resources without their bodies.
    assert INDEX_URI in observed["resources"]
    assert _skill_uri() in observed["resources"]
    assert observed["resourceNames"][_skill_uri()] == "l-01-agent-lifecycles: SKILL.md"
    assert (
        observed["resourceMeta"][_skill_uri()]["io.modelcontextprotocol.skills/origin"]
        == "agents-remember/skills"
    )
    # 3. This server's own index resource names every served skill. It is *this
    # server's* discovery surface, not the extension's enumeration result.
    index = json.loads(observed["index"])
    assert observed["indexMime"] == "application/json"
    assert {row["name"] for row in index["skills"]} == set(skill_catalog_registry().names())
    # 4. A selected body is the exact bytes on disk in this worktree.
    on_disk = (LIFECYCLE_SKILLS / "SKILL.md").read_text(encoding="utf-8")
    assert observed["body"] == on_disk
    assert observed["bodyMime"] == "text/markdown"
    assert observed["bodyMeta"]["io.modelcontextprotocol.skills/revision"] == _sha256_bytes(
        on_disk.encode("utf-8")
    )
    assert (
        observed["bodyMeta"]["io.modelcontextprotocol.skills/contentTrust"]
        == "server-supplied-data"
    )
    # 5. A relative supporting reference resolves against the skill root.
    assert observed["guide"] == (LIFECYCLE_SKILLS / "roles" / "worker.md").read_text(
        encoding="utf-8"
    )
    # 6. The unsupported read is refused rather than served as an empty success.
    assert "absent" in observed, "an unlisted skill URI must not read successfully"
    # 7. The advertised tool surface includes the capsule operation and the skill reads.
    for name in ("role_capsule_compile", "skill_catalog_list", "skill_catalog_read"):
        assert name in observed["tools"]
    # 8. The declared extension's own two methods are answered on this same transport.
    extension = observed["extension"]
    assert extension["list"]["ok"], extension["list"]
    listing = extension["list"]["result"]
    assert listing["resultType"] == "complete"
    assert listing["skills"], "a declaring server answers skills/list with its entries"
    entries = {entry["uri"]: entry for entry in listing["skills"]}
    assert _skill_uri() in entries
    entry = entries[_skill_uri()]
    assert set(entry) == SEP_ENTRY_KEYS
    assert entry["frontmatter"] == _frontmatter_of(on_disk)
    assert {record["uri"] for record in entry["resources"]} == {
        record.uri for record in _served_entry().files
    }
    assert extension["get"]["ok"], extension["get"]
    assert extension["get"]["result"]["skill"] == entry
    # An unlisted URI is the error `resources/read` uses, not an empty success.
    assert not extension["getAbsent"]["ok"]
    # A method this server does not register is still refused: the extension is a
    # bounded addition, not a blanket "answer anything" mode.
    assert not extension["unregistered"]["ok"]


@pytest.mark.integration
def test_the_server_process_never_serves_a_file_outside_a_skill_directory(
    tmp_path: Path,
) -> None:
    """A traversal URI is refused by the running server, not normalized into a read."""

    settings = _live_settings(tmp_path)
    (tmp_path / "coord").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agents_remember.mcp", "--config", str(settings)],
        env={**os.environ, "PYTHONPATH": str(DEV_SOURCE), "PYTHONDONTWRITEBYTECODE": "1"},
    )

    async def probe() -> str:
        with open(os.devnull, "w") as devnull:
            async with stdio_client(parameters, errlog=devnull) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    try:
                        await session.read_resource(
                            AnyUrl(
                                "skill://agents-remember/skills/l-01-agent-lifecycles"
                                "/../../../../etc/passwd"
                            )
                        )
                    except Exception as error:
                        return type(error).__name__
        return "served"

    assert asyncio.run(probe()) != "served"


def test_the_mutation_harness_can_actually_fail() -> None:
    """A sanity check on the evidence: a wrong assertion here does raise.

    Every seeded-mutation proof in this leaf's evidence is only as good as the claim
    that its assertions can fail. This states that claim in the test module itself.
    """

    with pytest.raises(AssertionError):
        assert _sha256_bytes(b"one") == _sha256_bytes(b"another")
    with pytest.raises(KeyError):
        {"present": 1}["absent"]
    with pytest.raises(subprocess.SubprocessError):
        raise subprocess.SubprocessError("the harness's own failure path is reachable")


# ---------------------------------------------------------------------------
# The guards the baseline review found unpinned (F-L4-08), and the nesting rule
# ---------------------------------------------------------------------------


def test_a_catalog_record_that_escapes_its_skill_directory_is_refused(world: World) -> None:
    """The containment proof, reached: a record resolving outside the skill refuses.

    The reviewer's seed removed ``catalog.py``'s containment guard and every case still
    passed, because the traversal case refuses earlier, at catalog lookup. This case
    builds the record the guard exists for — one whose ``relative_path`` escapes — and
    drives the read itself, so removing the guard fails here.
    """

    catalog = world.catalog
    entry = _entry_named(catalog, "shared")
    target = next(record for record in entry.files if record.relative_path == "SKILL.md")
    escaping = replace(target, relative_path="../../../../etc/passwd")
    tampered = replace(
        catalog,
        entries=tuple(
            replace(candidate, files=tuple(escaping if r is target else r for r in candidate.files))
            if candidate is entry
            else candidate
            for candidate in catalog.entries
        ),
    )
    with pytest.raises(Exception) as raised:
        read_served_file(tampered, escaping.uri)
    assert "outside its skill directory" in str(raised.value)


def test_the_index_reader_refuses_while_a_skill_cannot_be_served() -> None:
    """The wire index loader's refusal, reached through the served surface.

    The reviewer's seed removed ``require_servable`` from the index resource's loader
    and every case still passed. This case serves an index over a catalog with an
    unreadable skill and asserts the refusal, so removing that call fails here.
    """

    catalog = replace(
        skill_catalog_registry(),
        unreadable=(UnreadableSkill(skill_path="broken", reason="no usable frontmatter"),),
    )
    resource = index_resource(catalog)
    with pytest.raises(Exception) as raised:
        asyncio.run(resource.read())
    assert "cannot be served" in str(raised.value)


def test_a_skill_whose_directory_name_disagrees_with_its_name_is_recorded_not_served(
    world: World,
) -> None:
    """The name/path-segment rule, reached: a mismatched pair is refused.

    SEP-2640 requires the final ``<skill-path>`` segment to equal the frontmatter
    ``name``. The reviewer's seed removed that check and every case still passed; this
    case writes the disagreement the rule forbids and asserts it is not served.
    """

    misplaced = world.corpus / "declared-name"
    misplaced.mkdir(parents=True)
    (misplaced / "SKILL.md").write_text(
        "---\nname: a-different-name\ndescription: name and directory disagree.\n---\n\n# x\n",
        encoding="utf-8",
    )
    catalog = build_skill_catalog(world.tree)
    recorded = {row.skill_path: row.reason for row in catalog.unreadable}
    assert "declared-name" in recorded
    assert "final skill-path segment" in recorded["declared-name"]
    assert "declared-name" not in catalog.names()


def test_the_extension_declaration_is_installed_once_per_server(world: World) -> None:
    """The declaration idempotence guard, reached: a second install adds no second wrapper.

    The reviewer's seed removed the guard and every case still passed. This case
    registers the same server twice and asserts that the ``initialize`` options are
    built by one wrapper and declare the extension exactly once.
    """

    server = FastMCP("idempotence-probe")
    declare_skills_extension(server)
    first = server._mcp_server.create_initialization_options
    declare_skills_extension(server)
    second = server._mcp_server.create_initialization_options
    assert first is second, "a second declaration must not stack a second wrapper"
    assert declared_extensions(server) == {EXTENSION_ID: {}}


def test_altitude_admission_admits_every_role_the_document_can_carry_and_refuses_the_rest(
    world: World,
) -> None:
    """The role rule is the task layer's, not a hardcoded role name.

    The reviewer's point: this leaf's document admits ``worker``, ``curator`` and
    ``reviewer``, so an implementation that hardcoded ``role == "worker"`` would pass
    every earlier case. Both sides here come from the task layer itself, driven against
    the fixture's own leaf document, so a narrowed rule fails on the first admitted role
    it omits. The document is the fixture's, not the leaf's real one, so the case runs
    wherever the worktree is checked out.
    """

    topology = TaskDocumentTopology(world.coord)
    ref = TaskDocumentRef(repository=REPOSITORY, path=ALPHA_TASK)
    admitted: list[str] = []
    refused: list[str] = []
    for role in _ALL_ROLE_NAMES:
        try:
            topology.validate_role(ref, role)
        except Exception:
            refused.append(role)
            continue
        admitted.append(role)
    assert admitted == ["worker", "curator", "reviewer"]
    # The rest are refused: four need a sprint, one needs a master, and ``bootstrap``
    # carries no structural task altitude at all -- it is the seat a workspace reaches
    # before a task document exists.
    assert refused == [
        "architect",
        "orchestrator",
        "designer",
        "strategist",
        "manager",
        "system-specialist",
        "bootstrap",
    ]
    # A role outside the frozen ten is refused as unsupported, not as a wrong altitude.
    with pytest.raises(Exception) as raised:
        topology.validate_role(ref, "not-a-role")
    assert "no structural task altitude" in str(raised.value)


def test_the_capsule_operation_serves_every_seat_its_document_can_carry(world: World) -> None:
    """The capsule path — not only the task layer — admits every same-altitude seat.

    The reviewer's seed `G5b` narrows the rule to ``worker`` *inside* the capsule
    operation, and it survived every case: the other altitude case drives
    ``TaskDocumentTopology.validate_role`` directly, so nothing ever compiled the
    capsule for a different same-altitude seat. This case does, through the two entry
    points a consumer uses — the typed ``compile_task_capsule`` and the
    ``role_capsule_compile_tool`` response — and asserts the seat and altitude each
    reports. The mirror half proves the refusal *by name* for a role this document
    cannot carry, through the same call, so a blanket widening fails here too.
    """

    # Every seat this leaf document can carry, with an operation each may run.
    for role, operation in (
        ("worker", "implementation"),
        ("curator", "curation"),
        ("reviewer", "review"),
    ):
        outcome = world.compile(world.request(role=role, operation=operation))
        assert outcome.ok, f"{role} was refused: {outcome.explanation()}"
        assert outcome.binding is not None
        seat = outcome.binding.admitted.seat
        assert seat.role == role
        assert seat.altitude == "leaf"
        # The same seat reaches the wire response a consumer reads.
        response = world.response(world.request(role=role, operation=operation)).to_payload()
        assert response["ok"] is True, f"{role}: {response['explanation']}"
        assert response["role"] == role
        assert response["seatAltitude"] == "leaf"

    # The mirror: a role at another altitude is refused by name, through the same call.
    for role, operation in (("manager", "coordination"), ("architect", "planning")):
        refused = world.compile(world.request(role=role, operation=operation))
        assert not refused.ok, f"{role} should not be admitted at leaf altitude"
        assert refused.refusal is not None
        assert refused.refusal.status == "role-altitude-mismatch"
        assert role in refused.refusal.detail
        mirror = world.response(world.request(role=role, operation=operation)).to_payload()
        assert mirror["ok"] is False
        assert mirror["refusalStatus"] == "role-altitude-mismatch"


def test_a_nested_skill_is_published_flat_like_any_other(world: World) -> None:
    """SEP-2640 §Nested skills: nesting is permitted and publication stays flat.

    The enclosing skill's own entry lists the nested skill's files as supporting files,
    and the nested skill additionally has its own ordinary entry whose URI merely
    shares a path prefix. Nothing in either entry marks the nesting.
    """

    catalog = world.catalog
    outer = _entry_named(catalog, "shared")
    inner = _entry_named(catalog, "inner-skill")
    assert inner.skill_path == "shared/nested/inner-skill"
    assert inner.uri.startswith(f"{outer.uri.rsplit('/', 1)[0]}/nested/")
    # The enclosing entry is complete: it lists the nested skill's files too.
    outer_files = {record.relative_path for record in outer.files}
    assert "nested/inner-skill/SKILL.md" in outer_files
    assert "nested/inner-skill/support.md" in outer_files
    # The nested entry is an ordinary flat entry: its own root file, no nesting marker.
    inner_files = {record.relative_path for record in inner.files}
    assert inner_files == {"SKILL.md", "support.md"}
    assert set(inner.entry_document()) == SEP_ENTRY_KEYS
    # The nested frontmatter is verbatim, nested mapping and list included — and the
    # *entry*, which is what a client reads, carries those fields rather than a curated
    # subset. Both sides are independent: the fixture's own declared values, and the
    # file on disk parsed here.
    declared = {
        "name": "inner-skill",
        "description": "The nested skill the synthetic corpus publishes flat.",
        "metadata": {"version": "2.1.0", "author": "the fixture"},
        "license": "Apache-2.0",
        "tags": ["nested", "fixture"],
    }
    assert inner.frontmatter == declared
    assert inner.entry_document()["frontmatter"] == declared
    on_disk = (world.corpus / "shared" / "nested" / "inner-skill" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "license: Apache-2.0" in on_disk
    # Reading the nested root file through the enclosing skill's entry is ordinary
    # reading, and it is the same bytes either way.
    nested_file = next(
        record for record in outer.files if record.relative_path == "nested/inner-skill/SKILL.md"
    )
    assert (
        read_served_file(catalog, nested_file.uri).content
        == (world.corpus / "shared" / "nested" / "inner-skill" / "SKILL.md").read_bytes()
    )
