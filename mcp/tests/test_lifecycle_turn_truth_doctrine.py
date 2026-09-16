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
- **The detectors read vocabulary and attribution, not meaning.** They are clause-scoped regexes for
  the retired promise shapes plus a negated-fragment filter. The acceptance detector asks *who* an
  acceptance is attributed to, because that attribution is the whole difference between the boundary
  and a violation of it: *"the completed master branch is approved by the developer before it
  lands"* and *"a proven candidate from a completed leaf is sufficient for the next wave"* are
  sentences about a branch and a candidate -- doctrine R13 explicitly preserves (the developer
  approves; the owning seat judges) -- while *"a completed outcome accepts the handoff"* and *"That
  outcome accepts the handoff"* attribute the acceptance to the outcome itself. A contradiction
  phrased in fresh verbs this file has never seen is out of reach. What the required statements buy
  is narrower and worth stating exactly: they make the boundary *present* on every declared surface
  and make its removal a failure. They are **not** a semantic check, and on their own they would
  certify a document that states the boundary and denies it in the next sentence -- which is why
  `_assert_owed` also consults the contradiction findings and `classify_reading` also refuses an
  in-place denial. The two mechanisms together are what make a stated-and-denied surface fail;
  neither alone does.
- **The negated-fragment filter is deliberately coarse.** A fragment carrying any negation token is
  dropped whole, so a violation that shares a fragment with an unrelated negation escapes. That
  limit is why the sweep is not the primary pin.
- **The detector reads attribution, not subjects, and the window is where that shows.** An
  acceptance is read as the outcome's when it sits within `_CLAIM_WINDOW` characters of it, so
  another agent's acceptance INSIDE that window is still read as the outcome's --
  "The completed turn is what the manager confirms." fires -- while the same sentence with the
  agent further away ("A completed leaf is required before the manager advances lifecycle state",
  31 characters) is clean. Classifying those apart needs a subject, which a regex does not have;
  `DeclaredLimitsTests` pins the measured behaviour rather than leaving it implied. Widening the
  authority sanitize instead was measured and rejected: it swallowed the packet's own named failure,
  "the manager may treat a completed outcome as acceptance of the handoff".
- **Measured reach over the repository, not just the swept tree.** Both this detector and the one
  it replaced were run over every `.md` surface in the repository (817 files at this revision): this
  one fires nowhere, and the one it replaced fired on 21 fragments, every one of them a false
  positive of the over-fire class this repair exists for ("Until the locator advances to
  terminal-archived", "confirm the checklist reflects completed code changes", "a conflicting
  terminal request must expose the archive-accepted verb"). No detection of a real contradiction was
  lost; the guard's own sweep is still the pin, this is the reach measurement.
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
#
# Re-pointed at the consolidated corpus (R01's one-home ruling, 2026-09-16). The boundary's one home
# is now `core/acceptance.md`: the retired `SKILL.md` section *is* that file, verbatim, and the
# consolidated `SKILL.md` cites it instead of restating it, so it stopped speaking the vocabulary --
# and this census rule's own remedy for a row whose surface went silent is to remove the row. The
# seven other surfaces added here already composed completion from terminal/finalizer truth in the
# consolidated tree and were simply never declared. The roster is a declaration, not a wish list: it
# must EQUAL the speakers in both directions, so every entry below is a measured speaker and every
# measured speaker is an entry. `roles/worker.md` keeps its row because its own side of the boundary
# is restored to the wording this module reads (see OWED_STATEMENTS).
COMPLETION_TRUTH_ROSTER = (
    "skills/l-01-agent-lifecycles/core/acceptance.md",
    "skills/l-01-agent-lifecycles/operations/curation.md",
    "skills/l-01-agent-lifecycles/operations/recovery.md",
    "skills/l-01-agent-lifecycles/operations/review.md",
    "skills/l-01-agent-lifecycles/reference/rulings.md",
    "skills/l-01-agent-lifecycles/roles/architect.md",
    "skills/l-01-agent-lifecycles/roles/curator.md",
    "skills/l-01-agent-lifecycles/roles/designer.md",
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
#
# Verified against the consolidated corpus rather than assumed: both members still state the relay's
# own side ("the relay itself never inspects the artifact" in the worker file, "The relay never
# inspects it" in the turn-report template), and no surface that newly states it was left out.
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
# and *end* a turn; it may not accept, attest, certify, vouch for, supply, authorize, settle,
# ratify, discharge, license, close, or advance a handoff -- that is owner work.
_ACCEPTANCE_ATTRIBUTION = (
    r"(?:accept\w*|attest\w*|certif\w*|vouch\w*|suppl\w*|advanc\w*|suffic\w*"
    r"|authoriz\w*|approv\w*|confirm\w*|green-?light\w*|counts? as|proves?|proven|establish\w*"
    # R13 draws the boundary around ACCEPTANCE, not around this list. A handoff the outcome
    # "settles", "ratifies", "discharges", "licenses", or "closes" has been accepted as plainly as
    # one it "accepts", and "is enough" / "means final" are the copula forms of the same claim.
    # While those were missing, the detector's reach was a property of its vocabulary rather than
    # of the boundary it exists to defend.
    #
    # Each added verb is inflected, not stemmed: `clos\w*` reads "closeout" -- the shipped noun in
    # "every completed leaf-closeout ... gate" -- as an act of closing, which is a false positive on
    # doctrine the detector must not own.
    r"|settl(?:e|es|ed|ing)\b|ratif(?:y|ies|ied|ying)\b|discharg(?:e|es|ed|ing)\b"
    r"|licens(?:e|es|ed|ing)\b|clos(?:e|es|ed|ing)\b"
    r"|(?:is|are|was|were|means?|counts? as|serves as|amounts to)"
    r"\s+(?:enough|final|decisive|dispositive)"
    r")"
    # "accepted-series authority" is an adjective, not an attribution: a token glued to a hyphen
    # names a thing rather than claiming that a turn outcome accepted one.
    r"(?!-)"
)
_TERMINAL_OUTCOME_TOKEN = r"(?:completed|terminal|finalizer)"
# How far from the outcome its acceptance may sit. This is the detector's declared reach, and it is
# deliberately short: inside it a verb is read as the outcome's, and it is what keeps "A completed
# leaf is required before the manager ADVANCES lifecycle state" clean -- there the acceptance is the
# manager's, and it sits 31 characters away. The limit is real rather than solved: an acceptance by
# another agent INSIDE the window is still read as the outcome's (see DECLARED_LIMITS).
_CLAIM_WINDOW = 25
_OUTCOME_HEAD = r"(?:outcome|truth|state|evidence|status|signal|fact|result)"
# The nouns a turn outcome is *about*: a completed turn, a completed leaf. `completed` in front of
# one of these is still the outcome the boundary is about -- "The completed turn accepts the
# handoff" is the retired claim -- so leaving them out of the naming vocabulary read that claim
# clean. They are read FORWARD only, and a preposition-governed occurrence is a modifier rather
# than a subject: "a proven candidate FROM A COMPLETED LEAF is sufficient for the next wave" is
# about the candidate, while "A COMPLETED LEAF is sufficient for the next wave" is about the
# outcome. Reading them in the reverse direction as well turns ordinary seat cleanup
# ("worktree_integrate auto-closes a completed leaf's worker/reviewer/curator seats") into a
# violation, which is why the reverse read stays on the nouns that NAME an outcome.
_STRUCTURAL_OUTCOME_HEAD = r"(?:turn|leaf|branch|series|task|seat)"
# `(?!-)` on the head as well as on the attribution: in "every completed leaf-closeout or
# leaf/master-integration gate" the boundary after "leaf" is inside a hyphen compound, so the token
# is naming a gate rather than the outcome, and the same guard on both sides is what stops the
# compound from being read as an attribution. `(?!['\u2019]s\b)` is the possessive form of the same
# mistake: in "worktree_integrate auto-closes a completed leaf's worker/reviewer/curator seats" and
# "a completed leaf's seats close when its report is durable" the seats are what closes, and the
# token is describing the leaf.
_STRUCTURAL_OUTCOME = (
    rf"{_TERMINAL_OUTCOME_TOKEN}\s+(?:and\s+)?{_STRUCTURAL_OUTCOME_HEAD}\b(?!-)(?!['\u2019]s\b)"
)
# The outcome, *named*. `completed` is an adjective before it is a noun, and that is the difference
# between the boundary and a violation of it: "the completed master branch is approved by the
# developer" and "a proven candidate from a completed leaf is sufficient" are about a branch and a
# candidate, so reading either as the outcome accepting something reddens doctrine R13 preserves.
# The token denotes the outcome in exactly three ways:
#
#   - it names one -- `a completed outcome`, `terminal truth`, `finalizer evidence`;
#   - nothing stands between it and the claim, because what follows is the acceptance verb or a
#     copula: "`completed` attests more than that the provider turn ended" *is* the outcome
#     attesting, and "a completed outcome is acceptance of the handoff" *is* the outcome claiming;
#   - a turn-end form names the outcome by itself (`turn-ended`, `end of turn`).
_TERMINAL_OUTCOME = (
    r"(?:"
    rf"{_TERMINAL_OUTCOME_TOKEN}\s+(?:and\s+)?{_OUTCOME_HEAD}\b(?!-)"
    rf"|{_TERMINAL_OUTCOME_TOKEN}(?=\s+{_ACCEPTANCE_ATTRIBUTION}\b)"
    rf"|{_TERMINAL_OUTCOME_TOKEN}"
    r"(?=\s+(?:is|are|was|were|means?|counts? as|serves as|amounts to)\b)"
    r"|turn[- ]ended|turn[- ]ends?|end of (?:the |this )?turn"
    r")"
)
# A demonstrative cannot introduce a new referent: once a surface has named the outcome, "That
# outcome accepts the handoff" attributes the acceptance to *that* outcome, which is the same claim
# as naming it. This is the shape a state-and-deny contradiction splits across: the boundary stated
# in one sentence, the acceptance claimed in the next, with no repeated token for a clause-scoped
# proximity read to catch. It is read FORWARD only -- after the verb a demonstrative is the verb's
# object ("the acceptance envelope for that outcome"), not the agent that accepted.
_OUTCOME_ANAPHOR = (
    r"(?:that|this)\s+(?:(?:terminal|completed|finalizer|turn[- ]ended|same)\s+)?outcome"
)
# "attests only that this turn ended", "means only that the provider turn ended normally": an
# attribution restricted to turn-end states the boundary and must not be read as a violation.
_TURN_END_RESTRICTION = re.compile(
    r"\b(?:attests?|attesting|means|mean|is|are|counts? as|serves as|amounts to)\s+"
    r"(?:only|merely|nothing more than)\s+that[^.;:]{0,60}?\bturn end\w*",
    re.IGNORECASE,
)
# "... rule 4's validation is what accepts it": here the *owner's* validation accepts the handoff,
# which is the boundary being stated. The same is true of the passive form R13's preservation
# boundary is written in -- "the completed master branch is approved by the developer before it
# lands" -- where the acceptance has an explicit human authority as its agent. Read the attribution
# after the sanitizers, not the proximity of two words after them.
#
# The two lists are deliberately different. The pre-nominal one is left as narrow as it was: a wide
# gap there would swallow "the manager may treat a completed outcome as acceptance of the handoff",
# which is the packet's own named failure. The passive form names its agent outright, so it can take
# the full authority list -- an outcome is never one of them.
_PRE_NOMINAL_AUTHORITY = (
    r"(?:validation|validates|validated|the decider|the owner|the reviewer|the curator)"
)
_PASSIVE_AGENT = (
    r"(?:developer|human|owner|decider|reviewer|curator|orchestrator|architect|manager"
    r"|strategist|system[- ]specialist|validation|validators?)"
)
_OWNER_BOUND_VERB = (
    r"(?:accept\w*|confirm\w*|certif\w*|approv\w*|vouch\w*|authoriz\w*|green-?light\w*)"
)
_VALIDATOR_BOUND_ATTRIBUTION = re.compile(
    rf"\b{_PRE_NOMINAL_AUTHORITY}\b[^.;:]{{0,30}}?\b{_OWNER_BOUND_VERB}\b"
    rf"|\b{_OWNER_BOUND_VERB}\b\s+by\s+(?:the\s+|a\s+|its\s+)?{_PASSIVE_AGENT}\b",
    re.IGNORECASE,
)
# A structural noun the token modifies can be the outcome when its own noun phrase is the subject of
# the claim; a subject never begins with a preposition, so an occurrence a preposition governs is
# normally a modifier: "a proven candidate FROM A COMPLETED LEAF is sufficient for the next wave" is
# a sentence about the candidate.
#
# Two exceptions, because the rule above is a rule of thumb and not a parse:
#
#   - "by" is not in the list. After a passive verb it introduces the AGENT, and the agent is the
#     whole question: "the handoff is accepted by the completed turn" is the retired claim.
#   - nothing is removed when the phrase is itself the subject or agent of the acceptance the
#     clause goes on to make -- "After the completed turn accepts the handoff", "For a completed
#     leaf to accept the handoff", both of which the rule alone would have deleted. Those are the
#     claim, not a modifier of it, and they are pinned in the cases below.
#
# The outcome nouns are deliberately not listed here -- "the manager may advance lifecycle state ON
# A COMPLETED OUTCOME" is a violation, and it is carried by exactly this shape.
_OBLIQUE_MODIFIER = re.compile(
    r"\b(?:about|above|across|after|against|along|among|around|at|before|behind|below|beneath"
    r"|beside|between|beyond|despite|down|during|except|for|from|in|inside|into|like|near|of"
    r"|off|on|onto|out|outside|over|past|per|since|through|throughout|to|toward|towards|under"
    r"|underneath|until|up|upon|via|with|within|without)\s+"
    r"(?:(?:the|a|an|its|their|his|her|our|your|this|that|these|those|each|every|any|no|both"
    r"|one|two|three|all|some|such)\s+)?"
    rf"{_STRUCTURAL_OUTCOME}"
    rf"(?!\s+(?:to\s+)?{_ACCEPTANCE_ATTRIBUTION})",
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
            "supplies, authorizes, settles, ratifies, discharges, licenses, closes, or advances "
            "the handoff"
        ),
        pattern=re.compile(
            # The forward read accepts the anaphor and the structural turn/leaf nouns as well as the
            # named outcome, so the sentence that claims the acceptance is caught on its own terms
            # even when the outcome was named in the sentence before it.
            rf"\b(?:{_TERMINAL_OUTCOME}|{_OUTCOME_ANAPHOR}|{_STRUCTURAL_OUTCOME})\b"
            rf"[^.;:]{{0,{_CLAIM_WINDOW}}}?\b{_ACCEPTANCE_ATTRIBUTION}\b"
            # The reverse order is kept only at noun-phrase distance: "approval is still required
            # for the final completed super branch" mentions both words without claiming that the
            # outcome accepted anything, and a wide gap there reads ordinary prose as a violation.
            rf"|\b{_ACCEPTANCE_ATTRIBUTION}\b[^.;:]{{0,{_CLAIM_WINDOW}}}?\b{_TERMINAL_OUTCOME}\b"
            # The passive agent: "the handoff is accepted BY the completed turn". This is the shape
            # a preposition check alone would delete, and it is the claim.
            rf"|\b{_ACCEPTANCE_ATTRIBUTION}\b\s+by\s+(?:the\s+|a\s+|an\s+|its\s+)?"
            rf"(?:{_TERMINAL_OUTCOME}|{_STRUCTURAL_OUTCOME})\b",
            re.IGNORECASE,
        ),
        sample="The manager may treat a `completed` outcome as acceptance of the handoff.",
        sanitize=(
            _TURN_END_RESTRICTION,
            _VALIDATOR_BOUND_ATTRIBUTION,
            _OBLIQUE_MODIFIER,
        ),
        clause_only=True,
    ),
)

# The clause sets each declared surface owes. A surface may be legitimately silent on a clause; a
# surface on this roster is not, and the markers are the shipped wording for its own side of the
# boundary. Matching is on normalized words, so emphasis and re-wrapping are free.
#
# Re-pointed at the consolidated corpus's shipped wording. The whole-boundary set moved with the
# boundary itself, from the retired `SKILL.md` section to `core/acceptance.md`, which carries all
# eight clauses verbatim; the per-role markers that no longer matched were re-quoted from the file
# that ships them today, and one of them (`roles/strategist.md`) named the wrong validating seat --
# the consolidated handoff-artifact table in `core/acceptance.md` gives the strategist's
# orchestration-task draft to the architect, so the marker follows the corpus and not the reverse.
# A marker is still the same clause it always was: `_assert_owed` requires its words to be present
# AND the surface to contradict nothing, so deletion or an in-place denial still fails.
OWED_STATEMENTS: dict[str, tuple[str, ...]] = {
    "skills/l-01-agent-lifecycles/core/acceptance.md": (
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
        "never opens or evaluates the artifact",
        "only that the provider turn ended",
        "never attests that the report exists, is current, or satisfies its requirement",
        "open and validate the required artifact, candidate identity, evidence, and acceptance "
        "envelope",
        "before advancing lifecycle state",
        "this seat's own detected handoff defect",
    ),
    "skills/l-01-agent-lifecycles/roles/worker.md": (
        "terminal/finalizer truth attests only that this turn ended",
        "never that the report exists, is current, or satisfies its requirement",
        "the owning seat detects after your turn-ended state signal wakes it",
        "Never author a second model-authored completion post",
    ),
    "skills/l-01-agent-lifecycles/roles/reviewer.md": (
        "Terminal/finalizer truth then attests only that this turn ended",
        "wakes the decider, who validates the verdict independently",
    ),
    "skills/l-01-agent-lifecycles/roles/curator.md": (
        "terminal/finalizer evidence then attests only that this turn ended",
        "wakes the manager, who validates it",
    ),
    "skills/l-01-agent-lifecycles/roles/system-specialist.md": (
        "terminal/finalizer state then attests only that this turn ended",
        "wakes the orchestrator, which validates the report",
    ),
    "skills/l-01-agent-lifecycles/roles/strategist.md": (
        "Terminal/finalizer truth attests only that this turn ended",
        "wakes the architect, who validates the artifact",
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

    def test_the_boundarys_one_home_states_the_whole_boundary(self) -> None:
        """The whole boundary is owed by the surface that owns it, not by the file it used to sit in.

        This case asserted the shared root (`SKILL.md`) while the boundary lived in a section there.
        R01 moved that section -- verbatim -- into `core/acceptance.md`, whose own title declares it
        the boundary's one home, and the consolidated `SKILL.md` cites it instead of restating it.
        The case follows the boundary instead of the path it used to be reachable at: the same
        assertion, against the surface that now holds the whole of it. `SKILL.md` is not unchecked by
        that move -- it is swept by `test_no_canonical_surface_contradicts_the_boundary` like every
        other canonical surface.
        """

        self._assert_owed("skills/l-01-agent-lifecycles/core/acceptance.md")

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


class ConvergenceTeethTests(unittest.TestCase):
    """The re-pointed data kept the detectors' teeth: one mutant pair per reconciled site.

    The consolidation repair moved this module's roster and required-statement *data* to the
    consolidated corpus and moved four corpus sentences so the EXISTING detectors read them
    correctly. That is exactly the kind of change that can buy a green run by hollowing a guard out,
    so the proof is stated instead of implied: for each reconciled site, the surface as it ships must
    read clean, and the contradicting variant that site carried (or would carry) must still fail the
    same detector. The shipped side is READ FROM THE CORPUS -- not pasted here -- so the pair cannot
    agree with itself; only the mutant is synthetic. Change the corpus into a contradiction, or
    hollow a detector out, and one half of a pair fails.
    """

    #: site -> (corpus surface, synthetic contradicting variant, claim the variant must trip)
    RECONCILED_SITE_MUTANTS: tuple[tuple[str, str, str, str], ...] = (
        (
            "reference/rulings.md migration map (historical record)",
            "skills/l-01-agent-lifecycles/reference/rulings.md",
            "| LOCR-1 | x | The completed outcome accepts the handoff and validates the artifact |",
            "terminal-outcome-semantic-acceptance",
        ),
        (
            "roles/architect.md relay sentence",
            "skills/l-01-agent-lifecycles/roles/architect.md",
            "The relay evaluates the artifact and nudges the seat when a report is missing.",
            "relay-evaluates-artifact",
        ),
        (
            "roles/orchestrator.md sweep sentence",
            "skills/l-01-agent-lifecycles/roles/orchestrator.md",
            "The sweep inspects the durable report before the owner is woken.",
            "relay-evaluates-artifact",
        ),
        (
            "roles/reviewer.md negated instruction",
            "skills/l-01-agent-lifecycles/roles/reviewer.md",
            "**Never:** implement or edit code, run an unrequested full suite, or author a second "
            "completion row.",
            "second-completion-message",
        ),
    )

    def test_each_reconciled_site_reads_clean_and_its_mutant_still_fails(self) -> None:
        for site, relative, mutant, claim_id in self.RECONCILED_SITE_MUTANTS:
            with self.subTest(site=site):
                self.assertEqual(
                    [
                        finding.render(relative)
                        for finding in contradictions(read_surface(relative))
                    ],
                    [],
                    f"{site}: the surface stopped reading clean; the reconciliation went the wrong way",
                )
                self.assertIn(
                    claim_id,
                    [finding.claim_id for finding in contradictions(mutant)],
                    f"{site}: the detector no longer catches the contradiction it exists for",
                )

    def test_a_contradicting_sentence_added_to_a_roster_surface_still_fails_it(self) -> None:
        """The per-surface check is state-and-deny aware, and this is the packet's own named failure."""

        relative = "skills/l-01-agent-lifecycles/roles/manager.md"
        shipped = read_surface(relative)
        self.assertEqual(contradictions(shipped), ())
        self.assertEqual(classify_reading(shipped), Reading.STATED)
        mutated = (
            shipped
            + "\nThe manager may treat a `completed` outcome as acceptance of the handoff.\n"
        )
        self.assertEqual(classify_reading(mutated), Reading.CONTRADICTED)

    def test_removing_an_owed_clause_from_the_shipped_text_still_fails_its_surface(self) -> None:
        relative = "skills/l-01-agent-lifecycles/core/acceptance.md"
        # The mutation is applied to the normalized reading, because that is what `_assert_owed`
        # tests: a clause the shipped file carries across a line break or through markdown emphasis
        # is removed here by its words, exactly as the check reads it.
        shipped = _flatten(read_surface(relative))
        for clause in OWED_STATEMENTS[relative]:
            with self.subTest(clause=clause):
                marker = normalize(clause)
                self.assertIn(marker, shipped, f"{relative} no longer states its owed clause")
                mutated = shipped.replace(marker, " ")
                self.assertIn(
                    clause,
                    [owed for owed in OWED_STATEMENTS[relative] if normalize(owed) not in mutated],
                    "a surface that lost its owed clause must be reported as missing it",
                )


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

    def test_a_state_and_deny_split_across_sentences_is_contradicted(self) -> None:
        """The claim needs no second outcome token, and the split is where the guard used to read clean.

        A demonstrative names the outcome the previous sentence established, so the sentence that
        makes the claim carries none of the vocabulary a proximity read looks for -- and a surface
        that states the boundary in one sentence and accepts in the next holds no reading at all.
        """

        text = (
            "The terminal outcome attests only that this turn ended and wakes the owner. "
            "That outcome accepts the handoff."
        )
        self.assertEqual(classify_reading(text), Reading.CONTRADICTED)
        self.assertEqual(
            [finding.claim_id for finding in contradictions(text)],
            ["terminal-outcome-semantic-acceptance"],
        )

    def test_the_turn_and_leaf_nouns_are_read_as_the_outcome(self) -> None:
        """The vocabulary must name what the claim is ABOUT, and a turn or a leaf is what it is about.

        Requiring the token to name the outcome was right, but leaving these nouns out of the naming
        vocabulary read "The completed turn accepts the handoff" clean -- the retired claim itself,
        invisible on a surface that states every owed clause. Position decides which reading is
        meant, and the owner-bound case below pins the other side of the same line.
        """

        for text in (
            "The completed turn accepts the handoff.",
            "The completed leaf accepts the handoff on the owner's behalf.",
            "A completed leaf is sufficient for the next wave.",
        ):
            with self.subTest(text=text):
                self.assertEqual(classify_reading(text), Reading.CONTRADICTED, text)
                self.assertEqual(
                    [finding.claim_id for finding in contradictions(text)],
                    ["terminal-outcome-semantic-acceptance"],
                    text,
                )

    def test_a_preposition_does_not_delete_the_acceptance_itself(self) -> None:
        """The oblique-modifier rule is a rule of thumb, and these are where it must NOT apply.

        A structural noun phrase a preposition governs is normally a modifier ("a proven candidate
        from a completed leaf is sufficient"), so it is removed before the detector runs. But the
        phrase can also be the subject or the agent of the acceptance the clause goes on to make,
        and then it is the claim: `After the completed turn accepts ...`, `For a completed leaf to
        accept ...`, `accepted by the completed turn`. A rule applied without those exceptions read
        all three clean when they had been caught before it existed.
        """

        for text in (
            "After the completed turn accepts the handoff, the wave advances.",
            "The handoff is accepted by the completed turn.",
            "For a completed leaf to accept the handoff would break the boundary.",
        ):
            with self.subTest(text=text):
                self.assertEqual(classify_reading(text), Reading.CONTRADICTED, text)
                self.assertEqual(
                    [finding.claim_id for finding in contradictions(text)],
                    ["terminal-outcome-semantic-acceptance"],
                    text,
                )

    def test_every_acceptance_verb_is_read_as_an_attribution(self) -> None:
        """R13's boundary is acceptance, not the verb list the detector was first written with.

        Every one of these asserts what "accepts the handoff" asserts. While the vocabulary stopped
        at the words the first detector happened to carry, all of them were certified `stated`.
        """

        for verb in (
            "settles",
            "ratifies",
            "discharges",
            "means final",
            "is enough",
            "licenses",
            "closes",
        ):
            with self.subTest(verb=verb):
                text = f"The terminal outcome {verb} the handoff."
                self.assertEqual(classify_reading(text), Reading.CONTRADICTED, verb)
                self.assertEqual(
                    [finding.claim_id for finding in contradictions(text)],
                    ["terminal-outcome-semantic-acceptance"],
                    verb,
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

    def test_owner_bound_approval_doctrine_is_not_a_contradiction(self) -> None:
        """The detector asks WHO accepts. R13 preserves the developer's and the owner's approvals.

        `completed` is an adjective before it is a noun, so "the completed master branch is approved
        by the developer" and "a proven candidate from a completed leaf is sufficient" are sentences
        about a branch and a candidate. Reading the proximity of two words instead of the
        attribution reddened four cases in a fail-closed lane on doctrine the packet protects -- and
        would do it again to the next author who writes the preservation boundary down.
        """

        for text in (
            "The completed master branch is approved by the developer before it lands.",
            "A proven candidate from a completed leaf is sufficient for the next wave.",
            # Same shape with the outcome NAMED: the acceptance is still the developer's.
            "The completed outcome is approved by the developer before it lands.",
            # The anaphor is read forward only: after a verb, "that outcome" is the verb's object.
            "The manager writes the acceptance envelope for that outcome.",
            # Shipped doctrine: the structural noun is governed by a preposition / is not what the
            # clause attributes anything to, so it is a modifier and not the outcome.
            "`worktree_integrate` auto-closes a completed leaf's worker/reviewer/curator seats",
            "remove this rendered scaffold from the completed turn report and retain only the "
            "exact journal anchor in the table above.",
            # `docs/reference/mcp-tools.md`: a hyphen compound whose `closeout` noun is not an act of
            # closing, and whose subject is a gate. Fired until both sides were boundary-guarded.
            "Every completed leaf-closeout or leaf/master-integration gate atomically replaces the "
            "run status.",
            # The acceptance is another agent's, and it sits beyond the declared window.
            "A completed leaf is required before the manager advances lifecycle state.",
            # The possessive form of the same mistake: the seats close, not the outcome.
            "A completed leaf's worker seats close after the report lands.",
        ):
            with self.subTest(text=text):
                self.assertEqual(contradictions(text), (), text)

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


class DeclaredLimitsTests(unittest.TestCase):
    """Shapes this vocabulary detector cannot classify soundly, pinned at their MEASURED behaviour.

    Each case asserts what the detector does, not what a reader would want it to do. That is the
    point: a limit nobody can see is a limit that gets claimed as reach. If a later change makes one
    of these classify correctly, the case fails on purpose -- the module docstring's declared limit
    has to be rewritten with it, because an undisclosed improvement is how a fail-closed lane starts
    claiming coverage it does not have.
    """

    def test_another_agents_acceptance_inside_the_window_reads_as_the_outcomes(self) -> None:
        """The declared limit: subject is not parsed, so proximity decides inside the window.

        `A completed leaf is required before the manager advances lifecycle state` is clean because
        the acceptance is 31 characters away (``_CLAIM_WINDOW`` is 25). Move the other agent inside
        that window and the verb is read as the outcome's: the measured sentences below fire. Making
        them clean needs subject detection, not another vocabulary entry -- widening the authority
        sanitize far enough to swallow "the manager" was tried and made the packet's own named
        failure ("the manager may treat a completed outcome as acceptance of the handoff") clean too,
        which is a worse trade than this limit.
        """

        for text in (
            "The completed turn is what the manager confirms.",
            "The completed turn is what the owner ratifies.",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    [finding.claim_id for finding in contradictions(text)],
                    ["terminal-outcome-semantic-acceptance"],
                    "declared limit changed: update the module docstring with the new measurement",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
