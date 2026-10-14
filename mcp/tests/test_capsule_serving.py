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

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    require_servable,
    role_capsule_response,
    skill_catalog_list_response,
    skill_catalog_read_response,
)
from agents_remember.application.task_projection import (
    ProjectionScopeRequest,
    resolve_task_projection_scope,
)
from agents_remember.kernel.coordination_context.models import EnclosureSelector
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
)
from agents_remember.models.role_capsules.vocabulary import CAPSULE_ROLES
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS

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
    "investigator",
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
    "investigator": "leaf",
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
    "investigator": ("orientation", "recovery"),
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
memory_mode: external

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
  mode: external
  repo_path: {coordination_root.as_posix()}
  source_branch: ar/sprint
  work_branch: {spec.work_branch}
  base_commit: "{"2" * 40}"
  worktree: {coordination_root.as_posix()}
  ledger: {coordination_root.as_posix()}/memory-repos/ar-{REPOSITORY}/memory.md

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
        return role_capsule_response(self.compile(request, corpus=corpus))

    def catalog_response(self, request: SkillCatalogRequest) -> Any:
        """The discovery response for one catalog request, without the removed tool."""

        selected = request or SkillCatalogRequest()
        catalog = build_skill_catalog(
            SkillSourceTree(root=selected.root or self.corpus, origin=selected.origin)
        )
        return skill_catalog_list_response(catalog)

    def read_response(self, uri: str, request: SkillCatalogRequest | None = None) -> Any:
        """One served skill file for one catalog request, without the removed tool."""

        selected = request or SkillCatalogRequest()
        catalog = build_skill_catalog(
            SkillSourceTree(root=selected.root or self.corpus, origin=selected.origin)
        )
        require_servable(catalog, action="reading a skill file")
        served = read_served_file(catalog, uri)
        root = served.entry.root
        return skill_catalog_read_response(
            served, skill_revision="" if root is None else root.revision
        )

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
    models_before = tuple(sorted(TOOL_RESPONSE_MODELS))
    catalog = world.catalog
    read_served_file(catalog, catalog.entries[0].uri)
    world.read_response(catalog.entries[0].uri, world.catalog_request(corpus=world.corpus))
    assert tuple(PUBLIC_TOOLS) == roster_before
    assert tuple(sorted(TOOL_RESPONSE_MODELS)) == models_before


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
    listing = world.catalog_response(world.catalog_request(corpus=world.corpus))
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

    selected = world.read_response(
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
    listing = world.catalog_response(world.catalog_request(corpus=world.corpus)).to_payload()
    assert [row["skillPath"] for row in listing["unreadable"]] == ["broken-skill"]
    assert "shared" in {row["name"] for row in listing["skills"]}
    # Delivery refuses while an unreadable skill exists, so a tree edit cannot make a
    # published skill disappear from the served surface without saying so.
    with pytest.raises(Exception) as raised:
        world.read_response(
            _entry_named(catalog, "shared").uri, world.catalog_request(corpus=world.corpus)
        )
    assert "cannot be served" in str(raised.value)


# ---------------------------------------------------------------------------
# The shipped-surface, protocol-exchange and guard cases moved whole to
# test_capsule_serving_surface.py (MIK-R50 A3 size extraction). That module
# imports this module's worlds, vocabularies and helpers, so every definition
# still lives exactly once.
# ---------------------------------------------------------------------------
