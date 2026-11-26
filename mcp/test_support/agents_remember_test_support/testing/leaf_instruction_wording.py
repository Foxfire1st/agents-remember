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

from agents_remember_test_support.testing.instruction_renewal import RENEW_COMMAND


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
    assert len(matches) == 1, (
        source,
        action,
        "missing or repeated action pointer",
        f"If derived facts are stale, run {RENEW_COMMAND}",
    )


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
    "architect": "[msgs](",
    "orchestrator": "[msgs](",
    "manager": "[msgs](",
    "worker": "[msg](",
    "reviewer": "[msg](",
    "curator": "[msg](",
    "investigator": "[msgs](",
}
# One refused-write pointer per first message: the shared every-role paragraph
# links the served file for every role; only the Manager's capsule also carries
# the detail, for its separate task retirement and movement action.
REFUSED_WRITE_ACTIONS = {
    "manager": "[retire](",
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


_SHARED_AUTONOMY_CLAUSES: dict[str, tuple[str, ...]] = {
    "affirmative freedom": (
        "Inside an assignment the seat organises its own work with its harness, "
        "including sub-agents; the seat answers for their work and alone performs "
        "boundary acts.",
    ),
    "own split": (
        "The roles order work at the boundaries of tasks and their execution graph: "
        "assignment, hand-over, independent review, curation and landing.",
    ),
    "seat checks credits and answers": (
        "The seat organizes their work, verifies and credits it, and performs "
        "every boundary act itself.",
    ),
    "seat boundary acts": ("the seat answers for their work and alone performs boundary acts.",),
    "helper seat role and scope": (
        "A sub-agent holds no AR seat, starts no role, shares the seat's working "
        "folder, permissions and assignment, and cannot supply independent review "
        "of its seat's own work.",
        "A sub-agent holds no AR seat, does not start a role, shares the seat's "
        "working folder, permissions and assignment, and cannot supply independent "
        "review of its seat's own work.",
    ),
    "independent review": (
        "Helpers hold no Agents Remember seat, start no role and cannot provide "
        "independent review of their author.",
    ),
    "no harness dependency": (
        "A harness without sub-agents can still start a role agent through the framework.",
    ),
}

AUTONOMY_CLAUSES: dict[str, dict[str, tuple[str, ...]]] = {
    "architect": dict(_SHARED_AUTONOMY_CLAUSES),
    "orchestrator": dict(_SHARED_AUTONOMY_CLAUSES),
    "worker": {
        **_SHARED_AUTONOMY_CLAUSES,
        "helper guidelines": ("(helpers too; never edit skill guidelines)",),
    },
    "reviewer": dict(_SHARED_AUTONOMY_CLAUSES),
    "curator": {
        **_SHARED_AUTONOMY_CLAUSES,
        "one curator writer": (
            "One Curator writer lane (no helper writers); own placement, admission, "
            "meaning, checks and handover.",
        ),
    },
    "manager": dict(_SHARED_AUTONOMY_CLAUSES),
    "investigator": dict(_SHARED_AUTONOMY_CLAUSES),
    "bootstrap": {
        **_SHARED_AUTONOMY_CLAUSES,
        "bootstrap read-only helper scope": ("Helpers assist read-only queries only.",),
    },
}


def autonomy_clauses(role: str) -> dict[str, tuple[str, ...]]:
    """Return the role's current clauses and explicit whole-obligation equivalents."""
    return AUTONOMY_CLAUSES[role]


_UPWARD_NOTICE_RECOVERY = (
    "When a required Manager notice fails delivery for a Worker, Curator or "
    "Reviewer, record the notice as pending, continue independent work, and "
    "retry the notice through `role_message`. Never turn a delivery failure "
    "into a question to the developer in your own chat."
)

_REFUSED_HANDOVER_NOTICE = (
    "Report non-work replies and empty-seat or doubled-seat refusals to the "
    "Manager once, naming the seat and quoting the refusal text. Never pick a "
    "candidate yourself. When the Manager gives the occasion, send the "
    "handover yourself; the Manager relays none of the contents."
)

_BUSY_RETRY = (
    "Full-store recipient-busy is a refusal:\n"
    "\n"
    "1. Record the handover as pending and continue independent work.\n"
    "2. Resend once before the turn ends.\n"
    "3. If it is refused again, report the second refusal and leave it pending."
)

_MEMORY_REVIEW_LANE = (
    "Memory review uses a separate lane (`reviewKind=memory`; own sealed "
    "reports/IDs/pass count, same limit; extra passes need Architect/developer "
    "instruction recorded by Manager); code verdict/baseline stay unchanged."
)

IMPLEMENTATION_CLAUSES = {
    "freeze to reviewer": (
        "1. Freeze; tell Manager to start Reviewer.\n"
        "2. Send Reviewer report path (no pasted text), head/hash + next stretch "
        "via `role_message`.",
    ),
    "identity and whole stretch": (
        "2. Send Reviewer report path (no pasted text), head/hash + next stretch "
        "via `role_message`.",
    ),
    "repair return": (
        "3. Repair sealed own-file findings directly; return freeze to same Reviewer.",
    ),
    "producer to curator": (
        "4. After code PASS + Curator start, send Curator list + `handoff-check` directly.",
    ),
    "factual independence": (
        "Each producer sends its own list directly, with its data unchanged.",
    ),
    "upward notice recovery": (_UPWARD_NOTICE_RECOVERY,),
    "busy retry": (_BUSY_RETRY,),
}


REVIEW_CLAUSES = {
    "verdict to worker": (
        "2. Send verdict path directly to Worker via `role_message` (no pasted "
        "text; block names sealed report + repair stretch).",
    ),
    "sealed whole repair": ("(no pasted text; block names sealed report + repair stretch).",),
    "pass to curator": (
        "1. On code PASS, tell Manager to start Curator first.\n"
        "2. Send verdict path directly to Worker via `role_message` (no pasted "
        "text; block names sealed report + repair stretch).\n"
        "3. Once Curator exists, run `handoff-check` (`server_info`) and send "
        "Curator freeze, code-PASS verdict, list + result directly.",
    ),
    "memory verdict to curator": (
        "4. Review Curator's memory change; return findings or PASS directly.",
    ),
    "identity and whole stretch": (
        "Name leaf, candidate, path + next stretch (sibling role, leaf refs, whole IDs).",
    ),
    "own code begin": (
        "Open leaf code rounds via `task_doc(begin_review)`, record via "
        "`record_review`/`record_route_review`; non-leaf keeps owner recording.",
    ),
    "own code result": ("record via `record_review`/`record_route_review`",),
    "preceding result": (
        "Record the preceding result, exact outstanding identifiers and their "
        "fixed/unfixed dispositions, and retain earlier accepted adjudications.",
    ),
    "manager acceptance": (
        "Manager owns gate acceptance and records Architect/developer extra-round approvals.",
    ),
    "memory leaves code untouched": ("code verdict/baseline stay unchanged.",),
    "memory extra approval": (
        "extra passes need Architect/developer instruction recorded by Manager",
    ),
    "post-pass concern": (
        "Send evidenced code concerns to both Worker and Reviewer, including after "
        "passing code review, showing the requirement or report mismatch. Repair no "
        "code and author no knowledge claiming behavior the code lacks. The "
        "Reviewer records a finding and reopens the point, or explains its "
        "rejection; the Curator records the answer, reports both the finding and "
        "the answer, and sends unresolved disagreement to the Manager.",
    ),
    "reopen or refute answer": (
        "For all three routes, add a sealed finding with its own identifier and "
        "reopen only that point. Give the reason when rejecting an invalid finding. "
        "The Worker repairs an accepted finding in a successor. Send an unresolved "
        "disagreement or authority issue to the Manager.",
    ),
    "upward notice recovery": (_REFUSED_HANDOVER_NOTICE + "\n\n" + _UPWARD_NOTICE_RECOVERY,),
    "memory extra approval authority": (
        "Manager owns gate acceptance and records Architect/developer extra-round "
        "approvals. " + _MEMORY_REVIEW_LANE,
    ),
    "busy retry": (_BUSY_RETRY,),
}


CURATION_CLAUSES = {
    "memory to reviewer": (
        "Send memory candidate to Reviewer via `role_message` (sibling role, leaf refs, whole IDs);",
    ),
    "identity and whole stretch": (
        "Each handover names the leaf, the Worker's exact code head and "
        "change hash, the memory candidate's identity, the report or "
        "verdict path, and the whole next stretch requested.",
    ),
    "post-pass code finding": (
        "Send evidenced code concerns to both Worker and Reviewer, including after "
        "passing code review, showing the requirement or report mismatch.",
    ),
    "code answer": (
        "The Reviewer records a finding and reopens the point, or explains its rejection;",
    ),
    "finding and answer report": (
        "the Curator records the answer, reports both the finding and the answer, "
        "and sends unresolved disagreement to the Manager.",
    ),
    "memory lane and count": (_MEMORY_REVIEW_LANE,),
    "memory leaves code untouched": ("code verdict/baseline stay unchanged.",),
    "memory ordinary limit": ("own sealed reports/IDs/pass count, same limit;",),
    "upward notice recovery": (_UPWARD_NOTICE_RECOVERY,),
    "memory extra approval": (
        "extra passes need Architect/developer instruction recorded by Manager",
    ),
    "busy retry": (_BUSY_RETRY,),
}


LEAF_CLAUSES: dict[str, dict[str, tuple[str, ...]]] = {
    "roles/worker.md": {
        **IMPLEMENTATION_CLAUSES,
        "Reviewer start notice": (
            "1. Freeze; tell Manager to start Reviewer.\n"
            "2. Send Reviewer report path (no pasted text), head/hash + next stretch "
            "via `role_message`.",
        ),
        "producer data preserved": (
            "4. After code PASS + Curator start, send Curator list + `handoff-check` directly.",
        ),
        "seat address": (
            "Send via `role_message` by ID or role+task refs (role != self; invent no IDs).",
        ),
        "whole product identity": (
            "Use the agent IDs these tools return and the sender line of each message "
            "you receive; never invent a sender, recipient, parent, or delivery result.",
            "Use only the agent IDs these tools return and the sender line of each "
            "message you receive; never invent a sender, recipient, parent, or delivery result.",
        ),
        "action evidence": (
            "Count a handover as taken only when the recipient's reply, report or "
            "intake names the exact object and shows the requested work or its start. "
            "Open every supplied recipient artifact before you call one absent.",
        ),
        "nonaction notice": (_REFUSED_HANDOVER_NOTICE,),
        "empty doubled refusal": (_REFUSED_HANDOVER_NOTICE,),
        "sender owes contents": (
            "When the Manager gives the occasion, send the handover yourself; "
            "the Manager relays none of the contents.",
        ),
        "useful report": (
            "Write one local report (report template + "
            "`system/code-quality-report-template.md`; map evidence; per-finding repairs).",
        ),
    },
    "operations/implementation.md": {
        **IMPLEMENTATION_CLAUSES,
    },
    "roles/reviewer.md": {
        **REVIEW_CLAUSES,
        "sealed memory lane": (_MEMORY_REVIEW_LANE,),
        "memory ordinary limit": (_MEMORY_REVIEW_LANE,),
    },
    "operations/review.md": {
        **REVIEW_CLAUSES,
        "extra round beyond the limit": (
            "Within the settings limit, this is an ordinary fix-verification round. Beyond "
            "the limit, the round needs the Architect's or developer's approval, which the "
            "Manager records. No new finding or item is covered.",
        ),
        "sealed memory lane and count": (_MEMORY_REVIEW_LANE,),
    },
    "roles/curator.md": {
        **CURATION_CLAUSES,
        "manager contact": (
            "Manager notices: delivery/seat, authority, beyond-limit passes, disputed "
            "findings, reserved sync steps; sync memory conflicts -> Curator; "
            "cross-leaf needs named dependency.",
        ),
    },
    "operations/curation.md": {
        **CURATION_CLAUSES,
    },
}
