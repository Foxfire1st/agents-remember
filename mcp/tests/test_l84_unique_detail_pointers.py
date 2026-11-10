"""All admitted capsules deliver each required detail once, through its existing role/step."""

from __future__ import annotations

import json
import re
import shutil
from hashlib import sha256
from pathlib import Path

import pytest
from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember.cli.role_brief_message import shared_block
from agents_remember.cli.role_instruction_details import compose_launch_instructions
from agents_remember.models.role_capsules.types import CapsuleCompilationResult
from agents_remember.serving.capsule_delivery import capsule_delivery_from
from agents_remember_test_support.testing.capsule_audience_fixtures import (
    GROUPED_DETAIL_CLAUSES,
    INLINE_RULE_CLAUSES,
    REMOVED_SHORT_RULE_ACTIONS,
    ROLE_POINTER_QUALIFICATIONS,
    SHARED_SHORT_RULE_CLAUSES,
)
from agents_remember_test_support.testing.leaf_instruction_wording import assert_action_pointer
from agents_remember_test_support.testing.manifest_step_derivation import (
    manifest_mismatches,
    sentence_start,
)
from agents_remember_test_support.testing.pointer_uniqueness import duplicate_pointers
from test_l84_capsule_delivery_proofs import (
    _compiled,
    _compiled_outcome,
    _emitted_instruction_detail,
    _linked_instruction_source,
    _shipped_parsed,
)
from test_role_capsule_audiences import (
    OPERATION_POINTER_ACTIONS,
    ROLE_POINTER_ACTIONS,
    _assert_curator_message_detail,
)

# Complete current reference population, including methods performed by the Architect.
ROLE_DETAILS = {
    "architect": [
        "reference/coordination/shared/child-questions.md",
        "reference/coordination/shared/requirement-design.md",
        "reference/coordination/architect/curator-measurement.md",
        "reference/coordination/architect/developer-decisions.md",
        "reference/coordination/architect/models-and-settings.md",
        "reference/coordination/architect/process-decisions.md",
        "reference/coordination/architect/claim-verification.md",
        "reference/coordination/shared/master-state.md",
        "reference/coordination/shared/third-rejected-round.md",
        "reference/coordination/shared/seat-recovery.md",
        "reference/sandbox-actors.md",
        "reference/knowledge/commands.md",
        "reference/knowledge/use.md",
        "reference/message-handover.md",
        "reference/tasks/document-authoring.md",
    ],
    "orchestrator": [
        "reference/coordination/shared/child-questions.md",
        "reference/coordination/shared/master-state.md",
        "reference/coordination/shared/seat-recovery.md",
        "reference/sandbox-actors.md",
        "reference/knowledge/commands.md",
        "reference/message-handover.md",
        "reference/tasks/document-authoring.md",
    ],
    "manager": [
        "reference/coordination/shared/child-questions.md",
        "reference/coordination/shared/master-state.md",
        "reference/refused-write.md",
        "reference/coordination/shared/third-rejected-round.md",
        "reference/coordination/shared/seat-recovery.md",
        "reference/sandbox-actors.md",
        "reference/knowledge/commands.md",
        "reference/message-handover.md",
        "reference/tasks/document-authoring.md",
    ],
    "worker": [
        "reference/sandbox-actors.md",
        "reference/knowledge/serving-build.md",
        "reference/knowledge/commands.md",
        "reference/knowledge/use.md",
        "reference/message-handover.md",
        "reference/tasks/document-authoring.md",
        "reference/writing/visual-explainers.md",
        "templates/turn-report.md",
    ],
    "curator": [
        "reference/sandbox-actors.md",
        "reference/curator/card-formats-and-citations.md",
        "reference/curator/card-sentence-decisions.md",
        "reference/curator/change-routing.md",
        "reference/curator/coherence-judgments.md",
        "reference/curator/current-prose-and-history.md",
        "reference/curator/encountered-records.md",
        "reference/curator/family-obligations.md",
        "reference/curator/governing-sources.md",
        "reference/curator/memory-handover.md",
        "reference/curator/knowledge-ingest.md",
        "reference/curator/publication-report.md",
        "reference/curator/intent-reconciliation.md",
        "reference/curator/repository-foundation.md",
        "reference/knowledge/serving-build.md",
        "reference/curator/worklist.md",
        "reference/knowledge/commands.md",
        "reference/knowledge/use.md",
        "reference/message-handover.md",
        "reference/writing/technical-english-profile.md",
    ],
    "reviewer": [
        "reference/sandbox-actors.md",
        "reference/knowledge/commands.md",
        "reference/knowledge/use.md",
        "reference/message-handover.md",
        "reference/review-baseline.md",
    ],
    "investigator": [
        "reference/sandbox-actors.md",
        "reference/instruction-shortfall-review.md",
        "reference/knowledge/commands.md",
        "reference/knowledge/use.md",
        "reference/message-handover.md",
    ],
    "bootstrap": [
        "reference/bootstrap/foundation-state.md",
        "reference/sandbox-actors.md",
        "reference/knowledge/commands.md",
    ],
}
OPERATION_DETAILS = {
    "bootstrap/bootstrap": [
        "reference/bootstrap/failure-states.md",
    ],
}


def _assert_coordination_start_owner(
    result: CapsuleCompilationResult, role: str, report_path: Path
) -> None:
    """Direct start pointers retain the complete stage and first-intake owner."""
    # The text that leads into each role's one first-intake link (round 6: the Architect's and
    # the Orchestrator's start pointers each name it inside their single start sentence).
    action, label = {
        "manager": ("Before starting and coordinating the leaf, read", "leaf-stage coordination"),
        "architect": (
            "for the permitted starters and",
            "Start leaf stages and deliver the first intake",
        ),
        "orchestrator": ("for selection and start rules,", "the first intake"),
    }[role]
    pointer = action + (
        f" [{label}](../operations/coordination.md#start-leaf-stages-and-deliver-the-first-intake)"
    )
    rendered = result.render_instructions()
    _assert_moved_action_pointer(rendered, pointer)
    for mutant in (rendered.replace(pointer, "", 1), rendered + "\n" + pointer):
        with pytest.raises(AssertionError):
            _assert_moved_action_pointer(mutant, pointer)
    instructions, record = compose_launch_instructions(
        capsule_delivery_from(result), str(report_path), {}
    )
    assert record["baselineDigest"] == result.capsule.semantic_digest
    owner = _linked_instruction_source(
        instructions,
        action,
        "operations/coordination.md",
        "start-leaf-stages-and-deliver-the-first-intake",
        "Start leaf stages and deliver the first intake",
    )
    for clause in (
        "The Manager calls `role_start` on `agents-remember-task` once per stage: Worker first, "
        "Reviewer at the freeze, and Curator when curation begins. Start only the Worker at build. "
        "The Worker's freeze notice and the Reviewer's code-`PASS` notice go to the Manager first.",
        "Complete this start and first-intake procedure before the seat's first inspection.",
        "After the individual `role_start` on `agents-remember-task`, send the leaf Reviewer's "
        "complete first intake as your first "
        "`role_message` to its returned agent identifier. Use the assigned review mode and review "
        "purpose, bound catalogs and resolved `system/tools.md`. The requirement set includes "
        "identifiers, versions, packets and the durable [approval record]"
        "(../core/every-role.md#requirements-and-limits). The Reviewer inspects nothing before "
        "its intake.",
    ):
        assert clause in owner
        with pytest.raises(AssertionError):
            assert clause in owner.replace(clause, "", 1)
    for mutant in (
        instructions.replace(action, "", 1),
        instructions + "\n" + next(line for line in instructions.splitlines() if action in line),
    ):
        with pytest.raises(AssertionError):
            _linked_instruction_source(
                mutant,
                action,
                "operations/coordination.md",
                "start-leaf-stages-and-deliver-the-first-intake",
                "Start leaf stages and deliver the first intake",
            )


def _assert_named_detail_action(text: str, role: str, source: str, action: str) -> None:
    assert_action_pointer(text, source, action)
    fragment, _suffix = ROLE_POINTER_QUALIFICATIONS.get((role, source, action), ("", ""))
    if fragment:
        marker = "{{detail:" + source + "}}" + fragment + ")"
        paragraphs = [part for part in text.split("\n\n") if action in part and marker in part]
        assert len(paragraphs) == 1, (role, source, action, fragment)
        assert paragraphs[0].count(marker) == 1


def _detail_pointer_sentences(rendered: str, source: str) -> int:
    """Sentences that point to one detail; two of its sections in one sentence are one pointer."""
    marker = re.compile(re.escape("{{detail:" + source + "}}"))
    return sum(
        len({sentence_start(line, match.start()) for match in marker.finditer(line)})
        for line in rendered.splitlines()
    )


def _assert_lists_disjoint(
    role_list: list[tuple[str, str]], operation_list: list[tuple[str, str]]
) -> None:
    """P5-18: a detail pointed to from the role file is not pointed to again by the operation."""
    shared = {source for source, _ in role_list} & {source for source, _ in operation_list}
    assert not shared, sorted(shared)


def _first_message_units(role: str, operation: str) -> list[tuple[str, str]]:
    outcome = _compiled_outcome(role, operation)
    assert outcome.result is not None and outcome.shared_block is not None
    units = [
        (unit.block.source_path, unit.block.content)
        for unit in outcome.result.capsule.instruction_units
    ]
    return [*units, ("core/every-role.md", outcome.shared_block)]


MH_SECTION = "../reference/message-handover.md#send-a-message-that-names-the-files"
#: (role, operation, source, sentence): duplicates removed in rounds 4 to 6, re-added. They
#: cover a whole-file link plus a section link (C3-C5), sentences without a Before/When/For
#: opener (C7-C9), two sections reached for one action (R2) and the pairs named in round 5.
RESTORED_DUPLICATES = (
    (
        "architect",
        "planning",
        "operations/planning.md",
        "Before releasing a new or revised packet, follow the [Cross-packet alignment sweep]"
        "(#cross-packet-alignment-sweep), including its amendment-approval rule.",
    ),
    (
        "curator",
        "curation",
        "operations/curation.md",
        "Before beginning a repository's first or resumed knowledge foundation, read [Enter "
        "through the real foundation task](../reference/curator/repository-foundation.md"
        "#enter-through-the-real-foundation-task) for its task and scope.",
    ),
    (
        "curator",
        "curation",
        "operations/curation.md",
        "Before updating current overview prose, read [the meaning-owning body rules](../"
        "reference/curator/current-prose-and-history.md#update-the-meaning-owning-body) for "
        "its home.",
    ),
    (
        "curator",
        "curation",
        "operations/curation.md",
        "Before sending a memory candidate or repair, read [Send the whole requested review or "
        "repair](../reference/curator/memory-handover.md#send-the-whole-requested-review-or-"
        "repair) for the exact identities.",
    ),
    (
        "curator",
        "curation",
        "roles/curator.md",
        "Reject structurally blocked intake under [required curation inputs]"
        "(../operations/curation.md#required-inputs).",
    ),
    (
        "curator",
        "curation",
        "operations/curation.md",
        "Repair stale assigned or encountered memory under [Repair and finish the stage]"
        "(../roles/curator.md#repair-and-finish-the-stage), including pre-existing staleness.",
    ),
    (
        "curator",
        "curation",
        "operations/curation.md",
        "Apply the bounded encountered-memory repair in [Repair and finish the stage]"
        "(../roles/curator.md#repair-and-finish-the-stage).",
    ),
    (
        "curator",
        "curation",
        "operations/curation.md",
        "Before routing a notes item, read [change routing](../reference/curator/"
        "change-routing.md) for its home.",
    ),
    (
        "reviewer",
        "review",
        "operations/review.md",
        "Before acting on a relayed answer, read [Recognize a relayed developer answer]"
        "(orientation.md#recognize-a-relayed-developer-answer) for its source.",
    ),
    (
        "bootstrap",
        "bootstrap",
        "operations/bootstrap.md",
        "Before reading and reporting the foundation's state, read [knowledge-foundation "
        "states]({{detail:reference/bootstrap/foundation-state.md}}) for the state fields.",
    ),
    (
        "curator",
        "curation",
        "operations/curation.md",
        f"Before sending the handover, read [Send a message that names the files]({MH_SECTION}) "
        "for its payload and file rules.",
    ),
    (
        "architect",
        "planning",
        "operations/planning.md",
        f"Before sending the handover, read [Send a message that names the files]({MH_SECTION}) "
        "for its payload and file rules.",
    ),
    (
        "manager",
        "coordination",
        "operations/coordination.md",
        f"Before composing a message, read [Send a message that names the files]({MH_SECTION}) "
        "for the sender and payload rules.",
    ),
    (
        "orchestrator",
        "authorized-closeout",
        "operations/closeout.md",
        "Before handing over the verified closeout result, read [Send a message that names the "
        f"files]({MH_SECTION}) for its payload and file rules.",
    ),
    (
        "reviewer",
        "review",
        "roles/reviewer.md",
        "The two sanctioned routes for adding findings after sealing are defined in [review "
        "boundaries](../operations/review.md#boundaries).",
    ),
    (
        "reviewer",
        "review",
        "operations/review.md",
        "Beyond the limit, the Manager follows [the extra-round approval procedure]"
        "(../roles/manager.md#make-decisions-within-your-authority). The Manager retains gate "
        "acceptance; extra-round approval follows [the Manager's procedure]"
        "(../roles/manager.md#make-decisions-within-your-authority).",
    ),
    (
        "manager",
        "orientation",
        "operations/orientation.md",
        "Before resuming after compaction or reconnect, read [Restore the saved assignment]"
        "(recovery.md#restore-the-saved-assignment) for exact references.",
    ),
    (
        "worker",
        "implementation",
        "operations/implementation.md",
        "The freeze report carries one entry per repaired finding, as [Write the report]"
        "(#write-the-report) requires.",
    ),
    (
        "architect",
        "planning",
        "operations/planning.md",
        "Before starting a role, read [Start one canonical role assignment]"
        "(coordination.md#start-one-canonical-role-assignment) for the permitted starters.",
    ),
    (
        "curator",
        "curation",
        "operations/curation.md",
        "Before naming the trees in a memory handover, read [Identify the reviewed trees]"
        "(../reference/curator/memory-handover.md#identify-the-reviewed-trees) for the "
        "Reviewer's comparison.",
    ),
    # Another role's listed wording re-added in this role (round-6 orchestrator.md:24).
    (
        "orchestrator",
        "coordination",
        "roles/orchestrator.md",
        "Before starting a role and delivering its first intake, read [Start leaf stages and "
        "deliver the first intake](../operations/coordination.md#start-leaf-stages-and-deliver-"
        "the-first-intake) for stage timing and intake fields.",
    ),
    # A second action inside another action's sentence (round-6 reviewer.md:75).
    (
        "reviewer",
        "review",
        "roles/reviewer.md",
        "Write and record the verdict, then follow [the leaf verdict handover]"
        "(../operations/review.md#receive-the-intake-and-hand-over-the-leaf-verdict) for the "
        "code-PASS notice, and [review boundaries](../operations/review.md#boundaries) for the "
        "separate memory lane.",
    ),
)


def test_every_native_first_message_has_one_pointer_per_action() -> None:
    """P5-18: one pointer per action per first message, keyed by target file, every link."""
    manifest = _shipped_parsed()
    checked = set()
    for role, entry in manifest.roles.items():
        for operation in entry.operations:
            units = _first_message_units(role, operation)
            assert duplicate_pointers(units) == [], (role, operation)
            checked.add((role, operation))
    assert len(checked) == 32
    # A same-action pair declared on both the role and the operation list fails.
    with pytest.raises(AssertionError):
        _assert_lists_disjoint(
            [
                (
                    "reference/curator/change-routing.md",
                    "Before routing a changed file or notes item, read",
                )
            ],
            [("reference/curator/change-routing.md", "Before routing a notes item, read")],
        )
    # Each removed duplicate, re-added to its first message, fails.
    for role, operation, source, added in RESTORED_DUPLICATES:
        units = _first_message_units(role, operation)
        assert any(unit_source == source for unit_source, _ in units), (role, operation, source)
        mutant = [(s, t + "\n\n" + added if s == source else t) for s, t in units]
        assert duplicate_pointers(mutant), (role, operation, added)
    # A second link into one file inside another action's sentence, in place, fails.
    units = _first_message_units("reviewer", "review")
    kept = "Curator start; the memory lane stays separate."
    restored = (
        "Curator start, and [review boundaries](../operations/review.md#boundaries) for the "
        "separate memory lane."
    )
    assert sum(text.count(kept) for _, text in units) == 1
    mutant = [(s, t.replace(kept, restored)) for s, t in units]
    assert any("one sentence links it twice" in p for p in duplicate_pointers(mutant))
    # Two wordings of one role-level action in one message fail even when both are listed.
    units = _first_message_units("worker", "implementation")
    added = (
        "Before sending a message, read [message handovers and receipts]"
        "({{detail:reference/message-handover.md}}) for the payload fields."
    )
    mutant = [(s, t + "\n\n" + added if s == "roles/worker.md" else t) for s, t in units]
    assert any("two wordings" in problem for problem in duplicate_pointers(mutant))


FULL_STORE_POINTER = (
    "Before responding to a full-store refusal, read [Handle a full-store refusal]("
)


def test_every_required_detail_has_one_pointer_in_all_36_real_compiles(tmp_path: Path):
    manifest = _shipped_parsed()
    checked = set()
    for role, entry in manifest.roles.items():
        for operation in entry.operations:
            result = _compiled(role, operation)
            rendered = result.render_instructions()
            details = result.capsule.detail_references
            expected = set(ROLE_DETAILS[role]) | set(
                OPERATION_DETAILS.get(role + "/" + operation, [])
            )
            assert {item.source_path for item in details} == expected, (role, operation)
            assert set(re.findall(r"\{\{detail:([^}]+)\}\}", rendered)) == expected
            _assert_lists_disjoint(
                ROLE_POINTER_ACTIONS[role], OPERATION_POINTER_ACTIONS[operation][role]
            )
            actions = [*ROLE_POINTER_ACTIONS[role], *OPERATION_POINTER_ACTIONS[operation][role]]
            for detail in details:
                assert detail.content and detail.step.strip()
                required = [action for source, action in actions if source == detail.source_path]
                assert required
                assert _detail_pointer_sentences(rendered, detail.source_path) == len(required)
                for action in required:
                    _assert_named_detail_action(rendered, role, detail.source_path, action)
                    fragment, _suffix = ROLE_POINTER_QUALIFICATIONS.get(
                        (role, detail.source_path, action), ("", "")
                    )
                    if fragment:
                        marker = "{{detail:" + detail.source_path + "}}" + fragment + ")"
                        mutant = rendered.replace(
                            marker, marker.replace(fragment, "#wrong-section"), 1
                        )
                        assert mutant != rendered
                        with pytest.raises(AssertionError):
                            _assert_named_detail_action(mutant, role, detail.source_path, action)
            role_block = next(
                unit.block.content
                for unit in result.capsule.instruction_units
                if unit.block.source_path == f"roles/{role}.md"
            )
            assert ("{{detail:reference/message-handover.md}}" in role_block) == (
                role != "bootstrap"
            )
            # P5-18 (Architect look 2): one full-store pointer per first message, from the
            # operation; role files carry none. Bootstrap has no message handover.
            assert rendered.count(FULL_STORE_POINTER) == (role != "bootstrap"), (role, operation)
            assert "full-store refusal, read" not in role_block, role
            if role == "curator":
                message = next(
                    detail
                    for detail in details
                    if detail.source_path == "reference/message-handover.md"
                )
                _assert_curator_message_detail(
                    role_block,
                    "{{detail:reference/message-handover.md}}",
                    message.step,
                    message.content.decode(),
                )
                assert "reference/curator-message-details.md" not in expected
            if role in {"architect", "orchestrator", "manager"}:
                _assert_coordination_start_owner(result, role, tmp_path / f"{role}-{operation}.md")
            checked.add((role, operation))
    assert checked == {
        (role, operation)
        for operation, entry in manifest.operations.items()
        for role in entry.applies_to_roles
    }
    assert len(checked) == 32
    assert set(ROLE_DETAILS) == {
        "architect",
        "orchestrator",
        "manager",
        "worker",
        "curator",
        "reviewer",
        "investigator",
        "bootstrap",
    }


MOVED_ACTION_POINTERS = {
    "curator": (
        "reference/knowledge/use.md",
        ("Before checking code and memory, read", "Before interpreting a knowledge read, read"),
    ),
    "manager": (
        "reference/tasks/document-authoring.md",
        (
            "Before editing, skipping or previewing task-document fields, or using `task_doc "
            "append_decision`, read",
        ),
    ),
    "orchestrator": (
        "reference/tasks/document-authoring.md",
        ("Before using `task_doc append_decision`, read",),
    ),
    "worker": (
        "reference/tasks/document-authoring.md",
        ("Before editing, skipping or previewing task-document fields, read",),
    ),
}
SETTINGS_AUTHORITY_POINTER = "Before relying on storage or path eligibility, cross-repository allowances, or serving-build metadata, read [settings authority](skill://agents-remember/skills/l-01-agent-lifecycles/reference/knowledge/use.md#read-the-source-that-owns-a-setting) for the sources that govern those facts."


def _assert_moved_action_pointer(text: str, pointer: str) -> None:
    assert text.count(pointer) == 1, pointer


def test_moved_piece_pointers_reach_each_required_compile_and_reject_removal() -> None:
    manifest = _shipped_parsed()
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    shared = (root / "core/every-role.md").read_text()
    checked = set()
    for role, metadata in manifest.roles.items():
        for operation in metadata.operations:
            outcome = _compiled_outcome(role, operation)
            assert outcome.result is not None
            result = outcome.result
            assert outcome.shared_block is not None
            assert outcome.shared_block == shared
            assert shared_block(outcome.shared_block).count(shared) == 1
            rendered = result.render_instructions()
            source = next(
                record for record in result.manifest.sources if record.path == "core/every-role.md"
            )
            assert source.revision == "sha256:" + sha256(shared.encode()).hexdigest()
            # The shared body is delivered separately, not injected into render_instructions.
            _assert_moved_action_pointer(shared, SETTINGS_AUTHORITY_POINTER)
            with pytest.raises(AssertionError):
                _assert_moved_action_pointer(
                    shared.replace(SETTINGS_AUTHORITY_POINTER, "", 1),
                    SETTINGS_AUTHORITY_POINTER,
                )
            if role in MOVED_ACTION_POINTERS:
                target, actions = MOVED_ACTION_POINTERS[role]
                for action in actions:
                    assert_action_pointer(rendered, target, action)
                    pointer = next(line for line in rendered.splitlines() if action in line)
                    if role == "manager":
                        detail = next(
                            item
                            for item in result.capsule.detail_references
                            if item.source_path == "reference/tasks/document-authoring.md"
                        )
                        assert detail.content == (root / detail.source_path).read_bytes()
                        assert "codeExamplesNote" in detail.content.decode()
                    with pytest.raises(AssertionError):
                        assert_action_pointer(rendered.replace(pointer, "", 1), target, action)
                    with pytest.raises(AssertionError):
                        assert_action_pointer(rendered + "\n" + pointer, target, action)
            checked.add((role, operation))
    assert checked == {
        (role, operation)
        for operation, entry in manifest.operations.items()
        for role in entry.applies_to_roles
    }
    assert len(checked) == 32
    assert {role for role, _ in checked} == {
        "architect",
        "orchestrator",
        "manager",
        "worker",
        "curator",
        "reviewer",
        "investigator",
        "bootstrap",
    }


COLLABORATION_KEYS = ("tasks/collaboration-method", "tasks/document-authoring")


def _assert_collaboration_reference_matches_pointers(root: Path, document: dict) -> None:
    """The collaboration and authoring references equal their pointer-derived step and roles.

    The derivation (P5-05, ``manifest_step_derivation``) reads the first-message texts:
    roles, operations, SKILL.md and every-role.
    """
    problems = [
        problem
        for problem in manifest_mismatches(root, document)
        if problem.startswith(tuple(key + ":" for key in COLLABORATION_KEYS))
    ]
    assert problems == []
    assert document["references"]["tasks/collaboration-method"]["step"].strip()


def test_collaboration_reference_declares_actual_pointer_audiences_and_step() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    document = json.loads((root / "composition-manifest.json").read_bytes())
    reference = document["references"]["tasks/collaboration-method"]
    assert reference["source"] == "reference/tasks/collaboration-method.md"
    _assert_collaboration_reference_matches_pointers(root, document)
    assert reference["injected"] is False
    assert reference["step_detail"] is False
    assert (root / reference["source"]).is_file()
    assert reference["source"] not in {
        entry.source for entry in _shipped_parsed().detail_references
    }


def test_collaboration_reference_rejects_unpointed_audience_and_outbound_step(
    tmp_path: Path,
) -> None:
    source_root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    document = json.loads((source_root / "composition-manifest.json").read_bytes())
    reference = document["references"]["tasks/collaboration-method"]
    changed = json.loads(json.dumps(document))
    changed["references"]["tasks/collaboration-method"]["roles"] += [
        next(role for role in document["role_order"] if role not in reference["roles"])
    ]
    with pytest.raises(AssertionError):
        _assert_collaboration_reference_matches_pointers(source_root, changed)
    # Moving the inbound first-message pointer into the method itself leaves an outbound
    # self-reference, which cannot satisfy the callers' action contract.
    root = tmp_path / "l-01"
    shutil.copytree(source_root, root)
    every_role = root / "core/every-role.md"
    body = every_role.read_text()
    pointer = next(line for line in body.splitlines() if "[collaboration method](" in line)
    every_role.write_text(body.replace(pointer + "\n", "", 1))
    own = root / "reference/tasks/collaboration-method.md"
    own.write_text(own.read_text() + "\n" + pointer.split(": ", 1)[1] + "\n")
    assert every_role.read_text() != body
    with pytest.raises(AssertionError):
        _assert_collaboration_reference_matches_pointers(root, document)


def test_collaboration_reference_rejects_missing_actual_pointer_audience() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    document = json.loads((root / "composition-manifest.json").read_bytes())
    changed = json.loads(json.dumps(document))
    audience = changed["references"]["tasks/document-authoring"]["roles"]
    assert "worker" in audience
    audience.remove("worker")
    assert changed != document
    with pytest.raises(AssertionError):
        _assert_collaboration_reference_matches_pointers(root, changed)


LEAF_EVIDENCE_POINTER = (
    "Before creating a leaf through `task_doc`, read [retained-evidence lifecycle]"
    "(../../core/acceptance.md#record-how-retained-evidence-will-be-maintained) for the "
    "contract or expiry information its handoff must record for each new or retained "
    "fixture, recording, generator, shared support file or migration proof."
)
LEAF_AUTHORING_POINTERS = {
    "architect": "Before authoring a task document or requirement packet, read"
}


@pytest.mark.parametrize("role", ["architect"])
def test_leaf_creation_reaches_retained_evidence_lifecycle_and_rejects_removal(role: str) -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    authoring = (root / "reference/tasks/document-authoring.md").read_bytes()
    acceptance = (root / "core/acceptance.md").read_text()
    section = acceptance.split("## Record how retained evidence will be maintained\n", 1)[1]
    for clause in (
        "For each new or retained fixture, recording, generator, shared support file or "
        "migration proof, the handoff records one of these sets:",
        "1. Registered stable contract identity, real owner, executable evidence node "
        "and exact consumers.",
        "2. Expiry date, executable replacement or removal event, owner and compatibility "
        "consequence.",
        "Missing or contradictory catalog metadata requires implementation work.",
        "A satisfied requirement cannot waive missing lifecycle metadata for durable evidence.",
    ):
        assert clause in section
    checked = set()
    for operation in _shipped_parsed().roles[role].operations:
        result = _compiled(role, operation)
        rendered = result.render_instructions()
        action = LEAF_AUTHORING_POINTERS[role]
        assert_action_pointer(rendered, "reference/tasks/document-authoring.md", action)
        pointer = next(line for line in rendered.splitlines() if action in line)
        detail = next(
            item
            for item in result.capsule.detail_references
            if item.source_path == "reference/tasks/document-authoring.md"
        )
        assert detail.content == authoring
        assert detail.revision == "sha256:" + sha256(authoring).hexdigest()
        assert detail.step.strip()
        delivered = detail.content.decode()
        _assert_moved_action_pointer(delivered, LEAF_EVIDENCE_POINTER)
        for mutant in (rendered.replace(pointer, "", 1), rendered + "\n" + pointer):
            assert mutant != rendered
            with pytest.raises(AssertionError):
                assert_action_pointer(mutant, "reference/tasks/document-authoring.md", action)
        for text, required in ((delivered, LEAF_EVIDENCE_POINTER),):
            with pytest.raises(AssertionError):
                _assert_moved_action_pointer(text.replace(required, "", 1), required)
            with pytest.raises(AssertionError):
                _assert_moved_action_pointer(text + "\n" + required, required)
        checked.add(operation)
    assert checked == set(_shipped_parsed().roles[role].operations)


ARCHITECT_ACTION_READS = (
    ("r29-architect", "Before diagnosing, read", "knowledge/use"),
    ("0:0", "Take closeout trees by filename", "core/every-role"),
    (
        "20:2",
        "Before recovering idle, unfinished, stopped or unavailable-provider work, read",
        "coordination/shared/seat-recovery",
    ),
    (
        "21:0",
        "Before updating the master after a start, handover or landing, read",
        "coordination/shared/master-state",
    ),
    (
        "40:3",
        "Before recovering idle, unfinished, stopped or unavailable-provider work, read",
        "coordination/shared/seat-recovery",
    ),
    ("10:0", "If your harness offers helpers (sub-agents)", "core/every-role"),
    (
        "43:2",
        "Before selecting a seat model and its authorized settings, read",
        "coordination/architect/models-and-settings",
    ),
    (
        "51:0",
        "Before selecting a seat model and its authorized settings, read",
        "coordination/architect/models-and-settings",
    ),
    ("40:0", "Identify stalls from task progress.", "roles/architect"),
)


ARCHITECT_ACTION_CLAUSES = {
    "r29-architect": "When diagnosing a failure before proposing a repair task, use attributed invariants, "
    "families, sibling code and proofs alongside code reads to form and check the cause. Carry "
    "the evidence and remaining gaps into the packet and Prepared context.",
    "0:0": "Take closeout trees by filename from the highest-numbered memory PASS and landing-check verdict, "
    "never from a message.",
    "20:2": "When every agent of a leaf is idle, read each agent's last lines. If a turn ended silently while "
    "helpers still run, tell that seat to collect its results and finish. A valid declared helper wait "
    "remains a wait.",
    "21:0": "In the same turn as each start, role handover and landing, update the master document's parts "
    "changed by that act: its status note, counts, order state and **In work now** section. Include the "
    "task, stage, agents and time since that state began. Leave product-mirrored stage fields with "
    "their existing owner; do not write a contradictory second status.",
    "40:3": "After receiving a report, check whether its sender is still in a turn. If it ended with unfinished "
    "authorized work and no valid named wait or stop, send a continuation at once.",
    "10:0": "If your harness offers helpers (sub-agents), they run on your own seat's model unless the developer names another.",
    "40:0": "Identify stalls from task progress. These include fifteen minutes without an active turn, or a "
    "ready task left unstarted.",
    "43:2": "When a model the developer names is missing from a list, look it up online and check the harness's "
    "and host's versions before you ask him about another.",
    "51:0": "The developer may name role sets: complete role settings for different situations, for example one "
    "for when a provider is available and a cheaper one for when it is not. The Architect may switch between "
    "the sets he has named on its own, for example when a provider is out, and keeps the earlier "
    "settings bytes. Any other change to his settings needs his word for that case.",
}


def _assert_architect_action_read(text: str, action: str, detail: str) -> None:
    marker = "{{detail:reference/" + detail + ".md}}"
    matching = [line for line in text.splitlines() if action in line]
    assert len(matching) == 1, "missing or duplicate action cue"
    assert marker in matching[0], "action cue lost its delivered detail"
    assert "read [" in matching[0], "action cue lost its read instruction"


@pytest.mark.parametrize(("scenario_id", "action", "detail"), ARCHITECT_ACTION_READS)
def test_architect_named_action_reads_and_pointer_removal(scenario_id, action, detail) -> None:
    for operation in _shipped_parsed().roles["architect"].operations:
        result = _compiled("architect", operation)
        text = result.render_instructions()
        if scenario_id in {"0:0", "10:0"}:
            outcome = _compiled_outcome("architect", operation)
            assert outcome.result is not None
            assert outcome.shared_block is not None
            clause = ARCHITECT_ACTION_CLAUSES[scenario_id]
            _assert_moved_action_pointer(outcome.shared_block, clause)
            for mutant in (
                outcome.shared_block.replace(clause, "", 1),
                outcome.shared_block + "\n" + clause,
            ):
                with pytest.raises(AssertionError):
                    _assert_moved_action_pointer(mutant, clause)
            if scenario_id == "0:0":
                assert "Before selecting closeout trees, read" not in text
            continue
        if scenario_id == "40:3":
            outcome = _compiled_outcome("architect", operation)
            assert outcome.result is not None
            assert outcome.shared_block is not None
            _assert_architect_action_read(text, action, detail)
            clause = ARCHITECT_ACTION_CLAUSES[scenario_id]
            _assert_moved_action_pointer(outcome.shared_block, clause)
            for mutant in (
                outcome.shared_block.replace(clause, "", 1),
                outcome.shared_block + "\n" + clause,
            ):
                with pytest.raises(AssertionError):
                    _assert_moved_action_pointer(mutant, clause)
            continue
        if scenario_id == "40:0":
            role_body = next(
                unit.block.content
                for unit in result.capsule.instruction_units
                if unit.block.source_path == "roles/architect.md"
            )
            clause = ARCHITECT_ACTION_CLAUSES[scenario_id]
            _assert_moved_action_pointer(role_body, clause)
            for mutant in (role_body.replace(clause, "", 1), role_body + "\n" + clause):
                with pytest.raises(AssertionError):
                    _assert_moved_action_pointer(mutant, clause)
            continue
        _assert_architect_action_read(text, action, detail)
        delivered = next(
            item
            for item in result.capsule.detail_references
            if item.source_path == "reference/" + detail + ".md"
        )
        assert delivered.content, scenario_id
        clause = ARCHITECT_ACTION_CLAUSES[scenario_id]
        content = delivered.content.decode()
        assert clause in content
        mutant = content.replace(clause, "", 1)
        assert mutant != content
        with pytest.raises(AssertionError):
            assert clause in mutant
        marker = "{{detail:reference/" + detail + ".md}}"
        (action_line,) = (line for line in text.splitlines() if action in line)
        without_target = text.replace(action_line, action_line.replace(marker, "", 1), 1)
        assert without_target != text
        for mutant in (text.replace(action, "", 1), without_target):
            with pytest.raises(AssertionError):
                _assert_architect_action_read(mutant, action, detail)


@pytest.mark.parametrize(
    ("trigger", "heading"),
    [
        (
            "Before a new task starts, write its task document with one major step per role: build, review, curation, knowledge check and landing.",
            "Give each role a major step in new tasks",
        ),
        (
            "An existing task gains a step only when the developer slots new scope into it.",
            "Preserve existing task structure",
        ),
    ],
)
def test_architect_task_layout_reaches_its_existing_section(trigger, heading) -> None:
    source = "reference/coordination/shared/requirement-design.md"
    action = "Before authoring or amending a requirement and its task structure, read"
    for operation in _shipped_parsed().roles["architect"].operations:
        result = _compiled("architect", operation)
        text = result.render_instructions()
        assert_action_pointer(text, source, action)
        delivered = next(
            item for item in result.capsule.detail_references if item.source_path == source
        )
        content = delivered.content.decode()
        assert "## " + heading + "\n" in content
        assert trigger in content
        for mutant in (
            text.replace(action, "", 1),
            text.replace("{{detail:" + source + "}}", "", 1),
        ):
            assert mutant != text
            with pytest.raises(AssertionError):
                assert_action_pointer(mutant, source, action)
        mutant = content.replace(trigger, "", 1)
        assert mutant != content
        with pytest.raises(AssertionError):
            assert trigger in mutant


def _assert_shared_retirement_boundary(paragraph: str) -> None:
    for clause in (
        "including an admitted task retirement or folder movement",
        "same tool and input class before applying a stop",
        "Read, check, validation and sync refusals, starts, other product guards "
        "and commit gates retain their stops.",
    ):
        assert clause in paragraph


def _assert_retirement_permission_stop(result: CapsuleCompilationResult, report_path: Path) -> None:
    """The emitted refusal procedure reaches the complete permission-stop owner."""
    _instructions, detail = _emitted_instruction_detail(
        result, "reference/refused-write.md", report_path
    )
    for clause in (
        "An admitted `task_doc` retirement or folder movement is included.",
        "A missing tool or unadmitted capability is not a refused correct write.",
        "never a Git commit or another transaction step",
    ):
        assert clause in detail
    action = "Before acting on a denied or unanswered harness request, read"
    owner = _linked_instruction_source(
        detail,
        action,
        "operations/orientation.md",
        "honor-harness-permission-decisions",
        "Honor harness permission decisions",
    )
    for mutant in (
        detail.replace(action, "", 1),
        detail + "\n" + next(line for line in detail.splitlines() if action in line),
        detail.replace("#honor-harness-permission-decisions", "#formal-permissions", 1),
    ):
        with pytest.raises(AssertionError):
            _linked_instruction_source(
                mutant,
                action,
                "operations/orientation.md",
                "honor-harness-permission-decisions",
                "Honor harness permission decisions",
            )
    _assert_permission_owner_section(owner)


def _assert_served_retirement_permission_stop(paragraph: str) -> None:
    """Without a capsule detail, the shared skill:// link serves the same complete owner."""
    uri = "skill://agents-remember/skills/l-01-agent-lifecycles/reference/refused-write.md"
    assert paragraph.count("](" + uri + ")") == 1, "shared paragraph must link the served file"
    action = "Before acting on a denied or unanswered harness request, read"
    with shipped_composition_corpus() as (root, _manifest):
        detail = (root / "reference/refused-write.md").read_text()
        owner = (root / "operations/orientation.md").read_text()
    for clause in (
        "An admitted `task_doc` retirement or folder movement is included.",
        "A missing tool or unadmitted capability is not a refused correct write.",
        "never a Git commit or another transaction step",
    ):
        assert clause in detail
    link = (
        action + " [Honor harness permission decisions]"
        "(../operations/orientation.md#honor-harness-permission-decisions)"
    )
    assert detail.count(link) == 1
    _assert_permission_owner_section(owner)


def _assert_permission_owner_section(owner: str) -> None:
    section = owner.split("## Honor harness permission decisions\n", 1)[1].split("\n## ", 1)[0]
    clauses = (
        "When the harness asks to allow a tool call and the request is denied or unanswered, "
        "the agent does not reach the same effect by another tool, another command, a sub-agent "
        "or a later turn.",
        "At an open request it waits.",
        "After a denial it goes on without that effect and names it in its report.",
    )
    for clause in clauses:
        assert clause in section
        with pytest.raises(AssertionError):
            assert clause in section.replace(clause, "", 1)
    for facet in (
        "denied or unanswered",
        "another tool",
        "another command",
        "a sub-agent",
        "a later turn",
        "it waits",
        "names it in its report",
    ):
        damaged = section.replace(facet, "", 1)
        assert damaged != section
        with pytest.raises(AssertionError):
            assert all(clause in damaged for clause in clauses)


RETIREMENT_DETAIL_ROLES = frozenset({"manager"})


@pytest.mark.parametrize("role", ["architect", "orchestrator", "manager", "worker"])
def test_retirement_shared_source_and_delivered_detail_preserve_stops(role, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    shared = (root / "core/every-role.md").read_text()
    paragraph = next(
        line for line in shared.splitlines() if "correct-input refusal of a covered write" in line
    )
    _assert_shared_retirement_boundary(paragraph)
    for clause in (
        "including an admitted task retirement or folder movement",
        "same tool and input class before applying a stop",
        "Read, check, validation and sync refusals",
    ):
        with pytest.raises(AssertionError):
            _assert_shared_retirement_boundary(paragraph.replace(clause, "", 1))
    operations = {
        "architect": "orientation",
        "orchestrator": "coordination",
        "manager": "coordination",
        "worker": "implementation",
    }
    for operation in (operations[role],):
        outcome = _compiled_outcome(role, operation)
        assert outcome.result is not None
        assert outcome.shared_block == shared
        assert shared_block(outcome.shared_block).count(shared) == 1
        result = outcome.result
        assert outcome.shared_block is not None
        _assert_shared_retirement_boundary(
            next(
                line
                for line in outcome.shared_block.splitlines()
                if "correct-input refusal of a covered write" in line
            )
        )
        # P5-18: only the Manager's retirement pointer delivers the detail; the other
        # roles reach the same owner through the shared paragraph's served link. Both
        # paths are checked explicitly; neither is skipped.
        delivered = any(
            item.source_path == "reference/refused-write.md"
            for item in result.capsule.detail_references
        )
        assert delivered == (role in RETIREMENT_DETAIL_ROLES), role
        if delivered:
            _assert_retirement_permission_stop(result, tmp_path / f"{role}-retirement.md")
        else:
            _assert_served_retirement_permission_stop(paragraph)


def _assert_inline_rule_section(body: str, heading: str, clauses: tuple[str, ...]) -> None:
    assert body.count(heading + "\n") == 1
    section = body.split(heading + "\n", 1)[1]
    level = len(heading.split(maxsplit=1)[0])
    section = re.split(r"\n#{2," + str(level) + r"} ", section, maxsplit=1)[0]
    for clause in clauses:
        assert section.count(clause) == 1, clause


def test_v13_inline_rules_reach_their_actual_owning_sections_and_reject_deletion() -> None:
    manifest = _shipped_parsed()
    checked = set()
    seen = set()
    for role, metadata in manifest.roles.items():
        for operation in metadata.operations:
            outcome = _compiled_outcome(role, operation)
            assert outcome.result is not None
            result = outcome.result
            role_body = next(
                unit.block.content
                for unit in result.capsule.instruction_units
                if unit.block.source_path == f"roles/{role}.md"
            )
            for retired, owner, heading, clauses in INLINE_RULE_CLAUSES:
                assert retired not in {d.source_path for d in result.capsule.detail_references}
                assert "{{detail:" + retired + "}}" not in result.render_instructions()
                if owner not in {"core/every-role.md", f"roles/{role}.md"}:
                    continue
                body = outcome.shared_block if owner == "core/every-role.md" else role_body
                assert body is not None
                _assert_inline_rule_section(body, heading, clauses)
                for clause in clauses:
                    for mutant in (
                        body.replace(clause, "", 1),
                        body.replace(clause, clause * 2, 1),
                    ):
                        assert mutant != body
                        with pytest.raises(AssertionError):
                            _assert_inline_rule_section(mutant, heading, clauses)
                seen.add(retired)
            checked.add((role, operation))
    assert len(checked) == 32
    assert checked == {
        (role, operation)
        for operation, entry in manifest.operations.items()
        for role in entry.applies_to_roles
    }
    assert seen == {retired for retired, _owner, _heading, _clauses in INLINE_RULE_CLAUSES}
    assert len(seen) == 28


def _assert_short_shared_rule(body: str, line: str) -> None:
    assert body.count(line) == 1


def test_short_rules_use_shared_delivery_and_reject_removed_step_reads() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    shared = (root / "core/every-role.md").read_text()
    units = {
        item["id"]: item
        for item in json.loads((root / "core/digest-map.json").read_bytes())["units"]
    }
    checked = set()
    for role, metadata in _shipped_parsed().roles.items():
        for operation in metadata.operations:
            outcome = _compiled_outcome(role, operation)
            assert outcome.result is not None
            assert outcome.shared_block is not None
            assert outcome.shared_block == shared
            assert shared_block(outcome.shared_block).count(shared) == 1
            source = next(
                record
                for record in outcome.result.manifest.sources
                if record.path == "core/every-role.md"
            )
            assert source.revision == "sha256:" + sha256(shared.encode()).hexdigest()
            for unit_id, owner, line, roles, clauses in SHARED_SHORT_RULE_CLAUSES:
                unit = units[unit_id]
                assert unit["file"] == owner
                assert set(roles) <= set(unit["audience"])
                assert unit["disposition"] == "digest"
                assert unit["digestPlace"]
                start, end = unit["lines"]
                core = "\n".join((root / owner).read_text().splitlines()[start - 1 : end])
                assert sha256(core.encode()).hexdigest() == unit["sha256"]
                for clause in clauses:
                    assert clause in core
                _assert_short_shared_rule(outcome.shared_block, line)
                for mutant in (
                    outcome.shared_block.replace(line, "", 1),
                    outcome.shared_block + "\n" + line,
                ):
                    with pytest.raises(AssertionError):
                        _assert_short_shared_rule(mutant, line)
            rendered = outcome.result.render_instructions()
            for actor, _source, action in REMOVED_SHORT_RULE_ACTIONS:
                if actor == role:
                    assert action not in rendered
            assert "#handle-held-delivery" not in rendered
            assert "#notify-the-seat-waiting-on-a-completed-step" not in rendered
            checked.add((role, operation))
    assert len(checked) == 32
    assert checked == {
        (role, operation)
        for operation, entry in _shipped_parsed().operations.items()
        for role in entry.applies_to_roles
    }


@pytest.mark.parametrize("case", GROUPED_DETAIL_CLAUSES)
def test_grouped_action_reads_the_whole_delivered_owner(
    case: tuple[str, str, str, str, tuple[str, ...]], tmp_path: Path
) -> None:
    role, source, action, suffix, clauses = case
    for operation in _shipped_parsed().roles[role].operations:
        result = _compiled(role, operation)
        rendered = result.render_instructions()
        assert_action_pointer(rendered, source, action)
        marker = "{{detail:" + source + "}}"
        paragraph = next(part for part in rendered.split("\n\n") if action in part)
        assert paragraph.count(marker + ")" + suffix) == 1
        assert marker + "#" not in paragraph
        for mutant in (
            rendered.replace(action, "", 1),
            rendered.replace(marker + ")", marker + "#wrong-section)", 1),
        ):
            assert mutant != rendered
            with pytest.raises(AssertionError):
                changed = next(part for part in mutant.split("\n\n") if marker in part)
                assert action in changed and marker + ")" + suffix in changed
        _instructions, delivered = _emitted_instruction_detail(
            result, source, tmp_path / f"{role}-{operation}.md"
        )
        for clause in clauses:
            assert clause in delivered
            with pytest.raises(AssertionError):
                assert clause in delivered.replace(clause, "", 1)
