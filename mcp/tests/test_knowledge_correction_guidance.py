"""The canonical curator and planner surfaces describe the writer's supported corrections."""

import re
from pathlib import Path

from agents_remember.application.knowledge_gate.gate import __doc__ as GATE_GUIDE
from agents_remember.application.knowledge_writer import __doc__ as WRITER_GUIDE
from agents_remember_test_support.testing.capsule_wording import instruction_surface
from agents_remember_test_support.testing.leaf_instruction_wording import normalized

ROOT = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"


def test_curator_deduplication_keeps_distinct_authority_and_outcome_clauses() -> None:
    surface = normalized(instruction_surface(ROOT, "roles/curator.md"))
    for clause in (
        "Report a missing task; never fabricate a task, worktree or enclosure.",
        "never hand-write records, sidecar entries or history, open or edit the derived index",
        "`planned` and `refused` wrote nothing",
        "report refusals exactly as returned.",
        "Send code questions and evidenced concerns under `../operations/curation.md` §Failure handling, including after code PASS.",
    ):
        assert normalized(clause) in surface, clause


def test_curator_guidance_names_one_run_rows_proofs_and_link_recovery() -> None:
    markers = (
        "handoff:<entry id>",
        "replace_evidence",
        "recompute the worklist",
        "line_range",
        "link_target",
        "refresh_not_current",
        "identical changes",
        "current design",
    )
    for relative in ("roles/curator.md", "templates/curator-handoff-list.md"):
        capsule = (ROOT / relative).read_text(encoding="utf-8")
        details = re.findall(r"\{\{detail:([^{}]+)\}\}", capsule)
        text = " ".join(
            (capsule + "\n" + "\n".join((ROOT / path).read_text() for path in details)).split()
        )
        for marker in markers:
            assert marker in text, (relative, marker)


def test_planning_declares_only_packet_named_recorded_effects_before_implementation() -> None:
    for relative in ("roles/architect.md", "operations/planning.md"):
        text = " ".join((ROOT / relative).read_text(encoding="utf-8").split())
        for marker in ("expectedKnowledgeEffects", "before implementation", "declares nothing"):
            assert marker in text, (relative, marker)


def test_writer_and_gate_do_not_teach_the_retired_installed_cutover() -> None:
    assert WRITER_GUIDE is not None and GATE_GUIDE is not None
    writer = " ".join(WRITER_GUIDE.split())
    gate = " ".join(GATE_GUIDE.split())
    assert "installed database ingest until the cutover" not in writer
    assert "Unconverted memory converts before this writer is used" in writer
    assert "not wired here" not in gate
    assert "cutover's installed build" not in gate
    assert "An unreadable layout probe is never treated as an exemption" in gate


def test_shared_writing_profile_teaches_preservation_and_detectable_meaning_drift() -> None:
    """Protect the delivered review instructions, not a machine judgment of equivalence."""
    profile = ROOT / "reference/shared-writing-profile.md"
    assert profile.is_file(), "The shared curator/explainer writing profile is missing"
    text = " ".join(profile.read_text(encoding="utf-8").split())
    for clause in (
        "one independently testable promise per invariant",
        "20\N{EN DASH}25 words",
        "precision takes priority",
        "does not claim full ASD-STE100 compliance",
        "actor, trigger, required behavior, forbidden behavior, quantifiers, conditions and exclusions",
        "missing, unexamined, unavailable and measured absence",
        "producer-owned fields remain verbatim",
        "A reader who wrote none of the drafts",
        "conforming, violating and boundary cases",
        "`clarify` only after independent review",
        "Removed negation",
        "Changed quantifier",
        "Lost condition",
        "Unrecorded effect",
        "Altered frozen row",
        "no automatic semantic validator",
    ):
        assert clause in text, clause
    assert "../criteria/onboarding-memory.md#om-2" in text
    assert "../templates/curator-handoff-list.md" in text


def test_authored_handoff_conversion_and_walkthrough_reach_one_profile() -> None:
    """Follow actual authoring links; a profile isolated beside the roles is not delivered."""
    profile = (ROOT / "reference/shared-writing-profile.md").resolve()
    sources = (
        ROOT / "templates/curator-handoff-list.md",
        ROOT.parents[1] / "docs/guides/first-conversion.md",
        ROOT.parent / "c-13-install-and-onboard/SKILL.md",
        ROOT / "reference/visual-explainer-authoring.md",
    )
    for source in sources:
        assert source.is_file(), source
        links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", source.read_text(encoding="utf-8"))
        targets = [(source.parent / link.split("#")[0]).resolve() for link in links]
        assert profile in targets, f"{source} does not link the canonical writing profile"
    report = ROOT / "templates/turn-report.md"
    assert "../reference/visual-explainer-authoring.md" in report.read_text(encoding="utf-8")
    handoff = sources[0].read_text(encoding="utf-8")
    assert "producer-owned fields remain verbatim" in handoff
    assert "never changes the producer's original statement" in handoff
