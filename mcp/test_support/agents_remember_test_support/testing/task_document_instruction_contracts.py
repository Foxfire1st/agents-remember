"""Source checks for task authoring entrypoints, current fields and rich scaffolds."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from agents_remember.kernel.onboarding_doc import unfenced_lines

POINTERS = {
    "roles/architect.md": "Before authoring a task document or requirement packet, read [task document authoring]({{detail:reference/tasks/document-authoring.md}}) for reusing the work that owns the request, explaining the plan, the prose of each task field, the proposed changes and preserving decisions and approved intent.",
    "templates/leaf-task.md": "When filling the task fields, follow [task prose fields](../reference/tasks/document-authoring.md#put-prose-in-the-fields-that-render-it) for what each field holds",
    "templates/master-task.md": "When authoring its fields and ordered sections through `task_doc`, follow [task prose fields](../reference/tasks/document-authoring.md#put-prose-in-the-fields-that-render-it) for what each field and section holds.",
    "templates/requirement-packet.md": "Before authoring a packet, read [task prose fields](../reference/tasks/document-authoring.md#put-prose-in-the-fields-that-render-it) for prose conventions.",
}
CLAUSES = (
    "Author task documents through `task_doc` on `agents-remember-task`.",
    "Put the outcome and its reason in `objective`, the settled approach in `design`, "
    "exclusions in `nonGoals` and observable completion in `acceptance`.",
    "Link approved packets in `requirements`; a task's explanation does not rewrite their contract.",
    "Additional narrative belongs in a `freeform` section's `body`.",
    "use `subTasks` for the generated child list and `sharedDecisions` for the decision table.",
    "Put its other narrative, questions and references in `freeform` sections.",
    "For a leaf, use `codeExamples`.",
    "When its examples are deferred or unnecessary, explain their absence in `codeExamplesNote`; "
    "do not combine that note with examples.",
    "A master accepts neither `codeExamples` nor `codeExamplesNote`.",
    "The templates guide authoring; the task schema and tool own the stored structure and rendered headings.",
)
TEMPLATE_CLAUSES = {
    "leaf-task": (
        "## Objective",
        "## Design",
        "## Acceptance",
        "## Proposed Code Examples",
        "## Decision Log",
        "## Open Questions",
        "Explain why the change matters",
        "Discuss relevant alternatives and the reasons for rejecting them.",
    ),
    "master-task": (
        "## Objective",
        "## Design",
        "## Acceptance",
        "## Sub-tasks",
        "## Shared Decisions",
        "Explain the relevant alternatives, interactions, dependencies and risks.",
        "Use a `freeform` section for cross-cutting unresolved questions",
    ),
    "requirement-packet": (
        "## Normative Requirement",
        "## Required Behavior",
        "## Preservation Boundaries",
        "## Forbidden Overreach",
        "## Failure And Recovery Behavior",
        "## Expected Evidence",
    ),
}
HEADING = "## Put prose in the fields that render it"


def assert_contract(repository_root: Path, generated_skill_copies: tuple[str, ...]) -> None:
    """Every authoring entry points to its current home outside example fences."""
    for surface in ("skills", *generated_skill_copies):
        root = repository_root / surface / "l-01-agent-lifecycles"
        for name, pointer in POINTERS.items():
            path = root / name
            raw = path.read_text(encoding="utf-8")
            text = " ".join(raw.split())
            assert "runtime ships" not in text and "not tool-managed yet" not in text, path
            assert "hand-authored markdown for now" not in text, path
            prose = " ".join(line for _, line in unfenced_lines(raw.splitlines()))
            prose = " ".join(prose.split())
            assert pointer in prose, pointer
            relocated = prose.replace(pointer, "") + f"\n````markdown\n{pointer}\n````\n"
            relocated_prose = " ".join(line for _, line in unfenced_lines(relocated.splitlines()))
            with pytest.raises(AssertionError, match=re.escape(pointer)):
                assert pointer in relocated_prose, pointer
        detail = (root / "reference/tasks/document-authoring.md").read_text(encoding="utf-8")
        procedure = " ".join(line for _, line in unfenced_lines(detail.splitlines()))
        procedure = " ".join(procedure.split())
        assert procedure.count(HEADING) == 1, HEADING
        for clause in CLAUSES:
            assert clause in procedure, clause
            with pytest.raises(AssertionError, match=re.escape(clause)):
                for required_clause in CLAUSES:
                    assert required_clause in procedure.replace(clause, ""), required_clause
        for name, current_clauses in {
            "operations/orientation.md": (
                "For canonical task-document editing admitted to your role, use `task_doc` "
                "on `agents-remember-task` first.",
                "Edits to rendered copies are never read back and are lost on the next publication.",
            ),
        }.items():
            current = " ".join(
                line
                for _, line in unfenced_lines(
                    (root / name).read_text(encoding="utf-8").splitlines()
                )
            )
            current = " ".join(current.split())
            for clause in current_clauses:
                assert clause in current, (name, clause)
                with pytest.raises(AssertionError, match=re.escape(clause)):
                    assert clause in current.replace(clause, ""), clause
        references = json.loads((root / "composition-manifest.json").read_text())["references"]
        for key in ("tasks/document-authoring", *TEMPLATE_CLAUSES):
            reference = references[key]
            source = (
                "reference/tasks/document-authoring.md"
                if key == "tasks/document-authoring"
                else "templates/" + key + ".md"
            )
            assert reference["source"] == source
            expected_roles = (
                ["architect", "orchestrator", "manager", "worker", "bootstrap"]
                if key == "tasks/document-authoring"
                else ["architect", "manager"]
            )
            assert reference["roles"] == expected_roles
            assert reference["injected"] is False
            assert reference["step_detail"] is (key == "tasks/document-authoring")
            if key != "tasks/document-authoring":
                template = (root / source).read_text(encoding="utf-8")
                for clause in TEMPLATE_CLAUSES[key]:
                    assert clause in template, (key, clause)
                    with pytest.raises(AssertionError, match=re.escape(clause)):
                        assert clause in template.replace(clause, ""), clause
