"""MIK-R202@v2 rule 14: the master chain proof admits a publication merge, and only that merge.

The admission is additive. In a ``local`` repository nothing changes: every merge the proof admits
today is still admitted and every merge it refuses is still refused with today's text. In a
``pull-request`` repository one extra merge is admitted -- the merge whose parents are the recorded
position B and the integrated landing commit H of a leaf this proof already admits on this side, and
whose tree equals H's tree. A contributor's merge, a squash, a merge with content of its own, a merge
of the wrong pair, and a publication-shaped merge in a local repository are all refused.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.worktrees import publication_admission
from agents_remember.worktrees.series_closeout import (
    _landing_workflow,
    _MergeAdmission,
    _publication_merge_admitted,
    _SpineStep,
    publication_merge_is_admitted,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

BASE = "a" * 40
LANDING = "b" * 40
OTHER = "c" * 40
LEAF_RANGES = (("260928-MIK-L99", BASE, LANDING),)


def _series_contract(memory_worktree: Path | None = None) -> WorktreeContract:
    """The real series type the closeout reader consumes, with no memory side by default."""

    root = Path("/nonexistent/l194-series")
    return WorktreeContract(
        kind="series",
        task_id="260928-MIK-L194",
        task_name="260928_maintained-invariant-knowledge",
        repo_name="agents-remember",
        workflow_kind="light-task",
        memory_mode="external",
        coordination_root=root,
        task_root=root / "tasks",
        contract_path=root / "enclosures" / "260928-mik-l194" / "series-contract.md",
        task_artifact=root / "tasks" / "260928_maintained-invariant-knowledge.md",
        worktree_group=root / "worktrees",
        code_repo_path=root / "repo",
        code_source_branch="main",
        code_work_branch="ar/194_probe",
        code_base_commit="a" * 40,
        code_worktree=root / "repo",
        memory_worktree=memory_worktree,
    )


def admitted(
    *,
    workflow: str = "pull-request",
    parents=(),
    merge_tree: str = "t1",
    second_parent_tree: str = "t1",
    authority_ranges=LEAF_RANGES,
) -> bool:
    return publication_merge_is_admitted(
        workflow=workflow,
        parents=tuple(parents),
        merge_tree=merge_tree,
        second_parent_tree=second_parent_tree,
        authority_ranges=authority_ranges,
    )


class PublicationMergeRuleTests(unittest.TestCase):
    def test_the_publication_merge_is_admitted(self) -> None:
        self.assertTrue(admitted(parents=(BASE, LANDING)))

    def test_a_local_repository_admits_nothing_new(self) -> None:
        self.assertFalse(admitted(workflow="local", parents=(BASE, LANDING)))

    def test_parents_that_are_not_the_recorded_pair_are_refused(self) -> None:
        self.assertFalse(admitted(parents=(OTHER, LANDING)))
        self.assertFalse(admitted(parents=(BASE, OTHER)))
        self.assertFalse(admitted(parents=(LANDING, BASE)))  # reversed: first parent must be B

    def test_a_contributor_merge_is_refused(self) -> None:
        self.assertFalse(admitted(parents=("d" * 40, "e" * 40)))

    def test_a_merge_with_its_own_content_is_refused(self) -> None:
        self.assertFalse(
            admitted(parents=(BASE, LANDING), merge_tree="t2", second_parent_tree="t1")
        )

    def test_an_octopus_merge_is_refused(self) -> None:
        self.assertFalse(admitted(parents=(BASE, LANDING, OTHER)))

    def test_two_identical_parents_are_not_the_pair(self) -> None:
        self.assertFalse(admitted(parents=(BASE, BASE)))

    def test_a_leaf_this_proof_does_not_admit_supplies_no_pair(self) -> None:
        self.assertFalse(admitted(parents=(BASE, LANDING), authority_ranges=()))


class LandingWorkflowReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.memory = Path(self._tmp.name) / "memory"
        (self.memory / "system").mkdir(parents=True)

    def test_a_series_without_a_memory_worktree_lands_locally(self) -> None:
        self.assertEqual(_landing_workflow(_series_contract()), "local")

    def test_the_setting_is_read_from_the_memory_worktree(self) -> None:
        (self.memory / "system" / "settings.json").write_text(
            '{"version": 2, "landing": {"workflow": "pull-request"}}', encoding="utf-8"
        )
        series = _series_contract(self.memory)
        self.assertEqual(_landing_workflow(series), "pull-request")

    def test_an_absent_settings_file_lands_locally(self) -> None:
        series = _series_contract(self.memory)
        self.assertEqual(_landing_workflow(series), "local")


@unittest.skipUnless(
    subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0,
    "git is required to prove the merge reads",
)
class PublicationMergeOnRealObjectsTests(unittest.TestCase):
    """The wrapper reads parents and trees from git, so one case is proved on real objects."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()

        def git(*args: str) -> str:
            result = subprocess.run(
                ["git", *args], cwd=self.repo, capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()

        self.git = git
        git("init", "-q", "-b", "master")
        git("config", "user.email", "worker@example.invalid")
        git("config", "user.name", "Worker")
        (self.repo / "file.txt").write_text("base\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-q", "-m", "base")
        self.base = git("rev-parse", "HEAD")

    def test_a_real_publication_merge_is_admitted_in_both_modes(self) -> None:
        self.git("checkout", "-q", "-b", "land", self.base)
        (self.repo / "file.txt").write_text("landing\n", encoding="utf-8")
        self.git("commit", "-q", "-am", "landing")
        landing = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "master")
        self.git("merge", "-q", "--no-ff", "land", "-m", "Merge landing")
        merge = self.git("rev-parse", "HEAD")
        self.assertEqual(
            tuple(self.git("rev-list", "--parents", "-n", "1", merge).split()[1:]),
            (self.base, landing),
        )
        admission = _MergeAdmission(
            heads=(),
            leaf_ranges=(("260928-MIK-L99", self.base, landing),),
            authority_ranges=(("260928-MIK-L99", self.base, landing),),
            positions=(),
            legacy=False,
            workflow="pull-request",
        )
        step = _SpineStep(side="code", step="closeout", task_id="260928-MIK-L194")
        self.assertTrue(_publication_merge_admitted(self.repo, merge, admission, step))
        local = _MergeAdmission(
            heads=(),
            leaf_ranges=admission.leaf_ranges,
            authority_ranges=admission.authority_ranges,
            positions=(),
            legacy=False,
            workflow="local",
        )
        self.assertFalse(_publication_merge_admitted(self.repo, merge, local, step))


class TodayRefusalTextTests(unittest.TestCase):
    def test_the_refusal_text_is_unchanged(self) -> None:
        source = (MCP_SRC / "agents_remember" / "worktrees" / "series_closeout.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("A master line holds only leaf ", source)
        self.assertIn(
            "landings and its parent line; take the commit off the master branch and make the ",
            source,
        )
        self.assertIn("change in a leaf", source)


if __name__ == "__main__":
    unittest.main()


class LeafAuthorityBindingTests(unittest.TestCase):
    """L194-V2-001: the expected pair is the authority's, never the leaf contract's editable base."""

    AUTHORITY_B = "b" * 40
    AUTHORITY_H = "c" * 40
    CONTRACT_A = "a" * 40

    def leaf(self, base: str) -> SimpleNamespace:
        return SimpleNamespace(
            leaf_id="260928-MIK-L99", code_base_commit=base, memory_base_commit=""
        )

    def reading(self, record: object):
        store = SimpleNamespace(read=lambda: record)
        return mock.patch.object(
            publication_admission,
            "located_lifecycle_operation_store",
            lambda *args, **kwargs: store,
        )

    def authority(self, source: str, candidate: str) -> SimpleNamespace:
        return SimpleNamespace(
            codeSourceCommit=source,
            codeCandidateCommit=candidate,
            memorySourceCommit="",
            memoryContentCommit="",
        )

    def test_the_pair_is_the_authority_pair_not_the_contract_base(self) -> None:
        with self.reading(
            SimpleNamespace(integrationAuthority=self.authority(self.AUTHORITY_B, self.AUTHORITY_H))
        ):
            self.assertEqual(
                publication_admission.leaf_authority_range(self.leaf(self.CONTRACT_A), "code"),
                ("260928-MIK-L99", self.AUTHORITY_B, self.AUTHORITY_H),
            )

    def test_moving_the_contract_base_does_not_move_the_admitted_pair(self) -> None:
        with self.reading(
            SimpleNamespace(integrationAuthority=self.authority(self.AUTHORITY_B, self.AUTHORITY_H))
        ):
            first = publication_admission.publication_ranges([self.leaf("a" * 40)], "code")
            second = publication_admission.publication_ranges([self.leaf("d" * 40)], "code")
        self.assertEqual(first, second)
        self.assertEqual(first, (("260928-MIK-L99", self.AUTHORITY_B, self.AUTHORITY_H),))
        self.assertNotIn(self.CONTRACT_A, first[0])
        self.assertNotIn("d" * 40, second[0])

    def test_a_side_whose_candidate_equals_its_source_grants_nothing(self) -> None:
        with self.reading(
            SimpleNamespace(integrationAuthority=self.authority(self.AUTHORITY_B, self.AUTHORITY_B))
        ):
            self.assertIsNone(
                publication_admission.leaf_authority_range(self.leaf(self.CONTRACT_A), "code")
            )
            self.assertEqual(
                publication_admission.publication_ranges([self.leaf(self.CONTRACT_A)], "code"), ()
            )

    def test_a_side_the_authority_never_recorded_grants_nothing(self) -> None:
        with self.reading(
            SimpleNamespace(integrationAuthority=self.authority(self.AUTHORITY_B, self.AUTHORITY_H))
        ):
            self.assertIsNone(
                publication_admission.leaf_authority_range(self.leaf(self.CONTRACT_A), "memory")
            )

    def test_an_unreadable_authority_grants_nothing_and_does_not_raise(self) -> None:
        def explode(*args: object, **kwargs: object) -> object:
            raise RuntimeError("store refused")

        with (
            mock.patch.object(publication_admission, "located_lifecycle_operation_store", explode),
            mock.patch.object(
                publication_admission, "_archived_authority_range", lambda *a, **k: None
            ),
        ):
            self.assertIsNone(
                publication_admission.leaf_authority_range(self.leaf(self.CONTRACT_A), "code")
            )
            self.assertEqual(
                publication_admission.publication_ranges([self.leaf(self.CONTRACT_A)], "code"),
                (),
            )
