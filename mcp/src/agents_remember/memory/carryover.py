#!/usr/bin/env python3
"""Carry landed branch onboarding into official external memory.

Requires Python 3.10+ and git.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

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
from agents_remember.kernel.route_index import build_route_indexes

PROVEN_EVIDENCE = {"exact-landed-commit", "patch-id-match", "final-content-match"}
FILE_SIDECAR_KIND = "file-sidecar"
ROUTE_OVERVIEW_KIND = "route-overview"


@dataclass(frozen=True)
class CarryoverCandidate:
    source_path: str
    branch_onboarding: str
    official_onboarding: str
    evidence: str
    decision: str
    reason: str
    official_exists: bool
    kind: str = FILE_SIDECAR_KIND


@dataclass(frozen=True)
class CarryoverRequest:
    code_repository_root: Path
    official_code_ref: str
    source_code_ref: str
    old_base: str
    official_memory: Path
    source_memory: Path
    code_repository_name: str
    replace_existing: bool = False


def request_from_args(args: argparse.Namespace) -> CarryoverRequest:
    return CarryoverRequest(
        code_repository_root=args.code_repository_root,
        official_code_ref=args.official_code_ref,
        source_code_ref=args.source_code_ref,
        old_base=args.old_base,
        official_memory=args.official_memory,
        source_memory=args.source_memory,
        code_repository_name=args.code_repository_name,
        replace_existing=args.replace_existing,
    )


def run_git(
    repo: Path, args: list[str], *, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    # stdin must never inherit the parent's descriptor: under the stdio MCP
    # transport that descriptor IS the protocol request pipe, and a child
    # holding or reading it wedges the tool call (GitHub #49).
    stdin_kwargs: dict[str, object] = (
        {"input": input_text} if input_text is not None else {"stdin": subprocess.DEVNULL}
    )
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo.as_posix()}", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        **stdin_kwargs,  # type: ignore[arg-type]
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


def branch_exists(repo: Path, branch: str) -> bool:
    return run_git(repo, ["rev-parse", "--verify", "--quiet", branch]).returncode == 0


def current_branch(repo: Path) -> str:
    return require_git(repo, ["branch", "--show-current"])


def _advance_memory_main(official_memory: Path, main_branch: str = "main") -> dict[str, object]:
    """Fast-forward memory main to the official checkout tip after a carryover (issue #54).

    Code main advances via the GitHub PR merge, but memory has no PR flow: when a
    cycle runs on a non-main source branch, nothing moves memory main and it falls
    behind indefinitely. Carryover by definition runs after code landed officially,
    so it is the natural place to bring memory main forward. ff-only: a diverged
    main (or one pinned by another worktree) is reported, never forced.
    """
    if not branch_exists(official_memory, main_branch):
        return {"state": "skipped", "reason": f"memory repo has no {main_branch!r} branch"}
    if current_branch(official_memory) == main_branch:
        return {"state": "already-current", "branch": main_branch}
    tip = head_commit(official_memory, "HEAD")
    if head_commit(official_memory, main_branch) == tip:
        return {"state": "already-current", "branch": main_branch}
    if not is_ancestor(official_memory, main_branch, tip):
        return {
            "state": "diverged",
            "branch": main_branch,
            "reason": "memory main holds commits the official checkout tip does not",
        }
    result = run_git(official_memory, ["branch", "-f", main_branch, tip])
    if result.returncode != 0:
        return {
            "state": "failed",
            "branch": main_branch,
            "reason": (result.stderr or result.stdout).strip(),
        }
    return {"state": "fast-forwarded", "branch": main_branch, "to": tip}


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


def candidate_for_path(
    *,
    code_repository_root: Path,
    old_base: str,
    official_ref: str,
    source_ref: str,
    official_memory: Path,
    source_memory: Path,
    source_path: str,
    replace_existing: bool,
) -> CarryoverCandidate:
    branch_onboarding = onboarding_path(source_memory, source_path)
    official_onboarding = onboarding_path(official_memory, source_path)
    official_exists = official_onboarding.exists()
    evidence, reason = evidence_for_path(
        code_repository_root, old_base, official_ref, source_ref, source_path
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
        official_exists
        and branch_onboarding.read_text(encoding="utf-8")
        != official_onboarding.read_text(encoding="utf-8")
        and not replace_existing
    ):
        decision = "review-required"
        reason = "official onboarding already exists with different content; use --replace-existing after review"
    return CarryoverCandidate(
        source_path=source_path,
        branch_onboarding=branch_onboarding.as_posix(),
        official_onboarding=official_onboarding.as_posix(),
        evidence=evidence,
        decision=decision,
        reason=reason,
        official_exists=official_exists,
    )


def overview_candidates(
    *,
    official_memory: Path,
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
        official_file = official_memory / "onboarding" / rel
        official_exists = official_file.exists()
        identical = official_exists and branch_file.read_text(
            encoding="utf-8"
        ) == official_file.read_text(encoding="utf-8")
        candidates.append(
            CarryoverCandidate(
                source_path=route,
                branch_onboarding=branch_file.as_posix(),
                official_onboarding=official_file.as_posix(),
                evidence="route-covers-landed-paths",
                decision="auto-carry" if identical else "review-required",
                reason=(
                    "branch and official route overview content match; "
                    "metadata re-verification only"
                    if identical
                    else "route overview body differs from official; model re-review "
                    f"required (covers landed: {', '.join(covered[:5])})"
                ),
                official_exists=official_exists,
                kind=ROUTE_OVERVIEW_KIND,
            )
        )
    return candidates


def build_plan_for_request(request: CarryoverRequest) -> dict[str, object]:
    code_repository_root = request.code_repository_root.resolve()
    official_memory = request.official_memory.resolve()
    source_memory = request.source_memory.resolve()
    official_head = head_commit(code_repository_root, request.official_code_ref)
    source_head = head_commit(code_repository_root, request.source_code_ref)
    old_base = head_commit(code_repository_root, request.old_base)
    official_changed = changed_paths(code_repository_root, old_base, request.official_code_ref)
    source_changed = changed_paths(code_repository_root, old_base, request.source_code_ref)
    candidates = [
        candidate_for_path(
            code_repository_root=code_repository_root,
            old_base=old_base,
            official_ref=request.official_code_ref,
            source_ref=request.source_code_ref,
            official_memory=official_memory,
            source_memory=source_memory,
            source_path=source_path,
            replace_existing=request.replace_existing,
        )
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
                official_onboarding=onboarding_path(official_memory, source_path).as_posix(),
                evidence="not-landed",
                decision="reject",
                reason="source path did not change on official code ref",
                official_exists=onboarding_path(official_memory, source_path).exists(),
            )
        )
    candidates.extend(
        overview_candidates(
            official_memory=official_memory,
            source_memory=source_memory,
            landed_paths=sorted(source_changed & official_changed),
        )
    )
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
        "official_memory": official_memory.as_posix(),
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


def _refresh_official_route_indexes(
    request: CarryoverRequest, official_head: str
) -> dict[str, object]:
    """Regenerate official-side route indexes after carrying onboarding.

    ``overview.index.json`` files are derived artifacts: they are regenerated on
    the official side, never copied from the branch. ``build_route_indexes``
    scans the code working tree, so regeneration only runs when the code
    repository is a clean checkout of the official ref; otherwise the skip is
    reported instead of silently baking wrong coverage into official memory.
    """
    code_root = request.code_repository_root.resolve()
    if head_commit(code_root, "HEAD") != official_head or has_changes(code_root):
        return {
            "state": "skipped",
            "reason": "code repository is not a clean checkout of the official ref; "
            "check out the official ref and rerun carryover, or regenerate via "
            "route_index_refresh",
        }
    onboarding_root = request.official_memory.resolve() / "onboarding"
    if not onboarding_root.is_dir():
        return {
            "state": "skipped",
            "reason": "official memory has no onboarding directory",
        }
    result = build_route_indexes(
        code_root=code_root,
        onboarding_root=onboarding_root,
        repository=request.code_repository_name,
        dry_run=False,
    )
    return {"state": "refreshed", **result.to_dict()}


def _nothing_to_carry_result(
    *,
    plan: dict[str, object],
    cleaned_note: str,
    carried: list[dict[str, object]],
    ledger: MemoryLedger,
    ledger_path: Path,
    official_memory: Path,
    official_head: str,
    ledger_commit_message: str,
) -> dict[str, object]:
    """Result when no onboarding was carried over.

    When nothing is actionable (no auto-carry candidate and no pending
    review-required candidate) the official memory is already current for
    ``official_head``. If the ledger has no entry for that exact code commit —
    e.g. a PR merge commit that landed on top of the verified tip, tree-identical
    but a new SHA — map it to the current memory content commit so the next
    worktree can base off the merged branch without a manual reconciliation.
    Otherwise there is genuinely nothing to record.
    """
    counts = plan.get("counts", {})
    assert isinstance(counts, dict)
    pending = bool(counts.get("auto-carry", 0)) or bool(counts.get("review-required", 0))
    if not pending and find_mapping(ledger, official_head) is None:
        write_ledger(
            ledger_path,
            prepend_mapping(ledger, official_head, ledger.last_memory_content_commit),
        )
        require_git(official_memory, ["add", "memory.md"])
        ledger_commit = commit_if_dirty(official_memory, ledger_commit_message)
        return {
            **plan,
            "state": "ledger-mapped-head",
            "intent_note": cleaned_note,
            "carried": carried,
            "memory_content_commit": ledger.last_memory_content_commit,
            "ledger_commit": ledger_commit,
        }
    return {**plan, "state": "nothing-to-carryover", "carried": carried}


def apply_carryover_for_request(
    request: CarryoverRequest,
    *,
    intent_note: str,
    include_review_required: list[str] | None = None,
    memory_commit_message: str = "Carry over landed branch memory",
    ledger_commit_message: str = "Record branch memory carryover",
) -> dict[str, object]:
    cleaned_note = intent_note.replace("\n", " ").strip()
    if not cleaned_note:
        raise RuntimeError("apply requires an intent_note describing the requested carryover")
    plan = build_plan_for_request(request)
    official_memory = request.official_memory.resolve()
    ensure_clean(official_memory, "official memory")
    ledger_path = official_memory / "memory.md"
    ledger = load_ledger(ledger_path)
    official_head = str(plan["official_code_head"])
    official_date = commit_date(request.code_repository_root.resolve(), official_head)
    included_review_required = set(include_review_required or [])
    carried = []
    for candidate in selected_candidates(plan, included_review_required):
        source = Path(str(candidate["branch_onboarding"]))
        target = Path(str(candidate["official_onboarding"]))
        if not source.exists():
            raise RuntimeError(
                f"selected candidate is missing source branch onboarding: {source.as_posix()}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        refresh_onboarding_metadata(target, official_head, official_date)
        carried.append(candidate)
    route_index_refresh: dict[str, object] = {
        "state": "skipped",
        "reason": "no onboarding was carried over",
    }
    if carried:
        route_index_refresh = _refresh_official_route_indexes(request, official_head)
    if not carried or not has_changes(official_memory):
        return {
            **_nothing_to_carry_result(
                plan=plan,
                cleaned_note=cleaned_note,
                carried=carried,
                ledger=ledger,
                ledger_path=ledger_path,
                official_memory=official_memory,
                official_head=official_head,
                ledger_commit_message=ledger_commit_message,
            ),
            "route_index_refresh": route_index_refresh,
            "memory_main_advance": _advance_memory_main(official_memory),
        }
    memory_content_commit = commit_if_dirty(official_memory, memory_commit_message)
    write_ledger(ledger_path, prepend_mapping(ledger, official_head, memory_content_commit))
    require_git(official_memory, ["add", "memory.md"])
    ledger_commit = commit_if_dirty(official_memory, ledger_commit_message)
    return {
        **plan,
        "state": "carried-over",
        "intent_note": cleaned_note,
        "carried": carried,
        "route_index_refresh": route_index_refresh,
        "memory_content_commit": memory_content_commit,
        "ledger_commit": ledger_commit,
        "memory_main_advance": _advance_memory_main(official_memory),
    }


def apply_carryover(args: argparse.Namespace) -> dict[str, object]:
    if not args.approved or not args.approval_note:
        raise RuntimeError("apply requires --approved and --approval-note")
    return apply_carryover_for_request(
        request_from_args(args),
        intent_note=args.approval_note,
        include_review_required=args.include_review_required,
        memory_commit_message=args.memory_commit_message,
        ledger_commit_message=args.ledger_commit_message,
    )


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--code-repository-root", type=Path, required=True)
    parser.add_argument("--official-code-ref", required=True)
    parser.add_argument("--source-code-ref", required=True)
    parser.add_argument("--old-base", required=True)
    parser.add_argument("--official-memory", type=Path, required=True)
    parser.add_argument("--source-memory", type=Path, required=True)
    parser.add_argument("--code-repository-name", required=True)
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Allow proven candidates to replace existing different official onboarding.",
    )


def command_plan(args: argparse.Namespace) -> int:
    print(json.dumps(build_plan(args), indent=2))
    return 0


def command_apply(args: argparse.Namespace) -> int:
    print(json.dumps(apply_carryover(args), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan")
    add_common(plan)
    plan.set_defaults(func=command_plan)

    apply = subparsers.add_parser("apply")
    add_common(apply)
    apply.add_argument("--approved", action="store_true")
    apply.add_argument("--approval-note")
    apply.add_argument("--include-review-required", action="append", default=[])
    apply.add_argument("--memory-commit-message", default="Carry over landed branch memory")
    apply.add_argument("--ledger-commit-message", default="Record branch memory carryover")
    apply.set_defaults(func=command_apply)
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
