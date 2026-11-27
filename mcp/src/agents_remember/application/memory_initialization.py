"""Initialise a memory root and describe its knowledge foundation."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from agents_remember.application.file_briefs.triggers import refresh_written_memory_folder
from agents_remember.application.knowledge_bootstrap_admission import (
    BootstrapRefusal,
    admit_bootstrap_context,
)
from agents_remember.application.published_intent import (
    PublishedIntentUnavailable,
    resolve_published_intent,
)
from agents_remember.kernel.memory_init import initialize_memory
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge_index import KnowledgeIndex
from agents_remember.memory_quality.style.citations.language_scanner import (
    warm_repository_languages,
)
from agents_remember.models.knowledge_files.records import RECORD_MODELS
from agents_remember.worktrees.cutover_lock import LEGACY_FORMAT_CODE, legacy_format_refusal
from agents_remember.worktrees.knowledge_validation import PairedCode
from agents_remember.worktrees.modules.git import branch_commit

# The foundation handover shared by the two knowledge-state reporting branches below.
KNOWLEDGE_BOOTSTRAP_ROUTE = (
    "report knowledge state {state!r}, then have the developer start the Architect, which runs "
    "the knowledge foundation as a regular task with a Curator"
)


def memory_init_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    dry_run: bool = False,
    initialize_git: bool = True,
    initial_branch: str | None = None,
) -> dict[str, Any]:
    """Scaffold a repository's memory root, and report what its knowledge foundation is.

    The scaffold this calls has never created knowledge storage, and that is correct: a memory root
    is a *place*, while the knowledge dataset is an authored result no initializer may invent. What
    was missing is the other half of that statement -- the initializer said nothing at all about
    where knowledge will live, so "the normal memory initializer never creates knowledge storage"
    read as "the product has no knowledge step" rather than as "the next step is the curator's
    bootstrap". The ``knowledge`` block below is that half, and it is a **measurement**: the location
    is the one the ordinary read route selects, its state is what a read of that location finds now
    (``not-recorded``, ``recorded`` or ``unusable``), and a location whose context cannot be admitted
    yet reports the admission's own refusal rather than a hopeful path.
    """

    result = initialize_memory(
        config,
        repo_id=repo_id,
        dry_run=dry_run,
        initialize_git=initialize_git,
        initial_branch=initial_branch,
    )
    if (
        not dry_run
        and result.get("ok")
        and (result["createdFiles"] or result["createdDirs"] or result["git"].get("ran"))
    ):
        problem = _refresh_initialized_memory(config, repo_id, result)
        if problem is not None:
            result["briefsProblem"] = problem
    result["knowledge"] = _knowledge_foundation_state(config, repo_id)
    if result.get("ok") and repo_id in config.repositories:
        result["languages"] = _warm_repo_languages_summary(
            config.repositories[repo_id].path, dry_run=dry_run
        )
    return result


def _warm_repo_languages_summary(code_root: Path, *, dry_run: bool) -> dict[str, Any]:
    report = warm_repository_languages(code_root, dry_run=dry_run)
    summary: dict[str, Any] = {
        "detected": list(report.scan.detected),
        "warmed": list(report.warmed),
        "alreadyCached": list(report.already_cached),
    }
    if report.failed:
        summary["failed"] = dict(report.failed)
    return summary


def _refresh_initialized_memory(
    config: McpRuntimeConfig, repo_id: str, result: dict[str, Any]
) -> str | None:
    memory_root = Path(result["memoryRoot"])
    if result["layoutMarker"] not in {"created", "present"}:
        return None
    code_root = config.repositories[repo_id].path
    branch = result["initialBranch"]
    try:
        code = branch_commit(code_root, branch)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        return f"The briefs could not be written from code branch {branch!r} ({error})."
    writing = refresh_written_memory_folder(
        memory_root, PairedCode(code_root, code), config.coordination_root
    )
    return writing if isinstance(writing, str) else None


def _knowledge_foundation_state(config: McpRuntimeConfig, repo_id: str) -> dict[str, Any]:
    """Where this repository's knowledge foundation lives, and what is there now.

    The admission used here is the bootstrap's own, so the location a curator is told to populate is
    the location the bootstrap publishes to, which is the location the ordinary read route selects.
    Nothing here writes: an initializer that created knowledge would be inventing authored content,
    and the whole point of the split is that it does not.
    """

    admitted = admit_bootstrap_context(config, repo_id)
    if isinstance(admitted, BootstrapRefusal):
        return {
            "state": "context-not-admitted",
            "datasetPath": None,
            "code": admitted.code,
            "detail": admitted.detail,
            "nextAction": admitted.next_action,
        }
    resolved = resolve_published_intent(admitted.context)
    if isinstance(resolved, PublishedIntentUnavailable):
        return {
            "state": resolved.state,
            "datasetPath": resolved.dataset_path.as_posix(),
            "code": resolved.code,
            "detail": resolved.detail,
            "nextAction": (
                legacy_format_refusal(
                    admitted.context.memory_root,
                    operation="knowledge-bootstrap",
                    subject="this memory root",
                )
                if resolved.code == LEGACY_FORMAT_CODE
                else KNOWLEDGE_BOOTSTRAP_ROUTE.format(state=resolved.state)
            ),
        }
    tree = resolved.memory_tree
    assert tree is not None
    with KnowledgeIndex(resolved.database_path, expected_key=tree.tree_key) as index:
        has_records = any(index.record_ids(kind).value for kind in RECORD_MODELS)
    state = (
        "unusable"
        if tree.index_state == "partial"
        else "recorded"
        if has_records
        else "not-recorded"
    )
    return {
        "state": state,
        "datasetPath": resolved.database_path.as_posix(),
        "code": "snapshot_unavailable" if state == "unusable" else None,
        "detail": (
            f"the memory tree {tree.tree_key} has a partial index: {tree.problems[:3]}"
            if state == "unusable"
            else f"text knowledge records at {admitted.context.memory_root} are indexed for memory tree {tree.tree_key}"
            if has_records
            else f"the complete memory tree {tree.tree_key} records no authored knowledge at {admitted.context.memory_root}"
        ),
        "nextAction": (
            "repair the named knowledge files, then rebuild their derived index"
            if state == "unusable"
            else KNOWLEDGE_BOOTSTRAP_ROUTE.format(state=state)
        ),
    }
