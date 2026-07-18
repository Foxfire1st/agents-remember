from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from agents_remember.kernel import coordination_context_resolver as resolver
from agents_remember.kernel import filesystem
from agents_remember.kernel.onboarding_doc import (
    ROUTE_OVERVIEW_DOC_TYPES,
    has_no_impact_marker,
    markdown_table_cells,
    meaningful_body_changed,
    new_history_lines,
    normalize_route,
    onboarding_metadata_row,
    route_contains_changed_path,
    table_metadata,
)
from agents_remember.kernel.route_index import build_route_indexes
from agents_remember.worktrees.modules.context import contract_context
from agents_remember.worktrees.modules.git import (
    changed_worktree_paths,
    commit_text_or_none,
    committed_changed_paths,
    require_git,
)
from agents_remember.worktrees.modules.models import (
    PATH_SAMPLE_LIMIT,
    EntityFingerprintRefreshPlan,
    EntityFingerprintRequiredItem,
    EntityFingerprintRow,
    OnboardingRefreshPlan,
    RouteOverviewBodyClassification,
    RouteOverviewRefreshPlan,
    SidecarBodyClassification,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

ENTITY_FINGERPRINT_ALGORITHM = "git-blob-set-v1"


def sidecar_onboarding_path(onboarding_root: Path, source_path: str) -> Path:
    return onboarding_root / f"{source_path}.md"


def contract_memory_verified_commit(contract: WorktreeContract) -> str:
    """The last memory commit closeout verified against, for body-gate baselines."""
    return contract.ledger_commit or contract.memory_content_commit or contract.memory_base_commit


def _changed_memory_paths(memory_root: Path, memory_verified_commit: str) -> set[str]:
    """Memory-tree paths changed since the last verified memory commit.

    Dirty paths plus the committed range, so sidecar work committed in the
    memory worktree before closeout still counts as updated this task.
    """
    changed = set(changed_worktree_paths(memory_root))
    if memory_verified_commit:
        changed.update(
            committed_changed_paths(memory_root, memory_verified_commit, memory_verified_commit)
        )
    return changed


def _joined_sample(paths: list[str]) -> str:
    """Comma-join capped at PATH_SAMPLE_LIMIT so gate errors never flood payloads."""
    extra = len(paths) - PATH_SAMPLE_LIMIT
    joined = ", ".join(paths[:PATH_SAMPLE_LIMIT])
    return f"{joined}, ... (+{extra} more)" if extra > 0 else joined


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


def route_overview_metadata_refresh_plan_for_context(
    context, changed_paths: list[str]
) -> RouteOverviewRefreshPlan:
    required: list[dict[str, str]] = []
    missing_metadata: list[str] = []
    for overview_path in sorted(context.onboarding_root.rglob("overview.md")):
        if not filesystem.is_file(overview_path):
            continue
        metadata = table_metadata(overview_path)
        if metadata.get("doc_type", "").strip("`") not in ROUTE_OVERVIEW_DOC_TYPES:
            continue
        source_route = normalize_route(metadata.get("sourceRoute", "."))
        if not route_contains_changed_path(source_route, changed_paths):
            continue
        rel_path = overview_path.relative_to(context.onboarding_root).as_posix()
        if "lastVerifiedCommitHash" not in metadata or "lastVerifiedCommitDate" not in metadata:
            missing_metadata.append(rel_path)
            continue
        required.append(
            {
                "source_route": source_route,
                "onboarding_file": overview_path.as_posix(),
            }
        )
    return {
        "required": required,
        "missing_metadata": missing_metadata,
    }


def _nearest_governing_route(source_path: str, routes: list[str]) -> str | None:
    """The longest route among routes that covers source_path; '.' covers all."""
    candidates = [
        route
        for route in routes
        if route in {".", source_path} or source_path.startswith(f"{route}/")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda route: (route != ".", len(route), route))


def classify_route_overview_updates(
    context,
    plan: RouteOverviewRefreshPlan,
    changed_paths: list[str],
    *,
    memory_tree: Path | None = None,
    memory_verified_commit: str = "",
) -> RouteOverviewBodyClassification:
    """Classify matched route overviews by meaningful body and history changes.

    Only overviews that are the **nearest governor** of at least one changed
    source path are domain-evident and classified like sidecars (``stale`` /
    ``untraced`` / ``attested_no_impact``, with ``No route impact:`` as the
    marker). Overviews matched only as ancestors — including the repo-root
    overview matched by happenstance — are reported under
    ``stamped_without_body_review`` when their body was not reviewed this
    cycle, and never gate closeout.
    """
    classification: RouteOverviewBodyClassification = {
        "stale": [],
        "untraced": [],
        "attested_no_impact": [],
        "stamped_without_body_review": [],
    }
    required = plan["required"]
    if not required:
        return classification
    tree = memory_tree if memory_tree is not None else getattr(context, "memory_root", None)
    if tree is None:
        return classification
    memory_root = Path(tree).resolve()
    baseline_ref = memory_verified_commit or "HEAD"
    changed_memory = _changed_memory_paths(memory_root, memory_verified_commit)
    routes = [normalize_route(item["source_route"]) for item in required]
    domain_routes = {
        route
        for route in (_nearest_governing_route(path, routes) for path in changed_paths)
        if route is not None
    }
    for item in required:
        route = normalize_route(item["source_route"])
        overview_path = Path(item["onboarding_file"]).resolve()
        try:
            relative = overview_path.relative_to(memory_root).as_posix()
        except ValueError:
            continue
        baseline_text = commit_text_or_none(memory_root, baseline_ref, relative)
        if baseline_text is None:
            continue
        current = filesystem.read_text(overview_path, encoding="utf-8")
        body_changed = relative in changed_memory and meaningful_body_changed(
            baseline_text, current
        )
        added_history = new_history_lines(baseline_text, current)
        if route not in domain_routes:
            if not body_changed:
                classification["stamped_without_body_review"].append(route)
            continue
        if body_changed and added_history:
            continue
        if body_changed:
            classification["untraced"].append(route)
        elif added_history and has_no_impact_marker(added_history):
            classification["attested_no_impact"].append(route)
        else:
            classification["stale"].append(route)
    return classification


def require_updated_route_overview_content(
    context,
    plan: RouteOverviewRefreshPlan,
    changed_paths: list[str],
    *,
    memory_tree: Path | None = None,
    memory_verified_commit: str = "",
) -> list[str]:
    """Fail closeout when a domain-evident route overview lacks an honest update.

    A route overview that directly governs a changed source path is the front
    door for that change: stamping its verification header over an untouched
    body silently presents stale route documentation as current. The overview
    must therefore pair a meaningful body change with a new Update History
    entry, or carry an explicit ``No route impact:`` history entry recording
    that the route was reviewed and intentionally left unchanged. Returns the
    marker-attested routes so closeout payloads can surface them.
    """
    classification = classify_route_overview_updates(
        context,
        plan,
        changed_paths,
        memory_tree=memory_tree,
        memory_verified_commit=memory_verified_commit,
    )
    stale = classification["stale"]
    untraced = classification["untraced"]
    if stale or untraced:
        details: list[str] = []
        if stale:
            details.append(
                "the following governing route overviews have an unmodified or "
                "metadata/history-only body: " + ", ".join(sorted(stale))
            )
        if untraced:
            details.append(
                "the following governing route overviews have a body update without a new "
                "Update History entry: " + ", ".join(sorted(untraced))
            )
        raise RuntimeError(
            "external-memory closeout requires updated route overview content for routes "
            "whose governed sources changed; "
            + "; ".join(details)
            + ". Update each overview body through the c-05-create-or-update-onboarding-files "
            "skill and record the change in its Update History, or record an explicit "
            "'No route impact: <reason>' Update History entry when the route was reviewed "
            "and is unaffected. Advancing lastVerifiedCommitHash on stale content is a "
            "prohibited metadata-only refresh."
        )
    return classification["attested_no_impact"]


def validate_route_overview_refresh_plan_for_context(
    context,
    changed_paths: list[str],
    *,
    memory_tree: Path | None = None,
    memory_verified_commit: str = "",
) -> RouteOverviewRefreshPlan:
    plan = route_overview_metadata_refresh_plan_for_context(context, changed_paths)
    missing_metadata = plan["missing_metadata"]
    if missing_metadata:
        raise RuntimeError(
            "external-memory closeout requires route overview verification metadata before memory commit; "
            f"missing lastVerifiedCommitHash or lastVerifiedCommitDate in: {', '.join(missing_metadata)}. "
            "Run the c-05-create-or-update-onboarding-files skill, then rerun closeout."
        )
    require_updated_route_overview_content(
        context,
        plan,
        changed_paths,
        memory_tree=memory_tree,
        memory_verified_commit=memory_verified_commit,
    )
    return plan


def refresh_route_overview_metadata_for_context(
    context,
    changed_paths: list[str],
    verified_commit: str,
    verified_date: str,
    *,
    memory_tree: Path | None = None,
    memory_verified_commit: str = "",
) -> list[dict[str, str]]:
    plan = validate_route_overview_refresh_plan_for_context(
        context,
        changed_paths,
        memory_tree=memory_tree,
        memory_verified_commit=memory_verified_commit,
    )
    refreshed: list[dict[str, str]] = []
    for item in plan["required"]:
        overview_path = Path(item["onboarding_file"])
        text = filesystem.read_text(overview_path, encoding="utf-8")
        text, hash_found = onboarding_metadata_row(
            text, "lastVerifiedCommitHash", verified_commit, code=True
        )
        text, date_found = onboarding_metadata_row(text, "lastVerifiedCommitDate", verified_date)
        if not hash_found or not date_found:
            raise RuntimeError(
                "external-memory closeout requires route overview verification metadata before memory commit; "
                f"{overview_path.as_posix()} is missing lastVerifiedCommitHash or lastVerifiedCommitDate. "
                "Run the c-05-create-or-update-onboarding-files skill, then rerun closeout."
            )
        filesystem.write_text(overview_path, text, encoding="utf-8")
        refreshed.append(item)
    return refreshed


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


def route_overview_metadata_refresh_plan(
    contract: WorktreeContract, changed_paths: list[str]
) -> RouteOverviewRefreshPlan:
    return route_overview_metadata_refresh_plan_for_context(
        contract_context(contract), changed_paths
    )


def validate_route_overview_refresh_plan(
    contract: WorktreeContract, changed_paths: list[str]
) -> RouteOverviewRefreshPlan:
    return validate_route_overview_refresh_plan_for_context(
        contract_context(contract),
        changed_paths,
        memory_tree=contract.memory_worktree,
        memory_verified_commit=contract_memory_verified_commit(contract),
    )


def classify_sidecar_updates(
    context,
    plan: OnboardingRefreshPlan,
    *,
    memory_tree: Path | None = None,
    memory_verified_commit: str = "",
) -> SidecarBodyClassification:
    """Classify changed-source sidecars by meaningful body and history changes.

    ``stale`` collects unchanged sidecars, metadata-only edits, and history-only
    edits without the no-impact marker; ``untraced`` collects body edits that
    lack a new Update History entry; ``attested_no_impact`` collects
    history-only edits whose new entry carries the explicit
    ``No content impact:`` marker. New sidecars absent from the memory tree's
    HEAD pass without classification, and sidecars that do not resolve under
    the memory tree are skipped rather than reported as false findings.
    """
    classification: SidecarBodyClassification = {
        "stale": [],
        "untraced": [],
        "attested_no_impact": [],
    }
    required = plan["required"]
    if not required:
        return classification
    tree = memory_tree if memory_tree is not None else getattr(context, "memory_root", None)
    if tree is None:
        return classification
    memory_root = Path(tree).resolve()
    baseline_ref = memory_verified_commit or "HEAD"
    changed_memory = _changed_memory_paths(memory_root, memory_verified_commit)
    for item in required:
        onboarding_path = Path(item["onboarding_file"]).resolve()
        try:
            relative = onboarding_path.relative_to(memory_root).as_posix()
        except ValueError:
            continue
        if relative not in changed_memory:
            classification["stale"].append(item["source_path"])
            continue
        baseline_text = commit_text_or_none(memory_root, baseline_ref, relative)
        if baseline_text is None:
            continue
        current = filesystem.read_text(onboarding_path, encoding="utf-8")
        body_changed = meaningful_body_changed(baseline_text, current)
        added_history = new_history_lines(baseline_text, current)
        if body_changed and added_history:
            continue
        if body_changed:
            classification["untraced"].append(item["source_path"])
        elif added_history and has_no_impact_marker(added_history):
            classification["attested_no_impact"].append(item["source_path"])
        else:
            classification["stale"].append(item["source_path"])
    return classification


def require_updated_sidecar_content(
    context,
    plan: OnboardingRefreshPlan,
    *,
    memory_tree: Path | None = None,
    memory_verified_commit: str = "",
) -> list[str]:
    """Fail closeout when a changed source file's sidecar lacks an honest update.

    Refreshing only verification metadata (or only the Update History) on an
    unchanged sidecar body silently defeats the commit-hash-based drift check,
    and a body edit without a new Update History entry loses traceability. A
    changed source file's sidecar must therefore pair a meaningful body change
    with a new history entry; a history-only edit passes only when the new
    entry carries the explicit ``No content impact:`` marker. Returns the
    marker-attested source paths so closeout payloads can surface them. The
    check is a no-op when no sidecar onboarding is required for the changed
    files.
    """

    classification = classify_sidecar_updates(
        context, plan, memory_tree=memory_tree, memory_verified_commit=memory_verified_commit
    )
    stale = classification["stale"]
    untraced = classification["untraced"]
    if stale or untraced:
        details: list[str] = []
        if stale:
            details.append(
                "the following changed sources have an unmodified or metadata/history-only "
                "sidecar body: " + _joined_sample(stale)
            )
        if untraced:
            details.append(
                "the following changed sources have a sidecar body update without a new "
                "Update History entry: " + _joined_sample(untraced)
            )
        raise RuntimeError(
            "external-memory closeout requires updated onboarding content for changed source "
            "files, not only refreshed verification metadata; "
            + "; ".join(details)
            + ". Update each sidecar body through the c-05-create-or-update-onboarding-files "
            "skill and record the change in its Update History, or record an explicit "
            "'No content impact: <reason>' Update History entry when the body is verified "
            "current. Advancing lastVerifiedCommitHash on stale content is a prohibited "
            "metadata-only refresh."
        )
    return classification["attested_no_impact"]


def validate_onboarding_refresh_plan_for_context(
    context,
    changed_paths: list[str],
    *,
    working_paths: list[str] | None = None,
    memory_tree: Path | None = None,
    memory_verified_commit: str = "",
) -> OnboardingRefreshPlan:
    plan = onboarding_refresh_plan_for_context(context, changed_paths, working_paths=working_paths)
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
            + ". Run the c-05-create-or-update-onboarding-files skill, then rerun closeout."
        )
    require_updated_sidecar_content(
        context, plan, memory_tree=memory_tree, memory_verified_commit=memory_verified_commit
    )
    for item in plan["required"]:
        onboarding_path = Path(item["onboarding_file"])
        text = filesystem.read_text(onboarding_path, encoding="utf-8")
        if "lastVerifiedCommitHash" not in text or "lastVerifiedCommitDate" not in text:
            raise RuntimeError(
                "external-memory closeout requires onboarding verification metadata before memory commit; "
                f"{onboarding_path.as_posix()} is missing lastVerifiedCommitHash or lastVerifiedCommitDate. "
                "Run the c-05-create-or-update-onboarding-files skill, then rerun closeout."
            )
    return plan


def validate_onboarding_refresh_plan(
    contract: WorktreeContract,
    changed_paths: list[str],
    *,
    working_paths: list[str] | None = None,
) -> OnboardingRefreshPlan:
    return validate_onboarding_refresh_plan_for_context(
        contract_context(contract),
        changed_paths,
        working_paths=working_paths,
        memory_tree=contract.memory_worktree,
        memory_verified_commit=contract_memory_verified_commit(contract),
    )


def refresh_onboarding_metadata_for_context(
    context,
    changed_paths: list[str],
    verified_commit: str,
    verified_date: str,
    *,
    working_paths: list[str] | None = None,
    memory_tree: Path | None = None,
    memory_verified_commit: str = "",
) -> list[dict[str, str]]:
    plan = validate_onboarding_refresh_plan_for_context(
        context,
        changed_paths,
        working_paths=working_paths,
        memory_tree=memory_tree,
        memory_verified_commit=memory_verified_commit,
    )
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
                "Run the c-05-create-or-update-onboarding-files skill, then rerun closeout."
            )
        filesystem.write_text(onboarding_path, text, encoding="utf-8")
        refreshed.append(item)
    return refreshed


def refresh_onboarding_metadata(
    contract: WorktreeContract,
    changed_paths: list[str],
    verified_commit: str,
    verified_date: str,
    *,
    working_paths: list[str] | None = None,
) -> list[dict[str, str]]:
    return refresh_onboarding_metadata_for_context(
        contract_context(contract),
        changed_paths,
        verified_commit,
        verified_date,
        working_paths=working_paths,
        memory_tree=contract.memory_worktree,
        memory_verified_commit=contract_memory_verified_commit(contract),
    )
