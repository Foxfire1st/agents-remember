"""Plan/apply parity for the routes this leaf repaired, on real Git repositories.

The checkpoint half: every other checkpoint case is a unit proof of one seam; this module is the
boundary proof. It drives the PUBLIC operation (``worktree_checkpoint_landing_tool``) over real
temporary Git repositories, starting from the state the deadlock made unreachable -- a master whose
``closeout_status`` is still ``not-started`` -- and verifies both destination refs, the ledger
mapping, retry, and continued work afterwards.

The parity half: the same file also proves the rule this leaf exists to enforce -- a dry run must
refuse exactly what its apply refuses -- for the ordinary integrate route's ledger projection and
for the closeout route's atomic-completion gate, the original instance of the whole family. Those
cases live here because they share this file's real-Git fixture and its public-operation discipline.

Neither prohibition is negotiated here. ``closeout_status`` is never pre-populated for the
checkpoint cases (the closeout case sets it only when it has authored the real commits that back
it, because the closeout route cannot be entered otherwise), and no case calls
``publish_series_checkpoint_under_authority`` or another inner helper in place of the public
operation.
"""

from __future__ import annotations

import contextlib
import subprocess
import tempfile
import unittest
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

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
from agents_remember.worktrees.integration import integration_ref_transaction
from agents_remember.worktrees.ledger_projection import (
    LedgerWorld,
    project_ledger,
    read_ledger_source,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.integrate import (
    checkpoint_landing_result,
    integrate_result,
)
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from test_closeout_queue import MASTER_B, QueueFixture
from test_worktree_support import git


def _rev(repository: Path, branch: str) -> str:
    return git(repository, "rev-parse", branch)


def _memory_repository(series: WorktreeContract) -> Path:
    """The external-memory repository this series contract must have."""

    assert series.memory_repo_path is not None
    return series.memory_repo_path


def _payload_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload[key]
    assert isinstance(value, str), (key, value)
    return value


@contextlib.contextmanager
def _branch_checkout(repository: Path, branch: str, scratch: Path) -> Iterator[Path]:
    """A disposable checkout of one branch, so a commit can be authored on it directly."""

    scratch.mkdir(parents=True, exist_ok=True)
    git(repository, "worktree", "add", scratch.as_posix(), branch)
    try:
        yield scratch
    finally:
        git(repository, "worktree", "remove", "--force", scratch.as_posix())


def _close_out_leaf(leaf: WorktreeContract) -> WorktreeContract:
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


def _accumulate_master_line(
    fixture: QueueFixture, series: WorktreeContract, scratch: Path, *, label: str
) -> LedgerRow:
    """Commit one accumulated code+memory pair on the master's own branches.

    This is the state a paused master is actually in: leaves landed their code and their memory
    content on the master's branches, and the master's ledger carries the mapping between them,
    while the sprint super branch is still behind. It is authored with the repository's own ledger
    helpers rather than by hand, so the projection the landing re-proves is the real one.
    """

    del fixture
    code_commit = _commit_code(series, scratch, label=label)
    memory_content = _commit_memory_content(series, scratch, label=label)
    row = LedgerRow(code_commit, memory_content)
    _commit_ledger_mapping(series, scratch, row, label=label)
    return row


def _commit_code(series: WorktreeContract, scratch: Path, *, label: str) -> str:
    with _branch_checkout(series.code_repo_path, series.code_work_branch, scratch / "code") as tree:
        (tree / f"{label}.txt").write_text(f"{label}\n", encoding="utf-8")
        git(tree, "add", "-A")
        git(tree, "commit", "-m", f"Land {label} code")
        return git(tree, "rev-parse", "HEAD")


def _commit_memory_content(series: WorktreeContract, scratch: Path, *, label: str) -> str:
    with _branch_checkout(
        _memory_repository(series), series.memory_work_branch, scratch / "memory-content"
    ) as tree:
        (tree / f"{label}.md").write_text(f"# {label}\n", encoding="utf-8")
        git(tree, "add", "-A")
        git(tree, "commit", "-m", f"Land {label} memory content")
        return git(tree, "rev-parse", "HEAD")


def _commit_ledger_mapping(
    series: WorktreeContract, scratch: Path, row: LedgerRow, *, label: str
) -> str:
    with _branch_checkout(
        _memory_repository(series), series.memory_work_branch, scratch / f"memory-ledger-{label}"
    ) as tree:
        write_ledger(
            tree / "memory.md",
            prepend_mapping(load_ledger(tree / "memory.md"), row.code_commit, row.memory_commit),
        )
        git(tree, "add", "memory.md")
        git(tree, "commit", "-m", f"Record {label} pair")
        return git(tree, "rev-parse", "HEAD")


def _git_that_conflicts(repository: Path, *args: str) -> str:
    """Run a git step whose failure is the expected outcome, and return what it said.

    The union merge below starts from the conflict a plain merge of two prepended ledger rows
    produces, so the shared helper -- which raises on any non-zero status -- cannot express it.
    """

    result = subprocess.run(
        ["git", *args], cwd=repository, text=True, capture_output=True, check=False
    )
    assert result.returncode != 0, f"git {' '.join(args)} was expected to conflict"
    return result.stdout + result.stderr


def _advance_source_line(series: WorktreeContract, scratch: Path, *, label: str) -> LedgerRow:
    """Commit one new mapping on the master's memory SOURCE branch, the line it lands into."""

    memory = _memory_repository(series)
    code_commit = _commit_code(series, scratch, label=label)
    with _branch_checkout(memory, series.memory_source_branch, scratch / f"source-{label}") as tree:
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


def _absorb_source_into_master_line(series: WorktreeContract, scratch: Path, *, label: str) -> None:
    """Merge the master's memory SOURCE into its work branch, resolving by unioning both sides.

    This is the shape a paused master's ledger really reaches, and the one L34's boundary proof
    never built. LOCR's memory line absorbed the IAS line three times -- 666196d5 ("unioning both
    curation lines"), then 4aab7e7f, then 9b045bb5 -- and because both sides had prepended a row to
    the same table the merge CONFLICTS, so the union that resolved it holds the two sides' own rows
    in an order the projection does not write. The resolution here is that union: every row from
    both sides, each side's additions ahead of the rows they share. It is computed from the three
    ledgers rather than written out, so the fixture cannot drift from the shape it claims.
    """

    memory = _memory_repository(series)
    source = series.memory_source_branch
    with _branch_checkout(memory, series.memory_work_branch, scratch / f"absorb-{label}") as tree:
        ours = load_ledger(tree / "memory.md")
        merge_base = git(tree, "merge-base", "HEAD", source)
        shared = parse_ledger_text(git(memory, "show", f"{merge_base}:memory.md"))
        _git_that_conflicts(tree, "merge", "--no-commit", "--no-ff", source)
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


def _rewrite_master_ledger(
    series: WorktreeContract, scratch: Path, *, label: str, rows: list[LedgerRow]
) -> str:
    """Commit an exactly specified master ledger table, so one content class can be isolated."""

    with _branch_checkout(
        _memory_repository(series), series.memory_work_branch, scratch / f"rewrite-{label}"
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


def _interleave_leaf_ledger(contract: WorktreeContract, *, label: str) -> str:
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


def _write_divergent_ledger(checkout: Path, *, base_commit: str) -> None:
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


def _hand_edit_ledger(series: WorktreeContract, scratch: Path, *, label: str) -> str:
    """Commit a hand-edited SERIES ledger through a disposable checkout of its memory branch.

    A series contract has no live worktree of its own (``memory_worktree`` is None), so the edit
    is authored on a scratch checkout and returned as the new branch head.
    """

    with _branch_checkout(
        _memory_repository(series), series.memory_work_branch, scratch / f"hand-edit-{label}"
    ) as tree:
        _write_divergent_ledger(tree, base_commit=series.code_base_commit)
        git(tree, "add", "memory.md")
        git(tree, "commit", "-m", f"Hand edit {label}")
        return git(tree, "rev-parse", "HEAD")


def _hand_edit_ledger_in_place(contract: WorktreeContract, *, label: str) -> str:
    """Commit a hand-edited ledger in the contract's OWN memory worktree.

    This is the shape a hand edit really reaches on a leaf: its memory worktree is live and on the
    branch, so the edit is made and committed where it lives, and the recorded ledger head moves.
    """

    worktree = contract.memory_worktree
    assert worktree is not None
    _write_divergent_ledger(worktree, base_commit=contract.code_base_commit)
    git(worktree, "add", "memory.md")
    git(worktree, "commit", "-m", f"Hand edit {label}")
    return git(worktree, "rev-parse", "HEAD")


def _closeout_messages() -> Any:
    """The raw commit messages one closeout call carries; resolution decides the enabled legs."""

    return worktree_tools.CloseoutCommitMessages(code="Code", memory="Memory", ledger="Ledger")


def _checkpoint(
    fixture: QueueFixture, series: WorktreeContract, *, dry_run: bool
) -> dict[str, Any]:
    """Drive the PUBLIC operation, exactly as the registered tool does."""

    return worktree_tools.worktree_checkpoint_landing_tool(
        fixture.cfg,
        contract_path=series.contract_path.as_posix(),
        strategy="ff-only",
        dry_run=dry_run,
    )


def _ledger_at(repository: Path, commit: str) -> MemoryLedger:
    """The ledger table one exact commit carries, read the way a landing reads it."""

    return parse_ledger_text(git(repository, "show", f"{commit}:memory.md"))


def _ledger_mapping(series: WorktreeContract, ledger_commit: str, code_commit: str) -> LedgerRow:
    """The mapping row the landed memory ref really carries for one code ref."""

    ledger = parse_ledger_text(
        git(_memory_repository(series), "show", f"{ledger_commit}:memory.md")
    )
    mapping = find_mapping(ledger, code_commit)
    assert mapping is not None, (ledger_commit, code_commit)
    return mapping


def _master_status(series: WorktreeContract) -> str:
    """The master's own task status, read from its document rather than from the contract."""

    document = TaskDocument.model_validate_json(
        (series.task_root / "task.json").read_text(encoding="utf-8")
    )
    return document.status


class CheckpointPausesAnUnfinishedMasterTests(unittest.TestCase):
    """One real temporary Git world per case, and the deadlock's own starting state."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = QueueFixture(self.root, atomic_b=True, memory_mode="external")
        # ``QueueFixture.contracts`` holds each master's LEAF enclosure; the atomic master's own
        # series contract is the canonical sibling at the master task root.
        self.series = load_contract(self.fixture.tasks / "master-b" / "series-contract.md")
        self.leaf = self.fixture.contracts[MASTER_B]
        assert (self.series.kind, self.leaf.kind) == ("series", "leaf")
        self.scratch = self.root / "scratch"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _unclosed_contract(self) -> WorktreeContract:
        contract = load_contract(self.series.contract_path)
        # The deadlock's prerequisite, asserted and never fabricated: this master has NEVER been
        # closed out, so every closeout cell is still empty.
        self.assertEqual(contract.closeout_status, "not-started")
        self.assertFalse(contract.approved_for_commit)
        self.assertEqual(contract.code_commit, "")
        self.assertEqual(contract.ledger_commit, "")
        self.assertEqual(contract.integration_status, "not-started")
        return contract

    def test_an_unfinished_master_checkpoints_its_own_refs_end_to_end(self) -> None:
        self._unclosed_contract()
        memory = _memory_repository(self.series)
        code_before = _rev(self.series.code_repo_path, self.series.code_source_branch)
        memory_before = _rev(memory, self.series.memory_source_branch)
        row = _accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        live_code = _rev(self.series.code_repo_path, self.series.code_work_branch)
        live_ledger = _rev(memory, self.series.memory_work_branch)

        preview = _checkpoint(self.fixture, self.series, dry_run=True)
        self.assertTrue(preview["ok"], preview)
        self.assertEqual(preview["state"], "would-checkpoint")
        eligibility = preview["eligibility"]
        assert isinstance(eligibility, dict)
        # The preview reports the conditions the apply enforces, including the two that make this
        # route reachable at all: no closeout is required, and the refs are the live ones.
        self.assertFalse(eligibility["closeoutRequired"])
        self.assertTrue(eligibility["approvalRequired"])
        self.assertTrue(eligibility["ledgerMappingVerified"])
        self.assertEqual(eligibility["codeCandidate"], live_code)
        self.assertEqual(eligibility["ledgerCandidate"], live_ledger)
        # A preview moves nothing.
        self.assertEqual(
            _rev(self.series.code_repo_path, self.series.code_source_branch), code_before
        )
        self.assertEqual(_rev(memory, self.series.memory_source_branch), memory_before)

        result = _checkpoint(self.fixture, self.series, dry_run=False)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["state"], "checkpointed")
        # BOTH destination refs moved to the exact captured commits, and the recorded cell equals
        # the live tips that were captured rather than any stale contract value.
        self.assertEqual(
            _rev(self.series.code_repo_path, self.series.code_source_branch), live_code
        )
        self.assertEqual(_rev(memory, self.series.memory_source_branch), live_ledger)
        self.assertEqual(result["integrated_code_commit"], live_code)
        self.assertEqual(result["integrated_ledger_commit"], live_ledger)

        stored = load_contract(self.series.contract_path)
        self.assertEqual(stored.integration_status, "checkpointed")
        self.assertEqual(stored.integrated_code_commit, live_code)
        self.assertEqual(stored.integrated_ledger_commit, live_ledger)
        # ``checkpointed`` is not a closeout: the master is still unfinished and unclosed, and
        # nothing was reclaimed.
        self.assertEqual(stored.closeout_status, "not-started")
        self.assertFalse(stored.approved_for_commit)
        self.assertEqual(stored.cleanup, self.series.cleanup)
        self.assertTrue(self.series.code_worktree.exists())
        self.assertEqual(_rev(self.series.code_repo_path, self.series.code_work_branch), live_code)
        # Resumable: the master's own document is untouched and its enclosure survives, so the
        # work that continues after the pause still has its task and its refs.
        self.assertEqual(_master_status(self.series), "inProgress")
        self.assertTrue(self.series.contract_path.is_file())
        self.assertTrue(self.series.worktree_group.is_dir())

        mapping = _ledger_mapping(self.series, live_ledger, live_code)
        self.assertEqual(mapping, row)
        self.assertEqual(result["integrated_memory_content_commit"], mapping.memory_commit)

    def test_a_master_line_that_unioned_its_source_still_checkpoints(self) -> None:
        """The merge a master's own memory line carries is accepted; L34's proof never built one.

        A union merge leaves the branch's own rows among the source rows instead of above them, so
        the fixed point the strict rule demands is refused. This is the real LOCR pause's shape, and
        it is asserted next to every content promise that survives it.
        """

        self._unclosed_contract()
        memory = _memory_repository(self.series)
        # The master lands a pair of its own, the line it lands INTO advances, a real merge unions
        # the two, and the master lands one more: the placement the projection never writes.
        _accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        _advance_source_line(self.series, self.scratch, label="source-one")
        _absorb_source_into_master_line(self.series, self.scratch, label="absorb")
        row = _accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-two")

        live_code = _rev(self.series.code_repo_path, self.series.code_work_branch)
        live_ledger = _rev(memory, self.series.memory_work_branch)
        projection = project_ledger(
            source=read_ledger_source(memory, _rev(memory, self.series.memory_source_branch)),
            observed=parse_ledger_text(git(memory, "show", f"{live_ledger}:memory.md")),
            world=LedgerWorld(
                memory_repository=memory,
                memory_reachable_from=live_ledger,
                code_repository=self.series.code_repo_path,
            ),
        )

        # The table really is the shape this leaf exists for: exactly the projection's rows, the
        # source rows still in source order, the master's own rows among them -- so the fixed point
        # is refused while every content promise holds.
        self.assertFalse(projection.is_fixed_point)
        self.assertTrue(projection.is_interleaved_projection)
        self.assertEqual(projection.added_rows, ())
        self.assertEqual(projection.removed_rows, ())
        self.assertEqual(projection.observed_source_rows, projection.source_rows)
        self.assertTrue(projection.reordered_rows)

        preview = _checkpoint(self.fixture, self.series, dry_run=True)
        self.assertTrue(preview["ok"], preview)
        self.assertEqual(preview["state"], "would-checkpoint")

        result = _checkpoint(self.fixture, self.series, dry_run=False)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["state"], "checkpointed")
        self.assertEqual(
            _rev(self.series.code_repo_path, self.series.code_source_branch), live_code
        )
        self.assertEqual(_rev(memory, self.series.memory_source_branch), live_ledger)
        mapping = _ledger_mapping(self.series, live_ledger, live_code)
        self.assertEqual(mapping, row)
        self.assertEqual(result["integrated_memory_content_commit"], mapping.memory_commit)

    def test_a_unioned_master_line_still_refuses_a_content_difference(self) -> None:
        """Interleaved placement is accepted; a different table is not.

        The relaxation is one condition wide, and this keeps it there. Each class below changes the
        rows while leaving the mapping the capture needs on top, so what refuses is the projection
        proof and not an earlier gate -- and the remedy it prints is one this route can act on.
        """

        _accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        _advance_source_line(self.series, self.scratch, label="source-one")
        _advance_source_line(self.series, self.scratch, label="source-two")
        _absorb_source_into_master_line(self.series, self.scratch, label="absorb")
        _accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-two")
        memory = _memory_repository(self.series)
        live_ledger = _rev(memory, self.series.memory_work_branch)
        source_rows = set(
            read_ledger_source(memory, _rev(memory, self.series.memory_source_branch)).ledger.rows
        )
        rows = parse_ledger_text(git(memory, "show", f"{live_ledger}:memory.md")).rows
        source_positions = [index for index, row in enumerate(rows) if row in source_rows]
        self.assertGreaterEqual(len(source_positions), 2, "the fixture needs two source rows")
        oldest, second_oldest = source_positions[-1], source_positions[-2]
        reordered = list(rows)
        reordered[oldest], reordered[second_oldest] = reordered[second_oldest], reordered[oldest]
        # One own row is present but untrue. That is the only class below which leaves every source
        # row in place, so it is what keeps the removed-rows clause honest rather than redundant.
        own_positions = [index for index, row in enumerate(rows) if row not in source_rows]
        self.assertGreaterEqual(len(own_positions), 2, "the fixture needs two own rows")
        untrue = list(rows)
        untrue[own_positions[-1]] = LedgerRow(rows[own_positions[-1]].code_commit, "0" * 40)
        corruptions = {
            "dropped": [row for index, row in enumerate(rows) if index != oldest],
            "reordered": reordered,
            "duplicated": [*rows, rows[oldest]],
            "untrue": untrue,
        }
        code_before = _rev(self.series.code_repo_path, self.series.code_source_branch)
        memory_before = _rev(memory, self.series.memory_source_branch)

        for label, corrupted in corruptions.items():
            _rewrite_master_ledger(self.series, self.scratch, label=label, rows=corrupted)
            for dry_run in (True, False):
                with self.assertRaises(RuntimeError, msg=f"{label} dry_run={dry_run}") as refused:
                    _checkpoint(self.fixture, self.series, dry_run=dry_run)

                refusal = str(refused.exception)
                self.assertIn("is not the projection of its source", refusal, label)
                # The remedy names what this route can do; the closeout re-run it CANNOT run is no
                # longer advertised to a master that is still open.
                self.assertIn("this route has none", refusal, label)
                self.assertNotIn("re-run worktree_closeout_apply", refusal, label)

        self.assertEqual(_rev(memory, self.series.memory_source_branch), memory_before)
        self.assertEqual(
            _rev(self.series.code_repo_path, self.series.code_source_branch), code_before
        )
        self.assertEqual(load_contract(self.series.contract_path).integration_status, "not-started")

    def test_retry_is_idempotent_and_continued_work_checkpoints_again(self) -> None:
        _accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        first = _checkpoint(self.fixture, self.series, dry_run=False)
        landed_code = _payload_text(first, "integrated_code_commit")
        landed_ledger = _payload_text(first, "integrated_ledger_commit")

        # A retry captures the very same live refs, re-proves the same ledger mapping, and
        # re-records the same cell. The design is idempotent rather than refusing: a crash between
        # the ref move and the contract write must converge on a retry, and convergence is exactly
        # what re-recording the same proved refs gives.
        second = _checkpoint(self.fixture, self.series, dry_run=False)

        self.assertTrue(second["ok"], second)
        self.assertEqual(second["state"], "checkpointed")
        self.assertEqual(second["integrated_code_commit"], landed_code)
        self.assertEqual(second["integrated_ledger_commit"], landed_ledger)
        self.assertEqual(
            _rev(self.series.code_repo_path, self.series.code_source_branch), landed_code
        )

        # Continued work: the master lands another accumulated pair and checkpoints again. The
        # route is not a one-shot terminal move -- that is what "paused, not retired" means.
        row = _accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-two")
        further_code = _rev(self.series.code_repo_path, self.series.code_work_branch)
        self.assertNotEqual(further_code, landed_code)
        third = _checkpoint(self.fixture, self.series, dry_run=False)

        self.assertTrue(third["ok"], third)
        self.assertEqual(third["state"], "checkpointed")
        self.assertEqual(third["integrated_code_commit"], further_code)
        self.assertEqual(
            _rev(self.series.code_repo_path, self.series.code_source_branch), further_code
        )
        ledger_commit = _payload_text(third, "integrated_ledger_commit")
        self.assertEqual(_ledger_mapping(self.series, ledger_commit, further_code), row)
        self.assertEqual(
            _ledger_mapping(self.series, ledger_commit, landed_code).code_commit, landed_code
        )

    def test_the_checkpoint_is_the_only_route_that_admits_an_unclosed_master(self) -> None:
        # The ordinary series integrate route still demands a completed closeout, and it refuses
        # BEFORE anything moves. That is the gate the checkpoint exemption does not touch.
        self._unclosed_contract()
        memory = _memory_repository(self.series)
        _accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        code_before = _rev(self.series.code_repo_path, self.series.code_source_branch)
        memory_before = _rev(memory, self.series.memory_source_branch)

        with self.assertRaises(RuntimeError) as raised:
            integrate_result(
                WorktreeArgs(
                    contract_path=self.series.contract_path,
                    strategy="ff-only",
                    approved=True,
                ),
                self._unclosed_contract(),
            )

        self.assertIn("integration requires closeout.status completed", str(raised.exception))
        self.assertEqual(
            _rev(self.series.code_repo_path, self.series.code_source_branch), code_before
        )
        self.assertEqual(_rev(memory, self.series.memory_source_branch), memory_before)

    def test_the_closeout_preview_refuses_what_the_closeout_apply_refuses(self) -> None:
        # The ORIGINAL instance of this whole family: a partial master's closeout preview answered
        # ``would-closeout`` while the apply refused on every completion blocker. Both surfaces now
        # read one evaluation, so the preview cannot plan a closeout the apply will reject.
        self._unclosed_contract()
        messages = _closeout_messages()
        code_before = _rev(self.series.code_repo_path, self.series.code_source_branch)

        with self.assertRaises(CloseoutQueueError, msg="the preview must refuse") as preview:
            worktree_tools.worktree_closeout_preview_tool(
                self.fixture.cfg,
                self.series.contract_path.as_posix(),
                messages,
            )

        self.assertEqual(preview.exception.status, "atomic-series-closeout-master-incomplete")
        # The 19-blocker shape that started this: the refusal carries the exact completion facts.
        self.assertIn("exact completion facts", str(preview.exception))

        with self.assertRaises(CloseoutQueueError, msg="the apply must refuse") as applied:
            worktree_tools.worktree_closeout_apply_tool(
                self.fixture.cfg,
                self.series.contract_path.as_posix(),
                messages,
                worktree_tools.CloseoutApproval(intent_note="developer approved"),
            )

        self.assertEqual(applied.exception.status, "atomic-series-closeout-master-incomplete")
        self.assertEqual(
            _rev(self.series.code_repo_path, self.series.code_source_branch), code_before
        )
        self.assertEqual(load_contract(self.series.contract_path).closeout_status, "not-started")

    def test_a_leaf_closeout_preview_is_untouched_by_the_series_completion_gate(self) -> None:
        # Blast radius: a leaf owes nothing to the series completion gate
        # (``publish_closeout_under_authority`` returns straight to its publication for a leaf), so
        # the leaf closeout preview must still plan the closeout exactly as it did before.
        leaf = load_contract(self.leaf.contract_path)
        self.assertEqual(leaf.kind, "leaf")

        preview = worktree_tools.worktree_closeout_preview_tool(
            self.fixture.cfg,
            leaf.contract_path.as_posix(),
            _closeout_messages(),
        )

        self.assertTrue(preview["ok"], preview)
        self.assertEqual(preview["state"], "would-closeout")
        self.assertTrue(preview["commit_approval_required"])

    def test_the_ordinary_leaf_route_refuses_a_divergent_ledger_at_preview_and_apply(self) -> None:
        # The SAME parity rule on the ordinary route: the ledger projection proof is owed by both
        # routes and is now evaluated for both at the preview/apply seam, so a dry run cannot
        # promise a landing the apply refuses. The leaf arm is exercised because it is the one
        # whose ledger history takes the ``project_ledger`` form (a series takes the leaf-chain
        # prefix form, which needs a finished chain the checkpoint route deliberately forgoes).
        closed = _close_out_leaf(load_contract(self.leaf.contract_path))
        edited = _hand_edit_ledger_in_place(closed, label="leaf-divergence")
        # The recorded ledger head moves to the hand-edited commit, which is the state a
        # hand-edited table really reaches: the file is committed and the cell names it.
        re_recorded = replace(closed, ledger_commit=edited)
        write_contract(re_recorded.contract_path, re_recorded)
        parent_path = re_recorded.parent_contract_path
        assert parent_path is not None
        parent = load_contract(parent_path)
        memory = _memory_repository(re_recorded)
        code_destination = _rev(re_recorded.code_repo_path, parent.code_work_branch)
        memory_destination = _rev(memory, parent.memory_work_branch)

        for dry_run in (True, False):
            surface = "preview" if dry_run else "apply"
            with self.assertRaises(RuntimeError, msg=f"the {surface} must refuse") as refused:
                integrate_result(
                    WorktreeArgs(
                        contract_path=re_recorded.contract_path,
                        strategy="ff-only",
                        approved=not dry_run,
                        dry_run=dry_run,
                    ),
                    load_contract(re_recorded.contract_path),
                )

            refusal = str(refused.exception)
            self.assertIn(
                "is not the projection of its source and its own true mappings",
                refusal,
                f"dry_run={dry_run}",
            )
            self.assertIn("is not an ancestor of the landed ledger commit", refusal)

        # Nothing landed and the contract did not move: both destination refs are where they were.
        self.assertEqual(
            _rev(re_recorded.code_repo_path, parent.code_work_branch), code_destination
        )
        self.assertEqual(_rev(memory, parent.memory_work_branch), memory_destination)
        self.assertEqual(load_contract(re_recorded.contract_path), re_recorded)

    def test_a_reversed_repeated_code_mapping_is_refused_at_preview_and_apply(self) -> None:
        """Interleaving may not republish an older memory commit as a code commit's current one.

        ``find_mapping`` returns the FIRST row naming a code commit, and a later closeout supersedes
        an earlier mapping without deleting it, so two rows for one code commit are normal. Accepting
        the interleaved PLACEMENT must not also accept REVERSING that pair: an external review
        reproduced the checkpoint succeeding while the older memory commit became current, which
        silently rewrites what a code commit resolves to and nothing downstream would report.
        """

        old = _accumulate_master_line(self.fixture, self.series, self.scratch, label="first")
        updated_memory = _commit_memory_content(self.series, self.scratch, label="first-update")
        new = LedgerRow(old.code_commit, updated_memory)
        _commit_ledger_mapping(self.series, self.scratch, new, label="first-update")
        _accumulate_master_line(self.fixture, self.series, self.scratch, label="second")
        memory = _memory_repository(self.series)
        live_ledger = _rev(memory, self.series.memory_work_branch)
        rows = parse_ledger_text(git(memory, "show", f"{live_ledger}:memory.md")).rows
        self.assertEqual(find_mapping(_ledger_at(memory, live_ledger), old.code_commit), new)

        reversed_rows = list(rows)
        older, newer = reversed_rows.index(old), reversed_rows.index(new)
        reversed_rows[older], reversed_rows[newer] = reversed_rows[newer], reversed_rows[older]
        candidate = _rewrite_master_ledger(
            self.series, self.scratch, label="reverse-history", rows=reversed_rows
        )
        projection = project_ledger(
            source=read_ledger_source(memory, _rev(memory, self.series.memory_source_branch)),
            observed=_ledger_at(memory, candidate),
            world=LedgerWorld(
                memory_repository=memory,
                memory_reachable_from=candidate,
                code_repository=self.series.code_repo_path,
            ),
        )
        # Every OTHER condition the relaxation allows still holds, so the refusal below is
        # attributed to the reversed mapping and the case cannot pass for an unrelated reason.
        self.assertEqual(projection.added_rows, ())
        self.assertEqual(projection.removed_rows, ())
        self.assertEqual(projection.observed_source_rows, projection.source_rows)
        self.assertFalse(projection.header_changed)
        self.assertFalse(projection.is_fixed_point)
        self.assertFalse(projection.is_interleaved_projection)

        code_before = _rev(self.series.code_repo_path, self.series.code_source_branch)
        memory_before = _rev(memory, self.series.memory_source_branch)

        for dry_run in (True, False):
            surface = "preview" if dry_run else "apply"
            with self.assertRaises(RuntimeError, msg=f"the {surface} must refuse") as refused:
                _checkpoint(self.fixture, self.series, dry_run=dry_run)

            self.assertIn("is not the projection of its source", str(refused.exception))

        # Nothing landed, so the reversed table is still only a candidate on the work branch: the
        # destination carries no mapping for that code commit at all, which is what it carried
        # before the attempt.
        self.assertEqual(
            _rev(self.series.code_repo_path, self.series.code_source_branch), code_before
        )
        self.assertEqual(_rev(memory, self.series.memory_source_branch), memory_before)
        self.assertIsNone(find_mapping(_ledger_at(memory, memory_before), old.code_commit))

    def test_an_untrue_historical_row_below_a_current_one_is_refused(self) -> None:
        """A table may not carry a row the world contradicts, even where no lookup reaches it.

        The mapping clause cannot catch this one on its own: the current row for that code commit
        stays on top, so every lookup still resolves to it. It is refused because the ledger is the
        durable record of what landed, and a row naming memory content that never reached the branch
        is a false entry whether or not a reader would ever reach it. This is the class that keeps
        the removed-rows clause load-bearing after the mapping clause was added.
        """

        old = _accumulate_master_line(self.fixture, self.series, self.scratch, label="first")
        updated_memory = _commit_memory_content(self.series, self.scratch, label="first-update")
        new = LedgerRow(old.code_commit, updated_memory)
        _commit_ledger_mapping(self.series, self.scratch, new, label="first-update")
        _accumulate_master_line(self.fixture, self.series, self.scratch, label="second")
        memory = _memory_repository(self.series)
        live_ledger = _rev(memory, self.series.memory_work_branch)
        untrue_rows = list(_ledger_at(memory, live_ledger).rows)
        untrue_rows[untrue_rows.index(old)] = LedgerRow(old.code_commit, "0" * 40)
        candidate = _rewrite_master_ledger(
            self.series, self.scratch, label="untrue-history", rows=untrue_rows
        )
        projection = project_ledger(
            source=read_ledger_source(memory, _rev(memory, self.series.memory_source_branch)),
            observed=_ledger_at(memory, candidate),
            world=LedgerWorld(
                memory_repository=memory,
                memory_reachable_from=candidate,
                code_repository=self.series.code_repo_path,
            ),
        )
        # The current mapping is untouched and every other acceptance condition still holds, so the
        # untrue row is the ONLY thing refusing this table -- which is the point of the case.
        self.assertEqual(find_mapping(_ledger_at(memory, candidate), old.code_commit), new)
        self.assertEqual(projection.added_rows, ())
        self.assertEqual(projection.observed_source_rows, projection.source_rows)
        self.assertFalse(projection.header_changed)
        self.assertTrue(projection.removed_rows)
        self.assertFalse(projection.is_interleaved_projection)

        code_before = _rev(self.series.code_repo_path, self.series.code_source_branch)
        memory_before = _rev(memory, self.series.memory_source_branch)

        for dry_run in (True, False):
            surface = "preview" if dry_run else "apply"
            with self.assertRaises(RuntimeError, msg=f"the {surface} must refuse") as refused:
                _checkpoint(self.fixture, self.series, dry_run=dry_run)

            self.assertIn("is not the projection of its source", str(refused.exception))

        self.assertEqual(
            _rev(self.series.code_repo_path, self.series.code_source_branch), code_before
        )
        self.assertEqual(_rev(memory, self.series.memory_source_branch), memory_before)

    def test_a_changed_ledger_is_refused_even_when_the_mapping_already_exists(self) -> None:
        """An already-landed pair does not license republishing a damaged table.

        The landing returned early when the source ledger already named the landed code and memory
        commits, on the grounds that there was no new row to verify. That was true and irrelevant: a
        table with a source row deleted adds no row either, so the shortcut was the one way a
        damaged ledger could still reach a protected ref. An external review reproduced it on a
        second checkpoint. A genuinely unchanged retry must keep converging.
        """

        _accumulate_master_line(self.fixture, self.series, self.scratch, label="first")
        first = _checkpoint(self.fixture, self.series, dry_run=False)
        self.assertEqual(first["state"], "checkpointed")

        # The unchanged retry is the case the shortcut existed to serve, and it still converges.
        again = _checkpoint(self.fixture, self.series, dry_run=False)
        self.assertEqual(again["state"], "checkpointed")
        self.assertEqual(again["integrated_code_commit"], first["integrated_code_commit"])
        self.assertEqual(again["integrated_ledger_commit"], first["integrated_ledger_commit"])

        memory = _memory_repository(self.series)
        source_before = _rev(memory, self.series.memory_source_branch)
        before = _ledger_at(memory, source_before)
        self.assertGreaterEqual(len(before.rows), 2)
        _rewrite_master_ledger(
            self.series, self.scratch, label="drop-source-row", rows=before.rows[:-1]
        )

        for dry_run in (True, False):
            surface = "preview" if dry_run else "apply"
            with self.assertRaises(RuntimeError, msg=f"the {surface} must refuse") as refused:
                _checkpoint(self.fixture, self.series, dry_run=dry_run)

            self.assertIn("is not the projection of its source", str(refused.exception))

        self.assertEqual(_rev(memory, self.series.memory_source_branch), source_before)
        self.assertEqual(_ledger_at(memory, source_before).rows, before.rows)

    def test_the_landing_does_not_pose_as_a_pause(self) -> None:
        """Landing a partial master and pausing one are two operations, and this verb only lands.

        The checkpoint moves the series' accumulated line into its source branch and leaves the
        master open, so the remaining work continues and the result says so. Folding the pause into
        this verb -- reporting the master as PAUSED and handing control back -- is the hidden side
        effect the split exists to prevent: pausing is its own operation, it releases the master's
        atomic-series selection, and it moves no ref at all.
        """

        _accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        result = _checkpoint(self.fixture, self.series, dry_run=False)

        self.assertEqual(result["state"], "checkpointed")
        self.assertEqual(result["nextOperation"], "continue_work")
        summary = _payload_text(result, "summary")
        self.assertNotIn("PAUSED", summary)
        self.assertNotIn("Control returns to the developer", summary)
        self.assertIn("the master stays open", summary)
        # It landed, and it neither closed the master out nor reclaimed anything.
        stored = load_contract(self.series.contract_path)
        self.assertEqual(stored.integration_status, "checkpointed")
        self.assertEqual(stored.closeout_status, "not-started")
        self.assertEqual(stored.cleanup, self.series.cleanup)
        self.assertEqual(_master_status(self.series), "inProgress")
        self.assertTrue(self.series.code_worktree.exists())

    def test_the_leaf_route_still_refuses_the_ledger_the_checkpoint_accepts(self) -> None:
        """The relaxation is the checkpoint's alone, and a leaf keeps the remedy it can run.

        The table below is exactly the interleaved projection the checkpoint accepts -- every row
        present once, the source rows in source order, the newest mapping first -- replayed on a
        LEAF, where closeout can still rewrite it. It must still refuse, and the remedy it prints
        must be the closeout re-run a leaf really has. Dropping the checkpoint-only guard is what
        this case is for.
        """

        closed = _close_out_leaf(load_contract(self.leaf.contract_path))
        edited = _interleave_leaf_ledger(closed, label="leaf-interleave")
        re_recorded = replace(closed, ledger_commit=edited)
        write_contract(re_recorded.contract_path, re_recorded)
        parent_path = re_recorded.parent_contract_path
        assert parent_path is not None
        parent = load_contract(parent_path)
        memory = _memory_repository(re_recorded)
        code_destination = _rev(re_recorded.code_repo_path, parent.code_work_branch)
        memory_destination = _rev(memory, parent.memory_work_branch)
        projection = project_ledger(
            source=read_ledger_source(memory, _rev(memory, closed.memory_source_branch)),
            observed=parse_ledger_text(git(memory, "show", f"{edited}:memory.md")),
            world=LedgerWorld(
                memory_repository=memory,
                memory_reachable_from=edited,
                code_repository=closed.code_repo_path,
            ),
        )
        # The table IS the form the checkpoint accepts. Asserted rather than assumed: without it the
        # leaf route would be refusing a table the checkpoint also refuses, and this case would pass
        # for a reason that has nothing to do with the guard it exists to witness.
        self.assertFalse(projection.is_fixed_point)
        self.assertTrue(projection.is_interleaved_projection)

        for dry_run in (True, False):
            surface = "preview" if dry_run else "apply"
            with self.assertRaises(RuntimeError, msg=f"the {surface} must refuse") as refused:
                integrate_result(
                    WorktreeArgs(
                        contract_path=re_recorded.contract_path,
                        strategy="ff-only",
                        approved=not dry_run,
                        dry_run=dry_run,
                    ),
                    load_contract(re_recorded.contract_path),
                )

            refusal = str(refused.exception)
            self.assertIn("is not the projection of its source", refusal)
            # A leaf's repair IS reachable, so it is named. The checkpoint's is not, and says so.
            self.assertIn("re-run worktree_closeout_apply", refusal)
            self.assertNotIn("this route has none", refusal)

        self.assertEqual(
            _rev(re_recorded.code_repo_path, parent.code_work_branch), code_destination
        )
        self.assertEqual(_rev(memory, parent.memory_work_branch), memory_destination)
        self.assertEqual(load_contract(re_recorded.contract_path), re_recorded)

    def test_a_leaf_that_has_not_closed_out_is_still_refused_by_integrate(self) -> None:
        # The leaf arm of the same gate. The gate itself is untouched for both ordinary routes --
        # the checkpoint exemption lives in the checkpoint's own preflight -- and only the SERIES
        # message carries the alternative verb, which the case below pins in both directions.
        leaf = load_contract(self.leaf.contract_path)
        self.assertEqual(leaf.closeout_status, "not-started")
        code_before = _rev(leaf.code_repo_path, leaf.code_source_branch)

        with self.assertRaises(RuntimeError) as raised:
            integrate_result(
                WorktreeArgs(contract_path=leaf.contract_path, strategy="ff-only", approved=True),
                leaf,
            )

        self.assertIn("integration requires closeout.status completed", str(raised.exception))
        self.assertEqual(_rev(leaf.code_repo_path, leaf.code_source_branch), code_before)

    def test_the_open_master_refusal_names_the_route_that_can_land_it(self) -> None:
        """An open master's integrate refusal is not a dead end: it names the checkpoint.

        A refusal that names a prerequisite the caller cannot satisfy is the defect this leaf fixed
        in the ledger refusal's remedy, and the ordinary verb for an open master carried the same
        shape: it demanded a closeout that an unfinished master cannot produce. The series message
        now carries the alternative. A leaf has none, so its message must not invent one.
        """

        self._unclosed_contract()
        _accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")

        with self.assertRaises(RuntimeError) as raised:
            integrate_result(
                WorktreeArgs(
                    contract_path=self.series.contract_path, strategy="ff-only", approved=True
                ),
                self._unclosed_contract(),
            )

        refusal = str(raised.exception)
        self.assertIn("integration requires closeout.status completed", refusal)
        self.assertIn("worktree_checkpoint_landing", refusal)

        leaf = load_contract(self.leaf.contract_path)
        with self.assertRaises(RuntimeError) as leaf_raised:
            integrate_result(
                WorktreeArgs(contract_path=leaf.contract_path, strategy="ff-only", approved=True),
                leaf,
            )

        leaf_refusal = str(leaf_raised.exception)
        self.assertIn("integration requires closeout.status completed", leaf_refusal)
        self.assertNotIn("worktree_checkpoint_landing", leaf_refusal)

    def test_a_completed_master_is_still_refused_by_the_checkpoint(self) -> None:
        # The other surviving refusal, driven through the PUBLIC operation. It fires at preflight,
        # so the preview refuses identically and cannot promise a pause the apply would reject.
        _set_master_status(self.series, status="Completed", row_status="Completed")

        with self.assertRaises(CloseoutQueueError) as raised:
            _checkpoint(self.fixture, self.series, dry_run=True)
        self.assertEqual(raised.exception.status, "atomic-series-checkpoint-master-complete")

        with self.assertRaises(CloseoutQueueError) as applied:
            _checkpoint(self.fixture, self.series, dry_run=False)
        self.assertEqual(applied.exception.status, "atomic-series-checkpoint-master-complete")

    def test_a_candidate_whose_ledger_does_not_map_the_code_ref_is_refused(self) -> None:
        # The capture's own proof. The code branch is advanced WITHOUT a mapping row for its new
        # tip, so no ledger entry maps the ref that would land, and the public operation refuses
        # instead of capturing it.
        code_commit = _commit_code(self.series, self.scratch, label="unmapped")
        code_before = _rev(self.series.code_repo_path, self.series.code_source_branch)

        with self.assertRaises(RuntimeError) as raised:
            _checkpoint(self.fixture, self.series, dry_run=True)

        self.assertIn("ledger head to map the exact series code commit", str(raised.exception))
        self.assertEqual(
            _rev(self.series.code_repo_path, self.series.code_work_branch), code_commit
        )
        self.assertEqual(
            _rev(self.series.code_repo_path, self.series.code_source_branch), code_before
        )

    def test_a_hand_edited_master_ledger_is_refused_by_the_preview_and_the_apply(self) -> None:
        # A paused master is exactly the state where someone might hand-edit ``memory.md``, so the
        # landing's projection proof is what stops that becoming a landing. The edit here keeps the
        # row the capture needs (so capture succeeds and this is genuinely the projection refusal,
        # not an earlier one) and adds a row whose memory commit is not reachable.
        _accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        _hand_edit_ledger(self.series, self.scratch, label="leaf-one")
        memory = _memory_repository(self.series)
        code_before = _rev(self.series.code_repo_path, self.series.code_source_branch)
        memory_before = _rev(memory, self.series.memory_source_branch)

        # BOTH surfaces refuse, and for the same reason: a preview that promised a checkpoint the
        # apply then rejected is the class of surprise this route was repaired for.
        for dry_run in (True, False):
            surface = "preview" if dry_run else "apply"
            with self.assertRaises(RuntimeError, msg=f"the {surface} must refuse") as refused:
                _checkpoint(self.fixture, self.series, dry_run=dry_run)

            refusal = str(refused.exception)
            self.assertIn(
                "is not the projection of its source and its own true mappings",
                refusal,
                f"dry_run={dry_run}",
            )
            self.assertIn("is not an ancestor of the landed ledger commit", refusal)

        # Nothing landed: both destination refs are where they were and no cell was written.
        self.assertEqual(
            _rev(self.series.code_repo_path, self.series.code_source_branch), code_before
        )
        self.assertEqual(_rev(memory, self.series.memory_source_branch), memory_before)
        self.assertEqual(load_contract(self.series.contract_path).integration_status, "not-started")

    def test_a_ref_race_names_the_checkpoint_as_the_tool_to_rerun(self) -> None:
        # The ref-race payload routes the operator at the tool that was attempting the move. On the
        # checkpoint route that is the checkpoint, not ``worktree_integrate``: the operation name
        # travels from the route into the shared protected-ref edge rather than being a fixed
        # literal, and this is the one payload where a wrong name would send the operator elsewhere.
        _accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        memory = _memory_repository(self.series)
        code_before = _rev(self.series.code_repo_path, self.series.code_source_branch)
        memory_before = _rev(memory, self.series.memory_source_branch)
        losing_race = mock.patch.object(
            integration_ref_transaction, "_compare_and_swap_ref", return_value=False
        )

        with losing_race:
            result = _checkpoint(self.fixture, self.series, dry_run=False)

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["state"], "integration-ref-race")
        self.assertEqual(result["nextTool"], "worktree_checkpoint_landing")
        self.assertEqual(
            result["nextArgs"], {"contract_path": self.series.contract_path.as_posix()}
        )
        self.assertEqual(
            _rev(self.series.code_repo_path, self.series.code_source_branch), code_before
        )
        self.assertEqual(_rev(memory, self.series.memory_source_branch), memory_before)
        self.assertEqual(load_contract(self.series.contract_path).integration_status, "not-started")

    # UNREPRODUCED FLAKE, RECORDED 2026-09-13 (leaf 260831-LOCR-L34). This case was reported
    # FAILED once, during a mutation run that deleted the shared preflight ledger proof
    # (integrate.py `_require_ledger_projection(...)`) while checking that the mutation fails
    # exactly the two ledger-divergence cases. It has never failed on the real tree, passes in
    # isolation under that same mutation, and did not reproduce in eight subsequent identical
    # replays of the mutated build (each: exactly the two divergence cases failed, this one
    # passed) or in three clean-tree runs. The failure text was not captured, and this case's
    # body -- accumulate a line, then call `checkpoint_landing_result` with approved=False and
    # assert the approval refusal -- shares no state with the mutated path beyond the fixture.
    # Recorded rather than dropped so the next person who sees it knows it was seen before and
    # does not start from zero; if it recurs, capture the assertion text and treat it as a real
    # flake in this case rather than in the closeout/landing code.
    def test_checkpoint_landing_requires_explicit_developer_approval(self) -> None:
        # ``args.approved`` is the checkpoint's own approval channel and was NOT traded away for the
        # dropped closeout requirement. The registered tool expresses it as ``dry_run=False``, so
        # this is the same seam with that flag cleared.
        self._unclosed_contract()
        _accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")

        with self.assertRaises(RuntimeError) as raised:
            checkpoint_landing_result(
                WorktreeArgs(
                    contract_path=self.series.contract_path,
                    strategy="ff-only",
                    approved=False,
                    dry_run=False,
                ),
                self._unclosed_contract(),
            )

        self.assertIn("explicit developer approval", str(raised.exception))


def _set_master_status(series: WorktreeContract, *, status: str, row_status: str) -> None:
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


if __name__ == "__main__":
    unittest.main()
