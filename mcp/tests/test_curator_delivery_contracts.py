"""L84's scheduled R62 rules31-37/A1/BA6 subset: real source and delivery proof.

V8 removes mutation mandates, not source-local duties, actual audiences or step reach.
R07v2 rules3/5 govern the separate invariant/family disposition definitions.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
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
    FACTCHECK_OPERATION,
    FULL_CURATION_ACTION,
    FULL_CURATION_CLAUSES,
    HEALING_UNION_CLAUSES,
    KNOWLEDGE,
    OPERATION,
    PUBLICATION,
    PUBLICATION_SEMANTICS,
    ROLE,
    ROLE_CONTINUATION,
    ROLE_CURRENT_TRUTH,
    ROLE_HEALING,
    SEMANTIC_TRUTH,
    SERVING_AUTHORITY,
    SHARED_CONTINUATION,
    WRITER,
    WRITER_IDENTITY,
    WRITER_OPERATION,
    Delivered,
    SectionLink,
    compile_matrix,
    expected_bound_detail,
    healing_findings,
    linked_section,
    normalized,
    require_clauses,
    writer_findings,
)
from agents_remember_test_support.testing.leaf_instruction_wording import (
    assert_action_pointer,
    assert_emitted_detail_links,
)

ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = ROOT / "skills/l-01-agent-lifecycles"
DETAIL_ACTIONS = {
    AUTHORING: ("Before editing a card or its paired notes, read", {"curator"}),
    CARD: ("Before creating or maintaining cards or overviews, read", {"curator"}),
    WRITER: ("Before a command-line run that decides or writes, read", {"worker", "curator"}),
    PUBLICATION: (
        "Before interpreting an ingest result or claiming publication, read",
        {"curator"},
    ),
    AUTHORED_HISTORY: (
        "Before updating current overview prose or answering changed-file history, read",
        {"curator"},
    ),
    CHANGE_ROUTING: ("Before routing a changed file or notes item, read", {"curator"}),
}


@pytest.fixture(scope="module")
def deliveries() -> tuple[Delivered, ...]:
    return compile_matrix(LIFECYCLE)


def source(path: str) -> str:
    return (LIFECYCLE / path).read_text(encoding="utf-8")


def test_curator_role_itself_retains_complete_truth_continuation_stops_and_exceptions(
    deliveries: tuple[Delivered, ...],
) -> None:
    clauses = (*ROLE_CURRENT_TRUTH, *ROLE_CONTINUATION, *ROLE_HEALING)
    require_clauses(source(ROLE), clauses, ROLE)

    def require_full_checks(text: str) -> None:
        checks = linked_section(
            text,
            LIFECYCLE / ROLE,
            SectionLink(
                LIFECYCLE / OPERATION,
                "Run the complete curation checks",
                FULL_CURATION_ACTION,
                "### Run the complete curation checks",
            ),
        )
        require_clauses(checks, FULL_CURATION_CLAUSES, "linked full-operation home")

    require_full_checks(source(ROLE))
    checks = linked_section(
        source(ROLE),
        LIFECYCLE / ROLE,
        SectionLink(
            LIFECYCLE / OPERATION,
            "Run the complete curation checks",
            FULL_CURATION_ACTION,
            "### Run the complete curation checks",
        ),
    )
    for clause in FULL_CURATION_CLAUSES:
        reading = normalized(checks)
        mutant = reading.replace(normalized(clause), "", 1)
        assert mutant != reading
        with pytest.raises(AssertionError):
            require_clauses(mutant, FULL_CURATION_CLAUSES, "deleted check obligation")
    for mutant in (
        source(ROLE).replace("[Run the complete curation checks]", "[missing checks]", 1),
        source(ROLE).replace("#run-the-complete-curation-checks", "#wrong-section", 1),
        source(ROLE).replace(
            "../operations/curation.md#run-the-complete-curation-checks",
            "../operations/review.md#run-the-complete-curation-checks",
            1,
        ),
    ):
        assert mutant != source(ROLE)
        with pytest.raises(AssertionError):
            require_full_checks(mutant)

    def require_handover_checks(text: str, where: object) -> None:
        handover = text.split("## Produce the handover", 1)[1].split(
            "## Use direct handovers within the leaf", 1
        )[0]
        require_clauses(
            handover,
            ("Exact full-operation commands and results, every failed, blocked or not-run check",),
            (where, "Produce the handover"),
        )

    require_handover_checks(source(ROLE), ROLE)
    require_clauses(source("core/every-role.md"), SHARED_CONTINUATION, "core/every-role.md")
    for delivery in deliveries:
        role_block = delivery.block("role")
        if delivery.role == "curator":
            require_clauses(role_block, clauses, (delivery.role, delivery.operation, ROLE))
            require_full_checks(role_block)
            require_handover_checks(role_block, (delivery.role, delivery.operation, ROLE))
            assert delivery.shared_block is not None
            assert delivery.shared_block == source("core/every-role.md")
            require_clauses(
                delivery.shared_block,
                SHARED_CONTINUATION,
                (delivery.role, delivery.operation, "admitted shared continuation"),
            )
            emitted_shared = render_shared_block(delivery.shared_block)
            assert emitted_shared.count("## What every role acts on") == 1
            for clause in SHARED_CONTINUATION:
                assert normalized(emitted_shared).count(normalized(clause)) == 1
            assert not any(
                block.source_path == "core/every-role.md"
                for block in delivery.result.capsule.instructions
            )
        else:
            assert ROLE not in {
                block.source_path for block in delivery.result.capsule.instructions
            }, (delivery.role, delivery.operation)
    # Operation repetition cannot substitute for a missing role-local stop above.
    require_clauses(
        source("operations/orientation.md"), SERVING_AUTHORITY, "operations/orientation.md"
    )
    require_clauses(source(OPERATION), WRITER_OPERATION, OPERATION)
    for delivery in deliveries:
        if (delivery.role, delivery.operation) == ("curator", "curation"):
            require_clauses(delivery.block("operation"), WRITER_OPERATION, OPERATION)


def test_step_details_reach_actual_files_at_actions_over_the_complete_matrix(
    deliveries: tuple[Delivered, ...],
    tmp_path: Path,
) -> None:
    for delivery in deliveries:
        details = {item.source_path: item for item in delivery.result.capsule.detail_references}
        rendered = delivery.result.render_instructions()
        for name, (action, carriers) in DETAIL_ACTIONS.items():
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
                assert_action_pointer(rendered, name, action)
                assert details[name].content == (LIFECYCLE / name).read_bytes()
        if delivery.role == "curator":
            report = tmp_path / delivery.role / delivery.operation / "request.md"
            instructions, record = compose_launch_instructions(
                capsule_delivery_from(delivery.result),
                str(report),
                {},
            )
            references = {entry["sourcePath"]: entry for entry in record["detailReferences"]}
            for name in DETAIL_ACTIONS:
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
                checks = linked_section(
                    instructions,
                    report,
                    SectionLink(
                        origin / OPERATION,
                        "Run the complete curation checks",
                        FULL_CURATION_ACTION,
                        "### Run the complete curation checks",
                    ),
                )
                require_clauses(checks, FULL_CURATION_CLAUSES, "actual emitted check owner")

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
            assert_action_pointer(
                delivery.result.render_instructions(), AUTHORING, DETAIL_ACTIONS[AUTHORING][0]
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
            assert_action_pointer(
                delivery.result.render_instructions(), CARD, DETAIL_ACTIONS[CARD][0]
            )
            require_clauses(
                delivery.block("role"),
                ("Before creating or maintaining cards or overviews, read",),
                (delivery.operation, "card-detail action timing"),
            )
            assert "{{detail:" + CARD + "}}" in delivery.block("role")


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
    require_clauses(source(ROLE), ROLE_HEALING, ROLE)
    require_clauses(
        source(OPERATION),
        ("Apply the bounded encountered-memory repair", "continue without a broad sweep"),
        OPERATION,
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


CARD_SCOPE_FRAGMENT = "choose-card-and-overview-scope"


def card_maintenance_links(text: str) -> tuple[str, ...]:
    return tuple(
        re.findall(
            r"\[(?:card formats and citation checks|card and overview scope)\]\(([^)]+)\)", text
        )
    )


def entity_catalog_links(text: str) -> tuple[str, ...]:
    return tuple(
        re.findall(
            r"\[(?:Maintain the entity catalog|entity catalog maintenance)\]\(([^)]+)\)", text
        )
    )


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
    # P5-18: the Curator's one card-scope and one entity-catalog pointer stand in its role
    # file, for every Curator operation; the curation operation repeats neither.
    source_links = card_maintenance_links(source(CURATOR_ROLE))
    source_catalog_links = entity_catalog_links(source(CURATOR_ROLE))
    assert len(source_links) == len(source_catalog_links) == 1
    assert card_maintenance_links(source(OPERATION)) == ()
    assert entity_catalog_links(source(OPERATION)) == ()
    resolved = [_source_target(CURATOR_ROLE, t) for t in (*source_links, *source_catalog_links)]
    assert {path.name for path, _fragment in resolved} == {Path(CARD).name}
    assert [fragment for _path, fragment in resolved] == [
        CARD_SCOPE_FRAGMENT,
        "maintain-the-entity-catalog",
    ]
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
        assert entity_catalog_links(operation_block.content) == ()
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
        catalogs = entity_catalog_links(role_block.content) if role_block else ()
        assert len(links) == len(catalogs) == (1 if delivery.role == "curator" else 0), (
            delivery.role,
            delivery.operation,
        )
        for target in (*links, *catalogs):
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
                    "Create, refresh, move or remove cards within their scope",
                    "Maintain the entity catalog, and edit an entity only for a real entity change.",
                    "Restore memory only under the case's recorded authority and order.",
                    "If the capsule exposes no writer and admits none, report the missing capability and the required records not written.",
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
    assert parts.fragment == "normal-workflow"
    assert (card_origin.parent / unquote(parts.path)).resolve() == operation_path.resolve()
    assert operation_path.is_file()
    assert "## Normal workflow\n" in operation_text
    require_clauses(card_text, CARD_FACTCHECK, card_origin)
    require_clauses(operation_text, FACTCHECK_OPERATION, operation_path)
    links = re.findall(
        r"\[Independent fact-check of curated knowledge\]\(([^)]+)\)", operation_text
    )
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
        (operation_text, FACTCHECK_OPERATION, OPERATION),
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
                "../operations/curation.md#normal-workflow",
                "../operations/review.md#normal-workflow",
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
            require_clauses(
                delivery.block("role"),
                (
                    "Before sending a memory candidate for review or returning a memory repair, read",
                    "Before closeout, follow the complete independent fact-check in [Obtain the independent fact-check](../operations/curation.md#obtain-the-independent-fact-check) for its checking seat, blocking-finding repairs and reruns.",
                    "Evidence and working files belong in the task record or scratch root, never the memory worktree.",
                    "The result is review evidence, not a new closeout gate or an intent verdict.",
                ),
                ROLE,
            )
            assert "The curation operation owns the workflow sequence." in delivery.block("role")
            assert "{{detail:reference/curator/memory-handover.md}}" in delivery.block("role")
            assert_action_pointer(
                delivery.result.render_instructions(),
                "reference/curator/memory-handover.md",
                "Before sending a memory candidate for review or returning a memory repair, read",
            )
            instructions, record = compose_launch_instructions(
                capsule_delivery_from(delivery.result),
                str(tmp_path / delivery.role / delivery.operation / "factcheck.md"),
                {},
            )
            # The Curator role no longer links the curation operation (P5-14/15); the
            # emitted curation launch itself carries the fact-check, and any emitted link
            # to the operation must resolve to it.
            targets = re.findall(r"\]\(([^)]*operations/curation\.md[^)]*)\)", instructions)
            # P5-23 b3: the check must run; every Curator launch links the curation operation
            # from its role file (fact-check, checks and authority gates).
            assert targets, (delivery.role, delivery.operation)
            for target in targets:
                linked = Path(unquote(urlsplit(target).path))
                assert linked.is_file(), target
                require_clauses(linked.read_text(), FACTCHECK_OPERATION, target)
            if delivery.operation == "curation":
                require_clauses(instructions, FACTCHECK_OPERATION, (delivery.role, OPERATION))
            card_record = next(
                item for item in record["detailReferences"] if item["sourcePath"] == CARD
            )
            emitted_card = Path(card_record["path"])
            assert emitted_card.is_file()
            with shipped_composition_corpus() as (origin, _):
                assert (origin / OPERATION).read_bytes() == (LIFECYCLE / OPERATION).read_bytes()
                assert (origin / CRITERIA).read_bytes() == (LIFECYCLE / CRITERIA).read_bytes()
                _require_factcheck_chain(
                    (emitted_card.read_text(), emitted_card),
                    origin / OPERATION,
                    origin / CRITERIA,
                    (origin / OPERATION).read_text(),
                    (origin / CRITERIA).read_text(),
                )
        if (delivery.role, delivery.operation) == ("curator", "curation"):
            require_clauses(delivery.block("operation"), FACTCHECK_OPERATION, OPERATION)
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
        catalogs = entity_catalog_links(text)
        assert len(links) == 1  # P5-18: one card-scope pointer per first message
        assert len(catalogs) == 1
        assert {urlsplit(link).fragment for link in links} == {CARD_SCOPE_FRAGMENT}
        assert urlsplit(catalogs[0]).fragment == "maintain-the-entity-catalog"
        for target in (*links, *catalogs):
            parts = urlsplit(target)
            assert not parts.scheme and not parts.netloc and not parts.query
            assert Path(unquote(parts.path)) == Path(reference["path"])
            assert (report.parent / unquote(parts.path)).resolve().is_file(), target

    actual = report.read_text(encoding="utf-8")
    check_links(actual)
    card_target = card_maintenance_links(actual)[0]
    catalog_target = entity_catalog_links(actual)[0]
    card_link = f"[card and overview scope]({card_target})"
    assert card_link in actual
    catalog_link = f"[entity catalog maintenance]({catalog_target})"
    for mutant in (
        actual.replace(card_link, "", 1),
        actual.replace(card_link, "[card formats and citation checks](missing-card.md)", 1),
        actual + "\n" + card_link,
        actual.replace(catalog_link, "", 1),
        actual.replace(
            catalog_link, catalog_link.replace("#maintain-the-entity-catalog", "#wrong-section"), 1
        ),
    ):
        assert mutant != actual
        with pytest.raises(AssertionError):
            check_links(mutant)


def test_failed_memory_tool_preserves_other_admitted_tools_and_dependent_stops(
    deliveries: tuple[Delivered, ...],
    tmp_path: Path,
) -> None:
    role_clauses = (
        "When one memory tool fails, continue independent work through other admitted memory tools.",
        "Before continuing after a memory-tool failure, follow [Repair and republish]"
        "(../operations/curation.md#repair-and-republish) for the refusal and tool-boundary rules.",
    )
    operation_clauses = (
        "If one memory tool fails, continue independent work through the other admitted memory tools.",
        "Existing refusal stops still bind dependent work.",
        "Change no provider, permission, scope or tool boundary.",
    )
    carriers = [
        (ROLE, source(ROLE), role_clauses),
        (OPERATION, source(OPERATION), operation_clauses),
    ]
    for delivery in deliveries:
        identity = f"{delivery.role}/{delivery.operation}"
        if delivery.role == "curator":
            carriers.append((f"{identity}:role", delivery.block("role"), role_clauses))
        if (delivery.role, delivery.operation) == ("curator", "curation"):
            carriers.append(
                (f"{identity}:operation", delivery.block("operation"), operation_clauses)
            )
        report = tmp_path / delivery.role / delivery.operation / "launch.md"
        instructions, _ = compose_launch_instructions(
            capsule_delivery_from(delivery.result),
            str(report),
            {},
        )
        report.write_text(instructions, encoding="utf-8")
        emitted = normalized(report.read_text(encoding="utf-8"))
        if delivery.role == "curator":
            links = re.findall(r"\[Repair and republish\]\(([^)]+)\)", emitted)
            assert len(links) == 1, (identity, links)
            target, fragment = links[0].rsplit("#", 1)
            assert fragment == "repair-and-republish"
            reached = Path(target)
            with shipped_composition_corpus() as (origin, _):
                assert reached.resolve() == (origin / OPERATION).resolve()
                assert reached.read_bytes() == (LIFECYCLE / OPERATION).read_bytes()
            repair = (
                reached.read_text(encoding="utf-8")
                .split("### Repair and republish", 1)[1]
                .split("## Authority gates", 1)[0]
            )
            require_clauses(repair, operation_clauses, (identity, "reached repair home"))
            for clause in operation_clauses:
                mutant = repair.replace(clause, "", 1)
                assert mutant != repair
                with pytest.raises(AssertionError):
                    require_clauses(mutant, operation_clauses, identity)
        for clauses, expected in (
            (role_clauses, int(delivery.role == "curator")),
            (
                operation_clauses,
                int((delivery.role, delivery.operation) == ("curator", "curation")),
            ),
        ):
            observed = emitted.count(normalized(clauses[0]))
            assert observed == expected, (identity, clauses[0], observed, expected)
            if expected == 0:
                assert normalized(clauses[0]) not in normalized(delivery.block("role")), identity
                assert normalized(clauses[0]) not in normalized(delivery.block("operation")), (
                    identity
                )
    missing = [
        (carrier, clause)
        for carrier, text, clauses in carriers
        for clause in clauses
        if normalized(clause) not in normalized(text)
    ]
    assert not missing, missing


def test_encountered_memory_healing_has_one_complete_role_home_and_delivery(
    deliveries: tuple[Delivered, ...],
    tmp_path: Path,
) -> None:
    findings = []

    def check_carrier(identity: str, text: str, expected: int) -> None:
        paragraphs = [
            paragraph
            for paragraph in re.split(r"\n\s*\n", text)
            if "repair wrong or missing memory" in normalized(paragraph).casefold()
        ]
        if len(paragraphs) != expected:
            findings.append((identity, "healing-paragraph-count", len(paragraphs), expected))
        if expected:
            for paragraph in paragraphs:
                for clause in HEALING_UNION_CLAUSES:
                    if normalized(clause) not in normalized(paragraph):
                        findings.append((identity, "missing-union-clause", clause))

    role_source = source(ROLE)
    stage = role_source.split("## Repair and finish the stage", 1)[1].split(
        "## Report findings to their owners", 1
    )[0]
    check_carrier(ROLE, role_source, 1)
    check_carrier(f"{ROLE}:Repair and finish the stage", stage, 1)
    for delivery in deliveries:
        identity = f"{delivery.role}/{delivery.operation}"
        expected = int(delivery.role == "curator")
        check_carrier(f"{identity}:role", delivery.block("role"), expected)
        check_carrier(
            f"{identity}:all-compiled-blocks",
            "\n\n".join(block.content for block in delivery.result.capsule.instructions),
            expected,
        )
        report = tmp_path / delivery.role / delivery.operation / "launch.md"
        instructions, _ = compose_launch_instructions(
            capsule_delivery_from(delivery.result),
            str(report),
            {},
        )
        report.write_text(instructions, encoding="utf-8")
        check_carrier(f"{identity}:emitted", report.read_text(encoding="utf-8"), expected)
    assert not findings, findings
