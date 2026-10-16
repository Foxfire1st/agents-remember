"""Positive R99 clauses, classified by the role/operation that must deliver them.

Match affirmative propositions rather than disconnected vocabulary. Alternatives cover the
actual role variants and equivalent preserved boundaries, not weaker permissions.
"""

from __future__ import annotations

import re
from pathlib import Path

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


def delivered_step(
    root: Path,
    role_operation: tuple[CapsuleRole, CapsuleOperation],
    source: str,
    step: str,
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
    assert reference.step == step
    assert reference.content == (root / source).read_bytes()
    instructions, record = compose_launch_instructions(delivery, str(report_path), {})
    entries = [entry for entry in record["detailReferences"] if entry["sourcePath"] == source]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["step"] == step
    path = Path(entry["path"])
    assert f"]({path.as_posix()})" in instructions
    assert "{{detail:" not in instructions
    assert path.read_bytes() == reference.content
    return instructions, path.read_text(encoding="utf-8")


ROLES = (
    "architect",
    "bootstrap",
    "curator",
    "designer",
    "manager",
    "orchestrator",
    "reviewer",
    "strategist",
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


def autonomy_clauses(role: str) -> dict[str, tuple[str, ...]]:
    clauses = {
        "affirmative freedom": (
            "Inside your assignment, organise your work yourself with what your harness offers, including sub-agents.",
            "Organise your own work inside the assignment, with whatever your harness offers, at any size.",
        ),
        "own split": (
            "You decide how to split it; the roles order work at task boundaries.",
            "You decide the split; the roles order work at task boundaries.",
            "You may split the work among harness sub-agents as you see fit; no rule prescribes how many or how deep.",
        ),
        "seat checks credits and answers": (
            "You answer for all their work: check it, credit it in your report, and hand it over under your own name.",
            "The seat answers for all of it: check their work, report which parts they did, and hand it over under your own name.",
        ),
        "seat boundary acts": (
            "Messages to other seats, task records and product operations that change leaf state are your own acts.",
            "Every boundary act is yours: a message to another seat, a task record or a product operation changing leaf state.",
        ),
        "helper seat role and scope": (
            "A harness sub-agent holds no AR seat, starts no role, and has the same working folder, permissions and assignment as you.",
            "A harness sub-agent is no AR role, holds no seat, starts no role and has the same working folder, permissions and assignment.",
            "A harness sub-agent holds no AR seat, does not start a role, and has the same working folder, permissions and assignment as you.",
            "A harness sub-agent is no AR role, holds no seat, does not start a role and has the same working folder, permissions and assignment.",
        ),
        "independent review": (
            "Nobody checks itself through a sub-agent: independent code and memory review belongs to the Reviewer.",
            "Nobody checks itself through a sub-agent: code and memory changes still require the independent Reviewer, never their author's helper.",
            "Nobody checks itself through a sub-agent: the Reviewer's check of your memory candidate stays the Reviewer's.",
        ),
        "no harness dependency": (
            "A harness without sub-agents retains the same duties.",
            "A harness without sub-agents can do all the same work.",
        ),
    }
    if role == "reviewer":
        equivalents = {
            "affirmative freedom": "Organise assignment work at any size with any harness facilities.",
            "own split": "You choose the split; roles order work at task boundaries.",
            "seat checks credits and answers": "You answer for all work: check helpers\u2019 work, report their parts, and hand over under your name.",
            "seat boundary acts": "Every boundary act remains yours: inter-seat messages, task records and product operations changing leaf state.",
            "helper seat role and scope": "Harness sub-agents hold no AR role or seat, start no role, and share your working folder, permissions and assignment.",
            "independent review": "Sub-agents cannot self-check: code and memory changes require the independent Reviewer, never their author\u2019s helper.",
            "no harness dependency": "Harnesses without sub-agents can do the same work.",
        }
        for name, equivalent in equivalents.items():
            clauses[name] += (equivalent,)
    if role == "curator":
        equivalents = {
            "affirmative freedom": "Organise assignment work with any harness facilities, at any size.",
            "own split": "Use harness sub-agents freely; neither number nor depth is prescribed.",
            "seat checks credits and answers": "You answer for all work: verify delegates, report their parts and hand over under your name.",
            "seat boundary acts": "You own every boundary act: cross-seat messages, task records and leaf-state-changing product operations.",
            "helper seat role and scope": "Sub-agents hold no AR role or seat, start no role and share your folder, permissions and assignment.",
            "independent review": "No self-check through sub-agents: the Reviewer retains your memory-candidate check.",
        }
        for name, equivalent in equivalents.items():
            clauses[name] += (equivalent,)
        clauses["one curator writer"] = (
            "Keep exactly one Curator writer through the admitted writer",
            "Keep exactly one Curator writer through the admitted route, never a helper lane.",
        )
        completed_forms = {
            "affirmative freedom": "Organise work at any size with any harness facilities.",
            "own split": "Helper number/depth is unrestricted.",
            "seat checks credits and answers": "You answer for all work. Verify helpers, report their parts and hand over under your name.",
            "seat boundary acts": "You own inter-seat messages, task records and leaf-state-changing operations.",
            "helper seat role and scope": "Helpers share your assignment, folder and permissions; they hold no AR role/seat and start none.",
            "independent review": "No helper self-checking: the independent Reviewer retains memory-candidate review.",
        }
        for name, equivalent in completed_forms.items():
            clauses[name] += (equivalent,)
    return clauses


LEAF_CLAUSES: dict[str, dict[str, tuple[str, ...]]] = {
    "roles/worker.md": {
        "freeze to reviewer": (
            "Freeze the candidate and send it directly to the leaf's Reviewer as `../operations/implementation.md` §Outputs states.",
        ),
        "set start": (
            "the Manager starts the leaf's Worker, Reviewer and Curator as a set in one `role_start_set` call",
        ),
        "identity and whole stretch": (
            "Freeze the candidate and send it directly to the leaf's Reviewer as `../operations/implementation.md` §Outputs states.",
        ),
        "repair return": (
            "Freeze the candidate and send it directly to the leaf's Reviewer as `../operations/implementation.md` §Outputs states.",
        ),
        "producer to curator": (
            "After code PASS, send the Curator your structured hand-off list for that exact freeze with producer data preserved verbatim",
        ),
        "factual independence": (
            "Freeze the candidate and send it directly to the leaf's Reviewer as `../operations/implementation.md` §Outputs states.",
        ),
        "seat address": ("Use the sibling role and exact leaf task references from the handover.",),
        "whole product identity": (
            "Use an agent ID only whole and unchanged as the product supplied it",
            "Use only the whole, unchanged agent ID supplied by the product",
        ),
        "action evidence": (
            "Count a hand-over as taken and handle provider, seat and busy refusals as `../operations/implementation.md` §Boundaries states.",
        ),
        "nonaction notice": (
            "Count a hand-over as taken and handle provider, seat and busy refusals as `../operations/implementation.md` §Boundaries states.",
        ),
        "empty doubled refusal": (
            "Count a hand-over as taken and handle provider, seat and busy refusals as `../operations/implementation.md` §Boundaries states.",
        ),
        "busy retry": (
            "Count a hand-over as taken and handle provider, seat and busy refusals as `../operations/implementation.md` §Boundaries states.",
        ),
        "sender owes contents": (
            "Count a hand-over as taken and handle provider, seat and busy refusals as `../operations/implementation.md` §Boundaries states.",
        ),
        "upward notice recovery": (
            "Count a hand-over as taken and handle provider, seat and busy refusals as `../operations/implementation.md` §Boundaries states.",
        ),
        "useful report": (
            "Write one useful report at the canonical task report path as `../operations/implementation.md` §Outputs states.",
        ),
    },
    "operations/implementation.md": {
        "freeze to reviewer": ("Freeze the candidate and hand it directly to the leaf's Reviewer",),
        "identity and whole stretch": (
            "naming the leaf, head and change hash in your report, report path, and whole next stretch",
        ),
        "repair return": (
            "Repair sealed findings and return a new freeze directly to the same Reviewer.",
        ),
        "producer to curator": (
            "After code PASS, send your hand-off list for that freeze directly to the Curator.",
        ),
        "factual independence": ("This is a factual hand-over, not a review brief.",),
        "busy retry": (
            "For busy, record pending, retry before ending your turn and report a second refusal once.",
        ),
        "upward notice recovery": (
            "Record and retry an unreachable-Manager notice rather than put that transport failure to the developer.",
        ),
    },
    "roles/reviewer.md": {
        "verdict to worker": (
            "hand it directly to the leaf's Worker as `../operations/review.md` §Outputs states",
            "Write/record the verdict and hand it directly to the leaf\u2019s Worker per `../operations/review.md` §Outputs.",
        ),
        "sealed whole repair": (
            "A block names the report with sealed findings and requests the whole repair stretch",
            "A block names the sealed-findings report and requests the whole repair stretch",
        ),
        "pass to curator": (
            "code PASS also goes directly to the Curator with your hand-off list for that freeze",
            "send code PASS and your freeze hand-off list directly to the Curator",
        ),
        "memory verdict to curator": (
            "hand it directly to the leaf's Worker as `../operations/review.md` §Outputs states",
            "Review the Curator\u2019s memory change in its separate sealed lane; send findings or PASS directly to the Curator.",
        ),
        "identity and whole stretch": (
            "candidate head and change hash from the Worker's report, or memory change identity), report or verdict path, and request the recipient's whole next stretch",
            "Every leaf hand-over names the leaf, exact object (Worker-reported candidate head and change hash, or memory change identity), report/verdict path, and requests the recipient\u2019s whole next stretch.",
        ),
        "own code begin": (
            "record your own begin and result as `../operations/review.md` §Outputs states",
            "On taking a leaf code hand-over within the settings limit, record your begin/result per `../operations/review.md` §Outputs.",
        ),
        "own code result": (
            "record your own begin and result as `../operations/review.md` §Outputs states",
            "On taking a leaf code hand-over within the settings limit, record your begin/result per `../operations/review.md` §Outputs.",
        ),
        "preceding result": (
            "record your own begin and result as `../operations/review.md` §Outputs states",
            "On taking a leaf code hand-over within the settings limit, record your begin/result per `../operations/review.md` §Outputs.",
        ),
        "manager acceptance": (
            "The Manager accepts the verdict for the gate and records the architect's or the developer's authorization beyond the limit.",
            "The Manager accepts the gate verdict and records architect/developer authorization beyond the limit.",
        ),
        "sealed memory lane": (
            "Curator memory review keeps its separately sealed lane as `../operations/review.md` §Boundaries states",
            "Curator memory review keeps its separately sealed lane per `../operations/review.md` §Boundaries",
            "Record memory rounds with `begin_review`/`record_review`, `reviewKind=memory`: separate baseline/list",
        ),
        "memory leaves code untouched": (
            "Curator memory review keeps its separately sealed lane as `../operations/review.md` §Boundaries states",
            "Curator memory review keeps its separately sealed lane per `../operations/review.md` §Boundaries",
            "unchanged code state",
        ),
        "memory ordinary limit": (
            "Curator memory review keeps its separately sealed lane as `../operations/review.md` §Boundaries states",
            "Curator memory review keeps its separately sealed lane per `../operations/review.md` §Boundaries",
            "same limit; extras need architect/developer approval recorded by Manager in leaf decisions.",
        ),
        "memory extra approval": (
            "the task's architect or the developer must authorize an extra pass, and the Manager records that authorization in the leaf's decisions",
            "extra passes require the task\u2019s architect or developer\u2019s authorization, recorded by the Manager in leaf decisions",
            "extras need architect/developer approval recorded by Manager in leaf decisions.",
        ),
        "post-pass concern": (
            "Answer a Curator finding that code fails the requirement or contradicts the Worker's report as `../operations/review.md` §Boundaries states, including after code PASS.",
            "Answer Curator findings of code failing requirements or contradicting the Worker\u2019s report per `../operations/review.md` §Boundaries, even after code PASS.",
        ),
        "reopen or refute answer": (
            "Answer a Curator finding that code fails the requirement or contradicts the Worker's report as `../operations/review.md` §Boundaries states, including after code PASS.",
            "Answer Curator findings of code failing requirements or contradicting the Worker\u2019s report per `../operations/review.md` §Boundaries, even after code PASS.",
        ),
        "busy retry": (
            "Count a hand-over as taken and handle provider, seat and busy refusals as `../operations/review.md` §Boundaries states.",
            "Apply `../operations/review.md` §Boundaries to taken hand-overs and provider, seat and busy refusals.",
        ),
        "upward notice recovery": (
            "Count a hand-over as taken and handle provider, seat and busy refusals as `../operations/review.md` §Boundaries states.",
            "Apply `../operations/review.md` §Boundaries to taken hand-overs and provider, seat and busy refusals.",
        ),
    },
    "operations/review.md": {
        "verdict to worker": ("For a leaf, hand the verdict directly to the Worker",),
        "sealed whole repair": (
            "a block names the sealed findings report and asks for the whole repair stretch",
        ),
        "pass to curator": ("Code PASS also goes directly to the Curator for the passed freeze.",),
        "memory verdict to curator": (
            "Review its memory change and return findings or PASS directly to it.",
        ),
        "identity and whole stretch": (
            "candidate head and change hash from the Worker's report, or memory change identity), report/verdict path, and the whole next stretch",
        ),
        "own code begin": (
            'The Reviewer opens each ordinary leaf code round with task_doc(operation="begin_review")',
        ),
        "own code result": (
            "records its own verdict with record_review or applicable record_route_review",
        ),
        "preceding result": ("including any preceding result required before the next begin",),
        "manager acceptance": (
            "The Manager retains gate acceptance and records the architect's or the developer's approval for any extra round.",
        ),
        "sealed memory lane and count": (
            "Curator memory checks keep separately sealed reports, IDs and a count with the same settings limit as code",
            "Curator memory checks use their own sealed reports, IDs and recorded lane: `begin_review`/`record_review` with `reviewKind=memory`, same settings limit and extra-round approval as code.",
        ),
        "memory leaves code untouched": (
            "they open no review-state round, reset no passed code review and add no IDs to its baseline",
            "They open no code review-state round, reset no passed code review and add no IDs to its baseline.",
        ),
        "memory extra approval": (
            "Any extra memory pass follows `../operations/curation.md` §Handoff / exit states.",
        ),
        "post-pass concern": (
            "Answer Curator code findings directly to Curator and Worker, even after code PASS",
        ),
        "reopen or refute answer": (
            "take a valid finding into your findings and reopen the verdict for that point, or state why it does not hold",
        ),
        "busy retry": (
            "Count a hand-over as taken and handle provider, seat, busy and unreachable-Manager refusals as `../operations/implementation.md` §Boundaries states.",
        ),
        "upward notice recovery": (
            "Count a hand-over as taken and handle provider, seat, busy and unreachable-Manager refusals as `../operations/implementation.md` §Boundaries states.",
        ),
    },
    "roles/curator.md": {
        "memory to reviewer": (
            "Hand your memory candidate and its report directly to the Reviewer for the whole memory check as `../operations/curation.md` §Handoff / exit states",
        ),
        "identity and whole stretch": (
            "Hand your memory candidate and its report directly to the Reviewer for the whole memory check as `../operations/curation.md` §Handoff / exit states",
        ),
        "post-pass code finding": (
            "Send code questions and evidenced code concerns as `../operations/curation.md` §Failure handling states, even after the Reviewer has passed the code",
        ),
        "code answer": (
            "Send code questions and evidenced code concerns as `../operations/curation.md` §Failure handling states, even after the Reviewer has passed the code",
        ),
        "finding and answer report": ("Your report names the finding and the Reviewer's answer.",),
        "memory lane and count": (
            "Hand your memory candidate and its report directly to the Reviewer for the whole memory check as `../operations/curation.md` §Handoff / exit states",
        ),
        "memory leaves code untouched": (
            "Hand your memory candidate and its report directly to the Reviewer for the whole memory check as `../operations/curation.md` §Handoff / exit states",
        ),
        "memory ordinary limit": (
            "Hand your memory candidate and its report directly to the Reviewer for the whole memory check as `../operations/curation.md` §Handoff / exit states",
        ),
        "busy retry": (
            "Address handovers and count them as taken as `../operations/curation.md` §Handoff / exit states.",
        ),
        "upward notice recovery": (
            "If a required Manager notice cannot be delivered, record it as pending and retry it as `../operations/curation.md` §Handoff / exit states; do not turn this transport failure into a developer question in your own chat.",
        ),
        "manager contact": (
            "Contact the Manager only as `../operations/curation.md` §Handoff / exit states.",
        ),
    },
    "operations/curation.md": {
        "memory to reviewer": (
            "The curator's memory candidate goes directly to the Reviewer for its whole memory check",
        ),
        "identity and whole stretch": (
            "the memory candidate's identity), its report or verdict path, and the whole next stretch requested",
        ),
        "post-pass code finding": (
            "A code concern goes to the Reviewer and Worker, even after code PASS.",
        ),
        "code answer": (
            "The Reviewer answers by taking it into its findings and reopening that point, or explaining why it does not hold.",
        ),
        "finding and answer report": ("Name the finding and answer in the Curator report",),
        "memory lane and count": (
            "The memory check keeps separate sealed reports, finding IDs and its own pass count",
        ),
        "memory leaves code untouched": (
            "it opens no code-review round, resets no code verdict and adds no IDs to the code baseline",
        ),
        "memory ordinary limit": (
            "Its ordinary limit is the same number of passes as the code review.",
        ),
        "busy retry": (
            "Count a hand-over as taken and handle provider, seat, busy and unreachable-Manager refusals as `../operations/implementation.md` §Boundaries states.",
        ),
        "upward notice recovery": (
            "If a required Manager notice cannot be delivered, record it as pending",
            "Count a hand-over as taken and handle provider, seat, busy and unreachable-Manager refusals as `../operations/implementation.md` §Boundaries states.",
        ),
    },
}
