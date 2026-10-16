"""MIK-R116 evidence 6 and 10: no text names a review-round number.

Evidence 6: a test over the role sources and over the compiled texts of every
harness fails when a place names a number of review rounds, and the texts of
the Worker, the Reviewer, the Curator and the Manager hold the section of rule 6
with the parts of rules 6 to 8.

Evidence 10: the same test holds the texts of the Reviewer, the Worker and the
Manager to the sentences of rule 9.

The nine skill trees are the canonical ``skills/`` tree plus the nine copies
``scripts/sync-skills.py`` projects (the MCP package data and the eight harness
starter packages); ``test_sync_scripts.py`` pins their byte equality, and the
compiled capsule is read from the packaged copy the role start uses.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
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
STARTER_SKILLS = tuple(
    CANONICAL_SKILLS.parent / dirname
    for dirname in (
        ".claude/skills",
        ".codex/skills",
        ".cursor/skills",
        ".github-vscode/skills",
        ".hermes/skills",
        ".openclaw/workspace/skills",
        ".pi/skills",
        ".agents/skills",
    )
)
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

SECTION_HEADING = "## Where your stage ends"

#: Stops every stage section names (rule 8): the permission prompt, the
#: product guard, the meaning question, the outside-workspace effect.
STOPS = (
    "You stop for an open permission prompt",
    "a refusal by a product guard",
    "a question about what the requirement means",
    "an effect outside your own worktree",
)

#: Rule 6 end states, rule 7 repair-own-slips and rule 8 stops, per role. Every
#: facet must hold inside that role's single stage-end section, not merely
#: somewhere in the file.
STAGE_PARTS = {
    "worker": (
        "Your stage ends when the candidate meets the requirement",
        "the focused tests, the lint and the type check pass on its final bytes",
        "the report is with the Reviewer",
        "you repair, and you go on",
        "Your report names what you repaired",
        "says in its last line what it waits for and from whom",
        "the same Reviewer checks it on the round it last recorded, with no new round",
        *STOPS,
    ),
    "reviewer": (
        "Your stage ends when a verdict over the whole requirement",
        "with its findings and its coverage",
        "you repair, and you go on",
        "Your verdict names what you repaired",
        "says in its last line what it waits for and from whom",
        "check the repaired record on the round you last recorded",
        "byte for byte",
        *STOPS,
    ),
    "curator": (
        "the task's knowledge is written, checked and published",
        "the quality check is clean",
        "you repair, and you go on",
        "Your report names what you repaired",
        "says in its last line what it waits for and from whom",
        *STOPS,
    ),
    "manager": (
        "You hand a stage over whole",
        "the sync to the master's line",
        "you repair, and you go on",
        "Your report names what you repaired",
        "says in its last line what it waits for and from whom",
        *STOPS,
    ),
}

# Reviewer equivalents only: untouched roles still require their own wording.
REVIEWER_STAGE_EQUIVALENTS = {
    "Your stage ends when a verdict over the whole requirement": "Your stage ends at a recorded verdict covering the whole requirement",
    "with its findings and its coverage": "its findings and coverage",
    "you repair, and you go on": "Repair faults from your work or instrument and continue",
    "says in its last line what it waits for and from whom": "If your stage is unfinished at turn end, state what and whom you await in your last line",
    "You stop for an open permission prompt": "Stop for open permission prompts",
    "a refusal by a product guard": "product-guard/AR-tool refusals with correct input or naming unowned state",
    "a question about what the requirement means": "requirement-meaning questions",
    "an effect outside your own worktree": "effects outside your worktree, scratch folder and task",
}


CURATOR_STAGE_EQUIVALENTS = {
    "you repair, and you go on": "Repair faults your work or instrument causes, then continue",
    "Your report names what you repaired": "Report your repairs.",
    "says in its last line what it waits for and from whom": "If unfinished, end your turn with the awaited thing and its owner.",
    "a refusal by a product guard": "a product-guard or AR-tool refusal with correct input or unowned state",
    "a question about what the requirement means": "a requirement-meaning question",
    "an effect outside your own worktree": "effects outside your worktree, scratch folder and task",
}


def _facet_variants(parts: tuple[str, ...], facet: str) -> tuple[str, ...]:
    if parts == STAGE_PARTS["reviewer"] and facet in REVIEWER_STAGE_EQUIVALENTS:
        return facet, REVIEWER_STAGE_EQUIVALENTS[facet]
    if parts == STAGE_PARTS["curator"] and facet in CURATOR_STAGE_EQUIVALENTS:
        return facet, CURATOR_STAGE_EQUIVALENTS[facet]
    return (facet,)


def _delete_facet(section: str, parts: tuple[str, ...], facet: str) -> str:
    flat = _flatten(section)
    for variant in _facet_variants(parts, facet):
        flat = flat.replace(_flatten(variant), "", 1)
    return flat


#: Rule 9 sentences (evidence 10).
RULE_NINE = {
    "reviewer": (
        "A finding names the property that the requirement asks for and the candidate breaks.",
        "The case that shows it is its proof and not the finding.",
    ),
    "worker": (
        "Where a finding is still open after a repair, the next repair does not answer the new case.",
        "Write the property down for every case, and either show why the construction holds it or change the construction.",
    ),
    "manager": (
        "the question carries the Worker's account of the property and the construction behind the open finding.",
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


def _section_of(text: str, heading: str = SECTION_HEADING) -> str | None:
    """Return the single stage-end section body, or ``None`` when absent."""
    start = text.find(heading)
    if start < 0:
        return None
    body = text[start + len(heading) :]
    end = body.find("\n## ")
    return body if end < 0 else body[:end]


def _section_missing(section: str | None, parts: tuple[str, ...]) -> list[str]:
    """Facets of one section that do not hold inside it."""
    if section is None:
        return ["no stage-end section"]
    flat = _flatten(section)
    return [
        part
        for part in parts
        if not any(_flatten(variant) in flat for variant in _facet_variants(parts, part))
    ]


def _check_source_sections(role: str, parts: tuple[str, ...], missing: list[str]) -> None:
    for tree in SKILL_TREES:
        path = tree / LIFECYCLE / "roles" / f"{role}.md"
        section = _section_of(path.read_text(encoding="utf-8"))
        for gap in _section_missing(section, parts):
            missing.append(f"{path}: stage-end section {gap}")


def _check_compiled_sections(role: str, parts: tuple[str, ...], missing: list[str]) -> None:
    for root in (CANONICAL_SKILLS / LIFECYCLE, PACKAGED_SKILLS / LIFECYCLE):
        for operation in _role_operations(role):
            compiled = _compile(root, role, operation)
            section = _section_of(compiled)
            for gap in _section_missing(section, parts):
                missing.append(f"compiled {root.name} {role}/{operation}: stage-end section {gap}")


@pytest.mark.parametrize("role", STAGE_PARTS)
def test_stage_end_sections_hold_in_sources_and_compiled_texts(role: str) -> None:
    """Evidence 6, rules 6 to 8: the four roles hold the section and its parts."""
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


def _mutate_section(role: str, mutate) -> str:
    text = (CANONICAL_SKILLS / LIFECYCLE / "roles" / f"{role}.md").read_text(encoding="utf-8")
    start = text.find(SECTION_HEADING)
    assert start >= 0
    body = text[start:]
    end = body.find("\n## ")
    head, section, tail = (
        text[:start],
        body if end < 0 else body[:end],
        "" if end < 0 else body[end:],
    )
    return head + mutate(section) + tail


@pytest.mark.parametrize("role", STAGE_PARTS)
def test_stage_section_binding_fails_on_deletion_and_relocation(role: str) -> None:
    """Every required facet, deleted or moved out of the section, fails the check."""
    parts = STAGE_PARTS[role]
    original = (CANONICAL_SKILLS / LIFECYCLE / "roles" / f"{role}.md").read_text()
    assert _section_missing(_section_of(original), parts) == [], role
    for facet in parts:
        deleted = _mutate_section(
            role, lambda section, facet=facet: _delete_facet(section, parts, facet)
        )
        assert _flatten(deleted) != _flatten(original), f"{role}: deletion must change text"
        assert _section_missing(_section_of(deleted), parts), (
            f"{role}: deleting {facet!r} still holds"
        )
        relocated = _mutate_section(
            role,
            lambda section, facet=facet: (
                _delete_facet(section, parts, facet)
                + "\n\n## Elsewhere\n\n"
                + "\n".join(_facet_variants(parts, facet))
                + "\n"
            ),
        )
        assert _section_missing(_section_of(relocated), parts), (
            f"{role}: relocating {facet!r} still holds"
        )


@pytest.mark.parametrize("role", STAGE_PARTS)
def test_compiled_section_binding_fails_on_deletion_and_relocation(role: str) -> None:
    """The same deletion/relocation proof on one compiled capsule per role."""
    root = CANONICAL_SKILLS / LIFECYCLE
    parts = STAGE_PARTS[role]
    operation = _role_operations(role)[0]
    compiled = _compile(root, role, operation)
    for facet in parts:
        assert _section_missing(_section_of(compiled), parts) == []
        section = _section_of(compiled)
        assert section is not None
        deleted = compiled.replace(section, "\n" + _delete_facet(section, parts, facet) + "\n", 1)
        assert _flatten(deleted) != _flatten(compiled), "control must delete real instructions"
        assert _section_missing(_section_of(deleted), parts), (
            f"compiled {role}/{operation}: deleting {facet!r} still holds"
        )
        relocated = deleted + "\n\n## Elsewhere\n\n" + "\n".join(_facet_variants(parts, facet))
        assert _section_missing(_section_of(relocated), parts), (
            f"compiled {role}/{operation}: relocating {facet!r} still holds"
        )


def test_rule_nine_sentences_hold_for_reviewer_worker_manager() -> None:
    """Evidence 10: the rule 9 sentences hold in sources and compiled texts."""
    missing: list[str] = []
    for role, sentences in RULE_NINE.items():
        text = (CANONICAL_SKILLS / LIFECYCLE / "roles" / f"{role}.md").read_text(encoding="utf-8")
        for sentence in sentences:
            if sentence not in text:
                missing.append(f"roles/{role}.md: missing {sentence!r}")
        for operation in _role_operations(role):
            compiled = _flatten(_compile(CANONICAL_SKILLS / LIFECYCLE, role, operation))
            for sentence in sentences:
                if _flatten(sentence) not in compiled:
                    missing.append(f"compiled {role}/{operation}: missing {sentence!r}")
    assert missing == [], "\n".join(missing)
