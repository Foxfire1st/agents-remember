"""Compose authored capsule routing with its renewed pointer-derived values."""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.errors import CapsuleManifestError


def validate_no_step_marks(document: Mapping[str, object]) -> None:
    """Validate authored no-step decisions independently of generated routing."""
    references = _mapping(document.get("references", {}), "references")
    for name, authored in references.items():
        entry = _mapping(authored, f"references.{name}")
        _validate_mark(name, entry)


def compose_manifest(document: Mapping[str, object]) -> dict:
    """Return the current routing view without mutating either ownership part.

    Reference topology remains authored. Its roles and action wording come only
    from the derived part, including when a caller removes an authored reference.
    """
    result = dict(document)
    if "references" not in document:
        return result
    references = _mapping(document["references"], "references")
    derived = _mapping(document.get("derived"), "derived")
    generated = _mapping(derived.get("references"), "derived.references")
    result["references"] = {
        name: _reference(name, entry, generated.get(name, {})) for name, entry in references.items()
    }
    if "core" in derived:
        core = _mapping(document.get("core"), "core")
        generated_core = _mapping(derived["core"], "derived.core")
        result["core"] = {
            name: {
                **{
                    key: value
                    for key, value in _mapping(entry, f"core.{name}").items()
                    if key != "roles"
                },
                **_mapping(generated_core.get(name, {}), f"derived.core.{name}"),
            }
            for name, entry in core.items()
        }
    return result


def _reference(name: str, authored: object, generated: object) -> dict[str, object]:
    entry = _mapping(authored, f"references.{name}")
    values = _mapping(generated, f"derived.references.{name}")
    _validate_mark(name, entry)
    no_step = "step" in entry and entry["step"] is None
    has_step = "step" in values
    if no_step and has_step:
        raise CapsuleManifestError(
            status="manifest-invalid",
            detail=f"derived.references.{name}.step is stale beside its authored step:null mark",
            next_action="run mcp/.venv/bin/python scripts/renew-instructions.py",
        )
    if not no_step and not has_step:
        raise CapsuleManifestError(
            status="manifest-invalid",
            detail=f"references.{name} has neither a no-step mark nor a derived step",
            next_action=(
                "run mcp/.venv/bin/python scripts/renew-instructions.py; use step:null only "
                "for an ordinary reference that declares no step"
            ),
        )
    return {
        **{key: value for key, value in entry.items() if key not in {"roles", "step"}},
        **values,
    }


def _validate_mark(name: str, entry: Mapping[str, object]) -> None:
    if "step" not in entry or entry["step"] is not None or entry.get("step_detail") is not True:
        return
    raise CapsuleManifestError(
        status="manifest-invalid",
        detail=f"references.{name} has a no-step mark on a step_detail:true reference",
        next_action=(
            f"give references.{name} a source action and remove its step:null mark; "
            "then run mcp/.venv/bin/python scripts/renew-instructions.py; "
            "a no-step decision is legal only on an ordinary reference"
        ),
    )


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CapsuleManifestError(
            status="manifest-invalid",
            detail=f"{label} must be an object in the instruction manifest",
            next_action=(
                "run mcp/.venv/bin/python scripts/renew-instructions.py from the source repository"
            ),
        )
    return value
