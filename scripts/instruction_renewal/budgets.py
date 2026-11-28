"""Measure instruction sources and compiled capsules without changing authored policy."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import cast

from agents_remember.application.role_capsules.sources import (
    CapsuleAdmissionRequest,
    admit_capsule_sources,
)
from agents_remember.application.role_capsules.wire_budget import (
    COMPILED_CAPSULE_TOKEN_CEILINGS,
    evaluate_wire_token_ceilings,
    measure_compiled_capsule_tokens,
)
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.errors import AuthorityError
from agents_remember.models.role_capsules.compiler import compile_role_capsule
from agents_remember.models.role_capsules.manifest import (
    CapsuleCompositionManifest,
    parse_composition_manifest,
)
from agents_remember.models.role_capsules.manifest_derived import compose_manifest
from agents_remember.models.role_capsules.sources import CapsuleSource
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleLauncherSeat,
    CapsuleOperation,
    CapsuleRole,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS

LIFECYCLE = "skills/l-01-agent-lifecycles"
RECORD = f"{LIFECYCLE}/capsule-budgets-current.json"
_REGISTRIES = {"capsule-budgets.json", "capsule-budgets-d694.json", "capsule-budgets-current.json"}


def _read(root: Path, path: str, overrides: Mapping[str, bytes]) -> bytes:
    return overrides[path] if path in overrides else (root / path).read_bytes()


def _request(
    root: Path, manifest: bytes, parsed: CapsuleCompositionManifest, role: str, operation: str
) -> CapsuleAdmissionRequest:
    if role != "launcher":
        return routed_admission_for(
            root,
            "composition-manifest.json",
            manifest,
            CapsuleSeatAddress(
                role=cast(CapsuleRole, role), operation=cast(CapsuleOperation, operation)
            ),
        )
    return CapsuleAdmissionRequest(
        root=root,
        manifest="composition-manifest.json",
        core=tuple(parsed.core[name].source for name in parsed.launcher.core),
        operation=(parsed.operations[operation].source,),
        skill=tuple(entry.source for entry in parsed.skills.values()),
    )


def _binding(parsed: CapsuleCompositionManifest, role: str, operation: str) -> CapsuleBinding:
    seat = (
        CapsuleLauncherSeat(routing_condition=parsed.launcher.routing_condition)
        if role == "launcher"
        else CapsuleRoleSeat(role=cast(CapsuleRole, role), altitude=parsed.roles[role].altitude)
    )
    return CapsuleBinding(
        operation=cast(CapsuleOperation, operation),
        admitted=CapsuleAdmittedFacts(
            task_reference="agents-remember/tasks/demo/task.json",
            task_document_digest=compute_content_digest(b'{"id": "demo"}'),
            seat=seat,
            repository_id="agents-remember",
            work_branch="fixture",
            tool_policy=CapsuleToolPolicy(granted=frozenset(PUBLIC_TOOLS)),
        ),
    )


def _matrix(
    root: Path, overrides: Mapping[str, bytes]
) -> tuple[dict[str, int], dict[str, int], dict[str, str]]:
    lifecycle = root / LIFECYCLE
    manifest = _read(root, f"{LIFECYCLE}/composition-manifest.json", overrides)
    parsed = parse_composition_manifest(manifest)
    every_role_path = f"{LIFECYCLE}/core/every-role.md"
    every_role = (
        _read(root, every_role_path, overrides).decode("utf-8")
        if every_role_path in overrides or (root / every_role_path).is_file()
        else ""
    )
    keys: list[tuple[str, str]] = [
        (role, operation)
        for role in parsed.role_order
        for operation, entry in parsed.operations.items()
        if role in entry.applies_to_roles
    ]
    keys.extend(("launcher", operation) for operation in parsed.launcher.operations)
    sizes: dict[str, int] = {}
    compiled_tokens: dict[str, int] = {}
    revisions: dict[str, str] = {}
    for role, operation in keys:
        request = _request(lifecycle, manifest, parsed, role, operation)
        sources = _sources(request, overrides)
        result = compile_role_capsule(
            _binding(parsed, role, operation), manifest, sources, request.as_selection()
        )
        key = f"{role}/{operation}"
        sizes[key] = len(result.render_instructions().split())
        compiled_tokens[key] = measure_compiled_capsule_tokens(
            result, corpus_root=lifecycle, every_role=every_role
        )
        revisions.update({source.path: source.revision for source in sources})
    return sizes, compiled_tokens, dict(sorted(revisions.items()))


def _sources(
    request: CapsuleAdmissionRequest, overrides: Mapping[str, bytes]
) -> tuple[CapsuleSource, ...]:
    sources = []
    for source in admit_capsule_sources(request):
        updated = source
        path = f"{LIFECYCLE}/{source.path}"
        if path in overrides:
            content = overrides[path]
            updated = replace(source, content=content, revision=compute_content_digest(content))
        sources.append(updated)
    return tuple(sources)


def _measure_with_tokens(root: Path, overrides: Mapping[str, bytes]) -> tuple[dict, dict[str, int]]:
    raw = _read(root, f"{LIFECYCLE}/composition-manifest.json", overrides)
    references = compose_manifest(json.loads(raw))["references"]
    details = sorted(
        {entry["source"] for entry in references.values() if entry.get("step_detail") is True}
    )
    ordinary = sorted(
        {
            entry["source"]
            for entry in references.values()
            if entry.get("step_detail") is False
            and entry["source"].startswith("templates/")
            and entry["source"].endswith(".md")
        }
    )
    capsules, compiled_tokens, revisions = _matrix(root, overrides)
    sources = {
        path.relative_to(root).as_posix()
        for path in (root / LIFECYCLE).rglob("*")
        if path.is_file() and path.suffix in {".md", ".json"} and path.name not in _REGISTRIES
    }
    sources.update(
        path
        for path in overrides
        if path.startswith(LIFECYCLE + "/")
        and Path(path).suffix in {".md", ".json"}
        and Path(path).name not in _REGISTRIES
    )
    source_sizes = {}
    for path in sorted(sources):
        content = _read(root, path, overrides)
        source_sizes[path] = {
            "size_words": len(content.decode("utf-8").split()),
            "sha256": sha256(content).hexdigest(),
        }
    return {
        "current_measurements": {
            "capsules": capsules,
            "step_details": {
                path: source_sizes[f"{LIFECYCLE}/{path}"]["size_words"] for path in details
            },
            "digest": {
                "core/every-role.md": source_sizes[f"{LIFECYCLE}/core/every-role.md"]["size_words"]
            },
            "ordinary_templates": {
                path: source_sizes[f"{LIFECYCLE}/{path}"]["size_words"] for path in ordinary
            },
        },
        "current_detail_population": details,
        "current_linked_template_population": ordinary,
        "approved_source_sizes": source_sizes,
        "compiler_input_revisions": revisions,
    }, compiled_tokens


def _measure(root: Path, overrides: Mapping[str, bytes]) -> dict:
    return _measure_with_tokens(root, overrides)[0]


def _size_notices(before: dict, after: dict) -> list[str]:
    notices = []
    current = {
        **after["current_measurements"],
        "sources": {key: row["size_words"] for key, row in after["approved_source_sizes"].items()},
    }
    old = {
        **before["current_measurements"],
        "sources": {key: row["size_words"] for key, row in before["approved_source_sizes"].items()},
    }
    for area, entries in current.items():
        previous = old[area]
        for key, size in entries.items():
            if key not in previous:
                notices.append(f"{area}/{key}: measured {size} words (new)")
            elif size != previous[key]:
                difference = size - previous[key]
                change = "grew" if difference > 0 else "shrank"
                notices.append(
                    f"{area}/{key}: {change} by {abs(difference)} words ({previous[key]} -> {size})"
                )
        for key in previous.keys() - entries.keys():
            notices.append(f"{area}/{key}: removed measurement ({previous[key]} words)")
    return notices


def plan(root: Path, overrides: Mapping[str, bytes] | None = None) -> tuple[dict, list[str]]:
    """Return all current measured facts and visible size deltas for the planned bytes."""
    changes = {} if overrides is None else overrides
    record = json.loads((root / RECORD).read_bytes())
    measured, compiled_tokens = _measure_with_tokens(root, changes)
    _check_ceilings(compiled_tokens)
    return measured, _size_notices(record["derived"], measured)


def _check_ceilings(compiled_tokens: Mapping[str, int]) -> None:
    findings = evaluate_wire_token_ceilings(compiled_tokens, COMPILED_CAPSULE_TOKEN_CEILINGS)
    missing = sorted(COMPILED_CAPSULE_TOKEN_CEILINGS.keys() - compiled_tokens.keys())
    findings.extend(f"{key}: compiled capsule missing token measurement" for key in missing)
    if findings:
        raise AuthorityError(
            "\n".join(
                f"{finding}; raising a ceiling is the developer's decision" for finding in findings
            )
        )
