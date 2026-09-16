"""D18: the citation source index indexes Git's own population, and the caps keep their teeth.

THE DEFECT THIS MAKES IMPOSSIBLE
--------------------------------
The contract-scoped ``memory_quality_check`` is a mandated step of every leaf's curation and of the
final verification, and it could not run at all on a worktree carrying the eve runtime state. It
returned an **error** instead of ``ok=false``::

    citation source-index input exceeds the 4194304-byte per-file cap:
    ['eve_runtime/.eve/dev-hosts/…/nitro/dev/index.mjs', …]

``eve_runtime/.eve/`` is gitignored and absent from Git membership; the explicit candidate route
read membership correctly all along. Only the default acquisition walk ingested it, because
``_tree_state`` walked the code root with ``os.walk`` and filtered by ``SKIPPED_DIRECTORIES`` and
``SKIPPED_SUFFIXES`` alone -- neither of which names ``.eve`` or ``.mjs``. Two independent refusals
were in play: 34 machine-local files over the per-file cap, and an aggregate over the 64 MiB cap.
A check whose ability to run depends on which machine-local artifacts happen to be present is worse
than one that always refuses, because the same command reports a different verdict per checkout.

THE POPULATION RULE, AND THE ONE THE REMEDY DOES *NOT* MEAN
----------------------------------------------------------
The walk asks Git for the paths that belong to the code root -- ``ls-files --cached --others
--exclude-standard``: tracked, plus untracked-but-not-ignored -- and indexes those. Reading the
remedy as "skip every untracked path" was measured and rejected: it re-reds eight landed citation
cases (`test_memory_citation_fix_scopes.py`, `test_memory_citation_grammars.py`), because a curator
cites code a leaf has written and not yet committed, and those fixtures build exactly that state.
Only what Git calls *ignored* is removed, and the untracked case below pins that half of the rule.

WHAT DEFENDS WHAT
-----------------
    reading                                              defended by
    --------------------------------------------------   ---------------------------------------
    a gitignored scratch tree is not the candidate        the ignored-oversized case: a file above
                                                           the per-file cap under an ignored
                                                           directory is not indexed and does not
                                                           refuse the run
    the population is "not ignored", not "tracked"        the untracked case: a fresh, unignored
                                                           working-tree source is still indexed
    the per-file cap still refuses                        the tracked-oversized case: the same
                                                           bytes, tracked, still raise by name --
                                                           and the untracked-oversized case shows
                                                           the cap is a content bound, not a Git one
    the aggregate cap still refuses                       the tracked-aggregate case: 17 files each
                                                           just under the per-file cap cross 64 MiB
                                                           with no oversized file involved
    a plain directory still works                         the non-work-tree case: outside Git the
                                                           documented walk is kept, so an
                                                           oversized file there still refuses
    this checkout can run the mandated check              the tip case: the real population is
                                                           enumerated and passes the same bounds

WHAT THIS DOES NOT COVER (stated, not implied)
----------------------------------------------
- **The population is not a content judgement.** A candidate file is indexed whether or not it is
  text; the walk's suffix skip list and the caps decide the rest.
- **Scope is the default acquisition walk.** The explicit candidate route already read Git
  membership and is unchanged.
- **The aggregate cap is not re-scoped here.** If the population of this repository ever crosses it,
  the tip case fails on purpose: the fix for that is a deliberate decision about the declared bound,
  not a wider walk.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from agents_remember.memory_quality.style.citations import source_index
from agents_remember.memory_quality.style.citations.resolution import Trees
from agents_remember.memory_quality.style.citations.source_index_state import (
    MAX_SOURCE_BYTES,
    MAX_SOURCE_FILE_BYTES,
    SourceIndexError,
    check_source_bounds,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PER_FILE_CAP = MAX_SOURCE_FILE_BYTES
AGGREGATE_CAP = MAX_SOURCE_BYTES
# A memory root that is outside the code root under test. The walk excludes the memory root, and
# none of these trees has one; naming a path outside keeps that exclusion out of the measurement.
_ABSENT_MEMORY_ROOT = Path(tempfile.gettempdir()) / "ar-citation-index-no-memory-root"


def git(root: Path, *args: str) -> str:
    """Run one fixture Git command; a failure is the fixture's, so it is raised, not swallowed."""

    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def sparse(path: Path, size: int) -> None:
    """Create a file whose STAT size is ``size`` without writing the bytes.

    The caps read ``Identity.size``, which is a stat, so the subject under test is the reported
    size -- writing 64 MiB of zeros would measure the disk instead.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size)


def indexed(code_root: Path) -> tuple[tuple[str, ...], int]:
    """The default walk's population for ``code_root``, as paths and total bytes."""

    trees = Trees(code_root=code_root, memory_root=_ABSENT_MEMORY_ROOT)
    state = source_index._tree_state(trees)
    check_source_bounds(tuple(one.identity for one in state.files))
    return tuple(one.identity.path for one in state.files), sum(
        one.identity.size for one in state.files
    )


class GitMembershipSourceIndexTests(unittest.TestCase):
    """A worktree carrying a gitignored scratch tree is still indexable, and the caps still bite."""

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory(prefix="ar-citation-membership-")
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        self.code = self.tmp / "code"
        self.code.mkdir()
        git(self.code, "init", "--quiet")
        git(self.code, "config", "user.email", "fixture@example.invalid")
        git(self.code, "config", "user.name", "Fixture")
        (self.code / ".gitignore").write_text("/scratch/\n", encoding="utf-8")
        (self.code / "src").mkdir()
        (self.code / "src" / "tracked.py").write_text("x = 1\n", encoding="utf-8")
        git(self.code, "add", "--all")
        git(self.code, "commit", "--quiet", "-m", "tracked")

    def test_a_gitignored_oversized_scratch_file_is_not_indexed_and_does_not_refuse(self) -> None:
        sparse(self.code / "scratch" / "dev-hosts" / "nitro" / "index.mjs", PER_FILE_CAP + 1)
        self.assertEqual(
            git(self.code, "status", "--porcelain", "--ignored", "scratch").split()[0], "!!"
        )
        paths, _total = indexed(self.code)
        self.assertIn("src/tracked.py", paths)
        self.assertNotIn("scratch/dev-hosts/nitro/index.mjs", paths)

    def test_an_untracked_but_unignored_source_is_still_indexed(self) -> None:
        """The population is Git's "not ignored", NOT "tracked only" -- and this is why.

        A curator cites code a leaf has written but not yet committed, and the citation-repair
        fixtures build exactly that state. Reading the D18 remedy as "skip every untracked path"
        re-reds eight landed citation cases (`test_memory_citation_fix_scopes.py`,
        `test_memory_citation_grammars.py`), so the candidate population asks Git for what belongs
        to the work tree -- tracked, plus untracked-but-not-ignored -- and removes only what Git
        calls ignored. This case pins the second half of that rule.
        """

        (self.code / "src" / "fresh.py").write_text("z = 3\n", encoding="utf-8")
        self.assertEqual(
            git(self.code, "status", "--porcelain", "--", "src/fresh.py"), "?? src/fresh.py"
        )
        paths, _total = indexed(self.code)
        self.assertIn("src/fresh.py", paths)

    def test_a_tracked_oversized_file_still_refuses_the_index(self) -> None:
        sparse(self.code / "src" / "oversized.bin", PER_FILE_CAP + 1)
        git(self.code, "add", "--all")
        with self.assertRaises(SourceIndexError) as caught:
            indexed(self.code)
        self.assertIn("per-file", str(caught.exception))
        self.assertIn("src/oversized.bin", str(caught.exception))

    def test_an_untracked_oversized_file_still_refuses_the_index(self) -> None:
        """Not ignored is not the same as not oversized: the cap is a content bound, not a Git one."""

        sparse(self.code / "src" / "fresh-oversized.bin", PER_FILE_CAP + 1)
        with self.assertRaises(SourceIndexError) as caught:
            indexed(self.code)
        self.assertIn("src/fresh-oversized.bin", str(caught.exception))

    def test_a_tracked_population_above_the_aggregate_cap_still_refuses(self) -> None:
        each = AGGREGATE_CAP // 16 - 1
        self.assertLess(each, PER_FILE_CAP)
        for index in range(17):
            sparse(self.code / "src" / f"part-{index:02d}.bin", each)
        git(self.code, "add", "--all")
        with self.assertRaises(SourceIndexError) as caught:
            indexed(self.code)
        self.assertIn("above its", str(caught.exception))
        self.assertNotIn("per-file", str(caught.exception))

    def test_a_root_outside_a_work_tree_keeps_the_git_independent_walk(self) -> None:
        plain = self.tmp / "plain"
        (plain / "nested").mkdir(parents=True)
        (plain / "nested" / "untracked.py").write_text("y = 2\n", encoding="utf-8")
        sparse(plain / "nested" / "oversized.bin", PER_FILE_CAP + 1)
        self.assertIsNone(
            source_index._git_candidate_paths(plain),
            "this case's subject is the non-work-tree fallback; the fixture tree is inside a "
            "repository, so the walk can no longer be reached here",
        )
        with self.assertRaises(SourceIndexError) as caught:
            indexed(plain)
        self.assertIn("nested/oversized.bin", str(caught.exception))


class ThisCheckoutsCitationIndexBoundsTests(unittest.TestCase):
    """The mandated memory-quality check has to be able to run HERE, at this checkout's own tip."""

    def test_the_code_roots_candidate_population_is_inside_the_citation_caps(self) -> None:
        paths, total = indexed(REPOSITORY_ROOT)
        self.assertGreater(len(paths), 0, "the walk indexed nothing, so this case proves nothing")
        self.assertLessEqual(
            total,
            AGGREGATE_CAP,
            f"this checkout's indexed population is {total} bytes, above the {AGGREGATE_CAP}-byte "
            "citation source cap, so the contract-scoped memory_quality_check cannot run here: "
            "decide the declared bound deliberately (it is not a walk-population defect)",
        )
        members = source_index._git_candidate_paths(REPOSITORY_ROOT)
        if members is None:
            self.fail(
                "this checkout is not a Git work tree, so its candidate population is unknown"
            )
        self.assertEqual(
            sorted(path for path in paths if path not in members),
            [],
            "the default walk indexed a path outside Git's own population for this work tree",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
