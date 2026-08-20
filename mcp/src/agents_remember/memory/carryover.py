#!/usr/bin/env python3
"""Carry landed branch onboarding into an ordinary external-memory recovery leaf.

Requires Python 3.10+ and git.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.coordination_context.models import StorageSettings
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import (
    LedgerError,
    MemoryLedger,
    find_mapping,
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.kernel.onboarding_doc import (
    discover_route_overviews,
    route_contains_changed_path,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.kernel.route_index import build_route_indexes
from agents_remember.memory.carryover_authority import required_target_storage
from agents_remember.memory_quality.integrity.onboarding_drift_check.entities import (
    parse_entity_fingerprint_rows,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.git_ops import (
    compute_git_blob_set_fingerprint,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import (
    GIT_BLOB_SET_ALGORITHM,
)
from agents_remember.worktrees.integration.integration_branch_authority import (
    RepositoryCheckoutRequest,
    require_ordinary_repository_checkout,
    require_ordinary_worktree,
)
from agents_remember.worktrees.modules.git import repository_identity
from agents_remember.worktrees.worktree_contract import load_contract

PROVEN_EVIDENCE = {"exact-landed-commit", "patch-id-match", "final-content-match"}
FILE_SIDECAR_KIND = "file-sidecar"
ROUTE_OVERVIEW_KIND = "route-overview"
ENTITY_CATALOG_KIND = "entity-catalog"
MEMORY_ONLY_DOC_KIND = "memory-only-doc"
# Selection key for the entity catalog in include_review_required: a stable
# token instead of "entities.md" so it can never collide with a code path.
ENTITY_CATALOG_KEY = "entity-catalog"
ENTITY_CATALOG_REL = "entities.md"
VERIFIED_HASH_PATTERN = re.compile(
    r"\|\s*lastVerifiedCommitHash\s*\|\s*`?([0-9a-fA-F]{7,40})`?\s*\|"
)


@dataclass(frozen=True)
class CarryoverCandidate:
    source_path: str
    branch_onboarding: str
    target_onboarding: str
    evidence: str
    decision: str
    reason: str
    target_exists: bool
    kind: str = FILE_SIDECAR_KIND


@dataclass(frozen=True)
class CarryoverRequest:
    config_path: Path
    target_contract_path: Path
    code_repository_root: Path
    official_code_ref: str
    source_code_ref: str
    old_base: str
    target_memory: Path
    source_memory: Path
    code_repository_name: str
    replace_existing: bool = False


@dataclass(frozen=True)
class CarryoverApplyOptions:
    intent_note: str
    include_review_required: list[str] | None = None
    memory_commit_message: str = "Carry over landed branch memory"
    ledger_commit_message: str = "Record branch memory carryover"


def request_from_args(args: argparse.Namespace) -> CarryoverRequest:
    return CarryoverRequest(
        config_path=args.config_path,
        target_contract_path=args.contract_path,
        code_repository_root=args.code_repository_root,
        official_code_ref=args.official_code_ref,
        source_code_ref=args.source_code_ref,
        old_base=args.old_base,
        target_memory=args.target_memory,
        source_memory=args.source_memory,
        code_repository_name=args.code_repository_name,
        replace_existing=args.replace_existing,
    )


def require_git(repo: Path, args: list[str], *, input_text: str | None = None) -> str:
    result = run_git(repo, args, input_text=input_text)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def head_commit(repo: Path, ref: str) -> str:
    return require_git(repo, ["rev-parse", ref])


def commit_date(repo: Path, commit: str) -> str:
    return require_git(repo, ["show", "-s", "--format=%cI", commit])


def ensure_clean(repo: Path, label: str) -> None:
    status = require_git(repo, ["status", "--porcelain"])
    if status:
        raise RuntimeError(f"{label} is not clean:\n{status}")


def ensure_git_identity(repo: Path) -> None:
    if not run_git(repo, ["config", "--get", "user.email"]).stdout.strip():
        require_git(repo, ["config", "user.email", "agents-remember@example.invalid"])
    if not run_git(repo, ["config", "--get", "user.name"]).stdout.strip():
        require_git(repo, ["config", "user.name", "Agents Remember"])


def has_changes(repo: Path) -> bool:
    return bool(require_git(repo, ["status", "--porcelain"]))


def commit_if_dirty(repo: Path, message: str) -> str:
    if not has_changes(repo):
        return head_commit(repo, "HEAD")
    ensure_git_identity(repo)
    require_git(repo, ["add", "-A"])
    require_git(repo, ["commit", "-m", message])
    return head_commit(repo, "HEAD")


def changed_paths(repo: Path, base_ref: str, head_ref: str) -> set[str]:
    output = require_git(
        repo, ["diff", "--name-only", "--diff-filter=ACMRT", base_ref, head_ref, "--"]
    )
    return {line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()}


def path_exists_at_ref(repo: Path, ref: str, source_path: str) -> bool:
    return run_git(repo, ["cat-file", "-e", f"{ref}:{source_path}"]).returncode == 0


def blob_at_ref(repo: Path, ref: str, source_path: str) -> str | None:
    result = run_git(repo, ["show", f"{ref}:{source_path}"])
    if result.returncode != 0:
        return None
    return result.stdout


def path_commits(repo: Path, base_ref: str, head_ref: str, source_path: str) -> list[str]:
    output = require_git(repo, ["log", "--format=%H", f"{base_ref}..{head_ref}", "--", source_path])
    return [line.strip() for line in output.splitlines() if line.strip()]


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return run_git(repo, ["merge-base", "--is-ancestor", ancestor, descendant]).returncode == 0


def patch_id(repo: Path, base_ref: str, head_ref: str, source_path: str) -> str | None:
    diff_text = require_git(repo, ["diff", base_ref, head_ref, "--", source_path])
    if not diff_text.strip():
        return None
    result = run_git(repo, ["patch-id", "--stable"], input_text=diff_text)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.split()[0]


def evidence_for_path(
    repo: Path, base_ref: str, official_ref: str, source_ref: str, source_path: str
) -> tuple[str, str]:
    if not path_exists_at_ref(repo, official_ref, source_path):
        return "not-landed", "source path is not present on official code ref"
    path_commit_list = path_commits(repo, base_ref, source_ref, source_path)
    if path_commit_list and all(
        is_ancestor(repo, commit, official_ref) for commit in path_commit_list
    ):
        # Only the strongest proof when EVERY source-branch commit touching this
        # path has landed on the official ref. A single landed commit is not
        # enough: a later, unlanded commit to the same path would otherwise be
        # silently carried over as if it had landed.
        return (
            "exact-landed-commit",
            f"all {len(path_commit_list)} source branch commit(s) touching this path "
            "are ancestors of official code ref",
        )
    source_patch = patch_id(repo, base_ref, source_ref, source_path)
    official_patch = patch_id(repo, base_ref, official_ref, source_path)
    if source_patch and official_patch and source_patch == official_patch:
        return (
            "patch-id-match",
            "old-base-to-source-branch patch matches old-base-to-official patch",
        )
    if blob_at_ref(repo, source_ref, source_path) == blob_at_ref(repo, official_ref, source_path):
        return (
            "final-content-match",
            "source branch and official source content match at the selected refs",
        )
    return (
        "same-path-changed",
        "official and source branch changed this path but no equivalence was proven",
    )


def onboarding_path(memory_root: Path, source_path: str) -> Path:
    return memory_root / "onboarding" / f"{source_path}.md"


@dataclass(frozen=True)
class CarryoverRefs:
    """The two states of the world a carryover compares, and the base they diverged from.

    The code repository plus the landed and source refs inside it, and the target and source
    memory trees. Every candidate builder judges one path against exactly this pair of
    sides; the pair is constant for a whole plan, so it is the plan's frame, not per-call
    arguments that could be assembled from two different plans.
    """

    code_repository_root: Path
    official_ref: str
    source_ref: str
    old_base: str
    target_memory: Path
    source_memory: Path


@dataclass(frozen=True)
class MemoryOnlyDoc:
    """One onboarding doc that changed only in branch memory: the branch copy, its target
    counterpart, its path relative to the onboarding root, and the source path it documents."""

    branch_doc: Path
    target_file: Path
    rel: str
    source_path: str


def candidate_for_path(
    refs: CarryoverRefs,
    source_path: str,
    *,
    replace_existing: bool,
) -> CarryoverCandidate:
    branch_onboarding = onboarding_path(refs.source_memory, source_path)
    target_onboarding = onboarding_path(refs.target_memory, source_path)
    target_exists = target_onboarding.exists()
    evidence, reason = evidence_for_path(
        refs.code_repository_root, refs.old_base, refs.official_ref, refs.source_ref, source_path
    )
    decision = (
        "auto-carry"
        if evidence in PROVEN_EVIDENCE
        else "review-required"
        if evidence == "same-path-changed"
        else "reject"
    )
    if not branch_onboarding.exists():
        decision = "reject"
        reason = "source branch onboarding does not exist"
    elif (
        target_exists
        and branch_onboarding.read_text(encoding="utf-8")
        != target_onboarding.read_text(encoding="utf-8")
        and not replace_existing
    ):
        decision = "review-required"
        reason = (
            "target onboarding already exists with different content; "
            "use --replace-existing after review"
        )
    return CarryoverCandidate(
        source_path=source_path,
        branch_onboarding=branch_onboarding.as_posix(),
        target_onboarding=target_onboarding.as_posix(),
        evidence=evidence,
        decision=decision,
        reason=reason,
        target_exists=target_exists,
    )


def overview_candidates(
    *,
    target_memory: Path,
    source_memory: Path,
    landed_paths: list[str],
) -> list[CarryoverCandidate]:
    """Route-overview carryover candidates for routes covering landed paths.

    Overviews are model-authored aggregates, so a differing body is always
    ``review-required`` regardless of per-path evidence; identical branch and
    official content auto-carries for metadata re-verification only. Candidate
    identity is the normalized code route ('.' for the repo root), matching the
    ``include_review_required`` selection contract.
    """
    candidates: list[CarryoverCandidate] = []
    for route, rel in discover_route_overviews(source_memory / "onboarding"):
        covered = sorted(
            path for path in landed_paths if route_contains_changed_path(route, [path])
        )
        if not covered:
            continue
        branch_file = source_memory / "onboarding" / rel
        target_file = target_memory / "onboarding" / rel
        target_exists = target_file.exists()
        identical = target_exists and branch_file.read_text(
            encoding="utf-8"
        ) == target_file.read_text(encoding="utf-8")
        candidates.append(
            CarryoverCandidate(
                source_path=route,
                branch_onboarding=branch_file.as_posix(),
                target_onboarding=target_file.as_posix(),
                evidence="route-covers-landed-paths",
                decision="auto-carry" if identical else "review-required",
                reason=(
                    "branch and target route overview content match; metadata re-verification only"
                    if identical
                    else "route overview body differs from target; model re-review "
                    f"required (covers landed: {', '.join(covered[:5])})"
                ),
                target_exists=target_exists,
                kind=ROUTE_OVERVIEW_KIND,
            )
        )
    return candidates


def object_id_at_ref(repo: Path, ref: str, source_path: str) -> str | None:
    """Git object id of a file blob or directory tree at a ref; None if absent.

    The repo-root route ('.') resolves to the commit's root tree because
    ``<ref>:.`` is not valid rev syntax.
    """
    target = f"{ref}^{{tree}}" if source_path in {"", "."} else f"{ref}:{source_path}"
    result = run_git(repo, ["rev-parse", target])
    return result.stdout.strip() if result.returncode == 0 else None


def verified_commit_of(doc: Path) -> str | None:
    match = VERIFIED_HASH_PATTERN.search(doc.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def memory_merge_base(target_memory: Path, source_memory: Path) -> str | None:
    source_head = run_git(source_memory, ["rev-parse", "HEAD"])
    if source_head.returncode != 0:
        return None
    result = run_git(target_memory, ["merge-base", "HEAD", source_head.stdout.strip()])
    return result.stdout.strip() if result.returncode == 0 else None


def _memory_only_evidence(
    refs: CarryoverRefs,
    doc: MemoryOnlyDoc,
    mem_base: str | None,
) -> tuple[str, str, str]:
    """(evidence, decision, reason) for one memory-only doc candidate."""
    code_repository_root = refs.code_repository_root
    official_ref = refs.official_ref
    target_memory = refs.target_memory
    branch_doc = doc.branch_doc
    target_file = doc.target_file
    rel = doc.rel
    source_path = doc.source_path
    if object_id_at_ref(code_repository_root, official_ref, source_path) is None:
        return "not-landed", "reject", "source path is not present on official code ref"
    verified = verified_commit_of(branch_doc)
    if verified is None:
        return (
            "unverifiable",
            "review-required",
            "branch doc has no resolvable lastVerifiedCommitHash; model re-review required",
        )
    verified_object = object_id_at_ref(code_repository_root, verified, source_path)
    if verified_object is None:
        return (
            "unverifiable",
            "review-required",
            f"verification commit {verified[:12]} or its source object is not "
            "resolvable in the code repository; model re-review required",
        )
    if verified_object != object_id_at_ref(code_repository_root, official_ref, source_path):
        return (
            "source-diverged",
            "review-required",
            "source content changed between the branch verification commit and the "
            "official ref; model re-review required",
        )
    base_blob = blob_at_ref(target_memory, mem_base, f"onboarding/{rel}") if mem_base else None
    target_text = target_file.read_text(encoding="utf-8") if target_file.exists() else None
    if mem_base is None or base_blob != target_text:
        return (
            "target-memory-moved",
            "review-required",
            "target memory changed this doc independently since the memory "
            "merge-base (or no merge-base is resolvable); model re-review required",
        )
    return (
        "memory-only-reverification-valid",
        "auto-carry",
        "source content at the branch verification commit matches the official ref "
        "and target memory has not changed this doc since the merge-base",
    )


def memory_only_doc_candidates(
    refs: CarryoverRefs,
    *,
    existing: set[str],
) -> list[CarryoverCandidate]:
    """Candidates for onboarding docs changed only in branch memory.

    Closeout can legitimately improve docs whose source path is outside the
    code diff (e.g. re-verifying a pre-existing drift), which the diff-derived
    candidate builders structurally cannot see. Auto-carry needs two proofs:
    the source object at the branch doc's verification commit must match the
    official ref, and target memory must not have changed the doc since the
    memory merge-base — a parallel official change is always review-required.
    """
    target_memory = refs.target_memory
    source_onboarding = refs.source_memory / "onboarding"
    if not source_onboarding.is_dir():
        return []
    route_by_rel = {rel: route for route, rel in discover_route_overviews(source_onboarding)}
    mem_base = memory_merge_base(target_memory, refs.source_memory)
    candidates: list[CarryoverCandidate] = []
    for branch_doc in sorted(source_onboarding.rglob("*.md")):
        if not branch_doc.is_file():
            continue
        rel = branch_doc.relative_to(source_onboarding).as_posix()
        if rel == ENTITY_CATALOG_REL:
            continue
        source_path = route_by_rel.get(rel, rel[: -len(".md")])
        if source_path in existing:
            continue
        target_file = target_memory / "onboarding" / rel
        if target_file.exists() and target_file.read_text(encoding="utf-8") == branch_doc.read_text(
            encoding="utf-8"
        ):
            continue
        evidence, decision, reason = _memory_only_evidence(
            refs,
            MemoryOnlyDoc(
                branch_doc=branch_doc,
                target_file=target_file,
                rel=rel,
                source_path=source_path,
            ),
            mem_base,
        )
        candidates.append(
            CarryoverCandidate(
                source_path=source_path,
                branch_onboarding=branch_doc.as_posix(),
                target_onboarding=target_file.as_posix(),
                evidence=evidence,
                decision=decision,
                reason=reason,
                target_exists=target_file.exists(),
                kind=MEMORY_ONLY_DOC_KIND,
            )
        )
    return candidates


def entity_catalog_candidate(
    *, target_memory: Path, source_memory: Path
) -> CarryoverCandidate | None:
    """Review-required candidate when the branch entity catalog differs.

    The catalog is a single model-authored aggregate covering many entities,
    so it is never auto-carried and never merged entry-by-entry; an identical
    catalog yields no candidate because it has no per-doc verification
    metadata to bump. Selected via the stable ``entity-catalog`` key.
    """
    branch_catalog = source_memory / "onboarding" / ENTITY_CATALOG_REL
    if not branch_catalog.exists():
        return None
    target_catalog = target_memory / "onboarding" / ENTITY_CATALOG_REL
    target_exists = target_catalog.exists()
    if target_exists and target_catalog.read_text(encoding="utf-8") == branch_catalog.read_text(
        encoding="utf-8"
    ):
        return None
    return CarryoverCandidate(
        source_path=ENTITY_CATALOG_KEY,
        branch_onboarding=branch_catalog.as_posix(),
        target_onboarding=target_catalog.as_posix(),
        evidence="entity-catalog-differs",
        decision="review-required",
        reason=(
            "entity catalog body differs from target; whole-file carry after "
            "model review (fingerprints are validated against the official ref "
            "on apply)"
        ),
        target_exists=target_exists,
        kind=ENTITY_CATALOG_KIND,
    )


def build_plan_for_request(request: CarryoverRequest) -> dict[str, object]:
    code_repository_root = request.code_repository_root.resolve()
    target_memory = request.target_memory.resolve()
    source_memory = request.source_memory.resolve()
    official_head = head_commit(code_repository_root, request.official_code_ref)
    source_head = head_commit(code_repository_root, request.source_code_ref)
    old_base = head_commit(code_repository_root, request.old_base)
    official_changed = changed_paths(code_repository_root, old_base, request.official_code_ref)
    source_changed = changed_paths(code_repository_root, old_base, request.source_code_ref)
    refs = CarryoverRefs(
        code_repository_root=code_repository_root,
        official_ref=request.official_code_ref,
        source_ref=request.source_code_ref,
        old_base=old_base,
        target_memory=target_memory,
        source_memory=source_memory,
    )
    candidates = [
        candidate_for_path(refs, source_path, replace_existing=request.replace_existing)
        for source_path in sorted(source_changed)
        if source_path in official_changed
    ]
    absent_paths = sorted(
        source_path for source_path in source_changed if source_path not in official_changed
    )
    for source_path in absent_paths:
        if source_path in {candidate.source_path for candidate in candidates}:
            continue
        candidates.append(
            CarryoverCandidate(
                source_path=source_path,
                branch_onboarding=onboarding_path(source_memory, source_path).as_posix(),
                target_onboarding=onboarding_path(target_memory, source_path).as_posix(),
                evidence="not-landed",
                decision="reject",
                reason="source path did not change on official code ref",
                target_exists=onboarding_path(target_memory, source_path).exists(),
            )
        )
    candidates.extend(
        overview_candidates(
            target_memory=target_memory,
            source_memory=source_memory,
            landed_paths=sorted(source_changed & official_changed),
        )
    )
    candidates.extend(
        memory_only_doc_candidates(
            refs, existing={candidate.source_path for candidate in candidates}
        )
    )
    catalog_candidate = entity_catalog_candidate(
        target_memory=target_memory, source_memory=source_memory
    )
    if catalog_candidate is not None:
        candidates.append(catalog_candidate)
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.decision] = counts.get(candidate.decision, 0) + 1
    return {
        "state": "would-carryover",
        "code_repository_name": request.code_repository_name,
        "code_repository_root": code_repository_root.as_posix(),
        "official_code_ref": request.official_code_ref,
        "official_code_head": official_head,
        "source_code_ref": request.source_code_ref,
        "source_code_head": source_head,
        "old_base": old_base,
        "target_memory": target_memory.as_posix(),
        "source_memory": source_memory.as_posix(),
        "replace_existing": bool(request.replace_existing),
        "counts": counts,
        "candidates": [asdict(candidate) for candidate in candidates],
    }


def build_plan(args: argparse.Namespace) -> dict[str, object]:
    return build_plan_for_request(request_from_args(args))


def metadata_row(text: str, field: str, value: str, *, code: bool = False) -> tuple[str, bool]:
    rendered = f"`{value}`" if code else value
    pattern = re.compile(rf"(\|\s*{re.escape(field)}\s*\|\s*)`?[^|`]*`?(\s*\|)")
    updated, count = pattern.subn(rf"\g<1>{rendered}\g<2>", text, count=1)
    return updated, count > 0


def refresh_onboarding_metadata(path: Path, verified_commit: str, verified_date: str) -> None:
    text = path.read_text(encoding="utf-8")
    text, hash_found = metadata_row(text, "lastVerifiedCommitHash", verified_commit, code=True)
    text, date_found = metadata_row(text, "lastVerifiedCommitDate", verified_date)
    if not hash_found or not date_found:
        raise RuntimeError(f"{path.as_posix()} is missing onboarding verification metadata")
    path.write_text(text, encoding="utf-8")


def selected_candidates(
    plan: dict[str, object], include_review_required: set[str]
) -> list[dict[str, object]]:
    selected = []
    candidates = plan["candidates"]
    assert isinstance(candidates, list)
    for candidate in candidates:
        assert isinstance(candidate, dict)
        if candidate["decision"] == "auto-carry" or (
            candidate["decision"] == "review-required"
            and candidate["source_path"] in include_review_required
        ):
            selected.append(candidate)
    return selected


def _refresh_target_route_indexes(
    request: CarryoverRequest,
    official_head: str,
    storage: StorageSettings,
) -> dict[str, object]:
    """Regenerate recovery-leaf route indexes after carrying onboarding.

    ``overview.index.json`` files are derived artifacts: they are regenerated on
    the target side, never copied from the source. ``build_route_indexes``
    uses Git and configured path-rule authority. Regeneration only runs when
    the code repository is a clean checkout of the official ref; otherwise the
    skip is reported instead of indexing a different code state.
    """
    code_root = request.code_repository_root.resolve()
    if head_commit(code_root, "HEAD") != official_head or has_changes(code_root):
        return {
            "state": "skipped",
            "reason": "code repository is not a clean checkout of the official ref; "
            "check out the official ref and rerun carryover, or regenerate via "
            "route_index_refresh",
        }
    onboarding_root = request.target_memory.resolve() / "onboarding"
    if not onboarding_root.is_dir():
        return {
            "state": "skipped",
            "reason": "target memory has no onboarding directory",
        }
    result = build_route_indexes(
        code_root=code_root,
        onboarding_root=onboarding_root,
        repository=request.code_repository_name,
        storage=storage,
        dry_run=False,
    )
    return {"state": "refreshed", **result.to_dict()}


def _validate_entity_fingerprints(
    code_repository_root: Path, official_ref: str, catalog: Path
) -> dict[str, object]:
    """Recompute every catalog fingerprint row against the official code ref.

    Fingerprints are derived values: a carried catalog is validated against
    the official ref instead of being trusted as copied. Mismatches are
    reported, not blocking — the reviewer selected the carry, and the next
    memory quality check keeps them visible.
    """
    mismatches: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    rows = parse_entity_fingerprint_rows(catalog)
    for row in rows:
        if row.algorithm != GIT_BLOB_SET_ALGORITHM:
            errors.append(
                {"entity": row.entity, "reason": f"unsupported algorithm {row.algorithm!r}"}
            )
            continue
        try:
            computed = compute_git_blob_set_fingerprint(
                code_repository_root, row.evidence_paths, ref=official_ref
            )
        except RuntimeError as error:
            errors.append({"entity": row.entity, "reason": str(error)})
            continue
        if computed != row.fingerprint:
            mismatches.append(
                {"entity": row.entity, "recorded": row.fingerprint, "computed": computed}
            )
    state = "validated" if not mismatches and not errors else "mismatch"
    return {"state": state, "rows": len(rows), "mismatches": mismatches, "errors": errors}


@dataclass(frozen=True)
class TargetLedger:
    """The recovery-leaf ledger as carryover writes it: the loaded ledger, the file it was
    read from, the memory tree that must stage and commit that file, and the message to commit
    it with. A ledger without its path and tree cannot be persisted, so they are one handle."""

    ledger: MemoryLedger
    path: Path
    memory_root: Path
    commit_message: str


def _nothing_to_carry_result(
    plan: dict[str, object],
    target_ledger: TargetLedger,
    *,
    cleaned_note: str,
    carried: list[dict[str, object]],
    official_head: str,
) -> dict[str, object]:
    """Result when no onboarding was carried over.

    When nothing is actionable (no auto-carry candidate and no pending
    review-required candidate) the target memory is already current for
    ``official_head``. If the ledger has no entry for that exact code commit —
    e.g. a PR merge commit that landed on top of the verified tip, tree-identical
    but a new SHA — map it to the current memory content commit so the next
    worktree can base off the merged branch without a manual reconciliation.
    Otherwise there is genuinely nothing to record.
    """
    ledger = target_ledger.ledger
    counts = plan.get("counts", {})
    assert isinstance(counts, dict)
    pending = bool(counts.get("auto-carry", 0)) or bool(counts.get("review-required", 0))
    if not pending and find_mapping(ledger, official_head) is None:
        write_ledger(
            target_ledger.path,
            prepend_mapping(ledger, official_head, ledger.last_memory_content_commit),
        )
        require_git(target_ledger.memory_root, ["add", "memory.md"])
        ledger_commit = commit_if_dirty(target_ledger.memory_root, target_ledger.commit_message)
        return {
            **plan,
            "state": "ledger-mapped-head",
            "intent_note": cleaned_note,
            "carried": carried,
            "memory_content_commit": ledger.last_memory_content_commit,
            "ledger_commit": ledger_commit,
        }
    return {**plan, "state": "nothing-to-carryover", "carried": carried}


def _apply_carryover_for_request(
    request: CarryoverRequest,
    *,
    authority: McpRuntimeConfig,
    options: CarryoverApplyOptions,
) -> dict[str, object]:
    cleaned_note = options.intent_note.replace("\n", " ").strip()
    if not cleaned_note:
        raise RuntimeError("apply requires an intent_note describing the requested carryover")
    target_memory = request.target_memory.resolve()
    configured = _require_carryover_authority(request, authority)
    require_ordinary_repository_checkout(
        RepositoryCheckoutRequest(
            coordination_root=authority.coordination_root,
            repo_name=request.code_repository_name,
            code_repository=configured.path,
            memory_repository=configured.memory_root,
            checkout=target_memory,
            side_name="memory",
            operation="memory_carryover_apply",
        )
    )
    plan = build_plan_for_request(request)
    ensure_clean(target_memory, "target memory")
    target_storage = required_target_storage(target_memory)
    ledger_path = target_memory / "memory.md"
    ledger = load_ledger(ledger_path)
    official_head = str(plan["official_code_head"])
    official_date = commit_date(request.code_repository_root.resolve(), official_head)
    included_review_required = set(options.include_review_required or [])
    carried = []
    entity_fingerprint_validation: dict[str, object] = {
        "state": "skipped",
        "reason": "entity catalog was not carried",
    }
    for candidate in selected_candidates(plan, included_review_required):
        source = Path(str(candidate["branch_onboarding"]))
        target = Path(str(candidate["target_onboarding"]))
        if not source.exists():
            raise RuntimeError(
                f"selected candidate is missing source branch onboarding: {source.as_posix()}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        if candidate["kind"] == ENTITY_CATALOG_KIND:
            # The catalog has no per-doc verification metadata table; its
            # integrity check is the fingerprint validation instead.
            entity_fingerprint_validation = _validate_entity_fingerprints(
                request.code_repository_root.resolve(),
                request.official_code_ref,
                target,
            )
        else:
            refresh_onboarding_metadata(target, official_head, official_date)
        carried.append(candidate)
    route_index_refresh: dict[str, object] = {
        "state": "skipped",
        "reason": "no onboarding was carried over",
    }
    if carried:
        route_index_refresh = _refresh_target_route_indexes(
            request,
            official_head,
            target_storage,
        )
    if not carried or not has_changes(target_memory):
        return {
            **_nothing_to_carry_result(
                plan,
                TargetLedger(
                    ledger=ledger,
                    path=ledger_path,
                    memory_root=target_memory,
                    commit_message=options.ledger_commit_message,
                ),
                cleaned_note=cleaned_note,
                carried=carried,
                official_head=official_head,
            ),
            "route_index_refresh": route_index_refresh,
        }
    memory_content_commit = commit_if_dirty(target_memory, options.memory_commit_message)
    write_ledger(ledger_path, prepend_mapping(ledger, official_head, memory_content_commit))
    require_git(target_memory, ["add", "memory.md"])
    ledger_commit = commit_if_dirty(target_memory, options.ledger_commit_message)
    return {
        **plan,
        "state": "carried-over",
        "intent_note": cleaned_note,
        "carried": carried,
        "route_index_refresh": route_index_refresh,
        "entity_fingerprint_validation": entity_fingerprint_validation,
        "memory_content_commit": memory_content_commit,
        "ledger_commit": ledger_commit,
    }


def _require_carryover_authority(
    request: CarryoverRequest,
    authority: McpRuntimeConfig,
) -> RepositoryScope:
    if request.config_path.resolve() != authority.config_path.resolve():
        raise RuntimeError("carryover request does not match runtime config authority")
    configured = require_repo(authority, request.code_repository_name)
    if repository_identity(request.code_repository_root) != repository_identity(configured.path):
        raise RuntimeError("carryover code repository does not match configured authority")
    if configured.memory_root is None:
        raise RuntimeError("carryover requires configured external memory authority")
    if repository_identity(request.target_memory) != repository_identity(configured.memory_root):
        raise RuntimeError("carryover target memory does not match configured authority")
    contract = load_contract(request.target_contract_path)
    if contract.coordination_root.resolve() != authority.coordination_root.resolve():
        raise RuntimeError("carryover target contract does not match coordination authority")
    if (
        contract.kind != "leaf"
        or contract.repo_name != request.code_repository_name
        or contract.memory_mode != "external"
        or contract.memory_worktree is None
        or contract.memory_worktree.resolve() != request.target_memory.resolve()
    ):
        raise RuntimeError("carryover target is not the exact external-memory leaf worktree")
    if contract.closeout_status != "not-started" or contract.integration_status != "not-started":
        raise RuntimeError("carryover target leaf is no longer open for memory work")
    require_ordinary_worktree(contract, operation="memory_carryover_apply")
    official_code_head = head_commit(request.code_repository_root, request.official_code_ref)
    if (
        contract.code_base_commit != official_code_head
        or head_commit(contract.code_worktree, "HEAD") != official_code_head
    ):
        raise RuntimeError(
            "carryover target leaf code worktree must be unchanged at the selected official tip"
        )
    ensure_clean(contract.code_worktree, "carryover target code worktree")
    return configured


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config-path", type=Path, required=True)
    parser.add_argument("--contract-path", type=Path, required=True)
    parser.add_argument("--code-repository-root", type=Path, required=True)
    parser.add_argument("--official-code-ref", required=True)
    parser.add_argument("--source-code-ref", required=True)
    parser.add_argument("--old-base", required=True)
    parser.add_argument("--target-memory", type=Path, required=True)
    parser.add_argument("--source-memory", type=Path, required=True)
    parser.add_argument("--code-repository-name", required=True)
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Allow proven candidates to replace existing different target onboarding.",
    )


def command_plan(args: argparse.Namespace) -> int:
    print(json.dumps(build_plan(args), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan")
    add_common(plan)
    plan.set_defaults(func=command_plan)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ValueError, LedgerError) as error:
        parser.error(str(error))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
