"""Real temporary Git world for the checkpoint-landing boundary proofs.

One authoritative builder for the master/leaf series world that
``test_checkpoint_landing_end_to_end.py`` drives through the public operations. These helpers
construct state on real repositories and read it back; they assert nothing about the routes,
so the cases keep the whole claim.
"""

from __future__ import annotations

import contextlib
import subprocess
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from agents_remember.application import worktree_tools
from agents_remember.kernel.memory_ledger import (
    LedgerRow,
    MemoryLedger,
    find_mapping,
    load_ledger,
    parse_ledger_text,
    prepend_mapping,
    write_ledger,
)
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
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
    """Record a leaf's closeout from REAL commits authored in its own two worktrees.

    The ordinary integrate route's entry gate requires a closed-out contract, so a case about
    anything downstream of it -- here, the ledger projection proof -- needs one. The closeout is
    not fabricated: the code, memory-content and ledger commits are committed in the leaf's
    worktrees and then recorded, which is exactly what closeout leaves behind. Nothing here is the
    deadlock's prerequisite; the checkpoint cases never touch these cells.
    """

    code_worktree = leaf.code_worktree
    memory_worktree = leaf.memory_worktree
    assert memory_worktree is not None
    git(code_worktree, "add", "-A")
    git(code_worktree, "commit", "-m", "Leaf code")
    code_commit = git(code_worktree, "rev-parse", "HEAD")
    git(memory_worktree, "add", "-A")
    git(memory_worktree, "commit", "-m", "Leaf memory content")
    memory_content = git(memory_worktree, "rev-parse", "HEAD")
    write_ledger(
        memory_worktree / "memory.md",
        prepend_mapping(load_ledger(memory_worktree / "memory.md"), code_commit, memory_content),
    )
    git(memory_worktree, "add", "memory.md")
    git(memory_worktree, "commit", "-m", "Leaf ledger")
    closed = replace(
        leaf,
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=code_commit,
        memory_content_commit=memory_content,
        ledger_commit=git(memory_worktree, "rev-parse", "HEAD"),
    )
    write_contract(closed.contract_path, closed)
    return closed


def accumulate_master_line(
    fixture: QueueFixture, series: WorktreeContract, scratch: Path, *, label: str
) -> LedgerRow:
    """Commit one accumulated code+memory pair on the master's own branches.

    This is the state a paused master is actually in: leaves landed their code and their memory
    content on the master's branches, and the master's ledger carries the mapping between them,
    while the sprint super branch is still behind. It is authored with the repository's own ledger
    helpers rather than by hand, so the projection the landing re-proves is the real one.
    """

    del fixture
    code_commit = commit_code(series, scratch, label=label)
    memory_content = commit_memory_content(series, scratch, label=label)
    row = LedgerRow(code_commit, memory_content)
    commit_ledger_mapping(series, scratch, row, label=label)
    return row


def commit_code(series: WorktreeContract, scratch: Path, *, label: str) -> str:
    with branch_checkout(series.code_repo_path, series.code_work_branch, scratch / "code") as tree:
        (tree / f"{label}.txt").write_text(f"{label}\n", encoding="utf-8")
        git(tree, "add", "-A")
        git(tree, "commit", "-m", f"Land {label} code")
        return git(tree, "rev-parse", "HEAD")


def commit_memory_content(series: WorktreeContract, scratch: Path, *, label: str) -> str:
    with branch_checkout(
        memory_repository(series), series.memory_work_branch, scratch / "memory-content"
    ) as tree:
        (tree / f"{label}.md").write_text(f"# {label}\n", encoding="utf-8")
        git(tree, "add", "-A")
        git(tree, "commit", "-m", f"Land {label} memory content")
        return git(tree, "rev-parse", "HEAD")


def commit_ledger_mapping(
    series: WorktreeContract, scratch: Path, row: LedgerRow, *, label: str
) -> str:
    with branch_checkout(
        memory_repository(series), series.memory_work_branch, scratch / f"memory-ledger-{label}"
    ) as tree:
        write_ledger(
            tree / "memory.md",
            prepend_mapping(load_ledger(tree / "memory.md"), row.code_commit, row.memory_commit),
        )
        git(tree, "add", "memory.md")
        git(tree, "commit", "-m", f"Record {label} pair")
        return git(tree, "rev-parse", "HEAD")


def git_that_conflicts(repository: Path, *args: str) -> str:
    """Run a git step whose failure is the expected outcome, and return what it said.

    The union merge below starts from the conflict a plain merge of two prepended ledger rows
    produces, so the shared helper -- which raises on any non-zero status -- cannot express it.
    """

    result = subprocess.run(
        ["git", *args], cwd=repository, text=True, capture_output=True, check=False
    )
    assert result.returncode != 0, f"git {' '.join(args)} was expected to conflict"
    return result.stdout + result.stderr


def advance_source_line(series: WorktreeContract, scratch: Path, *, label: str) -> LedgerRow:
    """Commit one new mapping on the master's memory SOURCE branch, the line it lands into."""

    memory = memory_repository(series)
    code_commit = commit_code(series, scratch, label=label)
    with branch_checkout(memory, series.memory_source_branch, scratch / f"source-{label}") as tree:
        (tree / f"{label}.md").write_text(f"# {label}\n", encoding="utf-8")
        git(tree, "add", "-A")
        git(tree, "commit", "-m", f"Land {label} source content")
        memory_content = git(tree, "rev-parse", "HEAD")
        row = LedgerRow(code_commit, memory_content)
        write_ledger(
            tree / "memory.md",
            prepend_mapping(load_ledger(tree / "memory.md"), row.code_commit, row.memory_commit),
        )
        git(tree, "add", "memory.md")
        git(tree, "commit", "-m", f"Record {label} source pair")
    return row


def absorb_source_into_master_line(series: WorktreeContract, scratch: Path, *, label: str) -> None:
    """Merge the master's memory SOURCE into its work branch, resolving by unioning both sides.

    This is the shape a paused master's ledger really reaches, and the one L34's boundary proof
    never built. LOCR's memory line absorbed the IAS line three times -- 666196d5 ("unioning both
    curation lines"), then 4aab7e7f, then 9b045bb5 -- and because both sides had prepended a row to
    the same table the merge CONFLICTS, so the union that resolved it holds the two sides' own rows
    in an order the projection does not write. The resolution here is that union: every row from
    both sides, each side's additions ahead of the rows they share. It is computed from the three
    ledgers rather than written out, so the fixture cannot drift from the shape it claims.
    """

    memory = memory_repository(series)
    source = series.memory_source_branch
    with branch_checkout(memory, series.memory_work_branch, scratch / f"absorb-{label}") as tree:
        ours = load_ledger(tree / "memory.md")
        merge_base = git(tree, "merge-base", "HEAD", source)
        shared = parse_ledger_text(git(memory, "show", f"{merge_base}:memory.md"))
        git_that_conflicts(tree, "merge", "--no-commit", "--no-ff", source)
        theirs = parse_ledger_text(git(memory, "show", f"{source}:memory.md"))
        shared_rows = set(shared.rows)
        union = [
            *[row for row in theirs.rows if row not in shared_rows],
            *[row for row in ours.rows if row not in shared_rows],
            *shared.rows,
        ]
        write_ledger(
            tree / "memory.md",
            replace(
                ours,
                rows=union,
                last_verified_code_commit=union[0].code_commit,
                last_memory_content_commit=union[0].memory_commit,
            ),
        )
        git(tree, "add", "memory.md")
        git(tree, "commit", "-m", f"Merge {label}: absorb the source line, unioning both")


def rewrite_master_ledger(
    series: WorktreeContract, scratch: Path, *, label: str, rows: list[LedgerRow]
) -> str:
    """Commit an exactly specified master ledger table, so one content class can be isolated."""

    with branch_checkout(
        memory_repository(series), series.memory_work_branch, scratch / f"rewrite-{label}"
    ) as tree:
        write_ledger(
            tree / "memory.md",
            replace(
                load_ledger(tree / "memory.md"),
                rows=rows,
                last_verified_code_commit=rows[0].code_commit,
                last_memory_content_commit=rows[0].memory_commit,
            ),
        )
        git(tree, "add", "memory.md")
        git(tree, "commit", "-m", f"Rewrite {label}")
        return git(tree, "rev-parse", "HEAD")


def interleave_leaf_ledger(contract: WorktreeContract, *, label: str) -> str:
    """Place a leaf's own rows among its source rows, keeping every row and the newest on top.

    A leaf cannot reach the checkpoint's relaxed form through its own closeout: closeout writes the
    table, so its own mapping is a prefix by construction. A merge of the source into the leaf's
    memory branch can still produce the placement, so this builds it deliberately -- the case that
    uses it measures the ROUTE, not whether the shape is reachable -- and it leaves the newest row
    first so the header is not what refuses.
    """

    worktree = contract.memory_worktree
    assert worktree is not None
    assert contract.memory_content_commit, "a closed-out leaf records its memory content"
    ledger = load_ledger(worktree / "memory.md")
    own, *source = ledger.rows
    # A second own mapping, so a source row can sit BETWEEN the leaf's two rows: with only one own
    # row every interleaving would put a source row first and the header check would refuse it.
    # Its memory commit is the ledger commit rather than the memory content, because two rows
    # sharing one memory commit have no ancestry order between them -- ``_newest_first`` would put
    # this one second, and the header check rather than the guard would be what refused.
    extra = LedgerRow(source[0].code_commit, contract.ledger_commit)
    rows = [extra, source[0], own, *source[1:]]
    write_ledger(
        worktree / "memory.md",
        replace(
            ledger,
            rows=rows,
            last_verified_code_commit=rows[0].code_commit,
            last_memory_content_commit=rows[0].memory_commit,
        ),
    )
    git(worktree, "add", "memory.md")
    git(worktree, "commit", "-m", f"Interleave {label}")
    return git(worktree, "rev-parse", "HEAD")


def write_divergent_ledger(checkout: Path, *, base_commit: str) -> None:
    """Rewrite a checkout's ``memory.md`` into a table the projection rejects, in place.

    The fabricated row's code commit really exists and its memory commit does not, so the table
    still parses, still carries the row for the code tip (which the capture and the closeout cells
    need), and is no longer the projection of its source and its own true mappings.
    """

    ledger = load_ledger(checkout / "memory.md")
    fabricated = LedgerRow(base_commit, "0" * 40)
    write_ledger(
        checkout / "memory.md",
        replace(
            ledger,
            rows=[fabricated, *ledger.rows],
            last_verified_code_commit=fabricated.code_commit,
            last_memory_content_commit=fabricated.memory_commit,
        ),
    )


def hand_edit_ledger(series: WorktreeContract, scratch: Path, *, label: str) -> str:
    """Commit a hand-edited SERIES ledger through a disposable checkout of its memory branch.

    A series contract has no live worktree of its own (``memory_worktree`` is None), so the edit
    is authored on a scratch checkout and returned as the new branch head.
    """

    with branch_checkout(
        memory_repository(series), series.memory_work_branch, scratch / f"hand-edit-{label}"
    ) as tree:
        write_divergent_ledger(tree, base_commit=series.code_base_commit)
        git(tree, "add", "memory.md")
        git(tree, "commit", "-m", f"Hand edit {label}")
        return git(tree, "rev-parse", "HEAD")


def hand_edit_ledger_in_place(contract: WorktreeContract, *, label: str) -> str:
    """Commit a hand-edited ledger in the contract's OWN memory worktree.

    This is the shape a hand edit really reaches on a leaf: its memory worktree is live and on the
    branch, so the edit is made and committed where it lives, and the recorded ledger head moves.
    """

    worktree = contract.memory_worktree
    assert worktree is not None
    write_divergent_ledger(worktree, base_commit=contract.code_base_commit)
    git(worktree, "add", "memory.md")
    git(worktree, "commit", "-m", f"Hand edit {label}")
    return git(worktree, "rev-parse", "HEAD")


def closeout_messages() -> Any:
    """The raw commit messages one closeout call carries; resolution decides the enabled legs."""

    return worktree_tools.CloseoutCommitMessages(code="Code", memory="Memory", ledger="Ledger")


def checkpoint(fixture: QueueFixture, series: WorktreeContract, *, dry_run: bool) -> dict[str, Any]:
    """Drive the PUBLIC operation, exactly as the registered tool does."""

    return worktree_tools.worktree_checkpoint_landing_tool(
        fixture.cfg,
        contract_path=series.contract_path.as_posix(),
        strategy="ff-only",
        dry_run=dry_run,
    )


def ledger_at(repository: Path, commit: str) -> MemoryLedger:
    """The ledger table one exact commit carries, read the way a landing reads it."""

    return parse_ledger_text(git(repository, "show", f"{commit}:memory.md"))


def ledger_mapping(series: WorktreeContract, ledger_commit: str, code_commit: str) -> LedgerRow:
    """The mapping row the landed memory ref really carries for one code ref."""

    ledger = parse_ledger_text(git(memory_repository(series), "show", f"{ledger_commit}:memory.md"))
    mapping = find_mapping(ledger, code_commit)
    assert mapping is not None, (ledger_commit, code_commit)
    return mapping


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
