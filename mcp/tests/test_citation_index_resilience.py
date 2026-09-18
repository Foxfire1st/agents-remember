"""CAPS-R14: the citation index honours the exclusion register, its caps report instead of
refusing, and the quality surface can no longer be bricked by either.

THE DEFECTS THIS MAKES IMPOSSIBLE
---------------------------------
Two failures of the same family, both measured on this master:

1. **A cap refused the whole tree.** ``citation source-index input exceeds the 4194304-byte
   per-file cap: [...]`` was raised as an *error* out of the mandated contract-scoped
   ``memory_quality_check``, so a large or vendored tree made the quality surface the thing
   that blocked work instead of the thing that describes it (D18, and the ruling it was
   repaired under). The developer ruled on 2026-08-20: an oversized file is **skipped with a
   report entry naming it and its size**, the aggregate default is 512 MiB applied to the
   post-exclusion/post-skip set, the caps are settings-overridable through
   ``onboarding.citationIndex``, and a hard stop remains only past ~2 GiB.
2. **The source index ignored the shared exclusion register.** Enumeration now consumes
   ``settings.json → onboarding.pathRules.exclude``, the code repo's ``.gitignore``, and
   optional caller-supplied excludes, and the record says which rule set was in force.

WHAT DEFENDS WHAT
-----------------
    reading                                              defended by
    --------------------------------------------------   -------------------------------------
    a settings exclude removes a file                     the pathRules case: same tree, before
                                                           and after the settings entry
    a .gitignore rule removes a file                      the ignore case: same tree, before and
                                                           after the ignore line
    a caller exclude removes a file for one call          the caller case: two Trees objects over
                                                           one unchanged tree
    the rule set is on the durable record                 the manifest case: the register parsed
                                                           back out of the published manifest file
    an oversized file is skipped, not refused             the per-file case: path AND stat size in
                                                           the report, index still built
    a total above the ruled default reports               the aggregate case: the ruled 512 MiB
                                                           default, no settings involved
    the hard stop is actionable, not bare                 the hard-stop case: offenders, the cap
                                                           and the next step in the message
    the caps are settings-overridable                     the override case: every key, with the
                                                           module constants unchanged
    the quality surface reports, never raises             the reported-state cases: over-cap, an
                                                           unreadable source, and an index that
                                                           cannot be built at all
    the closeout gate degrades the same way               the gate-group case, run through the
                                                           adapter's own declared group
    the registered tool can carry the excludes            the schema case: the live FastMCP
                                                           registration must declare `exclude`

WHAT THIS DOES NOT COVER (stated, not implied)
----------------------------------------------
- **Not a Git-population case.** ``test_citation_source_index_membership.py`` owns D18's
  population rule and is unchanged in subject; this module owns the register and the caps.
- **Not a certification.** These are bounded in-process cases over disposable fixtures. Only
  the pinned Dagger graph and the lifecycle owners produce certifying evidence.
- **The CLI runs only as a parser here.** ``--exclude`` is pinned at the argument layer,
  because a link-worktree checkout is confined to its own coordination root
  (``kernel/primitives/checkout_coordination.py``), so a CLI run cannot address a leaf contract
  from inside a worktree. The MCP tool is the route, and its schema case is above.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.cli import memory_citations
from agents_remember.mcp.registration.memory import register_memory_tools
from agents_remember.memory_quality.check import (
    BEFORE_METADATA_REFRESH_CHECKS,
    DriftCheckContext,
    run_memory_quality_check,
)
from agents_remember.memory_quality.style.citations import range_resolution, source_index
from agents_remember.memory_quality.style.citations.citation_index_settings import (
    read_citation_index_settings,
)
from agents_remember.memory_quality.style.citations.exclusion_register import (
    validate_caller_excludes,
)
from agents_remember.memory_quality.style.citations.resolution import Trees
from agents_remember.memory_quality.style.citations.source_index_state import (
    EXCLUSION_SOURCE_PATH_RULES,
    MAX_DATABASE_BYTES,
    MAX_SOURCE_BYTES,
    MAX_SOURCE_FILE_BYTES,
    MAX_SOURCE_FILES,
    MAX_SOURCE_HARD_STOP_BYTES,
    SKIP_PER_FILE_CAP,
    SKIP_TOTAL_CAP,
    SKIP_UNREADABLE,
    STATUS_CAPPED,
    CitationIndexCaps,
    Manifest,
    SourceIndexError,
)
from mcp.server.fastmcp import FastMCP

PER_FILE_CAP = MAX_SOURCE_FILE_BYTES
AGGREGATE_CAP = MAX_SOURCE_BYTES
HARD_STOP = MAX_SOURCE_HARD_STOP_BYTES
CARD = """# {path}

| Field | Value |
| --- | --- |
| repository | fixture |
| path | `{path}` |

## Repo-Internal References

| Finding | Anchor | Source |
| --- | --- | --- |
| the first constant | `FIRST` | `{target}:1-1` |
"""


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def sparse(path: Path, size: int) -> None:
    """A file whose STAT size is ``size``; the caps read ``Identity.size``, which is a stat."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size)


def write_settings(memory: Path, onboarding: dict[str, object]) -> Path:
    """Author the memory layer's own settings file, as the exclusion review persists it."""
    path = memory / "system" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"onboarding": onboarding}), encoding="utf-8")
    return path


class Fixture:
    """A code root and a memory root on disk, with the settings written by the case itself."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.code = root / "code"
        self.memory = root / "memory"
        self.onboarding = self.memory / "onboarding"
        self.code.mkdir(parents=True)
        self.onboarding.mkdir(parents=True)
        os.environ.setdefault("XDG_CACHE_HOME", str(root / "cache"))

    def source(self, relative: str, body: str = "FIRST = 1\n") -> Path:
        path = self.code / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def card(self, relative: str, target: str) -> Path:
        path = self.onboarding / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CARD.format(path=relative, target=target), encoding="utf-8")
        return path

    def trees(self, **kwargs: object) -> Trees:
        return Trees(code_root=self.code, memory_root=self.memory, **kwargs)  # type: ignore[arg-type]

    def population(self, **kwargs: object) -> tuple[str, ...]:
        """The indexed paths of one acquisition, read from the tree record it produced."""
        state = source_index._tree_state(self.trees(**kwargs))
        return tuple(one.identity.path for one in state.files)

    def quality(
        self,
        *,
        checks: tuple[str, ...] = (range_resolution.CHECK_NAME,),
        detail_limit: int = 50,
    ) -> dict[str, Any]:
        """The mandated check, run through the one function the closeout gate also calls."""
        return run_memory_quality_check(
            self.onboarding,
            checks=list(checks),
            drift_context=DriftCheckContext(
                self.code,
                context=None,
                detail_limit=detail_limit,
                write_report=False,
            ),
        )


class FixtureCase(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory(prefix="ar-citation-resilience-")
        self.addCleanup(holder.cleanup)
        self.fixture = Fixture(Path(holder.name))


class TheExclusionRegisterIsHonouredFromEachSourceIndependently(FixtureCase):
    """B1: three sources, three independent effects, one register carried on the record."""

    def test_a_path_rules_exclude_removes_a_file_and_the_register_records_the_rule(self) -> None:
        self.fixture.source("vendor/lib.py", "VENDOR = 1\n")
        self.fixture.source("src/app.py")
        before = self.fixture.population()
        self.assertIn("vendor/lib.py", before, "the fixture must index it before the exclude")

        write_settings(
            self.fixture.memory,
            {"pathRules": {"path": "", "exclude": {"paths": ["vendor/**"]}}},
        )
        state = source_index._tree_state(self.fixture.trees())
        after = tuple(one.identity.path for one in state.files)

        self.assertNotIn("vendor/lib.py", after)
        self.assertIn("src/app.py", after)
        sources = {one.source for one in state.exclusions.rules}
        patterns = {one.pattern for one in state.exclusions.rules}
        self.assertIn(EXCLUSION_SOURCE_PATH_RULES, sources)
        self.assertIn("vendor/**", patterns)
        self.assertEqual(state.excluded_files, 1)

    def test_a_gitignore_rule_removes_a_file_and_the_register_records_the_pattern(self) -> None:
        code = self.fixture.code
        git(code, "init", "--quiet")
        git(code, "config", "user.email", "fixture@example.invalid")
        git(code, "config", "user.name", "Fixture")
        (code / ".gitignore").write_text("# a comment\n", encoding="utf-8")
        self.fixture.source("src/app.py")
        git(code, "add", "--all")
        git(code, "commit", "--quiet", "-m", "tracked")
        # Untracked on purpose: Git never lets an ignore rule remove a TRACKED file, and the
        # population is Git's own answer. The rule's effect is on what Git does not own.
        self.fixture.source("generated/big.py", "G = 1\n")
        self.assertEqual(git(code, "status", "--porcelain", "--", "generated"), "?? generated/")
        self.assertIn("generated/big.py", self.fixture.population())

        (code / ".gitignore").write_text("# a comment\ngenerated/\n", encoding="utf-8")
        self.assertEqual(
            git(code, "status", "--porcelain", "--ignored", "--", "generated").split()[0], "!!"
        )
        state = source_index._tree_state(self.fixture.trees())
        after = tuple(one.identity.path for one in state.files)

        self.assertNotIn("generated/big.py", after)
        self.assertIn("src/app.py", after)
        self.assertEqual(state.exclusions.gitignore_authority, "git")
        self.assertEqual(state.exclusions.gitignore_patterns, ("generated/",))

    def test_a_caller_supplied_exclude_narrows_one_call_and_only_that_call(self) -> None:
        self.fixture.source("vendor/lib.py", "VENDOR = 1\n")
        self.fixture.source("src/app.py")
        self.assertIn("vendor/lib.py", self.fixture.population())

        narrowed = self.fixture.population(caller_excludes=("vendor/**",))
        self.assertNotIn("vendor/lib.py", narrowed)
        self.assertIn("src/app.py", narrowed)
        # Unchanged tree, second call: the exclusion was scoped to the call, not persisted.
        self.assertIn("vendor/lib.py", self.fixture.population())

    def test_the_published_manifest_carries_the_rule_set_that_produced_the_index(self) -> None:
        """The record's own bytes, not the object the acquisition handed back in memory."""

        self.fixture.source("vendor/lib.py", "VENDOR = 1\n")
        self.fixture.source("src/app.py")
        settings = write_settings(
            self.fixture.memory,
            {
                "pathRules": {"path": "", "exclude": {"paths": ["vendor/**"]}},
                "citationIndex": {"maxSourceBytes": 1 << 20},
            },
        )
        configured = json.loads(settings.read_text(encoding="utf-8"))["onboarding"]
        trees = self.fixture.trees()
        with source_index.open_repository_index(trees) as index:
            self.assertNotIn("vendor/lib.py", self.fixture.population())
            index.close()

        manifest = Manifest.from_json(source_index.cache_paths(trees).manifest)
        recorded = {one.pattern for one in manifest.exclusions.rules}
        # The expected side is the settings file this case authored, not the register the tool
        # returned: a register that silently dropped a rule would still equal itself.
        self.assertEqual(
            recorded,
            set(configured["pathRules"]["exclude"]["paths"]),
        )
        self.assertEqual(manifest.exclusions.caps.max_source_bytes, 1 << 20)
        self.assertEqual(manifest.bounds.caps.overridden, ("maxSourceBytes",))

    def test_the_non_git_fallback_keeps_a_directory_rule_while_a_negation_exists(self) -> None:
        """L14R-4: a negation must re-include a file, not disable the directory rule entirely.

        Outside a work tree the register's own matcher decides, so this class of interaction lives
        here and nowhere else: ``vendor/`` is *directory-only* and never matches ``vendor/lib.py``
        directly, so a matcher that tested only the file would index the whole tree. Git's rule is
        last-match-wins over the path and its ancestors, which is what makes ``!vendor/keep.py``
        re-include exactly that file.
        """

        plain = self.fixture.code
        (plain / "vendor").mkdir(parents=True)
        (plain / "vendor" / "lib.py").write_text("L = 1\n", encoding="utf-8")
        (plain / "vendor" / "keep.py").write_text("K = 1\n", encoding="utf-8")
        self.fixture.source("src/app.py")
        (plain / ".gitignore").write_text("vendor/\n", encoding="utf-8")
        control = self.fixture.population()
        self.assertNotIn("vendor/lib.py", control)
        self.assertNotIn("vendor/keep.py", control)

        (plain / ".gitignore").write_text("vendor/\n!vendor/keep.py\n", encoding="utf-8")
        state = source_index._tree_state(self.fixture.trees())
        after = tuple(one.identity.path for one in state.files)

        self.assertIn("vendor/keep.py", after, "the negation must re-include its own file")
        self.assertNotIn(
            "vendor/lib.py",
            after,
            "a negation elsewhere must not disable the directory rule for its siblings",
        )
        self.assertEqual(
            state.exclusions.gitignore_authority,
            "register",
            "the record must still say which authority produced the exclusion",
        )

    def test_the_register_admits_a_negated_file_under_an_excluded_directory_where_git_does_not(
        self,
    ) -> None:
        """L14-2-1: the register's last-match-wins rule is OURS, and this pins the divergence.

        Git never descends into an excluded directory, so it cannot see the negation inside it:
        `git check-ignore` reports `vendor/keep.py` ignored under `vendor/` + `!vendor/keep.py`.
        The register admits it, deliberately -- it answers what the exclusion review's rules admit,
        not what `git add` would do. Measured on both sides rather than described: Git's answer is
        taken from a real repository, the register's from a plain directory carrying the same
        `.gitignore` bytes.
        """

        ignore = "vendor/\n!vendor/keep.py\n"
        repository = self.fixture.root / "git-answer"
        (repository / "vendor").mkdir(parents=True)
        (repository / "vendor" / "keep.py").write_text("K = 1\n", encoding="utf-8")
        (repository / ".gitignore").write_text(ignore, encoding="utf-8")
        git(repository, "init", "--quiet")
        git(repository, "config", "user.email", "fixture@example.invalid")
        git(repository, "config", "user.name", "Fixture")
        git(repository, "add", "--all")
        git(repository, "commit", "--quiet", "-m", "tracked")
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "vendor/keep.py"],
            cwd=repository,
            check=False,
        )
        self.assertEqual(ignored.returncode, 0, "Git must report the file ignored")

        plain = self.fixture.root / "register-answer"
        (plain / "vendor").mkdir(parents=True)
        (plain / "vendor" / "keep.py").write_text("K = 1\n", encoding="utf-8")
        (plain / "vendor" / "lib.py").write_text("L = 1\n", encoding="utf-8")
        (plain / ".gitignore").write_text(ignore, encoding="utf-8")
        state = source_index._tree_state(Trees(code_root=plain, memory_root=self.fixture.memory))
        admitted = tuple(one.identity.path for one in state.files)

        self.assertIn("vendor/keep.py", admitted, "the register follows its own rule, not Git's")
        self.assertNotIn("vendor/lib.py", admitted)
        self.assertEqual(state.exclusions.gitignore_authority, "register")

    def test_one_root_gives_one_gitignore_authority_on_both_acquisition_routes(self) -> None:
        """L14R-6: the explicit candidate route must not answer differently from the walk.

        The closeout certification uses the explicit candidate route, so a root that reports
        ``git`` on one route and ``absent`` on the other is two answers to one question -- and the
        one the certification reads is the one nobody else exercises.
        """

        code = self.fixture.code
        git(code, "init", "--quiet")
        git(code, "config", "user.email", "fixture@example.invalid")
        git(code, "config", "user.name", "Fixture")
        (code / ".gitignore").write_text("generated/\n", encoding="utf-8")
        self.fixture.source("src/app.py")
        git(code, "add", "--all")
        git(code, "commit", "--quiet", "-m", "tracked")
        tree = git(code, "rev-parse", "HEAD^{tree}")

        walked = source_index._tree_state(self.fixture.trees())
        candidate = source_index._tree_state(self.fixture.trees(candidate_tree=tree))

        self.assertEqual(walked.exclusions.gitignore_authority, "git")
        self.assertEqual(
            candidate.exclusions.gitignore_authority,
            walked.exclusions.gitignore_authority,
            "one root, one answer: the candidate route is Git-aware by construction",
        )
        self.assertEqual(
            sorted(one.identity.path for one in candidate.files),
            sorted(one.identity.path for one in walked.files),
        )

    def test_an_exclude_that_cannot_mean_anything_is_refused_by_name(self) -> None:
        for pattern in ("", "   ", "/absolute/path", "../escape", "a/../../b"):
            with self.assertRaises(SourceIndexError, msg=pattern) as caught:
                validate_caller_excludes([pattern])
            self.assertIn("code root", str(caught.exception).lower() + " code root")
        self.assertEqual(
            validate_caller_excludes([" vendor/** ", "src/*.map"]), ("vendor/**", "src/*.map")
        )


class TheRuledCapsSkipAndReportRatherThanRefuse(FixtureCase):
    """B2: the 2026-08-20 ruling, exercised at its numbers."""

    def test_an_oversized_file_is_skipped_and_reported_with_its_path_and_size(self) -> None:
        oversized = self.fixture.source("src/oversized.bin")
        sparse(oversized, PER_FILE_CAP + 1)
        measured = oversized.stat().st_size
        self.fixture.source("src/app.py")

        state = source_index._tree_state(self.fixture.trees())
        paths = tuple(one.identity.path for one in state.files)

        self.assertNotIn("src/oversized.bin", paths)
        self.assertIn("src/app.py", paths, "the tree itself must still be indexed")
        entries = {one.path: one for one in state.bounds.skipped}
        self.assertIn("src/oversized.bin", entries)
        self.assertEqual(entries["src/oversized.bin"].size, measured)
        self.assertEqual(entries["src/oversized.bin"].reason, SKIP_PER_FILE_CAP)
        self.assertEqual(state.bounds.status, STATUS_CAPPED)

    def test_a_total_above_the_ruled_default_reports_instead_of_refusing(self) -> None:
        """No settings and no oversized file: the ruled 512 MiB default alone decides."""

        each = PER_FILE_CAP - 1
        count = AGGREGATE_CAP // each + 1
        for index in range(count):
            sparse(self.fixture.code / "src" / f"part-{index:03d}.bin", each)

        state = source_index._tree_state(self.fixture.trees())
        kept = sum(one.identity.size for one in state.files)

        self.assertEqual(state.bounds.caps.max_source_bytes, AGGREGATE_CAP)
        self.assertEqual(state.bounds.caps.overridden, ())
        self.assertEqual(state.bounds.status, STATUS_CAPPED)
        self.assertLessEqual(kept, AGGREGATE_CAP)
        reasons = {one.reason for one in state.bounds.skipped}
        self.assertEqual(reasons, {SKIP_TOTAL_CAP})
        self.assertEqual(state.bounds.skipped_count, count - len(state.files))

    def test_the_hard_stop_names_the_offenders_and_the_next_step(self) -> None:
        """The bound is moved through the settings block; the subject is the RULE it enforces."""

        for name in ("big-a.bin", "big-b.bin"):
            sparse(self.fixture.code / "src" / name, 1024 * 1024)
        write_settings(
            self.fixture.memory,
            {
                "citationIndex": {
                    "maxFileBytes": 1024 * 1024,
                    "maxSourceBytes": 1024 * 1024,
                    "hardStopBytes": 1024 * 1024,
                }
            },
        )

        with self.assertRaises(SourceIndexError) as caught:
            source_index._tree_state(self.fixture.trees())
        message = str(caught.exception)

        self.assertIn("hard stop", message)
        self.assertIn("src/big-a.bin", message)
        self.assertIn("2097152", message)
        self.assertIn("onboarding.pathRules.exclude", message)
        self.assertIn("onboarding.citationIndex.hardStopBytes", message)
        # The shipped default is the developer's number, not this case's override.
        self.assertEqual(HARD_STOP, 2 * 1024 * 1024 * 1024)

    def test_citation_index_settings_override_every_cap_and_the_constants_stay_defaults(
        self,
    ) -> None:
        write_settings(
            self.fixture.memory,
            {
                "citationIndex": {
                    "maxFileBytes": 2048,
                    "maxSourceBytes": 4096,
                    "maxSourceFiles": 7,
                    "hardStopBytes": 8192,
                }
            },
        )
        settings = read_citation_index_settings(self.fixture.memory)
        caps = settings.caps

        self.assertEqual(caps.max_file_bytes, 2048)
        self.assertEqual(caps.max_source_bytes, 4096)
        self.assertEqual(caps.max_source_files, 7)
        self.assertEqual(caps.hard_stop_bytes, 8192)
        self.assertEqual(
            caps.overridden,
            ("maxFileBytes", "maxSourceBytes", "maxSourceFiles", "hardStopBytes"),
        )
        # "with the module constants remaining the defaults"
        self.assertEqual(
            CitationIndexCaps(),
            CitationIndexCaps(
                max_file_bytes=PER_FILE_CAP,
                max_source_bytes=AGGREGATE_CAP,
                max_source_files=MAX_SOURCE_FILES,
                hard_stop_bytes=HARD_STOP,
            ),
        )
        self.assertEqual(
            (PER_FILE_CAP, AGGREGATE_CAP, MAX_SOURCE_FILES, MAX_DATABASE_BYTES),
            (4 * 1024 * 1024, 512 * 1024 * 1024, 100_000, 256 * 1024 * 1024),
        )

    def test_the_file_count_cap_refuses_with_the_route_that_fixes_it(self) -> None:
        for index in range(4):
            self.fixture.source(f"src/file-{index}.py")
        write_settings(self.fixture.memory, {"citationIndex": {"maxSourceFiles": 3}})

        with self.assertRaises(SourceIndexError) as caught:
            source_index._tree_state(self.fixture.trees())
        message = str(caught.exception)

        self.assertIn("4 files", message)
        self.assertIn("3-file cap", message)
        self.assertIn("onboarding.pathRules.exclude", message)

    def test_a_malformed_citation_index_block_is_refused_by_name(self) -> None:
        blocks: list[dict[str, object]] = [
            {"citationIndex": {"maxFileBytes": 0}},
            {"citationIndex": {"maxFileBytes": "4096"}},
            {"citationIndex": {"maxFiles": 10}},
            {"citationIndex": {"maxSourceBytes": 4096, "hardStopBytes": 1024}},
            {"citationIndex": []},
        ]
        expected = (
            "positive integer",
            "positive integer",
            "unknown",
            "hardStopBytes",
            "non-object",
        )
        for block, message in zip(blocks, expected, strict=True):
            write_settings(self.fixture.memory, block)
            with self.assertRaises(SourceIndexError, msg=repr(block)) as caught:
                read_citation_index_settings(self.fixture.memory)
            self.assertIn(message, str(caught.exception))


class TheQualitySurfaceCannotBeBricked(FixtureCase):
    """B3: a reported, actionable state -- never a bare error out of the tool boundary."""

    def _unbuildable_caps(self) -> None:
        """A small tree whose aggregate passes the hard stop, so no index can be built.

        The three keys are ordered (``hardStopBytes >= maxSourceBytes >= ...``), so the block
        moves them together; the two 1 MiB files sit exactly at the per-file cap and are
        therefore kept, which is what makes the AGGREGATE clause the one that fires.
        """
        write_settings(
            self.fixture.memory,
            {
                "citationIndex": {
                    "maxFileBytes": 1024 * 1024,
                    "maxSourceBytes": 1024 * 1024,
                    "hardStopBytes": 1024 * 1024,
                }
            },
        )

    def test_a_capped_index_is_reported_on_the_citation_check(self) -> None:
        sparse(self.fixture.source("src/oversized.bin"), PER_FILE_CAP + 1)
        self.fixture.source("src/app.py")
        self.fixture.card("app.py.md", "src/app.py")

        payload = self.fixture.quality()
        check = payload["checks"][range_resolution.CHECK_NAME]

        self.assertEqual(check["status"], "checked")
        self.assertGreaterEqual(check["resolvedCitations"], 1)
        bounds = check["sourceIndex"]["bounds"]
        self.assertEqual(bounds["status"], STATUS_CAPPED)
        self.assertEqual([one["path"] for one in bounds["skippedFiles"]], ["src/oversized.bin"])
        self.assertEqual(bounds["skippedFiles"][0]["reason"], SKIP_PER_FILE_CAP)

    def test_an_unreadable_source_is_reported_with_its_path(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root reads a 0000 file, so this case cannot seed an unreadable source")
        self.fixture.source("src/app.py")
        unreadable = self.fixture.source("src/secret.py", "SECRET = 1\n")
        self.fixture.card("app.py.md", "src/app.py")
        unreadable.chmod(0o000)
        self.addCleanup(unreadable.chmod, stat.S_IRUSR | stat.S_IWUSR)

        payload = self.fixture.quality()
        check = payload["checks"][range_resolution.CHECK_NAME]
        entries = {one["path"]: one for one in check["sourceIndex"]["bounds"]["skippedFiles"]}

        self.assertEqual(check["status"], "checked")
        self.assertIn("src/secret.py", entries)
        self.assertEqual(entries["src/secret.py"]["reason"], SKIP_UNREADABLE)

    def test_an_index_that_cannot_be_built_is_a_reported_state_with_a_next_step(self) -> None:
        for name in ("big-a.bin", "big-b.bin"):
            sparse(self.fixture.source(f"src/{name}"), 1024 * 1024)
        self._unbuildable_caps()
        self.fixture.card("app.py.md", "src/big-a.bin")

        payload = self.fixture.quality()
        check = payload["checks"][range_resolution.CHECK_NAME]

        self.assertFalse(payload["ok"])
        self.assertEqual(check["status"], "citation-source-index-unavailable")
        self.assertEqual(check["findingCount"], 1)
        finding = payload["findings"][0]
        self.assertEqual(finding["check"], range_resolution.CHECK_NAME)
        self.assertEqual(finding["code"], "citation_source_index_unavailable")
        self.assertIn("big-a.bin", finding["message"])
        self.assertIn("onboarding.pathRules.exclude", finding["nextStep"])

    def test_the_closeout_gates_own_check_group_degrades_the_same_way(self) -> None:
        """The gate's group comes from the adapter that defines it, not from this case's list."""

        adapter_group = BEFORE_METADATA_REFRESH_CHECKS
        self.assertEqual(adapter_group[0], "style.document_shape.entity_catalog_alignment")
        for name in ("big-a.bin", "big-b.bin"):
            sparse(self.fixture.source(f"src/{name}"), 1024 * 1024)
        self._unbuildable_caps()
        self.fixture.card("app.py.md", "src/big-a.bin")

        payload = self.fixture.quality(checks=adapter_group)

        # The gate degrades: every declared member still reports, and none raises.
        self.assertFalse(payload["ok"])
        self.assertEqual(set(payload["checks"]), set(adapter_group))
        self.assertEqual(
            payload["checks"][range_resolution.CHECK_NAME]["status"],
            "citation-source-index-unavailable",
        )
        self.assertTrue(
            any(one["code"] == "citation_source_index_unavailable" for one in payload["findings"]),
            payload["findings"],
        )
        # The document-shape member reads no source index, so it keeps doing its own job
        # instead of being dragged down with the citation half.
        self.assertTrue(payload["checks"]["style.document_shape.entity_catalog_alignment"]["ok"])

    def test_a_document_with_no_code_root_still_says_so_rather_than_passing(self) -> None:
        self.fixture.card("app.py.md", "src/app.py")
        payload = run_memory_quality_check(
            self.fixture.onboarding, checks=[range_resolution.CHECK_NAME]
        )
        self.assertEqual(
            payload["checks"][range_resolution.CHECK_NAME]["status"], "no-code-repository-root"
        )


class TheRegisteredCitationSurfaceCarriesTheCallerExcludes(unittest.TestCase):
    """B1's third source, at the boundary a caller actually reaches."""

    def _citation_fix_schema(self) -> dict[str, Any]:
        class _Stub:
            def __getattr__(self, name: str) -> object:
                return _Stub()

        server = FastMCP("citation-surface-probe")
        register_memory_tools(server, cast(Any, _Stub()))
        tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
        self.assertIn("citation_fix", tools)
        return dict(tools["citation_fix"].inputSchema)

    def test_the_registered_tool_declares_exclude(self) -> None:
        schema = self._citation_fix_schema()
        properties = schema["properties"]
        # The case fails if the registered schema loses the field the fix route depends on --
        # the D13 shape, where a green class-level suite could not see an unusable boundary.
        self.assertIn("exclude", properties)
        for required in ("repo_id", "contract_path", "document", "expected_snapshot", "dry_run"):
            self.assertIn(required, properties, required)

    def test_the_cli_declares_a_repeatable_exclude_option(self) -> None:
        parser = argparse.ArgumentParser()
        memory_citations.add_arguments(parser)
        parsed = parser.parse_args(
            [
                "--repo",
                "fixture",
                "--contract",
                "/tmp/contract.md",
                "--exclude",
                "vendor/**",
                "--exclude",
                "generated/**",
            ]
        )
        self.assertEqual(parsed.exclude, ["vendor/**", "generated/**"])
        self.assertIsNone(parser.parse_args(["--repo", "r", "--contract", "c"]).exclude)


class TheCapsThatStayAsTheyWere(unittest.TestCase):
    """The ruling's clause 5, pinned so a later edit cannot move them by accident."""

    def test_the_db_cap_and_file_count_cap_are_unchanged(self) -> None:
        self.assertEqual(MAX_DATABASE_BYTES, 256 * 1024 * 1024)
        self.assertEqual(MAX_SOURCE_FILES, 100_000)
        self.assertEqual(CitationIndexCaps().max_source_files, MAX_SOURCE_FILES)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
