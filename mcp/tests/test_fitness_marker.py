"""The ``fitness`` marker selects exactly the inherited S1-S4 acceptance surface."""

from __future__ import annotations

import functools
import subprocess
import sys
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

FULL_MODULE_SELECTORS = (
    "mcp/tests/test_atomic_write.py",
    "mcp/tests/test_structural_limits.py",
    "mcp/tests/test_wire_contract.py",
    "mcp/tests/test_application_boundary.py",
)
CLASS_SELECTORS = (
    "mcp/tests/test_durable_store_contract.py::InProcessExclusivityTests",
    "mcp/tests/test_durable_store_contract.py::UnsafeLockFilesystemTests",
    "mcp/tests/test_durable_store_contract.py::OrchestrationNudgeRewriteTests",
    "mcp/tests/test_durable_store_contract.py::FailedRewriteTests",
    "mcp/tests/test_single_owner_primitives.py::GitSweepReachTests",
    "mcp/tests/test_single_owner_primitives.py::GitSweepFalsePositiveTests",
    "mcp/tests/test_single_owner_primitives.py::ReplaceSweepReachTests",
    "mcp/tests/test_single_owner_primitives.py::ReplaceSweepFalsePositiveTests",
    "mcp/tests/test_single_owner_primitives.py::OffenderReportTests",
)
METHOD_SELECTORS = (
    "mcp/tests/test_single_owner_primitives.py::SingleOwnerPrimitiveTests::"
    "test_only_the_kernel_git_runner_names_the_git_program",
    "mcp/tests/test_single_owner_primitives.py::SingleOwnerPrimitiveTests::"
    "test_only_the_kernel_atomic_write_owner_reaches_the_replace_syscall",
    "mcp/tests/test_single_owner_primitives.py::SingleOwnerPrimitiveTests::"
    "test_both_owners_exist_and_are_the_files_the_rules_name",
    "mcp/tests/test_single_owner_primitives.py::SingleOwnerPrimitiveTests::"
    "test_the_sweep_reaches_the_whole_package_except_runtime_assets",
)
FITNESS_SELECTORS = (*FULL_MODULE_SELECTORS, *CLASS_SELECTORS, *METHOD_SELECTORS)
SCOPED_MODULES = (
    *FULL_MODULE_SELECTORS,
    "mcp/tests/test_durable_store_contract.py",
    "mcp/tests/test_single_owner_primitives.py",
)
EXPECTED_FITNESS_COUNT = 173
EXPECTED_ORDINARY_COUNT = 190

LATER_WORK_NODES = (
    "mcp/tests/test_single_owner_primitives.py::SingleOwnerPrimitiveTests::"
    "test_task_document_writers_match_the_reviewed_authority_set",
    "mcp/tests/test_single_owner_primitives.py::SingleOwnerPrimitiveTests::"
    "test_task_document_store_owner_exists_and_is_the_file_the_rule_names",
)
LATER_WORK_CLASS = "mcp/tests/test_single_owner_primitives.py::TaskDocumentWriterCensusTests"


@functools.cache
def collect_nodes(selectors: tuple[str, ...], marker: str | None = None) -> tuple[str, ...]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    if marker is not None:
        command.extend(("-m", marker))
    command.extend(selectors)
    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"collection failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return tuple(
        line
        for line in completed.stdout.splitlines()
        if line.startswith("mcp/tests/") and "::" in line
    )


def selector_contribution(selector: str, nodes: set[str]) -> set[str]:
    return {node for node in nodes if node == selector or node.startswith(f"{selector}::")}


class FitnessMarkerContractTests(unittest.TestCase):
    def test_every_selector_contributes_and_the_marker_equals_the_manifest(self) -> None:
        manifest = set(collect_nodes(FITNESS_SELECTORS))
        selected = set(collect_nodes((), "fitness"))

        self.assertEqual(len(manifest), EXPECTED_FITNESS_COUNT)
        for selector in FITNESS_SELECTORS:
            with self.subTest(selector=selector):
                self.assertTrue(selector_contribution(selector, manifest))
        self.assertEqual(selected, manifest)

    def test_later_task_document_writer_work_does_not_leak_into_fitness(self) -> None:
        selected = set(collect_nodes((), "fitness"))

        for node in LATER_WORK_NODES:
            self.assertNotIn(node, selected)
        self.assertFalse(selector_contribution(LATER_WORK_CLASS, selected))

    def test_marked_and_unmarked_partitions_preserve_ordinary_collection(self) -> None:
        ordinary = set(collect_nodes(SCOPED_MODULES))
        selected = set(collect_nodes(SCOPED_MODULES, "fitness"))
        unselected = set(collect_nodes(SCOPED_MODULES, "not fitness"))

        self.assertEqual(len(ordinary), EXPECTED_ORDINARY_COUNT)
        self.assertTrue(selected)
        self.assertTrue(unselected)
        self.assertTrue(selected.isdisjoint(unselected))
        self.assertEqual(selected | unselected, ordinary)


if __name__ == "__main__":
    unittest.main()
