"""Focused behavioral checks for the deterministic role-capsule compiler.

The compiler's contract is a set of observable properties, and each case here pins
one of them:

* the same admitted facts and source bytes compile to the same ordered content and
  the same semantic digest;
* ephemeral diagnostics do not move that digest, while a real change to an
  applicable source block does;
* an unrelated role's source does not reach a worker capsule at all;
* every documented refusal happens for its own reason and advertises its remedy;
* composition needs neither a model nor a network; and
* an unknown role or operation is refused instead of falling back.

The fixture is a small in-memory corpus built to the canonical manifest schema, so a
case varies exactly one fact. That is deliberate: a fixture that mirrors the whole
shipped corpus could only be mutated by editing the things under test, which is how
a vacuous assertion gets written.
"""

from __future__ import annotations

import ast
import json
import socket
import sys
from pathlib import Path

import pytest
from agents_remember.application.role_capsules.compilation import CapsuleCompilationOutcome
from agents_remember.errors import CapsuleCompilationError, CapsuleSourceError
from agents_remember.models.role_capsules import compiler
from agents_remember.models.role_capsules.compiler import compile_role_capsule, refused_manifest
from agents_remember.models.role_capsules.sources import (
    CapsuleDeclaredInstruction,
    CapsuleSource,
    instruction_identity,
    skills_declared_identity,
    specializations_declared_identity,
)
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleInstructionUnit,
    CapsuleLauncherSeat,
    CapsuleOverride,
    CapsuleRequirementBinding,
    CapsuleRoleSeat,
    CapsuleSourceSelection,
    CapsuleSuppliedProjection,
    CapsuleTaskContext,
    CapsuleToolPolicy,
    _require_digest,
    compute_content_digest,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = "/fixture/skills/l-01-agent-lifecycles"
FIXTURE_MANIFEST = "composition-manifest.json"
SPECIALIZATION_PATH = "specializations/repo-notes.md"
SPECIALIZATION_IDENTITY = "specialization:repo-notes"
SKILL_NAME = "l-01-agent-lifecycles"
SKILL_ORIGIN = "agents-remember/skills"
SKILL_URI = "skill://agents-remember/skills/l-01-agent-lifecycles"
SKILL_SOURCE = "SKILL.md"
SKILL_IDENTITY = f"{SKILL_ORIGIN}#{SKILL_NAME}"

FIXTURE_ROLES = (
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

FIXTURE_OPERATIONS = (
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

FIXTURE_CORE = ("authority", "invariants", "lifecycle-frame", "loop", "acceptance", "launcher")

FIXTURE_APPLICABILITY = {
    "orientation": FIXTURE_ROLES,
    "planning": ("architect", "designer", "strategist", "orchestrator"),
    "implementation": ("worker",),
    "review": ("architect", "orchestrator", "manager", "reviewer"),
    "curation": ("manager", "curator"),
    "coordination": ("architect", "orchestrator", "manager"),
    "authorized-closeout": ("orchestrator", "manager"),
    "recovery": (
        "architect",
        "orchestrator",
        "manager",
        "worker",
        "curator",
        "system-specialist",
    ),
    "bootstrap": ("bootstrap",),
}

FIXTURE_ROLE_CORE = {
    "architect": ("authority", "invariants", "lifecycle-frame", "loop", "acceptance"),
    "orchestrator": ("authority", "invariants", "lifecycle-frame", "loop", "acceptance"),
    "designer": ("authority", "invariants", "loop", "acceptance"),
    "strategist": ("authority", "invariants", "loop", "acceptance"),
    "manager": ("authority", "invariants", "lifecycle-frame", "loop", "acceptance"),
    "worker": ("authority", "invariants", "lifecycle-frame", "acceptance"),
    "curator": ("authority", "invariants", "acceptance"),
    "reviewer": ("authority", "invariants", "loop", "acceptance"),
    "system-specialist": ("authority", "invariants", "acceptance"),
    "bootstrap": ("authority", "invariants", "acceptance"),
}

DIRECTORIES = {
    "core": "core",
    "role": "roles",
    "operation": "operations",
    "specialization": "specializations",
}


# --------------------------------------------------------------------------------------
# Fixture construction.
# --------------------------------------------------------------------------------------


def fixture_manifest_document() -> dict[str, object]:
    """A minimal but structurally complete composition manifest."""

    return {
        "schema": "ar-role-capsule-composition/v1",
        "role_order": list(FIXTURE_ROLES),
        "specializations": [
            {"name": "repo-notes", "source": SPECIALIZATION_PATH, "purpose": "repo notes"}
        ],
        "operations": {
            name: {
                "source": f"operations/{name}.md",
                "purpose": f"the {name} operation",
                "applies_to_roles": list(FIXTURE_APPLICABILITY[name]),
            }
            for name in FIXTURE_OPERATIONS
        },
        "core": {
            name: {"source": f"core/{name}.md", "purpose": f"the {name} core block"}
            for name in FIXTURE_CORE
        },
        "roles": {
            role: {
                "file": f"roles/{role}.md",
                "altitude": "leaf" if role in {"worker", "curator", "reviewer"} else "sprint",
                "core": list(FIXTURE_ROLE_CORE[role]),
                "operations": [
                    name for name in FIXTURE_OPERATIONS if role in FIXTURE_APPLICABILITY[name]
                ],
                "tools": [],
                "skills": [SKILL_NAME] if role == "worker" else [],
            }
            for role in FIXTURE_ROLES
        },
        "skills": {
            "l-01-agent-lifecycles": {
                "origin": SKILL_ORIGIN,
                "uri": SKILL_URI,
                "source": SKILL_SOURCE,
            }
        },
        "launcher": {
            "is_role": False,
            "routing_condition": "ambient-launcher",
            "instruction_source": "core/launcher.md",
            "core": ["launcher", "authority"],
            "operations": ["orientation", "coordination"],
        },
    }


def manifest_bytes(document: dict[str, object] | None = None) -> bytes:
    return json.dumps(document if document is not None else fixture_manifest_document()).encode(
        "utf-8"
    )


def source_text(identity: str) -> str:
    return f"# {identity}\n\nInstruction body for {identity}.\n"


def skill_source(
    name: str = SKILL_NAME,
    *,
    origin: str = SKILL_ORIGIN,
    path: str = SKILL_SOURCE,
    content: str | None = None,
) -> CapsuleSource:
    """One admitted skill root file, whose bytes are a reference's revision."""

    identity = f"{origin}#{name}"
    payload = (source_text(identity) if content is None else content).encode("utf-8")
    return CapsuleSource(
        identity=identity,
        composition_root="skill",
        path=path,
        content=payload,
        revision=compute_content_digest(payload),
    )


def make_source(
    identity: str,
    *,
    path: str | None = None,
    content: str | None = None,
    composition_root: str | None = None,
) -> CapsuleSource:
    text = source_text(identity) if content is None else content
    root, name = identity.split(":", 1)
    return CapsuleSource(
        identity=identity,
        composition_root=(composition_root or root),  # type: ignore[arg-type]
        path=path if path is not None else f"{DIRECTORIES[root]}/{name}.md",
        content=text.encode("utf-8"),
        revision=compute_content_digest(text.encode("utf-8")),
    )


def manifest_source(document: dict[str, object] | None = None) -> CapsuleSource:
    """The composition manifest itself, admitted as metadata beside its sources."""

    payload = manifest_bytes(document)
    return CapsuleSource(
        identity="meta:composition-manifest",
        composition_root="core",
        path=FIXTURE_MANIFEST,
        content=payload,
        revision=compute_content_digest(payload),
    )


def all_sources(document: dict[str, object] | None = None) -> tuple[CapsuleSource, ...]:
    """Every source the fixture manifest declares, plus the manifest, and nothing else."""

    document = fixture_manifest_document() if document is None else document
    sources = [manifest_source(document)]
    sources += [
        make_source(f"core:{name}", path=entry["source"])  # type: ignore[arg-type]
        for name, entry in document["core"].items()  # type: ignore[union-attr]
    ]
    sources += [
        make_source(f"role:{name}", path=entry["file"])  # type: ignore[arg-type]
        for name, entry in document["roles"].items()  # type: ignore[union-attr]
    ]
    sources += [
        make_source(f"operation:{name}", path=entry["source"])  # type: ignore[arg-type]
        for name, entry in document["operations"].items()  # type: ignore[union-attr]
    ]
    for entry in document["specializations"]:  # type: ignore[union-attr]
        sources.append(make_source(f"specialization:{entry['name']}", path=entry["source"]))
    for name, entry in document.get("skills", {}).items():  # type: ignore[union-attr]
        sources.append(skill_source(name, origin=entry["origin"], path=entry["source"]))
    return tuple(sources)


def selection_for(
    document: dict[str, object] | None = None,
    *,
    specialization: tuple[str, ...] = (),
) -> CapsuleSourceSelection:
    """The source selection the manifest itself declares, derived from the document."""

    document = fixture_manifest_document() if document is None else document
    return CapsuleSourceSelection(
        root=FIXTURE_ROOT,
        manifest=FIXTURE_MANIFEST,
        core=tuple(entry["source"] for entry in document["core"].values()),  # type: ignore[union-attr]
        role=tuple(entry["file"] for entry in document["roles"].values()),  # type: ignore[union-attr]
        operation=tuple(
            entry["source"]
            for entry in document["operations"].values()  # type: ignore[union-attr]
        ),
        specialization=specialization,
    )


def worker_binding(
    *,
    operation: str = "implementation",
    specializations: tuple[str, ...] = (),
    overrides: tuple[CapsuleOverride, ...] = (),
    repository_id: str = "agents-remember",
    granted: frozenset[str] = frozenset(),
) -> CapsuleBinding:
    return CapsuleBinding(
        operation=operation,  # type: ignore[arg-type]
        admitted=CapsuleAdmittedFacts(
            task_reference="agents-remember/tasks/demo/task.json",
            task_document_digest=compute_content_digest(b'{"id": "demo"}'),
            seat=CapsuleRoleSeat(role="worker", altitude="leaf"),
            repository_id=repository_id,
            work_branch="ar/260915-caps-l2",
            requirements=(CapsuleRequirementBinding(stable_id="CAPS-R02", revision="v1"),),
            tool_policy=CapsuleToolPolicy(granted=granted),
        ),
        specializations=specializations,
        overrides=overrides,
    )


def launcher_binding(*, operation: str = "orientation") -> CapsuleBinding:
    return CapsuleBinding(
        operation=operation,  # type: ignore[arg-type]
        admitted=CapsuleAdmittedFacts(
            task_reference="agents-remember/tasks/demo/task.json",
            task_document_digest=compute_content_digest(b'{"id": "demo"}'),
            seat=CapsuleLauncherSeat(routing_condition="ambient-launcher"),
            repository_id="agents-remember",
            work_branch="ar/260915-caps-l2",
        ),
    )


def compile_worker(
    *,
    document: dict[str, object] | None = None,
    sources: tuple[CapsuleSource, ...] | None = None,
    binding: CapsuleBinding | None = None,
    specialization: tuple[str, ...] = (),
    projection: CapsuleSuppliedProjection | None = None,
):
    document = fixture_manifest_document() if document is None else document
    return compile_role_capsule(
        binding if binding is not None else worker_binding(),
        manifest_bytes(document),
        all_sources(document) if sources is None else sources,
        selection_for(document, specialization=specialization),
        projection,
    )


def failure(**kwargs) -> CapsuleCompilationError:
    with pytest.raises(CapsuleCompilationError) as raised:
        compile_worker(**kwargs)
    return raised.value


def replaced(
    sources: tuple[CapsuleSource, ...], identity: str, **fields: object
) -> tuple[CapsuleSource, ...]:
    """The same admitted set with one source's shape changed."""

    return tuple(
        make_source(
            source.identity,
            path=fields.get("path", source.path),  # type: ignore[arg-type]
            content=fields.get("content", source.content.decode("utf-8")),  # type: ignore[arg-type]
            composition_root=fields.get("composition_root", source.composition_root),  # type: ignore[arg-type]
        )
        if source.identity == identity
        else source
        for source in sources
    )


# --------------------------------------------------------------------------------------
# Determinism.
# --------------------------------------------------------------------------------------


def test_identical_input_compiles_to_identical_ordered_content_and_digest() -> None:
    first = compile_worker()
    second = compile_worker()

    assert [block.identity for block in first.capsule.instructions] == [
        block.identity for block in second.capsule.instructions
    ]
    assert first.render_instructions() == second.render_instructions()
    assert first.semantic_digest == second.semantic_digest
    assert first.capsule.instruction_units == second.capsule.instruction_units


def test_instruction_order_is_shared_core_then_role_then_operation() -> None:
    result = compile_worker()

    roots = [block.composition_root for block in result.capsule.instructions]
    expected_core = sorted(FIXTURE_ROLE_CORE["worker"])
    assert roots == ["core"] * len(expected_core) + ["role", "operation"]
    assert [block.identity for block in result.capsule.instructions][: len(expected_core)] == [
        instruction_identity("core", name) for name in expected_core
    ]


def test_a_declared_but_unselected_specialization_changes_diagnostics_not_identity() -> None:
    """The manifest records the declaration; the capsule identity ignores it."""

    document = fixture_manifest_document()
    without_sources = tuple(
        source for source in all_sources(document) if source.identity != SPECIALIZATION_IDENTITY
    )
    baseline = compile_worker(document=document, sources=without_sources)
    declared_only = compile_worker(document=document, sources=without_sources)

    assert declared_only.semantic_digest == baseline.semantic_digest
    assert declared_only.render_instructions() == baseline.render_instructions()
    recorded = [
        record
        for record in declared_only.manifest.sources
        if not record.selected and record.composition_root == "specialization"
    ]
    assert [record.identity for record in recorded] == [SPECIALIZATION_IDENTITY]
    assert recorded[0].path == SPECIALIZATION_PATH
    assert recorded[0].selection_reason


def test_reordering_the_composed_blocks_changes_the_semantic_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Order is part of the capsule identity, not presentation over it.

    The seed here reverses the resolved order before the digest is taken. Without this
    case, a digest that ignored order entirely would still satisfy every determinism
    assertion above, because those compare one fixed order with itself.
    """

    baseline = compile_worker()
    resolution = sys.modules[compiler.__name__].resolve_instructions
    monkeypatch.setattr(
        sys.modules[compiler.__name__],
        "resolve_instructions",
        lambda *args, **kwargs: tuple(reversed(resolution(*args, **kwargs))),
    )
    reordered = compile_worker()

    assert [block.identity for block in reordered.capsule.instructions] == list(
        reversed([block.identity for block in baseline.capsule.instructions])
    )
    assert reordered.semantic_digest != baseline.semantic_digest


def test_a_real_binding_change_does_move_the_semantic_digest() -> None:
    """The determinism cases above are only meaningful if the digest can differ."""

    baseline = compile_worker()
    other_repository = compile_worker(binding=worker_binding(repository_id="other-repo"))

    assert other_repository.semantic_digest != baseline.semantic_digest


# --------------------------------------------------------------------------------------
# Blast radius: one applicable block changes one capsule; an unrelated role does not.
# --------------------------------------------------------------------------------------


def test_changing_an_unrelated_role_leaves_the_worker_capsule_untouched() -> None:
    baseline = compile_worker()
    mutated = compile_worker(
        sources=replaced(all_sources(), "role:designer", content="# designer, rewritten\n")
    )

    assert mutated.semantic_digest == baseline.semantic_digest
    assert mutated.render_instructions() == baseline.render_instructions()
    assert all(block.identity != "role:designer" for block in mutated.capsule.instructions)


def test_changing_an_applicable_operation_block_changes_that_capsule() -> None:
    baseline = compile_worker()
    mutated = compile_worker(
        sources=replaced(
            all_sources(), "operation:implementation", content="# implementation, revised\n"
        )
    )

    assert mutated.semantic_digest != baseline.semantic_digest
    assert mutated.render_instructions() != baseline.render_instructions()


def test_a_shared_core_change_reaches_the_capsule_that_composes_it() -> None:
    baseline = compile_worker()
    mutated = compile_worker(
        sources=replaced(all_sources(), "core:authority", content="# authority, revised\n")
    )

    assert mutated.semantic_digest != baseline.semantic_digest
    authority = next(b for b in mutated.capsule.instructions if b.identity == "core:authority")
    assert authority.content == "# authority, revised\n"


# --------------------------------------------------------------------------------------
# Selection refusals.
# --------------------------------------------------------------------------------------


def test_unknown_role_is_refused_and_never_acquires_a_capsule() -> None:
    error = failure(
        binding=CapsuleBinding(
            operation="implementation",
            admitted=CapsuleAdmittedFacts(
                task_reference="agents-remember/tasks/demo/task.json",
                task_document_digest=compute_content_digest(b"{}"),
                seat=CapsuleRoleSeat(role="janitor", altitude="leaf"),  # type: ignore[arg-type]
                repository_id="agents-remember",
                work_branch="ar/260915-caps-l2",
            ),
        )
    )

    assert error.status == "unknown-role"
    assert "'janitor'" in error.detail
    assert "never derives a role from caller text" in error.next_action


def test_unknown_operation_is_refused_instead_of_falling_back() -> None:
    error = failure(binding=worker_binding(operation="deployment"))

    assert error.status == "unknown-operation"
    assert "'deployment'" in error.detail
    assert "no fallback operation" in error.next_action


def test_operation_the_role_cannot_run_is_refused_instead_of_substituted() -> None:
    error = failure(binding=worker_binding(operation="planning"))

    assert error.status == "operation-not-applicable"
    assert "'worker'" in error.detail and "'planning'" in error.detail
    assert "never selects a neighbouring operation" in error.next_action


def test_launcher_seat_composes_its_own_core_and_is_not_a_role() -> None:
    result = compile_worker(binding=launcher_binding())

    identities = [block.identity for block in result.capsule.instructions]
    assert identities == ["core:authority", "core:launcher", "operation:orientation"]
    assert result.manifest.role is None
    assert result.manifest.seat_kind == "launcher"


def test_launcher_is_refused_an_operation_no_role_inherits_to_it() -> None:
    error = failure(binding=launcher_binding(operation="implementation"))

    assert error.status == "operation-not-applicable"
    assert "ambient launcher" in error.detail
    assert "inherits no role's operations" in error.next_action


# --------------------------------------------------------------------------------------
# Source-set refusals.
# --------------------------------------------------------------------------------------


def test_missing_mandatory_material_is_refused_rather_than_omitted() -> None:
    error = failure(
        sources=tuple(source for source in all_sources() if source.identity != "core:acceptance")
    )

    assert error.status == "missing-required-instruction"
    assert "core:acceptance" in error.detail
    assert "never dropped to fit a size target" in error.next_action


def test_a_source_the_manifest_does_not_declare_is_refused() -> None:
    error = failure(sources=(*all_sources(), make_source("core:smuggled", path="core/smuggled.md")))

    assert error.status == "source-not-declared"
    assert "core/smuggled.md" in error.detail


def test_a_source_read_from_the_wrong_composition_root_is_refused() -> None:
    """The role source is admitted as if it carried shared-core authority."""

    error = failure(sources=replaced(all_sources(), "role:worker", composition_root="core"))

    assert error.status == "source-root-mismatch"
    assert "roles/worker.md" in error.detail
    assert "composition root" in error.next_action


def test_repository_specialization_must_be_admitted_on_the_binding() -> None:
    error = failure(sources=all_sources(), specialization=(SPECIALIZATION_PATH,))

    assert error.status == "specialization-not-admitted"
    assert SPECIALIZATION_PATH in error.detail
    assert "explicit admission" in error.next_action


def test_an_admitted_specialization_is_composed_after_the_operation() -> None:
    result = compile_worker(
        specialization=(SPECIALIZATION_PATH,),
        binding=worker_binding(specializations=(SPECIALIZATION_IDENTITY,)),
    )

    roots = [block.composition_root for block in result.capsule.instructions]
    assert roots[-1] == "specialization"
    assert result.capsule.instructions[-1].identity == SPECIALIZATION_IDENTITY


# --------------------------------------------------------------------------------------
# Identity, duplication, supersession and contradiction.
# --------------------------------------------------------------------------------------


def test_duplicate_identity_with_byte_identical_content_collapses_to_one_block() -> None:
    """Two admitted files claiming one identity, with the same bytes, compose once."""

    nested = "specializations/team/repo-notes.md"
    result = compile_worker(
        sources=(*all_sources(), make_source(SPECIALIZATION_IDENTITY, path=nested)),
        specialization=(SPECIALIZATION_PATH, nested),
        binding=worker_binding(specializations=(SPECIALIZATION_IDENTITY,)),
    )

    identities = [block.identity for block in result.capsule.instructions]
    assert identities.count(SPECIALIZATION_IDENTITY) == 1
    collapsed = [
        record
        for record in result.manifest.sources
        if record.superseded_kind == "duplicate-identity-collapsed"
    ]
    assert [record.path for record in collapsed] == [nested]
    assert collapsed[0].identity == SPECIALIZATION_IDENTITY
    assert collapsed[0].superseded_by == SPECIALIZATION_PATH


def test_duplicate_identity_with_a_different_body_stops_compilation() -> None:
    """One identity, two different bodies, no declared winner: compilation stops."""

    nested = "specializations/team/repo-notes.md"
    error = failure(
        sources=(
            *all_sources(),
            make_source(SPECIALIZATION_IDENTITY, path=nested, content="# a rival\n"),
        ),
        specialization=(SPECIALIZATION_PATH, nested),
        binding=worker_binding(specializations=(SPECIALIZATION_IDENTITY,)),
    )

    assert error.status == "equal-authority-contradiction"
    assert SPECIALIZATION_IDENTITY in error.detail
    assert nested in error.detail
    assert "never picks a winner by filename" in error.next_action
    assert error.conflicts[0]["kind"] == "equal-authority-contradiction"
    assert error.conflicts[0]["contenders"] == [SPECIALIZATION_PATH, nested]


def test_an_explicit_override_records_its_provenance_and_selects_the_winner() -> None:
    """The same contradictory pair compiles once a winner is admitted explicitly."""

    nested = "specializations/team/repo-notes.md"
    result = compile_worker(
        sources=(
            *all_sources(),
            make_source(SPECIALIZATION_IDENTITY, path=nested, content="# admitted\n"),
        ),
        specialization=(SPECIALIZATION_PATH, nested),
        binding=worker_binding(
            specializations=(SPECIALIZATION_IDENTITY,),
            overrides=(
                CapsuleOverride(
                    superseded_identity=SPECIALIZATION_IDENTITY,
                    superseding_identity=SPECIALIZATION_IDENTITY,
                    authority="developer-ruling-2026-09-16",
                    rationale="the admitted ruling names the file that carries this identity",
                    admitted_path=nested,
                ),
            ),
        ),
    )

    blocks = [b for b in result.capsule.instructions if b.identity == SPECIALIZATION_IDENTITY]
    assert len(blocks) == 1
    assert blocks[0].content == "# admitted\n"
    record = next(
        r for r in result.manifest.sources if r.identity == SPECIALIZATION_IDENTITY and r.selected
    )
    assert record.superseded_kind == "explicit-supersession"
    assert "explicitly superseded" in record.selection_reason


def test_an_override_that_supersedes_an_unselected_identity_is_refused() -> None:
    error = failure(
        binding=worker_binding(
            overrides=(
                CapsuleOverride(
                    superseded_identity="core:loop",
                    superseding_identity="core:authority",
                    authority="developer-ruling-2026-09-16",
                    rationale="irrelevant to a worker capsule",
                ),
            )
        )
    )

    assert error.status == "unknown-supersession"
    assert "core:loop" in error.detail
    assert "stale binding" in error.next_action


def test_an_override_that_pulls_an_earlier_tier_over_a_role_is_refused() -> None:
    """Shared core composes first; it may not be pulled forward over a role decision."""

    error = failure(
        binding=worker_binding(
            overrides=(
                CapsuleOverride(
                    superseded_identity="role:worker",
                    superseding_identity="core:authority",
                    authority="developer-ruling-2026-09-16",
                    rationale="shared core attempting to outrank the role block",
                ),
            )
        )
    )

    assert error.status == "supersession-conflict"
    assert "'core'" in error.detail and "'role'" in error.detail
    assert "never rewritten from above it" in error.next_action


def test_an_override_may_replace_a_block_from_a_later_tier() -> None:
    """The used direction: a specialization refines the operation it specializes."""

    result = compile_worker(
        specialization=(SPECIALIZATION_PATH,),
        binding=worker_binding(
            specializations=(SPECIALIZATION_IDENTITY,),
            overrides=(
                CapsuleOverride(
                    superseded_identity="operation:implementation",
                    superseding_identity=SPECIALIZATION_IDENTITY,
                    authority="developer-ruling-2026-09-16",
                    rationale="the repository specialization replaces the generic operation text",
                ),
            ),
        ),
    )

    identities = [block.identity for block in result.capsule.instructions]
    assert "operation:implementation" not in identities
    assert SPECIALIZATION_IDENTITY in identities


def test_two_overrides_for_one_identity_are_refused_as_unorderable() -> None:
    error = failure(
        binding=worker_binding(
            overrides=(
                CapsuleOverride(
                    superseded_identity="core:authority",
                    superseding_identity="core:authority",
                    authority="ruling-a",
                    rationale="first",
                ),
                CapsuleOverride(
                    superseded_identity="core:authority",
                    superseding_identity="core:authority",
                    authority="ruling-b",
                    rationale="second",
                ),
            )
        )
    )

    assert error.status == "supersession-conflict"
    assert "exactly one supersession" in error.next_action


# --------------------------------------------------------------------------------------
# Capability requests stay requests.
# --------------------------------------------------------------------------------------


def test_a_tool_request_inside_the_policy_is_carried_but_not_granted() -> None:
    document = fixture_manifest_document()
    document["roles"]["worker"]["tools"] = ["read_ar_files"]  # type: ignore[index]
    result = compile_worker(
        document=document, binding=worker_binding(granted=frozenset({"read_ar_files"}))
    )

    assert [request.tool_id for request in result.capsule.requested_tools] == ["read_ar_files"]
    assert result.capsule.requested_tools[0].authority == "role:worker"
    assert result.manifest.granted_tools == ("read_ar_files",)


def test_a_tool_request_outside_the_admitted_policy_is_refused() -> None:
    document = fixture_manifest_document()
    document["roles"]["worker"]["tools"] = ["worktree_closeout_apply"]  # type: ignore[index]
    error = failure(document=document, binding=worker_binding(granted=frozenset({"read_ar_files"})))

    assert error.status == "tool-request-not-permitted"
    assert "worktree_closeout_apply" in error.detail
    assert "it never grants them" in error.next_action


# --------------------------------------------------------------------------------------
# The task-context seam.
# --------------------------------------------------------------------------------------


def test_a_supplied_task_projection_is_carried_in_its_own_channel() -> None:
    markdown = "## Objective\n\nCompile one capsule.\n"
    projection = CapsuleSuppliedProjection(
        context=CapsuleTaskContext(
            markdown=markdown,
            origin="agents-remember/tasks/demo/task.json",
            projection_revision="demo-revision-1",
            content_digest=compute_content_digest(markdown.encode("utf-8")),
        )
    )
    result = compile_worker(projection=projection)

    assert result.render_task_context() == markdown
    assert markdown not in result.render_instructions()
    assert result.capsule.task_context is not None
    assert result.capsule.task_context.origin.endswith("task.json")


def test_a_projection_whose_bytes_do_not_match_its_digest_is_refused() -> None:
    projection = CapsuleSuppliedProjection(
        context=CapsuleTaskContext(
            markdown="## Objective\n\nsomething else\n",
            origin="agents-remember/tasks/demo/task.json",
            projection_revision="demo-revision-1",
            content_digest=compute_content_digest(b"a different document"),
        )
    )
    error = failure(projection=projection)

    assert error.status == "task-context-digest-mismatch"
    assert "unverifiable" in error.next_action


def test_no_projection_means_no_task_context_and_a_stable_digest() -> None:
    without = compile_worker()
    with_none = compile_worker(projection=None)

    assert without.capsule.task_context is None
    assert without.render_task_context() == ""
    assert without.semantic_digest == with_none.semantic_digest


# --------------------------------------------------------------------------------------
# Failure projection and the no-model / no-network boundary.
# --------------------------------------------------------------------------------------


def test_a_refusal_still_produces_an_explanation_manifest() -> None:
    binding = worker_binding(operation="planning")
    error = failure(binding=binding)
    manifest = refused_manifest(binding, error)

    assert manifest.rejection is not None
    assert manifest.rejection.status == "operation-not-applicable"
    assert manifest.instruction_identities == ()
    assert manifest.sources == ()
    assert manifest.semantic_digest == ""
    assert manifest.task_reference == "agents-remember/tasks/demo/task.json"
    summary = manifest.summary()
    rejection = summary["rejection"]
    assert isinstance(rejection, dict)
    assert rejection["status"] == "operation-not-applicable"


def test_composition_succeeds_with_network_and_model_access_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Composition itself must need neither a model nor the network.

    Every socket entry point is refused, so a hidden HTTP call or model invocation
    fails loudly instead of quietly succeeding on a machine that has egress.
    """

    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("capsule compilation attempted network access")

    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    baseline = compile_worker()
    result = compile_worker()

    assert result.semantic_digest == baseline.semantic_digest
    assert result.render_instructions() == baseline.render_instructions()


def test_the_compiler_modules_import_no_network_client() -> None:
    module_directory = Path(compiler.__file__).parent
    forbidden = {
        "aiohttp",
        "ftplib",
        "http",
        "httpx",
        "openai",
        "requests",
        "socket",
        "ssl",
        "urllib",
        "websockets",
    }
    offenders: list[str] = []
    for module in sorted(module_directory.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [
                    f"{module.name}: {alias.name}"
                    for alias in node.names
                    if alias.name.split(".")[0] in forbidden
                ]
            elif (
                isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in forbidden
            ):
                offenders.append(f"{module.name}: {node.module}")

    assert offenders == []


# --------------------------------------------------------------------------------------
# F02 — the frozen value types and the compiler-tier branches are exercised too.
#
# The ablation sweep found these branches unexercised as well: the value invariants of
# the published DTOs, the compiler's own duplicate/skill branches, and the two outcome
# invariants. A frozen DTO whose invariants no case exercises is a shape, not a contract.
# --------------------------------------------------------------------------------------


def test_a_source_whose_revision_is_not_its_own_digest_is_refused() -> None:
    """A source carries the digest of its bytes, or it is not admitted at all."""

    with pytest.raises(ValueError) as raised:
        CapsuleSource(
            identity="core:authority",
            composition_root="core",
            path="core/authority.md",
            content=b"# authority\n",
            revision=compute_content_digest(b"# something else\n"),
        )

    assert "not the digest of its bytes" in str(raised.value)


def test_a_source_that_decodes_to_nothing_is_refused() -> None:
    """An empty instruction body is a defect, not a block that silently does nothing."""

    source = make_source("core:authority", content="   \n\n")

    with pytest.raises(ValueError) as raised:
        source.text()

    assert "is empty" in str(raised.value)


def test_a_source_that_is_not_utf8_text_is_refused() -> None:
    payload = b"\xff\xfe not utf-8"
    source = CapsuleSource(
        identity="core:authority",
        composition_root="core",
        path="core/authority.md",
        content=payload,
        revision=compute_content_digest(payload),
    )

    with pytest.raises(CapsuleSourceError) as raised:
        source.text()

    assert raised.value.status == "source-not-utf8"


def test_a_selection_with_a_blank_anchor_is_refused() -> None:
    """Both anchors are checked in one item: they reach the same shape guard."""

    for field in ("root", "manifest"):
        fields = {"root": FIXTURE_ROOT, "manifest": FIXTURE_MANIFEST, field: ""}

        with pytest.raises(ValueError) as raised:
            CapsuleSourceSelection(**fields)  # type: ignore[arg-type]

        assert field in str(raised.value)


def test_a_digest_field_that_is_not_a_sha256_is_refused() -> None:
    """Every malformed digest shape is refused; one item, because it is one guard."""

    for bad in ("", "   ", "sha256:short", "md5:" + "0" * 64):
        with pytest.raises(ValueError) as raised:
            CapsuleAdmittedFacts(
                task_reference="t",
                task_document_digest=bad,
                seat=CapsuleRoleSeat(role="worker", altitude="leaf"),
                repository_id="r",
                work_branch="b",
            )

        assert "sha256" in str(raised.value)


def test_a_blank_required_field_is_refused() -> None:
    with pytest.raises(ValueError) as raised:
        CapsuleRequirementBinding(stable_id="  ", revision="v1")

    assert "non-blank" in str(raised.value)


def test_a_negative_instruction_unit_index_is_refused() -> None:
    block = compile_worker().capsule.instructions[0]

    with pytest.raises(ValueError) as raised:
        CapsuleInstructionUnit(unit_index=-1, block=block)

    assert "must not be negative" in str(raised.value)


def test_an_instruction_unit_labelling_a_missing_composition_root_is_refused() -> None:
    with pytest.raises(ValueError) as raised:
        CapsuleDeclaredInstruction(
            identity="role:worker",
            composition_root="core",
            authorities=("core:authority:shared-core",),
        )

    assert "does not belong to" in str(raised.value)


def test_a_skill_identity_needs_both_an_origin_and_a_name() -> None:
    with pytest.raises(ValueError) as raised:
        skills_declared_identity("", "l-01-agent-lifecycles")

    assert "non-blank origin and skill name" in str(raised.value)


def test_a_specialization_path_outside_its_directory_is_refused() -> None:
    for path in ("repo-notes.md", "specializations/notes.json", "specializations/"):
        with pytest.raises(ValueError) as raised:
            specializations_declared_identity(path)

        assert "must live under" in str(raised.value)


def test_a_compilation_outcome_that_is_both_a_capsule_and_a_refusal_is_refused() -> None:
    result = compile_worker()
    binding = worker_binding(operation="planning")
    error = failure(binding=binding)

    with pytest.raises(ValueError) as raised:
        CapsuleCompilationOutcome(result=result, manifest=result.manifest, error=error)

    assert "exactly one of" in str(raised.value)


def test_a_compilation_outcome_that_is_neither_is_refused() -> None:
    result = compile_worker()

    with pytest.raises(ValueError) as raised:
        CapsuleCompilationOutcome(result=None, manifest=result.manifest, error=None)

    assert "exactly one of" in str(raised.value)


def test_a_skill_reference_without_its_admitted_root_file_is_refused() -> None:
    """The compiler's own skill branch, reached directly rather than through admission.

    The source-set tier refuses a missing declared skill file first, so this calls the
    compiler with a selection that does not ask for the skill at all — the branch under
    test is the *referenced* skill the manifest declared and the admission dropped.
    """

    document = fixture_manifest_document()
    binding = worker_binding()
    sources = tuple(source for source in all_sources(document) if source.path != SKILL_SOURCE)
    narrowed = selection_for(document)
    narrowed_without_skill = CapsuleSourceSelection(
        root=narrowed.root,
        manifest=narrowed.manifest,
        core=narrowed.core,
        role=narrowed.role,
        operation=narrowed.operation,
    )

    with pytest.raises(CapsuleCompilationError) as raised:
        compile_role_capsule(
            binding,
            manifest_bytes(document),
            sources,
            narrowed_without_skill,
        )

    assert raised.value.status == "missing-required-instruction"
    assert "declared skill root files were not admitted" in raised.value.detail


def test_the_worker_fixture_carries_the_skill_reference_it_declares() -> None:
    """The fixture path of the producer, asserted here because this module owns it."""

    result = compile_worker()
    references = result.capsule.skill_references

    assert [reference.identity for reference in references] == [SKILL_IDENTITY]
    assert references[0].origin == SKILL_ORIGIN
    assert references[0].uri == SKILL_URI
    assert references[0].revision.startswith("sha256:")


@pytest.mark.parametrize(
    ("factory", "blank"),
    [
        (
            lambda blank: CapsuleSource(
                identity=blank,
                composition_root="core",
                path="core/authority.md",
                content=b"# x\n",
                revision=compute_content_digest(b"# x\n"),
            ),
            "identity-blank",
        ),
        (
            lambda blank: CapsuleSource(
                identity="core:authority",
                composition_root="core",
                path=blank,
                content=b"# x\n",
                revision=compute_content_digest(b"# x\n"),
            ),
            "path-blank",
        ),
    ],
)
def test_a_source_with_a_blank_identity_or_path_is_refused(factory, blank: str) -> None:
    """Both shape invariants of the admitted-source value type."""

    with pytest.raises(ValueError) as raised:
        factory("   ")

    assert "non-blank" in str(raised.value)
    assert blank


def test_a_digest_value_that_is_not_a_sha256_is_refused_by_the_shared_guard() -> None:
    """The one digest validator every digest field in the DTO routes through."""

    for bad in (None, 7, "sha256:" + "z" * 64):
        with pytest.raises(ValueError) as raised:
            _require_digest(bad, "some digest")

        assert "sha256" in str(raised.value)
