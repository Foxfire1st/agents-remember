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


def _compile_moved_piece_witness(root: Path, role: str, operation: str) -> str:
    request = routed_admission_for(
        root,
        MANIFEST_RELATIVE,
        (root / MANIFEST_RELATIVE).read_bytes(),
        CapsuleSeatAddress(role, operation),
    )
    outcome = compile_admitted_capsule(_shipped_binding(role, operation), request)
    assert outcome.ok, outcome.render_explanation()
    assert outcome.result is not None
    return outcome.result.render_instructions()


@pytest.mark.parametrize(
    ("name", "action", "label", "clause"),
    (
        (
            "requirement-packet",
            "Before writing a requirement packet, read",
            "the requirement packet template",
            "<Write the independently falsifiable promise. State who acts, when it applies and what must be observably true.>",
        ),
        (
            "leaf-task",
            "Before authoring a leaf, read",
            "the leaf task template",
            "<The outcome and the focused evidence that demonstrates it. Use the task's step records for progress.>",
        ),
        (
            "master-task",
            "Before authoring a master, read",
            "the master task template",
            "<Use a section with kind `sharedDecisions` for the task's decision records. Its introduction explains any cross-cutting context the decisions need.>",
        ),
    ),
)
def test_architect_ordinary_template_reach_rejects_compiled_pointer_loss(
    tmp_path: Path, name: str, action: str, label: str, clause: str
) -> None:
    """U0737/U0738/U0768 need a working-step link, not just a template on disk."""
    root = tmp_path / "corpus"
    shutil.copytree(LIFECYCLE_ROOT, root)
    source = root / "roles/architect.md"
    original = source.read_text()
    link = f"../templates/{name}.md"
    pointer = f"{action} [{label}]({link})"
    assert original.count(pointer) == 1
    expected = (LIFECYCLE_ROOT / "templates" / f"{name}.md").read_bytes()
    assert clause in expected.decode()

    def require_reach(rendered: str) -> None:
        assert pointer in " ".join(rendered.split()), "missing template action pointer"
        target = (source.parent / link).resolve()
        assert target == (root / "templates" / f"{name}.md").resolve()
        assert target.read_bytes() == expected
        assert clause in target.read_text()

    require_reach(_compile_moved_piece_witness(root, "architect", "planning"))
    for mutant in (
        original.replace(pointer, "", 1),
        original.replace(action, "After completing unrelated work, read", 1),
    ):
        assert mutant != original
        try:
            source.write_text(mutant)
            changed = _compile_moved_piece_witness(root, "architect", "planning")
            with pytest.raises(AssertionError, match="missing template action pointer"):
                require_reach(changed)
        finally:
            source.write_text(original)
    require_reach(_compile_moved_piece_witness(root, "architect", "planning"))


@pytest.mark.parametrize(
    "prefix",
    (
        "Before writing memory, read `worktree_status` on `agents-remember-task`",
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
        section = rendered.split("## Normal workflow\n", 1)[1].split("\n## ", 1)[0]
        assert " ".join(paragraph.split()) in " ".join(section.split()), (
            "missing inline workflow paragraph"
        )

    require_workflow(_compile_moved_piece_witness(root, "curator", "curation"))
    try:
        source.write_text(original.replace(paragraph, "", 1))
        changed = _compile_moved_piece_witness(root, "curator", "curation")
        with pytest.raises(AssertionError, match="missing inline workflow paragraph"):
            require_workflow(changed)
    finally:
        source.write_text(original)
    require_workflow(_compile_moved_piece_witness(root, "curator", "curation"))
