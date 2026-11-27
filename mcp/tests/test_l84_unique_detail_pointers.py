"""All admitted capsules deliver each required detail once, through its existing role/step."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from agents_remember.cli.role_instruction_details import compose_launch_instructions
from agents_remember.models.role_capsules.types import CapsuleCompilationResult
from agents_remember.serving.capsule_delivery import capsule_delivery_from
from agents_remember_test_support.testing.capsule_audience_fixtures import (
    CURATOR_MESSAGE_CONTENT,
)
from agents_remember_test_support.testing.leaf_instruction_wording import assert_action_pointer
from agents_remember_test_support.testing.manifest_step_derivation import (
    sentence_start,
)
from agents_remember_test_support.testing.pointer_renewal_cases import (
    assert_pointer_ambiguity_requires_explicit_bindings,
    assert_pointer_binding_uses_identity,
    assert_pointer_changes_do_not_learn_admissions,
    assert_pointer_wording_is_current,
)
from agents_remember_test_support.testing.pointer_uniqueness import duplicate_pointers
from test_l84_capsule_delivery_proofs import (
    _assert_index_detail_pointer,
    _compiled,
    _compiled_outcome,
    _linked_instruction_source,
    _shipped_parsed,
)

# Complete current reference population delivered via role Reference indexes.
ROLE_DETAILS = {
    "architect": [
        "reference/coordination/shared/requirement-design.md",
        "reference/tasks/document-authoring.md",
        "reference/knowledge/commands.md",
        "reference/knowledge/use.md",
        "reference/coordination/architect/models-and-settings.md",
        "reference/sandbox-actors.md",
        "reference/coordination/shared/master-state.md",
        "reference/coordination/shared/seat-recovery.md",
        "reference/coordination/shared/third-rejected-round.md",
        "reference/coordination/architect/process-decisions.md",
        "reference/coordination/architect/claim-verification.md",
        "reference/coordination/architect/developer-decisions.md",
        "reference/coordination/shared/child-questions.md",
        "reference/message-handover.md",
        "reference/coordination/architect/curator-measurement.md",
        "reference/review/advisory-reviews.md",
        "reference/coordination/shared/closeout-recovery.md",
    ],
    "orchestrator": [
        "reference/coordination/shared/seat-recovery.md",
        "reference/coordination/shared/master-state.md",
        "reference/tasks/document-authoring.md",
        "reference/coordination/shared/closeout-recovery.md",
        "reference/knowledge/commands.md",
        "reference/message-handover.md",
        "reference/sandbox-actors.md",
        "reference/coordination/shared/child-questions.md",
    ],
    "manager": [
        "reference/coordination/shared/seat-recovery.md",
        "reference/coordination/shared/third-rejected-round.md",
        "reference/worker/sync-and-repairs.md",
        "reference/coordination/shared/closeout-recovery.md",
        "reference/coordination/shared/master-state.md",
        "reference/tasks/document-authoring.md",
        "reference/refused-write.md",
        "reference/knowledge/commands.md",
        "reference/message-handover.md",
        "reference/sandbox-actors.md",
        "reference/coordination/shared/child-questions.md",
    ],
    "worker": [
        "reference/knowledge/use.md",
        "reference/knowledge/commands.md",
        "reference/knowledge/serving-build.md",
        "reference/tasks/document-authoring.md",
        "templates/turn-report.md",
        "reference/writing/visual-explainers.md",
        "reference/message-handover.md",
        "reference/worker/sync-and-repairs.md",
        "reference/sandbox-actors.md",
    ],
    "curator": [
        "reference/knowledge/use.md",
        "reference/knowledge/commands.md",
        "reference/knowledge/serving-build.md",
        "reference/curator/intent-reconciliation.md",
        "reference/curator/worklist.md",
        "reference/curator/knowledge-ingest.md",
        "reference/curator/publication-report.md",
        "reference/curator/family-obligations.md",
        "reference/curator/change-routing.md",
        "reference/curator/encountered-records.md",
        "reference/curator/current-prose-and-history.md",
        "reference/curator/card-sentence-decisions.md",
        "reference/curator/card-formats-and-citations.md",
        "reference/curator/governing-sources.md",
        "reference/writing/technical-english-profile.md",
        "reference/curator/coherence-judgments.md",
        "reference/curator/memory-handover.md",
        "reference/message-handover.md",
        "reference/curator/exceptions-and-recovery.md",
        "reference/curator/repository-foundation.md",
        "reference/worker/sync-and-repairs.md",
        "reference/sandbox-actors.md",
    ],
    "reviewer": [
        "reference/review/non-leaf-seams-and-catalogs.md",
        "reference/review/rounds-and-late-findings.md",
        "reference/review-baseline.md",
        "reference/knowledge/use.md",
        "reference/knowledge/commands.md",
        "reference/message-handover.md",
        "reference/sandbox-actors.md",
    ],
    "consolidator": [
        "reference/knowledge/use.md",
        "reference/knowledge/commands.md",
        "reference/consolidation-pass.md",
        "reference/curator/worklist.md",
        "reference/curator/knowledge-ingest.md",
        "reference/curator/publication-report.md",
        "reference/curator/family-obligations.md",
        "reference/curator/governing-sources.md",
        "reference/writing/technical-english-profile.md",
        "reference/curator/coherence-judgments.md",
        "reference/message-handover.md",
        "reference/worker/sync-and-repairs.md",
        "reference/sandbox-actors.md",
    ],
    "investigator": [
        "reference/knowledge/commands.md",
        "reference/knowledge/use.md",
        "reference/instruction-shortfall-review.md",
        "reference/sandbox-actors.md",
        "reference/message-handover.md",
    ],
    "bootstrap": [
        "reference/bootstrap/foundation-state.md",
        "reference/knowledge/commands.md",
        "reference/sandbox-actors.md",
    ],
}

OPERATION_DETAILS = {
    "bootstrap/bootstrap": [
        "reference/bootstrap/failure-states.md",
    ],
    "architect/review": [
        "reference/review/advisory-reviews.md",
    ],
}

# (source, trigger_cell, link_label, fragment) for every role's Reference index rows.
ROLE_INDEX_POINTERS: dict[str, list[tuple[str, str, str, str]]] = {
    "architect": [
        (
            "reference/coordination/shared/requirement-design.md",
            "Reqs/`task_doc`/`context_packet`/`knowledge_read`",
            "design",
            "",
        ),
        (
            "reference/tasks/document-authoring.md",
            "Reqs/`task_doc`/`context_packet`/`knowledge_read`",
            "authoring",
            "",
        ),
        (
            "reference/knowledge/commands.md",
            "Reqs/`task_doc`/`context_packet`/`knowledge_read`",
            "cmds",
            "",
        ),
        (
            "reference/knowledge/use.md",
            "Reqs/`task_doc`/`context_packet`/`knowledge_read`",
            "diagnose",
            "#architect-diagnose-before-proposing-a-repair",
        ),
        (
            "reference/coordination/architect/models-and-settings.md",
            "Models/sandbox (`role_start`), master, seats (`retire_child`), 3rd",
            "models",
            "",
        ),
        (
            "reference/sandbox-actors.md",
            "Models/sandbox (`role_start`), master, seats (`retire_child`), 3rd",
            "sandbox",
            "",
        ),
        (
            "reference/coordination/shared/master-state.md",
            "Models/sandbox (`role_start`), master, seats (`retire_child`), 3rd",
            "master",
            "",
        ),
        (
            "reference/coordination/shared/seat-recovery.md",
            "Models/sandbox (`role_start`), master, seats (`retire_child`), 3rd",
            "seats",
            "",
        ),
        (
            "reference/coordination/shared/third-rejected-round.md",
            "Models/sandbox (`role_start`), master, seats (`retire_child`), 3rd",
            "3rd",
            "",
        ),
        (
            "reference/coordination/architect/process-decisions.md",
            "Process, claims, dev/child Qs, `role_message`",
            "process",
            "",
        ),
        (
            "reference/coordination/architect/claim-verification.md",
            "Process, claims, dev/child Qs, `role_message`",
            "claims",
            "",
        ),
        (
            "reference/coordination/architect/developer-decisions.md",
            "Process, claims, dev/child Qs, `role_message`",
            "dev",
            "",
        ),
        (
            "reference/coordination/shared/child-questions.md",
            "Process, claims, dev/child Qs, `role_message`",
            "child",
            "",
        ),
        (
            "reference/message-handover.md",
            "Process, claims, dev/child Qs, `role_message`",
            "msgs",
            "",
        ),
        (
            "reference/coordination/architect/curator-measurement.md",
            "Meas., advisory, flat closeout",
            "meas",
            "",
        ),
        (
            "reference/review/advisory-reviews.md",
            "Meas., advisory, flat closeout",
            "advisory",
            "",
        ),
        (
            "reference/coordination/shared/closeout-recovery.md",
            "Meas., advisory, flat closeout",
            "flat",
            "#flat-mode-direct-paired-closeout",
        ),
    ],
    "orchestrator": [
        (
            "reference/coordination/shared/seat-recovery.md",
            "Manager brief, stages (`role_start`), hand-off, seats (`retire_child`), master/decisions (`task_doc`), closeout",
            "seats",
            "",
        ),
        (
            "reference/coordination/shared/master-state.md",
            "Manager brief, stages (`role_start`), hand-off, seats (`retire_child`), master/decisions (`task_doc`), closeout",
            "master",
            "",
        ),
        (
            "reference/tasks/document-authoring.md",
            "Manager brief, stages (`role_start`), hand-off, seats (`retire_child`), master/decisions (`task_doc`), closeout",
            "decisions",
            "#preserve-decisions-and-approved-intent",
        ),
        (
            "reference/coordination/shared/closeout-recovery.md",
            "Manager brief, stages (`role_start`), hand-off, seats (`retire_child`), master/decisions (`task_doc`), closeout",
            "recovery",
            "#recover-an-unfinished-closeout",
        ),
        (
            "reference/knowledge/commands.md",
            "`knowledge_read`/`context_packet`, `role_message`, sandbox, child Qs",
            "cmds",
            "",
        ),
        (
            "reference/message-handover.md",
            "`knowledge_read`/`context_packet`, `role_message`, sandbox, child Qs",
            "msgs",
            "",
        ),
        (
            "reference/sandbox-actors.md",
            "`knowledge_read`/`context_packet`, `role_message`, sandbox, child Qs",
            "sandbox",
            "",
        ),
        (
            "reference/coordination/shared/child-questions.md",
            "`knowledge_read`/`context_packet`, `role_message`, sandbox, child Qs",
            "child",
            "",
        ),
    ],
    "manager": [
        (
            "reference/coordination/shared/seat-recovery.md",
            "Seats (`retire_child`), 3rd, sync (`worktree_closeout_preview`), switch (`worktree_closeout_*`)",
            "seats",
            "",
        ),
        (
            "reference/coordination/shared/third-rejected-round.md",
            "Seats (`retire_child`), 3rd, sync (`worktree_closeout_preview`), switch (`worktree_closeout_*`)",
            "3rd",
            "",
        ),
        (
            "reference/worker/sync-and-repairs.md",
            "Seats (`retire_child`), 3rd, sync (`worktree_closeout_preview`), switch (`worktree_closeout_*`)",
            "sync",
            "#synchronize-the-leaf",
        ),
        (
            "reference/coordination/shared/closeout-recovery.md",
            "Seats (`retire_child`), 3rd, sync (`worktree_closeout_preview`), switch (`worktree_closeout_*`)",
            "switch",
            "#orchestrator-recipient-switch-and-closeout-notices",
        ),
        (
            "reference/coordination/shared/master-state.md",
            "Master (`task_doc`/`worktree_abandon`/`record_landing`), retire, cmds, msgs, sandbox, child",
            "master",
            "",
        ),
        (
            "reference/tasks/document-authoring.md",
            "Master (`task_doc`/`worktree_abandon`/`record_landing`), retire, cmds, msgs, sandbox, child",
            "authoring",
            "",
        ),
        (
            "reference/refused-write.md",
            "Master (`task_doc`/`worktree_abandon`/`record_landing`), retire, cmds, msgs, sandbox, child",
            "retire",
            "#apply-the-procedure-to-a-task-document-or-folder",
        ),
        (
            "reference/knowledge/commands.md",
            "Master (`task_doc`/`worktree_abandon`/`record_landing`), retire, cmds, msgs, sandbox, child",
            "cmds",
            "",
        ),
        (
            "reference/message-handover.md",
            "Master (`task_doc`/`worktree_abandon`/`record_landing`), retire, cmds, msgs, sandbox, child",
            "msgs",
            "",
        ),
        (
            "reference/sandbox-actors.md",
            "Master (`task_doc`/`worktree_abandon`/`record_landing`), retire, cmds, msgs, sandbox, child",
            "sandbox",
            "",
        ),
        (
            "reference/coordination/shared/child-questions.md",
            "Master (`task_doc`/`worktree_abandon`/`record_landing`), retire, cmds, msgs, sandbox, child",
            "child",
            "",
        ),
    ],
    "worker": [
        ("reference/knowledge/use.md", "Knowledge/CLI", "use", ""),
        ("reference/knowledge/commands.md", "Knowledge/CLI", "cmds", ""),
        ("reference/knowledge/serving-build.md", "Knowledge/CLI", "cli", ""),
        ("reference/tasks/document-authoring.md", "Task/report/list", "task", ""),
        ("templates/turn-report.md", "Task/report/list", "report", "#shape"),
        ("reference/writing/visual-explainers.md", "Task/report/list", "explainer", ""),
        ("reference/message-handover.md", "Msg/sync/repairs", "msg", ""),
        ("reference/worker/sync-and-repairs.md", "Msg/sync/repairs", "sync", ""),
        ("reference/sandbox-actors.md", "Sandbox/ask", "sandbox", ""),
    ],
    "curator": [
        ("reference/knowledge/use.md", "Knowledge/CLI", "use", ""),
        ("reference/knowledge/commands.md", "Knowledge/CLI", "cmds", ""),
        ("reference/knowledge/serving-build.md", "Knowledge/CLI", "cli", ""),
        ("reference/curator/intent-reconciliation.md", "Intake/worklist", "reconcile", ""),
        ("reference/curator/worklist.md", "Intake/worklist", "worklist", ""),
        ("reference/curator/knowledge-ingest.md", "Write/route", "ingest", ""),
        ("reference/curator/publication-report.md", "Write/route", "pub", ""),
        ("reference/curator/family-obligations.md", "Write/route", "families", ""),
        ("reference/curator/change-routing.md", "Write/route", "route", ""),
        ("reference/curator/encountered-records.md", "Write/route", "encountered", ""),
        ("reference/curator/current-prose-and-history.md", "Cards/style", "prose", ""),
        ("reference/curator/card-sentence-decisions.md", "Cards/style", "sentences", ""),
        ("reference/curator/card-formats-and-citations.md", "Cards/style", "cards", ""),
        ("reference/curator/governing-sources.md", "Cards/style", "governing", ""),
        ("reference/writing/technical-english-profile.md", "Cards/style", "style", ""),
        ("reference/curator/coherence-judgments.md", "Coherence/msg", "coherence", ""),
        ("reference/curator/memory-handover.md", "Coherence/msg", "handover", ""),
        ("reference/message-handover.md", "Coherence/msg", "msg", ""),
        ("reference/curator/exceptions-and-recovery.md", "Exceptions/sync", "exceptions", ""),
        ("reference/curator/repository-foundation.md", "Exceptions/sync", "foundation", ""),
        (
            "reference/worker/sync-and-repairs.md",
            "Exceptions/sync",
            "sync",
            "#synchronize-the-leaf",
        ),
        ("reference/sandbox-actors.md", "Exceptions/sync", "sandbox", ""),
    ],
    "reviewer": [
        ("reference/review/non-leaf-seams-and-catalogs.md", "Non-leaf/rounds", "non-leaf", ""),
        ("reference/review/rounds-and-late-findings.md", "Non-leaf/rounds", "rounds", ""),
        ("reference/review-baseline.md", "Baseline/knowledge", "baseline", ""),
        ("reference/knowledge/use.md", "Baseline/knowledge", "use", ""),
        ("reference/knowledge/commands.md", "Baseline/knowledge", "cmds", ""),
        ("reference/message-handover.md", "Templates & msg", "msg", ""),
        ("reference/sandbox-actors.md", "Sandbox/ask", "sandbox", ""),
    ],
    "consolidator": [
        ("reference/knowledge/use.md", "Knowledge/CLI", "use", ""),
        ("reference/knowledge/commands.md", "Knowledge/CLI", "cmds", ""),
        ("reference/consolidation-pass.md", "Pass/worklist", "pass", ""),
        ("reference/curator/worklist.md", "Pass/worklist", "worklist", ""),
        ("reference/curator/knowledge-ingest.md", "Write/families", "ingest", ""),
        ("reference/curator/publication-report.md", "Write/families", "pub", ""),
        ("reference/curator/family-obligations.md", "Write/families", "families", ""),
        ("reference/curator/governing-sources.md", "Write/families", "governing", ""),
        ("reference/writing/technical-english-profile.md", "Write/families", "style", ""),
        ("reference/curator/coherence-judgments.md", "Coherence/msg", "coherence", ""),
        ("reference/message-handover.md", "Coherence/msg", "msg", ""),
        (
            "reference/worker/sync-and-repairs.md",
            "Exceptions/sync",
            "sync",
            "#synchronize-the-leaf",
        ),
        ("reference/sandbox-actors.md", "Exceptions/sync", "sandbox", ""),
    ],
    "investigator": [
        ("reference/knowledge/commands.md", "Knowledge readers", "cmds", ""),
        (
            "reference/knowledge/use.md",
            "Knowledge readers",
            "claims",
            "#investigator-distinguish-claims-observations-and-hypotheses",
        ),
        ("reference/instruction-shortfall-review.md", "Research/shortfalls/msg", "shortfalls", ""),
        ("reference/sandbox-actors.md", "Research/shortfalls/msg", "sandbox", ""),
        (
            "reference/message-handover.md",
            "Research/shortfalls/msg",
            "msgs",
            "#send-a-message-that-names-the-files",
        ),
    ],
    "bootstrap": [
        (
            "reference/bootstrap/foundation-state.md",
            "Report, `memory_init`, cmds",
            "foundation",
            "#read-the-state",
        ),
        ("reference/knowledge/commands.md", "Report, `memory_init`, cmds", "cmds", ""),
        ("reference/sandbox-actors.md", "Settings, sandbox", "sandbox", ""),
    ],
}

# Operation-level prose detail pointers: (role, operation) -> [(source, action_prefix)].
OPERATION_PROSE_POINTERS: dict[tuple[str, str], list[tuple[str, str]]] = {
    ("bootstrap", "bootstrap"): [
        (
            "reference/bootstrap/failure-states.md",
            "When hitting unfamiliar states "
            "(`memory_init`/`memory_baseline_status`/`user_settings_init`/"
            "`server_info`/`context_packet`/`runtime_install`), read",
        ),
    ],
    ("architect", "review"): [
        (
            "reference/review/advisory-reviews.md",
            "Before commissioning/routing advisory reviews via `role_start`, read",
        ),
    ],
}

# Distinct-action exceptions explicitly admitted in pointer-actions.json where both a role
# Reference index and an operation prose step point to the same detail file for distinct acts.
ADMITTED_ROLE_AND_OPERATION_DETAILS: dict[tuple[str, str], frozenset[str]] = {
    ("architect", "review"): frozenset({"reference/review/advisory-reviews.md"}),
}


def test_pointer_wording_renewal_matches_identity_after_reordering(tmp_path: Path) -> None:
    assert_pointer_binding_uses_identity(tmp_path)


def test_pointer_wording_renewal_requires_explicit_ambiguous_bindings(tmp_path: Path) -> None:
    assert_pointer_ambiguity_requires_explicit_bindings(tmp_path)


def test_pointer_wording_renewal_preserves_admission_policy(tmp_path: Path) -> None:
    assert_pointer_changes_do_not_learn_admissions(tmp_path)


def _assert_moved_action_pointer(text: str, pointer: str) -> None:
    assert text.count(pointer) == 1, pointer


def _assert_coordination_start_owner(
    result: CapsuleCompilationResult, role: str, report_path: Path
) -> None:
    """Direct start pointers retain the complete stage and first-intake owner."""
    action, label = {
        "manager": (
            "Check packet deps before starting/integrating (the product does not perform that check); follow",
            "leaf stages",
        ),
        "orchestrator": ("[Manager](../templates/manager-brief.md),", "stages"),
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
    if role == "orchestrator":
        matches = re.findall(r"\[Manager\]\([^)\n]+templates/manager-brief\.md\),", instructions)
        assert len(matches) == 1
        emitted_action = matches[0]
    else:
        emitted_action = action
    owner = _linked_instruction_source(
        instructions,
        emitted_action,
        "operations/coordination.md",
        "start-leaf-stages-and-deliver-the-first-intake",
        "Start leaf stages and deliver the first intake",
    )
    for clause in (
        "The Manager calls `role_start` once per stage: Worker first, Reviewer at the freeze, "
        "and Curator when curation begins. Start only the Worker at build. The Worker's freeze "
        "notice and the Reviewer's code-`PASS` notice go to the Manager first.",
        "Send intake before inspection (mode, task/purpose, seam, req set, tools; "
        "candidate/attempts at freeze/exit).",
        "After `role_start`, send Reviewer's intake via `role_message` (mode, purpose, "
        "`system/tools.md`, req IDs/versions/packets, approval record); it inspects nothing earlier.",
    ):
        assert clause in owner
        with pytest.raises(AssertionError):
            assert clause in owner.replace(clause, "", 1)
    for mutant in (
        instructions.replace(emitted_action, "", 1),
        instructions
        + "\n"
        + next(line for line in instructions.splitlines() if emitted_action in line),
    ):
        with pytest.raises(AssertionError):
            _linked_instruction_source(
                mutant,
                emitted_action,
                "operations/coordination.md",
                "start-leaf-stages-and-deliver-the-first-intake",
                "Start leaf stages and deliver the first intake",
            )


def _detail_pointer_sentences(rendered: str, source: str) -> int:
    """Sentences that point to one detail; two of its sections in one sentence are one pointer."""
    marker = re.compile(re.escape("{{detail:" + source + "}}"))
    return sum(
        len({sentence_start(line, match.start()) for match in marker.finditer(line)})
        for line in rendered.splitlines()
    )


def _assert_lists_disjoint(
    role_list: list[tuple[str, str]],
    operation_list: list[tuple[str, str]],
    allowed: frozenset[str] = frozenset(),
) -> None:
    """A detail pointed to from the role file is not pointed to again by the operation."""
    shared = (
        {source for source, _ in role_list} & {source for source, _ in operation_list}
    ) - allowed
    assert not shared, sorted(shared)


def _assert_curator_message_detail_local(role_block: str, step: str, content: str) -> None:
    assert step.strip()
    _assert_index_detail_pointer(
        role_block, "reference/message-handover.md", "Coherence/msg", "msg", ""
    )
    assert "curator-message-details.md" not in role_block
    for clause in CURATOR_MESSAGE_CONTENT:
        assert content.count(clause) == 1, clause
        assert clause not in role_block, clause


def _first_message_units(role: str, operation: str) -> list[tuple[str, str]]:
    outcome = _compiled_outcome(role, operation)
    assert outcome.result is not None and outcome.shared_block is not None
    units = [
        (unit.block.source_path, unit.block.content)
        for unit in outcome.result.capsule.instruction_units
    ]
    return [*units, ("core/every-role.md", outcome.shared_block)]


MH_SECTION = "../reference/message-handover.md#send-a-message-that-names-the-files"
#: (role, operation, source, sentence): duplicates re-added to current first messages. They
#: cover whole-file plus section links, table-row plus prose links, non-Before/When/For openers,
#: and cross-file repeats.
RESTORED_DUPLICATES = (
    (
        "architect",
        "planning",
        "operations/planning.md",
        "Before authoring a requirement, read [requirement design]"
        "(../reference/coordination/shared/requirement-design.md"
        "#plan-a-topic-merge-or-propose-a-split) for merges and splits.",
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
        "operations/curation.md",
        "Before routing a notes item, read [change routing](../reference/curator/"
        "change-routing.md) for its home.",
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
    (
        "orchestrator",
        "coordination",
        "roles/orchestrator.md",
        "Before starting a role and delivering its first intake, read [Start leaf stages and "
        "deliver the first intake](../operations/coordination.md#start-leaf-stages-and-deliver-"
        "the-first-intake) for stage timing and intake fields.",
    ),
    (
        "worker",
        "implementation",
        "roles/worker.md",
        "Before asking a developer question, read [Ask for a developer decision]"
        "(../operations/orientation.md#ask-for-a-developer-decision) for the route.",
    ),
    (
        "reviewer",
        "review",
        "roles/reviewer.md",
        "Inspect non-leaf criteria under [non-leaf seams]"
        "(../reference/review/non-leaf-seams-and-catalogs.md).",
    ),
)


def test_every_native_first_message_has_one_pointer_per_action() -> None:
    """One pointer per action per first message, keyed by target file, every link."""
    assert_pointer_wording_is_current()
    manifest = _shipped_parsed()
    checked = set()
    for role, entry in manifest.roles.items():
        for operation in entry.operations:
            units = _first_message_units(role, operation)
            assert duplicate_pointers(units) == [], (role, operation)
            checked.add((role, operation))
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
    # Each restored duplicate, re-added to its first message, fails.
    for role, operation, source, added in RESTORED_DUPLICATES:
        units = _first_message_units(role, operation)
        assert any(unit_source == source for unit_source, _ in units), (role, operation, source)
        mutant = [(s, t + "\n\n" + added if s == source else t) for s, t in units]
        assert duplicate_pointers(mutant), (role, operation, added)
    # A second link into one file inside another action's sentence, in place, fails.
    units = _first_message_units("reviewer", "review")
    kept = "[code-seam](../criteria/code-seam.md),"
    restored = (
        "[code-seam](../criteria/code-seam.md) and [code-seam again](../criteria/code-seam.md#top),"
    )
    assert sum(text.count(kept) for _, text in units) == 1
    mutant = [(s, t.replace(kept, restored)) for s, t in units]
    assert any("one sentence links it twice" in p for p in duplicate_pointers(mutant))
    # An unlisted second wording of a detail pointer fails, and a listed wording from
    # another source fails with a foreign-source diagnosis.
    units = _first_message_units("worker", "implementation")
    unlisted = (
        "Before sending a message, read [message handovers and receipts]"
        "({{detail:reference/message-handover.md}}) for the payload fields."
    )
    mutant = [(s, t + "\n\n" + unlisted if s == "roles/worker.md" else t) for s, t in units]
    assert any("unlisted repeat" in problem for problem in duplicate_pointers(mutant))
    foreign = (
        "On full-store refusal: "
        "[full-store]({{detail:reference/message-handover.md}}#handle-a-full-store-refusal)."
    )
    mutant = [(s, t + "\n\n" + foreign if s == "roles/worker.md" else t) for s, t in units]
    assert any(
        "listed action from another source" in problem for problem in duplicate_pointers(mutant)
    )


FULL_STORE_POINTER = "On full-store refusal: [full-store](../reference/message-handover.md#handle-a-full-store-refusal)."


def test_every_required_detail_has_one_pointer_in_all_real_compiles(tmp_path: Path):
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
            role_entries = ROLE_INDEX_POINTERS[role]
            op_entries = OPERATION_PROSE_POINTERS.get((role, operation), [])
            _assert_lists_disjoint(
                [(source, trigger) for source, trigger, _label, _frag in role_entries],
                op_entries,
                ADMITTED_ROLE_AND_OPERATION_DETAILS.get((role, operation), frozenset()),
            )
            for detail in details:
                assert detail.content and detail.step.strip()
                role_matches = [item for item in role_entries if item[0] == detail.source_path]
                op_matches = [item for item in op_entries if item[0] == detail.source_path]
                assert role_matches or op_matches
                assert _detail_pointer_sentences(rendered, detail.source_path) == (
                    len(role_matches) + len(op_matches)
                )
                for source, trigger, label, fragment in role_matches:
                    _assert_index_detail_pointer(rendered, source, trigger, label, fragment)
                    if fragment:
                        marker = "{{detail:" + source + "}}" + fragment + ")"
                        mutant = rendered.replace(
                            marker, marker.replace(fragment, "#wrong-section"), 1
                        )
                        assert mutant != rendered
                        with pytest.raises(AssertionError):
                            _assert_index_detail_pointer(mutant, source, trigger, label, fragment)
                for source, action in op_matches:
                    assert_action_pointer(rendered, source, action)
                    mutant = rendered.replace(action, "Read during intake", 1)
                    assert mutant != rendered
                    with pytest.raises(AssertionError):
                        assert_action_pointer(mutant, source, action)
            role_block = next(
                unit.block.content
                for unit in result.capsule.instruction_units
                if unit.block.source_path == f"roles/{role}.md"
            )
            assert ("{{detail:reference/message-handover.md}}" in role_block) == (
                role != "bootstrap"
            )
            # Recovery carries the one full-store refusal pointer for non-bootstrap roles;
            # role files carry none.
            assert rendered.count(FULL_STORE_POINTER) == (
                operation == "recovery" and role != "bootstrap"
            ), (role, operation)
            assert "handle-a-full-store-refusal" not in role_block, role
            if role == "curator":
                message = next(
                    detail
                    for detail in details
                    if detail.source_path == "reference/message-handover.md"
                )
                _assert_curator_message_detail_local(
                    role_block,
                    message.step,
                    message.content.decode(),
                )
                assert "reference/curator-message-details.md" not in expected
            if role in {"orchestrator", "manager"}:
                _assert_coordination_start_owner(result, role, tmp_path / f"{role}-{operation}.md")
            checked.add((role, operation))
    assert checked == {
        (role, operation)
        for operation, entry in manifest.operations.items()
        for role in entry.applies_to_roles
    }
    assert set(ROLE_DETAILS) == {
        "architect",
        "orchestrator",
        "manager",
        "worker",
        "curator",
        "reviewer",
        "consolidator",
        "investigator",
        "bootstrap",
    }
