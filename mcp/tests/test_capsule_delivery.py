"""How an admitted capsule becomes the value a role launch carries to its agent.

Every expected value in this module is an independent fixture: the capsule shapes are built here
as literals against the compiler's published frozen DTO, and the conversion is compared to a
genuine compilation of the shipped corpus. Nothing imports its expectation from the module under
test.

The cases are organized by the boundary they protect: what the value refuses to be built from,
and how the compiler's own result (role seat and launcher seat) maps onto the value's binding.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.role_capsules.sources import CapsuleAdmissionRequest
from agents_remember.models.role_capsules.manifest import parse_composition_manifest
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleCapsule,
    CapsuleCompilationResult,
    CapsuleLauncherSeat,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.serving.capsule_delivery import (
    LAUNCHER_ROLE_SENTINEL,
    CapsuleBindingIdentity,
    CapsuleDelivery,
    capsule_delivery_from,
)

# Independent expectations. The trusted stream is a literal with a token no other artifact contains.
TRUSTED_INSTRUCTIONS = (
    "# Role — worker\n\n"
    "You are the WORKER for leaf `L5`. TRUSTED-STREAM-TOKEN-4c17\n"
    "Never commit; the owning seat commits.\n"
)
BINDING = CapsuleBindingIdentity(
    repository_id="agents-remember",
    task_path="260915_role-capsules-and-native-eve/05_codex-capsule-delivery.json",
    role="worker",
    operation="implementation",
)


def delivery(
    *,
    instructions: str = TRUSTED_INSTRUCTIONS,
    digest: str = "sha256:" + "1f" * 32,
) -> CapsuleDelivery:
    return CapsuleDelivery(
        binding=BINDING,
        trusted_instructions=instructions,
        semantic_digest=digest,
    )


def test_task_and_skill_content_cannot_enter_the_instruction_slot() -> None:
    """The value type refuses an instruction parameter smuggled through the task channel."""

    # The admitted detail channel is separate from instructions; task text still has no field.
    assert set(CapsuleDelivery.__dataclass_fields__) == {
        "binding",
        "trusted_instructions",
        "semantic_digest",
        "detail_references",
    }
    assert delivery().detail_references == ()

    with pytest.raises(Exception, match="non-empty trusted instruction stream"):
        delivery(instructions="   ")

    with pytest.raises(Exception, match="semantic digest"):
        delivery(digest="not-a-digest")

    for field in ("repository_id", "task_path", "role", "operation"):
        with pytest.raises(Exception, match=f"binding {field}"):
            CapsuleBindingIdentity(
                repository_id="repo" if field != "repository_id" else "",
                task_path="task.json" if field != "task_path" else " ",
                role="worker" if field != "role" else "",
                operation="implementation" if field != "operation" else " ",
            )


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE_ROOT = REPOSITORY_ROOT / "skills" / "l-01-agent-lifecycles"
MANIFEST_RELATIVE = "composition-manifest.json"


def _shipped_manifest():
    return parse_composition_manifest((LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes())


def real_compilation(role: str = "worker", operation: str = "implementation"):
    """A GENUINE ``CapsuleCompilationResult`` from the public frozen compiler path.

    Nothing here is a stand-in: the binding is a real ``CapsuleBinding`` over real
    ``CapsuleAdmittedFacts``, the source set is the shipped corpus, and the result comes from
    ``compile_admitted_capsule``. This is the input shape the seam must consume.
    """

    parsed = _shipped_manifest()
    entry = parsed.roles[role]
    request = CapsuleAdmissionRequest(
        root=LIFECYCLE_ROOT,
        manifest=MANIFEST_RELATIVE,
        core=tuple(parsed.core[name].source for name in entry.core),
        role=(entry.file,),
        operation=(parsed.operations[operation].source,),
        skill=tuple(
            parsed.skills[name].source
            for name in getattr(entry, "skills", ())
            if name in parsed.skills
        ),
    )
    binding = CapsuleBinding(
        operation=operation,  # type: ignore[arg-type]
        admitted=CapsuleAdmittedFacts(
            task_reference="agents-remember/tasks/demo/task.json",
            task_document_digest=compute_content_digest(b'{"id": "demo"}'),
            seat=CapsuleRoleSeat(role=role, altitude=entry.altitude),  # type: ignore[arg-type]
            repository_id="agents-remember",
            work_branch="ar/260915-caps-l5-ar",
            # The admitted snapshot must cover what the composed blocks request; a role's own
            # declared tools are the honest source for that, exactly as the corpus declares them.
            tool_policy=CapsuleToolPolicy(
                granted=frozenset(tool for entry in parsed.roles.values() for tool in entry.tools)
            ),
        ),
    )
    outcome = compile_admitted_capsule(binding, request)
    assert outcome.ok, f"the shipped {role}/{operation} capsule failed to compile: {outcome.error}"
    assert outcome.result is not None
    return outcome.result


def test_the_frozen_shapes_this_conversion_depends_on() -> None:
    """The DTO field names are asserted against L2's real types, not assumed.

    If L2 ever moves ``admitted`` or renames ``task_reference``, this fails first and names the
    shape, instead of the conversion silently reading a missing attribute.
    """

    binding_fields = set(CapsuleBinding.__dataclass_fields__)
    assert {"operation", "admitted"} <= binding_fields, binding_fields
    admitted = CapsuleBinding.__dataclass_fields__["admitted"].type
    assert "CapsuleAdmittedFacts" in str(admitted), admitted
    assert "render_instructions" in vars(CapsuleCompilationResult)
    assert "binding" in CapsuleCapsule.__dataclass_fields__


def test_delivery_consumes_a_genuine_compilation_result() -> None:
    """L5R-1: the seam consumes the real frozen compilation, not a mirror of its own guesses."""

    compilation = real_compilation("worker", "implementation")
    result = capsule_delivery_from(compilation)

    admitted = compilation.capsule.binding.admitted
    assert result.binding.repository_id == admitted.repository_id
    assert result.binding.task_path == admitted.task_reference
    assert result.binding.role == admitted.seat.role
    assert result.binding.operation == compilation.capsule.binding.operation
    assert result.trusted_instructions == compilation.render_instructions()
    assert result.trusted_instructions.strip(), "the real capsule must render actual instructions"
    assert result.semantic_digest == compilation.semantic_digest
    # The binding a launch publishes is exactly these four admitted facts, under these keys.
    assert result.binding.as_report() == {
        "repositoryId": admitted.repository_id,
        "taskPath": admitted.task_reference,
        "role": admitted.seat.role,
        "operation": compilation.capsule.binding.operation,
    }
    # The old, wrong shape is not silently accepted: the stand-in field names must not exist.
    for legacy in ("repository_id", "task_path", "role"):
        assert not hasattr(compilation.capsule.binding, legacy), legacy


def test_a_launcher_seat_reports_its_sentinel_role() -> None:
    """The launcher seat has no role but still gets a capsule, so the role slot stays non-empty."""

    seat = CapsuleLauncherSeat(routing_condition="ambient-launcher")
    assert seat.role is None, "the launcher really does expose no role"
    assert seat.block_identity is None
    assert seat.key.startswith("launcher:")


def test_a_seat_the_conversion_does_not_recognise_is_refused() -> None:
    """An unknown seat kind refuses loudly rather than inventing a role."""

    class _UnknownSeat:
        kind = "wizard"

    class _Capsule:
        binding = type(
            "B",
            (),
            {
                "operation": "implementation",
                "admitted": type(
                    "A",
                    (),
                    {
                        "task_reference": "t.json",
                        "repository_id": "repo",
                        "seat": _UnknownSeat(),
                    },
                )(),
            },
        )()

    class _Result:
        capsule = _Capsule()

        def render_instructions(self) -> str:
            return "instructions"

    with pytest.raises(Exception, match="unrecognised seat kind"):
        capsule_delivery_from(_Result())


def _admitted_facts():
    """The real admitted-facts construction shared by the launcher and role assertions."""

    return {
        "task_reference": "agents-remember/tasks/demo/task.json",
        "task_document_digest": compute_content_digest(b'{"id": "demo"}'),
        "repository_id": "agents-remember",
        "work_branch": "ar/260915-caps-l5-ar",
        "tool_policy": CapsuleToolPolicy(granted=frozenset()),
    }, CapsuleAdmittedFacts


def test_a_real_launcher_compilation_reports_the_sentinel_role() -> None:
    """A genuine launcher binding converts, reporting the sentinel instead of an empty role."""

    kwargs, facts_type = _admitted_facts()
    binding = CapsuleBinding(
        operation="orientation",
        admitted=facts_type(
            seat=CapsuleLauncherSeat(routing_condition="ambient-launcher"), **kwargs
        ),
    )

    class _Result:
        capsule = type("C", (), {"binding": binding, "semantic_digest": "sha256:" + "0a" * 32})()

        def render_instructions(self) -> str:
            return "# Launcher\n"

    result = capsule_delivery_from(_Result())
    assert result.binding.role == LAUNCHER_ROLE_SENTINEL
    assert result.binding.operation == "orientation"
