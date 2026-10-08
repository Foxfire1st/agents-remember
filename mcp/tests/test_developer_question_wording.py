"""Keep parent routing, refusal recovery and developer-answer provenance in each capsule."""

import test_role_instruction_wording as wording
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember_test_support.testing.curation_doctrine import (
    normalize_statement,
    retired_statement_findings,
)
from test_role_instruction_wording import LIFECYCLE, STARTERS, read, role_text


def test_each_parentable_role_has_question_recovery_and_answer_provenance() -> None:
    clauses = (
        "With a parent named in `host.parent`, send every question requiring the developer's decision",
        "addressed to its agent ID",
        "what you hold back until it is answered, and what you recommend and why",
        "do not end your turn on the question or hold a wait for the developer",
        "Without a parent, ask the developer in your own chat and end your turn",
        "If delivery to your parent is refused as busy or at a permission prompt",
        "**Pending developer questions** in your report",
        "send it again before ending your turn",
        "A second refusal leaves it pending",
        "Having no work left does not open your own chat",
        "Only when your parent cannot be reached at all",
        "archived, not found, not resumable, no runtime configured, or host unreachable",
        "name the refusal and why the parent cannot be reached",
        "do not ask twice for an answer you already have",
        "only when it states that the developer gave it",
        "names the agent ID of the agent that received it from the developer",
        "carries the developer's words in quotation marks",
        "record the relayed answer in the same place and form, with the quoted words",
        "Silence is no approval",
        "Only the developer can answer a harness permission prompt",
    )
    for role in ("orchestrator", "manager", "worker", "reviewer", "curator", "system-specialist"):
        text = role_text(role)
        for clause in clauses:
            assert clause in text, (role, clause)


def test_each_parent_has_upward_notice_and_unchanged_downward_answer_duties() -> None:
    clauses = (
        "answer it yourself if it lies within your authority and say that the answer is your own",
        "pass the question unchanged to your parent",
        "naming the originating role, task and agent ID",
        "add your own recommendation separately",
        "Without a parent, put it to the developer in your own chat",
        "Send the developer's answer to the agent that sent the question",
        "your agent ID as the agent that received it",
        "the developer's words in quotation marks",
        "Each parent on the way down passes these three unchanged",
        "its wait returns `permission-pending`, tell your parent at once",
        'or "not supplied" when none is named',
        "each parent passes that notice up unchanged",
        "tell the developer which chat to open",
        "never give only a count",
        "Do not ask the developer to confirm an answered prompt or keep resending to the child",
        "try your held message once in each turn you take for any reason",
        "give the parent that occasion when you next take a turn",
    )
    for role in STARTERS:
        text = role_text(role)
        for clause in clauses:
            assert clause in text, (role, clause)


def test_question_operations_keep_conditional_routing_and_pending_recovery() -> None:
    for operation in ("orientation", "review", "curation"):
        text = " ".join(read(LIFECYCLE / "operations" / f"{operation}.md").split())
        for clause in (
            "With a parent named in `host.parent`",
            "Without a parent, ask the developer in your own chat",
            "**Pending developer questions** in your report",
            "Having no work left does not open your own chat",
            "Only when your parent cannot be reached at all",
            "record the relayed answer in the same place and form, with the quoted words",
        ):
            assert clause in text, (operation, clause)


def test_leaf_owner_relation_keeps_peer_results_and_named_parent_occasions() -> None:
    fixture = wording.HandoverTextWordingTests()
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
