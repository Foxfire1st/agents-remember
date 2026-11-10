"""Task-authoring prose and template boundaries at their existing model owners."""

from pathlib import Path
from typing import Any, Literal

import pytest
from agents_remember.application.task_projection.packets import (
    INJECTED_PACKET_ROLES,
    parse_packet,
    role_section,
)
from agents_remember.tasks.document import TaskDocument
from agents_remember.tasks.render import render_markdown
from pydantic import ValidationError


def _document(kind: Literal["subTask", "master"], **fields: Any) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": "example",
            "slug": "example",
            "title": "Explain the change",
            "kind": kind,
            "repo": "example",
            "createdAt": "2026-01-01T00:00",
            **fields,
        }
    )


def test_rich_leaf_and_master_prose_survives_rendering() -> None:
    story = "A reviewer loses the explanation.\n\nThe plan keeps both paragraphs visible."
    design = "The existing fields carry the story.\n\nA second document would divide its authority."
    for kind in ("subTask", "master"):
        document = _document(
            kind,
            objective=story,
            design=design,
            nonGoals="Other ownership stays unchanged.",
            acceptance="Both complete paragraphs remain in the rendered task.",
            sections=[{"kind": "freeform", "heading": "Reasoning example", "body": story}],
        )
        rendered = render_markdown(document)
        assert "## Objective\n\n" + story in rendered
        assert "## Design\n\n" + design in rendered
        assert "## Reasoning example\n\n" + story in rendered
        assert "Other ownership stays unchanged." in rendered
        assert "Both complete paragraphs remain in the rendered task." in rendered
        assert render_markdown(document) == rendered


def test_master_examples_use_freeform_prose_and_refuse_the_leaf_absence_field() -> None:
    explanation = (
        "Examples follow the unresolved interface choice.\n\nThe design records that dependency."
    )
    master = _document(
        "master",
        sections=[{"kind": "freeform", "heading": "Proposed examples", "body": explanation}],
    )
    assert "## Proposed examples\n\n" + explanation in render_markdown(master)
    with pytest.raises(ValidationError, match="a master document has no steps, codeExamples"):
        _document("master", codeExamplesNote=explanation)
    leaf = _document("subTask", codeExamplesNote=explanation)
    assert "## Proposed Code Examples\n\n" + explanation in render_markdown(leaf)


def test_current_packet_template_retains_every_parsed_obligation_heading() -> None:
    template = (
        Path(__file__).resolve().parents[2]
        / "skills/l-01-agent-lifecycles/templates/requirement-packet.md"
    ).read_bytes()
    packet = parse_packet(raw=template, stable_id="EXAMPLE", revision="v1", path="template.md")
    for role in INJECTED_PACKET_ROLES:
        section = role_section(packet, role)
        assert section is not None and section.body.strip(), role
    mutant = template.replace(b"## Expected Evidence", b"## Unclassified evidence")
    changed = parse_packet(raw=mutant, stable_id="EXAMPLE", revision="v1", path="template.md")
    assert role_section(changed, "expected-evidence") is None
    assert role_section(changed, "required-behavior") == role_section(packet, "required-behavior")
