"""Focused checks for source admission, the manifest parser and the frozen vocabulary.

Three boundaries are pinned here, each of which a later leaf consumes:

* the frozen vocabulary is exactly ten roles and nine operations, and the ambient
  launcher is a routing condition rather than a role;
* the canonical composition manifest parses, agrees with that vocabulary, and
  declares no key that no longer exists;
* admitting sources proves containment before it reads a byte, and preserves
  content-addressed revisions, so "the same bytes compile to the same capsule" is a
  property of what was read rather than of what was requested; and
* the shipped corpus compiles end to end from disk, twice, to the same digest.

The last case is the only one that touches the real tree; everything else builds its
own fixture so a failure names one boundary instead of a whole corpus.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, get_args

import pytest
from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.role_capsules.sources import (
    CapsuleAdmissionRequest,
    admit_capsule_sources,
)
from agents_remember.errors import CapsuleCompilationError, CapsuleSourceError
from agents_remember.models.role_capsules.compiler import compile_role_capsule
from agents_remember.models.role_capsules.manifest import (
    COMPOSITION_MANIFEST_SCHEMA,
    CapsuleCompositionManifest,
    parse_composition_manifest,
)
from agents_remember.models.role_capsules.selection import narrow_role
from agents_remember.models.role_capsules.source_set import admit_source_set
from agents_remember.models.role_capsules.sources import (
    CapsuleDeclaredInstruction,
    CapsuleSource,
    instruction_identity,
    skills_declared_identity,
)
from agents_remember.models.role_capsules.statuses import CAPSULE_STATUSES
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleLauncherSeat,
    CapsuleOverride,
    CapsuleRoleSeat,
    CapsuleSourceAdmission,
    CapsuleSourceSelection,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.models.role_capsules.vocabulary import (
    CAPSULE_COMPOSITION_ORDER,
    CAPSULE_LAUNCHER_MODE,
    CAPSULE_OPERATIONS,
    CAPSULE_ROLES,
    CapsuleOperation,
    CapsuleRole,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE_ROOT = REPOSITORY_ROOT / "skills" / "l-01-agent-lifecycles"
MANIFEST_RELATIVE = "composition-manifest.json"

# The ten roles the corpus ships, and the nine frozen operations. Restated here
# rather than imported from the compiler so the two definitions must agree: an
# imported constant would make this case assert that a tuple equals itself.
SHIPPED_ROLES = (
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

SHIPPED_OPERATIONS = (
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


@pytest.fixture
def corpus_tree(tmp_path: Path) -> Iterator[Path]:
    """A tiny canonical tree on disk, with one core, one role and one operation."""

    root = tmp_path / "l-01-agent-lifecycles"
    (root / "core").mkdir(parents=True)
    (root / "roles").mkdir()
    (root / "operations").mkdir()
    (root / "core" / "authority.md").write_text("# authority\n", encoding="utf-8")
    (root / "roles" / "worker.md").write_text("# worker\n", encoding="utf-8")
    (root / "operations" / "implementation.md").write_text("# implementation\n", encoding="utf-8")
    (root / MANIFEST_RELATIVE).write_text(json.dumps({"schema": "fixture"}), encoding="utf-8")
    yield root


def request_for(root: Path, **overrides: object) -> CapsuleAdmissionRequest:
    defaults: dict[str, object] = {
        "root": root,
        "manifest": MANIFEST_RELATIVE,
        "core": ("core/authority.md",),
        "role": ("roles/worker.md",),
        "operation": ("operations/implementation.md",),
    }
    defaults.update(overrides)
    return CapsuleAdmissionRequest(**defaults)  # type: ignore[arg-type]


def worker_binding(*, tool_policy: CapsuleToolPolicy | None = None) -> CapsuleBinding:
    return CapsuleBinding(
        operation="implementation",
        admitted=CapsuleAdmittedFacts(
            task_reference="agents-remember/tasks/demo/task.json",
            task_document_digest=compute_content_digest(b'{"id": "demo"}'),
            seat=CapsuleRoleSeat(role="worker", altitude="leaf"),
            repository_id="agents-remember",
            work_branch="ar/260915-caps-l2",
            tool_policy=tool_policy or CapsuleToolPolicy(granted=frozenset()),
        ),
    )


# --------------------------------------------------------------------------------------
# Vocabulary.
# --------------------------------------------------------------------------------------


def test_the_frozen_vocabulary_is_exactly_the_ten_roles_and_nine_operations() -> None:
    assert CAPSULE_ROLES == SHIPPED_ROLES
    assert len(CAPSULE_ROLES) == 10
    assert CAPSULE_OPERATIONS == SHIPPED_OPERATIONS
    assert len(CAPSULE_OPERATIONS) == 9


def test_the_role_and_operation_literals_agree_with_their_runtime_tuples() -> None:
    """The registry has two readers (type checker, runtime) and must not drift."""

    assert get_args(CapsuleRole.__value__) == CAPSULE_ROLES
    assert get_args(CapsuleOperation.__value__) == CAPSULE_OPERATIONS


def test_the_launcher_is_a_seat_kind_and_not_a_role() -> None:
    assert CAPSULE_LAUNCHER_MODE == "launcher"
    assert CAPSULE_LAUNCHER_MODE not in CAPSULE_ROLES
    assert CAPSULE_COMPOSITION_ORDER == ("core", "role", "operation", "specialization")


def test_every_documented_refusal_code_is_registered_exactly_once() -> None:
    assert len(CAPSULE_STATUSES) == len(set(CAPSULE_STATUSES))
    assert set(CAPSULE_STATUSES) >= {
        "unknown-role",
        "unknown-operation",
        "operation-not-applicable",
        "missing-required-instruction",
        "duplicate-identity",
        "equal-authority-contradiction",
        "tool-request-not-permitted",
        "task-context-digest-mismatch",
    }


# --------------------------------------------------------------------------------------
# The shipped manifest.
# --------------------------------------------------------------------------------------


def test_the_shipped_manifest_parses_and_agrees_with_the_frozen_vocabulary() -> None:
    payload = (LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes()
    parsed = parse_composition_manifest(payload)

    assert parsed.schema == COMPOSITION_MANIFEST_SCHEMA
    assert tuple(sorted(parsed.roles)) == tuple(sorted(SHIPPED_ROLES))
    assert tuple(sorted(parsed.operations)) == tuple(sorted(SHIPPED_OPERATIONS))
    assert tuple(sorted(parsed.role_order)) == tuple(sorted(SHIPPED_ROLES))
    assert parsed.launcher.is_role is False
    assert CAPSULE_LAUNCHER_MODE in parsed.launcher.core


def test_every_role_and_operation_the_shipped_manifest_declares_has_a_source() -> None:
    parsed = parse_composition_manifest((LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes())

    missing = sorted(
        source
        for source in (
            *(entry.file for entry in parsed.roles.values()),
            *(entry.source for entry in parsed.operations.values()),
            *(entry.source for entry in parsed.core.values()),
        )
        if not (LIFECYCLE_ROOT / source).is_file()
    )
    specializations = sorted(
        entry.source
        for entry in parsed.specializations.values()
        if not (LIFECYCLE_ROOT / entry.source).is_file()
    )

    assert missing == []
    assert specializations == []


def test_every_tool_the_shipped_manifest_requests_exists_in_the_public_roster() -> None:
    """A requested tool identity must be one the MCP surface actually publishes."""

    parsed = parse_composition_manifest((LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes())
    declared = {tool for role in parsed.roles.values() for tool in role.tools}

    assert declared, "the manifest must request at least one tool identity to exercise this check"
    assert sorted(declared - set(PUBLIC_TOOLS)) == []


def test_a_manifest_that_disagrees_with_the_frozen_vocabulary_is_refused() -> None:
    payload = (LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes()
    document = json.loads(payload)
    document["operations"]["deployment"] = document["operations"].pop("implementation")

    with pytest.raises(CapsuleCompilationError) as raised:
        parse_composition_manifest(json.dumps(document).encode("utf-8"))

    assert raised.value.status == "manifest-vocabulary-mismatch"
    assert "deployment" in raised.value.detail


def test_a_manifest_whose_applicability_contradicts_itself_is_refused() -> None:
    """The two spellings of applicability must agree, so no winner is picked later."""

    payload = (LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes()
    document = json.loads(payload)
    document["operations"]["implementation"]["applies_to_roles"] = list(SHIPPED_ROLES)

    with pytest.raises(CapsuleCompilationError) as raised:
        parse_composition_manifest(json.dumps(document).encode("utf-8"))

    assert raised.value.status == "manifest-inconsistent-applicability"
    assert "implementation" in raised.value.detail


def test_a_manifest_that_routes_two_identities_at_one_file_is_refused() -> None:
    payload = (LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes()
    document = json.loads(payload)
    document["core"]["extra"] = {
        "source": document["core"]["authority"]["source"],
        "purpose": "a second identity claiming the same file",
    }

    with pytest.raises(CapsuleCompilationError) as raised:
        parse_composition_manifest(json.dumps(document).encode("utf-8"))

    assert raised.value.status == "manifest-vocabulary-mismatch"
    assert "more than one instruction identity" in raised.value.detail


# --------------------------------------------------------------------------------------
# Admission.
# --------------------------------------------------------------------------------------


def test_admission_reads_every_requested_source_with_its_content_digest(
    corpus_tree: Path,
) -> None:
    sources = admit_capsule_sources(request_for(corpus_tree))

    assert [source.path for source in sources] == [
        MANIFEST_RELATIVE,
        "core/authority.md",
        "roles/worker.md",
        "operations/implementation.md",
    ]
    for source in sources:
        assert source.revision == compute_content_digest(source.content)


def test_admission_is_reproducible_over_one_unchanged_tree(corpus_tree: Path) -> None:
    first = admit_capsule_sources(request_for(corpus_tree))
    second = admit_capsule_sources(request_for(corpus_tree))

    assert first == second


@pytest.mark.parametrize(
    "escaping",
    ["../outside.md", "/etc/passwd", "core/../../outside.md", ""],
)
def test_admission_refuses_a_path_that_escapes_its_root(corpus_tree: Path, escaping: str) -> None:
    """Containment is proven before any byte is read."""

    request = request_for(corpus_tree, core=(escaping,))
    with pytest.raises(CapsuleSourceError) as raised:
        admit_capsule_sources(request)

    assert raised.value.status in {"source-path-escapes-root", "source-path-invalid"}


def test_admission_refuses_a_missing_source_instead_of_skipping_it(corpus_tree: Path) -> None:
    request = request_for(corpus_tree, core=("core/absent.md",))
    with pytest.raises(CapsuleSourceError) as raised:
        admit_capsule_sources(request)

    assert raised.value.status == "source-missing"
    assert "core/absent.md" in raised.value.detail


def test_admission_refuses_a_root_that_is_not_a_directory(tmp_path: Path) -> None:
    with pytest.raises(CapsuleSourceError) as raised:
        admit_capsule_sources(request_for(tmp_path / "absent"))

    assert raised.value.status == "source-root-missing"


def test_admission_refuses_the_same_path_requested_twice(corpus_tree: Path) -> None:
    request = request_for(corpus_tree, core=("core/authority.md", "core/authority.md"))
    with pytest.raises(CapsuleSourceError) as raised:
        admit_capsule_sources(request)

    assert raised.value.status == "duplicate-identity"


def test_an_unreadable_source_returns_a_refusal_rather_than_raising(
    corpus_tree: Path,
) -> None:
    """A caller that has to explain a failure gets the manifest as well as the error."""

    outcome = compile_admitted_capsule(
        worker_binding(), request_for(corpus_tree, core=("core/absent.md",))
    )

    assert not outcome.ok
    assert outcome.result is None
    assert outcome.capsule is None
    assert outcome.error is not None
    assert outcome.error.status == "source-missing"
    assert outcome.manifest.task_reference == "agents-remember/tasks/demo/task.json"
    assert outcome.manifest.sources == ()
    assert "source-missing" in outcome.render_explanation()

    # The same "a refusal, not a raise" boundary for an EMPTIED source (defect D25). It needs the
    # SHIPPED corpus rather than the tiny fixture above, because emptiness is discovered while the
    # blocks are composed — after admission succeeded and after a real manifest parsed — so the
    # seed is written into a disposable copy of the shipped tree and the real application boundary
    # is driven over it. Before the repair this call raised an uncaught ``ValueError`` out of
    # ``CapsuleSource.text`` instead of returning anything, which is what makes the seed failable.
    emptied_root = corpus_tree.parent / "shipped-copy" / "l-01-agent-lifecycles"
    shutil.copytree(LIFECYCLE_ROOT, emptied_root)
    emptied_path = _shipped_parsed().core["acceptance"].source
    target = emptied_root / emptied_path
    assert target.read_text(encoding="utf-8").strip(), (
        "the seed must empty a source that really carried content"
    )
    target.write_text("", encoding="utf-8")
    shipped = _shipped_request("worker", "implementation")
    emptied = compile_admitted_capsule(
        _shipped_binding("worker", "implementation"),
        CapsuleAdmissionRequest(
            root=emptied_root,
            manifest=MANIFEST_RELATIVE,
            core=shipped.core,
            role=shipped.role,
            operation=shipped.operation,
            skill=shipped.skill,
        ),
    )

    assert not emptied.ok, "an emptied required instruction block must not compile"
    assert emptied.error is not None, "the refusal is a value, not an escaping exception"
    assert emptied.error.status == "source-empty"
    assert emptied_path in emptied.error.detail
    assert "source-empty" in emptied.render_explanation()


# --------------------------------------------------------------------------------------
# The shipped corpus end to end.
# --------------------------------------------------------------------------------------


def _shipped_parsed() -> CapsuleCompositionManifest:
    return parse_composition_manifest((LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes())


def _shipped_request(
    role: str,
    operation: str,
    *,
    core: tuple[str, ...] | None = None,
    skills: bool = True,
) -> CapsuleAdmissionRequest:
    parsed = _shipped_parsed()
    entry = parsed.roles[role]
    declared_core = (
        core if core is not None else tuple(parsed.core[name].source for name in entry.core)
    )
    return CapsuleAdmissionRequest(
        root=LIFECYCLE_ROOT,
        manifest=MANIFEST_RELATIVE,
        core=declared_core,
        role=(entry.file,),
        operation=(parsed.operations[operation].source,),
        skill=(
            tuple(entry_skill.source for entry_skill in parsed.skills.values()) if skills else ()
        ),
    )


def _shipped_binding(role: str, operation: str) -> CapsuleBinding:
    parsed = _shipped_parsed()
    return CapsuleBinding(
        operation=operation,  # type: ignore[arg-type]
        admitted=CapsuleAdmittedFacts(
            task_reference="agents-remember/tasks/demo/task.json",
            task_document_digest=compute_content_digest(b'{"id": "demo"}'),
            seat=CapsuleRoleSeat(role=role, altitude=parsed.roles[role].altitude),  # type: ignore[arg-type]
            repository_id="agents-remember",
            work_branch="ar/260915-caps-l2",
            tool_policy=CapsuleToolPolicy(
                granted=frozenset(tool for entry in parsed.roles.values() for tool in entry.tools)
            ),
        ),
    )


# The role files declare their shared inputs on their own `**Inherits:**` line. That is a
# genuinely independent source for the expected routing: the manifest does not feed it, so a
# routing block dropped from the manifest moves only one side of the comparison. Deriving the
# expectation from the manifest instead made the case tautological — the round-1 seed that
# dropped `core:acceptance` from architect's routing left every case green.
ROLE_FILE_ROOT = LIFECYCLE_ROOT / "roles"
INHERITS_TOKEN = re.compile(r"`((?:core|operations)/[a-z0-9._-]+\.md)`")

#: The one place the manifest's operation vocabulary and the corpus's file names differ:
#: the operation id is `authorized-closeout`, the block is `operations/closeout.md`.
OPERATION_BLOCK_ALIASES = {"authorized-closeout": "closeout"}


def declared_inherits(role: str) -> tuple[set[str], set[str]]:
    """The core blocks and operations one role file declares on its own `Inherits:` line."""

    text = (ROLE_FILE_ROOT / f"{role}.md").read_text(encoding="utf-8")
    start = text.index("**Inherits:**")
    block = text[start : text.index("\n\n", start)]
    tokens = INHERITS_TOKEN.findall(block)
    core = {token.split("/", 1)[1][: -len(".md")] for token in tokens if token.startswith("core/")}
    operations = {
        token.split("/", 1)[1][: -len(".md")] for token in tokens if token.startswith("operations/")
    }
    return core, operations


def test_every_role_file_declares_the_core_blocks_and_operations_it_inherits() -> None:
    """The corpus's own statement of each role's inputs, read without the manifest.

    Asserted first and separately so a failure names the *declaration* rather than a
    compilation: this is the independent side of the routing comparison below.
    """

    declared = {role: declared_inherits(role) for role in SHIPPED_ROLES}

    for role, (core, operations) in declared.items():
        assert core, f"{role} declares no core block on its Inherits: line"
        assert operations, f"{role} declares no operation on its Inherits: line"

    assert declared["architect"][0] == {
        "authority",
        "invariants",
        "lifecycle-frame",
        "loop",
        "acceptance",
    }
    assert declared["worker"][1] == {"orientation", "implementation", "recovery"}
    assert declared["architect"][1] == {
        "orientation",
        "planning",
        "coordination",
        "review",
        "recovery",
    }
    assert declared["orchestrator"][1] == {
        "orientation",
        "planning",
        "coordination",
        "review",
        "closeout",
        "recovery",
    }


def test_every_shipped_role_compiles_to_the_routing_its_own_role_file_declares() -> None:
    """The composed routing is checked against the role FILE, not against the manifest.

    The expected set comes from `roles/<role>.md`'s `Inherits:` line and the composed set
    from the compiler. Both the manifest's declared routing and the compilation are
    compared to that independent statement, so either a manifest routing loss or a
    composition defect moves exactly one side and fails here — for all nine roles.

    What this case does **not** claim: it checks the two independently-declared sets, so a
    change made consistently to both the manifest and the role file is a corpus edit, not a
    regression, and is correctly invisible.
    """

    parsed = _shipped_parsed()

    for role in SHIPPED_ROLES:
        declared_core, declared_operations = declared_inherits(role)
        expected_operations = {
            OPERATION_BLOCK_ALIASES.get(name, name) for name in parsed.roles[role].operations
        }
        manifest_core = set(parsed.roles[role].core)

        assert manifest_core == declared_core, (
            f"{role}: the manifest routes {sorted(manifest_core)} but the role file "
            f"declares {sorted(declared_core)}"
        )
        assert expected_operations == declared_operations, (
            f"{role}: the manifest routes operations {sorted(expected_operations)} but the "
            f"role file declares {sorted(declared_operations)}"
        )

        for operation in parsed.roles[role].operations:
            composed = compile_admitted_capsule(
                _shipped_binding(role, operation), _shipped_request(role, operation)
            )
            assert composed.ok, f"{role}/{operation}: {composed.render_explanation()}"
            assert composed.result is not None
            identities = {block.identity for block in composed.result.capsule.instructions}
            assert identities == {
                *(f"core:{name}" for name in declared_core),
                f"role:{role}",
                f"operation:{operation}",
            }, f"{role}/{operation}"
            assert not any(
                identity.startswith("operation:") and identity != f"operation:{operation}"
                for identity in identities
            ), f"{role}/{operation} composed a foreign operation block"


def test_every_shipped_role_compiles_deterministically_under_every_declared_operation() -> None:
    """Two compilations per declared operation agree in ordered content and digest."""

    parsed = _shipped_parsed()

    for role in SHIPPED_ROLES:
        assert parsed.roles[role].operations, f"{role} declares no operation"
        for operation in parsed.roles[role].operations:
            first = compile_admitted_capsule(
                _shipped_binding(role, operation), _shipped_request(role, operation)
            )
            second = compile_admitted_capsule(
                _shipped_binding(role, operation), _shipped_request(role, operation)
            )

            assert first.ok, f"{role}/{operation}: {first.render_explanation()}"
            assert second.ok, f"{role}/{operation}: {second.render_explanation()}"
            assert first.semantic_digest == second.semantic_digest, f"{role}/{operation}"
            assert first.result is not None and second.result is not None
            assert first.result.render_instructions() == second.result.render_instructions()
            assert first.result.render_instructions().strip()


def test_a_worker_capsule_composes_only_its_own_role_block() -> None:
    """The delivered result is self-contained: no sibling role BLOCK is composed in.

    A sibling role's file may legitimately be *named* by shared or worker text (the
    escalation ladder names the owning seat). What must not happen is another role's
    instruction BODY being composed into this capsule, which is what the block
    identities prove.
    """

    outcome = compile_admitted_capsule(
        _shipped_binding("worker", "implementation"), _shipped_request("worker", "implementation")
    )

    assert outcome.ok
    assert outcome.result is not None
    identities = [block.identity for block in outcome.result.capsule.instructions]
    assert identities == [
        "core:acceptance",
        "core:authority",
        "core:invariants",
        "core:lifecycle-frame",
        "role:worker",
        "operation:implementation",
    ]
    role_blocks = [
        block for block in outcome.result.capsule.instructions if block.composition_root == "role"
    ]
    assert [block.identity for block in role_blocks] == ["role:worker"]
    rendered = outcome.result.render_instructions()
    for sibling in SHIPPED_ROLES:
        if sibling == "worker":
            continue
        sibling_text = (LIFECYCLE_ROOT / "roles" / f"{sibling}.md").read_text(encoding="utf-8")
        body = sibling_text.split("\n", 1)[1][:200]
        assert body not in rendered


# --------------------------------------------------------------------------------------
# F01 — the skill-reference plane, asserted against the SHIPPED corpus.
# --------------------------------------------------------------------------------------


def test_a_shipped_role_declaring_a_skill_actually_carries_the_reference() -> None:
    """The producer is asserted on the real corpus, not on the fixture alone."""

    outcome = compile_admitted_capsule(
        _shipped_binding("worker", "implementation"), _shipped_request("worker", "implementation")
    )

    assert outcome.ok, outcome.render_explanation()
    assert outcome.result is not None
    references = outcome.result.capsule.skill_references
    assert len(references) == 1
    reference = references[0]
    assert reference.identity == "agents-remember/skills#l-01-agent-lifecycles"
    assert reference.origin == "agents-remember/skills"
    assert reference.uri == "skill://agents-remember/skills/l-01-agent-lifecycles"
    assert reference.revision.startswith("sha256:")
    # The revision is the digest of the admitted skill root file, not a constant.
    assert reference.revision == compute_content_digest((LIFECYCLE_ROOT / "SKILL.md").read_bytes())


def test_every_shipped_role_that_declares_a_skill_carries_one_reference_per_declaration() -> None:
    parsed = _shipped_parsed()
    for role in SHIPPED_ROLES:
        operation = parsed.roles[role].operations[0]
        outcome = compile_admitted_capsule(
            _shipped_binding(role, operation), _shipped_request(role, operation)
        )
        assert outcome.ok, f"{role}: {outcome.render_explanation()}"
        assert outcome.result is not None
        declared = parsed.roles[role].skills
        assert [r.identity for r in outcome.result.capsule.skill_references] == [
            f"{parsed.skills[name].origin}#{name}" for name in declared
        ], role


def test_a_role_declaring_no_skills_returns_an_empty_reference_tuple() -> None:
    """Optional means a seat that declares none gets none — not a fabricated one.

    Every shipped role currently declares the same skill, so "declares none" is
    exercised against the shipped corpus in a copy whose worker entry declares no
    skill — the real files, the real manifest, one metadata field changed. The skill
    stays declared at the top level, so this isolates the *reference* plane from the
    declaration plane.
    """

    document = json.loads((LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_text(encoding="utf-8"))
    document["roles"]["worker"]["skills"] = []
    payload = json.dumps(document).encode("utf-8")
    parsed = parse_composition_manifest(payload)
    assert parsed.roles["worker"].skills == ()
    assert parsed.skills, "the declaration plane must survive this mutation"

    sources = tuple(
        source
        for source in admit_capsule_sources(_shipped_request("worker", "implementation"))
        if source.path != MANIFEST_RELATIVE
    )
    manifest_source = CapsuleSource(
        identity="meta:composition-manifest",
        composition_root="core",
        path=MANIFEST_RELATIVE,
        content=payload,
        revision=compute_content_digest(payload),
    )
    outcome = compile_role_capsule(
        _shipped_binding("worker", "implementation"),
        payload,
        (*sources, manifest_source),
        _shipped_request("worker", "implementation").as_selection(),
    )

    assert outcome.capsule.skill_references == ()


def test_the_skill_revision_follows_the_admitted_skill_bytes() -> None:
    """The revision is content-addressed: different skill bytes, different revision."""

    baseline = compile_admitted_capsule(
        _shipped_binding("worker", "implementation"), _shipped_request("worker", "implementation")
    )
    request = _shipped_request("worker", "implementation")
    sources = tuple(
        source for source in admit_capsule_sources(request) if source.path != "SKILL.md"
    )
    revised = b"# A different skill body\n"
    pinned = type(sources[0])(
        identity=skills_declared_identity("agents-remember/skills", "l-01-agent-lifecycles"),
        composition_root="skill",
        path="SKILL.md",
        content=revised,
        revision=compute_content_digest(revised),
    )
    mutated = compile_role_capsule(
        _shipped_binding("worker", "implementation"),
        (LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes(),
        (*sources, pinned),
        request.as_selection(),
    )

    assert baseline.ok and baseline.result is not None
    before = baseline.result.capsule.skill_references[0].revision
    after = mutated.capsule.skill_references[0].revision
    assert before != after
    assert after == compute_content_digest(revised)
    assert mutated.semantic_digest != baseline.result.semantic_digest


# --------------------------------------------------------------------------------------
# F03 — shipped-corpus coverage that is not tautological.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", SHIPPED_ROLES)
def test_every_shipped_role_composes_exactly_its_manifest_declared_routing(role: str) -> None:
    """The composed identity list is asserted against the manifest's declared set.

    This case does NOT derive its expectation from the compiled result: the expected
    set is read from the manifest's routing metadata and compared to what the compiler
    composed. Dropping a block from a role's routing therefore fails here, for every
    role, which the compile-twice case cannot see because it builds its own request.
    """

    parsed = _shipped_parsed()
    entry = parsed.roles[role]
    operation = entry.operations[0]

    expected = sorted(
        [
            *(f"core:{name}" for name in entry.core),
            f"role:{role}",
            f"operation:{operation}",
        ]
    )
    outcome = compile_admitted_capsule(
        _shipped_binding(role, operation), _shipped_request(role, operation)
    )

    assert outcome.ok, outcome.render_explanation()
    assert outcome.result is not None
    composed = sorted(block.identity for block in outcome.result.capsule.instructions)
    assert composed == expected


def test_the_launcher_seat_compiles_from_the_shipped_manifest() -> None:
    """The one addition the compiler relies on had no shipped-corpus case at all."""

    parsed = _shipped_parsed()
    operation = parsed.launcher.operations[0]
    binding = CapsuleBinding(
        operation=operation,
        admitted=CapsuleAdmittedFacts(
            task_reference="agents-remember/tasks/demo/task.json",
            task_document_digest=compute_content_digest(b'{"id": "demo"}'),
            seat=CapsuleLauncherSeat(routing_condition=parsed.launcher.routing_condition),
            repository_id="agents-remember",
            work_branch="ar/260915-caps-l2",
        ),
    )
    request = CapsuleAdmissionRequest(
        root=LIFECYCLE_ROOT,
        manifest=MANIFEST_RELATIVE,
        core=tuple(parsed.core[name].source for name in parsed.launcher.core),
        operation=(parsed.operations[operation].source,),
        skill=tuple(entry.source for entry in parsed.skills.values()),
    )

    outcome = compile_admitted_capsule(binding, request)

    assert outcome.ok, outcome.render_explanation()
    assert outcome.result is not None
    assert sorted(block.identity for block in outcome.result.capsule.instructions) == sorted(
        [
            *(f"core:{name}" for name in parsed.launcher.core),
            f"operation:{operation}",
        ]
    )
    assert outcome.result.manifest.role is None
    assert outcome.result.manifest.seat_kind == "launcher"


def test_emptying_the_launcher_operations_refuses_instead_of_compiling() -> None:
    """The seed the reviewer used to prove the gap: emptying launcher.operations."""

    document = json.loads((LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_text(encoding="utf-8"))
    document["launcher"]["operations"] = []
    parsed = parse_composition_manifest(json.dumps(document).encode("utf-8"))
    assert parsed.launcher.operations == ()
    binding = CapsuleBinding(
        operation="orientation",
        admitted=CapsuleAdmittedFacts(
            task_reference="agents-remember/tasks/demo/task.json",
            task_document_digest=compute_content_digest(b'{"id": "demo"}'),
            seat=CapsuleLauncherSeat(routing_condition=parsed.launcher.routing_condition),
            repository_id="agents-remember",
            work_branch="ar/260915-caps-l2",
        ),
    )

    with pytest.raises(CapsuleCompilationError) as raised:
        compile_role_capsule(
            binding,
            json.dumps(document).encode("utf-8"),
            admit_capsule_sources(
                CapsuleAdmissionRequest(
                    root=LIFECYCLE_ROOT,
                    manifest=MANIFEST_RELATIVE,
                    core=tuple(parsed.core[name].source for name in parsed.launcher.core),
                    operation=(parsed.operations["orientation"].source,),
                    skill=tuple(entry.source for entry in parsed.skills.values()),
                )
            ),
            CapsuleSourceSelection(
                root=str(LIFECYCLE_ROOT),
                manifest=MANIFEST_RELATIVE,
                core=tuple(parsed.core[name].source for name in parsed.launcher.core),
                operation=(parsed.operations["orientation"].source,),
                skill=tuple(entry.source for entry in parsed.skills.values()),
            ),
        )

    assert raised.value.status == "operation-not-applicable"
    assert "launcher" in raised.value.detail


# --------------------------------------------------------------------------------------
# F02 — every refusal branch of the manifest parser and the source set is exercised.
#
# The ablation sweep (notes/reports/caps-l2-ablation-sweep.py) found these branches
# unexercised. Each case below exists so that disabling its guard fails a named case;
# the sweep re-run is the proof, and it is mechanical rather than a hand list.
# --------------------------------------------------------------------------------------


def _shipped_document() -> dict:
    return json.loads((LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_text(encoding="utf-8"))


def _refuse(payload: bytes) -> str:
    """The status of the refusal a payload produces, or a clear failure if it compiles."""

    try:
        parse_composition_manifest(payload)
    except CapsuleCompilationError as error:
        return error.status
    raise AssertionError("the manifest was accepted; this case exists to see it refused")


def _manifest_with(mutate) -> bytes:
    document = _shipped_document()
    mutate(document)
    return json.dumps(document).encode("utf-8")


#: One entry per parser refusal branch: the mutation that triggers it and the status the
#: parser must answer with. Kept as data so the ablation sweep can name the branch a
#: disabled guard belongs to, and so a new guard without an entry is visible as a gap.
MANIFEST_DEFECTS: dict[str, dict[str, Callable[[dict[str, Any]], object]]] = {
    "manifest-vocabulary-mismatch": {
        "schema-disagreement": lambda d: d.__setitem__("schema", "ar-role-capsule-composition/v99"),
        "role-order-disagrees-with-roles": lambda d: d["role_order"].pop(),
        "role-declares-no-core": lambda d: d["roles"]["worker"].__setitem__("core", []),
        "role-names-unknown-core": lambda d: d["roles"]["worker"]["core"].append("no-such-block"),
        "role-names-unknown-operation": lambda d: d["roles"]["worker"]["operations"].append("x"),
        "operation-applies-to-unknown-role": lambda d: d["operations"]["implementation"][
            "applies_to_roles"
        ].append("janitor"),
        "role-references-undeclared-skill": lambda d: d["roles"]["worker"]["skills"].append("x"),
        "launcher-claims-to-be-a-role": lambda d: d["launcher"].__setitem__("is_role", True),
        "launcher-names-unknown-core": lambda d: d["launcher"].__setitem__(
            "core", ["no-such-core"]
        ),
        "launcher-names-unknown-operation": lambda d: d["launcher"].__setitem__(
            "operations", ["x"]
        ),
        "specialization-declared-twice": lambda d: d.__setitem__(
            "specializations",
            [
                {"name": "dup", "source": "specializations/a.md", "purpose": "x"},
                {"name": "dup", "source": "specializations/b.md", "purpose": "y"},
            ],
        ),
        "registry-is-missing-a-frozen-role": lambda d: (
            d["roles"].pop("worker"),
            d["role_order"].remove("worker"),
        ),
        "registry-names-a-role-the-frozen-set-does-not": lambda d: (
            d["roles"].__setitem__("janitor", d["roles"].pop("worker")),
            d.__setitem__(
                "role_order", ["janitor" if r == "worker" else r for r in d["role_order"]]
            ),
        ),
        "two-identities-at-one-file": lambda d: d["core"].__setitem__(
            "shadow", {"source": d["core"]["authority"]["source"], "purpose": "clash"}
        ),
    },
    "manifest-invalid": {
        "core-source-not-a-string": lambda d: d["core"]["authority"].__setitem__("source", 7),
        "core-source-blank": lambda d: d["core"]["authority"].__setitem__("source", "   "),
        "core-object-not-an-object": lambda d: d.__setitem__("core", "not-an-object"),
        "core-list-not-an-array": lambda d: d["roles"]["worker"].__setitem__("core", "authority"),
        "core-list-entry-blank": lambda d: d["roles"]["worker"]["core"].append(""),
        "skills-entry-not-an-object": lambda d: d["skills"].__setitem__("extra", "not-an-object"),
        "specializations-not-an-array": lambda d: d.__setitem__("specializations", "not-an-array"),
        "specialization-entry-not-an-object": lambda d: d.__setitem__(
            "specializations", ["not-an-object"]
        ),
        "applies-to-roles-not-an-array": lambda d: d["operations"]["implementation"].__setitem__(
            "applies_to_roles", {}
        ),
    },
    "manifest-inconsistent-applicability": {
        "applicability-self-contradiction": lambda d: d["operations"]["implementation"][
            "applies_to_roles"
        ].remove("worker"),
    },
}

#: Payloads that are not a readable JSON document at all.
MANIFEST_UNREADABLE: dict[str, bytes] = {
    "not-json": b"NOT JSON AT ALL",
    "not-utf8": b"\xff\xfe\x00",
}


def test_the_manifest_parser_refuses_each_metadata_defect() -> None:
    """Every parser refusal branch, asserted by name so a disabled guard fails here.

    The defects are data rather than one parametrized item each, because the population
    is the budget and these all reach the same assertion: what must hold is that each
    *branch* is exercised, which the ablation sweep checks mechanically.
    """

    exercised: list[str] = []
    for expectation, defects in MANIFEST_DEFECTS.items():
        for name, mutate in defects.items():
            assert _refuse(_manifest_with(mutate)) == expectation, name
            exercised.append(name)
    for name, payload in MANIFEST_UNREADABLE.items():
        assert _refuse(payload) == "manifest-invalid", name
        exercised.append(name)

    assert len(exercised) == len(set(exercised))
    assert "registry-is-missing-a-frozen-role" in exercised
    assert "applies-to-roles-not-an-array" in exercised


def test_a_role_whose_routing_needs_an_absent_launcher_block_is_refused() -> None:
    """The launcher's own core block must exist, not merely be named."""

    def mutate(document: dict) -> None:
        del document["core"]["launcher"]
        document["core"]["extra"] = {"source": "core/extra.md", "purpose": "filler"}

    assert _refuse(_manifest_with(mutate)) == "manifest-vocabulary-mismatch"


def test_a_source_set_with_no_admitted_bytes_is_refused() -> None:
    """The empty-set branch of the source set, called directly.

    Reached directly rather than through ``compile_admitted_capsule`` because the
    application boundary legitimately runs first and refuses an unreadable manifest —
    this case is about the *source-set* branch, which needs a selection and an empty
    mapping to reach at all.
    """

    parsed = _shipped_parsed()
    with pytest.raises(CapsuleCompilationError) as raised:
        admit_source_set(
            parsed,
            CapsuleSourceAdmission(
                binding=_shipped_binding("worker", "implementation"),
                selection=_shipped_request("worker", "implementation").as_selection(),
            ),
            {},
            {},
            (),
        )

    assert raised.value.status == "missing-required-instruction"
    assert "no instruction source bytes were admitted" in raised.value.detail


def test_a_duplicate_path_admitted_to_the_compiler_is_refused() -> None:
    """The compiler-tier duplicate guard, distinct from the application-tier one."""

    parsed = _shipped_parsed()
    entry = parsed.roles["worker"]
    selection = _shipped_request("worker", "implementation").as_selection()
    request = _shipped_request("worker", "implementation")
    core = tuple(parsed.core[name].source for name in entry.core)
    duplicated = (*admit_capsule_sources(request), admit_capsule_sources(request)[1])

    with pytest.raises(CapsuleCompilationError) as raised:
        compile_role_capsule(
            _shipped_binding("worker", "implementation"),
            (LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes(),
            duplicated,
            selection,
        )

    assert raised.value.status == "duplicate-identity"
    assert "was admitted twice" in raised.value.detail
    assert core


def test_an_admitted_set_without_the_composition_manifest_is_refused() -> None:
    """Selection cannot be validated against a manifest that was never admitted.

    The compiler-tier branch is reached directly: through the application boundary a
    manifest the admission cannot read refuses first as ``source-missing``, which is
    also correct but is a different branch. Both are asserted, so neither can be
    disabled without a case failing.
    """

    parsed = _shipped_parsed()
    entry = parsed.roles["worker"]
    selection = CapsuleSourceSelection(
        root=str(LIFECYCLE_ROOT),
        manifest="absent-manifest.json",
        core=tuple(parsed.core[name].source for name in entry.core),
        role=(entry.file,),
        operation=(parsed.operations["implementation"].source,),
    )
    request = _shipped_request("worker", "implementation")
    sources = admit_capsule_sources(request)

    with pytest.raises(CapsuleCompilationError) as raised:
        compile_role_capsule(
            _shipped_binding("worker", "implementation"),
            (LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes(),
            sources,
            selection,
        )
    assert raised.value.status == "source-not-declared"
    assert "does not include the composition manifest" in raised.value.detail

    unreadable = compile_admitted_capsule(
        _shipped_binding("worker", "implementation"),
        CapsuleAdmissionRequest(
            root=LIFECYCLE_ROOT,
            manifest="absent-manifest.json",
            core=tuple(parsed.core[name].source for name in entry.core),
            role=(entry.file,),
            operation=(parsed.operations["implementation"].source,),
        ),
    )
    assert not unreadable.ok
    assert unreadable.error is not None
    assert unreadable.error.status == "source-missing"


def test_a_skill_reference_whose_root_file_is_not_admitted_is_refused() -> None:
    """A reference with no revision is not a reference."""

    parsed = _shipped_parsed()
    entry = parsed.roles["worker"]
    sources = tuple(
        source
        for source in admit_capsule_sources(_shipped_request("worker", "implementation"))
        if source.path != "SKILL.md"
    )

    with pytest.raises(CapsuleCompilationError) as raised:
        compile_role_capsule(
            _shipped_binding("worker", "implementation"),
            (LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes(),
            sources,
            _shipped_request("worker", "implementation", skills=False).as_selection(),
        )

    assert raised.value.status == "missing-required-instruction"
    assert "declared skill root files were not admitted" in raised.value.detail
    assert entry.skills, "the role must reference a skill for this case to mean anything"


def test_a_launcher_whose_own_block_is_absent_is_refused_by_name() -> None:
    """The block-existence refusal names the missing block, not a generic mismatch."""

    def mutate(document: dict) -> None:
        del document["core"]["launcher"]
        document["core"]["extra"] = {"source": "core/extra.md", "purpose": "filler"}

    document = _shipped_document()
    mutate(document)
    with pytest.raises(CapsuleCompilationError) as raised:
        parse_composition_manifest(json.dumps(document).encode("utf-8"))

    assert raised.value.status == "manifest-vocabulary-mismatch"
    assert "launcher" in raised.value.detail


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


def test_the_specializations_field_must_be_an_array_when_present() -> None:
    """A present-but-wrong-typed optional field is refused, not ignored.

    All three non-array shapes are checked in one item because they reach the same
    branch: the point is the branch, not three spellings of it.
    """

    for bad in ({"notes": "not-an-array"}, "specializations/a.md", 7):

        def mutate(document: dict, value=bad) -> None:
            document["specializations"] = value

        assert _refuse(_manifest_with(mutate)) == "manifest-invalid"


def test_an_override_whose_replacement_file_is_absent_is_refused() -> None:
    """The second unknown-supersession branch: no admitted source carries the winner."""

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
                superseded_identity="core:authority",
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


def test_a_declared_instruction_with_a_blank_identity_is_refused() -> None:
    """The declared-instruction value type refuses a blank identity."""

    with pytest.raises(ValueError) as raised:
        CapsuleDeclaredInstruction(
            identity="   ",
            composition_root="core",
            authorities=("core:authority:shared-core",),
        )

    assert "non-blank" in str(raised.value)


def test_an_unknown_skill_name_is_refused_by_the_manifest_accessor() -> None:
    """Asking for an undeclared skill is refused, not answered with a default."""

    parsed = _shipped_parsed()
    assert parsed.skill_entry("l-01-agent-lifecycles").origin == "agents-remember/skills"

    with pytest.raises(CapsuleCompilationError) as raised:
        parsed.skill_entry("no-such-skill")

    assert raised.value.status == "manifest-vocabulary-mismatch"
    assert "no-such-skill" in raised.value.detail


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
