"""MIK-R162: the restore proof of a sync merges a path the way the reapply merges it.

The reapply of parked work is ``git stash apply`` in the leaf's worktree. Git merges each path
by the driver the carried head's attributes name for it, the carried side first, under the
configuration of that worktree. The proof makes that one merge again with git itself and holds no
merge of its own. These cases run the real sync on scratch repositories and compare every tree
byte for byte.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application.worktree_services import (
    bind_worktree_services,
    build_default_worktree_services,
)
from agents_remember.worktrees import sync_transaction_authority
from agents_remember.worktrees import sync_transaction_git as sync_git
from agents_remember.worktrees.services import reset_worktree_services
from agents_remember.worktrees.sync_transaction_git import _proof as sync_git_proof
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncOperationStore,
    SyncSideRecord,
)
from test_worktree_sync import SyncFixture, git, section

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CATALOGS = ("tests/lanes.toml", "tests/lifecycle.toml")
BASE_ROWS = ("tests/test_a.py", "tests/test_m.py", "tests/test_z.py")
PARKED_ROW = "tests/test_parked.py"
LANDED_ROW = "tests/test_landed.py"
#: A PNG's first bytes: its signature holds a carriage return and a line feed.
PICTURE = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) + b"\rIEND\r\n"
#: Three contents of one union-merged file whose merge depends on the diff algorithm.
ROWS_BASE = b"c\nb\nb\nc\na\nb\n"
ROWS_LANDED = b"a\nc\nb\nb\nc\nb\n"
ROWS_PARKED = b"c\nb\nb\nb\nc\na\nb\n"
#: Their union under each algorithm (measured: git 2.55.0 gives the one its configuration
#: names, git 2.43.0 gives the histogram one whatever it is told).
ROWS_MERGED = {
    "histogram": b"c\nb\nb\nb\nc\na\nc\nb\nb\nc\nb\n",
    "myers": b"a\nc\nb\nb\nb\nc\nb\n",
}
#: A file a merge driver gives the parked side, and what a text merge would give instead.
DRIVEN_BASE, DRIVEN_LANDED, DRIVEN_PARKED = b"one\n", b"one\nline\n", b"parked\none\n"


def lane_list(*rows: str) -> bytes:
    """A catalog in the form of the real ones: one path per line in a list."""

    return ("[files]\nlane = [\n" + "".join(f'  "{row}",\n' for row in rows) + "]\n").encode()


#: What git gives for the two catalogs under each value of ``merge`` the repository uses, when
#: the landed line and the parked work each add a row after the same row. A new value in
#: ``.gitattributes`` needs its case here before the proof is known to reproduce it.
MERGE_CASES: dict[str, bytes] = {
    "union": lane_list(
        "tests/test_a.py", "tests/test_m.py", LANDED_ROW, PARKED_ROW, "tests/test_z.py"
    ),
}


def snapshot_tree(worktree: Path) -> dict[str, bytes]:
    """Every worktree byte below the checkout, keyed by relative path."""

    return {
        path.relative_to(worktree).as_posix(): path.read_bytes()
        for path in sorted(worktree.rglob("*"))
        if ".git" not in path.parts and (path.is_symlink() or path.is_file())
    }


def sync_record(fixture: SyncFixture) -> SyncOperationRecord:
    record = SyncOperationStore(fixture.contract.worktree_group).read()
    assert isinstance(record, SyncOperationRecord)
    return record


def share_base(fixture: SyncFixture, files: dict[str, bytes], message: str) -> None:
    """Commit files on the work branch and land them, so both lines start from them."""

    worktree = fixture.contract.code_worktree
    for name, content in files.items():
        (worktree / name).parent.mkdir(parents=True, exist_ok=True)
        (worktree / name).write_bytes(content)
    git(worktree, "add", "-A")
    git(worktree, "commit", "-m", message)
    git(fixture.code_repo, "merge", "--ff-only", fixture.contract.code_work_branch)


def land(fixture: SyncFixture, files: dict[str, bytes], message: str) -> str:
    """Land files on the official line and attribute memory to the new tip."""

    for name, content in files.items():
        (fixture.code_repo / name).write_bytes(content)
    git(fixture.code_repo, "add", "-A")
    git(fixture.code_repo, "commit", "-m", message)
    tip = git(fixture.code_repo, "rev-parse", "main")
    fixture.map_official_memory(tip)
    return tip


def park_rows_beside_landed_rows(fixture: SyncFixture, merge: str) -> str:
    """The case of L109 in small: both lines add a row at the same place of both catalogs."""

    attributes = "".join(f"{catalog} merge={merge}\n" for catalog in CATALOGS).encode()
    base = {catalog: lane_list(*BASE_ROWS) for catalog in CATALOGS}
    share_base(fixture, {".gitattributes": attributes, "other.txt": b"base line\n", **base}, "base")
    worktree = fixture.contract.code_worktree
    for catalog in CATALOGS:
        (worktree / catalog).write_bytes(lane_list(*BASE_ROWS[:2], PARKED_ROW, BASE_ROWS[2]))
    landed = {catalog: lane_list(*BASE_ROWS[:2], LANDED_ROW, BASE_ROWS[2]) for catalog in CATALOGS}
    return land(fixture, landed, "land a row in both catalogs")


def reapply_then(change):  # type: ignore[no-untyped-def]
    """The real reapply, followed by one change to the code worktree before the proof reads it."""

    real_apply = sync_transaction_authority.apply_parked_wip

    def apply(side: SyncSideRecord) -> tuple[str, tuple[str, ...]]:
        answer = real_apply(side)
        if side.side == "code":
            change(Path(side.worktree))
        return answer

    return mock.patch.object(sync_transaction_authority, "apply_parked_wip", side_effect=apply)


def drop_parked_byte_of_other(worktree: Path) -> None:
    (worktree / "other.txt").write_bytes(b"base line\n")


def park_rows_and_a_driven_file(fixture: SyncFixture, driver: str) -> str:
    """Park and land changes of ``rows.txt`` (union) and ``driven.txt`` (merged by ``driver``)."""

    attributes = f"rows.txt merge=union\ndriven.txt merge={driver}\n".encode()
    base = {"rows.txt": ROWS_BASE, "driven.txt": DRIVEN_BASE, "other.txt": b"base line\n"}
    share_base(fixture, {".gitattributes": attributes, **base}, "base rows and a driven file")
    worktree = fixture.contract.code_worktree
    (worktree / "rows.txt").write_bytes(ROWS_PARKED)
    (worktree / "driven.txt").write_bytes(DRIVEN_PARKED)
    (worktree / "other.txt").write_bytes(b"base line\nparked byte\n")
    return land(fixture, {"rows.txt": ROWS_LANDED, "driven.txt": DRIVEN_LANDED}, "land both")


def the_other_merge(given: bytes) -> bytes:
    """The union of the rows under the algorithm the reapply did not take."""

    assert given in ROWS_MERGED.values(), given
    return next(rows for rows in ROWS_MERGED.values() if rows != given)


def git_that_fails(failing: str, words: str):  # type: ignore[no-untyped-def]
    """The module's git with one command answering as a git that cannot run it."""

    real_run = sync_git.run_git

    def run(repository: Path, args: list[str], *more: object):  # type: ignore[no-untyped-def]
        if failing in " ".join(args):
            return subprocess.CompletedProcess(args, 128, "", f"{words}\n")
        return real_run(repository, args, *more)  # type: ignore[arg-type]

    return mock.patch.object(sync_git_proof, "run_git", side_effect=run)


def merge_values_in_use(root: Path) -> dict[str, list[str]]:
    """Each value of ``merge`` the tracked ``.gitattributes`` files give, with where it stands."""

    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", ":(glob)**/.gitattributes"],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    values: dict[str, list[str]] = {}
    for name in filter(None, listed.stdout.split("\0")):
        for number, line in enumerate((root / name).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            for flag, value in re.findall(r"\s([-!]?)merge(?:=(\S+))?(?=\s|$)", line):
                if flag != "!":
                    values.setdefault(value or f"{flag}merge", []).append(f"{name}:{number}")
            if re.search(r"\sbinary(?=\s|$)", line):
                values.setdefault("binary", []).append(f"{name}:{number}")
    return values


class ReapplyMergeTests(unittest.TestCase):
    """The proof and the cancel judge a path by what git's own reapply gives."""

    def setUp(self) -> None:
        # Fast-forwards onto converted memory run the validator (MIK-R61 rule 4).
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)

    def test_rows_added_at_one_place_of_a_merged_catalog_finish_the_sync(self) -> None:
        for merge, merged in MERGE_CASES.items():
            with self.subTest(merge=merge), tempfile.TemporaryDirectory() as tmp:
                fixture = SyncFixture(Path(tmp))
                worktree = fixture.contract.code_worktree
                tip = park_rows_beside_landed_rows(fixture, merge)
                (worktree / "other.txt").write_bytes(b"base line\nparked byte\n")
                expected = {**snapshot_tree(worktree), **dict.fromkeys(CATALOGS, merged)}

                with mock.patch.object(sync_git_proof, "run_git", wraps=sync_git.run_git) as runner:
                    result = fixture.sync()

                # One merge per proof (before the reapply and after it), not one per catalog.
                merges = [call for call in runner.call_args_list if "merge-tree" in call.args[1]]
                self.assertEqual(len(merges), 2)
                self.assertEqual(result.payload["state"], "synced", result.payload)
                self.assertEqual(section(result.payload, "code")["wip"]["state"], "restored")
                self.assertEqual(snapshot_tree(worktree), expected)
                self.assertEqual(git(worktree, "stash", "list"), "")
                self.assertEqual(git(worktree, "rev-parse", "HEAD"), tip)

    def test_the_merge_rule_is_the_carried_heads_wherever_the_repository_stands(self) -> None:
        # The landing itself brings the rule; the repository's own checkout stands elsewhere.
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            catalog = CATALOGS[0]
            share_base(fixture, {catalog: lane_list(*BASE_ROWS)}, "base catalog without a rule")
            before_the_rule = git(worktree, "rev-parse", "HEAD")
            (worktree / catalog).write_bytes(lane_list(*BASE_ROWS[:2], PARKED_ROW, BASE_ROWS[2]))
            landed = {
                ".gitattributes": f"{catalog} merge=union\n".encode(),
                catalog: lane_list(*BASE_ROWS[:2], LANDED_ROW, BASE_ROWS[2]),
            }
            tip = land(fixture, landed, "land the rule with a row")
            git(fixture.code_repo, "checkout", "--detach", before_the_rule)

            result = fixture.sync()

            self.assertEqual(result.payload["state"], "synced", result.payload)
            self.assertEqual((worktree / catalog).read_bytes(), MERGE_CASES["union"])
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), tip)
            self.assertEqual(git(worktree, "stash", "list"), "")

    def test_the_merge_rule_is_the_carried_heads_when_the_parked_work_takes_it_away(self) -> None:
        # The reapply merges under the attributes of the head it runs on. A parked change of
        # ``.gitattributes`` reaches the tree only as part of the reapply's result.
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            tip = park_rows_beside_landed_rows(fixture, "union")
            without_the_rule = b"# the parked work takes the rule away\n"
            (worktree / ".gitattributes").write_bytes(without_the_rule)

            result = fixture.sync()

            self.assertEqual(result.payload["state"], "synced", result.payload)
            for catalog in CATALOGS:
                self.assertEqual((worktree / catalog).read_bytes(), MERGE_CASES["union"])
            self.assertEqual((worktree / ".gitattributes").read_bytes(), without_the_rule)
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), tip)
            self.assertEqual(git(worktree, "stash", "list"), "")

    def test_a_parked_row_missing_from_a_union_merged_catalog_fails_the_proof_on_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            park_rows_beside_landed_rows(fixture, "union")
            damaged, intact = CATALOGS

            def drop_parked_row(tree: Path) -> None:
                held = (tree / damaged).read_bytes()
                (tree / damaged).write_bytes(held.replace(f'  "{PARKED_ROW}",\n'.encode(), b""))

            with reapply_then(drop_parked_row):
                result = fixture.sync()

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "sync-git-proof-failed", result.payload)
            summary = str(result.payload["summary"])
            self.assertIn("1 path holds none of the parked bytes", summary)
            self.assertIn(f"{damaged} (merge=union)", summary)
            self.assertNotIn(intact, summary)
            self.assertEqual((worktree / intact).read_bytes(), MERGE_CASES["union"])
            self.assertNotEqual(git(worktree, "stash", "list"), "")
            record = sync_record(fixture)
            self.assertEqual((record.phase, record.code.wipState), ("running-code", "parked"))

    def test_cancel_after_a_stop_takes_the_reapplied_union_catalogs_as_its_own(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            park_rows_beside_landed_rows(fixture, "union")
            (worktree / "other.txt").write_bytes(b"base line\nparked byte\n")
            parked_tree = snapshot_tree(worktree)
            pre_sync = git(worktree, "rev-parse", "HEAD")

            with reapply_then(drop_parked_byte_of_other):
                stopped = fixture.sync()
            self.assertEqual(stopped.payload["state"], "sync-git-proof-failed", stopped.payload)
            for catalog in CATALOGS:
                self.assertEqual((worktree / catalog).read_bytes(), MERGE_CASES["union"])

            cancelled = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), pre_sync)
            self.assertEqual(snapshot_tree(worktree), parked_tree)
            self.assertEqual(git(worktree, "stash", "list"), "")
            self.assertIn("1 path holds none of the parked bytes: other.txt.", str(stopped.payload))

    def test_cancel_takes_a_file_staged_as_added_at_parking_as_its_own(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            share_base(fixture, {"other.txt": b"base line\n"}, "base other")
            picture = worktree / "docs" / "picture.png"
            picture.parent.mkdir()
            picture.write_bytes(PICTURE)
            picture.chmod(0o755)
            git(worktree, "add", "docs/picture.png")
            (worktree / "other.txt").write_bytes(b"base line\nparked byte\n")
            parked_tree = snapshot_tree(worktree)
            parked_status = git(worktree, "status", "--porcelain")
            pre_sync = git(worktree, "rev-parse", "HEAD")
            fixture.map_official_memory(fixture.move_official_code())

            with reapply_then(drop_parked_byte_of_other):
                stopped = fixture.sync()
            self.assertEqual(stopped.payload["state"], "sync-git-proof-failed", stopped.payload)
            self.assertIn("1 path holds none of the parked bytes: other.txt.", str(stopped.payload))
            self.assertEqual(git(worktree, "status", "--porcelain", "docs"), "A  docs/picture.png")

            cancelled = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), pre_sync)
            self.assertEqual(snapshot_tree(worktree), parked_tree)
            self.assertEqual(git(worktree, "status", "--porcelain"), parked_status)
            self.assertEqual(git(worktree, "stash", "list"), "")

    def test_a_plain_file_is_proven_by_gits_merge_not_by_a_plain_file_merge(self) -> None:
        # Git's merge places these lines without a conflict; ``git merge-file`` on the same
        # three contents reports one (measured on git 2.43.0 and 2.55.0).
        base = b"c\nc\na\nc\nb\nb\na\nb\nb\nb\n"
        landed = b"c\nc\na\nc\nb\na\nb\na\nb\n"
        parked = b"c\nc\na\nc\nb\nb\nb\na\nb\nb\nb\n"
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            share_base(fixture, {"notes.txt": base}, "base notes")
            (worktree / "notes.txt").write_bytes(parked)
            tip = land(fixture, {"notes.txt": landed}, "land other lines of the notes")

            result = fixture.sync()

            self.assertEqual(result.payload["state"], "synced", result.payload)
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), tip)
            self.assertEqual(git(worktree, "status", "--porcelain"), "M notes.txt")
            self.assertEqual(git(worktree, "stash", "list"), "")

    def test_the_reapply_under_a_configured_diff_algorithm_is_proven(self) -> None:
        # A newer git's reapply follows ``diff.algorithm`` (measured: 2.52.0 and 2.55.0); an
        # older one merges with histogram diffs whatever it is told (2.43.0 to 2.49.1).
        for algorithm in ("myers", "histogram", ""):
            with self.subTest(algorithm=algorithm), tempfile.TemporaryDirectory() as tmp:
                fixture = SyncFixture(Path(tmp))
                worktree = fixture.contract.code_worktree
                if algorithm:
                    git(fixture.code_repo, "config", "diff.algorithm", algorithm)
                tip = park_rows_and_a_driven_file(fixture, "union")
                given: list[bytes] = []

                def keep_rows(tree: Path, given: list[bytes] = given) -> None:
                    given.append((tree / "rows.txt").read_bytes())

                with reapply_then(keep_rows):
                    result = fixture.sync()

                self.assertEqual(result.payload["state"], "synced", result.payload)
                self.assertIn(given[0], ROWS_MERGED.values())
                self.assertEqual((worktree / "rows.txt").read_bytes(), given[0])
                self.assertEqual(git(worktree, "rev-parse", "HEAD"), tip)
                self.assertEqual(git(worktree, "stash", "list"), "")

    def test_the_merge_of_another_diff_algorithm_does_not_pass_the_proof(self) -> None:
        # What git gives under the algorithm this reapply did not take is not the reapply's.
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            git(fixture.code_repo, "config", "diff.algorithm", "myers")
            park_rows_and_a_driven_file(fixture, "union")

            def write_the_other_merge(tree: Path) -> None:
                (tree / "rows.txt").write_bytes(the_other_merge((tree / "rows.txt").read_bytes()))

            with reapply_then(write_the_other_merge):
                result = fixture.sync()

            self.assertEqual(result.payload["state"], "sync-git-proof-failed", result.payload)
            summary = str(result.payload["summary"])
            self.assertIn("1 path holds none of the parked bytes: rows.txt (merge=union).", summary)
            self.assertNotEqual(git(worktree, "stash", "list"), "")
            record = sync_record(fixture)
            self.assertEqual((record.phase, record.code.wipState), ("running-code", "parked"))

    def test_cancel_saves_a_later_edit_that_equals_another_algorithms_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            git(fixture.code_repo, "config", "diff.algorithm", "myers")
            park_rows_and_a_driven_file(fixture, "union")
            with reapply_then(drop_parked_byte_of_other):
                stopped = fixture.sync()
            self.assertEqual(stopped.payload["state"], "sync-git-proof-failed", stopped.payload)
            self.assertIn("1 path holds none of the parked bytes: other.txt.", str(stopped.payload))
            # The stop is repaired; the only later work is an edit of the reapplied rows.
            (worktree / "other.txt").write_bytes(b"base line\nparked byte\n")
            later_edit = the_other_merge((worktree / "rows.txt").read_bytes())
            (worktree / "rows.txt").write_bytes(later_edit)
            record = sync_record(fixture)
            generation = record.generation

            # MIK-R61 rule 12: no way out is manual. The later edit is saved
            # first, then the cancel completes.
            cancelled = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            self.assertIn("could not attribute", str(cancelled.payload["summary"]))
            saved = (
                fixture.contract.worktree_group
                / "scratch"
                / f"sync-cancel-generation-{generation}"
                / "code"
            )
            listed = (saved / "paths.txt").read_text(encoding="utf-8").splitlines()
            self.assertIn("rows.txt", listed)
            held = (saved / "staged.patch").read_bytes() + (saved / "unstaged.patch").read_bytes()
            self.assertIn(b"+parked byte\n", held)
            self.assertIn(b"+c\n+b\n+b\n+b\n+c\n", held)

    def test_configuration_of_the_worktree_alone_is_the_proofs_configuration(self) -> None:
        # The reapply runs in the leaf's worktree; a diff algorithm and a merge driver set
        # for that worktree alone are its configuration, and the repository's own has neither.
        for stop in (False, True):
            with self.subTest(stop=stop), tempfile.TemporaryDirectory() as tmp:
                fixture = SyncFixture(Path(tmp))
                worktree = fixture.contract.code_worktree
                git(fixture.code_repo, "config", "extensions.worktreeConfig", "true")
                git(worktree, "config", "--worktree", "diff.algorithm", "myers")
                git(worktree, "config", "--worktree", "merge.parked-wins.driver", "cp %B %A")
                tip = park_rows_and_a_driven_file(fixture, "parked-wins")
                parked_tree = snapshot_tree(worktree)
                pre_sync = git(worktree, "rev-parse", "HEAD")
                given: list[bytes] = []

                def after_reapply(
                    tree: Path, stop: bool = stop, given: list[bytes] = given
                ) -> None:
                    given.append((tree / "rows.txt").read_bytes())
                    if stop:
                        drop_parked_byte_of_other(tree)

                with reapply_then(after_reapply):
                    result = fixture.sync()

                self.assertIn(given[0], ROWS_MERGED.values())
                self.assertEqual((worktree / "rows.txt").read_bytes(), given[0])
                self.assertEqual((worktree / "driven.txt").read_bytes(), DRIVEN_PARKED)
                if not stop:
                    self.assertEqual(result.payload["state"], "synced", result.payload)
                    self.assertEqual(git(worktree, "rev-parse", "HEAD"), tip)
                    self.assertEqual(git(worktree, "stash", "list"), "")
                    continue
                self.assertEqual(result.payload["state"], "sync-git-proof-failed", result.payload)
                self.assertIn(
                    "1 path holds none of the parked bytes: other.txt.", str(result.payload)
                )

                cancelled = fixture.sync(resolution_action="cancel")

                self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
                self.assertEqual(git(worktree, "rev-parse", "HEAD"), pre_sync)
                self.assertEqual(snapshot_tree(worktree), parked_tree)
                self.assertEqual(git(worktree, "stash", "list"), "")

    def test_a_driver_the_proof_cannot_start_fails_its_path_and_names_the_driver(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as programs:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            program = Path(programs) / "parked-wins"
            program.write_text('#!/bin/sh\ncp "$2" "$1"\n', encoding="utf-8")
            program.chmod(0o755)
            git(fixture.code_repo, "config", "merge.scripted.driver", f"{program} %A %B")
            park_rows_and_a_driven_file(fixture, "scripted")
            parked_tree = snapshot_tree(worktree)
            pre_sync = git(worktree, "rev-parse", "HEAD")

            # The reapply runs the driver; its program is gone when the proof merges again.
            with reapply_then(lambda _tree: program.rename(program.with_name("moved-away"))):
                result = fixture.sync()

            self.assertEqual(result.payload["state"], "sync-git-proof-failed", result.payload)
            summary = str(result.payload["summary"])
            self.assertIn(
                "1 path holds none of the parked bytes: driven.txt (merge=scripted).", summary
            )
            self.assertEqual((worktree / "driven.txt").read_bytes(), DRIVEN_PARKED)
            self.assertNotEqual(git(worktree, "stash", "list"), "")

            program.with_name("moved-away").rename(program)
            cancelled = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), pre_sync)
            self.assertEqual(snapshot_tree(worktree), parked_tree)
            self.assertEqual(git(worktree, "stash", "list"), "")

    def test_a_merge_git_cannot_make_fails_its_path_and_no_other_merge_stands_in(self) -> None:
        # Both sides change the file at different places: a plain file merge would give the
        # reapply's bytes here, and the proof must not take it in place of git's own merge.
        faults = {
            "merge-tree": "fatal: unable to read attribute source",
            "config --get diff.algorithm": "fatal: bad config line 1 in file .git/config",
        }
        base = "".join(f"line {index:02d}\n" for index in range(1, 21))
        for failing, words in faults.items():
            with self.subTest(failing=failing), tempfile.TemporaryDirectory() as tmp:
                fixture = SyncFixture(Path(tmp))
                worktree = fixture.contract.code_worktree
                share_base(
                    fixture,
                    {".gitattributes": b"notes.txt merge=union\n", "notes.txt": base.encode()},
                    "base notes",
                )
                (worktree / "notes.txt").write_bytes(f"parked top\n{base}".encode())
                parked_tree = snapshot_tree(worktree)
                pre_sync = git(worktree, "rev-parse", "HEAD")
                land(fixture, {"notes.txt": f"{base}landed end\n".encode()}, "land an end line")
                said = f"git {failing} ended with 128: {words}"

                with git_that_fails(failing, words) as runner:
                    stopped = fixture.sync()
                    reapplied = snapshot_tree(worktree)
                    # MIK-R61 rule 12: while git cannot make the merge, the
                    # cancel cannot own the path — but no way out is manual
                    # either. It saves the unproven state first, then cancels.
                    saved_first = fixture.sync(resolution_action="cancel")

                self.assertEqual(stopped.payload["state"], "sync-git-proof-failed", stopped.payload)
                summary = str(stopped.payload["summary"])
                self.assertIn("none of the parked bytes: notes.txt (merge=union).", summary)
                self.assertIn(f"Git could not make the reapply's merge again ({said})", summary)
                self.assertFalse(
                    [call for call in runner.call_args_list if "merge-file" in call.args[1]]
                )
                self.assertEqual(reapplied["notes.txt"], f"parked top\n{base}landed end\n".encode())
                self.assertEqual(
                    saved_first.payload["state"], "sync-cancelled", saved_first.payload
                )
                self.assertIn(
                    said,
                    str(saved_first.payload["summary"]),
                )
                self.assertIn("could not attribute", str(saved_first.payload["summary"]))
                self.assertEqual(git(worktree, "rev-parse", "HEAD"), pre_sync)
                self.assertEqual(snapshot_tree(worktree), parked_tree)
                self.assertEqual(git(worktree, "stash", "list"), "")
                # F04: the save-first cancel is repeatable: a second cancel
                # answers terminally without touching anything.
                again = fixture.sync(resolution_action="cancel")
                self.assertEqual(again.payload["state"], "sync-cancelled", again.payload)
                self.assertIn("already cancelled", str(again.payload["summary"]))
                self.assertEqual(snapshot_tree(worktree), parked_tree)

    def test_every_merge_rule_of_the_repository_has_its_case(self) -> None:
        unknown = {
            value: places
            for value, places in merge_values_in_use(REPOSITORY_ROOT).items()
            if value not in MERGE_CASES
        }
        self.assertEqual(
            unknown,
            {},
            "The repository's .gitattributes name a merge the sync's restore proof has no case "
            f"for: {unknown}. Add the value to MERGE_CASES with the bytes git gives, so that "
            "test_rows_added_at_one_place_of_a_merged_catalog_finish_the_sync shows the proof "
            "reproduces it.",
        )
        self.assertIn("union", merge_values_in_use(REPOSITORY_ROOT))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
