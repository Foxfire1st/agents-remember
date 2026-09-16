"""Focused cases for the eve capsule carrier and the launch binding that proves it.

Every expectation here has a source the code under test does not feed. The instruction text is
compared against the compilation result's own render and against the block text this fixture wrote
into its corpus; the workspace identity is compared against ``git`` itself; the carrier digest is
recomputed from the bytes on disk with :mod:`hashlib`; the task facts are compared against the
projection the fixture's own task document produces. A case whose two sides both came from the
carrier would prove nothing, and the runtime cases that execute the TypeScript half live in
``test_eve_capsule_runtime.py`` because they need a real Node.

The whole point of the seam is that a launch cannot run without an admitted capsule in the admitted
worktree, so most of these cases assert a *refusal* and the defect each refusal names.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application import eve_capsule as eve_capsule_module
from agents_remember.application.eve_capsule import (
    CARRIER_FILENAME,
    CURATOR_WRITE_SURFACES,
    WORKER_WRITE_SURFACES,
    build_carrier,
    carrier_env,
    carrier_seat,
    write_scopes_for,
)
from agents_remember.application.task_projection import (
    ProjectionScopeRequest,
    TaskProjectionRequest,
    project_task_context,
    resolve_task_projection_scope,
    task_context_of,
)
from agents_remember.errors import HarnessControlError
from agents_remember.kernel.coordination_context.models import EnclosureSelector
from agents_remember.models.eve_capsule_carrier import (
    BINDING_REF_ENV,
    CAPSULE_DIGEST_ENV,
    CAPSULE_PATH_ENV,
    EVE_CAPSULE_CARRIER_SCHEMA,
    WORKSPACE_ROOT_ENV,
    EveCapsuleCarrier,
    carrier_digest,
    instruction_digest,
)
from agents_remember.serving.eve_runtime_launch import (
    EveWorkspaceBinding,
    verify_capsule_binding,
)
from eve_capsule_test_support import REPOSITORY, FixtureWorld, build_world, run_git


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> FixtureWorld:
    return build_world(tmp_path_factory.mktemp("l7-world"))


def _carrier_json(world: FixtureWorld) -> dict[str, object]:
    launch = world.bind()
    return json.loads(launch.carrier_path.read_text(encoding="utf-8"))


def test_carrier_carries_the_compiled_capsule_verbatim(world: FixtureWorld) -> None:
    """The carrier's payload is the compilation's own output, not a re-render of it."""

    launch = world.bind()
    result = launch.compilation.result
    assert result is not None
    carrier = launch.carrier

    assert carrier.instruction_text == result.render_instructions()
    assert carrier.task_context_markdown == result.render_task_context()
    assert list(carrier.instructions) == [block.content for block in result.capsule.instructions]
    assert carrier.identity.semantic_digest == result.semantic_digest
    assert carrier.identity.role == "worker"
    assert carrier.identity.operation == "implementation"
    assert carrier.identity.task_reference.startswith(f"{REPOSITORY}/")
    # The corpus this fixture authored, matched by content rather than by position.
    assert "AUTHORITY RULE authored by the L7 fixture." in carrier.instruction_text
    assert "WORKER SEAT RULE authored by the L7 fixture." in carrier.instruction_text


def test_carrier_identities_and_digests_match_the_compilation_manifest(world: FixtureWorld) -> None:
    """Block identity and content address come from the manifest and from hashlib, not the carrier."""

    launch = world.bind()
    result = launch.compilation.result
    assert result is not None
    carrier = launch.carrier

    assert carrier.instruction_identities == result.manifest.instruction_identities
    assert carrier.instruction_digests == tuple(
        f"sha256:{hashlib.sha256(block.content.encode('utf-8')).hexdigest()}"
        for block in result.capsule.instructions
    )
    assert carrier.instruction_digests == tuple(
        instruction_digest(block.content) for block in result.capsule.instructions
    )
    assert carrier.carry_forward == tuple(
        f"{unit.block.identity}@{unit.block.revision}" for unit in result.capsule.instruction_units
    )


def test_carrier_binds_the_admitted_worktree_git_identity(world: FixtureWorld) -> None:
    """The workspace half is git's own answer for the admitted worktree, branch and revision."""

    launch = world.bind()
    carrier = launch.carrier

    assert carrier.workspace.root == str(world.code_worktree.resolve())
    assert carrier.workspace.repository_id == REPOSITORY
    assert carrier.workspace.work_branch == world.work_branch
    assert carrier.workspace.base_commit == run_git(world.code_worktree, "rev-parse", "HEAD")
    assert run_git(world.code_worktree, "rev-parse", "--abbrev-ref", "HEAD") == world.work_branch
    assert carrier.workspace.contract_path == str(world.contract.resolve())


def test_carrier_digest_addresses_the_exact_bytes_on_disk(world: FixtureWorld) -> None:
    """The declared digest covers the carrier's real bytes, and an edit is visible."""

    launch = world.bind()
    payload = launch.carrier_path.read_bytes()

    assert launch.digest == f"sha256:{hashlib.sha256(payload).hexdigest()}"
    assert launch.digest == carrier_digest(payload)
    assert EveCapsuleCarrier.from_bytes(payload) == launch.carrier

    tampered = payload.replace(b'"role": "worker"', b'"role": "curator"')
    assert tampered != payload
    assert carrier_digest(tampered) != launch.digest
    assert EveCapsuleCarrier.from_bytes(tampered) != launch.carrier


def test_carrier_env_names_exactly_the_declared_binding(world: FixtureWorld) -> None:
    """One launch environment, four values, each the verified carrier's own."""

    launch = world.bind()
    env = launch.env
    assert set(env) == {BINDING_REF_ENV, CAPSULE_PATH_ENV, CAPSULE_DIGEST_ENV, WORKSPACE_ROOT_ENV}
    assert env[BINDING_REF_ENV] == launch.carrier.identity.binding_ref
    assert env[CAPSULE_PATH_ENV] == str(launch.carrier_path)
    assert env[CAPSULE_DIGEST_ENV] == launch.digest
    assert env[WORKSPACE_ROOT_ENV] == str(world.code_worktree.resolve())
    assert (
        carrier_env(
            binding_ref="ar-binding:worker:x:implementation",
            capsule_path=Path("/tmp/carrier.json"),
            capsule_digest="sha256:" + "0" * 64,
            workspace_root=Path("/tmp/workspace"),
        )[BINDING_REF_ENV]
        == "ar-binding:worker:x:implementation"
    )


def test_carrier_refuses_a_degenerate_instruction_set(world: FixtureWorld) -> None:
    """An empty or internally inconsistent instruction set cannot be read as a bound seat."""

    payload = _carrier_json(world)
    empty = {**payload, "instructions": [], "instructionIdentities": [], "instructionDigests": []}
    with pytest.raises(HarnessControlError, match="no instruction block"):
        EveCapsuleCarrier.from_bytes(json.dumps(empty).encode())

    short = dict(payload)
    short["instructionDigests"] = list(payload["instructionDigests"])[:-1]  # type: ignore[arg-type]
    with pytest.raises(HarnessControlError, match="one to one"):
        EveCapsuleCarrier.from_bytes(json.dumps(short).encode())

    wrong_schema = {**payload, "schema": "ar-eve-capsule-carrier/v2"}
    with pytest.raises(HarnessControlError, match="schema"):
        EveCapsuleCarrier.from_bytes(json.dumps(wrong_schema).encode())


def test_carrier_refuses_a_workspace_scope_that_is_not_its_workspace(world: FixtureWorld) -> None:
    """Write confinement and execution cannot name two different roots."""

    payload = _carrier_json(world)
    scopes = list(payload["writeScopes"])  # type: ignore[arg-type]
    scopes[0] = {**scopes[0], "root": "/tmp/somewhere-else"}
    with pytest.raises(HarnessControlError, match="workspace scope root"):
        EveCapsuleCarrier.from_bytes(json.dumps({**payload, "writeScopes": scopes}).encode())

    doubled = [*scopes, scopes[0]]
    with pytest.raises(HarnessControlError, match="exactly one workspace scope"):
        EveCapsuleCarrier.from_bytes(json.dumps({**payload, "writeScopes": doubled}).encode())

    with pytest.raises(HarnessControlError, match="unknown kind"):
        EveCapsuleCarrier.from_bytes(
            json.dumps({**payload, "writeScopes": [{**scopes[0], "kind": "sandbox"}]}).encode()
        )


def test_role_write_surfaces_follow_the_role_authority_table(world: FixtureWorld) -> None:
    """A worker cannot inherit the curator's memory surface, and an unknown role gets the least."""

    memory_surface = world.memory_worktree
    worker = write_scopes_for(
        "worker",
        workspace_root=world.code_worktree,
        report_root=world.report_root,
        memory_root=memory_surface,
    )
    assert [scope.kind for scope in worker] == ["workspace", "absolute"]
    assert [scope.root for scope in worker][1] == str(world.report_root.resolve())
    assert str(memory_surface.resolve()) not in [scope.root for scope in worker]

    curator = write_scopes_for(
        "curator",
        workspace_root=world.code_worktree,
        report_root=world.report_root,
        memory_root=memory_surface,
    )
    assert [scope.root for scope in curator][2] == str(memory_surface.resolve())

    unknown = write_scopes_for(
        "astronaut",
        workspace_root=world.code_worktree,
        report_root=world.report_root,
        memory_root=memory_surface,
    )
    assert [scope.root for scope in unknown] == [
        str(world.code_worktree.resolve()),
        str(world.report_root.resolve()),
    ]
    assert WORKER_WRITE_SURFACES < CURATOR_WRITE_SURFACES


def test_materialize_refuses_an_unadmitted_surface_root(world: FixtureWorld) -> None:
    """A surface the role's table names but nobody admitted stops the carrier being written."""

    with pytest.raises(HarnessControlError, match="absolute path"):
        world.bind(report_root=Path("notes/reports"))
    with pytest.raises(HarnessControlError, match="not an existing directory"):
        world.bind(report_root=world.root / "absent-reports")


def test_carrier_seat_builds_the_canonical_binding_reference(world: FixtureWorld) -> None:
    """The binding reference is the seat, spelled the same way on every path that builds one."""

    launch = world.bind()
    seat = carrier_seat(
        role="worker",
        task_path="SPRINT/MASTER/ALPHA.json",
        operation="implementation",
    )
    assert seat.binding_ref == "ar-binding:worker:SPRINT/MASTER/ALPHA.json:implementation"
    assert launch.carrier.identity.binding_ref == seat.binding_ref
    with pytest.raises(HarnessControlError, match="separator"):
        carrier_seat(role="worker:curator", task_path="x", operation="implementation")


def test_carrier_task_context_is_the_admitted_projection(world: FixtureWorld) -> None:
    """The task facts the runtime applies are L3's projection, byte for byte and digest for digest."""

    launch = world.bind()
    result = launch.compilation.result
    assert result is not None
    binding = result.capsule.binding
    scope = resolve_task_projection_scope(
        binding,
        coordination_root=world.coordination_root,
        scope_request=ProjectionScopeRequest(
            selector=EnclosureSelector(contract_path=world.contract),
            workspace_root=world.root,
            code_repository_root=world.repository,
        ),
    )
    projected = task_context_of(project_task_context(scope, binding, TaskProjectionRequest()))
    assert launch.carrier.task_context_markdown == projected.markdown
    assert launch.carrier.task_context_digest == projected.content_digest
    assert launch.carrier.task_context_digest == (
        f"sha256:{hashlib.sha256(projected.markdown.encode('utf-8')).hexdigest()}"
    )
    assert "ALPHA objective." in launch.carrier.task_context_markdown


def test_materialize_refuses_a_projection_that_disagrees_with_the_capsule(
    world: FixtureWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The re-derived projection is compared, not assumed: a disagreement stops the carrier."""

    original = eve_capsule_module.task_context_of

    def mismatched(projection: object):  # type: ignore[no-untyped-def]
        return replace(original(projection), content_digest="sha256:" + "1" * 64)  # type: ignore[arg-type]

    monkeypatch.setattr(eve_capsule_module, "task_context_of", mismatched)
    with pytest.raises(HarnessControlError, match="not the admitted projection's"):
        world.bind()


def test_building_a_carrier_by_hand_keeps_the_workspace_scopes_consistent(
    world: FixtureWorld,
) -> None:
    """A hand-built carrier is refused when its declared surfaces do not match its workspace."""

    launch = world.bind()
    result = launch.compilation.result
    assert result is not None
    scope = resolve_task_projection_scope(
        result.capsule.binding,
        coordination_root=world.coordination_root,
        scope_request=ProjectionScopeRequest(
            selector=EnclosureSelector(contract_path=world.contract),
            workspace_root=world.root,
            code_repository_root=world.repository,
        ),
    )
    projection = project_task_context(scope, result.capsule.binding, TaskProjectionRequest())
    seat = carrier_seat(
        role="worker",
        task_path="SPRINT/MASTER/ALPHA.json",
        operation="implementation",
        report_root=world.report_root,
    )
    carrier = build_carrier(result=result, projection=projection, seat=seat)
    assert carrier.workspace_scope is not None
    assert carrier.workspace_scope.root == carrier.workspace.root
    assert carrier.write_scopes[-1].root == str(world.report_root.resolve())


def test_materialize_writes_only_the_carrier_file_it_declares(world: FixtureWorld) -> None:
    """The produce side writes inside its own epoch directory and leaves the worktree alone."""

    epoch = world.root / "epoch-check"
    before = run_git(world.code_worktree, "status", "--porcelain")
    launch = world.bind(carrier_directory=epoch)
    written = sorted(
        path.relative_to(epoch).as_posix() for path in epoch.rglob("*") if path.is_file()
    )
    assert written == [f"capsule/{CARRIER_FILENAME}"]
    assert launch.carrier_path == epoch / "capsule" / CARRIER_FILENAME
    assert run_git(world.code_worktree, "status", "--porcelain") == before
    assert EVE_CAPSULE_CARRIER_SCHEMA in launch.carrier_path.read_text(encoding="utf-8")


def _launch_binding(world: FixtureWorld, **overrides: object) -> EveWorkspaceBinding:
    """The launch binding one admitted capsule implies, with fields a case can replace."""

    launch = world.bind()
    binding = EveWorkspaceBinding(
        workspace_root=world.code_worktree,
        binding_ref=launch.carrier.identity.binding_ref,
        capsule_digest=launch.digest,
        capsule_path=launch.carrier_path,
    )
    return replace(binding, **overrides) if overrides else binding


def test_launch_verification_accepts_the_admitted_capsule(world: FixtureWorld) -> None:
    """The launch path returns the verified carrier, which is what a caller reports as applied."""

    carrier = verify_capsule_binding(_launch_binding(world))
    assert carrier is not None
    assert carrier.workspace.root == str(world.code_worktree.resolve())
    assert carrier.instruction_text


def test_launch_verification_refuses_every_declared_defect(
    world: FixtureWorld, tmp_path: Path
) -> None:
    """Six defects, each named, each refused before a process could exist."""

    launch = world.bind()
    tampered = tmp_path / "tampered.json"
    tampered.write_bytes(launch.carrier_path.read_bytes() + b"\n")
    sibling = world.sibling_worktree
    cases = {
        "partial binding": _launch_binding(world, capsule_digest=None),
        "missing carrier": _launch_binding(world, capsule_path=tmp_path / "absent.json"),
        "digest mismatch": _launch_binding(world, capsule_path=tampered),
        "foreign binding": _launch_binding(world, binding_ref="ar-binding:curator:other:x"),
        "foreign workspace": _launch_binding(world, workspace_root=sibling),
    }
    refusals = {}
    for label, binding in cases.items():
        try:
            verify_capsule_binding(binding)
        except HarnessControlError as error:
            refusals[label] = str(error)
    assert set(refusals) == set(cases), f"these were accepted: {sorted(set(cases) - set(refusals))}"
    assert "names the binding reference" in refusals["partial binding"]
    assert "could not be read" in refusals["missing carrier"]
    assert "not the declared" in refusals["digest mismatch"]
    assert "belongs to binding" in refusals["foreign binding"]
    assert "refusing to execute a capsule outside the worktree" in refusals["foreign workspace"]
    # A launch that declares no binding at all stays unbound here: refusing it is the runtime's job,
    # where the absence of a capsule would otherwise leave a session running on static instructions.
    assert verify_capsule_binding(EveWorkspaceBinding(workspace_root=world.code_worktree)) is None


def test_launch_verification_refuses_a_workspace_on_another_branch(
    world: FixtureWorld, tmp_path: Path
) -> None:
    """A directory that exists at the admitted path is not enough: git has to agree."""

    launch = world.bind()
    payload = json.loads(launch.carrier_path.read_text(encoding="utf-8"))
    payload["workspace"]["root"] = str(world.sibling_worktree.resolve())
    for scope in payload["writeScopes"]:
        if scope["kind"] == "workspace":
            scope["root"] = str(world.sibling_worktree.resolve())
    body = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    moved = tmp_path / "moved.json"
    moved.write_bytes(body)
    binding = _launch_binding(
        world,
        workspace_root=world.sibling_worktree,
        capsule_path=moved,
        capsule_digest=f"sha256:{hashlib.sha256(body).hexdigest()}",
    )
    with pytest.raises(HarnessControlError, match="not the admitted work branch"):
        verify_capsule_binding(binding)
