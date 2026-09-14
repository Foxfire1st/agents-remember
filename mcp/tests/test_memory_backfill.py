"""The memory-history backfill: the rule that chooses a trailer, and that it writes exactly one.

The migration this module tests is the one operation in the trailer transition that no
``git revert`` can undo, so the tests aim at the properties that make it safe rather than at its
happy path. Four are load-bearing:

* every pairing the table records that a trailer CAN hold receives one, because a commit renders
  one value per trailer key and the reader takes the last -- so which pairings the format cannot
  hold is a decision the code makes and therefore a decision a test has to pin;
* a duplicate pairing whose memory commit changed onboarding or any file content is not dropped
  when nothing else contradicts it, because repeated onboarding repair is the history of the
  process and collapsing it is the documented failure;
* a conflicting claim -- several code commits naming one memory commit -- is resolved by the
  table's own order and REPORTED, so no code commit loses its mapping without being named;
* a second run plans nothing and moves no ref, which is what makes the plan its own dry run and
  the migration re-runnable rather than one-shot;
* the rewrite reproduces every commit's tree, identity and timestamps, so the trailer is the only
  difference between an original and its replacement.

The closed-loop acceptance proof deliberately reads the rewritten tip with an ABSENT ledger path,
so ``read_ledger_source`` cannot fall back to a table and the comparison is against Git-parsed
trailers alone. The earlier version of that proof read the table the migration carries forward,
and because that reader unions table rows with trailer rows it proved the table had survived
rather than that the trailers alone preserve the pairings -- which is exactly how 60 omitted
pairings passed a green suite.

Every fixture is a throwaway repository under ``tempfile``; nothing here reads or writes the
coordination tree, the installed memory repository, or any shared ref.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.cli.memory_backfill import add_arguments, run
from agents_remember.kernel.git_command import GitRunnerOptions, run_git
from agents_remember.kernel.memory_attribution import parse_code_commit_trailer
from agents_remember.kernel.memory_backfill import (
    SKIP_CODE_COMMIT_ALREADY_NAMED,
    SKIP_CODE_COMMIT_NOT_HELD,
    SKIP_MEMORY_COMMIT_CLAIMED,
    SKIP_MEMORY_COMMIT_MISSING,
    SKIP_MEMORY_COMMIT_UNREACHABLE,
    MemoryBackfillRefusal,
    MemoryBackfillRequest,
    apply_memory_backfill,
    carry_ledger_cells,
    plan_memory_backfill,
)
from agents_remember.kernel.memory_ledger import (
    parse_ledger_text,
)
from agents_remember.worktrees.ledger_projection import read_ledger_source

_IDENTITY = {
    "GIT_AUTHOR_NAME": "writer",
    "GIT_AUTHOR_EMAIL": "writer@example.invalid",
    "GIT_COMMITTER_NAME": "writer",
    "GIT_COMMITTER_EMAIL": "writer@example.invalid",
}

# Two spellings of one object, so a table cell can be abbreviated exactly as the real one is.
_ABBREVIATION = 8

# A ledger path no commit carries, so a read through it can only answer from trailers. This is how
# the acceptance proof below denies the fallback its gap-filling role without deleting any file.
_ABSENT_LEDGER = "memory-absent-for-the-trailer-only-proof.md"


def _commit(repo: Path, message: str, *, stamp: int) -> str:
    identity = {
        **_IDENTITY,
        "GIT_AUTHOR_DATE": f"2020-01-01T00:00:{stamp:02d}+00:00",
        "GIT_COMMITTER_DATE": f"2020-01-01T00:00:{stamp:02d}+00:00",
    }
    run_git(repo, ["add", "-A"])
    run_git(
        repo,
        ["commit", "-m", message],
        GitRunnerOptions(identity=identity),
    )
    return run_git(repo, ["rev-parse", "HEAD"]).stdout.strip()


def _write_table(repo: Path, rows: list[tuple[str, str]], *, commit: bool = True) -> str | None:
    """A ledger whose header agrees with its own first row, exactly as the real one does.

    ``commit=False`` rewrites the file WITHOUT recording a new commit, which is how a test can
    replace a table's row order while leaving every memory commit the table names untouched. That
    matters for any assertion about which of two claims wins: committing the second table would
    rebuild the memory line, so the two runs would differ in their object names as well as in the
    order under test, and the comparison would prove nothing about the order.
    """

    first_code, first_memory = rows[0]
    metadata = (
        '{"schema":"ar-memory-ledger/v1","repoName":"fixture","baseCodeCommit":"'
        + "a" * 40
        + '","baseMemoryCommit":"'
        + "b" * 40
        + '","lastVerifiedCodeCommit":"'
        + first_code
        + '","lastMemoryContentCommit":"'
        + first_memory
        + '","sortOrder":"newest-first"}'
    )
    body = [
        "# Memory Ledger",
        "",
        "```json ar-memory-ledger",
        metadata,
        "```",
        "",
        "Newest entries are always inserted at the top.",
        "",
        "| Code commit | Memory commit |",
        "| ----------- | ------------- |",
        *(f"| {code} | {memory} |" for code, memory in rows),
    ]
    (repo / "memory.md").write_text("\n".join(body) + "\n", encoding="utf-8")
    if not commit:
        return None
    return _commit(repo, "chore: record the ledger", stamp=len(rows) + 1)


class BackfillFixture:
    """A memory repository of trailerless content commits and a code repository beside it."""

    def __init__(self, *, memory_branch: str = "main") -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.memory = self.root / "memory"
        self.code = self.root / "code"
        self.memory_branch = memory_branch
        for repo in (self.memory, self.code):
            repo.mkdir()
            run_git(repo, ["init", "-q", "-b", memory_branch])

    def close(self) -> None:
        self._temporary.cleanup()

    def build_code(self, count: int) -> list[str]:
        (self.code / "code.txt").write_text("seed\n", encoding="utf-8")
        _commit(self.code, "chore: seed", stamp=0)
        shas = []
        for index in range(count):
            (self.code / "code.txt").write_text(f"code {index}\n", encoding="utf-8")
            shas.append(_commit(self.code, f"feat: code {index}", stamp=index + 1))
        return shas

    def build_memory(self, count: int) -> list[str]:
        (self.memory / "notes.txt").write_text("seed\n", encoding="utf-8")
        _commit(self.memory, "chore: seed", stamp=0)
        shas = []
        for index in range(count):
            (self.memory / "notes.txt").write_text(f"note {index}\n", encoding="utf-8")
            shas.append(_commit(self.memory, f"docs: curate {index}", stamp=index + 1))
        return shas

    def table(self, rows: list[tuple[str, str]], *, commit: bool = True) -> str | None:
        """Record a table, or with ``commit=False`` re-record its rows over the same commits."""

        return _write_table(self.memory, rows, commit=commit)

    def tip(self) -> str:
        """The branch tip, read from the ref -- a rewrite moves refs, not a checked-out HEAD."""

        return run_git(self.memory, ["rev-parse", self.branch_ref()]).stdout.strip()

    def branch_ref(self) -> str:
        """The full ref name of the branch this fixture commits to."""

        return f"refs/heads/{self.memory_branch}"

    def message(self, commit: str) -> str:
        return run_git(self.memory, ["show", "-s", "--format=%B", commit]).stdout

    def request(
        self, tip: str, *, rescue: str = "refs/backup/pre-migration", refs: tuple[str, ...] = ()
    ):
        return MemoryBackfillRequest(
            memory_repo=self.memory,
            tip=tip,
            code_repo=self.code,
            rescue_ref=rescue,
            update_refs=refs or (self.branch_ref(),),
        )

    def plan(self, tip: str):
        return plan_memory_backfill(self.request(tip))

    def migrate_table(self, result, *, commit: bool = True) -> str:
        """Carry the table onto the new ids through the production seam, then commit it.

        The backfill rewrites commit MESSAGES and deliberately not file contents, so the table
        that recorded the pre-rewrite ids still names them afterwards. A caller that skipped this
        step would leave the table pointing into the rescue ref, which is the state the exclusion
        census reports rather than hides.
        """

        table = self.memory / "memory.md"
        table.write_text(
            carry_ledger_cells(
                self.memory, table.read_text(encoding="utf-8"), result.rewritten_ids
            ),
            encoding="utf-8",
        )
        if not commit:
            return run_git(self.memory, ["rev-parse", self.branch_ref()]).stdout.strip()
        return _commit(self.memory, "chore: carry the readback onto the rewritten ids", stamp=90)

    def apply(
        self, tip: str, *, rescue: str = "refs/backup/pre-migration", refs: tuple[str, ...] = ()
    ):
        return apply_memory_backfill(self.request(tip, rescue=rescue, refs=refs))


class MemoryBackfillPlanTests(unittest.TestCase):
    """Which row wins the trailer, and that every row it passes over is accounted for."""

    def setUp(self) -> None:
        self.fixture = BackfillFixture()
        self.addCleanup(self.fixture.close)

    def test_every_recorded_pairing_that_can_be_carried_gets_its_own_trailer(self) -> None:
        # The table is newest-first, and BOTH pairings of code[0] are real: it was paired with
        # memory[0] first and repaired again as memory[5]. Collapsing them is the documented
        # failure, and nothing here contradicts either one, so both must receive a trailer.
        code = self.fixture.build_code(3)
        memory = self.fixture.build_memory(6)
        self.fixture.table(
            [(code[0], memory[0]), (code[1], memory[2]), (code[2], memory[3]), (code[0], memory[5])]
        )
        plan = self.fixture.plan(self.fixture.tip())

        self.assertEqual(
            {item.memory_commit: item.code_commit for item in plan.trailers},
            {memory[0]: code[0], memory[2]: code[1], memory[3]: code[2], memory[5]: code[0]},
            "a duplicate pairing that nothing contradicts must keep its own trailer",
        )
        self.assertEqual(
            plan.lost_code_commits, (), "nothing was lost, so nothing is reported lost"
        )
        self.assertEqual(plan.named_code_commits, 3)

    def test_a_contested_memory_commit_names_the_oldest_claim_and_reports_the_loss(self) -> None:
        # Two code commits name ONE memory commit and neither has another row, so exactly one can
        # be carried. The table records code[1]'s pairing BELOW code[0]'s, which is the older row,
        # so code[1] wins on the table's own order -- and code[0] is left with no mapping at all,
        # which the plan must name rather than absorb into a skip count.
        code = self.fixture.build_code(2)
        memory = self.fixture.build_memory(2)
        self.fixture.table([(code[0], memory[1]), (code[1], memory[1])])
        plan = self.fixture.plan(self.fixture.tip())

        self.assertEqual(
            {item.memory_commit: item.code_commit for item in plan.trailers},
            {memory[1]: code[1]},
        )
        self.assertEqual(plan.count_of(SKIP_MEMORY_COMMIT_CLAIMED), 1)
        self.assertEqual(len(plan.lost_claims), 1)
        lost = plan.lost_claims[0]
        self.assertEqual(lost.code_commit, code[0])
        self.assertEqual(lost.memory_commit, memory[1])
        self.assertEqual(lost.winner, code[1], "the winner must be named, not merely counted")
        self.assertEqual(plan.lost_code_commits, (code[0],))
        self.assertFalse(plan.is_empty, "a plan that lost a mapping is not an empty plan")

    def test_the_winner_does_not_depend_on_hash_order(self) -> None:
        # The old rule stored the chosen code commit in a dictionary keyed by memory commit, so
        # which claim survived followed the order code commits were iterated in -- a HASH order.
        # Both tables below name the SAME two code commits in opposite row order, and the table is
        # newest-first, so the bottom-most row names the pairing recorded FIRST. That is the one
        # that must win in both: a rule that let hash order decide would name one code commit when
        # it sits on top and the other when it does not.
        older, newer = sorted(self.fixture.build_code(2))
        memory = self.fixture.build_memory(2)
        self.fixture.table([(newer, memory[1]), (older, memory[1])])
        tip = self.fixture.tip()
        newest_claim_on_top = self.fixture.plan(tip)

        # Same commits, same table, only the two rows swapped -- so the assertion isolates order.
        self.fixture.table([(older, memory[1]), (newer, memory[1])], commit=False)
        oldest_claim_on_top = self.fixture.plan(tip)

        self.assertEqual(
            [item.code_commit for item in newest_claim_on_top.trailers],
            [older],
            "the bottom-most row names the pairing recorded first, whatever its hash",
        )
        self.assertEqual(
            [item.code_commit for item in oldest_claim_on_top.trailers],
            [older],
            "swapping which claim sits at the top of the table must not move the winner",
        )

    def test_a_declined_row_names_whether_it_is_a_duplicate_or_a_lost_mapping(self) -> None:
        # The closed vocabulary has to carry the distinction the migration is judged by: a row the
        # plan may legitimately decline is one whose pairing is still carried somewhere, while a
        # row whose code commit nothing carries is a mapping the history lost. A census that
        # reported one number for both would look identical in the two tables below, which differ
        # exactly in whether the declined row cost a mapping.
        code = self.fixture.build_code(2)
        memory = self.fixture.build_memory(3)
        # code[0] is paired twice, and code[1] takes the OLDER of those two pairings -- so one of
        # code[0]'s rows is declined while code[0] keeps the other and stays attributable.
        self.fixture.table([(code[0], memory[2]), (code[1], memory[2]), (code[0], memory[1])])
        duplicated = self.fixture.plan(self.fixture.tip())

        self.assertEqual(duplicated.count_of(SKIP_CODE_COMMIT_ALREADY_NAMED), 1)
        self.assertEqual(duplicated.count_of(SKIP_MEMORY_COMMIT_CLAIMED), 0)
        self.assertEqual(duplicated.lost_code_commits, (), "a duplicate costs the history nothing")

        other = BackfillFixture()
        self.addCleanup(other.close)
        contested_code = other.build_code(2)
        contested_memory = other.build_memory(2)
        # The same decline, but code[0] has no second pairing to fall back on, so the declined row
        # IS the lost mapping. Only ``lost_code_commits`` can tell these two tables apart.
        other.table(
            [(contested_code[0], contested_memory[1]), (contested_code[1], contested_memory[1])]
        )
        contested = other.plan(other.tip())

        self.assertEqual(contested.count_of(SKIP_MEMORY_COMMIT_CLAIMED), 1)
        self.assertEqual(contested.lost_code_commits, (contested_code[0],))
        self.assertEqual(
            contested.skipped[0].reason,
            SKIP_MEMORY_COMMIT_CLAIMED,
            "the loser's row names its cause, not a generic decline",
        )

    def test_a_code_commit_the_code_repository_lacks_skips_its_whole_row_group(self) -> None:
        code = self.fixture.build_code(2)
        memory = self.fixture.build_memory(4)
        absent = "f" * 40
        self.fixture.table(
            [(absent, memory[0]), (code[0], memory[1]), (code[1], memory[2]), (absent, memory[3])]
        )
        plan = self.fixture.plan(self.fixture.tip())

        self.assertEqual(plan.count_of(SKIP_CODE_COMMIT_NOT_HELD), 2)
        self.assertNotIn(absent, {item.code_commit for item in plan.trailers})

    def test_a_cell_naming_no_object_is_not_confused_with_an_unreachable_commit(self) -> None:
        code = self.fixture.build_code(2)
        memory = self.fixture.build_memory(4)
        self.fixture.table([(code[0], "0" * 40), (code[1], memory[2])])
        plan = self.fixture.plan(self.fixture.tip())

        self.assertEqual(plan.count_of(SKIP_MEMORY_COMMIT_MISSING), 1)
        self.assertEqual(plan.count_of(SKIP_MEMORY_COMMIT_UNREACHABLE), 0)

    def test_an_abbreviated_cell_is_the_same_row_as_its_full_name(self) -> None:
        code = self.fixture.build_code(1)
        memory = self.fixture.build_memory(2)
        self.fixture.table([(code[0], memory[1][:_ABBREVIATION])])
        plan = self.fixture.plan(self.fixture.tip())

        self.assertEqual(
            [(item.memory_commit, item.code_commit) for item in plan.trailers],
            [(memory[1], code[0])],
            "an abbreviated memory cell must resolve to the commit it names, not stay text",
        )

    def test_the_same_history_plans_the_same_digest_twice(self) -> None:
        code = self.fixture.build_code(1)
        memory = self.fixture.build_memory(2)
        self.fixture.table([(code[0], memory[1])])
        tip = self.fixture.tip()

        self.assertEqual(self.fixture.plan(tip).digest, self.fixture.plan(tip).digest)

    def test_a_plan_that_lost_a_mapping_does_not_share_a_digest_with_one_that_did_not(self) -> None:
        # The digest is what a caller pins an apply to, so a decision that loses a mapping must
        # not hash the same as the decision that keeps it -- otherwise a previewed digest would
        # authorize the wrong run.
        code = self.fixture.build_code(2)
        memory = self.fixture.build_memory(3)
        self.fixture.table([(code[0], memory[1]), (code[1], memory[1])])
        contested = self.fixture.plan(self.fixture.tip())

        self.fixture.table([(code[0], memory[1]), (code[1], memory[2])])
        distinct = self.fixture.plan(self.fixture.tip())

        self.assertNotEqual(contested.digest, distinct.digest)
        self.assertEqual(len(contested.lost_claims), 1)
        self.assertEqual(distinct.lost_claims, ())


class MemoryBackfillApplyTests(unittest.TestCase):
    """What the rewrite writes, and what it must leave exactly as it found it."""

    def setUp(self) -> None:
        self.fixture = BackfillFixture()
        self.addCleanup(self.fixture.close)

    def test_the_written_trailer_is_the_code_commit_the_row_named(self) -> None:
        code = self.fixture.build_code(2)
        memory = self.fixture.build_memory(4)
        self.fixture.table([(code[0], memory[3]), (code[1], memory[2])])
        result = self.fixture.apply(self.fixture.tip())

        self.assertEqual({item.memory_commit for item in result.rewritten}, {memory[3], memory[2]})
        for item in result.rewritten:
            # The plan names the ORIGINAL commit; the trailer is on its replacement, which the
            # total identity map is what lets a caller find.
            replaced = result.rewritten_ids[item.memory_commit]
            self.assertEqual(
                parse_code_commit_trailer(self.fixture.message(replaced)),
                item.code_commit,
            )

    def test_a_second_run_rewrites_nothing_and_moves_no_ref(self) -> None:
        code = self.fixture.build_code(1)
        memory = self.fixture.build_memory(2)
        self.fixture.table([(code[0], memory[1])])
        first = self.fixture.apply(self.fixture.tip())
        after_migration = self.fixture.migrate_table(first)

        plan = self.fixture.plan(after_migration)
        self.assertTrue(plan.is_empty, "a migrated history has nothing left to rewrite")
        self.assertEqual(len(plan.already_attributed), 1)

        second = self.fixture.apply(after_migration, rescue="refs/backup/second")
        self.assertEqual((second.old_tip, second.new_tip), (after_migration, after_migration))
        self.assertEqual(second.updated_refs, ())
        self.assertEqual(second.rewritten, ())
        self.assertEqual(self.fixture.tip(), after_migration)

    def test_every_commit_keeps_its_tree_identity_and_dates(self) -> None:
        code = self.fixture.build_code(1)
        memory = self.fixture.build_memory(3)
        self.fixture.table([(code[0], memory[2])])
        tip = self.fixture.tip()
        shape = "%T|%an|%ae|%aI|%cn|%ce|%cI|%s"
        before = run_git(self.fixture.memory, ["log", f"--format={shape}", tip]).stdout
        result = self.fixture.apply(tip)
        after = run_git(self.fixture.memory, ["log", f"--format={shape}", result.new_tip]).stdout

        self.assertEqual(before, after, "a rewrite may add a trailer and change nothing else")

    def test_the_identity_map_is_total_so_every_original_id_has_a_replacement(self) -> None:
        code = self.fixture.build_code(1)
        memory = self.fixture.build_memory(3)
        self.fixture.table([(code[0], memory[2])])
        tip = self.fixture.tip()
        originals = run_git(self.fixture.memory, ["rev-list", tip]).stdout.split()
        result = self.fixture.apply(tip)

        self.assertEqual(set(result.rewritten_ids), set(originals))
        self.assertEqual(result.rewritten_ids[tip], result.new_tip)

    def test_the_trailers_alone_preserve_every_pairing_the_ledger_file_recorded(self) -> None:
        """The acceptance proof, read with the ledger file unable to fill any gap.

        ``read_ledger_source`` merges the table it finds at the ledger path INTO the rows the
        trailers name, so reading it after the table is carried forward proves the TABLE survived
        and says nothing about the trailers. That is the proof gap the review found: the trailer
        set was 412 mappings for 412 code commits while the table held 472 pairings, and the old
        assertion passed anyway. This reads the same rewritten tip through a path no commit
        carries, so the table contributes nothing and the rows can only come from Git-parsed
        trailers. No file is deleted and no reader is made tolerant to get there.

        The expected set is the historical table's own pairings, explicitly classified, mapped
        through the old-to-new memory ids. Every pairing a trailer can hold must appear in it. The
        two that cannot are the ones two code commits claimed with nothing to fall back on, and
        those are asserted as REPORTED losses rather than waved through -- so an omission that is
        not named fails this proof, which is the property the old one lacked.
        """

        code = self.fixture.build_code(4)
        memory = self.fixture.build_memory(6)
        # Six pairings across six memory commits and four code commits: code[0] and code[1] are
        # each paired with two memory commits, and memory[2] is claimed by code[2] and code[3].
        # So five of the six can be carried and the sixth is a contested claim with no fallback.
        rows = [
            (code[0], memory[5]),
            (code[1], memory[4]),
            (code[0], memory[3]),
            (code[2], memory[2]),
            (code[3], memory[2]),
            (code[1], memory[1]),
        ]
        self.fixture.table(rows)
        tip = self.fixture.tip()
        plan = self.fixture.plan(tip)
        self.assertEqual(
            len(plan.lost_claims),
            1,
            "this table has exactly one contested claim with no fallback for the loser",
        )
        loser = plan.lost_claims[0]
        result = self.fixture.apply(tip)
        final = self.fixture.migrate_table(result)

        read = read_ledger_source(self.fixture.memory, final, relative=_ABSENT_LEDGER)

        self.assertEqual(
            read.excluded_rows,
            (),
            "a trailer naming a memory commit outside the tip would be dropped here",
        )
        encoded = {(row.code_commit, row.memory_commit) for row in read.ledger.rows}
        expected = {
            (code_commit, result.rewritten_ids.get(memory_commit, memory_commit))
            for code_commit, memory_commit in rows
            if not (code_commit == loser.code_commit and memory_commit == loser.memory_commit)
        }
        self.assertEqual(
            encoded,
            expected,
            "the trailers alone must carry every pairing except the one the plan named as lost",
        )
        self.assertEqual(len(encoded), 5, "five of the six pairings fit, and all five are here")
        self.assertEqual(
            read.trailered_commits,
            len({memory_commit for _, memory_commit in rows}),
            "every memory commit the table names carries a trailer, the contested one included",
        )
        self.assertEqual(
            {code_commit for code_commit, _ in encoded},
            {code_commit for code_commit, _ in rows} - {loser.code_commit},
            "one code commit loses its mapping, and the proof names which",
        )
        self.assertEqual(
            loser.code_commit,
            code[2],
            "code[2]'s row for the contested memory commit sits ABOVE code[3]'s, so it is the "
            "newer claim and it loses",
        )
        self.assertEqual(
            loser.winner,
            code[3],
            "the bottom-most row names the pairing recorded first, so code[3] carries the claim",
        )
        self.assertFalse(plan.is_empty, "a plan that reports a lost mapping is not empty")

    def test_a_content_bearing_duplicate_that_would_be_dropped_fails_the_proof(self) -> None:
        """The omission the acceptance proof must catch, reproduced against a rule that drops it.

        The old rule kept one row per code commit and let the last assignment win, so this table --
        where code[0] is paired with memory[0] first and repaired again as memory[2] -- lost the
        memory[0] pairing entirely, and the review measured 60 losses of exactly this shape. The
        proof above cannot pass while that happens, because there is no table left to read: this
        asserts the loss is visible in the trailer-only read rather than hidden by a union.
        """

        code = self.fixture.build_code(2)
        memory = self.fixture.build_memory(3)
        self.fixture.table([(code[0], memory[2]), (code[1], memory[1]), (code[0], memory[0])])
        result = self.fixture.apply(self.fixture.tip())
        final = self.fixture.migrate_table(result)

        read = read_ledger_source(self.fixture.memory, final, relative=_ABSENT_LEDGER)

        encoded = {(row.code_commit, row.memory_commit) for row in read.ledger.rows}
        self.assertIn(
            (code[0], result.rewritten_ids[memory[0]]),
            encoded,
            "the older duplicate pairing must be readable from the trailers and from nowhere else",
        )
        self.assertIn((code[0], result.rewritten_ids[memory[2]]), encoded)
        self.assertEqual(len(encoded), 3, "every pairing in this table fits, so all three are here")

    def test_the_rescue_ref_holds_the_original_tip_before_the_rewrite(self) -> None:
        code = self.fixture.build_code(1)
        memory = self.fixture.build_memory(2)
        self.fixture.table([(code[0], memory[1])])
        tip = self.fixture.tip()
        result = self.fixture.apply(tip)

        rescued = run_git(self.fixture.memory, ["rev-parse", "refs/backup/pre-migration"])
        self.assertEqual(rescued.stdout.strip(), tip)
        self.assertNotEqual(result.new_tip, tip)

    def test_an_existing_rescue_ref_refuses_before_anything_is_rewritten(self) -> None:
        code = self.fixture.build_code(1)
        memory = self.fixture.build_memory(2)
        self.fixture.table([(code[0], memory[1])])
        tip = self.fixture.tip()
        run_git(self.fixture.memory, ["update-ref", "refs/backup/pre-migration", tip])

        with self.assertRaisesRegex(MemoryBackfillRefusal, "already exists"):
            self.fixture.apply(tip)
        self.assertEqual(self.fixture.tip(), tip)

    def test_a_previewed_digest_that_no_longer_matches_refuses(self) -> None:
        code = self.fixture.build_code(1)
        memory = self.fixture.build_memory(3)
        self.fixture.table([(code[0], memory[2])])
        previewed = self.fixture.plan(self.fixture.tip()).digest
        # The plan is derived from the table, so recording another row is what invalidates the
        # preview: the tip has moved and the decision it produced is no longer the same one.
        self.fixture.table([(code[0], memory[2]), (code[0], memory[1])])

        with self.assertRaisesRegex(MemoryBackfillRefusal, "was expected"):
            apply_memory_backfill(
                self.fixture.request(self.fixture.tip()),
                expected_digest=previewed,
            )

    def test_the_rescue_ref_may_not_be_one_of_the_refs_the_run_moves(self) -> None:
        code = self.fixture.build_code(1)
        memory = self.fixture.build_memory(2)
        self.fixture.table([(code[0], memory[1])])

        with self.assertRaisesRegex(MemoryBackfillRefusal, "also a ref this run would move"):
            self.fixture.apply(
                self.fixture.tip(),
                rescue=self.fixture.branch_ref(),
                refs=(self.fixture.branch_ref(),),
            )

    def test_a_rescue_ref_outside_refs_is_refused(self) -> None:
        code = self.fixture.build_code(1)
        memory = self.fixture.build_memory(2)
        self.fixture.table([(code[0], memory[1])])

        with self.assertRaisesRegex(MemoryBackfillRefusal, "not a full ref name"):
            self.fixture.apply(self.fixture.tip(), rescue="backup/pre-migration")


class MemoryBackfillLedgerReadTests(unittest.TestCase):
    """Reading the table the history carries, without the header check the strict reader keeps."""

    def setUp(self) -> None:
        self.fixture = BackfillFixture()
        self.addCleanup(self.fixture.close)

    def test_a_header_disagreeing_with_its_own_first_row_is_still_read(self) -> None:
        code = self.fixture.build_code(1)
        memory = self.fixture.build_memory(2)
        self.fixture.table([(code[0], memory[1])])
        table = self.fixture.memory / "memory.md"
        table.write_text(
            table.read_text(encoding="utf-8").replace(code[0], "c" * 40, 1), encoding="utf-8"
        )
        _commit(self.fixture.memory, "chore: disagree with the header", stamp=5)

        plan = self.fixture.plan(self.fixture.tip())
        self.assertEqual(plan.row_count, 1)
        self.assertEqual(
            [(item.memory_commit, item.code_commit) for item in plan.trailers],
            [(memory[1], code[0])],
        )

    def test_a_tip_with_no_ledger_refuses_rather_than_planning_nothing(self) -> None:
        self.fixture.build_memory(1)
        with self.assertRaisesRegex(MemoryBackfillRefusal, "not readable"):
            self.fixture.plan(self.fixture.tip())


class MemoryBackfillCarryTests(unittest.TestCase):
    """Moving the table's memory cells onto the ids the rewrite produced."""

    def setUp(self) -> None:
        self.fixture = BackfillFixture()
        self.addCleanup(self.fixture.close)

    def ledger_text(self, rows, *, base_code=None, base_memory=None) -> str:
        """A ledger whose header is valid for its own first row, with both bases overridable."""

        metadata = {
            "schema": "ar-memory-ledger/v1",
            "repoName": "fixture",
            "baseCodeCommit": base_code if base_code is not None else "c" * 40,
            "baseMemoryCommit": base_memory if base_memory is not None else rows[0][1],
            "lastVerifiedCodeCommit": rows[0][0],
            "lastMemoryContentCommit": rows[0][1],
            "sortOrder": "newest-first",
        }
        body = [
            "# Memory Ledger",
            "",
            "```json ar-memory-ledger",
            json.dumps(metadata, indent=2),
            "```",
            "",
            "Newest entries are always inserted at the top.",
            "",
            "| Code commit | Memory commit |",
            "| ----------- | ------------- |",
            *(f"| {code} | {memory} |" for code, memory in rows),
        ]
        return "\n".join(body) + "\n"

    def test_an_abbreviated_cell_is_carried_to_the_full_new_name(self) -> None:
        # The trap a textual find-and-replace falls into: an eight-character cell is a prefix, so
        # replacing the full ids leaves it naming the OLD commit and one row stays excluded.
        code = self.fixture.build_code(1)
        memory = self.fixture.build_memory(2)
        self.fixture.table([(code[0], memory[1])])
        result = self.fixture.apply(self.fixture.tip())
        new_memory = result.rewritten_ids[memory[1]]
        text = self.ledger_text([(code[0], memory[1][:_ABBREVIATION])])

        carried = carry_ledger_cells(self.fixture.memory, text, result.rewritten_ids)

        self.assertIn(f"| {code[0]} | {new_memory} |", carried)
        self.assertNotIn(memory[1][:_ABBREVIATION], carried)

    def test_the_code_column_and_the_code_base_are_carried_through_untouched(self) -> None:
        code = self.fixture.build_code(1)
        memory = self.fixture.build_memory(2)
        self.fixture.table([(code[0], memory[1])])
        result = self.fixture.apply(self.fixture.tip())
        new_memory = result.rewritten_ids[memory[1]]
        text = self.ledger_text([(code[0], memory[1])], base_memory=memory[1])

        carried = carry_ledger_cells(self.fixture.memory, text, result.rewritten_ids)

        self.assertIn(f"| {code[0]} | {new_memory} |", carried)
        self.assertIn(f'"baseCodeCommit": "{"c" * 40}"', carried)
        self.assertIn(f'"baseMemoryCommit": "{new_memory}"', carried)

    def test_a_cell_absent_from_the_map_is_carried_as_its_own_resolved_name(self) -> None:
        # `rewritten_ids` is total, so this cannot happen from `apply`; a partial map must still
        # carry an unmapped cell as itself rather than rewriting it into nothing.
        code = self.fixture.build_code(2)
        memory = self.fixture.build_memory(3)
        self.fixture.table([(code[0], memory[1])])
        result = self.fixture.apply(self.fixture.tip())
        new_memory = result.rewritten_ids[memory[1]]
        text = self.ledger_text([(code[0], memory[1]), (code[1], memory[2])])

        carried = carry_ledger_cells(self.fixture.memory, text, {memory[1]: new_memory})

        self.assertIn(f"| {code[0]} | {new_memory} |", carried)
        self.assertIn(f"| {code[1]} | {memory[2]} |", carried)

    def test_the_carried_table_validates_and_reads_with_no_exclusion(self) -> None:
        code = self.fixture.build_code(2)
        memory = self.fixture.build_memory(4)
        self.fixture.table([(code[1], memory[3]), (code[0], memory[2]), (code[1], memory[1])])
        result = self.fixture.apply(self.fixture.tip())
        final = self.fixture.migrate_table(result)

        ledger = parse_ledger_text((self.fixture.memory / "memory.md").read_text(encoding="utf-8"))
        self.assertEqual(ledger.rows[0].memory_commit, ledger.last_memory_content_commit)
        read = read_ledger_source(self.fixture.memory, final)
        self.assertEqual(read.excluded_rows, ())
        self.assertEqual({row.code_commit for row in read.ledger.rows}, {code[0], code[1]})


class MemoryBackfillCliTests(unittest.TestCase):
    """The public ``memory-backfill`` command, aimed exactly as the CLI aims it.

    This class exists because the review's second finding was invisible from every other test in
    this module: the kernel API was always called with a resolved full commit hash, while the CLI
    passes the contract's memory WORK BRANCH NAME. A rescue ref built from a name and then read
    back as a hash cannot compare equal to the name it was built from, so the ordinary apply
    refused after writing rescue refs and before rewriting anything, and a retry then tripped the
    existing-ref check. Nothing short of the real command path exercises that, so the fixture here
    drives ``run`` through the same parser the console script builds.
    """

    def setUp(self) -> None:
        self.fixture = BackfillFixture(memory_branch="ar/leaf")
        self.addCleanup(self.fixture.close)

    def contract_path(self) -> Path:
        """A leaf contract whose memory work branch is a NAME, exactly as a live one is."""

        path = self.fixture.root / "series-contract.md"
        path.write_text(
            "\n".join(
                [
                    "---",
                    "schema: ar-series-contract/v1",
                    "schemaVersion: 1.0",
                    "kind: leaf",
                    "task_id: 260913_TEST",
                    "task_name: 260913_ledger-commit-attribution",
                    "repo_name: agents-remember",
                    "workflow_kind: light-task",
                    "memory_mode: external",
                    "",
                    "coordination:",
                    f"  root: {self.fixture.root.as_posix()}",
                    f"  task_root: {self.fixture.root.as_posix()}",
                    f"  series_contract_path: {path.as_posix()}",
                    f"  task_artifact: {(self.fixture.root / 'task.md').as_posix()}",
                    f"  worktree_group: {self.fixture.root.as_posix()}",
                    "  leaf_id: 260913-TEST-L1",
                    "",
                    "code:",
                    f"  repo_path: {self.fixture.code.as_posix()}",
                    "  source_branch: main",
                    "  work_branch: ar/leaf",
                    "  base_commit: " + "a" * 40,
                    f"  worktree: {self.fixture.code.as_posix()}",
                    "",
                    "memory:",
                    "  mode: external",
                    f"  repo_path: {self.fixture.memory.as_posix()}",
                    "  source_branch: main",
                    f"  work_branch: {self.fixture.memory_branch}",
                    "  base_commit: " + "b" * 40,
                    f"  worktree: {self.fixture.memory.as_posix()}",
                    f"  ledger: {(self.fixture.memory / 'memory.md').as_posix()}",
                    "",
                    "human_review:",
                    "  status: pending-review",
                    "  approved_for_commit: no",
                    "",
                    "closeout:",
                    "  status: not-started",
                    "",
                    "integration:",
                    "  status: not-started",
                    "  cleanup: pending",
                    "---",
                    "",
                    "# Series Contract",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def invoke(self, *argv: str) -> int:
        """Drive the command exactly as ``agents-remember memory-backfill`` does."""

        parser = argparse.ArgumentParser(prog="agents-remember")
        sub = parser.add_subparsers(dest="command", required=True)
        backfill = sub.add_parser("memory-backfill")
        add_arguments(backfill)
        return run(parser.parse_args(["memory-backfill", *argv]))

    def test_the_cli_applies_a_branch_name_tip_and_survives_its_own_retry(self) -> None:
        # The tip is the contract's memory WORK BRANCH. Under the reviewed code this wrote rescue
        # refs and then refused, because the readback was a hash compared against the branch name;
        # the retry then failed on the rescue ref the first attempt had just created. Both halves
        # of that are exercised below: the first apply must succeed from a name, and a second
        # apply that meets its own rescue ref must still be able to do its work.
        code = self.fixture.build_code(2)
        memory = self.fixture.build_memory(3)
        self.fixture.table([(code[0], memory[2]), (code[1], memory[1])])
        contract = self.contract_path()
        before = self.fixture.tip()

        self.assertEqual(self.invoke("--contract", str(contract)), 1, "planning reports work left")
        self.assertEqual(self.invoke("--contract", str(contract), "--apply"), 0)
        after = self.fixture.tip()
        self.assertNotEqual(after, before, "the apply must have moved the branch")

        rescued = run_git(
            self.fixture.memory, ["rev-parse", "refs/backup/memory-pre-migration"]
        ).stdout.strip()
        self.assertEqual(rescued, before, "the rescue ref holds the tip the rewrite replaced")
        trailered = {
            parse_code_commit_trailer(
                run_git(self.fixture.memory, ["show", "-s", "--format=%B", commit]).stdout
            )
            for commit in run_git(self.fixture.memory, ["rev-list", after]).stdout.split()
        }
        self.assertEqual(
            trailered,
            {code[0], code[1], None},
            "the rewritten line carries both attributions the table recorded",
        )

        # The retry half. A second apply meets the rescue ref the FIRST run created, and under the
        # reviewed code that was unsatisfiable: the first attempt had written the refs and then
        # refused, so the retry's existing-ref check fired on refs its own predecessor made and it
        # could never reach the rewrite. This asserts the run completes and touches nothing --
        # the rescue ref still records the pre-rewrite tip, and the branch is where the first run
        # left it.
        self.assertEqual(
            self.invoke("--contract", str(contract), "--apply"),
            0,
            "a retry must not refuse on the rescue ref its own first attempt created",
        )
        self.assertEqual(
            run_git(
                self.fixture.memory, ["rev-parse", "refs/backup/memory-pre-migration"]
            ).stdout.strip(),
            before,
            "the retry must not move a rescue ref that already records the pre-rewrite tip",
        )
        self.assertEqual(self.fixture.tip(), after, "the retry must not move the branch")

    def test_the_cli_refuses_a_contract_whose_memory_repository_is_missing(self) -> None:
        contract = self.contract_path()
        contract.write_text(
            contract.read_text(encoding="utf-8").replace(
                self.fixture.memory.as_posix(), (self.fixture.root / "absent").as_posix()
            ),
            encoding="utf-8",
        )

        self.assertEqual(self.invoke("--contract", str(contract)), 2)


if __name__ == "__main__":
    unittest.main()
