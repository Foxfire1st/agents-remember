"""Stage audience selection preserves role boundaries in compiled capsules."""

from __future__ import annotations

import re
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest
from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.cli import role_handover_artifacts
from agents_remember.cli.role_instruction_details import compose_launch_instructions
from agents_remember.models.role_capsules.compiler import compile_role_capsule
from agents_remember.models.role_capsules.manifest import parse_composition_manifest
from agents_remember.models.role_capsules.types import (
    CapsuleCompilationResult,
    CapsuleDetailReference,
    CapsuleRoleSeat,
    compute_content_digest,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.serving.capsule_delivery import CapsuleBindingIdentity, CapsuleDelivery
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
    """The actual launch corpus exports syntax, not serving-build or readback authority."""
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
    worklist = references["reference/curator-worklist-details.md"]
    writer = references["reference/curator-writer-details.md"]
    cards = references["reference/curator-card-details.md"]
    assert {
        "reference/curator-intake-details.md",
        "reference/curator-message-details.md",
    } <= references.keys()
    intake = references["reference/curator-intake-details.md"]
    messages = references["reference/curator-message-details.md"]
    for reference in (worklist, writer, cards, intake, messages):
        assert reference.content == (root / reference.source_path).read_bytes()
        assert len(reference.content.decode().split()) <= 2500
        assert f"{{{{detail:{reference.source_path}}}}}" in text
        assert reference.content.decode() not in text
    for reference, syntax in (
        (intake, "`knowledge_read` takes `memoryRoot`"),
        (messages, "The full text goes in `body` and its files in `attach`"),
    ):
        assert syntax in reference.content.decode()
        assert syntax not in text
    assert "replace_evidence: true" in worklist.content.decode()
    assert "replace_evidence: true" not in text
    assert "--authorization-ref <ref> --commit --json" in writer.content.decode()
    assert "--authorization-ref <ref> --commit --json" not in text
    for boundary in (
        "Require a readable persisted leaf worklist before ingest.",
        "There is no dataset to\n  publish.",
        "On development installs, only the serving build produces:",
        "Use the clean main checkout; its serving commit is the ruler.",
        "substitute no producer after refusal.",
        "Zero exit does not prove a write.",
        "Use report state, listed records/entries and refusals as evidence; report refusals exactly as returned.",
        "Implementation alone never changes invariant or family meaning.",
        "Include **family coverage**: authored/updated families with members/routes, and examined unchanged families with reasons.",
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
    assert "reads leaf state from reports and retains task acceptance and landing authority" in text
    assert "transaction steps stay with the\nManager." in text
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
            {
                f"reference/reviewer-{part}-d301.md"
                for part in ("orient", "baseline", "adjudication", "memory", "master-exit")
            },
        ),
        ("bootstrap", "bootstrap", {"reference/bootstrap-foundation-d301.md"}),
        (
            "curator",
            "curation",
            {"reference/curator-authoring-d301.md", "reference/curator-handoff-d301.md"},
        ),
    ):
        metadata = parse_composition_manifest(manifest).roles[role]
        result = _compile_corpus_pair(
            root, manifest, CapsuleRoleSeat(metadata.role, metadata.altitude), operation
        )
        references = {entry.source_path: entry for entry in result.capsule.detail_references}
        assert expected <= references.keys()
        assert {source for source in references if source.endswith("-d301.md")} == expected
        delivery = CapsuleDelivery(
            CapsuleBindingIdentity("fixture", "task.json", role, operation),
            result.render_instructions(),
            result.semantic_digest,
            result.capsule.detail_references,
        )
        text, record = compose_launch_instructions(
            delivery, str(tmp_path / role / "request.md"), {}
        )
        assert "{{detail:" not in text
        paths = {entry["sourcePath"]: Path(entry["path"]) for entry in record["detailReferences"]}
        for source in expected:
            assert paths[source].read_bytes() == references[source].content
            assert paths[source].read_bytes() == (root / source).read_bytes()
            assert str(paths[source]) in text
            assert references[source].content.decode() not in text
            assert paths[source].stat().st_mode & 0o222 == 0


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
        "**Before ingest**, each invariant's `scope` states applicability, conditions and exclusions; provenance is not scope.",
        "Implementation alone never changes invariant or family meaning.",
        "Each handover names leaf, code head/change hash, memory identity, report/verdict path and whole next stretch.",
        "Reviewer records `begin_review`/`record_review`, `reviewKind=memory`: separate sealed memory IDs/reports/count, code-review limit; code state unchanged.",
        "Extras need Manager-recorded architect/developer approval.",
        "Reviewer compares Git bases/candidates; Curator records no separate comparison.",
        "No task/date overview headings or task-log dumping.",
        "Group only justified obligations; membership changes prompt a fresh guarantee check.",
        "L3 is last resort; entity edits need real entity changes.",
        "**Reconcile** current intent, ruled intent and implemented reality; surface every material divergence.",
    ):
        assert boundary in text, boundary


def test_step_details_are_named_for_reaching_their_step_not_intake(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    manifest = (root / "composition-manifest.json").read_bytes()
    seen: set[str] = set()

    def check(text: str, records: list[dict]) -> None:
        for entry in records:
            assert "when you reach" in entry["step"] and "not before" in entry["step"]
            sentences = re.split(r"(?<=\.)\s+|\n", text)
            naming = [sentence for sentence in sentences if entry["path"] in sentence]
            assert naming, entry["sourcePath"]
            for sentence in naming:
                assert "when you reach" in sentence and "not before" in sentence, sentence
                assert "intake" not in sentence.lower(), sentence

    for role, operation in (
        ("curator", "curation"),
        ("reviewer", "orientation"),
        ("bootstrap", "bootstrap"),
    ):
        metadata = parse_composition_manifest(manifest).roles[role]
        result = _compile_corpus_pair(
            root, manifest, CapsuleRoleSeat(metadata.role, metadata.altitude), operation
        )
        delivery = CapsuleDelivery(
            CapsuleBindingIdentity("fixture", "task.json", role, operation),
            result.render_instructions(),
            result.semantic_digest,
            result.capsule.detail_references,
        )
        text, record = compose_launch_instructions(
            delivery, str(tmp_path / role / "request.md"), {}
        )
        entries = record["detailReferences"]
        check(text, entries)
        seen.update(entry["sourcePath"] for entry in entries)
        mutant = text.replace("not before", "read during intake", 1)
        with pytest.raises(AssertionError):
            check(mutant, entries)
    assert seen == {
        entry.source for entry in parse_composition_manifest(manifest).detail_references
    }


def test_curator_execution_support_is_an_unconditional_inline_output() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    text = _compile_corpus_pair(
        root,
        (root / "composition-manifest.json").read_bytes(),
        CapsuleRoleSeat("curator", "leaf"),
        "curation",
    ).render_instructions()
    outputs = text.split("## Outputs\n", 1)[1].split("## Direct handovers", 1)[0]
    clause = "- **Execution support (every handover):** actual host/sub-agent support, delegated work and its verification; explicitly state none where unused."
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
    delivery = CapsuleDelivery(
        CapsuleBindingIdentity("fixture", "task.json", "curator", "curation"),
        result.render_instructions(),
        result.semantic_digest,
        result.capsule.detail_references,
    )
    return compose_launch_instructions(delivery, str(tmp_path / "request.md"), {})


def test_d301_external_backing_output_stays_inline(tmp_path: Path) -> None:
    text, _ = _delivered_curation_for_d301(tmp_path)
    outputs = text.split("## Outputs\n", 1)[1].split("## Direct handovers", 1)[0]
    clause = "Externally backed records use `external` sidecar targets, never repository/Git-blob references; handoff evidence names each backing document for the record's `origin`."
    assert clause in outputs
    assert clause not in outputs.replace(clause, "")


def test_d301_bootstrap_requires_reproducibility_not_reader_execution() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    manifest = (root / "composition-manifest.json").read_bytes()
    role = parse_composition_manifest(manifest).roles["bootstrap"]
    text = _compile_corpus_pair(
        root, manifest, CapsuleRoleSeat(role.role, role.altitude), "bootstrap"
    ).render_instructions()
    assert "Every report claim must be **re-derivable by its reader**" in text
    assert "A reader must be able to get the same answer from the same call." in text
    assert "Readers must **re-derive every report claim**" not in text
    assert "Readers must get the same answer from the same call." not in text


def test_d301_delivered_authoring_resolves_actual_capsule_routing(tmp_path: Path) -> None:
    text, record = _delivered_curation_for_d301(tmp_path)
    entry = next(
        item
        for item in record["detailReferences"]
        if item["sourcePath"] == "reference/curator-authoring-d301.md"
    )
    authoring = Path(entry["path"]).read_text()
    assert "under the Curation capsule's §Failure handling and §Handoff / exit rules:" in authoring
    assert "under the handover rules below" not in authoring
    assert "## Failure handling" in text and "## Handoff / exit" in text
    assert "ask the Worker directly about code or Reviewer about verdict" in text
    assert "ask the Manager only for ownership/authority" in text


def test_d301_handoff_file_is_read_for_first_review_then_reused_for_repairs(tmp_path: Path) -> None:
    text, record = _delivered_curation_for_d301(tmp_path)
    entry = next(
        item
        for item in record["detailReferences"]
        if item["sourcePath"] == "reference/curator-handoff-d301.md"
    )
    assert (
        entry["step"]
        == "when you reach Curation workflow 7: Undergo the independent fact-check, not before; reuse at Curation workflow 8: Repair, then republish"
    )
    assert (
        f"Read [handoff procedures]({entry['path']}) when you reach **Curation workflow 7: Undergo the independent fact-check**, not before; reuse at **Curation workflow 8: Repair, then republish**."
        in text
    )
    assert "7. **Undergo the independent fact-check" in text
    assert "8. **Repair, then republish.**" in text
    handoff = Path(entry["path"]).read_text()
    assert "## Before memory review" in handoff
    assert "separate sealed reports, finding IDs and its own pass count" in handoff
    assert "resets no code verdict" in handoff


def test_curator_detail_names_use_only_curation_workflow_numbers_and_titles(tmp_path: Path) -> None:
    text, record = _delivered_curation_for_d301(tmp_path)
    expected = {
        "curator-intake-details.md": (1, "Reconcile"),
        "curator-authoring-d301.md": (1, "Reconcile"),
        "curator-message-details.md": (1, "Reconcile"),
        "curator-worklist-details.md": (2, "Before ingest"),
        "shared-writing-profile.md": (2, "Before ingest"),
        "curator-writer-details.md": (2, "Before ingest"),
        "curator-card-details.md": (4, "L3 is last resort"),
        "curator-handoff-d301.md": (7, "Undergo the independent fact-check"),
    }
    workflow = text.split("## Normal workflow\n", 1)[1].split("## Authority gates", 1)[0]
    actual = {}
    for match in re.finditer(r"^([1-8])\. (.+)$", workflow, re.M):
        body = match[2]
        bold = re.match(r"\*\*(.+?)\*\*", body)
        actual[int(match[1])] = (
            (bold[1] if bold else body.split(";", 1)[0]).split(" (", 1)[0].rstrip(".")
        )
    assert set(actual) == set(range(1, 9))
    entries = record["detailReferences"]
    assert {Path(entry["sourcePath"]).name for entry in entries} == set(expected)
    for entry in entries:
        number, title = expected[Path(entry["sourcePath"]).name]
        assert actual[number] == title
        label = f"Curation workflow {number}: {title}"
        assert entry["step"].startswith(f"when you reach {label}, not before")
        naming = [line for line in text.splitlines() if entry["path"] in line]
        assert naming and all(f"when you reach **{label}**, not before" in line for line in naming)
    role_process = text.split("## Process\n", 1)[1].split("## Outputs\n", 1)[0]
    assert "Workflow numbering: Curation only." in role_process
    assert not re.search(r"^5\.", role_process, re.M)


def test_nested_curator_file_names_use_the_same_operation_titles(tmp_path: Path) -> None:
    _, record = _delivered_curation_for_d301(tmp_path)
    authoring_entry = next(
        item
        for item in record["detailReferences"]
        if item["sourcePath"] == "reference/curator-authoring-d301.md"
    )
    authoring = Path(authoring_entry["path"]).read_text()
    for line in authoring.splitlines():
        if "reference/curator-card-details.md" in line:
            assert "when you reach **Curation workflow 4: L3 is last resort**, not before" in line
        if "reference/curator-writer-details.md" in line:
            assert "when you reach **Curation workflow 2: Before ingest**, not before" in line
    writer_entry = next(
        item
        for item in record["detailReferences"]
        if item["sourcePath"] == "reference/curator-writer-details.md"
    )
    writer = Path(writer_entry["path"]).read_text()
    assert (
        "Read this file when you reach **Curation workflow 2: Before ingest**, not before."
        in writer
    )


def test_leaf_role_texts_mark_only_their_own_completed_steps() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles/roles"
    sentence = (
        "Before you report, set the task document's step that names your part to `done` through "
        "`task_doc` (`set_step` with its step ID and one line of note); if no step names your part, "
        "say so in your report and set nothing."
    )
    assert sentence in (root / "reviewer.md").read_text()
    for role in ("worker", "curator"):
        text = (root / f"{role}.md").read_text()
        assert "When your part is complete, set its task-document step to `done`" in text
        assert (
            "Incomplete, blocked, progress and escalation reports never mark unfinished work done."
            in text
        )
        assert "if none names your part, report that and set nothing." in text
        assert sentence not in text
    assert "when your last round is recorded as passed" in (root / "reviewer.md").read_text()
    assert "The Manager sets no step for another agent." in (root / "manager.md").read_text()


def test_every_curator_stage_keeps_the_serving_build_producer_boundary() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    manifest = (root / "composition-manifest.json").read_bytes()
    for operation in ("orientation", "curation", "recovery"):
        binding = worker_binding(operation=operation, granted=frozenset(PUBLIC_TOOLS))
        binding = replace(
            binding, admitted=replace(binding.admitted, seat=CapsuleRoleSeat("curator", "leaf"))
        )
        admission = routed_admission_for(
            root, "composition-manifest.json", manifest, CapsuleSeatAddress("curator", operation)
        )
        outcome = compile_admitted_capsule(binding, admission)
        assert outcome.ok, outcome.render_explanation()
        assert outcome.result is not None
        text = outcome.result.render_instructions()
        for boundary in (
            "On development installs, only the serving build produces:",
            "the starting tool server or that same build's CLI.",
            "Use the clean main checkout; its serving commit is the ruler.",
            "Every run states its ruler.",
            "Leaf source never produces leaf memory, admitted or not.",
            "substitute no producer after refusal.",
        ):
            assert boundary in text, (operation, boundary)


# R125's independent recipient oracle. These five complete base paragraphs are
# the only non-common text in the seven operations; ownership is not read from
# their current markers. The remaining base bytes, including every closeout and
# integration boundary, must reach every existing carrier unchanged.
R125_ADDRESSED_PARAGRAPHS = (
    (
        "planning",
        "architect",
        "When the leaf's packet names an effect on a recorded invariant or family, the Architect "
        "declares it in `expectedKnowledgeEffects` through `task_doc` on `agents-remember-task` "
        "before implementation. A leaf whose packet names none declares nothing.",
    ),
    (
        "coordination",
        "manager",
        "The Manager receives only what `../roles/manager.md` lists for the Manager. It supplies "
        "the occasion for the sender to retry a pending hand-over, without relaying contents or "
        "asking for developer confirmation of that occasion. Read reports and task records for "
        "routine state and record which seat holds each next step and since when.",
    ),
    (
        "review",
        "reviewer",
        "For a leaf, hand the verdict directly to the Worker with `role_message` on "
        "`agents-remember-task`; the verdict travels as a file and the message names its path, "
        "never its pasted text; a block names the sealed findings report and asks for the whole "
        "repair stretch. Code PASS also goes directly to the Curator for the passed freeze. "
        "Review its memory change and return findings or PASS directly to it. Name the leaf, "
        "exact object (candidate head and change hash from the Worker's report, or memory change "
        "identity), report/verdict path, and the whole next stretch. Use the sibling role and "
        "exact leaf task references from the handover; product-supplied IDs stay whole and "
        "unchanged. A Worker's factual hand-over gives no review instructions, and the Reviewer "
        "does not read the Worker's chat. A cleanly synced leaf sends no new freeze to you and "
        "opens no round: where no path was changed on both sides, the Worker runs its focused "
        "tests once more and the Manager's closeout preview decides. Where a path was changed "
        "on both sides, or a focused test fails, the Worker sends exactly that repair for one "
        "look. When a freeze arrives, check every finding against its entry in the Worker's "
        "freeze report and return a freeze that lacks the entries, with no round counted.",
    ),
    (
        "review",
        "reviewer",
        "Where every finding on the candidate is closed and what remains is a finding on the "
        "Worker's record alone (its report, its evidence table, a hash or an anchor in it), the "
        "same Reviewer checks the repaired record on the round it last recorded: `begin_review` "
        "is not called and the count stays, and only while the candidate is byte for byte the "
        "candidate that round was recorded on. With a changed candidate the tool refuses and "
        "names `begin_review` as the next step.",
    ),
    (
        "review",
        "reviewer",
        "Answer Curator code findings directly to Curator and Worker, even after code PASS: "
        "take a valid finding into your findings and reopen the verdict for that point, or state "
        "why it does not hold. Preserve the sealed fixed-list baseline; an unresolved "
        "disagreement or authority issue goes to the Manager.",
    ),
)

# Six operation fingerprints retain their complete base bytes. Bootstrap alone
# pins the D301 candidate wording and named-step move, not unchanged-base prose;
# its sentence ledger still needs independent meaning review. These pins bind
# the expected delivery corpus without reading markers as an ownership oracle.
R125_OPERATION_SHA256 = {
    "orientation": "e532ec7587048c7e6b53cf2fe0fa3b78212f5c6fc2431d57e807c1d2296587da",
    "planning": "d6a1c6458d3a67ed54c0bbf0b399066022d8ac094110e5fae4673c5d0bd36f63",
    "review": "c7ddda94f04ea23c555296fbf8108037598098a17d22cad26613be65de64c317",
    "coordination": "4d0e8fe886c1806091a9355cad05d3d28199bf7b5bd9d1f5d4283c0ee015b2be",
    "recovery": "ae7583596bc9f30d49b5d675e48b5076da2aabca5cf3d992ab97ce438e7607b1",
    "implementation": "c3a028dec741ee1edc277bc6ef2841d7e04fe1af529e491ec52bb486abe10b02",
    "bootstrap": "e1235cd05d77c8684df3f9028ade5ac361d725097082a3604ae658b7ae1589bf",
}
R125_CLOSEOUT_SHA256 = "baea8d9c29a19b55b3bd58cb8c81382965f4ad5f2d665e42839f2843ef3411f0"
R125_OPERATION_ROLES: dict[str, tuple[str, ...]] = {
    "orientation": (
        "architect",
        "orchestrator",
        "designer",
        "strategist",
        "manager",
        "worker",
        "curator",
        "reviewer",
        "investigator",
        "bootstrap",
    ),
    "planning": ("architect", "designer", "strategist", "orchestrator"),
    "implementation": ("worker",),
    "review": ("reviewer", "manager", "orchestrator", "architect"),
    "curation": ("curator", "manager"),
    "coordination": ("architect", "orchestrator", "manager"),
    "authorized-closeout": ("manager", "orchestrator", "architect"),
    "recovery": (
        "architect",
        "orchestrator",
        "manager",
        "worker",
        "curator",
        "investigator",
        "bootstrap",
        "reviewer",
    ),
    "bootstrap": ("bootstrap",),
}


def test_memory_review_instructions_select_the_independent_recorded_lane() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    manifest = (root / "composition-manifest.json").read_bytes()
    for role in ("curator", "reviewer"):
        result = _compile_corpus_pair(root, manifest, CapsuleRoleSeat(role, "leaf"), "orientation")
        text = result.render_instructions()
        for required in ("`begin_review`/`record_review`", "`reviewKind=memory`", "code state"):
            assert required in text, (role, required)
    review = (root / "operations/review.md").read_text()
    assert "`reviewKind=memory`" in review
    assert "open no code review-state round" in review
    assert "open no review-state round" not in review
    curation = (root / "operations/curation.md").read_text()
    assert "`reviewKind=memory`" in curation
    assert "Under-way reviews finish as begun; new lanes apply after landing." in curation


MEMORY_REVIEW_PREDECESSOR = (
    "Curator memory checks keep separately sealed reports, IDs and a count with the same settings "
    "limit as code; they open no review-state round, reset no passed code review and add no IDs "
    "to its baseline."
)
MEMORY_REVIEW_RECORDED_LANE = (
    "Curator memory checks use their own sealed reports, IDs and recorded lane: "
    "`begin_review`/`record_review` with `reviewKind=memory`, same settings limit and extra-round "
    "approval as code. They open no code review-state round, reset no passed code review and "
    "add no IDs to its baseline. Under-way memory reviews finish as begun; recorded lanes apply "
    "after landing."
)


CURATOR_SERVING_STAGE_BOUNDARY = (
    "On development installs, only the serving build produces: the starting tool server or "
    "that same build's CLI. Use the clean main checkout; its serving commit is the ruler. "
    "Every run states its ruler. Leaf source never produces leaf memory, admitted or not. "
    "Prove leaf writer/check changes on fixtures and scratch copies; that evidences the build, "
    "not an ordered fixture procedure. Install nothing for a run, disable no guard, "
    "substitute no producer after refusal."
)


def _unmarked_pinned_operation(root: Path, operation: str) -> str:
    content = (root / f"operations/{operation}.md").read_bytes()
    unmarked = b"".join(
        line
        for line in content.splitlines(keepends=True)
        if not re.fullmatch(rb"<!-- ar:roles [a-z,\-]+ -->\n", line)
    )
    pinned = unmarked
    if operation in {"orientation", "recovery"}:
        addition = ("\n" + CURATOR_SERVING_STAGE_BOUNDARY + "\n").encode()
        assert unmarked.endswith(addition), operation
        pinned = unmarked[: -len(addition)]
    elif operation == "review":
        assert unmarked.count(MEMORY_REVIEW_RECORDED_LANE.encode()) == 1
        pinned = unmarked.replace(
            MEMORY_REVIEW_RECORDED_LANE.encode(), MEMORY_REVIEW_PREDECESSOR.encode()
        )
    assert sha256(pinned).hexdigest() == R125_OPERATION_SHA256[operation], operation
    return unmarked.decode()


def _expected_operation_stage(operation: str, role: str, base: str) -> str:
    for addressed_operation, owner, paragraph in R125_ADDRESSED_PARAGRAPHS:
        if operation != addressed_operation:
            continue
        assert base.count(paragraph + "\n") == 1, (operation, paragraph)
        if role != owner:
            base = base.replace(paragraph + "\n", "")
    if operation in {"orientation", "recovery"}:
        assert base.count(CURATOR_SERVING_STAGE_BOUNDARY + "\n") == 1
        if role != "curator":
            base = base.replace(CURATOR_SERVING_STAGE_BOUNDARY + "\n", "")
    return base


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
        base = (
            _unmarked_pinned_operation(root, operation)
            if operation in R125_OPERATION_SHA256
            else None
        )
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
            if base is not None:
                assert stage.content == _expected_operation_stage(operation, role, base), (
                    role,
                    operation,
                )
            elif operation == "authorized-closeout":
                assert sha256(stage.content.encode()).hexdigest() == R125_CLOSEOUT_SHA256, (
                    role,
                    operation,
                )
            checked.add((role, operation))
    assert checked == {
        (role.role, operation) for role in document.roles.values() for operation in role.operations
    }


def test_operation_audience_markings_preserve_pinned_bytes_and_immutable_closeout() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    document = parse_composition_manifest((root / "composition-manifest.json").read_bytes())
    for operation in R125_OPERATION_SHA256:
        assert document.operations[operation].audience == "sections", operation
        assert document.operations[operation].source == f"operations/{operation}.md"
        content = (root / f"operations/{operation}.md").read_bytes()
        assert content.startswith(b"<!-- ar:roles all -->\n")
        _unmarked_pinned_operation(root, operation)
    closeout = document.operations["authorized-closeout"]
    assert closeout.audience == "all"
    assert closeout.source == "operations/closeout.md"
    assert sha256((root / closeout.source).read_bytes()).hexdigest() == R125_CLOSEOUT_SHA256, (
        "authorized-closeout"
    )


def test_repairing_roles_stop_after_three_repairs_of_the_same_check_or_finding() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles/roles"
    sentence = (
        "When the same check or finding still fails after three repairs of your own, stop repairing, "
        "write what you tried and what still fails, and tell the Manager; the Manager steps the seat "
        "up or answers otherwise."
    )
    for role in ("worker", "curator"):
        assert sentence in (root / f"{role}.md").read_text()
