"""Additional manifest and source-admission refusal boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.role_capsules.launch import compile_launch_capsule
from agents_remember.application.role_capsules.sources import (
    CapsuleAdmissionRequest,
    admit_capsule_sources,
)
from agents_remember.errors import CapsuleCompilationError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_capsules.compiler import compile_role_capsule
from agents_remember.models.role_capsules.selection import narrow_role
from agents_remember.models.role_capsules.source_set import admit_source_set
from agents_remember.models.role_capsules.sources import (
    CapsuleDeclaredInstruction,
    instruction_identity,
)
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleLauncherSeat,
    CapsuleOverride,
    CapsuleRoleSeat,
    CapsuleSourceAdmission,
    CapsuleSourceSelection,
    compute_content_digest,
)
from agents_remember.serving.launch_capsule import LaunchCapsuleMode, LaunchCapsuleRequest
from test_capsule_launch_wiring import LEAF_REF, launch_corpus_fixture, world_fixture
from test_role_capsule_admission import (
    LIFECYCLE_ROOT,
    MANIFEST_RELATIVE,
    _manifest_with,
    _refuse,
    _shipped_binding,
    _shipped_parsed,
    _shipped_request,
)

__all__ = ["launch_corpus_fixture", "world_fixture"]


def test_launch_refuses_malformed_audience_markers_after_common_or_excluded_passages(
    world: tuple[Path, McpRuntimeConfig, Path], launch_corpus: Path
) -> None:
    root, config, _repo = world
    manifest = launch_corpus / "composition-manifest.json"
    document = json.loads(manifest.read_text())
    document["operations"]["orientation"]["audience"] = "sections"
    manifest.write_text(json.dumps(document))
    source = launch_corpus / "operations/orientation.md"
    request = LaunchCapsuleRequest(role="worker", workspace_root=root, task_document_ref=LEAF_REF)
    common = "<!-- ar:roles all -->\nCommon boundary.\n"
    excluded = "<!-- ar:roles curator -->\nCurator-only boundary.\n"
    addressed = "<!-- ar:roles worker -->\nWorker-only boundary.\n"
    source.write_text(common + excluded + addressed)
    accepted = compile_launch_capsule(config, request)
    assert accepted.delivery is not None, accepted.explain()
    assert "Common boundary." in accepted.delivery.trusted_instructions
    assert "Worker-only boundary." in accepted.delivery.trusted_instructions
    assert "Curator-only boundary." not in accepted.delivery.trusted_instructions
    for prefix in (common, excluded):
        for recipients in ("curator2", "Curator", "curator, worker", "janitor"):
            marker = f"<!-- ar:roles {recipients} -->"
            text = prefix + marker + "\nOther-role instructions.\n" + addressed
            source.write_text(text)
            refused = compile_launch_capsule(config, request)
            assert refused.mode is LaunchCapsuleMode.REFUSED, recipients
            assert refused.delivery is None
            assert refused.refusal_status == "operation-audience-invalid"
            line = text.splitlines().index(marker) + 1
            assert f"operations/orientation.md:{line}:" in refused.refusal_detail


def test_the_role_lookup_answers_through_the_one_narrowing_gate() -> None:
    """A role question has exactly one answer, and it is the selection gate's.

    There is no second accessor that could disagree with the gate or stand in for it:
    the parsed manifest is a mapping, and the refusal for an unknown role is raised in
    exactly one place.
    """

    parsed = _shipped_parsed()
    assert parsed.roles["worker"].role == "worker"

    with pytest.raises(CapsuleCompilationError) as raised:
        narrow_role("janitor")

    assert raised.value.status == "unknown-role"
    assert "'janitor'" in raised.value.detail

    # The gate's other lookup, asked the same question: a name the manifest does not declare is
    # refused by name rather than answered with a default. This was a second case until the two were
    # merged, because both measure the one narrowing gate that turns a caller's string into a
    # declared manifest entry.
    assert parsed.skill_entry("l-01-agent-lifecycles").origin == "agents-remember/skills"

    with pytest.raises(CapsuleCompilationError) as unknown_skill:
        parsed.skill_entry("no-such-skill")

    assert unknown_skill.value.status == "manifest-vocabulary-mismatch"
    assert "no-such-skill" in unknown_skill.value.detail


def test_launch_identity_seals_audience_projection_but_not_unrelated_sources(
    world: tuple[Path, McpRuntimeConfig, Path], launch_corpus: Path
) -> None:
    root, config, _repo = world
    manifest = launch_corpus / "composition-manifest.json"
    document = json.loads(manifest.read_text())
    stage = launch_corpus / "operations/orientation.md"
    stage.write_text(
        "<!-- ar:roles all -->\nCommon obligation.\n"
        "<!-- ar:roles curator -->\nCurator obligation.\n"
        "<!-- ar:roles worker -->\nWorker obligation.\n"
    )
    source_bytes = stage.read_bytes()
    request = LaunchCapsuleRequest(role="worker", workspace_root=root, task_document_ref=LEAF_REF)

    def delivery_for(audience: str):
        document["operations"]["orientation"]["audience"] = audience
        manifest.write_text(json.dumps(document))
        answer = compile_launch_capsule(config, request)
        assert answer.delivery is not None, answer.explain()
        return answer.delivery

    sections = delivery_for("sections")
    whole = delivery_for("all")
    assert stage.read_bytes() == source_bytes
    assert sections.trusted_instructions != whole.trusted_instructions
    assert "Curator obligation." not in sections.trusted_instructions
    assert "Curator obligation." in whole.trusted_instructions
    assert sections.semantic_digest != whole.semantic_digest
    assert delivery_for("sections") == sections
    (launch_corpus / "roles/curator.md").write_text("Unrelated changed role instructions.\n")
    document["operations"]["curation"]["purpose"] = "Unrelated manifest diagnostic change."
    assert delivery_for("sections") == sections


def test_the_specializations_field_must_be_an_array_when_present() -> None:
    """A present-but-wrong-typed optional field is refused, not ignored.

    All three non-array shapes are checked in one item because they reach the same
    branch: the point is the branch, not three spellings of it.
    """

    for bad in ({"notes": "not-an-array"}, "specializations/a.md", 7):

        def mutate(document: dict, value=bad) -> None:
            document["specializations"] = value

        assert _refuse(_manifest_with(mutate)) == "manifest-invalid"

    # The same rule one level down, in a declared value rather than the manifest: a blank identity is
    # refused at construction instead of becoming an entry the compiler would have to name. The two
    # were one case until they were merged -- both measure a required string being blank.
    with pytest.raises(ValueError) as blank:
        CapsuleDeclaredInstruction(
            identity="   ",
            composition_root="core",
            authorities=("core:authority:shared-core",),
        )

    assert "non-blank" in str(blank.value)


def test_an_override_whose_replacement_file_is_absent_is_refused() -> None:
    """The second unknown-supersession branch: no admitted source carries the winner.

    The superseded identity is the seat's own **role** block rather than a shared core block:
    a role capsule composes no core, so ``core:authority`` is now not selected for this
    capsule and the case would exercise the first branch instead of the second.
    """

    parsed = _shipped_parsed()
    entry = parsed.roles["worker"]
    binding = CapsuleBinding(
        operation="implementation",
        admitted=CapsuleAdmittedFacts(
            task_reference="agents-remember/tasks/demo/task.json",
            task_document_digest=compute_content_digest(b'{"id": "demo"}'),
            seat=CapsuleRoleSeat(role="worker", altitude=entry.altitude),
            repository_id="agents-remember",
            work_branch="ar/260915-caps-l2",
        ),
        overrides=(
            CapsuleOverride(
                superseded_identity="role:worker",
                superseding_identity="specialization:absent",
                authority="developer-ruling-2026-09-16",
                rationale="names a replacement nothing carries",
            ),
        ),
    )

    with pytest.raises(CapsuleCompilationError) as raised:
        compile_role_capsule(
            binding,
            (LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes(),
            admit_capsule_sources(_shipped_request("worker", "implementation")),
            _shipped_request("worker", "implementation").as_selection(),
        )

    assert raised.value.status == "unknown-supersession"
    assert "specialization:absent" in raised.value.detail


def test_a_source_root_that_is_not_a_path_is_refused() -> None:
    """The admission boundary refuses a non-path root instead of guessing."""

    with pytest.raises(CapsuleCompilationError) as raised:
        admit_capsule_sources(
            CapsuleAdmissionRequest(root="not-a-path", manifest=MANIFEST_RELATIVE)  # type: ignore[arg-type]
        )

    assert raised.value.status == "source-root-invalid"


def test_an_admitted_set_that_drops_the_manifest_mid_compile_is_refused() -> None:
    """A selection whose manifest path is absent from the admitted mapping."""

    parsed = _shipped_parsed()
    entry = parsed.roles["worker"]
    request = _shipped_request("worker", "implementation")
    sources = admit_capsule_sources(request)

    with pytest.raises(CapsuleCompilationError) as raised:
        admit_source_set(
            parsed,
            CapsuleSourceAdmission(
                binding=_shipped_binding("worker", "implementation"),
                selection=CapsuleSourceSelection(
                    root=str(LIFECYCLE_ROOT),
                    manifest="other-manifest.json",
                    core=tuple(parsed.core[name].source for name in entry.core),
                    role=(entry.file,),
                    operation=(parsed.operations["implementation"].source,),
                ),
            ),
            {source.path: source for source in sources},
            {source.path: instruction_identity("core", "authority") for source in sources},
            (),
        )

    assert raised.value.status == "source-not-declared"
    assert "does not include the composition manifest" in raised.value.detail


def test_composing_roots_answers_what_each_seat_kind_can_actually_contribute() -> None:
    """The accessor must not promise a root the seat kind cannot contribute from.

    A consumer asking about the launcher and being told ``role`` is available would
    contradict the boundary the compiler enforces, so the answer is asserted against
    what the two seat kinds really compose.
    """

    parsed = _shipped_parsed()

    role_roots = parsed.composing_roots("role")
    launcher_roots = parsed.composing_roots("launcher")

    assert role_roots == ("core", "role", "operation", "specialization")
    assert launcher_roots == ("core", "operation", "specialization")
    assert "role" not in launcher_roots

    # And the answer matches what the compiler actually composes for each seat kind.
    worker = compile_admitted_capsule(
        _shipped_binding("worker", "implementation"), _shipped_request("worker", "implementation")
    )
    assert worker.result is not None
    composed = {block.composition_root for block in worker.result.capsule.instructions}
    assert composed <= set(role_roots)

    operation = parsed.launcher.operations[0]
    launcher = compile_admitted_capsule(
        CapsuleBinding(
            operation=operation,
            admitted=CapsuleAdmittedFacts(
                task_reference="agents-remember/tasks/demo/task.json",
                task_document_digest=compute_content_digest(b'{"id": "demo"}'),
                seat=CapsuleLauncherSeat(routing_condition=parsed.launcher.routing_condition),
                repository_id="agents-remember",
                work_branch="ar/260915-caps-l2",
            ),
        ),
        CapsuleAdmissionRequest(
            root=LIFECYCLE_ROOT,
            manifest=MANIFEST_RELATIVE,
            core=tuple(parsed.core[name].source for name in parsed.launcher.core),
            operation=(parsed.operations[operation].source,),
            skill=tuple(entry.source for entry in parsed.skills.values()),
        ),
    )
    assert launcher.result is not None
    launcher_composed = {block.composition_root for block in launcher.result.capsule.instructions}
    assert "role" not in launcher_composed
    assert launcher_composed <= set(launcher_roots)
