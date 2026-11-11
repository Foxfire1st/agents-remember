"""The skill trees, the forbidden strings and the file readers the wording cases share.

Split out of ``test_role_instruction_wording.py`` so the two halves of the check read on their
own: ``test_role_instruction_wording.py`` holds the instruction-file cases (the router, the role
files, their operations and the packaged copies), and
``test_role_instruction_wording_handover.py`` holds the handover text built in code and the
descriptions of the role tools. Both read the same canonical tree, the same forbidden list and
the same ``forbidden_in`` matcher, defined once here.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import get_args

from agents_remember.models.role_capsules.vocabulary import capsule_role_or_none
from agents_remember.models.role_identity import canonical_role
from agents_remember.models.role_launcher import LauncherRole
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember_test_support.testing.capsule_wording import (
    assert_memory_review_owner_link,
    instruction_owner,
)
from agents_remember_test_support.testing.curator_contracts import compile_matrix
from agents_remember_test_support.testing.leaf_instruction_wording import (
    MESSAGE_HANDOVER_ACTIONS,
    MESSAGE_HANDOVER_SOURCE,
    assert_emitted_detail_links,
    delivered_step,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = Path("l-01-agent-lifecycles")
CANONICAL = REPOSITORY_ROOT / "skills"
# The copy the capsule compiler reads; a rewrite is not live until the sync has run.
PACKAGED = (
    REPOSITORY_ROOT / "mcp" / "src" / "agents_remember" / "package_data" / "runtime" / "skills"
)

# PNT-R06 item 12, searched case-insensitively.
FORBIDDEN = (
    "orca",
    "native Run",
    "Run/Task/Dispatch",
    "Dispatch worker",
    "worker-start",
    "run-create",
    "worker_done",
    "dispatch_agent",
    "message_parent",
    "message_child",
    # MIK-R113 rule 10: the other removed tools, the seat variables of the earlier way and the
    # removed argument of worktree_attach. No role text tells an agent to use a removed part.
    "rename_child",
    "rename_self",
    "lifecycle_start",
    "lifecycle_resume",
    "lifecycle_turn_end_notification",
    "lifecycle_end",
    "switch_lifecycle",
    "lifecycle_phase",
    "lifecycle_gate",
    "gate_decide",
    "gate_list",
    "AR_SPAWN_ROLE",
    "AR_HOSTED_SESSION_ID",
    "on_unsaved",
)
# The report directory of a role, as a segment of the report and artifact paths in a handover. It
# carried the previous host's name until the rename leaf (PNT-R08); the search below now reads
# the whole text, this segment included.
REPORT_DIRECTORY = "/role-launch/"

TOOL_SERVER = "agents-remember-task"
OTHER_INSTALLATION = "agents-remember"
LAUNCHER_ROLES: tuple[str, ...] = tuple(
    dict.fromkeys(canonical_role(role) for role in get_args(LauncherRole))
)
STARTERS = ("architect", "orchestrator", "manager")
_NAMED_TOOL = re.compile("`(" + "|".join(sorted(map(re.escape, PUBLIC_TOOLS))) + ")`")


def forbidden_in(text: str) -> list[str]:
    """The forbidden strings a text contains, in the packet's spelling."""

    folded = text.casefold()
    return [word for word in FORBIDDEN if word.casefold() in folded]


def instruction_files() -> list[Path]:
    """The router, the launcher roles' files and their operations, relative to a skill tree."""

    manifest = json.loads((CANONICAL / LIFECYCLE / "composition-manifest.json").read_text("utf-8"))
    roles = [manifest["roles"][role]["file"] for role in LAUNCHER_ROLES]
    operations = [
        entry["source"]
        for entry in manifest["operations"].values()
        if set(entry["applies_to_roles"]) & set(LAUNCHER_ROLES)
    ]
    return [LIFECYCLE / name for name in ("SKILL.md", *roles, *sorted(operations))]


def read(relative: Path) -> str:
    return (CANONICAL / relative).read_text(encoding="utf-8")


def role_text(role: str) -> str:
    return " ".join(read(LIFECYCLE / "roles" / f"{role}.md").split())


def assert_message_handover_delivery(roles: tuple[str, ...]) -> None:
    """Rules44/47 and BA3: exercise each sender's real launch-delivered step."""
    shared_by_role = {
        delivery.role: delivery.shared_block
        for delivery in compile_matrix(CANONICAL / LIFECYCLE)
        if delivery.operation == "orientation"
    }
    shared_clause = (
        "A held receipt means queued eventual delivery, possibly many turns later. Never resend it. "
        "Count a handover as taken only when the recipient's reply, report or intake names the exact "
        "object and shows the requested work or its start. Open every supplied recipient artifact "
        "before you call one absent. Withdraw a superseded held message with "
        "`withdraw_held_message_id`. A receiver does not poll."
    )
    core = (CANONICAL / LIFECYCLE / "core/authority.md").read_text()
    assert shared_clause in core
    for role_name in roles:
        owner = instruction_owner(CANONICAL / LIFECYCLE, role_name)
        role = capsule_role_or_none(owner)
        assert role is not None, role_name
        pointer = "{{detail:" + MESSAGE_HANDOVER_SOURCE + "}}"
        if role == "bootstrap":
            assert pointer not in role_text(role), role
            continue
        assert pointer in role_text(role), role
        shared = shared_by_role[role]
        assert shared is not None
        assert shared == (CANONICAL / LIFECYCLE / "core/every-role.md").read_text()
        assert shared.count(shared_clause) == 1, role
        for mutant in (shared.replace(shared_clause, "", 1), shared + "\n" + shared_clause):
            with unittest.TestCase().assertRaises(AssertionError):
                assert mutant.count(shared_clause) == 1
        with tempfile.TemporaryDirectory() as directory:
            _, held = delivered_step(
                CANONICAL / LIFECYCLE,
                (role, "orientation"),
                MESSAGE_HANDOVER_SOURCE,
                MESSAGE_HANDOVER_ACTIONS[role],
                Path(directory) / "report.md",
            )
        for clause in (
            "Full-store recipient-busy is a refusal:",
            "1. Record the handover as pending and continue independent work.",
            "2. Resend once before the turn ends.",
            "3. If it is refused again, report the second refusal and leave it pending.",
            # Replaced A004's "Keep noncircular Manager/parent routes and source-specific
            # fields." in message-handover.md:19 (P5-15 wording); its successor stays pinned.
            "Report the second refusal through the route named by your role, using its "
            "recipient and required notice fields.",
            "Never address the notice to yourself.",
            "A receipt or turn-end does not establish semantic acceptance.",
            "Messages are filed in the narrowest task shared by sender and recipient:",
            "- same-leaf messages: `notes/messages/<leaf-slug>/`;",
            "- shared-master or shared-sprint messages: that task's `notes/messages/`.",
            "The active enclosure holds its own receipts. Otherwise, receipts use the configured `transcriptRoot`.",
            "Before claiming that no message was sent, search the complete applicable message folder and receipt scope.",
        ):
            assert clause in held, (role, clause)


# Actual owners reached by the role/action assertions in the existing wording case.
RELOCATED_WORKING_STEPS = (
    (
        "roles/manager.md",
        "Start leaf agents and preserve their work",
        (
            "Check packet dependencies before starting a leaf and before integrating it; "
            "the product does not perform that check.",
        ),
    ),
    (
        "operations/closeout.md",
        "Repair a gate refusal through its owner",
        (
            "A gate refusal prevents publication and names the findings. Closeout and integration "
            "report `knowledge-gate-refused`; direct landing reports "
            "`direct-landing-knowledge-gate-refused`.",
            "Send the findings to the Curator for repair, then retry the owning route on the "
            "corrected pair. Bypass no gate.",
        ),
    ),
    (
        "operations/implementation.md",
        "Boundaries",
        (
            "Fix every measured defect within the task's aim.",
            "When one small item costs about an hour, a second repair round or a second freeze, tell "
            "the Manager immediately.",
            "Give the hours and a proposal to carry or bound its complete repair, then continue the "
            "rest.",
            "Dropping applies only to nondefect polish.",
        ),
    ),
    (
        "operations/orientation.md",
        "Recognize a relayed developer answer",
        (
            "Treat a relayed answer as the developer's instruction only when all three conditions "
            "hold:",
            "it states that the developer gave the answer;",
            "it names the agent identifier of the agent that received the answer from the developer;",
            "it carries the developer's words in quotation marks.",
            "Otherwise it is the sending agent's own word.",
            "Silence is no approval.",
            "An agent stopped at a prompt cannot relay it.",
        ),
    ),
)


def assert_detail_link_provenance_controls() -> None:
    """A repeated ordinary label is valid; every marker occurrence remains exact."""
    source = "reference/message-handover.md"
    path = Path("/fixture/busy retry/detail.md")
    compiled = (
        "[Notify](../reference/message-handover.md#notify) "
        "[Notify]({{detail:reference/message-handover.md}}#notify)"
    )
    ordinary = "[Notify](/fixture/busy%20retry/detail.md#notify)"
    marker = "[Notify](/fixture/busy%20retry/detail.md#notify)"
    instructions = ordinary + " " + marker
    assert_emitted_detail_links(instructions, compiled, source, path)
    for mutant in (
        ordinary,
        instructions + " " + marker,
        ordinary + " [Notify](/fixture/wrong/detail.md#notify)",
        ordinary + " [Notify](/fixture/busy%20retry/detail.md#wrong)",
        ordinary + " [Other](/fixture/busy%20retry/detail.md#notify)",
        ordinary + " [Notify](https://example.test/fixture/busy%20retry/detail.md#notify)",
        ordinary + " [Notify](/fixture/busy%20retry/detail.md?query#notify)",
    ):
        assert mutant != instructions
        with unittest.TestCase().assertRaises(AssertionError):
            assert_emitted_detail_links(mutant, compiled, source, path)


COORDINATION_ACTION_STEPS = (
    (
        "roles/architect.md",
        "Establish the work",
        (
            "Before starting a role and delivering its first intake, read [Start one canonical role "
            "assignment](../operations/coordination.md#start-one-canonical-role-assignment) for the "
            "permitted starters of `role_start` and [Start leaf stages and deliver the first "
            "intake](../operations/coordination.md#start-leaf-stages-and-deliver-the-first-intake)",
        ),
    ),
    (
        "roles/manager.md",
        "Start leaf agents and preserve their work",
        (
            "Before starting and coordinating the leaf, read [leaf-stage coordination]"
            "(../operations/coordination.md#start-leaf-stages-and-deliver-the-first-intake) "
            "for start timing and intake",
        ),
    ),
    (
        "operations/coordination.md",
        "Start one canonical role assignment",
        (
            "For an uncertain start, follow [Start and configure role agents]"
            "(../core/every-role.md#start-and-configure-role-agents) for same-identifier "
            "reconciliation and refusal reporting.",
        ),
    ),
    (
        "operations/coordination.md",
        "Start leaf stages and deliver the first intake",
        (
            "Before sending or answering a Curator's code concern, read [Curation failure "
            "handling](curation.md#failure-handling) for both recipients, post-`PASS` handling, the "
            "finding and answer, and disagreement routing.",
        ),
    ),
    (
        "operations/curation.md",
        "Failure handling",
        (
            "Send evidenced code concerns to both Worker and Reviewer, including after passing code "
            "review. Show the requirement or report mismatch.",
            "Repair no code and author no knowledge claiming behavior the code lacks.",
            "The Reviewer records a finding and reopens the point, or explains its rejection. The "
            "Curator records the answer.",
            "Report the finding and answer; send unresolved disagreement to the Manager.",
        ),
    ),
    (
        "roles/curator.md",
        "Reconcile, author and check",
        (
            "Before sending a memory candidate for review or returning a memory repair, read [memory handover]({{detail:reference/curator/memory-handover.md}})",
            "Reuse it when returning a repair.",
        ),
    ),
    (
        "reference/curator/memory-handover.md",
        "Send the whole requested review or repair",
        (
            "Before sending a memory candidate for review, read [Review "
            "boundaries](../../operations/review.md#boundaries) for separate reports, finding "
            "identifiers, pass count, code-state isolation and recorded extra-pass approval.",
        ),
    ),
)


def assert_nonstarter_role_boundary(
    role: str,
    text: str,
    check_step: Callable[[str, str, tuple[str, ...]], None],
) -> None:
    """Require the same seat's standing restriction and its pre-action pointer."""
    if role == "reviewer":
        check_step(
            "roles/reviewer.md",
            "Messages and working files",
            # P5-18: the restriction stands in the role file; review.md:114 is the one pointer.
            (
                "Starting an agent makes the starter its parent, and a checked agent cannot start its checker.",
            ),
        )
        check_step(
            "roles/reviewer.md",
            "Permitted work and boundaries",
            (
                "Never implement or edit code, rewrite a requirement, edit a Worker record, write onboarding, record AR task acceptance, start any role, publish Git, decide a gate or absorb another seat's work",
            ),
        )
    else:
        unittest.TestCase().assertRegex(
            text,
            r"(?:A (?:Worker|Reviewer|Curator)|An Investigator) starts no role"
            r"|Start no role\.|Bootstrap starts no role",
        )


def assert_memory_review_owner_link_controls() -> None:
    """Reject a missing action, wrong owner/fragment and repeated owner link."""
    owner = Path("/fixture/shipped/operations/review.md")
    action = "Before sending a memory candidate for review, read "
    link = "[Review boundaries](/fixture/shipped/operations/review.md#boundaries)"
    handover = action + link
    assert_memory_review_owner_link(handover, owner)
    for mutant in (
        link,
        handover.replace("read ", ""),
        handover.replace("operations/review.md", "operations/curation.md"),
        handover.replace("#boundaries", "#messages"),
        handover.replace("#boundaries", ""),
        handover + " " + link,
        handover + " " + handover,
    ):
        assert mutant != handover
        with unittest.TestCase().assertRaises(AssertionError):
            assert_memory_review_owner_link(mutant, owner)
