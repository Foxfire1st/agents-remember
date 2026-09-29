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
from typing import Any
from uuid import uuid4

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
from agents_remember.models.knowledge.read import (
    InvariantIdentitySeed,
    InvariantRevisionSeed,
)
from agents_remember.observer import (
    AmbientLifecycle,
    EventStore,
    install_ambient,
    observer_root,
    reset_ambient,
)
from agents_remember.observer.ambient import AmbientTiming
from read_scope_test_support import (
    BATCH_PATH,
    INTEGRATION_PATH,
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
    """The paired read also reads the repository's published intent, without a task.

    ICR-R19@v1: a planner that holds no leaf, no enclosure and no task must still be able to read
    what the repository already recorded about the paths it is standing in, at that dataset's exact
    identities -- and every way that read can fail has to be a *named* state rather than an empty
    page. The dataset is the read-scope fixture's real one (built through the public store
    operations, with real Git blobs), placed at the published location, and the code root is the
    fixture's own Git tree, so the anchors these cases observe are real.
    """

    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        self.fixture = build_read_scope_fixture(self._dir / "read-scope")
        # The fixture's Git tree becomes the configured repository itself, so the code root this
        # read resolves is the tree the fixture's recorded anchors were authored against.
        self.code = self._dir / "read-scope" / REPO
        shutil.move(str(self.fixture.git_root), str(self.code))
        self.memory = self._dir / "memory"
        (self.memory / "onboarding").mkdir(parents=True)
        self.config = _make_config(self._dir, self.code)
        reset_ambient()

    def tearDown(self) -> None:
        reset_ambient()

    def _published(self, memory_root: Path | None = None, payload: bytes | None = None) -> Path:
        """Place (or damage) the repository's published dataset at its one selected location."""
        root = memory_root or self.memory
        (root / "onboarding").mkdir(parents=True, exist_ok=True)
        target = root / "knowledge.sqlite"
        if payload is None:
            shutil.copyfile(self.fixture.database_path, target)
        else:
            target.write_bytes(payload)
        return target

    def _context(self, *, memory_root: Path | None = None, repo_name: str = REPO):
        root = memory_root or self.memory
        return _build_context(
            self.code,
            root / "onboarding",
            coordination_root=self.config.coordination_root,
            storage_mode="repo-sidecar",
            repo_name=repo_name,
        )

    def _read(self, files, **kwargs):
        context = self._context(**kwargs)
        payload = read_ar_files_tool(self.config, repo_id=REPO, files=files, _context=context)
        # MIK-R24 rule 9: the read of an unconverted memory tree carries no knowledge section and
        # marks its onboarding legacy-format. These cases measure the database publication route
        # itself, which a converted tree reaches through the index, so they read its block directly.
        assert payload["published_intent"]["state"] == "legacy-format", payload["published_intent"]
        payload["published_intent"] = published_intent_block(
            context, [str(entry["path"]) for entry in files]
        )
        return payload

    def _selection(self, **kwargs) -> PublishedIntentSelection:
        """The fixture's publication as a resolved selection, or a case that cannot be measured.

        Resolution answers with a selection *or* a named absence, and every case that reads by seed
        needs the first: asserting the type here keeps a fixture that stopped resolving from being
        read as a page that happened to be empty.
        """

        resolved = resolve_published_intent(self._context(**kwargs))
        assert isinstance(resolved, PublishedIntentSelection), (
            f"the fixture's publication did not resolve to a selection: {resolved}"
        )
        return resolved

    def test_the_ordinary_read_returns_the_published_intent_at_its_exact_identities(self) -> None:
        published = self._published()
        result = self._read([{"path": INTEGRATION_PATH}])

        # The source half is untouched: the intent read never replaces the paired read.
        self.assertIn("shared retry budget", result["files"][0]["source"])

        block = result["published_intent"]
        self.assertEqual(block["state"], "recorded")
        self.assertEqual(block["datasetPath"], str(published))
        self.assertEqual(block["repositoryId"], self.fixture.repository_id)
        self.assertEqual(block["snapshot"], self.fixture.knowledge_digest)
        self.assertEqual(block["sourceResolution"]["codeTreeId"], self.fixture.git_tree_id)

        entry = block["seeds"][0]
        self.assertEqual(entry["seed"], {"kind": "path", "path": INTEGRATION_PATH})
        self.assertEqual(entry["state"], "page")
        self.assertEqual(entry["snapshot"], self.fixture.knowledge_digest)
        self.assertGreater(entry["counts"]["primary_items_total"], len(entry["items"]))
        self.assertTrue(entry["hasMore"])
        self.assertTrue(entry["counts"]["primary_items_total"] > 0)
        # The token this page mints continues the scope read, not the mounted view tool: the block
        # says which operation continues it rather than leaving the caller to discover the refusal.
        self.assertEqual(entry["continuationOperation"], "read_knowledge_scope")
        subjects = {(item.get("invariant_id"), item.get("revision_id")) for item in entry["items"]}
        self.assertIn(
            (self.fixture.retry_invariant_id, self.fixture.subject_revision_id),
            subjects,
            "the invariant realized at the requested path is read at its exact retained identity",
        )
        statement = next(
            item["statement"]
            for item in entry["items"]
            if item.get("revision_id") == self.fixture.subject_revision_id
        )
        self.assertIn("share one budget", statement)

    def test_a_repository_that_publishes_nothing_reports_not_recorded(self) -> None:
        result = self._read([{"path": INTEGRATION_PATH}])

        block = result["published_intent"]
        self.assertEqual(block["state"], "not-recorded")
        self.assertEqual(block["refusalCode"], "selected_input_unavailable")
        self.assertIn(str(self.memory / "knowledge.sqlite"), block["refusalDetail"])
        self.assertEqual(block["seeds"], [])
        # The boundary example: no initialized knowledge still allows source/onboarding research.
        self.assertIn("shared retry budget", result["files"][0]["source"])

    def test_a_dataset_that_is_not_a_dataset_names_the_failed_binding(self) -> None:
        published = self._published(payload=b"this is not a database\n")

        block = self._read([{"path": INTEGRATION_PATH}])["published_intent"]
        self.assertEqual(block["state"], "unusable")
        self.assertEqual(block["refusalCode"], "snapshot_unavailable")
        self.assertIn(str(published), block["refusalDetail"])
        self.assertEqual(block["seeds"], [])

    def test_a_directory_at_the_publication_path_is_not_reported_as_nothing_recorded(self) -> None:
        """A non-file entry where the publication belongs is a named state, not an absence.

        Nothing was ever published here, so the same location can also hold a directory (or a
        dangling link) put there by something else. Reporting that as ``not-recorded`` would send the
        caller away believing the repository publishes nothing, when the truth is that the expected
        publication cannot be read from what is there.
        """

        (self.memory / "knowledge.sqlite").mkdir()

        block = self._read([{"path": INTEGRATION_PATH}])["published_intent"]
        self.assertEqual(block["state"], "unusable")
        self.assertEqual(block["refusalCode"], "selected_input_unavailable")
        self.assertIn("a directory", block["refusalDetail"])
        self.assertIn(str(self.memory / "knowledge.sqlite"), block["refusalDetail"])
        self.assertEqual(block["seeds"], [])

    def test_a_seed_that_is_not_a_typed_seed_is_refused_rather_than_raising(self) -> None:
        """The exported route answers a caller's wrong seed with a refusal, never an AttributeError.

        ``read_published_intent`` is an exported application function, so a caller can reach it with
        a value its annotation describes but the runtime never checked. That input is a mistake at
        the call site, and this route's discipline is to name it in the same vocabulary as every
        other unusable input instead of raising from inside the read.
        """

        self._published()
        selection = self._selection()
        misaddressed: Any = "src/integration.py"

        entry = read_published_intent(selection, [misaddressed])["seeds"][0]
        self.assertEqual(entry["state"], "refused")
        self.assertEqual(entry["refusalCode"], "invalid_payload")
        self.assertEqual(entry["seed"]["kind"], "unaddressable")
        self.assertEqual(entry["seed"]["python_type"], "str")
        self.assertIn("not one of the typed seeds", entry["refusalDetail"])

    def test_the_carrier_uses_the_field_spellings_the_payload_actually_returns(self) -> None:
        """The carrier names the block's own keys, not a camelCase guess.

        A carrier that spells a field the payload never carries is a reader's dead end, and no
        behavioural case can catch it: the code is right and the instruction is wrong. This case
        derives the item and count vocabulary from a real page and requires the carrier to use it --
        and requires the camelCase variants, which nothing produces, to be absent.
        """

        self._published()
        page = self._read([{"path": INTEGRATION_PATH}])["published_intent"]["seeds"][0]
        carrier = CARRIER.read_text(encoding="utf-8")

        for spelling in ("item_id", "invariant_id", "record_id", "revision_id"):
            self.assertIn(spelling, page["items"][0])
            self.assertIn(f"`{spelling}`", carrier)
        for spelling in ("primary_items_total", "primary_items_remaining"):
            self.assertIn(spelling, page["counts"])
            self.assertIn(spelling, carrier)
        self.assertIn("continuationOperation", page)
        self.assertIn("continuationOperation", carrier)
        self.assertIn("read_knowledge_scope", carrier)
        for unproduced in ("itemId", "invariantId", "recordId", "revisionId", "primaryItemsTotal"):
            self.assertNotIn(unproduced, carrier)

    def test_a_dataset_bound_to_another_repository_is_never_silently_read(self) -> None:
        published = self._published()

        block = self._read([{"path": INTEGRATION_PATH}], repo_name="another-repository")[
            "published_intent"
        ]
        self.assertEqual(block["state"], "unusable")
        self.assertIn("agents-remember", block["refusalDetail"])
        self.assertIn("another-repository", block["refusalDetail"])
        self.assertIn(str(published), block["refusalDetail"])
        # The other repository's records are not answered from, under any spelling.
        self.assertEqual(block["seeds"], [])

    def test_a_path_the_snapshot_records_nothing_about_is_a_named_absence(self) -> None:
        self._published()

        block = self._read([{"path": "src/never-recorded.py"}])["published_intent"]
        self.assertEqual(block["state"], "recorded")
        entry = block["seeds"][0]
        self.assertEqual(entry["state"], "refused")
        self.assertEqual(entry["refusalCode"], "registration_absent")
        self.assertNotIn("items", entry)

    def test_an_identity_the_snapshot_does_not_hold_is_a_named_absence(self) -> None:
        self._published()
        selection = self._selection()
        missing_revision = str(uuid4())

        block = read_published_intent(
            selection,
            [
                InvariantRevisionSeed(
                    invariant_id=self.fixture.retry_invariant_id, revision_id=missing_revision
                )
            ],
        )
        entry = block["seeds"][0]
        self.assertEqual(entry["state"], "refused")
        self.assertEqual(entry["refusalCode"], "selector_absent")
        self.assertIn(missing_revision, entry["refusalDetail"])

        held = read_published_intent(
            selection, [InvariantIdentitySeed(invariant_id=self.fixture.retry_invariant_id)]
        )["seeds"][0]
        self.assertEqual(held["state"], "page")
        retained = {item["revision_id"] for item in held["items"]}
        self.assertIn(self.fixture.base_revision_id, retained)
        self.assertIn(self.fixture.subject_revision_id, retained)
        self.assertGreater(held["counts"]["primary_items_total"], len(held["items"]))

    def test_a_path_no_recorded_anchor_could_carry_is_refused_as_a_seed(self) -> None:
        self._published()

        block = self._read([{"path": ":(exclude)src/integration.py"}])["published_intent"]
        entry = block["seeds"][0]
        self.assertEqual(entry["state"], "refused")
        self.assertEqual(entry["refusalCode"], "invalid_payload")
        self.assertIn("no seed selects it", entry["refusalDetail"])

    def test_the_resolved_source_pair_is_what_recorded_anchors_are_observed_against(self) -> None:
        self._published()

        resolved = self._read([{"path": INTEGRATION_PATH}])["published_intent"]["seeds"][0]
        plain_root = self._dir / "plain-code"
        plain_root.mkdir()
        unrequested = published_intent_block(
            _build_context(
                plain_root,
                self.memory / "onboarding",
                coordination_root=self.config.coordination_root,
                storage_mode="repo-sidecar",
            ),
            [INTEGRATION_PATH],
        )

        # The fixture records seven realizations at six locations: five resolve against its own
        # tree, one names a path the tree does not hold and one names another blob -- so a pair
        # that was really used reports exactly two unresolved anchors.
        self.assertEqual(unrequested["sourceResolution"], None)
        both = (
            resolved["counts"]["unresolved_anchor_total"],
            unrequested["seeds"][0]["counts"]["unresolved_anchor_total"],
        )
        self.assertEqual(both, (2, resolved["counts"]["realization_claims_total"]))

    def test_an_identity_seeded_page_carries_exact_retained_revisions(self) -> None:
        self._published()

        block = self._identity_seeded_page()
        entry = block["seeds"][0]
        self.assertEqual(entry["state"], "page")
        self.assertEqual(entry["seed"]["kind"], "invariant_revision")
        # The seed names one exact revision, and the page carries that identity's own retained
        # revisions at their exact ids. Every retained revision the fixture authored for it is one
        # of the three the page can name -- a sibling revision that entered through a directly
        # containing family keeps its own invariant id, so it is never re-attributed here.
        mine = {
            item["revision_id"]
            for item in entry["items"]
            if item.get("invariant_id") == self.fixture.retry_invariant_id
        }
        self.assertTrue(
            mine
            <= {
                self.fixture.base_revision_id,
                self.fixture.subject_revision_id,
                self.fixture.successor_revision_id,
            }
        )
        self.assertIn(self.fixture.subject_revision_id, mine)
        self.assertEqual(entry["snapshot"], self.fixture.knowledge_digest)
        self.assertGreater(entry["counts"]["primary_items_total"], len(entry["items"]))

    def _identity_seeded_page(self):
        """One exact revision of the fixture's retry identity, read through the same route.

        The identity seed is the second half of "bounded source/identity seeds": a caller that
        already holds a recorded identity reads it without re-discovering it from a path first.
        """

        selection = self._selection()
        return read_published_intent(
            selection,
            [
                InvariantRevisionSeed(
                    invariant_id=self.fixture.retry_invariant_id,
                    revision_id=self.fixture.subject_revision_id,
                )
            ],
        )


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
