"""Real-Git preview/apply proofs for checkpoint and ordinary integration."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.application import worktree_tools
from agents_remember.kernel.memory_cache import derive_memory_ledger
from agents_remember.worktrees.integration import integration_ref_transaction
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
    branch_checkout,
    checkpoint,
    close_out_leaf,
    closeout_messages,
    commit_code,
    master_status,
    memory_repository,
    payload_text,
    rev,
    set_master_status,
)
from test_closeout_queue import MASTER_B, QueueFixture


class CheckpointPausesAnUnfinishedMasterTests(unittest.TestCase):
    """One real temporary Git world per case, and the deadlock's own starting state."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = QueueFixture(self.root, atomic_b=True, memory_mode="external")
        # ``QueueFixture.contracts`` holds each master's LEAF enclosure; the atomic master's own
        # series contract is the canonical sibling at the master task root.
        self.series = load_contract(self.fixture.tasks / "master-b" / "series-contract.md")
        self.leaf = self.fixture.contracts[MASTER_B]
        assert (self.series.kind, self.leaf.kind) == ("series", "leaf")
        self.scratch = self.root / "scratch"

    def _unclosed_contract(self) -> WorktreeContract:
        contract = load_contract(self.series.contract_path)
        # The deadlock's prerequisite, asserted and never fabricated: this master has NEVER been
        # closed out, so every closeout cell is still empty.
        self.assertEqual(contract.closeout_status, "not-started")
        self.assertFalse(contract.approved_for_commit)
        self.assertEqual(contract.code_commit, "")
        self.assertEqual(contract.memory_content_commit, "")
        self.assertEqual(contract.integration_status, "not-started")
        return contract

    def test_an_unfinished_master_checkpoints_its_own_refs_end_to_end(self) -> None:
        self._unclosed_contract()
        memory = memory_repository(self.series)
        code_before = rev(self.series.code_repo_path, self.series.code_source_branch)
        memory_before = rev(memory, self.series.memory_source_branch)
        row = accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        live_code = rev(self.series.code_repo_path, self.series.code_work_branch)
        live_memory = rev(memory, self.series.memory_work_branch)

        preview = checkpoint(self.fixture, self.series, dry_run=True)
        self.assertTrue(preview["ok"], preview)
        self.assertEqual(preview["state"], "would-checkpoint")
        eligibility = preview["eligibility"]
        assert isinstance(eligibility, dict)
        # The preview reports the conditions the apply enforces, including the two that make this
        # route reachable at all: no closeout is required, and the refs are the live ones.
        self.assertFalse(eligibility["closeoutRequired"])
        self.assertTrue(eligibility["approvalRequired"])
        self.assertEqual(eligibility["codeCandidate"], live_code)
        self.assertEqual(eligibility["memoryContentCandidate"], live_memory)
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
        self.assertEqual(rev(memory, self.series.memory_source_branch), live_memory)
        self.assertEqual(result["integrated_code_commit"], live_code)
        self.assertEqual(result["integrated_memory_content_commit"], live_memory)

        stored = load_contract(self.series.contract_path)
        self.assertEqual(stored.integration_status, "checkpointed")
        self.assertEqual(stored.integrated_code_commit, live_code)
        self.assertEqual(stored.integrated_memory_content_commit, live_memory)
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

        self.assertIn(row, derive_memory_ledger(memory, live_memory).rows)
        self.assertEqual(result["integrated_memory_content_commit"], row.memory_commit)

    def test_a_master_line_that_merged_its_source_still_checkpoints(self) -> None:
        first = accumulate_master_line(self.fixture, self.series, self.scratch, label="first")
        source = advance_source_line(self.series, self.scratch, label="source")
        absorb_source_into_master_line(self.series, self.scratch, label="source")
        self.series = replace(
            self.series,
            memory_base_commit=rev(
                memory_repository(self.series), self.series.memory_source_branch
            ),
        )
        write_contract(self.series.contract_path, self.series)
        last = accumulate_master_line(self.fixture, self.series, self.scratch, label="last")
        memory = memory_repository(self.series)
        memory_tip = rev(memory, self.series.memory_work_branch)

        preview = checkpoint(self.fixture, self.series, dry_run=True)
        self.assertEqual(preview["state"], "would-checkpoint", preview)
        result = checkpoint(self.fixture, self.series, dry_run=False)

        self.assertEqual(result["state"], "checkpointed", result)
        self.assertEqual(rev(memory, self.series.memory_source_branch), memory_tip)
        rows = derive_memory_ledger(memory, memory_tip).rows
        for pair in (first, source, last):
            self.assertIn(pair, rows)

    def test_source_memory_divergence_still_blocks_preview_and_apply(self) -> None:
        accumulate_master_line(self.fixture, self.series, self.scratch, label="first")
        advance_source_line(self.series, self.scratch, label="independent-source")
        memory = memory_repository(self.series)
        code_before = rev(self.series.code_repo_path, self.series.code_source_branch)
        memory_before = rev(memory, self.series.memory_source_branch)

        for dry_run in (True, False):
            result = checkpoint(self.fixture, self.series, dry_run=dry_run)
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["state"], "blocked-non-ff", result)
        self.assertEqual(
            rev(self.series.code_repo_path, self.series.code_source_branch), code_before
        )
        self.assertEqual(rev(memory, self.series.memory_source_branch), memory_before)

    def test_retry_is_idempotent_and_continued_work_checkpoints_again(self) -> None:
        accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf-one")
        first = checkpoint(self.fixture, self.series, dry_run=False)
        landed_code = payload_text(first, "integrated_code_commit")
        landed_memory = payload_text(first, "integrated_memory_content_commit")

        # A retry captures the very same live refs, re-proves the same ancestry, and
        # re-records the same cell. The design is idempotent rather than refusing: a crash between
        # the ref move and the contract write must converge on a retry, and convergence is exactly
        # what re-recording the same proved refs gives.
        second = checkpoint(self.fixture, self.series, dry_run=False)

        self.assertTrue(second["ok"], second)
        self.assertEqual(second["state"], "checkpointed")
        self.assertEqual(second["integrated_code_commit"], landed_code)
        self.assertEqual(second["integrated_memory_content_commit"], landed_memory)
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
        memory_commit = payload_text(third, "integrated_memory_content_commit")
        rows = derive_memory_ledger(memory_repository(self.series), memory_commit).rows
        self.assertIn(row, rows)
        self.assertIn(landed_code, {item.code_commit for item in rows})

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

    def test_the_ordinary_leaf_route_lands_without_a_cache_file(self) -> None:
        closed = close_out_leaf(self.leaf)
        assert closed.memory_worktree is not None
        assert closed.memory_repo_path is not None
        (closed.memory_worktree / "memory.md").unlink(missing_ok=True)
        history = rev(closed.memory_repo_path, closed.memory_work_branch)

        for dry_run in (True, False):
            result = integrate_result(
                WorktreeArgs(
                    contract_path=closed.contract_path,
                    strategy="ff-only",
                    approved=not dry_run,
                    dry_run=dry_run,
                ),
                closed,
            )
            self.assertEqual(result.returncode, 0, result.payload)
            self.assertEqual(
                result.payload["state"], "would-integrate" if dry_run else "integrated"
            )
        self.assertEqual(
            rev(closed.memory_repo_path, closed.memory_source_branch), closed.memory_content_commit
        )
        self.assertEqual(rev(closed.memory_repo_path, closed.memory_work_branch), history)

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

    def test_a_code_advance_without_memory_attribution_can_checkpoint(self) -> None:
        code_commit = commit_code(self.series, self.scratch, label="code-only")
        memory = memory_repository(self.series)
        memory_tip = rev(memory, self.series.memory_work_branch)
        preview = checkpoint(self.fixture, self.series, dry_run=True)
        self.assertEqual(preview["state"], "would-checkpoint", preview)

        result = checkpoint(self.fixture, self.series, dry_run=False)

        self.assertEqual(result["state"], "checkpointed", result)
        self.assertEqual(result["integrated_code_commit"], code_commit)
        self.assertEqual(result["integrated_memory_content_commit"], memory_tip)
        self.assertNotIn(
            code_commit, {row.code_commit for row in derive_memory_ledger(memory, memory_tip).rows}
        )

    def test_missing_stale_or_malformed_source_cache_cannot_block_checkpoint(self) -> None:
        accumulate_master_line(self.fixture, self.series, self.scratch, label="first")
        memory = memory_repository(self.series)
        memory_tip = rev(memory, self.series.memory_work_branch)
        with branch_checkout(
            memory, self.series.memory_source_branch, self.scratch / "source-owner"
        ) as tree:
            for text in (None, "stale cache row\n", "<<<<<<< malformed cache\n"):
                cache = tree / "memory.md"
                if text is None:
                    cache.unlink(missing_ok=True)
                else:
                    cache.write_text(text, encoding="utf-8")
                with self.subTest(cache=text):
                    preview = checkpoint(self.fixture, self.series, dry_run=True)
                    self.assertEqual(preview["state"], "would-checkpoint", preview)
                    result = checkpoint(self.fixture, self.series, dry_run=False)
                    self.assertEqual(result["state"], "checkpointed", result)
                    self.assertEqual(result["integrated_memory_content_commit"], memory_tip)
                    self.assertEqual(rev(memory, self.series.memory_source_branch), memory_tip)

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
