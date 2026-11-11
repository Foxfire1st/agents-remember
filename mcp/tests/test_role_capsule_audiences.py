"""Stage audience selection preserves role boundaries in compiled capsules."""

from __future__ import annotations

import re
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import cast
from urllib.parse import quote

import pytest
from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember.cli import role_handover_artifacts
from agents_remember.cli.role_instruction_details import compose_launch_instructions
from agents_remember.models.role_capsules.compiler import compile_role_capsule
from agents_remember.models.role_capsules.contracts import strip_contract_markers
from agents_remember.models.role_capsules.manifest import parse_composition_manifest
from agents_remember.models.role_capsules.sources import strip_frontmatter
from agents_remember.models.role_capsules.types import (
    CapsuleCompilationResult,
    CapsuleDetailReference,
    CapsuleRoleSeat,
    compute_content_digest,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.serving.capsule_delivery import (
    CapsuleBindingIdentity,
    CapsuleDelivery,
    capsule_delivery_from,
)
from agents_remember_test_support.testing.capsule_audience_fixtures import (
    CURATOR_MESSAGE_CONTENT,
    CURATOR_MESSAGE_TIMING,
    CURATOR_SEMANTIC_SCOPE_CLAUSES,
    CURATOR_SERVING_STAGE_BOUNDARY,
    D656_OPERATION_SHA256,
    D656_STAGE_SHA256,
    INLINE_RULE_CLAUSES,
    OPERATION_POINTER_ACTIONS,
    ORDINARY_DETAIL_LINK_COUNTS,
    R125_OPERATION_ROLES,
    ROLE_POINTER_ACTIONS,
    ROLE_POINTER_QUALIFICATIONS,
)
from agents_remember_test_support.testing.capsule_audience_v13_fixtures import (
    V13_OPERATION_SHA256,
    V13_PRE_SHORT_OPERATION_SHA256,
    V13_PRE_SHORT_STAGE_SHA256,
    V13_STAGE_SHA256,
    inverse_v13_paths,
    inverse_v13_short_rules,
)
from test_role_capsule_compiler import (
    all_sources,
    fixture_manifest_document,
    manifest_bytes,
    selection_for,
    skill_source,
    worker_binding,
)


def test_compiler_delivers_only_common_and_selected_role_stage_parts() -> None:
    document = fixture_manifest_document()
    operations = cast(dict[str, dict[str, object]], document["operations"])
    operations["orientation"]["audience"] = "sections"
    stage = (
        "<!-- ar:roles all -->\nCommon stage obligation.\n"
        "<!-- ar:roles curator -->\nCurator writes this card.\n"
        "<!-- ar:roles worker -->\nWorker supplies the code freeze.\n"
    )
    sources = tuple(
        replace(source, content=stage.encode(), revision=compute_content_digest(stage.encode()))
        if source.path == "operations/orientation.md"
        else source
        for source in all_sources(document)
    )
    result = compile_role_capsule(
        worker_binding(operation="orientation"),
        manifest_bytes(document),
        sources,
        selection_for(document),
    )
    text = result.render_instructions()
    assert "Common stage obligation." in text
    assert "Worker supplies the code freeze." in text
    assert "Curator writes this card." not in text
    assert "<!-- ar:roles" not in text


def test_compiler_carries_declared_step_detail_bytes_as_separate_references() -> None:
    document = fixture_manifest_document()
    document["references"] = {
        "sample-detail": {
            "source": "reference/sample-detail.md",
            "roles": ["worker"],
            "step_detail": True,
            "step": "when writing the sample",
        }
    }
    role = "Read {{detail:reference/sample-detail.md}} when writing the sample.\n"
    sources = (
        *(
            replace(source, content=role.encode(), revision=compute_content_digest(role.encode()))
            if source.path == "roles/worker.md"
            else source
            for source in all_sources(document)
        ),
        skill_source(
            name="sample-detail", path="reference/sample-detail.md", content="Exact format.\n"
        ),
    )
    result = compile_role_capsule(
        worker_binding(),
        manifest_bytes(document),
        sources,
        replace(selection_for(document), skill=("reference/sample-detail.md",)),
    )
    (reference,) = result.capsule.detail_references
    assert reference.source_path == "reference/sample-detail.md"
    assert reference.content == b"Exact format.\n"
    assert "Exact format." not in result.render_instructions()
    changed = tuple(
        replace(
            source,
            content=b"Changed format.\n",
            revision=compute_content_digest(b"Changed format.\n"),
        )
        if source.path == "reference/sample-detail.md"
        else source
        for source in sources
    )
    other = compile_role_capsule(
        worker_binding(),
        manifest_bytes(document),
        changed,
        replace(selection_for(document), skill=("reference/sample-detail.md",)),
    )
    assert other.semantic_digest != result.semantic_digest


def test_handover_writes_immutable_source_details_beside_its_file(tmp_path: Path) -> None:

    report = tmp_path / "request.md"
    reference = CapsuleDetailReference(
        "reference/sample.md",
        compute_content_digest(b"Frozen detail.\n"),
        b"Frozen detail.\n",
        "when authoring",
    )
    paths = role_handover_artifacts.write_detail_artifacts(str(report), (reference,))
    stored = paths["reference/sample.md"]
    assert stored.parent == report.parent
    assert stored.read_bytes() == reference.content
    assert stored.stat().st_mode & 0o222 == 0
    assert role_handover_artifacts.write_detail_artifacts(str(report), (reference,)) == paths


def test_launch_composition_names_frozen_details_by_their_request_paths(tmp_path: Path) -> None:

    reference = CapsuleDetailReference(
        "reference/sample.md",
        compute_content_digest(b"Frozen detail.\n"),
        b"Frozen detail.\n",
        "when authoring",
    )
    delivery = CapsuleDelivery(
        CapsuleBindingIdentity("fixture", "task.json", "worker", "implementation"),
        "Read {{detail:reference/sample.md}} when authoring.",
        compute_content_digest(b"capsule"),
        (reference,),
    )
    instructions, record = compose_launch_instructions(delivery, str(tmp_path / "request.md"), {})
    path = Path(record["detailReferences"][0]["path"])
    assert path.read_bytes() == reference.content
    assert str(path) in instructions
    assert "{{detail:" not in instructions


def test_curator_receives_ruled_step_details_separately_and_keeps_writer_boundaries() -> None:
    """Command/report details stay separate; the operation links the serving authority home."""
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    manifest = (root / "composition-manifest.json").read_bytes()
    binding = worker_binding(operation="curation", granted=frozenset(PUBLIC_TOOLS))
    binding = replace(
        binding, admitted=replace(binding.admitted, seat=CapsuleRoleSeat("curator", "leaf"))
    )
    admission = routed_admission_for(
        root, "composition-manifest.json", manifest, CapsuleSeatAddress("curator", "curation")
    )
    outcome = compile_admitted_capsule(binding, admission)
    assert outcome.ok, outcome.render_explanation()
    assert outcome.result is not None
    result = outcome.result
    text = result.render_instructions()
    references = {entry.source_path: entry for entry in result.capsule.detail_references}
    worklist = references["reference/curator/worklist.md"]
    writer = references["reference/knowledge/serving-build.md"]
    publication = references["reference/curator/publication-report.md"]
    cards = references["reference/curator/card-formats-and-citations.md"]
    assert "reference/message-handover.md" in references
    assert "reference/curator-intake-details.md" not in references
    role_block = next(
        unit.block.content
        for unit in result.capsule.instruction_units
        if unit.block.source_path == "roles/curator.md"
    )
    intake_syntax = "`knowledge_read` on `agents-remember-task` takes `workspace.memoryRoot`"
    assert intake_syntax in role_block
    assert intake_syntax in text
    with pytest.raises(AssertionError):
        assert intake_syntax in role_block.replace(intake_syntax, "", 1)
    messages = references["reference/message-handover.md"]
    assert "reference/curator-message-details.md" not in references
    for reference in (worklist, writer, publication, cards, messages):
        assert reference.content == (root / reference.source_path).read_bytes()
        assert len(reference.content.decode().split()) <= 2500
        assert f"{{{{detail:{reference.source_path}}}}}" in text
        assert reference.content.decode() not in text
    for reference, syntax in (
        (messages, "Put the full message text in `body` and its files in `attach`."),
    ):
        assert syntax in reference.content.decode()
        assert syntax not in text
    _assert_curator_message_detail(
        text,
        "{{detail:reference/message-handover.md}}",
        messages.step,
        messages.content.decode(),
    )
    for clause in CURATOR_MESSAGE_CONTENT:
        mutant = messages.content.decode().replace(clause, "", 1)
        assert mutant != messages.content.decode()
        with pytest.raises(AssertionError):
            _assert_curator_message_detail(
                text, "{{detail:reference/message-handover.md}}", messages.step, mutant
            )
    assert "replace_evidence: true" in (root / "templates/curator-handoff-ingestion.md").read_text()
    assert "replace_evidence: true" not in text
    assert (
        "--authorization-ref '<the admitted authorization>' --commit --json"
        in writer.content.decode()
    )
    assert "--authorization-ref '<the admitted authorization>' --commit --json" not in text
    for semantics in (
        "Exit zero covers both a plan and a write.",
        "Even `written` with no written paths establishes no publication.",
        "Read the actual report state, written paths, record and entry identifiers, and refusals.",
    ):
        assert semantics in publication.content.decode(), semantics
        assert semantics not in text, semantics
    family = _assert_detail_owner(
        result,
        "reference/curator/family-obligations.md",
        "Before deciding family coverage or updating a family, read",
    )
    _assert_owner_clauses(
        family,
        (
            "Implementation alone never rewrites member intent or changes an invariant's or family's meaning.",
        ),
    )
    for boundary in (
        "Require that worklist to be readable before ingest.",
        "These are memory-repository text committed with the leaf memory branch and read by the next planner from its base files.",
        "The commit option authorizes file writes, not Git or acceptance.",
        "Family coverage: authored or updated families with members and routes, and examined unchanged families with reasons.",
    ):
        assert boundary in text, boundary


def test_manager_curation_does_not_receive_curators_card_or_writer_steps() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    manifest = (root / "composition-manifest.json").read_bytes()
    binding = worker_binding(operation="curation", granted=frozenset(PUBLIC_TOOLS))
    binding = replace(
        binding, admitted=replace(binding.admitted, seat=CapsuleRoleSeat("manager", "master"))
    )
    admission = routed_admission_for(
        root, "composition-manifest.json", manifest, CapsuleSeatAddress("manager", "curation")
    )
    outcome = compile_admitted_capsule(binding, admission)
    assert outcome.ok, outcome.render_explanation()
    assert outcome.result is not None
    text = outcome.result.render_instructions()
    assert "**Route the durable knowledge through the real writer.**" not in text
    assert "changed source files → their file-level sidecars" not in text
    assert "curator-writer-details.md" not in text
    assert (
        "The Manager reads ordinary leaf state from reports and retains task acceptance and landing authority."
        in text
    )
    assert "transaction steps remain with the Manager." in text
    assert (
        "Inspect the complete changed-file diff and both verdicts before deciding the gate." in text
    )


def test_d301_real_capsules_deliver_named_step_files_as_frozen_launch_artifacts(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    manifest = (root / "composition-manifest.json").read_bytes()
    for role, operation, expected in (
        (
            "reviewer",
            "orientation",
            {"reference/review-baseline.md"},
        ),
        ("bootstrap", "bootstrap", {"reference/bootstrap/foundation-state.md"}),
        (
            "curator",
            "curation",
            {"reference/curator/intent-reconciliation.md", "reference/curator/memory-handover.md"},
        ),
    ):
        metadata = parse_composition_manifest(manifest).roles[role]
        result = _compile_corpus_pair(
            root, manifest, CapsuleRoleSeat(metadata.role, metadata.altitude), operation
        )
        references = {entry.source_path: entry for entry in result.capsule.detail_references}
        assert expected <= references.keys()
        assert {source for source in references if source.endswith("-d301.md")} == set()
        delivery = capsule_delivery_from(result)
        text, record = compose_launch_instructions(
            delivery, str(tmp_path / role / "request.md"), {}
        )
        assert "{{detail:" not in text
        if role == "reviewer":
            role_block = next(
                unit.block
                for unit in result.capsule.instruction_units
                if unit.block.source_path == "roles/reviewer.md"
            )
            role_source = root / "roles/reviewer.md"
            assert role_block.revision == compute_content_digest(role_source.read_bytes())
            role_text = role_block.content
            assert (
                role_text
                == strip_contract_markers(
                    replace(role_block, content=strip_frontmatter(role_source.read_text()))
                ).content
            )
            inline_rules = [row for row in INLINE_RULE_CLAUSES if row[1] == "roles/reviewer.md"]
            assert len(inline_rules) == 7
            for retired, _, heading, clauses in inline_rules:
                assert retired not in references
                assert role_text.count(heading + "\n") == 1
                section = role_text.split(heading + "\n", 1)[1].split("\n## ", 1)[0]
                reading = " ".join(section.split())
                for clause in clauses:
                    normalized_clause = " ".join(clause.split())
                    assert normalized_clause in reading
                    assert normalized_clause in " ".join(text.split())
                    with pytest.raises(AssertionError):
                        assert clause in reading.replace(clause, "", 1)
        paths = {entry["sourcePath"]: Path(entry["path"]) for entry in record["detailReferences"]}
        for source in expected:
            # Source identity and emitted identity are separate: link binding rewrites details.
            assert references[source].content == (root / source).read_bytes()
            emitted = paths[source].read_bytes()
            emitted_record = next(
                item for item in record["detailReferences"] if item["sourcePath"] == source
            )
            assert compute_content_digest(emitted) == emitted_record["revision"]
            assert len(emitted) == emitted_record["sizeBytes"]
            assert str(paths[source]) in text
            assert emitted.decode() not in text
            assert references[source].content.decode() not in text
            assert paths[source].stat().st_mode & 0o222 == 0


def _without_action_operand(text: str, location: str, action: str) -> str:
    cue = " ".join(action.replace("*", "").replace("`", "").split())
    paragraphs = [
        paragraph
        for paragraph in text.split("\n\n")
        if cue in " ".join(paragraph.replace("*", "").replace("`", "").split())
        and location in paragraph
    ]
    assert len(paragraphs) == 1, (location, action)
    paragraph = paragraphs[0]
    assert paragraph.count(location) == 1, (location, action)
    return text.replace(paragraph, paragraph.replace(location, "", 1), 1)


def _assert_detail_owner(result: CapsuleCompilationResult, source: str, action: str) -> str:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    reference = next(
        item for item in result.capsule.detail_references if item.source_path == source
    )
    assert reference.content == (root / source).read_bytes()
    assert reference.step.strip()
    location = "{{detail:" + source + "}}"
    text = result.render_instructions()
    _assert_bound_action(text, location, action)
    mutant = _without_action_operand(text, location, action)
    with pytest.raises(AssertionError):
        _assert_bound_action(mutant, location, action)
    return reference.content.decode()


def _assert_owner_clauses(content: str, clauses: tuple[str, ...]) -> None:
    for clause in clauses:
        assert clause in content, clause
        mutant = content.replace(clause, "", 1)
        assert mutant != content
        with pytest.raises(AssertionError):
            assert clause in mutant, clause


def _assert_review_boundary_owner(caller: str, location: str, action: str) -> str:
    _assert_bound_action(caller, location, action)
    mutant = _without_action_operand(caller, location, action)
    with pytest.raises(AssertionError):
        _assert_bound_action(mutant, location, action)
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    content = (root / "operations/review.md").read_text()
    boundaries = content.split("## Boundaries\n", 1)[1]
    _assert_owner_clauses(
        boundaries,
        (
            "Memory review uses separate sealed reports, finding identifiers and its own pass count.",
            "The Reviewer records its own lane with `begin_review` and `record_review`, using `reviewKind=memory`.",
            "Code state stays unchanged.",
            "Memory review opens no code review-state round, resets no passed code review, code verdict or code baseline, and adds no identifiers to that baseline.",
            "Its ordinary pass limit is the same as code review's.",
            "A pass beyond the settings limit needs the Architect's or developer's instruction recorded by the Manager.",
        ),
    )
    return boundaries


def test_d301_curator_keeps_whole_handover_and_step_limits_in_capsule() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    result = _compile_corpus_pair(
        root,
        (root / "composition-manifest.json").read_bytes(),
        CapsuleRoleSeat("curator", "leaf"),
        "curation",
    )
    text = result.render_instructions()
    for boundary in (
        "Maintain the entity catalog, and edit an entity only for a real entity change.",
        "Reconcile current intent, ruled intent and implemented reality. Surface every material divergence",
    ):
        assert boundary in text, boundary
    scope_location = "../templates/curator-handoff-ingestion.md#curator-authored-semantic-scope"
    _assert_bound_action(
        text,
        scope_location,
        # The scope link follows the field-list and input-section links in curator.md:41.
        "the ingestion shape of `knowledge_write` on `agents-remember-task` and",
    )
    with pytest.raises(AssertionError):
        _assert_bound_action(
            text.replace(scope_location, "", 1),
            scope_location,
            "the ingestion shape of `knowledge_write` on `agents-remember-task` and",
        )
    scope = (
        (root / "templates/curator-handoff-ingestion.md")
        .read_text()
        .split("## Curator-authored semantic scope\n", 1)[1]
        .split("## The curator's authored keys", 1)[0]
    )
    _assert_owner_clauses(
        scope,
        CURATOR_SEMANTIC_SCOPE_CLAUSES,
    )
    family = _assert_detail_owner(
        result,
        "reference/curator/family-obligations.md",
        "Before deciding family coverage or updating a family, read",
    )
    # P5-18: the role file's family pointer is the one pointer; the workflow step is plain.
    assert "../reference/curator/family-obligations.md" not in text
    assert (
        "3. Group obligations and change family membership only with a grouping justification "
        "and a re-examined guarantee." in text
    )
    _assert_owner_clauses(
        family,
        (
            "For every scoped obligation, decide whether evidence and project intent justify a joint obligation with others.",
            "A member or membership change requires a fresh examination of the recorded guarantee.",
            "Implementation alone never rewrites member intent or changes an invariant's or family's meaning.",
        ),
    )
    routing = _assert_detail_owner(
        result,
        "reference/curator/change-routing.md",
        "Before routing a changed file or notes item, read",
    )
    _assert_owner_clauses(
        routing,
        (
            "Use the level-three Operational-Notes target only for a notes item with no file, route or entity home.",
            "It is the last resort, never the default for an inconvenient finding.",
        ),
    )
    history_source = "reference/curator/current-prose-and-history.md"
    history = next(
        item for item in result.capsule.detail_references if item.source_path == history_source
    )
    assert history.content == (root / history_source).read_bytes()
    location = "{{detail:" + history_source + "}}"
    action = "Before updating current overview prose or answering changed-file history, read"
    _assert_bound_action(text, location, action)
    mutant = _without_action_operand(text, location, action)
    with pytest.raises(AssertionError):
        _assert_bound_action(mutant, location, action)
    for clause in (
        "Never add an overview heading named for a leaf or date.",
        "Update the body section that owns the meaning.",
        "Add no task log.",
    ):
        assert clause in history.content.decode(), clause
        mutant = history.content.decode().replace(clause, "", 1)
        assert mutant != history.content.decode()
        with pytest.raises(AssertionError):
            assert clause in mutant, clause
    handoff = next(
        item
        for item in result.capsule.detail_references
        if item.source_path == "reference/curator/memory-handover.md"
    )
    assert "{{detail:reference/curator/memory-handover.md}}" in text
    _assert_owner_clauses(
        handoff.content.decode(),
        (
            "Each handover names the leaf, the Worker's exact code head and change hash, the memory candidate's identity, the report or verdict path, and the whole next stretch requested.",
            "The Reviewer compares the leaf's base and candidate in Git: the code trees and the knowledge files in both memory trees. The Curator records no separate comparison.",
        ),
    )
    _assert_review_boundary_owner(
        handoff.content.decode(),
        "../../operations/review.md#boundaries",
        "Before sending a memory candidate for review, read",
    )


def _required_action_paragraph(
    text: str, location: str, action: str, role: str, source: str
) -> str:
    fragment, suffix = ROLE_POINTER_QUALIFICATIONS.get((role, source, action), ("", ""))
    cue = " ".join(action.replace("*", "").replace("`", "").split())
    ending = re.escape(fragment) if fragment else r"(?:#[^)]*)?"
    pattern = (
        re.escape(cue)
        + r"(?:(?!\]\().)*?\]\("
        + re.escape(location)
        + ending
        + r"\)"
        + re.escape(suffix)
    )
    matches = [
        paragraph
        for paragraph in text.split("\n\n")
        for _match in re.finditer(
            pattern, " ".join(paragraph.replace("*", "").replace("`", "").split())
        )
    ]
    assert len(matches) == 1, (location, action, fragment, suffix)
    _assert_bound_action(matches[0], location, action)
    return matches[0]


def _assert_step_detail_timing(text: str, records: list[dict], role: str, operation: str) -> None:
    actions = [*ROLE_POINTER_ACTIONS[role], *OPERATION_POINTER_ACTIONS[operation][role]]
    expected = {source for source, _ in actions}
    assert {entry["sourcePath"] for entry in records} == expected
    assert len(records) == len(expected)
    for entry in records:
        assert entry["step"].strip()
        required = [action for source, action in actions if source == entry["sourcePath"]]
        ordinary = ORDINARY_DETAIL_LINK_COUNTS.get(role + "/" + operation, {}).get(
            entry["sourcePath"], 0
        )
        # Count all exact final URL targets, but do not mistake ordinary section links
        # for additional metadata-driven read actions. Each action below remains unique.
        assert (
            text.count("](" + entry["path"] + ")") + text.count("](" + entry["path"] + "#")
            == len(required) + ordinary
        )
        for action in required:
            _required_action_paragraph(text, entry["path"], action, role, entry["sourcePath"])
        if entry["sourcePath"] == "reference/message-handover.md" and role == "curator":
            _assert_curator_message_detail(
                text, entry["path"], entry["step"], Path(entry["path"]).read_text()
            )


def _assert_bound_action(text: str, location: str, action: str) -> None:
    assert action.strip()
    cue = " ".join(action.replace("*", "").replace("`", "").split())
    pattern = re.escape(cue) + r"(?:(?!\]\().)*?\]\(" + re.escape(location) + r"(?:#[^)]*)?\)"
    matches = [
        match
        for paragraph in text.split("\n\n")
        for match in re.finditer(
            pattern, " ".join(paragraph.replace("*", "").replace("`", "").split())
        )
    ]
    assert len(matches) == 1, (location, action)


def _assert_detail_roundtrip(root: Path, entry: dict, reference: CapsuleDetailReference) -> None:
    assert entry["sourcePath"] == reference.source_path
    assert reference.content == (root / reference.source_path).read_bytes()
    assert reference.revision == compute_content_digest(reference.content)
    assert entry["step"] == reference.step
    emitted = Path(entry["path"]).read_bytes()
    assert entry["revision"] == compute_content_digest(emitted)
    assert entry["sizeBytes"] == len(emitted)
    assert Path(entry["path"]).stat().st_mode & 0o222 == 0


def test_step_details_are_named_for_reaching_their_step_not_intake(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    manifest = (root / "composition-manifest.json").read_bytes()
    parsed = parse_composition_manifest(manifest)
    seen: set[str] = set()
    checked: set[tuple[str, str]] = set()
    for role in parsed.role_order:
        for operation in parsed.roles[role].operations:
            metadata = parsed.roles[role]
            result = _compile_corpus_pair(
                root, manifest, CapsuleRoleSeat(metadata.role, metadata.altitude), operation
            )
            text, record = compose_launch_instructions(
                capsule_delivery_from(result), str(tmp_path / role / operation / "request.md"), {}
            )
            entries = record["detailReferences"]
            _assert_step_detail_timing(text, entries, role, operation)
            with pytest.raises(AssertionError):
                _assert_step_detail_timing(text, [*entries, entries[0]], role, operation)
            references = {item.source_path: item for item in result.capsule.detail_references}
            for entry in entries:
                reference = references[entry["sourcePath"]]
                _assert_detail_roundtrip(root, entry, reference)
                damaged = [dict(item, step="") if item is entry else item for item in entries]
                with pytest.raises(AssertionError):
                    _assert_step_detail_timing(text, damaged, role, operation)
                mismatched = dict(entry, step=entry["step"] + " altered")
                assert mismatched != entry
                with pytest.raises(AssertionError):
                    _assert_detail_roundtrip(root, mismatched, reference)
            actions = [*ROLE_POINTER_ACTIONS[role], *OPERATION_POINTER_ACTIONS[operation][role]]
            for source, action in actions:
                entry = next(item for item in entries if item["sourcePath"] == source)
                required_paragraph = _required_action_paragraph(
                    text, entry["path"], action, role, source
                )
                pattern = (
                    re.escape(action)
                    + r"(?:(?!\]\().)*?\]\((?P<target>"
                    + re.escape(entry["path"])
                    + r"(?:#[^)]*)?)\)"
                )
                matches = list(re.finditer(pattern, required_paragraph, re.S))
                assert len(matches) == 1
                start, end = matches[0].span("target")
                broken_paragraph = (
                    required_paragraph[:start] + "missing-detail" + required_paragraph[end:]
                )
                missing_action_target = text.replace(required_paragraph, broken_paragraph, 1)
                for mutant in (
                    text.replace(
                        required_paragraph,
                        required_paragraph.replace(action, "Read during intake", 1),
                        1,
                    ),
                    missing_action_target,
                    missing_action_target
                    + "\n\nRead during intake: [detail]("
                    + entry["path"]
                    + ")",
                    text + "\n\n" + required_paragraph,
                ):
                    assert mutant != text
                    with pytest.raises(AssertionError):
                        _assert_step_detail_timing(mutant, entries, role, operation)
            message = next(
                (
                    entry
                    for entry in entries
                    if entry["sourcePath"] == "reference/message-handover.md"
                ),
                None,
            )
            if role == "curator":
                assert message is not None
                for clause in CURATOR_MESSAGE_TIMING:
                    mutant = text.replace(clause, "read during intake", 1)
                    assert mutant != text
                    with pytest.raises(AssertionError):
                        _assert_step_detail_timing(mutant, entries, role, operation)
            seen.update(item["sourcePath"] for item in entries)
            checked.add((role, operation))
    assert checked == {
        (role, op) for op, entry in parsed.operations.items() for role in entry.applies_to_roles
    }
    assert seen == {entry.source for entry in parsed.detail_references}


def test_curator_execution_support_is_an_unconditional_inline_output() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    text = _compile_corpus_pair(
        root,
        (root / "composition-manifest.json").read_bytes(),
        CapsuleRoleSeat("curator", "leaf"),
        "curation",
    ).render_instructions()
    outputs = text.split("## Produce the handover\n", 1)[1].split("## Use direct handovers", 1)[0]
    clause = "Every handover describes actual host and helper support, delegated work and how it was verified. State explicitly when none was used."
    assert clause in outputs
    assert "{{detail:" not in clause


def _delivered_curation_for_d301(tmp_path: Path) -> tuple[str, dict]:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    result = _compile_corpus_pair(
        root,
        (root / "composition-manifest.json").read_bytes(),
        CapsuleRoleSeat("curator", "leaf"),
        "curation",
    )
    delivery = capsule_delivery_from(result)
    return compose_launch_instructions(delivery, str(tmp_path / "request.md"), {})


def test_d301_external_backing_output_stays_inline(tmp_path: Path) -> None:
    text, _record = _delivered_curation_for_d301(tmp_path)
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    role = strip_frontmatter((root / "roles/curator.md").read_text())
    outputs = role.split("## Produce the handover\n", 1)[1].split("## Use direct handovers", 1)[0]
    clauses = (
        "Cite an external document supporting a card as an `external` sidecar reference, never as a repository path with a Git blob.",
        "Name the document in the handover evidence for the record's `origin`, so each backed record identifies its supporting document.",
    )
    _assert_owner_clauses(outputs, clauses)
    _assert_owner_clauses(text, clauses)
    assert "Before citing external evidence supporting a card, read" not in text


def test_d301_bootstrap_requires_reproducibility_not_reader_execution() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    manifest = (root / "composition-manifest.json").read_bytes()
    role = parse_composition_manifest(manifest).roles["bootstrap"]
    text = _compile_corpus_pair(
        root, manifest, CapsuleRoleSeat(role.role, role.altitude), "bootstrap"
    ).render_instructions()
    assert (
        "Report claims are checked against the recorded observation, not a promise that a later live call returns the same answer."
        in text
    )
    assert "record the surface, exact arguments, recorded answer and before and after state" in text
    assert (
        "so a reader can reproduce the observation from that evidence without asking Bootstrap."
        in text
    )
    assert "Readers must **re-derive every report claim**" not in text
    assert "Readers must get the same answer from the same call." not in text


def test_d301_delivered_authoring_resolves_actual_capsule_routing(tmp_path: Path) -> None:
    text, record = _delivered_curation_for_d301(tmp_path)
    entry = next(
        item
        for item in record["detailReferences"]
        if item["sourcePath"] == "reference/curator/intent-reconciliation.md"
    )
    authoring = Path(entry["path"]).read_text()
    assert "role admission and handover authority remain in the curation operation." in authoring
    assert "under the handover rules below" not in authoring
    assert "## Failure handling" in text and "## Handoff / exit" in text
    assert "ask the Worker directly about code or the Reviewer about its verdict." in text
    assert "Ask the Manager only about ownership or authority." in text


def test_d301_handoff_file_is_read_for_first_review_then_reused_for_repairs(tmp_path: Path) -> None:
    text, record = _delivered_curation_for_d301(tmp_path)
    entry = next(
        item
        for item in record["detailReferences"]
        if item["sourcePath"] == "reference/curator/memory-handover.md"
    )
    assert entry["step"].strip()
    _assert_bound_action(
        text,
        entry["path"],
        "Before sending a memory candidate for review or returning a memory repair, read",
    )
    assert "Reuse it when returning a repair." in text
    assert "### Obtain the independent fact-check" in text
    assert "### Repair and republish" in text
    handoff = Path(entry["path"]).read_text()
    assert "operations/review.md" not in {item["sourcePath"] for item in record["detailReferences"]}
    with shipped_composition_corpus() as (corpus, _manifest):
        review_source = (corpus / "operations/review.md").resolve()
        review_target = quote(review_source.as_posix(), safe="/:@") + "#boundaries"
        root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
        assert review_source.read_bytes() == (root / "operations/review.md").read_bytes()
    for clause in (
        "## Send the whole requested review or repair",
        "Before sending a memory candidate for review, read",
        review_target,
    ):
        assert clause in handoff
        mutant = handoff.replace(clause, "", 1)
        assert mutant != handoff
        with pytest.raises(AssertionError):
            assert clause in mutant
    _assert_review_boundary_owner(
        handoff, review_target, "Before sending a memory candidate for review, read"
    )


def test_curator_detail_names_use_only_curation_workflow_numbers_and_titles(tmp_path: Path) -> None:
    text, record = _delivered_curation_for_d301(tmp_path)
    workflow = text.split("## Normal workflow\n", 1)[1].split("## Authority gates", 1)[0]
    numbered = [int(match[1]) for match in re.finditer(r"^([1-9])\. ", workflow, re.M)]
    assert numbered == [1, 2, 3, 4, 5]
    for heading in (
        "### Run the complete curation checks",
        "### Obtain the independent fact-check",
        "### Repair and republish",
    ):
        assert heading in workflow
    assert {entry["sourcePath"] for entry in record["detailReferences"]} == {
        source
        for source, _ in [
            *ROLE_POINTER_ACTIONS["curator"],
            *OPERATION_POINTER_ACTIONS["curation"]["curator"],
        ]
    }
    _assert_step_detail_timing(text, record["detailReferences"], "curator", "curation")
    role_process = text.split("## Reconcile, author and check\n", 1)[1].split(
        "## Produce the handover", 1
    )[0]
    assert not re.search(r"^[1-9]\.", role_process, re.M)


def test_nested_curator_file_names_use_the_same_operation_titles(tmp_path: Path) -> None:
    text, record = _delivered_curation_for_d301(tmp_path)
    entries = {item["sourcePath"]: item for item in record["detailReferences"]}
    # Each successor is read through its actual launch artifact, with rewritten sibling links.
    history = Path(entries["reference/curator/current-prose-and-history.md"]["path"]).read_text()
    ingest = Path(entries["reference/curator/knowledge-ingest.md"]["path"]).read_text()
    cards = entries["reference/curator/card-formats-and-citations.md"]
    writer = entries["reference/knowledge/serving-build.md"]
    _assert_bound_action(history, cards["path"], "Before writing card history, read")
    _assert_bound_action(
        ingest, writer["path"], "Before a command-line run that decides or writes, read"
    )
    for source, action in (
        (
            "reference/curator/knowledge-ingest.md",
            "Before preparing or ingesting authored knowledge, read",
        ),
        (
            "reference/knowledge/serving-build.md",
            "Before a command-line run that decides or writes, read",
        ),
    ):
        _assert_bound_action(text, entries[source]["path"], action)
    cli = Path(writer["path"]).read_text()
    assert (
        "Before a command-line interface (CLI) run that decides or writes, bind it to the starting server's `server_info.servingBuild`."
        in cli
    )
    assert "Orientation" in cli and "owns serving-build authority." in cli


def test_leaf_role_texts_mark_only_their_own_completed_steps() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles/roles"
    reviewer = (root / "reviewer.md").read_text()
    for clause in (
        "Before reporting, mark the task step naming your part `done` with `task_doc` on `agents-remember-task`, using `set_step`, its step identifier and a one-line note.",
        "Do this for the review step only after the last round is recorded as passed.",
        "If no step names your part, report that and set nothing.",
        "Task status and acceptance remain the Manager's.",
    ):
        assert clause in reviewer
    worker = (root / "worker.md").read_text()
    curator = (root / "curator.md").read_text()
    assert "When your part is complete, mark its task-document step `done`" in worker
    assert "If no step names your part, report that and mark nothing." in worker
    assert (
        "Incomplete, blocked, progress and escalation reports never mark unfinished work done."
        in worker
    )
    assert (
        "This covers only your build and evidence steps, not review, curation, task status or acceptance."
        in worker
    )
    assert "When your part is complete, mark only its task-document step `done`" in curator
    assert "If no step names your part, report that and set nothing." in curator
    assert (
        "An incomplete, blocked, progress or escalation report never marks unfinished work done."
        in curator
    )
    assert "This changes no task status, acceptance or landing state." in curator
    assert "You set no step for another agent." in (root / "manager.md").read_text()


def test_every_curator_stage_keeps_the_serving_build_producer_boundary() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    manifest = (root / "composition-manifest.json").read_bytes()
    authority = (
        (root / "operations/orientation.md")
        .read_text()
        .split("## Bind curation to the serving build\n", 1)[1]
        .split("\n<!-- ar:roles", 1)[0]
    )
    assert CURATOR_SERVING_STAGE_BOUNDARY in authority
    for operation in ("orientation", "curation", "recovery"):
        result = _compile_corpus_pair(root, manifest, CapsuleRoleSeat("curator", "leaf"), operation)
        text = result.render_instructions()
        if operation == "orientation":
            assert CURATOR_SERVING_STAGE_BOUNDARY in text
        else:
            action = (
                "Before deciding or writing memory, read"
                if operation == "curation"
                else "Before resuming a memory decision or write, read"
            )
            pointer = "[Bind curation to the serving build](orientation.md#bind-curation-to-the-serving-build)"
            assert action + " " + pointer in text
            mutant = text.replace(pointer, "", 1)
            assert mutant != text
            with pytest.raises(AssertionError):
                assert action + " " + pointer in mutant
        cli = next(
            item
            for item in result.capsule.detail_references
            if item.source_path == "reference/knowledge/serving-build.md"
        )
        assert cli.content == (root / cli.source_path).read_bytes()
        assert (
            "[Orientation](../../operations/orientation.md#bind-curation-to-the-serving-build) owns serving-build authority."
            in cli.content.decode()
        )
        for boundary in (
            "Use that build both to decide and write.",
            "A bare `agents-remember` from the executable search path may be stale; report any such use and its observed identity honestly.",
        ):
            assert boundary in text, (operation, boundary)


def test_memory_review_instructions_select_the_independent_recorded_lane() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    manifest = (root / "composition-manifest.json").read_bytes()
    result = _compile_corpus_pair(root, manifest, CapsuleRoleSeat("curator", "leaf"), "orientation")
    curator = result.render_instructions()
    _assert_review_boundary_owner(
        curator,
        "../operations/review.md#boundaries",
        "Before interpreting a memory-review result, read",
    )
    reviewer = _compile_corpus_pair(
        root, manifest, CapsuleRoleSeat("reviewer", "leaf"), "orientation"
    ).render_instructions()
    assert (
        "Before recording an ordinary leaf code round, read [code review round recording]"
        in reviewer
    )
    assert "Before recording a memory round" in reviewer
    assert "[review boundaries](../operations/review.md#boundaries)" in reviewer
    review = (root / "operations/review.md").read_text()
    assert "open no review-state round" not in review
    # P5-18: the Curator's one memory-handover pointer stands in its role file.
    curation = (root / "operations/curation.md").read_text()
    assert "memory-handover.md#send-the-whole-requested-review-or-repair" not in curation
    _assert_bound_action(
        (root / "roles/curator.md").read_text(),
        "{{detail:reference/curator/memory-handover.md}}",
        "Before sending a memory candidate for review or returning a memory repair, read",
    )
    handoff = _assert_detail_owner(
        result,
        "reference/curator/memory-handover.md",
        "Before sending a memory candidate for review or returning a memory repair, read",
    )
    _assert_review_boundary_owner(
        handoff,
        "../../operations/review.md#boundaries",
        "Before sending a memory candidate for review, read",
    )


def _assert_curator_message_detail(text: str, location: str, step: str, content: str) -> None:
    assert step.strip()
    for action in CURATOR_MESSAGE_TIMING:
        _required_action_paragraph(
            text, location, action, "curator", "reference/message-handover.md"
        )
    assert "curator-message-details.md" not in text
    for clause in CURATOR_MESSAGE_CONTENT:
        assert content.count(clause) == 1, clause
        assert clause not in text, clause


def _compile_corpus_pair(
    root: Path, manifest: bytes, seat: CapsuleRoleSeat, operation: str
) -> CapsuleCompilationResult:
    binding = worker_binding(operation=operation, granted=frozenset(PUBLIC_TOOLS))
    binding = replace(binding, admitted=replace(binding.admitted, seat=seat))
    admission = routed_admission_for(
        root, "composition-manifest.json", manifest, CapsuleSeatAddress(seat.role, operation)
    )
    outcome = compile_admitted_capsule(binding, admission)
    assert outcome.ok, (seat.role, operation, outcome.render_explanation())
    assert outcome.result is not None
    return outcome.result


def test_real_operation_audiences_preserve_pinned_common_and_only_addressed_paragraphs() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    manifest = (root / "composition-manifest.json").read_bytes()
    document = parse_composition_manifest(manifest)
    assert set(document.operations) == set(R125_OPERATION_ROLES)
    checked: set[tuple[str, str]] = set()
    for operation, roles in R125_OPERATION_ROLES.items():
        metadata = document.operations[operation]
        assert set(metadata.applies_to_roles) == set(roles), operation
        for role in roles:
            entry = document.roles[role]
            result = _compile_corpus_pair(
                root, manifest, CapsuleRoleSeat(entry.role, entry.altitude), operation
            )
            assert "<!-- ar:roles" not in result.render_instructions(), (role, operation)
            (stage,) = (
                block
                for block in result.capsule.instructions
                if block.source_path == metadata.source
            )
            assert (
                sha256(stage.content.encode()).hexdigest()
                == V13_STAGE_SHA256[f"{operation}/{role}"]
            ), (role, operation)
            restored = inverse_v13_short_rules(stage.content, f"{operation}/{role}")
            assert (
                sha256(restored.encode()).hexdigest()
                == V13_PRE_SHORT_STAGE_SHA256[f"{operation}/{role}"]
            )
            assert (
                sha256(inverse_v13_paths(restored).encode()).hexdigest()
                == D656_STAGE_SHA256[f"{operation}/{role}"]
            )
            checked.add((role, operation))
    assert checked == {
        (role.role, operation) for role in document.roles.values() for operation in role.operations
    }


def test_operation_audience_markings_preserve_pinned_bytes_and_immutable_closeout() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    document = parse_composition_manifest((root / "composition-manifest.json").read_bytes())
    for operation, expected in D656_OPERATION_SHA256.items():
        metadata = document.operations[operation]
        content = (root / metadata.source).read_bytes()
        assert sha256(content).hexdigest() == V13_OPERATION_SHA256[operation], operation
        restored = inverse_v13_short_rules(content.decode(), "operation/" + operation)
        assert sha256(restored.encode()).hexdigest() == V13_PRE_SHORT_OPERATION_SHA256[operation], (
            operation
        )
        assert sha256(inverse_v13_paths(restored).encode()).hexdigest() == expected, operation
        if operation == "authorized-closeout":
            assert metadata.audience == "all"
            assert metadata.source == "operations/closeout.md"
        else:
            assert metadata.audience == "sections", operation
            assert content.startswith(b"<!-- ar:roles all -->\n"), operation


def test_repairing_roles_stop_after_three_repairs_of_the_same_check_or_finding() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles/roles"
    worker = (root / "worker.md").read_text()
    curator = (root / "curator.md").read_text()
    assert (
        "When the same check or finding still fails after three of your repairs, stop repairing."
        in worker
    )
    assert (
        "Write what you tried and what still fails, then tell the Manager; it steps the seat up or answers otherwise."
        in worker
    )
    assert (
        "If the same check or finding still fails after three repairs of your own, stop repairing, record what you tried and what still fails, and tell the Manager."
        in curator
    )
    assert "The Manager increases the seat's capability or answers otherwise." in curator
