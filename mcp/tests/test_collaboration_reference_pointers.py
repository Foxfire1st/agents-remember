"""The collaboration and authoring references equal the step and audiences their pointers derive.

The derivation (``manifest_step_derivation``) reads the first-message texts: roles, operations,
SKILL.md and every-role. Each refusal case changes one side and shows the mismatch is caught.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from agents_remember_test_support.testing.manifest_step_derivation import manifest_mismatches
from test_l84_capsule_delivery_proofs import _shipped_parsed

COLLABORATION_KEYS = ("tasks/collaboration-method", "tasks/document-authoring")


def _assert_collaboration_reference_matches_pointers(root: Path, document: dict) -> None:
    """The collaboration and authoring references equal their pointer-derived step and roles.

    The derivation (``manifest_step_derivation``) reads the first-message texts:
    roles, operations, SKILL.md and every-role.
    """
    problems = [
        problem
        for problem in manifest_mismatches(root, document)
        if problem.startswith(tuple(key + ":" for key in COLLABORATION_KEYS))
    ]
    assert problems == []
    assert document["references"]["tasks/collaboration-method"]["step"].strip()


def test_collaboration_reference_declares_actual_pointer_audiences_and_step() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    document = json.loads((root / "composition-manifest.json").read_bytes())
    reference = document["references"]["tasks/collaboration-method"]
    assert reference["source"] == "reference/tasks/collaboration-method.md"
    _assert_collaboration_reference_matches_pointers(root, document)
    assert reference["injected"] is False
    assert reference["step_detail"] is False
    assert (root / reference["source"]).is_file()
    assert reference["source"] not in {
        entry.source for entry in _shipped_parsed().detail_references
    }


def test_collaboration_reference_rejects_unpointed_audience_and_outbound_step(
    tmp_path: Path,
) -> None:
    source_root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    document = json.loads((source_root / "composition-manifest.json").read_bytes())
    reference = document["references"]["tasks/collaboration-method"]
    changed = json.loads(json.dumps(document))
    changed["references"]["tasks/collaboration-method"]["roles"] += [
        next(role for role in document["role_order"] if role not in reference["roles"])
    ]
    with pytest.raises(AssertionError):
        _assert_collaboration_reference_matches_pointers(source_root, changed)
    # Moving the inbound first-message pointer into the method itself leaves an outbound
    # self-reference, which cannot satisfy the callers' action contract.
    root = tmp_path / "l-01"
    shutil.copytree(source_root, root)
    every_role = root / "core/every-role.md"
    body = every_role.read_text()
    pointer = next(line for line in body.splitlines() if "[collaboration method](" in line)
    every_role.write_text(body.replace(pointer + "\n", "", 1))
    own = root / "reference/tasks/collaboration-method.md"
    own.write_text(own.read_text() + "\n" + pointer.split(": ", 1)[1] + "\n")
    assert every_role.read_text() != body
    with pytest.raises(AssertionError):
        _assert_collaboration_reference_matches_pointers(root, document)


def test_collaboration_reference_rejects_missing_actual_pointer_audience() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    document = json.loads((root / "composition-manifest.json").read_bytes())
    changed = json.loads(json.dumps(document))
    audience = changed["references"]["tasks/document-authoring"]["roles"]
    assert "worker" in audience
    audience.remove("worker")
    assert changed != document
    with pytest.raises(AssertionError):
        _assert_collaboration_reference_matches_pointers(root, changed)
