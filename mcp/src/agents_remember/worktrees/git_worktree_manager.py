#!/usr/bin/env python3
"""Manage Agents Remember task Git lifecycle.

Requires Python 3.10+ and git. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from agents_remember.kernel import coordination_context_resolver as resolver
from agents_remember.kernel.memory_ledger import (
    LedgerError,
    find_mapping,
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.providers import provider_setup
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    default_contract,
    load_contract,
    write_contract,
)

ENTITY_FINGERPRINT_ALGORITHM = "git-blob-set-v1"


@dataclass(frozen=True)
class WorktreeCommandResult:
    returncode: int
    payload: dict[str, object]


def run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo.as_posix()}", *args],
        cwd=repo,
        text=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )


def require_git(repo: Path, args: list[str]) -> str:
    result = run_git(repo, args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def current_branch(repo: Path) -> str:
    return require_git(repo, ["branch", "--show-current"])


def head_commit(repo: Path, ref: str = "HEAD") -> str:
    return require_git(repo, ["rev-parse", ref])


def branch_exists(repo: Path, branch: str) -> bool:
    return run_git(repo, ["rev-parse", "--verify", "--quiet", branch]).returncode == 0


def has_changes(repo: Path) -> bool:
    return bool(require_git(repo, ["status", "--porcelain"]))


def worktree_dirty(repo: Path | None) -> bool:
    return bool(repo and repo.exists() and run_git(repo, ["status", "--porcelain"]).stdout.strip())


def contract_has_worktree_changes(contract: WorktreeContract) -> bool:
    return worktree_dirty(contract.code_worktree) or worktree_dirty(contract.memory_worktree)


def require_clean(repo: Path, label: str) -> None:
    changes = require_git(repo, ["status", "--porcelain"])
    if changes:
        raise RuntimeError(f"{label} is not clean:\n{changes}")


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return run_git(repo, ["merge-base", "--is-ancestor", ancestor, descendant]).returncode == 0


def ensure_git_identity(repo: Path) -> None:
    if not run_git(repo, ["config", "--get", "user.email"]).stdout.strip():
        require_git(repo, ["config", "user.email", "agents-remember@example.invalid"])
    if not run_git(repo, ["config", "--get", "user.name"]).stdout.strip():
        require_git(repo, ["config", "user.name", "Agents Remember"])


def ensure_worktree(
    repo: Path, worktree: Path, branch: str, source_branch: str, dry_run: bool
) -> str:
    if worktree.exists():
        return "existing"
    if dry_run:
        return "would-create"
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if branch_exists(repo, branch):
        require_git(repo, ["worktree", "add", str(worktree), branch])
    else:
        require_git(repo, ["worktree", "add", "-b", branch, str(worktree), source_branch])
    return "created"


def resolve_context(args: argparse.Namespace):
    return resolver.resolve_coordination_context(
        code_repository_name=args.code_repository_name,
        workspace_root=args.workspace_root,
        requested_topology=args.topology,
        coordination_root=args.coordination_root,
        code_repository_root=args.code_repository_root,
        task_name=getattr(args, "task_name", None),
        worktree_name=getattr(args, "worktree_name", None),
        contract_path=getattr(args, "contract_path", None),
    )


def contract_payload(contract: WorktreeContract) -> dict[str, object]:
    data = asdict(contract)
    for key, value in list(data.items()):
        if isinstance(value, Path):
            data[key] = value.as_posix()
        elif value is None:
            data[key] = ""
    return data


def lifecycle_guidance(contract: WorktreeContract) -> dict[str, str]:
    if contract.cleanup == "completed":
        return {
            "phase": "cleanup-completed",
            "summary": "Worktree task lifecycle is complete and cleanup has already run.",
            "next_action": "done",
            "next_command": "",
        }
    if contract.integration_status == "blocked":
        return {
            "phase": "integration-blocked",
            "summary": "Integration is blocked; review the conflict or non-fast-forward state with the developer before retrying.",
            "next_action": "developer-decision",
            "next_command": f"integrate --contract-path {contract.contract_path.as_posix()} --approved --strategy replay",
        }
    if contract.integration_status == "completed":
        return {
            "phase": "cleanup-pending",
            "summary": "Integration completed; cleanup is still pending.",
            "next_action": "cleanup",
            "next_command": f"cleanup --contract-path {contract.contract_path.as_posix()} --approved",
        }
    if contract_has_worktree_changes(contract):
        return {
            "phase": "commit-approval-pending",
            "summary": "Worktree changes are present; prepare a closeout preview and ask for explicit commit approval before creating commits.",
            "next_action": "request-commit-approval",
            "next_command": f"closeout --contract-path {contract.contract_path.as_posix()} --dry-run --code-commit-message <message>",
        }
    if contract.closeout_status == "completed":
        return {
            "phase": "integration-pending",
            "summary": "Closeout completed; integrate the task branches back into their source branches.",
            "next_action": "integrate",
            "next_command": f"integrate --contract-path {contract.contract_path.as_posix()} --approved --strategy ff-only",
        }
    if contract.approved_for_commit:
        return {
            "phase": "closeout-pending",
            "summary": "Closeout approval is recorded, but closeout has not completed.",
            "next_action": "closeout",
            "next_command": f"closeout --contract-path {contract.contract_path.as_posix()} --approved",
        }
    return {
        "phase": "worktree-started",
        "summary": "Worktrees are ready; continue the wrapped workflow and close out after review.",
        "next_action": "work",
        "next_command": f"status --contract-path {contract.contract_path.as_posix()}",
    }


def status_payload(contract: WorktreeContract) -> dict[str, object]:
    guidance = lifecycle_guidance(contract)
    payload = {
        "task_id": contract.task_id,
        "task_name": contract.task_name,
        "code_repository_name": contract.repo_name,
        "workflow_kind": contract.workflow_kind,
        "memory_mode": contract.memory_mode,
        "contract_path": contract.contract_path.as_posix(),
        "worktree_group": contract.worktree_group.as_posix(),
        "code_worktree": contract.code_worktree.as_posix(),
        "code_worktree_exists": contract.code_worktree.exists(),
        "code_worktree_dirty": worktree_dirty(contract.code_worktree),
        "memory_worktree": contract.memory_worktree.as_posix() if contract.memory_worktree else "",
        "memory_worktree_exists": contract.memory_worktree.exists()
        if contract.memory_worktree
        else False,
        "memory_worktree_dirty": worktree_dirty(contract.memory_worktree),
        "ledger_path": contract.ledger_path.as_posix() if contract.ledger_path else "",
        "human_review_status": contract.human_review_status,
        "approved_for_commit": contract.approved_for_commit,
        "closeout_status": contract.closeout_status,
        "integration_status": contract.integration_status,
        "cleanup": contract.cleanup,
    }
    payload.update(guidance)
    return payload


def load_contract_from_args(args: argparse.Namespace) -> WorktreeContract:
    if args.contract_path is not None:
        return load_contract(args.contract_path)
    context = resolve_context(args)
    if not args.task_name:
        raise RuntimeError("--task-name or --contract-path is required")
    contract_path = context.task_root / "contract.md"
    return load_contract(contract_path)


def status_result(args: argparse.Namespace) -> WorktreeCommandResult:
    contract = load_contract_from_args(args)
    return WorktreeCommandResult(0, status_payload(contract))


def command_status(args: argparse.Namespace) -> int:
    result = status_result(args)
    print(json.dumps(result.payload, indent=2))
    return result.returncode


def attach_result(args: argparse.Namespace) -> WorktreeCommandResult:
    contract = load_contract_from_args(args)
    return WorktreeCommandResult(
        0, {"state": "attached", "attached": True, **status_payload(contract)}
    )


def command_attach(args: argparse.Namespace) -> int:
    result = attach_result(args)
    print(json.dumps(result.payload, indent=2))
    return result.returncode


def start_result(args: argparse.Namespace) -> WorktreeCommandResult:
    context = resolve_context(args)
    repo = context.code_repository_root
    source_branch = args.source_branch or current_branch(repo)
    work_branch = args.work_branch or f"ar/{args.worktree_name}"
    base_commit = head_commit(repo, source_branch)
    memory_mode = args.memory_mode or context.memory_mode
    memory_repo = (
        context.coordination_root / "memory-repos" / f"ar-{context.code_repository_name}"
        if memory_mode == "external"
        else None
    )
    memory_base = (
        head_commit(memory_repo)
        if memory_repo is not None and memory_repo.exists() and (memory_repo / ".git").exists()
        else ""
    )
    contract = default_contract(
        task_name=args.task_name,
        repo_name=context.code_repository_name,
        workflow_kind=args.workflow_kind,
        memory_mode=memory_mode,
        coordination_root=context.coordination_root,
        code_repo_path=repo,
        code_source_branch=source_branch,
        code_work_branch=work_branch,
        code_base_commit=base_commit,
        worktree_name=args.worktree_name,
        memory_repo_path=memory_repo,
        memory_source_branch=source_branch if memory_mode == "external" else "",
        memory_work_branch=work_branch if memory_mode == "external" else "",
        memory_base_commit=memory_base,
    )

    if contract.contract_path.exists():
        contract = load_contract(contract.contract_path)
        return WorktreeCommandResult(
            0, {"state": "attached-existing-contract", **status_payload(contract)}
        )

    code_state = ensure_worktree(
        repo,
        contract.code_worktree,
        contract.code_work_branch,
        contract.code_source_branch,
        args.dry_run,
    )
    memory_state = prepare_memory_for_start(contract, args)
    if memory_state["state"] == "blocked":
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                "summary": "Code worktree is prepared, but external memory cannot be used until the developer selects a recovery path.",
                "next_action": "choose-memory-recovery",
                "code_worktree": code_state,
                "memory": memory_state,
            },
        )
    if contract.memory_mode == "external" and memory_state["state"] == "disabled":
        contract = replace(
            contract,
            memory_mode="disabled",
            memory_repo_path=None,
            memory_source_branch="",
            memory_work_branch="",
            memory_base_commit="",
            memory_worktree=None,
            ledger_path=None,
            memory_state="disabled",
        )
    provider_state = prepare_providers_for_start(context, contract, args)
    if provider_state["state"] == "blocked":
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                "summary": "Worktree provider setup could not be prepared safely.",
                "next_action": "choose-provider-setup-recovery",
                "code_worktree": code_state,
                "memory": memory_state,
                "providers": provider_state,
            },
        )
    if not args.dry_run:
        write_contract(contract.contract_path, contract)
    return WorktreeCommandResult(
        0,
        {
            "state": "started",
            "summary": "Worktree task started; continue the wrapped workflow before closeout.",
            "next_action": "work",
            "code_worktree": code_state,
            "memory": memory_state,
            "providers": provider_state,
            "contract_path": contract.contract_path.as_posix(),
            "task_artifact": contract.task_artifact.as_posix(),
            "contract": contract_payload(contract),
        },
    )


def command_start(args: argparse.Namespace) -> int:
    result = start_result(args)
    print(json.dumps(result.payload, indent=2))
    return result.returncode


def parse_json_stdout(stdout: str) -> object:
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def prepare_providers_for_start(
    context, contract: WorktreeContract, args: argparse.Namespace
) -> dict[str, object]:
    if args.skip_provider_setup:
        return {"state": "skipped", "reason": "provider setup was skipped"}

    target_coordination_root = (
        args.provider_coordination_root or context.coordination_root
    ).resolve()
    source_coordination_root = (
        args.provider_seed_source_coordination_root or context.coordination_root
    ).resolve()
    source_repo_root = context.code_repository_root.resolve()
    target_repo_root = contract.code_worktree.resolve()
    provider_runtime_root = (
        args.provider_runtime_root or contract.worktree_group / "provider-runtime"
    ).resolve()
    provider_settings_path = (
        args.provider_from_settings.resolve() if args.provider_from_settings else None
    )

    try:
        settings = provider_setup.load_settings(target_coordination_root, provider_settings_path)
        cgc_enabled = bool(settings) and provider_setup.provider_enabled(
            settings, "codegraphcontext-code"
        )
    except RuntimeError as error:
        return {
            "state": "blocked",
            "reason": str(error),
            "targetCoordinationRoot": target_coordination_root.as_posix(),
        }
    if not cgc_enabled:
        return {
            "state": "skipped",
            "reason": "codegraphcontext-code is not enabled in provider settings",
            "settingsFile": provider_setup.settings_path(
                target_coordination_root, provider_settings_path
            ).as_posix(),
        }

    request = provider_setup.ProviderSetupRequest(
        action="prepare",
        coordination_root=target_coordination_root,
        settings_path=provider_setup.settings_path(
            target_coordination_root, provider_settings_path
        ),
        timeout=args.provider_timeout,
        dry_run=args.dry_run,
        skip_grepai=True,
        cgc_seed=provider_setup.CgcSeedOptions(
            source_coordination_root=source_coordination_root,
            repo_id=context.code_repository_name,
            source_repo_root=source_repo_root,
            target_repo_root=target_repo_root,
        ),
        cgc_isolated=provider_setup.IsolatedCgcOptions(runtime_root=provider_runtime_root),
    )
    payload = provider_setup.run_provider_setup(request)
    if not payload.get("ok"):
        return {
            "state": "blocked",
            "reason": "provider setup failed",
            "payload": payload,
        }
    return {
        "state": "planned" if args.dry_run else "prepared",
        "payload": payload,
    }


def prepare_memory_for_start(
    contract: WorktreeContract, args: argparse.Namespace
) -> dict[str, object]:
    if contract.memory_mode == "internal":
        return {"state": "internal", "reason": "memory lives in the code worktree"}
    if contract.memory_mode == "disabled":
        return {"state": "disabled"}
    assert contract.memory_repo_path is not None
    if not contract.memory_repo_path.exists():
        if args.memory_choice == "disabled-memory":
            return {"state": "disabled", "reason": "human selected disabled memory"}
        return {
            "state": "blocked",
            "reason": "external memory repo is missing; run C-00-initialize-memory-repo before starting an external-memory worktree",
            "choices": ["initialize-memory-repo", "disabled-memory", "custom"],
        }
    if (contract.memory_repo_path / ".git").exists() and has_changes(contract.memory_repo_path):
        if args.memory_choice == "disabled-memory":
            return {"state": "disabled", "reason": "human selected disabled memory"}
        return {
            "state": "blocked",
            "reason": "external memory source repo has uncommitted changes; commit refreshed onboarding and ledger before starting worktrees",
            "choices": ["commit-memory-and-ledger-first", "disabled-memory", "custom"],
        }
    ledger_path = contract.memory_repo_path / "memory.md"
    try:
        ledger = load_ledger(ledger_path)
    except LedgerError as error:
        if args.memory_choice == "disabled-memory":
            return {"state": "disabled", "reason": "human selected disabled memory"}
        return {
            "state": "blocked",
            "reason": str(error),
            "choices": ["initialize-memory-repo", "reconciliation", "disabled-memory", "custom"],
        }
    mapping = find_mapping(ledger, contract.code_base_commit)
    if mapping is None:
        return {
            "state": "blocked",
            "reason": "no exact ledger mapping for selected code base commit",
            "codeBaseCommit": contract.code_base_commit,
            "lastVerifiedCodeCommit": ledger.last_verified_code_commit,
            "choices": ["reconciliation", "disabled-memory", "custom"],
        }
    assert contract.memory_worktree is not None
    memory_branch_state = ensure_worktree(
        contract.memory_repo_path,
        contract.memory_worktree,
        contract.memory_work_branch,
        contract.memory_source_branch,
        args.dry_run,
    )
    return {
        "state": "compatible",
        "worktree": memory_branch_state,
        "lastVerifiedCodeCommit": ledger.last_verified_code_commit,
        "lastMemoryContentCommit": ledger.last_memory_content_commit,
    }


def commit_if_dirty(repo: Path, message: str) -> str:
    if not has_changes(repo):
        return head_commit(repo)
    require_git(repo, ["add", "-A"])
    require_git(repo, ["commit", "-m", message])
    return head_commit(repo)


def commit_date(repo: Path, commit: str) -> str:
    return require_git(repo, ["show", "-s", "--format=%cI", commit])


def changed_worktree_paths(repo: Path) -> list[str]:
    tracked = require_git(repo, ["diff", "--name-only", "HEAD", "--"]).splitlines()
    untracked = require_git(repo, ["ls-files", "--others", "--exclude-standard"]).splitlines()
    paths = {
        path.strip().replace("\\", "/")
        for path in [*tracked, *untracked]
        if path.strip() and (repo / path.strip()).is_file()
    }
    return sorted(paths)


def contract_context(contract: WorktreeContract):
    return resolver.resolve_coordination_context(
        code_repository_name=contract.repo_name,
        workspace_root=contract.coordination_root.parent,
        code_repository_root=contract.code_repo_path,
        contract_path=contract.contract_path,
    )


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
        if not onboarding_path.exists():
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


def parse_entity_fingerprint_rows(catalog_path: Path) -> list[dict[str, object]]:
    if not catalog_path.exists():
        return []
    lines = catalog_path.read_text(encoding="utf-8").splitlines()
    header: dict[str, int] | None = None
    start_index = 0
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        cells = markdown_table_cells(line)
        normalized = {normalized_table_cell(cell): position for position, cell in enumerate(cells)}
        required = {"entity", "algorithm", "fingerprint", "evidencepaths"}
        if required.issubset(normalized):
            header = {key: normalized[key] for key in required}
            start_index = index + 2
            break
    if header is None:
        return []

    rows: list[dict[str, object]] = []
    for index in range(start_index, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            break
        if not line.lstrip().startswith("|"):
            continue
        cells = markdown_table_cells(line)
        if len(cells) <= max(header.values()):
            continue
        algorithm = cells[header["algorithm"]].strip("`")
        evidence_cell = cells[header["evidencepaths"]]
        rows.append(
            {
                "line_index": index,
                "entity": cells[header["entity"]].strip("`"),
                "algorithm": algorithm,
                "fingerprint": cells[header["fingerprint"]].strip("`"),
                "evidence_paths": re.findall(r"`([^`]+)`", evidence_cell),
            }
        )
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
    lines = catalog_path.read_text(encoding="utf-8").splitlines()
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
    catalog_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
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
        text = onboarding_path.read_text(encoding="utf-8")
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
        text = onboarding_path.read_text(encoding="utf-8")
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
        onboarding_path.write_text(text, encoding="utf-8")
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


def closeout_preview_payload(
    contract: WorktreeContract, args: argparse.Namespace
) -> dict[str, object]:
    ledger_message = (
        args.ledger_commit_message
        or f"[{contract.task_id}] Ledger sync: <code_commit> -> <memory_commit>"
    )
    code_dirty = worktree_dirty(contract.code_worktree)
    memory_dirty = contract.memory_mode == "external" and worktree_dirty(contract.memory_worktree)
    changed_paths = changed_worktree_paths(contract.code_worktree)
    metadata_refresh = (
        onboarding_refresh_plan(contract, changed_paths)
        if contract.memory_mode == "external"
        else {
            "required": [],
            "missing": [],
            "unsupported": [],
        }
    )
    entity_refresh = (
        entity_fingerprint_refresh_plan(contract, changed_paths)
        if contract.memory_mode == "external"
        else {
            "required": [],
            "unsupported": [],
        }
    )
    payload = {
        "state": "would-closeout",
        **status_payload(contract),
        "phase": "commit-approval-pending",
        "summary": "Closeout preview only; no commits were created. External-memory closeout will commit code first, refresh onboarding verification metadata and affected entity fingerprints to that code commit, then commit memory and ledger.",
        "next_action": "request-commit-approval",
        "next_command": f"closeout --contract-path {contract.contract_path.as_posix()} --approved --approval-note <note>",
        "commit_approval_required": True,
        "approval_question": "Approve creating the code, memory, and ledger commits with these messages?",
        "closeout_order": [
            "commit-code",
            "refresh-onboarding-metadata-and-entity-fingerprints",
            "commit-memory-content",
            "update-ledger",
            "commit-ledger",
            "update-contract",
        ],
        "changed_code_paths": changed_paths,
        "onboarding_metadata_refresh": metadata_refresh,
        "entity_fingerprint_refresh": entity_refresh,
        "proposed_commits": {
            "code": {
                "would_commit": code_dirty,
                "message": args.code_commit_message,
                "worktree": contract.code_worktree.as_posix(),
            },
            "memory": {
                "would_commit": memory_dirty,
                "message": args.memory_commit_message,
                "worktree": contract.memory_worktree.as_posix() if contract.memory_worktree else "",
                "metadata_refresh_after_code_commit": contract.memory_mode == "external",
                "entity_fingerprint_refresh_after_code_commit": contract.memory_mode == "external",
            },
            "ledger": {
                "would_update": contract.memory_mode == "external",
                "message": ledger_message,
                "path": contract.ledger_path.as_posix() if contract.ledger_path else "",
            },
        },
    }
    return payload


def closeout_result(args: argparse.Namespace) -> WorktreeCommandResult:
    contract = load_contract(args.contract_path)
    current_code_source = head_commit(contract.code_repo_path, contract.code_source_branch)
    if current_code_source != contract.code_base_commit:
        raise RuntimeError(
            "code source branch moved since task start: "
            f"{contract.code_source_branch} is {current_code_source}, expected {contract.code_base_commit}"
        )
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and contract.memory_base_commit
    ):
        current_memory_source = head_commit(
            contract.memory_repo_path, contract.memory_source_branch
        )
        if current_memory_source != contract.memory_base_commit:
            raise RuntimeError(
                "memory source branch moved since task start: "
                f"{contract.memory_source_branch} is {current_memory_source}, expected {contract.memory_base_commit}"
            )
    if args.dry_run:
        return WorktreeCommandResult(0, closeout_preview_payload(contract, args))
    if not args.approved:
        raise RuntimeError("closeout requires --approved after explicit commit approval")
    approval_note = args.approval_note.replace("\n", " ").strip()
    if not approval_note:
        raise RuntimeError(
            "closeout requires --approval-note describing the developer's explicit commit approval"
        )
    changed_paths = changed_worktree_paths(contract.code_worktree)
    if contract.memory_mode == "external":
        validate_onboarding_refresh_plan(contract, changed_paths)
    code_commit = commit_if_dirty(contract.code_worktree, args.code_commit_message)
    code_commit_date = commit_date(contract.code_worktree, code_commit)
    memory_commit = ""
    ledger_commit = ""
    refreshed_onboarding: list[dict[str, str]] = []
    if contract.memory_mode == "external":
        if contract.memory_worktree is None or contract.ledger_path is None:
            raise RuntimeError("external-memory closeout requires memory worktree and ledger path")
        refreshed_onboarding = refresh_onboarding_metadata(
            contract, changed_paths, code_commit, code_commit_date
        )
        refreshed_entities = refresh_entity_fingerprints_for_context(
            contract_context(contract), changed_paths
        )
        memory_commit = commit_if_dirty(contract.memory_worktree, args.memory_commit_message)
        ledger = load_ledger(contract.ledger_path)
        write_ledger(contract.ledger_path, prepend_mapping(ledger, code_commit, memory_commit))
        require_git(contract.memory_worktree, ["add", "memory.md"])
        ledger_commit = commit_if_dirty(
            contract.memory_worktree,
            args.ledger_commit_message
            or f"[{contract.task_id}] Ledger sync: {code_commit} -> {memory_commit}",
        )
    updated = replace(
        contract,
        human_review_status="approved",
        approved_for_commit=True,
        commit_approval_note=approval_note,
        closeout_status="completed",
        code_commit=code_commit,
        memory_content_commit=memory_commit,
        ledger_commit=ledger_commit,
    )
    write_contract(contract.contract_path, updated)
    return WorktreeCommandResult(
        0,
        {
            "state": "closed",
            **status_payload(updated),
            "summary": "Closeout completed; integrate the task branches back into their source branches.",
            "next_action": "integrate",
            "code_commit": code_commit,
            "memory_content_commit": memory_commit,
            "ledger_commit": ledger_commit,
            "refreshed_onboarding": refreshed_onboarding,
            "refreshed_entities": refreshed_entities if contract.memory_mode == "external" else [],
        },
    )


def command_closeout(args: argparse.Namespace) -> int:
    result = closeout_result(args)
    print(json.dumps(result.payload, indent=2))
    return result.returncode


def validate_direct_external_context(context, source_branch: str) -> object:
    if context.memory_mode != "external":
        raise RuntimeError("direct closeout currently requires external memory mode")
    if not context.memory_root.exists() or not (context.memory_root / ".git").exists():
        raise RuntimeError(
            f"external memory repo is missing or is not a Git repo: {context.memory_root.as_posix()}"
        )
    if current_branch(context.code_repository_root) != source_branch:
        raise RuntimeError(
            f"code repository is on {current_branch(context.code_repository_root)}, expected {source_branch}"
        )
    memory_branch = current_branch(context.memory_root)
    if memory_branch != source_branch:
        raise RuntimeError(f"memory repo is on {memory_branch}, expected {source_branch}")
    ledger = load_ledger(context.memory_root / "memory.md")
    return ledger


def direct_closeout_preview_payload(
    context, args: argparse.Namespace, source_branch: str
) -> dict[str, object]:
    validate_direct_external_context(context, source_branch)
    code_dirty = worktree_dirty(context.code_repository_root)
    memory_dirty = worktree_dirty(context.memory_root)
    changed_paths = changed_worktree_paths(context.code_repository_root)
    metadata_refresh = onboarding_refresh_plan_for_context(context, changed_paths)
    entity_refresh = entity_fingerprint_refresh_plan_for_context(context, changed_paths)
    ledger_message = (
        args.ledger_commit_message
        or "[direct-closeout] Ledger sync: <code_commit> -> <memory_commit>"
    )
    return {
        "state": "would-direct-closeout",
        "phase": "commit-approval-pending",
        "code_repository_name": context.code_repository_name,
        "code_repository_root": context.code_repository_root.as_posix(),
        "memory_repo": context.memory_root.as_posix(),
        "source_branch": source_branch,
        "summary": "Direct closeout preview only; no commits were created. The real command will commit code first, refresh onboarding verification metadata and affected entity fingerprints to that code commit, then commit memory and ledger.",
        "next_action": "request-commit-approval",
        "next_command": "direct-closeout --approved --approval-note <note> --code-commit-message <message>",
        "commit_approval_required": True,
        "approval_question": "Approve creating the direct code, memory, and ledger commits with these messages?",
        "closeout_order": [
            "commit-code",
            "refresh-onboarding-metadata-and-entity-fingerprints",
            "commit-memory-content",
            "update-ledger",
            "commit-ledger",
        ],
        "changed_code_paths": changed_paths,
        "onboarding_metadata_refresh": metadata_refresh,
        "entity_fingerprint_refresh": entity_refresh,
        "proposed_commits": {
            "code": {
                "would_commit": code_dirty,
                "message": args.code_commit_message,
                "worktree": context.code_repository_root.as_posix(),
            },
            "memory": {
                "would_commit": memory_dirty
                or bool(metadata_refresh["required"])
                or bool(entity_refresh["required"]),
                "message": args.memory_commit_message,
                "worktree": context.memory_root.as_posix(),
                "metadata_refresh_after_code_commit": True,
                "entity_fingerprint_refresh_after_code_commit": True,
            },
            "ledger": {
                "would_update": code_dirty
                or memory_dirty
                or bool(metadata_refresh["required"])
                or bool(entity_refresh["required"]),
                "message": ledger_message,
                "path": (context.memory_root / "memory.md").as_posix(),
            },
        },
    }


def direct_closeout_result(args: argparse.Namespace) -> WorktreeCommandResult:
    context = resolve_context(args)
    source_branch = args.source_branch or current_branch(context.code_repository_root)
    validate_direct_external_context(context, source_branch)
    if args.dry_run:
        return WorktreeCommandResult(
            0, direct_closeout_preview_payload(context, args, source_branch)
        )
    if not args.approved:
        raise RuntimeError("direct closeout requires --approved after explicit commit approval")
    approval_note = args.approval_note.replace("\n", " ").strip()
    if not approval_note:
        raise RuntimeError(
            "direct closeout requires --approval-note describing the developer's explicit commit approval"
        )

    changed_paths = changed_worktree_paths(context.code_repository_root)
    memory_was_dirty = worktree_dirty(context.memory_root)
    if not changed_paths and not memory_was_dirty:
        raise RuntimeError("direct closeout found no code or memory changes to commit")
    validate_onboarding_refresh_plan_for_context(context, changed_paths)

    code_commit = commit_if_dirty(context.code_repository_root, args.code_commit_message)
    code_commit_date = commit_date(context.code_repository_root, code_commit)
    refreshed_onboarding = refresh_onboarding_metadata_for_context(
        context, changed_paths, code_commit, code_commit_date
    )
    refreshed_entities = refresh_entity_fingerprints_for_context(context, changed_paths)
    memory_commit = commit_if_dirty(context.memory_root, args.memory_commit_message)
    ledger_path = context.memory_root / "memory.md"
    ledger = load_ledger(ledger_path)
    write_ledger(ledger_path, prepend_mapping(ledger, code_commit, memory_commit))
    require_git(context.memory_root, ["add", "memory.md"])
    ledger_commit = commit_if_dirty(
        context.memory_root,
        args.ledger_commit_message
        or f"[direct-closeout] Ledger sync: {code_commit} -> {memory_commit}",
    )
    return WorktreeCommandResult(
        0,
        {
            "state": "direct-closed",
            "phase": "done",
            "code_repository_name": context.code_repository_name,
            "code_repository_root": context.code_repository_root.as_posix(),
            "memory_repo": context.memory_root.as_posix(),
            "source_branch": source_branch,
            "summary": "Direct closeout completed; code, memory content, and ledger commits were created on the current branches.",
            "approval_note": approval_note,
            "changed_code_paths": changed_paths,
            "code_commit": code_commit,
            "memory_content_commit": memory_commit,
            "ledger_commit": ledger_commit,
            "refreshed_onboarding": refreshed_onboarding,
            "refreshed_entities": refreshed_entities,
        },
    )


def command_direct_closeout(args: argparse.Namespace) -> int:
    result = direct_closeout_result(args)
    print(json.dumps(result.payload, indent=2))
    return result.returncode


def integration_branch(contract: WorktreeContract) -> str:
    return f"{contract.memory_work_branch}-integration"


def blocked_integration_payload(
    contract: WorktreeContract, state: str, reason: str, persist: bool = True, **extra: object
) -> dict[str, object]:
    blocked = replace(contract, integration_status="blocked")
    if persist:
        write_contract(blocked.contract_path, blocked)
    return {
        "state": state,
        **status_payload(blocked),
        "reason": reason,
        "summary": reason,
        "next_action": "developer-decision",
        "developer_decision_required": True,
        **extra,
    }


def validate_integrate_contract(contract: WorktreeContract) -> None:
    if contract.closeout_status != "completed":
        raise RuntimeError("integration requires closeout.status completed")
    if not contract.approved_for_commit:
        raise RuntimeError("integration requires approved closeout")
    if not contract.code_commit:
        raise RuntimeError("integration requires closeout code_commit")
    if not contract.code_worktree.exists():
        raise RuntimeError(f"code worktree does not exist: {contract.code_worktree}")
    if current_branch(contract.code_repo_path) != contract.code_source_branch:
        raise RuntimeError(f"code source repo must have {contract.code_source_branch} checked out")
    if current_branch(contract.code_worktree) != contract.code_work_branch:
        raise RuntimeError(f"code worktree must have {contract.code_work_branch} checked out")
    require_clean(contract.code_repo_path, "code source repo")
    require_clean(contract.code_worktree, "code worktree")
    if head_commit(contract.code_worktree) != contract.code_commit:
        raise RuntimeError("code worktree HEAD does not match closeout code_commit")
    if contract.memory_mode == "external":
        if (
            contract.memory_repo_path is None
            or contract.memory_worktree is None
            or contract.ledger_path is None
        ):
            raise RuntimeError(
                "external-memory integration requires memory repo, worktree, and ledger path"
            )
        if not contract.memory_content_commit or not contract.ledger_commit:
            raise RuntimeError(
                "external-memory integration requires closeout memory_content_commit and ledger_commit"
            )
        if current_branch(contract.memory_repo_path) != contract.memory_source_branch:
            raise RuntimeError(
                f"memory source repo must have {contract.memory_source_branch} checked out"
            )
        if current_branch(contract.memory_worktree) != contract.memory_work_branch:
            raise RuntimeError(
                f"memory worktree must have {contract.memory_work_branch} checked out"
            )
        require_clean(contract.memory_repo_path, "memory source repo")
        require_clean(contract.memory_worktree, "memory worktree")
        if head_commit(contract.memory_worktree) != contract.ledger_commit:
            raise RuntimeError("memory worktree HEAD does not match closeout ledger_commit")


def replay_code_if_needed(
    contract: WorktreeContract, current_code_source: str
) -> tuple[str, dict[str, object] | None]:
    if is_ancestor(contract.code_repo_path, current_code_source, contract.code_commit):
        return contract.code_commit, None
    result = run_git(contract.code_worktree, ["rebase", contract.code_source_branch])
    if result.returncode != 0:
        return "", blocked_integration_payload(
            contract,
            "blocked-code-conflict",
            "code replay conflicted; resolve with the developer before moving main",
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            conflict_scope="code",
        )
    return head_commit(contract.code_worktree), None


def replay_memory_content(
    contract: WorktreeContract,
    integrated_code_commit: str,
    _current_memory_source: str,
    ledger_message: str,
) -> tuple[str, str, dict[str, object] | None]:
    assert contract.memory_repo_path is not None
    assert contract.memory_worktree is not None
    assert contract.ledger_path is not None
    scratch_branch = integration_branch(contract)
    if branch_exists(contract.memory_repo_path, scratch_branch):
        return (
            "",
            "",
            blocked_integration_payload(
                contract,
                "blocked-existing-integration-branch",
                f"memory integration branch already exists: {scratch_branch}",
                conflict_scope="memory",
                branch=scratch_branch,
            ),
        )
    result = run_git(
        contract.memory_worktree, ["checkout", "-b", scratch_branch, contract.memory_content_commit]
    )
    if result.returncode != 0:
        return (
            "",
            "",
            blocked_integration_payload(
                contract,
                "blocked-memory-replay",
                "could not create memory integration branch",
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
                conflict_scope="memory",
            ),
        )
    result = run_git(
        contract.memory_worktree,
        ["rebase", "--onto", contract.memory_source_branch, contract.memory_base_commit],
    )
    if result.returncode != 0:
        return (
            "",
            "",
            blocked_integration_payload(
                contract,
                "blocked-memory-conflict",
                "memory replay conflicted; resolve with the developer before moving memory main",
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
                conflict_scope="memory",
                branch=scratch_branch,
            ),
        )
    integrated_memory_content_commit = head_commit(contract.memory_worktree)
    ledger = load_ledger(contract.ledger_path)
    write_ledger(
        contract.ledger_path,
        prepend_mapping(ledger, integrated_code_commit, integrated_memory_content_commit),
    )
    require_git(contract.memory_worktree, ["add", "memory.md"])
    integrated_ledger_commit = commit_if_dirty(contract.memory_worktree, ledger_message)
    return integrated_memory_content_commit, integrated_ledger_commit, None


def integrate_result(args: argparse.Namespace) -> WorktreeCommandResult:
    if not args.approved and not args.dry_run:
        raise RuntimeError("integration requires --approved after human review")
    contract = load_contract(args.contract_path)
    if contract.integration_status == "completed":
        return WorktreeCommandResult(0, {"state": "already-integrated", **status_payload(contract)})
    validate_integrate_contract(contract)

    current_code_source = head_commit(contract.code_repo_path, contract.code_source_branch)
    current_memory_source = ""
    code_replay_required = not is_ancestor(
        contract.code_repo_path, current_code_source, contract.code_commit
    )
    memory_replay_required = False
    if contract.memory_mode == "external":
        assert contract.memory_repo_path is not None
        current_memory_source = head_commit(
            contract.memory_repo_path, contract.memory_source_branch
        )
        memory_replay_required = not is_ancestor(
            contract.memory_repo_path, current_memory_source, contract.ledger_commit
        )
    if args.strategy == "ff-only" and (code_replay_required or memory_replay_required):
        return WorktreeCommandResult(
            2,
            blocked_integration_payload(
                contract,
                "blocked-non-ff",
                "source branch moved; rerun with --strategy replay after reviewing parallel changes",
                persist=not args.dry_run,
                code_replay_required=code_replay_required,
                memory_replay_required=memory_replay_required,
            ),
        )

    if args.dry_run:
        return WorktreeCommandResult(
            0,
            {
                "state": "would-integrate",
                **status_payload(contract),
                "summary": "Dry run completed; integration preflight can proceed with the selected strategy.",
                "next_action": "integrate",
                "strategy": args.strategy,
                "code_replay_required": code_replay_required,
                "memory_replay_required": memory_replay_required,
                "cleanup_question": "After successful integration, ask whether to remove the code and memory worktrees plus merged local task branches.",
            },
        )

    integrated_code_commit = contract.code_commit
    if args.strategy == "replay":
        integrated_code_commit, blocked = replay_code_if_needed(contract, current_code_source)
        if blocked is not None:
            return WorktreeCommandResult(2, blocked)
    if not is_ancestor(contract.code_repo_path, current_code_source, integrated_code_commit):
        raise RuntimeError(
            "integrated code commit is not a fast-forward from the current code source branch"
        )

    integrated_memory_content_commit = contract.memory_content_commit
    integrated_ledger_commit = contract.ledger_commit
    if contract.memory_mode == "external":
        assert contract.memory_repo_path is not None
        needs_new_ledger = args.strategy == "replay" and (
            integrated_code_commit != contract.code_commit
            or not is_ancestor(
                contract.memory_repo_path, current_memory_source, contract.ledger_commit
            )
        )
        if needs_new_ledger:
            integrated_memory_content_commit, integrated_ledger_commit, blocked = (
                replay_memory_content(
                    contract,
                    integrated_code_commit,
                    current_memory_source,
                    args.ledger_commit_message
                    or f"[{contract.task_id}] Integration ledger sync: {integrated_code_commit} -> {contract.memory_content_commit}",
                )
            )
            if blocked is not None:
                return WorktreeCommandResult(2, blocked)
        if not is_ancestor(
            contract.memory_repo_path, current_memory_source, integrated_ledger_commit
        ):
            raise RuntimeError(
                "integrated memory ledger commit is not a fast-forward from the current memory source branch"
            )

    require_git(contract.code_repo_path, ["merge", "--ff-only", integrated_code_commit])
    if contract.memory_mode == "external":
        assert contract.memory_repo_path is not None
        require_git(contract.memory_repo_path, ["merge", "--ff-only", integrated_ledger_commit])
        ledger = load_ledger(contract.memory_repo_path / "memory.md")
        mapping = find_mapping(ledger, integrated_code_commit)
        if mapping is None or mapping.memory_commit != integrated_memory_content_commit:
            raise RuntimeError(
                "integrated memory ledger does not map landed code commit to landed memory content commit"
            )

    updated = replace(
        contract,
        integration_status="completed",
        integration_strategy=args.strategy,
        integrated_code_commit=integrated_code_commit,
        integrated_memory_content_commit=integrated_memory_content_commit,
        integrated_ledger_commit=integrated_ledger_commit,
        cleanup="pending",
    )
    write_contract(contract.contract_path, updated)
    return WorktreeCommandResult(
        0,
        {
            "state": "integrated",
            **status_payload(updated),
            "summary": "Integration completed; ask the developer whether to clean up worktrees and merged local branches.",
            "next_action": "cleanup",
            "strategy": args.strategy,
            "integrated_code_commit": integrated_code_commit,
            "integrated_memory_content_commit": integrated_memory_content_commit,
            "integrated_ledger_commit": integrated_ledger_commit,
            "cleanup_question": "Integration completed. Remove the code and memory worktrees plus merged local task branches now?",
        },
    )


def command_integrate(args: argparse.Namespace) -> int:
    result = integrate_result(args)
    print(json.dumps(result.payload, indent=2))
    return result.returncode


def remove_registered_worktree(repo: Path, worktree: Path, dry_run: bool) -> dict[str, object]:
    if not worktree.exists():
        return {"path": worktree.as_posix(), "removed": False, "reason": "already-absent"}
    if dry_run:
        return {"path": worktree.as_posix(), "removed": False, "would_remove": True}
    result = run_git(repo, ["worktree", "remove", str(worktree)])
    if result.returncode != 0:
        return {
            "path": worktree.as_posix(),
            "removed": False,
            "reason": result.stderr.strip() or "git worktree remove failed",
        }
    return {"path": worktree.as_posix(), "removed": True}


def delete_branch_if_merged(repo: Path, branch: str, dry_run: bool) -> dict[str, object]:
    if not branch_exists(repo, branch):
        return {"branch": branch, "deleted": False, "reason": "already-absent"}
    if dry_run:
        return {"branch": branch, "deleted": False, "would_delete": True}
    result = run_git(repo, ["branch", "-d", branch])
    if result.returncode != 0:
        return {
            "branch": branch,
            "deleted": False,
            "reason": result.stderr.strip() or "git branch -d refused the branch",
        }
    return {"branch": branch, "deleted": True}


def remove_empty_dir(path: Path, dry_run: bool) -> dict[str, object]:
    if not path.exists():
        return {"path": path.as_posix(), "removed": False, "reason": "already-absent"}
    if any(path.iterdir()):
        return {"path": path.as_posix(), "removed": False, "reason": "not-empty"}
    if dry_run:
        return {"path": path.as_posix(), "removed": False, "would_remove": True}
    path.rmdir()
    return {"path": path.as_posix(), "removed": True}


def cleanup_result(args: argparse.Namespace) -> WorktreeCommandResult:
    if not args.approved and not args.dry_run:
        raise RuntimeError("cleanup requires --approved after successful integration")
    contract = load_contract(args.contract_path)
    if contract.integration_status != "completed":
        raise RuntimeError("cleanup requires integration.status completed")

    removed_worktrees = {
        "code": remove_registered_worktree(
            contract.code_repo_path, contract.code_worktree, args.dry_run
        ),
    }
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and contract.memory_worktree is not None
    ):
        removed_worktrees["memory"] = remove_registered_worktree(
            contract.memory_repo_path, contract.memory_worktree, args.dry_run
        )

    branches = {
        "code": delete_branch_if_merged(
            contract.code_repo_path, contract.code_work_branch, args.dry_run
        ),
    }
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and contract.memory_work_branch
    ):
        branches["memory"] = delete_branch_if_merged(
            contract.memory_repo_path, contract.memory_work_branch, args.dry_run
        )
        integration_work_branch = integration_branch(contract)
        if branch_exists(contract.memory_repo_path, integration_work_branch):
            branches["memory_integration"] = delete_branch_if_merged(
                contract.memory_repo_path, integration_work_branch, args.dry_run
            )

    directories = {
        "worktree_group": remove_empty_dir(contract.worktree_group, args.dry_run),
    }
    if contract.worktree_group.parent.exists():
        directories["repo_worktree_group"] = remove_empty_dir(
            contract.worktree_group.parent, args.dry_run
        )

    updated = contract if args.dry_run else replace(contract, cleanup="completed")
    if not args.dry_run:
        write_contract(contract.contract_path, updated)

    already_clean = (
        all(
            not item.get("removed") and item.get("reason") == "already-absent"
            for item in removed_worktrees.values()
        )
        and all(
            not item.get("deleted") and item.get("reason") == "already-absent"
            for item in branches.values()
        )
        and updated.cleanup == "completed"
    )
    state = (
        "would-cleanup"
        if args.dry_run
        else ("already-clean" if already_clean else "cleanup-completed")
    )
    kept_branches = {
        key: value
        for key, value in branches.items()
        if not value.get("deleted") and value.get("reason") not in {"already-absent", None}
    }
    return WorktreeCommandResult(
        0,
        {
            "state": state,
            **status_payload(updated),
            "summary": "Cleanup completed; worktrees were removed and merged local task branches were deleted where Git proved they were merged.",
            "next_action": "done",
            "removed_worktrees": removed_worktrees,
            "branches": branches,
            "directories": directories,
            "kept_branches": kept_branches,
        },
    )


def command_cleanup(args: argparse.Namespace) -> int:
    result = cleanup_result(args)
    print(json.dumps(result.payload, indent=2))
    return result.returncode


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--code-repository-name", help="Code repository name to resolve.")
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path.cwd(),
        help="Workspace root used to find --code-repository-name.",
    )
    parser.add_argument(
        "--code-repository-root",
        type=Path,
        help="Root directory of the code repository to resolve.",
    )
    parser.add_argument(
        "--topology", choices=("internal", "external"), help="Optional topology override."
    )
    parser.add_argument("--coordination-root", type=Path, help="Optional coordination root.")
    parser.add_argument("--contract-path", type=Path, help="Path to an existing contract.md.")
    parser.add_argument("--task-name", help="Task name used for task folder resolution.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    add_common(start)
    start.add_argument("--worktree-name", required=True)
    start.add_argument("--workflow-kind", default="light-task")
    start.add_argument("--source-branch")
    start.add_argument("--work-branch")
    start.add_argument("--memory-mode", choices=("internal", "external", "disabled"))
    start.add_argument("--memory-choice", choices=("reconciliation", "disabled-memory", "custom"))
    start.add_argument("--custom-instruction")
    start.add_argument("--skip-provider-setup", action="store_true")
    start.add_argument("--provider-coordination-root", type=Path)
    start.add_argument("--provider-seed-source-coordination-root", type=Path)
    start.add_argument("--provider-runtime-root", type=Path)
    start.add_argument("--provider-from-settings", type=Path)
    start.add_argument("--provider-timeout", type=int, default=1800)
    start.add_argument("--dry-run", action="store_true")
    start.set_defaults(func=command_start)

    attach = subparsers.add_parser("attach")
    add_common(attach)
    attach.set_defaults(func=command_attach)

    status = subparsers.add_parser("status")
    add_common(status)
    status.set_defaults(func=command_status)

    closeout = subparsers.add_parser("closeout")
    closeout.add_argument("--contract-path", type=Path, required=True)
    closeout.add_argument("--approved", action="store_true")
    closeout.add_argument("--approval-note", default="")
    closeout.add_argument("--code-commit-message", required=True)
    closeout.add_argument("--memory-commit-message", default="")
    closeout.add_argument("--ledger-commit-message", default="")
    closeout.add_argument("--dry-run", action="store_true")
    closeout.set_defaults(func=command_closeout)

    direct_closeout = subparsers.add_parser("direct-closeout")
    add_common(direct_closeout)
    direct_closeout.add_argument("--source-branch")
    direct_closeout.add_argument("--approved", action="store_true")
    direct_closeout.add_argument("--approval-note", default="")
    direct_closeout.add_argument("--code-commit-message", required=True)
    direct_closeout.add_argument("--memory-commit-message", default="")
    direct_closeout.add_argument("--ledger-commit-message", default="")
    direct_closeout.add_argument("--dry-run", action="store_true")
    direct_closeout.set_defaults(func=command_direct_closeout)

    integrate = subparsers.add_parser("integrate")
    integrate.add_argument("--contract-path", type=Path, required=True)
    integrate.add_argument("--approved", action="store_true")
    integrate.add_argument("--strategy", choices=("ff-only", "replay"), default="ff-only")
    integrate.add_argument("--ledger-commit-message", default="")
    integrate.add_argument("--dry-run", action="store_true")
    integrate.set_defaults(func=command_integrate)

    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--contract-path", type=Path, required=True)
    cleanup.add_argument("--approved", action="store_true")
    cleanup.add_argument("--dry-run", action="store_true")
    cleanup.set_defaults(func=command_cleanup)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ContractError, LedgerError, ValueError) as error:
        parser.error(str(error))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
