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
    the per-file cap still bites, as a REPORT           the tracked-oversized case: the same bytes,
                                                           tracked, are skipped with their path and
                                                           size in the report -- and the
                                                           untracked-oversized case shows the cap is
                                                           a content bound, not a Git one
    the aggregate cap still bites, as a REPORT           the tracked-aggregate case: 17 files each
                                                           just under the per-file cap cross the
                                                           aggregate cap with no oversized file
                                                           involved, and the largest are skipped by
                                                           name instead of refusing the tree
    a plain directory still works, and its               the non-work-tree cases: outside Git the
    .gitignore is honoured by the register               documented walk is kept, an oversized file
                                                           there is reported, and the root's
                                                           .gitignore is applied by the register's
                                                           own bounded matcher
    this checkout can run the mandated check              the tip case: the real population is
                                                           enumerated and passes the same bounds

CHANGED BY LEAF 260915-CAPS-L14 (declared cross-leaf change)
-----------------------------------------------------------
This module was landed by leaf 260915-CAPS-L13/L16 to close D18. Its four cap cases asserted the
pre-ruling behaviour -- ``check_source_bounds`` raising on an oversized file and on an aggregate
over the 64 MiB cap. The developer's 2026-08-20 ruling (carried verbatim in the CAPS-R14 packet)
replaced that: an oversized file is **skipped with a report entry naming it and its size**, the
aggregate cap defaults to 512 MiB and is applied to the post-exclusion/post-skip set, and a hard
stop remains only past ~2 GiB. So exactly those four cases are re-pointed at the ruled behaviour --
same fixtures, same subjects, same file names -- and the aggregate-bound case the non-Git fallback
branch never had is added. Every D18 case is unchanged. The population rule this module exists to
pin ("Git's own population, not 'tracked only'") is untouched.

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

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.memory_quality.style.citations import source_index
from agents_remember.memory_quality.style.citations.resolution import Trees
from agents_remember.memory_quality.style.citations.source_index_state import (
    MAX_SOURCE_BYTES,
    MAX_SOURCE_FILE_BYTES,
    SKIP_PER_FILE_CAP,
    SKIP_TOTAL_CAP,
    STATUS_CAPPED,
    SourceSkip,
    TreeState,
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


def indexed(code_root: Path, memory_root: Path = _ABSENT_MEMORY_ROOT) -> TreeState:
    """The default acquisition's tree record for ``code_root``: population, register, report.

    The caps are applied by the acquisition itself and reported on the record, so a case reads
    what was indexed and what was skipped from one place. Nothing here calls a cap helper with
    hand-supplied identities -- that would be the seam-plus-caller-supplied-value shape this
    master forbids.
    """

    return source_index._tree_state(Trees(code_root=code_root, memory_root=memory_root))


def paths_of(state: TreeState) -> tuple[str, ...]:
    return tuple(one.identity.path for one in state.files)


def bytes_of(state: TreeState) -> int:
    return sum(one.identity.size for one in state.files)


def skipped(state: TreeState, path: str) -> SourceSkip | None:
    """The report entry for ``path``, or ``None`` when the acquisition did not skip it."""
    for one in state.bounds.skipped:
        if one.path == path:
            return one
    return None


def write_citation_settings(memory_root: Path, block: dict[str, object]) -> None:
    """Author the memory layer's own settings file, the way a user's exclusion review does."""
    (memory_root / "system").mkdir(parents=True, exist_ok=True)
    (memory_root / "system" / "settings.json").write_text(
        json.dumps({"onboarding": block}), encoding="utf-8"
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
        state = indexed(self.code)
        paths = paths_of(state)
        self.assertIn("src/tracked.py", paths)
        self.assertNotIn("scratch/dev-hosts/nitro/index.mjs", paths)
        # Git applied the ignore rule, and the record says so: the pattern is carried even
        # though this acquisition did not apply it.
        self.assertEqual(state.exclusions.gitignore_authority, "git")
        self.assertIn("/scratch/", state.exclusions.gitignore_patterns)

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
        paths = paths_of(indexed(self.code))
        self.assertIn("src/fresh.py", paths)

    def test_a_tracked_oversized_file_is_skipped_and_reported_not_refused(self) -> None:
        """The cap keeps its teeth: the file is out of the index AND named, with its size.

        The size on the right is read here from the filesystem, not taken from the report, so
        the two sides of the assertion do not come from one artifact.
        """

        oversized = self.code / "src" / "oversized.bin"
        sparse(oversized, PER_FILE_CAP + 1)
        measured = oversized.stat().st_size
        git(self.code, "add", "--all")
        state = indexed(self.code)
        self.assertIn("src/tracked.py", paths_of(state))
        self.assertNotIn("src/oversized.bin", paths_of(state))
        entry = skipped(state, "src/oversized.bin")
        self.assertIsNotNone(entry, f"not reported: {state.bounds.to_dict()}")
        assert entry is not None
        self.assertEqual(entry.reason, SKIP_PER_FILE_CAP)
        self.assertEqual(entry.size, measured)
        self.assertEqual(state.bounds.status, STATUS_CAPPED)

    def test_an_untracked_oversized_file_is_skipped_and_reported(self) -> None:
        """Not ignored is not the same as not oversized: the cap is a content bound, not a Git one."""

        oversized = self.code / "src" / "fresh-oversized.bin"
        sparse(oversized, PER_FILE_CAP + 1)
        state = indexed(self.code)
        self.assertNotIn("src/fresh-oversized.bin", paths_of(state))
        entry = skipped(state, "src/fresh-oversized.bin")
        self.assertIsNotNone(entry, f"not reported: {state.bounds.to_dict()}")
        assert entry is not None
        self.assertEqual(entry.size, oversized.stat().st_size)

    def test_a_tracked_population_above_the_aggregate_cap_is_skipped_and_reported(self) -> None:
        """No oversized file is involved: the aggregate cap alone decides, and it reports.

        ``AGGREGATE_CAP / PER_FILE_CAP`` files sit just under the per-file cap, so the only
        clause in play is the aggregate one.
        """

        each = PER_FILE_CAP - 1
        count = AGGREGATE_CAP // each + 1
        self.assertGreater(count * each, AGGREGATE_CAP)
        for index in range(count):
            sparse(self.code / "src" / f"part-{index:03d}.bin", each)
        git(self.code, "add", "--all")
        state = indexed(self.code)
        self.assertEqual(state.bounds.status, STATUS_CAPPED)
        self.assertLessEqual(bytes_of(state), AGGREGATE_CAP)
        dropped = [one for one in state.bounds.skipped if one.reason == SKIP_TOTAL_CAP]
        self.assertTrue(dropped, f"no aggregate skip reported: {state.bounds.to_dict()}")
        for one in dropped:
            self.assertRegex(one.path, r"^src/part-\d{3}\.bin$")
            self.assertEqual(one.size, each)
            self.assertNotIn(one.path, paths_of(state))
        # The report is bounded, and its count is exact rather than a sample length.
        self.assertGreaterEqual(state.bounds.skipped_count, len(dropped))

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
        state = indexed(plain)
        self.assertIn("nested/untracked.py", paths_of(state))
        entry = skipped(state, "nested/oversized.bin")
        self.assertIsNotNone(entry, f"not reported: {state.bounds.to_dict()}")
        assert entry is not None
        self.assertEqual(entry.reason, SKIP_PER_FILE_CAP)

    def test_the_non_git_fallback_applies_the_aggregate_bound_and_reports_it(self) -> None:
        """The gap L16's curator flagged for this module: the fallback branch had no aggregate case.

        The bound is moved through the memory layer's own settings block rather than by writing
        512 MiB, because the subject is the *rule*, not the size: ``maxSourceBytes`` is a
        settings key, and a tree past it must produce a reported skip list on the walk path
        exactly as it does on the Git path.
        """

        plain = self.tmp / "plain-aggregate"
        (plain / "nested").mkdir(parents=True)
        each = 4096
        for index in range(6):
            (plain / "nested" / f"part-{index}.py").write_text("x" * each, encoding="utf-8")
        memory = self.tmp / "memory-aggregate"
        write_citation_settings(memory, {"citationIndex": {"maxSourceBytes": 4 * each}})
        self.assertIsNone(source_index._git_candidate_paths(plain))
        state = indexed(plain, memory)
        self.assertEqual(state.bounds.caps.max_source_bytes, 4 * each)
        self.assertEqual(state.bounds.status, STATUS_CAPPED)
        self.assertEqual(state.bounds.skipped_count, 2)
        self.assertLessEqual(bytes_of(state), 4 * each)
        for one in state.bounds.skipped:
            self.assertEqual(one.reason, SKIP_TOTAL_CAP)
            self.assertNotIn(one.path, paths_of(state))

    def test_the_non_git_fallback_honours_the_roots_gitignore_through_the_register(self) -> None:
        """Outside a work tree nothing applies the ignore file for us, so the register must."""

        plain = self.tmp / "plain-ignored"
        (plain / "keep").mkdir(parents=True)
        (plain / "keep" / "kept.py").write_text("k = 1\n", encoding="utf-8")
        (plain / "generated").mkdir(parents=True)
        (plain / "generated" / "huge.py").write_text("g" * 8192, encoding="utf-8")
        (plain / ".gitignore").write_text("generated/\n", encoding="utf-8")
        state = indexed(plain)
        self.assertEqual(state.exclusions.gitignore_authority, "register")
        self.assertIn("keep/kept.py", paths_of(state))
        self.assertNotIn("generated/huge.py", paths_of(state))


class ThisCheckoutsCitationIndexBoundsTests(unittest.TestCase):
    """The mandated memory-quality check has to be able to run HERE, at this checkout's own tip."""

    def test_the_code_roots_candidate_population_is_inside_the_citation_caps(self) -> None:
        state = indexed(REPOSITORY_ROOT)
        paths, total = paths_of(state), bytes_of(state)
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


class BoundTreeResolutionTests(unittest.TestCase):
    """D-43: an answer must be a member of a bound tree, for both receiver forms.

    ``Trees.resolve`` is called from thirteen shipped sites in two spellings -- six ``trees.resolve(``
    and seven ``run.trees.resolve(`` -- and both forms are attribute lookups on this one method, so
    the guard below is the whole reach and not a second implementation. What it refuses is the old
    filesystem fallback: when the code tree's candidate lookup missed, ``resolve`` answered
    ``memory_root / path`` because the file existed, so a caller that bound ONE tree could receive a
    path from the other root with no tree behind it. ``system/tools.md`` is the measured case: the
    code repository is gitignored for it, so it is no member of the code tree, while the file exists
    beside a real ``system/`` directory.
    """

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory(prefix="ar-citation-bound-trees-")
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        self.code = self.tmp / "code"
        self.memory = self.tmp / "memory"
        for root in (self.code, self.memory):
            root.mkdir()
            git(root, "init", "--quiet")
            git(root, "config", "user.email", "fixture@example.invalid")
            git(root, "config", "user.name", "Fixture")
        # The code repository ignores its local ``system/tools.md``, exactly as the real one does
        # (.gitignore line 4), so the file is present on disk and absent from the code tree.
        (self.code / ".gitignore").write_text("system/tools.md\n", encoding="utf-8")
        (self.code / "src").mkdir()
        (self.code / "src" / "tracked.py").write_text("x = 1\n", encoding="utf-8")
        (self.code / "system").mkdir()
        (self.code / "system" / "tools.md").write_text("code-local, untracked, ignored\n")
        git(self.code, "add", "--all")
        git(self.code, "commit", "--quiet", "-m", "tracked")
        # The memory repository TRACKS the same relative path, which is where it belongs.
        (self.memory / "system").mkdir()
        (self.memory / "system" / "tools.md").write_text("memory-owned\n", encoding="utf-8")
        git(self.memory, "add", "--all")
        git(self.memory, "commit", "--quiet", "-m", "memory tools")
        self.code_tree = git(self.code, "rev-parse", "HEAD^{tree}")
        self.memory_tree = git(self.memory, "rev-parse", "HEAD^{tree}")

    def test_the_guard_holds_at_the_resolver_and_reaches_both_receiver_forms(self) -> None:
        """One case, because the resolver and its two receivers are one subject.

        The guard is a property of ``Trees.resolve``; the two receiver spellings are attribute
        chains onto that same method, so proving them separately would prove one thing twice -- and
        the unit lane has no budget for a second copy of it.
        """

        from agents_remember.memory_quality.style.citations import (
            claim_change_router,
            model,
            range_resolution,
        )

        # The defect, at the resolver: ``system/tools.md`` has no blob in the code tree, yet the
        # old fallback answered the memory root's file as though the code tree had carried it.
        single = Trees(
            code_root=self.code, memory_root=self.memory, candidate_tree=self.code_tree
        )
        self.assertIsNone(single.resolve("system/tools.md"))
        self.assertEqual(single.resolve("src/tracked.py"), self.code / "src" / "tracked.py")
        # The capability is not removed, it is PROVEN: a memory-rooted citation is answered by the
        # memory tree once the caller says which tree its memory root stood on.
        both = Trees(
            code_root=self.code,
            memory_root=self.memory,
            candidate_tree=self.code_tree,
            memory_candidate_tree=self.memory_tree,
        )
        self.assertEqual(both.resolve("system/tools.md"), self.memory / "system" / "tools.md")
        self.assertEqual(both.resolve("src/tracked.py"), self.code / "src" / "tracked.py")

        citation = model.Citation(text="system/tools.md:1-1", path="system/tools.md", start=1, end=1)
        claim = model.Claim(
            line=1, anchors=(), citations=(citation,), malformed=(), unchecked_spans=0
        )
        single = Trees(code_root=self.code, memory_root=self.memory, candidate_tree=self.code_tree)

        # The bare receiver: ``claim_change_router.classify_citation`` calls ``trees.resolve(...)``.
        source, error = claim_change_router.classify_citation(single, citation)
        self.assertIsNone(source)
        self.assertIsNotNone(error, "a path the bound tree cannot carry is no local citation")

        # The run receiver: ``range_resolution.claim_findings`` calls ``run.trees.resolve(...)``.
        run = range_resolution.Run(
            trees=single,
            index=cast("range_resolution.source_index.RepositoryIndex", mock.MagicMock()),
            sources=range_resolution.Sources(),
            tally=range_resolution.Tally(),
        )
        findings = range_resolution.claim_findings("onboarding/card.md", claim, run)
        self.assertEqual(run.tally.citations, 0, "nothing resolved, so nothing was cited")
        self.assertEqual(run.tally.unresolved, 1)
        self.assertIn(
            "citation_source_vanished",
            [finding.code for finding in findings],
            "a citation the bound tree cannot carry is the finding a reader must see",
        )
        # The same binding with the memory tree bound resolves it, in both forms, from the
        # memory tree's own member -- so the guard removes a wrong answer, not the capability.
        both = Trees(
            code_root=self.code,
            memory_root=self.memory,
            candidate_tree=self.code_tree,
            memory_candidate_tree=self.memory_tree,
        )
        resolved, resolved_error = claim_change_router.classify_citation(both, citation)
        self.assertIsNone(resolved_error)
        assert resolved is not None
        self.assertEqual(
            (resolved.repository, resolved.target), ("memory", self.memory / "system" / "tools.md")
        )
        bound_run = range_resolution.Run(
            trees=both,
            index=cast("range_resolution.source_index.RepositoryIndex", mock.MagicMock()),
            sources=range_resolution.Sources(),
            tally=range_resolution.Tally(),
        )
        bound_findings = range_resolution.claim_findings("onboarding/card.md", claim, bound_run)
        self.assertEqual(bound_run.tally.citations, 1)
        self.assertEqual(bound_run.tally.unresolved, 0)
        self.assertNotIn(
            "citation_source_vanished", [finding.code for finding in bound_findings]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
