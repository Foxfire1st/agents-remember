"""Actual selected lifecycle fixtures, separate from bare journal/component repositories."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.models.lifecycles.door import CloseoutDoorRequest
from agents_remember.models.lifecycles.operation import (
    CloseoutOperationInput,
)
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.leaf_doc import resolve_terminal_leaf_doc
from agents_remember.worktrees.activation.atomic_series_activation import (
    publish_atomic_series_selection,
)
from agents_remember.worktrees.integration.closeout.door_control import (
    DoorActor,
    closeout_door_tool,
)
from agents_remember.worktrees.integration.closeout.door_source import door_task_context
from agents_remember.worktrees.route_review import build_route_review
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
)
from closeout_input_test_support import closeout_operation_input
from curator_coherence_test_support import write_curator_evidence
from test_closeout_certification_entrypoint import _fixture
from test_closeout_queue import (
    JUDGMENT_HEADING,
    MASTER_A,
    NOW,
    PRIORITY_HEADING,
    _grade,
    _judgment_row,
    _judgment_table,
    _priority_row,
    _priority_table,
)


def declare_selected_candidate(
    contract: WorktreeContract, *, config_path: Path | None = None
) -> WorktreeContract:
    """Declare fixture evidence through the actual owners without replacing its task tree.

    Callers install their explicit profile before branch cuts and prepare all candidate bytes
    before this boundary. Existing doors are never silently refreshed or reselected.
    """
    contract = load_contract(contract.contract_path)
    if contract.closeout_door is not None:
        return contract
    assert contract.kind == "leaf"
    configured = config_path or contract.code_repo_path.parent / "settings.json"
    config = load_config(configured)
    found = resolve_terminal_leaf_doc(contract.task_root, contract.leaf_id)
    assert found is not None, "selected fixture needs its actual canonical leaf document"
    path, document = found
    leaf = TaskDocument.model_validate(
        {
            **document.model_dump(mode="json"),
            "objective": document.objective or "Exercise the exact lifecycle fixture candidate.",
            "requirements": document.requirements or ["Preserve the selected operation boundary."],
            "enclosures": [
                {"leafId": contract.leaf_id, "enclosurePath": str(contract.contract_path)}
            ],
            "steps": [{"id": "S1", "title": "Fixture candidate prepared", "status": "done"}],
        }
    )
    write_task_doc(path.parent, leaf)
    request = CloseoutDoorRequest(action="status", contract_path=str(contract.contract_path))
    context = door_task_context(config, contract, request)
    assert context.candidate is not None
    if context.master.document.executionNature == "atomic":
        assert contract.parent_contract_path is not None
        publish_atomic_series_selection(
            load_contract(contract.parent_contract_path), "active", timestamp=NOW
        )
    report = contract.task_root / "notes" / "reports" / f"{leaf.slug}-review.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        "# Fixture route review\n\nPass: candidate setup is complete.\n", encoding="utf-8"
    )
    report_ref = report.relative_to(contract.task_root).as_posix()
    review = build_route_review(
        contract,
        ResolvedTaskDocument(ref=context.candidate.ref, path=path, document=leaf),
        {
            "verdict": "pass",
            "verdictRef": report_ref,
            "routes": [{"route": leaf.slug, "verdict": "pass", "evidenceRef": report_ref}],
        },
        now=datetime.fromisoformat(NOW),
    )
    write_task_doc(path.parent, leaf.model_copy(update={"routeReview": review}))
    rows = (
        (JUDGMENT_HEADING, _judgment_row(context.candidate.ref, "normal"), _judgment_table),
        (PRIORITY_HEADING, _priority_row(context.candidate.ref, "normal"), _priority_table),
    )
    sprint = read_task_doc(context.sprint.path).model_dump(mode="json")
    sections = sprint["sections"]
    for heading, row, table in rows:
        existing = next(
            (section for section in sections if section.get("heading") == heading), None
        )
        if existing is None:
            sections.append({"kind": "freeform", "heading": heading, "body": table([row])})
        else:
            assert context.candidate.ref.key not in existing["body"], (
                "fixture grade already authored"
            )
            existing["body"] += "\n" + row
    write_task_doc(context.sprint.path.parent, TaskDocument.model_validate(sprint))
    (context.sprint.path.parent / "grade.md").write_text(
        "# Fixture scheduling grade\n", encoding="utf-8"
    )
    if contract.memory_worktree is not None:
        write_curator_evidence(contract, caller_ref=context.sprint.ref)
    result = closeout_door_tool(
        config,
        CloseoutDoorRequest.model_validate(
            {
                "action": "declare",
                "contract_path": str(contract.contract_path),
                "grade": _grade("normal", context.candidate.ref),
                "admission": {},
            }
        ),
        actor=DoorActor(role="manager", task_document_ref=context.master.ref),
        admitted_contract=contract,
    )
    assert result["ok"] is True and result["state"] == "waiting", result
    declared = load_contract(contract.contract_path)
    assert declared.closeout_door is not None
    assert not declared.closeout_door.declaredBy.startswith("test-fixture:")
    return declared


def selected_closeout_operation_input(
    contract: WorktreeContract, **values
) -> CloseoutOperationInput:
    """Explicitly prepare a selected fixture door before normalizing its admission input."""
    declared = declare_selected_candidate(contract, config_path=values.get("config_path"))
    return closeout_operation_input(declared, **values)


def selected_contract(
    root: Path, *, candidate_file: tuple[str, str] | None = None
) -> WorktreeContract:
    fixture = _fixture(root, candidate_file=candidate_file)
    contract = fixture.contracts[MASTER_A]
    contract = load_contract(contract.contract_path)
    assert contract.closeout_door is not None
    assert contract.closeout_door.disposition == "waiting"
    assert not contract.closeout_door.declaredBy.startswith("test-fixture:")
    return contract
