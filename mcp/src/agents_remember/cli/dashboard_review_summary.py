"""Bind the native summary's dependency observations without a serving/application import edge."""

from __future__ import annotations

from functools import partial
from pathlib import Path

from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    recorded_leaf_contract,
    review_context_refusal,
)
from agents_remember.application.review_committed_leaf import resolve_review_comparison
from agents_remember.application.review_comparison_generation import leaf_generation_root
from agents_remember.application.review_intent_summary import summarize_review
from agents_remember.application.review_tree_comparison import comparison_directory
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.changeset_cache import (
    ContentRead,
    DirectoryRead,
    ReadInput,
    RepositoryRead,
    observe_repository_capture,
)
from agents_remember.serving.intent_summary_cache import Observe, ReviewIntentSummaryPort
from agents_remember.worktrees.modules.capture_baseline import memo_capture_baselines


def _repository(path: Path, *, working: bool = False) -> ReadInput:
    # Absence must be observed too: creation can change the native resolution.
    return RepositoryRead(path, working=working, references=("HEAD",)) if path.exists() else path


def _initial(
    config: McpRuntimeConfig, repo: str, master: str, leaf: str, number: int | None
) -> tuple[ReadInput, ...]:
    if review_context_refusal(repo, master, leaf) is not None:
        return ()  # The native reader returns its unchanged refusal; follow no invalid path.
    task_root = config.coordination_root / "tasks" / repo / master
    contract = recorded_leaf_contract(config, repo, master, leaf)
    selection = DirectoryRead(task_root / "enclosures")
    if contract is None:
        return (selection,)
    inputs: list[ReadInput] = [
        selection,
        contract.contract_path,
        contract.contract_path.parent / "knowledge-worklist.json",
        _repository(contract.code_repo_path),
    ]
    if number is not None:
        # A numbered summary reads that record whether the leaf is live or not (MIK-R89 v2 rule
        # 10), so the comparison directory, which decides whether the number is recorded at all,
        # is an input of it as it is of a closed leaf's.
        inputs.append(comparison_directory(task_root, leaf))
    if contract.code_worktree is None or not contract.code_worktree.exists():
        inputs.extend(
            (comparison_directory(task_root, leaf), leaf_generation_root(task_root, leaf))
        )
    for path in (contract.code_worktree, contract.memory_worktree):
        if path is not None:
            inputs.append(_repository(path, working=True))
    if contract.memory_repo_path is not None:
        inputs.append(_repository(contract.memory_repo_path))
    return tuple(dict.fromkeys(inputs))


def _resolved(resolved: ReviewCandidateResolution) -> tuple[ReadInput, ...]:
    inputs: list[ReadInput] = [
        _repository(path)
        for path in (resolved.baseline_code_root, resolved.candidate_code_root)
        if path is not None
    ]
    if resolved.trees is not None:
        inputs.append(_repository(resolved.trees.memory_repository))
        if resolved.trees.record_path is not None:
            inputs.append(ContentRead(resolved.trees.record_path))
    for path in (resolved.baseline_database, resolved.candidate_database):
        # Native derived-index reuse touches timestamps. Its bytes, not its use time,
        # determine the summary. Missing files remain explicit absence observations.
        inputs.append(ContentRead(path) if resolved.trees is not None or path.is_file() else path)
    return tuple(dict.fromkeys(inputs))


def review_intent_summary_port(config: McpRuntimeConfig) -> ReviewIntentSummaryPort:
    def read(repo: str, master: str, leaf: str, number: int | None, observe: Observe):
        # The leaf's own resolution, or its recorded comparison ``number`` (MIK-R89 v2 rule 10).
        resolve = partial(resolve_review_comparison, config, repo, master, leaf, number)
        with memo_capture_baselines(on_capture=observe_repository_capture):
            return summarize_review(
                resolve, repo, master, leaf, observe=lambda resolved: observe(_resolved(resolved))
            )

    return ReviewIntentSummaryPort(
        inputs=lambda repo, master, leaf, number: _initial(config, repo, master, leaf, number),
        read=read,
    )
