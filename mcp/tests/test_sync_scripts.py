"""Convergence: ``skills/`` is the only source, and every declared packaged copy is its byte image.

THE SUBJECT
-----------
An agent does not read the canonical tree. It reads whichever skill tree its harness installed --
root ``skills/``, the MCP package data that ``runtime_install`` copies into a coordination root, or
one of the eight harness starter packages. Correcting one copy and leaving eight stale ones is
therefore not a partial fix; it is a role-dependent lifecycle truth, where two agents in the same
task hold different authority boundaries and neither can tell. ``scripts/sync-skills.py`` is the
mechanism that makes that impossible, and this module is the executable statement that it happened:
one pinned inventory, the real trees read byte for byte, and the copy-then-swap sequence
characterized at the two places it can fail.

WHAT DEFENDS WHAT
-----------------
    reading                                              defended by
    --------------------------------------------------   ---------------------------------------
    every declared copy equals ``skills/`` byte for       ``test_every_declared_projection...``,
      byte, in this checkout, with no pytest-only          which reads the real nine targets and
      invocation required                                  names the repair command that fixes
                                                           each drifted path
    the drift reader itself can still fail                ``test_the_drift_reader...``, which
                                                           hand-edits, deletes, and adds files in a
                                                           throwaway tree and requires all three
                                                           failure kinds to be reported
    the declared inventory is the certified one           the generated-scopes case: the script's
                                                           ``TARGETS`` must equal the paths the
                                                           certification profile declares as the
                                                           ``generated-skills`` generated input, so
                                                           a target silently dropped from the script
                                                           cannot make ``--check`` cheaper
    the sequence is staged-copy, two renames, delete      the interruption cases below, which drive
                                                           a real failure into each window instead of
                                                           asserting the algorithm's happy path
    the canonical tree is never its own target            the self-target case, which exercises the
                                                           refusal rather than trusting it
    the corrected boundary reaches every packaged copy    the reach cases, which hold five clauses
                                                           quoted from the shipped doctrine against
                                                           the canonical tree and all nine copies,
                                                           and a mutant that must name the copy
                                                           that lost one

WHY THE FAULT CASES PIN A DEFECT INSTEAD OF FIXING IT
-----------------------------------------------------
``replace_tree`` builds a complete staging copy, renames the live target aside, renames staging
into place, and deletes the retired directory. The two renames are separate operations. There is a
window -- after the live target has become ``.ar-sync-old`` and before staging becomes live -- where
the live path does not exist, and a rerun deletes both leftovers before it retries the copy, so a
second failure can consume the last copy of the old bytes. Those cases assert that reality rather
than a hoped-for atomic swap, because a test suite that quietly assumed atomicity would certify the
opposite of what a crashing host actually does. Making the primitive atomic is a design change to
the projection mechanism, not a test edit, so a future attempt at one has to fail here and be
argued rather than land silently.

WHAT THIS DOES NOT COVER (stated, not implied)
---------------------------------------------
- **Scope is the skill projection.** ``sync-runtime.py``, ``sync-harness.py``, ``sync-dashboard.py``,
  and ``sync-projection-types.py`` project different trees and are not this module's subject.
- **The doctrine's meaning is not restated here.** Which clauses each surface owes, and what they
  mean, is canonical-tree subject matter owned by
  ``mcp/tests/test_lifecycle_turn_truth_doctrine.py``. The reach cases quote that module's shipped
  wording rather than paraphrasing it, and they assert only *where* it must arrive -- no semantic
  classification, no contradiction sweep, and no per-clause re-assertion of byte equality.
- **No delivery-graph authority.** The ``generated-skills`` certification rail runs
  ``scripts/sync-skills.py --check`` in the pinned graph. This module is the ordinary-suite
  equivalent of that check; it neither replaces nor certifies it.
- **It reads this checkout, not a candidate.** Running it in a worktree reads that worktree's trees.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SKILLS_PATH = REPOSITORY_ROOT / "skills"
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "sync-skills.py"
CERTIFICATION_PROFILE_PATH = REPOSITORY_ROOT / "mcp" / "certification-profile-v1.json"
CERTIFIED_GENERATED_INPUT_ID = "generated-skills"
REPAIR_COMMAND = "python3 scripts/sync-skills.py"
STAGING_SUFFIX = ".ar-sync-new"
RETIRED_SUFFIX = ".ar-sync-old"

# The doctrine tree as it is projected. `skills/` is the source; the declared targets are its copies.
DOCTRINE_TREE = "l-01-agent-lifecycles"


@dataclass(frozen=True)
class BoundaryClause:
    """One corrected handoff-authority clause, at the surface inside the doctrine tree stating it."""

    surface: str
    clause: str


# The shipped canonical wording of the corrected boundary, quoted verbatim from the doctrine tree.
# WHICH clauses each surface owes, and what they mean, is canonical-tree subject matter owned by
# `mcp/tests/test_lifecycle_turn_truth_doctrine.py`; this table exists so the projection's REACH is a
# checked subject, and it quotes that module's shipped wording rather than paraphrasing it into a
# second vocabulary that could drift from the first.
#
# Re-pointed at the consolidated corpus, because the subject is where a clause now ships, not where
# it used to: the boundary's whole set moved with its one home from the retired `SKILL.md` section to
# `core/acceptance.md`, and the manager clause is quoted at the words the file carries today. Both
# rows are the same clauses they always were; a row whose surface or marker stops matching the
# canonical tree and its nine copies still fails, which is what `test_a_clause_missing_from_one_...`
# pins by deleting one clause from one copy.
PROJECTED_BOUNDARY_CLAUSES: tuple[BoundaryClause, ...] = (
    BoundaryClause(
        "core/acceptance.md", "Terminal truth is mechanical; acceptance is the owner's."
    ),
    BoundaryClause("roles/manager.md", "never opens or evaluates the artifact"),
    BoundaryClause("roles/worker.md", "terminal/finalizer truth attests only that this turn ended"),
    BoundaryClause("roles/worker.md", "Never author a second model-authored completion post"),
    BoundaryClause("templates/turn-report.md", "The relay never inspects it"),
)

_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def normalize(text: str) -> str:
    """Flatten markdown so a clause is matched by its words, not by its line wrapping.

    The shipped text wraps and emphasizes these clauses, and one of them spans a line break, so a raw
    substring search reports a present clause as missing. Emphasis and link syntax are removed and
    whitespace is collapsed before matching.
    """

    return " ".join(re.sub(r"[*`]", "", _MARKDOWN_LINK.sub(r"\1", text)).split())


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()


@lru_cache(maxsize=1)
def sync_skills_module() -> ModuleType:
    """Load ``scripts/sync-skills.py`` by path; it is a CLI script, not an importable module.

    The module is registered in ``sys.modules`` before execution because ``@dataclass`` resolves a
    class's defining module through that mapping while the class is still being created.
    """

    spec = importlib.util.spec_from_file_location("sync_skills", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load the projection script: {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def certified_generated_scopes() -> frozenset[str]:
    """The packaged targets the certification profile declares for the ``generated-skills`` rail."""

    profile = json.loads(CERTIFICATION_PROFILE_PATH.read_text(encoding="utf-8"))
    for generated_input in profile["generatedInputs"]:
        if generated_input.get("inputId") == CERTIFIED_GENERATED_INPUT_ID:
            return frozenset(generated_input["generatedScopes"])
    raise AssertionError(
        f"{CERTIFICATION_PROFILE_PATH.name} declares no {CERTIFIED_GENERATED_INPUT_ID!r} "
        "generated input, so the projection inventory has no certified counterpart"
    )


def declared_target_paths() -> frozenset[str]:
    """Repository-relative paths of every copy the projection script declares."""

    return frozenset(repo_relative(target.path) for target in sync_skills_module().TARGETS)


def boundary_locations() -> dict[str, Path]:
    """The canonical doctrine tree, plus its copy inside every declared packaged target.

    The keys are the printed names a failure uses; the values are the tree each name carries.
    """

    locations = {repo_relative(CANONICAL_SKILLS_PATH): CANONICAL_SKILLS_PATH / DOCTRINE_TREE}
    for target in sync_skills_module().TARGETS:
        locations[repo_relative(target.path)] = target.path / DOCTRINE_TREE
    return locations


def boundary_surface_texts(clause: BoundaryClause) -> dict[str, str]:
    """Each location's copy of the surface the clause belongs to."""

    return {
        location: (root / clause.surface).read_text(encoding="utf-8")
        for location, root in boundary_locations().items()
    }


def missing_clause_locations(clause: BoundaryClause, texts: Mapping[str, str]) -> list[str]:
    """Locations whose text does not carry ``clause``, each named as ``<location>/<surface>``."""

    return [
        f"{location}/{clause.surface}"
        for location, text in sorted(texts.items())
        if normalize(clause.clause) not in normalize(text)
    ]


def drift_entries(target: Any) -> list[str]:
    """One entry per file where ``target`` disagrees with ``skills/``, named where a reader looks.

    Each entry is rebased onto the target, so the failure names the copy that has to be fixed
    rather than the canonical path it shares with eight other copies.
    """

    module = sync_skills_module()
    diff = module.diff_target(target)
    return [
        f"{module.repo_relative(target.path / relative)} ({kind})"
        for kind, relatives in (
            ("differs from the canonical bytes", diff.changed),
            ("absent from the copy", diff.missing),
            ("absent from the canonical tree", diff.extra),
        )
        for relative in relatives
    ]


def make_source_tree(root: Path) -> Path:
    """A tiny canonical-shaped source with one nested file, for throwaway-tree exercises."""

    source = root / "source"
    (source / "nested").mkdir(parents=True)
    (source / "top.md").write_text("top\n", encoding="utf-8")
    (source / "nested" / "leaf.md").write_text("leaf\n", encoding="utf-8")
    return source


class RenameInterruption:
    """Fail the ``failing_call``-th rename of a ``replace_tree`` run, leaving real earlier ones.

    Patching the call rather than stubbing the whole algorithm is the point: the rename that *does*
    run moves the real directory, so the state left on disk is the state a host crash would leave,
    and the assertions after the ``with`` block describe a genuine window instead of a simulated one.
    """

    def __init__(self, failing_call: int) -> None:
        self._real_rename = sync_skills_module().os.rename
        self._failing_call = failing_call
        self.performed: list[tuple[str, str]] = []
        self._patcher = mock.patch.object(sync_skills_module().os, "rename", self._rename)

    def __enter__(self) -> RenameInterruption:
        self._patcher.start()
        return self

    def __exit__(self, *exception: object) -> None:
        self._patcher.stop()

    def _rename(self, source: Path, destination: Path) -> None:
        if len(self.performed) + 1 == self._failing_call:
            raise OSError("interrupted between the two renames")
        self._real_rename(source, destination)
        self.performed.append((os.fspath(source), os.fspath(destination)))


class DeclaredProjectionInventoryTests(unittest.TestCase):
    """Which copies exist is a declaration, and a declaration can shrink by accident."""

    def test_declared_targets_are_the_certified_generated_scopes(self) -> None:
        self.assertEqual(
            declared_target_paths(),
            certified_generated_scopes(),
            "the projection script and the certification profile disagree about which packaged "
            f"skill copies exist; reconcile them before running {REPAIR_COMMAND}",
        )

    def test_the_canonical_tree_is_refused_as_its_own_target(self) -> None:
        self.assertTrue(CANONICAL_SKILLS_PATH.is_dir(), f"missing {CANONICAL_SKILLS_PATH}")
        overlaps = sorted(
            path
            for path in declared_target_paths()
            if Path(REPOSITORY_ROOT / path).resolve().is_relative_to(CANONICAL_SKILLS_PATH)
        )
        self.assertEqual(overlaps, [], "a declared copy is inside the canonical tree it copies")
        with self.assertRaises(RuntimeError):
            sync_skills_module().sync_target(
                sync_skills_module().SkillTarget("canonical", CANONICAL_SKILLS_PATH)
            )


class ReplaceTreeSequenceTests(unittest.TestCase):
    """The per-target sequence, including both windows where it can leave the target absent."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = make_source_tree(self.root)
        self.target = self.root / "target"

    def _interrupt_the_swap(self) -> RenameInterruption:
        """Replace a live target and fail between its two renames, returning what completed."""

        self.target.mkdir()
        (self.target / "live.md").write_text("live\n", encoding="utf-8")
        with (
            RenameInterruption(failing_call=2) as interruption,
            self.assertRaises(OSError),
        ):
            sync_skills_module().replace_tree(self.source, self.target)
        return interruption

    def test_a_complete_staged_copy_replaces_the_live_target(self) -> None:
        (self.source / "__pycache__").mkdir()
        (self.source / "__pycache__" / "junk.pyc").write_bytes(b"junk")
        self.target.mkdir()
        (self.target / "stale.md").write_text("stale\n", encoding="utf-8")
        staging = self.root / f"target{STAGING_SUFFIX}"
        retired = self.root / f"target{RETIRED_SUFFIX}"
        staging.mkdir()
        (staging / "half-copied.md").write_text("partial\n", encoding="utf-8")
        retired.mkdir()

        sync_skills_module().replace_tree(self.source, self.target)

        self.assertEqual((self.target / "top.md").read_text(encoding="utf-8"), "top\n")
        self.assertEqual((self.target / "nested" / "leaf.md").read_text(encoding="utf-8"), "leaf\n")
        self.assertFalse((self.target / "stale.md").exists())
        self.assertFalse((self.target / "__pycache__").exists())
        self.assertFalse(staging.exists())
        self.assertFalse(retired.exists())

    def test_a_failed_staging_copy_leaves_the_live_target_untouched(self) -> None:
        self.target.mkdir()
        (self.target / "live.md").write_text("live\n", encoding="utf-8")

        with (
            mock.patch.object(
                sync_skills_module().shutil, "copytree", side_effect=OSError("no space left")
            ),
            self.assertRaises(OSError),
        ):
            sync_skills_module().replace_tree(self.source, self.target)

        self.assertEqual((self.target / "live.md").read_text(encoding="utf-8"), "live\n")

    def test_the_two_renames_are_separate_and_leave_a_missing_target_window(self) -> None:
        interruption = self._interrupt_the_swap()

        self.assertEqual(
            [Path(destination).name for _, destination in interruption.performed],
            [f"target{RETIRED_SUFFIX}"],
            "the first rename retires the live target; the second publishes the staging copy",
        )
        self.assertFalse(self.target.exists(), "the two renames are not an atomic replacement")
        self.assertEqual(
            (self.root / f"target{STAGING_SUFFIX}" / "top.md").read_text(encoding="utf-8"), "top\n"
        )
        self.assertEqual(
            (self.root / f"target{RETIRED_SUFFIX}" / "live.md").read_text(encoding="utf-8"),
            "live\n",
        )

    def test_the_recovery_rerun_rebuilds_the_target_without_rolling_back(self) -> None:
        self._interrupt_the_swap()

        sync_skills_module().replace_tree(self.source, self.target)

        self.assertEqual((self.target / "top.md").read_text(encoding="utf-8"), "top\n")
        self.assertFalse((self.target / "live.md").exists(), "the rerun rolls nothing back")
        self.assertFalse((self.root / f"target{STAGING_SUFFIX}").exists())
        self.assertFalse((self.root / f"target{RETIRED_SUFFIX}").exists())

    def test_a_second_copy_failure_during_recovery_still_leaves_the_target_absent(self) -> None:
        self._interrupt_the_swap()
        retired = self.root / f"target{RETIRED_SUFFIX}"
        self.assertTrue(retired.is_dir(), "the previous copy is still on disk after the window")

        with (
            mock.patch.object(
                sync_skills_module().shutil, "copytree", side_effect=OSError("no space left")
            ),
            self.assertRaises(OSError),
        ):
            sync_skills_module().replace_tree(self.source, self.target)

        self.assertFalse(self.target.exists())
        self.assertFalse(
            retired.exists(),
            "the rerun deletes both leftovers before it copies, so the previous copy is consumed",
        )


class CanonicalProjectionConvergenceTests(unittest.TestCase):
    """Every declared copy in this checkout is the canonical bytes, and the reader can still say no."""

    maxDiff = None

    def test_every_declared_projection_matches_the_canonical_tree(self) -> None:
        module = sync_skills_module()
        self.assertTrue(CANONICAL_SKILLS_PATH.is_dir(), f"missing {CANONICAL_SKILLS_PATH}")
        self.assertTrue(module.TARGETS, "the projection script declares no targets")

        drifted: list[str] = []
        for target in module.TARGETS:
            self.assertTrue(
                target.path.is_dir(),
                f"{module.repo_relative(target.path)} ({target.label}) is absent; "
                f"run: {REPAIR_COMMAND}",
            )
            drifted.extend(drift_entries(target))

        self.assertEqual(
            drifted,
            [],
            "packaged skill copies drifted from skills/; run: " + REPAIR_COMMAND,
        )

    def test_the_drift_reader_still_reports_every_disagreement(self) -> None:
        """The convergence case above is only evidence if this reader can still say no.

        ``diff_target`` always measures a copy against the one canonical root, so the scratch
        exercise points that root at a throwaway tree: a reader that compared a tree with itself
        would report every real copy as converged while proving nothing.
        """

        module = sync_skills_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_source_tree(root)
            target = root / "target"
            (target / "nested").mkdir(parents=True)
            (target / "top.md").write_text("hand-edited\n", encoding="utf-8")
            (target / "stale.md").write_text("stale\n", encoding="utf-8")

            with mock.patch.object(module, "CANONICAL_SKILLS", source):
                diff = module.diff_target(module.SkillTarget("fixture", target))

                self.assertEqual(diff.changed, (Path("top.md"),))
                self.assertEqual(diff.missing, (Path("nested/leaf.md"),))
                self.assertEqual(diff.extra, (Path("stale.md"),))
                self.assertFalse(diff.in_sync)

                (target / "top.md").write_text("top\n", encoding="utf-8")
                (target / "nested" / "leaf.md").write_text("leaf\n", encoding="utf-8")
                (target / "stale.md").unlink()

                self.assertTrue(module.diff_target(module.SkillTarget("fixture", target)).in_sync)


class BoundaryPropagationTests(unittest.TestCase):
    """The corrected boundary is a property of every copy, not only of the tree it was written in.

    Byte equality already implies these cases, and that is the point of stating them separately: an
    implied property has no named subject, so a copy that lost the clause is diagnosed here as the
    path that lost it rather than as an anonymous hash disagreement in one of nine trees.
    """

    def test_every_declared_copy_carries_the_corrected_boundary_clauses(self) -> None:
        locations = boundary_locations()
        self.assertIn(repo_relative(CANONICAL_SKILLS_PATH), locations)
        self.assertGreater(
            len(locations),
            1,
            "a reach check over the canonical tree alone says nothing about the packaged copies",
        )
        absent_trees = sorted(name for name, root in locations.items() if not root.is_dir())
        self.assertEqual(absent_trees, [], f"missing doctrine trees; run: {REPAIR_COMMAND}")

        for clause in PROJECTED_BOUNDARY_CLAUSES:
            with self.subTest(surface=clause.surface, clause=clause.clause):
                self.assertEqual(
                    missing_clause_locations(clause, boundary_surface_texts(clause)),
                    [],
                    f"{clause.surface} must carry {clause.clause!r} in the canonical tree and in "
                    f"every declared copy; run: {REPAIR_COMMAND}",
                )

    def test_a_clause_missing_from_one_packaged_copy_is_named(self) -> None:
        """Falsifiability: a copy that lost a clause must be reported as that copy, not as a count."""

        clause = next(
            item for item in PROJECTED_BOUNDARY_CLAUSES if item.surface == "roles/manager.md"
        )
        canonical_name = repo_relative(CANONICAL_SKILLS_PATH)
        packaged = sorted(name for name in boundary_locations() if name != canonical_name)
        self.assertTrue(packaged, "the projection declares no packaged copy to lose a clause")

        texts = {name: f"# boundary\n\n{clause.clause}\n" for name in boundary_locations()}
        self.assertEqual(missing_clause_locations(clause, texts), [])

        texts[packaged[0]] = "# boundary\n\nSomething else entirely.\n"

        self.assertEqual(
            missing_clause_locations(clause, texts),
            [f"{packaged[0]}/{clause.surface}"],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
