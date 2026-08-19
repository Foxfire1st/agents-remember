"""Focused fail-closed coverage for terminal and atomic-series authority."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees import series_closeout
from agents_remember.worktrees.modules import terminal_validation
from integration_branch_authority_test_support import _authority_fixture


class TerminalChildCensusCoverageTests(unittest.TestCase):
    def test_series_census_refuses_wrong_shape_and_skips_owned_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            series = fixture.master_contract
            with self.assertRaisesRegex(RuntimeError, "requires a series"):
                terminal_validation.require_series_children_retired(fixture.leaf_contract)

            with self.assertRaisesRegex(RuntimeError, "worktree group"):
                terminal_validation.require_series_children_retired(
                    replace(series, worktree_group=root / "wrong")
                )

            missing_root = root / "missing-task"
            terminal_validation.require_series_children_retired(
                replace(series, task_root=missing_root)
            )

            empty_root = root / "empty-task"
            reports = empty_root / "enclosures" / "reports"
            reports.mkdir(parents=True, exist_ok=True)
            terminal_validation.require_series_children_retired(
                replace(series, task_root=empty_root)
            )

    def test_child_blockers_cover_invalid_foreign_and_live_resources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            series = fixture.master_contract
            invalid = root / "not-an-enclosure"
            invalid.write_text("not a directory\n", encoding="utf-8")
            self.assertIn(
                "invalid child enclosure",
                terminal_validation._child_terminal_blocker(series, invalid) or "",
            )

            enclosure = root / "invalid-contract"
            enclosure.mkdir()
            (enclosure / "series-contract.md").write_text("invalid\n", encoding="utf-8")
            self.assertIn(
                "invalid child contract",
                terminal_validation._child_terminal_blocker(series, enclosure) or "",
            )

            child = replace(fixture.leaf_contract, cleanup="completed")
            self.assertFalse(
                terminal_validation._child_contract_matches_series(
                    series,
                    replace(child, parent_contract_path=None),
                    child.contract_path,
                )
            )
            self.assertTrue(terminal_validation._child_memory_edge_matches_series(series, child))

            child.code_worktree.mkdir(parents=True, exist_ok=True)
            with mock.patch.object(
                terminal_validation,
                "_append_live_branch",
            ):
                self.assertIn("code worktree", terminal_validation._live_child_resources(child))

            external_fixture = _authority_fixture(root / "external", external_memory=True)
            external = replace(
                external_fixture.leaf_contract,
                memory_repo_path=None,
                memory_worktree=None,
            )
            self.assertEqual(
                terminal_validation._live_child_resources(external)[-1],
                "invalid external-memory edge",
            )

    def test_live_branch_probe_covers_present_and_git_error(self) -> None:
        resources: list[str] = []
        with mock.patch.object(
            terminal_validation,
            "run_git",
            return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
        ):
            terminal_validation._append_live_branch(
                resources,
                Path("/repo"),
                "leaf",
                "code branch",
            )
        self.assertEqual(resources, ["code branch"])

        with (
            mock.patch.object(
                terminal_validation,
                "run_git",
                return_value=SimpleNamespace(
                    returncode=2,
                    stdout="",
                    stderr="cannot read refs",
                ),
            ),
            self.assertRaisesRegex(RuntimeError, "cannot read refs"),
        ):
            terminal_validation._append_live_branch(
                [],
                Path("/repo"),
                "leaf",
                "code branch",
            )


class AtomicSeriesAuthorityCoverageTests(unittest.TestCase):
    def test_publication_refuses_wrong_kind_and_accepts_standalone_lock_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            with self.assertRaisesRegex(RuntimeError, "requires a series contract"):
                series_closeout.publish_closeout_under_authority(
                    replace(fixture.leaf_contract, kind="invalid"),
                    lambda: None,
                )
            with self.assertRaisesRegex(RuntimeError, "requires a series contract"):
                series_closeout.publish_series_integration_under_authority(
                    fixture.leaf_contract,
                    lambda: None,
                )

            master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
            topology = SimpleNamespace(
                canonical_ref=lambda *_args: master_ref,
                parent=lambda _ref: None,
            )
            with (
                mock.patch.object(series_closeout, "TaskDocumentTopology", return_value=topology),
                mock.patch.object(series_closeout, "_require_atomic_master_complete"),
                mock.patch.object(series_closeout, "_require_every_atomic_leaf_landed"),
                mock.patch.object(
                    series_closeout,
                    "integration_authority_lock",
                    return_value=nullcontext(),
                ),
            ):
                self.assertEqual(
                    series_closeout._publish_atomic_series_edge(
                        fixture.master_contract,
                        lambda: "published",
                        edge="closeout",
                    ),
                    "published",
                )

    def test_queue_publication_refuses_graph_blocker_and_candidate_races(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
            sprint_ref = TaskDocumentRef(repository="repo", path="sprint/task.json")
            topology = SimpleNamespace(
                canonical_ref=lambda *_args: master_ref,
                parent=lambda _ref: sprint_ref,
            )

            def publish(
                state: SimpleNamespace,
                *,
                graph_side_effect: list[SimpleNamespace] | None = None,
            ) -> None:
                store = SimpleNamespace(inspect=lambda _initial, action: action(state))
                graph = (
                    mock.patch.object(
                        series_closeout,
                        "_graph_context",
                        side_effect=graph_side_effect,
                    )
                    if graph_side_effect is not None
                    else mock.patch.object(
                        series_closeout,
                        "_graph_context",
                        return_value=SimpleNamespace(revision="1"),
                    )
                )
                with ExitStack() as stack:
                    stack.enter_context(
                        mock.patch.object(
                            series_closeout,
                            "TaskDocumentTopology",
                            return_value=topology,
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(series_closeout, "_require_atomic_master_complete")
                    )
                    stack.enter_context(
                        mock.patch.object(series_closeout, "_require_every_atomic_leaf_landed")
                    )
                    stack.enter_context(
                        mock.patch.object(
                            series_closeout,
                            "integration_authority_lock",
                            return_value=nullcontext(),
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            series_closeout,
                            "_initial_state",
                            return_value=object(),
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            series_closeout,
                            "CloseoutQueueStore",
                            return_value=store,
                        )
                    )
                    stack.enter_context(graph)
                    series_closeout._publish_atomic_series_edge(
                        fixture.master_contract,
                        lambda: None,
                        edge="closeout",
                    )

            state = SimpleNamespace(activeBlocker=None, candidates={})
            with self.assertRaisesRegex(series_closeout.CloseoutQueueError, "graph changed"):
                publish(
                    state,
                    graph_side_effect=[
                        SimpleNamespace(revision="1"),
                        SimpleNamespace(revision="2"),
                    ],
                )

            with self.assertRaisesRegex(series_closeout.CloseoutQueueError, "blocker"):
                publish(state)

            state = SimpleNamespace(
                activeBlocker=SimpleNamespace(master=master_ref),
                candidates={
                    "leaf": SimpleNamespace(
                        owningMaster=master_ref,
                        taskDocumentRef=TaskDocumentRef(
                            repository="repo",
                            path="master/leaf.json",
                        ),
                    )
                },
            )
            with self.assertRaisesRegex(series_closeout.CloseoutQueueError, "every own leaf"):
                publish(state)

    def test_chain_and_leaf_proofs_cover_invalid_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            series = fixture.master_contract
            leaf = fixture.leaf_contract
            leaves = {
                "one": replace(leaf, leaf_id="one"),
                "two": replace(leaf, leaf_id="two"),
            }
            refs = {
                leaf_id: TaskDocumentRef(
                    repository="repo",
                    path=f"master/{leaf_id}.json",
                )
                for leaf_id in leaves
            }
            with self.assertRaisesRegex(series_closeout.CloseoutQueueError, "one exact"):
                series_closeout._require_exact_atomic_landing_chain(
                    series,
                    leaves,
                    refs,
                    None,
                )

            with (
                mock.patch.object(series_closeout, "branch_commit", return_value="wrong"),
                self.assertRaisesRegex(series_closeout.CloseoutQueueError, "code ref"),
            ):
                series_closeout._require_atomic_chain_tips(series, "code", "")

            external_fixture = _authority_fixture(Path(tmp) / "external", external_memory=True)
            external_series = external_fixture.master_contract
            with (
                mock.patch.object(
                    series_closeout,
                    "branch_commit",
                    side_effect=["code", "wrong"],
                ),
                self.assertRaisesRegex(series_closeout.CloseoutQueueError, "memory ref"),
            ):
                series_closeout._require_atomic_chain_tips(
                    external_series,
                    "code",
                    "ledger",
                )

            facts = series_closeout._AtomicLandingFacts(
                TaskDocumentRef(repository="repo", path="master/leaf.json"),
                None,
            )
            with (
                mock.patch.object(series_closeout, "_atomic_leaf_code_matches", return_value=False),
                self.assertRaisesRegex(series_closeout.CloseoutQueueError, "not-landed"),
            ):
                series_closeout._require_atomic_leaf_landed(series, leaf, facts)

            external_leaf = external_fixture.leaf_contract
            with (
                mock.patch.object(series_closeout, "_atomic_leaf_code_matches", return_value=True),
                mock.patch.object(
                    series_closeout, "_atomic_leaf_memory_matches", return_value=False
                ),
                self.assertRaisesRegex(series_closeout.CloseoutQueueError, "memory-not-landed"),
            ):
                series_closeout._require_atomic_leaf_landed(
                    external_series,
                    external_leaf,
                    facts,
                )

            self.assertFalse(
                series_closeout._atomic_leaf_memory_matches(
                    external_series,
                    replace(external_leaf, memory_repo_path=None),
                )
            )

    def test_master_and_named_ref_closeout_refuse_invalid_facts(self) -> None:
        master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
        organizational = SimpleNamespace(
            document=SimpleNamespace(executionNature="organizational", status="Completed")
        )
        with self.assertRaisesRegex(series_closeout.CloseoutQueueError, "canonical atomic"):
            series_closeout._require_atomic_master_complete(
                cast(
                    TaskDocumentTopology,
                    SimpleNamespace(resolve=lambda _ref: organizational),
                ),
                master_ref,
            )

        incomplete = SimpleNamespace(
            document=SimpleNamespace(executionNature="atomic", status="inProgress")
        )
        with (
            mock.patch.object(series_closeout, "completion_blockers", return_value=[]),
            self.assertRaisesRegex(series_closeout.CloseoutQueueError, "completion facts"),
        ):
            series_closeout._require_atomic_master_complete(
                cast(
                    TaskDocumentTopology,
                    SimpleNamespace(resolve=lambda _ref: incomplete),
                ),
                master_ref,
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            with (
                mock.patch.object(series_closeout, "branch_worktree_owners", return_value=(root,)),
                mock.patch.object(series_closeout, "worktree_dirty", return_value=True),
                self.assertRaisesRegex(RuntimeError, "cannot create"),
            ):
                series_closeout.refuse_series_workbench_commit(fixture.master_contract)

            external_fixture = _authority_fixture(root / "external", external_memory=True)
            missing_memory = replace(external_fixture.master_contract, memory_repo_path=None)
            with self.assertRaisesRegex(RuntimeError, "requires a memory repository"):
                series_closeout.refuse_series_workbench_commit(missing_memory)
            with self.assertRaisesRegex(RuntimeError, "requires a memory repository"):
                series_closeout.exact_series_memory_closeout(missing_memory, "code")

            with (
                mock.patch.object(series_closeout, "branch_commit", return_value="ledger"),
                mock.patch.object(series_closeout, "require_git", return_value="ledger text"),
                mock.patch.object(series_closeout, "parse_ledger_text", return_value=object()),
                mock.patch.object(series_closeout, "find_mapping", return_value=None),
                self.assertRaisesRegex(RuntimeError, "map the exact series code"),
            ):
                series_closeout.exact_series_memory_closeout(
                    external_fixture.master_contract,
                    "code",
                )

            with (
                mock.patch.object(series_closeout, "branch_commit", return_value="ledger"),
                mock.patch.object(series_closeout, "require_git", return_value="ledger text"),
                mock.patch.object(series_closeout, "parse_ledger_text", return_value=object()),
                mock.patch.object(
                    series_closeout,
                    "find_mapping",
                    return_value=SimpleNamespace(memory_commit="memory"),
                ),
                mock.patch.object(series_closeout, "is_ancestor", return_value=False),
                self.assertRaisesRegex(RuntimeError, "not reachable"),
            ):
                series_closeout.exact_series_memory_closeout(
                    external_fixture.master_contract,
                    "code",
                )


if __name__ == "__main__":
    unittest.main()
