"""L62 remainder: real digest delivery and complete compiled instruction audiences."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

import pytest
import test_worktree_sync as sync_fixture
from agents_remember.application.agent_binding import AgentBinding
from agents_remember.application.role_capsules.compilation import (
    CapsuleCompilationOutcome,
    compile_admitted_capsule,
)
from agents_remember.application.role_launch_context import (
    RoleLaunchContext,
    resolve_role_launch_context,
)
from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.worktree_tools import worktree_status_tool
from agents_remember.cli import role_launch_routes, role_launch_workspace
from agents_remember.cli.paseo_role_tools import start_role
from agents_remember.cli.role_brief_message import SHARED_BLOCK_HEADING
from agents_remember.cli.role_instruction_details import compose_launch_instructions
from agents_remember.cli.role_launch_preparation import RoleHandoverRequest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.mcp.registration.knowledge import register_knowledge_tools
from agents_remember.models.role_agents import RoleStartCall
from agents_remember.models.role_capsules.types import (
    CapsuleCompilationResult,
    compute_content_digest,
)
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_intent import ApprovedRequirementPacketRef
from agents_remember.serving.capsule_delivery import capsule_delivery_from
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology, repository_master_documents
from agents_remember.worktrees.integration.integration_branch_authority import (
    integration_surfaces,
    integration_targets,
    require_sync_worktree,
)
from agents_remember.worktrees.modules.git import repository_identity
from agents_remember.worktrees.scheduling_mode import effective_execution_nature
from agents_remember.worktrees.source_lineage import source_lineage_for_contract
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import WorktreeContract, contract_publication_text
from agents_remember_test_support.testing.leaf_instruction_wording import (
    assert_action_pointer,
    assert_emitted_detail_links,
)
from lifecycle_enclosure_test_support import discard_test_enclosures, publish_test_enclosure
from mcp.server.fastmcp import FastMCP
from paseo_role_tools_test_support import RoleToolsTestCase
from role_first_message_support import FIXTURE_HARNESSES, FirstMessageMatrixBase
from test_role_capsule_admission import (
    LIFECYCLE_ROOT,
    MANIFEST_RELATIVE,
    _shipped_binding,
    _shipped_parsed,
    _shipped_request,
)
from test_worktree_sync import git
from test_worktree_sync_knowledge_merge import (
    APP_SIDECAR,
    Lines,
    _converted_lines,
    _edited,
    _sidecar,
)

KNOWLEDGE_SOURCE = "reference/knowledge/use.md"
KNOWLEDGE_STEPS = {
    "architect": (
        (
            "Before diagnosing, call `knowledge_read` on "
            "`agents-remember-task` for the files involved; read"
        ),
    ),
    "investigator": (
        (
            "Before investigating repository code, call `knowledge_read` "
            "on `agents-remember-task` for the files involved; read"
        ),
    ),
    "worker": (
        (
            "Before editing source, call `knowledge_read` on "
            '`agents-remember-task` with `view="source_context"` for each '
            "file you will edit; read"
        ),
        (
            "Before diagnosing a failing test, call `knowledge_read` on "
            '`agents-remember-task` with `view="source_context"` for the '
            "test file; read"
        ),
    ),
    "reviewer": (
        (
            "When inspecting changes, call `knowledge_read` on "
            "`agents-remember-task` for each changed file; read"
        ),
    ),
    "curator": (
        (
            "Before checking code and memory, call `knowledge_read` on "
            "`agents-remember-task` for the files or records you check; "
            "read"
        ),
        "Before interpreting a knowledge read, read",
    ),
}
ADVICE_RULE = (
    "Reviews, cold reads, re-checks, sweeps and Investigator reports are advice, not requirements. "
    "A finding that only corrects a wrong fact may be applied. A finding that adds, narrows or "
    "overrides a rule beyond the developer's words goes to the developer first, quoted beside "
    "the developer's earlier words."
)
REPORT_RULE = (
    "Report what is and what is not, with evidence for each. State no requirement, ruling or "
    "duty of your own. Name a needed change as an open question for the parent, not a fix to apply."
)


def _compiled_outcome(role: str, operation: str) -> CapsuleCompilationOutcome:
    outcome = compile_admitted_capsule(
        _shipped_binding(role, operation), _shipped_request(role, operation)
    )
    assert outcome.ok, outcome.render_explanation()
    assert outcome.result is not None
    return outcome


def _compiled(role: str, operation: str) -> CapsuleCompilationResult:
    outcome = _compiled_outcome(role, operation)
    assert outcome.result is not None
    return outcome.result


def _linked_instruction_source(
    text: str, action: str, source: str, fragment: str, heading: str
) -> str:
    """Follow one actual native link to its exact current packaged instruction owner."""
    matches = re.findall(re.escape(action) + r" \[[^]\n]+\]\(([^)\n]+)\)", text)
    assert len(matches) == 1, (action, "missing or repeated source link")
    parts = urlsplit(matches[0])
    assert not parts.scheme and not parts.netloc and not parts.query, matches[0]
    assert parts.fragment == fragment
    path = Path(unquote(parts.path))
    assert path.is_absolute(), path
    with shipped_composition_corpus() as (root, _manifest):
        assert path.resolve() == (root / source).resolve()
        actual = path.read_bytes()
        assert actual == (LIFECYCLE_ROOT / source).read_bytes()
    owner = actual.decode("utf-8")
    assert "\n## " + heading + "\n" in owner
    return owner


def _emitted_instruction_detail(
    result: CapsuleCompilationResult, source: str, report_path: Path
) -> tuple[str, str]:
    """Read the exact emitted detail from the same admitted capsule and native receipt."""
    [reference] = [item for item in result.capsule.detail_references if item.source_path == source]
    assert reference.content == (LIFECYCLE_ROOT / source).read_bytes()
    assert reference.revision == compute_content_digest(reference.content)
    delivery = capsule_delivery_from(result)
    assert delivery.trusted_instructions == result.render_instructions()
    instructions, record = compose_launch_instructions(delivery, str(report_path), {})
    assert record["baselineDigest"] == result.capsule.semantic_digest
    assert record["semanticDigest"] == result.capsule.semantic_digest
    assert record["baselineReplaced"] is False and record["userTextDigest"] is None
    [entry] = [item for item in record["detailReferences"] if item["sourcePath"] == source]
    assert entry["step"] == reference.step and reference.step.strip()
    path = Path(entry["path"])
    assert_emitted_detail_links(instructions, result.render_instructions(), source, path)
    assert "{{detail:" not in instructions
    content = path.read_bytes()
    assert entry["sizeBytes"] == len(content)
    assert entry["revision"] == compute_content_digest(content)
    return instructions, content.decode("utf-8")


def _prerequisite_curation_owner(
    result: CapsuleCompilationResult, rendered: str, report_path: Path
) -> str:
    """Follow the Manager's stage-start action, in its curation compile, to the stage owner.

    P5-18: ``roles/manager.md`` holds the one stage-start pointer in every Manager operation;
    the curation operation no longer repeats it for starting the Curator.
    """
    action = "Before starting and coordinating the leaf, read"
    pointer = action + (
        " [leaf-stage coordination]"
        "(../operations/coordination.md#start-leaf-stages-and-deliver-the-first-intake)"
    )
    assert "Before starting the Curator for ordinary delivery" not in rendered
    assert pointer in rendered
    with pytest.raises(AssertionError):
        assert pointer in rendered.replace(pointer, "", 1)
    instructions, _record = compose_launch_instructions(
        capsule_delivery_from(result), str(report_path), {}
    )
    owner = _linked_instruction_source(
        instructions,
        action,
        "operations/coordination.md",
        "start-leaf-stages-and-deliver-the-first-intake",
        "Start leaf stages and deliver the first intake",
    )
    return " ".join(owner.split())


class RealDigestFirstMessageTests(FirstMessageMatrixBase):
    def test_native_worker_architect_and_bootstrap_carry_the_real_digest_once(self) -> None:
        """No corpus or compiler patch: the host receives the shipped digest's exact bytes."""

        digest = (LIFECYCLE_ROOT / "core/every-role.md").read_text(encoding="utf-8")
        heading = f"## {SHARED_BLOCK_HEADING}\n\n"
        for role, kind in (
            ("worker", "task-bound"),
            ("architect", "taskless"),
            ("bootstrap", "taskless"),
        ):
            with self.subTest(role=role, kind=kind):
                request = self.request(role, FIXTURE_HARNESSES[0])
                self.assertEqual(request.task_document_ref is None, kind == "taskless")
                role_launch_routes._start_execution(self.config, request)
                receipt = self.receipt(request)
                message = self.sent_prompt(request)
                self.check_message(
                    message, receipt, role=role, harness=FIXTURE_HARNESSES[0], kind=kind
                )
                self.assertEqual(message.count(heading), 1)
                self.assertEqual(message.count(digest), 1)
                self.assertIn(f"{heading}{digest}\n\n# {role.title()}", message)
                self.release(receipt)


def test_knowledge_detail_reaches_its_step_in_every_declared_role_operation() -> None:
    """Every allowed pair is compiled; nonconsumers must carry neither pointer nor detail."""

    expected = (LIFECYCLE_ROOT / KNOWLEDGE_SOURCE).read_bytes()
    manifest = _shipped_parsed()
    checked: set[tuple[str, str]] = set()
    for role, entry in manifest.roles.items():
        for operation in entry.operations:
            result = _compiled(role, operation)
            details = {item.source_path: item for item in result.capsule.detail_references}
            rendered = result.render_instructions()
            pointer = "{{detail:" + KNOWLEDGE_SOURCE + "}}"
            assert (KNOWLEDGE_SOURCE in details) == (role in KNOWLEDGE_STEPS), (role, operation)
            assert (pointer in rendered) == (role in KNOWLEDGE_STEPS), (role, operation)
            if role in KNOWLEDGE_STEPS:
                assert details[KNOWLEDGE_SOURCE].content == expected, (role, operation)
                for action in KNOWLEDGE_STEPS[role]:
                    assert_action_pointer(rendered, KNOWLEDGE_SOURCE, action)
                    mutant = rendered.replace(action, "Read during intake", 1)
                    assert mutant != rendered
                    with pytest.raises(AssertionError):
                        assert_action_pointer(mutant, KNOWLEDGE_SOURCE, action)
                assert details[KNOWLEDGE_SOURCE].step.strip()
            checked.add((role, operation))
    assert checked == {
        (role, operation)
        for operation, entry in manifest.operations.items()
        for role in entry.applies_to_roles
    }


def _knowledge_read_instruction_arguments(text: str) -> set[str]:
    """Read request keywords from their owning section, not unrelated tools or result rows."""

    bindings = text.split("## Bind and complete the knowledge read\n", 1)[1].split("\n## ", 1)[0]
    # This exact clause selects a returned row; it does not pass a request argument.
    bindings = bindings.replace('named with `role="chain"`', "named with a chain result role", 1)
    words = set(re.findall(r"`([^`]+)`", bindings))
    nonarguments = {"knowledge_read", "proofs", "proves"}
    arguments = {word for word in words - nonarguments if word.isidentifier()}
    arguments.update(re.findall(r"\b([A-Za-z]\w*)=", bindings))
    return arguments


@pytest.mark.anyio
async def test_knowledge_detail_views_and_keywords_match_the_registered_tool(
    tmp_path: Path,
) -> None:
    """Check the mounted schema and advertised views, rather than another documentation list."""

    server = FastMCP("capsule-knowledge-schema")
    register_knowledge_tools(
        server,
        McpRuntimeConfig(
            config_path=tmp_path / "settings.json",
            workspace_root=tmp_path,
            coordination_root=tmp_path / "coordination",
            transcript_root=tmp_path / "transcripts",
        ),
    )
    tool = next(tool for tool in await server.list_tools() if tool.name == "knowledge_read")
    text = (LIFECYCLE_ROOT / KNOWLEDGE_SOURCE).read_text(encoding="utf-8")
    properties = set(tool.inputSchema["properties"])
    views = set(re.findall(r'view="([a-z_]+)"', text))
    assert views == {"source_context", "invariant", "family"}
    description = tool.description or ""
    advertised = description.split("Views:", 1)[1].split(".", 1)[0]
    assert views <= set(re.findall(r"[a-z_]+", advertised))
    for view in views:
        _content, answer = await server.call_tool(
            "knowledge_read", {"memoryRoot": str(tmp_path / "absent-memory"), "view": view}
        )
        payload = cast(dict[str, Any], answer)
        assert (payload["state"], payload["refusalCode"]) == (
            "refused",
            "selected_input_unavailable",
        ), (view, payload)
    assert not (tmp_path / "coordination").exists()
    arguments = _knowledge_read_instruction_arguments(text)
    assert arguments <= properties, arguments - properties
    for wrong in ("sourcePth", "role", "server_info"):
        damaged = text.replace("set `sourcePath`", f"set `{wrong}`", 1)
        assert damaged != text
        damaged_arguments = _knowledge_read_instruction_arguments(damaged)
        assert wrong in damaged_arguments
        with pytest.raises(AssertionError):
            assert damaged_arguments <= properties, damaged_arguments - properties
    assert {"memoryRoot", "repositoryRoot", "codeTreeId", "sourcePath", "continuation"} <= arguments
    for clause in (
        "Read every page through `continuation` until enumeration is complete",
        "rows name files, not their text",
        "exact inspected code tree",
        "A proof records what a test demonstrates, not that it passed",
        "Report a refusal, partial index or unverified currentness as a gap",
    ):
        assert clause in text


def test_d639_rules_reach_only_their_compiled_role_audience() -> None:
    """Advice authority and investigative reporting duties survive every applicable operation."""

    for role, entry in _shipped_parsed().roles.items():
        for operation in entry.operations:
            rendered = " ".join(_compiled(role, operation).render_instructions().split())
            for audience, sentence in (("architect", ADVICE_RULE), ("investigator", REPORT_RULE)):
                assert (sentence in rendered) == (role == audience), (role, operation, audience)


def test_worker_measurement_checkpoint_steps_reach_every_worker_operation() -> None:
    """Worker builds and forwards identities while Architect remains the measurement owner."""

    clauses = (
        "Before making a measurement-tooling handover, follow [Make the measurement handover]"
        "(../operations/implementation.md#make-the-measurement-handover) for the candidate and "
        "case-set binding and the Manager's response.",
    )
    for role, entry in _shipped_parsed().roles.items():
        for operation in entry.operations:
            rendered = " ".join(_compiled(role, operation).render_instructions().split())
            for clause in clauses:
                assert (clause in rendered) == (role == "worker"), (role, operation, clause)
            if role == "worker":
                assert "{{detail:reference/sandbox-actors.md}}" in rendered
                sandbox = next(
                    item
                    for item in _compiled(role, operation).capsule.detail_references
                    if item.source_path == "reference/sandbox-actors.md"
                )
                assert sandbox.content == (LIFECYCLE_ROOT / sandbox.source_path).read_bytes()
                for clause in (
                    "Before sandbox actors start, prove their tool-server binding with a stub",
                    "Count the host's agents before and after the first `role_start` on `agents-remember-task`.",
                ):
                    assert clause in sandbox.content.decode()
                    mutant = sandbox.content.decode().replace(clause, "", 1)
                    assert mutant != sandbox.content.decode()
                    with pytest.raises(AssertionError):
                        assert clause in mutant
    source = (LIFECYCLE_ROOT / "operations/implementation.md").read_text(encoding="utf-8")
    handover = source.split("### Make the measurement handover", 1)[1].split(
        "### Write the report", 1
    )[0]
    for clause in (
        "For measurement tooling, send the candidate and case-set identity to the Manager. "
        "Await the Manager's go on the same route.",
        "This simple handover is not a review freeze and starts no seat.",
    ):
        assert clause in " ".join(handover.split())
        mutant = handover.replace(clause, "", 1)
        assert mutant != handover
        with pytest.raises(AssertionError):
            assert clause in " ".join(mutant.split())


def test_digest_homes_declare_audiences_and_steps_without_injecting_the_map() -> None:
    """The digest and its audit map each have an exact path, audience and read step."""

    document = json.loads((LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_text(encoding="utf-8"))
    shared = document["core"]["every-role"]
    assert shared["source"] == "core/every-role.md"
    assert shared["roles"] == document["role_order"]
    assert shared["step"].strip()
    mapping = document["references"]["digest-map"]
    assert mapping["source"] == "core/digest-map.json"
    assert mapping["roles"] == document["role_order"]
    # P5-05: no text points at the digest map, so its step_detail:false entry declares no
    # step (test_manifest_steps_name_the_pointer_derived_actions fails on one declared).
    assert "step" not in mapping
    assert mapping["injected"] is False
    assert mapping["step_detail"] is False
    assert (LIFECYCLE_ROOT / mapping["source"]).is_file()
    assert mapping["source"] not in {item.source for item in _shipped_parsed().detail_references}
    assert not (LIFECYCLE_ROOT / "reference/role-capsules.md").exists()
    assert (LIFECYCLE_ROOT.parents[1] / "docs/reference/role-capsules.md").is_file()


@pytest.mark.usefixtures("worktree_services")
class PrerequisiteSyncCuratorTests(RoleToolsTestCase):
    """Native sync/start composition; the host and handover compiler remain fixture doubles."""

    def context(self, config: McpRuntimeConfig, selection: RoleSelection) -> RoleLaunchContext:
        return resolve_role_launch_context(config, selection)

    def topology(self, coordination_root: Path) -> TaskDocumentTopology:
        return TaskDocumentTopology(coordination_root)

    @staticmethod
    def reader_context(
        _config: McpRuntimeConfig, context: RoleLaunchContext, workspace: dict[str, str]
    ) -> dict[str, Any]:
        assert context.task is not None
        return {
            "scopeKind": "canonical-leaf",
            "taskContext": {
                "task_document_ref": context.task.ref.model_dump(mode="json"),
                "contract_path": workspace["contractPath"],
            },
        }

    def compile_handover(self, request: RoleHandoverRequest) -> dict[str, Any]:
        assert request.context.task is not None
        self.prepared_workspace = dict(request.workspace)
        self.prepared_task = request.context.task.ref
        return super().compile_handover(request)

    def _conflicted_lines(self) -> tuple[Lines, Path]:
        self.addCleanup(discard_test_enclosures)
        with patch.object(sync_fixture, "write_contract", self._publish_initial_contract):
            lines = _converted_lines(self.root / "prerequisite-sync", source_branch="super")
        contract = lines.fixture.contract
        self.assertEqual(contract.code_source_branch, "super")
        self.assertEqual(contract.memory_source_branch, "super")
        for repository in (lines.fixture.code_repo, lines.fixture.memory_repo):
            self.assertEqual(git(repository, "symbolic-ref", "--short", "HEAD"), "super")
            self.assertEqual(
                git(repository, "symbolic-ref", "refs/remotes/origin/HEAD"),
                "refs/remotes/origin/main",
            )
            self.assertNotEqual(
                git(repository, "rev-parse", "main"), git(repository, "rev-parse", "super")
            )
        base = _sidecar(lines.leaf)
        leaf_head = lines.on_leaf({APP_SIDECAR: _edited(base, "1", note="Leaf wording.")})
        line_head = lines.on_line({APP_SIDECAR: _edited(base, "1", note="Line wording.")})
        result = lines.merge()
        self.assertEqual(result["state"], "sync-resolution-required", result)
        resolution = result["resolution"]
        self.assertEqual(resolution["files"], [APP_SIDECAR])
        crossing = resolution["crossing"]
        self.assertEqual(
            [(item["path"], item["item"]) for item in crossing["conflicts"]],
            [(APP_SIDECAR, "references.1")],
        )
        report_path = Path(crossing["reportPath"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(
            (report["conflicts"][0]["own"]["note"], report["conflicts"][0]["incoming"]["note"]),
            ("Leaf wording.", "Line wording."),
        )
        self.assertEqual(set(_sidecar(lines.leaf)["references"]["1"]), {"crossing-conflict"})
        self.assertEqual(git(lines.leaf, "rev-parse", "MERGE_HEAD"), line_head)
        self.assertEqual(git(lines.leaf, "rev-parse", "HEAD"), leaf_head)
        return lines, report_path

    def _publish_initial_contract(self, path: Path, contract: WorktreeContract) -> None:
        self.assertEqual(path, contract.contract_path)
        self.assertFalse(path.exists())
        self._prepare_task(contract, None)
        publish_test_enclosure(contract, contract_publication_text(path, contract))
        self._assert_branch_authority(contract)

    def _assert_branch_authority(self, contract: WorktreeContract) -> None:
        topology = TaskDocumentTopology(contract.coordination_root)
        master_ref = topology.canonical_ref(contract.repo_name, contract.task_root / "task.json")
        sprint_ref = TaskDocumentRef(repository=contract.repo_name, path="fixture-sprint/task.json")
        leaf_ref = topology.canonical_ref(
            contract.repo_name, contract.task_root / "01_sync-thing.json"
        )
        self.assertEqual(topology.parent(leaf_ref), master_ref)
        self.assertEqual(topology.parent(master_ref), sprint_ref)
        self.assertEqual(
            {item.ref for item in repository_master_documents(topology, contract.repo_name)},
            {sprint_ref, master_ref},
        )
        self.assertEqual(
            [item.ref for item in topology.validate_execution_topology(sprint_ref)],
            [master_ref],
        )
        sprint = topology.resolve(sprint_ref).document
        master = topology.resolve(master_ref).document
        self.assertEqual(master.executionNature, "organizational")
        self.assertEqual(effective_execution_nature(master, sprint), "organizational")
        self.assertEqual(
            effective_execution_nature(master, sprint.model_copy(update={"executionGraph": None})),
            "atomic",
        )
        self.assertFalse(series_contract_path(contract.task_root).exists())
        self.assertIsNone(contract.parent_contract_path)
        assert contract.memory_repo_path is not None
        assert contract.memory_worktree is not None
        identities = {
            "code": repository_identity(contract.code_repo_path),
            "memory": repository_identity(contract.memory_repo_path),
        }
        self.assertNotIn(None, identities.values())
        self.assertNotEqual(identities["code"], identities["memory"])
        for side, repository, worktree in (
            ("code", contract.code_repo_path, contract.code_worktree),
            ("memory", contract.memory_repo_path, contract.memory_worktree),
        ):
            self.assertEqual(repository_identity(worktree), identities[side])
            self.assertEqual(git(repository, "symbolic-ref", "--short", "HEAD"), "super")
            self.assertEqual(
                git(repository, "symbolic-ref", "refs/remotes/origin/HEAD"),
                "refs/remotes/origin/main",
            )
            self.assertEqual(git(worktree, "symbolic-ref", "--short", "HEAD"), "ar/sync-thing")
        self.assertEqual(
            {
                (surface.side, surface.repository, surface.kind, surface.branch, surface.owner)
                for surface in integration_surfaces(contract)
            },
            {
                (side, identity, kind, branch, owner)
                for side, identity in identities.items()
                for kind, branch, owner in (
                    ("repository-default", "main", "repository default branch"),
                    ("sprint-super", "super", sprint_ref.key),
                )
            },
        )
        self.assertEqual(
            {
                (target.side, target.repository, target.kind, target.branch, target.owner)
                for target in integration_targets(contract)
            },
            {
                (side, identity, "sprint-super", "super", sprint_ref.key)
                for side, identity in identities.items()
            },
        )
        require_sync_worktree(contract)
        lineage = source_lineage_for_contract(contract)
        assert lineage is not None
        self.assertEqual(lineage.state, "current", lineage)
        self.assertEqual(
            {
                (
                    edge.side,
                    edge.relation,
                    edge.sourceBranch,
                    edge.descendantBranch,
                    edge.contractPath,
                )
                for edge in lineage.edges
            },
            {
                (side, "super-to-leaf", "super", "ar/sync-thing", str(contract.contract_path))
                for side in identities
            },
        )

    def _prepare_task(self, contract: WorktreeContract, report_path: Path | None) -> RoleStartCall:
        task_root = contract.task_root
        repo_tasks = contract.coordination_root / "tasks" / contract.repo_name
        paths = (
            repo_tasks / "fixture-sprint" / "task.json",
            task_root / "task.json",
            task_root / "01_sync-thing.json",
        )
        refs = tuple(
            TaskDocumentRef(
                repository=contract.repo_name, path=path.relative_to(repo_tasks).as_posix()
            )
            for path in paths
        )
        packet = task_root / "requirements" / "prerequisite-v1.md"
        packet.parent.mkdir(parents=True, exist_ok=True)
        packet.write_text(
            "# FIXTURE-R1 v1\n\nSynthetic approved-packet fixture input only; no human approval "
            "is asserted. Prerequisite sync must complete before implementation. The Curator "
            "owns its actual authored-memory reconciliation. The Manager starts that Curator. "
            "Ordinary delivery curation and knowledge check still follow code PASS.\n",
            encoding="utf-8",
        )
        common = {
            "repo": contract.repo_name,
            "createdAt": "2026-11-05T00:00:00+00:00",
            "status": "inProgress",
        }
        documents = (
            {
                **common,
                "id": "FIXTURE-SPRINT",
                "slug": "task",
                "title": "Fixture sprint",
                "kind": "master",
                "integrationBranch": contract.code_source_branch,
                "orchestrates": ["FIXTURE-MASTER"],
                "executionGraph": {
                    "nodes": [refs[1].model_dump(mode="json")],
                    "edges": [],
                },
            },
            {
                **common,
                "id": "FIXTURE-MASTER",
                "slug": "task",
                "title": "Fixture master",
                "kind": "master",
                "executionNature": "organizational",
                "subTasks": [
                    {
                        "number": contract.leaf_id,
                        "name": "Prerequisite sync",
                        "file": paths[2].with_suffix(".md").name,
                        "status": "inProgress",
                    }
                ],
            },
            {
                **common,
                "id": contract.leaf_id,
                "slug": paths[2].stem,
                "title": "Prerequisite memory reconciliation",
                "kind": "subTask",
                "master": "task.json",
                "objective": "Synthetic approved prerequisite sync with Curator-owned memory reconciliation.",
                "requirements": [
                    ApprovedRequirementPacketRef(
                        path="requirements/prerequisite-v1.md", stableId="FIXTURE-R1", version="v1"
                    )
                ],
                "enclosures": [
                    {"leafId": contract.leaf_id, "enclosurePath": str(contract.contract_path)}
                ],
                "references": [str(report_path)] if report_path is not None else [],
                "acceptance": "Start Curator for this actual conflict; no code-PASS verdict is asserted.",
            },
        )
        for path, document in zip(paths, documents, strict=True):
            write_task_doc(path.parent, TaskDocument.model_validate(document))
            self.assertTrue(path.is_file(), path)
        config_path = self.config.config_path
        host = self.config.paseo_runtime
        settings = json.loads(config_path.read_text(encoding="utf-8"))
        host_block = settings["paseoRuntime"]
        settings.update(
            {
                "coordinationRoot": str(contract.coordination_root),
                "workspaceRoot": str(contract.code_repo_path.parent),
                "repositories": {contract.repo_name: {"contractPath": str(contract.contract_path)}},
            }
        )
        config_path.write_text(json.dumps(settings) + "\n", encoding="utf-8")
        self.config = load_config(config_path)
        self.assertEqual(self.config, load_config(config_path))
        self.assertEqual(self.config.config_path, config_path)
        self.assertEqual(self.config.coordination_root, contract.coordination_root)
        self.assertEqual(self.config.workspace_root, contract.code_repo_path.parent)
        self.assertEqual(self.config.transcript_root, contract.coordination_root / "logs" / "mcp")
        self.assertEqual(set(self.config.repositories), {contract.repo_name})
        configured = self.config.repositories[contract.repo_name]
        self.assertEqual(configured.path, contract.code_repo_path)
        self.assertEqual(configured.memory_root, contract.memory_repo_path)
        self.assertEqual(configured.contract_path, contract.contract_path)
        self.assertEqual(
            repository_identity(configured.path), repository_identity(contract.code_repo_path)
        )
        assert configured.memory_root is not None
        assert contract.memory_repo_path is not None
        self.assertEqual(
            repository_identity(configured.memory_root),
            repository_identity(contract.memory_repo_path),
        )
        self.assertEqual(self.config.paseo_runtime, host)
        self.assertEqual(
            json.loads(config_path.read_text(encoding="utf-8"))["paseoRuntime"], host_block
        )
        self.replace(role_launch_workspace, "worktree_status_tool", worktree_status_tool)
        return RoleStartCall(
            role="curator",
            request_id=uuid.uuid4(),
            sprint_document_ref=refs[0],
            master_document_ref=refs[1],
            task_document_ref=refs[2],
        )

    def _assert_native_context(self, call: RoleStartCall, lines: Lines) -> None:
        contract = lines.fixture.contract
        selection = RoleSelection(
            role="curator",
            sprintDocumentRef=call.sprint_document_ref,
            masterDocumentRef=call.master_document_ref,
            taskDocumentRef=call.task_document_ref,
        )
        context = resolve_role_launch_context(self.config, selection)
        assert context.task is not None
        assert contract.memory_worktree is not None
        self.assertEqual(
            role_launch_workspace._leaf_contract_path(context.task), contract.contract_path
        )
        topology = TaskDocumentTopology(contract.coordination_root)
        assert call.task_document_ref is not None and call.master_document_ref is not None
        self.assertEqual(topology.parent(call.task_document_ref), call.master_document_ref)
        self.assertEqual(topology.parent(call.master_document_ref), call.sprint_document_ref)
        status = worktree_status_tool(
            self.config,
            TaskRef(repo_id=contract.repo_name, contract_path=str(contract.contract_path)),
        )
        self.assertTrue(status.get("ok"), status)
        for field, expected in (
            ("worktree_group", contract.worktree_group),
            ("code_worktree", contract.code_worktree),
            ("memory_worktree", contract.memory_worktree),
        ):
            self.assertEqual(Path(status[field]).resolve(), expected.resolve())

    @staticmethod
    def _snapshot(lines: Lines, report_path: Path) -> dict[str, object]:
        contract = lines.fixture.contract
        return {
            "codeHead": git(contract.code_worktree, "rev-parse", "HEAD"),
            "codeLine": git(lines.fixture.code_repo, "rev-parse", "main"),
            "codeSource": git(lines.fixture.code_repo, "rev-parse", lines.fixture.source_branch),
            "memoryHead": git(lines.leaf, "rev-parse", "HEAD"),
            "memoryLine": git(lines.line, "rev-parse", "main"),
            "memorySource": git(lines.line, "rev-parse", lines.fixture.source_branch),
            "mergeHead": git(lines.leaf, "rev-parse", "MERGE_HEAD"),
            "marker": (lines.leaf / APP_SIDECAR).read_bytes(),
            "report": report_path.read_bytes(),
            "journal": lines.journal(),
            "contract": contract.contract_path.read_bytes(),
        }

    def _assert_started(self, call: RoleStartCall, lines: Lines, report_path: Path) -> None:
        contract = lines.fixture.contract
        assert contract.memory_worktree is not None
        self.prompt = (
            "Synthetic fixture opening; compiler remains a double.\n"
            "Purpose: Curator-owned prerequisite-sync memory reconciliation.\n"
            f"Actual conflict report: {report_path}\nNo human approval or code PASS asserted.\n"
        )
        manager = AgentBinding(
            agent_id=str(uuid.uuid4()),
            role="manager",
            request_id=str(uuid.uuid4()),
            report_path=str(self.root / "fixture-manager-report.md"),
            sprint_ref=call.sprint_document_ref,
            master_ref=call.master_document_ref,
        )
        result = start_role(self.config, call, environment=manager.environment())
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["status"], "running")
        self.assertEqual(self.enclosures.start_calls, [])
        creates = [payload for command, payload in self.runtime.calls if command == "agent-create"]
        opens = [payload for command, payload in self.runtime.calls if command == "workspace-open"]
        self.assertEqual(len(creates), 1)
        self.assertEqual(len(opens), 1)
        self.assertEqual(Path(opens[0]["cwd"]).resolve(), contract.worktree_group.resolve())
        self.assertEqual(set(self.runtime.agents), {result["agentId"]})
        self.assertEqual(self.prepared_task, call.task_document_ref)
        for field, expected in (
            ("contractPath", contract.contract_path),
            ("codeRoot", contract.code_worktree),
            ("memoryRoot", contract.memory_worktree),
        ):
            self.assertEqual(Path(self.prepared_workspace[field]).resolve(), expected.resolve())
        self.assertIn(str(report_path), creates[0]["prompt"])

    def test_manager_starts_curator_for_actual_prerequisite_memory_reconciliation(self) -> None:
        """The documented exception reaches the exact conflicted enclosure without healing it."""
        clauses = (
            "When an approved packet's prerequisite sync reaches Curator-owned memory "
            "reconciliation, the Manager starts the Curator for that reconciliation.",
            "Ordinary delivery curation and the knowledge check still follow code `PASS`.",
        )
        for role, operation in (("manager", "coordination"), ("manager", "curation")):
            result = _compiled(role, operation)
            blocks = [
                block.content
                for block in result.capsule.instructions
                if block.source_path == f"operations/{operation}.md"
            ]
            self.assertEqual(len(blocks), 1)
            rendered = " ".join(blocks[0].split())
            if operation == "curation":
                rendered = _prerequisite_curation_owner(
                    result,
                    " ".join(result.render_instructions().split()),
                    self.root / "prerequisite-intake.md",
                )
            for clause in clauses:
                self.assertIn(clause, rendered)
                with self.assertRaises(AssertionError):
                    self.assertIn(clause, rendered.replace(clause, "", 1))
        lines, report_path = self._conflicted_lines()
        call = self._prepare_task(lines.fixture.contract, report_path)
        self._assert_native_context(call, lines)
        before = self._snapshot(lines, report_path)
        self._assert_started(call, lines, report_path)
        self.assertEqual(self._snapshot(lines, report_path), before)
