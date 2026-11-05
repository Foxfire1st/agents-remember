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
    """Command/report details stay separate; serving authority remains inline."""
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
        (intake, "`knowledge_read` takes `workspace.memoryRoot`"),
        (messages, "The full text goes in `body` and its files in `attach`"),
    ):
        assert syntax in reference.content.decode()
        assert syntax not in text
    assert "replace_evidence: true" in worklist.content.decode()
    assert "replace_evidence: true" not in text
    assert (
        "--authorization-ref '<the admitted authorization>' --commit --json"
        in writer.content.decode()
    )
    assert "--authorization-ref '<the admitted authorization>' --commit --json" not in text
    for semantics in (
        "exit zero covers both a plan and a write",
        "`written` report with no written paths establishes no publication.",
        "written paths, record/entry IDs and refusals",
    ):
        assert semantics in writer.content.decode(), semantics
        assert semantics not in text, semantics
    for boundary in (
        "Require a readable persisted leaf worklist before ingest.",
        "There is no dataset to\n  publish.",
        CURATOR_SERVING_STAGE_BOUNDARY,
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

    def check(text: str, records: list[dict], role: str) -> None:
        for entry in records:
            sentences = re.split(r"(?<=\.)\s+|\n", text)
            naming = [sentence for sentence in sentences if entry["path"] in sentence]
            assert naming, entry["sourcePath"]
            if entry["sourcePath"] == "reference/knowledge-use.md":
                assert entry["step"] == (
                    "when you reach Architect diagnosis, Investigator repository-code investigation, "
                    "Worker editing or a failing test, or Reviewer inspection of changes, not before"
                )
                trigger = {
                    "architect": "when diagnosing, not before",
                    "investigator": "when investigating repository code, not before",
                    "worker": "when you reach editing or a failing test, not before",
                    "reviewer": "when inspecting changes, not before",
                }[role]
                assert all(trigger in sentence for sentence in naming)
                continue
            conditional = CURATOR_CONDITIONAL_DETAIL_TRIGGERS.get(entry["sourcePath"])
            if conditional:
                step, trigger = conditional
                assert entry["step"] == step
                assert all(trigger in sentence for sentence in naming)
                continue
            assert "when you reach" in entry["step"] and "not before" in entry["step"]
            for sentence in naming:
                assert "when you reach" in sentence and "not before" in sentence, sentence
                assert "read during intake" not in sentence.lower(), sentence

    # Every declared role is relevant now: standing-rule details include scopes
    # absent from the three original D301 roles (D656).
    parsed = parse_composition_manifest(manifest)
    for role, operation in (
        (name, step) for name in parsed.role_order for step in parsed.roles[name].operations
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
        check(text, entries, role)
        seen.update(entry["sourcePath"] for entry in entries)
        mutant = text.replace("not before", "read during intake", 1)
        with pytest.raises(AssertionError):
            check(mutant, entries, role)
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
        "curator-card-details.md": (2, "Before ingest"),
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
    standing_names = {
        "message-handover.md",
        "shared-working-rules.md",
        "knowledge-commands.md",
        "curator-repository-foundation.md",
    }
    conditional_names = {Path(path).name for path in CURATOR_CONDITIONAL_DETAIL_TRIGGERS}
    assert {Path(entry["sourcePath"]).name for entry in entries} == (
        set(expected) | conditional_names | standing_names
    )
    for entry in entries:
        if Path(entry["sourcePath"]).name in standing_names:
            naming = [line for line in text.splitlines() if entry["path"] in line]
            assert naming and all(
                "when you reach" in line and "not before" in line for line in naming
            )
            continue
        conditional = CURATOR_CONDITIONAL_DETAIL_TRIGGERS.get(entry["sourcePath"])
        if conditional:
            step, trigger = conditional
            assert entry["step"] == step
            naming = [line for line in text.splitlines() if entry["path"] in line]
            assert naming and all(trigger in line for line in naming)
            continue
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
            assert "when you reach **Curation workflow 2: Before ingest**, not before" in line
        if "reference/curator-writer-details.md" in line:
            assert "when you reach **Curation workflow 2: Before ingest**, not before" in line
    writer_entry = next(
        item
        for item in record["detailReferences"]
        if item["sourcePath"] == "reference/curator-writer-details.md"
    )
    writer = Path(writer_entry["path"]).read_text()
    assert "Read the serving-build command section before a CLI deciding run or write." in writer
    assert (
        "authoring detail when you reach **Curation workflow 2: Before ingest**, not before."
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
            CURATOR_SERVING_STAGE_BOUNDARY,
            "Use that build for deciding and writing.",
            "A PATH `agents-remember` may be stale; report its use and observed identity honestly.",
        ):
            assert boundary in text, (operation, boundary)


# D656 renews the complete operation and per-recipient byte pins after its approved
# stage-start, handover, permission and continuation wording changes. The expected
# projections were derived independently from the reviewed source audiences once;
# tests never read current audience markers to decide their expected recipients.
D656_OPERATION_SHA256 = {
    "orientation": "127ead9498fbd9811abe4acfab1edbb53a446122a58924e1d1f045443759a651",
    "planning": "ec4e036bb3190f1e55609b940535c59cceaf5563d3116f54041cb7cbdc28de5c",
    "implementation": "f79d875a5459baea525fba1d9b6e0b924517fc636c5b0041b5a428c810945a38",
    "review": "9ce18b86a587453034f52c538aab64b29200a8c53586813a078d41352ee64539",
    "curation": "d4419241535f8cc0f0b8e8685fc6b740dea5a79428fec70d372001fd95d6a711",
    "coordination": "e3cb95ab87a89b81e4c2015f8ca9d616d5819187405fb9964e165710434e9afb",
    "authorized-closeout": "2861b5d5b09c9e4f84c6c217cfe418849592e197f0d5e9c5cc9a5c42110fc0cc",
    "recovery": "77bf9d941b300f292480f66fd7528066ce9d744c69da740a16648849b559f8f8",
    "bootstrap": "8ee46540770891de1b0c855b3e246282462b4f07da29cfbc78f7c3a00cf5ccf1",
}
D656_STAGE_SHA256 = {
    "orientation/architect": "b68c955e51b41ab7a646a84f2bf84517583a04d77d44f79635ec4b64827bb95c",
    "orientation/orchestrator": "0683b3f0becff41384f6a2c79a8870c5edc0e47b9bb821a825c917baf9be3281",
    "orientation/designer": "0683b3f0becff41384f6a2c79a8870c5edc0e47b9bb821a825c917baf9be3281",
    "orientation/strategist": "0683b3f0becff41384f6a2c79a8870c5edc0e47b9bb821a825c917baf9be3281",
    "orientation/manager": "0683b3f0becff41384f6a2c79a8870c5edc0e47b9bb821a825c917baf9be3281",
    "orientation/worker": "0683b3f0becff41384f6a2c79a8870c5edc0e47b9bb821a825c917baf9be3281",
    "orientation/curator": "0a49eb8ea424315579e377419d9343313bf161a466306f1fe1eb22739ca105ed",
    "orientation/reviewer": "0683b3f0becff41384f6a2c79a8870c5edc0e47b9bb821a825c917baf9be3281",
    "orientation/investigator": "89a2a41b7621a9c7a03dd149f40793bcaaf126c53533f262435e02530bc2f907",
    "orientation/bootstrap": "fb292901ed2d3a528cbd80e7d7030ed884973c9976407a6f40c864eccded8162",
    "planning/architect": "d39b63bb7b6eaec8e8ffef109e001b73e47430a711ec050758abff604558775c",
    "planning/designer": "c7e44a4163f1a49d45aea0159d9b0dd76f96f727a59b8564887a129c856be68a",
    "planning/strategist": "c7e44a4163f1a49d45aea0159d9b0dd76f96f727a59b8564887a129c856be68a",
    "planning/orchestrator": "c7e44a4163f1a49d45aea0159d9b0dd76f96f727a59b8564887a129c856be68a",
    "implementation/worker": "2620a4f02c35dc90edcc1a5b828ad95ca952a2b53e19f5542044c028c86fa269",
    "review/reviewer": "71ebbc4a38f7572a0a2705dae1fb722c13d63b21ce3309c782292a0101b99a19",
    "review/manager": "f9313b61362f48b604a45006900706dfda248c81c32f40fbf1a267ec46745107",
    "review/orchestrator": "f9313b61362f48b604a45006900706dfda248c81c32f40fbf1a267ec46745107",
    "review/architect": "6f8653dc517266a90a65f306d30bc66ef302cf0872ff655787db7ea7e959fd1d",
    "curation/curator": "87d423772acfaa4e3da98b1c7086de8822cda50558dafc18391ddd9cf5e99d80",
    "curation/manager": "252e3db2c7f4ed0deb8a4c37d39d965919935d4ca6e29d652513c1d0cd8d24ac",
    "coordination/architect": "f117578686e46db35a47a7fcb63d5a4b38c9575e3c7122c3441e8562c0eb9171",
    "coordination/orchestrator": "f117578686e46db35a47a7fcb63d5a4b38c9575e3c7122c3441e8562c0eb9171",
    "coordination/manager": "2c5241ab758ccfc7fde7315240b13457663cb23a95233820e27133de12378cff",
    "authorized-closeout/manager": "2861b5d5b09c9e4f84c6c217cfe418849592e197f0d5e9c5cc9a5c42110fc0cc",
    "authorized-closeout/orchestrator": "2861b5d5b09c9e4f84c6c217cfe418849592e197f0d5e9c5cc9a5c42110fc0cc",
    "authorized-closeout/architect": "2861b5d5b09c9e4f84c6c217cfe418849592e197f0d5e9c5cc9a5c42110fc0cc",
    "recovery/architect": "5482387b04386d2d73c1662c9572550c0ce8ffa505964f54f659fac10a1a131d",
    "recovery/orchestrator": "5482387b04386d2d73c1662c9572550c0ce8ffa505964f54f659fac10a1a131d",
    "recovery/manager": "5482387b04386d2d73c1662c9572550c0ce8ffa505964f54f659fac10a1a131d",
    "recovery/worker": "5482387b04386d2d73c1662c9572550c0ce8ffa505964f54f659fac10a1a131d",
    "recovery/curator": "bb6cb529c621d9e637dd2eae5cf4b91fd514897891088aceb7b814608d2177d8",
    "recovery/investigator": "5482387b04386d2d73c1662c9572550c0ce8ffa505964f54f659fac10a1a131d",
    "recovery/bootstrap": "41558f9722778d9cada28a4a011d773e5c893352fb88a2c8254adab6de00ab94",
    "recovery/reviewer": "5482387b04386d2d73c1662c9572550c0ce8ffa505964f54f659fac10a1a131d",
    "bootstrap/bootstrap": "1bfd3c57a6bb7d6f4759cc41a9076debfaf25429e8a9525fb78ab3a3de391c4e",
}
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


CURATOR_SERVING_STAGE_BOUNDARY = "Decide and write through the starting server or its serving-build CLI. Development uses clean main and its stated serving commit as ruler; name it. Leaf source never produces leaf memory: test leaf writer/check changes on fixtures or scratch. Install nothing; disable no guard."

CURATOR_CONDITIONAL_DETAIL_TRIGGERS = {
    "reference/curator-writer-details.md": (
        "before a CLI deciding run or write; use record formats at Curation workflow 2: Before ingest",
        "before a CLI deciding run or write; use its record formats at **Curation workflow 2: Before ingest**",
    ),
    "reference/refused-write.md": (
        "when a covered writer or record tool refuses a correct write, not before",
        "when a covered writer or record tool refuses a correct write, not before",
    ),
}


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
                == D656_STAGE_SHA256[f"{operation}/{role}"]
            ), (role, operation)
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
        assert sha256(content).hexdigest() == expected, operation
        if operation == "authorized-closeout":
            assert metadata.audience == "all"
            assert metadata.source == "operations/closeout.md"
        else:
            assert metadata.audience == "sections", operation
            assert content.startswith(b"<!-- ar:roles all -->\n"), operation


def test_repairing_roles_stop_after_three_repairs_of_the_same_check_or_finding() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles/roles"
    sentence = (
        "When the same check or finding still fails after three repairs of your own, stop repairing, "
        "write what you tried and what still fails, and tell the Manager; the Manager steps the seat "
        "up or answers otherwise."
    )
    for role in ("worker", "curator"):
        assert sentence in (root / f"{role}.md").read_text()
