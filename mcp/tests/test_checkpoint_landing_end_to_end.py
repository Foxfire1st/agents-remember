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

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.application import worktree_tools
from agents_remember.kernel.memory_ledger import (
    LedgerRow,
    find_mapping,
    parse_ledger_text,
)
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
from checkpoint_landing_test_support import (
    absorb_source_into_master_line,
    accumulate_master_line,
    advance_source_line,
    checkpoint,
    close_out_leaf,
    closeout_messages,
    commit_code,
    commit_ledger_mapping,
    commit_memory_content,
    hand_edit_ledger,
    hand_edit_ledger_in_place,
    interleave_leaf_ledger,
    ledger_at,
    ledger_mapping,
    master_status,
    memory_repository,
    payload_text,
    rev,
    rewrite_master_ledger,
    set_master_status,
)
from test_closeout_queue import MASTER_B, QueueFixture
from test_worktree_support import git


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
        memory = memory_repository(self.series)
        code_before = rev(self.series.code_repo_path, self.series.code_source_branch)
        memory_before = rev(memory, self.series.memory_source_branch)
        row = accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        live_code = rev(self.series.code_repo_path, self.series.code_work_branch)
        live_ledger = rev(memory, self.series.memory_work_branch)

        preview = checkpoint(self.fixture, self.series, dry_run=True)
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
            rev(self.series.code_repo_path, self.series.code_source_branch), code_before
        )
        self.assertEqual(rev(memory, self.series.memory_source_branch), memory_before)

        result = checkpoint(self.fixture, self.series, dry_run=False)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["state"], "checkpointed")
        # BOTH destination refs moved to the exact captured commits, and the recorded cell equals
        # the live tips that were captured rather than any stale contract value.
        self.assertEqual(rev(self.series.code_repo_path, self.series.code_source_branch), live_code)
        self.assertEqual(rev(memory, self.series.memory_source_branch), live_ledger)
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
        self.assertEqual(rev(self.series.code_repo_path, self.series.code_work_branch), live_code)
        # Resumable: the master's own document is untouched and its enclosure survives, so the
        # work that continues after the pause still has its task and its refs.
        self.assertEqual(master_status(self.series), "inProgress")
        self.assertTrue(self.series.contract_path.is_file())
        self.assertTrue(self.series.worktree_group.is_dir())

        mapping = ledger_mapping(self.series, live_ledger, live_code)
        self.assertEqual(mapping, row)
        self.assertEqual(result["integrated_memory_content_commit"], mapping.memory_commit)

    def test_a_master_line_that_unioned_its_source_still_checkpoints(self) -> None:
        """The merge a master's own memory line carries is accepted; L34's proof never built one.

        A union merge leaves the branch's own rows among the source rows instead of above them, so
        the fixed point the strict rule demands is refused. This is the real LOCR pause's shape, and
        it is asserted next to every content promise that survives it.
        """

        self._unclosed_contract()
        memory = memory_repository(self.series)
        # The master lands a pair of its own, the line it lands INTO advances, a real merge unions
        # the two, and the master lands one more: the placement the projection never writes.
        accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        advance_source_line(self.series, self.scratch, label="source-one")
        absorb_source_into_master_line(self.series, self.scratch, label="absorb")
        row = accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-two")

        live_code = rev(self.series.code_repo_path, self.series.code_work_branch)
        live_ledger = rev(memory, self.series.memory_work_branch)
        projection = project_ledger(
            source=read_ledger_source(memory, rev(memory, self.series.memory_source_branch)),
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

        preview = checkpoint(self.fixture, self.series, dry_run=True)
        self.assertTrue(preview["ok"], preview)
        self.assertEqual(preview["state"], "would-checkpoint")

        result = checkpoint(self.fixture, self.series, dry_run=False)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["state"], "checkpointed")
        self.assertEqual(rev(self.series.code_repo_path, self.series.code_source_branch), live_code)
        self.assertEqual(rev(memory, self.series.memory_source_branch), live_ledger)
        mapping = ledger_mapping(self.series, live_ledger, live_code)
        self.assertEqual(mapping, row)
        self.assertEqual(result["integrated_memory_content_commit"], mapping.memory_commit)

    def test_a_unioned_master_line_still_refuses_a_content_difference(self) -> None:
        """A unioned line lands at any placement, and a row the world contradicts does not.

        The file-preservation rule that used to refuse a dropped, reordered or duplicated source
        row is gone, so this case now measures what is left: the content classes that are a FALSE
        ENTRY rather than a different arrangement. The untrue row is the one that refuses, under
        the row-level rule that replaced the file rule, and the reorderings land.

        The corruptions that remain are asserted as accepted directly below the refusal, because a
        case that only asserted the refusal would read as if the file rule were still in force.
        """

        accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        advance_source_line(self.series, self.scratch, label="source-one")
        advance_source_line(self.series, self.scratch, label="source-two")
        absorb_source_into_master_line(self.series, self.scratch, label="absorb")
        accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-two")
        memory = memory_repository(self.series)
        live_ledger = rev(memory, self.series.memory_work_branch)
        source_rows = set(
            read_ledger_source(memory, rev(memory, self.series.memory_source_branch)).ledger.rows
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
        # ``dropped`` and ``duplicated`` used to be here; both are shapes a rebuild produces and
        # the ruling makes them legal, so what remains is the class that is still a false entry.
        corruptions = {
            "reordered": reordered,
            "untrue": untrue,
        }
        code_before = rev(self.series.code_repo_path, self.series.code_source_branch)
        memory_before = rev(memory, self.series.memory_source_branch)

        # Only the false entry refuses: it names memory content that exists nowhere, so no rebuild
        # could have produced it. The reordering beside it is an arrangement and lands below.
        rewrite_master_ledger(self.series, self.scratch, label="untrue", rows=corruptions["untrue"])
        for dry_run in (True, False):
            with self.assertRaises(RuntimeError, msg=f"untrue dry_run={dry_run}") as refused:
                checkpoint(self.fixture, self.series, dry_run=dry_run)

            refusal = str(refused.exception)
            self.assertIn("must be a mapping the repositories really hold", refusal)
            self.assertIn(corruptions["untrue"][own_positions[-1]].code_commit, refusal)

        self.assertEqual(rev(memory, self.series.memory_source_branch), memory_before)
        self.assertEqual(
            rev(self.series.code_repo_path, self.series.code_source_branch), code_before
        )
        self.assertEqual(load_contract(self.series.contract_path).integration_status, "not-started")

        # The reordered source region lands, which is the removal working as ruled: the rows are
        # the same true rows in a different arrangement.
        rewrite_master_ledger(self.series, self.scratch, label="reordered", rows=reordered)
        landed = checkpoint(self.fixture, self.series, dry_run=False)
        self.assertEqual(landed["state"], "checkpointed")

    def test_retry_is_idempotent_and_continued_work_checkpoints_again(self) -> None:
        accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        first = checkpoint(self.fixture, self.series, dry_run=False)
        landed_code = payload_text(first, "integrated_code_commit")
        landed_ledger = payload_text(first, "integrated_ledger_commit")

        # A retry captures the very same live refs, re-proves the same ledger mapping, and
        # re-records the same cell. The design is idempotent rather than refusing: a crash between
        # the ref move and the contract write must converge on a retry, and convergence is exactly
        # what re-recording the same proved refs gives.
        second = checkpoint(self.fixture, self.series, dry_run=False)

        self.assertTrue(second["ok"], second)
        self.assertEqual(second["state"], "checkpointed")
        self.assertEqual(second["integrated_code_commit"], landed_code)
        self.assertEqual(second["integrated_ledger_commit"], landed_ledger)
        self.assertEqual(
            rev(self.series.code_repo_path, self.series.code_source_branch), landed_code
        )

        # Continued work: the master lands another accumulated pair and checkpoints again. The
        # route is not a one-shot terminal move -- that is what "paused, not retired" means.
        row = accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-two")
        further_code = rev(self.series.code_repo_path, self.series.code_work_branch)
        self.assertNotEqual(further_code, landed_code)
        third = checkpoint(self.fixture, self.series, dry_run=False)

        self.assertTrue(third["ok"], third)
        self.assertEqual(third["state"], "checkpointed")
        self.assertEqual(third["integrated_code_commit"], further_code)
        self.assertEqual(
            rev(self.series.code_repo_path, self.series.code_source_branch), further_code
        )
        ledger_commit = payload_text(third, "integrated_ledger_commit")
        self.assertEqual(ledger_mapping(self.series, ledger_commit, further_code), row)
        self.assertEqual(
            ledger_mapping(self.series, ledger_commit, landed_code).code_commit, landed_code
        )

    def test_the_checkpoint_is_the_only_route_that_admits_an_unclosed_master(self) -> None:
        # The ordinary series integrate route still demands a completed closeout, and it refuses
        # BEFORE anything moves. That is the gate the checkpoint exemption does not touch.
        self._unclosed_contract()
        memory = memory_repository(self.series)
        accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        code_before = rev(self.series.code_repo_path, self.series.code_source_branch)
        memory_before = rev(memory, self.series.memory_source_branch)

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
            rev(self.series.code_repo_path, self.series.code_source_branch), code_before
        )
        self.assertEqual(rev(memory, self.series.memory_source_branch), memory_before)

    def test_the_closeout_preview_refuses_what_the_closeout_apply_refuses(self) -> None:
        # The ORIGINAL instance of this whole family: a partial master's closeout preview answered
        # ``would-closeout`` while the apply refused on every completion blocker. Both surfaces now
        # read one evaluation, so the preview cannot plan a closeout the apply will reject.
        self._unclosed_contract()
        messages = closeout_messages()
        code_before = rev(self.series.code_repo_path, self.series.code_source_branch)

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
            rev(self.series.code_repo_path, self.series.code_source_branch), code_before
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
            closeout_messages(),
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
        closed = close_out_leaf(load_contract(self.leaf.contract_path))
        edited = hand_edit_ledger_in_place(closed, label="leaf-divergence")
        # The recorded ledger head moves to the hand-edited commit, which is the state a
        # hand-edited table really reaches: the file is committed and the cell names it.
        re_recorded = replace(closed, ledger_commit=edited)
        write_contract(re_recorded.contract_path, re_recorded)
        parent_path = re_recorded.parent_contract_path
        assert parent_path is not None
        parent = load_contract(parent_path)
        memory = memory_repository(re_recorded)
        code_destination = rev(re_recorded.code_repo_path, parent.code_work_branch)
        memory_destination = rev(memory, parent.memory_work_branch)

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
            # The fabricated row is what refuses, under the rule that replaced the file rule: a
            # landed table may not carry a row the world contradicts.
            self.assertIn(
                "does not name memory content the landed ledger commit carries",
                refusal,
                f"dry_run={dry_run}",
            )
            self.assertIn("must be a mapping the repositories really hold", refusal)

        # Nothing landed and the contract did not move: both destination refs are where they were.
        self.assertEqual(rev(re_recorded.code_repo_path, parent.code_work_branch), code_destination)
        self.assertEqual(rev(memory, parent.memory_work_branch), memory_destination)
        self.assertEqual(load_contract(re_recorded.contract_path), re_recorded)

    def test_a_reversed_repeated_code_mapping_now_lands_and_the_hazard_is_recorded(self) -> None:
        """A reordered superseding pair lands, because row ORDER left the landing's rule set.

        ``find_mapping`` returns the FIRST row naming a code commit, and a later closeout supersedes
        an earlier mapping without deleting it, so two rows for one code commit are normal. Reversing
        such a pair changes what that code commit resolves to, and this case used to refuse it.

        It lands now, and the reason is the ruling's own content: order is a property of the tracked
        table rather than of the commits, the table is derived state, and no rule here may keep the
        file's arrangement authoritative. Both rows of the pair are TRUE -- each names a code commit
        the repository holds and memory content the landed ledger carries -- and every other promise
        still holds, so nothing in the landing's rules refuses it. Read together with the L11 report,
        which records this as the price of removing the file rule rather than as a feature.
        """

        old = accumulate_master_line(self.fixture, self.series, self.scratch, label="first")
        updated_memory = commit_memory_content(self.series, self.scratch, label="first-update")
        new = LedgerRow(old.code_commit, updated_memory)
        commit_ledger_mapping(self.series, self.scratch, new, label="first-update")
        accumulate_master_line(self.fixture, self.series, self.scratch, label="second")
        memory = memory_repository(self.series)
        live_ledger = rev(memory, self.series.memory_work_branch)
        rows = parse_ledger_text(git(memory, "show", f"{live_ledger}:memory.md")).rows
        self.assertEqual(find_mapping(ledger_at(memory, live_ledger), old.code_commit), new)

        reversed_rows = list(rows)
        older, newer = reversed_rows.index(old), reversed_rows.index(new)
        reversed_rows[older], reversed_rows[newer] = reversed_rows[newer], reversed_rows[older]
        candidate = rewrite_master_ledger(
            self.series, self.scratch, label="reverse-history", rows=reversed_rows
        )
        projection = project_ledger(
            source=read_ledger_source(memory, rev(memory, self.series.memory_source_branch)),
            observed=ledger_at(memory, candidate),
            world=LedgerWorld(
                memory_repository=memory,
                memory_reachable_from=candidate,
                code_repository=self.series.code_repo_path,
            ),
        )
        # The reversed pair is a reordering and nothing else: the same rows, both true, each code
        # commit still resolving to a memory commit the landed ledger really carries.
        self.assertEqual(projection.added_rows, ())
        self.assertEqual(projection.removed_rows, ())
        self.assertEqual(projection.observed_source_rows, projection.source_rows)
        self.assertFalse(projection.header_changed)

        result = checkpoint(self.fixture, self.series, dry_run=False)
        self.assertEqual(result["state"], "checkpointed")
        landed = ledger_at(memory, rev(memory, self.series.memory_source_branch))
        # Both rows of the pair survive the landing, so the reversal republished an older mapping
        # rather than losing one -- which is exactly the trade the removal makes.
        self.assertIn(old, landed.rows)
        self.assertIn(new, landed.rows)

    def test_an_untrue_historical_row_below_a_current_one_is_refused(self) -> None:
        """A table may not carry a row the world contradicts, even where no lookup reaches it.

        The mapping clause cannot catch this one on its own: the current row for that code commit
        stays on top, so every lookup still resolves to it. It is refused because the ledger is the
        durable record of what landed, and a row naming memory content that never reached the branch
        is a false entry whether or not a reader would ever reach it. This is the class that keeps
        the removed-rows clause load-bearing after the mapping clause was added.
        """

        old = accumulate_master_line(self.fixture, self.series, self.scratch, label="first")
        updated_memory = commit_memory_content(self.series, self.scratch, label="first-update")
        new = LedgerRow(old.code_commit, updated_memory)
        commit_ledger_mapping(self.series, self.scratch, new, label="first-update")
        accumulate_master_line(self.fixture, self.series, self.scratch, label="second")
        memory = memory_repository(self.series)
        live_ledger = rev(memory, self.series.memory_work_branch)
        untrue_rows = list(ledger_at(memory, live_ledger).rows)
        untrue_rows[untrue_rows.index(old)] = LedgerRow(old.code_commit, "0" * 40)
        candidate = rewrite_master_ledger(
            self.series, self.scratch, label="untrue-history", rows=untrue_rows
        )
        projection = project_ledger(
            source=read_ledger_source(memory, rev(memory, self.series.memory_source_branch)),
            observed=ledger_at(memory, candidate),
            world=LedgerWorld(
                memory_repository=memory,
                memory_reachable_from=candidate,
                code_repository=self.series.code_repo_path,
            ),
        )
        # The current mapping is untouched and every other acceptance condition still holds, so the
        # untrue row is the ONLY thing refusing this table -- which is the point of the case.
        self.assertEqual(find_mapping(ledger_at(memory, candidate), old.code_commit), new)
        self.assertEqual(projection.added_rows, ())
        self.assertEqual(projection.observed_source_rows, projection.source_rows)
        self.assertFalse(projection.header_changed)
        self.assertTrue(projection.removed_rows)
        self.assertFalse(projection.is_interleaved_projection)

        code_before = rev(self.series.code_repo_path, self.series.code_source_branch)
        memory_before = rev(memory, self.series.memory_source_branch)

        for dry_run in (True, False):
            surface = "preview" if dry_run else "apply"
            with self.assertRaises(RuntimeError, msg=f"the {surface} must refuse") as refused:
                checkpoint(self.fixture, self.series, dry_run=dry_run)

            self.assertIn(
                "does not name memory content the landed ledger commit carries",
                str(refused.exception),
            )

        self.assertEqual(
            rev(self.series.code_repo_path, self.series.code_source_branch), code_before
        )
        self.assertEqual(rev(memory, self.series.memory_source_branch), memory_before)

    def test_a_rebuilt_table_still_checkpoints_and_an_untrue_row_still_refuses(self) -> None:
        """The retry converges, and a false row is what refuses a changed table.

        The landing used to return early when the source ledger already named the landed pair,
        which made a republished table with a source row deleted indistinguishable from a genuine
        retry. The projection proof closed that; the projection proof is gone, and what replaces it
        is narrower: the table is judged row by row against the repositories rather than against
        the file it replaced.

        Both halves are asserted here because the replacement must not be weaker where it counts.
        The unchanged retry keeps converging -- the shape the old shortcut existed to serve -- and
        a table carrying a row the world contradicts is refused, which is the class the file rule
        was never needed for.
        """

        accumulate_master_line(self.fixture, self.series, self.scratch, label="first")
        first = checkpoint(self.fixture, self.series, dry_run=False)
        self.assertEqual(first["state"], "checkpointed")

        # The unchanged retry is the case the shortcut existed to serve, and it still converges.
        again = checkpoint(self.fixture, self.series, dry_run=False)
        self.assertEqual(again["state"], "checkpointed")
        self.assertEqual(again["integrated_code_commit"], first["integrated_code_commit"])
        self.assertEqual(again["integrated_ledger_commit"], first["integrated_ledger_commit"])

        memory = memory_repository(self.series)
        source_before = rev(memory, self.series.memory_source_branch)
        before = ledger_at(memory, source_before)
        self.assertGreaterEqual(len(before.rows), 2)

        # A row the world contradicts refuses, and it is the new rule that says so rather than the
        # file comparison: the fabricated memory commit exists nowhere, so no rebuild could have
        # produced it and no landing may publish it.
        untrue = [*before.rows]
        untrue[-1] = LedgerRow(untrue[-1].code_commit, "0" * 40)
        rewrite_master_ledger(self.series, self.scratch, label="untrue-row", rows=untrue)
        for dry_run in (True, False):
            surface = "preview" if dry_run else "apply"
            with self.assertRaises(RuntimeError, msg=f"the {surface} must refuse") as refused:
                checkpoint(self.fixture, self.series, dry_run=dry_run)
            self.assertIn("must be a mapping the repositories really hold", str(refused.exception))

    def test_the_landing_does_not_pose_as_a_pause(self) -> None:
        """Landing a partial master and pausing one are two operations, and this verb only lands.

        The checkpoint moves the series' accumulated line into its source branch and leaves the
        master open, so the remaining work continues and the result says so. Folding the pause into
        this verb -- reporting the master as PAUSED and handing control back -- is the hidden side
        effect the split exists to prevent: pausing is its own operation, it releases the master's
        atomic-series selection, and it moves no ref at all.
        """

        accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        result = checkpoint(self.fixture, self.series, dry_run=False)

        self.assertEqual(result["state"], "checkpointed")
        self.assertEqual(result["nextOperation"], "continue_work")
        summary = payload_text(result, "summary")
        self.assertNotIn("PAUSED", summary)
        self.assertNotIn("Control returns to the developer", summary)
        self.assertIn("the master stays open", summary)
        # It landed, and it neither closed the master out nor reclaimed anything.
        stored = load_contract(self.series.contract_path)
        self.assertEqual(stored.integration_status, "checkpointed")
        self.assertEqual(stored.closeout_status, "not-started")
        self.assertEqual(stored.cleanup, self.series.cleanup)
        self.assertEqual(master_status(self.series), "inProgress")
        self.assertTrue(self.series.code_worktree.exists())

    def test_the_leaf_route_lands_the_ledger_the_checkpoint_accepts(self) -> None:
        """The placement the checkpoint accepts is accepted on the leaf route too, by one rule.

        The table below is the interleaved projection: every row present once, the source rows in
        source order, the newest mapping first, with the leaf's own rows among the source rows. A
        LEAF used to refuse it while a checkpoint accepted it -- the asymmetry the ruling names --
        because the leaf route carried the file-preservation rule and the checkpoint route did not.

        Both routes are one rule now, so this case witnesses the parity instead of the difference:
        the leaf lands a placement it used to refuse. What it is still refused for is a row the
        world contradicts, which the untrue-row cases cover.
        """

        closed = close_out_leaf(load_contract(self.leaf.contract_path))
        edited = interleave_leaf_ledger(closed, label="leaf-interleave")
        re_recorded = replace(closed, ledger_commit=edited)
        write_contract(re_recorded.contract_path, re_recorded)
        parent_path = re_recorded.parent_contract_path
        assert parent_path is not None
        parent = load_contract(parent_path)
        memory = memory_repository(re_recorded)
        code_destination = rev(re_recorded.code_repo_path, parent.code_work_branch)
        memory_destination = rev(memory, parent.memory_work_branch)
        projection = project_ledger(
            source=read_ledger_source(memory, rev(memory, closed.memory_source_branch)),
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

        # The preview and the apply agree, and they agree on the table LANDING now rather than on
        # the table refusing -- which is the parity the ruling restores. The preview is asserted on
        # the apply's own terms: both are driven through the registered tool, and the apply is the
        # one that moves a ref.
        preview = worktree_tools.worktree_integrate_tool(
            self.fixture.cfg,
            contract_path=re_recorded.contract_path.as_posix(),
            strategy="ff-only",
            dry_run=True,
        )
        self.assertTrue(preview["ok"], preview)
        landed = integrate_result(
            WorktreeArgs(
                contract_path=re_recorded.contract_path,
                strategy="ff-only",
                approved=True,
                dry_run=False,
            ),
            load_contract(re_recorded.contract_path),
        )
        self.assertEqual(landed.payload["state"], "integrated", landed.payload)
        # The refs really moved -- the interleaved table was LANDED rather than merely tolerated,
        # which is the difference between a relaxed rule and a rule that stopped running.
        self.assertNotEqual(
            rev(re_recorded.code_repo_path, parent.code_work_branch), code_destination
        )
        self.assertNotEqual(rev(memory, parent.memory_work_branch), memory_destination)

    def test_a_leaf_that_has_not_closed_out_is_still_refused_by_integrate(self) -> None:
        # The leaf arm of the same gate. The gate itself is untouched for both ordinary routes --
        # the checkpoint exemption lives in the checkpoint's own preflight -- and only the SERIES
        # message carries the alternative verb, which the case below pins in both directions.
        leaf = load_contract(self.leaf.contract_path)
        self.assertEqual(leaf.closeout_status, "not-started")
        code_before = rev(leaf.code_repo_path, leaf.code_source_branch)

        with self.assertRaises(RuntimeError) as raised:
            integrate_result(
                WorktreeArgs(contract_path=leaf.contract_path, strategy="ff-only", approved=True),
                leaf,
            )

        self.assertIn("integration requires closeout.status completed", str(raised.exception))
        self.assertEqual(rev(leaf.code_repo_path, leaf.code_source_branch), code_before)

    def test_the_open_master_refusal_names_the_route_that_can_land_it(self) -> None:
        """An open master's integrate refusal is not a dead end: it names the checkpoint.

        A refusal that names a prerequisite the caller cannot satisfy is the defect this leaf fixed
        in the ledger refusal's remedy, and the ordinary verb for an open master carried the same
        shape: it demanded a closeout that an unfinished master cannot produce. The series message
        now carries the alternative. A leaf has none, so its message must not invent one.
        """

        self._unclosed_contract()
        accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")

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
        set_master_status(self.series, status="Completed", row_status="Completed")

        with self.assertRaises(CloseoutQueueError) as raised:
            checkpoint(self.fixture, self.series, dry_run=True)
        self.assertEqual(raised.exception.status, "atomic-series-checkpoint-master-complete")

        with self.assertRaises(CloseoutQueueError) as applied:
            checkpoint(self.fixture, self.series, dry_run=False)
        self.assertEqual(applied.exception.status, "atomic-series-checkpoint-master-complete")

    def test_a_candidate_whose_ledger_does_not_map_the_code_ref_is_refused(self) -> None:
        # The capture's own proof. The code branch is advanced WITHOUT a mapping row for its new
        # tip, so no ledger entry maps the ref that would land, and the public operation refuses
        # instead of capturing it.
        code_commit = commit_code(self.series, self.scratch, label="unmapped")
        code_before = rev(self.series.code_repo_path, self.series.code_source_branch)

        with self.assertRaises(RuntimeError) as raised:
            checkpoint(self.fixture, self.series, dry_run=True)

        self.assertIn("ledger head to map the exact series code commit", str(raised.exception))
        self.assertEqual(rev(self.series.code_repo_path, self.series.code_work_branch), code_commit)
        self.assertEqual(
            rev(self.series.code_repo_path, self.series.code_source_branch), code_before
        )

    def test_a_hand_edited_master_ledger_is_refused_by_the_preview_and_the_apply(self) -> None:
        # A paused master is exactly the state where someone might hand-edit ``memory.md``, so the
        # landing's projection proof is what stops that becoming a landing. The edit here keeps the
        # row the capture needs (so capture succeeds and this is genuinely the projection refusal,
        # not an earlier one) and adds a row whose memory commit is not reachable.
        accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        hand_edit_ledger(self.series, self.scratch, label="leaf-one")
        memory = memory_repository(self.series)
        code_before = rev(self.series.code_repo_path, self.series.code_source_branch)
        memory_before = rev(memory, self.series.memory_source_branch)

        # BOTH surfaces refuse, and for the same reason: a preview that promised a checkpoint the
        # apply then rejected is the class of surprise this route was repaired for.
        for dry_run in (True, False):
            surface = "preview" if dry_run else "apply"
            with self.assertRaises(RuntimeError, msg=f"the {surface} must refuse") as refused:
                checkpoint(self.fixture, self.series, dry_run=dry_run)

            refusal = str(refused.exception)
            # The fabricated row is what refuses, under the rule that replaced the file rule: a
            # landed table may not carry a row the world contradicts.
            self.assertIn(
                "does not name memory content the landed ledger commit carries",
                refusal,
                f"dry_run={dry_run}",
            )
            self.assertIn("must be a mapping the repositories really hold", refusal)

        # Nothing landed: both destination refs are where they were and no cell was written.
        self.assertEqual(
            rev(self.series.code_repo_path, self.series.code_source_branch), code_before
        )
        self.assertEqual(rev(memory, self.series.memory_source_branch), memory_before)
        self.assertEqual(load_contract(self.series.contract_path).integration_status, "not-started")

    def test_a_ref_race_names_the_checkpoint_as_the_tool_to_rerun(self) -> None:
        # The ref-race payload routes the operator at the tool that was attempting the move. On the
        # checkpoint route that is the checkpoint, not ``worktree_integrate``: the operation name
        # travels from the route into the shared protected-ref edge rather than being a fixed
        # literal, and this is the one payload where a wrong name would send the operator elsewhere.
        accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        memory = memory_repository(self.series)
        code_before = rev(self.series.code_repo_path, self.series.code_source_branch)
        memory_before = rev(memory, self.series.memory_source_branch)
        losing_race = mock.patch.object(
            integration_ref_transaction, "_compare_and_swap_ref", return_value=False
        )

        with losing_race:
            result = checkpoint(self.fixture, self.series, dry_run=False)

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["state"], "integration-ref-race")
        self.assertEqual(result["nextTool"], "worktree_checkpoint_landing")
        self.assertEqual(
            result["nextArgs"], {"contract_path": self.series.contract_path.as_posix()}
        )
        self.assertEqual(
            rev(self.series.code_repo_path, self.series.code_source_branch), code_before
        )
        self.assertEqual(rev(memory, self.series.memory_source_branch), memory_before)
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
        accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")

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
