"""Scheduled curation delivery contracts: real source and compiled delivery proof.

Verifies source-local duties, actual audiences, step-detail reach, emitted links,
and mutation controls across the three-tier progressive-disclosure lifecycle corpus.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from agents_remember.application.role_capsules.shared_block import filter_shared_block
from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember.cli.__main__ import build_parser
from agents_remember.cli.role_brief_message import shared_block as render_shared_block
from agents_remember.cli.role_instruction_details import compose_launch_instructions
from agents_remember.serving.capsule_delivery import capsule_delivery_from
from agents_remember_test_support.testing.curation_doctrine import CURATION_DOCTRINE_SURFACES
from agents_remember_test_support.testing.curator_contracts import (
    AUTHORED_HISTORY,
    AUTHORING,
    AUTHORING_TRUTH,
    CARD,
    CARD_FACTCHECK,
    CHANGE_ROUTING,
    CITATION_FIX_BOUNDARY,
    CRITERIA,
    CRITERIA_TRUTH,
    DISPOSITIONS,
    FACTCHECK_CRITERIA,
    KNOWLEDGE,
    OPERATION,
    PUBLICATION,
    PUBLICATION_SEMANTICS,
    ROLE,
    SEMANTIC_TRUTH,
    WRITER,
    WRITER_IDENTITY,
    Delivered,
    compile_matrix,
    expected_bound_detail,
    healing_findings,
    normalized,
    require_clauses,
    writer_findings,
)
from agents_remember_test_support.testing.leaf_instruction_wording import (
    assert_emitted_detail_links,
)

ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = ROOT / "skills/l-01-agent-lifecycles"
EXCEPTIONS = "reference/curator/exceptions-and-recovery.md"
MEMORY_HANDOVER = "reference/curator/memory-handover.md"

DETAIL_INDEX_ENTRIES = {
    AUTHORING: ("Cards/style", "sentences", {"curator"}),
    CARD: ("Cards/style", "cards", {"curator"}),
    WRITER: ("Knowledge/CLI", "cli", {"worker", "curator"}),
    PUBLICATION: (
        {"curator": "Write/route", "consolidator": "Write/families"},
        "pub",
        {"curator", "consolidator"},
    ),
    AUTHORED_HISTORY: ("Cards/style", "prose", {"curator"}),
    CHANGE_ROUTING: ("Write/route", "route", {"curator"}),
    EXCEPTIONS: ("Exceptions/sync", "exceptions", {"curator"}),
}

CURATOR_ROLE_CLAUSES = (
    "Reconcile one leaf's intent and implementation in one fresh session, update affected memory, and deliver the coherence record.",
    "Write `onboarding/`+`knowledge/` via one writer (`knowledge_write` or serving-build CLI `--commit`/`commit: true`; missing writer != correct-input refusal); never edit derived indexes.",
    "Withdraw falsified claims; discoveries use `capture-candidate`, never current intent.",
    "Fix faulty inputs on same writer, repair own/tool/coherence faults, stop after 3 failed repairs on one item and tell Manager.",
)

FULL_CHECKS_CLAUSES = (
    "Report sidecars, overviews/indexes/entities, `git diff --check`, `knowledge_integrity_check` + full `memory_quality_check` (scoped checks never substitute; rerun until `curatorActionableCount=0` + `qualityChecklistStatus=ready-for-closeout`).",
    "Closeout runs stale `dependent` check (see exceptions).",
    "Non-authoring seat runs [OM-2](../criteria/onboarding-memory.md#om-2--independent-fact-check-of-curated-knowledge).",
)

HANDOVER_REPORT_CLAUSES = (
    "Requested curation reports cover changed paths, reconciliation/repairs/answers, commands and results, writer state/IDs/paths/refusals, family coverage (updated plus unchanged with reasons; mark unexamined), worklist count and host/helper support (delegated work and how it was verified; state explicitly when none was used).",
)

CURATOR_STOPS_AND_UNFINISHED_CLAUSES = (
    "Stop for an open permission prompt, a standing refusal outside covered-write recovery, a question about what the requirement means, or an effect outside the assigned worktree, scratch folder, and task.",
    "If you have a parent and remain unfinished at turn end, tell that parent what and whom you await via `role_message`.",
)

SHARED_CONTINUATION_KERNEL = (
    "Parallelize independent work (serial landing, no cap).",
    "Never poll, watch, or timer-loop; answer wakes first.",
    "End waits with `WAITING FOR SUBAGENTS:` or `WAITING FOR BACKGROUND:` (process, id/log, expected end), sending no parent progress `role_message`.",
)

AUTHORITY_CONTINUATION_REFERENCE = (
    "Collect helpers and continue ordinary work.",
    "Use the helper-wait procedure declared by your launch; otherwise preserve the harness's native collection within the turn.",
)

SERVING_AUTHORITY_CLAUSES = (
    "Decide/write via starting server or serving-build command-line interface (CLI) against clean main (name commit);",
    "leaf source never produces leaf memory (test on fixtures/scratch);",
    "install nothing, disable no guard.",
)

WRITER_OPERATION_CLAUSES = (
    "Verify writer outputs resolve in `workspace.memoryRoot`.",
    "Keep partial/refused handovers partial.",
)

FACTCHECK_OPERATION_CLAUSES = (
    "### Checks and gates",
    "Non-authoring seat runs [OM-2](../criteria/onboarding-memory.md#om-2--independent-fact-check-of-curated-knowledge).",
)


@pytest.fixture(scope="module")
def deliveries() -> tuple[Delivered, ...]:
    return compile_matrix(LIFECYCLE)


def source(path: str) -> str:
    return (LIFECYCLE / path).read_text(encoding="utf-8")


def _assert_index_detail_pointer(
    text: str,
    trigger: str,
    label: str,
    source_path: str,
    fragment: str = "",
) -> None:
    """Require one Reference-index table row linking the named detail."""
    marker = "{{detail:" + source_path + "}}" + fragment
    link = f"[{label}]({marker})"
    rows = [
        line
        for line in text.splitlines()
        if line.lstrip().startswith("|") and f"| {trigger} |" in line and link in line
    ]
    assert len(rows) == 1, (trigger, label, source_path, "missing or repeated index pointer")
    assert text.count(marker) == 1, (source_path, "expected one detail marker")


def _extract_section(text: str, heading: str) -> str:
    assert text.count(heading + "\n") == 1, heading
    level = len(heading) - len(heading.lstrip("#"))
    body = text.split(heading + "\n", 1)[1]
    return re.split(r"(?m)^#{1," + str(level) + r"} ", body, maxsplit=1)[0]


def _assert_emitted_om2_link(text: str, criteria_path: Path) -> None:
    om2_links = re.findall(r"\[OM-2\]\(([^)]+)\)", text)
    assert len(om2_links) == 1, om2_links
    om2_parts = urlsplit(om2_links[0])
    assert Path(unquote(om2_parts.path)).resolve() == criteria_path.resolve()
    assert om2_parts.fragment == "om-2--independent-fact-check-of-curated-knowledge"


def test_curator_role_itself_retains_complete_truth_continuation_stops_and_exceptions(
    deliveries: tuple[Delivered, ...],
) -> None:
    require_clauses(source(ROLE), CURATOR_ROLE_CLAUSES, ROLE)
    _assert_index_detail_pointer(source(ROLE), "Exceptions/sync", "exceptions", EXCEPTIONS)
    exceptions_clauses = (
        "Report unsatisfied checks as blocked.",
        *HANDOVER_REPORT_CLAUSES,
        *CURATOR_STOPS_AND_UNFINISHED_CLAUSES,
    )
    require_clauses(source(EXCEPTIONS), exceptions_clauses, EXCEPTIONS)
    for clause in CURATOR_STOPS_AND_UNFINISHED_CLAUSES:
        reading = normalized(source(EXCEPTIONS))
        mutant = reading.replace(normalized(clause), "", 1)
        assert mutant != reading
        with pytest.raises(AssertionError):
            require_clauses(mutant, exceptions_clauses, "deleted curator stop or parent notice")
    require_clauses(
        source("core/every-role.md"),
        (
            "For correct-input refused covered writes, follow [refused-write](skill://agents-remember/skills/l-01-agent-lifecycles/reference/refused-write.md) (same tool and input class) before applying a stop.",
            "Other refusals keep stops.",
            *SHARED_CONTINUATION_KERNEL,
        ),
        "core/every-role.md",
    )
    require_clauses(
        source("core/authority.md"),
        AUTHORITY_CONTINUATION_REFERENCE,
        "core/authority.md",
    )

    checks = _extract_section(source(OPERATION), "### Checks and gates")
    require_clauses(checks, FULL_CHECKS_CLAUSES, "curation full checks home")
    for clause in FULL_CHECKS_CLAUSES:
        reading = normalized(checks)
        mutant = reading.replace(normalized(clause), "", 1)
        assert mutant != reading
        with pytest.raises(AssertionError):
            require_clauses(mutant, FULL_CHECKS_CLAUSES, "deleted check obligation")

    for mutant in (
        source(ROLE).replace("[exceptions](", "[missing](", 1),
        source(ROLE).replace("{{detail:" + EXCEPTIONS + "}}", "{{detail:reference/wrong.md}}", 1),
        source(ROLE).replace("| Exceptions/sync |", "| Other |", 1),
    ):
        assert mutant != source(ROLE)
        with pytest.raises(AssertionError):
            _assert_index_detail_pointer(mutant, "Exceptions/sync", "exceptions", EXCEPTIONS)

    for delivery in deliveries:
        role_block = delivery.block("role")
        if delivery.role == "curator":
            require_clauses(role_block, CURATOR_ROLE_CLAUSES, (delivery.role, delivery.operation))
            _assert_index_detail_pointer(role_block, "Exceptions/sync", "exceptions", EXCEPTIONS)
            exceptions_detail = next(
                item
                for item in delivery.result.capsule.detail_references
                if item.source_path == EXCEPTIONS
            )
            require_clauses(
                exceptions_detail.content.decode(),
                exceptions_clauses,
                (delivery.role, delivery.operation, EXCEPTIONS),
            )
            assert delivery.shared_block is not None
            assert delivery.shared_block == filter_shared_block(
                source("core/every-role.md"), delivery.role
            )
            require_clauses(
                delivery.shared_block,
                SHARED_CONTINUATION_KERNEL,
                (delivery.role, delivery.operation, "admitted shared continuation"),
            )
            emitted_shared = render_shared_block(delivery.shared_block)
            assert emitted_shared.count("## What every role acts on") == 1
            for clause in SHARED_CONTINUATION_KERNEL:
                assert normalized(emitted_shared).count(normalized(clause)) == 1
            assert not any(
                block.source_path == "core/every-role.md"
                for block in delivery.result.capsule.instructions
            )
        else:
            assert ROLE not in {
                block.source_path for block in delivery.result.capsule.instructions
            }, (delivery.role, delivery.operation)

    require_clauses(
        source("operations/orientation.md"),
        SERVING_AUTHORITY_CLAUSES,
        "operations/orientation.md",
    )
    require_clauses(source(OPERATION), WRITER_OPERATION_CLAUSES, OPERATION)
    require_clauses(
        source("reference/refused-write.md"),
        ("A missing tool or unadmitted capability is not a refused correct write.",),
        "reference/refused-write.md",
    )
    for delivery in deliveries:
        if (delivery.role, delivery.operation) == ("curator", "curation"):
            op_block = delivery.block("operation")
            require_clauses(op_block, WRITER_OPERATION_CLAUSES, OPERATION)
            require_clauses(
                _extract_section(op_block, "### Checks and gates"),
                FULL_CHECKS_CLAUSES,
                (delivery.role, delivery.operation, "Checks and gates"),
            )


def test_step_details_reach_actual_files_at_actions_over_the_complete_matrix(
    deliveries: tuple[Delivered, ...],
    tmp_path: Path,
) -> None:
    for delivery in deliveries:
        details = {item.source_path: item for item in delivery.result.capsule.detail_references}
        rendered = delivery.result.render_instructions()
        for name, (trigger, label, carriers) in DETAIL_INDEX_ENTRIES.items():
            # Manifest admission alone does not deliver a detail without a role pointer.
            assert (name in details) == (delivery.role in carriers), (
                delivery.role,
                delivery.operation,
                name,
            )
            pointer = "{{detail:" + name + "}}"
            assert (pointer in rendered) == (delivery.role in carriers), (
                delivery.role,
                delivery.operation,
                name,
            )
            if name in details:
                assert details[name].step.strip()
                row_trigger = trigger[delivery.role] if isinstance(trigger, dict) else trigger
                _assert_index_detail_pointer(rendered, row_trigger, label, name)
                assert details[name].content == (LIFECYCLE / name).read_bytes()
        if delivery.role == "curator":
            report = tmp_path / delivery.role / delivery.operation / "request.md"
            instructions, record = compose_launch_instructions(
                capsule_delivery_from(delivery.result),
                str(report),
                {},
            )
            references = {entry["sourcePath"]: entry for entry in record["detailReferences"]}
            for name in DETAIL_INDEX_ENTRIES:
                entry = references[name]
                actual = Path(entry["path"])
                assert entry["step"] == details[name].step
                with shipped_composition_corpus() as (origin, _):
                    targets = {
                        (origin / key).resolve(): Path(value["path"])
                        for key, value in references.items()
                    }
                    source_bytes = (LIFECYCLE / name).read_bytes()
                    assert (origin / name).read_bytes() == source_bytes
                    expected = expected_bound_detail(source_bytes.decode(), origin / name, targets)
                assert actual.read_bytes() == expected
                assert entry["sizeBytes"] == len(expected)
                assert entry["revision"] == "sha256:" + hashlib.sha256(expected).hexdigest()
                assert_emitted_detail_links(instructions, rendered, name, actual)
            assert "{{detail:" not in instructions
            with shipped_composition_corpus() as (origin, _):
                assert (origin / OPERATION).read_bytes() == (LIFECYCLE / OPERATION).read_bytes()
                checks = _extract_section(
                    (origin / OPERATION).read_text(encoding="utf-8"), "### Checks and gates"
                )
                require_clauses(checks, FULL_CHECKS_CLAUSES, "shipped curation checks owner")
                if delivery.operation == "curation":
                    emitted_checks = _extract_section(instructions, "### Checks and gates")
                    require_clauses(
                        emitted_checks,
                        FULL_CHECKS_CLAUSES[:-1],
                        "actual emitted curation check owner",
                    )
                    _assert_emitted_om2_link(emitted_checks, origin / CRITERIA)

    # Keep the oracle controls in this existing delivery test node.
    origin = tmp_path / "source" / "reference" / "detail.md"
    sibling = origin.with_name("other.md")
    frozen = tmp_path / "launch.other.md"
    original = (
        "Keep every duty. [sibling](other.md#rule) [self](#step) [external](https://example.test/x)"
    )
    expected = (
        f"Keep every duty. [sibling]({frozen.as_posix()}#rule) "
        f"[self]({origin.as_posix()}#step) [external](https://example.test/x)"
    ).encode()
    result = expected_bound_detail(original, origin, {sibling: frozen})
    assert result == expected
    assert result != expected.replace(b"Keep every duty. ", b"")
    assert result != expected.replace(b"launch.other.md#rule", b"wrong.md#rule")
    # A source-selected fragment must survive actual emitted-link binding exactly.
    compiled = "[step]({{detail:reference/detail.md}}#rule)"
    emitted = f"[step]({frozen.as_posix()}#rule)"
    assert_emitted_detail_links(emitted, compiled, "reference/detail.md", frozen)
    for mutant in (
        emitted.replace("#rule", "#wrong-section"),
        emitted.replace("#rule", ""),
        emitted.replace(frozen.as_posix(), frozen.with_name("unbound.md").as_posix()),
    ):
        assert mutant != emitted
        with pytest.raises(AssertionError):
            assert_emitted_detail_links(mutant, compiled, "reference/detail.md", frozen)


def test_current_truth_and_all_carrier_repair_are_in_delivered_reconciliation_detail(
    deliveries: tuple[Delivered, ...],
) -> None:
    require_clauses(source(AUTHORING), AUTHORING_TRUTH, AUTHORING)
    for delivery in deliveries:
        if delivery.role == "curator":
            detail = next(
                item
                for item in delivery.result.capsule.detail_references
                if item.source_path == AUTHORING
            )
            require_clauses(
                detail.content.decode(), AUTHORING_TRUTH, (delivery.operation, AUTHORING)
            )
            assert detail.step.strip()
            trigger, label, _ = DETAIL_INDEX_ENTRIES[AUTHORING]
            _assert_index_detail_pointer(
                delivery.result.render_instructions(), trigger, label, AUTHORING
            )
            # Procedural content remains disclosed at reconciliation, not eagerly in the capsule.
            assert "Repair the class, not just one example." not in delivery.block("role")


def test_all_invariant_definitions_and_separate_family_revision_binding_are_delivered(
    deliveries: tuple[Delivered, ...],
) -> None:
    require_clauses(source(CARD), DISPOSITIONS, CARD)
    for delivery in deliveries:
        if delivery.role == "curator":
            detail = next(
                item
                for item in delivery.result.capsule.detail_references
                if item.source_path == CARD
            )
            require_clauses(detail.content.decode(), DISPOSITIONS, (delivery.operation, CARD))
            assert detail.step.strip()
            trigger, label, _ = DETAIL_INDEX_ENTRIES[CARD]
            _assert_index_detail_pointer(
                delivery.result.render_instructions(), trigger, label, CARD
            )
            _assert_index_detail_pointer(delivery.block("role"), trigger, label, CARD)


def test_healing_corpus_rejects_own_fault_only_and_blanket_debt_routing() -> None:
    findings = []
    for carrier in CURATION_DOCTRINE_SURFACES:
        for path in sorted((ROOT / carrier).rglob("*.md")):
            findings.extend(
                (path.relative_to(ROOT).as_posix(), finding)
                for finding in healing_findings(path.read_text())
            )
    assert not findings, findings
    assert healing_findings("Repair only your own memory faults.") == ("own-fault-only",)
    assert healing_findings("Route all encountered memory debt to the Manager.") == (
        "route-all-debt",
    )
    assert not healing_findings(
        "Repair encountered memory, whoever left it; route work needing code and make no broad sweep."
    )
    _assert_index_detail_pointer(source(ROLE), "Exceptions/sync", "exceptions", EXCEPTIONS)
    require_clauses(
        source(EXCEPTIONS),
        (
            "Apply bounded encountered-memory repairs—whoever left it, beyond the quality check's findings—without a broad sweep, report every repair",
            "Send evidenced code concerns to both Worker and Reviewer",
            "route other cross-leaf matters through the Manager.",
        ),
        EXCEPTIONS,
    )


def test_writer_source_and_compilation_keep_identity_arguments_and_report_semantics(
    deliveries: tuple[Delivered, ...],
) -> None:
    require_clauses(source(WRITER), WRITER_IDENTITY, WRITER)
    require_clauses(source(PUBLICATION), PUBLICATION_SEMANTICS, PUBLICATION)
    for delivery in deliveries:
        if delivery.role == "curator":
            detail = next(
                item
                for item in delivery.result.capsule.detail_references
                if item.source_path == WRITER
            )
            require_clauses(detail.content.decode(), WRITER_IDENTITY, (delivery.operation, WRITER))
            assert not writer_findings(detail.content.decode())
            publication = next(
                item
                for item in delivery.result.capsule.detail_references
                if item.source_path == PUBLICATION
            )
            require_clauses(
                publication.content.decode(),
                PUBLICATION_SEMANTICS,
                (delivery.operation, PUBLICATION),
            )
    for path in (ROLE, OPERATION, WRITER):
        assert not writer_findings(source(path)), path
    assert writer_findings("agents-remember knowledge-ingest --contract leaf --list data") == (
        "bare-PATH-writer",
    )
    assert writer_findings("Use the leaf writer to author this leaf's memory.") == (
        "leaf-self-production",
    )
    # This exercises registration and parsing only, never a writer against this leaf's memory.
    arguments = [
        "knowledge-ingest",
        "--contract",
        "fixture",
        "--list",
        "list.json",
        "--authorization-ref",
        "fixture",
        "--commit",
        "--json",
    ]
    assert callable(build_parser().parse_args(arguments).func)
    require_clauses(
        source(PUBLICATION),
        (
            "The ingest state is written, planned or refused.",
            "A refused ingest wrote nothing and names each problem with its own reason.",
            "Keep a partial handover visibly partial.",
        ),
        PUBLICATION,
    )


def test_writer_examples_reject_bare_path_in_tables_and_shell() -> None:
    examples = (
        "| Example | `agents-remember knowledge-ingest --contract leaf --list data` |",
        "| Example | `agents-remember knowledge-bootstrap --repo app --list data` |",
        "```sh\nagents-remember knowledge-ingest --contract leaf --list data\n```",
        "$ agents-remember knowledge-bootstrap --repo app --list data",
    )
    for example in examples:
        assert writer_findings(example) == ("bare-PATH-writer",), example
    for command in ("knowledge-ingest", "knowledge-bootstrap"):
        bound = (
            'PYTHONPATH="$AR_SERVING_PARENT" "$AR_PYTHON" -P -m '
            f"agents_remember.cli {command} --list data"
        )
        assert not writer_findings(f"| Example | `{bound}` |")
        assert not writer_findings(f"```sh\n{bound}\n```")
    assert not writer_findings("The `agents-remember knowledge-ingest` command writes records.")


def test_writer_examples_in_complete_curator_delivery_and_followed_skills(
    deliveries: tuple[Delivered, ...],
) -> None:
    pending = []
    for delivery in deliveries:
        if delivery.role != "curator":
            continue
        for block in delivery.result.capsule.instructions:
            assert not writer_findings(block.content), (delivery.operation, block.source_path)
            pending.append((LIFECYCLE / block.source_path, block.content))
        for detail in delivery.result.capsule.detail_references:
            text = detail.content.decode()
            assert not writer_findings(text), (delivery.operation, detail.source_path)
            pending.append((LIFECYCLE / detail.source_path, text))
    followed = set()
    while pending:
        path, text = pending.pop()
        path = path.resolve()
        if path in followed:
            continue
        followed.add(path)
        assert not writer_findings(text), path.relative_to(ROOT)
        for reference in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            target = reference.split("#", 1)[0]
            if target.startswith("skill://agents-remember/"):
                linked = ROOT / target.removeprefix("skill://agents-remember/")
            else:
                linked = path.parent / target
            linked = linked.resolve()
            if (
                linked.is_relative_to(ROOT / "skills")
                and linked.suffix == ".md"
                and linked.is_file()
            ):
                pending.append((linked, linked.read_text()))
    assert LIFECYCLE / "reference/knowledge/commands.md" in followed


def test_authored_effect_rule_names_retiring_deleted_rows(
    deliveries: tuple[Delivered, ...],
) -> None:
    clause = (
        "A `changed` row names its effect; a `deleted` row retiring the invariant names only "
        "`retire`; other invariant rows name no effect."
    )
    assert clause in source(AUTHORED_HISTORY)
    for delivery in deliveries:
        if delivery.role == "curator":
            detail = next(
                item
                for item in delivery.result.capsule.detail_references
                if item.source_path == AUTHORED_HISTORY
            )
            assert clause in detail.content.decode(), delivery.operation


def card_maintenance_links(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\[cards\]\(([^)]+)\)", text))


CURATOR_ROLE = "roles/curator.md"
DETAIL_TARGET = re.compile(r"^\{\{detail:([^}]+)\}\}(?:#(.*))?$")


def _source_target(origin: str, target: str) -> tuple[Path, str]:
    """Resolve a source link, `{{detail:F}}#a` or relative, to its file and fragment."""
    detail = DETAIL_TARGET.match(target)
    if detail:
        return (LIFECYCLE / detail[1]).resolve(), detail[2] or ""
    parts = urlsplit(target)
    assert not parts.scheme and not parts.netloc and not parts.query
    return (LIFECYCLE / origin).parent.joinpath(unquote(parts.path)).resolve(), parts.fragment


def test_card_maintenance_links_resolve_from_source_and_compiled_source_origins(
    deliveries: tuple[Delivered, ...],
) -> None:
    expected = (
        "## Choose card and overview scope",
        "## Keep current card content",
        "## Refresh or remove a reference",
        "For a changed target, replace its old evidence line with one citation-table row",
        "To withdraw a reference, remove its evidence line and any prose citing its number",
        "For a source rename, complete the entry moves through the writer before moving its card",
        "## Maintain the entity catalog",
        "Inspect the evidence and decide whether the entity's meaning, identifiers, relationships, naming or references changed before refreshing it.",
        "establish whether the entity moved, was renamed or was removed before replacing paths or deleting rows",
    )
    deletion = (
        "When the leaf deletes or renames a source file, retire its card and re-point or remove "
        "every reference, including references the validator only reports."
    )
    require_clauses(source(CHANGE_ROUTING), (deletion,), CHANGE_ROUTING)
    source_links = card_maintenance_links(source(CURATOR_ROLE))
    assert len(source_links) == 1
    assert card_maintenance_links(source(OPERATION)) == ()
    resolved = [_source_target(CURATOR_ROLE, target) for target in source_links]
    assert {path.name for path, _fragment in resolved} == {Path(CARD).name}
    for path, _fragment in resolved:
        assert path.is_relative_to(ROOT / "skills") and path.is_file(), path
        require_clauses(path.read_text(), expected, path)
    for delivery in deliveries:
        operation_block = next(
            item
            for item in delivery.result.capsule.instructions
            if item.composition_root == "operation"
        )
        assert card_maintenance_links(operation_block.content) == ()
        role_block = (
            next(
                item
                for item in delivery.result.capsule.instructions
                if item.source_path == CURATOR_ROLE
            )
            if delivery.role == "curator"
            else None
        )
        links = card_maintenance_links(role_block.content) if role_block else ()
        assert len(links) == (1 if delivery.role == "curator" else 0), (
            delivery.role,
            delivery.operation,
        )
        for target in links:
            path, _fragment = _source_target(CURATOR_ROLE, target)
            assert path.is_relative_to(ROOT / "skills") and path.is_file(), target
            require_clauses(path.read_text(), expected, (delivery.role, delivery.operation, target))
        if delivery.role == "curator":
            authoring = next(
                item
                for item in delivery.result.capsule.detail_references
                if item.source_path == CHANGE_ROUTING
            )
            require_clauses(
                authoring.content.decode(), (deletion,), (delivery.operation, CHANGE_ROUTING)
            )
        if links and delivery.operation == "curation":
            require_clauses(
                operation_block.content,
                (
                    "3. Route notes to file/route/entity or Operational-Notes (edit entities for real changes only).",
                    "4. Update cards/sidecars/prose; run leaf-scoped `route_index_refresh`.",
                ),
                OPERATION,
            )


def _require_factcheck_chain(
    card: tuple[str, Path],
    operation_path: Path,
    criteria_path: Path,
    operation_text: str,
    criteria_text: str,
) -> None:
    """Follow a card's fact-check route through its operation and criterion."""
    card_text, card_origin = card
    card_links = re.findall(r"\[the curation fact-check procedure\]\(([^)]+)\)", card_text)
    assert len(card_links) == 1
    parts = urlsplit(card_links[0])
    assert not parts.scheme and not parts.netloc and not parts.query
    assert parts.fragment == "workflow"
    assert (card_origin.parent / unquote(parts.path)).resolve() == operation_path.resolve()
    assert operation_path.is_file()
    assert "## Workflow\n" in operation_text
    require_clauses(card_text, CARD_FACTCHECK, card_origin)
    require_clauses(operation_text, FACTCHECK_OPERATION_CLAUSES, operation_path)
    links = re.findall(r"\[OM-2\]\(([^)]+)\)", operation_text)
    assert len(links) == 1
    parts = urlsplit(links[0])
    assert not parts.scheme and not parts.netloc and not parts.query
    assert parts.fragment == "om-2--independent-fact-check-of-curated-knowledge"
    assert (operation_path.parent / unquote(parts.path)).resolve() == criteria_path.resolve()
    assert criteria_path.is_file()
    heading = "### OM-2 — Independent fact-check of curated knowledge\n"
    assert heading in criteria_text
    section = criteria_text.split(heading, 1)[1].split("\n### ", 1)[0]
    require_clauses(section, FACTCHECK_CRITERIA, criteria_path)


def test_semantic_truth_counts_lines_and_independent_factcheck_keep_their_homes(
    deliveries: tuple[Delivered, ...],
    tmp_path: Path,
) -> None:
    require_clauses(
        source(MEMORY_HANDOVER),
        ("[Review rounds](../../operations/review.md#rounds)",),
        MEMORY_HANDOVER,
    )
    assert "## Rounds\n" in source("operations/review.md")
    card_text = source(CARD)
    operation_text = source(OPERATION)
    criteria_text = source(CRITERIA)
    require_clauses(card_text, (*SEMANTIC_TRUTH, *CARD_FACTCHECK), CARD)
    require_clauses(source(KNOWLEDGE), CITATION_FIX_BOUNDARY, KNOWLEDGE)
    require_clauses(
        criteria_text,
        (
            *CRITERIA_TRUTH,
            "Read the whole affected body. A true paragraph added beside a contradictory claim does not repair it.",
            "A role agent that wrote none of the leaf's cards or records checks curation before closeout.",
        ),
        CRITERIA,
    )
    _require_factcheck_chain(
        (card_text, LIFECYCLE / CARD),
        LIFECYCLE / OPERATION,
        LIFECYCLE / CRITERIA,
        operation_text,
        criteria_text,
    )
    for text, clauses, where in (
        (card_text, CARD_FACTCHECK, CARD),
        (operation_text, FACTCHECK_OPERATION_CLAUSES, OPERATION),
        (criteria_text, FACTCHECK_CRITERIA, CRITERIA),
    ):
        reading = normalized(text)
        for clause in clauses:
            mutant = reading.replace(normalized(clause), "", 1)
            assert mutant != reading
            with pytest.raises(AssertionError):
                require_clauses(mutant, clauses, where)
    # The same route assertion rejects a missing pointer, either wrong home, and a renamed criterion.
    for damaged_card, damaged_operation, damaged_criteria in (
        (
            card_text.replace("[the curation fact-check procedure]", "[missing procedure]", 1),
            operation_text,
            criteria_text,
        ),
        (
            card_text.replace(
                "../../operations/curation.md#workflow",
                "../../operations/review.md#workflow",
                1,
            ),
            operation_text,
            criteria_text,
        ),
        (
            card_text,
            operation_text.replace(
                "../criteria/onboarding-memory.md#om-2--independent-fact-check-of-curated-knowledge",
                "../criteria/code-seam.md#om-2--independent-fact-check-of-curated-knowledge",
                1,
            ),
            criteria_text,
        ),
        (
            card_text,
            operation_text,
            criteria_text.replace(
                "### OM-2 — Independent fact-check of curated knowledge", "### Renamed criterion", 1
            ),
        ),
    ):
        assert (damaged_card, damaged_operation, damaged_criteria) != (
            card_text,
            operation_text,
            criteria_text,
        )
        with pytest.raises(AssertionError):
            _require_factcheck_chain(
                (damaged_card, LIFECYCLE / CARD),
                LIFECYCLE / OPERATION,
                LIFECYCLE / CRITERIA,
                damaged_operation,
                damaged_criteria,
            )
    for delivery in deliveries:
        if delivery.role == "curator":
            detail = next(
                item
                for item in delivery.result.capsule.detail_references
                if item.source_path == CARD
            )
            require_clauses(
                detail.content.decode(),
                (*SEMANTIC_TRUTH, *CARD_FACTCHECK),
                (delivery.operation, CARD),
            )
            _require_factcheck_chain(
                (detail.content.decode(), LIFECYCLE / CARD),
                LIFECYCLE / OPERATION,
                LIFECYCLE / CRITERIA,
                operation_text,
                criteria_text,
            )
            commands = next(
                item
                for item in delivery.result.capsule.detail_references
                if item.source_path == KNOWLEDGE
            )
            require_clauses(
                commands.content.decode(), CITATION_FIX_BOUNDARY, (delivery.operation, KNOWLEDGE)
            )
            _assert_index_detail_pointer(
                delivery.block("role"), "Coherence/msg", "handover", MEMORY_HANDOVER
            )
            assert delivery.shared_block is not None
            require_clauses(
                delivery.shared_block,
                ("Run harness commands in supplied scratch roots only, never a worktree.",),
                (delivery.role, delivery.operation, "scratch root boundary"),
            )
            instructions, record = compose_launch_instructions(
                capsule_delivery_from(delivery.result),
                str(tmp_path / delivery.role / delivery.operation / "factcheck.md"),
                {},
            )
            card_record = next(
                item for item in record["detailReferences"] if item["sourcePath"] == CARD
            )
            emitted_card = Path(card_record["path"])
            assert emitted_card.is_file()
            with shipped_composition_corpus() as (origin, _):
                assert (origin / OPERATION).read_bytes() == (LIFECYCLE / OPERATION).read_bytes()
                assert (origin / CRITERIA).read_bytes() == (LIFECYCLE / CRITERIA).read_bytes()
                if delivery.operation == "curation":
                    require_clauses(
                        instructions,
                        (
                            "### Checks and gates",
                            "Non-authoring seat runs [OM-2](",
                            "Curation is evidence, not a gate.",
                        ),
                        (delivery.role, OPERATION),
                    )
                    _assert_emitted_om2_link(instructions, origin / CRITERIA)
                _require_factcheck_chain(
                    (emitted_card.read_text(), emitted_card),
                    origin / OPERATION,
                    origin / CRITERIA,
                    (origin / OPERATION).read_text(),
                    (origin / CRITERIA).read_text(),
                )
        if (delivery.role, delivery.operation) == ("curator", "curation"):
            require_clauses(delivery.block("operation"), FACTCHECK_OPERATION_CLAUSES, OPERATION)
            assert "../criteria/onboarding-memory.md" in delivery.block("operation")


def test_card_maintenance_links_resolve_from_the_actual_emitted_launch_file(
    deliveries: tuple[Delivered, ...],
    tmp_path: Path,
) -> None:
    delivery = next(
        item for item in deliveries if (item.role, item.operation) == ("curator", "curation")
    )
    report = tmp_path / "curator" / "curation" / "launch.md"
    instructions, record = compose_launch_instructions(
        capsule_delivery_from(delivery.result), str(report), {}
    )
    report.write_text(instructions, encoding="utf-8")
    reference = next(row for row in record["detailReferences"] if row["sourcePath"] == CARD)

    def check_links(text: str) -> None:
        links = card_maintenance_links(text)
        assert len(links) == 1
        parts = urlsplit(links[0])
        assert not parts.scheme and not parts.netloc and not parts.query
        assert Path(unquote(parts.path)) == Path(reference["path"])
        target_file = (report.parent / unquote(parts.path)).resolve()
        assert target_file.is_file(), links[0]
        target_text = target_file.read_text(encoding="utf-8")
        assert "## Choose card and overview scope" in target_text
        assert "## Maintain the entity catalog" in target_text

    actual = report.read_text(encoding="utf-8")
    check_links(actual)
    card_target = card_maintenance_links(actual)[0]
    card_link = f"[cards]({card_target})"
    assert card_link in actual
    for mutant in (
        actual.replace(card_link, "", 1),
        actual.replace(card_link, "[cards](missing-card.md)", 1),
        actual + "\n" + card_link,
        actual.replace(card_link, f"[cards]({card_target}?unexpected=1)", 1),
    ):
        assert mutant != actual
        with pytest.raises(AssertionError):
            check_links(mutant)


def test_failed_memory_tool_preserves_other_admitted_tools_and_dependent_stops(
    deliveries: tuple[Delivered, ...],
    tmp_path: Path,
) -> None:
    tool_failure_clauses = (
        "When one memory tool fails, continue independent work through other admitted memory tools;",
        "existing refusal stops still bind dependent work",
        "no provider, permission, scope or tool boundary changes.",
    )
    require_clauses(source(EXCEPTIONS), tool_failure_clauses, EXCEPTIONS)
    for delivery in deliveries:
        identity = f"{delivery.role}/{delivery.operation}"
        report = tmp_path / delivery.role / delivery.operation / "launch.md"
        instructions, record = compose_launch_instructions(
            capsule_delivery_from(delivery.result),
            str(report),
            {},
        )
        report.write_text(instructions, encoding="utf-8")
        emitted = report.read_text(encoding="utf-8")
        references = {entry["sourcePath"]: entry for entry in record["detailReferences"]}
        if delivery.role == "curator":
            _assert_index_detail_pointer(
                delivery.block("role"), "Exceptions/sync", "exceptions", EXCEPTIONS
            )
            links = re.findall(r"\[exceptions\]\(([^)]+)\)", emitted)
            assert len(links) == 1, (identity, links)
            reached = Path(unquote(urlsplit(links[0]).path))
            assert reached == Path(references[EXCEPTIONS]["path"])
            assert reached.is_file(), reached
            body = reached.read_text(encoding="utf-8")
            require_clauses(body, tool_failure_clauses, (identity, "reached exceptions home"))
            for clause in tool_failure_clauses:
                mutant = body.replace(clause, "", 1)
                assert mutant != body
                with pytest.raises(AssertionError):
                    require_clauses(mutant, tool_failure_clauses, identity)
        else:
            assert EXCEPTIONS not in references, identity
            assert normalized(tool_failure_clauses[0]) not in normalized(delivery.block("role")), (
                identity
            )
            assert normalized(tool_failure_clauses[0]) not in normalized(
                delivery.block("operation")
            ), identity


def test_encountered_memory_healing_has_one_complete_role_home_and_delivery(
    deliveries: tuple[Delivered, ...],
    tmp_path: Path,
) -> None:
    healing_clauses = (
        "Apply bounded encountered-memory repairs",
        "whoever left it",
        "beyond the quality check's findings",
        "without a broad sweep",
        "report every repair",
    )
    findings = []

    def check_detail(identity: str, text: str) -> None:
        bullets = [
            line
            for line in text.splitlines()
            if "encountered-memory repairs" in normalized(line).casefold()
        ]
        if len(bullets) != 1:
            findings.append((identity, "healing-bullet-count", len(bullets), 1))
            return
        for clause in healing_clauses:
            if normalized(clause) not in normalized(bullets[0]):
                findings.append((identity, "missing-healing-clause", clause))

    check_detail(EXCEPTIONS, source(EXCEPTIONS))
    for delivery in deliveries:
        identity = f"{delivery.role}/{delivery.operation}"
        report = tmp_path / delivery.role / delivery.operation / "launch.md"
        instructions, record = compose_launch_instructions(
            capsule_delivery_from(delivery.result),
            str(report),
            {},
        )
        report.write_text(instructions, encoding="utf-8")
        references = {entry["sourcePath"]: entry for entry in record["detailReferences"]}
        if delivery.role == "curator":
            _assert_index_detail_pointer(
                delivery.block("role"), "Exceptions/sync", "exceptions", EXCEPTIONS
            )
            emitted_path = Path(references[EXCEPTIONS]["path"])
            check_detail(f"{identity}:emitted", emitted_path.read_text(encoding="utf-8"))
        else:
            if EXCEPTIONS in references:
                findings.append((identity, "unexpected-exceptions-detail"))
            if "encountered-memory repairs" in normalized(instructions).casefold():
                findings.append((identity, "unexpected-healing-text"))
    assert not findings, findings
