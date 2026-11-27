"""The canonical curator and planner surfaces describe the writer's supported corrections."""

import json
import re
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application.knowledge_gate.gate import __doc__ as GATE_GUIDE
from agents_remember.application.knowledge_writer import __doc__ as WRITER_GUIDE
from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember.cli.role_instruction_details import compose_launch_instructions
from agents_remember.models.role_capsules.manifest_derived import compose_manifest
from agents_remember.models.role_capsules.types import compute_content_digest
from agents_remember.serving.capsule_delivery import CapsuleDelivery
from agents_remember_test_support.testing.capsule_budget_d695 import (
    current_budget_view,
    load_current_budget_view,
)
from agents_remember_test_support.testing.curator_contracts import (
    PERMISSION_ACTION,
    SectionLink,
    compile_matrix,
    linked_section,
    require_clauses,
)
from agents_remember_test_support.testing.leaf_instruction_wording import normalized
from test_knowledge_bootstrap_procedure import (
    FOUNDATION_AUTHORITY_CLAUSES,
    FOUNDATION_HEADING,
    FOUNDATION_SOURCE,
    free_agent_capsule,
)

ROOT = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
PERMISSION_CLAUSES = (
    "Never bypass an open/denied harness permission; wait while open, else continue without it and report.",
)


def _assert_index_detail_pointer(
    text: str,
    trigger: str,
    label: str,
    source: str,
    fragment: str = "",
) -> None:
    """Require one Reference-index table row linking the named detail."""
    marker = "{{detail:" + source + "}}" + fragment
    link = f"[{label}]({marker})"
    rows = [
        line
        for line in text.splitlines()
        if line.lstrip().startswith("|") and f"| {trigger} |" in line and link in line
    ]
    assert len(rows) == 1, (trigger, label, source, "missing or repeated index pointer")
    assert text.count(marker) == 1, (source, "expected one detail marker")


def _require_permission_delivery(detail: str, root: Path) -> None:
    """Verify the emitted permission link resolves to the shipped orientation boundary."""
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


def _delivered_foundation(
    capsule: CapsuleDelivery, root: Path, report_path: Path, clauses: tuple[str, ...]
) -> str:
    """Verify the Curator's declared, index-linked and emitted foundation detail."""
    budgets = current_budget_view(load_current_budget_view(root / "capsule-budgets-current.json"))
    assert FOUNDATION_SOURCE in budgets["step_details"], "undeclared budget detail"
    manifest = compose_manifest(json.loads((root / "composition-manifest.json").read_text()))
    declared = manifest["references"]["curator/repository-foundation"]
    assert declared["source"] == FOUNDATION_SOURCE
    assert declared["step_detail"] is True
    assert "curator" in declared["roles"]
    assert declared["step"].strip()
    _assert_index_detail_pointer(
        capsule.trusted_instructions, "Exceptions/sync", "foundation", FOUNDATION_SOURCE
    )
    references = [r for r in capsule.detail_references if r.source_path == FOUNDATION_SOURCE]
    assert len(references) == 1
    reference = references[0]
    assert reference.step == declared["step"]
    assert reference.content == (root / FOUNDATION_SOURCE).read_bytes()
    instructions, record = compose_launch_instructions(capsule, str(report_path), {})
    entries = [r for r in record["detailReferences"] if r["sourcePath"] == FOUNDATION_SOURCE]
    assert len(entries) == 1
    assert entries[0]["step"] == reference.step
    path = Path(entries[0]["path"])
    assert f"[foundation]({path.as_posix()})" in instructions
    assert "{{detail:" not in instructions

    link = re.compile(r"\]\(([^)]+)\)")
    emitted_text, source_text = path.read_text(), reference.content.decode("utf-8")
    assert link.sub("](target)", emitted_text) == link.sub("](target)", source_text)
    with shipped_composition_corpus() as (origin, _manifest):
        owner = origin / FOUNDATION_SOURCE
        for emitted, written in zip(
            link.findall(emitted_text), link.findall(source_text), strict=True
        ):
            if written.startswith("skill://"):
                assert emitted == written
                continue
            emitted_path, _, emitted_anchor = emitted.partition("#")
            written_path, _, written_anchor = written.partition("#")
            assert emitted_anchor == written_anchor, (emitted, written)
            assert Path(emitted_path).resolve() == (owner.parent / written_path).resolve()
    detail = path.read_text()
    assert detail.splitlines()[0] == FOUNDATION_HEADING
    for clause in clauses:
        assert normalized(clause) in normalized(detail), clause
    return detail


def _foundation_delivery_controls(
    capsule: CapsuleDelivery,
    root: Path,
    output: Path,
    clauses: tuple[str, ...],
    sentence: str,
) -> None:
    """All five corruptions fail the foundation delivery proof without editing sources."""
    for mutation in (
        "step_details",
        "heading",
        "sentence",
        "undeclared-pointer",
        "relocated-pointer",
    ):
        copied = output / mutation / "lifecycle"
        for relative in (
            "capsule-budgets-current.json",
            "composition-manifest.json",
            FOUNDATION_SOURCE,
        ):
            target = copied / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, target)
        changed_capsule = capsule
        if mutation == "step_details":
            target = copied / "capsule-budgets-current.json"
            budgets = json.loads(target.read_text())
            del budgets["derived"]["current_measurements"]["step_details"][FOUNDATION_SOURCE]
            target.write_text(json.dumps(budgets))
        elif mutation == "relocated-pointer":
            marker = "{{detail:" + FOUNDATION_SOURCE + "}}"
            assert capsule.trusted_instructions.count(marker) == 1
            changed = capsule.trusted_instructions.replace(f"[foundation]({marker})", "", 1)
            changed += f"\n\n| Unrelated | [foundation]({marker}) |"
            assert changed.count(marker) == 1
            changed_capsule = replace(capsule, trusted_instructions=changed)
        elif mutation == "undeclared-pointer":
            changed = capsule.trusted_instructions.replace(
                "{{detail:" + FOUNDATION_SOURCE + "}}", "{{detail:reference/undeclared.md}}"
            )
            assert changed != capsule.trusted_instructions
            changed_capsule = replace(capsule, trusted_instructions=changed)
        else:
            target = copied / FOUNDATION_SOURCE
            original = target.read_text()
            changed = original.replace(
                FOUNDATION_HEADING if mutation == "heading" else sentence,
                "# Renamed foundation" if mutation == "heading" else "",
            )
            assert changed != original
            target.write_text(changed)
            content = target.read_bytes()
            changed_capsule = replace(
                capsule,
                detail_references=tuple(
                    replace(r, content=content, revision=compute_content_digest(content))
                    if r.source_path == FOUNDATION_SOURCE
                    else r
                    for r in capsule.detail_references
                ),
            )
        with pytest.raises(AssertionError):
            _delivered_foundation(changed_capsule, copied, output / mutation / "report.md", clauses)


def test_curator_deduplication_keeps_distinct_authority_and_outcome_clauses(tmp_path: Path) -> None:
    delivery = next(
        item
        for item in compile_matrix(ROOT)
        if (item.role, item.operation) == ("curator", "curation")
    )
    surface = normalized(delivery.result.render_instructions())
    for clause in (
        "Write `onboarding/`+`knowledge/` via one writer (`knowledge_write` or serving-build CLI `--commit`/`commit: true`; missing writer != correct-input refusal); never edit derived indexes.",
        "`knowledge_write` `commit: true` writes files, not Git",
        "Send evidenced code concerns to Worker+Reviewer; repair no code, author no false knowledge.",
    ):
        assert normalized(clause) in surface, clause

    details = {
        item.source_path: normalized(item.content.decode("utf-8"))
        for item in delivery.result.capsule.detail_references
    }
    for source_path, clause in (
        (
            "reference/curator/publication-report.md",
            "`--commit` authorizes file writes, never a Git commit or acceptance.",
        ),
        (
            "reference/curator/publication-report.md",
            "Read the actual report state, written paths, record and entry identifiers, and refusals.",
        ),
        (
            "reference/curator/exceptions-and-recovery.md",
            "(`planned` and `refused` wrote nothing)",
        ),
        (
            "reference/curator/exceptions-and-recovery.md",
            "writer state/IDs/paths/refusals",
        ),
        (
            "reference/curator/exceptions-and-recovery.md",
            "Send evidenced code concerns to both Worker and Reviewer, including after passing code review, showing the requirement or report mismatch.",
        ),
    ):
        assert normalized(clause) in details[source_path], (source_path, clause)

    capsule = free_agent_capsule("curator")
    _delivered_foundation(capsule, ROOT, tmp_path / "foundation.md", FOUNDATION_AUTHORITY_CLAUSES)
    _foundation_delivery_controls(
        capsule,
        ROOT,
        tmp_path / "foundation-controls",
        FOUNDATION_AUTHORITY_CLAUSES,
        "Report a missing task; never fabricate a task, worktree or enclosure.",
    )
    shared = (ROOT / "core/every-role.md").read_text(encoding="utf-8")
    [paragraph] = [
        line for line in shared.splitlines() if "correct-input refused covered writes" in line
    ]
    assert paragraph.startswith("For correct-input refused covered writes"), (
        "unlabelled: every role, Curator too"
    )
    served = "skill://agents-remember/skills/l-01-agent-lifecycles/reference/refused-write.md"
    assert paragraph.count("](" + served + ")") == 1
    assert "(same tool and input class) before applying a stop." in paragraph
    assert "Other refusals keep stops." in paragraph
    assert "{{detail:reference/refused-write.md}}" not in (ROOT / "roles/curator.md").read_text()
    with shipped_composition_corpus() as (origin, _manifest):
        detail = (origin / "reference/refused-write.md").read_text(encoding="utf-8")
    assert detail == (ROOT / "reference/refused-write.md").read_text(encoding="utf-8")
    for clause in (
        "A missing tool or unadmitted capability is not a refused correct write. Report the missing capability and what remains unwritten.",
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
        _require_permission_delivery(detail, origin)


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
        ingestion = "templates/curator-handoff-ingestion.md"
        manifest = json.loads((ROOT / "composition-manifest.json").read_text(encoding="utf-8"))
        assert ingestion.removeprefix("templates/") in manifest["roles"]["curator"]["templates"]
        assert re.search(
            r"\]\((?:\.\./templates/)?curator-handoff-ingestion\.md(?:#[^)]*)?\)", capsule
        ), relative
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
    expected = (
        ("roles/architect.md", "pre-build"),
        ("operations/planning.md", "before implementation"),
    )
    for relative, timing in expected:
        text = " ".join((ROOT / relative).read_text(encoding="utf-8").split())
        for marker in ("expectedKnowledgeEffects", timing, "(else none)"):
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
    _assert_index_detail_pointer(
        worker, "Task/report/list", "report", "templates/turn-report.md", "#shape"
    )
    _assert_index_detail_pointer(
        worker, "Task/report/list", "explainer", "reference/writing/visual-explainers.md"
    )
    handoff = sources[0].read_text(encoding="utf-8")
    assert "producer-owned fields remain verbatim" in handoff
    assert "never changes the producer's original statement" in handoff
