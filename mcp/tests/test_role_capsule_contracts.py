"""R62 rule25: source-addressed handoff contracts never become instruction prose."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.errors import CapsuleCompilationError
from agents_remember.models.role_capsules.compiler import compile_role_capsule
from agents_remember.models.role_capsules.types import CapsuleRoleSeat, compute_content_digest
from agents_remember.models.role_capsules.vocabulary import CAPSULE_ROLES
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from test_role_capsule_compiler import (
    all_sources,
    fixture_manifest_document,
    manifest_bytes,
    selection_for,
    worker_binding,
)


def _compile(role_text: str, stage: str | None = None):
    document = fixture_manifest_document()
    if stage is not None:
        operations = cast(dict[str, dict[str, object]], document["operations"])
        operations["orientation"]["audience"] = "sections"
    changes = {"roles/worker.md": role_text}
    if stage is not None:
        changes["operations/orientation.md"] = stage
    sources = tuple(
        replace(source, content=text.encode(), revision=compute_content_digest(text.encode()))
        if (text := changes.get(source.path)) is not None
        else source
        for source in all_sources(document)
    )
    return compile_role_capsule(
        worker_binding(operation="orientation"),
        manifest_bytes(document),
        sources,
        selection_for(document),
    )


def test_contract_lines_are_stripped_and_keep_their_source_address() -> None:
    plain = "# Worker\nGive the freeze report.\n"
    marked = "# Worker\n<!-- ar:gives freeze-handoff to reviewer: freeze-report, candidate-identity, attempt-records -->\nGive the freeze report.\n"
    result = _compile(marked)
    assert result.render_instructions() == _compile(plain).render_instructions()
    assert result.semantic_digest != _compile(plain).semantic_digest
    markers = getattr(result.capsule, "contract_markers", None)
    assert markers is not None, "compiled capsule must expose source-addressed contract metadata"
    assert len(markers) == 1
    marker = markers[0]
    assert (marker.kind, marker.key, marker.peer_role) == ("gives", "freeze-handoff", "reviewer")
    assert marker.fields == ("freeze-report", "candidate-identity", "attempt-records")
    assert (marker.source_path, marker.line) == ("roles/worker.md", 2)


@pytest.mark.parametrize(
    "marker",
    [
        "<!-- ar:needs freeze-handoff to worker: report -->",
        "<!-- ar:gives freeze-handoff from reviewer: report -->",
        "<!-- ar:gives Bad_Key to reviewer: report -->",
        "<!-- ar:gives freeze-handoff to invented-role: report -->",
        "<!-- ar:gives freeze-handoff to reviewer: report, report -->",
        "<!-- AR:GIVES freeze-handoff to reviewer: report -->",
        "<!-- ar:gives freeze-handoff to reviewer: report --> extra",
    ],
)
def test_malformed_contracts_refuse_with_original_path_and_line(marker: str) -> None:
    with pytest.raises(CapsuleCompilationError) as raised:
        _compile("# Worker\n" + marker + "\nKeep working.\n")
    assert raised.value.status == "contract-marker-invalid"
    assert "roles/worker.md:2:" in str(raised.value)


def test_off_audience_malformed_contract_is_validated_before_selection() -> None:
    stage = "<!-- ar:roles all -->\nCommon.\n<!-- ar:roles curator -->\n<!-- ar:needs bad to worker: report -->\n<!-- ar:roles worker -->\nBuild.\n"
    with pytest.raises(CapsuleCompilationError) as raised:
        _compile("# Worker\n", stage)
    assert raised.value.status == "contract-marker-invalid"
    assert "operations/orientation.md:4:" in str(raised.value)


def test_contract_metadata_contains_only_the_selected_audience() -> None:
    stage = "<!-- ar:roles all -->\nCommon.\n<!-- ar:roles curator -->\n<!-- ar:needs producer-list from reviewer: list-file -->\n<!-- ar:roles worker -->\n<!-- ar:gives producer-list to curator: list-file -->\nBuild.\n"
    result = _compile("# Worker\n", stage)
    assert "ar:gives" not in result.render_instructions()
    assert "ar:needs" not in result.render_instructions()
    markers = getattr(result.capsule, "contract_markers", None)
    assert markers is not None
    assert [(x.kind, x.peer_role, x.line) for x in markers] == [("gives", "curator", 6)]


def test_shipped_contracts_have_one_matching_sender_and_receiver() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    manifest = (root / "composition-manifest.json").read_bytes()
    operations = {
        "architect": "planning",
        "orchestrator": "coordination",
        "manager": "coordination",
        "worker": "implementation",
        "reviewer": "review",
        "curator": "curation",
        "investigator": "orientation",
        "designer": "planning",
        "strategist": "planning",
        "bootstrap": "bootstrap",
    }
    sides: dict[tuple[str, str, str], dict[str, list]] = {}
    for role in CAPSULE_ROLES:
        binding = worker_binding(operation=operations[role], granted=frozenset(PUBLIC_TOOLS))
        binding = replace(
            binding, admitted=replace(binding.admitted, seat=CapsuleRoleSeat(role, "leaf"))
        )
        admission = routed_admission_for(
            root, "composition-manifest.json", manifest, CapsuleSeatAddress(role, operations[role])
        )
        outcome = compile_admitted_capsule(binding, admission)
        assert outcome.ok, outcome.render_explanation()
        assert outcome.result is not None
        capsule = outcome.result.capsule
        markers = getattr(capsule, "contract_markers", None)
        assert markers is not None
        assert "<!-- ar:needs" not in outcome.result.render_instructions()
        assert "<!-- ar:gives" not in outcome.result.render_instructions()
        for marker in markers:
            sender, recipient = (
                (role, marker.peer_role) if marker.kind == "gives" else (marker.peer_role, role)
            )
            sides.setdefault((marker.key, sender, recipient), {"needs": [], "gives": []})[
                marker.kind
            ].append(marker)
    expected = {
        ("leaf-review-intake", "manager", "reviewer"),
        ("leaf-review-intake", "architect", "reviewer"),
        ("review-intake", "manager", "reviewer"),
        ("freeze-handoff", "worker", "reviewer"),
        ("producer-list", "worker", "curator"),
        ("producer-list", "reviewer", "curator"),
    }
    assert set(sides) == expected
    for triple, pair in sides.items():
        assert len(pair["needs"]) == len(pair["gives"]) == 1, (triple, pair)
        assert pair["needs"][0].fields == pair["gives"][0].fields, (triple, pair)
