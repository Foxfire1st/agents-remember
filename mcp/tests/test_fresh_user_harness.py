"""The fresh-user acceptance harness's own contract: fixtures, entry point, and blocked steps.

`scripts/e2e_harness/**` is a permanent evidence-support root, so every non-`test_` module under it
is a **governed evidence artifact**: the lifecycle catalog must carry a row for it, that row needs a
real consumer and an executable replacement node, and the artifact must not sit in the tree with no
one to answer for it. This module is that consumer. It is deliberately small and it does not re-run
the acceptance scenario -- the scenario's own transcript is the acceptance evidence; what is pinned
here is the *shape the scenario needs*, so a later edit to the fixtures cannot quietly stop
producing it.

WHAT DEFENDS WHAT
-----------------
    reading                                              defended by
    --------------------------------------------------   -------------------------------------
    the over-cap fixture really crosses the cap            the oversize case: the file's stat size
                                                           is compared against the shipped
                                                           `MAX_SOURCE_FILE_BYTES`, and the
                                                           vendored tree and the distinct
                                                           spear/sprint branches are asserted
    the plain fixture stays inside every cap               the plain case: no file over the
                                                           per-file cap and the population inside
                                                           the aggregate cap
    a step that cannot run is never `completed`            the blocked-step case: the free-agent
                                                           seat step must be `blocked`, must name
                                                           its owner, and must not claim a result
    the entry point is the one the report publishes        the argument case: `--reports` is
                                                           required and `--work-root` is optional

WHAT THIS DOES NOT COVER (stated, not implied)
----------------------------------------------
- **Not the acceptance itself.** The transcript is written by running
  `scripts/e2e_harness/run_fresh_user.py`; this module never runs it, so it cannot and does not
  claim the chain works.
- **Not the fixtures' Git behaviour.** Branch topology and `origin/HEAD` are asserted as facts about
  the created repository, not as a claim about the product's integration-branch authority.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HARNESS_ROOT = REPOSITORY_ROOT / "scripts" / "e2e_harness"
# Strings, never Path objects: a non-``str`` entry in ``sys.path`` is silently ignored by the
# import machinery, which then finds nothing here and reports a missing module.
if HARNESS_ROOT.as_posix() not in sys.path:
    sys.path.insert(0, HARNESS_ROOT.as_posix())
MCP_SRC = REPOSITORY_ROOT / "mcp" / "src"
if MCP_SRC.as_posix() not in sys.path:
    sys.path.insert(0, MCP_SRC.as_posix())

from agents_remember.memory_quality.style.citations.source_index_state import (
    MAX_SOURCE_BYTES,
    MAX_SOURCE_FILE_BYTES,
)


def _load(module_name: str) -> ModuleType:
    """Load one flat harness module by path.

    The harness's modules import each other by bare name, so they are loaded and registered here
    rather than imported statically -- which is also what keeps this module's own contract honest:
    the three governed artifacts are declared by their exact paths above, and the loader below
    fails loudly if one is missing.
    """
    path = HARNESS_ROOT / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - a missing file is a hard stop
        raise AssertionError(f"cannot load harness module {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


fresh_user_fixture = _load("fresh_user_fixture")
fresh_user_scenario = _load("fresh_user_scenario")
run_fresh_user = _load("run_fresh_user")


GOVERNED_HARNESS_MODULES = (
    "scripts/e2e_harness/fresh_user_fixture.py",
    "scripts/e2e_harness/fresh_user_scenario.py",
    "scripts/e2e_harness/run_fresh_user.py",
)
"""The governed artifacts this module consumes, as exact paths.

`scripts/e2e_harness/**` is a permanent evidence-support root, so each of these is a governed
evidence artifact with a lifecycle row; a row needs a consumer, and this tuple is the exact-path
declaration that makes this module one. The literal spelling is load-bearing -- an import alone
cannot be resolved for a flat harness module that is not on a package path -- so it is written out
rather than derived, and the case below asserts every path exists.
"""


def sizes(root: Path) -> dict[str, int]:
    return {
        path.relative_to(root).as_posix(): path.stat().st_size
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.parts
    }


class FreshUserFixtureShapeTests(unittest.TestCase):
    """The two fixtures carry the shapes the packet names, measured rather than assumed."""

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory(prefix="ar-fresh-user-harness-")
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)

    def test_the_over_cap_fixture_really_crosses_the_cap(self) -> None:
        fixture = fresh_user_fixture.create_fresh_user_fixture(
            self.root / "spear-not-main",
            name="spear-not-main",
            spear_branch="dev",
            over_cap_source=True,
            vendored_tree=True,
        )
        measured = sizes(fixture.code_repo)
        self.assertGreater(
            measured["src/generated-blob.bin"],
            MAX_SOURCE_FILE_BYTES,
            "the fixture's whole point is a source file over the shipped per-file cap",
        )
        self.assertEqual(measured["src/generated-blob.bin"], fresh_user_fixture.OVERSIZED_BYTES)
        self.assertIn("vendor/lib.py", measured)
        self.assertIn(".gitignore", measured)
        # The spear is not the repository default, and the sprint's workbench is a third branch:
        # both facts are what the integration-branch authority forced on this fixture.
        self.assertEqual(fixture.spear_branch, "dev")
        self.assertEqual(fixture.sprint_branch, fresh_user_fixture.SPRINT_BRANCH)
        self.assertNotEqual(fixture.sprint_branch, fixture.spear_branch)
        heads = fresh_user_fixture.git(
            fixture.code_repo, "branch", "--list", "--format=%(refname:short)"
        )
        self.assertIn("main", heads.split())
        self.assertIn("dev", heads.split())
        self.assertIn(fresh_user_fixture.SPRINT_BRANCH, heads.split())
        self.assertEqual(
            fresh_user_fixture.git(fixture.code_repo, "symbolic-ref", "refs/remotes/origin/HEAD"),
            "refs/remotes/origin/main",
        )

    def test_the_plain_fixture_stays_inside_every_cap(self) -> None:
        fixture = fresh_user_fixture.create_fresh_user_fixture(
            self.root / "plain-main",
            name="plain-main",
            spear_branch="main",
            over_cap_source=False,
            vendored_tree=False,
        )
        measured = sizes(fixture.code_repo)
        self.assertNotIn("vendor/lib.py", measured)
        total = sum(measured.values())
        self.assertLessEqual(
            max(measured.values(), default=0),
            MAX_SOURCE_FILE_BYTES,
            "the default-path fixture must stay inside the per-file cap",
        )
        self.assertLess(total, MAX_SOURCE_BYTES)
        self.assertEqual(fixture.spear_branch, "main")

    def test_the_fixture_owns_everything_under_its_own_run_root(self) -> None:
        """A clean-room fixture reads no machine-local state, and this is where that is checked."""

        fixture = fresh_user_fixture.create_fresh_user_fixture(
            self.root / "plain-main",
            name="plain-main",
            spear_branch="main",
            over_cap_source=False,
            vendored_tree=False,
        )
        root = (self.root / "plain-main").resolve()
        for path in (fixture.code_repo, fixture.memory_root, fixture.coordination_root):
            self.assertTrue(
                path.resolve().is_relative_to(root),
                f"{path} escaped the run root {root}",
            )
        self.assertTrue(fixture.authority_path.resolve().is_relative_to(root))


class FreshUserScenarioContractTests(unittest.TestCase):
    """The scenario's own contract: what it may report, and what it must never report."""

    def test_a_step_that_cannot_run_is_blocked_by_name_and_not_completed(self) -> None:
        """The rule that outlives any one step: a step which cannot run is never `completed`.

        The free agent's acceptance now DOES run (L14R-2 was closed by L15's launch wiring), so this
        case pins the contract on the step that genuinely cannot run at this point in the flow --
        the citation fix without a leaf contract -- rather than on a step that has since become
        runnable. A step's status is a claim about what happened, so the blocked shape must carry
        its reason and its owner.
        """

        holder = tempfile.TemporaryDirectory(prefix="ar-fresh-user-blocked-")
        self.addCleanup(holder.cleanup)
        fixture = fresh_user_fixture.create_fresh_user_fixture(
            Path(holder.name) / "plain-main",
            name="plain-main",
            spear_branch="main",
            over_cap_source=False,
            vendored_tree=False,
        )
        record = fresh_user_scenario.citation_fix_blocked(fixture, {"state": "started"})
        self.assertEqual(record.status, "blocked")
        self.assertTrue(record.blocked_reason)
        self.assertIn("owner", record.result)
        self.assertFalse(record.result["reachable"])
        self.assertNotEqual(record.status, "completed")

    def test_every_governed_harness_module_this_suite_answers_for_exists(self) -> None:
        """A registered artifact that has been moved or deleted reddens here, not in the catalog."""

        for relative in GOVERNED_HARNESS_MODULES:
            self.assertTrue((REPOSITORY_ROOT / relative).is_file(), relative)

    def test_the_entry_point_requires_reports_and_defaults_the_run_root(self) -> None:
        parsed = run_fresh_user.arguments(["--reports", "/tmp/fresh-user-reports"])
        self.assertEqual(parsed.reports, Path("/tmp/fresh-user-reports"))
        self.assertIsNone(parsed.work_root)
        with self.assertRaises(SystemExit):
            run_fresh_user.arguments([])
        parser = argparse.ArgumentParser()
        self.assertIsNotNone(parser)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
