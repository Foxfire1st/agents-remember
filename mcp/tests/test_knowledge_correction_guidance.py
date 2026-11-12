"""The canonical curator and planner surfaces describe the writer's supported corrections."""

import json
import re
from pathlib import Path

from agents_remember.application.knowledge_gate.gate import __doc__ as GATE_GUIDE
from agents_remember.application.knowledge_writer import __doc__ as WRITER_GUIDE
from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember_test_support.testing.capsule_wording import instruction_surface
from agents_remember_test_support.testing.curator_contracts import (
    PERMISSION_ACTION,
    require_permission_delivery,
)
from agents_remember_test_support.testing.leaf_instruction_wording import (
    assert_action_pointer,
    normalized,
)
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
        "Use mounted `knowledge_write` on `agents-remember-task` or the admitted serving-build command-line writer with the task contract, handoff list and `--commit` or `commit: true`.",
        "Only the tool and input class of a covered correct write admit open manual recovery; every other write retains its tool.",
        "A missing writer is not a correct-write refusal: report the required records not written.",
        "Never edit the derived index.",
        "The commit option authorizes file writes, not Git or acceptance.",
        "`planned` and `refused` wrote nothing.",
        "Writer-report state, written record and entry identifiers and paths, history rows, and each refusal's own reason.",
        "Send code questions and evidenced concerns, including after passing code review, to the recipients that Report findings to their owners names.",
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
    # P5-18: the Curator reaches correct-write recovery only through the shared
    # every-role paragraph, which links the served file; read that served file.
    shared = (ROOT / "core/every-role.md").read_text(encoding="utf-8")
    [paragraph] = [
        line for line in shared.splitlines() if "correct-input refusal of a covered" in line
    ]
    assert paragraph.startswith("For a correct-input refusal"), (
        "unlabelled: every role, Curator too"
    )
    served = "skill://agents-remember/skills/l-01-agent-lifecycles/reference/refused-write.md"
    assert paragraph.count("](" + served + ")") == 1
    assert "{{detail:reference/refused-write.md}}" not in (ROOT / "roles/curator.md").read_text()
    with shipped_composition_corpus() as (origin, _manifest):
        detail = (origin / "reference/refused-write.md").read_text(encoding="utf-8")
    assert detail == (ROOT / "reference/refused-write.md").read_text(encoding="utf-8")
    for clause in (
        "Retain the tool's actual refusal.",
        "Record every input and refusal.",
        "A route requiring hidden edits, an older build without a check, false tool authorship or a weakened false claim is not an admitted route.",
        "Tell the owner of the record or machine state before writing.",
        "This exception covers only the same tool and input class that refused; every other write still uses its tool.",
        "record the write and its provenance;",
        "run every check that still works;",
        "record the defect in an issue row;",
        "put the tool, every input tried, every refusal, the actual write, all check results and the issue row in the full report;",
        "claim no tool receipt for manual work.",
        "Read, check, validation and sync refusals, starts, product guards and commit gates keep their boundaries.",
        PERMISSION_ACTION,
    ):
        assert normalized(clause) in normalized(detail), clause
    with shipped_composition_corpus() as (origin, _manifest):
        # The served file's relative links resolve inside the served package.
        require_permission_delivery(detail, origin)


def test_curator_guidance_names_one_run_rows_proofs_and_link_recovery() -> None:
    markers = (
        "handoff:<entry id>",
        "replace_evidence",
        "line_range",
        "link_target",
        "refresh_not_current",
        "identical changes",
        "current design",
    )
    for relative in ("roles/curator.md", "templates/curator-handoff-list.md"):
        capsule = (ROOT / relative).read_text(encoding="utf-8")
        details = re.findall(r"\{\{detail:([^{}]+)\}\}", capsule)
        # The ingestion template (Architect F1) is a Curator template reached by a real
        # link from this file, not a detail; count it only when that holds.
        ingestion = "templates/curator-handoff-ingestion.md"
        manifest = json.loads((ROOT / "composition-manifest.json").read_text(encoding="utf-8"))
        assert ingestion.removeprefix("templates/") in manifest["roles"]["curator"]["templates"]
        assert re.search(r"\]\((?:\.\./templates/)?curator-handoff-ingestion\.md#", capsule), (
            relative
        )
        details = [*details, ingestion]
        text = " ".join(
            (capsule + "\n" + "\n".join((ROOT / path).read_text() for path in details)).split()
        )
        for marker in markers:
            assert marker in text, (relative, marker)
        recovery = {
            "roles/curator.md": (
                "A stale reconsideration refusal is cleared when you recompute "
                "the worklist and judge its current item."
            ),
            "templates/curator-handoff-list.md": (
                "A stale reconsideration refusal is cleared when you recompute "
                "the worklist and judge its current item."
            ),
        }
        assert recovery[relative] in text, (relative, recovery[relative])


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
    profile = ROOT / "reference/writing/technical-english-profile.md"
    assert profile.is_file(), "The shared curator/explainer writing profile is missing"
    text = " ".join(profile.read_text(encoding="utf-8").split())
    for clause in (
        "one independently testable promise per invariant",
        "20\N{EN DASH}25 words",
        "precision takes priority",
        "It selects clarity principles from [ASD-STE100](https://asd-ste100.org/about_STE.html); "
        'it does not claim full compliance, an "80 percent" measurement or a new controlled-language standard.',
        "actor, trigger, required behavior, forbidden behavior, quantifiers, conditions and exclusions",
        "**Missing:** not supplied.",
        "**Unexamined:** not checked.",
        "**Unavailable:** not readable or obtainable.",
        "**Measured absence:** checked with no instance found in a stated scope.",
        "Before rewriting a producer's statement as a proposed record, read the [handoff contract](../../templates/curator-handoff-list.md#rule-2--carry-producer-data-verbatim) for producer-owned fields and Curator authoring responsibilities.",
        "Have a reader who wrote none of the drafts explain each promise without oral repair.",
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
    assert (
        "../../criteria/onboarding-memory.md#om-2--independent-fact-check-of-curated-knowledge"
        in text
    )
    assert "../../templates/curator-handoff-list.md" in text
    target = "../../templates/curator-handoff-list.md#rule-2--carry-producer-data-verbatim"
    assert f"]({target})" in text
    contract = (profile.parent / target.split("#", 1)[0]).resolve()
    assert contract == (ROOT / "templates/curator-handoff-list.md").resolve()
    body = contract.read_text(encoding="utf-8")
    section = body.split("## Rule 2 — carry producer data verbatim\n", 1)[1].split("\n## ", 1)[0]
    for clause in (
        "Carry `statement`, `kind`, `disposition`, `evidence` and every `found_at` record verbatim from the producer's list.",
        "Do not reword the statement, turn the disposition into an enumeration, summarize the evidence pointer or reorder entries.",
        "The input and output lists must retain the same entries, with `statement`, `kind`, `disposition` and `evidence` byte-identical.",
    ):
        assert clause in " ".join(section.split()), clause


def test_authored_handoff_and_walkthrough_reach_one_profile() -> None:
    """Follow actual authoring links; a profile isolated beside the roles is not delivered."""
    profile = (ROOT / "reference/writing/technical-english-profile.md").resolve()
    sources = (
        ROOT / "templates/curator-handoff-list.md",
        ROOT / "reference/writing/visual-explainers.md",
    )
    for source in sources:
        assert source.is_file(), source
        links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", source.read_text(encoding="utf-8"))
        targets = [(source.parent / link.split("#")[0]).resolve() for link in links]
        assert profile in targets, f"{source} does not link the canonical writing profile"
    explainers = ROOT / "reference/writing/visual-explainers.md"
    explainer_text = normalized(explainers.read_text(encoding="utf-8"))
    assert (
        "An optional walkthrough supplements the required report and its exact evidence."
        in explainer_text
    )
    assert "It cannot replace" in explainer_text
    worker = (ROOT / "roles/worker.md").read_text(encoding="utf-8")
    assert_action_pointer(
        worker,
        "templates/turn-report.md",
        "Before writing the freeze report, read",
    )
    assert_action_pointer(
        worker,
        "reference/writing/visual-explainers.md",
        "Before you add an optional walkthrough to your freeze report, read",
    )
    handoff = sources[0].read_text(encoding="utf-8")
    assert "producer-owned fields remain verbatim" in handoff
    assert "never changes the producer's original statement" in handoff
