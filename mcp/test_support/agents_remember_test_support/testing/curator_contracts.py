"""Retained R62 rules31-37: source-local semantic checks and real compiled delivery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import quote, unquote, urlsplit

import pytest
from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember.models.role_capsules.manifest import parse_composition_manifest
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleCompilationResult,
    CapsuleOperation,
    CapsuleRole,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS

AUTHORING = "reference/curator/card-sentence-decisions.md"
CARD = "reference/curator/card-formats-and-citations.md"
KNOWLEDGE = "reference/knowledge/commands.md"
WRITER = "reference/knowledge/serving-build.md"
PUBLICATION = "reference/curator/publication-report.md"
AUTHORED_HISTORY = "reference/curator/current-prose-and-history.md"
CHANGE_ROUTING = "reference/curator/change-routing.md"
ROLE = "roles/curator.md"
OPERATION = "operations/curation.md"
CRITERIA = "criteria/onboarding-memory.md"


def gfm_anchor(heading: str) -> str:
    """GitHub's heading anchor: lower-cased, punctuation dropped except '-' and '_',
    and every space turned into one '-' (runs are not collapsed: "A — B" is "a--b")."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lstrip("#").strip().lower()).replace(" ", "-")


def normalized(text: str) -> str:
    return " ".join(text.replace("*", "").replace("`", "").split())


def require_clauses(text: str, clauses: tuple[str, ...], where: object) -> None:
    reading = normalized(text)
    for clause in clauses:
        assert normalized(clause) in reading, (where, clause)


@dataclass(frozen=True)
class SectionLink:
    """The owner and complete action cue of one required section link."""

    target: Path
    label: str
    action: str
    heading: str


def linked_section(text: str, origin: Path, link: SectionLink) -> str:
    """Follow one action-local link to its exact existing owner and heading."""
    target, label, action, heading = link.target, link.label, link.action, link.heading
    paragraphs = [
        paragraph
        for paragraph in text.split("\n\n")
        if normalized(action) in normalized(paragraph) and f"[{label}](" in paragraph
    ]
    assert len(paragraphs) == 1, (label, action, "missing or misplaced pointer")
    assert paragraphs[0].index(action) < paragraphs[0].index(f"[{label}](")
    links = re.findall(r"\[" + re.escape(label) + r"\]\(([^)]+)\)", paragraphs[0])
    assert len(links) == 1, (label, "missing or repeated owner link")
    parts = urlsplit(links[0])
    assert not parts.scheme and not parts.netloc and not parts.query
    assert (origin.parent / unquote(parts.path)).resolve() == target.resolve()
    assert parts.fragment == gfm_anchor(heading)
    assert target.is_file(), target
    owner = target.read_text(encoding="utf-8")
    assert owner.count(heading + "\n") == 1, (target, heading)
    level = len(heading) - len(heading.lstrip("#"))
    section = owner.split(heading + "\n", 1)[1]
    return re.split(r"(?m)^#{1," + str(level) + r"} ", section, maxsplit=1)[0]


def require_permission_delivery(detail: str, root: Path) -> None:
    # Require the actual emitted permission pointer and its complete reached rule.
    with shipped_composition_corpus() as (origin, _):
        target = origin / "operations/orientation.md"
        assert target.read_bytes() == (root / "operations/orientation.md").read_bytes()
        boundary = linked_section(
            detail,
            root / "reference/refused-write.md",
            SectionLink(
                target,
                "Honor harness permission decisions",
                PERMISSION_ACTION,
                "## Honor harness permission decisions",
            ),
        )
        require_clauses(boundary, PERMISSION_CLAUSES, "permission owner")
        for obligation in PERMISSION_CLAUSES:
            reading = normalized(boundary)
            mutant = reading.replace(normalized(obligation), "", 1)
            assert mutant != reading
            with pytest.raises(AssertionError):
                require_clauses(mutant, PERMISSION_CLAUSES, "deleted permission condition")
        for mutant in (
            detail.replace("[Honor harness permission decisions]", "[missing rule]", 1),
            detail.replace("#honor-harness-permission-decisions", "#wrong-section", 1),
            detail.replace(
                "[Honor harness permission decisions]",
                "\n\n[Honor harness permission decisions]",
                1,
            ),
        ):
            assert mutant != detail
            with pytest.raises(AssertionError):
                linked_section(
                    mutant,
                    root / "reference/refused-write.md",
                    SectionLink(
                        target,
                        "Honor harness permission decisions",
                        PERMISSION_ACTION,
                        "## Honor harness permission decisions",
                    ),
                )


def require_coordination_routes(root: Path) -> None:
    """Require each moved coordination rule at its exact action-local owner."""
    # The short held-delivery rule arrives in the real shared stream; it has no detail read.
    held = (
        "A held receipt means queued eventual delivery, possibly many turns later. Never resend it. "
        "Count a handover as taken only when the recipient's reply, report or intake names the exact "
        "object and shows the requested work or its start. Open every supplied recipient artifact "
        "before you call one absent. Withdraw a superseded held message with "
        "`withdraw_held_message_id`. A receiver does not poll."
    )
    require_clauses((root / "core/authority.md").read_text(), (held,), "held core owner")
    for delivery in compile_matrix(root):
        if delivery.role != "orchestrator":
            continue
        shared = delivery.shared_block
        assert shared is not None and shared == (root / "core/every-role.md").read_text()
        assert shared.count(held) == 1
        for mutant in (shared.replace(held, "", 1), shared + "\n" + held):
            with pytest.raises(AssertionError):
                assert mutant.count(held) == 1
    caller = (root / "roles/orchestrator.md").read_text()
    action = "When the send answers held, follow [the held-delivery rule]"
    assert caller.count(action) == 1
    for mutant in (caller.replace(action, "", 1), caller + "\n" + action):
        with pytest.raises(AssertionError):
            assert mutant.count(action) == 1
    assert "#handle-held-delivery" not in caller
    routes = (
        # P5-18: the full-store pointer lives once per first message, in the operation.
        (
            "operations/coordination.md",
            "reference/message-handover.md",
            "Handle a full-store refusal",
            "Before responding to a full-store refusal, read",
            "## Handle a full-store refusal",
        ),
        (
            "operations/coordination.md",
            "operations/curation.md",
            "Curation failure handling",
            "Before sending or answering a Curator's code concern, read",
            "## Failure handling",
        ),
    )
    for caller, owner, label, action, heading in routes:
        actual = (root / caller).read_text()
        linked_section(actual, root / caller, SectionLink(root / owner, label, action, heading))
        for mutant in (
            actual.replace("[" + label + "]", "[missing owner]", 1),
            actual.replace("[" + label + "](", "[" + label + "](wrong-owner/", 1),
        ):
            assert mutant != actual
            with pytest.raises(AssertionError):
                linked_section(
                    mutant, root / caller, SectionLink(root / owner, label, action, heading)
                )


@dataclass(frozen=True)
class Delivered:
    role: str
    operation: str
    result: CapsuleCompilationResult
    shared_block: str | None

    def block(self, kind: str) -> str:
        blocks = [
            block.content
            for block in self.result.capsule.instructions
            if block.composition_root == kind
        ]
        assert len(blocks) == 1, (self.role, self.operation, kind)
        return blocks[0]


def compile_matrix(root: Path) -> tuple[Delivered, ...]:
    """Compile every declared valid pair, with no role/operation sample or synthetic corpus."""
    manifest = (root / "composition-manifest.json").read_bytes()
    parsed = parse_composition_manifest(manifest)
    pairs = {
        (role, operation) for role, entry in parsed.roles.items() for operation in entry.operations
    }
    assert pairs == {
        (role, operation)
        for operation, entry in parsed.operations.items()
        for role in entry.applies_to_roles
    }
    delivered = []
    for role, operation in sorted(pairs):
        binding = CapsuleBinding(
            operation=cast(CapsuleOperation, operation),
            admitted=CapsuleAdmittedFacts(
                task_reference="fixture/leaf.json",
                task_document_digest=compute_content_digest(b'{"id":"leaf"}'),
                seat=CapsuleRoleSeat(cast(CapsuleRole, role), parsed.roles[role].altitude),
                repository_id="fixture",
                work_branch="fixture",
                tool_policy=CapsuleToolPolicy(granted=frozenset(PUBLIC_TOOLS)),
            ),
        )
        admission = routed_admission_for(
            root,
            "composition-manifest.json",
            manifest,
            CapsuleSeatAddress(cast(CapsuleRole, role), cast(CapsuleOperation, operation)),
        )
        outcome = compile_admitted_capsule(binding, admission)
        assert outcome.ok, outcome.render_explanation()
        assert outcome.result is not None
        delivered.append(Delivered(role, operation, outcome.result, outcome.shared_block))
    return tuple(delivered)


FULL_CURATION_ACTION = "Before running curation checks, read"
FULL_CURATION_CLAUSES = (
    "Use `agents-remember-task`. Run the complete curation check set and report each check as passed, failed, blocked or not run, with its exact command and scope:",
    "- Changed sidecars.",
    "- Affected overviews, indexes and entities.",
    "- `git diff --check` in the memory worktree.",
    "- The full `memory_quality_check` operation on `agents-remember-task`.",
    "A named scoped check never substitutes for the full operation.",
    "Repair every Curator-actionable finding, or escalate it as blocked with its exact returned code.",
    "Run the full operation at intake and after every repair until `curatorActionableCount=0` and the raw `qualityChecklistStatus` is `ready-for-closeout`.",
    "If coherence is then missing or stale, complete the authority gates below before closeout.",
)
PERMISSION_ACTION = "Before acting on a denied or unanswered harness request, read"
PERMISSION_CLAUSES = (
    "When the harness asks to allow a tool call and the request is denied or unanswered, the agent does not reach the same effect by another tool, another command, a sub-agent or a later turn.",
    "At an open request it waits.",
    "After a denial it goes on without that effect and names it in its report.",
)


# Complete substantive clauses on their own required homes, not a union of repeated text.
ROLE_CURRENT_TRUTH = (
    "Affected onboarding states current truth in the leaf's memory worktree.",
    "A correction withdraws or qualifies every affected-body claim it makes false; adding a true paragraph does not answer a contradictory claim.",
)
AUTHORING_TRUTH = (
    "Cards say what is true now.",
    "Decide each sentence of every card and its paired notes, including reference captions, one by one, whatever its tense or location, dated sections included.",
    "Edit individual sentences; never replace a section or regenerate a card wholesale.",
    "When a sentence is true now, keep its wording, also when it states something true about a retired thing.",
    "Before keeping a sentence, verify it in the supplied source.",
    "Before narrowing or dropping one, search the supplied source and memory for what supports it.",
    "A sentence about what happens is true only if that code runs today; code that exists but never runs does not make it true.",
    "Repair or withdraw every false claim in every current carrier found by a bounded search: the card body, paired notes and sibling cards.",
    "Repair the class, not just one example.",
    "Remove past accounts, including dated change notes, “former … was removed” and “The fix: …”, from the card and name them in that card's history row, written after the card's last edit.",
)
DISPOSITIONS = (
    "Before ingesting a row, read the subject's earlier rows.",
    "| changed | Changes the record's meaning and increments its revision. |",
    "| moved | Re-anchors without changing meaning. |",
    "| deleted | Removes entries or retires the invariant. |",
    "| extended | Adds an entry without changing meaning. |",
    "| no_impact | Preserves meaning and the entry set, including admission, origin or slug-only changes. |",
    "Family and onboarding rows retain their own schemas.",
    "A family row uses its family identifier as subject and one of changed, rerouted, assigned, retired or no_impact as disposition.",
    "Its examined field lists each examined member's identifier and revision in the candidate memory at the time of examination.",
    "The family record names members only by identifier, so a member's meaning change does not itself require a new family revision.",
    "If a member's revision changes after the row was written, examine the family again; the row is no longer current.",
)
ROLE_CONTINUATION = (
    "For helper collection and continuation, follow [Parallel work and waiting](../core/every-role.md#parallel-work-and-waiting) in the shared instructions delivered with this role.",
    "Verify helpers' work, report their parts and hand over under your own name.",
    "If the same check or finding still fails after three repairs of your own, stop repairing, record what you tried and what still fails, and tell the Manager.",
    "Your stage ends when task knowledge is written and checked and quality is clean.",
    "Correct your own faulty-input refusal through the same writer.",
    "Repair faults caused by your work or tools, and repair rather than relabel coherence findings.",
    "Report unsatisfied checks as blocked.",
    "Never pass incomplete onboarding.",
    "Stop for open permission prompts, correct-input refusals other than of a covered write, unowned state, requirement-meaning questions or effects outside worktree, scratch and task.",
    "Work from step to step and wave to wave in one turn, collecting helpers' results. End only at the handover, a named wait or a nearly full context.",
    (
        "Outside a helper wait that your launch directs you to handle with the Agent Client Protocol, "
        "if you have a parent and remain unfinished, report what you await and from whom to that "
        "parent with role_message on agents-remember-task. That helper wait follows your shared "
        "instructions."
    ),
    "Only the tool and input class of a covered correct write admit open manual recovery; every other write retains its tool.",
    "A missing writer is not a correct-write refusal: report the required records not written.",
)
HEALING_UNION_CLAUSES = (
    "Also repair wrong or missing memory encountered while curating",
    "whoever left it",
    "beyond the quality check's findings",
    "Use the writer",
    "name each repair",
    "make no broad or unrelated sweep",
    "Route repairs needing code, a requirement decision or another leaf's work.",
)
ROLE_HEALING = (
    *HEALING_UNION_CLAUSES,
    "Mark and route out-of-scope code.",
    "When repairing memory encountered during curation, follow [Repair and finish the stage](#repair-and-finish-the-stage) below for the writer, the named repairs and the routing of repairs you cannot make.",
)
WRITER_IDENTITY = (
    "Prefer the mounted knowledge_write tool.",
    "Before a command-line interface (CLI) run that decides or writes, bind it to the starting server's server_info.servingBuild.",
    "Obtain the original interpreter from toolServer.command[0] in the role-launch receipt",
    "If the admitted launch command is unavailable, report that the CLI binding is unavailable. Do not infer it from the resolved path or install another build.",
    "Compare version, sourceDigest, packageRoot, pythonExecutable and commit with the starting stamp before using this CLI to decide or write.",
    "Keep an unreported commit unknown; never call it a verified clean checkout.",
    'PYTHONPATH="$AR_SERVING_PARENT" "$AR_SERVING_PYTHON" -P -m agents_remember.cli',
    "knowledge-worklist --contract '<this leaf enclosure contract>'",
    "knowledge-ingest --contract '<this leaf enclosure contract>'",
    "--list '<the hand-off list>' --authorization-ref '<the admitted authorization>' --commit --json",
)
PUBLICATION_SEMANTICS = (
    "Without --commit, ingest plans and validates without writing files. --commit authorizes file "
    "writes, never a Git commit or acceptance.",
    "Read the actual report state, written paths, record and entry identifiers, and refusals.",
    "Exit zero covers both a plan and a write.",
    "Even written with no written paths establishes no publication.",
    "The ingest state is written, planned or refused.",
    "A refused ingest wrote nothing and names each problem with its own reason.",
    "Keep a partial handover visibly partial.",
)
SERVING_AUTHORITY = (
    "Decide and write through the starting server or its serving-build command-line interface (CLI).",
    "Development uses clean main and its stated serving commit as the reference build; name it.",
    "Leaf source never produces leaf memory: test leaf writer/check changes on fixtures or scratch.",
    "Install nothing; disable no guard.",
)
WRITER_OPERATION = (
    "A report naming the next authorized step needs no new Manager approval.",
    "A missing capability is not a correct-input refusal.",
    "Verify every output resolves inside the selected memory root.",
    "use the mounted writer or an admitted command-line interface (CLI) bound to the selected serving build.",
)
SEMANTIC_TRUTH = (
    "Check every changed claim, count and prose line reference against code.",
    "A prose line reference must lie inside its citation anchor.",
    "Passing structural checks and authentic anchors do not prove the prose true.",
)
CARD_FACTCHECK = (
    "Before completing the independent fact-check, follow",
    "Semantic or ambiguous items remain Curator decisions; retain the complete decision-item report "
    "in the workspace.",
    "Do not claim an unimplemented citation feature exists.",
)


CITATION_FIX_BOUNDARY = (
    "| `citation_fix` | Repairs moved anchors in admitted converted-memory sidecars. Does not choose renamed/deleted/ambiguous targets.",
)


CRITERIA_TRUTH = (
    "Check every changed claim, count and prose line reference against code; a prose line reference must lie inside its citation anchor.",
    "Passing structural checks and authentic anchors do not establish that the prose is true.",
)

FACTCHECK_OPERATION = (
    "### Obtain the independent fact-check",
    "Before the independent fact-check of a converted-memory leaf, read",
    "A seat that wrote none of the leaf's cards or records performs this check before closeout.",
    "Repair blocking findings before closeout and rerun the full operation after every repair.",
    "This is a lifecycle step; closeout does not perform the check.",
)


FACTCHECK_CRITERIA = (
    "A role agent that wrote none of the leaf's cards or records checks curation before closeout.",
    "On converted memory, check every changed card, sidecar and record statement by statement against the leaf's final code.",
    "Check every history row's disposition, effect and reason against the code diff, and state whether important information about the change was omitted.",
    "Require repair of blocking findings before closeout.",
)


SHARED_CONTINUATION = (
    "Collect your helpers' results and continue the work.",
    "Use the helper-wait procedure declared by your launch; otherwise preserve the harness's native collection of results within the turn.",
)


def expected_bound_detail(text: str, source: Path, targets: dict[Path, Path]) -> bytes:
    """Preserve all text; independently resolve only these details' inline links."""

    def bind(match: re.Match[str]) -> str:
        destination = match[2]
        parts = urlsplit(destination)
        if parts.scheme or destination.startswith("/"):
            return match[0]
        path = (source.parent / unquote(parts.path)).resolve() if parts.path else source.resolve()
        path = targets.get(path, path)
        suffix = "?" + parts.query if parts.query else ""
        suffix += "#" + parts.fragment if parts.fragment else ""
        return match[1] + quote(path.as_posix(), safe="/:@") + suffix + ")"

    return re.sub(r"(\[[^\]\n]+\]\()([^()\s]+)\)", bind, text).encode("utf-8")


def healing_findings(text: str) -> tuple[str, ...]:
    """Reject ownership-only healing and blanket debt routing; do not forbid bounded routing."""
    reading = normalized(text).casefold()
    fault = r"(?:memory\s+)?(?:faults?|mistakes?|errors?|debts?|problems?)"
    own = r"(?:your|the curator[\u2019']?s)\s+own\s+"
    own_fault = rf"{own}(?:{fault}|memory)"
    caused_fault = (
        rf"(?:the\s+)?(?:{fault}|memory)\s+(?:that\s+)?"
        r"(?:you\s+(?:caused|introduced|broke)|your\s+(?:own\s+)?leaf\s+broke)"
    )
    patterns = {
        "own-fault-only": (
            rf"\b(?:(?:repair|fix|heal|correct)\s+(?:only|exclusively)\s+"
            rf"(?:{own_fault}|{caused_fault})|"
            rf"(?:limit|restrict)\s+(?:healing|repairs?)\s+to\s+{own_fault})\b"
        ),
        "route-all-debt": (
            rf"\b(?:route|defer|escalate|report)\s+(?:all|every|any)\s+"
            rf"(?:encountered\s+)?{fault}\s+"
            r"(?:you\s+(?:find|encounter)\s+)?"
            r"(?:away|to\s+(?:the\s+)?(?:manager|owner|another\s+leaf))\b"
        ),
    }
    return tuple(kind for kind, pattern in patterns.items() if re.search(pattern, reading))


def writer_findings(text: str) -> tuple[str, ...]:
    reading = normalized(text).casefold()
    findings = []
    command = r"agents-remember[ \t]+knowledge-(?:ingest|bootstrap)\b"
    if re.search(rf"(?m)^[ \t]*(?:[$>] ?)?{command}|(?<![\w./-]){command}[ \t]+(?=--)", text):
        findings.append("bare-PATH-writer")
    if re.search(
        r"\buse\s+(?:the\s+)?(?:leaf|candidate|worktree)\s+(?:writer|build)\s+to\s+(?:write|produce|author)\s+(?:this\s+)?leaf[\u2019']?s\s+memory\b",
        reading,
    ):
        findings.append("leaf-self-production")
    return tuple(findings)
