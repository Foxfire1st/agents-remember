"""R13: the canonical lifecycle doctrine states ONE handoff authority boundary.

The defect this exists to make impossible is not a typo and not a missing paragraph. It is
*disagreement between role descriptions*: the boundary stated correctly in the manager file and
contradicted in the worker file, or asserted in the shared root while a role file still promises a
report checker that does not exist. An agent reading the contradicting file cannot tell which
authority it holds, so it hands semantic acceptance to a mechanical terminal signal. That is a
liveness and an authority defect at once, and no per-file check can see it: each file is
individually plausible.

WHAT DEFENDS WHAT
-----------------
Read this before trusting a test below, because the mechanisms are not interchangeable.

    reading                                              defended by
    --------------------------------------------------   ---------------------------------------
    no canonical surface contradicts the boundary         the claim sweep over EVERY `.md` under
      (the cross-role agreement property)                 `skills/l-01-agent-lifecycles`, not over
                                                          the declared roster. A contradiction in
                                                          an undeclared file still fails.
    every surface that speaks the completion-truth        the roster census: the declared roster
      vocabulary states the mechanical reading            must EQUAL the files that use the
                                                          vocabulary, in both directions.
    each declared surface states its own owed clauses     the per-surface required-statement sets,
      and does not deny them                              which are also contradiction-aware: a
                                                          surface that states and denies fails
                                                          `_assert_owed` and reads CONTRADICTED.
    the sweep can still fail                              `DetectorTeethTests` runs every retired
                                                          -claim detector against the historical
                                                          contradicting text it was written for, and
                                                          against the shipped wording it must pass.

SILENCE IS NOT CONTRADICTION
----------------------------
`classify_reading` returns three states, and `SilenceIsNotContradictionTests` pins all three on
synthetic samples. `ABSENT` means the surface does not speak to the clause at all -- architect,
designer, and orchestrator files are declared silent, and staying silent is not a failure. Only a
surface that owes the clause must `STATE` it; every canonical surface, owed or not, must avoid
`CONTRADICTED`.

WHAT THIS DOES NOT COVER (stated, not implied)
----------------------------------------------
- **Scope is the canonical tree only.** Generated copies are a projection of `skills/`, so their
  agreement is the `generated-skills` rail's subject (`scripts/sync-skills.py --check`), not this
  module's. Editing a generated copy is already the wrong repair for anything found here.
- **The detectors read vocabulary, not meaning.** They are sentence-scoped regexes for the retired
  promise shapes plus a negated-fragment filter. A contradiction phrased in fresh verbs this file
  has never seen is out of reach. What the required statements buy is narrower and worth stating
  exactly: they make the boundary *present* on every declared surface and make its removal a
  failure. They are **not** a semantic check, and on their own they would certify a document that
  states the boundary and denies it in the next sentence -- which is why `_assert_owed` also
  consults the contradiction findings and `classify_reading` also refuses an in-place denial. The
  two mechanisms together are what make a stated-and-denied surface fail; neither alone does.
- **The negated-fragment filter is deliberately coarse.** A fragment carrying any negation token is
  dropped whole, so a violation that shares a fragment with an unrelated negation escapes. That
  limit is why the sweep is not the primary pin.
- **Targeted test selection sees the roster by literal path only.** Each declared surface appears
  here as its exact repository-relative path, so a change to that file selects this module. A brand
  new file added to the tree is not a literal here and will not select it; the census case fails
  loudly the next time the module runs.
"""

from __future__ import annotations

import re
import unittest
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

# The canonical doctrine tree. `skills/` is the source; the nine generated copies are projections.
DOCTRINE_TREE = "skills/l-01-agent-lifecycles"
TREE_LITERAL = REPOSITORY_ROOT / DOCTRINE_TREE

# Every surface that must speak the completion-truth vocabulary, as exact path literals so the
# dependency-ownership selector observes this module as their consumer.
COMPLETION_TRUTH_ROSTER = (
    "skills/l-01-agent-lifecycles/SKILL.md",
    "skills/l-01-agent-lifecycles/roles/curator.md",
    "skills/l-01-agent-lifecycles/roles/manager.md",
    "skills/l-01-agent-lifecycles/roles/reviewer.md",
    "skills/l-01-agent-lifecycles/roles/strategist.md",
    "skills/l-01-agent-lifecycles/roles/system-specialist.md",
    "skills/l-01-agent-lifecycles/roles/worker.md",
    "skills/l-01-agent-lifecycles/templates/master-handover-packet.md",
)
# The completion-truth vocabulary itself: a surface uses it when it composes completion from
# terminal/finalizer truth. `agent-notifier` is excluded on purpose -- naming the relay is not the
# same act as composing completion, and three criteria/history files name it for other reasons.
COMPLETION_TRUTH_VOCABULARY = re.compile(r"terminal/finalizer|terminal truth", re.IGNORECASE)

# Surfaces that state the relay's own mechanical side without composing completion.
RELAY_MECHANICS_SURFACES = (
    "skills/l-01-agent-lifecycles/roles/worker.md",
    "skills/l-01-agent-lifecycles/templates/turn-report.md",
)

# The one reading every completion-truth surface must carry: the terminal outcome ends a turn and
# nothing more. Written as one alternation so a legitimate rephrasing inside the pattern passes.
MECHANICAL_READING = re.compile(
    r"only that (?:the provider|this|the manager's) turn ended",
)


def normalize(text: str) -> str:
    """Strip markdown emphasis and collapse whitespace so a clause is matched by its words."""

    return " ".join(re.sub(r"[*`]", "", text).split())


_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def _flatten(text: str) -> str:
    return normalize(_MARKDOWN_LINK.sub(r"\1", text))


_CLAUSE_SPLIT = re.compile(r"[,;:]|\band\b|\bbut\b")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_NEGATION = re.compile(r"\b(?:never|not|no|nor|cannot|can't|without|instead of)\b", re.IGNORECASE)
# Past this many surviving clauses the splitter is looking at a markdown list or table, not at a
# sentence, so no rejoinder is produced for it.
MAX_JOINED_CLAUSES = 8


@dataclass(frozen=True)
class View:
    """One reading of a surface: a clause, or a sentence rejoined from its surviving clauses."""

    text: str
    is_clause: bool

    def __bool__(self) -> bool:
        return bool(self.text)


def promise_views(text: str) -> tuple[View, ...]:
    """Two readings of the same text, both with negated clauses removed.

    A negated clause cannot be a promise -- "it never opens the artifact" states the boundary, it
    does not violate it -- so removing negated clauses is what keeps prohibitions from reading as
    violations. Both readings are kept because neither alone is enough:

    - each surviving **clause**, which keeps a subject next to its own verb
      (`A missing report is nudged by the notifier sweep`);
    - each **sentence rejoined** from its surviving clauses, which keeps a subject in one clause and
      its object in the next (`The notifier sweep evaluates ... / ... each artifact`). A rejoinder is
      produced only while the sentence stays within `MAX_JOINED_CLAUSES`: past that the splitter has
      met a markdown list or table rather than a sentence, and joining its items would invent claims
      nobody wrote.

    A rejoinder is an artifact of the splitter, so a detector that reads *local attribution* can ask
    for clause views only (`RetiredClaim.clause_only`) rather than trust invented adjacency.

    Paragraphs are respected before sentences so a markdown heading is never joined to the body
    that follows it into one oversized reading.
    """

    views: list[View] = []
    for block in re.split(r"\n\s*\n", text):
        flat = _flatten(block)
        for sentence in _SENTENCE_SPLIT.split(flat):
            surviving = [
                clause.strip()
                for clause in _CLAUSE_SPLIT.split(sentence)
                if clause.strip() and not _NEGATION.search(clause)
            ]
            views.extend(View(clause, True) for clause in surviving)
            if len(surviving) <= MAX_JOINED_CLAUSES:
                joined = " ".join(surviving)
                if joined:
                    views.append(View(joined, False))
    unique: dict[str, View] = {}
    for view in views:
        if view and view.text not in unique:
            unique[view.text] = view
    return tuple(unique.values())


@dataclass(frozen=True)
class RetiredClaim:
    """One claim the reconciled boundary retires, with the text it was written to catch.

    ``sanitize`` removes the spans of a reading that *state* the boundary rather than violate it,
    before ``pattern`` is applied. It exists because a clause can carry both: the shipped
    master-handover-packet wording says the terminal outcome ``attests only that the turn ended``
    and that ``rule 4's validation is what accepts it`` -- correct doctrine, but it contains every
    word an acceptance-side detector looks for. Removing those spans keeps the detector's teeth on
    the parts of the clause that do make a claim.
    """

    claim_id: str
    description: str
    pattern: re.Pattern[str]
    sample: str
    sanitize: tuple[re.Pattern[str], ...] = ()
    clause_only: bool = False


_ARTIFACT = r"(?:artifact|report|verdict|envelope|packet|expectation|coherence)"
_RELAY = r"(?:notifier|sweep|relay)"
# R13's third named failure: terminal-outcome semantic acceptance. The outcome may *wake* an owner
# and *end* a turn; it may not accept, attest, certify, vouch for, supply, authorize, or advance a
# handoff -- that is owner work.
_TERMINAL_OUTCOME = (
    r"(?:completed|terminal|finalizer|turn[- ]ended|turn[- ]ends?|end of (?:the |this )?turn)"
)
_ACCEPTANCE_ATTRIBUTION = (
    r"(?:accept\w*|attest\w*|certif\w*|vouch\w*|suppl\w*|advanc\w*|suffic\w*"
    r"|authoriz\w*|approv\w*|confirm\w*|green-?light\w*|counts? as|proves?|proven|establish\w*)"
    # "accepted-series authority" is an adjective, not an attribution: a token glued to a hyphen
    # names a thing rather than claiming that a turn outcome accepted one.
    r"(?!-)"
)
# "attests only that this turn ended", "means only that the provider turn ended normally": an
# attribution restricted to turn-end states the boundary and must not be read as a violation.
_TURN_END_RESTRICTION = re.compile(
    r"\b(?:attests?|attesting|means|mean|is|are|counts? as|serves as|amounts to)\s+"
    r"(?:only|merely|nothing more than)\s+that[^.;:]{0,60}?\bturn end\w*",
    re.IGNORECASE,
)
# "... rule 4's validation is what accepts it": here the *owner's* validation accepts the handoff,
# which is the boundary being stated. Only the validator-bound attribution is removed.
_VALIDATOR_BOUND_ATTRIBUTION = re.compile(
    r"\b(?:validation|validates|validated|the decider|the owner|the reviewer|the curator)\b"
    r"[^.;:]{0,30}?\b(?:accept\w*|confirm\w*|certif\w*|approv\w*|vouch\w*)\b",
    re.IGNORECASE,
)

RETIRED_CLAIMS = (
    RetiredClaim(
        claim_id="relay-evaluates-artifact",
        description="the relay evaluates, inspects, checks, or verifies a required artifact",
        pattern=re.compile(
            rf"(?=.*\b{_RELAY}\b)"
            rf"(?=.*\b(?:evaluat|inspect|verif|assess|judg|check|pars|open)\w*\b)"
            rf"(?=.*\b{_ARTIFACT}\w*\b)"
        ),
        sample=(
            "The HFX2-L2 agent-notifier sweep evaluates each expected artifact at every hand-off."
        ),
    ),
    RetiredClaim(
        claim_id="relay-polices-artifact-absence",
        description="the relay detects an absent artifact and nudges its author",
        pattern=re.compile(
            rf"(?=.*\b(?:missing|absent|forgotten|unwritten|not yet written)\b)"
            rf"(?=.*\b{_ARTIFACT}\w*\b)"
            rf"(?=.*\b(?:nudg|chase|remind|polic|detect)\w*\b)"
            rf"(?=.*\b{_RELAY}\b)"
        ),
        sample="A missing turn report is nudged by the HFX2-L2 agent-notifier sweep.",
    ),
    RetiredClaim(
        claim_id="relay-check-is-the-wake-trigger",
        description="the relay checks a named artifact before waking its owner",
        pattern=re.compile(
            rf"(?=.*\b{_RELAY}\b)(?=.*\b(?:check|confirm|verif|evaluat|inspect)\w*\b)"
            rf"(?=.*\b{_ARTIFACT}\w*\b)(?=.*\b(?:before|prior to|once|only after)\b)"
        ),
        sample="The sweep confirms the report exists before the manager is woken.",
    ),
    RetiredClaim(
        claim_id="second-completion-message",
        description="a redundant completion post is mandated after the durable artifact",
        pattern=re.compile(
            r"(?=.*\b(?:second|duplicate|parallel|redundant|extra|additional)\b)"
            r"(?=.*\bcompletion (?:post|message|row|note|prose)\b)"
            r"|(?=.*\b(?:must|shall|always|remember to|be sure to|make sure)\b)"
            r"(?=.*\b(?:send|post|write|author|emit)\w*\b)"
            r"(?=.*\bcompletion (?:post|message|row|note)\b)"
        ),
        sample="You must send a second completion message after the report is written.",
    ),
    RetiredClaim(
        claim_id="terminal-outcome-semantic-acceptance",
        description=(
            "a terminal/completed/finalizer outcome accepts, attests, certifies, vouches for, "
            "supplies, authorizes, or advances the handoff"
        ),
        pattern=re.compile(
            rf"\b{_TERMINAL_OUTCOME}\b[^.;:]{{0,80}}?\b{_ACCEPTANCE_ATTRIBUTION}\b"
            # The reverse order is kept only at noun-phrase distance: "approval is still required
            # for the final completed super branch" mentions both words without claiming that the
            # outcome accepted anything, and a wide gap there reads ordinary prose as a violation.
            rf"|\b{_ACCEPTANCE_ATTRIBUTION}\b[^.;:]{{0,25}}?\b{_TERMINAL_OUTCOME}\b",
            re.IGNORECASE,
        ),
        sample="The manager may treat a `completed` outcome as acceptance of the handoff.",
        sanitize=(_TURN_END_RESTRICTION, _VALIDATOR_BOUND_ATTRIBUTION),
        clause_only=True,
    ),
)

# The clause sets each declared surface owes. A surface may be legitimately silent on a clause; a
# surface on this roster is not, and the markers are the shipped wording for its own side of the
# boundary. Matching is on normalized words, so emphasis and re-wrapping are free.
OWED_STATEMENTS: dict[str, tuple[str, ...]] = {
    "skills/l-01-agent-lifecycles/SKILL.md": (
        "Terminal truth is mechanical; acceptance is the owner's.",
        "means only that the provider turn ended normally",
        "does not attest that the required artifact exists, is current, or satisfies its requirement",
        "derives that mechanical seat-state fact, and delivers it to the structurally current owner",
        "never opens, parses, or evaluates a report, verdict, coherence record, expectation row, "
        "or acceptance envelope",
        "before advancing lifecycle state",
        "nudges, rejects, replaces, or escalates",
        "needs no second model-authored completion post",
    ),
    "skills/l-01-agent-lifecycles/roles/manager.md": (
        "it never opens or evaluates the artifact",
        "only that the provider turn ended",
        "never attests that the report exists, is current, or satisfies its requirement",
        "open and validate the required artifact, candidate identity, evidence, and acceptance "
        "envelope",
        "before advancing lifecycle state",
        "your detected handoff defect",
    ),
    "skills/l-01-agent-lifecycles/roles/worker.md": (
        "terminal/finalizer truth attests only that this turn ended",
        "never that the report exists, is current, or satisfies its requirement",
        "the owning seat detects after your turn-ended state signal wakes it",
        "Never author a second model-authored completion post",
    ),
    "skills/l-01-agent-lifecycles/roles/reviewer.md": (
        "terminal/finalizer truth attests only that this turn ended",
        "wakes the decider, who validates the verdict independently",
    ),
    "skills/l-01-agent-lifecycles/roles/curator.md": (
        "terminal/finalizer evidence then attests only that this turn ended",
        "wakes the owner, who validates the report",
    ),
    "skills/l-01-agent-lifecycles/roles/system-specialist.md": (
        "terminal/finalizer state then attests only that this turn ended",
        "wakes the orchestrator, which validates the report",
    ),
    "skills/l-01-agent-lifecycles/roles/strategist.md": (
        "terminal/finalizer truth then attests only that this turn ended",
        "wakes the orchestrator, which validates the artifact",
    ),
    "skills/l-01-agent-lifecycles/templates/master-handover-packet.md": (
        "which attests only that the manager's turn ended",
        "who validates the packet",
    ),
    "skills/l-01-agent-lifecycles/templates/turn-report.md": (
        "The relay never inspects it",
        "the manager — never a seat-local watcher — detects a missing report",
    ),
}


class Reading(StrEnum):
    """What one canonical surface says about the mechanical-only reading of completion."""

    STATED = "stated"
    ABSENT = "absent"
    CONTRADICTED = "contradicted"


@dataclass(frozen=True)
class Finding:
    """One contradiction on one surface, with the fragment that carries it."""

    claim_id: str
    fragment: str

    def render(self, surface: str) -> str:
        return f"{surface}: {self.claim_id}: {self.fragment!r}"


def contradictions(text: str) -> tuple[Finding, ...]:
    """Every retired-claim shape this text promises; first evidence per claim, in document order."""

    findings: dict[str, Finding] = {}
    for view in promise_views(text):
        for claim in RETIRED_CLAIMS:
            if claim.claim_id in findings or (claim.clause_only and not view.is_clause):
                continue
            statement = view.text
            for removal in claim.sanitize:
                statement = removal.sub(" ", statement)
            if claim.pattern.search(statement):
                findings[claim.claim_id] = Finding(claim.claim_id, view.text)
    return tuple(findings.values())


def _reading_is_denied_in_place(text: str) -> bool:
    """True when every mechanical-reading match sits inside a clause that negates it.

    `MECHANICAL_READING` is a substring search, so "it is not the case that terminal truth attests
    only that this turn ended" would otherwise read as a statement of the boundary.
    """

    flat = _flatten(text)
    matches = list(MECHANICAL_READING.finditer(flat))
    if not matches:
        return False
    for match in matches:
        boundary = max(flat.rfind(character, 0, match.start()) for character in ",;:.")
        if not _NEGATION.search(flat[boundary + 1 : match.start()]):
            return False
    return True


def classify_reading(text: str) -> Reading:
    """Three-state reading of one surface: stated, silent, or contradicted.

    Consistency-aware by construction: a surface that carries the owed wording *and* also denies the
    boundary is `CONTRADICTED`, never `STATED`. Presence of the right sentence is not evidence that
    the surface holds the reading -- it is only evidence that the sentence was once pasted there.
    """

    if contradictions(text):
        return Reading.CONTRADICTED
    if MECHANICAL_READING.search(_flatten(text)) and not _reading_is_denied_in_place(text):
        return Reading.STATED
    return Reading.ABSENT


def canonical_surfaces() -> tuple[Path, ...]:
    """Every canonical markdown surface, declared roster and straggler alike."""

    return tuple(sorted(TREE_LITERAL.rglob("*.md")))


def read_surface(relative: str) -> str:
    return (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")


class AgreementAcrossTheRoleSetTests(unittest.TestCase):
    """The property R13 exists for: no canonical surface contradicts the boundary."""

    def test_no_canonical_surface_contradicts_the_boundary(self) -> None:
        surfaces = canonical_surfaces()
        # Fail closed on the tree itself: an empty or shrunken surface set would let this case pass
        # with nothing examined, which is the one way a sweep like this can lie.
        self.assertTrue(
            TREE_LITERAL.is_dir(),
            f"the canonical doctrine tree is missing: {DOCTRINE_TREE}",
        )
        self.assertGreater(
            len(surfaces),
            len(COMPLETION_TRUTH_ROSTER),
            "the contradiction sweep examined no more surfaces than the declared roster",
        )
        findings: list[str] = []
        for path in surfaces:
            surface = path.relative_to(REPOSITORY_ROOT).as_posix()
            findings.extend(
                finding.render(surface)
                for finding in contradictions(path.read_text(encoding="utf-8"))
            )
        self.assertEqual(
            findings,
            [],
            "canonical lifecycle doctrine contradicts the handoff authority boundary "
            "(terminal truth only ends a turn; the owner validates the artifact) in:\n  "
            + "\n  ".join(findings),
        )

    def test_the_shared_root_states_the_whole_boundary(self) -> None:
        self._assert_owed("skills/l-01-agent-lifecycles/SKILL.md")

    def test_every_declared_surface_states_its_owed_clause(self) -> None:
        for relative in sorted(OWED_STATEMENTS):
            with self.subTest(surface=relative):
                self._assert_owed(relative)

    def _assert_owed(self, relative: str) -> None:
        """The surface must state its owed clauses AND not deny the boundary anywhere.

        Presence alone is not a pass: a surface that states the boundary and contradicts it in the
        next sentence holds no reading an agent can rely on, and it is exactly the disagreement R13
        exists to prevent.
        """

        raw = read_surface(relative)
        text = _flatten(raw)
        missing = [
            statement for statement in OWED_STATEMENTS[relative] if normalize(statement) not in text
        ]
        self.assertEqual(
            missing,
            [],
            f"{relative} no longer states its side of the handoff authority boundary: {missing}",
        )
        self.assertEqual(
            [finding.render(relative) for finding in contradictions(raw)],
            [],
            f"{relative} states its owed boundary clauses and contradicts them elsewhere",
        )

    def test_the_declared_roster_equals_the_surfaces_that_speak_the_vocabulary(self) -> None:
        observed = tuple(
            path.relative_to(REPOSITORY_ROOT).as_posix()
            for path in canonical_surfaces()
            if COMPLETION_TRUTH_VOCABULARY.search(path.read_text(encoding="utf-8"))
        )
        self.assertEqual(
            sorted(observed),
            sorted(COMPLETION_TRUTH_ROSTER),
            "every canonical surface that composes completion from terminal/finalizer truth must "
            "be on the roster (and the roster must not outlive the vocabulary): declare a new "
            "participant in COMPLETION_TRUTH_ROSTER and make it state the mechanical reading, or "
            "remove the row when the surface stops speaking the vocabulary",
        )

    def test_every_roster_surface_carries_the_mechanical_reading(self) -> None:
        for relative in sorted(COMPLETION_TRUTH_ROSTER):
            with self.subTest(surface=relative):
                self.assertEqual(
                    classify_reading(read_surface(relative)),
                    Reading.STATED,
                    f"{relative} speaks the completion-truth vocabulary without stating that the "
                    "terminal outcome only ends the turn",
                )

    def test_the_relay_mechanics_surfaces_state_the_relay_side(self) -> None:
        for relative in sorted(RELAY_MECHANICS_SURFACES):
            with self.subTest(surface=relative):
                self._assert_owed(relative)


class SilenceIsNotContradictionTests(unittest.TestCase):
    """Silence and contradiction are different states, and the classifier must say so."""

    def test_a_stated_clause_is_stated(self) -> None:
        self.assertEqual(
            classify_reading(
                "Terminal truth attests only that this turn ended; the owner validates."
            ),
            Reading.STATED,
        )

    def test_a_prohibition_is_not_a_contradiction(self) -> None:
        """Negation-only wording states no positive reading, and must not read as a violation."""

        self.assertNotEqual(
            classify_reading(
                "The relay never inspects the report, and it never nudges a seat about an artifact."
            ),
            Reading.CONTRADICTED,
        )

    def test_silence_is_absent_rather_than_contradicted(self) -> None:
        self.assertEqual(
            classify_reading("This seat dispatches work and records decisions in the task doc."),
            Reading.ABSENT,
        )

    def test_stating_and_denying_is_contradicted_not_stated(self) -> None:
        """Presence of the owed sentence is not a pass; the same surface may also deny it."""

        self.assertEqual(
            classify_reading(
                "Terminal/finalizer truth attests only that this turn ended and wakes the owner. "
                "The manager may treat a `completed` outcome as acceptance of the handoff."
            ),
            Reading.CONTRADICTED,
        )

    def test_an_in_place_denial_is_not_stated(self) -> None:
        """A substring match inside a negated clause is not a statement of the reading."""

        self.assertEqual(
            classify_reading(
                "It is not the case that terminal truth attests only that this turn ended."
            ),
            Reading.ABSENT,
        )

    def test_the_retired_promise_is_contradicted(self) -> None:
        self.assertEqual(
            classify_reading(RETIRED_CLAIMS[0].sample),
            Reading.CONTRADICTED,
        )


class DetectorTeethTests(unittest.TestCase):
    """Each detector still catches the shipped text it was written for, and clears the new one."""

    def test_every_retired_claim_is_caught_in_its_own_sample(self) -> None:
        for claim in RETIRED_CLAIMS:
            with self.subTest(claim=claim.claim_id):
                matched = [
                    finding.claim_id
                    for finding in contradictions(claim.sample)
                    if finding.claim_id == claim.claim_id
                ]
                self.assertEqual(matched, [claim.claim_id], claim.description)

    def test_the_reconciled_manager_wording_is_clean(self) -> None:
        self.assertEqual(
            contradictions(read_surface("skills/l-01-agent-lifecycles/roles/manager.md")), ()
        )

    def test_the_scan_is_over_a_tree_and_not_over_the_roster(self) -> None:
        surfaces = {path.relative_to(REPOSITORY_ROOT).as_posix() for path in canonical_surfaces()}
        self.assertTrue(surfaces, "the canonical doctrine tree produced no markdown surfaces")
        self.assertTrue(
            set(COMPLETION_TRUTH_ROSTER).issubset(surfaces),
            "a declared surface is missing from the canonical tree",
        )
        self.assertTrue(
            surfaces - set(COMPLETION_TRUTH_ROSTER),
            "the scan must range over undeclared surfaces too, not only the roster",
        )
        for relative in (*COMPLETION_TRUTH_ROSTER, *RELAY_MECHANICS_SURFACES):
            with self.subTest(surface=relative):
                self.assertTrue(read_surface(relative).strip(), f"{relative} is empty")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
