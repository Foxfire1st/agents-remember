"""``KS-R20@v1`` §1, §2, §3 and §6 as behaviour: the views, the classification, the bounding, and
the mounted surface's own refusals.

Every case names the failure it catches. The view vocabulary, the closed rule registry and the
ordering pass are pure functions of their inputs -- which is exactly why the classification rule
can be tested without a store. The §1 literal case is pure too. The §6 cases are not: they drive the
**registered** ``knowledge_*`` handlers through a real ``FastMCP`` server over a converted memory
tree, and their point is precisely that the mounted surface executes rather than merely appearing in
the roster.

The mounted family is three operations over a memory tree (MIK-R26 rule 5): no registered tool
takes a database path, and ``knowledge_change`` and ``knowledge_project`` left the registered set
with the canonical database. The projection writer's cases left with it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.registration.knowledge import register_knowledge_tools
from agents_remember.memory.knowledge_index import INDEX_REPOSITORY_ID, text_uuid
from agents_remember.models.knowledge.classification import (
    AUTHORED_CLASS,
    CLASSIFICATION_CLASSES,
    MECHANICAL_CLASS,
    MECHANICAL_RULE_REGISTRY_VERSION,
    MECHANICAL_RULES,
    ORDERING_INPUTS,
    MechanicalRuleNotRegistered,
    Provenance,
    authored_provenance,
    mechanical_provenance,
    mechanical_rule,
    mechanical_rules_for,
)
from agents_remember.models.knowledge.read import KnowledgeReadSnapshot
from agents_remember.models.knowledge.view import (
    VIEW_NAMES,
    VIEW_PAYLOADS,
    VIEW_PURPOSES,
    CurationQueueRow,
    MachineWorkItem,
    NoConsequenceStatement,
    OrderedPosition,
    SourceContextView,
    SubjectRef,
    ViewCompleteness,
    ViewCounts,
    ViewRefusal,
    ViewScope,
    continuation_for,
    ordering_position,
    require_admitted_ordering_input,
    require_continuation_snapshot,
    view_counts,
)
from knowledge_index_test_support import (
    FAMILY,
    commit_all,
    init_repository,
    write_review_tree,
)
from mcp.server.fastmcp import FastMCP

SNAPSHOT = KnowledgeReadSnapshot(
    repository_id="11111111-1111-1111-1111-111111111111",
    schema_version="ar-knowledge-sqlite/v8",
    logical_digest="a" * 64,
    context_digest="b" * 64,
)


def _completeness(snapshot: KnowledgeReadSnapshot = SNAPSHOT) -> ViewCompleteness:
    return ViewCompleteness(
        complete_within_declared_scope=True,
        scope=ViewScope(
            snapshot_logical_digest=snapshot.logical_digest,
            recorded_graph="recorded",
            traversal_policy="knowledge-view-selection/v1",
        ),
    )


# ---------------------------------------------------------------------------
# The closed provenance class.
# ---------------------------------------------------------------------------


def test_the_class_set_is_closed_at_two_members() -> None:
    """A third class would be the unclassifiable value the packet forbids wearing a name."""

    assert CLASSIFICATION_CLASSES == ("authored", "mechanical")


def test_an_authored_classification_carries_its_author_and_cannot_cite_a_rule() -> None:
    """Requirement 2.3: an authored determination never cites a mechanical rule as its reason."""

    provenance = authored_provenance("agent-7", "rewording only; the obligation is unchanged")
    assert provenance.provenance_class == AUTHORED_CLASS
    assert provenance.authored is not None
    assert provenance.authored.author_ref == "agent-7"
    with pytest.raises(ValueError):
        Provenance(
            provenance_class=AUTHORED_CLASS,
            authored=provenance.authored,
            rule_id="ordering.declared-tiebreak",
            rule_version=1,
        )


def test_a_mechanical_classification_names_its_rule_and_has_no_author() -> None:
    """Requirement 2.3: the mechanical determination never writes an authored record."""

    provenance = mechanical_provenance("ordering.declared-tiebreak", 1)
    assert provenance.provenance_class == MECHANICAL_CLASS
    assert provenance.rule_id == "ordering.declared-tiebreak"
    assert provenance.authored is None
    with pytest.raises(ValueError):
        Provenance(
            provenance_class=MECHANICAL_CLASS,
            authored=authored_provenance("a", "b").authored,
            rule_id="ordering.declared-tiebreak",
            rule_version=1,
        )


def test_a_classification_needs_its_evidence_and_a_rule_the_registry_holds() -> None:
    """Two directions of one rule: a class with no evidence is refused, and so is a foreign rule.

    Requirement 2.2 with its own contrapositive, merged because both measure whether a
    classification can exist without the input it is defined by: an unregistered rule cannot produce
    one, and a class whose evidence is missing cannot be emitted as classified either.
    """

    with pytest.raises(ValueError):
        Provenance(provenance_class=AUTHORED_CLASS)
    with pytest.raises(ValueError):
        Provenance(provenance_class=MECHANICAL_CLASS)

    with pytest.raises(MechanicalRuleNotRegistered):
        mechanical_provenance("ordering.invented-by-a-renderer", 1)
    with pytest.raises(MechanicalRuleNotRegistered):
        mechanical_rule("ordering.declared-tiebreak", 999)


def test_the_registry_admits_one_rule_per_ordering_input() -> None:
    """Expected Evidence: at least one rule per admitted ordering input, and the closure holds."""

    declared = {rule.ordering_input for rule in mechanical_rules_for("ordering")}
    assert declared == set(ORDERING_INPUTS)
    assert MECHANICAL_RULE_REGISTRY_VERSION == 1
    assert all(rule.reads for rule in MECHANICAL_RULES)


def test_no_registered_rule_reads_a_name_a_path_or_a_score() -> None:
    """The four forbidden ordering sources are absent from every registered rule's declared reads."""

    forbidden = ("name", "symbol", "prefix", "extension", "depth", "score", "rank", "weight")
    for rule in MECHANICAL_RULES:
        for read in rule.reads:
            assert not any(token in read for token in forbidden), (rule.rule_id, read)


# ---------------------------------------------------------------------------
# Ordering and the refusals around it.
# ---------------------------------------------------------------------------


def test_an_unadmitted_ordering_input_is_refused_rather_than_defaulted() -> None:
    """Failure state: ``unadmitted_ordering_input``, with no fallback order and no rows."""

    refusal = require_admitted_ordering_input("by_symbol_name_length")
    assert isinstance(refusal, ViewRefusal)
    assert refusal.code == "unadmitted_ordering_input"
    assert refusal.observed == "by_symbol_name_length"
    assert require_admitted_ordering_input("declared_priority") is None


def test_every_admitted_position_names_the_declared_tiebreak_rule() -> None:
    """A position that named no registered tiebreak could not be reproduced across two runs."""

    position = ordering_position(
        1, "declared_priority", authored_provenance("curator-2", "highest")
    )
    assert position.position == 1
    assert position.tiebreak_rule_id == "ordering.declared-tiebreak"
    assert position.provenance.provenance_class == AUTHORED_CLASS


def test_a_position_cannot_name_an_unregistered_rule() -> None:
    """An inline comparator cannot acquire a position by naming a rule the registry does not carry."""

    with pytest.raises(MechanicalRuleNotRegistered):
        OrderedPosition(
            position=1,
            ordering_input="stable_ordering",
            provenance=mechanical_provenance("ordering.declared-tiebreak", 1),
            tiebreak_rule_id="ordering.made-up",
            tiebreak_rule_version=1,
        )


# ---------------------------------------------------------------------------
# Counts, continuation and the bounding honesty rule.
# ---------------------------------------------------------------------------


def test_incompleteness_and_its_continuation_must_agree() -> None:
    """Requirement 3.3 in both directions: rows remaining need a token, and a token needs them.

    One statement about one pair of fields, so one case: a bounded response never presents its first
    page as the whole scope, and a page that says it is complete never carries the token that says it
    is not. Either half alone would let a reader size the scope from the wrong field.
    """

    remaining = view_counts(
        registered_realizations=2, registered_families=1, rows_returned=2, rows_remaining=3
    )
    with pytest.raises(ValueError):
        SourceContextView(
            snapshot=SNAPSHOT,
            counts=remaining,
            completeness=_completeness(),
            renderer_version="knowledge-view-renderer/1",
        )

    complete = view_counts(
        registered_realizations=2, registered_families=1, rows_returned=2, rows_remaining=0
    )
    with pytest.raises(ValueError):
        SourceContextView(
            snapshot=SNAPSHOT,
            counts=complete,
            completeness=_completeness(),
            continuation=continuation_for(view="source_context", snapshot=SNAPSHOT, position=2),
            renderer_version="knowledge-view-renderer/1",
        )


def test_a_continuation_presented_against_another_snapshot_is_refused_with_both_named() -> None:
    """Failure state: the view does not silently re-resolve and the caller receives no page."""

    other = SNAPSHOT.model_copy(update={"logical_digest": "c" * 64})
    continuation = continuation_for(view="review_matrix", snapshot=SNAPSHOT, position=4)
    assert require_continuation_snapshot(continuation, SNAPSHOT) is None
    refusal = require_continuation_snapshot(continuation, other)
    assert refusal is not None
    assert refusal.code == "continuation_binding_mismatch"
    assert refusal.expected == "a" * 64
    assert refusal.observed == "c" * 64


def test_a_quantity_with_no_meaning_for_a_view_says_so_instead_of_reporting_zero() -> None:
    """Every required quantity is present, and an absent measurement is stated, never a zero."""

    counts = view_counts(
        registered_realizations=None, registered_families=None, rows_returned=0, rows_remaining=0
    )
    assert counts.registered_realizations.state == "not_applicable"
    assert counts.registered_realizations.value is None
    assert counts.registered_realizations.reason
    assert counts.rows_returned.value == 0
    with pytest.raises(ValueError):
        ViewCounts.model_validate(
            {
                **counts.model_dump(mode="json"),
                "facets_omitted": {"state": "not_applicable"},
            }
        )


def test_the_curation_queue_keeps_a_work_item_free_of_any_curator_judgement() -> None:
    """Requirement 1.6 is a shape obligation: the two attributions live in two records."""

    item = MachineWorkItem(
        work_item_id="w-1",
        condition_code="member_source_diverged",
        condition_vocabulary_version="detection-condition/v1",
        matched_facts=("claim-4",),
    )
    body = item.model_dump()
    assert "disposition" not in body
    assert "rationale" not in body
    assert "author_ref" not in body
    row = CurationQueueRow(
        item=item,
        order=ordering_position(
            1, "explicit_trigger_rule", mechanical_provenance("ordering.trigger-rule", 1)
        ),
    )
    assert row.disposition is None


def test_a_no_consequence_statement_is_refused_without_the_class_that_produced_it() -> None:
    """Requirement 2.3: a mechanical determination writes no authored record to name."""

    subject = SubjectRef(record_kind="invariant_revision", record_id="INV-014")
    mechanical = NoConsequenceStatement(
        subject=subject,
        detail="whitespace only",
        provenance=mechanical_provenance("consequence.anchor-text-whitespace-only", 1),
    )
    assert mechanical.claim_ref is None
    with pytest.raises(ValueError):
        NoConsequenceStatement(
            subject=subject,
            detail="whitespace only",
            provenance=mechanical_provenance("consequence.anchor-text-whitespace-only", 1),
            claim_ref="NC-7",
        )


def test_an_authored_no_consequence_statement_names_the_stored_claim_it_is() -> None:
    """Requirement 2.3's other half: the authored determination is a stored, attributable record."""

    statement = NoConsequenceStatement(
        subject=SubjectRef(record_kind="invariant_revision", record_id="INV-031"),
        detail="rewording only",
        provenance=authored_provenance("agent-7", "the obligation is unchanged in every branch"),
        claim_ref="NC-7",
    )
    assert statement.claim_ref == "NC-7"
    assert statement.provenance.authored is not None


# ---------------------------------------------------------------------------
# §1.1's closed set and order, as a literal.
# ---------------------------------------------------------------------------


def test_the_five_view_names_are_the_closed_ordered_set() -> None:
    """Requirement 1.1: the five names, spelled the same and in the same order.

    Both view-looping cases in this module -- and the differential beside them -- iterate
    ``VIEW_NAMES``, so every one of them stays green under a rename, an insertion, a reorder or a
    sixth member: they check that each view *behaves*, never that the set is the one the packet
    enumerated. This case is the one assertion that cannot be satisfied by iterating the constant it
    is checking, because the expected tuple is written out here rather than derived. It goes red on
    a sixth view, on a rename, on a reorder, and on a name that is admitted by the ``ViewName``
    literal union but missing from the published ``VIEW_PURPOSES`` map -- and the last of those is
    the one a reader of ``VIEW_NAMES`` alone cannot see.
    """

    assert VIEW_NAMES == (
        "source_context",
        "invariant",
        "family",
        "review_matrix",
        "curation_queue",
    )
    assert tuple(VIEW_PURPOSES) == VIEW_NAMES
    # Each payload class declares the view it is a payload *of*, in this order, so a swapped pair of
    # payload types is red here even though the set of five would be unchanged.
    assert tuple(payload.model_fields["view"].default for payload in VIEW_PAYLOADS) == VIEW_NAMES


# ---------------------------------------------------------------------------
# §6: the registered handlers, over a converted memory tree.
# ---------------------------------------------------------------------------


@pytest.fixture
def anyio_backend() -> str:
    """Run the §6 cases on asyncio, the backend every registered handler is served on."""

    return "asyncio"


def _knowledge_tool_server(root: Path) -> FastMCP:
    """One server carrying **only** the knowledge family, as its own registrar mounts it.

    The registrar reads two registration-time facts: the workspace root a read falls back to, and
    the coordination root a tree's index is cached under. Both are under ``root``.
    """

    class _RegistrationConfig:
        workspace_root = root / "workspace"
        coordination_root = root / "coordination"

    server = FastMCP("knowledge-refusal-probe")
    register_knowledge_tools(server, cast(McpRuntimeConfig, _RegistrationConfig()))
    return server


async def _call(server: FastMCP, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """The JSON body the named registered tool returned for one call.

    A registered handler that lets a model's own ``ValidationError`` escape reaches the transport as
    ``ToolError`` instead of returning, so a case that read only the returned dictionary would be
    checking nothing about such an input. Every call in this module goes through here for that
    reason: "a schema-conformant input is answered" is asserted at the mounted boundary.
    """

    _content, structured = await server.call_tool(tool, arguments)
    return cast(dict[str, Any], structured)


def _memory_tree(tmp_path: Path) -> Path:
    root = tmp_path / "memory"
    init_repository(root)
    write_review_tree(root)
    commit_all(root)
    return root


@pytest.mark.anyio
async def test_the_read_family_refuses_an_ordering_input_it_does_not_admit(
    tmp_path: Path,
) -> None:
    """§2.5 and §6.5: a fifth ordering is refused by name, with no fallback order and no page.

    ``ViewRequest.ordering_input`` is a ``Literal``, so an unadmitted spelling cannot travel through
    a request model -- constructing one raises instead of refusing. The surface therefore asks the
    view module's own closure check before it opens the tree, and this case is what catches a
    regression to the raise: the caller must receive the refusal the requirement promises, naming
    the offending input, rather than a tool error. A handler that fell back to the declared stable
    ordering would serve a page here and be this surface's own semantic choice wearing a caller's
    request.
    """

    body = await _call(
        _knowledge_tool_server(tmp_path),
        "knowledge_read",
        {
            "memoryRoot": str(_memory_tree(tmp_path)),
            "view": "invariant",
            "orderingInput": "symbol_name",
        },
    )

    assert body["state"] == "refused", body
    assert body["refusalCode"] == "unadmitted_ordering_input", body
    assert body["view"] == "invariant", body
    assert "none of the four admitted ordering inputs" in body["refusalDetail"], body


@pytest.mark.anyio
async def test_the_read_family_refuses_a_sixth_view_before_it_touches_a_tree(
    tmp_path: Path,
) -> None:
    """§1.1 and §6.1: the closed set of five, and a memory root that cannot be read at all.

    The memory root in the first call does not exist, which is the second half of the assertion: a
    sixth view is refused from the name alone, so a caller cannot reach a tree -- or a filesystem
    error -- through a view this surface does not admit. A reader that opened first and checked the
    name later would refuse the missing root instead, which is the mutation the first call catches.
    The second call names an admitted view and the same absent root: it is answered as an absent
    selection inside the envelope, never as a transport error.
    """

    server = _knowledge_tool_server(tmp_path)
    absent = str(tmp_path / "no-such-memory-root")

    sixth = await _call(server, "knowledge_read", {"memoryRoot": absent, "view": "timeline"})
    assert (sixth["state"], sixth["refusalCode"]) == ("refused", "unknown_view"), sixth
    assert "five named query views" in sixth["refusalDetail"], sixth

    missing = await _call(server, "knowledge_read", {"memoryRoot": absent, "view": "family"})
    assert (missing["state"], missing["refusalCode"]) == (
        "refused",
        "selected_input_unavailable",
    ), missing
    assert absent in missing["refusalDetail"], missing
    assert not (tmp_path / "coordination").exists()  # nothing was indexed for a refused call


@pytest.mark.anyio
async def test_the_registered_handlers_read_a_memory_tree_and_take_no_database_path(
    tmp_path: Path,
) -> None:
    """MIK-R26 rule 5: each registered tool executes over a memory tree, and none of the published
    input schemas carries a database path or a caller-supplied namespace.

    Catches a tool that still advertises ``databasePath`` or ``repositoryId``, a handler whose
    wire names and builder disagree (it would raise here, not return), and a response that lost
    the namespace the read's contract states.
    """

    server = _knowledge_tool_server(tmp_path)
    root = _memory_tree(tmp_path)
    tools = await server.list_tools()
    # A read that writes says so where its caller reads it: without `afterRevision` the diff
    # captures the working tree, which writes loose objects into the memory repository.
    (diff_description,) = (
        tool.description or "" for tool in tools if tool.name == "knowledge_diff"
    )
    assert "no `afterRevision`" in diff_description
    assert "writes loose, unreferenced objects" in " ".join(diff_description.split())
    schemas = {tool.name: set(tool.inputSchema["properties"]) for tool in tools}
    assert schemas == {
        "knowledge_read": {
            "memoryRoot",
            "view",
            "orderingInput",
            "limit",
            "continuation",
            "invariantRevisionId",
            "familyRevisionId",
            "sourcePath",
            "repositoryRoot",
            "codeTreeId",
        },
        "knowledge_diff": {"memoryRoot", "beforeRevision", "afterRevision", "recordId", "path"},
        "knowledge_integrity_check": {"memoryRoot", "codeRoot", "baseCommits", "contractPath"},
    }

    family = await _call(
        server,
        "knowledge_read",
        {
            "memoryRoot": str(root),
            "view": "family",
            "familyRevisionId": text_uuid("revision", f"{FAMILY}@1"),
        },
    )
    assert family["state"] == "view", family
    assert family["repositoryId"] == INDEX_REPOSITORY_ID
    assert family["memoryTree"]["memoryRoot"] == str(root)

    # A converted scratch tree with a second commit: the registered handler answers the Git diff.
    (root / "knowledge" / "notes-for-the-diff.json").write_text("{}\n", encoding="utf-8")
    commit_all(root, "a second state")
    compared = await _call(
        server,
        "knowledge_diff",
        {"memoryRoot": str(root), "beforeRevision": "HEAD~1", "afterRevision": "HEAD"},
    )
    assert compared["state"] == "compared", compared
    assert [change["path"] for change in compared["diff"]["other"]] == [
        "knowledge/notes-for-the-diff.json"
    ]
    assert compared["diff"]["changed_files"] == 1
    assert "semanticEffectLabels" not in compared
    empty = await _call(
        server, "knowledge_diff", {"memoryRoot": str(root), "beforeRevision": "HEAD"}
    )
    assert empty["state"] == "compared" and empty["diff"]["changed_files"] == 0

    code = tmp_path / "code"
    code.mkdir()
    checked = await _call(
        server,
        "knowledge_integrity_check",
        {"memoryRoot": str(root), "codeRoot": str(code), "baseCommits": ["HEAD"]},
    )
    assert checked["state"] == "reported", checked
    assert checked["validation"]["candidate"] and checked["bases"] == ["HEAD"]


@pytest.mark.anyio
async def test_the_mounted_families_are_exactly_the_three_these_cases_drive(
    tmp_path: Path,
) -> None:
    """§6.1: the family module mounts three, this module drives three, and the two sets are equal.

    The roster case in ``test_tools.py`` proves the names are advertised and that existing names
    keep their positions. It cannot prove that any of them executes. This case closes the loop from
    the other end. It reads *this module's own source* for a call naming each mounted tool, so a
    family added to the surface -- or one of these three renamed out from under the cases that
    drive it -- reddens here instead of shipping with a roster row and no caller.
    """

    server = _knowledge_tool_server(tmp_path)
    mounted = tuple(tool.name for tool in await server.list_tools())

    assert mounted == ("knowledge_read", "knowledge_diff", "knowledge_integrity_check")
    source = Path(__file__).read_text(encoding="utf-8")
    undriven = [name for name in mounted if f'"{name}"' not in source]
    assert not undriven, (
        f"the mounted families {undriven} have no case in this module that calls them by name; a "
        "roster row is not coverage"
    )
