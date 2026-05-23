#!/usr/bin/env python3
"""Carry landed branch onboarding into official external memory.

Requires Python 3.10+ and git. Uses only the Python standard library.
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
    load_ledger,
    prepend_mapping,
    write_ledger,
)

PROVEN_EVIDENCE = {"exact-landed-commit", "patch-id-match", "final-content-match"}


@dataclass(frozen=True)
class CarryoverCandidate:
    source_path: str
    branch_onboarding: str
    official_onboarding: str
    evidence: str
    decision: str
    reason: str
    official_exists: bool


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
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo.as_posix()}", *args],
        cwd=repo,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
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
    for commit in path_commits(repo, base_ref, source_ref, source_path):
        if is_ancestor(repo, commit, official_ref):
            return (
                "exact-landed-commit",
                f"source branch commit {commit} is an ancestor of official code ref",
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
    for candidate in plan["candidates"]:
        assert isinstance(candidate, dict)
        if candidate["decision"] == "auto-carry" or (
            candidate["decision"] == "review-required"
            and candidate["source_path"] in include_review_required
        ):
            selected.append(candidate)
    return selected


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
    if not carried or not has_changes(official_memory):
        payload = {**plan, "state": "nothing-to-carryover", "carried": carried}
        return payload
    memory_content_commit = commit_if_dirty(official_memory, memory_commit_message)
    write_ledger(ledger_path, prepend_mapping(ledger, official_head, memory_content_commit))
    require_git(official_memory, ["add", "memory.md"])
    ledger_commit = commit_if_dirty(official_memory, ledger_commit_message)
    return {
        **plan,
        "state": "carried-over",
        "intent_note": cleaned_note,
        "carried": carried,
        "memory_content_commit": memory_content_commit,
        "ledger_commit": ledger_commit,
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
