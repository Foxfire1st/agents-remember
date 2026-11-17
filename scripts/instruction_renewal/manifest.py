"""Derive capsule reference routing from authored pointer topology."""

from __future__ import annotations

import json
import re
from pathlib import Path

from agents_remember.errors import CapsuleManifestError
from agents_remember.models.role_capsules.manifest_derived import (
    compose_manifest,
    validate_no_step_marks,
)
from agents_remember_test_support.testing.manifest_step_derivation import (
    Derivation,
    derive,
    ordered_roles,
)


def plan(root: Path) -> dict:
    """Compute generated routing without writing files.

    First-message pointers follow source path and line order, then detail pointers
    follow manifest reference order and line order; an action keeps its first place.
    """
    lifecycle = root / "skills/l-01-agent-lifecycles"
    document = json.loads((lifecycle / "composition-manifest.json").read_text(encoding="utf-8"))
    validate_no_step_marks(document)
    facts = derive(lifecycle, document)
    references = {
        key: _reference_values(document, key, entry, facts.get(key))
        for key, entry in document["references"].items()
    }
    core_roles = {
        role for role, entry in document["roles"].items() if "every-role" in entry.get("core", [])
    }
    generated = {
        "references": references,
        "core": {"every-role": {"roles": ordered_roles(document, core_roles)}},
    }
    compose_manifest({**document, "derived": generated})
    return generated


def _reference_values(document: dict, key: str, entry: dict, item: Derivation | None) -> dict:
    roles = set(item.roles) if item is not None else set()
    reason = entry.get("rolesReason", "")
    roles.update(role for role in document["role_order"] if re.search(rf"\b{role}\b", reason, re.I))
    values: dict[str, object] = {"roles": ordered_roles(document, roles)} if roles else {}
    if "step" in entry and entry["step"] is None:
        return values
    if item is None or not item.actions:
        raise CapsuleManifestError(
            status="manifest-invalid",
            detail=f"references.{key} has no pointer-derived read step or step:null mark",
            next_action=(
                "name the reference's Before/When/For read action in its source pointer, "
                "or mark an ordinary reference step:null; then run "
                "mcp/.venv/bin/python scripts/renew-instructions.py"
            ),
        )
    values["step"] = "; ".join(item.actions)
    return values
