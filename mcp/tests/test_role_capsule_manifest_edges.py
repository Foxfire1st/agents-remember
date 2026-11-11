"""Additional manifest and source-admission refusal boundaries."""

from __future__ import annotations

import copy
import json
import re
import shutil
from dataclasses import replace
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
from agents_remember.cli.role_brief_message import shared_block
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
    CapsuleDetailReference,
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
    source = "reference/instruction-shortfall-review.md"

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
    window_clauses = (
        "The parent names the review window and instruction tree.",
        "Without a parent, the developer names them.",
        "The default window is two weeks.",
    )

    def require_window(text: str) -> None:
        for clause in window_clauses:
            assert clause in " ".join(text.split())

    require_window(references[source].content.decode())
    instructions = accepted.result.render_instructions()
    action = "Before reviewing an instruction shortfall named by the concern, read"
    pointer = action + " [instruction-shortfall review]({{detail:" + source + "}})"
    assert pointer in " ".join(instructions.split())
    misplaced = instructions.replace(action, "Read", 1)
    with pytest.raises(AssertionError):
        assert pointer in " ".join(misplaced.split())
    declaration = document["references"]["instruction-shortfall-review"]
    assert declaration["source"] == source
    assert declaration["roles"] == ["investigator"]
    assert declaration["step_detail"] is True and declaration["injected"] is False
    assert references[source].revision == compute_content_digest((root / source).read_bytes())
    assert references[source].step == declaration["step"] and references[source].step
    original = (root / source).read_text()
    for clause in window_clauses:
        mutant = re.sub(re.escape(clause).replace(r"\ ", r"\s+"), "", original)
        assert mutant != original
        (root / source).write_text(mutant)
        changed = compile_current()
        assert changed.ok and changed.result is not None
        delivered = next(
            entry.content.decode()
            for entry in changed.result.capsule.detail_references
            if entry.source_path == source
        )
        with pytest.raises(AssertionError):
            require_window(delivered)
    (root / source).write_text(original)
    del document["references"]["instruction-shortfall-review"]
    manifest.write_text(json.dumps(document))
    refused = compile_current()
    assert refused.error is not None
    assert refused.error.status == "detail-reference-missing"
    assert source in refused.error.detail
    assert (root / source).is_file()


def test_progress_judgment_detail_reaches_all_declared_coordination_seats() -> None:
    """The split topic follows its actual judgment owners and selective action pointers."""
    parsed = _shipped_parsed()
    document = json.loads((LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_text())
    key = "coordination/shared/third-rejected-round"
    source = "reference/coordination/shared/third-rejected-round.md"
    declaration = document["references"][key]
    assert set(declaration["roles"]) == {"architect", "manager"}
    assert declaration["source"] == source
    assert declaration["step_detail"] is True and declaration["injected"] is False
    actions = {
        "architect": "Before judging a third rejected round during direct coordination, read",
        "manager": (
            "Before judging repair progress at a third rejected round, an earlier coder switch "
            "or a Reviewer replacement, read"
        ),
    }
    clauses = (
        "Start with the Worker role's default.",
        "Clear progress may justify one more bounded repair with a concrete recorded reason",
        "If the fixes remain too large or have barely moved, use the configured stronger model in `role.stepUp`.",
        "Under direct coordination, the Architect uses its own developer-decision route.",
        "a low closed-finding count does not authorize it.",
        "The product's automatic step-up (`afterTries`) counts the task's failed review rounds "
        "of the matching lane, not only the current coder's, and does not reset when the coder "
        "changes; apply the round limits as written.",
        "At the same coder's third rejected round, the Manager reads the sealed finding trail",
        "Under developer-chosen direct coordination, the Architect does this.",
        "A third rejection does not automatically require a stronger model.",
        "a separately authorized further round",
        "If none is configured, the Manager asks the Architect through its existing parent route.",
        "A stronger model grants no extra review round.",
        "Switching the coder earlier requires the developer's agreement",
        "Replace a Reviewer only on clear observed signs.",
        "The three-failed-rounds rule counts the rounds of the current coder.",
        "does not reset when the coder changes",
    )
    for role, entry in parsed.roles.items():
        for operation in entry.operations:
            outcome = compile_admitted_capsule(
                _shipped_binding(role, operation), _shipped_request(role, operation)
            )
            assert outcome.ok, outcome.render_explanation()
            assert outcome.result is not None
            details = {item.source_path: item for item in outcome.result.capsule.detail_references}
            assert (source in details) == (role in actions), (role, operation)
            rendered = " ".join(outcome.result.render_instructions().split())
            if role in actions:
                reference = details[source]
                assert reference.content == (LIFECYCLE_ROOT / source).read_bytes()
                assert reference.revision == compute_content_digest(reference.content)
                assert reference.step == declaration["step"] and reference.step
                pointer = actions[role] + " [repair-progress judgment]({{detail:" + source + "}})"
                assert pointer in rendered, (role, operation)
                with pytest.raises(AssertionError):
                    assert pointer in rendered.replace(actions[role], "Read", 1)
                body = " ".join(reference.content.decode().split())
                for clause in clauses:
                    assert clause in body, (role, operation, clause)
                    with pytest.raises(AssertionError):
                        assert clause in body.replace(clause, "", 1)
            elif role == "orchestrator":
                assert "The Manager retains its leaf finding-trail judgment." in rendered


def test_architect_measurement_detail_keeps_change_trigger_and_unknown_boundary() -> None:
    """Rule50's retained trigger and outcome meanings reach the actual Architect detail."""
    source = "reference/coordination/architect/curator-measurement.md"
    document = json.loads((LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_text())
    declaration = document["references"]["coordination/architect/curator-measurement"]
    assert declaration["source"] == source and declaration["roles"] == ["architect"]
    assert declaration["step_detail"] is True and declaration["injected"] is False
    for operation in _shipped_parsed().roles["architect"].operations:
        outcome = compile_admitted_capsule(
            _shipped_binding("architect", operation), _shipped_request("architect", operation)
        )
        assert outcome.ok, outcome.render_explanation()
        assert outcome.result is not None
        reference = next(
            entry
            for entry in outcome.result.capsule.detail_references
            if entry.source_path == source
        )
        assert reference.content == (LIFECYCLE_ROOT / source).read_bytes()
        assert reference.revision == compute_content_digest(reference.content)
        assert reference.step == declaration["step"] and reference.step
        pointer = (
            "Before running or discussing a Curator measurement, read "
            "[Curator measurement rules]({{detail:" + source + "}})"
        )
        rendered = " ".join(outcome.result.render_instructions().split())
        assert pointer in rendered, operation
        with pytest.raises(AssertionError):
            assert pointer in rendered.replace(
                "Before running or discussing a Curator measurement", "Read", 1
            )
        detail = reference.content.decode()
        for clause in (
            "receive the candidate and case-set identity through the coordinating Manager",
            "Return the go on that same route.",
            "This is a simple handover, not a review freeze; it starts no seat.",
            "New-text trials use the tool build of the candidate whose text is measured.",
            "Every run binds the head, change hash, build, text signature, case hashes, model, mode, judge and capture versions.",
            "fourteen cases in seven fixed pairs (two cases per theme)",
            'Report "all 3 right" and "at least 2 of 3" per case and per fixed pair, separately per model.',
            "A case at 2 of 3 is reported and discussed, and does not force another round.",
            "run the full set, discuss the failures with the developer, change the text, repeat the failing cases until none remain, then one final full run.",
            "A theme is worse, on a model, when fewer of its two cases reach at least 2 of 3 in the final run than in the old-text run.",
            "Other failures are reported.",
            "Unchanged text is re-run only to replace a trial spoiled by an infrastructure fault.",
            "Report it with its cause. The Architect fixes the cause and reruns those trials.",
            "Only a case that cannot run at all goes to the developer.",
            "when the Curator's text, model or role set changes, and on the developer's request",
            "Every measurement belongs to the Architect; the Worker implements tooling and agreed edits.",
            "An infrastructure failure, a missing capture or an unanswered prompt makes a trial unknown",
            "Record tokens per attempt and per run, judge usage separately, and unknown usage distinctly.",
            "About 2.5 million tokens is the comparison guide, not a per-trial cap",
            "A case below 2 of 3 on a model is a failure for the loop.",
            "A run gets its own coordination report, raw evidence, and a one-page summary per case theme.",
        ):
            assert clause in detail, (operation, clause)
        assert "No role text gains a grader step on every leaf." in detail
        assert "only a worse theme reopens the loop" in detail
        assert "Spending does not wait for the developer." in detail
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


_D700_RETIRED_CORE_UNITS = frozenset({"AU2.5", "LO0.4", "AC1.4", "AC1.5"})
_D700_RETIREMENT_DECISION = {
    "path": "/home/firefox/projects/ar-coordination/notes/reports/paseo-native-executions/architect/messages/20261107T145802Z_architect-97e0320d_to_manager-96726ec5.md",
    "messageId": "bc7dacb7-24b8-40c9-9922-a5ddb930ef50",
    "sections": ["Q1", "Q2"],
    "sha256": "ebd655e1327fb427386324d7ad383ffe63398d8daeab1a4e138e19d74c298e68",
}


def _assert_core_retirements(document: dict) -> None:
    retired = {
        row["id"] for row in document["units"] if row["disposition"] == "retired (D699, D700)"
    }
    assert retired == _D700_RETIRED_CORE_UNITS, "unknown or missing retired unit"
    for row in document["units"]:
        if row["id"] in retired:
            assert row.get("retirementDecision") == _D700_RETIREMENT_DECISION, (
                row["id"],
                "missing or wrong retirement decision",
            )
            assert "digestPlace" not in row and "evidence" not in row, (
                row["id"],
                "retired unit has live bindings",
            )
            assert row["retiredBinding"]["file"] and row["retiredBinding"]["lines"]
            assert row["retiredBinding"]["sha256"], "missing retired binding history"


def _assert_core_source_map(root: Path, document: dict) -> None:
    """Every current non-heading source line belongs to exactly one frozen/new unit."""
    _assert_core_retirements(document)
    files: dict[str, list] = {}
    for row in document["units"]:
        if row["disposition"] == "retired (D699, D700)":
            continue
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


def _digest_range(lines: list[str], extent: list[int]) -> set[int]:
    assert isinstance(extent, list) and len(extent) == 2, "invalid digest range"
    start, end = extent
    assert type(start) is int and type(end) is int, "invalid digest range"
    assert 1 <= start <= end <= len(lines), "invalid digest range"
    assert lines[start - 1].strip() and lines[end - 1].strip(), "empty digest range"
    return set(range(start, end + 1))


def _assert_digest_structure(text: str) -> None:
    lines = text.splitlines()
    assert lines and lines[0].startswith("# "), "missing digest title"
    headings = [i for i, line in enumerate(lines) if line.startswith("#")]
    assert any(lines[i].startswith("## ") for i in headings), "missing digest sections"
    for start, end in zip(headings, [*headings[1:], len(lines)], strict=True):
        assert lines[start].lstrip("#").strip(), "empty heading"
        assert any(line.strip() for line in lines[start + 1 : end]), "empty section"
    assert not re.search(r"\b(?:AU|IN|LF|LO|AC)\d+\.\d+\b", text), "unit code in prose"


def _assert_digest_places(document: dict, text: str) -> None:
    _assert_digest_structure(text)
    lines = text.splitlines()
    rows = {row["id"]: row for row in document["units"] if row["disposition"] == "digest"}
    places = document["digest"]["places"]
    by_id = {place["id"]: place for place in places}
    assert len(by_id) == len(places), "duplicate place"
    intro = document["digest"]["introduction"]
    covered = list(_digest_range(lines, intro["lines"]))
    first_section = next(i for i, line in enumerate(lines, 1) if line.startswith("## "))
    assert set(covered) == {i for i in range(2, first_section) if lines[i - 1].strip()}
    assert (
        sha256("\n".join(lines[i - 1] for i in sorted(covered)).encode()).hexdigest()
        == intro["sha256"]
    )
    assert [p["lines"] for p in places] == sorted(p["lines"] for p in places)
    homes: dict[str, list[str]] = {}
    for place in places:
        extent = _digest_range(lines, place["lines"])
        assert not any(lines[i - 1].startswith("#") for i in extent), "heading inside place"
        heading = next(
            line.lstrip("#").strip()
            for line in reversed(lines[: min(extent)])
            if line.startswith("#")
        )
        assert heading == place["heading"], "wrong place heading"
        body = "\n".join(lines[i - 1] for i in sorted(extent))
        assert sha256(body.encode()).hexdigest() == place["sha256"], "stale place hash"
        units = place["units"]
        assert units and len(units) == len(set(units)), "invalid place units"
        assert set(units) <= rows.keys(), "unknown place unit"
        for identity in units:
            homes.setdefault(identity, []).append(place["id"])
        clauses = place["clauses"]
        assert clauses, "missing clauses"
        clause_lines, clause_units = [], set()
        assert [c["lines"] for c in clauses] == sorted(c["lines"] for c in clauses)
        for clause in clauses:
            selected = _digest_range(lines, clause["lines"])
            assert selected <= extent, "clause outside place"
            assert clause["units"] and len(clause["units"]) == len(set(clause["units"]))
            assert set(clause["units"]) <= set(units), "clause unit outside place"
            clause_lines.extend(selected)
            clause_units.update(clause["units"])
        assert clause_units == set(units), "missing clause unit"
        assert len(clause_lines) == len(set(clause_lines)), "overlapping clauses"
        assert {i for i in clause_lines if lines[i - 1].strip()} == {
            i for i in extent if lines[i - 1].strip()
        }, "unmapped clause prose"
        covered.extend(extent)
    assert len(covered) == len(set(covered)), "overlapping places"
    assert set(covered) >= {
        i for i, line in enumerate(lines, 1) if line.strip() and not line.startswith("#")
    }, "unmapped digest prose"
    assert set(homes) == set(rows), "missing unit home"
    for identity, row in rows.items():
        assert "digestLines" not in row and "digestAudiences" not in row
        assert homes[identity] == [row["digestPlace"]], "unit home mismatch"


def _assert_core_dispositions(root: Path, document: dict, text: str) -> None:
    _assert_core_retirements(document)
    rows = {row["id"]: row for row in document["units"]}
    assert len(rows) == len(document["units"]), "duplicate ID"
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
        if row["disposition"] == "retired (D699, D700)":
            continue
        if row["disposition"] == "digest":
            assert row["digestPlace"], row["id"]
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
    _assert_digest_places(document, text)


def _assert_digest_audiences(document: dict, text: str) -> None:
    roles = set(document["roles"])
    admitted_roles = roles
    assert admitted_roles == {
        "architect",
        "orchestrator",
        "manager",
        "worker",
        "curator",
        "reviewer",
        "investigator",
        "bootstrap",
    }
    rows = {row["id"]: row for row in document["units"] if row["disposition"] == "digest"}
    lines = text.splitlines()
    audiences: dict[str, set[str]] = {identity: set() for identity in rows}
    for place in document["digest"]["places"]:
        for clause in place["clauses"]:
            start, end = clause["lines"]
            body = " ".join(lines[start - 1 : end])
            prefix = set(body.split(":", 1)[0].lower().split(", "))
            expected = prefix if prefix <= roles else admitted_roles
            actual = set(clause["audience"])
            assert len(actual) == len(clause["audience"]) and actual <= roles
            assert actual == expected, "clause audience union"
            for identity in clause["units"]:
                audiences[identity].update(actual)
    for identity, row in rows.items():
        assert audiences[identity] == set(row["audience"]), (identity, "unit audience union")


def test_digest_line_and_unit_audiences_match_the_delivered_prefixes() -> None:
    document = json.loads((LIFECYCLE_ROOT / "core/digest-map.json").read_text())
    text = (LIFECYCLE_ROOT / "core/every-role.md").read_text()
    _assert_digest_audiences(document, text)
    wrong_line = copy.deepcopy(document)
    place = next(p for p in wrong_line["digest"]["places"] if "IN3.4" in p["units"])
    clause = next(
        c for c in place["clauses"] if set(c["audience"]) == {"architect", "investigator"}
    )
    clause["audience"].append("worker")
    with pytest.raises(AssertionError, match="clause audience union"):
        _assert_digest_audiences(wrong_line, text)
    wrong_unit = copy.deepcopy(document)
    row = next(row for row in wrong_unit["units"] if row["id"] == "IN3.4")
    row["audience"].remove("bootstrap")
    with pytest.raises(AssertionError, match="unit audience union"):
        _assert_digest_audiences(wrong_unit, text)
    extra_designer = copy.deepcopy(document)
    row = next(row for row in extra_designer["units"] if row["id"] == "AU1.1")
    row["audience"].append("designer")
    with pytest.raises(AssertionError, match="unit audience union"):
        _assert_digest_audiences(extra_designer, text)


def _assert_digest_prose_corruption_rejected(document: dict, text: str) -> None:
    mutant = copy.deepcopy(document)
    mutant["digest"]["introduction"]["sha256"] = "0" * 64
    with pytest.raises(AssertionError):
        _assert_digest_places(mutant, text)
    with pytest.raises(AssertionError, match="unmapped digest prose"):
        _assert_digest_places(document, text + "\nA lost obligation.\n")
    for tail in ("\n## Empty section\n", "\nAU1.1\n"):
        with pytest.raises(AssertionError):
            _assert_digest_structure(text + tail)


def test_digest_places_reject_lost_or_ambiguous_bindings() -> None:
    document = json.loads((LIFECYCLE_ROOT / "core/digest-map.json").read_text())
    text = (LIFECYCLE_ROOT / "core/every-role.md").read_text()
    _assert_digest_places(document, text)
    for field, value in (
        ("lines", [0, 0]),
        ("lines", [True, True]),
        ("sha256", "0" * 64),
        ("heading", "Absent"),
        ("units", []),
        ("units", ["unknown-unit"]),
        ("clauses", []),
    ):
        mutant = copy.deepcopy(document)
        mutant["digest"]["places"][0][field] = value
        with pytest.raises(AssertionError):
            _assert_digest_places(mutant, text)
    mutant = copy.deepcopy(document)
    mutant["digest"]["places"].append(copy.deepcopy(mutant["digest"]["places"][0]))
    with pytest.raises(AssertionError, match="duplicate place"):
        _assert_digest_places(mutant, text)
    mutant = copy.deepcopy(document)
    first, second = mutant["digest"]["places"][:2]
    for key in ("lines", "sha256", "heading"):
        second[key] = first[key]
    second["clauses"] = [{"lines": first["lines"], "units": second["units"], "audience": []}]
    with pytest.raises(AssertionError, match="overlapping places"):
        _assert_digest_places(mutant, text)
    mutant = copy.deepcopy(document)
    first, second = mutant["digest"]["places"][:2]
    second["units"].append(first["units"][0])
    second["clauses"][0]["units"].append(first["units"][0])
    with pytest.raises(AssertionError, match="unit home mismatch"):
        _assert_digest_places(mutant, text)
    for field in ("units", "clauses"):
        mutant = copy.deepcopy(document)
        del mutant["digest"]["places"][0][field][0]
        with pytest.raises(AssertionError):
            _assert_digest_places(mutant, text)
    mutant = copy.deepcopy(document)
    next(row for row in mutant["units"] if row["disposition"] == "digest")["digestPlace"] = "absent"
    with pytest.raises(AssertionError, match="unit home mismatch"):
        _assert_digest_places(mutant, text)
    mutant = copy.deepcopy(document)
    place = mutant["digest"]["places"][0]
    place["clauses"][0]["lines"][1] = place["lines"][1] + 1
    with pytest.raises(AssertionError):
        _assert_digest_places(mutant, text)
    mutant = copy.deepcopy(document)
    place = mutant["digest"]["places"][0]
    place["clauses"].append(copy.deepcopy(place["clauses"][0]))
    with pytest.raises(AssertionError, match="overlapping clauses"):
        _assert_digest_places(mutant, text)
    mutant = copy.deepcopy(document)
    place = next(p for p in mutant["digest"]["places"] if "IN3.4" in p["units"])
    del place["clauses"][1]
    with pytest.raises(AssertionError, match="unmapped clause prose"):
        _assert_digest_places(mutant, text)
    _assert_digest_prose_corruption_rejected(document, text)


def test_structured_shared_digest_is_delivered_once_and_seals_each_role() -> None:
    text = (LIFECYCLE_ROOT / "core/every-role.md").read_text()
    _assert_digest_structure(text)
    archive_rule = "Worker, Reviewer, Curator: At the end of each round, pack that round's bulk evidence (captures, logs, generated packages) into one archive in your report folder, with a manifest of its files and hashes, and keep loose only the archive, its manifest and the files the next step reads."
    evidence_rule = "Worker, Reviewer: For evidence kept for later use, record one of two sets of information. Either name the stable contract it serves, its owner, the exact executable test or check that uses or verifies it, and its consumers; or name its expiry, the executable step that removes or replaces it, its owner and the effect on compatibility. Missing catalog information must be repaired even if the requirement is satisfied; complete catalog information does not itself satisfy the requirement."
    for role, entry in _shipped_parsed().roles.items():
        for operation in entry.operations:
            request = _shipped_request(role, operation)
            binding = _shipped_binding(role, operation)
            outcome = compile_admitted_capsule(binding, request)
            assert outcome.ok and outcome.result is not None, (role, operation)
            assert outcome.shared_block == text
            if role in {"worker", "reviewer", "curator"}:
                assert outcome.shared_block is not None
                assert outcome.shared_block.count(archive_rule) == 1, (role, operation)
                assert outcome.shared_block.count(evidence_rule) == 1, (role, operation)
            rendered = shared_block(outcome.shared_block) + outcome.result.render_instructions()
            assert rendered.count(text) == 1
            assert not any(
                block.identity == "core:every-role" for block in outcome.result.capsule.instructions
            )
            sources = admit_capsule_sources(request)
            changed = tuple(
                replace(
                    source,
                    content=source.content + b"\n",
                    revision=compute_content_digest(source.content + b"\n"),
                )
                if source.path == "core/every-role.md"
                else source
                for source in sources
            )
            mutation = compile_role_capsule(
                binding,
                (LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes(),
                changed,
                request.as_selection(),
            )
            assert mutation.semantic_digest != outcome.result.semantic_digest
            assert mutation.render_instructions() == outcome.result.render_instructions()


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


def test_retired_core_units_require_the_exact_admitted_decision() -> None:
    document = json.loads((LIFECYCLE_ROOT / "core/digest-map.json").read_text())
    _assert_core_retirements(document)
    unknown = copy.deepcopy(document)
    row = next(row for row in unknown["units"] if row["id"] == "AU1.1")
    row["disposition"] = "retired (D699, D700)"
    with pytest.raises(AssertionError, match="unknown or missing retired unit"):
        _assert_core_retirements(unknown)
    missing = copy.deepcopy(document)
    row = next(row for row in missing["units"] if row["id"] == "AU2.5")
    del row["retirementDecision"]
    with pytest.raises(AssertionError, match="missing or wrong retirement decision"):
        _assert_core_retirements(missing)
    wrong = copy.deepcopy(document)
    row = next(row for row in wrong["units"] if row["id"] == "LO0.4")
    row["retirementDecision"]["sha256"] = "0" * 64
    with pytest.raises(AssertionError, match="missing or wrong retirement decision"):
        _assert_core_retirements(wrong)
    live = copy.deepcopy(document)
    row = next(row for row in live["units"] if row["id"] == "AC1.4")
    row["evidence"] = []
    with pytest.raises(AssertionError, match="retired unit has live bindings"):
        _assert_core_retirements(live)


def test_git_root_scope_has_one_complete_source_and_digest_home() -> None:
    document = json.loads((LIFECYCLE_ROOT / "core/digest-map.json").read_text())
    text = (LIFECYCLE_ROOT / "core/every-role.md").read_text()
    rule = next(row for row in document["units"] if row["id"] == "IN8.7")
    assert rule["file"] == "core/invariants.md" and rule["disposition"] == "digest"
    assert rule["digestPlace"] == "place-40"
    assert set(rule["audience"]) == set(document["roles"])
    place = next(p for p in document["digest"]["places"] if p["id"] == "place-40")
    assert place["units"] == ["IN8.7"]
    expected = (
        "Run Git commands only against the resolved code repository root or memory root, "
        "and only when that root is a Git repository. "
        "Never run Git against the coordination root as a whole."
    )
    source = (LIFECYCLE_ROOT / rule["file"]).read_text().splitlines()
    start, end = rule["lines"]
    assert "\n".join(source[start - 1 : end]) == expected
    start, end = place["lines"]
    assert "\n".join(text.splitlines()[start - 1 : end]) == expected
    _assert_core_source_map(LIFECYCLE_ROOT, document)
    _assert_digest_places(document, text)
    _assert_digest_audiences(document, text)
    mutant = copy.deepcopy(document)
    mutant["units"] = [row for row in mutant["units"] if row["id"] != "IN8.7"]
    with pytest.raises(AssertionError, match="unmapped source line"):
        _assert_core_source_map(LIFECYCLE_ROOT, mutant)
    mutant = copy.deepcopy(document)
    mutant["digest"]["places"] = [p for p in mutant["digest"]["places"] if p["id"] != "place-40"]
    with pytest.raises(AssertionError, match="unmapped digest prose"):
        _assert_digest_places(mutant, text)
    mutant = copy.deepcopy(document)
    place = next(p for p in mutant["digest"]["places"] if p["id"] == "place-40")
    place["clauses"][0]["audience"].remove("bootstrap")
    with pytest.raises(AssertionError, match="clause audience union"):
        _assert_digest_audiences(mutant, text)


def _assert_foundation_detail_at_its_action(
    reference: CapsuleDetailReference, foundation: str, step: str, rendered: str
) -> None:
    """Check the exact foundation bytes, metadata, pointer and displaced-action control."""
    assert reference.content == (LIFECYCLE_ROOT / foundation).read_bytes()
    assert reference.revision == compute_content_digest(reference.content)
    assert reference.step == step and reference.step
    pointer = (
        "Before a repository's first or resumed knowledge foundation, read "
        "[repository foundation]({{detail:" + foundation + "}})"
    )
    assert pointer in rendered
    with pytest.raises(AssertionError):
        assert pointer in rendered.replace(
            "Before a repository's first or resumed knowledge foundation", "Read", 1
        )


def test_reviewer_complete_diff_rule_stays_inline_once() -> None:
    """P5-07 RG-021 (round-5 look F2): the complete-diff rule's one home is the role file."""
    inline = "Inspect every changed file, including files without invariant attribution."
    assert (LIFECYCLE_ROOT / "roles/reviewer.md").read_text().count(inline) == 1
    assert inline not in (LIFECYCLE_ROOT / "reference/knowledge/use.md").read_text()


#: Each role that reads knowledge use, the operation it reads it in and its working pointers.
KNOWLEDGE_USE_CONSUMERS = {
    "architect": (
        "planning",
        (
            "Before diagnosing, call `knowledge_read` on `agents-remember-task` for the files involved; read [knowledge use]",
        ),
    ),
    "worker": (
        "implementation",
        (
            (
                "Before editing source, call `knowledge_read` on "
                '`agents-remember-task` with `view="source_context"` for each '
                "file you will edit; read [knowledge use]"
            ),
            (
                "Before diagnosing a failing test, call `knowledge_read` on "
                '`agents-remember-task` with `view="source_context"` for the '
                "test file; read [knowledge use]"
            ),
        ),
    ),
    "reviewer": (
        "review",
        (
            "When inspecting changes, call `knowledge_read` on `agents-remember-task` for each changed file; read [knowledge use]",
        ),
    ),
    "investigator": (
        "orientation",
        (
            "Before investigating repository code, call `knowledge_read` on `agents-remember-task` for the files involved; read [knowledge use]",
        ),
    ),
    "curator": (
        "curation",
        (
            "Before checking code and memory, call `knowledge_read` on `agents-remember-task` for the files or records you check; read [knowledge use]",
            "Before interpreting a knowledge read, read [knowledge-read limits]",
        ),
    ),
}


def test_inherited_step_details_reach_only_the_seats_that_use_them(tmp_path: Path) -> None:
    """A registry row is insufficient: actual capsules need their selective working pointer."""
    manifest_bytes = (LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes()
    knowledge = "reference/knowledge/use.md"
    foundation = "reference/curator/repository-foundation.md"
    document = json.loads(manifest_bytes)
    declaration = document["references"]["knowledge/use"]
    assert declaration["source"] == knowledge
    assert set(declaration["roles"]) == {
        "architect",
        "orchestrator",
        "manager",
        "worker",
        "curator",
        "reviewer",
        "investigator",
        "bootstrap",
    }
    assert declaration["step_detail"] is True and declaration["injected"] is False
    foundation_declaration = document["references"]["curator/repository-foundation"]
    assert foundation_declaration["source"] == foundation
    # operations/curation.md:10 (a relative link, not a capsule detail) sits in an all-roles
    # passage, so the Manager's curation text points at it too (P5-05 first-message roles).
    assert foundation_declaration["roles"] == ["manager", "curator"]
    assert foundation_declaration["step_detail"] is True
    assert foundation_declaration["injected"] is False
    for role, entry in _shipped_parsed().roles.items():
        operation = (
            KNOWLEDGE_USE_CONSUMERS[role][0]
            if role in KNOWLEDGE_USE_CONSUMERS
            else entry.operations[0]
        )
        request = routed_admission_for(
            LIFECYCLE_ROOT, MANIFEST_RELATIVE, manifest_bytes, CapsuleSeatAddress(role, operation)
        )
        outcome = compile_admitted_capsule(_shipped_binding(role, operation), request)
        assert outcome.ok, outcome.render_explanation()
        assert outcome.result is not None
        rendered = " ".join(outcome.result.render_instructions().split())
        details = {item.source_path: item for item in outcome.result.capsule.detail_references}
        assert (knowledge in details) == (role in KNOWLEDGE_USE_CONSUMERS), role
        assert (foundation in details) == (role == "curator"), role
        if role == "curator":
            reference = details[foundation]
            _assert_foundation_detail_at_its_action(
                reference, foundation, foundation_declaration["step"], rendered
            )
        if role in KNOWLEDGE_USE_CONSUMERS:
            reference = details[knowledge]
            assert reference.content == (LIFECYCLE_ROOT / knowledge).read_bytes()
            assert reference.revision == compute_content_digest(reference.content)
            assert reference.step == declaration["step"] and reference.step
            for action in KNOWLEDGE_USE_CONSUMERS[role][1]:
                # The link closes or carries one section anchor (P5-14 section links).
                pointer = re.compile(
                    re.escape(action + "({{detail:" + knowledge + "}}") + r"(?:#[a-z0-9-]+)?\)"
                )
                assert pointer.search(rendered), (role, action)
                with pytest.raises(AssertionError):
                    assert pointer.search(rendered.replace(action, "Read [knowledge use]", 1))
            detail = details[knowledge].content.decode()
            for clause in (
                "memoryRoot",
                "repositoryRoot",
                "codeTreeId",
                'view="source_context"',
                "Read every page through `continuation` until enumeration is complete",
                "A proof records what a test demonstrates, not that it passed",
                "Report a refusal, partial index or unverified currentness as a gap",
                "even when the handover names no invariant or family",
                "When a test fails, inspect its recorded `proves` links",
                "If no link is recorded, read the test and code and state that gap",
                "Do not change a sibling outside the assigned scope",
                "Distinguish recorded claims, observed evidence and your hypothesis",
                # P5-07 RG-021: the complete-diff rule stands inline in roles/reviewer.md.
                "Attribution adds a review dimension; it filters nothing",
            ):
                assert clause in detail, (role, clause)
    root = tmp_path / "corpus"
    shutil.copytree(LIFECYCLE_ROOT, root)
    manifest = root / MANIFEST_RELATIVE
    document = json.loads(manifest_bytes)
    for key, role, operation in (
        ("knowledge/use", "worker", "implementation"),
        ("curator/repository-foundation", "curator", "curation"),
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
