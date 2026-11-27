"""A compiled capsule keeps the pointer or inline paragraph a moved piece of a role text needs.

Each case mutates one source in a copied corpus and shows the compile then loses the reach; the
unmutated corpus keeps it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from test_role_capsule_admission import LIFECYCLE_ROOT, MANIFEST_RELATIVE, _shipped_binding


def _compile_moved_piece_witness(root: Path, role: str, operation: str) -> tuple[str, set[str]]:
    request = routed_admission_for(
        root,
        MANIFEST_RELATIVE,
        (root / MANIFEST_RELATIVE).read_bytes(),
        CapsuleSeatAddress(role, operation),
    )
    outcome = compile_admitted_capsule(_shipped_binding(role, operation), request)
    assert outcome.ok, outcome.render_explanation()
    assert outcome.result is not None
    detail_paths = {ref.source_path for ref in outcome.result.capsule.detail_references}
    return outcome.result.render_instructions(), detail_paths


@pytest.mark.parametrize(
    ("name", "label", "clause"),
    (
        (
            "requirement-packet",
            "requirement packet template",
            "<Write the independently falsifiable promise. State who acts, when it applies and what must be observably true.>",
        ),
        (
            "leaf-task",
            "leaf task template",
            "<The outcome and the focused evidence that demonstrates it. Use the task's step records for progress.>",
        ),
        (
            "master-task",
            "master task template",
            "<Use a section with kind `sharedDecisions` for the task's decision records. Its introduction explains any cross-cutting context the decisions need.>",
        ),
    ),
)
def test_architect_ordinary_template_reach_rejects_compiled_pointer_loss(
    tmp_path: Path, name: str, label: str, clause: str
) -> None:
    """Reach templates via Architect's Tier-2 document-authoring detail."""
    root = tmp_path / "corpus"
    shutil.copytree(LIFECYCLE_ROOT, root)
    architect_source = root / "roles/architect.md"
    authoring_rel = "reference/tasks/document-authoring.md"
    authoring_source = root / authoring_rel
    original_architect = architect_source.read_text()
    original_authoring = authoring_source.read_text()
    detail_pointer = f"[authoring]({{{{detail:{authoring_rel}}}}})"
    template_link = f"../../templates/{name}.md"
    template_pointer = f"[{label}]({template_link})"
    assert original_architect.count(detail_pointer) == 1
    assert original_authoring.count(template_pointer) == 1
    expected = (LIFECYCLE_ROOT / "templates" / f"{name}.md").read_bytes()
    assert clause in expected.decode()

    def require_reach(rendered: str, detail_paths: set[str]) -> None:
        assert detail_pointer in " ".join(rendered.split()), "missing authoring detail pointer"
        assert authoring_rel in detail_paths, "missing authoring detail reference"
        authoring_text = authoring_source.read_text()
        assert template_pointer in " ".join(authoring_text.split()), "missing template link"
        target = (authoring_source.parent / template_link).resolve()
        assert target == (root / "templates" / f"{name}.md").resolve()
        assert target.read_bytes() == expected
        assert clause in target.read_text()

    require_reach(*_compile_moved_piece_witness(root, "architect", "planning"))
    try:
        architect_source.write_text(original_architect.replace(detail_pointer, "", 1))
        with pytest.raises(AssertionError, match="missing authoring detail pointer"):
            require_reach(*_compile_moved_piece_witness(root, "architect", "planning"))
    finally:
        architect_source.write_text(original_architect)
    try:
        authoring_source.write_text(original_authoring.replace(template_pointer, "", 1))
        with pytest.raises(AssertionError, match="missing template link"):
            require_reach(*_compile_moved_piece_witness(root, "architect", "planning"))
    finally:
        authoring_source.write_text(original_authoring)
    require_reach(*_compile_moved_piece_witness(root, "architect", "planning"))


@pytest.mark.parametrize(
    "prefix",
    (
        "Verify writer outputs resolve in `workspace.memoryRoot`. Read `worktree_status`",
        "Record staleness known at task intake as the task\u2019s curation work.",
    ),
)
def test_curator_inline_workflow_rejects_compiled_owner_paragraph_loss(
    tmp_path: Path, prefix: str
) -> None:
    """U0305/U0421 remain inline Curator workflow, without an invented role pointer."""
    root = tmp_path / "corpus"
    shutil.copytree(LIFECYCLE_ROOT, root)
    source = root / "operations/curation.md"
    original = source.read_text()
    paragraphs = [p for p in original.split("\n\n") if p.startswith(prefix)]
    assert len(paragraphs) == 1
    paragraph = paragraphs[0]

    def require_workflow(rendered: str) -> None:
        section = rendered.split("## Workflow\n", 1)[1].split("\n## ", 1)[0]
        assert " ".join(paragraph.split()) in " ".join(section.split()), (
            "missing inline workflow paragraph"
        )

    require_workflow(_compile_moved_piece_witness(root, "curator", "curation")[0])
    try:
        source.write_text(original.replace(paragraph, "", 1))
        changed = _compile_moved_piece_witness(root, "curator", "curation")[0]
        with pytest.raises(AssertionError, match="missing inline workflow paragraph"):
            require_workflow(changed)
    finally:
        source.write_text(original)
    require_workflow(_compile_moved_piece_witness(root, "curator", "curation")[0])
