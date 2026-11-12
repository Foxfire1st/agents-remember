"""Keep parent routing, refusal recovery and developer-answer provenance in each capsule."""

import re
import tempfile
from pathlib import Path
from typing import cast
from urllib.parse import quote

import pytest
import test_role_instruction_wording_handover as handover_wording
from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember.models.role_capsules.types import CapsuleRole
from agents_remember_test_support.testing.curation_doctrine import (
    normalize_statement,
    retired_statement_findings,
)
from agents_remember_test_support.testing.curator_contracts import compile_matrix
from agents_remember_test_support.testing.leaf_instruction_wording import (
    MESSAGE_HANDOVER_ACTIONS,
    MESSAGE_HANDOVER_SOURCE,
    delivered_step,
)
from test_role_instruction_wording import CANONICAL, LIFECYCLE, STARTERS, read, role_text

QUESTION_TRANSPORT_CLAUSES = (
    "Full-store recipient-busy is a refusal",
    "Record the handover as pending and continue independent work",
    "Resend once before the turn ends",
    "If it is refused again, report the second refusal and leave it pending",
    "A held receipt means queued eventual delivery, possibly many turns later. Never resend it",
    "Count a handover as taken only when the recipient's reply, report or intake names the exact object and shows the requested work or its start",
    "Open every supplied recipient artifact before you call one absent",
    "Withdraw a superseded held message with `withdraw_held_message_id`",
)
SHARED_QUESTION_AUDIENCE = (
    "Architect, Orchestrator, Manager, Worker, Curator, Reviewer, Investigator: "
)
SHARED_QUESTION_RULE = (
    "A held receipt means queued eventual delivery, possibly many turns later. Never resend it. "
    "Count a handover as taken only when the recipient's reply, report or intake names the exact "
    "object and shows the requested work or its start. Open every supplied recipient artifact "
    "before you call one absent. Withdraw a superseded held message with "
    "`withdraw_held_message_id`. A receiver does not poll."
)
CHILD_QUESTION_SOURCE = "reference/coordination/shared/child-questions.md"
CHILD_QUESTION_ACTIONS = {
    "architect": "Before answering a child's question or returning the developer's answer, read",
    "orchestrator": "Before handling a child's question or the developer's answer to it, read",
    "manager": "Before handling a child's question or returning the developer's answer, read",
}
ANSWER_CLAUSES = (
    "first check whether the decision falls within your authority",
    "If it does, answer and identify the answer as your own",
    "pass the question unchanged to your parent",
    "Name the originating role, task and agent identifier",
    "add your recommendation separately",
    "Without a parent, ask the developer in your own chat",
    "Send the developer's answer to the agent that sent the question",
    "it states that the developer gave the answer",
    "it names the agent identifier of the agent that received the answer from the developer",
    "it carries the developer's words in quotation marks",
    "Each parent on the way down passes those three items unchanged",
)

ORIENTATION_REFERRALS = {
    "orchestrator": (
        "Before sending a developer question or handling a relayed answer, "
        "read [developer questions](../operations/orientation.md#ask-for-a-developer-decision) "
        "for the reporting route through `role_message` on "
        "`agents-remember-task` and "
        "[relayed answers](../operations/orientation.md#recognize-a-relayed-developer-answer) "
        "for attribution and quoted words."
    ),
    "manager": "Before sending a developer question or recognizing a relayed answer, read [developer questions](../operations/orientation.md#ask-for-a-developer-decision) for the reporting route and [relayed answers](../operations/orientation.md#recognize-a-relayed-developer-answer) for attribution and quoted words.",
    "worker": (
        "Before asking a question, follow "
        "[Ask for a developer decision](../operations/orientation.md#ask-for-a-developer-decision) "
        "for the reporting recipient and parentless route: send questions "
        "requiring the developer's decision to your parent with `role_message` "
        "on `agents-remember-task`, or ask in your own chat when you have no "
        "parent."
    ),
    "reviewer": (
        "Before sending developer questions or handling relayed answers, read "
        "[developer-question routing](../operations/orientation.md#ask-for-a-developer-decision) "
        "for the question and answer procedure through `role_message` on "
        "`agents-remember-task`."
    ),
    "curator": (
        "Before sending a developer question or handling a relayed answer, "
        "read [developer questions](../operations/orientation.md#ask-for-a-developer-decision) "
        "for the reporting route through `role_message` on "
        "`agents-remember-task` and "
        "[relayed answers](../operations/orientation.md#recognize-a-relayed-developer-answer) "
        "for attribution and quoted words."
    ),
    "investigator": (
        "Before sending developer questions or handling relayed answers, read "
        "[developer-question routing](../operations/orientation.md#ask-for-a-developer-decision) "
        "for the question and answer procedure."
    ),
}
COORDINATION_REFERRALS = {
    "architect": "When `role_message` on `agents-remember-task` answers `held` because a child waits at a permission prompt, or its wait returns `permission-pending`, follow [Relay a child\N{RIGHT SINGLE QUOTATION MARK}s permission notice](../operations/coordination.md#relay-a-childs-permission-notice) for the upward route, child identity and supplied permission facts.",
    "orchestrator": "When `role_message` on `agents-remember-task` answers `held` because a child waits at a permission prompt, or its wait returns `permission-pending`, follow [Relay a child\N{RIGHT SINGLE QUOTATION MARK}s permission notice](../operations/coordination.md#relay-a-childs-permission-notice) for the upward route, child identity and supplied permission facts.",
    "manager": "When `role_message` on `agents-remember-task` returns `held` because a child waits at a permission prompt, or a wait returns `permission-pending`, follow [Relay a child\N{RIGHT SINGLE QUOTATION MARK}s permission notice](../operations/coordination.md#relay-a-childs-permission-notice) for the upward route, child identity and supplied permission facts.",
}


def held_question_transport(shared: str, role: str) -> str:
    """Require the held rule in its actual role-qualified shared paragraph."""
    assert role.capitalize() in SHARED_QUESTION_AUDIENCE.removesuffix(": ").split(", ")
    paragraph = SHARED_QUESTION_AUDIENCE + SHARED_QUESTION_RULE
    paragraphs = [" ".join(part.split()) for part in shared.split("\n\n")]
    assert paragraphs.count(paragraph) == 1, (role, "held shared paragraph")
    assert shared.count(SHARED_QUESTION_RULE) == 1, role
    return paragraph


def delivered_question_transport(role: str, detail: str) -> str:
    """Join only this capsule's shared held rule and emitted full-store detail."""
    root = CANONICAL / LIFECYCLE
    deliveries = [
        delivery
        for delivery in compile_matrix(root)
        if (delivery.role, delivery.operation) == (role, "orientation")
    ]
    assert len(deliveries) == 1, role
    shared = deliveries[0].shared_block
    assert shared is not None, role
    assert shared == (root / "core/every-role.md").read_text()
    assert SHARED_QUESTION_RULE in (root / "core/authority.md").read_text()
    paragraph = held_question_transport(shared, role)
    # Exercise actual shared delivery, not a fixture pretending the rule is in a detail.
    for clause in (*QUESTION_TRANSPORT_CLAUSES[4:], "A receiver does not poll."):
        mutant = shared.replace(clause, "", 1)
        assert mutant != shared, (role, clause)
        with pytest.raises(AssertionError):
            held_question_transport(mutant, role)
    for mutant in (
        # The audience label of this exact paragraph (other paragraphs share the label).
        shared.replace(
            SHARED_QUESTION_AUDIENCE + SHARED_QUESTION_RULE, "Bootstrap: " + SHARED_QUESTION_RULE, 1
        ),
        shared.replace(SHARED_QUESTION_RULE, SHARED_QUESTION_RULE + " One more.", 1),
        shared + "\n\n" + paragraph,
    ):
        assert mutant != shared
        with pytest.raises(AssertionError):
            held_question_transport(mutant, role)
    assert not missing_question_clauses(detail, QUESTION_TRANSPORT_CLAUSES[:4]), role
    assert not any(clause in detail for clause in QUESTION_TRANSPORT_CLAUSES[4:]), role
    return detail + "\n" + paragraph


def question_step(role: str, source: str, action: str) -> str:
    """Require the action's emitted owner and, for transport, its same-seat shared rule."""
    with tempfile.TemporaryDirectory() as directory:
        _, text = delivered_step(
            CANONICAL / LIFECYCLE,
            (cast(CapsuleRole, role), "orientation"),
            source,
            action,
            Path(directory) / "report.md",
        )
    if source == CHILD_QUESTION_SOURCE:
        text = answer_provenance(text)
    elif source == MESSAGE_HANDOVER_SOURCE:
        text = delivered_question_transport(role, text)
    return " ".join(text.split())


def answer_provenance(text: str) -> str:
    """Follow the delivered child's return action to its one attribution owner."""
    with shipped_composition_corpus() as (corpus, _manifest):
        owner = (corpus / "operations/orientation.md").resolve()
        target = quote(owner.as_posix(), safe="/:@") + "#recognize-a-relayed-developer-answer"
        pointer = (
            "Before composing that return, read [Recognize a relayed developer answer]("
            + target
            + ") for the three required attribution items."
        )
        assert " ".join(text.split()).count(pointer) == 1
        assert (
            owner.read_bytes() == (CANONICAL / LIFECYCLE / "operations/orientation.md").read_bytes()
        )
        section = re.search(
            r"(?ms)^## Recognize a relayed developer answer\n(.*?)(?=^## |\Z)",
            owner.read_text(),
        )
        assert section is not None
        return text + "\n" + section[1]


def missing_question_clauses(text: str, clauses: tuple[str, ...]) -> list[str]:
    return [clause for clause in clauses if clause not in text]


def test_each_parentable_role_has_question_recovery_and_answer_provenance() -> None:
    home = " ".join(read(LIFECYCLE / "operations" / "orientation.md").split())
    clauses = (
        "Send every question requiring the developer's decision to the reporting recipient your role instructs",
        "If your role names none, use the parent named in `host.parent`, addressed by its agent ID",
        "what you hold back until it is answered, and what you recommend and why",
        "do not end your turn on the question or hold a wait for the developer",
        "When neither exists, ask the developer in your own chat and end your turn",
        "**Pending developer questions** in your report",
        "Having no work left does not open your own chat",
        "Only when that recipient cannot be reached at all",
        "archived, not found, not resumable, no runtime configured, or host unreachable",
        "name the refusal and why the recipient cannot be reached",
        "Do not ask twice for an answer you already have",
        "Treat a relayed answer as the developer's instruction only when all three conditions hold",
        "it states that the developer gave the answer",
        "it names the agent identifier of the agent that received the answer from the developer",
        "it carries the developer's words in quotation marks",
        "record the relayed answer in the same place and form, with the quoted words",
        "Silence is no approval",
        "Only the developer can answer a harness permission prompt",
    )
    for clause in clauses:
        assert clause in home, ("orientation", clause)
    for role in ("orchestrator", "manager", "worker", "reviewer", "curator", "investigator"):
        transport = question_step(role, MESSAGE_HANDOVER_SOURCE, MESSAGE_HANDOVER_ACTIONS[role])
        assert not missing_question_clauses(transport, QUESTION_TRANSPORT_CLAUSES), role
        text = role_text(role)
        assert ORIENTATION_REFERRALS[role] in text, (role, "one-home pointer")
        assert (
            "Send every question requiring the developer's decision to the reporting recipient your role instructs"
            not in text
        ), (
            role,
            "no full doublet copy",
        )


def test_each_parent_has_upward_notice_and_unchanged_downward_answer_duties() -> None:
    home = " ".join(read(LIFECYCLE / "operations" / "coordination.md").split())
    notice = (
        "or a wait returns `permission-pending`, tell your parent immediately",
        "Without a parent, tell the developer in your own chat",
        "Name the child's role, task number, what the task does, agent identifier, and the permission as supplied",
        'or "not supplied" if none was named',
        "Each parent passes the notice upward unchanged",
        "tell the developer which chat to open",
        "Never give only a count",
        "Do not ask the developer to confirm an answered prompt",
    )
    for clause in notice:
        assert clause in home, ("coordination", clause)
    for role in STARTERS:
        text = role_text(role)
        assert COORDINATION_REFERRALS[role] in text, (role, "one-home referral")
        transport = question_step(role, MESSAGE_HANDOVER_SOURCE, MESSAGE_HANDOVER_ACTIONS[role])
        assert not missing_question_clauses(transport, QUESTION_TRANSPORT_CLAUSES), role
        assert "or a wait returns `permission-pending`, tell your parent immediately" not in text, (
            role,
            "no full notice copy",
        )
        answer = question_step(role, CHILD_QUESTION_SOURCE, CHILD_QUESTION_ACTIONS[role])
        assert not missing_question_clauses(answer, ANSWER_CLAUSES), role
        assert "first check whether the decision falls within your authority" not in text, (
            role,
            "no full questions copy",
        )


def test_question_step_checks_reject_each_actual_transport_or_answer_deletion() -> None:
    for source, step, clauses in (
        (MESSAGE_HANDOVER_SOURCE, MESSAGE_HANDOVER_ACTIONS["manager"], QUESTION_TRANSPORT_CLAUSES),
        (CHILD_QUESTION_SOURCE, CHILD_QUESTION_ACTIONS["manager"], ANSWER_CLAUSES),
    ):
        text = question_step("manager", source, step)
        assert not missing_question_clauses(text, clauses)
        if source == CHILD_QUESTION_SOURCE:
            for fragment in (
                "Before composing that return, read",
                "#recognize-a-relayed-developer-answer",
            ):
                mutant = text.replace(fragment, "")
                assert mutant != text
                with pytest.raises(AssertionError):
                    answer_provenance(mutant)
        for clause in clauses:
            mutant = text.replace(clause, "")
            assert mutant != text, (source, clause)
            assert clause in missing_question_clauses(mutant, clauses), (source, clause)


def test_question_operations_keep_conditional_routing_and_pending_recovery() -> None:
    home = " ".join(read(LIFECYCLE / "operations" / "orientation.md").split())
    for clause in (
        "Send every question requiring the developer's decision to the reporting recipient your role instructs",
        "If your role names none, use the parent named in `host.parent`, addressed by its agent ID",
        "When neither exists, ask the developer in your own chat",
        "**Pending developer questions** in your report",
        "Having no work left does not open your own chat",
        "Only when that recipient cannot be reached at all",
        "record the relayed answer in the same place and form, with the quoted words",
    ):
        assert clause in home, ("orientation", clause)
    for role, operation in (("reviewer", "review"), ("curator", "curation")):
        text = (
            role_text(role)
            + " "
            + " ".join(read(LIFECYCLE / "operations" / f"{operation}.md").split())
        )
        assert ORIENTATION_REFERRALS[role] in text, (operation, "one-home pointer")
        assert (
            "Send every question requiring the developer's decision to the reporting recipient your role instructs"
            not in text
        ), (
            operation,
            "no full doublet copy",
        )


def test_leaf_owner_relation_keeps_peer_results_and_named_parent_occasions() -> None:
    fixture = handover_wording.HandoverTextWordingTests()
    fixture.setUp()
    parent = StartingAgent("1f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "manager", "MASTER")
    try:
        for role in ("worker", "reviewer", "curator"):
            _, handover = fixture.compiled(role, parent)
            relation = handover["host"]["ownerRelation"]
            assert handover["host"]["parent"] == {
                "agentId": parent.agent_id,
                "role": parent.role,
                "task": parent.subject,
                "sprintDocumentRef": None,
                "masterDocumentRef": None,
                "taskDocumentRef": None,
                # A return goes to the parent's role with its task references, so a
                # leaf's return still reaches the role after a replacement (rule 10).
                "returnAddress": {
                    "role": "manager",
                    "sprint_document_ref": {"repository": "repo", "path": "sprint/task.json"},
                    "master_document_ref": {"repository": "repo", "path": "master/task.json"},
                },
            }
            assert f"Agent {parent.agent_id} ({parent.role} · {parent.subject})" in relation
            assert "developer's decision or a decision above the leaf" in relation
            assert "occasions assigned by your role" in relation
            assert "results and questions about another leaf seat's work go directly" in relation
            assert "leafSeats.roleMessageArguments" in relation
            assert "Send no routine result to the parent." in relation
            assert "and your result with role_message" not in relation
            assert set(handover["leafSeats"]["roleMessageArguments"]) == {
                "worker",
                "reviewer",
                "curator",
            } - {role}
    finally:
        fixture.doCleanups()


def test_exact_retired_directives_are_detected_as_independent_sentences() -> None:
    forwarding = "Do not forward role agents' messages or send a notice of a landing."
    own_chat = "Put every question for the developer in your own chat, as your reply in this session; the developer reads it there and answers there."
    exception = "As the exception, an Orchestrator or Manager started by another agent sends what needs the developer's decision to that parent with `role_message` on `agents-remember-task`, following its handover's developerQuestions rule, keeps working and does not end its turn on the question; this exception takes precedence over the own-chat sentence for those two roles with a parent."
    for source, sentence in (
        ("roles/manager.md", forwarding),
        ("roles/orchestrator.md", forwarding),
        ("SKILL.md", own_chat),
        ("SKILL.md", exception),
    ):
        path = LIFECYCLE / source
        current = read(path)
        assert not retired_statement_findings(normalize_statement(current), str(path))
        assert retired_statement_findings(normalize_statement(current + "\n" + sentence), str(path))
    assert not retired_statement_findings(
        normalize_statement(own_chat), str(LIFECYCLE / "roles/architect.md")
    )
