#!/usr/bin/env python3
"""Carry converted branch memory into an open recovery leaf, without committing."""

from __future__ import annotations

import argparse
import json
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, fields
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agents_remember.application.knowledge_currentness.observe import CodeTree, open_code_tree
from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
    load_config,
)
from agents_remember.memory.carryover_apply import (
    _changed_paths,
    _conflicts,
    _Tree,
    apply_converted,
)
from agents_remember.memory.carryover_authority import required_target_storage
from agents_remember.memory.carryover_documents import (
    compare_document_pair,
    document_selection,
    fresh_document_payloads,
    load_document_unit,
    memory_only_evidence,
    observe_document_anchors,
    require_converted_formats,
)
from agents_remember.memory.carryover_evidence import (
    changed_paths,
    ensure_clean,
    evidence_for_path,
    head_commit,
    memory_merge_base,
)
from agents_remember.memory.carryover_records import (
    RecordContext,
    plan_record_dependencies,
    record_payloads,
)
from agents_remember.memory.knowledge_index import (
    IndexMismatchError,
    KnowledgeIndex,
    build_index,
    default_cache_directory,
    directory_snapshot,
)
from agents_remember.memory_quality.knowledge_validator import knowledge_tree_from_git
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
ENTITY_CATALOG_KEY = "entity-catalog"
ENTITY_CATALOG_REL = "entities.md"


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


def request_from_args(args: argparse.Namespace) -> CarryoverRequest:
    values = {
        field.name: getattr(args, field.name)
        for field in fields(CarryoverRequest)
        if field.name != "target_contract_path"
    }
    return CarryoverRequest(target_contract_path=args.contract_path, **values)


def onboarding_path(memory_root: Path, source_path: str) -> Path:
    return memory_root / "onboarding" / f"{source_path}.md"


@contextmanager
def _record_context(request: CarryoverRequest):
    # Existing derived index, disposable only: no persistent new store or memory write.
    with TemporaryDirectory(prefix="ar-carryover-index-") as temporary, ExitStack() as stack:
        indexes = []
        try:
            cache = default_cache_directory(load_config(request.config_path).coordination_root)
            for number, memory in enumerate((request.source_memory, request.target_memory)):
                snapshot = directory_snapshot(memory)
                cached = cache / f"{snapshot.key}.sqlite"
                index = None
                if cached.is_file():
                    # A cache that does not serve this tree is not reused.
                    with suppress(IndexMismatchError):
                        index = KnowledgeIndex(cached, expected_key=snapshot.key)
                if index is None:
                    destination = Path(temporary) / f"{number}.sqlite"
                    build_index(snapshot, destination)
                    index = KnowledgeIndex(destination, expected_key=snapshot.key)
                indexes.append(stack.enter_context(index))
        except (ValueError, OSError, RuntimeError) as error:
            yield None, str(error)
            return
        yield RecordContext(request.source_memory, request.target_memory, *indexes), None


@dataclass(frozen=True)
class _PlanFrame:
    request: CarryoverRequest
    code: Any
    context: RecordContext | None
    index_error: str | None
    memory_base: Any


def _candidate(frame: _PlanFrame, relative: str, seed: dict[str, Any]):
    request = frame.request
    code = frame.code
    context = frame.context
    index_error = frame.index_error
    kind, decision = seed["kind"], seed["decision"]
    result = {
        **seed,
        "branch_onboarding": str(request.source_memory / relative),
        "target_onboarding": str(request.target_memory / relative),
        "target_exists": False,
    }
    try:
        pair = compare_document_pair(request.source_memory, request.target_memory, relative)
        result.update(pair=pair.to_document(), target_exists=pair.target_exists)
        unit = load_document_unit(request.source_memory, relative)
        anchors = observe_document_anchors(unit.document, unit.paths[1], code)
        result["anchors"] = anchors
        if kind == MEMORY_ONLY_DOC_KIND:
            result["evidence"], result["decision"], result["reason"] = memory_only_evidence(
                request.target_memory,
                relative,
                anchors,
                frame.memory_base,
            )
        if result["decision"] != "reject" and anchors["problems"]:
            result.update(decision="review-required", reason="; ".join(anchors["problems"]))
        if pair.target_exists and pair.differing_paths and not request.replace_existing:
            payloads = fresh_document_payloads(request.source_memory, relative, code, reviewed=True)
            resumable = all(
                not (request.target_memory / path).exists()
                or (
                    (request.target_memory / path).is_file()
                    and (request.target_memory / path).read_bytes() == data
                )
                for path, data in payloads.items()
            )
            if not resumable and result["decision"] != "reject":
                result.update(
                    decision="review-required",
                    reason="target document/sidecar differs; use replace_existing after review",
                )
        if context is None:
            result.update(
                records=dict(
                    needed=[],
                    conflicts=[dict(state="index-incomplete", reason=index_error)],
                    history=[],
                )
            )
        else:
            records = plan_record_dependencies(context, unit.document).to_document()
            result["records"] = records
        if result["records"]["conflicts"] and result["decision"] != "reject":
            result.update(
                decision="review-required",
                reason="; ".join(item["reason"] for item in result["records"]["conflicts"]),
            )
    except (ValueError, OSError, RuntimeError) as error:
        result.update(
            decision="review-required" if decision != "reject" else "reject", reason=str(error)
        )
    return result


def build_plan_for_request(request: CarryoverRequest) -> dict[str, Any]:
    version = require_converted_formats(
        request.source_memory, request.target_memory, "memory_carryover_plan"
    )
    official = head_commit(request.code_repository_root, request.official_code_ref)
    source = head_commit(request.code_repository_root, request.source_code_ref)
    base = head_commit(request.code_repository_root, request.old_base)
    official_changed = changed_paths(request.code_repository_root, base, official)
    source_changed = changed_paths(request.code_repository_root, base, source)
    code = open_code_tree(CodeTree(request.code_repository_root, official))
    candidates, seen = [], set()
    memory_base = memory_merge_base(request.target_memory, request.source_memory)
    base_files = (
        knowledge_tree_from_git(request.target_memory, memory_base).files if memory_base else None
    )
    with _record_context(request) as (context, index_error):
        frame = _PlanFrame(request, code, context, index_error, base_files)
        for path in sorted(source_changed):
            relative = f"onboarding/{path}.md"
            evidence, reason = evidence_for_path(
                request.code_repository_root, base, official, source, path
            )
            if path not in official_changed:
                evidence, reason = "not-landed", "source path did not change on official code ref"
            decision = (
                "auto-carry"
                if evidence in PROVEN_EVIDENCE
                else "review-required"
                if evidence == "same-path-changed"
                else "reject"
            )
            seed = dict(
                source_path=path,
                kind=FILE_SIDECAR_KIND,
                evidence=evidence,
                decision=decision,
                reason=reason,
            )
            candidates.append(_candidate(frame, relative, seed))
            seen.add(relative)
        onboarding = request.source_memory / "onboarding"
        for markdown in sorted(onboarding.rglob("*.md")):
            relative = markdown.relative_to(request.source_memory).as_posix()
            if relative in seen:
                continue
            key, kind, pair = document_selection(
                request.source_memory,
                request.target_memory,
                relative,
                source_changed & official_changed,
            )
            if kind in {MEMORY_ONLY_DOC_KIND, ENTITY_CATALOG_KIND} and pair.identical:
                continue
            decision = "auto-carry" if pair.identical else "review-required"
            evidence = (
                "route-covers-landed-paths"
                if kind == ROUTE_OVERVIEW_KIND
                else "entity-catalog-differs"
                if kind == ENTITY_CATALOG_KIND
                else "memory-only"
            )
            reason = (
                "identical pair" if pair.identical else "whole document/sidecar review required"
            )
            seed = dict(
                source_path=key, kind=kind, evidence=evidence, decision=decision, reason=reason
            )
            candidates.append(_candidate(frame, relative, seed))
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate["decision"]] = counts.get(candidate["decision"], 0) + 1
    return dict(
        state="would-carryover",
        code_repository_name=request.code_repository_name,
        code_repository_root=str(request.code_repository_root),
        official_code_ref=request.official_code_ref,
        official_code_head=official,
        source_code_ref=request.source_code_ref,
        source_code_head=source,
        old_base=base,
        target_memory=str(request.target_memory),
        source_memory=str(request.source_memory),
        conversion=version,
        replace_existing=request.replace_existing,
        counts=counts,
        candidates=candidates,
        publication="The recovery leaf curator answers the worklist with its own rows; closeout commits the judged tree.",
    )


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    return build_plan_for_request(request_from_args(args))


def selected_candidates(
    plan: dict[str, Any], include_review_required: set[str]
) -> list[dict[str, Any]]:
    return [
        candidate
        for candidate in plan["candidates"]
        if candidate["decision"] == "auto-carry"
        or (
            candidate["decision"] == "review-required"
            and candidate["source_path"] in include_review_required
        )
    ]


def _apply_carryover_for_request(
    request: CarryoverRequest, *, authority: McpRuntimeConfig, options: CarryoverApplyOptions
) -> dict[str, Any]:
    cleaned = options.intent_note.replace("\n", " ").strip()
    if not cleaned:
        raise RuntimeError("apply requires an intent_note describing the requested carryover")
    require_converted_formats(
        request.source_memory, request.target_memory, "memory_carryover_apply"
    )
    configured = _require_carryover_authority(request, authority)
    require_ordinary_repository_checkout(
        RepositoryCheckoutRequest(
            coordination_root=authority.coordination_root,
            repo_name=request.code_repository_name,
            code_repository=configured.path,
            memory_repository=configured.memory_root,
            checkout=request.target_memory,
            side_name="memory",
            operation="memory_carryover_apply",
        )
    )
    plan = build_plan_for_request(request)
    storage = required_target_storage(request.target_memory)
    included = set(options.include_review_required or [])
    code = open_code_tree(CodeTree(request.code_repository_root, plan["official_code_head"]))
    payloads, selected, skipped = {}, [], []
    before, head = (
        _Tree.read(request.target_memory),
        knowledge_tree_from_git(request.target_memory, "HEAD"),
    )
    changed = _changed_paths(request.target_memory)
    with _record_context(request) as (context, error):
        for candidate in selected_candidates(plan, included):
            relative = (
                Path(candidate["branch_onboarding"]).relative_to(request.source_memory).as_posix()
            )
            try:
                unit = load_document_unit(request.source_memory, relative)
                if context is None:
                    raise ValueError(f"record index unavailable: {error}")
                records = plan_record_dependencies(context, unit.document)
                prepared = fresh_document_payloads(
                    request.source_memory,
                    relative,
                    code,
                    reviewed=candidate["source_path"] in included,
                )
                prepared.update(record_payloads(context, records))
                conflicts = _conflicts(before, head, prepared, changed)
                if conflicts:
                    skipped.append(
                        {**candidate, "decision": "target-edited", "target_edited_paths": conflicts}
                    )
                    continue
                for path, data in prepared.items():
                    if path in payloads and payloads[path] != data:
                        raise ValueError(f"selected payloads disagree at {path}")
                    payloads[path] = data
                selected.append(candidate)
            except (OSError, ValueError, RuntimeError) as error:
                return {
                    **plan,
                    "state": "refused",
                    "reason": str(error),
                    "written_paths": [],
                    "carried": [],
                }
    result = apply_converted(request, plan, selected, payloads, storage)
    result.update(intent_note=cleaned, target_edited=skipped + result.get("target_edited", []))
    return result


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
    except (RuntimeError, ValueError) as error:
        parser.error(str(error))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
