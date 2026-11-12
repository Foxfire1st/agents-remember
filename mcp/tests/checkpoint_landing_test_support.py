"""Real temporary Git world for the checkpoint-landing boundary proofs.

One authoritative builder for the master/leaf series world that
``test_checkpoint_landing_end_to_end.py`` drives through the public operations. These helpers
construct state on real repositories and read it back; they assert nothing about the routes,
so the cases keep the whole claim.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from agents_remember.application import worktree_tools
from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.kernel.memory_cache import prepare_memory_cache, refresh_memory_cache
from agents_remember.kernel.memory_ledger import LedgerRow
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.knowledge_gate import close_owner_history
from agents_remember.worktrees.modules import integrate as integrate_module
from agents_remember.worktrees.modules import sync as sync_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from test_closeout_queue import QueueFixture
from test_worktree_support import git


def rev(repository: Path, branch: str) -> str:
    return git(repository, "rev-parse", branch)


def memory_repository(series: WorktreeContract) -> Path:
    """The external-memory repository this series contract must have."""

    assert series.memory_repo_path is not None
    return series.memory_repo_path


def payload_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload[key]
    assert isinstance(value, str), (key, value)
    return value


@contextlib.contextmanager
def branch_checkout(repository: Path, branch: str, scratch: Path) -> Iterator[Path]:
    """A disposable checkout of one branch, so a commit can be authored on it directly."""

    scratch.mkdir(parents=True, exist_ok=True)
    git(repository, "worktree", "add", scratch.as_posix(), branch)
    try:
        yield scratch
    finally:
        git(repository, "worktree", "remove", "--force", scratch.as_posix())


def close_out_leaf(leaf: WorktreeContract) -> WorktreeContract:
    """Record the real code and attributed memory commits authored in the leaf's worktrees."""

    code_worktree = leaf.code_worktree
    memory_worktree = leaf.memory_worktree
    assert memory_worktree is not None
    git(code_worktree, "add", "-A")
    git(code_worktree, "commit", "-m", "Leaf code")
    code_commit = git(code_worktree, "rev-parse", "HEAD")
    # The leaf's publication closes its own history (MIK-R07 rule 7): the close is the
    # product's own write, committed inside the memory content below, so a later
    # master-level landing finds every history file closed.
    assert leaf.leaf_id is not None
    close_owner_history(memory_worktree, leaf.leaf_id)
    prepare_memory_cache(memory_worktree)
    git(memory_worktree, "add", "-A")
    git(memory_worktree, "commit", "-m", render_memory_content_message("Leaf memory", code_commit))
    memory_content = git(memory_worktree, "rev-parse", "HEAD")
    refresh_memory_cache(memory_worktree, repo_name=leaf.repo_name)
    closed = replace(
        leaf,
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=code_commit,
        memory_content_commit=memory_content,
    )
    write_contract(closed.contract_path, closed)
    return closed


def _open_history_attempt(history_dir: Path, leaf_id: str) -> Path:
    """The history file the leaf's next rows belong in: its latest open attempt.

    A closed attempt is frozen, so rows follow it into the next attempt file, named
    ``<leaf>-attempt-<n>.json`` like the product names it.
    """

    attempt = 1
    while True:
        path = (
            history_dir / f"{leaf_id}.json"
            if attempt == 1
            else history_dir / f"{leaf_id}-attempt-{attempt}.json"
        )
        if not path.is_file():
            return path
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return path
        if not isinstance(document, dict) or document.get("closed") is not True:
            return path
        attempt += 1


def _series_leaf_enclosures(series: WorktreeContract) -> Iterator[WorktreeContract]:
    """Every leaf enclosure contract filed under the series, oldest first."""

    enclosures = series.task_root / "enclosures"
    if not enclosures.is_dir():
        return
    for enclosure in sorted(enclosures.iterdir()):
        contract_path = enclosure / "series-contract.md"
        if not contract_path.is_file():
            continue
        candidate = load_contract(contract_path)
        if candidate.kind != "leaf" or candidate.leaf_id is None:
            continue
        if (
            candidate.parent_contract_path is not None
            and candidate.parent_contract_path.as_posix() != series.contract_path.as_posix()
        ):
            continue
        yield candidate


def _unlanded_series_leaf(fixture: QueueFixture, series: WorktreeContract) -> WorktreeContract:
    """The series' leaf enclosure that has not landed yet, starting a new one if needed.

    One leaf lands once: its history closes with its closeout, and the chain proof reads
    each landing through its own leaf's range. The first call returns the fixture's
    built-in leaf; a later call commands (``author_unstarted_leaf``) and starts
    (``start_leaf``) a new leaf on the master's lines as they then stand, so its range
    starts exactly where the previous landing ended. Both halves are the product's own
    route; the landing itself is unchanged.
    """

    enclosures = series.task_root / "enclosures"
    landed_ids: set[str] = set()
    unlanded: WorktreeContract | None = None
    template = "LEAF-B"
    for candidate in _series_leaf_enclosures(series):
        assert candidate.leaf_id is not None
        template = candidate.leaf_id
        if not candidate.integrated_code_commit:
            if unlanded is None:
                unlanded = candidate
        else:
            landed_ids.add(candidate.leaf_id)
    if unlanded is not None:
        return unlanded
    # Fresh identities extend the oldest enclosure's (LEAF-B2, LEAF-A2, ...), skipping
    # any identity the task document or the enclosures already carry.
    for attempt in range(2, 20):
        leaf_id = f"{template}{attempt}"
        if (
            leaf_id not in landed_ids
            and not (enclosures / leaf_id.lower()).exists()
            and not (series.task_root / f"{leaf_id.lower()}.json").exists()
        ):
            break
    else:  # pragma: no cover - the tests land at most three pairs per master
        raise AssertionError("no fresh leaf identity left for the landing")
    fixture.author_unstarted_leaf(series.task_name, leaf_id)
    return fixture.start_leaf(leaf_id, master=series.task_name)


def accumulate_master_line(
    fixture: QueueFixture, series: WorktreeContract, scratch: Path, *, label: str
) -> LedgerRow:
    """Land one code/memory pair through the series leaf's own landing route.

    MIK-R52 rule 4: the master line holds only leaf landings and its parent line, so the
    pair arrives by closing out the series' leaf enclosure and integrating it -- the
    product's own route -- instead of committing straight onto the master branches.
    The label files give each landing its own content; the ledger row derives from
    the integrated pair exactly as the checkpoint reads it.
    """

    del scratch
    leaf = _unlanded_series_leaf(fixture, series)
    assert leaf.memory_worktree is not None
    (leaf.code_worktree / f"{label}.txt").write_text(f"{label}\n", encoding="utf-8")
    (leaf.memory_worktree / f"{label}.md").write_text(f"# {label}\n", encoding="utf-8")
    # The gate judges the leaf's new code file: extend its latest open history attempt
    # with the trace row, keeping every existing row; a closed history stays frozen and
    # the row goes to the leaf's next attempt file (MIK-R07 rule 7). The closeout
    # extends the same attempt with its own rows.
    assert leaf.leaf_id is not None
    history_dir = leaf.memory_worktree / "knowledge" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_path = _open_history_attempt(history_dir, leaf.leaf_id)
    if history_path.is_file():
        document = json.loads(history_path.read_text(encoding="utf-8"))
        rows = list(document.get("rows", []))
    else:
        rows = []
    rows.append(
        {
            "id": f"ROW-{len(rows):06d}",
            "subject": f"onboarding:{label}.txt",
            "disposition": "no_impact",
            "reason": "The landing's file carries no invariant.",
            "items": [],
            "sources": [
                {
                    "path": f"{label}.txt",
                    "blob": CodeSnapshot.capture(leaf.code_worktree).blobs[f"{label}.txt"],
                }
            ],
        }
    )
    history_path.write_text(
        canonical_text(
            {
                "schema": "ar-history/v1",
                "leaf": leaf.leaf_id,
                "closed": False,
                "rows": rows,
            }
        ),
        encoding="utf-8",
    )
    closed_result = worktree_tools.worktree_closeout_apply_tool(
        fixture.cfg,
        leaf.contract_path.as_posix(),
        worktree_tools.CloseoutCommitMessages(
            code=f"Land {label} code",
            memory=f"Land {label} memory",
        ),
        worktree_tools.CloseoutApproval(intent_note=f"Land {label}"),
    )
    assert closed_result.get("ok"), closed_result
    assert closed_result.get("state") == "closed", closed_result
    closed = load_contract(leaf.contract_path)
    result = integrate_module.integrate_result(
        WorktreeArgs(
            contract_path=closed.contract_path,
            strategy="ff-only",
            approved=True,
        ),
        closed,
    )
    assert result.returncode == 0, result.payload
    assert result.payload["state"] == "integrated", result.payload
    code_commit = result.payload["integrated_code_commit"]
    memory_commit = result.payload["integrated_memory_content_commit"]
    assert isinstance(code_commit, str) and isinstance(memory_commit, str), result.payload
    return LedgerRow(code_commit, memory_commit)


def commit_code(series: WorktreeContract, scratch: Path, *, label: str) -> str:
    with branch_checkout(series.code_repo_path, series.code_work_branch, scratch / "code") as tree:
        (tree / f"{label}.txt").write_text(f"{label}\n", encoding="utf-8")
        git(tree, "add", "-A")
        git(tree, "commit", "-m", f"Land {label} code")
        return git(tree, "rev-parse", "HEAD")


def commit_memory_content(series: WorktreeContract, scratch: Path, *, label: str) -> str:
    code_commit = rev(series.code_repo_path, series.code_work_branch)
    with branch_checkout(
        memory_repository(series), series.memory_work_branch, scratch / "memory-content"
    ) as tree:
        (tree / f"{label}.md").write_text(f"# {label}\n", encoding="utf-8")
        prepare_memory_cache(tree)
        git(tree, "add", "-A")
        git(
            tree, "commit", "-m", render_memory_content_message(f"Land {label} memory", code_commit)
        )
        return git(tree, "rev-parse", "HEAD")


def commit_code_on_source_branch(series: WorktreeContract, scratch: Path, *, label: str) -> str:
    """Commit one code advance on the code *source* branch, leaving the work branch alone.

    A source advance belongs to the source line: a commit straight onto the work branch
    is a master-line change no leaf landed, which MIK-R52 rule 4 refuses. The ledger row
    still pairs this commit with the memory advance below.
    """

    del scratch
    code = series.code_repo_path
    assert series.code_source_branch is not None
    git(code, "checkout", series.code_source_branch)
    (code / f"{label}.txt").write_text(f"{label}\n", encoding="utf-8")
    git(code, "add", "-A")
    git(code, "commit", "-m", label)
    return git(code, "rev-parse", "HEAD")


def advance_source_line(series: WorktreeContract, scratch: Path, *, label: str) -> LedgerRow:
    """Advance the memory source independently of the master's memory work branch."""

    memory = memory_repository(series)
    code_commit = commit_code_on_source_branch(series, scratch, label=label)
    with branch_checkout(memory, series.memory_source_branch, scratch / f"source-{label}") as tree:
        (tree / f"{label}.md").write_text(f"# {label}\n", encoding="utf-8")
        prepare_memory_cache(tree)
        git(tree, "add", "-A")
        git(
            tree, "commit", "-m", render_memory_content_message(f"Land {label} source", code_commit)
        )
        return LedgerRow(code_commit, git(tree, "rev-parse", "HEAD"))


def absorb_source_into_master_line(series: WorktreeContract, scratch: Path, *, label: str) -> None:
    """Reconcile the advanced source into the master line through the product's sync.

    The merge arrives journaled by a real ``worktree_sync`` (merge-memory), which is the
    route that admits source merges onto a converted line -- not a hand-made merge
    commit. Callers holding the series contract reload it afterwards: the sync rebinds
    the contract it wrote.
    """

    del scratch, label
    result = sync_module.sync_result(
        WorktreeArgs(
            contract_path=series.contract_path,
            memory_sync_choice="merge-memory",
        )
    )
    assert result.returncode == 0, result.payload
    assert result.payload["state"] == "synced", result.payload


def closeout_messages() -> Any:
    """The raw commit messages one closeout call carries; resolution decides the enabled legs."""

    return worktree_tools.CloseoutCommitMessages(code="Code", memory="Memory")


def checkpoint(fixture: QueueFixture, series: WorktreeContract, *, dry_run: bool) -> dict[str, Any]:
    """Drive the PUBLIC operation, exactly as the registered tool does."""

    return worktree_tools.worktree_checkpoint_landing_tool(
        fixture.cfg,
        contract_path=series.contract_path.as_posix(),
        strategy="ff-only",
        dry_run=dry_run,
    )


def master_status(series: WorktreeContract) -> str:
    """The master's own task status, read from its document rather than from the contract."""

    document = TaskDocument.model_validate_json(
        (series.task_root / "task.json").read_text(encoding="utf-8")
    )
    return document.status


def set_master_status(series: WorktreeContract, *, status: str, row_status: str) -> None:
    """Rewrite the master's own status and its sub-task row status."""

    master = TaskDocument.model_validate_json(
        (series.task_root / "task.json").read_text(encoding="utf-8")
    )
    write_task_doc(
        series.task_root,
        master.model_copy(
            update={
                "status": status,
                "subTasks": [
                    row.model_copy(update={"status": row_status}) for row in master.subTasks
                ],
            }
        ),
    )
