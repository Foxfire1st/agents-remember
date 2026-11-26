"""Operational policy and contract checks for the consolidated role-instruction corpus.

Split from ``test_role_instruction_corpus.py``. Covers the curator hand-off list contract,
JSON-primary master and leaf dependency rules, packet-alignment and advisory-review ownership,
knowledge-gate and worklist landing explanations, public command documentation ownership,
retired lens/phase exclusions, and role task-publication moments.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.tasks import write_task_doc
from agents_remember_test_support.testing import task_document_instruction_contracts
from agents_remember_test_support.testing.curation_doctrine import GENERATED_SKILL_COPIES
from agents_remember_test_support.testing.instruction_consistency import (
    assert_public_command_reference,
    command_reference,
    misplace_command_documentation,
    public_commands,
)
from test_role_instruction_corpus import (
    LIFECYCLE_ROOT,
    REPOSITORY_ROOT,
    ROLE_ORDER,
    SKILLS_ROOT,
    _manifest,
    unresolved_references,
)
from test_task_document import _config, _doc


def test_the_curator_hand_off_list_contract_has_one_home_its_consequences_declared() -> None:
    """The shape the producers emit for the curator exists exactly once, and its seats name it.

    The rule this protects is the hand-off contract, not a file name. A requirement-shaped item the
    builder or the reviewer produces must reach the curator as data in one agreed shape; when the
    contract has no home, or a seat that emits or consumes the list does not name it, each leaf
    re-invents the shape and the curator ingests a different document every time. The consequences
    asserted here are the four that make it a contract rather than formatting -- the field ownership
    split, the JSON shape, the co-resolution rule and the no-paraphrase rule -- plus the registry
    wiring, because an unregistered contract is one no seat is routed to.
    """

    relative = "templates/curator-handoff-list.md"
    contract = LIFECYCLE_ROOT / relative
    assert contract.is_file(), f"the curator hand-off list contract is missing: {relative}"

    text = contract.read_text(encoding="utf-8")
    for section in (
        "## The entry",
        "## Shape",
        "## Rule 1 — co-resolution",
        "## Rule 2 — carry producer data verbatim",
    ):
        assert section in text, f"{relative} no longer carries '{section}'"
    # F12: every in-scope copy of the hand-off contract requires the current onboarding row
    # and narrows counted sidecar edits to references.
    ingestion_relative = "templates/curator-handoff-ingestion.md"
    ingestion = LIFECYCLE_ROOT / ingestion_relative
    assert ingestion.is_file(), f"the curator ingestion detail is missing: {ingestion_relative}"
    copies = [ingestion] + [
        REPOSITORY_ROOT / root / "l-01-agent-lifecycles" / ingestion_relative
        for root in GENERATED_SKILL_COPIES
    ]
    for copy_path in copies:
        copy_text = " ".join(copy_path.read_text(encoding="utf-8").split())
        assert "its current `onboarding:<path>` row" in copy_text, copy_path
        assert "a counted change of its card alone never answers" in copy_text, copy_path
        assert "its sidecar's `references` beyond mechanical anchor fields" in copy_text, copy_path
    # Card/overview row schema and current-source binding live in the hand-off contract;
    # card-authoring guidance points at the same current dispositions.
    card_detail = "reference/curator/card-formats-and-citations.md"
    for surface in ("skills", *GENERATED_SKILL_COPIES):
        lifecycle = REPOSITORY_ROOT / surface / "l-01-agent-lifecycles"
        card_text = " ".join((lifecycle / card_detail).read_text(encoding="utf-8").split())
        assert "subject `onboarding:<path>` or `onboarding:<route>/overview`" in card_text
        assert (
            "Use `updated` with a counted card change, or `no_impact` with a reason." in card_text
        )
        handoff_text = " ".join(
            (lifecycle / ingestion_relative).read_text(encoding="utf-8").split()
        )
        for clause in (
            "`onboarding:<source path>` or `onboarding:<route>/overview`",
            "Its disposition is `updated` when the card or overview was brought up to date",
            "the writer refuses an `updated` row without one",
            "or `no_impact` when the reviewed file needs no change",
            "with each file's blob at the code candidate (`sources`)",
            "When a covered file changes again after the row was written, the item reopens: name the row again.",
        ):
            assert clause in handoff_text, (surface, clause)
            with pytest.raises(AssertionError, match=re.escape(clause)):
                assert clause in handoff_text.replace(clause, ""), clause
    assert "target: []" in text, f"{relative} no longer encodes a ruling that applies nowhere"
    assert unresolved_references(LIFECYCLE_ROOT) == [], "the contract cites a path that is gone"

    # Every seat on either side of the interface declares the contract it hands over or ingests.
    roles = _manifest()["roles"]
    for role in ("worker", "reviewer", "orchestrator", "curator"):
        assert "curator-handoff-list.md" in roles[role]["templates"], (
            f"role '{role}' emits or consumes the curator hand-off list but does not declare it"
        )
        role_text = (LIFECYCLE_ROOT / roles[role]["file"]).read_text(encoding="utf-8")
        # Q-P5-b split: producers name the list contract; the Curator names the ingestion
        # sections it consumes (and declares that template too).
        contract = (
            "templates/curator-handoff-ingestion.md"
            if role == "curator"
            else "templates/curator-handoff-list.md"
        )
        assert contract in role_text, (
            f"{roles[role]['file']} does not name the hand-off list contract it depends on"
        )
    assert "curator-handoff-ingestion.md" in roles["curator"]["templates"]


def test_new_series_guidance_requires_json_primary_master_before_dependencies() -> None:
    task_document_instruction_contracts.assert_contract(REPOSITORY_ROOT, GENERATED_SKILL_COPIES)


def test_native_new_master_then_indexed_leaf_dependencies(tmp_path: Path) -> None:
    config = _config(tmp_path)
    root = tmp_path / "tasks" / "agents-remember" / "legacy"

    def call(operation, target_slug=None, **fields):
        target = TaskDocTarget(repo_id="agents-remember", task_name=root.name, slug=target_slug)
        return task_doc_tool(config, target, operation=operation, edit=TaskDocEdit(fields=fields))

    base = dict(repo="agents-remember", createdAt="2026-01-01T00:00")
    write_task_doc(
        root, _doc(**base, id="A", slug="01_a", title="A", kind="subTask", master="task.md")
    )
    (root / "task.md").write_text("# Task: Legacy Markdown master\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in root.iterdir() if path.is_file()}
    with pytest.raises(TaskDocError, match="master"):
        call("set_field", "01_a", waitsFor=["B"])
    assert {path: path.read_bytes() for path in before} == before
    root = root.with_name("series")
    call("create", **base, id="series", slug="series", title="Series", kind="master")
    call("create", **base, id="A", slug="01_a", title="A", kind="subTask", master="task.md")
    call("create", **base, id="B", slug="02_b", title="B", kind="subTask", master="task.md")
    rows = json.loads((root / "task.json").read_bytes())["subTasks"]
    assert [row["number"] for row in rows] == ["A", "B"]
    assert call("set_field", "01_a", waitsFor=["B"])["ok"]
    assert json.loads((root / "01_a.json").read_bytes())["waitsFor"] == ["B"]
    assert "## Order" in (root / "task.md").read_text(encoding="utf-8")


def _require_clauses(text: str, clauses: tuple[str, ...]) -> None:
    for clause in clauses:
        assert clause in text, clause


def _architect_passages(text: str) -> str:
    return " ".join(
        " ".join(
            re.findall(r"(?s)<!-- ar:roles architect -->\s*(.*?)(?=<!-- ar:roles |\Z)", text)
        ).split()
    )


def _require_originating_leaf_home(text: str) -> None:
    """Keep the complete all-role originating-leaf rule in its common planning owner."""
    common = "\n".join(re.findall(r"(?s)<!-- ar:roles all -->\s*(.*?)(?=<!-- ar:roles |\Z)", text))
    section = re.search(
        r"(?ms)^## Complete the plan and preserve its boundaries\n(.*?)(?=^## |\Z)", common
    )
    assert section is not None, "originating-leaf rule needs its common owner"
    assert (
        "keep issues in originating leaf unless another open leaf's charter covers them."
        in " ".join(section[1].split())
    )


def _assert_originating_leaf_home_controls(text: str) -> None:
    _require_originating_leaf_home(text)
    for old, new in (
        ("keep issues in originating leaf", ""),
        ("unless another open leaf's charter covers them.", ""),
        ("## Complete the plan and preserve its boundaries", "## Unrelated action"),
        ("<!-- ar:roles all -->", "<!-- ar:roles curator -->"),
    ):
        mutant = text.replace(old, new)
        assert mutant != text, old
        with pytest.raises(AssertionError):
            _require_originating_leaf_home(mutant)


def _assert_advisory_and_formal_permission_controls() -> None:
    advisory_detail_clauses = (
        "## Advisory independent reviews",
        "exception to normal routing",
        "the Architect commissions it and carries the findings to the Reviewer.",
        "It holds no leaf seat",
        "Provide the freeze, requirement packets, rulings, design source and Worker evidence supplied to the Reviewer.",
        "own output folder",
        "clean copy for anything it runs",
        "separate ports.",
        "No advisory reviewer reads the Reviewer's or Curator's files",
        "another advisory reviewer's folders",
        "Each reports only to the Architect.",
        "Name nothing the Architect or developer has already noticed",
        "An advisory report changes no verdict by itself",
        "one questions file",
        "tell the Manager once",
        "rule on each",
        "An extra round for advisory findings is the Architect's grant.",
        "The Architect's pre-landing check confirms that every advisory finding has a ruling.",
        "sufficient seat permissions",
        "Before responding to an advisory reviewer's permission prompt, read "
        "[Formal permissions](../../operations/orientation.md#formal-permissions) for the once-only "
        "developer notice and existing parent route.",
    )
    advisory_detail = " ".join(
        (LIFECYCLE_ROOT / "reference/review/advisory-reviews.md").read_text().split()
    )
    _require_clauses(advisory_detail, advisory_detail_clauses)
    for clause in advisory_detail_clauses:
        mutant = advisory_detail.replace(clause, "")
        with pytest.raises(AssertionError, match=re.escape(clause)):
            _require_clauses(mutant, advisory_detail_clauses)

    formal_clauses = (
        "Answer formality-only prompts within seat permissions; no blanket approver.",
        "On default/auto-review or refusal, name the prompt once to developer.",
    )
    permission_route_clauses = (
        "Before answering a formal permission prompt, the Architect reads "
        "[formal permissions](../../../operations/orientation.md#formal-permissions) "
        "for its seat-limited authority and the developer decision route.",
        "A child sends to its parent, and the parent relays notices through the existing route. "
        "Do not invent a direct developer route for a parented seat.",
        "The parent route applies only when a parent exists. "
        "Do not direct a parentless Architect to route a question through itself.",
    )

    def require_formal_permission_home(text: str) -> None:
        blocks = re.findall(r"(?s)<!-- ar:roles architect -->\s*(.*?)(?=<!-- ar:roles |\Z)", text)
        sections = [
            match[1]
            for block in blocks
            if (match := re.search(r"(?ms)^## Formal permissions\n(.*?)(?=^## |\Z)", block))
        ]
        assert len(sections) == 1, "Formal permissions needs its Architect-owned home"
        _require_clauses(" ".join(sections[0].split()), formal_clauses)

    orientation = (LIFECYCLE_ROOT / "operations/orientation.md").read_text()
    require_formal_permission_home(orientation)
    for clause in formal_clauses:
        mutant = orientation.replace(clause, "", 1)
        assert mutant != orientation, clause
        with pytest.raises(AssertionError):
            require_formal_permission_home(mutant)
    with pytest.raises(AssertionError):
        require_formal_permission_home(
            orientation.replace("<!-- ar:roles architect -->", "<!-- ar:roles all -->")
        )
    with pytest.raises(AssertionError):
        require_formal_permission_home(
            orientation.replace("## Formal permissions\n", "## Unrelated permissions\n")
        )
    child_questions = " ".join(
        (LIFECYCLE_ROOT / "reference/coordination/shared/child-questions.md").read_text().split()
    )
    _require_clauses(child_questions, permission_route_clauses)
    for clause in permission_route_clauses:
        mutant = child_questions.replace(clause, "", 1)
        assert mutant != child_questions, clause
        with pytest.raises(AssertionError):
            _require_clauses(mutant, permission_route_clauses)


def test_packet_alignment_and_advisory_review_keep_independent_preparation() -> None:
    """Packet alignment and advisory review keep independent preparation and ownership."""
    required = {
        "operations/planning.md": (
            "## Cross-packet alignment sweep",
            "Before releasing a new/revised packet into a master with open leaves",
            "counts, paths, IDs, order, premises",
            "bidirectionally",
            "non-drafter",
            'before `"go, or review first?"`',
            "repeat before `go` if revised",
            "Revise stale facts within promise; escalate beyond developer words.",
            "(including no-findings)",
            "all pages",
            "unattributed code",
            "Prepared context",
            "row kinds/dispositions/writer fields/gates",
            "how curation completes on installed build",
            "prove on fixtures/scratch",
            "Report any small item costing ~1h or 2nd round/freeze that day with hours & proposal.",
            "Take plan-review/super-exit to developer with sprint-level Reviewer idea.",
        ),
        "operations/review.md": (
            "## Advisory reviews",
            "Before commissioning/routing advisory reviews via `role_start`, read "
            "[advisory-reviews]({{detail:reference/review/advisory-reviews.md}}).",
        ),
    }
    planning = (LIFECYCLE_ROOT / "operations/planning.md").read_text()
    _assert_originating_leaf_home_controls(planning)
    one_home = "Escalate contradictions, promise/scope changes, or high-impact decisions;"
    assert planning.count(one_home) == 1
    assert "goes to the developer before either leaf proceeds" not in planning
    assert one_home not in _architect_passages(planning)
    for source, clauses in required.items():
        text = (LIFECYCLE_ROOT / source).read_text()
        selected = _architect_passages(text)
        _require_clauses(selected, clauses)
        for clause in clauses:
            mutant = selected.replace(clause, "")
            with pytest.raises(AssertionError, match=re.escape(clause)):
                _require_clauses(mutant, clauses)
        # The same prose in the all-roles passage cannot satisfy Architect-only ownership.
        moved = text.replace("<!-- ar:roles architect -->", "<!-- ar:roles all -->")
        assert not _architect_passages(moved)
    _assert_advisory_and_formal_permission_controls()

    architect = (LIFECYCLE_ROOT / "roles/architect.md").read_text()
    index_section = architect.split("## Reference index\n", 1)[1]
    for target in (
        "reference/coordination/shared/requirement-design.md",
        "reference/review/advisory-reviews.md",
    ):
        pointer = next(line for line in index_section.splitlines() if target in line)
        assert len(pointer.split()) <= 25, (target, pointer)
    coordination = (LIFECYCLE_ROOT / "operations/coordination.md").read_text()
    manager = " ".join(
        " ".join(
            re.findall(r"(?s)<!-- ar:roles manager -->\s*(.*?)(?=<!-- ar:roles |\Z)", coordination)
        ).split()
    )
    _require_clauses(
        manager,
        (
            "For integration/master-exit",
            "acceptance/delivery-proof leaf",
            "(ask Architect if unnamed)",
            "add candidate & attempts.",
            "send Reviewer's intake via `role_message`",
        ),
    )
    sync_repairs = " ".join(
        (LIFECYCLE_ROOT / "reference/worker/sync-and-repairs.md").read_text().split()
    )
    assert (
        "When the Manager gives the occasion, send the handover yourself; "
        "the Manager relays none of the contents." in sync_repairs
    )
    # Advice does not recreate the superseded cold-read sweep requirement.
    alignment = _architect_passages((LIFECYCLE_ROOT / "operations/planning.md").read_text())
    assert "drafted, cold-read and registered" not in alignment


def _require_worklist_fields_and_knowledge_command_owners() -> None:
    detail = (LIFECYCLE_ROOT / "reference/curator/worklist.md").read_text()
    worklist, gate = detail.split("`knowledgeGate`", 1)
    for field in ("state", "path", "digest", "itemCount", "itemsByKind", "incomplete"):
        assert f"| `{field}` |" in worklist, field
    for field in (
        "state",
        "owner",
        "worklistState",
        "worklistDigest",
        "worklistPath",
        "findingCount",
        "openItemCount",
        "incompleteCount",
        "violationCount",
        "reportOnlyCount",
    ):
        assert f"| `{field}` |" in gate, field
    assert "an absent block is not a passing judgment or an empty result" in detail
    reference = command_reference(REPOSITORY_ROOT)
    for column in ("What it does", "Who and when", "Example"):
        assert column in reference
    for tool in (name for name in PUBLIC_TOOLS if name.startswith("knowledge_")):
        assert f"| `{tool}` |" in reference
    assert_public_command_reference(reference)
    for role in ROLE_ORDER:
        text = (LIFECYCLE_ROOT / f"roles/{role}.md").read_text()
        assert "{{detail:reference/knowledge/commands.md}}" in text, role
    assert not (SKILLS_ROOT / "c-02-memory-quality-control").exists()


def _require_dependency_owner(text: str) -> None:
    section = re.search(r"(?ms)^## Check packet dependencies\n(.*?)(?=^## |\Z)", text)
    assert section is not None, "the Closeout dependency owner is missing"
    _require_clauses(
        " ".join(section[1].split()),
        (
            "The Manager checks packet dependencies before starting a leaf and before integrating it; "
            "the product does not perform that check.",
        ),
    )


def test_knowledge_gate_and_worklist_explanations_cover_actual_landing_steps() -> None:
    """Closeout and worklist retain exact-tree/refusal meaning and field owners."""
    closeout = " ".join((LIFECYCLE_ROOT / "operations/closeout.md").read_text().split())
    for clause in (
        "exact candidate pair",
        "leaf integration re-judges",
        "master memory against parent tip",
        "net code-diff currentness",
        "all non-abandoned leaves landed",
        "checkpoint proves landed chain",
        "direct landing judges its exact pair",
        "`knowledge-gate-refused`",
        "`direct-landing-knowledge-gate-refused`",
        "findings to Curator for repair",
        "bypass no gate",
    ):
        assert clause in closeout, clause
        assert clause not in closeout.replace(clause, "", 1)
    brief_routes = (
        "Before starting or integrating a leaf, read [the Manager\N{RIGHT SINGLE QUOTATION MARK}s dependency check]"
        "(../roles/manager.md#start-leaf-agents-and-preserve-their-work) for prerequisite packets "
        "and leaf-stage start authority.",
        "Before handling a landing-gate refusal, read [Authorized Closeout]"
        "(../operations/closeout.md#repair-a-gate-refusal-through-its-owner) for the gate-finding route.",
    )
    dependency_clauses = (
        "Check packet deps before starting/integrating (the product does not perform that check);",
    )
    gate_clauses = (
        "Send `knowledge-gate-refused` (closeout/integration) or "
        "`direct-landing-knowledge-gate-refused` (direct landing) findings to Curator for repair, "
        "then retry the owning route;",
        "bypass no gate.",
    )

    def require_brief_routes(text: str) -> None:
        section = re.search(r"(?ms)^### Check dependencies\n(.*?)(?=^### |\Z)", text)
        assert section is not None, "the Manager brief needs its dependency action"
        _require_clauses(" ".join(section[1].split()), brief_routes)

    def require_dependency_home(text: str) -> None:
        section = re.search(
            r"(?ms)^## Start leaf agents and preserve their work\n(.*?)(?=^## |\Z)", text
        )
        assert section is not None, "the Manager dependency home is missing"
        _require_clauses(" ".join(section[1].split()), dependency_clauses)

    def require_gate_home(text: str) -> None:
        section = re.search(
            r"(?ms)^## Repair a gate refusal through its owner\n(.*?)(?=^## |\Z)", text
        )
        assert section is not None, "the closeout refusal home is missing"
        _require_clauses(" ".join(section[1].split()), gate_clauses)

    brief = (LIFECYCLE_ROOT / "templates/manager-brief.md").read_text()
    manager = (LIFECYCLE_ROOT / "roles/manager.md").read_text()
    closeout_source = (LIFECYCLE_ROOT / "operations/closeout.md").read_text()
    for text, clauses, require_home in (
        (brief, brief_routes, require_brief_routes),
        (manager, dependency_clauses, require_dependency_home),
        (closeout_source, gate_clauses, require_gate_home),
    ):
        require_home(text)
        for clause in clauses:
            mutant = text.replace(clause, "", 1)
            assert mutant != text, clause
            with pytest.raises(AssertionError):
                require_home(mutant)
        # The same rule after an unrelated heading cannot stand in for its action home.
        heading = {
            require_brief_routes: "### Check dependencies",
            require_dependency_home: "## Start leaf agents and preserve their work",
            require_gate_home: "## Repair a gate refusal through its owner",
        }[require_home]
        with pytest.raises(AssertionError):
            require_home(text.replace(heading, heading.split(" ", 1)[0] + " Unrelated action", 1))
    _require_worklist_fields_and_knowledge_command_owners()


@pytest.mark.parametrize("command", sorted(public_commands()))
def test_each_public_command_requires_its_documentation_owner(command: str) -> None:
    reference = command_reference(REPOSITORY_ROOT)
    assert_public_command_reference(reference)
    with pytest.raises(AssertionError):
        assert_public_command_reference(misplace_command_documentation(reference, command))


def test_root_instructions_name_no_retired_job_lens_or_phase() -> None:
    """L84-CUR-04: lenses.md is gone and l-01 defines no job lens or reframe-research phase.

    The source checkout's root AGENTS.md therefore states no step that depends on either.
    """

    retired = ("reframe-research", "as a *lens*")
    lifecycle = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(LIFECYCLE_ROOT.rglob("*.md"))
    )
    assert not (LIFECYCLE_ROOT / "lenses.md").exists()
    assert [term for term in retired if term in lifecycle] == []
    root = (REPOSITORY_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert [term for term in retired if term in root] == []


@pytest.mark.parametrize(
    ("role", "clause"),
    [
        (
            "manager",
            "Follow `system/task-publication.md` if present (claiming a leaf assigns its issue "
            "and sets In progress before start; after landing and both pushes, close the issue; "
            "without that file, publish nothing).",
        ),
        (
            "architect",
            "Follow `system/task-publication.md` if present (registering a leaf creates its open "
            "linked issue; without that file, publish nothing);",
        ),
    ],
)
def test_role_names_its_publication_moments(role: str, clause: str) -> None:
    """L84-CUR-07: the publication clause names the role's moments (Architect ruling b2508984).

    The clause is the role's one pointer to task-publication.md; the generic
    "When publishing task work" sentence does not return in either copy.
    """

    packaged = REPOSITORY_ROOT / "mcp/src/agents_remember/package_data/runtime/skills"
    for root in (SKILLS_ROOT, packaged):
        text = (root / "l-01-agent-lifecycles" / "roles" / f"{role}.md").read_text(encoding="utf-8")
        assert text.count(clause) == 1
        assert text.count("system/task-publication.md") == 1
        assert "When publishing task work" not in text
