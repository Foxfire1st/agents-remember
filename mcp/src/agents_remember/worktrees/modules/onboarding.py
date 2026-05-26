from __future__ import annotations

import hashlib
import re
from pathlib import Path

from agents_remember.kernel import coordination_context_resolver as resolver
from agents_remember.kernel import filesystem
from agents_remember.worktrees.modules.context import contract_context
from agents_remember.worktrees.modules.git import require_git
from agents_remember.worktrees.worktree_contract import WorktreeContract

ENTITY_FINGERPRINT_ALGORITHM = "git-blob-set-v1"


def onboarding_metadata_row(
    text: str, field: str, value: str, *, code: bool = False
) -> tuple[str, bool]:
    rendered = f"`{value}`" if code else value
    pattern = re.compile(rf"(\|\s*{re.escape(field)}\s*\|\s*)`?[^|`]*`?(\s*\|)")
    updated, count = pattern.subn(rf"\g<1>{rendered}\g<2>", text, count=1)
    return updated, count > 0


def sidecar_onboarding_path(onboarding_root: Path, source_path: str) -> Path:
    return onboarding_root / f"{source_path}.md"


def onboarding_refresh_plan_for_context(context, changed_paths: list[str]) -> dict[str, object]:
    required: list[dict[str, str]] = []
    missing: list[str] = []
    unsupported: list[str] = []
    for source_path in changed_paths:
        storage = resolver.resolve_storage_for_source(
            source_path, context.storage, context.code_repository_name
        )
        if storage == "disabled":
            continue
        if not resolver.sidecar_storage_label(storage):
            unsupported.append(source_path)
            continue
        onboarding_path = sidecar_onboarding_path(context.onboarding_root, source_path)
        if not filesystem.exists(onboarding_path):
            missing.append(source_path)
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
    }


def markdown_table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def normalized_table_cell(cell: str) -> str:
    return re.sub(r"[^a-z0-9]", "", cell.lower())


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


def _fingerprint_row(line: str, index: int, header: dict[str, int]) -> dict[str, object] | None:
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


def parse_entity_fingerprint_rows(catalog_path: Path) -> list[dict[str, object]]:
    if not filesystem.exists(catalog_path):
        return []
    lines = filesystem.read_text(catalog_path, encoding="utf-8").splitlines()
    table = _fingerprint_table_header(lines)
    if table is None:
        return []
    header, start_index = table

    rows: list[dict[str, object]] = []
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
) -> dict[str, object]:
    changed = set(changed_paths)
    catalog_path = context.onboarding_root / "entities.md"
    required: list[dict[str, object]] = []
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
            f"unsupported rows: {details}. Run C-05 create-or-update-onboarding-files, then rerun closeout."
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
                "Run C-05 create-or-update-onboarding-files, then rerun closeout."
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
    contract: WorktreeContract, changed_paths: list[str]
) -> dict[str, object]:
    return onboarding_refresh_plan_for_context(contract_context(contract), changed_paths)


def entity_fingerprint_refresh_plan(
    contract: WorktreeContract, changed_paths: list[str]
) -> dict[str, object]:
    return entity_fingerprint_refresh_plan_for_context(contract_context(contract), changed_paths)


def validate_onboarding_refresh_plan_for_context(
    context, changed_paths: list[str]
) -> dict[str, object]:
    plan = onboarding_refresh_plan_for_context(context, changed_paths)
    missing = plan["missing"]
    unsupported = plan["unsupported"]
    if missing or unsupported:
        details: list[str] = []
        if missing:
            details.append(f"missing sidecar onboarding for: {', '.join(missing)}")
        if unsupported:
            details.append(f"unsupported onboarding storage for: {', '.join(unsupported)}")
        raise RuntimeError(
            "external-memory closeout requires current onboarding for changed source files before memory commit; "
            + "; ".join(details)
            + ". Run C-05 create-or-update-onboarding-files, then rerun closeout."
        )
    for item in plan["required"]:
        onboarding_path = Path(item["onboarding_file"])
        text = filesystem.read_text(onboarding_path, encoding="utf-8")
        if "lastVerifiedCommitHash" not in text or "lastVerifiedCommitDate" not in text:
            raise RuntimeError(
                "external-memory closeout requires onboarding verification metadata before memory commit; "
                f"{onboarding_path.as_posix()} is missing lastVerifiedCommitHash or lastVerifiedCommitDate. "
                "Run C-05 create-or-update-onboarding-files, then rerun closeout."
            )
    return plan


def validate_onboarding_refresh_plan(
    contract: WorktreeContract, changed_paths: list[str]
) -> dict[str, object]:
    return validate_onboarding_refresh_plan_for_context(contract_context(contract), changed_paths)


def refresh_onboarding_metadata_for_context(
    context,
    changed_paths: list[str],
    verified_commit: str,
    verified_date: str,
) -> list[dict[str, str]]:
    plan = validate_onboarding_refresh_plan_for_context(context, changed_paths)
    refreshed: list[dict[str, str]] = []
    for item in plan["required"]:
        onboarding_path = Path(item["onboarding_file"])
        text = filesystem.read_text(onboarding_path, encoding="utf-8")
        text, hash_found = onboarding_metadata_row(
            text, "lastVerifiedCommitHash", verified_commit, code=True
        )
        text, date_found = onboarding_metadata_row(text, "lastVerifiedCommitDate", verified_date)
        if not hash_found or not date_found:
            raise RuntimeError(
                "external-memory closeout requires onboarding verification metadata before memory commit; "
                f"{onboarding_path.as_posix()} is missing lastVerifiedCommitHash or lastVerifiedCommitDate. "
                "Run C-05 create-or-update-onboarding-files, then rerun closeout."
            )
        filesystem.write_text(onboarding_path, text, encoding="utf-8")
        refreshed.append(item)
    return refreshed


def refresh_onboarding_metadata(
    contract: WorktreeContract,
    changed_paths: list[str],
    verified_commit: str,
    verified_date: str,
) -> list[dict[str, str]]:
    return refresh_onboarding_metadata_for_context(
        contract_context(contract), changed_paths, verified_commit, verified_date
    )
