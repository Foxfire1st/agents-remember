from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.kernel import coordination_context_resolver as resolver
from agents_remember.kernel import filesystem
from agents_remember.kernel.onboarding_doc import (
    markdown_table_cells,
)
from agents_remember.kernel.route_index import build_route_indexes
from agents_remember.worktrees.modules.context import contract_context
from agents_remember.worktrees.modules.git import (
    require_git,
)
from agents_remember.worktrees.modules.models import (
    EntityFingerprintRefreshPlan,
    EntityFingerprintRequiredItem,
    EntityFingerprintRow,
    OnboardingRefreshPlan,
)
from agents_remember.worktrees.modules.onboarding_trace import (
    OnboardingTraceResult,
    OnboardingTraceSides,
    onboarding_trace_result,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

ENTITY_FINGERPRINT_ALGORITHM = "git-blob-set-v1"


def sidecar_onboarding_path(onboarding_root: Path, source_path: str) -> Path:
    return onboarding_root / f"{source_path}.md"


def onboarding_refresh_plan_for_context(
    context, changed_paths: list[str], *, working_paths: list[str] | None = None
) -> OnboardingRefreshPlan:
    """Plan sidecar refreshes for changed sources, split by responsibility tier.

    ``working_paths`` names the paths changed in the working tree; paths outside
    it arrived via commits (merges, pre-committed slices). Working paths without
    onboarding block closeout (``missing``/``unsupported``); committed-range
    paths without onboarding are collected as ``unonboarded`` and never block,
    so transported history cannot force whole-repository onboarding. ``None``
    keeps the strict legacy semantics: every changed path is treated as working.
    """
    working = set(changed_paths if working_paths is None else working_paths)
    required: list[dict[str, str]] = []
    missing: list[str] = []
    unsupported: list[str] = []
    unonboarded: list[str] = []
    for source_path in changed_paths:
        storage = resolver.resolve_storage_for_source(
            source_path, context.storage, context.code_repository_name
        )
        if storage == "disabled":
            continue
        if not resolver.is_sidecar_storage(storage):
            (unsupported if source_path in working else unonboarded).append(source_path)
            continue
        onboarding_path = sidecar_onboarding_path(context.onboarding_root, source_path)
        if not filesystem.exists(onboarding_path):
            (missing if source_path in working else unonboarded).append(source_path)
            continue
        required.append(
            {
                "source_path": source_path,
                "onboarding_file": onboarding_path.as_posix(),
            }
        )
    return {
        "required": required,
        "missing": missing,
        "unsupported": unsupported,
        "unonboarded": unonboarded,
    }


def normalized_table_cell(cell: str) -> str:
    return re.sub(r"[^a-z0-9]", "", cell.lower())


def refresh_route_indexes_for_context(context) -> dict[str, Any]:
    result = build_route_indexes(
        code_root=context.code_repository_root,
        onboarding_root=context.onboarding_root,
        repository=context.code_repository_name,
        storage=context.storage,
        dry_run=False,
    )
    return result.to_dict()


def route_index_refresh_plan_for_context(context) -> dict[str, Any]:
    result = build_route_indexes(
        code_root=context.code_repository_root,
        onboarding_root=context.onboarding_root,
        repository=context.code_repository_name,
        storage=context.storage,
        dry_run=True,
    )
    return result.to_dict()


def _fingerprint_table_header(lines: list[str]) -> tuple[dict[str, int], int] | None:
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        cells = markdown_table_cells(line)
        normalized = {normalized_table_cell(cell): position for position, cell in enumerate(cells)}
        required = {"entity", "algorithm", "fingerprint", "evidencepaths"}
        if required.issubset(normalized):
            return {key: normalized[key] for key in required}, index + 2
    return None


def _fingerprint_row(line: str, index: int, header: dict[str, int]) -> EntityFingerprintRow | None:
    if not line.lstrip().startswith("|"):
        return None
    cells = markdown_table_cells(line)
    if len(cells) <= max(header.values()):
        return None
    algorithm = cells[header["algorithm"]].strip("`")
    evidence_cell = cells[header["evidencepaths"]]
    return {
        "line_index": index,
        "entity": cells[header["entity"]].strip("`"),
        "algorithm": algorithm,
        "fingerprint": cells[header["fingerprint"]].strip("`"),
        "evidence_paths": re.findall(r"`([^`]+)`", evidence_cell),
    }


def parse_entity_fingerprint_rows(catalog_path: Path) -> list[EntityFingerprintRow]:
    if not filesystem.exists(catalog_path):
        return []
    lines = filesystem.read_text(catalog_path, encoding="utf-8").splitlines()
    table = _fingerprint_table_header(lines)
    if table is None:
        return []
    header, start_index = table

    rows: list[EntityFingerprintRow] = []
    for index in range(start_index, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            break
        row = _fingerprint_row(line, index, header)
        if row is not None:
            rows.append(row)
    return rows


def entity_fingerprint_refresh_plan_for_context(
    context, changed_paths: list[str]
) -> EntityFingerprintRefreshPlan:
    changed = set(changed_paths)
    catalog_path = context.onboarding_root / "entities.md"
    required: list[EntityFingerprintRequiredItem] = []
    unsupported: list[dict[str, str]] = []
    for row in parse_entity_fingerprint_rows(catalog_path):
        evidence_paths = list(row["evidence_paths"])
        affected_paths = sorted(changed.intersection(evidence_paths))
        if not affected_paths:
            continue
        entity = str(row["entity"])
        if row["algorithm"] != ENTITY_FINGERPRINT_ALGORITHM:
            unsupported.append(
                {
                    "entity": entity,
                    "algorithm": str(row["algorithm"]),
                }
            )
            continue
        required.append(
            {
                "entity": entity,
                "onboarding_file": catalog_path.as_posix(),
                "evidence_paths": evidence_paths,
                "affected_paths": affected_paths,
            }
        )
    return {
        "required": required,
        "unsupported": unsupported,
    }


def compute_git_blob_set_fingerprint(repo_root: Path, evidence_paths: list[str]) -> str:
    lines: list[str] = []
    for source_path in sorted(evidence_paths):
        blob_hash = require_git(repo_root, ["rev-parse", f"HEAD:{source_path}"])
        lines.append(f"{source_path}\0{blob_hash}\n")
    digest = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def refresh_entity_fingerprints_for_context(
    context, changed_paths: list[str]
) -> list[dict[str, object]]:
    plan = entity_fingerprint_refresh_plan_for_context(context, changed_paths)
    unsupported = plan["unsupported"]
    if unsupported:
        details = ", ".join(f"{item['entity']} ({item['algorithm']})" for item in unsupported)
        raise RuntimeError(
            "external-memory closeout requires supported entity fingerprint rows before memory commit; "
            f"unsupported rows: {details}. Run the c-05-create-or-update-onboarding-files skill, then rerun closeout."
        )
    required = list(plan["required"])
    if not required:
        return []

    catalog_path = Path(required[0]["onboarding_file"])
    lines = filesystem.read_text(catalog_path, encoding="utf-8").splitlines()
    refreshed: list[dict[str, object]] = []
    rows_by_entity = {
        str(row["entity"]): row for row in parse_entity_fingerprint_rows(catalog_path)
    }
    for item in required:
        entity = str(item["entity"])
        row = rows_by_entity[entity]
        fingerprint = compute_git_blob_set_fingerprint(
            context.code_repository_root, list(item["evidence_paths"])
        )
        line_index = int(row["line_index"])
        old_fingerprint = str(row["fingerprint"])
        if old_fingerprint:
            lines[line_index] = lines[line_index].replace(old_fingerprint, fingerprint, 1)
        else:
            raise RuntimeError(
                "external-memory closeout requires entity fingerprint values before memory commit; "
                f"{catalog_path.as_posix()} row {entity!r} is missing a fingerprint. "
                "Run the c-05-create-or-update-onboarding-files skill, then rerun closeout."
            )
        refreshed.append(
            {
                "entity": entity,
                "onboarding_file": catalog_path.as_posix(),
                "fingerprint": fingerprint,
                "affected_paths": item["affected_paths"],
            }
        )
    filesystem.write_text(catalog_path, "\n".join(lines) + "\n", encoding="utf-8")
    return refreshed


def onboarding_refresh_plan(
    contract: WorktreeContract,
    changed_paths: list[str],
    *,
    working_paths: list[str] | None = None,
) -> OnboardingRefreshPlan:
    return onboarding_refresh_plan_for_context(
        contract_context(contract), changed_paths, working_paths=working_paths
    )


def entity_fingerprint_refresh_plan(
    contract: WorktreeContract, changed_paths: list[str]
) -> EntityFingerprintRefreshPlan:
    return entity_fingerprint_refresh_plan_for_context(contract_context(contract), changed_paths)


def _require_onboarded_sources(
    context, changed_paths: list[str], working_paths: list[str] | None
) -> OnboardingRefreshPlan:
    """Today's missing/unsupported sidecar refusal, shared by both gates unchanged."""
    plan = onboarding_refresh_plan_for_context(context, changed_paths, working_paths=working_paths)
    details: list[str] = []
    if plan["missing"]:
        details.append(f"missing sidecar onboarding for: {', '.join(plan['missing'])}")
    if plan["unsupported"]:
        details.append(f"unsupported onboarding storage for: {', '.join(plan['unsupported'])}")
    if details:
        raise RuntimeError(
            "external-memory closeout requires current onboarding for changed source files before memory commit; "
            + "; ".join(details)
            + ". Run the c-05-create-or-update-onboarding-files skill, then rerun closeout."
        )
    return plan


@dataclass(frozen=True)
class OnboardingTraceInputs:
    """What both gate entries read about a change, beside the memory sides (MIK-R53, MIK-R177).

    ``code_blobs`` maps every changed source path to its blob at the code candidate (``None`` for a
    deleted file). ``identical_changes`` is the leaf's worklist run's ``identicalChanges``: the gate
    groups identical copies exactly as ``worklist_onboarding`` does, so an item a row answers in the
    worklist is the item the gate raises. It carries no default, so no caller judges ungrouped
    items where the worklist grouped identical copies. ``working_paths`` names the changed paths
    that are uncommitted, which the missing-sidecar refusal reads; ``None`` keeps its strict legacy
    meaning, every changed path treated as working.
    """

    code_blobs: Mapping[str, str | None]
    identical_changes: Mapping[str, Sequence[str]]
    working_paths: list[str] | None = None


class IdenticalChangesUnreadable(ValueError):
    """A worklist run's ``identicalChanges`` is not a group map (MIK-R177)."""


def identical_changes_from_run(document: Mapping[str, Any]) -> dict[str, list[str]]:
    """The run's ``identicalChanges`` as ordered source lists, or a named refusal (MIK-R177).

    The one reader both gate entries use. A complete run's groups are
    ``{representative source path: [source path, ...]}``; an entry that is not a list of non-empty
    source paths is a malformed grouping input and is refused by name. Nothing is dropped, coerced
    or replaced by a singleton, so a malformed group can never shorten itself into an ungrouped
    item. A genuine empty mapping stays empty, and each group keeps the run's order.
    """

    groups = document.get("identicalChanges")
    if not isinstance(groups, Mapping):
        raise IdenticalChangesUnreadable("the run names no identicalChanges mapping")
    parsed: dict[str, list[str]] = {}
    for representative, members in groups.items():
        if not isinstance(representative, str) or not representative:
            raise IdenticalChangesUnreadable(
                f"an identicalChanges key is not a source path: {representative!r}"
            )
        if not isinstance(members, (list, tuple)):
            raise IdenticalChangesUnreadable(
                f"identicalChanges[{representative!r}] is not a list of source paths: {members!r}"
            )
        if not all(isinstance(one, str) and one for one in members):
            raise IdenticalChangesUnreadable(
                f"identicalChanges[{representative!r}] holds a member that is not a source path: "
                f"{list(members)!r}"
            )
        if not members:
            raise IdenticalChangesUnreadable(
                f"identicalChanges[{representative!r}] names no member"
            )
        if representative not in members:
            raise IdenticalChangesUnreadable(
                f"identicalChanges[{representative!r}] does not name its representative among its "
                f"members: {list(members)!r}"
            )
        parsed[representative] = list(members)
    return parsed


def onboarding_trace_gate_for_context(
    context,
    changed_paths: list[str],
    sides: OnboardingTraceSides,
    inputs: OnboardingTraceInputs,
) -> tuple[OnboardingTraceResult, list[dict[str, str]]]:
    """MIK-R30 on a converted tree, for the curator's memory-quality run: never raises.

    Returns the gate's result and its repair findings: one per missing trace (rule 6), one per
    unreadable input, and one for changed sources without onboarding (today's refusal, unchanged).
    ``inputs`` carries the candidate's blobs and the worklist's identical-copy groups (MIK-R177).
    """
    result = onboarding_trace_result(
        context,
        changed_paths,
        sides,
        code_blobs=inputs.code_blobs,
        source_groups=inputs.identical_changes,
    )
    findings = result.repair_findings()
    try:
        _require_onboarded_sources(context, changed_paths, inputs.working_paths)
    except RuntimeError as error:
        findings.insert(
            0,
            {
                "check": "memory-refresh-attestations",
                "code": "memory-refresh-attestation-failed",
                "path": "",
                "message": f"sidecar onboarding: {error}",
            },
        )
    return result, findings
