"""How an admitted capsule becomes the value a role launch carries to its agent.

Every expected value in this module is an independent fixture: the capsule shapes are built here
as literals against the compiler's published frozen DTO, and the conversion is compared to a
genuine compilation of the shipped corpus. Nothing imports its expectation from the module under
test.

The cases are organized by the boundary they protect: what the value refuses to be built from,
and how the compiler's own result (role seat and launcher seat) maps onto the value's binding.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.role_capsules.sources import CapsuleAdmissionRequest
from agents_remember.cli import role_instruction_details
from agents_remember.cli.role_instruction_details import compose_launch_instructions
from agents_remember.cli.role_instruction_links import bind_markdown_links
from agents_remember.kernel.onboarding_doc import unfenced_lines
from agents_remember.models.role_capsules.manifest import parse_composition_manifest
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleCapsule,
    CapsuleCompilationResult,
    CapsuleDetailReference,
    CapsuleInstructionBlock,
    CapsuleInstructionUnit,
    CapsuleLauncherSeat,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.serving.capsule_delivery import (
    LAUNCHER_ROLE_SENTINEL,
    CapsuleBindingIdentity,
    CapsuleDelivery,
    CapsuleInstructionOrigin,
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
        "instruction_origins",
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
        )
        + tuple(detail.source for detail in parsed.detail_references if role in detail.roles),
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
    assert tuple(origin.source_path for origin in result.instruction_origins) == tuple(
        unit.block.source_path for unit in compilation.capsule.instruction_units
    )
    for origin, unit in zip(
        result.instruction_origins, compilation.capsule.instruction_units, strict=True
    ):
        assert result.trusted_instructions[origin.start : origin.end] == unit.block.content
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
        capsule = type(
            "C",
            (),
            {
                "binding": binding,
                "semantic_digest": "sha256:" + "0a" * 32,
                "instruction_units": (
                    CapsuleInstructionUnit(
                        0,
                        CapsuleInstructionBlock(
                            "launcher:ambient-launcher",
                            "core",
                            (),
                            "core/launcher.md",
                            compute_content_digest(b"# Launcher\n"),
                            compute_content_digest(b"# Launcher\n"),
                            "# Launcher\n",
                        ),
                    ),
                ),
            },
        )()

        def render_instructions(self) -> str:
            return "# Launcher\n"

    result = capsule_delivery_from(_Result())
    assert result.binding.role == LAUNCHER_ROLE_SENTINEL
    assert result.binding.operation == "orientation"


def test_launch_binds_each_inline_link_to_its_admitted_source_without_changing_compilation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = "[same](local.md) [sibling](../../other/workflow.md#step)\n"
    second = "[same](local.md) [self](#section)\n"
    trusted = first + second
    carrier = replace(
        delivery(instructions=trusted),
        instruction_origins=(
            CapsuleInstructionOrigin("operations/one.md", 0, len(first)),
            CapsuleInstructionOrigin("roles/two.md", len(first), len(trusted)),
        ),
    )
    root = tmp_path / "skills/lifecycle"

    @contextmanager
    def corpus():
        yield root, "composition-manifest.json"

    monkeypatch.setattr(role_instruction_details, "shipped_composition_corpus", corpus)
    text, record = role_instruction_details.compose_launch_instructions(
        carrier, str(tmp_path / "elsewhere/request.md"), {}
    )
    assert f"[same]({root / 'operations/local.md'})" in text
    assert f"[same]({root / 'roles/local.md'})" in text
    assert f"[sibling]({tmp_path / 'skills/other/workflow.md'}#step)" in text
    assert f"[self]({root / 'roles/two.md'}#section)" in text
    assert carrier.trusted_instructions == trusted
    assert record["semanticDigest"] == carrier.semantic_digest


def test_launch_markdown_binding_preserves_non_destinations_and_code(tmp_path: Path) -> None:
    source = tmp_path / "roles/worker.md"
    text = (
        '[nested](../a(b).md "Title") [space](<../a b.md#part>)\n'
        "![image](../picture.png?size=2#part) [reference][target]\n"
        '[target]: ../target.md "Reference title"\n'
        "[external](https://example.com/doc) [absolute](/a/doc.md)\n"
        "[detail]({{detail:reference/frozen.md}})\n"
        "`[inline](../unchanged.md)`\n"
        "```md\n[fence](../unchanged.md)\n```\n"
        "    [indent](../unchanged.md)\n"
        "\\[literal](../unchanged.md)\n"
    )
    bound = bind_markdown_links(text, source)
    assert f'[nested]({tmp_path}/a%28b%29.md "Title")' in bound
    assert f"[space](<{tmp_path}/a%20b.md#part>)" in bound
    assert f"![image]({tmp_path}/picture.png?size=2#part)" in bound
    assert f'[target]: {tmp_path}/target.md "Reference title"' in bound
    for line in text.splitlines()[3:]:
        assert line in bound
    assert "[reference][target]" in bound


def test_launch_refuses_unbound_or_incomplete_origins(tmp_path: Path) -> None:
    linked = delivery(instructions="[link](relative.md)\n")
    with pytest.raises(Exception, match="require complete source origins"):
        compose_launch_instructions(linked, str(tmp_path / "request.md"), {})
    for origins in (
        (CapsuleInstructionOrigin("roles/worker.md", 1, len(linked.trusted_instructions)),),
        (CapsuleInstructionOrigin("roles/worker.md", 0, 4),),
    ):
        with pytest.raises(Exception, match="origins do not cover"):
            compose_launch_instructions(
                replace(linked, instruction_origins=origins), str(tmp_path / "request.md"), {}
            )


def _missing_emitted_link_targets(path: Path, body: str) -> list[str]:
    """Inspect local destinations outside quoted Markdown fence examples."""
    prose = "\n".join(line for _, line in unfenced_lines(body.splitlines()))
    missing = []
    for destination in re.findall(r"\]\(([^)\s]+)\)", prose):
        parts = urlsplit(destination)
        if parts.scheme or not parts.path:
            continue
        target = path.parent / unquote(parts.path)
        if not target.is_file():
            missing.append(destination)
    return missing


@pytest.mark.parametrize("marker", ("```", "~~~", "````"))
def test_emitted_link_scan_excludes_only_fenced_examples(tmp_path: Path, marker: str) -> None:
    """A real missing link fails before and after an unchanged quoted example."""
    report = tmp_path / "launch.md"
    literal = "[missing](absent.md)"
    assert _missing_emitted_link_targets(report, literal) == ["absent.md"]
    example = f"{marker}markdown\n{literal}\n{marker}\n"
    assert _missing_emitted_link_targets(report, example) == []
    assert _missing_emitted_link_targets(report, example + literal) == ["absent.md"]
    assert _missing_emitted_link_targets(report, literal + "\n" + example + literal) == [
        "absent.md",
        "absent.md",
    ]
    if marker == "````":
        # A shorter inner fence cannot resume checking the outer example.
        nested = "````markdown\n```\n" + literal + "\n````\n" + literal
        assert _missing_emitted_link_targets(report, nested) == ["absent.md"]
    present = tmp_path / "present file.md"
    present.write_text("present")
    assert (
        _missing_emitted_link_targets(
            report, "[present](present%20file.md) [external](https://example.com) [self](#step)"
        )
        == []
    )
    assert _missing_emitted_link_targets(report, "[missing](absent%20file.md)") == [
        "absent%20file.md"
    ]


def test_every_emitted_role_operation_link_resolves(tmp_path: Path) -> None:
    """Inspect actual output files for the complete declared role/operation population."""
    parsed = _shipped_manifest()
    pairs = {(role, op) for role, entry in parsed.roles.items() for op in entry.operations}
    assert pairs == {
        (role, op) for op, entry in parsed.operations.items() for role in entry.applies_to_roles
    }
    failures = []
    for role, operation in sorted(pairs):
        compiled = real_compilation(role, operation)
        carrier = capsule_delivery_from(compiled)
        report = tmp_path / role / operation / "launch.md"
        text, record = compose_launch_instructions(carrier, str(report), {})
        emitted = [(report, text)] + [
            (Path(item["path"]), Path(item["path"]).read_text())
            for item in record["detailReferences"]
        ]
        for path, body in emitted:
            # Independent inspection of the corpus's Markdown links, including absolute output.
            for destination in _missing_emitted_link_targets(path, body):
                failures.append((role, operation, path.name, destination))
        assert carrier == capsule_delivery_from(compiled)
        assert record["semanticDigest"] == compiled.capsule.semantic_digest
    assert not failures, failures


def test_detail_links_bind_frozen_siblings_and_keep_replay_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "corpus"
    first = b"[second](second.md#step) [other](../other.md) [self](#here)\n"
    second = b"# Step\nOriginal second.\n"
    references = tuple(
        CapsuleDetailReference(f"reference/{name}.md", compute_content_digest(body), body, "step")
        for name, body in (("first", first), ("second", second))
    )

    @contextmanager
    def corpus():
        yield root, "composition-manifest.json"

    monkeypatch.setattr(role_instruction_details, "shipped_composition_corpus", corpus)
    original = replace(
        delivery(instructions="Read {{detail:reference/first.md}}."), detail_references=references
    )
    report = tmp_path / "request.md"
    text, record = compose_launch_instructions(original, str(report), {})
    first_path, second_path = [Path(item["path"]) for item in record["detailReferences"]]
    body = first_path.read_text()
    assert f"[second]({second_path}#step)" in body
    assert f"[self]({first_path}#here)" in body
    assert f"[other]({root / 'other.md'})" in body
    assert original.detail_references == references
    for item in record["detailReferences"]:
        assert item["revision"] == compute_content_digest(Path(item["path"]).read_bytes())
    # Replay needs no current corpus, and rejects a modified emitted detail.
    receipt = {
        "instructionComposition": record,
        "report": {"path": str(report), "canonicalPath": str(report)},
        "handoverArtifact": {
            "path": str(report.with_suffix(".handover.txt")),
            "canonicalPath": str(report.with_suffix(".handover.txt")),
        },
    }
    role_instruction_details.validate_instruction_detail_artifacts(receipt)
    assert compose_launch_instructions(original, str(report), {}) == (text, record)
    second_path.chmod(0o644)
    second_path.write_bytes(b"Changed.\n")
    with pytest.raises(ValueError, match="bytes no longer match"):
        role_instruction_details.validate_instruction_detail_artifacts(receipt)
