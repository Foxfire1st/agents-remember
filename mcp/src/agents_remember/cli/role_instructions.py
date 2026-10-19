"""Plain-text instruction composition at the launch boundary: baseline, addition, replacement.

Two contracts live here, and both are pure text work over bytes this process already owns:

* :func:`compose_role_instructions` puts one user's note over one admitted role capsule: the
  baseline stays, the note is appended, or the note replaces the baseline whole. The record says
  which of the three applied.
* :class:`CapabilityResource` registers a *capability's* canonical instruction text — the presenter
  is the first — and :func:`load_capability_resource` reads the **installed generated copy** under
  ``package_data``, never the checkout's source file, because an installed build has no checkout.
  The finite word budget and the required wording are enforced at that read, so a build that ships
  a truncated or edited-away text refuses by name instead of handing a presenter a different
  assignment.

The generated copy is produced by :func:`write_generated_capability_copy` from the canonical source
and compared by :func:`capability_copy_drift`. Nothing on the read path touches a repository path:
:func:`load_capability_resource` resolves the package's own data through
:mod:`importlib.resources`, which is also what a wheel or an sdist installation exposes.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath
from typing import Any

#: The declared schema of one capability resource registration.
CAPABILITY_RESOURCE_SCHEMA = "ar-capability-resource/v1"

#: The kinds a delivered capability text can have, in the order a reader checks them.
CAPABILITY_BASELINE = "baseline"
CAPABILITY_ADDITION = "addition"
CAPABILITY_REPLACEMENT = "replacement"


def instruction_metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **record,
        "blocks": [
            {k: v for k, v in block.items() if k != "deliveredText"} for block in record["blocks"]
        ],
    }


def launch_choice_metadata(choices: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(choices)
    notes = result.get("notes", {})
    text = notes.pop("value", None)
    if isinstance(text, str):
        notes["digest"] = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
        notes["present"] = bool(text.strip())
        notes["baselineReplaced"] = (
            next((line.strip() for line in text.splitlines() if line.strip()), None)
            == "<overwrite>"
        )
    profile = result.get("profileRead", {}).get("profile", {})
    profile.pop("notes", None)
    return result


def compose_role_instructions(
    baseline: str, baseline_digest: str, choice: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    text = choice.get("value")
    if not isinstance(text, str) or not text.strip():
        return baseline, {
            "kind": "compiled-role-operation-capsule",
            "semanticDigest": baseline_digest,
            "baselineDigest": baseline_digest,
            "baselineReplaced": False,
            "userTextDigest": None,
            "blocks": [{"kind": "baseline", "digest": baseline_digest}],
        }
    lines = text.splitlines(keepends=True)
    first = next(i for i, line in enumerate(lines) if line.strip())
    replaced = lines[first].strip() == "<overwrite>"
    delivered = "".join(lines[:first] + lines[first + 1 :]) if replaced else text
    if replaced and not delivered.strip():
        raise ValueError("notes contains <overwrite> without instructions")
    raw_digest = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    block = {
        "kind": "user replacement" if replaced else "user addition",
        "source": choice.get("source"),
        "key": choice.get("key"),
        "digest": raw_digest,
        "deliveredText": delivered,
    }
    blocks = [block] if replaced else [{"kind": "baseline", "digest": baseline_digest}, block]
    digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                {"baseline": baseline_digest, "blocks": blocks}, sort_keys=True, ensure_ascii=False
            ).encode()
        ).hexdigest()
    )
    label = f"User {'replacement' if replaced else 'addition'} from {choice.get('source')} ({choice.get('key')}). The user's text holds where it differs from the baseline."
    instructions = ("" if replaced else baseline + "\n\n") + label + "\n\n" + delivered
    return instructions, {
        "kind": "user-replaced-role-instructions" if replaced else "capsule-with-user-addition",
        "semanticDigest": digest,
        "baselineDigest": baseline_digest,
        "baselineReplaced": replaced,
        "userTextDigest": raw_digest,
        "blocks": blocks,
    }


# --------------------------------------------------------------------------------------
# Capability resources: the canonical text, its installed copy and its budget.
# --------------------------------------------------------------------------------------


class CapabilityRefused(Exception):
    """A capability text that cannot be delivered, with the reason a caller branches on."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class CapabilityResource:
    """One capability's canonical instruction text and the contract it must satisfy.

    ``canonical_path`` is repository-relative and exists only in a source checkout: it is the text
    an author edits and the source the generated copy is made from. ``packaged_path`` is
    package-relative, under ``package_data``, and is the only path a launch ever reads.
    """

    capability: str
    canonical_path: str
    packaged_path: str
    word_budget: int
    required_wording: tuple[str, ...]

    def __post_init__(self) -> None:
        for label, path in (
            ("canonical_path", self.canonical_path),
            ("packaged_path", self.packaged_path),
        ):
            candidate = PurePosixPath(path)
            if not path.strip() or candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError(f"capability {self.capability!r} needs a confined {label}")
        if not self.packaged_path.startswith("package_data/"):
            raise ValueError(
                f"capability {self.capability!r} must ship under package_data/, not "
                f"{self.packaged_path!r}: an installed build has no other tree"
            )
        if self.word_budget <= 0:
            raise ValueError(f"capability {self.capability!r} needs a finite positive word budget")
        if not self.required_wording or any(not one.strip() for one in self.required_wording):
            raise ValueError(f"capability {self.capability!r} must declare its required wording")


@dataclass(frozen=True, slots=True)
class CapabilityText:
    """The loaded capability text, its revision and the contract it was read against."""

    capability: str
    text: str
    digest: str
    revision: str
    words: int
    word_budget: int
    canonical_path: str
    packaged_path: str
    schema: str = CAPABILITY_RESOURCE_SCHEMA


#: Every capability whose instruction text this build ships. The registration is data, not prose:
#: one entry per capability, with the repository path an author edits and the package path a launch
#: reads. ``review-presenter`` is MIK-R105 rule 12's capability text.
CAPABILITY_RESOURCES: dict[str, CapabilityResource] = {
    "review-presenter": CapabilityResource(
        capability="review-presenter",
        canonical_path="mcp/src/agents_remember/canvas/review-presenter.md",
        packaged_path="package_data/canvas/review-presenter.md",
        word_budget=900,
        required_wording=(
            "You are not the Reviewer",
            "Keywords:",
            "You write only in your working folder.",
            "The side conversation belongs to this attempt",
        ),
    ),
}


def capability_resource(capability: str) -> CapabilityResource:
    """The registration for one capability, refusing an unregistered name by name."""

    try:
        return CAPABILITY_RESOURCES[capability]
    except KeyError as error:
        known = ", ".join(sorted(CAPABILITY_RESOURCES))
        raise CapabilityRefused(
            "capability-unknown",
            f"no capability resource is registered as {capability!r}; registered: {known}",
        ) from error


def _packaged_target(packaged_path: str, root: Path | None) -> Traversable | Path:
    """The installed copy of one capability text: package data, or ``root`` in a test."""

    parts = PurePosixPath(packaged_path).parts
    if root is not None:
        return Path(root).joinpath(*parts)
    return resources.files("agents_remember").joinpath(*parts)


def _word_count(text: str) -> int:
    return len([token for token in text.split() if token.strip()])


def load_capability_resource(capability: str, *, root: Path | None = None) -> CapabilityText:
    """Read the installed copy of one capability text, inside its finite contract.

    The read is the installed build's own: :mod:`importlib.resources` resolves the package's data,
    so a wheel, an sdist installation and a checkout all answer the same way, and the checkout's
    canonical source file is never opened. A missing, empty, oversized or reworded copy refuses
    with a typed reason rather than delivering a different assignment.
    """

    resource = capability_resource(capability)
    target = _packaged_target(resource.packaged_path, root)
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise CapabilityRefused(
            "capability-missing",
            f"the installed copy {resource.packaged_path!r} of {capability!r} cannot be read: "
            f"{error}. The generated copy is made by write_generated_capability_copy from "
            f"{resource.canonical_path!r}",
        ) from error
    if not text.strip():
        raise CapabilityRefused(
            "capability-empty",
            f"the installed copy {resource.packaged_path!r} of {capability!r} carries no text",
        )
    words = _word_count(text)
    if words > resource.word_budget:
        raise CapabilityRefused(
            "capability-too-long",
            f"the {capability!r} text holds {words} words, beyond its {resource.word_budget}-word "
            "budget",
        )
    missing = [marker for marker in resource.required_wording if marker not in text]
    if missing:
        raise CapabilityRefused(
            "capability-wording",
            f"the {capability!r} text no longer states: {missing}",
        )
    digest = hashlib.sha256(text.encode()).hexdigest()
    return CapabilityText(
        capability=capability,
        text=text,
        digest=digest,
        revision=f"sha256:{digest}",
        words=words,
        word_budget=resource.word_budget,
        canonical_path=resource.canonical_path,
        packaged_path=resource.packaged_path,
    )


def _path_under(root: Path, relative: str) -> Path:
    return Path(root).joinpath(*PurePosixPath(relative).parts)


def write_generated_capability_copy(
    capability: str, repository_root: Path, package_root: Path
) -> Path:
    """Produce the installed copy from the canonical source, byte for byte.

    ``repository_root`` is the source checkout the canonical text lives in; ``package_root`` is the
    ``agents_remember`` package directory the generated copy belongs under. This is the generator
    the equivalence test and a build step both call, so the checked-in copy is produced rather than
    hand-maintained.
    """

    resource = capability_resource(capability)
    source = _path_under(repository_root, resource.canonical_path)
    try:
        payload = source.read_bytes()
    except OSError as error:
        raise CapabilityRefused(
            "capability-source-missing",
            f"the canonical source {resource.canonical_path!r} of {capability!r} cannot be read: "
            f"{error}",
        ) from error
    if not payload.strip():
        raise CapabilityRefused(
            "capability-source-empty",
            f"the canonical source {resource.canonical_path!r} of {capability!r} carries no text",
        )
    target = _path_under(package_root, resource.packaged_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def capability_copy_drift(capability: str, repository_root: Path, package_root: Path) -> str | None:
    """``None`` when the installed copy is exactly the canonical text, else what differs."""

    resource = capability_resource(capability)
    source = _path_under(repository_root, resource.canonical_path)
    target = _path_under(package_root, resource.packaged_path)
    try:
        canonical = source.read_bytes()
        packaged = target.read_bytes()
    except OSError as error:
        return f"{capability}: {error}"
    if canonical == packaged:
        return None
    return (
        f"{capability}: {resource.packaged_path!r} is not the generated copy of "
        f"{resource.canonical_path!r}; run the generator again"
    )


def compose_capability_capsule(
    capability: str, choice: dict[str, Any], *, root: Path | None = None
) -> tuple[str, dict[str, Any]]:
    """One capability's instructions, with the provenance of what was actually delivered.

    ``choice`` is the same launch-note value a role capsule composes with: absent, a user addition,
    or ``<overwrite>`` plus a user replacement. The record names the applied kind and the canonical
    baseline's own path, revision and budget, so a reader can tell which text the seat received and
    where it came from.
    """

    loaded = load_capability_resource(capability, root=root)
    instructions, record = compose_role_instructions(loaded.text, loaded.revision, choice)
    if record["baselineReplaced"]:
        applied = CAPABILITY_REPLACEMENT
    elif record["userTextDigest"]:
        applied = CAPABILITY_ADDITION
    else:
        applied = CAPABILITY_BASELINE
    record["kind"] = {
        CAPABILITY_BASELINE: "compiled-capability-capsule",
        CAPABILITY_ADDITION: "capability-with-user-addition",
        CAPABILITY_REPLACEMENT: "user-replaced-capability",
    }[applied]
    user = next(
        (block for block in record["blocks"] if block.get("kind", "").startswith("user ")), None
    )
    record["capability"] = capability
    record["applied"] = applied
    record["provenance"] = {
        "schema": CAPABILITY_RESOURCE_SCHEMA,
        "applied": applied,
        "baseline": {
            "kind": "canonical baseline",
            "capability": capability,
            "canonicalPath": loaded.canonical_path,
            "packagedPath": loaded.packaged_path,
            "revision": loaded.revision,
            "words": loaded.words,
            "wordBudget": loaded.word_budget,
        },
        "user": None
        if user is None
        else {key: value for key, value in user.items() if key != "deliveredText"},
    }
    return instructions, record


__all__ = [
    "CAPABILITY_ADDITION",
    "CAPABILITY_BASELINE",
    "CAPABILITY_REPLACEMENT",
    "CAPABILITY_RESOURCES",
    "CAPABILITY_RESOURCE_SCHEMA",
    "CapabilityRefused",
    "CapabilityResource",
    "CapabilityText",
    "capability_copy_drift",
    "capability_resource",
    "compose_capability_capsule",
    "compose_role_instructions",
    "instruction_metadata",
    "launch_choice_metadata",
    "load_capability_resource",
    "write_generated_capability_copy",
]
