"""The canonical curator and planner surfaces describe the writer's supported corrections."""

import re
from pathlib import Path

from agents_remember.application.knowledge_gate.gate import __doc__ as GATE_GUIDE
from agents_remember.application.knowledge_writer import __doc__ as WRITER_GUIDE
from agents_remember_test_support.testing.capsule_wording import instruction_surface
from agents_remember_test_support.testing.leaf_instruction_wording import delivered_step, normalized
from test_knowledge_bootstrap_procedure import (
    FOUNDATION_AUTHORITY_CLAUSES,
    delivered_foundation,
    foundation_delivery_controls,
    free_agent_capsule,
)

ROOT = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"


def test_curator_deduplication_keeps_distinct_authority_and_outcome_clauses(tmp_path: Path) -> None:
    surface = normalized(instruction_surface(ROOT, "roles/curator.md"))
    for clause in (
        "Use mounted `knowledge_write` or the admitted serving-build CLI with this task's contract, hand-off list and `--commit` (`commit: true`).",
        "Only that tool and input class admits open manual recovery; other writes keep their tools.",
        "A missing writer is not a refused correct write: report required records not written.",
        "Never edit the derived index.",
        "`--commit` (`commit: true`) authorizes file writes, not Git or acceptance.",
        "`planned` and `refused` wrote nothing",
        "writer-report state, written record/entry IDs/paths, history rows and each refusal's own reason.",
        "Send code questions and evidenced concerns under `../operations/curation.md` §Failure handling, including after code PASS.",
    ):
        assert normalized(clause) in surface, clause
    capsule = free_agent_capsule("curator")
    delivered_foundation(capsule, ROOT, tmp_path / "foundation.md", FOUNDATION_AUTHORITY_CLAUSES)
    foundation_delivery_controls(
        capsule,
        ROOT,
        tmp_path / "foundation-controls",
        FOUNDATION_AUTHORITY_CLAUSES,
        "Report a missing task; never fabricate a task, worktree or enclosure.",
    )
    _, detail = delivered_step(
        ROOT,
        ("curator", "curation"),
        "reference/refused-write.md",
        "when a covered writer or record tool refuses a correct write, not before",
        tmp_path / "report.md",
    )
    detail = normalized(detail)
    for clause in (
        "Retain the tool's actual refusal.",
        "Record each input and refusal",
        "a route that requires hidden edits, an older build lacking a check, false tool authorship or a weakened false claim is no route.",
        "Tell the record's or machine state's owner before writing.",
        "This exception covers only the same tool and input class that refused; every other write still uses its tool.",
        "Record the manual write and its provenance, run every check that still works, and record the defect in an issue row.",
        "Put the tool, every input tried, every refusal, the actual write, all check results and that issue row in the full report.",
        "Claim no tool receipt for the manual work.",
        "Read, check, validation and sync refusals, starts, product guards and commit gates retain their boundaries.",
        "A denied or unanswered permission request is not worked around",
    ):
        assert normalized(clause) in detail, clause


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
