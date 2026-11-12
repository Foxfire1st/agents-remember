"""Current affirmative clauses and explicit whole-obligation equivalence fixtures.

Each alternative must express the whole named obligation. Separate conditions are
kept together, so deleting any condition cannot leave a weaker alternative passing.
The four equivalent phrasings below exercise existing positive controls; they
retain the full obligation and do not admit historical source versions.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.cli.role_instruction_details import compose_launch_instructions
from agents_remember.models.role_capsules.manifest import parse_composition_manifest
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleOperation,
    CapsuleRole,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.serving.capsule_delivery import capsule_delivery_from


def assert_action_pointer(instructions: str, source: str, action: str) -> None:
    """Require one pointer at the named action, independently of metadata prose."""
    assert action.strip(), "an expected action is required"
    marker = "{{detail:" + source + "}}"
    pattern = re.compile(
        re.escape(normalized(action)) + r"(?:(?!\{\{detail:).)*?" + re.escape(marker)
    )
    matches = [
        match
        for paragraph in instructions.split("\n\n")
        for match in pattern.finditer(normalized(paragraph))
    ]
    assert len(matches) == 1, (source, action, "missing or repeated action pointer")


def assert_emitted_detail_links(instructions: str, compiled: str, source: str, path: Path) -> None:
    """Check each marker's label occurrence, emitted path, suffix and exact count."""
    marker = "{{detail:" + source + "}}"
    links = re.findall(r"\[([^]\n]+)\]\(([^)\n]+)\)", compiled)
    emitted = re.findall(r"\[([^]\n]+)\]\(([^)\n]+)\)", instructions)
    labels = {label for label, target in links if target.startswith(marker)}
    assert labels, (source, "no compiled detail links")
    for label in labels:
        original = [target for name, target in links if name == label]
        actual = [target for name, target in emitted if name == label]
        assert len(actual) == len(original), (source, label, original, actual)
        for target, resolved in zip(original, actual, strict=True):
            if not target.startswith(marker):
                continue
            suffix = target.removeprefix(marker)
            expected = urlsplit(path.as_posix() + suffix)
            parts = urlsplit(resolved)
            assert not parts.scheme and not parts.netloc and not parts.query, resolved
            assert unquote(parts.path) == path.as_posix(), resolved
            assert parts.fragment == expected.fragment, (source, target, resolved)


def delivered_step(
    root: Path,
    role_operation: tuple[CapsuleRole, CapsuleOperation],
    source: str,
    action: str,
    report_path: Path,
) -> tuple[str, str]:
    """Read one declared detail through real admission, compilation and launch delivery.

    Return the resolved instructions and that exact delivered file, not a concatenation
    of references which could hide missing role declarations or broken step links.
    """
    role, operation = role_operation
    manifest = (root / "composition-manifest.json").read_bytes()
    parsed = parse_composition_manifest(manifest)
    binding = CapsuleBinding(
        operation=operation,
        admitted=CapsuleAdmittedFacts(
            task_reference="fixture/leaf.json",
            task_document_digest=compute_content_digest(b'{"id":"leaf"}'),
            seat=CapsuleRoleSeat(role, parsed.roles[role].altitude),
            repository_id="fixture",
            work_branch="fixture",
            tool_policy=CapsuleToolPolicy(granted=frozenset(PUBLIC_TOOLS)),
        ),
    )
    admission = routed_admission_for(
        root, "composition-manifest.json", manifest, CapsuleSeatAddress(role, operation)
    )
    outcome = compile_admitted_capsule(binding, admission)
    assert outcome.ok, outcome.render_explanation()
    assert outcome.result is not None
    delivery = capsule_delivery_from(outcome.result)
    references = [entry for entry in delivery.detail_references if entry.source_path == source]
    assert len(references) == 1, f"{role}/{operation}: missing or duplicate {source}"
    reference = references[0]
    assert reference.step.strip()
    assert_action_pointer(outcome.result.render_instructions(), source, action)
    assert reference.content == (root / source).read_bytes()
    assert reference.revision == compute_content_digest(reference.content)
    instructions, record = compose_launch_instructions(delivery, str(report_path), {})
    entries = [entry for entry in record["detailReferences"] if entry["sourcePath"] == source]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["step"] == reference.step
    path = Path(entry["path"])
    assert_emitted_detail_links(instructions, outcome.result.render_instructions(), source, path)
    assert "{{detail:" not in instructions
    emitted = path.read_bytes()
    assert entry["sizeBytes"] == len(emitted)
    assert entry["revision"] == compute_content_digest(emitted)
    return instructions, emitted.decode("utf-8")


# These action contracts are independent of manifest step descriptions.
MESSAGE_HANDOVER_SOURCE = "reference/message-handover.md"
MESSAGE_HANDOVER_ACTIONS = {
    "architect": "Before handing work to a waiting seat, read",
    "orchestrator": "Before handing work to a waiting seat, read",
    "manager": "Before handing work to a waiting seat, read",
    "worker": "Before sending a handover, read",
    "reviewer": "Before handing over work, read",
    "curator": "Before sending a message, read",
    "investigator": "Before handing over work, read",
}
# P5-18: one refused-write pointer per first message. The shared every-role paragraph
# links the served file for every role; only the Manager's capsule also carries the
# detail, for its separate task retirement and movement action.
REFUSED_WRITE_ACTIONS = {
    "manager": "Before retiring or moving a task, read",
}
SERVED_REFUSED_WRITE = (
    "skill://agents-remember/skills/l-01-agent-lifecycles/reference/refused-write.md"
)

ROLES = (
    "architect",
    "bootstrap",
    "curator",
    "manager",
    "orchestrator",
    "reviewer",
    "investigator",
    "worker",
)


def normalized(text: str) -> str:
    return " ".join(text.replace("*", "").replace("`", "").split())


def missing_clauses(text: str, clauses: dict[str, tuple[str, ...]]) -> list[str]:
    reading = normalized(text)
    return [
        name
        for name, alternatives in clauses.items()
        if not any(normalized(clause) in reading for clause in alternatives)
    ]


def forbidden_autonomy_clauses(role: str, text: str) -> list[str]:
    """Catch retired helper restrictions without rejecting seat-owned mutating acts.

    The setup-surface rule is Bootstrap-specific: its permitted read-only status calls
    can be delegated. Other seats do not acquire Bootstrap's setup permissions.
    """
    reading = normalized(text).casefold()
    checks = {
        "read/search-only helpers": r"\bsub-agents(?:\s+for)?\s*[\u2014:\u2013-]?\s*read/search[- ]only\b",
    }
    if role == "bootstrap":
        checks["blanket setup-surface prohibition"] = r"\bno sub-agent runs a setup surface\b"
    return [name for name, pattern in checks.items() if re.search(pattern, reading)]


AUTONOMY_CLAUSES: dict[str, dict[str, tuple[str, ...]]] = {
    "architect": {
        "affirmative freedom": (
            "Organise your own work inside the assignment, with "
            "whatever your harness offers, at any size.",
        ),
        "own split": ("You decide the split; the roles order work at task boundaries.",),
        "seat checks credits and answers": (
            "You answer for all of it: check the helpers' "
            "work, report which parts they did, and hand it "
            "over under your own name.",
        ),
        "seat boundary acts": (
            "Perform every boundary act yourself: a message to another "
            "seat, a task record or a product operation changing leaf "
            "state.",
        ),
        "helper seat role and scope": (
            "A harness sub-agent holds no AR role or seat and "
            "does not start a role. It has the same working "
            "folder, permissions and assignment.",
        ),
        "independent review": (
            "A helper cannot provide independent review of its author: "
            "code and memory changes still require the independent "
            "Reviewer.",
        ),
        "no harness dependency": ("A harness without sub-agents can do all the same work.",),
    },
    "orchestrator": {
        "affirmative freedom": (
            "Organise your own work inside the assignment, with "
            "whatever your harness offers, at any size.",
        ),
        "own split": ("You decide the split; the roles order work at task boundaries.",),
        "seat checks credits and answers": (
            "Check your helpers' work, report which "
            "parts they did, and hand it over under your "
            "own name.",
        ),
        "seat boundary acts": (
            "Every boundary act is yours: a message to another seat, "
            "a task record or a product operation changing leaf "
            "state.",
        ),
        "helper seat role and scope": (
            "A harness sub-agent holds no AR role or seat and "
            "does not start a role. It has the same working "
            "folder, permissions and assignment.",
        ),
        "independent review": (
            "Nobody checks itself through a sub-agent: code and "
            "memory changes still require the independent Reviewer, "
            "never their author's helper.",
        ),
        "no harness dependency": ("A harness without sub-agents can do all the same work.",),
    },
    "worker": {
        "affirmative freedom": (
            "Organise work inside the assignment with whatever your harness offers, at any size.",
        ),
        "own split": ("You decide the split; roles order work at task boundaries.",),
        "seat checks credits and answers": (
            "You answer for all helper work: check it, report "
            "each helper's contribution and hand it over under "
            "your own name.",
        ),
        "seat boundary acts": (
            "Every boundary act is yours, including messages to another "
            "seat, task records and product operations that change leaf "
            "state.",
        ),
        "helper seat role and scope": (
            "A harness sub-agent holds no AR seat, starts no role, "
            "and shares your working folder, permissions and "
            "assignment.",
            "A harness sub-agent holds no AR seat, does not start a role, and shares "
            "your working folder, permissions and assignment.",
        ),
        "independent review": (
            "It cannot independently review its author's work: code and "
            "memory changes still require the independent Reviewer.",
        ),
        "no harness dependency": ("A harness without sub-agents can do all the same work.",),
    },
    "reviewer": {
        "affirmative freedom": (
            "Organise work at any size with the harness facilities available.",
        ),
        "own split": (
            "Choose the split, check helpers' work, report their parts and hand "
            "over under your name.",
        ),
        "seat checks credits and answers": (
            "Choose the split, check helpers' work, report "
            "their parts and hand over under your name.",
        ),
        "seat boundary acts": (
            "Every boundary act stays yours: inter-seat messages, task "
            "records and product operations changing leaf state.",
        ),
        "helper seat role and scope": (
            "Helpers hold no Agents Remember role or seat, start "
            "no role, and share the working folder, permissions "
            "and assignment.",
        ),
        "independent review": (
            "An author's helper cannot provide its independent code or memory review.",
        ),
        "no harness dependency": ("A harness without helpers can do the same work.",),
    },
    "curator": {
        "affirmative freedom": (
            "Use any available harness facilities at any size, with no "
            "helper-number or depth limit.",
        ),
        "own split": (
            "Use any available harness facilities at any size, with no "
            "helper-number or depth limit.",
        ),
        "seat checks credits and answers": (
            "Verify helpers' work, report their parts and hand over under your own name.",
        ),
        "seat boundary acts": (
            "You own inter-seat messages, task records and operations that change leaf state.",
        ),
        "helper seat role and scope": (
            "Helpers share your assignment, folder and "
            "permissions. They hold no Agents Remember role or "
            "seat and start none.",
        ),
        "independent review": (
            "A helper cannot provide independent review of its author's "
            "work; the Reviewer retains memory-candidate review.",
        ),
        "no harness dependency": ("A harness without helpers can perform the same work.",),
        "one curator writer": (
            "Keep exactly one Curator writer through the admitted route, "
            "never a helper writer lane.",
        ),
    },
    "manager": {
        "affirmative freedom": (
            "Organize work inside your assignment with the facilities "
            "your harness offers, at any size.",
        ),
        "own split": ("You decide the split; roles order work at task boundaries.",),
        "seat checks credits and answers": (
            "Check helpers' work, credit their parts and hand over under your own name.",
        ),
        "seat boundary acts": (
            "Every boundary act is yours: messages to other seats, task "
            "records and product operations that change leaf state.",
        ),
        "helper seat role and scope": (
            "A harness sub-agent has no AR role or seat and starts "
            "no role. It shares your working folder, permissions "
            "and assignment.",
            "A harness sub-agent has no AR role or seat and does not start a role. It "
            "shares your working folder, permissions and assignment.",
        ),
        "independent review": (
            "It cannot independently review your work; code and memory "
            "changes still require the independent Reviewer.",
        ),
        "no harness dependency": ("A harness without sub-agents can do the same work.",),
    },
    "investigator": {
        "affirmative freedom": (
            "Organise work inside your assignment with whatever your harness offers, at any size.",
        ),
        "own split": ("You decide the split; the roles order work at task boundaries.",),
        "seat checks credits and answers": (
            "You answer for all of it: check helpers' "
            "work, report which parts they did and hand "
            "it over under your own name.",
        ),
        "seat boundary acts": (
            "You perform every boundary act yourself: a message to "
            "another seat, a task record or a product operation "
            "changing leaf state.",
        ),
        "helper seat role and scope": (
            "A harness sub-agent holds no role seat in Agents "
            "Remember, starts no role and has the same "
            "working folder, permissions and assignment.",
            "A harness sub-agent holds no role seat in Agents Remember, does not start "
            "a role and has the same working folder, permissions and assignment.",
        ),
        "independent review": (
            "Nobody checks itself through a sub-agent. Code and "
            "memory changes still require the independent Reviewer, "
            "never their author's helper.",
        ),
        "no harness dependency": ("A harness without sub-agents can do the same work.",),
    },
    "bootstrap": {
        "affirmative freedom": (
            "Use harness helpers within this assignment and its "
            "permissions, including permitted read-only setup-status "
            "queries.",
        ),
        "own split": (
            "You decide the split, at any size, and answer for all their work: "
            "check it, credit their parts in your report and hand over under your "
            "own name. Roles order work at task boundaries.",
        ),
        "seat checks credits and answers": (
            "answer for all their work: check it, credit "
            "their parts in your report and hand over under "
            "your own name.",
        ),
        "seat boundary acts": (
            "Boundary acts also remain yours: a message to another seat, "
            "a task record or a product operation changing leaf state. "
            "This ownership grants none of the acts prohibited above.",
        ),
        "helper seat role and scope": (
            "Helpers share your working folder, permissions and "
            "assignment. They hold no Agents Remember seat and "
            "start no role.",
        ),
        "independent review": (
            "A helper cannot independently review its author. Code and "
            "memory changes still require the independent Reviewer, "
            "never the author's helper.",
        ),
        "no harness dependency": ("A harness without helpers can do the same work.",),
    },
}


def autonomy_clauses(role: str) -> dict[str, tuple[str, ...]]:
    """Return the role's current clauses and explicit whole-obligation equivalents."""
    return AUTONOMY_CLAUSES[role]


IMPLEMENTATION_CLAUSES = {
    "freeze to reviewer": (
        "1. Freeze the candidate and tell the Manager to start the Reviewer.\n"
        "2. Once the Reviewer exists, send it the freeze directly with "
        "`role_message` on `agents-remember-task`. Name the leaf, the report path "
        "and the whole next stretch of work. The report names the head and change "
        "hash.",
    ),
    "identity and whole stretch": (
        "2. Once the Reviewer exists, send it the freeze directly with "
        "`role_message` on `agents-remember-task`. Name the leaf, the "
        "report path and the whole next stretch of work. The report names "
        "the head and change hash.",
    ),
    "repair return": (
        "3. Repair sealed findings and return a new freeze directly to the same Reviewer.",
    ),
    "producer to curator": (
        "4. After code review returns `PASS` and the Manager has started the "
        "Curator, send the Curator your hand-off list for that freeze directly. "
        "Name the same entries and changed paths so the changed-memory pass reads "
        "them.",
    ),
    "factual independence": ("This is a factual handover, not a review brief.",),
    "upward notice recovery": (
        "For a Worker, Curator or Reviewer whose required Manager "
        "notice fails delivery, record it as pending, "
        "continue independent work and retry the notice "
        "through `role_message` on `agents-remember-task`. "
        "Do not turn that delivery failure into a question "
        "to the developer in your own chat.",
    ),
    "busy retry": (
        "Full-store recipient-busy is a refusal:\n"
        "\n"
        "1. Record the handover as pending and continue independent work.\n"
        "2. Resend once before the turn ends.\n"
        "3. If it is refused again, report the second refusal and leave it pending.",
    ),
}


REVIEW_CLAUSES = {
    "verdict to worker": (
        "2. Send the verdict directly to the Worker through `role_message` on "
        "`agents-remember-task`. The message names the verdict file; it never "
        "pastes the text. A block names the sealed-findings report and requests the "
        "whole repair stretch.",
    ),
    "sealed whole repair": (
        "A block names the sealed-findings report and requests the whole repair stretch.",
    ),
    "pass to curator": (
        "1. On code `PASS`, first tell the Manager to start the Curator, before peer "
        "handover.\n"
        "2. Send the verdict directly to the Worker through `role_message` on "
        "`agents-remember-task`. The message names the verdict file; it never pastes "
        "the text. A block names the sealed-findings report and requests the whole "
        "repair stretch.\n"
        "3. Once the Curator exists, send it the passed freeze and code-`PASS` "
        "verdict directly.",
    ),
    "memory verdict to curator": (
        "4. Review the Curator's memory change and return findings or `PASS` directly to it.",
    ),
    "identity and whole stretch": (
        "Every handover names the leaf, exact object, report or verdict "
        "path, and whole next stretch. Identify a code candidate by the "
        "head and change hash in the Worker's report, or identify the "
        "exact memory change. Use the sibling role and exact leaf task "
        "references from the handover, preserving product-supplied "
        "identifiers whole and unchanged.",
    ),
    "own code begin": (
        "The Reviewer opens each ordinary leaf code round with "
        '`task_doc(operation="begin_review")` and records its own verdict through '
        "`record_review` or the applicable `record_route_review`. Include any "
        "preceding result required before the next begin.",
    ),
    "own code result": (
        "records its own verdict through `record_review` or the applicable `record_route_review`.",
    ),
    "preceding result": ("Include any preceding result required before the next begin.",),
    "manager acceptance": (
        "The Manager retains gate acceptance and records the Architect's or developer's "
        "extra-round approval.",
    ),
    "memory leaves code untouched": (
        "Code state stays unchanged. Memory review "
        "opens no code review-state round, resets no "
        "passed code review, code verdict or code "
        "baseline, and adds no identifiers to that "
        "baseline.",
    ),
    "memory extra approval": (
        "A pass beyond the settings limit needs the "
        "Architect's or developer's instruction recorded by "
        "the Manager.",
    ),
    "post-pass concern": (
        "Send evidenced code concerns to both Worker and "
        "Reviewer, including after passing code review. Show the "
        "requirement or report mismatch. Repair no code and "
        "author no knowledge claiming behavior the code lacks. "
        "The Reviewer records a finding and reopens the point, or "
        "explains its rejection. The Curator records the answer. "
        "Report the finding and answer; send unresolved "
        "disagreement to the Manager.",
    ),
    "reopen or refute answer": (
        (
            "Two routes may add a finding after the list is sealed:\n"
            "\n"
            "- a valid code finding from the Curator;\n"
            "- a valid finding from an advisory review the Architect commissioned and "
            "forwarded in its questions file.\n"
            "\n"
            "For both routes, add a new sealed finding with its own identifier and reopen "
            "the verdict for that point only. Earlier sealed findings stay as they are. "
            "Give the reason for an invalid finding. The Worker repairs an accepted finding"
            " in a successor.\n"
            "\n"
            "A repair round for a Curator finding counts under the settings limit. Beyond "
            "it, send the extra-round question to the instructed reporting recipient where "
            "your role has one; otherwise send it through your parent, or ask in your own "
            "chat when you have no parent. An extra round for advisory findings is the "
            "Architect's grant. Every other new observation follows your role's "
            "developer-question route."
        ),
    ),
    "upward notice recovery": (
        "Report non-work replies and empty or doubled-seat refusals to the Manager "
        "once, with the seat and quoted text. Never pick a candidate yourself. When "
        "the Manager gives the occasion, send the handover yourself; it relays none "
        "of the contents.\n"
        "\n"
        "For a Worker, Curator or Reviewer whose required Manager notice fails "
        "delivery, record it as pending, continue independent work and retry the "
        "notice through `role_message` on `agents-remember-task`. Do not turn that "
        "delivery failure into a question to the developer in your own chat.",
    ),
    "memory extra approval authority": (
        "The Manager retains gate acceptance and records the Architect's or developer's "
        "extra-round approval.\n"
        "\n"
        "Memory review uses separate sealed "
        "reports, finding identifiers and its own "
        "pass count. The Reviewer records its own "
        "lane with `begin_review` and "
        "`record_review`, using "
        "`reviewKind=memory`. Code state stays "
        "unchanged. Memory review opens no code "
        "review-state round, resets no passed code "
        "review, code verdict or code baseline, and "
        "adds no identifiers to that baseline. Its "
        "ordinary pass limit is the same as code "
        "review's. A pass beyond the settings limit "
        "needs the Architect's or developer's "
        "instruction recorded by the Manager.",
    ),
    "busy retry": (
        "Full-store recipient-busy is a refusal:\n"
        "\n"
        "1. Record the handover as pending and continue independent work.\n"
        "2. Resend once before the turn ends.\n"
        "3. If it is refused again, report the second refusal and leave it pending.",
    ),
}


CURATION_CLAUSES = {
    "memory to reviewer": (
        "For an Agents Remember leaf, send the memory candidate directly to the "
        "Reviewer through `role_message` on `agents-remember-task`, using the "
        "handover's role and task arguments.",
    ),
    "identity and whole stretch": (
        "Each handover names the leaf, the Worker's exact code head and "
        "change hash, the memory candidate's identity, the report or "
        "verdict path, and the whole next stretch requested.",
    ),
    "post-pass code finding": (
        "Send evidenced code concerns to both Worker and Reviewer, including "
        "after passing code review. Show the requirement or report mismatch.",
    ),
    "code answer": (
        "The Reviewer records a finding and reopens the point, or explains its rejection.",
    ),
    "finding and answer report": (
        "Report the finding and answer; send unresolved disagreement to the Manager.",
    ),
    "memory lane and count": (
        "Memory review uses separate sealed reports, finding "
        "identifiers and its own pass count. The Reviewer "
        "records its own lane with `begin_review` and "
        "`record_review`, using `reviewKind=memory`.",
    ),
    "memory leaves code untouched": (
        "Code state stays unchanged. Memory review "
        "opens no code review-state round, resets no "
        "passed code review, code verdict or code "
        "baseline, and adds no identifiers to that "
        "baseline.",
    ),
    "memory ordinary limit": ("Its ordinary pass limit is the same as code review's.",),
    "upward notice recovery": (
        "For a Worker, Curator or Reviewer whose required Manager "
        "notice fails delivery, record it as pending, "
        "continue independent work and retry the notice "
        "through `role_message` on `agents-remember-task`. "
        "Do not turn that delivery failure into a question "
        "to the developer in your own chat.",
    ),
    "memory extra approval": (
        "A pass beyond the settings limit needs the Architect's or developer's "
        "instruction recorded by the Manager.",
    ),
    "busy retry": (
        "Full-store recipient-busy is a refusal:\n"
        "\n"
        "1. Record the handover as pending and continue independent work.\n"
        "2. Resend once before the turn ends.\n"
        "3. If it is refused again, report the second refusal and leave it pending.",
    ),
}


LEAF_CLAUSES: dict[str, dict[str, tuple[str, ...]]] = {
    "roles/worker.md": {
        **IMPLEMENTATION_CLAUSES,
        "Reviewer start notice": (
            "1. Freeze the candidate and tell the Manager to start the Reviewer.\n"
            "2. Once the Reviewer exists, send it the freeze directly with `role_message` on "
            "`agents-remember-task`. Name the leaf, the report path and the whole next stretch "
            "of work. The report names the head and change hash.",
        ),
        "producer data preserved": (
            "4. After code review returns `PASS` and the Manager has started the Curator, send the "
            "Curator your hand-off list for that freeze directly. Name the same entries and "
            "changed paths so the changed-memory pass reads them.",
        ),
        "seat address": ("Use the sibling role and exact leaf task references from the handover.",),
        "whole product identity": (
            "Use a product-supplied agent identifier only whole and unchanged; never invent an "
            "owner, recipient or identifier.",
            "Use only the whole, unchanged agent ID supplied by the product; never "
            "invent an owner, recipient or identifier.",
        ),
        "action evidence": (
            "Count a handover as taken only when the recipient's reply, report or intake names the "
            "exact object and shows the requested work or its start.",
            "Open every supplied recipient artifact before you call one absent.",
        ),
        "nonaction notice": (
            "Report non-work replies and empty or doubled-seat refusals to the Manager once, with "
            "the seat and quoted text. Never pick a candidate yourself. When the Manager gives "
            "the occasion, send the handover yourself; it relays none of the contents.",
        ),
        "empty doubled refusal": (
            "Report non-work replies and empty or doubled-seat refusals to the Manager once, with "
            "the seat and quoted text. Never pick a candidate yourself. When the Manager gives "
            "the occasion, send the handover yourself; it relays none of the contents.",
        ),
        "sender owes contents": (
            "When the Manager gives the occasion, send the handover yourself; it relays none of "
            "the contents.",
        ),
        "useful report": (
            (
                "Write one useful report at the local report path named by the handover, and keep its "
                "evidence in that report folder. The product copies them to canonical notes before "
                "removal. Before writing the report, follow [Implementation outputs]"
                "(../operations/implementation.md#outputs) "
                "for report contents, checks and the freeze procedure."
            ),
        ),
    },
    "operations/implementation.md": {
        **IMPLEMENTATION_CLAUSES,
    },
    "roles/reviewer.md": {
        **REVIEW_CLAUSES,
        "sealed memory lane": (
            (
                "Memory review uses separate sealed reports, finding identifiers and its "
                "own pass count. The Reviewer records its own lane with `begin_review` "
                "and `record_review`, using `reviewKind=memory`. Code state stays "
                "unchanged. Memory review opens no code review-state round, resets no "
                "passed code review, code verdict or code baseline, and adds no "
                "identifiers to that baseline. Its ordinary pass limit is the same as "
                "code review's. A pass beyond the settings limit needs the Architect's or "
                "developer's instruction recorded by the Manager."
            ),
        ),
        "memory ordinary limit": (
            (
                "Memory review uses separate sealed reports, finding identifiers and its "
                "own pass count. The Reviewer records its own lane with `begin_review` "
                "and `record_review`, using `reviewKind=memory`. Code state stays "
                "unchanged. Memory review opens no code review-state round, resets no "
                "passed code review, code verdict or code baseline, and adds no "
                "identifiers to that baseline. Its ordinary pass limit is the same as "
                "code review's. A pass beyond the settings limit needs the Architect's or "
                "developer's instruction recorded by the Manager."
            ),
        ),
    },
    "operations/review.md": {
        **REVIEW_CLAUSES,
        "extra round beyond the limit": (
            "Within the settings limit, this is an ordinary fix-verification round. Beyond "
            "the limit, the round needs the Architect's or developer's approval, which the "
            "Manager records. No new finding or item is covered.",
        ),
        "sealed memory lane and count": (
            (
                "Memory review uses separate sealed reports, finding identifiers and its "
                "own pass count. The Reviewer records its own lane with `begin_review` "
                "and `record_review`, using `reviewKind=memory`. Code state stays "
                "unchanged. Memory review opens no code review-state round, resets no "
                "passed code review, code verdict or code baseline, and adds no "
                "identifiers to that baseline. Its ordinary pass limit is the same as "
                "code review's. A pass beyond the settings limit needs the Architect's or "
                "developer's instruction recorded by the Manager."
            ),
        ),
    },
    "roles/curator.md": {
        **CURATION_CLAUSES,
        "manager contact": (
            "The Manager receives Curator messages only for delivery or seat problems, ownership "
            "or authority, a pass beyond the settings limit, a code finding disputed with the "
            "Reviewer, or a sync step reserved to the Manager. It reads ordinary state from "
            "reports and relays no handover contents.",
        ),
    },
    "operations/curation.md": {
        **CURATION_CLAUSES,
    },
}
