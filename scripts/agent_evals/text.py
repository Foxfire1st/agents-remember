"""Measure actual compiled Curator text and directly delivered details, without editing either."""

from __future__ import annotations

from pathlib import Path

from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.cli.role_brief_message import SHARED_BLOCK_HEADING
from agents_remember.errors import AuthorityError
from agents_remember.models.role_capsules.manifest import parse_composition_manifest
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleCompilationResult,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.models.role_capsules.vocabulary import capsule_operation_or_none
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.serving.capsule_delivery import capsule_delivery_from

from .artifacts import json_digest, sha256_file


def _captured_origins(
    result: CapsuleCompilationResult, shared: str, shared_block: str | None
) -> list[dict]:
    """Retain the compiled source spans and the admitted shared digest's own source."""
    origins = []
    if shared:
        sources = [
            source for source in result.manifest.sources if source.identity == "core:every-role"
        ]
        if (
            len(sources) != 1
            or shared_block is None
            or sources[0].revision != compute_content_digest(shared_block.encode())
        ):
            raise AuthorityError(
                "captured shared instruction prefix has ambiguous source provenance"
            )
        origins.append({"sourcePath": sources[0].path, "start": 0, "end": len(shared)})
    origins.extend(
        {
            "sourcePath": origin.source_path,
            "start": len(shared) + origin.start,
            "end": len(shared) + origin.end,
        }
        for origin in capsule_delivery_from(result).instruction_origins
    )
    return origins


def capture_curator_text(code: Path) -> dict:
    root = code / "skills/l-01-agent-lifecycles"
    manifest = "composition-manifest.json"
    raw = (root / manifest).read_bytes()
    parsed = parse_composition_manifest(raw)
    entries = {}
    for name, description in parsed.operations.items():
        if "curator" not in description.applies_to_roles:
            continue
        operation = capsule_operation_or_none(name)
        if operation is None:
            raise AuthorityError(f"unknown Curator operation in composition manifest: {name}")
        address = CapsuleSeatAddress(role="curator", operation=operation)
        request = routed_admission_for(root, manifest, raw, address)
        binding = CapsuleBinding(
            operation=operation,
            admitted=CapsuleAdmittedFacts(
                task_reference="agents-remember/eval-fixture/task.json",
                task_document_digest=compute_content_digest(b'{"id":"eval-fixture"}'),
                seat=CapsuleRoleSeat(role="curator", altitude=parsed.roles["curator"].altitude),
                repository_id="agents-remember",
                work_branch="eval-fixture",
                tool_policy=CapsuleToolPolicy(granted=frozenset(PUBLIC_TOOLS)),
            ),
        )
        outcome = compile_admitted_capsule(binding, request)
        if not outcome.ok or outcome.result is None:
            raise AuthorityError(outcome.render_explanation())
        result = outcome.result
        shared = (
            f"## {SHARED_BLOCK_HEADING}\n\n{outcome.shared_block}\n\n"
            if outcome.shared_block
            else ""
        )
        entries[operation] = {
            "instructions": shared + result.render_instructions(),
            "instructionOrigins": _captured_origins(result, shared, outcome.shared_block),
            "semanticDigest": str(result.semantic_digest),
            "details": [
                {
                    "path": detail.source_path,
                    "step": detail.step,
                    "revision": str(detail.revision),
                    "content": detail.content.decode("utf-8"),
                }
                for detail in result.capsule.detail_references
            ],
        }
    return {
        "schema": "ar-curator-effective-text/v1",
        "manifestSha256": sha256_file(root / manifest),
        "signature": json_digest(entries),
        "entries": entries,
        "taskBinding": "Fixed synthetic binding excluded from rendered instruction text; actual case bindings are separate evidence",
    }
