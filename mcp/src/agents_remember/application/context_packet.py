"""Application entry point for the versioned Agents Remember context packet."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.memory_quality.converted_base import context_check_base
from agents_remember.application.worktree_status import worktree_status_packet
from agents_remember.errors import AuthorityError
from agents_remember.kernel.coordination_context.models import CoordinationRequest
from agents_remember.kernel.coordination_context_resolver import (
    CoordinationHints,
    EnclosureSelector,
    context_to_dict,
    resolve_coordination_context,
)
from agents_remember.kernel.git_facts import (
    GitFacts,
    git_facts_to_packet,
    read_git_facts,
)
from agents_remember.kernel.git_freshness import (
    freshness_to_packet,
    read_branch_freshness,
)
from agents_remember.kernel.memory_ledger import LedgerError, find_mapping, load_ledger
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import (
    DriftSummaryPacket,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    DriftSummaryOutput,
    not_checked,
    run_drift_summary,
)
from agents_remember.memory_quality.reference_state import is_converted_memory
from agents_remember.models.context_packet import (
    ContextPacketV2,
    ContextPaths,
    CrossRepoSummary,
    FreshnessSummary,
    MemorySummary,
    RepoSummary,
    StorageSummary,
)
from agents_remember.models.drift import DriftSummary
from agents_remember.models.providers import ProviderSummary
from agents_remember.providers.status import provider_summary_packet
from agents_remember.worktrees.cutover_lock import legacy_format_refusal
from agents_remember.worktrees.modules.contract_reader import WorktreeContractReader

CONTEXT_PACKET_VERSION = 2

#: The slim agent answer's version: roots, task, actions and short values.
SLIM_CONTEXT_PACKET_VERSION = 3

_GIT_TIMEOUT_SECONDS = 20


class ContextPacketError(AuthorityError):
    """Raised when the context packet request violates MCP authority settings."""


@dataclass(frozen=True)
class ContextPacketRequest:
    repo_id: str
    include_drift: bool = False
    include_freshness: bool = False
    drift_detail_limit: int = 10
    fetch_timeout: int = 30


def build_context_packet(
    config: McpRuntimeConfig,
    request: ContextPacketRequest,
) -> dict[str, Any]:
    repo_scope = _repo_scope(config, request.repo_id)
    context = resolve_coordination_context(
        code_repository_name=repo_scope.repo_id,
        workspace_root=config.workspace_root,
        code_repository_root=repo_scope.path,
        request=CoordinationRequest(
            hints=CoordinationHints(
                coordination_root=config.coordination_root,
                onboarding_root=(repo_scope.memory_root / "onboarding")
                if repo_scope.memory_root
                else None,
            ),
            selector=EnclosureSelector(contract_path=repo_scope.contract_path),
            contract_reader=WorktreeContractReader(),
        ),
    )
    context_dict = context_to_dict(context)
    git_facts = read_git_facts(repo_scope.repo_id, repo_scope.path)

    packet = ContextPacketV2(
        ok=git_facts.state in {"available", "detached"},
        repo=RepoSummary.model_validate(git_facts_to_packet(git_facts)),
        paths=_context_paths(context_dict),
        memory=_memory_summary(context_dict),
        # Not `model_validate(...)`: the projection returns the model, so a value the
        # worktree state machine can emit and this block cannot accept is now a type error
        # in `application.worktree_status` rather than a ValidationError raised from inside the
        # `context_packet` tool handler, where nothing catches it.
        worktree=worktree_status_packet(config, context.contract_path),
        providers=ProviderSummary.model_validate(
            provider_summary_packet(config),
        ),
        drift=DriftSummary.model_validate(_drift_packet(request, context, repo_scope)),
        freshness=FreshnessSummary.model_validate(
            _freshness_packet(request, context_dict, repo_scope, git_facts)
        ),
    )
    full = packet.model_dump(mode="json", exclude_none=True)
    index_block = _memory_index_block(config, context)
    if index_block is not None:
        full["index"] = index_block
    return full


def _memory_index_block(config: McpRuntimeConfig, context: Any) -> dict[str, Any] | None:
    """The memory knowledge index's state for action lines, or ``None``.

    A partial index is actionable: the caller must repair and reread
    before trusting counts. The observation never breaks the packet --
    an unreadable tree simply omits the block -- and reuses the derived
    index cache, so repeat calls do not rebuild.
    """

    from agents_remember.application.published_intent import (  # noqa: PLC0415
        select_knowledge_dataset,
    )
    from agents_remember.models.knowledge_files.documents import (  # noqa: PLC0415
        LAYOUT_MARKER_PATH,
    )

    try:
        memory_root = context.onboarding_root.parent
    except AttributeError:
        return None
    if memory_root is None or not (memory_root / LAYOUT_MARKER_PATH).is_file():
        return None
    try:
        selected = select_knowledge_dataset(
            memory_root, coordination_root=Path(config.coordination_root)
        )
    except Exception:
        return None
    tree = selected.memory_tree
    if tree is None:
        return None
    return {
        "state": tree.index_state,
        "problems": [{"path": path, "detail": detail} for path, detail in tree.problems],
    }


def slim_context_packet(
    full: dict[str, Any], *, serving_build_commit: str | None = None
) -> dict[str, Any]:
    """The agent's answer from one whole context packet (MIK-R109 rule 4).

    Where the caller works and what needs action: the roots (the code
    worktree absolute, the memory worktree, the enclosure and the rendered
    task document code-relative when shorter, else absolute, joined with
    the code root), the task (its ID and its master), one line for each state
    that needs action and none when nothing does, the serving build as its
    commit, and the pair identity as its two commits. An ordinary answer is
    under 600 tokens. The whole packet stays whole behind the answer's log.
    """

    worktree_raw = full.get("worktree")
    worktree: dict[str, Any] = worktree_raw if isinstance(worktree_raw, dict) else {}
    paths_raw = full.get("paths")
    paths: dict[str, Any] = paths_raw if isinstance(paths_raw, dict) else {}
    repo_raw = full.get("repo")
    repo: dict[str, Any] = repo_raw if isinstance(repo_raw, dict) else {}
    roots: dict[str, Any] = {
        "code": worktree.get("codeWorktree") or repo.get("root"),
        "memory": worktree.get("memoryWorktree") or paths.get("memoryRoot"),
    }
    enclosure = worktree.get("worktreeGroup")
    if isinstance(enclosure, str) and enclosure:
        roots["enclosure"] = enclosure
    task_document, task = _leaf_task(worktree)
    if task_document is not None:
        roots["taskDocument"] = task_document
    code_head = repo.get("head") if isinstance(repo.get("head"), str) else None
    memory_head = _git_head(roots["memory"]) if isinstance(roots.get("memory"), str) else None
    roots = _compact_roots(roots)
    slim: dict[str, Any] = {
        "ok": full.get("ok", False),
        "contextPacketVersion": SLIM_CONTEXT_PACKET_VERSION,
        "roots": roots,
    }
    if task is not None:
        slim["task"] = task
    actions = _action_lines(full)
    if actions:
        slim["actions"] = actions
    if serving_build_commit:
        slim["servingBuild"] = serving_build_commit
    if code_head and memory_head:
        slim["pair"] = {"code": code_head, "memory": memory_head}
    return slim


def _compact_roots(roots: dict[str, Any]) -> dict[str, Any]:
    """The slim roots with shared prefixes factored out (MIK-R109 rule 4).

    ``code`` stays the absolute anchor. The memory worktree, the enclosure
    and the rendered task document are code-relative when that is shorter
    (they usually share the coordination root), otherwise absolute; the
    caller joins them with ``roots.code``. Either form names the same
    directory, so the packet keeps its ordinary bound on any root length.
    """

    code = roots.get("code")
    if not isinstance(code, str) or not code:
        return roots
    compacted: dict[str, Any] = {"code": code}
    for key in ("memory", "enclosure", "taskDocument"):
        value = roots.get(key)
        if not isinstance(value, str) or not value:
            continue
        try:
            relative = os.path.relpath(value, code)
        except (OSError, ValueError):
            compacted[key] = value
            continue
        compacted[key] = relative if len(relative) < len(value) else value
    return compacted


def _leaf_task(
    worktree: dict[str, Any],
) -> tuple[str | None, dict[str, str] | None]:
    """The rendered task document and the task block, or neither without a leaf.

    The contract names the rendered document (``task_artifact``) and the
    master (``parent_task_name``); the leaf ID comes from the worktree
    block. Anything unreadable omits both rather than guessing.
    """

    if worktree.get("state") != "active":
        return None, None
    contract_path = worktree.get("contractPath")
    if not isinstance(contract_path, str) or not contract_path:
        return None, None
    try:
        from agents_remember.worktrees.worktree_contract import (  # noqa: PLC0415
            load_contract,
        )

        contract = load_contract(Path(contract_path))
    except Exception:
        return None, None
    task_document = None
    try:
        if contract.task_artifact.is_file():
            task_document = contract.task_artifact.as_posix()
    except OSError:
        task_document = None
    leaf_id = worktree.get("leafId") or contract.leaf_id or contract.task_id
    if not isinstance(leaf_id, str) or not leaf_id or not contract.parent_task_name:
        return task_document, None
    return task_document, {"id": leaf_id, "master": contract.parent_task_name}


def _git_head(directory: str) -> str | None:
    """The HEAD commit of one checkout, best effort (a read, never a fetch)."""

    try:
        completed = subprocess.run(
            ["git", "-C", directory, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    head = completed.stdout.strip()
    return head if completed.returncode == 0 and head else None


def _action_lines(full: dict[str, Any]) -> list[str]:
    """One line for each state of the packet that needs action (MIK-R109 rule 4)."""

    actions: list[str] = []
    worktree_raw = full.get("worktree")
    worktree: dict[str, Any] = worktree_raw if isinstance(worktree_raw, dict) else {}
    lineage_raw = worktree.get("sourceLineage")
    lineage: dict[str, Any] = lineage_raw if isinstance(lineage_raw, dict) else {}
    for edge in lineage.get("edges", []) or []:
        if not isinstance(edge, dict):
            continue
        behind = edge.get("behind")
        if isinstance(behind, int) and behind > 0:
            actions.append(
                f"the {edge.get('descendantBranch')} worktree is {behind} behind "
                f"{edge.get('sourceBranch')}"
            )
    freshness_raw = full.get("freshness")
    freshness: dict[str, Any] = freshness_raw if isinstance(freshness_raw, dict) else {}
    for side in ("code", "memory"):
        block_raw = freshness.get(side)
        block: dict[str, Any] = block_raw if isinstance(block_raw, dict) else {}
        behind = block.get("behind")
        if isinstance(behind, int) and behind > 0:
            actions.append(f"the {side} checkout is {behind} behind its upstream")
    drift_raw = full.get("drift")
    drift: dict[str, Any] = drift_raw if isinstance(drift_raw, dict) else {}
    actionable = drift.get("actionableCount")
    if isinstance(actionable, int) and actionable > 0:
        line = f"{actionable} onboarding cards drifted"
        report_path = drift.get("reportPath")
        if isinstance(report_path, str) and report_path:
            line += f"; report at {report_path}"
        actions.append(line)
    index_raw = full.get("index")
    index: dict[str, Any] = index_raw if isinstance(index_raw, dict) else {}
    if index.get("state") == "partial":
        problems = index.get("problems")
        skipped = len(problems) if isinstance(problems, list) else 0
        actions.append(
            f"the memory knowledge index is partial ({skipped} files skipped); "
            "run knowledge_integrity_check for the skipped files, then reread"
        )
    return actions


def _freshness_packet(
    request: ContextPacketRequest,
    context_dict: dict[str, Any],
    repo_scope: RepositoryScope,
    git_facts: GitFacts,
) -> dict[str, Any]:
    if not request.include_freshness:
        return {"status": "not-checked"}
    packet: dict[str, Any] = {
        "status": "checked",
        "code": freshness_to_packet(
            read_branch_freshness(repo_scope.path, fetch_timeout=request.fetch_timeout)
        ),
    }
    memory_root = repo_scope.memory_root
    if memory_root and (memory_root / ".git").exists():
        packet["memory"] = freshness_to_packet(
            read_branch_freshness(memory_root, fetch_timeout=request.fetch_timeout)
        )
    ledger_path = context_dict.get("ledger_path")
    if ledger_path and git_facts.head and Path(ledger_path).is_file():
        try:
            ledger = load_ledger(Path(ledger_path))
        except (LedgerError, OSError) as error:
            packet["ledgerError"] = str(error)
        else:
            packet["ledgerMapsCodeHead"] = find_mapping(ledger, git_facts.head) is not None
    return packet


def _context_paths(context_dict: dict[str, Any]) -> ContextPaths:
    return ContextPaths(
        coordinationRoot=context_dict["coordination_root"],
        taskRoot=context_dict["task_root"],
        tempRoot=context_dict["temp_root"],
        memoryRoot=context_dict["memory_root"],
        onboardingRoot=context_dict["onboarding_root"],
        ledgerPath=context_dict["ledger_path"],
        settingsPath=context_dict["settings_path"],
        pathSettingsPath=context_dict["path_settings_path"],
        systemRoot=context_dict["system_root"],
        sourcesPath=context_dict["sources_path"],
        toolsPath=context_dict["tools_path"],
        docsRoot=context_dict["docs_root"],
    )


def _memory_summary(context_dict: dict[str, Any]) -> MemorySummary:
    return MemorySummary(
        mode=context_dict["memory_mode"],
        storage=StorageSummary.model_validate(context_dict["storage"]),
        crossRepo=CrossRepoSummary.model_validate(context_dict["crossRepo"]),
    )


def _repo_scope(config: McpRuntimeConfig, repo_id: str) -> RepositoryScope:
    if repo_id not in config.repositories:
        allowed = ", ".join(config.allowed_repo_ids) or "<none>"
        raise ContextPacketError(
            f"repoId {repo_id!r} is not allowed by MCP settings; allowed: {allowed}"
        )
    return config.repositories[repo_id]


def _leaf_drift_base(repo_scope: RepositoryScope, context: Any) -> Any:
    """Return the converted comparison base for a leaf context, else `None`.

    A context that names an enclosure contract compares against the
    application-owned converted base for that leaf (L44-C1-05), so this route
    agrees with the drift tool on the same candidate and contract. A context
    without a contract keeps today's semantics: no separate base.
    """

    contract_path = getattr(context, "contract_path", None)
    if not isinstance(contract_path, Path) or not contract_path.is_file():
        return None
    return context_check_base(repo_scope.path, context)


def _drift_packet(
    request: ContextPacketRequest,
    context: Any,
    repo_scope: RepositoryScope,
) -> DriftSummaryPacket:
    if not request.include_drift:
        return not_checked()
    # MIK-R46 rules 1 and 2: the drift block on unconverted memory is status "error" with
    # the one rule-2 message; it is not measured in the old format. A root that cannot be
    # told (missing) keeps its own error from the summary below.
    memory_root = getattr(context, "memory_root", None) or repo_scope.memory_root
    if memory_root is not None:
        memory_path = memory_root if isinstance(memory_root, Path) else Path(str(memory_root))
        if memory_path.is_dir() and not is_converted_memory(memory_path):
            return {
                "status": "error",
                "error": legacy_format_refusal(
                    memory_path,
                    operation="context_packet",
                    subject=f"the memory tree {memory_path.as_posix()}",
                ),
            }
    # The packet's drift section is read-only content: no temp report is filed (rule 6).
    return run_drift_summary(
        code_repository_root=repo_scope.path,
        context=context,
        detail_limit=request.drift_detail_limit,
        base=_leaf_drift_base(repo_scope, context),
        output=DriftSummaryOutput(write_report=False),
    )
