"""MIK-R116 evidence 6 and 10: no text names a review-round number.

Evidence 6: a test over the role sources and over the compiled texts of every
harness fails when a place names a number of review rounds, and the texts of
the Worker, the Reviewer, the Curator and the Manager hold the section of rule 6
with the parts of rules 6 to 8.

Evidence 10: the same test holds the texts of the Reviewer, the Worker and the
Manager to the sentences of rule 9.

The two skill trees are the canonical ``skills/`` tree and its MCP package-data
copy. ``test_sync_scripts.py`` pins their byte equality. Compiled checks cover
every declared role operation and bind linked procedures to the same skill tree.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from agents_remember.application.role_capsules.compilation import (
    compile_admitted_capsule,
)
from agents_remember.application.role_capsules.sources import CapsuleAdmissionRequest
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.models.role_capsules.manifest import parse_composition_manifest
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleLauncherSeat,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from test_role_capsule_budgets import matrix_keys

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SKILLS = REPOSITORY_ROOT / "skills"
PACKAGED_SKILLS = (
    REPOSITORY_ROOT / "mcp" / "src" / "agents_remember" / "package_data" / "runtime" / "skills"
)
# MIK-R196 rule 7: the eight harness mirrors are gone, so the canonical tree and its packaged
# serving copy are the only two skill trees any wording claim can still hold over.
STARTER_SKILLS: tuple[Path, ...] = ()
SKILL_TREES = (CANONICAL_SKILLS, PACKAGED_SKILLS, *STARTER_SKILLS)
LIFECYCLE = "l-01-agent-lifecycles"
MANIFEST_RELATIVE = "composition-manifest.json"

#: Role sources in packet scope: roles/, operations/, core/loop.md, templates/.
SCOPED = ("roles", "operations", "templates")
SCOPED_FILES = ("core/loop.md",)

#: Cardinal numbers in word and digit form. Ordinals (first, second, third)
#: never express a limit — "the third round" is a round label — and stay out.
#: The word grammar is generative, not a spelling list: ones, teens, tens,
#: hyphenated or spaced compounds (twenty-one), and hundred/thousand scales
#: with an optional remainder ("one hundred and five"). Digits cover every
#: magnitude alongside it; the word grammar runs past any number a role text
#: would ever claim as a limit.
_ONES = r"(?:one|two|three|four|five|six|seven|eight|nine)"
_TEENS = (
    r"(?:ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen)"
)
_TENS = r"(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
_TWO_DIGIT_WORD = rf"(?:{_TEENS}|{_TENS}(?:[ -]{_ONES})?|{_ONES})"
_NUMBER_WORD = (
    rf"(?:{_TWO_DIGIT_WORD}(?:\s+(?:hundred|thousand)"
    rf"(?:\s+and\s+{_TWO_DIGIT_WORD})?)?)"
)
_NUMBER = rf"(?:{_NUMBER_WORD}|\d+)"

#: Phrasings that name a number of review rounds. Each is a limit-naming shape
#: the packet removes; round labels without a limit claim (``round 2``,
#: ``reviews 2 and 3``, ``round four begins``) and granted allowances (``two
#: extra rounds``) are out of this rule's scope.
ROUND_NUMBER_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # A limit quantifier with a number and rounds: "at most five review
        # rounds", "at most 8 rounds", "maximum of three rounds".
        rf"\b(?:at most|at least|up to|maximum(?: of)?|max(?:imum)?|no more than|"
        rf"fewer than|or fewer)\s+{_NUMBER}\s+(?:review\s+)?rounds?\b",
        # "five is the ordinary maximum", "three is the ordinary maximum".
        rf"\b{_NUMBER}\s+is the ordinary maximum\b",
        # A bare number of (review) rounds: "four review rounds". The optional
        # kind word is a closed set of round kinds (loop, verification); granted
        # allowances ("two extra rounds") and ordinary prose ("the one that
        # round") never match it.
        rf"\b{_NUMBER}\s+(?:(?:loop|fix-verification|verification)\s+)?"
        rf"(?:review\s+)?rounds?\b",
        # Hyphenated limit adjectives: "three-round", "five-round".
        rf"\b{_NUMBER}-round\b",
        # The known fix-verification limit shape.
        rf"\b{_NUMBER}\s+fix-verification\s+rounds?\b",
    )
)

RETIRED_WORKER_CHECKS = "the focused tests, the lint and the type check pass on its final bytes"

#: Stops every stage section names (rule 8): the permission prompt, the
#: product guard, the meaning question, the outside-workspace effect.
STOPS = (
    "You stop for an open permission prompt",
    "a refusal by a product guard",
    "a question about what the requirement means",
    "an effect outside your own worktree",
)

#: Rule 6 end states, rule 7 repair-own-slips and rule 8 stops, per role. Every
#: facet keeps its identity below and is bound to its current owning section.
STAGE_PARTS = {
    "worker": (
        "Your stage ends when the candidate meets the requirement",
        "the handover checks named in `system/coding-guidelines.md` under `workspace.memoryRoot` pass on a clean copy of the frozen candidate",
        "the reports are with the Reviewer and the Manager",
        "you repair, and you go on",
        "Your report names what you repaired",
        "tell that parent what and whom you await with `role_message` on `agents-remember-task`",
        "the same Reviewer checks it on the round it last recorded, with no new round",
        *STOPS,
    ),
    "reviewer": (
        "Your stage ends when a verdict over the whole requirement",
        "with its findings and its coverage",
        "you repair, and you go on",
        "Repair faults in your work or instruments",
        "Product guards and commit gates keep their stops",
        "Your verdict names what you repaired",
        "tell that parent what and whom you await with `role_message` on `agents-remember-task`",
        "check the repaired record on the round you last recorded",
        "byte for byte",
        *STOPS,
    ),
    "curator": (
        "Your stage ends when task knowledge is written and checked and quality is clean. "
        "Distinguish installed-writer publication from scoped manual provenance.",
        "quality is clean.",
        "Repair faults your work or instrument causes:",
        "Report your repairs.",
        "If unfinished, report what you await and from whom to your parent via `role_message` "
        "on `agents-remember-task`.",
        "Stop for open permission prompts",
        "Stop for open permission prompts, correct-input refusals other than of a covered write, "
        "unowned state, requirement-meaning questions or effects outside worktree/scratch/task.",
        "requirement-meaning questions",
        "effects outside worktree/scratch/task",
    ),
    "manager": (
        "You hand a stage over whole",
        "the sync to the master's line",
        "you repair, and you go on",
        "Your report names what you repaired",
        "Report an outside-authority block and record a working-child wait",
        *STOPS,
    ),
}

# Current source homes for the existing rule 6-8 facet identities above.
# Linked procedures are checked through their explicit role pointer, never as
# an alternative substring anywhere in the role or compiled text.
_WORKER_STAGE_END = (
    "Implement one leaf's approved requirement and preservation rules in its paired code "
    "worktree (memory read-only)."
)
_WORKER_HANDOVER_CHECKS = (
    "Pass `system/coding-guidelines.md` handover checks on a clean freeze copy "
    "(record check/tree/result; fix first)."
)
_WORKER_REPORTS = (
    "2. Send Reviewer report path (no pasted text), head/hash + next stretch via `role_message`."
)
_WORKER_REPAIR = (
    "Repair own/instrument breaks (name repairs in report); fix faulty input on same tool; "
    "stop after 3 failed repairs on one item and tell Manager."
)
_WORKER_REPAIR_REPORT = "Repair own/instrument breaks (name repairs in report)"
_WORKER_FREEZE_REPAIR_CLAUSE = "own/instrument repairs made"
_WORKER_RECORD = (
    "The candidate stays unchanged, and the same Reviewer checks the record on its last "
    "recorded round, without opening a new round."
)
_STOP_BOUNDARIES = (
    "Stop for an open permission prompt, a standing refusal outside covered-write recovery, "
    "a question about what the requirement means, or an effect outside the assigned worktree, "
    "scratch folder, and task."
)
_UNFINISHED_PARENT_NOTICE = (
    "If you have a parent and remain unfinished at turn end, tell that parent what and whom "
    "you await via `role_message`."
)
_REVIEWER_REPAIR_AND_STOPS = (
    "Repair faults in your work or instruments, including mistaken records, wrong evidence "
    "anchors and misread candidate identities, and continue. Correct input faults through "
    "the same tool; name repairs in the verdict. Product guards, read/check/validation/sync "
    "refusals and commit gates keep their stops."
)
_CURATOR_REPAIR = (
    "Fix faulty inputs on same writer, repair own/tool/coherence faults, "
    "stop after 3 failed repairs on one item and tell Manager."
)
_CURATOR_REPAIR_REPORT = (
    "Apply bounded encountered-memory repairs—whoever left it, beyond the quality check's "
    "findings—without a broad sweep, report every repair, and rerun complete checks and the "
    "independent fact-check before handover."
)
_MANAGER_STOPS_AND_REPAIR = (
    "Repair own mistakes and name repairs in the report; stop for open permissions, "
    "standing refusals (except covered-write recovery), out-of-authority questions, or "
    "outside effects."
)
_MANAGER_NOTICE = (
    "While a child works or awaiting an already-sent developer question, record wait locally "
    "in end line and status file without sending a second wait message."
)

STAGE_CONTRACTS = {
    "worker": {
        "Your stage ends when the candidate meets the requirement": (
            "role",
            "# Worker",
            _WORKER_STAGE_END,
            _WORKER_STAGE_END,
        ),
        "the handover checks named in `system/coding-guidelines.md` under `workspace.memoryRoot` pass on a clean copy of the frozen candidate": (
            "operations/implementation.md",
            "# Operation — Implementation",
            _WORKER_HANDOVER_CHECKS,
            _WORKER_HANDOVER_CHECKS,
        ),
        "the reports are with the Reviewer and the Manager": (
            "operations/implementation.md",
            "# Operation — Implementation",
            _WORKER_REPORTS,
            _WORKER_REPORTS,
        ),
        "you repair, and you go on": (
            "role",
            "# Worker",
            _WORKER_REPAIR,
            "Repair own/instrument breaks (name repairs in report); fix faulty input on same tool;",
        ),
        "Your report names what you repaired": (
            "role",
            "# Worker",
            _WORKER_REPAIR_REPORT,
            "name repairs in report",
        ),
        "tell that parent what and whom you await with `role_message` on `agents-remember-task`": (
            "reference/worker/sync-and-repairs.md",
            "## Worker stops, unfinished turns, and step marking",
            _UNFINISHED_PARENT_NOTICE,
            "tell that parent what and whom you await via `role_message`.",
        ),
        "the same Reviewer checks it on the round it last recorded, with no new round": (
            "reference/worker/sync-and-repairs.md",
            "## Repair limits, costly small repairs, and record-only repairs",
            _WORKER_RECORD,
            _WORKER_RECORD,
        ),
        "You stop for an open permission prompt": (
            "reference/worker/sync-and-repairs.md",
            "## Worker stops, unfinished turns, and step marking",
            _STOP_BOUNDARIES,
            "an open permission prompt",
        ),
        "a refusal by a product guard": (
            "reference/worker/sync-and-repairs.md",
            "## Worker stops, unfinished turns, and step marking",
            _STOP_BOUNDARIES,
            "a standing refusal outside covered-write recovery",
        ),
        "a question about what the requirement means": (
            "reference/worker/sync-and-repairs.md",
            "## Worker stops, unfinished turns, and step marking",
            _STOP_BOUNDARIES,
            "a question about what the requirement means",
        ),
        "an effect outside your own worktree": (
            "reference/worker/sync-and-repairs.md",
            "## Worker stops, unfinished turns, and step marking",
            _STOP_BOUNDARIES,
            "an effect outside the assigned worktree, scratch folder, and task",
        ),
    },
    "reviewer": {
        "Your stage ends when a verdict over the whole requirement": (
            "operations/review.md",
            "## Outputs",
            "Baseline covers full diff (tests != acceptance);",
            "Baseline covers full diff",
        ),
        "with its findings and its coverage": (
            "operations/review.md",
            "## Outputs",
            "Verdict: Catalog Binding, recommendation, checklist, coverage, findings",
            "coverage, findings",
        ),
        "you repair, and you go on": (
            "role",
            "## Boundaries",
            _REVIEWER_REPAIR_AND_STOPS,
            "Repair faults in your work or instruments, including mistaken records, wrong evidence anchors and misread candidate identities, and continue.",
        ),
        "Repair faults in your work or instruments": (
            "role",
            "## Boundaries",
            _REVIEWER_REPAIR_AND_STOPS,
            "Repair faults in your work or instruments, including mistaken records, wrong evidence anchors and misread candidate identities, and continue.",
        ),
        "Product guards and commit gates keep their stops": (
            "role",
            "## Boundaries",
            _REVIEWER_REPAIR_AND_STOPS,
            "Product guards, read/check/validation/sync refusals and commit gates keep their stops.",
        ),
        "Your verdict names what you repaired": (
            "role",
            "## Boundaries",
            _REVIEWER_REPAIR_AND_STOPS,
            "name repairs in the verdict.",
        ),
        "tell that parent what and whom you await with `role_message` on `agents-remember-task`": (
            "reference/review/rounds-and-late-findings.md",
            "## Review stops, unfinished turns, and step marking",
            _UNFINISHED_PARENT_NOTICE,
            "tell that parent what and whom you await via `role_message`.",
        ),
        "check the repaired record on the round you last recorded": (
            "reference/review/rounds-and-late-findings.md",
            "## Recheck a repaired Worker record",
            "When all candidate findings are closed and only a finding on the Worker's record remains, check the repaired record on the last recorded round. The record may be its report, evidence table, hash or anchor.",
            "check the repaired record on the last recorded round.",
        ),
        "byte for byte": (
            "reference/review/rounds-and-late-findings.md",
            "## Recheck a repaired Worker record",
            "Do not call `begin_review`; the round count stays unchanged. This applies only while the candidate is byte-for-byte the candidate that round recorded.",
            "byte-for-byte",
        ),
        "You stop for an open permission prompt": (
            "reference/review/rounds-and-late-findings.md",
            "## Review stops, unfinished turns, and step marking",
            _STOP_BOUNDARIES,
            "an open permission prompt",
        ),
        "a refusal by a product guard": (
            "reference/review/rounds-and-late-findings.md",
            "## Review stops, unfinished turns, and step marking",
            _STOP_BOUNDARIES,
            "a standing refusal outside covered-write recovery",
        ),
        "a question about what the requirement means": (
            "reference/review/rounds-and-late-findings.md",
            "## Review stops, unfinished turns, and step marking",
            _STOP_BOUNDARIES,
            "a question about what the requirement means",
        ),
        "an effect outside your own worktree": (
            "reference/review/rounds-and-late-findings.md",
            "## Review stops, unfinished turns, and step marking",
            _STOP_BOUNDARIES,
            "an effect outside the assigned worktree, scratch folder, and task",
        ),
    },
    "curator": {
        "Your stage ends when task knowledge is written and checked and quality is clean. Distinguish installed-writer publication from scoped manual provenance.": (
            "reference/refused-write.md",
            "## Follow the same-tool ladder",
            "claim no tool receipt for manual work.",
            "claim no tool receipt for manual work.",
        ),
        "quality is clean.": (
            "operations/curation.md",
            "### Checks and gates",
            "full `memory_quality_check` (scoped checks never substitute; rerun until `curatorActionableCount=0` + `qualityChecklistStatus=ready-for-closeout`).",
            "`curatorActionableCount=0` + `qualityChecklistStatus=ready-for-closeout`",
        ),
        "Repair faults your work or instrument causes:": (
            "role",
            "## Boundaries",
            _CURATOR_REPAIR,
            "repair own/tool/coherence faults",
        ),
        "Report your repairs.": (
            "reference/curator/exceptions-and-recovery.md",
            "## Memory-tool failures, code concerns, and sync conflicts",
            _CURATOR_REPAIR_REPORT,
            "report every repair",
        ),
        "If unfinished, report what you await and from whom to your parent via `role_message` on `agents-remember-task`.": (
            "reference/curator/exceptions-and-recovery.md",
            "## Memory-tool failures, code concerns, and sync conflicts",
            _UNFINISHED_PARENT_NOTICE,
            "tell that parent what and whom you await via `role_message`.",
        ),
        "Stop for open permission prompts": (
            "reference/curator/exceptions-and-recovery.md",
            "## Memory-tool failures, code concerns, and sync conflicts",
            _STOP_BOUNDARIES,
            "an open permission prompt",
        ),
        "Stop for open permission prompts, correct-input refusals other than of a covered write, unowned state, requirement-meaning questions or effects outside worktree/scratch/task.": (
            "reference/curator/exceptions-and-recovery.md",
            "## Memory-tool failures, code concerns, and sync conflicts",
            _STOP_BOUNDARIES,
            "a standing refusal outside covered-write recovery",
        ),
        "requirement-meaning questions": (
            "reference/curator/exceptions-and-recovery.md",
            "## Memory-tool failures, code concerns, and sync conflicts",
            _STOP_BOUNDARIES,
            "a question about what the requirement means",
        ),
        "effects outside worktree/scratch/task": (
            "reference/curator/exceptions-and-recovery.md",
            "## Memory-tool failures, code concerns, and sync conflicts",
            _STOP_BOUNDARIES,
            "an effect outside the assigned worktree, scratch folder, and task",
        ),
    },
    "manager": {
        "You hand a stage over whole": (
            "role",
            "## Start leaf agents and preserve their work",
            "Hand over a whole stage, not a chain of steps.",
            "Hand over a whole stage, not a chain of steps.",
        ),
        "the sync to the master's line": (
            "reference/worker/sync-and-repairs.md",
            "## Synchronize the leaf",
            "When the master line moves, the Worker runs `worktree_sync` for its own leaf.",
            "When the master line moves, the Worker runs `worktree_sync` for its own leaf.",
        ),
        "you repair, and you go on": (
            "role",
            "## Leaf notices, reporting, and boundaries",
            _MANAGER_STOPS_AND_REPAIR,
            "Repair own mistakes",
        ),
        "Your report names what you repaired": (
            "role",
            "## Leaf notices, reporting, and boundaries",
            _MANAGER_STOPS_AND_REPAIR,
            "name repairs in the report",
        ),
        "Report an outside-authority block and record a working-child wait": (
            "role",
            "## Leaf notices, reporting, and boundaries",
            _MANAGER_NOTICE,
            "record wait locally in end line and status file without sending a second wait message.",
        ),
        "You stop for an open permission prompt": (
            "role",
            "## Leaf notices, reporting, and boundaries",
            _MANAGER_STOPS_AND_REPAIR,
            "open permissions",
        ),
        "a refusal by a product guard": (
            "role",
            "## Leaf notices, reporting, and boundaries",
            _MANAGER_STOPS_AND_REPAIR,
            "standing refusals (except covered-write recovery)",
        ),
        "a question about what the requirement means": (
            "role",
            "## Leaf notices, reporting, and boundaries",
            _MANAGER_STOPS_AND_REPAIR,
            "out-of-authority questions",
        ),
        "an effect outside your own worktree": (
            "role",
            "## Leaf notices, reporting, and boundaries",
            _MANAGER_STOPS_AND_REPAIR,
            "outside effects",
        ),
    },
}

ROLE_INDEX_POINTERS = {
    "worker": "[sync]({{detail:reference/worker/sync-and-repairs.md}})",
    "reviewer": "[rounds]({{detail:reference/review/rounds-and-late-findings.md}})",
    "curator": "[exceptions]({{detail:reference/curator/exceptions-and-recovery.md}})",
    "manager": "[sync]({{detail:reference/worker/sync-and-repairs.md}}#synchronize-the-leaf)",
}

REVIEWER_ROUNDS_LIMIT_CLAUSE = (
    "Read the current review-state limit from the `task_doc` answer. "
    "Never run an unapproved round or split the scope to avoid that limit."
)
REVIEWER_NON_LEAF_RECORDING_CLAUSE = "Non-leaf seams retain their owner's recording act."

#: Rule 9 sentences (evidence 10), keyed by role and owning surface(s).
RULE_NINE: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "reviewer": (
        ("roles/reviewer.md", ("state broken property.",)),
        (
            "reference/review/rounds-and-late-findings.md",
            (
                "State the broken property itself: the case demonstrating the failure is its "
                "proof, not the finding itself.",
            ),
        ),
    ),
    "worker": (
        (
            "reference/worker/sync-and-repairs.md",
            (
                "When a finding stays open after a repair, the next repair must not merely answer "
                "the new case.",
                "Write down the property for every case, either show why the construction holds it "
                "or change the construction, and state which you did in the report.",
            ),
        ),
    ),
    "manager": (
        (
            "roles/manager.md",
            (
                "Request extra rounds via reporting route with the Worker's account of the "
                "property and the construction behind the open finding, and record approval.",
            ),
        ),
    ),
}

_COMPILED: dict[tuple[str, str, str], str] = {}


def _role_operations(role: str) -> list[str]:
    """The operations the composition manifest yields for one role."""
    return [key.split("/", 1)[1] for key in matrix_keys() if key.startswith(f"{role}/")]


def _flatten(text: str) -> str:
    return " ".join(text.split())


def _scoped_files(tree: Path) -> list[Path]:
    root = tree / LIFECYCLE
    paths: list[Path] = []
    for dirname in SCOPED:
        paths.extend(sorted((root / dirname).glob("*.md")))
    paths.extend(root / name for name in SCOPED_FILES)
    return paths


def _number_hits(text: str) -> list[str]:
    flat = _flatten(text)
    return sorted({pattern.pattern for pattern in ROUND_NUMBER_PATTERNS if pattern.search(flat)})


def _compile(lifecycle_root: Path, role: str, operation: str) -> str:
    key = (str(lifecycle_root), role, operation)
    if key not in _COMPILED:
        manifest_bytes = (lifecycle_root / MANIFEST_RELATIVE).read_bytes()
        parsed = parse_composition_manifest(manifest_bytes)
        if role == "launcher":
            seat: CapsuleRoleSeat | CapsuleLauncherSeat = CapsuleLauncherSeat(
                routing_condition=parsed.launcher.routing_condition
            )
            request = CapsuleAdmissionRequest(
                root=lifecycle_root,
                manifest=MANIFEST_RELATIVE,
                core=tuple(parsed.core[name].source for name in parsed.launcher.core),
                operation=(parsed.operations[operation].source,),
                skill=tuple(entry.source for entry in parsed.skills.values()),
            )
        else:
            seat = CapsuleRoleSeat(role=role, altitude=parsed.roles[role].altitude)  # type: ignore[arg-type]
            request = routed_admission_for(
                lifecycle_root,
                MANIFEST_RELATIVE,
                manifest_bytes,
                CapsuleSeatAddress(role=role, operation=operation),  # type: ignore[arg-type]
            )
        binding = CapsuleBinding(
            operation=operation,  # type: ignore[arg-type]
            admitted=CapsuleAdmittedFacts(
                task_reference="agents-remember/tasks/demo/task.json",
                task_document_digest=compute_content_digest(b'{"id": "demo"}'),
                seat=seat,
                repository_id="agents-remember",
                work_branch="fixture",
                tool_policy=CapsuleToolPolicy(granted=frozenset(PUBLIC_TOOLS)),
            ),
        )
        outcome = compile_admitted_capsule(binding, request)
        assert outcome.ok, outcome.render_explanation()
        assert outcome.result is not None
        _COMPILED[key] = outcome.result.render_instructions()
    return _COMPILED[key]


def test_no_role_source_names_a_review_round_number() -> None:
    """Evidence 6, sources: every place that named the number is listed."""
    offending: list[str] = []
    for tree in SKILL_TREES:
        for path in _scoped_files(tree):
            hits = _number_hits(path.read_text(encoding="utf-8"))
            for hit in hits:
                offending.append(f"{path}: names a review-round number ({hit})")
    assert offending == [], "\n".join(offending)


def test_no_compiled_capsule_names_a_review_round_number() -> None:
    """Evidence 6, compiled texts: the packaged matrix names no number."""
    packaged = PACKAGED_SKILLS / LIFECYCLE
    offending: list[str] = []
    for key in matrix_keys():
        role, operation = key.split("/", 1)
        for hit in _number_hits(_compile(packaged, role, operation)):
            offending.append(f"capsule {key}: names a review-round number ({hit})")
    assert offending == [], "\n".join(offending)


def _section_of(text: str, heading: str) -> str | None:
    """Return the named owning section, never text from a later section."""
    start = text.find(heading + "\n")
    if start < 0:
        return None
    body = text[start + len(heading) :]
    level = len(heading) - len(heading.lstrip("#"))
    stop_level = max(level, 2)
    match = re.search(rf"\n#{{1,{stop_level}}} ", body)
    return body if match is None else body[: match.start()]


def _stage_documents(root: Path, role: str, text: str) -> dict[str, str]:
    documents = {"role": text}
    for owner, _heading, _clause, _deletion in STAGE_CONTRACTS[role].values():
        if owner != "role" and owner not in documents:
            documents[owner] = (root / owner).read_text(encoding="utf-8")
    return documents


def _section_missing(documents: dict[str, str], role: str) -> list[str]:
    """Each original facet must hold at its current authoritative location."""
    missing = []
    for facet, (owner, heading, clause, _deletion) in STAGE_CONTRACTS[role].items():
        section = _section_of(documents[owner], heading)
        if section is None or _flatten(clause) not in _flatten(section):
            missing.append(facet)
    index_section = _section_of(documents["role"], "## Reference index")
    pointer = ROLE_INDEX_POINTERS[role]
    if index_section is None or _flatten(pointer) not in _flatten(index_section):
        missing.append("reference index pointer")
    if role == "reviewer":
        rounds = documents["reference/review/rounds-and-late-findings.md"]
        limit_section = _section_of(rounds, "## Verify repaired findings at the round limit")
        recheck_section = _section_of(rounds, "## Recheck a repaired Worker record")
        if (
            limit_section is None
            or _flatten(REVIEWER_ROUNDS_LIMIT_CLAUSE) not in _flatten(limit_section)
            or recheck_section is None
            or _flatten(REVIEWER_NON_LEAF_RECORDING_CLAUSE) not in _flatten(recheck_section)
        ):
            missing.append("record-only procedure pointer")
    if role == "worker":
        freeze_section = _section_of(
            documents["reference/worker/sync-and-repairs.md"],
            "## Focused test selection and freeze report details",
        )
        if freeze_section is None or _flatten(_WORKER_FREEZE_REPAIR_CLAUSE) not in _flatten(
            freeze_section
        ):
            missing.append("worker freeze-report own repairs")
        for owner, heading in (
            ("role", "# Worker"),
            ("operations/implementation.md", "# Operation — Implementation"),
        ):
            section = _section_of(documents[owner], heading)
            if section is not None and RETIRED_WORKER_CHECKS in _flatten(section):
                missing.append("retired focused/lint/type handover rule")
                break
    return missing


def _mutate_section(documents: dict[str, str], owner: str, heading: str, mutate) -> dict[str, str]:
    changed = dict(documents)
    text = changed[owner]
    section = _section_of(text, heading)
    assert section is not None
    start = text.index(heading + "\n") + len(heading)
    changed[owner] = text[:start] + mutate(section) + text[start + len(section) :]
    return changed


def test_worker_stage_requires_named_clean_copy_checks_and_refuses_the_retired_rule() -> None:
    root = CANONICAL_SKILLS / LIFECYCLE
    documents = _stage_documents(root, "worker", (root / "roles/worker.md").read_text())
    facet = STAGE_PARTS["worker"][1]
    assert facet not in _section_missing(documents, "worker")
    assert "retired focused/lint/type handover rule" not in _section_missing(documents, "worker")
    owner, heading, live, _deletion = STAGE_CONTRACTS["worker"][facet]
    replaced = _mutate_section(
        documents,
        owner,
        heading,
        lambda section: section.replace(live, RETIRED_WORKER_CHECKS),
    )
    assert replaced != documents
    assert facet in _section_missing(replaced, "worker")
    assert "retired focused/lint/type handover rule" in _section_missing(replaced, "worker")
    appended = _mutate_section(
        documents,
        owner,
        heading,
        lambda section: section + "\n" + RETIRED_WORKER_CHECKS,
    )
    assert "retired focused/lint/type handover rule" in _section_missing(appended, "worker")


def _check_source_sections(role: str, parts: tuple[str, ...], missing: list[str]) -> None:
    assert tuple(STAGE_CONTRACTS[role]) == parts
    for tree in SKILL_TREES:
        root = tree / LIFECYCLE
        path = root / "roles" / f"{role}.md"
        documents = _stage_documents(root, role, path.read_text(encoding="utf-8"))
        for gap in _section_missing(documents, role):
            missing.append(f"{path}: owning section {gap}")


def _check_compiled_sections(role: str, parts: tuple[str, ...], missing: list[str]) -> None:
    assert tuple(STAGE_CONTRACTS[role]) == parts
    for root in (CANONICAL_SKILLS / LIFECYCLE, PACKAGED_SKILLS / LIFECYCLE):
        for operation in _role_operations(role):
            documents = _stage_documents(root, role, _compile(root, role, operation))
            for gap in _section_missing(documents, role):
                missing.append(f"compiled {root} {role}/{operation}: owning section {gap}")


@pytest.mark.parametrize("role", STAGE_PARTS)
def test_stage_end_sections_hold_in_sources_and_compiled_texts(role: str) -> None:
    """Evidence 6, rules 6 to 8: every facet holds in its current source home."""
    missing: list[str] = []
    parts = STAGE_PARTS[role]
    _check_source_sections(role, parts, missing)
    _check_compiled_sections(role, parts, missing)
    assert missing == [], "\n".join(missing)


def test_number_detector_catches_limit_forms_and_exempts_labels() -> None:
    """The oracle defends the prohibited-number class, not a spelling list."""
    forbidden = [
        "five is the ordinary maximum",
        "three is the ordinary maximum",
        "fifteen is the ordinary maximum",
        "at most five review rounds",
        "at most 8 rounds",
        "at most twenty rounds",
        "thirteen review rounds",
        "four review rounds",
        "twenty-one review rounds",
        "one hundred rounds",
        "maximum of three rounds",
        "three-round review limit",
        "two fix-verification rounds",
    ]
    for text in forbidden:
        assert _number_hits(text), f"limit form not detected: {text!r}"
    allowed = [
        "round 2",
        "reviews 2 and 3 verify the fix",
        "round four begins",
        "Round 1 opens the loop",
        "two extra rounds",
        "two additional rounds",
        "seven under five",
        "the third round",
        "the one that round was recorded on",
        "a single seam or loop round",
        "one hundred percent",
        "twenty questions",
        "begin_review",
    ]
    for text in allowed:
        assert _number_hits(text) == [], f"label or allowance flagged: {text!r}"


def _assert_facet_mutations(documents: dict[str, str], role: str) -> None:
    assert _section_missing(documents, role) == [], role
    for facet, (owner, heading, _clause, deletion) in STAGE_CONTRACTS[role].items():
        section = _section_of(documents[owner], heading)
        assert section is not None
        assert _flatten(deletion) in _flatten(section), "control must delete the named facet"
        deleted = _mutate_section(
            documents,
            owner,
            heading,
            lambda section, deletion=deletion: (
                "\n" + _flatten(section).replace(_flatten(deletion), "", 1) + "\n"
            ),
        )
        assert deleted != documents, f"{role}: deletion must change text"
        assert facet in _section_missing(deleted, role), f"{role}: deleting {facet!r} still holds"
        relocated = dict(deleted)
        relocated[owner] += "\n\n## Elsewhere\n\n" + deletion
        assert facet in _section_missing(relocated, role), (
            f"{role}: relocating {facet!r} still holds"
        )
    pointer = ROLE_INDEX_POINTERS[role]
    deleted = _mutate_section(
        documents,
        "role",
        "## Reference index",
        lambda section, pointer=pointer: (
            "\n" + _flatten(section).replace(_flatten(pointer), "", 1) + "\n"
        ),
    )
    assert deleted != documents
    assert "reference index pointer" in _section_missing(deleted, role)
    relocated = dict(deleted)
    relocated["role"] += "\n\n## Elsewhere\n\n" + pointer
    assert "reference index pointer" in _section_missing(relocated, role)
    if role == "worker":
        deleted = _mutate_section(
            documents,
            "reference/worker/sync-and-repairs.md",
            "## Focused test selection and freeze report details",
            lambda section: (
                "\n"
                + _flatten(section).replace(_flatten(_WORKER_FREEZE_REPAIR_CLAUSE), "", 1)
                + "\n"
            ),
        )
        assert deleted != documents
        assert "worker freeze-report own repairs" in _section_missing(deleted, role)
        relocated = dict(deleted)
        relocated["reference/worker/sync-and-repairs.md"] += (
            "\n\n## Elsewhere\n\n" + _WORKER_FREEZE_REPAIR_CLAUSE
        )
        assert "worker freeze-report own repairs" in _section_missing(relocated, role)
    if role == "reviewer":
        deleted = _mutate_section(
            documents,
            "reference/review/rounds-and-late-findings.md",
            "## Verify repaired findings at the round limit",
            lambda section: (
                "\n"
                + _flatten(section).replace(_flatten(REVIEWER_ROUNDS_LIMIT_CLAUSE), "", 1)
                + "\n"
            ),
        )
        assert deleted != documents
        assert "record-only procedure pointer" in _section_missing(deleted, role)
        relocated = dict(deleted)
        relocated["reference/review/rounds-and-late-findings.md"] += (
            "\n\n## Elsewhere\n\n" + REVIEWER_ROUNDS_LIMIT_CLAUSE
        )
        assert "record-only procedure pointer" in _section_missing(relocated, role)


@pytest.mark.parametrize("role", STAGE_PARTS)
def test_stage_section_binding_fails_on_deletion_and_relocation(role: str) -> None:
    """Every facet, deleted or moved out of its owning section, fails."""
    root = CANONICAL_SKILLS / LIFECYCLE
    original = (root / "roles" / f"{role}.md").read_text()
    _assert_facet_mutations(_stage_documents(root, role, original), role)


@pytest.mark.parametrize("role", STAGE_PARTS)
def test_compiled_section_binding_fails_on_deletion_and_relocation(role: str) -> None:
    """The same owning-section proof on one compiled capsule per role."""
    root = CANONICAL_SKILLS / LIFECYCLE
    operation = _role_operations(role)[0]
    compiled = _compile(root, role, operation)
    _assert_facet_mutations(_stage_documents(root, role, compiled), role)


def test_rule_nine_sentences_hold_for_reviewer_worker_manager() -> None:
    """Evidence 10: the rule 9 sentences hold in sources and compiled texts."""
    missing: list[str] = []
    root = CANONICAL_SKILLS / LIFECYCLE
    for role, homes in RULE_NINE.items():
        for owner_path, sentences in homes:
            text = _flatten((root / owner_path).read_text(encoding="utf-8"))
            for sentence in sentences:
                if _flatten(sentence) not in text:
                    missing.append(f"{owner_path}: missing {sentence!r}")
            for operation in _role_operations(role):
                compiled = _flatten(_compile(root, role, operation))
                if owner_path.startswith("roles/"):
                    for sentence in sentences:
                        if _flatten(sentence) not in compiled:
                            missing.append(f"compiled {role}/{operation}: missing {sentence!r}")
                else:
                    pointer = f"{{{{detail:{owner_path}}}}}"
                    if pointer not in compiled:
                        missing.append(f"compiled {role}/{operation}: missing pointer {pointer!r}")
    assert missing == [], "\n".join(missing)
