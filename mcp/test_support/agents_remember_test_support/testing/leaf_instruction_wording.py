"""Positive R99 clauses, classified by the role/operation that must deliver them.

Match affirmative propositions rather than disconnected vocabulary. Alternatives cover the
actual role variants and equivalent preserved boundaries, not weaker permissions.
"""

from __future__ import annotations

import re

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
    if role == "curator":
        clauses["one curator writer"] = (
            "Keep exactly one Curator writer through the admitted writer",
        )
    return clauses


LEAF_CLAUSES: dict[str, dict[str, tuple[str, ...]]] = {
    "roles/worker.md": {
        "freeze to reviewer": (
            "Freeze the candidate and send it directly to the leaf's Reviewer as `../operations/implementation.md` §Outputs states.",
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
        ),
        "sealed whole repair": (
            "A block names the report with sealed findings and requests the whole repair stretch",
        ),
        "pass to curator": (
            "code PASS also goes directly to the Curator with your hand-off list for that freeze",
        ),
        "memory verdict to curator": (
            "hand it directly to the leaf's Worker as `../operations/review.md` §Outputs states",
        ),
        "identity and whole stretch": (
            "candidate head and change hash from the Worker's report, or memory change identity), report or verdict path, and request the recipient's whole next stretch",
        ),
        "own code begin": (
            "record your own begin and result as `../operations/review.md` §Outputs states",
        ),
        "own code result": (
            "record your own begin and result as `../operations/review.md` §Outputs states",
        ),
        "preceding result": (
            "record your own begin and result as `../operations/review.md` §Outputs states",
        ),
        "manager acceptance": (
            "The Manager accepts the verdict for the gate and records the developer's authorization beyond the limit.",
        ),
        "sealed memory lane": (
            "Curator memory review keeps its separately sealed lane as `../operations/review.md` §Boundaries states",
        ),
        "memory leaves code untouched": (
            "Curator memory review keeps its separately sealed lane as `../operations/review.md` §Boundaries states",
        ),
        "memory ordinary limit": (
            "Curator memory review keeps its separately sealed lane as `../operations/review.md` §Boundaries states",
        ),
        "memory extra approval": (
            "the developer must authorize an extra pass, and the Manager records that authorization in the leaf's decisions",
        ),
        "post-pass concern": (
            "Answer a Curator finding that code fails the requirement or contradicts the Worker's report as `../operations/review.md` §Boundaries states, including after code PASS.",
        ),
        "reopen or refute answer": (
            "Answer a Curator finding that code fails the requirement or contradicts the Worker's report as `../operations/review.md` §Boundaries states, including after code PASS.",
        ),
        "busy retry": (
            "Count a hand-over as taken and handle provider, seat and busy refusals as `../operations/review.md` §Boundaries states.",
        ),
        "upward notice recovery": (
            "Count a hand-over as taken and handle provider, seat and busy refusals as `../operations/review.md` §Boundaries states.",
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
            "The Manager retains gate acceptance and records the developer's approval for any extra round.",
        ),
        "sealed memory lane and count": (
            "Curator memory checks keep separately sealed reports, IDs and a count with the same ordinary limit as code",
        ),
        "memory leaves code untouched": (
            "they open no review-state round, reset no passed code review and add no IDs to its baseline",
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
