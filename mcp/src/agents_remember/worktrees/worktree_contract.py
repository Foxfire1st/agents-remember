"""Read and write Agents Remember series and leaf enclosure contracts.

The contract is a small markdown file with a limited YAML-like front matter block.
This parser intentionally supports only the subset the workflow writes: scalar
top-level fields and one-level nested sections.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agents_remember.errors import AgentsRememberError
from agents_remember.worktrees.task_resolver import (
    SERIES_CONTRACT_FILENAME,
    leaf_enclosure_path,
    resolve_active_task_root,
    series_contract_path,
    slugify,
    task_root_for,
)

CONTRACT_SCHEMA = "ar-series-contract/v1"
VALID_MEMORY_MODES = {"internal", "external", "disabled"}
VALID_KINDS = {"series", "leaf"}


class ContractError(AgentsRememberError):
    """Raised when a worktree contract cannot be parsed or validated."""


@dataclass(frozen=True)
class WorktreeContract:
    task_id: str
    task_name: str
    repo_name: str
    workflow_kind: str
    memory_mode: str
    coordination_root: Path
    task_root: Path
    contract_path: Path
    task_artifact: Path
    worktree_group: Path
    code_repo_path: Path
    code_source_branch: str
    code_work_branch: str
    code_base_commit: str
    code_worktree: Path
    memory_repo_path: Path | None = None
    memory_source_branch: str = ""
    memory_work_branch: str = ""
    memory_base_commit: str = ""
    memory_worktree: Path | None = None
    ledger_path: Path | None = None
    memory_state: str = ""
    human_review_status: str = "pending-review"
    approved_for_commit: bool = False
    commit_approval_note: str = ""
    closeout_status: str = "not-started"
    code_commit: str = ""
    memory_content_commit: str = ""
    ledger_commit: str = ""
    integration_status: str = "not-started"
    integration_strategy: str = ""
    integrated_code_commit: str = ""
    integrated_memory_content_commit: str = ""
    integrated_ledger_commit: str = ""
    cleanup: str = "pending"
    kind: str = "leaf"
    leaf_id: str = ""
    parent_task_name: str = ""
    parent_contract_path: Path | None = None
    # The lifecycle this enclosure anchors (design §1.1): written by worktree_start
    # promotion, read by worktree_attach to resume. Additive on schema v1 -- old
    # contracts parse to "" (the v2 schema flip is the deliberate 3.0 cutover).
    lifecycle_id: str = ""
    # Mid-task base syncs (issue #54): one entry per worktree_sync that advanced
    # the recorded base pair. A real dataclass field because the closeout/contract
    # rewrite regenerates the document from this model — freeform contract prose
    # does not survive.
    sync_log: tuple[dict[str, str], ...] = field(default=())


def worktree_folder_name(worktree_name: str) -> str:
    slug = slugify(worktree_name)
    return slug if slug.endswith("-ar") else f"{slug}-ar"


def worktree_group_for(coordination_root: Path, repo_name: str, worktree_name: str) -> Path:
    return coordination_root / "worktrees" / repo_name / worktree_folder_name(worktree_name)


def default_contract(
    *,
    task_name: str,
    repo_name: str,
    workflow_kind: str,
    memory_mode: str,
    coordination_root: Path,
    code_repo_path: Path,
    code_source_branch: str,
    code_work_branch: str,
    code_base_commit: str,
    worktree_name: str,
    memory_repo_path: Path | None = None,
    memory_source_branch: str = "",
    memory_work_branch: str = "",
    memory_base_commit: str = "",
    lifecycle_id: str = "",
    leaf_id: str | None = None,
    parent_task_name: str = "",
    parent_contract_path: Path | None = None,
) -> WorktreeContract:
    if memory_mode not in VALID_MEMORY_MODES:
        raise ContractError(f"memory_mode must be one of {sorted(VALID_MEMORY_MODES)}")
    task_id = slugify(task_name).upper()
    task_root = resolve_active_task_root(coordination_root, repo_name, task_name)
    leaf = leaf_id or worktree_name
    contract_path = leaf_enclosure_path(task_root, leaf)
    task_artifact = task_root / "task.md"
    worktree_group = worktree_group_for(coordination_root, repo_name, worktree_name)
    code_worktree = worktree_group / slugify(worktree_name)
    memory_worktree = (
        worktree_group / f"memory-{slugify(worktree_name)}" if memory_mode == "external" else None
    )
    ledger_path = memory_worktree / "memory.md" if memory_worktree else None
    return WorktreeContract(
        kind="leaf",
        task_id=task_id,
        task_name=task_name,
        repo_name=repo_name,
        workflow_kind=workflow_kind,
        memory_mode=memory_mode,
        coordination_root=coordination_root,
        task_root=task_root,
        contract_path=contract_path,
        task_artifact=task_artifact,
        worktree_group=worktree_group,
        code_repo_path=code_repo_path,
        code_source_branch=code_source_branch,
        code_work_branch=code_work_branch,
        code_base_commit=code_base_commit,
        code_worktree=code_worktree,
        memory_repo_path=memory_repo_path,
        memory_source_branch=memory_source_branch,
        memory_work_branch=memory_work_branch,
        memory_base_commit=memory_base_commit,
        memory_worktree=memory_worktree,
        ledger_path=ledger_path,
        memory_state="disabled" if memory_mode == "disabled" else "",
        lifecycle_id=lifecycle_id,
        leaf_id=slugify(leaf),
        parent_task_name=parent_task_name,
        parent_contract_path=parent_contract_path or series_contract_path(task_root),
    )


def default_series_contract(
    *,
    task_name: str,
    repo_name: str,
    workflow_kind: str,
    memory_mode: str,
    coordination_root: Path,
    code_repo_path: Path,
    protected_branch: str,
    integration_branch: str,
    code_base_commit: str,
    memory_repo_path: Path | None = None,
    memory_source_branch: str = "",
    memory_work_branch: str = "",
    memory_base_commit: str = "",
    parent_task_name: str = "",
    parent_contract_path: Path | None = None,
    task_root: Path | None = None,
) -> WorktreeContract:
    if memory_mode not in VALID_MEMORY_MODES:
        raise ContractError(f"memory_mode must be one of {sorted(VALID_MEMORY_MODES)}")
    task_id = slugify(task_name).upper()
    task_root = task_root or task_root_for(coordination_root, repo_name, task_name)
    contract_path = series_contract_path(task_root)
    memory_ledger = memory_repo_path / "memory.md" if memory_repo_path else None
    return WorktreeContract(
        kind="series",
        task_id=task_id,
        task_name=task_name,
        repo_name=repo_name,
        workflow_kind=workflow_kind,
        memory_mode=memory_mode,
        coordination_root=coordination_root,
        task_root=task_root,
        contract_path=contract_path,
        task_artifact=task_root / "task.md",
        worktree_group=task_root / "enclosures",
        code_repo_path=code_repo_path,
        code_source_branch=protected_branch,
        code_work_branch=integration_branch,
        code_base_commit=code_base_commit,
        code_worktree=code_repo_path,
        memory_repo_path=memory_repo_path,
        memory_source_branch=memory_source_branch,
        memory_work_branch=memory_work_branch,
        memory_base_commit=memory_base_commit,
        ledger_path=memory_ledger,
        parent_task_name=parent_task_name,
        parent_contract_path=parent_contract_path,
    )


def load_contract(path: Path) -> WorktreeContract:
    if not path.exists():
        raise ContractError(f"worktree contract does not exist: {path}")
    front_matter = _extract_front_matter(path.read_text(encoding="utf-8"))
    data = _parse_limited_yaml(front_matter)
    contract = _contract_from_data(data, path)
    validate_contract(contract)
    return contract


def write_contract(path: Path, contract: WorktreeContract) -> None:
    validate_contract(contract)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contract_to_text(contract), encoding="utf-8")


def _memory_lines(contract: WorktreeContract) -> list[str]:
    lines = [
        "memory:",
        f"  mode: {contract.memory_mode}",
    ]
    if contract.memory_repo_path is not None:
        lines.append(f"  repo_path: {contract.memory_repo_path.as_posix()}")
    if contract.memory_source_branch:
        lines.append(f"  source_branch: {contract.memory_source_branch}")
    if contract.memory_work_branch:
        lines.append(f"  work_branch: {contract.memory_work_branch}")
    if contract.memory_base_commit:
        lines.append(f"  base_commit: {contract.memory_base_commit}")
    if contract.memory_worktree is not None:
        lines.append(f"  worktree: {contract.memory_worktree.as_posix()}")
    if contract.ledger_path is not None:
        lines.append(f"  ledger: {contract.ledger_path.as_posix()}")
    if contract.memory_state:
        lines.append(f"  state: {contract.memory_state}")
    return lines


def _sync_lines(contract: WorktreeContract) -> list[str]:
    if not contract.sync_log:
        return []
    # One JSON scalar keeps the limited front-matter parser (scalar one-level
    # sections only) able to round-trip the log.
    return [
        "sync:",
        f"  log: {json.dumps(list(contract.sync_log), separators=(',', ':'))}",
        "",
    ]


def _parse_sync_log(value: str) -> tuple[dict[str, str], ...]:
    if not value:
        return ()
    try:
        entries = json.loads(value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(entries, list):
        return ()
    return tuple(entry for entry in entries if isinstance(entry, dict))


def _human_review_lines(contract: WorktreeContract, approved: str) -> list[str]:
    lines = [
        "human_review:",
        f"  status: {contract.human_review_status}",
        f"  approved_for_commit: {approved}",
    ]
    if contract.commit_approval_note:
        lines.append(f"  commit_approval_note: {contract.commit_approval_note}")
    return lines


def _closeout_lines(contract: WorktreeContract) -> list[str]:
    lines = [
        "closeout:",
        f"  status: {contract.closeout_status}",
    ]
    if contract.code_commit:
        lines.append(f"  code_commit: {contract.code_commit}")
    if contract.memory_content_commit:
        lines.append(f"  memory_content_commit: {contract.memory_content_commit}")
    if contract.ledger_commit:
        lines.append(f"  ledger_commit: {contract.ledger_commit}")
    return lines


def _integration_lines(contract: WorktreeContract) -> list[str]:
    lines = [
        "integration:",
        f"  status: {contract.integration_status}",
    ]
    if contract.integration_strategy:
        lines.append(f"  strategy: {contract.integration_strategy}")
    if contract.integrated_code_commit:
        lines.append(f"  code_commit: {contract.integrated_code_commit}")
    if contract.integrated_memory_content_commit:
        lines.append(f"  memory_content_commit: {contract.integrated_memory_content_commit}")
    if contract.integrated_ledger_commit:
        lines.append(f"  ledger_commit: {contract.integrated_ledger_commit}")
    lines.append(f"  cleanup: {contract.cleanup}")
    return lines


def _contract_body_lines(contract: WorktreeContract, approved: str) -> list[str]:
    lines = [
        f"# Series Contract - {contract.task_id}",
        "",
        "## Wrapped Workflow",
        "",
        f"Artifact: `{contract.task_artifact.as_posix()}`",
        "",
        "## Human Review State",
        "",
        f"- Status: {contract.human_review_status}",
        f"- Approved for commit: {approved}",
    ]
    if contract.commit_approval_note:
        lines.append(f"- Commit approval note: {contract.commit_approval_note}")
    lines.append("")
    return lines


def contract_to_text(contract: WorktreeContract) -> str:
    approved = "yes" if contract.approved_for_commit else "no"
    lines = [
        "---",
        f"schema: {CONTRACT_SCHEMA}",
        f"kind: {contract.kind}",
        f"task_id: {contract.task_id}",
        f"task_name: {contract.task_name}",
        f"repo_name: {contract.repo_name}",
        f"workflow_kind: {contract.workflow_kind}",
        f"memory_mode: {contract.memory_mode}",
        "",
        "coordination:",
        f"  root: {contract.coordination_root.as_posix()}",
        f"  task_root: {contract.task_root.as_posix()}",
        f"  series_contract_path: {contract.contract_path.as_posix()}",
        f"  task_artifact: {contract.task_artifact.as_posix()}",
        f"  worktree_group: {contract.worktree_group.as_posix()}",
        f"  leaf_id: {contract.leaf_id}",
    ]
    if contract.parent_task_name:
        lines.append(f"  parent_task_name: {contract.parent_task_name}")
    if contract.parent_contract_path is not None:
        lines.append(f"  parent_contract_path: {contract.parent_contract_path.as_posix()}")
    lines.extend(
        [
            "",
            "code:",
            f"  repo_path: {contract.code_repo_path.as_posix()}",
            f"  source_branch: {contract.code_source_branch}",
            f"  work_branch: {contract.code_work_branch}",
            f"  base_commit: {contract.code_base_commit}",
            f"  worktree: {contract.code_worktree.as_posix()}",
            "",
            "lifecycle:",
            f"  id: {contract.lifecycle_id}",
            "",
            *_memory_lines(contract),
            "",
            *_sync_lines(contract),
            *_human_review_lines(contract, approved),
            "",
            *_closeout_lines(contract),
            "",
            *_integration_lines(contract),
            "---",
            "",
            *_contract_body_lines(contract, approved),
        ]
    )
    return "\n".join(lines)


def validate_contract(contract: WorktreeContract) -> None:
    missing = [
        name
        for name, value in {
            "task_id": contract.task_id,
            "task_name": contract.task_name,
            "repo_name": contract.repo_name,
            "workflow_kind": contract.workflow_kind,
            "memory_mode": contract.memory_mode,
            "code_source_branch": contract.code_source_branch,
            "code_work_branch": contract.code_work_branch,
            "code_base_commit": contract.code_base_commit,
        }.items()
        if not value
    ]
    if missing:
        raise ContractError(f"contract missing required fields: {', '.join(missing)}")
    if contract.kind not in VALID_KINDS:
        raise ContractError(f"invalid contract kind: {contract.kind}")
    if contract.memory_mode not in VALID_MEMORY_MODES:
        raise ContractError(f"invalid memory mode: {contract.memory_mode}")
    if contract.kind == "leaf" and not contract.leaf_id:
        raise ContractError("leaf contract missing leaf_id")
    if contract.memory_mode == "external" and contract.kind == "leaf":
        for name, value in {
            "memory_repo_path": contract.memory_repo_path,
            "memory_worktree": contract.memory_worktree,
            "ledger_path": contract.ledger_path,
        }.items():
            if value is None:
                raise ContractError(f"external-memory contract missing {name}")


def _extract_front_matter(text: str) -> str:
    if not text.startswith("---\n"):
        raise ContractError(f"{SERIES_CONTRACT_FILENAME} must start with a front matter block")
    end = text.find("\n---", 4)
    if end == -1:
        raise ContractError(f"{SERIES_CONTRACT_FILENAME} front matter is not closed")
    return text[4:end]


def _parse_limited_yaml(text: str) -> dict[str, object]:
    data: dict[str, object] = {}
    current_section: dict[str, str] | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        if raw_line.startswith("  "):
            if current_section is None or ":" not in raw_line:
                continue
            key, value = raw_line.strip().split(":", 1)
            current_section[key.strip()] = value.strip()
            continue
        current_section = None
        if raw_line.endswith(":"):
            section: dict[str, str] = {}
            data[raw_line[:-1].strip()] = section
            current_section = section
            continue
        if ":" in raw_line:
            key, value = raw_line.split(":", 1)
            data[key.strip()] = value.strip()
    return data


def _section(data: dict[str, object], name: str) -> dict[str, str]:
    value = data.get(name)
    return value if isinstance(value, dict) else {}


def _path(value: str) -> Path:
    if not value:
        raise ContractError("required contract path field is empty")
    return Path(value)


def _optional_path(value: str) -> Path | None:
    return Path(value) if value else None


def _contract_from_data(data: dict[str, object], contract_path: Path) -> WorktreeContract:
    if data.get("schema") != CONTRACT_SCHEMA:
        raise ContractError(f"unsupported series contract schema: {data.get('schema', '')}")
    coordination = _section(data, "coordination")
    code = _section(data, "code")
    memory = _section(data, "memory")
    sync = _section(data, "sync")
    human_review = _section(data, "human_review")
    closeout = _section(data, "closeout")
    integration = _section(data, "integration")
    lifecycle = _section(data, "lifecycle")
    memory_mode = str(data.get("memory_mode") or memory.get("mode") or "").strip()
    path = (
        _optional_path(coordination.get("series_contract_path", ""))
        or _optional_path(coordination.get("enclosure_path", ""))
        or _optional_path(coordination.get("contract_path", ""))
        or contract_path
    )
    return WorktreeContract(
        kind=str(data.get("kind", "leaf")).strip() or "leaf",
        task_id=str(data.get("task_id", "")).strip(),
        task_name=str(data.get("task_name", "")).strip(),
        repo_name=str(data.get("repo_name", "")).strip(),
        workflow_kind=str(data.get("workflow_kind", "")).strip(),
        memory_mode=memory_mode,
        coordination_root=_path(coordination.get("root", "")),
        task_root=_path(coordination.get("task_root", "")),
        contract_path=path,
        task_artifact=_path(coordination.get("task_artifact", "")),
        worktree_group=_path(coordination.get("worktree_group", "")),
        code_repo_path=_path(code.get("repo_path", "")),
        code_source_branch=code.get("source_branch", ""),
        code_work_branch=code.get("work_branch", ""),
        code_base_commit=code.get("base_commit", ""),
        code_worktree=_path(code.get("worktree", "")),
        memory_repo_path=_optional_path(memory.get("repo_path", "")),
        memory_source_branch=memory.get("source_branch", ""),
        memory_work_branch=memory.get("work_branch", ""),
        memory_base_commit=memory.get("base_commit", ""),
        memory_worktree=_optional_path(memory.get("worktree", "")),
        ledger_path=_optional_path(memory.get("ledger", "")),
        memory_state=memory.get("state", ""),
        human_review_status=human_review.get("status", "pending-review"),
        approved_for_commit=human_review.get("approved_for_commit", "no").lower()
        in {"yes", "true", "1"},
        commit_approval_note=human_review.get("commit_approval_note", ""),
        closeout_status=closeout.get("status", "not-started"),
        code_commit=closeout.get("code_commit", ""),
        memory_content_commit=closeout.get("memory_content_commit", ""),
        ledger_commit=closeout.get("ledger_commit", ""),
        integration_status=integration.get("status", "not-started"),
        integration_strategy=integration.get("strategy", ""),
        integrated_code_commit=integration.get("code_commit", ""),
        integrated_memory_content_commit=integration.get("memory_content_commit", ""),
        integrated_ledger_commit=integration.get("ledger_commit", ""),
        cleanup=integration.get("cleanup", closeout.get("cleanup", "pending")),
        leaf_id=coordination.get("leaf_id", ""),
        parent_task_name=coordination.get("parent_task_name", ""),
        parent_contract_path=_optional_path(coordination.get("parent_contract_path", "")),
        lifecycle_id=lifecycle.get("id", ""),
        sync_log=_parse_sync_log(sync.get("log", "")),
    )
