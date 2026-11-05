"""Additional manifest and source-admission refusal boundaries."""

from __future__ import annotations

import copy
import json
import re
import shutil
from hashlib import sha256
from pathlib import Path

import pytest
from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.role_capsules.launch import compile_launch_capsule
from agents_remember.application.role_capsules.sources import (
    CapsuleAdmissionRequest,
    admit_capsule_sources,
)
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
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


def test_investigator_review_pointer_requires_its_declared_source(tmp_path: Path) -> None:
    """A named facet reaches the seat; a present but undeclared file cannot stand in."""
    root = tmp_path / "corpus"
    shutil.copytree(LIFECYCLE_ROOT, root)
    manifest = root / MANIFEST_RELATIVE
    document = json.loads(manifest.read_text())
    source = "reference/investigator-instruction-review.md"

    def compile_current():
        request = routed_admission_for(
            root,
            MANIFEST_RELATIVE,
            manifest.read_bytes(),
            CapsuleSeatAddress("investigator", "orientation"),
        )
        return compile_admitted_capsule(_shipped_binding("investigator", "orientation"), request)

    accepted = compile_current()
    assert accepted.ok, accepted.render_explanation()
    assert accepted.result is not None
    references = {entry.source_path: entry for entry in accepted.result.capsule.detail_references}
    assert references[source].content == (root / source).read_bytes()
    assert "two-week window" in references[source].content.decode()
    assert "when you reach a concern" in accepted.result.render_instructions()
    del document["references"]["investigator-instruction-review"]
    manifest.write_text(json.dumps(document))
    refused = compile_current()
    assert refused.error is not None
    assert refused.error.status == "detail-reference-missing"
    assert source in refused.error.detail
    assert (root / source).is_file()


def test_progress_judgment_detail_reaches_all_declared_coordination_seats() -> None:
    """Rule41 requires a direct selective pointer; a declaration alone delivers nothing."""
    parsed = _shipped_parsed()
    source = "reference/manager-working-rules.md"
    for role in ("architect", "manager", "orchestrator"):
        for operation in parsed.roles[role].operations:
            outcome = compile_admitted_capsule(
                _shipped_binding(role, operation), _shipped_request(role, operation)
            )
            assert outcome.ok, outcome.render_explanation()
            assert outcome.result is not None
            details = {
                entry.source_path: entry for entry in outcome.result.capsule.detail_references
            }
            assert source in details, (role, operation)
            assert details[source].content == (LIFECYCLE_ROOT / source).read_bytes()
            assert "{{detail:" + source + "}}" in outcome.result.render_instructions()


def test_architect_measurement_detail_keeps_change_trigger_and_unknown_boundary() -> None:
    """Rule50's retained trigger and outcome meanings reach the actual Architect detail."""
    for operation in _shipped_parsed().roles["architect"].operations:
        outcome = compile_admitted_capsule(
            _shipped_binding("architect", operation), _shipped_request("architect", operation)
        )
        assert outcome.ok, outcome.render_explanation()
        assert outcome.result is not None
        detail = next(
            entry
            for entry in outcome.result.capsule.detail_references
            if entry.source_path == "reference/architect-working-rules.md"
        ).content.decode()
        for clause in (
            "when the Curator's text, model or role set changes, and on the developer's request",
            "Every measurement belongs to the Architect; the Worker implements tooling and agreed edits.",
            "An infrastructure failure, a missing capture or an unanswered prompt makes a trial unknown",
            "Record tokens per attempt and per run, judge usage separately and unknown usage distinctly.",
            "About 2.5 million tokens is the comparison guide, not a per-trial cap",
            "A case below 2 of 3 on a model is a failure for the loop.",
            "A run gets its own coordination report with raw evidence and a one-page summary per case theme.",
        ):
            assert clause in detail, (operation, clause)
        assert "No role text gains a per-leaf grader step." in detail
        assert "only a worse theme reopens the loop" in detail
        assert "Nothing waits for the developer before spending." in detail
        assert "No ruling record is kept per loop change." in detail
        for superseded in (
            "bring a projection well beyond it to the developer before spending",
            "take its cause, proposed retry/recovery/drop and changed population to the developer",
            "Each agreed change records a ruling",
        ):
            assert superseded not in detail


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


def _assert_core_source_map(root: Path, document: dict) -> None:
    """Every current non-heading source line belongs to exactly one frozen/new unit."""
    files: dict[str, list] = {}
    for row in document["units"]:
        source = (root / row["file"]).read_text().splitlines()
        start, end = row["lines"]
        assert 1 <= start <= end <= len(source), row["id"]
        assert sha256("\n".join(source[start - 1 : end]).encode()).hexdigest() == row["sha256"], (
            row["id"]
        )
        files.setdefault(row["file"], []).append(row)
    for path, rows in files.items():
        extents = [row["lines"] for row in rows]
        assert extents == sorted(extents), path
        covered = [number for start, end in extents for number in range(start, end + 1)]
        assert len(covered) == len(set(covered)), path
        lines = (root / path).read_text().splitlines()
        for number, line in enumerate(lines, 1):
            following = lines[number] if number < len(lines) else ""
            table_header = line.startswith("|") and re.fullmatch(r"\|[\s:|\-]+\|", following)
            structural = (
                not line.strip()
                or line.startswith("#")
                or table_header
                or re.fullmatch(r"\|[\s:|\-]+\|", line)
            )
            assert structural or number in covered, (path, number, "unmapped source line")


def _assert_core_dispositions(root: Path, document: dict, text: str) -> None:
    rows = {row["id"]: row for row in document["units"]}
    assert len(rows) == len(document["units"]), "duplicate ID"
    lines = text.splitlines()
    reasons = {
        "rationale or history",
        "stated already",
        "enforced by the product",
        "stage procedure reached through its operation file",
        "no action",
        "binds no started role",
        "lead-in",
    }
    for row in rows.values():
        if row["disposition"] == "digest":
            assert row["digestLines"], row["id"]
            for number in row["digestLines"]:
                match = re.search(r"\(([^()]*)\)$", lines[number - 1])
                assert match and row["id"] in match[1].split(", "), row["id"]
        else:
            assert row["disposition"] == "reference" and row["reason"] in reasons, row["id"]
            assert row["evidence"], row["id"]
            for evidence in row["evidence"]:
                if "sha256" in evidence:
                    source = (root.parents[1] / evidence["path"]).read_text().splitlines()
                    start, end = evidence["lines"]
                    assert (
                        sha256("\n".join(source[start - 1 : end]).encode()).hexdigest()
                        == evidence["sha256"]
                    ), row["id"]
    for number, line in enumerate(lines, 1):
        match = re.search(r"\(([^()]*)\)$", line)
        assert match and not line.startswith("#"), number
        for identity in match[1].split(", "):
            assert rows[identity]["disposition"] == "digest"
            assert number in rows[identity]["digestLines"]


def test_frozen_core_inventory_and_digest_map_remain_complete(tmp_path: Path) -> None:
    document = json.loads((LIFECYCLE_ROOT / "core/digest-map.json").read_text())
    text = (LIFECYCLE_ROOT / "core/every-role.md").read_text()
    baseline = document["baseline"]
    assert baseline["tree"] == "cd0e68f0" and baseline["unitCount"] == 145
    assert len(baseline["units"]) == len(set(baseline["unitIds"])) == 145
    assert {row["id"] for row in baseline["units"]} == set(baseline["unitIds"])
    for row in baseline["units"]:
        assert sha256(row["text"].encode()).hexdigest() == row["sha256"]
    current = {row["id"] for row in document["units"]}
    removed = {row["id"] for row in document["removed"]}
    assert set(baseline["unitIds"]) <= current | removed
    assert not current & removed
    assert document["digest"]["sha256"] == sha256(text.encode()).hexdigest()
    assert document["digest"]["sizeWords"] == len(text.split())
    _assert_core_source_map(LIFECYCLE_ROOT, document)
    _assert_core_dispositions(LIFECYCLE_ROOT, document, text)
    for key, value in (("sha256", "0" * 64), ("lines", [0, 0])):
        mutant = copy.deepcopy(document)
        mutant["units"][0][key] = value
        with pytest.raises(AssertionError):
            _assert_core_source_map(LIFECYCLE_ROOT, mutant)
    mutant = copy.deepcopy(document)
    next(row for row in mutant["units"] if row["disposition"] == "reference")["reason"] = (
        "not needed"
    )
    with pytest.raises(AssertionError):
        _assert_core_dispositions(LIFECYCLE_ROOT, mutant, text)
    copied = tmp_path / "corpus"
    shutil.copytree(LIFECYCLE_ROOT / "core", copied / "core")
    source = copied / "core/authority.md"
    source.write_text(source.read_text() + "\nA new unmapped duty.\n")
    with pytest.raises(AssertionError, match="unmapped source line"):
        _assert_core_source_map(copied, document)


def test_inherited_step_details_reach_only_the_seats_that_use_them(tmp_path: Path) -> None:
    """A registry row is insufficient: actual capsules need their selective working pointer."""
    manifest_bytes = (LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes()
    knowledge = "reference/knowledge-use.md"
    foundation = "reference/curator-repository-foundation.md"
    consumers = {
        "architect": ("planning", "when diagnosing, not before"),
        "worker": ("implementation", "when you reach editing or a failing test, not before"),
        "reviewer": ("review", "when inspecting changes, not before"),
        "investigator": ("orientation", "when investigating repository code, not before"),
    }
    for role, entry in _shipped_parsed().roles.items():
        operation = consumers[role][0] if role in consumers else entry.operations[0]
        request = routed_admission_for(
            LIFECYCLE_ROOT, MANIFEST_RELATIVE, manifest_bytes, CapsuleSeatAddress(role, operation)
        )
        outcome = compile_admitted_capsule(_shipped_binding(role, operation), request)
        assert outcome.ok, outcome.render_explanation()
        assert outcome.result is not None
        rendered = " ".join(outcome.result.render_instructions().split())
        details = {item.source_path: item for item in outcome.result.capsule.detail_references}
        assert (knowledge in details) == (role in consumers), role
        assert (foundation in details) == (role == "curator"), role
        if role in consumers:
            assert "{{detail:" + knowledge + "}}" in rendered
            assert consumers[role][1] in rendered
            detail = details[knowledge].content.decode()
            for clause in (
                "memoryRoot",
                "repositoryRoot",
                "codeTreeId",
                'view="source_context"',
                "Read every page through `continuation` until enumeration is complete",
                "A proof records what a test demonstrates, not that it passed",
                "A refusal, partial index or unverified currentness is a stated gap",
                "whether or not the handover names an invariant or family",
                "On a failing test, inspect its recorded `proves` links",
                "where no link is recorded, read the test and code and state that gap",
                "Change no sibling outside the assigned scope",
                "distinguish recorded claims, observed evidence and your hypothesis",
                "Inspect the complete diff, including unattributed files",
            ):
                assert clause in detail, (role, clause)
    root = tmp_path / "corpus"
    shutil.copytree(LIFECYCLE_ROOT, root)
    manifest = root / MANIFEST_RELATIVE
    document = json.loads(manifest_bytes)
    for key, role, operation in (
        ("knowledge-use", "worker", "implementation"),
        ("curator-repository-foundation", "curator", "curation"),
    ):
        mutant = copy.deepcopy(document)
        del mutant["references"][key]
        manifest.write_text(json.dumps(mutant))
        request = routed_admission_for(
            root, MANIFEST_RELATIVE, manifest.read_bytes(), CapsuleSeatAddress(role, operation)
        )
        outcome = compile_admitted_capsule(_shipped_binding(role, operation), request)
        assert outcome.error is not None, key
        assert outcome.error.status == "detail-reference-missing", outcome.render_explanation()
        assert key in outcome.error.detail
