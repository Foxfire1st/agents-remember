"""Paired source reads preserve requested bytes, confine paths and deduplicate unchanged onboarding.

The second half of the module is the published-intent half of the same route (ICR-R19@v1): the
ordinary paired read also resolves the repository's published knowledge dataset from its
coordination context and reads the recorded intent about each requested path at that dataset's own
snapshot, or names -- by its exact binding -- why it could not.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
# The canonical retrieval carrier that documents this route: the one file a reader follows to
# discover it, and therefore the file a carrier-parity case has to measure.
CARRIER = Path(__file__).resolve().parents[2] / "skills/c-04-retrieval-strategy-router/SKILL.md"
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.published_intent import (
    PublishedIntentSelection,
    published_intent_block,
    read_published_intent,
    resolve_published_intent,
)
from agents_remember.application.read_files import read_ar_files_tool
from agents_remember.errors import AuthorityError
from agents_remember.kernel.coordination_context.models import (
    CoordinationContext,
    CrossRepoSettings,
    StorageSettings,
)
from agents_remember.kernel.primitives.runtime_config import (
    load_config,
)
from agents_remember.mcp.tools import read_ar_files_payload
from agents_remember.observer import (
    AmbientLifecycle,
    EventStore,
    install_ambient,
    observer_root,
    reset_ambient,
)
from agents_remember.observer.ambient import AmbientTiming
from knowledge_writer_test_support import CODE_FILE, build_world
from read_scope_test_support import (
    BATCH_PATH,
    build_read_scope_fixture,
)
from test_config import settings_payload

REPO = "agents-remember"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _make_config(root: Path, code_root: Path):
    """A real ``McpRuntimeConfig`` whose coordination root is ``root`` and whose
    one repo points at ``code_root`` -- so ``observer_root`` and the reset marker
    resolve under ``root/logs/observer`` and the repo path confines correctly."""
    settings = settings_payload(root)
    settings["workspaceRoot"] = str(code_root.parent)
    settings["repositories"] = {REPO: {}}
    path = root / ".codex" / "mcp" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings), encoding="utf-8")
    return load_config(path)


def _build_context(
    code_root: Path,
    onboarding_root: Path,
    *,
    coordination_root: Path,
    storage_mode: str,
    repo_name: str = REPO,
) -> CoordinationContext:
    """A minimal context with the fields the application layer reads, isolating storage."""
    storage = StorageSettings(mode=storage_mode, default=storage_mode)
    return CoordinationContext(
        topology="external",
        code_repository_name=repo_name,
        code_repository_root=code_root,
        coordination_root=coordination_root,
        memory_root=onboarding_root.parent,
        onboarding_root=onboarding_root,
        settings_path=onboarding_root / "settings.md",
        path_settings_path=None,
        task_root=coordination_root,
        temp_root=coordination_root / "temp",
        docs_root=coordination_root / "docs",
        system_root=onboarding_root.parent / "system",
        sources_path=onboarding_root.parent / "system" / "sources.md",
        tools_path=onboarding_root.parent / "system" / "tools.md",
        storage=storage,
        path_rules=storage.path_rules,
        cross_repo=CrossRepoSettings(),
        memory_mode="external",
    )


def _write_sidecar(onboarding_root: Path, source_rel: str, body: str) -> None:
    path = onboarding_root / f"{source_rel}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# {source_rel}",
                "",
                "| Field | Value |",
                "| --- | --- |",
                f"| path | `{source_rel}` |",
                "| doc_type | `file-level-onboarding` |",
                "| lastUpdated | 2026-06-01T00:00 |",
                "",
                body,
                "",
                "## Update History",
                "- 2026-06-01 seeded",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_overview(onboarding_root: Path, route: str, text: str) -> None:
    rel = "overview.md" if route == "" else f"{route}/overview.md"
    path = onboarding_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_route_index(onboarding_root: Path, route: str, *, covered: list[str]) -> None:
    rel = "overview.index.json" if route == "" else f"{route}/overview.index.json"
    scope = "**" if route == "" else f"{route}/**"
    path = onboarding_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "agents-remember-route-index",
                "route": route,
                "sourceScope": [scope],
                "coveredFiles": covered,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# ranged-read helper
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# application layer: status semantics, path confinement, source independence
# --------------------------------------------------------------------------


class ApplicationStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        self.code = self._dir / "workspace" / REPO
        (self.code / "pkg").mkdir(parents=True)
        (self.code / "pkg" / "mod.py").write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
        (self.code / "pkg" / "other.py").write_text("# other\n", encoding="utf-8")
        (self.code / "bin.dat").write_bytes(b"\xff\xfe\x00\x01binary")
        self.onb = self._dir / "memory" / "onboarding"
        self.onb.mkdir(parents=True)
        self.config = _make_config(self._dir, self.code)
        reset_ambient()

    def tearDown(self) -> None:
        reset_ambient()

    def _read(self, files, *, storage_mode: str):
        ctx = _build_context(
            self.code,
            self.onb,
            coordination_root=self.config.coordination_root,
            storage_mode=storage_mode,
        )
        return read_ar_files_tool(self.config, repo_id=REPO, files=files, _context=ctx)

    def test_range_request_returns_exact_slice(self) -> None:
        result = self._read(
            [{"path": "pkg/mod.py", "source": {"startLine": 2, "endLine": 3}}],
            storage_mode="disabled",
        )
        self.assertEqual(result["files"][0]["source"], "y = 2\nz = 3\n")

    def test_full_read_is_not_truncated(self) -> None:
        # A file larger than a few KB read as "full" returns its content
        # byte-for-byte -- the full path must never silently truncate.
        big = "\n".join(f"line {i:05d} " + "x" * 80 for i in range(400)) + "\n"
        self.assertGreater(len(big.encode("utf-8")), 4096)
        (self.code / "pkg" / "big.py").write_text(big, encoding="utf-8")
        result = self._read([{"path": "pkg/big.py"}], storage_mode="disabled")
        self.assertEqual(result["files"][0]["source"], big)

    def test_binary_source_is_omitted(self) -> None:
        result = self._read([{"path": "bin.dat"}], storage_mode="disabled")
        self.assertNotIn("source", result["files"][0])

    def test_path_confinement_rejects_escape(self) -> None:
        with self.assertRaises(AuthorityError):
            self._read([{"path": "../secret.txt"}], storage_mode="disabled")

    def test_symlink_file_escape_rejected(self) -> None:
        # A symlink inside the repo pointing at a file OUTSIDE the repo resolves
        # out of root, so confinement rejects it (not a literal ".." token).
        outside = self._dir / "outside_secret.txt"
        outside.write_text("secret\n", encoding="utf-8")
        link = self.code / "leak.py"
        link.symlink_to(outside)
        with self.assertRaises(AuthorityError):
            self._read([{"path": "leak.py"}], storage_mode="disabled")

    def test_symlink_dir_escape_rejected(self) -> None:
        # A symlinked directory inside the repo pointing OUTSIDE, addressed via a
        # child path, still resolves out of root and is rejected.
        outside_dir = self._dir / "outside_dir"
        outside_dir.mkdir()
        (outside_dir / "secret.py").write_text("secret = 1\n", encoding="utf-8")
        link = self.code / "linkdir"
        link.symlink_to(outside_dir, target_is_directory=True)
        with self.assertRaises(AuthorityError):
            self._read([{"path": "linkdir/secret.py"}], storage_mode="disabled")


class FrontDoorDedupTests(unittest.TestCase):
    """Auto-attach + per-lifecycle dedup of repo overview + route chain."""

    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        self.code = self._dir / "workspace" / REPO
        (self.code / "pkg" / "sub").mkdir(parents=True)
        (self.code / "pkg" / "sub" / "mod.py").write_text("a = 1\n", encoding="utf-8")
        self.onb = self._dir / "memory" / "onboarding"
        self.onb.mkdir(parents=True)
        _write_overview(self.onb, "", "# Repo overview\nroot text\n")
        _write_overview(self.onb, "pkg", "# pkg overview\npkg text\n")
        _write_overview(self.onb, "pkg/sub", "# sub overview\nsub text\n")
        _write_sidecar(self.onb, "pkg/sub/mod.py", "Mod body.")
        _write_route_index(self.onb, "pkg/sub", covered=["pkg/sub/mod.py"])
        self.config = _make_config(self._dir, self.code)
        self.ctx = _build_context(
            self.code,
            self.onb,
            coordination_root=self.config.coordination_root,
            storage_mode="repo-sidecar",
        )
        reset_ambient()
        amb = AmbientLifecycle(
            EventStore(observer_root(self.config)), timing=AmbientTiming(heartbeat_seconds=3600)
        )
        amb.start(fleeting=True)
        install_ambient(amb)
        self.amb = amb

    def tearDown(self) -> None:
        reset_ambient()

    def _read(self, files, *, refresh: bool = False):
        return read_ar_files_tool(
            self.config, repo_id=REPO, files=files, refresh=refresh, _context=self.ctx
        )

    def test_first_read_attaches_overview_and_route_chain(self) -> None:
        result = self._read([{"path": "pkg/sub/mod.py"}])
        self.assertEqual(result["repository_overview"]["path"], "overview.md")
        self.assertIn("root text", result["repository_overview"]["overview"])
        routes = result["route_overviews"]
        self.assertIn("pkg/sub", routes)
        self.assertIn("pkg", routes)

    def test_second_read_dedups_unchanged_pieces(self) -> None:
        self._read([{"path": "pkg/sub/mod.py"}])
        again = self._read([{"path": "pkg/sub/mod.py"}])
        self.assertNotIn("repository_overview", again)
        self.assertNotIn("route_overviews", again)

    def test_equal_overviews_dedup_independently_across_roots(self) -> None:
        self._read([{"path": "pkg/sub/mod.py"}])

        other_root = self._dir / "other"
        other_code = other_root / "workspace" / REPO
        (other_code / "pkg" / "sub").mkdir(parents=True)
        (other_code / "pkg" / "sub" / "mod.py").write_text("a = 1\n", encoding="utf-8")
        other_onboarding = other_root / "memory" / "onboarding"
        other_onboarding.mkdir(parents=True)
        _write_overview(other_onboarding, "", "# Repo overview\nroot text\n")
        _write_overview(other_onboarding, "pkg", "# pkg overview\npkg text\n")
        _write_overview(other_onboarding, "pkg/sub", "# sub overview\nsub text\n")
        _write_sidecar(other_onboarding, "pkg/sub/mod.py", "Mod body.")
        _write_route_index(other_onboarding, "pkg/sub", covered=["pkg/sub/mod.py"])
        other_config = _make_config(other_root, other_code)
        other_context = _build_context(
            other_code,
            other_onboarding,
            coordination_root=other_config.coordination_root,
            storage_mode="repo-sidecar",
        )

        other_first = read_ar_files_tool(
            other_config,
            repo_id=REPO,
            files=[{"path": "pkg/sub/mod.py"}],
            _context=other_context,
        )
        self.assertIn("repository_overview", other_first)
        self.assertIn("route_overviews", other_first)
        other_second = read_ar_files_tool(
            other_config,
            repo_id=REPO,
            files=[{"path": "pkg/sub/mod.py"}],
            _context=other_context,
        )
        self.assertNotIn("repository_overview", other_second)
        self.assertNotIn("route_overviews", other_second)

    def test_changed_overview_is_reserved(self) -> None:
        self._read([{"path": "pkg/sub/mod.py"}])
        _write_overview(self.onb, "", "# Repo overview\nCHANGED root text\n")
        again = self._read([{"path": "pkg/sub/mod.py"}])
        self.assertIn("repository_overview", again)
        self.assertIn("CHANGED", again["repository_overview"]["overview"])
        # The unchanged route overviews stay deduped.
        self.assertNotIn("route_overviews", again)

    def test_refresh_forces_reserve(self) -> None:
        self._read([{"path": "pkg/sub/mod.py"}])
        again = self._read([{"path": "pkg/sub/mod.py"}], refresh=True)
        self.assertIn("repository_overview", again)
        self.assertIn("route_overviews", again)

    def test_compact_marker_resets_served(self) -> None:
        self._read([{"path": "pkg/sub/mod.py"}])
        marker = observer_root(self.config) / "workspace" / "compact-reset.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}", encoding="utf-8")
        again = self._read([{"path": "pkg/sub/mod.py"}])
        self.assertIn("repository_overview", again)
        # Marker consumed exactly once.
        self.assertFalse(marker.exists())


# --------------------------------------------------------------------------
# integration: five-file cap + payload builder through the real fixture
# --------------------------------------------------------------------------


class FiveFileCapAndPayloadTests(unittest.TestCase):
    """End-to-end through the real resolver + payload builder + token choke point."""

    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        self.code = self._dir / "workspace" / REPO
        memory = self._dir / "ar-coordination" / "memory-repos" / f"ar-{REPO}"
        (memory / "system").mkdir(parents=True, exist_ok=True)
        (memory / "onboarding").mkdir(parents=True, exist_ok=True)
        (memory / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")
        self.code.mkdir(parents=True, exist_ok=True)
        (self.code / "README.md").write_text("# Fixture\nline2\n", encoding="utf-8")
        self.config = _make_config(self._dir, self.code)
        reset_ambient()

    def tearDown(self) -> None:
        reset_ambient()

    def test_payload_reads_committed_source(self) -> None:
        payload = read_ar_files_payload(self.config, REPO, [{"path": "README.md"}])
        self.assertEqual(payload["files"][0]["source"], "# Fixture\nline2\n")


# --------------------------------------------------------------------------
# published intent: the ordinary route reads the repository's publication
# --------------------------------------------------------------------------


class PublishedIntentRouteTests(unittest.TestCase):
    """Published intent is the selected text tree's derived index, with bounded pages and honest refusals."""

    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        world = build_world(self._dir / "converted")
        self.code = self._dir / "workspace" / REPO
        self.code.parent.mkdir()
        shutil.move(str(world.code), str(self.code))
        self.memory = world.memory
        self.config = _make_config(self._dir, self.code)
        reset_ambient()

    def tearDown(self) -> None:
        reset_ambient()
        shutil.rmtree(self._dir)

    def _context(self):
        return _build_context(
            self.code,
            self.memory / "onboarding",
            coordination_root=self.config.coordination_root,
            storage_mode="repo-sidecar",
        )

    def _selection(self) -> PublishedIntentSelection:
        selected = resolve_published_intent(self._context())
        assert isinstance(selected, PublishedIntentSelection), selected
        return selected

    def test_the_ordinary_read_returns_source_and_the_selected_tree_index(self) -> None:
        context = self._context()
        result = read_ar_files_tool(
            self.config, repo_id=REPO, files=[{"path": CODE_FILE}], _context=context
        )
        self.assertIn("source", result["files"][0])
        block = result["published_intent"]
        self.assertEqual(block["state"], "recorded", block)
        self.assertEqual(block["memoryTree"]["memoryRoot"], str(self.memory))
        self.assertEqual(block["memoryTree"]["indexState"], "complete")
        self.assertEqual(
            Path(block["datasetPath"]).parent,
            self.config.coordination_root / "runtime" / "knowledge-index",
        )
        self.assertTrue(block["seeds"][0]["rows"])

    def test_a_seed_that_is_not_typed_is_refused_rather_than_raising(self) -> None:
        entry = read_published_intent(self._selection(), [cast(Any, object())])["seeds"][0]
        self.assertEqual(entry["state"], "refused")
        self.assertEqual(entry["refusalCode"], "invalid_payload")

    def test_a_path_with_no_recorded_anchor_is_a_named_absence(self) -> None:
        entry = published_intent_block(self._context(), ["unassigned/never-recorded.py"])["seeds"][
            0
        ]
        self.assertEqual(entry["refusalCode"], "registration_absent")
        self.assertEqual(entry["state"], "refused")
        self.assertNotIn("rows", entry)

    def test_a_path_that_cannot_be_a_seed_is_refused(self) -> None:
        entry = published_intent_block(self._context(), [":(exclude)pkg/a.py"])["seeds"][0]
        self.assertEqual(entry["state"], "refused")
        self.assertEqual(entry["refusalCode"], "invalid_payload")

    def test_an_unconverted_root_cannot_open_a_legacy_database(self) -> None:
        marker = self.memory / "knowledge/layout.json"
        marker.unlink()
        frozen = self.memory / "knowledge.sqlite"
        frozen.write_bytes(b"SQLite format 3\x00 frozen legacy bytes")
        block = published_intent_block(self._context(), [CODE_FILE])
        self.assertEqual(block["state"], "unusable")
        self.assertEqual(block["refusalCode"], "legacy-format")
        self.assertEqual(block["seeds"], [])

    def test_the_carrier_uses_the_field_spellings_the_payload_actually_returns(self) -> None:
        """The carrier names the block's own keys, not a guess.

        A carrier that spells a field the payload never carries is a reader's dead end, and no
        behavioural case can catch it: the code is right and the instruction is wrong. This case
        derives the row and count vocabulary from a real page of a converted memory tree -- the
        only memory a planner reads knowledge from (MIK-R26) -- and requires the carrier to use
        it. The spellings of the retired database page and the retired tool arguments are absent.
        """

        context = self._context()
        block = published_intent_block(cast(Any, context), [CODE_FILE])
        self.assertEqual(block["state"], "recorded", block)
        page = block["seeds"][0]
        carrier = CARRIER.read_text(encoding="utf-8")

        for spelling in ("rows", "counts", "hasMore", "continuation"):
            self.assertIn(spelling, page)
            self.assertIn(f"`{spelling}`", carrier)
        for spelling in ("continuationOperation", "continuationView"):
            self.assertIn(spelling, page)
            self.assertIn(spelling, carrier)
        for spelling in ("rowsTotal", "rowsReturned", "rowsRemaining"):
            self.assertIn(spelling, page["counts"])
            self.assertIn(f"`counts.{spelling}`", carrier)
        for row in page["rows"]:
            self.assertIn("kind", row)
            self.assertIn("id", row)
        self.assertIn("memoryRoot", block["memoryTree"])
        self.assertIn("`memoryTree.memoryRoot`", carrier)
        for retired in (
            "primary_items_total",
            "read_knowledge_scope",
            "databasePath",
            "item_id",
            "itemId",
            "primaryItemsTotal",
        ):
            self.assertNotIn(retired, carrier)


class PublishedIntentMountedRouteTests(unittest.TestCase):
    """The mounted ``read_ar_files`` route resolves the publication from the real coordination tree.

    The application cases above inject a context; these two drive ``read_ar_files_payload``, so the
    memory root is the one the repository's own coordination declaration resolves and the response
    is the one the registered model validates and the token choke point stamps.
    """

    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        self.fixture = build_read_scope_fixture(self._dir / "read-scope")
        self.code = self._dir / "read-scope" / REPO
        shutil.move(str(self.fixture.git_root), str(self.code))
        self.memory = self._dir / "ar-coordination" / "memory-repos" / f"ar-{REPO}"
        (self.memory / "system").mkdir(parents=True)
        (self.memory / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")
        (self.memory / "onboarding").mkdir(parents=True)
        self.config = _make_config(self._dir, self.code)
        reset_ambient()

    def tearDown(self) -> None:
        reset_ambient()

    def test_the_mounted_route_returns_no_knowledge_section_for_unconverted_memory(self) -> None:
        """MIK-R24 rule 9: an unconverted memory tree is read as legacy-format, never misread."""

        shutil.copyfile(self.fixture.database_path, self.memory / "knowledge.sqlite")
        card = self.memory / "onboarding" / f"{BATCH_PATH}.md"
        card.parent.mkdir(parents=True, exist_ok=True)
        card.write_text("# batch\n\nLegacy card.\n", encoding="utf-8")

        payload = read_ar_files_payload(self.config, REPO, [{"path": BATCH_PATH}])

        block = payload["published_intent"]
        self.assertEqual(block["state"], "legacy-format")
        self.assertEqual(block["memoryRoot"], str(self.memory))
        self.assertIn("crossing sync", block["detail"])
        self.assertIn("# batch", payload["files"][0]["source"])

    def test_the_mounted_route_names_the_legacy_format_before_anything_is_published(self) -> None:
        payload = read_ar_files_payload(self.config, REPO, [{"path": BATCH_PATH}])

        self.assertEqual(payload["published_intent"]["state"], "legacy-format")
        self.assertIn("# batch", payload["files"][0]["source"])


# --------------------------------------------------------------------------
# test plumbing
# --------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main()
