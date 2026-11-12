"""The capsule a role launch compiles for its seat, from the compiler every seat already uses.

A role launch asks :func:`compile_launch_capsule` for its instructions before it builds its launch
request, and the answer is one of two modes: the compiled capsule, or a named refusal. The cases
here run against a real coordination world (a task, its enclosure contract and its memory root)
and take the expected side from the compiler's own result for the same seat, never from a
hand-built literal:

* a task-attached seat's launch capsule is the compiler's own capsule for that seat;
* the mode gate answers ``capsule`` or ``refused`` for every seat class, and a refusal names the
  role;
* a free agent's admission names its absent task plane, and its capsule identity moves with the
  seat;
* the capsule path resolves a repository out of the enclosure contract.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import agents_remember.application.skill_resources.capsule as capsule_application
import pytest
from agents_remember.application.role_capsules.launch import (
    FREE_AGENT_TASK_REFERENCE_PREFIX,
    FreeAgentSeatAdmission,
    compile_launch_capsule,
    free_agent_seat_admission,
)
from agents_remember.application.skill_resources import (
    CapsuleCompileRequest,
    compile_task_capsule,
)
from agents_remember.application.skill_resources.responses import role_capsule_response
from agents_remember.application.task_projection import parse_task_reference
from agents_remember.errors import TaskProjectionSourceError
from agents_remember.kernel.coordination_context.models import EnclosureSelector
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp.registration.capsule_serving import register_capsule_and_skill_tools
from agents_remember.models.role_capsules.types import CapsuleDigest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.launch_capsule import LaunchCapsuleMode, LaunchCapsuleRequest
from agents_remember.tasks import (
    TaskDocument,
    write_task_doc,
)
from mcp.server.fastmcp import FastMCP
from test_worktree_support import write_current_task_lineage


def _write_leaf_task(
    coordination_root: Path,
    *,
    repo: str = "repo",
    master: str = "master",
    doc_id: str = "leaf-1",
    slug: str = "leaf-1",
) -> None:
    """Write the sprint, master and leaf task documents the scratch world's seat is admitted on."""
    write_task_doc(
        coordination_root / "tasks" / repo / "sprint",
        TaskDocument.model_validate(
            {
                "id": "SPRINT",
                "slug": "task",
                "title": "Sprint",
                "kind": "master",
                "repo": repo,
                "createdAt": "2026-07-07T09:59",
                "orchestrates": [master],
            }
        ),
    )
    task_root = coordination_root / "tasks" / repo / master
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": master.upper(),
                "slug": "task",
                "title": "Master",
                "kind": "master",
                "repo": repo,
                "createdAt": "2026-07-07T10:00",
                "subTasks": [
                    {
                        "number": doc_id,
                        "name": "Leaf",
                        "file": f"{slug}.md",
                        "status": "inProgress",
                    }
                ],
            }
        ),
    )
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": doc_id,
                "slug": slug,
                "title": "Leaf",
                "kind": "subTask",
                "repo": repo,
                "createdAt": "2026-07-07T10:01",
                "master": "task.md",
            }
        ),
    )


LEAF_REF = TaskDocumentRef(repository="repo", path="master/leaf-1.json")
MASTER = "master"
REPO = "repo"
LEAF_ID = "leaf-1"


@pytest.fixture(name="world")
def world_fixture(tmp_path: Path) -> tuple[Path, McpRuntimeConfig, Path]:
    """A real coordination world: its root, its configuration and its code repository."""

    return (tmp_path, *_scratch_world(tmp_path))


def _scratch_world(tmp_path: Path) -> tuple[McpRuntimeConfig, Path]:
    """A real coordination world: a task, its enclosure contract and its memory root.

    The coordination surface the compiler resolves through is the real one, so the world has to be
    complete: the enclosure lookup goes through the project's own memory resolution, which refuses
    when the repository's external memory root is absent.
    """

    memory_root = tmp_path / "memory-repos" / f"ar-{REPO}"
    (memory_root / "system").mkdir(parents=True, exist_ok=True)
    (memory_root / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory_root / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")
    _write_leaf_task(tmp_path)
    repo = write_current_task_lineage(tmp_path, repo_name=REPO, master_name=MASTER, leaf_id=LEAF_ID)
    config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path,
        workspace_root=tmp_path,
        transcript_root=tmp_path / "logs" / "mcp",
        repositories={REPO: RepositoryScope(repo_id=REPO, path=repo)},
    )
    return config, repo


def _tool_payload(result: object) -> dict[str, object]:
    """The JSON body of a FastMCP ``call_tool`` result, whichever shape the SDK returns it in."""

    if isinstance(result, dict):
        return cast(dict[str, object], result)
    if isinstance(result, tuple):
        _content, structured = result
        if isinstance(structured, dict):
            return cast(dict[str, object], structured)
        result = _content
    blocks = result if isinstance(result, list) else [result]
    for block in blocks:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, str):
            text = block
        if text:
            return cast(dict[str, object], json.loads(text))
    raise AssertionError(f"no JSON body in the tool result: {result!r}")


# --------------------------------------------------------------------------------------------
# The launch capsule: the compiler's own capsule for the seat, or a named refusal.
# --------------------------------------------------------------------------------------------


def test_a_task_attached_launch_carries_the_compilers_own_capsule_for_the_same_seat(
    world: tuple[Path, McpRuntimeConfig, Path],
) -> None:
    """A leaf seat's launch capsule is what the compiler answers for that seat, text and digest."""

    tmp_path, config, repo = world
    launched = compile_launch_capsule(
        config,
        LaunchCapsuleRequest(role="worker", workspace_root=tmp_path, task_document_ref=LEAF_REF),
    )
    assert launched.mode is LaunchCapsuleMode.CAPSULE, launched.explain()
    assert launched.delivery is not None

    # The expected side is the compiler's own result for the same admitted seat, read through the
    # same application entry point the MCP operation uses — never a hand-built literal.
    expected = compile_task_capsule(
        config,
        CapsuleCompileRequest(
            enclosure=EnclosureSelector(task_name=MASTER, leaf_id=LEAF_ID),
            task_path=LEAF_REF.path,
            operation="orientation",
            role="worker",
            code_repository_root=repo,
        ),
    )
    assert expected.ok, expected.explanation()
    assert expected.result is not None
    assert launched.delivery.trusted_instructions == expected.result.render_instructions()
    assert launched.delivery.semantic_digest == expected.result.capsule.semantic_digest


def test_the_mode_gate_names_capsule_or_refused_for_every_seat_class(
    world: tuple[Path, McpRuntimeConfig, Path],
) -> None:
    """One decision point with two answers: a capsule role compiles, every other seat refuses."""

    tmp_path, config, _repo = world
    assert [mode.value for mode in LaunchCapsuleMode] == ["capsule", "refused"]

    # A seat with no role, a seat that is no agent seat and a role the frozen vocabulary does not
    # admit are each a refusal that names the role; none of them launches without instructions.
    for role in (None, "chat", "terminal", "bogus"):
        refused = compile_launch_capsule(
            config, LaunchCapsuleRequest(role=role, workspace_root=tmp_path)
        )
        assert refused.mode is LaunchCapsuleMode.REFUSED, role
        assert refused.is_refusal, role
        assert refused.delivery is None, role
        assert refused.refusal_status == "role-not-capsule-addressable", role
        assert repr(role) in refused.refusal_detail, refused.refusal_detail
        assert refused.refusal_status in refused.explain(), refused.explain()

    admitted = compile_launch_capsule(
        config, LaunchCapsuleRequest(role="bootstrap", workspace_root=tmp_path)
    )
    assert admitted.mode is LaunchCapsuleMode.CAPSULE, admitted.explain()
    assert not admitted.is_refusal
    assert admitted.delivery is not None


# --------------------------------------------------------------------------------------------
# R125 evidence 5 replacement route: role-start's real compilation boundary (R112 ruling).
# --------------------------------------------------------------------------------------------


@pytest.fixture(name="launch_corpus")
def launch_corpus_fixture(
    world: tuple[Path, McpRuntimeConfig, Path], monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Change only a copied source corpus; admission, selection and compilation stay real."""
    tmp_path, _config, _repo = world
    shipped = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    corpus = tmp_path / "launch-corpus"
    shutil.copytree(shipped, corpus)

    @contextmanager
    def local_corpus() -> Iterator[tuple[Path, str]]:
        yield corpus, "composition-manifest.json"

    monkeypatch.setattr(capsule_application, "shipped_composition_corpus", local_corpus)
    return corpus


def test_role_start_replacement_route_names_an_unmarked_stage_part(
    world: tuple[Path, McpRuntimeConfig, Path], launch_corpus: Path
) -> None:
    tmp_path, config, _repo = world
    manifest = launch_corpus / "composition-manifest.json"
    document = json.loads(manifest.read_text())
    document["operations"]["implementation"]["audience"] = "sections"
    manifest.write_text(json.dumps(document))
    passage = "This stage passage has no audience."
    for header in ("", "---\nname: fixture\n---\n\n\n"):
        source = f"{header}{passage}\n<!-- ar:roles all -->\nShared boundary.\n"
        (launch_corpus / "operations/implementation.md").write_text(source)
        answer = compile_launch_capsule(
            config,
            LaunchCapsuleRequest(
                role="worker", workspace_root=tmp_path, task_document_ref=LEAF_REF
            ),
            operation="implementation",
        )
        assert answer.mode is LaunchCapsuleMode.REFUSED
        assert answer.delivery is None
        assert answer.refusal_status == "operation-audience-invalid"
        line = source.splitlines().index(passage) + 1
        assert f"operations/implementation.md:{line}" in answer.refusal_detail
        assert "stage text has no audience marking" in answer.refusal_detail


def test_role_start_replacement_route_names_a_role_the_stage_lacks(
    world: tuple[Path, McpRuntimeConfig, Path], launch_corpus: Path
) -> None:
    tmp_path, config, _repo = world
    assert (launch_corpus / "operations/curation.md").is_file()
    answer = compile_launch_capsule(
        config,
        LaunchCapsuleRequest(role="worker", workspace_root=tmp_path, task_document_ref=LEAF_REF),
        operation="curation",
    )
    assert answer.mode is LaunchCapsuleMode.REFUSED
    assert answer.delivery is None
    assert answer.refusal_status == "operation-not-applicable"
    assert "role 'worker'" in answer.refusal_detail
    assert "'curation'" in answer.refusal_detail


def test_role_start_replacement_route_names_a_missing_required_file(
    world: tuple[Path, McpRuntimeConfig, Path], launch_corpus: Path
) -> None:
    tmp_path, config, _repo = world
    (launch_corpus / "operations/implementation.md").unlink()
    answer = compile_launch_capsule(
        config,
        LaunchCapsuleRequest(role="worker", workspace_root=tmp_path, task_document_ref=LEAF_REF),
        operation="implementation",
    )
    assert answer.mode is LaunchCapsuleMode.REFUSED
    assert answer.delivery is None
    assert "operations/implementation.md" in answer.refusal_detail
    assert answer.refusal_status == "source-missing"


# --------------------------------------------------------------------------------------------
# The free agent's admission: a named absence, and an identity that moves with the seat.
# --------------------------------------------------------------------------------------------


def test_the_free_agent_admission_names_its_absent_task_plane(tmp_path: Path) -> None:
    """No task document, said out loud — and not a value the task layer would accept as one."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    admission = FreeAgentSeatAdmission(
        role="bootstrap",
        operation="orientation",
        altitude="free-agent",
        repository_id="workspace",
        work_branch="<unversioned>",
    )
    assert admission.task_reference == f"{FREE_AGENT_TASK_REFERENCE_PREFIX}:bootstrap"
    assert admission.is_taskless
    facts = admission.admitted_facts(tool_policy=None)
    assert facts.task_reference == "free-agent:bootstrap"
    # The task layer's own parser refuses it: this is not a task document reference, and nothing
    # that expects one may absorb it quietly.
    with pytest.raises(TaskProjectionSourceError) as refused:
        parse_task_reference(facts.task_reference)
    assert facts.task_reference in str(refused.value), (
        "the task layer must refuse the value BY NAME rather than absorb it as a document"
    )
    assert facts.task_document_digest.startswith("sha256:")


def test_the_free_agent_capsule_identity_moves_with_the_seat(tmp_path: Path) -> None:
    """Two different seats must not share an identity: the digest covers the admission."""

    other = tmp_path / "other-workspace"
    other.mkdir()
    config, repo = _scratch_world(tmp_path)

    def digest_for(role: str, workspace: Path) -> CapsuleDigest:
        answer = compile_launch_capsule(
            config, LaunchCapsuleRequest(role=role, workspace_root=workspace)
        )
        assert answer.delivery is not None, answer.explain()
        return cast(CapsuleDigest, answer.delivery.semantic_digest)

    bootstrap_here = digest_for("bootstrap", tmp_path)
    bootstrap_there = digest_for("bootstrap", other)
    assert bootstrap_here != bootstrap_there, "the seat's workspace must move the identity"
    assert repo  # the scratch repo exists; the free agent is not bound to it, and says so
    admission = free_agent_seat_admission(config, role="bootstrap", workspace_root=tmp_path)
    assert admission.repository_id == tmp_path.name
    assert admission.altitude == "free-agent", "the altitude is the corpus's own declaration"


# --------------------------------------------------------------------------------------------
# D13 — the registered MCP operation resolves a repository through its declared schema.
# --------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_registered_capsule_operation_resolves_a_repository_through_its_schema(
    world: tuple[Path, McpRuntimeConfig, Path],
) -> None:
    """D13: the capsule operation is no longer a registered tool; the path stays usable.

    MIK-R112 removed the ``role_capsule_compile`` tool; registration now serves only the
    skill resources. The repository is still resolved out of the enclosure contract by the
    application entry point role launch uses, which is what the second half pins.
    """

    tmp_path, config, _repo = world
    server = FastMCP("l15-d13")
    register_capsule_and_skill_tools(server, config)

    tools = await server.list_tools()
    assert all(tool.name != "role_capsule_compile" for tool in tools), [tool.name for tool in tools]

    contract = tmp_path / "tasks" / REPO / MASTER / "enclosures" / LEAF_ID / "series-contract.md"
    assert contract.is_file(), f"the scratch world must have a real enclosure: {contract}"

    # And the hand-built request object L4's own suite used still answers identically.
    direct = role_capsule_response(
        compile_task_capsule(
            config,
            CapsuleCompileRequest(
                enclosure=EnclosureSelector(contract_path=Path(contract)),
                task_path=LEAF_REF.path,
                role="worker",
                operation="orientation",
            ),
        )
    )
    assert direct.ok
    assert direct.instructions, "the capsule path returned no compiled instruction blocks"
